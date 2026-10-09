"""
Purpose: GUI for performing current-voltage (I-V) sweeps using a Keithley 6221 Current Source and a Keithley 2182A Nanovoltmeter.

Author: Prathamesh Deshmukh
Date: October 2025
Version: 1.7

v1.7 (09 Oct 2026) - custom list + zero fixes
  * Sweep types: "Start → Stop (linear)", "Start → Stop (log)" and
    "Custom List". The point generator is a pure, testable function
    (build_sweep_points) shared in spirit with the other I-V modules; the
    custom list accepts commas, semicolons, spaces, tabs and new lines, so
    every rendering of utils/List_Maker_GUI.py pastes straight in.
  * Currents are entered in µA (Start/Stop and the custom list); the
    K6221 limit of 105 mA is checked BEFORE the instrument is touched.
  * FIX: Start validation used Python truthiness, so a Start or Stop
    Current of 0, or a 0 s delay, was refused with "All fields ... are
    required". Zero is now accepted wherever it is meaningful (linear and
    custom sweeps, delays); the log sweep still refuses a zero crossing.
  * FIX: the resistance at I = 0 was written as inf and fed to the plot.
    It is now NaN (as in the other I-V modules) and skipped when plotting.
  * Data-file header carries program, sweep type, list and parameters;
    written with an explicit encoding and ASCII-safe labels.
"""

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import numpy as np
import os
import sys
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl
import threading
import queue
import runpy
from multiprocessing import Process

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

try:
    # Dynamically find the project root and add it to the path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
    if project_root not in sys.path:
        sys.path.append(project_root)
except Exception:
    pass # Path manipulation can fail in some environments (e.g., frozen executables)

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)

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
        # Go up 2 levels: delta_mode -> keithley -> pica
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
        # Go up 2 levels: delta_mode -> keithley -> pica
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
# parse_custom_list / check_sweep_limits / ascii_label are the same code as
# in IV_K2400_GUI.py, IV_K2400_K2182_GUI.py and IV_K6517B_GUI.py, so a list
# made with utils/List_Maker_GUI.py reads identically in every I-V module.)
# ===============================================================================

SWEEP_LINEAR = "Start → Stop (linear)"
SWEEP_LOG = "Start → Stop (log)"
SWEEP_CUSTOM = "Custom List"
SWEEP_TYPES = (SWEEP_LINEAR, SWEEP_LOG, SWEEP_CUSTOM)

# Keithley 6221: 105 mA is the full scale of the 100 mA source range
# (SOUR:CURR -105e-3 to 105e-3 A); compliance 0.1 V to 105 V.
K6221_MAX_CURRENT_A = 0.105
K6221_MAX_COMPLIANCE_V = 105.0

# Shown inside the custom-list box the first time "Custom List" is chosen,
# so the expected format is visible without reading a manual. It is a valid
# list (µA): a full loop with fine steps near zero and coarse steps at the top.
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


