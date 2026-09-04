"""
Module: RT_K6517B_L340_T_Control_GUI.py
Purpose: High-resistance R vs T (Keithley 6517B) with the temperature ramp
         driven by a Lake Shore Model 340 (heater range 5).
         Port of RT_K6517B_L350_T_Control_GUI.py to the Model 340 command set.

What is different from the Model 350 version, and why
-----------------------------------------------------
  * No *RST at connect.  On a 340 "*RST sets controller parameters to
    power-up settings" (340 manual, printed 9-24): it disables the control
    loop and resets setpoint and ramp.  Only *CLS is sent.
  * Control Loop 1 is enabled explicitly with "CSET 1,<input>,1,1" and
    verified with "CSET? 1" (printed 9-31).  A 340 loop is DISABLED from
    the factory and the heater stays off until CSET turns it on.  The loop
    is put in Manual PID mode with "CMODE 1,1" and verified (printed 9-29).
  * "CLIMIT? 1" is read at start (printed 9-28): setpoint limit, slope
    limits, max current code and max heater range are logged.  An end
    temperature above the setpoint limit is refused, and so is the module's
    fixed heater range 5 when the CLIMIT max range is lower.  The 350's
    HTRSET (25 ohm / 1 A) does not exist on a 340 and is not sent.
  * RANGE takes no output number: "RANGE 5" / "RANGE 0", read back with
    "RANGE?" and verified after every write (printed 9-40 / 9-41).  The 350
    form "RANGE 1,5" is a syntax error on a 340.
  * HTR? takes no argument and reports Loop 1 in percent (printed 9-33).
  * The ramp is pinned: "RAMP 1,0,0"; "SETP 1,<T now>"; "RAMP 1,1,<rate>";
    "SETP 1,<target>".  A 340 ramps from the CURRENT SETPOINT, not from the
    temperature.  Heater range 5 is set BEFORE the ramp is enabled.  The
    ramp rate is validated to 0.1-100 K/min.
  * HTRST? (heater error, 0 = ok, 5 = open load, 6 = load < 10 ohm) is read
    with every sample and logged once whenever it changes: beep + console,
    no dialog.
  * RDGST? <input> is read with every sample (printed 9-41).  A non-zero
    status (invalid, old, under/over range, units zero/overrange) is logged
    with the reading and such a sample never drives the stabilisation test
    or the end-of-ramp / cutoff test.
  * The control input is selectable (A or B on a base Model 340; C and D
    only with the 3462 option card).  The 350 version fixed it to A.
  * Stop, cutoff, completion and runtime errors no longer open a modal
    dialog: a ramp can run unattended, so they log, beep and retitle the
    plot.  Only a refused Start opens a dialog.
  * The scanner pre-selects the lab's 340 at IEEE address 19 ("::19::")
    instead of the old "15"/"12" hint; the *IDN? reply must contain MODEL340.

Commands used (all verified against the Model 340 User's Manual, Chapter 9):
  *IDN?, *CLS, CSET 1,<in>,1,1 / CSET? 1, CMODE 1,1 / CMODE? 1, CLIMIT? 1,
  RAMP 1,<on>,<rate> (0.1-100 K/min), SETP 1,<K>, RANGE <0-5> / RANGE?,
  HTR?, HTRST?, KRDG? <in>, RDGST? <in>
"""

# -------------------------------------------------------------------------------
# Name:             Integrated R-T Measurement GUI (Aggressive Ramp)
# Purpose:          Provide a GUI for the Lakeshore 340 and Keithley 6517B
#                   using a hardware ramp with a fixed high-power heater setting.
# Author:           Prathamesh Deshmukh
# Created:          26/09/2025
# Version:          V: 3.9 (Performance & UI Update)
# -------------------------------------------------------------------------------


# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
import threading
import queue
import os
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
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
    from pymeasure.instruments.keithley import Keithley6517B
    from pyvisa.errors import VisaIOError
    PYMEASURE_AVAILABLE = True

except ImportError:
    pyvisa = None
    Keithley6517B = None
    VisaIOError = None
    PYMEASURE_AVAILABLE = False

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
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")
# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------


# RDGST? bit weights, Model 340 manual printed 9-41.
RDGST_BITS = (
    (1, "invalid reading"),
    (2, "old reading"),
    (16, "temp underrange"),
    (32, "temp overrange"),
    (64, "units zero"),
    (128, "units overrange"),
)


