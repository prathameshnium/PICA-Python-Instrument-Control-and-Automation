'''
 PROGRAM:      Keysight E4980A CV Scan (R-X) GUI
 PURPOSE:      Provide a robust interface for automating Capacitance-Voltage
               (CV) sweeps with nested frequency loops, ALC, Aperture control,
               Open/Short corrections, Cable Length correction, and Full
               Impedance calculations derived from measured R-X.
               Requires Option 001 (continuous DC bias) on the E4980A.
'''

import tkinter as tk
from tkinter import (
    ttk,
    Label,
    Entry,
    LabelFrame,
    filedialog,
    messagebox,
    scrolledtext,
    Canvas,
)
import os
import time
import math
import traceback
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
from multiprocessing import Process
import runpy
import threading
import queue
import atexit


# --- Absolute hardware safety limits (E4980A) ---
V_ABS_MAX = 20.0        # Hardcoded DC bias ceiling. NEVER raise this.
                        # (Opt 001 hardware allows ±40 V; we cap at ±20 V.)
V_AC_MAX = 2.0          # AC test signal ceiling (Vrms), standard unit spec.
V_STD_BIAS_MAX = 2.0    # Max bias without Option 001.
FREQ_MIN, FREQ_MAX = 20.0, 2e6


def run_script_process(script_path):
    """Wrapper to execute a script using runpy in its own directory."""
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
        plotter_path = os.path.join(
            script_dir, "..", "utils", "PlotterUtil_GUI.py"
        )
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}",
            )
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch Plotter Utility: {e}"
        )


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py"
        )
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}",
            )
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}"
        )


# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk

    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa

    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# BACKEND CLASS - Instrument Control Logic
# ===============================================================================

