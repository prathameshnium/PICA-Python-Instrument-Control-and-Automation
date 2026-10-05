"""
Module: IV_K2400_GUI.py
Purpose: GUI module for IV K2400 GUI v13.

v13.0 (05 Oct 2026) - verified and hardened sweep logic
  * Sweep types: "0 to Max", "Loop (0 -> Max -> 0 -> -Max -> 0)" and
    "Custom List". The point generator is a pure, testable function
    (build_sweep_points): every sweep hits Max exactly even when Max is not
    a multiple of Step, never overshoots Max, the loop passes through 0 and
    the turnarounds carry no duplicate points, and "Loops" repeats the
    whole pattern. The custom list accepts commas, semicolons, spaces or
    new lines, and the GUI shows a worked example in the box.
  * FIX: the 2400 was never told to measure volts. After *RST the sense
    function is CURRent only, so every :READ? returned +9.91E+37 for the
    voltage element (manual p. 18-11 / 18-59). The backend now calls
    measure_voltage() before the compliance is set.
  * FIX: compliance is now detected with :SENS:VOLT:PROT:TRIP? (manual
    p. 18-68) instead of comparing the reading against 9.9e37, which is the
    overflow value and is never returned for a compliance-limited point.
  * FIX: the sweep is generated and validated BEFORE the instrument is
    touched, so a bad entry can no longer leave the output enabled.
  * The sweep runs in a worker thread that owns the instrument from
    connect to shutdown on every exit path (finished, Stop, error); the GUI
    stays responsive and Stop is honoured mid-delay.
  * No modal dialog is opened during or after a run: completion and errors
    go to the console, the title banner and a beep. Dialogs remain only
    for pre-run validation and the close confirmation.
"""

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
    from pymeasure.instruments.keithley import Keithley2400
    PYMEASURE_AVAILABLE = True
except ImportError:
    Keithley2400 = None
    PYMEASURE_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up 2 levels: k2400 -> keithley -> pica
    plotter_path = os.path.join(
        script_dir,
        "..", "..", "utils", "PlotterUtil_GUI.py")

    if not os.path.exists(plotter_path):
        messagebox.showerror("File Not Found",
                             f"Plotter Utility not found at:\n{plotter_path}")
        return
    Process(target=run_script_process, args=(plotter_path,)).start()


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up 2 levels: k2400 -> keithley -> pica
    scanner_path = os.path.join(
        script_dir,
        "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")

    if not os.path.exists(scanner_path):
        messagebox.showerror("File Not Found",
                             f"GPIB Scanner not found at:\n{scanner_path}")
        return
    Process(target=run_script_process, args=(scanner_path,)).start()


# ===============================================================================
# SWEEP GENERATION  (pure functions; inlined so the module stays standalone.
# The same code is carried by IV_K2400_K2182_GUI.py and IV_K6517B_GUI.py.)
# ===============================================================================

SWEEP_ZERO_TO_MAX = "0 to Max"
SWEEP_LOOP = "Loop (0 → Max → 0 → -Max → 0)"
SWEEP_CUSTOM = "Custom List"
SWEEP_TYPES = (SWEEP_ZERO_TO_MAX, SWEEP_LOOP, SWEEP_CUSTOM)

# Keithley 2400: 1.05 A is the full scale of the 1 A source range
# (manual Table 18-63 / :SOUR:CURR:RANG, -1.05 to 1.05 A).
K2400_MAX_CURRENT_A = 1.05
K2400_MAX_COMPLIANCE_V = 210.0
# A value of 9.9e37 or more in a reading is the 2400's "OVERFLOW" code
# (manual p. 7-6: "9.91E+37 will be returned via remote").
K2400_OVERFLOW = 9.9e37

# Shown inside the custom-list box the first time "Custom List" is chosen,
# so the expected format is visible without reading a manual. It is a valid
# list: a full loop with coarse steps near zero and a bigger step at the top.
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
                       custom_values=None):
    """Return the ordered list of source set-points for one run.

    sweep_type   one of SWEEP_TYPES
    max_val      end value for "0 to Max" / turning value for "Loop"
    step_val     step magnitude for those two modes
    num_loops    how many times the whole pattern is repeated (>= 1)
    custom_values  list of floats for "Custom List" (already parsed)

    Values are returned in the unit they were given in. The caller applies
    the hardware limit check (see check_sweep_limits).
    """
    try:
        loops = int(num_loops)
    except (TypeError, ValueError):
        raise ValueError("Loops must be a whole number of 1 or more.")
    if loops < 1:
        raise ValueError("Loops must be 1 or more.")

    if sweep_type == SWEEP_ZERO_TO_MAX:
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


