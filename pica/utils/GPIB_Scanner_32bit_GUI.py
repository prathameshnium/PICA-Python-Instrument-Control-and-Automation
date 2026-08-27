"""
Module: GPIB_Scanner_32bit_GUI.py
Purpose: GUI module for the 32-bit raw NI-488.2 GPIB Scanner.
"""

# -------------------------------------------------------------------------------
# Name:         GPIB Scanner (32-bit, raw NI-488.2)
# Purpose:      Find which GPIB addresses are occupied on a PC whose only
#               working GPIB stack is a 32-bit gpib-32.dll with no usable VISA
#               layer (the Novocontrol BDS PC). There, pyvisa's
#               list_resources() returns nothing however healthy the bus is,
#               so the GPIB/VISA Instrument Scanner shows an empty list. This
#               tool never touches pyvisa or VISA: it is stdlib ctypes
#               straight onto the driver DLL.
#
#               BITNESS: a 32-bit DLL can only be driven from 32-bit Python.
#               This window says which Python it is running in and offers to
#               relaunch itself under a 32-bit interpreter when it is not.
#
#               GENTLE BY DESIGN: the scan uses ibln only, which asserts a
#               listen address and watches the NDAC handshake line. It sends
#               no data, no *IDN?, no device clear and no interface clear, so
#               an instrument that speaks its own command set (the Novocontrol
#               Alpha) is found without being spoken to. The only thing this
#               tool ever writes to the bus is a read-only *IDN? that you ask
#               for, one address at a time, with the Identify button.
#
# Author:       Prathamesh Deshmukh
# Created:      28/08/2026
# Version:      V: 1.0
# -------------------------------------------------------------------------------

import ctypes
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, scrolledtext, filedialog, messagebox

# -------------------------------------------------------------------------------
# --- BACK END (raw NI-488.2 through ctypes; no pyvisa, no VISA) ---
# -------------------------------------------------------------------------------

# --- NI-488.2 status bits (ibsta) ---
ERR = 1 << 15    # error: consult iberr -- and ONLY then, it is stale otherwise
TIMO = 1 << 14   # timeout
END = 1 << 13    # EOI (or EOS) seen: full response received
CMPL = 1 << 8    # I/O complete

# --- NI-488.2 timeout codes for ibdev ---
TIMEOUT_CODES = {0.3: 10, 1: 11, 3: 12, 10: 13, 30: 14}

IBERR_MEANINGS = {
    0: "EDVR: system error (board index not present / driver not bound)",
    1: "ECIC: board is not Controller-In-Charge",
    2: "ENOL: no listener at that address (device off / wrong address?)",
    3: "EADR: board not addressed correctly",
    4: "EARG: invalid argument",
    5: "ESAC: board is not the system controller",
    6: "EABO: I/O aborted (timeout -- device present but silent?)",
    7: "ENEB: no such GPIB board index (driver/board configuration)",
    8: "EDMA: DMA error",
    10: "EOIP: asynchronous I/O in progress",
    11: "ECAP: capability not available",
    12: "EFSO: file system error",
    14: "EBUS: command byte transfer error",
    15: "ESTB: serial poll status byte lost",
    16: "ESRQ: SRQ line stuck on",
    20: "ETAB: table overflow",
    28: "EPWR: interface has lost power",
}

# Response padding the Novocontrol Alpha adds around EOI-terminated replies.
RESPONSE_STRIP = b" \t\r\n\x00\x10"

# GPIB primary addresses that may hold an instrument. 0 is the controller
# board itself and 31 is UNL/UNT, so neither is probed.
SCAN_ADDRESSES = range(1, 31)

# 32-bit interpreters to offer, in order, through the Windows py launcher.
PY32_LAUNCHER_ARGS = (["py", "-3.10-32"], ["py", "-3-32"])


def iberr_text(code):
    if code is None:
        return "iberr unavailable"
    return IBERR_MEANINGS.get(code, f"iberr={code} (unknown)")


def sta_text(sta):
    bits = []
    for mask, name in ((ERR, "ERR"), (TIMO, "TIMO"), (END, "END"),
                       (CMPL, "CMPL")):
        if sta & mask:
            bits.append(name)
    return f"0x{sta & 0xFFFF:04X} [{' '.join(bits) or 'none'}]"


