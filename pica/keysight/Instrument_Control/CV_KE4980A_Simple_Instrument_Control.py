"""
 PROGRAM:      Keysight E4980A CV sweep, plain script (no GUI)
 VERSION:      2.0 (13 Oct 2026)
 PURPOSE:      The same C-V run path as CV_KE4980A_GUI.py v2.0 without a
               window: edit the USER INPUT block, run the script, get one
               plotter-compatible data file per frequency with the same
               header, columns, cycle/segment labels, monitors, status
               handling, bus-error retry and END lines. Raw SCPI over
               PyVISA, BUS trigger (:TRIG:IMM, *OPC?, :FETC?), like every
               other E4980A module in PICA. Requires Option 001.

 SAFETY:       DC bias hard-capped at +/-20 V (V_ABS_MAX), ramped in 0.5 V
               steps, forced to 0 V and switched off at the end, on error
               and on Ctrl-C.

 The pure functions, link and worker are copied from the GUI module (PICA
 programs are self-contained; nothing is imported between them).
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
VISA_ADDRESS = "GPIB1::17::INSTR"
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
AC_LEVEL_VRMS = 1.0
DELAY_S = 0.2                # hold per point before the trigger
APERTURE = "MED"             # SHOR | MED | LONG
ALC_ENABLED = True
CORRECTIONS_ENABLED = True   # open/short
CABLE_LENGTH_M = "1"         # "0" | "1" | "2" | "4"
# ---------------------------------------------------------------

VERSION = "2.0"
MODULE_NAME = "CV_KE4980A_Simple_Instrument_Control"

# --- Absolute hardware safety limits (E4980A) ---
V_ABS_MAX = 20.0        # Hardcoded DC bias ceiling. NEVER raise this.
V_AC_MAX = 2.0          # AC test signal ceiling (Vrms), standard unit spec.
FREQ_MIN, FREQ_MAX = 20.0, 2e6

RAMP_STEP_V = 0.5
RAMP_DWELL_S = 0.1
RAMP_SKIP_V = 1e-4
V_STEP_MIN = 1e-3

VISA_TIMEOUT_S = 60.0
RST_SLEEP_S = 1.0
SETTLE_S = 0.2
AC_SETTLE_S = 0.5
BIAS_ON_SETTLE_S = 0.5

RETRY_SCHEDULE_S = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

E4980_OVERLOAD = 9.9e37
STATUS_TEXT = {-1: "no data", 0: "normal", 1: "overload",
               3: "signal source overloaded", 4: "ALC unable to regulate"}
NAN_STATUSES = (-1, 1)

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
    "Vac_mon(V)\tIac_mon(A)\tR_raw\tX_raw\tStatus"
)
COLUMN_ROW = "Voltage(V)\tFrequency\t" + DERIVED_HEADER + "\t" + EXTRA_HEADER
N_DERIVED = 18


# ===============================================================================
# PURE FUNCTIONS (copied from CV_KE4980A_GUI.py v2.0)
# ===============================================================================

class BusError(Exception):
    """Any VISA failure; the worker applies the retry rule to it."""


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
        if not (FREQ_MIN <= f <= FREQ_MAX):
            raise ValueError(
                f"Frequency {f} Hz outside {FREQ_MIN}-{FREQ_MAX} Hz."
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
    """The bias is written as :BIAS:VOLT with 1 mV resolution; a value
    between millivolts would be rounded while the file kept the typed V."""
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
    if not (0 < float(p["ac_bias"]) <= V_AC_MAX):
        raise ValueError(f"AC level must be in (0, {V_AC_MAX}] Vrms.")
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
    p["waveform"] = build_waveform(
        p["cycle_mode"], p["shape"], p["v_max"], p["v_start"], p["v_stop"],
        p["v_step"], int(p["n_cycles"]), p["ladder"],
    )
    return p


# ===============================================================================
# LINK (copied from CV_KE4980A_GUI.py v2.0)
# ===============================================================================

class E4980ALink:
    """Transport plus the E4980A configuration sequence. The last
    configuration is remembered so reconnect() can re-assert it; after a
    reconnect the bias is on at 0 V and the worker ramps it to the point."""

    def __init__(self, address, timeout_s=VISA_TIMEOUT_S, sleep=time.sleep):
        self.address = address
        self.timeout_s = float(timeout_s)
        self.sleep = sleep
        self.rm = None
        self.inst = None
        self.idn = ""
        self.opt = ""
        self.has_opt001 = False
        self.config = None
        self.freq = None            # last :FREQ written, re-asserted by reconnect()
        self.bias_target = None     # last ramp_bias() target, re-ramped by reconnect()

    # -- transport ------------------------------------------------------
    def open(self):
        if pyvisa is None:
            raise BusError("PyVISA is not installed.")
        try:
            if self.rm is None:
                self.rm = pyvisa.ResourceManager()
            inst = self.rm.open_resource(self.address)
            inst.timeout = int(self.timeout_s * 1000)
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            self.inst = inst
        except Exception as exc:
            self.inst = None
            raise BusError(f"open {self.address}: {exc}")

    def close(self):
        try:
            if self.inst is not None:
                self.inst.close()
        except Exception:
            pass
        self.inst = None

    def write(self, cmd):
        if self.inst is None:
            raise BusError("not connected")
        try:
            self.inst.write(cmd)
        except Exception as exc:
            raise BusError(f"write {cmd!r}: {exc}")

    def query(self, cmd):
        if self.inst is None:
            raise BusError("not connected")
        try:
            return str(self.inst.query(cmd)).strip()
        except Exception as exc:
            raise BusError(f"query {cmd!r}: {exc}")

    def query_floats(self, cmd):
        reply = self.query(cmd)
        try:
            return [float(tok) for tok in reply.split(",") if tok.strip()]
        except ValueError:
            raise BusError(f"unparseable reply to {cmd!r}: {reply!r}")

    # -- startup ----------------------------------------------------------
    def identify(self):
        self.idn = self.query("*IDN?")
        if "E4980" not in self.idn:
            raise RuntimeError(f"Not an E4980A: {self.idn}")  # not a bus fault: no retry
        self.opt = self.query("*OPT?")
        self.has_opt001 = "001" in self.opt
        return self.idn

    def check_errors(self, context):
        errors = []
        for _ in range(10):
            err = self.query(":SYST:ERR?")
            if err.startswith("0,") or err.startswith("+0,"):
                break
            errors.append(err)
        if errors:
            raise RuntimeError(f"SCPI errors after {context}: {errors}")  # setting fault: no retry

    def configure(self, p):
        """The CV configuration (R-X function, BUS trigger, all four
        monitors on, bias on at 0 V). Remembered for reconnect()."""
        self.config = dict(p)
        if not self.has_opt001:
            raise RuntimeError(  # not a bus fault: no retry
                "CV sweeps require Option 001 (continuous DC bias). "
                "Standard unit only supports discrete 0/1.5/2 V bias."
            )
        self.write("*RST; *CLS")
        self.sleep(RST_SLEEP_S)
        self.write(":DISP:ENAB ON")
        self.sleep(SETTLE_S)

        self.write(":FUNC:IMP RX")
        self.write(f":APER {p['aper']}")
        self.write(":FUNC:IMP:RANG:AUTO ON")
        self.sleep(SETTLE_S)

        self.write(":FORM ASC")

        # All four monitors on so the measured waveform goes into the file
        # (VDC and IDC need Option 001, manual p. 75; read with
        # :FETC:SMON:xxx?, p. 347).
        self.write(":FUNC:SMON:VAC ON")
        self.write(":FUNC:SMON:IAC ON")
        self.write(":FUNC:SMON:VDC ON")
        self.write(":FUNC:SMON:IDC ON")
        self.sleep(SETTLE_S)

        self.write(":AMPL:ALC ON" if p["alc_enabled"] else ":AMPL:ALC OFF")
        self.sleep(SETTLE_S)

        self.write(f":CORR:LENG {p['cable_len']}")
        if p["corr_enabled"]:
            self.write(":CORR:OPEN:STAT ON")
            self.write(":CORR:SHOR:STAT ON")
        else:
            self.write(":CORR:OPEN:STAT OFF")
            self.write(":CORR:SHOR:STAT OFF")
        self.sleep(SETTLE_S)

        self.write(f":VOLT {p['ac_bias']}")
        self.sleep(AC_SETTLE_S)

        # BUS trigger: :TRIG:IMM moves Idle -> Measurement (manual p. 249-250)
        self.write(":TRIG:SOUR BUS")
        self.write(":INIT:CONT ON")
        self.sleep(SETTLE_S)

        # CV mode: start at 0 V with bias output enabled; sweep sets values.
        self.write(":BIAS:VOLT 0")
        self.write(":BIAS:STAT ON")
        self.sleep(BIAS_ON_SETTLE_S)

        self.check_errors("configuration")

    def snapshot(self):
        """Instrument-side settings for the file header (query only)."""
        snap = {}
        for key, cmd in (
            ("aperture", ":APER?"),
            ("ac_level_vrms", ":VOLT?"),
            ("alc", ":AMPL:ALC?"),
            ("open_corr", ":CORR:OPEN:STAT?"),
            ("short_corr", ":CORR:SHOR:STAT?"),
            ("cable_m", ":CORR:LENG?"),
            ("bias_range_auto", ":BIAS:RANG:AUTO?"),
            ("impedance_range_auto", ":FUNC:IMP:RANG:AUTO?"),
            ("bias_state", ":BIAS:STAT?"),
        ):
            try:
                snap[key] = self.query(cmd)
            except BusError:
                snap[key] = "unknown"
        return snap

    def reconnect(self):
        """Close, reopen, identify, re-configure (*RST leaves the unit at
        1 kHz, bias on at 0 V), then put back the frequency and ramp the
        bias to the point the run was at, so the retried command measures
        what its row says."""
        self.close()
        self.open()
        self.identify()
        if self.config is not None:
            self.configure(self.config)
            if self.freq is not None:
                self.set_frequency(self.freq)
            if self.bias_target is not None:
                self.ramp_bias(self.bias_target)

    # -- bias -------------------------------------------------------------
    def bias_voltage(self):
        return float(self.query(":BIAS:VOLT?"))

    def ramp_bias(self, target_v, step=RAMP_STEP_V, dwell=RAMP_DWELL_S):
        """Ramp the DC bias to target_v. Final hardware-protection clamp."""
        if abs(target_v) > V_ABS_MAX:  # defense in depth, should never trigger
            raise ValueError(
                f"Bias {target_v} V blocked by hardcoded {V_ABS_MAX} V cutoff."
            )
        self.bias_target = float(target_v)
        current_v = self.bias_voltage()
        if abs(target_v - current_v) < RAMP_SKIP_V:
            return
        if step <= 0:
            self.write(f":BIAS:VOLT {target_v:.3f}")
            return
        direction = 1 if target_v > current_v else -1
        pts = list(np.arange(current_v, target_v, direction * step)) + [target_v]
        for v in pts:
            self.write(f":BIAS:VOLT {v:.3f}")
            self.sleep(dwell)

    def force_zero_bias(self):
        """Emergency: drive the bias to 0 V and switch it off. Swallows
        every error so it can be used in any error handler."""
        if self.inst is None:
            try:
                self.open()  # link lost mid-retry: reopen once to zero the bias
            except Exception as exc:
                print(f"  CRITICAL SAFETY WARNING: Failed to force bias to 0V! {exc}")
                return
        try:
            try:
                self.ramp_bias(0.0, step=1.0, dwell=0.05)
            except Exception:
                try:
                    self.inst.write(":BIAS:VOLT 0")
                    self.sleep(0.2)
                except Exception:
                    pass
            self.inst.write(":BIAS:STAT OFF")
        except Exception as exc:
            print(f"  CRITICAL SAFETY WARNING: Failed to force bias to 0V! {exc}")

    # -- measurement ------------------------------------------------------
    def set_frequency(self, freq):
        self.freq = freq
        self.write(f":FREQ {freq}")

    def trigger_and_fetch(self):
        """One bus-triggered measurement: R, X, status."""
        self.write(":TRIG:IMM")
        self.query("*OPC?")
        vals = self.query_floats(":FETC?")
        if len(vals) < 2:
            raise BusError(f"short :FETC? reply: {vals}")
        status = int(vals[2]) if len(vals) > 2 else 0
        return vals[0], vals[1], status

    def fetch_monitors(self):
        """Vdc, Idc, Vac, Iac of the latest measurement (manual p. 347)."""
        out = []
        for cmd in (":FETC:SMON:VDC?", ":FETC:SMON:IDC?",
                    ":FETC:SMON:VAC?", ":FETC:SMON:IAC?"):
            vals = self.query_floats(cmd)
            out.append(vals[0] if vals else float("nan"))
        return tuple(out)


# ===============================================================================
# WORKER (copied from CV_KE4980A_GUI.py v2.0; messages go to a queue that
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
        self.retry_total = 0
        self.files = []
        self.end_reason = ""
        self.in_retry = False

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

    def with_retry(self, fn, what="command"):
        """Run fn(); on BusError retry after 1, 2, 5, 10, 30, 60 s and then
        every 60 s until it succeeds or Stop is pressed. Each attempt is a
        RETRY line; a recovered gap is exactly one RECONNECT line."""
        attempt = 0
        gap_start = None
        while True:
            try:
                result = fn()
                if attempt:
                    self.log_event(
                        f"RECONNECT after {time.perf_counter() - gap_start:.1f} s | "
                        f"{what} succeeded on attempt {attempt + 1}"
                    )
                    self.log(f"Reconnected; {what} succeeded.")
                    self.in_retry = False
                return result
            except BusError as exc:
                attempt += 1
                self.retry_total += 1
                self.in_retry = True
                if gap_start is None:
                    gap_start = time.perf_counter()
                delay = RETRY_SCHEDULE_S[min(attempt - 1, len(RETRY_SCHEDULE_S) - 1)]
                self.log_event(f"RETRY n={attempt} | what={what} | error={header_value(exc)}")
                self.log(f"Bus error ({what}): {exc}. Retry {attempt} in {delay:g} s.")
                self.status(f"RECONNECTING (attempt {attempt}, next try in {delay:g} s)")
                # Reconnect until it fully succeeds; fn() only runs again on a
                # re-configured unit with frequency and bias restored.
                while True:
                    if wait_with_stop(delay, self.stop_event, sleep=self.sleep):
                        raise StopRequested()
                    try:
                        self.link.reconnect()
                        break
                    except BusError as exc2:
                        attempt += 1
                        self.retry_total += 1
                        delay = RETRY_SCHEDULE_S[min(attempt - 1, len(RETRY_SCHEDULE_S) - 1)]
                        self.log_event(f"RETRY n={attempt} | what=reconnect | error={header_value(exc2)}")
                        self.status(f"RECONNECTING (attempt {attempt}, next try in {delay:g} s)")

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
            f"# [instrument] idn={header_value(self.link.idn)} | opt={header_value(self.link.opt)} | "
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
            f"# [lcr] ac_level_vrms_set={p['ac_bias']:g} | ac_level_vrms_read={header_value(snap.get('ac_level_vrms'))} | "
            f"aperture={header_value(snap.get('aperture'))} | alc={header_value(snap.get('alc'))} | "
            f"open_corr={header_value(snap.get('open_corr'))} | short_corr={header_value(snap.get('short_corr'))} | "
            f"cable_m={header_value(snap.get('cable_m'))} | bias_range_auto={header_value(snap.get('bias_range_auto'))} | "
            f"impedance_range_auto={header_value(snap.get('impedance_range_auto'))} | "
            f"dci_isolation=instrument default (not set by this program) | monitors=VDC,IDC,VAC,IAC | "
            f"trigger=BUS | function=RX",
            f"# [timing] run_start={self.start_iso} | file_start={iso_now()} | t_s=seconds since run_start",
            f"# waveform: {header_value(waveform_text(p, freq))}",
            f"# segments: {segment_text(p['cycle_mode'], p['shape'])}; Cycle counts from 1",
            "# note: the E4980A DC bias is a voltage source with no current compliance (manual p. 326); "
            "Idc_mon(A) is the DC current through the sample at each bias",
            "# status: 0 normal | 1 overload | 3 signal source overloaded | 4 ALC unable to regulate | "
            "-1 no data; the 18 derived columns are NaN when status is -1 or 1 (data = 9.9E37)",
            f"# log_file={os.path.basename(self.log_path)}",
        ]
        return lines

    def _open_data_file(self, k, freq, snap):
        p = self.p
        fname = f"{p['sample_name']}_{self.run_stamp}_CV_{freq:.0f}Hz.txt"
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

    def _end_data_file(self, reason):
        if not self.data_path:
            return
        line = (f"END reason={header_value(reason)} | points={self.points_in_file} | "
                f"end_time={iso_now()} | file={os.path.basename(self.data_path)}")
        self._append(self.data_path, "# " + line + "\n")
        self.log_event(line)
        self.data_path = ""

    def _write_row(self, v, f, derived, t_s, stamp, cycle, seg, mons, R, X, status):
        vdc, idc, vac, iac = mons
        cells = [v, f] + list(derived) + [t_s, stamp, cycle, seg, vdc, idc, vac, iac, R, X, status]
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
            self.status("CONNECTING")
            self.with_retry(self.link.open, "open")
            self.with_retry(self.link.identify, "identify")
            self.log(f"Connected: {self.link.idn} | options: {self.link.opt}")
            self.with_retry(lambda: self.link.configure(p), "configure")
            snap = self.with_retry(self.link.snapshot, "snapshot")
            self.log("E4980A configured (RX, BUS trigger, monitors on, bias on at 0 V).")

            for k, f in enumerate(p["freqs"]):
                self.check_stop()
                self.with_retry(lambda f=f: self.link.set_frequency(f), "set frequency")
                self.with_retry(lambda: self.link.ramp_bias(0.0), "discharge")
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
                    self.with_retry(lambda v=v: self.link.ramp_bias(v), "set bias")
                    if wait_with_stop(p["delay"], self.stop_event, sleep=self.sleep):
                        raise StopRequested()
                    R, X, status = self.with_retry(self.link.trigger_and_fetch, "measure")
                    mons = self.with_retry(self.link.fetch_monitors, "monitors")
                    t_s = self.t_s()
                    stamp = iso_now()
                    if status in NAN_STATUSES:
                        derived = nan_derived()
                        self.log(f"WARNING: V={v:+.3f} V status {status} "
                                 f"({STATUS_TEXT.get(status, '?')}); derived values NaN.")
                    else:
                        if status != 0:
                            self.log(f"WARNING: V={v:+.3f} V status {status} "
                                     f"({STATUS_TEXT.get(status, '?')}).")
                        try:
                            derived = derived_columns(f, R, X)
                        except Exception as calc_err:
                            self.log(f"Calc error at V={v} V: {calc_err}. Derived values NaN.")
                            derived = nan_derived()
                    self._write_row(v, f, derived, t_s, stamp, cycle, seg, mons, R, X, status)
                    self.q.put(("point", f, v, cycle, seg, status, derived))
                self._end_data_file("complete")
            self.status("DONE")
        except StopRequested:
            reason = "stop_button_during_retry" if self.in_retry else "stop_button"
            self.log("Stop acknowledged by the worker.")
        except Exception as exc:
            reason = f"error: {exc}"
            self.q.put(("error", f"{exc}\n{traceback.format_exc()}"))
        finally:
            # Bias to 0 V first; a failing file write (e.g. the data file
            # locked by Excel) must never skip the discharge or the "done".
            try:
                self.status("DISCHARGING")
                for step in (self.link.force_zero_bias, self.link.close):
                    try:
                        step()
                    except Exception:
                        pass
                for step in (
                    lambda: self._end_data_file(reason),
                    lambda: self.log_event(
                        f"END reason={header_value(reason)} | points={self.points_total} | "
                        f"retries={self.retry_total} | files={len(self.files)} | end_time={iso_now()}"),
                ):
                    try:
                        step()
                    except Exception as exc:
                        self.log(f"WARNING: could not write the END line: {exc}")
            finally:
                self.end_reason = reason
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
        "aper": APERTURE,
        "alc_enabled": ALC_ENABLED,
        "corr_enabled": CORRECTIONS_ENABLED,
        "cable_len": CABLE_LENGTH_M,
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
    link = E4980ALink(p["lcr_visa"])
    worker = CVWorker(p, link, stop, q)
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    done = False
    try:
        while not done:
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                continue
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
            elif kind == "done":
                print(f"Run ended: {msg[1]}")
                done = True
    except KeyboardInterrupt:
        print("Ctrl-C: stopping (bias to 0 V, END lines written)...")
        stop.set()
        t.join(60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
