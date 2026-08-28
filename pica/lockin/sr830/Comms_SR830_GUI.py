"""
===============================================================================
 PROGRAM:      PICA SR830 Lock-in Communication and Control
 PURPOSE:      A panel for talking to a Stanford Research Systems SR830 DSP
               lock-in amplifier: connect and identify, read back and change
               every important setting, and watch X, Y, R and Theta live.
               X (in phase), Y (quadrature) and R (magnitude) are plotted
               against time; R and Theta are X and Y in polar form.

               This is a communication and control module, NOT a measurement
               module. There is deliberately no sweep and no acquisition loop.
               The live readout exists so the instrument can be seen
               responding while the settings are being touched.

               Every enumerated code table below is transcribed from the SR830
               manual, chapter 5 "Remote Programming", DETAILED COMMAND LIST:
               https://www.thinksrs.com/downloads/pdfs/manuals/SR830m.pdf
 AUTHOR:       Prathamesh Deshmukh
 VERSION:      V: 1.0
===============================================================================
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import threading
import queue
from datetime import datetime
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


def run_script_process(script_path):
    """Execute a script with runpy in its own directory, in a new process."""
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
        # Go up 2 levels: sr830 -> lockin -> pica
        plotter_path = os.path.join(
            script_dir, "..", "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 2 levels: sr830 -> lockin -> pica
        scanner_path = os.path.join(
            script_dir, "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}")


# An SR830 answers *IDN? with "Stanford_Research_Systems,SR830,s/n,ver".
# Instruments are identified by this string, never by address: the default in
# the address box is only a starting value and any address can be re-set from
# an instrument's front panel.
SR830_IDN_MARKER = "SR830"


def is_sr830_idn(idn):
    """True if a *IDN? reply came from an SR830 lock-in amplifier."""
    return SR830_IDN_MARKER in str(idn).upper()


# -----------------------------------------------------------------------------
# --- ENUMERATED CODE TABLES ---
# The SR830 reports and accepts integer codes, not physical values. Index into
# each list with the code to get the value a human should read. A dropdown that
# reads "24" where it should read "300 ms" is how a setting gets logged wrong.
# -----------------------------------------------------------------------------

# SR830 manual ch.5, REFERENCE and PHASE COMMANDS, FMOD: external i=0,
# internal i=1.
FMOD_LABELS = ["External", "Internal"]

# SR830 manual ch.5, REFERENCE and PHASE COMMANDS, RSLP: external reference
# trigger, sine zero crossing i=0, TTL rising edge i=1, TTL falling edge i=2.
RSLP_LABELS = ["Sine zero crossing", "TTL rising edge", "TTL falling edge"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, ISRC: A i=0, A-B i=1,
# I (1 MOhm) i=2, I (100 MOhm) i=3. The two current settings are the
# 1e6 V/A and 1e8 V/A transimpedance gains.
ISRC_LABELS = ["A", "A-B", "I (1 MOhm, 1e6 V/A)", "I (100 MOhm, 1e8 V/A)"]
ISRC_SHORT = ["A", "A-B", "I 1e6", "I 1e8"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, ICPL: AC i=0, DC i=1.
ICPL_LABELS = ["AC", "DC"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, IGND: Float i=0, Ground i=1.
IGND_LABELS = ["Float", "Ground"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, ILIN: no filters i=0,
# line notch in i=1, 2xline notch in i=2, both notch filters in i=3.
ILIN_LABELS = ["No filters", "Line notch", "2x Line notch", "Both notches"]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, SYNC: off i=0,
# synchronous filtering below 200 Hz i=1.
SYNC_LABELS = ["Off", "On (below 200 Hz)"]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, SENS table, codes 0-26.
SENS_LABELS = [
    "2 nV/fA", "5 nV/fA", "10 nV/fA", "20 nV/fA", "50 nV/fA", "100 nV/fA",
    "200 nV/fA", "500 nV/fA", "1 uV/pA", "2 uV/pA", "5 uV/pA", "10 uV/pA",
    "20 uV/pA", "50 uV/pA", "100 uV/pA", "200 uV/pA", "500 uV/pA",
    "1 mV/nA", "2 mV/nA", "5 mV/nA", "10 mV/nA", "20 mV/nA", "50 mV/nA",
    "100 mV/nA", "200 mV/nA", "500 mV/nA", "1 V/uA",
]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, OFLT table, codes 0-19.
OFLT_LABELS = [
    "10 us", "30 us", "100 us", "300 us", "1 ms", "3 ms", "10 ms", "30 ms",
    "100 ms", "300 ms", "1 s", "3 s", "10 s", "30 s", "100 s", "300 s",
    "1 ks", "3 ks", "10 ks", "30 ks",
]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, OFSL: 6 dB/oct i=0,
# 12 dB/oct i=1, 18 dB/oct i=2, 24 dB/oct i=3.
OFSL_LABELS = ["6 dB/oct", "12 dB/oct", "18 dB/oct", "24 dB/oct"]
OFSL_DB = [6, 12, 18, 24]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, RMOD: High Reserve i=0,
# Normal i=1, Low Noise (minimum) i=2.
RMOD_LABELS = ["High Reserve", "Normal", "Low Noise"]

# SR830 manual ch.5, STATUS BYTE DEFINITIONS, LIA STATUS BYTE.
LIA_STATUS_BITS = [
    "INPUT/RESRV overload", "FILTR overload", "OUTPT overload",
    "Reference unlock", "Detection frequency range switched",
    "Time constant changed indirectly", "Data storage triggered", "unused",
]

# SR830 manual ch.5, STATUS BYTE DEFINITIONS, ERROR STATUS BYTE.
ERROR_STATUS_BITS = [
    "unused", "Backup error", "RAM error", "unused", "ROM error",
    "GPIB error", "DSP error", "Math error",
]

# SR830 manual ch.5, REFERENCE and PHASE COMMANDS: FREQ is limited to
# 0.001 <= f <= 102000 Hz, SLVL to 0.004 <= x <= 5.000 Vrms, PHAS to
# -360.00 <= x <= 729.99 degrees and HARM to an integer from 1 to 19999.
# SR830 manual ch.5, AUX INPUT and OUTPUT COMMANDS: AUXV is limited to
# -10.500 <= x <= 10.500 V.
FREQ_MIN, FREQ_MAX = 0.001, 102000.0
SLVL_MIN, SLVL_MAX = 0.004, 5.0
PHAS_MIN, PHAS_MAX = -360.0, 729.99
HARM_MIN, HARM_MAX = 1, 19999
AUXV_MIN, AUXV_MAX = -10.5, 10.5

# The order in which the instrument state is read. Kept in one place so the
# settings panel and the log header always see the same sequence.
SETTINGS_QUERIES = [
    ("fmod", "FMOD?"),
    ("freq", "FREQ?"),
    ("harm", "HARM?"),
    ("slvl", "SLVL?"),
    ("phas", "PHAS?"),
    ("rslp", "RSLP?"),
    ("isrc", "ISRC?"),
    ("icpl", "ICPL?"),
    ("ignd", "IGND?"),
    ("ilin", "ILIN?"),
    ("sync", "SYNC?"),
    ("sens", "SENS?"),
    ("oflt", "OFLT?"),
    ("ofsl", "OFSL?"),
    ("rmod", "RMOD?"),
]

FLOAT_KEYS = ("freq", "slvl", "phas")

# key -> (SR830 mnemonic, kind, table of labels or (low, high) bounds)
SETTABLE = {
    "fmod": ("FMOD", "enum", FMOD_LABELS),
    "freq": ("FREQ", "float", (FREQ_MIN, FREQ_MAX)),
    "slvl": ("SLVL", "float", (SLVL_MIN, SLVL_MAX)),
    "phas": ("PHAS", "float", (PHAS_MIN, PHAS_MAX)),
    "harm": ("HARM", "int", (HARM_MIN, HARM_MAX)),
    "rslp": ("RSLP", "enum", RSLP_LABELS),
    "isrc": ("ISRC", "enum", ISRC_LABELS),
    "icpl": ("ICPL", "enum", ICPL_LABELS),
    "ignd": ("IGND", "enum", IGND_LABELS),
    "ilin": ("ILIN", "enum", ILIN_LABELS),
    "sync": ("SYNC", "enum", SYNC_LABELS),
    "sens": ("SENS", "enum", SENS_LABELS),
    "oflt": ("OFLT", "enum", OFLT_LABELS),
    "ofsl": ("OFSL", "enum", OFSL_LABELS),
    "rmod": ("RMOD", "enum", RMOD_LABELS),
    "auxv1": ("AUXV 1,", "float", (AUXV_MIN, AUXV_MAX)),
    "auxv2": ("AUXV 2,", "float", (AUXV_MIN, AUXV_MAX)),
    "auxv3": ("AUXV 3,", "float", (AUXV_MIN, AUXV_MAX)),
    "auxv4": ("AUXV 4,", "float", (AUXV_MIN, AUXV_MAX)),
}


def build_set_command(key, value):
    """Validate one key and value and return (command, human readable text).

    Raises ValueError with a plain message when the key is unknown or the
    value falls outside what the SR830 accepts. Rejecting here is better than
    sending it and letting the instrument silently clamp.
    """
    key = str(key).strip().lower()
    if key not in SETTABLE:
        raise ValueError(
            "Unknown setting '%s'. Known keys: %s"
            % (key, ", ".join(sorted(SETTABLE))))
    mnemonic, kind, table = SETTABLE[key]
    text = str(value).strip()

    if kind == "enum":
        try:
            code = int(float(text))
        except ValueError:
            raise ValueError(
                "%s expects an integer code, got '%s'" % (key, text))
        if not 0 <= code < len(table):
            raise ValueError(
                "%s code %d is out of range 0..%d"
                % (key, code, len(table) - 1))
        return ("%s %d" % (mnemonic, code),
                "%s = %d (%s)" % (key, code, table[code]))

    if kind == "int":
        try:
            number = int(float(text))
        except ValueError:
            raise ValueError("%s expects an integer, got '%s'" % (key, text))
        low, high = table
        if not low <= number <= high:
            raise ValueError(
                "%s must be between %d and %d" % (key, low, high))
        return "%s %d" % (mnemonic, number), "%s = %d" % (key, number)

    try:
        number = float(text)
    except ValueError:
        raise ValueError("%s expects a number, got '%s'" % (key, text))
    low, high = table
    if not low <= number <= high:
        raise ValueError("%s must be between %g and %g" % (key, low, high))
    if mnemonic.endswith(","):
        return ("%s%.4f" % (mnemonic, number), "%s = %g" % (key, number))
    return "%s %.6g" % (mnemonic, number), "%s = %g" % (key, number)


def decode_status_byte(value, names):
    """Return the names of the set bits in a status byte, or ['none']."""
    flags = [names[bit] for bit in range(8) if value & (1 << bit)]
    return flags if flags else ["none"]


def build_log_header(module_name, version, sample, operator, idn, address,
                     settings, interval):
    """The fuller commented header, '#' on every line for PlotterUtil_GUI.

    Every settings line comes from the instrument itself at the moment the
    file is opened, never from the GUI widgets. The two can differ and the
    instrument is the truth.
    """
    ilin = settings["ilin"]
    return "\n".join([
        "# PICA - SR830 lock-in monitor",
        "# Module: %s, version %s" % (module_name, version),
        "# Sample: %s" % sample,
        "# Operator: %s" % operator,
        "# Instrument: %s" % idn,
        "# VISA address: %s" % address,
        "# Reference: %s, %.4f Hz, harmonic %d"
        % (FMOD_LABELS[settings["fmod"]].lower(), settings["freq"],
           settings["harm"]),
        "# Sine amplitude (Vrms): %.3f" % settings["slvl"],
        "# Phase offset (deg): %.2f" % settings["phas"],
        "# Input: %s, coupling %s, shield %s"
        % (ISRC_SHORT[settings["isrc"]],
           ICPL_LABELS[settings["icpl"]],
           IGND_LABELS[settings["ignd"]].lower()),
        "# Filters: line %s, 2x line %s, sync %s"
        % ("on" if ilin & 1 else "off",
           "on" if ilin & 2 else "off",
           "on" if settings["sync"] else "off"),
        "# Sensitivity: %s" % SENS_LABELS[settings["sens"]],
        "# Time constant: %s, slope %d dB/oct, reserve %s"
        % (OFLT_LABELS[settings["oflt"]], OFSL_DB[settings["ofsl"]],
           RMOD_LABELS[settings["rmod"]]),
        "# Poll interval (s): %g" % interval,
        "# Started: %s" % datetime.now().isoformat(timespec="seconds"),
        "Timestamp,Elapsed (s),X (V),Y (V),R (V),Theta (deg)",
        "",
    ])


def diagnose_connection_failure(address, error):
    """A clean diagnosis, not a traceback."""
    return [
        "Could not talk to an SR830 at '%s'." % address,
        "Details: %s" % error,
        "Check that:",
        "  - the instrument is powered on and the GPIB cable is seated,",
        "  - the address matches the SR830 [Setup] GPIB address,",
        "  - NI-488.2 / NI-VISA or the Keysight IO Libraries are installed,",
        "  - no other program is currently holding the GPIB board.",
    ]


# -----------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -----------------------------------------------------------------------------

class SR830Backend:
    """A thin wrapper around the SR830 over VISA."""

    def __init__(self, visa_address):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("PyVISA is not installed.")
        self.address = visa_address
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 5000

        # SR830 manual ch.5, SETUP COMMANDS: the SR830 sends responses to only
        # ONE interface. OUTX 1 selects GPIB, OUTX 0 selects RS232. This has to
        # go out before any query or queries time out on a good connection.
        self.instrument.write('OUTX 1')

        # Unlike some older instruments the SR830 does answer *IDN?, so it
        # doubles as the connection check.
        self.idn = self.instrument.query('*IDN?').strip()

        # This module writes SLVL (sine amplitude into the sample) and AUXV
        # (up to +/-10.5 V on the rear DC outputs). GPIB addresses get
        # changed, so confirm what actually answered before any of that can
        # be sent to it.
        if not is_sr830_idn(self.idn):
            try:
                self.instrument.close()
            finally:
                self.instrument = None
            raise ConnectionError(
                "%s is not an SR830: it identifies itself as '%s'. Refusing "
                "to send lock-in commands. Scan the bus and use the SR830's "
                "actual address." % (visa_address, self.idn))

    def read_settings(self):
        """Read the whole instrument state in the canonical order."""
        settings = {}
        for key, command in SETTINGS_QUERIES:
            reply = self.instrument.query(command).strip()
            if key in FLOAT_KEYS:
                settings[key] = float(reply)
            else:
                settings[key] = int(float(reply))
        return settings

    def read_status(self):
        """Read the LIA status byte and the error status byte."""
        lias = int(float(self.instrument.query('LIAS?').strip()))
        errs = int(float(self.instrument.query('ERRS?').strip()))
        return lias, errs

    def snap(self):
        """One SNAP? query for X, Y, R and Theta.

        SR830 manual ch.5, DATA TRANSFER COMMANDS: SNAP? records the requested
        parameters at a single instant. Four separate OUTP? calls would return
        four numbers taken at four different moments.
        """
        reply = self.instrument.query('SNAP? 1,2,3,4').strip()
        parts = [float(value) for value in reply.split(',')]
        return parts[0], parts[1], parts[2], parts[3]

    def read_aux_inputs(self):
        """Read the four Aux Inputs with OAUX?."""
        return [float(self.instrument.query('OAUX? %d' % i).strip())
                for i in (1, 2, 3, 4)]

    def read_display(self, channel):
        """Read the CH1 or CH2 display value with OUTR?."""
        return float(self.instrument.query('OUTR? %d' % channel).strip())

    def read_single(self, parameter):
        """Read one of X, Y, R or Theta with OUTP? i (i = 1, 2, 3 or 4)."""
        return float(self.instrument.query('OUTP? %d' % parameter).strip())

    def apply_setting(self, key, value):
        command, text = build_set_command(key, value)
        self.instrument.write(command)
        return command, text

    def auto_function(self, command):
        """Run AGAN, ARSV, APHS or 'AOFF i'. All of them are write only."""
        self.instrument.write(command)

    def reset(self):
        """Send *RST, then re-select GPIB because the reset clears OUTX."""
        self.instrument.write('*RST')
        time.sleep(1.0)
        self.instrument.write('OUTX 1')

    def close(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception as exc:
                print("Warning: issue while closing the SR830: %s" % exc)
            finally:
                self.instrument = None


# -----------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -----------------------------------------------------------------------------

class SR830CommsGUI:
    PROGRAM_VERSION = "1.0"
    MODULE_NAME = "Comms_SR830_GUI.py"
    DEFAULT_ADDRESS = "GPIB0::8::INSTR"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 520

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
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
    # Three distinguishable traces on the cream graph background. X and Y can
    # both be negative and R never is, so they share one volts axis.
    CLR_TRACE_X = '#BA6B5E'
    CLR_TRACE_Y = '#5E7C8A'
    CLR_TRACE_R = '#4A3F35'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_READOUT = ('Segoe UI', 22, 'bold')

    # Settings widgets driven by a dropdown, in the order they are laid out.
    ENUM_ROWS = [
        ("fmod", "Reference source", FMOD_LABELS),
        ("rslp", "External trigger", RSLP_LABELS),
        ("isrc", "Input configuration", ISRC_LABELS),
        ("icpl", "Input coupling", ICPL_LABELS),
        ("ignd", "Input shield", IGND_LABELS),
        ("ilin", "Notch filters", ILIN_LABELS),
        ("sync", "Synchronous filter", SYNC_LABELS),
        ("sens", "Sensitivity", SENS_LABELS),
        ("oflt", "Time constant", OFLT_LABELS),
        ("ofsl", "Filter slope", OFSL_LABELS),
        ("rmod", "Dynamic reserve", RMOD_LABELS),
    ]

    # Settings widgets driven by a text entry plus a Set button.
    NUMERIC_ROWS = [
        ("freq", "Frequency (Hz)", "%.4f"),
        ("slvl", "Amplitude (Vrms)", "%.3f"),
        ("phas", "Phase (deg)", "%.2f"),
        ("harm", "Harmonic (n)", "%d"),
    ]

    def __init__(self, root):
        self.root = root
        self.root.title("PICA SR830 Lock-in Communication and Control")
        try:
            self.root.state('zoomed')
        except tk.TclError:
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1250, 850)

        self.backend = None
        self.io_lock = threading.Lock()
        self.action_queue = queue.Queue()
        self.data_queue = queue.Queue()
        self.is_monitoring = False
        self.start_time = None
        self.data_storage = {'time': [], 'x': [], 'y': [], 'r': []}
        self.file_location_path = ""
        self.data_filepath = None
        self.logo_image = None
        self.enum_widgets = {}
        self.numeric_widgets = {}
        self._populating = False

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(150, self._process_action_queue)

    # ------------------------------------------------------------- appearance
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
            'Sub.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL)
        style.configure(
            'Readout.TLabel',
            background=self.CLR_GRAPH_BG,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_READOUT)
        style.configure(
            'ReadoutName.TLabel',
            background=self.CLR_GRAPH_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL)
        style.configure('TCheckbutton',
                        background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT,
                        font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_BG_DARK)])

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
            padding=(10, 7),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Small.TButton',
            font=self.FONT_SUB_LABEL,
            padding=(6, 4),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0)
        style.configure(
            'Start.TButton',
            font=self.FONT_BASE,
            padding=(10, 7),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(10, 7),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)

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

    # ---------------------------------------------------------------- widgets
    def create_widgets(self):
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(
            self.main_pane, width=self.LEFT_PANEL_WIDTH)
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
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame)
        self.create_connection_frame(scrollable_frame)
        self.create_settings_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)

        self.create_readout_frame(right_panel)
        self.create_graph_frame(right_panel)

        self._set_controls_enabled(False)
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        ttk.Button(
            header_frame, text="\U0001F4C8",
            command=launch_plotter_utility, width=3).pack(
            side='right', padx=10, pady=5)
        ttk.Button(
            header_frame, text="\U0001F4DF",
            command=launch_gpib_scanner, width=3).pack(
            side='right', padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="SR830 Lock-in Communication and Control",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(
            header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

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
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception:
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    text="LOGO\nERROR", font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT, justify='center')
        else:
            logo_canvas.create_text(
                self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                text="LOGO\nMISSING", font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT, justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(
            frame, text="UGC-DAE Consortium for Scientific Research",
            font=institute_font).grid(
            row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font).grid(
            row=1, column=1, padx=10, sticky='nw')
        ttk.Separator(frame, orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = (
            "Program Name: SR830 Communication and Control\n"
            "Instrument: SRS SR830 DSP Lock-in Amplifier\n"
            "Scope: connect, inspect, set, monitor. No measurement protocol.")
        ttk.Label(frame, text=details_text, justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_connection_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="SR830 VISA address:").grid(
            row=0, column=0, columnspan=2, padx=10, pady=(6, 2), sticky='w')
        self.address_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.address_cb.grid(
            row=1, column=0, columnspan=2, padx=10, pady=(0, 6), sticky='ew')
        self.address_cb.set(self.DEFAULT_ADDRESS)

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 6),
                        sticky='ew')
        button_row.columnconfigure((0, 1, 2), weight=1)
        ttk.Button(button_row, text="Scan",
                   command=self._scan_for_visa_instruments).grid(
            row=0, column=0, sticky='ew', padx=(0, 4))
        self.connect_button = ttk.Button(
            button_row, text="Connect", command=self.connect,
            style='Start.TButton')
        self.connect_button.grid(row=0, column=1, sticky='ew', padx=4)
        self.disconnect_button = ttk.Button(
            button_row, text="Disconnect", command=self.disconnect,
            style='Stop.TButton', state='disabled')
        self.disconnect_button.grid(row=0, column=2, sticky='ew', padx=(4, 0))

        self.idn_var = tk.StringVar(value="Not connected.")
        ttk.Label(frame, textvariable=self.idn_var, style='Sub.TLabel',
                  wraplength=440, justify='left').grid(
            row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='w')

    def create_settings_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Instrument Settings')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            frame,
            text=("Read back from the instrument on connect, so this panel "
                  "shows what the SR830 actually holds."),
            style='Sub.TLabel', wraplength=440, justify='left').grid(
            row=row, column=0, columnspan=3, padx=10, pady=(6, 8), sticky='w')
        row += 1

        for key, label, fmt in self.NUMERIC_ROWS:
            ttk.Label(frame, text=label + ":").grid(
                row=row, column=0, padx=(10, 6), pady=3, sticky='w')
            entry = ttk.Entry(frame, font=self.FONT_BASE, width=14)
            entry.grid(row=row, column=1, padx=(0, 6), pady=3, sticky='ew')
            entry.bind('<Return>', lambda e, k=key: self._set_numeric(k))
            ttk.Button(
                frame, text="Set", style='Small.TButton', width=5,
                command=lambda k=key: self._set_numeric(k)).grid(
                row=row, column=2, padx=(0, 10), pady=3)
            self.numeric_widgets[key] = (entry, fmt)
            row += 1

        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', padx=10, pady=6)
        row += 1

        for key, label, labels in self.ENUM_ROWS:
            ttk.Label(frame, text=label + ":").grid(
                row=row, column=0, padx=(10, 6), pady=3, sticky='w')
            combo = ttk.Combobox(
                frame, font=self.FONT_BASE, state='readonly', values=labels)
            combo.grid(row=row, column=1, columnspan=2, padx=(0, 10), pady=3,
                       sticky='ew')
            combo.bind('<<ComboboxSelected>>',
                       lambda e, k=key: self._set_enum(k))
            self.enum_widgets[key] = combo
            row += 1

        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', padx=10, pady=6)
        row += 1

        auto_frame = ttk.Frame(frame)
        auto_frame.grid(row=row, column=0, columnspan=3, padx=10, pady=(0, 6),
                        sticky='ew')
        auto_frame.columnconfigure((0, 1, 2), weight=1)
        # SR830 manual ch.5, AUTO FUNCTIONS. All four are write only.
        autos = [
            ("Auto Gain", "AGAN"),
            ("Auto Reserve", "ARSV"),
            ("Auto Phase", "APHS"),
            ("Auto Offset X", "AOFF 1"),
            ("Auto Offset Y", "AOFF 2"),
            ("Auto Offset R", "AOFF 3"),
        ]
        for index, (label, command) in enumerate(autos):
            ttk.Button(
                auto_frame, text=label, style='Small.TButton',
                command=lambda c=command, t=label: self._auto(c, t)).grid(
                row=index // 3, column=index % 3, sticky='ew', padx=2, pady=2)
        row += 1

        refresh_frame = ttk.Frame(frame)
        refresh_frame.grid(row=row, column=0, columnspan=3, padx=10,
                           pady=(0, 10), sticky='ew')
        refresh_frame.columnconfigure((0, 1), weight=1)
        ttk.Button(
            refresh_frame, text="Read All From Instrument",
            command=self.refresh_settings).grid(
            row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(
            refresh_frame, text="Read Status Bytes",
            command=self.read_status).grid(
            row=0, column=1, sticky='ew', padx=(4, 0))

        self.settings_frame = frame

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Console Output')
        frame.pack(pady=5, padx=10, fill='both', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled', height=10, bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word',
            bd=0, relief='flat')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Set the VISA address and press Connect.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found. Install it with 'pip install pyvisa'.")

    def create_readout_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Live Readout (SNAP?)')
        frame.pack(side='top', fill='x', padx=5, pady=(5, 0))

        numbers = tk.Frame(frame, bg=self.CLR_GRAPH_BG)
        numbers.pack(fill='x', padx=8, pady=8)
        numbers.columnconfigure((0, 1, 2, 3), weight=1)

        self.readout_vars = {}
        for index, (key, title) in enumerate(
                [('x', 'X (V)'), ('y', 'Y (V)'),
                 ('r', 'R (V)'), ('theta', 'Theta (deg)')]):
            cell = tk.Frame(numbers, bg=self.CLR_GRAPH_BG)
            cell.grid(row=0, column=index, sticky='ew', padx=6, pady=4)
            ttk.Label(cell, text=title, style='ReadoutName.TLabel').pack()
            var = tk.StringVar(value="--")
            ttk.Label(cell, textvariable=var, style='Readout.TLabel').pack()
            self.readout_vars[key] = var

        controls = ttk.Frame(frame)
        controls.pack(fill='x', padx=8, pady=(0, 8))

        ttk.Label(controls, text="Poll interval (s):").grid(
            row=0, column=0, padx=(0, 6), pady=3, sticky='w')
        self.interval_entry = ttk.Entry(controls, font=self.FONT_BASE, width=8)
        self.interval_entry.grid(row=0, column=1, pady=3, sticky='w')
        self.interval_entry.insert(0, "0.5")

        self.monitor_start_button = ttk.Button(
            controls, text="Start", command=self.start_monitor,
            style='Start.TButton')
        self.monitor_start_button.grid(row=0, column=2, padx=(12, 4), pady=3)
        self.monitor_stop_button = ttk.Button(
            controls, text="Stop", command=self.stop_monitor,
            style='Stop.TButton', state='disabled')
        self.monitor_stop_button.grid(row=0, column=3, padx=4, pady=3)

        self.log_to_file_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls, text="Log to file", variable=self.log_to_file_var).grid(
            row=0, column=4, padx=(16, 4), pady=3, sticky='w')

        ttk.Label(controls, text="Sample:").grid(
            row=1, column=0, padx=(0, 6), pady=3, sticky='w')
        self.sample_entry = ttk.Entry(controls, font=self.FONT_BASE, width=18)
        self.sample_entry.grid(row=1, column=1, columnspan=2, pady=3,
                               sticky='ew')
        ttk.Label(controls, text="Operator:").grid(
            row=1, column=3, padx=(12, 6), pady=3, sticky='e')
        self.operator_entry = ttk.Entry(controls, font=self.FONT_BASE, width=18)
        self.operator_entry.grid(row=1, column=4, pady=3, sticky='ew')
        ttk.Button(
            controls, text="Browse Save Location...",
            command=self._browse_file_location).grid(
            row=1, column=5, padx=(12, 0), pady=3, sticky='e')

    def create_graph_frame(self, parent):
        """X, Y and R against time, each on its own switchable trace.

        X is the component in phase with the reference and Y the quadrature
        component; R = sqrt(X^2+Y^2) is the two of them in polar form. R alone
        hides the sign and hides which of the two the signal is sitting in, so
        a phase problem (everything in Y, nothing in X) is invisible on an R
        plot and obvious here. All three are volts, so one axis carries them.
        """
        graph_container = ttk.LabelFrame(parent, text='X, Y and R vs. Time')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        trace_row = ttk.Frame(graph_container)
        trace_row.pack(fill='x', padx=8, pady=(6, 0))
        ttk.Label(trace_row, text="Traces:", style='Sub.TLabel').pack(
            side='left', padx=(0, 8))

        self.figure = Figure(figsize=(7, 4), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.ax_main = self.figure.add_subplot(1, 1, 1)

        self.trace_lines = {}
        self.trace_vars = {}
        traces = [
            ('x', 'X (in phase)', self.CLR_TRACE_X, 'o'),
            ('y', 'Y (quadrature)', self.CLR_TRACE_Y, 's'),
            ('r', 'R (magnitude)', self.CLR_TRACE_R, '^'),
        ]
        for key, label, colour, marker in traces:
            line, = self.ax_main.plot(
                [], [], color=colour, marker=marker, markersize=3,
                linestyle='-', label=label)
            self.trace_lines[key] = line
            variable = tk.BooleanVar(value=True)
            self.trace_vars[key] = variable
            ttk.Checkbutton(
                trace_row, text=label, variable=variable,
                command=self._refresh_traces).pack(side='left', padx=(0, 12))

        self.ax_main.set_title("X, Y and R vs. Time", fontweight='bold')
        self.ax_main.set_xlabel("Elapsed Time (s)")
        self.ax_main.set_ylabel("Signal (V)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.ax_main.legend(loc='best', fontsize=self.FONT_SIZE_BASE - 2)
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _refresh_traces(self):
        """Redraw from the stored points, showing only the ticked traces.

        An unticked trace is given empty data rather than just hidden, so it
        stops pulling the autoscale: with X and Y switched off the axis fits
        R alone, which is the point of switching them off.
        """
        for key, line in self.trace_lines.items():
            if self.trace_vars[key].get():
                line.set_data(self.data_storage['time'],
                              self.data_storage[key])
            else:
                line.set_data([], [])
        self.ax_main.relim()
        self.ax_main.autoscale_view()
        self.canvas.draw_idle()

    # ---------------------------------------------------------------- logging
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    # --------------------------------------------------------- worker plumbing
    def _submit(self, func, description, on_success=None):
        """Run one blocking instrument call on a worker thread.

        Every VISA access in this module goes through io_lock so the live
        monitor and a settings write can never overlap on the bus.
        """
        def worker():
            try:
                with self.io_lock:
                    result = func()
            except Exception as exc:
                self.action_queue.put(('error', description, exc, None))
                return
            self.action_queue.put(('ok', description, result, on_success))
        threading.Thread(target=worker, daemon=True).start()

    def _process_action_queue(self):
        try:
            while True:
                kind, description, payload, callback = \
                    self.action_queue.get_nowait()
                if kind == 'connect_failed':
                    # A clean diagnosis, never a traceback.
                    for line in diagnose_connection_failure(
                            description, payload):
                        self.log(line)
                    self.idn_var.set("Not connected.")
                    self.connect_button.config(state='normal')
                elif kind == 'error':
                    self.log(f"ERROR ({description}): {payload}")
                else:
                    if description:
                        self.log(description)
                    if callback is not None:
                        callback(payload)
        except queue.Empty:
            pass
        self.root.after(150, self._process_action_queue)

    def _set_controls_enabled(self, connected):
        state = 'normal' if connected else 'disabled'
        for combo in self.enum_widgets.values():
            combo.config(state='readonly' if connected else 'disabled')
        for entry, _fmt in self.numeric_widgets.values():
            entry.config(state=state)
        for child in self.settings_frame.winfo_children():
            self._set_button_state(child, state)
        self.monitor_start_button.config(state=state)
        self.connect_button.config(state='disabled' if connected else 'normal')
        self.disconnect_button.config(state=state)

    def _set_button_state(self, widget, state):
        if isinstance(widget, ttk.Button):
            widget.config(state=state)
        for child in widget.winfo_children():
            self._set_button_state(child, state)

    # ------------------------------------------------------------- connection
    def _scan_for_visa_instruments(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.address_cb['values'] = resources
                for res in resources:
                    if "::8::" in res:
                        self.address_cb.set(res)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def connect(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA is not installed, cannot connect.")
            return
        address = self.address_cb.get().strip()
        if not address:
            self.log("ERROR: enter a VISA address first.")
            return

        self.connect_button.config(state='disabled')
        self.log(f"Connecting to {address} ...")

        def task():
            backend = SR830Backend(address)
            settings = backend.read_settings()
            status = backend.read_status()
            return backend, settings, status

        def worker():
            try:
                with self.io_lock:
                    payload = task()
            except Exception as exc:
                self.action_queue.put(
                    ('connect_failed', address, exc, None))
                return
            self.action_queue.put(('ok', None, payload, self._on_connected))

        threading.Thread(target=worker, daemon=True).start()

    def _on_connected(self, payload):
        backend, settings, status = payload
        self.backend = backend
        self.log(f"Connected. *IDN? -> {backend.idn}")
        self.idn_var.set(f"{backend.idn}  @  {backend.address}")
        self._set_controls_enabled(True)
        self._populate_settings(settings)
        self._report_status(status)

    def disconnect(self):
        if self.is_monitoring:
            self.stop_monitor()
        if self.backend is not None:
            backend, self.backend = self.backend, None
            try:
                with self.io_lock:
                    backend.close()
            except Exception as exc:
                self.log(f"Warning during disconnect: {exc}")
        self.idn_var.set("Not connected.")
        self._set_controls_enabled(False)
        self.log("Disconnected.")

    # --------------------------------------------------------------- settings
    def _populate_settings(self, settings):
        """Show what the instrument actually holds, never a set of defaults."""
        self._populating = True
        try:
            for key, (entry, fmt) in self.numeric_widgets.items():
                entry.delete(0, 'end')
                entry.insert(0, fmt % settings[key])
            for key, combo in self.enum_widgets.items():
                combo.current(settings[key])
        finally:
            self._populating = False
        self.log("Settings panel populated from the instrument.")

    def refresh_settings(self):
        if not self._require_connection():
            return
        self._submit(self.backend.read_settings, None, self._populate_settings)

    def read_status(self):
        if not self._require_connection():
            return
        self._submit(self.backend.read_status, None, self._report_status)

    def _report_status(self, status):
        lias, errs = status
        self.log("LIAS? %d -> %s"
                 % (lias, ", ".join(decode_status_byte(lias, LIA_STATUS_BITS))))
        self.log("ERRS? %d -> %s"
                 % (errs, ", ".join(decode_status_byte(errs, ERROR_STATUS_BITS))))

    def _require_connection(self):
        if self.backend is None:
            self.log("Not connected. Press Connect first.")
            return False
        return True

    def _set_numeric(self, key):
        if not self._require_connection():
            return
        entry, _fmt = self.numeric_widgets[key]
        raw_value = entry.get().strip()
        try:
            # Validated before it is sent. The oscillator covers 1 mHz to
            # 102 kHz and 4 mVrms to 5 Vrms, and out of range values would
            # otherwise be silently clamped by the instrument.
            command, text = build_set_command(key, raw_value)
        except ValueError as exc:
            self.log(f"Rejected: {exc}")
            messagebox.showwarning("Value out of range", str(exc))
            return
        self._submit(
            lambda: self.backend.instrument.write(command),
            f"Sent {command}   ({text})")

    def _set_enum(self, key):
        if self._populating:
            return
        if not self._require_connection():
            return
        code = self.enum_widgets[key].current()
        try:
            command, text = build_set_command(key, code)
        except ValueError as exc:
            self.log(f"Rejected: {exc}")
            return
        self._submit(
            lambda: self.backend.instrument.write(command),
            f"Sent {command}   ({text})")

    def _auto(self, command, label):
        if not self._require_connection():
            return
        self._submit(
            lambda: self.backend.auto_function(command),
            f"Sent {command}   ({label})")

    # ------------------------------------------------------------- monitoring
    def start_monitor(self):
        if not self._require_connection():
            return
        try:
            interval = float(self.interval_entry.get())
            if interval <= 0:
                raise ValueError("Poll interval must be greater than zero.")
        except ValueError as exc:
            messagebox.showwarning("Invalid poll interval", str(exc))
            return

        self.data_filepath = None
        if self.log_to_file_var.get():
            sample = self.sample_entry.get().strip()
            if not sample or not self.file_location_path:
                messagebox.showwarning(
                    "Logging",
                    "A sample name and a save location are required to log "
                    "to file.")
                return
            try:
                self._open_log_file(sample, interval)
            except Exception as exc:
                self.log(f"ERROR: could not open the log file. {exc}")
                return

        self.is_monitoring = True
        self.monitor_start_button.config(state='disabled')
        self.monitor_stop_button.config(state='normal')
        for values in self.data_storage.values():
            values.clear()
        self._refresh_traces()
        self.start_time = time.time()
        self.monitor_interval = interval
        self.log(f"Live readout started, polling SNAP? every {interval} s.")

        threading.Thread(target=self._monitor_worker, daemon=True).start()
        self.root.after(100, self._process_data_queue)

    def _open_log_file(self, sample, interval):
        """Read the settings back from the instrument, then write the header."""
        with self.io_lock:
            settings = self.backend.read_settings()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{sample}_{stamp}_SR830_monitor.dat"
        self.data_filepath = os.path.join(self.file_location_path, filename)
        with open(self.data_filepath, 'w') as handle:
            handle.write(build_log_header(
                self.MODULE_NAME, self.PROGRAM_VERSION, sample,
                self.operator_entry.get().strip(), self.backend.idn,
                self.backend.address, settings, interval))
        self.log(f"Output file created: {filename}")
        self._populate_settings(settings)

    def stop_monitor(self):
        if not self.is_monitoring:
            return
        self.is_monitoring = False
        self.monitor_start_button.config(state='normal')
        self.monitor_stop_button.config(state='disabled')
        self.log("Live readout stopped.")

    def _monitor_worker(self):
        """Worker thread for the blocking SNAP? calls."""
        while self.is_monitoring:
            try:
                with self.io_lock:
                    if self.backend is None:
                        break
                    x, y, r, theta = self.backend.snap()
                elapsed = time.time() - self.start_time
                self.data_queue.put((elapsed, x, y, r, theta))
                time.sleep(self.monitor_interval)
            except Exception as exc:
                self.data_queue.put(exc)
                break

    def _process_data_queue(self):
        try:
            while True:
                item = self.data_queue.get_nowait()
                if isinstance(item, Exception):
                    self.log(f"RUNTIME ERROR in the polling thread: {item}")
                    self.stop_monitor()
                    return
                elapsed, x, y, r, theta = item
                self.readout_vars['x'].set(f"{x:.4E}")
                self.readout_vars['y'].set(f"{y:.4E}")
                self.readout_vars['r'].set(f"{r:.4E}")
                self.readout_vars['theta'].set(f"{theta:.3f}")

                if self.data_filepath:
                    with open(self.data_filepath, 'a') as handle:
                        handle.write("%s,%.3f,%.6E,%.6E,%.6E,%.4f\n" % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            elapsed, x, y, r, theta))

                self.data_storage['time'].append(elapsed)
                self.data_storage['x'].append(x)
                self.data_storage['y'].append(y)
                self.data_storage['r'].append(r)
                self._refresh_traces()
        except queue.Empty:
            pass

        if self.is_monitoring:
            self.root.after(200, self._process_data_queue)

    # ------------------------------------------------------------------ misc
    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_monitoring:
            if not messagebox.askyesno(
                    "Exit", "The live readout is running. Stop and exit?"):
                return
            self.stop_monitor()
        self.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    SR830CommsGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
