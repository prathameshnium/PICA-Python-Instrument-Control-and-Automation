'''
 PROGRAM:      Novocontrol Alpha-AN CV Scan (Zs', Zs'') GUI
 VERSION:      1.0 (Alpha-AN port, 22 Oct 2026)
 PORT OF:      keysight/CV_KE4980A_GUI.py v2.0. The waveform (linear, cycle
               shapes, ladder), the per-frequency files, columns, header,
               END lines, plots and the no-pop-up rule are unchanged; only
               the instrument layer is replaced by the Alpha-AN backend
               copied from novocontrol/Frequency_Scan_AlphaAN_GUI.py
               (EOI-only framing, every set command's "OK" ack read, MST
               written directly with SRQ completion, ZRE? result, ZG4 AC
               ceiling, safe state on every exit). What changed with it:
                 - AC level -> ACV (Vrms, ZG4 ceiling 3/2/1 Vrms by
                   frequency); aperture -> MTM integration time (0.5 s);
                   ALC, open/short correction and cable length have no
                   Alpha equivalent and are gone; a ZG4 wire-mode choice
                   (2/3/4, default 2) is new.
                 - R, X from :FETC? -> Zs', Zs'' from ZRE?; the 18 derived
                   values use the frequency the Alpha actually produced
                   (f_actual, new last column), files stay keyed by the
                   requested one. Status 2 is valid, status 6 aborts the
                   run, every other status keeps its row (raw Zs', Zs'',
                   Status) with NaN derived values.
                 - The Alpha has no verified monitor queries, so the four
                   *_mon columns are kept for the file format but are NaN.
                 - No bus-error retry: the Alpha is driven under the lab's
                   gentle-GPIB rule (one attempt per operation; the only
                   recoveries are the backend's two: *IDN? retried on ENOL
                   or timeout (with a device clear) after *RST and at
                   connect, one Interface Clear on ECIC). A bus error ends the
                   run with the bias ramped to 0 V and switched off.
               Not yet run on the hardware.

 FILE FORMAT (tab separated, plotter compatible):
   '#' header blocks ([instrument], [sample], [sweep], [frequency], [alpha],
   [timing]), one 'waveform:' line in words, the column row, one data row
   per point (flushed and fsynced), then '# END reason=... | points=...'.
   Columns: Voltage(V), Frequency and the 18 derived columns as before,
   then t_s, Timestamp, Cycle, Segment, Vdc_mon(V), Idc_mon(A), Vac_mon(V),
   Iac_mon(A), R_raw, X_raw, Status, f_actual(Hz). R_raw/X_raw are Zs'/Zs''
   from ZRE?. Derived columns are NaN for every status but 2 (valid). The
   END lines carry bias=off, or bias=BIAS_OFF_FAILED when the bias-off could
   not be confirmed (also a red banner in the window).
   FILE, PRE_RAMP, BUS_ERROR, BIAS_OFF_FAILED and END events also go to
   <sample>_<ts>_CV_log.txt.

 LOOP SHAPES (cycle mode): positive first, negative first, unipolar +/-,
   and ladder (one bipolar cycle per listed amplitude, as in the memristor
   sweep module; Cycle = ladder entry number, ladder_list_V in the header;
   optional pause at 0 V between entries, ladder_pause_s, default 0).

 SAFETY: This is the one PICA module that uses the Alpha's DC bias: the
   bias is the C-V staircase itself. It is hard-capped at +/-20 V
   (V_ABS_MAX, never raised; checked before any byte is sent, again in
   the ramp and again where the command is formed), switched on at 0 V,
   ramped in 0.5 V steps, and on every exit (end, Stop, error, crash,
   window close) ramped back to 0 V and switched off BEFORE the generator
   safe state (MBK, ACV=0, ZCONSPL=0) and *RST. The DCV/DCE syntax is
   UNVERIFIED against the Alpha manual (see AlphaAN_Backend.
   _dc_bias_command, the only place a bias command is formed).

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

 Pure functions and the link/worker classes carry no Tk so the run path is
 testable with a fake instrument (tests/test_cv_alphaan.py).
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
import queue
import sys  # TerminalTee swaps sys.stdout / sys.stderr
import threading
import traceback
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
from multiprocessing import Process
import runpy
import atexit

VERSION = "1.0"
MODULE_NAME = "CV_AlphaAN_GUI"

# --- Absolute hardware safety limits (Alpha-AN DC bias) ---
V_ABS_MAX = 20.0        # Hardcoded DC bias ceiling. NEVER raise this.
                        # (The Alpha-A technical data gives +/-40 V with
                        # mainframe option B; this unit's maximum is not yet
                        # confirmed. 20 V is the lab's standing C-V cap, the
                        # same as the E4980A C-V module; tested to +/-20 V
                        # on this Alpha, Oct 2026, as WinDETA also allows.)
# The AC ceiling is the ZG4's frequency-dependent one (acv_ceiling(),
# validate_alpha_parameters() in the backend block below).

# --- Bias ramp (the only soft start the instrument gets) ---
RAMP_STEP_V = 0.5       # volts per DCV command while ramping the DC bias
RAMP_DWELL_S = 0.1      # seconds between ramp commands
RAMP_SKIP_V = 1e-4      # |target - current| below this: no command at all
V_STEP_MIN = 1e-3       # smallest sweep step accepted (V); 1 mV = DCV resolution

# --- Timing ---
BIAS_ON_SETTLE_S = 0.5  # after the bias is switched on at 0 V
MTM_DEFAULT_S = 0.5     # integration time: the lab's WinDETA definition
                        # ("Maximum of 0.500 Seconds or 1 Periods")

# ZRE? status: only 2 (Result valid) is written as data. Any other status
# (0 buffer empty, 1 still measuring, 3/4/5 range errors) keeps its row,
# with the raw Zs', Zs'' and the Status column, but the 18 derived columns
# are NaN so it is never read as a good point; 6 (signal source
# disconnected) aborts the run.

# Loop shapes (cycle mode). Segment numbers are the leg order within a cycle.
SHAPE_POS_FIRST = "positive first: 0 -> +V -> 0 -> -V -> 0"
SHAPE_NEG_FIRST = "negative first: 0 -> -V -> 0 -> +V -> 0"
SHAPE_UNI_POS = "unipolar positive: 0 -> +V -> 0"
SHAPE_UNI_NEG = "unipolar negative: 0 -> -V -> 0"
SHAPE_LADDER = "ladder: 0 -> +V1 -> 0 -> -V1 -> 0, then V2, V3 ... (one cycle per listed V)"
SHAPES = (SHAPE_POS_FIRST, SHAPE_NEG_FIRST, SHAPE_UNI_POS, SHAPE_UNI_NEG, SHAPE_LADDER)
SHAPE_SIGNS = {
    SHAPE_POS_FIRST: (1, -1),
    SHAPE_NEG_FIRST: (-1, 1),
    SHAPE_UNI_POS: (1,),
    SHAPE_UNI_NEG: (-1,),
    SHAPE_LADDER: (1, -1),
}

# Window fields the mode/shape ignores (greyed in the window, written as
# n/a in the header, never validated), as disabled_shape_fields() in the
# memristor sweep module. Keys are the entry names.
SWEEP_FIELDS = ("v_start", "v_stop", "n_cycles", "shape", "ladder", "ladder_pause")


def disabled_fields(cycle_mode, shape):
    """Entry keys the current mode and shape ignore."""
    if not cycle_mode:
        return {"n_cycles", "shape", "ladder", "ladder_pause"}
    if shape == SHAPE_LADDER:
        return {"v_start", "v_stop", "n_cycles"}
    return {"v_start", "ladder", "ladder_pause"}


DERIVED_HEADER = (
    "Q\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\t"
    "R(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''"
)
EXTRA_HEADER = (
    "t_s\tTimestamp\tCycle\tSegment\tVdc_mon(V)\tIdc_mon(A)\t"
    "Vac_mon(V)\tIac_mon(A)\tR_raw\tX_raw\tStatus\tf_actual(Hz)"
)
COLUMN_ROW = "Voltage(V)\tFrequency\t" + DERIVED_HEADER + "\t" + EXTRA_HEADER
N_DERIVED = 18


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
# PURE FUNCTIONS - waveform, formulas, formatting (no Tk, no instrument)
# ===============================================================================

class BusError(Exception):
    """A VISA failure while opening the link. No retry (gentle GPIB): the
    worker ends the run on it, with the bias ramped to 0 V and off."""


BIAS_OFF_FAILED_TEXT = (
    "BIAS_OFF_FAILED: the DC bias could NOT be confirmed 0 V and off. "
    "The sample may still be biased: switch the bias off at the Alpha "
    "(or WinDETA) and discharge the sample before touching it.")


class StopRequested(Exception):
    """Raised inside the worker when Stop is pressed."""


def ascii_text(text):
    """ASCII-safe text for file headers (lab PCs read files as cp1252)."""
    return str(text).encode("ascii", "replace").decode("ascii")


def header_value(value):
    """One header value: ASCII, no newlines, no ' | ' separators inside."""
    return ascii_text(value).replace("\n", " ").replace("\r", " ").replace(" | ", " / ")


def fmt_cell(value):
    """One data cell: 6-digit exponent form; NaN spelled out for genfromtxt."""
    if isinstance(value, str):
        return value
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "NaN"
        if isinstance(value, (bool, np.bool_)):
            return str(int(value))
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        return f"{float(value):.6E}"
    except (TypeError, ValueError):
        return "NaN"


def iso_now(now=None):
    """ISO time without spaces, so the Timestamp column is one token."""
    return (now or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def timestamp_for_name(now=None):
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def wait_with_stop(seconds, stop_event, slice_s=0.05, sleep=time.sleep):
    """Sleep `seconds` in slices; True when stop_event was set meanwhile."""
    end = time.perf_counter() + max(0.0, seconds)
    while True:
        if stop_event is not None and stop_event.is_set():
            return True
        remaining = end - time.perf_counter()
        if remaining <= 0:
            return False
        sleep(min(slice_s, remaining))


def _ramp(a, b, step):
    """Staircase from a to b inclusive (b hit exactly, no duplicate of b)."""
    d = 1 if b >= a else -1
    pts = list(np.arange(a, b, d * step))
    if pts and abs(pts[-1] - b) < 1e-9:
        pts.pop()
    pts.append(b)
    return [float(round(v, 9)) for v in pts]


def half_leg(vmax, step):
    """0 (exclusive) to vmax (inclusive)."""
    return _ramp(0.0, vmax, step)[1:]


def return_leg(top, step):
    """top (exclusive) back to 0 (inclusive)."""
    return _ramp(top, 0.0, step)[1:]


def build_cycle(shape, v_max, v_step):
    """One cycle as a list of (segment, v); starts after 0, ends at 0."""
    out = []
    seg = 0
    for sign in SHAPE_SIGNS[shape]:
        seg += 1
        out.extend((seg, v) for v in half_leg(sign * abs(v_max), v_step))
        seg += 1
        out.extend((seg, v) for v in return_leg(sign * abs(v_max), v_step))
    return out


def build_waveform(cycle_mode, shape, v_max, v_start, v_stop, v_step, n_cycles, ladder=None):
    """The full bias sequence of one file as (cycle, segment, v) tuples.

    Cycle mode: a measured 0 V point first (cycle 1, segment 1), then
    n_cycles of the chosen shape, each ending at 0 V. Ladder shape: one
    bipolar cycle per listed amplitude, Cycle = ladder entry number.
    Linear mode: one staircase v_start -> v_stop, cycle 1, segment 1."""
    v_step = abs(float(v_step))
    if v_step <= 0:
        raise ValueError("Voltage step must be > 0.")
    if cycle_mode and shape == SHAPE_LADDER:
        if not ladder:
            raise ValueError("Ladder shape needs a ladder list, e.g. 0.5, 1, 2.")
        pts = [(1, 1, 0.0)]
        for k, amp in enumerate(ladder):
            pts.extend((k + 1, seg, v) for seg, v in build_cycle(SHAPE_LADDER, abs(amp), v_step))
        return pts
    if cycle_mode:
        if n_cycles < 1:
            raise ValueError("Number of cycles must be >= 1.")
        if abs(v_max) <= 0:
            raise ValueError(
                "Cycle mode requires a non-zero Stop Voltage "
                "(V Stop sets the cycle amplitude)."
            )
        pts = [(1, 1, 0.0)]
        one = build_cycle(shape, abs(v_max), v_step)
        for c in range(1, n_cycles + 1):
            pts.extend((c, seg, v) for seg, v in one)
        return pts
    return [(1, 1, v) for v in _ramp(float(v_start), float(v_stop), v_step)]


def segment_text(cycle_mode, shape):
    """What the Segment column means in this file."""
    if not cycle_mode:
        return "1 = one-way staircase v_start -> v_stop"
    legs = []
    seg = 0
    for sign in SHAPE_SIGNS[shape]:
        s = "+" if sign > 0 else "-"
        seg += 1
        legs.append(f"{seg} = 0 -> {s}V")
        seg += 1
        legs.append(f"{seg} = {s}V -> 0")
    return ", ".join(legs)


def ramp_time_s(delta_v, step=RAMP_STEP_V, dwell=RAMP_DWELL_S):
    """Seconds the bias ramp spends for a change of delta_v (writes x dwell)."""
    delta_v = abs(delta_v)
    if delta_v < RAMP_SKIP_V:
        return 0.0
    n_writes = len(np.arange(0.0, delta_v, step)) + 1
    return n_writes * dwell


def waveform_text(p, freq):
    """The waveform of one file in words (header 'waveform:' line)."""
    ramp = ramp_time_s(p["v_step"])
    hold = (
        f"hold {p['delay']:g} s + ramp {ramp:g} s per point "
        f"(ramp {RAMP_STEP_V:g} V / {RAMP_DWELL_S:g} s)"
    )
    ac = f"AC {p['ac_bias']:g} Vrms sine at {freq:g} Hz"
    if p["cycle_mode"] and p["shape"] == SHAPE_LADDER:
        amps = ", ".join(f"{v:g}" for v in p["ladder"])
        pause = (f", pause {p['ladder_pause']:g} s at 0 V between values"
                 if p.get("ladder_pause", 0) > 0 else "")
        return (
            f"DC staircase ladder 0 -> +V -> 0 -> -V -> 0 with V = {amps} V in turn "
            f"(one cycle per value{pause}), step {p['v_step']:g} V, {hold}; {ac}"
        )
    if p["cycle_mode"]:
        path = p["shape"].split(": ", 1)[1].replace("V", f"{abs(p['v_max']):g}")
        return (
            f"DC staircase {path} V, step {p['v_step']:g} V, {hold}, "
            f"x{p['n_cycles']} cycle(s); {ac}"
        )
    return (
        f"DC staircase {p['v_start']:g} -> {p['v_stop']:g} V one way, "
        f"step {p['v_step']:g} V, {hold}; {ac}"
    )


def parse_freq_list(text):
    """Frequencies in the order typed, duplicates dropped, range checked."""
    out = []
    for tok in str(text).split(","):
        tok = tok.strip()
        if not tok:
            continue
        f = float(tok)
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(
                f"Frequency {f} Hz outside {FREQ_HW_MIN:g}-{FREQ_HW_MAX:g} Hz."
            )
        if f not in out:
            out.append(f)
    if not out:
        raise ValueError("At least one frequency is required.")
    return out


def parse_ladder(text):
    """'5, 10, 20' -> [5.0, 10.0, 20.0] (as the memristor sweep module):
    commas, semicolons, spaces or new lines separate the entries; every
    entry must be a positive voltage within V_ABS_MAX."""
    tokens = str(text or "").replace(",", " ").replace(";", " ").split()
    if not tokens:
        raise ValueError("Ladder list is empty. Example: 0.5, 1, 2")
    values = []
    for tok in tokens:
        try:
            v = float(tok)
        except ValueError:
            raise ValueError(f"Ladder list: '{tok}' is not a number.")
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"Ladder list: '{tok}' must be a positive voltage.")
        if v > V_ABS_MAX:
            raise ValueError(
                f"Ladder list: {v:g} V exceeds the hardcoded {V_ABS_MAX:g} V safety cutoff.")
        values.append(v)
    return values


def derived_columns(f, R, X):
    """The 18 derived values from measured R (series) and X (reactance).
    Formulas unchanged from v1 (legacy LabVIEW convention)."""
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
    Cs_double_prime = D * Cs  # series-model loss (legacy LabVIEW convention)

    return [
        float(x) for x in (
            Q, D, G, B, Cp, Lp, Cs, Ls, Z_mag, theta_rad, chi, Rs,
            theta_deg, Rp, Y_mag, omega, Cp_double_prime, Cs_double_prime,
        )
    ]


def nan_derived():
    return [float("nan")] * N_DERIVED


BAD_NAME_CHARS = '\\/:*?"<>|'


def _check_whole_mv(label, v):
    """The bias is written as DCV with 1 mV resolution; a value between
    millivolts would be rounded while the file kept the typed V."""
    if abs(float(v) * 1000.0 - round(float(v) * 1000.0)) > 1e-6:
        raise ValueError(f"{label} {v:g} V must be a whole number of millivolts.")


def validate_params(p):
    """Raise ValueError on anything the window should refuse at Start.
    Returns the params with v_max set and voltages clamped to V_ABS_MAX."""
    p = dict(p)
    bad = [c for c in BAD_NAME_CHARS if c in str(p.get("sample_name", ""))]
    if bad:
        raise ValueError(f"Sample name must not contain {' '.join(bad)} (used in file names).")
    v_step = abs(float(p["v_step"]))
    if v_step < V_STEP_MIN:
        raise ValueError(f"Voltage step must be >= {V_STEP_MIN:g} V.")
    _check_whole_mv("Voltage step", v_step)
    p["v_step"] = v_step
    if float(p["delay"]) < 0:
        raise ValueError("Delay per step must be >= 0 s.")
    if p["cycle_mode"] and p.get("shape") not in SHAPES:
        raise ValueError("Choose a loop shape for cycle mode.")
    ignored = disabled_fields(p["cycle_mode"], p.get("shape"))
    p["ignored"] = sorted(ignored)

    def volts(key):
        """A voltage entry; an ignored entry may be blank and counts as 0."""
        if key in ignored:
            return 0.0
        v = float(p.get(key, ""))
        if not math.isfinite(v):
            raise ValueError(f"{key} must be a finite number.")
        return v

    v_start, v_stop = volts("v_start"), volts("v_stop")
    p["clamped"] = any(abs(v) > V_ABS_MAX for k, v in (("v_start", v_start), ("v_stop", v_stop))
                       if k not in ignored)
    for key, label in (("v_start", "V Start"), ("v_stop", "V Stop")):
        if key not in ignored:
            _check_whole_mv(label, v_start if key == "v_start" else v_stop)
    p["v_start"] = max(-V_ABS_MAX, min(V_ABS_MAX, v_start))
    p["v_stop"] = max(-V_ABS_MAX, min(V_ABS_MAX, v_stop))
    p["v_max"] = abs(p["v_stop"]) if p["cycle_mode"] else 0.0
    ladder_text = p.get("ladder", "")
    p["ladder"] = []
    pause_raw = p.get("ladder_pause", 0)
    p["ladder_pause"] = 0.0
    if p["cycle_mode"]:
        if p["shape"] == SHAPE_LADDER:
            p["ladder"] = parse_ladder(ladder_text)
            for v in p["ladder"]:
                _check_whole_mv("Ladder entry", v)
            p["v_max"] = max(p["ladder"])
            p["n_cycles"] = len(p["ladder"])
            pause = 0.0 if pause_raw is None or str(pause_raw).strip() == "" else float(pause_raw)
            if pause < 0:
                raise ValueError("Pause between ladder entries must be >= 0 s.")
            p["ladder_pause"] = pause
        else:
            n_raw = p.get("n_cycles", "")
            if n_raw is None or str(n_raw).strip() == "" or int(float(n_raw)) < 1:
                raise ValueError("Number of cycles must be >= 1.")
            p["n_cycles"] = int(float(n_raw))
    else:
        p["n_cycles"] = 1
        p["shape"] = ""
    p["freqs"] = parse_freq_list(p["freq_list"])
    # ACV against the ZG4 ceiling of the highest frequency, MTM, wire mode:
    # all checked here, before any instrument is opened.
    p["ac_bias"] = float(p["ac_bias"])
    p["mtm"] = float(p.get("mtm", MTM_DEFAULT_S))
    p["wire_mode"] = str(p.get("wire_mode", "2"))
    validate_alpha_parameters(p["freqs"], p["ac_bias"], p["mtm"], p["wire_mode"])
    p["waveform"] = build_waveform(
        p["cycle_mode"], p["shape"], p["v_max"], p["v_start"], p["v_stop"],
        p["v_step"], int(p["n_cycles"]), p["ladder"],
    )
    check_bias_cap(p["waveform"])
    return p


def check_bias_cap(waveform):
    """Every voltage of the waveform within V_ABS_MAX (raises ValueError).
    Run at Start and again by the worker before the link is opened."""
    for _, _, v in waveform:
        if not math.isfinite(v) or abs(v) > V_ABS_MAX:
            raise ValueError(
                f"Bias {v} V blocked by the hardcoded {V_ABS_MAX:g} V cutoff.")


def freq_tag(freq):
    """Frequency for the file name: whole Hz as before, sub-Hz frequencies
    (the Alpha goes down to 30 uHz) written so they stay distinct."""
    if freq >= 1.0:
        return f"{freq:.0f}"
    return f"{freq:g}".replace(".", "p")


# ===============================================================================
# BACKEND: NOVOCONTROL ALPHA-AN  (copied from
# novocontrol/Frequency_Scan_AlphaAN_GUI.py - keep the two in step;
# the DC bias methods are this C-V module's own addition)
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


def stop_wait_s(freqs, mtm_s):
    """How long a Stop may take before the window gives up on the worker.

    The worker sees Stop within about SRQ_SLICE_S during a point and
    within STOP_POLL_S during the *RST rejoin wait, but a bias ramp (up to
    -20 -> +20 V) or one *IDN? try still runs to its end; then the running
    point is aborted (MBK), the bias ramped down and the link closed. This
    window is far longer than that on purpose: it still allows the longest
    single point, so the STOP OVERDUE banner only appears when the Alpha
    has really stopped answering. Every window shorter than the real
    shutdown lets the window thread touch the VISA session while the
    worker still uses it.
    """
    worst = max((point_timeout_s(f, mtm_s) for f in freqs),
                default=SRQ_TIMEOUT_MEASURE_S)
    return worst + ramp_time_s(2 * V_ABS_MAX) + 15.0

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
# Stop during a point or a rejoin wait (CV-2, 27 Oct 2026): a point's SRQ
# wait (460 s at 0.01 Hz, hours at 1e-4 Hz) is waited in SRQ_SLICE_S slices
# and the settle/retry sleeps in STOP_POLL_S slices, with the Stop flag
# checked between slices. Waiting for SRQ is a local driver wait: no byte
# goes on the bus, so the slices cannot disturb the measurement.
SRQ_SLICE_S = 1.0
STOP_POLL_S = 0.1

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

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.rm = None
        # threading.Event set by Stop (AlphaANLink.open hands it over). None
        # = the backend used on its own: plain sleeps, one SRQ wait.
        self.stop_event = None
        # True between MST/ZRUNCAL and the corresponding SRQ. Tells the
        # shutdown path whether an MBK abort is actually needed.
        self._task_may_be_running = False
        # DC bias state (C-V module only). _bias_touched is set before the
        # first bias byte is sent and cleared once the bias is confirmed 0 V
        # and off, so every shutdown path knows whether bias-off is needed.
        # _bias_level is the last level the Alpha acknowledged (no DCV? read
        # back is used: its syntax is unverified).
        self._bias_touched = False
        self._bias_level = 0.0
        # True from the *RST write in _reset_and_set_impedance_mode until
        # the rejoin *IDN? is answered. Stays True when Stop ends the rejoin
        # wait: the Alpha is then still off the bus, and close_instrument
        # sends it nothing (on 64-bit Keysight VISA every command would hang
        # for the full session timeout).
        self._alpha_rebooting = False
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
            # (DCV/DCE, ACV=0, ZCONSPL=0, *RST) sent to another instrument.
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
                self._pause(RESET_RETRY_INTERVAL_S)
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
        # Set before the write: the Alpha may drop off the bus the moment
        # *RST lands. Cleared only once *IDN? is answered again, so a Stop
        # (StopRequested) out of the waits below leaves it True.
        self._alpha_rebooting = True
        try:
            inst.write("*RST")
        except Exception:
            # The *RST never went out: the Alpha is not resetting.
            self._alpha_rebooting = False
            raise
        start = time.monotonic()
        self._pause(RESET_SETTLE_S)
        self._query_idn_until_answered(timeout_s, "after *RST")
        self._alpha_rebooting = False
        print(f"  *RST complete; Alpha answering after "
              f"{time.monotonic() - start:.1f} s.")
        self._pause(1.0)
        self._exec("MODE=IMP")

    def _pause(self, seconds):
        """time.sleep(seconds), cut short by Stop: StopRequested is raised
        within STOP_POLL_S of the Stop flag being set. Without a stop event
        (the backend used on its own) it is one plain sleep."""
        stop = getattr(self, "stop_event", None)
        if stop is None:
            time.sleep(seconds)
            return
        left = float(seconds)
        while True:
            if stop.is_set():
                raise StopRequested()
            if left <= 0:
                return
            step = min(STOP_POLL_S, left)
            time.sleep(step)
            left -= step

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
        stop = getattr(self, "stop_event", None)
        try:
            if stop is None:
                inst.wait_for_srq(int(timeout_s * 1000))
            else:
                self._wait_for_srq_sliced(timeout_s, stop)
        except StopRequested:
            # Stop pressed mid-point: abort it (MBK) so the bias commands
            # of the discharge are not refused, then end the run.
            state = self.abort_and_diagnose()
            print(f"  Stop during {context}: aborted (MBK). "
                  f"ZTSTAT? reports: {state}.")
            raise
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

    def _wait_for_srq_sliced(self, timeout_s, stop):
        """The SRQ wait in SRQ_SLICE_S slices, Stop checked between them.
        The overall budget is timeout_s, as for one long wait. Only a timed
        out slice is waited again; any other error is raised. Raises
        StopRequested on Stop and TimeoutError when the budget is spent."""
        inst = self.instrument
        left = float(timeout_s)
        while True:
            if stop.is_set():
                raise StopRequested()
            if left <= 0:
                raise TimeoutError("no SRQ")
            step = min(SRQ_SLICE_S, left)
            try:
                inst.wait_for_srq(max(1, int(step * 1000)))
                return
            except Exception as exc:
                if not (isinstance(exc, TimeoutError)
                        or visa_error_code(exc) == VI_ERROR_TMO):
                    raise
            left -= step

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

        # DC bias is not touched here: dc_bias_on() switches it on at
        # 0 V after this configuration (C-V module only).

        print(f"  Configured: ACV={p['acv']} Vrms, MTM={p['mtm']} s, "
              f"FRS={p['wire_mode']}-wire")

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

    # --- DC bias (C-V module only) -----------------------------------------

    def _dc_bias_command(self, action, volts=0.0, tolerant=False):
        """THE ONLY PLACE THIS PROGRAM FORMS A DC BIAS COMMAND.

        UNVERIFIED against the Alpha manual. What the public sources settle
        (checked 22 Oct 2026):
          - JUMP (github.com/JMoVS/JUMP, MeasurementHardware.py, class
            ALPHA) never sends a bias command, but its ack parser maps the
            answer "NA" to "DC bias is not activated ... use DCE=1 to
            activate it": so DCE=1 enables the bias, and a bias-dependent
            command answers NA while it is off.
          - Novocontrol's Alpha-A technical data (rev. C): dc bias range
            +/-40 V with mainframe option B, 1 mV resolution, +/-10 mV
            accuracy, current limit about +/-70 mA, 50 ohm output impedance.
        NOT settled: the DCV value format and unit (assumed volts, written
        with 3 decimals = the 1 mV resolution), whether DCE=0 is "off",
        whether DCV must come before or after DCE=1, and what *RST does to
        either (the copied close path assumes *RST leaves DCE=0). If the
        manual says otherwise, correct the three command strings below and
        nothing else.

        action: "level" (DCV=<volts>), "enable" (DCE=1), "disable" (DCE=0).
        The 20 V cap is checked here, before the command is formed.
        Strict mode raises on anything but "OK" (via _exec); tolerant mode
        (shutdown paths) never raises and returns True only on "OK".
        """
        if action == "level":
            volts = round(float(volts), 3) + 0.0   # + 0.0: no "-0.000"
            if not math.isfinite(volts) or abs(volts) > V_ABS_MAX:
                raise ValueError(
                    f"Bias {volts} V blocked by the hardcoded "
                    f"{V_ABS_MAX:g} V cutoff.")
            cmd = f"DCV={volts:.3f}"           # UNVERIFIED syntax
        elif action == "enable":
            cmd = "DCE=1"                      # enable: confirmed by JUMP's NA text
        elif action == "disable":
            cmd = "DCE=0"                      # UNVERIFIED (assumed the inverse)
        else:
            raise ValueError(f"unknown DC bias action {action!r}")
        self._bias_touched = True
        if tolerant:
            ok = self._exec_tolerant(cmd)
        else:
            self._exec(cmd)
            ok = True
        if ok and action == "level":
            self._bias_level = volts
        return ok

    def dc_bias_on(self):
        """Switch the DC bias on at 0 V. Raises on a refused command.

        The level is zeroed first (tolerated: with the bias still off the
        Alpha may answer NA), then enabled, then zeroed again strictly, so
        the bias never comes on at a stale level.
        """
        self._dc_bias_command("level", 0.0, tolerant=True)
        self._dc_bias_command("enable")
        self._dc_bias_command("level", 0.0)

    def set_dc_bias(self, volts):
        """One DCV command (the caller ramps). Raises on a refused command."""
        self._dc_bias_command("level", volts)

    def dc_bias_off(self, sleep=time.sleep):
        """Ramp the DC bias to 0 V and switch it off. Never raises.

        Nothing is sent unless a bias command was ever sent this session.
        Ramps in RAMP_STEP_V steps from the last acknowledged level; on the
        first refused step the ramp stops and DCV=0, DCE=0 are sent anyway.
        Returns True when the bias is confirmed 0 V and off.
        """
        if self.instrument is None or not getattr(self, "_bias_touched", False):
            return True
        ok = True
        try:
            level = getattr(self, "_bias_level", 0.0)
            if abs(level) >= RAMP_SKIP_V:
                print(f"  Ramping DC bias {level:+.3f} V -> 0 V...")
                for v in _ramp(level, 0.0, RAMP_STEP_V)[1:]:
                    if not self._dc_bias_command("level", v, tolerant=True):
                        ok = False
                        break
                    sleep(RAMP_DWELL_S)
            if not ok or abs(self._bias_level) >= RAMP_SKIP_V:
                ok = self._dc_bias_command("level", 0.0, tolerant=True)
            ok = self._dc_bias_command("disable", tolerant=True) and ok
        except Exception as e:
            print(f"  CRITICAL SAFETY WARNING: DC bias off failed: {e}")
            ok = False
        if ok:
            self._bias_touched = False
        else:
            print("  CRITICAL SAFETY WARNING: the DC bias could not be "
                  "confirmed 0 V and off.")
        return ok

    # --- shutdown --------------------------------------------------------

    def _still_resetting(self):
        """True when the Alpha is still away after a *RST (Stop ended the
        rejoin wait) and no DC bias command was ever sent. Shutdown then
        sends nothing: the fresh *RST already leaves the generator off, and
        the bias is only switched on after the rejoin, so it cannot be on.
        If the bias was ever touched this is False and the full discharge
        runs regardless."""
        return (getattr(self, "_alpha_rebooting", False)
                and not getattr(self, "_bias_touched", False))

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
        if inst is None or self._still_resetting():
            return
        if self._task_may_be_running:
            if self._exec_tolerant("MBK"):
                self._task_may_be_running = False
        for cmd in ("ACV=0", "ZCONSPL=0"):
            self._exec_tolerant(cmd)

    def close_instrument(self):
        """Safe-state, reset, release the session. Never leaves output live.

        Returns True when the DC bias is confirmed 0 V and off (or was never
        touched), False when it could not be confirmed. Never raises."""
        print("--- [Backend] Closing Alpha-AN connection. ---")
        if not self.instrument:
            return not getattr(self, "_bias_touched", False)
        if self._still_resetting():
            # Stop came during the *RST rejoin wait: the Alpha is not a
            # listener yet. No MBK/ACV/ZCONSPL/*RST/RSTH/bias command - just
            # release the session. Bias was never touched (asserted by
            # _still_resetting), so it cannot be on.
            print("  Alpha still resetting after *RST - closing the session "
                  "without shutdown commands (a fresh *RST leaves the "
                  "generator off)")
            try:
                self.instrument.close()
                print("  Alpha-AN connection closed.")
            except Exception as e:
                print(f"  Warning closing VISA session: {e}")
            finally:
                self.instrument = None
            return True
        try:
            # DC bias to 0 V and off FIRST, then the generator safe state.
            # A bias command is refused (MR) while a task runs; if the first
            # attempt failed, MBK in safe_state() has stopped the task and
            # the bias-off is sent once more (no ramp left to do then).
            bias_off = self.dc_bias_off()
            self.safe_state()
            if not bias_off:
                self.dc_bias_off()
            try:
                # *RST parks the generator (ACV=0, DCE=0 - the DCE part is
                # unverified, hence the explicit bias-off above) and
                # preserves the stored calibration tables.
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
        return not getattr(self, "_bias_touched", False)


# ===============================================================================
# LINK - the C-V run path's view of the Alpha-AN (one backend per run)
# ===============================================================================

class AlphaANLink:
    """The C-V configuration sequence on top of AlphaAN_Backend. One
    attempt per operation (gentle GPIB): there is no reconnect; a failure
    ends the run and the worker's cleanup ramps the bias to 0 V."""

    def __init__(self, address, sleep=time.sleep):
        self.address = address
        self.sleep = sleep
        self.backend = None
        self.idn = ""
        self.interface_code = None
        # Stop event of the run (CVWorker.run sets it); handed to the
        # backend so its SRQ and rejoin waits end promptly on Stop.
        self.stop_event = None
        self.config = None
        self.freq = None            # frequency of the next measure_point()
        # False once a bias-off could not be confirmed; stays False after
        # the session is gone, so a later caller is not told "off".
        self.bias_confirmed_off = True

    @property
    def inst(self):
        """The open VISA session, or None (the GUI's cleanup checks it)."""
        return None if self.backend is None else self.backend.instrument

    # -- transport ------------------------------------------------------
    def open(self):
        """Open the session: *IDN?, INTTYP? must be the ZG4 (code 5)."""
        if pyvisa is None:
            raise BusError("PyVISA is not installed.")
        backend = AlphaAN_Backend()
        backend.stop_event = self.stop_event
        try:
            self.idn, self.interface_code = backend.connect(self.address)
        except StopRequested:
            raise
        except Exception as exc:
            raise BusError(f"open {self.address}: {exc}")
        self.backend = backend

    def close(self):
        """Bias to 0 V and off, generator safe state, *RST, release.
        close_instrument() never raises. Returns True when the bias is
        confirmed 0 V and off."""
        if self.backend is not None:
            try:
                self.bias_confirmed_off = bool(self.backend.close_instrument())
            except Exception as exc:
                print(f"  CRITICAL SAFETY WARNING: close failed: {exc}")
                self.bias_confirmed_off = False
        self.backend = None
        return self.bias_confirmed_off

    # -- startup ----------------------------------------------------------
    def identify(self):
        """No bytes: connect() already read *IDN? and INTTYP?."""
        upper = self.idn.upper()
        if "ALPHA" not in upper and "NOVOCONTROL" not in upper:
            raise RuntimeError(f"Not a Novocontrol Alpha: {self.idn}")
        return self.idn

    def configure(self, p):
        """The CV configuration: *RST (Alpha rejoins the bus), MODE=IMP,
        ZG4 check, reference/correction settings, FRS wire mode, ACV, MTM,
        then the DC bias on at 0 V. Every command's "OK" is read."""
        self.config = dict(p)
        self.backend.initialize_instrument(
            {"acv": p["ac_bias"], "mtm": p["mtm"], "wire_mode": p["wire_mode"]})
        # CV mode: start at 0 V with bias output enabled; sweep sets values.
        self.backend.dc_bias_on()
        self.sleep(BIAS_ON_SETTLE_S)

    def snapshot(self):
        """Settings for the file header. Nothing is queried: each value was
        sent with _exec and acknowledged "OK", so the set value is what the
        Alpha holds (no read-back query is used for them)."""
        p = self.config or {}
        return {
            "acv_vrms": p.get("ac_bias"),
            "mtm_s": p.get("mtm"),
            "wire_mode": p.get("wire_mode"),
            "interface_code": self.interface_code,
        }

    # -- bias -------------------------------------------------------------
    def bias_voltage(self):
        """The last bias level the Alpha acknowledged (no read-back)."""
        return float(self.backend._bias_level)

    def ramp_bias(self, target_v, step=RAMP_STEP_V, dwell=RAMP_DWELL_S):
        """Ramp the DC bias to target_v. Final hardware-protection clamp."""
        if not math.isfinite(target_v) or abs(target_v) > V_ABS_MAX:
            raise ValueError(  # defense in depth, should never trigger
                f"Bias {target_v} V blocked by hardcoded {V_ABS_MAX} V cutoff."
            )
        current_v = self.bias_voltage()
        if abs(target_v - current_v) < RAMP_SKIP_V:
            return
        if step <= 0:
            self.backend.set_dc_bias(target_v)
            return
        direction = 1 if target_v > current_v else -1
        pts = list(np.arange(current_v, target_v, direction * step)) + [target_v]
        for v in pts:
            self.backend.set_dc_bias(v)
            self.sleep(dwell)

    def force_zero_bias(self):
        """Emergency: drive the bias to 0 V and switch it off. Swallows
        every error so it can be used in any error handler. No reopen of a
        lost session (gentle GPIB). Returns True when the bias is confirmed
        0 V and off (or was never touched), False otherwise - the caller
        must surface a False (BIAS_OFF_FAILED)."""
        try:
            backend = self.backend
            if backend is None:
                return self.bias_confirmed_off
            if backend.instrument is None:
                ok = not getattr(backend, "_bias_touched", False)
            else:
                ok = bool(backend.dc_bias_off(sleep=self.sleep))
        except Exception as exc:
            print(f"  CRITICAL SAFETY WARNING: Failed to force bias to 0V! {exc}")
            ok = False
        self.bias_confirmed_off = ok
        return ok

    # -- measurement ------------------------------------------------------
    def set_frequency(self, freq):
        """No bytes: measure_point() sends GFR= with every point."""
        self.freq = freq

    def trigger_and_fetch(self):
        """One point: GFR, MST, SRQ, ZRE?. Returns Zs', Zs'', status,
        f_actual. Status 6 (signal source disconnected) aborts the run."""
        zr, zi, f_act, status, _ref = self.backend.measure_point(self.freq)
        if status in STATUS_FATAL:
            raise RuntimeError(
                f"Alpha-AN status {status} at {self.freq:.6g} Hz: "
                f"{status_message(status)}. Run aborted.")
        return zr, zi, status, f_act

    def fetch_monitors(self):
        """Vdc, Idc, Vac, Iac: no verified Alpha query exists for them, so
        nothing is sent and the columns are NaN (kept for the file format)."""
        return (float("nan"),) * 4


# ===============================================================================
# WORKER - the run path (thread), file writing, END lines
# ===============================================================================

class CVWorker:
    """Runs the whole CV measurement: frequencies in the order typed, one
    data file each, every row flushed and fsynced. Messages to the GUI go
    through q as tuples: ("log", text), ("status", text),
    ("new_file", path, freq), ("point", freq, v, cycle, segment, status,
    derived), ("done", reason), ("error", text)."""

    def __init__(self, params, link, stop_event, q, sleep=time.sleep):
        self.p = params
        self.link = link
        self.stop_event = stop_event
        self.q = q
        self.sleep = sleep
        self.t0 = None
        self.start_iso = ""
        self.run_stamp = ""
        self.log_path = ""
        self.data_path = ""
        self.points_in_file = 0
        self.points_total = 0
        self.files = []
        self.end_reason = ""
        self.bias_confirmed = None      # True/False once the run has ended

    # -- helpers ------------------------------------------------------------
    def t_s(self):
        return 0.0 if self.t0 is None else time.perf_counter() - self.t0

    def log(self, text):
        self.q.put(("log", text))

    def status(self, text):
        self.q.put(("status", text))

    def _append(self, path, text):
        """open/append/close + flush + fsync so a crash loses nothing."""
        if not path:
            return
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass

    def log_event(self, text):
        """One line in the run log: ISO time | t_s | event."""
        self._append(self.log_path, f"{iso_now()} | t_s={self.t_s():.3f} | {header_value(text)}\n")

    def check_stop(self):
        if self.stop_event.is_set():
            raise StopRequested()

    def once(self, fn, what="command"):
        """Run fn() once. Gentle GPIB: no retry and no reconnect; a failure
        is one BUS_ERROR line in the log and ends the run (the cleanup in
        run() still ramps the bias to 0 V and switches it off)."""
        try:
            return fn()
        except (StopRequested, ValueError):
            raise
        except Exception as exc:
            self.log_event(f"BUS_ERROR what={what} | error={header_value(exc)}")
            raise

    # -- files --------------------------------------------------------------
    def _header_lines(self, k, freq, snap):
        p = self.p
        freqs = p["freqs"]
        earlier = ",".join(f"{f:g}" for f in freqs[:k]) or "none"
        mode = "cycle" if p["cycle_mode"] else "linear"
        ignored = set(p.get("ignored", ()))

        def na(key, value):
            """n/a for a field the mode/shape ignores, else the value."""
            if key in ignored:
                return "n/a"
            return f"{value:g}" if isinstance(value, float) else str(value)

        v_max_txt = f"{p['v_max']:g}" if p["cycle_mode"] else "n/a"
        lines = [
            f"# PICA {MODULE_NAME} v{VERSION} | file {k + 1} of {len(freqs)}",
            f"# [instrument] idn={header_value(self.link.idn)} | "
            f"interface_code={header_value(snap.get('interface_code'))} (5 = ZG4) | "
            f"visa={header_value(p['lcr_visa'])}",
            f"# [sample] name={header_value(p['sample_name'])}",
            f"# [sweep] mode={mode} | shape={header_value(p['shape'] or 'one-way')} | "
            f"v_start_v={na('v_start', p['v_start'])} | v_stop_v={na('v_stop', p['v_stop'])} | "
            f"v_max_v={v_max_txt} | "
            f"v_step_v={p['v_step']:g} | n_cycles={p['n_cycles']} | "
            f"ladder_list_V={na('ladder', ' '.join(f'{v:g}' for v in p['ladder']))} | "
            f"ladder_pause_s={na('ladder_pause', p.get('ladder_pause', 0.0))} | "
            f"points_per_file={len(p['waveform'])} | "
            f"delay_s={p['delay']:g} | ramp_step_v={RAMP_STEP_V:g} | ramp_dwell_s={RAMP_DWELL_S:g} | "
            f"v_abs_max_v={V_ABS_MAX:g} | pre_ramp=0 V -> first point, ramped, not recorded",
            f"# [frequency] this_hz={freq:g} | order_hz={','.join(f'{f:g}' for f in freqs)} | earlier_hz={earlier}",
            f"# [alpha] acv_vrms_set={p['ac_bias']:g} | acv_ceiling_vrms={acv_ceiling(freq):g} | "
            f"mtm_s={p['mtm']:g} | wire_mode={header_value(p['wire_mode'])} | "
            f"settings=acknowledged OK, not read back | "
            f"zrefmode=-3 | zllcor=1 | zslcal=1 | drs=0 0 | mode=IMP | "
            f"dc_bias=DCV/DCE (syntax UNVERIFIED against the Alpha manual) | "
            f"monitors=none (Vdc/Idc/Vac/Iac columns NaN) | "
            f"trigger=MST + SRQ | result=ZRE? (Zs', Zs'', f_actual, status)",
            f"# [timing] run_start={self.start_iso} | file_start={iso_now()} | t_s=seconds since run_start",
            f"# waveform: {header_value(waveform_text(p, freq))}",
            f"# segments: {segment_text(p['cycle_mode'], p['shape'])}; Cycle counts from 1",
            "# note: Alpha-A technical data: dc bias +/-40 V with option B, current limit about +/-70 mA, "
            "50 ohm output impedance; this unit's maximum is not confirmed and this program caps at +/-20 V; "
            "no DC current is measured",
            "# columns: R_raw = Zs' and X_raw = Zs'' (ohm, series) from ZRE?; the 18 derived columns use "
            "f_actual(Hz), the frequency the Alpha produced; Frequency is the requested one",
            "# status: 2 valid | 0 no result | 1 still measuring | 3 V1 out of range (sample) | "
            "4 current out of range | 5 V1 out of range (reference) | 6 source disconnected (run aborted); "
            "the 18 derived columns are NaN for every status but 2",
            f"# log_file={os.path.basename(self.log_path)}",
        ]
        return lines

    def _open_data_file(self, k, freq, snap):
        p = self.p
        fname = f"{p['sample_name']}_{self.run_stamp}_CV_{freq_tag(freq)}Hz.txt"
        path = os.path.join(p["folder"], fname)
        n = 2
        while os.path.exists(path):
            path = os.path.join(p["folder"], fname.replace(".txt", f"_{n}.txt"))
            n += 1
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(self._header_lines(k, freq, snap)) + "\n")
            fh.write(COLUMN_ROW + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        self.data_path = path
        self.points_in_file = 0
        self.files.append(path)
        self.log_event(f"FILE data_file={os.path.basename(path)} | freq_hz={freq:g}")
        self.q.put(("new_file", path, freq))
        self.log(f"New file: {os.path.basename(path)}")

    def _end_data_file(self, reason, bias=""):
        if not self.data_path:
            return
        line = (f"END reason={header_value(reason)} | points={self.points_in_file} | "
                f"end_time={iso_now()} | file={os.path.basename(self.data_path)}")
        if bias:
            line += f" | bias={bias}"
        self._append(self.data_path, "# " + line + "\n")
        self.log_event(line)
        self.data_path = ""

    def _write_row(self, v, f, derived, t_s, stamp, cycle, seg, mons, R, X, status, f_act):
        vdc, idc, vac, iac = mons
        cells = [v, f] + list(derived) + [t_s, stamp, cycle, seg, vdc, idc, vac, iac, R, X, status,
                                          f_act]
        self._append(self.data_path, "\t".join(fmt_cell(c) for c in cells) + "\n")
        self.points_in_file += 1
        self.points_total += 1

    # -- run ------------------------------------------------------------------
    def run(self):
        p = self.p
        reason = "complete"
        self.t0 = time.perf_counter()
        self.start_iso = iso_now()
        self.run_stamp = timestamp_for_name()
        self.log_path = os.path.join(
            p["folder"], f"{p['sample_name']}_{self.run_stamp}_CV_log.txt")
        try:
            self._append(self.log_path, "")
            self.log_event(f"START module={MODULE_NAME} v{VERSION} | sample={p['sample_name']} | "
                           f"freqs_hz={','.join(f'{f:g}' for f in p['freqs'])} | "
                           f"points_per_file={len(p['waveform'])}")
            # Every setting and every bias value is checked again here,
            # before the link is opened and before any byte is sent.
            validate_alpha_parameters(p["freqs"], p["ac_bias"], p["mtm"], p["wire_mode"])
            check_bias_cap(p["waveform"])
            self.status("CONNECTING")
            self.link.stop_event = self.stop_event
            self.once(self.link.open, "open")
            self.once(self.link.identify, "identify")
            self.log(f"Connected: {self.link.idn} | ZG4 interface code {self.link.interface_code}")
            self.log("DC bias commands DCV/DCE are UNVERIFIED against the Alpha manual; "
                     "the bias is ramped in "
                     f"{RAMP_STEP_V:g} V steps and capped at {V_ABS_MAX:g} V.")
            self.once(lambda: self.link.configure(p), "configure")
            snap = self.once(self.link.snapshot, "snapshot")
            self.log(f"Alpha-AN configured (ACV {p['ac_bias']:g} Vrms, MTM {p['mtm']:g} s, "
                     f"{p['wire_mode']}-wire, bias on at 0 V).")

            for k, f in enumerate(p["freqs"]):
                self.check_stop()
                self.once(lambda f=f: self.link.set_frequency(f), "set frequency")
                self.once(lambda: self.link.ramp_bias(0.0), "discharge")
                self._open_data_file(k, f, snap)
                self.log_event(f"PRE_RAMP from=0 V | to={p['waveform'][0][2]:g} V | freq_hz={f:g}")
                last_cycle = None
                for cycle, seg, v in p["waveform"]:
                    self.check_stop()
                    if (p.get("ladder_pause", 0) > 0 and last_cycle is not None
                            and cycle != last_cycle):
                        # ladder: the previous cycle ended at 0 V; rest there
                        # before the next amplitude starts
                        self.log(f"Ladder pause {p['ladder_pause']:g} s at 0 V before "
                                 f"entry {cycle} ({p['ladder'][cycle - 1]:g} V).")
                        self.log_event(f"LADDER_PAUSE s={p['ladder_pause']:g} | next_entry={cycle} | "
                                       f"next_v={p['ladder'][cycle - 1]:g}")
                        if wait_with_stop(p["ladder_pause"], self.stop_event, sleep=self.sleep):
                            raise StopRequested()
                    last_cycle = cycle
                    self.once(lambda v=v: self.link.ramp_bias(v), "set bias")
                    if wait_with_stop(p["delay"], self.stop_event, sleep=self.sleep):
                        raise StopRequested()
                    R, X, status, f_act = self.once(self.link.trigger_and_fetch, "measure")
                    mons = self.once(self.link.fetch_monitors, "monitors")
                    t_s = self.t_s()
                    stamp = iso_now()
                    if status != STATUS_RESULT_VALID:
                        # not a valid result: the row keeps the raw values
                        # and the status, never derived values
                        derived = nan_derived()
                        self.log(f"WARNING: V={v:+.3f} V status {status} "
                                 f"({status_message(status)}); derived values NaN.")
                    else:
                        # convert with the frequency the Alpha produced
                        f_conv = f_act if (math.isfinite(f_act) and f_act > 0) else f
                        try:
                            derived = derived_columns(f_conv, R, X)
                        except Exception as calc_err:
                            self.log(f"Calc error at V={v} V: {calc_err}. Derived values NaN.")
                            derived = nan_derived()
                    self._write_row(v, f, derived, t_s, stamp, cycle, seg, mons, R, X, status, f_act)
                    self.q.put(("point", f, v, cycle, seg, status, derived))
                self._end_data_file("complete")
            self.status("DONE")
        except StopRequested:
            reason = "stop_button"
            self.log("Stop acknowledged by the worker.")
        except Exception as exc:
            reason = f"error: {exc}"
            self.q.put(("error", f"{exc}\n{traceback.format_exc()}"))
        finally:
            # Bias to 0 V and off first, then the link closes (generator
            # safe state, *RST); a failing file write (e.g. the data file
            # locked by Excel) must never skip the discharge or the "done".
            bias_ok = False
            try:
                self.status("DISCHARGING")
                try:
                    bias_ok = bool(self.link.force_zero_bias())
                except Exception:
                    pass
                try:
                    # close() retries the bias-off after MBK when the first
                    # attempt failed; its answer is the final word.
                    bias_ok = bool(self.link.close()) or bias_ok
                except Exception:
                    pass
                bias = "off" if bias_ok else "BIAS_OFF_FAILED"
                # a completed run ended its last file before the discharge
                ended_before = not self.data_path
                steps = [lambda: self._end_data_file(reason, bias)]
                if not bias_ok:
                    if ended_before and self.files:
                        steps.append(lambda: self._append(
                            self.files[-1],
                            f"# BIAS_OFF_FAILED after END | end_time={iso_now()}\n"))
                    steps.append(lambda: self.log_event(
                        f"BIAS_OFF_FAILED | {BIAS_OFF_FAILED_TEXT}"))
                steps.append(lambda: self.log_event(
                    f"END reason={header_value(reason)} | points={self.points_total} | "
                    f"files={len(self.files)} | end_time={iso_now()} | bias={bias}"))
                for step in steps:
                    try:
                        step()
                    except Exception as exc:
                        self.log(f"WARNING: could not write the END line: {exc}")
            finally:
                self.end_reason = reason
                self.bias_confirmed = bias_ok
                self.q.put(("bias", bias_ok, "DC bias confirmed 0 V and off." if bias_ok
                            else BIAS_OFF_FAILED_TEXT))
                self.q.put(("done", reason))


# ===============================================================================
# FRONTEND CLASS - The Main GUI Application
# ===============================================================================

STOP_OVERDUE_TEXT = (
    "STOP OVERDUE: the run is still shutting down the Alpha (it may not be "
    "answering). The DC bias is NOT yet confirmed 0 V and off: do not touch "
    "the sample. This line clears once the bias-off is confirmed.")


class LCR_Freq_GUI:
    """The main GUI application class for CV measurements."""

    LOGO_SIZE = 110

    # --- Layout constants for deferred sash placement ---
    SASH_FALLBACK_WIDTH = 480
    SASH_PADDING = 30  # scrollbar + padding

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

    DATA_HEADER = "Frequency\t" + DERIVED_HEADER  # kept for older readers

    def __init__(self, root):
        self.root = root
        self.root.title(f"Novocontrol Alpha-AN CV Scan v{VERSION} - 64-bit (VISA)")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self._stopping = False          # re-entrancy guard for stop_sweep
        self._close_after_stop = False  # destroy window once worker exits
        self.link = None
        self.worker = None
        self._bias_confirmed = None     # last run's bias-off result
        self._end_reason = None         # worker's end reason (final label)
        self._overdue_alarm = False     # STOP_OVERDUE_TEXT banner shown
        self.alarm_banner = None        # red BIAS_OFF_FAILED line (no dialog)
        self.file_location_path = ""
        self.data_filepath = ""

        # Threading components
        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None

        # Forced Cleanup: zero the bias on crash/exit if a link is still open
        atexit.register(self._atexit_cleanup)

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

        # Y-scale mode: 'auto' uses decade-snapped log when the data
        # spans >= 1 decade and linear otherwise; 'log'/'linear' force it.
        self.y_scale_var = tk.StringVar(value="auto")
        self._decade_ylims = {}

        # Built at start_sweep from validated user input:
        self.voltage_points = []
        self.freq_list = []

        # Sash-placement retry counter (Fix #1 + #2)
        self._sash_attempts = 0

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        # Terminal output (print, warnings, tracebacks) also goes to the console.
        install_terminal_log(self)

    def _atexit_cleanup(self):
        link = self.link
        if link is not None and link.inst is not None:
            ok = link.force_zero_bias()
            ok = link.close() or ok
            if not ok:
                print(BIAS_OFF_FAILED_TEXT)

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
        # Y-scale strip above the plots: the graph panel's own background,
        # not the parameter panel's (TFrame) or clam's default grey.
        for name in ("Graph.TFrame", "Graph.TLabel", "Graph.TRadiobutton"):
            style.configure(name, background=self.CLR_GRAPH_BG,
                            foreground=self.CLR_FG_LIGHT)
        style.map("Graph.TRadiobutton",
                  background=[("active", self.CLR_GRAPH_BG)])

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
            text="Novocontrol Alpha-AN: CV Scan (Zs', Zs'')"
                 "   [64-bit edition, VISA]",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=font_title_italic,
        ).pack(side="left", padx=20, pady=10)

        # --- Utility Launch Buttons ---
        ttk.Button(
            header_frame,
            text="\U0001F4C8",
            command=launch_plotter_utility,
            width=3,
        ).pack(side="right", padx=10, pady=5)
        ttk.Button(
            header_frame,
            text="\U0001F4DF",
            command=launch_gpib_scanner,
            width=3,
        ).pack(side="right", padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)
        self.main_pane = main_pane  # Fix #1: store reference for sashpos()

        # Red alarm line above the panes, shown only when the bias-off of a
        # run could not be confirmed (never a dialog: runs are unattended).
        self.alarm_banner = tk.Label(
            self.root, text="", bg="#B00000", fg="white",
            font=("Segoe UI", 12, "bold"), wraplength=1400, justify="left",
            padx=12, pady=8)

        left_panel_container = ttk.Frame(main_pane)
        # Fix #4: prevent child widgets from shrinking this pane
        left_panel_container.pack_propagate(False)
        self.left_panel_container = left_panel_container
        main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        left_canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=left_canvas.yview,
        )
        scrollable_frame = ttk.Frame(left_canvas)
        self.scrollable_frame = scrollable_frame  # used by _set_initial_sash
        scrollable_frame.bind(
            "<Configure>",
            lambda e: left_canvas.configure(
                scrollregion=left_canvas.bbox("all")
            ),
        )

        # Fix #3: capture window id and keep inner frame width matched to
        # the canvas viewport (no hardcoded width).
        window_id = left_canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw"
        )
        left_canvas.bind(
            "<Configure>",
            lambda e: left_canvas.itemconfigure(window_id, width=e.width),
        )

        left_canvas.configure(yscrollcommand=scrollbar.set)
        left_canvas.pack(side="left", fill="both", expand=True)
        # The mouse wheel never changes a dropdown (wire mode, address,
        # shape): the ttk class binding would change the value under
        # the pointer unnoticed. 15 Oct 2026.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.unbind_class("TCombobox", seq)
        # ... but over the parameter panel the wheel scrolls the panel
        # (Start/Stop and the console sit below the fold at 1300x850).
        self.left_canvas = left_canvas
        self._bind_panel_wheel(left_canvas)
        scrollbar.pack(side="right", fill="y")

        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill="x", expand=True, padx=10, pady=5)
        input_frame = self.create_input_frame(scrollable_frame)
        input_frame.pack(fill="x", expand=True, padx=10, pady=5)
        console_frame = self.create_console_frame(scrollable_frame)
        console_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

        # Fix #1 + #2: deferred, verified, retried sash placement.
        # sashpos() silently no-ops until the PanedWindow is mapped, so we
        # schedule the call after the event loop has had a chance to layout
        # the widgets.
        self._sash_attempts = 0
        self.root.after(50, self._set_initial_sash)

    _WHEEL_SEQS = ("<MouseWheel>", "<Button-4>", "<Button-5>")

    def _bind_panel_wheel(self, canvas):
        """One application-wide wheel binding; _on_panel_wheel acts only
        when the pointer is over the panel (Enter/Leave on the canvas also
        fire when the pointer moves onto one of its child widgets)."""
        for seq in self._WHEEL_SEQS:
            canvas.bind_all(seq, self._on_panel_wheel, add="+")

    def _on_panel_wheel(self, event):
        canvas = self.left_canvas
        widget = getattr(event, "widget", None)
        path, top = str(widget), str(canvas)
        if path != top and not path.startswith(top + "."):
            return                      # plots, dialogs: not the panel
        try:
            if widget.winfo_class() == "Text":
                return                  # the console scrolls itself
        except Exception:
            pass
        if getattr(event, "num", None) in (4, 5):        # X11
            step = -1 if event.num == 4 else 1
        else:                                            # Windows / macOS
            delta = getattr(event, "delta", 0) or 0
            step = -int(delta / 120) or (-1 if delta > 0 else 1 if delta < 0 else 0)
        if step:
            canvas.yview_scroll(step, "units")

    def _set_initial_sash(self):
        """Set left-pane width; verify and retry (sashpos fails silently
        before the PanedWindow is mapped)."""
        self.root.update_idletasks()
        content_w = self.scrollable_frame.winfo_reqwidth()
        target = (
            content_w + self.SASH_PADDING
            if content_w > 1
            else self.SASH_FALLBACK_WIDTH
        )
        try:
            self.main_pane.sashpos(0, target)
            self.root.update_idletasks()
            actual = self.main_pane.sashpos(0)
        except tk.TclError:
            actual = -1

        if abs(actual - target) > 5 and self._sash_attempts < 10:
            self._sash_attempts += 1
            self.root.after(100, self._set_initial_sash)

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

        # Integer-entry validation: allow only digits while typing
        def _vint(P):
            return P == "" or P.isdigit()

        self._vint_cmd = (frame.register(_vint), "%P")

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
            frame, "AC Level (Vrms)", "ac_bias", 5, 1, default="1.0"
        )

        # Row 7-8: Frequencies (comma-separated list, measured in this order)
        self._add_entry(
            frame,
            "Frequencies (Hz, comma-sep, measured in this order)",
            "freq_list",
            7,
            0,
            colspan=2,
            default="1000, 10000, 100000, 1000000",
        )

        # Row 9-10: Delay | Integration time (MTM; the Alpha's aperture)
        self._add_entry(
            frame, "Delay per step (s)", "delay", 9, 0, default="0.2"
        )
        # MTM: 0.5 s matches the lab's WinDETA definition ("Maximum of
        # 0.500 Seconds or 1 Periods"); the Alpha never integrates less
        # than one period.
        self._add_entry(
            frame, "Integration Time, MTM (s)", "mtm", 9, 1,
            default=f"{MTM_DEFAULT_S:g}",
        )

        # Row 11-12: ZG4 wire mode (FRS). ALC, open/short correction and
        # cable length have no Alpha equivalent and are not offered.
        Label(
            frame, text="ZG4 Wire Mode (FRS):", font=self.FONT_BASE,
            bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
        ).grid(row=11, column=0, padx=padx, pady=pady, sticky="w")
        self.wire_combobox = ttk.Combobox(
            frame,
            font=self.FONT_BASE,
            state="readonly",
            values=list(WIRE_MODES),
        )
        self.wire_combobox.set("2")
        self.wire_combobox.grid(
            row=12, column=0, padx=padx, pady=(0, 10), sticky="ew"
        )

        # Row 14-15: Alpha-AN VISA
        Label(
            frame, text="Alpha-AN VISA:", font=self.FONT_BASE,
            bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
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

        self.browse_button = ttk.Button(
            frame,
            text="Browse Save Loc...",
            command=self._browse_file_location,
        )
        self.browse_button.grid(row=16, column=1, padx=padx, pady=5, sticky="ew")

        # Row 17-18: Cycle-mode controls (CV cycle feature).
        # Cycle amplitude comes from V Stop; V Start is disabled in this mode.
        self.var_cycle = tk.BooleanVar(value=False)
        self.cycle_check = ttk.Checkbutton(
            frame,
            text="Cycle mode (amplitude = |V Stop|)",
            variable=self.var_cycle,
            command=self._on_cycle_mode_toggle,
        )
        self.cycle_check.grid(row=17, column=0, padx=padx, pady=2, sticky="w")

        self._add_entry(
            frame, "No. of Cycles", "n_cycles", 17, 1, default="1"
        )

        # Row 19-20: Loop shape (cycle mode only)
        Label(
            frame, text="Loop shape (cycle mode):", font=self.FONT_BASE,
            bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
        ).grid(row=19, column=0, columnspan=2, padx=padx, pady=(2, 0), sticky="w")
        self.shape_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state="readonly", values=list(SHAPES)
        )
        self.shape_combobox.set(SHAPE_POS_FIRST)
        self.shape_combobox.grid(
            row=20, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky="ew"
        )
        self.shape_combobox.bind("<<ComboboxSelected>>", lambda e: self._on_cycle_mode_toggle())

        # Row 21-22: Ladder list (ladder shape only)
        self._add_entry(
            frame, "Ladder list (V, ladder shape only; e.g. 0.5, 1, 2)", "ladder", 21, 0,
            default="",
        )
        self._add_entry(
            frame, "Pause between ladder entries (s)", "ladder_pause", 21, 1, default="0",
        )

        # Row 23: neutral note on the bias source (no sample-specific limit)
        tk.Label(
            frame,
            text=(
                "Note: the Alpha's DC bias (DCV/DCE, syntax not yet "
                "verified against the manual) is capped here at "
                f"+/-{V_ABS_MAX:g} V and ramped in {RAMP_STEP_V:g} V steps. "
                "No DC current is measured; the *_mon columns are NaN."
            ),
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=("Segoe UI", 9),
            wraplength=440,
            justify="left",
        ).grid(row=23, column=0, columnspan=2, padx=10, pady=(0, 6), sticky="ew")

        # Row 24: Start + Stop buttons
        self.start_button = ttk.Button(
            frame,
            text="Start Sweep",
            command=self.start_sweep,
            style="Start.TButton",
        )
        self.start_button.grid(
            row=24, column=0, padx=(padx, 5), pady=15, sticky="ew"
        )

        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_sweep,
            style="Stop.TButton",
            state="disabled",
        )
        self.stop_button.grid(
            row=24, column=1, padx=(5, padx), pady=15, sticky="ew"
        )

        # Row 25: Current measurement label
        self.lbl_current_freq = ttk.Label(
            frame,
            text="Measuring: -- Hz",
            font=("Segoe UI", 12, "bold"),
            foreground=self.CLR_ACCENT_RED,
        )
        self.lbl_current_freq.grid(
            row=25, column=0, columnspan=2, pady=5
        )

        # Row 26: Progress bar
        self.progress_bar = ttk.Progressbar(
            frame,
            orient="horizontal",
            mode="determinate",
            style="green.Horizontal.TProgressbar",
        )
        self.progress_bar.grid(
            row=26,
            column=0,
            columnspan=2,
            padx=padx,
            pady=(5, 10),
            sticky="ew",
        )

        # Apply keystroke validation to numeric entries
        for key in ("v_start", "v_stop", "v_step", "ac_bias", "delay", "ladder_pause", "mtm"):
            self.entries[key].config(
                validate="key", validatecommand=self._vfloat_cmd
            )
        self.entries["n_cycles"].config(
            validate="key", validatecommand=self._vint_cmd
        )

        # Sync V Start / cycle widgets with the cycle-mode checkbox
        self._on_cycle_mode_toggle()

        return frame

    def _on_cycle_mode_toggle(self):
        """Cycle mode derives its amplitude from V Stop, so V Start is not
        used; No. of Cycles and the loop shape apply only in cycle mode.
        Grey out whichever does not apply so nothing is silently ignored."""
        off = disabled_fields(self.var_cycle.get(), self.shape_combobox.get())
        for key in SWEEP_FIELDS:
            if key == "shape":
                self.shape_combobox.config(state="disabled" if key in off else "readonly")
            else:
                self.entries[key].config(state="disabled" if key in off else "normal")

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
            f"Alpha-AN CV Scan v{VERSION} initialized. Not yet run on the hardware."
        )
        self.log(
            "DC bias: DCV/DCE are UNVERIFIED against the Alpha manual; "
            f"hard cap +/-{V_ABS_MAX:g} V."
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

        scale_bar = ttk.Frame(parent, style="Graph.TFrame")
        scale_bar.pack(anchor="w", padx=5, pady=(5, 0))
        ttk.Label(scale_bar, text="Y scale:", style="Graph.TLabel").pack(side="left")
        for text, val in (("Auto", "auto"), ("Log", "log"),
                          ("Linear", "linear")):
            ttk.Radiobutton(scale_bar, text=text, value=val,
                            style="Graph.TRadiobutton",
                            variable=self.y_scale_var,
                            command=self._on_y_scale_change).pack(
                side="left", padx=(8, 0))

        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=0, pady=0
        )

    def _on_y_scale_change(self):
        """Re-snap axes from scratch when the Y-scale mode changes."""
        self._decade_ylims.clear()
        self._update_sweep_plot()

    def _apply_y_scale(self, ax, values, key):
        """Adaptive Y-scale driven by self.y_scale_var ('auto'|'log'|'linear').

        log:    LabVIEW-style decade autoscale, snap y-limits to
                [10^floor(log10(min_pos)), 10^ceil(log10(max_pos))],
                expand only (cached in self._decade_ylims[key]) so the
                scale never jitters per point.
        linear: plain relim/autoscale_view.
        auto:   log when the positive data spans >= 1 decade, else
                linear, so narrow-span data is not squashed onto a
                single decade band.
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
            parent, text=f"{text}:", font=self.FONT_BASE,
            bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
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
        """Kept for older callers; the formulas live in derived_columns()."""
        return derived_columns(f, R, X)

    # -- Start / Stop -----------------------------------------------------------
    # Window labels of the numeric boxes, for error messages that name the box.
    ENTRY_LABELS = {
        "v_start": "V Start (V)", "v_stop": "V Stop (V)", "v_step": "V Step (V)",
        "ac_bias": "AC Level (Vrms)", "delay": "Delay per step (s)",
        "mtm": "Integration Time, MTM (s)",
        "ladder_pause": "Pause between ladder entries (s)",
    }

    def _number(self, key):
        """One numeric box as a finite float; ValueError names the box
        (an empty box, '-', or a pasted 'inf'/'nan' never reaches the run)."""
        text = self.entries[key].get().strip()
        label = self.ENTRY_LABELS[key]
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{label}: '{text}' is not a number.")
        if not math.isfinite(value):
            raise ValueError(f"{label}: '{text}' must be a finite number.")
        return value

    def _collect_params(self):
        """Raw window values -> validated params dict (raises ValueError)."""
        if not PYVISA_AVAILABLE:
            raise ValueError("PyVISA is not installed.")
        visa_addr = self.lcr_combobox.get().split("  ->  ")[0]
        cycle_mode = self.var_cycle.get()
        shape = self.shape_combobox.get()
        ignored = disabled_fields(cycle_mode, shape)
        raw = {
            "sample_name": self.entries["sample_name"].get().strip(),
            "ac_bias": self._number("ac_bias"),
            "v_start": self.entries["v_start"].get(),
            "v_stop": self.entries["v_stop"].get(),
            "v_step": self._number("v_step"),
            "n_cycles": self.entries["n_cycles"].get(),
            "cycle_mode": cycle_mode,
            "shape": shape,
            "ladder": self.entries["ladder"].get(),
            "ladder_pause": self.entries["ladder_pause"].get(),
            "freq_list": self.entries["freq_list"].get(),
            "delay": self._number("delay"),
            "mtm": self._number("mtm"),
            "wire_mode": self.wire_combobox.get(),
            "lcr_visa": visa_addr,
            "folder": self.file_location_path,
        }
        # Boxes the mode uses are checked here with their window label; a
        # greyed box may stay blank (validate_params counts it as 0).
        for key in ("v_start", "v_stop"):
            if key not in ignored:
                raw[key] = self._number(key)
        if "ladder_pause" not in ignored and raw["ladder_pause"].strip():
            raw["ladder_pause"] = self._number("ladder_pause")
        for tok in raw["freq_list"].split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                f = float(tok)
            except ValueError:
                raise ValueError(f"Frequencies (Hz): '{tok}' is not a number.")
            if not math.isfinite(f):
                raise ValueError(f"Frequencies (Hz): '{tok}' must be a finite number.")
        if not all([raw["sample_name"], raw["lcr_visa"], raw["folder"]]):
            raise ValueError(
                "Sample Name, VISA address, and Save Location are required."
            )
        # A tab or other control character pasted into the name would only
        # fail at the first file write, after the safety dialog.
        if any(ord(ch) < 32 for ch in raw["sample_name"]):
            raise ValueError("Sample Name must not contain tabs or other "
                             "control characters (used in file names).")
        # A folder that has gone (USB stick out, network share renamed)
        # is refused here, not reported as a runtime error after Start.
        if not os.path.isdir(raw["folder"]):
            raise ValueError(f"Save Location: folder not found: {raw['folder']}")
        if not os.access(raw["folder"], os.W_OK):
            raise ValueError(f"Save Location: folder is not writable: {raw['folder']}")
        return validate_params(raw)

    def start_sweep(self):
        if self.is_running or self._stopping:
            return
        t = self.worker_thread
        if t is not None and t.is_alive():
            self.log("Previous run is still finishing (bias ramp-down); wait "
                     "for it before starting again.")
            return
        try:
            params = self._collect_params()
        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Input Error", f"Could not start sweep.\n\n{e}")
            return

        if params["clamped"]:
            self.log(
                f"WARNING: voltage limits clamped to +/-{V_ABS_MAX} V "
                f"(hardcoded safety cutoff)."
            )
        if params["cycle_mode"] and params["ladder"]:
            self.log(
                f"Ladder: {len(params['ladder'])} cycle(s) at "
                f"{', '.join(f'{v:g}' for v in params['ladder'])} V, "
                f"pause {params['ladder_pause']:g} s between entries, "
                f"{len(params['waveform'])} points/frequency."
            )
        elif params["cycle_mode"]:
            self.log(
                f"Cycle mode: {params['n_cycles']} cycle(s), {params['shape']}, "
                f"Vmax = {params['v_max']} V, {len(params['waveform'])} points/frequency."
            )
        else:
            self.log(
                f"Linear mode: {params['v_start']} -> {params['v_stop']} V, "
                f"{len(params['waveform'])} points/frequency."
            )

        # --- Mandatory discharge confirmation (operator is present at Start) ---
        if not messagebox.askokcancel(
            "Safety Check",
            "Warning: Ensure the sample/capacitor is fully discharged "
            "before measurement to prevent instrument damage.\n\n"
            "Proceed with CV sweep?",
            icon="warning",
        ):
            self.log("Sweep aborted by user at discharge check.")
            return

        self._hide_alarm()
        self._bias_confirmed = None
        self.voltage_points = [v for _, _, v in params["waveform"]]
        self.freq_list = params["freqs"]
        self.sweep_delay = params["delay"]

        self.is_running = True
        self._end_reason = None         # set by the worker's "done"
        self._overdue_alarm = False
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.scan_button.config(state="disabled")
        self._set_inputs_locked(True)
        self.lbl_current_freq.config(text="Measuring: -- Hz")

        for key in self.data_storage:
            self.data_storage[key].clear()
        self.line_cp.set_data([], [])
        self.line_g.set_data([], [])
        self._decade_ylims.clear()  # fresh dataset re-snaps decades
        self.canvas.draw()

        self.sweep_index = 0
        self.progress_bar["value"] = 0
        self.progress_bar["maximum"] = len(self.freq_list) * len(self.voltage_points)

        self.log(
            f"Starting CV sweep: {len(self.freq_list)} freq(s) x "
            f"{len(self.voltage_points)} voltage points..."
        )

        # A fresh Event per run: an old worker still stuck after a stop
        # timeout keeps seeing its own Stop.
        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()
        self.link = AlphaANLink(params["lcr_visa"])
        self.worker = CVWorker(params, self.link, self.stop_event, self.data_queue)
        self.worker_thread = threading.Thread(target=self.worker.run, daemon=True)
        self.worker_thread.start()
        self.root.after(100, self._poll_queue)

    def _set_inputs_locked(self, locked):
        """Every input greyed during a run (the worker took its params at
        Start, so an edit could only make the window disagree with the file
        headers); released at the end with the mode-dependent greying
        re-applied."""
        state = "disabled" if locked else "normal"
        for entry in self.entries.values():
            entry.config(state=state)
        for box in (self.wire_combobox, self.lcr_combobox, self.shape_combobox):
            box.config(state="disabled" if locked else "readonly")
        self.cycle_check.config(state=state)
        self.browse_button.config(state=state)
        if not locked:
            self._on_cycle_mode_toggle()

    def stop_sweep(self, reason=""):
        """Signal the worker to stop, then finish cleanup asynchronously.
        The worker zeroes the bias, writes the END lines and closes the
        link itself; the GUI only waits for it (non-blocking poll)."""
        if self._stopping or not self.is_running:
            return
        self._stopping = True
        self.is_running = False
        self.stop_event.set()
        self.lbl_current_freq.config(text="Measuring: STOPPING...")
        self.stop_button.config(state="disabled")

        if reason:
            self.log(f"Sweep stopped: {reason}")
        else:
            self.log("Sweep stopped by user.")
        self.log("Waiting for worker thread to finish...")

        # The worker finishes the current point (or bias ramp), then ramps
        # the bias down and closes the link. Until then the window must not
        # touch the link (two threads on one VISA session desync the acks).
        mtm = (self.worker.p.get("mtm", MTM_DEFAULT_S)
               if self.worker is not None else MTM_DEFAULT_S)
        self._stop_deadline = time.time() + stop_wait_s(self.freq_list, mtm)
        self._poll_worker_stopped(reason)

    def _poll_worker_stopped(self, reason):
        self._drain_queue()
        t = self.worker_thread
        if (
            t is not None
            and t.is_alive()
            and time.time() < self._stop_deadline
        ):
            self.root.after(200, lambda: self._poll_worker_stopped(reason))
            return
        if t is not None and t.is_alive():
            # Overdue (e.g. the Alpha stopped answering: every shutdown
            # command then waits out the 60 s session timeout, several
            # minutes in all). The window still never touches the link while
            # the worker uses it - a second thread on the same VISA session
            # desyncs the acks and would freeze this window inside a 60 s
            # query. Warn (banner, no dialog) and keep waiting.
            if not getattr(self, "_forced_zero", False):
                self._forced_zero = True
                self._overdue_alarm = True
                self.log(
                    "WARNING: the worker has not finished its shutdown "
                    "within the expected time (the Alpha may not be "
                    "answering). Still waiting for it to ramp the DC bias "
                    "to 0 V; Start stays disabled until it has exited."
                )
                self._show_alarm(STOP_OVERDUE_TEXT)
            self.root.after(1000, lambda: self._poll_worker_stopped(reason))
            return
        self._forced_zero = False
        if self.link is not None and self.link.inst is not None:
            # the worker died without its own cleanup: zero the bias here
            self._force_bias_off_from_window()
        self._finalize_stop(reason)

    def _force_bias_off_from_window(self):
        """Last resort from the window thread (worker gone or overdue)."""
        ok = False
        try:
            ok = bool(self.link.force_zero_bias())
            ok = bool(self.link.close()) or ok
        except Exception as e:
            self.log(f"WARNING: error closing instrument: {e}")
        self._bias_confirmed = ok

    def _finalize_stop(self, reason):
        self._drain_queue()
        # The last word on the label: how the run ended (the worker's
        # DISCHARGING and the window's STOPPING... came after it).
        end = getattr(self, "_end_reason", None)
        if end == "complete":
            final = "DONE"
        elif end == "stop_button":
            final = "STOPPED by user"
        elif end:
            final = "ERROR (see console)"
        else:
            final = "STOPPED"
        self.lbl_current_freq.config(text=f"Measuring: {final}")
        self.start_button.config(state="normal")
        self.scan_button.config(state="normal")
        self._set_inputs_locked(False)
        self._stopping = False
        if self._bias_confirmed:
            if getattr(self, "_overdue_alarm", False):
                self._overdue_alarm = False
                self._hide_alarm()
            self.log("Instrument disconnected (bias 0 V, off).")
        else:
            self.log("Instrument disconnected, but the DC bias was NOT "
                     "confirmed 0 V and off.")
            self._show_alarm(BIAS_OFF_FAILED_TEXT)

        if self._close_after_stop:
            self.root.destroy()

    # -- Queue handling --------------------------------------------------------
    def _drain_queue(self):
        """Process everything the worker has queued; no re-scheduling."""
        while True:
            try:
                msg = self.data_queue.get_nowait()
            except queue.Empty:
                return
            self._handle_message(msg)

    def _handle_message(self, msg):
        kind = msg[0]
        if kind == "log":
            self.log(msg[1])
        elif kind == "status":
            self.lbl_current_freq.config(text=f"Measuring: {msg[1]}")
        elif kind == "new_file":
            self._on_new_file(msg[1], msg[2])
        elif kind == "point":
            _, f, v, cycle, seg, status, derived = msg
            self.sweep_index += 1
            self.lbl_current_freq.config(
                text=f"f = {f:,.6g} Hz | V = {v:+.3f} V | cycle {cycle} seg {seg}"
            )
            self._process_sweep_point(v, status, derived)
            self._update_sweep_plot()
        elif kind == "error":
            self._handle_sweep_error(msg[1])
        elif kind == "bias":
            _, ok, text = msg
            self._bias_confirmed = ok
            self.log(text)
            if not ok:
                self._show_alarm(text)
        elif kind == "done":
            self._handle_sweep_completion(msg[1])

    def _poll_queue(self):
        """Main thread: processes data from the worker thread."""
        self._drain_queue()
        if self.is_running:
            self.root.after(100, self._poll_queue)

    def _on_new_file(self, path, freq):
        self.data_filepath = path
        for key in self.data_storage:
            self.data_storage[key].clear()
        self.line_cp.set_data([], [])
        self.line_g.set_data([], [])
        self._decade_ylims.clear()  # fresh dataset re-snaps decades
        self.canvas.draw_idle()

    def _scan_for_visa(self):
        """
        Identity-aware instrument scan, Alpha-safe. Queries *IDN? with
        EOI-only framing (the Alpha uses no newline terminator) and
        auto-selects the device reporting Novocontrol/ALPHA (falling back
        to GPIB address {DEFAULT_ALPHA_PAD}, the Alpha's address in
        WinALPHA). A board left not-Controller-In-Charge by
        WinALPHA/WinDETA gets ONE Interface Clear, then the query is
        retried once.
        """
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA/VISA manager unavailable.")
            return

        try:
            rm = pyvisa.ResourceManager()
        except Exception as e:
            self.log(f"ERROR: VISA manager unavailable: {e}")
            return

        def _identify(res):
            with rm.open_resource(res) as dev:
                dev.timeout = 2000
                dev.read_termination = ""
                dev.write_termination = ""
                dev.send_end = True
                return dev.query("*IDN?").strip(" \t\r\n\x00\x10")

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        found = []
        alpha_label = None
        alpha_default = None
        cleared_boards = set()

        try:
            for res in rm.list_resources():
                idn = "Unknown / no response"
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
                                 f"Sending one Interface Clear...")
                        try:
                            intfc = rm.open_resource(f"GPIB{board}::INTFC")
                            try:
                                intfc.send_ifc()
                            finally:
                                intfc.close()
                            idn = _identify(res)
                        except Exception:
                            pass
                    # otherwise busy / non-SCPI / timeout - skip silently

                label = f"{res}  ->  {idn}"
                found.append(label)
                self.log(f"  {label}")
                upper = idn.upper()
                if alpha_label is None and (
                        "NOVOCONTROL" in upper or "ALPHA" in upper):
                    alpha_label = label
                if (alpha_default is None and res.upper().startswith("GPIB")
                        and f"::{DEFAULT_ALPHA_PAD}::" in res):
                    alpha_default = label

            self.lcr_combobox["values"] = found

            if alpha_label:
                self.lcr_combobox.set(alpha_label)
                self.log("Alpha-AN auto-selected.")
            elif alpha_default:
                self.lcr_combobox.set(alpha_default)
                self.log(f"WARNING: no instrument identified itself as an "
                         f"Alpha; selected address {DEFAULT_ALPHA_PAD}. Start "
                         f"will confirm it is an Alpha with a ZG4.")
            elif found:
                self.lcr_combobox.set(found[0])
                self.log(
                    "WARNING: No Alpha-AN found; defaulted to first device."
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
                # Destroy is deferred to _finalize_stop so the worker
                # can exit cleanly without freezing the GUI.
                self._close_after_stop = True
                self.stop_sweep("User closed application.")
        elif self._stopping:
            # Stop already in progress; just close once it finishes.
            self._close_after_stop = True
        else:
            self.root.destroy()

    def _process_sweep_point(self, v, status, derived):
        # derived indices: Cp=4, G=2, Rs=11, Rp=13
        self.data_storage["volt"].append(v)
        self.data_storage["cp"].append(derived[4])
        self.data_storage["g"].append(derived[2])
        self.data_storage["rs"].append(derived[11])
        self.data_storage["rp"].append(derived[13])
        self.data_storage["status"].append(status)

    def _update_sweep_plot(self):
        self.line_cp.set_data(
            self.data_storage["volt"], self.data_storage["cp"]
        )
        self.line_g.set_data(
            self.data_storage["volt"], self.data_storage["g"]
        )

        for ax, line, key, data in (
            (self.ax_cp, self.line_cp, "cp", self.data_storage["cp"]),
            (self.ax_g, self.line_g, "g", self.data_storage["g"]),
        ):
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)
            if self._apply_y_scale(ax, data, key):
                # Log axis: a zero or negative G (noisy Zs' near 0) or Cp
                # (inductive) cannot be drawn and would show as a drop off
                # the bottom of the plot; leave a gap instead. Display only:
                # data_storage and the file keep every value.
                line.set_data(
                    self.data_storage["volt"],
                    [v if isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                     else float("nan") for v in data])

        self.canvas.draw_idle()
        self.progress_bar["value"] = self.sweep_index

    # Unattended rule: nothing modal during or after a run. Log, label, beep.
    def _handle_sweep_completion(self, reason):
        self._end_reason = reason       # _finalize_stop's final label
        if reason == "complete":
            self.lbl_current_freq.config(text="Measuring: DONE")
            self.log("CV sweep finished successfully.")
        else:
            self.log(f"Run ended: {reason}")
        try:
            self.root.bell()
        except tk.TclError:
            pass
        if self.is_running:
            self.stop_sweep(f"Worker finished ({reason}).")

    def _show_alarm(self, text):
        """Red banner + bell. Never a dialog (unattended runs)."""
        banner = self.alarm_banner
        if banner is not None:
            try:
                banner.config(text=text)
                if not banner.winfo_ismapped():
                    banner.pack(side="top", fill="x", before=self.main_pane)
            except tk.TclError:
                pass
        try:
            self.root.bell()
        except tk.TclError:
            pass

    def _hide_alarm(self):
        banner = self.alarm_banner
        if banner is not None:
            try:
                banner.pack_forget()
            except tk.TclError:
                pass

    def _handle_sweep_error(self, text):
        self.log(f"RUNTIME ERROR: {text}")
        self.lbl_current_freq.config(text="Measuring: ERROR (see console)")
        try:
            self.root.bell()
        except tk.TclError:
            pass


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
