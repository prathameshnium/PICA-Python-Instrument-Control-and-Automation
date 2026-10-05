"""
Module: IV_K2400_K2182_GUI.py
Purpose: GUI module for IV K2400 K2182 GUI v3.

v3.0 (05 Oct 2026) - sweep types, verified logic, hardened run path
  * Sweep types: "Start → Stop (linear)" (the original behaviour, still
    the default), "0 to Max", "Loop (0 → Max → 0 → -Max → 0)" and
    "Custom List" (mA, with a worked example shown in the box). Same
    generator as IV_K2400_GUI.py and IV_K6517B_GUI.py.
  * FIX: a Start Current of 0 mA was rejected as "All fields must be
    filled" because 0.0 is falsy; only the text fields are checked now.
  * FIX: the 2400 source range was set from the Stop current alone, so a
    sweep from -5 mA to +1 mA asked for 5 mA on a 1 mA range. The range
    now follows the largest |set-point| of the whole sweep.
  * FIX: the step sign is taken from the direction (Start -> Stop), so a
    positive step with Start > Stop no longer yields an empty sweep.
  * Compliance is reported per point via :SENS:VOLT:PROT:TRIP? and a
    resistance column (V / I) is written next to the raw readings.
  * One worker thread owns both instruments from connect to shutdown on
    every exit path (finished, Stop, error): Stop no longer calls into a
    VISA session the measurement thread is still using.
  * No modal dialog during or after a run: completion and errors go to the
    console, the title banner and a beep.
"""

# -------------------------------------------------------------------------------
# Name:         IV Sweep GUI for Keithley 2400/2182
# Purpose:      Provide a professional GUI for performing I-V sweeps using a
#               Keithley 2400 as a current source and a Keithley 2182
#               as a nanovoltmeter.
# Author:       Prathamesh Deshmukh
# Created:      04/10/2025
# Version:      1.0
# -------------------------------------------------------------------------------

# --- GUI and Plotting Packages ---
import tkinter as tk
from tkinter import Canvas
from tkinter import ttk, filedialog, messagebox, scrolledtext
import numpy as np
import os
import time
import traceback
import csv
import threading
import queue
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

# --- Instrument Control Packages ---
try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley2400
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa, Keithley2400 = None, None
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
        # Go up 2 levels: k2400_2182 -> keithley -> pica
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
        # Go up 2 levels: k2400_2182 -> keithley -> pica
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

# ===============================================================================
# SWEEP GENERATION  (pure functions; inlined so the module stays standalone.
# The same code is carried by IV_K2400_GUI.py and IV_K6517B_GUI.py.)
# ===============================================================================

SWEEP_LINEAR = "Start → Stop (linear)"
SWEEP_ZERO_TO_MAX = "0 to Max"
SWEEP_LOOP = "Loop (0 → Max → 0 → -Max → 0)"
SWEEP_CUSTOM = "Custom List"
SWEEP_TYPES = (SWEEP_LINEAR, SWEEP_ZERO_TO_MAX, SWEEP_LOOP, SWEEP_CUSTOM)

# Keithley 2400: 1.05 A is the full scale of the 1 A source range
# (manual Table 18-63 / :SOUR:CURR:RANG, -1.05 to 1.05 A).
K2400_MAX_CURRENT_A = 1.05
K2400_MAX_COMPLIANCE_V = 210.0

# Shown inside the custom-list box the first time "Custom List" is chosen,
# so the expected format is visible without reading a manual. It is a valid
# list (mA): a full loop with fine steps near zero and coarse steps at the top.
CUSTOM_LIST_EXAMPLE = (
    "0, 0.1, 0.2, 0.5, 1, 2, 5, 10,\n"
    "5, 2, 1, 0.5, 0.2, 0.1, 0,\n"
    "-0.1, -0.2, -0.5, -1, -2, -5, -10,\n"
    "-5, -2, -1, -0.5, -0.2, -0.1, 0")


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


def _linear_segment(start_val, stop_val, step_val):
    """start, start+step ... up to and INCLUDING stop (direction from the
    sign of stop - start; step is a magnitude). A single point when
    start == stop."""
    step = abs(float(step_val))
    a, b = float(start_val), float(stop_val)
    if step <= 0:
        raise ValueError("Step must be greater than zero.")
    if a == b:
        return [a]
    span = b - a
    sign = 1.0 if span > 0 else -1.0
    n_full = int(np.floor(abs(span) / step + 1e-9))
    points = [a + sign * k * step for k in range(n_full + 1)]
    if abs(points[-1] - a) < abs(span) * (1 - 1e-9):
        points.append(b)
    else:
        points[-1] = b
    return points