def decode(data):
    return data.strip(RESPONSE_STRIP).decode("ascii", "replace")


def find_dll_candidates():
    """The standard GPIB DLL locations, tried once each -- no disk hunting.

    On 64-bit Windows the loader redirects a 32-bit process's System32 path to
    SysWOW64, so the System32 entry is what actually delivers the 32-bit
    gpib-32.dll to a 32-bit interpreter. SysWOW64 is listed explicitly as well
    so that a 64-bit run can at least report the 32-bit library is present; it
    will refuse to load it with WinError 193, which is itself the diagnosis.
    """
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    paths = (
        os.path.join(windir, "System32", "gpib-32.dll"),
        os.path.join(windir, "System32", "ni4882.dll"),
        os.path.join(windir, "SysWOW64", "gpib-32.dll"),
    )
    seen, ordered = set(), []
    for path in paths:
        key = os.path.normcase(path)
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            ordered.append(path)
    return ordered


def _bind(lib, name, argtypes, restype=ctypes.c_int):
    try:
        function = getattr(lib, name)
    except AttributeError:
        return None
    function.argtypes = argtypes
    function.restype = restype
    return function


class RawGpib:
    """Minimal, defensive ctypes binding of the traditional NI-488.2 API.

    Deliberately does not touch ibfind: the 32-bit library on the Novocontrol
    PC exports ibfindA/ibfindW only, so board-level work here goes through the
    board index directly (0-3 are valid board descriptors).
    """

    def __init__(self, dll_path):
        self.path = dll_path
        lib = self.lib = ctypes.WinDLL(dll_path)
        c_int, c_size_t = ctypes.c_int, ctypes.c_size_t

        self.ibdev = _bind(lib, "ibdev", [c_int] * 6)
        self.ibonl = _bind(lib, "ibonl", [c_int, c_int])
        self.ibwrt = _bind(lib, "ibwrt", [c_int, ctypes.c_char_p, c_size_t])
        self.ibrd = _bind(lib, "ibrd", [c_int, ctypes.c_char_p, c_size_t])
        self.ibln = _bind(
            lib, "ibln",
            [c_int, c_int, c_int, ctypes.POINTER(ctypes.c_short)])

        missing = [name for name in ("ibdev", "ibonl", "ibwrt", "ibrd", "ibln")
                   if getattr(self, name) is None]
        if missing:
            raise OSError(
                f"{os.path.basename(dll_path)} lacks required exports: "
                f"{', '.join(missing)}")

        # Error / byte-count readback, most portable variant first.
        self._thread_iberr = _bind(lib, "ThreadIberr", [])
        self._thread_ibcnt = (_bind(lib, "ThreadIbcntl", [], ctypes.c_long)
                              or _bind(lib, "ThreadIbcnt", [], ctypes.c_long))
        self._ibcnt_var = None
        if self._thread_ibcnt is None:
            for name in ("ibcntl", "ibcnt"):
                try:
                    self._ibcnt_var = ctypes.c_long.in_dll(lib, name)
                    break
                except Exception:
                    continue

    def error_code(self):
        if self._thread_iberr is not None:
            try:
                return self._thread_iberr()
            except Exception:
                return None
        try:
            return ctypes.c_int.in_dll(self.lib, "iberr").value
        except Exception:
            return None

    def count(self):
        if self._thread_ibcnt is not None:
            try:
                return self._thread_ibcnt()
            except Exception:
                return None
        if self._ibcnt_var is not None:
            return self._ibcnt_var.value
        return None

    # --------------------------------------------------------- the census
    def listener_at(self, board, pad):
        """ibln: assert a listen address and watch NDAC. Sends no data.

        Returns (present, ibsta). 'present' is None when the call itself
        errored, which means the board is unusable -- not that the address is
        empty.
        """
        flag = ctypes.c_short(0)
        sta = self.ibln(board, pad, 0, ctypes.byref(flag))
        if sta & ERR:
            return None, sta
        return bool(flag.value), sta

    # --------------------------------------------------------- transfers
    def open_device(self, board, pad, timeout_code):
        """ibdev with EOI on writes (eot=1) and no EOS byte -- the framing
        the Novocontrol Alpha requires."""
        return self.ibdev(board, pad, 0, timeout_code, 1, 0)

    def close_device(self, handle):
        try:
            self.ibonl(handle, 0)
        except Exception:
            pass

    def write(self, handle, text):
        payload = text.encode("ascii")
        return self.ibwrt(handle, payload, len(payload))

    def read(self, handle, max_len=65536):
        buffer = ctypes.create_string_buffer(max_len)
        sta = self.ibrd(handle, buffer, max_len)
        if sta & ERR:
            return None, sta
        n = self.count()
        if n is not None and 0 <= n <= max_len:
            data = buffer.raw[:n]
        else:
            data = buffer.raw.rstrip(b"\x00")
        return data, sta


