"""
Module: Memristor_Sweep_K2400_GUI.py
Purpose: Switching I-V sweep of a memristor pellet with current compliance
         on a Keithley 2400 (protocol "Memristor measurement modules for
         PICA" v1.1, Section 3).

v1.1 (11 Oct 2026) - PICA GUI conventions
  * Information frame with the institute logo; plotter and GPIB-scanner
    buttons in the header; Browse... button for the output folder.
  * Electrode, area, thickness, HI-face and Notes entries removed; the
    [sample] header carries the sample name only (put such details in it).
  * Start / Stop / Open analysis moved into the left panel (a short screen
    could push the old bottom bar out of view).
  * File names follow the PICA convention <sample>_<YYYYMMDD_HHMMSS>_<kind>
    .csv; a rerun in the same second gets _1, _2 ... (never overwrites).
  * '# RETRY' / '# RECONNECT' lines keep ' | ' between fields like every
    other header line; one RECONNECT line per recovered gap.
v1.0 (11 Oct 2026) - first version
  * Shapes: bipolar (+ first / - first), unipolar (+ / -) and ladder; each
    loop starts and ends at 0 V, the turn voltage is measured once and the
    last outgoing point is exactly V max.
  * Compliance handling (protocol 3.4): after the first flagged point on an
    outgoing segment, n confirm further points are taken; when all are
    flagged the segment turns around from the present voltage and the rest
    of the loop is measured. Events: trip_first, trip_unconfirmed,
    trip_confirmed, trip_confirmed;turnaround; the file column event_code
    holds the integer code (0 none, 1 first, 2 unconfirmed, 3 confirmed,
    4 confirmed+turnaround, 5 ovp) so data rows stay numeric.
  * Separate I cc for the positive and the negative half (K's ruling B9).
  * compliance_flag = status bit 3 or 16 of every :READ?; trip_query =
    :SENS:CURR:PROT:TRIP? sent right after the point; status_bits raw.
  * Over-voltage protection (status bit 4) stops the run: reason=ovp_trip.
  * Bus errors (protocol 2.8): the failed point is retried after 1, 2, 5,
    10, 30, 60 s and then every 60 s, forever, with '# RETRY' and
    '# RECONNECT' lines in the data file; Stop during a gap ends the run
    with reason=stop_button_during_retry.
  * Shutdown on every exit path: ramp to 0 V in steps no larger than the
    sweep step, output off, '# END' line, session closed.
  * Measurement only: no V set / V reset estimation here (analysis GUI).
"""

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
import threading
import queue
import csv
import os
import sys
import math
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

# --- winsound for run-end alerts (stdlib, Windows only; optional) ---
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# --- Pillow for the institute logo (optional; a placeholder is drawn without it) ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- PyVISA for the instrument (raw SCPI, no pymeasure: protocol Section 8) ---
try:
    import pyvisa
except ImportError:
    pyvisa = None

from multiprocessing import Process
import runpy


# ===============================================================================
# COMMON BLOCK  (protocol Section 2; copied verbatim into the three memristor
# measurement modules so that each file stays self-contained, as PICA requires)
# ===============================================================================

PROTOCOL_VERSION = "1.1"

# GPIB scanner address book (pica/utils/GPIB_Instrument_Scanner_GUI.py):
# Keithley 2400 at address 24 on GPIB1, scan of 29 Aug 2026.
DEFAULT_GPIB_ADDRESS = "GPIB1::24::INSTR"
ALT_GPIB_ADDRESS = "GPIB1::4::INSTR"

# Command pacing and settling (protocol 2.2, 2.3; all go into the header).
PACING_S = 0.05            # after every configuration write
RST_SLEEP_S = 1.0          # after *RST / *CLS
SETTLE_S = 2.0             # output on at 0 V before the settle readings
N_SETTLE_READINGS = 3      # open-circuit offset record, phase "settle"
RAMP_STEP_S = 0.02         # per step of the shutdown ramp
LOG_INTERVAL_S = 1.0       # recovery / soak logging interval
RECOVERY_BETWEEN_LEGS_S = 1.0   # endurance: 0 V between a leg and its read
CMD_OVERHEAD_S = 0.010     # per :READ? (spec sheet 7 to 10 ms); time estimate only
TRIP_QUERY_OVERHEAD_S = 0.010   # the :SENS:CURR:PROT:TRIP? round trip
AUTORANGE_EXTRA_PER_10_POINTS = 1.0   # one extra dwell per 10 points (estimate only)
NPLC_DEFAULT = 1.0         # fixed, not on the window (protocol 2.5)
PLC_S = 0.020              # one power-line cycle at 50 Hz
PLOT_MIN_INTERVAL_S = 0.2  # redraw at most 5 times per second
VISA_TIMEOUT_MIN_S = 10.0

# Bus-error retry (protocol 2.8): 1, 2, 5, 10, 30, 60 s then every 60 s forever.
RETRY_SCHEDULE_S = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

# Keithley 2400 facts (manual 2400S-900-01 Rev. K).
K2400_OVERFLOW = 9.9e37             # p. 7-6: 9.91E+37 returned for overflow
STAT_COMPLIANCE = 1 << 3            # p. 18-50: bit 3 real compliance (decimal 8)
STAT_OVP = 1 << 4                   # bit 4 over-voltage protection (decimal 16)
STAT_RANGE_COMPLIANCE = 1 << 16     # bit 16 range compliance (decimal 65536)
PROTECTION_CHOICES = ("20", "40", "60", "80", "100", "120", "160", "210")  # p. 3-12; 210 = no clamp
PROTECTION_NONE_VALUE = 210.0       # p. 18-79: 161 to 210 or DEFault means NONE (shown as "210")
PROTECTION_WARN_FRACTION = 0.95     # p. 3-12: "absolute values with 5% tolerance"
SOURCE_RANGES_V = (2.0, 20.0, 200.0)    # lowest range at or above the largest |V|
SOURCE_RANGE_HEADROOM = 1.05        # each source range spans 105 % of nominal
V_SOURCE_MAX_V = 210.0
I_CC_MIN_A = 1e-9                   # p. 6-2: compliance 1 nA to 1.05 A
I_CC_MAX_A = 1.05
COMPLIANCE_READBACK_TOLERANCE = 0.05    # protocol 2.2 step 4 (A6) said 1 %; widened to 5 % for read-back rounding
CONSOLE_MAX_LINES = 3000                # the console keeps only the newest lines (every point is logged)
MEASURE_RANGES_A = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0)

# Shock warning for the ZERO + Auto-Off pulse trick, manual p. 13-10, verbatim.
LO_EARTH_WARNING = (
    "Hazardous voltages (30V rms) can appear on the selected INPUT/OUTPUT LO "
    "terminal when generating quick, pulsed waveforms using the ZERO, AUTO-OFF "
    "output state. To eliminate this shock hazard, connect the LO terminal to "
    "earth ground.")

# Muted plot colours (no pure red, no yellow).
SEGMENT_COLOURS = ("#4C72B0", "#55A868", "#8172B2", "#937860")
COLOUR_LRS = "#4C72B0"
COLOUR_HRS = "#C44E52"
COLOUR_CONTROL = "#8C8C8C"
COLOUR_VSET = "#4C72B0"
COLOUR_VMEAS = "#DD8452"


class BusError(Exception):
    """Any failure while talking to the instrument (PyVISA error, garbage
    reply, timeout). The worker retries these forever (protocol 2.8)."""


class OvpTrip(Exception):
    """Status bit 4 was set: the instrument clamped below the programmed
    voltage. The whole run stops with reason=ovp_trip (protocol 3.4 item 6)."""


class StopRequested(Exception):
    """Raised inside the worker when Stop is pressed during a retry gap."""


class ConfigMismatch(Exception):
    """The instrument did not accept a configuration value (A5, A6)."""


def ascii_text(text):
    """Header-, console- and file-safe text: arrows, micro, ohm and minus
    signs replaced; everything else outside ASCII becomes '?'. The lab PCs
    run cp1252 and a single non-ASCII character can break file creation."""
    s = str(text)
    for src, dst in (("\u2192", "->"), ("\u00b5", "u"), ("\u03bc", "u"),
                     ("\u03a9", "Ohm"), ("\u2126", "Ohm"), ("\u2212", "-"),
                     ("\u00b1", "+/-"), ("\u00b0", "deg"), ("\u2014", "-"),
                     ("\u2013", "-")):
        s = s.replace(src, dst)
    return s.encode("ascii", "replace").decode("ascii")


def header_line(text):
    """One header line: ASCII, single line; ' | ' between fields is kept."""
    s = ascii_text("" if text is None else text)
    return " ".join(s.replace("\r", " ").replace("\n", " ").split())


def header_value(text):
    """One header value: ASCII, one line, no '|' (the pair separator)."""
    return header_line(text).replace("|", "/")


