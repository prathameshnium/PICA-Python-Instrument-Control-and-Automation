"""
Module: Monitor_K197A_GUI.py
Purpose: GUI module for logging readings from a Keithley 197A Autoranging
         Microvolt DMM over GPIB.

The 197A is a pre-SCPI instrument with no built-in bus. Remote operation
requires the add-on Model 1973A (or 1972A) IEEE-488 interface, which speaks
IEEE-488-1978. There is no identify query, no *RST and no colon-prefixed
commands. Nothing in this module assumes otherwise.
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import os
import re
import time
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

# --- Packages for Back end ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

import runpy
from multiprocessing import Process

# ---------------------------------------------------------------------------
# Keithley 197A device-dependent commands, via the Model 1973A / 1972A
# IEEE-488 interface.
#
# !! UNVERIFIED !!  These letters have NOT been confirmed against hardware or
# against a machine-readable manual. The authoritative source is the printed
# Model 1973/1972 IEEE-488 Interface Instruction Manual. Correct this table
# from that manual before trusting any reading, and change nothing else.
#
# Every command is terminated with 'X' to execute. Several may be concatenated
# into one string, e.g. "F0R0X".
# ---------------------------------------------------------------------------
CMD = {
    "function": {          # F<n>X
        "DC volts":    "F0",
        "AC volts":    "F1",
        "2-wire ohms": "F2",
        "DC amps":     "F3",
        "AC amps":     "F4",
        "dB":          "F5",
        "4-wire ohms": "F6",   # verify: may not exist as its own function code
    },
    "range": {             # R<n>X ; R0 is autorange on this family
        "auto": "R0", "1": "R1", "2": "R2", "3": "R3",
        "4": "R4", "5": "R5", "6": "R6", "7": "R7",
    },
    "execute": "X",
}

# Unit label per function. Derived from the selected function, never from the
# reply string, because the reply format is itself unverified.
FUNCTION_UNITS = {
    "DC volts": "V",
    "AC volts": "V",
    "2-wire ohms": "Ohm",
    "4-wire ohms": "Ohm",
    "DC amps": "A",
    "AC amps": "A",
    "dB": "dB",
}

FUNCTION_NAMES = [
    "DC volts", "AC volts", "2-wire ohms", "4-wire ohms",
    "DC amps", "AC amps", "dB",
]

RANGE_NAMES = ["auto", "1", "2", "3", "4", "5", "6", "7"]

# The 197A specifications (Rev. B) give a maximum of 3 readings per second.
# Polling faster than the meter can answer returns stale readings that look
# like real data, so the interval is clamped here.
MIN_POLL_INTERVAL = 0.34
DEFAULT_POLL_INTERVAL = 1.0

# The owner has only confirmed one address on this rack (SR830 at
# GPIB0::8::INSTR). The 197A address is a placeholder.
DEFAULT_VISA_ADDRESS = "GPIB0::7::INSTR"

# UNVERIFIED: the reply format of the 197A is not documented to us. This
# regex pulls the first floating point number out of whatever comes back.
# Confirm the real format from the 1973/1972 interface manual, or by using
# the raw command box on the panel against the meter.
NUMBER_RE = re.compile(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?')

NO_RESPONSE_MESSAGE = (
    "No response from GPIB address {addr}. The 197A has no built-in IEEE-488 "
    "interface; check that a Model 1973A or 1972A card is fitted, that the "
    "address switches on the card match, and that the meter is in remote. "
    "Run the GPIB Scanner utility to list what is actually on the bus.")


def parse_reading(raw):
    """Pulls the first floating point number out of a 197A reply string.

    Returns (value, raw) where value is None if nothing numeric was found.
    """
    if raw is None:
        return None, ""
    match = NUMBER_RE.search(raw)
    if not match:
        return None, raw
    try:
        return float(match.group(0)), raw
    except ValueError:
        return None, raw


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
        # Go up 2 levels: k197a -> keithley -> pica
        plotter_path = os.path.join(
            script_dir,
            "..", "..", "utils", "PlotterUtil_GUI.py")
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
        # Go up 2 levels: k197a -> keithley -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------


class NoResponseError(Exception):
    """Raised when the address answers nothing at all, so the caller can
    print a diagnosis instead of a traceback."""


class Keithley197A_Backend:
    """Talks to a Keithley 197A through a Model 1973A / 1972A interface.

    Deliberately contains no identify, reset or clear query. Those are
    IEEE 488.2 features the 1973/1972 does not provide.
    """

    def __init__(self, visa_address):
        self.visa_address = visa_address
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 5000

    def probe(self):
        """Sends the bare execute character and tries to read one reply.

        This is the only connection check available: there is no identify
        query on this interface. Raises NoResponseError on a bus timeout.
        """
        try:
            self.instrument.write(CMD["execute"])
            return self.instrument.read().strip()
        except Exception as e:
            if pyvisa is not None and isinstance(e, pyvisa.errors.VisaIOError):
                raise NoResponseError(
                    NO_RESPONSE_MESSAGE.format(addr=self.visa_address))
            raise

    def configure(self, function_name, range_name):
        """Sets function and range in one concatenated command string."""
        # UNVERIFIED: that F and R may be concatenated and that a single
        # trailing X executes both. Confirm from the 1973/1972 manual.
        command = (CMD["function"][function_name]
                   + CMD["range"][range_name]
                   + CMD["execute"])
        self.instrument.write(command)
        return command

    def read_raw(self):
        """Reads one reading from the meter as a raw, unparsed string."""
        # UNVERIFIED: whether the meter talks on demand (a bare read) or
        # needs a trigger command first. Confirm from the interface manual.
        return self.instrument.read().strip()

    def send_raw(self, command_string):
        """Writes an arbitrary string and returns whatever comes back.

        Used by the raw command box. No parsing whatsoever.
        """
        self.instrument.write(command_string)
        try:
            return self.instrument.read().strip()
        except Exception as e:
            if pyvisa is not None and isinstance(e, pyvisa.errors.VisaIOError):
                return "(no reply within timeout)"
            raise

    def close(self):
        """Closes the connection to the instrument."""
        if self.instrument:
            try:
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during 197A shutdown: {e}")
            finally:
                self.instrument = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class K197AMonitorGUI:
    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

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
    FONT_STATUS = ('Segoe UI', 28, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("Keithley 197A Monitor")
        self.root.state('zoomed')
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.start_time = None
        self.backend = None
        self.file_location_path = ""
        self.data_filepath = None
        self.data_storage = {'time': [], 'reading': []}
        self.logo_image = None
        self.data_queue = queue.Queue()
        self.poll_interval = DEFAULT_POLL_INTERVAL
        self.active_unit = "V"

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.log("Keithley 197A monitor ready.")
        self.log("The 197A needs a Model 1973A / 1972A IEEE-488 card to be "
                 "controllable at all.")
        self.log(f"Default address is the placeholder {DEFAULT_VISA_ADDRESS}. "
                 "If that is wrong, run the GPIB Scanner utility (the keypad "
                 "button at the top right) to see what is on the bus.")
        self.log("Command table in this module is UNVERIFIED. Use the Raw "
                 "Command box to confirm the real command letters.")

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
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)
        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
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
        self.create_console_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_raw_frame(scrollable_frame)
        self.create_status_frame(scrollable_frame)

        self.create_graph_frame(right_panel)

        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30
            else:
                target = self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        plotter_button = ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="Keithley 197A Monitor",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(
            side='left',
            padx=20,
            pady=10)
        Label(
            header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE).pack(
            side='right',
            padx=20,
            pady=10)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception:
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    text="LOGO\nERROR",
                    font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT,
                    justify='center')
        else:
            logo_canvas.create_text(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                text="LOGO\nMISSING",
                font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT,
                justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = ("Program Name: Keithley 197A Monitor\n"
                        "Instrument: Keithley 197A Autoranging Microvolt DMM\n"
                        "Interface: Model 1973A / 1972A IEEE-488 card (required)\n"
                        "Max reading rate: 3 readings per second")
        ttk.Label(
            frame,
            text=details_text,
            justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.entries = {}
        pady_val = (5, 5)
        row = 0

        ttk.Label(frame, text="Sample Name:").grid(
            row=row, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        row += 1
        self.entries["Sample Name"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=row, column=0, columnspan=2, padx=10, pady=(0, 5), sticky='ew')
        row += 1

        ttk.Label(frame, text="Operator (optional):").grid(
            row=row, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        row += 1
        self.entries["Operator"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Operator"].grid(
            row=row, column=0, columnspan=2, padx=10, pady=(0, 5), sticky='ew')
        row += 1

        ttk.Label(frame, text="VISA Address:").grid(
            row=row, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        row += 1
        self.entries["Address"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Address"].grid(
            row=row, column=0, columnspan=2, padx=10, pady=(0, 5), sticky='ew')
        self.entries["Address"].insert(0, DEFAULT_VISA_ADDRESS)
        row += 1

        ttk.Label(frame, text="Function:").grid(
            row=row, column=0, padx=10, pady=pady_val, sticky='w')
        ttk.Label(frame, text="Range:").grid(
            row=row, column=1, padx=10, pady=pady_val, sticky='w')
        row += 1
        self.function_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly', values=FUNCTION_NAMES)
        self.function_cb.set(FUNCTION_NAMES[0])
        self.function_cb.grid(row=row, column=0, padx=10, pady=(0, 5), sticky='ew')
        self.range_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly', values=RANGE_NAMES)
        self.range_cb.set("auto")
        self.range_cb.grid(row=row, column=1, padx=10, pady=(0, 5), sticky='ew')
        row += 1

        ttk.Label(
            frame,
            text=f"Poll Interval (s), floor {MIN_POLL_INTERVAL}:").grid(
            row=row, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        row += 1
        self.entries["Interval"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Interval"].grid(
            row=row, column=0, columnspan=2, padx=10, pady=(0, 5), sticky='ew')
        self.entries["Interval"].insert(0, str(DEFAULT_POLL_INTERVAL))
        row += 1

        self.connect_button = ttk.Button(
            frame, text="Connect", command=self.connect_instrument)
        self.connect_button.grid(
            row=row, column=0, columnspan=2, padx=10, pady=4, sticky='ew')
        row += 1
        self.file_button = ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location)
        self.file_button.grid(
            row=row, column=0, columnspan=2, padx=10, pady=4, sticky='ew')
        row += 1

        control_frame = ttk.Frame(frame)
        control_frame.grid(
            row=row, column=0, columnspan=2, padx=10, pady=(10, 10), sticky='ew')
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(
            control_frame,
            text="Start Logging",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.stop_button = ttk.Button(
            control_frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=(5, 0))

    def create_raw_frame(self, parent):
        """The raw command box. Until the command table above is confirmed
        against the printed 1973/1972 manual, this is the most useful control
        on the panel: it writes exactly what is typed and shows the reply
        verbatim, with no parsing."""
        frame = ttk.LabelFrame(parent, text='Raw Command (unparsed)')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text="Sent verbatim, reply shown verbatim in the console.",
            font=self.FONT_SUB_LABEL).grid(
            row=0, column=0, columnspan=2, padx=10, pady=(5, 2), sticky='w')

        self.raw_entry = ttk.Entry(frame, font=self.FONT_BASE)
        self.raw_entry.grid(row=1, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.raw_entry.bind("<Return>", lambda e: self.send_raw_command())

        self.raw_button = ttk.Button(
            frame, text="Send", command=self.send_raw_command)
        self.raw_button.grid(row=1, column=1, padx=(0, 10), pady=(0, 10), sticky='ew')

    def create_status_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Live Status')
        frame.pack(pady=5, padx=10, fill='x')

        status_inner_frame = ttk.Frame(frame, style='TFrame')
        status_inner_frame.pack(fill='x', expand=True, padx=5, pady=5)

        self.reading_label_var = tk.StringVar(value="--.---- V")
        status_label = ttk.Label(
            status_inner_frame,
            textvariable=self.reading_label_var,
            style='Status.TLabel',
            anchor='center',
            padding=(0, 10))
        status_label.pack(pady=10, fill='x')

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(
            parent,
            text='Console Output',
            style='TLabelframe')
        frame.pack(pady=5, padx=10, fill='x', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0,
            relief='flat',
            height=12)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graph')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Reading vs. Time", fontweight='bold')
        self.ax_main.set_xlabel("Elapsed Time (s)")
        self.ax_main.set_ylabel("Reading (V)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _get_interval(self):
        """Reads the poll interval field, clamping it to the meter's own
        maximum reading rate and saying so."""
        try:
            value = float(self.entries["Interval"].get())
        except ValueError:
            self.log(f"Poll interval not a number. Using "
                     f"{DEFAULT_POLL_INTERVAL} s.")
            value = DEFAULT_POLL_INTERVAL
        if value < MIN_POLL_INTERVAL:
            self.log(f"Poll interval {value} s is faster than the 197A can "
                     f"answer (3 readings per second). Clamped to "
                     f"{MIN_POLL_INTERVAL} s.")
            value = MIN_POLL_INTERVAL
        self.entries["Interval"].delete(0, 'end')
        self.entries["Interval"].insert(0, str(value))
        return value

    def connect_instrument(self):
        """Opens the resource and probes it. Reports a diagnosis, never a
        traceback, when nothing answers."""
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed, cannot connect.")
            return False

        address = self.entries["Address"].get().strip()
        if not address:
            self.log("ERROR: VISA address is empty.")
            return False

        if self.backend:
            self.backend.close()
            self.backend = None

        try:
            backend = Keithley197A_Backend(address)
        except Exception as e:
            self.log(f"ERROR: could not open {address}: {e}")
            return False

        try:
            reply = backend.probe()
        except NoResponseError as e:
            self.log(str(e))
            backend.close()
            return False
        except Exception as e:
            self.log(f"ERROR while probing {address}: {e}")
            backend.close()
            return False

        self.backend = backend
        self.log(f"Address {address} answered. Raw reply: {reply!r}")
        self.log("Note: there is no identify query on this interface, so the "
                 "reply above does not prove the instrument is a 197A.")
        return True

    def send_raw_command(self):
        """Writes whatever is in the raw box and logs the reply verbatim."""
        command_string = self.raw_entry.get()
        if not command_string:
            self.log("Raw command box is empty.")
            return
        if not self.backend:
            if not self.connect_instrument():
                return
        try:
            reply = self.backend.send_raw(command_string)
            self.log(f"RAW SENT: {command_string}")
            self.log(f"RAW REPLY: {reply!r}")
        except Exception as e:
            self.log(f"RAW SENT: {command_string}")
            self.log(f"RAW ERROR: {e}")

    def start_measurement(self):
        try:
            sample_name = self.entries["Sample Name"].get().strip()
            operator = self.entries["Operator"].get().strip()
            address = self.entries["Address"].get().strip()
            function_name = self.function_cb.get()
            range_name = self.range_cb.get()

            if not sample_name:
                raise ValueError("Sample name is required.")
            if not self.file_location_path:
                raise ValueError("Save location is required.")

            interval = self._get_interval()
            self.poll_interval = interval
            self.active_unit = FUNCTION_UNITS[function_name]

            if not self.backend:
                if not self.connect_instrument():
                    return

            command = self.backend.configure(function_name, range_name)
            self.log(f"Configured with: {command}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{sample_name}_{ts}_K197A_monitor.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["# PICA - Keithley 197A monitor"])
                writer.writerow(
                    [f"# Module: Monitor_K197A_GUI.py, version {self.PROGRAM_VERSION}"])
                writer.writerow([f"# Sample: {sample_name}"])
                writer.writerow([f"# Operator: {operator}"])
                writer.writerow([f"# Instrument: Keithley 197A at {address}"])
                writer.writerow(
                    ["# Interface: Model 1973A / 1972A IEEE-488 (assumed; "
                     "not identified by the instrument)"])
                writer.writerow([f"# Function: {function_name}"])
                writer.writerow([f"# Range: {range_name}"])
                writer.writerow([f"# Poll interval (s): {interval}"])
                writer.writerow(["# Command table verified against manual: NO"])
                writer.writerow(
                    [f"# Started: {datetime.now().isoformat(timespec='seconds')}"])
                writer.writerow(
                    ["Timestamp", "Elapsed (s)", "Reading", "Unit", "Raw response"])

            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(f"{function_name}: {sample_name}",
                                   fontweight='bold')
            self.ax_main.set_ylabel(f"Reading ({self.active_unit})")
            self.canvas.draw()

            self.log(f"Logging {function_name} every {interval} s...")
            self.start_time = time.time()

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error", f"Could not start logging.\n{e}")

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Logging stopped by user.")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')

    def _measurement_worker(self):
        """Worker thread for handling blocking instrument calls."""
        while self.is_running:
            try:
                raw = self.backend.read_raw()
                elapsed = time.time() - self.start_time
                self.data_queue.put((elapsed, raw))
                time.sleep(self.poll_interval)
            except Exception as e:
                self.data_queue.put(e)
                break

    def _process_data_queue(self):
        """Processes data from the worker thread to update the GUI."""
        try:
            while not self.data_queue.empty():
                item = self.data_queue.get_nowait()
                if isinstance(item, Exception):
                    self.log(f"RUNTIME ERROR in worker thread: {item}")
                    self.stop_measurement()
                    return

                elapsed, raw = item
                value, raw_text = parse_reading(raw)

                if value is None:
                    # A reply we cannot read as a number is logged and written
                    # with a blank value, so a bad parse never kills a run.
                    self.log(f"Unparsed reply: {raw_text!r}")
                    value_text = ""
                else:
                    self.reading_label_var.set(
                        f"{value:.6g} {self.active_unit}")
                    self.log(f"{value:.6g} {self.active_unit}")
                    value_text = f"{value:.6e}"

                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        f"{elapsed:.2f}",
                        value_text,
                        self.active_unit,
                        raw_text])

                if value is not None:
                    self.data_storage['time'].append(elapsed)
                    self.data_storage['reading'].append(value)
                    self.line_main.set_data(
                        self.data_storage['time'],
                        self.data_storage['reading'])
                    self.ax_main.relim()
                    self.ax_main.autoscale_view()
                    self.figure.tight_layout(pad=3.0)
                    self.canvas.draw_idle()

        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Logging running. Stop and exit?"):
                self.stop_measurement()
                if self.backend:
                    self.backend.close()
                self.root.destroy()
        else:
            if self.backend:
                self.backend.close()
            self.root.destroy()


def main():
    root = tk.Tk()
    K197AMonitorGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