def open_driver(explicit_path=None):
    """Returns (RawGpib or None, list of report lines) -- never raises."""
    bits = struct.calcsize("P") * 8
    lines = []
    candidates = [explicit_path] if explicit_path else find_dll_candidates()
    if not candidates:
        lines.append("No GPIB driver DLL found in System32 or SysWOW64.")
        return None, lines

    driver = None
    for path in candidates:
        try:
            ctypes.WinDLL(path)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 193:
                lines.append(f"{path}: WRONG BITNESS for this {bits}-bit "
                             "Python.")
            else:
                lines.append(f"{path}: failed to load ({exc}).")
            continue
        try:
            driver = RawGpib(path)
        except OSError as exc:
            lines.append(f"{path}: {exc}")
            continue
        lines.append(f"Driver in use: {path}")
        break
    return driver, lines


def find_python32():
    """Returns argv for a 32-bit interpreter, or None. Never raises."""
    if os.name != "nt":
        return None
    for argv in PY32_LAUNCHER_ARGS:
        try:
            result = subprocess.run(
                argv + ["-c", "import struct;print(struct.calcsize('P')*8)"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout.strip() == "32":
            return argv
    return None


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class GPIB_Scanner_32bit_GUI:
    """Scanner window for the raw 32-bit NI-488.2 path."""

    PROGRAM_VERSION = "1.0"
    # --- Styling constants from PICA Launcher ---
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_TEXT_DARK = '#1A1A1A'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_HEADLINE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"GPIB Scanner - 32-bit raw NI-488.2 v{self.PROGRAM_VERSION}")
        self.root.configure(bg=self.CLR_BG_DARK)

        win_width, win_height = 640, 620
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        self.root.geometry(
            f"{win_width}x{win_height}+{screen_width - win_width - 50}+40")
        self.root.minsize(600, 520)

        self.bits = struct.calcsize("P") * 8
        self.result_queue = queue.Queue()
        self.driver = None
        self.driver_path = None
        self.dll_override = None
        self.python32 = None
        self.found = []          # list of (board, address)
        self.busy = False

        self.setup_styles()
        self.create_widgets()

        self.log(f"GPIB Scanner (raw NI-488.2) started in {self.bits}-bit "
                 "Python.")
        self.log("Nothing has been sent to the bus. Press 'Scan the bus' when "
                 "ready.")
        self.root.after(100, self.process_queue)
        # Loading the driver reads a DLL only; it does not touch the bus.
        self.root.after(200, self.load_driver)

    # ------------------------------------------------------------- styling
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabelframe', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TLabel', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('Title.TLabel', font=self.FONT_TITLE)
        style.configure('Headline.TLabel', font=self.FONT_HEADLINE)
        style.configure('TCheckbutton', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_BG_DARK)])
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 8),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('App.TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Scan.TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_ACCENT_GREEN)
        style.map('Scan.TButton', background=[('active', '#8AB845'),
                                              ('hover', '#8AB845')])

    # ------------------------------------------------------------- widgets
    def create_widgets(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill='both', expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        # --- Row 0: what this Python is, and the way out if it is wrong ---
        status = ttk.Labelframe(main, text=" This computer ", padding=10)
        status.grid(row=0, column=0, sticky='ew')
        status.columnconfigure(0, weight=1)

        self.bitness_label = ttk.Label(status, text="", wraplength=560,
                                       justify='left', style='Title.TLabel')
        self.bitness_label.grid(row=0, column=0, sticky='w')

        self.driver_label = ttk.Label(status, text="Looking for the GPIB "
                                                   "driver...",
                                      wraplength=560, justify='left')
        self.driver_label.grid(row=1, column=0, sticky='w', pady=(6, 0))

        button_row = ttk.Frame(status)
        button_row.grid(row=2, column=0, sticky='w', pady=(10, 0))
        self.relaunch_button = ttk.Button(
            button_row, text="Reopen in 32-bit Python",
            style='Scan.TButton', command=self.relaunch_32bit)
        self.relaunch_button.pack(side='left')
        self.relaunch_button.pack_forget()
        ttk.Button(button_row, text="Choose driver DLL...", style='App.TButton',
                   command=self.choose_dll).pack(side='left', padx=(8, 0))

        # --- Row 1: which boards to look at ---
        boards = ttk.Labelframe(main, text=" Which GPIB boards to look at ",
                                padding=10)
        boards.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        self.board_vars = {}
        for index, board in enumerate((0, 1, 2, 3)):
            var = tk.BooleanVar(value=board in (0, 1))
            self.board_vars[board] = var
            ttk.Checkbutton(boards, text=f"Board {board}", variable=var).grid(
                row=0, column=index, sticky='w', padx=(0, 16))

        # --- Row 2: the one action, plus its promise ---
        action = ttk.Frame(main)
        action.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        action.columnconfigure(1, weight=1)
        self.scan_button = ttk.Button(action, text="Scan the bus",
                                      style='Scan.TButton',
                                      command=self.start_scan)
        self.scan_button.grid(row=0, column=0, sticky='w')
        ttk.Label(action,
                  text="The scan only asks each address to answer the "
                       "handshake.\nNothing is sent to any instrument.",
                  wraplength=420, justify='left').grid(
            row=0, column=1, sticky='w', padx=(12, 0))

        # --- Row 3: the answer, in one line ---
        self.headline = ttk.Label(main, text="Not scanned yet",
                                  style='Headline.TLabel')
        self.headline.grid(row=3, column=0, sticky='w', pady=(14, 4))

        # --- Row 4: what was found, and the log ---
        results = ttk.Labelframe(main, text=" Instruments found ", padding=10)
        results.grid(row=4, column=0, sticky='nsew')
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)

        self.found_frame = ttk.Frame(results)
        self.found_frame.grid(row=0, column=0, sticky='nsew')
        self.found_frame.columnconfigure(0, weight=1)
        self._render_found()

        self.console_widget = scrolledtext.ScrolledText(
            main, state='disabled', height=10, bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.grid(row=5, column=0, sticky='nsew', pady=(12, 0))
        main.rowconfigure(5, weight=1)

        # --- Row 6: housekeeping ---
        footer = ttk.Frame(main)
        footer.grid(row=6, column=0, sticky='ew', pady=(12, 0))
        footer.columnconfigure((0, 1, 2), weight=1)
        ttk.Button(footer, text="Save report", style='App.TButton',
                   command=self.save_report).grid(row=0, column=0, sticky='ew',
                                                  padx=(0, 5))
        ttk.Button(footer, text="Clear log", style='App.TButton',
                   command=self.clear_log).grid(row=0, column=1, sticky='ew',
                                                padx=5)
        ttk.Button(footer, text="Close", style='App.TButton',
                   command=self.root.destroy).grid(row=0, column=2,
                                                   sticky='ew', padx=(5, 0))

        self._update_bitness_label()

    def _update_bitness_label(self):
        if self.bits == 32:
            self.bitness_label.config(
                text="This is 32-bit Python, which is what the lab's GPIB "
                     "driver needs.")
        else:
            self.bitness_label.config(
                text="This is 64-bit Python. A 32-bit GPIB driver cannot be "
                     "used from here.")

    # ------------------------------------------------------------- logging
    def log(self, message, add_timestamp=True):
        """Adds a message to the console widget with a timestamp."""
        self.console_widget.config(state='normal')
        if add_timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        else:
            self.console_widget.insert('end', message)
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def clear_log(self):
        self.console_widget.config(state='normal')
        self.console_widget.delete('1.0', 'end')
        self.console_widget.config(state='disabled')
        self.log("Log cleared.")

    def save_report(self):
        path = filedialog.asksaveasfilename(
            title="Save scan report",
            defaultextension=".txt",
            initialfile=f"gpib_scan_report_{datetime.now():%Y%m%d_%H%M%S}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.console_widget.get('1.0', 'end'))
        except OSError as exc:
            messagebox.showerror("Save failed", f"Could not write the "
                                                f"report:\n\n{exc}")
            return
        self.log(f"Report saved to {path}")

    # ------------------------------------------------------------- driver
    def load_driver(self):
        if os.name != "nt":
            self.driver_label.config(
                text="This tool drives the Windows NI-488.2 DLL; on Linux use "
                     "linux-gpib.")
            self.scan_button.config(state='disabled')
            return

        self.driver, lines = open_driver(self.dll_override)
        for line in lines:
            self.log(line)

        if self.driver is not None:
            self.driver_path = self.driver.path
            self.driver_label.config(
                text=f"GPIB driver ready: {self.driver.path}")
            self.scan_button.config(state='normal')
            self.relaunch_button.pack_forget()
            return

        self.driver_path = None
        self.scan_button.config(state='disabled')
        if self.bits == 64:
            self.driver_label.config(
                text="No usable GPIB driver in this 64-bit Python. If the "
                     "lab's driver is the 32-bit gpib-32.dll, reopen this "
                     "window in 32-bit Python.")
            self.relaunch_button.pack(side='left')
        else:
            self.driver_label.config(
                text="No usable GPIB driver found. Install or repair the GPIB "
                     "driver, or choose the DLL by hand.")

    def choose_dll(self):
        path = filedialog.askopenfilename(
            title="Choose the GPIB driver DLL",
            filetypes=[("DLL files", "*.dll"), ("All files", "*.*")])
        if not path:
            return
        self.dll_override = path
        self.log(f"Trying driver DLL chosen by hand: {path}")
        self.load_driver()

    def relaunch_32bit(self):
        """Starts this same window under a 32-bit interpreter, then closes."""
        self.relaunch_button.config(state='disabled')
        self.log("Looking for a 32-bit Python (py -3.10-32, py -3-32)...")
        argv = find_python32()
        if argv is None:
            self.relaunch_button.config(state='normal')
            self.log("No 32-bit Python found through the py launcher.")
            messagebox.showinfo(
                "32-bit Python not found",
                "No 32-bit Python answered 'py -3.10-32' or 'py -3-32'.\n\n"
                "Install the 32-bit Python 3.10 build, then reopen this "
                "window.")
            return
        command = argv + [os.path.abspath(__file__)]
        self.log("Reopening with: " + " ".join(command))
        try:
            subprocess.Popen(command, close_fds=True)
        except OSError as exc:
            self.relaunch_button.config(state='normal')
            self.log(f"Could not start the 32-bit window: {exc}")
            messagebox.showerror("Could not reopen",
                                 f"Could not start the 32-bit window:\n\n"
                                 f"{exc}")
            return
        self.root.after(400, self.root.destroy)

    # ------------------------------------------------------------- the scan
    def start_scan(self):
        if self.busy or self.driver is None:
            return
        boards = [board for board, var in self.board_vars.items()
                  if var.get()]
        if not boards:
            messagebox.showinfo("Nothing to scan",
                                "Tick at least one board to look at.")
            return
        self.busy = True
        self.scan_button.config(state='disabled')
        self.found = []
        self.headline.config(text="Scanning...")
        self._render_found()
        self.log(f"Scanning boards {', '.join(str(b) for b in boards)}, "
                 "addresses 1 to 30, with ibln only.")
        threading.Thread(target=self.run_scan_thread, args=(boards,),
                         daemon=True).start()

    def run_scan_thread(self, boards):
        """Worker: the census. Writes nothing to any instrument."""
        found = []
        for board in boards:
            probe, sta = self.driver.listener_at(board, 1)
            if probe is None:
                self.result_queue.put(
                    ("log", f"Board {board} is unusable: {sta_text(sta)}; "
                            f"{iberr_text(self.driver.error_code())}"))
                continue
            for pad in SCAN_ADDRESSES:
                present, sta = self.driver.listener_at(board, pad)
                if present is None:
                    self.result_queue.put(
                        ("log", f"Board {board} address {pad}: probe failed "
                                f"{sta_text(sta)}; "
                                f"{iberr_text(self.driver.error_code())}"))
                    continue
                if present:
                    found.append((board, pad))
                    self.result_queue.put(
                        ("log", f"Listener at GPIB{board}::{pad}::INSTR"))
        self.result_queue.put(("scan_done", found))

    # --------------------------------------------------------- identify one
    def start_identify(self, board, pad):
        if self.busy or self.driver is None:
            return
        self.busy = True
        self.scan_button.config(state='disabled')
        self.log(f"Sending one read-only *IDN? to GPIB{board}::{pad}.")
        threading.Thread(target=self.run_identify_thread, args=(board, pad),
                         daemon=True).start()

    def run_identify_thread(self, board, pad):
        """Worker: one *IDN?, one attempt, no retry. Read-only."""
        label = f"GPIB{board}::{pad}"
        handle = self.driver.open_device(board, pad, TIMEOUT_CODES[3])
        if handle < 0:
            self.result_queue.put(
                ("log", f"{label}: could not open "
                        f"({iberr_text(self.driver.error_code())})"))
            self.result_queue.put(("idle", None))
            return
        try:
            started = time.perf_counter()
            sta = self.driver.write(handle, "*IDN?")
            if sta & ERR:
                self.result_queue.put(
                    ("log", f"{label}: *IDN? write failed {sta_text(sta)}; "
                            f"{iberr_text(self.driver.error_code())}"))
                return
            data, sta = self.driver.read(handle)
            elapsed = (time.perf_counter() - started) * 1000
            if data is None:
                self.result_queue.put(
                    ("log", f"{label}: no reply {sta_text(sta)}; "
                            f"{iberr_text(self.driver.error_code())} "
                            f"[{elapsed:.0f} ms]"))
                self.result_queue.put(
                    ("log", "   A Novocontrol Alpha may legitimately ignore "
                            "*IDN?; it answers its own command set. Try "
                            "INTTYP? in the GPIB Lifeline console."))
                return
            self.result_queue.put(
                ("log", f"{label}: {decode(data)}  [{elapsed:.0f} ms]"))
        finally:
            self.driver.close_device(handle)
            self.result_queue.put(("idle", None))

    # ------------------------------------------------------------- plumbing
    def process_queue(self):
        """Checks the queue for messages from the worker thread."""
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "scan_done":
                    self.found = payload
                    self._finish_scan()
                elif kind == "idle":
                    self.busy = False
                    if self.driver is not None:
                        self.scan_button.config(state='normal')
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    def _finish_scan(self):
        self.busy = False
        self.scan_button.config(state='normal')
        count = len(self.found)
        if count == 0:
            self.headline.config(text="No instruments answered")
            self.log("No listeners. Check instrument power, cable seating, "
                     "the address set on the instrument, and that no other "
                     "GPIB program (WinDETA, NI MAX) holds the bus.")
        else:
            noun = "instrument" if count == 1 else "instruments"
            self.headline.config(text=f"{count} {noun} on the bus")
            self.log("A listener answer proves the instrument is powered, "
                     "cabled and correctly addressed, whether or not it "
                     "speaks SCPI.")
        self._render_found()

    def _render_found(self):
        for child in self.found_frame.winfo_children():
            child.destroy()
        if not self.found:
            ttk.Label(self.found_frame,
                      text="Nothing listed yet. Press 'Scan the bus'.").grid(
                row=0, column=0, sticky='w')
            return
        for row, (board, pad) in enumerate(self.found):
            ttk.Label(self.found_frame,
                      text=f"GPIB{board}::{pad}::INSTR").grid(
                row=row, column=0, sticky='w', pady=2)
            ttk.Button(self.found_frame, text="Ask who it is (*IDN?)",
                       style='App.TButton',
                       command=lambda b=board, p=pad: self.start_identify(b, p)
                       ).grid(row=row, column=1, sticky='e', padx=(12, 0),
                              pady=2)


def main():
    root = tk.Tk()
    GPIB_Scanner_32bit_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
