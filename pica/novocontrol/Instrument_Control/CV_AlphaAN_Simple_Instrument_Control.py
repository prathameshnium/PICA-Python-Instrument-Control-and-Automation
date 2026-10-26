"""
 PROGRAM:      Novocontrol Alpha-AN CV sweep, plain script (no GUI)
 VERSION:      1.0 (Alpha-AN port, 22 Oct 2026)
 PORT OF:      keysight/Instrument_Control/CV_KE4980A_Simple_Instrument_
               Control.py v2.0, with the same changes as its window twin
               CV_AlphaAN_GUI.py v1.0: the instrument layer is the Alpha-AN
               backend (EOI-only framing, every set command's "OK" ack
               read, MST + SRQ + ZRE?, ZG4 AC ceiling, safe state on every
               exit); aperture -> MTM, ALC / corrections / cable length
               removed, ZG4 wire mode added; Zs', Zs'' converted with
               f_actual (new last column), files keyed by the requested
               frequency; status 6 aborts; *_mon columns NaN (no verified
               Alpha monitor query); no bus-error retry (gentle GPIB: one
               attempt per operation, a bus error ends the run).
               Edit the USER INPUT block, run the script, get one
               plotter-compatible data file per frequency.
               Not yet run on the hardware.

 SAFETY:       This is the one PICA module pair that uses the Alpha's DC
               bias: the C-V staircase. Hard-capped at +/-20 V (V_ABS_MAX;
               checked before any byte is sent), switched on at 0 V, ramped
               in 0.5 V steps, and at the end, on error and on Ctrl-C
               ramped to 0 V and switched off BEFORE the generator safe
               state (MBK, ACV=0, ZCONSPL=0) and *RST. The DCV/DCE syntax
               is UNVERIFIED against the Alpha manual (see
               AlphaAN_Backend._dc_bias_command, the only place a bias
               command is formed).

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

 The pure functions, backend, link and worker are copied from the GUI
 module (PICA programs are self-contained; nothing is imported between
 them).
"""

import os
import sys
import time
import math
import queue
import threading
import traceback
from datetime import datetime
import numpy as np

try:
    import pyvisa
except ImportError:
    pyvisa = None

# ---------------------------------------------------------------
# USER INPUT (edit here; placeholders only, no sample names in the repo)
SAMPLE_NAME = "Sample_A"
VISA_ADDRESS = "GPIB0::10::INSTR"  # the Alpha on the BDS PC (WinALPHA auto-detect)
SAVE_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop", "CV_data")
CYCLE_MODE = True            # True: loops of the chosen SHAPE; False: one way v_start -> v_stop
SHAPE = "positive first"     # "positive first" | "negative first" | "unipolar positive" | "unipolar negative" | "ladder"
LADDER_LIST_V = [0.5, 1.0, 2.0]  # ladder shape only: one bipolar cycle per amplitude, in this order
LADDER_PAUSE_S = 0.0         # ladder shape only: rest at 0 V this long between entries (0 = none)
# Fields a mode/shape ignores (same rule as the window, disabled_fields()):
#   linear mode  -> N_CYCLES, SHAPE, LADDER_LIST_V ignored
#   cycle shapes -> V_START ignored; ladder shape also ignores V_STOP, N_CYCLES
V_START = -2.0               # linear mode only (V)
V_STOP = 2.0                 # linear mode end (V); cycle mode amplitude = |V_STOP|
V_STEP = 0.05                # V
N_CYCLES = 1                 # cycle mode, non-ladder shapes only
FREQUENCIES_HZ = [1000.0]    # measured in this order
AC_LEVEL_VRMS = 1.0          # ACV; ZG4 ceiling 3 Vrms <= 4 MHz, 2 Vrms to 10 MHz, 1 Vrms above
DELAY_S = 0.2                # hold per point before the trigger
MTM_S = 0.5                  # integration time (s); the Alpha never integrates < 1 period
WIRE_MODE = "2"              # ZG4 wire mode (FRS): "2" | "3" | "4"
# ---------------------------------------------------------------

VERSION = "1.0"
MODULE_NAME = "CV_AlphaAN_Simple_Instrument_Control"

# --- Absolute hardware safety limits (Alpha-AN DC bias) ---
V_ABS_MAX = 20.0        # Hardcoded DC bias ceiling. NEVER raise this.
# The AC ceiling is the ZG4's frequency-dependent one (acv_ceiling()).

RAMP_STEP_V = 0.5
RAMP_DWELL_S = 0.1
RAMP_SKIP_V = 1e-4
V_STEP_MIN = 1e-3

BIAS_ON_SETTLE_S = 0.5
MTM_DEFAULT_S = 0.5

# ZRE? status: only 2 is written as data; any other status keeps its row
# (raw Zs', Zs'', Status) with the 18 derived columns NaN; 6 aborts the run.

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


# ===============================================================================
# PURE FUNCTIONS (copied from CV_AlphaAN_GUI.py v1.0)
# ===============================================================================

