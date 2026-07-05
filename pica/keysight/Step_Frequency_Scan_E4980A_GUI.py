"""
Module: Combined_TFreq_GUI.py
Purpose: Combined Lakeshore 350 temperature control + Keysight E4980A
         frequency sweep GUI.
Note: despite the filename (Step_Frequency_Scan…), this program performs
a temperature-STEPPED dielectric scan: a full frequency sweep at each
temperature setpoint. Filename kept for compatibility.

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

Bug fixes from §1 applied (see v1.0/1.1 history): #1-#13, CRIT-1/2,
MAJ-3/4/5, MIN-6/8, LCR-1.

============================================================
v1.2 — FREEZE + STABILIZATION OVERHAUL
============================================================
FREEZE FIXES (GUI froze mid-run, plot stopped updating):
  FRZ-1  Plot redraw is now THROTTLED. `temp_point` / `scan_point`
         messages only append to data lists and set a dirty flag.
         A single periodic `_redraw_tick` (every REDRAW_MS, default
         750 ms) does set_data / relim / autoscale / draw_idle ONCE.
         Previously every single point triggered a full relim+redraw
         on ever-growing lists → O(N) per point → GUI ground to a
         halt on long runs.
  FRZ-2  Plot data is DECIMATED for display: when a displayed series
         exceeds MAX_PLOT_POINTS, every 2nd point is dropped (display
         only — the CSV log always keeps every reading). Prevents
         unbounded memory + redraw cost on overnight runs.
  FRZ-3  `_beep` no longer touches Tk from the worker thread.
         Tkinter is NOT thread-safe: the old code called
         `self.root.bell()` from a worker-spawned thread, which can
         hard-freeze the whole interpreter with no traceback. The
         worker now sends a ("beep") message through gui_queue and the
         main thread performs the beep. This was the most likely cause
         of the total lock-up.
  FRZ-4  `_process_gui_queue` drains at most MAX_MSGS_PER_CYCLE
         messages per 50 ms tick so message bursts (interleaved temp
         logging during sweeps) can no longer starve the Tk event loop.
  FRZ-5  Matplotlib toolbars are now actually packed (they were
         created with pack_toolbar=False and never packed, so
         zoom/pan was invisible).

STABILIZATION OVERHAUL (overshoot < 100 K, very slow first point):
  STB-1  TWO-STAGE APPROACH. If the target is farther than
         `Approach Band` (K) away, the worker first ramps at the full
         rate to a PRE-TARGET that stops `Approach Band` short of the
         real target, then switches to a slow `Approach Rate` (K/min)
         for the final approach. The setpoint therefore never slews
         into the target at full speed → drastically reduced overshoot
         (the old code ramped the setpoint at 10 K/min straight into
         the target; thermal lag + integral windup then overshot,
         worst at low T).
  STB-2  ROLLING-WINDOW STABILITY CRITERION replaces the fragile
         "every instantaneous reading in band or restart the soak"
         logic. Stability = over the last `Soak/Window` seconds:
           (a) max |T - target| <= Tolerance, AND
           (b) |linear drift| <= Drift Limit (K/min).
         A single noise spike no longer resets a nearly-complete soak
         to zero (that was why the first point took forever); the
         window simply slides until the spike ages out. The drift
         check also guarantees the sample has genuinely settled (not
         merely passing through the band) before a sweep starts.
  STB-3  STABILIZATION TIMEOUT (min, 0 = disabled). For unattended
         overnight runs: if a setpoint cannot stabilize within the
         timeout, the program logs a loud warning and proceeds with
         the sweep anyway instead of hanging on one bad point all
         night. The warning is also written to the console log.

RELIABILITY:
  REL-1  `Lakeshore_Backend.get_status` now retries transient VISA
         read failures (2 retries with device clear + 0.5 s backoff)
         so a single glitched query at 3 a.m. no longer aborts the
         whole run. Persistent failures still raise.
  REL-2  Live "rate" updates are deferred while the slow final
         approach is active (would otherwise silently defeat STB-1).
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

    def set_ramp_rate(self, rate):
        """Change ramp rate without touching heater range or setpoint."""
        self.lakeshore.write(f"RAMP 1,1,{rate}")

    def set_setpoint(self, setpoint):
        self.lakeshore.write(f"SETP 1,{setpoint}")

    def get_status(self, retries=2):
        """REL-1: retry transient VISA glitches so a single failed query
        at 3 a.m. does not abort an overnight run. Persistent failures
        still raise (and the worker's except/finally shuts down safely)."""
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
        # NOTE (audit MIN-7): LS350 treats I=0 / D=0 as "off". Presets keep
        # I>0; do not set I=0 from the live panel unless you intend to
        # disable integral action (will produce a permanent offset).
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
        # MIN-6: block until the triggered measurement is actually complete
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
# FRONTEND: Combined GUI
# ============================================================
class CombinedGUI:
    PROGRAM_VERSION = "1.2-Combined"  # freeze + stabilization overhaul
    LEFT_PANEL_WIDTH = 480  # default sash position so the left panel starts fully visible

    # --- FRZ-1 / FRZ-2 / FRZ-4 tuning knobs ---
    REDRAW_MS = 750           # plot redraw interval (ms). One redraw per tick.
    MAX_PLOT_POINTS = 4000    # displayed points per series before decimation
    MAX_MSGS_PER_CYCLE = 300  # gui_queue messages processed per 50 ms tick

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

        # Plot data — main thread only (fix #1; CRIT-2 adds plot_heater)
        self.plot_t = []
        self.plot_temp = []
        self.plot_target = []
        self.plot_heater = []   # CRIT-2: GUI-owned heater % series
        self.meas_t = []
        self.meas_temp = []
        self.scan_f = []
        self.scan_cp = []
        self.scan_g = []

        # FRZ-1: dirty flags — redraw happens only in _redraw_tick
        self._temp_plot_dirty = False
        self._freq_plot_dirty = False
        self._pending_progress = None

        # Decade log autoscale state (LabVIEW-style): current snapped
        # y-limits per axis key; log-Y on by default (Cp/G span decades).
        self.log_y_var = tk.BooleanVar(value=True)
        self._decade_ylims = {}

        # REL-2: worker-side phase marker (worker thread only writes it;
        # used to defer live ramp-rate updates during final approach)
        self._worker_phase = None

        # PID presets for the live PID panel
        self.PID_PRESETS = {
            "Slow (P=0.5, I=4, D=0)": self.PID_SLOW,
            "Medium (P=20, I=15, D=0)": self.PID_MEDIUM,
            "Fast (P=50, I=20, D=0)": (50.0, 20.0, 0),
        }

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        # FRZ-1: single periodic redraw loop — runs for the app lifetime
        self.root.after(self.REDRAW_MS, self._redraw_tick)
        self.log("Combined GUI v1.2 initialized. 40 Hz – 2 MHz sweep, "
                 "340 K kill switch active.")
        self.log("Stabilization: two-stage approach + rolling-window "
                 "(tolerance AND drift) criterion.")

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

        self.main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left.pack_propagate(False)
        self.main_pane.add(left, weight=0)
        right = ttk.Frame(self.main_pane)
        self.main_pane.add(right, weight=1)

        self._populate_left(left)
        self._populate_right(right)

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

    def _populate_left(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        sb = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = sf

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
        self._create_grid_entry(frame, "Stab. Window (s):", "soak", "120", 0, 3)
        self._create_grid_entry(frame, "Ramp Rate (K/min):", "rate", "10.0", 1, 0)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "1", 1, 3)
        # --- STB-1 / STB-2 / STB-3: new stabilization parameters ---
        self._create_grid_entry(frame, "Approach Band (K):", "app_band", "3.0", 2, 0)
        self._create_grid_entry(frame, "Approach (K/min):", "app_rate", "2.0", 2, 3)
        self._create_grid_entry(frame, "Drift Lim (K/min):", "drift", "0.10", 3, 0)
        self._create_grid_entry(frame, "Stab. T/O (min):", "stab_timeout", "90", 3, 3)

        ttk.Label(frame, text="Heater Range:").grid(row=4, column=0, sticky="w", padx=10, pady=5)
        self.heater_range_var = tk.StringVar(value="5")
        self.heater_cb = ttk.Combobox(frame, textvariable=self.heater_range_var,
                                      values=["0", "1", "2", "3", "4", "5"], state="readonly", width=8)
        self.heater_cb.grid(row=4, column=1, columnspan=2, sticky="ew", padx=5)
        self.heater_cb.bind("<<ComboboxSelected>>", self._on_heater_range_changed)

        ttk.Label(frame, text="LS VISA:").grid(row=4, column=3, sticky="w", padx=5, pady=5)
        self.ls_cb = ttk.Combobox(frame, state="readonly", width=18)
        self.ls_cb.grid(row=4, column=4, columnspan=2, sticky="ew", padx=5)

        # MAJ-4: surface the previously-unreachable `_send_live_updates`
        # so unlocked tolerance/soak/rate/delay values can be pushed mid-run.
        ttk.Button(frame, text="Apply Live Updates",
                   command=self._send_live_updates
                   ).grid(row=5, column=0, columnspan=6, sticky="ew",
                          padx=10, pady=(2, 6))

    def _create_lcr_settings_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="E4980A LCR Settings")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)
        self.lcr_entries = {}

        # LCR-1 Layout Fix: Restructured grid to strictly avoid overlaps.
        # Row 0: Sample Name (spanning all columns)
        self._add_lcr_entry(frame, "Sample Name:", "sample_name", 0, 0, 3, "Sample")

        # Row 1: AC Bias and DC Bias
        self._add_lcr_entry(frame, "AC Bias (V):", "ac_bias", 1, 0, 1, "1.0")
        self._add_lcr_entry(frame, "DC Bias (V):", "dc_bias", 1, 2, 1, "0.0")

        # Row 2: Freq Delay and Aperture
        self._add_lcr_entry(frame, "Freq Delay (s):", "delay", 2, 0, 1, "0.2")
        ttk.Label(frame, text="Aperture:").grid(row=2, column=2, sticky="w", padx=5, pady=2)
        self.aper_cb = ttk.Combobox(frame, values=["SHOR", "MED", "LONG"], state="readonly", width=8)
        self.aper_cb.set("MED")  # LCR-1 Enforced default MED
        self.aper_cb.grid(row=2, column=3, sticky="w", padx=5, pady=2)

        # Row 3: Cable and LCR VISA
        ttk.Label(frame, text="Cable (m):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.cable_cb = ttk.Combobox(frame, values=["0", "1", "2", "4"], state="readonly", width=4)
        self.cable_cb.set("1")
        self.cable_cb.grid(row=3, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(frame, text="LCR VISA:").grid(row=3, column=2, sticky="w", padx=5, pady=2)
        self.lcr_cb = ttk.Combobox(frame, state="readonly", width=28)
        self.lcr_cb.grid(row=3, column=3, sticky="ew", padx=5, pady=2)

        # Row 4: ALC and Open/Short Corr Defaults checked
        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="ALC", variable=self.var_alc).grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=2)
        ttk.Checkbutton(frame, text="Open/Short Corr", variable=self.var_corr).grid(row=4, column=2, columnspan=2, sticky="w", padx=5, pady=2)

        # Row 5: Start/Stop/Scan Buttons
        bf = ttk.Frame(frame); bf.grid(row=5, column=0, columnspan=4, sticky="ew", pady=5, padx=5)
        bf.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(bf, text="Start Sequence", style="Start.TButton", command=self.start_sequence)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.stop_button = ttk.Button(bf, text="Stop All", style="Stop.TButton", state="disabled", command=self.stop_sequence)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(bf, text="Scan VISA", command=self._scan_for_visa).grid(row=0, column=2, sticky="ew", padx=2)

        # Row 6: Browse Save Button
        ttk.Button(frame, text="Browse Save…", command=self._browse_save).grid(row=6, column=0, columnspan=4, sticky="ew", padx=5, pady=(0, 5))

        # Row 7: Save Directory Label
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
        # FRZ-5: pack toolbar BEFORE canvas so it stays visible at the bottom
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

    def _on_log_y_toggle(self):
        """Re-snap decade limits when the log-Y checkbox flips.
        FRZ-1: never draws directly — just marks the plot dirty and lets
        the throttled _redraw_tick do the drawing."""
        self._decade_ylims.clear()
        self._freq_plot_dirty = True

    def _decade_autoscale_y(self, ax, values, key):
        """LabVIEW-style decade autoscale: snap y-limits to
        [10^floor(log10(min_pos)), 10^ceil(log10(max_pos))]; expand only,
        whole decades at a time, so the scale never jitters per point.
        Linear fallback if no positive finite data."""
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
            lo, hi = min(lo, cur[0]), max(hi, cur[1])  # expand only
        if cur != (lo, hi):
            self._decade_ylims[key] = (lo, hi)
            ax.set_yscale('log')
            ax.set_ylim(lo, hi)
        return True

    # ------------------------------------------------------------
    # FRZ-1 / FRZ-2: throttled redraw + display decimation
    # ------------------------------------------------------------
    def _decimate_display_series(self):
        """Keep displayed series bounded (display only; CSV keeps all data)."""
        if len(self.plot_t) > self.MAX_PLOT_POINTS:
            self.plot_t[:] = self.plot_t[::2]
            self.plot_temp[:] = self.plot_temp[::2]
            self.plot_target[:] = self.plot_target[::2]
            self.plot_heater[:] = self.plot_heater[::2]
        if len(self.meas_t) > self.MAX_PLOT_POINTS:
            self.meas_t[:] = self.meas_t[::2]
            self.meas_temp[:] = self.meas_temp[::2]

    def _redraw_tick(self):
        """Single periodic redraw. All plot mutation happens here, at a
        bounded rate, regardless of how fast data messages arrive."""
        try:
            if self._temp_plot_dirty:
                self._temp_plot_dirty = False
                self._decimate_display_series()
                self.line_temp.set_data(self.plot_t, self.plot_temp)
                self.line_target.set_data(self.plot_t, self.plot_target)
                self.scat_meas.set_data(self.meas_t, self.meas_temp)
                self.line_heater.set_data(self.plot_t, self.plot_heater)
                for ax in (self.ax_temp, self.ax_heater):
                    ax.relim(); ax.autoscale_view()
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
            # A plotting hiccup must never kill the redraw loop overnight.
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
        # LCR-1 Layout fix: label and entry placed cleanly on same row.
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
        # Keep the console itself bounded on overnight runs
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
        """FRZ-3: MUST only be called from the main (Tk) thread.
        winsound runs in a helper thread (it blocks); root.bell() is
        called directly on the main thread — never from a worker."""
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
    # Start / Stop (fix #4: stop only signals worker; MAJ-5: no direct flag write)
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
        self._worker_phase = None

        # Clear plot data (CRIT-2: include plot_heater)
        for L in (self.plot_t, self.plot_temp, self.plot_target, self.plot_heater,
                  self.meas_t, self.meas_temp,
                  self.scan_f, self.scan_cp, self.scan_g):
            L.clear()
        self.line_temp.set_data([], []); self.line_target.set_data([], [])
        self.scat_meas.set_data([], []); self.line_heater.set_data([], [])
        self.line_cp.set_data([], []); self.line_g.set_data([], [])
        self.canvas_t.draw_idle(); self.canvas_f.draw_idle()
        self._decade_ylims.clear()
        self._temp_plot_dirty = False
        self._freq_plot_dirty = False
        self.progress["value"] = 0
        self._pending_progress = None
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
        # MAJ-5: do NOT mutate is_running here. The worker is the only
        # writer; it flips it False inside its ("stop",) handler and again
        # in its finally block. This eliminates the second stop path.
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
            "app_band": float(self.entries["app_band"]["entry"].get()),
            "app_rate": float(self.entries["app_rate"]["entry"].get()),
            "drift": float(self.entries["drift"]["entry"].get()),
            "stab_timeout": float(self.entries["stab_timeout"]["entry"].get()),
            "heater_range": self.heater_range_var.get().split()[0],  # single split here
            "ls_visa": ls_visa,
        }
        if not p["ls_visa"]: raise ValueError("Select Lakeshore VISA.")
        if p["rate"] <= 0: raise ValueError("Ramp rate must be positive.")
        if p["tol"] <= 0: raise ValueError("Tolerance must be positive.")
        if p["soak"] <= 0: raise ValueError("Stabilization window must be positive.")
        if p["delay"] <= 0: raise ValueError("Poll delay must be positive.")
        if p["app_band"] <= 0: raise ValueError("Approach band must be positive.")
        if p["app_rate"] <= 0: raise ValueError("Approach rate must be positive.")
        if p["drift"] <= 0: raise ValueError("Drift limit must be positive.")
        if p["stab_timeout"] < 0: raise ValueError("Timeout must be >= 0 (0 disables).")
        if p["app_band"] <= p["tol"]:
            raise ValueError("Approach band should be larger than tolerance.")
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
        """FRZ-1: data messages only append + mark dirty (no drawing here).
        FRZ-4: bounded drain so bursts cannot starve the Tk event loop."""
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
                    self._beep()   # FRZ-3: beep executes on the Tk thread
                elif t == "temp_point":
                    self.plot_t.append(m["t"])
                    self.plot_temp.append(m["temp"])
                    self.plot_target.append(m["target"])
                    # CRIT-2: heater series lives entirely in the GUI thread.
                    self.plot_heater.append(m["heater"])
                    if m["measuring"] == 1:
                        self.meas_t.append(m["t"])
                        self.meas_temp.append(m["temp"])
                    self._temp_plot_dirty = True
                elif t == "scan_reset":
                    self.scan_f.clear(); self.scan_cp.clear(); self.scan_g.clear()
                    self._decade_ylims.clear()  # new spectrum re-snaps decades
                    self._freq_plot_dirty = True
                elif t == "scan_point":
                    self.scan_f.append(m["freq"])
                    self.scan_cp.append(m["cp"])
                    self.scan_g.append(m["g"])
                    self._freq_plot_dirty = True
                    self._pending_progress = m["progress"]
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
        elif not self.gui_queue.empty():
            # Worker just died with messages still queued — finish draining.
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
        # Cs″ = D·Cs (series-model loss) — the legacy LabVIEW convention,
        # verified against its reference data files; differs from Cp″ by
        # (1 + D²) in general.
        Cs_dp = D * Cs
        return [Q, D, G, B, Cp, Lp, Cs, Ls, Z_mag, theta_rad, X, R,
                theta_deg, Rp, Y_mag, omega, Cp_dp, Cs_dp]

    # ============================================================
    # WORKER THREAD (owns both instruments)
    # ============================================================
    def _hardware_worker_loop(self):
        self.start_time = time.time()
        # CRIT-2: no worker-side `_heater_series` — heater plot values
        # travel through the gui_queue and are accumulated in plot_heater.
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
                self._put_gui_msg("scan_reset")

                # §3b: dynamic PID per setpoint
                self._apply_dynamic_pid(target)

                # STB-1/2/3: two-stage approach + rolling-window stability
                result = self._ramp_and_stabilize(target)
                if not self.is_running or result is False:
                    break
                if result == "timeout":
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ STABILIZATION TIMEOUT at {target} K — "
                             f"proceeding with sweep anyway (unattended-run policy). "
                             f"Check this data point!")

                self._put_gui_msg("log",
                    text=f"Stable. Starting frequency sweep at {target} K.")
                self._put_gui_msg("status",
                    text=f"MEASURING AT {target} K", color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("beep")   # FRZ-3: beep on Tk thread
                done_pts = self._run_frequency_sweep(target, done_pts, total_pts)
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
            self._worker_phase = None
            self._put_gui_msg("worker_done")

    # ------------------------------------------------------------
    # STB-1/2/3: robust ramp + stabilization
    # ------------------------------------------------------------
    def _window_check(self, window, target, p):
        """Rolling-window stability test (STB-2).

        Returns (ok, max_dev, drift_K_per_min).
        ok requires: window spans >= 95% of the configured window length,
        max |T - target| <= tol, AND |linear drift| <= drift limit.
        """
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
        ok = (span >= 0.95 * p["soak"]
              and max_dev <= p["tol"]
              and abs(drift) <= p["drift"])
        return ok, max_dev, drift

    def _ramp_and_stabilize(self, target):
        """Two-stage approach (STB-1) + rolling-window stability (STB-2)
        + optional timeout (STB-3).

        Stage 1 (PRE_RAMP): full-rate ramp to a pre-target that stops
        `app_band` K short of the real target — the setpoint never slews
        into the target at full speed, which is what caused the big
        overshoots below 100 K.
        Stage 2 (FINAL_APPROACH): slow `app_rate` ramp from the pre-target
        to the real target, then wait until the rolling window is stable.

        Returns True (stable), "timeout" (proceed with warning), or
        False (stop requested). Raises RuntimeError on kill switch.
        """
        p = self.params
        temp, _, _ = self._log_temperature_point(target, measuring_flag=0)
        delta = target - temp
        heating = delta > 0

        if abs(delta) > p["app_band"]:
            pre_target = target - math.copysign(p["app_band"], delta)
            self._worker_phase = "PRE_RAMP"
            self.ls_backend.configure_ramp(pre_target, p["rate"],
                                           p["heater_range"])
            self._put_gui_msg("log",
                text=f"Stage 1: fast ramp ({p['rate']} K/min) to pre-target "
                     f"{pre_target:.2f} K ({p['app_band']} K short of {target} K).")
            self._put_gui_msg("status",
                text=f"RAMPING TO {target} K (pre: {pre_target:.1f} K)",
                color=self.CLR_ACCENT_RED)
        else:
            pre_target = None
            self._worker_phase = "FINAL_APPROACH"
            self.ls_backend.configure_ramp(target, p["app_rate"],
                                           p["heater_range"])
            self._put_gui_msg("log",
                text=f"Already within approach band — slow approach "
                     f"({p['app_rate']} K/min) to {target} K.")
            self._put_gui_msg("status",
                text=f"APPROACHING {target} K", color=self.CLR_STABLE_WAIT)

        window = deque()          # (time, temp) rolling stability window
        step_start = time.time()
        last_status = 0.0

        try:
            while self.is_running:
                if self._process_cmd_queue():
                    return False   # stop requested

                temp, _, _ = self._log_temperature_point(target, measuring_flag=0)
                now = time.time()

                if self._worker_phase == "PRE_RAMP":
                    reached = (temp >= pre_target - p["tol"]) if heating \
                        else (temp <= pre_target + p["tol"])
                    if reached:
                        # STB-1 stage 2: hand over to the slow final approach.
                        self._worker_phase = "FINAL_APPROACH"
                        self.ls_backend.set_ramp_rate(p["app_rate"])
                        time.sleep(0.1)
                        self.ls_backend.set_setpoint(target)
                        window.clear()
                        self._put_gui_msg("log",
                            text=f"Pre-target reached ({temp:.3f} K). Stage 2: "
                                 f"slow approach ({p['app_rate']} K/min) to {target} K.")
                        self._put_gui_msg("status",
                            text=f"APPROACHING {target} K", color=self.CLR_STABLE_WAIT)
                else:
                    # FINAL_APPROACH / STABILIZING: rolling-window criterion.
                    window.append((now, temp))
                    while window and (now - window[0][0]) > p["soak"]:
                        window.popleft()
                    ok, max_dev, drift = self._window_check(window, target, p)
                    if ok:
                        self._put_gui_msg("log",
                            text=f"STABLE at {target} K: max dev "
                                 f"{max_dev:.3f} K, drift {drift:+.3f} K/min "
                                 f"over last {p['soak']:.0f} s.")
                        return True
                    # Live status (throttled to every ~3 s to limit messages)
                    if now - last_status > 3.0:
                        last_status = now
                        if max_dev is not None:
                            self._put_gui_msg("status",
                                text=(f"STABILIZING {target} K | "
                                      f"Δmax={max_dev:.3f} K | "
                                      f"drift={drift:+.3f} K/min"),
                                color=self.CLR_STABLE_WAIT)

                # STB-3: overnight-safety timeout
                if p["stab_timeout"] > 0 and (now - step_start) > p["stab_timeout"] * 60.0:
                    return "timeout"

                time.sleep(p["delay"])
        finally:
            self._worker_phase = None
        return False

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
                        # REL-2: never override the slow final approach —
                        # the new rate takes effect from the next stage/step.
                        if self._worker_phase == "FINAL_APPROACH":
                            self._put_gui_msg("log",
                                text="Ramp rate stored; slow final approach "
                                     "active — applies from next ramp stage.")
                        else:
                            try:
                                self.ls_backend.set_ramp_rate(updates["rate"])
                                self._put_gui_msg("log",
                                    text=f"Ramp rate -> {updates['rate']} K/min")
                            except Exception as e:
                                self._put_gui_msg("log",
                                    text=f"Ramp rate update failed: {e}")
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

    def _log_temperature_point(self, target, measuring_flag):
        """Reads Lakeshore, checks overtemp, writes CSV row, queues plot msg.

        CRIT-1: the unused `heater=None` kwarg/parameter has been removed;
        this function always reads the heater itself via get_status().
        Raises RuntimeError if the kill switch trips.
        """
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
        # CRIT-2: heater value rides on the queue msg; no worker-side list.
        self._put_gui_msg("temp_point", t=elapsed, temp=temp, target=target,
                          measuring=measuring_flag, heater=htr)
        return temp, _, htr

    def _run_frequency_sweep(self, target_temp, done_pts, total_pts):
        """Runs the E4980A frequency sweep at one stable setpoint.
        Logs temperature (flag=1) interleaved between frequency points (§3d)."""
        # MAJ-3: timestamp prevents silent overwrite on re-run.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = (f"{self.lcr_params['sample_name']}_{target_temp:.2f}K_"
                 f"{stamp}_FreqScan.txt")
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
                # MIN-6: surface non-zero E4980A status words. Bits indicate
                # overload/out-of-range/bridge warnings per the E4980A manual.
                if status != 0:
                    self._put_gui_msg("log",
                        text=f"⚠️ E4980A status {status} @ {freq:.1f} Hz — row kept, check manual")
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
                # Non-blocking shutdown: poll for worker exit with
                # root.after instead of join() so the GUI stays alive
                # while a slow VISA read drains (was a hard freeze of
                # up to 10 s here). _atexit_shutdown remains the
                # backstop for instrument cleanup after destroy.
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
    CombinedGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()