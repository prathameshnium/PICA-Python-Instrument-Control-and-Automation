"""
Module: T_Control_L350_Step_GUI_advanced.py
Purpose: ADVANCED Lakeshore 350 temperature step-sequence controller.
         Fully self-contained (no imports from other PICA programs).

This program supersedes T_Control_L350_Step_GUI.py for demanding runs
(the classic script is kept unchanged for existing workflows). It embeds
the newer, field-tested stabilization logic from the v1.2 overhaul of
Step_Frequency_Scan_E4980A_GUI.py (STB-1/2/3, REL-1, FRZ-1..5) and adds
adaptive ramping and safety features for the LN2-dewar probe setup.

============================================================
FEATURES
============================================================
STABILIZATION (embedded from the proven v1.2 logic):
  * TWO-STAGE APPROACH (STB-1): if the target is farther than
    `Approach Band` (K) away, ramp at the computed step rate to a
    PRE-TARGET that stops `Approach Band` short of the target, then
    switch to the slow `Approach Rate` (K/min) for the final approach.
    The setpoint never slews into the target at full speed -> greatly
    reduced overshoot (worst below 100 K).
  * ROLLING-WINDOW STABILITY (STB-2): stable = over the last
    `Window` (s): max |T - target| <= Tolerance AND |linear drift| <=
    Drift Limit (K/min). Noise spikes slide out of the window instead
    of resetting the soak.
  * STABILIZATION TIMEOUT (STB-3): `Timeout` (min, 0 = off). On
    timeout a loud warning is logged and the program still beeps and
    waits for Proceed so the user decides.

ADAPTIVE RAMP RATE (new):
    proposed = |target - current| * Step Factor        (bigger step -> faster)
    cap      = low-T cap table looked up at min(current, target)
               (the coldest temperature the ramp traverses governs)
    rate     = clamp(min(proposed, cap), Min Rate .. Max Rate)
    first setpoint of a run: rate *= First-Step Factor (overshoot is
    worst on the first point, so it is approached extra slowly)

    Cap table syntax (editable in the GUI):  "77:0.3, 100:0.5, else:5"
      -> below 77 K max 0.3 K/min; below 100 K max 0.5 K/min;
         otherwise max 5 K/min.
    THE CAP TABLE IS A GLOBAL SAFETY ENVELOPE: it clamps EVERY rate the
    program sends to the Lakeshore — the adaptive rate, the fixed rate,
    the slow approach rate, and live mid-run rate updates (`capped_rate`
    helper; a log line reports every clamp). Min Rate cannot override a
    cap either (it floors the proposed rate BEFORE the cap is applied).
    Rationale: on the LN2-dewar probe even a small heater input raises
    the temperature a lot below 100 K; medium/low PID still overshoots
    unless the setpoint ramp itself is very slow.

    Worked examples (defaults: factor 0.2, caps 77:0.3/100:0.5/else:5):
      10 K step at  80 K -> min(10*0.2, 0.5) = 0.5 K/min
      10 K step at 150 K -> min(10*0.2, 5.0) = 2.0 K/min
      50 K step at 250 K -> min(50*0.2, 5.0) = 5.0 K/min (ceiling)
    The "Temperature Ramp" panel selects the rate source with radio
    buttons (Adaptive / Fixed); the inactive group is greyed out so it
    is always obvious which rate is in effect.

APPROACH FROM ONE SIDE (new, for hysteresis-sensitive measurements):
    "Always from below"/"Always from above" forces the pre-target onto
    the chosen side of the target, so the slow final ramp always comes
    from that side (e.g. from below: when cooling, first go to
    target - band, then ramp slowly UP into the target).
    "From above" setpoints whose pre-target (T + band) would exceed the
    soft Max Temp limit are rejected at Start (fail early instead of a
    SoftLimitAbort in the middle of an overnight run).

SAFETY:
  * Hardcoded 340 K kill switch (heater off + abort) — not in the GUI.
  * User-configurable soft Max Temp limit (<= 340 K): graceful abort
    (stop ramp, heater off) if exceeded; setpoints above it are
    rejected at Start. Heater range 0 (off) is rejected at Start.
  * Crash-safe: worker `finally` + atexit always stop the ramp and
    turn the heater off; window close is non-blocking (polls worker).

CONTROL / GUI:
  * Non-blocking worker thread; two queues (cmd_queue GUI->worker,
    gui_queue worker->GUI); the worker is the only writer of
    is_running. Pause/Resume (holds the current setpoint, suspends the
    stability window + timeout clock) and Skip Step (abandons the
    current setpoint) are available mid-run.
  * After each stabilization the program ALWAYS beeps and waits for
    the Proceed button (external measurement handshake).
  * Live plot: temperature + heater vs time, target line, +/-Tolerance
    band for the current setpoint, Matplotlib toolbar, X-min window
    control. Throttled redraw + display decimation (FRZ-1/2) so
    overnight runs cannot freeze the GUI.
  * Dynamic PID per setpoint (optional): Slow PID below 100 K,
    Medium above; live PID panel still available.
  * Sequence editable while running (append/remove upcoming steps).
  * Logging: main CSV (per poll) with ramp rate + phase columns, and a
    per-setpoint summary CSV (rate used, stabilization time, outcome).
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, Canvas
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

# --- Optional Packages ---
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


def run_script_process(script_path):
    """Wrapper function to execute a script using runpy in its own directory."""
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)


def launch_plotter_utility():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(script_dir, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror("File Not Found", plotter_path)
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py"
        )
        if not os.path.exists(scanner_path):
            messagebox.showerror("File Not Found", scanner_path)
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


# ===============================================================================
# --- STABILIZATION / ADAPTIVE-RAMP HELPERS (pure functions, unit-testable) ---
# (embedded copy of the v1.2 logic from Step_Frequency_Scan_E4980A_GUI.py)
# ===============================================================================

def window_check(window, target, tol, window_s, drift_limit):
    """Rolling-window stability test (STB-2).

    `window` = iterable of (time_s, temp_K), already trimmed to window_s.
    Returns (ok, max_dev, drift_K_per_min).
    ok requires: span >= 95% of window_s, max |T - target| <= tol,
    AND |linear drift| <= drift_limit (K/min).
    """
    window = list(window)
    if len(window) < 5:
        return False, None, None
    t0 = window[0][0]
    span = window[-1][0] - t0
    temps = np.array([w[1] for w in window])
    times = np.array([w[0] - t0 for w in window])
    max_dev = float(np.max(np.abs(temps - target)))
    drift = 0.0
    if span > 1.0:
        drift = float(np.polyfit(times, temps, 1)[0]) * 60.0  # K/min
    ok = (span >= 0.95 * window_s
          and max_dev <= tol
          and abs(drift) <= drift_limit)
    return ok, max_dev, drift


def parse_ramp_table(text):
    """Parse "77:0.3, 100:0.5, else:5" -> (sorted caps list, default cap).

    Raises ValueError with a readable message on malformed input.
    """
    caps = []
    default_cap = None
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"Ramp table entry '{chunk}' must be 'T:rate'.")
        key, val = (s.strip() for s in chunk.split(":", 1))
        rate = float(val)
        if rate <= 0:
            raise ValueError(f"Ramp table rate must be positive: '{chunk}'.")
        if key.lower() in ("else", "default", "*"):
            default_cap = rate
        else:
            caps.append((float(key), rate))
    if default_cap is None:
        raise ValueError("Ramp table needs an 'else:<rate>' entry.")
    if not caps:
        raise ValueError("Ramp table needs at least one 'T:rate' entry.")
    return sorted(caps), default_cap


def ramp_cap_for(temperature_K, caps, default_cap):
    """Low-T cap lookup: first (T_upper, cap) with temperature < T_upper."""
    for t_upper, cap in caps:
        if temperature_K < t_upper:
            return cap
    return default_cap


def compute_ramp_rate(current_T, target_T, p, is_first_step=False):
    """Adaptive ramp rate for one step (formula in the module docstring).

    `p` is the validated params dict (keys: step_factor, ramp_caps,
    ramp_default_cap, min_rate, max_rate, first_factor).
    Returns (rate_K_per_min, reason_str) — reason_str goes to the log so
    the user sees why the rate was chosen.

    Clamp order: Min Rate floors the PROPOSED rate before the low-T cap
    and Max Rate are applied, so the caps are hard ceilings that neither
    Min Rate nor the first-step factor can override.
    """
    step = abs(target_T - current_T)
    proposed = step * p["step_factor"]
    coldest = min(current_T, target_T)
    cap = ramp_cap_for(coldest, p["ramp_caps"], p["ramp_default_cap"])
    rate = min(max(proposed, p["min_rate"]), cap, p["max_rate"])
    reason = (f"step {step:.1f} K x {p['step_factor']:g} = {proposed:.2f} "
              f"K/min, low-T cap @ {coldest:.1f} K = {cap:g} K/min")
    if is_first_step and p["first_factor"] != 1.0:
        rate *= p["first_factor"]
        reason += f", first-step x{p['first_factor']:g}"
    rate = min(rate, cap, p["max_rate"])
    reason += f" -> {rate:.2f} K/min"
    return rate, reason


def capped_rate(requested, current_T, target_T, p, label=""):
    """Clamp ANY rate sent to the Lakeshore to the low-T cap table.

    The table is a global safety envelope: it also governs the fixed
    (manual) rate and the slow approach rate, not just the adaptive
    rate. The cap is looked up at min(current_T, target_T) — the
    coldest temperature the ramp traverses.
    Returns (rate_K_per_min, clamp_message_or_None).
    """
    coldest = min(current_T, target_T)
    cap = ramp_cap_for(coldest, p["ramp_caps"], p["ramp_default_cap"])
    if requested > cap:
        return cap, (f"{label} rate {requested:g} K/min capped to "
                     f"{cap:g} K/min by low-T table @ {coldest:.1f} K")
    return requested, None


class SoftLimitAbort(RuntimeError):
    """Raised when the user-configured soft Max Temp limit is exceeded."""


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL (v1.2 backend: retries + kill switch) ---
# -------------------------------------------------------------------------------

class Lakeshore_Backend:
    HARD_TEMP_LIMIT_K = 340.0  # hardcoded kill switch — not in the GUI

    def __init__(self):
        self.lakeshore = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.write("*CLS")  # do NOT *RST mid-run
        idn = self.lakeshore.query("*IDN?").strip()
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
        self.set_heater_range(1, heater_range)
        self.lakeshore.write(f"RAMP 1,1,{rate}")  # enable ramp FIRST
        time.sleep(0.1)
        self.lakeshore.write(f"SETP 1,{setpoint}")

    def set_ramp_rate(self, rate):
        """Change ramp rate without touching heater range or setpoint."""
        self.lakeshore.write(f"RAMP 1,1,{rate}")

    def set_setpoint(self, setpoint):
        self.lakeshore.write(f"SETP 1,{setpoint}")

    def get_status(self, retries=2):
        """REL-1: retry transient VISA glitches so a single failed query
        at 3 a.m. does not abort an overnight run. Persistent failures
        still raise (the worker's except/finally shuts down safely)."""
        last_err = None
        for attempt in range(retries + 1):
            try:
                temp = float(self.lakeshore.query("KRDG? A").strip())
                resistance = float(self.lakeshore.query("SRDG? A").strip())
                htr_output = float(self.lakeshore.query("HTR? 1").strip())
                return temp, resistance, htr_output
            except Exception as e:
                last_err = e
                if attempt < retries:
                    print(f"get_status retry {attempt+1}: {e}")
                    try:
                        self.lakeshore.clear()   # VISA device clear
                    except Exception:
                        pass
                    time.sleep(0.5)
        raise last_err

    def set_pid(self, output, p, i, d):
        if not (0 <= p <= 9999 and 0 <= i <= 1000 and 0 <= d <= 200):
            raise ValueError("PID values out of range.")
        # NOTE: LS350 treats I=0 / D=0 as "off". Do not set I=0 unless you
        # intend to disable integral action (permanent offset).
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


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------

class TempControlAdvancedGUI:
    PROGRAM_VERSION = "1.0-Step-Advanced"

    # --- FRZ-1 / FRZ-2 / FRZ-4 tuning knobs ---
    REDRAW_MS = 750           # plot redraw interval (ms), one redraw per tick
    MAX_PLOT_POINTS = 4000    # displayed points per series before decimation
    MAX_MSGS_PER_CYCLE = 300  # gui_queue messages processed per 50 ms tick

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
    FONT_BASE = ("Segoe UI", 10)
    FONT_TITLE = ("Segoe UI", 12, "bold")
    FONT_CONSOLE = ("Consolas", 9)

    LEFT_PANEL_WIDTH = 540

    # Dynamic PID presets (applied per setpoint when Auto-PID is on)
    PID_SLOW = (0.5, 4.0, 0)      # below 100 K
    PID_MEDIUM = (20.0, 15.0, 0)  # 100 K and above

    APPROACH_MODES = {
        "Two-stage (default)": None,
        "Always from below": "below",
        "Always from above": "above",
    }

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Lakeshore 350 ADVANCED Step Control v{self.PROGRAM_VERSION}"
        )
        self.root.geometry("1500x900")
        self.root.minsize(1250, 800)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = Lakeshore_Backend()

        self.is_running = False          # written ONLY by the worker thread
        self.worker_thread = None
        self.cmd_queue = queue.Queue()   # GUI -> worker
        self.gui_queue = queue.Queue()   # worker -> GUI

        atexit.register(self._atexit_shutdown)

        self.logo_image = None
        self.save_dir = ""   # chosen by the user via Browse Save…

        # Plot data — main thread only
        self.plot_t = []
        self.plot_temp = []
        self.plot_target = []
        self.plot_heater = []
        self._temp_plot_dirty = False
        self._band_params = None       # (target, tol) of the current setpoint
        self._band_dirty = False
        self.band_patch = None

        # Worker-side state (worker thread only writes these)
        self._phase = ""               # PRE_RAMP/FINAL_APPROACH/WAITING/PAUSED
        self._paused = False
        self._current_rate = 0.0
        self._current_target = None    # setpoint being worked on (for caps)
        self._pause_pending = None     # GUI request, consumed by worker

        # Dynamic step-sequence state (editable while running)
        self.setpoint_lock = threading.Lock()
        self.setpoint_floats = []
        self.current_step_index = 0

        self.PID_PRESETS = {
            "Slow (P=0.5, I=4, D=0)": self.PID_SLOW,
            "Medium (P=20, I=15, D=0)": self.PID_MEDIUM,
            "Fast (P=50, I=20, D=0)": (50.0, 20.0, 0),
        }

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(self.REDRAW_MS, self._redraw_tick)
        self.log(f"Advanced Step Control v{self.PROGRAM_VERSION} initialized.")
        self.log("Stabilization: two-stage approach + rolling-window "
                 "(tolerance AND drift) criterion. 340 K kill switch active.")
        self.log("Ramp: adaptive or fixed (radio buttons); the low-T cap "
                 "table clamps EVERY rate incl. fixed + approach "
                 "(defaults: <77 K: 0.3, <100 K: 0.5, else 5 K/min).")

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
                        foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor="none")
        style.map("TButton",
                  background=[("active", self.CLR_ACCENT_GOLD),
                              ("hover", self.CLR_ACCENT_GOLD)])
        style.configure("Start.TButton", background=self.CLR_ACCENT_GREEN)
        style.configure("Stop.TButton", background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FRAME_BG)
        style.configure("Proceed.TButton", font=("Segoe UI", 12, "bold"),
                        background=self.CLR_ACCENT_GREEN)
        style.configure("TLabelframe", background=self.CLR_FRAME_BG,
                        bordercolor="#BA6B5E")
        style.configure("TLabelframe.Label", background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure("TEntry", fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure("TCheckbutton", background=self.CLR_FRAME_BG)
        style.configure("TRadiobutton", background=self.CLR_FRAME_BG)
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
        ttk.Label(header,
                  text="Lakeshore 350 Advanced Step Sequence Utility",
                  style="Header.TLabel",
                  font=("Segoe UI", 14, "bold"),
                  foreground=self.CLR_ACCENT_GOLD
                  ).pack(side="left", padx=20, pady=10)
        ttk.Button(header, text="📈", command=launch_plotter_utility,
                   width=3).pack(side="right", padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner,
                   width=3).pack(side="right", padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = sf
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        sf.grid_columnconfigure(0, weight=1)
        sf.grid_rowconfigure(7, weight=1)

        self._create_info_panel(sf, 0)
        self._create_sequence_panel(sf, 1)
        self._create_stability_panel(sf, 2)
        self._create_ramp_panel(sf, 3)
        self._create_safety_panel(sf, 4)
        self._create_pid_panel(sf, 5)
        self._create_action_panel(sf, 6)
        self._create_console_panel(sf, 7)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Information")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 80
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE,
                             bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), RESAMPLE_FILTER)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception:
            pass
        f = ("Segoe UI", 12, "bold")
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=f, background=self.CLR_FRAME_BG
                  ).grid(row=0, column=1, padx=5, pady=(12, 0), sticky="sw")
        ttk.Label(frame, text="Mumbai Centre", font=f,
                  background=self.CLR_FRAME_BG
                  ).grid(row=1, column=1, padx=5, sticky="nw")

    def _create_sequence_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Measurement Sequence Builder")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=0, column=0, columnspan=4, sticky="nsew",
                        padx=10, pady=5)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(list_frame, height=6, selectmode=tk.EXTENDED,
                                  font=self.FONT_BASE, bg=self.CLR_INPUT_BG,
                                  fg=self.CLR_TEXT_DARK,
                                  yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(frame, text="Start(K):").grid(row=1, column=0, sticky="e", padx=2)
        self.entry_start = ttk.Entry(frame, width=6)
        self.entry_start.grid(row=1, column=1, sticky="w", padx=2)
        ttk.Label(frame, text="End(K):").grid(row=1, column=2, sticky="e", padx=2)
        self.entry_end = ttk.Entry(frame, width=6)
        self.entry_end.grid(row=1, column=3, sticky="w", padx=2)
        ttk.Label(frame, text="Step(K):").grid(row=2, column=0, sticky="e", padx=2)
        self.entry_step = ttk.Entry(frame, width=6)
        self.entry_step.grid(row=2, column=1, sticky="w", padx=2)
        self.btn_generate_steps = ttk.Button(
            frame, text="Generate Steps", command=self._generate_steps)
        self.btn_generate_steps.grid(row=2, column=2, columnspan=2,
                                     sticky="ew", padx=5, pady=2)

        ttk.Separator(frame, orient="horizontal").grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=5, padx=10)

        ttk.Label(frame, text="Order:").grid(row=4, column=0, sticky="e", padx=2)
        self.sort_var = tk.StringVar(value="Ascending")
        self.sort_cb = ttk.Combobox(frame, textvariable=self.sort_var,
                                    values=["Ascending", "Descending"],
                                    state="readonly", width=10)
        self.sort_cb.grid(row=4, column=1, sticky="w", padx=2)
        self.sort_cb.bind("<<ComboboxSelected>>", lambda e: self._sort_listbox())

        ttk.Label(frame, text="Manual(K):").grid(row=5, column=0, sticky="e",
                                                 padx=2, pady=5)
        self.entry_manual = ttk.Entry(frame, width=6)
        self.entry_manual.grid(row=5, column=1, sticky="w", padx=2, pady=5)
        ttk.Button(frame, text="Add", command=self._add_manual_step).grid(
            row=5, column=2, sticky="ew", padx=2, pady=5)
        ttk.Button(frame, text="Remove", command=self._remove_step).grid(
            row=5, column=3, sticky="ew", padx=2, pady=5)
        ttk.Button(frame, text="Clear All", command=self._clear_listbox).grid(
            row=6, column=0, columnspan=4, sticky="ew", padx=10, pady=(0, 5))

        self.lbl_seq_hint = ttk.Label(frame, text="",
                                      foreground=self.CLR_ACCENT_GOLD,
                                      font=("Segoe UI", 8, "italic"))
        self.lbl_seq_hint.grid(row=7, column=0, columnspan=4, sticky="w", padx=10)

    def _create_stability_panel(self, parent, grid_row):
        """When do we call the temperature stable (STB-2/3)."""
        frame = ttk.LabelFrame(parent, text="Stabilization Criteria")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)
        self.entries = {}
        self._create_grid_entry(frame, "Tolerance (±K):", "tol", "0.5", 0, 0)
        self._create_grid_entry(frame, "Window (s):", "soak", "120", 0, 3)
        self._create_grid_entry(frame, "Drift Lim (K/min):", "drift", "0.10", 1, 0)
        self._create_grid_entry(frame, "Timeout (min, 0=off):",
                                "stab_timeout", "90", 1, 3)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "1", 2, 0)

    def _create_ramp_panel(self, parent, grid_row):
        """How we travel to a setpoint: rate source + final approach.
        Exactly ONE rate source (adaptive or fixed) is active; the other
        group is greyed out so it is always obvious which rate wins."""
        frame = ttk.LabelFrame(parent, text="Temperature Ramp")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)

        self.ramp_mode_var = tk.StringVar(value="adaptive")
        self.rb_adaptive = ttk.Radiobutton(
            frame, text="Adaptive rate (recommended)",
            variable=self.ramp_mode_var, value="adaptive",
            command=self._on_ramp_mode_changed)
        self.rb_adaptive.grid(row=0, column=0, columnspan=3, sticky="w",
                              padx=10, pady=(5, 2))
        self.rb_fixed = ttk.Radiobutton(
            frame, text="Fixed rate", variable=self.ramp_mode_var,
            value="fixed", command=self._on_ramp_mode_changed)
        self.rb_fixed.grid(row=0, column=3, columnspan=3, sticky="w",
                           padx=10, pady=(5, 2))

        self._create_grid_entry(frame, "Step Factor:", "step_factor", "0.2", 1, 0)
        self._create_grid_entry(frame, "First-Step x:", "first_factor", "0.5", 1, 3)
        self._create_grid_entry(frame, "Min Rate (K/min):", "min_rate", "0.1", 2, 0)
        self._create_grid_entry(frame, "Max Rate (K/min):", "max_rate", "5.0", 2, 3)
        self._create_grid_entry(frame, "Fixed Rate (K/min):", "rate", "2.0", 3, 0)

        ttk.Label(frame, text="Low-T caps — all modes (K:K/min):").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 2))
        self.ramp_table_var = tk.StringVar(value="77:0.3, 100:0.5, else:5")
        self.ramp_table_entry = ttk.Entry(frame, textvariable=self.ramp_table_var,
                                          font=self.FONT_BASE)
        self.ramp_table_entry.grid(row=4, column=2, columnspan=4, sticky="ew",
                                   padx=(2, 10), pady=(5, 2))
        ttk.Label(frame,
                  text="Hard ceiling on EVERY rate sent (adaptive, fixed and "
                       "approach). e.g. below 77 K max 0.3 K/min.",
                  font=("Segoe UI", 8, "italic")
                  ).grid(row=5, column=0, columnspan=6, sticky="w",
                         padx=10, pady=(0, 5))

        ttk.Separator(frame, orient="horizontal").grid(
            row=6, column=0, columnspan=6, sticky="ew", padx=10, pady=4)

        self._create_grid_entry(frame, "Approach Band (K):", "app_band", "3.0", 7, 0)
        self._create_grid_entry(frame, "Approach Rate (K/min):", "app_rate", "2.0", 7, 3)
        ttk.Label(frame, text="Approach Mode:").grid(row=8, column=0,
                                                     sticky="w", padx=10, pady=5)
        self.approach_var = tk.StringVar(value="Two-stage (default)")
        self.approach_cb = ttk.Combobox(
            frame, textvariable=self.approach_var,
            values=list(self.APPROACH_MODES.keys()),
            state="readonly", width=20)
        self.approach_cb.grid(row=8, column=1, columnspan=4,
                              sticky="ew", padx=5, pady=5)

        self._on_ramp_mode_changed()

    # Rate-source groups for greying (only the active source is editable)
    ADAPTIVE_KEYS = ("step_factor", "first_factor", "min_rate", "max_rate")
    FIXED_KEYS = ("rate",)

    def _on_ramp_mode_changed(self):
        adaptive = self.ramp_mode_var.get() == "adaptive"
        for key in self.ADAPTIVE_KEYS:
            self._set_entry_enabled(key, adaptive)
        for key in self.FIXED_KEYS:
            self._set_entry_enabled(key, not adaptive)

    def _set_entry_enabled(self, key, enabled):
        w = self.entries.get(key)
        if not w:
            return
        if enabled and not w["locked"]:
            w["entry"].config(state="normal")
        else:
            w["entry"].config(state="disabled")

    def _create_safety_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Instrument & Safety")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)

        self._create_grid_entry(frame, "Max Temp (K):", "max_temp", "320", 0, 0)

        ttk.Label(frame, text="Heater Range:").grid(row=0, column=3,
                                                    sticky="w", padx=10, pady=5)
        self.heater_range_var = tk.StringVar(value="5")
        self.heater_cb = ttk.Combobox(
            frame, textvariable=self.heater_range_var,
            values=["0 (Off)", "1", "2", "3", "4", "5 (Max)"],
            state="readonly", width=8)
        self.heater_cb.grid(row=0, column=4, columnspan=2, sticky="ew", padx=5)
        self.heater_cb.bind("<<ComboboxSelected>>", self._on_heater_range_changed)

        self.var_auto_pid = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame,
                        text="Auto PID per setpoint (Slow <100 K, Medium ≥100 K)",
                        variable=self.var_auto_pid
                        ).grid(row=1, column=0, columnspan=6, sticky="w",
                               padx=10, pady=2)

        ttk.Label(frame, text="VISA Addr:").grid(row=2, column=0, sticky="w",
                                                 padx=10, pady=5)
        self.ls_cb = ttk.Combobox(frame, state="readonly", width=18)
        self.ls_cb.grid(row=2, column=1, columnspan=3, sticky="ew", padx=5)
        ttk.Button(frame, text="Scan VISA", command=self._scan_for_visa).grid(
            row=2, column=4, columnspan=2, sticky="ew", padx=5)

        ttk.Button(frame, text="Browse Save…", command=self._browse_save).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(2, 2))
        self.save_dir_lbl = ttk.Label(frame, text="Save dir: (not set)",
                                      foreground=self.CLR_ACCENT_GOLD,
                                      font=("Segoe UI", 8))
        self.save_dir_lbl.grid(row=3, column=2, columnspan=4, sticky="w", padx=5)

    def _create_pid_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Live PID Tuning (Output 1)")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        ttk.Label(frame, text="Preset:").grid(row=0, column=0, sticky="w",
                                              padx=10, pady=5)
        self.pid_preset_var = tk.StringVar()
        pid_cb = ttk.Combobox(frame, textvariable=self.pid_preset_var,
                              values=list(self.PID_PRESETS.keys()) + ["Custom"],
                              state="readonly")
        pid_cb.grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        pid_cb.bind("<<ComboboxSelected>>", self._on_pid_preset_change)
        self.pid_p_entry = self._mk_entry(frame, "P:", "50.0", 1, 0)
        self.pid_i_entry = self._mk_entry(frame, "I:", "30.0", 1, 2)
        self.pid_d_entry = self._mk_entry(frame, "D:", "0.0", 2, 0)
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=6, sticky="ew", pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send PID", command=self._send_pid).grid(
            row=0, column=0, sticky="ew", padx=5)
        ttk.Button(btn_frame, text="Read PID", command=self._read_pid).grid(
            row=0, column=1, sticky="ew", padx=5)

    def _create_action_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Run Control")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.start_button = ttk.Button(frame, text="Start Sequence",
                                       style="Start.TButton",
                                       command=self.start_sequence)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        self.stop_button = ttk.Button(frame, text="Stop All",
                                      style="Stop.TButton", state="disabled",
                                      command=self.stop_sequence)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(frame, text="Apply Live Updates",
                   command=self._send_live_updates
                   ).grid(row=0, column=2, sticky="ew", padx=5, pady=5)

        self.pause_button = ttk.Button(frame, text="Pause", state="disabled",
                                       command=self._toggle_pause)
        self.pause_button.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 5))
        self.skip_button = ttk.Button(frame, text="Skip Step", state="disabled",
                                      command=self._skip_step)
        self.skip_button.grid(row=1, column=1, sticky="ew", padx=5, pady=(0, 5))

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Console Log")
        frame.grid(row=grid_row, column=0, sticky="nsew", pady=5, padx=5)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            frame, state="disabled", bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap="word",
            borderwidth=0)
        self.console.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        status_frame = ttk.Frame(panel)
        status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        status_frame.grid_columnconfigure(0, weight=1)

        self.lbl_status = tk.Label(status_frame, text="READY TO START",
                                   font=("Segoe UI", 16, "bold"),
                                   bg=self.CLR_FRAME_BG,
                                   fg=self.CLR_TEXT_DARK, pady=10)
        self.lbl_status.grid(row=0, column=0, sticky="ew")

        self.lbl_current_temp = tk.Label(status_frame, text="--- K",
                                         font=("Segoe UI", 26, "bold"),
                                         bg=self.CLR_FRAME_BG,
                                         fg=self.CLR_ACCENT_RED, padx=20)
        self.lbl_current_temp.grid(row=0, column=1, sticky="e", padx=10)

        self.btn_proceed = ttk.Button(status_frame,
                                      text="Measurement Complete - Proceed ➔",
                                      style="Proceed.TButton",
                                      state="disabled",
                                      command=self._on_proceed)
        self.btn_proceed.grid(row=0, column=2, sticky="ew", padx=10, ipady=5)

        container = ttk.LabelFrame(panel, text="Live Temperature Monitoring")
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        plot_ctrl = ttk.Frame(container)
        plot_ctrl.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))
        ttk.Label(plot_ctrl, text="X-axis min (s):").pack(side="left", padx=(5, 2))
        self.xmin_var = tk.StringVar(value="0")
        xmin_entry = ttk.Entry(plot_ctrl, textvariable=self.xmin_var, width=8)
        xmin_entry.pack(side="left")
        xmin_entry.bind("<Return>", lambda e: self._mark_plot_dirty())
        ttk.Button(plot_ctrl, text="Apply",
                   command=self._mark_plot_dirty).pack(side="left", padx=4)
        ttk.Button(plot_ctrl, text="Full View",
                   command=lambda: (self.xmin_var.set("0"),
                                    self._mark_plot_dirty())
                   ).pack(side="left", padx=4)

        plot_frame = ttk.Frame(container)
        plot_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.figure.add_subplot(211)
        self.ax_heater = self.figure.add_subplot(212, sharex=self.ax_temp)

        self.line_target = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_GREEN, linestyle="--",
            label="Target Setpoint")[0]
        self.line_temp = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_RED, marker="o", markersize=3,
            linestyle="-", label="Actual Temp")[0]
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, linestyle="--", alpha=0.6)
        self.ax_temp.legend(loc="best", frameon=True,
                            facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis="x", which="both", bottom=False,
                                 labelbottom=False)

        self.line_heater = self.ax_heater.plot(
            [], [], color=self.CLR_ACCENT_GOLD, marker=".", markersize=3,
            linestyle="-")[0]
        self.ax_heater.set_xlabel("Time (s)")
        self.ax_heater.set_ylabel("Heater Output (%)")
        self.ax_heater.grid(True, linestyle="--", alpha=0.6)
        self.figure.tight_layout()

        # FRZ-5: toolbar packed BEFORE the canvas so it stays visible
        self.canvas = FigureCanvasTkAgg(self.figure, plot_frame)
        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame,
                                       pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # ------------------------------------------------------------
    # UI HELPERS
    # ------------------------------------------------------------
    def _create_grid_entry(self, parent, label, key, default_value, row, col,
                           lockable=True):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w",
                                           padx=(10, 2), pady=2)
        entry = ttk.Entry(parent, font=self.FONT_BASE, width=10)
        entry.grid(row=row, column=col + 1, sticky="ew", padx=2, pady=2)
        entry.insert(0, default_value)
        if lockable:
            lock_btn = ttk.Button(parent, text="🔓", width=2,
                                  command=lambda k=key: self._toggle_entry_lock(k))
            lock_btn.grid(row=row, column=col + 2, sticky="w",
                          padx=(0, 10), pady=2)
            self.entries[key] = {"entry": entry, "lock": lock_btn,
                                 "locked": False}
        else:
            self.entries[key] = {"entry": entry, "lock": None, "locked": False}
        return entry

    def _mk_entry(self, parent, label, default, r, c):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w",
                                           padx=(10, 2), pady=2)
        e = ttk.Entry(parent, width=10)
        e.grid(row=r, column=c + 1, sticky="ew", padx=2, pady=2)
        e.insert(0, default)
        return e

    def _toggle_entry_lock(self, key):
        w = self.entries[key]
        if w["locked"]:
            w["entry"].config(state="normal")
            w["lock"].config(text="🔓")
            w["locked"] = False
        else:
            w["entry"].config(state="disabled")
            w["lock"].config(text="🔒")
            w["locked"] = True

    def _on_pid_preset_change(self, event=None):
        preset = self.pid_preset_var.get()
        if preset in self.PID_PRESETS:
            p, i, d = self.PID_PRESETS[preset]
            for e, v in ((self.pid_p_entry, p), (self.pid_i_entry, i),
                         (self.pid_d_entry, d)):
                e.delete(0, "end")
                e.insert(0, str(v))

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state="normal")
        self.console.insert("end", f"[{ts}] {message}\n")
        # Keep the console bounded on overnight runs
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
        """FRZ-3: only ever called from the main (Tk) thread."""
        if HAS_WINSOUND and platform.system() == "Windows":
            threading.Thread(
                target=lambda: winsound.Beep(1000, 500), daemon=True).start()
        else:
            try:
                self.root.bell()
            except Exception:
                pass

    # ------------------------------------------------------------
    # Sequence builder (with live editing while running)
    # ------------------------------------------------------------
    def _sort_listbox(self):
        if self.is_running:
            self.log("Sort disabled while a sequence is running.")
            return
        items = list(self.listbox.get(0, tk.END))
        if not items:
            return
        try:
            floats = sorted({float(x) for x in items},
                            reverse=(self.sort_var.get() == "Descending"))
            self.listbox.delete(0, tk.END)
            for val in floats:
                self.listbox.insert(tk.END, f"{val:.2f}")
        except Exception:
            pass

    def _generate_steps(self):
        if self.is_running:
            messagebox.showwarning(
                "Not Available",
                "Bulk-generate is disabled while a sequence is running.\n"
                "Use 'Manual(K) → Add' to append steps live.")
            return
        try:
            start = float(self.entry_start.get())
            end = float(self.entry_end.get())
            step = float(self.entry_step.get())
            if step <= 0:
                raise ValueError("Step must be positive")
            if start < end:
                pts = np.arange(start, end + step / 2, step)
            else:
                pts = np.arange(start, end - step / 2, -step)
            for v in pts:
                self.listbox.insert(tk.END, f"{v:.2f}")
            self._sort_listbox()
        except ValueError:
            messagebox.showerror(
                "Input Error",
                "Please enter valid numeric values for Start, End, and Step.")

    def _add_manual_step(self):
        try:
            val = float(self.entry_manual.get())
            self.listbox.insert(tk.END, f"{val:.2f}")
            if not self.is_running:
                self._sort_listbox()
            self.entry_manual.delete(0, tk.END)
            self._sync_setpoints_from_listbox()
        except ValueError:
            messagebox.showerror("Input Error", "Enter a valid numeric temperature.")

    def _remove_step(self):
        selection = self.listbox.curselection()
        if self.is_running:
            for index in reversed(selection):
                if index <= self.current_step_index:
                    messagebox.showwarning(
                        "Cannot Remove",
                        "That step has already completed or is currently "
                        "active and cannot be removed.")
                    return
        for index in reversed(selection):
            self.listbox.delete(index)
        self._sync_setpoints_from_listbox()

    def _clear_listbox(self):
        if self.is_running:
            messagebox.showwarning(
                "Not Available", "Cannot clear the sequence while it is running.")
            return
        self.listbox.delete(0, tk.END)

    def _sync_setpoints_from_listbox(self):
        """Push listbox contents into the worker's shared setpoint list.
        Completed/active steps must remain an untouched prefix."""
        if not self.is_running:
            return
        try:
            new_list = [float(x) for x in self.listbox.get(0, tk.END)]
        except ValueError:
            return
        with self.setpoint_lock:
            idx = self.current_step_index
            protected = self.setpoint_floats[: idx + 1]
            if new_list[: idx + 1] == protected:
                self.setpoint_floats = new_list
                remaining = new_list[idx + 1:]
                self.log(f"Sequence updated live. Remaining steps: {remaining}")
                restore = None
            else:
                self.log("WARN: edit touched a completed/active step; "
                         "change rejected.")
                restore = list(self.setpoint_floats)
        if restore is not None:
            self.listbox.delete(0, tk.END)
            for v in restore:
                self.listbox.insert(tk.END, f"{v:.2f}")

    # ------------------------------------------------------------
    # Live-update command senders (queue-based)
    # ------------------------------------------------------------
    def _on_heater_range_changed(self, event=None):
        if self.is_running:
            r = self.heater_range_var.get().split()[0]
            self.log(f"Queued heater range update: {r}")
            self.cmd_queue.put(("heater", r))

    def _send_pid(self):
        if not self.is_running:
            messagebox.showwarning("Not Running",
                                   "PID can only be sent while running.")
            return
        try:
            p = float(self.pid_p_entry.get())
            i = float(self.pid_i_entry.get())
            d = float(self.pid_d_entry.get())
            self.cmd_queue.put(("pid_send", (p, i, d)))
            self.log(f"Queued PID SEND: P={p}, I={i}, D={d}")
        except ValueError:
            messagebox.showerror("Invalid Input", "P, I, D must be numeric.")

    def _read_pid(self):
        if not self.is_running:
            messagebox.showwarning("Not Running",
                                   "PID can only be read while running.")
            return
        self.cmd_queue.put(("pid_read",))
        self.log("Queued PID READ request.")

    def _send_live_updates(self):
        """Validate unlocked numeric fields + ramp table, queue an update."""
        if not self.is_running:
            messagebox.showwarning(
                "Not Running",
                "Parameters can only be updated while a sequence is active.")
            return
        updates = {}
        try:
            for key, w in self.entries.items():
                if w["lock"] is not None and not w["locked"]:
                    updates[key] = float(w["entry"].get())
        except ValueError:
            messagebox.showerror("Invalid Input",
                                 "All unlocked parameter values must be numeric.")
            return
        try:
            caps, default_cap = parse_ramp_table(self.ramp_table_var.get())
            updates["ramp_caps"] = caps
            updates["ramp_default_cap"] = default_cap
        except ValueError as e:
            messagebox.showerror("Ramp Table Error", str(e))
            return
        self.cmd_queue.put(("params", updates))
        self.log(f"Queued live parameter update: "
                 f"{ {k: v for k, v in updates.items() if k != 'ramp_caps'} }")

    def _toggle_pause(self):
        if not self.is_running:
            return
        if self.pause_button["text"] == "Pause":
            self.cmd_queue.put(("pause",))
            self.pause_button.config(text="Resume")
            self.log("PAUSE requested (holds current setpoint; window & "
                     "timeout suspended).")
        else:
            self.cmd_queue.put(("resume",))
            self.pause_button.config(text="Pause")
            self.log("RESUME requested.")

    def _skip_step(self):
        if not self.is_running:
            return
        self.cmd_queue.put(("skip",))
        self.log("SKIP STEP requested.")

    def _on_proceed(self):
        self.log("User confirmed measurement. Moving to next setpoint.")
        self.btn_proceed.config(state="disabled")
        self.cmd_queue.put(("proceed",))

    # ------------------------------------------------------------
    # VISA scan / save dir
    # ------------------------------------------------------------
    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
        except Exception as e:
            self.log(f"Scan error: {e}")
            return
        if resources:
            self.log(f"Found: {resources}")
            self.ls_cb["values"] = resources
            for r in resources:
                if "GPIB" in r and ("12" in r or "15" in r):
                    self.ls_cb.set(r)
                    break
        else:
            self.log("No VISA instruments found.")

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
        setpoints = list(self.listbox.get(0, tk.END))
        if not setpoints:
            messagebox.showwarning(
                "Empty Sequence",
                "Please add at least one target temperature to the list.")
            return
        if not self.save_dir:
            messagebox.showwarning(
                "No Save Dir",
                "Choose a save directory first (Browse Save…).")
            return
        try:
            self.params = self._validate_and_get_params()
            floats = [float(x) for x in setpoints]
            # Safety: every setpoint must respect the soft limit
            too_hot = [t for t in floats
                       if t > self.params["max_temp"]]
            if too_hot:
                raise ValueError(
                    f"Setpoints above Max Temp ({self.params['max_temp']} K): "
                    f"{too_hot}. Raise Max Temp (≤ "
                    f"{Lakeshore_Backend.HARD_TEMP_LIMIT_K:g} K) or remove them.")
            # 'From above' would place the pre-target ABOVE the setpoint;
            # reject at Start rather than SoftLimitAbort mid-run.
            if self.params["approach_side"] == "above":
                band = self.params["app_band"]
                too_close = [t for t in floats
                             if t + band > self.params["max_temp"]]
                if too_close:
                    raise ValueError(
                        f"'From above' pre-target (T + {band:g} K band) "
                        f"exceeds Max Temp ({self.params['max_temp']:g} K) "
                        f"for setpoints: {too_close}. Lower the setpoints/"
                        f"band or raise Max Temp.")
            # Heads-up (not an error): approach rate will be auto-capped
            # by the low-T table at the coldest setpoint of the run.
            coldest_cap = ramp_cap_for(min(floats),
                                       self.params["ramp_caps"],
                                       self.params["ramp_default_cap"])
            if self.params["app_rate"] > coldest_cap:
                self.log(f"NOTE: Approach Rate "
                         f"{self.params['app_rate']:g} K/min exceeds the "
                         f"low-T cap ({coldest_cap:g} K/min at "
                         f"{min(floats):g} K) — it will be auto-capped "
                         f"during the run.")
            with self.setpoint_lock:
                self.setpoint_floats = floats
                self.current_step_index = 0
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
            return

        self.set_ui_state(running=True)
        self.is_running = True   # handed to the worker; worker owns it from here

        # Clear plot data
        for L in (self.plot_t, self.plot_temp, self.plot_target,
                  self.plot_heater):
            L.clear()
        self.line_target.set_data([], [])
        self.line_temp.set_data([], [])
        self.line_heater.set_data([], [])
        if self.band_patch is not None:
            try:
                self.band_patch.remove()
            except Exception:
                pass
            self.band_patch = None
        self._band_params = None
        self.xmin_var.set("0")
        self.canvas.draw_idle()
        self._temp_plot_dirty = False
        self._band_dirty = False

        # Drain queues from any previous run
        for q in (self.cmd_queue, self.gui_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

        self.pause_button.config(text="Pause")
        self.start_time = time.time()

        # Open CSV files (main log + per-setpoint summary)
        os.makedirs(self.save_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_filepath = os.path.join(self.save_dir,
                                          f"TStepAdv_{stamp}.csv")
        self.data_file = open(self.data_filepath, "w", newline="")
        self.csv_writer = csv.writer(self.data_file)
        self.csv_writer.writerow(
            ["Timestamp", "Elapsed_s", "Target_K", "Temperature_K",
             "Resistance_Ohm", "Heater_pct", "Ramp_rate_K_min", "Phase"])
        self.data_file.flush()

        self.summary_filepath = os.path.join(
            self.save_dir, f"TStepAdv_{stamp}_summary.csv")
        self.summary_file = open(self.summary_filepath, "w", newline="")
        self.summary_writer = csv.writer(self.summary_file)
        self.summary_writer.writerow(
            ["Setpoint_K", "Ramp_rate_K_min", "Approach_mode", "Start_time",
             "End_time", "Stabilization_s", "Outcome"])
        self.summary_file.flush()

        self.log(f"Logging data to: {self.data_filepath}")
        self.log(f"Step summary to: {self.summary_filepath}")

        self.worker_thread = threading.Thread(
            target=self._hardware_worker_loop, daemon=True)
        self.worker_thread.start()
        self.root.after(50, self._process_gui_queue)

    def stop_sequence(self, reason=""):
        if not self.is_running:
            return
        self.log(f"STOP requested: {reason or 'user'}")
        # The worker is the only writer of is_running; it flips it False
        # in its ("stop",) handler and again in its finally block.
        self.cmd_queue.put(("stop",))
        self._update_status_ui("STOPPING…", self.CLR_ACCENT_RED)

    def _validate_and_get_params(self):
        p = {
            "tol": float(self.entries["tol"]["entry"].get()),
            "soak": float(self.entries["soak"]["entry"].get()),
            "drift": float(self.entries["drift"]["entry"].get()),
            "delay": float(self.entries["delay"]["entry"].get()),
            "app_band": float(self.entries["app_band"]["entry"].get()),
            "app_rate": float(self.entries["app_rate"]["entry"].get()),
            "stab_timeout": float(self.entries["stab_timeout"]["entry"].get()),
            "step_factor": float(self.entries["step_factor"]["entry"].get()),
            "first_factor": float(self.entries["first_factor"]["entry"].get()),
            "min_rate": float(self.entries["min_rate"]["entry"].get()),
            "max_rate": float(self.entries["max_rate"]["entry"].get()),
            "rate": float(self.entries["rate"]["entry"].get()),
            "max_temp": float(self.entries["max_temp"]["entry"].get()),
            "heater_range": self.heater_range_var.get().split()[0],
            "ls_visa": self.ls_cb.get(),
            "adaptive": self.ramp_mode_var.get() == "adaptive",
            "auto_pid": self.var_auto_pid.get(),
            "approach_side": self.APPROACH_MODES.get(
                self.approach_var.get(), None),
        }
        p["ramp_caps"], p["ramp_default_cap"] = \
            parse_ramp_table(self.ramp_table_var.get())

        if not p["ls_visa"]:
            raise ValueError("Please select a VISA address.")
        if p["tol"] <= 0:
            raise ValueError("Tolerance must be positive.")
        if p["soak"] <= 0:
            raise ValueError("Stability window must be positive.")
        if p["drift"] <= 0:
            raise ValueError("Drift limit must be positive.")
        if p["delay"] <= 0:
            raise ValueError("Poll delay must be positive.")
        if p["app_band"] <= p["tol"]:
            raise ValueError("Approach band should be larger than Tolerance.")
        if p["app_rate"] <= 0:
            raise ValueError("Approach rate must be positive.")
        if p["stab_timeout"] < 0:
            raise ValueError("Timeout must be >= 0 (0 disables).")
        if p["step_factor"] <= 0:
            raise ValueError("Step factor must be positive.")
        if not (0 < p["first_factor"] <= 1):
            raise ValueError("First-step factor must be in (0, 1].")
        if p["min_rate"] <= 0 or p["max_rate"] < p["min_rate"]:
            raise ValueError("Need 0 < Min Rate <= Max Rate.")
        if p["rate"] <= 0:
            raise ValueError("Fixed rate must be positive.")
        if p["heater_range"] == "0":
            raise ValueError("Heater range 0 (off) cannot start a sequence.")
        if not (0 < p["max_temp"] <= Lakeshore_Backend.HARD_TEMP_LIMIT_K):
            raise ValueError(
                f"Max Temp must be in (0, "
                f"{Lakeshore_Backend.HARD_TEMP_LIMIT_K:g}] K.")
        return p

    def set_ui_state(self, running: bool):
        state = "disabled" if running else "normal"
        self.start_button.config(state=state)
        self.stop_button.config(state="normal" if running else "disabled")
        self.pause_button.config(state="normal" if running else "disabled")
        self.skip_button.config(state="normal" if running else "disabled")
        if not running:
            self.pause_button.config(text="Pause")

        for w in self.entries.values():
            entry = w["entry"]
            if running:
                if w.get("lock") is not None:
                    entry.config(state="disabled" if w.get("locked") else "normal")
                else:
                    entry.config(state="normal")
            else:
                entry.config(state="disabled" if w.get("locked") else "normal")
            if w.get("lock") is not None:
                w["lock"].config(state="normal")  # locks stay clickable mid-run

        # Sequence builder: dynamic add/remove stays enabled during a run
        self.entry_manual.config(state="normal")
        self.listbox.config(state="normal")
        self.btn_generate_steps.config(state=state)
        self.entry_start.config(state=state)
        self.entry_end.config(state=state)
        self.entry_step.config(state=state)
        self.sort_cb.config(state=("disabled" if running else "readonly"))
        self.lbl_seq_hint.config(
            text="Sequence is live: you may Add/Remove upcoming steps."
            if running else "")

        self.approach_cb.config(state=("disabled" if running else "readonly"))
        # Ramp mode is only read at Start — lock the radios during a run
        self.rb_adaptive.config(state=state)
        self.rb_fixed.config(state=state)
        self._on_ramp_mode_changed()  # re-grey the inactive rate source
        self.ramp_table_entry.config(state="normal")  # live-updatable
        self.ls_cb.config(state=state if state == "normal" else "readonly")
        self.btn_proceed.config(state="disabled")

    # ------------------------------------------------------------
    # GUI queue processing (FRZ-3/4) + throttled redraw (FRZ-1/2)
    # ------------------------------------------------------------
    def _mark_plot_dirty(self):
        self._temp_plot_dirty = True

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
                elif t == "temp_display":
                    self.lbl_current_temp.config(text=f"{m['value']:.3f} K")
                elif t == "temp_point":
                    self.plot_t.append(m["t"])
                    self.plot_temp.append(m["temp"])
                    self.plot_target.append(m["target"])
                    self.plot_heater.append(m["heater"])
                    self._temp_plot_dirty = True
                elif t == "new_setpoint":
                    self._band_params = (m["target"], m["tol"])
                    self._band_dirty = True
                    self._temp_plot_dirty = True
                elif t == "handshake_ready":
                    self.btn_proceed.config(state="normal")
                    self._beep()
                elif t == "pid_read_result":
                    p, i, d = m["values"]
                    for e, v in ((self.pid_p_entry, p), (self.pid_i_entry, i),
                                 (self.pid_d_entry, d)):
                        e.delete(0, "end")
                        e.insert(0, str(v))
                    self.pid_preset_var.set("Custom")
                elif t == "pause_state":
                    self.pause_button.config(
                        text="Resume" if m["paused"] else "Pause")
                elif t == "sequence_complete":
                    messagebox.showinfo("Sequence Complete",
                                        "All setpoints processed.")
                elif t == "worker_done":
                    self.set_ui_state(running=False)
                    self._update_status_ui("IDLE", self.CLR_HEADER)
                    return  # worker gone; stop polling
        except queue.Empty:
            pass
        if self.worker_thread and self.worker_thread.is_alive():
            self.root.after(50, self._process_gui_queue)
        elif not self.gui_queue.empty():
            self.root.after(50, self._process_gui_queue)

    def _decimate_display_series(self):
        """FRZ-2: bound displayed series (display only; CSV keeps all data)."""
        if len(self.plot_t) > self.MAX_PLOT_POINTS:
            self.plot_t[:] = self.plot_t[::2]
            self.plot_temp[:] = self.plot_temp[::2]
            self.plot_target[:] = self.plot_target[::2]
            self.plot_heater[:] = self.plot_heater[::2]

    def _redraw_tick(self):
        """FRZ-1: single periodic redraw; all plot mutation happens here."""
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
                    target, tol = self._band_params
                    self.band_patch = self.ax_temp.axhspan(
                        target - tol, target + tol,
                        color=self.CLR_ACCENT_GREEN, alpha=0.15, zorder=0)
            if self._temp_plot_dirty:
                self._temp_plot_dirty = False
                self._decimate_display_series()
                t = self.plot_t
                self.line_target.set_data(t, self.plot_target)
                self.line_temp.set_data(t, self.plot_temp)
                self.line_heater.set_data(t, self.plot_heater)
                if t:
                    try:
                        x_min = max(0.0, float(self.xmin_var.get()))
                    except (ValueError, tk.TclError):
                        x_min = 0.0
                    x_max = t[-1]
                    if x_min >= x_max:
                        x_min = max(0.0, x_max - 60)
                    vis = [k for k, tv in enumerate(t) if tv >= x_min]

                    def _ylim_from(*series_list):
                        vals = [s[k] for s in series_list for k in vis]
                        if not vals:
                            return None
                        lo, hi = min(vals), max(vals)
                        pad = max((hi - lo) * 0.05, 0.05)
                        return lo - pad, hi + pad

                    margin = max((x_max - x_min) * 0.02, 1)
                    for ax in (self.ax_temp, self.ax_heater):
                        ax.set_xlim(x_min, x_max + margin)
                    yl = _ylim_from(self.plot_temp, self.plot_target)
                    if yl:
                        self.ax_temp.set_ylim(*yl)
                    yl = _ylim_from(self.plot_heater)
                    if yl:
                        self.ax_heater.set_ylim(*yl)
                self.canvas.draw_idle()
        except Exception as e:
            # A plotting hiccup must never kill the redraw loop overnight.
            print(f"redraw warning: {e}")
        finally:
            self.root.after(self.REDRAW_MS, self._redraw_tick)

    # ============================================================
    # WORKER THREAD
    # ============================================================
    def _put_gui_msg(self, msg_type, **kw):
        kw["type"] = msg_type
        self.gui_queue.put(kw)

    def _process_cmd_queue(self):
        """Drain the command queue. Returns "stop", "skip", "proceed" or
        None. Heater/PID/params/pause commands are handled inline."""
        action = None
        try:
            while True:
                cmd = self.cmd_queue.get_nowait()
                kind = cmd[0]
                if kind == "stop":
                    self._put_gui_msg("log", text="Stop received by worker.")
                    self.is_running = False
                    return "stop"
                elif kind == "skip":
                    action = "skip"
                elif kind == "proceed":
                    action = action or "proceed"
                elif kind == "pause":
                    self._paused = True
                    self._put_gui_msg("pause_state", paused=True)
                elif kind == "resume":
                    self._paused = False
                    self._put_gui_msg("pause_state", paused=False)
                elif kind == "heater":
                    try:
                        self.backend.set_heater_range(1, cmd[1])
                        self._put_gui_msg("log",
                                          text=f"Heater range set: {cmd[1]}")
                    except Exception as e:
                        self._put_gui_msg("log",
                                          text=f"Heater update failed: {e}")
                elif kind == "pid_send":
                    p, i, d = cmd[1]
                    try:
                        self.backend.set_pid(1, p, i, d)
                        self._put_gui_msg("log",
                                          text=f"PID set: P={p}, I={i}, D={d}")
                    except Exception as e:
                        self._put_gui_msg("log", text=f"PID send failed: {e}")
                elif kind == "pid_read":
                    try:
                        vals = self.backend.get_pid(1)
                        self._put_gui_msg("pid_read_result", values=vals)
                    except Exception as e:
                        self._put_gui_msg("log", text=f"PID read failed: {e}")
                elif kind == "params":
                    updates = cmd[1]
                    self.params.update(updates)
                    shown = {k: v for k, v in updates.items()
                             if k not in ("ramp_caps",)}
                    self._put_gui_msg("log", text=f"Params applied: {shown}")
                    # Never override the slow final approach (REL-2)
                    if "rate" in updates and not self.params["adaptive"]:
                        if self._phase == "FINAL_APPROACH":
                            self._put_gui_msg(
                                "log",
                                text="Ramp rate stored; slow final approach "
                                     "active — applies from next ramp stage.")
                        else:
                            try:
                                temp_now, _, _ = self.backend.get_status()
                                tgt = self._current_target
                                new_rate, cap_msg = capped_rate(
                                    updates["rate"], temp_now,
                                    tgt if tgt is not None else temp_now,
                                    self.params, "Live")
                                if cap_msg:
                                    self._put_gui_msg("log", text=cap_msg)
                                self.backend.set_ramp_rate(new_rate)
                                self._current_rate = new_rate
                                self._put_gui_msg(
                                    "log",
                                    text=f"Ramp rate -> {new_rate} K/min")
                            except Exception as e:
                                self._put_gui_msg(
                                    "log", text=f"Ramp rate update failed: {e}")
        except queue.Empty:
            pass
        return action

    def _read_temp_point(self, target):
        """Read Lakeshore, check hard + soft temperature limits, write the
        CSV row (with ramp rate + phase), queue plot/display messages."""
        temp, resistance, htr = self.backend.get_status()
        if self.backend.check_overtemp(temp):
            self._put_gui_msg(
                "log",
                text=f"!!! KILL SWITCH: {temp:.2f} K >= "
                     f"{Lakeshore_Backend.HARD_TEMP_LIMIT_K:g} K. "
                     f"Heater OFF. Aborting.")
            self._put_gui_msg("status", text="OVERTEMP ABORT",
                              color=self.CLR_ACCENT_RED)
            self.is_running = False
            raise RuntimeError("Hard overtemperature limit reached.")
        if temp >= self.params["max_temp"]:
            raise SoftLimitAbort(
                f"Soft Max Temp limit reached: {temp:.2f} K >= "
                f"{self.params['max_temp']:g} K.")
        elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        phase = "PAUSED" if self._paused else (self._phase or "")
        try:
            self.csv_writer.writerow(
                [now_str, f"{elapsed:.2f}", f"{target:.4f}", f"{temp:.4f}",
                 f"{resistance:.6g}", f"{htr:.2f}",
                 f"{self._current_rate:.3f}", phase])
            self.data_file.flush()
        except Exception as e:
            self._put_gui_msg("log", text=f"WARN: data write failed: {e}")
        self._put_gui_msg("temp_point", t=elapsed, temp=temp, target=target,
                          heater=htr)
        self._put_gui_msg("temp_display", value=temp)
        return temp

    def _apply_dynamic_pid(self, target):
        if not self.params["auto_pid"]:
            return
        p, i, d = self.PID_SLOW if target < 100.0 else self.PID_MEDIUM
        try:
            self.backend.set_pid(1, p, i, d)
            self._put_gui_msg("log",
                              text=f"Dynamic PID @ {target} K: "
                                   f"P={p}, I={i}, D={d}")
        except Exception as e:
            self._put_gui_msg("log", text=f"Dynamic PID failed: {e}")

    def _choose_pre_target(self, temp, target):
        """Pre-target selection (STB-1 + optional one-side approach).
        Returns the pre-target setpoint, or None for a direct slow approach."""
        p = self.params
        delta = target - temp
        side = p["approach_side"]
        if side in ("below", "above"):
            sign = -1.0 if side == "below" else 1.0
            on_correct_side = (temp <= target) if side == "below" \
                else (temp >= target)
            if on_correct_side and abs(delta) <= p["app_band"]:
                return None
            # Force the pre-target onto the chosen side so the final slow
            # ramp always comes from that side.
            return target + sign * p["app_band"]
        if abs(delta) > p["app_band"]:
            return target - math.copysign(p["app_band"], delta)
        return None

    def _ramp_and_stabilize(self, target, ramp_rate):
        """Two-stage approach (STB-1) + rolling-window stability (STB-2)
        + timeout (STB-3) + pause/skip support.

        Returns (outcome, elapsed_s); outcome in
        "stable" | "timeout" | "skipped" | "stopped".
        Raises on kill switch / soft limit / persistent VISA failure.
        """
        p = self.params
        self._current_rate = ramp_rate
        self._current_target = target
        temp = self._read_temp_point(target)
        pre_target = self._choose_pre_target(temp, target)
        # Low-T safety envelope applies to the approach rate too
        app_rate, app_msg = capped_rate(p["app_rate"], temp, target, p,
                                        "Approach")
        if app_msg:
            self._put_gui_msg("log", text=app_msg)

        if pre_target is not None:
            self._phase = "PRE_RAMP"
            self.backend.configure_ramp(pre_target, ramp_rate,
                                        p["heater_range"])
            self._put_gui_msg(
                "log",
                text=f"Stage 1: ramp ({ramp_rate:g} K/min) to pre-target "
                     f"{pre_target:.2f} K ({p['app_band']:g} K short of "
                     f"{target} K).")
            self._put_gui_msg(
                "status",
                text=f"RAMPING TO {target} K (pre: {pre_target:.1f} K)",
                color=self.CLR_ACCENT_RED)
        else:
            self._phase = "FINAL_APPROACH"
            self._current_rate = app_rate
            self.backend.configure_ramp(target, app_rate,
                                        p["heater_range"])
            self._put_gui_msg(
                "log",
                text=f"Already within approach band — slow approach "
                     f"({app_rate:g} K/min) to {target} K.")
            self._put_gui_msg("status", text=f"APPROACHING {target} K",
                              color=self.CLR_STABLE_WAIT)

        # Direction of travel toward the pre-target (may differ from the
        # overall direction when approach_side forces an overshoot).
        pre_heating = (pre_target is not None) and (pre_target > temp)

        window = deque()          # (time, temp) rolling stability window
        step_start = time.time()
        paused_s = 0.0            # pause time excluded from the timeout
        last_status = 0.0

        try:
            while self.is_running:
                action = self._process_cmd_queue()
                if action == "stop":
                    return "stopped", time.time() - step_start
                if action == "skip":
                    return "skipped", time.time() - step_start

                temp = self._read_temp_point(target)
                now = time.time()

                if self._paused:
                    # Hold at the current setpoint: keep polling/logging,
                    # but suspend the stability window and timeout clock.
                    paused_s += p["delay"]
                    window.clear()
                    self._put_gui_msg(
                        "status",
                        text=f"PAUSED (holding, target {target} K)",
                        color=self.CLR_ACCENT_GOLD)
                    time.sleep(p["delay"])
                    continue

                if self._phase == "PRE_RAMP":
                    reached = (temp >= pre_target - p["tol"]) if pre_heating \
                        else (temp <= pre_target + p["tol"])
                    if reached:
                        # Stage 2: hand over to the slow final approach.
                        # Re-check the cap at the handover temperature.
                        app_rate, app_msg = capped_rate(
                            p["app_rate"], temp, target, p, "Approach")
                        if app_msg:
                            self._put_gui_msg("log", text=app_msg)
                        self._phase = "FINAL_APPROACH"
                        self._current_rate = app_rate
                        self.backend.set_ramp_rate(app_rate)
                        time.sleep(0.1)
                        self.backend.set_setpoint(target)
                        window.clear()
                        self._put_gui_msg(
                            "log",
                            text=f"Pre-target reached ({temp:.3f} K). "
                                 f"Stage 2: slow approach "
                                 f"({app_rate:g} K/min) to {target} K.")
                        self._put_gui_msg("status",
                                          text=f"APPROACHING {target} K",
                                          color=self.CLR_STABLE_WAIT)
                else:
                    # FINAL_APPROACH: rolling-window criterion (STB-2)
                    window.append((now, temp))
                    while window and (now - window[0][0]) > p["soak"]:
                        window.popleft()
                    ok, max_dev, drift = window_check(
                        window, target, p["tol"], p["soak"], p["drift"])
                    if ok:
                        self._put_gui_msg(
                            "log",
                            text=f"STABLE at {target} K: max dev "
                                 f"{max_dev:.3f} K, drift {drift:+.3f} K/min "
                                 f"over last {p['soak']:.0f} s.")
                        return "stable", time.time() - step_start
                    if now - last_status > 3.0:
                        last_status = now
                        if max_dev is not None:
                            self._put_gui_msg(
                                "status",
                                text=f"STABILIZING {target} K | "
                                     f"Δmax={max_dev:.3f} K | "
                                     f"drift={drift:+.3f} K/min",
                                color=self.CLR_STABLE_WAIT)

                # STB-3: overnight-safety timeout (pause time excluded)
                if p["stab_timeout"] > 0 and \
                        (now - step_start - paused_s) > p["stab_timeout"] * 60.0:
                    return "timeout", time.time() - step_start

                time.sleep(p["delay"])
        finally:
            self._phase = ""
        return "stopped", time.time() - step_start

    def _wait_for_proceed(self, target):
        """Hold at the stable setpoint, keep logging, until Proceed /
        Skip / Stop. Returns "proceed" or "stopped"."""
        self._phase = "WAITING"
        try:
            while self.is_running:
                action = self._process_cmd_queue()
                if action == "stop":
                    return "stopped"
                if action in ("proceed", "skip"):
                    return "proceed"
                self._read_temp_point(target)
                time.sleep(self.params["delay"])
            return "stopped"
        finally:
            self._phase = ""

    def _hardware_worker_loop(self):
        try:
            self._put_gui_msg("log", text="Connecting to Lakeshore...")
            idn = self.backend.connect(self.params["ls_visa"])
            self._put_gui_msg("log", text=f"Connected: {idn}")
            if self.params["approach_side"]:
                self._put_gui_msg(
                    "log",
                    text=f"Approach mode: always from "
                         f"{self.params['approach_side']} (hysteresis-safe).")

            step_index = 0
            first_step = True
            while self.is_running:
                with self.setpoint_lock:
                    total = len(self.setpoint_floats)
                    if step_index >= total:
                        break
                    target = self.setpoint_floats[step_index]
                    self.current_step_index = step_index

                self._put_gui_msg(
                    "log",
                    text=f"--- Sequence Step {step_index+1}/{total}: "
                         f"Target {target} K ---")
                self._put_gui_msg("new_setpoint", target=target,
                                  tol=self.params["tol"])

                # Adaptive (or manual) ramp rate for this step
                temp_now, _, _ = self.backend.get_status()
                if self.params["adaptive"]:
                    rate, reason = compute_ramp_rate(
                        temp_now, target, self.params, first_step)
                    self._put_gui_msg("log",
                                      text=f"Adaptive ramp rate: {reason}")
                else:
                    rate, cap_msg = capped_rate(self.params["rate"],
                                                temp_now, target,
                                                self.params, "Fixed")
                    if cap_msg:
                        self._put_gui_msg("log", text=cap_msg)
                    self._put_gui_msg(
                        "log", text=f"Fixed ramp rate: {rate:g} K/min.")

                self._apply_dynamic_pid(target)

                t_start = datetime.now()
                outcome, elapsed = self._ramp_and_stabilize(target, rate)
                self._write_summary_row(target, rate, t_start,
                                        elapsed, outcome)

                if outcome == "stopped" or not self.is_running:
                    break
                if outcome == "skipped":
                    self._put_gui_msg(
                        "log", text=f"Step {target} K skipped by user.")
                    step_index += 1
                    first_step = False
                    continue
                if outcome == "timeout":
                    self._put_gui_msg(
                        "log",
                        text=f"⚠️⚠️ STABILIZATION TIMEOUT at {target} K after "
                             f"{elapsed/60.0:.1f} min — check this point! "
                             f"Waiting for Proceed anyway.")
                    self._put_gui_msg(
                        "status",
                        text=f"TIMEOUT AT {target} K | AWAITING MEASUREMENT",
                        color=self.CLR_ACCENT_RED)
                else:
                    self._put_gui_msg(
                        "log",
                        text=f"Stabilized at {target} K in "
                             f"{elapsed/60.0:.1f} min. "
                             f"Ready for external measurement.")
                    self._put_gui_msg(
                        "status",
                        text=f"STABLE AT {target} K | AWAITING MEASUREMENT",
                        color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("handshake_ready")

                if self._wait_for_proceed(target) == "stopped":
                    break
                step_index += 1
                first_step = False

            if self.is_running:
                self._put_gui_msg("log", text="Measurement Sequence Complete.")
                self._put_gui_msg("status", text="COMPLETE",
                                  color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("sequence_complete")

        except SoftLimitAbort as e:
            self._put_gui_msg("log", text=f"SAFETY ABORT: {e} "
                                          f"Ramp stopped, heater off.")
            self._put_gui_msg("status", text="SAFETY ABORT (MAX TEMP)",
                              color=self.CLR_ACCENT_RED)
        except Exception as e:
            self._put_gui_msg(
                "log",
                text=f"CRITICAL ERROR IN HARDWARE THREAD: {e}\n"
                     f"{traceback.format_exc()}")
            self._put_gui_msg("status", text="ERROR", color=self.CLR_ACCENT_RED)
        finally:
            # Crash-safe: always stop the ramp + heater, close files.
            try:
                self.backend.shutdown()
            except Exception as e:
                print(f"Lakeshore shutdown warning: {e}")
            self._close_data_files()
            self.is_running = False
            self._paused = False
            self._phase = ""
            self._current_target = None
            self._put_gui_msg("worker_done")

    def _write_summary_row(self, target, rate, t_start, elapsed, outcome):
        try:
            side = self.params["approach_side"] or "two-stage"
            self.summary_writer.writerow(
                [f"{target:.4f}", f"{rate:.3f}", side,
                 t_start.strftime("%Y-%m-%d %H:%M:%S"),
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 f"{elapsed:.1f}", outcome])
            self.summary_file.flush()
        except Exception as e:
            self._put_gui_msg("log", text=f"WARN: summary write failed: {e}")

    def _close_data_files(self):
        for attr, label in (("data_file", "Data"), ("summary_file", "Summary")):
            f = getattr(self, attr, None)
            if f:
                try:
                    f.flush()
                    f.close()
                    self._put_gui_msg("log", text=f"{label} file closed.")
                except Exception:
                    pass
                finally:
                    setattr(self, attr, None)

    # ------------------------------------------------------------
    # Shutdown / close
    # ------------------------------------------------------------
    def _atexit_shutdown(self):
        try:
            self.backend.shutdown()
        except Exception:
            pass

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit",
                                   "A sequence is active. Stop hardware "
                                   "and exit?"):
                self.stop_sequence("User closed application.")
                # Non-blocking shutdown: poll for worker exit so the GUI
                # stays alive while a slow VISA read drains; atexit is
                # the backstop for instrument cleanup after destroy.
                self._close_deadline = time.time() + 15.0
                self._poll_worker_exit_then_destroy()
        else:
            self.root.destroy()

    def _poll_worker_exit_then_destroy(self):
        t = self.worker_thread
        if (t is not None and t.is_alive()
                and time.time() < self._close_deadline):
            self.root.after(200, self._poll_worker_exit_then_destroy)
            return
        if t is not None and t.is_alive():
            self.log("WARNING: worker did not exit within timeout; closing "
                     "anyway (atexit will clean up instruments).")
        self.root.destroy()


# ============================================================
# Entry point
# ============================================================
def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed. Please run 'pip install pyvisa'.")
        return
    root = tk.Tk()
    TempControlAdvancedGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