def fmt_num(value, digits=8):
    """Numbers for the data file. None or NaN is written as an EMPTY field,
    never as 0 (hard rule: never fabricate a value)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return header_value(value)
    if math.isnan(v) or math.isinf(v):
        return ""
    return f"{v:.{digits}e}"


def fmt_cell(value):
    """Any cell: numbers through fmt_num, strings cleaned, None empty."""
    if value is None:
        return ""
    if isinstance(value, str):
        return header_value(value)
    return fmt_num(value)


def source_range_for(vmax_abs):
    """Lowest V-source range (2, 20, 200 V) whose 105 % span holds vmax."""
    v = abs(float(vmax_abs))
    for rng in SOURCE_RANGES_V:
        if v <= rng * SOURCE_RANGE_HEADROOM:
            return rng
    raise ValueError(f"{v:g} V is above the 2400's 210 V source limit.")


def measure_range_for(i_cc):
    """Lowest fixed current measurement range that holds I cc (A6 sequence)."""
    i = abs(float(i_cc))
    for rng in MEASURE_RANGES_A:
        if i <= rng * 1.05:
            return rng
    return MEASURE_RANGES_A[-1]


def parse_protection(choice):
    """'20' ... '210' -> float volts. '210' is the instrument's "no clamp"
    setting (manual p. 18-79); the legacy word 'NONE' is accepted as 210."""
    s = str(choice).strip().upper()
    if s == "NONE":
        return PROTECTION_NONE_VALUE
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"Never-exceed voltage '{choice}' is not one of "
                         f"{', '.join(PROTECTION_CHOICES)}.")
    if f"{int(v)}" not in PROTECTION_CHOICES:
        raise ValueError(f"Never-exceed voltage must be one of "
                         f"{', '.join(PROTECTION_CHOICES)} (manual p. 3-12).")
    return v


def protection_command_value(protection_v):
    """Value for :SOUR:VOLT:PROT; NONE is sent as 210 (manual p. 18-79)."""
    return PROTECTION_NONE_VALUE if protection_v is None else float(protection_v)


def check_vmax_against_protection(vmax_abs, protection_v):
    """Returns (ok, message). ok False -> refuse Start. ok True with a
    message -> yellow warning (above 95 % of the protection value)."""
    v = abs(float(vmax_abs))
    if v > V_SOURCE_MAX_V:
        return False, f"{v:g} V is above the instrument's 210 V limit."
    if protection_v is None:
        return True, ""
    if v > protection_v:
        return False, (f"V max {v:g} V is above the never-exceed value "
                       f"{protection_v:g} V. Lower V max or raise the "
                       f"never-exceed value.")
    if v > PROTECTION_WARN_FRACTION * protection_v:
        return True, (f"V max {v:g} V is within 5 % of the {protection_v:g} V "
                      f"protection; the instrument may clamp from "
                      f"{PROTECTION_WARN_FRACTION * protection_v:g} V.")
    return True, ""


def validate_i_cc(i_cc_a, label="I cc"):
    i = float(i_cc_a)
    if not (I_CC_MIN_A <= i <= I_CC_MAX_A):
        raise ValueError(f"{label} must be between 1 nA and 1.05 A "
                         f"(manual p. 6-2); got {i:g} A.")
    return i


def half_leg(vmax, step):
    """0, step, 2 step ... up to and INCLUDING vmax, exactly. The sign of
    vmax sets the direction, step is a magnitude. When vmax is not a
    multiple of step the final partial step lands on vmax itself."""
    step = abs(float(step))
    target = float(vmax)
    if step <= 0:
        raise ValueError("Step must be greater than zero.")
    if target == 0:
        raise ValueError("V max must not be zero.")
    sign = 1.0 if target > 0 else -1.0
    magnitude = abs(target)
    n_full = int(math.floor(magnitude / step + 1e-9))
    points = [sign * k * step for k in range(n_full + 1)]
    if abs(points[-1]) < magnitude * (1 - 1e-9):
        points.append(target)
    else:
        points[-1] = target
    return points


def return_leg(top_v, step):
    """From top_v back to 0 in steps of |step|, EXCLUDING top_v and
    INCLUDING 0 (the turn voltage is measured once, on the outgoing leg)."""
    step = abs(float(step))
    top = float(top_v)
    if step <= 0:
        raise ValueError("Step must be greater than zero.")
    if top == 0:
        return [0.0]
    sign = 1.0 if top > 0 else -1.0
    magnitude = abs(top)
    points = []
    k = 1
    while magnitude - k * step > 1e-9 * max(1.0, magnitude):
        points.append(sign * (magnitude - k * step))
        k += 1
    points.append(0.0)
    return points


def point_time_s(dwell_s, nplc=NPLC_DEFAULT):
    """Time for one sourced point: dwell + integration + command overhead
    (+ the TRIP? round trip). Estimate only."""
    return float(dwell_s) + nplc * PLC_S + CMD_OVERHEAD_S + TRIP_QUERY_OVERHEAD_S


def sweep_time_estimate_s(n_points, dwell_s, nplc=NPLC_DEFAULT):
    """n points plus one extra dwell per 10 points for autorange repeats."""
    n = int(n_points)
    return n * point_time_s(dwell_s, nplc) + (n // 10) * float(dwell_s) * AUTORANGE_EXTRA_PER_10_POINTS


def format_duration(seconds):
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def decode_status(stat):
    """(compliance_flag, ovp_flag) from the 2400 status word. compliance
    = bit 3 or bit 16 (K's ruling B1)."""
    try:
        bits = int(float(stat))
    except (TypeError, ValueError):
        return 0, 0
    compliance = 1 if (bits & STAT_COMPLIANCE) or (bits & STAT_RANGE_COMPLIANCE) else 0
    ovp = 1 if bits & STAT_OVP else 0
    return compliance, ovp


def parse_read_reply(reply):
    """':READ?' with :FORM:ELEM VOLT,CURR,TIME,STAT -> (v, i, t_instr, stat).
    An overflow element (>= 9.9e37) becomes NaN. Raises BusError on a
    reply that is not four numbers."""
    try:
        parts = [float(p) for p in str(reply).strip().split(",")]
    except ValueError:
        raise BusError(f"unparseable :READ? reply {reply!r}")
    if len(parts) < 4:
        raise BusError(f":READ? returned {len(parts)} elements, expected 4: {reply!r}")
    v, i, t_instr, stat = parts[:4]
    if abs(v) >= K2400_OVERFLOW:
        v = float("nan")
    if abs(i) >= K2400_OVERFLOW:
        i = float("nan")
    return v, i, t_instr, int(stat)


POINT_LOG_KEYS = (("cycle", "C"), ("loop", "L"), ("leg", "leg"), ("segment", "S"), ("phase", "ph"),
                  ("t_s", "t"), ("t_since_write_s", "tsw"), ("V_set_V", "Vset"), ("V_meas_V", "Vmeas"),
                  ("I_A", "I"), ("R_ohm", "R"), ("n_avg", "n"), ("compliance_flag", "cc"), ("event", "ev"))


def point_line(tag, row):
    """Compact one-line console text for a measured point (row dict of any
    module). Empty and None fields are skipped; floats get 4 significant digits."""
    parts = [str(tag)]
    for key, short in POINT_LOG_KEYS:
        if key == "loop" and "cycle" in row:
            continue        # endurance raw rows carry loop = cycle
        if key not in row:
            continue
        val = row[key]
        if val is None or val == "":
            continue
        if isinstance(val, float):
            val = "nan" if math.isnan(val) else f"{val:.4g}"
        parts.append(f"{short}={val}")
    return " ".join(parts)


EVENT_CODES = {"": 0, "trip_first": 1, "trip_unconfirmed": 2, "trip_confirmed": 3,
               "trip_confirmed;turnaround": 4, "ovp": 5}
EVENT_CODE_LEGEND = "0=none 1=trip_first 2=trip_unconfirmed 3=trip_confirmed 4=trip_confirmed+turnaround 5=ovp 9=other"


def event_code(event):
    """Integer for the event_code column: data rows stay all-numeric (no free
    text, no ';') so Origin and the like import them without surprises."""
    return EVENT_CODES.get(event or "", 9)


def resistance_ohm(v_meas, i_a):
    """V / I, or None when I is exactly zero or either value is missing."""
    if v_meas is None or i_a is None:
        return None
    if isinstance(v_meas, float) and math.isnan(v_meas):
        return None
    if isinstance(i_a, float) and math.isnan(i_a):
        return None
    if i_a == 0:
        return None
    return float(v_meas) / float(i_a)


def timestamp_for_name(now=None):
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def run_file_path(folder, stem, kind, ts):
    """<folder>/<sample>_<YYYYMMDD_HHMMSS>_<kind>.csv, the PICA naming
    (sample, time stamp, measurement kind), so every run gets its own file."""
    return unique_path(os.path.join(folder, f"{stem}_{ts}_{kind}.csv"))


def unique_path(path):
    """Never overwrite: if the name is taken (a rerun within the same second)
    append _1, _2, ... before the extension."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{base}_{n}{ext}"):
        n += 1
    return f"{base}_{n}{ext}"


def iso_now(now=None):
    return (now or datetime.now()).astimezone().isoformat(timespec="seconds")


def safe_stem(text):
    """File-name-safe sample name."""
    s = ascii_text(text).strip()
    keep = []
    for ch in s:
        keep.append(ch if (ch.isalnum() or ch in "-_.") else "_")
    out = "".join(keep).strip("_")
    return out or "sample"


def wait_with_stop(seconds, stop_event, slice_s=0.05, sleep=time.sleep):
    """Sleep in short slices; returns True if stop was requested."""
    end = time.perf_counter() + max(0.0, float(seconds))
    while True:
        if stop_event is not None and stop_event.is_set():
            return True
        remaining = end - time.perf_counter()
        if remaining <= 0:
            return False
        sleep(min(slice_s, remaining))


class TripTracker:
    """Compliance handling on an outgoing segment (protocol 3.4, B3).

    observe(flag) is called with the compliance flag of every point of the
    segment, in order. It returns (event, turnaround):
      event       '' | 'trip_first' | 'trip_unconfirmed' | 'trip_confirmed'
                  | 'trip_confirmed;turnaround'
      turnaround  True exactly once: the segment must turn around AFTER the
                  point just observed.
    After a confirmed trip or a false alarm the tracker is quiet; a false
    alarm re-arms it so a later trip can be confirmed.
    """

    def __init__(self, n_confirm):
        self.n_confirm = max(0, int(n_confirm))
        self.pending = None      # number of confirmation points still to see
        self.confirmed = False
        self.trip_index = None   # index of the first flagged point
        self.n_seen = 0

    def observe(self, flag):
        idx = self.n_seen
        self.n_seen += 1
        flag = 1 if flag else 0
        if self.confirmed:
            return "", False
        if self.pending is None:
            if flag:
                self.trip_index = idx
                if self.n_confirm == 0:
                    self.confirmed = True
                    return "trip_confirmed;turnaround", True
                self.pending = self.n_confirm
                return "trip_first", False
            return "", False
        # inside the confirmation window
        if not flag:
            self.pending = None
            self.trip_index = None
            return "trip_unconfirmed", False
        self.pending -= 1
        if self.pending == 0:
            self.confirmed = True
            return "trip_confirmed;turnaround", True
        return "", False

    def close_segment(self):
        """The segment ended (reached V max) while still confirming. Returns
        the event to append to the LAST point: 'trip_confirmed' when every
        confirmation point so far was flagged, else ''."""
        if self.pending is not None and self.pending < self.n_confirm:
            self.confirmed = True
            return "trip_confirmed"
        return ""


class Reading:
    """One :READ? result plus the trip query (protocol 2.4)."""
    __slots__ = ("v", "i", "t_instr", "stat", "compliance_flag", "ovp_flag",
                 "trip_query")

    def __init__(self, v, i, t_instr, stat, trip_query=None):
        self.v = v
        self.i = i
        self.t_instr = t_instr
        self.stat = stat
        self.compliance_flag, self.ovp_flag = decode_status(stat)
        self.trip_query = trip_query

    @property
    def r_ohm(self):
        return resistance_ohm(self.v, self.i)


class K2400Link:
    """Raw SCPI over PyVISA (protocol Section 8: no pymeasure; :READ? is
    the only read command). Every failure is re-raised as BusError so the
    worker can apply the retry rule. The last configuration is remembered
    so reconnect() can re-assert it (protocol 2.8)."""

    def __init__(self, address, timeout_s=VISA_TIMEOUT_MIN_S, sleep=time.sleep):
        self.address = address
        self.timeout_s = float(timeout_s)
        self.sleep = sleep
        self.rm = None
        self.inst = None
        self.idn = ""
        self.config = None          # dict given to configure()
        self.i_cc = None
        self.i_cc_readback = None
        self.source_delay_s = None
        self.nplc = None
        self.smode = "NORM"
        self.clear_auto = False
        self.last_source_v = 0.0

    # -- transport ------------------------------------------------------
    def open(self):
        if pyvisa is None:
            raise BusError("PyVISA is not installed.")
        try:
            if self.rm is None:
                self.rm = pyvisa.ResourceManager()
            self.inst = self.rm.open_resource(self.address)
            self.inst.timeout = int(self.timeout_s * 1000)
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

    def write(self, cmd, pace=True):
        if self.inst is None:
            raise BusError("not connected")
        try:
            self.inst.write(cmd)
        except Exception as exc:
            raise BusError(f"write {cmd!r}: {exc}")
        if pace:
            self.sleep(PACING_S)

    def query(self, cmd):
        if self.inst is None:
            raise BusError("not connected")
        try:
            return str(self.inst.query(cmd)).strip()
        except Exception as exc:
            raise BusError(f"query {cmd!r}: {exc}")

    # -- startup (protocol 2.2) -------------------------------------------
    def reset_and_identify(self):
        self.write("*RST", pace=False)
        self.write("*CLS", pace=False)
        self.sleep(RST_SLEEP_S)
        self.idn = self.query("*IDN?")
        if "2400" not in self.idn and "KEITHLEY" not in self.idn.upper():
            raise BusError(f"unexpected *IDN? reply {self.idn!r}")
        return self.idn

    def configure(self, source_range_v, protection_v, i_cc, dwell_s,
                  nplc=NPLC_DEFAULT):
        """Steps 2 to 5 of the startup sequence. Remembered for reconnect."""
        self.config = dict(source_range_v=float(source_range_v),
                           protection_v=protection_v, i_cc=float(i_cc),
                           dwell_s=float(dwell_s), nplc=float(nplc))
        # step 2
        self.write(":ROUT:TERM FRON")
        self.write(":SYST:RSEN OFF")
        self.write(":SOUR:FUNC VOLT")
        self.write(":SOUR:VOLT:MODE FIXED")
        self.write(":SENS:FUNC:CONC ON")
        self.write(':SENS:FUNC "VOLT","CURR"')
        self.write(":FORM:ELEM VOLT,CURR,TIME,STAT")
        # step 3
        self.write(f":SOUR:VOLT:RANG {float(source_range_v):g}")
        self.write(f":SOUR:VOLT:PROT {protection_command_value(protection_v):g}")
        # step 4
        self.set_compliance(i_cc)
        self.set_nplc(nplc)
        self.set_source_delay(dwell_s)
        # step 5
        self.set_output_off_mode("NORM")
        self.set_clear_auto(False)

    def set_compliance(self, i_cc):
        """A6 sequence: fixed range that holds I cc, then PROT, then AUTO ON,
        then read back PROT? and refuse a mismatch above 5 %."""
        i_cc = float(i_cc)
        self.write(f":SENS:CURR:RANG {measure_range_for(i_cc):g}")
        self.write(f":SENS:CURR:PROT {i_cc:g}")
        self.write(":SENS:CURR:RANG:AUTO ON")
        reply = self.query(":SENS:CURR:PROT?")
        try:
            got = float(reply)
        except ValueError:
            raise BusError(f"unparseable :SENS:CURR:PROT? reply {reply!r}")
        if abs(got - i_cc) > COMPLIANCE_READBACK_TOLERANCE * abs(i_cc):
            raise ConfigMismatch(
                f"compliance read-back {got:g} A differs from the requested "
                f"{i_cc:g} A by more than {COMPLIANCE_READBACK_TOLERANCE:.0%}")
        self.i_cc = i_cc
        self.i_cc_readback = got
        self.sleep(PACING_S)

    def set_source_delay(self, seconds):
        self.write(f":SOUR:DEL {float(seconds):g}")
        self.source_delay_s = float(seconds)

    def set_nplc(self, nplc):
        self.write(f":SENS:CURR:NPLC {float(nplc):g}")
        self.nplc = float(nplc)

    def set_output_off_mode(self, mode):
        self.write(f":OUTP:SMOD {mode}")
        self.smode = mode

    def set_clear_auto(self, enabled):
        self.write(f":SOUR:CLE:AUTO {'ON' if enabled else 'OFF'}")
        self.clear_auto = bool(enabled)

    def source(self, volts):
        self.write(f":SOUR:VOLT {float(volts):.6g}", pace=False)
        self.last_source_v = float(volts)

    def output(self, on):
        self.write(f":OUTP {'ON' if on else 'OFF'}")

    def output_is_on(self):
        reply = self.query(":OUTP?")
        return reply.strip().startswith("1")

    def read_point(self, query_trip=True):
        """One :READ? (and the TRIP? query unless query_trip is False, which
        pulse mode uses because the output is already off)."""
        v, i, t_instr, stat = parse_read_reply(self.query(":READ?"))
        trip = None
        if query_trip:
            reply = self.query(":SENS:CURR:PROT:TRIP?")
            trip = 1 if reply.startswith("1") else 0
        return Reading(v, i, t_instr, stat, trip)

    def ramp_to_zero(self, step_v):
        """Shutdown ramp (protocol 2.3 step 1): from the present source value
        to 0 V in steps no larger than step_v, RAMP_STEP_S per step."""
        start = float(self.last_source_v)
        step = abs(float(step_v)) if step_v else 0.1
        if start != 0.0:
            for v in return_leg(start, step):
                self.source(v)
                self.sleep(RAMP_STEP_S)
        self.source(0.0)

    def reconnect(self):
        """Protocol 2.8: *IDN? must answer, then steps 2 to 5 are re-asserted
        and the output is turned on at the remembered source value."""
        self.close()
        self.open()
        idn = self.query("*IDN?")
        if "2400" not in idn and "KEITHLEY" not in idn.upper():
            raise BusError(f"unexpected *IDN? reply after reconnect {idn!r}")
        self.idn = idn
        if self.config:
            cfg = dict(self.config)
            i_cc = self.i_cc if self.i_cc is not None else cfg["i_cc"]
            delay = self.source_delay_s if self.source_delay_s is not None else cfg["dwell_s"]
            nplc = self.nplc if self.nplc is not None else cfg["nplc"]
            smode, clear_auto = self.smode, self.clear_auto
            self.configure(cfg["source_range_v"], cfg["protection_v"], i_cc,
                           delay, nplc)
            if smode != "NORM":
                self.set_output_off_mode(smode)
            if clear_auto:
                self.set_clear_auto(True)
        if not self.output_is_on() and not self.clear_auto:
            self.source(self.last_source_v)
            self.output(True)


class RunFile:
    """One output file: '#' header blocks, a column row, one data row per
    point flushed and fsynced immediately, '#' comment lines where events
    happen, and the trailing '# END' line (protocol 2.6). Comma separated."""

    def __init__(self, path, module_name, blocks, columns, version="1.0"):
        self.path = path
        self.columns = list(columns)
        self.points = 0
        self.closed = False
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh, delimiter=",", lineterminator="\n")
        self._fh.write(f"# PICA memristor {module_name} v{version} | protocol v{PROTOCOL_VERSION}\n")
        for name, pairs in blocks:
            body = " | ".join(f"{k}={header_value(v)}" for k, v in pairs)
            self._fh.write(f"# [{name}] {body}\n")
        self._fh.write("# columns: " + ",".join(self.columns) + "\n")
        self._writer.writerow(self.columns)
        self._flush()

    def _flush(self):
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except Exception:
            pass

    def write_row(self, values):
        if self.closed:
            return
        self._writer.writerow([fmt_cell(v) for v in values])
        self.points += 1
        self._flush()

    def comment(self, text):
        if self.closed:
            return
        self._fh.write("# " + header_line(text) + "\n")
        self._flush()

    def end(self, reason, extra=None):
        if self.closed:
            return
        line = f"# END reason={header_value(reason)} | points={self.points} | end_time={iso_now()}"
        if extra:
            line += " | " + " | ".join(f"{k}={header_value(v)}" for k, v in extra)
        self._fh.write(line + "\n")
        self._flush()
        self.close()

    def close(self):
        if not self.closed:
            try:
                self._fh.close()
            finally:
                self.closed = True


class WorkerBase:
    """Owns the instrument from connect to shutdown on every exit path.
    Subclasses implement protocol() and shutdown_step_v(). Messages to the
    GUI go through a queue: ('status', dict), ('row', tag, dict),
    ('log', text), ('done', outcome, reason)."""

    def __init__(self, params, link, stop_event, out_queue, sleep=time.sleep):
        self.p = params
        self.link = link
        self.stop_event = stop_event
        self.q = out_queue
        self.sleep = sleep
        self.t0 = None
        self.files = []          # RunFile objects that receive RETRY lines
        self.reason = "completed"
        self.outcome = "finished"
        self.last_compliance_flag = 0
        self.state = "idle"
        self.retry_total = 0     # RETRY lines written so far (endurance marks retried=1)

    # -- plumbing ---------------------------------------------------------
    def post(self, kind, *payload):
        try:
            self.q.put((kind,) + payload)
        except Exception:
            pass

    def log(self, text):
        self.post("log", ascii_text(text))

    def status(self, **kw):
        if "state" in kw:
            self.state = kw["state"]
        self.post("status", kw)

    def t_s(self):
        return 0.0 if self.t0 is None else time.perf_counter() - self.t0

    def check_stop(self):
        return self.stop_event.is_set()

    def comment_all(self, text):
        for f in self.files:
            f.comment(text)

    # -- bus-error retry (protocol 2.8) ----------------------------------
    def with_retry(self, fn, what="command"):
        """Run fn(); on BusError retry after 1, 2, 5, 10, 30, 60 s and then
        every 60 s until it succeeds or Stop is pressed. Each attempt is a
        '# RETRY' line in every open data file; a recovered gap is exactly one
        '# RECONNECT' line, written when fn() succeeds. Returns fn()'s result."""
        attempt = 0
        gap_start = None
        while True:
            try:
                result = fn()
                if attempt:
                    self.comment_all(f"RECONNECT after {time.perf_counter() - gap_start:.1f} s | {what} succeeded on attempt {attempt + 1}")
                    self.status(state="running")
                return result
            except BusError as exc:
                attempt += 1
                self.retry_total += 1
                if gap_start is None:
                    gap_start = time.perf_counter()
                delay = RETRY_SCHEDULE_S[min(attempt - 1, len(RETRY_SCHEDULE_S) - 1)]
                self.comment_all(f"RETRY n={attempt} | t_s={self.t_s():.3f} | what={what} | error={header_value(exc)}")
                self.log(f"Bus error ({what}): {exc}. Retry {attempt} in {delay:g} s.")
                self.status(state=f"reconnecting (attempt {attempt}, next try in {delay:g} s)")
                if wait_with_stop(delay, self.stop_event, sleep=self.sleep):
                    raise StopRequested()
                try:
                    self.link.reconnect()   # the RECONNECT line is written once fn() succeeds
                except BusError as exc2:
                    self.comment_all(f"RETRY n={attempt} | t_s={self.t_s():.3f} | what=reconnect | error={header_value(exc2)}")
                    continue

    # -- startup (protocol 2.2) -------------------------------------------
    def startup(self, source_range_v, protection_v, i_cc, dwell_s):
        self.status(state="configuring")
        self.link.open()
        idn = self.link.reset_and_identify()
        self.link.configure(source_range_v, protection_v, i_cc, dwell_s, NPLC_DEFAULT)
        self.log_compliance()
        self.link.source(0.0)
        self.link.output(True)
        return idn

    def log_compliance(self):
        """One console line per compliance change: requested vs read back."""
        got = self.link.i_cc_readback
        self.log(f"Compliance set: requested {self.link.i_cc:g} A, instrument reads back "
                 f"{'?' if got is None else f'{got:g}'} A.")

    def settle_readings(self, write_fn):
        """Step 7: 2 s at 0 V, then N readings written as phase 'settle'.
        A +9.91E+37 voltage element means volt sensing is not enabled (A5)."""
        self.status(state="settling")
        if wait_with_stop(SETTLE_S, self.stop_event, sleep=self.sleep):
            return
        for k in range(N_SETTLE_READINGS):
            rd = self.with_retry(lambda: self.link.read_point(True), "settle read")
            if k == 0 and (rd.v is None or (isinstance(rd.v, float) and math.isnan(rd.v))):
                raise ConfigMismatch("volt sense not enabled")
            write_fn(rd)

    # -- shutdown (protocol 2.3) -------------------------------------------
    def shutdown(self, ramp_step_v):
        """Ramp to 0 V, output off, close the link. Never raises. Returns a
        text describing anything that went wrong (empty when clean)."""
        problems = []
        self.status(state="ramping down")
        try:
            if self.link.clear_auto:
                self.link.set_clear_auto(False)
            if self.link.smode != "NORM":
                self.link.set_output_off_mode("NORM")
        except Exception as exc:
            problems.append(f"restore output mode: {exc}")
        try:
            self.link.ramp_to_zero(ramp_step_v)
        except Exception as exc:
            problems.append(f"ramp: {exc}")
        try:
            self.link.output(False)
        except Exception as exc:
            problems.append(f"output off: {exc}")
            # one more attempt with a short timeout (D4)
            try:
                if self.link.inst is not None:
                    self.link.inst.timeout = 2000
                self.link.write(":OUTP OFF", pace=False)
            except Exception as exc2:
                problems.append(f"output off retry: {exc2}")
        try:
            self.link.close()
        except Exception:
            pass
        return "; ".join(problems)

    # -- run skeleton -----------------------------------------------------
    def run(self):
        self.t0 = time.perf_counter()
        shutdown_note = ""
        try:
            self.protocol()
            if self.check_stop() and self.reason == "completed":
                self.reason = "stop_button"
                self.outcome = "stopped"
        except StopRequested:
            self.reason = "stop_button_during_retry"
            self.outcome = "stopped"
        except OvpTrip as exc:
            self.reason = "ovp_trip"
            self.outcome = "error"
            self.log(f"Over-voltage protection tripped: {exc}")
        except ConfigMismatch as exc:
            self.reason = f"exception: {exc}"
            self.outcome = "error"
            self.log(f"Configuration refused: {exc}")
        except Exception as exc:
            self.reason = f"exception: {type(exc).__name__}: {exc}"
            self.outcome = "error"
            self.log("ERROR: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
        finally:
            shutdown_note = self.shutdown(self.shutdown_step_v())
            extra = [("shutdown", shutdown_note)] if shutdown_note else None
            for f in self.files:
                try:
                    f.end(self.reason, extra)
                except Exception:
                    pass
            self.status(state="done" if self.outcome == "finished" else self.outcome)
            self.post("done", self.outcome, self.reason)

    def protocol(self):
        raise NotImplementedError

    def shutdown_step_v(self):
        return 0.1


def run_script_process(script_path, argv=None):
    """Target of the 'Open analysis' process: run a script in its own
    directory with the given argv."""
    try:
        os.chdir(os.path.dirname(script_path))
        sys.argv = [script_path] + list(argv or [])
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def analysis_script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "utils", "Memristor_Analysis_GUI.py"))


