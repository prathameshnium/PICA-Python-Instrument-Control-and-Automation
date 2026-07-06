"""
Module: PPMS_Sync_Freq_Scan_E4980A_GUI.py
Purpose: PASSIVE temperature-synchronized dielectric spectroscopy for
         PPMS-based measurements. Keysight E4980A frequency sweep +
         Lakeshore 350 used STRICTLY READ-ONLY as the probe thermometer.

The PPMS runs its own temperature sequence — this program never talks to
the PPMS and never sends a single control command to the Lakeshore (no
RANGE/RAMP/SETP/PID). The sample sits on a custom probe with large
thermal inertia: it lags the PPMS setpoint and needs extra time to
equilibrate after the PPMS itself has settled. Per schedule step this
program therefore:

  1. SLEEP        — optional fixed wait (e.g. 3 h 30 min for the first
                    cooldown) entered per step in the schedule table.
  2. WAIT_STABLE  — active stabilization detection from the probe
                    thermometer alone (rolling window; three selectable
                    criteria, see below).
  3. SCAN         — full 40 Hz – 2 MHz frequency sweep (identical scan
                    engine and data format as
                    Step_Frequency_Scan_E4980A_GUI.py).

Stability criteria (radio selectable):
  - Band around target : every reading in the window within ±Tolerance
                         of the schedule target AND |drift| ≤ limit.
  - Flatness           : window peak-to-peak ≤ 2×Tolerance AND |drift|
                         ≤ limit — self-referenced, immune to the
                         (unknown) probe-vs-PPMS temperature offset.
                         A coarse "Target guard" (±K, 0 = off) rejects
                         stabilization at the WRONG setpoint (e.g. a
                         misjudged sleep time left the PPMS at the
                         previous temperature).
  - Flatness + band    : both checks.

Timing intelligence:
  - Live scan-duration estimate from the sweep parameters (aperture,
    per-point delay, VISA overhead, low-frequency period limit);
    replaced by the MEASURED mean per-point time after the first sweep.
  - Per step the program logs sleep used, probe settle time and scan
    time, and computes the wait the user should program into the PPMS
    sequence at that setpoint:
        suggested PPMS wait = max(0, sleep + settle − PPMS ramp est.)
                              + scan time + margin
    (margin = max(base × Margin %, Margin floor)). Suggestions appear
    live in the "Timing / PPMS Suggestions" tab and in the TimingLog
    CSV, so the next run of the same sequence can be tightened.
  - If the sample temperature leaves the stability band DURING a sweep
    (PPMS moved on too early), the sweep still completes but the step
    is flagged (TempDriftDuringScan) in the timing log — flag-only
    policy for unattended runs.

Architecture (inherited from Step_Frequency_Scan_E4980A_GUI.py v1.4):
  - Single GUI thread, single hardware worker thread. Worker owns BOTH
    instruments; no VISA access from the Tk thread.
  - Two message queues: cmd_queue (GUI->worker), gui_queue (worker->GUI).
  - Throttled plot redraw (FRZ-1), display decimation (FRZ-2), beep via
    gui_queue (FRZ-3), bounded queue drain (FRZ-4).
  - VISA read retry with device clear (REL-1).
  - Crash-safe finally block + atexit.
Self-contained by design: PICA programs never import from each other;
shared logic is embedded as a copy.
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
from collections import deque
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
    import winsound
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
# Timing helpers
# ============================================================
def parse_duration_min(text):
    """Parse a schedule duration into MINUTES.

    Accepts plain minutes ("210", "12.5") or clock style "H:MM" /
    "H:MM:SS" ("3:30" -> 210 min). Raises ValueError on garbage.
    """
    s = str(text).strip()
    if not s:
        return 0.0
    if ":" in s:
        parts = s.split(":")
        if len(parts) == 2:
            h, m = parts
            return float(h) * 60.0 + float(m)
        if len(parts) == 3:
            h, m, sec = parts
            return float(h) * 60.0 + float(m) + float(sec) / 60.0
        raise ValueError(f"Duration '{s}' must be minutes, H:MM or H:MM:SS.")
    return float(s)


def fmt_hms(seconds):
    """Seconds -> 'H:MM:SS' (floored at 0)."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


# Per-point timing model for the E4980A sweep (used until real data
# replaces it). t_meas = max(base, cycles/f): apertures have a fixed
# floor but are period-limited at low frequency (dominates < ~1 kHz).
APER_MEAS_MODEL = {          # aperture -> (base_s, cycles)
    "SHOR": (0.02, 1.0),
    "MED": (0.09, 4.0),
    "LONG": (0.85, 32.0),
}
VISA_OVERHEAD_S = 0.25       # :FREQ + :TRIG:IMM + *OPC? + :FETC? round trips
TEMP_LOG_S = 0.10            # one interleaved KRDG? per frequency point


def estimate_scan_seconds(freqs, freq_delay, aper):
    """Model estimate of one full frequency sweep, in seconds."""
    base, cycles = APER_MEAS_MODEL.get(aper, APER_MEAS_MODEL["MED"])
    total = 0.0
    for f in freqs:
        total += freq_delay + max(base, cycles / max(float(f), 1.0)) \
                 + VISA_OVERHEAD_S + TEMP_LOG_S
    return total


# ============================================================
# BACKEND: Lakeshore 350 as READ-ONLY probe thermometer
# ============================================================
class Probe_Thermometer_Backend:
    """Strictly read-only: the only commands ever sent are *CLS, *IDN?
    and KRDG? — the PPMS owns temperature control entirely."""

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
        self.lakeshore.write("*CLS")
        idn = self.lakeshore.query("*IDN?").strip()
        if "350" not in idn:
            print(f"WARNING: IDN does not contain '350': {idn}")
        return idn

    def get_temperature(self, channel, retries=2):
        """REL-1: retry transient VISA glitches (device clear + backoff)
        so one failed query at 3 a.m. does not abort an overnight run."""
        last_err = None
        for attempt in range(retries + 1):
            try:
                return float(
                    self.lakeshore.query(f"KRDG? {channel}").strip())
            except Exception as e:
                last_err = e
                if attempt < retries:
                    print(f"get_temperature retry {attempt+1}: {e}")
                    try:
                        self.lakeshore.clear()
                    except Exception:
                        pass
                    time.sleep(0.5)
        raise last_err

    def shutdown(self):
        if self.lakeshore:
            try:
                self.lakeshore.close()
            except Exception as e:
                print(f"Thermometer shutdown warning: {e}")
            finally:
                self.lakeshore = None


