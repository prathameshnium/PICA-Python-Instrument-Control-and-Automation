"""
Module: Pyroelectric_K6517B_L340_GUI.py
Purpose: Pyroelectric current vs T (Keithley 6517B) with the temperature
         ramp driven by a Lake Shore Model 340 (heater range 5).
         Port of Pyroelectric_K6517B_L350_GUI.py to the Model 340 command set.

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
    instead of the old GPIB1::15 hint; the *IDN? reply must contain MODEL340.

Commands used (all verified against the Model 340 User's Manual, Chapter 9):
  *IDN?, *CLS, CSET 1,<in>,1,1 / CSET? 1, CMODE 1,1 / CMODE? 1, CLIMIT? 1,
  RAMP 1,<on>,<rate> (0.1-100 K/min), SETP 1,<K>, RANGE <0-5> / RANGE?,
  HTRST?, KRDG? <in>, RDGST? <in>
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import threading
import queue
import matplotlib.pyplot as plt
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
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False

import runpy


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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    plotter_path = os.path.join(
        script_dir,
        "..", "..", "..", "utils", "PlotterUtil_GUI.py")

    if not os.path.exists(plotter_path):
        messagebox.showerror(
            "File Not Found",
            f"Plotter utility not found at expected path:\n{plotter_path}")
        return
    Process(target=run_script_process, args=(plotter_path,)).start()


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    scanner_path = os.path.join(
        script_dir,
        "..", "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")

    if not os.path.exists(scanner_path):
        messagebox.showerror(
            "File Not Found",
            f"GPIB Scanner not found at expected path:\n{scanner_path}")
        return
    Process(target=run_script_process, args=(scanner_path,)).start()


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


class PyroelectricBackend:
    """
    Handles all backend instrument communication.
    Integrates advanced ramp control with pyroelectric current measurement.
    """

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
    RAMP_RANGE = 5  # 'High' (5): the module's fixed heater range

    def __init__(self):
        self.params = {}
        self.keithley = None
        self.lakeshore = None
        self.lakeshore_idn = ""
        self.control_input = 'A'
        self.limits = {}
        if PYVISA_AVAILABLE:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(
                    f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def initialize_instruments(self, parameters):
        """Connects, resets, and performs initial configuration of
        instruments."""
        print("\n--- [Backend] Initializing Instruments ---")
        self.params = parameters
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        try:
            # --- Connect and Configure Lakeshore 340 ---
            print(
                f"  Connecting to Lakeshore 340 via "
                f"{self.params['lakeshore_visa']}...")
            self.lakeshore = self.rm.open_resource(
                self.params['lakeshore_visa'])
            self.lakeshore.timeout = 15000
            # The 340 answers with <CR><LF> and EOI by default (IEEE
            # command, printed 9-33); '\n' as read terminator works on
            # the lab's 340.
            self.lakeshore.read_termination = '\n'
            self.lakeshore.write_termination = '\n'
            self.lakeshore_idn = self._ls_query('*IDN?')
            print(f"    Connected to: {self.lakeshore_idn}")
            if not self.is_model_340():
                raise RuntimeError(
                    f"'{self.params['lakeshore_visa']}' answered "
                    f"'{self.lakeshore_idn}', which is not a Lake Shore "
                    "Model 340. Refusing to send 340-only commands (CSET, "
                    "RANGE n) to it. Pick the right address.")

            # No *RST: on a 340 it resets loop, setpoint and ramp
            # (printed 9-24).  *CLS only.
            self._ls_write('*CLS')
            time.sleep(0.2)

            # Enable Loop 1 (a 340 loop is disabled from the factory),
            # Manual PID, ramp off, and read the CLIMIT? limits.
            self.limits = self.prepare_loop(self.params.get('input', 'A'))
            cur = self.MAX_CURRENT_CODES.get(
                self.limits['max_current'],
                f"code {self.limits['max_current']}")
            print(f"  Loop 1 enabled on input {self.control_input} "
                  f"(kelvin), Manual PID. CLIMIT: setpoint <= "
                  f"{self.limits['sp_limit']:g} K, max current {cur}, "
                  f"max range {self.limits['max_range']}.")

            # --- Connect and Configure Keithley 6517B ---
            print(
                f"  Connecting to Keithley 6517B via "
                f"{self.params['keithley_visa']}...")
            self.keithley = Keithley6517B(self.params['keithley_visa'])
            time.sleep(1)
            print(f"    Connected to: {self.keithley.id}")

            # Configure for current measurement
            self.keithley.measure_current()

            # --- Disable Zero Check (CRITICAL) ---
            # After *RST / power-up the 6517B input is internally
            # disconnected (Zero Check ON), which returns the ~9.9e37
            # overflow value.
            # SYST:ZCH OFF reconnects the input so real current is read.
            self.keithley.write("SYST:ZCH OFF")
            time.sleep(0.5)

            # Enable autorange so a too-low fixed range does not also
            # trigger overflow.
            self.keithley.write("CURR:RANG:AUTO ON")
            time.sleep(0.5)

            # --- Perform zero correction (best practice) ---
            # 1. Re-enable zero check to acquire the zero reference.
            self.keithley.write("SYST:ZCH ON")
            time.sleep(0.5)
            # 2. Acquire the zero-correction value.
            self.keithley.write("SYST:ZCOR:ACQ")
            time.sleep(0.5)
            # 3. Disable zero check so real current can be measured.
            self.keithley.write("SYST:ZCH OFF")
            time.sleep(0.5)
            # 4. Enable zero correction on the measurement.
            self.keithley.write("SYST:ZCOR ON")
            time.sleep(0.5)

            print(
                "  Keithley 6517B configured: current mode, Zero Check OFF, "
                "autorange ON, zero correction applied.")
            print(
                "--- [Backend] Instrument Initialization Complete ---")

        except Exception as e:
            print(
                f"  ERROR: Could not connect/configure an instrument. {e}")
            self.close_instruments()
            raise e

    # --- Lake Shore 340 helpers ---

    def _ls_write(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Lakeshore 340 is not connected.")
        self.lakeshore.write(cmd)

    def _ls_query(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Lakeshore 340 is not connected.")
        return self.lakeshore.query(cmd).strip()

    def is_model_340(self):
        idn = self.lakeshore_idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def prepare_loop(self, control_input):
        """CSET 1,<input>,1,1 (verified); CMODE 1,1 (verified); ramp off;
        returns CLIMIT? 1 as a dict."""
        control_input = str(control_input).strip().upper() or 'A'
        self.control_input = control_input
        self._ls_write(f'CSET 1,{control_input},1,1')
        self._ls_write('CMODE 1,1')
        time.sleep(0.2)
        cset = self.get_control_loop()
        if cset['input'].upper() != control_input or not cset['enabled']:
            raise RuntimeError(
                f"CSET 1,{control_input},1,1 did not stick: CSET? 1 reads "
                f"{cset}. Check the front panel (Remote/Local) and retry.")
        cmode = int(float(self._ls_query('CMODE? 1')))
        if cmode != 1:
            raise RuntimeError(f"CMODE 1,1 did not stick: CMODE? 1 = {cmode}.")
        # Ramp off so the stabilisation setpoint takes effect at once; a
        # leftover ramp would make the 340 crawl from its old setpoint.
        self._ls_write('RAMP 1,0,0')
        return self.get_control_limits()

    def get_control_loop(self):
        parts = [p.strip() for p in self._ls_query('CSET? 1').split(',')]
        if len(parts) < 4:
            raise ValueError(f"unexpected CSET? reply '{','.join(parts)}'")
        return {'input': parts[0], 'units': int(float(parts[1])),
                'enabled': int(float(parts[2])), 'powerup': int(float(parts[3]))}

    def get_control_limits(self):
        parts = [p.strip() for p in self._ls_query('CLIMIT? 1').split(',')]
        if len(parts) < 5:
            raise ValueError(f"unexpected CLIMIT? reply '{','.join(parts)}'")
        return {'sp_limit': float(parts[0]), 'pos_slope': float(parts[1]),
                'neg_slope': float(parts[2]), 'max_current': int(float(parts[3])),
                'max_range': int(float(parts[4]))}

    def set_heater_range(self, range_code):
        """RANGE <0-5>: Loop 1 only, no output number on a 340. Verified."""
        range_code = int(range_code)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5, got {range_code}")
        self._ls_write(f'RANGE {range_code}')
        time.sleep(0.1)
        back = self.get_heater_range()
        if back != range_code:
            raise RuntimeError(
                f"RANGE {range_code} did not stick: RANGE? = {back}. The CLIMIT "
                "max range may be lower, or the loop is disabled.")

    def get_heater_range(self):
        """RANGE? -> 0 (off) .. 5.  No output argument on a 340."""
        return int(float(self._ls_query('RANGE?')))

    def get_temperature(self):
        return float(self._ls_query(f'KRDG? {self.control_input}'))

    def get_reading_status(self):
        """RDGST? <input> -> bit-weighted status (0 = good)."""
        return int(float(self._ls_query(f'RDGST? {self.control_input}')))

    def get_heater_status(self):
        """HTRST? -> (code, text); 0 = no error."""
        code = int(float(self._ls_query('HTRST?')))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    def start_stabilization(self):
        """Begins moving to the start temperature for stabilization."""
        print(
            f"  Moving to start temperature: "
            f"{self.params['start_temp']} K")
        # Ramp is off (prepare_loop), so the setpoint jumps at once.
        self._ls_write(f"SETP 1,{self.params['start_temp']}")
        # Use 'high' range (5) for stabilization, verified with RANGE?.
        self.set_heater_range(self.RAMP_RANGE)
        print("  Heater range set to 'High' (5) for stabilization.")

    def start_ramp(self):
        """Configures and starts the temperature ramp.

        RANGE first, then the setpoint is pinned to the present temperature
        with the ramp off, then RAMP 1,1,<rate> and the end setpoint: a 340
        ramps from the CURRENT SETPOINT, not from the temperature.
        """
        rate = float(self.params['rate'])
        if not (0.1 <= rate <= 100):
            raise ValueError(
                f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        temp_now = self.get_temperature()
        print(
            f"  Ramp starting from {temp_now:.3f} K towards "
            f"{self.params['end_temp']} K at {rate} K/min.")
        # Ensure heater range is sufficient for ramp (RANGE before RAMP)
        self.set_heater_range(self.RAMP_RANGE)
        self._ls_write('RAMP 1,0,0')
        self._ls_write(f"SETP 1,{temp_now:.3f}")
        time.sleep(0.2)
        # RAMP <loop>,<on/off>,<rate>
        self._ls_write(f"RAMP 1,1,{rate}")
        self._ls_write(f"SETP 1,{self.params['end_temp']}")
        print("  Ramp configured and setpoint updated.")

    def stop_ramp(self):
        """RAMP off and heater off (RANGE 0). Loop stays enabled."""
        self._ls_write('RAMP 1,0,0')
        self._ls_write('RANGE 0')

    def get_measurement(self):
        """Reads temperature, current, RDGST? and HTRST? from the instruments.

        -> (temperature K, current A, reading-status code, heater-error code,
            heater-error text).  A VISA/parse failure returns NaN readings
            with reading status 1 (invalid) so the sample is never used for
            the stabilisation or end-of-ramp tests.
        """
        if not self.keithley or not self.lakeshore:
            raise ConnectionError(
                "One or more instruments are not connected.")
        try:
            temperature = self.get_temperature()
            rd_status = self.get_reading_status()
            htr_code, htr_text = self.get_heater_status()
            current = self.keithley.current
            return temperature, current, rd_status, htr_code, htr_text
        except (pyvisa.errors.VisaIOError, ValueError):
            return (float('nan'), float('nan'), 1,
                    -1, "no HTRST? reply (VISA error)")

    def close_instruments(self):
        """Safely shuts down and disconnects from all instruments."""
        print("--- [Backend] Closing instrument connections. ---")
        if self.keithley:
            try:
                # Re-enable Zero Check to protect the input before shutdown.
                self.keithley.write("SYST:ZCH ON")
                self.keithley.shutdown()
                print("  Keithley 6517B connection closed.")
            except Exception:
                pass
            finally:
                self.keithley = None
        if self.lakeshore:
            try:
                self.stop_ramp()  # RAMP 1,0,0 + RANGE 0: heater off
                self.lakeshore.close()
                print("  Lakeshore 340 connection closed, heater OFF.")
            except Exception:
                pass
            finally:
                self.lakeshore = None


class PyroelectricAppGUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "3.1"  # Added Zero Check / Zero Correction fix
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

    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    def __init__(self, root):
        self.root = root
        self.root.title("Pyroelectric Measurement Interface")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running, self.start_time = False, None
        self.experiment_state = 'idle'
        self.last_htr_error = 0
        self.backend = PyroelectricBackend()
        self.file_location_path = ""
        self.data_storage = {
            'time': [], 'temperature': [], 'current': []}
        self.data_queue = queue.Queue()
        self.measurement_thread = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles for a modern, beautiful look."""
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
            'TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton',
            background=[
                ('active', self.CLR_ACCENT_GOLD),
                ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[
                ('active', self.CLR_TEXT_DARK),
                ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Start.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton',
            background=[('active', '#8AB845'),
                        ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton',
            background=[('active', '#D63C2A'),
                        ('hover', '#D63C2A')])
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
        """Lays out the main frames and populates them with widgets."""
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=15, pady=15)

        # --- Create a scrollable left panel ---
        # FIX (2b): pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

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
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
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

        # --- Create the right panel for graphs ---
        right_panel = tk.Frame(self.main_pane, bg=self.CLR_BG_DARK)
        self.main_pane.add(right_panel, weight=3)

        # --- Populate the scrollable frame ---
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
            text="Pyroelectric Measurement",
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
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(0, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        LOGO_SIZE = 110
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=15, pady=15)

        # Corrected logo path
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir,
                "..",
                "..",
                "..",
                "assets",
                "LOGO",
                "UGC_DAE_CSR_NBG.jpeg")
        except NameError:
            logo_path = "../../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"
        self.logo_image = self._process_logo_image(
            logo_path, size=LOGO_SIZE)
        if self.logo_image:
            logo_canvas.create_image(
                LOGO_SIZE / 2,
                LOGO_SIZE / 2,
                image=self.logo_image)
        else:
            logo_canvas.create_text(
                LOGO_SIZE / 2,
                LOGO_SIZE / 2,
                text="LOGO",
                font=self.FONT_TITLE,
                fill=self.CLR_FG_LIGHT)

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=0,
            column=1,
            padx=10,
            pady=(10, 0),
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

        details_text = (
            "Program Name: Pyroelectric Current vs. T\n"
            "Instruments: Keithley 6517B, Lakeshore 340\n"
            "Measurement Range: 1 fA to 20 mA\n"
            "Temperature Controller Range: 5 (High)\n"
            "Lakeshore inputs: A, B (C, D with 3462 option card)")
        ttk.Label(
            frame,
            text=details_text,
            justify='left').grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(0, 10),
            sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=10, padx=10, fill='x', expand=False)
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
            row=1, column=0, columnspan=2, padx=10, pady=(0, 10),
            sticky='ew')

        Label(
            frame,
            text="Start Temp (K):").grid(
            row=2,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries['Start Temp'] = Entry(frame, font=self.FONT_BASE)
        self.entries['Start Temp'].grid(
            row=3, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')

        Label(
            frame,
            text="End Temp (K):").grid(
            row=2,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries['End Temp'] = Entry(frame, font=self.FONT_BASE)
        self.entries['End Temp'].grid(
            row=3, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')

        Label(
            frame,
            text="Ramp Rate (K/min):").grid(
            row=4,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries['Ramp Rate'] = Entry(frame, font=self.FONT_BASE)
        self.entries['Ramp Rate'].grid(
            row=5, column=0, padx=(10, 5), pady=(0, 15), sticky='ew')

        Label(
            frame,
            text="Safety Cutoff (K):").grid(
            row=4,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries['Safety Cutoff'] = Entry(frame, font=self.FONT_BASE)
        self.entries['Safety Cutoff'].grid(
            row=5, column=1, padx=(5, 10), pady=(0, 15), sticky='ew')

        Label(
            frame,
            text="Lakeshore 350 VISA:").grid(
            row=6,
            column=0,
            columnspan=2,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.lakeshore_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_combobox.grid(
            row=7, column=0, columnspan=2, padx=10, pady=(0, 5),
            sticky='ew')

        Label(
            frame,
            text="Keithley 6517B VISA:").grid(
            row=8,
            column=0,
            columnspan=2,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.keithley_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_combobox.grid(
            row=9, column=0, columnspan=2, padx=10, pady=(0, 15),
            sticky='ew')

        self.scan_button = ttk.Button(
            frame,
            text="Scan Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=10,
            column=0,
            columnspan=2,
            padx=10,
            pady=5,
            sticky='ew')
        self.file_location_button = ttk.Button(
            frame,
            text="Browse Save Location",
            command=self._browse_file_location)
        self.file_location_button.grid(
            row=11,
            column=0,
            columnspan=2,
            padx=10,
            pady=5,
            sticky='ew')

        button_frame = ttk.Frame(frame)
        button_frame.grid(
            row=12,
            column=0,
            columnspan=2,
            padx=10,
            pady=10,
            sticky='ew')
        button_frame.grid_columnconfigure(0, weight=1)
        button_frame.grid_columnconfigure(1, weight=1)
        self.start_button = ttk.Button(
            button_frame,
            text="Start Measurement",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=0, column=0, padx=(0, 5), pady=5, sticky='ew')
        self.stop_button = ttk.Button(
            button_frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=0, column=1, padx=(5, 0), pady=5, sticky='ew')

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Console Output')
        frame.pack(pady=10, padx=10, fill='both', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0,
            relief='flat',
            height=10)
        self.console_widget.pack(pady=10, padx=10, fill='both',
                                 expand=True)
        self.log("Console initialized.")
        if not PIL_AVAILABLE:
            self.log(
                "Note: 'Pillow' not found. Logo cannot be displayed. "
                "Run 'pip install Pillow'.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL ERROR: pyvisa or pymeasure not found.")
        else:
            self.log(
                "Please select a save location and scan for instruments.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graphs')
        graph_container.pack(fill='both', expand=True, padx=(10, 0),
                            pady=0)

        try:
            plt.style.use('seaborn-v0_8-whitegrid')
        except OSError:
            try:
                plt.style.use('seaborn-whitegrid')
            except OSError:
                self.log(
                    "Warning: Seaborn plot style not found. "
                    "Using default.")
                pass

        self.figure = Figure(
            figsize=(10, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = self.figure.add_gridspec(2, 2, height_ratios=[2, 1.2])
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])
        self.axes = [self.ax_main, self.ax_sub1, self.ax_sub2]

        self.line_main, = self.ax_main.plot(
            [], [], color='#e63946', marker='o', markersize=4,
            linestyle='-', linewidth=1.5)
        self.ax_main.set_title("Current vs. Temperature",
                               fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Current (A)")

        self.line_sub1, = self.ax_sub1.plot(
            [], [], color='#0077b6', marker='.', markersize=4,
            linestyle='-', linewidth=1)
        self.ax_sub1.set_title("Temp vs. Time")
        self.ax_sub1.set_xlabel("Time (s)")
        self.ax_sub1.set_ylabel("Temperature (K)")

        self.line_sub2, = self.ax_sub2.plot(
            [], [], color='#06d6a0', marker='.', markersize=4,
            linestyle='-', linewidth=1)
        self.ax_sub2.set_title("Current vs. Time")
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Current (A)")

        for ax in self.axes:
            ax.grid(True, linestyle='--', alpha=0.7)
            ax.ticklabel_format(
                axis='y', style='sci', scilimits=(-2, 3),
                useMathText=True)
        self.figure.tight_layout(pad=3.0)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(
            fill='both', expand=True, padx=5, pady=5)

    def _process_logo_image(self, input_path, size=100):
        if not (PIL_AVAILABLE and os.path.exists(input_path)):
            return None
        try:
            with Image.open(input_path) as img:
                img_resized = img.resize(
                    (size, size), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img_resized)
        except Exception as e:
            print(
                f"ERROR: Could not process logo image "
                f"'{input_path}'. Reason: {e}")
            return None

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _handle_worker_thread_completion(self):
        pass

    def _handle_worker_thread_error(self, exception):
        self.log(f"RUNTIME ERROR in worker thread: {traceback.format_exc()}")
        self.stop_measurement("runtime error")
        messagebox.showerror(
            "Runtime Error", "An error occurred. Check console.")

    def _process_stabilizing_state(self, current_temp, params):
        self.log(
            f"Stabilizing... Current Temp: {current_temp:.4f} K "
            f"(Target: {params['start_temp']} K)")
        if abs(current_temp - params['start_temp']) < 5:
            self.log(
                f"Stabilized at {params['start_temp']} K. Starting ramp.")
            self.experiment_state = 'ramping'
            self.backend.start_ramp()
            self.start_time = time.time()

    def _process_ramping_state(self, current_temp, current_val, params):
        elapsed_time = time.time() - self.start_time
        self._log_and_save_ramping_data(
            elapsed_time, current_temp, current_val)
        self._update_data_storage_and_plots(
            elapsed_time, current_temp, current_val)
        self._check_ramping_completion_conditions(current_temp, params)

    def _log_and_save_ramping_data(
            self, elapsed_time, current_temp, current_val):
        log_msg = (
            f"Time: {elapsed_time:.1f}s | "
            f"Temp: {current_temp:.2f}K | "
            f"Current: {current_val:.2e}A"
        )
        self.log(log_msg)
        with open(self.data_filepath, 'a', newline='') as f:
            f.write(f"{elapsed_time:.2f},{current_temp:.4f},{current_val}\n")

    def _update_data_storage_and_plots(
            self, elapsed_time, current_temp, current_val):
        self.data_storage['time'].append(elapsed_time)
        self.data_storage['temperature'].append(current_temp)
        self.data_storage['current'].append(current_val)

        temp = self.data_storage['temperature']
        curr = self.data_storage['current']
        t = self.data_storage['time']

        self.line_main.set_data(temp, curr)
        self.line_sub1.set_data(t, temp)
        self.line_sub2.set_data(t, curr)

        for ax in self.axes:
            ax.relim()
            ax.autoscale_view()

        self.canvas.draw_idle()

    def _check_ramping_completion_conditions(self, current_temp, params):
        if current_temp >= params['safety_cutoff']:
            self.stop_measurement(
                f"SAFETY CUTOFF REACHED at {current_temp:.4f} K!")
            return
        if current_temp >= params['end_temp']:
            self.stop_measurement(
                f"Target temperature of {params['end_temp']} K reached.")
            return

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'start_temp': float(self.entries["Start Temp"].get()),
                'end_temp': float(self.entries["End Temp"].get()),
                'rate': float(self.entries["Ramp Rate"].get()),
                'safety_cutoff': float(
                    self.entries["Safety Cutoff"].get()),
                'lakeshore_visa': self.lakeshore_combobox.get(),
                'keithley_visa': self.keithley_combobox.get()
            }
            if not all([params['sample_name'],
                        params['lakeshore_visa'],
                        params['keithley_visa'],
                        self.file_location_path]):
                raise ValueError(
                    "All fields, VISA addresses, and a save location "
                    "are required.")
            if not (params['start_temp'] < params['end_temp']
                    < params['safety_cutoff']):
                raise ValueError(
                    "Temperatures must be in ascending order "
                    "(Start < End < Cutoff).")
            if params['rate'] <= 0:
                raise ValueError("Ramp rate must be a positive number.")

            self.backend.initialize_instruments(params)
            self.log(
                f"Backend initialized for sample: "
                f"{params['sample_name']}")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_Pyro.csv"
            self.data_filepath = os.path.join(
                self.file_location_path, file_name)
            with open(self.data_filepath, 'w', newline='') as f:
                header = (
                    f"# Sample: {params['sample_name']}\n"
                    f"# Start: {params['start_temp']} K, "
                    f"End: {params['end_temp']} K, "
                    f"Ramp: {params['rate']} K/min\n"
                )
                f.write(header)
                f.write("Time (s),Temperature (K),Current (A)\n")
            self.log(
                f"Output file created: "
                f"{os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]:
                line.set_data([], [])
            self.ax_main.set_title(
                f"I vs T | Sample: {params['sample_name']}",
                fontweight='bold')

            for ax in self.axes:
                ax.relim()
                ax.autoscale_view()
            self.figure.tight_layout(pad=3.0)
            self.canvas.draw_idle()
            self.log("Live graphs initialized.")

            self.log(
                "Moving to start temperature for stabilization...")
            self.experiment_state = 'stabilizing'
            self.backend.start_stabilization()

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start measurement.\n\nDetails:\n{e}")

    def stop_measurement(self, reason="stopped by user"):
        if self.is_running:
            self.is_running = False
            self.experiment_state = 'idle'
            self.log(f"Measurement loop {reason}.")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            if (self.measurement_thread is not None
                    and self.measurement_thread.is_alive()
                    and threading.current_thread()
                    is not self.measurement_thread):
                self.measurement_thread.join(timeout=3.0)
            self.backend.close_instruments()
            self.log("Instrument connections closed.")
            messagebox.showinfo(
                "Info", f"Measurement stopped.\nReason: {reason}")

    def _measurement_worker(self):
        """Worker thread to perform measurements and put data into a
        queue."""
        while self.is_running:
            try:
                current_temp, current_val = \
                    self.backend.get_measurement()
                self.data_queue.put(
                    (current_temp, current_val, self.experiment_state))
                time.sleep(2)  # Control the measurement frequency
            except Exception as e:
                self.data_queue.put(e)
                break
        self.data_queue.put(None)  # Sentinel value to signal completion

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI. Runs in the
        main thread."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if data is None:
                    self._handle_worker_thread_completion()
                    return
                if isinstance(data, Exception):
                    self._handle_worker_thread_error(data)
                    return

                current_temp, current_val, state = data
                params = self.backend.params

                if state == 'stabilizing':
                    self._process_stabilizing_state(current_temp, params)
                elif state == 'ramping':
                    self._process_ramping_state(
                        current_temp, current_val, params)

        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _scan_for_visa_instruments(self):
        if not self._check_visa_prerequisites():
            return

        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self._assign_instruments_to_comboboxes(resources)
                self._set_default_combobox_values(resources)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno(
                    "Exit",
                    "Measurement is running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.root.destroy()

    def _check_visa_prerequisites(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return False
        if self.backend.rm is None:
            self.log("ERROR: VISA manager failed. Is NI-VISA installed?")
            return False
        return True

    def _assign_instruments_to_comboboxes(self, resources):
        self.keithley_combobox['values'] = resources
        self.lakeshore_combobox['values'] = resources
        for res in resources:
            if "GPIB1::27" in res:
                self.keithley_combobox.set(res)
            if "GPIB1::15" in res:
                self.lakeshore_combobox.set(res)

    def _set_default_combobox_values(self, resources):
        if not self.keithley_combobox.get() and resources:
            self.keithley_combobox.set(resources[0])
        if not self.lakeshore_combobox.get() and resources:
            if (len(resources) > 1
                    and resources[0] == self.keithley_combobox.get()):
                self.lakeshore_combobox.set(resources[1])
            elif resources:
                self.lakeshore_combobox.set(resources[0])


if __name__ == '__main__':
    root = tk.Tk()
    app = PyroelectricAppGUI(root)
    root.mainloop()