# ===============================================================================
# BACKEND
# ===============================================================================

class Keithley2400_IV_Backend:
    """Backend communication with the Keithley 2400 for I-V sweeps.

    Source current, measure voltage. Every method is called from the
    worker thread only; the GUI thread never touches the instrument.
    """

    def __init__(self):
        self.keithley = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(
                    f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def connect_and_configure(self, visa_address, max_abs_current, compliance_v):
        """Connect, reset and configure: source I, measure V.

        Order matters on the 2400 (manual section 18, SENSe1 subsystem):
          1. *RST leaves the sense function at CURRent only, so
             measure_voltage() must enable VOLTage and make :READ? return
             the voltage element alone (:FORM:ELEM VOLT).
          2. The compliance value also selects the voltage measurement
             range ("the measurement range will be on the same range as the
             compliance setting"), so it is written AFTER measure_voltage()
             and is the last word on the range.
          3. The output is enabled at 0 A, never at the first set-point.
        """
        if not PYMEASURE_AVAILABLE:
            raise ImportError(
                "Pymeasure library is required. Please run 'pip install pymeasure'.")

        self.keithley = Keithley2400(visa_address)
        self.keithley.reset()
        self.keithley.use_front_terminals()
        self.keithley.apply_current()
        self.keithley.measure_voltage(nplc=1, auto_range=True)

        # :SOUR:CURR:RANG picks the lowest source range that accommodates
        # the value (each range spans 105 % of its nominal), so the largest
        # |set-point| of the sweep is the right value to hand it.
        rng = abs(float(max_abs_current))
        self.keithley.source_current_range = rng if rng > 0 else 1e-6
        self.keithley.compliance_voltage = float(compliance_v)
        self.keithley.source_current = 0
        self.keithley.enable_source()
        return self.keithley.id

    def set_current(self, current_setpoint):
        """Ramp the source to the set-point (5 short steps)."""
        self.keithley.ramp_to_current(float(current_setpoint), steps=5, pause=0.01)

    def read_voltage(self):
        """Read volts and the compliance flag.

        Returns (voltage, in_compliance). An overflow reading (>= 9.9e37)
        is returned as NaN so it never pollutes a plot or a fit. The
        compliance flag is :SENS:VOLT:PROT:TRIP? (1 = I-source is limited
        by the voltage compliance, manual p. 18-68).
        """
        voltage_reading = self.keithley.voltage
        if isinstance(voltage_reading, (list, tuple, np.ndarray)):
            voltage_reading = voltage_reading[0]
        voltage = float(voltage_reading)
        if abs(voltage) >= K2400_OVERFLOW:
            voltage = float('nan')
        tripped = str(self.keithley.ask(":SENS:VOLT:PROT:TRIP?")).strip()
        in_compliance = tripped.startswith("1")
        return voltage, in_compliance

    def measure_at_current(self, current_setpoint, delay_s, wait=time.sleep):
        """Ramp to the set-point, settle for delay_s, then read.

        wait(seconds) defaults to time.sleep; the GUI passes
        threading.Event.wait so Stop interrupts the settling delay. If the
        wait returns True (stop requested) nothing is read and None is
        returned.
        """
        self.set_current(current_setpoint)
        if delay_s > 0 and wait(delay_s):
            return None
        return self.read_voltage()

    def shutdown(self):
        """Ramp to 0 A, output OFF, close. Never raises."""
        inst, self.keithley = self.keithley, None
        if inst is None:
            return
        try:
            inst.shutdown()
        except Exception as e:
            # Last resort: make sure the output is off even if the ramp
            # (which queries the present level) failed.
            try:
                inst.write("OUTPUT OFF")
            except Exception:
                pass
            print(f"Warning: shutdown error on Keithley 2400: {e}")


# ===============================================================================
# FRONT END
# ===============================================================================

class MeasurementAppGUI:
    PROGRAM_VERSION = "13.0"
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#B8A392', '#E5DCD3', '#2C2825'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_GOLD = '#B68B6E', '#EF233C', '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_HINT = ('Segoe UI', FONT_SIZE_BASE - 2, 'italic')
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE = os.path.join(
            SCRIPT_DIR,
            "..",
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE = "../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"
    LOGO_SIZE = 120
    LEFT_PANEL_WIDTH = 480  # default sash position so the left panel starts fully visible
    BASE_TITLE = "Keithley 2400 I-V Measurement"

    def __init__(self, root):
        self.root = root
        self.root.title(self.BASE_TITLE)
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self._pump_after_id = None
        self.backend = Keithley2400_IV_Backend()
        self.file_location_path = ""
        self.data_filepath = None
        self.params = {}
        self.sweep_points = np.array([])
        self.data_storage = {'current': [], 'voltage': [], 'resistance': []}
        self.logo_image = None
        self.pre_init_logs = []

        self.custom_list_label = None
        self.custom_list_text = None
        self.custom_list_hint = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._on_sweep_type_change()

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
        style.configure('Hint.TLabel', font=self.FONT_HINT)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8))
        style.map(
            'TButton', foreground=[
                ('!active', '#B8A392'), ('active', '#2C2825')], background=[
                ('!active', '#BA6B5E'), ('active', '#B8A392')])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)
        style.configure(
            'TProgressbar',
            thickness=25,
            background=self.CLR_ACCENT_GREEN)
        mpl.rcParams['font.family'] = 'Segoe UI'

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

        right_panel_container = tk.Frame(self.main_pane, bg='white')
        self.main_pane.add(right_panel_container, weight=1)

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
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
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

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)
        self.create_graph_frame(right_panel_container)

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
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')

        # --- Plotter Launch Button (packed first to be on the far right) ---
        plotter_button = ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Button (packed second to be to the left of the plotter) ---
        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="Keithley 2400: I-V Measurement",
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
        frame = LabelFrame(
            parent,
            text='Information',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.grid(
            row=0,
            column=0,
            rowspan=4,
            padx=15,
            pady=15,
            sticky='ns')

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE):
            try:
                img = Image.open(
                    self.LOGO_FILE).resize(
                    (self.LOGO_SIZE,
                     self.LOGO_SIZE),
                    Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception as e:
                self.log(f"WARNING: Could not process logo file: {e}")
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    text="LOGO\nERROR",
                    font=self.FONT_BASE,
                    fill="white",
                    justify='center')
        else:
            self.log(
                f"WARNING: Logo file '{self.LOGO_FILE}' not found or Pillow not installed.")
            logo_canvas.create_text(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                text="LOGO\nMISSING",
                font=self.FONT_BASE,
                fill="white",
                justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
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
        details_text = ("Program Name: I-V Sweep (4-Probe)\n"
                        "Instrument: Keithley 2400\n"
                        "Measurement Range: 100 µΩ to 200 MΩ")
        ttk.Label(
            frame,
            text=details_text,
            justify='left',
            background=self.CLR_BG_DARK).grid(
            row=3,
            column=1,
            padx=10,
            pady=(
                0,
                10),
            sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Sweep Parameters',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        self.entries = {}
        grid = ttk.Frame(frame)
        grid.pack(padx=10, pady=10, fill='x')
        grid.grid_columnconfigure((0, 1, 2), weight=1)

        ttk.Label(
            grid,
            text="Sample Name:").grid(
            row=0,
            column=0,
            columnspan=3,
            sticky='w')
        self.entries["Sample Name"] = Entry(
            grid, font=self.FONT_BASE, width=20)
        self.entries["Sample Name"].grid(
            row=1,
            column=0,
            columnspan=3,
            sticky='ew',
            pady=(
                0,
                10))

        ttk.Label(
            grid,
            text="Max Current (µA):").grid(
            row=2,
            column=0,
            sticky='w')
        self.entries["Max Current"] = Entry(
            grid, font=self.FONT_BASE, width=10)
        self.entries["Max Current"].grid(
            row=3, column=0, sticky='ew', padx=(0, 5))
        ttk.Label(
            grid,
            text="Step Current (µA):").grid(
            row=2,
            column=1,
            sticky='w')
        self.entries["Step Current"] = Entry(
            grid, font=self.FONT_BASE, width=10)
        self.entries["Step Current"].grid(
            row=3, column=1, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Loops:").grid(row=2, column=2, sticky='w')
        self.entries["Num Loops"] = Entry(grid, font=self.FONT_BASE, width=5)
        self.entries["Num Loops"].grid(row=3, column=2, sticky='ew')
        self.entries["Num Loops"].insert(0, "1")

        ttk.Label(
            grid,
            text="Compliance (V):").grid(
            row=4,
            column=0,
            columnspan=2,
            sticky='w',
            pady=(
                10,
                0))
        self.entries["Compliance"] = Entry(grid, font=self.FONT_BASE, width=10)
        self.entries["Compliance"].grid(
            row=5,
            column=0,
            columnspan=2,
            sticky='ew',
            padx=(
                0,
                5))
        ttk.Label(
            grid,
            text="Delay (s):").grid(
            row=4,
            column=2,
            sticky='w',
            pady=(
                10,
                0))
        self.entries["Delay"] = Entry(grid, font=self.FONT_BASE, width=5)
        self.entries["Delay"].grid(row=5, column=2, sticky='ew')
        self.entries["Delay"].insert(0, "0.1")

        ttk.Label(
            grid,
            text="Sweep Type:").grid(
            row=6,
            column=0,
            columnspan=3,
            sticky='w',
            pady=(
                10,
                0))
        # Explicit master: a StringVar without one binds to Tk's default
        # root, which may belong to another window in the same process.
        self.sweep_type_var = tk.StringVar(master=self.root)
        self.sweep_type_cb = ttk.Combobox(
            grid,
            textvariable=self.sweep_type_var,
            state='readonly',
            font=self.FONT_BASE,
            values=list(SWEEP_TYPES))
        self.sweep_type_cb.grid(
            row=7,
            column=0,
            columnspan=3,
            sticky='ew',
            pady=(
                0,
                10))
        self.sweep_type_cb.set(SWEEP_ZERO_TO_MAX)
        self.sweep_type_cb.bind(
            "<<ComboboxSelected>>",
            self._on_sweep_type_change)

        self.custom_list_label = ttk.Label(
            grid, text="Custom Current List (µA):")
        self.custom_list_label.grid(
            row=8,
            column=0,
            columnspan=3,
            sticky='w',
            pady=(
                10,
                0))
        self.custom_list_hint = ttk.Label(
            grid,
            style='Hint.TLabel',
            text=("Separate values with commas, spaces or new lines. "
                  "Points are sourced in the order written, e.g.\n"
                  "0, 1, 2, 5, 10, 5, 2, 1, 0, -1, -2, -5, -10, -5, -2, -1, 0"),
            wraplength=self.LEFT_PANEL_WIDTH - 60,
            justify='left')
        self.custom_list_hint.grid(
            row=9, column=0, columnspan=3, sticky='w')
        self.custom_list_text = scrolledtext.ScrolledText(
            grid, height=5, font=self.FONT_BASE, wrap='word')
        self.custom_list_text.grid(row=10, column=0, columnspan=3, sticky='ew')

        ttk.Label(
            grid,
            text="Keithley 2400 VISA:").grid(
            row=11,
            column=0,
            columnspan=3,
            sticky='w',
            pady=(10, 0))
        self.keithley_combobox = ttk.Combobox(
            grid, font=self.FONT_BASE, state='readonly', width=20)
        self.keithley_combobox.grid(
            row=12,
            column=0,
            columnspan=3,
            sticky='ew',
            pady=(
                0,
                10))

        button_grid = ttk.Frame(frame)
        button_grid.pack(padx=10, pady=5, fill='x')
        button_grid.grid_columnconfigure((0, 1), weight=1)
        self.scan_button = ttk.Button(
            button_grid,
            text="Scan Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.file_location_button = ttk.Button(
            button_grid,
            text="Save Location...",
            command=self._browse_file_location)
        self.file_location_button.grid(row=0, column=1, sticky='ew')

        bf = ttk.Frame(frame)
        bf.pack(padx=10, pady=10, fill='x')
        bf.grid_columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(
            bf,
            text="Start",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.stop_button = ttk.Button(
            bf,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(row=0, column=1, sticky='ew')

        self.progress_bar = ttk.Progressbar(
            frame, orient='horizontal', mode='determinate')
        self.progress_bar.pack(padx=10, pady=(5, 10), fill='x')

    def _on_sweep_type_change(self, event=None):
        if not hasattr(self, 'sweep_type_var'):
            return

        selection = self.sweep_type_var.get()
        standard_sweep_entries = [
            self.entries["Max Current"],
            self.entries["Step Current"]]

        if selection == SWEEP_CUSTOM:
            self.custom_list_label.grid()
            self.custom_list_hint.grid()
            self.custom_list_text.grid()
            # Show the worked example the first time the box appears so the
            # expected format is obvious; the user overwrites it freely.
            try:
                if not self.custom_list_text.get("1.0", tk.END).strip():
                    self.custom_list_text.insert("1.0", CUSTOM_LIST_EXAMPLE)
            except tk.TclError:
                pass
            for entry in standard_sweep_entries:
                entry.config(state='disabled')
        else:
            self.custom_list_label.grid_remove()
            self.custom_list_hint.grid_remove()
            self.custom_list_text.grid_remove()
            for entry in standard_sweep_entries:
                entry.config(state='normal')

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Console Output',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=(
                'Consolas', 10), wrap='word', bd=0)
        self.console_widget.pack(
            pady=5,
            padx=5,
            fill='both',
            expand=True,
            side='bottom')

        if self.pre_init_logs:
            self.console_widget.config(state='normal')
            for msg in self.pre_init_logs:
                self.console_widget.insert('end', msg)
            self.console_widget.see('end')
            self.console_widget.config(state='disabled')
            self.pre_init_logs = []

        self.log("Console initialized.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(
            parent,
            text='Live I-V Curve',
            relief='groove',
            bg='white',
            fg=self.CLR_BG_DARK,
            font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100, constrained_layout=True)
        self.ax_vi, self.ax_ri = self.figure.subplots(2, 1, sharex=True)

        self.ax_vi.grid(True, linestyle='--', alpha=0.7)
        self.ax_vi.axhline(
            0,
            color='k',
            linestyle='--',
            linewidth=0.7,
            alpha=0.5)
        self.line_main, = self.ax_vi.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_vi.set_title("Voltage vs. Current", fontweight='bold')
        self.ax_vi.set_ylabel("Voltage (V)")

        self.ax_ri.grid(True, linestyle='--', alpha=0.7)
        self.line_resistance, = self.ax_ri.plot(
            [], [], color=self.CLR_ACCENT_GREEN, marker='o', markersize=4, linestyle='-')
        self.ax_ri.set_title("Resistance vs. Current", fontweight='bold')
        self.ax_ri.set_xlabel("Current (A)")
        self.ax_ri.set_ylabel("Resistance (Ω)")
        self.ax_ri.set_yscale('log')

        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"
        if hasattr(self, 'console_widget'):
            self.console_widget.config(state='normal')
            self.console_widget.insert('end', log_line)
            self.console_widget.see('end')
            self.console_widget.config(state='disabled')
        else:
            self.pre_init_logs.append(log_line)

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
        """Run state in the window title and the figure title. Never raises."""
        try:
            self.root.title(f"{self.BASE_TITLE} -- {text}" if text else self.BASE_TITLE)
            sample = self.params.get('sample_name', '')
            self.figure.suptitle(
                f"Sample: {sample}  |  {text}" if text else f"Sample: {sample}",
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
            'visa_address': self.keithley_combobox.get(),
            'save_path': self.file_location_path,
            'max_current_uA': 0.0,
            'step_current_uA': 0.0,
            'custom_list_str': '',
        }
        if not params['sample_name']:
            raise ValueError("Sample Name is required.")
        if not params['visa_address']:
            raise ValueError("Select the Keithley 2400 VISA address (Scan Instruments).")
        if not params['save_path']:
            raise ValueError("Choose a Save Location first.")
        if not os.path.isdir(params['save_path']):
            raise ValueError(f"Save Location does not exist:\n{params['save_path']}")

        try:
            params['num_loops'] = int(self.entries["Num Loops"].get().strip())
        except ValueError:
            raise ValueError("Loops must be a whole number (1 or more).")
        try:
            params['compliance_v'] = float(self.entries["Compliance"].get().strip())
        except ValueError:
            raise ValueError("Compliance (V) must be a number.")
        if not (0 < params['compliance_v'] <= K2400_MAX_COMPLIANCE_V):
            raise ValueError(
                f"Compliance must be between 0 and {K2400_MAX_COMPLIANCE_V:g} V.")
        try:
            params['delay_s'] = float(self.entries["Delay"].get().strip())
        except ValueError:
            raise ValueError("Delay (s) must be a number.")
        if params['delay_s'] < 0:
            raise ValueError("Delay (s) cannot be negative.")

        custom_values = None
        if sweep_type == SWEEP_CUSTOM:
            params['custom_list_str'] = self.custom_list_text.get("1.0", tk.END)
            custom_values = parse_custom_list(params['custom_list_str'])
        else:
            try:
                params['max_current_uA'] = float(self.entries["Max Current"].get().strip())
                params['step_current_uA'] = float(self.entries["Step Current"].get().strip())
            except ValueError:
                raise ValueError("Max Current and Step Current (µA) must be numbers.")
            if params['max_current_uA'] == 0:
                raise ValueError("Max Current must not be zero.")
            if params['step_current_uA'] <= 0:
                raise ValueError("Step Current must be greater than zero.")

        points_uA = build_sweep_points(
            sweep_type,
            max_val=params['max_current_uA'],
            step_val=params['step_current_uA'],
            num_loops=params['num_loops'],
            custom_values=custom_values)
        points_A = points_uA * 1e-6
        check_sweep_limits(points_A, K2400_MAX_CURRENT_A, "A")
        params['max_abs_current_A'] = float(np.max(np.abs(points_A)))
        return params, points_A

    def _write_file_header(self, params, n_points):
        with open(self.data_filepath, 'w', newline='', encoding='utf-8') as f:
            f.write(f"# Program: Keithley 2400 I-V Sweep v{self.PROGRAM_VERSION}\n")
            f.write(f"# Sample: {params['sample_name']}\n")
            f.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Instrument: Keithley 2400 at {params['visa_address']} (source I, measure V, front terminals)\n")
            f.write(f"# Sweep type: {ascii_label(params['sweep_type'])}\n")
            if params['sweep_type'] == SWEEP_CUSTOM:
                f.write(f"# Custom list (uA): {' '.join(params['custom_list_str'].split())}\n")
            else:
                f.write(f"# Max current: {params['max_current_uA']:g} uA, "
                        f"step: {params['step_current_uA']:g} uA\n")
            f.write(f"# Loops: {params['num_loops']}, points: {n_points}\n")
            f.write(f"# Compliance: {params['compliance_v']:g} V, delay: {params['delay_s']:g} s\n")
            f.write("# Compliance column: 1 = voltage compliance reached at that point\n")
            writer = csv.writer(f, delimiter='\t')
            writer.writerow(
                ["Current (A)", "Voltage (V)", "Resistance (Ohm)", "Compliance"])

    def _append_row(self, current, voltage, resistance, in_compliance):
        with open(self.data_filepath, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f, delimiter='\t').writerow(
                [f"{current:.8e}", f"{voltage:.8e}", f"{resistance:.8e}",
                 "1" if in_compliance else "0"])
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
        try:
            params, points = self._collect_params()
        except Exception as e:
            self.log(f"Cannot start: {e}")
            messagebox.showerror("Check the parameters", f"{e}")
            return

        try:
            self.params = params
            self.sweep_points = points
            self.log(f"Sweep type '{params['sweep_type']}': {len(points)} points, "
                     f"|I|max = {params['max_abs_current_A'] * 1e6:g} µA, "
                     f"compliance {params['compliance_v']:g} V.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_IV.dat"
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
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        for key in self.data_storage:
            self.data_storage[key].clear()

        self.line_main.set_data([], [])
        self.line_resistance.set_data([], [])
        self.progress_bar['value'] = 0
        self.progress_bar['maximum'] = len(points)
        self._set_banner("RUNNING")
        self.log("Measurement sweep started.")

        self.measurement_thread = threading.Thread(
            target=self._measurement_worker,
            args=(params, points),
            daemon=True)
        self.measurement_thread.start()
        self._pump_after_id = self.root.after(100, self._process_data_queue)

    def stop_measurement(self):
        """Ask the worker to stop. The worker finishes the point in hand,
        ramps the source to zero and switches the output off itself; the
        GUI thread never touches the instrument."""
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
    def _measurement_worker(self, params, points):
        q = self.data_queue
        outcome = "finished"
        try:
            q.put(("LOG", f"Connecting to {params['visa_address']}..."))
            idn = self.backend.connect_and_configure(
                params['visa_address'],
                params['max_abs_current_A'],
                params['compliance_v'])
            q.put(("LOG", f"Connected: {idn}"))
            q.put(("LOG", "Output ON at 0 A. Sweeping..."))

            delay = params['delay_s']
            n = len(points)
            for i, current in enumerate(points):
                if self.stop_event.is_set():
                    outcome = "stopped"
                    break
                # Event.wait returns True as soon as Stop is pressed, so a
                # long settling delay never holds the output on.
                result = self.backend.measure_at_current(
                    float(current), delay, wait=self.stop_event.wait)
                if result is None:
                    outcome = "stopped"
                    break
                voltage, tripped = result
                q.put(("DATA", i, n, float(current), voltage, tripped))
        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            q.put(("ERROR", tb))
            outcome = "error"
        finally:
            q.put(("LOG", "Ramping to 0 A and switching the output OFF..."))
            self.backend.shutdown()
            q.put(("DONE", outcome))

    # ------------------------------------------------------------------
    # GUI-thread queue pump
    # ------------------------------------------------------------------
    def _process_data_queue(self):
        self._pump_after_id = None
        finished = False
        try:
            while True:
                item = self.data_queue.get_nowait()
                kind = item[0]
                if kind == "LOG":
                    self.log(item[1])
                elif kind == "DATA":
                    _, i, n, current, voltage, tripped = item
                    self._handle_point(i, n, current, voltage, tripped)
                elif kind == "ERROR":
                    self.log(f"RUNTIME ERROR in worker thread:\n{item[1]}")
                elif kind == "DONE":
                    self._finish_run(item[1])
                    finished = True
        except queue.Empty:
            pass

        if self.is_running and not finished:
            self._pump_after_id = self.root.after(100, self._process_data_queue)

    def _handle_point(self, i, n, current, voltage, tripped):
        if tripped:
            self.log(f"WARNING: voltage compliance reached at {current:.4e} A "
                     "- check the sample connections.")
        resistance = voltage / current if current != 0 else float('nan')
        self.data_storage['current'].append(current)
        self.data_storage['voltage'].append(voltage)
        self.data_storage['resistance'].append(resistance)
        try:
            self._append_row(current, voltage, resistance, tripped)
        except Exception as e:
            self.log(f"ERROR writing data file: {e}")
        self.log(f"  {i + 1}/{n}: I = {current:.4e} A, V = {voltage:.6e} V, "
                 f"R = {resistance:.4e} Ω")
        self._update_plots()
        self.progress_bar['value'] = i + 1

    def _update_plots(self):
        i_data = np.array(self.data_storage['current'], dtype=float)
        v_data = np.array(self.data_storage['voltage'], dtype=float)
        r_data = np.array(self.data_storage['resistance'], dtype=float)

        ok_v = np.isfinite(i_data) & np.isfinite(v_data)
        self.line_main.set_data(i_data[ok_v], v_data[ok_v])
        # log y-axis: keep finite, positive resistances only
        ok_r = np.isfinite(i_data) & np.isfinite(r_data) & (r_data > 0)
        self.line_resistance.set_data(i_data[ok_r], r_data[ok_r])

        for ax in (self.ax_vi, self.ax_ri):
            ax.relim()
            ax.autoscale_view()
        self.canvas.draw_idle()

    def _finish_run(self, outcome):
        self.is_running = False
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        n_pts = len(self.data_storage['current'])
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
        if pyvisa is None or self.backend.rm is None:
            self.log("ERROR: PyVISA not found or NI-VISA backend is missing.")
            return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_combobox['values'] = resources
                default_k2400_addr = 'GPIB1::4::INSTR'
                if default_k2400_addr in resources:
                    self.keithley_combobox.set(default_k2400_addr)
                else:
                    for res in resources:
                        if "::4::" in res or "24" in res:
                            self.keithley_combobox.set(res)
                            break
                    if not self.keithley_combobox.get() and resources:
                        self.keithley_combobox.set(resources[0])
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
            if not messagebox.askyesno(
                    "Exit", "Measurement is running. Stop and exit?"):
                return
            # Let the worker take the output off; it owns the instrument.
            self.stop_event.set()
            t = self.measurement_thread
            if t is not None and t.is_alive():
                t.join(timeout=15)
            try:
                if self._pump_after_id is not None:
                    self.root.after_cancel(self._pump_after_id)
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    MeasurementAppGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
