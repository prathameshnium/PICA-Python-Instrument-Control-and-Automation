#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 PROGRAM:      Novocontrol Alpha-AN Diagnostics
 VERSION:      1.0
 DATE:         24 Oct 2026

 PURPOSE:
   One button that answers "can this PC talk to the Alpha-AN through VISA?".
   It reports the Python (version, 32- or 64-bit), GPIB-related programs that
   are running (WinALPHA / WinDETA holding the bus is the usual culprit), the
   VISA library and its resource list, and then asks the Alpha *IDN? and
   INTTYP? at the address you give. The answer is shown as one plain sentence
   above the full log.

 READ-ONLY:
   Only queries are sent (every command must contain "?"; anything else is
   refused before it reaches the bus). No *RST, no ACV / DCV, no MST. The
   *RST timing test lives in Alpha_VISA_Diagnostic_64bit.py --reset-test.

 DEPENDENCIES:
   Standard library and Tkinter; pyvisa is imported when Run is pressed, so a
   missing pyvisa is a finding, not a crash.
===============================================================================
"""

import os
import platform
import queue
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

DEFAULT_ADDRESS = "GPIB0::10::INSTR"
QUERY_TIMEOUT_MS = 10000          # same as Frequency_Scan_AlphaAN_GUI.py

VI_ERROR_TMO = -1073807339
VI_ERROR_NLISTENERS = -1073807265
VI_ERROR_NCIC = -1073807264
ERROR_NAMES = {VI_ERROR_TMO: "TMO (timeout)",
               VI_ERROR_NLISTENERS: "NLISTENERS (no listener)",
               VI_ERROR_NCIC: "NCIC (board not Controller-In-Charge)"}

# Process names worth a mention: anything that may already own the GPIB bus.
GPIB_PROCESS_HINTS = ("winalpha", "windeta", "wingeta", "nimax", "ni max",
                      "keysight", "agilent", "labview")

SEPARATOR = "-" * 70


# ===============================================================================
# THE CHECKS (no Tk here; the GUI runs them on a worker thread)
# ===============================================================================

def describe(exc):
    """A VISA error as its name and code; anything else as type: text."""
    code = getattr(exc, "error_code", None)
    if code is None:
        return f"{type(exc).__name__}: {exc}"
    return f"{ERROR_NAMES.get(int(code), 'code')} [{int(code)}] {exc}"


def safe_query(inst, cmd):
    """Send a query. Anything without '?' is refused: this tool is read-only."""
    if "?" not in cmd:
        raise ValueError(f"refused non-query command {cmd!r} (read-only tool)")
    return inst.query(cmd)


def gpib_processes():
    """Names of running programs that may hold the bus (Windows only)."""
    if os.name != "nt":
        return None
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, timeout=20).stdout
    return sorted({row.split('","')[0].strip('"')
                   for row in out.splitlines()
                   if any(k in row.lower() for k in GPIB_PROCESS_HINTS)})


def run_checks(address, emit, visa=None):
    """Run every check, emitting log lines. Returns (ok, headline).

    `visa` is the pyvisa module; tests pass a fake one.
    """
    emit(SEPARATOR)
    emit("1. THIS PC")
    emit(SEPARATOR)
    emit(f"Python {platform.python_version()} {struct.calcsize('P') * 8}-bit"
         f"   {sys.executable}")
    emit(f"OS {platform.platform()}")
    try:
        hits = gpib_processes()
        if hits is not None:
            emit("Programs that may hold the GPIB bus: "
                 + (", ".join(hits) if hits else "none"))
    except Exception as exc:
        emit(f"Could not list running programs: {exc}")

    emit("")
    emit(SEPARATOR)
    emit("2. VISA")
    emit(SEPARATOR)
    if visa is None:
        try:
            import pyvisa as visa
        except ImportError as exc:
            emit(f"pyvisa is not installed: {exc}")
            return False, "pyvisa is not installed (pip install pyvisa)."
    emit(f"pyvisa {getattr(visa, '__version__', '?')}")
    try:
        rm = visa.ResourceManager()
    except Exception as exc:
        emit(f"ResourceManager() failed: {describe(exc)}")
        return False, "No VISA library found. Install NI-VISA (64-bit)."
    emit(f"VISA library: {rm.visalib}")
    try:
        resources = tuple(rm.list_resources())
        emit(f"Resources: {resources}")
    except Exception as exc:
        resources = None
        emit(f"list_resources failed: {describe(exc)}")

    emit("")
    emit(SEPARATOR)
    emit(f"3. ALPHA-AN at {address} (queries only)")
    emit(SEPARATOR)
    if resources is not None and address not in resources:
        emit(f"Note: {address} is not in the resource list; trying anyway.")
    inst = None
    try:
        try:
            inst = rm.open_resource(address)
        except Exception as exc:
            emit(f"open_resource failed: {describe(exc)}")
            return False, f"Could not open {address}. Check the address."
        # Same session settings as Frequency_Scan_AlphaAN_GUI.py.
        inst.timeout = QUERY_TIMEOUT_MS
        inst.read_termination = ""
        inst.write_termination = ""
        inst.send_end = True
        answers = {}
        for cmd in ("*IDN?", "INTTYP?"):
            t = time.monotonic()
            try:
                answers[cmd] = safe_query(inst, cmd).strip()
                emit(f"  {cmd:8} -> {answers[cmd]!r}  "
                     f"({time.monotonic() - t:.2f} s)")
            except Exception as exc:
                emit(f"  {cmd:8} FAILED after {time.monotonic() - t:.2f} s: "
                     f"{describe(exc)}")
                busy = ("  Close WinALPHA / WinDETA and try again."
                        if getattr(exc, "error_code", None) == VI_ERROR_TMO
                        else "")
                return False, f"The Alpha did not answer {cmd}.{busy}"
        try:
            emit(f"  status byte: 0x{inst.read_stb():02X}")
        except Exception as exc:
            emit(f"  read_stb failed: {describe(exc)}")
        return True, f"The Alpha answered: {answers['*IDN?']}"
    finally:
        for handle in (inst, rm):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass


# ===============================================================================
# THE WINDOW
# ===============================================================================

class AlphaANDiagnosticsGUI:
    """Address, Run, one-sentence answer, full log."""

    PROGRAM_NAME = "Novocontrol Alpha-AN Diagnostics"
    PROGRAM_VERSION = "1.0"
    POLL_MS = 60

    # House palette, identical to the other PICA modules.
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#F4EFEA'
    CLR_STATUS_OK = '#6B8E4E'
    CLR_STATUS_BAD = '#BA6B5E'

    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_HEADLINE = ('Segoe UI', 16, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Novocontrol Alpha-AN Diagnostics - 64-bit (VISA)")
        self.root.geometry("960x660")
        self.root.minsize(760, 480)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.queue = queue.Queue()
        self.worker = None
        self.after_id = None
        self.lines = []

        self.setup_styles()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self._log_line(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self._log_line("Read-only: only *IDN? and INTTYP? are sent.")
        self._log_line("Close WinALPHA / WinDETA first, then press Run Check.")

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('Backing.TLabel', background=self.CLR_BG_DARK)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Connect.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Connect.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor=self.CLR_ACCENT_GOLD)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TProgressbar', background=self.CLR_ACCENT_GREEN,
                        troughcolor=self.CLR_INPUT_BG,
                        bordercolor=self.CLR_ACCENT_GOLD)

    def _build(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(header, text=f"{self.PROGRAM_NAME}   [64-bit edition, VISA]",
                  style='Header.TLabel',
                  font=('Segoe UI', self.FONT_BASE[1] + 4, 'bold'),
                  foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)
        ttk.Label(header, text="read-only", style='Header.TLabel',
                  foreground=self.CLR_STATUS_OK).pack(side='right', padx=20)

        ask = ttk.LabelFrame(self.root, text="Is the Alpha-AN reachable?",
                             padding=(10, 8))
        ask.pack(fill='x', padx=12, pady=(10, 4))
        ttk.Label(ask, text="Ask the Alpha at").pack(side='left')
        self.address_var = tk.StringVar(value=DEFAULT_ADDRESS)
        ttk.Entry(ask, textvariable=self.address_var, width=22,
                  font=self.FONT_CONSOLE).pack(side='left', padx=6)
        self.run_btn = ttk.Button(ask, text="Run Check",
                                  style='Connect.TButton', command=self._start)
        self.run_btn.pack(side='left', padx=(6, 0))
        self.save_btn = ttk.Button(ask, text="Save Log As...",
                                   command=self._save_as, state='disabled')
        self.save_btn.pack(side='right')
        ttk.Button(ask, text="Copy All", command=self._copy).pack(
            side='right', padx=(0, 6))

        self.headline_var = tk.StringVar(value="Not checked yet.")
        self.headline = ttk.Label(self.root, textvariable=self.headline_var,
                                  style='Backing.TLabel',
                                  font=self.FONT_HEADLINE,
                                  wraplength=920, justify='left')
        self.headline.pack(fill='x', padx=14, pady=(6, 2))

        self.progress = ttk.Progressbar(self.root, mode='determinate')
        self.progress.pack(fill='x', padx=12)

        self.console = scrolledtext.ScrolledText(
            self.root, wrap='none', font=self.FONT_CONSOLE,
            bg=self.CLR_CONSOLE_BG, fg=self.CLR_TEXT_DARK,
            insertbackground=self.CLR_TEXT_DARK,
            relief='flat', highlightthickness=1,
            highlightbackground=self.CLR_ACCENT_GOLD)
        self.console.pack(fill='both', expand=True, padx=12, pady=(6, 12))
        self.console.config(state='disabled')

    def _log_line(self, text):
        self.lines.append(text)
        self.console.config(state='normal')
        self.console.insert('end', text + "\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _start(self):
        """Run the checks on a worker thread; the queue carries lines back."""
        if self.worker is not None and self.worker.is_alive():
            return
        address = self.address_var.get().strip() or DEFAULT_ADDRESS
        self.run_btn.config(state='disabled')
        self.save_btn.config(state='disabled')
        self.headline_var.set(f"Asking {address}...")
        self.headline.config(foreground=self.CLR_FG_LIGHT)
        self.progress.config(mode='indeterminate')
        self.progress.start(40)
        self.console.config(state='normal')
        self.console.delete('1.0', 'end')
        self.console.config(state='disabled')
        self.lines = [f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}  "
                      f"{datetime.now():%Y-%m-%d %H:%M:%S}"]

        def work():
            try:
                ok, headline = run_checks(address, self.queue.put)
            except Exception as exc:                        # pragma: no cover
                ok, headline = False, f"The check itself failed: {exc}"
            self.queue.put(("__done__", ok, headline))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.after_id = self.root.after(self.POLL_MS, self._drain)

    def _drain(self):
        """Move finished lines onto the console. One tk.after chain only."""
        self.after_id = None
        try:
            while True:
                item = self.queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__done__":
                    self._finish(item[1], item[2])
                    return
                self._log_line(item)
        except queue.Empty:
            pass
        self.after_id = self.root.after(self.POLL_MS, self._drain)

    def _finish(self, ok, headline):
        self.progress.stop()
        self.progress.config(mode='determinate', value=0)
        self.run_btn.config(state='normal')
        self.save_btn.config(state='normal')
        self.headline_var.set(headline)
        self.headline.config(
            foreground=self.CLR_STATUS_OK if ok else self.CLR_STATUS_BAD)
        self._log_line("")
        self._log_line(f"RESULT: {headline}")

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="Save the diagnostics log",
            defaultextension=".log",
            initialfile=datetime.now().strftime(
                "AlphaVisaDiag_%Y%m%d_%H%M%S.log"),
            filetypes=[("Log files", "*.log"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write("\n".join(self.lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self._log_line(f"(log saved to {path})")

    def _copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(self.lines))

    def _on_closing(self):
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        self.root.destroy()


def main():
    root = tk.Tk()
    AlphaANDiagnosticsGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