def build_sweep_points(sweep_type, start_val=0.0, stop_val=0.0, num_points=2,
                       custom_values=None):
    """Return the ordered array of source set-points for one run.

    sweep_type     one of SWEEP_TYPES
    start_val, stop_val, num_points   for the two Start → Stop modes
    custom_values  list of floats for "Custom List" (already parsed)

    Linear: num_points points from start to stop inclusive; zero anywhere
    is fine. Log: points spaced evenly in log10(|I|); start and stop must
    be non-zero and of the same sign. Values come back in the unit they
    were given in; the caller applies check_sweep_limits.
    """
    if sweep_type == SWEEP_CUSTOM:
        if not custom_values:
            raise ValueError("Custom list is empty.")
        return np.asarray([float(v) for v in custom_values], dtype=float)

    if sweep_type not in (SWEEP_LINEAR, SWEEP_LOG):
        raise ValueError(f"Unknown sweep type '{sweep_type}'.")
    try:
        n = int(num_points)
    except (TypeError, ValueError):
        raise ValueError("Number of Points must be a whole number of 2 or more.")
    if n < 2:
        raise ValueError("Number of Points must be 2 or more.")
    a, b = float(start_val), float(stop_val)
    if not (np.isfinite(a) and np.isfinite(b)):
        raise ValueError("Start and Stop Current must be finite numbers.")

    if sweep_type == SWEEP_LINEAR:
        pts = np.linspace(a, b, n)
        pts[0], pts[-1] = a, b          # kill the float residue on the ends
        return pts

    if a == 0 or b == 0 or a * b < 0:
        raise ValueError("Log sweep cannot start at, stop at or cross zero. "
                         "Use a linear sweep or a Custom List instead.")
    pts = np.logspace(np.log10(abs(a)), np.log10(abs(b)), n) * np.sign(a)
    pts[0], pts[-1] = a, b
    return pts


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
class Backend_Passthrough:
    """ Manages K6221 and K2182 via GPIB passthrough communication. """
    def __init__(self):
        self.visa_queue = queue.Queue()
        self.k6221 = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")

    def connect(self, k6221_visa):
        if not self.rm: raise ConnectionError("VISA is not available.")
        self.k6221 = self.rm.open_resource(k6221_visa); self.k6221.timeout=25000
        print(f"  K6221 Connected: {self.k6221.query('*IDN?').strip()}")

    def configure_instruments(self, compliance):
        print("\n--- [Backend] Configuring Instruments via Passthrough ---")
        self.k6221.write("*RST"); self.k6221.write("SOUR:FUNC CURR"); self.k6221.write("SOUR:CURR:RANG:AUTO ON")
        self.k6221.write(f"SOUR:CURR:COMP {compliance}"); print("  K6221 configured for DC source.")
        print("  Sending commands to K2182 via K6221 RS-232 Port...")
        self.k6221.write("SYST:COMM:SER:SEND '*RST'"); time.sleep(0.5)
        self.k6221.write("SYST:COMM:SER:SEND 'FUNC \"VOLT\"'"); time.sleep(0.5)
        self.k6221.write("SYST:COMM:SER:SEND 'SENS:VOLT:DC:RANG:AUTO ON'"); time.sleep(0.5)
        # --- NEW: Put K2182 into continuous, free-running measurement mode ---
        self.k6221.write("SYST:COMM:SER:SEND 'INIT:CONT ON'"); time.sleep(0.5)
        print("  K2182 configured and set to free-running measurement mode.")

    def set_current(self, current):
        """ Sets the current level on the K6221 and turns the output on. """
        self.k6221.write(f"SOUR:CURR {current}"); time.sleep(0.5)
        self.k6221.write("OUTP:STAT ON"); time.sleep(0.5)

    def read_voltage(self):
        """ Fetches the latest reading from the free-running K2182. """
        # --- NEW: Use FETC? to get the latest reading instead of READ? ---
        self.k6221.write("SYST:COMM:SER:SEND 'FETC?'")

        timeout = 2.0; start_poll_time = time.time(); voltage_str = ""
        while time.time() - start_poll_time < timeout:
            response = self.k6221.query("SYST:COMM:SER:ENT?").strip()
            if response: voltage_str = response; break
            time.sleep(0.1)

        if not voltage_str: raise TimeoutError("No response from K2182 via passthrough.")
        last_line = voltage_str.strip().split('\n')[-1]
        return float(last_line)

    def turn_off_output(self):
        if self.k6221:
            try: self.k6221.write("OUTP:STAT OFF")
            except: pass
            print("  K6221 source is OFF.")

    def close(self):
        if self.k6221:
            # Also tell the 2182 to stop continuous measurement
            try: self.k6221.write("SYST:COMM:SER:SEND 'INIT:CONT OFF'")
            except: pass
            self.turn_off_output()
            self.k6221.close()
            print("  K6221 connection closed.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Passthrough_IV_GUI:
    PROGRAM_VERSION = "1.7"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible
    LOGO_FILE_PATH = resource_path("../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg") # Path to your logo image
    CLR_BG_DARK = '#B8A392'; CLR_HEADER = '#E5DCD3'; CLR_FG_LIGHT = '#2C2825'; CLR_TEXT_DARK = '#1A1A1A' # Base colors
    CLR_ACCENT_GOLD = '#BA6B5E'; CLR_ACCENT_GREEN = '#B68B6E'; CLR_ACCENT_RED = '#BA6B5E' # Accent colors
    CLR_CONSOLE_BG = '#E5DCD3'; CLR_GRAPH_BG = '#F4EFEA' # Specific component colors
    FONT_BASE = ('Segoe UI', 11); FONT_TITLE = ('Segoe UI', 13, 'bold'); FONT_CONSOLE = ('Consolas', 10) # Fonts

    def __init__(self, root):
        self.root = root; self.root.title("K6221/2182 I-V Sweep")
        self.root.geometry("1600x950"); self.root.minsize(1300, 850); self.root.configure(bg=self.CLR_BG_DARK)
        self.is_running = False; self.sweep_thread = None; self.logo_image = None
        self.save_path = None
        self.backend = Backend_Passthrough(); self.data_storage = {'current': [], 'voltage': [], 'resistance': []}
        self.setup_styles(); self.create_widgets(); self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root); style.theme_use('clam'); style.configure('TFrame', background=self.CLR_BG_DARK); style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE); style.configure('TRadiobutton', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.map('TRadiobutton', background=[('active', self.CLR_BG_DARK)]); style.configure('TButton', font=self.FONT_BASE, padding=(10, 9))
        style.configure('Hint.TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=('Segoe UI', 9))
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, font=('Segoe UI', 11, 'bold')); style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT, font=('Segoe UI', 11, 'bold')); style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11, 'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        header = tk.Frame(self.root, bg=self.CLR_HEADER); header.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(header, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(header, text="📟", command=launch_gpib_scanner, width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(header, text="K6221/2182 I-V Sweep", bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(side='left', padx=20, pady=10)
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal'); self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # pack_propagate(False) makes the requested width stick; weight=0
        # keeps the left panel from being squeezed as the window resizes,
        # while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(
            self.main_pane, width=self.LEFT_PANEL_WIDTH)
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

        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw")
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

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG); self.main_pane.add(right_panel, weight=1)

        # Create the console frame first to initialize the logger, but
        # don't pack it yet.
        console_pane = self.create_console_frame(scrollable_frame)
        self.create_info_frame(scrollable_frame); self.create_input_frame(scrollable_frame)
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
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); frame.pack(pady=5, padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0); logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)
        self.root.after(50, lambda: self._load_logo(logo_canvas))
        institute_font = ('Segoe UI', self.FONT_BASE[1] + 6, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)

        # Program details
        details_text = ("Program Name: Delta Mode I-V Sweep\n"
                        "Instruments: K6221 (Source), K2182 (Meter)\n"
                        "Measurement Range: 10 nΩ to 100 MΩ")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def _load_logo(self, canvas):
        """Loads the logo image after the main window is drawn."""
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img) # Keep a reference
                canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Sweep Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}; pady_val, padx_val = (5, 5), 10

        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE); self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=padx_val, pady=(0, 10), sticky='ew')

        Label(frame, text="Keithley 6221 (GPIB Address):").grid(row=2, column=0, padx=padx_val, pady=pady_val, sticky='w'); self.k6221_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly'); self.k6221_cb.grid(row=3, column=0, padx=(padx_val, 5), pady=(0, 5), sticky='ew');
        self.scan_button = ttk.Button(frame, text="Scan", command=self.start_visa_scan); self.scan_button.grid(row=3, column=1, padx=(5, padx_val), pady=(0,5), sticky='ew')

        # --- Sweep type: linear / log / custom list ---
        Label(frame, text="Sweep Type:").grid(row=4, column=0, padx=padx_val, pady=pady_val, sticky='w')
        # Explicit master: a StringVar without one binds to Tk's default
        # root, which may belong to another window in the same process.
        self.sweep_type_var = tk.StringVar(master=self.root)
        self.sweep_type_cb = ttk.Combobox(frame, textvariable=self.sweep_type_var, state='readonly', font=self.FONT_BASE, values=list(SWEEP_TYPES))
        self.sweep_type_cb.grid(row=5, column=0, columnspan=2, padx=padx_val, pady=(0, 5), sticky='ew')
        self.sweep_type_cb.set(SWEEP_LINEAR)
        self.sweep_type_cb.bind("<<ComboboxSelected>>", self._on_sweep_type_change)

        # Row widgets are remembered per sweep type so the panel only shows
        # the entries the chosen type uses.
        self._rows = {}
        lbl = Label(frame, text="Start Current (µA):"); lbl.grid(row=6, column=0, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Start Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Start Current"].grid(row=7, column=0, padx=(padx_val, 5), pady=(0, 5), sticky='ew'); self.entries["Start Current"].insert(0, "-10")
        self._rows["Start Current"] = (lbl, self.entries["Start Current"])
        lbl = Label(frame, text="Stop Current (µA):"); lbl.grid(row=6, column=1, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Stop Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Stop Current"].grid(row=7, column=1, padx=(5, padx_val), pady=(0, 5), sticky='ew'); self.entries["Stop Current"].insert(0, "10")
        self._rows["Stop Current"] = (lbl, self.entries["Stop Current"])
        lbl = Label(frame, text="Number of Points:"); lbl.grid(row=8, column=0, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Num Points"] = Entry(frame, font=self.FONT_BASE); self.entries["Num Points"].grid(row=9, column=0, padx=(padx_val, 5), pady=(0, 5), sticky='ew'); self.entries["Num Points"].insert(0, "51")
        self._rows["Num Points"] = (lbl, self.entries["Num Points"])

        self.custom_list_label = Label(frame, text="Custom Current List (µA):")
        self.custom_list_label.grid(row=10, column=0, columnspan=2, padx=padx_val, pady=(6, 0), sticky='w')
        self.custom_list_hint = ttk.Label(
            frame,
            style='Hint.TLabel',
            text=("Separate values with commas, spaces or new lines (paste from the "
                  "List Maker). Points are sourced in the order written, e.g.\n"
                  "0, 1, 2, 5, 10, 5, 2, 1, 0, -1, -2, -5, -10, -5, -2, -1, 0"),
            wraplength=self.LEFT_PANEL_WIDTH - 60,
            justify='left')
        self.custom_list_hint.grid(row=11, column=0, columnspan=2, padx=padx_val, sticky='w')
        self.custom_list_text = scrolledtext.ScrolledText(frame, height=5, font=self.FONT_BASE, wrap='word')
        self.custom_list_text.grid(row=12, column=0, columnspan=2, padx=padx_val, pady=(0, 6), sticky='ew')

        Label(frame, text="Step Delay (s):").grid(row=13, column=0, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Delay"] = Entry(frame, font=self.FONT_BASE); self.entries["Delay"].grid(row=14, column=0, padx=(padx_val, 5), pady=(0, 5), sticky='ew'); self.entries["Delay"].insert(0, "0.2")
        Label(frame, text="Initial Settle Delay (s):").grid(row=13, column=1, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Initial Delay"] = Entry(frame, font=self.FONT_BASE); self.entries["Initial Delay"].grid(row=14, column=1, padx=(5, padx_val), pady=(0, 5), sticky='ew'); self.entries["Initial Delay"].insert(0, "2.0")
        Label(frame, text="Compliance (V):").grid(row=15, column=0, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE); self.entries["Compliance"].grid(row=16, column=0, padx=(padx_val, 5), pady=(0, 5), sticky='ew'); self.entries["Compliance"].insert(0, "10")

        ttk.Button(frame, text="Browse Save Location...", command=self._browse_save).grid(row=17, column=0, columnspan=2, padx=padx_val, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start Sweep", command=self.start_sweep, style='Start.TButton'); self.start_button.grid(row=18, column=0, padx=(padx_val, 5), pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop Sweep", command=self.stop_sweep, style='Stop.TButton', state='disabled'); self.stop_button.grid(row=18, column=1, padx=(5, padx_val), pady=(10, 10), sticky='ew')
        self._on_sweep_type_change()

    def _on_sweep_type_change(self, event=None):
        """Show only the entries that the chosen sweep type uses."""
        if not hasattr(self, 'sweep_type_var'):
            return
        selection = self.sweep_type_var.get()
        show_range = selection in (SWEEP_LINEAR, SWEEP_LOG)
        for key in ("Start Current", "Stop Current", "Num Points"):
            for w in self._rows[key]:
                w.grid() if show_range else w.grid_remove()
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

    def create_console_frame(self, parent): frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0); self.console.pack(pady=5, padx=5, fill='both', expand=True); return frame
    def create_graph_frame(self, parent): container = LabelFrame(parent, text='I-V Curve', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_TEXT_DARK, font=self.FONT_TITLE); container.pack(fill='both', expand=True, padx=5, pady=5); self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG); self.canvas = FigureCanvasTkAgg(self.figure, container); gs = gridspec.GridSpec(2, 1, figure=self.figure); self.ax_main = self.figure.add_subplot(gs[0]); self.ax_sub = self.figure.add_subplot(gs[1]); self.line_main, = self.ax_main.plot([], [], 'o-', c=self.CLR_ACCENT_RED, markersize=4); self.ax_main.set_title("I-V Curve", fontweight='bold'); self.ax_main.set_xlabel("Current (A)"); self.ax_main.set_ylabel("Voltage (V)"); self.line_sub, = self.ax_sub.plot([], [], 's:', c=self.CLR_ACCENT_GREEN, markersize=4); self.ax_sub.set_xlabel("Current (A)"); self.ax_sub.set_ylabel("Resistance (Ω)"); [ax.grid(True, ls='--', alpha=0.6) for ax in [self.ax_main, self.ax_sub]]; self.figure.tight_layout(pad=3.0); self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    def log(self, message): ts = datetime.now().strftime("%H:%M:%S"); self.console.config(state='normal'); self.console.insert('end', f"[{ts}] {message}\n"); self.console.see('end'); self.console.config(state='disabled')

    # ------------------------------------------------------------------
    def _collect_params(self):
        """Read and validate every entry. Returns (params, points_A).
        Raises ValueError with a message the user can act on. Nothing here
        touches the instrument. Zero is a valid entry wherever it is
        meaningful (no truthiness tests)."""
        sweep_type = self.sweep_type_var.get()
        params = {
            'name': self.entries["Sample Name"].get().strip(),
            'sweep_type': sweep_type,
            'k6221_visa': self.k6221_cb.get(),
            'save_path': self.save_path,
            'start_uA': 0.0, 'stop_uA': 0.0, 'points': 0,
            'custom_list_str': '',
        }
        if not params['name']:
            raise ValueError("Sample Name is required.")
        if not params['k6221_visa']:
            raise ValueError("Select the Keithley 6221 GPIB address (Scan).")
        if not params['save_path']:
            raise ValueError("Choose a save location first (Browse Save Location...).")
        if not os.path.isdir(params['save_path']):
            raise ValueError(f"Save location does not exist:\n{params['save_path']}")

        def _num(key, label):
            try:
                return float(self.entries[key].get().strip())
            except ValueError:
                raise ValueError(f"{label} must be a number.")

        params['delay'] = _num("Delay", "Step Delay (s)")
        if params['delay'] < 0:
            raise ValueError("Step Delay (s) cannot be negative.")
        params['initial_delay'] = _num("Initial Delay", "Initial Settle Delay (s)")
        if params['initial_delay'] < 0:
            raise ValueError("Initial Settle Delay (s) cannot be negative.")
        params['compliance'] = _num("Compliance", "Compliance (V)")
        if not (0 < params['compliance'] <= K6221_MAX_COMPLIANCE_V):
            raise ValueError(
                f"Compliance must be between 0 and {K6221_MAX_COMPLIANCE_V:g} V.")

        custom_values = None
        if sweep_type == SWEEP_CUSTOM:
            params['custom_list_str'] = self.custom_list_text.get("1.0", tk.END)
            custom_values = parse_custom_list(params['custom_list_str'])
        else:
            params['start_uA'] = _num("Start Current", "Start Current (µA)")
            params['stop_uA'] = _num("Stop Current", "Stop Current (µA)")
            try:
                params['points'] = int(self.entries["Num Points"].get().strip())
            except ValueError:
                raise ValueError("Number of Points must be a whole number (2 or more).")

        points_uA = build_sweep_points(
            sweep_type,
            start_val=params['start_uA'],
            stop_val=params['stop_uA'],
            num_points=params['points'],
            custom_values=custom_values)
        points_A = points_uA * 1e-6
        check_sweep_limits(points_A, K6221_MAX_CURRENT_A, "A")
        params['max_abs_current_A'] = float(np.max(np.abs(points_A)))
        return params, points_A

    def _write_file_header(self, params, n_points):
        """One parameter per line, every value with its unit, so the file
        is self-describing without the GUI. Columns are SI (A, V, Ohm)."""
        with open(self.data_filepath, 'w', newline='', encoding='utf-8') as f:
            f.write(f"# Program: K6221/2182 I-V Sweep v{self.PROGRAM_VERSION}\n")
            f.write(f"# Sample: {params['name']}\n")
            f.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Source: Keithley 6221 at {params['k6221_visa']} (DC current)\n")
            f.write("# Meter: Keithley 2182 via 6221 RS-232 passthrough (DC voltage)\n")
            f.write(f"# Sweep type: {ascii_label(params['sweep_type'])}\n")
            if params['sweep_type'] == SWEEP_CUSTOM:
                f.write(f"# Custom list (uA): {' '.join(params['custom_list_str'].split())}\n")
            else:
                f.write(f"# Start current (uA): {params['start_uA']:g}\n")
                f.write(f"# Stop current (uA): {params['stop_uA']:g}\n")
            f.write(f"# Number of points: {n_points}\n")
            f.write(f"# Max |current| (A): {params['max_abs_current_A']:.6e}\n")
            f.write(f"# Compliance (V): {params['compliance']:g}\n")
            f.write(f"# Step delay (s): {params['delay']:g}\n")
            f.write(f"# Initial settle delay (s): {params['initial_delay']:g}\n")
            f.write("# Columns: set current in A, measured voltage in V, resistance V/I in Ohm\n")
            f.write("# Resistance is NaN where the set current is 0 A\n")
            writer = csv.writer(f)
            writer.writerow(["Set Current (A)", "Measured Voltage (V)", "Resistance (Ohm)"])

    def _append_row(self, current, voltage, resistance):
        with open(self.data_filepath, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([f"{current:.6e}", f"{voltage:.6e}", f"{resistance:.6e}"])
            try:
                f.flush(); os.fsync(f.fileno())
            except Exception:
                pass

    def start_sweep(self):
        if self.is_running:
            return
        try:
            self.params, points = self._collect_params()
        except Exception as e:
            self.log(f"Input error: {e}"); messagebox.showerror("Input Error", f"{e}"); return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); filename = f"{self.params['name']}_{ts}_IV.dat"
            self.data_filepath = os.path.join(self.params['save_path'], filename)
            self._write_file_header(self.params, len(points))
            self.log(f"Data file: {self.data_filepath}")
            self.log(f"Sweep: {ascii_label(self.params['sweep_type'])}, {len(points)} points, "
                     f"|I|max = {self.params['max_abs_current_A']:.4e} A")
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal'); self.is_running = True; [self.data_storage[key].clear() for key in self.data_storage]; [line.set_data([], []) for line in [self.line_main, self.line_sub]]; self.ax_main.set_title(f"I-V Curve: {self.params['name']}"); self.canvas.draw()
            self.sweep_thread = threading.Thread(target=self._sweep_worker, args=(self.params, points), daemon=True); self.sweep_thread.start()
        except Exception as e:
            self.log(f"ERROR on startup: {traceback.format_exc()}"); messagebox.showerror("Startup Error", f"{e}")
    def stop_sweep(self):
        if self.is_running: self.is_running = False; self.log("Stop command received..."); self.stop_button.config(state='disabled')
    def _sweep_worker(self, params, current_points):
        try:
            self.backend.connect(params['k6221_visa']); self.backend.configure_instruments(params['compliance'])

            self.log("Sweep process starting...")
            self.log(f"Applying dummy current (1e-13 A) for stabilization..."); self.backend.set_current(1e-13)
            self.log(f"Waiting for initial settle delay ({params['initial_delay']}s)..."); time.sleep(params['initial_delay'])

            self.log("Initial stabilization complete. Starting main sweep.")
            for i, current in enumerate(current_points):
                if not self.is_running: self.log("Sweep aborted by user."); break
                self.log(f"Step {i+1}/{len(current_points)}: Setting current to {current:.4e} A...")
                self.backend.set_current(current); time.sleep(params['delay'])
                voltage = self.backend.read_voltage()
                self.root.after(0, self._update_ui_with_point, current, voltage)
            else: self.log("Sweep completed successfully.")
        except Exception as e:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
        finally:
            self.is_running = False; self.backend.close(); self.root.after(0, self._sweep_cleanup_ui)
    def _update_ui_with_point(self, current, voltage):
        # R is undefined at I = 0: store NaN (never inf) and skip it in the plot.
        resistance = voltage / current if current != 0 else float('nan'); self.log(f"  Read: {voltage:.6e} V, R: {resistance:.6e} Ω")
        self.data_storage['current'].append(current); self.data_storage['voltage'].append(voltage); self.data_storage['resistance'].append(resistance)
        try:
            self._append_row(current, voltage, resistance)
        except Exception as e:
            self.log(f"ERROR writing data file: {e}")
        c = np.array(self.data_storage['current'], dtype=float); v = np.array(self.data_storage['voltage'], dtype=float); r = np.array(self.data_storage['resistance'], dtype=float)
        ok_v = np.isfinite(c) & np.isfinite(v); ok_r = ok_v & np.isfinite(r)
        self.line_main.set_data(c[ok_v], v[ok_v]); self.line_sub.set_data(c[ok_r], r[ok_r])
        for ax in [self.ax_main, self.ax_sub]: ax.relim(); ax.autoscale_view(True)
        self.figure.tight_layout(pad=3.0); self.canvas.draw_idle()
    def _sweep_cleanup_ui(self):
        self.start_button.config(state='normal'); self.stop_button.config(state='disabled'); self.log("Ready for next sweep.")

    def start_visa_scan(self):
        """Starts the VISA scan in a separate thread to keep the GUI responsive."""
        self.scan_button.config(state='disabled')
        self.log("Scanning for VISA instruments...")
        threading.Thread(target=self._visa_scan_worker, daemon=True).start()
        self.root.after(100, self._process_visa_queue)

    def _visa_scan_worker(self):
        """Worker function that performs the slow VISA scan."""
        if not pyvisa: self.log("ERROR: PyVISA is not installed."); return
        try:
            rm = pyvisa.ResourceManager()
            resources = rm.list_resources()
            self.backend.visa_queue.put(resources) # Use a queue on the backend object
        except Exception as e:
            self.backend.visa_queue.put(e)

    def _process_visa_queue(self):
        """Checks the queue for results from the VISA scan worker."""
        try:
            result = self.backend.visa_queue.get_nowait()
            if isinstance(result, Exception): self.log(f"ERROR during VISA scan: {result}")
            elif result:
                self.log(f"Found: {result}"); self.k6221_cb['values'] = result
                for res in result:
                    if "GPIB0::13" in res: self.k6221_cb.set(res); break
            else: self.log("No VISA instruments found.")
            self.scan_button.config(state='normal')
        except queue.Empty:
            self.root.after(100, self._process_visa_queue)

    def _browse_save(self):
        path = filedialog.askdirectory();
        if path: self.save_path = path; self.log(f"Save location set to: {path}")
    def _on_closing(self):
        if self.is_running: self.is_running = False; time.sleep(0.2)
        self.backend.close(); self.root.destroy()

def main():
    root = tk.Tk()
    app = Passthrough_IV_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
