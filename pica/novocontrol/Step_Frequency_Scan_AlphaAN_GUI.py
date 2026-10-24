"""
Module: Step_Frequency_Scan_AlphaAN_GUI.py
Purpose: Combined Lakeshore 350 temperature control + Novocontrol Alpha-AN
         frequency sweep GUI.
Port of keysight/Step_Frequency_Scan_E4980A_GUI.py v1.7: the Lakeshore
ramp (adaptive rate, low-T caps, two-stage approach, PID tiers), the
rolling-window stabilisation, pause/skip, soft Max-Temp limit, the
temperature log, stabilisation summary, per-setpoint sweep files and the
HARD-1..4 hardening are unchanged. Only the LCR layer is replaced by the
Alpha-AN backend from novocontrol/Frequency_Scan_AlphaAN_GUI.py (EOI-only
framing, read-every-ack, SRQ completion, ZG4 AC ceiling, safe-state on
every exit). What changed with the instrument:
  - AC level -> ACV (Vrms), checked against the frequency-dependent ZG4
    ceiling for the whole sweep at Start, before any instrument is opened.
  - Aperture -> MTM integration time (default 0.5 s); ZG4 wire mode
    (2/3/4, default 2) added; ALC, open/short correction and cable length
    removed (no Alpha equivalent); DC bias field removed.
  - Each point is (Zs', Zs'', f_actual, status) from ZRE?. The
    Frequency column holds f_actual and the 18-parameter conversion uses
    it; the column layout is unchanged. Status 2 = valid; other statuses
    are logged and the point is discarded; status 6 (signal source
    disconnected) aborts the run.
  - HARD-2 reconnect re-opens the Alpha with connect + initialize_instrument
    (*RST, MODE=IMP, ZG4 check, ACV/MTM/FRS). No bias is ever ramped.
  - An Alpha refusal (non-OK ack, 'CR') aborts the run instead of entering
    the retry-forever reconnect: a reset cannot fix it.
Version: V: 1.0 (Alpha-AN port, 22 Oct 2026). Not yet run on the hardware.

SAFETY:  This module never uses DC bias. DCV/DCE are never transmitted
         (the analyzer does have DC bias - see INSTRUMENT).

INSTRUMENT:   The lab system this module was written for, read from
              WinALPHA's auto-detect on 22 Oct 2026:
                Analyzer   Novocontrol ALPHA ("ALPHA, BETA Analyzer"), SN 284
                Interface  ZG4 sample cell interface, SN 284-0
                *IDN?      ALPHA Analyzer, Novocontrol Technologies,
                           6.25_200_40_284_ANB_16012009_G2
                GPIB       address 10 on the BDS PC's NI GPIB card; a Keysight
                           E4991B shares that bus at 17
                DC bias    present (used in WinDETA experiments); maximum voltage
                           not yet confirmed

History of the E4980A source (v1.0 - v1.7) follows unchanged.
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

============================================================
v1.3 — ADVANCED CONTROL (parity with T_Control_L350_Step_GUI_advanced)
============================================================
Self-contained by design: the logic below is an embedded copy of the
helpers in pica/lakeshore/T_Control_L350_Step_GUI_advanced.py (PICA
programs do not import from each other).

  ADV-1  ADAPTIVE RAMP RATE. Per setpoint:
             proposed = |target - current| * Step Factor
             cap      = low-T cap table at min(current, target)
                        (the coldest temperature traversed governs)
             rate     = clamp(min(proposed, cap), Min Rate .. Max Rate)
         First setpoint of a run is slowed further by First-Step x
         (overshoot is worst there). Cap table is editable text,
         default "77:0.3, 100:0.5, else:5" — below 100 K never faster
         than 0.5 K/min (0.3 below 77 K): on the LN2-dewar probe even
         a small heater input overshoots at low T. Untick "Adaptive"
         to use the fixed Ramp Rate field as before.
  ADV-2  APPROACH FROM ONE SIDE (hysteresis-sensitive measurements):
         "Always from below/above" forces the pre-target onto the
         chosen side so the slow final ramp always enters the
         setpoint from that side.
  ADV-3  PAUSE/RESUME + SKIP STEP. Pause holds the current setpoint
         (stability window + timeout clock suspended, logging
         continues). Skip abandons the current stabilization (that
         setpoint gets no sweep) or aborts the remainder of a running
         sweep.
  ADV-4  SOFT MAX-TEMP LIMIT (GUI, default 320 K, <= 340 K): graceful
         abort (ramp stopped, heater off) if exceeded; setpoints
         above it are rejected at Start; heater range 0 rejected.
         The hardcoded 340 K kill switch remains the backstop.
  ADV-5  Tolerance band (target +/- tol) drawn on the temperature
         plot for the current setpoint.
  ADV-6  Logging: temperature CSV gains Ramp_rate_K_min and Phase
         columns (appended at the end — existing readers unaffected);
         new per-setpoint <sample>_<stamp>_StabSummary.csv records
         rate used, approach mode, stabilization duration, outcome.

============================================================
v1.4 — INTUITIVE PANELS + RATE-CONFLICT FIXES
============================================================
  UI-1   Left panel reorganized by intent (mirrors
         T_Control_L350_Step_GUI_advanced): "Stabilization Criteria"
         (tol/window/drift/timeout/poll), "Temperature Ramp" (rate
         source + low-T caps + approach band/rate/mode), "Instrument &
         Safety" (Max Temp, heater range, Auto-PID, VISA). The old
         grab-bag "Lakeshore 350 Settings" frame is gone.
  UI-2   Rate source is a RADIO pair (Adaptive / Fixed); the inactive
         group is greyed out, so exactly one rate source is ever
         editable. Radios are locked while a sequence runs (the mode
         is only read at Start). Fixed Rate default is 2.0 K/min
         (was 10.0 — unsafe on the LN2-dewar probe).
  RATE-1 The low-T cap table is now a GLOBAL SAFETY ENVELOPE: it
         clamps EVERY rate sent to the Lakeshore — adaptive, fixed,
         the slow approach rate (both send points), and live mid-run
         rate updates (`capped_rate`; each clamp is logged). This also
         fixes the inversion where below the caps the stage-1 ramp
         crawled while the "slow" stage-2 approach was faster.
  RATE-2 `compute_ramp_rate` clamp order fixed: Min Rate floors the
         proposed rate BEFORE the cap, so Min Rate can no longer
         silently defeat a low-T safety cap.
  RATE-3 "Always from above" setpoints whose pre-target (T + band)
         would exceed Max Temp are rejected at Start instead of
         SoftLimitAbort-ing the run at 3 a.m.
  PID-1  "Auto PID per setpoint" checkbox (parity with the advanced
         T-controller): untick to keep manual Live-PID values instead
         of having them silently overwritten at every setpoint.

============================================================
v1.5 — 2x2 PLOT GRID + PERSISTENT SPECTRUM
============================================================
  UI-3   All four plots (Temperature, Heater %, Cp, G) now live in
         ONE 2x2 figure on a single canvas — no more toggling
         between the two plot tabs.
  UI-4   The completed Cp/G spectrum PERSISTS on screen, titled with
         its measurement temperature, through the entire ramp +
         stabilization to the next setpoint. `scan_reset` moved from
         the top of the setpoint loop to the moment the sweep starts
         (so the plot is cleared only when new points are imminent);
         a new `scan_done` message marks the held spectrum as
         "Last scan: <T> K".

============================================================
v1.6 — ULTRA-SLOW PID BELOW 100 K (anti-windup)
============================================================
  PID-2  Lab runs (2026-07-10) showed the Slow preset (P=0.5, I=4)
         still overshoots at 80-90 K, and after the overshoot the
         temperature would NOT come back down (it crept upward).
         Diagnosis: integral windup. The LS350 output is
         ~ P*[e + (I/1000)*Int(e dt) + ...]; the integral accumulated
         during the ramp keeps the heater output nonzero after the
         temperature passes the setpoint, and on the LN2-dewar probe
         the only way down is passive cooling. New PID_ULTRA
         (P=0.2, I=1, D=0) — P*I windup product ~10x smaller — is now
         the Auto-PID tier below 100 K. Slow stays in the live-PID
         preset dropdown for manual use. Expect a slower final
         approach in exchange for reduced overshoot and much faster
         recovery.

============================================================
v1.7 — UNATTENDED-RUN HARDENING (Passive v1.4 pattern)
============================================================
  HARD-1 Windows keep-awake (SetThreadExecutionState) while a
         sequence runs — an overnight run can no longer be killed
         by system sleep. Display may still sleep.
  HARD-2 RETRY-FOREVER COMM RECOVERY. Any VISA/comm failure in the
         worker (Lakeshore status reads, E4980A measurements) now
         enters a reconnect loop with escalating backoff
         (5→10→30→60 s) instead of aborting the run: both sessions
         are closed and re-opened (the E4980A is fully
         re-configured, bias re-ramped; the Lakeshore reconnect is
         SESSION-ONLY and never re-sends setpoints — the 350 keeps
         executing its ramp while the link is down). The current
         frequency point is retried after recovery. Stop stays
         responsive throughout. REL-1's quick retries remain the
         first line of defense.
  HARD-3 FSYNC-PER-ROW writes: temperature-log rows, stabilization
         summary rows and every sweep data row are forced to the
         physical disk (flush + os.fsync) so a power cut cannot
         lose OS-buffered data.
  HARD-4 NO MODAL DIALOGS during/after a run (unattended policy):
         the "Sequence Complete" messagebox is replaced by the
         banner + log + triple beep; safety aborts and critical
         errors also beep. `_beep(times=N)` upgraded to the
         Passive v1.4 multi-beep helper. Start-time validation
         dialogs and the exit confirm (user-clicked) remain.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import os
import time
import math
import csv
import queue
import sys  # TerminalTee swaps sys.stdout / sys.stderr
import threading
import atexit
import traceback
import platform
import ctypes                    # HARD-1: Windows keep-awake during a run
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


def launch_list_maker():
    """Finds and launches the List Maker utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        maker_path = os.path.join(
            script_dir, "..", "utils", "List_Maker_GUI.py"
        )
        if not os.path.exists(maker_path):
            messagebox.showerror(
                "File Not Found",
                f"List Maker not found at expected path:\n{maker_path}",
            )
            return
        Process(target=run_script_process, args=(maker_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch List Maker: {e}"
        )


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
# ADV-1/2/4 helpers (embedded copy — PICA programs stay self-contained)
# ============================================================
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
    """Adaptive ramp rate for one step (ADV-1; formula in the docstring).

    `p` is the validated params dict (keys: step_factor, ramp_caps,
    ramp_default_cap, min_rate, max_rate, first_factor).
    Returns (rate_K_per_min, reason_str) — reason_str goes to the log.

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
    rate and the slow approach rate, not just the adaptive rate. The
    cap is looked up at min(current_T, target_T) — the coldest
    temperature the ramp traverses.
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


# ============================================================
# BACKEND: Lakeshore 350
# ============================================================
class Lakeshore_Backend:
    HARD_TEMP_LIMIT_K = 340.0  # hardcoded kill switch (§3a) — not in GUI

    def __init__(self):
        self.lakeshore = None
        self.last_visa = None    # HARD-2: remembered for reconnect()
        # True once a session has been confirmed as a 350: shutdown() then
        # knows a missing session means heater-off could not be sent.
        self.was_connected = False
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    def connect(self, visa_address, retry_stale_idn=False):
        """Open the session and confirm a Model 350 before any write.

        retry_stale_idn (reconnect only - the address was already
        confirmed as a 350): a fresh session after a comm fault can read
        the reply the interrupted query left behind, so a non-350 answer
        gets a VISA device clear (as get_status does) and ONE more *IDN?
        before it is refused. At Start a wrong answer is refused at once:
        an unknown device gets nothing but *IDN?."""
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")
        self.last_visa = visa_address   # HARD-2: for reconnect()
        inst = self.rm.open_resource(visa_address)
        # *IDN? is the ONLY thing a wrong instrument ever receives: the
        # model is checked before the first write (*CLS included), and the
        # session becomes self.lakeshore only once confirmed, so neither a
        # refused nor a failed handshake leaves shutdown() a session to
        # send RAMP/RANGE to.
        try:
            inst.timeout = 10000
            idn = inst.query("*IDN?").strip()
            if retry_stale_idn and "MODEL350" not in idn.upper().replace(
                    " ", ""):
                print(f"  Lakeshore answered '{idn}' on reconnect - maybe "
                      "a stale reply; device clear, then *IDN? once more.")
                try:
                    inst.clear()
                except Exception as e:
                    print(f"  Lakeshore device clear warning: {e}")
                idn = inst.query("*IDN?").strip()
        except Exception:
            try:
                inst.close()
            except Exception:
                pass
            raise
        if "MODEL350" not in idn.upper().replace(" ", ""):
            try:
                inst.close()
            except Exception:
                pass
            raise RuntimeError(
                f"'{visa_address}' answered '{idn}', which is not a Lake "
                "Shore Model 350. Refusing to send Lakeshore commands to "
                "it. Pick the right address.")
        self.lakeshore = inst
        self.was_connected = True
        self.lakeshore.write("*CLS")  # do NOT *RST mid-run
        return idn

    def reconnect(self):
        """HARD-2: close and re-open the VISA session only. NEVER
        touches heater range / ramp / setpoint — the 350 keeps
        executing its last program while the PC link is down, and the
        retry-forever loop must not disturb it on recovery."""
        try:
            if self.lakeshore:
                self.lakeshore.close()
        except Exception:
            pass
        self.lakeshore = None
        return self.connect(self.last_visa, retry_stale_idn=True)

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
        """Ramp off, heater off, close. Never raises. Returns False when a
        confirmed 350 was connected earlier but its session is gone (lost
        in a comm recovery), so heater-off could NOT be sent - nothing is
        sent to an unconfirmed device to make up for it."""
        if not self.lakeshore:
            if getattr(self, "was_connected", False):
                print("WARNING: no Lakeshore session (lost during comm "
                      "recovery): RAMP off / RANGE 0 could NOT be sent. "
                      "Turn the heater off at the controller.")
                return False
            return True
        try:
            self.stop_ramp()
            self.lakeshore.close()
        except Exception as e:
            print(f"Lakeshore shutdown warning: {e}")
        finally:
            self.lakeshore = None
        return True


# ===============================================================================
# BACKEND: NOVOCONTROL ALPHA-AN  (copied from
# novocontrol/Frequency_Scan_AlphaAN_GUI.py - keep the two in step)
# ===============================================================================
#
# SAFETY CONSTANTS - hardware ceilings. NEVER raise these to "make it work".
# ===============================================================================

# Alpha-AN generator span, from the manual. Every requested frequency is
# checked against this before a single byte reaches the bus.
FREQ_HW_MIN, FREQ_HW_MAX = 3e-5, 20e6

# Generator AC amplitude ceiling (Vrms), from the ZG4 spec: 3 Vrms up to
# 4 MHz, 2 Vrms from 4 to 10 MHz (ZG4 technical data chart), 1 Vrms above
# 10 MHz. Frequency-dependent, so it is enforced per-point rather than as
# one flat number. The 2 Vrms mid tier is the conservative reading of the
# manual ("3 Vrms <= 10 MHz" in one place, "2 Vrms above 4 MHz" in the
# ZG4 chart) - we take the lower of the two.
ACV_MAX_LOW_FREQ = 3.0        # <= 4 MHz
ACV_MAX_MID_FREQ = 2.0        # 4 - 10 MHz
ACV_MAX_HIGH_FREQ = 1.0       # > 10 MHz
ACV_FREQ_MID_HZ = 4e6
ACV_FREQ_BREAK_HZ = 10e6

# Integration time bounds (s). MTM=0 is legal and means "shortest available";
# the analyzer never integrates less than one period regardless, which is
# where the recipe's "max of 0.5 s or 1 period" behaviour comes from. The
# 0.01 s floor here is conservative but harmless.
MTM_MIN_S, MTM_MAX_S = 0.01, 1000.0

# ZRE? status field. 2 = Result Valid; anything else is not trustworthy.
# Meanings verified against the Alpha manual.
STATUS_RESULT_VALID = 2
# Statuses that abort the whole sweep rather than just flagging one point.
# 6 = the signal source was disconnected mid-measurement (hardware fault).
STATUS_FATAL = frozenset({6})
STATUS_MEANINGS = {
    0: "Invalid (result buffer empty)",
    1: "Measurement still in progress",
    2: "Result valid",
    3: "Voltage V1 out of range for sample measurement (check ACV/sample)",
    4: "Current out of range for sample measurement (check ACV/sample)",
    5: "Voltage V1 out of range for reference measurement",
    6: "Signal source disconnected during measurement (hardware fault)",
}

# Acknowledgment strings a Novocontrol executable command leaves in the
# result buffer. Every set command (ACV=, MTM=, GFR=, FRS=, ...) answers
# "OK" on success or one of these codes on failure, and that response MUST
# be read - otherwise it desynchronizes every later query. (MST is the
# exception: it leaves no buffered ack; completion is SRQ + ZRE?.)
CMD_ERROR_MEANINGS = {
    "CA": "Cannot execute during an active calibration",
    "CR": "Required test-interface calibration does not exist (Recalibrate)",
    "CN": "Unavailable while the CE output of a POT/GAL interface is "
          "connected",
    "EC": "System connection test (CONCHECK) required",
    "ER": "General command error",
    "II": "Command not supported by the connected test interface",
    "IM": "Measurement type not supported in the current mode",
    "IP": "Invalid command parameter",
    "MR": "Not allowed while a measurement/calibration is running",
    "RE": "Command error (see manual)",
    "UC": "Unknown command",
}

# ZG4 interface code reported by INTTYP? (the ZG4 is code 5, not a string).
ZG4_INTERFACE_CODE = 5

# Timeouts (s) for the SRQ waits. A REF calibration takes ~30 s.
SRQ_TIMEOUT_MEASURE_S = 120.0
SRQ_TIMEOUT_CAL_S = 300.0


def point_timeout_s(freq_hz, mtm_s):
    """SRQ budget for one point.

    A point integrates the sample and (reference mode "Always") the
    reference, each for at least MTM and at least one signal period. A fixed
    120 s budget is too short below ~0.02 Hz or above MTM ~55 s, so scale it;
    never go below SRQ_TIMEOUT_MEASURE_S.
    """
    return max(SRQ_TIMEOUT_MEASURE_S,
               4.0 * max(float(mtm_s), 1.0 / float(freq_hz)) + 60.0)


def freq_tag(freq_hz):
    """Frequency label for file names and the plot dropdown.

    int(f) collapsed sub-Hz and fractional frequencies (0.1 and 0.5 Hz both
    became "0"), interleaving two frequencies in one file. Integer
    frequencies keep their old form, so existing file names do not change.
    """
    f = float(freq_hz)
    return str(int(f)) if f.is_integer() else format(f, ".10g")

# After *RST the Alpha drops off the GPIB bus while it reinitializes (no
# listener: every write fails with VI_ERROR_NLISTENERS, NI-488.2's ENOL) and
# only answers again some seconds later. Seen on hardware 22 Oct 2026: a
# fixed 1 s sleep was too short and MODE=IMP failed. Commands are retried on
# "no listeners" and on timeout, until this deadline.
# Through 64-bit Keysight VISA (23 Oct 2026) the first *IDN? after *RST did
# not fail with "no listeners" but hung in viWrite for the whole 60 s session
# timeout (VI_ERROR_TMO). While waiting, each try therefore gets a short
# timeout of its own, and a timed-out try is followed by a device clear.
# On one PC (23 Oct 2026) the Alpha answered 1.4 s after *RST through the
# 32-bit NI-488.2 edition and 4.1 s through 64-bit VISA, so the first try
# waits RESET_SETTLE_S and the deadline is generous.
# A session opened just after a park (*RST) can meet the same wait, so
# connect retries *IDN? the same way for CONNECT_READY_TIMEOUT_S.
RESET_SETTLE_S = 2.0
RESET_READY_TIMEOUT_S = 60.0
RESET_RETRY_INTERVAL_S = 0.5
RESET_POLL_TIMEOUT_MS = 3000
CONNECT_READY_TIMEOUT_S = 15.0

# VISA status codes, as pyvisa reports them in VisaIOError.error_code.
# NLISTENERS: no listener at that address; the write put no byte on the bus,
# so it is the one GPIB error that is always safe to retry.
# NCIC: the board is not Controller-In-Charge - typically left behind by
# WinALPHA / WinDETA; cleared by one Interface Clear on the board.
# TMO: the transfer did not complete in time; part of a command may have
# reached the device, so it is retried only after a device clear.
VI_ERROR_NLISTENERS = -1073807265
VI_ERROR_NCIC = -1073807264
VI_ERROR_TMO = -1073807339

# The Alpha's address on the BDS bus, as WinALPHA's auto-detect reports it
# ("Found device: Address: 10 - IDN string: ALPHA Analyzer, ..."). The
# Keysight E4991B shares that bus at 17.
DEFAULT_ALPHA_PAD = 10

EPS0_SI = 8.8541878128e-12   # F/m  - used for the geometric capacitance C0
EPS0_CM = 8.8541878128e-14   # F/cm - used only so sigma lands in S/cm

# Wire modes exposed by the ZG4 interface.
WIRE_MODES = ("2", "3", "4")


# WinDETA column header, copied from Frequency_Scan_AlphaAN_GUI.py. Order and
# names are FIXED - WinFIT parses this positionally. Do not add, remove or
# reorder columns. Zp comes BEFORE Sig, matching the WinDETA export paired
# with the mes_def (data_file_for_ref/Fscan_data_Novo_Windeta.dat).
WINDETA_HEADER = (
    " Freq. [Hz]\t Eps'    \t Eps''   \t Modulus'  \t Modulus''  \t"
    " Zp' [Ohms]\t Zp'' [Ohms]\t Sig' [S/cm]\t Sig'' [S/cm]"
)


def compute_c0(diameter_mm, thickness_mm):
    """Geometric (empty-cell) capacitance of a round-plate electrode, in F.

    A = pi * (D/2)^2 ;  C0 = eps0 * A / d
    """
    if diameter_mm <= 0 or thickness_mm <= 0:
        raise ValueError("Diameter and thickness must both be positive.")
    d_m = diameter_mm * 1e-3
    t_m = thickness_mm * 1e-3
    area = math.pi * (d_m / 2.0) ** 2
    return EPS0_SI * area / t_m


def compute_c0_from_area(area_mm2, thickness_mm):
    """Geometric (empty-cell) capacitance from electrode AREA, in F.

    For irregular electrodes where the area is known directly.
    C0 = eps0 * A / d, with A in mm^2 (as WinDETA) and d in mm.
    """
    if area_mm2 <= 0 or thickness_mm <= 0:
        raise ValueError("Area and thickness must both be positive.")
    a_m2 = area_mm2 * 1e-6
    t_m = thickness_mm * 1e-3
    return EPS0_SI * a_m2 / t_m


def c0_from_params(params):
    """C0 from the geometry the user chose (validated, positive)."""
    if params["geometry_mode"] == "area":
        return compute_c0_from_area(params["area_mm2"],
                                    params["thickness_mm"])
    return compute_c0(params["diameter_mm"], params["thickness_mm"])


def impedance_to_dielectric(zr, zi, f_actual, c0):
    """Convert one complex-impedance point to the nine WinDETA quantities,
    exactly as Frequency_Scan_AlphaAN_GUI.py does (see its docstring for
    the derivation and the reference-export verification):

        eps'  = -X / (w * C0 * |Z|^2)
        eps'' =  R / (w * C0 * |Z|^2)
        M'    =  eps'  / (eps'^2 + eps''^2)
        M''   =  eps'' / (eps'^2 + eps''^2)
        Zp'   =  (R^2 + X^2) / R          (parallel-equivalent, no C0)
        Zp''  = -(R^2 + X^2) / X
        sig'  =  w * eps0 * eps''        (eps0 in F/cm -> sigma in S/cm)
        sig'' = -w * eps0 * (eps' - 1)   (WinDETA subtracts the vacuum)

    Returns a 9-tuple in WINDETA_HEADER column order.
    """
    omega = 2.0 * math.pi * f_actual
    omega_safe = omega if omega != 0 else 1e-20

    z_mag_sq = zr ** 2 + zi ** 2
    z_mag_sq_safe = z_mag_sq if z_mag_sq != 0 else 1e-20

    c0_safe = c0 if c0 != 0 else 1e-20
    denom = omega_safe * c0_safe * z_mag_sq_safe

    eps1 = -zi / denom
    eps2 = zr / denom

    eps_sq = eps1 ** 2 + eps2 ** 2
    eps_sq_safe = eps_sq if eps_sq != 0 else 1e-20
    m1 = eps1 / eps_sq_safe
    m2 = eps2 / eps_sq_safe

    sig1 = omega * EPS0_CM * eps2
    sig2 = -omega * EPS0_CM * (eps1 - 1.0)

    zr_safe = zr if zr != 0 else 1e-20
    zi_safe = zi if zi != 0 else 1e-20
    zp1 = z_mag_sq / zr_safe    # 1/G = parallel resistance
    zp2 = -z_mag_sq / zi_safe   # 1/B = parallel reactance

    return (f_actual, eps1, eps2, m1, m2, zp1, zp2, sig1, sig2)


def windeta_header_lines(params, target_temp, when):
    """The three header lines of the WinDETA frequency-scan export, as the
    frequency scan module writes them. Line 1 is the WinDETA comment
    (sample name and setpoint), dated the WinDETA way."""
    date_str = f"{when.day:2d}.{when.month:02d}.{when.year}"
    time_str = f"{when.hour}:{when.minute:02d}"
    return [
        f"{params['sample_name']} {target_temp:.2f} K, {date_str}, "
        f"{time_str}",
        f"Fixed value(s) :  AC Volt  [Vrms]={params['acv']:.4e}",
        WINDETA_HEADER,
    ]


def dat_metadata_lines(params, c0_farads, target_temp, frequencies, when):
    """'#' comment rows for a PICA per-setpoint sweep file: what WinDETA
    keeps in its measurement definition (SAMPLE / START CONDITIONS / LIST
    / IMPEDANCE DEVICE sections of mes_def_Fscan.txt), so a file documents
    itself. The first row is the one this module always wrote.

    ASCII only - the lab PCs are cp1252 and the Plotter skips '#' rows.
    The analyzer settings below are the ones initialize_instrument sends.
    """
    freqs = [float(f) for f in frequencies]
    if params["geometry_mode"] == "area":
        evaluation = "Area electrode"
        size = f"A = {params['area_mm2']} mm^2"
    else:
        evaluation = "Round plate electrode"
        size = f"D = {params['diameter_mm']} mm"
    wires = "4 Wire" if str(params["wire_mode"]) == "4" else "2 Wire"
    return [
        f"# Sample: {params['sample_name']} | T_set = {target_temp} K | "
        f"ACV: {params['acv']} Vrms | MTM: {params['mtm']} s | "
        f"Wire: {params['wire_mode']} | Instrument: Alpha-AN + ZG4",
        f"# Geometry: {size}, d = {params['thickness_mm']} mm, "
        f"C0 = {c0_farads:.5e} F",
        "# Last REF calibration: not recorded by PICA (Start never "
        "calibrates)",
        f"# Comment    : {params['sample_name']} {target_temp:.2f} K",
        f"# Date       : {when.strftime('%d.%m.%Y %H:%M:%S')}",
        f"# Evaluation : {evaluation}",
        f"# Size       : {size}",
        f"# Thickness  = {params['thickness_mm']} mm",
        "# C-Spacer   = 0 pF, A-Spacer = 0 mm^2, edge compensation off",
        f"# AcV        = {params['acv']} Vrms (no DC bias)",
        f"# Frequency list : {len(freqs)} pts, {min(freqs):g} - "
        f"{max(freqs):g} Hz",
        f"# Temperature    : setpoint {target_temp} K, held stable on the "
        f"{params['temp_controller']} before the sweep",
        f"# Freq delay     : {params['delay']} s",
        f"# Analyzer       : Novocontrol Alpha-AN + ZG4 at "
        f"{params['lcr_visa']}",
        "# Reference Measurement                : Always, 3 auto "
        "capacitors max (ZREFMODE=-3)",
        "# Low capacity open calibration        : Off",
        "# Low impedance load short calibration : On (ZSLCAL=1)",
        "# Low loss reference capacitor corr.   : On (ZLLCOR=1)",
        f"# 4 Wire Front State                   : {wires}",
        "# Driven HI / LO Shield                : Off / Off (DRS=0 0)",
        f"# Integration time                     : {params['mtm']} s (MTM)",
    ]


def validate_output_settings(params):
    """Geometry and output-format checks, run at Start before any
    instrument opens."""
    if params["geometry_mode"] == "area":
        if params["area_mm2"] <= 0 or params["thickness_mm"] <= 0:
            raise ValueError("Area and thickness must both be positive.")
    else:
        if params["diameter_mm"] <= 0 or params["thickness_mm"] <= 0:
            raise ValueError("Diameter and thickness must both be positive.")
    if not (params["save_pica"] or params["save_windeta"]):
        raise ValueError(
            "Tick at least one output format (PICA or WinDETA).")


def append_line_durably(path, line, encoding="utf-8", newline=None):
    """Open, append one line, flush and fsync, close (HARD-3: a power cut
    loses at most the point being written)."""
    with open(path, "a", encoding=encoding, newline=newline) as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def acv_ceiling(freq_hz):
    """ZG4 AC-voltage ceiling (Vrms) at a given frequency."""
    if freq_hz <= ACV_FREQ_MID_HZ:
        return ACV_MAX_LOW_FREQ
    if freq_hz <= ACV_FREQ_BREAK_HZ:
        return ACV_MAX_MID_FREQ
    return ACV_MAX_HIGH_FREQ


def status_message(status):
    """Human-readable meaning of a ZRE? status code."""
    return STATUS_MEANINGS.get(status, f"unknown status {status}")


def parse_inttyp(response):
    """Extract the numeric interface code from an INTTYP? response.

    Format is 'INTTYP=%d1 %d2' (code, then serial); the ZG4 is code 5.
    """
    text = response.strip(" \t\r\n\x00\x10")
    if "=" in text:
        text = text.split("=", 1)[1]
    return int(text.split()[0])


def visa_error_code(exc):
    """The VISA status code carried by a pyvisa VisaIOError, else None."""
    code = getattr(exc, "error_code", None)
    return int(code) if code is not None else None


def gpib_board_of(visa_addr):
    """'GPIB0::10::INSTR' -> 0; 'GPIB::10' -> 0; None if not a GPIB address."""
    head = str(visa_addr).strip().upper().split("::", 1)[0]
    if not head.startswith("GPIB"):
        return None
    tail = head[4:]
    return int(tail) if tail.isdigit() else 0


def parse_zre(response):
    """Parse a ZRE? response into (Zs', Zs'', f_actual, status, ref).

    The analyzer answers 'ZRE=Zs' Zs'' freq status ref'. The leading
    'ZRE=' echo is stripped if present; fields are whitespace separated.

    Responses terminate on EOI (plus an EOS byte), so strip trailing
    non-whitespace terminator bytes (CR/NUL/DLE) that .strip() misses.
    """
    text = response.strip(" \t\r\n\x00\x10")
    if "=" in text:
        text = text.split("=", 1)[1]
    fields = text.replace(",", " ").split()
    # A bare 'CR' (Recalibrate) is returned when load-short correction
    # (ZSLCAL=1) is on but no load-short calibration is stored.
    if fields and fields[0].upper() == "CR":
        raise RuntimeError(
            "Instrument returned 'CR' (Recalibrate): load-short correction "
            "(ZSLCAL=1) is enabled but no load-short calibration is stored. "
            "Run a load-short calibration, or disable ZSLCAL."
        )
    if len(fields) < 5:
        raise ValueError(f"Malformed ZRE? response: {response!r}")
    zr = float(fields[0])
    zi = float(fields[1])
    f_actual = float(fields[2])
    status = int(float(fields[3]))
    ref = int(float(fields[4]))
    return zr, zi, f_actual, status, ref


# ===============================================================================
# FREQUENCY LISTS  (copied from novocontrol/Frequency_Scan_AlphaAN_GUI.py -
# keep the two in step; only the default differs)
# ===============================================================================

# Frozen frequency series: 20 Hz, x1.1 per step, final point clamped to
# exactly 1 MHz. 115 points. Generated once, deliberately NOT recomputed at
# runtime and NOT user-editable, so every scan in the lab is directly
# comparable point-for-point.
FREQUENCIES_115PT_1MHZ = (
    2.000000e+01, 2.200000e+01, 2.420000e+01, 2.662000e+01, 2.928200e+01,
    3.221020e+01, 3.543122e+01, 3.897434e+01, 4.287178e+01, 4.715895e+01,
    5.187485e+01, 5.706233e+01, 6.276857e+01, 6.904542e+01, 7.594997e+01,
    8.354496e+01, 9.189946e+01, 1.010894e+02, 1.111983e+02, 1.223182e+02,
    1.345500e+02, 1.480050e+02, 1.628055e+02, 1.790860e+02, 1.969947e+02,
    2.166941e+02, 2.383635e+02, 2.621999e+02, 2.884199e+02, 3.172619e+02,
    3.489880e+02, 3.838868e+02, 4.222755e+02, 4.645031e+02, 5.109534e+02,
    5.620487e+02, 6.182536e+02, 6.800790e+02, 7.480869e+02, 8.228956e+02,
    9.051851e+02, 9.957036e+02, 1.095274e+03, 1.204801e+03, 1.325282e+03,
    1.457810e+03, 1.603591e+03, 1.763950e+03, 1.940345e+03, 2.134379e+03,
    2.347817e+03, 2.582599e+03, 2.840859e+03, 3.124945e+03, 3.437439e+03,
    3.781183e+03, 4.159301e+03, 4.575231e+03, 5.032754e+03, 5.536030e+03,
    6.089633e+03, 6.698596e+03, 7.368456e+03, 8.105301e+03, 8.915831e+03,
    9.807415e+03, 1.078816e+04, 1.186697e+04, 1.305367e+04, 1.435904e+04,
    1.579494e+04, 1.737443e+04, 1.911188e+04, 2.102306e+04, 2.312537e+04,
    2.543791e+04, 2.798170e+04, 3.077987e+04, 3.385785e+04, 3.724364e+04,
    4.096800e+04, 4.506480e+04, 4.957129e+04, 5.452841e+04, 5.998126e+04,
    6.597938e+04, 7.257732e+04, 7.983505e+04, 8.781856e+04, 9.660041e+04,
    1.062605e+05, 1.168865e+05, 1.285751e+05, 1.414327e+05, 1.555759e+05,
    1.711335e+05, 1.882469e+05, 2.070716e+05, 2.277787e+05, 2.505566e+05,
    2.756122e+05, 3.031735e+05, 3.334908e+05, 3.668399e+05, 4.035239e+05,
    4.438763e+05, 4.882639e+05, 5.370903e+05, 5.907993e+05, 6.498793e+05,
    7.148672e+05, 7.863539e+05, 8.649893e+05, 9.514882e+05, 1.000000e+06,
)

# Second frozen series: the WinDETA measurement definition
# (data_file_for_ref/mes_def_Fscan.txt, LIST ORDERING). 20 Hz, x1.4 per
# step, 40 points, final point exactly 10 MHz. Transcribed verbatim from
# the mes_def - deliberately NOT recomputed and NOT user-editable.
FREQUENCIES_40PT_10MHZ = (
    2.000000e+01, 2.800000e+01, 3.920000e+01, 5.488000e+01, 7.683200e+01,
    1.075648e+02, 1.505907e+02, 2.108270e+02, 2.951578e+02, 4.132209e+02,
    5.785093e+02, 8.099130e+02, 1.133878e+03, 1.587430e+03, 2.222401e+03,
    3.111362e+03, 4.355907e+03, 6.098269e+03, 8.537577e+03, 1.195261e+04,
    1.673365e+04, 2.342711e+04, 3.279796e+04, 4.591714e+04, 6.428399e+04,
    8.999759e+04, 1.259966e+05, 1.763953e+05, 2.469534e+05, 3.457347e+05,
    4.840286e+05, 6.776401e+05, 9.486961e+05, 1.328175e+06, 1.859444e+06,
    2.603222e+06, 3.644511e+06, 5.102316e+06, 7.143242e+06, 1.000000e+07,
)

# Third frozen series: the list the Keysight E4980A modules sweep
# (Frequency_Scan_E4980A_GUI), so an Alpha scan lines up point-for-point
# with an LCR-meter scan. Linear inside each decade: 40-990 Hz in 10 Hz
# steps, then 100 Hz, 1 kHz and 10 kHz steps, then 1-2 MHz in 100 kHz
# steps. 377 points. Built from integer ranges, so every value is exact.
FREQUENCIES_377PT_2MHZ = tuple(
    float(f) for f in (
        *range(40, 1000, 10),
        *range(1000, 10000, 100),
        *range(10000, 100000, 1000),
        *range(100000, 1000000, 10000),
        *range(1000000, 2000001, 100000),
    )
)

# The only three scan recipes this program will run. Keys are the combobox
# labels; the default is listed first. The default is the E4980A list this
# module always swept, so a run started without touching the box measures
# exactly the points it did before the list became selectable.
FREQ_PRESETS = {
    "377 pts, 40 Hz - 2 MHz (E4980A list)": FREQUENCIES_377PT_2MHZ,
    "115 pts, 20 Hz - 1 MHz (x1.1)": FREQUENCIES_115PT_1MHZ,
    "40 pts, 20 Hz - 10 MHz (x1.4)": FREQUENCIES_40PT_10MHZ,
}
DEFAULT_FREQ_PRESET = "377 pts, 40 Hz - 2 MHz (E4980A list)"

# Guardrail: refuse to even import with a frequency the hardware cannot reach.
for _name, _series in FREQ_PRESETS.items():
    assert all(
        FREQ_HW_MIN <= _f <= FREQ_HW_MAX for _f in _series
    ), f"Preset {_name!r} contains a point outside the Alpha-AN range."

# Last choice in the Frequency List box: a list the user types. Not a
# preset, so it never goes through the guardrail above; every value is
# checked by parse_frequency_list instead (and again by
# validate_parameters before anything reaches the bus).
CUSTOM_FREQ_LABEL = "Custom list (type your own below)"
# Sanity cap only: at ~1 s per point this is already a 15+ minute sweep
# at EVERY temperature step.
CUSTOM_FREQ_MAX_POINTS = 1000


def parse_frequency_list(text):
    """User-typed frequency list (Hz) -> tuple of floats, in typed order.

    Commas separate values; semicolons, spaces and new lines are accepted
    too, so a list pasted from List Maker or a spreadsheet works as is.
    Every value must lie inside the Alpha-AN generator span
    FREQ_HW_MIN-FREQ_HW_MAX. Raises ValueError naming the first bad entry.
    """
    tokens = text.replace(",", " ").replace(";", " ").split()
    if not tokens:
        raise ValueError("Custom frequency list is empty.")
    if len(tokens) > CUSTOM_FREQ_MAX_POINTS:
        raise ValueError(
            f"Custom frequency list has {len(tokens)} points; the limit is "
            f"{CUSTOM_FREQ_MAX_POINTS}."
        )
    values = []
    for tok in tokens:
        try:
            f = float(tok)
        except ValueError:
            raise ValueError(
                f"Custom frequency list: '{tok}' is not a number. "
                f"Use values in Hz such as 20, 1000, 1e6."
            ) from None
        if not math.isfinite(f):
            raise ValueError(
                f"Custom frequency list: '{tok}' is not a finite number."
            )
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(
                f"Custom frequency list: {tok} Hz is outside the Alpha-AN "
                f"range {FREQ_HW_MIN:g} Hz to {FREQ_HW_MAX:g} Hz."
            )
        values.append(f)
    return tuple(values)



def validate_alpha_parameters(frequencies, acv, mtm, wire_mode):
    """Reject anything unsafe BEFORE a single byte reaches the Alpha.

    The ZG4 AC ceiling is frequency-dependent, so the binding limit is the
    lowest ceiling over the whole frequency list. Raises ValueError.
    """
    if not frequencies:
        raise ValueError("Frequency list is empty.")
    for f in frequencies:
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(
                f"Frequency {f} Hz is outside the Alpha-AN range "
                f"{FREQ_HW_MIN:g}-{FREQ_HW_MAX:g} Hz.")
    max_acv = min(acv_ceiling(f) for f in frequencies)
    if not (0.0 < acv <= max_acv):
        raise ValueError(
            f"AC voltage {acv} Vrms outside the 0 < V <= {max_acv} Vrms "
            f"safety limit (frequency-dependent ZG4 ceiling).")
    if not (MTM_MIN_S <= mtm <= MTM_MAX_S):
        raise ValueError(
            f"Integration time {mtm} s outside {MTM_MIN_S}-{MTM_MAX_S} s.")
    if wire_mode not in WIRE_MODES:
        raise ValueError(f"Wire mode must be one of {WIRE_MODES}.")


class AlphaAN_Backend:
    """All GPIB traffic with the Novocontrol Alpha-AN mainframe.

    Not SCPI. Commands are Novocontrol proprietary high-level commands plus
    the IEEE-488.2 common set (*IDN?, *RST).
    """
    # Column layout unchanged from the E4980A program. 'Frequency' holds
    # the Alpha's actual generator frequency (f_actual from ZRE?).
    DATA_HEADER = (
        "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\t"
        "R(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''\tT_set(K)"
    )

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.rm = None
        # True between MST/ZRUNCAL and the corresponding SRQ. Tells the
        # shutdown path whether an MBK abort is actually needed.
        self._task_may_be_running = False
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    # --- connection ---------------------------------------------------

    def connect(self, visa_addr):
        """Open the session and confirm we are talking to an Alpha with ZG4."""
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        inst = self.rm.open_resource(visa_addr)
        inst.timeout = 60000  # 60 s; a long MTM at low frequency is slow
        # The Alpha-AN terminates responses with EOI (plus an EOS byte) and
        # accepts commands on EOI only - it does NOT use a newline terminator.
        inst.read_termination = ""
        inst.write_termination = ""
        inst.send_end = True
        self.instrument = inst
        is_alpha = False

        try:
            try:
                idn = self._query_idn_until_answered(
                    CONNECT_READY_TIMEOUT_S, "while connecting").strip()
            except Exception as exc:
                # A board left not-CIC by another program (WinALPHA /
                # WinDETA) fails every transfer. Reclaim it ONCE with
                # Interface Clear and retry; any other error is raised.
                if visa_error_code(exc) != VI_ERROR_NCIC:
                    raise
                board = gpib_board_of(visa_addr)
                print(f"  GPIB board {board} is not Controller-In-Charge "
                      f"(another GPIB program such as WinALPHA may have "
                      f"taken the bus). Reclaiming it with Interface "
                      f"Clear...")
                self.send_interface_clear(board)
                try:
                    idn = self._query_idn_until_answered(
                        CONNECT_READY_TIMEOUT_S, "while connecting").strip()
                except Exception as exc2:
                    if visa_error_code(exc2) == VI_ERROR_NCIC:
                        raise ConnectionError(
                            f"GPIB board {board} is still not "
                            f"Controller-In-Charge after Interface Clear. "
                            f"Close WinALPHA / WinDETA and any other GPIB "
                            f"program, then try again.") from exc2
                    raise
            # A wrong address must not get the Alpha shutdown sequence
            # (ACV=0, ZCONSPL=0, *RST) sent to another instrument.
            if not any(k in idn.upper() for k in ("ALPHA", "NOVOCONTROL")):
                raise ConnectionError(
                    f"{visa_addr} answered {idn!r}, which is not a "
                    f"Novocontrol Alpha. Check the address.")
            is_alpha = True
            # INTTYP? returns 'INTTYP=<code> <serial>'; the ZG4 is code 5,
            # NOT the literal string "ZG4".
            code = parse_inttyp(inst.query("INTTYP?"))
            if code != ZG4_INTERFACE_CODE:
                raise ConnectionError(
                    f"Expected a ZG4 sample interface (INTTYP code "
                    f"{ZG4_INTERFACE_CODE}), got code {code}. Refusing to "
                    f"continue."
                )
            # Serial-poll once to clear any stale status left on the bus, so
            # the first wait_for_srq cannot latch onto a leftover event.
            inst.read_stb()
        except Exception:
            # Never leave a half-open session behind on a failed handshake.
            if is_alpha:
                self.close_instrument()
            else:
                try:
                    inst.close()
                except Exception:
                    pass
                self.instrument = None
            raise

        print(f"  Connected: {idn} | ZG4 interface code {code}")
        return idn, code

    def send_interface_clear(self, board):
        """Assert IFC on a GPIB board and make it Controller-In-Charge.

        IFC resets the GPIB interface of every device on the bus (they are
        unaddressed); it is not a device clear and sends no command bytes,
        so instrument settings and measurements are not touched. Only ever
        sent after the board reported 'not CIC', never on a healthy board.
        """
        if board is None:
            raise ConnectionError(
                "Interface Clear needs a GPIB address (GPIBn::...).")
        intfc = self.rm.open_resource(f"GPIB{board}::INTFC")
        try:
            intfc.send_ifc()
        finally:
            intfc.close()
        print(f"  Interface Clear sent on GPIB{board}.")

    # --- command acknowledgment ---------------------------------------

    def _exec(self, cmd):
        """Send one executable command and consume its acknowledgment.

        Every Novocontrol set command (ACV=, MTM=, GFR=, FRS=, ...) writes
        'OK' (or an error code) into the result buffer, and that response
        MUST be read - unread acks desynchronize every later query, e.g.
        ZRE? would return a stale 'OK' instead of data. MST is the one
        exception (no buffered ack; completion is SRQ + ZRE?) and is
        written directly, never through here.
        """
        resp = self.instrument.query(cmd).strip(" \t\r\n\x00\x10")
        if resp != "OK":
            meaning = CMD_ERROR_MEANINGS.get(resp, "unrecognized response")
            raise RuntimeError(
                f"Command {cmd!r} failed: instrument answered {resp!r} "
                f"({meaning})."
            )

    def _query_idn_until_answered(self, timeout_s, situation):
        """*IDN?, repeated while the Alpha is away reinitializing.

        While the Alpha reinitializes after *RST it is not a listener. Through
        NI-488.2 every write then fails at once with VI_ERROR_NLISTENERS,
        which put nothing on the bus. Through 64-bit Keysight VISA the write
        hangs until the session timeout instead (VI_ERROR_TMO). Both are
        retried: each try runs on a short timeout, a timed-out try is
        followed by a device clear (flushes any half-received command), and
        the session timeout is restored afterwards. Any other error is
        raised at once.
        Returns the *IDN? answer.
        """
        inst = self.instrument
        start = time.monotonic()
        session_timeout = inst.timeout
        inst.timeout = RESET_POLL_TIMEOUT_MS
        try:
            while True:
                try:
                    return inst.query("*IDN?")
                except Exception as exc:
                    code = visa_error_code(exc)
                    if code not in (VI_ERROR_NLISTENERS, VI_ERROR_TMO):
                        raise
                    elapsed = time.monotonic() - start
                    kind = ("no listener" if code == VI_ERROR_NLISTENERS
                            else "timeout")
                    print(f"  Waiting for the Alpha {situation}: "
                          f"{elapsed:.1f} s, {kind}.")
                    if code == VI_ERROR_TMO:
                        try:
                            inst.clear()
                        except Exception as clear_exc:
                            print(f"  Device clear failed: {clear_exc}")
                    if elapsed >= timeout_s:
                        raise ConnectionError(
                            f"The Alpha did not rejoin the GPIB bus within "
                            f"{timeout_s:.0f} s {situation}. Last error: "
                            f"{exc}") from exc
                time.sleep(RESET_RETRY_INTERVAL_S)
        finally:
            inst.timeout = session_timeout

    def _reset_and_set_impedance_mode(self,
                                      timeout_s=RESET_READY_TIMEOUT_S):
        """*RST, wait until the Alpha answers again, then MODE=IMP.

        *RST leaves no Novocontrol ack. The first *IDN? goes out after
        RESET_SETTLE_S and is repeated until it is answered - the same
        handshake JUMP uses after *RST (github.com/JMoVS/JUMP,
        MeasurementHardware.py, ALPHA.initialize_instrument), including its
        extra 1 s settle before MODE=IMP.
        """
        inst = self.instrument
        inst.write("*RST")
        start = time.monotonic()
        time.sleep(RESET_SETTLE_S)
        self._query_idn_until_answered(timeout_s, "after *RST")
        print(f"  *RST complete; Alpha answering after "
              f"{time.monotonic() - start:.1f} s.")
        time.sleep(1.0)
        self._exec("MODE=IMP")

    def _exec_tolerant(self, cmd):
        """_exec for shutdown paths: never raises, reports success."""
        try:
            resp = self.instrument.query(cmd).strip(" \t\r\n\x00\x10")
        except Exception as e:
            print(f"  Warning: {cmd} failed during shutdown: {e}")
            return False
        if resp != "OK":
            print(f"  Warning: {cmd} answered {resp!r} during shutdown.")
            return False
        return True

    # --- SRQ ------------------------------------------------------------

    def _wait_for_srq(self, timeout_s, context):
        """Block until the analyzer asserts SRQ, then clear the request.

        Completion is SRQ-driven precisely so we never poll ZTSTAT?/ZRE?
        during a measurement - the manual warns that the extra bus traffic
        degrades point accuracy. On timeout we abort first, and only THEN
        query ZTSTAT? (legitimate once the measurement is stopped).
        """
        inst = self.instrument
        try:
            inst.wait_for_srq(int(timeout_s * 1000))
        except Exception as exc:
            state = self.abort_and_diagnose()
            raise TimeoutError(
                f"No SRQ within {timeout_s:.0f} s during {context}. "
                f"Aborted (MBK). ZTSTAT? reports: {state}. "
                f"Underlying: {exc}"
            )

        self._task_may_be_running = False
        stb = inst.read_stb()
        if not stb & 0x01:
            print(f"  WARNING: SRQ during {context} with STB=0x{stb:02X} "
                  f"(bit 0 not set).")
        return stb

    def abort_and_diagnose(self):
        """Abort any running task, then read the task state ONCE.

        ZTSTAT? is a post-fault diagnostic only. Calling it here is safe
        because MBK has already stopped the measurement.
        """
        state = "unavailable"
        try:
            # MBK is an executable command: read its ack like any other,
            # but tolerate a non-OK answer - we are already in a fault path.
            resp = self.instrument.query("MBK").strip(" \t\r\n\x00\x10")
            self._task_may_be_running = False
            if resp != "OK":
                state = f"MBK answered {resp!r}; "
        except Exception as e:
            return f"MBK failed: {e}"
        try:
            state = self.instrument.query("ZTSTAT?").strip()
        except Exception as e:
            state = f"ZTSTAT? failed: {e}"
        return state

    # --- measurement setup ----------------------------------------------

    def initialize_instrument(self, p):
        """Reset, confirm the interface, then apply measurement settings.

        *RST does NOT delete stored calibration data. Test interfaces are
        auto-identified on connection and the mainframe reloads that
        interface's latest cal set from internal memory, which is why no
        recalibration is needed after a reset or an interface swap. So a
        sweep here runs on the previous REF calibration, by design.
        """
        print("\n--- [Backend] Initializing Novocontrol Alpha-AN ---")
        self.params = p


        inst = self.instrument
        if inst is None:
            raise ConnectionError("Instrument is not connected.")

        self._reset_and_set_impedance_mode()

        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(f"ZG4 not present (INTTYP code {code}).")

        # Settings AFTER any calibration: a calibration task may change
        # MTM, ZTDEL, ACV, DCE, IAC and IRG. ZREFMODE/ZLLCOR/ZSLCAL already
        # hold these values after *RST; we write them anyway - defensive
        # against a non-default state, and it documents intent at the call
        # site. Each _exec consumes the command's 'OK' ack, which also
        # fully synchronizes the bus (no settle sleeps needed).
        #
        # ZREFMODE (verified against the manual): negative = auto reference
        # mode, magnitude = max number of internal reference capacitors
        # combined. -3 = auto, up to 3 caps = the mes_def's "Reference
        # Measurement: Always, 3 auto capacitors max".
        self._exec("ZREFMODE=-3")
        self._exec("ZLLCOR=1")      # low-loss correction on
        self._exec("ZSLCAL=1")      # apply stored load-short cal data
        # "Low capacity open calibration: Off" (mes_def): RESOLVED - there
        # is no analyzer command for it. Open calibration is a host-software
        # correction (WinDETA measures Z_open and computes
        # 1/Zs = 1/Zmes - 1/Zopen itself). This program does not implement
        # it, so the mes_def's "Off" is matched by construction.
        self._exec(f"FRS={p['wire_mode']}")
        self._exec("DRS=0 0")       # driven shields off

        self._exec(f"ACV={p['acv']:.6g}")
        self._exec(f"MTM={p['mtm']:.6g}")

        # DCV / DCE are NEVER sent: this module does not use DC bias.

        print(f"  Configured: ACV={p['acv']} Vrms, MTM={p['mtm']} s, "
              f"FRS={p['wire_mode']}-wire")

    def reconnect(self):
        """HARD-2: close and fully re-open from the stored params:
        connect (IDN, ZG4 check) + initialize_instrument (*RST, MODE=IMP,
        ACV, MTM, FRS). Works after an instrument power-cycle. DC bias is
        never sent, so there is nothing to re-ramp."""
        try:
            self.release_for_reconnect()   # B1: no *RST without rejoin wait
        except Exception:
            pass
        self.instrument = None
        self.connect(self.params["lcr_visa"])
        self.initialize_instrument(self.params)

    # --- per-point -------------------------------------------------------

    def measure_point(self, freq, timeout_s=None):
        """Set frequency, trigger one point, wait for SRQ, read the result.

        Returns (Zs', Zs'', f_actual, status, ref). The caller must use
        f_actual - not the requested frequency - for the conversion.
        """
        inst = self.instrument
        if inst is None:
            raise ConnectionError("Instrument is not connected.")
        if timeout_s is None:
            timeout_s = point_timeout_s(freq, self.params.get('mtm', 0.0))

        self._exec(f"GFR={freq:.6e}")
        # Clear any stale status immediately before triggering, so a fast
        # point cannot let wait_for_srq latch onto a leftover completion.
        inst.read_stb()
        self._task_may_be_running = True
        # MST leaves NO buffered ack (unlike set commands): completion is
        # signalled by SRQ and the result is fetched with ZRE?.
        inst.write("MST")
        self._wait_for_srq(timeout_s, f"measurement at {freq:.6g} Hz")

        # ZRE? also clears status bit 0.
        return parse_zre(inst.query("ZRE?"))

    # --- shutdown --------------------------------------------------------

    def safe_state(self):
        """Drive the generator to a safe state. Never raises.

        Safe for any error handler to call. MBK first (a task may be
        mid-flight), then park the AC generator (ACV=0) and disconnect the
        current input to high impedance (ZCONSPL=0) - the manual's defined
        safe idle state. *RST would also park the generator, but it is
        attempted separately in close_instrument so a failing *RST cannot
        prevent the ACV=0 that actually matters.
        """
        inst = self.instrument
        if inst is None:
            return
        if self._task_may_be_running:
            if self._exec_tolerant("MBK"):
                self._task_may_be_running = False
        for cmd in ("ACV=0", "ZCONSPL=0"):
            self._exec_tolerant(cmd)

    def release_for_reconnect(self):
        """Park the generator and drop the VISA session for a reconnect.

        No *RST here: close_instrument's *RST is followed by a 0.2 s sleep
        only, and the Alpha is off the bus for seconds after a reset, so an
        immediate connect would fail. initialize_instrument() does the *RST
        with the proper rejoin wait. Never raises."""
        inst = self.instrument
        if inst is None:
            return
        try:
            self.safe_state()
        except Exception as e:
            print(f"  Warning: safe state before reconnect failed: {e}")
        try:
            inst.close()
        except Exception as e:
            print(f"  Warning closing VISA session: {e}")
        finally:
            self.instrument = None

    def close_instrument(self):
        """Safe-state, reset, release the session. Never leaves output live."""
        print("--- [Backend] Closing Alpha-AN connection. ---")
        if not self.instrument:
            return
        try:
            self.safe_state()
            try:
                # *RST parks the generator (ACV=0, DCE=0) and preserves the
                # stored calibration tables.
                self.instrument.write("*RST")
                time.sleep(0.2)
            except Exception as e:
                print(f"  Warning: *RST failed ({e}); escalating to RSTH.")
                try:
                    self.instrument.write("RSTH")
                    time.sleep(0.5)
                except Exception as e2:
                    print(f"  Warning: RSTH also failed: {e2}")
        except Exception as e:
            print(f"  Warning during shutdown: {e}")
        finally:
            try:
                self.instrument.close()
                print("  Alpha-AN connection closed.")
            except Exception as e:
                print(f"  Warning closing VISA session: {e}")
            finally:
                self.instrument = None


# ============================================================
# FRONTEND: Combined GUI
# ============================================================
class CombinedGUI:
    PROGRAM_VERSION = "1.0-AlphaAN"  # Alpha-AN port of E4980A v1.7

    # HARD-1: SetThreadExecutionState flags (Windows keep-awake)
    ES_CONTINUOUS      = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
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
    # PID-2: LS350 integral term scales with BOTH P and I, and the LN2
    # probe can only cool passively — so below 100 K windup must stay
    # tiny or an overshoot never recovers. Keep I > 0 (I=0 disables
    # integral action entirely -> permanent offset; see MIN-7).
    PID_ULTRA = (0.2, 1.0, 0)     # below 100 K — anti-windup
    PID_SLOW = (0.5, 4.0, 0)      # manual use; winds up below 100 K
    PID_MEDIUM = (20.0, 15.0, 0)  # 100 K and above

    # ADV-2: approach-mode selector -> stabilizer argument
    APPROACH_MODES = {
        "Two-stage (default)": None,
        "Always from below": "below",
        "Always from above": "above",
    }

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
            f"Alpha-AN & L350: T-Control + Freq Sweep v{self.PROGRAM_VERSION}"
            " - 64-bit (VISA)"
        )
        self.root.geometry("1700x950")
        self.root.minsize(1400, 850)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.ls_backend = Lakeshore_Backend()
        self.lcr_backend = AlphaAN_Backend()

        self.is_running = False
        self.worker_thread = None

        # Two-way queues (fix #1, #3)
        self.cmd_queue = queue.Queue()   # GUI -> worker
        self.gui_queue = queue.Queue()   # worker -> GUI

        atexit.register(self._atexit_shutdown)

        self.logo_image = None
        self.save_dir = ""
        self.c0_farads = None   # set at Start from the geometry inputs

        # Frequency list: replaced at Start by the one chosen in the
        # Frequency List box (default: the E4980A list, as before)
        self.sweep_frequencies = FREQ_PRESETS[DEFAULT_FREQ_PRESET]

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

        # ADV-3/5: pause/skip + tolerance-band state
        self._paused = False           # worker-only write
        self._skip_requested = False   # worker-only write
        self._current_rate = 0.0       # ramp rate for the CSV Phase column
        self._current_target = None    # setpoint being worked on (for caps)
        self._band_params = None       # (target, tol) of current setpoint
        self._band_dirty = False
        self.band_patch = None

        # Y-scale mode: 'auto' uses decade-snapped log when the data
        # spans >= 1 decade and linear otherwise; 'log'/'linear' force it.
        self.y_scale_var = tk.StringVar(value="auto")
        self._decade_ylims = {}

        # UI-4: scan label state — ("measuring"|"done", T_set) or None.
        # Rendered as the Cp axis title by _redraw_tick.
        self._scan_info = None

        # REL-2: worker-side phase marker (worker thread only writes it;
        # used to defer live ramp-rate updates during final approach)
        self._worker_phase = None

        # PID presets for the live PID panel
        self.PID_PRESETS = {
            "Ultra-slow (P=0.2, I=1, D=0)": self.PID_ULTRA,
            "Slow (P=0.5, I=4, D=0)": self.PID_SLOW,
            "Medium (P=20, I=15, D=0)": self.PID_MEDIUM,
            "Fast (P=50, I=20, D=0)": (50.0, 20.0, 0),
        }

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        # Terminal output (print, warnings, tracebacks) also goes to the console.
        install_terminal_log(self)
        # FRZ-1: single periodic redraw loop — runs for the app lifetime
        self.root.after(self.REDRAW_MS, self._redraw_tick)
        self.log("Alpha-AN + L350 GUI v1.0 initialized. 40 Hz – 2 MHz sweep, "
                 "340 K kill switch active.")
        self.log("Stabilization: two-stage approach + rolling-window "
                 "(tolerance AND drift) criterion.")
        self.log("Ramp: adaptive or fixed (radio buttons); the low-T cap "
                 "table clamps EVERY rate incl. fixed + approach "
                 "(defaults: <77 K: 0.3, <100 K: 0.5, else 5 K/min). "
                 "Pause/Skip + soft Max-Temp limit available.")

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
        style.configure("TCheckbutton", background=self.CLR_FRAME_BG)
        style.configure("TRadiobutton", background=self.CLR_FRAME_BG)
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
        ttk.Label(header, text="Alpha-AN & L350: T-Control + Dielectric Spectroscopy"
                               "   [64-bit edition, VISA]",
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
        sf.grid_rowconfigure(7, weight=1)

        self._create_info_panel(sf, 0)
        self._create_sequence_panel(sf, 1)
        self._create_stability_panel(sf, 2)
        self._create_ramp_panel(sf, 3)
        self._create_safety_panel(sf, 4)
        self._create_lcr_settings_panel(sf, 5)
        self._create_pid_panel(sf, 6)
        self._create_console_panel(sf, 7)

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

    def _create_stability_panel(self, parent, row):
        """When do we call the temperature stable (STB-2/3)."""
        frame = ttk.LabelFrame(parent, text="Stabilization Criteria")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)
        self.entries = {}
        self._create_grid_entry(frame, "Tolerance (±K):", "tol", "0.5", 0, 0)
        self._create_grid_entry(frame, "Window (s):", "soak", "120", 0, 3)
        self._create_grid_entry(frame, "Drift Lim (K/min):", "drift", "0.10", 1, 0)
        self._create_grid_entry(frame, "Timeout (min, 0=off):",
                                "stab_timeout", "90", 1, 3)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "1", 2, 0)

        # MAJ-4: push unlocked values to the worker mid-run.
        ttk.Button(frame, text="Apply Live Updates",
                   command=self._send_live_updates
                   ).grid(row=3, column=0, columnspan=6, sticky="ew",
                          padx=10, pady=(2, 6))

    def _create_ramp_panel(self, parent, row):
        """How we travel to a setpoint: rate source + final approach.
        Exactly ONE rate source (adaptive or fixed) is active; the other
        group is greyed out so it is always obvious which rate wins."""
        frame = ttk.LabelFrame(parent, text="Temperature Ramp")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
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
        self.approach_cb = ttk.Combobox(frame, textvariable=self.approach_var,
                                        values=list(self.APPROACH_MODES.keys()),
                                        state="readonly", width=16)
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

    def _create_safety_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Instrument & Safety")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)

        # ADV-4: soft max-temp limit (hardcoded 340 K remains the backstop)
        self._create_grid_entry(frame, "Max Temp (K):", "max_temp", "320", 0, 0)
        ttk.Label(frame, text="Heater Range:").grid(row=0, column=3,
                                                    sticky="w", padx=10, pady=5)
        self.heater_range_var = tk.StringVar(value="5 (Max)")
        self.heater_cb = ttk.Combobox(
            frame, textvariable=self.heater_range_var,
            values=["0 (Off)", "1", "2", "3", "4", "5 (Max)"],
            state="readonly", width=8)
        self.heater_cb.grid(row=0, column=4, columnspan=2, sticky="ew", padx=5)
        self.heater_cb.bind("<<ComboboxSelected>>", self._on_heater_range_changed)

        self.var_auto_pid = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame,
                        text="Auto PID per setpoint (Ultra-slow <100 K, Medium ≥100 K)",
                        variable=self.var_auto_pid
                        ).grid(row=1, column=0, columnspan=6, sticky="w",
                               padx=10, pady=2)

        ttk.Label(frame, text="LS VISA:").grid(row=2, column=0, sticky="w",
                                               padx=10, pady=5)
        self.ls_cb = ttk.Combobox(frame, state="readonly", width=18)
        self.ls_cb.grid(row=2, column=1, columnspan=5, sticky="ew", padx=5)

    def _create_lcr_settings_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Alpha-AN Settings")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)
        self.lcr_entries = {}

        # LCR-1 Layout Fix: Restructured grid to strictly avoid overlaps.
        # Row 0: Sample Name (spanning all columns)
        self._add_lcr_entry(frame, "Sample Name:", "sample_name", 0, 0, 3, "Sample")

        # Row 1: AC Voltage and Integration Time
        # No DC bias field: this module never sends DCV/DCE.
        self._add_lcr_entry(frame, "AC Voltage (Vrms):", "acv", 1, 0, 1, "1.0")
        # MTM: 0.5 s matches the lab's WinDETA definition ("Maximum of
        # 0.500 Seconds or 1 Periods"); the Alpha never integrates less
        # than one period.
        self._add_lcr_entry(frame, "Integration (s):", "mtm", 1, 2, 1, "0.5")

        # Row 2: Freq Delay and ZG4 Wire Mode
        # Delay default 0: SRQ completion makes a settle sleep unnecessary.
        self._add_lcr_entry(frame, "Freq Delay (s):", "delay", 2, 0, 1, "0")
        ttk.Label(frame, text="ZG4 Wire Mode:").grid(row=2, column=2, sticky="w", padx=5, pady=2)
        self.wire_cb = ttk.Combobox(frame, values=list(WIRE_MODES), state="readonly", width=8)
        self.wire_cb.set("2")
        self.wire_cb.grid(row=2, column=3, sticky="w", padx=5, pady=2)

        # Row 3: Alpha VISA (ALC / open-short / cable length have no
        # Alpha equivalent and were removed)
        ttk.Label(frame, text="Alpha-AN VISA:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.lcr_cb = ttk.Combobox(frame, state="readonly", width=28)
        self.lcr_cb.grid(row=3, column=1, columnspan=3, sticky="ew", padx=5, pady=2)

        # Row 4: Geometry, Area (mm²) or Diameter (mm) as WinDETA, plus the
        # live C0. Each mode keeps its own value, so flipping the toggle
        # never turns an area into a diameter: defaults D = 1 mm,
        # A = 1 mm², d = 1 mm.
        self.geom_mode_var = tk.StringVar(value="area")
        self._geom_values = {"area": "1.0", "diameter": "1.0"}
        gf = ttk.Frame(frame); gf.grid(row=4, column=0, columnspan=4, sticky="w", padx=5, pady=2)
        ttk.Label(gf, text="Geometry:").pack(side="left")
        ttk.Radiobutton(gf, text="Area (mm²)", value="area", variable=self.geom_mode_var,
                        command=self._on_geom_mode_change).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(gf, text="Diameter (mm)", value="diameter", variable=self.geom_mode_var,
                        command=self._on_geom_mode_change).pack(side="left", padx=(8, 0))
        self.lbl_c0 = ttk.Label(gf, text="C0 = --")
        self.lbl_c0.pack(side="left", padx=(12, 0))

        # Row 5: Geometry value and thickness
        self.lbl_geom = self._add_lcr_entry(frame, "Area (mm²):", "geom_value", 5, 0, 1, "1.0")
        self._add_lcr_entry(frame, "Thickness (mm):", "thickness_mm", 5, 2, 1, "1.0")
        for key in ("geom_value", "thickness_mm"):
            self.lcr_entries[key].bind("<KeyRelease>", self._update_c0_label)
        self._update_c0_label()

        # Row 6: Output formats. Both on by default; at least one required.
        self.fmt_pica_var = tk.BooleanVar(value=True)
        self.fmt_windeta_var = tk.BooleanVar(value=True)
        ff = ttk.Frame(frame); ff.grid(row=6, column=0, columnspan=4, sticky="w", padx=5, pady=2)
        ttk.Label(ff, text="Save as:").pack(side="left")
        ttk.Checkbutton(ff, text="PICA format (.dat)", variable=self.fmt_pica_var).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(ff, text="WinDETA format (.txt)", variable=self.fmt_windeta_var).pack(side="left", padx=(8, 0))

        # Row 7: Frequency list. Three frozen recipes (never editable, so
        # runs stay comparable point-for-point) plus a custom list the user
        # types. The custom box and its List Maker button are greyed unless
        # chosen. Read once at Start, locked during a run.
        fl = ttk.Frame(frame); fl.grid(row=7, column=0, columnspan=4, sticky="ew", padx=5, pady=2)
        fl.grid_columnconfigure(1, weight=1)
        ttk.Label(fl, text="Frequency List:").grid(row=0, column=0, sticky="w")
        self.freq_cb = ttk.Combobox(fl, state="readonly",
                                    values=list(FREQ_PRESETS) + [CUSTOM_FREQ_LABEL])
        self.freq_cb.set(DEFAULT_FREQ_PRESET)
        self.freq_cb.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))
        self.freq_cb.bind("<<ComboboxSelected>>", self._on_freq_list_change)
        self.lbl_custom_freq = ttk.Label(
            fl, text=f"Frequencies in Hz, comma-separated "
                     f"({FREQ_HW_MIN:g} to {FREQ_HW_MAX:g} Hz):")
        self.lbl_custom_freq.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.list_maker_button = ttk.Button(fl, text="List Maker", width=10,
                                            command=launch_list_maker)
        self.list_maker_button.grid(row=1, column=2, sticky="e", pady=(4, 0))
        # width=1: let the grid size the box instead of 80 characters
        self.custom_freq_text = tk.Text(fl, height=3, width=1, wrap="word",
                                        font=self.FONT_CONSOLE, relief="solid", bd=1)
        self.custom_freq_text.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        self.custom_freq_text.bind("<KeyRelease>", self._update_custom_freq_hint)
        self.lbl_custom_hint = ttk.Label(fl, text="", justify="left",
                                         wraplength=self.LEFT_PANEL_WIDTH - 60)
        self.lbl_custom_hint.grid(row=3, column=0, columnspan=3, sticky="w")
        self._on_freq_list_change()

        # Row 8: Start/Stop/Scan Buttons
        bf = ttk.Frame(frame); bf.grid(row=8, column=0, columnspan=4, sticky="ew", pady=5, padx=5)
        bf.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(bf, text="Start Sequence", style="Start.TButton", command=self.start_sequence)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.stop_button = ttk.Button(bf, text="Stop All", style="Stop.TButton", state="disabled", command=self.stop_sequence)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(bf, text="Scan VISA", command=self._scan_for_visa).grid(row=0, column=2, sticky="ew", padx=2)
        # ADV-3: pause/resume + skip step
        self.pause_button = ttk.Button(bf, text="Pause", state="disabled",
                                       command=self._toggle_pause)
        self.pause_button.grid(row=1, column=0, sticky="ew", padx=2, pady=(4, 0))
        self.skip_button = ttk.Button(bf, text="Skip Step", state="disabled",
                                      command=self._skip_step)
        self.skip_button.grid(row=1, column=1, sticky="ew", padx=2, pady=(4, 0))

        # Row 9: Browse Save Button
        ttk.Button(frame, text="Browse Save…", command=self._browse_save).grid(row=9, column=0, columnspan=4, sticky="ew", padx=5, pady=(0, 5))

        # Row 10: Save Directory Label
        self.save_dir_lbl = ttk.Label(frame, text="Save dir: (not set)", foreground=self.CLR_ACCENT_GOLD)
        self.save_dir_lbl.grid(row=10, column=0, columnspan=4, sticky="w", padx=5)

    def _on_freq_list_change(self, _event=None):
        """Grey the custom-list box and List Maker button unless chosen."""
        if self.freq_cb.get() == CUSTOM_FREQ_LABEL:
            self.custom_freq_text.config(state="normal", bg="white",
                                         fg=self.CLR_TEXT_DARK)
            self.lbl_custom_freq.config(foreground=self.CLR_TEXT_DARK)
            self.list_maker_button.config(state="normal")
            self.lbl_custom_hint.grid()
            self._update_custom_freq_hint()
        else:
            self.custom_freq_text.config(state="disabled", bg="#D9D2CB",
                                         fg="#8A837D")
            self.lbl_custom_freq.config(foreground="#8A837D")
            self.list_maker_button.config(state="disabled")
            # An empty Label still draws as a blank strip, so hide it.
            self.lbl_custom_hint.grid_remove()

    def _update_custom_freq_hint(self, _event=None):
        """Live readout under the custom box: point count and span, or
        the reason the list would be refused at Start."""
        if self.freq_cb.get() != CUSTOM_FREQ_LABEL:
            return
        text = self.custom_freq_text.get("1.0", "end")
        if not text.strip():
            self.lbl_custom_hint.config(
                text="Type or paste a list, e.g. 20, 100, 1000, 1e4, 1e5",
                foreground=self.CLR_TEXT_DARK)
            return
        try:
            freqs = parse_frequency_list(text)
        except ValueError as e:
            self.lbl_custom_hint.config(text=str(e), foreground="#B00020")
            return
        self.lbl_custom_hint.config(
            text=f"{len(freqs)} points, {min(freqs):g} Hz to "
                 f"{max(freqs):g} Hz",
            foreground=self.CLR_TEXT_DARK)

    def _selected_frequencies(self):
        """(label for the log, frequency tuple) for the chosen list."""
        choice = self.freq_cb.get()
        if choice == CUSTOM_FREQ_LABEL:
            freqs = parse_frequency_list(
                self.custom_freq_text.get("1.0", "end"))
            return f"Custom list ({len(freqs)} pts)", freqs
        return choice, FREQ_PRESETS[choice]

    def _on_geom_mode_change(self):
        """Relabel the geometry entry when the Diameter/Area toggle flips,
        parking the old mode's value and restoring the new mode's."""
        new_mode = self.geom_mode_var.get()
        old_mode = "diameter" if new_mode == "area" else "area"
        entry = self.lcr_entries["geom_value"]
        self._geom_values[old_mode] = entry.get()
        entry.delete(0, "end")
        entry.insert(0, self._geom_values[new_mode])
        self.lbl_geom.config(
            text="Area (mm²):" if new_mode == "area" else "Diameter (mm):")
        self._update_c0_label()

    def _update_c0_label(self, _event=None):
        try:
            g = float(self.lcr_entries["geom_value"].get())
            t = float(self.lcr_entries["thickness_mm"].get())
            if self.geom_mode_var.get() == "area":
                c0 = compute_c0_from_area(g, t)
            else:
                c0 = compute_c0(g, t)
            self.lbl_c0.config(text=f"C0 = {c0:.5e} F")
        except (ValueError, ZeroDivisionError):
            self.lbl_c0.config(text="C0 = -- (check geometry)")

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

        # UI-3: single 2x2 plot grid — all plots visible at once, no tabs
        plot_frame = ttk.Frame(panel)
        plot_frame.grid(row=1, column=0, sticky="nsew")
        self._build_plots(plot_frame)

    def _build_plots(self, parent):
        """UI-3: one figure, 2x2 grid. Left column: Temperature and
        Heater % vs time (shared x). Right column: Cp and G vs
        frequency (shared log x)."""
        self.fig = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.fig.add_subplot(221)
        self.ax_heater = self.fig.add_subplot(223, sharex=self.ax_temp)
        self.ax_cp = self.fig.add_subplot(222)
        self.ax_g = self.fig.add_subplot(224, sharex=self.ax_cp)

        self.line_target, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_GREEN, ls="--", label="Target")
        self.line_temp, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker="o", ms=3, ls="-", label="Temp")
        self.scat_meas, = self.ax_temp.plot([], [], ls="", marker="o", ms=5, color=self.CLR_MEAS, label="Measuring (flag=1)")
        self.ax_temp.set_ylabel("Temperature (K)"); self.ax_temp.grid(True, ls="--", alpha=0.6)
        self.ax_temp.legend(loc="best", frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis="x", which="both", bottom=False, labelbottom=False)

        self.line_heater, = self.ax_heater.plot([], [], color=self.CLR_ACCENT_GOLD, marker=".", ms=3, ls="-")
        self.ax_heater.set_xlabel("Time (s)"); self.ax_heater.set_ylabel("Heater (%)")
        self.ax_heater.grid(True, ls="--", alpha=0.6)

        self.line_cp, = self.ax_cp.plot([], [], color="#C00000", marker="o", ms=3, ls="-")
        self.ax_cp.set_ylabel("Cp (F)"); self.ax_cp.set_xscale("log")
        self.ax_cp.grid(True, ls="--", alpha=0.7)
        self.ax_cp.tick_params(axis="x", which="both", labelbottom=False)
        self.line_g, = self.ax_g.plot([], [], color=self.CLR_MEAS, marker="s", ms=3, ls="-")
        self.ax_g.set_xlabel("Frequency (Hz)"); self.ax_g.set_ylabel("G (S)")
        self.ax_g.set_xscale("log"); self.ax_g.grid(True, ls="--", alpha=0.7)

        # top leaves room for the UI-4 scan-temperature title on ax_cp
        self.fig.subplots_adjust(left=0.09, right=0.98, top=0.93,
                                 bottom=0.08, hspace=0.15, wspace=0.28)

        scale_bar = ttk.Frame(parent)
        scale_bar.pack(side="top", anchor="w", padx=5, pady=(5, 0))
        ttk.Label(scale_bar, text="Y scale:").pack(side="left")
        for text, val in (("Auto", "auto"), ("Log", "log"),
                          ("Linear", "linear")):
            ttk.Radiobutton(scale_bar, text=text, value=val,
                            variable=self.y_scale_var,
                            command=self._on_y_scale_change).pack(
                side="left", padx=(8, 0))
        # FRZ-5: pack toolbar BEFORE canvas so it stays visible at the bottom
        self.canvas_plots = FigureCanvasTkAgg(self.fig, parent)
        tb = NavigationToolbar2Tk(self.canvas_plots, parent, pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas_plots.get_tk_widget().pack(fill="both", expand=True)

    def _on_y_scale_change(self):
        """Re-snap decade limits when the Y-scale mode changes.
        FRZ-1: never draws directly — just marks the plot dirty and lets
        the throttled _redraw_tick do the drawing."""
        self._decade_ylims.clear()
        self._freq_plot_dirty = True

    def _apply_y_scale(self, ax, values, key):
        """Adaptive Y-scale driven by self.y_scale_var ('auto'|'log'|'linear').

        log:    LabVIEW-style decade autoscale — snap y-limits to
                [10^floor(log10(min_pos)), 10^ceil(log10(max_pos))],
                expand only (cached in self._decade_ylims[key]) so the
                scale never jitters per point.
        linear: plain relim/autoscale_view.
        auto:   log when the positive data spans >= 1 decade, else
                linear, so narrow-span data is not squashed onto a
                single decade band. Data only accumulates during a
                sweep, so the span is monotone and auto cannot flicker
                back from log to linear mid-sweep.
        Falls back to linear whenever there is no positive finite data.
        Returns True when a log scale was applied."""
        mode = self.y_scale_var.get()
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        use_log = bool(pos) and mode != 'linear'
        if use_log and mode == 'auto':
            span = math.log10(max(pos)) - math.log10(min(pos))
            use_log = span >= 1.0  # at least one full decade of data
        if not use_log:
            ax.set_yscale('linear')
            ax.relim()
            ax.set_autoscaley_on(True)  # set_ylim in log mode disables it
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
            if self._band_dirty:
                # ADV-5: redraw the tolerance band for the current setpoint
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
            need_draw = False
            if self._temp_plot_dirty:
                self._temp_plot_dirty = False
                self._decimate_display_series()
                self.line_temp.set_data(self.plot_t, self.plot_temp)
                self.line_target.set_data(self.plot_t, self.plot_target)
                self.scat_meas.set_data(self.meas_t, self.meas_temp)
                self.line_heater.set_data(self.plot_t, self.plot_heater)
                for ax in (self.ax_temp, self.ax_heater):
                    ax.relim(); ax.autoscale_view()
                need_draw = True
            if self._freq_plot_dirty:
                self._freq_plot_dirty = False
                self.line_cp.set_data(self.scan_f, self.scan_cp)
                self.line_g.set_data(self.scan_f, self.scan_g)
                for ax, key, data in ((self.ax_cp, "cp", self.scan_cp),
                                      (self.ax_g, "g", self.scan_g)):
                    ax.relim(); ax.autoscale_view(scalex=True, scaley=False)
                    self._apply_y_scale(ax, data, key)
                # UI-4: measurement-temperature label on the spectrum
                if self._scan_info is None:
                    self.ax_cp.set_title("")
                else:
                    state, t_set = self._scan_info
                    if state == "measuring":
                        self.ax_cp.set_title(
                            f"Measuring at {t_set:.2f} K …", fontsize=10)
                    else:
                        self.ax_cp.set_title(
                            f"Last scan: {t_set:.2f} K "
                            f"(held until next scan)", fontsize=10)
                need_draw = True
            if need_draw:
                self.canvas_plots.draw_idle()
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
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=r, column=c, sticky="w", padx=5, pady=2)
        e = ttk.Entry(parent, font=self.FONT_BASE, width=12)
        e.grid(row=r, column=c+1, columnspan=span, sticky="ew", padx=5, pady=2)
        e.insert(0, default); self.lcr_entries[key] = e
        return lbl

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

    def _beep(self, times=1):
        """FRZ-3: MUST only be called from the main (Tk) thread.
        winsound runs in a helper thread (it blocks); root.bell() is
        called directly on the main thread — never from a worker.
        HARD-4: multi-beep (Passive v1.4 pattern) — used instead of
        modal dialogs on unattended-run events; a messagebox nobody is
        there to click must never linger over an overnight run."""
        if HAS_WINSOUND and platform.system() == "Windows":
            def _do_beep():
                for _ in range(max(1, times)):
                    winsound.Beep(1000, 500)
                    time.sleep(0.2)
            threading.Thread(target=_do_beep, daemon=True).start()
        else:
            try:
                self.root.bell()
            except Exception:
                pass

    def _set_keep_awake(self, enable):
        """HARD-1: stop Windows from sleeping mid-run (display may still
        sleep). Best-effort no-op on other platforms."""
        try:
            flags = self.ES_CONTINUOUS | (
                self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
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
            r = self.heater_range_var.get().split()[0]
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
        except ValueError:
            messagebox.showerror("Invalid Input", "Unlocked params must be numeric.")
            return
        try:
            caps, default_cap = parse_ramp_table(self.ramp_table_var.get())
            updates["ramp_caps"] = caps
            updates["ramp_default_cap"] = default_cap
        except ValueError as e:
            messagebox.showerror("Ramp Table Error", str(e))
            return
        self.cmd_queue.put(("params", updates))
        shown = {k: v for k, v in updates.items() if k != "ramp_caps"}
        self.log(f"Queued live params: {shown}")

    def _toggle_pause(self):
        """ADV-3: pause holds the current setpoint; window/timeout suspend."""
        if not self.is_running:
            return
        if self.pause_button["text"] == "Pause":
            self.cmd_queue.put(("pause",))
            self.pause_button.config(text="Resume")
            self.log("PAUSE requested (holds current setpoint; stability "
                     "window & timeout suspended).")
        else:
            self.cmd_queue.put(("resume",))
            self.pause_button.config(text="Pause")
            self.log("RESUME requested.")

    def _skip_step(self):
        """ADV-3: skip the current stabilization (no sweep at that point)
        or abort the remainder of a running sweep."""
        if not self.is_running:
            return
        self.cmd_queue.put(("skip",))
        self.log("SKIP requested.")

    # ------------------------------------------------------------
    # VISA scan / file browse
    # ------------------------------------------------------------
    def _scan_for_visa(self):
        """Alpha-safe identity scan. *IDN? goes out with EOI-only framing
        (the Alpha uses no newline terminator; the Lakeshore 350 also
        accepts EOI). The device reporting Novocontrol/ALPHA is picked for
        the Alpha combobox (falling back to GPIB address DEFAULT_ALPHA_PAD,
        the Alpha's address in WinALPHA). A board left not-Controller-In-
        Charge by WinALPHA/WinDETA gets ONE Interface Clear, then the
        query is retried once."""
        if not PYVISA_AVAILABLE:
            self.log("PyVISA not available."); return
        rm = self.ls_backend.rm or self.lcr_backend.rm
        if rm is None:
            self.log("VISA RM unavailable."); return

        def _identify(res):
            with rm.open_resource(res) as dev:
                dev.timeout = 2000
                dev.read_termination = ""
                dev.write_termination = ""
                dev.send_end = True
                return dev.query("*IDN?").strip(" \t\r\n\x00\x10")

        self.log("Scanning VISA instruments (querying *IDN?)…")
        found = []
        ls_pick = lcr_pick = alpha_default = None
        cleared_boards = set()
        try:
            for res in rm.list_resources():
                idn = "Unknown"
                try:
                    idn = _identify(res)
                except Exception as exc:
                    board = gpib_board_of(res)
                    if (visa_error_code(exc) == VI_ERROR_NCIC
                            and board is not None
                            and board not in cleared_boards):
                        cleared_boards.add(board)
                        self.log(f"  GPIB{board} is not Controller-In-Charge "
                                 f"(WinALPHA/WinDETA may have taken the bus). "
                                 f"Sending one Interface Clear…")
                        try:
                            self.lcr_backend.send_interface_clear(board)
                            idn = _identify(res)
                        except Exception:
                            pass
                label = f"{res}  ->  {idn}"
                found.append(label)
                upper = idn.upper()
                is_alpha = "NOVOCONTROL" in upper or "ALPHA" in upper
                if is_alpha and lcr_pick is None:
                    lcr_pick = label
                elif "350" in idn and ls_pick is None:
                    ls_pick = label
                if (alpha_default is None and res.upper().startswith("GPIB")
                        and f"::{DEFAULT_ALPHA_PAD}::" in res):
                    alpha_default = label
                self.log(f"  {label}")
            self.ls_cb["values"] = found
            self.lcr_cb["values"] = found
            if ls_pick: self.ls_cb.set(ls_pick); self.log(f"Lakeshore auto-selected: {ls_pick}")
            if lcr_pick: self.lcr_cb.set(lcr_pick); self.log(f"Alpha-AN auto-selected: {lcr_pick}")
            elif alpha_default:
                self.lcr_cb.set(alpha_default)
                self.log(f"WARNING: no instrument identified itself as an "
                         f"Alpha; selected address {DEFAULT_ALPHA_PAD}. Start "
                         f"will confirm it is an Alpha with a ZG4.")
            if not ls_pick and found: self.ls_cb.set(found[0])
            if not lcr_pick and not alpha_default and found: self.lcr_cb.set(found[0])
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
        t = self.worker_thread
        if t is not None and t.is_alive():
            self.log("Previous run is still finishing; wait for it before "
                     "starting again.")
            return
        setpoints = list(self.listbox.get(0, tk.END))
        if not setpoints:
            messagebox.showwarning("Empty Sequence", "Add at least one setpoint."); return
        if not self.save_dir:
            messagebox.showwarning("No Save Dir", "Choose a save directory first."); return
        try:
            self.params = self._validate_ls_params()
            self.lcr_params = self._validate_lcr_params()
            self.setpoint_floats = [float(x) for x in setpoints]
            # ADV-4: every setpoint must respect the soft limit
            too_hot = [t for t in self.setpoint_floats
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
                too_close = [t for t in self.setpoint_floats
                             if t + band > self.params["max_temp"]]
                if too_close:
                    raise ValueError(
                        f"'From above' pre-target (T + {band:g} K band) "
                        f"exceeds Max Temp ({self.params['max_temp']:g} K) "
                        f"for setpoints: {too_close}. Lower the setpoints/"
                        f"band or raise Max Temp.")
            # Heads-up (not an error): approach rate will be auto-capped
            # by the low-T table at the coldest setpoint of the run.
            coldest_cap = ramp_cap_for(min(self.setpoint_floats),
                                       self.params["ramp_caps"],
                                       self.params["ramp_default_cap"])
            if self.params["app_rate"] > coldest_cap:
                self.log(f"NOTE: Approach Rate "
                         f"{self.params['app_rate']:g} K/min exceeds the "
                         f"low-T cap ({coldest_cap:g} K/min at "
                         f"{min(self.setpoint_floats):g} K) — it will be "
                         f"auto-capped during the run.")
        except Exception as e:
            messagebox.showerror("Config Error", str(e)); return

        self.set_ui_state(running=True)
        self.is_running = True
        self._worker_phase = None
        self._paused = False
        self._skip_requested = False
        self._current_rate = 0.0
        self._current_target = None
        self.pause_button.config(text="Pause")

        # Clear plot data (CRIT-2: include plot_heater)
        for L in (self.plot_t, self.plot_temp, self.plot_target, self.plot_heater,
                  self.meas_t, self.meas_temp,
                  self.scan_f, self.scan_cp, self.scan_g):
            L.clear()
        self.line_temp.set_data([], []); self.line_target.set_data([], [])
        self.scat_meas.set_data([], []); self.line_heater.set_data([], [])
        self.line_cp.set_data([], []); self.line_g.set_data([], [])
        if self.band_patch is not None:            # ADV-5: clear old band
            try:
                self.band_patch.remove()
            except Exception:
                pass
            self.band_patch = None
        self._band_params = None
        self._band_dirty = False
        self._scan_info = None            # UI-4: drop the held spectrum label
        self.ax_cp.set_title("")
        self.canvas_plots.draw_idle()
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
        self._set_keep_awake(True)   # HARD-1: overnight run, no system sleep
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
            # ADV-1/2/4: adaptive ramp + approach mode + soft limit
            "max_temp": float(self.entries["max_temp"]["entry"].get()),
            "step_factor": float(self.entries["step_factor"]["entry"].get()),
            "first_factor": float(self.entries["first_factor"]["entry"].get()),
            "min_rate": float(self.entries["min_rate"]["entry"].get()),
            "max_rate": float(self.entries["max_rate"]["entry"].get()),
            "adaptive": self.ramp_mode_var.get() == "adaptive",
            "auto_pid": self.var_auto_pid.get(),
            "approach_side": self.APPROACH_MODES.get(self.approach_var.get(), None),
        }
        p["ramp_caps"], p["ramp_default_cap"] = \
            parse_ramp_table(self.ramp_table_var.get())
        if not p["ls_visa"]: raise ValueError("Select Lakeshore VISA.")
        if p["rate"] <= 0: raise ValueError("Fixed rate must be positive.")
        if p["tol"] <= 0: raise ValueError("Tolerance must be positive.")
        if p["soak"] <= 0: raise ValueError("Stabilization window must be positive.")
        if p["delay"] <= 0: raise ValueError("Poll delay must be positive.")
        if p["app_band"] <= 0: raise ValueError("Approach band must be positive.")
        if p["app_rate"] <= 0: raise ValueError("Approach rate must be positive.")
        if p["drift"] <= 0: raise ValueError("Drift limit must be positive.")
        if p["stab_timeout"] < 0: raise ValueError("Timeout must be >= 0 (0 disables).")
        if p["app_band"] <= p["tol"]:
            raise ValueError("Approach band should be larger than tolerance.")
        if p["step_factor"] <= 0:
            raise ValueError("Step factor must be positive.")
        if not (0 < p["first_factor"] <= 1):
            raise ValueError("First-step factor must be in (0, 1].")
        if p["min_rate"] <= 0 or p["max_rate"] < p["min_rate"]:
            raise ValueError("Need 0 < Min Rate <= Max Rate.")
        if p["heater_range"] == "0":
            raise ValueError("Heater range 0 (off) cannot start a sequence.")
        if not (0 < p["max_temp"] <= Lakeshore_Backend.HARD_TEMP_LIMIT_K):
            raise ValueError(
                f"Max Temp must be in (0, "
                f"{Lakeshore_Backend.HARD_TEMP_LIMIT_K:g}] K.")
        return p

    def _validate_lcr_params(self):
        freq_label, freqs = self._selected_frequencies()
        lcr_visa = self.lcr_cb.get()
        if "  ->  " in lcr_visa:
            lcr_visa = lcr_visa.split("  ->  ")[0].strip()
        p = {
            "sample_name": self.lcr_entries["sample_name"].get().strip() or "Sample",
            "acv": float(self.lcr_entries["acv"].get()),
            "mtm": float(self.lcr_entries["mtm"].get()),
            "delay": float(self.lcr_entries["delay"].get()),
            "wire_mode": self.wire_cb.get(),
            "lcr_visa": lcr_visa,
            "geometry_mode": self.geom_mode_var.get(),
            "thickness_mm": float(self.lcr_entries["thickness_mm"].get()),
            "save_pica": self.fmt_pica_var.get(),
            "save_windeta": self.fmt_windeta_var.get(),
            "temp_controller": "Lakeshore 350",
        }
        geom_value = float(self.lcr_entries["geom_value"].get())
        if p["geometry_mode"] == "area":
            p["area_mm2"] = geom_value
        else:
            p["diameter_mm"] = geom_value
        if not p["lcr_visa"]: raise ValueError("Select Alpha-AN VISA.")
        if p["delay"] < 0: raise ValueError("Freq Delay cannot be negative.")
        validate_output_settings(p)
        self.c0_farads = c0_from_params(p)
        # Every Alpha setting is checked here, at Start, before the worker
        # opens either instrument (ZG4 AC ceiling over the whole sweep).
        validate_alpha_parameters(
            [float(f) for f in freqs], p["acv"],
            p["mtm"], p["wire_mode"])
        p["freq_preset"] = freq_label
        self.sweep_frequencies = freqs
        self.log(f"Frequency list: {freq_label}")
        return p

    def set_ui_state(self, running: bool):
        st = "disabled" if running else "normal"
        self.start_button.config(state=st)
        self.stop_button.config(state="normal" if running else "disabled")
        # ADV-3: run-control buttons
        self.pause_button.config(state="normal" if running else "disabled")
        self.skip_button.config(state="normal" if running else "disabled")
        if not running:
            self.pause_button.config(text="Pause")
        self.approach_cb.config(state="disabled" if running else "readonly")
        # Ramp mode is only read at Start — lock the radios during a run
        self.rb_adaptive.config(state=st)
        self.rb_fixed.config(state=st)
        self.ramp_table_entry.config(state="normal")  # live-updatable
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
        self._on_ramp_mode_changed()  # re-grey the inactive rate source
        for e in (self.entry_start, self.entry_end, self.entry_step, self.entry_manual):
            e.config(state=st)
        self.ls_cb.config(state="readonly" if not running else "disabled")
        self.lcr_cb.config(state="readonly" if not running else "disabled")
        # Frequency list is read once at Start: lock it during a run
        self.freq_cb.config(state="disabled" if running else "readonly")
        if running:
            self.custom_freq_text.config(state="disabled")
            self.list_maker_button.config(state="disabled")
        else:
            self._on_freq_list_change()
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
                    # FRZ-3: beep executes on the Tk thread. HARD-4: the
                    # worker may ask for several (alarm-grade events).
                    self._beep(times=m.get("times", 1))
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
                elif t == "band":
                    # ADV-5: tolerance band for the current setpoint
                    self._band_params = (m["target"], m["tol"])
                    self._band_dirty = True
                    self._temp_plot_dirty = True
                elif t == "pause_state":
                    self.pause_button.config(
                        text="Resume" if m["paused"] else "Pause")
                elif t == "scan_reset":
                    self.scan_f.clear(); self.scan_cp.clear(); self.scan_g.clear()
                    self._decade_ylims.clear()  # new spectrum re-snaps decades
                    # UI-4: label the incoming spectrum with its temperature
                    target = m.get("target")
                    self._scan_info = None if target is None \
                        else ("measuring", target)
                    self._freq_plot_dirty = True
                elif t == "scan_done":
                    # UI-4: sweep finished — hold the spectrum, mark it done
                    self._scan_info = ("done", m["target"])
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
                    # HARD-4: unattended policy — no modal on completion.
                    # The worker already set the COMPLETE banner; log +
                    # triple beep announce it audibly instead.
                    self.log("SEQUENCE COMPLETE — all setpoints measured.")
                    self._beep(times=3)
                elif t == "worker_done":
                    self.set_ui_state(running=False)
                    self._set_keep_awake(False)  # HARD-1: sleep allowed again
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
    # Impedance math (carried over verbatim from E4980A program; fed
    # Zs' and Zs'' from the Alpha and its actual frequency)
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
    # HARD-2: retry-forever comm recovery (worker thread only;
    # embedded Passive v1.3/1.4 pattern — PICA programs do not
    # import from each other)
    # ============================================================
    def _comm_recover(self, context, err):
        """Close and re-open BOTH sessions with escalating backoff
        (5→10→30→60 s) until they respond or the user stops. The
        Lakeshore keeps executing its last ramp while the link is
        down — its reconnect is session-only and never re-sends
        setpoints. Returns False only if Stop was requested."""
        attempt = 1
        self._put_gui_msg("log",
            text=f"⚠️ COMM ERROR during {context}: {err} — entering "
                 "retry-forever reconnect (Stop stays responsive).")
        self._put_gui_msg("beep", times=2)
        backoffs = (5, 10, 30, 60)
        while self.is_running:
            delay_s = backoffs[min(attempt - 1, len(backoffs) - 1)]
            self._put_gui_msg("status",
                text=f"COMM LOST — reconnect #{attempt} in {delay_s} s",
                color=self.CLR_ACCENT_RED)
            deadline = time.time() + delay_s
            while time.time() < deadline:
                if self._process_cmd_queue() or not self.is_running:
                    return False
                time.sleep(0.5)
            try:
                idn = self.ls_backend.reconnect()
            except Exception as e:
                self._put_gui_msg("log",
                    text=f"Reconnect attempt #{attempt} failed: {e}")
                attempt += 1
                continue
            try:
                # Alpha: release + connect + initialize_instrument.
                self.lcr_backend.reconnect()
            except RuntimeError as refusal:
                # B2: the Alpha ANSWERED with a refusal (non-OK ack, 'CR').
                # Not a link fault, so retrying cannot cure it: end the run.
                self._put_gui_msg("log",
                    text=f"Alpha-AN refused a command during reconnect: "
                         f"{refusal}. Ending the run.")
                raise
            except Exception as e:
                # Bus / VISA faults (incl. TimeoutError, OSError): retry.
                self._put_gui_msg("log",
                    text=f"Reconnect attempt #{attempt} failed: {e}")
                attempt += 1
                continue
            # B3: Stop pressed while the reconnect was in progress - never
            # leave the freshly opened Alpha session live.
            if self._process_cmd_queue() or not self.is_running:
                try:
                    self.lcr_backend.close_instrument()
                except Exception as close_err:
                    print(f"Alpha-AN shutdown warning: {close_err}")
                return False
            self._put_gui_msg("log",
                text=f"✅ Reconnected after {attempt} attempt(s) "
                     f"(Lakeshore: {idn}). Resuming sequence.")
            return True
        return False

    def _get_status_hardened(self):
        """HARD-2: get_status that survives a dead link. REL-1's quick
        retries run first (inside get_status); on persistent failure the
        retry-forever reconnect takes over. Raises RuntimeError only
        when the user stops mid-recovery, so callers unwind cleanly."""
        while True:
            try:
                return self.ls_backend.get_status()
            except Exception as e:
                if not self._comm_recover("Lakeshore status read", e):
                    raise RuntimeError("Stopped during comm recovery.")

    # ============================================================
    # WORKER THREAD (owns both instruments)
    # ============================================================
    def _hardware_worker_loop(self):
        self.start_time = time.time()
        # CRIT-2: no worker-side `_heater_series` — heater plot values
        # travel through the gui_queue and are accumulated in plot_heater.
        self.data_file = None
        self.csv_writer = None
        self.summary_file = None
        self.summary_writer = None
        try:
            # --- Connect Lakeshore ---
            self._put_gui_msg("log", text="Connecting to Lakeshore 350…")
            idn = self.ls_backend.connect(self.params["ls_visa"])
            self._put_gui_msg("log", text=f"Lakeshore: {idn}")

            # --- Connect & configure Alpha-AN ---
            self._put_gui_msg("log", text="Connecting to Novocontrol Alpha-AN…")
            idn_a, _code = self.lcr_backend.connect(self.lcr_params["lcr_visa"])
            self._put_gui_msg("log", text=f"Alpha-AN: {idn_a}")
            self.lcr_backend.initialize_instrument(self.lcr_params)
            self._put_gui_msg("log", text="Alpha-AN initialized (ZG4).")

            # --- Open combined temperature log (§3c) ---
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tlog_path = os.path.join(
                self.save_dir,
                f"{self.lcr_params['sample_name']}_{stamp}_TempLog.csv",
            )
            self.data_file = open(tlog_path, "w", newline="",
                                  encoding="utf-8")
            self.csv_writer = csv.writer(self.data_file)
            self.csv_writer.writerow(
                ["Timestamp", "Elapsed_s", "Target_K", "Temperature_K",
                 "Heater_pct", "Measuring", "Ramp_rate_K_min", "Phase"]
            )
            self.data_file.flush()
            os.fsync(self.data_file.fileno())   # HARD-3
            self._put_gui_msg("log", text=f"Temperature log: {tlog_path}")

            # ADV-6: per-setpoint stabilization summary
            sum_path = os.path.join(
                self.save_dir,
                f"{self.lcr_params['sample_name']}_{stamp}_StabSummary.csv",
            )
            self.summary_file = open(sum_path, "w", newline="",
                                     encoding="utf-8")
            self.summary_writer = csv.writer(self.summary_file)
            self.summary_writer.writerow(
                ["Setpoint_K", "Ramp_rate_K_min", "Approach_mode",
                 "Start_time", "End_time", "Stabilization_s", "Outcome"])
            self.summary_file.flush()
            os.fsync(self.summary_file.fileno())   # HARD-3
            self._put_gui_msg("log", text=f"Stabilization summary: {sum_path}")

            total_pts = len(self.setpoint_floats) * len(self.sweep_frequencies)
            done_pts = 0

            for i, target in enumerate(self.setpoint_floats):
                if not self.is_running:
                    break
                self._put_gui_msg("log",
                    text=f"--- Step {i+1}/{len(self.setpoint_floats)}: target {target} K ---")
                self._put_gui_msg("band", target=target,
                                  tol=self.params["tol"])

                # ADV-1: adaptive (or fixed) ramp rate for this step
                temp_now, _, _ = self._get_status_hardened()  # HARD-2
                if self.params["adaptive"]:
                    rate, reason = compute_ramp_rate(
                        temp_now, target, self.params, is_first_step=(i == 0))
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

                # §3b: dynamic PID per setpoint
                self._apply_dynamic_pid(target)

                # STB-1/2/3: two-stage approach + rolling-window stability
                t_start = datetime.now()
                result, elapsed = self._ramp_and_stabilize(target, rate)
                self._write_summary_row(target, rate, t_start, elapsed, result)
                if not self.is_running or result == "stopped":
                    break
                if result == "skipped":
                    self._put_gui_msg("log",
                        text=f"⏭ Setpoint {target} K skipped — no sweep taken.")
                    continue
                if result == "timeout":
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ STABILIZATION TIMEOUT at {target} K after "
                             f"{elapsed/60.0:.1f} min — "
                             f"proceeding with sweep anyway (unattended-run policy). "
                             f"Check this data point!")

                self._put_gui_msg("log",
                    text=f"Stable. Starting frequency sweep at {target} K.")
                self._put_gui_msg("status",
                    text=f"MEASURING AT {target} K", color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("beep")   # FRZ-3: beep on Tk thread
                # UI-4: clear the held previous spectrum only NOW, when
                # new points are imminent (was at the top of the loop).
                self._put_gui_msg("scan_reset", target=target)
                done_pts = self._run_frequency_sweep(target, done_pts, total_pts)
                if not self.is_running:
                    break
                self._put_gui_msg("scan_done", target=target)  # UI-4
                self._put_gui_msg("log",
                    text=f"Sweep done at {target} K. Proceeding.")

            if self.is_running:
                self._put_gui_msg("log", text="Sequence complete.")
                self._put_gui_msg("status", text="COMPLETE", color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("sequence_complete")

        except SoftLimitAbort as e:
            # ADV-4: graceful safety abort (ramp/heater stopped in finally)
            self._put_gui_msg("log",
                text=f"SAFETY ABORT: {e} Ramp stopped, heater off.")
            self._put_gui_msg("status", text="SAFETY ABORT (MAX TEMP)",
                              color=self.CLR_ACCENT_RED)
            self._put_gui_msg("beep", times=5)   # HARD-4: alarm, no modal
        except Exception as e:
            self._put_gui_msg("log",
                text=f"CRITICAL: {e}\n{traceback.format_exc()}")
            self._put_gui_msg("status", text="ERROR", color=self.CLR_ACCENT_RED)
            self._put_gui_msg("beep", times=3)   # HARD-4: alarm, no modal
        finally:
            # §3f: crash-safe shutdown
            try:
                self.lcr_backend.close_instrument()
            except Exception as e:
                print(f"Alpha-AN shutdown warning: {e}")
            try:
                if self.ls_backend.shutdown() is False:
                    # Session lost in a comm recovery: heater-off was NOT
                    # sent. Log + status + beep only (unattended run).
                    self._put_gui_msg("log",
                        text="WARNING: the Lakeshore session was lost, so "
                             "RAMP off / heater RANGE 0 could NOT be sent. "
                             "The controller may still be ramping - turn "
                             "its heater off at the front panel.")
                    self._put_gui_msg("status",
                        text="HEATER NOT TURNED OFF - check the Lakeshore",
                        color=self.CLR_ACCENT_RED)
                    self._put_gui_msg("beep", times=5)
            except Exception as e:
                print(f"Lakeshore shutdown warning: {e}")
            self._close_data_file()
            self.is_running = False
            self._worker_phase = None
            self._paused = False
            self._skip_requested = False
            self._current_target = None
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

    def _choose_pre_target(self, temp, target):
        """Pre-target selection (STB-1 + ADV-2 one-side approach).
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
            # ramp always enters the target from that side.
            return target + sign * p["app_band"]
        if abs(delta) > p["app_band"]:
            return target - math.copysign(p["app_band"], delta)
        return None

    def _write_summary_row(self, target, rate, t_start, elapsed, outcome):
        """ADV-6: one row per setpoint in the stabilization summary CSV."""
        try:
            side = self.params["approach_side"] or "two-stage"
            self.summary_writer.writerow(
                [f"{target:.4f}", f"{rate:.3f}", side,
                 t_start.strftime("%Y-%m-%d %H:%M:%S"),
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 f"{elapsed:.1f}", outcome])
            self.summary_file.flush()
            os.fsync(self.summary_file.fileno())   # HARD-3
        except Exception as e:
            self._put_gui_msg("log", text=f"WARN: summary write failed: {e}")

    def _ramp_and_stabilize(self, target, ramp_rate):
        """Two-stage approach (STB-1) + rolling-window stability (STB-2)
        + optional timeout (STB-3) + pause/skip (ADV-3) + one-side
        approach (ADV-2).

        Stage 1 (PRE_RAMP): ramp at `ramp_rate` (adaptive per step) to a
        pre-target that stops `app_band` K short of the real target — the
        setpoint never slews into the target at full speed, which is what
        caused the big overshoots below 100 K.
        Stage 2 (FINAL_APPROACH): slow `app_rate` ramp from the pre-target
        to the real target, then wait until the rolling window is stable.

        Returns (outcome, elapsed_s); outcome in
        "stable" | "timeout" | "skipped" | "stopped".
        Raises on kill switch / soft limit / persistent VISA failure.
        """
        p = self.params
        self._current_rate = ramp_rate
        self._current_target = target
        temp, _, _ = self._log_temperature_point(target, measuring_flag=0)
        pre_target = self._choose_pre_target(temp, target)
        # Low-T safety envelope applies to the approach rate too
        app_rate, app_msg = capped_rate(p["app_rate"], temp, target, p,
                                        "Approach")
        if app_msg:
            self._put_gui_msg("log", text=app_msg)

        if pre_target is not None:
            self._worker_phase = "PRE_RAMP"
            self.ls_backend.configure_ramp(pre_target, ramp_rate,
                                           p["heater_range"])
            self._put_gui_msg("log",
                text=f"Stage 1: ramp ({ramp_rate:g} K/min) to pre-target "
                     f"{pre_target:.2f} K ({p['app_band']} K short of {target} K).")
            self._put_gui_msg("status",
                text=f"RAMPING TO {target} K (pre: {pre_target:.1f} K)",
                color=self.CLR_ACCENT_RED)
        else:
            self._worker_phase = "FINAL_APPROACH"
            self._current_rate = app_rate
            self.ls_backend.configure_ramp(target, app_rate,
                                           p["heater_range"])
            self._put_gui_msg("log",
                text=f"Already within approach band — slow approach "
                     f"({app_rate:g} K/min) to {target} K.")
            self._put_gui_msg("status",
                text=f"APPROACHING {target} K", color=self.CLR_STABLE_WAIT)

        # Direction of travel toward the pre-target (may differ from the
        # overall direction when approach_side forces an overshoot).
        pre_heating = (pre_target is not None) and (pre_target > temp)

        window = deque()          # (time, temp) rolling stability window
        step_start = time.time()
        paused_s = 0.0            # pause time excluded from the timeout
        last_status = 0.0

        try:
            while self.is_running:
                if self._process_cmd_queue():
                    return "stopped", time.time() - step_start
                if self._skip_requested:
                    self._skip_requested = False
                    return "skipped", time.time() - step_start

                temp, _, _ = self._log_temperature_point(target, measuring_flag=0)
                now = time.time()

                if self._paused:
                    # ADV-3: hold at the current setpoint; keep logging but
                    # suspend the stability window and timeout clock.
                    paused_s += p["delay"]
                    window.clear()
                    self._put_gui_msg("status",
                        text=f"PAUSED (holding, target {target} K)",
                        color=self.CLR_ACCENT_GOLD)
                    time.sleep(p["delay"])
                    continue

                if self._worker_phase == "PRE_RAMP":
                    reached = (temp >= pre_target - p["tol"]) if pre_heating \
                        else (temp <= pre_target + p["tol"])
                    if reached:
                        # STB-1 stage 2: hand over to the slow final approach.
                        # Re-check the cap at the handover temperature.
                        app_rate, app_msg = capped_rate(
                            p["app_rate"], temp, target, p, "Approach")
                        if app_msg:
                            self._put_gui_msg("log", text=app_msg)
                        self._worker_phase = "FINAL_APPROACH"
                        self._current_rate = app_rate
                        self.ls_backend.set_ramp_rate(app_rate)
                        time.sleep(0.1)
                        self.ls_backend.set_setpoint(target)
                        window.clear()
                        self._put_gui_msg("log",
                            text=f"Pre-target reached ({temp:.3f} K). Stage 2: "
                                 f"slow approach ({app_rate:g} K/min) to {target} K.")
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
                        return "stable", time.time() - step_start
                    # Live status (throttled to every ~3 s to limit messages)
                    if now - last_status > 3.0:
                        last_status = now
                        if max_dev is not None:
                            self._put_gui_msg("status",
                                text=(f"STABILIZING {target} K | "
                                      f"Δmax={max_dev:.3f} K | "
                                      f"drift={drift:+.3f} K/min"),
                                color=self.CLR_STABLE_WAIT)

                # STB-3: overnight-safety timeout (pause time excluded)
                if p["stab_timeout"] > 0 and \
                        (now - step_start - paused_s) > p["stab_timeout"] * 60.0:
                    return "timeout", time.time() - step_start

                time.sleep(p["delay"])
        finally:
            self._worker_phase = None
        return "stopped", time.time() - step_start

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
                    shown = {k: v for k, v in updates.items()
                             if k != "ramp_caps"}
                    self._put_gui_msg("log", text=f"Params applied: {shown}")
                    if "rate" in updates and self.params.get("adaptive"):
                        # ADV-1: adaptive mode computes the rate per step;
                        # a manual rate only applies with Adaptive unticked.
                        self._put_gui_msg("log",
                            text="Adaptive ramp active — manual rate stored "
                                 "but rates stay computed per setpoint.")
                    elif "rate" in updates:
                        # REL-2: never override the slow final approach —
                        # the new rate takes effect from the next stage/step.
                        if self._worker_phase == "FINAL_APPROACH":
                            self._put_gui_msg("log",
                                text="Ramp rate stored; slow final approach "
                                     "active — applies from next ramp stage.")
                        else:
                            try:
                                temp_now, _, _ = self.ls_backend.get_status()
                                tgt = self._current_target
                                new_rate, cap_msg = capped_rate(
                                    updates["rate"], temp_now,
                                    tgt if tgt is not None else temp_now,
                                    self.params, "Live")
                                if cap_msg:
                                    self._put_gui_msg("log", text=cap_msg)
                                self.ls_backend.set_ramp_rate(new_rate)
                                self._current_rate = new_rate
                                self._put_gui_msg("log",
                                    text=f"Ramp rate -> {new_rate} K/min")
                            except Exception as e:
                                self._put_gui_msg("log",
                                    text=f"Ramp rate update failed: {e}")
        except queue.Empty:
            pass
        return False

    def _apply_dynamic_pid(self, target):
        if not self.params["auto_pid"]:
            return
        p, i, d = self.PID_ULTRA if target < 100.0 else self.PID_MEDIUM
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
        temp, _, htr = self._get_status_hardened()  # HARD-2
        if self.ls_backend.check_overtemp(temp):
            self._put_gui_msg("log",
                text=f"!!! KILL SWITCH: {temp:.2f} K >= 340 K. Heater OFF. Aborting.")
            self._put_gui_msg("status", text="OVERTEMP ABORT", color=self.CLR_ACCENT_RED)
            self.is_running = False
            raise RuntimeError("Hard overtemperature limit reached.")
        if temp >= self.params["max_temp"]:
            # ADV-4: graceful abort well before the hard kill switch
            raise SoftLimitAbort(
                f"Soft Max Temp limit reached: {temp:.2f} K >= "
                f"{self.params['max_temp']:g} K.")
        elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        phase = "PAUSED" if self._paused else (self._worker_phase or "")
        self.csv_writer.writerow(
            [now_str, f"{elapsed:.2f}", f"{target:.4f}", f"{temp:.4f}",
             f"{htr:.2f}", measuring_flag,
             f"{self._current_rate:.3f}", phase]
        )
        self.data_file.flush()
        os.fsync(self.data_file.fileno())   # HARD-3
        # CRIT-2: heater value rides on the queue msg; no worker-side list.
        self._put_gui_msg("temp_point", t=elapsed, temp=temp, target=target,
                          measuring=measuring_flag, heater=htr)
        return temp, _, htr

    def _run_frequency_sweep(self, target_temp, done_pts, total_pts):
        """Runs the Alpha-AN frequency sweep at one stable setpoint.
        Logs temperature (flag=1) interleaved between frequency points (§3d)."""
        # MAJ-3: timestamp prevents silent overwrite on re-run.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(
            self.save_dir,
            f"{self.lcr_params['sample_name']}_{target_temp:.2f}K_"
            f"{stamp}_FreqScan")
        # The suffix names the format; an unticked format gets no file.
        pica_path = (base + "_pica_format.dat"
                     if self.lcr_params["save_pica"] else "")
        windeta_path = (base + "_windeta_format.txt"
                        if self.lcr_params["save_windeta"] else "")
        now = datetime.now()
        if pica_path:
            with open(pica_path, "w", encoding="utf-8") as f:
                for line in dat_metadata_lines(
                        self.lcr_params, self.c0_farads, target_temp,
                        self.sweep_frequencies, now):
                    f.write(line + "\n")
                f.write(self.lcr_backend.DATA_HEADER + "\n")
        # WinDETA exports are cp1252 with CRLF, on any OS.
        if windeta_path:
            with open(windeta_path, "w", encoding="cp1252",
                      newline="\r\n") as f:
                for line in windeta_header_lines(
                        self.lcr_params, target_temp, now):
                    f.write(line + "\n")
        fname = ", ".join(os.path.basename(p)
                          for p in (pica_path, windeta_path) if p)
        self._worker_phase = "SWEEP"
        # HARD-2: while-loop so a frequency point can be RETRIED
        # after a comm recovery instead of abandoning the sweep.
        idx = 0
        while idx < len(self.sweep_frequencies):
            freq = self.sweep_frequencies[idx]
            if not self.is_running:
                break
            # check for stop / pid updates mid-sweep
            if self._process_cmd_queue():
                break
            if not self.is_running:
                break
            # ADV-3: skip aborts the remainder of this sweep
            if self._skip_requested:
                self._skip_requested = False
                self._put_gui_msg("log",
                    text="⏭ Remaining sweep skipped by user.")
                break
            # ADV-3: pause holds between frequency points
            while self._paused and self.is_running:
                self._put_gui_msg("status",
                    text=f"PAUSED (sweep at {target_temp} K)",
                    color=self.CLR_ACCENT_GOLD)
                if self._process_cmd_queue():
                    break
                time.sleep(1.0)
            if not self.is_running:
                break
            if self.lcr_params["delay"] > 0:
                time.sleep(self.lcr_params["delay"])   # optional settle
            try:
                R, X, f_act, status, _ref = self.lcr_backend.measure_point(
                    float(freq))
            except RuntimeError:
                # The Alpha REFUSED a command (non-OK ack) or answered
                # 'CR' (load-short cal missing). Not a link fault: a
                # reconnect (*RST) cannot fix it, so abort the run.
                raise
            except Exception as e:
                # HARD-2: reconnect forever, then retry THIS point
                # (was: log + abandon the rest of the sweep).
                if self._comm_recover(
                        f"Alpha-AN measurement @ {freq_tag(freq)} Hz", e):
                    continue
                break   # stop requested during recovery
            # Status 6 = signal source disconnected (hardware fault):
            # abort the run (instruments are parked in the finally).
            if status in STATUS_FATAL:
                raise RuntimeError(
                    f"Alpha-AN status {status} at {freq_tag(freq)} Hz: "
                    f"{status_message(status)}. Sweep aborted.")
            # A6: a non-valid ZRE? status (2 = Result valid) is not a
            # result: discard it (not written, not plotted), as
            # Frequency_Scan_AlphaAN does. The point still counts as
            # done so progress stays right.
            if status != STATUS_RESULT_VALID:
                self._put_gui_msg("log",
                    text=f"⚠️ Alpha-AN status {status} @ "
                         f"{freq_tag(freq)} Hz "
                         f"({status_message(status)}) — Point discarded.")
                done_pts += 1
                idx += 1
                continue
            # Convert with the frequency the generator really produced.
            vals = self.calculate_impedance_parameters(f_act, R, X)
            row = [f_act] + vals + [target_temp]
            # HARD-3: every point is fsynced, in each ticked format.
            if pica_path:
                append_line_durably(
                    pica_path, "\t".join(f"{v:.6E}" for v in row))
            if windeta_path:
                append_line_durably(
                    windeta_path,
                    "\t".join(f"{v:.5e}" for v in impedance_to_dielectric(
                        R, X, f_act, self.c0_farads)),
                    encoding="cp1252", newline="\r\n")
            # Interleaved temperature log (flag=1) — §3c/§3d
            try:
                self._log_temperature_point(target_temp, measuring_flag=1)
            except SoftLimitAbort:
                raise    # ADV-4: graceful abort must reach the worker
            except RuntimeError:
                # Overtemp kill and "Stopped during comm recovery" have
                # already cleared is_running: just leave the sweep. With
                # is_running still set this is an Alpha refusal re-raised
                # by _comm_recover - the Alpha is half-configured, so the
                # run must end through the worker's error path.
                if self.is_running:
                    raise
                break
            done_pts += 1
            self._put_gui_msg("scan_point", freq=f_act, cp=vals[4], g=vals[2],
                              progress=done_pts)
            idx += 1   # HARD-2: advance only after a completed point
        self._worker_phase = None
        self._put_gui_msg("log", text=f"Sweep saved: {fname}")
        return done_pts

    def _close_data_file(self):
        for attr, label in (("data_file", "Temperature log"),
                            ("summary_file", "Stabilization summary")):
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
                # The worker only sees Stop between points: give it the
                # slowest single point plus margin before closing.
                mtm = self.lcr_params.get('mtm', 0.0)
                worst = max((point_timeout_s(f, mtm)
                             for f in self.sweep_frequencies),
                            default=SRQ_TIMEOUT_MEASURE_S)
                self._close_deadline = time.time() + worst + 15.0
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
# ===============================================================================
# TERMINAL -> PICA CONSOLE
# Backend code reports with print() (e.g. "*RST failed; escalating to RSTH",
# shutdown warnings, tracebacks). Those lines used to reach only the black
# terminal window, so the PICA console could not show them. TerminalTee
# copies every complete stdout/stderr line into a queue that the GUI drains
# into its own console as "[terminal] ..."; the terminal still gets
# everything. The same block is in every Alpha-AN GUI - keep them in step.
# ===============================================================================

TERMINAL_LOG_POLL_MS = 250
TERMINAL_LOG_MAX_LINES_PER_POLL = 200


class TerminalTee:
    """File-like stand-in for sys.stdout / sys.stderr.

    Forwards every write to the original stream (when there is one: under
    pythonw there is none) and queues each complete line for the GUI
    console. Writes from the thread that is draining the queue are not
    queued, so the console can never echo itself."""

    def __init__(self, original, line_queue):
        self.original = original
        self.line_queue = line_queue
        self.muted_thread = None
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text):
        if self.original is not None:
            try:
                self.original.write(text)
            except Exception:
                pass
        if not text or threading.get_ident() == self.muted_thread:
            return len(text or "")
        with self._lock:
            self._buffer += text
            *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            line = line.rstrip()
            if line:
                self.line_queue.put(line)
        return len(text)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        # encoding, isatty, fileno ... come from the real stream.
        return getattr(self.original, name)


def install_terminal_log(gui):
    """Copy terminal output into gui.log() until gui.root is destroyed.

    Called once from the GUI's __init__. The queue is drained on the Tk
    thread with a stored, cancellable after() id; the original streams are
    put back when the root window goes. Returns the restore function."""
    lines = queue.Queue()
    saved = (sys.stdout, sys.stderr)
    tees = (TerminalTee(saved[0], lines), TerminalTee(saved[1], lines))
    sys.stdout, sys.stderr = tees
    state = {'after_id': None}

    def drain():
        state['after_id'] = None
        for tee in tees:
            tee.muted_thread = threading.get_ident()
        try:
            for _ in range(TERMINAL_LOG_MAX_LINES_PER_POLL):
                try:
                    line = lines.get_nowait()
                except queue.Empty:
                    break
                try:
                    gui.log(f"[terminal] {line}")
                except Exception:
                    break  # console not usable; the terminal still has it
        finally:
            for tee in tees:
                tee.muted_thread = None
        try:
            state['after_id'] = gui.root.after(TERMINAL_LOG_POLL_MS, drain)
        except Exception:
            pass  # root already destroyed

    def restore(event=None):
        if event is not None and event.widget is not gui.root:
            return  # <Destroy> also fires for every child widget
        if state['after_id'] is not None:
            try:
                gui.root.after_cancel(state['after_id'])
            except Exception:
                pass
            state['after_id'] = None
        if sys.stdout is tees[0]:
            sys.stdout = saved[0]
        if sys.stderr is tees[1]:
            sys.stderr = saved[1]

    gui.root.bind("<Destroy>", restore, add="+")
    state['after_id'] = gui.root.after(TERMINAL_LOG_POLL_MS, drain)
    return restore


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