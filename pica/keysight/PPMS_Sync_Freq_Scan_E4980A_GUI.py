"""
Module: PPMS_Sync_Freq_Scan_E4980A_GUI.py
Purpose: PASSIVE temperature-synchronized dielectric spectroscopy for
         PPMS-based measurements. Keysight E4980A frequency sweep +
         Lakeshore 350 used STRICTLY READ-ONLY as the probe thermometer.

The PPMS runs its own temperature sequence — this program never talks to
the PPMS and never sends a single control command to the Lakeshore (no
RANGE/RAMP/SETP/PID). The sample sits on a custom probe with large
thermal inertia: it lags the PPMS setpoint and needs extra time to
equilibrate after the PPMS itself has settled. The run is:

  0. INITIAL SLEEP — optional ONE-TIME fixed wait at Start (e.g.
                     3 h 30 min while the PPMS does its first big
                     cooldown). Checkbox + duration; not per step.
  Then, per temperature in the schedule list:
  1. WAIT_STABLE   — active stabilization detection from the probe
                     thermometer alone (rolling window; three selectable
                     criteria, see below).
  2. SCAN          — full 40 Hz – 2 MHz frequency sweep (identical scan
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
  - Flatness + band    : both checks (DEFAULT since v1.3).

Timing intelligence:
  - Live scan-duration estimate from the sweep parameters (aperture,
    per-point delay, VISA overhead, low-frequency period limit);
    replaced by the MEASURED mean per-point time after the first sweep.
  - "Generate PPMS plan" works BEFORE the run, with no
    instruments connected: per setpoint it shows
        suggested PPMS wait = stability window + estimated scan + Margin
    so the PPMS sequence can be written up front.
  - During the run each row is replaced by measured values:
        suggested PPMS wait = probe settle time + scan time + Margin
    (settle is measured from the end of the previous scan — i.e. from
    when the PPMS started moving — to declared stability). Suggestions
    appear live in the "Timing / PPMS Suggestions" tab and in the
    TimingLog CSV, so the next run of the same sequence can be
    tightened.
  - If the sample temperature leaves the stability band DURING a sweep
    (PPMS moved on too early), the sweep still completes but the step
    is flagged (TempDriftDuringScan) in the timing log — flag-only
    policy for unattended runs.
  - The same button is also the clock-time planner: enter the sequence
    start and desired end time (24h or AM/PM) and it back-solves the PPMS
    ramp rate for the N-1 ramps between setpoints —
        rate = sum(|dT|) / (available - initial sleep - N x wait)
    then shows ramping / stabilizing / initial-delay / total and the
    projected finish clock. Clamped to Max rate (default 12 K/min)
    with an achievable-end warning when the window is too tight.

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

============================================================
v1.1 — 2x2 PLOT GRID + PERSISTENT SPECTRUM
============================================================
  UI-3   Temperature, Cp and G now live in ONE figure on a single
         canvas ("Plots" tab): temperature spans the left column,
         Cp/G stack in the right column — no more toggling between
         the two plot tabs. The Timing / PPMS Suggestions table
         keeps its own tab.
  UI-4   The completed Cp/G spectrum PERSISTS on screen, titled with
         its measurement temperature, through the entire wait for
         the next PPMS setpoint. `scan_reset` moved from the top of
         the schedule loop to the moment the sweep starts (so the
         plot is cleared only when new points are imminent); a new
         `scan_done` message marks the held spectrum as
         "Last scan: <T> K".

============================================================
v1.2 — EDITABLE PER-STEP PPMS SEQUENCE BUILDER
============================================================
  UI-5   The "Timing / PPMS Suggestions" tab is now an editable PPMS
         sequence guide (planning aid — this program still never
         commands the PPMS). Each setpoint carries its OWN ramp rate
         and wait/soak; double-click a Rate or Wait cell to edit, or
         use "Set all" at the top of a column to change the whole
         column at once. An "Initial wait" sits at the top and the
         Total + projected finish clock recompute live as you edit.
         Default rates are temperature-aware (slower below 100 K) and
         clamped to the Max rate. During a run the MEASURED PPMS wait
         replaces each row's wait in place. Timer meridiem now defaults
         to AM/PM instead of 24h.

============================================================
v1.3 — UNATTENDED-RUN HARDENING (power cuts / comm errors)
============================================================
  HARD-1 Durable data writes: every row (TempLog CSV, TimingLog CSV
         and the per-scan data file) is now written open/append/close
         with flush + os.fsync, so a sudden power cut cannot lose
         OS-buffered rows. A disk/share hiccup buffers rows in memory
         and retries them on every subsequent write — measured data
         is never silently dropped (pattern from
         Temprature_Scan_Passive_E4980A_GUI.py).
  HARD-2 Comm errors retry forever: a failed thermometer or E4980A
         query no longer kills the run. The worker reconnects with
         escalating backoff (5 → 10 → 30 → 60 s cap) and resumes at
         the exact point it left off — the E4980A re-init restores
         the full configuration (RX mode, aperture, ALC, corrections,
         bias re-ramp) even after an instrument power-cycle. Stop
         stays responsive throughout. LCR VISA timeout lowered
         60 s → 15 s so a hung bus is detected quickly.
  HARD-3 Windows keep-awake (SetThreadExecutionState) during a run so
         the PC cannot sleep mid-sequence; released when idle.
  HARD-4 Defaults tuned for the PPMS probe: criterion = Flatness +
         band, Tolerance ±0.5 K, stabilization timeout 90 min
         (timeout proceeds with the sweep and flags the step).

============================================================
v1.4 — UNATTENDED POLICY + VALIDATED .seq EXPORT
(backported from PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py)
============================================================
  UNAT-1 No modal dialogs during/after a run: the sequence-complete
         messagebox is gone (it blocked the GUI queue pump — logs,
         plots, worker_done — until someone clicked OK, and runs
         execute overnight/holidays with nobody in the lab).
         Completion = console log + banner + beep only.
  SEQ-1  "Export .seq…" on the Timing / PPMS Suggestions tab renders
         the editable per-step plan (each row's rate and wait) into a
         real MultiVu sequence:
             TMP TEMP <K> <K/min> <mode> + WAI WAITFOR <wait> 1 0 0 0 0
         with a TMP approach toggle (No overshoot (1) default, as the
         reference Dielectric_Fscan.seq; Fast settle (0) selectable).
         The first setpoint (no ramp row) uses the temperature-aware
         default rate.
  SEQ-2  validate_ppms_seq(): every exported line is checked against
         the exact MultiVu grammar with PPMS value ranges (T <= 400 K,
         rate <= 20 K/min) BEFORE the file can be saved — a faulty
         sequence ruins an unattended run.
  SAFE-1 One-time loud console warning + beep if the sample reads
         above 340 K (this program is read-only and cannot act).
  SMART-1 Cooldown-end detection for the initial sleep (checkbox, ON
         by default per user decision): the sleep ends as soon as the
         probe dips below "Base arm" (default 30 K) and then rises
         2 K off its observed minimum, held for 3 min (median-of-5
         filtered) — i.e. the PPMS has finished its first cooldown
         and is moving to the first setpoint. The timed sleep stays
         as a fallback ceiling. Same "definitely sure" detector as
         the Master module.
  RATE-1 Sequence-plan default ramp rate is now a flat 1 K/min for
         all setpoints, matching the reference Dielectric_Tscan.seq /
         Dielectric_Fscan.seq (the PPMS owns the ramp; the old
         temperature-tiered 0.5/1/2 K/min defaults were for the
         LN2-dewar Lakeshore rig). Still clamped to Max rate and
         per-cell editable.
  FIX-1  The left "Timing & PPMS Suggestion" planner and the right
         "Timing / PPMS Suggestions" table now always agree:
         'Generate PPMS plan' RESETS the table to the computed plan
         (stale cell edits no longer survive it), writes the solved
         ramp rate into every ramp row, stores waits unrounded (was
         a 0.01-min rounding drift), and mirrors a DISABLED initial
         sleep as a zero initial wait. Same rates + same waits +
         same initial wait = identical Total and projected finish
         on both sides.

============================================================
v1.5 — DOUBLE-START RACE FIX + ASCII-SAFE .seq
============================================================
  HARD-5 start_sequence() now has a re-entry guard, and
         sequence_complete no longer re-enables the UI — only
         worker_done does. Previously the Start button came back
         while the worker was still in its finally block closing
         instruments (bias ramp-down takes seconds); a click in
         that window launched a SECOND worker onto the same VISA
         sessions.
  SEQ-3  Exported .seq files are now guaranteed pure ASCII:
         MultiVu/DynaCool reads .seq as ANSI, so the em-dash in
         the initial-wait REM note rendered as mojibake in the
         sequence editor. The note is reworded symbol-free and
         validate_ppms_seq() now rejects ANY non-ASCII character
         on any line (comments included), catching hand-edits too.

============================================================
v1.6 — TEMPERATURE-DEPENDENT TOLERANCE (low-T probe offset)
============================================================
  TOL-1  Measured 2026-07-18: the probe settles ABOVE the PPMS
         setpoint at low T (~1.15 K at 30 K, ~1.0 at 40 K, ~0.55
         at 50 K, ~0.3 at 60–70 K) — constant heat leak vs falling
         link conductance. One fixed tolerance either fails the
         band check down low or is too loose up high, and nobody
         can raise it live overnight. New "Tolerance varies with
         T" table (ON by default; presets "Safe (recommended)" /
         "As used 2026-07-18" / Custom): linear between entries,
         held above the top entry, slope-extrapolated (cap 3 K,
         logged) below the lowest, effective Tol = max(base Tol,
         table). Applies to the band check, flatness (2×tol), the
         plotted band and the mid-sweep drift flag; the DRIFT
         LIMIT is deliberately untouched — it stays the
         offset-immune "still equilibrating" guard.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import os
import re                        # SEQ-2: sequence grammar validation
import time
import math
import queue
import threading
import atexit
import traceback
import platform
import ctypes                    # HARD-3: Windows keep-awake during a run
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


def parse_clock_minutes(text, meridiem="24h"):
    """'H:MM' (+ AM/PM combobox choice) -> minutes since midnight.

    meridiem '24h': hours 0-23. 'AM'/'PM': 12-hour style where
    12 AM -> 0 h and 12 PM -> 12 h. Raises ValueError on garbage.
    """
    s = str(text).strip()
    if ":" not in s:
        raise ValueError(f"Time '{s}' must be H:MM.")
    h_str, m_str = s.split(":", 1)
    h, m = int(h_str), int(m_str)
    if not (0 <= m <= 59):
        raise ValueError(f"Minutes in '{s}' must be 00-59.")
    if meridiem in ("AM", "PM"):
        if not (1 <= h <= 12):
            raise ValueError(f"Hour in '{s}' must be 1-12 for {meridiem}.")
        h = h % 12 + (12 if meridiem == "PM" else 0)
    elif not (0 <= h <= 23):
        raise ValueError(f"Hour in '{s}' must be 0-23 (24h).")
    return h * 60 + m


def fmt_clock(minutes):
    """Minutes since midnight (any float, wrapped mod 24 h) -> 'H:MM AM/PM'."""
    total = int(round(minutes)) % (24 * 60)
    h24, m = divmod(total, 60)
    ampm = "AM" if h24 < 12 else "PM"
    h12 = h24 % 12 or 12
    return f"{h12}:{m:02d} {ampm}"


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
# SEQ-1/SEQ-2: MultiVu sequence rendering + strict validation
# (embedded copy from PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py —
# PICA programs never import from each other)
# ============================================================
def render_fscan_seq(sample, steps, mode, initial_note=None):
    """Render the per-step plan into a MultiVu sequence, in the exact
    line format of the reference Dielectric_Fscan.seq:
        TMP TEMP <target K> <rate K/min> <mode>
        WAI WAITFOR <wait s> 1 0 0 0 0     (temperature-stable + delay)
    steps: [(target_K, rate_K_per_min, wait_s), ...]
    mode:  TMP approach — 0 fast settle, 1 no overshoot (reference).
    """
    L = []
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    L.append(f"REM ==== PICA PPMS-Sync Fscan plan | sample: {sample} | "
             f"generated {stamp} ====")
    if initial_note:
        L.append(f"REM {initial_note}")
    for i, (target, rate, wait_s) in enumerate(steps, 1):
        L.append(f"REM -- step {i}: {target:g} K --")
        L.append(f"TMP TEMP {target:.6f} {rate:.6f} {int(mode)}")
        L.append(f"WAI WAITFOR {int(round(wait_s))} 1 0 0 0 0")
    L.append("REM ==== End of Fscan plan ====")
    return "\n".join(L) + "\n"


# MultiVu is unforgiving: one malformed line ruins an unattended run.
# The validator accepts general decimal formatting (a hand-edited
# "25.5" is as valid as the generator's "25.500000") but rejects any
# line whose SHAPE or VALUES a PPMS could not execute.
SEQ_TMP_RE = re.compile(
    r"^TMP TEMP (\d+(?:\.\d+)?) (\d+(?:\.\d+)?) ([01])$")
SEQ_FLD_RE = re.compile(
    r"^FLD FIELD (-?\d+(?:\.\d+)?) (\d+(?:\.\d+)?) ([0-2]) ([01])$")
SEQ_WAI_RE = re.compile(
    r"^WAI WAITFOR (\d+(?:\.\d+)?) ([01]) ([01]) ([01]) ([01]) ([01])$")

PPMS_MAX_TEMP_K = 400.0        # PPMS temperature range ends at 400 K
PPMS_MAX_TRATE = 20.0          # PPMS max temperature rate (K/min)
PPMS_MAX_FIELD_OE = 160000.0   # generous: covers any PPMS magnet


def validate_ppms_seq(text):
    """Check every line of a MultiVu sequence against the exact grammar
    of the reference sequences. Returns a list of error strings (empty
    list = the sequence is safe to save). Comment lines (REM), disabled
    lines (! prefix), MES remarks and blank lines are passed through.
    Every line (comments included) must be pure ASCII: MultiVu/DynaCool
    reads .seq files as ANSI, so a UTF-8 em-dash or arrow turns into
    mojibake in the sequence editor."""
    errors = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.isascii():
            bad = "".join(sorted({ch for ch in raw if not ch.isascii()}))
            errors.append(f"line {lineno}: non-ASCII character(s) {bad!r} "
                          f"- DynaCool reads .seq as ANSI and will "
                          f"garble them: {raw!r}")
            continue
        s = raw.strip()
        if not s or s.startswith(("REM", "!", "MES")):
            continue
        m = SEQ_TMP_RE.match(s)
        if m:
            temp, rate = float(m.group(1)), float(m.group(2))
            if not (0.0 < temp <= PPMS_MAX_TEMP_K):
                errors.append(f"line {lineno}: temperature {temp:g} K "
                              f"outside PPMS range (0, {PPMS_MAX_TEMP_K:g}]"
                              f": {s!r}")
            if not (0.0 < rate <= PPMS_MAX_TRATE):
                errors.append(f"line {lineno}: temperature rate {rate:g} "
                              f"K/min outside (0, {PPMS_MAX_TRATE:g}]: {s!r}")
            continue
        m = SEQ_FLD_RE.match(s)
        if m:
            field, rate = float(m.group(1)), float(m.group(2))
            if abs(field) > PPMS_MAX_FIELD_OE:
                errors.append(f"line {lineno}: field {field:g} Oe beyond "
                              f"±{PPMS_MAX_FIELD_OE:g} Oe: {s!r}")
            if rate <= 0:
                errors.append(f"line {lineno}: field rate must be "
                              f"positive: {s!r}")
            continue
        if SEQ_WAI_RE.match(s):
            continue
        errors.append(f"line {lineno}: does not match any known MultiVu "
                      f"command (TMP TEMP / FLD FIELD / WAI WAITFOR / "
                      f"REM / MES / !): {s!r}")
    return errors


# ============================================================
# TOL-1: temperature-dependent stability tolerance
# (the probe settles ABOVE the PPMS setpoint at low T — measured
# 2026-07-18: ~1.15 K high at 30 K, ~1.0 at 40 K, ~0.55 at 50 K,
# ~0.3 at 60–70 K, small above 100 K — so a single tolerance either
# fails the band check at low T or is too loose at high T)
# ============================================================
TOL_TABLE_PRESETS = {
    # observed offset + ~0.3 K cushion, settling to the overnight-safe 0.4
    "Safe (recommended)":
        "30:1.5, 40:1.2, 50:0.8, 60:0.55, 70:0.45, 100:0.4",
    # exactly the values used attended on 2026-07-18 (thin cushion)
    "As used 2026-07-18":
        "30:1.2, 40:1.2, 50:1.0, 60:0.5, 70:0.4, 100:0.4",
}
TOL_EXTRAP_CAP_K = 3.0   # ceiling for below-table extrapolation


def parse_tol_table(text):
    """'30:1.5, 40:1.2, …' -> sorted [(T_K, tol_K), …].
    Raises ValueError on garbage, duplicate temperatures or
    non-positive tolerances — a bad table must fail loudly at Start,
    never silently mid-run."""
    pairs = []
    for tok in str(text).replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" not in tok:
            raise ValueError(f"Tolerance table entry '{tok}' must be "
                             "T:tolerance (e.g. 30:1.5).")
        a, b = tok.split(":", 1)
        T, tol = float(a), float(b)
        if tol <= 0:
            raise ValueError(f"Tolerance at {T:g} K must be positive.")
        pairs.append((T, tol))
    if not pairs:
        raise ValueError("Tolerance table is empty.")
    pairs.sort()
    temps = [p[0] for p in pairs]
    if len(set(temps)) != len(temps):
        raise ValueError("Tolerance table has duplicate temperatures.")
    return pairs


def tol_from_table(table, target, base_tol):
    """Effective tolerance at a target temperature.

    Inside the table: linear interpolation. Above the top entry: hold
    the last value. Below the lowest entry: extrapolate the slope of
    the two lowest entries (the probe offset keeps growing toward
    base), never below the lowest entry and capped at
    TOL_EXTRAP_CAP_K. The result is never below base_tol — the table
    can only widen the tolerance, not tighten it."""
    if not table:
        return float(base_tol)
    temps = [t for t, _ in table]
    tols = [v for _, v in table]
    if target >= temps[-1]:
        v = tols[-1]
    elif target <= temps[0]:
        v = tols[0]
        if target < temps[0] and len(table) >= 2:
            slope = (tols[1] - tols[0]) / (temps[1] - temps[0])
            v = tols[0] + slope * (target - temps[0])
            v = min(max(v, tols[0]), TOL_EXTRAP_CAP_K)
    else:
        v = float(np.interp(target, temps, tols))
    return max(float(base_tol), float(v))


# ============================================================
# SMART-1: cooldown-end detection for the initial sleep
# (embedded copy from PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py)
# ============================================================
class TurnaroundDetector:
    """Detects warming-start (rise off the minimum) from a stream of
    probe temperatures. A median-of-5 filter on the incoming readings
    makes single glitched values (sensor spike, comm hiccup) unable to
    poison the tracked min/max or fake a turnaround."""

    MEDIAN_N = 5

    def __init__(self):
        self._raw = deque(maxlen=self.MEDIAN_N)
        self.min_T = float("inf")
        self.max_T = float("-inf")
        self.last_T = float("nan")   # median-filtered

    def update(self, temp):
        if temp is None or not math.isfinite(temp):
            return self.last_T
        self._raw.append(float(temp))
        med = float(np.median(list(self._raw)))
        self.last_T = med
        if med < self.min_T:
            self.min_T = med
        if med > self.max_T:
            self.max_T = med
        return med

    def warming_started(self, arm_below_k, rise_k):
        """True once T dipped below arm_below_k and has since risen
        rise_k off the observed minimum."""
        return (self.min_T <= arm_below_k
                and math.isfinite(self.last_T)
                and (self.last_T - self.min_T) >= rise_k)


class SustainedCondition:
    """A condition must hold CONTINUOUSLY for hold_s seconds before it
    counts — the phase only changes when it is definitely sure."""

    def __init__(self, hold_s):
        self.hold_s = max(0.0, float(hold_s))
        self._since = None

    def update(self, ok, now=None):
        now = time.time() if now is None else now
        if not ok:
            self._since = None
            return False
        if self._since is None:
            self._since = now
        return (now - self._since) >= self.hold_s


# ============================================================
# BACKEND: Lakeshore 350 as READ-ONLY probe thermometer
# ============================================================
class Probe_Thermometer_Backend:
    """Strictly read-only: the only commands ever sent are *CLS, *IDN?
    and KRDG? — the PPMS owns temperature control entirely."""

    def __init__(self):
        self.lakeshore = None
        self.visa_address = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")
        self.visa_address = visa_address   # HARD-2: kept for reconnect()
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.write("*CLS")
        idn = self.lakeshore.query("*IDN?").strip()
        if "350" not in idn:
            print(f"WARNING: IDN does not contain '350': {idn}")
        return idn

    def reconnect(self):
        """HARD-2: close and re-open the session from the stored address.
        Used by the worker's retry-forever loop after a comm failure;
        survives a controller power-cycle (still read-only afterwards)."""
        try:
            self.shutdown()
        except Exception as e:
            print(f"  Pre-reconnect cleanup warning: {e}")
        return self.connect(self.visa_address)

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
        # HARD-2: 15 s — LONG-aperture point time is < 1 s, so this is
        # generous while letting the retry loop detect a hung bus quickly.
        inst.timeout = 15000
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

    def reconnect(self):
        """HARD-2: close and fully re-initialize from the stored params.
        Used by the worker's retry-forever loop after a comm failure;
        re-init restores the whole configuration (RX mode, aperture,
        ALC, corrections, bias re-ramp) even after an instrument
        power-cycle."""
        try:
            self.close_instrument()
        except Exception as e:
            print(f"  Pre-reconnect cleanup warning: {e}")
        self.initialize_instrument(self.params)

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
    PROGRAM_VERSION = "1.6-PPMS-Sync"  # temperature-dependent tolerance
    LEFT_PANEL_WIDTH = 480

    # HARD-3: SetThreadExecutionState flags (Windows keep-awake during a run)
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

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

        # PPMS sequence-builder rows (editable planning aid; rendered in
        # the "Timing / PPMS Suggestions" tab). Each dict:
        #   target, dT (|Δ| from previous, None for the first), rate
        #   (K/min, None for the first), wait (min), status.
        self.seq_rows = []

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

        # Y-scale mode: 'auto' uses decade-snapped log when the data
        # spans >= 1 decade and linear otherwise; 'log'/'linear' force it.
        self.y_scale_var = tk.StringVar(value="auto")
        self._decade_ylims = {}

        # UI-4: scan label state — ("measuring"|"done", T_set) or None.
        # Rendered as the Cp axis title by _redraw_tick.
        self._scan_info = None

        # Worker-side phase marker (worker thread writes; CSV Phase column)
        self._worker_phase = None

        # Scan-time estimate state: measured mean per-point time replaces
        # the analytic model after the first completed sweep.
        self._measured_point_s = None
        self._scan_est_s = 0.0

        # HARD-1: rows a failing disk could not take yet (worker thread
        # only) — retried before every subsequent write, order preserved.
        self._pending_rows = deque()
        self._write_error_logged = False
        self.tlog_path = None
        self.timing_path = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(self.REDRAW_MS, self._redraw_tick)
        self._update_scan_estimate()
        self.log(f"PPMS-Sync GUI v{self.PROGRAM_VERSION} initialized. "
                 "Lakeshore 350 is READ-ONLY (KRDG? only) — the PPMS owns "
                 "all temperature control.")
        self.log("Run: optional one-time initial sleep, then per setpoint: "
                 "stability detection (band / flatness / both) -> "
                 "40 Hz–2 MHz sweep.")
        self.log("Use 'Generate PPMS plan' (Timing panel) to plan the PPMS "
                 "sequence BEFORE the run — wait table plus, with Start/End "
                 "times filled, a suggested ramp rate; rows update with "
                 "measured times as steps complete.")

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
    # Temperature schedule (simple setpoint list, like the original)
    # ------------------------------------------------------------
    def _create_schedule_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Temperature Schedule "
                                             "(same setpoints as the PPMS "
                                             "sequence)")
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
        self.entry_start = ttk.Entry(frame, width=6)
        self.entry_start.grid(row=1, column=1, sticky="w", padx=2)
        ttk.Label(frame, text="End(K):").grid(row=1, column=2, sticky="e", padx=2)
        self.entry_end = ttk.Entry(frame, width=6)
        self.entry_end.grid(row=1, column=3, sticky="w", padx=2)
        ttk.Label(frame, text="Step(K):").grid(row=2, column=0, sticky="e", padx=2)
        self.entry_step = ttk.Entry(frame, width=6)
        self.entry_step.grid(row=2, column=1, sticky="w", padx=2)
        self.sched_buttons = []
        b = ttk.Button(frame, text="Generate Steps", command=self._generate_steps)
        b.grid(row=2, column=2, columnspan=2, sticky="ew", padx=5, pady=2)
        self.sched_buttons.append(b)

        ttk.Label(frame, text="Order:").grid(row=3, column=0, sticky="e", padx=2)
        self.sort_var = tk.StringVar(value="Ascending")
        self.sort_cb = ttk.Combobox(frame, textvariable=self.sort_var,
                                    values=["Ascending", "Descending"],
                                    state="readonly", width=10)
        self.sort_cb.grid(row=3, column=1, sticky="w", padx=2)
        self.sort_cb.bind("<<ComboboxSelected>>", lambda e: self._sort_listbox())

        ttk.Label(frame, text="Manual(K):").grid(row=4, column=0, sticky="e", padx=2, pady=5)
        self.entry_manual = ttk.Entry(frame, width=6)
        self.entry_manual.grid(row=4, column=1, sticky="w", padx=2, pady=5)
        b = ttk.Button(frame, text="Add", command=self._add_manual_step)
        b.grid(row=4, column=2, sticky="ew", padx=2, pady=5)
        self.sched_buttons.append(b)
        b = ttk.Button(frame, text="Remove", command=self._remove_step)
        b.grid(row=4, column=3, sticky="ew", padx=2, pady=5)
        self.sched_buttons.append(b)
        b = ttk.Button(frame, text="Clear All", command=self._clear_listbox)
        b.grid(row=5, column=0, columnspan=4, sticky="ew", padx=10, pady=(0, 5))
        self.sched_buttons.append(b)

        ttk.Separator(frame, orient="horizontal").grid(
            row=6, column=0, columnspan=4, sticky="ew", pady=4, padx=10)

        # One-time initial sleep (first big cooldown), NOT per setpoint.
        self.var_sleep_enabled = tk.BooleanVar(value=True)
        self.chk_sleep = ttk.Checkbutton(
            frame, text="Initial sleep before the first setpoint:",
            variable=self.var_sleep_enabled)
        self.chk_sleep.grid(row=7, column=0, columnspan=2, sticky="w",
                            padx=10, pady=(2, 0))
        self.sleep_entry = ttk.Entry(frame, width=8)
        self.sleep_entry.insert(0, "3:30")
        self.sleep_entry.grid(row=7, column=2, sticky="w", padx=2, pady=(2, 0))
        ttk.Label(frame, text="(min or h:mm)").grid(row=7, column=3,
                                                    sticky="w", padx=2,
                                                    pady=(2, 0))
        ttk.Label(frame,
                  text="One-time wait at Start while the PPMS finishes its "
                       "first cooldown. Every later step goes straight to "
                       "stability detection.",
                  font=("Segoe UI", 8, "italic"), wraplength=420
                  ).grid(row=8, column=0, columnspan=4, sticky="w",
                         padx=10, pady=(0, 5))

        # SMART-1: temperature-inferred early end for the initial sleep
        # (ON by default per user decision 2026-07-17 — the protocol
        # always starts with the big cooldown, and detection beats a
        # blind timer; the fixed time above stays a fallback ceiling).
        self.var_smart_sleep = tk.BooleanVar(value=True)
        self.chk_smart_sleep = ttk.Checkbutton(
            frame, text="End sleep early when cooldown-end is detected:",
            variable=self.var_smart_sleep)
        self.chk_smart_sleep.grid(row=9, column=0, columnspan=2,
                                  sticky="w", padx=10, pady=(2, 0))
        ttk.Label(frame, text="Base arm (K):").grid(
            row=9, column=2, sticky="e", padx=2, pady=(2, 0))
        self.smart_arm_entry = ttk.Entry(frame, width=6)
        self.smart_arm_entry.insert(0, "30")
        self.smart_arm_entry.grid(row=9, column=3, sticky="w", padx=2,
                                  pady=(2, 0))
        ttk.Label(frame,
                  text="Probe must dip below Base arm, then rise 2 K off "
                       "its minimum, held 3 min (median-filtered) — the "
                       "PPMS is moving to the first setpoint. The timed "
                       "sleep above still ends the wait if detection "
                       "never fires.",
                  font=("Segoe UI", 8, "italic"), wraplength=420
                  ).grid(row=10, column=0, columnspan=4, sticky="w",
                         padx=10, pady=(0, 5))

    def _generate_steps(self):
        try:
            start = float(self.entry_start.get())
            end = float(self.entry_end.get())
            step = float(self.entry_step.get())
            if step <= 0:
                raise ValueError("Step must be positive")
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
            floats = sorted({float(x) for x in items},  # dedupe
                            reverse=(self.sort_var.get() == "Descending"))
            self.listbox.delete(0, tk.END)
            for v in floats:
                self.listbox.insert(tk.END, f"{v:.2f}")
        except Exception:
            pass

    def _get_targets(self):
        """Schedule as a list of floats (listbox order)."""
        return [float(x) for x in self.listbox.get(0, tk.END)]

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

        self.stab_mode_var = tk.StringVar(value="flat_band")
        self.stab_mode_radios = []
        for c, (value, label) in enumerate(self.STAB_MODES):
            rb = ttk.Radiobutton(frame, text=label,
                                 variable=self.stab_mode_var, value=value,
                                 command=self._on_stab_mode_changed)
            rb.grid(row=0 if c < 2 else 1, column=(c % 2) * 3,
                    columnspan=3, sticky="w", padx=10, pady=(5, 0))
            self.stab_mode_radios.append(rb)

        self._create_grid_entry(frame, "Tolerance (±K):", "tol", "0.5", 2, 0)
        self._create_grid_entry(frame, "Window (min):", "window_min", "10", 2, 3)
        self._create_grid_entry(frame, "Drift Lim (K/min):", "drift", "0.05", 3, 0)
        self._create_grid_entry(frame, "Target guard (±K, 0=off):",
                                "guard", "2.0", 3, 3)
        self._create_grid_entry(frame, "Timeout (min, 0=off):",
                                "stab_timeout", "90", 4, 0)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "2", 4, 3)

        # TOL-1: temperature-dependent tolerance table (ON by default —
        # the probe settles above the PPMS setpoint at low T, so a single
        # tolerance cannot serve 30 K and 300 K at once).
        self.var_tol_table = tk.BooleanVar(value=True)
        self.chk_tol_table = ttk.Checkbutton(
            frame, text="Tolerance varies with T:",
            variable=self.var_tol_table)
        self.chk_tol_table.grid(row=5, column=0, columnspan=3,
                                sticky="w", padx=10, pady=(4, 0))
        self.tol_preset_cb = ttk.Combobox(
            frame, state="readonly", width=18,
            values=list(TOL_TABLE_PRESETS) + ["Custom"])
        self.tol_preset_cb.set("Safe (recommended)")
        self.tol_preset_cb.grid(row=5, column=3, columnspan=3,
                                sticky="ew", padx=(2, 10), pady=(4, 0))
        self.tol_preset_cb.bind("<<ComboboxSelected>>",
                                self._on_tol_preset)
        self.tol_table_entry = ttk.Entry(frame, font=self.FONT_BASE)
        self.tol_table_entry.insert(
            0, TOL_TABLE_PRESETS["Safe (recommended)"])
        self.tol_table_entry.grid(row=6, column=0, columnspan=6,
                                  sticky="ew", padx=10, pady=2)
        self.tol_table_entry.bind(
            "<KeyRelease>", lambda e: self.tol_preset_cb.set("Custom"))

        # Live preview: the EFFECTIVE tolerance at every schedule
        # setpoint (or example temperatures) — 20 K, 25 K, anything
        # between table entries is spelled out, never left implicit.
        self.tol_preview_lbl = ttk.Label(
            frame, text="—", font=("Segoe UI", 8), wraplength=420,
            foreground="#2A6B3A", justify="left")
        self.tol_preview_lbl.grid(row=7, column=0, columnspan=6,
                                  sticky="w", padx=10, pady=(0, 2))

        ttk.Label(frame,
                  text="Flatness: peak-to-peak ≤ 2×Tol over the window, "
                       "any offset from target. Guard rejects 'stable at "
                       "the wrong setpoint'. Table: T:tol pairs, linear "
                       "between entries, held above the top entry, "
                       "extrapolated (cap 3 K) below the lowest; "
                       "effective Tol = max(base Tol, table) — read at "
                       "Start. Drift limit is unaffected.",
                  font=("Segoe UI", 8, "italic"), wraplength=420
                  ).grid(row=8, column=0, columnspan=6, sticky="w",
                         padx=10, pady=(0, 2))

        ttk.Button(frame, text="Apply Live Updates",
                   command=self._send_live_updates
                   ).grid(row=9, column=0, columnspan=6, sticky="ew",
                          padx=10, pady=(2, 6))
        self._on_stab_mode_changed()
        self.root.after(500, self._tol_preview_tick)

    def _on_tol_preset(self, event=None):
        name = self.tol_preset_cb.get()
        preset = TOL_TABLE_PRESETS.get(name)
        if preset is not None:
            self.tol_table_entry.delete(0, tk.END)
            self.tol_table_entry.insert(0, preset)
        self._update_tol_preview()

    def _tol_preview_tick(self):
        """Cheap periodic refresh so the preview always reflects the
        current table, base Tolerance and schedule."""
        try:
            self._update_tol_preview()
        except Exception:
            pass
        self.root.after(1500, self._tol_preview_tick)

    def _update_tol_preview(self):
        if not self.var_tol_table.get():
            self.tol_preview_lbl.config(
                text="Table OFF — the single Tolerance above applies at "
                     "every temperature.")
            return
        try:
            table = parse_tol_table(self.tol_table_entry.get())
            base = float(self.entries["tol"]["entry"].get())
        except (ValueError, tk.TclError):
            self.tol_preview_lbl.config(
                text="Effective tolerance: — (fix the table / Tolerance "
                     "entry first)")
            return
        try:
            targets = self._get_targets()
        except (ValueError, tk.TclError):
            targets = []
        if targets:
            temps = sorted(set(targets))
            prefix = "At your setpoints:  "
        else:
            temps = [20, 25, 30, 35, 40, 50, 60, 70, 80, 100, 200, 300]
            prefix = "Examples (add setpoints to see yours):  "
        parts = []
        for T in temps:
            tol = tol_from_table(table, T, base)
            mark = "*" if T < table[0][0] else ""
            parts.append(f"{T:g} K→±{tol:.2f}{mark}")
        txt = prefix + "   ".join(parts)
        if any(T < table[0][0] for T in temps):
            txt += "   (* extrapolated below the table)"
        self.tol_preview_lbl.config(text=txt)

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
        self.skip_button = ttk.Button(bf, text="Skip Step", state="disabled",
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

        self._create_grid_entry(frame, "Margin (min):", "margin_min", "10", 1, 0)
        ttk.Label(frame,
                  text="One button, whole plan: per-setpoint wait table "
                       "(= probe settle + scan time + Margin, see the "
                       "'Timing / PPMS Suggestions' tab) plus — when Start "
                       "and End are filled — a suggested PPMS ramp rate. "
                       "Works BEFORE the run; measured values replace each "
                       "row as steps complete.",
                  font=("Segoe UI", 8, "italic"), wraplength=420
                  ).grid(row=2, column=0, columnspan=6, sticky="w",
                         padx=10, pady=(0, 5))

        # Clock-time planner: start + estimated end -> suggested PPMS
        # ramp rate for the N-1 ramps between setpoints (advisory only).
        ttk.Separator(frame, orient="horizontal").grid(
            row=3, column=0, columnspan=6, sticky="ew", padx=10, pady=4)
        ttk.Label(frame, text="Start:").grid(row=4, column=0, sticky="w",
                                             padx=(10, 2), pady=2)
        self.time_start_entry = ttk.Entry(frame, font=self.FONT_BASE, width=7)
        self.time_start_entry.grid(row=4, column=1, sticky="ew", padx=2, pady=2)
        self.time_start_ampm = ttk.Combobox(
            frame, values=["24h", "AM", "PM"], state="readonly", width=4)
        self.time_start_ampm.set("AM" if datetime.now().hour < 12 else "PM")
        self.time_start_ampm.grid(row=4, column=2, sticky="w", padx=2, pady=2)
        # Editing Start also re-projects the sequence-builder finish clock.
        self.time_start_ampm.bind(
            "<<ComboboxSelected>>", lambda e: self._seq_recompute_total())
        self.time_start_entry.bind(
            "<KeyRelease>", lambda e: self._seq_recompute_total())
        self.time_now_button = ttk.Button(frame, text="Now", width=5,
                                          command=self._fill_start_now)
        self.time_now_button.grid(row=4, column=3, sticky="w", padx=2, pady=2)

        ttk.Label(frame, text="End (est.):").grid(row=5, column=0, sticky="w",
                                                  padx=(10, 2), pady=2)
        self.time_end_entry = ttk.Entry(frame, font=self.FONT_BASE, width=7)
        self.time_end_entry.grid(row=5, column=1, sticky="ew", padx=2, pady=2)
        self.time_end_ampm = ttk.Combobox(
            frame, values=["24h", "AM", "PM"], state="readonly", width=4)
        self.time_end_ampm.set("AM" if datetime.now().hour < 12 else "PM")
        self.time_end_ampm.grid(row=5, column=2, sticky="w", padx=2, pady=2)
        ttk.Label(frame, text="(H:MM; end may roll past midnight)",
                  font=("Segoe UI", 8, "italic")
                  ).grid(row=5, column=3, columnspan=3, sticky="w", padx=2)

        self._create_grid_entry(frame, "Max rate (K/min):", "max_rate", "12",
                                6, 0, lockable=False)
        self.suggest_button = ttk.Button(
            frame, text="Generate PPMS plan",
            command=self._generate_ppms_plan)
        self.suggest_button.grid(row=6, column=3, columnspan=3,
                                 sticky="ew", padx=(2, 10), pady=2)

        self.ramp_sug_lbl = ttk.Label(frame, text="Ramp suggestion: —",
                                      font=("Segoe UI", 9, "bold"),
                                      foreground=self.CLR_ACCENT_GOLD,
                                      wraplength=420, justify="left")
        self.ramp_sug_lbl.grid(row=7, column=0, columnspan=6, sticky="w",
                               padx=10, pady=(2, 5))

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

        # UI-3: single plot grid — temperature + Cp + G on one canvas
        p_tab = ttk.Frame(nb); nb.add(p_tab, text="Plots")
        self._build_plots(p_tab)

        s_tab = ttk.Frame(nb); nb.add(s_tab, text="Timing / PPMS Suggestions")
        self._build_timing_tab(s_tab)

    def _build_plots(self, parent):
        """UI-3: one figure, 2x2 grid. Temperature vs time spans the
        whole left column; Cp and G vs frequency (shared log x) stack
        in the right column."""
        self.fig = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = self.fig.add_gridspec(2, 2)
        self.ax_temp = self.fig.add_subplot(gs[:, 0])
        self.ax_cp = self.fig.add_subplot(gs[0, 1])
        self.ax_g = self.fig.add_subplot(gs[1, 1], sharex=self.ax_cp)

        self.line_target, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_GREEN, ls="--", label="Schedule target")
        self.line_temp, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker="o", ms=3, ls="-", label="Sample T")
        self.scat_meas, = self.ax_temp.plot([], [], ls="", marker="o", ms=5, color=self.CLR_MEAS, label="Measuring (flag=1)")
        self.ax_temp.set_xlabel("Time (s)")
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, ls="--", alpha=0.6)
        self.ax_temp.legend(loc="best", frameon=True, facecolor=self.CLR_GRAPH_BG)

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
        self.canvas_plots = FigureCanvasTkAgg(self.fig, parent)
        tb = NavigationToolbar2Tk(self.canvas_plots, parent, pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas_plots.get_tk_widget().pack(fill="both", expand=True)

    TIMING_COLS = ("step", "target", "dT", "rate", "ramp",
                   "wait", "steptot", "status")

    def _build_timing_tab(self, parent):
        ttk.Label(parent,
                  text="PPMS sequence guide (planning aid — this program "
                       "never commands the PPMS). Each setpoint gets its own "
                       "ramp rate and wait/soak; double-click a Rate or Wait "
                       "cell to edit, or use 'Set all' to change a whole "
                       "column at once. The Total and projected finish "
                       "recompute as you edit. During a run the measured PPMS "
                       "wait replaces each row's Wait.",
                  wraplength=900, background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT, justify="left"
                  ).pack(side="top", anchor="w", padx=8, pady=(6, 2))

        # --- Controls: load/reset + initial wait ---
        ctl = ttk.Frame(parent)
        ctl.pack(side="top", fill="x", padx=8, pady=2)
        self.seq_load_btn = ttk.Button(
            ctl, text="Load / reset from schedule",
            command=self._seq_load_from_schedule)
        self.seq_load_btn.pack(side="left", padx=(0, 14))
        ttk.Label(ctl, text="Initial wait (min or h:mm):",
                  background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT).pack(side="left")
        self.seq_init_entry = ttk.Entry(ctl, width=8)
        self.seq_init_entry.insert(0, "3:30")
        self.seq_init_entry.pack(side="left", padx=(4, 0))
        self.seq_init_entry.bind("<KeyRelease>",
                                 lambda e: self._seq_recompute_total())

        # --- Bulk "set all" for the Rate and Wait columns ---
        bulk = ttk.Frame(parent)
        bulk.pack(side="top", fill="x", padx=8, pady=(0, 2))
        ttk.Label(bulk, text="Set all →", background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT).pack(side="left")
        ttk.Label(bulk, text="Rate (K/min):", background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT).pack(side="left", padx=(12, 2))
        self.seq_rate_all = ttk.Entry(bulk, width=7)
        self.seq_rate_all.pack(side="left")
        self.seq_rate_all_btn = ttk.Button(
            bulk, text="Apply", width=7,
            command=lambda: self._seq_set_all("rate", self.seq_rate_all))
        self.seq_rate_all_btn.pack(side="left", padx=(2, 16))
        ttk.Label(bulk, text="Wait (min or h:mm):",
                  background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT).pack(side="left", padx=(0, 2))
        self.seq_wait_all = ttk.Entry(bulk, width=7)
        self.seq_wait_all.pack(side="left")
        self.seq_wait_all_btn = ttk.Button(
            bulk, text="Apply", width=7,
            command=lambda: self._seq_set_all("wait", self.seq_wait_all))
        self.seq_wait_all_btn.pack(side="left", padx=(2, 0))

        # --- SEQ-1: export the edited plan as a validated .seq file ---
        exp = ttk.Frame(parent)
        exp.pack(side="top", fill="x", padx=8, pady=(0, 2))
        ttk.Label(exp, text="TMP approach:", background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT).pack(side="left")
        self.seq_mode_cb = ttk.Combobox(
            exp, values=["Fast settle (0)", "No overshoot (1)"],
            state="readonly", width=14)
        self.seq_mode_cb.set("No overshoot (1)")   # reference Fscan default
        self.seq_mode_cb.pack(side="left", padx=(4, 12))
        self.seq_export_btn = ttk.Button(exp, text="Export .seq…",
                                         command=self._export_seq)
        self.seq_export_btn.pack(side="left")
        ttk.Label(exp,
                  text="(validated line-by-line before saving — a faulty "
                       "sequence ruins an unattended run)",
                  font=("Segoe UI", 8, "italic"),
                  background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT).pack(side="left", padx=8)

        # --- Live total / projected finish ---
        self.seq_total_lbl = ttk.Label(
            parent, text="Total: —  (click 'Load / reset from schedule')",
            font=("Segoe UI", 11, "bold"), background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD)
        self.seq_total_lbl.pack(side="top", anchor="w", padx=8, pady=(2, 4))

        tf = ttk.Frame(parent); tf.pack(fill="both", expand=True, padx=5, pady=5)
        sb = ttk.Scrollbar(tf, orient="vertical")
        self.timing_tree = ttk.Treeview(
            tf, columns=self.TIMING_COLS, show="headings",
            yscrollcommand=sb.set)
        sb.config(command=self.timing_tree.yview)
        heads = {"step": ("#", 36), "target": ("Target (K)", 82),
                 "dT": ("ΔT (K)", 66),
                 "rate": ("Rate K/min ✎", 108),
                 "ramp": ("Ramp time", 92),
                 "wait": ("Wait / soak ✎", 108),
                 "steptot": ("Step total", 96),
                 "status": ("Status", 168)}
        for col in self.TIMING_COLS:
            text, width = heads[col]
            self.timing_tree.heading(col, text=text)
            self.timing_tree.column(col, width=width, anchor="center",
                                    stretch=True)
        self.timing_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # Double-click a Rate/Wait cell to edit it in place.
        self.timing_tree.bind("<Double-1>", self._seq_on_double_click)

        # Disabled during a run (pre-run planning aids)
        self.seq_controls = [self.seq_load_btn, self.seq_init_entry,
                             self.seq_rate_all, self.seq_rate_all_btn,
                             self.seq_wait_all, self.seq_wait_all_btn,
                             self.seq_export_btn]

    def _on_y_scale_change(self):
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
            need_draw = False
            if self._temp_plot_dirty:
                self._temp_plot_dirty = False
                self._decimate_display_series()
                self.line_temp.set_data(self.plot_t, self.plot_temp)
                self.line_target.set_data(self.plot_t, self.plot_target)
                self.scat_meas.set_data(self.meas_t, self.meas_temp)
                self.ax_temp.relim(); self.ax_temp.autoscale_view()
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

    def _set_keep_awake(self, enable):
        """HARD-3: stop Windows from sleeping mid-run (display may still
        sleep). Best-effort no-op on other platforms."""
        try:
            flags = self.ES_CONTINUOUS | (
                self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

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
        self.log("SKIP STEP requested.")

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
        if self.is_running:
            return   # HARD-5: re-entry guard — never launch a second worker
        try:
            self.schedule = self._get_targets()
        except ValueError as e:
            messagebox.showerror("Schedule Error", f"Bad setpoint: {e}")
            return
        if not self.schedule:
            messagebox.showwarning("Empty Schedule", "Add at least one setpoint."); return
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
        self._scan_info = None            # UI-4: drop the held spectrum label
        self.ax_cp.set_title("")
        self.canvas_plots.draw_idle()
        self._decade_ylims.clear()
        self._temp_plot_dirty = False
        self._freq_plot_dirty = False
        self.progress["value"] = 0
        self._pending_progress = None
        self.progress["maximum"] = len(self.schedule) * len(self.sweep_frequencies)

        # Pre-fill the suggestions tab with lower-bound estimates;
        # measured rows replace them as steps complete.
        self._update_scan_estimate()
        sug = (self.params["window_min"] * 60.0 + self._scan_est_s
               + self.params["margin_min"] * 60.0)
        # Keep the user's edited plan if it already matches this schedule.
        self._fill_timing_tab(self.schedule, sug)

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
            "margin_min": float(self.entries["margin_min"]["entry"].get()),
            "thermo_visa": ls_visa,
            "channel": self.channel_cb.get() or "A",
            "sleep_enabled": self.var_sleep_enabled.get(),
            "initial_sleep_min": parse_duration_min(
                self.sleep_entry.get() or "0"),
            "smart_sleep": self.var_smart_sleep.get(),
            "smart_arm": float(self.smart_arm_entry.get() or "30"),
            # TOL-1: parsed once at Start; a bad table fails loudly here.
            "tol_table": (parse_tol_table(self.tol_table_entry.get())
                          if self.var_tol_table.get() else None),
        }
        if not p["thermo_visa"]: raise ValueError("Select the thermometer VISA.")
        if p["tol"] <= 0: raise ValueError("Tolerance must be positive.")
        if p["window_min"] <= 0: raise ValueError("Window must be positive.")
        if p["drift"] <= 0: raise ValueError("Drift limit must be positive.")
        if p["guard"] < 0: raise ValueError("Target guard must be >= 0 (0 disables).")
        if p["stab_timeout"] < 0: raise ValueError("Timeout must be >= 0 (0 disables).")
        if p["delay"] <= 0: raise ValueError("Poll delay must be positive.")
        if p["margin_min"] < 0: raise ValueError("Margin must be >= 0 min.")
        if p["initial_sleep_min"] < 0:
            raise ValueError("Initial sleep must be >= 0.")
        if p["smart_arm"] <= 0:
            raise ValueError("Base arm (K) must be positive.")
        for t in self.schedule:
            if t <= 0:
                raise ValueError(f"Schedule setpoint {t} K invalid.")
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
        # HARD-3: keep-awake tracks the run state exactly
        self._set_keep_awake(running)
        st = "disabled" if running else "normal"
        self.start_button.config(state=st)
        self.stop_button.config(state="normal" if running else "disabled")
        self.pause_button.config(state="normal" if running else "disabled")
        self.skip_button.config(state="normal" if running else "disabled")
        if not running:
            self.pause_button.config(text="Pause")
        # Stability mode + initial sleep are only read at Start
        for rb in self.stab_mode_radios:
            rb.config(state=st)
        self.chk_sleep.config(state=st)
        self.sleep_entry.config(state=st)
        self.chk_smart_sleep.config(state=st)
        self.smart_arm_entry.config(state=st)
        # TOL-1: the tolerance table is read once at Start
        self.chk_tol_table.config(state=st)
        self.tol_table_entry.config(state=st)
        self.tol_preset_cb.config(
            state="readonly" if not running else "disabled")
        # Pre-run suggestion generator would clobber measured rows mid-run
        self.suggest_button.config(state=st)
        # Clock-time ramp planner is pre-run only (advisory)
        self.time_now_button.config(state=st)
        self.time_start_entry.config(state=st)
        self.time_end_entry.config(state=st)
        self.time_start_ampm.config(
            state="readonly" if not running else "disabled")
        self.time_end_ampm.config(
            state="readonly" if not running else "disabled")
        for b in self.sched_buttons:
            b.config(state=st)
        self.sort_cb.config(state="readonly" if not running else "disabled")
        for e in (self.entry_start, self.entry_end, self.entry_step,
                  self.entry_manual):
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
        # Sequence-builder controls are pre-run planning aids
        for w in getattr(self, "seq_controls", []):
            try:
                w.config(state=st)
            except tk.TclError:
                pass
        # Comboboxes must go back to readonly, never editable "normal"
        if getattr(self, "seq_mode_cb", None) is not None:
            self.seq_mode_cb.config(
                state="disabled" if running else "readonly")

    # ------------------------------------------------------------
    # Timing tab plumbing (main thread)
    # ------------------------------------------------------------
    def _generate_ppms_plan(self):
        """One-button PPMS plan: fill the per-setpoint wait table, then —
        if Start / End (est.) are filled — also back-solve the ramp rate."""
        if not self._generate_pre_run_suggestions():
            return
        if (self.time_start_entry.get().strip()
                and self.time_end_entry.get().strip()):
            self._suggest_ramp_rate()
        else:
            self.ramp_sug_lbl.config(
                text="Ramp suggestion: — (fill Start and End (est.) above "
                     "to also get a suggested PPMS ramp rate)",
                foreground=self.CLR_ACCENT_GOLD)

    def _generate_pre_run_suggestions(self):
        """Fill the Timing / PPMS Suggestions tab from the schedule alone —
        no instruments needed, works before the run: per setpoint
        suggested wait = stability window + estimated scan + Margin.
        This is the pre-run 'Generate PPMS plan' path (Start rebuilds the
        table directly via _fill_timing_tab). Returns True on success."""
        if self.is_running:
            return False
        try:
            targets = self._get_targets()
        except ValueError as e:
            messagebox.showerror("Schedule", f"Bad setpoint in list: {e}")
            return False
        if not targets:
            messagebox.showwarning(
                "Empty Schedule",
                "Add setpoints first (Generate Steps or Manual Add).")
            return False
        try:
            window_min = float(self.entries["window_min"]["entry"].get())
            margin_min = float(self.entries["margin_min"]["entry"].get())
            if window_min <= 0 or margin_min < 0:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Timing",
                                 "Check Window (min) and Margin (min).")
            return False
        self._update_scan_estimate()
        sug = window_min * 60.0 + self._scan_est_s + margin_min * 60.0
        # FIX-1: 'Generate PPMS plan' is authoritative — RESET the table
        # to this plan (keep_edits would preserve stale cell edits and
        # make the left planner and the right table disagree). Fine-tune
        # cells by double-click AFTER generating.
        self._fill_timing_tab(targets, sug, keep_edits=False)
        self.log(f"PPMS suggestion (pre-run, all setpoints): wait ≥ "
                 f"{fmt_hms(sug)} at each temperature "
                 f"(= window {window_min:g} min + scan "
                 f"{fmt_hms(self._scan_est_s)} + margin {margin_min:g} min). "
                 f"Measured values will replace these during the run.")
        return True

    def _fill_start_now(self):
        now = datetime.now()
        h12 = now.hour % 12 or 12
        self.time_start_entry.delete(0, tk.END)
        self.time_start_entry.insert(0, f"{h12}:{now.minute:02d}")
        self.time_start_ampm.set("AM" if now.hour < 12 else "PM")

    def _suggest_ramp_rate(self):
        """Back-solve the PPMS ramp rate from the start / estimated-end
        clock times: rate = sum|dT| between setpoints over whatever time
        is left after the initial sleep and N per-setpoint waits
        (window + scan estimate + Margin — same formula as the pre-run
        suggestions). Advisory only; nothing is sent anywhere."""
        if self.is_running:
            return
        try:
            targets = self._get_targets()
        except ValueError as e:
            messagebox.showerror("Schedule", f"Bad setpoint in list: {e}")
            return
        if not targets:
            messagebox.showwarning(
                "Empty Schedule",
                "Add setpoints first (Generate Steps or Manual Add).")
            return
        try:
            window_min = float(self.entries["window_min"]["entry"].get())
            margin_min = float(self.entries["margin_min"]["entry"].get())
            if window_min <= 0 or margin_min < 0:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Timing",
                                 "Check Window (min) and Margin (min).")
            return
        try:
            max_rate = float(self.entries["max_rate"]["entry"].get())
            if max_rate <= 0:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Timing", "Max rate (K/min) must be > 0.")
            return
        try:
            start_min = parse_clock_minutes(self.time_start_entry.get(),
                                            self.time_start_ampm.get())
            end_min = parse_clock_minutes(self.time_end_entry.get(),
                                          self.time_end_ampm.get())
        except ValueError as e:
            messagebox.showerror("Timing", f"Start/End time: {e}")
            return
        try:
            sleep_s = (parse_duration_min(self.sleep_entry.get() or "0")
                       * 60.0 if self.var_sleep_enabled.get() else 0.0)
            if sleep_s < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Timing",
                                 "Check Initial sleep (min or h:mm).")
            return

        avail_s = ((end_min - start_min) % (24 * 60)) * 60.0
        if avail_s <= 0:
            messagebox.showerror("Timing",
                                 "Start and End times are identical.")
            return

        self._update_scan_estimate()
        n = len(targets)
        per_wait_s = window_min * 60.0 + self._scan_est_s + margin_min * 60.0
        stab_s = n * per_wait_s
        dT = float(sum(abs(b - a) for a, b in zip(targets, targets[1:])))
        ramp_budget_s = avail_s - sleep_s - stab_s

        # Shared breakdown text; the rate line + color depend on the case.
        def breakdown(ramp_s):
            parts = [f"Ramping {fmt_hms(ramp_s)} ({dT:.1f} K over "
                     f"{n - 1} ramps)",
                     f"Stabilizing {fmt_hms(stab_s)} "
                     f"({n} × {fmt_hms(per_wait_s)})"]
            if sleep_s > 0:
                parts.append(f"Initial delay {fmt_hms(sleep_s)}")
            return "  +  ".join(parts)

        if dT <= 0:
            total_s = sleep_s + stab_s
            head = "No ramping needed (single setpoint / zero span)."
            ok = total_s <= avail_s
            tail = (f"Total {fmt_hms(total_s)} → finishes "
                    f"~{fmt_clock(start_min + total_s / 60.0)}"
                    + ("" if ok else
                       f" — LATER than requested {fmt_clock(end_min)}"))
            text = f"{head}\n{breakdown(0)}\n{tail}" if n > 1 else \
                   f"{head}\n{tail}"
            color = self.CLR_ACCENT_GOLD if ok else self.CLR_ACCENT_RED
        elif ramp_budget_s <= 0:
            min_total_s = sleep_s + stab_s + dT / max_rate * 60.0
            text = ("INFEASIBLE: the initial delay + stabilizing waits "
                    f"alone exceed the {fmt_hms(avail_s)} window — no ramp "
                    "rate can help.\n"
                    f"{breakdown(dT / max_rate * 60.0)}\n"
                    f"Earliest possible end (at max {max_rate:g} K/min): "
                    f"~{fmt_clock(start_min + min_total_s / 60.0)} "
                    f"(total {fmt_hms(min_total_s)}).")
            color = self.CLR_ACCENT_RED
        else:
            # Round UP to 0.1 K/min so the plan finishes at/before End.
            rate_needed = dT / (ramp_budget_s / 60.0)
            rate = math.ceil(rate_needed * 10.0 - 1e-9) / 10.0
            clamped = rate > max_rate
            rate = min(rate, max_rate)
            ramp_s = dT / rate * 60.0
            total_s = sleep_s + ramp_s + stab_s
            finish = fmt_clock(start_min + total_s / 60.0)
            if clamped:
                head = (f"Needs {rate_needed:.1f} K/min — clamped to max "
                        f"{max_rate:g} K/min, end time will slip.")
                tail = (f"Total {fmt_hms(total_s)} → finishes ~{finish} "
                        f"(requested {fmt_clock(end_min)})")
                color = self.CLR_ACCENT_RED
            else:
                head = (f"Suggested PPMS rate: {rate:g} K/min "
                        f"(max {max_rate:g})")
                tail = f"Total {fmt_hms(total_s)} → finishes ~{finish}"
                color = self.CLR_ACCENT_GOLD
            text = f"{head}\n{breakdown(ramp_s)}\n{tail}"

        # FIX-1: write the solved rate into every ramp row of the
        # sequence table, so the left planner and the right table show
        # the SAME rates, the same Total and the same projected finish.
        # (Infeasible window -> max rate, matching the 'earliest
        # possible end' the label reports.)
        if dT > 0:
            applied = max_rate if ramp_budget_s <= 0 else rate
            for i, r in enumerate(self.seq_rows):
                if i > 0 and r.get("dT"):
                    r["rate"] = applied
            self._seq_render()
            self.log(f"Sequence table: applied {applied:g} K/min to all "
                     "ramp rows (matches the planner above).")

        self.ramp_sug_lbl.config(text=text, foreground=color)
        self.log("PPMS ramp planner ["
                 f"{fmt_clock(start_min)} → {fmt_clock(end_min)}]: "
                 + text.replace("\n", " | "))

    # ------------------------------------------------------------
    # PPMS sequence builder (editable planning table)
    # ------------------------------------------------------------
    def _seq_default_rate(self, target, prev):
        """Default ramp rate (K/min) for the PPMS plan, clamped to Max
        rate. RATE-1: the reference Dielectric_Tscan.seq /
        Dielectric_Fscan.seq drive every step at a flat 1 K/min — the
        PPMS owns the ramp here, so that reference rate is the default
        for all setpoints (the old temperature-tiered 0.5/1/2 K/min
        defaults were for the LN2-dewar Lakeshore rig). Purely a
        starting suggestion — every cell is editable."""
        if prev is None:
            return None
        rate = 1.0
        try:
            max_rate = float(self.entries["max_rate"]["entry"].get())
            if max_rate > 0:
                rate = min(rate, max_rate)
        except (ValueError, tk.TclError, KeyError):
            pass
        return rate

    def _seq_clamp_rate(self, rate):
        """Clamp a user-entered rate to the Max rate envelope, logging
        when it bites (Max rate is the global safety ceiling)."""
        try:
            max_rate = float(self.entries["max_rate"]["entry"].get())
            if max_rate <= 0:
                raise ValueError
        except (ValueError, tk.TclError):
            return rate
        if rate > max_rate:
            self.log(f"Sequence: rate {rate:g} K/min clamped to Max rate "
                     f"{max_rate:g} K/min.")
            return max_rate
        return rate

    def _fill_timing_tab(self, targets, suggest_s, keep_edits=True):
        """(Re)build the sequence-builder rows from a list of setpoints,
        seeding each per-step wait from the suggested PPMS wait (seconds)
        and each rate from the temperature-aware default. When keep_edits
        is True and the schedule is unchanged, existing rate/wait edits
        are preserved (only a re-render happens)."""
        targets = [float(t) for t in targets]
        if (keep_edits and self.seq_rows
                and [r["target"] for r in self.seq_rows] == targets):
            self._seq_render()
            return
        # FIX-1: store the wait UNROUNDED so the table total agrees with
        # the left planner to the second (was round(.., 2) -> up to
        # 0.3 s/row drift between the two displays).
        wait_min = suggest_s / 60.0
        rows = []
        prev = None
        for t in targets:
            dT = None if prev is None else abs(t - prev)
            rate = None if (prev is None or dT == 0) \
                else self._seq_default_rate(t, prev)
            rows.append({"target": t, "dT": dT, "rate": rate,
                         "wait": wait_min, "status": "planned"})
            prev = t
        self.seq_rows = rows
        # Mirror the schedule's initial-sleep field as the initial wait.
        # Pre-run only: during a run seq_init_entry is disabled (writes
        # would be ignored) and the user's planned value must stand.
        # FIX-1: mirror in BOTH states — a disabled sleep must zero the
        # table's initial wait, or the two totals disagree.
        if (not self.is_running
                and getattr(self, "seq_init_entry", None) is not None):
            self.seq_init_entry.delete(0, tk.END)
            self.seq_init_entry.insert(
                0, (self.sleep_entry.get() or "0")
                if self.var_sleep_enabled.get() else "0")
        self._seq_render()

    def _seq_load_from_schedule(self):
        """Pull the current setpoints and rebuild a fresh default plan
        (discards previous rate/wait edits)."""
        if self.is_running:
            return
        try:
            targets = self._get_targets()
        except ValueError as e:
            messagebox.showerror("Schedule", f"Bad setpoint in list: {e}")
            return
        if not targets:
            messagebox.showwarning(
                "Empty Schedule",
                "Add setpoints first (Generate Steps or Manual Add).")
            return
        self._update_scan_estimate()
        try:
            window_min = float(self.entries["window_min"]["entry"].get())
            margin_min = float(self.entries["margin_min"]["entry"].get())
        except (ValueError, tk.TclError):
            window_min, margin_min = 10.0, 10.0
        sug_s = window_min * 60.0 + self._scan_est_s + margin_min * 60.0
        self._fill_timing_tab(targets, sug_s, keep_edits=False)
        self.log(f"PPMS sequence loaded ({len(targets)} setpoints): default "
                 f"wait {fmt_hms(sug_s)} per step, temperature-aware rates. "
                 f"Double-click Rate/Wait cells or use 'Set all' to edit.")

    def _export_seq(self):
        """SEQ-1: render the (edited) per-step plan into a MultiVu .seq
        file. Every line is validated against the exact grammar before
        the file can be saved (SEQ-2) — pre-run, user-initiated, so
        dialogs are fine here."""
        if self.is_running:
            return
        if not self.seq_rows:
            messagebox.showinfo(
                "Sequence", "No steps yet — click 'Load / reset from "
                            "schedule' first.")
            return
        mode = 1 if "(1)" in self.seq_mode_cb.get() else 0
        steps = []
        for r in self.seq_rows:
            target = float(r["target"])
            rate = r.get("rate")
            if not rate:
                # First setpoint has no ramp row in the table; MultiVu
                # still needs a rate — use the temperature-aware default.
                rate = self._seq_default_rate(target, target) or 1.0
            wait_min = r.get("wait") or 0.0
            steps.append((target, float(rate), wait_min * 60.0))

        # ASCII only: MultiVu/DynaCool reads .seq files as ANSI, so any
        # non-ASCII character (em-dash, arrows, ...) shows up as mojibake
        # like 'a-euro-' garbage in the sequence editor.
        note = None
        init_txt = self.seq_init_entry.get().strip()
        try:
            if init_txt and parse_duration_min(init_txt) > 0:
                note = (f"Initial wait {init_txt} is a PC-side sleep in "
                        "the measurement program. Each WAITFOR below "
                        "starts only after the PPMS reports stable.")
        except ValueError:
            pass

        sample = self.lcr_entries["sample_name"].get().strip() or "Sample"
        text = render_fscan_seq(sample, steps, mode, initial_note=note)
        errors = validate_ppms_seq(text)
        if errors:
            shown = "\n".join(errors[:8])
            if len(errors) > 8:
                shown += f"\n… and {len(errors) - 8} more."
            messagebox.showerror(
                "Invalid Sequence — NOT saved",
                "The exported plan has errors (check per-step rates and "
                f"waits in the table):\n\n{shown}")
            self.log(f"Sequence export blocked: {len(errors)} validation "
                     "error(s).")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".seq",
            initialfile=f"{sample}_Fscan.seq",
            filetypes=[("MultiVu sequence", "*.seq"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.log(f"PPMS sequence exported and VALIDATED "
                     f"({len(steps)} steps, TMP mode {mode}): {path}")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))

    def _seq_render(self):
        """Redraw the sequence tree from self.seq_rows and refresh totals.
        Each row caches its ramp and step seconds for the total."""
        if not hasattr(self, "timing_tree"):
            return
        for iid in self.timing_tree.get_children():
            self.timing_tree.delete(iid)
        for i, r in enumerate(self.seq_rows):
            dT = r.get("dT")
            rate = r.get("rate")
            wait_min = r.get("wait", 0.0) or 0.0
            if i == 0 or dT is None or dT <= 0 or not rate:
                ramp_s = 0.0
                dT_txt = "—" if dT is None else f"{dT:.2f}"
                rate_txt = "—"
                ramp_txt = "—"
            else:
                ramp_s = dT / rate * 60.0
                dT_txt = f"{dT:.2f}"
                rate_txt = f"{rate:g}"
                ramp_txt = fmt_hms(ramp_s)
            wait_s = wait_min * 60.0
            step_s = ramp_s + wait_s
            r["_ramp_s"] = ramp_s
            r["_step_s"] = step_s
            self.timing_tree.insert("", "end", iid=f"s{i}", values=(
                i + 1, f"{r['target']:.2f}", dT_txt, rate_txt, ramp_txt,
                fmt_hms(wait_s), fmt_hms(step_s),
                r.get("status", "planned")))
        self._seq_recompute_total()

    def _seq_recompute_total(self):
        """Total = initial wait + Σ(ramp + wait); append the projected
        finish clock when a Start time is filled."""
        if not hasattr(self, "seq_total_lbl"):
            return
        try:
            init_min = parse_duration_min(self.seq_init_entry.get() or "0")
            if init_min < 0:
                init_min = 0.0
        except ValueError:
            init_min = 0.0
        steps_s = sum(r.get("_step_s", 0.0) for r in self.seq_rows)
        total_s = init_min * 60.0 + steps_s
        n = len(self.seq_rows)
        if n == 0:
            self.seq_total_lbl.config(
                text="Total: —  (click 'Load / reset from schedule')")
            return
        txt = (f"Total: {fmt_hms(total_s)}   (initial wait "
               f"{fmt_hms(init_min * 60.0)} + {n} step"
               f"{'' if n == 1 else 's'} {fmt_hms(steps_s)})")
        start_txt = self.time_start_entry.get().strip()
        if start_txt:
            try:
                start_min = parse_clock_minutes(
                    start_txt, self.time_start_ampm.get())
                txt += ("   →  finishes ~ "
                        f"{fmt_clock(start_min + total_s / 60.0)}")
            except ValueError:
                pass
        self.seq_total_lbl.config(text=txt)

    def _seq_set_all(self, field, entry):
        """Bulk-set the Rate or Wait column from the header entry."""
        if not self.seq_rows:
            messagebox.showinfo(
                "Sequence", "No steps yet — click 'Load / reset from "
                            "schedule' first.")
            return
        txt = entry.get().strip()
        try:
            if field == "wait":
                v = parse_duration_min(txt)
                if v < 0:
                    raise ValueError
            else:
                v = float(txt)
                if v <= 0:
                    raise ValueError
        except ValueError:
            messagebox.showerror(
                "Sequence",
                "Enter a positive number "
                + ("(min or h:mm) " if field == "wait" else "")
                + "to set the whole column.")
            return
        if field == "rate":
            v = self._seq_clamp_rate(v)
        for i, r in enumerate(self.seq_rows):
            if field == "rate" and (i == 0 or not r.get("dT")):
                continue  # first setpoint has no ramp
            r[field] = v
        self._seq_render()
        self.log(f"Sequence: set all {field} = {v:g}"
                 + (" min." if field == "wait" else " K/min."))

    def _seq_on_double_click(self, event):
        """Open an in-place editor for a Rate or Wait cell."""
        if self.is_running:
            return
        if self.timing_tree.identify_region(event.x, event.y) != "cell":
            return
        col = self.timing_tree.identify_column(event.x)
        rowid = self.timing_tree.identify_row(event.y)
        if not rowid or not col:
            return
        try:
            col_name = self.TIMING_COLS[int(col.replace("#", "")) - 1]
            idx = int(rowid[1:])
        except (ValueError, IndexError):
            return
        if col_name not in ("rate", "wait"):
            return
        if not (0 <= idx < len(self.seq_rows)):
            return
        if col_name == "rate" and (idx == 0 or not self.seq_rows[idx].get("dT")):
            return  # first setpoint has no ramp
        bbox = self.timing_tree.bbox(rowid, col)
        if not bbox:
            return
        self._seq_edit_cell(idx, col_name, bbox)

    def _seq_apply_cell_value(self, idx, col_name, raw):
        """Parse/validate a raw cell string and write it into seq_rows[idx].
        Rate must be > 0 (clamped to Max rate); wait may be >= 0 and accepts
        min or H:MM. Re-renders on success. Returns (ok, error_message)."""
        if not (0 <= idx < len(self.seq_rows)):
            return False, "Row no longer exists."
        try:
            if col_name == "wait":
                v = parse_duration_min(raw)
                if v < 0:
                    raise ValueError
            else:
                v = float(raw)
                if v <= 0:
                    raise ValueError
        except (ValueError, TypeError):
            return False, (
                "Wait must be a non-negative number (min or h:mm)."
                if col_name == "wait"
                else "Rate must be a positive number (K/min).")
        if col_name == "rate":
            v = self._seq_clamp_rate(v)
        self.seq_rows[idx][col_name] = v
        self._seq_render()
        return True, ""

    def _seq_edit_cell(self, idx, col_name, bbox):
        x, y, w, h = bbox
        cur = self.seq_rows[idx][col_name]
        editor = ttk.Entry(self.timing_tree)
        editor.place(x=x, y=y, width=w, height=h)
        editor.insert(0, f"{cur:g}" if cur is not None else "")
        editor.select_range(0, tk.END)
        editor.focus_set()
        state = {"done": False}

        def commit(event=None):
            if state["done"]:
                return
            state["done"] = True
            raw = editor.get().strip()
            editor.destroy()
            ok, err = self._seq_apply_cell_value(idx, col_name, raw)
            if not ok:
                messagebox.showerror("Sequence", err)

        def cancel(event=None):
            if state["done"]:
                return
            state["done"] = True
            editor.destroy()

        editor.bind("<Return>", commit)
        editor.bind("<KP_Enter>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def _apply_timing_row(self, m):
        """During a run: replace a step's planned Wait with the MEASURED
        PPMS wait (settle + scan + margin) and update its status. Planned
        rate/ramp are kept; the total re-projects live."""
        idx = m["index"]
        if 0 <= idx < len(self.seq_rows):
            self.seq_rows[idx]["wait"] = round(m["suggest_s"] / 60.0, 2)
            self.seq_rows[idx]["status"] = f"measured · {m['status']}"
            self._seq_render()

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
                elif t == "point_time":
                    self._measured_point_s = m["avg"]
                    self._update_scan_estimate()
                elif t == "timing_row":
                    self._apply_timing_row(m)
                elif t == "sequence_complete":
                    # UNAT-1: never open a modal dialog from the queue
                    # pump — it would block all further queue processing
                    # (logs, plots, worker_done) until someone clicks OK,
                    # and unattended runs have nobody in the lab.
                    # HARD-5: the UI is re-enabled ONLY on worker_done —
                    # the worker is still closing instruments here, and a
                    # re-enabled Start could launch a second worker onto
                    # the same VISA sessions.
                    self.log("★★★ SEQUENCE COMPLETE — all schedule steps "
                             "measured. Data is on disk. ★★★")
                    self._beep()
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
        self.tlog_path = None
        self.timing_path = None
        self._pending_rows.clear()
        self._write_error_logged = False
        try:
            self._put_gui_msg("log", text="Connecting to probe thermometer "
                                          "(Lakeshore 350, read-only)…")
            idn = self.thermo_backend.connect(self.params["thermo_visa"])
            self._put_gui_msg("log", text=f"Thermometer: {idn} "
                                          f"(channel {self.params['channel']})")

            self._put_gui_msg("log", text="Connecting to Keysight E4980A…")
            self.lcr_backend.initialize_instrument(self.lcr_params)
            self._put_gui_msg("log", text="E4980A initialized.")

            # HARD-1: both logs are written open/append/close + fsync per
            # row — no handle is held, so a power cut or disk hiccup can
            # never lose or corrupt previously written rows.
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.tlog_path = os.path.join(
                self.save_dir,
                f"{self.lcr_params['sample_name']}_{stamp}_TempLog.csv",
            )
            self._write_or_buffer(self.tlog_path, self._csv_line(
                ["Timestamp", "Elapsed_s", "Target_K", "Sample_T_K",
                 "Measuring", "Phase"]))
            self._put_gui_msg("log", text=f"Temperature log: {self.tlog_path}")

            self.timing_path = os.path.join(
                self.save_dir,
                f"{self.lcr_params['sample_name']}_{stamp}_TimingLog.csv",
            )
            self._write_or_buffer(self.timing_path, self._csv_line(
                ["Step", "Target_K", "Step_start", "Sleep_used_s",
                 "Stab_wait_s", "Stab_outcome", "Scan_s",
                 "TempDriftDuringScan",
                 "Suggested_PPMS_wait_s", "Suggested_PPMS_wait_hms"]))
            self._put_gui_msg("log", text=f"Timing log: {self.timing_path}")

            total_pts = len(self.schedule) * len(self.sweep_frequencies)
            done_pts = 0
            n_steps = len(self.schedule)

            # Phase 0: ONE-TIME initial sleep (first big PPMS cooldown)
            initial_sleep_used_s = 0.0
            p = self.params
            if p["sleep_enabled"] and p["initial_sleep_min"] > 0 \
                    and self.is_running:
                first_target = self.schedule[0]
                self._send_band_msg(first_target)
                outcome, initial_sleep_used_s = self._sleep_phase(
                    first_target, p["initial_sleep_min"] * 60.0)
                if outcome == "stopped":
                    self.is_running = False

            for i, target in enumerate(self.schedule):
                if not self.is_running:
                    break
                self._put_gui_msg("log",
                    text=f"--- Step {i+1}/{n_steps}: target {target} K ---")
                self._send_band_msg(target)

                step_start_dt = datetime.now()
                sleep_used_s = initial_sleep_used_s if i == 0 else 0.0

                # Phase 1: WAIT_STABLE (active detection)
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

                # Phase 2: SCAN
                self._put_gui_msg("log",
                    text=f"Starting frequency sweep at {target} K "
                         f"(sample reads {self._last_temp:.3f} K).")
                self._put_gui_msg("status",
                    text=f"SCANNING AT {target} K", color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("beep")
                # UI-4: clear the held previous spectrum only NOW, when
                # new points are imminent (was at the top of the loop).
                self._put_gui_msg("scan_reset", target=target)
                done_pts, scan_s, drift_flag = self._run_frequency_sweep(
                    target, done_pts, total_pts)

                self._write_timing_row(
                    i, target, step_start_dt, sleep_used_s, settle_s,
                    stab_outcome, scan_s, drift_flag)
                if not self.is_running:
                    break
                self._put_gui_msg("scan_done", target=target)  # UI-4
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
        band modes; ±guard (if set, else ±2·tol) in pure flatness mode.
        TOL-1: the plotted band uses the same per-target effective
        tolerance as the stability check, so what you see is what is
        tested."""
        p = self.params
        tol = tol_from_table(p.get("tol_table"), target, p["tol"])
        if p["mode"] == "flat":
            halfw = p["guard"] if p["guard"] > 0 else 2.0 * tol
        else:
            halfw = tol
        self._put_gui_msg("band", center=target, halfw=halfw)

    def _sleep_phase(self, target, sleep_s):
        """Phase 0: ONE-TIME initial wait while the PPMS finishes its
        first cooldown. Pause freezes the countdown (deadline shifts);
        Skip ends the sleep early. SMART-1: when enabled, the sleep also
        ends as soon as cooldown-end is DETECTED from the probe (dip
        below Base arm, rise 2 K off the minimum, held 3 min) — the
        timed sleep then only acts as a fallback ceiling.
        Returns (outcome, used_s);
        outcome in "done" | "detected" | "skipped" | "stopped"."""
        p = self.params
        self._worker_phase = "SLEEP"
        phase_start = time.time()
        deadline = phase_start + sleep_s
        smart = bool(p.get("smart_sleep"))
        det = TurnaroundDetector()
        confirm = SustainedCondition(180.0)   # 3 min "definitely sure"
        self._put_gui_msg("log",
            text=f"Initial sleep {fmt_hms(sleep_s)} before stability "
                 f"detection at {target} K (skip with 'Skip Step')."
                 + (f" Smart end armed: dip below {p['smart_arm']:g} K, "
                    "rise 2 K, hold 3 min." if smart else ""))
        try:
            while self.is_running and time.time() < deadline:
                if self._process_cmd_queue():
                    return "stopped", time.time() - phase_start
                if self._skip_requested:
                    self._skip_requested = False
                    self._put_gui_msg("log",
                        text="⏭ Sleep skipped — starting stability detection.")
                    return "skipped", time.time() - phase_start
                temp = self._log_temperature_point(target, measuring_flag=0)
                if self._paused:
                    deadline += p["delay"]   # paused time doesn't count
                    self._put_gui_msg("status",
                        text=f"PAUSED (sleeping, target {target} K)",
                        color=self.CLR_ACCENT_GOLD)
                else:
                    if smart:
                        det.update(temp)
                        if confirm.update(
                                det.warming_started(p["smart_arm"], 2.0)):
                            used = time.time() - phase_start
                            self._put_gui_msg("log",
                                text=f"COOLDOWN END DETECTED after "
                                     f"{fmt_hms(used)} (min "
                                     f"{det.min_T:.2f} K, now "
                                     f"{det.last_T:.2f} K) — ending the "
                                     f"initial sleep "
                                     f"{fmt_hms(sleep_s)} early; starting "
                                     "stability detection.")
                            self._put_gui_msg("beep")
                            return "detected", used
                    remaining = deadline - time.time()
                    self._put_gui_msg("status",
                        text=(f"SLEEPING — {fmt_hms(remaining)} left "
                              f"(of {fmt_hms(sleep_s)}) | target {target} K"
                              + (" | auto-end on warming" if smart else "")),
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

        # TOL-1: per-target effective tolerance (probe offset grows at
        # low T). Drift limit is deliberately NOT table-widened — it is
        # the offset-immune "still equilibrating" guard.
        tol = tol_from_table(p.get("tol_table"), target, p["tol"])
        span_ok = span >= 0.95 * p["window_min"] * 60.0
        drift_ok = abs(drift) <= p["drift"]
        band_ok = max_dev <= tol
        flat_ok = pkpk <= 2.0 * tol
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
        # TOL-1: announce the effective tolerance for this step
        eff_tol = tol_from_table(p.get("tol_table"), target, p["tol"])
        if p.get("tol_table"):
            note = ""
            if target < p["tol_table"][0][0]:
                note = (" (EXTRAPOLATED below the table's "
                        f"{p['tol_table'][0][0]:g} K entry)")
            self._put_gui_msg("log",
                text=f"Effective tolerance at {target} K: "
                     f"±{eff_tol:g} K [table]{note}")
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

    # ------------------------------------------------------------
    # HARD-1: durable, buffered file writes (worker thread only)
    # ------------------------------------------------------------
    @staticmethod
    def _csv_line(fields):
        """Comma-joined CSV row. Every field this program writes is
        comma-free by construction (numbers, ISO timestamps, single
        words), so no quoting is needed."""
        return ",".join(str(v) for v in fields) + "\n"

    @staticmethod
    def _durable_append(path, text):
        """Append text and force it to the physical disk immediately, so
        a sudden power cut cannot lose OS-buffered rows."""
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def _write_or_buffer(self, path, text):
        """Write a row durably; on a disk/share hiccup, buffer it for
        retry on the next write so measured data is never silently
        dropped. While rows are pending, new rows join the buffer to
        preserve per-file ordering."""
        self._flush_pending_rows()
        if self._pending_rows:
            self._pending_rows.append((path, text))
            return
        try:
            self._durable_append(path, text)
        except OSError as e:
            self._pending_rows.append((path, text))
            if not self._write_error_logged:
                self._write_error_logged = True
                self._put_gui_msg("log",
                    text=f"⚠️ WRITE ERROR: {e} — buffering rows and "
                         "retrying on every subsequent write.")

    def _flush_pending_rows(self):
        while self._pending_rows:
            path, text = self._pending_rows[0]
            try:
                self._durable_append(path, text)
            except OSError:
                return   # still failing; keep buffer, retry next write
            self._pending_rows.popleft()
        if self._write_error_logged:
            self._write_error_logged = False
            self._put_gui_msg("log",
                text="Write path recovered; buffered rows flushed.")

    # ------------------------------------------------------------
    # HARD-2: retry-forever comm recovery (worker thread only)
    # ------------------------------------------------------------
    def _reconnect_with_backoff(self, name, reconnect_fn, attempt):
        """Reconnect one instrument with escalating waits between tries
        (5 -> 10 -> 30 -> 60 s cap). Loops until reconnected; returns
        False only if Stop was requested while waiting or reconnecting.
        Stop stays responsive (cmd queue polled every second)."""
        backoffs = (5, 10, 30, 60)
        while self.is_running:
            delay_s = backoffs[min(attempt - 1, len(backoffs) - 1)]
            self._put_gui_msg("status",
                text=f"COMM ERROR ({name}) — reconnecting…",
                color=self.CLR_ACCENT_RED)
            self._put_gui_msg("log",
                text=f"Reconnect ({name}) attempt #{attempt} in {delay_s} s "
                     "(Stop stays responsive)…")
            deadline = time.time() + delay_s
            while time.time() < deadline:
                if self._process_cmd_queue():
                    return False
                time.sleep(1.0)
            try:
                reconnect_fn()
                self._put_gui_msg("log",
                    text=f"{name} reconnected. Resuming where it left off.")
                return True
            except Exception as e:
                self._put_gui_msg("log", text=f"Reconnect ({name}) failed: {e}")
                attempt += 1
        return False

    _last_temp = float("nan")
    _overtemp_warned = False

    def _log_temperature_point(self, target, measuring_flag):
        """Reads the probe thermometer, writes a durable CSV row, queues
        the plot message. On a comm failure the thermometer is
        reconnected with escalating backoff, forever (HARD-2); returns
        NaN only if Stop was requested during recovery. Read-only — no
        safety actions exist because this program controls no heater."""
        attempt = 0
        while True:
            try:
                temp = self.thermo_backend.get_temperature(
                    self.params["channel"])
                break
            except Exception as e:
                attempt += 1
                self._put_gui_msg("log",
                    text=f"⚠️ THERMOMETER COMM ERROR (failure #{attempt}): {e}")
                if not self._reconnect_with_backoff(
                        "thermometer", self.thermo_backend.reconnect,
                        attempt):
                    return float("nan")   # Stop requested during recovery
        self._last_temp = temp
        # SAFE-1: one-time loud overtemperature warning. This program is
        # strictly read-only — it cannot act, only alert the console.
        if temp > 340.0 and not self._overtemp_warned:
            self._overtemp_warned = True
            self._put_gui_msg("log",
                text=f"⚠️⚠️ SAMPLE ABOVE 340 K ({temp:.2f} K)! This "
                     "program is read-only and cannot act — check the "
                     "PPMS sequence!")
            self._put_gui_msg("beep")
        elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        phase = "PAUSED" if self._paused else (self._worker_phase or "")
        self._write_or_buffer(self.tlog_path, self._csv_line(
            [now_str, f"{elapsed:.2f}", f"{target:.4f}", f"{temp:.4f}",
             measuring_flag, phase]))
        self._put_gui_msg("temp_point", t=elapsed, temp=temp, target=target,
                          measuring=measuring_flag)
        return temp

    def _run_frequency_sweep(self, target_temp, done_pts, total_pts):
        """Runs the E4980A frequency sweep at one stable setpoint.
        Logs temperature (flag=1) interleaved between frequency points.
        Watches for the sample temperature leaving the stability band
        mid-sweep (flag-only: the sweep always completes). Comm errors
        reconnect forever and resume at the same point (HARD-2); every
        row is fsync'd to disk (HARD-1).
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
        drift_halfw = 2.0 * tol_from_table(
            self.params.get("tol_table"), target_temp,
            self.params["tol"])
        n_measured = 0
        self._write_or_buffer(
            fpath,
            f"# Sample: {self.lcr_params['sample_name']} | T_set = {target_temp} K | "
            f"AC: {self.lcr_params['ac_bias']} V | DC: {self.lcr_params['dc_bias']} V | "
            f"APER: {self.lcr_params['aper']}\n"
            + self.lcr_backend.DATA_HEADER + "\n")
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
            # HARD-2: a comm failure never aborts the sweep — reconnect
            # with backoff (re-init restores the full LCR config) and
            # retry the SAME frequency point until it measures or Stop.
            comm_attempt = 0
            while True:
                try:
                    R, X, status = self.lcr_backend.perform_measurement(
                        freq, self.lcr_params["delay"]
                    )
                    break
                except Exception as e:
                    comm_attempt += 1
                    self._put_gui_msg("log",
                        text=f"⚠️ LCR COMM ERROR @ {freq} Hz "
                             f"(failure #{comm_attempt}): {e}")
                    if not self._reconnect_with_backoff(
                            "E4980A", self.lcr_backend.reconnect,
                            comm_attempt):
                        break   # Stop requested during recovery
            if not self.is_running:
                break
            if status != 0:
                self._put_gui_msg("log",
                    text=f"⚠️ E4980A status {status} @ {freq:.1f} Hz — row kept, check manual")
            vals = self.calculate_impedance_parameters(freq, R, X)
            row = [freq] + vals + [target_temp]
            self._write_or_buffer(
                fpath, "\t".join(f"{v:.6E}" for v in row) + "\n")
            n_measured += 1
            # Interleaved temperature log (flag=1) + mid-sweep drift watch
            temp = self._log_temperature_point(target_temp,
                                               measuring_flag=1)
            if not self.is_running:
                break
            if not drift_flag and abs(temp - ref_T) > drift_halfw:
                drift_flag = True
                self._put_gui_msg("log",
                    text=f"⚠️⚠️ TEMPERATURE DRIFT DURING SCAN at "
                         f"{target_temp} K: sample moved to "
                         f"{temp:.3f} K (> ±{drift_halfw:g} K from "
                         f"{ref_T:.3f} K). PPMS probably moved on — "
                         f"increase its wait time. Sweep continues; "
                         f"step is flagged in the timing log.")
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

    def _write_timing_row(self, index, target, step_start_dt, sleep_s,
                          settle_s, stab_outcome, scan_s, drift_flag):
        """One row per completed step: measured timings + the PPMS wait
        suggestion (settle + scan + Margin; the initial sleep counts into
        the first step's settle). Written to the TimingLog CSV and
        mirrored into the Timing / PPMS Suggestions tab."""
        p = self.params
        margin_s = p["margin_min"] * 60.0
        suggest_s = sleep_s + settle_s + scan_s + margin_s
        status = {"stable": "stable", "timeout": "TIMEOUT — check data",
                  "forced": "forced by user"}.get(stab_outcome, stab_outcome)
        if drift_flag:
            status += " | DRIFT DURING SCAN"
        self._write_or_buffer(self.timing_path, self._csv_line(
            [index + 1, f"{target:.4f}",
             step_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
             f"{sleep_s:.1f}", f"{settle_s:.1f}", stab_outcome,
             f"{scan_s:.1f}", int(drift_flag),
             f"{suggest_s:.1f}", fmt_hms(suggest_s)]))
        self._put_gui_msg("timing_row", index=index, target=target,
                          sleep_s=sleep_s, settle_s=settle_s, scan_s=scan_s,
                          suggest_s=suggest_s, status=status)
        self._put_gui_msg("log",
            text=f"→ Suggested PPMS wait at {target} K: {fmt_hms(suggest_s)} "
                 f"(settle {fmt_hms(sleep_s + settle_s)} + scan "
                 f"{fmt_hms(scan_s)} + margin {p['margin_min']:g} min).")

    def _close_data_file(self):
        """HARD-1: files are opened per append, so there is nothing to
        close — just retry any rows a failing disk left buffered."""
        try:
            self._flush_pending_rows()
        except Exception:
            pass
        n = len(self._pending_rows)
        if n:
            self._put_gui_msg("log",
                text=f"WARNING: {n} data row(s) could not be written "
                     "(disk error) — see the first WRITE ERROR above.")
        else:
            self._put_gui_msg("log", text="All data rows are on disk.")

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