def gpib_scanner_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py"))


def plotter_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "utils", "PlotterUtil_GUI.py"))


class MemristorGuiBase:
    """PICA standard layout (as IV_K6517B_GUI.py / Delta_RT_*_GUI.py): logo,
    inputs, run buttons and console on the left, Live Graphs on the right. Subclasses provide the module inputs,
    the figure, parameter collection, the worker and the plot update.

    Nothing modal is opened during or after a run (PICA unattended rule):
    completion and errors go to the console, the title banner and a beep.
    Dialogs are used only for pre-run validation and the close question."""

    MODULE_TITLE = "Memristor"
    MODULE_KEY = "sweep"
    PROGRAM_VERSION = "1.1"
    # Palette, fonts and layout identical to the other PICA modules
    # (IV_K6517B_GUI.py / Delta_RT_*_GUI.py).
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#B8A392', '#E5DCD3', '#2C2825'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_GOLD = '#B68B6E', '#EF233C', '#BA6B5E'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    CLR_WARN = '#C9A227'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_HINT = ('Segoe UI', FONT_SIZE_BASE - 2, 'italic')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Consolas', FONT_SIZE_BASE - 1)
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE = os.path.join(SCRIPT_DIR, "..", "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE = "../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    def __init__(self, root):
        self.root = root
        self.root.title(f"Keithley 2400 Memristor {self.MODULE_TITLE}")
        try:
            # Fit the screen (a 1366x768 lab laptop must still show everything).
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{min(1550, sw - 20)}x{min(900, sh - 90)}+0+0")
            self.root.minsize(1100, 650)
            self.root.configure(bg=self.CLR_BG_DARK)
        except Exception:
            pass
        self.is_running = False
        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()
        self.worker_thread = None
        self._pump_after_id = None
        self._last_draw = 0.0
        self._draw_pending = False
        self.file_location_path = os.getcwd()
        self.current_files = []
        self.params = {}
        self.logo_image = None
        self.pre_init_logs = []
        self.run_t0 = None
        self.points_written = 0
        self.estimate_total_s = None
        self.progress_fraction = 0.0
        self.vars = {}
        self.setup_styles()
        self.create_widgets()
        try:
            self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        except Exception:
            pass
        try:
            self.root.after(50, self._update_start_state)
            self.root.after(60, self.refresh_estimate)
        except Exception:
            pass

    # -- styles / skeleton ---------------------------------------------
    def setup_styles(self):
        """ttk styles and Matplotlib defaults, as in the other PICA modules."""
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('Hint.TLabel', font=self.FONT_HINT)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8), foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TCheckbutton', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TCombobox', font=self.FONT_BASE)
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        """Header on top; left panel (scrollable): Information, Experiment
        Parameters, module inputs, Instrument & Run, Console Output; right
        panel: Live Graphs. Same skeleton as IV_K6517B_GUI.py."""
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_container.pack_propagate(False)
        self.main_pane.add(left_container, weight=0)

        canvas = Canvas(left_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_container, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.left_scrollable_frame = inner

        def _on_mousewheel(event):
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        right_container = tk.Frame(self.main_pane, bg='white')
        self.main_pane.add(right_container, weight=1)

        # Console and graphs first so that logging and the status strip exist
        # while the input frames are being built; the console is packed last.
        console_pane = self.create_console_frame(inner)
        self.create_graph_frame(right_container)
        self.create_info_frame(inner)
        self.create_sample_frame(inner)
        self.create_module_inputs(inner)
        self.create_run_frame(inner)
        console_pane.pack(pady=5, padx=10, fill='x')
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def create_header(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        ttk.Button(header, text="\U0001F4C8", command=self.launch_plotter, width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header, text="\U0001F4DF", command=self.launch_gpib_scanner, width=3).pack(side='right', padx=(0, 5), pady=5)
        Label(header, text=f"Keithley 2400: Memristor {self.MODULE_TITLE}", bg=self.CLR_HEADER,
              fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(header, text=f"Version: {self.PROGRAM_VERSION} | protocol v{PROTOCOL_VERSION}", bg=self.CLR_HEADER,
              fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    # -- helpers for input rows (PICA look: label chip above a full-width
    #    entry, two columns per frame; the `row` argument is kept for the
    #    subclasses but placement is automatic) -----------------------------
    def make_frame(self, parent, title):
        frame = LabelFrame(parent, text=title, relief='groove', bg=self.CLR_BG_DARK,
                           fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        frame.grid_columnconfigure(0, weight=1, uniform='half')
        frame.grid_columnconfigure(1, weight=1, uniform='half')
        frame._slot = 0
        return frame

    def _cell(self, frame, wide=False):
        """Next free cell: (label_row, column, columnspan, padx)."""
        slot = getattr(frame, "_slot", 0)
        if wide and slot % 2:
            slot += 1
        row, col = (slot // 2) * 2, slot % 2
        frame._slot = slot + (2 if wide else 1)
        if wide:
            return row, 0, 2, (10, 10)
        return row, col, 1, ((10, 5) if col == 0 else (5, 10))

    def _chip(self, frame, text, hint, row, col, colspan, padx):
        """Label chip (tk default look, as in the other modules) with an
        optional italic hint to its right."""
        holder = tk.Frame(frame, bg=self.CLR_BG_DARK)
        holder.grid(row=row, column=col, columnspan=colspan, padx=padx, pady=(5, 0), sticky='w')
        Label(holder, text=text).pack(side='left')
        if hint:
            ttk.Label(holder, text=hint, style='Hint.TLabel').pack(side='left', padx=(8, 0))
        return holder

    def add_entry(self, parent, row, label, key, default, hint=None, width=14):
        r, c, span, padx = self._cell(parent, wide=width > 14)
        self._chip(parent, label + ":", hint, r, c, span, padx)
        var = tk.StringVar(master=self.root, value=str(default))
        entry = Entry(parent, textvariable=var, font=self.FONT_BASE)
        entry.grid(row=r + 1, column=c, columnspan=span, padx=padx, pady=(0, 5), sticky='ew')
        var.trace_add("write", lambda *a: self.refresh_estimate())
        self.vars[key] = var
        return var

    def add_combo(self, parent, row, label, key, values, default, hint=None, width=20, command=None):
        r, c, span, padx = self._cell(parent)
        self._chip(parent, label + ":", hint, r, c, span, padx)
        var = tk.StringVar(master=self.root, value=str(default))
        combo = ttk.Combobox(parent, textvariable=var, values=list(values), state='readonly', font=self.FONT_BASE)
        combo.grid(row=r + 1, column=c, columnspan=span, padx=padx, pady=(0, 5), sticky='ew')
        var.trace_add("write", lambda *a: self.refresh_estimate())
        if command is not None:
            combo.bind("<<ComboboxSelected>>", lambda e: command())
        self.vars[key] = var
        return var

    def add_check(self, parent, row, label, key, default=False, hint=None, command=None):
        r, c, span, padx = self._cell(parent, wide=True)
        var = tk.BooleanVar(master=self.root, value=bool(default))
        cb = ttk.Checkbutton(parent, text=label, variable=var, command=command)
        cb.grid(row=r, column=c, columnspan=span, padx=padx, pady=(6, 2), sticky='w')
        if hint:
            ttk.Label(parent, text=hint, style='Hint.TLabel').grid(row=r + 1, column=c, columnspan=span,
                                                                   padx=(padx[0] + 24, padx[1]), pady=(0, 4), sticky='w')
        self.vars[key] = var
        return var

    def add_wide_label(self, parent, text, fg=None, bg=None):
        """Full-width wrapped text (warnings, notices)."""
        r, c, span, padx = self._cell(parent, wide=True)
        lbl = Label(parent, text=text, bg=bg or self.CLR_BG_DARK, fg=fg or self.CLR_FG_LIGHT, font=self.FONT_HINT,
                    wraplength=self.LEFT_PANEL_WIDTH - 60, justify='left', anchor='w')
        lbl.grid(row=r, column=c, columnspan=span, padx=padx, pady=4, sticky='ew')
        return lbl

    def add_wide_button(self, parent, text, command, style=None):
        r, c, span, padx = self._cell(parent, wide=True)
        btn = ttk.Button(parent, text=text, command=command, style=style) if style else \
            ttk.Button(parent, text=text, command=command)
        btn.grid(row=r, column=c, columnspan=span, padx=padx, pady=4, sticky='ew')
        return btn

    def add_file_row(self, parent, button_text, command, initial="none"):
        """Full-width button with a path label under it; returns the label."""
        self.add_wide_button(parent, button_text, command)
        r, c, span, padx = self._cell(parent, wide=True)
        lbl = Label(parent, text=initial, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_HINT,
                    wraplength=self.LEFT_PANEL_WIDTH - 60, justify='left', anchor='w')
        lbl.grid(row=r, column=c, columnspan=span, padx=padx, pady=(0, 4), sticky='ew')
        return lbl

    def get_str(self, key):
        try:
            return str(self.vars[key].get()).strip()
        except Exception:
            return ""

    def get_float(self, key, label, minimum=None, maximum=None, allow_empty=False):
        text = self.get_str(key)
        if text == "":
            if allow_empty:
                return None
            raise ValueError(f"{label} is empty.")
        try:
            v = float(text)
        except ValueError:
            raise ValueError(f"{label}: '{text}' is not a number.")
        if not math.isfinite(v):
            raise ValueError(f"{label} must be a finite number.")
        if minimum is not None and v < minimum:
            raise ValueError(f"{label} must be at least {minimum:g}.")
        if maximum is not None and v > maximum:
            raise ValueError(f"{label} must be at most {maximum:g}.")
        return v

    def get_int(self, key, label, minimum=None):
        v = self.get_float(key, label, minimum)
        if abs(v - round(v)) > 1e-9:
            raise ValueError(f"{label} must be a whole number.")
        return int(round(v))

    # -- information block (PICA convention: logo + institute + program) ----
    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK,
                           fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=15, pady=15, sticky='n')
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE):
            try:
                img = Image.open(self.LOGO_FILE)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, image=self.logo_image)
            except Exception as exc:
                self.log(f"WARNING: Could not process logo file: {exc}")
                logo_canvas.create_text(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, text="LOGO\nERROR",
                                        font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        else:
            self.log(f"WARNING: Logo file '{self.LOGO_FILE}' not found or Pillow not installed.")
            logo_canvas.create_text(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, text="LOGO\nMISSING",
                                    font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font,
                  background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(20, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font,
                  background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')
        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)
        details_text = (f"Program Name: Memristor {self.MODULE_TITLE}\n"
                        "Instrument: Keithley 2400 SourceMeter\n"
                        f"Protocol: Memristor measurement modules v{PROTOCOL_VERSION}")
        ttk.Label(frame, text=details_text, justify='left',
                  background=self.CLR_BG_DARK).grid(row=3, column=0, columnspan=2, padx=20, pady=(0, 10), sticky='w')

    # -- experiment parameters (sample name; protocol 2.1) ------------------
    def create_sample_frame(self, parent):
        frame = self.make_frame(parent, 'Experiment Parameters')
        r, c, span, padx = self._cell(frame, wide=True)
        self._chip(frame, "Sample Name:", "file name stem; add electrode / face / notes to it", r, c, span, padx)
        self.vars["sample_name"] = tk.StringVar(master=self.root, value="")
        Entry(frame, textvariable=self.vars["sample_name"], font=self.FONT_BASE).grid(
            row=r + 1, column=c, columnspan=span, padx=padx, pady=(0, 10), sticky='ew')
        self.vars["sample_name"].trace_add("write", lambda *a: self._update_start_state())

    def create_run_frame(self, parent):
        """Instrument address, protection, Scan / Browse / Analysis buttons and
        Start / Stop, laid out as in the other PICA modules."""
        frame = self.make_frame(parent, 'Instrument & Run')
        r, c, span, padx = self._cell(frame)
        self._chip(frame, "Keithley 2400 VISA:", None, r, c, span, padx)
        self.vars["visa_address"] = tk.StringVar(master=self.root, value=DEFAULT_GPIB_ADDRESS)
        self.visa_combobox = ttk.Combobox(frame, textvariable=self.vars["visa_address"],
                                          values=[DEFAULT_GPIB_ADDRESS, ALT_GPIB_ADDRESS], font=self.FONT_BASE)
        self.visa_combobox.grid(row=r + 1, column=c, columnspan=span, padx=padx, pady=(0, 5), sticky='ew')
        self.add_combo(frame, 0, "Never-exceed V", "protection", PROTECTION_CHOICES, "20", ":SOUR:VOLT:PROT")
        self.scan_button = self.add_wide_button(frame, "Scan for Instruments", self._scan_for_visa_instruments)
        self.folder_label = self.add_file_row(frame, "Browse Save Location...", self._browse_file_location,
                                              self.file_location_path)
        self.analysis_button = self.add_wide_button(frame, "Open Analysis Utility...", self.open_analysis)
        r, c, span, padx = self._cell(frame, wide=True)
        self.start_button = ttk.Button(frame, text="Start Measurement", style='Start.TButton',
                                       command=self.start_run, state='disabled')
        self.start_button.grid(row=r, column=0, padx=(10, 5), pady=(10, 4), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", style='Stop.TButton', command=self.stop_run, state='disabled')
        self.stop_button.grid(row=r, column=1, padx=(5, 10), pady=(10, 4), sticky='ew')
        self.start_hint = Label(frame, text="Start needs a sample name.", bg=self.CLR_BG_DARK,
                                fg=self.CLR_FG_LIGHT, font=self.FONT_HINT, anchor='w')
        self.start_hint.grid(row=r + 1, column=0, columnspan=2, padx=10, pady=(0, 8), sticky='ew')

    def create_module_inputs(self, parent):
        raise NotImplementedError

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK,
                           fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console = scrolledtext.ScrolledText(frame, height=9, state='disabled', bg=self.CLR_CONSOLE_BG,
                                                 fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console.pack(pady=5, padx=5, fill='both', expand=True)
        for line in self.pre_init_logs:
            self.log(line)
        self.pre_init_logs = []
        self.log("Console initialized. Configure parameters and scan for instruments.")
        if pyvisa is None:
            self.log("CRITICAL: PyVISA not found. Please run 'pip install pyvisa'.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG,
                                     fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        # Constrained layout re-flows the axes on every draw, so the gaps stay
        # tight however the window (and hence the figure) is resized.
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG, layout='constrained')
        self.build_figure(self.figure)
        # Run status strip under the graphs (state, elapsed, points, file ...),
        # packed before the canvas so the canvas cannot squeeze it out.
        status = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        status.pack(side='bottom', fill='x', padx=10, pady=(0, 6))
        self.status_labels = {}
        for key, text in (("state", "state: idle"), ("elapsed", "elapsed: --"), ("left", "left: --"),
                          ("points", "points: 0"), ("compliance", "compliance: -"), ("file", "file: -")):
            lbl = Label(status, text=text, bg=self.CLR_GRAPH_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_STATUS, anchor='w')
            lbl.pack(side='left', padx=(0, 18))
            self.status_labels[key] = lbl
        self.estimate_label = Label(graph_container, text="estimate: --", bg=self.CLR_GRAPH_BG, fg=self.CLR_FG_LIGHT,
                                    font=self.FONT_STATUS, anchor='w')
        self.estimate_label.pack(side='bottom', fill='x', padx=10)
        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_container)
        self.canvas.get_tk_widget().pack(side='top', fill='both', expand=True, padx=5, pady=5)

    def build_figure(self, fig):
        raise NotImplementedError

    # -- console / banner / beep ----------------------------------------
    def log(self, message):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {ascii_text(message)}"
        try:
            self.console.config(state='normal')
            self.console.insert('end', line + "\n")
            n_lines = int(self.console.index('end-1c').split('.')[0])
            if n_lines > CONSOLE_MAX_LINES:
                self.console.delete('1.0', f"{n_lines - CONSOLE_MAX_LINES + 1}.0")
            self.console.see('end')
            self.console.config(state='disabled')
        except Exception:
            self.pre_init_logs.append(message)
        print(line)

    def _set_banner(self, text):
        try:
            self.root.title(f"Keithley 2400 Memristor {self.MODULE_TITLE} - {ascii_text(text)}")
        except Exception:
            pass

    def _beep(self, times=1):
        if not HAS_WINSOUND:
            return
        try:
            for _ in range(times):
                winsound.Beep(1000, 200)
                time.sleep(0.1)
        except Exception:
            pass

    def set_status(self, key, text):
        try:
            self.status_labels[key].config(text=f"{key}: {text}")
        except Exception:
            pass

    # -- start gating, estimate ----------------------------------------
    def _update_start_state(self):
        try:
            ok = bool(self.get_str("sample_name"))
            if self.is_running:
                self.start_button.config(state='disabled')
            else:
                self.start_button.config(state='normal' if ok else 'disabled')
            self.start_hint.config(text="" if ok else "Start needs a sample name.")
        except Exception:
            pass

    def refresh_estimate(self):
        try:
            est = self.estimate_run_time_s()
            self.estimate_total_s = est
            self.estimate_label.config(text=f"estimate: {format_duration(est)}" if est is not None else "estimate: --")
        except Exception as exc:
            try:
                self.estimate_label.config(text=f"estimate: -- ({ascii_text(exc)})")
            except Exception:
                pass

    def estimate_run_time_s(self):
        return None

    # -- common parameter collection -------------------------------------
    def collect_common_params(self):
        p = {}
        p["sample_name"] = self.get_str("sample_name")
        if not p["sample_name"]:
            raise ValueError("Sample name is mandatory.")
        p["visa_address"] = self.get_str("visa_address") or DEFAULT_GPIB_ADDRESS
        p["protection_v"] = parse_protection(self.get_str("protection"))
        p["folder"] = self.file_location_path
        if not p["folder"] or not os.path.isdir(p["folder"]):
            raise ValueError("Choose an existing output folder first.")
        p["stem"] = safe_stem(p["sample_name"])
        return p

    def sample_block(self, p):
        return ("sample", [("name", p["sample_name"])])

    def instrument_block(self, p, idn, source_range_v, i_cc, extra=None):
        pairs = [("idn", idn), ("address", p["visa_address"]), ("source_range_V", f"{source_range_v:g}"),
                 ("meas_range", "AUTO"), ("NPLC", f"{NPLC_DEFAULT:g}"), ("I_cc_A", f"{i_cc:g}")]
        pairs += list(extra or [])
        pairs += [("V_protection_V", f"{protection_command_value(p['protection_v']):g}"),
                  ("terminals", "FRONT"), ("sense", "2-wire"), ("read_delay_s", "0"),
                  ("pacing_s", f"{PACING_S:g}"), ("rst_sleep_s", f"{RST_SLEEP_S:g}"),
                  ("settle_s", f"{SETTLE_S:g}"), ("n_settle", str(N_SETTLE_READINGS)),
                  ("ramp_step_s", f"{RAMP_STEP_S:g}"), ("compliance_flag", "STAT bit3 or bit16"),
                  ("trip_query", ":SENS:CURR:PROT:TRIP?"), ("event_code", EVENT_CODE_LEGEND)]
        return ("instrument", pairs)

    # -- run control -------------------------------------------------------
    def start_run(self):
        if self.is_running:
            return
        try:
            params = self.collect_params()
        except ValueError as exc:
            messagebox.showerror("Check the inputs", ascii_text(exc))
            return
        warning = params.get("_warning")
        if warning:
            self.log("WARNING: " + warning)
        self.params = params
        self.stop_event.clear()
        self.is_running = True
        self.points_written = 0
        self.current_files = []
        self.run_t0 = time.perf_counter()
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.reset_plot()
        self.set_status("points", "0")
        self.set_status("compliance", "-")
        self._set_banner("RUNNING")
        self.log(f"Start: {self.describe_params(params)}")
        worker = self.make_worker(params)
        self.worker_thread = threading.Thread(target=worker.run, daemon=True)
        self.worker_thread.start()
        self._pump()

    def stop_run(self):
        if self.is_running and not self.stop_event.is_set():
            self.stop_event.set()
            self.log("Stop requested: finishing the point in progress, then ramping down.")
            self.set_status("state", "stopping")

    def make_worker(self, params):
        raise NotImplementedError

    def collect_params(self):
        raise NotImplementedError

    def describe_params(self, params):
        return ", ".join(f"{k}={v}" for k, v in params.items() if not k.startswith("_"))

    def reset_plot(self):
        pass

    def on_row(self, tag, row):
        pass

    def redraw(self):
        pass

    def _pump(self):
        drew = False
        try:
            while True:
                msg = self.data_queue.get_nowait()
                kind = msg[0]
                if kind == "row":
                    _, tag, row = msg
                    self.points_written += 1
                    self.log(point_line(tag, row))
                    self.on_row(tag, row)
                    flag = row.get("compliance_flag")
                    if flag is not None:
                        self.set_status("compliance", str(flag))
                    self._draw_pending = True
                elif kind == "status":
                    for k, v in msg[1].items():
                        if k == "file":
                            self.set_status("file", os.path.basename(str(v)))
                            self.current_files.append(v)
                        elif k == "progress":
                            self.progress_fraction = float(v)
                        elif k == "left_s":
                            self.set_status("left", format_duration(v))
                        else:
                            self.set_status(k, str(v))
                elif kind == "log":
                    self.log(msg[1])
                elif kind == "done":
                    self._finish_run(msg[1], msg[2])
        except queue.Empty:
            pass
        now = time.perf_counter()
        if self._draw_pending and now - self._last_draw >= PLOT_MIN_INTERVAL_S:
            try:
                self.redraw()
                self.canvas.draw_idle()
            except Exception as exc:
                self.log(f"plot error: {exc}")
            self._last_draw = now
            self._draw_pending = False
            drew = True
        if self.is_running:
            self.set_status("points", str(self.points_written))
            if self.run_t0 is not None:
                elapsed = now - self.run_t0
                self.set_status("elapsed", format_duration(elapsed))
                if self.estimate_total_s and self.progress_fraction > 0:
                    self.set_status("left", format_duration(self.estimate_total_s * (1 - self.progress_fraction)))
            self._pump_after_id = self.root.after(100, self._pump)
        elif self._draw_pending and not drew:
            self._pump_after_id = self.root.after(100, self._pump)

    def _finish_run(self, outcome, reason):
        self.is_running = False
        self.stop_button.config(state='disabled')
        self._update_start_state()
        if outcome == "finished":
            msg = f"COMPLETE - {self.points_written} points written ({reason})."
        elif outcome == "stopped":
            msg = f"STOPPED by user after {self.points_written} points ({reason}). Output is OFF."
        else:
            msg = f"ABORTED after {self.points_written} points ({reason}). Output is OFF. See console."
        self.log(msg)
        for f in self.current_files:
            self.log(f"File: {f}")
        self.set_status("left", "--")
        self._set_banner(msg.split(' - ')[0].split('.')[0])
        self._beep(2 if outcome == "finished" else 3)
        try:
            self.redraw()
            self.canvas.draw_idle()
        except Exception:
            pass

    # -- utilities --------------------------------------------------------
    def _scan_for_visa_instruments(self):
        if pyvisa is None:
            self.log("ERROR: PyVISA not found.")
            return
        try:
            rm = pyvisa.ResourceManager()
            resources = list(rm.list_resources())
        except Exception as exc:
            self.log(f"ERROR during scan: {exc}")
            return
        if not resources:
            self.log("No VISA instruments found.")
            return
        self.log(f"Found: {resources}")
        self.visa_combobox['values'] = resources
        if DEFAULT_GPIB_ADDRESS in resources:
            self.vars["visa_address"].set(DEFAULT_GPIB_ADDRESS)
        elif ALT_GPIB_ADDRESS in resources:
            self.vars["visa_address"].set(ALT_GPIB_ADDRESS)
        else:
            self.vars["visa_address"].set(resources[0])

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            try:
                self.folder_label.config(text=path)
            except Exception:
                pass
            self.log(f"Save location set to: {path}")

    def launch_gpib_scanner(self):
        path = gpib_scanner_path()
        if not os.path.exists(path):
            self.log(f"GPIB scanner not found at {path}")
            return
        Process(target=run_script_process, args=(path, [])).start()

    def launch_plotter(self):
        path = plotter_path()
        if not os.path.exists(path):
            self.log(f"Plotter utility not found at {path}")
            return
        Process(target=run_script_process, args=(path, [])).start()

    def open_analysis(self):
        path = analysis_script_path()
        if not os.path.exists(path):
            self.log(f"Analysis GUI not found at {path}")
            return
        Process(target=run_script_process, args=(path, ["--tab", self.MODULE_KEY])).start()
        self.log("Analysis GUI opened in a separate process (choose files there with Add...).")

    def _on_closing(self):
        if self.is_running:
            if not messagebox.askyesno("Exit", "Measurement is running. Stop and exit?"):
                return
            self.stop_event.set()
            t = self.worker_thread
            if t is not None and t.is_alive():
                t.join(timeout=20)
            try:
                if self._pump_after_id is not None:
                    self.root.after_cancel(self._pump_after_id)
            except Exception:
                pass
        self.root.destroy()


# ===============================================================================
# END OF COMMON BLOCK
# ===============================================================================


# ===============================================================================
# SWEEP MODULE (protocol Section 3)
# ===============================================================================

SHAPE_BIPOLAR_PLUS = "bipolar, + first"
SHAPE_BIPOLAR_MINUS = "bipolar, - first"
SHAPE_UNIPOLAR_PLUS = "unipolar +"
SHAPE_UNIPOLAR_MINUS = "unipolar -"
SHAPE_LADDER = "ladder"
SHAPES = (SHAPE_BIPOLAR_PLUS, SHAPE_BIPOLAR_MINUS, SHAPE_UNIPOLAR_PLUS,
          SHAPE_UNIPOLAR_MINUS, SHAPE_LADDER)
SHAPE_KEYS = {SHAPE_BIPOLAR_PLUS: "bipolar_plus_first", SHAPE_BIPOLAR_MINUS: "bipolar_minus_first",
              SHAPE_UNIPOLAR_PLUS: "unipolar_plus", SHAPE_UNIPOLAR_MINUS: "unipolar_minus",
              SHAPE_LADDER: "ladder"}

SWEEP_COLUMNS = ("t_s", "loop", "segment", "vmax_V", "V_set_V", "V_meas_V", "I_A", "R_ohm",
                 "compliance_flag", "trip_query", "status_bits", "event_code", "phase")


def parse_ladder(text):
    """'5, 10, 20' -> [5.0, 10.0, 20.0]; commas, semicolons, spaces or new
    lines separate the entries; every entry must be a positive number."""
    if text is None:
        raise ValueError("Ladder list is empty.")
    tokens = str(text).replace(",", " ").replace(";", " ").split()
    if not tokens:
        raise ValueError("Ladder list is empty. Example: 5, 10, 20")
    values = []
    for tok in tokens:
        try:
            v = float(tok)
        except ValueError:
            raise ValueError(f"Ladder list: '{tok}' is not a number.")
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"Ladder list: '{tok}' must be a positive voltage.")
        values.append(v)
    return values


def build_loop(shape, vmax_plus, vmax_minus, step):
    """One loop as a list of segments (protocol 3.3, B2).

    Each segment is a dict: segment (1..4), sign (+1/-1), outgoing (bool),
    points (list of volts). The turn voltage is measured once and belongs
    to the outgoing segment; the 0 V at the half-loop boundary belongs to
    the return segment that ends there. Every loop starts and ends at 0 V.
    """
    vp = abs(float(vmax_plus))
    vm = abs(float(vmax_minus))

    def out(v):
        return half_leg(v, step)

    def ret(v):
        return return_leg(v, step)

    if shape in (SHAPE_BIPOLAR_PLUS, SHAPE_LADDER):
        return [dict(segment=1, sign=1, outgoing=True, points=out(vp)),
                dict(segment=2, sign=1, outgoing=False, points=ret(vp)),
                dict(segment=3, sign=-1, outgoing=True, points=out(-vm)[1:]),
                dict(segment=4, sign=-1, outgoing=False, points=ret(-vm))]
    if shape == SHAPE_BIPOLAR_MINUS:
        return [dict(segment=1, sign=-1, outgoing=True, points=out(-vm)),
                dict(segment=2, sign=-1, outgoing=False, points=ret(-vm)),
                dict(segment=3, sign=1, outgoing=True, points=out(vp)[1:]),
                dict(segment=4, sign=1, outgoing=False, points=ret(vp))]
    if shape == SHAPE_UNIPOLAR_PLUS:
        return [dict(segment=1, sign=1, outgoing=True, points=out(vp)),
                dict(segment=2, sign=1, outgoing=False, points=ret(vp))]
    if shape == SHAPE_UNIPOLAR_MINUS:
        return [dict(segment=1, sign=-1, outgoing=True, points=out(-vm)),
                dict(segment=2, sign=-1, outgoing=False, points=ret(-vm))]
    raise ValueError(f"Unknown shape '{shape}'.")


def loop_point_count(shape, vmax_plus, vmax_minus, step):
    return sum(len(s["points"]) for s in build_loop(shape, vmax_plus, vmax_minus, step))


def run_plan(shape, vmax_plus, vmax_minus, step, loops, ladder):
    """List of (loop_number, vmax_plus, vmax_minus) for the whole run."""
    if shape == SHAPE_LADDER:
        return [(k + 1, v, v) for k, v in enumerate(ladder)]
    return [(k + 1, vmax_plus, vmax_minus) for k in range(int(loops))]


class SweepWorker(WorkerBase):
    """Switching I-V sweep with current compliance (protocol Section 3)."""

    def shutdown_step_v(self):
        return self.p["step_v"]

    def protocol(self):
        p = self.p
        plan = run_plan(p["shape"], p["vmax_plus"], p["vmax_minus"], p["step_v"], p["loops"], p["ladder"])
        largest_v = max(max(a, b) for _, a, b in plan)
        source_range = source_range_for(largest_v)
        idn = self.startup(source_range, p["protection_v"], p["i_cc_plus"], p["dwell_s"])
        self.current_sign = 1
        self.link.i_cc = p["i_cc_plus"]

        ts = timestamp_for_name()
        path = run_file_path(p["folder"], p["stem"], "sweep", ts)
        blocks = [p["_sample_block"],
                  self.instrument_block_pairs(idn, source_range),
                  ("protocol", [("module", "sweep"), ("mode", SHAPE_KEYS[p["shape"]]),
                                ("Vmax_plus_V", f"{p['vmax_plus']:g}"), ("Vmax_minus_V", f"{p['vmax_minus']:g}"),
                                ("step_V", f"{p['step_v']:g}"), ("dwell_s", f"{p['dwell_s']:g}"),
                                ("loops", str(p["loops"])),
                                ("ladder_list_V", " ".join(f"{v:g}" for v in p["ladder"]) if p["ladder"] else ""),
                                ("n_confirm", str(p["n_confirm"])), ("start_time", iso_now())])]
        self.file = RunFile(path, "sweep", blocks, SWEEP_COLUMNS, self.gui_version)
        self.files = [self.file]
        self.status(file=path)

        total_points = sum(loop_point_count(p["shape"], a, b, p["step_v"]) for _, a, b in plan) + N_SETTLE_READINGS
        self.n_done = 0
        self.n_total = max(1, total_points)

        self.settle_readings(lambda rd: self.write_point(rd, 0, 0, None, 0.0, "", "settle"))
        if self.check_stop():
            return

        self.status(state="running")
        for loop_no, vp, vm in plan:
            confirmed_in_loop = self.run_loop(loop_no, p["shape"], vp, vm)
            if self.check_stop():
                return
            if p["shape"] == SHAPE_LADDER and confirmed_in_loop:
                self.log(f"Ladder: confirmed trip in entry {loop_no} ({vp:g} V); later entries skipped.")
                break
        self.reason = "completed"

    # -- helpers ---------------------------------------------------------
    def instrument_block_pairs(self, idn, source_range):
        p = self.p
        pairs = [("idn", idn), ("address", p["visa_address"]), ("source_range_V", f"{source_range:g}"),
                 ("meas_range", "AUTO"), ("NPLC", f"{NPLC_DEFAULT:g}"), ("I_cc_A", f"{p['i_cc_plus']:g}"),
                 ("I_cc_minus_A", f"{p['i_cc_minus']:g}"),
                 ("V_protection_V", f"{protection_command_value(p['protection_v']):g}"),
                 ("terminals", "FRONT"), ("sense", "2-wire"), ("read_delay_s", f"{p['dwell_s']:g}"),
                 ("pacing_s", f"{PACING_S:g}"), ("rst_sleep_s", f"{RST_SLEEP_S:g}"), ("settle_s", f"{SETTLE_S:g}"),
                 ("n_settle", str(N_SETTLE_READINGS)), ("ramp_step_s", f"{RAMP_STEP_S:g}"),
                 ("compliance_flag", "STAT bit3 or bit16"), ("trip_query", ":SENS:CURR:PROT:TRIP?"),
                 ("event_code", EVENT_CODE_LEGEND)]
        return ("instrument", pairs)

    def write_point(self, rd, loop, segment, vmax, v_set, event, phase):
        row = {"t_s": self.t_s(), "loop": loop, "segment": segment, "vmax_V": vmax, "V_set_V": v_set,
               "V_meas_V": rd.v, "I_A": rd.i, "R_ohm": rd.r_ohm, "compliance_flag": rd.compliance_flag,
               "trip_query": rd.trip_query, "status_bits": rd.stat, "event": event,
               "event_code": event_code(event), "phase": phase}
        self.file.write_row([row[c] for c in SWEEP_COLUMNS])
        self.last_compliance_flag = rd.compliance_flag
        self.post("row", "sweep", row)
        self.n_done += 1
        self.status(progress=min(1.0, self.n_done / self.n_total))
        return row

    def ensure_compliance_for(self, sign):
        if sign == self.current_sign:
            return
        i_cc = self.p["i_cc_plus"] if sign > 0 else self.p["i_cc_minus"]
        self.with_retry(lambda: self.link.set_compliance(i_cc), f"compliance {i_cc:g} A")
        self.log_compliance()
        self.current_sign = sign

    def measure(self, v):
        def op():
            self.link.source(v)
            return self.link.read_point(True)
        rd = self.with_retry(op, f"point {v:g} V")
        if rd.ovp_flag:
            self.write_point(rd, self._loop, self._segment, self._vmax, v, "ovp", "sweep")
            raise OvpTrip(f"status bit 4 set at {v:g} V")
        return rd

    def run_loop(self, loop_no, shape, vp, vm):
        """Returns True if a compliance trip was confirmed in this loop."""
        segments = build_loop(shape, vp, vm, self.p["step_v"])
        confirmed = False
        pending_return_from = None
        for seg in segments:
            if self.check_stop():
                return confirmed
            self.ensure_compliance_for(seg["sign"])
            vmax = vp if seg["sign"] > 0 else vm
            self._loop, self._segment, self._vmax = loop_no, seg["segment"], vmax
            points = seg["points"]
            if not seg["outgoing"] and pending_return_from is not None:
                points = return_leg(pending_return_from, self.p["step_v"])
                pending_return_from = None
            tracker = TripTracker(self.p["n_confirm"]) if seg["outgoing"] else None
            for idx, v in enumerate(points):
                if self.check_stop():
                    return confirmed
                rd = self.measure(v)
                event = ""
                if tracker is not None:
                    event, turnaround = tracker.observe(rd.compliance_flag)
                    if idx == len(points) - 1 and not turnaround:
                        closing = tracker.close_segment()
                        if closing:
                            event = closing
                    if "trip_confirmed" in event:
                        confirmed = True
                    self.write_point(rd, loop_no, seg["segment"], vmax, v, event, "sweep")
                    if turnaround:
                        pending_return_from = v
                        self.log(f"Loop {loop_no} segment {seg['segment']}: compliance confirmed at {v:g} V, turning around.")
                        break
                else:
                    self.write_point(rd, loop_no, seg["segment"], vmax, v, event, "sweep")
        return confirmed


class SweepGUI(MemristorGuiBase):
    MODULE_TITLE = "Sweep"
    MODULE_KEY = "sweep"
    PROGRAM_VERSION = "1.1"

    def create_module_inputs(self, parent):
        frame = self.make_frame(parent, 'Sweep')
        r = 0
        self.add_combo(frame, r, "Shape", "shape", SHAPES, SHAPE_BIPOLAR_PLUS, width=18, command=self._on_shape); r += 1
        self.add_entry(frame, r, "V max + (V)", "vmax_plus", "5", "at or below never-exceed"); r += 1
        self.add_entry(frame, r, "V max - (V)", "vmax_minus", "5", "positive number, applied negative"); r += 1
        self.add_entry(frame, r, "Step (V)", "step_v", "0.1"); r += 1
        self.add_entry(frame, r, "Dwell (s)", "dwell_s", "0.1", ":SOUR:DEL, per point"); r += 1
        self.add_entry(frame, r, "I cc + (uA)", "i_cc_plus_uA", "10", "compliance, positive half"); r += 1
        self.add_entry(frame, r, "I cc - (uA)", "i_cc_minus_uA", "10", "compliance, negative half"); r += 1
        self.add_entry(frame, r, "Loops", "loops", "1", "full loops"); r += 1
        self.add_entry(frame, r, "Ladder list (V)", "ladder", "", "e.g. 5, 10, 20 (ladder shape only)", width=22); r += 1
        self.add_entry(frame, r, "n confirm", "n_confirm", "3", "points confirming a trip"); r += 1
        self.warn_label = self.add_wide_label(frame, "", fg='#7A3B00')

    def _on_shape(self):
        self.refresh_estimate()

    def build_figure(self, fig):
        self.ax_lin = fig.add_subplot(1, 2, 1)
        self.ax_log = fig.add_subplot(1, 2, 2)
        self.ax_lin.set_title("Current vs. Voltage", fontweight='bold')
        self.ax_log.set_title("|Current| vs. Voltage", fontweight='bold')
        self.ax_lin.set_xlabel("V (V)")
        self.ax_lin.set_ylabel("I (A)")
        self.ax_log.set_xlabel("V (V)")
        self.ax_log.set_ylabel("|I| (A)")
        self.ax_log.set_yscale("log")
        self.ax_lin.grid(True, alpha=0.3)
        self.ax_log.grid(True, alpha=0.3, which='both')
        self.points = []

    def reset_plot(self):
        self.points = []
        self.redraw()

    def on_row(self, tag, row):
        if row.get("phase") != "sweep":
            return
        self.points.append(row)

    def redraw(self):
        p = self.params or {}
        for ax in (self.ax_lin, self.ax_log):
            ax.clear()
            ax.grid(True, alpha=0.3)
            ax.set_xlabel("V (V)")
        self.ax_lin.set_ylabel("I (A)")
        self.ax_log.set_ylabel("|I| (A)")
        self.ax_log.set_yscale("log")
        self.ax_lin.set_title("Current vs. Voltage", fontweight='bold')
        self.ax_log.set_title("|Current| vs. Voltage", fontweight='bold')
        loops = sorted({r["loop"] for r in self.points})
        for loop in loops:
            for seg in (1, 2, 3, 4):
                rows = [r for r in self.points if r["loop"] == loop and r["segment"] == seg]
                if not rows:
                    continue
                v = [r["V_meas_V"] for r in rows]
                i = [r["I_A"] for r in rows]
                colour = SEGMENT_COLOURS[(seg - 1) % 4]
                label = f"loop {loop}" if seg == 1 else None
                self.ax_lin.plot(v, i, '-', color=colour, lw=1.0, label=label)
                self.ax_log.plot(v, [abs(x) if x else float('nan') for x in i], '-', color=colour, lw=1.0)
                full = [(a, b) for a, b, r in zip(v, i, rows) if not r["compliance_flag"]]
                hollow = [(a, b) for a, b, r in zip(v, i, rows) if r["compliance_flag"]]
                if full:
                    self.ax_lin.plot([a for a, _ in full], [b for _, b in full], 'o', ms=3, color=colour)
                    self.ax_log.plot([a for a, _ in full], [abs(b) if b else float('nan') for _, b in full], 'o', ms=3, color=colour)
                if hollow:
                    self.ax_lin.plot([a for a, _ in hollow], [b for _, b in hollow], 'o', ms=5, mfc='none', color=colour)
                    self.ax_log.plot([a for a, _ in hollow], [abs(b) if b else float('nan') for _, b in hollow], 'o', ms=5, mfc='none', color=colour)
        if p.get("i_cc_plus"):
            self.ax_lin.axhline(p["i_cc_plus"], ls='--', lw=0.8, color='#777777')
            self.ax_lin.axhline(-p["i_cc_minus"], ls='--', lw=0.8, color='#777777')
            self.ax_log.axhline(p["i_cc_plus"], ls='--', lw=0.8, color='#777777')
            if p["i_cc_minus"] != p["i_cc_plus"]:
                self.ax_log.axhline(p["i_cc_minus"], ls='--', lw=0.8, color='#777777')
        if loops:
            self.ax_lin.legend(fontsize=8, loc='best')
        if p:
            self.figure.suptitle(ascii_text(
                f"{p['sample_name']} | sweep | {SHAPE_KEYS[p['shape']]} | Vmax +{p['vmax_plus']:g}/-{p['vmax_minus']:g} V | "
                f"Icc {p['i_cc_plus'] * 1e6:g}/{p['i_cc_minus'] * 1e6:g} uA"), fontsize=10)

    def _plan_from_inputs(self):
        shape = self.get_str("shape")
        step = self.get_float("step_v", "Step", 1e-6)
        vmax_plus = self.get_float("vmax_plus", "V max +", 1e-6)
        vmax_minus = self.get_float("vmax_minus", "V max -", 1e-6)
        loops = self.get_int("loops", "Loops", 1)
        ladder = parse_ladder(self.get_str("ladder")) if shape == SHAPE_LADDER else []
        return shape, step, vmax_plus, vmax_minus, loops, ladder

    def estimate_run_time_s(self):
        shape, step, vp, vm, loops, ladder = self._plan_from_inputs()
        dwell = self.get_float("dwell_s", "Dwell", 0)
        plan = run_plan(shape, vp, vm, step, loops, ladder)
        n = sum(loop_point_count(shape, a, b, step) for _, a, b in plan)
        return sweep_time_estimate_s(n, dwell) + SETTLE_S + RST_SLEEP_S

    def collect_params(self):
        p = self.collect_common_params()
        shape, step, vp, vm, loops, ladder = self._plan_from_inputs()
        p.update(shape=shape, step_v=step, vmax_plus=vp, vmax_minus=vm, loops=loops, ladder=ladder)
        p["dwell_s"] = self.get_float("dwell_s", "Dwell", 0)
        p["i_cc_plus"] = validate_i_cc(self.get_float("i_cc_plus_uA", "I cc +", 0) / 1e6, "I cc +")
        p["i_cc_minus"] = validate_i_cc(self.get_float("i_cc_minus_uA", "I cc -", 0) / 1e6, "I cc -")
        p["n_confirm"] = self.get_int("n_confirm", "n confirm", 0)
        if shape == SHAPE_LADDER and not ladder:
            raise ValueError("Ladder shape needs a ladder list, e.g. 5, 10, 20.")
        warnings = []
        candidates = ladder if shape == SHAPE_LADDER else [vp, vm]
        for v in candidates:
            if step > v:
                raise ValueError(f"Step {step:g} V is larger than V max {v:g} V.")
            ok, msg = check_vmax_against_protection(v, p["protection_v"])
            if not ok:
                raise ValueError(msg)
            if msg:
                warnings.append(msg)
        p["_warning"] = " ".join(warnings)
        p["_sample_block"] = self.sample_block(p)
        try:
            self.warn_label.config(text=p["_warning"])
        except Exception:
            pass
        return p

    def make_worker(self, params):
        timeout = max(VISA_TIMEOUT_MIN_S, 3 * (params["dwell_s"] + NPLC_DEFAULT * PLC_S + 0.5))
        link = K2400Link(params["visa_address"], timeout)
        worker = SweepWorker(params, link, self.stop_event, self.data_queue)
        worker.gui_version = self.PROGRAM_VERSION
        return worker

    def describe_params(self, p):
        return (f"{p['sample_name']} | {SHAPE_KEYS[p['shape']]} | Vmax +{p['vmax_plus']:g}/-{p['vmax_minus']:g} V | "
                f"step {p['step_v']:g} V | dwell {p['dwell_s']:g} s | Icc {p['i_cc_plus']:g}/{p['i_cc_minus']:g} A | "
                f"loops {p['loops']} | ladder {p['ladder']} | n_confirm {p['n_confirm']} | "
                f"protection {protection_command_value(p['protection_v']):g} V")


def main():
    root = tk.Tk()
    SweepGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