class LCR_Backend:
    """Handles all SCPI communication with the Keysight E4980A."""

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.has_opt001 = False
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    def _check_errors(self, context=""):
        """Drain SCPI error queue; raise on any error."""
        errors = []
        for _ in range(20):
            err = self.instrument.query(":SYST:ERR?").strip()
            if err.startswith("0,") or err.startswith("+0,"):
                break
            errors.append(err)
        if errors:
            raise RuntimeError(f"SCPI errors after {context}: {errors}")

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        """Safely ramps the DC bias to the target voltage."""
        current_v = float(self.instrument.query(":BIAS:VOLT?"))
        if abs(target_v - current_v) < 0.01:
            return

        if step <= 0:
            self.instrument.write(f":BIAS:VOLT {target_v:.3f}")
            return

        direction = 1 if target_v > current_v else -1
        ramp_points = np.arange(current_v, target_v, direction * step)
        ramp_points = np.append(ramp_points, target_v)

        for v in ramp_points:
            self.instrument.write(f":BIAS:VOLT {v:.3f}")
            time.sleep(dwell)

    def set_bias_voltage(self, v):
        """Ramp DC bias to v. Final hardware-protection clamp lives here."""
        if abs(v) > V_ABS_MAX:  # defense in depth — should never trigger
            raise ValueError(
                f"Bias {v} V blocked by hardcoded {V_ABS_MAX} V cutoff."
            )
        self.safe_ramp_dc_bias(v)

    def set_frequency(self, freq):
        self.instrument.write(f":FREQ {freq}")

    def measure_point(self, delay):
        """Trigger and fetch one R, X, status at current freq/bias."""
        time.sleep(delay)
        self.instrument.write(":TRIG:IMM")
        vals = self.instrument.query_ascii_values(":FETC?")
        R, X = vals[0], vals[1]
        status = int(vals[2]) if len(vals) > 2 else 0
        return R, X, status

    def initialize_instrument(self, p):
        """Configures the instrument for a CV sweep using R-X function."""
        print("\n--- [Backend] Initializing Keysight E4980A ---")
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        inst = self.rm.open_resource(p["lcr_visa"])
        inst.timeout = 60000  # 60 s; low-freq + LONG + autorange can be slow
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self.instrument = inst

        idn = inst.query("*IDN?").strip()
        if "E4980" not in idn:
            inst.close()
            raise ConnectionError(f"Not an E4980A: {idn}")

        self.has_opt001 = "001" in inst.query("*OPT?")

        # --- Hardcoded safety ceilings (do not modify) ---
        if not self.has_opt001:
            raise RuntimeError(
                "CV sweeps require Option 001 (continuous DC bias). "
                "Standard unit only supports discrete 0/1.5/2 V bias."
            )
        for key in ("v_start", "v_stop"):
            if abs(p[key]) > V_ABS_MAX:
                raise ValueError(
                    f"|{key}| = {abs(p[key])} V exceeds hardcoded "
                    f"{V_ABS_MAX} V safety cutoff."
                )
        if not (0 < p["ac_bias"] <= V_AC_MAX):
            raise ValueError(
                f"AC level must be in (0, {V_AC_MAX}] Vrms."
            )

        # Instrument configuration
        inst.write("*RST; *CLS")
        time.sleep(1.0)  # Graceful reset
        inst.write(":DISP:ENAB ON")
        time.sleep(0.2)

        inst.write(":FUNC:IMP RX")
        inst.write(f":APER {p['aper']}")
        inst.write(":FUNC:IMP:RANG:AUTO ON")
        time.sleep(0.2)

        inst.write(":FORM ASC")

        inst.write(":FUNC:SMON:VAC ON")
        inst.write(":FUNC:SMON:IAC ON")
        inst.write(":FUNC:SMON:VDC OFF")
        inst.write(":FUNC:SMON:IDC OFF")
        time.sleep(0.2)

        if p["alc_enabled"]:
            inst.write(":AMPL:ALC ON")
        else:
            inst.write(":AMPL:ALC OFF")
        time.sleep(0.2)

        inst.write(f":CORR:LENG {p['cable_len']}")
        if p["corr_enabled"]:
            inst.write(":CORR:OPEN:STAT ON")
            inst.write(":CORR:SHOR:STAT ON")
        else:
            inst.write(":CORR:OPEN:STAT OFF")
            inst.write(":CORR:SHOR:STAT OFF")
        time.sleep(0.2)

        inst.write(f":VOLT {p['ac_bias']}")
        time.sleep(0.5)  # Let AC level settle

        inst.write(":TRIG:SOUR BUS")
        inst.write(":INIT:CONT ON")
        time.sleep(0.2)

        # CV mode: start at 0 V with bias output enabled; sweep sets values.
        inst.write(":BIAS:VOLT 0")
        inst.write(":BIAS:STAT ON")
        time.sleep(0.5)

        self._check_errors("configuration")
        print(f"  Connected & configured (RX/CV mode): {idn}")

    def close_instrument(self):
        print("--- [Backend] Closing instrument connection. ---")
        if not self.instrument:
            return
        try:
            if self.has_opt001:
                print("  Ramping bias to zero and turning off...")
                self.safe_ramp_dc_bias(0.0)
            else:
                self.instrument.write(":BIAS:VOLT 0")
                time.sleep(0.5)
            self.instrument.write(":BIAS:STAT OFF")
            self.instrument.write(":DISP:PAGE MEAS")
            time.sleep(0.2)
        except Exception as e:
            print(f"  Warning during shutdown: {e}")
        finally:
            try:
                self.instrument.close()
                print("  E4980A connection closed.")
            finally:
                self.instrument = None


# ===============================================================================
# FRONTEND CLASS - The Main GUI Application
# ===============================================================================