class BusError(Exception):
    """A VISA failure while opening the link. No retry (gentle GPIB): the
    worker ends the run on it, with the bias ramped to 0 V and off."""


BIAS_OFF_FAILED_TEXT = (
    "BIAS_OFF_FAILED: the DC bias could NOT be confirmed 0 V and off. "
    "The sample may still be biased: switch the bias off at the Alpha "
    "(or WinDETA) and discharge the sample before touching it.")


class StopRequested(Exception):
    """Raised inside the worker when Stop is requested (Ctrl-C here)."""


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
# LINK (copied from CV_AlphaAN_GUI.py v1.0)
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
# WORKER (copied from CV_AlphaAN_GUI.py v1.0; messages go to a queue that
# this script prints)
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
# SCRIPT ENTRY
# ===============================================================================

SHAPE_BY_WORD = {
    "positive first": SHAPE_POS_FIRST,
    "negative first": SHAPE_NEG_FIRST,
    "unipolar positive": SHAPE_UNI_POS,
    "unipolar negative": SHAPE_UNI_NEG,
    "ladder": SHAPE_LADDER,
}


def params_from_user_input():
    """The USER INPUT block as the params dict the worker expects."""
    return validate_params({
        "sample_name": SAMPLE_NAME,
        "ac_bias": AC_LEVEL_VRMS,
        "v_start": V_START,
        "v_stop": V_STOP,
        "v_step": V_STEP,
        "n_cycles": N_CYCLES,
        "cycle_mode": CYCLE_MODE,
        "shape": SHAPE_BY_WORD.get(SHAPE, SHAPE),
        "ladder": ", ".join(str(v) for v in LADDER_LIST_V),
        "ladder_pause": LADDER_PAUSE_S,
        "freq_list": ",".join(str(f) for f in FREQUENCIES_HZ),
        "delay": DELAY_S,
        "mtm": MTM_S,
        "wire_mode": WIRE_MODE,
        "lcr_visa": VISA_ADDRESS,
        "folder": SAVE_FOLDER,
    })


def main():
    if pyvisa is None:
        print("PyVISA is not installed. Run: pip install pyvisa")
        return 1
    try:
        p = params_from_user_input()
    except ValueError as exc:
        print(f"Input error: {exc}")
        return 1
    if p["clamped"]:
        print(f"WARNING: voltage limits clamped to +/-{V_ABS_MAX:g} V (hardcoded safety cutoff).")
    os.makedirs(p["folder"], exist_ok=True)
    print(f"CV sweep v{VERSION}: {len(p['freqs'])} frequency file(s) x {len(p['waveform'])} points")
    print("Ensure the sample is fully discharged before connecting. Ctrl-C stops the run safely.")

    stop = threading.Event()
    q = queue.Queue()
    link = AlphaANLink(p["lcr_visa"])
    worker = CVWorker(p, link, stop, q)
    # Daemon, so a worker hung on a dead bus cannot keep the process alive
    # after the forced bias-off below; Ctrl-C never abandons it before then.
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()

    def show(msg):
        """Print one worker message; True on "done"."""
        kind = msg[0]
        if kind == "log":
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg[1]}")
        elif kind == "status":
            print(f"  status: {msg[1]}")
        elif kind == "point":
            _, f, v, cycle, seg, status, derived = msg
            print(f"  f={f:g} Hz | V={v:+.3f} V | cycle {cycle} seg {seg} | "
                  f"Cp={derived[4]:.4e} F | G={derived[2]:.4e} S | st={status}")
        elif kind == "error":
            print(f"RUNTIME ERROR: {msg[1]}")
        elif kind == "bias":
            print(msg[2])
        elif kind == "done":
            print(f"Run ended: {msg[1]}")
            return True
        return False

    def drain():
        while True:
            try:
                show(q.get_nowait())
            except queue.Empty:
                return

    done = False
    try:
        while not done:
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                continue
            done = show(msg)
    except KeyboardInterrupt:
        print("Ctrl-C: stopping (bias to 0 V, END lines written). "
              "Further Ctrl-C is ignored until the bias is off...")
        stop.set()
        # Wait as long as the worker may need (current point, bias ramp,
        # discharge, close); a second Ctrl-C must not cut the discharge.
        deadline = time.monotonic() + stop_wait_s(p["freqs"], p["mtm"])
        while t.is_alive() and time.monotonic() < deadline:
            try:
                t.join(0.2)
                drain()
            except KeyboardInterrupt:
                print("Still ramping the DC bias down; please wait.")
        if t.is_alive():
            print("WARNING: the worker did not finish in time; forcing the "
                  "DC bias to 0 V and closing the instrument from here.")
            ok = link.force_zero_bias()
            ok = link.close() or ok
            print("DC bias confirmed 0 V and off." if ok else BIAS_OFF_FAILED_TEXT)
            return 2
    drain()
    if worker.bias_confirmed is not True:
        return 2
    return 1 if worker.end_reason.startswith("error") else 0


if __name__ == "__main__":
    sys.exit(main())
