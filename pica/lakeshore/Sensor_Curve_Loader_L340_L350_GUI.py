"""
Module: Sensor_Curve_Loader_L340_L350_GUI.py
Purpose: Install a calibrated sensor curve (Cernox, Ruthenium-Oxide,
         Platinum, Germanium, diode ...) into a Lake Shore Model 340 or
         Model 350 user curve, then read every point back off the instrument
         and check it.

         Written for the lab Cernox CX-1030-SD-4L sensors X17680 (in use) and
         X17681, whose Lake Shore CD files live in
         Untracked_Stuff/Curv_for_Cernox/Cernox/Calibration Data/1068/.

         THE SOURCE FILE IS X17680.340. There is no '.350' on that CD: the
         '.340' IS Lake Shore's breakpoint format, and a Model 350 stores the
         same breakpoints with the same commands. The per-instrument files on
         the CD (.234, .330, .340, .34A, .91C) are the same calibration
         rendered for different controllers, not different data.

IT ONLY EVER FILLS AN EMPTY USER CURVE. It does not replace an existing one
and has no button that does. The operator picks the target from a map of what
every curve holds, and the curve is read once more with CRVHDR? immediately
before anything is written: if it is not empty then, the send is abandoned
with nothing sent. An unreadable reply counts as not-empty. Refusing a send
costs a retry; overwriting a calibration nobody can get back does not.

Non-destructive: this module never sends *RST, DFLT, SETP, RANGE, RAMP,
PID, MOUT or any other heater, loop or setpoint command. It writes exactly
three kinds of thing, and only when the operator presses the button that
says so:
  - CRVDEL / CRVHDR / CRVPT (+ CRVSAV on the 340), into an empty user curve;
  - optionally INCRV, to put that curve on an input;
  - nothing else.

===============================================================================
EVERYTHING BELOW IS TAKEN FROM THE TWO MANUALS, NOT ASSUMED
===============================================================================

Sources, both in this repository:
  Untracked_Stuff/Lakeshore_340_Manual.pdf   ("Lake Shore Model 340
      Temperature Controller User's Manual"). Curve commands are on PDF
      page 132, printed page 9-30; INCRV on PDF page 135, printed 9-33;
      INTYPE on PDF page 136, printed 9-34.
  Untracked_Stuff/Lakeshore_350_Manual.pdf   ("Model 350 Temperature
      Controller"). Curve commands are on PDF page 157, printed page 143;
      INCRV and INTYPE on PDF pages 161-163, printed 147-149.

THE TWO MODELS ARE NOT THE SAME INSTRUMENT. Everything they disagree about
is in MODEL_SPECS below and nowhere else, so nothing here has to remember
which is which:

                            Model 340              Model 350
  user curve slots          21 - 60                21 - 59
  slots a query may read    1 - 60                 1 - 59
  points per curve          1 - 200                1 - 200
  data formats              1 mV/K, 2 V/K,         1 mV/K, 2 V/K,
                            3 ohm/K, 4 log ohm/K,  3 ohm/K, 4 log ohm/K
                            5 log ohm/log K
  writes reach flash        ONLY after CRVSAV      immediately
  input designators         A, B (C, D with the    A - D (D1-D5 with the
                            option cards)          3062 option)

THE ONE THAT BITES: CRVSAV.
  Model 340, printed 9-30, under both CRVHDR and CRVPT: "NOTE: Curves are
  not permanently updated in the curve FLASH until a CRVSAV command is
  issued." CRVSAV itself: "Updates the Curve Flash with the current user
  curves. May take several seconds; use the BUSY? command to determine when
  complete."
  So on a 340 a curve that was sent, read back and verified is still only in
  RAM, and the next power cycle throws it away. There is no error, no
  warning and no way to tell afterwards. This module therefore sends CRVSAV
  as part of the transfer on a 340, waits for BUSY? to clear, and only then
  reads the curve back. The Model 350 has no CRVSAV and needs none.

THE COMMANDS (both models, except where noted):

  CRVDEL <curve>
      Deletes a user curve. Model 340: 21-60. Model 350: 21-59.
      Sent before a write because CRVHDR and CRVPT do not shorten a curve:
      if the slot held 200 points and the new curve has 134, points 135-200
      would survive and the instrument would interpolate straight through
      somebody else's calibration at the cold end. See ERASE_FIRST.

  CRVHDR <curve>,<name>,<SN>,<format>,<limit value>,<coefficient>
      <name>        15 characters.
      <SN>          10 characters.
      <format>      1 = mV/K, 2 = V/K, 3 = ohm/K, 4 = log ohm/K,
                    and on the 340 only, 5 = log ohm/log K.
      <limit value> the curve temperature limit in kelvin, format +nnn.nnn.
      <coefficient> 1 = negative, 2 = positive.
      The 350 adds: "The coefficient parameter will be calculated
      automatically based on the first 2 curve datapoints. It is included as
      a parameter for compatability with the CRVHDR? query." The 340 says no
      such thing, so the coefficient is always computed from the data here
      and sent, and always checked on readback.

  CRVPT <curve>,<index>,<units value>,<temp value>
      <index>       1 - 200.
      <units value> the sensor reading, to 6 digits.
      <temp value>  the temperature in kelvin, to 6 digits.
      THE SENSOR READING COMES FIRST AND THE TEMPERATURE SECOND, which is
      the same order as a Lake Shore .340 file and the OPPOSITE of a .dat.
      Nothing is guessed: every reader in this module locates its columns by
      the file's own headings and refuses a file that has none.
      The 350 manual's worked example prints a fifth field
      ("CRVPT 21,2,0.10191,470.000,N"), which its own syntax line does not
      have and the 340's does not either. It is a misprint. Four fields are
      sent.

  CRVSAV                                     (Model 340 only)
  CRVHDR? <curve>                            reads the header back
  CRVPT? <curve>,<index>                     reads one point back
  INCRV <input>,<curve number>               puts a curve on an input
  INTYPE / INTYPE?                           the input's sensor type

THE OTHER ONE THAT BITES: INCRV SILENTLY REFUSES.
  Model 350, printed 147: "If specified curve type does not match the
  configured input type, the curve number defaults to 0."
  Model 340, printed 9-33: "If specified curve parameters do not match the
  input, the curve number defaults to 0."
  So putting a log-ohm Cernox curve on an input that is still configured for
  a diode does not fail: the input quietly goes back to 'no curve' and shows
  a sensor-units reading forever. This module reads INTYPE? before INCRV,
  says so if the input is not a resistance type, and re-reads INCRV?
  afterwards to confirm the number stuck. An assignment that comes back as 0
  is reported as the failure it is.

INPUT SENSOR TYPES, for the check above:
  Model 340, printed 9-34: 0 Special, 1 Silicon Diode, 2 GaAlAs Diode,
    3 Platinum 100 (250 ohm), 4 Platinum 100 (500 ohm), 5 Platinum 1000,
    6 Rhodium Iron, 7 Carbon-Glass, 8 CERNOX, 9 RuOx, 10 Germanium,
    11 Capacitor, 12 Thermocouple.
    A Cernox has its own type, 8, and units, coefficient, excitation and
    range are all "a predetermined value based on <type>" - supplying any of
    them turns the input into type 0, Special. So the 340 needs one number
    and no more.
  Model 350, printed 148: 0 Disabled, 1 Diode (3062 option), 2 Platinum RTD,
    3 NTC RTD, 4 Thermocouple (3060 option), 5 Capacitance (3061 option).
    A Cernox is 3, NTC RTD. Its ranges are 10 ohm to 300 kohm (printed 149),
    and its sensor excitation is 0 = 1 mV or 1 = 10 mV; the manual
    recommends 10 mV above about 300 mK.

Lake Shore source files (Lake Shore Sensor CD v1.1, Calibration Data/1068/):
  X17680.340  PREFER THIS ONE. 129 breakpoints, already log-ohm, already
              reading-first, 4.000 to 325.000 K, with the sensor model, the
              serial, the format code, the setpoint limit and the sign of
              the temperature coefficient in its header. Every CRVHDR field
              except the curve name comes straight out of it.
  X17680.dat  the raw calibration: "Temperature (Kelvin)" then "Resistance
              (Ohms)", 71 points from 3.5913 K / 977.25 ohm to 330.03 K /
              43.761 ohm. Reaches further at both ends than the .340 does.
  X17680.tbl  the same calibration on a tidy grid, with sensitivity columns
              this module reads past.
  The .cof, .91C, .234, .330 and .34A files are for other instruments and
  are not read here.

Loading the .340 and extending it with the .dat gives 134 points covering
3.5913 to 330.027 K: the dense certified table plus the extra reach at the
cold end. That is off by default, because the certificate (X17680.pdf,
page 1) states "Temperature Range: 4.00K to 325K" and everything outside it
is measured but not certified. Below 3.5913 K there is no data at all, and
no software can invent it.

v1.0, 1 Sep 2026.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext, Canvas
import math
import os
import queue
import sys
import re
import time
import threading
import traceback
from datetime import datetime

# --- Optional packages -----------------------------------------------------
# Each of these is a feature, not a requirement. The file readers, the curve
# checks and the command builder all work with none of them installed.

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

# Matplotlib draws the curve so it can be looked at before it is sent, which
# is the cheapest check there is: a curve with the columns crossed over does
# not look like a thermometer. Without it the numeric table still shows every
# point.
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False

import runpy
from multiprocessing import Process


# ---------------------------------------------------------------------------
# UTILITY LAUNCHERS (identical to the sibling Lakeshore modules)
# ---------------------------------------------------------------------------

def run_script_process(script_path):
    """Wrapper to execute a script in its own directory via runpy."""
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in "
              f"{os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


# ===============================================================================
# PICA RESOURCE RESOLUTION  (self-contained; inlined in every module)
# ===============================================================================
#
# These modules are run three ways: from inside the installed pica package,
# from a copy of the repository tree, and as a single file dropped somewhere
# on its own. find_pica_root() looks for the package in three ways and gives
# up quietly. Everything downstream treats a missing resource as a disabled
# feature, never as an error.

def find_pica_root():
    """Absolute path of the pica package directory, or None."""
    def looks_like_root(path):
        return (os.path.isdir(os.path.join(path, "assets")) and
                os.path.isdir(os.path.join(path, "utils")))

    try:
        import pica as _pica_pkg
        pkg_dir = os.path.dirname(os.path.abspath(_pica_pkg.__file__))
        if looks_like_root(pkg_dir):
            return pkg_dir
    except Exception:
        pass

    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = os.path.abspath(os.getcwd())

    candidate = here
    for _ in range(5):
        if looks_like_root(candidate):
            return candidate
        nested = os.path.join(candidate, "pica")
        if os.path.isdir(nested) and looks_like_root(nested):
            return nested
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return None


PICA_ROOT = find_pica_root()


def pica_asset(*parts):
    """Absolute path to a file under pica/assets, or '' if unavailable."""
    if not PICA_ROOT:
        return ""
    path = os.path.join(PICA_ROOT, "assets", *parts)
    return path if os.path.exists(path) else ""


def pica_utility(script_name):
    """Absolute path to a script under pica/utils, or '' if unavailable."""
    if not PICA_ROOT:
        return ""
    path = os.path.join(PICA_ROOT, "utils", script_name)
    return path if os.path.exists(path) else ""


def launch_pica_utility(script_name, friendly_name):
    """Run a pica/utils script in its own process."""
    path = pica_utility(script_name)
    if not path:
        messagebox.showerror(
            f"{friendly_name} Not Available",
            f"{friendly_name} could not be found.\n\n"
            "This module is running outside the pica package, so the shared "
            "utilities in pica/utils are not reachable. Everything else in "
            "this window works normally.")
        return False
    try:
        Process(target=run_script_process, args=(path,)).start()
        return True
    except Exception as e:
        messagebox.showerror("Launch Error",
                             f"Failed to launch {friendly_name}: {e}")
        return False


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    launch_pica_utility("PlotterUtil_GUI.py", "Plotter Utility")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    launch_pica_utility("GPIB_Instrument_Scanner_GUI.py", "GPIB Scanner")


# ===============================================================================
# WHAT EACH MODEL IS
# ===============================================================================
#
# Every difference between the 340 and the 350 lives here. Nothing else in
# this module may hard-code a slot range, a format code or a CRVSAV.

MODEL_340 = '340'
MODEL_350 = '350'

# Lake Shore data format codes, as printed in a .340 header and as CRVHDR
# takes them. The second element is the internal unit name this module works
# in; the third is what the manual calls it.
FORMAT_CODES = {
    1: ('MV', "mV/K"),
    2: ('V', "V/K"),
    3: ('OHMS', "ohm/K"),
    4: ('LOGOHM', "log ohm/K"),
    5: ('LOGOHM_LOGK', "log ohm/log K"),
}

# The unit names this module converts between, and what may become what.
# Ohms and log-ohms are the same measurement written two ways; millivolts and
# volts likewise. A resistance is never turned into a voltage.
CONVERTIBLE_GROUPS = (frozenset({'OHMS', 'LOGOHM'}), frozenset({'MV', 'V'}))

MIN_CURVE_POINTS = 1
MAX_CURVE_POINTS = 200          # both models, printed 9-30 and 144
MAX_NAME_CHARS = 15             # both models
MAX_SERIAL_CHARS = 10           # both models
MAX_LIMIT_KELVIN = 999.999      # the +nnn.nnn field cannot hold more

# Characters that must never reach a CRVHDR field. The 350 manual warns
# under INNAME that commas and semicolons are query-response delimiters;
# CRVHDR is a comma-separated command, so a comma in a name would silently
# shift every field after it by one.
NAME_FORBIDDEN = ',;"\''

MODEL_SPECS = {
    MODEL_340: {
        'label': "Lake Shore Model 340",
        'short': "Model 340",
        # '*IDN?' on a 340 answers 'LSCI,MODEL340,<serial>,<firmware>'.
        'idn_tokens': ('MODEL340', 'MODEL 340'),
        'user_curve_min': 21,
        'user_curve_max': 60,
        'query_curve_min': 1,
        'query_curve_max': 60,
        'formats': (1, 2, 3, 4, 5),
        'needs_crvsav': True,
        'inputs': ('A', 'B', 'C', 'D'),
        'input_note': ("A and B are standard; C and D exist only with the "
                       "option cards."),
        # INTYPE <input>,<type>,... printed 9-34.
        'sensor_types': {
            0: "Special",
            1: "Silicon Diode",
            2: "GaAlAs Diode",
            3: "Platinum 100 (250 ohm)",
            4: "Platinum 100 (500 ohm)",
            5: "Platinum 1000",
            6: "Rhodium Iron",
            7: "Carbon-Glass",
            8: "Cernox",
            9: "RuOx",
            10: "Germanium",
            11: "Capacitor",
            12: "Thermocouple",
        },
        # Types that measure a resistance, so that a log-ohm or ohm curve
        # will be accepted by INCRV. Type 0 (Special) can be either, so it is
        # included and reported as unknown rather than refused.
        'resistive_types': (0, 3, 4, 5, 6, 7, 8, 9, 10),
        'voltage_types': (1, 2, 12),
        'ntc_type': 8,
        'ntc_type_label': "Cernox",
        # The 340 works out units, coefficient, excitation and range from the
        # type, and supplying any of them turns the input into Special.
        'intype_command': "INTYPE {input},{type}",
        'ranges': None,
        'manual': ("Lake Shore Model 340 User's Manual, printed 9-30 to "
                   "9-34"),
    },
    MODEL_350: {
        'label': "Lake Shore Model 350",
        'short': "Model 350",
        'idn_tokens': ('MODEL350', 'MODEL 350'),
        'user_curve_min': 21,
        'user_curve_max': 59,
        'query_curve_min': 1,
        'query_curve_max': 59,
        'formats': (1, 2, 3, 4),
        'needs_crvsav': False,
        'inputs': ('A', 'B', 'C', 'D'),
        'input_note': ("A to D are standard; D1 to D5 exist only with the "
                       "3062 option."),
        'sensor_types': {
            0: "Disabled",
            1: "Diode (3062 option only)",
            2: "Platinum RTD",
            3: "NTC RTD",
            4: "Thermocouple (3060 option only)",
            5: "Capacitance (3061 option only)",
        },
        'resistive_types': (2, 3),
        'voltage_types': (1, 4),
        'ntc_type': 3,
        'ntc_type_label': "NTC RTD",
        'intype_command': ("INTYPE {input},{type},{autorange},{range},"
                           "{compensation},{units},{excitation}"),
        # NTC RTD input ranges, printed 149. Index -> full scale in ohms.
        'ranges': {0: 10.0, 1: 30.0, 2: 100.0, 3: 300.0, 4: 1.0e3,
                   5: 3.0e3, 6: 10.0e3, 7: 30.0e3, 8: 100.0e3, 9: 300.0e3},
        'manual': "Model 350 Temperature Controller manual, printed 143-149",
    },
}

MODEL_ORDER = (MODEL_340, MODEL_350)

# Lake Shore temperature-coefficient codes, used by both the .340 file header
# and the CRVHDR command. They agree, which is why nothing is translated.
COEFFICIENT_NEGATIVE = 1
COEFFICIENT_POSITIVE = 2
COEFFICIENT_NAMES = {COEFFICIENT_NEGATIVE: "negative",
                     COEFFICIENT_POSITIVE: "positive"}

# What the lab Cernox wants, taken from the file itself rather than typed in:
# format 4, coefficient 1, limit 325 K. These are only the fallbacks used
# when a source file does not state them.
CERNOX_DEFAULTS = {
    'format': 4,
    'coefficient': COEFFICIENT_NEGATIVE,
    'limit': 325.0,
}


def model_spec(model):
    """The spec dict for a model key, or KeyError with a useful message."""
    try:
        return MODEL_SPECS[str(model).strip()]
    except KeyError:
        raise KeyError(
            f"{model!r} is not a model this module knows. Choose one of "
            f"{', '.join(MODEL_ORDER)}.")


def model_from_idn(idn):
    """Which model a '*IDN?' reply came from, or None.

    Matched on the model token rather than on 'LSCI', because this module
    writes calibration curves and the slot ranges, the format codes and the
    need for CRVSAV all differ between the two. A Lake Shore that is neither
    returns None and is refused.
    """
    text = str(idn).upper().replace('-', '')
    for key in MODEL_ORDER:
        for token in MODEL_SPECS[key]['idn_tokens']:
            if token.replace(' ', '') in text.replace(' ', ''):
                return key
    return None


def is_lakeshore_idn(idn):
    """True if a '*IDN?' reply looks like any Lake Shore instrument."""
    return 'LSCI' in str(idn).upper() or 'LAKE SHORE' in str(idn).upper()


# ===============================================================================
# NUMBER AND FILE HANDLING
# ===============================================================================


class CurveFileError(ValueError):
    """A sensor file could not be read with certainty."""


def fmt6(value):
    """Six significant digits, in plain decimal.

    Six is what CRVPT takes: "Specifies sensor units for this point to 6
    digits", and the same for the temperature. Writing more would imply a
    precision the instrument does not store.

    Exponent notation is avoided because the CRVPT syntax line is
    '?nnnnnn,+nnnnnn', which has no exponent in it, and a value written as
    '1e-05' that were read as '1' would be wrong by five orders of magnitude
    without looking wrong.
    """
    if not math.isfinite(value):
        raise ValueError(f"{value!r} is not a finite number")
    text = f"{value:.6g}"
    if 'e' in text or 'E' in text:
        text = f"{value:.12f}".rstrip('0').rstrip('.')
    if not text:
        text = "0"
    if '.' not in text:
        text += ".0"
    return text


def fmt_limit(value):
    """The CRVHDR <limit value> field, printed as its +nnn.nnn syntax."""
    if not math.isfinite(value):
        raise ValueError(f"{value!r} is not a finite temperature limit")
    return f"{value:.3f}"


def _clean_lines(raw_text):
    """Split into lines, dropping the BOM and any carriage returns."""
    return raw_text.replace('﻿', '').replace('\r\n', '\n') \
                   .replace('\r', '\n').split('\n')


def _read_text(path):
    """Read a sensor file as text.

    Lake Shore CD files are plain ASCII with CRLF endings, but they are
    twenty-odd years old, so a stray high byte is decoded rather than allowed
    to abort the load.
    """
    with open(path, 'rb') as handle:
        blob = handle.read()
    try:
        return blob.decode('ascii')
    except UnicodeDecodeError:
        return blob.decode('latin-1')


def _tokens_are_numeric(tokens, count=None):
    """True if every token parses as a float (and, optionally, there are N)."""
    if not tokens:
        return False
    if count is not None and len(tokens) != count:
        return False
    for token in tokens:
        try:
            float(token)
        except ValueError:
            return False
    return True


def _identify_columns(header_tokens):
    """Work out which column is temperature and which is the sensor reading.

    Returns (temperature_index, reading_index, units). Raises if the header
    does not say, because guessing the column order is precisely the mistake
    this module exists to prevent: a Lake Shore .dat is temperature-first and
    a CRVPT command is reading-first, and the two are indistinguishable once
    the labels are gone.
    """
    temp_index = None
    reading_index = None
    units = None
    for index, token in enumerate(header_tokens):
        word = token.lower().strip('.():')
        if temp_index is None and word.startswith('temp'):
            temp_index = index
            continue
        if reading_index is not None:
            continue
        # The .tbl sensitivity columns also carry ohm-ish words, so the
        # reading column is taken from the first match only and they fall
        # past it.
        if word.startswith('resist') or word in ('ohms', 'ohm'):
            reading_index, units = index, 'OHMS'
        elif word.startswith('millivolt'):
            reading_index, units = index, 'MV'
        elif word.startswith('volt'):
            reading_index, units = index, 'V'
        elif word.startswith('logohm') or word.startswith('log'):
            reading_index, units = index, 'LOGOHM'
    if temp_index is None or reading_index is None:
        raise CurveFileError(
            "This file has no column headings, so which column is the "
            "temperature and which is the sensor reading cannot be "
            "established.\n\n"
            "The two orders look identical once the labels are gone, and "
            "loading them the wrong way round would put a silently wrong "
            "curve into the instrument, so the file is refused rather than "
            "guessed at.\n\n"
            "Use the original Lake Shore .340, .dat or .tbl file, all of "
            "which say which column is which.")
    return temp_index, reading_index, units


def _parse_lakeshore_columns(lines, source_name):
    """Read a Lake Shore .dat or .tbl file.

    .dat is the raw calibration:
        Temperature             Resistance
        (Kelvin)                (Ohms)
        <blank>
        3.59132424209341E+00   9.77251462533428E+02
    .tbl is the same calibration on a tidy grid, with two extra sensitivity
    columns that are read past and ignored.
    """
    header_tokens = None
    unit_tokens = None
    rows = []
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        if _tokens_are_numeric(tokens):
            if header_tokens is None:
                raise CurveFileError(
                    f"{source_name} starts with numbers before any column "
                    "heading, so its column order cannot be established.")
            if len(tokens) < len(header_tokens):
                raise CurveFileError(
                    f"{source_name} has a data line with {len(tokens)} "
                    f"columns where the heading names {len(header_tokens)}: "
                    f"'{line.strip()}'.")
            rows.append(tokens)
        elif header_tokens is None:
            header_tokens = tokens
        elif unit_tokens is None:
            unit_tokens = tokens

    if header_tokens is None:
        raise CurveFileError(f"{source_name} contains no column heading.")
    if not rows:
        raise CurveFileError(f"{source_name} contains no numeric data rows.")

    temp_index, reading_index, units = _identify_columns(header_tokens)

    # The second header line carries the units in parentheses. Where it is
    # present it confirms what the first line implied; a disagreement is
    # reported rather than resolved silently.
    if unit_tokens and len(unit_tokens) > reading_index:
        stated = unit_tokens[reading_index].lower().strip('()')
        confirmed = {'ohms': 'OHMS', 'ohm': 'OHMS',
                     'volts': 'V', 'volt': 'V',
                     'millivolts': 'MV', 'mv': 'MV',
                     'logohms': 'LOGOHM', 'logohm': 'LOGOHM'}.get(stated)
        if confirmed and confirmed != units:
            raise CurveFileError(
                f"{source_name} disagrees with itself: the column is called "
                f"'{header_tokens[reading_index]}' but its unit row says "
                f"'{unit_tokens[reading_index]}'. The file is refused rather "
                "than one of the two being picked.")
    if unit_tokens and len(unit_tokens) > temp_index:
        stated = unit_tokens[temp_index].lower().strip('()')
        if stated and stated not in ('kelvin', 'k'):
            raise CurveFileError(
                f"{source_name} states its temperature column in "
                f"'{unit_tokens[temp_index]}', not Kelvin. A Lake Shore curve "
                "point is always in Kelvin, so this file is refused.")

    points = [(float(tokens[temp_index]), float(tokens[reading_index]))
              for tokens in rows]
    meta = {
        'columns': (f"column {temp_index + 1} = temperature, "
                    f"column {reading_index + 1} = sensor reading"),
    }
    return points, units, meta


def _parse_lakeshore_340(lines, source_name):
    """Read a Lake Shore .340 (or .330) breakpoint table.

        Sensor Model:   CX-1030-SD-4L
        Serial Number:  X17680
        Data Format:    4      (Log Ohms/Kelvin)
        SetPoint Limit: 325.      (Kelvin)
        Temperature coefficient:  1 (Negative)
        Number of Breakpoints:   129

        No.   Units      Temperature (K)
          1  1.64523       325.000

    The units column comes first and the temperature second, which is exactly
    the order CRVPT wants, so nothing is swapped anywhere.

    Every CRVHDR field except the curve name is in this header: the format
    code, the setpoint limit and the coefficient are read out of it and put
    into the form, so the operator confirms them rather than types them.
    """
    meta = {}
    fmt_code = None
    stated_count = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r'Data Format:\s*(\d+)', stripped, re.I)
        if match:
            fmt_code = int(match.group(1))
            continue
        match = re.match(r'Number of Breakpoints:\s*(\d+)', stripped, re.I)
        if match:
            stated_count = int(match.group(1))
            continue
        match = re.match(r'Sensor Model:\s*(.+)', stripped, re.I)
        if match:
            meta['model'] = match.group(1).strip()
            continue
        match = re.match(r'Serial Number:\s*(.+)', stripped, re.I)
        if match:
            meta['serial'] = match.group(1).strip()
            continue
        match = re.match(r'SetPoint Limit:\s*([0-9.]+)', stripped, re.I)
        if match:
            try:
                meta['limit'] = float(match.group(1))
            except ValueError:
                pass
            continue
        match = re.match(r'Temperature coefficient:\s*(\d+)', stripped, re.I)
        if match:
            # Lake Shore: 1 = negative, 2 = positive. CRVHDR uses the same
            # two numbers, so this is carried across unchanged.
            meta['coefficient'] = int(match.group(1))
            continue

    if fmt_code is None:
        raise CurveFileError(
            f"{source_name} has no 'Data Format:' line, so the units of its "
            "middle column are unknown and it cannot be sent safely.")
    if fmt_code not in FORMAT_CODES:
        known = ", ".join(str(k) for k in sorted(FORMAT_CODES))
        raise CurveFileError(
            f"{source_name} is Lake Shore data format {fmt_code}, which is "
            f"not one of the formats these instruments store ({known}).")
    if fmt_code == 5:
        raise CurveFileError(
            f"{source_name} is data format 5, log ohm versus LOG kelvin. Its "
            "temperature column holds log10(K), not kelvin, so it is a "
            "different table from the one this module builds and it is "
            "refused rather than read as if it were kelvin.\n\n"
            "Use the .340 in format 4 (log ohm/K) or the raw .dat for this "
            "sensor. Format 5 is also not available on a Model 350 at all.")

    units, format_name = FORMAT_CODES[fmt_code]
    meta['format'] = fmt_code
    meta['format_name'] = f"{fmt_code} ({format_name})"
    meta['columns'] = ("column 2 = sensor reading, column 3 = temperature "
                       "(the same order CRVPT wants)")

    points = []
    for line in lines:
        tokens = line.split()
        if not _tokens_are_numeric(tokens, count=3):
            continue
        # The first token is the breakpoint number and must be a whole
        # number; anything else means this is not a breakpoint row.
        index, reading, temperature = tokens
        if float(index) != int(float(index)):
            continue
        points.append((float(temperature), float(reading)))

    if not points:
        raise CurveFileError(f"{source_name} contains no breakpoint rows.")
    if stated_count is not None and stated_count != len(points):
        raise CurveFileError(
            f"{source_name} says it holds {stated_count} breakpoints but "
            f"{len(points)} were read. The file is refused rather than "
            "loaded partially.")
    return points, units, meta


def load_sensor_file(path):
    """Read any supported Lake Shore sensor file.

    Returns a dict:
        points   [(temperature_K, reading), ...] in the file's own units
        units    'MV' | 'V' | 'OHMS' | 'LOGOHM'
        kind     which reader was used
        meta     whatever the file said about itself
    """
    text = _read_text(path)
    lines = _clean_lines(text)
    name = os.path.basename(path)
    extension = os.path.splitext(path)[1].lower()

    if any(re.match(r'\s*Data Format:', line, re.I) for line in lines):
        points, units, meta = _parse_lakeshore_340(lines, name)
        return {'points': points, 'units': units, 'kind': 'lakeshore-340',
                'meta': meta}

    points, units, meta = _parse_lakeshore_columns(lines, name)
    kind = 'lakeshore-tbl' if extension == '.tbl' else 'lakeshore-dat'
    return {'points': points, 'units': units, 'kind': kind, 'meta': meta}


def convert_units(points, from_units, to_units):
    """Convert (temperature, reading) pairs between curve units.

    Ohms and log-ohms interconvert, and millivolts and volts interconvert.
    A resistance is never turned into a voltage: those measure different
    things, and asking for it is an error rather than a silent no-op.
    """
    if from_units == to_units:
        return list(points)
    pair = {from_units, to_units}
    if pair == {'OHMS', 'LOGOHM'}:
        converted = []
        for temperature, reading in points:
            if from_units == 'OHMS':
                if reading <= 0:
                    raise CurveFileError(
                        f"A resistance of {reading} ohm at {temperature} K "
                        "has no logarithm, so this curve cannot be written "
                        "in log ohm/K. Use ohm/K, or check the file.")
                converted.append((temperature, math.log10(reading)))
            else:
                converted.append((temperature, 10.0 ** reading))
        return converted
    if pair == {'MV', 'V'}:
        scale = 1.0e-3 if from_units == 'MV' else 1.0e3
        return [(temperature, reading * scale)
                for temperature, reading in points]
    raise CurveFileError(
        f"A curve in {from_units} cannot be turned into {to_units}: they "
        f"measure different things. Keep the file's own units, or load a "
        f"file that is already in {to_units}.")


def extend_curve(primary, extra, primary_units, extra_units):
    """Extend a curve at its ends with points from a second file.

    Written for the lab Cernox, where neither Lake Shore file alone is the
    best answer:

      X17680.340  129 breakpoints Lake Shore placed for an instrument that
                  interpolates between them, over the certified
                  4.000-325.000 K.
      X17680.dat  the 71 raw measured points, reaching 3.5913 K and
                  330.03 K but sparser in between.

    Taking the .340 as the curve and adding the .dat points that lie beyond
    its ends gives the dense certified table AND the extra reach at the cold
    end, which is what a cryostat running below 4 K needs.

    The rules are deliberately narrow, because merging two derivations of
    the same calibration is a good way to build a curve worse than either:

      * every point of `primary` is kept exactly as it is;
      * a point from `extra` is added ONLY if it lies outside `primary`'s
        temperature span. Nothing is interleaved, so the interpolation
        inside the certified range is still purely Lake Shore's table;
      * an added point must also lie beyond `primary`'s span in SENSOR
        READING. For a monotonic sensor that is automatic; if it fails, the
        two files disagree about the same sensor and the merge is refused
        rather than papered over.

    Returns (points, added, notes).
    """
    if not primary:
        raise CurveFileError("The main curve is empty, so there is nothing "
                             "to extend.")
    converted = convert_units(extra, extra_units, primary_units)

    temperatures = [t for t, _ in primary]
    readings = [r for _, r in primary]
    t_low, t_high = min(temperatures), max(temperatures)
    r_low, r_high = min(readings), max(readings)

    added = []
    notes = []
    for temperature, reading in converted:
        if t_low <= temperature <= t_high:
            continue                      # inside the main curve; not ours
        if not (reading < r_low or reading > r_high):
            raise CurveFileError(
                f"The second file has a point at {temperature:.6g} K, "
                f"outside the main curve's {t_low:.6g}-{t_high:.6g} K span, "
                f"whose sensor reading {reading:.6g} falls back INSIDE the "
                f"main curve's {r_low:.6g}-{r_high:.6g}. The two files "
                "disagree about this sensor, so they are not merged. Use one "
                "file on its own.")
        added.append((temperature, reading))

    if not added:
        notes.append(
            "The second file adds nothing: every one of its points lies "
            f"inside the {t_low:.6g}-{t_high:.6g} K span the main curve "
            "already covers.")
        return list(primary), [], notes

    merged = sorted(list(primary) + added, key=lambda pair: pair[1])
    cold = [t for t, _ in added if t < t_low]
    hot = [t for t, _ in added if t > t_high]
    if cold:
        notes.append(
            f"{len(cold)} point(s) added below the main curve: it now "
            f"reaches {min(cold):.6g} K instead of {t_low:.6g} K. Those "
            "points are measured, but a calibration certificate states a "
            "range, and anything outside it is uncertified.")
    if hot:
        notes.append(
            f"{len(hot)} point(s) added above the main curve: it now "
            f"reaches {max(hot):.6g} K instead of {t_high:.6g} K.")
    notes.append(
        "Nothing inside the main curve's own span was changed, so the "
        "interpolation there is still exactly the main file's.")
    return merged, added, notes


def thin_points(points, limit=MAX_CURVE_POINTS):
    """Reduce a curve to at most `limit` points, keeping both ends.

    Both instruments hold 200 breakpoints. Points are dropped at even
    spacing through the list rather than by any cleverness, so what survives
    is a plain subset of the calibration and nothing is invented by
    interpolation.
    """
    if limit < 2:
        raise ValueError(f"A curve cannot be thinned to {limit} points.")
    count = len(points)
    if count <= limit:
        return list(points), 0
    keep = sorted({round(i * (count - 1) / (limit - 1)) for i in range(limit)})
    thinned = [points[i] for i in keep]
    return thinned, count - len(thinned)


def coefficient_from_points(points):
    """1 (negative) or 2 (positive), read off the data.

    CRVHDR's <coefficient> is the sign of dT/d(sensor units): negative for a
    Cernox, whose resistance rises as it gets colder. The 350 recomputes it
    from the first two points; the 340 does not say it does. It is computed
    here either way so the readback has something to be checked against.
    """
    ordered = sorted(points, key=lambda pair: pair[1])
    if len(ordered) < 2:
        return COEFFICIENT_NEGATIVE
    return (COEFFICIENT_NEGATIVE if ordered[-1][0] < ordered[0][0]
            else COEFFICIENT_POSITIVE)


def analyse_curve(model, points, units, fmt_code, name, serial, limit,
                  coefficient, working_range=None, input_type=None):
    """Check a curve against everything the two manuals require of one.

    `model` decides the slot range, the legal format codes and whether an
    input-range table exists, so the same curve can be judged for a 340 and
    for a 350 without either set of rules leaking into the other.

    `input_type` is the INTYPE sensor-type code the input will be set to.
    It is what decides whether INCRV will accept this curve at all: both
    manuals say a curve whose parameters do not match the input is silently
    replaced by curve 0.

    `working_range` is an optional (lowest, highest) pair of temperatures in
    kelvin that the sensor will actually be used over. This is not a Lake
    Shore rule; it is here because a curve that simply stops before the cold
    end of a run is not a fault the instrument reports as one, and if nobody
    was told to expect it, it is discovered on a cold cryostat at two in the
    morning.

    Returns (errors, warnings, stats). Errors block the transfer; warnings do
    not, but each one names something worth a second look.
    """
    spec = model_spec(model)
    errors = []
    warnings = []
    stats = {}

    if not points:
        return (["The curve holds no points."], [], stats)

    # Sorted by sensor reading, which is the order the points are indexed in
    # and therefore the order the instrument interpolates along.
    ordered = sorted(points, key=lambda pair: pair[1])
    readings = [reading for _, reading in ordered]
    temperatures = [temperature for temperature, _ in ordered]

    stats['count'] = len(ordered)
    stats['t_min'] = min(temperatures)
    stats['t_max'] = max(temperatures)
    stats['r_min'] = min(readings)
    stats['r_max'] = max(readings)
    stats['coefficient'] = coefficient_from_points(ordered)

    if len(ordered) > MAX_CURVE_POINTS:
        errors.append(
            f"A user curve holds at most {MAX_CURVE_POINTS} points; this one "
            f"has {len(ordered)}. Thin it before sending.")
    if len(ordered) < 2:
        errors.append(
            "A curve of one point cannot be interpolated: the instrument "
            "needs at least two.")

    for temperature, reading in ordered:
        if not (math.isfinite(temperature) and math.isfinite(reading)):
            errors.append("The curve contains a value that is not a finite "
                          "number.")
            break
    if any(temperature <= 0 for temperature in temperatures):
        errors.append(
            "The curve contains a temperature of zero or below. Lake Shore "
            "curve temperatures are absolute, in kelvin.")
    if units == 'OHMS' and any(reading <= 0 for reading in readings):
        # LOGOHM is deliberately not checked this way: its values are
        # logarithms, so a negative one only means a resistance below 1 ohm.
        errors.append("The curve contains a resistance of zero or below.")

    duplicates = [readings[i] for i in range(1, len(readings))
                  if readings[i] == readings[i - 1]]
    if duplicates:
        errors.append(
            f"{len(duplicates)} point(s) repeat a sensor reading (for "
            f"example {fmt6(duplicates[0])}). The instrument interpolates on "
            "the sensor reading, so a repeated reading has no single "
            "temperature.")

    rises = sum(1 for i in range(1, len(temperatures))
                if temperatures[i] > temperatures[i - 1])
    falls = sum(1 for i in range(1, len(temperatures))
                if temperatures[i] < temperatures[i - 1])
    if rises and falls:
        warnings.append(
            f"Temperature does not move in one direction along the curve "
            f"({rises} step(s) up, {falls} down). A sensor whose reading "
            "repeats at two temperatures cannot be interpolated reliably "
            "there. Check the source file.")

    # -- the CRVHDR fields ---------------------------------------------------

    if fmt_code not in spec['formats']:
        legal = ", ".join(
            f"{code} = {FORMAT_CODES[code][1]}" for code in spec['formats'])
        errors.append(
            f"Data format {fmt_code} is not one the {spec['short']} stores. "
            f"It takes {legal}."
            + (" Format 5, log ohm versus log kelvin, exists on the 340 only."
               if fmt_code == 5 else ""))
    else:
        expected_units = FORMAT_CODES[fmt_code][0]
        if expected_units != units:
            errors.append(
                f"The curve is in {units} but format {fmt_code} is "
                f"{FORMAT_CODES[fmt_code][1]}. The instrument would read "
                "every point in the wrong units.")

    if not name:
        errors.append("The curve needs a name; CRVHDR has a field for it.")
    if len(name) > MAX_NAME_CHARS:
        errors.append(
            f"The curve name '{name}' is {len(name)} characters; CRVHDR "
            f"keeps {MAX_NAME_CHARS}.")
    if any(character in NAME_FORBIDDEN for character in name):
        errors.append(
            f"The curve name '{name}' contains one of {NAME_FORBIDDEN}. "
            "CRVHDR is a comma-separated command and query replies are "
            "comma-separated too, so any of those would shift every field "
            "after it by one.")
    if not all(32 <= ord(character) < 127 for character in name):
        errors.append(
            f"The curve name '{name}' contains a character that is not "
            "printable ASCII.")

    if len(serial) > MAX_SERIAL_CHARS:
        errors.append(
            f"The serial number '{serial}' is {len(serial)} characters; "
            f"CRVHDR keeps {MAX_SERIAL_CHARS}.")
    if any(character in NAME_FORBIDDEN for character in serial):
        errors.append(
            f"The serial number '{serial}' contains one of "
            f"{NAME_FORBIDDEN}, which CRVHDR treats as a field separator.")

    if not math.isfinite(limit) or limit <= 0:
        errors.append(
            f"The temperature limit {limit!r} is not a positive number of "
            "kelvin.")
    elif limit > MAX_LIMIT_KELVIN:
        errors.append(
            f"The temperature limit {limit:g} K will not fit the CRVHDR "
            f"+nnn.nnn field, which stops at {MAX_LIMIT_KELVIN:g} K.")
    elif limit < stats['t_max']:
        warnings.append(
            f"The curve reaches {stats['t_max']:.4g} K but the temperature "
            f"limit in the header is {limit:g} K. The limit is what the "
            "instrument refuses to be driven past; set below the top of the "
            "curve it simply caps the setpoint sooner.")

    if coefficient not in COEFFICIENT_NAMES:
        errors.append(
            f"The temperature coefficient must be {COEFFICIENT_NEGATIVE} "
            f"(negative) or {COEFFICIENT_POSITIVE} (positive), not "
            f"{coefficient!r}.")
    elif coefficient != stats['coefficient']:
        errors.append(
            f"The data has a {COEFFICIENT_NAMES[stats['coefficient']]} "
            f"temperature coefficient, but the header says "
            f"{COEFFICIENT_NAMES[coefficient]}. A Cernox is negative: its "
            "reading rises as it gets colder.")

    # -- how the curve sits on the input ------------------------------------

    peak_ohms = None
    if units == 'OHMS':
        peak_ohms = stats['r_max']
    elif units == 'LOGOHM':
        peak_ohms = 10.0 ** stats['r_max']
    if peak_ohms is not None:
        stats['peak_ohms'] = peak_ohms

    resistive_curve = units in ('OHMS', 'LOGOHM')
    if input_type is not None:
        types = spec['sensor_types']
        if input_type not in types:
            errors.append(
                f"{input_type} is not an INTYPE sensor type on the "
                f"{spec['short']}.")
        else:
            stats['input_type_name'] = types[input_type]
            if resistive_curve and input_type in spec['voltage_types']:
                errors.append(
                    f"The curve is in {FORMAT_CODES[fmt_code][1]} but input "
                    f"type {input_type} ({types[input_type]}) measures a "
                    "voltage. INCRV would refuse the curve and set the input "
                    "back to curve 0 without saying so.")
            if (not resistive_curve) and input_type != 0 and \
                    input_type in spec['resistive_types']:
                errors.append(
                    f"The curve is in {FORMAT_CODES[fmt_code][1]} but input "
                    f"type {input_type} ({types[input_type]}) measures a "
                    "resistance. INCRV would set the input back to curve 0.")

    if peak_ohms is not None and spec['ranges']:
        # Model 350 only: the NTC RTD input ranges are a printed table, so
        # the peak resistance can be placed in it rather than guessed at.
        covering = [index
                    for index, full_scale in sorted(spec['ranges'].items())
                    if peak_ohms <= full_scale]
        if covering:
            index = covering[0]
            stats['suggested_range'] = index
            stats['suggested_range_ohms'] = spec['ranges'][index]
        else:
            biggest = max(spec['ranges'].values())
            errors.append(
                f"The curve reaches {peak_ohms:,.1f} ohm, which is off the "
                f"top of the {spec['short']}'s largest resistance range "
                f"({biggest:,.0f} ohm). The coldest part of the curve could "
                "not be measured at all.")

    if units == 'OHMS' and stats['r_min'] > 0 and \
            stats['r_max'] / stats['r_min'] > 20:
        warnings.append(
            "This resistance curve spans more than a factor of 20. Lake "
            "Shore ships its own Cernox tables as log ohm/K (format 4) for "
            "exactly this reason: the instrument interpolates between "
            "breakpoints, and in plain ohms the curve is steep enough at the "
            "cold end for that to lose accuracy.")

    if working_range:
        wanted_low, wanted_high = working_range
        stats['working_range'] = (wanted_low, wanted_high)
        if wanted_low < stats['t_min']:
            warnings.append(
                f"This curve stops at {stats['t_min']:.4g} K, but the sensor "
                f"is to be used down to {wanted_low:.4g} K. Between "
                f"{wanted_low:.4g} K and {stats['t_min']:.4g} K the "
                "instrument has no curve to read and the input will show a "
                "sensor-units reading or an error, not a temperature. "
                "Nothing here can invent that data. Use a source file that "
                "reaches lower, have the sensor recalibrated, or use a "
                "second thermometer for the cold end.")
        if wanted_high > stats['t_max']:
            warnings.append(
                f"This curve stops at {stats['t_max']:.4g} K, but the sensor "
                f"is to be used up to {wanted_high:.4g} K. Above "
                f"{stats['t_max']:.4g} K the input has no curve to read.")

    return errors, warnings, stats


# ===============================================================================
# THE COMMANDS THEMSELVES
# ===============================================================================
#
# Built as a plain list of strings, so exactly what will be sent can be shown
# in the window, saved to a file and checked by a test without an instrument
# being anywhere near it.

# Sending a curve without erasing the slot first leaves whatever the previous
# curve held beyond the new point count. A 200-point curve replaced by a
# 134-point one keeps points 135-200 of the old calibration, and the
# instrument interpolates straight through them past the cold end of the new
# curve. CRVDEL is therefore part of the transfer by default.
ERASE_FIRST_DEFAULT = True


def crvhdr_command(curve, name, serial, fmt_code, limit, coefficient):
    """The CRVHDR line, exactly as it goes on the wire."""
    return (f"CRVHDR {int(curve)},{name},{serial},{int(fmt_code)},"
            f"{fmt_limit(limit)},{int(coefficient)}")


def crvpt_command(curve, index, reading, temperature):
    """One CRVPT line. Sensor reading first, temperature second."""
    return f"CRVPT {int(curve)},{int(index)},{fmt6(reading)},{fmt6(temperature)}"


def build_curve_commands(model, curve, name, serial, fmt_code, limit,
                         coefficient, points, erase_first=ERASE_FIRST_DEFAULT):
    """Every command needed to install one user curve, in order.

    Returns a list of strings:
        CRVDEL <curve>                     (when erase_first)
        CRVHDR <curve>,<name>,<SN>,<format>,<limit>,<coefficient>
        CRVPT  <curve>,1,<reading>,<temp>
        ...
        CRVSAV                             (Model 340 only)

    Points are indexed in ascending sensor reading, which is the order the
    instrument interpolates along and the order a Lake Shore .340 file is
    already in.
    """
    spec = model_spec(model)
    curve = int(curve)
    if not (spec['user_curve_min'] <= curve <= spec['user_curve_max']):
        raise ValueError(
            f"User curve {curve} does not exist on a {spec['short']}: it has "
            f"{spec['user_curve_min']} to {spec['user_curve_max']}.")
    if not points:
        raise ValueError("A curve with no points cannot be sent.")
    if len(points) > MAX_CURVE_POINTS:
        raise ValueError(
            f"{len(points)} points is more than the {MAX_CURVE_POINTS} a "
            "user curve holds.")
    if fmt_code not in spec['formats']:
        raise ValueError(
            f"Data format {fmt_code} is not available on a {spec['short']}.")

    ordered = sorted(points, key=lambda pair: pair[1])
    commands = []
    if erase_first:
        commands.append(f"CRVDEL {curve}")
    commands.append(crvhdr_command(curve, name, serial, fmt_code, limit,
                                   coefficient))
    for index, (temperature, reading) in enumerate(ordered, start=1):
        commands.append(crvpt_command(curve, index, reading, temperature))
    if spec['needs_crvsav']:
        commands.append("CRVSAV")
    return commands


def command_file_text(model, commands):
    """The saved record of a transfer: a header comment and the commands.

    Plain ASCII, one command per line, with '#' comments the instrument never
    sees. This is not a file any Lake Shore tool loads; it is the record of
    exactly what was sent, which is what makes a transfer reviewable
    afterwards.
    """
    spec = model_spec(model)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# Lake Shore {spec['short']} user curve transfer",
        f"# written {stamp} by Sensor_Curve_Loader_L340_L350_GUI.py",
        f"# {len(commands)} commands, sent in this order, one per write",
    ]
    if spec['needs_crvsav']:
        lines.append("# the closing CRVSAV is what puts the curve in flash; "
                     "without it a 340 loses it on the next power cycle")
    lines.extend(commands)
    text = "\n".join(lines) + "\n"
    text.encode('ascii')          # raises rather than writing a bad file
    return text


# ===============================================================================
# READING IT BACK
# ===============================================================================


def parse_crvhdr_reply(text, source_name="CRVHDR?"):
    """Read a 'CRVHDR? <curve>' reply.

    Returned by both models as
        <name>,<SN>,<format>,<limit value>,<coefficient>
    with the name space-padded to 15 and the serial to 10. The padding is
    stripped; the numerals are also kept as printed, because how many digits
    the instrument prints is the limit on how closely anything can be
    checked.
    """
    body = str(text).strip()
    if not body:
        raise CurveFileError(f"{source_name} returned nothing.")
    fields = [field.strip() for field in body.split(',')]
    if len(fields) < 5:
        raise CurveFileError(
            f"{source_name} returned {len(fields)} field(s), not the five a "
            f"curve header has: '{body}'.")
    try:
        fmt_code = int(float(fields[2]))
        limit = float(fields[3])
        coefficient = int(float(fields[4]))
    except ValueError:
        raise CurveFileError(
            f"{source_name} returned a header whose format, limit or "
            f"coefficient is not a number: '{body}'.")
    return {
        'name': fields[0],
        'serial': fields[1],
        'format': fmt_code,
        'limit': limit,
        'coefficient': coefficient,
        'limit_text': fields[3],
        'raw': body,
    }


def header_is_empty(header):
    """True if a CRVHDR? reply describes an untouched user curve.

    A deleted or never-used slot answers with a blank name and a zero limit.
    That is how a free slot is told from an occupied one when the list of
    user curves is read.
    """
    return (not str(header.get('name', '')).strip()
            and float(header.get('limit', 0.0) or 0.0) == 0.0)


def parse_crvpt_reply(text, source_name="CRVPT?"):
    """Read a 'CRVPT? <curve>,<index>' reply.

    Returns (temperature, reading, (reading_text, temperature_text)) in the
    same (temperature, reading) order every other function here uses, plus
    the numerals exactly as printed so the comparison can be made at the
    precision the instrument actually offers.
    """
    body = str(text).strip()
    fields = [field.strip() for field in body.split(',')]
    if len(fields) < 2 or not _tokens_are_numeric(fields[:2]):
        raise CurveFileError(
            f"{source_name} returned '{body}', which is not a pair of "
            "numbers.")
    return float(fields[1]), float(fields[0]), (fields[0], fields[1])


def point_is_empty(temperature, reading):
    """True if a CRVPT? reply is an unset breakpoint.

    Both instruments answer an index past the end of a curve with zeroes
    rather than an error, so this is how the end of a curve is found and how
    a leftover tail from a longer previous curve is detected.
    """
    return temperature == 0.0 and reading == 0.0


def printed_tolerance(text):
    """Half a unit in the last decimal place a number was printed to.

    A readback can only be checked as closely as the instrument prints. If it
    answers '1.6452' for a value sent as '1.64523', the two agree as well as
    that reply can express, and calling it a mismatch would be wrong. If it
    answers '325' for 325.0 K, then this check cannot see an error smaller
    than half a kelvin, and saying so is more use than a tolerance invented
    here.

    Returns None for exponent notation, where the last-place argument does
    not hold; the caller falls back to a relative tolerance.
    """
    body = str(text).strip()
    if 'e' in body.lower():
        return None
    decimals = len(body.split('.', 1)[1]) if '.' in body else 0
    return 0.5 * 10.0 ** (-decimals)


def compare_curves(sent_points, read_points, read_texts=None,
                   relative_tolerance=1e-6):
    """Compare the curve that was sent with the curve read back.

    Both lists are sorted by sensor reading, which is the order they are
    indexed in.

    Where `read_texts` is given -- the numerals as the instrument printed
    them -- each point is checked against the precision of its own reply
    rather than against a fixed tolerance. That matters in both directions:

      * too tight a fixed tolerance fails a perfectly good curve whenever the
        instrument prints fewer digits than were sent, and a check that cries
        wolf is one the operator learns to ignore;
      * too loose a fixed tolerance passes a real error. A relative tolerance
        of 1e-4 on a log-ohm value sounds tiny, but at 300 K on this Cernox
        it is about 0.18 K, which is not a rounding artefact by any standard
        worth applying to a thermometer.
    """
    sent = sorted(sent_points, key=lambda pair: pair[1])
    read = sorted(read_points, key=lambda pair: pair[1])
    result = {
        'sent_count': len(sent),
        'read_count': len(read),
        'matched': False,
        'worst_reading_error': 0.0,
        'worst_temperature_error': 0.0,
        'worst_point': None,
        'worst_reading_limit': 0.0,
        'worst_temperature_limit': 0.0,
        'problems': [],
    }
    if len(sent) != len(read):
        result['problems'].append(
            f"{len(sent)} points were sent but {len(read)} came back.")
        return result

    texts = read_texts if read_texts and len(read_texts) == len(read) else None
    if texts is not None:
        # read_texts arrives in the order the points were read; the points
        # were sorted, so the texts are sorted the same way to stay paired
        # with them.
        texts = [pair for _, pair in
                 sorted(zip([r for _, r in read_points], read_texts),
                        key=lambda item: item[0])]

    worst_overall = -1.0
    for index in range(len(sent)):
        sent_t, sent_r = sent[index]
        read_t, read_r = read[index]
        reading_gap = abs(sent_r - read_r)
        temperature_gap = abs(sent_t - read_t)

        reading_limit = temperature_limit = None
        if texts is not None:
            reading_limit = printed_tolerance(texts[index][0])
            temperature_limit = printed_tolerance(texts[index][1])
        if reading_limit is None:
            reading_limit = abs(sent_r) * relative_tolerance
        if temperature_limit is None:
            temperature_limit = abs(sent_t) * relative_tolerance

        result['worst_reading_limit'] = max(result['worst_reading_limit'],
                                            reading_limit)
        result['worst_temperature_limit'] = max(
            result['worst_temperature_limit'], temperature_limit)

        reading_error = reading_gap / max(abs(sent_r), 1e-12)
        temperature_error = temperature_gap / max(abs(sent_t), 1e-12)
        result['worst_reading_error'] = max(result['worst_reading_error'],
                                            reading_error)
        result['worst_temperature_error'] = max(
            result['worst_temperature_error'], temperature_error)
        if max(reading_error, temperature_error) > worst_overall:
            worst_overall = max(reading_error, temperature_error)
            result['worst_point'] = index + 1

        if reading_gap > reading_limit or temperature_gap > temperature_limit:
            if len(result['problems']) < 8:
                result['problems'].append(
                    f"Point {index + 1}: sent {fmt6(sent_r)} -> "
                    f"{fmt6(sent_t)} K, read back {fmt6(read_r)} -> "
                    f"{fmt6(read_t)} K.")

    result['matched'] = not result['problems']
    return result


def compare_headers(expected, header):
    """Which of the five CRVHDR fields survived the round trip.

    Returns a dict of field -> (sent, read, matched). Strings are compared
    case-insensitively after stripping, because the instrument pads them to a
    fixed width and is free to echo its own casing. The limit is compared at
    the precision it was printed to, for the same reason every point is.
    """
    fields = {}

    for key in ('name', 'serial'):
        sent = str(expected.get(key, '')).strip()
        read = str(header.get(key, '')).strip()
        fields[key] = (sent, read, sent.upper() == read.upper())

    sent_format = int(expected.get('format', 0))
    read_format = header.get('format')
    fields['format'] = (str(sent_format), str(read_format),
                        read_format == sent_format)

    sent_coefficient = int(expected.get('coefficient', 0))
    read_coefficient = header.get('coefficient')
    fields['coefficient'] = (str(sent_coefficient), str(read_coefficient),
                             read_coefficient == sent_coefficient)

    sent_limit = float(expected.get('limit', 0.0))
    read_limit = header.get('limit')
    tolerance = printed_tolerance(header.get('limit_text', '')) or 1e-3
    matched = (read_limit is not None and
               abs(float(read_limit) - sent_limit) <= tolerance)
    fields['limit'] = (
        fmt_limit(sent_limit),
        ("" if read_limit is None else f"{float(read_limit):g}"),
        matched)
    return fields


def classify_verify(model, expected, header, comparison, tail_index=None,
                    baseline_header=None):
    """Work out WHAT went wrong, not just THAT something did.

    Returns (verdict, headline, advice) where verdict is one of:
        'verified'      header, every point, and the point after the last
        'not_written'   the slot holds what it held before; nothing landed
        'format_only'   the curve landed but the format code came back
                        different, so every reading is being interpreted in
                        the wrong units
        'points'        the header is right but points differ or are missing
        'tail'          the header and the points are right, but the slot
                        still holds points past the end of the new curve
        'mixed'         several header fields differ, no clean story

    `tail_index` is the index of the first point PAST the curve that came
    back non-empty, or None if the point after the last one was empty as it
    should be.
    """
    spec = model_spec(model)
    fields = compare_headers(expected, header)
    header_ok = all(matched for _, _, matched in fields.values())
    points_ok = comparison['matched']

    if header_ok and points_ok and tail_index is None:
        return ('verified', "", "")

    identical_to_baseline = False
    if baseline_header:
        identical_to_baseline = all(
            str(baseline_header.get(key, '')).strip().upper() ==
            str(header.get(key, '')).strip().upper()
            for key in ('name', 'serial', 'format'))

    wholly_different = (not fields['name'][2] and not fields['serial'][2]
                        and not fields['format'][2])

    if identical_to_baseline or wholly_different:
        if identical_to_baseline:
            headline = (
                "NOTHING WAS WRITTEN. The curve reads back exactly as it did "
                "before the send: same name, same serial, same format.")
        else:
            headline = (
                "NOTHING WAS WRITTEN. The name, the serial and the format "
                "all came back different, so the slot still holds another "
                "curve entirely.")
        advice = (
            "The point count is NOT evidence here: those points belong to "
            "whatever was already in the slot. Check, in this order: (1) the "
            f"curve number is inside {spec['user_curve_min']}-"
            f"{spec['user_curve_max']}, which is the user range on a "
            f"{spec['short']} -- a standard curve number is read-only and a "
            "write to it is discarded; (2) the name is at most "
            f"{MAX_NAME_CHARS} characters and the serial at most "
            f"{MAX_SERIAL_CHARS}, with no comma or semicolon in either; "
            f"(3) the data format is one the {spec['short']} has.")
        if spec['needs_crvsav']:
            advice += (" On a 340 also check the console shows CRVSAV going "
                       "out and BUSY? clearing afterwards.")
        return ('not_written', headline, advice)

    if (not fields['format'][2]) and fields['name'][2] and fields['serial'][2]:
        sent_code = int(expected.get('format', 0))
        read_code = header.get('format')
        headline = (
            f"The curve landed, but the data format came back as {read_code} "
            f"where {sent_code} was sent. The name and the serial survived, "
            "so the header was parsed and only the format was changed.")
        advice = (
            f"Format {sent_code} is "
            f"{FORMAT_CODES.get(sent_code, ('', 'unknown'))[1]} and format "
            f"{read_code} is "
            f"{FORMAT_CODES.get(read_code, ('', 'unknown'))[1]}. Every "
            "reading in this curve is now being interpreted in the wrong "
            "units, which gives a plausible temperature that is not the "
            f"right one. Check that the {spec['short']} has format "
            f"{sent_code} at all -- it takes "
            f"{', '.join(str(code) for code in spec['formats'])} -- and "
            "resend.")
        return ('format_only', headline, advice)

    if header_ok and points_ok and tail_index is not None:
        headline = (
            f"The header and all {comparison['sent_count']} points are "
            f"right, but point {tail_index} still holds data. The slot was "
            "not erased, so the tail of a longer previous curve is still "
            "there.")
        advice = (
            "The instrument will interpolate straight through those leftover "
            "points past the end of this curve, using somebody else's "
            "calibration. Send again with 'erase the curve first' switched "
            f"on, or send CRVDEL {expected.get('curve', '<n>')} and repeat "
            "the transfer.")
        return ('tail', headline, advice)

    if header_ok and not points_ok:
        if comparison['read_count'] != comparison['sent_count']:
            headline = (
                f"The header is correct but {comparison['sent_count']} "
                f"points were sent and {comparison['read_count']} came back.")
            advice = (
                "The header arrived, so the commands are being parsed and "
                "the loss is in the CRVPT lines. Raise the gap between "
                "commands under Advanced and send again; if the same index "
                "is always the missing one, check that point in the source "
                "file.")
        else:
            headline = ("The header is correct but some points differ from "
                        "what was sent.")
            advice = ("The differences are listed above. Do not use this "
                      "sensor: an interpolation table that is wrong in the "
                      "middle gives a plausible temperature that is not the "
                      "right one.")
        return ('points', headline, advice)

    differing = [name for name, (_, _, matched) in fields.items()
                 if not matched]
    headline = ("Header fields that did not survive: " +
                ", ".join(differing) + ".")
    advice = ("This does not match a known failure mode, so nothing here "
              "should be guessed at. Read the curve back with the inspect "
              "button, compare it with the command file on disk, and check "
              "the raw replies printed above before sending anything else.")
    return ('mixed', headline, advice)


# ===============================================================================
# THE INSTRUMENT LINK
# ===============================================================================
#
# Session handling follows T_Control_L350_DirectControl_GUI.py, which is what
# this lab's Lake Shore work already runs on: line-feed terminated, ten-second
# timeout, and a plain close on disconnect that leaves the instrument
# controlling. The retry-on-first-IDN loop is copied from the Cryocon modules,
# where a write timeout on the first command after opening turned out to mean
# "wait and ask again" rather than "the instrument is absent".

PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")
IDN_SCAN_TIMEOUT_MS = 1200

LAKESHORE_TIMEOUT_MS = 10000        # per-operation VISA timeout
LAKESHORE_OPEN_SETTLE_S = 0.25      # pause after open, before first command
LAKESHORE_MIN_GAP_S = 0.05          # minimum gap between operations
LAKESHORE_CONNECT_ATTEMPTS = 3      # tries for the first '*IDN?'
LAKESHORE_RETRY_WAIT_S = 1.5        # pause between those tries

# Extra pacing for the curve transfer itself. A curve is up to 203 commands in
# a row, which is far more back-to-back traffic than any other operation these
# instruments see from this repository.
CURVE_COMMAND_GAP_S = 0.05
CURVE_DELETE_SETTLE_S = 0.5         # after CRVDEL, before the header
CRVSAV_POLL_S = 0.5                 # how often BUSY? is asked
CRVSAV_TIMEOUT_S = 60.0             # "may take several seconds"; this is ample

# How often the window drains the worker-event queue. Fast enough that the
# console keeps up with a curve going out command by command, slow enough to
# be invisible.
EVENT_POLL_MS = 50


class LakeshoreLink:
    """One paced VISA session to a Lake Shore controller, opened with retries."""

    def __init__(self, visa_address, timeout_ms=LAKESHORE_TIMEOUT_MS,
                 log=None):
        # Gate on the module object, not on the import-time flag: the test
        # harness swaps in a fake pyvisa after import, and a flag frozen at
        # import would lock it out.
        if pyvisa is None:
            raise ConnectionError(
                "PyVISA is not available. Install pyvisa and a VISA backend "
                "(NI-VISA or pyvisa-py).")
        self.address = visa_address
        self.timeout_ms = timeout_ms
        self.instrument = None
        self.idn = ""
        self._log = log if callable(log) else (lambda msg: print(msg))
        self._last_io = 0.0
        self.rm = pyvisa.ResourceManager()
        self._open_and_identify()

    # -- session handling --

    def _drop_session(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception:
                pass
            finally:
                self.instrument = None

    def _open_and_identify(self):
        last_error = None
        for attempt in range(1, LAKESHORE_CONNECT_ATTEMPTS + 1):
            try:
                self.instrument = self.rm.open_resource(self.address)
                self.instrument.timeout = self.timeout_ms
                self.instrument.read_termination = '\n'
                self.instrument.write_termination = '\n'
                time.sleep(LAKESHORE_OPEN_SETTLE_S)
                self.idn = self.query('*IDN?')
                if not self.idn:
                    raise ConnectionError(
                        f"{self.address} accepted the command but sent no "
                        "identification.")
                if attempt > 1:
                    self._log(f"  The instrument answered on attempt "
                              f"{attempt}.")
                return
            except Exception as exc:
                last_error = exc
                self._drop_session()
                if attempt < LAKESHORE_CONNECT_ATTEMPTS:
                    self._log(
                        f"  No answer at {self.address} (attempt {attempt} of "
                        f"{LAKESHORE_CONNECT_ATTEMPTS}): "
                        f"{type(exc).__name__}. Retrying in "
                        f"{LAKESHORE_RETRY_WAIT_S:.1f} s.")
                    time.sleep(LAKESHORE_RETRY_WAIT_S)
        raise ConnectionError(
            f"No reply from a Lake Shore at {self.address} after "
            f"{LAKESHORE_CONNECT_ATTEMPTS} attempts. Last error: "
            f"{last_error}. Check that the instrument is powered, that its "
            "interface menu has IEEE-488 selected, and that its address "
            "matches this VISA address.")

    # -- paced I/O --

    def _pace(self, gap=None):
        """Hold a minimum gap between operations.

        The gap is looked up when it is needed, not bound as a default
        argument at import, so slowing the bus down for a sulky instrument --
        or speeding it up under test -- is not silently ignored.
        """
        gap = LAKESHORE_MIN_GAP_S if gap is None else gap
        wait = gap - (time.time() - self._last_io)
        if wait > 0:
            time.sleep(wait)

    def query(self, command, gap=None):
        if self.instrument is None:
            raise ConnectionError("Not connected to the instrument.")
        self._pace(gap)
        try:
            reply = self.instrument.query(command)
        finally:
            self._last_io = time.time()
        return reply.strip()

    def write(self, command, gap=None):
        if self.instrument is None:
            raise ConnectionError("Not connected to the instrument.")
        self._pace(gap)
        try:
            self.instrument.write(command)
        finally:
            self._last_io = time.time()

    def reconnect(self):
        """Drop the session and open a fresh one. Sends nothing but '*IDN?'."""
        self._drop_session()
        time.sleep(LAKESHORE_RETRY_WAIT_S)
        self._open_and_identify()

    @property
    def is_connected(self):
        return self.instrument is not None

    def close(self):
        """Close the session only. No *RST, no heater, loop or setpoint
        command, so whatever is driving the cryostat carries on."""
        self._drop_session()


class CurveLoaderBackend:
    """Everything this module says to a Lake Shore Model 340 or 350.

    Read-only except for send_curve(), delete_curve() and
    assign_curve_to_input(), each of which is driven by its own button.
    """

    def __init__(self, log=None):
        self.link = None
        self.rm = None
        self.model = None            # set by connect(), from '*IDN?'
        self.log = log if callable(log) else (lambda msg: print(msg))
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as exc:
                print(f"Could not initialize VISA: {exc}")
                self.rm = None

    # -- connection ---------------------------------------------------------

    def scan_resources(self):
        if not self.rm:
            return []
        return list(self.rm.list_resources())

    def identify_resources(self, resources):
        """Return {resource: idn} for every resource that answers '*IDN?'.

        Never raises: an address that is busy, silent or not SCPI simply does
        not appear. Serial resources are not probed, because on a Windows rack
        ASRL1 is as likely to be a UPS as an instrument and a '*IDN?' there
        blocks for the whole timeout.
        """
        found = {}
        if not self.rm:
            return found
        for resource in resources:
            if not str(resource).upper().startswith(PROBE_RESOURCE_PREFIXES):
                continue
            instrument = None
            try:
                instrument = self.rm.open_resource(resource)
                instrument.timeout = IDN_SCAN_TIMEOUT_MS
                instrument.read_termination = '\n'
                instrument.write_termination = '\n'
                idn = instrument.query('*IDN?').strip()
                if idn:
                    found[resource] = idn
            except Exception:
                pass
            finally:
                if instrument is not None:
                    try:
                        instrument.close()
                    except Exception:
                        pass
        return found

    def connect(self, visa_address, expected_model=None):
        """Open a session and settle which model is on the other end.

        Returns (idn, model). The model is taken from '*IDN?' and nothing
        else. `expected_model`, when given, is what the operator picked in the
        window: a disagreement closes the session rather than resolving it,
        because the slot range, the legal formats and the need for CRVSAV all
        follow from the model and getting it wrong writes a curve that is not
        there.
        """
        if not self.rm:
            raise ConnectionError(
                "PyVISA ResourceManager not available. Install pyvisa and a "
                "VISA backend (NI-VISA or pyvisa-py).")
        self.link = LakeshoreLink(visa_address, log=self.log)
        idn = self.link.idn
        model = model_from_idn(idn)
        if model is None:
            self.disconnect()
            raise ConnectionError(
                f"{visa_address} identifies itself as '{idn}', which is not a "
                "Lake Shore Model 340 or Model 350. Refusing to send curve "
                "data.\n\n"
                + ("It does look like a Lake Shore, so this is probably "
                   "another model in the family; this module only knows the "
                   "340 and the 350."
                   if is_lakeshore_idn(idn) else
                   "Scan the bus and pick the controller's actual address."))
        if expected_model and expected_model != model:
            self.disconnect()
            raise ConnectionError(
                f"The window is set to a Model {expected_model}, but "
                f"{visa_address} identifies itself as a Model {model}: "
                f"'{idn}'.\n\n"
                "These are not interchangeable -- the user curve range, the "
                "data formats and whether CRVSAV is needed all differ -- so "
                "nothing was sent. Set the model to "
                f"{model}, or connect to the other instrument.")
        self.model = model
        return idn, model

    def disconnect(self):
        """Closes the VISA session and nothing else. The instrument keeps
        every setting and carries on."""
        if self.link:
            try:
                self.link.close()
            except Exception as exc:
                print(f"  Warning during disconnect: {exc}")
            finally:
                self.link = None
        self.model = None

    @property
    def is_connected(self):
        return self.link is not None and self.link.is_connected

    def _require_link(self):
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        return self.link

    def _spec(self):
        if not self.model:
            raise ConnectionError(
                "The model has not been established yet. Connect first: it "
                "is read from '*IDN?'.")
        return model_spec(self.model)

    # -- the transfer -------------------------------------------------------

    def wait_until_idle(self, timeout_s=CRVSAV_TIMEOUT_S, progress=None):
        """Poll 'BUSY?' until the instrument says it is finished.

        The 340 manual asks for this after CRVSAV: "May take several seconds;
        use the BUSY? command to determine when complete." A firmware that
        does not answer BUSY? is not an error -- the command is undocumented
        on the 350 and this module never sends CRVSAV there -- so a failure
        falls back to a fixed wait and says so.

        Returns True if BUSY? reported idle, False if it had to be waited out.
        """
        link = self._require_link()
        deadline = time.time() + timeout_s
        asked = 0
        while time.time() < deadline:
            try:
                reply = link.query("BUSY?")
            except Exception as exc:
                self.log(f"  BUSY? was not answered ({type(exc).__name__}); "
                         f"waiting {CRVSAV_TIMEOUT_S / 10:.0f} s instead.")
                time.sleep(CRVSAV_TIMEOUT_S / 10)
                return False
            asked += 1
            try:
                busy = int(float(str(reply).strip().split(',')[0]))
            except (TypeError, ValueError):
                self.log(f"  BUSY? answered '{reply}', which is not a number; "
                         "treating the instrument as finished.")
                return False
            if busy == 0:
                if asked > 1:
                    self.log(f"  BUSY? cleared after {asked} asks.")
                return True
            if progress:
                progress(asked)
            time.sleep(CRVSAV_POLL_S)
        raise TimeoutError(
            f"BUSY? was still set {timeout_s:.0f} s after CRVSAV. The curve "
            "may not have reached flash. Do not power the instrument off; "
            "read the curve back and check it.")

    def delete_curve(self, curve):
        """CRVDEL one user curve, after checking it is a user curve."""
        link = self._require_link()
        spec = self._spec()
        curve = int(curve)
        if not (spec['user_curve_min'] <= curve <= spec['user_curve_max']):
            raise ValueError(
                f"Curve {curve} is not a user curve on a {spec['short']} "
                f"({spec['user_curve_min']}-{spec['user_curve_max']}). "
                "Standard curves cannot be deleted.")
        link.write(f"CRVDEL {curve}")
        time.sleep(CURVE_DELETE_SETTLE_S)
        if spec['needs_crvsav']:
            # CRVDEL is a curve edit like any other on a 340, so it is not in
            # flash either until CRVSAV. Sending it here means an erase that
            # is followed by nothing still leaves a genuinely empty slot.
            link.write("CRVSAV")
            self.wait_until_idle()

    def send_curve(self, commands, progress=None, should_stop=None):
        """Send a prepared list of commands, one VISA write each.

        `commands` is what build_curve_commands() returned. Nothing is
        composed here: what is sent is exactly what was shown in the window
        and saved to the command file.

        A CRVSAV in the list is followed by the BUSY? wait, because until
        that clears the 340 has not finished writing flash and a readback
        would be racing it.
        """
        link = self._require_link()
        if not commands:
            raise ValueError("There are no commands to send.")
        for position, command in enumerate(commands):
            if should_stop is not None and should_stop():
                raise RuntimeError(
                    f"Stopped after {position} of {len(commands)} commands. "
                    "The curve is now partial: erase it and send it again "
                    "before using the sensor.")
            link.write(command, gap=CURVE_COMMAND_GAP_S)
            if command.strip().upper().startswith("CRVDEL"):
                time.sleep(CURVE_DELETE_SETTLE_S)
            if command.strip().upper() == "CRVSAV":
                self.log("  CRVSAV sent; waiting for BUSY? to clear before "
                         "reading anything back.")
                self.wait_until_idle()
            if progress:
                progress(position + 1, len(commands), command)
        return len(commands)

    # -- reading it back ----------------------------------------------------

    def read_curve_header(self, curve):
        """CRVHDR? one curve. Works on standard curves too, which is how a
        target is inspected before anything is written to it."""
        link = self._require_link()
        spec = self._spec()
        curve = int(curve)
        if not (spec['query_curve_min'] <= curve <= spec['query_curve_max']):
            raise ValueError(
                f"Curve {curve} cannot be queried on a {spec['short']} "
                f"({spec['query_curve_min']}-{spec['query_curve_max']}).")
        return parse_crvhdr_reply(link.query(f"CRVHDR? {curve}"),
                                  f"CRVHDR? {curve}")

    def read_curve_point(self, curve, index):
        """CRVPT? one point. Returns (temperature, reading, texts)."""
        link = self._require_link()
        return parse_crvpt_reply(link.query(f"CRVPT? {int(curve)},{int(index)}"),
                                 f"CRVPT? {curve},{index}")

    def read_curve(self, curve, max_points=MAX_CURVE_POINTS, progress=None,
                   expected_count=None):
        """Read a whole curve back.

        Returns (header, points, texts, tail_index):
            header      the parsed CRVHDR? reply
            points      [(temperature, reading), ...] up to the first empty
            texts       [(reading_text, temperature_text), ...], paired
            tail_index  the index of the first non-empty point AFTER
                        `expected_count`, or None

        The tail check is why `expected_count` exists. CRVHDR and CRVPT do not
        shorten a curve, so a slot that held 200 points and was overwritten by
        134 keeps points 135-200 of the old calibration. Reading one point
        past the end of the new curve is what catches that, and it costs one
        query.
        """
        header = self.read_curve_header(curve)
        points = []
        texts = []
        for index in range(1, max_points + 1):
            temperature, reading, printed = self.read_curve_point(curve, index)
            if point_is_empty(temperature, reading):
                break
            points.append((temperature, reading))
            texts.append(printed)
            if progress:
                progress(index, expected_count or max_points)

        tail_index = None
        if expected_count is not None and len(points) > expected_count:
            tail_index = expected_count + 1
        return header, points, texts, tail_index

    def list_curves(self, first=None, last=None, progress=None):
        """CRVHDR? a range of curves and report what each one holds.

        READ-ONLY: one query per curve and not a single write. The whole user
        block is forty queries on a 340 and thirty-nine on a 350, a few
        seconds either way; adding the twenty standard curves takes it to
        sixty. Safe to run with a cryostat controlling.

        `first` and `last` default to the model's user block. Passing the
        query range instead maps the whole instrument, standard curves
        included -- those are read-only, so they appear in the map marked as
        such and are never offered as a target.

        Each entry is a dict with 'curve', the parsed header fields, 'empty',
        'standard', and 'error' where the curve would not answer. An entry
        that failed is kept rather than dropped, because a gap in the map is
        itself worth seeing.
        """
        spec = self._spec()
        first = spec['user_curve_min'] if first is None else int(first)
        last = spec['user_curve_max'] if last is None else int(last)
        entries = []
        total = max(1, last - first + 1)
        for offset, curve in enumerate(range(first, last + 1)):
            entry = {'curve': curve,
                     'standard': curve < spec['user_curve_min']}
            try:
                header = self.read_curve_header(curve)
                entry.update(header)
                entry['empty'] = header_is_empty(header)
            except Exception as exc:
                entry['error'] = f"{type(exc).__name__}: {exc}"
                entry['empty'] = None
            entries.append(entry)
            if progress:
                progress(offset + 1, total, entry)
        return entries

    def list_user_curves(self, progress=None):
        """CRVHDR? every user curve, so a free one can be picked by name."""
        return self.list_curves(progress=progress)

    # -- inputs -------------------------------------------------------------

    def get_input_type(self, channel):
        """INTYPE? one input. Returns (type_code, raw_reply).

        The type code is the first field on both models. It is the only field
        this module reads, because it is the only one that decides whether
        INCRV will accept a resistance curve.
        """
        link = self._require_link()
        raw = link.query(f"INTYPE? {channel}")
        try:
            code = int(float(str(raw).split(',')[0].strip()))
        except (TypeError, ValueError):
            return None, raw
        return code, raw

    def get_input_curve(self, channel):
        """INCRV? one input. Returns the curve number, or None."""
        link = self._require_link()
        raw = link.query(f"INCRV? {channel}")
        try:
            return int(float(str(raw).strip()))
        except (TypeError, ValueError):
            return None

    def assign_curve_to_input(self, channel, curve):
        """INCRV, then INCRV? to see whether it stuck.

        Both manuals: a curve whose parameters do not match the input type is
        silently replaced by curve 0. So the readback is not a formality, it
        is the only way to find out that the assignment was refused.
        """
        link = self._require_link()
        spec = self._spec()
        if channel not in spec['inputs']:
            raise ValueError(
                f"Input must be one of {', '.join(spec['inputs'])} on a "
                f"{spec['short']}, not {channel!r}.")
        link.write(f"INCRV {channel},{int(curve)}")
        time.sleep(LAKESHORE_MIN_GAP_S)
        return self.get_input_curve(channel)

    def read_input_temperature(self, channel):
        """KRDG? and SRDG? for one input, as a plain string pair.

        Both are read because they fail differently: a sensor with no curve
        still gives a sensor-units reading, so an SRDG? that looks right
        beside a KRDG? that does not is the signature of a curve that was
        never accepted.
        """
        link = self._require_link()
        readings = {}
        for key, command in (('kelvin', f"KRDG? {channel}"),
                             ('sensor', f"SRDG? {channel}")):
            try:
                readings[key] = link.query(command)
            except Exception as exc:
                readings[key] = f"<no answer: {type(exc).__name__}>"
        return readings


# ===============================================================================
# GUI
# ===============================================================================

MODEL_UNSET = "(not set — connect and it is read from *IDN?)"


class CurveLoaderGUI:
    """Install a Lake Shore calibration file as a Model 340 / 350 user curve.

    The left panel is the job in order: pick a file, check what came out of
    it, save it, connect, send it, verify it, put it on an input. The right
    panel always shows what would be sent, so nothing goes to the instrument
    unseen.
    """

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Lake Shore 340 / 350 Sensor Curve Loader"

    # Colour scheme, shared with the sibling Lakeshore and Cryocon modules.
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    CLR_STATUS_OK = '#6B8E4E'
    CLR_STATUS_BAD = '#BA6B5E'
    CLR_STATUS_WARN = '#B07D2E'

    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 12, 'bold')
    FONT_HEADLINE = ('Segoe UI', 15, 'bold')
    FONT_SMALL = ('Segoe UI', 9)

    LEFT_PANEL_WIDTH = 580

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x960")
        self.root.minsize(1200, 780)
        self.root.configure(bg=self.CLR_BG_DARK)

        # Everything a worker thread wants the window to do goes through this
        # queue and is carried out by _drain_events() on the Tk thread.
        # Tkinter is not thread-safe, and root.after() is not an escape from
        # that: called from a worker it raises 'main thread is not in main
        # loop' unless the main thread happens to be inside mainloop().
        self._events = queue.Queue()
        self.backend = CurveLoaderBackend(log=self.log)
        self.logo_image = None
        self.is_connected = False
        self.busy = False

        # The loaded file, and the curve derived from it.
        self.source = None
        self.source_path = ""
        self.second_source = None
        self.second_source_path = ""
        self.merge_notes = []
        self.curve_points = []       # (temperature, reading) in target units
        self.curve_commands = []     # exactly what would be sent
        self.curve_errors = []
        self.curve_warnings = []
        self.curve_stats = {}
        self.dropped_points = 0
        self.curve_list = None       # user curves, for the target picker
        self.curve_map = None        # everything read, for the map tab
        self.last_saved_path = ""

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._drain_events()         # starts the main-thread event pump
        self._describe_starting_point()

    # -----------------------------------------------------------------------
    # STYLES
    # -----------------------------------------------------------------------

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Connect.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Connect.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Disconnect.TButton', background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Disconnect.TButton',
                  background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('Send.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK,
                        font=('Segoe UI', 12, 'bold'), padding=(10, 12))
        style.map('Send.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor='#BA6B5E')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK)
        style.configure('TCombobox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Treeview', background=self.CLR_GRAPH_BG,
                        fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure('Treeview.Heading', background=self.CLR_HEADER,
                        foreground=self.CLR_TEXT_DARK,
                        font=('Segoe UI', 10, 'bold'))

    # -----------------------------------------------------------------------
    # WIDGETS
    # -----------------------------------------------------------------------

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(header, text=self.PROGRAM_NAME, style='Header.TLabel',
                  font=('Segoe UI', self.FONT_BASE[1] + 4, 'bold'),
                  foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)
        ttk.Button(header, text="\U0001F4C8",
                   command=launch_plotter_utility, width=3).pack(
            side='right', padx=10, pady=5)
        ttk.Button(header, text="\U0001F4DF",
                   command=launch_gpib_scanner, width=3).pack(
            side='right', padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        # sashpos() has no effect until the PanedWindow is mapped and laid
        # out, and an early call fails silently.
        try:
            self.root.update_idletasks()
            content_width = self.left_scrollable_frame.winfo_reqwidth()
            target = content_width + 30 if content_width > 1 \
                else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

    # -- left panel --

    def _populate_left_panel(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient='vertical',
                                  command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind('<Configure>',
                          lambda e: canvas.configure(
                              scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=scroll_frame,
                                         anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scroll_frame
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        canvas.bind_all('<MouseWheel>', _on_mousewheel)
        scroll_frame.grid_columnconfigure(0, weight=1)

        self._create_info_panel(scroll_frame, 0)
        self._create_file_panel(scroll_frame, 1)
        self._create_header_panel(scroll_frame, 2)
        self._create_save_panel(scroll_frame, 3)
        self._create_connection_panel(scroll_frame, 4)
        self._create_send_panel(scroll_frame, 5)
        self._create_input_panel(scroll_frame, 6)
        self._create_advanced_panel(scroll_frame, 7)
        self._create_console_panel(scroll_frame, 99)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        logo_size = 90
        logo_canvas = Canvas(frame, width=logo_size, height=logo_size,
                             bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            logo_path = pica_asset("LOGO", "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and logo_path:
                image = Image.open(logo_path).resize(
                    (logo_size, logo_size), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(image)
                logo_canvas.create_image(logo_size / 2, logo_size / 2,
                                         image=self.logo_image)
        except Exception:
            pass  # the logo is optional

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font,
                  background=self.CLR_FRAME_BG).grid(
            row=0, column=1, padx=10, pady=(20, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font,
                  background=self.CLR_FRAME_BG).grid(
            row=1, column=1, padx=10, pady=(0, 5), sticky='nw')
        ttk.Label(
            frame,
            text=("Model 340: user curves 21-60, and a curve is not in flash "
                  "until CRVSAV.\nModel 350: user curves 21-59, and writes "
                  "are permanent immediately.\nBoth hold 200 breakpoints per "
                  "curve."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=2, column=0, columnspan=2, padx=10,
                                 pady=(0, 10), sticky='w')

    def _create_file_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent,
                               text='Step 1  ·  Choose the sensor file')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text=("Pick the Lake Shore file for the sensor, for example\n"
                  "X17680.340 from the calibration CD. There is no '.350' on\n"
                  "that CD and none is needed: the .340 IS Lake Shore's\n"
                  "breakpoint format and a 350 stores the same table with\n"
                  "the same commands.\n\n"
                  "The .340 is the one to prefer: 129 breakpoints Lake Shore\n"
                  "placed for an instrument that interpolates between them,\n"
                  "already log-ohm, already reading-first, with the format\n"
                  "code, the setpoint limit and the coefficient in its\n"
                  "header."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(5, 5))

        ttk.Button(frame, text="Browse for a sensor file…",
                   command=self._choose_file).grid(
            row=1, column=0, sticky='ew', padx=10, pady=5)

        self.file_label = ttk.Label(
            frame, text="No file chosen yet.", background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9, 'italic'), wraplength=500, justify='left')
        self.file_label.grid(row=2, column=0, sticky='w', padx=10,
                             pady=(0, 8))

        ttk.Separator(frame, orient='horizontal').grid(
            row=3, column=0, sticky='ew', padx=10, pady=4)

        self.use_second_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Extend the ends of the curve from a second file",
            variable=self.use_second_var,
            command=self._toggle_second).grid(
            row=4, column=0, sticky='w', padx=10, pady=(4, 2))
        ttk.Label(
            frame,
            text=("Off by default. On, the second file's points BEYOND the\n"
                  "ends of the first are added and nothing inside the first\n"
                  "is touched. For X17680 that turns 129 points over the\n"
                  "certified 4.000-325.000 K into 134 points over\n"
                  "3.5913-330.027 K. The extra points are measured but they\n"
                  "are outside what the certificate covers, and the summary\n"
                  "says so."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=5, column=0, sticky='w',
                                 padx=10, pady=(0, 4))
        second_row = ttk.Frame(frame)
        second_row.grid(row=6, column=0, sticky='ew', padx=10, pady=(0, 4))
        second_row.grid_columnconfigure(0, weight=1)
        self.second_btn = ttk.Button(
            second_row, text="Choose the second file…",
            command=self._choose_second_file, state='disabled')
        self.second_btn.grid(row=0, column=0, sticky='ew')
        ttk.Button(second_row, text="Clear",
                   command=self._clear_second_file, width=7).grid(
            row=0, column=1, sticky='e', padx=(6, 0))
        self.second_file_label = ttk.Label(
            frame, text="Not using a second file.",
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9, 'italic'),
            wraplength=500, justify='left')
        self.second_file_label.grid(row=7, column=0, sticky='w',
                                    padx=10, pady=(0, 8))

    def _create_header_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 2  ·  How to store it')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("Every one of these except the name comes out of the .340\n"
                  "header when a .340 is loaded. Confirm them; do not retype\n"
                  "them from memory."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=0, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(6, 6))

        ttk.Label(frame, text="Curve name (15 max):").grid(
            row=1, column=0, sticky='w', padx=10, pady=4)
        self.name_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.name_var).grid(
            row=1, column=1, sticky='ew', padx=10, pady=4)
        self.name_var.trace_add('write', lambda *_: self._rebuild_curve())

        ttk.Label(frame, text="Serial number (10 max):").grid(
            row=2, column=0, sticky='w', padx=10, pady=4)
        self.serial_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.serial_var).grid(
            row=2, column=1, sticky='ew', padx=10, pady=4)
        self.serial_var.trace_add('write', lambda *_: self._rebuild_curve())

        ttk.Label(frame, text="Data format:").grid(
            row=3, column=0, sticky='w', padx=10, pady=4)
        self.format_var = tk.StringVar(value="")
        self.format_combo = ttk.Combobox(
            frame, textvariable=self.format_var,
            values=[f"{code} - {FORMAT_CODES[code][1]}"
                    for code in (1, 2, 3, 4)],
            state='readonly')
        self.format_combo.grid(row=3, column=1, sticky='ew', padx=10, pady=4)
        self.format_combo.bind('<<ComboboxSelected>>',
                               lambda *_: self._rebuild_curve())

        ttk.Label(frame, text="Temperature limit (K):").grid(
            row=4, column=0, sticky='w', padx=10, pady=4)
        self.limit_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.limit_var, width=12).grid(
            row=4, column=1, sticky='w', padx=10, pady=4)
        self.limit_var.trace_add('write', lambda *_: self._rebuild_curve())

        ttk.Label(frame, text="Temperature coefficient:").grid(
            row=5, column=0, sticky='w', padx=10, pady=4)
        self.coefficient_var = tk.StringVar(value="")
        self.coefficient_combo = ttk.Combobox(
            frame, textvariable=self.coefficient_var,
            values=[f"{code} - {label}"
                    for code, label in sorted(COEFFICIENT_NAMES.items())],
            state='readonly', width=18)
        self.coefficient_combo.grid(row=5, column=1, sticky='w', padx=10,
                                    pady=4)
        self.coefficient_combo.bind('<<ComboboxSelected>>',
                                    lambda *_: self._rebuild_curve())

        range_frame = ttk.Frame(frame)
        range_frame.grid(row=6, column=0, columnspan=2, sticky='w',
                         padx=10, pady=(8, 2))
        ttk.Label(range_frame, text="I will use this sensor from",
                  background=self.CLR_FRAME_BG).pack(side='left')
        self.use_low_var = tk.StringVar(value="")
        ttk.Entry(range_frame, textvariable=self.use_low_var,
                  width=7).pack(side='left', padx=4)
        ttk.Label(range_frame, text="K to",
                  background=self.CLR_FRAME_BG).pack(side='left')
        self.use_high_var = tk.StringVar(value="")
        ttk.Entry(range_frame, textvariable=self.use_high_var,
                  width=7).pack(side='left', padx=4)
        ttk.Label(range_frame, text="K",
                  background=self.CLR_FRAME_BG).pack(side='left')
        self.use_low_var.trace_add('write', lambda *_: self._rebuild_curve())
        self.use_high_var.trace_add('write', lambda *_: self._rebuild_curve())
        ttk.Label(
            frame,
            text=("Optional. Fill this in and the curve's coverage is checked\n"
                  "against it, so a gap at the cold end is said here rather\n"
                  "than found on the cryostat."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=7, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 6))

        ttk.Separator(frame, orient='horizontal').grid(
            row=8, column=0, columnspan=2, sticky='ew', padx=10, pady=4)

        ttk.Label(frame, text="Target user curve:").grid(
            row=9, column=0, sticky='w', padx=10, pady=4)
        self.curve_var = tk.StringVar(value="")
        self.curve_combo = ttk.Combobox(frame, textvariable=self.curve_var,
                                        values=[], state='readonly', width=40)
        self.curve_combo.grid(row=9, column=1, sticky='w', padx=10, pady=4)
        self.curve_combo.bind('<<ComboboxSelected>>',
                              lambda *_: self._refresh_curve_hint())
        self.curve_hint = ttk.Label(
            frame,
            text=("Set the model first: the 340 has curves 21-60 and the 350 "
                  "has 21-59, and a write to a number outside that is "
                  "discarded in silence."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9, 'italic'),
            wraplength=500, justify='left')
        self.curve_hint.grid(row=10, column=0, columnspan=2, sticky='w',
                             padx=10, pady=(0, 8))

    def _create_save_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent,
                               text='Step 3  ·  Save the command list')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)
        ttk.Label(
            frame,
            text=("Optional, but worth doing: the file is every command that\n"
                  "would be sent, in order, one per line. It is the record of\n"
                  "the transfer, and it can be read afterwards by anyone\n"
                  "asking what went into the instrument."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(5, 5))
        ttk.Button(frame, text="Save the command list…",
                   command=self._save_commands).grid(
            row=1, column=0, sticky='ew', padx=10, pady=(0, 8))

    def _create_connection_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 4  ·  Connect')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Model:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.model_var = tk.StringVar(value=MODEL_UNSET)
        self.model_combo = ttk.Combobox(
            frame, textvariable=self.model_var,
            values=[MODEL_UNSET] + [
                f"{key} - {MODEL_SPECS[key]['label']}" for key in MODEL_ORDER],
            state='readonly')
        self.model_combo.grid(row=0, column=1, sticky='ew', padx=10, pady=5)
        self.model_combo.bind('<<ComboboxSelected>>',
                              lambda *_: self._on_model_change())
        ttk.Label(
            frame,
            text=("Nothing can be sent until this is set. Connecting sets it\n"
                  "from *IDN?, and a manual choice that contradicts *IDN? is\n"
                  "refused rather than warned about: the curve range, the\n"
                  "data formats and whether CRVSAV is needed all follow from\n"
                  "it, and getting it wrong writes a curve that is not there."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=1, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 6))

        ttk.Label(frame, text="VISA address:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.visa_cb = ttk.Combobox(frame, font=self.FONT_BASE,
                                    state='readonly')
        self.visa_cb.grid(row=2, column=1, sticky='ew', padx=10, pady=5)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=3, column=0, columnspan=2, sticky='ew', pady=5)
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.connect_btn = ttk.Button(button_frame, text="Connect",
                                      style='Connect.TButton',
                                      command=self._do_connect)
        self.connect_btn.grid(row=0, column=0, sticky='ew', padx=5)
        self.disconnect_btn = ttk.Button(button_frame, text="Disconnect",
                                         style='Disconnect.TButton',
                                         state='disabled',
                                         command=self._do_disconnect)
        self.disconnect_btn.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(button_frame, text="Scan",
                   command=self._scan_visa).grid(row=0, column=2,
                                                 sticky='ew', padx=5)

        self.status_label = ttk.Label(
            frame, text="● Not connected", font=self.FONT_STATUS,
            foreground=self.CLR_STATUS_BAD, background=self.CLR_FRAME_BG)
        self.status_label.grid(row=4, column=0, columnspan=2, sticky='w',
                               padx=10, pady=(0, 5))

        ttk.Separator(frame, orient='horizontal').grid(
            row=5, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        ttk.Label(
            frame,
            text=("Mapping the curves sends one CRVHDR? per curve and not a\n"
                  "single write: no CRVDEL, no CRVPT, no loop, setpoint or\n"
                  "heater command. Safe with a cryostat controlling. What it\n"
                  "finds fills the 'What is on the instrument' tab on the\n"
                  "right, and the target picker below."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=6, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 4))
        self.map_standard_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text=("Include the standard curves 1-20 (read-only, never a "
                  "target)"),
            variable=self.map_standard_var).grid(
            row=7, column=0, columnspan=2, sticky='w', padx=10, pady=(0, 2))
        ttk.Button(frame, text="Map the curves (read only)",
                   command=self._list_curves).grid(
            row=8, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        ttk.Button(frame, text="Show every point of the chosen curve",
                   command=self._inspect_curve).grid(
            row=9, column=0, columnspan=2, sticky='ew', padx=10, pady=(0, 8))

    def _create_send_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 5  ·  Send and check')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        self.erase_var = tk.BooleanVar(value=ERASE_FIRST_DEFAULT)
        ttk.Checkbutton(
            frame, text="Erase the curve first (CRVDEL)",
            variable=self.erase_var,
            command=self._rebuild_curve).grid(
            row=0, column=0, sticky='w', padx=10, pady=(8, 0))
        ttk.Label(
            frame,
            text=("Leave this on. The target is always an empty curve, but\n"
                  "'empty' is judged from its header, and a transfer that was\n"
                  "interrupted can leave points behind a blank header. CRVDEL\n"
                  "costs one command and guarantees a clean slate; on an\n"
                  "already-empty curve it deletes nothing."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=1, column=0, sticky='w',
                                 padx=10, pady=(0, 6))

        self.verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Read the curve back afterwards and compare every point",
            variable=self.verify_var).grid(
            row=2, column=0, sticky='w', padx=10, pady=(0, 4))

        self.send_btn = ttk.Button(
            frame, text="Send the curve to the instrument",
            style='Send.TButton', command=self._send_curve)
        self.send_btn.grid(row=3, column=0, sticky='ew', padx=10, pady=6)

        ttk.Button(frame, text="Only read the curve back and compare",
                   command=self._verify_only).grid(
            row=4, column=0, sticky='ew', padx=10, pady=(0, 6))

        ttk.Separator(frame, orient='horizontal').grid(
            row=5, column=0, sticky='ew', padx=10, pady=6)
        ttk.Label(
            frame,
            text=("Or run every step in order, each one checked before the\n"
                  "next, stopping at the first failure. This is the one to\n"
                  "use: it reads the curve BEFORE sending, so a readback that\n"
                  "comes back unchanged is reported as 'nothing was written'\n"
                  "rather than as a point mismatch."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=6, column=0, sticky='w',
                                 padx=10, pady=(0, 4))
        self.sequence_assign_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text=("Include the input step: put the curve on the input chosen "
                  "in step 6"),
            variable=self.sequence_assign_var).grid(
            row=7, column=0, sticky='w', padx=10, pady=(0, 4))
        self.sequence_btn = ttk.Button(
            frame, text="Run the whole sequence and check every step",
            style='Send.TButton', command=self._run_full_sequence)
        self.sequence_btn.grid(row=8, column=0, sticky='ew', padx=10,
                               pady=(0, 6))

        self.progress = ttk.Progressbar(frame, mode='determinate')
        self.progress.grid(row=9, column=0, sticky='ew', padx=10, pady=(0, 8))

    def _create_input_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Step 6  ·  Put the curve on an input (optional)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("INCRV changes which curve an input uses to turn its\n"
                  "reading into a temperature. If the input's sensor type\n"
                  "does not match the curve, both manuals say the curve\n"
                  "number goes back to 0 WITHOUT AN ERROR, so this reads\n"
                  "INTYPE? first and INCRV? afterwards.\n\n"
                  "Nothing here touches a control loop, a setpoint or a\n"
                  "heater. This module never writes INTYPE: if the input is\n"
                  "on the wrong sensor type it says so and stops, because\n"
                  "changing an input's type changes what a running loop is\n"
                  "measuring."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=0, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(6, 4))

        ttk.Label(frame, text="Input:").grid(
            row=1, column=0, sticky='w', padx=10, pady=4)
        self.input_var = tk.StringVar(value='A')
        self.input_combo = ttk.Combobox(frame, textvariable=self.input_var,
                                        values=['A', 'B', 'C', 'D'],
                                        state='readonly', width=6)
        self.input_combo.grid(row=1, column=1, sticky='w', padx=10, pady=4)

        ttk.Button(frame, text="Show what each input is using now",
                   command=self._show_input_state).grid(
            row=2, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        ttk.Button(frame, text="Assign this curve to that input",
                   command=self._assign_input).grid(
            row=3, column=0, columnspan=2, sticky='ew', padx=10, pady=(4, 8))

    def _create_advanced_panel(self, parent, grid_row):
        outer = ttk.LabelFrame(parent, text='Advanced')
        outer.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        outer.grid_columnconfigure(0, weight=1)

        self.advanced_visible = False
        self.advanced_btn = ttk.Button(
            outer, text="Show advanced settings",
            command=self._toggle_advanced)
        self.advanced_btn.grid(row=0, column=0, sticky='ew', padx=10, pady=6)

        self.advanced_frame = ttk.Frame(outer)
        self.advanced_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(self.advanced_frame, text="Gap between commands (s):",
                  background=self.CLR_FRAME_BG).grid(
            row=0, column=0, sticky='w', padx=10, pady=4)
        self.gap_var = tk.StringVar(value=f"{CURVE_COMMAND_GAP_S:g}")
        ttk.Entry(self.advanced_frame, textvariable=self.gap_var,
                  width=10).grid(row=0, column=1, sticky='w', padx=10, pady=4)
        ttk.Button(self.advanced_frame, text="Apply",
                   command=self._apply_gap).grid(
            row=0, column=2, sticky='w', padx=10, pady=4)
        ttk.Label(
            self.advanced_frame,
            text=("A curve is up to 203 commands in a row. Raise this if the\n"
                  "readback shows points missing; it costs a few seconds and\n"
                  "nothing else."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=1, column=0, columnspan=3, sticky='w',
                                 padx=10, pady=(0, 6))

        ttk.Separator(self.advanced_frame, orient='horizontal').grid(
            row=2, column=0, columnspan=3, sticky='ew', padx=10, pady=4)
        ttk.Button(self.advanced_frame,
                   text="Run the built-in checks (no instrument needed)",
                   command=self._run_self_test).grid(
            row=3, column=0, columnspan=3, sticky='ew', padx=10, pady=4)
        ttk.Label(
            self.advanced_frame,
            text=("Checks on the file readers, the unit maths, the two "
                  "models'\nlimits, the command builder and the readback "
                  "classifier, run on\nmade-up data. Worth a press after any "
                  "edit to this file, and\nbefore a session on a cold "
                  "cryostat."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=4, column=0, columnspan=3, sticky='w',
                                 padx=10, pady=(0, 8))

    def _toggle_advanced(self):
        if self.advanced_visible:
            self.advanced_frame.grid_forget()
            self.advanced_btn.config(text="Show advanced settings")
        else:
            self.advanced_frame.grid(row=1, column=0, sticky='ew')
            self.advanced_btn.config(text="Hide advanced settings")
        self.advanced_visible = not self.advanced_visible

    def _apply_gap(self):
        global CURVE_COMMAND_GAP_S
        try:
            value = float(self.gap_var.get())
        except ValueError:
            messagebox.showerror("Not a Number",
                                 f"'{self.gap_var.get()}' is not a number of "
                                 "seconds.")
            return
        if not 0.0 <= value <= 2.0:
            messagebox.showerror(
                "Out of Range",
                "The gap between commands must be between 0 and 2 seconds. "
                "Two hundred commands at 2 s each would take seven minutes.")
            return
        CURVE_COMMAND_GAP_S = value
        self.log(f"Gap between commands set to {value:g} s. A "
                 f"{len(self.curve_points) or 129}-point curve will take "
                 f"about {(len(self.curve_points) or 129) * value:.0f} s to "
                 "send.")

    def _run_self_test(self):
        """Run the offline checks and print them in the console."""
        self.log("")
        passed = run_self_test(report=self.log)
        if passed:
            self._post('dialog', 'info', "Checks Passed",
                       "Every built-in check passed. The console has the "
                       "list.")
        else:
            self._post('dialog', 'error', "Checks Failed",
                       "At least one built-in check failed. The console names "
                       "them. Do not send a curve until this is resolved.")

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5, padx=10)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word',
            borderwidth=0, height=10)
        self.console.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)

    # -- right panel --

    def _populate_right_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        summary = ttk.LabelFrame(panel, text='What will be sent')
        summary.grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        summary.grid_columnconfigure(0, weight=1)

        self.headline_label = ttk.Label(
            summary, text="No curve loaded.", font=self.FONT_HEADLINE,
            background=self.CLR_FRAME_BG, wraplength=900, justify='left')
        self.headline_label.grid(row=0, column=0, sticky='w',
                                 padx=12, pady=(10, 4))
        self.detail_label = ttk.Label(
            summary, text="Choose a sensor file to begin.",
            background=self.CLR_FRAME_BG, wraplength=900, justify='left')
        self.detail_label.grid(row=1, column=0, sticky='w',
                               padx=12, pady=(0, 4))
        self.problem_label = ttk.Label(
            summary, text="", background=self.CLR_FRAME_BG,
            wraplength=900, justify='left', foreground=self.CLR_STATUS_BAD)
        self.problem_label.grid(row=2, column=0, sticky='w',
                                padx=12, pady=(0, 10))

        plot_frame = ttk.LabelFrame(panel, text='The curve')
        plot_frame.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        plot_frame.grid_rowconfigure(0, weight=1)
        plot_frame.grid_columnconfigure(0, weight=1)
        self.figure = None
        self.plot_canvas = None
        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(6, 3.2), dpi=100)
            self.figure.patch.set_facecolor(self.CLR_GRAPH_BG)
            self.plot_canvas = FigureCanvasTkAgg(self.figure,
                                                 master=plot_frame)
            self.plot_canvas.get_tk_widget().grid(row=0, column=0,
                                                  sticky='nsew',
                                                  padx=5, pady=5)
        else:
            ttk.Label(
                plot_frame,
                text=("Matplotlib is not installed, so the curve is not "
                      "drawn.\nThe table below shows every point that would "
                      "be sent."),
                background=self.CLR_FRAME_BG, justify='left').grid(
                row=0, column=0, padx=15, pady=15, sticky='w')

        # Two things want the same space and are never both needed at once:
        # the points about to go out, and what the instrument already holds.
        # Tabs give each of them the full width instead of splitting it.
        tabs = ttk.Notebook(panel)
        tabs.grid(row=2, column=0, sticky='nsew', padx=5, pady=5)
        self.right_tabs = tabs

        table_frame = ttk.Frame(tabs)
        tabs.add(table_frame, text='  The curve to be sent  ')
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        columns = ('n', 'reading', 'temperature', 'ohms')
        self.table = ttk.Treeview(table_frame, columns=columns,
                                  show='headings', height=8)
        for column, heading, width in (
                ('n', 'CRVPT index', 110),
                ('reading', 'Sensor units (sent first)', 220),
                ('temperature', 'Temperature / K (sent second)', 220),
                ('ohms', 'Resistance / ohm', 180)):
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, anchor='center')
        self.table.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        table_scroll = ttk.Scrollbar(table_frame, orient='vertical',
                                     command=self.table.yview)
        self.table.configure(yscrollcommand=table_scroll.set)
        table_scroll.grid(row=0, column=1, sticky='ns')

        self._create_curve_map_tab(tabs)

    def _create_curve_map_tab(self, tabs):
        """The map: one row per curve, saying what is in it.

        Built from CRVHDR? alone. That is one query per curve rather than the
        two hundred a point-by-point read would take, and the header is what
        identifies a curve anyway: the name and serial say which sensor it is,
        the format says what units it is in, and the limit says how far up it
        goes. Use 'Show every point of the chosen curve' for the points.
        """
        map_frame = ttk.Frame(tabs)
        tabs.add(map_frame, text='  What is on the instrument  ')
        map_frame.grid_rowconfigure(1, weight=1)
        map_frame.grid_columnconfigure(0, weight=1)

        self.map_status = ttk.Label(
            map_frame,
            text=("Not read yet. Connect, then press 'Map the curves' in "
                  "step 4."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9, 'italic'),
            justify='left')
        self.map_status.grid(row=0, column=0, columnspan=2, sticky='w',
                             padx=8, pady=(6, 2))

        columns = ('curve', 'kind', 'name', 'serial', 'format', 'limit',
                   'coefficient')
        self.map_table = ttk.Treeview(map_frame, columns=columns,
                                      show='headings', height=8)
        for column, heading, width, anchor in (
                ('curve', 'Curve', 70, 'center'),
                ('kind', 'Status', 150, 'center'),
                ('name', 'Name', 180, 'w'),
                ('serial', 'Serial', 130, 'w'),
                ('format', 'Format', 170, 'w'),
                ('limit', 'Limit / K', 100, 'center'),
                ('coefficient', 'Coefficient', 110, 'center')):
            self.map_table.heading(column, text=heading)
            self.map_table.column(column, width=width, anchor=anchor)
        self.map_table.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        map_scroll = ttk.Scrollbar(map_frame, orient='vertical',
                                   command=self.map_table.yview)
        self.map_table.configure(yscrollcommand=map_scroll.set)
        map_scroll.grid(row=1, column=1, sticky='ns')

        # Colour carries the same thing the Status column says in words, so
        # the map is readable at a glance and still readable without colour.
        self.map_table.tag_configure('free', foreground=self.CLR_STATUS_OK)
        self.map_table.tag_configure('inuse', foreground=self.CLR_TEXT_DARK)
        self.map_table.tag_configure('standard',
                                     foreground=self.CLR_STATUS_WARN)
        self.map_table.tag_configure('error', foreground=self.CLR_STATUS_BAD)
        self.map_table.bind('<<TreeviewSelect>>', self._map_row_chosen)

        ttk.Label(
            map_frame,
            text=("Double-click a free user curve to make it the target. "
                  "Standard curves are read-only and are never offered."),
            background=self.CLR_FRAME_BG, font=self.FONT_SMALL,
            justify='left').grid(row=2, column=0, columnspan=2, sticky='w',
                                 padx=8, pady=(0, 6))

    def _map_row_chosen(self, _event=None):
        """Selecting a free user curve in the map picks it as the target."""
        selection = self.map_table.selection()
        if not selection:
            return
        values = self.map_table.item(selection[0], 'values')
        if not values:
            return
        curve = self._parse_leading_int(values[0])
        if curve is None:
            return
        for value in (self.curve_combo['values'] or []):
            if self._parse_leading_int(value) == curve:
                self.curve_var.set(value)
                self._refresh_curve_hint()
                return

    def _render_curve_map(self):
        """Fill the map from the last listing."""
        for item in self.map_table.get_children():
            self.map_table.delete(item)
        if not self.curve_map:
            return
        free = 0
        for entry in self.curve_map:
            if entry.get('error'):
                kind, tag = "no answer", 'error'
            elif entry.get('standard'):
                kind, tag = "standard (read-only)", 'standard'
            elif entry.get('empty'):
                kind, tag = "free", 'free'
                free += 1
            else:
                kind, tag = "in use", 'inuse'
            fmt_code = entry.get('format')
            fmt_text = (f"{fmt_code} - {FORMAT_CODES[fmt_code][1]}"
                        if fmt_code in FORMAT_CODES else "")
            limit = entry.get('limit')
            self.map_table.insert(
                '', 'end', tags=(tag,), values=(
                    entry['curve'], kind,
                    str(entry.get('name', '')).strip(),
                    str(entry.get('serial', '')).strip(),
                    "" if entry.get('empty') else fmt_text,
                    "" if limit is None or entry.get('empty')
                    else f"{float(limit):g}",
                    COEFFICIENT_NAMES.get(entry.get('coefficient'), "")
                    if not entry.get('empty') else ""))
        user_curves = [entry for entry in self.curve_map
                       if not entry.get('standard')]
        self.map_status.config(
            text=(f"{len(self.curve_map)} curves read. {free} of "
                  f"{len(user_curves)} user curves are free."))

    # -----------------------------------------------------------------------
    # LOGGING AND STATE
    # -----------------------------------------------------------------------

    def log(self, message):
        """Append a timestamped message to the console. Safe from any thread.

        The message is only queued here; _drain_events() writes it into the
        console on the Tk thread. Timestamping happens here, so the console
        shows when something happened rather than when it was drawn.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._events.put(('log', f"[{timestamp}] {message}\n"))

    def _post(self, *event):
        """Queue one request for the Tk thread. Safe from any thread."""
        self._events.put(event)

    def _drain_events(self, reschedule=True):
        """Carry out queued work. MAIN THREAD ONLY, driven by after().

        This is the only place a worker's request reaches a widget. One bad
        event must never stop the pump: an exception escaping here would
        freeze the console and the busy flag for the rest of the session.
        """
        pending = []
        try:
            while True:
                pending.append(self._events.get_nowait())
        except queue.Empty:
            pass

        for event in pending:
            try:
                self._apply_event(event)
            except Exception as exc:          # never let the pump die
                print(f"Curve loader event {event[0]!r} failed: {exc}")

        if reschedule:
            try:
                self.root.after(EVENT_POLL_MS, self._drain_events)
            except tk.TclError:
                pass                          # the window is closing

    def _apply_event(self, event):
        """One queued request, carried out on the Tk thread."""
        kind = event[0]
        if kind == 'log':
            self.console.config(state='normal')
            self.console.insert('end', event[1])
            self.console.see('end')
            self.console.config(state='disabled')
        elif kind == 'busy':
            self._set_busy(event[1])
        elif kind == 'progress':
            self.progress['maximum'] = event[2]
            self.progress['value'] = event[1]
        elif kind == 'model':
            self._adopt_model(event[1])
        elif kind == 'curves_listed':
            self.curve_map = event[1]
            # The picker only ever offers user curves; standard curves are
            # read-only and appear in the map alone.
            first = model_spec(self.model())['user_curve_min']
            self.curve_list = [entry for entry in self.curve_map
                               if entry['curve'] >= first]
            self._render_curve_map()
            self._repopulate_curve_choices()
            self._refresh_curve_hint()
        elif kind == 'dialog':
            _, level, title, text = event
            {'info': messagebox.showinfo,
             'warning': messagebox.showwarning,
             'error': messagebox.showerror}[level](title, text)

    def _describe_starting_point(self):
        self.log(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION} ready.")
        self.log("Set the model, or connect and it is read from *IDN?. "
                 "Nothing can be sent until it is settled.")
        self.log("Source file: X17680.340 from the calibration CD. There is "
                 "no '.350' and none is needed; the .340 is Lake Shore's "
                 "breakpoint format and both controllers store it with the "
                 "same commands.")
        if not PYVISA_AVAILABLE:
            self.log("PyVISA is not installed, so nothing can be sent. The "
                     "file readers, the checks and the command list still "
                     "work.")
        if not MATPLOTLIB_AVAILABLE:
            self.log("Matplotlib is not installed, so the curve is shown as "
                     "a table only.")

    def _require_connection(self):
        if not self.is_connected or not self.backend.is_connected:
            self.log("Not connected to the instrument.")
            messagebox.showerror("Not Connected",
                                 "Connect to the controller first (step 4).")
            return False
        return True

    def _require_model(self):
        if not self.model():
            self.log("The model has not been set.")
            messagebox.showerror(
                "Model Not Set",
                "Set the model first, in step 4.\n\nThe 340 and the 350 are "
                "not interchangeable: they have different user curve ranges, "
                "different data formats and only the 340 needs CRVSAV. "
                "Nothing here guesses which one is on the bench.\n\n"
                "Connecting reads it from *IDN?.")
            return False
        return True

    def _set_busy(self, busy):
        self.busy = busy
        state = 'disabled' if busy else 'normal'
        for widget in ('send_btn', 'sequence_btn'):
            try:
                getattr(self, widget).config(state=state)
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # MODEL
    # -----------------------------------------------------------------------

    def model(self):
        """The model key the window is set to, or None."""
        raw = self.model_var.get().strip()
        if not raw or raw == MODEL_UNSET:
            return None
        key = raw.split()[0]
        return key if key in MODEL_SPECS else None

    def _adopt_model(self, key):
        """Set the model picker from '*IDN?'. MAIN THREAD ONLY."""
        self.model_var.set(f"{key} - {MODEL_SPECS[key]['label']}")
        self._on_model_change()

    def _on_model_change(self):
        key = self.model()
        if key is None:
            self.curve_combo['values'] = []
            self.curve_hint.config(
                text=("Set the model first: the 340 has curves 21-60 and the "
                      "350 has 21-59, and a write to a number outside that "
                      "is discarded in silence."))
            self._rebuild_curve()
            return
        spec = model_spec(key)
        self.input_combo['values'] = list(spec['inputs'])
        if self.input_var.get() not in spec['inputs']:
            self.input_var.set(spec['inputs'][0])
        self.curve_list = None
        self.curve_map = None
        self._render_curve_map()
        self._repopulate_curve_choices()
        self.log(f"Model set to {spec['label']}: user curves "
                 f"{spec['user_curve_min']}-{spec['user_curve_max']}, data "
                 f"formats {', '.join(str(c) for c in spec['formats'])}, "
                 + ("CRVSAV is required after every curve edit."
                    if spec['needs_crvsav']
                    else "writes are permanent immediately; there is no "
                         "CRVSAV."))
        self.log(f"  Inputs: {', '.join(spec['inputs'])}. "
                 f"{spec['input_note']}")
        self._rebuild_curve()

    def _repopulate_curve_choices(self):
        """Fill the target picker, with what each curve holds where known."""
        key = self.model()
        if key is None:
            return
        spec = model_spec(key)
        numbers = list(range(spec['user_curve_min'],
                             spec['user_curve_max'] + 1))
        if not self.curve_list:
            values = [str(number) for number in numbers]
        else:
            values = []
            for entry in self.curve_list:
                curve = entry['curve']
                if entry.get('error'):
                    values.append(f"{curve}  (no answer)")
                elif entry.get('empty'):
                    values.append(f"{curve}  [free]")
                else:
                    values.append(
                        f"{curve}  {entry.get('name', '').strip()}  "
                        f"{entry.get('serial', '').strip()}  [in use]")
        self.curve_combo['values'] = values
        wanted = self._curve_number()
        keep = wanted in numbers
        if keep and self.curve_list:
            # A selection made before the map arrived is only kept if the map
            # says it is still free. Otherwise the default target would be
            # somebody else's calibration, which this module will not write
            # to anyway -- better to move the picker than to sit on an error.
            keep = any(entry.get('empty') for entry in self.curve_list
                       if entry.get('curve') == wanted)
        if keep:
            for value in values:                # same number, new label
                if self._parse_leading_int(value) == wanted:
                    self.curve_var.set(value)
                    break
        else:
            free = [value for value in values if value.endswith("[free]")]
            if not free and self.curve_list:
                self.log("No free user curve was found. Nothing here "
                         "overwrites one, so free a curve from the front "
                         "panel before sending.")
            self.curve_var.set((free or values or [""])[0])
        self._refresh_curve_hint()

    def _first_free_curve(self):
        """The lowest user curve the listing calls free, or None."""
        for entry in (self.curve_list or []):
            if entry.get('empty'):
                return entry['curve']
        return None

    def _target_state(self):
        """'free', 'in use', 'no answer' or 'unknown' for the chosen curve.

        'unknown' means the user curves have not been listed in this session,
        so nothing here can say. It is not the same as free, and the module
        does not treat it as free: the pre-send check reads the curve itself.
        """
        curve = self._curve_number()
        if curve is None:
            return 'unknown'
        for entry in (self.curve_list or []):
            if entry.get('curve') != curve:
                continue
            if entry.get('error'):
                return 'no answer'
            return 'free' if entry.get('empty') else 'in use'
        return 'unknown'

    @staticmethod
    def _parse_leading_int(raw):
        try:
            return int(str(raw).strip().split()[0])
        except (ValueError, IndexError):
            return None

    def _curve_number(self):
        """The target user curve number, or None."""
        return self._parse_leading_int(self.curve_var.get())

    def _refresh_curve_hint(self):
        key = self.model()
        curve = self._curve_number()
        if key is None or curve is None:
            return
        spec = model_spec(key)
        held = None
        for entry in (self.curve_list or []):
            if entry.get('curve') == curve:
                held = entry
                break
        if held is None:
            text = (f"User curve {curve} on the {spec['short']}. The curves "
                    "have not been listed in this session, so nothing here "
                    "knows whether it is free. List them before sending; the "
                    "curve is read again immediately before the send either "
                    "way, and an occupied one is refused.")
        elif held.get('error'):
            text = (f"User curve {curve} did not answer CRVHDR? when the "
                    "curves were listed. Inspect it before sending.")
        elif held.get('empty'):
            text = f"User curve {curve} is free. Sending fills it."
        else:
            free = self._first_free_curve()
            text = (f"User curve {curve} already holds "
                    f"'{held.get('name', '').strip()}' "
                    f"(serial '{held.get('serial', '').strip()}', format "
                    f"{held.get('format')}, limit {held.get('limit')} K). "
                    "This module only writes into an empty curve, so nothing "
                    "will be sent here."
                    + (f" Curve {free} is free." if free else
                       " No user curve on this instrument is free."))
        self.curve_hint.config(text=text)
        self._render_summary()

    # -----------------------------------------------------------------------
    # STEP 1 AND 2: LOAD AND BUILD
    # -----------------------------------------------------------------------

    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="Choose a Lake Shore sensor file",
            filetypes=[
                ("Lake Shore breakpoint curve (preferred)", "*.340"),
                ("Lake Shore breakpoint curve", "*.330"),
                ("Lake Shore raw calibration", "*.dat"),
                ("Lake Shore table", "*.tbl"),
                ("All files", "*.*"),
            ])
        if not path:
            return
        self._load_file(path)

    def _load_file(self, path):
        if self.second_source:
            # The second file was chosen to extend a particular main curve;
            # carrying it onto a different sensor would silently merge two
            # sensors' data.
            self.second_source = None
            self.second_source_path = ""
            self.second_file_label.config(text="Not using a second file.")
            self.log("The second file was cleared: it belonged to the "
                     "previous main file.")
        self.source_path = path
        name = os.path.basename(path)
        self.log(f"Reading {name} ...")
        try:
            source = load_sensor_file(path)
        except CurveFileError as exc:
            self.source = None
            self.file_label.config(text=f"{name}: could not be read.")
            self.log(f"REFUSED: {exc}")
            messagebox.showerror("File Not Read", str(exc))
            self._rebuild_curve()
            return
        except Exception as exc:
            self.source = None
            self.log(f"ERROR reading {name}: {traceback.format_exc()}")
            messagebox.showerror("File Not Read",
                                 f"{name} could not be read:\n{exc}")
            self._rebuild_curve()
            return

        self.source = source
        points = source['points']
        temperatures = [t for t, _ in points]
        readings = [r for _, r in points]
        kinds = {
            'lakeshore-dat': "Lake Shore raw calibration (.dat)",
            'lakeshore-tbl': "Lake Shore interpolated table (.tbl)",
            'lakeshore-340': "Lake Shore breakpoint curve (.340 / .330)",
        }
        meta = source['meta']
        self.file_label.config(
            text=(f"{name}\n{kinds.get(source['kind'], source['kind'])}, "
                  f"{len(points)} points, units {source['units']}\n"
                  f"{meta.get('columns', '')}"))
        self.log(f"  {kinds.get(source['kind'], source['kind'])}, "
                 f"{len(points)} points.")
        self.log(f"  {meta.get('columns', '')}")
        self.log(f"  Temperature {min(temperatures):.4g} K to "
                 f"{max(temperatures):.4g} K; reading {min(readings):.6g} to "
                 f"{max(readings):.6g} {source['units']}.")

        # Everything the file states about itself goes into the form, so the
        # operator confirms it rather than types it from memory.
        for key in ('model', 'serial', 'format_name'):
            if key in meta:
                self.log(f"  {key}: {meta[key]}")
        if 'format' in meta:
            self._set_format(meta['format'])
        else:
            self._set_format(3 if source['units'] == 'OHMS'
                             else 4 if source['units'] == 'LOGOHM'
                             else 2 if source['units'] == 'V' else 1)
            self.log("  This file does not state a data format code, so one "
                     "was chosen from its units. Check it.")
        if 'limit' in meta:
            self.limit_var.set(f"{meta['limit']:g}")
        elif not self.limit_var.get().strip():
            self.limit_var.set(f"{max(temperatures):g}")
            self.log("  This file does not state a setpoint limit, so the "
                     "top of the curve was used. Check it.")
        if 'coefficient' in meta:
            self._set_coefficient(meta['coefficient'])
        else:
            self._set_coefficient(coefficient_from_points(points))
            self.log("  This file does not state a temperature coefficient, "
                     "so it was read off the data.")
        if not self.name_var.get().strip():
            self.name_var.set(self._suggest_name(name, meta))
        if not self.serial_var.get().strip():
            self.serial_var.set(
                str(meta.get('serial', ''))[:MAX_SERIAL_CHARS])

        self._rebuild_curve()

    def _set_format(self, code):
        for value in self.format_combo['values']:
            if self._parse_leading_int(value) == int(code):
                self.format_var.set(value)
                return
        self.format_var.set(str(code))

    def _set_coefficient(self, code):
        for value in self.coefficient_combo['values']:
            if self._parse_leading_int(value) == int(code):
                self.coefficient_var.set(value)
                return
        self.coefficient_var.set(str(code))

    @staticmethod
    def _suggest_name(file_name, meta):
        """A curve name of at most 15 printable ASCII characters.

        The sensor model is used where the file gave one, because that is
        what identifies the physical sensor on the bench and CRVHDR has a
        separate field for the serial. Commas and semicolons are stripped:
        CRVHDR is comma-separated and one in a name would shift every field
        after it.
        """
        candidate = str(meta.get('model', '')).strip() or \
            os.path.splitext(file_name)[0]
        candidate = ''.join(character for character in candidate
                            if 32 <= ord(character) < 127
                            and character not in NAME_FORBIDDEN)
        return candidate.strip()[:MAX_NAME_CHARS]

    def _toggle_second(self):
        if self.use_second_var.get():
            self.second_btn.config(state='normal')
            if not self.second_source:
                self.second_file_label.config(
                    text="Choose the file to extend the ends from.")
        else:
            self.second_btn.config(state='disabled')
            self.second_file_label.config(text="Not using a second file.")
        self._rebuild_curve()

    def _choose_second_file(self):
        """Pick the file whose ends will extend the main curve."""
        if not self.source:
            messagebox.showerror(
                "Choose the Main File First",
                "Pick the main curve file above before choosing one to "
                "extend it with. The second file only contributes points "
                "beyond the ends of the first, so there has to be a first.")
            return
        path = filedialog.askopenfilename(
            title="Choose a file to extend the ends of the curve",
            initialdir=(os.path.dirname(self.source_path)
                        if self.source_path else None),
            filetypes=[
                ("Lake Shore raw calibration", "*.dat"),
                ("Lake Shore breakpoint curve", "*.340"),
                ("Lake Shore table", "*.tbl"),
                ("All files", "*.*"),
            ])
        if not path:
            return
        name = os.path.basename(path)
        self.log(f"Reading {name} to extend the ends of the curve ...")
        try:
            extra = load_sensor_file(path)
        except CurveFileError as exc:
            self.log(f"REFUSED: {exc}")
            messagebox.showerror("File Not Read", str(exc))
            return
        except Exception as exc:
            self.log(f"ERROR reading {name}: {traceback.format_exc()}")
            messagebox.showerror("File Not Read",
                                 f"{name} could not be read:\n{exc}")
            return
        self.second_source = extra
        self.second_source_path = path
        self._rebuild_curve()

    def _clear_second_file(self):
        if not self.second_source:
            return
        self.second_source = None
        self.second_source_path = ""
        self.log("Second file cleared; the main file is used on its own.")
        self._rebuild_curve()

    def _working_range(self):
        """The temperatures the sensor will be used over, or None.

        Both boxes have to hold sensible numbers before the coverage check
        runs. A half-filled or mistyped pair is treated as "not stated"
        rather than guessed at, because a wrong range would produce a
        confident warning about the wrong thing.
        """
        try:
            low = float(self.use_low_var.get().strip())
            high = float(self.use_high_var.get().strip())
        except (ValueError, AttributeError):
            return None
        if not (math.isfinite(low) and math.isfinite(high)):
            return None
        if low <= 0 or high <= low:
            return None
        return (low, high)

    def _rebuild_curve(self):
        """Recompute the curve from the file and the header fields.

        Called on every change, so what the right panel shows is always what
        the send button would transmit.
        """
        self.curve_commands = []
        self.curve_points = []
        self.curve_errors = []
        self.curve_warnings = []
        self.curve_stats = {}
        self.dropped_points = 0
        self.merge_notes = []

        if not self.source:
            self._render_summary()
            return

        key = self.model()
        fmt_code = self._parse_leading_int(self.format_var.get())
        if fmt_code is None or fmt_code not in FORMAT_CODES:
            self.curve_errors = ["Choose a data format."]
            self._render_summary()
            return
        target_units = FORMAT_CODES[fmt_code][0]

        try:
            points = convert_units(self.source['points'],
                                   self.source['units'], target_units)
            if self.use_second_var.get() and self.second_source:
                points, added, self.merge_notes = extend_curve(
                    points, self.second_source['points'],
                    target_units, self.second_source['units'])
                second_name = os.path.basename(self.second_source_path)
                if added:
                    self.second_file_label.config(
                        text=(f"{second_name}: {len(added)} point(s) added "
                              "beyond the ends of the main curve."))
                else:
                    self.second_file_label.config(
                        text=(f"{second_name}: adds nothing, every point is "
                              "inside the main curve."))
        except CurveFileError as exc:
            self.curve_errors = [str(exc)]
            self._render_summary()
            return

        points, dropped = thin_points(points, MAX_CURVE_POINTS)
        self.dropped_points = dropped
        self.curve_points = points

        if key is None:
            self.curve_errors = [
                "The model is not set, so nothing here knows the user curve "
                "range, which data formats exist or whether CRVSAV is "
                "needed. Set it in step 4."]
            self._render_summary()
            return

        try:
            limit = float(self.limit_var.get())
        except ValueError:
            self.curve_errors = [
                f"The temperature limit '{self.limit_var.get()}' is not a "
                "number of kelvin. On a .340 file it is the 'SetPoint "
                "Limit:' line."]
            self._render_summary()
            return
        coefficient = self._parse_leading_int(self.coefficient_var.get())
        if coefficient is None:
            self.curve_errors = ["Choose a temperature coefficient."]
            self._render_summary()
            return

        name = self.name_var.get().strip()
        serial = self.serial_var.get().strip()
        errors, warnings, stats = analyse_curve(
            key, points, target_units, fmt_code, name, serial, limit,
            coefficient, working_range=self._working_range())
        warnings.extend(self.merge_notes)
        if dropped:
            warnings.append(
                f"{dropped} point(s) were dropped to fit the "
                f"{MAX_CURVE_POINTS}-point limit. The points kept are a "
                "plain subset of the calibration, evenly spaced through it; "
                "nothing was interpolated.")
        if not self.erase_var.get():
            warnings.append(
                "The curve will NOT be erased first. The target is empty by "
                "its header, but a transfer interrupted part-way can leave "
                "points behind a blank header, and those would survive past "
                "the end of this curve for the instrument to interpolate "
                "through.")

        curve = self._curve_number()
        if curve is None:
            errors.append("Choose a target user curve.")
        else:
            # An occupied curve is a REFUSAL, not a warning. This module fills
            # empty user curves; it does not replace anybody's calibration,
            # and the instrument gives no way to get one back.
            state = self._target_state()
            if state == 'in use':
                free = self._first_free_curve()
                errors.append(
                    f"User curve {curve} already holds a curve. Nothing here "
                    "overwrites one: pick an empty curve."
                    + (f" Curve {free} is the lowest one the listing calls "
                       "free." if free else
                       " The listing found no free user curve on this "
                       "instrument; free one from the front panel first."))
            elif state == 'no answer':
                errors.append(
                    f"User curve {curve} did not answer CRVHDR? when the "
                    "curves were listed, so nothing here knows whether it is "
                    "empty. Inspect it, or pick a curve the listing calls "
                    "free.")
            elif state == 'unknown':
                warnings.append(
                    "The user curves have not been listed in this session, so "
                    f"nothing here knows whether curve {curve} is free. The "
                    "curve is read again immediately before the send and an "
                    "occupied one is refused there, but listing them first "
                    "(step 4) makes the choice an informed one.")

        self.curve_errors = errors
        self.curve_warnings = warnings
        self.curve_stats = stats
        if not errors:
            try:
                self.curve_commands = build_curve_commands(
                    key, curve, name, serial, fmt_code, limit, coefficient,
                    points, erase_first=self.erase_var.get())
            except Exception as exc:
                self.curve_errors = [f"The commands could not be built: {exc}"]
                self.curve_commands = []
        self._render_summary()

    # -----------------------------------------------------------------------
    # RENDERING
    # -----------------------------------------------------------------------

    def _render_summary(self):
        self._render_table()
        self._render_plot()

        if not self.source:
            self.headline_label.config(text="No curve loaded.",
                                       foreground=self.CLR_FG_LIGHT)
            self.detail_label.config(text="Choose a sensor file to begin.")
            self.problem_label.config(text="")
            return

        stats = self.curve_stats
        fmt_code = self._parse_leading_int(self.format_var.get())
        units = FORMAT_CODES.get(fmt_code, ('', ''))[0]
        curve = self._curve_number()
        key = self.model()

        if self.curve_errors:
            self.headline_label.config(text="This curve will not be sent.",
                                       foreground=self.CLR_STATUS_BAD)
        else:
            self.headline_label.config(
                text=(f"{stats.get('count', 0)} points ready for user curve "
                      f"{curve} on the "
                      f"{model_spec(key)['short'] if key else '?'}."),
                foreground=self.CLR_STATUS_OK)

        detail = []
        if stats:
            detail.append(
                f"{stats['t_min']:.4g} K to {stats['t_max']:.4g} K, "
                f"{COEFFICIENT_NAMES[stats['coefficient']]} temperature "
                "coefficient.")
            if units == 'LOGOHM':
                detail.append(
                    f"Stored as log10(ohm) {stats['r_min']:.6g} to "
                    f"{stats['r_max']:.6g}, which is "
                    f"{10 ** stats['r_min']:,.4g} to "
                    f"{10 ** stats['r_max']:,.4g} ohm.")
            elif units == 'OHMS':
                detail.append(f"Stored in ohms, {stats['r_min']:,.6g} to "
                              f"{stats['r_max']:,.6g}.")
            elif units:
                detail.append(f"Stored in {units.lower()}, "
                              f"{stats['r_min']:.6g} to {stats['r_max']:.6g}.")
            if 'suggested_range_ohms' in stats:
                detail.append(
                    f"The peak {stats['peak_ohms']:,.1f} ohm fits input range "
                    f"{stats['suggested_range']} "
                    f"({stats['suggested_range_ohms']:,.0f} ohm full scale); "
                    "autorange picks it on its own.")
        if self.curve_commands:
            detail.append(
                f"{len(self.curve_commands)} commands: "
                + ", ".join(sorted(
                    {command.split()[0] for command in self.curve_commands})))
        detail.append(
            "Sent as sensor units first, temperature in kelvin second — the "
            "same order as the .340 file, and the opposite of a .dat.")
        self.detail_label.config(text="\n".join(detail))

        problems = []
        for message in self.curve_errors:
            problems.append(f"PROBLEM  {message}")
        for message in self.curve_warnings:
            problems.append(f"CHECK    {message}")
        self.problem_label.config(
            text="\n".join(problems),
            foreground=(self.CLR_STATUS_BAD if self.curve_errors
                        else self.CLR_STATUS_WARN))

    def _render_table(self):
        for item in self.table.get_children():
            self.table.delete(item)
        if not self.curve_points:
            return
        fmt_code = self._parse_leading_int(self.format_var.get())
        units = FORMAT_CODES.get(fmt_code, ('', ''))[0]
        ordered = sorted(self.curve_points, key=lambda pair: pair[1])
        for index, (temperature, reading) in enumerate(ordered, start=1):
            if units == 'LOGOHM':
                ohms = f"{10 ** reading:,.4f}"
            elif units == 'OHMS':
                ohms = f"{reading:,.4f}"
            else:
                ohms = "-"
            self.table.insert('', 'end', values=(
                index, fmt6(reading), fmt6(temperature), ohms))

    def _render_plot(self):
        if not MATPLOTLIB_AVAILABLE or self.figure is None:
            return
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.set_facecolor(self.CLR_GRAPH_BG)
        if self.curve_points:
            ordered = sorted(self.curve_points, key=lambda pair: pair[1])
            temperatures = [t for t, _ in ordered]
            readings = [r for _, r in ordered]
            axes.plot(temperatures, readings, marker='o', markersize=3,
                      linewidth=1.2, color='#8A4B3C')
            axes.set_xscale('log')
            axes.set_xlabel("Temperature / K")
            fmt_code = self._parse_leading_int(self.format_var.get())
            units = FORMAT_CODES.get(fmt_code, ('', ''))[0]
            axes.set_ylabel({'LOGOHM': "log10(R / ohm)",
                             'OHMS': "R / ohm",
                             'V': "V", 'MV': "mV"}.get(units, units))
            axes.grid(True, which='both', alpha=0.3)
            axes.set_title("Check this looks like the sensor it should be",
                           fontsize=10)
        else:
            axes.text(0.5, 0.5, "No curve loaded", ha='center', va='center',
                      transform=axes.transAxes)
            axes.set_xticks([])
            axes.set_yticks([])
        self.figure.tight_layout()
        self.plot_canvas.draw()

    # -----------------------------------------------------------------------
    # STEP 3: SAVE
    # -----------------------------------------------------------------------

    def _save_commands(self):
        if not self.curve_commands:
            messagebox.showerror(
                "Nothing to Save",
                "There is no valid curve yet. Load a file, set the model and "
                "clear any problems listed on the right first.")
            return
        default = "curve_commands.txt"
        if self.source_path:
            default = os.path.splitext(
                os.path.basename(self.source_path))[0] + \
                f"_curve{self._curve_number()}.txt"
        path = filedialog.asksaveasfilename(
            title="Save the command list",
            defaultextension=".txt", initialfile=default,
            initialdir=(os.path.dirname(self.source_path)
                        if self.source_path else None),
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = command_file_text(self.model(), self.curve_commands)
            with open(path, 'w', encoding='ascii', newline='\n') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception as exc:
            self.log(f"Could not save: {exc}")
            messagebox.showerror("Save Failed", f"{path}\n\n{exc}")
            return
        self.last_saved_path = path
        self.log(f"Saved {len(self.curve_commands)} commands to {path}")
        self.log("  That file is exactly what the send button transmits, one "
                 "command per line.")

    # -----------------------------------------------------------------------
    # STEP 4: CONNECTION
    # -----------------------------------------------------------------------

    def _scan_visa(self):
        self.log("Scanning for VISA instruments...")
        try:
            resources = list(self.backend.scan_resources())
        except Exception as exc:
            self.log(f"Scan error: {exc}")
            return
        if not resources:
            self.log("No VISA instruments found.")
            return
        self.log(f"Found {len(resources)} resource(s):")
        self.visa_cb['values'] = resources
        identities = self.backend.identify_resources(resources)
        found = None
        for resource in resources:
            idn = identities.get(resource, 'no reply')
            model = model_from_idn(idn)
            self.log(f"  {resource}  ->  {idn}"
                     + (f"   [Model {model}]" if model else ""))
            if model and found is None:
                found = (resource, model)
        if found:
            self.visa_cb.set(found[0])
            self.log(f"A Model {found[1]} answered at {found[0]}; selected.")
        else:
            self.log("WARNING: no Lake Shore Model 340 or 350 answered "
                     "*IDN? on this bus.")

    def _do_connect(self):
        address = self.visa_cb.get()
        if not address:
            messagebox.showerror("No Address",
                                 "Scan and select a VISA address first.")
            return
        expected = self.model()
        try:
            self.log(f"Connecting to {address}...")
            idn, model = self.backend.connect(address, expected_model=expected)
            self.is_connected = True
            self.log(f"Connected: {idn}")
            if expected is None:
                self.log(f"  Model {model} read from *IDN?.")
                self._adopt_model(model)
            else:
                self.log(f"  *IDN? confirms the Model {model} this window is "
                         "set to.")
            self.status_label.config(text=f"● Connected  ·  Model {model}",
                                     foreground=self.CLR_STATUS_OK)
            self.connect_btn.config(state='disabled')
            self.disconnect_btn.config(state='normal')
            self.visa_cb.config(state='disabled')
            self.model_combo.config(state='disabled')
        except Exception as exc:
            self.log(f"CONNECT ERROR: {exc}")
            messagebox.showerror("Connection Failed",
                                 f"Could not connect to {address}:\n\n{exc}")

    def _do_disconnect(self):
        self.log("Disconnecting (non-destructive)...")
        self.backend.disconnect()
        self.is_connected = False
        self.log("Disconnected. The instrument keeps every setting and "
                 "carries on.")
        self.status_label.config(text="● Not connected",
                                 foreground=self.CLR_STATUS_BAD)
        self.connect_btn.config(state='normal')
        self.disconnect_btn.config(state='disabled')
        self.visa_cb.config(state='readonly')
        self.model_combo.config(state='readonly')

    # -----------------------------------------------------------------------
    # WORKER PLUMBING
    # -----------------------------------------------------------------------

    def _run_in_worker(self, description, function):
        """Run one instrument job off the Tk thread.

        The transfer is up to 203 paced writes plus a readback of the same
        length, which is well over half a minute; done on the main thread the
        window would appear to hang in the middle of writing a calibration
        curve, which is the worst possible moment to look frozen.
        """
        if self.busy:
            self.log("Another instrument operation is still running.")
            return
        self._set_busy(True)

        def target():
            try:
                function()
            except Exception as exc:
                # The message is copied out of the exception here, on this
                # thread, and the traceback is formatted here too. Python
                # unbinds the name in 'except ... as exc' when the block ends,
                # so a lambda that closed over 'exc' and ran later on the Tk
                # thread would raise NameError instead of showing the dialog,
                # losing the very report it was meant to deliver.
                message = f"{type(exc).__name__}: {exc}"
                self.log(f"{description} FAILED: {message}")
                self.log(traceback.format_exc())
                self._post('dialog', 'error', f"{description} Failed",
                           message)
            finally:
                self._post('busy', False)

        threading.Thread(target=target, daemon=True).start()

    def _set_progress(self, done, total):
        """Move the progress bar. Safe from the worker thread."""
        self._post('progress', done, total)

    # -----------------------------------------------------------------------
    # STEP 4b: LOOKING BEFORE WRITING
    # -----------------------------------------------------------------------

    def _list_curves(self):
        """CRVHDR? a range of curves and build the map. Queries, no writes."""
        if not self._require_model() or not self._require_connection():
            return
        spec = model_spec(self.model())
        first = (spec['query_curve_min'] if self.map_standard_var.get()
                 else spec['user_curve_min'])
        last = spec['user_curve_max']

        def job():
            self.log("")
            self.log(f"Mapping curves {first} to {last} with CRVHDR?. This "
                     "sends queries only: no CRVDEL, no CRVPT, no CRVSAV, no "
                     "loop, setpoint or heater command.")
            entries = self.backend.list_curves(
                first, last,
                progress=lambda done, total, entry: self._set_progress(
                    done, total))
            free = 0
            for entry in entries:
                mark = "  [standard, read-only]" if entry.get('standard') \
                    else ""
                if entry.get('error'):
                    self.log(f"  {entry['curve']:>3}  (no answer: "
                             f"{entry['error']}){mark}")
                elif entry.get('empty'):
                    if not entry.get('standard'):
                        free += 1
                    self.log(f"  {entry['curve']:>3}  [free]{mark}")
                else:
                    self.log(f"  {entry['curve']:>3}  "
                             f"{str(entry.get('name', '')).strip():<16} "
                             f"SN={str(entry.get('serial', '')).strip():<11}"
                             f"format={entry.get('format')}  "
                             f"limit={entry.get('limit')} K  "
                             f"coeff={entry.get('coefficient')}{mark}")
            user_curves = [entry for entry in entries
                           if not entry.get('standard')]
            self.log(f"  {free} of {len(user_curves)} user curves are free.")
            if not free:
                self.log("  Nothing here overwrites a curve, so with no free "
                         "user curve there is nowhere to put this one. Free "
                         "one from the front panel first.")
            self._post('curves_listed', entries)

        self._run_in_worker("Mapping the curves", job)

    def _inspect_curve(self):
        """Read one curve back in full, without comparing it to anything."""
        if not self._require_model() or not self._require_connection():
            return
        curve = self._curve_number()
        if curve is None:
            messagebox.showerror("No Curve Chosen",
                                 "Choose a target user curve in step 2.")
            return

        def job():
            self.log("")
            self.log(f"Reading curve {curve} with CRVHDR? and CRVPT? ...")
            header, points, texts, _ = self.backend.read_curve(
                curve,
                progress=lambda done, total: self._set_progress(done, total))
            self.log(f"  Header: name '{header['name'].strip()}', serial "
                     f"'{header['serial'].strip()}', format "
                     f"{header['format']} "
                     f"({FORMAT_CODES.get(header['format'], ('', '?'))[1]}), "
                     f"limit {header['limit']:g} K, coefficient "
                     f"{header['coefficient']} "
                     f"({COEFFICIENT_NAMES.get(header['coefficient'], '?')}).")
            if header_is_empty(header):
                self.log("  That is an empty curve: no name and no limit. "
                         "Nothing is stored here.")
            if not points:
                self.log("  No points. The curve is empty.")
                return
            temperatures = [t for t, _ in points]
            self.log(f"  {len(points)} points, covering "
                     f"{min(temperatures):.4g} K to "
                     f"{max(temperatures):.4g} K.")
            self.log(f"  First: {texts[0][0]} -> {texts[0][1]} K.  "
                     f"Last: {texts[-1][0]} -> {texts[-1][1]} K.")

        self._run_in_worker("Reading the curve", job)

    # -----------------------------------------------------------------------
    # STEP 5: SEND AND VERIFY
    # -----------------------------------------------------------------------

    def _expected_header(self):
        """The five CRVHDR fields, read off the form. MAIN THREAD ONLY.

        Tk variables belong to the thread running the event loop: reading one
        from a worker raises 'main thread is not in main loop'. The
        verification runs on a worker, so it is handed a plain dict captured
        here instead of reaching back into the widgets. Snapshotting also
        means the check compares against what was actually sent, not against
        whatever the operator has since typed into the form.
        """
        return {
            'curve': self._curve_number(),
            'name': self.name_var.get().strip(),
            'serial': self.serial_var.get().strip(),
            'format': self._parse_leading_int(self.format_var.get()),
            'limit': float(self.limit_var.get()),
            'coefficient': self._parse_leading_int(self.coefficient_var.get()),
        }

    def _confirm_send(self, expected, steps=None):
        """The dialog shown before anything is written. Returns True to go."""
        spec = model_spec(self.model())
        warning_text = ""
        if self.curve_warnings:
            warning_text = ("\n\nWarnings on this curve:\n  - " +
                            "\n  - ".join(self.curve_warnings))
        step_text = ("\n" + "\n".join(steps) + "\n") if steps else ""
        return messagebox.askyesno(
            "Send the curve?",
            f"This fills EMPTY user curve {expected['curve']} on the "
            f"{spec['label']}.\n\n"
            "The curve is read once more immediately before anything is "
            "written. If it turns out to hold a curve, the send is abandoned "
            "with nothing sent: this module does not replace an existing "
            "calibration.\n"
            f"{step_text}"
            f"\nName:        {expected['name']}"
            f"\nSerial:      {expected['serial']}"
            f"\nFormat:      {expected['format']} "
            f"({FORMAT_CODES[expected['format']][1]})"
            f"\nLimit:       {expected['limit']:g} K"
            f"\nCoefficient: {expected['coefficient']} "
            f"({COEFFICIENT_NAMES[expected['coefficient']]})"
            f"\nPoints:      {len(self.curve_points)}"
            f"\nCommands:    {len(self.curve_commands)}\n\n"
            "No loop, setpoint, heater, range or reset command is sent at any "
            "point."
            + ("\n\nThe transfer ends with CRVSAV, which is what puts the "
               "curve in the 340's flash. Do not power the instrument off "
               "until the console says BUSY? has cleared."
               if spec['needs_crvsav'] else "")
            + f"{warning_text}\n\nSend it?")

    def _target_is_empty_now(self, curve):
        """Read the target curve and say whether it is safe to fill.

        Runs on the worker thread, immediately before anything is written.
        Returns (is_empty, header, detail).

        This is the gate, not the listing. The listing can be minutes old, it
        can predate somebody else at the front panel, and it is not run at all
        unless the operator presses the button. One CRVHDR? costs nothing and
        turns "the picker said it was free" into "the instrument says it is
        free, now".

        An unreadable reply is NOT treated as empty. Refusing a send costs a
        retry; overwriting a calibration nobody can get back does not.
        """
        try:
            header = self.backend.read_curve_header(curve)
        except Exception as exc:
            return (False, None,
                    f"curve {curve} could not be read before sending "
                    f"({type(exc).__name__}: {exc}), so nothing here can say "
                    "it is empty. Nothing was sent.")
        if not header_is_empty(header):
            return (False, header,
                    f"curve {curve} already holds "
                    f"'{header['name'].strip()}' (serial "
                    f"'{header['serial'].strip()}', format "
                    f"{header['format']}, limit {header['limit']:g} K). This "
                    "module only fills empty curves, so nothing was sent. "
                    "Pick a free curve, or free this one from the front panel "
                    "if it really is finished with.")
        return (True, header, f"curve {curve} is empty")

    def _send_curve(self):
        if not self._require_model() or not self._require_connection():
            return
        if not self.curve_commands:
            messagebox.showerror(
                "Nothing to Send",
                "There is no valid curve yet. Load a file and clear the "
                "problems listed on the right first.")
            return

        expected = self._expected_header()
        if not self._confirm_send(expected):
            self.log("Send cancelled.")
            return

        model = self.model()
        curve = expected['curve']
        commands = list(self.curve_commands)
        points = list(self.curve_points)
        verify = self.verify_var.get()

        def job():
            # The gate. Nothing is written until the instrument itself says
            # this curve is empty.
            self.log(f"Checking curve {curve} is empty before writing ...")
            is_empty, baseline_header, detail = self._target_is_empty_now(curve)
            if not is_empty:
                self.log(f"REFUSED: {detail}")
                self._post('dialog', 'error', "Curve Not Empty", detail)
                return
            self.log(f"  {detail}.")
            self.log(f"Sending {len(commands)} commands to curve {curve} ...")
            self.backend.send_curve(
                commands,
                progress=lambda done, total, command: self._set_progress(
                    done, total))
            self.log("  All commands sent.")
            if verify:
                self._verify_against(model, points, curve, expected,
                                     baseline_header=baseline_header)
            else:
                self.log("Verification was switched off. Nothing has "
                         "confirmed what the instrument actually stored.")

        self._run_in_worker("Sending the curve", job)

    def _verify_only(self):
        if not self._require_model() or not self._require_connection():
            return
        if not self.curve_points:
            messagebox.showerror(
                "Nothing to Compare",
                "Load the file whose curve you want to compare against "
                "first.")
            return
        model = self.model()
        curve = self._curve_number()
        points = list(self.curve_points)
        expected = self._expected_header()
        self._run_in_worker(
            "Reading the curve back",
            lambda: self._verify_against(model, points, curve, expected))

    def _verify_against(self, model, sent_points, curve, expected,
                        baseline_header=None):
        """Read the curve back and compare it with what was sent.

        Runs on the worker thread, so it touches no widget: `expected` is the
        snapshot taken by _expected_header() before the job started.

        Returns the verdict string from classify_verify() so a caller running
        a sequence can decide whether to carry on.
        """
        self.log(f"Reading curve {curve} back with CRVHDR? and "
                 f"{len(sent_points)} CRVPT? ...")
        try:
            header, read_points, texts, tail_index = self.backend.read_curve(
                curve, expected_count=len(sent_points),
                progress=lambda done, total: self._set_progress(done, total))
        except CurveFileError as exc:
            self.log(f"VERIFY FAILED: the reply could not be read: {exc}")
            return 'unreadable'

        self.log(f"  The instrument reports: name "
                 f"'{header['name'].strip()}', serial "
                 f"'{header['serial'].strip()}', format {header['format']}, "
                 f"limit {header['limit']:g} K, coefficient "
                 f"{header['coefficient']}.")

        fields = compare_headers(expected, header)
        for label, key in (("name", 'name'), ("serial", 'serial'),
                           ("format", 'format'), ("limit", 'limit'),
                           ("coefficient", 'coefficient')):
            sent_value, read_value, matched = fields[key]
            mark = "ok  " if matched else "DIFF"
            self.log(f"  [{mark}] {label}: sent '{sent_value}', "
                     f"read '{read_value}'")

        # Checked against the precision the instrument printed, not against a
        # tolerance chosen here; see compare_curves().
        comparison = compare_curves(sent_points, read_points,
                                    read_texts=texts)
        self.log(f"  {comparison['sent_count']} points sent, "
                 f"{comparison['read_count']} read back.")
        if comparison['read_count'] == comparison['sent_count']:
            self.log("  Largest difference in any sensor reading: "
                     f"{comparison['worst_reading_error']:.2e} relative; in "
                     "any temperature: "
                     f"{comparison['worst_temperature_error']:.2e} "
                     f"(worst at point {comparison['worst_point']}).")
            self.log(
                "  Each point was compared at the precision its own reply was "
                "printed to; the loosest that got anywhere in this curve was "
                f"{comparison['worst_temperature_limit']:.3g} K in "
                f"temperature and {comparison['worst_reading_limit']:.3g} in "
                "the sensor reading.")
        if tail_index is not None:
            self.log(f"  Point {tail_index}, one past the end of this curve, "
                     "is NOT empty. The slot still holds the tail of a longer "
                     "curve.")
        else:
            self.log(f"  Point {len(sent_points) + 1}, one past the end, is "
                     "empty as it should be: nothing of the previous curve "
                     "survived.")

        verdict, headline, advice = classify_verify(
            model, expected, header, comparison, tail_index=tail_index,
            baseline_header=baseline_header)

        if verdict == 'verified':
            spec = model_spec(model)
            self.log(f"VERIFIED. User curve {curve} on the "
                     f"{spec['short']} matches what was sent: the header, "
                     "every point, and nothing left over past the end.")
            self.log(f"  To use it, set an input to curve {curve} (step 6, or "
                     "the front panel).")
            caveat = (
                "\n\nEach point was compared at the precision its own reply "
                "was printed to, the loosest being "
                f"{comparison['worst_temperature_limit']:.3g} K.")
            self._post('dialog', 'info', "Curve Verified",
                       f"User curve {curve} matches what was sent, point for "
                       f"point.\n\nSet an input to curve {curve} to use "
                       f"it.{caveat}")
            return verdict

        # Point-level differences are only worth printing when the header
        # matched. When the curve holds another calibration entirely they are
        # a list of differences between two unrelated tables, which reads like
        # evidence and is not.
        if verdict != 'not_written':
            for message in comparison['problems']:
                self.log(f"  {message}")

        self.log("VERIFY FAILED. " + headline)
        self.log("  " + advice)
        self.log("  Do not use this sensor until this is resolved.")
        self._post(
            'dialog', 'error', "Curve Not Verified",
            f"User curve {curve} does not match what was sent.\n\n"
            f"{headline}\n\n{advice}\n\n"
            "The console has the field-by-field comparison. Do not use this "
            "sensor until this is resolved.")
        return verdict

    # -----------------------------------------------------------------------
    # STEP 5b: THE WHOLE SEQUENCE, IN ORDER, STOPPING AT THE FIRST FAILURE
    # -----------------------------------------------------------------------

    def _run_full_sequence(self):
        """Every instrument step in order, each one checked before the next.

        The point of running these as a sequence rather than as separate
        buttons is the BASELINE. Reading the curve before the send is what
        turns "the readback does not match" into "the curve is unchanged, so
        nothing was written", and those two call for opposite actions.

        Order:
            1  identify the instrument and confirm the model
            2  read the target curve as it stands now, and keep it
            3  send CRVDEL / CRVHDR / CRVPT (+ CRVSAV on a 340)
            4  read it back, point by point, and classify what happened
            5  optionally check the input's sensor type, put the curve on it
               and read a temperature

        Any step that fails stops the sequence. Step 5 is skipped when 4 did
        not verify, because putting an input on a curve that is not there
        would only make the fault harder to see.
        """
        if not self._require_model() or not self._require_connection():
            return
        if not self.curve_commands:
            messagebox.showerror(
                "Nothing to Send",
                "There is no valid curve yet. Load a file and clear the "
                "problems listed on the right first.")
            return

        model = self.model()
        spec = model_spec(model)
        expected = self._expected_header()
        curve = expected['curve']
        assign = self.sequence_assign_var.get()
        channel = self.input_var.get()

        steps = [
            "1  identify the instrument and confirm the model",
            f"2  confirm curve {curve} is empty, and stop if it is not",
            f"3  send {len(self.curve_commands)} commands to curve {curve}"
            + (" and CRVSAV" if spec['needs_crvsav'] else ""),
            "4  read it back and compare, field by field and point by point",
        ]
        if assign:
            steps.append(f"5  check input {channel}'s sensor type, set it to "
                         f"curve {curve}, and read a temperature")
        if not self._confirm_send(expected, steps=steps):
            self.log("Sequence cancelled.")
            return

        commands = list(self.curve_commands)
        points = list(self.curve_points)
        fmt_code = expected['format']

        def job():
            results = []

            def record(number, title, passed, detail=""):
                results.append((number, title, passed, detail))
                mark = "PASS" if passed else "FAIL"
                self.log(f"[{mark}]  step {number}: {title}"
                         + (f" -- {detail}" if detail else ""))
                return passed

            def finish():
                self.log("")
                self.log("SEQUENCE SUMMARY")
                for number, title, passed, detail in results:
                    mark = "PASS" if passed else "FAIL"
                    self.log(f"  [{mark}] {number}. {title}"
                             + (f" -- {detail}" if detail else ""))
                failed = [item for item in results if not item[2]]
                if failed:
                    self.log(f"  Stopped at step {failed[0][0]}. Nothing "
                             "after it was attempted.")
                    self._post(
                        'dialog', 'error', "Sequence Stopped",
                        f"Step {failed[0][0]} ({failed[0][1]}) did not "
                        f"pass.\n\n{failed[0][3]}\n\nNothing after it was "
                        "attempted. The console has the detail.")
                else:
                    self.log("  Every step passed.")
                    self._post(
                        'dialog', 'info', "Sequence Complete",
                        f"User curve {curve} is installed and verified, and "
                        "every step in the sequence passed. The console has "
                        "the record.")

            # -- 1: identify ------------------------------------------------
            self.log("")
            self.log("=" * 62)
            self.log(f"SEQUENCE START  ·  {spec['label']}  ·  user curve "
                     f"{curve}")
            self.log("=" * 62)
            try:
                idn = self.backend.link.query('*IDN?')
            except Exception as exc:
                record(1, "identify the instrument", False,
                       f"*IDN? failed: {exc}")
                return finish()
            seen = model_from_idn(idn)
            if seen != model:
                record(1, "identify the instrument", False,
                       f"'{idn}' is a Model {seen}, not the Model {model} "
                       "this window is set to. Nothing was sent.")
                return finish()
            record(1, "identify the instrument", True, idn)

            # -- 2: the curve must be empty ---------------------------------
            # This step both gates the write and provides the baseline. An
            # empty curve IS the baseline: if step 4 reads back an empty
            # header, nothing landed, and that is a comparison rather than an
            # inference.
            is_empty, baseline_header, detail = self._target_is_empty_now(curve)
            if not record(2, f"confirm curve {curve} is empty", is_empty,
                          detail):
                return finish()

            # -- 3: send ----------------------------------------------------
            try:
                self.log(f"Sending {len(commands)} commands ...")
                self.backend.send_curve(
                    commands,
                    progress=lambda done, total, command: self._set_progress(
                        done, total))
            except Exception as exc:
                record(3, f"send the curve to curve {curve}", False, str(exc))
                return finish()
            record(3, f"send the curve to curve {curve}", True,
                   f"{len(commands)} commands"
                   + (", ending with CRVSAV and a cleared BUSY?"
                      if spec['needs_crvsav'] else ""))

            # -- 4: verify --------------------------------------------------
            verdict = self._verify_against(model, points, curve, expected,
                                           baseline_header=baseline_header)
            if verdict != 'verified':
                record(4, "read the curve back and compare", False,
                       f"verdict: {verdict}")
                return finish()
            record(4, "read the curve back and compare", True,
                   "the header, every point, and nothing left over")

            # -- 5: the input -----------------------------------------------
            if assign:
                passed, detail = self._install_on_input(
                    model, channel, curve, fmt_code)
                if not record(5, f"put curve {curve} on input {channel}",
                              passed, detail):
                    return finish()

            finish()

        self._run_in_worker("Running the sequence", job)

    # -----------------------------------------------------------------------
    # STEP 6: THE INPUT
    # -----------------------------------------------------------------------

    def _install_on_input(self, model, channel, curve, fmt_code):
        """Check the input type, set INCRV, confirm it stuck, read a value.

        Runs on the worker thread. Returns (passed, detail).

        The type check is not a nicety. Both manuals say a curve whose
        parameters do not match the input type is silently replaced by curve
        0, so an INCRV that was refused looks exactly like one that worked
        until somebody reads the input.
        """
        spec = model_spec(model)
        code, raw = self.backend.get_input_type(channel)
        name = spec['sensor_types'].get(code, "unrecognised")
        self.log(f"  INTYPE? {channel} -> {raw}   (sensor type {code}: "
                 f"{name})")
        if code is None:
            return (False,
                    f"INTYPE? {channel} answered '{raw}', which does not "
                    "start with a sensor type. Nothing was assigned.")
        if fmt_code in (3, 4) and code in spec['voltage_types']:
            return (False,
                    f"input {channel} is set to sensor type {code} ({name}), "
                    "which measures a voltage, and this is a resistance "
                    f"curve. INCRV would put input {channel} back on curve 0 "
                    "without reporting an error, so nothing was sent.\n\n"
                    f"Set input {channel} to "
                    f"{spec['ntc_type']} ({spec['ntc_type_label']}) from the "
                    "front panel or the Direct Control window first. This "
                    "module does not write INTYPE, because changing an "
                    "input's sensor type changes what any control loop "
                    "reading it is measuring.")
        if fmt_code in (1, 2) and code in spec['resistive_types'] and code != 0:
            return (False,
                    f"input {channel} is set to sensor type {code} ({name}), "
                    "which measures a resistance, and this is a voltage "
                    "curve. INCRV would put it back on curve 0. Nothing was "
                    "sent.")

        reported = self.backend.assign_curve_to_input(channel, curve)
        self.log(f"  INCRV? {channel} -> {reported}")
        if reported == 0:
            return (False,
                    f"input {channel} came back on curve 0. That is how both "
                    "manuals say an input refuses a curve whose parameters do "
                    f"not match its sensor type ({code}: {name}). The curve "
                    "itself is installed and verified; only the assignment "
                    "was refused.")
        if reported != curve:
            return (False,
                    f"input {channel} came back on curve {reported}, not "
                    f"{curve}. Nothing further was sent; set the curve from "
                    "the front panel and check it.")

        readings = self.backend.read_input_temperature(channel)
        self.log(f"  KRDG? {channel} -> {readings['kelvin']}    "
                 f"SRDG? {channel} -> {readings['sensor']}")
        return (True,
                f"reads {readings['kelvin']} K "
                f"({readings['sensor']} sensor units)")

    def _show_input_state(self):
        if not self._require_model() or not self._require_connection():
            return
        spec = model_spec(self.model())

        def job():
            self.log("")
            self.log("Asking each input for its sensor type, its curve and "
                     "its reading. Queries only.")
            for channel in spec['inputs']:
                code, raw = self.backend.get_input_type(channel)
                name = spec['sensor_types'].get(code, "unrecognised")
                curve = self.backend.get_input_curve(channel)
                readings = self.backend.read_input_temperature(channel)
                self.log(f"  Input {channel}: type {code} ({name}), curve "
                         f"{curve}, {readings['kelvin']} K, "
                         f"{readings['sensor']} sensor units")
                if curve == 0:
                    self.log("    Curve 0 means no curve: this input reports "
                             "sensor units and no temperature.")
            self.log(f"  {spec['input_note']}")

        self._run_in_worker("Reading the inputs", job)

    def _assign_input(self):
        if not self._require_model() or not self._require_connection():
            return
        curve = self._curve_number()
        channel = self.input_var.get()
        if curve is None:
            messagebox.showerror("No Curve Chosen",
                                 "Choose a target user curve in step 2.")
            return
        if not messagebox.askyesno(
                "Change the input's curve?",
                f"This sets input {channel} to user curve {curve} with "
                "INCRV.\n\n"
                "It changes how that input turns its reading into a "
                "temperature. If a control loop is reading that input, its "
                "temperature will change the moment this is sent.\n\n"
                "Nothing else is touched: no loop, setpoint, heater or "
                "sensor-type command.\n\nGo ahead?"):
            self.log("Input assignment cancelled.")
            return

        model = self.model()
        # Read here, on the Tk thread; a worker must not touch a Tk variable.
        fmt_code = self._parse_leading_int(self.format_var.get())

        def job():
            self.log("")
            self.log(f"Putting curve {curve} on input {channel} ...")
            passed, detail = self._install_on_input(model, channel, curve,
                                                    fmt_code)
            if passed:
                self.log(f"  Input {channel} is on curve {curve} and "
                         f"{detail}.")
                self.log("  Check that reading against another thermometer "
                         "before trusting it.")
            else:
                self.log(f"ASSIGNMENT NOT CONFIRMED. {detail}")
                self._post('dialog', 'warning', "Assignment Not Confirmed",
                           detail)

        self._run_in_worker("Assigning the input", job)

    # -----------------------------------------------------------------------

    def _on_closing(self):
        if self.busy:
            if not messagebox.askyesno(
                    "Still Working",
                    "An instrument operation is still running. Closing now "
                    "could leave a partial curve in the slot.\n\nClose "
                    "anyway?"):
                return
        if self.is_connected:
            self.backend.disconnect()
        self.root.destroy()


# ===============================================================================
# OFFLINE SELF-TEST
# ===============================================================================
#
# Everything here runs on made-up data with no instrument, no VISA and no Tk,
# so it can be run on the measurement PC before a session or anywhere else:
#
#     python Sensor_Curve_Loader_L340_L350_GUI.py --selftest
#
# It is not a substitute for tests/test_lakeshore_curve_loader.py; it is the
# subset that has to hold before this module is allowed near a controller.


def _selftest_cases():
    """Yield (name, callable) pairs. Each callable raises on failure."""

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    SAMPLE_340 = ("Sensor Model:   CX-1030-SD-4L\n"
                  "Serial Number:  X17680\n"
                  "Data Format:    4      (Log Ohms/Kelvin)\n"
                  "SetPoint Limit: 325.      (Kelvin)\n"
                  "Temperature coefficient:  1    (Negative)\n"
                  "Number of Breakpoints:   3\n"
                  "\n"
                  "No.   Units      Temperature (K)\n"
                  "\n"
                  "  1   1.64523      325.000\n"
                  "  2   2.00000      100.000\n"
                  "  3   2.94699        4.000\n")

    # -- 1: six significant digits, never in exponent form -------------------
    def case_fmt6():
        check(fmt6(1.6452312) == "1.64523", fmt6(1.6452312))
        check(fmt6(325.0) == "325.0", fmt6(325.0))
        check('e' not in fmt6(1.23e-7).lower(), fmt6(1.23e-7))
        check('.' in fmt6(4), fmt6(4))
        check(fmt_limit(325.0) == "325.000", fmt_limit(325.0))

    # -- 2: a .340 is read reading-first, with its whole header --------------
    def case_parse_340():
        points, units, meta = _parse_lakeshore_340(
            _clean_lines(SAMPLE_340), "X17680.340")
        check(units == 'LOGOHM', units)
        check(len(points) == 3, len(points))
        check(abs(points[0][0] - 325.0) < 1e-9, points[0])
        check(abs(points[0][1] - 1.64523) < 1e-9, points[0])
        check(meta['serial'] == 'X17680', meta)
        check(meta['model'] == 'CX-1030-SD-4L', meta)
        check(meta['format'] == 4, meta)
        check(meta['limit'] == 325.0, meta)
        check(meta['coefficient'] == COEFFICIENT_NEGATIVE, meta)

    # -- 3: a .dat is temperature-first and is located by its headings -------
    def case_parse_dat():
        text = ("Temperature   Resistance\n"
                "(Kelvin)      (Ohms)\n"
                "\n"
                "3.59132       977.251\n"
                "330.030        43.761\n")
        points, units, meta = _parse_lakeshore_columns(
            _clean_lines(text), "X17680.dat")
        check(units == 'OHMS', units)
        check(abs(points[0][0] - 3.59132) < 1e-9, points[0])
        check(abs(points[0][1] - 977.251) < 1e-9, points[0])

    # -- 4: a file with no column headings is refused, not guessed ----------
    def case_headerless_refused():
        try:
            _parse_lakeshore_columns(
                _clean_lines("1.0 2.0\n3.0 4.0\n"), "mystery.txt")
        except CurveFileError:
            return
        raise AssertionError("a headerless two-column file was accepted")

    # -- 5: format 5 is refused rather than read as kelvin ------------------
    def case_format_5_refused():
        text = SAMPLE_340.replace("Data Format:    4", "Data Format:    5")
        try:
            _parse_lakeshore_340(_clean_lines(text), "odd.340")
        except CurveFileError as exc:
            check('log' in str(exc).lower(), str(exc))
            return
        raise AssertionError("a log-K file was read as if it were kelvin")

    # -- 6: ohms and log-ohms interconvert, ohms and volts never ------------
    def case_units():
        converted = convert_units([(4.0, 100.0)], 'OHMS', 'LOGOHM')
        check(abs(converted[0][1] - 2.0) < 1e-12, converted)
        back = convert_units(converted, 'LOGOHM', 'OHMS')
        check(abs(back[0][1] - 100.0) < 1e-9, back)
        millivolts = convert_units([(4.0, 1.5)], 'V', 'MV')
        check(abs(millivolts[0][1] - 1500.0) < 1e-9, millivolts)
        try:
            convert_units([(4.0, 100.0)], 'OHMS', 'V')
        except CurveFileError:
            return
        raise AssertionError("ohms were converted to volts")

    # -- 7: thinning keeps both ends and invents nothing --------------------
    def case_thin():
        original = [(float(i), float(i)) for i in range(1, 51)]
        thinned, dropped = thin_points(original, 10)
        check(len(thinned) == 10, len(thinned))
        check(thinned[0] == original[0], thinned[0])
        check(thinned[-1] == original[-1], thinned[-1])
        check(dropped == 40, dropped)
        check(all(point in original for point in thinned), "invented a point")

    # -- 8: extending adds only beyond the ends -----------------------------
    def case_extend():
        primary = [(10.0, 1.0), (20.0, 2.0), (30.0, 3.0)]
        extra = [(5.0, 0.5), (25.0, 2.5), (40.0, 4.0)]
        merged, added, _ = extend_curve(primary, extra, 'LOGOHM', 'LOGOHM')
        check(len(added) == 2, added)
        check(all(t in (5.0, 40.0) for t, _ in added), added)
        check(len(merged) == 5, merged)

    # -- 9: the two models have different slot ranges, and it is enforced ----
    def case_slot_ranges():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        for model, highest in ((MODEL_340, 60), (MODEL_350, 59)):
            commands = build_curve_commands(
                model, highest, "CX-1030", "X17680", 4, 325.0,
                COEFFICIENT_NEGATIVE, points)
            check(commands[0] == f"CRVDEL {highest}", commands[0])
        try:
            build_curve_commands(MODEL_350, 60, "CX-1030", "X17680", 4,
                                 325.0, COEFFICIENT_NEGATIVE, points)
        except ValueError:
            pass
        else:
            raise AssertionError("curve 60 was accepted on a Model 350")
        try:
            build_curve_commands(MODEL_340, 20, "CX-1030", "X17680", 4,
                                 325.0, COEFFICIENT_NEGATIVE, points)
        except ValueError:
            return
        raise AssertionError("a standard curve number was accepted")

    # -- 10: REGRESSION. Only the 340 gets a CRVSAV -------------------------
    def case_crvsav():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        for_340 = build_curve_commands(MODEL_340, 21, "CX-1030", "X17680", 4,
                                       325.0, COEFFICIENT_NEGATIVE, points)
        for_350 = build_curve_commands(MODEL_350, 21, "CX-1030", "X17680", 4,
                                       325.0, COEFFICIENT_NEGATIVE, points)
        check(for_340[-1] == "CRVSAV", for_340[-1])
        check("CRVSAV" not in for_350, for_350)
        check(len(for_340) == len(for_350) + 1, (len(for_340), len(for_350)))

    # -- 11: the commands are exactly the manual's syntax --------------------
    def case_command_syntax():
        points = [(325.0, 1.64523), (100.0, 2.0), (4.0, 2.94699)]
        commands = build_curve_commands(
            MODEL_350, 21, "CX-1030-SD-4L", "X17680", 4, 325.0,
            COEFFICIENT_NEGATIVE, points)
        check(commands[0] == "CRVDEL 21", commands[0])
        check(commands[1] ==
              "CRVHDR 21,CX-1030-SD-4L,X17680,4,325.000,1", commands[1])
        # index 1 is the LOWEST sensor reading, which is the hot end
        check(commands[2] == "CRVPT 21,1,1.64523,325.0", commands[2])
        check(commands[4] == "CRVPT 21,3,2.94699,4.0", commands[4])
        check(len(commands) == 5, len(commands))
        # and without the erase, the header is first
        plain = build_curve_commands(
            MODEL_350, 21, "CX-1030-SD-4L", "X17680", 4, 325.0,
            COEFFICIENT_NEGATIVE, points, erase_first=False)
        check(plain[0].startswith("CRVHDR"), plain[0])

    # -- 12: format 5 exists on the 340 and not on the 350 ------------------
    def case_format_availability():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        errors, _, _ = analyse_curve(MODEL_350, points, 'LOGOHM', 5,
                                     "CX-1030", "X17680", 325.0,
                                     COEFFICIENT_NEGATIVE)
        check(any('format 5' in message.lower() for message in errors), errors)
        check(5 in MODEL_SPECS[MODEL_340]['formats'], "340 lost format 5")
        check(5 not in MODEL_SPECS[MODEL_350]['formats'], "350 gained one")

    # -- 13: a wrong coefficient is an error, not a warning -----------------
    def case_coefficient():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        check(coefficient_from_points(points) == COEFFICIENT_NEGATIVE,
              coefficient_from_points(points))
        errors, _, _ = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                     "CX-1030", "X17680", 325.0,
                                     COEFFICIENT_POSITIVE)
        check(any('coefficient' in message for message in errors), errors)

    # -- 14: a comma in a name is refused ----------------------------------
    def case_name_rules():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        errors, _, _ = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                     "CX-1030,SD", "X17680", 325.0,
                                     COEFFICIENT_NEGATIVE)
        check(any(',' in message for message in errors), errors)
        errors, _, _ = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                     "A" * 16, "X17680", 325.0,
                                     COEFFICIENT_NEGATIVE)
        check(any('15' in message for message in errors), errors)
        errors, _, _ = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                     "CX-1030", "X" * 11, 325.0,
                                     COEFFICIENT_NEGATIVE)
        check(any('10' in message for message in errors), errors)

    # -- 15: the curve is checked against the input's sensor type -----------
    def case_input_type():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        # a log-ohm curve on a 350 diode input: INCRV would refuse it
        errors, _, _ = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                     "CX-1030", "X17680", 325.0,
                                     COEFFICIENT_NEGATIVE, input_type=1)
        check(any('curve 0' in message for message in errors), errors)
        # the same curve on NTC RTD is fine
        errors, _, stats = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                         "CX-1030", "X17680", 325.0,
                                         COEFFICIENT_NEGATIVE, input_type=3)
        check(not errors, errors)
        check(stats['input_type_name'] == "NTC RTD", stats)
        # and on a 340 the Cernox has its own type number
        errors, _, stats = analyse_curve(MODEL_340, points, 'LOGOHM', 4,
                                         "CX-1030", "X17680", 325.0,
                                         COEFFICIENT_NEGATIVE, input_type=8)
        check(not errors, errors)
        check(stats['input_type_name'] == "Cernox", stats)

    # -- 16: the 350's input range table is used, not guessed at ------------
    def case_range_table():
        # X17680 peaks at 977 ohm, which the 1 kohm range covers.
        points = [(325.0, 1.64523), (4.0, 2.98999)]
        _, _, stats = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                    "CX-1030", "X17680", 325.0,
                                    COEFFICIENT_NEGATIVE)
        check(abs(stats['peak_ohms'] - 977.2) < 1.0, stats)
        check(stats['suggested_range'] == 4, stats)
        check(stats['suggested_range_ohms'] == 1000.0, stats)
        # a megaohm sensor is off the top of every range
        errors, _, _ = analyse_curve(MODEL_350, [(325.0, 1.0), (4.0, 6.0)],
                                     'LOGOHM', 4, "BIG", "1", 325.0,
                                     COEFFICIENT_NEGATIVE)
        check(any('off the top' in message for message in errors), errors)
        # the 340 has no printed range table, so nothing is invented for it
        _, _, stats = analyse_curve(MODEL_340, [(325.0, 1.0), (4.0, 6.0)],
                                    'LOGOHM', 4, "BIG", "1", 325.0,
                                    COEFFICIENT_NEGATIVE)
        check('suggested_range' not in stats, stats)

    # -- 17: a curve short of the working range warns -----------------------
    def case_coverage():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        _, warnings, _ = analyse_curve(MODEL_350, points, 'LOGOHM', 4,
                                       "CX-1030", "X17680", 325.0,
                                       COEFFICIENT_NEGATIVE,
                                       working_range=(2.0, 300.0))
        check(any('no curve to read' in message for message in warnings),
              warnings)

    # -- 18: a header and a point reply are read the way they are printed ---
    def case_readback_parsing():
        header = parse_crvhdr_reply(
            "CX-1030-SD-4L  ,X17680    ,4,+325.000,1")
        check(header['name'] == "CX-1030-SD-4L", header)
        check(header['serial'] == "X17680", header)
        check(header['format'] == 4, header)
        check(abs(header['limit'] - 325.0) < 1e-9, header)
        check(header['coefficient'] == 1, header)
        temperature, reading, texts = parse_crvpt_reply("+1.64523,+325.000")
        check(abs(temperature - 325.0) < 1e-9, temperature)
        check(abs(reading - 1.64523) < 1e-9, reading)
        check(texts == ("+1.64523", "+325.000"), texts)
        check(point_is_empty(*parse_crvpt_reply("+0.00000,+0.000")[:2]),
              "a zero point was not seen as empty")
        check(header_is_empty(parse_crvhdr_reply("   ,   ,1,+0.000,1")),
              "a blank header was not seen as empty")

    # -- 19: REGRESSION. Three fields differing means nothing landed --------
    def case_classify_not_written():
        expected = {'curve': 21, 'name': 'CX-1030', 'serial': 'X17680',
                    'format': 4, 'limit': 325.0, 'coefficient': 1}
        header = {'name': 'DT-470', 'serial': '00011134', 'format': 2,
                  'limit': 475.0, 'coefficient': 1, 'limit_text': '475.000'}
        comparison = {'matched': False, 'sent_count': 129, 'read_count': 86,
                      'problems': []}
        verdict, headline, advice = classify_verify(
            MODEL_340, expected, header, comparison)
        check(verdict == 'not_written', verdict)
        check('NOT evidence' in advice, advice)
        check('CRVSAV' in advice, advice)
        # the 350 gets the same verdict without the CRVSAV advice
        _, _, advice_350 = classify_verify(MODEL_350, expected, header,
                                           comparison)
        check('CRVSAV' not in advice_350, advice_350)
        # and with a baseline it is a comparison, not an inference
        verdict2, headline2, _ = classify_verify(
            MODEL_340, expected, header, comparison,
            baseline_header=dict(header))
        check(verdict2 == 'not_written', verdict2)
        check('before the send' in headline2, headline2)

    # -- 20: REGRESSION. A leftover tail is caught and named ----------------
    def case_classify_tail():
        expected = {'curve': 21, 'name': 'CX-1030', 'serial': 'X17680',
                    'format': 4, 'limit': 325.0, 'coefficient': 1}
        header = {'name': 'CX-1030', 'serial': 'X17680', 'format': 4,
                  'limit': 325.0, 'coefficient': 1, 'limit_text': '325.000'}
        comparison = {'matched': True, 'sent_count': 134, 'read_count': 134,
                      'problems': []}
        verdict, headline, advice = classify_verify(
            MODEL_350, expected, header, comparison, tail_index=135)
        check(verdict == 'tail', verdict)
        check('135' in headline, headline)
        check('CRVDEL 21' in advice, advice)
        # and with an empty point 135 it verifies
        verdict, _, _ = classify_verify(MODEL_350, expected, header,
                                        comparison, tail_index=None)
        check(verdict == 'verified', verdict)

    # -- 21: a changed format code is its own diagnosis ---------------------
    def case_classify_format():
        expected = {'curve': 21, 'name': 'CX-1030', 'serial': 'X17680',
                    'format': 4, 'limit': 325.0, 'coefficient': 1}
        header = {'name': 'CX-1030', 'serial': 'X17680', 'format': 3,
                  'limit': 325.0, 'coefficient': 1, 'limit_text': '325.000'}
        comparison = {'matched': True, 'sent_count': 129, 'read_count': 129,
                      'problems': []}
        verdict, headline, advice = classify_verify(
            MODEL_350, expected, header, comparison)
        check(verdict == 'format_only', verdict)
        check('wrong units' in advice, advice)

    # -- 22: the readback is judged at the precision it was printed to ------
    def case_printed_precision():
        sent = [(325.0, 1.645231)]
        read = [(325.0, 1.6452)]
        loose = compare_curves(sent, read,
                               read_texts=[("1.6452", "325.000")])
        check(loose['matched'], loose['problems'])
        tight = compare_curves(sent, read,
                               read_texts=[("1.64520000", "325.000")])
        check(not tight['matched'],
              "an under-printed value passed a tight tolerance")

    # -- 23: a model is recognised from *IDN? and nothing else --------------
    def case_model_from_idn():
        check(model_from_idn("LSCI,MODEL340,123456,053004") == MODEL_340,
              "340 not recognised")
        check(model_from_idn("LSCI,MODEL350,LSA23AR,1.5") == MODEL_350,
              "350 not recognised")
        check(model_from_idn("LSCI,MODEL331S,123,1.0") is None,
              "a 331 was taken for a 340 or 350")
        check(model_from_idn("Cryocon,Model 34,204683,3.18A") is None,
              "a Cryocon was taken for a Lake Shore")
        check(is_lakeshore_idn("LSCI,MODEL331S,123,1.0"),
              "a Lake Shore 331 was not seen as a Lake Shore at all")

    # -- 24: the whole round trip, offline ---------------------------------
    def case_round_trip():
        source = _parse_lakeshore_340(_clean_lines(SAMPLE_340), "X17680.340")
        points, units, meta = source
        commands = build_curve_commands(
            MODEL_340, 21, "CX-1030-SD-4L", meta['serial'], meta['format'],
            meta['limit'], meta['coefficient'], points)
        # play the commands back as if the instrument had stored them
        stored = []
        for command in commands:
            if command.startswith("CRVPT"):
                _, arguments = command.split(' ', 1)
                _, _, reading, temperature = arguments.split(',')
                stored.append((float(temperature), float(reading)))
        comparison = compare_curves(points, stored)
        check(comparison['matched'], comparison['problems'])
        # CRVHDR carries the curve number as its first field; a CRVHDR? reply
        # does not, so it is dropped before the reply parser sees it.
        header = parse_crvhdr_reply(
            commands[1].split(' ', 1)[1].split(',', 1)[1])
        expected = {'curve': 21, 'name': "CX-1030-SD-4L",
                    'serial': meta['serial'], 'format': meta['format'],
                    'limit': meta['limit'],
                    'coefficient': meta['coefficient']}
        verdict, _, _ = classify_verify(MODEL_340, expected, header,
                                        comparison)
        check(verdict == 'verified', verdict)

    return [
        ("fmt6 writes six digits, never an exponent", case_fmt6),
        ("a .340 file is read reading-first, with its whole header",
         case_parse_340),
        ("a .dat file's columns are located by its headings", case_parse_dat),
        ("a headerless file is refused, not guessed", case_headerless_refused),
        ("data format 5 is refused rather than read as kelvin",
         case_format_5_refused),
        ("ohms and log-ohms interconvert, ohms and volts never", case_units),
        ("thinning keeps both ends and invents nothing", case_thin),
        ("extending adds only beyond the ends", case_extend),
        ("the 340 has curves 21-60 and the 350 has 21-59", case_slot_ranges),
        ("REGRESSION: only the 340 transfer ends with CRVSAV", case_crvsav),
        ("the commands are exactly the manuals' syntax", case_command_syntax),
        ("format 5 exists on the 340 and not on the 350",
         case_format_availability),
        ("a coefficient that contradicts the data is an error",
         case_coefficient),
        ("a comma or an over-long name is refused", case_name_rules),
        ("the curve is checked against the input's sensor type",
         case_input_type),
        ("the 350's printed input-range table is used", case_range_table),
        ("a curve short of the working range warns", case_coverage),
        ("headers and points are read the way they are printed",
         case_readback_parsing),
        ("REGRESSION: three fields differing means nothing landed",
         case_classify_not_written),
        ("REGRESSION: a leftover tail past the curve is caught",
         case_classify_tail),
        ("a changed format code is its own diagnosis", case_classify_format),
        ("the readback is judged at the precision it was printed to",
         case_printed_precision),
        ("the model comes from *IDN? and nothing else", case_model_from_idn),
        ("a .340 file survives the whole round trip", case_round_trip),
    ]


