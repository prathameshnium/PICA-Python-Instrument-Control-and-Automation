"""
Module: Sensor_Curve_Loader_CC34_GUI.py
Purpose: Convert a Lake Shore calibrated-sensor file (Cernox, Ruthenium-Oxide,
         Platinum, diode ...) into a Cryo-con .crv calibration curve, send it
         to a Cryo-con Model 34 user curve slot with CALCUR, then read the
         curve back off the instrument and check it point by point.

         Written for the lab Cernox CX-1030-SD-4L sensors X17680 (in use) and
         X17681, whose Lake Shore CD files live in
         Untracked_Stuff/Curv_for_Cernox/Cernox/Calibration Data/1068/.

         The Model 34 has no Cernox curve of its own: its factory sensor list
         stops at two Ruthenium-Oxide entries (Appendix A). A Cernox therefore
         has to be installed as one of the twelve USER curves before any input
         channel can read it.

Non-destructive: this module never sends *RST, STOP, CONTROL, or any loop,
setpoint, heater or PID command. It writes exactly two kinds of thing, and
only when the operator presses the button that says so:
  - CALCUR <n>, the chosen user curve slot;
  - optionally SENTYPE / INPUT:SENIX, to name the curve and put it on a
    channel.

===============================================================================
EVERYTHING BELOW IS TAKEN FROM THE MODEL 34 MANUAL, NOT ASSUMED
===============================================================================

Source: "Cryo-con Model 34 Cryogenic Temperature Controller, User's Guide,
Edition 4, August 2006". In this repository that manual is the file
Untracked_Stuff/"The User Interface - Cryogenic Control Systems, Inc..pdf"
(the download lost its title, but page 1 is the Model 34 cover). Page numbers
below are PDF page numbers in that file.

CALCUR command (p.181-183):
  - Twelve user curves in the Model 34, numbered 1 through 12. Page 181 and
    the utility-software chapter on p.111 both say 1-12; one line on p.183
    says "1 through 11", which is a typo. This module accepts 1-12 and
    refuses anything else.
  - The block is the command line "CALCUR <index>", four header lines, the
    curve points, then a line holding a single semicolon:
        CALCUR 1
        <sensor name>        4 to 15 ASCII characters
        <sensor type>        enumeration, see SENSOR_TYPES below
        <multiplier>         signed float; the sign is the temperature coeff.
        <curve units>        OHMS | VOLTS | LOGOHM
        <reading> <temp K>   2 to 200 of these
        ;
  - THE SENSOR READING COMES FIRST AND THE TEMPERATURE SECOND. A Lake Shore
    .dat file is the other way round, so the columns are swapped on the way
    through. Getting this backwards is the single most likely way to put a
    wrong curve into the instrument, which is why the readback check exists.
  - Temperature is always in Kelvin, whatever the channel display units are.
  - Fields are separated by one or more spaces or tabs.
  - Values are stored as 32-bit floats, so about six significant digits. This
    module writes six and no more, so nothing is implied that is not kept.
  - Entries may be sent in any order; the instrument sorts them into ascending
    sensor reading before writing them to flash. Entries whose numeric fields
    do not parse are silently DELETED, which is why every point is validated
    here before anything is sent.
  - Line terminators: "each line must be terminated by a New Line, a Carriage
    Return, a Line Feed or a Null character" on RS-232. "This character is not
    used with the GPIB or USB interfaces since the end of a line is signaled
    by the interface itself. Here, lines are transmitted to the controller by
    using sequential write commands." So each line is one VISA write, and on
    GPIB/USB no terminator byte is appended. See LINE_ENDINGS.
  - Storing a curve takes up to 250 ms after the semicolon on the Model 34;
    the Model 24C guide asks for five seconds between consecutive curves.
    CURVE_SETTLE_S waits a full second, which costs nothing and covers both.
  - Factory curves cannot be changed or deleted by these commands.

Sensor types (p.22 "Supported Sensor Types", p.26 Table 4, p.197 SENTYPE:TYPE):
  - The Model 34 names a sensor type by its full-scale input and its
    excitation current: R8K10UA, R625R1MA and so on. SENSOR_TYPES below is
    Table 1 in full.
  - Table 4, "Resistor Sensor Configuration", is explicit for our sensor:
        Cernox  ->  R8K10UA,  10 uA excitation,  (-) coefficient,  LogOhms
    X17680 runs from 43.76 ohm at 330 K to 977.25 ohm at 3.59 K, so the 8
    kohm full-scale range fits it with room to spare.
  - LOGOHM is the base-10 log of ohms. The manual asks for it on Cernox,
    Ruthenium-Oxide, Germanium, Carbon Glass and thermistors because their
    resistance curve is far more linear in log form, and the instrument
    interpolates between breakpoints (p.24, p.100).
  - DANGER, and the reason this module verifies: p.109 says "If the controller
    receives a sensor type that it does not support, the 'Diode' type is
    selected." That substitution is silent, and a Cernox running on a diode
    input configuration reads nonsense. read_curve() reports the type the
    instrument actually kept.
  - The CALCUR page itself (p.183) lists an older, Model-32-era spelling of
    the type field: "Diode, ACR, 31kR, 3.1kR, 312R, 625R, TC80, TC40, None".
    The Supported Sensors table and the SENTYPE page, both written for the
    Model 34, use the R8K10UA family. The R-names are offered first here; ACR
    is kept in the list in case a firmware revision wants the old spelling,
    and either way the readback settles which one was accepted.

User curve slots and sensor index (p.220, Appendix A):
        Senix index 10 11 12 13 14 15 16 17 18 19 20 21
        User curve   1  2  3  4  5  6  7  8  9 10 11 12
  - CALCUR addresses the USER number, 1-12.
  - INPUT <ch>:SENIX addresses the SENIX number, so user curve n is senix n+9.
  - "INPUT A:SENIX 20 would set input A to use User Curve B" (user 11).
  - Firmware note: the Rev 3.03A unit in this lab also answers
    INPUT <ch>:ISENIX and INPUT <ch>:USENIX, which Edition 4 does not list
    (T_Control_CC34_DirectControl_GUI.py uses those). The two schemes number
    differently, so this module does not guess. The channel-assignment step
    queries first, sends SENIX, reads it back, and reports what happened; if
    the readback disagrees it stops rather than trying another spelling on
    its own.

Lake Shore source files (Lake Shore Sensor CD v1.1, Calibration Data/1068/):
  X17680.340  PREFER THIS ONE. Lake Shore's own 129-breakpoint curve for a
              Model 340: already in log-ohm, already reading-first, covering
              4.000 to 325.000 K, with the sensor model, serial and the sign
              of the temperature coefficient in its header.
  X17680.dat  the raw calibration: "Temperature (Kelvin)" then "Resistance
              (Ohms)", two header lines and a blank line, then 71 points from
              3.5913 K / 977.25 ohm to 330.03 K / 43.761 ohm.
  X17680.tbl  the same calibration interpolated onto a tidy grid, with
              sensitivity columns this module ignores.
  X17680.crv  not on the CD; written by this module.
  The .cof, .91C, .234 and .330 files are for other instruments and are not
  read here.

Why the .340 rather than the raw .dat, when the .dat is the measurement and
the .340 is derived from it: a Cryo-con interpolates linearly between the
breakpoints it stores, and a .340 file is a breakpoint table built for exactly
that kind of instrument, so Lake Shore placed its 129 points where the
interpolation error is worst rather than where the measurement happened to
land. Tested by leave-one-out against the 71 measured points of X17680 (see
tests/test_cryocon_curve_loader.py), the worst error the instrument would make
between breakpoints is about 23 mK with the .340 and about 39 mK with the
.dat. Both are far below what the sensor itself is calibrated to, so this is
not a decision worth agonising over; it is simply the better of two good
options. The .dat is the one to use if the 3.59-4.0 K or 325-330 K ends of the
range are actually wanted, since the .340 does not reach them.

Column order is never guessed. Each Lake Shore format is identified by its own
header text and the columns are located by name, so a file whose columns are
the other way round is rejected instead of being loaded backwards.

Communication follows the pattern proved by T_Control_CC34_DirectControl_GUI.py
on this lab's Model 34 Rev 3.03A: open, settle, retry the first *IDN?, and pace
every operation, because back-to-back traffic is what made that firmware stop
accepting bytes in the middle of a write.

v1.0, 29 Aug 2026.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext, Canvas
import math
import os
import re
import time
import threading
import traceback
from datetime import datetime

# --- Optional packages -----------------------------------------------------
# Each of these is a feature, not a requirement. The curve maths, the .crv
# writer and the instrument transfer all work with none of them installed.

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

# Matplotlib draws the curve so it can be looked at before it is sent. The
# manual asks for exactly this: "At this point, it is a good idea to view a
# graph of the curve data ... Check the graph for reasonableness." Without it
# the module falls back to the numeric table, which is always shown.
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
# UTILITY LAUNCHERS (identical to the sibling Cryocon modules)
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
# from a copy of the repository tree, and as a single file dropped somewhere on
# its own. find_pica_root() looks for the package in three ways and gives up
# quietly. Everything downstream treats a missing resource as a disabled
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
# CURVE MODEL
# ===============================================================================

# --- Limits from the CALCUR section of the manual --------------------------
MIN_USER_CURVE = 1
MAX_USER_CURVE = 12          # Model 34 and Model 62; the Model 32 has 4
SENIX_OFFSET = 9             # user curve n is Master Sensor Table index n + 9
MIN_CURVE_POINTS = 2
MAX_CURVE_POINTS = 200
MIN_NAME_CHARS = 4
MAX_NAME_CHARS = 15

# Sensor units the CALCUR header will accept. LOGOHM is base-10 log of ohms.
CURVE_UNITS = ('LOGOHM', 'OHMS', 'VOLTS')

# Supported Sensor Types, Model 34 manual Table 1 (p.22), with the full-scale
# input each one presents. The full scale is what makes a sensor type wrong or
# right for a given curve, so it is carried here and checked against the data.
#
# Two quirks of the printed table are preserved deliberately:
#   - R3K100UA is named for 3 kohm but Table 1 gives its full scale as 2 kohm.
#     The table value is used, because that is the number the instrument was
#     specified against, and it is the conservative one.
#   - 'ACR' is not in Table 1. It is the Model 32 / 24C spelling for a generic
#     AC resistance sensor and appears in the CALCUR page's own type list. It
#     is offered last, as a fallback if a firmware revision rejects R8K10UA.
#
# fields: (full scale, unit of that full scale, what it is for)
SENSOR_TYPES = {
    'R8K10UA':   (8.0e3,  'ohm',
                  "8 kohm FS, 10 uA - Cernox, RuOx, Germanium, Carbon Glass, "
                  "thermistors (manual Table 4)"),
    'R16K10UA':  (16.0e3, 'ohm', "16 kohm FS, 10 uA - PTC/NTC resistors, Pt 10k"),
    'R31K10UA':  (31.3e3, 'ohm', "31.3 kohm FS, 10 uA - Pt 10k / NTC resistors"),
    'R62K10UA':  (62.5e3, 'ohm', "62.5 kohm FS, 10 uA - Pt 10k / NTC resistors"),
    'R25K100UA': (25.0e3, 'ohm', "25 kohm FS, 100 uA - PTC/NTC resistors"),
    'R12K100UA': (12.5e3, 'ohm', "12.5 kohm FS, 100 uA - Platinum 1000"),
    'R6K100UA':  (6.25e3, 'ohm', "6.25 kohm FS, 100 uA - Platinum 1000"),
    'R3K100UA':  (2.0e3,  'ohm',
                  "2 kohm FS per Table 1, 100 uA - Platinum 1000"),
    'R625R1MA':  (625.0,  'ohm', "625 ohm FS, 1 mA - Platinum 100 above 800 K"),
    'R312R1MA':  (312.0,  'ohm', "312 ohm FS, 1 mA - Platinum 100 below 800 K"),
    'R156R1MA':  (156.0,  'ohm', "156 ohm FS, 1 mA - Rhodium-Iron"),
    'Diode':     (2.5,    'V',   "2.5 V FS, 10 uA - silicon and GaAlAs diodes"),
    'TC80':      (0.078,  'V',   "78 mV FS - thermocouple"),
    'TC40':      (0.039,  'V',   "39 mV FS - thermocouple"),
    'ACR':       (None,   '',
                  "Model 32 / 24C spelling for a generic AC resistance "
                  "sensor; try only if R8K10UA is rejected"),
}

# What the Cernox needs, from Table 4 of the manual. Used by the one-click
# "recommended settings" button so the operator does not have to transcribe it.
CERNOX_DEFAULTS = {
    'sensor_type': 'R8K10UA',
    'multiplier': '-1.0',
    'units': 'LOGOHM',
}

# How each line of a CALCUR block is terminated on the wire.
#   Auto  - the manual's rule: nothing on GPIB/USB, a line feed on RS-232 and
#           LAN, decided from the VISA resource string.
# The explicit choices exist so that, if a firmware revision disagrees with the
# manual, the operator can try another and let the readback decide, without
# anyone editing this file.
LINE_ENDINGS = {
    'Auto (manual rule for this interface)': None,
    'Nothing (GPIB / USB rule)': b'',
    'Line feed  \\n': b'\n',
    'Carriage return + line feed  \\r\\n': b'\r\n',
}

# Lake Shore "Data Format" codes as printed in the .340 / .330 header, mapped
# to the Cryo-con units they are already in. Formats outside this table (such
# as log ohm vs log Kelvin) are refused by name rather than misread.
LAKESHORE_FORMATS = {
    1: ('VOLTS', 1.0e-3, "mV/K"),
    2: ('VOLTS', 1.0,    "V/K"),
    3: ('OHMS',  1.0,    "Ohm/K"),
    4: ('LOGOHM', 1.0,   "Log Ohm/K"),
}


class CurveFileError(ValueError):
    """A sensor file could not be read with certainty."""


def fmt6(value):
    """Six significant digits, in plain decimal.

    Six is what a Cryo-con keeps: the manual says curve values are converted
    to 32-bit floats. Writing more would imply precision the instrument does
    not store and would make the readback comparison look worse than it is.

    Exponent notation is avoided because the firmware's number parser is not
    documented, and a value written as '1e-05' that were read as '1' would be
    wrong by five orders of magnitude without looking wrong.

    Every value keeps a decimal point for the same reason. The manual's own
    examples are all written that way ('-1.0', '300.1205'), and it warns that
    a header field the instrument cannot identify is replaced with a default
    rather than reported: the multiplier silently becomes -1.0 and the sensor
    type silently becomes Diode. A decimal point costs one byte and removes
    any question of how a bare integer is read.
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
    a Cryo-con .crv is reading-first, and the two are indistinguishable once
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
        # 'sensitivity' and 'dimensionless' also contain 'ohm'-ish words in
        # their unit rows, so the reading column is taken from the first
        # match only, and the .tbl sensitivity columns fall past it.
        if word.startswith('resist') or word == 'ohms' or word == 'ohm':
            reading_index, units = index, 'OHMS'
        elif word.startswith('volt'):
            reading_index, units = index, 'VOLTS'
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
            "Use the original Lake Shore .dat, .tbl or .340 file, or a "
            ".crv file, all of which say which column is which.")
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
    # present it is used to confirm what the first line implied; a
    # disagreement is reported rather than resolved silently.
    if unit_tokens and len(unit_tokens) > reading_index:
        stated = unit_tokens[reading_index].lower().strip('()')
        confirmed = {'ohms': 'OHMS', 'ohm': 'OHMS',
                     'volts': 'VOLTS', 'volt': 'VOLTS',
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
                f"'{unit_tokens[temp_index]}', not Kelvin. A Cryo-con curve "
                "is always in Kelvin, so this file is refused.")

    points = []
    for tokens in rows:
        points.append((float(tokens[temp_index]), float(tokens[reading_index])))

    meta = {
        'columns': f"column {temp_index + 1} = temperature, "
                   f"column {reading_index + 1} = sensor reading",
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

    Here the units column comes first and the temperature second, which is
    the Cryo-con order, so nothing is swapped. The Data Format code says what
    the units column holds.
    """
    meta = {}
    fmt_code = None
    stated_count = None
    coefficient = None

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
        match = re.match(r'Temperature coefficient:\s*(\d+)', stripped, re.I)
        if match:
            # Lake Shore: 1 = negative, 2 = positive.
            coefficient = -1.0 if int(match.group(1)) == 1 else 1.0
            continue

    if fmt_code is None:
        raise CurveFileError(
            f"{source_name} has no 'Data Format:' line, so the units of its "
            "middle column are unknown and it cannot be converted safely.")
    if fmt_code not in LAKESHORE_FORMATS:
        known = ", ".join(str(k) for k in sorted(LAKESHORE_FORMATS))
        raise CurveFileError(
            f"{source_name} is Lake Shore data format {fmt_code}, which this "
            f"module does not convert. Only formats {known} map onto the "
            "Cryo-con units OHMS, VOLTS and LOGOHM without an assumption.\n\n"
            "Use the .dat file for this sensor instead: it is the raw "
            "calibration and needs no format code.")

    units, scale, format_name = LAKESHORE_FORMATS[fmt_code]
    meta['lakeshore_format'] = f"{fmt_code} ({format_name})"
    if coefficient is not None:
        meta['stated_multiplier'] = coefficient
    meta['columns'] = ("column 2 = sensor reading, column 3 = temperature "
                       "(Lake Shore .340 order, already reading-first)")

    points = []
    for line in lines:
        tokens = line.split()
        if not _tokens_are_numeric(tokens, count=3):
            continue
        # First token is the breakpoint number and must be a whole number
        # counting up; anything else means this is not a breakpoint row.
        index, reading, temperature = tokens
        if float(index) != int(float(index)):
            continue
        points.append((float(temperature), float(reading) * scale))

    if not points:
        raise CurveFileError(f"{source_name} contains no breakpoint rows.")
    if stated_count is not None and stated_count != len(points):
        raise CurveFileError(
            f"{source_name} says it holds {stated_count} breakpoints but "
            f"{len(points)} were read. The file is refused rather than "
            "loaded partially.")
    return points, units, meta


def parse_crv_text(text, source_name=".crv data"):
    """Read a Cryo-con .crv block, or the reply to a CALCUR? query.

    Returns (header, points) where header holds name / sensor_type /
    multiplier / units and points is a list of (temperature, reading), the
    same shape every other reader returns.
    """
    lines = [line.strip() for line in _clean_lines(text)]
    lines = [line for line in lines if line]
    if len(lines) < 6:
        raise CurveFileError(
            f"{source_name} is too short to be a curve: a header of four "
            f"lines, at least two points and a semicolon are needed, but "
            f"{len(lines)} non-blank lines were found.")

    # A reply to 'CALCUR? n' may still carry the echoed command, and a file
    # written by this module never does. Either is accepted.
    if re.match(r'^CALCUR\??\s', lines[0], re.I):
        lines = lines[1:]

    name = lines[0]
    sensor_type = lines[1]
    try:
        multiplier = float(lines[2])
    except ValueError:
        raise CurveFileError(
            f"{source_name}: the third header line should be the multiplier, "
            f"a signed number, but it reads '{lines[2]}'.")
    units = lines[3].upper()
    if units not in CURVE_UNITS:
        raise CurveFileError(
            f"{source_name}: the fourth header line should be the curve "
            f"units, one of {', '.join(CURVE_UNITS)}, but it reads "
            f"'{lines[3]}'.")

    points = []
    terminated = False
    for line in lines[4:]:
        if line.startswith(';'):
            terminated = True
            break
        tokens = line.split()
        if not _tokens_are_numeric(tokens, count=2):
            raise CurveFileError(
                f"{source_name}: '{line}' is not a pair of numbers. A curve "
                "entry is the sensor reading then the temperature in Kelvin.")
        reading, temperature = float(tokens[0]), float(tokens[1])
        points.append((temperature, reading))

    if not terminated:
        raise CurveFileError(
            f"{source_name} does not end with the semicolon line that marks "
            "the end of a Cryo-con curve, so it may be truncated.")

    header = {
        'name': name,
        'sensor_type': sensor_type,
        'multiplier': multiplier,
        'units': units,
    }
    return header, points


def load_sensor_file(path):
    """Read any supported sensor file.

    Returns a dict:
        points        [(temperature_K, reading), ...] in the file's own units
        units         'OHMS' | 'VOLTS' | 'LOGOHM'
        kind          which reader was used
        meta          whatever the file said about itself
        header        the .crv header, for a .crv file; otherwise None
    """
    text = _read_text(path)
    lines = _clean_lines(text)
    name = os.path.basename(path)
    extension = os.path.splitext(path)[1].lower()

    # A .crv is self-describing, so it is recognised by content as well as by
    # extension: a file renamed on the way out of the utility software is
    # still read correctly.
    looks_like_crv = any(line.strip() == ';' for line in lines)
    if extension == '.crv' or (looks_like_crv and extension not in
                               ('.dat', '.tbl', '.340', '.330')):
        header, points = parse_crv_text(text, name)
        return {'points': points, 'units': header['units'], 'kind': 'crv',
                'meta': {'columns': "Cryo-con order: reading, then Kelvin"},
                'header': header}

    if any(re.match(r'\s*Data Format:', line, re.I) for line in lines):
        points, units, meta = _parse_lakeshore_340(lines, name)
        return {'points': points, 'units': units, 'kind': 'lakeshore-340',
                'meta': meta, 'header': None}

    points, units, meta = _parse_lakeshore_columns(lines, name)
    kind = 'lakeshore-tbl' if extension == '.tbl' else 'lakeshore-dat'
    return {'points': points, 'units': units, 'kind': kind,
            'meta': meta, 'header': None}


def convert_units(points, from_units, to_units):
    """Convert (temperature, reading) pairs between curve units.

    Only OHMS and LOGOHM interconvert. Volts is a different measurement and
    is never derived from a resistance, so asking for that is an error rather
    than a silent no-op.
    """
    if from_units == to_units:
        return list(points)
    if {from_units, to_units} == {'OHMS', 'LOGOHM'}:
        converted = []
        for temperature, reading in points:
            if from_units == 'OHMS':
                if reading <= 0:
                    raise CurveFileError(
                        f"A resistance of {reading} ohm at {temperature} K "
                        "has no logarithm, so this curve cannot be written "
                        "in LOGOHM. Use OHMS, or check the file.")
                converted.append((temperature, math.log10(reading)))
            else:
                converted.append((temperature, 10.0 ** reading))
        return converted
    raise CurveFileError(
        f"A curve in {from_units} cannot be turned into {to_units}: they "
        "measure different things. Choose {from_units} for this file, or "
        "load a file that is already in {to_units}.".format(
            from_units=from_units, to_units=to_units))


def thin_points(points, limit):
    """Reduce a curve to at most `limit` points, keeping both ends.

    The instrument holds 200 entries. Points are dropped at even spacing
    through the list rather than by any cleverness, so what survives is a
    plain subset of the calibration and nothing is invented by interpolation.
    """
    count = len(points)
    if count <= limit:
        return list(points), 0
    keep_indices = sorted({round(i * (count - 1) / (limit - 1))
                           for i in range(limit)})
    thinned = [points[i] for i in keep_indices]
    return thinned, count - len(thinned)


def analyse_curve(points, units, sensor_type, multiplier, name):
    """Check a curve against everything the manual requires of one.

    Returns (errors, warnings, stats). Errors block the transfer; warnings do
    not, but each one names something worth a second look.
    """
    errors = []
    warnings = []
    stats = {}

    if not points:
        return (["The curve holds no points."], [], stats)

    # Sort the way the instrument will, so what is checked is what it stores.
    ordered = sorted(points, key=lambda pair: pair[1])
    readings = [reading for _, reading in ordered]
    temperatures = [temperature for temperature, _ in ordered]

    stats['count'] = len(ordered)
    stats['t_min'] = min(temperatures)
    stats['t_max'] = max(temperatures)
    stats['r_min'] = min(readings)
    stats['r_max'] = max(readings)

    if len(ordered) < MIN_CURVE_POINTS:
        errors.append(
            f"A Cryo-con curve needs at least {MIN_CURVE_POINTS} points; "
            f"this one has {len(ordered)}.")
    if len(ordered) > MAX_CURVE_POINTS:
        errors.append(
            f"A Cryo-con curve holds at most {MAX_CURVE_POINTS} points; "
            f"this one has {len(ordered)}. Thin it before sending.")

    for temperature, reading in ordered:
        if not (math.isfinite(temperature) and math.isfinite(reading)):
            errors.append("The curve contains a value that is not a finite "
                          "number.")
            break
    if any(temperature <= 0 for temperature in temperatures):
        errors.append(
            "The curve contains a temperature of zero or below. Cryo-con "
            "curve temperatures are absolute, in Kelvin.")
    if units in ('OHMS', 'LOGOHM') and units == 'OHMS' and \
            any(reading <= 0 for reading in readings):
        errors.append("The curve contains a resistance of zero or below.")

    # Duplicate sensor readings make the curve ambiguous: the instrument
    # interpolates on the reading, so two temperatures at one reading has no
    # single answer. The instrument would keep both and sort them adjacent.
    duplicates = [readings[i] for i in range(1, len(readings))
                  if readings[i] == readings[i - 1]]
    if duplicates:
        errors.append(
            f"{len(duplicates)} point(s) repeat a sensor reading "
            f"(for example {fmt6(duplicates[0])}). The instrument "
            "interpolates on the sensor reading, so a repeated reading has "
            "no single temperature.")

    # Temperature must move one way along the curve or the sensor is not
    # usable as a thermometer over that span.
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

    # Sign of the temperature coefficient, read off the data rather than
    # taken on trust, and compared with the multiplier that will be sent.
    negative_coefficient = temperatures[-1] < temperatures[0]
    stats['coefficient'] = 'negative' if negative_coefficient else 'positive'
    if negative_coefficient and multiplier > 0:
        errors.append(
            "The data has a negative temperature coefficient (the reading "
            "rises as it gets colder, which is what a Cernox does), but the "
            f"multiplier is {multiplier:+g}. The manual gives -1.0 for an "
            "NTC sensor. Sending a positive multiplier here would tell the "
            "instrument the sensor behaves the opposite way.")
    if (not negative_coefficient) and multiplier < 0:
        errors.append(
            "The data has a positive temperature coefficient (the reading "
            "rises as it gets warmer, which is what a Platinum RTD does), "
            f"but the multiplier is {multiplier:+g}. The manual gives 1.0 "
            "for a PTC sensor.")
    if multiplier == 0:
        errors.append("The multiplier cannot be zero: its sign is the "
                      "sensor's temperature coefficient.")

    # Name rules, from the CALCUR page.
    if len(name) < MIN_NAME_CHARS:
        errors.append(
            f"The curve name '{name}' is shorter than the {MIN_NAME_CHARS} "
            "characters the manual requires.")
    if len(name) > MAX_NAME_CHARS:
        errors.append(
            f"The curve name '{name}' is {len(name)} characters; the "
            f"instrument keeps {MAX_NAME_CHARS} and truncates the rest.")
    if not all(32 <= ord(character) < 127 for character in name):
        errors.append(
            f"The curve name '{name}' contains a character that is not "
            "printable ASCII. The instrument stores an ASCII string.")

    # Sensor type against the data. This is the check that catches a Cernox
    # about to be installed on a 312 ohm Platinum range.
    if sensor_type not in SENSOR_TYPES:
        warnings.append(
            f"'{sensor_type}' is not one of the sensor types listed for the "
            "Model 34. If the instrument does not recognise it, the manual "
            "says it silently substitutes 'Diode'. The readback will show "
            "what it actually kept.")
    else:
        full_scale, full_scale_unit, _ = SENSOR_TYPES[sensor_type]
        if full_scale is not None:
            peak_ohms = None
            if units == 'OHMS':
                peak_ohms = stats['r_max']
            elif units == 'LOGOHM':
                peak_ohms = 10.0 ** stats['r_max']
            if peak_ohms is not None and full_scale_unit == 'ohm':
                stats['peak_ohms'] = peak_ohms
                stats['full_scale'] = full_scale
                if peak_ohms > full_scale:
                    errors.append(
                        f"The curve reaches {peak_ohms:,.1f} ohm but sensor "
                        f"type {sensor_type} has a full scale of "
                        f"{full_scale:,.0f} ohm. The coldest part of the "
                        "curve would be off the top of the input range.")
                elif peak_ohms > 0.9 * full_scale:
                    warnings.append(
                        f"The curve reaches {peak_ohms:,.1f} ohm, which is "
                        f"{100 * peak_ohms / full_scale:.0f}% of the "
                        f"{full_scale:,.0f} ohm full scale of {sensor_type}. "
                        "There is little headroom below the coldest "
                        "calibrated point.")
            if units == 'VOLTS' and full_scale_unit != 'V':
                errors.append(
                    f"The curve is in VOLTS but sensor type {sensor_type} is "
                    "a resistance input.")
            if units in ('OHMS', 'LOGOHM') and full_scale_unit != 'ohm':
                errors.append(
                    f"The curve is in {units} but sensor type "
                    f"{sensor_type} is a voltage input.")

    if units == 'OHMS' and stats['r_max'] > 0 and stats['r_min'] > 0 and \
            stats['r_max'] / stats['r_min'] > 20:
        warnings.append(
            "This resistance curve spans more than a factor of 20. The "
            "manual recommends LOGOHM for curves like this: the instrument "
            "interpolates between breakpoints, and in plain ohms the curve "
            "is steep enough for that to lose accuracy at the cold end.")

    return errors, warnings, stats


def build_crv_lines(name, sensor_type, multiplier, units, points):
    """The body of a CALCUR block: header, points, terminator.

    Points are written in ascending sensor reading. The instrument sorts them
    itself, but sending them sorted means the file on disk and the curve in
    the instrument read the same way round, which is what makes the readback
    comparison legible.
    """
    ordered = sorted(points, key=lambda pair: pair[1])
    lines = [name, sensor_type, fmt6(multiplier), units]
    for temperature, reading in ordered:
        lines.append(f"{fmt6(reading)}   {fmt6(temperature)}")
    lines.append(";")
    return lines


def crv_file_text(lines):
    """The .crv file: the same lines, terminated with line feeds.

    ASCII only. The manual describes an ASCII text file, and the Cryo-con
    utility software has to be able to read this back.
    """
    text = "\n".join(lines) + "\n"
    text.encode('ascii')          # raises rather than writing a bad file
    return text


def compare_curves(sent_points, read_points, tolerance=1e-4):
    """Compare the curve that was sent with the curve read back.

    Both lists are sorted by sensor reading first, because that is the order
    the instrument stores them in regardless of the order they arrived.

    Returns a dict describing the comparison. The tolerance is relative and
    generous next to the six significant digits that were sent, so a failure
    here means a real difference and not a rounding artefact.
    """
    sent = sorted(sent_points, key=lambda pair: pair[1])
    read = sorted(read_points, key=lambda pair: pair[1])
    result = {
        'sent_count': len(sent),
        'read_count': len(read),
        'matched': False,
        'worst_reading_error': 0.0,
        'worst_temperature_error': 0.0,
        'worst_index': None,
        'problems': [],
    }
    if len(sent) != len(read):
        result['problems'].append(
            f"{len(sent)} points were sent but {len(read)} came back. The "
            "instrument deletes entries whose numeric fields it cannot "
            "parse, so a shortfall means some lines did not arrive intact.")
        return result

    for index, ((sent_t, sent_r), (read_t, read_r)) in \
            enumerate(zip(sent, read)):
        reading_error = abs(sent_r - read_r) / max(abs(sent_r), 1e-12)
        temperature_error = abs(sent_t - read_t) / max(abs(sent_t), 1e-12)
        if reading_error > result['worst_reading_error'] or \
                temperature_error > result['worst_temperature_error']:
            if max(reading_error, temperature_error) > \
                    max(result['worst_reading_error'],
                        result['worst_temperature_error']):
                result['worst_index'] = index
            result['worst_reading_error'] = max(
                result['worst_reading_error'], reading_error)
            result['worst_temperature_error'] = max(
                result['worst_temperature_error'], temperature_error)
        if reading_error > tolerance or temperature_error > tolerance:
            if len(result['problems']) < 8:
                result['problems'].append(
                    f"Point {index + 1}: sent "
                    f"{fmt6(sent_r)} -> {fmt6(sent_t)} K, "
                    f"read back {fmt6(read_r)} -> {fmt6(read_t)} K.")

    result['matched'] = not result['problems']
    return result


# ===============================================================================
# CRYOCON LINK HARDENING  (inlined so this module stays standalone)
# ===============================================================================
#
# Copied from T_Control_CC34_DirectControl_GUI.py, where it was written after
# two failures on this lab's Model 34 Rev 3.03A:
#
#   1. The bus scan identified the instrument, and the very next session's
#      '*IDN?' died inside viWrite with VI_ERROR_TMO. A timeout on the WRITE
#      means the instrument stopped accepting bytes for a moment, not that it
#      is absent, so the cure is to wait and ask again.
#   2. A reading query answered with a Cryo-con status string instead of a
#      number and float() raised, killing the worker thread.
#
# Nothing in this block writes to the instrument.

CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON")
PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")
CRYOCON_ADDRESS_HINT = "GPIB0::12"
IDN_SCAN_TIMEOUT_MS = 1200

CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before first command
CRYOCON_MIN_GAP_S = 0.08            # minimum gap between operations
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries
ALLOW_DEVICE_CLEAR_ON_RETRY = False

# Extra pacing for the curve transfer itself. A curve is up to 205 writes in a
# row, which is far more back-to-back traffic than any other operation this
# instrument sees from this repository, and back-to-back traffic is exactly
# what provoked the write timeout above.
CURVE_LINE_GAP_S = 0.06
CURVE_SETTLE_S = 1.0                # after the semicolon, while flash is written
CURVE_READ_TIMEOUT_MS = 4000        # per line of a CALCUR? reply
CURVE_READ_MAX_LINES = MAX_CURVE_POINTS + 12

CRYOCON_STATUS_STRINGS = {
    '-------': "sensor fault: the sensor is open, disconnected or shorted",
    '.......': ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    'N/A': "the channel is disabled, or the value does not apply",
    'NACK': "the instrument did not acknowledge the command",
}


def is_cryocon_idn(idn):
    """True if a '*IDN?' reply came from a Cryo-con temperature instrument."""
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


class CryoconLink:
    """One paced VISA session to a Cryo-con, opened with retries."""

    def __init__(self, visa_address, timeout_ms=CRYOCON_TIMEOUT_MS, log=None):
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
        for attempt in range(1, CRYOCON_CONNECT_ATTEMPTS + 1):
            try:
                self.instrument = self.rm.open_resource(self.address)
                self.instrument.timeout = self.timeout_ms
                # The Cryocon GPIB port frames lines with EOI and no EOS
                # character, so the PyVISA termination defaults are left alone
                # for ordinary commands. The curve transfer bypasses them
                # entirely and writes raw bytes; see write_line().
                time.sleep(CRYOCON_OPEN_SETTLE_S)
                if attempt > 1 and ALLOW_DEVICE_CLEAR_ON_RETRY:
                    try:
                        self.instrument.clear()
                        time.sleep(CRYOCON_OPEN_SETTLE_S)
                    except Exception as exc:
                        self._log(f"  Device clear declined: {exc}")
                self.idn = self.query('*IDN?')
                if not self.idn:
                    raise ConnectionError(
                        f"{self.address} accepted the command but sent no "
                        "identification.")
                if attempt > 1:
                    self._log(f"  Cryocon answered on attempt {attempt}.")
                return
            except Exception as exc:
                last_error = exc
                self._drop_session()
                if attempt < CRYOCON_CONNECT_ATTEMPTS:
                    self._log(
                        f"  Cryocon did not answer at {self.address} "
                        f"(attempt {attempt} of {CRYOCON_CONNECT_ATTEMPTS}): "
                        f"{type(exc).__name__}. Retrying in "
                        f"{CRYOCON_RETRY_WAIT_S:.1f} s.")
                    time.sleep(CRYOCON_RETRY_WAIT_S)
        raise ConnectionError(
            f"No reply from a Cryo-con at {self.address} after "
            f"{CRYOCON_CONNECT_ATTEMPTS} attempts. Last error: {last_error}. "
            "Check that the instrument is powered, that its SYS menu has "
            "RIO-Port set to GPIB rather than RS-232, and that RIO-Address "
            "matches this VISA address.")

    # -- paced I/O --

    def _pace(self, gap=CRYOCON_MIN_GAP_S):
        """Hold a minimum gap between operations."""
        wait = gap - (time.time() - self._last_io)
        if wait > 0:
            time.sleep(wait)

    def query(self, command):
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        self._pace()
        try:
            reply = self.instrument.query(command)
        finally:
            self._last_io = time.time()
        return reply.strip()

    def write(self, command):
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        self._pace()
        try:
            self.instrument.write(command)
        finally:
            self._last_io = time.time()

    def write_line(self, line, ending, gap=CURVE_LINE_GAP_S):
        """Send one line of a CALCUR block, byte for byte.

        write_raw is used rather than write() because the manual is specific
        about what terminates a line of curve data, and PyVISA's write()
        appends its own write_termination behind our back. Here the bytes on
        the wire are exactly the ones chosen in the GUI, and EOI is asserted
        at the end of the buffer, which is how the Model 34 frames a line on
        GPIB.
        """
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        payload = line.encode('ascii') + (ending or b'')
        self._pace(gap)
        try:
            self.instrument.write_raw(payload)
        finally:
            self._last_io = time.time()

    def read_line(self):
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        self._pace()
        try:
            return self.instrument.read().strip()
        finally:
            self._last_io = time.time()

    def reconnect(self):
        """Drop the session and open a fresh one. Sends no SCPI beyond
        '*IDN?'."""
        self._drop_session()
        time.sleep(CRYOCON_RETRY_WAIT_S)
        self._open_and_identify()

    @property
    def is_connected(self):
        return self.instrument is not None

    def close(self):
        """Close the session only. No *RST, no STOP, no heater, loop or
        setpoint command, so whatever is driving the cryostat carries on."""
        self._drop_session()


class CurveLoaderBackend:
    """Everything this module says to a Cryo-con Model 34.

    Read-only except for send_curve(), set_sensor_name() and
    assign_curve_to_channel(), each of which is driven by its own button.
    """

    INPUT_CHANNELS = ['A', 'B', 'C', 'D']

    def __init__(self, log=None):
        self.link = None
        self.rm = None
        self.log = log if callable(log) else (lambda msg: print(msg))
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as exc:
                print(f"Could not initialize VISA: {exc}")
                self.rm = None

    # -- connection --

    def scan_resources(self):
        if not self.rm:
            return []
        return list(self.rm.list_resources())

    def identify_resources(self, resources):
        """Return {resource: idn} for every resource that answers *IDN?.

        Never raises: an address that is busy, silent or not SCPI simply does
        not appear. Serial resources are not probed at all, because on a
        Windows rack ASRL1 is as likely to be a UPS as an instrument and a
        '*IDN?' there blocks for the whole timeout.
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

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError(
                "PyVISA ResourceManager not available. Install pyvisa and a "
                "VISA backend (NI-VISA or pyvisa-py).")
        self.link = CryoconLink(visa_address, log=self.log)
        idn = self.link.idn
        # This module rewrites a calibration curve slot. GPIB addresses get
        # changed, so confirm what actually answered before sending CALCUR to
        # it; refuse anything that is not a Cryo-con.
        if not is_cryocon_idn(idn):
            self.disconnect()
            raise ConnectionError(
                f"{visa_address} is not a Cryo-con: it identifies itself as "
                f"'{idn}'. Refusing to send curve data. Scan the bus and pick "
                f"the Cryocon's actual address (it does not have to be "
                f"{CRYOCON_ADDRESS_HINT}).")
        return idn

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

    @property
    def is_connected(self):
        return self.link is not None and self.link.is_connected

    def resolve_line_ending(self, chosen):
        """The bytes that terminate each line of a CALCUR block.

        'Auto' follows the manual: nothing on GPIB and USB, where the
        interface signals the end of a line itself, and a line feed on RS-232
        and LAN, where it does not.
        """
        if chosen is not None:
            return chosen
        address = (self.link.address if self.link else '').upper()
        if address.startswith(('GPIB', 'USB')):
            return b''
        return b'\n'

    # -- curve transfer --

    def send_curve(self, index, lines, line_ending, progress=None,
                   should_stop=None):
        """Send one CALCUR block, line by line.

        `lines` is what build_crv_lines() returned: four header lines, the
        points, and the closing semicolon. The command line is added here so
        the same list can be written to a .crv file unchanged.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        if not (MIN_USER_CURVE <= index <= MAX_USER_CURVE):
            raise ValueError(
                f"User curve index must be {MIN_USER_CURVE} to "
                f"{MAX_USER_CURVE} on a Model 34, not {index}.")
        ending = self.resolve_line_ending(line_ending)
        block = [f"CALCUR {index}"] + list(lines)
        for position, line in enumerate(block):
            if should_stop is not None and should_stop():
                raise RuntimeError(
                    f"Stopped after {position} of {len(block)} lines. The "
                    f"curve in slot {index} is now partial: send it again "
                    "before using it.")
            self.link.write_line(line, ending)
            if progress:
                progress(position + 1, len(block), line)
        # The manual: the instrument conditions, sorts and copies the curve to
        # flash once the semicolon arrives, and that takes up to 250 ms on a
        # Model 34. Nothing is asked of it until that is done.
        time.sleep(CURVE_SETTLE_S)
        return len(block)

    def read_curve(self, index):
        """Read a user curve back with CALCUR?.

        The reply is a header and up to 200 points. On GPIB each line arrives
        as its own message, so lines are read until the closing semicolon; on
        an interface that packs the whole block into one message, the first
        read returns everything and the loop ends immediately. Both are
        handled without knowing in advance which one this is.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        if not (MIN_USER_CURVE <= index <= MAX_USER_CURVE):
            raise ValueError(
                f"User curve index must be {MIN_USER_CURVE} to "
                f"{MAX_USER_CURVE} on a Model 34, not {index}.")

        instrument = self.link.instrument
        previous_timeout = instrument.timeout
        collected = []
        try:
            instrument.timeout = CURVE_READ_TIMEOUT_MS
            self.link.write(f"CALCUR? {index}")
            for _ in range(CURVE_READ_MAX_LINES):
                try:
                    chunk = self.link.read_line()
                except Exception:
                    # A timeout here is how a Cryo-con says "that was the last
                    # line". It is only a failure if the semicolon never came,
                    # and the caller decides that from the text.
                    break
                if chunk:
                    collected.append(chunk)
                if any(part.strip() == ';'
                       for part in chunk.replace('\r', '\n').split('\n')):
                    break
        finally:
            try:
                instrument.timeout = previous_timeout
            except Exception:
                pass
        return "\n".join(collected)

    # -- sensor table and channel assignment --

    @staticmethod
    def senix_for_user_curve(index):
        """Master Sensor Table index for user curve `index` (manual p.220)."""
        return index + SENIX_OFFSET

    def get_sensor_table_entry(self, senix):
        """Name, type and multiplier of one Master Sensor Table entry.

        Every field is a query. Any of them may be unsupported on a given
        firmware revision, so each is reported as whatever came back, or as
        None if the instrument would not answer.
        """
        entry = {}
        for key, command in (
                ('name', f"SENTYPE? {senix}"),
                ('type', f"SENTYPE {senix}:TYPE?"),
                ('multiplier', f"SENTYPE {senix}:MULTIPLY?")):
            try:
                entry[key] = self.link.query(command)
            except Exception as exc:
                entry[key] = None
                entry[f"{key}_error"] = f"{type(exc).__name__}: {exc}"
        return entry

    def set_sensor_name(self, senix, name):
        """Name a user sensor. Writes SENTYPE <ix>:NAME and reads it back."""
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        self.link.write(f'SENTYPE {senix}:NAME "{name}"')
        time.sleep(CRYOCON_MIN_GAP_S)
        return self.link.query(f"SENTYPE {senix}:NAME?")

    def get_channel_sensor_index(self, channel):
        """What sensor index a channel is using.

        The Edition 4 manual documents INPUT <ch>:SENIX. The Rev 3.03A unit in
        this lab also answers ISENIX and USENIX, which that manual does not
        list, and the three number differently. All three are asked and all
        three answers are returned, so the operator can see which scheme this
        firmware is actually using instead of this module picking one.
        """
        answers = {}
        for key, command in (('SENIX', f"INPUT {channel}:SENIX?"),
                             ('ISENIX', f"INPUT {channel}:ISENIX?"),
                             ('USENIX', f"INPUT {channel}:USENIX?")):
            try:
                answers[key] = self.link.query(command)
            except Exception as exc:
                answers[key] = f"<no answer: {type(exc).__name__}>"
        return answers

    def assign_curve_to_channel(self, channel, senix):
        """Put a user curve on an input channel with INPUT <ch>:SENIX.

        Returns what the instrument says the channel is set to afterwards.
        The caller compares; this method does not retry with another spelling,
        because the alternatives number differently and a wrong guess would
        quietly put the channel on somebody else's curve.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        if channel not in self.INPUT_CHANNELS:
            raise ValueError(f"Input channel must be one of "
                             f"{self.INPUT_CHANNELS}, not {channel!r}")
        self.link.write(f"INPUT {channel}:SENIX {senix}")
        time.sleep(CRYOCON_MIN_GAP_S)
        return self.link.query(f"INPUT {channel}:SENIX?")

    def read_channel_temperature(self, channel):
        """One temperature reading, for the after-the-fact sanity check."""
        return self.link.query(f"INPUT? {channel}")


# ===============================================================================
# GUI
# ===============================================================================

class CurveLoaderGUI:
    """Convert a Lake Shore sensor file and install it as a Cryo-con user curve.

    The left panel is the job in order: pick a file, check what came out of
    it, save it, connect, send it, verify it. The right panel always shows
    what would be sent, so nothing goes to the instrument unseen.
    """

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Cryocon 34 Sensor Curve Loader"

    # Colour scheme, shared with the sibling Cryocon modules.
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

    LEFT_PANEL_WIDTH = 560

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x950")
        self.root.minsize(1200, 780)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = CurveLoaderBackend(log=self.log)
        self.logo_image = None
        self.is_connected = False
        self.busy = False

        # The loaded file, and the curve derived from it.
        self.source = None            # whatever load_sensor_file returned
        self.source_path = ""
        self.curve_points = []        # (temperature, reading) in target units
        self.curve_lines = []         # the CALCUR block body
        self.curve_errors = []
        self.curve_warnings = []
        self.curve_stats = {}
        self.dropped_points = 0
        self.last_saved_path = ""

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
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
        self._create_channel_panel(scroll_frame, 6)
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
        ttk.Label(frame,
                  text=("Cryocon Model 34 | 12 user curve slots, "
                        "200 points each"),
                  background=self.CLR_FRAME_BG).grid(
            row=2, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='w')

    def _create_file_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 1  ·  Choose the sensor file')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text=("Pick the Lake Shore file for the sensor, for example\n"
                  "X17680.340 from the calibration CD.\n\n"
                  "The .340 is the one to prefer: it is Lake Shore's own\n"
                  "129-breakpoint table, laid out for an instrument that\n"
                  "interpolates between breakpoints, which is what this one\n"
                  "does. Use the .dat instead if you need below 4 K or\n"
                  "above 325 K, which the .340 does not reach."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(5, 5))

        ttk.Button(frame, text="Browse for a sensor file…",
                   command=self._choose_file).grid(
            row=1, column=0, sticky='ew', padx=10, pady=5)

        self.file_label = ttk.Label(
            frame, text="No file chosen yet.", background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9, 'italic'), wraplength=480, justify='left')
        self.file_label.grid(row=2, column=0, sticky='w', padx=10, pady=(0, 8))

    def _create_header_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 2  ·  How to store it')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Button(
            frame, text="Use the settings the manual gives for a Cernox",
            command=self._apply_cernox_defaults).grid(
            row=0, column=0, columnspan=2, sticky='ew', padx=10, pady=(8, 6))

        ttk.Label(frame, text="Curve name:").grid(
            row=1, column=0, sticky='w', padx=10, pady=4)
        self.name_var = tk.StringVar(value="")
        name_entry = ttk.Entry(frame, textvariable=self.name_var)
        name_entry.grid(row=1, column=1, sticky='ew', padx=10, pady=4)
        self.name_var.trace_add('write', lambda *_: self._rebuild_curve())

        ttk.Label(frame, text="Sensor type:").grid(
            row=2, column=0, sticky='w', padx=10, pady=4)
        self.type_var = tk.StringVar(value=CERNOX_DEFAULTS['sensor_type'])
        type_combo = ttk.Combobox(frame, textvariable=self.type_var,
                                  values=list(SENSOR_TYPES), state='normal')
        type_combo.grid(row=2, column=1, sticky='ew', padx=10, pady=4)
        type_combo.bind('<<ComboboxSelected>>',
                        lambda *_: self._rebuild_curve())
        self.type_var.trace_add('write', lambda *_: self._rebuild_curve())

        self.type_hint = ttk.Label(
            frame, text="", background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9, 'italic'), wraplength=480, justify='left')
        self.type_hint.grid(row=3, column=0, columnspan=2, sticky='w',
                            padx=10, pady=(0, 4))

        ttk.Label(frame, text="Multiplier:").grid(
            row=4, column=0, sticky='w', padx=10, pady=4)
        self.multiplier_var = tk.StringVar(
            value=CERNOX_DEFAULTS['multiplier'])
        ttk.Entry(frame, textvariable=self.multiplier_var, width=12).grid(
            row=4, column=1, sticky='w', padx=10, pady=4)
        self.multiplier_var.trace_add('write', lambda *_: self._rebuild_curve())

        ttk.Label(frame, text="Curve units:").grid(
            row=5, column=0, sticky='w', padx=10, pady=4)
        self.units_var = tk.StringVar(value=CERNOX_DEFAULTS['units'])
        units_combo = ttk.Combobox(frame, textvariable=self.units_var,
                                   values=list(CURVE_UNITS), state='readonly',
                                   width=12)
        units_combo.grid(row=5, column=1, sticky='w', padx=10, pady=4)
        units_combo.bind('<<ComboboxSelected>>',
                         lambda *_: self._rebuild_curve())

        ttk.Label(frame, text="User curve slot:").grid(
            row=6, column=0, sticky='w', padx=10, pady=4)
        self.slot_var = tk.StringVar(value="1")
        slot_combo = ttk.Combobox(
            frame, textvariable=self.slot_var,
            values=[str(n) for n in
                    range(MIN_USER_CURVE, MAX_USER_CURVE + 1)],
            state='readonly', width=6)
        slot_combo.grid(row=6, column=1, sticky='w', padx=10, pady=4)
        slot_combo.bind('<<ComboboxSelected>>', lambda *_: self._refresh_slot())

        self.slot_hint = ttk.Label(
            frame,
            text=("Slot 1 is Master Sensor Table index 10. Sending overwrites "
                  "whatever is in that slot."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9, 'italic'),
            wraplength=480, justify='left')
        self.slot_hint.grid(row=7, column=0, columnspan=2, sticky='w',
                            padx=10, pady=(0, 8))

    def _create_save_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 3  ·  Save the .crv file')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)
        ttk.Label(
            frame,
            text=("Optional, but worth doing: the .crv is exactly what will "
                  "be\nsent, and the Cryo-con utility software can load the "
                  "same file."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(5, 5))
        ttk.Button(frame, text="Save the .crv file…",
                   command=self._save_crv).grid(
            row=1, column=0, sticky='ew', padx=10, pady=(0, 8))

    def _create_connection_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 4  ·  Connect')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="VISA address:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.visa_cb = ttk.Combobox(frame, font=self.FONT_BASE,
                                    state='readonly')
        self.visa_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=1, column=0, columnspan=2, sticky='ew', pady=5)
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
        self.status_label.grid(row=2, column=0, columnspan=2, sticky='w',
                               padx=10, pady=(0, 5))

        ttk.Button(frame, text="Show what is in the chosen slot now",
                   command=self._inspect_slot).grid(
            row=3, column=0, columnspan=2, sticky='ew', padx=10, pady=(0, 8))

    def _create_send_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 5  ·  Send and check')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        self.verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Read the curve back afterwards and compare every point",
            variable=self.verify_var).grid(
            row=0, column=0, sticky='w', padx=10, pady=(8, 2))

        self.send_btn = ttk.Button(
            frame, text="Send the curve to the instrument",
            style='Send.TButton', command=self._send_curve)
        self.send_btn.grid(row=1, column=0, sticky='ew', padx=10, pady=6)

        ttk.Button(frame, text="Only read the slot back and compare",
                   command=self._verify_only).grid(
            row=2, column=0, sticky='ew', padx=10, pady=(0, 6))

        self.progress = ttk.Progressbar(frame, mode='determinate')
        self.progress.grid(row=3, column=0, sticky='ew', padx=10, pady=(0, 8))

    def _create_channel_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Step 6  ·  Put the curve on a channel (optional)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("This changes which sensor an input channel is measuring.\n"
                  "It can be done from the front panel instead. Nothing here\n"
                  "touches a control loop, a setpoint or a heater."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=0, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(6, 4))

        ttk.Label(frame, text="Input channel:").grid(
            row=1, column=0, sticky='w', padx=10, pady=4)
        self.channel_var = tk.StringVar(value='A')
        ttk.Combobox(frame, textvariable=self.channel_var,
                     values=CurveLoaderBackend.INPUT_CHANNELS,
                     state='readonly', width=6).grid(
            row=1, column=1, sticky='w', padx=10, pady=4)

        ttk.Button(frame, text="Show what each channel is using now",
                   command=self._show_channel_sensors).grid(
            row=2, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        ttk.Button(frame, text="Assign this curve to that channel",
                   command=self._assign_channel).grid(
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

        ttk.Label(self.advanced_frame,
                  text="Line ending:", background=self.CLR_FRAME_BG).grid(
            row=0, column=0, sticky='w', padx=10, pady=4)
        self.ending_var = tk.StringVar(value=list(LINE_ENDINGS)[0])
        ttk.Combobox(self.advanced_frame, textvariable=self.ending_var,
                     values=list(LINE_ENDINGS), state='readonly').grid(
            row=0, column=1, sticky='ew', padx=10, pady=4)
        ttk.Label(
            self.advanced_frame,
            text=("The manual says GPIB and USB need no terminator because "
                  "the interface\nsignals the end of a line itself, and "
                  "RS-232 needs one. Auto follows that.\nChange it only if a "
                  "readback comes back wrong."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=1, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 6))

        ttk.Label(self.advanced_frame, text="Name the sensor as well:",
                  background=self.CLR_FRAME_BG).grid(
            row=2, column=0, sticky='w', padx=10, pady=4)
        self.set_name_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.advanced_frame,
            text="also send SENTYPE <ix>:NAME after the curve",
            variable=self.set_name_var).grid(
            row=2, column=1, sticky='w', padx=10, pady=4)
        ttk.Label(
            self.advanced_frame,
            text=("The CALCUR header already carries the name. This sends it "
                  "again through\nthe sensor table, which some firmware "
                  "revisions want. Off by default."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=3, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 8))

    def _toggle_advanced(self):
        if self.advanced_visible:
            self.advanced_frame.grid_forget()
            self.advanced_btn.config(text="Show advanced settings")
        else:
            self.advanced_frame.grid(row=1, column=0, sticky='ew')
            self.advanced_btn.config(text="Hide advanced settings")
        self.advanced_visible = not self.advanced_visible

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

        table_frame = ttk.LabelFrame(
            panel, text='Every point, in the order the instrument stores them')
        table_frame.grid(row=2, column=0, sticky='nsew', padx=5, pady=5)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        columns = ('n', 'reading', 'temperature', 'ohms')
        self.table = ttk.Treeview(table_frame, columns=columns,
                                  show='headings', height=8)
        for column, heading, width in (
                ('n', '#', 60),
                ('reading', 'Sensor reading (sent first)', 220),
                ('temperature', 'Temperature / K (sent second)', 220),
                ('ohms', 'Resistance / ohm', 180)):
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, anchor='center')
        self.table.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        table_scroll = ttk.Scrollbar(table_frame, orient='vertical',
                                     command=self.table.yview)
        self.table.configure(yscrollcommand=table_scroll.set)
        table_scroll.grid(row=0, column=1, sticky='ns')

    # -----------------------------------------------------------------------
    # LOGGING AND STATE
    # -----------------------------------------------------------------------

    def log(self, message):
        """Append a timestamped message to the console.

        Safe to call from the worker thread: Tk is only touched on the main
        thread, via after().
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = f"[{timestamp}] {message}\n"

        def append():
            self.console.config(state='normal')
            self.console.insert('end', text)
            self.console.see('end')
            self.console.config(state='disabled')

        try:
            if threading.current_thread() is threading.main_thread():
                append()
            else:
                self.root.after(0, append)
        except Exception:
            print(text, end='')

    def _describe_starting_point(self):
        self.log("Cryocon 34 sensor curve loader ready.")
        self.log("The Model 34 has no Cernox curve of its own, so a Cernox "
                 "must be installed as one of its twelve user curves.")
        if not PYVISA_AVAILABLE:
            self.log("PyVISA is not installed, so nothing can be sent. The "
                     "converter and the .crv writer still work.")
        if not MATPLOTLIB_AVAILABLE:
            self.log("Matplotlib is not installed, so the curve is shown as "
                     "a table only.")
        self._update_type_hint()

    def _require_connection(self):
        if not self.is_connected or not self.backend.is_connected:
            self.log("Not connected to the instrument.")
            messagebox.showerror("Not Connected",
                                 "Connect to the Cryocon first (step 4).")
            return False
        return True

    def _set_busy(self, busy):
        self.busy = busy
        state = 'disabled' if busy else 'normal'
        try:
            self.send_btn.config(state=state)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # STEP 1 AND 2: LOAD AND BUILD
    # -----------------------------------------------------------------------

    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="Choose a Lake Shore sensor file",
            filetypes=[
                ("Lake Shore 340 breakpoint curve (preferred)", "*.340"),
                ("Lake Shore raw calibration", "*.dat"),
                ("Lake Shore table", "*.tbl"),
                ("Cryo-con curve", "*.crv"),
                ("All files", "*.*"),
            ])
        if not path:
            return
        self._load_file(path)

    def _load_file(self, path):
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
            'lakeshore-340': "Lake Shore Model 340 breakpoint curve",
            'crv': "Cryo-con curve (.crv)",
        }
        self.file_label.config(
            text=(f"{name}\n{kinds.get(source['kind'], source['kind'])}, "
                  f"{len(points)} points, units {source['units']}\n"
                  f"{source['meta'].get('columns', '')}"))
        self.log(f"  {kinds.get(source['kind'], source['kind'])}, "
                 f"{len(points)} points.")
        self.log(f"  {source['meta'].get('columns', '')}")
        self.log(f"  Temperature {min(temperatures):.4g} K to "
                 f"{max(temperatures):.4g} K; reading {min(readings):.6g} to "
                 f"{max(readings):.6g} {source['units']}.")
        for key in ('model', 'serial', 'lakeshore_format'):
            if key in source['meta']:
                self.log(f"  {key}: {source['meta'][key]}")

        # A .crv already carries its own header, so it is adopted rather than
        # overwritten with defaults the file did not ask for.
        if source['header']:
            header = source['header']
            self.name_var.set(header['name'])
            self.type_var.set(header['sensor_type'])
            self.multiplier_var.set(fmt6(header['multiplier']))
            self.units_var.set(header['units'])
            self.log("  Header taken from the file itself.")
        else:
            self.units_var.set(
                'LOGOHM' if source['units'] in ('OHMS', 'LOGOHM')
                else source['units'])
            if not self.name_var.get().strip():
                self.name_var.set(self._suggest_name(name, source['meta']))
            if 'stated_multiplier' in source['meta']:
                self.multiplier_var.set(
                    fmt6(source['meta']['stated_multiplier']))

        self._rebuild_curve()

    @staticmethod
    def _suggest_name(file_name, meta):
        """A curve name of 4 to 15 printable ASCII characters.

        The sensor model and serial are used where the file gave them, since
        that is what identifies the physical sensor on the bench; otherwise
        the file's own stem is used.
        """
        model = re.sub(r'[^A-Za-z0-9]', '', meta.get('model', ''))[:6]
        serial = re.sub(r'[^A-Za-z0-9]', '', meta.get('serial', ''))
        if model and serial:
            candidate = f"{model} {serial}"
        elif serial:
            candidate = serial
        else:
            candidate = os.path.splitext(file_name)[0]
        candidate = ''.join(c for c in candidate if 32 <= ord(c) < 127)
        candidate = candidate.strip()[:MAX_NAME_CHARS]
        while len(candidate) < MIN_NAME_CHARS:
            candidate += "_"
        return candidate

    def _apply_cernox_defaults(self):
        """The Cernox row of Table 4 in the manual, in one press."""
        self.type_var.set(CERNOX_DEFAULTS['sensor_type'])
        self.multiplier_var.set(CERNOX_DEFAULTS['multiplier'])
        self.units_var.set(CERNOX_DEFAULTS['units'])
        self.log("Applied the manual's Cernox settings: "
                 f"{CERNOX_DEFAULTS['sensor_type']}, multiplier "
                 f"{CERNOX_DEFAULTS['multiplier']}, "
                 f"{CERNOX_DEFAULTS['units']} "
                 "(Model 34 manual Table 4, 10 uA excitation).")
        self._rebuild_curve()

    def _update_type_hint(self):
        chosen = self.type_var.get().strip()
        if chosen in SENSOR_TYPES:
            self.type_hint.config(text=SENSOR_TYPES[chosen][2])
        else:
            self.type_hint.config(
                text=("Not one of the Model 34's listed types. If the "
                      "instrument does not recognise it, the manual says it "
                      "silently uses 'Diode' instead."))

    def _refresh_slot(self):
        try:
            slot = int(self.slot_var.get())
        except ValueError:
            return
        senix = CurveLoaderBackend.senix_for_user_curve(slot)
        self.slot_hint.config(
            text=(f"User curve {slot} is Master Sensor Table index {senix}. "
                  "Sending overwrites whatever is in that slot."))

    def _rebuild_curve(self):
        """Recompute the curve from the file and the header fields.

        Called on every change, so what the right panel shows is always what
        the send button would transmit.
        """
        self._update_type_hint()
        self._refresh_slot()
        self.curve_lines = []
        self.curve_points = []
        self.curve_errors = []
        self.curve_warnings = []
        self.curve_stats = {}
        self.dropped_points = 0

        if not self.source:
            self._render_summary()
            return

        target_units = self.units_var.get()
        try:
            points = convert_units(self.source['points'],
                                   self.source['units'], target_units)
        except CurveFileError as exc:
            self.curve_errors = [str(exc)]
            self._render_summary()
            return

        points, dropped = thin_points(points, MAX_CURVE_POINTS)
        self.dropped_points = dropped

        try:
            multiplier = float(self.multiplier_var.get())
        except ValueError:
            self.curve_errors = [
                f"The multiplier '{self.multiplier_var.get()}' is not a "
                "number. It is +1.0 for a sensor whose reading rises with "
                "temperature and -1.0 for one whose reading falls."]
            self.curve_points = points
            self._render_summary()
            return

        name = self.name_var.get().strip()
        sensor_type = self.type_var.get().strip()
        errors, warnings, stats = analyse_curve(
            points, target_units, sensor_type, multiplier, name)
        if dropped:
            warnings.append(
                f"{dropped} point(s) were dropped to fit the instrument's "
                f"{MAX_CURVE_POINTS}-point limit. The points kept are a "
                "plain subset of the calibration, evenly spaced through it; "
                "nothing was interpolated.")

        self.curve_points = points
        self.curve_errors = errors
        self.curve_warnings = warnings
        self.curve_stats = stats
        if not errors:
            try:
                self.curve_lines = build_crv_lines(
                    name, sensor_type, multiplier, target_units, points)
            except Exception as exc:
                self.curve_errors = [f"The curve could not be written: {exc}"]
                self.curve_lines = []
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
        units = self.units_var.get()
        slot = self.slot_var.get()

        if self.curve_errors:
            self.headline_label.config(
                text="This curve will not be sent.",
                foreground=self.CLR_STATUS_BAD)
        else:
            self.headline_label.config(
                text=(f"{stats.get('count', 0)} points ready for user curve "
                      f"slot {slot}."),
                foreground=self.CLR_STATUS_OK)

        detail = []
        if stats:
            detail.append(
                f"{stats['t_min']:.4g} K to {stats['t_max']:.4g} K, "
                f"{stats['coefficient']} temperature coefficient.")
            if units == 'LOGOHM':
                detail.append(
                    f"Stored as log10(ohm) "
                    f"{stats['r_min']:.6g} to {stats['r_max']:.6g}, which is "
                    f"{10 ** stats['r_min']:,.4g} to "
                    f"{10 ** stats['r_max']:,.4g} ohm.")
            elif units == 'OHMS':
                detail.append(f"Stored in ohms, {stats['r_min']:,.6g} to "
                              f"{stats['r_max']:,.6g}.")
            else:
                detail.append(f"Stored in volts, {stats['r_min']:.6g} to "
                              f"{stats['r_max']:.6g}.")
            if 'full_scale' in stats:
                detail.append(
                    f"Sensor type {self.type_var.get()} has a full scale of "
                    f"{stats['full_scale']:,.0f} ohm; the curve peaks at "
                    f"{stats['peak_ohms']:,.1f} ohm "
                    f"({100 * stats['peak_ohms'] / stats['full_scale']:.0f}%"
                    ").")
        detail.append(
            "Sent as sensor reading first, temperature in Kelvin second — "
            "the opposite order to the Lake Shore file.")
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
        units = self.units_var.get()
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
            units = self.units_var.get()
            axes.set_ylabel({'LOGOHM': "log10(R / ohm)",
                             'OHMS': "R / ohm",
                             'VOLTS': "V"}.get(units, units))
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

    def _save_crv(self):
        if not self.curve_lines:
            messagebox.showerror(
                "Nothing to Save",
                "There is no valid curve yet. Load a file and clear any "
                "problems listed on the right first.")
            return
        default = "curve.crv"
        if self.source_path:
            default = os.path.splitext(
                os.path.basename(self.source_path))[0] + ".crv"
        path = filedialog.asksaveasfilename(
            title="Save the Cryo-con curve",
            defaultextension=".crv", initialfile=default,
            initialdir=(os.path.dirname(self.source_path)
                        if self.source_path else None),
            filetypes=[("Cryo-con curve", "*.crv"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = crv_file_text(self.curve_lines)
            with open(path, 'w', encoding='ascii', newline='\n') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception as exc:
            self.log(f"Could not save: {exc}")
            messagebox.showerror("Save Failed", f"{path}\n\n{exc}")
            return
        self.last_saved_path = path
        self.log(f"Saved {len(self.curve_lines)} lines to {path}")
        self.log("  That file is byte for byte what the send button "
                 "transmits, and the Cryo-con utility software reads the "
                 "same format.")

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
        for resource in resources:
            self.log(f"  {resource}  ->  "
                     f"{identities.get(resource, 'no reply')}")
        cryocon = next((r for r in resources
                        if is_cryocon_idn(identities.get(r, ''))), None)
        if cryocon:
            self.visa_cb.set(cryocon)
            self.log(f"Cryocon identified at {cryocon} and selected.")
            return
        hint = next((r for r in resources if CRYOCON_ADDRESS_HINT in r), None)
        if hint:
            self.visa_cb.set(hint)
            self.log(f"WARNING: no Cryo-con answered *IDN?. Selected {hint} "
                     "on the factory address alone.")
        else:
            self.log("WARNING: no Cryo-con found on the bus.")

    def _do_connect(self):
        address = self.visa_cb.get()
        if not address:
            messagebox.showerror("No Address",
                                 "Scan and select a VISA address first.")
            return
        try:
            self.log(f"Connecting to {address}...")
            idn = self.backend.connect(address)
            self.is_connected = True
            self.log(f"Connected: {idn}")
            ending = self.backend.resolve_line_ending(
                LINE_ENDINGS[self.ending_var.get()])
            self.log(f"  Curve lines will be terminated with "
                     f"{ending!r} on this interface.")
            self.status_label.config(text="● Connected",
                                     foreground=self.CLR_STATUS_OK)
            self.connect_btn.config(state='disabled')
            self.disconnect_btn.config(state='normal')
            self.visa_cb.config(state='disabled')
        except Exception as exc:
            self.log(f"CONNECT ERROR: {traceback.format_exc()}")
            messagebox.showerror("Connection Failed",
                                 f"Could not connect to {address}:\n{exc}")

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

    # -----------------------------------------------------------------------
    # WORKER PLUMBING
    # -----------------------------------------------------------------------

    def _run_in_worker(self, description, function):
        """Run one instrument job off the Tk thread.

        The transfer is up to 205 paced writes plus a readback, which is well
        over ten seconds; done on the main thread the window would appear to
        hang in the middle of writing a calibration curve, which is the worst
        possible moment to look frozen.
        """
        if self.busy:
            self.log("Another instrument operation is still running.")
            return
        self._set_busy(True)

        def target():
            try:
                function()
            except Exception as exc:
                self.log(f"{description} FAILED: {type(exc).__name__}: {exc}")
                self.log(traceback.format_exc())
                self.root.after(
                    0, lambda: messagebox.showerror(
                        f"{description} Failed", f"{exc}"))
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=target, daemon=True).start()

    def _set_progress(self, done, total):
        def apply():
            self.progress['maximum'] = total
            self.progress['value'] = done
        self.root.after(0, apply)

    # -----------------------------------------------------------------------
    # STEP 5: SEND AND VERIFY
    # -----------------------------------------------------------------------

    def _inspect_slot(self):
        if not self._require_connection():
            return
        slot = int(self.slot_var.get())

        def job():
            senix = CurveLoaderBackend.senix_for_user_curve(slot)
            self.log(f"Reading user curve slot {slot} "
                     f"(sensor table index {senix}) ...")
            entry = self.backend.get_sensor_table_entry(senix)
            for key in ('name', 'type', 'multiplier'):
                self.log(f"  SENTYPE {key}: {entry.get(key)}")
                if entry.get(f"{key}_error"):
                    self.log(f"    ({entry[f'{key}_error']})")
            text = self.backend.read_curve(slot)
            if not text.strip():
                self.log(f"  Slot {slot} returned nothing. It is probably "
                         "empty, which is fine if you are about to fill it.")
                return
            try:
                header, points = parse_crv_text(text, f"slot {slot}")
            except CurveFileError as exc:
                self.log(f"  The reply could not be read as a curve: {exc}")
                self.log(f"  Raw reply, first 400 characters:\n{text[:400]}")
                return
            self.log(f"  Name '{header['name']}', type "
                     f"'{header['sensor_type']}', multiplier "
                     f"{header['multiplier']:+g}, units {header['units']}, "
                     f"{len(points)} points.")
            if points:
                temperatures = [t for t, _ in points]
                self.log(f"  Covers {min(temperatures):.4g} K to "
                         f"{max(temperatures):.4g} K.")

        self._run_in_worker("Reading the slot", job)

    def _send_curve(self):
        if not self.curve_lines:
            messagebox.showerror(
                "Nothing to Send",
                "There is no valid curve yet. Load a file and clear the "
                "problems listed on the right first.")
            return
        if not self._require_connection():
            return

        slot = int(self.slot_var.get())
        senix = CurveLoaderBackend.senix_for_user_curve(slot)
        warning_text = ""
        if self.curve_warnings:
            warning_text = ("\n\nThere are warnings on this curve:\n  - " +
                            "\n  - ".join(self.curve_warnings))
        if not messagebox.askyesno(
                "Send the curve?",
                f"This overwrites user curve {slot} (sensor table index "
                f"{senix}) on the Cryocon.\n\n"
                f"Name:       {self.name_var.get().strip()}\n"
                f"Type:       {self.type_var.get().strip()}\n"
                f"Multiplier: {self.multiplier_var.get()}\n"
                f"Units:      {self.units_var.get()}\n"
                f"Points:     {len(self.curve_points)}\n\n"
                "Nothing else on the instrument is touched: no loop, "
                "setpoint, heater or reset."
                f"{warning_text}\n\nSend it?"):
            self.log("Send cancelled.")
            return

        lines = list(self.curve_lines)
        points = list(self.curve_points)
        ending = LINE_ENDINGS[self.ending_var.get()]
        verify = self.verify_var.get()
        also_name = self.set_name_var.get()
        name = self.name_var.get().strip()

        def job():
            self.log(f"Sending {len(lines) + 1} lines to user curve {slot} "
                     f"(CALCUR {slot}) ...")
            self.backend.send_curve(
                slot, lines, ending,
                progress=lambda done, total, line: self._set_progress(
                    done, total))
            self.log(f"  All lines sent. Waited {CURVE_SETTLE_S:.1f} s for "
                     "the instrument to sort the curve and write it to "
                     "flash.")
            if also_name:
                try:
                    reported = self.backend.set_sensor_name(senix, name)
                    self.log(f"  SENTYPE {senix}:NAME set; the instrument "
                             f"now reports '{reported}'.")
                except Exception as exc:
                    self.log(f"  SENTYPE {senix}:NAME was refused: {exc}. "
                             "The name in the CALCUR header still stands.")
            if verify:
                self._verify_against(points, slot)
            else:
                self.log("Verification was switched off. Nothing has "
                         "confirmed what the instrument actually stored.")

        self._run_in_worker("Sending the curve", job)

    def _verify_only(self):
        if not self.curve_points:
            messagebox.showerror(
                "Nothing to Compare",
                "Load the file whose curve you want to compare against "
                "first.")
            return
        if not self._require_connection():
            return
        slot = int(self.slot_var.get())
        points = list(self.curve_points)
        self._run_in_worker("Reading the curve back",
                            lambda: self._verify_against(points, slot))

    def _verify_against(self, sent_points, slot):
        """Read the slot back and compare it, point by point, with what was
        sent. Runs on the worker thread."""
        senix = CurveLoaderBackend.senix_for_user_curve(slot)
        self.log(f"Reading user curve {slot} back with CALCUR? {slot} ...")
        text = self.backend.read_curve(slot)
        if not text.strip():
            self.log("VERIFY FAILED: the instrument sent nothing back. The "
                     "curve may not have been stored. Check the front panel "
                     "(Sensors key) before using this sensor.")
            return
        try:
            header, read_points = parse_crv_text(text, f"slot {slot}")
        except CurveFileError as exc:
            self.log(f"VERIFY FAILED: the reply could not be read as a "
                     f"curve: {exc}")
            self.log(f"  Raw reply, first 400 characters:\n{text[:400]}")
            return

        self.log(f"  The instrument reports: name '{header['name']}', type "
                 f"'{header['sensor_type']}', multiplier "
                 f"{header['multiplier']:+g}, units {header['units']}.")

        problems = []
        wanted_type = self.type_var.get().strip()
        if header['sensor_type'].strip().upper() != wanted_type.upper():
            problems.append(
                f"the sensor type came back as '{header['sensor_type']}' "
                f"where '{wanted_type}' was sent")
            if header['sensor_type'].strip().lower() == 'diode':
                problems.append(
                    "and 'Diode' is what the manual says the instrument "
                    "substitutes when it does not recognise a type, so this "
                    "sensor type is not supported by this firmware")
        if header['units'].strip().upper() != self.units_var.get().upper():
            problems.append(
                f"the units came back as '{header['units']}' where "
                f"'{self.units_var.get()}' was sent")
        try:
            wanted_multiplier = float(self.multiplier_var.get())
            if abs(header['multiplier'] - wanted_multiplier) > 1e-4:
                problems.append(
                    f"the multiplier came back as {header['multiplier']:+g} "
                    f"where {wanted_multiplier:+g} was sent")
        except ValueError:
            pass
        if header['name'].strip() != self.name_var.get().strip():
            problems.append(
                f"the name came back as '{header['name']}' where "
                f"'{self.name_var.get().strip()}' was sent")

        comparison = compare_curves(sent_points, read_points)
        self.log(f"  {comparison['sent_count']} points sent, "
                 f"{comparison['read_count']} read back.")
        if comparison['read_count'] == comparison['sent_count']:
            self.log("  Largest difference in any sensor reading: "
                     f"{comparison['worst_reading_error']:.2e} relative; "
                     "in any temperature: "
                     f"{comparison['worst_temperature_error']:.2e}.")
        for message in comparison['problems']:
            self.log(f"  {message}")

        if comparison['matched'] and not problems:
            self.log(f"VERIFIED. User curve {slot} on the instrument matches "
                     "what was sent: every point, the name, the sensor type, "
                     "the multiplier and the units.")
            self.log(f"  To use it, set an input channel to sensor index "
                     f"{senix} (step 6, or the Sensors key on the front "
                     "panel).")
            self.root.after(0, lambda: messagebox.showinfo(
                "Curve Verified",
                f"User curve {slot} matches what was sent, point for "
                f"point.\n\nSet an input channel to sensor index {senix} to "
                "use it."))
            return

        summary = []
        if problems:
            summary.append("Header differences: " + "; ".join(problems) + ".")
        if not comparison['matched']:
            summary.append(
                f"{len(comparison['problems'])} point difference(s) "
                "reported above.")
        self.log("VERIFY FAILED. " + " ".join(summary))
        self.log("  Do not use this sensor until this is resolved. If the "
                 "point count is short, try a different line ending under "
                 "Advanced and send again. If only the sensor type differs, "
                 "the firmware does not accept that name; try another from "
                 "the list.")
        self.root.after(0, lambda: messagebox.showerror(
            "Curve Not Verified",
            f"User curve {slot} does not match what was sent.\n\n" +
            "\n".join(summary) +
            "\n\nThe console lists the differences. Do not use this sensor "
            "until this is resolved."))

    # -----------------------------------------------------------------------
    # STEP 6: CHANNEL ASSIGNMENT
    # -----------------------------------------------------------------------

    def _show_channel_sensors(self):
        if not self._require_connection():
            return

        def job():
            self.log("Asking each input channel which sensor it is using ...")
            self.log("  Three spellings are asked because this firmware "
                     "revision may answer any of them, and they number "
                     "differently.")
            for channel in CurveLoaderBackend.INPUT_CHANNELS:
                answers = self.backend.get_channel_sensor_index(channel)
                rendered = ", ".join(f"{key}={value}"
                                     for key, value in answers.items())
                self.log(f"  Channel {channel}: {rendered}")

        self._run_in_worker("Reading the channels", job)

    def _assign_channel(self):
        if not self._require_connection():
            return
        channel = self.channel_var.get()
        slot = int(self.slot_var.get())
        senix = CurveLoaderBackend.senix_for_user_curve(slot)
        if not messagebox.askyesno(
                "Change the channel's sensor?",
                f"This sets input channel {channel} to sensor index {senix}, "
                f"which the manual gives as user curve {slot}.\n\n"
                "It changes what that channel measures. If a control loop is "
                "reading that channel, its temperature reading will change "
                "the moment this is sent.\n\n"
                "Nothing else is touched: no loop, setpoint or heater "
                "command is sent.\n\nGo ahead?"):
            self.log("Channel assignment cancelled.")
            return

        def job():
            self.log(f"Setting input {channel} to sensor index {senix} "
                     f"(user curve {slot}) ...")
            reported = self.backend.assign_curve_to_channel(channel, senix)
            self.log(f"  The instrument now reports INPUT {channel}:SENIX? "
                     f"= {reported}")
            try:
                matched = int(float(reported)) == senix
            except (TypeError, ValueError):
                matched = False
            if not matched:
                self.log(
                    "ASSIGNMENT NOT CONFIRMED. The channel did not come back "
                    f"as {senix}. This firmware revision may index user "
                    "curves differently from the Edition 4 manual (it also "
                    "answers ISENIX and USENIX). Nothing further was sent: "
                    "set the sensor from the front panel with the Sensors "
                    "key, or press 'Show what each channel is using now' to "
                    "see how this unit numbers them.")
                self.root.after(0, lambda: messagebox.showwarning(
                    "Assignment Not Confirmed",
                    f"Input {channel} reads back as {reported}, not "
                    f"{senix}.\n\nNo further command was sent. Set the "
                    "sensor from the front panel instead."))
                return
            reading = self.backend.read_channel_temperature(channel)
            self.log(f"  Input {channel} now reads: {reading}")
            self.log("  A run of dashes means a sensor fault and a run of "
                     "dots means the reading is off the curve; either one "
                     "means check the wiring before trusting this.")

        self._run_in_worker("Assigning the channel", job)

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


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    root = tk.Tk()
    app = CurveLoaderGUI(root)
    if not PYVISA_AVAILABLE:
        # Not fatal: the converter, the checks and the .crv writer are the
        # bulk of this module and none of them need an instrument.
        messagebox.showwarning(
            "PyVISA Not Installed",
            "PyVISA is not installed, so no curve can be sent to an "
            "instrument.\n\nEverything else works: you can still read a Lake "
            "Shore file, check it and save the .crv, then load that file "
            "with the Cryo-con utility software.\n\n"
            "To send from here:\n  pip install pyvisa pyvisa-py")
    root.mainloop()
