"""
Module: IV_K6517B_GUI.py
Purpose: GUI module for IV K6517B GUI v5.

v5.0 (05 Oct 2026) - sweep types, verified logic, hardened run path
  * Sweep types: "Start → Stop (linear)" (the original behaviour, still
    the default), "0 to Max", "Loop (0 → Max → 0 → -Max → 0)" and
    "Custom List" (volts, with a worked example shown in the box). The
    generator is a pure function shared with the K2400 I-V modules: Max is
    hit exactly, nothing overshoots, turnarounds carry no duplicate point,
    and "Loops" repeats the pattern.
  * FIX: the 6517B voltage source has two ranges, 100 V and 1000 V, and
    *RST leaves it on 100 V (reference manual p. 11-112). A sweep above
    100 V now selects the 1000 V range before the output is enabled;
    previously every point above 100 V was rejected by the instrument.
  * FIX: a reading pymeasure cannot parse (returns None) or an overflow
    (+9.9e37, manual p. 11-59) is recorded as NaN instead of crashing the
    worker with a TypeError on "voltage / resistance".
  * FIX: errors raised in the worker were logged with traceback.format_exc()
    from the GUI thread, which prints "NoneType: None"; the traceback is
    now formatted in the thread that caught it.
  * The worker thread owns the instrument from connect to shutdown on
    every exit path (finished, Stop, error). The ~7 s connect + zero
    correction no longer freezes the window, and Stop never calls into a
    VISA session the worker is still using.
  * No modal dialog during or after a run: completion and errors go to the
    console, the title banner and a beep.
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
import threading
import queue
import numpy as np
import csv
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- winsound for run-end alerts (stdlib, Windows only; optional) ---
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

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


# ===============================================================================
# SWEEP GENERATION  (pure functions; inlined so the module stays standalone.
# The same code is carried by IV_K2400_GUI.py and IV_K2400_K2182_GUI.py.)
# ===============================================================================

SWEEP_LINEAR = "Start → Stop (linear)"
SWEEP_ZERO_TO_MAX = "0 to Max"
SWEEP_LOOP = "Loop (0 → Max → 0 → -Max → 0)"
SWEEP_CUSTOM = "Custom List"
SWEEP_TYPES = (SWEEP_LINEAR, SWEEP_ZERO_TO_MAX, SWEEP_LOOP, SWEEP_CUSTOM)

# Keithley 6517B voltage source: +/-1000 V on the 1000 V range, +/-100 V
# on the 100 V range (reference manual p. 11-112, :SOURce:VOLTage:RANGe).
K6517B_MAX_VOLTAGE_V = 1000.0
K6517B_LOW_RANGE_V = 100.0
# A reading of 9.9e37 or more is the 6517B overflow code (manual p. 11-59:
# "An overflow reading reads as +9.9e37"; zero-check reads +9.91E37).
K6517B_OVERFLOW = 9.9e37

# Shown inside the custom-list box the first time "Custom List" is chosen,
# so the expected format is visible without reading a manual. It is a valid
# list: a full loop with fine steps near zero and coarse steps at the top.
CUSTOM_LIST_EXAMPLE = (
    "0, 1, 2, 5, 10, 20, 50, 100,\n"
    "50, 20, 10, 5, 2, 1, 0,\n"
    "-1, -2, -5, -10, -20, -50, -100,\n"
    "-50, -20, -10, -5, -2, -1, 0")


def parse_custom_list(text):
    """Parse a user-typed list of numbers.

    Commas, semicolons, spaces, tabs and new lines all separate values, so
    a column pasted from a spreadsheet works as well as "0, 1, 2". Blank
    tokens are ignored. Raises ValueError naming the first bad token.
    Returns a plain list of floats (in the unit the user typed).
    """
    if text is None:
        raise ValueError("Custom list is empty.")
    cleaned = text.replace(",", " ").replace(";", " ")
    tokens = cleaned.split()
    if not tokens:
        raise ValueError("Custom list is empty.")
    values = []
    for tok in tokens:
        try:
            v = float(tok)
        except ValueError:
            raise ValueError(
                f"Custom list: '{tok}' is not a number. Use values such as "
                f"'0, 1, 2, 5' separated by commas, spaces or new lines.")
        if not np.isfinite(v):
            raise ValueError(f"Custom list: '{tok}' is not a finite number.")
        values.append(v)
    return values


def _zero_to_max_segment(max_val, step_val):
    """0, step, 2*step ... up to and INCLUDING max_val.

    Built from an integer count, not np.arange on floats, so the last point
    is exactly max_val and nothing beyond it. If max_val is not a multiple
    of step_val the final partial step lands on max_val itself (0, 0.3,
    0.6, 0.9, 1.0 for max 1, step 0.3). The sign of max_val sets the
    direction; step_val is always taken as a magnitude.
    """
    step = abs(float(step_val))
    target = float(max_val)
    if step <= 0:
        raise ValueError("Step must be greater than zero.")
    if target == 0:
        raise ValueError("Max must not be zero.")
    sign = 1.0 if target > 0 else -1.0
    magnitude = abs(target)
    # 1e-9 relative slack so 1.0 / 0.1 counts as 10 steps, not 9.
    n_full = int(np.floor(magnitude / step + 1e-9))
    points = [sign * k * step for k in range(n_full + 1)]
    if abs(points[-1]) < magnitude * (1 - 1e-9):
        points.append(target)
    else:
        points[-1] = target  # kill the float residue on the end point
    return points


def build_sweep_points(sweep_type, max_val=0.0, step_val=0.0, num_loops=1,
                       custom_values=None, start_val=0.0, stop_val=0.0,
                       num_points=0):
    """Return the ordered list of source set-points for one run.

    sweep_type   one of SWEEP_TYPES
    max_val      end value for "0 to Max" / turning value for "Loop"
    step_val     step magnitude for those two modes
    num_loops    how many times the whole pattern is repeated (>= 1)
    custom_values  list of floats for "Custom List" (already parsed)
    start_val, stop_val, num_points   for "Start → Stop (linear)"

    Values are returned in the unit they were given in. The caller applies
    the hardware limit check (see check_sweep_limits).
    """
    try:
        loops = int(num_loops)
    except (TypeError, ValueError):
        raise ValueError("Loops must be a whole number of 1 or more.")
    if loops < 1:
        raise ValueError("Loops must be 1 or more.")

    if sweep_type == SWEEP_LINEAR:
        try:
            n = int(num_points)
        except (TypeError, ValueError):
            raise ValueError("No. of Points must be a whole number.")
        if n < 2:
            raise ValueError("No. of Points must be 2 or more.")
        base = list(np.linspace(float(start_val), float(stop_val), n))
    elif sweep_type == SWEEP_ZERO_TO_MAX:
        base = _zero_to_max_segment(max_val, step_val)
    elif sweep_type == SWEEP_LOOP:
        up = _zero_to_max_segment(max_val, step_val)          # 0 -> Max
        down = up[::-1][1:]                                    # Max -> 0
        neg_up = [-v for v in up][1:]                          # 0 -> -Max
        neg_down = [-v for v in up][::-1][1:]                  # -Max -> 0
        base = up + down + neg_up + neg_down
    elif sweep_type == SWEEP_CUSTOM:
        if not custom_values:
            raise ValueError("Custom list is empty.")
        base = [float(v) for v in custom_values]
    else:
        raise ValueError(f"Unknown sweep type '{sweep_type}'.")

    return np.tile(np.asarray(base, dtype=float), loops)


def ascii_label(text):
    """Data-file-safe version of a GUI label: the arrows in the sweep-type
    names and the micro sign are replaced so the header never depends on
    the console code page (cp1252 on the lab PCs cannot encode the arrow).
    """
    return (str(text).replace("\u2192", "->").replace("\u00b5", "u")
            .replace("\u03a9", "Ohm").encode("ascii", "replace").decode("ascii"))


def check_sweep_limits(points, limit, unit_name):
    """Raise ValueError if any |point| exceeds the hardware limit."""
    if len(points) == 0:
        raise ValueError("The sweep contains no points.")
    worst = float(np.max(np.abs(points)))
    if worst > limit:
        raise ValueError(
            f"Sweep reaches {worst:g} {unit_name}, above the instrument "
            f"limit of {limit:g} {unit_name}.")


def source_range_for(max_abs_voltage):
    """100 V range when every point fits in it, else the 1000 V range."""
    return K6517B_LOW_RANGE_V if abs(max_abs_voltage) <= K6517B_LOW_RANGE_V \
        else K6517B_MAX_VOLTAGE_V


# -------------------------------------------------------------------------------
# --- REAL INSTRUMENT BACKEND ---
# The setup and measurement logic from the V5 Core script is kept here.
# -------------------------------------------------------------------------------


class Keithley6517B_Backend:
    """
    Backend communication with a real Keithley 6517B.
    Every method is called from the worker thread only; the GUI thread
    never touches the instrument.
    """

    def __init__(self):
        self.keithley = None
        self.is_connected = False
        if not PYMEASURE_AVAILABLE:
            raise ImportError(
                "PyMeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")

    def initialize_instruments(self, parameters, log=print):
        """
        Connects to the instrument, performs the zero-check sequence
        (V5 Core methodology), selects the source range for the sweep and
        enables the output at 0 V.
        parameters['keithley_visa']     VISA address
        parameters['max_abs_voltage']   largest |V| of the sweep
        """
        log(f"--- [Backend] Initializing Instrument at {parameters['keithley_visa']} ---")
        try:
            self.keithley = Keithley6517B(
                parameters['keithley_visa'], timeout=20000)
            log(f"  Successfully connected to: {self.keithley.id}")

            # --- Configure Measurement and Perform Zero Correction (V5 Core Logic) ---
            log("  Configuring instrument and performing zero correction...")
            self.keithley.reset()
            # Resistance function with the 6517B's own V-source: after *RST
            # :SENS:RES:VSControl is MANual (manual p. 11-100), so the sweep
            # voltages written below are the ones used for R = V / I.
            self.keithley.measure_resistance()

            # --- Perform Zero Correction Sequence ---
            log("  Starting zero correction procedure...")
            time.sleep(1)  # Reduced wait time for GUI responsiveness

            # 1. Enable Zero Check
            log("    Step 1/4: Enabling Zero Check mode...")
            self.keithley.write(':SYSTem:ZCHeck ON')  # type: ignore
            time.sleep(2)

            # 2. Acquire the zero measurement
            log("    Step 2/4: Acquiring zero correction value...")
            self.keithley.write(':SYSTem:ZCORrect:ACQuire')  # type: ignore
            time.sleep(2)  # Allow time for acquisition

            # 3. Disable Zero Check
            log("    Step 3/4: Disabling Zero Check mode...")
            self.keithley.write(':SYSTem:ZCHeck OFF')  # type: ignore
            time.sleep(1)

            # 4. Enable Zero Correct
            log("    Step 4/4: Enabling Zero Correction for all measurements.")
            self.keithley.write(':SYSTem:ZCORrect ON')  # type: ignore
            time.sleep(1)
            log("  Zero Correction Complete.")

            # Set integration rate for noise reduction (as per V5 core script)
            self.keithley.current_nplc = 1

            # Source range: *RST leaves 100 V; anything above needs 1000 V.
            rng = source_range_for(parameters.get('max_abs_voltage', 0.0))
            self.keithley.write(f':SOURce:VOLTage:RANGe {rng:g}')
            log(f"  Voltage source range: {rng:g} V")

            # Output ON at 0 V, never at the first set-point.
            self.keithley.source_voltage = 0
            self.keithley.enable_source()

            self.is_connected = True
            log("--- [Backend] Instrument Initialized and Ready (output ON at 0 V) ---")

        except Exception as e:
            if VisaIOError is not None and isinstance(e, VisaIOError):
                log(f"  [VISA Connection Error] Could not connect. Details: {e}")
                self.close_instruments(log)
                raise ConnectionError(
                    "Could not connect to Keithley 6517B.\nCheck address and connections.") from e
            log(f"  [Unexpected Error] during initialization. Details: {e}")
            self.close_instruments(log)
            raise

    def set_voltage(self, voltage):
        """Sets the voltage source level (output is already enabled)."""
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")
        self.keithley.source_voltage = float(voltage)

    def get_measurement(self):
        """
        Reads resistance and derives current, mirroring the V5 Core script.
        Returns (resistance, current, voltage). A reading the driver cannot
        parse, or an overflow (>= 9.9e37), comes back as NaN.
        """
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")

        voltage = self.keithley.source_voltage  # Read back the actual source voltage
        voltage = float(voltage) if voltage is not None else float('nan')
        resistance = self.keithley.resistance
        resistance = float(resistance) if resistance is not None else float('nan')
        if abs(resistance) >= K6517B_OVERFLOW:
            resistance = float('nan')

        if np.isfinite(resistance) and resistance != 0:
            current = voltage / resistance
        else:
            current = float('nan')
        return resistance, current, voltage

    def measure_at_voltage(self, voltage, delay_s, wait=time.sleep):
        """Set the voltage, settle for delay_s, then read.

        wait(seconds) defaults to time.sleep; the GUI passes
        threading.Event.wait so Stop interrupts the settling delay. If the
        wait returns True (stop requested) nothing is read and None is
        returned.
        """
        self.set_voltage(voltage)
        if delay_s > 0 and wait(delay_s):
            return None
        return self.get_measurement()

    def close_instruments(self, log=print):
        """Safely shuts down the voltage source and disconnects. Never raises."""
        inst, self.keithley = self.keithley, None
        self.is_connected = False
        if inst is None:
            return
        log("--- [Backend] Closing instrument connection. ---")
        try:
            log("  Shutting down voltage source...")
            inst.shutdown()
            log("  Voltage source OFF. Instrument is safe.")
        except Exception as e:
            log(f"  Warning: Could not gracefully shut down instrument. Error: {e}")
            try:
                inst.write("OUTPUT OFF")
                log("  Output forced OFF.")
            except Exception:
                pass

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class HighResistanceIV_GUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "5.0"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible
    BASE_TITLE = "Keithley 6517B: High Resistance I-V Measurement"
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
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#EF233C'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_HINT = ('Segoe UI', FONT_SIZE_BASE - 2, 'italic')
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(self.BASE_TITLE)
        self.root.geometry("1550x900")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 800)

        self.is_running = False
        self.start_time = None
        self.logo_image = None  # Attribute to hold the logo image reference
        try:
            self.backend = Keithley6517B_Backend()
        except Exception as e:
            messagebox.showerror(
                "Backend Error",
                f"Could not initialize the backend.\nError: {e}\n\n"
                "Please ensure PyMeasure and NI-VISA are installed correctly.")
            self.backend = None
        self.file_location_path = ""
        self.data_filepath = None
        self.params = {}
        self.data_storage = {
            'time': [],
            'voltage_applied': [],
            'current_measured': [],
            'resistance': []}
        self.voltage_list = np.array([])
        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.measurement_thread = None
        self._pump_after_id = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._on_sweep_type_change()

    def setup_styles(self):
        """Configures ttk styles and Matplotlib for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure('Hint.TLabel', font=self.FONT_HINT)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                8),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton',
            background=[('active', self.CLR_ACCENT_GOLD),
                        ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[('active', self.CLR_TEXT_DARK),
                        ('hover', self.CLR_TEXT_DARK)]
        )
        style.configure(
            'Start.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
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
        """Lays out the main frames and populates them with widgets."""
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

        right_panel = tk.Frame(self.main_pane, bg='white')
        self.main_pane.add(right_panel, weight=1)  # More weight for the graph panel

        # Create the console frame first to initialize the logger, but don't pack it yet.
        console_pane = self.create_console_frame(scrollable_frame)

        # Create and populate the top frame, which can now safely log messages.
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

        Label(
            header_frame,
            text="Keithley 6517B: High Resistance I-V Sweep",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(
            side='left',
            padx=20,
            pady=10)

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
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)

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

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        info_label = ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK)
        info_label.grid(
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

        details_text = ("Program Name: I-V Sweep\n"
                        "Instrument: Keithley 6517B Electrometer\n"
                        "Measurement Range: 1 Ω to 10 PΩ (10¹⁶ Ω)")
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
        frame.pack(pady=10, padx=10, fill='x')
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)
        row = 0

        Label(
            frame,
            text="Sample Name:").grid(
            row=row, column=0, columnspan=4, padx=10, pady=pady_val, sticky='w')
        row += 1
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=row, column=0, columnspan=4, padx=10, pady=(0, 10), sticky='ew')
        row += 1

        # --- Sweep type ---
        Label(frame, text="Sweep Type:").grid(
            row=row, column=0, columnspan=4, padx=10, pady=pady_val, sticky='w')
        row += 1
        # Explicit master: a StringVar without one binds to Tk's default
        # root, which may belong to another window in the same process.
        self.sweep_type_var = tk.StringVar(master=self.root)
        self.sweep_type_cb = ttk.Combobox(
            frame,
            textvariable=self.sweep_type_var,
            state='readonly',
            font=self.FONT_BASE,
            values=list(SWEEP_TYPES))
        self.sweep_type_cb.grid(
            row=row, column=0, columnspan=4, padx=10, pady=(0, 10), sticky='ew')
        self.sweep_type_cb.set(SWEEP_LINEAR)
        self.sweep_type_cb.bind("<<ComboboxSelected>>", self._on_sweep_type_change)
        row += 1

        # --- Linear: Start / Stop / Points  (one row, shown for "linear") ---
        self._linear_widgets = []
        lbl = Label(frame, text="Start V:")
        lbl.grid(row=row, column=0, padx=(10, 0), pady=pady_val, sticky='w')
        self.entries["Start V"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Start V"].grid(row=row, column=1, padx=(0, 10), pady=pady_val, sticky='w')
        lbl2 = Label(frame, text="Stop V:")
        lbl2.grid(row=row, column=2, padx=(10, 0), pady=pady_val, sticky='w')
        self.entries["Stop V"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Stop V"].grid(row=row, column=3, padx=(0, 10), pady=pady_val, sticky='w')
        self._linear_widgets += [lbl, self.entries["Start V"], lbl2, self.entries["Stop V"]]
        row += 1
        lbl3 = Label(frame, text="No. of Points:")
        lbl3.grid(row=row, column=0, padx=(10, 0), pady=pady_val, sticky='w')
        self.entries["Steps"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Steps"].grid(row=row, column=1, padx=(0, 10), pady=pady_val, sticky='w')
        self._linear_widgets += [lbl3, self.entries["Steps"]]
        self._linear_row_points = row
        row += 1

        # --- 0 to Max / Loop: Max V / Step V ---
        self._maxstep_widgets = []
        lbl4 = Label(frame, text="Max V:")
        lbl4.grid(row=row, column=0, padx=(10, 0), pady=pady_val, sticky='w')
        self.entries["Max V"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Max V"].grid(row=row, column=1, padx=(0, 10), pady=pady_val, sticky='w')
        lbl5 = Label(frame, text="Step V:")
        lbl5.grid(row=row, column=2, padx=(10, 0), pady=pady_val, sticky='w')
        self.entries["Step V"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Step V"].grid(row=row, column=3, padx=(0, 10), pady=pady_val, sticky='w')
        self._maxstep_widgets += [lbl4, self.entries["Max V"], lbl5, self.entries["Step V"]]
        row += 1

        # --- Custom list ---
        self.custom_list_label = Label(frame, text="Custom Voltage List (V):")
        self.custom_list_label.grid(
            row=row, column=0, columnspan=4, padx=10, pady=(5, 0), sticky='w')
        row += 1
        self.custom_list_hint = ttk.Label(
            frame,
            style='Hint.TLabel',
            text=("Separate values with commas, spaces or new lines. "
                  "Points are sourced in the order written, e.g.\n"
                  "0, 1, 2, 5, 10, 5, 2, 1, 0, -1, -2, -5, -10, -5, -2, -1, 0"),
            wraplength=self.LEFT_PANEL_WIDTH - 60,
            justify='left')
        self.custom_list_hint.grid(
            row=row, column=0, columnspan=4, padx=10, sticky='w')
        row += 1
        self.custom_list_text = scrolledtext.ScrolledText(
            frame, height=5, font=self.FONT_BASE, wrap='word')
        self.custom_list_text.grid(
            row=row, column=0, columnspan=4, padx=10, pady=(0, 5), sticky='ew')
        row += 1

        # --- Loops / Delay (all sweep types) ---
        Label(frame, text="Loops:").grid(
            row=row, column=0, padx=(10, 0), pady=pady_val, sticky='w')
        self.entries["Loops"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Loops"].grid(row=row, column=1, padx=(0, 10), pady=pady_val, sticky='w')
        self.entries["Loops"].insert(0, "1")
        Label(frame, text="Delay (s):").grid(
            row=row, column=2, padx=(10, 0), pady=pady_val, sticky='w')
        self.entries["Delay (s)"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Delay (s)"].grid(
            row=row, column=3, padx=(0, 10), pady=pady_val, sticky='w')
        self.entries["Delay (s)"].insert(0, "1.0")
        row += 1

        Label(
            frame,
            text="Keithley 6517B VISA:").grid(
            row=row, column=0, columnspan=4, padx=10, pady=(10, 5), sticky='w')
        row += 1
        self.keithley_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_combobox.grid(
            row=row, column=0, columnspan=4, padx=10, pady=(0, 5), sticky='ew')
        row += 1

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=row, column=0, columnspan=4, padx=10, pady=5, sticky='ew')
        row += 1

        self.file_location_button = ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location)
        self.file_location_button.grid(
            row=row, column=0, columnspan=4, padx=10, pady=5, sticky='ew')
        row += 1

        self.start_button = ttk.Button(
            frame,
            text="Start Sweep",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=row, column=0, columnspan=2, padx=10, pady=15, sticky='ew')
        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=row, column=2, columnspan=2, padx=10, pady=15, sticky='ew')

    def _on_sweep_type_change(self, event=None):
        """Show only the entries that the chosen sweep type uses."""
        if not hasattr(self, 'sweep_type_var'):
            return
        selection = self.sweep_type_var.get()
        show_linear = selection == SWEEP_LINEAR
        show_maxstep = selection in (SWEEP_ZERO_TO_MAX, SWEEP_LOOP)
        show_custom = selection == SWEEP_CUSTOM

        for w in self._linear_widgets:
            w.grid() if show_linear else w.grid_remove()
        for w in self._maxstep_widgets:
            w.grid() if show_maxstep else w.grid_remove()
        for w in (self.custom_list_label, self.custom_list_hint, self.custom_list_text):
            w.grid() if show_custom else w.grid_remove()

        if show_custom:
            # Show the worked example the first time the box appears so the
            # expected format is obvious; the user overwrites it freely.
            try:
                if not self.custom_list_text.get("1.0", tk.END).strip():
                    self.custom_list_text.insert("1.0", CUSTOM_LIST_EXAMPLE)
            except tk.TclError:
                pass

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
            self.log(
                "CRITICAL: PyMeasure or PyVISA not found. Please run 'pip install pymeasure'.")
        return frame

    def create_graph_frame(self, parent):
        """Creates the frame for graphs, now with two subplots."""
        graph_container = LabelFrame(
            parent,
            text='Live Graphs',
            relief='groove',
            bg=self.CLR_GRAPH_BG,
            fg=self.CLR_BG_DARK,
            font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)

        # Create two subplots stacked vertically
        self.ax_iv = self.figure.add_subplot(2, 1, 1)  # Top plot
        self.ax_rv = self.figure.add_subplot(2, 1, 2)  # Bottom plot

        # --- Configure Top Plot: I-V Curve ---
        self.line_iv, = self.ax_iv.plot(
            [], [], color=self.CLR_ACCENT_BLUE, marker='o', markersize=5, linestyle='-')
        self.ax_iv.set_title("Current vs. Voltage", fontweight='bold')
        self.ax_iv.set_ylabel("Measured Current (A)")
        self.ax_iv.grid(True, linestyle='--', alpha=0.6)

        # --- Configure Bottom Plot: R-V Curve ---
        self.line_rv, = self.ax_rv.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=5, linestyle='-')
        self.ax_rv.set_title("Resistance vs. Voltage", fontweight='bold')
        self.ax_rv.set_xlabel("Applied Voltage (V)")
        self.ax_rv.set_ylabel("Resistance (Ω)")

        # The y-axis is now logarithmic for better visualization of high resistance changes.
        self.ax_rv.set_yscale('log')
        self.ax_rv.grid(True, which="both", linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    # ------------------------------------------------------------------
    # Run-end alerts: never a modal dialog (nobody may be there to click)
    # ------------------------------------------------------------------
    def _beep(self, times=2):
        """Audible alert from a daemon thread so the GUI never blocks."""
        if HAS_WINSOUND:
            def _do_beep():
                try:
                    for _ in range(max(1, times)):
                        winsound.Beep(1000, 400)
                        time.sleep(0.15)
                except Exception:
                    pass
            threading.Thread(target=_do_beep, daemon=True).start()
        else:
            try:
                self.root.bell()
            except Exception:
                pass

    def _set_banner(self, text=""):
        """Run state in the window title and the top plot title. Never raises."""
        try:
            self.root.title(f"{self.BASE_TITLE} -- {text}" if text else self.BASE_TITLE)
            sample = self.params.get('sample_name', '')
            self.ax_iv.set_title(
                f"I-V Curve: {sample}  |  {text}" if text else f"I-V Curve: {sample}",
                fontweight='bold')
            self.canvas.draw_idle()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Parameter validation (pre-run; a dialog here is acceptable)
    # ------------------------------------------------------------------
    def _collect_params(self):
        """Read and validate every entry. Raises ValueError with a message
        the user can act on. Nothing here touches the instrument."""
        sweep_type = self.sweep_type_var.get()
        params = {
            'sample_name': self.entries["Sample Name"].get().strip(),
            'sweep_type': sweep_type,
            'keithley_visa': self.keithley_combobox.get(),
            'save_path': self.file_location_path,
            'start_v': 0.0, 'stop_v': 0.0, 'steps': 0,
            'max_v': 0.0, 'step_v': 0.0, 'custom_list_str': '',
        }
        if not params['sample_name']:
            raise ValueError("Sample Name is required.")
        if not params['keithley_visa']:
            raise ValueError("Select the Keithley 6517B VISA address (Scan for Instruments).")
        if not params['save_path']:
            raise ValueError("Choose a save location first.")
        if not os.path.isdir(params['save_path']):
            raise ValueError(f"Save location does not exist:\n{params['save_path']}")

        try:
            params['num_loops'] = int(self.entries["Loops"].get().strip())
        except ValueError:
            raise ValueError("Loops must be a whole number (1 or more).")
        try:
            params['delay_s'] = float(self.entries["Delay (s)"].get().strip())
        except ValueError:
            raise ValueError("Delay (s) must be a number.")
        if params['delay_s'] < 0:
            raise ValueError("Delay (s) cannot be negative.")

        custom_values = None
        if sweep_type == SWEEP_LINEAR:
            try:
                params['start_v'] = float(self.entries["Start V"].get().strip())
                params['stop_v'] = float(self.entries["Stop V"].get().strip())
            except ValueError:
                raise ValueError("Start V and Stop V must be numbers.")
            try:
                params['steps'] = int(self.entries["Steps"].get().strip())
            except ValueError:
                raise ValueError("No. of Points must be a whole number.")
        elif sweep_type in (SWEEP_ZERO_TO_MAX, SWEEP_LOOP):
            try:
                params['max_v'] = float(self.entries["Max V"].get().strip())
                params['step_v'] = float(self.entries["Step V"].get().strip())
            except ValueError:
                raise ValueError("Max V and Step V must be numbers.")
            if params['max_v'] == 0:
                raise ValueError("Max V must not be zero.")
            if params['step_v'] <= 0:
                raise ValueError("Step V must be greater than zero.")
        elif sweep_type == SWEEP_CUSTOM:
            params['custom_list_str'] = self.custom_list_text.get("1.0", tk.END)
            custom_values = parse_custom_list(params['custom_list_str'])

        points = build_sweep_points(
            sweep_type,
            max_val=params['max_v'],
            step_val=params['step_v'],
            num_loops=params['num_loops'],
            custom_values=custom_values,
            start_val=params['start_v'],
            stop_val=params['stop_v'],
            num_points=params['steps'])
        check_sweep_limits(points, K6517B_MAX_VOLTAGE_V, "V")
        params['max_abs_voltage'] = float(np.max(np.abs(points)))
        return params, points

    def _sweep_description(self, params):
        t = params['sweep_type']
        if t == SWEEP_LINEAR:
            return f"{params['start_v']:g} V to {params['stop_v']:g} V in {params['steps']} points"
        if t == SWEEP_CUSTOM:
            return "custom list: " + " ".join(params['custom_list_str'].split())
        return f"{t}: max {params['max_v']:g} V, step {params['step_v']:g} V"

    def _write_file_header(self, params, n_points):
        with open(self.data_filepath, 'w', newline='', encoding='utf-8') as f:
            f.write(f"# Program: Keithley 6517B High Resistance I-V Sweep v{self.PROGRAM_VERSION}\n")
            f.write(f"# Sample Name: {params['sample_name']}\n")
            f.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Instrument: Keithley 6517B at {params['keithley_visa']} "
                    f"(V-source, resistance function, zero corrected, NPLC 1)\n")
            f.write(f"# Sweep type: {ascii_label(params['sweep_type'])}\n")
            f.write(f"# Voltage Sweep: {ascii_label(self._sweep_description(params))}\n")
            f.write(f"# Loops: {params['num_loops']}, points: {n_points}, "
                    f"delay: {params['delay_s']:g} s, "
                    f"source range: {source_range_for(params['max_abs_voltage']):g} V\n")
            writer = csv.writer(f)
            writer.writerow(["Time (s)",
                             "Applied Voltage (V)",
                             "Measured Current (A)",
                             "Resistance (Ohms)"])

    def _append_row(self, elapsed_time, volt, cur, res):
        with open(self.data_filepath, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(
                [f"{elapsed_time:.3f}", f"{volt:.4e}", f"{cur:.4e}", f"{res:.4e}"])
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------
    def start_measurement(self):
        if self.is_running:
            return
        if self.backend is None:
            messagebox.showerror(
                "Backend Error",
                "Backend is not available. Cannot start measurement.")
            return
        try:
            params, points = self._collect_params()
        except Exception as e:
            self.log(f"Cannot start: {e}")
            messagebox.showerror("Check the parameters", f"{e}")
            return

        try:
            self.params = params
            self.voltage_list = points
            self.log(f"Sweep '{params['sweep_type']}': {len(points)} points, "
                     f"|V|max = {params['max_abs_voltage']:g} V "
                     f"({source_range_for(params['max_abs_voltage']):g} V source range).")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_IV.dat"
            self.data_filepath = os.path.join(params['save_path'], file_name)
            self._write_file_header(params, len(points))
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")
        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not create the data file.\n{e}")
            return

        self.is_running = True
        self.stop_event.clear()
        self.start_time = time.time()
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        for key in self.data_storage:
            self.data_storage[key].clear()

        # Clear both plot lines and redraw with the new title
        self.line_iv.set_data([], [])
        self.line_rv.set_data([], [])
        for ax in [self.ax_iv, self.ax_rv]:
            ax.relim()
            ax.autoscale_view()
        self._set_banner("CONNECTING")
        self.log("Measurement sweep started.")

        # The worker owns the instrument: connect, zero-correct, sweep, shut down.
        self.measurement_thread = threading.Thread(
            target=self._measurement_worker,
            args=(params, points),
            daemon=True)
        self.measurement_thread.start()
        self._pump_after_id = self.root.after(100, self._process_data_queue)

    def stop_measurement(self):
        """Ask the worker to stop. It finishes the point in hand, ramps the
        source to zero and switches the output off itself; the GUI thread
        never touches the instrument."""
        if not self.is_running:
            return
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.log("Stop requested; finishing the current point and switching the output off...")
            self._set_banner("STOPPING")
        self.stop_button.config(state='disabled')

    # ------------------------------------------------------------------
    # Worker thread: owns the instrument from connect to shutdown
    # ------------------------------------------------------------------
    def _measurement_worker(self, params, voltage_list):
        q = self.data_queue
        outcome = "finished"
        log = lambda msg: q.put(("LOG", msg))
        try:
            self.backend.initialize_instruments(params, log=log)
            if self.stop_event.is_set():
                outcome = "stopped"
            else:
                q.put(("STATE", "RUNNING"))
                delay = params['delay_s']
                n = len(voltage_list)
                for i, voltage in enumerate(voltage_list):
                    if self.stop_event.is_set():
                        outcome = "stopped"
                        break
                    log(f"Step {i + 1}/{n}: Set V = {voltage:.3f} V. Waiting {delay:g} s...")
                    # Event.wait returns True as soon as Stop is pressed, so
                    # a long settling delay never holds the output on.
                    result = self.backend.measure_at_voltage(
                        float(voltage), delay, wait=self.stop_event.wait)
                    if result is None:
                        outcome = "stopped"
                        break
                    res, cur, volt = result
                    elapsed_time = time.time() - self.start_time
                    q.put(("DATA", res, cur, volt, elapsed_time))
        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            q.put(("ERROR", tb))
            outcome = "error"
        finally:
            self.backend.close_instruments(log=log)
            q.put(("DONE", outcome))

    # ------------------------------------------------------------------
    # GUI-thread queue pump
    # ------------------------------------------------------------------
    def _update_plots(self):
        """Safely updates plots discarding non-finite values to prevent crashes on log scales."""
        v_data = np.array(self.data_storage['voltage_applied'], dtype=float)
        i_data = np.array(self.data_storage['current_measured'], dtype=float)
        r_data = np.array(self.data_storage['resistance'], dtype=float)

        # --- Update I-V Plot ---
        # Filter out NaN/Inf that might result from math errors (like zero division)
        valid_iv = np.isfinite(v_data) & np.isfinite(i_data)
        self.line_iv.set_data(v_data[valid_iv], i_data[valid_iv])
        self.ax_iv.relim()
        self.ax_iv.autoscale_view()

        # --- Update R-V Plot ---
        # Because R-V has a log y-axis, we must also filter out zero and negative values
        valid_rv = np.isfinite(v_data) & np.isfinite(r_data) & (r_data > 0)
        self.line_rv.set_data(v_data[valid_rv], r_data[valid_rv])
        self.ax_rv.relim()
        self.ax_rv.autoscale_view()

        # Trigger a full redraw of the canvas to ensure ticks/labels update
        self.canvas.draw_idle()

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI. Runs in the main thread."""
        self._pump_after_id = None
        finished = False
        try:
            while True:
                data = self.data_queue.get_nowait()
                kind = data[0]
                if kind == "LOG":
                    self.log(data[1])
                elif kind == "STATE":
                    self._set_banner(data[1])
                elif kind == "DATA":
                    _, res, cur, volt, elapsed_time = data
                    self._handle_point(res, cur, volt, elapsed_time)
                elif kind == "ERROR":
                    self.log(f"RUNTIME ERROR in worker thread:\n{data[1]}")
                elif kind == "DONE":
                    self._finish_run(data[1])
                    finished = True
        except queue.Empty:
            pass  # No data to process, which is normal

        if self.is_running and not finished:
            self._pump_after_id = self.root.after(200, self._process_data_queue)

    def _handle_point(self, res, cur, volt, elapsed_time):
        self.log(f"  Read -> V: {volt:.3e} V, I: {cur:.3e} A, R: {res:.3e} Ω")
        try:
            self._append_row(elapsed_time, volt, cur, res)
        except Exception as e:
            self.log(f"ERROR writing data file: {e}")

        self.data_storage['time'].append(elapsed_time)
        self.data_storage['voltage_applied'].append(volt)
        self.data_storage['current_measured'].append(cur)
        self.data_storage['resistance'].append(res)
        self._update_plots()

    def _finish_run(self, outcome):
        self.is_running = False
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        n_pts = len(self.data_storage['voltage_applied'])
        if outcome == "finished":
            msg = f"SWEEP COMPLETE - {n_pts} points saved."
        elif outcome == "stopped":
            msg = f"STOPPED by user after {n_pts} points. Output is OFF."
        else:
            msg = f"ABORTED on error after {n_pts} points. Output is OFF. See console."
        self.log(msg)
        if self.data_filepath:
            self.log(f"Data file: {self.data_filepath}")
        self._set_banner(msg.split(' - ')[0].split('.')[0])
        self._beep(2 if outcome == "finished" else 3)

    # ------------------------------------------------------------------
    def _scan_for_visa_instruments(self):
        if not pyvisa:
            self.log("ERROR: PyVISA is not installed. Cannot scan.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_combobox['values'] = resources
                # Attempt to find a likely candidate for the Keithley
                for res in resources:
                    if "GPIB" in res.upper() and ("27" in res or "26" in res or "25" in res):
                        self.keithley_combobox.set(res)
                        break
                else:
                    self.keithley_combobox.set(resources[0])
            else:
                self.log("No VISA instruments found.")
                self.keithley_combobox['values'] = []
                self.keithley_combobox.set("")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if not messagebox.askyesno(
                    "Exit", "Measurement sweep is running. Stop and exit?"):
                return
            # Let the worker take the output off; it owns the instrument.
            self.stop_event.set()
            t = self.measurement_thread
            if t is not None and t.is_alive():
                t.join(timeout=30)
            try:
                if self._pump_after_id is not None:
                    self.root.after_cancel(self._pump_after_id)
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    HighResistanceIV_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