def run_self_test(report=print):
    """Run every offline check. Returns True when all of them pass."""
    cases = _selftest_cases()
    failures = []
    report(f"Offline self-test: {len(cases)} checks, no instrument needed.")
    for number, (title, function) in enumerate(cases, start=1):
        try:
            function()
        except Exception as exc:
            failures.append((number, title, exc))
            report(f"  [FAIL] {number:2d}. {title}")
            report(f"         {type(exc).__name__}: {exc}")
        else:
            report(f"  [ ok ] {number:2d}. {title}")
    if failures:
        report(f"{len(failures)} of {len(cases)} checks FAILED. Do not send a "
               "curve with this build.")
    else:
        report(f"All {len(cases)} checks passed.")
    return not failures


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == '__main__' and '--selftest' in sys.argv:
    raise SystemExit(0 if run_self_test() else 1)

if __name__ == '__main__':
    root = tk.Tk()
    app = CurveLoaderGUI(root)
    if not PYVISA_AVAILABLE:
        # Not fatal: the file readers, the checks and the command list are
        # the bulk of this module and none of them need an instrument.
        messagebox.showwarning(
            "PyVISA Not Installed",
            "PyVISA is not installed, so no curve can be sent to an "
            "instrument.\n\nEverything else works: you can still read a Lake "
            "Shore file, check it and save the command list, then send those "
            "commands from any other terminal.\n\n"
            "To send from here:\n  pip install pyvisa pyvisa-py")
    root.mainloop()
