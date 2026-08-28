"""
Module: PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py
Purpose: PPMS Dielectric Master — fully unattended multi-day protocol
         combining PASSIVE warming temperature scans (Tscan) and
         temperature-STEPPED frequency scans (Fscan) on the Keysight
         E4980A, with a Cryo-con Model 34 used STRICTLY READ-ONLY as
         the probe thermometer. The PPMS runs its own MultiVu sequence;
         this program never talks to the PPMS and never sends a single
         control command to the Cryocon (no STOP/CONTROL/loop/setpoint).

This is the Cryo-con sibling of
PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py, which stays exactly as
it is for Lakeshore 350 work. Only the thermometer differs: the
protocol, the phase detector, the sequence generator, the hardening and
the data format are the same code.

THE PROTOCOL (mirrors the reference MultiVu sequences in
pica/PPMS/data_file_for_ref/Dielectric_Tscan.seq / Dielectric_Fscan.seq):

  For each passive run in the run list (e.g. 0 Oe, 5000 Oe):
    1. WAIT_BASE — PPMS cools to base (10 K set; probe bottoms out
                   ~20 K, ~3 h). This program idles, logging T only.
                   (For field runs the PPMS sets the field at base.)
    2. TSCAN     — PPMS warms base→top at the warming rate (1 K/min)
                   + a hold at top (30 min). This program measures a
                   multi-frequency dielectric cycle CONTINUOUSLY the
                   whole way, exactly like
                   Temprature_Scan_Passive_E4980A_GUI.py (one legacy
                   19-column file per frequency, per run).
  Then:
    3. WAIT_BASE — final PPMS cooldown to base.
    4. FSCAN     — temperature-step frequency scans: per schedule
                   setpoint, rolling-window stability detection from
                   the probe thermometer alone, then a dense
                   40 Hz–2 MHz sweep — exactly like
                   PPMS_Sync_Freq_Scan_E4980A_GUI.py.

PHASE TRANSITIONS — temperature inference with time guards:
  The PC cannot see the PPMS state or the magnetic field; it infers
  every transition from the probe temperature alone:
    cooldown done / warming started:
        probe fell below "Base arm" (default 30 K) and has since risen
        "Rise" K (default 2 K) off its observed minimum.
    warming run over / next cooldown started:
        probe rose above top − "Top arm offset" (default 10 K) and has
        since fallen "Fall" K (default 2 K) off its observed maximum.
  A median-of-5 filter makes the detector immune to single glitched
  readings. Field values are FILE TAGS ONLY (run order defines which
  is which). Every phase has an expected duration; an overdue phase
  logs loud repeating warnings but NEVER aborts (unattended policy).

PPMS SEQUENCE GENERATOR:
  One button writes the complete MultiVu .seq for the whole protocol
  (cooldowns, FLD set/reset, warming ramps, top holds, and the Fscan
  section with per-step waits = stability window + scan estimate +
  margin) in the exact format of the reference sequences:
      TMP TEMP <K> <K/min> <mode>
      FLD FIELD <Oe> <Oe/s> <approach> <mode>
      WAI WAITFOR <s> <T> <H> <pos> <chamber> <err>
  The generated text is shown in an editable preview before saving, so
  the PICA plan and the MultiVu sequence can never silently disagree.

UNATTENDED-RUN HARDENING (inherited from both parents, v1.3 pattern):
  - Durable data writes: every row is open/append/close + flush +
    os.fsync; a disk/share hiccup buffers rows and retries them on
    every subsequent write — measured data is never silently dropped.
  - Comm errors retry forever with escalating backoff (5→10→30→60 s);
    the E4980A re-init restores its full configuration even after an
    instrument power-cycle. Stop stays responsive throughout.
  - Windows keep-awake (SetThreadExecutionState) during a run.
  - Stabilization timeout proceeds with the sweep and flags the step;
    mid-sweep temperature drift is flagged, never aborted.

Architecture (inherited from PPMS_Sync_Freq_Scan_E4980A_GUI.py):
  - Single GUI thread, single hardware worker thread. Worker owns BOTH
    instruments; no VISA access from the Tk thread.
  - Two queues: cmd_queue (GUI->worker), gui_queue (worker->GUI).
  - Throttled plot redraw, display decimation, beep via gui_queue,
    bounded queue drain, VISA read retry with device clear.
  - Crash-safe finally block + atexit.
Self-contained by design: PICA programs never import from each other;
shared logic is embedded as a copy.

============================================================
v1.0 — initial release (Tscan + Fscan master protocol)
============================================================

============================================================
v1.1 — SEQUENCE-GENERATOR ACCURACY
============================================================
  SEQ-1  TMP approach-mode toggles: separate GUI selectors for the
         Tscan section (default Fast settle, mode 0 — as the reference
         Dielectric_Tscan.seq) and the Fscan steps (default No
         overshoot, mode 1 — as the reference Dielectric_Fscan.seq).
  SEQ-2  validate_ppms_seq(): every non-comment line of a sequence is
         checked against the exact MultiVu grammar (TMP TEMP / FLD
         FIELD / WAI WAITFOR) with PPMS value ranges (T <= 400 K,
         rate <= 20 K/min). Runs automatically on every generated
         preview (a failure there is a program bug and is reported
         loudly) and again on Save — so even MANUAL edits in the
         preview box are caught before a faulty sequence can reach
         MultiVu.
  SEQ-3  Cooldown arithmetic made explicit: each run's cooldown wait
         is reported as PPMS ramp time (span / cool rate) + probe
         soak; a cooldown wait SHORTER than the ramp itself is
         rejected at generation (the sequence would start warming
         before base is ever reached).

============================================================
v1.2 — UNATTENDED-RUN RESCUE + CRASH AID + FSCAN STEP SKIP
============================================================
  FALL-1 Cooldown fallback ceiling (user decision 2026-07-17, ON by
         default at 2x expected; 0 disables): a WAIT_BASE phase that
         never detects warming (first Fscan setpoint at/below the
         probe's bottom-out T, sensor quirk, mis-set Base arm) used
         to wait FOREVER while the PPMS walked the rest of the
         sequence alone — the whole unattended run was lost. Now the
         phase also ends, loudly flagged ("fallback (time ceiling)"
         in the ProtocolLog), at Fallback x its expected duration.
         Turnaround detection stays primary.
  AID-1  RUNSTATE crash/restart aid: a tiny
         {sample}_{stamp}_Master_RUNSTATE.txt in the save dir is
         rewritten (fsync'd) at every phase transition with the
         current status, phase i/n and last probe T — after a power
         cut the file shows exactly where the protocol died. Start
         warns (console only) about RUNSTATE files of previous
         unfinished runs in the same folder.
  SKIP-1 New "Skip Freq Step" button (Fscan only): abandons the
         CURRENT temperature step — stability wait or running sweep
         — and moves to the NEXT schedule setpoint in one press.
         "Skip Phase" is unchanged (cooldown -> next phase; Tscan ->
         end run; stability wait -> sweep now; sweep -> abort rest).
         Pressed outside an Fscan step it is ignored with a log line.
  SEQ-4  validate_ppms_seq() now rejects ANY non-ASCII character on
         any line (comments included): MultiVu/DynaCool reads .seq
         files as ANSI, so a UTF-8 em-dash renders as mojibake in
         the sequence editor.

============================================================
v1.3 — TEMPERATURE-DEPENDENT TOLERANCE (low-T probe offset)
============================================================
  TOL-1  Measured 2026-07-18 on the Sync module: the probe settles
         ABOVE the PPMS setpoint at low T (~1.15 K at 30 K, ~1.0 at
         40 K, ~0.55 at 50 K, ~0.3 at 60–70 K). New "Tolerance
         varies with T" table (ON by default; presets
         "Safe (recommended)" / "As used 2026-07-18" / Custom):
         linear between entries, held above the top entry,
         slope-extrapolated (cap 3 K, logged) below the lowest,
         effective Tol = max(base Tol, table). Applies to the
         band check, flatness (2×tol), the plotted band and the
         mid-sweep drift flag; the DRIFT LIMIT is deliberately
         untouched — it stays the offset-immune "still
         equilibrating" guard. Embedded copy of the Sync v1.6
         TOL-1 logic.

============================================================
v1.4 — EXPLICIT LOW-T TOLERANCES + FAST-SETTLE DEFAULT
============================================================
  TOL-2  The tolerance presets now spell out 20 K and 25 K
         EXPLICITLY (numerically identical to what the old
         below-table extrapolation produced) so the low-T Fscan
         setpoints are directly editable in the entry. Base
         Tolerance default 0.5 → 0.4 (the table's high-T floor).
         Mirrors Sync v1.7 — the two embedded copies must stay
         identical (tested).
  SEQ-3  The Fscan TMP approach now ALSO defaults to
         "Fast settle (0)" (user decision 2026-07-19), matching
         the Tscan default; the reference Fscan's
         "No overshoot (1)" stays selectable.
  COOL-1 "Add Cooldown Only": a run-list entry that is JUST a
         cooldown to base — no field tag, no warming Tscan
         (used e.g. before taking M(H) manually during warming).
         Contributes one WAIT_BASE phase and a TMP-to-base +
         timed WAITFOR pair in the generated .seq, nothing else.
         Unlike measurement cooldowns, standalone cooldowns end
         ON TIME (when the planned wait — the .seq WAITFOR — is
         over) with a LOUD tell (green banner + double beep:
         "start the manual M(H) heating run NOW"), because the
         next step is the user's; waiting for warming detection
         would deadlock (the user waits for the signal, the
         signal waits for their heating). Warming detection
         stays as an early-out.
  COOL-2 After the M(T) runs a FINAL COOLDOWN IS SET BY DEFAULT
         even when no Fscan schedule exists (checkbox, ON): the
         protocol always ends at base unless unticked, so the
         manual M(H)-during-heating starts from base without
         extra clicks. Uses the existing Final cooldown (h:mm)
         duration; same timed end + loud tell as COOL-1. Its
         duration is now validated against the cooldown ramp
         time in this case too. The run LIST shows it as an
         explicit trailing line ("then (default): FINAL
         COOLDOWN to base (3:00) -> e.g. take M(H) while
         heating"), live-updated with the checkbox and the
         duration entry, so what will happen is never implicit.

============================================================
v1.5 — DESYNC-RECOVERY BACKPORT (from Sync v1.8, 2026-07-24)
============================================================
Backport of the Co-07 Fscan-at-Tstep forensic fixes; the two
embedded copies stay behaviorally identical where the programs
overlap (Fscan phase).
  DATA-1   Fscan rows carry the MEASURED probe temperature
           (T_sample(K), read right before each row); the
           commanded setpoint appears ONLY in the '#' header.
           After the sweep the median measured T is inserted as
           a second TOP comment (no footers — user decision).
  FNAME-1  Fscan filenames carry the measured temperature with
           'p' as the decimal mark (Sample_80p05K_..._FreqScan
           .txt); provisional name from the start-of-sweep
           reading, atomically renamed to the sweep MEDIAN.
  GLITCH-1 Probe readings validated before use: T ≤ 1 K
           (Lake Shore dropout) or a > 20 K one-poll jump is an
           INVALID READ — TempLog phase '|GLITCH', never fed to
           stability windows, turnaround detectors' data rows or
           plots. A real step is accepted once two consecutive
           reads agree (±2 K).
  DESYNC-1 Loud MISLABEL-RISK banner + beep when a stabilization
           timeout ends with the sample outside the guard around
           the target (no modal dialogs — unattended policy).
  TIME-1   Stabilization Timeout default 90 → 35 min: it must
           stay below one PPMS Fscan step period (ramp + step
           wait) minus the scan, or one timeout leaves the
           scanner a full step behind the PPMS.
  RATE-2   Fscan ramp default 1 → 0.5 K/min: the probe follows
           the block at only ~0.3-0.5 K/min above 120 K
           (measured, Co-07 TempLog) — faster commanded ramps
           only build probe lag.
  RESYNC-1 "Re-sync to PPMS on timeout" checkbox (OFF by
           default): on a diverged stabilization timeout the
           Fscan step counter jumps forward to the later
           setpoint the sample actually sits at; skipped steps
           become SKIPPED-resync TimingLog rows.
  AGNOS-1  "Setpoint-agnostic Fscan" checkbox (OFF by default):
           the generated .seq still steps the schedule, but the
           scanner scans each detected plateau (self-referenced
           flatness + drift) and labels it with the measured
           MEDIAN — nothing can desync. Completes after one
           plateau per schedule entry; Min ΔT separates
           plateaus; Skip Wait scans the current window, Skip
           Freq Step abandons one plateau slot.
  DWELL-1  "Fscan PPMS wait varies with T" (T:min pairs, ON by
           default, 200:30 210:40 310:45): each generated Fscan
           WAITFOR becomes max(computed step wait, table) — the
           .seq under-waits nowhere even though probe settle
           time grows with T (the PPMS's own temp-stable gate
           fires while the probe still lags).
  UI fixes worker_done drains the GUI queue tail; Skip can break
           out of a mid-sweep Pause; non-lockable entries are
           read-only while running.

============================================================
v1.5-CC34 — CRYO-CON MODEL 34 SIBLING (29 Aug 2026)
============================================================
Derived from PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py v1.5.
That file is unchanged and stays the Lakeshore 350 program; this one
is its Cryo-con sibling, not a replacement. Everything below is
forced by the instrument:

  CC34-1  Thermometry is a Cryo-con Model 34. The reading command is
          'INPUT? <ch>' rather than 'KRDG? <ch>', and the reply is
          parsed by parse_cryocon_number() instead of float(): a
          Model 34 can answer '77.350K' (trailing unit), a run of
          dashes (sensor fault) or a run of dots (reading off the
          sensor's calibration curve), and a bare float() turned all
          three into a dead worker thread — mid-protocol, on a run
          that takes days to reach that point.
  CC34-2  The input channel is verified at Start. INPUT? reports in
          each channel's own display units, so a channel left in C
          or F would log wrong numbers against every point of the
          protocol AND mislead every phase-transition detector.
          'INPUT <ch>:UNITS?' must answer K, and the channel must
          return a live reading, before the run begins.
  CC34-3  The instrument is chosen by identity, never by address.
          The Cryocon is at GPIB0::12 as of 29 Aug 2026 and the
          Lakeshore 350 answers on GPIB1::12 — the Cryo-con's own
          factory address — so an address-based pick would drive the
          whole phase machine off the wrong instrument. Connect
          refuses anything whose *IDN? is not a Cryo-con.
  CC34-4  Opening is retried and all traffic is paced. On a Rev
          3.03A unit the first '*IDN?' after a bus scan could time
          out inside viWrite; a settle delay, three attempts and a
          minimum gap between operations fix it. GPIB device clear
          is NOT used: the Cryo-con guide does not document its
          effect (ALLOW_DEVICE_CLEAR_ON_RETRY, off).
  CC34-5  A sensor-status reply is an INVALID READING, not a comm
          error. The instrument answered; the sensor did not.
          get_temperature() returns NaN, GLITCH-1 flags the point
          '|GLITCH' in the TempLog, the turnaround detectors never
          see it, and the protocol carries on — where raising would
          have sent the retry-forever reconnect loop spinning for
          days on a fault reconnecting cannot cure.
  CC34-6  Still strictly read-only, and more strictly than before:
          the backend has no write() path at all. No *RST (on a
          Cryocon that is a ~15 s hardware reset to power-up
          defaults), no STOP, no CONTROL, no loop, heater, range or
          setpoint command. Whatever is driving the cryostat is
          untouched from Start to Stop.
  CC34-7  The VISA scan no longer probes serial resources and no
          longer forces "\n" terminations on the instruments it
          identifies: the Cryocon's GPIB port frames lines with EOI
          and no EOS character.
  CC34-8  A bad reading is RETRIED, not spent. Sensor-status
          replies, a range switch and a lone garbage spike all
          clear on their own, usually within a second, so a
          reading that fails validation is re-read in place
          (three times inside one poll, then for up to
          INVALID_READ_RETRY_S) before the point is given up
          on. Past that, every later poll retries as well, so
          a longer outage still recovers on its own with
          nobody in the lab. Comm errors retry FOREVER, as
          they always did (HARD-2). The per-poll window is
          bounded and degrades to one quick re-read once the
          sensor looks genuinely down: readings are
          interleaved between frequency points, so an
          unbounded wait would stretch every sweep and walk
          the scanner out of step with the PPMS.
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
import ctypes                    # Windows keep-awake during a run
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
# Timing helpers (copied from PPMS_Sync_Freq_Scan_E4980A_GUI.py)
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
TEMP_LOG_S = 0.10            # one interleaved INPUT? per frequency point


def estimate_scan_seconds(freqs, freq_delay, aper):
    """Model estimate of one full frequency sweep, in seconds."""
    base, cycles = APER_MEAS_MODEL.get(aper, APER_MEAS_MODEL["MED"])
    total = 0.0
    for f in freqs:
        total += freq_delay + max(base, cycles / max(float(f), 1.0)) \
                 + VISA_OVERHEAD_S + TEMP_LOG_S
    return total


# ============================================================
# TOL-1: temperature-dependent stability tolerance
# (embedded copy from PPMS_Sync_Freq_Scan_E4980A_GUI.py — the probe
# settles ABOVE the PPMS setpoint at low T; measured 2026-07-18:
# ~1.15 K high at 30 K, ~1.0 at 40 K, ~0.55 at 50 K, ~0.3 at
# 60–70 K, small above 100 K)
# ============================================================
TOL_TABLE_PRESETS = {
    # observed offset + ~0.3 K cushion, settling to the overnight-safe
    # 0.4. TOL-2: 20 K and 25 K are EXPLICIT entries (numerically what
    # the below-table extrapolation used to produce) so they can be
    # edited directly instead of being implied. TOL-3 (2026-07-23):
    # 20/25 K widened to 3.0/2.5 — kept numerically identical to the
    # PPMS_Sync copy (test_tol_table_copy_matches_sync).
    "Safe (recommended)":
        "20:3.0, 25:2.5, 30:1.5, 40:1.2, 50:0.8, 60:0.55, "
        "70:0.45, 100:0.4",
    # exactly the values used attended on 2026-07-18 (thin cushion);
    # 20/25 K spelled out flat, matching the old flat extrapolation
    "As used 2026-07-18":
        "20:1.2, 25:1.2, 30:1.2, 40:1.2, 50:1.0, 60:0.5, "
        "70:0.4, 100:0.4",
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
# GLITCH-1: probe-reading validation thresholds
# (embedded copy from PPMS_Sync_Freq_Scan_E4980A_GUI.py — the Co-07
# run logged 39 stray 0.000 K reads plus a few non-zero garbage
# spikes: brief Lake Shore glitches, never real temperature)
# ============================================================
GLITCH_LOW_K = 1.0       # PPMS base T is 1.8 K; anything ≤ 1 K is a dropout
GLITCH_JUMP_K = 20.0     # > 20 K in one ~2 s poll is physically impossible
GLITCH_CONFIRM_K = 2.0   # two consecutive "jumped" reads this close = real


# ============================================================
# CC34-8: invalid-read retry (transient faults clear themselves)
# ============================================================
# A Cryo-con can spoil a single poll without anything being wrong for
# long: it answers with dashes while an input range switches, with dots
# for a reading that has briefly wandered off the sensor's calibration
# curve, and a noisy cable can produce one garbage value. All of those
# clear on their own, usually within a second, so a reading that fails
# GLITCH-1 validation is RE-READ rather than spent. Only if it is still
# bad after the window below does the point get flagged; every later poll
# retries too, so recovery from a longer outage is still automatic and
# needs nobody in the lab.
#
# The window is bounded on purpose. Temperature is read interleaved
# between frequency points, so an unbounded wait on a genuinely dead
# sensor would stretch every sweep and walk the scanner out of step with
# the PPMS - exactly the desync DESYNC-1 exists to catch. After
# STREAK_MAX consecutive polls that never recovered, the sensor is
# treated as down and each poll pays only one quick re-read until a valid
# reading returns (which resets the streak and the full window).
#
# Comm errors are NOT bounded here: those retry FOREVER in
# _log_temperature_point via _reconnect_with_backoff (HARD-2).
INVALID_READ_RETRY_S = 6.0    # per-poll re-read window; 0 = accept first read
INVALID_READ_POLL_S = 0.5     # gap between re-reads inside that window
INVALID_READ_STREAK_MAX = 5   # polls that never recovered -> sensor is down


def fmt_temp_p(T):
    """FNAME-1: 80.05 -> '80p05' — a dot inside a filename stem reads
    as a bogus extension to some tools, so the decimal mark becomes
    'p' in scan filenames (the real values live in the header)."""
    return f"{float(T):.2f}".replace(".", "p")


# ============================================================
# DWELL-1: temperature-dependent PPMS Fscan wait (WAITFOR) floor
# (same T:value syntax and parser as the tolerance table; different
# lookup: hold below the first entry, interpolate between entries,
# hold above the last — no extrapolation, a dwell must never shrink
# below what was explicitly entered)
# ============================================================
DWELL_TABLE_DEFAULT = "200:30, 210:40, 310:45"


def dwell_from_table(table, target):
    """Suggested PPMS wait (minutes) at a target temperature.
    Below the first entry / above the last: hold that entry's value;
    between entries: linear interpolation."""
    temps = [t for t, _ in table]
    waits = [v for _, v in table]
    if target <= temps[0]:
        return float(waits[0])
    if target >= temps[-1]:
        return float(waits[-1])
    return float(np.interp(target, temps, waits))


def fscan_step_wait_s(cfg, target):
    """DWELL-1: the WAITFOR seconds for one Fscan step — the computed
    step wait (window + scan + margin), floored by the wait-vs-T table
    when one is present in cfg."""
    base = float(cfg["step_wait_s"])
    table = cfg.get("dwell_table")
    if table:
        return max(base, dwell_from_table(table, target) * 60.0)
    return base


# ============================================================
# PROTOCOL LOGIC (pure, instrument-free — unit-testable)
# ============================================================
class TurnaroundDetector:
    """Detects warming-start (rise off the minimum) and cooling-start
    (fall off the maximum) from a stream of probe temperatures.

    A median-of-5 filter on the incoming readings makes single glitched
    values (sensor spike, comm hiccup) unable to poison the tracked
    min/max or fake a turnaround.
    """

    MEDIAN_N = 5

    def __init__(self):
        self._raw = deque(maxlen=self.MEDIAN_N)
        self.min_T = float("inf")
        self.max_T = float("-inf")
        self.last_T = float("nan")   # median-filtered

    def update(self, temp):
        """Feed one reading; returns the filtered value used internally."""
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

    def cooling_started(self, arm_above_k, fall_k):
        """True once T rose above arm_above_k and has since fallen
        fall_k off the observed maximum."""
        return (self.max_T >= arm_above_k
                and math.isfinite(self.last_T)
                and (self.max_T - self.last_T) >= fall_k)


class SustainedCondition:
    """A condition must hold CONTINUOUSLY for hold_s seconds before it
    counts. The master only changes phase when it is definitely sure:
    a transient dip/wobble that breaks the condition resets the clock."""

    def __init__(self, hold_s):
        self.hold_s = max(0.0, float(hold_s))
        self._since = None

    def update(self, ok, now=None):
        """Feed the instantaneous condition; returns True only once it
        has been continuously true for hold_s."""
        now = time.time() if now is None else now
        if not ok:
            self._since = None
            return False
        if self._since is None:
            self._since = now
        return (now - self._since) >= self.hold_s

    def pending_s(self, now=None):
        """Seconds the condition has currently been holding (0 if not)."""
        now = time.time() if now is None else now
        return 0.0 if self._since is None else now - self._since


def make_run_label(index, field_oe):
    """Run 1 at 0 Oe -> 'Run1_0Oe'; Run 2 at 5000 Oe -> 'Run2_5000Oe'.
    Order-based: the PC cannot see the field, so the tag documents what
    the PPMS sequence was TOLD to do at that point of the protocol."""
    f = float(field_oe)
    ftxt = f"{int(round(f))}" if abs(f - round(f)) < 1e-9 else f"{f:g}"
    return f"Run{index}_{ftxt}Oe"


def build_protocol_phases(cfg):
    """Expand the protocol config into the ordered phase list the worker
    executes. Each phase dict:
      kind        WAIT_BASE | TSCAN | FSCAN
      label       short name shown in the protocol table / banner
      detail      one-line human description
      expected_s  planned duration (time-guard reference; 0 = no guard)
      run         {'label','field_oe','cooldown_wait_s'[,'kind']} for
                  TSCAN / the WAIT_BASE that precedes it, None otherwise
    Cooldown waits are PER RUN (the reference sequence uses 3 h before
    the 0 Oe run but 4 h before the 5000 Oe run, whose cooldown starts
    from a just-held 310 K).
    COOL-1: a run with kind 'cool' is a STANDALONE cooldown — it
    contributes only its WAIT_BASE phase (no TSCAN follows).
    """
    phases = []
    tscan_s = 0.0
    if cfg["runs"]:
        tscan_s = ((cfg["top_temp"] - cfg["base_temp"])
                   / max(cfg["warm_rate"], 1e-9) * 60.0
                   + cfg["top_hold_s"])
    for i, run in enumerate(cfg["runs"], 1):
        if run.get("kind", "run") == "cool":
            # COOL-1: standalone cooldown — the PPMS cools to base and
            # this program only waits through it. No field tag, no
            # warming Tscan (e.g. before a manual M(H) taken while
            # heating). The wait ends exactly like every WAIT_BASE:
            # detected warming, fallback ceiling, or Skip.
            phases.append({
                "kind": "WAIT_BASE",
                "label": f"Cooldown {i} (standalone)",
                "detail": (f"PPMS cools to {cfg['base_temp']:g} K — "
                           "cooldown only, nothing is measured "
                           "afterwards (e.g. before a manual M(H) "
                           "warming run); ends when its planned wait "
                           "is over (loud beep), or sooner if warming "
                           "is detected"),
                "expected_s": run["cooldown_wait_s"],
                "run": run,
            })
            continue
        field_note = (f"field {run['field_oe']:g} Oe set at base"
                      if abs(run["field_oe"]) > 0 else "zero field")
        phases.append({
            "kind": "WAIT_BASE",
            "label": f"Cooldown {i}",
            "detail": (f"PPMS cools to {cfg['base_temp']:g} K before "
                       f"{run['label']} ({field_note})"),
            "expected_s": run["cooldown_wait_s"],
            "run": run,
        })
        phases.append({
            "kind": "TSCAN",
            "label": f"Tscan {run['label']}",
            "detail": (f"measure continuously while warming "
                       f"{cfg['base_temp']:g}->{cfg['top_temp']:g} K at "
                       f"{cfg['warm_rate']:g} K/min + "
                       f"{cfg['top_hold_s']/60.0:g} min hold"),
            "expected_s": tscan_s,
            "run": run,
        })
    if cfg["runs"] and not cfg["schedule"] \
            and cfg.get("final_cd_no_fscan"):
        # COOL-2: no Fscan section, but the protocol still ENDS at base
        # by default — after the M(T) runs a final cooldown is set, so
        # a manual M(H) taken during the next heating starts from base.
        # kind 'cool' gives it the timed end + loud completion tell.
        phases.append({
            "kind": "WAIT_BASE",
            "label": "Final cooldown",
            "detail": (f"after the last run the PPMS cools to "
                       f"{cfg['base_temp']:g} K (set by default) — "
                       "cooldown only; ends when its planned wait is "
                       "over (loud beep: start the manual M(H) heating "
                       "then)"),
            "expected_s": cfg["final_cooldown_s"],
            "run": {"label": "FinalCooldown", "field_oe": 0.0,
                    "cooldown_wait_s": cfg["final_cooldown_s"],
                    "kind": "cool"},
        })
    if cfg["schedule"]:
        phases.append({
            "kind": "WAIT_BASE",
            "label": "Final cooldown",
            "detail": f"PPMS cools to {cfg['base_temp']:g} K before "
                      "the step Fscan",
            "expected_s": cfg["final_cooldown_s"],
            "run": None,
        })
        # Expected duration = per-step WAITFORs plus the PPMS ramps
        # between setpoints (base -> first, then step to step) — waits
        # alone systematically underestimate the phase.
        sched = cfg["schedule"]
        ramp_s = sum(abs(b - a) / max(cfg["fscan_rate"], 1e-9) * 60.0
                     for a, b in zip([cfg["base_temp"]] + sched[:-1],
                                     sched))
        phases.append({
            "kind": "FSCAN",
            "label": "Step Fscan",
            "detail": (f"{len(sched)} setpoints "
                       f"{sched[0]:g}->{sched[-1]:g} K: "
                       "stabilize, then 40 Hz-2 MHz sweep"),
            "expected_s": ramp_s + sum(fscan_step_wait_s(cfg, T)
                                       for T in sched),
            "run": None,
        })
    return phases


def generate_ppms_seq(cfg):
    """Render the complete MultiVu sequence for the protocol, in the
    exact line format of the reference sequences (SeqVisualizer grammar):
        TMP TEMP <target K> <rate K/min> <mode>
        FLD FIELD <target Oe> <rate Oe/s> <approach> <mode>
        WAI WAITFOR <delay s> <T> <H> <pos> <chamber> <err>
    TMP approach modes are selectable (SEQ-1):
        cfg['tscan_approach'] (default 0, fast settle — reference
        Dielectric_Tscan.seq) for every Tscan-section TMP line;
        cfg['fscan_approach'] (default 1, no overshoot — reference
        Dielectric_Fscan.seq) for the Fscan step TMP lines.
    Field is SET at cfg['field_rate'] (reference: 50 Oe/s) and RESET at
    20 Oe/s with approach 2, exactly as the reference sequence does.
    """
    tmode = int(cfg.get("tscan_approach", 0))
    fmode = int(cfg.get("fscan_approach", 1))
    L = []
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    L.append(f"REM ==== PICA Dielectric Master protocol | "
             f"sample: {cfg.get('sample', '?')} | generated {stamp} ====")
    for i, run in enumerate(cfg["runs"], 1):
        if run.get("kind", "run") == "cool":
            # COOL-1: cooldown only — TMP to base + timed WAITFOR,
            # nothing else (the manual measurement that follows lives
            # in the user's own sequence lines).
            L.append(f"REM ------------------------ Run {i}: "
                     f"{run['label']} (cooldown only) "
                     f"------------------------")
            L.append(f"TMP TEMP {cfg['base_temp']:.6f} "
                     f"{cfg['cool_rate']:.6f} {tmode}")
            L.append(f"WAI WAITFOR {int(round(run['cooldown_wait_s']))} "
                     f"0 0 0 0 0")
            continue
        L.append(f"REM ------------------------ Run {i}: {run['label']} "
                 f"------------------------")
        L.append(f"TMP TEMP {cfg['base_temp']:.6f} {cfg['cool_rate']:.6f} "
                 f"{tmode}")
        L.append(f"WAI WAITFOR {int(round(run['cooldown_wait_s']))} "
                 f"0 0 0 0 0")
        if abs(run["field_oe"]) > 0:
            L.append(f"FLD FIELD {run['field_oe']:.1f} "
                     f"{cfg['field_rate']:.1f} 0 0")
            L.append("WAI WAITFOR 120 0 1 0 0 0")
        L.append(f"TMP TEMP {cfg['top_temp']:.6f} {cfg['warm_rate']:.6f} "
                 f"{tmode}")
        L.append(f"WAI WAITFOR {int(round(cfg['top_hold_s']))} 1 0 0 0 0")
        if abs(run["field_oe"]) > 0:
            L.append("FLD FIELD 0.0 20.0 2 0")
            L.append("WAI WAITFOR 20 0 1 0 0 0")
    if cfg["runs"] and not cfg["schedule"] \
            and cfg.get("final_cd_no_fscan"):
        # COOL-2: end at base by default (manual M(H) follows in the
        # user's own sequence lines).
        L.append("REM ------------------------ Final cooldown "
                 "(after last run) ------------------------")
        L.append(f"TMP TEMP {cfg['base_temp']:.6f} {cfg['cool_rate']:.6f} "
                 f"{tmode}")
        L.append(f"WAI WAITFOR {int(round(cfg['final_cooldown_s']))} "
                 f"0 0 0 0 0")
    if cfg["schedule"]:
        L.append("REM ------------------------ Final cooldown "
                 "------------------------")
        L.append(f"TMP TEMP {cfg['base_temp']:.6f} {cfg['cool_rate']:.6f} "
                 f"{tmode}")
        L.append(f"WAI WAITFOR {int(round(cfg['final_cooldown_s']))} "
                 f"0 0 0 0 0")
        L.append("REM ------------------------ Step Fscan "
                 "------------------------")
        for T in cfg["schedule"]:
            L.append(f"REM -- {T:g} K step --")
            L.append(f"TMP TEMP {T:.6f} {cfg['fscan_rate']:.6f} {fmode}")
            # DWELL-1: per-temperature wait floor (probe settle time
            # grows with T; a flat wait under-waits the high-T steps).
            L.append(f"WAI WAITFOR {int(round(fscan_step_wait_s(cfg, T)))} "
                     f"1 0 0 0 0")
    L.append("REM ==== Protocol end ====")
    return "\n".join(L) + "\n"


# --- SEQ-2: strict sequence validation (grammar + PPMS value ranges) ---
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
# CRYO-CON LINK HARDENING
# (copied from PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py; inlined so
#  this module stays self-contained)
# ============================================================
#
# Three failures seen on a Cryo-con Model 34 Rev 3.03A, 28-29 Aug 2026:
#
#   1. The bus scan identified the instrument, and the very next session's
#      '*IDN?' died inside viWrite with VI_ERROR_TMO. Pressing Start again
#      connected normally. A timeout on the WRITE means the instrument
#      stopped accepting bytes for a moment, not that it is absent or at
#      another address, so the cure is to wait and ask again instead of
#      giving up. Handled by CRYOCON_OPEN_SETTLE_S plus the retry loop in
#      open_cryocon_session().
#
#   2. A reading query answered with a Cryo-con status string instead of a
#      number and float() raised, which killed the worker thread. The front
#      panel shows dashes for a sensor fault and dots for a reading that is
#      inside the instrument's range but off the sensor's calibration curve;
#      over the bus those arrive as the literal strings below. A healthy
#      reply can also carry a trailing unit character, as in '77.350K',
#      which float() rejects outright. Handled by parse_cryocon_number(),
#      which names the condition instead of raising a bare ValueError.
#
#   3. The Cryocon was picked by address alone. It is at GPIB0::12 as of
#      29 Aug 2026, and the Lakeshore 350 now sits on GPIB1::12 - the
#      Cryo-con's own factory address. Selection here is by '*IDN?'
#      content, so a re-addressed Cryocon is still found and a stranger on
#      the factory address is never mistaken for one. Every measured point
#      in this program is indexed by the probe temperature, so reading it
#      off the wrong instrument is worse than not running at all.
#
# Nothing in this block writes to the instrument.

# Factory address, used only as a last-resort hint when nothing answers.
CRYOCON_ADDRESS_HINT = "GPIB0::12"
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON", "CRYO CON")

# Input channels on a Model 34.
CRYOCON_INPUT_CHANNELS = ("A", "B", "C", "D")

CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before the first command
CRYOCON_MIN_GAP_S = 0.08            # minimum gap between consecutive operations
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries

# CC34-8: in-place re-reads of ONE temperature poll. A comm glitch and a
# sensor-status reply are both usually over within a second, so both are
# retried right here before the caller ever hears about them.
CRYOCON_READ_RETRIES = 3
CRYOCON_READ_RETRY_S = 0.4

# GPIB device clear is an interface message rather than a SCPI command, but
# its effect is device-dependent and the Cryo-con guide does not document
# it, so it stays off unless turned on deliberately.
ALLOW_DEVICE_CLEAR_ON_RETRY = False

# Timeout for the identification pass, matched to the standalone GPIB
# scanner so this module does not call an instrument silent that the
# scanner reads without trouble.
IDN_SCAN_TIMEOUT_MS = 2000
PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")

# Literal replies that are status, not data.
CRYOCON_STATUS_STRINGS = {
    '-------': "sensor fault: the sensor is open, disconnected or shorted",
    '.......': ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    'N/A': "the channel is disabled, or the value does not apply",
    'NACK': "the instrument did not acknowledge the command",
}

# Leading signed decimal, with or without an exponent. Used to peel a
# trailing unit character off replies such as '77.350K'.
_CRYOCON_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')

# The dash and dot runs are as long as the display resolution setting makes
# them, so they are matched by shape rather than by a fixed seven characters.
_CRYOCON_FAULT_RE = re.compile(r'^-{2,}$')
_CRYOCON_RANGE_RE = re.compile(r'^\.{2,}$')


class CryoconStatusError(ValueError):
    """A query returned a Cryo-con status string where a number was expected."""


def parse_cryocon_number(raw, what, channel=None):
    """Turn a Cryo-con reply into a float, or say precisely why it is not one.

    Handles three things the plain float() call did not: status strings, a
    trailing unit character, and multi-channel replies, which come back as
    fields separated by semicolons.
    """
    text = str(raw).strip()
    where = f" on channel {channel}" if channel else ""
    if ';' in text:
        text = text.split(';')[0].strip()
    if text in CRYOCON_STATUS_STRINGS:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS[text]}.")
    if _CRYOCON_FAULT_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS['-------']}.")
    if _CRYOCON_RANGE_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': sensor fault, no "
            f"sensor, or {CRYOCON_STATUS_STRINGS['.......']}.")
    if not text:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned an empty reply.")
    try:
        return float(text)
    except ValueError:
        pass
    match = _CRYOCON_NUMBER_RE.match(text)
    if match:
        return float(match.group(0))
    raise CryoconStatusError(
        f"Cryocon {what}{where} returned '{text}' "
        "(sensor fault, no sensor, or reading out of range).")


def is_cryocon_idn(idn):
    """True if a '*IDN?' reply came from a Cryo-con temperature instrument."""
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


def open_cryocon_session(visa_address, log=None):
    """Open a Cryo-con session, retrying the first '*IDN?'.

    Returns (instrument, idn). Raises ConnectionError if nothing answers,
    or if what answers is not a Cryo-con: this program indexes every
    measured point by the temperature read here, so reading it off the
    wrong instrument is worse than not running at all.

    Sends nothing but '*IDN?'. In particular no *RST - on a Cryocon that
    is a ~15 s hardware reset to power-up defaults, which would drop the
    heater of whatever is actually driving the cryostat.
    """
    if pyvisa is None:
        raise ConnectionError(
            "PyVISA is not available. Install pyvisa and a VISA backend "
            "(NI-VISA or pyvisa-py).")
    say = log if callable(log) else (lambda msg: print(msg))
    rm = pyvisa.ResourceManager()
    last_error = None
    for attempt in range(1, CRYOCON_CONNECT_ATTEMPTS + 1):
        inst = None
        try:
            inst = rm.open_resource(visa_address)
            inst.timeout = CRYOCON_TIMEOUT_MS
            # The Cryocon GPIB port frames lines with EOI and no EOS
            # character, so the PyVISA termination defaults are left alone.
            time.sleep(CRYOCON_OPEN_SETTLE_S)
            if attempt > 1 and ALLOW_DEVICE_CLEAR_ON_RETRY:
                try:
                    inst.clear()
                    time.sleep(CRYOCON_OPEN_SETTLE_S)
                except Exception as exc:
                    say(f"  Device clear declined: {exc}")
            idn = inst.query('*IDN?').strip()
            if not idn:
                raise ConnectionError(
                    f"{visa_address} accepted the command but sent no "
                    "identification.")
            if not is_cryocon_idn(idn):
                inst.close()
                raise ConnectionError(
                    f"{visa_address} is not a Cryo-con: it identifies itself "
                    f"as '{idn}'. Its reply to INPUT? would be logged as a "
                    "sample temperature, so this refuses to continue. Scan "
                    "the bus and pick the Cryocon's actual address (it does "
                    f"not have to be {CRYOCON_ADDRESS_HINT}).")
            if attempt > 1:
                say(f"  Cryocon answered on attempt {attempt}.")
            return inst, idn
        except ConnectionError:
            # Wrong instrument, or a silent one. Retrying will not change
            # the answer, so let it out immediately.
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            raise
        except Exception as exc:
            last_error = exc
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            if attempt < CRYOCON_CONNECT_ATTEMPTS:
                say(f"  Cryocon did not answer at {visa_address} "
                    f"(attempt {attempt} of {CRYOCON_CONNECT_ATTEMPTS}): "
                    f"{type(exc).__name__}. Retrying in "
                    f"{CRYOCON_RETRY_WAIT_S:.1f} s.")
                time.sleep(CRYOCON_RETRY_WAIT_S)
    raise ConnectionError(
        f"No reply from a Cryo-con at {visa_address} after "
        f"{CRYOCON_CONNECT_ATTEMPTS} attempts. Last error: {last_error}. "
        "Check that the instrument is powered, that its SYS menu has "
        "RIO-Port set to GPIB rather than RS-232, and that RIO-Address "
        "matches this VISA address.")


def identify_resources(rm, resources):
    """Return {resource: idn} for every resource that answers '*IDN?'.

    Never raises: an address that is busy, silent or not SCPI simply does
    not appear in the result. Serial resources are not probed at all.
    """
    found = {}
    for res in resources:
        if not str(res).upper().startswith(PROBE_RESOURCE_PREFIXES):
            continue
        inst = None
        try:
            inst = rm.open_resource(res)
            inst.timeout = IDN_SCAN_TIMEOUT_MS
            idn = inst.query('*IDN?').strip()
            if idn:
                found[res] = idn
        except Exception:
            pass
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
                # Let the address settle before the next one is addressed.
                time.sleep(0.05)
    return found


# ============================================================
# BACKEND: Cryo-con Model 34 as READ-ONLY probe thermometer
# ============================================================
class Probe_Thermometer_Backend:
    """Strictly read-only. The only commands ever sent are '*IDN?',
    'INPUT? <ch>' and 'INPUT <ch>:UNITS?' - the PPMS owns temperature
    control entirely, and the Cryocon may be driving the cryostat for
    somebody else at the same time.

    No *RST (on a Cryocon that is a ~15 s hardware reset to power-up
    defaults), no STOP, no CONTROL, no loop, heater, range or setpoint
    command. There is deliberately no write() path on this class: a write
    path that exists is a write path that can be called by mistake.

    The interface is the one the Lakeshore backend presented - connect /
    reconnect / get_temperature / shutdown - so the worker thread below is
    byte-for-byte the parent module's. verify_channel() is the single
    addition, called once at Start.
    """

    def __init__(self):
        self.cryocon = None
        self.idn = ""
        self.visa_address = None
        self.rm = None
        self._last_io = 0.0
        self._status_reports = 0
        self.last_status_error = None   # why the last NaN was a NaN
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    # -- session handling --

    def connect(self, visa_address):
        self.visa_address = visa_address   # HARD-2: kept for reconnect()
        self.cryocon, self.idn = open_cryocon_session(visa_address)
        self._last_io = time.time()
        return self.idn

    def reconnect(self):
        """HARD-2: close and re-open the session from the stored address.
        Used by the worker's retry-forever loop after a comm failure;
        survives a controller power-cycle (still read-only afterwards, and
        still refuses to talk to anything that is not a Cryo-con)."""
        try:
            self.shutdown()
        except Exception as e:
            print(f"  Pre-reconnect cleanup warning: {e}")
        return self.connect(self.visa_address)

    def shutdown(self):
        """Close the session only. Nothing is written on the way out, so
        whatever is driving the cryostat carries on untouched."""
        if self.cryocon:
            try:
                self.cryocon.close()
            except Exception as e:
                print(f"Thermometer shutdown warning: {e}")
            finally:
                self.cryocon = None

    # -- paced, read-only I/O --

    def _query(self, command):
        """Every instrument access in this class goes through here. Rev
        3.03A firmware is slow and back-to-back traffic is what provoked
        the write timeout, hence the enforced minimum gap."""
        if self.cryocon is None:
            raise ConnectionError("Not connected to the Cryocon.")
        gap = CRYOCON_MIN_GAP_S - (time.time() - self._last_io)
        if gap > 0:
            time.sleep(gap)
        try:
            return self.cryocon.query(command).strip()
        finally:
            self._last_io = time.time()

    def verify_channel(self, channel):
        """Confirm the chosen input reads Kelvin and has a live sensor.

        INPUT? reports in each channel's own display units, so a channel
        left in C or F would silently log wrong numbers against every data
        point of a multi-day run. Both checks run once at Start, which is
        attended by definition: a wrong or empty channel is named here, by
        letter, instead of poisoning the whole protocol. Returns the
        current reading in K.
        """
        ch = str(channel).strip().upper()
        units = self._query(f"INPUT {ch}:UNITS?").upper()
        if not units.startswith("K"):
            raise ValueError(
                f"Cryocon channel {ch} is reporting in '{units}', not "
                "Kelvin. Either set that channel to K on the Cryocon front "
                "panel (this program never writes to it), or pick the "
                "channel the probe sensor is actually on.")
        return parse_cryocon_number(
            self._query(f"INPUT? {ch}"), "temperature reading", ch)

    def get_temperature(self, channel, retries=CRYOCON_READ_RETRIES):
        """Read one temperature, retrying a transient fault in place.

        Two different things can spoil a single poll, and BOTH usually
        clear within a second, so both are retried here before the caller
        ever hears about them (CC34-8):

          - a comm glitch: a VISA timeout on a slow Rev 3.03A bus;
          - a STATUS reply instead of a number: dashes while an input
            range switches, dots for a reading that has briefly wandered
            off the sensor's calibration curve.

        After the retry budget the two part company. A status reply is NOT
        a comm error - the instrument answered, the sensor did not - and
        reconnecting cannot fix it, so it returns NaN rather than raising.
        The worker then keeps re-reading (_retry_invalid_reading) and, past
        that, retries on every later poll for as long as the run lasts, so
        recovery stays automatic without a reconnect loop spinning on a
        fault no reconnect can cure. A genuine comm failure still raises,
        and the worker's retry-forever reconnect loop takes it (HARD-2).
        """
        ch = str(channel).strip().upper()
        last_err = None
        for attempt in range(retries + 1):
            try:
                raw = self._query(f"INPUT? {ch}")
            except Exception as e:
                last_err = e
                if attempt < retries:
                    print(f"get_temperature retry {attempt+1}: {e}")
                    if ALLOW_DEVICE_CLEAR_ON_RETRY:
                        try:
                            self.cryocon.clear()
                        except Exception:
                            pass
                    time.sleep(CRYOCON_READ_RETRY_S)
                continue
            try:
                return parse_cryocon_number(raw, "temperature reading", ch)
            except CryoconStatusError as e:
                last_err = e
                self.last_status_error = str(e)
                if attempt < retries:
                    # Retried exactly like a comm glitch: an input range
                    # switch shows dashes for a moment and then reads
                    # normally, and that must not cost a data point.
                    time.sleep(CRYOCON_READ_RETRY_S)
                    continue
                self._status_reports += 1
                if (self._status_reports <= 5
                        or self._status_reports % 25 == 0):
                    print(f"Cryocon status reply "
                          f"#{self._status_reports}: {e}")
                return float("nan")
        raise last_err


# ============================================================
# BACKEND: Keysight E4980A
# (copied from PPMS_Sync_Freq_Scan_E4980A_GUI.py)
# ============================================================
class LCR_Backend:
    # Fscan per-setpoint files (frequency sweep at fixed T).
    # DATA-1 (2026-07-24, from the Sync GUI): the last column is the
    # MEASURED probe temperature per row; the commanded setpoint lives
    # only in the '#' header — a label can never silently desync.
    FSCAN_DATA_HEADER = (
        "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\t"
        "R(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''\tT_sample(K)"
    )
    # Tscan per-frequency files (legacy 19-column format of
    # Temprature_Scan_Passive_E4980A_GUI.py):
    TSCAN_DATA_HEADER = (
        "Temperature\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\t"
        "theta\tchi\tR(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\t"
        "Cp''\tCs''"
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
        # 15 s: LONG-aperture point time is < 1 s, so this is generous
        # while letting the retry loop detect a hung bus quickly.
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
        """Close and fully re-initialize from the stored params.
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
# FRONTEND: PPMS Dielectric Master GUI
# ============================================================
class PPMSMasterGUI:
    PROGRAM_VERSION = "1.5-CC34"   # Cryo-con 34 sibling of v1.5
    LEFT_PANEL_WIDTH = 500

    # SetThreadExecutionState flags (Windows keep-awake during a run)
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    # Throttled-redraw tuning knobs (FRZ pattern)
    REDRAW_MS = 750
    MAX_PLOT_POINTS = 4000
    MAX_MSGS_PER_CYCLE = 300

    # Theme (identical to the parent programs)
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

    # Default Tscan frequency list (same as the passive program)
    DEFAULT_TSCAN_FREQS = (
        "1000, 2000, 3000, 5000, 7000, 10000, 25000, 50000, "
        "70000, 90000, 100000, 120000, 150000, 170000, 200000, "
        "250000, 500000, 1000000, 1500000, 2000000"
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
            "PPMS Dielectric Master (Cryo-con 34): Tscan + Fscan "
            f"v{self.PROGRAM_VERSION}"
        )
        self.root.geometry("1750x980")
        self.root.minsize(1450, 870)
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

        # Fscan frequency array (fixed, as in the parent: 40 Hz to 2 MHz)
        self.sweep_frequencies = np.concatenate([
            np.arange(40, 1000, 10),
            np.arange(1000, 10000, 100),
            np.arange(10000, 100000, 1000),
            np.arange(100000, 1000000, 10000),
            np.arange(1000000, 2000001, 100000),
        ])

        # Passive-run rows (editable pre-run):
        # {'field_oe': float, 'cooldown': 'h:mm or min' string}
        self.run_rows = [
            {"field_oe": 0.0, "cooldown": "3:00"},
            {"field_oe": 5000.0, "cooldown": "4:00"},
        ]

        # Plot data — main thread only
        self.plot_t = []
        self.plot_temp = []
        self.plot_target = []
        self.meas_t = []
        self.meas_temp = []
        # Right-column data. fscan mode: current sweep (x = frequency).
        self.scan_f = []
        self.scan_cp = []
        self.scan_g = []
        # tscan mode: per-frequency histories (x = temperature).
        self.ts_store = {}
        self.plot_freq = None
        self._plot_mode = None        # None | 'tscan' | 'fscan'
        self._plot_mode_label = ""

        # Dirty flags — redraw happens only in _redraw_tick
        self._temp_plot_dirty = False
        self._freq_plot_dirty = False
        self._pending_progress = None

        # Pause/skip + band-patch state
        self._paused = False           # worker-only write
        self._skip_requested = False   # worker-only write
        # Fscan-only: abandon the CURRENT temperature step (stability
        # wait or running sweep) and move to the next setpoint.
        self._skip_step_requested = False   # worker-only write
        self._band_params = None
        self._band_dirty = False
        self.band_patch = None

        self.y_scale_var = tk.StringVar(value="auto")
        self._decade_ylims = {}

        # Fscan spectrum label state — ("measuring"|"done", T_set) or None
        self._scan_info = None

        # Worker-side markers
        self._worker_phase = None
        self._phase_index = -1

        # Scan-time estimate: measured mean per-point time replaces the
        # analytic model after the first completed sweep.
        self._measured_point_s = None
        self._scan_est_s = 0.0

        # Durable-write buffer (worker thread only)
        self._pending_rows = deque()
        self._write_error_logged = False
        self.tlog_path = None
        self.timing_path = None
        self.protolog_path = None
        self.runstate_path = None   # crash/restart aid (worker thread)

        # Protocol run state (set at Start)
        self.cfg = None
        self.protocol_phases = []
        self.schedule = []

        # UI bookkeeping for set_ui_state
        self._prerun_simple = []   # entries/buttons: normal <-> disabled
        self._prerun_combos = []   # comboboxes: readonly <-> disabled
        self.entries = {}          # stability entries (lock pattern)

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(self.REDRAW_MS, self._redraw_tick)
        self._update_scan_estimate()
        self.log(f"PPMS Dielectric Master (CC34) v{self.PROGRAM_VERSION} "
                 "initialized. Cryo-con Model 34 is READ-ONLY (INPUT? "
                 "only) — the PPMS owns all temperature and field "
                 "control.")
        self.log("Protocol: per run — cooldown (idle) then continuous "
                 "warming Tscan; after the last run — final cooldown then "
                 "temperature-step Fscan. All transitions are inferred "
                 "from the probe temperature.")
        self.log("Use the 'Protocol & PPMS Sequence' tab to preview the "
                 "phase plan and generate the matching MultiVu .seq file "
                 "BEFORE starting the PPMS sequence.")

    # ------------------------------------------------------------
    # Styling (unchanged from the parents)
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
        ttk.Label(header, text="PPMS Dielectric Master: Tscan + Fscan "
                               "(passive, sequence-synchronized)",
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
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

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
        sf.grid_rowconfigure(7, weight=1)

        self._create_info_panel(sf, 0)
        self._create_protocol_panel(sf, 1)
        self._create_detection_panel(sf, 2)
        self._create_schedule_panel(sf, 3)
        self._create_stability_panel(sf, 4)
        self._create_thermo_panel(sf, 5)
        self._create_lcr_settings_panel(sf, 6)
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

    # ------------------------------------------------------------
    # Protocol panel: passive runs + PPMS ramp parameters
    # ------------------------------------------------------------
    def _create_protocol_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Protocol: passive warming "
                                             "runs (Tscan)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)
        self.proto = {}

        lf = ttk.Frame(frame)
        lf.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=10, pady=5)
        sb = ttk.Scrollbar(lf, orient="vertical")
        self.runs_lb = tk.Listbox(lf, height=3, selectmode=tk.EXTENDED,
                                  font=self.FONT_BASE, bg=self.CLR_INPUT_BG,
                                  fg=self.CLR_TEXT_DARK,
                                  yscrollcommand=sb.set)
        sb.config(command=self.runs_lb.yview)
        self.runs_lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(frame, text="Field (Oe):").grid(row=1, column=0,
                                                  sticky="e", padx=2)
        self.run_field_entry = ttk.Entry(frame, width=8)
        self.run_field_entry.grid(row=1, column=1, sticky="w", padx=2)
        self._prerun_simple.append(self.run_field_entry)
        ttk.Label(frame, text="Cooldown (h:mm):").grid(row=1, column=2,
                                                       sticky="e", padx=2)
        self.run_cool_entry = ttk.Entry(frame, width=8)
        self.run_cool_entry.insert(0, "3:00")
        self.run_cool_entry.grid(row=1, column=3, sticky="w", padx=2)
        self._prerun_simple.append(self.run_cool_entry)

        b = ttk.Button(frame, text="Add Run", command=self._add_run)
        b.grid(row=2, column=0, sticky="ew", padx=(10, 2), pady=2)
        self._prerun_simple.append(b)
        # COOL-1: a standalone cooldown row (no warming measurement).
        b = ttk.Button(frame, text="Add Cooldown Only",
                       command=self._add_cooldown_only)
        b.grid(row=2, column=1, columnspan=2, sticky="ew", padx=2, pady=2)
        self._prerun_simple.append(b)
        b = ttk.Button(frame, text="Remove Selected",
                       command=self._remove_run)
        b.grid(row=2, column=3, sticky="ew", padx=(2, 10), pady=2)
        self._prerun_simple.append(b)

        ttk.Label(frame,
                  text="Field values are FILE TAGS ONLY (this PC cannot "
                       "see the PPMS field) — run order must match the "
                       "PPMS sequence. Cooldown = the WAITFOR the "
                       "sequence uses before that run's warming. "
                       "'Add Cooldown Only' adds just a cooldown to base "
                       "with NO measurement after it — e.g. before taking "
                       "M(H) manually during warming.",
                  font=("Segoe UI", 8, "italic"), wraplength=440
                  ).grid(row=3, column=0, columnspan=4, sticky="w",
                         padx=10, pady=(0, 4))

        ttk.Separator(frame, orient="horizontal").grid(
            row=4, column=0, columnspan=4, sticky="ew", pady=4, padx=10)

        self._add_proto_entry(frame, "Base T (K):", "base_temp", "10", 5, 0)
        self._add_proto_entry(frame, "Top T (K):", "top_temp", "310", 5, 2)
        self._add_proto_entry(frame, "Warm rate (K/min):", "warm_rate",
                              "1", 6, 0)
        self._add_proto_entry(frame, "Cool rate (K/min):", "cool_rate",
                              "3", 6, 2)
        self._add_proto_entry(frame, "Hold at top (min):", "top_hold",
                              "30", 7, 0)
        self._add_proto_entry(frame, "Final cooldown (h:mm):",
                              "final_cooldown", "3:00", 7, 2)
        self._add_proto_entry(frame, "Field set rate (Oe/s):", "field_rate",
                              "50", 8, 0)
        # RATE-2 (Co-07 forensic): the probe follows the block at only
        # ~0.3-0.5 K/min above 120 K — 1 K/min just built lag that the
        # step wait then had to absorb. Editable per protocol as always.
        self._add_proto_entry(frame, "Fscan ramp (K/min):", "fscan_rate",
                              "0.5", 8, 2)

        # COOL-2: after the M(T) warming runs a final cooldown is SET BY
        # DEFAULT even when there is no Fscan section — so a manual
        # M(H)-during-heating can start from base without extra clicks.
        self.var_final_cd_always = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(
            frame,
            text="After the last run: final cooldown to base (default ON "
                 "— e.g. ready for M(H) taken during the next heating)",
            variable=self.var_final_cd_always)
        cb.grid(row=9, column=0, columnspan=4, sticky="w",
                padx=10, pady=(4, 0))
        self._prerun_simple.append(cb)
        # COOL-2: toggling the checkbox or editing the duration redraws
        # the run list so its "then: FINAL COOLDOWN" line stays honest.
        self.var_final_cd_always.trace_add(
            "write", lambda *a: self._runs_render())
        self.proto["final_cooldown"].bind(
            "<KeyRelease>", lambda e: self._runs_render())

        ttk.Label(frame, text="Tscan frequencies (Hz, comma-separated):"
                  ).grid(row=10, column=0, columnspan=4, sticky="w",
                         padx=10, pady=(6, 0))
        self.tscan_freq_text = tk.Text(frame, font=self.FONT_BASE,
                                       height=3, wrap="word",
                                       bg=self.CLR_INPUT_BG,
                                       fg=self.CLR_TEXT_DARK)
        self.tscan_freq_text.insert("1.0", self.DEFAULT_TSCAN_FREQS)
        self.tscan_freq_text.grid(row=11, column=0, columnspan=4,
                                  sticky="ew", padx=10, pady=(0, 4))

        ttk.Label(frame, text="Tscan live-plot frequency:").grid(
            row=12, column=0, columnspan=2, sticky="w", padx=10)
        self.plot_freq_cb = ttk.Combobox(frame, state="disabled", width=12)
        self.plot_freq_cb.grid(row=12, column=2, columnspan=2, sticky="ew",
                               padx=10, pady=(0, 6))
        self.plot_freq_cb.bind("<<ComboboxSelected>>",
                               self._on_plot_freq_change)

        # SEQ-1: TMP approach modes for the generated PPMS sequence.
        # SEQ-3: BOTH default to fast-settle (0) (user decision
        # 2026-07-19); the reference Fscan's no-overshoot (1) stays
        # selectable.
        APPROACHES = ["Fast settle (0)", "No overshoot (1)"]
        ttk.Label(frame, text="Tscan TMP approach:").grid(
            row=13, column=0, sticky="w", padx=(10, 2), pady=2)
        self.tscan_mode_cb = ttk.Combobox(frame, values=APPROACHES,
                                          state="readonly", width=14)
        self.tscan_mode_cb.set(APPROACHES[0])
        self.tscan_mode_cb.grid(row=13, column=1, sticky="ew",
                                padx=(2, 10), pady=2)
        self._prerun_combos.append(self.tscan_mode_cb)
        ttk.Label(frame, text="Fscan TMP approach:").grid(
            row=13, column=2, sticky="w", padx=(10, 2), pady=2)
        self.fscan_mode_cb = ttk.Combobox(frame, values=APPROACHES,
                                          state="readonly", width=14)
        self.fscan_mode_cb.set(APPROACHES[0])
        self.fscan_mode_cb.grid(row=13, column=3, sticky="ew",
                                padx=(2, 10), pady=(2, 6))
        self._prerun_combos.append(self.fscan_mode_cb)

        self._runs_render()

    def _add_proto_entry(self, frame, label, key, default, r, c):
        ttk.Label(frame, text=label).grid(row=r, column=c, sticky="w",
                                          padx=(10, 2), pady=2)
        e = ttk.Entry(frame, font=self.FONT_BASE, width=8)
        e.grid(row=r, column=c + 1, sticky="ew", padx=(2, 10), pady=2)
        e.insert(0, default)
        self.proto[key] = e
        self._prerun_simple.append(e)
        return e

    def _runs_render(self):
        self.runs_lb.delete(0, tk.END)
        for i, r in enumerate(self.run_rows, 1):
            if r.get("kind", "run") == "cool":
                self.runs_lb.insert(
                    tk.END,
                    f"Run {i}:  COOLDOWN ONLY   "
                    f"(cooldown {r['cooldown']})   ->  no measurement")
            else:
                label = make_run_label(i, r["field_oe"])
                self.runs_lb.insert(
                    tk.END,
                    f"Run {i}:  {r['field_oe']:g} Oe   "
                    f"(cooldown {r['cooldown']})   ->  {label}")
        # COOL-2: the default final cooldown is part of the protocol, so
        # LIST it explicitly — the user must see what happens after the
        # last run without having to remember the checkbox. Info line
        # only (not in run_rows; Remove Selected ignores it).
        if self.run_rows and getattr(self, "var_final_cd_always", None) \
                and self.var_final_cd_always.get():
            try:
                dur = self.proto["final_cooldown"].get().strip() or "?"
            except (KeyError, tk.TclError):
                dur = "?"
            self.runs_lb.insert(
                tk.END,
                f"then (default):  FINAL COOLDOWN to base ({dur})   "
                "->  e.g. take M(H) while heating")

    def _add_run(self):
        try:
            field = float(self.run_field_entry.get())
        except ValueError:
            messagebox.showerror("Protocol", "Field must be a number (Oe).")
            return
        cool = self.run_cool_entry.get().strip() or "3:00"
        try:
            if parse_duration_min(cool) < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Protocol",
                                 "Cooldown must be minutes or h:mm.")
            return
        self.run_rows.append({"field_oe": field, "cooldown": cool})
        self.run_field_entry.delete(0, tk.END)
        self._runs_render()

    def _add_cooldown_only(self):
        """COOL-1: append a standalone cooldown — the PPMS cools to base
        and this program waits through it, then moves on. No field tag,
        no Tscan (used e.g. before taking M(H) manually while heating).
        Uses the same Cooldown (h:mm) entry as Add Run."""
        cool = self.run_cool_entry.get().strip() or "3:00"
        try:
            if parse_duration_min(cool) < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Protocol",
                                 "Cooldown must be minutes or h:mm.")
            return
        self.run_rows.append({"field_oe": 0.0, "cooldown": cool,
                              "kind": "cool"})
        self._runs_render()

    def _remove_run(self):
        sel = sorted(self.runs_lb.curselection(), reverse=True)
        for i in sel:
            if 0 <= i < len(self.run_rows):
                del self.run_rows[i]
        self._runs_render()

    # ------------------------------------------------------------
    # Phase-detection thresholds (advanced)
    # ------------------------------------------------------------
    def _create_detection_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Phase detection (advanced)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)
        self.det = {}

        def add(label, key, default, r, c):
            ttk.Label(frame, text=label).grid(row=r, column=c, sticky="w",
                                              padx=(10, 2), pady=2)
            e = ttk.Entry(frame, font=self.FONT_BASE, width=7)
            e.grid(row=r, column=c + 1, sticky="ew", padx=(2, 10), pady=2)
            e.insert(0, default)
            self.det[key] = e
            self._prerun_simple.append(e)

        add("Base arm (K):", "base_arm", "30", 0, 0)
        add("Rise (K):", "rise_k", "2", 0, 2)
        add("Top arm offset (K):", "top_arm_off", "10", 1, 0)
        add("Fall (K):", "fall_k", "2", 1, 2)
        add("Confirm (min):", "confirm_min", "3", 2, 0)
        add("Overdue warn (min):", "overdue_min", "20", 2, 2)
        # Fallback ceiling for cooldown waits (user decision 2026-07-17:
        # ON by default at 2x) — a missed warming detection must never
        # strand the whole holiday run in WAIT_BASE.
        add("Fallback (×expected, 0=off):", "fallback_x", "2", 3, 0)
        ttk.Label(frame,
                  text="Cooldown ends: T dipped below Base arm, then rose "
                       "'Rise' K off its minimum. Warming run ends: T got "
                       "within 'Top arm offset' of Top T, then fell 'Fall' "
                       "K off its maximum. Median-of-5 filtered, and the "
                       "turnaround must HOLD for 'Confirm' minutes before "
                       "the phase actually switches — the master only "
                       "moves on when it is definitely sure. Overdue "
                       "phases warn loudly but NEVER abort. Fallback: a "
                       "cooldown wait additionally ends (loudly flagged) "
                       "at Fallback × its expected time, so a missed "
                       "detection can never strand the run. Note: charging "
                       "the field at base can warm the probe a few K and "
                       "fire warming-detection slightly early — harmless, "
                       "the Tscan just starts with a few extra rows at "
                       "base.",
                  font=("Segoe UI", 8, "italic"), wraplength=440
                  ).grid(row=4, column=0, columnspan=4, sticky="w",
                         padx=10, pady=(0, 5))

    # ------------------------------------------------------------
    # Fscan temperature schedule (copied from PPMS_Sync)
    # ------------------------------------------------------------
    def _create_schedule_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Fscan temperature schedule "
                                             "(step scans after the last "
                                             "run)")
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
        for e in (self.entry_start, self.entry_end, self.entry_step):
            self._prerun_simple.append(e)
        b = ttk.Button(frame, text="Generate Steps", command=self._generate_steps)
        b.grid(row=2, column=2, columnspan=2, sticky="ew", padx=5, pady=2)
        self._prerun_simple.append(b)

        ttk.Label(frame, text="Order:").grid(row=3, column=0, sticky="e", padx=2)
        self.sort_var = tk.StringVar(value="Ascending")
        self.sort_cb = ttk.Combobox(frame, textvariable=self.sort_var,
                                    values=["Ascending", "Descending"],
                                    state="readonly", width=10)
        self.sort_cb.grid(row=3, column=1, sticky="w", padx=2)
        self.sort_cb.bind("<<ComboboxSelected>>", lambda e: self._sort_listbox())
        self._prerun_combos.append(self.sort_cb)

        ttk.Label(frame, text="Manual(K):").grid(row=4, column=0, sticky="e",
                                                 padx=2, pady=5)
        self.entry_manual = ttk.Entry(frame, width=6)
        self.entry_manual.grid(row=4, column=1, sticky="w", padx=2, pady=5)
        self._prerun_simple.append(self.entry_manual)
        b = ttk.Button(frame, text="Add", command=self._add_manual_step)
        b.grid(row=4, column=2, sticky="ew", padx=2, pady=5)
        self._prerun_simple.append(b)
        b = ttk.Button(frame, text="Remove", command=self._remove_step)
        b.grid(row=4, column=3, sticky="ew", padx=2, pady=5)
        self._prerun_simple.append(b)
        b = ttk.Button(frame, text="Clear All", command=self._clear_listbox)
        b.grid(row=5, column=0, columnspan=4, sticky="ew", padx=10,
               pady=(0, 5))
        self._prerun_simple.append(b)

        self.scan_est_lbl = ttk.Label(frame, text="Scan estimate: —",
                                      font=("Segoe UI", 10, "bold"),
                                      foreground=self.CLR_ACCENT_GOLD,
                                      background=self.CLR_FRAME_BG)
        self.scan_est_lbl.grid(row=6, column=0, columnspan=4, sticky="w",
                               padx=10, pady=(2, 5))

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
    # Stabilization criteria (copied from PPMS_Sync)
    # ------------------------------------------------------------
    def _create_stability_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Fscan stabilization criteria "
                                             "(sample thermometer)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in (1, 4) else 0)

        self.stab_mode_var = tk.StringVar(value="flat_band")
        self.stab_mode_radios = []
        for c, (value, label) in enumerate(self.STAB_MODES):
            rb = ttk.Radiobutton(frame, text=label,
                                 variable=self.stab_mode_var, value=value,
                                 command=self._on_stab_mode_changed)
            rb.grid(row=0 if c < 2 else 1, column=(c % 2) * 3,
                    columnspan=3, sticky="w", padx=10, pady=(5, 0))
            self.stab_mode_radios.append(rb)

        # TOL-2: base default 0.4 = the table's high-T floor, so table
        # ON/OFF agree above 100 K.
        self._create_grid_entry(frame, "Tolerance (±K):", "tol", "0.4", 2, 0)
        self._create_grid_entry(frame, "Window (min):", "window_min", "10", 2, 3)
        self._create_grid_entry(frame, "Drift Lim (K/min):", "drift", "0.05", 3, 0)
        self._create_grid_entry(frame, "Target guard (±K, 0=off):",
                                "guard", "2.0", 3, 3)
        # TIME-1 (Co-07 forensic, from the Sync GUI): the timeout must
        # stay below one PPMS step period (ramp + WAITFOR) minus the
        # scan — 90 min let the scanner fall multiple steps behind.
        self._create_grid_entry(frame, "Timeout (min, 0=off):",
                                "stab_timeout", "35", 4, 0)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "2", 4, 3)
        self._create_grid_entry(frame, "Margin (min):", "margin_min",
                                "10", 5, 0)

        # TOL-1: temperature-dependent tolerance table (ON by default —
        # the probe settles above the PPMS setpoint at low T, so a single
        # tolerance cannot serve 30 K and 300 K at once).
        self.var_tol_table = tk.BooleanVar(value=True)
        self.chk_tol_table = ttk.Checkbutton(
            frame, text="Tolerance varies with T:",
            variable=self.var_tol_table)
        self.chk_tol_table.grid(row=6, column=0, columnspan=3,
                                sticky="w", padx=10, pady=(4, 0))
        self._prerun_simple.append(self.chk_tol_table)
        self.tol_preset_cb = ttk.Combobox(
            frame, state="readonly", width=18,
            values=list(TOL_TABLE_PRESETS) + ["Custom"])
        self.tol_preset_cb.set("Safe (recommended)")
        self.tol_preset_cb.grid(row=6, column=3, columnspan=3,
                                sticky="ew", padx=(2, 10), pady=(4, 0))
        self.tol_preset_cb.bind("<<ComboboxSelected>>",
                                self._on_tol_preset)
        self._prerun_combos.append(self.tol_preset_cb)
        self.tol_table_entry = ttk.Entry(frame, font=self.FONT_BASE)
        self.tol_table_entry.insert(
            0, TOL_TABLE_PRESETS["Safe (recommended)"])
        self.tol_table_entry.grid(row=7, column=0, columnspan=6,
                                  sticky="ew", padx=10, pady=2)
        self.tol_table_entry.bind(
            "<KeyRelease>", lambda e: self.tol_preset_cb.set("Custom"))
        self._prerun_simple.append(self.tol_table_entry)

        # Live preview: the EFFECTIVE tolerance at every Fscan schedule
        # setpoint (or example temperatures) — 20 K, 25 K, anything
        # between table entries is spelled out, never left implicit.
        self.tol_preview_lbl = ttk.Label(
            frame, text="—", font=("Segoe UI", 8), wraplength=440,
            foreground="#2A6B3A", justify="left")
        self.tol_preview_lbl.grid(row=8, column=0, columnspan=6,
                                  sticky="w", padx=10, pady=(0, 2))

        ttk.Label(frame,
                  text="Flatness: peak-to-peak ≤ 2×Tol over the window, "
                       "any offset from target. Guard rejects 'stable at "
                       "the wrong setpoint'. Poll Delay is also the "
                       "temperature poll for cooldown phases. Margin pads "
                       "the generated PPMS step waits. Table: T:tol "
                       "pairs — BETWEEN entries the tolerance is linearly "
                       "interpolated (e.g. 35 K, halfway 30→40, gives "
                       "±1.35); above the top entry the last value holds; "
                       "below the lowest it is slope-extrapolated (cap "
                       "3 K). Effective Tol = max(base Tol, table) — read "
                       "at Start. Drift limit is unaffected.",
                  font=("Segoe UI", 8, "italic"), wraplength=440
                  ).grid(row=9, column=0, columnspan=6, sticky="w",
                         padx=10, pady=(0, 2))

        # RESYNC-1 (from Sync v1.8): jump the step counter forward on a
        # diverged timeout (OFF by default — new behavior is opt-in).
        self.var_resync = tk.BooleanVar(value=False)
        self.chk_resync = ttk.Checkbutton(
            frame, text="Re-sync to PPMS on timeout (jump to the later "
                        "setpoint the sample actually sits at)",
            variable=self.var_resync)
        self.chk_resync.grid(row=10, column=0, columnspan=6,
                             sticky="w", padx=10, pady=(4, 0))
        self._prerun_simple.append(self.chk_resync)

        # AGNOS-1 (from Sync v1.8): setpoint-agnostic Fscan phase (OFF
        # by default). The generated .seq still steps the schedule; the
        # SCANNER stops trusting labels — it scans each detected plateau
        # and labels it with the measured median. Completes after as
        # many plateaus as schedule entries.
        self.var_agnostic = tk.BooleanVar(value=False)
        self.chk_agnostic = ttk.Checkbutton(
            frame, text="Setpoint-agnostic Fscan: scan every plateau the "
                        "PPMS makes (label = measured median; ends after "
                        "one plateau per schedule entry).  Min ΔT (K):",
            variable=self.var_agnostic)
        self.chk_agnostic.grid(row=11, column=0, columnspan=5,
                               sticky="w", padx=10, pady=(2, 0))
        self._prerun_simple.append(self.chk_agnostic)
        self.agn_dt_entry = ttk.Entry(frame, width=6)
        self.agn_dt_entry.insert(0, "2.0")
        self.agn_dt_entry.grid(row=11, column=5, sticky="w",
                               padx=(2, 10), pady=(2, 0))
        self._prerun_simple.append(self.agn_dt_entry)

        # DWELL-1 (from Sync v1.8): per-temperature floor for the
        # generated Fscan WAITFORs — Co-07 showed probe settle time
        # GROWS with T while a single step wait is flat.
        self.var_dwell_table = tk.BooleanVar(value=True)
        self.chk_dwell_table = ttk.Checkbutton(
            frame, text="Fscan PPMS wait varies with T (T:min pairs — "
                        "floor under the computed step wait):",
            variable=self.var_dwell_table)
        self.chk_dwell_table.grid(row=12, column=0, columnspan=4,
                                  sticky="w", padx=10, pady=(2, 0))
        self._prerun_simple.append(self.chk_dwell_table)
        self.dwell_table_entry = ttk.Entry(frame, width=24)
        self.dwell_table_entry.insert(0, DWELL_TABLE_DEFAULT)
        self.dwell_table_entry.grid(row=12, column=4, columnspan=2,
                                    sticky="ew", padx=(2, 10), pady=(2, 0))
        self._prerun_simple.append(self.dwell_table_entry)

        b = ttk.Button(frame, text="Apply Live Updates",
                       command=self._send_live_updates)
        b.grid(row=13, column=0, columnspan=6, sticky="ew",
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
        current table, base Tolerance and Fscan schedule."""
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
            prefix = "At your Fscan setpoints:  "
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
    # Thermometer + LCR panels (copied from PPMS_Sync)
    # ------------------------------------------------------------
    def _create_thermo_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Probe Thermometer "
                                             "(Cryo-con Model 34, READ-ONLY)")
        frame.grid(row=row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        ttk.Label(frame, text="Cryocon VISA:").grid(row=0, column=0,
                                                    sticky="w",
                                                    padx=10, pady=5)
        self.ls_cb = ttk.Combobox(frame, state="readonly", width=18)
        self.ls_cb.grid(row=0, column=1, columnspan=2, sticky="ew", padx=5)
        self._prerun_combos.append(self.ls_cb)
        ttk.Label(frame, text="Input Ch:").grid(row=1, column=0, sticky="w",
                                                padx=10, pady=5)
        self.channel_cb = ttk.Combobox(frame, values=["A", "B", "C", "D"],
                                       state="readonly", width=4)
        self.channel_cb.set("A")
        self.channel_cb.grid(row=1, column=1, sticky="w", padx=5)
        self._prerun_combos.append(self.channel_cb)
        ttk.Label(frame,
                  text="Only *IDN?/INPUT?/UNITS? are ever sent — no STOP, "
                       "CONTROL, loop, heater or setpoint command exists "
                       "in this program.",
                  font=("Segoe UI", 8, "italic"), wraplength=440
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
        self._prerun_combos.append(self.aper_cb)
        self.lcr_entries["delay"].bind("<KeyRelease>",
                                       lambda e: self._update_scan_estimate())

        ttk.Label(frame, text="Cable (m):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.cable_cb = ttk.Combobox(frame, values=["0", "1", "2", "4"], state="readonly", width=4)
        self.cable_cb.set("1")
        self.cable_cb.grid(row=3, column=1, sticky="w", padx=5, pady=2)
        self._prerun_combos.append(self.cable_cb)
        ttk.Label(frame, text="LCR VISA:").grid(row=3, column=2, sticky="w", padx=5, pady=2)
        self.lcr_cb = ttk.Combobox(frame, state="readonly", width=28)
        self.lcr_cb.grid(row=3, column=3, sticky="ew", padx=5, pady=2)
        self._prerun_combos.append(self.lcr_cb)

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(frame, text="ALC", variable=self.var_alc)
        cb.grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=2)
        self._prerun_simple.append(cb)
        cb = ttk.Checkbutton(frame, text="Open/Short Corr",
                             variable=self.var_corr)
        cb.grid(row=4, column=2, columnspan=2, sticky="w", padx=5, pady=2)
        self._prerun_simple.append(cb)

        bf = ttk.Frame(frame); bf.grid(row=5, column=0, columnspan=4, sticky="ew", pady=5, padx=5)
        bf.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(bf, text="Start Protocol",
                                       style="Start.TButton",
                                       command=self.start_protocol)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.stop_button = ttk.Button(bf, text="Stop All",
                                      style="Stop.TButton", state="disabled",
                                      command=self.stop_protocol)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=2)
        b = ttk.Button(bf, text="Scan VISA", command=self._scan_for_visa)
        b.grid(row=0, column=2, sticky="ew", padx=2)
        self._prerun_simple.append(b)
        self.pause_button = ttk.Button(bf, text="Pause", state="disabled",
                                       command=self._toggle_pause)
        self.pause_button.grid(row=1, column=0, sticky="ew", padx=2, pady=(4, 0))
        self.skip_button = ttk.Button(bf, text="Skip Phase", state="disabled",
                                      command=self._skip_step)
        self.skip_button.grid(row=1, column=1, sticky="ew", padx=2, pady=(4, 0))
        self.skip_freq_button = ttk.Button(bf, text="Skip Freq Step",
                                           state="disabled",
                                           command=self._skip_freq_step)
        self.skip_freq_button.grid(row=1, column=2, sticky="ew", padx=2,
                                   pady=(4, 0))

        b = ttk.Button(frame, text="Browse Save…", command=self._browse_save)
        b.grid(row=6, column=0, columnspan=4, sticky="ew", padx=5, pady=(0, 5))
        self._prerun_simple.append(b)
        self.save_dir_lbl = ttk.Label(frame, text="Save dir: (not set)",
                                      foreground=self.CLR_ACCENT_GOLD)
        self.save_dir_lbl.grid(row=7, column=0, columnspan=4, sticky="w", padx=5)

    def _create_console_panel(self, parent, row):
        frame = ttk.LabelFrame(parent, text="Console Log")
        frame.grid(row=row, column=0, sticky="nsew", pady=5, padx=5)
        frame.grid_rowconfigure(0, weight=1); frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(frame, state="disabled",
                                                 bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
                                                 font=self.FONT_CONSOLE, wrap="word", borderwidth=0)
        self.console.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    # ------------------------------------------------------------
    # Right panel: status banner + plots + protocol/sequence tab
    # ------------------------------------------------------------
    def _populate_right(self, panel):
        panel.grid_rowconfigure(1, weight=1); panel.grid_columnconfigure(0, weight=1)

        sf = ttk.Frame(panel); sf.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        sf.grid_columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(sf, text="READY — CONFIGURE PROTOCOL, "
                                            "THEN START",
                                   font=("Segoe UI", 15, "bold"),
                                   bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK,
                                   pady=8)
        self.lbl_status.grid(row=0, column=0, sticky="ew")
        self.lbl_temp_now = tk.Label(sf, text="--- K",
                                     font=("Consolas", 18, "bold"),
                                     bg=self.CLR_HEADER,
                                     fg=self.CLR_TEXT_DARK, padx=16, pady=6)
        self.lbl_temp_now.grid(row=0, column=1, sticky="nse", padx=(8, 0))
        self.progress = ttk.Progressbar(sf, orient="horizontal",
                                        mode="determinate")
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew",
                           padx=10, pady=(0, 5))

        nb = ttk.Notebook(panel); nb.grid(row=1, column=0, sticky="nsew")

        p_tab = ttk.Frame(nb); nb.add(p_tab, text="Plots")
        self._build_plots(p_tab)

        s_tab = ttk.Frame(nb); nb.add(s_tab, text="Protocol & PPMS Sequence")
        self._build_protocol_tab(s_tab)

    def _build_plots(self, parent):
        """One figure, 2x2 grid. Temperature vs time spans the whole left
        column; the right column shows Cp (top) and G (bottom) — vs
        TEMPERATURE during a Tscan run, vs FREQUENCY during the Fscan."""
        self.fig = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = self.fig.add_gridspec(2, 2)
        self.ax_temp = self.fig.add_subplot(gs[:, 0])
        self.ax_a = self.fig.add_subplot(gs[0, 1])
        self.ax_b = self.fig.add_subplot(gs[1, 1], sharex=self.ax_a)

        self.line_target, = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_GREEN, ls="--",
            label="Fscan target")
        self.line_temp, = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_RED, marker="o", ms=3, ls="-",
            label="Sample T")
        self.scat_meas, = self.ax_temp.plot(
            [], [], ls="", marker="o", ms=5, color=self.CLR_MEAS,
            label="Measuring (flag=1)")
        self.ax_temp.set_xlabel("Time (s)")
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, ls="--", alpha=0.6)
        self.ax_temp.legend(loc="best", frameon=True,
                            facecolor=self.CLR_GRAPH_BG)

        self.line_a, = self.ax_a.plot([], [], color="#C00000", marker="o",
                                      ms=3, ls="-")
        self.ax_a.set_ylabel("Cp (F)")
        self.ax_a.grid(True, ls="--", alpha=0.7)
        self.ax_a.tick_params(axis="x", which="both", labelbottom=False)
        self.line_b, = self.ax_b.plot([], [], color=self.CLR_MEAS,
                                      marker="s", ms=3, ls="-")
        self.ax_b.set_xlabel("Frequency (Hz) / Temperature (K)")
        self.ax_b.set_ylabel("G (S)")
        self.ax_b.grid(True, ls="--", alpha=0.7)

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
        tb = NavigationToolbar2Tk(self.canvas_plots, parent,
                                  pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas_plots.get_tk_widget().pack(fill="both", expand=True)

    PROTO_COLS = ("num", "phase", "detail", "expected", "elapsed", "status")

    def _build_protocol_tab(self, parent):
        ttk.Label(parent,
                  text="Phase plan (what the master will do, in order) and "
                       "the matching MultiVu sequence. Generate and save "
                       "the .seq, load it into MultiVu, START THIS PROGRAM "
                       "FIRST, then start the PPMS sequence. This program "
                       "never commands the PPMS.",
                  wraplength=950, background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT, justify="left"
                  ).pack(side="top", anchor="w", padx=8, pady=(6, 2))

        ctl = ttk.Frame(parent)
        ctl.pack(side="top", fill="x", padx=8, pady=2)
        b = ttk.Button(ctl, text="Preview protocol plan",
                       command=self._preview_protocol)
        b.pack(side="left", padx=(0, 8))
        self._prerun_simple.append(b)
        b = ttk.Button(ctl, text="Generate PPMS .seq preview",
                       command=self._generate_seq_preview)
        b.pack(side="left", padx=(0, 8))
        self._prerun_simple.append(b)
        b = ttk.Button(ctl, text="Save .seq…", command=self._save_seq)
        b.pack(side="left")
        self._prerun_simple.append(b)

        self.proto_total_lbl = ttk.Label(
            parent, text="Planned total: —  (click 'Preview protocol plan')",
            font=("Segoe UI", 11, "bold"), background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD)
        self.proto_total_lbl.pack(side="top", anchor="w", padx=8,
                                  pady=(2, 4))

        tf = ttk.Frame(parent)
        tf.pack(fill="both", expand=True, padx=5, pady=5)
        sb = ttk.Scrollbar(tf, orient="vertical")
        self.proto_tree = ttk.Treeview(
            tf, columns=self.PROTO_COLS, show="headings", height=8,
            yscrollcommand=sb.set)
        sb.config(command=self.proto_tree.yview)
        heads = {"num": ("#", 36), "phase": ("Phase", 130),
                 "detail": ("What happens", 420),
                 "expected": ("Expected", 90),
                 "elapsed": ("Took", 90),
                 "status": ("Status", 150)}
        for col in self.PROTO_COLS:
            text, width = heads[col]
            self.proto_tree.heading(col, text=text)
            self.proto_tree.column(col, width=width, anchor="w",
                                   stretch=(col == "detail"))
        self.proto_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(parent,
                  text="Generated MultiVu sequence (editable before "
                       "saving):",
                  background=self.CLR_BG_DARK,
                  foreground=self.CLR_FG_LIGHT
                  ).pack(side="top", anchor="w", padx=8, pady=(4, 0))
        self.seq_text = scrolledtext.ScrolledText(
            parent, height=12, bg=self.CLR_INPUT_BG, fg=self.CLR_TEXT_DARK,
            font=self.FONT_CONSOLE, wrap="none")
        self.seq_text.pack(side="top", fill="both", expand=True,
                           padx=8, pady=(2, 8))

    # ------------------------------------------------------------
    # Protocol preview / .seq generation (main thread, pre-run)
    # ------------------------------------------------------------
    def _preview_protocol(self):
        try:
            cfg = self._collect_protocol_cfg()
        except Exception as e:
            messagebox.showerror("Protocol", str(e))
            return
        phases = build_protocol_phases(cfg)
        if not phases:
            messagebox.showwarning(
                "Empty Protocol",
                "Add at least one passive run or Fscan setpoints.")
            return
        self._fill_protocol_tree(phases)
        self.log(f"Protocol preview: {len(phases)} phases, planned total "
                 f"{fmt_hms(sum(p['expected_s'] for p in phases))}. "
                 f"Fscan step wait used for planning: "
                 f"{fmt_hms(cfg['step_wait_s'])}.")

    def _fill_protocol_tree(self, phases):
        for iid in self.proto_tree.get_children():
            self.proto_tree.delete(iid)
        total_s = 0.0
        for i, ph in enumerate(phases):
            total_s += ph["expected_s"]
            self.proto_tree.insert("", "end", iid=f"p{i}", values=(
                i + 1, ph["label"], ph["detail"],
                fmt_hms(ph["expected_s"]) if ph["expected_s"] > 0 else "—",
                "", "pending"))
        now = datetime.now()
        finish_min = now.hour * 60 + now.minute + total_s / 60.0
        txt = (f"Planned total ≈ {fmt_hms(total_s)}   →  if started now, "
               f"finishes ~ {fmt_clock(finish_min)}")
        if total_s >= 86400:
            txt += f" (+{int(total_s // 86400)} d)"
        self.proto_total_lbl.config(text=txt)

    def _generate_seq_preview(self):
        try:
            cfg = self._collect_protocol_cfg()
        except Exception as e:
            messagebox.showerror("Protocol", str(e))
            return
        text = generate_ppms_seq(cfg)
        # SEQ-2 self-check: a generated sequence failing its own grammar
        # is a program bug — refuse to present it as valid.
        errors = validate_ppms_seq(text)
        if errors:
            self.log("CRITICAL: generated sequence failed validation "
                     "(program bug — do NOT use):")
            for err in errors:
                self.log(f"  {err}")
            messagebox.showerror(
                "Sequence Generator Bug",
                "The generated sequence failed its own validation — "
                "see the console. Do not use it; please report this.")
            return
        self.seq_text.delete("1.0", tk.END)
        self.seq_text.insert("1.0", text)
        # SEQ-3: cooldown arithmetic, spelled out per run.
        ramp_s = ((cfg["top_temp"] - cfg["base_temp"])
                  / cfg["cool_rate"] * 60.0)
        for run in cfg["runs"]:
            soak = run["cooldown_wait_s"] - ramp_s
            self.log(f"  {run['label']}: cooldown "
                     f"{fmt_hms(run['cooldown_wait_s'])} = PPMS ramp "
                     f"{cfg['top_temp']:g}->{cfg['base_temp']:g} K at "
                     f"{cfg['cool_rate']:g} K/min ({fmt_hms(ramp_s)}) "
                     f"+ probe soak {fmt_hms(soak)}.")
        if cfg["schedule"]:
            soak = cfg["final_cooldown_s"] - ramp_s
            self.log(f"  Final cooldown: {fmt_hms(cfg['final_cooldown_s'])} "
                     f"= ramp {fmt_hms(ramp_s)} + probe soak "
                     f"{fmt_hms(soak)}.")
        self.log(f"PPMS .seq generated and VALIDATED "
                 f"({len(cfg['runs'])} passive run(s), "
                 f"{len(cfg['schedule'])} Fscan setpoint(s), Tscan TMP "
                 f"mode {cfg['tscan_approach']}, Fscan TMP mode "
                 f"{cfg['fscan_approach']}, step wait "
                 f"{fmt_hms(cfg['step_wait_s'])} = stability window + "
                 "scan estimate + margin). Review / edit the preview, "
                 "then 'Save .seq…'.")
        self._preview_protocol()

    def _save_seq(self):
        content = self.seq_text.get("1.0", tk.END).strip()
        if not content:
            self._generate_seq_preview()
            content = self.seq_text.get("1.0", tk.END).strip()
            if not content:
                return
        # SEQ-2: re-validate at save time — the preview is editable, so
        # this catches manual typos before a faulty sequence can ever
        # reach MultiVu. (Pre-run, user-initiated: a dialog is fine.)
        errors = validate_ppms_seq(content)
        if errors:
            shown = "\n".join(errors[:8])
            if len(errors) > 8:
                shown += f"\n… and {len(errors) - 8} more."
            messagebox.showerror(
                "Invalid Sequence — NOT saved",
                "The sequence in the preview has errors. A faulty "
                "sequence would ruin the unattended run, so saving is "
                f"blocked until they are fixed:\n\n{shown}")
            self.log(f"Sequence save blocked: {len(errors)} validation "
                     "error(s) — see the dialog.")
            return
        sample = self.lcr_entries["sample_name"].get().strip() or "Sample"
        path = filedialog.asksaveasfilename(
            defaultextension=".seq",
            initialfile=f"{sample}_Dielectric_Master.seq",
            filetypes=[("MultiVu sequence", "*.seq"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content + "\n")
            self.log(f"PPMS sequence saved: {path}")
            messagebox.showinfo(
                "Sequence Saved",
                f"Saved:\n{path}\n\nLoad it into MultiVu. Start THIS "
                "program first, then run the sequence.")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))

    # ------------------------------------------------------------
    # Plot-mode switching (main thread only)
    # ------------------------------------------------------------
    def _apply_plot_mode(self, mode, label):
        self._plot_mode = mode
        self._plot_mode_label = label
        self.scan_f.clear(); self.scan_cp.clear(); self.scan_g.clear()
        self._decade_ylims.clear()
        self._scan_info = None
        if mode == "tscan":
            self.ts_store = {f: {"T": [], "cp": [], "g": []}
                             for f in getattr(self, "tscan_freqs_list", [])}
            self.ax_a.set_xscale("linear")
            self.ax_b.set_xlabel("Temperature (K)")
            self.ax_a.set_title(f"Tscan {label}", fontsize=10)
        else:
            self.ax_a.set_xscale("log")
            self.ax_b.set_xlabel("Frequency (Hz)")
            self.ax_a.set_title("")
        self.line_a.set_data([], [])
        self.line_b.set_data([], [])
        self._freq_plot_dirty = True

    def _on_plot_freq_change(self, event=None):
        sel = self.plot_freq_cb.get().replace(" Hz", "").strip()
        try:
            self.plot_freq = float(sel)
        except ValueError:
            return
        self._decade_ylims.pop("cp", None)
        self._decade_ylims.pop("g", None)
        self._freq_plot_dirty = True

    def _on_y_scale_change(self):
        self._decade_ylims.clear()
        self._freq_plot_dirty = True

    def _apply_y_scale(self, ax, values, key):
        """Adaptive Y-scale driven by self.y_scale_var ('auto'|'log'|'linear').
        Same LabVIEW-style decade autoscale as the parent programs."""
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
    # Throttled redraw + display decimation
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
                if self._plot_mode == "tscan":
                    st = self.ts_store.get(self.plot_freq)
                    if st is not None:
                        self.line_a.set_data(st["T"], st["cp"])
                        self.line_b.set_data(st["T"], st["g"])
                        cp_data, g_data = st["cp"], st["g"]
                    else:
                        cp_data, g_data = [], []
                    if self.plot_freq is not None:
                        self.ax_a.set_title(
                            f"Tscan {self._plot_mode_label} @ "
                            f"{int(self.plot_freq)} Hz", fontsize=10)
                else:
                    self.line_a.set_data(self.scan_f, self.scan_cp)
                    self.line_b.set_data(self.scan_f, self.scan_g)
                    cp_data, g_data = self.scan_cp, self.scan_g
                    if self._scan_info is None:
                        self.ax_a.set_title("")
                    else:
                        state, t_set = self._scan_info
                        if state == "measuring":
                            self.ax_a.set_title(
                                f"Measuring at {t_set:.2f} K …", fontsize=10)
                        else:
                            self.ax_a.set_title(
                                f"Last scan: {t_set:.2f} K "
                                f"(held until next scan)", fontsize=10)
                for ax, key, data in ((self.ax_a, "cp", cp_data),
                                      (self.ax_b, "g", g_data)):
                    ax.relim(); ax.autoscale_view(scalex=True, scaley=False)
                    self._apply_y_scale(ax, data, key)
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
    # UI helpers (copied from PPMS_Sync)
    # ------------------------------------------------------------
    def _create_grid_entry(self, parent, label, key, default, r, c,
                           lockable=True):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w",
                                           padx=(10, 2), pady=2)
        e = ttk.Entry(parent, font=self.FONT_BASE, width=10)
        e.grid(row=r, column=c+1, sticky="ew", padx=2, pady=2)
        e.insert(0, default)
        if lockable:
            lb = ttk.Button(parent, text="🔓", width=2,
                            command=lambda k=key: self._toggle_entry_lock(k))
            lb.grid(row=r, column=c+2, sticky="w", padx=(0, 10), pady=2)
            self.entries[key] = {"entry": e, "lock": lb, "locked": False}
        else:
            self.entries[key] = {"entry": e, "lock": None, "locked": False}
        return e

    def _add_lcr_entry(self, parent, label, key, r, c, span, default):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w",
                                           padx=5, pady=2)
        e = ttk.Entry(parent, font=self.FONT_BASE, width=12)
        e.grid(row=r, column=c+1, columnspan=span, sticky="ew", padx=5,
               pady=2)
        e.insert(0, default)
        self.lcr_entries[key] = e
        self._prerun_simple.append(e)

    def _toggle_entry_lock(self, key):
        w = self.entries[key]
        if w["locked"]:
            w["entry"].config(state="normal")
            w["lock"].config(text="🔓"); w["locked"] = False
        else:
            w["entry"].config(state="disabled")
            w["lock"].config(text="🔒"); w["locked"] = True

    def log(self, msg):
        try:
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
        except tk.TclError:
            # Window mid-destroy — a late log line must never block
            # shutdown (root.destroy would otherwise never run).
            print(f"[log during teardown] {msg}")

    def _update_status_ui(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _set_keep_awake(self, enable):
        """Stop Windows from sleeping mid-run (display may still sleep).
        Best-effort no-op on other platforms."""
        try:
            flags = self.ES_CONTINUOUS | (
                self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _beep(self):
        """Main (Tk) thread only; worker beeps via gui_queue."""
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
    # Scan-time estimate (Fscan sweep)
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
            text=f"One Fscan sweep ({n} pts): ~{fmt_hms(total)}  [{src}]")

    # ------------------------------------------------------------
    # Live-update / run-control senders
    # ------------------------------------------------------------
    def _send_live_updates(self):
        if not self.is_running:
            messagebox.showwarning("Not Running",
                                   "Only during an active protocol.")
            return
        updates = {}
        try:
            for k, w in self.entries.items():
                if w["lock"] is not None and not w["locked"]:
                    updates[k] = float(w["entry"].get())
        except ValueError:
            messagebox.showerror("Invalid Input",
                                 "Unlocked params must be numeric.")
            return
        self.cmd_queue.put(("params", updates))
        self.log(f"Queued live params: {updates}")

    def _toggle_pause(self):
        if not self.is_running:
            return
        if self.pause_button["text"] == "Pause":
            self.cmd_queue.put(("pause",))
            self.pause_button.config(text="Resume")
            self.log("PAUSE requested (measurement suspended; temperature "
                     "logging continues; phase detection frozen).")
        else:
            self.cmd_queue.put(("resume",))
            self.pause_button.config(text="Pause")
            self.log("RESUME requested.")

    def _skip_step(self):
        """Phase-sensitive skip: WAIT_BASE -> next phase now; TSCAN ->
        end the run now; WAIT_STABLE -> sweep starts immediately;
        SCAN -> remainder of sweep aborted."""
        if not self.is_running:
            return
        self.cmd_queue.put(("skip",))
        self.log("SKIP PHASE requested.")

    def _skip_freq_step(self):
        """Fscan only: abandon the CURRENT temperature step — whether it
        is still waiting for stability or already sweeping — and move to
        the NEXT schedule setpoint. Ignored (with a log line) in every
        other phase."""
        if not self.is_running:
            return
        self.cmd_queue.put(("skip_freq",))
        self.log("SKIP FREQ STEP requested (Fscan only: current setpoint "
                 "is abandoned; protocol moves to the next one).")

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
            # CC34-7: identify_resources() skips serial resources and
            # leaves the PyVISA termination defaults alone — the
            # Cryocon frames its replies with EOI and no EOS character.
            resources = list(rm.list_resources())
            identities = identify_resources(rm, resources)
            for res in resources:
                idn = identities.get(res, "Unknown")
                label = f"{res}  ->  {idn}"
                found.append(label)
                # CC34-3: identity, never address. The Lakeshore 350
                # now sits on the Cryo-con factory address.
                if is_cryocon_idn(idn) and ls_pick is None:
                    ls_pick = label
                if "E4980" in idn and lcr_pick is None:
                    lcr_pick = label
                self.log(f"  {label}")
            self.ls_cb["values"] = found
            self.lcr_cb["values"] = found
            if ls_pick:
                self.ls_cb.set(ls_pick)
                self.log(f"Cryocon auto-selected: {ls_pick}")
            if lcr_pick:
                self.lcr_cb.set(lcr_pick)
                self.log(f"E4980A auto-selected: {lcr_pick}")
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
    # Config collection / validation
    # ------------------------------------------------------------
    def _parse_tscan_freqs(self):
        raw = self.tscan_freq_text.get("1.0", "end").replace("\n", " ")
        freqs = []
        for tok in raw.split(","):
            tok = tok.strip()
            if not tok:
                continue
            f = float(tok)
            if not (20 <= f <= 2e6):
                raise ValueError(
                    f"Tscan frequency {f} Hz outside E4980A range "
                    f"(20 Hz - 2 MHz).")
            freqs.append(f)
        return sorted(set(freqs))

    def _collect_protocol_cfg(self):
        """Assemble and validate the whole protocol config dict."""
        runs = []
        for i, r in enumerate(self.run_rows, 1):
            cd_min = parse_duration_min(r["cooldown"])
            if cd_min < 0:
                raise ValueError(f"Run {i}: cooldown must be >= 0.")
            kind = r.get("kind", "run")
            runs.append({"label": (f"Cooldown{i}_only" if kind == "cool"
                                   else make_run_label(i, r["field_oe"])),
                         "field_oe": float(r["field_oe"]),
                         "cooldown_wait_s": cd_min * 60.0,
                         "kind": kind})

        def fget(key, name):
            try:
                return float(self.proto[key].get())
            except ValueError:
                raise ValueError(f"{name} must be a number.")

        base_temp = fget("base_temp", "Base T")
        top_temp = fget("top_temp", "Top T")
        warm_rate = fget("warm_rate", "Warm rate")
        cool_rate = fget("cool_rate", "Cool rate")
        top_hold_min = fget("top_hold", "Hold at top")
        field_rate = fget("field_rate", "Field set rate")
        fscan_rate = fget("fscan_rate", "Fscan ramp")
        final_cd_min = parse_duration_min(
            self.proto["final_cooldown"].get() or "0")
        if base_temp >= top_temp:
            raise ValueError("Base T must be below Top T.")
        if warm_rate <= 0 or cool_rate <= 0 or fscan_rate <= 0:
            raise ValueError("Rates must be positive.")
        if top_hold_min < 0 or final_cd_min < 0:
            raise ValueError("Hold / final cooldown must be >= 0.")
        if field_rate <= 0:
            raise ValueError("Field set rate must be positive.")

        def dget(key, name):
            try:
                return float(self.det[key].get())
            except ValueError:
                raise ValueError(f"{name} must be a number.")

        base_arm = dget("base_arm", "Base arm")
        rise_k = dget("rise_k", "Rise")
        top_arm_off = dget("top_arm_off", "Top arm offset")
        fall_k = dget("fall_k", "Fall")
        confirm_min = dget("confirm_min", "Confirm")
        overdue_min = dget("overdue_min", "Overdue warn")
        fallback_x = dget("fallback_x", "Fallback")
        if rise_k <= 0 or fall_k <= 0:
            raise ValueError("Rise / Fall must be positive (K).")
        if fallback_x != 0 and fallback_x <= 1.25:
            raise ValueError(
                "Fallback must be 0 (off) or > 1.25 x expected — a "
                "ceiling at/below the overdue threshold would cut "
                "cooldowns short on normal timing jitter.")
        if base_arm <= base_temp:
            raise ValueError(
                f"Base arm ({base_arm:g} K) should be ABOVE Base T "
                f"({base_temp:g} K) — the probe never reaches the PPMS "
                "setpoint.")
        if top_arm_off <= 0 or top_arm_off >= (top_temp - base_temp):
            raise ValueError("Top arm offset must be > 0 and smaller than "
                             "the Base-to-Top span.")
        if confirm_min < 0 or overdue_min <= 0:
            raise ValueError("Confirm must be >= 0; Overdue warn > 0.")

        # SEQ-3: a cooldown WAITFOR shorter than the PPMS ramp itself is
        # a faulty sequence — warming would start before base is reached.
        ramp_s = (top_temp - base_temp) / cool_rate * 60.0
        for i, run in enumerate(runs, 1):
            if run["cooldown_wait_s"] < ramp_s:
                raise ValueError(
                    f"Run {i} cooldown ({fmt_hms(run['cooldown_wait_s'])}) "
                    f"is SHORTER than the PPMS ramp {top_temp:g}->"
                    f"{base_temp:g} K at {cool_rate:g} K/min "
                    f"({fmt_hms(ramp_s)}). The sequence would start "
                    "warming before base is ever reached — set cooldown = "
                    "ramp time + probe soak (the probe needs extra time "
                    "to bottom out).")

        tscan_freqs = self._parse_tscan_freqs()
        # COOL-1: cooldown-only rows need no Tscan frequencies.
        if any(r.get("kind", "run") == "run" for r in runs) \
                and not tscan_freqs:
            raise ValueError("Tscan frequency list is empty but passive "
                             "runs are defined.")

        schedule = self._get_targets()
        for t in schedule:
            if t <= 0:
                raise ValueError(f"Fscan setpoint {t} K invalid.")
        # COOL-2: the final cooldown also exists (by default) when there
        # is no Fscan section — validate its duration in both cases.
        final_cd_used = bool(schedule) or (
            bool(runs) and self.var_final_cd_always.get())
        if final_cd_used and final_cd_min * 60.0 < ramp_s:
            raise ValueError(
                f"Final cooldown ({fmt_hms(final_cd_min * 60.0)}) is "
                f"SHORTER than the PPMS ramp {top_temp:g}->{base_temp:g} K "
                f"at {cool_rate:g} K/min ({fmt_hms(ramp_s)}).")

        # Fscan step wait for the generated sequence / planning:
        # stability window + scan estimate + margin.
        self._update_scan_estimate()
        try:
            window_min = float(self.entries["window_min"]["entry"].get())
            margin_min = float(self.entries["margin_min"]["entry"].get())
        except (ValueError, tk.TclError):
            raise ValueError("Check Window (min) and Margin (min).")
        step_wait_s = window_min * 60.0 + self._scan_est_s \
            + margin_min * 60.0

        # DWELL-1: wait-vs-T floor for the generated Fscan WAITFORs.
        dwell_table = None
        if self.var_dwell_table.get():
            try:
                dwell_table = parse_tol_table(self.dwell_table_entry.get())
            except ValueError as e:
                raise ValueError(f"Fscan wait-vs-T table: {e}")

        return {
            "sample": self.lcr_entries["sample_name"].get().strip()
                      or "Sample",
            "runs": runs,
            "base_temp": base_temp,
            "top_temp": top_temp,
            "warm_rate": warm_rate,
            "cool_rate": cool_rate,
            "top_hold_s": top_hold_min * 60.0,
            "final_cooldown_s": final_cd_min * 60.0,
            "field_rate": field_rate,
            "fscan_rate": fscan_rate,
            "tscan_approach": 1 if "(1)" in self.tscan_mode_cb.get() else 0,
            "fscan_approach": 1 if "(1)" in self.fscan_mode_cb.get() else 0,
            "base_arm": base_arm,
            "rise_k": rise_k,
            "top_arm_off": top_arm_off,
            "fall_k": fall_k,
            "confirm_s": confirm_min * 60.0,
            "overdue_s": overdue_min * 60.0,
            "fallback_x": fallback_x,
            "tscan_freqs": tscan_freqs,
            "schedule": schedule,
            "step_wait_s": step_wait_s,
            "dwell_table": dwell_table,   # DWELL-1: None = table off
            # COOL-2: default ON — end with a cooldown to base even
            # without an Fscan section (ready for manual M(H) etc.).
            "final_cd_no_fscan": bool(self.var_final_cd_always.get()),
        }

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
            # TOL-1: parsed once at Start; a bad table fails loudly here.
            "tol_table": (parse_tol_table(self.tol_table_entry.get())
                          if self.var_tol_table.get() else None),
            "resync": self.var_resync.get(),        # RESYNC-1
            "agnostic": self.var_agnostic.get(),    # AGNOS-1
            "agn_min_dT": float(self.agn_dt_entry.get() or "2"),
        }
        if not p["thermo_visa"]:
            raise ValueError("Select the Cryocon VISA.")
        if p["tol"] <= 0: raise ValueError("Tolerance must be positive.")
        if p["window_min"] <= 0: raise ValueError("Window must be positive.")
        if p["drift"] <= 0: raise ValueError("Drift limit must be positive.")
        if p["guard"] < 0:
            raise ValueError("Target guard must be >= 0 (0 disables).")
        if p["stab_timeout"] < 0:
            raise ValueError("Timeout must be >= 0 (0 disables).")
        if p["delay"] <= 0: raise ValueError("Poll delay must be positive.")
        if p["margin_min"] < 0: raise ValueError("Margin must be >= 0 min.")
        if p["agnostic"] and p["agn_min_dT"] <= 0:
            raise ValueError("Min ΔT between plateau scans must be > 0 K.")
        return p

    def _validate_lcr_params(self):
        lcr_visa = self.lcr_cb.get()
        if "  ->  " in lcr_visa:
            lcr_visa = lcr_visa.split("  ->  ")[0].strip()
        p = {
            "sample_name": self.lcr_entries["sample_name"].get().strip()
                           or "Sample",
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

    # ------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------
    def start_protocol(self):
        if self.is_running:
            return
        if not self.save_dir:
            messagebox.showwarning("No Save Dir",
                                   "Choose a save directory first.")
            return
        try:
            self.params = self._validate_params()
            self.lcr_params = self._validate_lcr_params()
            cfg = self._collect_protocol_cfg()
        except Exception as e:
            messagebox.showerror("Config Error", str(e))
            return
        if not cfg["runs"] and not cfg["schedule"]:
            messagebox.showwarning(
                "Empty Protocol",
                "Add at least one passive run or Fscan setpoints.")
            return

        # Crash/restart aid: warn (console only, never a modal) if a
        # previous run in this folder died without finishing — its
        # RUNSTATE file still shows the phase it was in.
        self._warn_stale_runstates()

        self.cfg = cfg
        self.schedule = cfg["schedule"]
        self.tscan_freqs_list = cfg["tscan_freqs"]
        self.protocol_phases = build_protocol_phases(cfg)
        self._fill_protocol_tree(self.protocol_phases)

        # Tscan live-plot frequency dropdown
        if cfg["tscan_freqs"]:
            vals = [f"{int(f)} Hz" for f in cfg["tscan_freqs"]]
            self.plot_freq_cb.config(state="readonly")
            self.plot_freq_cb["values"] = vals
            self.plot_freq_cb.current(0)
            self.plot_freq = cfg["tscan_freqs"][0]

        self.set_ui_state(running=True)
        self.is_running = True
        self._worker_phase = None
        self._phase_index = -1
        self._paused = False
        self._skip_requested = False
        self._skip_step_requested = False
        # GLITCH-1: fresh probe-validation state per run — a stale
        # _last_temp from a previous run at a very different T would
        # make the new run's first reading look like a >20 K jump.
        self._last_temp = float("nan")
        self._glitch_candidate = None
        self._glitch_total = 0
        self.pause_button.config(text="Pause")

        for L in (self.plot_t, self.plot_temp, self.plot_target,
                  self.meas_t, self.meas_temp,
                  self.scan_f, self.scan_cp, self.scan_g):
            L.clear()
        self.ts_store = {}
        self._plot_mode = None
        self.line_temp.set_data([], []); self.line_target.set_data([], [])
        self.scat_meas.set_data([], [])
        self.line_a.set_data([], []); self.line_b.set_data([], [])
        if self.band_patch is not None:
            try:
                self.band_patch.remove()
            except Exception:
                pass
            self.band_patch = None
        self._band_params = None
        self._band_dirty = False
        self._scan_info = None
        self.ax_a.set_title("")
        self.canvas_plots.draw_idle()
        self._decade_ylims.clear()
        self._temp_plot_dirty = False
        self._freq_plot_dirty = False
        self.progress["value"] = 0
        self._pending_progress = None
        self.progress["maximum"] = max(
            1, len(self.schedule) * len(self.sweep_frequencies))

        while not self.cmd_queue.empty():
            try: self.cmd_queue.get_nowait()
            except queue.Empty: break
        while not self.gui_queue.empty():
            try: self.gui_queue.get_nowait()
            except queue.Empty: break

        self.worker_thread = threading.Thread(
            target=self._hardware_worker_loop, daemon=True)
        self.worker_thread.start()
        self.root.after(50, self._process_gui_queue)

    def _warn_stale_runstates(self):
        """Crash/restart aid: list RUNSTATE files in the save dir whose
        run never reached COMPLETE — a previous protocol died there
        (power cut, crash) or was stopped. Console log only; best-effort."""
        try:
            for fn in sorted(os.listdir(self.save_dir)):
                if not fn.endswith("_Master_RUNSTATE.txt"):
                    continue
                try:
                    with open(os.path.join(self.save_dir, fn),
                              encoding="utf-8") as fh:
                        content = fh.read()
                except OSError:
                    continue
                if "status: COMPLETE" in content:
                    continue
                status = next(
                    (ln[len("status: "):] for ln in content.splitlines()
                     if ln.startswith("status: ")), "unknown")
                phase = next(
                    (ln[len("phase: "):] for ln in content.splitlines()
                     if ln.startswith("phase: ")), "?")
                self.log(f"⚠️ PREVIOUS RUN DID NOT COMPLETE: {fn} — last "
                         f"state '{status}' at phase {phase}. Its data "
                         "files are still in this folder.")
        except Exception:
            pass

    def stop_protocol(self, reason=""):
        if not self.is_running:
            return
        self.log(f"STOP requested: {reason or 'user'}")
        # Worker is the only writer of is_running.
        self.cmd_queue.put(("stop",))
        self._update_status_ui("STOPPING…", self.CLR_ACCENT_RED)

    def set_ui_state(self, running: bool):
        # Keep-awake tracks the run state exactly
        self._set_keep_awake(running)
        st = "disabled" if running else "normal"
        self.start_button.config(state=st)
        self.stop_button.config(state="normal" if running else "disabled")
        self.pause_button.config(state="normal" if running else "disabled")
        self.skip_button.config(state="normal" if running else "disabled")
        self.skip_freq_button.config(
            state="normal" if running else "disabled")
        if not running:
            self.pause_button.config(text="Pause")
        for w in self._prerun_simple:
            try:
                w.config(state=st)
            except tk.TclError:
                pass
        for cb in self._prerun_combos:
            try:
                cb.config(state="disabled" if running else "readonly")
            except tk.TclError:
                pass
        self.tscan_freq_text.config(state=st)
        self.runs_lb.config(state=st)
        self.listbox.config(state=st)
        for rb in self.stab_mode_radios:
            rb.config(state=st)
        # Stability entries follow the lock pattern (live-editable unless
        # locked, exactly like the parent program).
        for w in self.entries.values():
            if running:
                if w["lock"] is not None:
                    w["entry"].config(
                        state="disabled" if w["locked"] else "normal")
                else:
                    # Non-lockable entries are pre-run-only — read-only
                    # while running (kept in sync with the Sync GUI).
                    w["entry"].config(state="disabled")
            else:
                w["entry"].config(
                    state="disabled" if w["locked"] else "normal")
            if w["lock"] is not None:
                w["lock"].config(state=st)
        if not running:
            self._on_stab_mode_changed()

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
                elif t == "plot_mode":
                    self._apply_plot_mode(m["mode"], m["label"])
                elif t == "tscan_cycle":
                    for (f, temp, cp, g) in m["points"]:
                        st = self.ts_store.get(f)
                        if st is None:
                            continue
                        st["T"].append(temp)
                        st["cp"].append(cp)
                        st["g"].append(g)
                        if len(st["T"]) > self.MAX_PLOT_POINTS:
                            st["T"] = st["T"][::2]
                            st["cp"] = st["cp"][::2]
                            st["g"] = st["g"][::2]
                    self._freq_plot_dirty = True
                elif t == "scan_reset":
                    self.scan_f.clear(); self.scan_cp.clear(); self.scan_g.clear()
                    self._decade_ylims.clear()
                    target = m.get("target")
                    self._scan_info = None if target is None \
                        else ("measuring", target)
                    self._freq_plot_dirty = True
                elif t == "scan_done":
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
                elif t == "phase_status":
                    iid = f"p{m['index']}"
                    if self.proto_tree.exists(iid):
                        self.proto_tree.set(iid, "status", m["status"])
                        if m.get("elapsed_s") is not None:
                            self.proto_tree.set(iid, "elapsed",
                                                fmt_hms(m["elapsed_s"]))
                elif t == "sequence_complete":
                    # UNATTENDED POLICY: never open a modal dialog from
                    # the queue pump — a messagebox here would block all
                    # further queue processing (logs, plots, worker_done)
                    # until someone clicks OK, and nobody is in the lab.
                    self.log("★★★ PROTOCOL COMPLETE — all phases "
                             "finished. Data is on disk. ★★★")
                elif t == "worker_done":
                    self.set_ui_state(running=False)
                    self._update_status_ui("IDLE", self.CLR_HEADER)
                    break
        except queue.Empty:
            pass
        if self.worker_thread and self.worker_thread.is_alive():
            self.root.after(50, self._process_gui_queue)
        elif not self.gui_queue.empty():
            self.root.after(50, self._process_gui_queue)

    # ------------------------------------------------------------
    # Impedance math (carried over verbatim from the parents)
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
    # WORKER THREAD (owns both instruments; runs the phase machine)
    # ============================================================
    def _phase_banner(self, idx):
        return f"PHASE {idx + 1}/{len(self.protocol_phases)}"

    def _hardware_worker_loop(self):
        self.start_time = time.time()
        self.tlog_path = None
        self.timing_path = None
        self.protolog_path = None
        self.runstate_path = None
        self.fscan_dir = None
        self._pending_rows.clear()
        self._write_error_logged = False
        runstate_finalized = False   # COMPLETE / CRITICAL already written
        try:
            self._put_gui_msg("log", text="Connecting to probe thermometer "
                                          "(Cryo-con Model 34, read-only)…")
            idn = self.thermo_backend.connect(self.params["thermo_visa"])
            # CC34-2: units and a live sensor are checked once, here at
            # Start, where a wrong channel can still be named by letter
            # instead of silently logging Celsius for days.
            t_now = self.thermo_backend.verify_channel(
                self.params["channel"])
            self._put_gui_msg("log",
                text=f"Thermometer: {idn} "
                     f"(channel {self.params['channel']}, "
                     f"{t_now:.3f} K, units K)")

            self._put_gui_msg("log", text="Connecting to Keysight E4980A…")
            self.lcr_backend.initialize_instrument(self.lcr_params)
            self._put_gui_msg("log", text="E4980A initialized.")

            # Master logs: open/append/close + fsync per row — no handle
            # is held, so a power cut can never lose or corrupt rows.
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sample = self.lcr_params["sample_name"]
            self.tlog_path = os.path.join(
                self.save_dir, f"{sample}_{stamp}_Master_TempLog.csv")
            self._write_or_buffer(self.tlog_path, self._csv_line(
                ["Timestamp", "Elapsed_s", "Target_K", "Sample_T_K",
                 "Measuring", "Phase"]))
            self._put_gui_msg("log", text=f"Temperature log: {self.tlog_path}")

            self.protolog_path = os.path.join(
                self.save_dir, f"{sample}_{stamp}_Master_ProtocolLog.csv")
            self._write_or_buffer(self.protolog_path, self._csv_line(
                ["Phase", "Kind", "Label", "Start", "End",
                 "Duration_s", "Outcome"]))
            self._put_gui_msg("log", text=f"Protocol log: {self.protolog_path}")

            # Crash/restart aid: rewritten at every phase transition so a
            # power cut leaves the last known protocol state on disk.
            self.runstate_path = os.path.join(
                self.save_dir, f"{sample}_{stamp}_Master_RUNSTATE.txt")
            self._write_runstate("STARTED (connecting done, protocol "
                                 "beginning)")
            self._put_gui_msg("log", text=f"Run state file: "
                                          f"{self.runstate_path}")

            if self.schedule:
                self.timing_path = os.path.join(
                    self.save_dir, f"{sample}_{stamp}_Master_TimingLog.csv")
                self._write_or_buffer(self.timing_path, self._csv_line(
                    ["Step", "Target_K", "Step_start", "Sleep_used_s",
                     "Stab_wait_s", "Stab_outcome", "Scan_s",
                     "TempDriftDuringScan",
                     "Suggested_PPMS_wait_s", "Suggested_PPMS_wait_hms"]))
                self._put_gui_msg("log",
                                  text=f"Fscan timing log: {self.timing_path}")

            n = len(self.protocol_phases)
            for idx, ph in enumerate(self.protocol_phases):
                if not self.is_running:
                    break
                self._phase_index = idx
                self._put_gui_msg("phase_status", index=idx, status="ACTIVE")
                self._write_runstate(
                    "ACTIVE", detail=f"{ph['kind']} — {ph['label']}")
                self._put_gui_msg("log",
                    text=f"=== {self._phase_banner(idx)}: {ph['label']} — "
                         f"{ph['detail']} ===")
                t0 = time.time()
                start_dt = datetime.now()
                if ph["kind"] == "WAIT_BASE":
                    outcome = self._phase_wait_base(idx, ph)
                elif ph["kind"] == "TSCAN":
                    outcome = self._phase_tscan(idx, ph)
                else:
                    outcome = self._phase_fscan(idx, ph)
                dur = time.time() - t0
                self._write_or_buffer(self.protolog_path, self._csv_line(
                    [idx + 1, ph["kind"], ph["label"],
                     start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     f"{dur:.1f}", outcome]))
                self._put_gui_msg(
                    "phase_status", index=idx,
                    status=("stopped" if outcome == "stopped"
                            else f"done · {outcome}"),
                    elapsed_s=dur)
                self._put_gui_msg("log",
                    text=f"=== {self._phase_banner(idx)} finished "
                         f"({outcome}) after {fmt_hms(dur)} ===")
                self._write_runstate(
                    f"phase finished: {outcome}",
                    detail=f"{ph['kind']} — {ph['label']} "
                           f"({fmt_hms(dur)})")
                if outcome == "stopped" or not self.is_running:
                    break

            if self.is_running:
                runstate_finalized = True
                self._write_runstate("COMPLETE — all phases finished")
                self._put_gui_msg("log", text="PROTOCOL COMPLETE — all "
                                              "phases finished.")
                self._put_gui_msg("status", text="PROTOCOL COMPLETE",
                                  color=self.CLR_ACCENT_GREEN)
                self._put_gui_msg("beep")
                self._put_gui_msg("sequence_complete")

        except Exception as e:
            runstate_finalized = True
            self._write_runstate(f"CRITICAL ERROR: {e}",
                                 detail="see console / ProtocolLog")
            self._put_gui_msg("log",
                text=f"CRITICAL: {e}\n{traceback.format_exc()}")
            self._put_gui_msg("status", text="ERROR", color=self.CLR_ACCENT_RED)
        finally:
            if not runstate_finalized:
                # User stop — leave the last known state on disk. (A hard
                # power cut writes nothing here; the last ACTIVE record
                # survives, which is exactly the point of this file.)
                self._write_runstate(
                    "STOPPED BY USER (or window closed) — see console "
                    "and ProtocolLog")
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
            self._skip_step_requested = False
            self._put_gui_msg("worker_done")

    # ------------------------------------------------------------
    # Phase: WAIT_BASE (idle during a PPMS cooldown; T-log only)
    # ------------------------------------------------------------
    def _phase_wait_base(self, idx, ph):
        p, cfg = self.params, self.cfg
        self._worker_phase = "WAIT_BASE"
        det = TurnaroundDetector()
        confirm = SustainedCondition(cfg["confirm_s"])
        run = ph["run"]
        if run is None:
            next_label = "step Fscan"
        elif run.get("kind", "run") == "cool":
            # COOL-1: nothing measured after a standalone cooldown —
            # the next thing is whatever phase (or manual work) follows.
            next_label = "the next phase (cooldown only, no measurement)"
        else:
            next_label = run["label"]
        expected_s = ph["expected_s"]
        banner = self._phase_banner(idx)
        start = time.time()
        last_warn = 0.0
        try:
            while self.is_running:
                if self._process_cmd_queue():
                    return "stopped"
                if self._skip_requested:
                    self._skip_requested = False
                    self._put_gui_msg("log",
                        text="⏭ Cooldown wait skipped by user — moving to "
                             f"{next_label} now.")
                    return "skipped by user"
                temp = self._log_temperature_point(float("nan"), 0)
                if not self.is_running:
                    return "stopped"
                now = time.time()
                if self._paused:
                    self._put_gui_msg("status",
                        text=f"{banner} — PAUSED (cooldown wait before "
                             f"{next_label})",
                        color=self.CLR_ACCENT_GOLD)
                    time.sleep(p["delay"])
                    continue
                det.update(temp)
                inst = det.warming_started(cfg["base_arm"], cfg["rise_k"])
                if confirm.update(inst, now):
                    self._put_gui_msg("log",
                        text=f"WARMING CONFIRMED: T reached "
                             f"{det.min_T:.2f} K minimum, now "
                             f"{det.last_T:.2f} K (rise ≥ {cfg['rise_k']:g} K "
                             f"held {cfg['confirm_s']/60.0:g} min). "
                             f"Starting {next_label}.")
                    self._put_gui_msg("beep")
                    return "warming confirmed"
                if inst:
                    self._put_gui_msg("status",
                        text=(f"{banner} — WARMING SUSPECTED, confirming "
                              f"{fmt_hms(confirm.pending_s(now))}/"
                              f"{fmt_hms(cfg['confirm_s'])} | "
                              f"T {temp:.2f} K (min {det.min_T:.2f} K) | "
                              f"next: {next_label}"),
                        color=self.CLR_STABLE_WAIT)
                else:
                    base_txt = (f"min {det.min_T:.2f} K"
                                if math.isfinite(det.min_T)
                                and det.min_T != float("inf") else "min —")
                    self._put_gui_msg("status",
                        text=(f"{banner} — WAITING FOR COOLDOWN | "
                              f"T {temp:.2f} K ({base_txt}, arm ≤ "
                              f"{cfg['base_arm']:g} K) | next: {next_label}"),
                        color=self.CLR_SLEEP)
                elapsed = now - start
                # COOL-1/2: a standalone/final cooldown ends ON TIME —
                # its .seq WAITFOR ends at exactly this moment, and what
                # follows is the USER's own step (e.g. M(H) taken during
                # heating). Waiting for warming detection here would
                # deadlock: the user waits for this signal while the
                # signal waits for their heating. Detection stays as an
                # early-out (heating already started). Loud tell so the
                # M(H) can start without delay.
                if (run is not None and run.get("kind", "run") == "cool"
                        and expected_s > 0 and elapsed >= expected_s):
                    min_txt = (f"{det.min_T:.2f} K"
                               if math.isfinite(det.min_T) else "—")
                    self._put_gui_msg("log",
                        text=f"✅ {ph['label']} DONE: planned wait "
                             f"{fmt_hms(expected_s)} is over — T "
                             f"{temp:.2f} K (min {min_txt}). The PPMS "
                             "sequence has moved past this WAITFOR: "
                             "start the manual M(H) heating run NOW.")
                    self._put_gui_msg("status",
                        text=f"{banner} — COOLDOWN DONE — ready for "
                             "manual M(H)",
                        color=self.CLR_ACCENT_GREEN)
                    self._put_gui_msg("beep")
                    self._put_gui_msg("beep")
                    return "timed wait complete"
                # FALLBACK ceiling (user-approved 2026-07-17, default 2x):
                # detection stays primary, but a cooldown wait must NEVER
                # strand the whole unattended protocol — if warming was
                # not detected by fallback_x * expected, proceed loudly.
                if (expected_s > 0 and cfg.get("fallback_x", 0) > 0
                        and elapsed > cfg["fallback_x"] * expected_s):
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ FALLBACK: {ph['label']} hit its time "
                             f"ceiling ({fmt_hms(elapsed)} elapsed > "
                             f"{cfg['fallback_x']:g} × expected "
                             f"{fmt_hms(expected_s)}) without warming "
                             f"detection (T {temp:.2f} K, min "
                             f"{det.min_T:.2f} K). Proceeding to "
                             f"{next_label} anyway — CHECK THIS RUN'S "
                             "DATA and the PPMS sequence.")
                    self._put_gui_msg("beep")
                    return "fallback (time ceiling)"
                if (expected_s > 0
                        and elapsed > expected_s * 1.25 + 900
                        and now - last_warn >= cfg["overdue_s"]):
                    last_warn = now
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ {ph['label']} OVERDUE: "
                             f"{fmt_hms(elapsed)} elapsed vs expected "
                             f"{fmt_hms(expected_s)} and warming is not "
                             f"detected yet (T {temp:.2f} K, min "
                             f"{det.min_T:.2f} K). Still waiting — check "
                             "the PPMS sequence when possible.")
                    self._put_gui_msg("beep")
                time.sleep(p["delay"])
        finally:
            self._worker_phase = None
        return "stopped"

    # ------------------------------------------------------------
    # Phase: TSCAN (continuous passive measurement while warming —
    # identical measurement policy to Temprature_Scan_Passive:
    # per-point temperature binding, no direction assumptions,
    # record whatever temperature profile is thrown at it)
    # ------------------------------------------------------------
    def _phase_tscan(self, idx, ph):
        p, cfg, lp = self.params, self.cfg, self.lcr_params
        run = ph["run"]
        label = run["label"]
        banner = self._phase_banner(idx)
        self._worker_phase = "TSCAN"

        folder = self._make_unique_dir(
            os.path.join(self.save_dir, f"{lp['sample_name']}_{label}"))
        freq_files = {}
        for f in cfg["tscan_freqs"]:
            # int() would collapse 1500.0 and 1500.5 onto ONE file name
            # (silent overwrite); :g keeps fractional Hz distinct.
            ftxt = f"{int(f)}" if f == int(f) else f"{f:g}".replace(".", "p")
            path = os.path.join(
                folder, f"{lp['sample_name']}_{label}-{ftxt}Hz.txt")
            self._write_or_buffer(path,
                                  LCR_Backend.TSCAN_DATA_HEADER + "\n")
            freq_files[f] = path
        run_tlog = os.path.join(
            folder, f"{lp['sample_name']}_{label}_T-log.txt")
        self._write_or_buffer(run_tlog,
                              "DateTime\tElapsed_s\tTemperature_K\n")

        self._put_gui_msg("plot_mode", mode="tscan", label=label)
        self._put_gui_msg("log",
            text=f"Tscan {label}: continuous passive measurement started "
                 f"({len(freq_files)} frequencies/cycle). Files: {folder}")

        det = TurnaroundDetector()
        confirm = SustainedCondition(cfg["confirm_s"])
        top_arm = cfg["top_temp"] - cfg["top_arm_off"]
        expected_s = ph["expected_s"]
        start = time.time()
        last_warn = 0.0
        warned_hot = False
        cycles = 0
        try:
            while self.is_running:
                if self._process_cmd_queue():
                    return "stopped"
                if self._skip_requested:
                    self._skip_requested = False
                    self._put_gui_msg("log",
                        text=f"⏭ Tscan {label} ended early by user.")
                    return "skipped by user"
                if self._paused:
                    temp = self._log_temperature_point(float("nan"), 0)
                    if not self.is_running:
                        return "stopped"
                    self._put_gui_msg("status",
                        text=f"{banner} — PAUSED (Tscan {label})",
                        color=self.CLR_ACCENT_GOLD)
                    time.sleep(1.0)
                    continue

                # --- One measurement cycle: T is read INSIDE the loop so
                # every point is bound to its own temperature. ---
                cycle_points = []
                temp = float("nan")
                for f in cfg["tscan_freqs"]:
                    if not self.is_running or self._skip_requested:
                        break
                    if self._process_cmd_queue():
                        return "stopped"
                    temp = self._log_temperature_point(float("nan"), 1)
                    if not self.is_running:
                        return "stopped"
                    det.update(temp)
                    res = self._lcr_measure_forever(f)
                    if res is None:
                        return "stopped"
                    R, X, status_code = res
                    if status_code != 0:
                        self._put_gui_msg("log",
                            text=f"⚠️ E4980A status {status_code} @ "
                                 f"{f:.1f} Hz (T {temp:.3f} K) — row kept")
                    vals = self.calculate_impedance_parameters(f, R, X)
                    # GLITCH-1: a rejected read (NaN) falls back to the
                    # last valid temperature for the data row.
                    row_T = temp if math.isfinite(temp) else self._last_temp
                    row = [row_T] + vals
                    self._write_or_buffer(
                        freq_files[f],
                        "\t".join(f"{v:.6E}" for v in row) + "\n")
                    cycle_points.append((f, row_T, vals[4], vals[2]))

                if cycle_points:
                    cycles += 1
                    elapsed = time.time() - self.start_time
                    tlog_T = temp if math.isfinite(temp) else self._last_temp
                    self._write_or_buffer(run_tlog,
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\t"
                        f"{elapsed:.1f}\t{tlog_T:.4f}\n")
                    self._put_gui_msg("tscan_cycle", points=cycle_points)

                now = time.time()
                inst = det.cooling_started(top_arm, cfg["fall_k"])
                if confirm.update(inst, now):
                    self._put_gui_msg("log",
                        text=f"TSCAN {label} COMPLETE: T peaked at "
                             f"{det.max_T:.2f} K (arm ≥ {top_arm:g} K), now "
                             f"{det.last_T:.2f} K — cooling held "
                             f"{cfg['confirm_s']/60.0:g} min. {cycles} "
                             f"cycles recorded.")
                    self._put_gui_msg("beep")
                    return "complete (cooling confirmed)"
                if inst:
                    self._put_gui_msg("status",
                        text=(f"{banner} — Tscan {label}: COOLING "
                              f"SUSPECTED, confirming "
                              f"{fmt_hms(confirm.pending_s(now))}/"
                              f"{fmt_hms(cfg['confirm_s'])} | "
                              f"T {det.last_T:.2f} K (max {det.max_T:.2f} K)"),
                        color=self.CLR_STABLE_WAIT)
                elif math.isfinite(det.last_T):
                    eta_s = (max(0.0, cfg["top_temp"] - det.last_T)
                             / cfg["warm_rate"] * 60.0 + cfg["top_hold_s"])
                    self._put_gui_msg("status",
                        text=(f"{banner} — MEASURING Tscan {label} | "
                              f"T {det.last_T:.2f} K → {cfg['top_temp']:g} K "
                              f"| cycle {cycles} | ≈{fmt_hms(eta_s)} to "
                              "run end"),
                        color=self.CLR_ACCENT_GREEN)

                if (not warned_hot and math.isfinite(det.last_T)
                        and det.last_T > 340.0):
                    warned_hot = True
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ SAMPLE ABOVE 340 K ({det.last_T:.2f} K)! "
                             "This program is read-only and cannot act — "
                             "check the PPMS sequence!")
                    self._put_gui_msg("beep")

                elapsed = now - start
                if (expected_s > 0
                        and elapsed > expected_s * 1.25 + 900
                        and now - last_warn >= cfg["overdue_s"]):
                    last_warn = now
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ Tscan {label} OVERDUE: "
                             f"{fmt_hms(elapsed)} elapsed vs expected "
                             f"{fmt_hms(expected_s)}; run-end (cooling) not "
                             f"confirmed yet (T {det.last_T:.2f} K, max "
                             f"{det.max_T:.2f} K). Still measuring.")
                    self._put_gui_msg("beep")
        finally:
            self._worker_phase = None
        return "stopped"

    # ------------------------------------------------------------
    # Phase: FSCAN (temperature-step frequency scans — engine copied
    # from PPMS_Sync_Freq_Scan_E4980A_GUI.py)
    # ------------------------------------------------------------
    def _phase_fscan(self, idx, ph):
        lp = self.lcr_params
        banner = self._phase_banner(idx)
        self._put_gui_msg("plot_mode", mode="fscan", label="")
        self.fscan_dir = self._make_unique_dir(
            os.path.join(self.save_dir, f"{lp['sample_name']}_Fscan"))
        self._put_gui_msg("log",
            text=f"Step Fscan: {len(self.schedule)} setpoints. "
                 f"Files: {self.fscan_dir}")

        total_pts = len(self.schedule) * len(self.sweep_frequencies)
        done_pts = 0
        n_steps = len(self.schedule)

        # AGNOS-1: setpoint-agnostic Fscan — the PPMS still runs the
        # generated schedule, but the scanner stops trusting labels:
        # it scans each detected plateau and labels it with the
        # measured median. One plateau per schedule entry, so the
        # protocol still terminates.
        if self.params.get("agnostic"):
            return self._phase_fscan_agnostic(banner, n_steps, total_pts)

        i = 0
        while self.is_running and i < n_steps:
            target = self.schedule[i]
            self._put_gui_msg("log",
                text=f"--- Fscan step {i+1}/{n_steps}: target {target} K ---")
            self._send_band_msg(target)
            step_start_dt = datetime.now()

            stab_outcome, settle_s = self._wait_for_stability(target, banner)
            if stab_outcome == "stopped" or not self.is_running:
                return "stopped"
            if stab_outcome == "step_skipped":
                # Skip Freq Step during the stability wait: abandon this
                # setpoint entirely — no sweep — and move to the next one.
                self._put_gui_msg("log",
                    text=f"⏭⏭ Step at {target} K abandoned by user (Skip "
                         "Freq Step) — no sweep; moving to the next "
                         "setpoint.")
                self._write_timing_row(
                    i, target, step_start_dt, 0.0, settle_s,
                    stab_outcome, 0.0, False)
                i += 1
                continue
            if stab_outcome == "timeout":
                self._put_gui_msg("log",
                    text=f"⚠️⚠️ STABILIZATION TIMEOUT at {target} K after "
                         f"{settle_s/60.0:.1f} min — proceeding with the "
                         f"sweep anyway (unattended-run policy). "
                         f"Check this data point!")
                # DESYNC-1 (from the Sync GUI): loud mislabel-risk alarm
                # whenever the sample sits outside the guard around the
                # target — the PPMS has probably run ahead.
                p = self.params
                eff_tol = tol_from_table(p.get("tol_table"), target,
                                         p["tol"])
                halfw = p["guard"] if p["guard"] > 0 else 2.0 * eff_tol
                if math.isfinite(self._last_temp) \
                        and abs(self._last_temp - target) > halfw:
                    self._put_gui_msg("log",
                        text=f"⚠️⚠️ DESYNC / MISLABEL RISK: sample reads "
                             f"{self._last_temp:.2f} K but this step is "
                             f"labelled {target} K (> ±{halfw:g} K). The "
                             f"PPMS has probably run ahead of this "
                             f"scanner — lengthen its WAITFOR dwells!")
                    self._put_gui_msg("beep")
                    if p.get("resync"):
                        j = self._resync_step(i)
                        if j is not None:
                            for k in range(i, j):
                                if not self.timing_path:
                                    break
                                # skipped steps: explicit TimingLog rows
                                self._write_or_buffer(
                                    self.timing_path, self._csv_line(
                                        [k + 1,
                                         f"{self.schedule[k]:.4f}",
                                         step_start_dt.strftime(
                                             "%Y-%m-%d %H:%M:%S"),
                                         "0.0", "0.0", "SKIPPED-resync",
                                         "0.0", 0, "0.0", "-"]))
                            i = j
                            continue   # redo stability at the new target
            elif stab_outcome == "forced":
                self._put_gui_msg("log",
                    text=f"⏭ Stability wait skipped by user at {target} K "
                         f"— starting sweep immediately.")

            self._put_gui_msg("log",
                text=f"Starting frequency sweep at {target} K "
                     f"(sample reads {self._last_temp:.3f} K).")
            self._put_gui_msg("status",
                text=f"{banner} — SCANNING AT {target} K",
                color=self.CLR_ACCENT_GREEN)
            self._put_gui_msg("beep")
            self._put_gui_msg("scan_reset", target=target)
            done_pts, scan_s, drift_flag = self._run_frequency_sweep(
                target, done_pts, total_pts, banner)

            self._write_timing_row(
                i, target, step_start_dt, 0.0, settle_s,
                stab_outcome, scan_s, drift_flag)
            if not self.is_running:
                return "stopped"
            self._put_gui_msg("scan_done", target=target)
            self._put_gui_msg("log",
                text=f"Sweep done at {target} K. Proceeding.")
            i += 1
        return "complete" if self.is_running else "stopped"

    def _resync_step(self, i):
        """RESYNC-1 (from Sync v1.8): called on a diverged stabilization
        timeout at step i. Returns the index j > i of the LATER schedule
        setpoint the measured temperature fits best — only if it fits
        strictly better than the current step's setpoint — else None."""
        T = self._last_temp
        if not math.isfinite(T):
            return None
        best, best_d = None, abs(self.schedule[i] - T)
        for j in range(i + 1, len(self.schedule)):
            d = abs(self.schedule[j] - T)
            if d < best_d:
                best, best_d = j, d
        if best is not None:
            self._put_gui_msg("log",
                text=f"⏩ RESYNC: sample at {T:.2f} K matches setpoint "
                     f"{self.schedule[best]:g} K (step {best+1}) better "
                     f"than {self.schedule[i]:g} K (step {i+1}) — jumping "
                     f"forward; steps {i+1}..{best} logged as SKIPPED. "
                     f"Redoing stability detection at the new target.")
            self._put_gui_msg("beep")
        return best

    def _phase_fscan_agnostic(self, banner, n_steps, total_pts):
        """AGNOS-1 (from Sync v1.8): scan every plateau the PPMS makes,
        label each with the measured MEDIAN temperature, complete after
        n_steps plateaus (one per schedule entry — the generated .seq
        makes exactly that many). Nothing can desync because no plateau
        carries a schedule label. Skip Wait scans the current window
        immediately; Skip Freq Step abandons one plateau slot."""
        p = self.params
        done_pts = 0
        scanned = 0
        last_label = None
        self._put_gui_msg("log",
            text=f"AGNOSTIC FSCAN: no setpoint labels — every detected "
                 f"plateau (peak-to-peak ≤ 2×Tol AND |drift| ≤ limit over "
                 f"the {p['window_min']:g}-min window) is scanned and "
                 f"labelled with the measured MEDIAN. Next plateau must "
                 f"differ by ≥ {p['agn_min_dT']:g} K. Completes after "
                 f"{n_steps} plateaus.")
        while self.is_running and scanned < n_steps:
            step_start_dt = datetime.now()
            outcome, label_T, settle_s = self._wait_for_plateau(
                last_label, banner)
            if outcome == "slot_skipped":
                scanned += 1
                self._put_gui_msg("log",
                    text=f"⏭⏭ Plateau slot {scanned}/{n_steps} abandoned "
                         "by user (Skip Freq Step) — no sweep.")
                continue
            if outcome != "plateau" or not self.is_running:
                return "stopped"
            scanned += 1
            self._put_gui_msg("log",
                text=f"Plateau {scanned}/{n_steps} detected at "
                     f"{label_T:.3f} K after {fmt_hms(settle_s)} — "
                     f"starting sweep.")
            self._put_gui_msg("status",
                text=f"{banner} — SCANNING AT {label_T:.2f} K",
                color=self.CLR_ACCENT_GREEN)
            self._put_gui_msg("beep")
            self._put_gui_msg("scan_reset", target=label_T)
            done_pts, scan_s, drift_flag = self._run_frequency_sweep(
                label_T, done_pts, total_pts, banner)
            self._write_timing_row(
                scanned - 1, label_T, step_start_dt, 0.0,
                settle_s, "plateau", scan_s, drift_flag)
            if not self.is_running:
                return "stopped"
            self._put_gui_msg("scan_done", target=label_T)
            last_label = label_T
            if scanned < n_steps:
                self._put_gui_msg("log",
                    text=f"Sweep done at {label_T:.3f} K. Watching for "
                         f"plateau {scanned + 1}/{n_steps} "
                         f"(≥ {p['agn_min_dT']:g} K away)…")
        return "complete" if self.is_running else "stopped"

    def _wait_for_plateau(self, last_label, banner=""):
        """AGNOS-1 stability wait: SELF-referenced — no target. The
        rolling window must span the configured length with peak-to-peak
        ≤ 2×Tol (tolerance table evaluated at the window MEAN) and
        |drift| ≤ the limit. A plateau within agn_min_dT of the last
        scanned one does not count (still the same plateau). The
        stabilization timeout only logs a heads-up here — with no label
        to get wrong there is nothing to force. Skip Wait scans the
        current window immediately; Skip Freq Step abandons the slot.
        Returns (outcome, label_T, wait_s); outcome
        'plateau' | 'slot_skipped' | 'stopped'; label_T = window median."""
        p = self.params
        self._worker_phase = "WAIT_STABLE"
        window = deque()
        phase_start = time.time()
        paused_s = 0.0
        last_status = 0.0
        timeout_warned = False

        def window_median():
            return float(np.median([w[1] for w in window]))

        try:
            while self.is_running:
                if self._process_cmd_queue():
                    break
                if self._skip_requested:
                    self._skip_requested = False
                    if window:
                        self._put_gui_msg("log",
                            text="⏭ Plateau wait skipped by user — "
                                 "scanning the current temperature.")
                        return ("plateau", window_median(),
                                time.time() - phase_start - paused_s)
                if self._skip_step_requested:
                    self._skip_step_requested = False
                    return ("slot_skipped", float("nan"),
                            time.time() - phase_start - paused_s)
                temp = self._log_temperature_point(float("nan"),
                                                   measuring_flag=0)
                now = time.time()
                if self._paused:
                    paused_s += p["delay"]
                    window.clear()
                    self._put_gui_msg("status",
                        text=f"{banner} — PAUSED (plateau watch)",
                        color=self.CLR_ACCENT_GOLD)
                    time.sleep(p["delay"])
                    continue
                if math.isfinite(temp):   # GLITCH-1 filtered upstream
                    window.append((now, temp))
                soak_s = p["window_min"] * 60.0
                while window and (now - window[0][0]) > soak_s:
                    window.popleft()

                ok = False
                m = None
                if len(window) >= 5:
                    t0 = window[0][0]
                    temps = np.array([w[1] for w in window])
                    times = np.array([w[0] - t0 for w in window])
                    span = window[-1][0] - t0
                    pkpk = float(np.max(temps) - np.min(temps))
                    mean = float(np.mean(temps))
                    med = float(np.median(temps))
                    drift = 0.0
                    if span > 1.0:
                        drift = float(np.polyfit(times, temps, 1)[0]) * 60.0
                    tol = tol_from_table(p.get("tol_table"), mean, p["tol"])
                    m = {"span": span, "pkpk": pkpk, "mean": mean,
                         "med": med, "drift": drift, "tol": tol}
                    ok = (span >= 0.95 * soak_s
                          and pkpk <= 2.0 * tol
                          and abs(drift) <= p["drift"])
                    if ok and last_label is not None \
                            and abs(med - last_label) < p["agn_min_dT"]:
                        ok = False   # still sitting on the scanned plateau
                if ok:
                    wait_s = time.time() - phase_start - paused_s
                    self._put_gui_msg("log",
                        text=f"PLATEAU at {m['med']:.3f} K (median; mean "
                             f"{m['mean']:.3f} K, p-p {m['pkpk']:.3f} K, "
                             f"drift {m['drift']:+.3f} K/min, tol "
                             f"±{m['tol']:g} K) after {fmt_hms(wait_s)}.")
                    return "plateau", m["med"], wait_s

                if now - last_status > 3.0:
                    last_status = now
                    if m is not None:
                        fill = min(100.0, 100.0 * m["span"] / soak_s)
                        extra = ""
                        if last_label is not None \
                                and abs(m["med"] - last_label) \
                                < p["agn_min_dT"]:
                            extra = (f" | still at scanned "
                                     f"{last_label:.2f} K plateau")
                        self._put_gui_msg("status",
                            text=(f"{banner} — PLATEAU WATCH "
                                  f"~{m['med']:.2f} K | "
                                  f"window {fill:.0f}% | "
                                  f"p-p {m['pkpk']:.3f} K | "
                                  f"drift {m['drift']:+.3f} K/min"
                                  + extra),
                            color=self.CLR_STABLE_WAIT)
                        # band drawn around the CURRENT median
                        self._put_gui_msg("band", center=m["med"],
                                          halfw=m["tol"])

                if (not timeout_warned and p["stab_timeout"] > 0
                        and (now - phase_start - paused_s)
                        > p["stab_timeout"] * 60.0):
                    timeout_warned = True
                    self._put_gui_msg("log",
                        text=f"Note: no plateau within "
                             f"{p['stab_timeout']:g} min — still watching "
                             "(agnostic mode has no label to get wrong).")

                time.sleep(p["delay"])
        finally:
            self._worker_phase = None
        return "stopped", float("nan"), time.time() - phase_start - paused_s

    # ------------------------------------------------------------
    # Fscan worker phases (copied from PPMS_Sync)
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

    def _window_check(self, window, target, p):
        """Rolling-window stability test with three modes (see the
        stabilization panel). Returns (ok, metrics)."""
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

    def _wait_for_stability(self, target, banner=""):
        """Rolling-window stabilization detection.
        Returns (outcome, wait_s) with outcome in
        "stable" | "timeout" | "forced" | "step_skipped" | "stopped".
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
            text=f"{banner} — WAITING FOR STABILITY at {target} K",
            color=self.CLR_STABLE_WAIT)
        try:
            while self.is_running:
                if self._process_cmd_queue():
                    return "stopped", time.time() - phase_start - paused_s
                if self._skip_requested:
                    self._skip_requested = False
                    return "forced", time.time() - phase_start - paused_s
                if self._skip_step_requested:
                    self._skip_step_requested = False
                    return ("step_skipped",
                            time.time() - phase_start - paused_s)

                temp = self._log_temperature_point(target, measuring_flag=0)
                now = time.time()

                if self._paused:
                    paused_s += p["delay"]
                    window.clear()
                    self._put_gui_msg("status",
                        text=f"{banner} — PAUSED (stability wait, target "
                             f"{target} K)",
                        color=self.CLR_ACCENT_GOLD)
                    time.sleep(p["delay"])
                    continue

                if math.isfinite(temp):   # GLITCH-1: never poison the window
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
                            text=(f"{banner} — WAITING STABILITY {target} K "
                                  f"| window {fill:.0f}% | "
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

    def _run_frequency_sweep(self, target_temp, done_pts, total_pts,
                             banner=""):
        """Runs the E4980A frequency sweep at one stable setpoint.
        Logs temperature (flag=1) interleaved between frequency points.
        Watches for the sample temperature leaving the stability band
        mid-sweep (flag-only: the sweep always completes). Comm errors
        reconnect forever and resume at the same point; every row is
        fsync'd to disk. Returns (done_pts, scan_s, drift_flag)."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # FNAME-1: the filename carries the MEASURED temperature ('p'
        # decimal mark). Provisional name = start-of-sweep probe reading;
        # _finalize_scan_header renames to the sweep MEDIAN when the
        # sweep completes. The commanded setpoint lives only in the header.
        name_T = self._last_temp if math.isfinite(self._last_temp) \
            else target_temp
        fname = (f"{self.lcr_params['sample_name']}_{fmt_temp_p(name_T)}K_"
                 f"{stamp}_FreqScan.txt")
        fpath = os.path.join(self.fscan_dir, fname)
        sweep_start = time.time()
        paused_s = 0.0
        drift_flag = False
        # Drift reference: the temperature the sweep started at (flatness
        # modes settle at an offset from the target, so the target itself
        # is the wrong reference there).
        ref_T = target_temp if self.params["mode"] == "band" \
            else (self._last_temp if math.isfinite(self._last_temp)
                  else target_temp)
        drift_halfw = 2.0 * tol_from_table(
            self.params.get("tol_table"), target_temp,
            self.params["tol"])
        n_measured = 0
        sweep_temps = []   # DATA-1: valid measured T over the sweep
        self._write_or_buffer(
            fpath,
            f"# Sample: {self.lcr_params['sample_name']} | T_set = {target_temp} K "
            f"(commanded setpoint; T_sample(K) column = measured probe) | "
            f"T_sample_start = {self._last_temp:.4f} K | "
            f"AC: {self.lcr_params['ac_bias']} V | DC: {self.lcr_params['dc_bias']} V | "
            f"APER: {self.lcr_params['aper']}\n"
            + LCR_Backend.FSCAN_DATA_HEADER + "\n")
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
            if self._skip_step_requested:
                # Mid-sweep, Skip Freq Step == abort the remainder of
                # this sweep; the protocol then proceeds to the next
                # setpoint exactly as Skip Phase would.
                self._skip_step_requested = False
                self._put_gui_msg("log",
                    text="⏭⏭ Remaining sweep skipped (Skip Freq Step) — "
                         "moving to the next setpoint.")
                break
            while self._paused and self.is_running:
                pause_tick = time.time()
                self._put_gui_msg("status",
                    text=f"{banner} — PAUSED (sweep at {target_temp} K)",
                    color=self.CLR_ACCENT_GOLD)
                if self._process_cmd_queue():
                    break
                if self._skip_requested or self._skip_step_requested:
                    break   # Skip must be able to break out of a pause too
                time.sleep(1.0)
                paused_s += time.time() - pause_tick
            if not self.is_running:
                break
            if self._skip_requested:
                self._skip_requested = False
                self._put_gui_msg("log",
                    text="⏭ Remaining sweep skipped by user.")
                break
            if self._skip_step_requested:
                self._skip_step_requested = False
                self._put_gui_msg("log",
                    text="⏭⏭ Remaining sweep skipped (Skip Freq Step) — "
                         "moving to the next setpoint.")
                break
            res = self._lcr_measure_forever(freq)
            if res is None:
                break   # Stop requested during recovery
            R, X, status = res
            if status != 0:
                self._put_gui_msg("log",
                    text=f"⚠️ E4980A status {status} @ {freq:.1f} Hz — "
                         "row kept, check manual")
            vals = self.calculate_impedance_parameters(freq, R, X)
            # DATA-1: read the probe BEFORE writing the row so each row
            # carries the temperature at which that point was measured;
            # a glitched read (NaN) falls back to the last valid value.
            temp = self._log_temperature_point(target_temp,
                                               measuring_flag=1)
            row_T = temp if math.isfinite(temp) else self._last_temp
            if math.isfinite(row_T):
                sweep_temps.append(row_T)
            row = [freq] + vals + [row_T]
            self._write_or_buffer(
                fpath, "\t".join(f"{v:.6E}" for v in row) + "\n")
            n_measured += 1
            if not self.is_running:
                break
            if math.isfinite(temp) and not drift_flag \
                    and abs(temp - ref_T) > drift_halfw:
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
                per_pt = (time.time() - sweep_start - paused_s) \
                    / max(n_measured, 1)
                eta = per_pt * (n_freqs - i - 1)
                self._put_gui_msg("status",
                    text=(f"{banner} — SCANNING AT {target_temp} K | "
                          f"pt {i+1}/{n_freqs} | ETA {fmt_hms(eta)}"),
                    color=self.CLR_ACCENT_GREEN)
        self._worker_phase = None
        scan_s = time.time() - sweep_start - paused_s
        # DATA-1/FNAME-1: median into the TOP comments (no footers) and
        # the file renamed to the median measured temperature.
        fname = self._finalize_scan_header(
            fpath, target_temp, sweep_temps, stamp) or fname
        if n_measured >= 20:
            # Refine the live scan estimate with real per-point timing
            self._put_gui_msg("point_time", avg=scan_s / n_measured)
        self._put_gui_msg("log", text=f"Sweep saved: {fname} "
                                      f"({n_measured} pts, {fmt_hms(scan_s)}).")
        return done_pts, scan_s, drift_flag

    def _finalize_scan_header(self, fpath, target_temp, sweep_temps,
                              stamp):
        """DATA-1/FNAME-1 (from the Sync GUI): after the sweep,
        (a) insert the measured MEDIAN sample temperature as a second
        '#' comment at the TOP of the scan file (no footers — user
        decision 2026-07-23) and (b) rename the file so its name
        carries that median ('p' decimal mark, e.g.
        Sample_80p05K_<stamp>_FreqScan.txt). One atomic step: the
        updated content is written to a temp file and os.replace'd onto
        the FINAL name, then the provisional file is removed — a crash
        at any point leaves a complete file under one of the two names,
        never a corrupt one.
        Returns the final filename, or None if nothing was finalized."""
        if not sweep_temps:
            return None
        med_T = float(np.median(sweep_temps))
        line = (f"# T_sample_median = {med_T:.4f} K over "
                f"{len(sweep_temps)} readings | T_set = {target_temp} K "
                f"| deviation = {med_T - target_temp:+.4f} K\n")
        new_fname = (f"{self.lcr_params['sample_name']}_"
                     f"{fmt_temp_p(med_T)}K_{stamp}_FreqScan.txt")
        new_path = os.path.join(self.fscan_dir, new_fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            lines.insert(1, line)
            tmp = fpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, new_path)
            if os.path.abspath(new_path) != os.path.abspath(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass   # provisional copy left behind is harmless
            self._put_gui_msg("log",
                text=f"Scan finalized as {new_fname}: T_sample_median "
                     f"{med_T:.3f} K (T_set {target_temp} K, deviation "
                     f"{med_T - target_temp:+.3f} K).")
            return new_fname
        except OSError as e:
            self._put_gui_msg("log",
                text=f"⚠️ Could not finalize the scan file ({e}) — data "
                     f"is intact under its provisional name; median for "
                     f"this sweep: {med_T:.4f} K.")
            return None

    def _write_timing_row(self, index, target, step_start_dt, sleep_s,
                          settle_s, stab_outcome, scan_s, drift_flag):
        """One row per completed Fscan step: measured timings + the PPMS
        wait suggestion (settle + scan + Margin) for tightening the next
        run of the same sequence."""
        if not self.timing_path:
            return
        p = self.params
        margin_s = p["margin_min"] * 60.0
        suggest_s = sleep_s + settle_s + scan_s + margin_s
        self._write_or_buffer(self.timing_path, self._csv_line(
            [index + 1, f"{target:.4f}",
             step_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
             f"{sleep_s:.1f}", f"{settle_s:.1f}", stab_outcome,
             f"{scan_s:.1f}", int(drift_flag),
             f"{suggest_s:.1f}", fmt_hms(suggest_s)]))
        self._put_gui_msg("log",
            text=f"→ Suggested PPMS wait at {target} K: {fmt_hms(suggest_s)} "
                 f"(settle {fmt_hms(sleep_s + settle_s)} + scan "
                 f"{fmt_hms(scan_s)} + margin {p['margin_min']:g} min).")

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
                elif kind == "skip_freq":
                    # Only meaningful inside an Fscan step; setting the
                    # flag in any other phase would silently skip the
                    # FIRST Fscan step hours later.
                    if self._worker_phase in ("WAIT_STABLE", "SCAN"):
                        self._skip_step_requested = True
                    else:
                        self._put_gui_msg("log",
                            text="Skip Freq Step ignored — it only applies "
                                 "during an Fscan step (stability wait or "
                                 "sweep). Use Skip Phase for cooldowns / "
                                 "Tscan.")
                elif kind == "params":
                    updates = cmd[1]
                    self.params.update(updates)
                    self._put_gui_msg("log", text=f"Params applied: {updates}")
        except queue.Empty:
            pass
        return False

    @staticmethod
    def _make_unique_dir(path):
        """Create path; if it already exists, append a timestamp so a
        repeated run never silently mixes with previous data."""
        if os.path.exists(path):
            path = f"{path}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(path, exist_ok=True)
        return path

    # ------------------------------------------------------------
    # Durable, buffered file writes (worker thread only)
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

    def _write_runstate(self, status, detail=""):
        """Crash/restart aid: OVERWRITE a tiny RUNSTATE file on every
        phase transition, so after a power cut the last known state of
        the protocol is on disk next to the data. Best-effort only — a
        failure here must never disturb the run."""
        if not self.runstate_path:
            return
        try:
            with open(self.runstate_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "PICA Dielectric Master run state "
                    "(rewritten at every phase transition)\n"
                    f"updated: "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"status: {status}\n"
                    f"phase: {self._phase_index + 1}/"
                    f"{len(self.protocol_phases)}\n"
                    f"last_T_K: {self._last_temp:.3f}\n"
                    + (f"detail: {detail}\n" if detail else ""))
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            pass

    # ------------------------------------------------------------
    # Retry-forever comm recovery (worker thread only)
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

    # Class-level defaults so minimal worker harnesses (tests) that build
    # the object via object.__new__ still read sane values.
    _last_temp = float("nan")
    _skip_step_requested = False
    runstate_path = None
    _glitch_candidate = None   # GLITCH-1: last rejected "jump" reading
    _glitch_total = 0
    _invalid_streak = 0        # CC34-8: consecutive polls never recovered
    _invalid_recoveries = 0    # CC34-8: points saved by a re-read
    _sensor_down_logged = False

    def _validate_probe_reading(self, raw):
        """GLITCH-1: True if the reading is physically plausible.
        Rejects the 0.000-K sensor dropout (raw ≤ 1 K) and any
        jump > GLITCH_JUMP_K from the last valid reading in one poll.
        A REAL large step (e.g. after a long comm outage) is accepted
        once two consecutive readings agree within GLITCH_CONFIRM_K."""
        if raw is None or not math.isfinite(raw) or raw <= GLITCH_LOW_K:
            self._glitch_candidate = None
            return False
        if math.isfinite(self._last_temp) \
                and abs(raw - self._last_temp) > GLITCH_JUMP_K:
            cand = self._glitch_candidate
            if cand is not None and abs(raw - cand) <= GLITCH_CONFIRM_K:
                self._glitch_candidate = None
                self._put_gui_msg("log",
                    text=f"Large temperature step CONFIRMED by two "
                         f"consecutive readings ({cand:.2f} / {raw:.2f} K) "
                         f"— accepting.")
                return True
            self._glitch_candidate = raw
            return False
        self._glitch_candidate = None
        return True

    def _retry_invalid_reading(self, temp):
        """CC34-8: re-read an invalid point instead of spending it.

        A Cryo-con status reply (dashes during a range switch, dots off
        the calibration curve) and a one-off garbage spike both usually
        clear within a second. Keep re-reading for up to
        INVALID_READ_RETRY_S; the moment a reading validates it is
        returned and no data point is lost. Re-reading also lets GLITCH-1
        confirm a REAL large step right here - two consecutive agreeing
        readings - instead of waiting a whole poll for it.

        Returns (temperature, valid). Stop stays responsive throughout:
        the command queue is polled between re-reads.

        A sustained fault degrades to one quick re-read per poll (see the
        constants) so a dead sensor cannot stretch every sweep and walk
        the scanner out of step with the PPMS. The run keeps moving and
        keeps logging evidence either way, and because every later poll
        retries as well, recovery whenever the sensor comes back is
        automatic. Comm failures are not handled here - they retry
        forever in the caller via _reconnect_with_backoff.
        """
        window = INVALID_READ_RETRY_S
        if window <= 0:
            return temp, False
        if self._invalid_streak >= INVALID_READ_STREAK_MAX:
            # Sensor looks genuinely down: stop paying the full window on
            # every point, but never stop looking.
            window = INVALID_READ_POLL_S * 2
            if not self._sensor_down_logged:
                self._sensor_down_logged = True
                self._put_gui_msg("log",
                    text=f"⚠️ Probe has not returned a valid reading for "
                         f"{self._invalid_streak} polls — treating the "
                         "sensor as down. Still re-reading every poll, "
                         "and the run continues; readings resume "
                         "automatically the moment it recovers.")
                self._put_gui_msg("beep")
        started = time.time()
        deadline = started + window
        tries = 0
        while self.is_running and time.time() < deadline:
            if self._process_cmd_queue():
                break                      # Stop requested
            time.sleep(INVALID_READ_POLL_S)
            tries += 1
            try:
                temp = self.thermo_backend.get_temperature(
                    self.params["channel"])
            except Exception as e:
                # A comm failure mid-recovery belongs to the caller's
                # retry-forever reconnect loop, not to a second copy here.
                self._put_gui_msg("log",
                    text=f"⚠️ Probe re-read failed ({e}) — handing over "
                         "to the reconnect loop.")
                break
            if self._validate_probe_reading(temp):
                if self._sensor_down_logged:
                    self._put_gui_msg("log",
                        text="Probe sensor is reading again.")
                self._invalid_streak = 0
                self._sensor_down_logged = False
                self._invalid_recoveries += 1
                self._put_gui_msg("log",
                    text=f"Probe read recovered after {tries} re-read"
                         f"{'' if tries == 1 else 's'} "
                         f"({time.time() - started:.1f} s): {temp:.3f} K "
                         "— no point lost.")
                return temp, True
        self._invalid_streak += 1
        return temp, False

    def _log_temperature_point(self, target, measuring_flag):
        """Reads the probe thermometer, writes a durable CSV row, queues
        the plot message. On a comm failure the thermometer is
        reconnected with escalating backoff, forever; returns NaN only
        if Stop was requested during recovery. Read-only — no safety
        actions exist because this program controls no heater.
        target=NaN for phases with no setpoint (cooldowns, Tscan)."""
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
        # GLITCH-1: validate before ANYTHING uses the reading. Invalid
        # reads are still logged (flagged) but never become _last_temp,
        # never enter a stability window and never reach the plot.
        valid = self._validate_probe_reading(temp)
        # CC34-8: an invalid reading is RETRIED, not spent — a status
        # reply or a lone garbage spike normally clears within a
        # second, and re-reading here saves the point.
        if not valid:
            temp, valid = self._retry_invalid_reading(temp)
        if valid:
            self._invalid_streak = 0
            self._last_temp = temp
        else:
            self._glitch_total += 1
            if self._glitch_total <= 5 or self._glitch_total % 25 == 0:
                self._put_gui_msg("log",
                    text=f"⚠️ INVALID PROBE READ #{self._glitch_total}: "
                         f"{temp:.3f} K (last valid "
                         f"{self._last_temp:.3f} K) — ignored, logged "
                         f"as GLITCH in the TempLog.")
        elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        phase = "PAUSED" if self._paused else (self._worker_phase or "")
        if not valid:
            phase = (phase + "|GLITCH") if phase else "GLITCH"
        no_target = (target is None
                     or (isinstance(target, float) and math.isnan(target)))
        tgt_txt = "" if no_target else f"{target:.4f}"
        self._write_or_buffer(self.tlog_path, self._csv_line(
            [now_str, f"{elapsed:.2f}", tgt_txt, f"{temp:.4f}",
             measuring_flag, phase]))
        if valid:
            self._put_gui_msg("temp_point", t=elapsed, temp=temp,
                              target=(float("nan") if no_target else target),
                              measuring=measuring_flag)
        return temp if valid else float("nan")

    def _lcr_measure_forever(self, freq):
        """One E4980A measurement with retry-forever comm recovery.
        Returns (R, X, status) or None if Stop was requested."""
        attempt = 0
        while True:
            try:
                return self.lcr_backend.perform_measurement(
                    freq, self.lcr_params["delay"])
            except Exception as e:
                attempt += 1
                self._put_gui_msg("log",
                    text=f"⚠️ LCR COMM ERROR @ {freq} Hz "
                         f"(failure #{attempt}): {e}")
                if not self._reconnect_with_backoff(
                        "E4980A", self.lcr_backend.reconnect, attempt):
                    return None

    def _close_data_file(self):
        """Files are opened per append, so there is nothing to close —
        just retry any rows a failing disk left buffered."""
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
            if messagebox.askyesno("Exit",
                                   "Protocol is running. Stop and exit?"):
                self.stop_protocol("User closed application.")
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
    PPMSMasterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