def describe_reading_status(status):
    """Turn an RDGST? integer into readable text ('' when the reading is good)."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        return f"unparseable status '{status}'"
    if status == 0:
        return ""
    names = [name for bit, name in RDGST_BITS if status & bit]
    if not names:
        names = [f"unknown bit(s) {status}"]
    return ", ".join(names)


# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


class Lakeshore340_Backend:
    """A class to control the Lakeshore Model 340 Temperature Controller."""

    MODEL_TOKENS = ("MODEL340", "MODEL 340")
    # HTRST? codes, 340 manual 11.9.
    HEATER_ERRORS = {
        0: "No error",
        1: "Power supply over voltage",
        2: "Power supply under voltage",
        3: "Output DAC error",
        4: "Current limit DAC error",
        5: "OPEN HEATER LOAD",
        6: "Heater load < 10 ohm",
    }
    # CLIMIT <max current> codes, printed 9-28.
    MAX_CURRENT_CODES = {1: "0.25 A", 2: "0.5 A", 3: "1.0 A", 4: "2.0 A",
                         5: "User (CLIMI)"}
    # Heater range names kept from the 350 version, mapped to RANGE <n>.
    RANGE_MAP = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}

    def __init__(self, visa_address):
        self.instrument = None
        self.idn = ""
        self.control_input = 'A'
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        # The 340 answers with <CR><LF> and EOI by default (IEEE command,
        # printed 9-33); '\n' as read terminator works on the lab's 340.
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.idn = self._query('*IDN?')
        print(f"Lakeshore Connected: {self.idn}")
        if not self.is_model_340():
            try:
                self.instrument.close()
            finally:
                self.instrument = None
            raise RuntimeError(
                f"'{visa_address}' answered '{self.idn}', which is not a "
                "Lake Shore Model 340. Refusing to send 340-only commands "
                "(CSET, RANGE n) to it. Pick the right address.")

    def _write(self, cmd):
        if not self.instrument:
            raise ConnectionError("Lakeshore 340 is not connected.")
        self.instrument.write(cmd)

    def _query(self, cmd):
        if not self.instrument:
            raise ConnectionError("Lakeshore 340 is not connected.")
        return self.instrument.query(cmd).strip()

    def is_model_340(self):
        idn = self.idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def clear_status(self):
        """*CLS only.  No *RST: on a 340 it resets loop, setpoint and ramp
        (printed 9-24)."""
        self._write('*CLS')
        time.sleep(0.2)

    def prepare_loop(self, control_input):
        """CSET 1,<input>,1,1 (verified); CMODE 1,1 (verified); ramp off;
        returns CLIMIT? 1 as a dict."""
        control_input = str(control_input).strip().upper()
        self.control_input = control_input
        self._write(f'CSET 1,{control_input},1,1')
        self._write('CMODE 1,1')
        time.sleep(0.2)
        cset = self.get_control_loop()
        if cset['input'].upper() != control_input or not cset['enabled']:
            raise RuntimeError(
                f"CSET 1,{control_input},1,1 did not stick: CSET? 1 reads "
                f"{cset}. Check the front panel (Remote/Local) and retry.")
        cmode = int(float(self._query('CMODE? 1')))
        if cmode != 1:
            raise RuntimeError(f"CMODE 1,1 did not stick: CMODE? 1 = {cmode}.")
        # Ramp off so the stabilisation setpoint takes effect at once; a
        # leftover ramp would make the 340 crawl from its old setpoint.
        self._write('RAMP 1,0,0')
        return self.get_control_limits()

    def get_control_loop(self):
        parts = [p.strip() for p in self._query('CSET? 1').split(',')]
        if len(parts) < 4:
            raise ValueError(f"unexpected CSET? reply '{','.join(parts)}'")
        return {'input': parts[0], 'units': int(float(parts[1])),
                'enabled': int(float(parts[2])), 'powerup': int(float(parts[3]))}

    def get_control_limits(self):
        parts = [p.strip() for p in self._query('CLIMIT? 1').split(',')]
        if len(parts) < 5:
            raise ValueError(f"unexpected CLIMIT? reply '{','.join(parts)}'")
        return {'sp_limit': float(parts[0]), 'pos_slope': float(parts[1]),
                'neg_slope': float(parts[2]), 'max_current': int(float(parts[3])),
                'max_range': int(float(parts[4]))}

    def setup_ramp(self, rate_k_per_min, ramp_on=True):
        """ Configures the instrument's internal ramp generator (Loop 1). """
        if ramp_on and not (0.1 <= rate_k_per_min <= 100):
            raise ValueError(
                f"Ramp rate must be 0.1-100 K/min on a Model 340, "
                f"got {rate_k_per_min}")
        self._write(
            f'RAMP 1,{1 if ramp_on else 0},{rate_k_per_min if ramp_on else 0}')
        time.sleep(0.5)

    def start_ramp(self, target_k, rate_k_per_min, current_temperature):
        """Setpoint = now (ramp off), then ramp on and target.

        The 340 ramps from the current setpoint; pinning it to the present
        temperature first makes the ramp start from where the sample is.
        The heater range must already be set (RANGE before RAMP).
        """
        if not (0.1 <= rate_k_per_min <= 100):
            raise ValueError(
                f"Ramp rate must be 0.1-100 K/min on a Model 340, "
                f"got {rate_k_per_min}")
        self._write('RAMP 1,0,0')
        self._write(f'SETP 1,{current_temperature:.3f}')
        time.sleep(0.2)
        self._write(f'RAMP 1,1,{rate_k_per_min}')
        self._write(f'SETP 1,{target_k}')

    def stop_ramp(self):
        """RAMP off and heater off (RANGE 0). Loop stays enabled."""
        self._write('RAMP 1,0,0')
        self._write('RANGE 0')

    def set_setpoint(self, temperature_k):
        self._write(f'SETP 1,{temperature_k}')

    def set_heater_range(self, heater_range):
        """RANGE <0-5>: Loop 1 only, no output number on a 340. Verified.

        Accepts the 350-era names (off/low/medium/high -> 0/2/4/5) or a
        numeric code 0-5.
        """
        if isinstance(heater_range, str) and not heater_range.strip().isdigit():
            range_code = self.RANGE_MAP.get(heater_range.strip().lower())
            if range_code is None:
                raise ValueError("Invalid heater range.")
        else:
            range_code = int(heater_range)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5, got {range_code}")
        self._write(f'RANGE {range_code}')
        time.sleep(0.1)
        back = self.get_heater_range()
        if back != range_code:
            raise RuntimeError(
                f"RANGE {range_code} did not stick: RANGE? = {back}. The CLIMIT "
                "max range may be lower, or the loop is disabled.")

    def get_heater_range(self):
        """RANGE? -> 0 (off) .. 5.  No output argument on a 340."""
        return int(float(self._query('RANGE?')))

    def get_temperature(self, sensor=None):
        sensor = sensor or self.control_input
        return float(self._query(f'KRDG? {sensor}'))

    def get_reading_status(self, sensor=None):
        """RDGST? <input> -> bit-weighted status (0 = good)."""
        sensor = sensor or self.control_input
        return int(float(self._query(f'RDGST? {sensor}')))

    def get_heater_output(self):
        """HTR? -> Loop 1 heater output in percent.  No argument on a 340."""
        return float(self._query('HTR?'))

    def get_heater_status(self):
        """HTRST? -> (code, text); 0 = no error."""
        code = int(float(self._query('HTRST?')))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    def close(self):
        if self.instrument:
            try:
                self.stop_ramp()
                time.sleep(0.5)
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None


