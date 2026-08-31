"""
Module: RT_K6517B_CC34_T_Sensing_GUI.py
Purpose: GUI module for RT K6517B CC34 T Sensing GUI v1.

         Cryocon Model 34 equivalent of RT_K6517B_L350_T_Sensing_GUI.py.
         Measurement logic is unchanged: the Keithley 6517B sources a bias
         voltage and measures resistance while temperature is logged
         passively. Only the thermometry differs -- temperature comes from
         Cryocon input channel A instead of Lakeshore 350 input A, and the
         logged heater column is Cryocon Loop 1 output power.

         The Cryocon is treated as READ ONLY. Unlike the Lakeshore version,
         which sends *RST and forces the heater off, this module sends no
         *RST, no CONTROL/STOP and no heater, loop or configuration command,
         so whatever is driving the temperature keeps running untouched.

Cryocon SCPI verified against the Cryo-con User's Guide; the command set is
common to the Model 32/32B/34 family:
  - INPUT? <ch>          -> channel temperature in that channel's display units
  - INPUT <ch>:UNITS?    -> display units (K, C, F, V or O)
  - LOOP <n>:OUTPWR?     -> control loop output power in percent
  - GPIB: factory address 12, EOI framing, no EOS terminator
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
import threading
import queue
import os
import re
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley6517B
    from pyvisa.errors import VisaIOError
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa = None
    Keithley6517B = None
    VisaIOError = None
    PYMEASURE_AVAILABLE = False


# ===============================================================================
# CRYOCON LINK HARDENING  (read-only; inlined so each module stays standalone)
# ===============================================================================
#
# Three failures seen on a Cryo-con Model 34 Rev 3.03A, 28 Aug 2026:
#
#   1. The bus scan identified the instrument, and the very next session's
#      '*IDN?' died inside viWrite with VI_ERROR_TMO. Pressing Start again
#      connected normally. A timeout on the WRITE means the instrument stopped
#      accepting bytes for a moment, not that it is absent or at another
#      address, so the cure is to wait and ask again instead of giving up.
#      Handled by CRYOCON_OPEN_SETTLE_S plus the retry loop in CryoconLink.
#
#   2. A reading query answered with a Cryo-con status string instead of a
#      number and float() raised, which killed the worker thread. The front
#      panel shows dashes for a sensor fault and dots for a reading that is
#      inside the instrument's range but off the sensor's calibration curve;
#      over the bus those arrive as the literal strings below. A reply can
#      also carry a trailing unit character, as in '77.350K', which float()
#      rejects outright. Handled by parse_cryocon_number(), which names the
#      condition instead of raising a bare ValueError.
#
#   3. The Cryocon was picked by address alone. It is at GPIB0::12 as of
#      29 Aug 2026, and the Lakeshore 350 now sits on GPIB1::12 -- the
#      Cryo-con's own factory address. Selection is by '*IDN?' content, so a
#      re-addressed Cryocon is still found and a stranger on the factory
#      address is not mistaken for one.
#
# Nothing in this block writes to the instrument.

# Factory address, used only as a last-resort hint when nothing answers.
CRYOCON_ADDRESS_HINT = "GPIB0::12"
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON", "CRYO CON")

CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before the first command
CRYOCON_MIN_GAP_S = 0.08            # minimum gap between consecutive operations
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries

# Timeout for the identification pass, matched to the standalone GPIB
# scanner so this module does not call an instrument silent that the scanner
# reads without trouble.
IDN_SCAN_TIMEOUT_MS = 2000
PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")

# Literal replies that are status, not data.
CRYOCON_STATUS_STRINGS = {
    '-------': "sensor fault: the sensor is open, disconnected or shorted",
    '.......': ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    'N/A': "the channel is disabled, or the value does not apply",
    'NACK': "the instrument did not acknowledge the command",
}

# Leading signed decimal, with or without an exponent. Used to peel a trailing
# unit character off replies such as '77.350K'.
_CRYOCON_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')

# The dash and dot runs are as long as the display resolution setting makes
# them, so they are matched by shape rather than by a fixed seven characters.
_CRYOCON_FAULT_RE = re.compile(r'^-{2,}$')
_CRYOCON_RANGE_RE = re.compile(r'^\.{2,}$')


# A sensor-status reply ('-------' / '.......') is an INVALID READING, not a
# communication error: the instrument answered, the sensor did not, so no
# amount of reconnecting cures it and the comm-retry path must never be
# entered on one. Almost every one of them is transient -- the Model 34 shows
# dashes for a moment while an input range switches -- so the reading is
# retried in place first. Past that the point's temperature becomes NaN: the
# electrical measurement at that point is still good and is still written,
# only the thermometry column is missing, and the run carries on so that a
# sensor which recovers at 3 a.m. resumes logging on its own.
CRYOCON_READ_RETRIES = 3            # extra tries before a point becomes NaN
CRYOCON_READ_RETRY_S = 0.3          # pause between those tries


class CryoconStatusError(ValueError):
    """A query returned a Cryo-con status string where a number was expected."""


def parse_cryocon_number(raw, what, channel=None):
    """Turn a Cryo-con reply into a float, or say precisely why it is not one.

    Handles three things the plain float() call did not: status strings, a
    trailing unit character, and multi-channel replies, which come back as
    fields separated by semicolons.
    """
    text = str(raw).strip()
    where = f" on channel {channel}" if channel else ""
    if ';' in text:
        text = text.split(';')[0].strip()
    if text in CRYOCON_STATUS_STRINGS:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS[text]}.")
    if _CRYOCON_FAULT_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS['-------']}.")
    if _CRYOCON_RANGE_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': sensor fault, no "
            f"sensor, or {CRYOCON_STATUS_STRINGS['.......']}.")
    if not text:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned an empty reply.")
    try:
        return float(text)
    except ValueError:
        pass
    match = _CRYOCON_NUMBER_RE.match(text)
    if match:
        return float(match.group(0))
    raise CryoconStatusError(
        f"Cryocon {what}{where} returned '{text}' "
        "(sensor fault, no sensor, or reading out of range).")


def is_cryocon_idn(idn):
    """True if a '*IDN?' reply came from a Cryo-con temperature instrument."""
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


def open_cryocon_session(visa_address, log=None):
    """Open a Cryo-con session, retrying the first '*IDN?'.

    Returns (instrument, idn). Raises ConnectionError if nothing answers, or
    if what answers is not a Cryo-con: this module logs the temperature that
    the whole run is indexed by, so reading it off the wrong instrument is
    worse than not running at all.
    """
    if pyvisa is None:
        raise ConnectionError(
            "PyVISA is not available. Install pyvisa and a VISA backend "
            "(NI-VISA or pyvisa-py).")
    say = log if callable(log) else (lambda msg: print(msg))
    rm = pyvisa.ResourceManager()
    last_error = None
    for attempt in range(1, CRYOCON_CONNECT_ATTEMPTS + 1):
        inst = None
        try:
            inst = rm.open_resource(visa_address)
            inst.timeout = CRYOCON_TIMEOUT_MS
            # The Cryocon GPIB port frames lines with EOI and no EOS
            # character, so the PyVISA termination defaults are left alone.
            time.sleep(CRYOCON_OPEN_SETTLE_S)
            idn = inst.query('*IDN?').strip()
            if not idn:
                raise ConnectionError(
                    f"{visa_address} accepted the command but sent no "
                    "identification.")
            if not is_cryocon_idn(idn):
                inst.close()
                raise ConnectionError(
                    f"{visa_address} is not a Cryo-con: it identifies itself "
                    f"as '{idn}'. Scan the bus and pick the Cryocon's actual "
                    f"address (it does not have to be "
                    f"{CRYOCON_ADDRESS_HINT}).")
            if attempt > 1:
                say(f"  Cryocon answered on attempt {attempt}.")
            return inst, idn
        except ConnectionError:
            # Wrong instrument, or a silent one. Retrying will not change
            # the answer, so let it out immediately.
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            raise
        except Exception as exc:
            last_error = exc
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            if attempt < CRYOCON_CONNECT_ATTEMPTS:
                say(f"  Cryocon did not answer at {visa_address} "
                    f"(attempt {attempt} of {CRYOCON_CONNECT_ATTEMPTS}): "
                    f"{type(exc).__name__}. Retrying in "
                    f"{CRYOCON_RETRY_WAIT_S:.1f} s.")
                time.sleep(CRYOCON_RETRY_WAIT_S)
    raise ConnectionError(
        f"No reply from a Cryo-con at {visa_address} after "
        f"{CRYOCON_CONNECT_ATTEMPTS} attempts. Last error: {last_error}. "
        "Check that the instrument is powered, that its SYS menu has "
        "RIO-Port set to GPIB rather than RS-232, and that RIO-Address "
        "matches this VISA address.")


def identify_resources(rm, resources):
    """Return {resource: idn} for every resource that answers '*IDN?'.

    Never raises: an address that is busy, silent or not SCPI simply does not
    appear in the result. Serial resources are not probed at all.
    """
    found = {}
    for res in resources:
        if not str(res).upper().startswith(PROBE_RESOURCE_PREFIXES):
            continue
        inst = None
        try:
            inst = rm.open_resource(res)
            inst.timeout = IDN_SCAN_TIMEOUT_MS
            idn = inst.query('*IDN?').strip()
            if idn:
                found[res] = idn
        except Exception:
            pass
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
                # Let the address settle before the next one is addressed.
                time.sleep(0.05)
    return found



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
        # Go up 3 levels: High_Resistance -> k6517b -> keithley -> pica
        plotter_path = os.path.join(
            script_dir,
            "..", "..", "..", "utils", "PlotterUtil_GUI.py")
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
        # Go up 3 levels: High_Resistance -> k6517b -> keithley -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------


class Cryocon34_Backend:
    """A class to passively read the Cryocon Model 34 Temperature Monitor.

    Every method here is a query. Nothing in this class writes to the
    instrument, so its control loops, heater and settings are untouched.
    """

    def __init__(self, visa_address, log=None):
        self.instrument = None
        self.idn = ""
        # A settle delay and a retried first '*IDN?': on 28 Aug 2026 the very
        # first query of a session died inside viWrite with VI_ERROR_TMO
        # seconds after a bus scan had identified the instrument.
        self.instrument, self.idn = open_cryocon_session(visa_address, log=log)
        print(f"Cryocon Connected: {self.idn}")

    def verify_units(self, sensor):
        """Confirm the channel reports Kelvin.

        INPUT? returns the reading in the channel's own display units, so a
        channel left in C or F would silently log wrong numbers. This is a
        query only -- the units are never changed from here.
        """
        units = self.instrument.query(
            f'INPUT {sensor}:UNITS?').strip().upper()
        if not units.startswith('K'):
            raise ValueError(
                f"Cryocon channel {sensor} is reporting in '{units}', not "
                "Kelvin. Set that channel to K on the Cryocon front panel "
                "(this program never writes to it).")
        print(f"Cryocon channel {sensor} display units: K")

    def get_temperature(self, sensor):
        """Read the Cryocon channel in Kelvin; NaN on a sensor fault.

        A status reply ('-------' or '.......') is retried in place, because
        dashes during an input range switch clear within a second and must
        not cost a data point. If it still will not read, NaN comes back
        rather than an exception: the run keeps its electrical data and
        carries on, and the reading resumes on its own when the sensor
        does. A genuine comm failure still raises.
        """
        # parse_cryocon_number names the condition and copes with a reply
        # such as '77.350K', which the plain float() call rejected outright.
        for attempt in range(CRYOCON_READ_RETRIES + 1):
            raw = self.instrument.query(f'INPUT? {sensor}').strip()
            try:
                return parse_cryocon_number(raw, "temperature",
                                            channel=sensor)
            except CryoconStatusError as e:
                if attempt < CRYOCON_READ_RETRIES:
                    time.sleep(CRYOCON_READ_RETRY_S)
                    continue
                self._sensor_faults = getattr(self, '_sensor_faults', 0) + 1
                if (self._sensor_faults <= 5
                        or self._sensor_faults % 25 == 0):
                    print(f"  Sensor fault #{self._sensor_faults}: "
                          f"temperature logged as NaN, run continues. {e}")
                return float('nan')

    def get_heater_output(self, loop):
        """Control loop output power in percent (read-only)."""
        raw = self.instrument.query(f'LOOP {loop}:OUTPWR?').strip()
        try:
            return parse_cryocon_number(raw, f"loop {loop} output power")
        except CryoconStatusError:
            # The heater column is context, not the measurement. A loop that
            # is off or not configured must not stop the run.
            return 0.0

    def close(self):
        if self.instrument:
            try:
                # Passive: close the session only. No STOP and no heater or
                # loop command, so the Cryocon carries on undisturbed --
                # the user may be monitoring an ongoing experiment.
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Cryocon shutdown: {e}")


class Combined_Backend:
    """Manages both the Cryocon 34 and Keithley 6517B."""

    # Cryocon input channel used for thermometry (fixed, as on the
    # Lakeshore version which always reads input A).
    CC_CHANNEL = 'A'

    def __init__(self):
        self.cryocon = None
        self.keithley = None
        self.params = {}

    def initialize_instruments(self, parameters):
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments ---")
        self.cryocon = Cryocon34_Backend(self.params['cryocon_visa'])
        self.cryocon.verify_units(self.CC_CHANNEL)
        print("Cryocon 34 connection is passive. No settings will be changed.")

        self.keithley = Keithley6517B(self.params['keithley_visa'])
        print(f"Keithley Connected: {self.keithley.id}")
        self._perform_keithley_zero_check()

        self.keithley.source_voltage = self.params['source_voltage']
        self.keithley.current_nplc = 1
        self.keithley.enable_source()
        print(f"Keithley source enabled: {self.params['source_voltage']} V")

    def _perform_keithley_zero_check(self):
        print("  --- Starting Keithley Zero Correction ---")
        self.keithley.reset()
        self.keithley.measure_resistance()
        print("  Step 1: Enabling Zero Check (shorts the input)...")
        self.keithley.write(':SYSTem:ZCHeck ON')
        time.sleep(2)
        print("  Step 2: Acquiring the zero correction value...")
        self.keithley.write(':SYSTem:ZCORrect:ACQuire')
        time.sleep(3)
        print("  Step 3: Disabling Zero Check...")
        self.keithley.write(':SYSTem:ZCHeck OFF')
        time.sleep(1)
        print("  Step 4: Enabling Zero Correction for all measurements...")
        self.keithley.write(':SYSTem:ZCORrect ON')
        time.sleep(1)
        print("  Zero Correction Complete.")

    def get_measurement(self):
        time.sleep(self.params['delay'])
        current_temp = self.cryocon.get_temperature(self.CC_CHANNEL)
        # Loop 1 output power, read only -- reports whatever the Cryocon is
        # doing on its own, which may be non-zero during a passive scan.
        heater_output = self.cryocon.get_heater_output(1)
        resistance = self.keithley.resistance
        if resistance != 0 and resistance != float(
                'inf') and resistance == resistance:
            current = self.params['source_voltage'] / resistance
        else:
            current = 0.0
        return current_temp, heater_output, current, resistance

    def close_instruments(self):
        print("\n--- [Backend] Closing all instrument connections. ---")
        if self.keithley:
            self.keithley.shutdown()
            print("  Keithley connection closed and source OFF.")
        if self.cryocon:
            self.cryocon.close()
            print("  Cryocon connection closed (heater state unchanged).")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class Integrated_RT_GUI:
    PROGRAM_VERSION = "4.2"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 400  # default sash position so the left panel starts fully visible

    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        # Path is three directories up from the script location
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "..",
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

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
    FONT_INPUT = ('Segoe UI', FONT_SIZE_BASE - 1)  # Smaller font for inputs
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("K6517B & Cryocon 34: R-T (T-Sensing)")
        self.root.geometry("1550x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {
            'time': [],
            'temperature': [],
            'current': [],
            'resistance': []}
        self.log_scale_var = tk.BooleanVar(value=True)
        self.logo_image = None  # Attribute to hold the logo image reference
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self._plot_dirty = False

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
            'TCheckbutton',
            background=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            font=self.FONT_BASE)
        style.map(
            'TCheckbutton', background=[
                ('active', self.CLR_GRAPH_BG)], indicatorcolor=[
                ('selected', self.CLR_ACCENT_GREEN)])
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
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
            padding=(
                10,
                9),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        # This is the frame that will be scrolled
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)  # More weight for the graph panel

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)
        self.create_graph_frame(right_panel)

        # sashpos() has no effect until the PanedWindow is actually mapped and
        # laid out — an early call fails SILENTLY. So we (a) wait for the
        # window to be drawn, (b) measure the real required width of the
        # left-panel content instead of guessing, and (c) retry until the
        # sash position verifiably sticks.
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()  # force geometry to be computed

            # Measure the actual content width: inner scrollable frame +
            # vertical scrollbar + a little breathing room. Falls back to
            # LEFT_PANEL_WIDTH if measurement isn't ready yet.
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30  # scrollbar (~15px) + padding
            else:
                target = self.LEFT_PANEL_WIDTH

            self.main_pane.sashpos(0, target)

            # Verify it stuck; if not (widget not mapped yet), retry.
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def create_header(self):
        # --- NEW: Define an italic font for the program name ---
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')

        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="K6517B & Cryocon 34: R-T (T-Sensing)",
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
            font=self.FONT_SUB_LABEL).pack(
            side='right',
            padx=20,
            pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Information',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
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
                # IMPORTANT: Keep a reference to the image to prevent it from
                # being garbage collected
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    text="LOGO\nERROR",
                    font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT,
                    justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                text="LOGO\nMISSING",
                font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT,
                justify='center')

        # Institute Name (larger font)
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 6, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=0,
            column=1,
            padx=10,
            pady=(
                10,
                0),
            sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=1,
            column=1,
            padx=10,
            sticky='nw')

        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)

        # Program details
        details_text = ("Program Name: R vs. T (T-Sensing)\n"
                        "Instruments: Cryocon 34, Keithley 6517B\n"
                        "Measurement Range: 1 Ω to 10 PΩ")
        ttk.Label(
            frame,
            text=details_text,
            justify='left').grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Experiment Parameters',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady_val = (5, 5)

        # --- SIMPLIFIED INPUTS ---
        Label(
            frame,
            text="Sample Name:").grid(
            row=0,
            column=0,
            columnspan=2,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=1, column=0, columnspan=2, padx=10, pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Source Voltage (V):").grid(
            row=2,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Source Voltage"] = Entry(
            frame, font=self.FONT_BASE, width=15)
        self.entries["Source Voltage"].grid(
            row=3, column=0, padx=(
                10, 5), pady=(
                0, 5), sticky='ew')

        Label(
            frame,
            text="Logging Delay (s):").grid(
            row=2,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE, width=15)
        self.entries["Delay"].grid(
            row=3, column=1, padx=(
                5, 10), pady=(
                0, 5), sticky='ew')
        self.entries["Delay"].insert(0, "1.0")  # Default to 1 second

        Label(
            frame,
            text="Cryocon 34 VISA:").grid(
            row=4,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.cryocon_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.cryocon_cb.grid(
            row=5, column=0, padx=(
                10, 5), pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Keithley VISA:").grid(
            row=4,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.keithley_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(
            row=5, column=1, padx=(
                5, 10), pady=(
                0, 10), sticky='ew')

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=6,
            column=0,
            columnspan=2,
            padx=10,
            pady=4,
            sticky='ew')
        self.file_button = ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location)
        self.file_button.grid(
            row=7,
            column=0,
            columnspan=2,
            padx=10,
            pady=4,
            sticky='ew')
        self.start_button = ttk.Button(
            frame,
            text="Start Logging",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=8, column=0, padx=(
                10, 5), pady=(
                10, 10), sticky='ew')
        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=8, column=1, padx=(
                5, 10), pady=(
                10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Console Output',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log(
            "Console initialized. Configure parameters and scan for instruments.")
        if not PYMEASURE_AVAILABLE:
            self.log("CRITICAL: PyMeasure or PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(
            parent,
            text='Live Graphs',
            relief='groove',
            bg=self.CLR_GRAPH_BG,
            fg=self.CLR_BG_DARK,
            font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        # Use a standard tk.Frame and set its background to match the graph
        # to make the checkbox appear integrated with the graph area.
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        self.log_scale_cb = ttk.Checkbutton(
            top_bar,
            text="Logarithmic Resistance Axis",
            variable=self.log_scale_var,
            command=self._update_y_scale)
        self.log_scale_cb.pack(side='right', padx=5)
        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(
            gs[1, 0], sharex=self.ax_main)  # Share X-axis with main plot
        self.ax_sub2 = self.figure.add_subplot(
            gs[1, 1])  # Temp vs Time has its own X-axis
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_ylabel("Resistance (Ω)")
        if self.log_scale_var.get():
            self.ax_main.set_yscale('log')
        else:
            self.ax_main.set_yscale('linear')
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)
        self.line_sub1, = self.ax_sub1.plot(
            [], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Current (A)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)
        self.line_sub2, = self.ax_sub2.plot(
            [], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=3, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _update_y_scale(self):
        self.ax_main.set_yscale('log' if self.log_scale_var.get() else 'linear')
        self._plot_dirty = True       # force a rescale on next refresh
        self.canvas.draw_idle()

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'source_voltage': float(self.entries["Source Voltage"].get()),
                'delay': float(self.entries["Delay"].get()),
                'cryocon_visa': self.cryocon_cb.get(),
                'keithley_visa': self.keithley_cb.get()
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError(
                    "All fields, VISA addresses, and save location are required.")

            self.backend.initialize_instruments(params)
            self.log(
                f"Backend initialized for sample: {params['sample_name']}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_RT_passive.dat"
            self.data_filepath = os.path.join(
                self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(
                    [f"# Sample: {params['sample_name']}", f"Source V: {params['source_voltage']}V"])
                writer.writerow(["Timestamp",
                                 "Elapsed Time (s)",
                                 "Temperature (K)",
                                 "Heater Output (%)",
                                 "Applied Voltage (V)",
                                 "Measured Current (A)",
                                 "Resistance (Ohm)"])

            self.log(
                f"Output file created: {os.path.basename(self.data_filepath)}")

            # --- START LOGGING DIRECTLY ---
            self.is_running = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]:
                line.set_data([], [])
            self.ax_main.set_title(
                f"R-T Curve: {params['sample_name']}",
                fontweight='bold')

            self.canvas.draw_idle()

            self.log("Starting passive data logging...")
            self.start_time = time.time()

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)
            self.root.after(250, self._refresh_plot)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start measurement.\n{e}")

    def stop_measurement(self, from_user=True):
        if self.is_running:
            self.is_running = False
            self.log("Measurement stopped by user.")
            self.canvas.draw_idle()
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            if from_user:
                messagebox.showinfo(
                    "Info", "Measurement stopped and instruments disconnected.")

    def _measurement_worker(self):
        """Worker thread to perform measurements and put data into a queue."""
        while self.is_running:
            try:
                temp, htr, cur, res = self.backend.get_measurement()
                elapsed = time.time() - self.start_time
                self.data_queue.put((temp, htr, cur, res, elapsed))
            except Exception as e:
                # Ship the worker's own traceback. Calling format_exc() on
                # the GUI thread renders 'NoneType: None', because no
                # exception is live there -- that is what hid the original
                # fault on 28 Aug 2026.
                self.data_queue.put((e, traceback.format_exc()))
                break

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                # The worker sends (exception, formatted traceback).
                if isinstance(data, tuple) and data and isinstance(
                        data[0], Exception):
                    exc, tb_text = data[0], data[1]
                    self.log(f"RUNTIME ERROR: {exc}")
                    self.log(tb_text)
                    self.stop_measurement(False)
                    messagebox.showerror(
                        "Runtime Error", f"A critical error occurred: {exc}")
                    return
                if isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR: {data}")
                    self.stop_measurement(False)
                    messagebox.showerror(
                        "Runtime Error", f"A critical error occurred: {data}")
                    return

                temp, htr, cur, res, elapsed = data
                self.log(f"T:{temp:.3f}K | R:{res:.3e}Ω | I:{cur:.3e}A")
                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            f"{elapsed:.2f}",
                            f"{temp:.4f}",
                            f"{htr:.2f}",
                            f"{self.backend.params['source_voltage']:.4e}",
                            f"{cur:.4e}",
                            f"{res:.4e}"])

                self.data_storage['time'].append(elapsed)
                self.data_storage['temperature'].append(temp)
                self.data_storage['current'].append(cur)
                self.data_storage['resistance'].append(res)
                # Mark that the plot needs a refresh; actual redraw is
                # decoupled and throttled (see _refresh_plot).
                self._plot_dirty = True

        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _refresh_plot(self):
        """Redraws the plots at a fixed cadence, independent of data rate.

        A normal (non-blitted) draw is used so that the axes — ticks,
        limits, gridlines and scale — always stay in sync with the data.
        """
        if self._plot_dirty:
            self._plot_dirty = False

            temps = self.data_storage['temperature']
            res = self.data_storage['resistance']
            cur = self.data_storage['current']
            t = self.data_storage['time']

            self.line_main.set_data(temps, res)
            self.line_sub1.set_data(temps, cur)
            self.line_sub2.set_data(t, temps)

            # Recompute and apply limits on every axis.
            self._autoscale_axis(self.ax_main, x=temps, y=res,
                                 log_y=self.log_scale_var.get())
            self._autoscale_axis(self.ax_sub1, x=temps, y=cur)
            self._autoscale_axis(self.ax_sub2, x=t, y=temps)

            # Full redraw keeps ticks/labels/gridlines correct and is
            # resize-proof. draw_idle() coalesces redraws efficiently.
            self.canvas.draw_idle()

        if self.is_running:
            self.root.after(250, self._refresh_plot)

    def _autoscale_axis(self, ax, x, y, log_y=False, margin=0.05):
        """Rescale an axis, ignoring non-finite and (for log) non-positive
        values so the axis never collapses or freezes."""
        import math

        xs = [v for v in x if v is not None and math.isfinite(v)]
        if log_y:
            ys = [v for v in y if v is not None and math.isfinite(v) and v > 0]
        else:
            ys = [v for v in y if v is not None and math.isfinite(v)]

        if not xs or not ys:
            return

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)

        # X padding (linear)
        xpad = (xmax - xmin) * margin or 0.5
        ax.set_xlim(xmin - xpad, xmax + xpad)

        # Y padding
        if log_y:
            # pad multiplicatively in log space
            ax.set_ylim(ymin / (1 + margin), ymax * (1 + margin))
        else:
            ypad = (ymax - ymin) * margin or abs(ymax) * margin or 1e-12
            ax.set_ylim(ymin - ypad, ymax + ypad)

    def _scan_for_visa_instruments(self):
        if not pyvisa:
            self.log("ERROR: PyVISA is not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.cryocon_cb['values'] = resources
                self.keithley_cb['values'] = resources

                # Pick the Cryocon by what it says it is, not by where it
                # sits. It moved to GPIB0::12 and the Lakeshore 350 now
                # answers on GPIB1::12, which is the Cryocon's own factory
                # address -- selecting by address would log the wrong
                # instrument's temperature against every resistance point.
                identities = identify_resources(rm, resources)
                for res in resources:
                    self.log(f"  {res}  ->  {identities.get(res, 'no reply')}")

                cryocon = next(
                    (r for r in resources
                     if is_cryocon_idn(identities.get(r, ''))), None)
                if cryocon:
                    self.cryocon_cb.set(cryocon)
                    self.log(f"Cryocon identified at {cryocon} and selected.")
                else:
                    hint = next(
                        (r for r in resources
                         if CRYOCON_ADDRESS_HINT in r), None)
                    if hint:
                        self.cryocon_cb.set(hint)
                        self.log(
                            f"WARNING: no Cryo-con answered *IDN?. Selected "
                            f"{hint} on the factory address alone -- check "
                            f"the instrument is powered and in remote.")
                    else:
                        self.log(
                            "WARNING: no Cryo-con found on the bus. Pick an "
                            "address manually if you know it.")

                keithley = next(
                    (r for r in resources
                     if '6517' in str(identities.get(r, ''))), None)
                if keithley:
                    self.keithley_cb.set(keithley)
                    self.log(f"Keithley 6517B identified at {keithley}.")
                else:
                    for res in resources:
                        if "GPIB1::27" in res:
                            self.keithley_cb.set(res)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit",
                                   "Measurement running. Stop and exit?"):
                self.stop_measurement(from_user=False)
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    Integrated_RT_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