def build_sweep_points(sweep_type, max_val=0.0, step_val=0.0, num_loops=1,
                       custom_values=None, start_val=0.0, stop_val=0.0):
    """Return the ordered list of source set-points for one run.

    sweep_type   one of SWEEP_TYPES
    max_val      end value for "0 to Max" / turning value for "Loop"
    step_val     step magnitude ("0 to Max", "Loop" and "linear")
    num_loops    how many times the whole pattern is repeated (>= 1)
    custom_values  list of floats for "Custom List" (already parsed)
    start_val, stop_val   for "Start → Stop (linear)"

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
        base = _linear_segment(start_val, stop_val, step_val)
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


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------


class IV_Backend:
    """ Manages communication with the Keithley 2400 and 2182.
    Every method is called from the worker thread only; the GUI thread
    never touches the instruments. """

    def __init__(self):
        self.k2400, self.k2182 = None, None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None

    def connect(self, k2400_visa, k2182_visa, log=print):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        if not PYMEASURE_AVAILABLE:
            raise ImportError("Pymeasure is not available.")
        self.k2400 = Keithley2400(k2400_visa)
        log(f"  K2400 Connected: {self.k2400.id}")
        self.k2182 = self.rm.open_resource(k2182_visa)
        log(f"  K2182 Connected: {self.k2182.query('*IDN?').strip()}")

    def configure_instruments(self, compliance_v, current_range_a):
        """current_range_a is the largest |set-point| of the whole sweep
        (:SOUR:CURR:RANG picks the lowest range that accommodates it)."""
        # Keithley 2400 setup: source I; the 2182 does the measuring.
        self.k2400.reset()
        self.k2400.apply_current()
        rng = abs(float(current_range_a))
        self.k2400.source_current_range = rng if rng > 0 else 1e-6
        self.k2400.compliance_voltage = float(compliance_v)
        self.k2400.source_current = 0
        self.k2400.enable_source()

        # Keithley 2182 setup
        self.k2182.write("*rst; status:preset; *cls")
        time.sleep(1)

    def set_current(self, current_a):
        self.k2400.ramp_to_current(float(current_a), steps=10, pause=0.05)

    def read_voltage(self):
        """One 2182 reading (mean of 2 samples, bus-triggered, SRQ-paced)
        plus the 2400 compliance flag. Sequence identical to the R-T
        modules of this family."""
        # K2182 measurement sequence
        self.k2182.write("status:measurement:enable 512; *sre 1")
        self.k2182.write("sample:count 2")
        self.k2182.write("trigger:source bus")
        self.k2182.write("trigger:delay 0.1")
        self.k2182.write("trace:points 2")
        self.k2182.write("trace:feed sense1; feed:control next")
        self.k2182.write("initiate")
        self.k2182.assert_trigger()
        self.k2182.wait_for_srq(timeout=10)
        voltages = self.k2182.query_ascii_values("trace:data?")
        self.k2182.query("status:measurement?")
        self.k2182.write("trace:clear; feed:control next")

        voltage = sum(voltages) / len(voltages) if voltages else float('nan')
        tripped = str(self.k2400.ask(":SENS:VOLT:PROT:TRIP?")).strip()
        return float(voltage), tripped.startswith("1")

    def measure_voltage_at_current(self, current_a, delay_s, wait=time.sleep):
        """Ramp, settle (interruptible: wait returning True aborts and
        returns None), then read. Returns (voltage, in_compliance)."""
        self.set_current(current_a)
        if delay_s > 0 and wait(delay_s):
            return None
        return self.read_voltage()

    def shutdown(self, log=print):
        """Ramp the 2400 to 0 A and output OFF, reset and close the 2182.
        Never raises."""
        k2400, self.k2400 = self.k2400, None
        k2182, self.k2182 = self.k2182, None
        if k2400 is not None:
            try:
                k2400.shutdown()
            except Exception as e:
                log(f"  Warning: K2400 shutdown error: {e}")
                try:
                    k2400.write("OUTPUT OFF")
                except Exception:
                    pass
        if k2182 is not None:
            try:
                k2182.write("*rst")
                k2182.close()
            except Exception:
                pass
        if k2400 is not None or k2182 is not None:
            log("  Instruments shut down and disconnected.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class IV_GUI:
    PROGRAM_VERSION = "3.0"
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_BLUE = '#B68B6E', '#BA6B5E', '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    LEFT_PANEL_WIDTH = 480  # default sash position so the left panel starts fully visible
    FONT_HINT = ('Segoe UI', 9, 'italic')

    def __init__(self, root):
        self.root = root
        self.BASE_TITLE = f"I-V Sweep (K2400 + K2182) v{self.PROGRAM_VERSION}"
        self.root.title(self.BASE_TITLE)
        self.root.geometry("1650x950")
        self.root.minsize(1400, 800)
        self.root.configure(bg=self.CLR_BG_DARK)
        self.is_running = False
        self.logo_image = None
        self.backend = IV_Backend()
        self.data_storage = {'current': [], 'voltage': [], 'resistance': []}
        self.params = {}
        self.current_points = np.array([])
        self.data_filepath = None
        self.stop_event = threading.Event()
        self.measurement_thread = None
        self._pump_after_id = None
        self.setup_styles()
        self.result_queue = queue.Queue()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._on_sweep_type_change()

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('Hint.TLabel', background=self.CLR_FRAME_BG, font=self.FONT_HINT)
        style.configure(
            'TEntry',
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG_LIGHT,
            insertcolor=self.CLR_FG_LIGHT)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER)
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_BG_DARK), ('hover', self.CLR_BG_DARK)])
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
        # --- NEW: Style for the Browse button ---
        style.configure(
            'Browse.TButton',
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_ACCENT_BLUE)
        style.map(
            'Browse.TButton', background=[
                ('active', '#7C899E'), ('hover', '#7C899E')])
        style.configure(
            'TLabelframe',
            background=self.CLR_FRAME_BG,
            bordercolor=self.CLR_ACCENT_BLUE)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        mpl.rcParams.update({'font.family': 'Segoe UI',
                             'font.size': 11,
                             'axes.titlesize': 15,
                             'axes.labelsize': 13})

    def create_widgets(self):
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        ttk.Label(
            header,
            text="I-V Sweep (K2400 + K2182)",
            style='Header.TLabel',
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left',
            padx=20,
            pady=10)
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane = main_pane
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(main_pane)
        left_panel_container.pack_propagate(False)
        # Give more weight to controls
        main_pane.add(left_panel_container, weight=0)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        # This is now the scrollable_frame
        left_panel = ttk.Frame(canvas, padding=5)
        left_panel.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=left_panel, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        # Keep the inner frame exactly as wide as the canvas viewport so
        # widgets are never clipped on the right edge (they reflow instead).
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = left_panel
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        right_panel = self._create_right_panel(main_pane)
        main_pane.add(right_panel, weight=1)
        self._populate_left_panel(left_panel)

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

    def _populate_left_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)
        self._create_info_panel(panel, 0)
        self._create_params_panel(panel, 1)
        self._create_control_panel(panel, 2)
        self._create_console_panel(panel, 3)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 110
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        try:  # Use a more robust relative path
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir,
                "..",
                "..",
                "assets",
                "LOGO",
                "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception as e:
            self.log(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 6, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=0,
            column=1,
            padx=10,
            pady=(
                15,
                0),
            sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=1,
            column=1,
            padx=10,
            pady=(
                0,
                5),
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
                        "Instruments: Keithley 2400, Keithley 2182\n"
                        "Measurement Range: 1 µΩ to 100 MΩ")
        ttk.Label(
            frame,
            text=details_text,
            justify='left',
            background=self.CLR_FRAME_BG).grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        container = ttk.LabelFrame(panel, text='Live I-V Curve')
        container.pack(fill='both', expand=True)
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_main = self.figure.add_subplot(111)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Waiting for experiment...", fontweight='bold')
        self.ax_main.set_xlabel("Voltage (V)")
        self.ax_main.set_ylabel("Current (A)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _create_params_panel(self, parent, grid_row):
        container = ttk.Frame(parent)
        container.grid(row=grid_row, column=0, sticky='new', pady=5)
        container.grid_columnconfigure(0, weight=1)
        self.entries = {}

        sweep_frame = ttk.LabelFrame(container, text='Sweep Parameters')
        sweep_frame.grid(row=0, column=0, sticky='nsew', pady=(0, 5))
        sweep_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(sweep_frame, text="Sweep Type:").grid(
            row=0, column=0, sticky='w', padx=10, pady=3)
        # Explicit master: a StringVar without one binds to Tk's default
        # root, which may belong to another window in the same process.
        self.sweep_type_var = tk.StringVar(master=self.root)
        self.sweep_type_cb = ttk.Combobox(
            sweep_frame,
            textvariable=self.sweep_type_var,
            state='readonly',
            font=self.FONT_BASE,
            values=list(SWEEP_TYPES))
        self.sweep_type_cb.grid(
            row=0, column=1, sticky='ew', padx=10, pady=3, columnspan=2)
        self.sweep_type_cb.set(SWEEP_LINEAR)
        self.sweep_type_cb.bind("<<ComboboxSelected>>", self._on_sweep_type_change)

        # Row widgets are remembered per sweep type so the panel only shows
        # the entries the chosen type uses.
        self._rows = {}
        self._rows["Start Current (mA)"] = self._create_entry(sweep_frame, "Start Current (mA)", "-1", 1)
        self._rows["Stop Current (mA)"] = self._create_entry(sweep_frame, "Stop Current (mA)", "1", 2)
        self._rows["Max Current (mA)"] = self._create_entry(sweep_frame, "Max Current (mA)", "1", 3)
        self._rows["Step Current (mA)"] = self._create_entry(sweep_frame, "Step Current (mA)", "0.1", 4)

        self.custom_list_label = ttk.Label(sweep_frame, text="Custom Current List (mA):")
        self.custom_list_label.grid(row=5, column=0, columnspan=3, sticky='w', padx=10, pady=(6, 0))
        self.custom_list_hint = ttk.Label(
            sweep_frame,
            style='Hint.TLabel',
            text=("Separate values with commas, spaces or new lines. "
                  "Points are sourced in the order written, e.g.\n"
                  "0, 0.1, 0.2, 0.5, 1, 0.5, 0.2, 0.1, 0, -0.1, -0.2, -0.5, -1, -0.5, -0.2, -0.1, 0"),
            wraplength=self.LEFT_PANEL_WIDTH - 70,
            justify='left')
        self.custom_list_hint.grid(row=6, column=0, columnspan=3, sticky='w', padx=10)
        self.custom_list_text = scrolledtext.ScrolledText(
            sweep_frame, height=5, font=self.FONT_BASE, wrap='word')
        self.custom_list_text.grid(row=7, column=0, columnspan=3, sticky='ew', padx=10, pady=(0, 6))

        self._create_entry(sweep_frame, "Loops", "1", 8)
        self._create_entry(sweep_frame, "Compliance (V)", "10", 9)
        self._create_entry(sweep_frame, "Dwell Time (s)", "0.5", 10)

        visa_frame = ttk.LabelFrame(container, text='Instrument Addresses')
        visa_frame.grid(row=1, column=0, sticky='nsew')
        visa_frame.grid_columnconfigure(1, weight=1)
        self.k2400_cb = self._create_combobox(
            visa_frame, "Keithley 2400 VISA", 0)
        self.k2182_cb = self._create_combobox(
            visa_frame, "Keithley 2182 VISA", 1)

    def _on_sweep_type_change(self, event=None):
        """Show only the entries that the chosen sweep type uses."""
        if not hasattr(self, 'sweep_type_var'):
            return
        selection = self.sweep_type_var.get()
        visible = {
            "Start Current (mA)": selection == SWEEP_LINEAR,
            "Stop Current (mA)": selection == SWEEP_LINEAR,
            "Max Current (mA)": selection in (SWEEP_ZERO_TO_MAX, SWEEP_LOOP),
            "Step Current (mA)": selection in (SWEEP_LINEAR, SWEEP_ZERO_TO_MAX, SWEEP_LOOP),
        }
        for key, show in visible.items():
            for w in self._rows[key]:
                w.grid() if show else w.grid_remove()
        show_custom = selection == SWEEP_CUSTOM
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

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Experiment Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(0, weight=1)
        self._create_entry(frame, "Sample Name", "Sample_IV", 0)
        self._create_entry(frame, "Save Location", "", 1, browse=True)
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=4, sticky='ew', pady=5)
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(
            button_frame,
            text="Start",
            style='Start.TButton',
            command=self.start_experiment)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=5)
        self.stop_button = ttk.Button(
            button_frame,
            text="Stop",
            style='Stop.TButton',
            state='disabled',
            command=self.stop_experiment)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(
            button_frame,
            text="Scan",
            command=self._scan_for_visa).grid(
            row=0,
            column=2,
            sticky='ew',
            padx=5)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

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
        """Run state in the window title and the plot title. Never raises."""
        try:
            self.root.title(f"{self.BASE_TITLE} -- {text}" if text else self.BASE_TITLE)
            name = self.params.get('name', '')
            self.ax_main.set_title(
                f"I-V Curve: {name}  |  {text}" if text else f"I-V Curve: {name}",
                fontweight='bold')
            self.canvas.draw_idle()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------
    def start_experiment(self):
        if self.is_running:
            return
        try:
            params, points = self._validate_and_get_params()
        except Exception as e:
            self.log(f"Cannot start: {e}")
            messagebox.showerror("Check the parameters", f"{e}")
            return

        try:
            self.params = params
            self.current_points = points
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{params['name']}_{ts}_IV.csv"
            self.data_filepath = os.path.join(params['save_path'], filename)
            self._write_file_header(params, len(points))
            self.log(f"Output file created: {filename}")
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Start Failed", f"Could not create the data file.\n{e}")
            return

        self.stop_event.clear()
        self.set_ui_state(running=True)
        for key in self.data_storage:
            self.data_storage[key].clear()
        self.line_main.set_data([], [])
        self._set_banner("CONNECTING")
        self.log(
            f"Starting sweep '{params['sweep_type']}': {len(points)} points, "
            f"|I|max = {params['max_abs_current_A'] * 1e3:g} mA, "
            f"compliance {params['compliance_v']:g} V.")

        self.measurement_thread = threading.Thread(
            target=self._measurement_worker, args=(params, points), daemon=True)
        self.measurement_thread.start()
        self._pump_after_id = self.root.after(100, self._process_queue)

    def stop_experiment(self, reason=""):
        """Ask the worker to stop. It finishes the point in hand, ramps the
        2400 to zero and switches the output off itself; the GUI thread
        never touches the instruments."""
        if not self.is_running:
            return
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.log(f"Stopping... {reason}" if reason else
                     "Stop requested; finishing the current point and switching the output off...")
            self._set_banner("STOPPING")
        self.stop_button.config(state='disabled')

    def _write_file_header(self, params, n_points):
        with open(self.data_filepath, 'w', newline='', encoding='utf-8') as f:
            f.write(f"# Program: I-V Sweep (K2400 + K2182) v{self.PROGRAM_VERSION}\n")
            f.write(f"# Sample: {params['name']}\n")
            f.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Instruments: Keithley 2400 at {params['k2400_visa']} (source I), "
                    f"Keithley 2182 at {params['k2182_visa']} (measure V)\n")
            f.write(f"# Sweep type: {ascii_label(params['sweep_type'])}\n")
            t = params['sweep_type']
            if t == SWEEP_LINEAR:
                f.write(f"# Start: {params['start_mA']:g} mA, stop: {params['stop_mA']:g} mA, "
                        f"step: {params['step_mA']:g} mA\n")
            elif t == SWEEP_CUSTOM:
                f.write(f"# Custom list (mA): {' '.join(params['custom_list_str'].split())}\n")
            else:
                f.write(f"# Max: {params['max_mA']:g} mA, step: {params['step_mA']:g} mA\n")
            f.write(f"# Loops: {params['num_loops']}, points: {n_points}, "
                    f"compliance: {params['compliance_v']:g} V, dwell: {params['delay_s']:g} s\n")
            f.write("# Compliance column: 1 = voltage compliance reached at that point\n")
            writer = csv.writer(f)
            writer.writerow(["Current (A)", "Voltage (V)", "Resistance (Ohm)", "Compliance"])

    def _append_row(self, current, voltage, resistance, tripped):
        with open(self.data_filepath, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(
                [f"{current:.6e}", f"{voltage:.6e}", f"{resistance:.6e}",
                 "1" if tripped else "0"])
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Worker thread: owns both instruments from connect to shutdown
    # ------------------------------------------------------------------
    def _measurement_worker(self, params, points):
        q = self.result_queue
        outcome = "finished"
        log = lambda msg: q.put(("LOG", msg))
        try:
            log("Connecting to instruments...")
            self.backend.connect(params['k2400_visa'], params['k2182_visa'], log=log)
            self.backend.configure_instruments(
                params['compliance_v'], params['max_abs_current_A'])
            log("All instruments connected and configured. Output ON at 0 A.")
            q.put(("STATE", "RUNNING"))

            delay = params['delay_s']
            n = len(points)
            for i, current in enumerate(points):
                if self.stop_event.is_set():
                    outcome = "stopped"
                    break
                log(f"--- Setting current to {current:.3e} A ({i + 1}/{n}) ---")
                result = self.backend.measure_voltage_at_current(
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
            log("Ramping to 0 A and switching the output OFF...")
            self.backend.shutdown(log=log)
            q.put(("DONE", outcome))

    # ------------------------------------------------------------------
    # GUI-thread queue pump
    # ------------------------------------------------------------------
    def _process_queue(self):
        """Process messages from the measurement worker thread."""
        self._pump_after_id = None
        finished = False
        try:
            while True:
                item = self.result_queue.get_nowait()
                kind = item[0]
                if kind == "LOG":
                    self.log(item[1])
                elif kind == "STATE":
                    self._set_banner(item[1])
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
            self._pump_after_id = self.root.after(100, self._process_queue)

    def _handle_point(self, i, n, current, voltage, tripped):
        if tripped:
            self.log(f"WARNING: voltage compliance reached at {current:.4e} A "
                     "- check the sample connections.")
        resistance = voltage / current if current != 0 else float('nan')
        self.log(f"  Read: V = {voltage:.6e} V, R = {resistance:.4e} Ω")
        self.data_storage['current'].append(current)
        self.data_storage['voltage'].append(voltage)
        self.data_storage['resistance'].append(resistance)
        try:
            self._append_row(current, voltage, resistance, tripped)
        except Exception as e:
            self.log(f"ERROR writing data file: {e}")
        v = np.array(self.data_storage['voltage'], dtype=float)
        c = np.array(self.data_storage['current'], dtype=float)
        ok = np.isfinite(v) & np.isfinite(c)
        self.line_main.set_data(v[ok], c[ok])
        self.ax_main.relim()
        self.ax_main.autoscale_view()
        self.canvas.draw_idle()

    def _finish_run(self, outcome):
        self.set_ui_state(running=False)
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
    # Parameter validation (pre-run; a dialog here is acceptable)
    # ------------------------------------------------------------------
    def _validate_and_get_params(self):
        """Read and validate every entry. Returns (params, points_A).
        Raises ValueError with a message the user can act on. Nothing here
        touches the instruments."""
        sweep_type = self.sweep_type_var.get()
        params = {
            'name': self.entries["Sample Name"].get().strip(),
            'save_path': self.entries["Save Location"].get().strip(),
            'sweep_type': sweep_type,
            'k2400_visa': self.k2400_cb.get(),
            'k2182_visa': self.k2182_cb.get(),
            'start_mA': 0.0, 'stop_mA': 0.0, 'max_mA': 0.0, 'step_mA': 0.0,
            'custom_list_str': '',
        }
        if not params['name']:
            raise ValueError("Sample Name is required.")
        if not params['save_path']:
            raise ValueError("Choose a Save Location first.")
        if not os.path.isdir(params['save_path']):
            raise ValueError(f"Save Location does not exist:\n{params['save_path']}")
        if not params['k2400_visa'] or not params['k2182_visa']:
            raise ValueError("Select both VISA addresses (Scan).")
        if params['k2400_visa'] == params['k2182_visa']:
            raise ValueError("The 2400 and the 2182 cannot share one VISA address.")

        def _num(key, label):
            try:
                return float(self.entries[key].get().strip())
            except ValueError:
                raise ValueError(f"{label} must be a number.")

        try:
            params['num_loops'] = int(self.entries["Loops"].get().strip())
        except ValueError:
            raise ValueError("Loops must be a whole number (1 or more).")
        params['compliance_v'] = _num("Compliance (V)", "Compliance (V)")
        if not (0 < params['compliance_v'] <= K2400_MAX_COMPLIANCE_V):
            raise ValueError(
                f"Compliance must be between 0 and {K2400_MAX_COMPLIANCE_V:g} V.")
        params['delay_s'] = _num("Dwell Time (s)", "Dwell Time (s)")
        if params['delay_s'] < 0:
            raise ValueError("Dwell Time (s) cannot be negative.")

        custom_values = None
        if sweep_type == SWEEP_LINEAR:
            params['start_mA'] = _num("Start Current (mA)", "Start Current (mA)")
            params['stop_mA'] = _num("Stop Current (mA)", "Stop Current (mA)")
            params['step_mA'] = _num("Step Current (mA)", "Step Current (mA)")
            if params['step_mA'] <= 0:
                raise ValueError("Step Current must be greater than zero.")
        elif sweep_type in (SWEEP_ZERO_TO_MAX, SWEEP_LOOP):
            params['max_mA'] = _num("Max Current (mA)", "Max Current (mA)")
            params['step_mA'] = _num("Step Current (mA)", "Step Current (mA)")
            if params['max_mA'] == 0:
                raise ValueError("Max Current must not be zero.")
            if params['step_mA'] <= 0:
                raise ValueError("Step Current must be greater than zero.")
        elif sweep_type == SWEEP_CUSTOM:
            params['custom_list_str'] = self.custom_list_text.get("1.0", tk.END)
            custom_values = parse_custom_list(params['custom_list_str'])

        points_mA = build_sweep_points(
            sweep_type,
            max_val=params['max_mA'],
            step_val=params['step_mA'],
            num_loops=params['num_loops'],
            custom_values=custom_values,
            start_val=params['start_mA'],
            stop_val=params['stop_mA'])
        points_A = points_mA * 1e-3
        check_sweep_limits(points_A, K2400_MAX_CURRENT_A, "A")
        params['max_abs_current_A'] = float(np.max(np.abs(points_A)))
        return params, points_A

    def set_ui_state(self, running: bool):
        self.is_running = running
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for key, w in self.entries.items():
            if key == "Save Location":
                continue  # always read-only; filled by Browse...
            w.config(state=state)
        self.custom_list_text.config(state=state)
        self.sweep_type_cb.config(state='disabled' if running else 'readonly')
        for cb in [self.k2400_cb, self.k2182_cb]:
            cb.config(state='disabled' if running else 'readonly')
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}")
            self.k2400_cb['values'] = resources
            self.k2182_cb['values'] = resources
            for r in resources:
                if '2400' in r or 'GPIB::4' in r:
                    self.k2400_cb.set(r)
                if '2182' in r or 'GPIB::7' in r:
                    self.k2182_cb.set(r)
        else:
            self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.entries["Save Location"].config(state='normal')
            self.entries["Save Location"].delete(0, 'end')
            self.entries["Save Location"].insert(0, path)
            self.entries["Save Location"].config(state='disabled')

    def _create_entry(
            self,
            parent,
            label_text,
            default_value,
            row,
            browse=False):
        label = ttk.Label(parent, text=f"{label_text}:")
        label.grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        entry = ttk.Entry(parent, font=self.FONT_BASE, width=30)
        entry.grid(
            row=row,
            column=1,
            sticky='ew',
            padx=10,
            pady=3,
            columnspan=2)
        entry.insert(0, default_value)
        self.entries[label_text] = entry
        if browse:
            btn = ttk.Button(
                parent,
                text="Browse...",
                style='Browse.TButton',
                command=self._browse_file_location)
            btn.grid(row=row, column=3, sticky='e', padx=(0, 10))
            entry.config(state='disabled')
        return (label, entry)

    def _create_combobox(self, parent, label_text, row):
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        cb = ttk.Combobox(
            parent,
            font=self.FONT_BASE,
            state='readonly',
            style='TCombobox')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _on_closing(self):
        if self.is_running:
            if not messagebox.askyesno(
                    "Exit", "Experiment is running. Stop and exit?"):
                return
            # Let the worker take the output off; it owns the instruments.
            self.stop_event.set()
            t = self.measurement_thread
            if t is not None and t.is_alive():
                t.join(timeout=20)
            try:
                if self._pump_after_id is not None:
                    self.root.after_cancel(self._pump_after_id)
            except Exception:
                pass
        self.root.destroy()


if __name__ == '__main__':
    if not PYMEASURE_AVAILABLE:
        messagebox.showerror(
            "Dependency Error",
            "Pymeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")
    else:
        root = tk.Tk()
        app = IV_GUI(root)
        root.mainloop()