class Combined_Backend:
    """Manages both the Lakeshore 340 and Keithley 6517B."""

    RAMP_RANGE = 5  # the module's fixed heater range for the ramp

    def __init__(self):
        self.lakeshore = None
        self.keithley = None
        self.params = {}
        self.limits = {}

    def initialize_instruments(self, parameters):
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments ---")
        self.lakeshore = Lakeshore340_Backend(self.params['lakeshore_visa'])
        self.lakeshore.clear_status()
        self.limits = self.lakeshore.prepare_loop(self.params.get('input', 'A'))
        cur = Lakeshore340_Backend.MAX_CURRENT_CODES.get(
            self.limits['max_current'], f"code {self.limits['max_current']}")
        print(f"  Loop 1 enabled on input {self.lakeshore.control_input} "
              f"(kelvin), Manual PID. CLIMIT: setpoint <= "
              f"{self.limits['sp_limit']:g} K, max current {cur}, "
              f"max range {self.limits['max_range']}.")

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
        """-> (temperature K, heater %, current A, resistance ohm, RDGST? code)."""
        time.sleep(self.params['delay'])
        current_temp = self.lakeshore.get_temperature()
        rd_status = self.lakeshore.get_reading_status()
        heater_output = self.lakeshore.get_heater_output()
        resistance = self.keithley.resistance
        if resistance != 0 and resistance != float(
                'inf') and resistance == resistance:
            current = self.params['source_voltage'] / resistance
        else:
            current = 0.0
        return current_temp, heater_output, current, resistance, rd_status

    def close_instruments(self):
        print("\n--- [Backend] Closing all instrument connections. ---")
        if self.keithley:
            self.keithley.shutdown()
            print("  Keithley connection closed and source OFF.")
        if self.lakeshore:
            self.lakeshore.close()
            print("  Lakeshore connection closed and heater OFF.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class Integrated_RT_GUI:
    PROGRAM_VERSION = "3.9"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500
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
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("K6517B & L340: R-T Measurement (T-Control)")
        self.root.geometry("1550x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        self._last_draw_time = 0.0
        self._redraw_interval = 0.25   # seconds; redraw at most ~4x/sec
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {
            'time': [],
            'temperature': [],
            'current': [],
            'resistance': []}
        self.log_scale_var = tk.BooleanVar(value=True)
        self.current_heater_range = 'off'
        self.last_htr_error = 0
        self.logo_image = None  # Attribute to hold the logo image reference
        self.data_queue = queue.Queue()
        self.measurement_thread = None

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

        # Create the console frame first to initialize the logger, but don't pack it yet.
        console_pane = self.create_console_frame(scrollable_frame)

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)

        # Pack the console last so it appears at the bottom of the scroll area.
        console_pane.pack(pady=5, padx=10, fill='x')

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
        Label(
            header_frame,
            text="K6517B & L340: R-T Measurement (T-Control)",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(
            side='left',
            padx=20,
            pady=10)

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
        # --- MODIFIED: Use grid layout with 2 columns ---
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

        # --- MODIFIED: Use a separator instead of a new frame ---
        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)

        # Program details
        details_text = ("Program Name: R vs. T (T-Control) - range 5\n"
                        "Instruments: Lakeshore 340, Keithley 6517B\n"
                        "Measurement Range: <10 Ω to >10 PΩ\n"
                        "Lakeshore inputs: A, B (C, D with 3462 option card)")
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
            text="Start Temp (K):").grid(
            row=2,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Start Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Start Temp"].grid(
            row=3, column=0, padx=(
                10, 5), pady=(
                0, 5), sticky='ew')
        Label(
            frame,
            text="End Temp (K):").grid(
            row=2,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["End Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["End Temp"].grid(
            row=3, column=1, padx=(
                5, 10), pady=(
                0, 5), sticky='ew')
        Label(frame, text="Ramp Rate (K/min):").grid(row=4,
                                                     column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Rate"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Rate"].grid(
            row=5, column=0, padx=(
                10, 5), pady=(
                0, 10), sticky='ew')
        Label(
            frame,
            text="Safety Cutoff (K):").grid(
            row=4,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Cutoff"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Cutoff"].grid(
            row=5, column=1, padx=(
                5, 10), pady=(
                0, 10), sticky='ew')
        Label(
            frame,
            text="Source Voltage (V):").grid(
            row=6,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Source Voltage"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Source Voltage"].grid(
            row=7, column=0, padx=(
                10, 5), pady=(
                0, 5), sticky='ew')
        Label(
            frame,
            text="Settling Delay (s):").grid(
            row=6,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].grid(
            row=7, column=1, padx=(
                5, 10), pady=(
                0, 5), sticky='ew')
        self.entries["Delay"].insert(0, "0.5")
        Label(
            frame,
            text="Lakeshore VISA:").grid(
            row=8,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.lakeshore_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(
            row=9, column=0, padx=(
                10, 5), pady=(
                0, 10), sticky='ew')
        Label(
            frame,
            text="Keithley VISA:").grid(
            row=8,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.keithley_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(
            row=9, column=1, padx=(
                5, 10), pady=(
                0, 10), sticky='ew')
        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=10,
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
            row=11,
            column=0,
            columnspan=2,
            padx=10,
            pady=4,
            sticky='ew')
        # Lake Shore 340 control input (A/B; C/D with the 3462 card).
        Label(
            frame,
            text="Lakeshore Input:").grid(
            row=12,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.input_var = tk.StringVar(master=self.root, value='A')
        self.input_cb = ttk.Combobox(
            frame, textvariable=self.input_var, font=self.FONT_BASE,
            values=['A', 'B', 'C', 'D'], state='readonly', width=4)
        self.input_cb.grid(
            row=12, column=1, padx=(5, 10), pady=pady_val, sticky='w')
        self.start_button = ttk.Button(
            frame,
            text="Start Measurement",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=13, column=0, padx=10, pady=(
                10, 10), sticky='ew')
        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=13, column=1, padx=10, pady=(
                10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Console Output',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
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
                             facecolor=self.CLR_GRAPH_BG,
                             layout='constrained')
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])
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
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _update_y_scale(self):
        if self.log_scale_var.get():
            self.ax_main.set_yscale('log')
        else:
            self.ax_main.set_yscale('linear')
        self._rescale_main_axis()
        self.canvas.draw_idle()

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _handle_log_message(self, message):
        self.log(message)

    def _beep(self):
        try:
            self.root.bell()
        except Exception:
            pass

    # Unattended-safe: cutoff, completion and runtime errors log, beep and
    # retitle the plot.  No modal dialog during or after a run.
    def _handle_cutoff_event(self):
        self.log("!!! SAFETY CUTOFF REACHED !!! Heater is OFF.")
        self._update_live_plots(force=True)
        self.stop_measurement(False)
        self.ax_main.set_title("SAFETY CUTOFF reached, heater off",
                               fontweight='bold')
        self.canvas.draw_idle()
        self._beep()

    def _handle_complete_event(self):
        self.log("Target temperature reached. Measurement complete. Heater is OFF.")
        self._update_live_plots(force=True)
        self.stop_measurement(False)
        self.ax_main.set_title("Measurement complete, heater off",
                               fontweight='bold')
        self.canvas.draw_idle()
        self._beep()

    def _handle_runtime_error(self, exception):
        self.log(f"RUNTIME ERROR: {exception}")
        self.stop_measurement(False)
        self.ax_main.set_title("ERROR: measurement stopped, heater off (see console)",
                               fontweight='bold')
        self.canvas.draw_idle()
        self._beep()

    def _process_measurement_data_point(self, data):
        temp, htr, cur, res, elapsed, rd_status = data
        self._log_measurement_data(temp, htr, cur, res, rd_status)
        self._save_measurement_to_csv(temp, htr, cur, res, elapsed)
        self._update_data_storage(temp, htr, cur, res, elapsed)
        self._update_live_plots()

    def _log_measurement_data(self, temp, htr, cur, res, rd_status=0):
        line = (f"T:{temp:.3f}K | R:{res:.3e}Ω | Htr:{htr:.1f}% "
                f"({self.current_heater_range})")
        rd_text = describe_reading_status(rd_status)
        if rd_text:
            line += f" | RDGST {self.backend.lakeshore.control_input}: {rd_text}"
        self.log(line)

    def _save_measurement_to_csv(self, temp, htr, cur, res, elapsed):
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

    def _update_data_storage(self, temp, htr, cur, res, elapsed):
        self.data_storage['time'].append(elapsed)
        self.data_storage['temperature'].append(temp)
        self.data_storage['current'].append(cur)
        self.data_storage['resistance'].append(res)

    def _update_live_plots(self, force=False):
        # Always update the underlying data (cheap)
        temps = self.data_storage['temperature']
        res = self.data_storage['resistance']
        cur = self.data_storage['current']
        t = self.data_storage['time']

        self.line_main.set_data(temps, res)
        self.line_sub1.set_data(temps, cur)
        self.line_sub2.set_data(t, temps)

        # Throttle the expensive redraw
        now = time.time()
        if not force and (now - self._last_draw_time) < self._redraw_interval:
            return
        self._last_draw_time = now

        # Rescale all axes to current data
        self._rescale_main_axis()
        for ax in (self.ax_sub1, self.ax_sub2):
            ax.relim()
            ax.autoscale_view()

        self.canvas.draw_idle()

    def _rescale_main_axis(self):
        """Autoscale the main axis, safely handling a log y-scale."""
        res = self.data_storage['resistance']
        temps = self.data_storage['temperature']
        if not res:
            return

        if self.log_scale_var.get():
            # Log axis: ignore non-positive / non-finite values for limits
            valid = [r for r in res if r > 0 and r == r and r != float('inf')]
            if valid:
                lo, hi = min(valid), max(valid)
                # pad by one decade-ish so points aren't on the frame
                self.ax_main.set_ylim(lo * 0.5, hi * 2.0)
        else:
            self.ax_main.relim()
            self.ax_main.autoscale_view(scaley=True)

        # X (temperature) autoscale is always linear-safe
        if temps:
            xlo, xhi = min(temps) , max(temps)
            if xhi > xlo:
                pad = (xhi - xlo) * 0.05
                self.ax_main.set_xlim(xlo - pad, xhi + pad)

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'start_temp': float(self.entries["Start Temp"].get()),
                'end_temp': float(self.entries["End Temp"].get()),
                'rate': float(self.entries["Rate"].get()),
                'cutoff': float(self.entries["Cutoff"].get()),
                'source_voltage': float(self.entries["Source Voltage"].get()),
                'delay': float(self.entries["Delay"].get()),
                'lakeshore_visa': self.lakeshore_cb.get(),
                'keithley_visa': self.keithley_cb.get(),
                'input': self.input_var.get()
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError(
                    "All fields, VISA addresses, and save location are required.")
            if not (params['start_temp'] <
                    params['end_temp'] < params['cutoff']):
                raise ValueError(
                    "Temperatures must be in order: start < end < cutoff.")
            if not (0.1 <= params['rate'] <= 100):
                raise ValueError(
                    "Ramp rate must be 0.1-100 K/min on a Model 340.")

            self.backend.initialize_instruments(params)
            limits = self.backend.limits
            ls = self.backend.lakeshore
            cur = ls.MAX_CURRENT_CODES.get(
                limits['max_current'], f"code {limits['max_current']}")
            self.log(f"Lakeshore: {ls.idn}")
            self.log(f"Loop 1 enabled on input {ls.control_input} (kelvin), "
                     f"Manual PID. CLIMIT: setpoint <= {limits['sp_limit']:g} K, "
                     f"max current {cur}, max range {limits['max_range']}.")
            try:
                if params['end_temp'] > limits['sp_limit']:
                    raise ValueError(
                        f"End temperature {params['end_temp']:g} K is above the "
                        f"340's setpoint limit of {limits['sp_limit']:g} K "
                        "(CLIMIT). Raise the limit in the Direct Control "
                        "module first.")
                if self.backend.RAMP_RANGE > limits['max_range']:
                    raise ValueError(
                        f"This module ramps on heater range "
                        f"{self.backend.RAMP_RANGE}, but the 340's max range is "
                        f"{limits['max_range']} (CLIMIT). Raise the limit first.")
                code, text = ls.get_heater_status()
                if code != 0:
                    raise RuntimeError(
                        f"Heater error HTRST? {code:02d}: {text}. Fix the "
                        "heater circuit before ramping.")
            except Exception:
                self.backend.close_instruments()
                raise
            self.last_htr_error = 0
            self.log(
                f"Backend initialized for sample: {params['sample_name']}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_RT.dat"
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
            self.is_stabilizing, self.is_running = True, False
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]:
                line.set_data([], [])
            self.ax_main.set_title(
                f"R-T Curve: {params['sample_name']}",
                fontweight='bold')
            self.canvas.draw()
            self.log("Starting stabilization process...")

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start measurement.\n{e}")

    def stop_measurement(self, from_user=True):
        if self.is_running or self.is_stabilizing:
            self.is_running, self.is_stabilizing = False, False
            self.log("Measurement stopped by user.")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            # This backend call will automatically turn the heater off
            # (RAMP 1,0,0 + RANGE 0).
            self.backend.close_instruments()
            if from_user:
                # Unattended-safe: no dialog; console line + beep instead.
                self.log("Measurement stopped and instruments disconnected. "
                         "Heater is OFF.")
                self._beep()

    def _check_heater_status(self):
        """HTRST? from the worker; log once on change, beep on error."""
        code, text = self.backend.lakeshore.get_heater_status()
        if code != self.last_htr_error:
            self.last_htr_error = code
            if code != 0:
                self.data_queue.put(f"LOG:HEATER ERROR HTRST? {code:02d}: {text}")
                self.data_queue.put("BEEP")
            else:
                self.data_queue.put("LOG:Heater error cleared (HTRST? 00).")

    def _measurement_worker(self):
        """Worker thread to handle stabilization and measurement loop."""
        params = self.backend.params
        ls = self.backend.lakeshore
        try:
            # --- Stabilization Phase ---
            while self.is_stabilizing:
                current_temp = ls.get_temperature()
                rd_text = describe_reading_status(ls.get_reading_status())
                self._check_heater_status()
                if rd_text:
                    # Not a valid sample: do not act on it, poll again.
                    self.data_queue.put(
                        f"LOG:RDGST {ls.control_input}: {rd_text} "
                        f"(reading {current_temp:.4f} K ignored)")
                    time.sleep(2)
                    continue
                self.data_queue.put(
                    f"LOG:Stabilizing... Current: {current_temp:.4f} K (Target: {params['start_temp']} K)")

                if current_temp > params['start_temp'] + 5.0:
                    ls.set_heater_range('off')
                else:
                    ls.set_heater_range('high')
                    ls.set_setpoint(params['start_temp'])

                if abs(current_temp - params['start_temp']) < 5.0:
                    self.data_queue.put(
                        f"LOG:Stabilized at {current_temp:.4f} K. Waiting 5s before ramp...")
                    time.sleep(5)
                    self.is_stabilizing = False
                    self.is_running = True
                    break
                time.sleep(2)

            # --- Ramp Phase ---
            if self.is_running:
                # RANGE before RAMP, then pin the setpoint to the present
                # temperature so the 340 ramps from where the sample is.
                self.current_heater_range = 'high'
                ls.set_heater_range(self.backend.RAMP_RANGE)
                temp_now = ls.get_temperature()
                ls.start_ramp(params['end_temp'], params['rate'], temp_now)
                self.data_queue.put(
                    f"LOG:RANGE {self.backend.RAMP_RANGE} set; setpoint pinned to "
                    f"{temp_now:.3f} K; hardware ramp started towards "
                    f"{params['end_temp']} K at {params['rate']} K/min.")
                self.start_time = time.time()
            while self.is_running:
                temp, htr, cur, res, rd_status = self.backend.get_measurement()
                elapsed = time.time() - self.start_time
                self.data_queue.put((temp, htr, cur, res, elapsed, rd_status))
                self._check_heater_status()

                if rd_status != 0:
                    # Invalid / over-range reading never ends the ramp.
                    continue
                if temp >= params['cutoff']:
                    self.data_queue.put("CUTOFF")
                    break
                elif temp >= params['end_temp']:
                    self.data_queue.put("COMPLETE")
                    break
        except Exception as e:
            self.data_queue.put(e)

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if isinstance(data, str) and data.startswith("LOG:"):
                    self._handle_log_message(data[4:])
                elif isinstance(data, str) and data == "BEEP":
                    self._beep()
                elif isinstance(data, str) and data == "CUTOFF":
                    self._handle_cutoff_event()
                    return
                elif isinstance(data, str) and data == "COMPLETE":
                    self._handle_complete_event()
                    return
                elif isinstance(data, Exception):
                    self._handle_runtime_error(data)
                    return
                else:
                    self._process_measurement_data_point(data)
        except queue.Empty:
            pass

        if self.is_running or self.is_stabilizing:
            self.root.after(200, self._process_data_queue)

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
                self.lakeshore_cb['values'] = resources
                self.keithley_cb['values'] = resources
                for res in resources:
                    # Lab 340 sits at IEEE address 19; a hint only, the
                    # IDN check at Start decides.
                    if LAKESHORE340_ADDRESS_HINT in res:
                        self.lakeshore_cb.set(res)
                    if "27" in res or "26" in res:
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
        if self.is_running or self.is_stabilizing:
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