# ============================================================
# BACKEND: Keysight E4980A (verbatim from the combined program)
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
        # Block until the triggered measurement is actually complete
        # before fetching. *OPC? returns "1" when the prior command finishes.
        self.instrument.query("*OPC?")
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
# FRONTEND: PPMS-synchronized GUI
# ============================================================
class PPMSSyncGUI:
    PROGRAM_VERSION = "1.0-PPMS-Sync"
    LEFT_PANEL_WIDTH = 480

    # --- FRZ-1 / FRZ-2 / FRZ-4 tuning knobs ---
    REDRAW_MS = 750
    MAX_PLOT_POINTS = 4000
    MAX_MSGS_PER_CYCLE = 300

    # Theme (identical to the combined program)
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
    CLR_SLEEP = "#9BA8B8"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    CLR_MEAS = "#2A6B3A"
    FONT_BASE = ("Segoe UI", 10)
    FONT_TITLE = ("Segoe UI", 12, "bold")
    FONT_CONSOLE = ("Consolas", 9)

    STAB_MODES = (
        ("band", "Band around target"),
        ("flat", "Flatness (self-referenced)"),
        ("flat_band", "Flatness + band"),
    )

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
            f"PPMS-Sync Dielectric Spectroscopy v{self.PROGRAM_VERSION}"
        )
        self.root.geometry("1700x950")
        self.root.minsize(1400, 850)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.thermo_backend = Probe_Thermometer_Backend()
        self.lcr_backend = LCR_Backend()

        self.is_running = False
        self.worker_thread = None

        self.cmd_queue = queue.Queue()   # GUI -> worker
        self.gui_queue = queue.Queue()   # worker -> GUI

        atexit.register(self._atexit_shutdown)

        self.logo_image = None
        self.save_dir = ""

        # Frequency array (fixed, as in the original: 40 Hz to 2 MHz)
        self.sweep_frequencies = np.concatenate([
            np.arange(40, 1000, 10),
            np.arange(1000, 10000, 100),
            np.arange(10000, 100000, 1000),
            np.arange(100000, 1000000, 10000),
            np.arange(1000000, 2000001, 100000),
        ])

        # Plot data — main thread only
        self.plot_t = []
        self.plot_temp = []
        self.plot_target = []
        self.meas_t = []
        self.meas_temp = []
        self.scan_f = []
        self.scan_cp = []
        self.scan_g = []

        # FRZ-1: dirty flags — redraw happens only in _redraw_tick
        self._temp_plot_dirty = False
        self._freq_plot_dirty = False
        self._pending_progress = None

        # Pause/skip + band-patch state
        self._paused = False           # worker-only write
        self._skip_requested = False   # worker-only write
        self._band_params = None       # (center, half_width) of current step
        self._band_dirty = False
        self.band_patch = None

        self.log_y_var = tk.BooleanVar(value=True)
        self._decade_ylims = {}

        # Worker-side phase marker (worker thread writes; CSV Phase column)
        self._worker_phase = None

        # Scan-time estimate state: measured mean per-point time replaces
        # the analytic model after the first completed sweep.
        self._measured_point_s = None
        self._scan_est_s = 0.0

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(self.REDRAW_MS, self._redraw_tick)
        self._update_scan_estimate()
        self.log(f"PPMS-Sync GUI v{self.PROGRAM_VERSION} initialized. "
                 "Lakeshore 350 is READ-ONLY (KRDG? only) — the PPMS owns "
                 "all temperature control.")
        self.log("Per step: optional sleep -> stability detection "
                 "(band / flatness / both) -> 40 Hz–2 MHz sweep. PPMS wait "
                 "suggestions appear in the Timing tab as steps complete.")

    # ------------------------------------------------------------
    # Styling (unchanged)
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
        style.configure("TCheckbutton", background=self.CLR_FRAME_BG)
        style.configure("TRadiobutton", background=self.CLR_FRAME_BG)
        style.configure("TNotebook", background=self.CLR_BG_DARK)
        style.configure("TNotebook.Tab", padding=(12, 6))
        style.configure("Treeview", background=self.CLR_INPUT_BG,
                        fieldbackground=self.CLR_INPUT_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure("Treeview.Heading", background=self.CLR_HEADER,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
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
        ttk.Label(header, text="PPMS-Synchronized Dielectric Spectroscopy "
                               "(passive T-sync)",
                  style="Header.TLabel",
                  font=("Segoe UI", 14, "bold", "italic"),
                  foreground=self.CLR_ACCENT_GOLD
                  ).pack(side="left", padx=20, pady=10)
        ttk.Button(header, text="📈", command=launch_plotter_utility,
                   width=3).pack(side="right", padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner,
                   width=3).pack(side="right", padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left.pack_propagate(False)
        self.main_pane.add(left, weight=0)
        right = ttk.Frame(self.main_pane)
        self.main_pane.add(right, weight=1)

        self._populate_left(left)
        self._populate_right(right)

        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30
            else:
                target = self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        sb = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = sf

        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        sf.grid_columnconfigure(0, weight=1)
        sf.grid_rowconfigure(6, weight=1)

        self._create_info_panel(sf, 0)
        self._create_schedule_panel(sf, 1)
        self._create_stability_panel(sf, 2)
        self._create_thermo_panel(sf, 3)
        self._create_lcr_settings_panel(sf, 4)
        self._create_timing_panel(sf, 5)
        self._create_console_panel(sf, 6)

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

    # ------------------------------------------------------------
    # Measurement schedule editor
    # ------------------------------------------------------------
    SCHED_COLS = ("step", "target", "ppms", "sleep")

    def _create_schedule_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Measurement Schedule "
                                             "(mirror of the PPMS sequence)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1)

        tf = ttk.Frame(frame)
        tf.grid(row=0, column=0, columnspan=6, sticky="nsew", padx=10, pady=5)
        sb = ttk.Scrollbar(tf, orient="vertical")
        self.sched_tree = ttk.Treeview(
            tf, columns=self.SCHED_COLS, show="headings", height=6,
            selectmode="browse", yscrollcommand=sb.set)
        sb.config(command=self.sched_tree.yview)
        heads = {"step": ("#", 30), "target": ("Target (K)", 90),
                 "ppms": ("PPMS ramp+settle (min)", 150),
                 "sleep": ("Sleep (min / h:mm)", 130)}
        for col in self.SCHED_COLS:
            text, width = heads[col]
            self.sched_tree.heading(col, text=text)
            self.sched_tree.column(col, width=width, anchor="center",
                                   stretch=True)
        self.sched_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.sched_tree.bind("<<TreeviewSelect>>", self._sched_on_select)

        ttk.Label(frame, text="Target(K):").grid(row=1, column=0, sticky="e", padx=2)
        self.sched_target = ttk.Entry(frame, width=7)
        self.sched_target.grid(row=1, column=1, sticky="w", padx=2)
        ttk.Label(frame, text="PPMS(min):").grid(row=1, column=2, sticky="e", padx=2)
        self.sched_ppms = ttk.Entry(frame, width=7)
        self.sched_ppms.grid(row=1, column=3, sticky="w", padx=2)
        self.sched_ppms.insert(0, "10")
        ttk.Label(frame, text="Sleep:").grid(row=1, column=4, sticky="e", padx=2)
        self.sched_sleep = ttk.Entry(frame, width=7)
        self.sched_sleep.grid(row=1, column=5, sticky="w", padx=2)
        self.sched_sleep.insert(0, "0:30")

        bf = ttk.Frame(frame)
        bf.grid(row=2, column=0, columnspan=6, sticky="ew", padx=10, pady=2)
        bf.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)
        self.sched_buttons = []
        for c, (txt, cmd) in enumerate((
                ("Add", self._sched_add),
                ("Update", self._sched_update),
                ("Remove", self._sched_remove),
                ("▲", lambda: self._sched_move(-1)),
                ("▼", lambda: self._sched_move(+1)),
                ("Clear", self._sched_clear))):
            b = ttk.Button(bf, text=txt, command=cmd, width=7)
            b.grid(row=0, column=c, sticky="ew", padx=1)
            self.sched_buttons.append(b)

        ttk.Separator(frame, orient="horizontal").grid(
            row=3, column=0, columnspan=6, sticky="ew", pady=4, padx=10)

        ttk.Label(frame, text="Start(K):").grid(row=4, column=0, sticky="e", padx=2)
        self.entry_start = ttk.Entry(frame, width=7)
        self.entry_start.grid(row=4, column=1, sticky="w", padx=2)
        ttk.Label(frame, text="End(K):").grid(row=4, column=2, sticky="e", padx=2)
        self.entry_end = ttk.Entry(frame, width=7)
        self.entry_end.grid(row=4, column=3, sticky="w", padx=2)
        ttk.Label(frame, text="Step(K):").grid(row=4, column=4, sticky="e", padx=2)
        self.entry_step = ttk.Entry(frame, width=7)
        self.entry_step.grid(row=4, column=5, sticky="w", padx=2)
        gen = ttk.Button(frame, text="Generate steps (uses PPMS/Sleep fields "
                                     "above as defaults)",
                         command=self._sched_generate)
        gen.grid(row=5, column=0, columnspan=6, sticky="ew", padx=10, pady=2)
        self.sched_buttons.append(gen)

        of = ttk.Frame(frame)
        of.grid(row=6, column=0, columnspan=6, sticky="ew", padx=10, pady=2)
        of.grid_columnconfigure((0, 1), weight=1)
        b_save = ttk.Button(of, text="Save schedule…", command=self._sched_save)
        b_save.grid(row=0, column=0, sticky="ew", padx=2)
        b_load = ttk.Button(of, text="Load schedule…", command=self._sched_load)
        b_load.grid(row=0, column=1, sticky="ew", padx=2)
        self.sched_buttons.extend((b_save, b_load))

        self.var_sleep_enabled = tk.BooleanVar(value=True)
        self.chk_sleep = ttk.Checkbutton(
            frame, text="Enable initial sleep phase (unchecked: go straight "
                        "to stability detection)",
            variable=self.var_sleep_enabled)
        self.chk_sleep.grid(row=7, column=0, columnspan=6, sticky="w",
                            padx=10, pady=(2, 5))

    def _sched_read_entry_row(self):
        """Validate the three schedule entry fields -> (target, ppms, sleep)."""
        target = float(self.sched_target.get())
        if target <= 0:
            raise ValueError("Target must be > 0 K.")
        ppms = parse_duration_min(self.sched_ppms.get() or "0")
        sleep = parse_duration_min(self.sched_sleep.get() or "0")
        if ppms < 0 or sleep < 0:
            raise ValueError("Durations must be >= 0.")
        return target, ppms, sleep

    def _sched_add(self):
        try:
            target, ppms, sleep = self._sched_read_entry_row()
        except ValueError as e:
            messagebox.showerror("Schedule", str(e)); return
        self.sched_tree.insert("", "end", values=(
            0, f"{target:.2f}", f"{ppms:g}", f"{sleep:g}"))
        self._sched_renumber()
        self.sched_target.delete(0, tk.END)

    def _sched_update(self):
        sel = self.sched_tree.selection()
        if not sel:
            return
        try:
            target, ppms, sleep = self._sched_read_entry_row()
        except ValueError as e:
            messagebox.showerror("Schedule", str(e)); return
        self.sched_tree.item(sel[0], values=(
            0, f"{target:.2f}", f"{ppms:g}", f"{sleep:g}"))
        self._sched_renumber()

    def _sched_remove(self):
        for iid in self.sched_tree.selection():
            self.sched_tree.delete(iid)
        self._sched_renumber()

    def _sched_move(self, delta):
        sel = self.sched_tree.selection()
        if not sel:
            return
        iid = sel[0]
        idx = self.sched_tree.index(iid)
        self.sched_tree.move(iid, "", idx + delta)
        self._sched_renumber()

    def _sched_clear(self):
        for iid in self.sched_tree.get_children():
            self.sched_tree.delete(iid)

    def _sched_generate(self):
        try:
            start = float(self.entry_start.get())
            end = float(self.entry_end.get())
            step = float(self.entry_step.get())
            if step <= 0:
                raise ValueError("Step must be positive.")
            ppms = parse_duration_min(self.sched_ppms.get() or "0")
            sleep = parse_duration_min(self.sched_sleep.get() or "0")
        except ValueError as e:
            messagebox.showerror("Input Error", f"Invalid Start/End/Step: {e}")
            return
        if start < end:
            pts = np.arange(start, end + step/2, step)
        else:
            pts = np.arange(start, end - step/2, -step)
        for v in pts:
            self.sched_tree.insert("", "end", values=(
                0, f"{v:.2f}", f"{ppms:g}", f"{sleep:g}"))
        self._sched_renumber()

    def _sched_on_select(self, event=None):
        sel = self.sched_tree.selection()
        if not sel:
            return
        _, target, ppms, sleep = self.sched_tree.item(sel[0], "values")
        for entry, val in ((self.sched_target, target),
                           (self.sched_ppms, ppms),
                           (self.sched_sleep, sleep)):
            entry.delete(0, tk.END)
            entry.insert(0, val)

    def _sched_renumber(self):
        for i, iid in enumerate(self.sched_tree.get_children(), start=1):
            vals = list(self.sched_tree.item(iid, "values"))
            vals[0] = i
            self.sched_tree.item(iid, values=vals)

    def _sched_rows(self):
        """Schedule as a list of dicts (in table order)."""
        rows = []
        for iid in self.sched_tree.get_children():
            _, target, ppms, sleep = self.sched_tree.item(iid, "values")
            rows.append({
                "target": float(target),
                "ppms_min": parse_duration_min(ppms),
                "sleep_min": parse_duration_min(sleep),
            })
        return rows

    def _sched_save(self):
        rows = self._sched_rows()
        if not rows:
            messagebox.showwarning("Schedule", "Nothing to save."); return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            title="Save schedule")
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Target_K", "PPMS_ramp_settle_min", "Sleep_min"])
                for r in rows:
                    w.writerow([r["target"], r["ppms_min"], r["sleep_min"]])
            self.log(f"Schedule saved: {path}")
        except Exception as e:
            messagebox.showerror("Schedule", f"Save failed: {e}")

    def _sched_load(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            title="Load schedule")
        if not path:
            return
        try:
            with open(path, newline="") as f:
                rdr = csv.reader(f)
                rows = []
                for i, row in enumerate(rdr):
                    if not row or (i == 0 and not
                                   row[0].replace(".", "", 1).isdigit()):
                        continue  # header / blank
                    rows.append((float(row[0]),
                                 parse_duration_min(row[1]) if len(row) > 1 else 0.0,
                                 parse_duration_min(row[2]) if len(row) > 2 else 0.0))
        except Exception as e:
            messagebox.showerror("Schedule", f"Load failed: {e}")
            return
        self._sched_clear()
        for target, ppms, sleep in rows:
            self.sched_tree.insert("", "end", values=(
                0, f"{target:.2f}", f"{ppms:g}", f"{sleep:g}"))
        self._sched_renumber()
        self.log(f"Schedule loaded ({len(rows)} steps): {path}")

    # ------------------------------------------------------------
    # Stabilization criteria
    # ------------------------------------------------------------
    def _create_stability_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Stabilization Criteria "
                                             "(sample thermometer)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)
        self.entries = {}

        self.stab_mode_var = tk.StringVar(value="flat")
        self.stab_mode_radios = []
        for c, (value, label) in enumerate(self.STAB_MODES):
            rb = ttk.Radiobutton(frame, text=label,
                                 variable=self.stab_mode_var, value=value,
                                 command=self._on_stab_mode_changed)
            rb.grid(row=0 if c < 2 else 1, column=(c % 2) * 3,
                    columnspan=3, sticky="w", padx=10, pady=(5, 0))
            self.stab_mode_radios.append(rb)

        self._create_grid_entry(frame, "Tolerance (±K):", "tol", "0.3", 2, 0)
        self._create_grid_entry(frame, "Window (min):", "window_min", "10", 2, 3)
        self._create_grid_entry(frame, "Drift Lim (K/min):", "drift", "0.05", 3, 0)
        self._create_grid_entry(frame, "Target guard (±K, 0=off):",
                                "guard", "2.0", 3, 3)
        self._create_grid_entry(frame, "Timeout (min, 0=off):",
                                "stab_timeout", "0", 4, 0)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "2", 4, 3)

        ttk.Label(frame,
                  text="Flatness: peak-to-peak ≤ 2×Tol over the window, "
                       "any offset from target. Guard rejects 'stable at "
                       "the wrong setpoint'.",
                  font=("Segoe UI", 8, "italic"), wraplength=420
                  ).grid(row=5, column=0, columnspan=6, sticky="w",
                         padx=10, pady=(0, 2))

        ttk.Button(frame, text="Apply Live Updates",
                   command=self._send_live_updates
                   ).grid(row=6, column=0, columnspan=6, sticky="ew",
                          padx=10, pady=(2, 6))
        self._on_stab_mode_changed()

    def _on_stab_mode_changed(self):
        # Target guard only matters for the flatness modes.
        self._set_entry_enabled("guard", self.stab_mode_var.get() != "band")

    def _set_entry_enabled(self, key, enabled):
        w = self.entries.get(key)
        if not w:
            return
        if enabled and not w["locked"]:
            w["entry"].config(state="normal")
        else:
            w["entry"].config(state="disabled")

    # ------------------------------------------------------------
    # Thermometer + LCR panels
    # ------------------------------------------------------------
    def _create_thermo_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Probe Thermometer "
                                             "(Lakeshore 350, READ-ONLY)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        ttk.Label(frame, text="LS VISA:").grid(row=0, column=0, sticky="w",
                                               padx=10, pady=5)
        self.ls_cb = ttk.Combobox(frame, state="readonly", width=18)
        self.ls_cb.grid(row=0, column=1, columnspan=2, sticky="ew", padx=5)
        ttk.Label(frame, text="Input Ch:").grid(row=1, column=0, sticky="w",
                                                padx=10, pady=5)
        self.channel_cb = ttk.Combobox(frame, values=["A", "B", "C", "D"],
                                       state="readonly", width=4)
        self.channel_cb.set("A")
        self.channel_cb.grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(frame,
                  text="Only *IDN?/KRDG? are ever sent — no heater, ramp "
                       "or PID commands exist in this program.",
                  font=("Segoe UI", 8, "italic"), wraplength=420
                  ).grid(row=2, column=0, columnspan=3, sticky="w",
                         padx=10, pady=(0, 5))

    def _create_lcr_settings_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="E4980A LCR Settings")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)
        self.lcr_entries = {}

        self._add_lcr_entry(frame, "Sample Name:", "sample_name", 0, 0, 3, "Sample")
        self._add_lcr_entry(frame, "AC Bias (V):", "ac_bias", 1, 0, 1, "1.0")
        self._add_lcr_entry(frame, "DC Bias (V):", "dc_bias", 1, 2, 1, "0.0")
        self._add_lcr_entry(frame, "Freq Delay (s):", "delay", 2, 0, 1, "0.2")
        ttk.Label(frame, text="Aperture:").grid(row=2, column=2, sticky="w", padx=5, pady=2)
        self.aper_cb = ttk.Combobox(frame, values=["SHOR", "MED", "LONG"], state="readonly", width=8)
        self.aper_cb.set("MED")
        self.aper_cb.grid(row=2, column=3, sticky="w", padx=5, pady=2)
        self.aper_cb.bind("<<ComboboxSelected>>",
                          lambda e: self._update_scan_estimate())
        self.lcr_entries["delay"].bind("<KeyRelease>",
                                       lambda e: self._update_scan_estimate())

        ttk.Label(frame, text="Cable (m):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.cable_cb = ttk.Combobox(frame, values=["0", "1", "2", "4"], state="readonly", width=4)
        self.cable_cb.set("1")
        self.cable_cb.grid(row=3, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(frame, text="LCR VISA:").grid(row=3, column=2, sticky="w", padx=5, pady=2)
        self.lcr_cb = ttk.Combobox(frame, state="readonly", width=28)
        self.lcr_cb.grid(row=3, column=3, sticky="ew", padx=5, pady=2)

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="ALC", variable=self.var_alc).grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=2)
        ttk.Checkbutton(frame, text="Open/Short Corr", variable=self.var_corr).grid(row=4, column=2, columnspan=2, sticky="w", padx=5, pady=2)

        bf = ttk.Frame(frame); bf.grid(row=5, column=0, columnspan=4, sticky="ew", pady=5, padx=5)
        bf.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(bf, text="Start Sequence", style="Start.TButton", command=self.start_sequence)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.stop_button = ttk.Button(bf, text="Stop All", style="Stop.TButton", state="disabled", command=self.stop_sequence)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(bf, text="Scan VISA", command=self._scan_for_visa).grid(row=0, column=2, sticky="ew", padx=2)
        self.pause_button = ttk.Button(bf, text="Pause", state="disabled",
                                       command=self._toggle_pause)
        self.pause_button.grid(row=1, column=0, sticky="ew", padx=2, pady=(4, 0))
        self.skip_button = ttk.Button(bf, text="Skip Phase", state="disabled",
                                      command=self._skip_step)
        self.skip_button.grid(row=1, column=1, sticky="ew", padx=2, pady=(4, 0))

        ttk.Button(frame, text="Browse Save…", command=self._browse_save).grid(row=6, column=0, columnspan=4, sticky="ew", padx=5, pady=(0, 5))
        self.save_dir_lbl = ttk.Label(frame, text="Save dir: (not set)", foreground=self.CLR_ACCENT_GOLD)
        self.save_dir_lbl.grid(row=7, column=0, columnspan=4, sticky="w", padx=5)

    # ------------------------------------------------------------
    # Timing / suggestion settings
    # ------------------------------------------------------------
    def _create_timing_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Timing & PPMS Suggestion")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)

        self.scan_est_lbl = ttk.Label(frame, text="Scan estimate: —",
                                      font=("Segoe UI", 10, "bold"),
                                      foreground=self.CLR_ACCENT_GOLD)
        self.scan_est_lbl.grid(row=0, column=0, columnspan=6, sticky="w",
                               padx=10, pady=(5, 2))

        self._create_grid_entry(frame, "Margin (%):", "margin_pct", "20", 1, 0)
        self._create_grid_entry(frame, "Margin floor (min):",
                                "margin_floor", "5", 1, 3)
        ttk.Label(frame,
                  text="Suggested PPMS wait = max(0, sleep + settle − PPMS "
                       "ramp est.) + scan + max(base×Margin%, floor). See "
                       "the 'Timing / PPMS Suggestions' tab.",
                  font=("Segoe UI", 8, "italic"), wraplength=420
                  ).grid(row=2, column=0, columnspan=6, sticky="w",
                         padx=10, pady=(0, 5))

    def _create_console_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Console Log")
        frame.grid(row=row, column=0, sticky="nsew", pady=5, padx=5)
        frame.grid_rowconfigure(0, weight=1); frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(frame, state="disabled",
                                                 bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
                                                 font=self.FONT_CONSOLE, wrap="word", borderwidth=0)
        self.console.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    # ------------------------------------------------------------
    # Right panel: status banner + plots + suggestions tab
    # ------------------------------------------------------------
    def _populate_right(self, panel):
        panel.grid_rowconfigure(1, weight=1); panel.grid_columnconfigure(0, weight=1)

        sf = ttk.Frame(panel); sf.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        sf.grid_columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(sf, text="READY TO START",
                                   font=("Segoe UI", 16, "bold"),
                                   bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK,
                                   pady=8)
        self.lbl_status.grid(row=0, column=0, sticky="ew")
        self.lbl_temp_now = tk.Label(sf, text="--- K",
                                     font=("Consolas", 18, "bold"),
                                     bg=self.CLR_HEADER,
                                     fg=self.CLR_TEXT_DARK, padx=16, pady=6)
        self.lbl_temp_now.grid(row=0, column=1, sticky="nse", padx=(8, 0))
        self.progress = ttk.Progressbar(sf, orient="horizontal", mode="determinate")
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew",
                           padx=10, pady=(0, 5))

        nb = ttk.Notebook(panel); nb.grid(row=1, column=0, sticky="nsew")

        t_tab = ttk.Frame(nb); nb.add(t_tab, text="Temperature vs Time")
        self._build_temp_plot(t_tab)

        f_tab = ttk.Frame(nb); nb.add(f_tab, text="Cp / G vs Frequency")
        self._build_freq_plot(f_tab)

        s_tab = ttk.Frame(nb); nb.add(s_tab, text="Timing / PPMS Suggestions")
        self._build_timing_tab(s_tab)

    def _build_temp_plot(self, parent):
        self.fig_t = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.fig_t.add_subplot(111)
        self.line_target, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_GREEN, ls="--", label="Schedule target")
        self.line_temp, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker="o", ms=3, ls="-", label="Sample T")
        self.scat_meas, = self.ax_temp.plot([], [], ls="", marker="o", ms=5, color=self.CLR_MEAS, label="Measuring (flag=1)")
        self.ax_temp.set_xlabel("Time (s)")
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, ls="--", alpha=0.6)
        self.ax_temp.legend(loc="best", frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.fig_t.tight_layout()
        self.canvas_t = FigureCanvasTkAgg(self.fig_t, parent)
        tb = NavigationToolbar2Tk(self.canvas_t, parent, pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas_t.get_tk_widget().pack(fill="both", expand=True)

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
        ttk.Checkbutton(parent, text="Log Y scale (decade autoscale)",
                        variable=self.log_y_var,
                        command=self._on_log_y_toggle).pack(side="top", anchor="w", padx=5, pady=(5, 0))
        self.canvas_f = FigureCanvasTkAgg(self.fig_f, parent)
        tb = NavigationToolbar2Tk(self.canvas_f, parent, pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas_f.get_tk_widget().pack(fill="both", expand=True)

    TIMING_COLS = ("step", "target", "sleep", "settle", "scan",
                   "suggest", "status")

    def _build_timing_tab(self, parent):
        ttk.Label(parent,
                  text="Per setpoint: measured probe settle + scan time → "
                       "the wait to program into the PPMS sequence at that "
                       "temperature. Rows start as pre-run estimates and "
                       "are replaced by measured values as steps complete.",
                  wraplength=900, background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT
                  ).pack(side="top", anchor="w", padx=8, pady=6)
        tf = ttk.Frame(parent); tf.pack(fill="both", expand=True, padx=5, pady=5)
        sb = ttk.Scrollbar(tf, orient="vertical")
        self.timing_tree = ttk.Treeview(
            tf, columns=self.TIMING_COLS, show="headings",
            yscrollcommand=sb.set)
        sb.config(command=self.timing_tree.yview)
        heads = {"step": ("#", 40), "target": ("Target (K)", 90),
                 "sleep": ("Sleep used", 110),
                 "settle": ("Probe settle", 110),
                 "scan": ("Scan time", 110),
                 "suggest": ("Suggested PPMS wait", 160),
                 "status": ("Status", 220)}
        for col in self.TIMING_COLS:
            text, width = heads[col]
            self.timing_tree.heading(col, text=text)
            self.timing_tree.column(col, width=width, anchor="center",
                                    stretch=True)
        self.timing_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _on_log_y_toggle(self):
        self._decade_ylims.clear()
        self._freq_plot_dirty = True

    def _decade_autoscale_y(self, ax, values, key):
        """LabVIEW-style decade autoscale (copied verbatim)."""
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        if not pos:
            ax.set_yscale('linear')
            ax.relim()
            ax.autoscale_view(scaley=True)
            self._decade_ylims.pop(key, None)
            return False
        lo = 10.0 ** math.floor(math.log10(min(pos)))
        hi = 10.0 ** math.ceil(math.log10(max(pos)))
        if hi <= lo:
            hi = lo * 10.0
        cur = self._decade_ylims.get(key)
        if cur is not None:
            lo, hi = min(lo, cur[0]), max(hi, cur[1])
        if cur != (lo, hi):
            self._decade_ylims[key] = (lo, hi)
            ax.set_yscale('log')
            ax.set_ylim(lo, hi)
        return True

    # ------------------------------------------------------------
    # FRZ-1 / FRZ-2: throttled redraw + display decimation
    # ------------------------------------------------------------
    def _decimate_display_series(self):
        if len(self.plot_t) > self.MAX_PLOT_POINTS:
            self.plot_t[:] = self.plot_t[::2]
            self.plot_temp[:] = self.plot_temp[::2]
            self.plot_target[:] = self.plot_target[::2]
        if len(self.meas_t) > self.MAX_PLOT_POINTS:
            self.meas_t[:] = self.meas_t[::2]
            self.meas_temp[:] = self.meas_temp[::2]

    def _redraw_tick(self):
        try:
            if self._band_dirty:
                self._band_dirty = False
                if self.band_patch is not None:
                    try:
                        self.band_patch.remove()
                    except Exception:
                        pass
                    self.band_patch = None
                if self._band_params is not None:
                    center, halfw = self._band_params
                    self.band_patch = self.ax_temp.axhspan(
                        center - halfw, center + halfw,
                        color=self.CLR_ACCENT_GREEN, alpha=0.15, zorder=0)
            if self._temp_plot_dirty:
                self._temp_plot_dirty = False
                self._decimate_display_series()
                self.line_temp.set_data(self.plot_t, self.plot_temp)
                self.line_target.set_data(self.plot_t, self.plot_target)
                self.scat_meas.set_data(self.meas_t, self.meas_temp)
                self.ax_temp.relim(); self.ax_temp.autoscale_view()
                self.canvas_t.draw_idle()
            if self._freq_plot_dirty:
                self._freq_plot_dirty = False
                self.line_cp.set_data(self.scan_f, self.scan_cp)
                self.line_g.set_data(self.scan_f, self.scan_g)
                for ax, key, data in ((self.ax_cp, "cp", self.scan_cp),
                                      (self.ax_g, "g", self.scan_g)):
                    ax.relim(); ax.autoscale_view(scalex=True, scaley=False)
                    if self.log_y_var.get():
                        self._decade_autoscale_y(ax, data, key)
                    else:
                        ax.set_yscale('linear'); ax.autoscale_view(scaley=True)
                self.canvas_f.draw_idle()
            if self._pending_progress is not None:
                self.progress["value"] = self._pending_progress
                self._pending_progress = None
        except Exception as e:
            print(f"redraw warning: {e}")
        finally:
            self.root.after(self.REDRAW_MS, self._redraw_tick)

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
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=5, pady=2)
        e = ttk.Entry(parent, font=self.FONT_BASE, width=12)
        e.grid(row=r, column=c+1, columnspan=span, sticky="ew", padx=5, pady=2)
        e.insert(0, default); self.lcr_entries[key] = e

    def _toggle_entry_lock(self, key):
        w = self.entries[key]
        if w["locked"]:
            w["entry"].config(state="normal"); w["lock"].config(text="🔓"); w["locked"] = False
        else:
            w["entry"].config(state="disabled"); w["lock"].config(text="🔒"); w["locked"] = True

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state="normal")
        self.console.insert("end", f"[{ts}] {msg}\n")
        try:
            if int(self.console.index("end-1c").split(".")[0]) > 5000:
                self.console.delete("1.0", "1000.0")
        except Exception:
            pass
        self.console.see("end")
        self.console.config(state="disabled")

    def _update_status_ui(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _beep(self):
        """FRZ-3: main (Tk) thread only; worker beeps via gui_queue."""
        if HAS_WINSOUND and platform.system() == "Windows":
            threading.Thread(
                target=lambda: winsound.Beep(1000, 500), daemon=True
            ).start()
        else:
            try:
                self.root.bell()
            except Exception:
                pass

    # ------------------------------------------------------------
    # Scan-time estimate
    # ------------------------------------------------------------
    def _update_scan_estimate(self):
        n = len(self.sweep_frequencies)
        try:
            freq_delay = float(self.lcr_entries["delay"].get())
            if freq_delay < 0:
                raise ValueError
        except (ValueError, tk.TclError):
            self.scan_est_lbl.config(text="Scan estimate: — (bad Freq Delay)")
            return
        if self._measured_point_s is not None:
            total = n * self._measured_point_s
            src = "measured"
        else:
            total = estimate_scan_seconds(self.sweep_frequencies,
                                          freq_delay, self.aper_cb.get())
            src = "model"
        self._scan_est_s = total
        self.scan_est_lbl.config(
            text=f"One scan ({n} pts): ~{fmt_hms(total)}  [{src}]")

    # ------------------------------------------------------------
    # Live-update / run-control senders
    # ------------------------------------------------------------
    def _send_live_updates(self):
        if not self.is_running:
            messagebox.showwarning("Not Running", "Only during an active sequence."); return
        updates = {}
        try:
            for k, w in self.entries.items():
                if w["lock"] is not None and not w["locked"]:
                    updates[k] = float(w["entry"].get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Unlocked params must be numeric.")
            return
        self.cmd_queue.put(("params", updates))
        self.log(f"Queued live params: {updates}")

    def _toggle_pause(self):
        if not self.is_running:
            return
        if self.pause_button["text"] == "Pause":
            self.cmd_queue.put(("pause",))
            self.pause_button.config(text="Resume")
            self.log("PAUSE requested (sleep countdown / stability clock "
                     "frozen; temperature logging continues).")
        else:
            self.cmd_queue.put(("resume",))
            self.pause_button.config(text="Pause")
            self.log("RESUME requested.")

    def _skip_step(self):
        """Phase-sensitive skip: SLEEP -> stability wait; WAIT_STABLE ->
        scan starts immediately; SCAN -> remainder of sweep aborted."""
        if not self.is_running:
            return
        self.cmd_queue.put(("skip",))
        self.log("SKIP PHASE requested.")

    # ------------------------------------------------------------
    # VISA scan / file browse
    # ------------------------------------------------------------
    def _scan_for_visa(self):
        if not PYVISA_AVAILABLE:
            self.log("PyVISA not available."); return
        rm = self.thermo_backend.rm or self.lcr_backend.rm
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
            if ls_pick: self.ls_cb.set(ls_pick); self.log(f"Thermometer auto-selected: {ls_pick}")
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
    # Start / Stop
    # ------------------------------------------------------------
    def start_sequence(self):
        try:
            self.schedule = self._sched_rows()
        except ValueError as e:
            messagebox.showerror("Schedule Error", f"Bad schedule row: {e}")
            return
        if not self.schedule:
            messagebox.showwarning("Empty Schedule", "Add at least one step."); return
        if not self.save_dir:
            messagebox.showwarning("No Save Dir", "Choose a save directory first."); return
        try:
            self.params = self._validate_params()
            self.lcr_params = self._validate_lcr_params()
        except Exception as e:
            messagebox.showerror("Config Error", str(e)); return

        self.set_ui_state(running=True)
        self.is_running = True
        self._worker_phase = None
        self._paused = False
        self._skip_requested = False
        self.pause_button.config(text="Pause")

        for L in (self.plot_t, self.plot_temp, self.plot_target,
                  self.meas_t, self.meas_temp,
                  self.scan_f, self.scan_cp, self.scan_g):
            L.clear()
        self.line_temp.set_data([], []); self.line_target.set_data([], [])
        self.scat_meas.set_data([], [])
        self.line_cp.set_data([], []); self.line_g.set_data([], [])
        if self.band_patch is not None:
            try:
                self.band_patch.remove()
            except Exception:
                pass
            self.band_patch = None
        self._band_params = None
        self._band_dirty = False
        self.canvas_t.draw_idle(); self.canvas_f.draw_idle()
        self._decade_ylims.clear()
        self._temp_plot_dirty = False
        self._freq_plot_dirty = False
        self.progress["value"] = 0
        self._pending_progress = None
        self.progress["maximum"] = len(self.schedule) * len(self.sweep_frequencies)

        self._prefill_timing_tab()

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
        # Worker is the only writer of is_running (MAJ-5 pattern).
        self.cmd_queue.put(("stop",))
        self._update_status_ui("STOPPING…", self.CLR_ACCENT_RED)

    def _validate_params(self):
        ls_visa = self.ls_cb.get()
        if "  ->  " in ls_visa:
            ls_visa = ls_visa.split("  ->  ")[0].strip()
        p = {
            "mode": self.stab_mode_var.get(),
            "tol": float(self.entries["tol"]["entry"].get()),
            "window_min": float(self.entries["window_min"]["entry"].get()),
            "drift": float(self.entries["drift"]["entry"].get()),
            "guard": float(self.entries["guard"]["entry"].get()),
            "stab_timeout": float(self.entries["stab_timeout"]["entry"].get()),
            "delay": float(self.entries["delay"]["entry"].get()),
            "margin_pct": float(self.entries["margin_pct"]["entry"].get()),
            "margin_floor": float(self.entries["margin_floor"]["entry"].get()),
            "thermo_visa": ls_visa,
            "channel": self.channel_cb.get() or "A",
            "sleep_enabled": self.var_sleep_enabled.get(),
        }
        if not p["thermo_visa"]: raise ValueError("Select the thermometer VISA.")
        if p["tol"] <= 0: raise ValueError("Tolerance must be positive.")
        if p["window_min"] <= 0: raise ValueError("Window must be positive.")
        if p["drift"] <= 0: raise ValueError("Drift limit must be positive.")
        if p["guard"] < 0: raise ValueError("Target guard must be >= 0 (0 disables).")
        if p["stab_timeout"] < 0: raise ValueError("Timeout must be >= 0 (0 disables).")
        if p["delay"] <= 0: raise ValueError("Poll delay must be positive.")
        if p["margin_pct"] < 0: raise ValueError("Margin % must be >= 0.")
        if p["margin_floor"] < 0: raise ValueError("Margin floor must be >= 0.")
        for r in self.schedule:
            if r["target"] <= 0:
                raise ValueError(f"Schedule target {r['target']} K invalid.")
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
        self.pause_button.config(state="normal" if running else "disabled")
        self.skip_button.config(state="normal" if running else "disabled")
        if not running:
            self.pause_button.config(text="Pause")
        # Stability mode + sleep enable are only read at Start
        for rb in self.stab_mode_radios:
            rb.config(state=st)
        self.chk_sleep.config(state=st)
        for b in self.sched_buttons:
            b.config(state=st)
        for e in (self.sched_target, self.sched_ppms, self.sched_sleep,
                  self.entry_start, self.entry_end, self.entry_step):
            e.config(state=st)
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
        if not running:
            self._on_stab_mode_changed()  # re-grey guard if band mode
        self.ls_cb.config(state="readonly" if not running else "disabled")
        self.lcr_cb.config(state="readonly" if not running else "disabled")
        self.channel_cb.config(state="readonly" if not running else "disabled")

    # ------------------------------------------------------------
    # Timing tab plumbing (main thread)
    # ------------------------------------------------------------
    def _prefill_timing_tab(self):
        """Pre-run lower-bound suggestion per step:
        stability window + estimated scan + margin."""
        for iid in self.timing_tree.get_children():
            self.timing_tree.delete(iid)
        self._update_scan_estimate()
        p = self.params
        base = p["window_min"] * 60.0 + self._scan_est_s
        margin = max(base * p["margin_pct"] / 100.0,
                     p["margin_floor"] * 60.0)
        sug = base + margin
        for i, row in enumerate(self.schedule):
            self.timing_tree.insert("", "end", iid=f"s{i}", values=(
                i + 1, f"{row['target']:.2f}", "—", "—",
                fmt_hms(self._scan_est_s), fmt_hms(sug),
                "pending (pre-run estimate)"))

    def _apply_timing_row(self, m):
        iid = f"s{m['index']}"
        vals = (m["index"] + 1, f"{m['target']:.2f}",
                fmt_hms(m["sleep_s"]), fmt_hms(m["settle_s"]),
                fmt_hms(m["scan_s"]), fmt_hms(m["suggest_s"]),
                m["status"])
        if self.timing_tree.exists(iid):
            self.timing_tree.item(iid, values=vals)
        else:
            self.timing_tree.insert("", "end", iid=iid, values=vals)

    # ------------------------------------------------------------
    # Queue plumbing
    # ------------------------------------------------------------
    def _put_gui_msg(self, msg_type, **kw):
        kw["type"] = msg_type
        self.gui_queue.put(kw)

    def _process_gui_queue(self):
        processed = 0
        try:
            while processed < self.MAX_MSGS_PER_CYCLE:
                m = self.gui_queue.get_nowait()
                processed += 1
                t = m["type"]
                if t == "log":
                    self.log(m["text"])
                elif t == "status":
                    self._update_status_ui(m["text"], m["color"])
                elif t == "beep":
                    self._beep()
                elif t == "temp_point":
                    self.plot_t.append(m["t"])
                    self.plot_temp.append(m["temp"])
                    self.plot_target.append(m["target"])
                    if m["measuring"] == 1:
                        self.meas_t.append(m["t"])
                        self.meas_temp.append(m["temp"])
                    self.lbl_temp_now.config(text=f"{m['temp']:.3f} K")
                    self._temp_plot_dirty = True
                elif t == "band":
                    self._band_params = (m["center"], m["halfw"])
                    self._band_dirty = True
                    self._temp_plot_dirty = True
                elif t == "pause_state":
                    self.pause_button.config(
                        text="Resume" if m["paused"] else "Pause")
                elif t == "scan_reset":
                    self.scan_f.clear(); self.scan_cp.clear(); self.scan_g.clear()
                    self._decade_ylims.clear()
                    self._freq_plot_dirty = True
                elif t == "scan_point":
                    self.scan_f.append(m["freq"])
                    self.scan_cp.append(m["cp"])
                    self.scan_g.append(m["g"])
                    self._freq_plot_dirty = True
                    self._pending_progress = m["progress"]
                elif t == "point_time":
                    self._measured_point_s = m["avg"]
                    self._update_scan_estimate()
                elif t == "timing_row":
                    self._apply_timing_row(m)
                elif t == "sequence_complete":
                    self.set_ui_state(running=False)
                    messagebox.showinfo("Sequence Complete", "All schedule steps measured.")
                elif t == "worker_done":
                    self.set_ui_state(running=False)
                    self._update_status_ui("IDLE", self.CLR_HEADER)
                    return
        except queue.Empty:
            pass
        if self.worker_thread and self.worker_thread.is_alive():
            self.root.after(50, self._process_gui_queue)
        elif not self.gui_queue.empty():
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
        Cs_dp = D * Cs   # legacy LabVIEW series-model loss convention
        return [Q, D, G, B, Cp, Lp, Cs, Ls, Z_mag, theta_rad, X, R,
                theta_deg, Rp, Y_mag, omega, Cp_dp, Cs_dp]

    # ============================================================
    # WORKER THREAD (owns both instruments)
    # ============================================================
    def _hardware_worker_loop(self):
        self.start_time = time.time()
        self.data_file = None
        self.csv_writer = None
        self.timing_file = None
        self.timing_writer = None
        try:
            self._put_gui_msg("log", text="Connecting to probe thermometer "
                                          "(Lakeshore 350, read-only)…")
            idn = self.thermo_backend.connect(self.params["thermo_visa"])
            self._put_gui_msg("log", text=f"Thermometer: {idn} "
                                          f"(channel {self.params['channel']})")

            self._put_gui_msg("log", text="Connecting to Keysight E4980A…")
            self.lcr_backend.initialize_instrument(self.lcr_params)
            self._put_gui_msg("log", text="E4980A initialized.")

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tlog_path = os.path.join(
                self.save_dir,
                f"{self.lcr_params['sample_name']}_{stamp}_TempLog.csv",
            )
            self.data_file = open(tlog_path, "w", newline="")
            self.csv_writer = csv.writer(self.data_file)
            self.csv_writer.writerow(
                ["Timestamp", "Elapsed_s", "Target_K", "Sample_T_K",
                 "Measuring", "Phase"]
            )
            self.data_file.flush()
            self._put_gui_msg("log", text=f"Temperature log: {tlog_path}")

            timing_path = os.path.join(
                self.save_dir,
                f"{self.lcr_params['sample_name']}_{stamp}_TimingLog.csv",
            )
            self.timing_file = open(timing_path, "w", newline="")
            self.timing_writer = csv.writer(self.timing_file)
            self.timing_writer.writerow(
                ["Step", "Target_K", "Step_start", "Sleep_used_s",
                 "Stab_wait_s", "Stab_outcome", "Scan_s",
                 "TempDriftDuringScan", "PPMS_ramp_est_s",
                 "Suggested_PPMS_wait_s", "Suggested_PPMS_wait_hms"])
            self.timing_file.flush()
            self._put_gui_msg("log", text=f"Timing log: {timing_path}")

            total_pts = len(self.schedule) * len(self.sweep_frequencies)
            done_pts = 0
            n_steps = len(self.schedule)

            for i, row in enumerate(self.schedule):
                if not self.is_running:
                    break
                target = row["target"]
                self._put_gui_msg("log",
                    text=f"--- Step {i+1}/{n_steps}: target {target} K "
                         f"(PPMS est. {row['ppms_min']:g} min, "
                         f"sleep {row['sleep_min']:g} min) ---")
                self._put_gui_msg("scan_reset")
                self._send_band_msg(target)

                step_start_dt = datetime.now()

                # Phase 1: SLEEP (optional fixed wait)
                sleep_used_s = 0.0
                if self.params["sleep_enabled"] and row["sleep_min"] > 0:
                    outcome, sleep_used_s = self._sleep_phase(
                        target, row["sleep_min"] * 60.0)
                    if outcome == "stopped" or not self.is_running:
                        break

                # Phase 2: WAIT_STABLE (active detection)
                stab_outcome, settle_s = self._wait_for_stability(target)
                if stab_outcome == "stopped" or not self.is_running:
                    break
                if stab_outcome == "timeout":
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ STABILIZATION TIMEOUT at {target} K after "
                             f"{settle_s/60.0:.1f} min — proceeding with the "
                             f"sweep anyway (unattended-run policy). "
                             f"Check this data point!")
                elif stab_outcome == "forced":
                    self._put_gui_msg("log",
                        text=f"⏭ Stability wait skipped by user at {target} K "
                             f"— starting sweep immediately.")

                # Phase 3: SCAN
                self._put_gui_msg("log",
                    text=f"Starting frequency sweep at {target} K "
                         f"(sample reads {self._last_temp:.3f} K).")
                self._put_gui_msg("status",
                    text=f"SCANNING AT {target} K", color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("beep")
                done_pts, scan_s, drift_flag = self._run_frequency_sweep(
                    target, done_pts, total_pts)

                self._write_timing_row(
                    i, row, step_start_dt, sleep_used_s, settle_s,
                    stab_outcome, scan_s, drift_flag)
                if not self.is_running:
                    break
                self._put_gui_msg("log",
                    text=f"Sweep done at {target} K. Proceeding.")

            if self.is_running:
                self._put_gui_msg("log", text="Sequence complete.")
                self._put_gui_msg("status", text="COMPLETE", color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("sequence_complete")

        except Exception as e:
            self._put_gui_msg("log",
                text=f"CRITICAL: {e}\n{traceback.format_exc()}")
            self._put_gui_msg("status", text="ERROR", color=self.CLR_ACCENT_RED)
        finally:
            try:
                self.lcr_backend.close_instrument()
            except Exception as e:
                print(f"LCR shutdown warning: {e}")
            try:
                self.thermo_backend.shutdown()
            except Exception as e:
                print(f"Thermometer shutdown warning: {e}")
            self._close_data_file()
            self.is_running = False
            self._worker_phase = None
            self._paused = False
            self._skip_requested = False
            self._put_gui_msg("worker_done")

    # ------------------------------------------------------------
    # Worker phases
    # ------------------------------------------------------------
    def _send_band_msg(self, target):
        """Tolerance band drawn on the plot: ±tol around the target in the
        band modes; ±guard (if set, else ±2·tol) in pure flatness mode."""
        p = self.params
        if p["mode"] == "flat":
            halfw = p["guard"] if p["guard"] > 0 else 2.0 * p["tol"]
        else:
            halfw = p["tol"]
        self._put_gui_msg("band", center=target, halfw=halfw)

    def _sleep_phase(self, target, sleep_s):
        """Phase 1: fixed wait. Pause freezes the countdown (deadline
        shifts); Skip ends the sleep early. Returns (outcome, used_s);
        outcome in "done" | "skipped" | "stopped"."""
        p = self.params
        self._worker_phase = "SLEEP"
        phase_start = time.time()
        deadline = phase_start + sleep_s
        self._put_gui_msg("log",
            text=f"Sleeping {fmt_hms(sleep_s)} before stability detection "
                 f"at {target} K (skip with 'Skip Phase').")
        try:
            while self.is_running and time.time() < deadline:
                if self._process_cmd_queue():
                    return "stopped", time.time() - phase_start
                if self._skip_requested:
                    self._skip_requested = False
                    self._put_gui_msg("log",
                        text="⏭ Sleep skipped — starting stability detection.")
                    return "skipped", time.time() - phase_start
                self._log_temperature_point(target, measuring_flag=0)
                if self._paused:
                    deadline += p["delay"]   # paused time doesn't count
                    self._put_gui_msg("status",
                        text=f"PAUSED (sleeping, target {target} K)",
                        color=self.CLR_ACCENT_GOLD)
                else:
                    remaining = deadline - time.time()
                    self._put_gui_msg("status",
                        text=(f"SLEEPING — {fmt_hms(remaining)} left "
                              f"(of {fmt_hms(sleep_s)}) | target {target} K"),
                        color=self.CLR_SLEEP)
                time.sleep(p["delay"])
        finally:
            self._worker_phase = None
        return "done", time.time() - phase_start

    def _window_check(self, window, target, p):
        """Rolling-window stability test with three modes.

        Returns (ok, metrics) where metrics is a dict with span, max_dev,
        pkpk, mean, drift (all None until >= 5 points collected).
        ok requires the window to span >= 95% of the configured length,
        plus, per mode:
          band      : max |T - target| <= tol  AND |drift| <= limit
          flat      : peak-to-peak <= 2*tol AND |drift| <= limit AND
                      (guard off OR |mean - target| <= guard)
          flat_band : flatness AND max |T - target| <= tol AND drift
        """
        if len(window) < 5:
            return False, None
        t0 = window[0][0]
        span = window[-1][0] - t0
        temps = np.array([w[1] for w in window])
        times = np.array([w[0] - t0 for w in window])
        max_dev = float(np.max(np.abs(temps - target)))
        pkpk = float(np.max(temps) - np.min(temps))
        mean = float(np.mean(temps))
        drift = 0.0
        if span > 1.0:
            drift = float(np.polyfit(times, temps, 1)[0]) * 60.0  # K/min
        metrics = {"span": span, "max_dev": max_dev, "pkpk": pkpk,
                   "mean": mean, "drift": drift}

        span_ok = span >= 0.95 * p["window_min"] * 60.0
        drift_ok = abs(drift) <= p["drift"]
        band_ok = max_dev <= p["tol"]
        flat_ok = pkpk <= 2.0 * p["tol"]
        guard_ok = p["guard"] <= 0 or abs(mean - target) <= p["guard"]

        mode = p["mode"]
        if mode == "band":
            ok = span_ok and band_ok and drift_ok
        elif mode == "flat":
            ok = span_ok and flat_ok and drift_ok and guard_ok
        else:  # flat_band
            ok = span_ok and flat_ok and band_ok and drift_ok
        return ok, metrics

    def _wait_for_stability(self, target):
        """Phase 2: rolling-window stabilization detection.
        Returns (outcome, wait_s) with outcome in
        "stable" | "timeout" | "forced" | "stopped".
        wait_s excludes paused time."""
        p = self.params
        self._worker_phase = "WAIT_STABLE"
        window = deque()
        phase_start = time.time()
        paused_s = 0.0
        last_status = 0.0
        self._put_gui_msg("status",
            text=f"WAITING FOR STABILITY at {target} K",
            color=self.CLR_STABLE_WAIT)
        try:
            while self.is_running:
                if self._process_cmd_queue():
                    return "stopped", time.time() - phase_start - paused_s
                if self._skip_requested:
                    self._skip_requested = False
                    return "forced", time.time() - phase_start - paused_s

                temp = self._log_temperature_point(target, measuring_flag=0)
                now = time.time()

                if self._paused:
                    paused_s += p["delay"]
                    window.clear()
                    self._put_gui_msg("status",
                        text=f"PAUSED (stability wait, target {target} K)",
                        color=self.CLR_ACCENT_GOLD)
                    time.sleep(p["delay"])
                    continue

                window.append((now, temp))
                soak_s = p["window_min"] * 60.0
                while window and (now - window[0][0]) > soak_s:
                    window.popleft()

                ok, m = self._window_check(window, target, p)
                if ok:
                    wait_s = time.time() - phase_start - paused_s
                    self._put_gui_msg("log",
                        text=f"STABLE at {target} K after {fmt_hms(wait_s)}: "
                             f"mean {m['mean']:.3f} K, p-p {m['pkpk']:.3f} K, "
                             f"drift {m['drift']:+.3f} K/min, max dev from "
                             f"target {m['max_dev']:.3f} K over last "
                             f"{p['window_min']:g} min "
                             f"[{p['mode']} criterion].")
                    return "stable", wait_s
                if now - last_status > 3.0:
                    last_status = now
                    if m is not None:
                        fill = min(100.0, 100.0 * m["span"]
                                   / (p["window_min"] * 60.0))
                        self._put_gui_msg("status",
                            text=(f"WAITING STABILITY {target} K | "
                                  f"window {fill:.0f}% | "
                                  f"p-p {m['pkpk']:.3f} K | "
                                  f"drift {m['drift']:+.3f} K/min"),
                            color=self.CLR_STABLE_WAIT)

                if p["stab_timeout"] > 0 and \
                        (now - phase_start - paused_s) > p["stab_timeout"] * 60.0:
                    return "timeout", time.time() - phase_start - paused_s

                time.sleep(p["delay"])
        finally:
            self._worker_phase = None
        return "stopped", time.time() - phase_start - paused_s

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
                elif kind == "pause":
                    self._paused = True
                    self._put_gui_msg("pause_state", paused=True)
                    self._put_gui_msg("log", text="PAUSED by user.")
                elif kind == "resume":
                    self._paused = False
                    self._put_gui_msg("pause_state", paused=False)
                    self._put_gui_msg("log", text="RESUMED.")
                elif kind == "skip":
                    self._skip_requested = True
                elif kind == "params":
                    updates = cmd[1]
                    self.params.update(updates)
                    self._put_gui_msg("log", text=f"Params applied: {updates}")
        except queue.Empty:
            pass
        return False

    _last_temp = float("nan")

    def _log_temperature_point(self, target, measuring_flag):
        """Reads the probe thermometer, writes a CSV row, queues the plot
        message. Read-only — no safety actions exist because this program
        controls no heater."""
        temp = self.thermo_backend.get_temperature(self.params["channel"])
        self._last_temp = temp
        elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        phase = "PAUSED" if self._paused else (self._worker_phase or "")
        self.csv_writer.writerow(
            [now_str, f"{elapsed:.2f}", f"{target:.4f}", f"{temp:.4f}",
             measuring_flag, phase]
        )
        self.data_file.flush()
        self._put_gui_msg("temp_point", t=elapsed, temp=temp, target=target,
                          measuring=measuring_flag)
        return temp

    def _run_frequency_sweep(self, target_temp, done_pts, total_pts):
        """Runs the E4980A frequency sweep at one stable setpoint.
        Logs temperature (flag=1) interleaved between frequency points.
        Watches for the sample temperature leaving the stability band
        mid-sweep (flag-only: the sweep always completes).
        Returns (done_pts, scan_s, drift_flag)."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = (f"{self.lcr_params['sample_name']}_{target_temp:.2f}K_"
                 f"{stamp}_FreqScan.txt")
        fpath = os.path.join(self.save_dir, fname)
        sweep_start = time.time()
        paused_s = 0.0
        drift_flag = False
        # Drift reference: the temperature the sweep started at (flatness
        # modes settle at an offset from the target, so the target itself
        # is the wrong reference there).
        ref_T = target_temp if self.params["mode"] == "band" \
            else self._last_temp
        drift_halfw = 2.0 * self.params["tol"]
        n_measured = 0
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(f"# Sample: {self.lcr_params['sample_name']} | T_set = {target_temp} K | "
                    f"AC: {self.lcr_params['ac_bias']} V | DC: {self.lcr_params['dc_bias']} V | "
                    f"APER: {self.lcr_params['aper']}\n")
            f.write(self.lcr_backend.DATA_HEADER + "\n")
            self._worker_phase = "SCAN"
            n_freqs = len(self.sweep_frequencies)
            for i, freq in enumerate(self.sweep_frequencies):
                if not self.is_running:
                    break
                if self._process_cmd_queue():
                    break
                if not self.is_running:
                    break
                if self._skip_requested:
                    self._skip_requested = False
                    self._put_gui_msg("log",
                        text="⏭ Remaining sweep skipped by user.")
                    break
                while self._paused and self.is_running:
                    pause_tick = time.time()
                    self._put_gui_msg("status",
                        text=f"PAUSED (sweep at {target_temp} K)",
                        color=self.CLR_ACCENT_GOLD)
                    if self._process_cmd_queue():
                        break
                    time.sleep(1.0)
                    paused_s += time.time() - pause_tick
                if not self.is_running:
                    break
                try:
                    R, X, status = self.lcr_backend.perform_measurement(
                        freq, self.lcr_params["delay"]
                    )
                except Exception as e:
                    self._put_gui_msg("log", text=f"Meas error @ {freq} Hz: {e}")
                    break
                if status != 0:
                    self._put_gui_msg("log",
                        text=f"⚠️ E4980A status {status} @ {freq:.1f} Hz — row kept, check manual")
                vals = self.calculate_impedance_parameters(freq, R, X)
                row = [freq] + vals + [target_temp]
                f.write("\t".join(f"{v:.6E}" for v in row) + "\n")
                f.flush()
                n_measured += 1
                # Interleaved temperature log (flag=1) + mid-sweep drift watch
                try:
                    temp = self._log_temperature_point(target_temp,
                                                       measuring_flag=1)
                    if not drift_flag and abs(temp - ref_T) > drift_halfw:
                        drift_flag = True
                        self._put_gui_msg("log",
                            text=f"⚠️⚠️ TEMPERATURE DRIFT DURING SCAN at "
                                 f"{target_temp} K: sample moved to "
                                 f"{temp:.3f} K (> ±{drift_halfw:g} K from "
                                 f"{ref_T:.3f} K). PPMS probably moved on — "
                                 f"increase its wait time. Sweep continues; "
                                 f"step is flagged in the timing log.")
                except RuntimeError:
                    break
                done_pts += 1
                self._put_gui_msg("scan_point", freq=freq, cp=vals[4], g=vals[2],
                                  progress=done_pts)
                # Throttled ETA in the banner (every 10 points)
                if i % 10 == 0 and i > 0:
                    per_pt = (time.time() - sweep_start - paused_s) / max(n_measured, 1)
                    eta = per_pt * (n_freqs - i - 1)
                    self._put_gui_msg("status",
                        text=(f"SCANNING AT {target_temp} K | "
                              f"pt {i+1}/{n_freqs} | ETA {fmt_hms(eta)}"),
                        color=self.CLR_ACCENT_GREEN)
        self._worker_phase = None
        scan_s = time.time() - sweep_start - paused_s
        if n_measured >= 20:
            # Refine the live scan estimate with real per-point timing
            self._put_gui_msg("point_time", avg=scan_s / n_measured)
        self._put_gui_msg("log", text=f"Sweep saved: {fname} "
                                      f"({n_measured} pts, {fmt_hms(scan_s)}).")
        return done_pts, scan_s, drift_flag

    def _write_timing_row(self, index, row, step_start_dt, sleep_s,
                          settle_s, stab_outcome, scan_s, drift_flag):
        """One row per completed step: measured timings + the PPMS wait
        suggestion. Written to the TimingLog CSV and mirrored into the
        Timing / PPMS Suggestions tab."""
        p = self.params
        ppms_est_s = row["ppms_min"] * 60.0
        base = max(0.0, sleep_s + settle_s - ppms_est_s) + scan_s
        margin = max(base * p["margin_pct"] / 100.0,
                     p["margin_floor"] * 60.0)
        suggest_s = base + margin
        status = {"stable": "stable", "timeout": "TIMEOUT — check data",
                  "forced": "forced by user"}.get(stab_outcome, stab_outcome)
        if drift_flag:
            status += " | DRIFT DURING SCAN"
        try:
            self.timing_writer.writerow(
                [index + 1, f"{row['target']:.4f}",
                 step_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                 f"{sleep_s:.1f}", f"{settle_s:.1f}", stab_outcome,
                 f"{scan_s:.1f}", int(drift_flag), f"{ppms_est_s:.1f}",
                 f"{suggest_s:.1f}", fmt_hms(suggest_s)])
            self.timing_file.flush()
        except Exception as e:
            self._put_gui_msg("log", text=f"WARN: timing write failed: {e}")
        self._put_gui_msg("timing_row", index=index, target=row["target"],
                          sleep_s=sleep_s, settle_s=settle_s, scan_s=scan_s,
                          suggest_s=suggest_s, status=status)
        self._put_gui_msg("log",
            text=f"→ Suggested PPMS wait at {row['target']} K: "
                 f"{fmt_hms(suggest_s)} (probe settle beyond PPMS ramp "
                 f"{fmt_hms(max(0.0, sleep_s + settle_s - ppms_est_s))} "
                 f"+ scan {fmt_hms(scan_s)} + margin).")

    def _close_data_file(self):
        for attr, label in (("data_file", "Temperature log"),
                            ("timing_file", "Timing log")):
            f = getattr(self, attr, None)
            if f:
                try:
                    f.flush(); f.close()
                    self._put_gui_msg("log", text=f"{label} closed.")
                except Exception:
                    pass
                finally:
                    setattr(self, attr, None)

    # ------------------------------------------------------------
    # Shutdown / close
    # ------------------------------------------------------------
    def _atexit_shutdown(self):
        try:
            self.lcr_backend.close_instrument()
        except Exception:
            pass
        try:
            self.thermo_backend.shutdown()
        except Exception:
            pass

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Sequence is running. Stop and exit?"):
                self.stop_sequence("User closed application.")
                self._close_deadline = time.time() + 15.0
                self._poll_worker_exit_then_destroy()
        else:
            self.root.destroy()

    def _poll_worker_exit_then_destroy(self):
        t = self.worker_thread
        if (
            t is not None
            and t.is_alive()
            and time.time() < self._close_deadline
        ):
            self.root.after(200, self._poll_worker_exit_then_destroy)
            return
        if t is not None and t.is_alive():
            self.log("WARNING: worker did not exit within timeout; "
                     "closing anyway (atexit will clean up instruments).")
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
    PPMSSyncGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