class LCR_Freq_GUI:
    """The main GUI application class for CV measurements."""

    LOGO_SIZE = 110

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theme Colours ---
    CLR_BG_DARK = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG_LIGHT = "#2C2825"
    CLR_TEXT_DARK = "#1A1A1A"
    CLR_ACCENT_GOLD = "#BA6B5E"
    CLR_ACCENT_GREEN = "#B68B6E"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    FONT_SIZE_BASE = 11
    FONT_BASE = ("Segoe UI", FONT_SIZE_BASE)
    FONT_TITLE = ("Segoe UI", FONT_SIZE_BASE + 2, "bold")
    FONT_CONSOLE = ("Consolas", 10)

    # Required output format string
    DATA_HEADER = (
        "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\t"
        "R(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''"
    )

    def __init__(self, root):
        self.root = root
        self.root.title("Keysight E4980A CV Scan (R-X)")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.backend = LCR_Backend()
        self.file_location_path = ""
        self.data_filepath = ""

        # Threading components
        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None

        # Forced Cleanup: register backend close on crash/exit
        atexit.register(self.backend.close_instrument)

        self.data_storage = {
            "volt": [],
            "cp": [],
            "g": [],
            "rs": [],
            "rp": [],
            "status": [],
        }
        self.logo_image = None
        self.sweep_index = 0
        self.sweep_delay = 0.2

        # Built at start_sweep from validated user input:
        self.voltage_points = np.array([])
        self.freq_list = []

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.CLR_BG_DARK)
        style.configure("TPanedWindow", background=self.CLR_BG_DARK)
        style.configure(
            "TLabel",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )
        style.configure(
            "TCheckbutton",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )
        style.configure(
            "TLabelframe",
            background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER,
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE,
        )

        style.configure(
            "TButton",
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor="none",
        )
        style.map(
            "TButton",
            background=[
                ("active", self.CLR_ACCENT_GOLD),
                ("hover", self.CLR_ACCENT_GOLD),
            ],
            foreground=[
                ("active", self.CLR_TEXT_DARK),
                ("hover", self.CLR_TEXT_DARK),
            ],
        )
        style.configure(
            "Start.TButton",
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK,
        )
        style.configure(
            "Stop.TButton",
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT,
        )
        style.configure(
            "green.Horizontal.TProgressbar",
            background=self.CLR_ACCENT_GREEN,
        )

        mpl.rcParams.update(
            {
                "font.family": "Segoe UI",
                "font.size": self.FONT_SIZE_BASE,
                "axes.titlesize": self.FONT_SIZE_BASE + 2,
                "axes.labelsize": self.FONT_SIZE_BASE,
                "figure.facecolor": self.CLR_GRAPH_BG,
            }
        )

    def create_widgets(self):
        font_title_italic = (
            "Segoe UI",
            self.FONT_SIZE_BASE + 2,
            "bold",
            "italic",
        )
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side="top", fill="x")

        Label(
            header_frame,
            text="Keysight E4980A: CV Scan (R-X)",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=font_title_italic,
        ).pack(side="left", padx=20, pady=10)

        # --- Utility Launch Buttons ---
        ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3,
        ).pack(side="right", padx=10, pady=5)
        ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3,
        ).pack(side="right", padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(main_pane)
        main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview,
        )
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw", width=480
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill="x", expand=True, padx=10, pady=5)

        input_frame = self.create_input_frame(scrollable_frame)
        input_frame.pack(fill="x", expand=True, padx=10, pady=5)

        console_frame = self.create_console_frame(scrollable_frame)
        console_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Information")
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (self.LOGO_SIZE, self.LOGO_SIZE),
                    RESAMPLE_FILTER,
                )
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image,
                )
            except Exception:
                pass

        institute_font = ("Segoe UI", self.FONT_SIZE_BASE + 2, "bold")
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(row=0, column=1, padx=10, pady=(10, 0), sticky="sw")
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(row=1, column=1, padx=10, sticky="nw")

        return frame

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Experiment Parameters")
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady = (2, 5)
        padx = 10

        # Row 0: Discharge warning banner (spans both columns)
        tk.Label(
            frame,
            text=(
                "⚠ Warning: Ensure the sample/capacitor is fully "
                "discharged before measurement to prevent instrument "
                "damage."
            ),
            bg="#8B0000",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            wraplength=440,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=8, sticky="ew")

        # Numeric-entry validation: allow only floats while typing
        def _vfloat(P):
            if P in ("", "-", ".", "-."):
                return True
            try:
                float(P)
                return True
            except ValueError:
                return False

        self._vfloat_cmd = (frame.register(_vfloat), "%P")

        # Row 1-2: Sample Name
        self._add_entry(
            frame,
            "Sample Name",
            "sample_name",
            1,
            0,
            colspan=2,
            default="Sample_CVScan",
        )

        # Row 3-4: V Start | V Stop
        self._add_entry(
            frame, "V Start (V)", "v_start", 3, 0, default="-2.0"
        )
        self._add_entry(
            frame, "V Stop (V)", "v_stop", 3, 1, default="2.0"
        )

        # Row 5-6: V Step | AC Bias
        self._add_entry(
            frame, "V Step (V)", "v_step", 5, 0, default="0.05"
        )
        self._add_entry(
            frame, "AC Bias Voltage (V)", "ac_bias", 5, 1, default="1.0"
        )

        # Row 7-8: Frequencies (comma-separated list)
        self._add_entry(
            frame,
            "Frequencies (Hz, comma-sep)",
            "freq_list",
            7,
            0,
            colspan=2,
            default="1000, 10000, 100000, 1000000",
        )

        # Row 9-10: Delay | Aperture
        self._add_entry(
            frame, "Delay per step (s)", "delay", 9, 0, default="0.2"
        )
        Label(
            frame, text="Aperture (:APER):", font=self.FONT_BASE
        ).grid(row=9, column=1, padx=padx, pady=pady, sticky="w")
        self.aper_combobox = ttk.Combobox(
            frame,
            font=self.FONT_BASE,
            state="readonly",
            values=["SHOR", "MED", "LONG"],
        )
        self.aper_combobox.set("MED")
        self.aper_combobox.grid(
            row=10, column=1, padx=padx, pady=(0, 10), sticky="ew"
        )

        # Row 11: ALC checkbox
        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Enable Auto Level Control (ALC)",
            variable=self.var_alc,
        ).grid(row=11, column=0, columnspan=2, padx=padx, pady=2, sticky="w")

        # Row 12: Corrections checkbox
        ttk.Checkbutton(
            frame,
            text="Enable Open/Short Corrections",
            variable=self.var_corr,
        ).grid(row=12, column=0, columnspan=2, padx=padx, pady=2, sticky="w")

        # Row 13: Cable Length selector
        Label(
            frame, text="Cable Length (m):", font=self.FONT_BASE
        ).grid(row=13, column=0, padx=padx, pady=pady, sticky="w")
        self.cable_len_combobox = ttk.Combobox(
            frame,
            font=self.FONT_BASE,
            state="readonly",
            values=["0", "1", "2", "4"],
        )
        self.cable_len_combobox.set("1")
        self.cable_len_combobox.grid(
            row=13, column=1, padx=padx, pady=pady, sticky="ew"
        )

        # Row 14-15: LCR Meter VISA
        Label(
            frame, text="LCR Meter VISA:", font=self.FONT_BASE
        ).grid(
            row=14, column=0, columnspan=2, padx=padx, pady=(10, 2), sticky="w"
        )
        self.lcr_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state="readonly"
        )
        self.lcr_combobox.grid(
            row=15, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky="ew"
        )

        # Row 16: Scan + Browse buttons
        self.scan_button = ttk.Button(
            frame, text="Scan Instruments", command=self._scan_for_visa
        )
        self.scan_button.grid(row=16, column=0, padx=padx, pady=5, sticky="ew")

        ttk.Button(
            frame,
            text="Browse Save Loc...",
            command=self._browse_file_location,
        ).grid(row=16, column=1, padx=padx, pady=5, sticky="ew")

        # Row 17: Start + Stop buttons
        self.start_button = ttk.Button(
            frame,
            text="Start Sweep",
            command=self.start_sweep,
            style="Start.TButton",
        )
        self.start_button.grid(
            row=17, column=0, padx=(padx, 5), pady=15, sticky="ew"
        )

        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_sweep,
            style="Stop.TButton",
            state="disabled",
        )
        self.stop_button.grid(
            row=17, column=1, padx=(5, padx), pady=15, sticky="ew"
        )

        # Row 18: Current measurement label
        self.lbl_current_freq = ttk.Label(
            frame,
            text="Measuring: -- Hz",
            font=("Segoe UI", 12, "bold"),
            foreground=self.CLR_ACCENT_RED,
        )
        self.lbl_current_freq.grid(
            row=18, column=0, columnspan=2, pady=5
        )

        # Row 19: Progress bar
        self.progress_bar = ttk.Progressbar(
            frame,
            orient="horizontal",
            mode="determinate",
            style="green.Horizontal.TProgressbar",
        )
        self.progress_bar.grid(
            row=19,
            column=0,
            columnspan=2,
            padx=padx,
            pady=(5, 10),
            sticky="ew",
        )

        # Apply keystroke validation to numeric entries
        for key in ("v_start", "v_stop", "v_step", "ac_bias"):
            self.entries[key].config(
                validate="key", validatecommand=self._vfloat_cmd
            )

        return frame

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text="Console Output",
            relief="groove",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state="disabled",
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap="word",
            bd=0,
            height=8,
        )
        self.console_widget.pack(pady=5, padx=5, fill="both", expand=True)
        self.log(
            "CV Scan Initialized. Requires Option 001 for continuous bias."
        )
        return frame

    def create_graph_frame(self, parent):
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)

        self.ax_cp = self.figure.add_subplot(2, 1, 1)
        self.line_cp, = self.ax_cp.plot(
            [], [], color="#C00000", marker="o", markersize=3, linestyle="-"
        )
        self.ax_cp.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp.set_xlabel("DC Bias Voltage (V)")
        self.ax_cp.grid(True, linestyle="--", alpha=0.7)

        self.ax_g = self.figure.add_subplot(2, 1, 2)
        self.line_g, = self.ax_g.plot(
            [], [], color="#2A6B3A", marker="s", markersize=3, linestyle="-"
        )
        self.ax_g.set_xlabel("DC Bias Voltage (V)")
        self.ax_g.set_ylabel("Conductance, G (S)")
        self.ax_g.grid(True, linestyle="--", alpha=0.7)

        self.figure.subplots_adjust(
            left=0.08, right=0.98, top=0.98, bottom=0.07, hspace=0.15
        )

        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=0, pady=0
        )

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state="normal")
        self.console_widget.insert("end", f"[{timestamp}] {message}\n")
        self.console_widget.see("end")
        self.console_widget.config(state="disabled")

    def _add_entry(
        self, parent, text, dict_key, r, c, colspan=1, default=""
    ):
        Label(
            parent, text=f"{text}:", font=self.FONT_BASE
        ).grid(row=r, column=c, padx=10, pady=(2, 0), sticky="w")
        entry = Entry(parent, font=self.FONT_BASE)
        entry.grid(
            row=r + 1,
            column=c,
            columnspan=colspan,
            padx=10,
            pady=(0, 10),
            sticky="ew",
        )
        entry.insert(0, default)
        self.entries[dict_key] = entry

    def calculate_impedance_parameters(self, f, R, X):
        """Calculates all 18 parameters from measured R (series resistance)
        and X (reactance)."""
        omega = 2 * np.pi * f
        omega_safe = omega if omega != 0 else 1e-20

        Z_mag = np.sqrt(R ** 2 + X ** 2)
        Z_mag_safe = Z_mag if Z_mag != 0 else 1e-20
        Z_mag_sq = Z_mag_safe ** 2

        G = R / Z_mag_sq
        B = -X / Z_mag_sq

        G_safe = G if G != 0 else 1e-20
        B_safe = B if B != 0 else 1e-20
        X_safe = X if X != 0 else 1e-20

        Rp = 1.0 / G_safe
        Cp = B / omega_safe
        Cs = -1.0 / (omega_safe * X_safe)
        Ls = X / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        Ls = abs(Ls)
        Lp = abs(Lp)

        D = G_safe / B_safe
        D_safe = D if D != 0 else 1e-20
        Q = 1.0 / D_safe

        theta_rad = math.atan2(X, R)
        theta_deg = math.degrees(theta_rad)

        chi = X
        Rs = R

        Y_mag = 1.0 / Z_mag_safe

        Cp_double_prime = G / omega_safe
        Cs_double_prime = Cp_double_prime

        return [
            Q, D, G, B, Cp, Lp, Cs, Ls, Z_mag, theta_rad, chi, Rs,
            theta_deg, Rp, Y_mag, omega, Cp_double_prime, Cs_double_prime,
        ]

    def start_sweep(self):
        try:
            visa_val = self.lcr_combobox.get()
            if "  ->  " in visa_val:
                visa_addr = visa_val.split("  ->  ")[0].strip()
            else:
                visa_addr = visa_val.strip()

            params = {
                "sample_name": self.entries["sample_name"].get(),
                "ac_bias": float(self.entries["ac_bias"].get()),
                "v_start": float(self.entries["v_start"].get()),
                "v_stop": float(self.entries["v_stop"].get()),
                "v_step": float(self.entries["v_step"].get()),
                "freq_list": self.entries["freq_list"].get(),
                "delay": float(self.entries["delay"].get()),
                "aper": self.aper_combobox.get(),
                "alc_enabled": self.var_alc.get(),
                "corr_enabled": self.var_corr.get(),
                "cable_len": self.cable_len_combobox.get(),
                "lcr_visa": visa_addr,
            }

            if not all(
                [
                    params["sample_name"],
                    params["lcr_visa"],
                    self.file_location_path,
                ]
            ):
                raise ValueError(
                    "Sample Name, VISA address, and Save Location"
                    " are required."
                )

            # --- Voltage input validation with clamping ---
            v_start = params["v_start"]
            v_stop = params["v_stop"]
            v_step = abs(params["v_step"])

            if v_step <= 0:
                raise ValueError("Voltage step must be > 0.")

            clamped = False
            for name, val in (("v_start", v_start), ("v_stop", v_stop)):
                if abs(val) > V_ABS_MAX:
                    clamped = True
            v_start = max(-V_ABS_MAX, min(V_ABS_MAX, v_start))
            v_stop = max(-V_ABS_MAX, min(V_ABS_MAX, v_stop))
            if clamped:
                self.log(
                    f"WARNING: voltage limits clamped to ±{V_ABS_MAX} V "
                    f"(hardcoded safety cutoff)."
                )
            params["v_start"], params["v_stop"] = v_start, v_stop

            if not (0 < params["ac_bias"] <= V_AC_MAX):
                raise ValueError(
                    f"AC level must be in (0, {V_AC_MAX}] Vrms."
                )

            direction = 1 if v_stop >= v_start else -1
            self.voltage_points = np.append(
                np.arange(v_start, v_stop, direction * v_step), v_stop
            )

            # --- Frequency list parsing & validation ---
            self.freq_list = sorted(
                {
                    float(tok)
                    for tok in params["freq_list"].split(",")
                    if tok.strip()
                }
            )
            if not self.freq_list:
                raise ValueError("At least one frequency is required.")
            for f in self.freq_list:
                if not (FREQ_MIN <= f <= FREQ_MAX):
                    raise ValueError(
                        f"Frequency {f} Hz outside "
                        f"{FREQ_MIN}-{FREQ_MAX} Hz."
                    )

            # --- Mandatory discharge confirmation ---
            if not messagebox.askokcancel(
                "Safety Check",
                "Warning: Ensure the sample/capacitor is fully discharged "
                "before measurement to prevent instrument damage.\n\n"
                "Proceed with CV sweep?",
                icon="warning",
            ):
                self.log("Sweep aborted by user at discharge check.")
                return

            self.backend.initialize_instrument(params)

            try:
                self.is_running = True
                self.start_button.config(state="disabled")
                self.stop_button.config(state="normal")
                self.scan_button.config(state="disabled")

                for key in self.data_storage:
                    self.data_storage[key].clear()

                self.line_cp.set_data([], [])
                self.line_g.set_data([], [])
                self.canvas.draw()

                self.sweep_index = 0
                self.progress_bar["value"] = 0
                self.progress_bar["maximum"] = (
                    len(self.freq_list) * len(self.voltage_points)
                )

                self.log(
                    f"Starting CV sweep: {len(self.freq_list)} freq(s) × "
                    f"{len(self.voltage_points)} voltage points..."
                )

                self.sweep_delay = float(self.entries["delay"].get())

                self.stop_event.clear()
                self.worker_thread = threading.Thread(
                    target=self._sweep_loop, daemon=True
                )
                self.worker_thread.start()

                self.root.after(100, self._poll_queue)

            except Exception as sweep_err:
                self.backend.close_instrument()
                raise sweep_err

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start sweep.\n\n{e}",
            )

    def stop_sweep(self, reason=""):
        if self.is_running:
            self.is_running = False
            self.stop_event.set()
            self.lbl_current_freq.config(text="Measuring: STOPPED")

            if reason:
                self.log(f"Sweep stopped: {reason}")
            else:
                self.log("Sweep stopped by user.")

            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.scan_button.config(state="normal")

            self.backend.close_instrument()

            if not reason:
                messagebox.showinfo(
                    "Info",
                    "Sweep stopped and instrument disconnected.",
                )

    def _sweep_loop(self):
        """Worker thread: nested freq (outer) × voltage (inner) CV sweep."""
        try:
            point = 0
            for f in self.freq_list:
                if self.stop_event.is_set():
                    break
                self.backend.set_frequency(f)
                self.backend.set_bias_voltage(0.0)  # discharge between freqs
                self.data_queue.put(("NEW_FILE", f, None, None, None, None))

                for v in self.voltage_points:
                    if self.stop_event.is_set():
                        break
                    self.backend.set_bias_voltage(v)
                    R, X, status = self.backend.measure_point(
                        self.sweep_delay
                    )
                    point += 1
                    self.data_queue.put((f, v, R, X, status, point))

            # Always end discharged — but only if instrument still alive
            try:
                if (
                    self.backend.instrument is not None
                    and not self.stop_event.is_set()
                ):
                    self.backend.set_bias_voltage(0.0)
            except Exception:
                pass  # ignore cleanup errors after stop

            if not self.stop_event.is_set():
                self.data_queue.put(("DONE", None, None, None, None, None))
        except Exception as e:
            self.data_queue.put(("ERROR", e, None, None, None, None))

    def _open_new_file(self, freq):
        p = self.backend.params
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{p['sample_name']}_{ts}_CV_{freq:.0f}Hz.txt"
        self.data_filepath = os.path.join(self.file_location_path, fname)
        with open(self.data_filepath, "w", encoding="utf-8") as fh:
            fh.write(
                f"# Sample: {p['sample_name']} | Freq: {freq} Hz | "
                f"AC: {p['ac_bias']}V | Sweep: {p['v_start']} to "
                f"{p['v_stop']} V | APER: {p['aper']}\n"
            )
            fh.write("Voltage(V)\t" + self.DATA_HEADER + "\n")

        # Reset plot for new frequency
        for key in self.data_storage:
            self.data_storage[key].clear()
        self.line_cp.set_data([], [])
        self.line_g.set_data([], [])
        self.canvas.draw_idle()
        self.log(f"New file: {fname}")

    def _poll_queue(self):
        """Main thread: processes data from the worker thread."""
        try:
            while not self.data_queue.empty():
                f, v, R, X, status, idx = self.data_queue.get_nowait()
                if f == "DONE":
                    self._handle_sweep_completion()
                    return
                if f == "ERROR":
                    self._handle_sweep_error(v)
                    return
                if f == "NEW_FILE":
                    self._open_new_file(v)  # v carries the frequency here
                    continue
                self.sweep_index = idx
                self.lbl_current_freq.config(
                    text=f"f = {f:,.0f} Hz | V = {v:+.3f} V"
                )
                self._process_sweep_point(f, v, R, X, status)
                self._update_sweep_plot()
        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(100, self._poll_queue)

    def _scan_for_visa(self):
        """Identity-aware instrument scan."""
        if not PYVISA_AVAILABLE or self.backend.rm is None:
            self.log("ERROR: PyVISA/VISA manager unavailable.")
            return

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        rm = self.backend.rm
        found = []
        e4980_label = None

        try:
            for res in rm.list_resources():
                idn = "Unknown / no response"
                try:
                    with rm.open_resource(res) as dev:
                        dev.timeout = 2000
                        dev.read_termination = "\n"
                        dev.write_termination = "\n"
                        idn = dev.query("*IDN?").strip()
                except Exception:
                    pass

                label = f"{res}  ->  {idn}"
                found.append(label)
                if "E4980" in idn and e4980_label is None:
                    e4980_label = label
                self.log(f"  {label}")

            self.lcr_combobox["values"] = found

            if e4980_label:
                self.lcr_combobox.set(e4980_label)
                self.log("E4980A auto-selected.")
            elif found:
                self.lcr_combobox.set(found[0])
                self.log(
                    "WARNING: No E4980A found; defaulted to first device."
                )
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
                "Exit", "Sweep is running. Stop and exit?"
            ):
                self.stop_sweep("User closed application.")
                self.root.destroy()
        else:
            self.root.destroy()

    def _process_sweep_point(self, f, v, R, X, status=0):
        if status != 0:
            self.log(
                f"WARNING: V={v} V returned status {status} "
                f"(0=normal, non-zero=overload/ALC issue)."
            )

        self.log(
            f"f: {f} Hz | V: {v:+.3f} V | R: {R:.4e} | X: {X:.4e} | "
            f"st: {status}"
        )

        try:
            calc_vals = self.calculate_impedance_parameters(f, R, X)
        except Exception as calc_err:
            self.log(
                f"Calc error at V={v} V: {calc_err}. "
                f"Saving raw row with NaN for derived values."
            )
            calc_vals = [float("nan")] * 18

        # Row: Voltage + Frequency + 18 calculated values
        row_vals = [v, f] + calc_vals
        row_str = "\t".join(f"{x:.6E}" for x in row_vals)

        with open(self.data_filepath, "a", encoding="utf-8") as file:
            file.write(row_str + "\n")
            file.flush()

        # calc_vals indices: Cp=4, G=2, Rs=11, Rp=13
        self.data_storage["volt"].append(v)
        self.data_storage["cp"].append(calc_vals[4])
        self.data_storage["g"].append(calc_vals[2])
        self.data_storage["rs"].append(calc_vals[11])
        self.data_storage["rp"].append(calc_vals[13])
        self.data_storage["status"].append(status)

    def _update_sweep_plot(self):
        self.line_cp.set_data(
            self.data_storage["volt"], self.data_storage["cp"]
        )
        self.line_g.set_data(
            self.data_storage["volt"], self.data_storage["g"]
        )

        self.ax_cp.relim()
        self.ax_cp.autoscale_view()
        self.ax_g.relim()
        self.ax_g.autoscale_view()

        self.canvas.draw_idle()
        self.progress_bar["value"] = self.sweep_index

    def _handle_sweep_completion(self):
        self.lbl_current_freq.config(text="Measuring: DONE")
        self.log("CV sweep finished successfully.")
        self.stop_sweep("Sweep naturally complete.")
        messagebox.showinfo("Finished", "CV sweep is complete.")

    def _handle_sweep_error(self, exception):
        self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
        self.stop_sweep(
            "A critical hardware or measurement error occurred."
        )
        messagebox.showerror(
            "Runtime Error",
            "An error occurred during the sweep. Check console.",
        )


def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\nPlease run:\n"
            "pip install pyvisa",
        )
        return

    root = tk.Tk()
    LCR_Freq_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()