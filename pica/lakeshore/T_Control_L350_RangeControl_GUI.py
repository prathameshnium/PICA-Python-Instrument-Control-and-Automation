"""
Module: T_Control_L350_RangeControl_GUI.py
Purpose: Single temperature ramp on a Lake Shore Model 350, with heater
         ranges shown in watts, PID presets for a CCR (closed-cycle, 4-350 K)
         and for the LN2-dewar probe, a PID zone table, both heater outputs,
         and optional heater / sensor / display setup.  v9.1.

REWRITTEN on 27 Nov 2026 at the user's request (was v8.0).  The L350
originals are otherwise frozen; this file is listed in APPROVED_REWRITES in
tests/test_lakeshore_340_siblings.py.  It is kept similar to
T_Control_L340_RangeControl_GUI.py, so both are handled the same way; only
the commands differ.

v9.1 (28 Nov 2026): cryostat choice (CCR / LN2 probe) with the "CCR 1"
zone table as the default; the mouse wheel scrolls the panel and never
changes a drop-down; the rate limit is an editable table; the sensor panel
picks input -> sensor type -> a curve from the list read from the
instrument (read-only: curve headers are only queried, never written);
Output 2 defaults to the lab's 25 ohm heater; a warning when a zone's
heater sits at 100 % below the setpoint (its range is too small).

CCR 1 (default zone table, untested in the lab: a starting point)
-----------------------------------------------------------------
  Up to 8 K    Output 1 range 4 (7.5 W)  P 6.3  I 2  D 0  MOUT 0
  Up to 350 K  Output 1 range 5 (75 W)   P 2    I 2  D 0  MOUT 0
  Why: the 350's integral term is P*I*integral(e) (manual 2.13), so it
  scales with P.  One range step is 10x in power = sqrt(10) in current, so
  dropping one range and multiplying P by sqrt(10) (I unchanged) keeps the
  heater power the same at the switch: no sudden heating (assumes the
  default Current heater display and that the 350 keeps integral(e), as the
  manual's equation reads; check at the first 8 K crossing).  At a switch
  the write that LOWERS the power goes first (RANGE before PID going down
  a range, PID before RANGE going up), so the in-between state is a short
  dip, never a 10x spike.  Zones switch on the MEASURED temperature, so a
  zone's range must be able to heat past its own upper bound: range 4
  tops out near 13-15 K on a 1 W (RDK-408D2 class) cold head, hence 8 K
  with margin for mains frequency and first-stage load, and no range-3
  zone (0.75 W cannot even hold 4.2 K on that head).  Copper's heat
  capacity falls ~T^3 (about 4000x less at 4 K than at 300 K): the stage
  is fast at low T and a slow integrator at high T, where I 2 (500 s)
  damps better than I 4.  P 2 sits between the lab's tested 340 setting
  (P 0.5 at 2 A = P 0.58 here) and the simulation's best (P 3).  From a
  simulation of the 350 algorithm on 0.1 W and 1 W @ 4.2 K Sumitomo 4 K GM
  cold heads with 15 % cold-head ripple, reviewed by a second (referee)
  pass, 28 Nov 2026.

How a run works
---------------
  1. Connect (or press Start, which connects first).  The window reads the
     present temperature, setpoint, PID, manual output, heater range,
     heater setup and the input type, so you can see what the instrument
     is doing before you change anything.  Connect/Disconnect never change
     a setting.
  2. Start checks the control input reads in kelvin (INTYPE units: the 350
     takes setpoints in the input's preferred units), that no heater error
     is latched, and that the target is under the safety maximum.
  3. Every output used in the run is put in Closed Loop PID on the control
     input (OUTMODE n,1,<input>,0), then PID, manual output (MOUT) and
     heater range are sent, then the ramp starts.  A 350 ramps from the
     CURRENT SETPOINT, so each setpoint is first pinned to the present
     temperature with the ramp off.  When both outputs are used, both
     setpoints ramp together, so a zone can hand over from one heater to
     the other without a jump.
  4. Every "Log every" seconds: read, write a CSV row (fsync), plot, and in
     zone mode send the PID / MOUT / range of the zone the MEASURED
     temperature is in when the zone changes (no hysteresis; logged).
  5. Done when the reading stays inside the tolerance for the dwell time:
     heater off (default) or hold at target.
  6. Stop = RAMP n,0,0 and RANGE n,0 on the outputs this run used.

No dialog appears during or after a run (runs are unattended): completion
and errors go to the console, the status banner and a beep.  Dialogs only
appear for actions you start yourself (Start refused, Send to instrument,
closing during a run).

Heater outputs (manual 2.11.1, Tables 1-5, 1-6, 2-3, 3-3, 4-14)
--------------------------------------------------------------
  Output 1: main heater, 50 V compliance.  25 ohm setting -> up to 1.732 A,
            75 W; 50 ohm setting -> up to 1 A, 50 W.  Max current
            0.707 / 1 / 1.414 / 1.732 A or a user value (HTRSET).
  Output 2: sample heater, 100 mA, 10 V -> 1 W into 100 ohm, but only
            0.25 W into the lab's 25 ohm heater (I^2 R = 0.1^2 x 25).
  Both have five ranges, each one decade lower in power than the next:
  range 5 = full power, range 4 = 1/10, ... range 1 = 1/10000.

Manual output (MOUT, manual 2.13.4 / 4.5.1.5.5)
-----------------------------------------------
  In Closed Loop PID the manual output is ADDED to the PID output: a fixed
  heater percentage the PID works around.  If you know the heater % that
  holds a temperature (read it from a previous run), putting about that
  value in MOUT lets the loop settle with less integral wind-up.  Too much
  MOUT heats the sample even when PID asks for zero, and the LN2 probe can
  only cool passively, so it overshoots and does not come back.  The
  manual says to leave it at 0 % when not in use; this window sends the
  value you set (0 by default) at Start, which also clears a value left
  over from the front panel or the Direct Control window.

Model 350 commands used (User's Manual, chapter 6):
  OUTMODE n,1,<in>,0   Closed Loop PID on input        OUTMODE? n
  PID n,P,I,D          P 0.1-1000, I 0.1-1000, D 0-200 PID? n
  MOUT n,<%>           0-100 %                         MOUT? n
  RANGE n,<0-5>                                        RANGE? n
  RAMP n,<on>,<rate>   0.001-100 K/min                 RAMP? n
  SETP n,<K>                                           SETP? n
  HTRSET 1,<res>,<max I>,<user I>,<I/P>                HTRSET? n
  INTYPE <in>,<type>,<auto>,<range>,<comp>,<units>,<exc>  INTYPE? <in>
  INCRV <in>,<curve>   DISPLAY <mode>,<fields>,<out>   DISPFLD <f>,<in>,<units>
  CRVHDR? <curve>      curve list, query only (1-59)
  KRDG? <in>, RDGST? <in>, HTR? n, HTRST? n, *IDN?, *CLS
  (*RST is never sent: it would reset the loop, setpoint and ramp.  No
  curve is ever written or deleted: only INCRV picks which one an input
  uses.  Changing an input's setup turns the heater range off (manual
  4.5.1.5.8), so the sensor panel is locked during a run.)
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, Canvas
import os
import csv
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- Optional Packages ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None


def run_script_process(script_path):
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    """
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
        # Go up 1 level: lakeshore -> pica
        plotter_path = os.path.join(
            script_dir,
            "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 1 level: lakeshore -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


# -------------------------------------------------------------------------------
# --- HEATER POWER (manual Tables 1-5, 1-6, 2-3, 3-3, 4-14) ---
# -------------------------------------------------------------------------------

# Range names.  Low = 2, Medium = 4, High = 5 are the names the v8 window
# used, so they keep their meaning; 1 and 3 got names in between.
RANGE_NAMES = {0: "Off", 1: "Very low", 2: "Low", 3: "Medium-low",
               4: "Medium", 5: "High"}

# HTRSET codes (Output 1 only).  The manual's HTRSET page prints 1.141 A
# and 2 A for codes 3 and 4; the front-panel menu and Table 4-14 give the
# real discrete limits, 1.414 A and 1.732 A (Output 1 cannot source more
# than 1.732 A).
OUT1_RESISTANCE = {1: 25.0, 2: 50.0}
OUT1_MAX_CURRENT = {1: 0.707, 2: 1.0, 3: 1.414, 4: 1.732}   # 0 = user
OUT1_CURRENT_LIMIT = {1: 1.732, 2: 1.0}     # by resistance setting
OUT1_COMPLIANCE_V = 50.0
OUT1_USER_CURRENT_RANGE = (0.1, 1.732)
OUT2_MAX_CURRENT = 0.1
OUT2_COMPLIANCE_V = 10.0
OUT2_DEFAULT_RESISTANCE = 25.0      # the lab's heaters are 25 ohm (0.25 W)


def heater_max_power(current_a, resistance_ohm, compliance_v):
    """Full-scale power of a current-source heater: the smaller of I^2 R and
    V^2 / R (manual 2.11.1.3)."""
    if current_a <= 0 or resistance_ohm <= 0:
        return 0.0
    return min(current_a ** 2 * resistance_ohm, compliance_v ** 2 / resistance_ohm)


def out1_full_power(resistance_code, max_current_code, user_current=0.0):
    """Output 1 power on range 5 for an HTRSET setting."""
    r = OUT1_RESISTANCE[int(resistance_code)]
    if int(max_current_code) == 0:
        i = float(user_current)
    else:
        i = OUT1_MAX_CURRENT[int(max_current_code)]
    i = min(i, OUT1_CURRENT_LIMIT[int(resistance_code)])
    return heater_max_power(i, r, OUT1_COMPLIANCE_V)


def out2_full_power(resistance_ohm=OUT2_DEFAULT_RESISTANCE):
    """Output 2 power on range 5 (1 W into 100 ohm, 0.25 W into 25 ohm)."""
    return heater_max_power(OUT2_MAX_CURRENT, float(resistance_ohm), OUT2_COMPLIANCE_V)


def range_power(range_code, full_power_w):
    """Power available on a range: each range is one decade below the next."""
    code = int(range_code)
    if code <= 0:
        return 0.0
    return full_power_w / (10 ** (5 - code))


def format_power(watts):
    """'75 W', '7.5 mW', '750 uW' (ASCII, safe in data files)."""
    if watts <= 0:
        return "0 W"
    if watts >= 1:
        return f"{watts:.3g} W"
    if watts >= 1e-3:
        return f"{watts * 1e3:.3g} mW"
    return f"{watts * 1e6:.3g} uW"


def range_label(code, full_power_w):
    """'5 - High (75 W)'; '0 - Off'."""
    code = int(code)
    if code == 0:
        return "0 - Off"
    return f"{code} - {RANGE_NAMES[code]} ({format_power(range_power(code, full_power_w))})"


def parse_range(label):
    """Range code from a range label or a bare digit."""
    return int(str(label).strip().split()[0])


def heater_watts(percent, range_power_w, display_is_power):
    """Rough heater power from HTR? percent: percent of full-scale power, or
    of full-scale current (power goes with the square)."""
    frac = max(0.0, float(percent)) / 100.0
    return range_power_w * (frac if display_is_power else frac * frac)


# -------------------------------------------------------------------------------
# --- PID PRESETS, RATE LIMITS AND ZONES (CCR and LN2-dewar probe) ---
# -------------------------------------------------------------------------------

# One range step is 10x in power; with the default Current heater display
# the PID output is a % of full-scale current, so one step is sqrt(10) in
# output.  The integral term is P*I*integral(e) (manual 2.13), so scaling P
# by this factor when the range changes keeps the heater power continuous.
RANGE_STEP_CURRENT = 10 ** 0.5


def bumpless_p(p, from_range, to_range, display_is_power=False):
    """P for `to_range` that gives the same heater power as P on
    `from_range` (I unchanged).  A lower range needs a higher P."""
    step = 10.0 if display_is_power else RANGE_STEP_CURRENT
    return p * step ** (int(from_range) - int(to_range))


# CCR 1: the default for the Sumitomo 4 K CCR, 4-350 K (see the module
# docstring).  A starting point to be tested in the lab, 28 Nov 2026.
CCR1_BOUND_K = 8.0
PID_CCR1_HIGH = (2.0, 2.0, 0.0)                                   # range 5
PID_CCR1_LOW = (round(bumpless_p(2.0, 5, 4), 1), 2.0, 0.0)        # range 4: P 6.3

# The 350's integral term scales with BOTH P and I, and the LN2 probe can
# only cool passively, so below 100 K the wind-up must stay tiny or an
# overshoot never recovers (lab result 10 Jul 2026).  Keep I > 0: I = 0
# turns the integral off (permanent offset).  Same values as
# T_Control_L350_Step_GUI_advanced.py and the Direct Control presets.
PID_ULTRA = (0.2, 1.0, 0.0)
PID_SLOW = (0.5, 4.0, 0.0)
PID_MEDIUM = (20.0, 15.0, 0.0)
PID_FAST = (50.0, 20.0, 0.0)
PID_PRESETS = {
    "CCR 1, 8-350 K (P=2, I=2, D=0) - use range 5": PID_CCR1_HIGH,
    "CCR 1, below 8 K (P=6.3, I=2, D=0) - use range 4": PID_CCR1_LOW,
    "LN2 Ultra-slow (P=0.2, I=1, D=0) - below 100 K": PID_ULTRA,
    "LN2 Slow (P=0.5, I=4, D=0) - winds up below 100 K": PID_SLOW,
    "LN2 Medium (P=20, I=15, D=0) - 100 K and above": PID_MEDIUM,
    "LN2 Fast (P=50, I=20, D=0) - large steps only": PID_FAST,
}
PID_CUSTOM = "Custom (type P, I, D)"

# Ramp-rate limit table "below T: max K/min, ..., else: max".  The default
# is the LN2-probe guideline of 5 Jul 2026, the same table the Step
# Frequency Scan (E4980A) windows use: below 77 K 0.3 K/min, below 100 K
# 0.5 K/min, else 5 K/min.  "Below" is strict.  Off unless ticked.
RATE_TABLE_DEFAULT = "77:0.3, 100:0.5, else:5"
LN2_RATE_CAPS = ((77.0, 0.3), (100.0, 0.5))
LN2_RATE_CAP_ELSE = 5.0

# Heater outputs a zone or the fixed setting uses.
HEATER_OUTPUTS = {1: (1,), 2: (2,), 3: (1, 2)}
FIXED_HEATER_LABELS = {
    1: "Output 1 only (main heater, up to 75 W)",
    2: "Output 2 only (sample heater, 0.25 W into 25 ohm)",
    3: "Both outputs (each with its own PID and range)",
}

# Zone rows: (upper bound K, heater 1/2/3, out1, out2) where each output
# is (P, I, D, MOUT %, range).  The first zone runs from 0 K up to its
# bound; a temperature above the last bound uses the last zone.  Output 2
# values are only used when a row's heater is "Out 2" or "Both".
ZONES_CCR1 = (
    (CCR1_BOUND_K, 1, PID_CCR1_LOW + (0.0, 4), PID_CCR1_LOW + (0.0, 4)),
    (350.0, 1, PID_CCR1_HIGH + (0.0, 5), PID_CCR1_HIGH + (0.0, 5)),
)
# LN2 probe: the anti-windup PID below 100 K, Medium above, range 5.
ZONES_LN2 = (
    (100.0, 1, (0.2, 1.0, 0.0, 0.0, 5), (0.2, 1.0, 0.0, 0.0, 5)),
    (340.0, 1, (20.0, 15.0, 0.0, 0.0, 5), (20.0, 15.0, 0.0, 0.0, 5)),
)

# Cryostat choice: what each one changes.  'ln2_checks' = the below-100 K
# wind-up warning at Start and the rate-limit reminder.
SYSTEMS = {
    'CCR': dict(label="CCR (closed cycle, 4-350 K)", zones=ZONES_CCR1,
                zone_name="CCR 1", t_min=4.0, t_max=350.0, max_temp=355.0,
                preset="CCR 1, 8-350 K (P=2, I=2, D=0) - use range 5",
                ln2_checks=False),
    'LN2': dict(label="LN2 dewar probe (77-340 K)", zones=ZONES_LN2,
                zone_name="LN2 probe", t_min=77.0, t_max=340.0, max_temp=340.0,
                preset="LN2 Medium (P=20, I=15, D=0) - 100 K and above",
                ln2_checks=True),
}
DEFAULT_SYSTEM = 'CCR'
ZONE_DEFAULTS = SYSTEMS[DEFAULT_SYSTEM]['zones']
ZONE_T_MIN = SYSTEMS[DEFAULT_SYSTEM]['t_min']
ZONE_T_MAX = SYSTEMS[DEFAULT_SYSTEM]['t_max']
PID_DEFAULT_PRESET = SYSTEMS[DEFAULT_SYSTEM]['preset']
HARD_TEMP_DEFAULT_K = SYSTEMS[DEFAULT_SYSTEM]['max_temp']

# A zone whose heater sits at 100 % this long while the reading is below the
# setpoint cannot reach its own upper bound: logged once per zone.
SATURATION_WARN_S = 120.0


def parse_rate_table(text):
    """'77:0.3, 100:0.5, else:5' -> (((77.0, 0.3), (100.0, 0.5)), 5.0)."""
    caps, default = [], None
    for part in str(text).replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        key, sep, val = part.partition(':')
        if not sep:
            raise ValueError(f"'{part}' is not 'temperature:rate'")
        rate = float(val)
        if rate <= 0:
            raise ValueError(f"rate in '{part}' must be positive")
        if key.strip().lower() == 'else':
            default = rate
        else:
            caps.append((float(key), rate))
    if default is None:
        raise ValueError("the table needs an 'else:<rate>' entry")
    return tuple(sorted(caps)), default


def rate_cap_for(coldest_k, caps, default):
    """Rate cap (K/min) for the coldest temperature the ramp passes."""
    for bound, cap in caps:
        if coldest_k < bound:
            return cap
    return default


def ln2_rate_cap(coldest_k):
    """Rate cap of the LN2-probe guideline table."""
    return rate_cap_for(coldest_k, LN2_RATE_CAPS, LN2_RATE_CAP_ELSE)


# Curve headers (CRVHDR?): <format> 1 mV/K, 2 V/K, 3 ohm/K, 4 log ohm/K;
# <coefficient> 1 negative, 2 positive.  Which curves suit which INTYPE
# sensor type, as (format set, coefficient or None = either).
CURVE_FORMATS = {1: "mV/K", 2: "V/K", 3: "ohm/K", 4: "log ohm/K"}
CURVE_KEEP = "(keep the present curve)"
CURVE_NONE = "00 - none (sensor units only)"
CURVE_MATCH = {
    1: ({1, 2}, None),       # diode
    2: ({3}, 2),             # platinum (PTC) RTD
    3: ({3, 4}, 1),          # NTC RTD (Cernox, RuOx, germanium)
    4: ({1}, None),          # thermocouple
    5: (None, None),         # capacitance: anything
}


def curve_matches(sensor_type, header):
    """True if a curve header (dict from read_curve_headers) suits a type."""
    formats, coeff = CURVE_MATCH.get(int(sensor_type), (None, None))
    if formats is not None and header['format'] not in formats:
        return False
    return coeff is None or header['coefficient'] == coeff


def curve_label(number, header):
    """'06 - PT-100 (STANDARD) to 800 K, ohm/K'."""
    sn = f" ({header['sn']})" if header['sn'] else ""
    fmt = CURVE_FORMATS.get(header['format'], f"format {header['format']}")
    return f"{int(number):02d} - {header['name']}{sn} to {header['limit']:g} K, {fmt}"


def select_zone(zones, temperature):
    """Index of the zone a temperature falls in (no hysteresis).

    zones: sequence of rows whose first item is the upper bound, sorted
    ascending.  The first zone whose bound is >= temperature wins; anything
    above the last bound uses the last zone.
    """
    if not zones:
        raise ValueError("zone table is empty")
    for idx, zone in enumerate(zones):
        if temperature <= zone[0]:
            return idx
    return len(zones) - 1


def generate_equal_zones(n_segments, t_min=ZONE_T_MIN, t_max=ZONE_T_MAX,
                         template=ZONE_DEFAULTS):
    """Split [t_min, t_max] into n equal segments; each takes the settings
    of the template zone its upper bound falls in."""
    n_segments = int(n_segments)
    if n_segments < 1:
        raise ValueError("at least one segment is required")
    if t_max <= t_min:
        raise ValueError("t_max must be above t_min")
    width = (t_max - t_min) / n_segments
    out = []
    for k in range(1, n_segments + 1):
        ub = t_min + width * k
        if k == n_segments:
            ub = t_max
        tpl = template[select_zone(template, ub)]
        out.append((round(ub, 3),) + tuple(tpl[1:]))
    return out


def validate_pid(p, i, d):
    """Model 350 PID limits (PID command): P 0.1-1000, I 0.1-1000, D 0-200."""
    if not 0.1 <= p <= 1000:
        raise ValueError(f"P must be 0.1-1000 on a Model 350, got {p:g}")
    if not 0.1 <= i <= 1000:
        raise ValueError(f"I must be 0.1-1000 on a Model 350, got {i:g} "
                         "(the 350 has no I = 0)")
    if not 0 <= d <= 200:
        raise ValueError(f"D must be 0-200 % on a Model 350, got {d:g}")


def validate_mout(value):
    if not 0 <= value <= 100:
        raise ValueError(f"Manual output must be 0-100 %, got {value:g}")


def ascii_label(text):
    """Data-file headers stay ASCII (lab PCs open files as cp1252)."""
    return (str(text).replace("→", "->").replace("µ", "u")
            .replace("Ω", "Ohm").replace("±", "+/-")
            .encode("ascii", "replace").decode("ascii"))


def explain_visa_error(exc):
    """Plain-language hint for the VISA errors seen on the lab PCs."""
    text = str(exc)
    if 'VI_ERROR_ALLOC' in text:
        return ("VI_ERROR_ALLOC comes from the VISA driver before any command "
                "is sent: most often the interface is a stale entry cached by "
                "Keysight Connection Expert (an adapter that is no longer "
                "plugged in). Remove it in Connection Expert, rescan, and use "
                "Identify to find the address that answers as MODEL350.")
    if 'VI_ERROR_TMO' in text:
        return ("Timeout: the address exists but nothing answered. Check the "
                "350 is on, its IEEE address matches, and no other programme "
                "holds the session.")
    if 'VI_ERROR_RSRC_NFOUND' in text or 'VI_ERROR_RSRC_BUSY' in text:
        return "The resource is missing or busy: rescan, close other programmes."
    return ""


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------


class Lakeshore350_Backend:
    MODEL_TOKENS = ("MODEL350", "MODEL 350")
    INPUT_NUMBERS = {'A': 1, 'B': 2, 'C': 3, 'D': 4}
    HEATER_ERRORS = {
        0: "No error",
        1: "Heater open load",
        2: "Heater short (Output 1) / compliance (Output 2)",
    }
    RDGST_BITS = (
        (1, "invalid reading"), (16, "temp underrange"), (32, "temp overrange"),
        (64, "sensor units zero"), (128, "sensor units overrange"),
    )
    # INTYPE <sensor type> codes and their <range> lists (Table 6-8).
    SENSOR_TYPES = {
        0: "Disabled",
        1: "Diode",
        2: "Platinum RTD (PTC)",
        3: "NTC RTD (Cernox, RuOx)",
        4: "Thermocouple (3060 option)",
        5: "Capacitance (3061 option)",
    }
    INPUT_RANGES = {
        0: ["(none)"],
        1: ["2.5 V (silicon diode, e.g. DT-470)", "10 V (GaAlAs diode)"],
        2: ["10 ohm", "30 ohm", "100 ohm", "300 ohm", "1 kohm", "3 kohm",
            "10 kohm"],
        3: ["10 ohm", "30 ohm", "100 ohm", "300 ohm", "1 kohm", "3 kohm",
            "10 kohm", "30 kohm", "100 kohm", "300 kohm"],
        4: ["50 mV"],
        5: ["15 nF", "150 nF"],
    }
    UNITS = {1: "Kelvin", 2: "Celsius", 3: "Sensor units"}
    EXCITATION = {0: "1 mV", 1: "10 mV"}
    DISPLAY_MODES = {0: "Input A", 1: "Input B", 2: "Input C", 3: "Input D",
                     4: "Custom", 5: "Four Loop", 6: "All Inputs"}
    CUSTOM_FIELDS = {0: "2 large", 1: "4 large", 2: "8 small"}
    FIELD_UNITS = {1: "Kelvin", 2: "Celsius", 3: "Sensor units",
                   4: "Minimum", 5: "Maximum"}

    def __init__(self):
        self.lakeshore = None
        self.idn = ""
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            self.rm = None

    # -- session --

    @property
    def is_connected(self):
        return self.lakeshore is not None

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.read_termination = '\n'
        self.lakeshore.write_termination = '\n'
        self.idn = self.lakeshore.query('*IDN?').strip()
        print(f"  Lakeshore Connected: {self.idn}")
        return self.idn

    def is_model_350(self):
        idn = self.idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def _write(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        self.lakeshore.write(cmd)

    def _query(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        return self.lakeshore.query(cmd).strip()

    def _fields(self, cmd, n):
        parts = [p.strip() for p in self._query(cmd).split(',')]
        if len(parts) < n:
            raise ValueError(f"unexpected reply to {cmd}: '{','.join(parts)}'")
        return parts

    def clear_status(self):
        self._write('*CLS')

    def identify_resources(self, addresses, timeout_ms=2000):
        """Send *IDN? to each address (user-triggered). {addr: reply|'ERROR: ..'}"""
        out = {}
        if not self.rm:
            return out
        for addr in addresses:
            try:
                inst = self.rm.open_resource(addr)
                try:
                    inst.timeout = timeout_ms
                    inst.read_termination = '\n'
                    inst.write_termination = '\n'
                    out[addr] = inst.query('*IDN?').strip()
                finally:
                    inst.close()
            except Exception as e:
                out[addr] = f"ERROR: {e}"
        return out

    # -- control loop setup --

    def prepare_output(self, output, control_input):
        """OUTMODE <n>,1,<input>,0: Closed Loop PID on the input, output off
        after a power cycle.  Verified by OUTMODE?."""
        inp = self.INPUT_NUMBERS[str(control_input).upper()]
        self._write(f'OUTMODE {output},1,{inp},0')
        time.sleep(0.1)
        mode = self.get_output_mode(output)
        if mode['mode'] != 1 or mode['input'] != inp:
            raise RuntimeError(
                f"OUTMODE {output},1,{inp},0 did not stick: OUTMODE? {output} "
                f"reads {mode}. Check the front panel (Remote/Local) and retry.")
        return mode

    def get_output_mode(self, output):
        parts = self._fields(f'OUTMODE? {output}', 3)
        return {'mode': int(float(parts[0])), 'input': int(float(parts[1])),
                'powerup': int(float(parts[2]))}

    # -- ramp --

    def pin_setpoints(self, outputs, current_temperature):
        """Ramp off and setpoint = present temperature on each output, so a
        loop that is switched on does not chase a leftover setpoint."""
        for n in outputs:
            self._write(f'RAMP {n},0,0')
            self._write(f'SETP {n},{current_temperature:.3f}')

    def start_ramp(self, outputs, target, rate, current_temperature):
        """Pin every setpoint to the present temperature (ramp off), then
        ramp on and send the target, on each output in `outputs`."""
        if not (0.001 <= rate <= 100):
            raise ValueError(f"Ramp rate must be 0.001-100 K/min on a Model 350, got {rate}")
        self.pin_setpoints(outputs, current_temperature)
        time.sleep(0.2)
        for n in outputs:
            self._write(f'RAMP {n},1,{rate}')
            self._write(f'SETP {n},{target}')

    # -- PID, manual output, range --

    def set_pid(self, output, p, i, d):
        validate_pid(p, i, d)
        self._write(f'PID {output},{p},{i},{d}')

    def get_pid(self, output):
        parts = self._fields(f'PID? {output}', 3)
        return float(parts[0]), float(parts[1]), float(parts[2])

    def set_mout(self, output, value):
        validate_mout(value)
        self._write(f'MOUT {output},{value}')

    def get_mout(self, output):
        return float(self._query(f'MOUT? {output}'))

    def set_heater_range(self, output, range_code):
        """RANGE <n>,<0-5>, verified by RANGE?."""
        range_code = int(range_code)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5, got {range_code}")
        self._write(f'RANGE {output},{range_code}')
        time.sleep(0.1)
        back = self.get_heater_range(output)
        if back != range_code:
            raise RuntimeError(f"RANGE {output},{range_code} did not stick: "
                               f"RANGE? {output} = {back}.")

    def get_heater_range(self, output):
        return int(float(self._query(f'RANGE? {output}')))

    # -- heater setup (HTRSET, Output 1) --

    def get_heater_setup(self, output=1):
        parts = self._fields(f'HTRSET? {output}', 4)
        return {'resistance': int(float(parts[0])),
                'max_current': int(float(parts[1])),
                'user_current': float(parts[2]),
                'display': int(float(parts[3]))}

    def set_heater_setup(self, resistance, max_current, user_current, display):
        """HTRSET 1,<res 1|2>,<max I 0-4>,<user I>,<display 1|2>."""
        resistance, max_current, display = int(resistance), int(max_current), int(display)
        if resistance not in OUT1_RESISTANCE:
            raise ValueError("heater resistance must be 1 (25 ohm) or 2 (50 ohm)")
        if max_current not in (0, 1, 2, 3, 4):
            raise ValueError("max current code must be 0 (user) or 1-4")
        if display not in (1, 2):
            raise ValueError("display must be 1 (current) or 2 (power)")
        limit = OUT1_CURRENT_LIMIT[resistance]
        if max_current == 0:
            lo, hi = OUT1_USER_CURRENT_RANGE
            if not lo <= user_current <= min(hi, limit):
                raise ValueError(f"user max current must be {lo:g}-{min(hi, limit):g} A "
                                 f"for the {OUT1_RESISTANCE[resistance]:g} ohm setting")
        elif OUT1_MAX_CURRENT[max_current] > limit + 1e-9:
            raise ValueError(
                f"{OUT1_MAX_CURRENT[max_current]:g} A is more than the "
                f"{OUT1_RESISTANCE[resistance]:g} ohm setting allows ({limit:g} A)")
        else:
            user_current = 0.0
        cmd = f'HTRSET 1,{resistance},{max_current},{user_current:.3f},{display}'
        self._write(cmd)
        return cmd

    # -- sensor input, curve, display --

    def get_input_type(self, control_input):
        parts = self._fields(f'INTYPE? {control_input}', 5)
        out = {'sensor': int(float(parts[0])), 'autorange': int(float(parts[1])),
               'range': int(float(parts[2])), 'compensation': int(float(parts[3])),
               'units': int(float(parts[4]))}
        out['excitation'] = int(float(parts[5])) if len(parts) > 5 and parts[5] else 0
        return out

    def set_input_type(self, control_input, sensor, autorange, rng,
                       compensation, units, excitation):
        sensor = int(sensor)
        if sensor not in self.SENSOR_TYPES:
            raise ValueError("sensor type must be 0-5")
        if not 0 <= int(rng) < len(self.INPUT_RANGES[sensor]):
            raise ValueError(f"range {rng} is not valid for {self.SENSOR_TYPES[sensor]}")
        if int(units) not in self.UNITS:
            raise ValueError("units must be 1 (K), 2 (C) or 3 (sensor)")
        # Non-applicable fields are sent as 0, as the manual recommends.
        autorange = int(autorange) if sensor in (2, 3) else 0
        compensation = int(compensation) if sensor in (2, 3, 4, 5) else 0
        excitation = int(excitation) if sensor == 3 else 0
        cmd = (f'INTYPE {control_input},{sensor},{autorange},{int(rng)},'
               f'{compensation},{int(units)},{excitation}')
        self._write(cmd)
        return cmd

    def get_input_curve(self, control_input):
        return int(float(self._query(f'INCRV? {control_input}')))

    CURVE_NUMBERS = range(1, 60)     # 1-20 standard, 21-59 user

    def get_curve_header(self, curve):
        """CRVHDR? <curve> (query only) -> dict, or None for an empty slot."""
        parts = [p.strip() for p in self._query(f'CRVHDR? {int(curve)}').split(',')]
        if len(parts) < 5:
            return None
        try:
            header = {'name': parts[0], 'sn': parts[1], 'format': int(float(parts[2])),
                      'limit': float(parts[3]), 'coefficient': int(float(parts[4]))}
        except ValueError:
            return None
        if not header['name'] or header['format'] == 0 or header['limit'] <= 0:
            return None
        return header

    def read_curve_headers(self, curves=None):
        """{curve: header} for every non-empty curve.  READ-ONLY: only the
        CRVHDR? query is sent; no curve is ever written or deleted here."""
        out = {}
        for n in (self.CURVE_NUMBERS if curves is None else curves):
            header = self.get_curve_header(n)
            if header:
                out[int(n)] = header
        return out

    def set_input_curve(self, control_input, curve):
        if not 0 <= int(curve) <= 59:
            raise ValueError("curve must be 0 (none), 1-20 standard or 21-59 user")
        cmd = f'INCRV {control_input},{int(curve)}'
        self._write(cmd)
        return cmd

    def get_display(self):
        parts = self._fields('DISPLAY?', 3)
        return int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))

    def set_display(self, mode, num_fields, output):
        if int(mode) not in self.DISPLAY_MODES:
            raise ValueError("display mode must be 0-6")
        cmd = f'DISPLAY {int(mode)},{int(num_fields)},{int(output)}'
        self._write(cmd)
        return cmd

    def get_display_field(self, field):
        parts = self._fields(f'DISPFLD? {int(field)}', 2)
        return int(float(parts[0])), int(float(parts[1]))

    def set_display_field(self, field, input_number, units):
        if not 1 <= int(field) <= 8:
            raise ValueError("display field must be 1-8")
        cmd = f'DISPFLD {int(field)},{int(input_number)},{int(units)}'
        self._write(cmd)
        return cmd

    # -- readings --

    def get_temperature(self, control_input):
        return float(self._query(f'KRDG? {control_input}'))

    def get_reading_status(self, control_input):
        code = int(float(self._query(f'RDGST? {control_input}')))
        return ", ".join(n for bit, n in self.RDGST_BITS if code & bit)

    def get_setpoint(self, output):
        return float(self._query(f'SETP? {output}'))

    def get_heater_output(self, output):
        return float(self._query(f'HTR? {output}'))

    def get_heater_status(self, output):
        code = int(float(self._query(f'HTRST? {output}')))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    # -- stop --

    def stop_ramp(self, outputs=(1,)):
        """Heater off (RANGE n,0, sent first and on its own) then RAMP off,
        on each output.  OUTMODE is left as is (harmless at range 0).
        Returns a list of problems; empty means every RANGE n,0 was sent."""
        if not self.lakeshore:
            return [f"not connected: RANGE 0 not sent to output(s) {list(outputs)}"]
        problems = []
        for n in outputs:
            try:
                self._write(f'RANGE {n},0')
            except Exception as e:
                problems.append(f"RANGE {n},0 failed: {e}")
            try:
                self._write(f'RAMP {n},0,0')
            except Exception as e:
                print(f"  Warning: RAMP {n},0,0 failed. {e}")
        print("  Lakeshore ramp stopped and heater turned off." if not problems
              else f"  WARNING: {problems}")
        return problems

    def close(self):
        """Close the session only; the instrument keeps its state."""
        if self.lakeshore:
            try:
                self.lakeshore.close()
            except Exception as e:
                print(f"  Warning: Error closing Lakeshore session. {e}")
            finally:
                self.lakeshore = None

    def shutdown(self, outputs=(1,)):
        if self.lakeshore:
            problems = self.stop_ramp(outputs)
            self.close()
            return problems
        return []


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class TempControlGUI:
    PROGRAM_VERSION = "9.1"
    LEFT_PANEL_WIDTH = 720
    IDLE_POLL_MS = 2000
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_MUTED = '#6E625A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_GOLD = '#B68B6E', '#BA6B5E', '#BA6B5E'
    CLR_STABLE_WAIT = '#D4A373'
    CLR_OK_GREEN = '#8AB845'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_BASE = ('Segoe UI', 11)
    FONT_SMALL = ('Segoe UI', 9)
    FONT_TABLE = ('Segoe UI', 10)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Lakeshore 350 Temperature Control v{self.PROGRAM_VERSION}")
        self.root.geometry("1500x900")
        self.root.minsize(1150, 700)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.logo_image = None
        self.backend = Lakeshore350_Backend()
        self.data_storage = {'time': [], 'temperature': [], 'setpoint': [],
                             'heater1': [], 'heater2': []}
        self.resource_labels = {}
        self.params = {}
        self.data_file = None
        self.csv_writer = None
        self.data_filepath = None
        self.active_zone = None
        self.zones = []
        self.run_outputs = ()
        self.stable_since = None
        self.reached = False
        self.last_htr_error = {1: 0, 2: 0}
        self.poll_job = None
        self.start_time = time.time()
        # What the instrument's heater setup is known to be (nominal until
        # read): drives the watt labels on every range box.
        self.heater_cfg = {'resistance': 1, 'max_current': 4, 'user_current': 0.0,
                           'display': 1, 'display2': 1, 'known': False}
        # Send buttons for sensor / display stay greyed until the matching
        # Read has succeeded, so defaults are never sent by accident.
        self.sensor_read_input = None
        self.display_read_ok = False
        self.out2_resistance = OUT2_DEFAULT_RESISTANCE
        self.system = DEFAULT_SYSTEM
        self.curve_headers = {}           # {curve: header} read with CRVHDR?
        self.sat_since = None             # heater at 100 % below setpoint since
        self.sat_warned = set()           # zones already warned about
        self._ui_ready = False

        self.setup_styles()
        self.create_widgets()           # sets _ui_ready when every widget exists
        if self._ui_ready:
            self._refresh_curve_choices()
        self._refresh_range_labels()
        self._refresh_states()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ------------------------------------------------------------------ styles

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('Card.TFrame', background=self.CLR_FRAME_BG)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Muted.TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_MUTED, font=self.FONT_SMALL)
        style.configure('Bold.TLabel', background=self.CLR_FRAME_BG,
                        font=('Segoe UI', 10, 'bold'))
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('TRadiobutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.map('TCheckbutton', foreground=[('disabled', self.CLR_MUTED)])
        style.map('TRadiobutton', foreground=[('disabled', self.CLR_MUTED)])
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TButton', font=self.FONT_BASE, padding=(8, 6),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('disabled', '#A89C92'),
                              ('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton',
                  background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor='#BA6B5E')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK)
        style.map('TEntry', fieldbackground=[('disabled', '#DDD3CA'),
                                             ('readonly', '#E9E1D9')],
                  foreground=[('disabled', '#8F837A')])
        style.configure('TCombobox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.map('TCombobox', fieldbackground=[('disabled', '#DDD3CA'),
                                                ('readonly', self.CLR_GRAPH_BG)],
                  foreground=[('disabled', '#8F837A')])
        style.configure('TSpinbox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11,
                             'axes.titlesize': 14, 'axes.labelsize': 12})

    # ----------------------------------------------------------------- layout

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(header, text="Lakeshore 350 Temperature Ramp Utility",
                  style='Header.TLabel', font=font_title_main,
                  foreground=self.CLR_ACCENT_GOLD).pack(side='left', padx=20, pady=10)
        ttk.Button(header, text="📈", command=launch_plotter_utility,
                   width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner,
                   width=3).pack(side='right', padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)
        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)
        self._ui_ready = True
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            target = max(target, self.LEFT_PANEL_WIDTH)
            # Leave the plot at least ~45 % of the window.
            window_w = self.root.winfo_width()
            if window_w > 1:
                target = min(target, int(window_w * 0.55))
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient='vertical', command=canvas.yview)
        frame = ttk.Frame(canvas)
        frame.bind('<Configure>',
                   lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = frame
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        def _wheel_scrolls_panel(event):
            # Over a drop-down or spinbox the wheel scrolls the panel and
            # never changes the selected value ('break' stops ttk's own
            # wheel binding that would step through the values).
            _on_mousewheel(event)
            return 'break'

        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))
        for cls in ('TCombobox', 'TSpinbox'):
            self.root.bind_class(cls, '<MouseWheel>', _wheel_scrolls_panel)

        frame.grid_columnconfigure(0, weight=1)
        self._create_info_panel(frame, 0)
        self._create_connect_panel(frame, 1)
        self._create_ramp_panel(frame, 2)
        self._create_pid_panel(frame, 3)
        self._create_heater_setup_panel(frame, 4)
        self._create_sensor_panel(frame, 5)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 90
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE,
                             bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=8)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "assets", "LOGO",
                                     "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE / 2, LOGO_SIZE / 2,
                                         image=self.logo_image)
        except Exception as e:
            print(f"Warning: Could not load logo. {e}")
        institute_font = ('Segoe UI', self.FONT_BASE[1] + 2, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font).grid(row=0, column=1, padx=10, pady=(14, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font).grid(
            row=1, column=1, padx=10, sticky='nw')
        ttk.Label(frame, text="Lake Shore Model 350 | Output 1 (75 W) + Output 2 (0.25 W, 25 ohm) "
                              "| CCR or LN2 probe",
                  style='Muted.TLabel').grid(row=2, column=1, padx=10, pady=(0, 8), sticky='w')

    def _section(self, parent, grid_row, title):
        frame = ttk.LabelFrame(parent, text=title)
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        # Spare width goes to the last column, so labels and their boxes stay
        # side by side on the left.
        frame.grid_columnconfigure(3, weight=1)
        return frame

    def _note(self, parent, text, row, column=0, columnspan=4, pady=(0, 4)):
        lbl = ttk.Label(parent, text=text, style='Muted.TLabel', justify='left')
        lbl.grid(row=row, column=column, columnspan=columnspan, sticky='w', padx=10, pady=pady)
        return lbl

    # -- 1. instrument --

    def _create_connect_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '1. Instrument')
        ttk.Label(frame, text="VISA address:").grid(row=0, column=0, sticky='w', padx=10, pady=4)
        self.ls_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.ls_cb.grid(row=0, column=1, columnspan=3, sticky='ew', padx=10, pady=4)
        btns = ttk.Frame(frame, style='Card.TFrame')
        btns.grid(row=1, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 4))
        btns.grid_columnconfigure((0, 1, 2), weight=1)
        self.scan_btn = ttk.Button(btns, text="Scan", command=self._scan_for_visa)
        self.scan_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.identify_btn = ttk.Button(btns, text="Identify", command=self._identify_visa)
        self.identify_btn.grid(row=0, column=1, sticky='ew', padx=4)
        self.connect_btn = ttk.Button(btns, text="Connect", command=self._toggle_connect)
        self.connect_btn.grid(row=0, column=2, sticky='ew', padx=4)
        self.lbl_conn = ttk.Label(frame, text="Not connected. Connect only reads; it changes nothing.",
                                  style='Muted.TLabel')
        self.lbl_conn.grid(row=2, column=0, columnspan=4, sticky='w', padx=10, pady=(0, 6))

    # -- 2. ramp --

    def _create_ramp_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '2. Ramp')
        self.entries = {}
        row1 = ttk.Frame(frame, style='Card.TFrame')
        row1.grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row1, text="Ramp to").pack(side='left')
        self.entries['target'] = self._inline_entry(row1, "310", 7)
        ttk.Label(row1, text="K at").pack(side='left')
        self.entries['rate'] = self._inline_entry(row1, "2", 6)
        ttk.Label(row1, text="K/min, controlling on input").pack(side='left')
        self.input_var = tk.StringVar(value='A')
        self.input_cb = ttk.Combobox(row1, textvariable=self.input_var,
                                     values=['A', 'B', 'C', 'D'], state='readonly', width=3)
        self.input_cb.pack(side='left', padx=4)

        row2 = ttk.Frame(frame, style='Card.TFrame')
        row2.grid(row=1, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row2, text="Done when within ±").pack(side='left')
        self.entries['tol'] = self._inline_entry(row2, "0.5", 5)
        ttk.Label(row2, text="K for").pack(side='left')
        self.entries['dwell'] = self._inline_entry(row2, "60", 5)
        ttk.Label(row2, text="s.  Log every").pack(side='left')
        self.entries['delay'] = self._inline_entry(row2, "1", 4)
        ttk.Label(row2, text="s.").pack(side='left')

        row3 = ttk.Frame(frame, style='Card.TFrame')
        row3.grid(row=2, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row3, text="When done:").pack(side='left')
        self.hold_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(row3, text="turn the heater off", variable=self.hold_var,
                        value=False).pack(side='left', padx=6)
        ttk.Radiobutton(row3, text="hold at the target", variable=self.hold_var,
                        value=True).pack(side='left', padx=6)

        row4 = ttk.Frame(frame, style='Card.TFrame')
        row4.grid(row=3, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row4, text="Safety: heater off and stop if T goes above").pack(side='left')
        self.entries['max_temp'] = self._inline_entry(row4, f"{HARD_TEMP_DEFAULT_K:g}", 6)
        ttk.Label(row4, text="K").pack(side='left')

        self.rate_cap_var = tk.BooleanVar(value=False)
        row5 = ttk.Frame(frame, style='Card.TFrame')
        row5.grid(row=4, column=0, columnspan=4, sticky='w', padx=10, pady=(2, 0))
        self.rate_cap_cb = ttk.Checkbutton(
            row5, variable=self.rate_cap_var, command=self._refresh_states,
            text="Limit the rate (below K: max K/min)")
        self.rate_cap_cb.pack(side='left')
        self.rate_table_entry = ttk.Entry(row5, width=24, font=self.FONT_TABLE)
        self.rate_table_entry.insert(0, RATE_TABLE_DEFAULT)
        self.rate_table_entry.pack(side='left', padx=4)
        self._note(frame, "Default table = the LN2-probe guideline (5 Jul 2026, the same as the "
                          "Step Frequency Scan windows);\nno CCR guideline exists yet. Off "
                          "unless ticked.", 5)
        self.lbl_rate_hint = self._note(frame, "", 6)
        for key in ('target', 'rate'):
            self.entries[key].bind('<KeyRelease>', lambda e: self._refresh_rate_hint())
        self.rate_table_entry.bind('<KeyRelease>', lambda e: self._refresh_rate_hint())

        btns = ttk.Frame(frame, style='Card.TFrame')
        btns.grid(row=7, column=0, columnspan=4, sticky='ew', padx=6, pady=(4, 8))
        btns.grid_columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(btns, text="Start Ramp", style='Start.TButton',
                                       command=self.start_ramp)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=4)
        self.stop_button = ttk.Button(btns, text="Stop (heater off)", style='Stop.TButton',
                                      command=self.stop_ramp)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=4)

    def _inline_entry(self, parent, default, width):
        e = ttk.Entry(parent, width=width, font=self.FONT_BASE, justify='center')
        e.insert(0, default)
        e.pack(side='left', padx=4)
        return e

    # -- 3. heater and PID --

    def _create_pid_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '3. Heater power and PID')
        sysf = ttk.Frame(frame, style='Card.TFrame')
        sysf.grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(6, 0))
        ttk.Label(sysf, text="Cryostat:").pack(side='left')
        self.system_var = tk.StringVar(value=SYSTEMS[DEFAULT_SYSTEM]['label'])
        self.system_cb = ttk.Combobox(sysf, textvariable=self.system_var, state='readonly',
                                      values=[s['label'] for s in SYSTEMS.values()], width=30)
        self.system_cb.pack(side='left', padx=6)
        self.system_cb.bind('<<ComboboxSelected>>', lambda e: self._on_system_changed())
        ttk.Label(sysf, text="sets the zone defaults, presets and safety maximum",
                  style='Muted.TLabel').pack(side='left')
        self.pid_mode_var = tk.StringVar(value='zones')
        ttk.Radiobutton(frame, text="One setting for the whole ramp", value='fixed',
                        variable=self.pid_mode_var, command=self._on_mode_changed).grid(
            row=1, column=0, columnspan=2, sticky='w', padx=10, pady=(4, 0))
        ttk.Radiobutton(frame, text="Zone table: change with the measured temperature",
                        value='zones', variable=self.pid_mode_var,
                        command=self._on_mode_changed).grid(
            row=1, column=2, columnspan=2, sticky='w', padx=10, pady=(4, 0))

        # ---- one setting ----
        fixed = ttk.LabelFrame(frame, text='One setting')
        fixed.grid(row=2, column=0, columnspan=4, sticky='ew', padx=8, pady=6)
        fixed.grid_columnconfigure(1, weight=1)
        ttk.Label(fixed, text="Heater output:").grid(row=0, column=0, sticky='w', padx=8, pady=4)
        self.fixed_heater_var = tk.StringVar(value=FIXED_HEATER_LABELS[1])
        self.fixed_heater_cb = ttk.Combobox(fixed, textvariable=self.fixed_heater_var,
                                            values=list(FIXED_HEATER_LABELS.values()),
                                            state='readonly', width=44)
        self.fixed_heater_cb.grid(row=0, column=1, columnspan=3, sticky='w', padx=8, pady=4)
        self.fixed_heater_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        self.out_widgets = {}
        for n in (1, 2):
            self.out_widgets[n] = self._create_output_block(fixed, n, row=1 + n)
        fb = ttk.Frame(fixed, style='Card.TFrame')
        fb.grid(row=4, column=0, columnspan=4, sticky='ew', padx=4, pady=(2, 6))
        fb.grid_columnconfigure((0, 1), weight=1)
        self.load_pid_btn = ttk.Button(fb, text="Copy present PID from instrument",
                                       command=self._load_present_pid)
        self.load_pid_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.apply_fixed_btn = ttk.Button(fb, text="Apply now (during a run)",
                                          command=self._apply_fixed_live)
        self.apply_fixed_btn.grid(row=0, column=1, sticky='ew', padx=4)
        self._note(fixed, "Manual output (MOUT) is a fixed heater % added on top of PID. "
                          "Leave 0 unless you know\nthe % that holds this temperature; "
                          "too much overshoots (the LN2 probe cannot cool back).",
                   row=5, pady=(0, 6))

        # ---- zone table ----
        zf = ttk.LabelFrame(frame, text='Zone table')
        zf.grid(row=3, column=0, columnspan=4, sticky='ew', padx=8, pady=6)
        zf.grid_columnconfigure(0, weight=1)
        self.zone_frame = zf
        self._note(zf, "Each row runs up to its 'Up to' temperature (the first from 0 K; the last "
                       "also covers anything\nabove). The switch happens when the MEASURED "
                       "temperature crosses a bound, no hysteresis.\nHeaters on: tick Out 1, "
                       "Out 2 (sample heater, 0.25 W into 25 ohm) or both, per zone.\nOne range lower = P x 3.16 (I the same) keeps the heater "
                       "power the same at the switch.\nA zone's range must heat past its own "
                       "'Up to' or the ramp stalls there.", row=0, pady=(4, 2))
        gen = ttk.Frame(zf, style='Card.TFrame')
        gen.grid(row=1, column=0, sticky='w', padx=10, pady=2)
        ttk.Label(gen, text="Make").pack(side='left')
        self.zone_segments_var = tk.StringVar(value=str(len(ZONE_DEFAULTS)))
        self.zone_seg_spin = ttk.Spinbox(gen, from_=1, to=10, width=3,
                                         textvariable=self.zone_segments_var)
        self.zone_seg_spin.pack(side='left', padx=4)
        ttk.Label(gen, text="equal zones from").pack(side='left')
        self.zone_tmin = self._inline_entry(gen, f"{ZONE_T_MIN:g}", 5)
        ttk.Label(gen, text="to").pack(side='left')
        self.zone_tmax = self._inline_entry(gen, f"{ZONE_T_MAX:g}", 5)
        ttk.Label(gen, text="K").pack(side='left')
        self.zone_gen_btn = ttk.Button(gen, text="Make", command=self._generate_zone_rows)
        self.zone_gen_btn.pack(side='left', padx=4)
        self.zone_reset_btn = ttk.Button(gen, text="Defaults", command=self._reset_zone_rows)
        self.zone_reset_btn.pack(side='left', padx=2)

        self.zone_table = ttk.Frame(zf, style='Card.TFrame')
        self.zone_table.grid(row=2, column=0, sticky='w', padx=8, pady=4)
        heads = [("Up to K", 0, 1), ("Heaters on", 1, 1), ("Output 1 (main)", 2, 5),
                 ("Output 2 (sample)", 7, 5)]
        for text, col, span in heads:
            ttk.Label(self.zone_table, text=text, style='Bold.TLabel').grid(
                row=0, column=col, columnspan=span, pady=(0, 1))
        for col, h in enumerate(["P", "I", "D", "MOUT%", "Range"] * 2, start=2):
            ttk.Label(self.zone_table, text=h, style='Muted.TLabel').grid(row=1, column=col)
        self.zone_rows = []
        for z in ZONE_DEFAULTS:
            self._add_zone_row(*z)

        zb = ttk.Frame(zf, style='Card.TFrame')
        zb.grid(row=3, column=0, sticky='ew', padx=4, pady=(2, 4))
        zb.grid_columnconfigure((0, 1, 2), weight=1)
        self.zone_add_btn = ttk.Button(zb, text="+ Add zone", command=lambda: self._add_zone_row(*self._system()['zones'][-1]))
        self.zone_add_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.zone_del_btn = ttk.Button(zb, text="- Remove last", command=self._remove_zone_row)
        self.zone_del_btn.grid(row=0, column=1, sticky='ew', padx=4)
        self.zone_apply_btn = ttk.Button(zb, text="Apply edits (during a run)",
                                         command=self._apply_zone_table)
        self.zone_apply_btn.grid(row=0, column=2, sticky='ew', padx=4)
        self.lbl_power_legend = self._note(zf, "", row=4, pady=(0, 2))
        self.lbl_zone_status = ttk.Label(zf, text="Active zone: --", style='Bold.TLabel',
                                         foreground=self.CLR_ACCENT_GOLD)
        self.lbl_zone_status.grid(row=5, column=0, sticky='w', padx=10, pady=(0, 6))

    def _create_output_block(self, parent, n, row):
        """Preset / P / I / D / MOUT / range widgets for one heater output."""
        w = {}
        name = "Output 1 (main)" if n == 1 else "Output 2 (sample)"
        box = ttk.Frame(parent, style='Card.TFrame')
        box.grid(row=row, column=0, columnspan=4, sticky='ew', padx=4, pady=3)
        w['title'] = ttk.Label(box, text=name, style='Bold.TLabel', width=16)
        w['title'].grid(row=0, column=0, sticky='w', padx=4)
        w['preset_var'] = tk.StringVar(value=PID_DEFAULT_PRESET)
        w['preset'] = ttk.Combobox(box, textvariable=w['preset_var'], state='readonly',
                                   values=list(PID_PRESETS.keys()) + [PID_CUSTOM], width=44)
        w['preset'].grid(row=0, column=1, columnspan=8, sticky='w', padx=4, pady=2)
        w['preset'].bind('<<ComboboxSelected>>', lambda e, k=n: self._on_preset(k))
        line = ttk.Frame(box, style='Card.TFrame')
        line.grid(row=1, column=1, columnspan=8, sticky='w', padx=4, pady=2)
        p, i, d = PID_PRESETS[PID_DEFAULT_PRESET]
        for key, label, val, width in (('p', 'P', p, 6), ('i', 'I', i, 6), ('d', 'D', d, 5),
                                       ('mout', 'MOUT %', 0.0, 5)):
            ttk.Label(line, text=label).pack(side='left', padx=(6, 2))
            e = ttk.Entry(line, width=width, font=self.FONT_TABLE, justify='center')
            e.insert(0, f"{val:g}")
            e.pack(side='left')
            w[key] = e
        ttk.Label(line, text="Range").pack(side='left', padx=(8, 2))
        w['range_var'] = tk.StringVar(value="5")
        w['range'] = ttk.Combobox(line, textvariable=w['range_var'], state='readonly', width=22)
        w['range'].pack(side='left')
        return w

    def _create_heater_setup_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '4. Heater setup (optional)')
        self.htr_edit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Let me change the Output 1 heater setup",
                        variable=self.htr_edit_var, command=self._refresh_states).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(4, 2))
        ttk.Label(frame, text="Output 1 heater resistance:").grid(row=1, column=0, sticky='w', padx=10, pady=3)
        self.htr_res_var = tk.StringVar(value="25 ohm")
        self.htr_res_cb = ttk.Combobox(frame, textvariable=self.htr_res_var, state='readonly',
                                       values=["25 ohm", "50 ohm"], width=10)
        self.htr_res_cb.grid(row=1, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(frame, text="Output 1 max current:").grid(row=2, column=0, sticky='w', padx=10, pady=3)
        self.htr_cur_var = tk.StringVar(value="1.732 A")
        self.htr_cur_cb = ttk.Combobox(frame, textvariable=self.htr_cur_var, state='readonly',
                                       values=["0.707 A", "1 A", "1.414 A", "1.732 A", "User"],
                                       width=10)
        self.htr_cur_cb.grid(row=2, column=1, sticky='w', padx=10, pady=3)
        ucf = ttk.Frame(frame, style='Card.TFrame')
        ucf.grid(row=2, column=2, columnspan=2, sticky='w')
        ttk.Label(ucf, text="user value").pack(side='left')
        self.htr_user_entry = ttk.Entry(ucf, width=6, font=self.FONT_TABLE, justify='center')
        self.htr_user_entry.insert(0, "1.0")
        self.htr_user_entry.pack(side='left', padx=4)
        ttk.Label(ucf, text="A").pack(side='left')
        ttk.Label(frame, text="Heater % means % of:").grid(row=3, column=0, sticky='w', padx=10, pady=3)
        self.htr_disp_var = tk.StringVar(value="Current")
        self.htr_disp_cb = ttk.Combobox(frame, textvariable=self.htr_disp_var, state='readonly',
                                        values=["Current", "Power"], width=10)
        self.htr_disp_cb.grid(row=3, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(frame, text="Output 2 heater resistance:").grid(row=4, column=0, sticky='w', padx=10, pady=3)
        o2 = ttk.Frame(frame, style='Card.TFrame')
        o2.grid(row=4, column=1, columnspan=3, sticky='w', padx=10)
        self.out2_res_entry = ttk.Entry(o2, width=7, font=self.FONT_TABLE, justify='center')
        self.out2_res_entry.insert(0, f"{OUT2_DEFAULT_RESISTANCE:g}")
        self.out2_res_entry.pack(side='left')
        ttk.Label(o2, text="ohm (only for the watt labels; not sent)",
                  style='Muted.TLabel').pack(side='left', padx=4)
        self.lbl_htr_preview = ttk.Label(frame, text="", style='Bold.TLabel')
        self.lbl_htr_preview.grid(row=5, column=0, columnspan=4, sticky='w', padx=10, pady=(4, 0))
        self._note(frame, "Output 1 limits (manual Table 4-14): 25 ohm up to 1.732 A = 75 W; "
                          "50 ohm up to 1 A = 50 W.\nUser value: 0.1 A up to that limit. "
                          "Output 2 is fixed at 100 mA / 10 V: 1 W into 100 ohm, 0.25 W into 25 ohm.", row=6)
        for wdg in (self.htr_res_cb, self.htr_cur_cb, self.htr_disp_cb):
            wdg.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        for wdg in (self.htr_user_entry, self.out2_res_entry):
            wdg.bind('<KeyRelease>', lambda e: self._refresh_states())
        hb = ttk.Frame(frame, style='Card.TFrame')
        hb.grid(row=7, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 8))
        hb.grid_columnconfigure((0, 1), weight=1)
        self.htr_read_btn = ttk.Button(hb, text="Read from instrument", command=self._read_heater_setup)
        self.htr_read_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.htr_send_btn = ttk.Button(hb, text="Send to instrument", command=self._send_heater_setup)
        self.htr_send_btn.grid(row=0, column=1, sticky='ew', padx=4)

    def _create_sensor_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '5. Sensor input and front display (optional)')
        self.sensor_edit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Let me change the sensor input and display settings",
                        variable=self.sensor_edit_var, command=self._refresh_states).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(4, 2))
        B = Lakeshore350_Backend
        r = 1
        ttk.Label(frame, text="Input:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_input_var = tk.StringVar(value='A')
        self.sens_input_cb = ttk.Combobox(frame, textvariable=self.sens_input_var, state='readonly',
                                          values=['A', 'B', 'C', 'D'], width=4)
        self.sens_input_cb.grid(row=r, column=1, sticky='w', padx=10, pady=3)
        self.sens_input_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        ttk.Label(frame, text="press Read input before Send",
                  style='Muted.TLabel').grid(row=r, column=2, columnspan=2, sticky='w')
        r += 1
        ttk.Label(frame, text="Sensor type:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_type_var = tk.StringVar(value=B.SENSOR_TYPES[1])
        self.sens_type_cb = ttk.Combobox(frame, textvariable=self.sens_type_var, state='readonly',
                                         values=list(B.SENSOR_TYPES.values()), width=28)
        self.sens_type_cb.grid(row=r, column=1, columnspan=3, sticky='w', padx=10, pady=3)
        self.sens_type_cb.bind('<<ComboboxSelected>>', lambda e: self._on_sensor_type())
        r += 1
        ttk.Label(frame, text="Curve:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_curve_var = tk.StringVar(value=CURVE_KEEP)
        self.sens_curve_cb = ttk.Combobox(frame, textvariable=self.sens_curve_var, state='readonly',
                                          values=[CURVE_KEEP], width=44)
        self.sens_curve_cb.grid(row=r, column=1, columnspan=3, sticky='w', padx=10, pady=3)
        r += 1
        cl = ttk.Frame(frame, style='Card.TFrame')
        cl.grid(row=r, column=1, columnspan=3, sticky='w', padx=10, pady=(0, 3))
        self.curve_list_btn = ttk.Button(cl, text="Read curve list",
                                         command=self._read_curve_list)
        self.curve_list_btn.pack(side='left')
        self.curve_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cl, text="show every curve, not only this type",
                        variable=self.curve_all_var,
                        command=self._refresh_curve_choices).pack(side='left', padx=8)
        r += 1
        self.lbl_curve_info = self._note(
            frame, "Read-only: the list is read with CRVHDR? queries; no curve is written or "
                   "deleted.\nSend only picks which stored curve the input uses (INCRV).", r)
        r += 1
        ttk.Label(frame, text="Autorange:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_auto_var = tk.StringVar(value="Off")
        self.sens_auto_cb = ttk.Combobox(frame, textvariable=self.sens_auto_var, state='readonly',
                                         values=["Off", "On"], width=6)
        self.sens_auto_cb.grid(row=r, column=1, sticky='w', padx=10, pady=3)
        self.sens_auto_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        r += 1
        ttk.Label(frame, text="Range:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_range_var = tk.StringVar(value=B.INPUT_RANGES[1][0])
        self.sens_range_cb = ttk.Combobox(frame, textvariable=self.sens_range_var, state='readonly',
                                          values=B.INPUT_RANGES[1], width=28)
        self.sens_range_cb.grid(row=r, column=1, columnspan=3, sticky='w', padx=10, pady=3)
        r += 1
        ttk.Label(frame, text="Current reversal:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_comp_var = tk.StringVar(value="Off")
        self.sens_comp_cb = ttk.Combobox(frame, textvariable=self.sens_comp_var, state='readonly',
                                         values=["Off", "On"], width=6)
        self.sens_comp_cb.grid(row=r, column=1, sticky='w', padx=10, pady=3)
        r += 1
        ttk.Label(frame, text="Excitation (NTC only):").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_exc_var = tk.StringVar(value=B.EXCITATION[1])
        self.sens_exc_cb = ttk.Combobox(frame, textvariable=self.sens_exc_var, state='readonly',
                                        values=list(B.EXCITATION.values()), width=8)
        self.sens_exc_cb.grid(row=r, column=1, sticky='w', padx=10, pady=3)
        r += 1
        ttk.Label(frame, text="Preferred units:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.sens_units_var = tk.StringVar(value=B.UNITS[1])
        self.sens_units_cb = ttk.Combobox(frame, textvariable=self.sens_units_var, state='readonly',
                                          values=list(B.UNITS.values()), width=14)
        self.sens_units_cb.grid(row=r, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(frame, text="keep Kelvin: setpoints use these units",
                  style='Muted.TLabel').grid(row=r, column=2, columnspan=2, sticky='w')
        r += 1
        sb = ttk.Frame(frame, style='Card.TFrame')
        sb.grid(row=r, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 6))
        sb.grid_columnconfigure((0, 1), weight=1)
        self.sens_read_btn = ttk.Button(sb, text="Read input", command=self._read_sensor)
        self.sens_read_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.sens_send_btn = ttk.Button(sb, text="Send input", command=self._send_sensor)
        self.sens_send_btn.grid(row=0, column=1, sticky='ew', padx=4)
        r += 1
        ttk.Separator(frame).grid(row=r, column=0, columnspan=4, sticky='ew', padx=10, pady=4)
        r += 1
        ttk.Label(frame, text="Display shows:").grid(row=r, column=0, sticky='w', padx=10, pady=3)
        self.disp_mode_var = tk.StringVar(value=B.DISPLAY_MODES[0])
        self.disp_mode_cb = ttk.Combobox(frame, textvariable=self.disp_mode_var, state='readonly',
                                         values=list(B.DISPLAY_MODES.values()), width=14)
        self.disp_mode_cb.grid(row=r, column=1, sticky='w', padx=10, pady=3)
        self.disp_mode_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        r += 1
        cf = ttk.Frame(frame, style='Card.TFrame')
        cf.grid(row=r, column=0, columnspan=4, sticky='w', padx=10, pady=3)
        ttk.Label(cf, text="Custom:").pack(side='left')
        self.disp_fields_var = tk.StringVar(value=B.CUSTOM_FIELDS[0])
        self.disp_fields_cb = ttk.Combobox(cf, textvariable=self.disp_fields_var, state='readonly',
                                           values=list(B.CUSTOM_FIELDS.values()), width=8)
        self.disp_fields_cb.pack(side='left', padx=4)
        ttk.Label(cf, text="fields, loop of output").pack(side='left')
        self.disp_out_var = tk.StringVar(value="1")
        self.disp_out_cb = ttk.Combobox(cf, textvariable=self.disp_out_var, state='readonly',
                                        values=["1", "2", "3", "4"], width=3)
        self.disp_out_cb.pack(side='left', padx=4)
        r += 1
        ff = ttk.Frame(frame, style='Card.TFrame')
        ff.grid(row=r, column=0, columnspan=4, sticky='w', padx=10, pady=3)
        ttk.Label(ff, text="Field").pack(side='left')
        self.disp_field_var = tk.StringVar(value="1")
        self.disp_field_cb = ttk.Combobox(ff, textvariable=self.disp_field_var, state='readonly',
                                          values=[str(k) for k in range(1, 9)], width=3)
        self.disp_field_cb.pack(side='left', padx=4)
        # Another field must be read before it can be sent.
        self.disp_field_cb.bind('<<ComboboxSelected>>', lambda e: self._display_field_changed())
        ttk.Label(ff, text="shows").pack(side='left')
        self.disp_fin_var = tk.StringVar(value="Input A")
        self.disp_fin_cb = ttk.Combobox(ff, textvariable=self.disp_fin_var, state='readonly',
                                        values=["None", "Input A", "Input B", "Input C", "Input D"],
                                        width=8)
        self.disp_fin_cb.pack(side='left', padx=4)
        ttk.Label(ff, text="in").pack(side='left')
        self.disp_funits_var = tk.StringVar(value=B.FIELD_UNITS[1])
        self.disp_funits_cb = ttk.Combobox(ff, textvariable=self.disp_funits_var, state='readonly',
                                           values=list(B.FIELD_UNITS.values()), width=12)
        self.disp_funits_cb.pack(side='left', padx=4)
        r += 1
        db = ttk.Frame(frame, style='Card.TFrame')
        db.grid(row=r, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 8))
        db.grid_columnconfigure((0, 1), weight=1)
        self.disp_read_btn = ttk.Button(db, text="Read display", command=self._read_display)
        self.disp_read_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.disp_send_btn = ttk.Button(db, text="Send display", command=self._send_display)
        self.disp_send_btn.grid(row=0, column=1, sticky='ew', padx=4)

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(2, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        status_frame = ttk.Frame(panel)
        status_frame.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        status_frame.grid_columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(status_frame, text="READY: CONNECT OR START",
                                   font=('Segoe UI', 15, 'bold'), bg=self.CLR_FRAME_BG,
                                   fg=self.CLR_TEXT_DARK, pady=10)
        self.lbl_status.grid(row=0, column=0, sticky='ew')
        self.lbl_current_temp = tk.Label(status_frame, text="--- K", font=('Segoe UI', 26, 'bold'),
                                         bg=self.CLR_FRAME_BG, fg=self.CLR_ACCENT_RED, padx=20)
        self.lbl_current_temp.grid(row=0, column=1, sticky='e', padx=(10, 0))

        strip = tk.Frame(panel, bg=self.CLR_FRAME_BG)
        strip.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        self.present = {}
        for col, key in enumerate(("Setpoint", "Output 1", "Output 2", "PID", "Zone")):
            strip.grid_columnconfigure(col, weight=1)
            tk.Label(strip, text=key, bg=self.CLR_FRAME_BG, fg=self.CLR_MUTED,
                     font=self.FONT_SMALL).grid(row=0, column=col, sticky='w', padx=8, pady=(4, 0))
            lbl = tk.Label(strip, text="--", bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK,
                           font=('Segoe UI', 10, 'bold'), justify='left', anchor='w')
            lbl.grid(row=1, column=col, sticky='w', padx=8, pady=(0, 4))
            self.present[key] = lbl

        container = ttk.LabelFrame(panel, text='Live Temperature')
        container.grid(row=2, column=0, sticky='nsew')
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.figure.add_subplot(211)
        self.ax_heater = self.figure.add_subplot(212, sharex=self.ax_temp)
        self.line_setp = self.ax_temp.plot([], [], color=self.CLR_ACCENT_GREEN,
                                           linestyle='--', label='Setpoint')[0]
        self.line_temp = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker='o',
                                           markersize=3, linestyle='-', label='Temperature')[0]
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, linestyle='--', alpha=0.6)
        self.ax_temp.legend(loc='best', frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis='x', which='both', bottom=False, top=False,
                                 labelbottom=False)
        self.line_heater1 = self.ax_heater.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.',
                                                markersize=3, linestyle='-', label='Output 1')[0]
        self.line_heater2 = self.ax_heater.plot([], [], color='#5E7FBA', marker='.',
                                                markersize=3, linestyle='-', label='Output 2')[0]
        self.ax_heater.set_xlabel("Time (s)")
        self.ax_heater.set_ylabel("Heater Output (%)")
        self.ax_heater.grid(True, linestyle='--', alpha=0.6)
        self.ax_heater.legend(loc='best', frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

        cframe = ttk.LabelFrame(panel, text='Console')
        cframe.grid(row=3, column=0, sticky='nsew', pady=(6, 0))
        cframe.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            cframe, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', borderwidth=0, height=9)
        self.console.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.log("Console initialized. Connect to read the instrument, or set the ramp and Start.")

    # ------------------------------------------------------- enable / disable

    @staticmethod
    def _set_state(widget, enabled, readonly_when_on=False):
        try:
            if enabled:
                widget.config(state='readonly' if readonly_when_on else 'normal')
            else:
                widget.config(state='disabled')
        except tk.TclError:
            pass

    def _fixed_heater_code(self):
        label = self.fixed_heater_var.get()
        return next((k for k, v in FIXED_HEATER_LABELS.items() if v == label), 1)

    def _refresh_states(self):
        """The one place that greys widgets out, so the rules stay together:
        a widget is live only when what it changes can take effect."""
        if not self._ui_ready:
            return
        connected = self.backend.is_connected
        running = self.is_running
        fixed = self.pid_mode_var.get() == 'fixed'
        idle = not running

        # 1. instrument
        self._set_state(self.ls_cb, idle and not connected, readonly_when_on=True)
        for b in (self.scan_btn, self.identify_btn):
            self._set_state(b, idle and not connected)
        self._set_state(self.connect_btn, idle)
        self.connect_btn.config(text="Disconnect" if connected else "Connect")

        # 2. ramp: fixed while running
        for e in self.entries.values():
            self._set_state(e, idle)
        self._set_state(self.input_cb, idle, readonly_when_on=True)
        self._set_state(self.rate_cap_cb, idle)
        self._set_state(self.rate_table_entry, idle and self.rate_cap_var.get())
        self._set_state(self.start_button, idle)
        self._set_state(self.stop_button, running)

        # 3. heater and PID
        self._set_state(self.system_cb, idle, readonly_when_on=True)
        used = HEATER_OUTPUTS[self._fixed_heater_code()]
        self._set_state(self.fixed_heater_cb, fixed and idle, readonly_when_on=True)
        for n, w in self.out_widgets.items():
            on = fixed and (n in used) and (not running or n in self.run_outputs)
            custom = w['preset_var'].get() == PID_CUSTOM
            self._set_state(w['preset'], on, readonly_when_on=True)
            for key in ('p', 'i', 'd'):
                self._set_state(w[key], on and custom)
            self._set_state(w['mout'], on)
            self._set_state(w['range'], on, readonly_when_on=True)
            w['title'].config(foreground=self.CLR_FG_LIGHT if on else self.CLR_MUTED)
        self._set_state(self.load_pid_btn, fixed and connected)
        self._set_state(self.apply_fixed_btn, fixed and running)
        for b in (self.zone_seg_spin, self.zone_tmin, self.zone_tmax, self.zone_gen_btn,
                  self.zone_reset_btn, self.zone_add_btn, self.zone_del_btn):
            self._set_state(b, not fixed and idle)
        self._set_state(self.zone_apply_btn, not fixed and running)
        for row in self.zone_rows:
            self._refresh_zone_row(row, enabled=not fixed)

        # 4. heater setup
        edit_htr = self.htr_edit_var.get() and idle
        self._set_state(self.htr_res_cb, edit_htr, readonly_when_on=True)
        self._set_state(self.htr_cur_cb, edit_htr, readonly_when_on=True)
        self._set_state(self.htr_user_entry, edit_htr and self.htr_cur_var.get() == "User")
        self._set_state(self.htr_disp_cb, edit_htr, readonly_when_on=True)
        self._set_state(self.htr_read_btn, connected and idle)
        self._set_state(self.htr_send_btn, connected and edit_htr and self.heater_cfg['known'])
        self._refresh_heater_preview()

        # 5. sensor and display (never while the loop is ramping)
        edit_s = self.sensor_edit_var.get() and idle
        stype = self._sensor_type_code()
        auto_on = self.sens_auto_var.get() == "On" and stype in (2, 3)
        self._set_state(self.sens_input_cb, edit_s, readonly_when_on=True)
        self._set_state(self.sens_type_cb, edit_s, readonly_when_on=True)
        self._set_state(self.sens_auto_cb, edit_s and stype in (2, 3), readonly_when_on=True)
        self._set_state(self.sens_range_cb, edit_s and stype not in (0, 4) and not auto_on,
                        readonly_when_on=True)
        self._set_state(self.sens_comp_cb, edit_s and stype in (2, 3, 4, 5), readonly_when_on=True)
        self._set_state(self.sens_exc_cb, edit_s and stype == 3, readonly_when_on=True)
        self._set_state(self.sens_units_cb, edit_s and stype != 0, readonly_when_on=True)
        self._set_state(self.sens_curve_cb, edit_s and stype != 0, readonly_when_on=True)
        self._set_state(self.curve_list_btn, connected and idle)
        self._set_state(self.sens_read_btn, connected and idle)
        self._set_state(self.sens_send_btn, connected and edit_s
                        and self.sensor_read_input == self.sens_input_var.get())
        custom_disp = self.disp_mode_var.get() == "Custom"
        self._set_state(self.disp_mode_cb, edit_s, readonly_when_on=True)
        for wdg in (self.disp_fields_cb, self.disp_out_cb, self.disp_field_cb,
                    self.disp_fin_cb, self.disp_funits_cb):
            self._set_state(wdg, edit_s and custom_disp, readonly_when_on=True)
        self._set_state(self.disp_read_btn, connected and idle)
        self._set_state(self.disp_send_btn, connected and edit_s and self.display_read_ok)

        self._refresh_rate_hint()

    def _on_mode_changed(self):
        """Switching mode during a run: zones take over from the next sample
        (only if they use the outputs this run set up); one setting keeps
        the present PID/range until 'Apply now'."""
        if self.is_running:
            if self.pid_mode_var.get() == 'zones':
                if self._apply_zone_table():
                    self.log("Zones on: PID/range follow the reading from the next sample.")
                else:
                    self.pid_mode_var.set('fixed')
                    self.log("Staying with one setting.")
            else:
                self.log("Zones off: PID/range stay as they are until you press Apply now.")
        self._refresh_states()

    def _refresh_zone_row(self, row, enabled):
        heater = self._zone_heater_code(row)
        self._set_state(row['ub'], enabled)
        for cb in row['heater'].values():
            self._set_state(cb, enabled and not self.is_running)
        for n in (1, 2):
            on = enabled and n in HEATER_OUTPUTS[heater]
            for key in ('p', 'i', 'd', 'mout'):
                self._set_state(row[n][key], on)
            self._set_state(row[n]['range'], on, readonly_when_on=True)

    def _refresh_rate_hint(self):
        try:
            target = float(self.entries['target'].get())
            rate = float(self.entries['rate'].get())
        except (ValueError, tk.TclError):
            self.lbl_rate_hint.config(text="")
            return
        try:
            caps, default = parse_rate_table(self.rate_table_entry.get())
        except ValueError as e:
            self.lbl_rate_hint.config(text=f"Rate table: {e}" if self.rate_cap_var.get() else "")
            return
        coldest = target
        try:
            coldest = min(target, float(self.lbl_current_temp.cget('text').split()[0]))
        except (ValueError, IndexError):
            pass
        cap = rate_cap_for(coldest, caps, default)
        text = ""
        if rate > cap:
            if self.rate_cap_var.get():
                text = f"The rate will be limited to {cap:g} K/min (coldest point {coldest:.0f} K)."
            elif self._system()['ln2_checks']:
                text = (f"Note: the table gives {cap:g} K/min at {coldest:.0f} K; "
                        "tick the box above to enforce it.")
        self.lbl_rate_hint.config(text=text)

    # ----------------------------------------------------- watt labels

    def _out_full_power(self, n):
        if n == 1:
            c = self.heater_cfg
            return out1_full_power(c['resistance'], c['max_current'], c['user_current'])
        return out2_full_power(self.out2_resistance)

    def _refresh_range_labels(self):
        if not self._ui_ready:
            return
        """Rebuild every range box so it shows watts for the known setup."""
        values = {n: [range_label(k, self._out_full_power(n)) for k in (5, 4, 3, 2, 1, 0)]
                  for n in (1, 2)}
        for n, w in self.out_widgets.items():
            code = self._safe_range(w['range_var'].get(), 5)
            w['range']['values'] = values[n][:-1]       # no "Off" for a ramp
            w['range_var'].set(range_label(code, self._out_full_power(n)))
        for row in self.zone_rows:
            for n in (1, 2):
                code = self._safe_range(row[n]['range_var'].get(), 5)
                row[n]['range']['values'] = [str(k) for k in (5, 4, 3, 2, 1, 0)]
                row[n]['range_var'].set(str(code))
        legend = []
        for n in (1, 2):
            full = self._out_full_power(n)
            legend.append(f"Output {n}: " + ", ".join(
                f"{k} = {format_power(range_power(k, full))}" for k in (5, 4, 3, 2, 1)))
        src = "as read from the instrument" if self.heater_cfg['known'] else \
            "nominal (25 ohm, 1.732 A) until read from the instrument"
        self.lbl_power_legend.config(text="Range power, " + src + ":\n" + "\n".join(legend))

    @staticmethod
    def _safe_range(label, default):
        try:
            return parse_range(label)
        except (ValueError, IndexError):
            return default

    def _heater_form(self):
        """(resistance code, max current code, user current, display code) from the form."""
        res = 1 if self.htr_res_var.get().startswith("25") else 2
        cur_map = {"0.707 A": 1, "1 A": 2, "1.414 A": 3, "1.732 A": 4, "User": 0}
        cur = cur_map.get(self.htr_cur_var.get(), 4)
        user = float(self.htr_user_entry.get()) if cur == 0 else 0.0
        disp = 2 if self.htr_disp_var.get() == "Power" else 1
        return res, cur, user, disp

    def _refresh_heater_preview(self):
        try:
            r2 = max(1.0, float(self.out2_res_entry.get()))
            if r2 != self.out2_resistance:
                self.out2_resistance = r2
                self._refresh_range_labels()
        except ValueError:
            pass
        try:
            res, cur, user, _disp = self._heater_form()
            p1 = out1_full_power(res, cur, user)
            text = (f"This setting gives Output 1 up to {format_power(p1)} on range 5; "
                    f"Output 2 up to {format_power(out2_full_power(self.out2_resistance))}.")
        except (ValueError, KeyError):
            text = "Type a number for the user current."
        self.lbl_htr_preview.config(text=text)

    # ----------------------------------------------------- zone table

    def _zone_heater_code(self, row):
        """1 = Output 1, 2 = Output 2, 3 = both, from the row's tick boxes."""
        return (1 if row['use'][1].get() else 0) + (2 if row['use'][2].get() else 0) or 1

    def _set_zone_heater(self, row, code):
        """Tick the row's boxes for heater code 1, 2 or 3."""
        code = int(code)
        row['use'][1].set(code in (1, 3))
        row['use'][2].set(code in (2, 3))
        self._refresh_states()

    def _on_zone_heater_toggled(self, row, n):
        """A zone needs at least one heater: unticking the last one is undone."""
        if not (row['use'][1].get() or row['use'][2].get()):
            row['use'][n].set(True)
            self.log("A zone needs at least one heater output ticked.")
        self._refresh_states()

    def _add_zone_row(self, upper=ZONE_T_MAX, heater=1, out1=PID_CCR1_HIGH + (0.0, 5),
                      out2=PID_CCR1_HIGH + (0.0, 5)):
        r = len(self.zone_rows) + 2
        widgets = []
        ub = ttk.Entry(self.zone_table, width=6, font=self.FONT_TABLE, justify='center')
        ub.insert(0, f"{upper:g}")
        ub.grid(row=r, column=0, padx=2, pady=1)
        widgets.append(ub)
        # Which heaters this zone uses: one tick box per output.
        hf = ttk.Frame(self.zone_table, style='Card.TFrame')
        hf.grid(row=r, column=1, padx=2, pady=1)
        widgets.append(hf)
        row = {'ub': ub, 'use': {}, 'heater': {}, 'widgets': widgets}
        for n in (1, 2):
            var = tk.BooleanVar(value=n in HEATER_OUTPUTS[int(heater)])
            cb = ttk.Checkbutton(hf, text=f"Out {n}", variable=var,
                                 command=lambda rw=row, k=n: self._on_zone_heater_toggled(rw, k))
            cb.pack(side='left', padx=(0, 2))
            row['use'][n] = var
            row['heater'][n] = cb
        for n, vals, col0 in ((1, out1, 2), (2, out2, 7)):
            cells = {}
            for k, (key, val, width) in enumerate((('p', vals[0], 5), ('i', vals[1], 5),
                                                   ('d', vals[2], 4), ('mout', vals[3], 5))):
                e = ttk.Entry(self.zone_table, width=width, font=self.FONT_TABLE, justify='center')
                e.insert(0, f"{val:g}")
                e.grid(row=r, column=col0 + k, padx=1, pady=1)
                widgets.append(e)
                cells[key] = e
            rv = tk.StringVar(value=str(int(vals[4])))
            rcb = ttk.Combobox(self.zone_table, textvariable=rv, state='readonly', width=3,
                               values=['5', '4', '3', '2', '1', '0'])
            rcb.grid(row=r, column=col0 + 4, padx=(1, 6), pady=1)
            widgets.append(rcb)
            cells['range_var'] = rv
            cells['range'] = rcb
            row[n] = cells
        self.zone_rows.append(row)
        if self._ui_ready:
            self._refresh_states()

    def _remove_zone_row(self):
        if len(self.zone_rows) <= 1:
            self.log("At least one zone is required.")
            return
        for w in self.zone_rows.pop()['widgets']:
            w.destroy()

    def _clear_zone_rows(self):
        while self.zone_rows:
            for w in self.zone_rows.pop()['widgets']:
                w.destroy()

    def _system(self):
        return SYSTEMS[self.system]

    def _reset_zone_rows(self, quiet=False):
        zones = self._system()['zones']
        self._clear_zone_rows()
        for z in zones:
            self._add_zone_row(*z)
        self.zone_segments_var.set(str(len(zones)))
        if not quiet:
            self.log(f"Zone table reset to the {self._system()['zone_name']} defaults.")

    def _on_system_changed(self):
        """Cryostat choice: zone defaults, presets, zone range and safety max."""
        label = self.system_var.get()
        key = next((k for k, s in SYSTEMS.items() if s['label'] == label), DEFAULT_SYSTEM)
        if key == self.system or self.is_running:
            self.system_var.set(self._system()['label'])
            return
        old, self.system = self._system(), key
        new = self._system()
        self._reset_zone_rows(quiet=True)
        for entry, value in ((self.zone_tmin, new['t_min']), (self.zone_tmax, new['t_max'])):
            entry.delete(0, 'end')
            entry.insert(0, f"{value:g}")
        if self.entries['max_temp'].get().strip() == f"{old['max_temp']:g}":
            self.entries['max_temp'].delete(0, 'end')
            self.entries['max_temp'].insert(0, f"{new['max_temp']:g}")
        for n in self.out_widgets:
            if self.out_widgets[n]['preset_var'].get() != PID_CUSTOM:
                self.out_widgets[n]['preset_var'].set(new['preset'])
                self._on_preset(n)
        self.log(f"Cryostat: {new['label']}. Zone table = {new['zone_name']} defaults, "
                 f"safety maximum {self.entries['max_temp'].get()} K"
                 + (", below-100 K wind-up warning on." if new['ln2_checks'] else "."))
        self._refresh_states()

    def _generate_zone_rows(self):
        try:
            n = int(self.zone_segments_var.get())
            t_min, t_max = float(self.zone_tmin.get()), float(self.zone_tmax.get())
            zones = generate_equal_zones(n, t_min, t_max, template=self._system()['zones'])
        except (ValueError, tk.TclError) as e:
            self.log(f"ERROR: cannot make zones: {e}")
            return
        self._clear_zone_rows()
        for z in zones:
            self._add_zone_row(*z)
        self.log(f"Made {n} equal zone(s) from {t_min:g} to {t_max:g} K, "
                 "each with the default settings for its temperature.")

    def _collect_zones(self):
        """Rows as (ub, heater, out1, out2), sorted; raises ValueError."""
        zones = []
        for row in self.zone_rows:
            ub = float(row['ub'].get())
            heater = self._zone_heater_code(row)
            outs = {}
            for n in (1, 2):
                c = row[n]
                vals = (float(c['p'].get()), float(c['i'].get()), float(c['d'].get()),
                        float(c['mout'].get()), int(c['range_var'].get()))
                if n in HEATER_OUTPUTS[heater]:
                    try:
                        validate_pid(*vals[:3])
                        validate_mout(vals[3])
                    except ValueError as e:
                        raise ValueError(f"zone up to {ub:g} K, Output {n}: {e}")
                outs[n] = vals
            zones.append((ub, heater, outs[1], outs[2]))
        if not zones:
            raise ValueError("the zone table is empty")
        zones.sort(key=lambda z: z[0])
        bounds = [z[0] for z in zones]
        if len(set(bounds)) != len(bounds):
            raise ValueError("two zones share the same 'Up to' temperature")
        return zones

    @staticmethod
    def _zone_text(zone):
        ub, heater, o1, o2 = zone
        parts = []
        for n, o in ((1, o1), (2, o2)):
            if n in HEATER_OUTPUTS[heater]:
                parts.append(f"Out{n} P{o[0]:g}/I{o[1]:g}/D{o[2]:g} MOUT{o[3]:g}% R{o[4]}")
        return " + ".join(parts)

    def _apply_zone_table(self):
        """Take the edited table into use.  Returns False (and keeps the old
        table) if it is invalid or needs an output this run did not set up."""
        try:
            zones = self._collect_zones()
        except ValueError as e:
            self.log(f"ERROR in zone table: {e}")
            return False
        needed = {n for z in zones for n in HEATER_OUTPUTS[z[1]]}
        if self.is_running and not needed <= set(self.run_outputs):
            self.log("ERROR: the edited table uses an output this run did not set up "
                     f"(set up: {list(self.run_outputs)}). Stop and Start again.")
            return False
        self.zones = zones
        self.active_zone = None   # re-evaluate on the next sample
        self.log("Zone table applied: " + "; ".join(
            f"<= {z[0]:g} K: {self._zone_text(z)}" for z in zones))
        return True

    def _send_output_settings(self, n, p, i, d, mout, rng):
        """PID, MOUT and range for one output.  The write that LOWERS the
        heater power goes first: going down a range, RANGE before the
        (higher) P; going up, the (lower) P before RANGE.  The in-between
        state is then a short dip, never P x 3.16 on a 10x range."""
        try:
            old = self.backend.get_heater_range(n)
        except Exception:
            old = int(rng)
        if int(rng) < old:
            self.backend.set_heater_range(n, rng)
            self.backend.set_pid(n, p, i, d)
            self.backend.set_mout(n, mout)
        else:
            self.backend.set_pid(n, p, i, d)
            self.backend.set_mout(n, mout)
            self.backend.set_heater_range(n, rng)

    def _apply_zone_for_temperature(self, temp, force=False):
        if self.pid_mode_var.get() != 'zones' or not self.zones:
            return
        idx = select_zone(self.zones, temp)
        if idx == self.active_zone and not force:
            return
        ub, heater, o1, o2 = self.zones[idx]
        active = HEATER_OUTPUTS[heater]
        lo = self.zones[idx - 1][0] if idx > 0 else 0.0
        for n in self.run_outputs:          # outputs this zone does not use: off
            if n not in active:
                self.backend.set_heater_range(n, 0)
                self.backend.set_mout(n, 0)
        for n in active:
            self._send_output_settings(n, *(o1 if n == 1 else o2))
        self.active_zone = idx
        text = self._zone_text(self.zones[idx])
        self.lbl_zone_status.config(text=f"Active zone {idx + 1} ({lo:g}-{ub:g} K): {text}")
        self.log(f"ZONE {idx + 1} ({lo:g}-{ub:g} K) at T={temp:.3f} K: {text}")
        self._csv_note(f"zone {idx + 1}: {text}")

    # ----------------------------------------------------- fixed PID

    def _on_preset(self, n):
        w = self.out_widgets[n]
        name = w['preset_var'].get()
        if name in PID_PRESETS:
            for key, val in zip(('p', 'i', 'd'), PID_PRESETS[name]):
                w[key].config(state='normal')
                w[key].delete(0, 'end')
                w[key].insert(0, f"{val:g}")
        self._refresh_states()

    def _fixed_settings(self):
        """{output: (P, I, D, MOUT, range)} for the outputs the fixed setting uses."""
        out = {}
        for n in HEATER_OUTPUTS[self._fixed_heater_code()]:
            w = self.out_widgets[n]
            try:
                vals = (float(w['p'].get()), float(w['i'].get()), float(w['d'].get()),
                        float(w['mout'].get()), parse_range(w['range_var'].get()))
            except (ValueError, IndexError):
                raise ValueError(f"Output {n}: P, I, D, MOUT must be numbers")
            validate_pid(*vals[:3])
            validate_mout(vals[3])
            if vals[4] == 0:
                raise ValueError(f"Output {n}: range 0 (Off) cannot ramp; pick 1-5.")
            out[n] = vals
        return out

    def _load_present_pid(self):
        if not self.backend.is_connected:
            self.log("Connect first to read the present PID.")
            return
        try:
            for n in HEATER_OUTPUTS[self._fixed_heater_code()]:
                p, i, d = self.backend.get_pid(n)
                mout = self.backend.get_mout(n)
                rng = self.backend.get_heater_range(n)
                w = self.out_widgets[n]
                w['preset_var'].set(PID_CUSTOM)
                for key, val in (('p', p), ('i', i), ('d', d), ('mout', mout)):
                    w[key].config(state='normal')
                    w[key].delete(0, 'end')
                    w[key].insert(0, f"{val:g}")
                if rng > 0:
                    w['range_var'].set(range_label(rng, self._out_full_power(n)))
                self.log(f"Output {n} present: PID {p:g}/{i:g}/{d:g}, MOUT {mout:g} %, "
                         f"range {rng}. Copied into the form as Custom; edit and Start or Apply.")
        except Exception as e:
            self.log(f"ERROR reading the present PID: {e}")
        self._refresh_states()

    def _apply_fixed_live(self):
        if not self.is_running:
            return
        try:
            settings = self._fixed_settings()
            extra = sorted(set(settings) - set(self.run_outputs))
            if extra:
                self.log(f"Not applied: Output(s) {extra} were not set up by this run "
                         f"(run uses {list(self.run_outputs)}). Stop and Start to use them.")
                return
            for n in self.run_outputs:
                if n not in settings:
                    self.backend.set_heater_range(n, 0)
                    self.log(f"Applied: Output {n} off (RANGE {n},0); the one setting does "
                             "not use it.")
            for n, vals in settings.items():
                self._send_output_settings(n, *vals)
                self.log(f"Applied Output {n}: PID {vals[0]:g}/{vals[1]:g}/{vals[2]:g}, "
                         f"MOUT {vals[3]:g} %, range {vals[4]}")
                self._csv_note(f"manual out{n} P{vals[0]:g}/I{vals[1]:g}/D{vals[2]:g} "
                               f"MOUT{vals[3]:g} R{vals[4]}")
        except Exception as e:
            self.log(f"ERROR applying: {e}")

    # ----------------------------------------------------- heater setup

    def _apply_heater_cfg(self, cfg):
        self.heater_cfg = dict(cfg, known=True)
        self.htr_res_var.set("25 ohm" if cfg['resistance'] == 1 else "50 ohm")
        names = {1: "0.707 A", 2: "1 A", 3: "1.414 A", 4: "1.732 A", 0: "User"}
        self.htr_cur_var.set(names.get(cfg['max_current'], "1.732 A"))
        self.htr_user_entry.config(state='normal')
        self.htr_user_entry.delete(0, 'end')
        self.htr_user_entry.insert(0, f"{cfg['user_current']:g}")
        self.htr_disp_var.set("Power" if cfg['display'] == 2 else "Current")
        self._refresh_range_labels()
        self._refresh_states()

    def _read_heater_setup(self, quiet=False):
        try:
            cfg = self.backend.get_heater_setup(1)
            cfg['display2'] = self.backend.get_heater_setup(2)['display']
            self._apply_heater_cfg(cfg)
            if not quiet:
                self.log(f"HTRSET? 1 -> {cfg}: Output 1 up to "
                         f"{format_power(self._out_full_power(1))} on range 5.")
        except Exception as e:
            self.log(f"ERROR reading the heater setup: {e}")

    def _send_heater_setup(self):
        try:
            res, cur, user, disp = self._heater_form()
            p1 = format_power(out1_full_power(res, cur, user))
        except (ValueError, KeyError) as e:
            self.log(f"ERROR: heater setup: {e}")
            return
        if not self._ask("Send heater setup",
                         f"Set Output 1 to {OUT1_RESISTANCE[res]:g} ohm, max current "
                         f"{self.htr_cur_var.get()}{f' ({user:g} A)' if cur == 0 else ''}?\n\n"
                         f"Range 5 will then give up to {p1}. Use the resistance of the heater "
                         "that is really wired, or the heater can be overdriven."):
            return
        try:
            cmd = self.backend.set_heater_setup(res, cur, user, disp)
            self.log(f"SENT: {cmd}")
            time.sleep(0.2)
            self._read_heater_setup()
        except Exception as e:
            self.log(f"ERROR sending the heater setup: {e}")

    # ----------------------------------------------------- sensor / display

    def _sensor_type_code(self):
        label = self.sens_type_var.get()
        return next((k for k, v in Lakeshore350_Backend.SENSOR_TYPES.items() if v == label), 0)

    def _on_sensor_type(self):
        ranges = Lakeshore350_Backend.INPUT_RANGES[self._sensor_type_code()]
        self.sens_range_cb['values'] = ranges
        if self.sens_range_var.get() not in ranges:
            self.sens_range_var.set(ranges[0])
        self._refresh_curve_choices()
        self._refresh_states()

    def _curve_choice_label(self, number):
        number = int(number)
        if number == 0:
            return CURVE_NONE
        if number in self.curve_headers:
            return curve_label(number, self.curve_headers[number])
        return f"{number:02d} - (press Read curve list for its name)"

    def _refresh_curve_choices(self):
        """Curves that suit the chosen sensor type (all with the checkbox),
        from the list read with CRVHDR?.  The selection is kept if it is
        still offered."""
        stype = self._sensor_type_code()
        shown = [n for n, h in sorted(self.curve_headers.items())
                 if self.curve_all_var.get() or curve_matches(stype, h)]
        values = [CURVE_KEEP, CURVE_NONE] + [self._curve_choice_label(n) for n in shown]
        current = self.sens_curve_var.get()
        if current not in (CURVE_KEEP, CURVE_NONE):
            try:                             # refresh its name once the list is read
                current = self._curve_choice_label(int(current.split()[0]))
                self.sens_curve_var.set(current)
            except (ValueError, IndexError):
                pass
        if current not in values and current != CURVE_KEEP:
            values.insert(2, current)        # e.g. the present curve of another type
        self.sens_curve_cb['values'] = values
        if not self.curve_headers:
            self.lbl_curve_info.config(text="Press Read curve list (read-only, CRVHDR? 1-59) to "
                                            "choose a curve by name.\nSend only picks which "
                                            "stored curve the input uses (INCRV).")
        else:
            self.lbl_curve_info.config(
                text=f"{len(shown)} of {len(self.curve_headers)} stored curves suit "
                     f"{Lakeshore350_Backend.SENSOR_TYPES.get(stype)}. Read-only list; Send "
                     "only picks one (INCRV).")

    def _read_curve_list(self):
        """Read every curve header (CRVHDR? 1-59).  Query only."""
        self.log("Reading the curve list (CRVHDR? 1-59, read-only)...")
        self.root.update_idletasks()
        self._cancel_poll()
        try:
            self.curve_headers = self.backend.read_curve_headers()
            self.log(f"{len(self.curve_headers)} stored curves: " + "; ".join(
                f"{n}={h['name']}" for n, h in sorted(self.curve_headers.items())))
        except Exception as e:
            self.log(f"ERROR reading the curve list: {e}")
        finally:
            if self.backend.is_connected:
                self._schedule_poll(300)
        self._refresh_curve_choices()
        self._refresh_states()

    def _chosen_curve(self):
        """Curve number to send, or None to leave the input's curve as it is."""
        label = self.sens_curve_var.get()
        if label == CURVE_KEEP:
            return None
        curve = int(label.split()[0])
        if not 0 <= curve <= 59:
            raise ValueError("curve must be 0-59")
        return curve

    def _read_sensor(self):
        inp = self.sens_input_var.get()
        try:
            t = self.backend.get_input_type(inp)
            curve = self.backend.get_input_curve(inp)
        except Exception as e:
            self.log(f"ERROR reading input {inp}: {e}")
            return
        B = Lakeshore350_Backend
        self.sens_type_var.set(B.SENSOR_TYPES.get(t['sensor'], B.SENSOR_TYPES[0]))
        self._on_sensor_type()
        ranges = B.INPUT_RANGES.get(t['sensor'], ["(none)"])
        self.sens_range_var.set(ranges[t['range']] if t['range'] < len(ranges) else ranges[0])
        self.sens_auto_var.set("On" if t['autorange'] else "Off")
        self.sens_comp_var.set("On" if t['compensation'] else "Off")
        self.sens_units_var.set(B.UNITS.get(t['units'], B.UNITS[1]))
        self.sens_exc_var.set(B.EXCITATION.get(t['excitation'], B.EXCITATION[1]))
        self.sens_curve_var.set(self._curve_choice_label(curve))
        self._refresh_curve_choices()
        self.sensor_read_input = inp
        self.log(f"Input {inp}: {B.SENSOR_TYPES.get(t['sensor'])}, range "
                 f"{self.sens_range_var.get()}, autorange {self.sens_auto_var.get()}, "
                 f"units {self.sens_units_var.get()}, curve {curve}.")
        self._refresh_states()

    def _send_sensor(self):
        B = Lakeshore350_Backend
        inp = self.sens_input_var.get()
        stype = self._sensor_type_code()
        try:
            rng = B.INPUT_RANGES[stype].index(self.sens_range_var.get())
        except ValueError:
            rng = 0
        units = next(k for k, v in B.UNITS.items() if v == self.sens_units_var.get())
        exc = next(k for k, v in B.EXCITATION.items() if v == self.sens_exc_var.get())
        try:
            curve = self._chosen_curve()
        except (ValueError, IndexError) as e:
            self.log(f"ERROR: curve: {e}")
            return
        warn = "" if units == 1 else ("\n\nUnits are not Kelvin: this window will refuse to "
                                      "ramp on this input until they are.")
        header = self.curve_headers.get(curve) if curve else None
        if header and not curve_matches(stype, header):
            warn += (f"\n\nCurve {curve} ({header['name']}) is a "
                     f"{CURVE_FORMATS.get(header['format'], '?')} curve and does not look "
                     f"like a {B.SENSOR_TYPES[stype]} curve.")
        curve_txt = '' if curve is None else f', curve {self._curve_choice_label(curve)}'
        if not self._ask("Send sensor input",
                         f"Change input {inp} to {B.SENSOR_TYPES[stype]}{curve_txt}?\n\n"
                         "This changes how the input reads temperature. A wrong type or curve "
                         "gives wrong readings and wrong control. The 350 also turns a heater "
                         "controlling on this input off when its setup changes. No curve data "
                         f"is changed.{warn}"):
            return
        try:
            cmd = self.backend.set_input_type(inp, stype, self.sens_auto_var.get() == "On",
                                              rng, self.sens_comp_var.get() == "On", units, exc)
            self.log(f"SENT: {cmd}")
            if curve is not None:
                self.log(f"SENT: {self.backend.set_input_curve(inp, curve)}")
            time.sleep(0.3)
            self._read_sensor()
        except Exception as e:
            self.log(f"ERROR sending input settings: {e}")

    def _display_field_changed(self):
        self.display_read_ok = False
        self._refresh_states()

    def _read_display(self):
        B = Lakeshore350_Backend
        try:
            mode, fields, out = self.backend.get_display()
            self.disp_mode_var.set(B.DISPLAY_MODES.get(mode, B.DISPLAY_MODES[0]))
            if mode == 4:
                self.disp_fields_var.set(B.CUSTOM_FIELDS.get(fields, B.CUSTOM_FIELDS[0]))
                self.disp_out_var.set(str(out))
                inp, units = self.backend.get_display_field(int(self.disp_field_var.get()))
                self.disp_fin_var.set(["None", "Input A", "Input B", "Input C", "Input D"][inp]
                                      if 0 <= inp <= 4 else "None")
                self.disp_funits_var.set(B.FIELD_UNITS.get(units, B.FIELD_UNITS[1]))
            self.display_read_ok = True
            self.log(f"DISPLAY? -> {mode},{fields},{out} ({B.DISPLAY_MODES.get(mode)})")
        except Exception as e:
            self.log(f"ERROR reading the display: {e}")
        self._refresh_states()

    def _send_display(self):
        B = Lakeshore350_Backend
        mode = next(k for k, v in B.DISPLAY_MODES.items() if v == self.disp_mode_var.get())
        try:
            if mode == 4:
                fields = next(k for k, v in B.CUSTOM_FIELDS.items()
                              if v == self.disp_fields_var.get())
                self.log(f"SENT: {self.backend.set_display(mode, fields, int(self.disp_out_var.get()))}")
                inp = ["None", "Input A", "Input B", "Input C", "Input D"].index(self.disp_fin_var.get())
                units = next(k for k, v in B.FIELD_UNITS.items() if v == self.disp_funits_var.get())
                self.log(f"SENT: {self.backend.set_display_field(int(self.disp_field_var.get()), inp, units)}")
            else:
                # Fields only matter in Custom (in All Inputs they mean size,
                # 0 or 1): send 0 and keep the output source that is there.
                _m, _fields, out = self.backend.get_display()
                self.log(f"SENT: {self.backend.set_display(mode, 0, out if 1 <= out <= 4 else 1)}")
        except Exception as e:
            self.log(f"ERROR sending the display: {e}")

    # ----------------------------------------------------- logging

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _beep(self):
        try:
            self.root.bell()
        except Exception:
            pass

    def _set_status(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _open_data_file(self):
        os.makedirs("data", exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_filepath = os.path.join("data", f"TRamp_L350_{stamp}.csv")
        self.data_file = open(self.data_filepath, 'w', newline='', encoding='utf-8')
        self.csv_writer = csv.writer(self.data_file)
        p = self.params
        self.csv_writer.writerow([ascii_label(f"# {self.backend.idn}")])
        self.csv_writer.writerow([ascii_label(
            f"# target {p['setpoint']} K, rate {p['rate']} K/min, input {p['input']}, "
            f"tolerance {p['tol']} K, dwell {p['dwell']} s, outputs {list(self.run_outputs)}, "
            f"mode {self.pid_mode_var.get()}, max {p['max_temp']} K")])
        self.csv_writer.writerow(["Timestamp", "Elapsed_s", "Setpoint_K", "Temperature_K",
                                  "Heater1_pct", "Range1", "Heater2_pct", "Range2",
                                  "Zone", "Note"])
        self.data_file.flush()
        self.log(f"Logging data to: {self.data_filepath}")

    def _csv_row(self, elapsed, setp, temp, htr, rng, note=""):
        if not self.csv_writer:
            return
        zone = "" if self.active_zone is None else str(self.active_zone + 1)
        cells = []
        for n in (1, 2):
            if n in self.run_outputs:
                cells += [f"{htr[n]:.2f}", rng[n]]
            else:
                cells += ["", ""]
        try:
            self.csv_writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                      f"{elapsed:.2f}", f"{setp:.4f}", f"{temp:.4f}"]
                                     + cells + [zone, ascii_label(note)])
            self.data_file.flush()
            os.fsync(self.data_file.fileno())
        except Exception as e:
            self.log(f"WARN: data write failed: {e}")

    def _csv_note(self, note):
        if self.csv_writer:
            try:
                self.csv_writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                          f"{time.time() - self.start_time:.2f}",
                                          "", "", "", "", "", "", "", ascii_label(note)])
                self.data_file.flush()
            except Exception:
                pass

    def _close_data_file(self):
        if self.data_file:
            try:
                self.data_file.flush()
                self.data_file.close()
                self.log(f"Data file closed: {self.data_filepath}")
            except Exception:
                pass
            finally:
                self.data_file = None
                self.csv_writer = None

    # ----------------------------------------------------- polling (one chain)

    def _schedule_poll(self, delay_ms):
        """Only ever one pending tick: cancel before scheduling."""
        self._cancel_poll()
        self.poll_job = self.root.after(int(delay_ms), self._poll_tick)

    def _cancel_poll(self):
        if self.poll_job is not None:
            try:
                self.root.after_cancel(self.poll_job)
            except Exception:
                pass
            self.poll_job = None

    def _poll_tick(self):
        self.poll_job = None
        if not self.backend.is_connected:
            return
        if self.is_running:
            self._monitoring_tick()
        else:
            self._idle_tick()

    def _idle_tick(self):
        inp = self.input_var.get()
        try:
            temp = self.backend.get_temperature(inp)
            self.lbl_current_temp.config(text=f"{temp:.3f} K")
            self.present["Setpoint"].config(text=f"{self.backend.get_setpoint(1):.3f} K")
            for n in (1, 2):
                pct = self.backend.get_heater_output(n)
                rng = self.backend.get_heater_range(n)
                self.present[f"Output {n}"].config(text=self._heater_text(n, pct, rng))
        except Exception as e:
            self.log(f"Reading stopped: {e}. Press Connect again.")
            self.backend.close()
            self.sensor_read_input = None
            self.display_read_ok = False
            self.lbl_conn.config(text="Disconnected after a read error.")
            self._refresh_states()
            return
        self._refresh_rate_hint()
        self._schedule_poll(self.IDLE_POLL_MS)

    def _heater_text(self, n, pct, rng):
        if rng == 0:
            return f"off ({pct:.1f} %)"
        full = range_power(rng, self._out_full_power(n))
        flag = self.heater_cfg['display'] if n == 1 else self.heater_cfg.get('display2', 1)
        watts = heater_watts(pct, full, flag == 2)
        return f"{pct:.1f} % on R{rng}\n~{format_power(watts)} of {format_power(full)}"

    def _ask(self, title, text):
        """A user-initiated yes/no. Polling stops while the dialog is open."""
        self._cancel_poll()
        try:
            return messagebox.askyesno(title, text)
        finally:
            if self.backend.is_connected:
                self._schedule_poll(300)

    # ----------------------------------------------------- connect

    def _selected_address(self):
        label = self.ls_cb.get()
        return self.resource_labels.get(label, label)

    def _toggle_connect(self):
        if self.backend.is_connected:
            self._cancel_poll()
            self.backend.close()
            self.sensor_read_input = None
            self.display_read_ok = False
            self.lbl_conn.config(text="Disconnected. The instrument keeps its settings.")
            self.log("Disconnected (session closed, nothing changed on the instrument).")
            self._refresh_states()
            return
        try:
            self._connect()
        except Exception as e:
            hint = explain_visa_error(e)
            self.log(f"ERROR connecting: {e}" + (f"\nHINT: {hint}" if hint else ""))
            self.backend.close()
            self._refresh_states()

    def _connect(self):
        addr = self._selected_address()
        if not addr:
            raise ValueError("Press Scan and pick the Lakeshore VISA address first.")
        self.log(f"Connecting to {addr}...")
        idn = self.backend.connect(addr)
        if not self.backend.is_model_350():
            self.backend.close()
            raise RuntimeError(f"'{idn}' is not a Lake Shore Model 350. Use the Model 340 "
                               "window for a 340.")
        self.backend.clear_status()
        self.lbl_conn.config(text=f"Connected: {idn}")
        self.log(f"Connected: {idn}")
        self._read_present_state()
        self._refresh_states()
        self._schedule_poll(200)

    def _read_present_state(self):
        """Read-only snapshot of what the instrument is doing now."""
        b = self.backend
        inp = self.input_var.get()
        try:
            self._read_heater_setup(quiet=True)
            lines = []
            pid_txt = []
            for n in (1, 2):
                mode = b.get_output_mode(n)
                p, i, d = b.get_pid(n)
                mout = b.get_mout(n)
                rng = b.get_heater_range(n)
                setp = b.get_setpoint(n)
                modes = {0: "Off", 1: "Closed Loop PID", 2: "Zone", 3: "Open Loop"}
                lines.append(f"Output {n}: {modes.get(mode['mode'], mode['mode'])}, input "
                             f"{' ABCD'[mode['input']] if 0 <= mode['input'] <= 4 else mode['input']}"
                             f", setpoint {setp:.3f} K, PID {p:g}/{i:g}/{d:g}, MOUT {mout:g} %, "
                             f"range {rng} ({format_power(range_power(rng, self._out_full_power(n)))})")
                pid_txt.append(f"{n}: {p:g}/{i:g}/{d:g}")
                if mout > 0:
                    lines.append(f"  NOTE: Output {n} has MOUT {mout:g} % (adds to PID). "
                                 "Start sends the MOUT you set in this window.")
            self.present["PID"].config(text="\n".join(pid_txt))
            t = b.get_input_type(inp)
            lines.append(f"Input {inp}: {Lakeshore350_Backend.SENSOR_TYPES.get(t['sensor'])}, "
                         f"units {Lakeshore350_Backend.UNITS.get(t['units'])}, curve "
                         f"{b.get_input_curve(inp)}")
            if t['units'] != 1:
                lines.append(f"  WARNING: input {inp} is not in kelvin; Start will refuse "
                             "until section 5 sets Kelvin.")
            for line in lines:
                self.log(line)
        except Exception as e:
            self.log(f"Could not read everything: {e}")

    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        try:
            resources = list(self.backend.rm.list_resources())
        except Exception as e:
            self.log(f"Scan error: {e}")
            return
        if not resources:
            self.log("No VISA instruments found.")
            return
        self.log(f"Found: {resources}")
        self.resource_labels = {r: r for r in resources}
        self.ls_cb['values'] = resources
        if len(resources) == 1:
            self.ls_cb.set(resources[0])
        else:
            self.ls_cb.set('')
            self.log("Several addresses: press Identify to find the MODEL350, or pick it.")

    def _identify_visa(self):
        if self.backend.rm is None or self.is_running:
            return
        addresses = list(self.resource_labels.values())
        if not addresses:
            self.log("Nothing to identify: press Scan first.")
            return
        self.log(f"Identifying {len(addresses)} address(es) with *IDN? (2 s timeout each)...")
        self.root.update_idletasks()
        replies = self.backend.identify_resources(addresses)
        labels, chosen = {}, None
        for addr in addresses:
            reply = replies.get(addr, "no reply")
            if reply.startswith("ERROR:"):
                self.log(f"  {addr}: {reply[:90]}")
                labels[f"{addr}  (no answer)"] = addr
            else:
                self.log(f"  {addr}: {reply}")
                label = f"{addr}  ({reply[:32]})"
                labels[label] = addr
                if 'MODEL350' in reply.upper().replace(' ', '') and chosen is None:
                    chosen = label
        self.resource_labels = labels
        self.ls_cb['values'] = list(labels.keys())
        if chosen:
            self.ls_cb.set(chosen)
            self.log(f"Selected the Model 350 at {labels[chosen]}.")
        else:
            self.ls_cb.set('')
            self.log("No address answered as a Model 350.")

    # ----------------------------------------------------- run control

    def _validate_and_get_params(self):
        try:
            params = {
                'setpoint': float(self.entries['target'].get()),
                'rate': float(self.entries['rate'].get()),
                'delay_s': float(self.entries['delay'].get()),
                'tol': float(self.entries['tol'].get()),
                'dwell': float(self.entries['dwell'].get()),
                'max_temp': float(self.entries['max_temp'].get()),
                'input': self.input_var.get(),
            }
        except ValueError:
            raise ValueError("Every ramp field must be a number.")
        if not (0.001 <= params['rate'] <= 100):
            raise ValueError("Ramp rate must be 0.001-100 K/min on a Model 350.")
        if params['delay_s'] <= 0:
            raise ValueError("'Log every' must be positive.")
        if params['tol'] <= 0:
            raise ValueError("The tolerance must be positive.")
        if params['dwell'] < 0:
            raise ValueError("The dwell cannot be negative.")
        if params['setpoint'] <= 0:
            raise ValueError("The target temperature must be positive.")
        if params['setpoint'] >= params['max_temp']:
            raise ValueError(f"The target {params['setpoint']:g} K is not below the safety "
                             f"maximum {params['max_temp']:g} K.")
        return params

    def _windup_warnings(self, temp_now, fixed):
        """LN2 probe: settings that wind up below 100 K (lab result 10 Jul
        2026: an overshoot that never comes back).  Empty list = fine.
        Only for the LN2 cryostat choice: a CCR cools actively."""
        coldest = min(temp_now, self.params['setpoint'])
        if coldest >= 100.0 or not self._system()['ln2_checks']:
            return []
        limit = PID_ULTRA[0] * PID_ULTRA[1]
        out = []
        if fixed is not None:
            for n, (p, i, d, mout, rng) in fixed.items():
                if p * i > limit + 1e-9:
                    out.append(f"Output {n}: P x I = {p * i:g} (Ultra-slow is {limit:g})")
                if mout > 0:
                    out.append(f"Output {n}: MOUT {mout:g} % adds heat the probe cannot shed")
        else:
            for k, (ub, heater, o1, o2) in enumerate(self.zones):
                lo = self.zones[k - 1][0] if k else 0.0
                if lo >= 100.0 or ub < coldest:
                    continue
                for n in HEATER_OUTPUTS[heater]:
                    p, i, d, mout, rng = o1 if n == 1 else o2
                    if p * i > limit + 1e-9:
                        out.append(f"Zone up to {ub:g} K, Output {n}: P x I = {p * i:g}")
                    if mout > 0:
                        out.append(f"Zone up to {ub:g} K, Output {n}: MOUT {mout:g} %")
        return out

    def start_ramp(self):
        if self.is_running:
            return
        touched = ()            # outputs this Start has written to
        try:
            self.params = self._validate_and_get_params()
            if self.pid_mode_var.get() == 'zones':
                self.zones = self._collect_zones()
                fixed = None
                outputs = sorted({n for z in self.zones for n in HEATER_OUTPUTS[z[1]]})
            else:
                self.zones = []
                fixed = self._fixed_settings()
                outputs = sorted(fixed)
            if not self.backend.is_connected:
                self._connect()
            self._cancel_poll()
            b = self.backend
            inp = self.params['input']

            # ---- read-only checks: nothing is written before they pass ----
            t = b.get_input_type(inp)
            if t['units'] != 1:
                raise RuntimeError(
                    f"Input {inp} reads in {Lakeshore350_Backend.UNITS.get(t['units'])}, not "
                    "kelvin. The 350 takes setpoints in the input's units; set Kelvin in "
                    "section 5 first.")
            for n in outputs:
                code, text = b.get_heater_status(n)
                if code != 0:
                    raise RuntimeError(
                        f"Output {n} heater error HTRST? {code}: {text}. Reading the error "
                        "clears it on the instrument, so check the heater wiring before "
                        "pressing Start again.")
            temp_now = b.get_temperature(inp)
            status = b.get_reading_status(inp)
            if status:
                raise RuntimeError(f"Input {inp} reading is not valid ({status}).")
            if temp_now >= self.params['max_temp']:
                raise RuntimeError(f"Present temperature {temp_now:.2f} K is already above "
                                   f"the safety maximum {self.params['max_temp']:g} K.")
            warnings = self._windup_warnings(temp_now, fixed)
            if warnings and not messagebox.askyesno(
                    "Below 100 K: risk of overshoot",
                    "This ramp goes below 100 K, where the LN2 probe winds up and an "
                    "overshoot does not come back:\n\n  " + "\n  ".join(warnings)
                    + "\n\nThe 'LN2 Ultra-slow' preset (P 0.2, I 1) with MOUT 0 is the "
                    "tested setting there. Start anyway?"):
                self.log("Start cancelled at the below-100 K warning. Nothing was sent.")
                if self.backend.is_connected:
                    self._schedule_poll(500)
                return

            rate = self.params['rate']
            if self.rate_cap_var.get():
                caps, default = parse_rate_table(self.rate_table_entry.get())
                cap = rate_cap_for(min(temp_now, self.params['setpoint']), caps, default)
                if rate > cap:
                    self.log(f"Rate {rate:g} K/min limited to {cap:g} K/min (rate table, coldest "
                             f"point {min(temp_now, self.params['setpoint']):.1f} K).")
                    rate = cap
            self.params['rate'] = rate
            self.direction = 'up' if self.params['setpoint'] >= temp_now else 'down'
            self.start_time = time.time()
            self.run_outputs = tuple(outputs)           # for the file header
            self._open_data_file()          # a disk problem stops us here, before any write
            self.log(f"Present temperature {temp_now:.3f} K; ramping {self.direction} to "
                     f"{self.params['setpoint']} K at {rate:g} K/min on output(s) {outputs}.")

            # ---- writes.  Heater off, loop on the control input, setpoint
            # = now (in that input's kelvin), then PID / MOUT / range, then
            # the ramp: the loop never chases a leftover setpoint and never
            # runs on stale gains.
            touched = self.run_outputs
            for n in outputs:
                b.set_heater_range(n, 0)
            for n in outputs:
                b.prepare_output(n, inp)
                self.log(f"Output {n}: Closed Loop PID on input {inp} (OUTMODE {n},1).")
            b.pin_setpoints(outputs, temp_now)
            self.active_zone = None
            if fixed is None:
                self._apply_zone_for_temperature(temp_now, force=True)
            else:
                for n, vals in fixed.items():
                    self._send_output_settings(n, *vals)
                    self.log(f"Output {n}: PID {vals[0]:g}/{vals[1]:g}/{vals[2]:g}, MOUT "
                             f"{vals[3]:g} %, range {vals[4]} "
                             f"({format_power(range_power(vals[4], self._out_full_power(n)))})")
                self.lbl_zone_status.config(text="Active zone: -- (one setting)")
            # Re-pinned to a fresh reading right before the ramp starts.
            b.start_ramp(outputs, self.params['setpoint'], rate, b.get_temperature(inp))
            self.log("Ramp started (setpoint pinned to the present temperature first).")

            self.is_running = True
            self.reached = False
            self.stable_since = None
            self.last_htr_error = {1: 0, 2: 0}
            self.sat_since = None
            self.sat_warned = set()
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in (self.line_temp, self.line_setp, self.line_heater1, self.line_heater2):
                line.set_data([], [])
            self.ax_temp.set_title(f"Ramping to {self.params['setpoint']} K")
            self._set_status(f"RAMPING TO {self.params['setpoint']} K", self.CLR_ACCENT_RED)
            self._refresh_states()
            self._schedule_poll(100)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            hint = explain_visa_error(e)
            self.is_running = False
            self._cancel_poll()
            if touched:
                problems = self.backend.stop_ramp(touched)    # half-started run: heater off
                if problems:
                    self.log("Start failed part-way and the heater could NOT be confirmed "
                             f"off: {problems}. CHECK THE INSTRUMENT.")
                    self._set_status("HEATER OFF NOT CONFIRMED: CHECK THE INSTRUMENT",
                                     self.CLR_ACCENT_RED)
                else:
                    self.log(f"Start failed part-way: RANGE 0 and RAMP off sent on "
                             f"output(s) {list(touched)}.")
            self.run_outputs = ()
            self._close_data_file()
            messagebox.showerror("Start Failed", f"{e}" + (f"\n\n{hint}" if hint else ""))
            if self.backend.is_connected:
                self._schedule_poll(500)
            self._refresh_states()

    def stop_ramp(self, reason="Stopping ramp by user request.", heater_off=True,
                  banner=None, banner_color=None):
        """End the run.  Returns False when turning the heater off could not be
        confirmed; the banner then says so, whatever `banner` asked for."""
        if not self.is_running:
            return True
        self.log(reason)
        self.is_running = False
        self._cancel_poll()
        ok = True
        if heater_off:
            problems = self.backend.stop_ramp(self.run_outputs)
            if problems:
                ok = False
                self.log(f"HEATER OFF NOT CONFIRMED: {problems}. Check the instrument.")
            else:
                self.log(f"RANGE 0 and RAMP off sent on output(s) {list(self.run_outputs)}: "
                         "heater OFF.")
        else:
            self.log("Heater left ON, the loop keeps controlling at the setpoint.")
        self._close_data_file()
        self.run_outputs = ()
        self._refresh_states()
        self.canvas.draw_idle()
        if not ok:
            self._beep()
            self._set_status("HEATER OFF NOT CONFIRMED: CHECK THE INSTRUMENT",
                             self.CLR_ACCENT_RED)
        elif banner:
            self._set_status(banner, banner_color or self.CLR_FRAME_BG)
        if self.backend.is_connected:
            self._schedule_poll(self.IDLE_POLL_MS)
        return ok

    def _monitoring_tick(self):
        try:
            b = self.backend
            inp = self.params['input']
            temp = b.get_temperature(inp)
            rd_status = b.get_reading_status(inp)
            setp = b.get_setpoint(self.run_outputs[0])
            htr, rng = {1: 0.0, 2: 0.0}, {1: 0, 2: 0}
            for n in self.run_outputs:
                htr[n] = b.get_heater_output(n)
                rng[n] = b.get_heater_range(n)
            elapsed = time.time() - self.start_time

            self.lbl_current_temp.config(text=f"{temp:.3f} K")
            self.present["Setpoint"].config(text=f"{setp:.3f} K")
            for n in (1, 2):
                self.present[f"Output {n}"].config(
                    text=self._heater_text(n, htr[n], rng[n]) if n in self.run_outputs
                    else "not used")
            self.present["Zone"].config(
                text="--" if self.active_zone is None else str(self.active_zone + 1))
            line = f"T: {temp:.3f} K | SP: {setp:.3f} K | " + " | ".join(
                f"Out{n}: {htr[n]:.1f}% R{rng[n]}" for n in self.run_outputs)
            if rd_status:
                line += f" | RDGST: {rd_status}"
            self.log(line)

            for n in self.run_outputs:
                code, text = b.get_heater_status(n)
                if code != self.last_htr_error[n]:
                    self.last_htr_error[n] = code
                    if code != 0:
                        self.log(f"HEATER ERROR Output {n} HTRST? {code}: {text}")
                        self._beep()
                        self._csv_note(f"out{n} heater error {code} {text}")
                    else:
                        self.log(f"Output {n} heater error cleared.")

            # Safety maximum (an invalid reading is not acted on: the 350's
            # own reading checks and the probe's limits cover that case).
            if not rd_status and temp >= self.params['max_temp']:
                self._csv_row(elapsed, setp, temp, htr, rng, "safety maximum exceeded")
                self._beep()
                self.stop_ramp(reason=f"T {temp:.2f} K is above the safety maximum "
                                      f"{self.params['max_temp']:g} K.", heater_off=True,
                               banner="SAFETY STOP: ABOVE MAX TEMPERATURE, HEATER OFF",
                               banner_color=self.CLR_ACCENT_RED)
                return

            if rd_status:
                # An invalid reading must not drive the zones or the arrival test.
                self._csv_row(elapsed, setp, temp, htr, rng, f"reading: {rd_status}")
            else:
                self._apply_zone_for_temperature(temp)
                self._csv_row(elapsed, setp, temp, htr, rng)
                self._check_saturation(temp, setp, htr, rng)

            ds = self.data_storage
            ds['time'].append(elapsed)
            ds['temperature'].append(temp)
            ds['setpoint'].append(setp)
            ds['heater1'].append(htr[1] if 1 in self.run_outputs else float('nan'))
            ds['heater2'].append(htr[2] if 2 in self.run_outputs else float('nan'))
            self.line_temp.set_data(ds['time'], ds['temperature'])
            self.line_setp.set_data(ds['time'], ds['setpoint'])
            self.line_heater1.set_data(ds['time'], ds['heater1'])
            self.line_heater2.set_data(ds['time'], ds['heater2'])
            for ax in (self.ax_temp, self.ax_heater):
                ax.relim()
                ax.autoscale_view()
            self.canvas.draw_idle()

            if not self.reached and not rd_status and self._arrival_check(temp):
                return
            self._schedule_poll(self.params['delay_s'] * 1000)
        except Exception:
            # Unattended-safe: no dialog. Heater off, banner, beep, console.
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            self._beep()
            self.stop_ramp(reason="Ramp stopped after a runtime error.", heater_off=True,
                           banner="ERROR: RAMP STOPPED, HEATER OFF (see console)",
                           banner_color=self.CLR_ACCENT_RED)

    def _check_saturation(self, temp, setp, htr, rng):
        """Warn once per zone (console + data file, no dialog) when every
        heater in use sits at 100 % while the reading is below the setpoint:
        the range cannot heat past this point, so with zones on the MEASURED
        temperature the run would stall here.  Nothing is changed."""
        used = [n for n in self.run_outputs if rng[n] > 0]
        if not used or temp >= setp - self.params['tol'] or \
                any(htr[n] < 99.5 for n in used):
            self.sat_since = None
            return
        now = time.time()
        if self.sat_since is None:
            self.sat_since = now
            return
        key = 'fixed' if self.active_zone is None else self.active_zone
        if now - self.sat_since < SATURATION_WARN_S or key in self.sat_warned:
            return
        self.sat_warned.add(key)
        where = "this setting" if key == 'fixed' else f"zone {key + 1}"
        text = (f"WARNING: heater at 100 % for {now - self.sat_since:.0f} s at T={temp:.2f} K, "
                f"below the setpoint {setp:.2f} K: the range of {where} "
                f"(R{'/'.join(str(rng[n]) for n in used)}) cannot heat further. Use a higher "
                "range, or lower this zone's 'Up to' below the temperature it can reach.")
        self.log(text)
        self._csv_note(text)
        self._beep()

    def _arrival_check(self, temp):
        """Tolerance + dwell test. True if the run ended."""
        sp = self.params['setpoint']
        if abs(temp - sp) > self.params['tol']:
            if self.stable_since is not None:
                self.log("Left the tolerance band; dwell restarted.")
                self._set_status(f"RAMPING TO {sp} K", self.CLR_ACCENT_RED)
            self.stable_since = None
            return False
        if self.stable_since is None:
            self.stable_since = time.time()
            self._set_status(f"IN BAND AT {sp} K, DWELLING...", self.CLR_STABLE_WAIT)
            self.log(f"Entered ±{self.params['tol']} K band; dwell {self.params['dwell']} s started.")
            return False
        if time.time() - self.stable_since < self.params['dwell']:
            return False
        self.reached = True
        self._beep()
        if self.hold_var.get():
            self._set_status(f"AT {sp} K | HOLDING (heater on)", self.CLR_OK_GREEN)
            self.ax_temp.set_title(f"Holding at {sp} K")
            self.log("Target reached and held for the dwell. Holding: the loop keeps "
                     "controlling and logging continues until Stop.")
            self._csv_note("target reached, holding")
            return False
        self.ax_temp.set_title(f"Reached {sp} K, heater off")
        self._csv_note("target reached, heater off")
        self.stop_ramp(reason="Target reached and held for the dwell. Ramp complete.",
                       heater_off=True, banner=f"RAMP COMPLETE | HEATER OFF ({sp} K)",
                       banner_color=self.CLR_OK_GREEN)
        return True

    def _on_closing(self):
        if self.is_running:
            self._cancel_poll()
            if messagebox.askyesno("Exit", "A ramp is active. Stop (heater off) and exit?"):
                self.stop_ramp()
                self.backend.close()
                self.root.destroy()
            else:
                self._schedule_poll(300)
        else:
            self._cancel_poll()
            self.backend.close()
            self.root.destroy()


if __name__ == '__main__':
    if not pyvisa:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed. Please run 'pip install pyvisa'.")
        root.destroy()
    else:
        root = tk.Tk()
        app = TempControlGUI(root)
        root.mainloop()
