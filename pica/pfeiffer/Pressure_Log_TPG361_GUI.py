"""
Module: Pressure_Log_TPG361_GUI.py
Purpose: GUI module for logging vacuum pressure vs. time from a Pfeiffer
         Vacuum TPG 361 SingleGauge controller.

  Instrument: Pfeiffer Vacuum TPG 361 SingleGauge
              D-35614 Asslar  |  P/N PT G28 040  |  S/N 44877780
              Mains 100-240 V, 50-60 Hz, 40 VA
  Interface:  RS-232C (9-pin D-sub on the rear panel), spoken to through
              PyVISA as an ASRL resource. There is no GPIB on this box, so
              it never appears in the launcher's GPIB scan -- pick the COM
              port here instead.

  Serial defaults (TPG 36x factory settings): 9600 baud, 8 data bits, no
  parity, 1 stop bit, no handshake. Baud is selectable below because the
  front panel can be set to 9600 / 19200 / 38400.

  Protocol -- the Pfeiffer "mnemonic" handshake used by the TPG 26x/36x
  family. Every exchange is three steps, and skipping the ENQ leaves the
  controller waiting mid-conversation:

      host -> PR1<CR><LF>              the mnemonic
      TPG  -> <ACK><CR><LF>            0x06 accepted, 0x15 (NAK) rejected
      host -> <ENQ>                    0x05, sent RAW with no terminator
      TPG  -> 0,+1.0000E-03<CR><LF>    status , pressure

  Mnemonics this module uses, all read-only -- nothing here ever changes a
  setting on the controller, so it is safe to run alongside a pump-down
  that somebody else is supervising:

      AYT   device identification (type, part no., serial no., firmware)
      UNI   pressure unit currently displayed  (0 mbar, 1 Torr, 2 Pa,
                                                3 micron, 4 hPa, 5 Volt)
      PR1   pressure of gauge 1 -- "status,value"

  PR1 status codes: 0 measurement ok, 1 underrange, 2 overrange,
  3 sensor error, 4 sensor off, 5 no sensor, 6 identification error.
  Only status 0 rows are plotted; every row, good or bad, is written to
  the data file with its status so a gap in the curve can be explained
  afterwards.

  Unattended runs: a pump-down is logged overnight, so this module follows
  the PICA hardening pattern -- retry-forever reconnect with backoff, an
  fsync after every data row, Windows keep-awake while running, and no
  modal dialog once the run has started (banner + console + beep instead).
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import platform
import ctypes
import traceback
import threading
import queue
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Audible alert for unattended runs (Windows only) ---
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# --- Packages for Back end ---
try:
    import pyvisa
    from pyvisa import constants as visa_constants
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    visa_constants = None
    PYVISA_AVAILABLE = False

import runpy
from multiprocessing import Process


def run_script_process(script_path):
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 1 level: pfeiffer -> pica
        plotter_path = os.path.join(
            script_dir, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 1 level: pfeiffer -> pica
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


# -------------------------------------------------------------------------------
# --- PROTOCOL CONSTANTS AND PURE HELPERS ---
# -------------------------------------------------------------------------------
# Kept as module-level functions with no instrument in sight so the reply
# parsing can be exercised by the test suite without a TPG 361 on the bench.

ETX = '\x03'      # reset the controller's comms state machine
ENQ = b'\x05'     # "send me the data you accepted" -- raw, no terminator
ACK = '\x06'
NAK = '\x15'

# PR1 status field -> what the gauge is telling you.
PRESSURE_STATUS = {
    0: "Measurement data okay",
    1: "Underrange",
    2: "Overrange",
    3: "Sensor error",
    4: "Sensor off",
    5: "No sensor",
    6: "Identification error",
}

# UNI reply -> unit name. The TPG displays and reports in whichever of these
# is selected on its front panel; the module reads it rather than assuming
# mbar, so the data file is always labelled with the truth.
PRESSURE_UNITS = {
    0: "mbar",
    1: "Torr",
    2: "Pa",
    3: "micron",
    4: "hPa",
    5: "Volt",
}


def parse_pressure_reply(reply):
    """Turn a PR1 reply into (status_code, value_float).

    A well-formed reply is "0,+1.0000E-03". Anything else is a protocol
    fault worth reporting by name, not a silent NaN -- a pump-down log that
    quietly fills with zeros is worse than one that stops and says why.
    """
    parts = [p.strip() for p in str(reply).split(',')]
    if len(parts) < 2:
        raise ValueError(f"Malformed PR1 reply: {reply!r}")
    try:
        status = int(parts[0])
    except ValueError:
        raise ValueError(f"Non-numeric status in PR1 reply: {reply!r}")
    try:
        value = float(parts[1])
    except ValueError:
        raise ValueError(f"Non-numeric pressure in PR1 reply: {reply!r}")
    return status, value


def parse_unit_reply(reply):
    """Turn a UNI reply into a unit name; unknown codes keep their number.

    An unrecognised code must not stop a run -- the pressure is still valid,
    only its label is in doubt, so the label says so and logging continues.
    """
    try:
        code = int(str(reply).strip())
    except ValueError:
        return "unknown"
    return PRESSURE_UNITS.get(code, f"unit-{code}")


def status_text(code):
    """Human-readable form of a PR1 status code."""
    return PRESSURE_STATUS.get(code, f"Unknown status {code}")


def format_pressure(value, unit):
    """Vacuum spans decades, so pressure is always shown in exponent form."""
    return f"{value:.3E} {unit}"


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class TPG361_Backend:
    """Read-only serial link to a Pfeiffer TPG 361 SingleGauge.

    Nothing in this class writes a setting to the controller. The three
    mnemonics used (AYT, UNI, PR1) are all queries, so attaching this
    module to a gauge that is already guarding a running experiment cannot
    change what that gauge does.
    """

    TIMEOUT_MS = 4000

    def __init__(self, visa_address, baud_rate=9600):
        self.visa_address = visa_address
        self.baud_rate = int(baud_rate)
        self.instrument = None
        self.unit = "mbar"
        self.identity = ""
        self._rm = None
        self.connect()

    # ------------------------------------------------------------------ link
    def connect(self):
        """Open the serial port and confirm the controller answers."""
        self._rm = pyvisa.ResourceManager()
        self.instrument = self._rm.open_resource(self.visa_address)
        self.instrument.timeout = self.TIMEOUT_MS

        # 8-N-1, no handshake: the TPG 36x factory setting. Each attribute is
        # set on its own because a backend that rejects one (pyvisa-py does
        # not expose flow control on every platform) must not cost us the
        # rest of the configuration.
        serial_settings = [('baud_rate', self.baud_rate), ('data_bits', 8)]
        if visa_constants is not None:
            serial_settings += [
                ('parity', visa_constants.Parity.none),
                ('stop_bits', visa_constants.StopBits.one),
                ('flow_control', visa_constants.ControlFlow.none),
            ]
        for attr, value in serial_settings:
            try:
                setattr(self.instrument, attr, value)
            except Exception as e:
                print(f"  Serial attribute '{attr}' not settable: {e}")

        self.instrument.write_termination = '\r\n'
        self.instrument.read_termination = '\r\n'

        # ETX clears any half-finished exchange left by a previous program
        # that wrote a mnemonic and never sent the ENQ. It is the one control
        # character the protocol defines for exactly this, and it changes no
        # setting. Sent raw, unacknowledged, and any stale bytes are dropped.
        try:
            self.instrument.write_raw(ETX.encode('ascii'))
            time.sleep(0.1)
            self._drain()
        except Exception:
            pass

        self.identity = self.query_mnemonic('AYT')
        print(f"TPG 361 Connected: {self.identity}")
        self.unit = parse_unit_reply(self.query_mnemonic('UNI'))
        print(f"TPG 361 reporting in: {self.unit}")

    def _drain(self):
        """Throw away anything already sitting in the input buffer."""
        original = self.instrument.timeout
        try:
            self.instrument.timeout = 120
            while True:
                self.instrument.read()
        except Exception:
            pass
        finally:
            self.instrument.timeout = original

    def query_mnemonic(self, mnemonic):
        """Run one full mnemonic / ACK / ENQ / data exchange.

        The ACK line is read in a short loop: a controller that was mid-reply
        when we opened the port can hand back one stale line before its ACK,
        and treating that as a failure would make every fresh connection look
        broken.
        """
        if self.instrument is None:
            raise IOError("TPG 361 is not connected.")

        self.instrument.write(mnemonic)

        acknowledged = False
        for _ in range(3):
            reply = self.instrument.read()
            if ACK in reply:
                acknowledged = True
                break
            if NAK in reply:
                raise IOError(
                    f"TPG 361 rejected '{mnemonic}' (NAK). Check that the "
                    "mnemonic is supported and the gauge is fitted.")
        if not acknowledged:
            raise IOError(f"No ACK from the TPG 361 for '{mnemonic}'.")

        # ENQ carries no terminator -- write() would append CRLF and the
        # controller would sit there waiting instead of answering.
        self.instrument.write_raw(ENQ)
        return self.instrument.read().strip()

    # --------------------------------------------------------------- reading
    def read_pressure(self):
        """One PR1 reading as (status_code, value, unit)."""
        status, value = parse_pressure_reply(self.query_mnemonic('PR1'))
        return status, value, self.unit

    def reconnect(self):
        """Close and re-open the port, re-reading identity and unit.

        Used by the worker's retry-forever loop. Re-reading UNI matters: if
        somebody power-cycled the controller and it came back on a different
        display unit, the rest of the file would otherwise be mislabelled.
        """
        try:
            self.close()
        except Exception as e:
            print(f"  Pre-reconnect cleanup warning: {e}")
        self.connect()

    def close(self):
        """Close the serial link. Never raises."""
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception as e:
                print(f"Warning: issue during TPG 361 shutdown: {e}")
            finally:
                self.instrument = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            finally:
                self._rm = None


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------

class PressureMonitorGUI:
    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    # Windows keep-awake flags (SetThreadExecutionState)
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    # Reconnect wait ladder, seconds. Caps rather than growing forever: a
    # pump-down that recovers at 03:00 should resume within the minute.
    RECONNECT_BACKOFFS = (5, 10, 30, 60)

    BAUD_RATES = ("9600", "19200", "38400")

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Modern Dark Theme (PICA Standard) ---
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 26, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("Pfeiffer TPG 361 Pressure Logger")
        try:
            self.root.state('zoomed')  # Launch maximized
        except tk.TclError:
            # 'zoomed' is a Windows-only window state; X11 (including the
            # xvfb display CI runs under) rejects it. Not being maximised is
            # not a reason to refuse to open.
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.stop_event = threading.Event()
        self.start_time = None
        self.backend = None
        self.file_location_path = ""
        self.data_filepath = None
        self.unit = "mbar"
        self.data_storage = {'time': [], 'pressure': []}
        self.logo_image = None
        self.data_queue = queue.Queue()

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure(
            'Status.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_STATUS)
        style.configure(
            'StatusSub.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL)
        style.configure('TCheckbutton',
                        background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT,
                        font=self.FONT_BASE)
        style.map('TCheckbutton',
                  background=[('active', self.CLR_BG_DARK)])

        # --- Style for Entry and Combobox widgets for better visibility ---
        style.configure('TEntry',
                        fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK,
                        borderwidth=0)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            arrowcolor=self.CLR_TEXT_DARK,
            selectbackground=self.CLR_ACCENT_GOLD,
            selectforeground=self.CLR_TEXT_DARK)

        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Start.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])

        style.configure(
            'TLabelframe',
            background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER,
            borderwidth=1)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE)

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        self.create_header()
        self.create_banner()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)
        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_status_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)

        self.create_graph_frame(right_panel)

        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        """sashpos() has no effect until the PanedWindow is mapped -- an early
        call fails SILENTLY -- so measure the real content width and retry
        until the position verifiably sticks."""
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        self.header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        self.header_frame.pack(side='top', fill='x')

        plotter_button = ttk.Button(
            self.header_frame, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        gpib_button = ttk.Button(
            self.header_frame, text="📟", command=launch_gpib_scanner, width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            self.header_frame,
            text="Vacuum Pressure Logger",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(
            self.header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_banner(self):
        """A one-line alert strip under the header.

        This is what replaces the modal dialog once a run has started: a
        messagebox raised at 02:00 blocks the queue drain behind a button
        nobody is there to press, and the log stops. The banner shouts
        without stopping anything.
        """
        self.banner_var = tk.StringVar(value="")
        self.banner = tk.Label(
            self.root, textvariable=self.banner_var, bg=self.CLR_ACCENT_RED,
            fg=self.CLR_HEADER, font=self.FONT_TITLE, anchor='w', padx=12)
        # Not packed yet -- it appears only when there is something to say.

    def _show_banner(self, message):
        self.banner_var.set(message)
        if not self.banner.winfo_ismapped():
            self.banner.pack(side='top', fill='x', after=self.header_frame)

    def _clear_banner(self):
        self.banner_var.set("")
        if self.banner.winfo_ismapped():
            self.banner.pack_forget()

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, text="LOGO\nERROR",
                    font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(
                self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, text="LOGO\nMISSING",
                font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(
            frame, text="UGC-DAE Consortium for Scientific Research",
            font=institute_font, background=self.CLR_BG_DARK).grid(
            row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(
            frame, text="Mumbai Centre", font=institute_font,
            background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = ("Program Name: Pressure vs. Time Logger\n"
                        "Instrument: Pfeiffer TPG 361 SingleGauge (RS-232)\n"
                        "Reading: gauge 1 (PR1), read-only -- no setting is changed")
        ttk.Label(frame, text=details_text, justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        Label(frame, text="Log File Name:").grid(
            row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        ttk.Label(frame, text="Logging Delay (s):").grid(
            row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Delay"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].grid(row=3, column=0, padx=10, pady=(0, 5), sticky='ew')
        self.entries["Delay"].insert(0, "2.0")

        ttk.Label(frame, text="TPG 361 Serial Port (VISA):").grid(
            row=4, column=0, padx=10, pady=pady_val, sticky='w')
        # Editable, not readonly: a COM port that VISA does not enumerate can
        # still be typed in by hand (ASRL4::INSTR), which is the difference
        # between a working evening and a re-install of the VISA backend.
        self.port_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.port_cb.grid(row=5, column=0, padx=10, pady=(0, 10), sticky='ew')

        ttk.Label(frame, text="Baud Rate:").grid(
            row=6, column=0, padx=10, pady=pady_val, sticky='w')
        self.baud_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly',
                                    values=self.BAUD_RATES)
        self.baud_cb.set("9600")   # TPG 36x factory default
        self.baud_cb.grid(row=7, column=0, padx=10, pady=(0, 10), sticky='ew')

        self.log_scale_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame, text="Logarithmic pressure axis",
            variable=self.log_scale_var,
            command=self._apply_axis_scale).grid(
            row=8, column=0, padx=10, pady=(0, 5), sticky='w')

        self.scan_button = ttk.Button(
            frame, text="Scan for Serial Ports", command=self._scan_for_serial_ports)
        self.scan_button.grid(row=9, column=0, padx=10, pady=4, sticky='ew')
        self.file_button = ttk.Button(
            frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_button.grid(row=10, column=0, padx=10, pady=4, sticky='ew')

        control_frame = ttk.Frame(frame)
        control_frame.grid(row=11, column=0, padx=10, pady=(10, 10), sticky='ew')
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(
            control_frame, text="Start Logging", command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.stop_button = ttk.Button(
            control_frame, text="Stop", command=self.stop_measurement,
            style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=(5, 0))

    def create_status_frame(self, parent):
        """Live pressure, plus the gauge's own status line underneath it."""
        frame = ttk.LabelFrame(parent, text='Live Status')
        frame.pack(pady=5, padx=10, fill='x')

        status_inner_frame = ttk.Frame(frame, style='TFrame')
        status_inner_frame.pack(fill='x', expand=True, padx=5, pady=5)

        self.pressure_label_var = tk.StringVar(value="-.---E+00 mbar")
        ttk.Label(
            status_inner_frame, textvariable=self.pressure_label_var,
            style='Status.TLabel', anchor='center', padding=(0, 10)).pack(
            pady=(10, 0), fill='x')

        self.gauge_status_var = tk.StringVar(value="Not connected")
        ttk.Label(
            status_inner_frame, textvariable=self.gauge_status_var,
            style='StatusSub.TLabel', anchor='center').pack(pady=(0, 10), fill='x')

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Console Output', style='TLabelframe')
        frame.pack(pady=5, padx=10, fill='x', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', bd=0, relief='flat')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Pick the COM port the TPG 361 is on, "
                 "then Start.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graph')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Pressure vs. Time", fontweight='bold')
        self.ax_main.set_xlabel("Elapsed Time (s)")
        self.ax_main.set_ylabel("Pressure (mbar)")
        self.ax_main.grid(True, which='both', linestyle='--', alpha=0.6)
        self._apply_axis_scale()

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _apply_axis_scale(self):
        """Log or linear y-axis.

        Log is the default: a pump-down runs from 1000 mbar to 1e-6 mbar and
        on a linear axis the whole interesting half of that is a flat line on
        the floor. Linear stays available for a gauge held at one pressure.
        """
        try:
            self.ax_main.set_yscale('log' if self.log_scale_var.get() else 'linear')
            self.ax_main.relim()
            self.ax_main.autoscale_view()
            self.canvas.draw_idle()
        except Exception as e:
            self.log(f"Axis scale change failed: {e}")

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _set_keep_awake(self, enable):
        """Stop Windows from sleeping mid-run (display may still sleep).
        Best-effort no-op on other platforms."""
        try:
            flags = self.ES_CONTINUOUS | (self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _beep(self, times=1):
        """Audible alert. Beeps in a daemon thread so the GUI never blocks;
        falls back to the Tk bell. Used instead of a modal dialog on
        unattended-run events."""
        def _ring():
            for _ in range(times):
                if HAS_WINSOUND and platform.system() == "Windows":
                    winsound.Beep(880, 250)
                else:
                    try:
                        self.root.bell()
                    except Exception:
                        return
                time.sleep(0.15)
        threading.Thread(target=_ring, daemon=True).start()

    # ------------------------------------------------------------------ run
    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get().strip(),
                'delay': float(self.entries["Delay"].get()),
                'port': self.port_cb.get().strip(),
                'baud': self.baud_cb.get().strip(),
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError(
                    "Log file name, delay, serial port, baud rate and save "
                    "location are all required.")
            if params['delay'] <= 0:
                raise ValueError("Logging delay must be greater than zero.")

            self.backend = TPG361_Backend(params['port'], params['baud'])
            self.unit = self.backend.unit
            self.log(f"Connected: {self.backend.identity}")
            self.log(f"Gauge reporting in {self.unit}.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_Pressure_TPG361.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Log File: {params['sample_name']}"])
                writer.writerow([f"# Instrument: Pfeiffer TPG 361 "
                                 f"({self.backend.identity})"])
                writer.writerow([f"# Port: {params['port']} @ "
                                 f"{params['baud']} baud, 8-N-1"])
                writer.writerow([f"# Pressure unit: {self.unit}"])
                writer.writerow([f"# Started: "
                                 f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
                writer.writerow(["Timestamp", "Elapsed Time (s)",
                                 f"Pressure ({self.unit})", "Status Code",
                                 "Status"])
                f.flush()
                os.fsync(f.fileno())
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.stop_event.clear()
            self._clear_banner()
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_ylabel(f"Pressure ({self.unit})")
            self.ax_main.set_title(
                f"Pressure Log: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()

            self._set_keep_awake(True)   # a pump-down log may run for days
            self.log("Starting pressure logging...")
            self.start_time = time.time()

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, args=(params['delay'],),
                daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            # Startup is the one moment somebody IS at the keyboard, so a
            # dialog here is fair -- nothing is running yet to be blocked.
            messagebox.showerror("Initialization Error",
                                 f"Could not start logging.\n{e}")
            if self.backend:
                self.backend.close()
                self.backend = None

    def stop_measurement(self):
        if not self.is_running:
            return
        self.is_running = False
        self.stop_event.set()
        self.log("Logging stopped. Data file is complete on disk.")
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self._set_keep_awake(False)
        if self.backend:
            self.backend.close()
            self.backend = None
        self.gauge_status_var.set("Disconnected")

    def _measurement_worker(self, delay_s):
        """Worker thread: read, queue, sleep. Never gives up on a comm error.

        A serial cable knocked loose at 02:00 must not end a pump-down log,
        so a read failure backs off and reconnects forever; only Stop ends
        the loop.
        """
        comm_failures = 0
        while self.is_running and not self.stop_event.is_set():
            try:
                status, value, unit = self.backend.read_pressure()
                elapsed = time.time() - self.start_time
                self.data_queue.put((elapsed, status, value, unit))
                comm_failures = 0
                # stop_event.wait() instead of sleep(): Stop stays instant
                # even with a 60 s logging delay.
                if self.stop_event.wait(delay_s):
                    break
            except Exception as e:
                comm_failures += 1
                self.data_queue.put(f"LOG:Comm error ({e}). Recovering...")
                if not self._reconnect_with_backoff(comm_failures):
                    break

    def _reconnect_with_backoff(self, attempt):
        """Close and re-open the port, escalating the wait (5 -> 10 -> 30 ->
        60 s cap). Loops until reconnected; returns False only if Stop was
        requested while waiting."""
        while self.is_running and not self.stop_event.is_set():
            delay_s = self.RECONNECT_BACKOFFS[
                min(attempt - 1, len(self.RECONNECT_BACKOFFS) - 1)]
            self.data_queue.put(
                f"LOG:Reconnect attempt in {delay_s} s (Stop stays responsive)...")
            if self.stop_event.wait(delay_s):
                return False
            try:
                self.backend.reconnect()
                self.data_queue.put("LOG:Reconnected. Resuming logging.")
                self.data_queue.put("BANNER_CLEAR")
                return True
            except Exception as e:
                attempt += 1
                self.data_queue.put(f"LOG:Reconnect failed: {e}")
        return False

    def _process_data_queue(self):
        """Drain the worker's queue on the main thread and update the GUI."""
        try:
            while not self.data_queue.empty():
                item = self.data_queue.get_nowait()

                if isinstance(item, str):
                    if item == "BANNER_CLEAR":
                        self._clear_banner()
                    elif item.startswith("LOG:"):
                        message = item[4:]
                        self.log(message)
                        if "Comm error" in message:
                            self._show_banner(
                                "COMMS LOST — retrying. Logging resumes "
                                "automatically; the file so far is safe.")
                            self._beep(2)
                    continue

                elapsed, status, value, unit = item
                self.pressure_label_var.set(format_pressure(value, unit))
                text = status_text(status)
                self.gauge_status_var.set(text)

                if status == 0:
                    self.log(f"P = {format_pressure(value, unit)}")
                else:
                    # Logged loudly but not fatal: an underrange on a Pirani
                    # is the normal end of a pump-down, not a failure.
                    self.log(f"Gauge status {status}: {text}")

                self._write_row(elapsed, value, status, text)

                if status == 0:
                    self.data_storage['time'].append(elapsed)
                    self.data_storage['pressure'].append(value)
                    self.line_main.set_data(self.data_storage['time'],
                                            self.data_storage['pressure'])
                    self.ax_main.relim()
                    self.ax_main.autoscale_view()
                    self.figure.tight_layout(pad=3.0)
                    self.canvas.draw_idle()

        except queue.Empty:
            pass  # This is normal

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _write_row(self, elapsed, value, status, text):
        """Append one row and fsync it.

        fsync, not just flush: a power cut during an unattended run must not
        take the last few minutes of the pump-down with it in an OS buffer.
        """
        try:
            with open(self.data_filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 f"{elapsed:.2f}", f"{value:.4E}", status, text])
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            self.log(f"ERROR: could not write data row: {e}")
            self._show_banner(f"FILE WRITE FAILED — {e}")

    # ---------------------------------------------------------------- helpers
    def _scan_for_serial_ports(self):
        """List the ASRL (serial) resources VISA knows about.

        Only ASRL resources are offered, and none of them is opened or
        written to by the scan. The TPG 361 answers a protocol that is not
        SCPI -- a *IDN? aimed at it would go unanswered and could leave its
        state machine mid-exchange -- so identification happens at Start,
        via AYT, on the one port the user picked.
        """
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for serial (ASRL) resources...")
            try:
                resources = [r for r in rm.list_resources()
                             if r.upper().startswith("ASRL")]
            finally:
                rm.close()
            if resources:
                self.log(f"Found: {resources}")
                self.port_cb['values'] = resources
                if not self.port_cb.get():
                    self.port_cb.set(resources[0])
            else:
                self.log("No serial resources found. Type the port by hand, "
                         "e.g. ASRL3::INSTR, if you know which COM it is on.")
        except Exception as e:
            self.log(f"ERROR during serial scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Logging is running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            if self.backend:
                self.backend.close()
            self._set_keep_awake(False)
            self.root.destroy()


def main():
    root = tk.Tk()
    PressureMonitorGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
