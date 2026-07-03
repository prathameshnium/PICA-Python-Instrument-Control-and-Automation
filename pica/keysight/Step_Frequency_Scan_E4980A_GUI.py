"""
Module: Combined_TFreq_GUI.py
Purpose: Combined Lakeshore 350 temperature control + Keysight E4980A
         frequency sweep GUI.

Architecture (per design doc §2):
  - Single GUI thread, single hardware worker thread.
  - Worker owns BOTH instruments (Lakeshore + E4980A). No VISA access
    from the Tk thread.
  - Two message queues: cmd_queue (GUI->worker), gui_queue (worker->GUI).
  - Hardcoded 340 K overtemperature kill switch (§3a).
  - Dynamic PID per setpoint (§3b).
  - Temperature log CSV with `Measuring` flag column (§3c).
  - Per-setpoint frequency scan files with T_set(K) as column 20 (§3d).
  - Temperature plot marks measurement regions; Matplotlib toolbar for
    zoom/pan (§3e).
  - Crash-safe finally block + atexit (§3f).

Bug fixes from §1 applied:
  #1  No cross-thread list sharing — plot data lives in main thread, fed
      by `temp_point`/`scan_point` queue messages.
  #2  configure_ramp no longer double-splits heater_range.
  #3  GUI->worker commands go through cmd_queue (no live_* flag races).
  #4  stop_sequence() only signals the worker; worker does hardware
      shutdown in its finally block.
  #5  _process_gui_queue reschedules while the worker thread is alive.
  #6  Kill switch checked at every temperature read.
  #7  Data file opened/closed inside worker try/finally.
  #8  winsound imported once.
  #9  _generate_steps uses np.arange + rounding.
  #10 No-op sort_var.set removed.
  #12 backend.connect warns if IDN lacks "350".
  #13 _sort_listbox dedupes.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import os
import time
import math
import csv
import queue
import threading
import atexit
import traceback
import platform
from datetime import datetime
from multiprocessing import Process
import runpy

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)
import matplotlib as mpl

# --- Optional packages ---
try:
    import winsound  # imported once (fix #8)
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ============================================================
# Subprocess launchers (unchanged from originals)
# ============================================================
def run_script_process(script_path):
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error: {e} ---")


def launch_plotter_utility():
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(d, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(p):
            messagebox.showerror("File Not Found", p); return
        Process(target=run_script_process, args=(p,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", str(e))


def launch_gpib_scanner():
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(d, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(p):
            messagebox.showerror("File Not Found", p); return
        Process(target=run_script_process, args=(p,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", str(e))


# ============================================================
# BACKEND: Lakeshore 350
# ============================================================
class Lakeshore_Backend:
    HARD_TEMP_LIMIT_K = 340.0  # hardcoded kill switch (§3a) — not in GUI

    def __init__(self):
        self.lakeshore = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.write("*CLS")  # do NOT *RST mid-run
        idn = self.lakeshore.query("*IDN?").strip()
        # Fix #12: warn (not fatal) if IDN doesn't look like a 350
        if "350" not in idn:
            print(f"WARNING: IDN does not contain '350': {idn}")
        return idn

    def set_heater_range(self, output, heater_range):
        try:
            range_code = int(heater_range)
        except (ValueError, TypeError):
            range_map = {"off": 0, "low": 1, "medium": 3, "high": 5}
            range_code = range_map.get(str(heater_range).lower(), 0)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5. Got: {heater_range}")
        self.lakeshore.write(f"RANGE {output},{range_code}")

    def configure_ramp(self, setpoint, rate, heater_range):
        # Fix #2: caller already passes a clean range string; no .split() here
        self.set_heater_range(1, heater_range)
        self.lakeshore.write(f"RAMP 1,1,{rate}")
        time.sleep(0.1)
        self.lakeshore.write(f"SETP 1,{setpoint}")

    def get_status(self):
        temp = float(self.lakeshore.query("KRDG? A").strip())
        resistance = float(self.lakeshore.query("SRDG? A").strip())
        htr_output = float(self.lakeshore.query("HTR? 1").strip())
        return temp, resistance, htr_output

    def set_pid(self, output, p, i, d):
        if not (0 <= p <= 9999 and 0 <= i <= 1000 and 0 <= d <= 200):
            raise ValueError("PID values out of range.")
        self.lakeshore.write(f"PID {output},{p},{i},{d}")

    def get_pid(self, output):
        parts = self.lakeshore.query(f"PID? {output}").split(",")
        return float(parts[0]), float(parts[1]), float(parts[2])

    def check_overtemp(self, temp):
        """Returns True and kills heater if temp >= HARD_TEMP_LIMIT_K."""
        if temp >= self.HARD_TEMP_LIMIT_K:
            try:
                self.lakeshore.write("RANGE 1,0")
                self.lakeshore.write("RAMP 1,0,0")
            except Exception as e:
                print(f"KILL SWITCH: heater-off command failed: {e}")
            return True
        return False

    def stop_ramp(self):
        if self.lakeshore:
            try:
                self.lakeshore.write("RAMP 1,0,0")
                self.set_heater_range(1, "off")
            except Exception as e:
                print(f"stop_ramp warning: {e}")

    def shutdown(self):
        if self.lakeshore:
            try:
                self.stop_ramp()
                self.lakeshore.close()
            except Exception as e:
                print(f"Lakeshore shutdown warning: {e}")
            finally:
                self.lakeshore = None


# ============================================================
# BACKEND: Keysight E4980A
# ============================================================
class LCR_Backend:
    DATA_HEADER = (
        "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\t"
        "R(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''\tT_set(K)"
    )

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
        errors = []
        for _ in range(20):
            err = self.instrument.query(":SYST:ERR?").strip()
            if err.startswith("0,") or err.startswith("+0,"):
                break
            errors.append(err)
        if errors:
            raise RuntimeError(f"SCPI errors after {context}: {errors}")

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
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

    def initialize_instrument(self, p):
        print("\n--- [LCR] Initializing E4980A ---")
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")
        inst = self.rm.open_resource(p["lcr_visa"])
        inst.timeout = 60000
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self.instrument = inst

        idn = inst.query("*IDN?").strip()
        if "E4980" not in idn:
            inst.close()
            raise ConnectionError(f"Not an E4980A: {idn}")
        self.has_opt001 = "001" in inst.query("*OPT?")

        v_bias_max = min(2.0, 40.0 if self.has_opt001 else 2.0)
        v_ac_max = min(2.0, 20.0 if self.has_opt001 else 2.0)
        if abs(p["dc_bias"]) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V safety limit.")
        if not (0 < p["ac_bias"] <= v_ac_max):
            raise ValueError(f"AC level outside 0-{v_ac_max} Vrms limit.")

        inst.write("*RST; *CLS"); time.sleep(1.0)
        inst.write(":DISP:ENAB ON"); time.sleep(0.2)
        inst.write(":FUNC:IMP RX")
        inst.write(f":APER {p['aper']}")
        inst.write(":FUNC:IMP:RANG:AUTO ON"); time.sleep(0.2)
        inst.write(":FORM ASC")
        inst.write(":FUNC:SMON:VAC ON")
        inst.write(":FUNC:SMON:IAC ON")
        inst.write(":FUNC:SMON:VDC OFF")
        inst.write(":FUNC:SMON:IDC OFF"); time.sleep(0.2)
        inst.write(":AMPL:ALC ON" if p["alc_enabled"] else ":AMPL:ALC OFF")
        time.sleep(0.2)
        inst.write(f":CORR:LENG {p['cable_len']}")
        if p["corr_enabled"]:
            inst.write(":CORR:OPEN:STAT ON")
            inst.write(":CORR:SHOR:STAT ON")
        else:
            inst.write(":CORR:OPEN:STAT OFF")
            inst.write(":CORR:SHOR:STAT OFF")
        time.sleep(0.2)
        inst.write(f":VOLT {p['ac_bias']}"); time.sleep(0.5)
        inst.write(":TRIG:SOUR BUS")
        inst.write(":INIT:CONT ON"); time.sleep(0.2)

        if abs(p["dc_bias"]) < 1e-9:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT OFF")
        else:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT ON"); time.sleep(0.5)
            if self.has_opt001:
                self.safe_ramp_dc_bias(p["dc_bias"])
            else:
                if p["dc_bias"] not in (1.5, 2.0):
                    raise ValueError(
                        "Without Option 001, DC bias must be 0, 1.5 or 2 V."
                    )
                inst.write(f":BIAS:VOLT {p['dc_bias']}")
                time.sleep(1.0)

        self._check_errors("configuration")
        print(f"  E4980A configured: {idn}")

    def perform_measurement(self, freq, delay):
        if not self.instrument:
            raise ConnectionError("Instrument not connected.")
        self.instrument.write(f":FREQ {freq}")
        time.sleep(delay)
        self.instrument.write(":TRIG:IMM")
        vals = self.instrument.query_ascii_values(":FETC?")
        R, X = vals[0], vals[1]
        status = int(vals[2]) if len(vals) > 2 else 0
        return R, X, status

    def close_instrument(self):
        print("--- [LCR] Closing ---")
        if not self.instrument:
            return
        try:
            if self.has_opt001:
                self.safe_ramp_dc_bias(0.0)
            else:
                self.instrument.write(":BIAS:VOLT 0")
                time.sleep(0.5)
            self.instrument.write(":BIAS:STAT OFF")
            self.instrument.write(":DISP:PAGE MEAS")
            time.sleep(0.2)
        except Exception as e:
            print(f"  LCR shutdown warning: {e}")
        finally:
            try:
                self.instrument.close()
            finally:
                self.instrument = None


# ============================================================
# FRONTEND: Combined GUI
# ============================================================
class CombinedGUI:
    PROGRAM_VERSION = "1.0-Combined"

    # Theme
    CLR_BG_DARK = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG_LIGHT = "#2C2825"
    CLR_FRAME_BG = "#E5DCD3"
    CLR_INPUT_BG = "#F4EFEA"
    CLR_TEXT_DARK = "#1A1A1A"
    CLR_ACCENT_GREEN = "#8AB845"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_ACCENT_GOLD = "#B68B6E"
    CLR_STABLE_WAIT = "#D4A373"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    CLR_MEAS = "#2A6B3A"
    FONT_BASE = ("Segoe UI", 10)
    FONT_TITLE = ("Segoe UI", 12, "bold")
    FONT_CONSOLE = ("Consolas", 9)

    # Dynamic PID presets (§3b)
    PID_SLOW = (0.5, 4.0, 0)      # below 100 K
    PID_MEDIUM = (20.0, 15.0, 0)  # 100 K and above

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Combined T-Control + Freq Sweep v{self.PROGRAM_VERSION}"
        )
        self.root.geometry("1700x950")
        self.root.minsize(1400, 850)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.ls_backend = Lakeshore_Backend()
        self.lcr_backend = LCR_Backend()

        self.is_running = False
        self.worker_thread = None

        # Two-way queues (fix #1, #3)
        self.cmd_queue = queue.Queue()   # GUI -> worker
        self.gui_queue = queue.Queue()   # worker -> GUI

        atexit.register(self._atexit_shutdown)

        self.logo_image = None
        self.save_dir = ""

        # Frequency array (unchanged: 40 Hz to 2 MHz)
        self.sweep_frequencies = np.concatenate([
            np.arange(40, 1000, 10),
            np.arange(1000, 10000, 100),
            np.arange(10000, 100000, 1000),
            np.arange(100000, 1000000, 10000),
            np.arange(1000000, 2000001, 100000),
        ])

        # Plot data — main thread only (fix #1)
        self.plot_t = []
        self.plot_temp = []
        self.plot_target = []
        self.meas_t = []
        self.meas_temp = []
        self.scan_f = []
        self.scan_cp = []
        self.scan_g = []

        # PID presets for the live PID panel
        self.PID_PRESETS = {
            "Slow (P=0.5, I=4, D=0)": self.PID_SLOW,
            "Medium (P=20, I=15, D=0)": self.PID_MEDIUM,
            "Fast (P=50, I=20, D=0)": (50.0, 20.0, 0),
        }

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.log("Combined GUI initialized. 40 Hz – 2 MHz sweep, 340 K kill switch active.")

    # ------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure("TFrame", background=self.CLR_BG_DARK)
        style.configure("TPanedWindow", background=self.CLR_BG_DARK)
        style.configure("TLabel", background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure("Header.TLabel", background=self.CLR_HEADER)
        style.configure("TButton", font=self.FONT_BASE, padding=(8, 6),
                        foreground=self.CLR_TEXT_DARK, background=self.CLR_HEADER,
                        borderwidth=0, focusthickness=0, focuscolor="none")
        style.map("TButton",
                  background=[("active", self.CLR_ACCENT_GOLD),
                              ("hover", self.CLR_ACCENT_GOLD)])
        style.configure("Start.TButton", background=self.CLR_ACCENT_GREEN)
        style.configure("Stop.TButton", background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FRAME_BG)
        style.configure("TLabelframe", background=self.CLR_FRAME_BG,
                        bordercolor="#BA6B5E")
        style.configure("TLabelframe.Label", background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure("TEntry", fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure("TNotebook", background=self.CLR_BG_DARK)
        style.configure("TNotebook.Tab", padding=(12, 6))
        mpl.rcParams.update({
            "font.family": "Segoe UI",
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "figure.facecolor": self.CLR_GRAPH_BG,
        })

    # ------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------
    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side="top", fill="x")
        ttk.Label(header, text="Combined T-Control + Dielectric Spectroscopy",
                  style="Header.TLabel",
                  font=("Segoe UI", 14, "bold", "italic"),
                  foreground=self.CLR_ACCENT_GOLD
                  ).pack(side="left", padx=20, pady=10)
        ttk.Button(header, text="📈", command=launch_plotter_utility,
                   width=3).pack(side="right", padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner,
                   width=3).pack(side="right", padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(main_pane, width=480)
        main_pane.add(left, weight=0)
        right = ttk.Frame(main_pane)
        main_pane.add(right, weight=1)

        self._populate_left(left)
        self._populate_right(right)

    def _populate_left(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        sb = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=sf, anchor="nw", width=460)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        sf.grid_columnconfigure(0, weight=1)
        sf.grid_rowconfigure(5, weight=1)

        self._create_info_panel(sf, 0)
        self._create_sequence_panel(sf, 1)
        self._create_ls_settings_panel(sf, 2)
        self._create_lcr_settings_panel(sf, 3)
        self._create_pid_panel(sf, 4)
        self._create_console_panel(sf, 5)

    def _create_info_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Information")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        LOGO = 80
        lc = tk.Canvas(frame, width=LOGO, height=LOGO,
                       bg=self.CLR_FRAME_BG, highlightthickness=0)
        lc.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (LOGO, LOGO), RESAMPLE_FILTER)
                self.logo_image = ImageTk.PhotoImage(img)
                lc.create_image(LOGO/2, LOGO/2, image=self.logo_image)
        except Exception:
            pass
        f = ("Segoe UI", 12, "bold")
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=f, background=self.CLR_FRAME_BG
                  ).grid(row=0, column=1, padx=5, pady=(12, 0), sticky="sw")
        ttk.Label(frame, text="Mumbai Centre", font=f,
                  background=self.CLR_FRAME_BG
                  ).grid(row=1, column=1, padx=5, sticky="nw")

    def _create_sequence_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Temperature Sequence Builder")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)

        lf = ttk.Frame(frame)
        lf.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=10, pady=5)
        sb = ttk.Scrollbar(lf, orient="vertical")
        self.listbox = tk.Listbox(lf, height=6, selectmode=tk.EXTENDED,
                                  font=self.FONT_BASE, bg=self.CLR_INPUT_BG,
                                  fg=self.CLR_TEXT_DARK, yscrollcommand=sb.set)
        sb.config(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(frame, text="Start(K):").grid(row=1, column=0, sticky="e", padx=2)
        self.entry_start = ttk.Entry(frame, width=6); self.entry_start.grid(row=1, column=1, sticky="w", padx=2)
        ttk.Label(frame, text="End(K):").grid(row=1, column=2, sticky="e", padx=2)
        self.entry_end = ttk.Entry(frame, width=6); self.entry_end.grid(row=1, column=3, sticky="w", padx=2)
        ttk.Label(frame, text="Step(K):").grid(row=2, column=0, sticky="e", padx=2)
        self.entry_step = ttk.Entry(frame, width=6); self.entry_step.grid(row=2, column=1, sticky="w", padx=2)
        ttk.Button(frame, text="Generate Steps",
                   command=self._generate_steps).grid(row=2, column=2, columnspan=2, sticky="ew", padx=5, pady=2)

        ttk.Separator(frame, orient="horizontal").grid(row=3, column=0, columnspan=4, sticky="ew", pady=5, padx=10)

        ttk.Label(frame, text="Order:").grid(row=4, column=0, sticky="e", padx=2)
        self.sort_var = tk.StringVar(value="Ascending")
        sc = ttk.Combobox(frame, textvariable=self.sort_var,
                          values=["Ascending", "Descending"], state="readonly", width=10)
        sc.grid(row=4, column=1, sticky="w", padx=2)
        sc.bind("<<ComboboxSelected>>", lambda e: self._sort_listbox())

        ttk.Label(frame, text="Manual(K):").grid(row=5, column=0, sticky="e", padx=2, pady=5)
        self.entry_manual = ttk.Entry(frame, width=6)
        self.entry_manual.grid(row=5, column=1, sticky="w", padx=2, pady=5)
        ttk.Button(frame, text="Add", command=self._add_manual_step).grid(row=5, column=2, sticky="ew", padx=2, pady=5)
        ttk.Button(frame, text="Remove", command=self._remove_step).grid(row=5, column=3, sticky="ew", padx=2, pady=5)
        ttk.Button(frame, text="Clear All", command=self._clear_listbox).grid(row=6, column=0, columnspan=4, sticky="ew", padx=10, pady=(0, 5))

    def _create_ls_settings_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Lakeshore 350 Settings")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)
        self.entries = {}
        self._create_grid_entry(frame, "Tolerance (±K):", "tol", "0.5", 0, 0)
        self._create_grid_entry(frame, "Soak Time (s):", "soak", "120", 0, 3)
        self._create_grid_entry(frame, "Ramp Rate (K/min):", "rate", "10.0", 1, 0)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "1", 1, 3)

        ttk.Label(frame, text="Heater Range:").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        self.heater_range_var = tk.StringVar(value="5")
        self.heater_cb = ttk.Combobox(frame, textvariable=self.heater_range_var,
                                      values=["0", "1", "2", "3", "4", "5"], state="readonly", width=8)
        self.heater_cb.grid(row=2, column=1, columnspan=2, sticky="ew", padx=5)
        self.heater_cb.bind("<<ComboboxSelected>>", self._on_heater_range_changed)

        ttk.Label(frame, text="LS VISA:").grid(row=2, column=3, sticky="w", padx=5, pady=5)
        self.ls_cb = ttk.Combobox(frame, state="readonly", width=18)
        self.ls_cb.grid(row=2, column=4, columnspan=2, sticky="ew", padx=5)

    def _create_lcr_settings_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="E4980A LCR Settings")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)
        self.lcr_entries = {}
        self._add_lcr_entry(frame, "Sample Name", "sample_name", 0, 0, 2, "Sample")
        self._add_lcr_entry(frame, "AC Bias (V)", "ac_bias", 1, 0, 1, "1.0")
        self._add_lcr_entry(frame, "DC Bias (V)", "dc_bias", 1, 2, 1, "0.0")
        self._add_lcr_entry(frame, "Freq Delay (s)", "delay", 2, 0, 1, "0.2")

        ttk.Label(frame, text="Aperture:").grid(row=2, column=2, sticky="w", padx=5, pady=2)
        self.aper_cb = ttk.Combobox(frame, values=["SHOR", "MED", "LONG"], state="readonly", width=8)
        self.aper_cb.set("MED"); self.aper_cb.grid(row=3, column=2, sticky="w", padx=5, pady=2)

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="ALC", variable=self.var_alc).grid(row=3, column=0, sticky="w", padx=5)
        ttk.Checkbutton(frame, text="Open/Short Corr", variable=self.var_corr).grid(row=3, column=1, sticky="w", padx=5)

        ttk.Label(frame, text="Cable (m):").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        self.cable_cb = ttk.Combobox(frame, values=["0", "1", "2", "4"], state="readonly", width=4)
        self.cable_cb.set("1"); self.cable_cb.grid(row=4, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(frame, text="LCR VISA:").grid(row=4, column=2, sticky="w", padx=5, pady=2)
        self.lcr_cb = ttk.Combobox(frame, state="readonly", width=28)
        self.lcr_cb.grid(row=4, column=3, sticky="ew", padx=5, pady=2)

        bf = ttk.Frame(frame); bf.grid(row=5, column=0, columnspan=4, sticky="ew", pady=5, padx=5)
        bf.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(bf, text="Start Sequence", style="Start.TButton", command=self.start_sequence)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.stop_button = ttk.Button(bf, text="Stop All", style="Stop.TButton", state="disabled", command=self.stop_sequence)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(bf, text="Scan VISA", command=self._scan_for_visa).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(bf, text="Browse Save…", command=self._browse_save).grid(row=6, column=0, columnspan=4, sticky="ew", padx=5, pady=(0, 5))
        self.save_dir_lbl = ttk.Label(frame, text="Save dir: (not set)", foreground=self.CLR_ACCENT_GOLD)
        self.save_dir_lbl.grid(row=7, column=0, columnspan=4, sticky="w", padx=5)

    def _create_pid_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Live PID Tuning (Output 1)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        ttk.Label(frame, text="Preset:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        self.pid_preset_var = tk.StringVar()
        cb = ttk.Combobox(frame, textvariable=self.pid_preset_var,
                          values=list(self.PID_PRESETS.keys()) + ["Custom"], state="readonly")
        cb.grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        cb.bind("<<ComboboxSelected>>", self._on_pid_preset_change)
        self.pid_p_entry = self._mk_entry(frame, "P:", "50.0", 1, 0)
        self.pid_i_entry = self._mk_entry(frame, "I:", "30.0", 1, 2)
        self.pid_d_entry = self._mk_entry(frame, "D:", "0.0", 2, 0)
        bf = ttk.Frame(frame); bf.grid(row=3, column=0, columnspan=6, sticky="ew", pady=5)
        bf.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(bf, text="Send PID", command=self._send_pid).grid(row=0, column=0, sticky="ew", padx=5)
        ttk.Button(bf, text="Read PID", command=self._read_pid).grid(row=0, column=1, sticky="ew", padx=5)

    def _mk_entry(self, parent, label, default, r, c):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=(10, 2), pady=2)
        e = ttk.Entry(parent, width=10); e.grid(row=r, column=c+1, sticky="ew", padx=2, pady=2)
        e.insert(0, default); return e

    def _create_console_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Console Log")
        frame.grid(row=row, column=0, sticky="nsew", pady=5, padx=5)
        frame.grid_rowconfigure(0, weight=1); frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(frame, state="disabled",
                                                 bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
                                                 font=self.FONT_CONSOLE, wrap="word", borderwidth=0)
        self.console.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    def _populate_right(self, panel):
        panel.grid_rowconfigure(1, weight=1); panel.grid_columnconfigure(0, weight=1)

        sf = ttk.Frame(panel); sf.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        sf.grid_columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(sf, text="READY TO START", font=("Segoe UI", 16, "bold"),
                                   bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK, pady=8)
        self.lbl_status.grid(row=0, column=0, sticky="ew")
        self.progress = ttk.Progressbar(sf, orient="horizontal", mode="determinate")
        self.progress.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 5))

        nb = ttk.Notebook(panel); nb.grid(row=1, column=0, sticky="nsew")

        # Temperature plot tab
        t_tab = ttk.Frame(nb); nb.add(t_tab, text="Temperature vs Time")
        self._build_temp_plot(t_tab)

        # Frequency scan plot tab
        f_tab = ttk.Frame(nb); nb.add(f_tab, text="Cp / G vs Frequency")
        self._build_freq_plot(f_tab)

    def _build_temp_plot(self, parent):
        self.fig_t = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.fig_t.add_subplot(211)
        self.ax_heater = self.fig_t.add_subplot(212, sharex=self.ax_temp)
        self.line_target, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_GREEN, ls="--", label="Target")
        self.line_temp, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker="o", ms=3, ls="-", label="Temp")
        self.scat_meas, = self.ax_temp.plot([], [], ls="", marker="o", ms=5, color=self.CLR_MEAS, label="Measuring (flag=1)")
        self.ax_temp.set_ylabel("Temperature (K)"); self.ax_temp.grid(True, ls="--", alpha=0.6)
        self.ax_temp.legend(loc="best", frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
        self.line_heater, = self.ax_heater.plot([], [], color=self.CLR_ACCENT_GOLD, marker=".", ms=3, ls="-")
        self.ax_heater.set_xlabel("Time (s)"); self.ax_heater.set_ylabel("Heater (%)")
        self.ax_heater.grid(True, ls="--", alpha=0.6)
        self.fig_t.tight_layout()
        self.canvas_t = FigureCanvasTkAgg(self.fig_t, parent)
        self.canvas_t.get_tk_widget().pack(fill="both", expand=True)
        # §3e: Matplotlib toolbar (zoom/pan)
        NavigationToolbar2Tk(self.canvas_t, parent, pack_toolbar=False).update()

    def _build_freq_plot(self, parent):
        self.fig_f = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_cp = self.fig_f.add_subplot(211)
        self.line_cp, = self.ax_cp.plot([], [], color="#C00000", marker="o", ms=3, ls="-")
        self.ax_cp.set_ylabel("Cp (F)"); self.ax_cp.set_xscale("log"); self.ax_cp.grid(True, ls="--", alpha=0.7)
        self.ax_g = self.fig_f.add_subplot(212)
        self.line_g, = self.ax_g.plot([], [], color=self.CLR_MEAS, marker="s", ms=3, ls="-")
        self.ax_g.set_xlabel("Frequency (Hz)"); self.ax_g.set_ylabel("G (S)")
        self.ax_g.set_xscale("log"); self.ax_g.grid(True, ls="--", alpha=0.7)
        self.fig_f.subplots_adjust(left=0.08, right=0.98, top=0.98, bottom=0.07, hspace=0.15)
        self.canvas_f = FigureCanvasTkAgg(self.fig_f, parent)
        self.canvas_f.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas_f, parent, pack_toolbar=False).update()

    # ------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------
    def _create_grid_entry(self, parent, label, key, default, r, c, lockable=True):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=(10, 2), pady=2)
        e = ttk.Entry(parent, font=self.FONT_BASE, width=10)
        e.grid(row=r, column=c+1, sticky="ew", padx=2, pady=2); e.insert(0, default)
        if lockable:
            lb = ttk.Button(parent, text="🔓", width=2,
                            command=lambda k=key: self._toggle_entry_lock(k))
            lb.grid(row=r, column=c+2, sticky="w", padx=(0, 10), pady=2)
            self.entries[key] = {"entry": e, "lock": lb, "locked": False}
        else:
            self.entries[key] = {"entry": e, "lock": None, "locked": False}
        return e

    def _add_lcr_entry(self, parent, label, key, r, c, span, default):
        ttk.Label(parent, text=f"{label}:").grid(row=r, column=c, sticky="w", padx=5, pady=2)
        e = ttk.Entry(parent, font=self.FONT_BASE, width=18)
        e.grid(row=r+1, column=c, columnspan=span, sticky="ew", padx=5, pady=2)
        e.insert(0, default); self.lcr_entries[key] = e

    def _toggle_entry_lock(self, key):
        w = self.entries[key]
        if w["locked"]:
            w["entry"].config(state="normal"); w["lock"].config(text="🔓"); w["locked"] = False
        else:
            w["entry"].config(state="disabled"); w["lock"].config(text="🔒"); w["locked"] = True

    def _on_pid_preset_change(self, event=None):
        p = self.pid_preset_var.get()
        if p in self.PID_PRESETS:
            v = self.PID_PRESETS[p]
            self.pid_p_entry.delete(0, "end"); self.pid_p_entry.insert(0, str(v[0]))
            self.pid_i_entry.delete(0, "end"); self.pid_i_entry.insert(0, str(v[1]))
            self.pid_d_entry.delete(0, "end"); self.pid_d_entry.insert(0, str(v[2]))

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state="normal")
        self.console.insert("end", f"[{ts}] {msg}\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _update_status_ui(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _beep(self):
        def _r():
            try:
                if HAS_WINSOUND and platform.system() == "Windows":
                    winsound.Beep(1000, 500)
                else:
                    self.root.bell()
            except Exception:
                pass
        threading.Thread(target=_r, daemon=True).start()

    # ------------------------------------------------------------
    # Sequence builder helpers (fix #9, #10, #13)
    # ------------------------------------------------------------
    def _generate_steps(self):
        try:
            start = float(self.entry_start.get())
            end = float(self.entry_end.get())
            step = float(self.entry_step.get())
            if step <= 0:
                raise ValueError("Step must be positive")
            # Fix #9: np.arange with drift-free endpoint
            if start < end:
                pts = np.arange(start, end + step/2, step)
            else:
                pts = np.arange(start, end - step/2, -step)
            for v in pts:
                self.listbox.insert(tk.END, f"{v:.2f}")
            self._sort_listbox()
        except ValueError:
            messagebox.showerror("Input Error", "Invalid Start/End/Step.")

    def _add_manual_step(self):
        try:
            v = float(self.entry_manual.get())
            self.listbox.insert(tk.END, f"{v:.2f}")
            self.entry_manual.delete(0, tk.END)
            self._sort_listbox()
        except ValueError:
            messagebox.showerror("Input Error", "Enter a valid temperature.")

    def _remove_step(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def _clear_listbox(self):
        self.listbox.delete(0, tk.END)

    def _sort_listbox(self):
        items = list(self.listbox.get(0, tk.END))
        if not items:
            return
        try:
            floats = sorted({float(x) for x in items},  # fix #13: dedupe
                            reverse=(self.sort_var.get() == "Descending"))
            self.listbox.delete(0, tk.END)
            for v in floats:
                self.listbox.insert(tk.END, f"{v:.2f}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Live-update command senders (queue-based, fix #3)
    # ------------------------------------------------------------
    def _on_heater_range_changed(self, event=None):
        if self.is_running:
            r = self.heater_range_var.get()
            self.log(f"Queued heater range update: {r}")
            self.cmd_queue.put(("heater", r))

    def _send_pid(self):
        if not self.is_running:
            messagebox.showwarning("Not Running", "PID can only be sent while running."); return
        try:
            p = float(self.pid_p_entry.get()); i = float(self.pid_i_entry.get()); d = float(self.pid_d_entry.get())
            self.cmd_queue.put(("pid_send", (p, i, d)))
            self.log(f"Queued PID SEND: P={p}, I={i}, D={d}")
        except ValueError:
            messagebox.showerror("Invalid Input", "P, I, D must be numeric.")

    def _read_pid(self):
        if not self.is_running:
            messagebox.showwarning("Not Running", "PID can only be read while running."); return
        self.cmd_queue.put(("pid_read",))
        self.log("Queued PID READ request.")

    def _send_live_updates(self):
        if not self.is_running:
            messagebox.showwarning("Not Running", "Only during an active sequence."); return
        updates = {}
        try:
            for k, w in self.entries.items():
                if w["lock"] is not None and not w["locked"]:
                    updates[k] = float(w["entry"].get())
            if updates:
                self.cmd_queue.put(("params", updates))
                self.log(f"Queued live params: {updates}")
            else:
                self.log("No unlocked parameters to update.")
        except ValueError:
            messagebox.showerror("Invalid Input", "Unlocked params must be numeric.")

    # ------------------------------------------------------------
    # VISA scan / file browse
    # ------------------------------------------------------------
    def _scan_for_visa(self):
        if not PYVISA_AVAILABLE:
            self.log("PyVISA not available."); return
        rm = self.ls_backend.rm or self.lcr_backend.rm
        if rm is None:
            self.log("VISA RM unavailable."); return
        self.log("Scanning VISA instruments (querying *IDN?)…")
        found = []
        ls_pick = lcr_pick = None
        try:
            for res in rm.list_resources():
                idn = "Unknown"
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
                if "350" in idn and ls_pick is None:
                    ls_pick = label
                if "E4980" in idn and lcr_pick is None:
                    lcr_pick = label
                self.log(f"  {label}")
            self.ls_cb["values"] = found
            self.lcr_cb["values"] = found
            if ls_pick: self.ls_cb.set(ls_pick); self.log(f"Lakeshore auto-selected: {ls_pick}")
            if lcr_pick: self.lcr_cb.set(lcr_pick); self.log(f"E4980A auto-selected: {lcr_pick}")
            if not ls_pick and found: self.ls_cb.set(found[0])
            if not lcr_pick and found: self.lcr_cb.set(found[0])
        except Exception as e:
            self.log(f"Scan error: {e}")

    def _browse_save(self):
        p = filedialog.askdirectory()
        if p:
            self.save_dir = p
            self.save_dir_lbl.config(text=f"Save dir: {p}")
            self.log(f"Save directory: {p}")

    # ------------------------------------------------------------
    # Start / Stop (fix #4: stop only signals worker)
    # ------------------------------------------------------------
    def start_sequence(self):
        setpoints = list(self.listbox.get(0, tk.END))
        if not setpoints:
            messagebox.showwarning("Empty Sequence", "Add at least one setpoint."); return
        if not self.save_dir:
            messagebox.showwarning("No Save Dir", "Choose a save directory first."); return
        try:
            self.params = self._validate_ls_params()
            self.lcr_params = self._validate_lcr_params()
            self.setpoint_floats = [float(x) for x in setpoints]
        except Exception as e:
            messagebox.showerror("Config Error", str(e)); return

        self.set_ui_state(running=True)
        self.is_running = True

        # Clear plot data
        for L in (self.plot_t, self.plot_temp, self.plot_target,
                  self.meas_t, self.meas_temp,
                  self.scan_f, self.scan_cp, self.scan_g):
            L.clear()
        self.line_temp.set_data([], []); self.line_target.set_data([], [])
        self.scat_meas.set_data([], []); self.line_heater.set_data([], [])
        self.line_cp.set_data([], []); self.line_g.set_data([], [])
        self.canvas_t.draw_idle(); self.canvas_f.draw_idle()
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.setpoint_floats) * len(self.sweep_frequencies)

        # Drain queues
        while not self.cmd_queue.empty():
            try: self.cmd_queue.get_nowait()
            except queue.Empty: break
        while not self.gui_queue.empty():
            try: self.gui_queue.get_nowait()
            except queue.Empty: break

        self.worker_thread = threading.Thread(target=self._hardware_worker_loop, daemon=True)
        self.worker_thread.start()
        self.root.after(50, self._process_gui_queue)

    def stop_sequence(self, reason=""):
        if not self.is_running:
            return
        self.log(f"STOP requested: {reason or 'user'}")
        self.is_running = False
        # Fix #4: only signal the worker; it will do the hardware shutdown
        self.cmd_queue.put(("stop",))
        self._update_status_ui("STOPPING…", self.CLR_ACCENT_RED)

    def _validate_ls_params(self):
        ls_visa = self.ls_cb.get()
        if "  ->  " in ls_visa:
            ls_visa = ls_visa.split("  ->  ")[0].strip()
        p = {
            "tol": float(self.entries["tol"]["entry"].get()),
            "soak": float(self.entries["soak"]["entry"].get()),
            "rate": float(self.entries["rate"]["entry"].get()),
            "delay": float(self.entries["delay"]["entry"].get()),
            "heater_range": self.heater_range_var.get().split()[0],  # single split here
            "ls_visa": ls_visa,
        }
        if not p["ls_visa"]: raise ValueError("Select Lakeshore VISA.")
        if p["rate"] <= 0: raise ValueError("Ramp rate must be positive.")
        if p["tol"] <= 0: raise ValueError("Tolerance must be positive.")
        return p

    def _validate_lcr_params(self):
        lcr_visa = self.lcr_cb.get()
        if "  ->  " in lcr_visa:
            lcr_visa = lcr_visa.split("  ->  ")[0].strip()
        p = {
            "sample_name": self.lcr_entries["sample_name"].get().strip() or "Sample",
            "ac_bias": float(self.lcr_entries["ac_bias"].get()),
            "dc_bias": float(self.lcr_entries["dc_bias"].get()),
            "delay": float(self.lcr_entries["delay"].get()),
            "aper": self.aper_cb.get(),
            "alc_enabled": self.var_alc.get(),
            "corr_enabled": self.var_corr.get(),
            "cable_len": self.cable_cb.get(),
            "lcr_visa": lcr_visa,
        }
        if not p["lcr_visa"]: raise ValueError("Select LCR VISA.")
        return p

    def set_ui_state(self, running: bool):
        st = "disabled" if running else "normal"
        self.start_button.config(state=st)
        self.stop_button.config(state="normal" if running else "disabled")
        for w in self.entries.values():
            if running:
                if w["lock"] is not None:
                    w["entry"].config(state="disabled" if w["locked"] else "normal")
                else:
                    w["entry"].config(state="normal")
            else:
                w["entry"].config(state="disabled" if w["locked"] else "normal")
            if w["lock"] is not None:
                w["lock"].config(state=st)
        for e in (self.entry_start, self.entry_end, self.entry_step, self.entry_manual):
            e.config(state=st)
        self.ls_cb.config(state="readonly" if not running else "disabled")
        self.lcr_cb.config(state="readonly" if not running else "disabled")
        # Fix #10: removed the no-op self.sort_var.set(self.sort_var.get())

    # ------------------------------------------------------------
    # Queue plumbing
    # ------------------------------------------------------------
    def _put_gui_msg(self, msg_type, **kw):
        kw["type"] = msg_type
        self.gui_queue.put(kw)

    def _process_gui_queue(self):
        try:
            while True:
                m = self.gui_queue.get_nowait()
                t = m["type"]
                if t == "log":
                    self.log(m["text"])
                elif t == "status":
                    self._update_status_ui(m["text"], m["color"])
                elif t == "temp_point":
                    self.plot_t.append(m["t"])
                    self.plot_temp.append(m["temp"])
                    self.plot_target.append(m["target"])
                    if m["measuring"] == 1:
                        self.meas_t.append(m["t"])
                        self.meas_temp.append(m["temp"])
                    self.line_temp.set_data(self.plot_t, self.plot_temp)
                    self.line_target.set_data(self.plot_t, self.plot_target)
                    self.scat_meas.set_data(self.meas_t, self.meas_temp)
                    self.line_heater.set_data(self.plot_t, m.get("heater", 0) and [m["heater"]] * len(self.plot_t))
                    # heater series: rebuild from a parallel list
                    self.line_heater.set_data(self.plot_t, self._heater_series)
                    for ax in (self.ax_temp, self.ax_heater):
                        ax.relim(); ax.autoscale_view()
                    self.canvas_t.draw_idle()
                elif t == "scan_reset":
                    self.scan_f.clear(); self.scan_cp.clear(); self.scan_g.clear()
                    self.line_cp.set_data([], []); self.line_g.set_data([], [])
                    self.canvas_f.draw_idle()
                elif t == "scan_point":
                    self.scan_f.append(m["freq"])
                    self.scan_cp.append(m["cp"])
                    self.scan_g.append(m["g"])
                    self.line_cp.set_data(self.scan_f, self.scan_cp)
                    self.line_g.set_data(self.scan_f, self.scan_g)
                    for ax in (self.ax_cp, self.ax_g):
                        ax.relim(); ax.autoscale_view()
                    self.canvas_f.draw_idle()
                    self.progress["value"] = m["progress"]
                elif t == "pid_read_result":
                    p, i, d = m["values"]
                    self.pid_p_entry.delete(0, "end"); self.pid_p_entry.insert(0, str(p))
                    self.pid_i_entry.delete(0, "end"); self.pid_i_entry.insert(0, str(i))
                    self.pid_d_entry.delete(0, "end"); self.pid_d_entry.insert(0, str(d))
                    self.pid_preset_var.set("Custom")
                elif t == "sequence_complete":
                    self.set_ui_state(running=False)
                    messagebox.showinfo("Sequence Complete", "All setpoints measured.")
                elif t == "worker_done":
                    self.set_ui_state(running=False)
                    self._update_status_ui("IDLE", self.CLR_HEADER)
                    return  # worker is gone; stop polling
        except queue.Empty:
            pass
        # Fix #5: keep polling while worker is alive (not just is_running)
        if self.worker_thread and self.worker_thread.is_alive():
            self.root.after(50, self._process_gui_queue)

    # ------------------------------------------------------------
    # Impedance math (carried over verbatim from E4980A program)
    # ------------------------------------------------------------
    def calculate_impedance_parameters(self, f, R, X):
        omega = 2 * np.pi * f
        omega_safe = omega if omega != 0 else 1e-20
        Z_mag = np.sqrt(R**2 + X**2)
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
        Ls = abs(Ls); Lp = abs(Lp)  # legacy convention
        D = G_safe / B_safe
        D_safe = D if D != 0 else 1e-20
        Q = 1.0 / D_safe
        theta_rad = math.atan2(X, R)
        theta_deg = math.degrees(theta_rad)
        Y_mag = 1.0 / Z_mag_safe
        Cp_dp = G / omega_safe
        Cs_dp = Cp_dp
        return [Q, D, G, B, Cp, Lp, Cs, Ls, Z_mag, theta_rad, X, R,
                theta_deg, Rp, Y_mag, omega, Cp_dp, Cs_dp]

    # ============================================================
    # WORKER THREAD (owns both instruments)
    # ============================================================
    def _hardware_worker_loop(self):
        self.start_time = time.time()
        self._heater_series = []  # parallel to plot_t, worker-side accumulator for heater %
        self.data_file = None
        self.csv_writer = None
        try:
            # --- Connect Lakeshore ---
            self._put_gui_msg("log", text="Connecting to Lakeshore 350…")
            idn = self.ls_backend.connect(self.params["ls_visa"])
            self._put_gui_msg("log", text=f"Lakeshore: {idn}")

            # --- Connect & configure E4980A ---
            self._put_gui_msg("log", text="Connecting to Keysight E4980A…")
            self.lcr_backend.initialize_instrument(self.lcr_params)
            self._put_gui_msg("log", text="E4980A initialized.")

            # --- Open combined temperature log (§3c) ---
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tlog_path = os.path.join(
                self.save_dir,
                f"{self.lcr_params['sample_name']}_{stamp}_TempLog.csv",
            )
            self.data_file = open(tlog_path, "w", newline="")
            self.csv_writer = csv.writer(self.data_file)
            self.csv_writer.writerow(
                ["Timestamp", "Elapsed_s", "Target_K", "Temperature_K",
                 "Heater_pct", "Measuring"]
            )
            self.data_file.flush()
            self._put_gui_msg("log", text=f"Temperature log: {tlog_path}")

            total_pts = len(self.setpoint_floats) * len(self.sweep_frequencies)
            done_pts = 0

            for i, target in enumerate(self.setpoint_floats):
                if not self.is_running:
                    break
                self._put_gui_msg("log",
                    text=f"--- Step {i+1}/{len(self.setpoint_floats)}: target {target} K ---")
                self._put_gui_msg("status", text=f"RAMPING TO {target} K", color=self.CLR_ACCENT_RED)
                self._put_gui_msg("scan_reset")

                # §3b: dynamic PID per setpoint
                self._apply_dynamic_pid(target)

                # Fix #2: heater_range already a clean code string
                self.ls_backend.configure_ramp(
                    target, self.params["rate"], self.params["heater_range"]
                )

                stable_start = None
                phase = "RAMPING"

                while self.is_running:
                    # Drain command queue (fix #3)
                    if self._process_cmd_queue():
                        # stop requested
                        break

                    # Temperature read + overtemp check (§3a, §6)
                    try:
                        temp, _, htr = self._log_temperature_point(target, measuring_flag=0, heater=htr if phase != "RAMPING" else None)
                    except RuntimeError:
                        raise

                    # State machine
                    if phase in ("RAMPING", "SOAKING"):
                        if abs(temp - target) <= self.params["tol"]:
                            if phase == "RAMPING":
                                stable_start = time.time()
                                phase = "SOAKING"
                                self._put_gui_msg("log",
                                    text=f"In tolerance. Soaking {self.params['soak']}s…")
                                self._put_gui_msg("status",
                                    text=f"SOAKING AT {target} K", color=self.CLR_STABLE_WAIT)
                            elif phase == "SOAKING" and (time.time() - stable_start >= self.params["soak"]):
                                # Soak complete → run the frequency sweep
                                self._put_gui_msg("log",
                                    text=f"Stable. Starting frequency sweep at {target} K.")
                                self._put_gui_msg("status",
                                    text=f"MEASURING AT {target} K", color=self.CLR_ACCENT_GREEN)
                                self._beep()
                                done_pts = self._run_frequency_sweep(target, done_pts, total_pts)
                                if not self.is_running:
                                    break
                                self._put_gui_msg("log",
                                    text=f"Sweep done at {target} K. Proceeding.")
                                break  # next setpoint
                        else:
                            if phase == "SOAKING":
                                self._put_gui_msg("log",
                                    text="Drifted outside tolerance. Restarting soak.")
                                self._put_gui_msg("status",
                                    text=f"RAMPING TO {target} K", color=self.CLR_ACCENT_RED)
                                stable_start = None
                                phase = "RAMPING"

                    time.sleep(self.params["delay"])

            if self.is_running:
                self._put_gui_msg("log", text="Sequence complete.")
                self._put_gui_msg("status", text="COMPLETE", color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("sequence_complete")

        except Exception as e:
            self._put_gui_msg("log",
                text=f"CRITICAL: {e}\n{traceback.format_exc()}")
            self._put_gui_msg("status", text="ERROR", color=self.CLR_ACCENT_RED)
            self._put_gui_msg("sequence_complete")
        finally:
            # §3f: crash-safe shutdown
            try:
                self.lcr_backend.close_instrument()
            except Exception as e:
                print(f"LCR shutdown warning: {e}")
            try:
                self.ls_backend.shutdown()
            except Exception as e:
                print(f"Lakeshore shutdown warning: {e}")
            self._close_data_file()
            self.is_running = False
            self._put_gui_msg("worker_done")

    # ------------------------------------------------------------
    # Worker-side helpers
    # ------------------------------------------------------------
    def _process_cmd_queue(self):
        """Returns True if a stop was requested."""
        try:
            while True:
                cmd = self.cmd_queue.get_nowait()
                kind = cmd[0]
                if kind == "stop":
                    self._put_gui_msg("log", text="Stop received by worker.")
                    self.is_running = False
                    return True
                elif kind == "heater":
                    try:
                        self.ls_backend.set_heater_range(1, cmd[1])
                        self._put_gui_msg("log", text=f"Heater range set: {cmd[1]}")
                    except Exception as e:
                        self._put_gui_msg("log", text=f"Heater update failed: {e}")
                elif kind == "pid_send":
                    p, i, d = cmd[1]
                    try:
                        self.ls_backend.set_pid(1, p, i, d)
                        self._put_gui_msg("log", text=f"PID set: P={p}, I={i}, D={d}")
                    except Exception as e:
                        self._put_gui_msg("log", text=f"PID send failed: {e}")
                elif kind == "pid_read":
                    try:
                        vals = self.ls_backend.get_pid(1)
                        self._put_gui_msg("pid_read_result", values=vals)
                    except Exception as e:
                        self._put_gui_msg("log", text=f"PID read failed: {e}")
                elif kind == "params":
                    updates = cmd[1]
                    self.params.update(updates)
                    self._put_gui_msg("log", text=f"Params applied: {updates}")
                    if "rate" in updates:
                        try:
                            self.ls_backend.lakeshore.write(f"RAMP 1,1,{updates['rate']}")
                            self._put_gui_msg("log", text=f"Ramp rate -> {updates['rate']} K/min")
                        except Exception as e:
                            self._put_gui_msg("log", text=f"Ramp rate update failed: {e}")
        except queue.Empty:
            pass
        return False

    def _apply_dynamic_pid(self, target):
        p, i, d = self.PID_SLOW if target < 100.0 else self.PID_MEDIUM
        try:
            self.ls_backend.set_pid(1, p, i, d)
            self._put_gui_msg("log", text=f"Dynamic PID @ {target} K: P={p}, I={i}, D={d}")
        except Exception as e:
            self._put_gui_msg("log", text=f"Dynamic PID failed: {e}")

    def _log_temperature_point(self, target, measuring_flag, heater=None):
        """Reads Lakeshore, checks overtemp, writes CSV row, queues plot msg.
        Raises RuntimeError if the kill switch trips."""
        temp, _, htr = self.ls_backend.get_status()
        if self.ls_backend.check_overtemp(temp):
            self._put_gui_msg("log",
                text=f"!!! KILL SWITCH: {temp:.2f} K >= 340 K. Heater OFF. Aborting.")
            self._put_gui_msg("status", text="OVERTEMP ABORT", color=self.CLR_ACCENT_RED)
            self.is_running = False
            raise RuntimeError("Hard overtemperature limit reached.")
        elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.csv_writer.writerow(
            [now_str, f"{elapsed:.2f}", f"{target:.4f}", f"{temp:.4f}",
             f"{htr:.2f}", measuring_flag]
        )
        self.data_file.flush()
        self._heater_series.append(htr)
        self._put_gui_msg("temp_point", t=elapsed, temp=temp, target=target,
                          measuring=measuring_flag, heater=htr)
        return temp, _, htr

    def _run_frequency_sweep(self, target_temp, done_pts, total_pts):
        """Runs the E4980A frequency sweep at one stable setpoint.
        Logs temperature (flag=1) interleaved between frequency points (§3d)."""
        fname = f"{self.lcr_params['sample_name']}_{target_temp:.2f}K_FreqScan.txt"
        fpath = os.path.join(self.save_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(f"# Sample: {self.lcr_params['sample_name']} | T_set = {target_temp} K | "
                    f"AC: {self.lcr_params['ac_bias']} V | DC: {self.lcr_params['dc_bias']} V | "
                    f"APER: {self.lcr_params['aper']}\n")
            f.write(self.lcr_backend.DATA_HEADER + "\n")
            for i, freq in enumerate(self.sweep_frequencies):
                if not self.is_running:
                    break
                # check for stop / pid updates mid-sweep
                if self._process_cmd_queue():
                    break
                if not self.is_running:
                    break
                try:
                    R, X, status = self.lcr_backend.perform_measurement(
                        freq, self.lcr_params["delay"]
                    )
                except Exception as e:
                    self._put_gui_msg("log", text=f"Meas error @ {freq} Hz: {e}")
                    break
                vals = self.calculate_impedance_parameters(freq, R, X)
                row = [freq] + vals + [target_temp]
                f.write("\t".join(f"{v:.6E}" for v in row) + "\n")
                f.flush()
                # Interleaved temperature log (flag=1) — §3c/§3d
                try:
                    self._log_temperature_point(target_temp, measuring_flag=1)
                except RuntimeError:
                    break
                done_pts += 1
                self._put_gui_msg("scan_point", freq=freq, cp=vals[4], g=vals[2],
                                  progress=done_pts)
        self._put_gui_msg("log", text=f"Sweep saved: {fname}")
        return done_pts

    def _close_data_file(self):
        f = getattr(self, "data_file", None)
        if f:
            try:
                f.flush(); f.close()
                self._put_gui_msg("log", text=f"Temperature log closed.")
            except Exception:
                pass
            finally:
                self.data_file = None

    # ------------------------------------------------------------
    # Shutdown / close
    # ------------------------------------------------------------
    def _atexit_shutdown(self):
        try:
            self.lcr_backend.close_instrument()
        except Exception:
            pass
        try:
            self.ls_backend.shutdown()
        except Exception:
            pass

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Sequence is running. Stop and exit?"):
                self.stop_sequence("User closed application.")
                if self.worker_thread and self.worker_thread.is_alive():
                    self.worker_thread.join(timeout=3.0)
                self.root.destroy()
        else:
            self.root.destroy()


# ============================================================
# Entry point
# ============================================================
def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("Dependency Error",
                             "PyVISA is not installed.\n\npip install pyvisa")
        return
    root = tk.Tk()
    CombinedGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()