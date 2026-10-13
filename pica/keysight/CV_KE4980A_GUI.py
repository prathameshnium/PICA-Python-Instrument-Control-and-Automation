'''
 PROGRAM:      Keysight E4980A CV Scan (R-X) GUI
 VERSION:      2.0 (13 Oct 2026)
 PURPOSE:      Capacitance-Voltage (CV) sweeps on any sample: a DC bias
               staircase with the AC test signal on top, one data file per
               frequency, every row time-stamped and labelled with its cycle
               and segment, the four instrument monitors (Vdc, Idc, Vac, Iac)
               and the raw R, X and status beside the 18 derived values.
               Requires Option 001 (continuous DC bias) on the E4980A.

 FILE FORMAT (tab separated, plotter compatible):
   '#' header blocks ([instrument], [sample], [sweep], [frequency], [lcr],
   [timing]), one 'waveform:' line in words, the column row, one data row
   per point (flushed and fsynced), then '# END reason=... | points=...'.
   Columns: Voltage(V), Frequency and the 18 derived columns as before,
   then t_s, Timestamp, Cycle, Segment, Vdc_mon(V), Idc_mon(A), Vac_mon(V),
   Iac_mon(A), R_raw, X_raw, Status. Derived columns are NaN when the
   status is -1 or 1 (the instrument returns 9.9E37 then, manual p. 265).
   RETRY, RECONNECT and END events also go to <sample>_<ts>_CV_log.txt.

 BUS-ERROR RETRY: any VISA error is retried after 1, 2, 5, 10, 30, 60 s and
   then every 60 s forever (reconnect = close, reopen, re-configure, bias
   back on at 0 V and ramped to the current point). Stop ends the wait.

 LOOP SHAPES (cycle mode): positive first, negative first, unipolar +/-,
   and ladder (one bipolar cycle per listed amplitude, as in the memristor
   sweep module; Cycle = ladder entry number, ladder_list_V in the header;
   optional pause at 0 V between entries, ladder_pause_s, default 0).

 SAFETY: DC bias hard-capped at +/-20 V (V_ABS_MAX, never raised), bias
   ramped in 0.5 V steps, forced to 0 V and switched off on Stop, error,
   crash or exit. The bias is a voltage source with no current compliance
   (manual p. 326); Idc_mon(A) records the DC current through the sample.

 Pure functions and the link/worker classes carry no Tk so the run path is
 testable with a fake instrument (tests/test_cv_ke4980a.py).
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

VERSION = "2.0"
MODULE_NAME = "CV_KE4980A_GUI"

# --- Absolute hardware safety limits (E4980A) ---
V_ABS_MAX = 20.0        # Hardcoded DC bias ceiling. NEVER raise this.
                        # (Opt 001 hardware allows +/-40 V; we cap at +/-20 V.
                        # Manual p. 65: Vosc*sqrt(2)*1.15 + Vdc*1.002 < 42 V,
                        # which 20 V + 2 Vrms satisfies with margin.)
V_AC_MAX = 2.0          # AC test signal ceiling (Vrms), standard unit spec.
V_STD_BIAS_MAX = 2.0    # Max bias without Option 001.
FREQ_MIN, FREQ_MAX = 20.0, 2e6

# --- Bias ramp (the only soft start the instrument gets) ---
RAMP_STEP_V = 0.5       # volts per write while ramping the DC bias
RAMP_DWELL_S = 0.1      # seconds between ramp writes
RAMP_SKIP_V = 0.01      # |target - current| below this: no write at all

# --- Timing ---
VISA_TIMEOUT_S = 60.0   # low-freq + LONG + autorange can be slow
RST_SLEEP_S = 1.0       # graceful reset
SETTLE_S = 0.2          # after groups of setting writes
AC_SETTLE_S = 0.5       # after the AC level write
BIAS_ON_SETTLE_S = 0.5  # after :BIAS:STAT ON

# Bus-error retry (memristor protocol 2.8): 1, 2, 5, 10, 30, 60 s then every 60 s forever.
RETRY_SCHEDULE_S = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

# E4980A facts (User's Guide, printed page numbers).
E4980_OVERLOAD = 9.9e37             # p. 265: data are 9.9E37 when status is -1 or 1
STATUS_TEXT = {                     # p. 265
    -1: "no data",
    0: "normal",
    1: "overload",
    3: "signal source overloaded",
    4: "ALC unable to regulate",
}
NAN_STATUSES = (-1, 1)              # p. 265: only these carry no real data

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
    "Vac_mon(V)\tIac_mon(A)\tR_raw\tX_raw\tStatus"
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
    """Any VISA failure; the worker applies the retry rule to it."""


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


def validate_params(p):
    """Raise ValueError on anything the window should refuse at Start.
    Returns the params with v_max set and voltages clamped to V_ABS_MAX."""
    p = dict(p)
    v_step = abs(float(p["v_step"]))
    if v_step <= 0:
        raise ValueError("Voltage step must be > 0.")
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
        raw = p.get(key, "")
        if key in ignored and (raw is None or str(raw).strip() == ""):
            return 0.0
        return float(raw)

    v_start, v_stop = volts("v_start"), volts("v_stop")
    p["clamped"] = any(abs(v) > V_ABS_MAX for k, v in (("v_start", v_start), ("v_stop", v_stop))
                       if k not in ignored)
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
# LINK - raw SCPI over PyVISA (every failure becomes BusError)
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
            return
        try:
            try:
                self.ramp_bias(0.0, step=1.0, dwell=0.05)
            except Exception:
                self.inst.write(":BIAS:VOLT 0")
                self.sleep(0.2)
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
# WORKER - the run path (thread), file writing, retry, END lines
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
                if wait_with_stop(delay, self.stop_event, sleep=self.sleep):
                    raise StopRequested()
                try:
                    self.link.reconnect()
                except BusError as exc2:
                    self.log_event(f"RETRY n={attempt} | what=reconnect | error={header_value(exc2)}")
                    continue

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
            self._end_data_file(reason)
            self.status("DISCHARGING")
            try:
                self.link.force_zero_bias()
            except Exception:
                pass
            self.link.close()
            self.log_event(f"END reason={header_value(reason)} | points={self.points_total} | "
                           f"retries={self.retry_total} | files={len(self.files)} | end_time={iso_now()}")
            self.end_reason = reason
            self.q.put(("done", reason))


# ===============================================================================
# FRONTEND CLASS - The Main GUI Application
# ===============================================================================

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
        self.root.title(f"Keysight E4980A CV Scan (R-X) v{VERSION}")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self._stopping = False          # re-entrancy guard for stop_sweep
        self._close_after_stop = False  # destroy window once worker exits
        self.link = None
        self.worker = None
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

    def _atexit_cleanup(self):
        link = self.link
        if link is not None and link.inst is not None:
            link.force_zero_bias()
            link.close()

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
            text="Keysight E4980A: CV Scan (R-X)",
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

        # Row 9-10: Delay | Aperture
        self._add_entry(
            frame, "Delay per step (s)", "delay", 9, 0, default="0.2"
        )
        Label(
            frame, text="Aperture (:APER):", font=self.FONT_BASE
        ).grid(row=9, column=1, padx=padx, pady=pady, sticky="w")
        self.aper_combobox = ttk.Combobox(
            frame,
            font=self.FONT_BASE,
            state="readonly",
            values=["SHOR", "MED", "LONG"],
        )
        self.aper_combobox.set("MED")
        self.aper_combobox.grid(
            row=10, column=1, padx=padx, pady=(0, 10), sticky="ew"
        )

        # Row 11: ALC checkbox
        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Enable Auto Level Control (ALC)",
            variable=self.var_alc,
        ).grid(row=11, column=0, columnspan=2, padx=padx, pady=2, sticky="w")

        # Row 12: Corrections checkbox
        ttk.Checkbutton(
            frame,
            text="Enable Open/Short Corrections",
            variable=self.var_corr,
        ).grid(row=12, column=0, columnspan=2, padx=padx, pady=2, sticky="w")

        # Row 13: Cable Length selector
        Label(
            frame, text="Cable Length (m):", font=self.FONT_BASE
        ).grid(row=13, column=0, padx=padx, pady=pady, sticky="w")
        self.cable_len_combobox = ttk.Combobox(
            frame,
            font=self.FONT_BASE,
            state="readonly",
            values=["0", "1", "2", "4"],
        )
        self.cable_len_combobox.set("1")
        self.cable_len_combobox.grid(
            row=13, column=1, padx=padx, pady=pady, sticky="ew"
        )

        # Row 14-15: LCR Meter VISA
        Label(
            frame, text="LCR Meter VISA:", font=self.FONT_BASE
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

        ttk.Button(
            frame,
            text="Browse Save Loc...",
            command=self._browse_file_location,
        ).grid(row=16, column=1, padx=padx, pady=5, sticky="ew")

        # Row 17-18: Cycle-mode controls (CV cycle feature).
        # Cycle amplitude comes from V Stop; V Start is disabled in this mode.
        self.var_cycle = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Cycle mode (amplitude = |V Stop|)",
            variable=self.var_cycle,
            command=self._on_cycle_mode_toggle,
        ).grid(row=17, column=0, padx=padx, pady=2, sticky="w")

        self._add_entry(
            frame, "No. of Cycles", "n_cycles", 17, 1, default="1"
        )

        # Row 19-20: Loop shape (cycle mode only)
        Label(
            frame, text="Loop shape (cycle mode):", font=self.FONT_BASE
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
                "Note: the E4980A DC bias is a voltage source with no "
                "current compliance. The DC current through the sample is "
                "recorded per row as Idc_mon(A)."
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
        for key in ("v_start", "v_stop", "v_step", "ac_bias", "delay", "ladder_pause"):
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
            f"CV Scan v{VERSION} initialized. Requires Option 001 for continuous bias."
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

        scale_bar = ttk.Frame(parent)
        scale_bar.pack(anchor="w", padx=5, pady=(5, 0))
        ttk.Label(scale_bar, text="Y scale:").pack(side="left")
        for text, val in (("Auto", "auto"), ("Log", "log"),
                          ("Linear", "linear")):
            ttk.Radiobutton(scale_bar, text=text, value=val,
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
            parent, text=f"{text}:", font=self.FONT_BASE
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
    def _collect_params(self):
        """Raw window values -> validated params dict (raises ValueError)."""
        if not PYVISA_AVAILABLE:
            raise ValueError("PyVISA is not installed.")
        visa_addr = self.lcr_combobox.get().split("  ->  ")[0]
        cycle_mode = self.var_cycle.get()
        raw = {
            "sample_name": self.entries["sample_name"].get().strip(),
            "ac_bias": float(self.entries["ac_bias"].get()),
            "v_start": self.entries["v_start"].get(),
            "v_stop": self.entries["v_stop"].get(),
            "v_step": float(self.entries["v_step"].get()),
            "n_cycles": self.entries["n_cycles"].get(),
            "cycle_mode": cycle_mode,
            "shape": self.shape_combobox.get(),
            "ladder": self.entries["ladder"].get(),
            "ladder_pause": self.entries["ladder_pause"].get(),
            "freq_list": self.entries["freq_list"].get(),
            "delay": float(self.entries["delay"].get()),
            "aper": self.aper_combobox.get(),
            "alc_enabled": self.var_alc.get(),
            "corr_enabled": self.var_corr.get(),
            "cable_len": self.cable_len_combobox.get(),
            "lcr_visa": visa_addr,
            "folder": self.file_location_path,
        }
        if not all([raw["sample_name"], raw["lcr_visa"], raw["folder"]]):
            raise ValueError(
                "Sample Name, VISA address, and Save Location are required."
            )
        return validate_params(raw)

    def start_sweep(self):
        if self.is_running or self._stopping:
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

        self.voltage_points = [v for _, _, v in params["waveform"]]
        self.freq_list = params["freqs"]
        self.sweep_delay = params["delay"]

        self.is_running = True
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.scan_button.config(state="disabled")

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

        self.stop_event.clear()
        self.data_queue = queue.Queue()
        self.link = E4980ALink(params["lcr_visa"])
        self.worker = CVWorker(params, self.link, self.stop_event, self.data_queue)
        self.worker_thread = threading.Thread(target=self.worker.run, daemon=True)
        self.worker_thread.start()
        self.root.after(100, self._poll_queue)

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

        self._stop_deadline = time.time() + 15.0
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
            self.log(
                "WARNING: worker did not exit within timeout; "
                "forcing bias to 0 V and closing the instrument anyway."
            )
            try:
                self.link.force_zero_bias()
                self.link.close()
            except Exception as e:
                self.log(f"WARNING: error closing instrument: {e}")
        self._finalize_stop(reason)

    def _finalize_stop(self, reason):
        self._drain_queue()
        self.lbl_current_freq.config(text="Measuring: STOPPED")
        self.start_button.config(state="normal")
        self.scan_button.config(state="normal")
        self._stopping = False
        self.log("Instrument disconnected (bias 0 V, off).")

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
                text=f"f = {f:,.0f} Hz | V = {v:+.3f} V | cycle {cycle} seg {seg}"
            )
            self._process_sweep_point(v, status, derived)
            self._update_sweep_plot()
        elif kind == "error":
            self._handle_sweep_error(msg[1])
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
        """Identity-aware instrument scan."""
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA/VISA manager unavailable.")
            return

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        try:
            rm = pyvisa.ResourceManager()
        except Exception as e:
            self.log(f"ERROR: VISA manager unavailable: {e}")
            return
        found = []
        e4980_label = None

        try:
            for res in rm.list_resources():
                idn = "Unknown / no response"
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
                if "E4980" in idn and e4980_label is None:
                    e4980_label = label
                self.log(f"  {label}")

            self.lcr_combobox["values"] = found

            if e4980_label:
                self.lcr_combobox.set(e4980_label)
                self.log("E4980A auto-selected.")
            elif found:
                self.lcr_combobox.set(found[0])
                self.log(
                    "WARNING: No E4980A found; defaulted to first device."
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

        for ax, key, data in (
            (self.ax_cp, "cp", self.data_storage["cp"]),
            (self.ax_g, "g", self.data_storage["g"]),
        ):
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)
            self._apply_y_scale(ax, data, key)

        self.canvas.draw_idle()
        self.progress_bar["value"] = self.sweep_index

    # Unattended rule: nothing modal during or after a run. Log, label, beep.
    def _handle_sweep_completion(self, reason):
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

    def _handle_sweep_error(self, text):
        self.log(f"RUNTIME ERROR: {text}")
        self.lbl_current_freq.config(text="Measuring: ERROR (see console)")
        try:
            self.root.bell()
        except tk.TclError:
            pass


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
