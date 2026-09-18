"""
Module: Sensor_Curve_Viewer_CC34_GUI.py
Purpose: Look at the calibration curves that are already inside a Cryo-con
         Model 34 -- list what the Master Sensor Table holds, read the points
         of one slot, plot them, and write them out as a .crv file and a CSV.

         This is the read-only companion to Sensor_Curve_Loader_CC34_GUI.py.
         The loader writes curves; this one only ever asks what is there. Use
         this when the question is "what is actually in slot 15" or "which of
         these entries is the Cernox" and nothing needs to change.

===============================================================================
THIS MODULE IS PASSIVE. IT NEVER CHANGES THE INSTRUMENT.
===============================================================================

Every command it can send is a query. There is no CALCUR write, no SENTYPE
set, no INPUT :SENIX set, no loop, setpoint, PID, heater or control command,
no STOP, no *RST and no *CLS. On the Model 34 *RST is a fifteen-second
hardware reset, so a module that browses curves has no business being able to
send one, and this one cannot.

That is not left to good intentions. CryoconReadOnlyLink.ask() and
ask_block() are the only two methods in this file that talk to the bus, and
both refuse to transmit any command that does not contain a '?'. A '?' is
what makes a Cryo-con command a query; every setting command is the same
mnemonic without one -- CALCUR? reads a curve, CALCUR writes one. So the
guard admits queries rather than listing commands to forbid, which means a
command added to this module later cannot slip past a list it was never added
to. Self-test cases 1 and 2 are that rule.

Two things a query can still do are worth knowing about.

  - CALCUR? is slow. On the Rev 3.03A unit in this lab one takes about twelve
    seconds, because the instrument walks its flash. That is why the slot
    list below is built from SENTYPE?, which answers in well under a second,
    and the curve points are read one slot at a time on request. Reading all
    thirty-two slots' curves would be six minutes of bus traffic for no good
    reason.
  - Reading takes the bus. Nothing is written, so a running control loop is
    unaffected, but another program polling the same instrument will see its
    queries queue behind these.

===============================================================================
WHAT THE MANUAL SAYS
===============================================================================

Source: "Cryo-con Model 34 Cryogenic Temperature Controller, User's Guide,
Edition 4, August 2006", which in this repository is the file
Untracked_Stuff/"The User Interface - Cryogenic Control Systems, Inc..pdf".

CALCUR? <index>   (p.181-183)
  Returns the curve at that index as the same block the CALCUR command
  accepts: four header lines, the points, then a line holding a semicolon.

      <sensor name>        4 to 15 ASCII characters
      <sensor type>        Diode | ACR | 31kR | 3.1kR | 312R | 625R | TC80 |
                           TC40 | None  (this firmware also answers the
                           R-name family used by SENTYPE)
      <multiplier>         signed float; the sign is the temperature coeff.
      <curve units>        OHMS | VOLTS | LOGOHM
      <reading> <temp K>   2 to 200 of these
      ;

  THE SENSOR READING COMES FIRST AND THE TEMPERATURE SECOND. That is the
  opposite way round from a Lake Shore .dat file and the same way round as a
  Lake Shore .340, and it is the single easiest thing to get backwards. The
  table on screen labels both columns for that reason.

  On GPIB each line of the reply arrives as its own message, so lines are
  read until the closing semicolon. On an interface that packs the whole
  block into one message the first read returns everything and the loop ends
  immediately. Both are handled without knowing in advance which one this is.

SENTYPE? <index>, SENTYPE <index>:TYPE?, SENTYPE <index>:MULTIPLY?  (p.187)
  The name, sensor type and multiplier of one Master Sensor Table entry.
  Three fast queries. This is what the slot list is built from.

INPUT <ch>:SENIX?   (p.187)
  Which sensor index an input channel is using, as a Master Sensor Table
  index. The only indexing scheme this firmware has.

  This block used to claim the lab's Rev 3.03A unit "also answers ISENIX?
  and USENIX?". It does not. The diagnostics survey settled it on
  2026-09-17 (two runs, GPIB0::23): INPUT <ch>:ISENIX? and
  INPUT <ch>:USENIX? both time out, on every channel, exactly as
  INPUT <ch>:SENPR? does. Edition 4 does not list any of the three and
  this firmware does not have them.

  They are still ASKED, once, on the first channel, because a Model 32/32B
  or another firmware may have them and the operator should see which
  scheme is in play rather than have this module pick one and be quietly
  wrong. When both go unanswered on the first channel they are dropped for
  the rest of the sweep - a timeout costs the full CRYOCON_TIMEOUT_MS, and
  paying that eight more times for an answer already known is waste.

INPUT? <ch>
  One temperature reading, for context. A run of dashes means a sensor fault
  and a run of dots means the reading is off the end of the curve.

Where the user curves live is genuinely unsettled on this firmware: Appendix
A contradicts itself and the instrument disagrees with both halves of it. The
loader module carries the whole story. This module sidesteps it entirely by
naming slots by their Master Sensor Table index, which is what CALCUR? and
SENTYPE? both take, and never converting to a "user curve number".

===============================================================================
WHAT IT WRITES TO DISK
===============================================================================

Three files, all optional, all to a path chosen in a save dialog:

  .crv   The Cryo-con curve file: the same four header lines, points and
         semicolon that came off the instrument. Byte-for-byte loadable by
         the Cryo-con utility software or by the loader module in this suite,
         so a curve can be copied from one instrument to another.

  .csv   Reading, temperature, and the resistance in ohms where the units
         make that meaningful. A '#' preamble carries the header fields so
         the file is self-describing. For plotting, not for re-loading.

  .csv   The slot list: every Master Sensor Table entry the scan reached.

Author: Prathamesh Deshmukh
Version: 1.0  (1 Sep 2026)
"""

import os
import re
import sys
import math
import queue
import time
import threading
import traceback
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas

# --- Optional packages -----------------------------------------------------
# Each of these is a feature, not a requirement. The parsing and both file
# writers work with none of them installed.

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

MIN_TABLE_INDEX = 0
MAX_TABLE_INDEX = 31         # how far up the Master Sensor Table is walked
MAX_CURVE_POINTS = 200
INPUT_CHANNELS = ('A', 'B', 'C', 'D')

CURVE_UNITS = ('LOGOHM', 'OHMS', 'VOLTS')

# What a units string means when the reading is converted to ohms for the
# third table column. VOLTS has no resistance to report, so it gets None
# rather than a number that would look like one.
UNITS_TO_OHMS = {
    'LOGOHM': lambda value: 10.0 ** value,
    'OHMS': lambda value: value,
    'VOLTS': None,
}

# Which unit family a Cryo-con sensor type belongs to. The instrument holds
# two vocabularies for the same idea -- the manual's CALCUR list and the
# R-name family SENTYPE? reports -- so both are here, folded to lower case
# because firmware capitalises them inconsistently ('Diode' and 'SiDiode' are
# the same type).
#
# Only used to notice a type that disagrees with the curve's own units. A
# diode curve stored against a resistance input does not fail: the channel
# passes a resistance-measuring current through the diode and reads a
# perfectly plausible wrong temperature, which is the whole reason this
# window bothers to compare them.
TYPE_UNIT_FAMILY = {
    'diode': 'V', 'sidiode': 'V', 'si diode': 'V', 'sidiod': 'V',
    'tc80': 'V', 'tc40': 'V',
    'acr': 'ohm', '31kr': 'ohm', '3.1kr': 'ohm', '625r': 'ohm',
    '312r': 'ohm',
    'r8k10ua': 'ohm', 'r16k10ua': 'ohm', 'r6k100ua': 'ohm',
    'r2k100ua': 'ohm', 'r625r1ma': 'ohm', 'r312r1ma': 'ohm',
}

UNITS_FAMILY = {'VOLTS': 'V', 'OHMS': 'ohm', 'LOGOHM': 'ohm'}


def type_unit_family(sensor_type):
    """'V', 'ohm', or None when the name is not one this module knows."""
    return TYPE_UNIT_FAMILY.get(str(sensor_type or '').strip().lower())


# ===============================================================================
# WHAT THIS FIRMWARE'S SENSOR-TYPE VOCABULARY ACTUALLY IS  (added 16 Sep 2026)
# ===============================================================================
#
# The Edition 4 manual prints TWO lists of sensor-type names, one per command,
# and this instrument uses neither of them as printed.
#
#   CALCUR header, printed p.173:
#       "Diode, ACR, 31kR, 3.1kR, 312R, 625R, TC80, TC40 and None."
#   SENTYPE <index>:TYPE, printed p.187:
#       "Diode ... R16K10UA, R8K10UA, R6K100UA, R2K100UA, R625R1MA and
#        R312R1MA ... Snone ... TC80 ... TC40."
#
# What the Rev 3.03A unit in this lab actually answers, over all 27 Master
# Sensor Table entries that exist (run of 15 Sep 2026):
#
#       SNONE  SIDIODE  R312R1MA  R2K100UA  R8K10UA  TC80
#
# 'ACR' appears nowhere. 'Diode' appears nowhere; the firmware spells it
# SIDIODE. The loader's type probe of the same afternoon settled the CALCUR
# question the same way: a header saying 'R8K10UA' was kept, headers saying
# 'ACR' and 'Diode' were DISCARDED outright, and a header saying 'SIDiode'
# was kept along with all 88 points of a DT-470 curve. So on this firmware
# there is one vocabulary, the R-name family, and both commands use it.
#
# That is worth checking rather than believing, because it is one instrument
# and one firmware revision. Hence the button: the type query below asks the
# instrument what names it uses, in its own spelling, and writes nothing.
# Every SENTYPE? reply is evidence; the manual is not.
#
# The two printed lists are kept here ONLY so the report can say which
# printed name was and was not seen. Nothing is ever sent from them.

MANUAL_CALCUR_TYPES = ('Diode', 'ACR', '31kR', '3.1kR', '312R', '625R',
                       'TC80', 'TC40', 'None')

MANUAL_SENTYPE_TYPES = ('Diode', 'R16K10UA', 'R8K10UA', 'R6K100UA',
                        'R2K100UA', 'R625R1MA', 'R312R1MA', 'Snone',
                        'TC80', 'TC40')

# Type names this lab has SEEN this firmware use, with the spelling it used
# and what the name means. Everything here was read off the instrument, not
# typed from the manual. A name absent from this is not thereby refused: it
# is unconfirmed, which is a different thing and is reported as such.
OBSERVED_FIRMWARE_TYPES = {
    'SNONE':    "no sensor; the entry is off",
    'SIDIODE':  "silicon or GaAlAs diode, 2.5 V full scale. THIS FIRMWARE'S "
                "SPELLING OF THE MANUAL'S 'Diode'",
    'R8K10UA':  "8 kohm full scale, 10 uA - Cernox, RuOx, Germanium, "
                "Carbon Glass, thermistors",
    'R2K100UA': "2 kohm full scale, 100 uA - Platinum 1000",
    'R312R1MA': "312 ohm full scale, 1 mA - Platinum 100",
    'TC80':     "80 mV full scale - thermocouple",
}

# Names from the manual's SENTYPE list that this lab has not yet seen on the
# instrument. They are plausible and are NOT refused anywhere; they are
# simply unconfirmed, and the report says so rather than implying otherwise.
PLAUSIBLE_UNSEEN_TYPES = ('R16K10UA', 'R6K100UA', 'R625R1MA', 'TC40')


def normalise_type(name):
    """A sensor-type name folded for comparison: upper case, no separators.

    'SiDiode', 'SIDIODE' and 'Si Diode' are one type printed three ways, and
    comparing them as typed is how a matching type gets reported as a
    mismatch.
    """
    return re.sub(r'[\s_-]+', '', str(name or '').strip().upper())


def summarise_observed_types(entries):
    """Every distinct sensor-type string in a table scan, and where it is.

    `entries` is what scan_sensor_table() returns. The key is the type string
    EXACTLY as the instrument printed it, because the spelling is the whole
    point: this is what settles whether a CALCUR header should say 'Diode' or
    'SIDiode'. Indices are in the order they were read.
    """
    seen = {}
    for entry in entries or ():
        raw = entry.get('type')
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        seen.setdefault(text, []).append(entry.get('index'))
    return seen


def type_vocabulary_report(entries):
    """Plain-language lines saying what type names this instrument uses.

    Nothing here is sent and nothing is decided. It is a reading of a scan
    that has already been taken, turned into the sentence an operator needs
    before they type a type into the loader: use this spelling, not that one.
    """
    lines = []
    seen = summarise_observed_types(entries)
    if not seen:
        return ["No sensor types were read. List the Master Sensor Table "
                "first, or connect and press this button again."]

    answered = sum(1 for e in entries or () if e.get('type') is not None)
    lines.append(f"{len(seen)} distinct sensor-type name(s) across "
                 f"{answered} answering table entries.")
    lines.append("")
    lines.append("WHAT THE INSTRUMENT CALLS THEM  (its own spelling)")
    for text in sorted(seen, key=lambda key: (-len(seen[key]), key)):
        indices = seen[text]
        where = ", ".join(str(i) for i in indices[:10])
        if len(indices) > 10:
            where += f", ... ({len(indices)} entries)"
        known = OBSERVED_FIRMWARE_TYPES.get(normalise_type(text), "")
        lines.append(f"  {text:<10s} at index {where}")
        if known:
            lines.append(f"             {known}")

    folded = {normalise_type(text) for text in seen}
    lines.append("")
    lines.append("THE MANUAL'S CALCUR LIST, CHECKED AGAINST THIS INSTRUMENT")
    for name in MANUAL_CALCUR_TYPES:
        mark = "SEEN    " if normalise_type(name) in folded else "not seen"
        lines.append(f"  {mark}  {name}")
    lines.append("")
    lines.append("THE MANUAL'S SENTYPE LIST, CHECKED THE SAME WAY")
    for name in MANUAL_SENTYPE_TYPES:
        mark = "SEEN    " if normalise_type(name) in folded else "not seen"
        lines.append(f"  {mark}  {name}")

    lines.append("")
    lines.append("WHAT THIS MEANS FOR THE LOADER")
    diode_spelling = [t for t in seen if normalise_type(t) == 'SIDIODE']
    plain_diode = [t for t in seen if normalise_type(t) == 'DIODE']
    if diode_spelling and not plain_diode:
        lines.append(
            f"  A silicon diode is called '{diode_spelling[0]}' here, not "
            "'Diode'. Edition 4 prints 'Diode' and this firmware does not "
            "use that word at all. A CALCUR header that says 'Diode' is "
            "discarded whole; one that says 'SIDiode' is kept.")
    if 'ACR' not in folded:
        lines.append(
            "  'ACR' does not appear anywhere on this instrument. The "
            "manual's p.173 CALCUR list is not this firmware's vocabulary. "
            "Use the R-name family for a resistance sensor: R8K10UA for a "
            "Cernox.")
    unseen = [name for name in PLAUSIBLE_UNSEEN_TYPES
              if normalise_type(name) not in folded]
    if unseen:
        lines.append(
            "  Not seen, and therefore UNCONFIRMED rather than refused: "
            + ", ".join(unseen) + ". No slot on this unit happens to use "
            "them, which is not evidence either way.")
    lines.append("")
    lines.append("This was read with SENTYPE? queries only. Nothing was "
                 "written and nothing was changed.")
    return lines


# ===============================================================================
# WHERE THE USER CURVES REALLY START  (added 16 Sep 2026)
# ===============================================================================
#
# Appendix A prints two tables that contradict each other, and the instrument
# contradicts both. The scan of 15 Sep settles it as arithmetic rather than as
# a guess, because the instrument names its own untouched slots:
#
#     index 19 answers 'User Sensor 5'   ->  19 - 5  = 14
#     index 26 answers 'User Sensor C'   ->  26 - 12 = 14
#
# 'User Sensor C' is user curve 12, since the slots count 1-9 and then A, B, C.
# So the user block is index 15 to 26, the factory block is index 0 to 14, and
# index 27 upwards does not answer at all. Appendix A's offset of 9 is simply
# wrong for this firmware, and a CALCUR sent to the manual's number lands in
# the factory block, where p.172 says it is discarded without a word.
#
# This is derived from the placeholder names every time rather than hard-coded,
# so a different unit reports its own answer instead of this one.
APPENDIX_A_OFFSET = 9        # what the manual claims; not trusted

# 'User Sensor 4', 'User Curve B' -- what an untouched slot answers with.
USER_SLOT_NAME_RE = re.compile(
    r'^\s*user\s*(?:sensor|curve)\s*([0-9A-Ca-c])\s*$', re.I)


def user_slot_number(name):
    """The user-curve number an untouched placeholder name carries, or None.

    The slots count 1-9 and then A, B, C for 10, 11 and 12, which is how the
    instrument prints them.
    """
    match = USER_SLOT_NAME_RE.match(str(name or ''))
    if not match:
        return None
    digit = match.group(1).upper()
    if digit.isdigit():
        number = int(digit)
        return number if 1 <= number <= 9 else None
    return {'A': 10, 'B': 11, 'C': 12}[digit]


def map_table_blocks(entries):
    """Work out, from the scan alone, which indices are user curve slots.

    Returns a dict with 'offset' (index = user curve number + offset),
    'agreement' (how many placeholders voted for it), 'disagreement',
    'user_first' / 'user_last', 'factory_last' and 'answered_last'. Every
    field is None when nothing could be derived, which is the honest answer
    for a scan with no untouched slot left in it.

    Nothing is assumed. If every user slot on an instrument has been filled
    there is no placeholder to count from, and this says so rather than
    falling back on Appendix A.
    """
    votes = {}
    answered = [e for e in entries or () if e.get('name') is not None]
    for entry in answered:
        number = user_slot_number(entry.get('name'))
        if number is None:
            continue
        try:
            index = int(entry.get('index'))
        except (TypeError, ValueError):
            continue
        votes.setdefault(index - number, []).append(index)

    result = {'offset': None, 'agreement': 0, 'disagreement': 0,
              'user_first': None, 'user_last': None, 'factory_last': None,
              'answered_last': None, 'votes': votes}
    if answered:
        try:
            result['answered_last'] = max(int(e['index']) for e in answered)
        except (TypeError, ValueError):
            pass
    if not votes:
        return result

    offset = max(votes, key=lambda key: len(votes[key]))
    result['offset'] = offset
    result['agreement'] = len(votes[offset])
    result['disagreement'] = sum(len(v) for k, v in votes.items()
                                 if k != offset)
    result['user_first'] = offset + 1
    result['user_last'] = offset + 12
    result['factory_last'] = offset
    return result


def table_geometry_report(entries):
    """Plain-language lines about where the user curves live on this unit.

    Returns (lines, blocks) so a caller can both print it and use it.
    """
    blocks = map_table_blocks(entries)
    lines = []
    if blocks['offset'] is None:
        lines.append(
            "No untouched 'User Sensor n' placeholder was found, so where "
            "the user block starts cannot be derived from this scan. That "
            "happens when every user slot has been filled. Nothing is "
            "assumed from the manual here.")
        return lines, blocks

    offset = blocks['offset']
    lines.append(f"User curve 1 is table index {blocks['user_first']}, and "
                 f"user curve 12 is index {blocks['user_last']}.")
    lines.append("  Master Sensor Table index = user curve number "
                 f"+ {offset}.")
    lines.append(f"  {blocks['agreement']} untouched placeholder slot(s) "
                 "agree on that.")
    if blocks['disagreement']:
        lines.append(f"  {blocks['disagreement']} placeholder(s) imply a "
                     "DIFFERENT offset. Something is inconsistent here; read "
                     "those slots before writing anything.")
    lines.append(f"  Index 0 to {blocks['factory_last']} are therefore the "
                 "factory block, which cannot be written.")
    if blocks['answered_last'] is not None:
        lines.append(f"  The highest index that answers at all is "
                     f"{blocks['answered_last']}.")
    if offset != APPENDIX_A_OFFSET:
        lines.append(
            f"  This does NOT match the manual. Appendix A gives an offset "
            f"of {APPENDIX_A_OFFSET}; this instrument uses {offset}. The "
            "instrument wins.")
    return lines, blocks


# Names that mean a slot is a factory entry rather than something an operator
# stored. Kept because the distinction matters when deciding what is safe to
# overwrite -- in the loader, not here -- and because it is useful to see.
#
# 16 Sep 2026: 'EXTERN' and 'CRYOCAL' were added after the scan showed
# 'TC K Extern', 'TC E Extern', 'TC T Extern' and 'Cryocal D3' sitting in the
# factory block and being labelled 'user' by this list, which flatly
# contradicted the index arithmetic above. Where the two disagree the index
# wins: these markers only decorate a row, and map_table_blocks() is what
# says which block an index is in.
FACTORY_NAME_MARKERS = ('LAKESHORE', 'LAKE SHORE', 'PLATINUM', 'PT-', 'PT1',
                        'RUOX', 'RO-', 'ROX', 'SI410', 'SI-410', 'SI 410',
                        'DIODE', 'TYPE ', 'THERMOCOUPLE', 'CRYOCON',
                        'CRYOCAL', 'EXTERN', 'FACTORY')

# Names that mean nothing is stored. A Cryo-con answers an unused slot with a
# blank, a dot, or the literal word NONE depending on firmware.
EMPTY_NAME_MARKERS = ('', '.', '-', 'NONE', 'SNONE', 'EMPTY', 'USER')


class CurveReadError(RuntimeError):
    """A curve could not be read back with certainty."""


class ReadOnlyViolation(RuntimeError):
    """Something tried to send a command that is not a query.

    Raised by the link before anything reaches the bus. If this is ever seen
    it is a bug in this module, not an instrument problem, and the operation
    that raised it sent nothing.
    """


def fmt6(value):
    """Six significant digits, in plain decimal.

    Six is what a Cryo-con keeps: the manual says curve values are stored as
    32-bit floats. Writing more would imply precision the instrument does not
    hold. Exponent notation is avoided because the firmware's number parser
    is not documented and a value written '1e-05' that is read back as '1' is
    wrong by five orders of magnitude without looking wrong. Every value
    keeps a decimal point for the same reason: the manual's own examples are
    written that way, and it warns that a header field it cannot identify is
    replaced with a default rather than reported.
    """
    if value is None or not math.isfinite(value):
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


def parse_calcur_block(text, source_name="the slot", allow_empty=False):
    """Read the reply to a 'CALCUR? n' query.

    Returns (header, points) where points is a list of (reading, temperature)
    in the order the instrument printed them, which is the order it stores
    them: ascending sensor reading.

    The numerals are kept exactly as they were printed, under
    header['point_texts']. How many digits the instrument prints is the limit
    on how precisely anything read here can be quoted, and that is not
    recoverable once the text has become a float.

    `allow_empty` accepts a header with no points after it. An untouched user
    slot answers CALCUR? with exactly that -- four header lines and the
    semicolon, e.g. 'User Sensor 4 / SiDiode / -1.000000 / Volts / ;' -- and
    it is a real answer meaning "there is no curve here". Refusing it made
    this window report a perfectly healthy empty slot as "no readable curve",
    which reads like a fault and is not one.
    """
    lines = [line.strip() for line in _clean_lines(text)]
    lines = [line for line in lines if line]

    # The reply may still carry the echoed command on some interfaces. It is
    # stripped BEFORE anything is counted: counting first made the echo
    # decide whether an empty slot parsed at all.
    if lines and re.match(r'^CALCUR\??\s', lines[0], re.I):
        lines = lines[1:]

    minimum = 5 if allow_empty else 6
    if len(lines) < minimum:
        raise CurveReadError(
            f"{source_name} answered {len(lines)} non-blank line(s). A curve "
            "is a header of four lines, "
            + ("" if allow_empty else "at least two points and ")
            + "a semicolon.")

    name = lines[0]
    sensor_type = lines[1]
    try:
        multiplier = float(lines[2])
    except ValueError:
        raise CurveReadError(
            f"{source_name}: the third header line should be the multiplier, "
            f"a signed number, but it reads '{lines[2]}'.")
    units = lines[3].upper()
    if units not in CURVE_UNITS:
        raise CurveReadError(
            f"{source_name}: the fourth header line should be the curve "
            f"units, one of {', '.join(CURVE_UNITS)}, but it reads "
            f"'{lines[3]}'.")

    points = []
    texts = []
    terminated = False
    for line in lines[4:]:
        if line.startswith(';'):
            terminated = True
            break
        tokens = line.split()
        if not _tokens_are_numeric(tokens, count=2):
            raise CurveReadError(
                f"{source_name}: '{line}' is not a pair of numbers. A curve "
                "entry is the sensor reading then the temperature in "
                "kelvin.")
        points.append((float(tokens[0]), float(tokens[1])))
        texts.append((tokens[0], tokens[1]))

    if not terminated:
        raise CurveReadError(
            f"{source_name} did not end with the semicolon line that marks "
            "the end of a Cryo-con curve, so the reply may be truncated. "
            "Nothing is shown from a partial read.")
    if len(points) < 2 and not (allow_empty and not points):
        # A slot with exactly one point is a broken curve and is refused.
        # A slot with NO points is an empty slot, which is a different thing
        # and only an error to a caller that did not ask for it.
        raise CurveReadError(
            f"{source_name} holds {len(points)} point(s); a curve needs at "
            "least two to interpolate.")

    header = {
        'name': name,
        'sensor_type': sensor_type,
        'multiplier': multiplier,
        'multiplier_text': lines[2],     # as printed, for the .crv export
        'units': units,
        'point_texts': texts,
        # True when the block is a header and nothing else: an empty slot.
        'no_points': not points,
    }
    return header, points


def looks_like_empty_slot(name):
    """True if a slot name means 'nothing stored here'."""
    stripped = str(name or '').strip().strip('"').strip().upper()
    return stripped in EMPTY_NAME_MARKERS


def looks_like_factory_entry(name):
    """True if a slot name looks like one of the built-in sensor entries.

    Cosmetic only: it labels a row in the list. Nothing is done or refused on
    the strength of it, which is why a guess here is harmless.
    """
    upper = str(name or '').strip().upper()
    return any(marker in upper for marker in FACTORY_NAME_MARKERS)


def curve_statistics(points, units):
    """Plain-language facts about a set of curve points.

    points is (reading, temperature). Nothing here is a judgement; it is what
    is in the numbers.
    """
    if not points:
        return {}
    readings = [pair[0] for pair in points]
    temps = [pair[1] for pair in points]
    ascending_t = all(b > a for a, b in zip(temps, temps[1:]))
    descending_t = all(b < a for a, b in zip(temps, temps[1:]))
    return {
        'count': len(points),
        'reading_min': min(readings),
        'reading_max': max(readings),
        'temp_min': min(temps),
        'temp_max': max(temps),
        'units': units,
        'readings_ascending': all(b > a for a, b in zip(readings,
                                                        readings[1:])),
        'temperature_monotonic': ascending_t or descending_t,
        'temperature_direction': ("rising with the sensor reading"
                                  if ascending_t else
                                  "falling as the sensor reading rises"
                                  if descending_t else
                                  "NOT monotonic"),
    }


def reading_in_ohms(value, units):
    """The reading converted to ohms, or None if the units are not resistive."""
    converter = UNITS_TO_OHMS.get(str(units).upper())
    if converter is None:
        return None
    try:
        return converter(value)
    except (OverflowError, ValueError):
        return None


# ===============================================================================
# IS THE CURVE IN THE SLOT ACTUALLY RIGHT?  (added 16 Sep 2026)
# ===============================================================================
#
# Reading a curve back proves the bytes arrived. It does not prove the curve
# is usable, and the two failures of 29 and 31 Aug were both cases where every
# point was fine and the HEADER was not. A header field the Model 34 cannot
# identify is replaced with a default rather than reported -- the multiplier
# silently becomes -1.0, the type silently becomes a diode -- so a curve can
# read back complete and still measure nonsense.
#
# What that costs is worth stating plainly: a diode curve stored against a
# resistance range is measured with the wrong excitation and returns a
# PLAUSIBLE WRONG TEMPERATURE. It does not fail, it does not warn, and the
# number it gives looks exactly like a reading. That is the failure this
# section exists to catch, and it is why every check below is run on the curve
# in the instrument rather than on the file that was sent to it.
#
# Nothing here writes. Nothing here is a guess dressed as a fact: each finding
# names the two numbers it compared.

# Full scale of each input range, for the check that a curve fits the range it
# is stored against. Folded keys, both vocabularies, because the firmware and
# the manual spell the same type differently. None means the range autoranges
# or has no single full scale, and the check is skipped rather than invented.
TYPE_FULL_SCALE = {
    'SIDIODE': (2.5, 'V'), 'DIODE': (2.5, 'V'),
    'TC80': (0.080, 'V'), 'TC40': (0.040, 'V'),
    'R8K10UA': (8.0e3, 'ohm'), 'R16K10UA': (16.0e3, 'ohm'),
    'R6K100UA': (6.25e3, 'ohm'), 'R2K100UA': (2.0e3, 'ohm'),
    'R625R1MA': (625.0, 'ohm'), 'R312R1MA': (312.0, 'ohm'),
    '31KR': (31.3e3, 'ohm'), '3.1KR': (3.13e3, 'ohm'),
    '625R': (625.0, 'ohm'), '312R': (312.0, 'ohm'),
    'ACR': (None, 'ohm'), 'SNONE': (None, ''), 'NONE': (None, ''),
}

# The manual's own limits on a CALCUR block, repeated here so this window can
# say a stored curve breaks one without the loader being open.
AUDIT_MIN_POINTS = 2
AUDIT_MAX_POINTS = 200
AUDIT_MIN_NAME = 4
AUDIT_MAX_NAME = 15


def audit_curve(index, header, points, catalogue=None):
    """Check a curve read off the instrument. Returns (problems, notes).

    `problems` are things that would make the sensor read wrongly or the
    curve unusable. `notes` are facts worth knowing that are not faults.
    Both are plain sentences, each naming what was compared, so the operator
    can disagree with any one of them on the evidence rather than on trust.

    `catalogue` is an optional table scan; where one is present the slot's
    position is checked against the user block derived from it.
    """
    problems = []
    notes = []
    if not header:
        return (["Nothing was read from this slot, so there is nothing to "
                 "check."], notes)

    name = str(header.get('name', '')).strip()
    sensor_type = str(header.get('sensor_type', '')).strip()
    units = str(header.get('units', '')).strip().upper()
    multiplier = header.get('multiplier')
    folded = normalise_type(sensor_type)

    # -- the slot is empty -------------------------------------------------
    if not points:
        slot_number = user_slot_number(name)
        if slot_number is not None:
            notes.append(
                f"This slot is EMPTY. '{name}' is the placeholder an "
                f"untouched user slot answers with, and it is user curve "
                f"{slot_number}. Nothing is stored here to be wrong.")
        else:
            problems.append(
                f"This slot holds a header named '{name}' and NO points. A "
                "curve needs at least two to interpolate between, so nothing "
                "can read a temperature from this.")
        return problems, notes

    readings = [pair[0] for pair in points]
    temps = [pair[1] for pair in points]

    # -- the header ---------------------------------------------------------
    if len(name) < AUDIT_MIN_NAME:
        problems.append(
            f"The name '{name}' is {len(name)} characters; the instrument "
            f"needs at least {AUDIT_MIN_NAME}.")
    elif len(name) > AUDIT_MAX_NAME:
        problems.append(
            f"The name '{name}' is {len(name)} characters; the instrument "
            f"keeps {AUDIT_MAX_NAME} and truncates the rest.")
    if any(not (32 <= ord(char) < 127) for char in name):
        problems.append(
            f"The name '{name}' holds a character that is not printable "
            "ASCII, so it will not survive being written back out.")
    if user_slot_number(name) is not None:
        problems.append(
            f"The name '{name}' is the placeholder an untouched slot uses, "
            "yet there are points stored here. Something wrote points "
            "without the header landing, which should not be possible; read "
            "the raw reply and treat this curve as untrustworthy.")

    if units not in CURVE_UNITS:
        problems.append(
            f"The curve units read '{units}', which is not one of "
            f"{', '.join(CURVE_UNITS)}.")

    # -- the type against the units -----------------------------------------
    # This is the check that matters most, and the one nothing on the
    # instrument does for you.
    type_family = type_unit_family(sensor_type)
    units_family = UNITS_FAMILY.get(units)
    if type_family is None:
        notes.append(
            f"The sensor type reads '{sensor_type}', which is not a name "
            "this module knows. It may be a spelling this firmware uses and "
            "nothing here has seen; run the type query to find out what "
            "names this instrument does use.")
    elif units_family and type_family != units_family:
        problems.append(
            f"The sensor type '{sensor_type}' is a "
            f"{'voltage' if type_family == 'V' else 'resistance'} input "
            f"while the curve units are {units}, which is a "
            f"{'voltage' if units_family == 'V' else 'resistance'}. A sensor "
            "stored against the wrong kind of input is measured with the "
            "wrong excitation and returns a plausible WRONG temperature "
            "rather than failing. Check SENTYPE for any channel using this "
            "curve.")

    # -- the curve against the full scale of that input ---------------------
    full_scale, fs_unit = TYPE_FULL_SCALE.get(folded, (None, None))
    if full_scale:
        if units == 'VOLTS' and fs_unit == 'V':
            worst = max(readings)
            if worst > full_scale:
                problems.append(
                    f"The curve reaches {worst:g} V but the '{sensor_type}' "
                    f"input measures only to {full_scale:g} V full scale. "
                    "Everything above that is off the end of the range.")
        elif fs_unit == 'ohm' and units in ('OHMS', 'LOGOHM'):
            ohms = [reading_in_ohms(value, units) for value in readings]
            ohms = [value for value in ohms if value is not None]
            if ohms and max(ohms) > full_scale:
                problems.append(
                    f"The curve reaches {max(ohms):.6g} ohm but the "
                    f"'{sensor_type}' range measures only to "
                    f"{full_scale:.6g} ohm full scale. The cold end of this "
                    "sensor is off the range.")

    # -- the multiplier against the data ------------------------------------
    # The sign of the multiplier IS the temperature coefficient, and the data
    # states the same thing independently, so the two can be compared instead
    # of the sign being taken on trust.
    #
    # A single point states no direction at all, so the comparison is only
    # made from two points up. Without that guard a one-point curve reads as
    # both ascending and descending -- all() of an empty sequence is True --
    # and the sign check fires on nothing.
    ascending_t = len(temps) > 1 and all(b > a for a, b in zip(temps,
                                                               temps[1:]))
    descending_t = len(temps) > 1 and all(b < a for a, b in zip(temps,
                                                                temps[1:]))
    if len(temps) > 1 and not (ascending_t or descending_t):
        problems.append(
            "Temperature does not move in one direction through this curve. "
            "It must rise or fall monotonically against the sensor reading, "
            "or interpolation has more than one answer for the same reading.")
    elif ascending_t or descending_t:
        implied = -1.0 if descending_t else 1.0
        if multiplier:
            sign = 1.0 if multiplier > 0 else -1.0
            if sign != implied:
                problems.append(
                    f"The multiplier is {multiplier:g}, so its sign says the "
                    f"temperature coefficient is "
                    f"{'positive' if sign > 0 else 'negative'}, but the "
                    "points themselves say temperature "
                    f"{'rises' if ascending_t else 'falls'} as the reading "
                    f"rises, which is a "
                    f"{'positive' if ascending_t else 'negative'} "
                    "coefficient. One of the two is wrong.")

    # Checked whatever the points do, because a zero multiplier is evidence
    # about the HEADER and the points have no bearing on it. Trapped inside
    # the monotonic branch, this never ran on the curve most likely to have
    # a substituted header.
    if multiplier == 0:
        problems.append(
            "The multiplier is 0, which carries no temperature coefficient "
            "at all. An unidentified multiplier is silently replaced with a "
            "default by this firmware, so this is a sign the header did not "
            "land as it was sent.")
    elif multiplier is not None and abs(multiplier) != 1.0:
        # Not a fault: 'Pt1K 385' on this instrument carries 10.0, because
        # the stored table is a Pt100 and the multiplier scales it. But if
        # the curve already holds the sensor's true readings, a multiplier
        # other than +-1 scales them a second time, and that is worth
        # knowing rather than discovering from a reading that is out by a
        # factor of ten.
        notes.append(
            f"The multiplier is {multiplier:g}, not +-1. The instrument "
            "scales the stored readings by it, so this curve is only right "
            f"if its readings really are the sensor's own divided by "
            f"{abs(multiplier):g}. Two of the factory entries here work that "
            "way; a curve loaded from a calibration file normally does not.")

    # -- the points themselves ----------------------------------------------
    if len(points) < AUDIT_MIN_POINTS:
        problems.append(
            f"{len(points)} point(s) are stored; at least {AUDIT_MIN_POINTS} "
            "are needed to interpolate.")
    if len(points) > AUDIT_MAX_POINTS:
        problems.append(
            f"{len(points)} points are stored, above the "
            f"{AUDIT_MAX_POINTS} the manual allows.")

    if not all(b > a for a, b in zip(readings, readings[1:])):
        repeats = sorted({a for a, b in zip(readings, readings[1:]) if a == b})
        if repeats:
            problems.append(
                f"{len(repeats)} sensor reading(s) appear more than once, "
                f"starting at {repeats[0]:g}. The instrument interpolates on "
                "the reading, so a repeated reading has two temperatures and "
                "no way to choose.")
        else:
            problems.append(
                "The sensor readings are not in ascending order. This "
                "firmware sorts a curve as it stores it, so a stored curve "
                "that is out of order means the block did not land whole.")

    if min(temps) <= 0:
        problems.append(
            f"The coldest point is {min(temps):g} K. Temperature in a "
            "Cryo-con curve is absolute, so nothing at or below 0 K belongs "
            "in one.")

    # -- resolution: how far apart the breakpoints are ----------------------
    # Not a fault, but it is what limits the instrument between breakpoints,
    # and it is the number to quote when somebody asks how good the curve is.
    gaps = [abs(b - a) for a, b in zip(temps, temps[1:])]
    if gaps:
        worst = max(gaps)
        at = temps[gaps.index(worst)]
        notes.append(
            f"{len(points)} breakpoints from {min(temps):g} K to "
            f"{max(temps):g} K. The instrument interpolates linearly between "
            f"them; the widest gap is {worst:g} K, near {at:g} K.")

    # -- where the slot sits ------------------------------------------------
    if catalogue:
        blocks = map_table_blocks(catalogue)
        if blocks['offset'] is not None and index is not None:
            if index <= blocks['factory_last']:
                problems.append(
                    f"Index {index} is inside the factory block "
                    f"(0 to {blocks['factory_last']} on this instrument). "
                    "Factory curves cannot be replaced, so anything sent "
                    "here is discarded without a word.")
            elif index > blocks['user_last']:
                problems.append(
                    f"Index {index} is above the last user slot "
                    f"({blocks['user_last']}).")
            else:
                notes.append(
                    f"Index {index} is user curve "
                    f"{index - blocks['offset']} of 12.")

    return problems, notes


# ---------------------------------------------------------------------------
# LAKE SHORE .340 EXPORT
# ---------------------------------------------------------------------------
#
# A .340 is a breakpoint table written for a Lake Shore Model 340, and it is
# the format the rest of this lab's curves are kept in: the sensor CD ships
# them, the Lake Shore 340 and 350 loaders in this suite read them, and the
# Cryo-con loader prefers them. Being able to write one means a curve that
# exists only inside a Cryo-con can be put on a Lake Shore, kept as a record
# in the same format as everything else, or sent back through the Cryo-con
# loader for a round trip that proves the transfer.
#
# The two formats are the same shape, which is why this is a rewrite of the
# header and nothing more: both list the sensor reading first and the
# temperature second, both are in kelvin, and both are ascending in the
# reading. The only real work is the Data Format code.
#
# Lake Shore data-format codes, as the .340 header prints them:
#     1  mV/K        2  V/K        3  Ohm/K        4  Log Ohm/K
# Temperature coefficient: 1 is negative, 2 is positive.
LAKESHORE_FORMAT_FOR_UNITS = {
    'VOLTS':  (2, 'V/K'),
    'OHMS':   (3, 'Ohm/K'),
    'LOGOHM': (4, 'Log Ohm/K'),
}


def build_lakeshore_340_text(header, points, index=None, idn="", address=""):
    """One curve as a Lake Shore .340 breakpoint table.

    The numerals are the ones the instrument printed, not a re-rounding of
    them, for the same reason the .crv export keeps them: how many digits the
    instrument prints is the limit on how precisely this curve can be quoted,
    and that is gone once the text has become a float.

    The temperature coefficient is taken from the POINTS, not from the
    multiplier, because the multiplier is a header field this firmware
    replaces silently when it cannot identify one and the points are not.
    Where the two disagree the file still says what the data says, and
    audit_curve() is what reports the disagreement.
    """
    if not points:
        raise ValueError("A .340 file needs at least one breakpoint.")
    units = str(header.get('units', '')).strip().upper()
    if units not in LAKESHORE_FORMAT_FOR_UNITS:
        raise ValueError(
            f"A curve in {units or 'unknown'} units has no Lake Shore data "
            "format. Only VOLTS, OHMS and LOGOHM map onto one without an "
            "assumption being made.")
    fmt_code, fmt_name = LAKESHORE_FORMAT_FOR_UNITS[units]

    # Keep the instrument's own numerals where they are available, and pair
    # each with its point so sorting moves both together.
    texts = header.get('point_texts')
    if texts and len(texts) == len(points):
        rows = [(pair[0], pair[1], texts[n][0], texts[n][1])
                for n, pair in enumerate(points)]
    else:
        rows = [(pair[0], pair[1], fmt6(pair[0]), fmt6(pair[1]))
                for pair in points]
    # A .340 is ascending in the reading column. A Cryo-con stores it that
    # way already, so this normally changes nothing; it is here so a curve
    # that came back out of order still writes a valid file.
    rows.sort(key=lambda row: row[0])

    temps = [row[1] for row in rows]
    if len(rows) < 2:
        # One point states no coefficient, and a .340 header has to declare
        # one. Refused rather than written with a coin-toss in the header.
        raise ValueError(
            "A curve of one point has no temperature coefficient to put in "
            "a .340 header, and nothing can interpolate through it. Read a "
            "slot that holds a real curve.")
    descending = temps[0] > temps[-1]
    coefficient = 1 if descending else 2
    coefficient_word = "Negative" if descending else "Positive"

    name = str(header.get('name', '')).strip() or "CryoconCurve"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # A Cryo-con curve has one name where a .340 has a model and a serial, so
    # the name is split at its first space -- which is exactly how the Cryo-con
    # loader joined them when it built the name in the first place. 'CX1030
    # X17680' goes back out as model CX1030, serial X17680 and comes back in
    # as 'CX1030 X17680', so the round trip is exact for any name that came
    # from a .340. A one-word name has no serial to recover and repeats
    # itself, which is visibly odd rather than quietly wrong.
    model, _, serial = name.partition(' ')
    serial = serial.strip() or model

    lines = [
        f"Sensor Model:   {model}",
        f"Serial Number:  {serial}",
        f"Data Format:    {fmt_code}      ({fmt_name})",
        # Written with a decimal point, the way Lake Shore's own files write
        # it ('475.0'), so nothing downstream has to decide how to read a
        # bare integer in a field that is a temperature.
        f"SetPoint Limit: {fmt6(max(temps))}      (Kelvin)",
        f"Temperature coefficient:  {coefficient} ({coefficient_word})",
        f"Number of Breakpoints:   {len(rows)}",
        "",
        "No.   Units      Temperature (K)",
        "",
    ]
    for number, row in enumerate(rows, start=1):
        lines.append(f"{number:3d}  {row[2]:<12s} {row[3]}")
    # Provenance goes at the FOOT, not the head: a Lake Shore 340 reads the
    # first lines of this file as its header, and the loaders in this suite
    # read a three-number line as a breakpoint. Comments below the table are
    # past both.
    lines += [
        "",
        f"Read from {idn or 'a Cryo-con'} at "
        f"{address or 'an unknown address'}",
        f"Master Sensor Table index {index if index is not None else '?'}, "
        f"read {stamp}",
        f"Cryo-con header: name '{name}', type "
        f"'{str(header.get('sensor_type', '')).strip()}', multiplier "
        f"{header.get('multiplier')}, units {units}",
    ]
    text = "\n".join(lines) + "\n"
    text.encode('ascii')          # raises rather than writing a bad file
    return text


# ---------------------------------------------------------------------------
# FILE WRITERS
# ---------------------------------------------------------------------------

def build_crv_lines(header, points):
    """The lines of a .crv file: header, points, terminator.

    Written from what came off the instrument, in the order it printed them,
    with the numerals it printed rather than re-formatted floats. A .crv
    written this way is what the instrument holds, not this module's rounding
    of it, so it can be loaded back into another Cryo-con and give the same
    curve.
    """
    if not points:
        raise ValueError("A .crv file needs at least one point.")
    # The multiplier goes out as the instrument printed it, like the points;
    # a header without one is not given a default, because the file would
    # then say something the instrument did not.
    if header.get('multiplier_text') is None and \
            header.get('multiplier') is None:
        raise ValueError("This curve's header carries no multiplier, so a "
                         ".crv cannot be written without inventing one.")
    lines = [str(header.get('name', '')),
             str(header.get('sensor_type', '')),
             (str(header['multiplier_text']).strip()
              if header.get('multiplier_text') is not None
              else fmt6(header['multiplier'])),
             str(header.get('units', '')).upper()]
    texts = header.get('point_texts')
    if texts and len(texts) == len(points):
        for reading_text, temperature_text in texts:
            lines.append(f"{reading_text}   {temperature_text}")
    else:
        for reading, temperature in points:
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


def build_curve_csv(index, header, points, idn="", address=""):
    """One curve as CSV: a '#' preamble, a column header, then the numbers."""
    units = str(header.get('units', '')).upper()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Cryo-con Model 34 calibration curve, read back over the bus",
        f"# Read on: {stamp}",
        f"# Instrument: {idn or 'unknown'}",
        f"# VISA address: {address or 'unknown'}",
        f"# Master Sensor Table index: {index}",
        f"# Name: {header.get('name')}",
        f"# Sensor type: {header.get('sensor_type')}",
        f"# Multiplier: {header.get('multiplier')}",
        f"# Curve units: {units}",
        f"# Points read: {len(points)}",
        "# Column order is the instrument's own: the sensor reading comes "
        "first, the temperature second.",
        f"Index,Reading_{units},Temperature_K,Resistance_ohm",
    ]
    for number, (reading, temperature) in enumerate(points, start=1):
        ohms = reading_in_ohms(reading, units)
        lines.append(f"{number},{fmt6(reading)},{fmt6(temperature)},"
                     f"{fmt6(ohms) if ohms is not None else ''}")
    return "\n".join(lines) + "\n"


def build_catalogue_csv(entries, idn="", address=""):
    """Every Master Sensor Table entry the scan reached, as CSV."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Cryo-con Model 34 Master Sensor Table",
        f"# Read on: {stamp}",
        f"# Instrument: {idn or 'unknown'}",
        f"# VISA address: {address or 'unknown'}",
        "# Read with SENTYPE? only. The curve points themselves were not "
        "read with CALCUR?; the names are what identify a slot.",
        "Index,Name,Type,Multiplier,Looks",
    ]
    for entry in entries:
        name = entry.get('name')
        if name is None:
            looks = "no answer"
            name = ""
        elif looks_like_empty_slot(name):
            looks = "empty"
        elif looks_like_factory_entry(name):
            looks = "factory"
        else:
            looks = "user"
        lines.append(
            f"{entry.get('index')},\"{name}\","
            f"\"{entry.get('type') or ''}\","
            f"\"{entry.get('multiplier') or ''}\",{looks}")
    return "\n".join(lines) + "\n"


# ===============================================================================
# INSTRUMENT LINK  --  QUERIES ONLY
# ===============================================================================

CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON")
# The lab's Cryocon 34 was moved to IEEE address 23 on 3 Sep 2026: 12 is the
# shared factory default of the Cryocon, the Lakeshore 340/350 and the 6221.
# Board-independent hint ("::23::INSTR" matches GPIB0 or GPIB1); *IDN? decides.
CRYOCON_ADDRESS_HINT = "::23::INSTR"       # where the CC34 sits in this lab

CRYOCON_TIMEOUT_MS = 10000
CRYOCON_OPEN_SETTLE_S = 0.30
CRYOCON_MIN_GAP_S = 0.08
CRYOCON_CONNECT_ATTEMPTS = 3
CRYOCON_RETRY_WAIT_S = 1.5

# CALCUR? walks flash before it answers. The Rev 3.03A unit in this lab has
# been seen to take up to about twelve seconds over it and, on the run of
# 15 Sep 2026, to answer an 88-point curve in half a second. Both happen, so
# this timeout covers the slow case and nothing here promises the slow one.
CURVE_READ_TIMEOUT_MS = 20000
CURVE_READ_MAX_LINES = MAX_CURVE_POINTS + 12

IDN_SCAN_TIMEOUT_MS = 1500
PROBE_RESOURCE_PREFIXES = ('GPIB', 'USB', 'TCPIP')

EVENT_POLL_MS = 50

# How long the window waits for a running read before it destroys itself. A
# A CALCUR? read has been seen to take up to about twelve seconds on this
# firmware, so the wait has to be longer than that or it would time out on a
# normal close during a slow read.
WORKER_JOIN_TIMEOUT_S = 15.0


def is_cryocon_idn(idn):
    """True if a '*IDN?' reply came from a Cryo-con temperature instrument."""
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


# The shape of a Cryo-con query: an optional '*', a mnemonic, optionally one
# argument that may carry a ':SUBSYSTEM' chain (SENTYPE 15:TYPE?,
# INPUT A:SENIX?), the '?' directly after that, then at most a parameter
# list of letters, digits, commas, dots and spaces. The old test was "'?'
# anywhere in the text", which admitted 'CALCUR? 15;CALCUR 15' -- and a
# Cryo-con takes several commands on one line separated by ';'.
QUERY_RE = re.compile(
    r'^\*?[A-Z][A-Z0-9]{0,7}(?:[ \t]+[A-Z0-9]+(?::[A-Z0-9]+)*)?\?'
    r'(?:[ \t]+[A-Z0-9,. \t]*)?$')

# Mnemonics that have no query form. A '?' behind one of these is a
# malformed setting command, never a question.
NEVER_A_QUERY = ('*RST', '*CLS', 'STOP', 'CONTROL')


def is_query(command):
    """True if `command` is a Cryo-con query and nothing else.

    A Cryo-con command is a query when it carries a '?' directly after its
    mnemonic or its argument: CALCUR? reads a curve, CALCUR writes one;
    SENTYPE 3:NAME? reads a name, SENTYPE 3:NAME sets it. The test admits
    that shape and only that shape -- one '?', no ';', no quoted text -- so a
    compound line, a trailing '?' behind a setting command, or a second
    command after a separator is refused. Admitting by shape rather than
    forbidding by list means a command added later cannot slip past.
    """
    text = str(command).strip().upper()
    if not QUERY_RE.match(text):
        return False
    mnemonic = text.split('?', 1)[0].split()[0]
    return mnemonic not in NEVER_A_QUERY


class CryoconReadOnlyLink:
    """One paced VISA session to a Cryo-con that can only ask questions.

    There is deliberately no write() method that takes an arbitrary command.
    ask() and ask_block() are the only ways anything reaches the bus, and
    both refuse a command without a '?' before opening their mouth. Nothing
    in this module can change a curve, a sensor type, a channel assignment, a
    setpoint or a heater range, and nothing added to it later can either
    without removing this guard on purpose.
    """

    def __init__(self, visa_address, timeout_ms=CRYOCON_TIMEOUT_MS, log=None):
        # Gate on the module object rather than the import-time flag: the
        # test harness swaps in a fake pyvisa after import, and a flag frozen
        # at import would lock it out.
        if pyvisa is None:
            raise ConnectionError(
                "PyVISA is not available. Install pyvisa and a VISA backend "
                "(NI-VISA or pyvisa-py).")
        self.address = visa_address
        self.timeout_ms = timeout_ms
        self.instrument = None
        self.idn = ""
        self.commands_sent = 0
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
                # character, so the PyVISA termination defaults are left
                # alone.
                time.sleep(CRYOCON_OPEN_SETTLE_S)
                self.idn = self.ask('*IDN?')
                if not self.idn:
                    raise ConnectionError(
                        f"{self.address} accepted the command but sent no "
                        "identification.")
                if attempt > 1:
                    self._log(f"  Cryocon answered on attempt {attempt}.")
                return
            except ReadOnlyViolation:
                raise
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
            f"{CRYOCON_CONNECT_ATTEMPTS} attempts. Last error: "
            f"{last_error}. Check that the instrument is powered, that its "
            "SYS menu has RIO-Port set to GPIB rather than RS-232, and that "
            "RIO-Address matches this VISA address.")

    # -- the only two ways to the bus --

    def _pace(self, gap=None):
        """Hold a minimum gap between operations.

        The gap is looked up when it is needed rather than bound as a default
        argument, so slowing the bus down for a sulky firmware revision -- or
        speeding it up under test -- is a matter of changing the module
        constant and nothing else.
        """
        gap = CRYOCON_MIN_GAP_S if gap is None else gap
        wait = gap - (time.time() - self._last_io)
        if wait > 0:
            time.sleep(wait)

    def _guard(self, command):
        if not is_query(command):
            raise ReadOnlyViolation(
                f"This module is read-only and {command!r} is not a query. "
                "Only commands carrying a '?' are ever transmitted. Nothing "
                "was sent.")

    def ask(self, command):
        """Send one query and return its single-line reply."""
        self._guard(command)
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        self._pace()
        try:
            reply = self.instrument.query(command)
        finally:
            self._last_io = time.time()
            self.commands_sent += 1
        return reply.strip()

    def ask_block(self, command, max_lines=CURVE_READ_MAX_LINES,
                  timeout_ms=CURVE_READ_TIMEOUT_MS, terminator=';',
                  progress=None, should_stop=None):
        """Send one query and read its reply until the terminator line.

        Used for CALCUR?, whose reply is a header, up to 200 points and a
        semicolon. On GPIB each line arrives as its own message; on an
        interface that packs the block into one message the first read
        returns everything and the loop ends immediately.

        The command still goes through the same guard, so a block read cannot
        be used as a back door for a setting command.
        """
        self._guard(command)
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        previous_timeout = self.instrument.timeout
        collected = []
        try:
            self.instrument.timeout = timeout_ms
            self._pace()
            try:
                self.instrument.write(command)
            finally:
                self._last_io = time.time()
                self.commands_sent += 1
            for line_number in range(max_lines):
                if should_stop is not None and should_stop():
                    # The lines already in hand are returned as they are;
                    # without the closing semicolon the parser refuses them
                    # as a curve, which is right, and the raw tab shows them.
                    break
                # No pacing between the lines of one reply: the gap is for
                # commands, and at 80 ms a line it added sixteen seconds to
                # a 200-point curve.
                try:
                    try:
                        chunk = self.instrument.read().strip()
                    finally:
                        self._last_io = time.time()
                except Exception:
                    # A timeout here is how a Cryo-con says "that was the
                    # last line". Whether that is a complete reply is decided
                    # by the parser from the text, not guessed at here.
                    break
                if chunk:
                    collected.append(chunk)
                if progress:
                    progress(line_number + 1, max_lines)
                if any(part.strip() == terminator
                       for part in chunk.replace('\r', '\n').split('\n')):
                    break
        finally:
            try:
                self.instrument.timeout = previous_timeout
            except Exception:
                pass
        return "\n".join(collected)

    @property
    def is_connected(self):
        return self.instrument is not None

    def close(self):
        """Close the session only. No *RST, no STOP, no heater, loop or
        setpoint command, so whatever is driving the cryostat carries on."""
        self._drop_session()


class CurveViewerBackend:
    """Everything this module says to a Cryo-con Model 34. All of it queries."""

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
        self.link = CryoconReadOnlyLink(visa_address, log=self.log)
        idn = self.link.idn
        # Nothing here can damage a foreign instrument, but CALCUR? on
        # something that is not a Cryo-con returns whatever that instrument
        # makes of it, and reading a stranger's reply as a curve would put
        # invented numbers on the screen.
        if not is_cryocon_idn(idn):
            self.disconnect()
            raise ConnectionError(
                f"{visa_address} is not a Cryo-con: it identifies itself as "
                f"'{idn}'. Scan the bus and pick the Cryocon's actual "
                f"address (it does not have to be {CRYOCON_ADDRESS_HINT}).")
        return idn

    def disconnect(self):
        """Closes the VISA session and nothing else."""
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

    @property
    def idn(self):
        return self.link.idn if self.link else ""

    @property
    def address(self):
        return self.link.address if self.link else ""

    # -- reading --

    def scan_sensor_table(self, first=MIN_TABLE_INDEX, last=MAX_TABLE_INDEX,
                          progress=None, should_stop=None):
        """Walk the Master Sensor Table and report what is in it.

        Three queries per index. CALCUR? is deliberately NOT used: on this
        firmware one of those can take about twelve seconds, so thirty-two of
        them risks six minutes on the bus, while SENTYPE? answers in well
        under a second and gives the name, which is what identifies a slot.
        Read the curve itself afterwards, at the indices that matter.

        An index that will not answer is included with None fields rather
        than dropped, because a gap in the table is itself informative.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        entries = []
        total = last - first + 1
        for offset, index in enumerate(range(first, last + 1), start=1):
            if should_stop is not None and should_stop():
                break
            entry = {'index': index}
            for key, command in (('name', f"SENTYPE? {index}"),
                                 ('type', f"SENTYPE {index}:TYPE?"),
                                 ('multiplier', f"SENTYPE {index}:MULTIPLY?")):
                try:
                    value = self.link.ask(command)
                    entry[key] = value if value else None
                except ReadOnlyViolation:
                    raise
                except Exception:
                    entry[key] = None
            entries.append(entry)
            if progress:
                progress(offset, total, entry)
        return entries

    def read_slot_curve(self, index, progress=None, should_stop=None):
        """Read one slot with CALCUR? and parse it.

        Returns (header, points, raw_text). An empty slot returns its
        placeholder header and an EMPTY point list, which is what it really
        holds; header and points are None only when the reply is not a curve
        at all. raw_text is always
        whatever came back, so the caller can print it rather than have this
        function decide quietly that nothing was there.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        if not (MIN_TABLE_INDEX <= index <= MAX_TABLE_INDEX):
            raise ValueError(
                f"Table index must be {MIN_TABLE_INDEX} to "
                f"{MAX_TABLE_INDEX}, not {index}.")
        text = self.link.ask_block(f"CALCUR? {index}", progress=progress,
                                   should_stop=should_stop)
        if not text.strip():
            return None, None, text
        try:
            header, points = parse_calcur_block(text, f"slot {index}",
                                                allow_empty=True)
        except CurveReadError as exc:
            # Say WHY, or a timed-out read looks exactly like an empty slot.
            self.log(f"  Slot {index} answered, but not with a curve: {exc}")
            return None, None, text
        return header, points, text

    def read_channel_sensors(self):
        """Which sensor index each input is using, and what it reads.

        Edition 4 documents INPUT <ch>:SENIX. This module used to ask ISENIX
        and USENIX alongside it, because a sibling module in this suite uses
        those and the three number differently, and asking all three was
        better than picking one and being quietly wrong.

        16 Sep 2026: the run of 15 Sep answered that question. ISENIX and
        USENIX do not exist on this firmware -- all eight queries timed out,
        on every channel -- while SENIX answered immediately and returned the
        Master Sensor Table index directly (A was 17, which is 'CX1030
        X17681'). There is one scheme, not three.

        So they are still asked, once, on the first channel: this is one unit
        and one firmware revision, and a module that stops looking is a
        module that cannot notice it was wrong. But once both have failed
        they are not asked again, because each timeout costs about ten
        seconds and asking eight of them turned a one-second answer into an
        eighty-second wait for information that was not there.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        result = {}
        ask_alternates = True
        for channel in INPUT_CHANNELS:
            entry = {}
            commands = [('SENIX', f"INPUT {channel}:SENIX?")]
            if ask_alternates:
                commands += [('ISENIX', f"INPUT {channel}:ISENIX?"),
                             ('USENIX', f"INPUT {channel}:USENIX?")]
            commands.append(('reading', f"INPUT? {channel}"))
            for key, command in commands:
                try:
                    entry[key] = self.link.ask(command)
                except ReadOnlyViolation:
                    raise
                except Exception as exc:
                    entry[key] = f"<no answer: {type(exc).__name__}>"
            if ask_alternates:
                alternates_dead = all(
                    str(entry.get(key, '')).startswith('<no answer')
                    for key in ('ISENIX', 'USENIX'))
                if alternates_dead:
                    ask_alternates = False
                    self.log(
                        "  ISENIX? and USENIX? did not answer on input "
                        f"{channel}. Edition 4 does not list them and this "
                        "firmware does not have them, so they are not asked "
                        "on the other channels; each timeout costs about ten "
                        "seconds. SENIX is the only scheme here, and it "
                        "gives the Master Sensor Table index directly.")
            for key in ('ISENIX', 'USENIX'):
                entry.setdefault(key, "<not asked: no answer on the first "
                                      "channel>")
            result[channel] = entry
        return result


# ===============================================================================
# GUI
# ===============================================================================

class CurveViewerGUI:
    """Browse and export the curves already inside a Cryo-con Model 34.

    The left panel is the job in order: connect, list what is there, read one
    slot, save it. The right panel always shows the slot that was read, as a
    plot and as every point, so nothing is exported unseen.
    """

    PROGRAM_VERSION = "1.1"
    PROGRAM_NAME = "Cryocon 34 Sensor Curve Viewer"

    # Which tab is which, in the order _populate_right_panel() adds them.
    # Named rather than counted at the call site, so inserting a tab does not
    # silently send a report to the wrong one.
    CURVE_TAB, LIST_TAB, CHECKS_TAB, TYPES_TAB, RAW_TAB = 0, 1, 2, 3, 4

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

        # Everything a worker thread wants the window to do goes through this
        # queue and is carried out by _drain_events() on the Tk thread.
        # Tkinter is not thread-safe, and root.after() is not an escape from
        # that: called from a worker it raises 'main thread is not in main
        # loop' unless the main thread happens to be inside mainloop().
        self._events = queue.Queue()
        self.backend = CurveViewerBackend(log=self.log)
        self.logo_image = None
        self.is_connected = False
        self.busy = False
        self._stop_flag = threading.Event()
        # SHUTDOWN (added 12 Sep 2026, after 'Tcl_AsyncDelete: async handler
        # deleted by the wrong thread' on closing the window). The stop flag
        # alone was not enough: it is only honoured between lines, and a
        # CALCUR? read can be twelve seconds, so destroy() ran while the worker
        # was still alive holding a reference to this window and through it
        # to the Tk interpreter. The launcher drops the main thread's copy as
        # soon as mainloop() returns, so the worker became the last holder
        # and Tk was freed on a thread that never created it. The window now
        # waits for the worker and cancels its own after() chain first.
        self._closing = False
        self._worker = None
        self._poll_id = None

        self.catalogue = []           # every SENTYPE? entry the scan reached
        self.blocks = {}              # where the user block starts, derived
        self.slot_index = None        # the slot on screen
        self.header = None
        self.points = []
        self.raw_text = ""

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._drain_events()          # starts the main-thread event pump
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
        style.configure('Read.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK,
                        font=('Segoe UI', 12, 'bold'), padding=(10, 12))
        style.map('Read.TButton',
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
        ttk.Label(header, text="read-only · queries only",
                  style='Header.TLabel', font=('Segoe UI', 10, 'italic'),
                  foreground=self.CLR_STATUS_OK).pack(
            side='left', padx=(0, 20), pady=10)
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
        self._create_connection_panel(scroll_frame, 1)
        self._create_catalogue_panel(scroll_frame, 2)
        self._create_read_panel(scroll_frame, 3)
        self._create_export_panel(scroll_frame, 4)
        self._create_channel_panel(scroll_frame, 5)
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
                  text=(f"Cryocon Model 34 | Master Sensor Table indices "
                        f"{MIN_TABLE_INDEX}-{MAX_TABLE_INDEX}, "
                        f"{MAX_CURVE_POINTS} points each\n"
                        "Slots are named by table index, which is what "
                        "CALCUR? and SENTYPE? both take."),
                  background=self.CLR_FRAME_BG, justify='left').grid(
            row=2, column=0, columnspan=2, padx=10, pady=(0, 4), sticky='w')
        ttk.Label(frame,
                  text=("This module only ever asks questions. It sends no\n"
                        "CALCUR, SENTYPE set, SENIX set, loop, setpoint,\n"
                        "PID, heater, STOP or *RST -- only the query forms.\n"
                        "Safe to open on a running cryostat."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  foreground=self.CLR_STATUS_OK, justify='left').grid(
            row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='w')

    def _create_connection_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 1  ·  Connect')
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
                               padx=10, pady=(0, 8))

    def _create_catalogue_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Step 2  ·  List what the instrument holds')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text=("Three SENTYPE? queries per index: the name, the sensor\n"
                  "type and the multiplier of every Master Sensor Table\n"
                  "entry. It writes nothing. The curve points themselves\n"
                  "are not read here; read the one slot that matters in\n"
                  "step 3. The list also says where the user block starts\n"
                  "on this unit, which is not what the manual says."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(6, 4))

        self.catalogue_btn = ttk.Button(
            frame, text="List the Master Sensor Table",
            command=self._scan_catalogue)
        self.catalogue_btn.grid(row=1, column=0, sticky='ew',
                                padx=10, pady=(0, 6))

        self.types_btn = ttk.Button(
            frame, text="What sensor types does this instrument use?",
            command=self._query_types)
        self.types_btn.grid(row=2, column=0, sticky='ew',
                            padx=10, pady=(0, 4))

        ttk.Label(
            frame,
            text=("The manual prints two lists of sensor-type names and\n"
                  "this firmware uses neither as printed: it spells a\n"
                  "diode 'SIDiode', and 'ACR' appears nowhere on it. The\n"
                  "button above reads the names off the instrument itself\n"
                  "so the loader can be given a spelling that lands\n"
                  "instead of one that is silently discarded."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=3, column=0, sticky='w',
                                 padx=10, pady=(0, 4))

        self.catalogue_label = ttk.Label(
            frame, text="Not listed yet.", font=('Segoe UI', 9, 'italic'),
            background=self.CLR_FRAME_BG, foreground=self.CLR_STATUS_WARN,
            wraplength=480, justify='left')
        self.catalogue_label.grid(row=4, column=0, sticky='w',
                                  padx=10, pady=(0, 8))

    def _create_read_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 3  ·  Read one slot')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Table index:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.slot_cb = ttk.Combobox(frame, font=self.FONT_BASE,
                                    state='readonly', width=44)
        self.slot_cb['values'] = self._slot_choices()
        self.slot_cb.current(0)
        self.slot_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        self.read_btn = ttk.Button(
            frame, text="Read this slot's curve", style='Read.TButton',
            command=self._read_slot)
        self.read_btn.grid(row=1, column=0, columnspan=2, sticky='ew',
                           padx=10, pady=(4, 4))

        self.progress = ttk.Progressbar(frame, mode='determinate')
        self.progress.grid(row=2, column=0, columnspan=2, sticky='ew',
                           padx=10, pady=(0, 4))

        # Says in words where the read is and, above all, when it is over.
        self.read_status_label = ttk.Label(
            frame, text="No curve read yet.", background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9, 'bold'), wraplength=300, justify='left')
        self.read_status_label.grid(row=3, column=0, columnspan=2,
                                    sticky='w', padx=10, pady=(0, 4))

        self.stop_btn = ttk.Button(frame, text="Stop the slot list or the read",
                                   state='disabled',
                                   command=self._request_stop)
        self.stop_btn.grid(row=4, column=0, columnspan=2, sticky='ew',
                           padx=10, pady=(0, 4))

        ttk.Label(
            frame,
            text=("One CALCUR? query. On the Rev 3.03A unit here it takes\n"
                  "anything from half a second to about twelve while the\n"
                  "instrument walks its flash; the window stays responsive\n"
                  "throughout. The stop button ends the slot list of step 2,\n"
                  "or this read between two lines; a read stopped part-way\n"
                  "is shown as raw text and never as a curve.\n"
                  "The curve is checked as soon as it arrives; the findings\n"
                  "are on the Checks tab and in the console."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=5, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 8))

    def _create_export_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 4  ·  Export')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Button(frame, text="Save this curve as a Cryo-con .crv file",
                   command=self._export_crv).grid(
            row=0, column=0, sticky='ew', padx=10, pady=(8, 4))
        ttk.Button(frame, text="Save this curve as a Lake Shore .340 file",
                   command=self._export_340).grid(
            row=1, column=0, sticky='ew', padx=10, pady=(0, 4))
        ttk.Button(frame, text="Save this curve as CSV",
                   command=self._export_curve_csv).grid(
            row=2, column=0, sticky='ew', padx=10, pady=(0, 4))
        ttk.Button(frame, text="Save the slot list as CSV",
                   command=self._export_catalogue_csv).grid(
            row=3, column=0, sticky='ew', padx=10, pady=(0, 4))

        ttk.Label(
            frame,
            text=("Every file here carries the numerals the instrument\n"
                  "printed, not this module's rounding of them, so it is\n"
                  "what the instrument holds. The .crv goes back into a\n"
                  "Cryo-con, through its utility software or the Sensor\n"
                  "Curve Loader here. The .340 is a Lake Shore breakpoint\n"
                  "table: it loads on a 340 or 350, it is the format the\n"
                  "rest of this lab's curves are kept in, and sending it\n"
                  "back through the Cryo-con loader is a round trip that\n"
                  "proves the transfer. The CSV is for plotting."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=4, column=0, sticky='w',
                                 padx=10, pady=(0, 8))

    def _create_channel_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Which sensor is each input using?')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text=("SENIX is the one that answers here, and it gives the\n"
                  "Master Sensor Table index directly. ISENIX and USENIX\n"
                  "are asked once, on input A, because a sibling module\n"
                  "uses them; on this firmware they do not exist, and once\n"
                  "they have failed they are not asked again. Each timeout\n"
                  "costs about ten seconds."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(6, 4))
        ttk.Button(frame, text="Ask the four inputs (read only)",
                   command=self._read_channels).grid(
            row=1, column=0, sticky='ew', padx=10, pady=(0, 4))
        self.channel_label = ttk.Label(
            frame, text="Not asked yet.", background=self.CLR_FRAME_BG,
            font=('Consolas', 9), justify='left', wraplength=480)
        self.channel_label.grid(row=2, column=0, sticky='w',
                                padx=10, pady=(0, 8))

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

        summary = ttk.LabelFrame(panel, text='The slot on screen')
        summary.grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        summary.grid_columnconfigure(0, weight=1)

        self.headline_label = ttk.Label(
            summary, text="Nothing read yet.", font=self.FONT_HEADLINE,
            background=self.CLR_FRAME_BG, wraplength=900, justify='left')
        self.headline_label.grid(row=0, column=0, sticky='w',
                                 padx=12, pady=(10, 4))
        self.detail_label = ttk.Label(
            summary, text="Connect, list the table, then read one slot.",
            background=self.CLR_FRAME_BG, wraplength=900, justify='left')
        self.detail_label.grid(row=1, column=0, sticky='w',
                               padx=12, pady=(0, 4))
        self.problem_label = ttk.Label(
            summary, text="", background=self.CLR_FRAME_BG,
            wraplength=900, justify='left', foreground=self.CLR_STATUS_WARN)
        self.problem_label.grid(row=2, column=0, sticky='w',
                                padx=12, pady=(0, 10))

        # The notebook keeps the slot list, the curve and the instrument's
        # raw reply side by side without any of them crowding the others.
        self.right_tabs = ttk.Notebook(panel)
        self.right_tabs.grid(row=1, column=0, rowspan=2, sticky='nsew',
                             padx=5, pady=5)

        curve_tab = ttk.Frame(self.right_tabs)
        self.right_tabs.add(curve_tab, text='  The curve  ')
        curve_tab.grid_columnconfigure(0, weight=1)
        curve_tab.grid_rowconfigure(0, weight=1)
        curve_tab.grid_rowconfigure(1, weight=1)

        plot_frame = ttk.LabelFrame(curve_tab, text='The curve, plotted')
        plot_frame.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
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
                      "drawn.\nThe table below shows every point that was "
                      "read."),
                background=self.CLR_FRAME_BG, justify='left').grid(
                row=0, column=0, padx=15, pady=15, sticky='w')

        table_frame = ttk.LabelFrame(
            curve_tab,
            text='Every point, in the order the instrument stores them')
        table_frame.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        columns = ('n', 'reading', 'temperature', 'ohms')
        self.table = ttk.Treeview(table_frame, columns=columns,
                                  show='headings', height=8)
        for column, heading, width in (
                ('n', '#', 60),
                ('reading', 'Sensor reading (stored first)', 240),
                ('temperature', 'Temperature / K (stored second)', 240),
                ('ohms', 'Resistance / ohm', 180)):
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, anchor='center')
        self.table.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        table_scroll = ttk.Scrollbar(table_frame, orient='vertical',
                                     command=self.table.yview)
        self.table.configure(yscrollcommand=table_scroll.set)
        table_scroll.grid(row=0, column=1, sticky='ns')

        list_tab = ttk.Frame(self.right_tabs)
        self.right_tabs.add(list_tab, text='  Every slot  ')
        list_tab.grid_columnconfigure(0, weight=1)
        list_tab.grid_rowconfigure(0, weight=1)
        columns = ('index', 'name', 'type', 'multiplier', 'looks')
        self.catalogue_table = ttk.Treeview(list_tab, columns=columns,
                                            show='headings')
        for column, heading, width in (
                ('index', 'Index', 70),
                ('name', 'Name', 240),
                ('type', 'Sensor type', 180),
                ('multiplier', 'Multiplier', 120),
                ('looks', 'Looks like', 120)):
            self.catalogue_table.heading(column, text=heading)
            self.catalogue_table.column(column, width=width, anchor='center')
        self.catalogue_table.column('name', anchor='w')
        self.catalogue_table.grid(row=0, column=0, sticky='nsew',
                                  padx=5, pady=5)
        catalogue_scroll = ttk.Scrollbar(list_tab, orient='vertical',
                                         command=self.catalogue_table.yview)
        self.catalogue_table.configure(yscrollcommand=catalogue_scroll.set)
        catalogue_scroll.grid(row=0, column=1, sticky='ns')
        # Double-clicking a row is the obvious way to say 'read that one'.
        self.catalogue_table.bind('<Double-1>', self._catalogue_double_click)
        self.catalogue_table.tag_configure('empty', foreground='#8A8177')

        checks_tab = ttk.Frame(self.right_tabs)
        self.right_tabs.add(checks_tab, text='  Checks  ')
        checks_tab.grid_columnconfigure(0, weight=1)
        checks_tab.grid_rowconfigure(0, weight=1)
        self.checks_view = scrolledtext.ScrolledText(
            checks_tab, state='disabled', bg=self.CLR_GRAPH_BG,
            fg=self.CLR_TEXT_DARK, font=self.FONT_CONSOLE, wrap='word',
            borderwidth=0)
        self.checks_view.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)

        types_tab = ttk.Frame(self.right_tabs)
        self.right_tabs.add(types_tab, text='  Sensor types  ')
        types_tab.grid_columnconfigure(0, weight=1)
        types_tab.grid_rowconfigure(0, weight=1)
        self.types_view = scrolledtext.ScrolledText(
            types_tab, state='disabled', bg=self.CLR_GRAPH_BG,
            fg=self.CLR_TEXT_DARK, font=self.FONT_CONSOLE, wrap='none',
            borderwidth=0)
        self.types_view.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)

        raw_tab = ttk.Frame(self.right_tabs)
        self.right_tabs.add(raw_tab, text='  What the instrument said  ')
        raw_tab.grid_columnconfigure(0, weight=1)
        raw_tab.grid_rowconfigure(0, weight=1)
        self.raw_view = scrolledtext.ScrolledText(
            raw_tab, state='disabled', bg=self.CLR_GRAPH_BG,
            fg=self.CLR_TEXT_DARK, font=self.FONT_CONSOLE, wrap='none',
            borderwidth=0)
        self.raw_view.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)

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

        One bad event must never stop the pump: an exception escaping here
        would freeze the console and the busy flag for the rest of the
        session.
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
                print(f"Curve viewer event {event[0]!r} failed: {exc}")

        if reschedule and not self._closing:
            try:
                self._poll_id = self.root.after(EVENT_POLL_MS,
                                                self._drain_events)
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
        elif kind == 'read_status':
            self.read_status_label.config(text=event[1])
        elif kind == 'catalogue':
            self._show_catalogue(event[1])
        elif kind == 'types':
            self._show_types(event[1], event[2])
        elif kind == 'curve':
            self._show_curve(event[1], event[2], event[3], event[4])
        elif kind == 'channels':
            self._show_channels(event[1])
        elif kind == 'dialog':
            _, level, title, text = event
            {'info': messagebox.showinfo,
             'warning': messagebox.showwarning,
             'error': messagebox.showerror}[level](title, text)

    def _describe_starting_point(self):
        self.log(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION} ready.")
        self.log("This module is passive: every command it can send is a "
                 "query, and the link refuses anything without a '?'. "
                 "Nothing on the instrument changes.")
        self.log("To install or replace a curve, use the Sensor Curve "
                 "Loader instead. This window cannot write one.")
        if not PYVISA_AVAILABLE:
            self.log("PyVISA is not installed, so nothing can be read. The "
                     "file writers still work on data already loaded.")
        if not MATPLOTLIB_AVAILABLE:
            self.log("Matplotlib is not installed, so curves are shown as a "
                     "table only.")

    @staticmethod
    def _slot_choices():
        return [f"{index:2d}" for index in
                range(MIN_TABLE_INDEX, MAX_TABLE_INDEX + 1)]

    def _selected_slot(self):
        raw = self.slot_cb.get().strip()
        match = re.match(r'\s*(\d+)', raw)
        if not match:
            return None
        value = int(match.group(1))
        return value if MIN_TABLE_INDEX <= value <= MAX_TABLE_INDEX else None

    def _require_connection(self):
        if not self.is_connected or not self.backend.is_connected:
            self.log("Not connected to the instrument.")
            messagebox.showerror("Not Connected",
                                 "Connect to the Cryocon first (step 1).")
            return False
        return True

    def _set_busy(self, busy):
        self.busy = busy
        state = 'disabled' if busy else 'normal'
        for widget_name in ('read_btn', 'catalogue_btn'):
            try:
                getattr(self, widget_name).config(state=state)
            except Exception:
                pass
        try:
            self.stop_btn.config(state='normal' if busy else 'disabled')
        except Exception:
            pass

    def _request_stop(self):
        self._stop_flag.set()
        self.log("Stop requested. The slot list, or the curve read, will end "
                 "after the line that is already in flight.")
        try:
            self.read_status_label.config(text="Stopping after the line in "
                                               "flight...")
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # CONNECTION
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
            self.log("Read-only session. Only queries will be sent.")
            self.status_label.config(text="● Connected (read only)",
                                     foreground=self.CLR_STATUS_OK)
            self.connect_btn.config(state='disabled')
            self.disconnect_btn.config(state='normal')
            self.visa_cb.config(state='disabled')
        except Exception as exc:
            self.log(f"CONNECT ERROR: {traceback.format_exc()}")
            messagebox.showerror("Connection Failed",
                                 f"Could not connect to {address}:\n{exc}")

    def _do_disconnect(self):
        if self.busy:
            self.log("Not disconnecting: a read is still running. Press "
                     "Stop, or wait for it to finish.")
            return
        self.log("Disconnecting...")
        self.backend.disconnect()
        self.is_connected = False
        self.log("Disconnected. Nothing was changed on the instrument.")
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

        A CALCUR? read can be twelve seconds on this firmware and a scan is
        about a hundred queries; done on the main thread the window would
        look frozen for the whole of it.
        """
        if self.busy:
            self.log("Another instrument job is still running.")
            return
        self._stop_flag.clear()
        # Set directly, not through the queue: this runs on the Tk thread,
        # and a second click inside the 50 ms before the queue was drained
        # used to start a second worker on the same VISA session.
        self._set_busy(True)

        def worker():
            try:
                function()
            except ReadOnlyViolation as exc:
                # This is a bug in this module, not an instrument fault, and
                # it means nothing was transmitted. Say so plainly.
                self.log(f"READ-ONLY GUARD TRIPPED: {exc}")
                self._post('dialog', 'error', "Read-only guard",
                           f"{exc}\n\nNothing was sent to the instrument. "
                           "This is a fault in the module; please report it.")
            except Exception as exc:
                self.log(f"{description} failed: {type(exc).__name__}: {exc}")
                self.log(traceback.format_exc())
            finally:
                self._post('busy', False)

        # Kept, so the window can wait for it rather than destroy the Tk
        # interpreter out from under it.
        self._worker = threading.Thread(target=worker, daemon=True,
                                        name=f"cc34-viewer-{description}")
        self._worker.start()

    # -----------------------------------------------------------------------
    # STEP 2: THE SLOT LIST
    # -----------------------------------------------------------------------

    def _scan_catalogue(self):
        if not self._require_connection():
            return

        def job():
            self.log(f"Listing Master Sensor Table indices "
                     f"{MIN_TABLE_INDEX} to {MAX_TABLE_INDEX} "
                     "(SENTYPE? only, read only)...")

            def progress(done, total, entry):
                self._post('progress', done, total)

            entries = self.backend.scan_sensor_table(
                progress=progress, should_stop=self._stop_flag.is_set)
            named = [e for e in entries
                     if e.get('name') and not looks_like_empty_slot(e['name'])]
            self.log(f"  {len(entries)} indices read; {len(named)} carry a "
                     "name.")
            for entry in entries:
                name = entry.get('name')
                if name is None:
                    self.log(f"    index {entry['index']:2d}  no answer")
                    continue
                self.log(f"    index {entry['index']:2d}  {name:<18s} "
                         f"type {entry.get('type') or '?':<10s} "
                         f"mult {entry.get('multiplier') or '?'}")
            self.log("")
            self.log("WHERE THE USER CURVES ARE ON THIS UNIT")
            for line in table_geometry_report(entries)[0]:
                self.log(f"  {line}")
            self._post('catalogue', entries)

        self._run_in_worker("Listing the sensor table", job)

    # -----------------------------------------------------------------------
    # WHAT TYPES DOES THIS INSTRUMENT USE?
    # -----------------------------------------------------------------------

    def _query_types(self):
        """Ask the instrument what sensor-type names it uses. Read only.

        There is no command that returns a list of legal types, and the
        manual's two printed lists are both wrong for this firmware, so the
        list is assembled from evidence: every SENTYPE? reply in the Master
        Sensor Table is a type name this instrument uses, in its own
        spelling. Twenty-seven entries is a decent sample and it costs
        nothing but queries.

        A scan already taken is reused rather than repeated. Pressing this
        without one takes a fresh scan first, so the button works on its own.
        """
        if not self._require_connection():
            return

        def job():
            entries = self.catalogue
            if entries:
                self.log("Reading the sensor types out of the slot list "
                         "already taken. Nothing is sent.")
            else:
                self.log("No slot list yet, so one is being taken first "
                         "(SENTYPE? only, read only)...")

                def progress(done, total, entry):
                    self._post('progress', done, total)

                entries = self.backend.scan_sensor_table(
                    progress=progress, should_stop=self._stop_flag.is_set)
                self._post('catalogue', entries)
            lines = type_vocabulary_report(entries)
            self.log("")
            self.log("SENSOR TYPES THIS INSTRUMENT USES")
            for line in lines:
                self.log(f"  {line}" if line else "")
            geometry, _ = table_geometry_report(entries)
            self._post('types', lines, geometry)

        self._run_in_worker("Asking what sensor types this instrument uses",
                            job)

    def _show_types(self, lines, geometry):
        """Put the type report on screen, in the Checks tab."""
        text = "\n".join(lines)
        if geometry:
            text += ("\n\nWHERE THE USER CURVES ARE ON THIS UNIT\n"
                     + "\n".join(geometry))
        self.types_view.config(state='normal')
        self.types_view.delete('1.0', 'end')
        self.types_view.insert('1.0', text)
        self.types_view.config(state='disabled')
        self.right_tabs.select(self.TYPES_TAB)

    def _show_catalogue(self, entries):
        self.catalogue = entries
        # Which block an index is in is arithmetic off the placeholder names,
        # not a guess from the name in front of us, so it is worked out once
        # for the whole table and the name markers are only the fallback. The
        # two disagreed on this instrument -- 'TC K Extern' and 'Cryocal D3'
        # sit in the factory block and read as 'user' by name alone -- and
        # when they disagree the arithmetic is the one that is checkable.
        self.blocks = map_table_blocks(entries)
        offset = self.blocks.get('offset')
        for row in self.catalogue_table.get_children():
            self.catalogue_table.delete(row)
        named = 0
        for entry in entries:
            name = entry.get('name')
            index = entry.get('index')
            if name is None:
                values = (index, '<no answer>', '', '', '')
                tags = ('empty',)
                self.catalogue_table.insert('', 'end', values=values,
                                            tags=tags)
                continue

            if offset is None:
                block = 'factory' if looks_like_factory_entry(name) else 'user'
            elif index <= self.blocks['factory_last']:
                block = 'factory'
            elif index <= self.blocks['user_last']:
                block = f"user {index - offset}"
            else:
                block = 'beyond'

            if looks_like_empty_slot(name) or user_slot_number(name):
                looks = f"{block}, empty" if offset is not None else 'empty'
                tags = ('empty',)
            else:
                named += 1
                looks = block
                tags = ()
            self.catalogue_table.insert(
                '', 'end',
                values=(index, name, entry.get('type') or '',
                        entry.get('multiplier') or '', looks), tags=tags)

        summary = (f"{named} of {len(entries)} indices hold something. "
                   "Double-click a row to read its curve.")
        if offset is not None:
            summary += (f"  User curves 1-12 are indices "
                        f"{self.blocks['user_first']}-"
                        f"{self.blocks['user_last']}.")
        self.catalogue_label.config(
            text=summary,
            foreground=self.CLR_STATUS_OK if named else self.CLR_STATUS_WARN)
        self.right_tabs.select(self.LIST_TAB)

    def _catalogue_double_click(self, _event):
        selection = self.catalogue_table.selection()
        if not selection:
            return
        values = self.catalogue_table.item(selection[0], 'values')
        try:
            index = int(values[0])
        except (IndexError, ValueError):
            return
        self.slot_cb.current(index - MIN_TABLE_INDEX)
        self._read_slot()

    # -----------------------------------------------------------------------
    # STEP 3: READ ONE SLOT
    # -----------------------------------------------------------------------

    def _read_slot(self):
        if not self._require_connection():
            return
        index = self._selected_slot()
        if index is None:
            messagebox.showerror("No Slot",
                                 "Choose a table index first (step 3).")
            return

        def job():
            self.log(f"Reading slot {index} with CALCUR? (read only). This "
                     "can take up to about twelve seconds on this firmware...")
            started = time.time()

            self._post('read_status', f"Waiting for CALCUR? {index} to "
                                      "answer (up to about twelve seconds)...")

            def progress(done, total):
                self._post('progress', done, total)
                self._post('read_status',
                           f"Receiving line {done} of up to {total}...")

            header, points, raw = self.backend.read_slot_curve(
                index, progress=progress, should_stop=self._stop_flag.is_set)
            if self._stop_flag.is_set():
                self.log(f"  Stopped by you after {len(raw.splitlines())} "
                         "line(s). What came back is on the 'What the "
                         "instrument said' tab; it is not a curve.")
            elif not raw.strip():
                # An empty slot and a reply that came too late look the
                # same from here. Ask once more before calling it empty.
                self.log(f"  Nothing came back in {time.time() - started:.1f}"
                         " s. A slow reply and an empty slot look the same, "
                         "so asking once more...")
                header, points, raw = self.backend.read_slot_curve(
                    index, progress=progress,
                    should_stop=self._stop_flag.is_set)
            elapsed = time.time() - started
            lines = len(raw.splitlines())
            self.log(f"  Reply in {elapsed:.1f} s, {lines} line(s). The "
                     "read is finished.")
            # Fill the bar: its total was the line ceiling, and a shorter
            # curve used to leave it part way across, looking unfinished.
            self._post('progress', max(lines, 1), max(lines, 1))
            self._post('read_status',
                       f"Done: {lines} line(s) received in {elapsed:.1f} s.")
            if header is None:
                self.log(f"  Slot {index} did not answer with a curve. The "
                         "reply is on the 'What the instrument said' tab.")
                self._post('curve', index, None, [], raw)
                return
            self.log(f"  Name '{header['name']}', type "
                     f"'{header['sensor_type']}', multiplier "
                     f"{header['multiplier']}, units {header['units']}, "
                     f"{len(points)} points.")
            self._post('curve', index, header, points, raw)

        self._run_in_worker("Reading the curve", job)

    def _show_curve(self, index, header, points, raw):
        self.slot_index = index
        self.header = header
        self.points = list(points or [])
        self.raw_text = raw or ""

        self.raw_view.config(state='normal')
        self.raw_view.delete('1.0', 'end')
        self.raw_view.insert('1.0', self.raw_text or "(no reply)")
        self.raw_view.config(state='disabled')

        for row in self.table.get_children():
            self.table.delete(row)

        if header is None and not self.raw_text.strip():
            # Said apart from a garbled reply: nothing at all came back,
            # twice, which is an empty slot OR an instrument that did not
            # answer in time, and the screen must not pick one.
            self.headline_label.config(
                text=f"Slot {index} did not answer.")
            self.detail_label.config(
                text=("Nothing came back to CALCUR? within the wait, asked "
                      "twice. Either the slot is empty, or the instrument "
                      "was still busy and answered too late. The name the "
                      "slot list (step 2) shows for this index says which "
                      "is likelier; read it once more before calling it "
                      "empty."))
            self.problem_label.config(text="")
            # The Checks tab still held the LAST slot's findings here, so a
            # read that came back with nothing left a clean bill of health
            # on screen for a slot that had not been read at all. Whatever
            # is on that tab must always be about the slot named on it.
            self._clear_checks(index, "nothing readable came back from it")
            self._draw_plot(None, [])
            self.right_tabs.select(self.CURVE_TAB)
            return
        if header is None:
            self.headline_label.config(
                text=f"Slot {index} holds no readable curve.")
            self.detail_label.config(
                text=("The instrument answered, but the reply is not the "
                      "four header lines, points and semicolon a Cryo-con "
                      "curve is made of. That normally means the slot is "
                      "empty, or that the read was stopped part-way. The "
                      "whole reply is on the 'What the instrument said' "
                      "tab."))
            self.problem_label.config(text="")
            # The Checks tab still held the LAST slot's findings here, so a
            # read that came back with nothing left a clean bill of health
            # on screen for a slot that had not been read at all. Whatever
            # is on that tab must always be about the slot named on it.
            self._clear_checks(index, "nothing readable came back from it")
            self._draw_plot(None, [])
            self.right_tabs.select(self.CURVE_TAB)
            return

        if not points:
            # A header and a semicolon, nothing between them. This is what an
            # untouched user slot holds, and saying so plainly is the whole
            # point: "no readable curve" reads like a fault, and an empty
            # slot is not one -- it is a slot you can write to.
            self.headline_label.config(
                text=f"Slot {index} is empty.")
            self.detail_label.config(
                text=(f"The slot answered with a header and nothing else: "
                      f"name '{header['name']}', type "
                      f"'{header['sensor_type']}', multiplier "
                      f"{header['multiplier']}, units "
                      f"{header.get('units', '')}. That is the placeholder "
                      "this firmware keeps in an untouched user slot, not a "
                      "curve. There are no breakpoints here, so nothing "
                      "would be lost by writing a curve into it."))
            problems, notes = audit_curve(index, header, [], self.catalogue)
            self.problem_label.config(text="\n".join(problems))
            self._show_checks(index, header, problems, notes)
            self._draw_plot(header, [])
            self.right_tabs.select(self.CURVE_TAB)
            return

        units = header.get('units', '')
        for number, (reading, temperature) in enumerate(points, start=1):
            ohms = reading_in_ohms(reading, units)
            self.table.insert('', 'end',
                              values=(number, fmt6(reading),
                                      fmt6(temperature),
                                      fmt6(ohms) if ohms is not None else "-"))

        stats = curve_statistics(points, units)
        self.headline_label.config(
            text=(f"Slot {index}  ·  {header['name']}  ·  "
                  f"{stats.get('count', 0)} points"))
        if stats:
            self.detail_label.config(
                text=(f"Sensor type '{header['sensor_type']}', multiplier "
                      f"{header['multiplier']}, curve units "
                      f"{units}. Temperature runs "
                      f"{fmt6(stats['temp_min'])} K to "
                      f"{fmt6(stats['temp_max'])} K; the sensor column runs "
                      f"{fmt6(stats['reading_min'])} to "
                      f"{fmt6(stats['reading_max'])} {units}. "
                      f"Temperature is {stats['temperature_direction']}."))
        else:
            self.detail_label.config(
                text="The header parsed but no points came back.")

        # Every check lives in audit_curve() now, so the same set runs here,
        # in the console, and in the self-test, instead of this screen having
        # its own private opinion of what counts as wrong.
        problems, notes = audit_curve(index, header, points, self.catalogue)
        self.problem_label.config(text="\n".join(problems))
        self._show_checks(index, header, problems, notes)

        self._draw_plot(header, points)
        self.right_tabs.select(self.CURVE_TAB)

    def _clear_checks(self, index, because):
        """Say the Checks tab has nothing to report, and why.

        A blank tab and a tab holding somebody else's findings look the same
        to a reader in a hurry, so this writes a sentence rather than
        clearing it.
        """
        self.checks_view.config(state='normal')
        self.checks_view.delete('1.0', 'end')
        self.checks_view.insert(
            '1.0',
            f"No checks were run on slot {index}: {because}.\n\n"
            "Nothing on this tab refers to any other slot. The raw reply is "
            "on the 'What the instrument said' tab.")
        self.checks_view.config(state='disabled')

    def _show_checks(self, index, header, problems, notes):
        """Write the audit of the curve on screen into the Checks tab.

        The findings also go to the console, because the console is what gets
        kept and pasted into a log when something has gone wrong, and a
        finding that only ever existed on a tab is one nobody can show anyone
        else.
        """
        header = header or {}
        lines = [f"CHECKS ON SLOT {index}  -  '"
                 f"{str(header.get('name', '')).strip()}'",
                 ""]
        if problems:
            lines.append(f"{len(problems)} PROBLEM(S)")
            for number, text in enumerate(problems, start=1):
                lines.append(f"  {number}. {text}")
        else:
            lines.append("No problems found. Every check below passed:")
            lines.append("  - the sensor type and the curve units are the "
                         "same kind of measurement")
            lines.append("  - the curve fits the full scale of that input")
            lines.append("  - the sign of the multiplier matches the way "
                         "temperature actually moves through the points")
            lines.append("  - temperature is monotonic and the readings are "
                         "strictly ascending, so interpolation is single "
                         "valued")
            lines.append("  - the name, the point count and the units are "
                         "within what the instrument keeps")
        if notes:
            lines.append("")
            lines.append("WORTH KNOWING")
            for text in notes:
                lines.append(f"  - {text}")
        lines.append("")
        lines.append("These are checks on what the INSTRUMENT holds, read "
                     "back off it. They do not depend on the file that was "
                     "sent, which is the point: a header field this firmware "
                     "cannot identify is replaced with a default rather than "
                     "reported, so a curve can arrive complete and still be "
                     "stored against the wrong input.")

        text = "\n".join(lines)
        self.checks_view.config(state='normal')
        self.checks_view.delete('1.0', 'end')
        self.checks_view.insert('1.0', text)
        self.checks_view.config(state='disabled')

        for line in lines:
            self.log(f"  {line}" if line else "")

    def _draw_plot(self, header, points):
        if not MATPLOTLIB_AVAILABLE or self.figure is None:
            return
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.set_facecolor(self.CLR_GRAPH_BG)
        if points:
            temperatures = [pair[1] for pair in points]
            readings = [pair[0] for pair in points]
            axes.plot(temperatures, readings, marker='o', markersize=3,
                      linewidth=1.2, color='#8A5A44')
            axes.set_xlabel("Temperature (K)")
            axes.set_ylabel(f"Sensor reading "
                            f"({header.get('units', '?')})")
            axes.set_title(f"Slot {self.slot_index}  ·  "
                           f"{header.get('name')}")
            # A resistance curve spans decades; a log x-axis is the only way
            # the low-temperature end is visible at all. Only used when every
            # temperature is positive, so nothing is silently dropped.
            if min(temperatures) > 0 and max(temperatures) / min(
                    temperatures) > 50:
                axes.set_xscale('log')
            axes.grid(True, which='both', alpha=0.3)
        else:
            axes.text(0.5, 0.5, "no curve", ha='center', va='center',
                      transform=axes.transAxes, color='#8A8177')
            axes.set_xticks([])
            axes.set_yticks([])
        self.figure.tight_layout()
        self.plot_canvas.draw()

    # -----------------------------------------------------------------------
    # CHANNELS
    # -----------------------------------------------------------------------

    def _read_channels(self):
        if not self._require_connection():
            return

        def job():
            self.log("Asking each input which sensor it uses (read only)...")
            answers = self.backend.read_channel_sensors()
            for channel, entry in answers.items():
                self.log(f"  Input {channel}: SENIX {entry['SENIX']}  "
                         f"ISENIX {entry['ISENIX']}  "
                         f"USENIX {entry['USENIX']}  "
                         f"reads {entry['reading']}")
            self._post('channels', answers)

        self._run_in_worker("Reading the channel assignments", job)

    def _show_channels(self, answers):
        lines = []
        # The slot list, where one has been run, turns a sensor index into
        # the name of the curve behind it. The Lake Shore viewer does the
        # same with INCRV?; a bare number is not much use to anyone.
        names = {}
        for entry in self.catalogue or ():
            try:
                names[int(entry.get('index'))] = entry.get('name')
            except (TypeError, ValueError):
                continue
        dead = set()
        for channel in INPUT_CHANNELS:
            entry = answers.get(channel, {})
            row = f"{channel}: SENIX {str(entry.get('SENIX', '?')):>6s}"
            for key in ('ISENIX', 'USENIX'):
                value = str(entry.get(key, '?'))
                if value.startswith('<'):
                    dead.add(key)
                else:
                    row += f"  {key} {value:>6s}"
            lines.append(row)
            named = []
            for key in ('SENIX', 'ISENIX', 'USENIX'):
                try:
                    number = int(float(str(entry.get(key, '')).strip()))
                except (TypeError, ValueError):
                    continue
                if number in names and names[number]:
                    named.append(f"{key} {number} = '{names[number]}'")
            if named:
                lines.append("   " + "; ".join(dict.fromkeys(named)))
            elif names:
                lines.append("   (no index above is in the slot list)")
            lines.append(f"   reads {entry.get('reading', '?')}")
        lines.append("")
        if dead:
            lines.append(f"{' and '.join(sorted(dead))} did not answer on")
            lines.append("this firmware, so SENIX is the only scheme and the")
            lines.append("numbers above are Master Sensor Table indices.")
            lines.append("")
        lines.append("A run of dashes is a sensor fault; a run of dots means")
        lines.append("the reading is off the end of the curve.")
        self.channel_label.config(text="\n".join(lines))

    # -----------------------------------------------------------------------
    # STEP 4: EXPORT
    # -----------------------------------------------------------------------

    def _have_curve(self):
        if not self.header or not self.points:
            messagebox.showerror(
                "Nothing To Save",
                "Read a slot that holds a curve first (step 3).")
            return False
        return True

    def _default_stem(self):
        header = self.header or {}
        name = re.sub(r'[^A-Za-z0-9_.-]+', '_',
                      str(header.get('name', 'curve')).strip()) or "curve"
        return f"CC34_slot{self.slot_index:02d}_{name}"

    def _write(self, path, text, description):
        try:
            # Checked before the file is opened, so a name the instrument
            # printed in a non-ASCII byte does not leave an empty file.
            text.encode('ascii')
            with open(path, 'w', encoding='ascii', newline='\n') as handle:
                handle.write(text)
        except Exception as exc:
            self.log(f"Could not write {path}: {type(exc).__name__}: {exc}")
            messagebox.showerror("Save Failed",
                                 f"Could not write {path}:\n{exc}")
            return False
        self.log(f"{description} written to {path}")
        return True

    def _export_crv(self):
        if not self._have_curve():
            return
        try:
            text = crv_file_text(build_crv_lines(self.header, self.points))
        except (ValueError, UnicodeEncodeError) as exc:
            self.log(f"Cannot write a .crv for this curve: {exc}")
            messagebox.showwarning("Cannot Write .crv", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="Save this curve as a Cryo-con .crv file",
            defaultextension=".crv",
            initialfile=f"{self._default_stem()}.crv",
            filetypes=[("Cryo-con curve", "*.crv"), ("All files", "*.*")])
        if not path:
            return
        self._write(path, text, "Cryo-con .crv file")

    def _export_340(self):
        if not self._have_curve():
            return
        try:
            text = build_lakeshore_340_text(
                self.header, self.points, index=self.slot_index,
                idn=self.backend.idn, address=self.backend.address)
        except (ValueError, UnicodeEncodeError) as exc:
            self.log(f"Cannot write a .340 for this curve: {exc}")
            messagebox.showwarning("Cannot Write .340", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="Save this curve as a Lake Shore .340 file",
            defaultextension=".340",
            initialfile=f"{self._default_stem()}.340",
            filetypes=[("Lake Shore breakpoint table", "*.340"),
                       ("All files", "*.*")])
        if not path:
            return
        self._write(path, text, "Lake Shore .340 file")

    def _export_curve_csv(self):
        if not self._have_curve():
            return
        path = filedialog.asksaveasfilename(
            title="Save this curve as CSV", defaultextension=".csv",
            initialfile=f"{self._default_stem()}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        text = build_curve_csv(self.slot_index, self.header, self.points,
                               idn=self.backend.idn,
                               address=self.backend.address)
        self._write(path, text, "Curve CSV")

    def _export_catalogue_csv(self):
        if not self.catalogue:
            messagebox.showerror("Nothing To Save",
                                 "List the sensor table first (step 2).")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="Save the slot list as CSV", defaultextension=".csv",
            initialfile=f"CC34_sensor_table_{stamp}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        text = build_catalogue_csv(self.catalogue, idn=self.backend.idn,
                                   address=self.backend.address)
        self._write(path, text, "Sensor table CSV")

    # -----------------------------------------------------------------------

    def _on_closing(self):
        # Nothing here can leave the instrument half-changed, because nothing
        # here changes it, so a running read is not a reason to refuse to
        # close. It is stopped, waited for, and the session is dropped.
        self._closing = True
        self._stop_flag.set()
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self._wait_for_worker()
        if self.is_connected:
            self.backend.disconnect()
        self.root.destroy()

    def _wait_for_worker(self, timeout_s=WORKER_JOIN_TIMEOUT_S):
        """Wait for the read to finish before Tk is torn down.

        The stop flag is only honoured between lines and a CALCUR? read is
        up to about twelve seconds on this firmware, so "stopped" and
        "finished"
        are far apart. Destroying the window in between is what freed the Tk
        interpreter on the worker thread.

        The queue is drained while waiting, without rescheduling, so the last
        lines the worker writes still reach the console.
        """
        worker = self._worker
        if worker is None or not worker.is_alive():
            return
        deadline = time.time() + timeout_s
        while worker.is_alive() and time.time() < deadline:
            worker.join(0.05)
            try:
                self._drain_events(reschedule=False)
                self.root.update_idletasks()
            except tk.TclError:
                break
        if worker.is_alive():
            print(f"Curve viewer: the '{worker.name}' thread is still "
                  f"running after {timeout_s:.0f} s; closing anyway.")


# ===============================================================================
# OFFLINE SELF-TEST
# ===============================================================================
#
# Everything here runs on made-up replies with no instrument, no VISA and no
# Tk, so it can be run on the measurement PC before a session:
#
#     python Sensor_Curve_Viewer_CC34_GUI.py --selftest
#
# Cases 1 and 2 are the ones that matter most: they are the proof that the
# read-only guard actually refuses a setting command, on both paths to the
# bus.

SAMPLE_BLOCK = ("X17680\n"
                "R8K10UA\n"
                "-1.0\n"
                "LOGOHM\n"
                "1.64523   325.0\n"
                "2.50000   100.0\n"
                "3.10000   4.0\n"
                ";")


def _selftest_cases():
    """Yield (name, callable) pairs. Each callable raises on failure."""

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    class FakeInstrument:
        """Records what reached it. Nothing here should ever record a write
        of a setting command."""

        def __init__(self, lines=()):
            self.written = []
            self.queried = []
            self.timeout = 1000
            self._lines = list(lines)

        def query(self, command):
            self.queried.append(command)
            return "Cryocon,34,204683,3.03A"

        def write(self, command):
            self.written.append(command)

        def read(self):
            if not self._lines:
                raise TimeoutError("no more lines")
            return self._lines.pop(0)

        def close(self):
            pass

    def make_link(lines=()):
        link = CryoconReadOnlyLink.__new__(CryoconReadOnlyLink)
        link.instrument = FakeInstrument(lines)
        link._last_io = 0.0
        link.commands_sent = 0
        return link

    # -- 1: the read-only guard admits queries and nothing else -------------
    def case_read_only_guard():
        for command in ('*IDN?', 'CALCUR? 15', 'SENTYPE? 15',
                        'SENTYPE 15:TYPE?', 'SENTYPE 15:MULTIPLY?',
                        'INPUT A:SENIX?', 'INPUT? A'):
            check(is_query(command), f"{command} should be allowed")
        for command in ('*RST', '*CLS', 'STOP', 'CALCUR 15',
                        'SENTYPE 15:NAME "X17680"', 'SENTYPE 15:TYPE ACR',
                        'INPUT A:SENIX 15', 'LOOP 1:SETPT 300',
                        'LOOP 1:RANGE HI', 'CONTROL',
                        # the shapes the old "'?' anywhere" test let through
                        'CALCUR? 15;CALCUR 15', 'CALCUR 15 ?', '*RST?',
                        'STOP?', 'SENTYPE 15:NAME "X"?', 'CALCUR?? 15',
                        'SENTYPE? 15; CONTROL', 'SENTYPE? 15\nSTOP',
                        'CALCUR? 15\r\nCALCUR 15'):
            check(not is_query(command), f"{command} must be refused")
        for command in ('INPUT A:ISENIX?', 'INPUT A:USENIX?',
                        'SENTYPE 15:NAME?', 'calcur? 15'):
            check(is_query(command), f"{command} should be allowed")

    # -- 2: both paths to the bus enforce it --------------------------------
    def case_link_refuses():
        link = make_link()
        for command in ('CALCUR 15', 'SENTYPE 15:TYPE ACR', '*RST'):
            for method in (link.ask, link.ask_block):
                try:
                    method(command)
                except ReadOnlyViolation:
                    continue
                raise AssertionError(
                    f"{method.__name__} accepted {command!r}")
        check(link.instrument.written == [],
              f"a refused command reached the bus: {link.instrument.written}")
        check(link.instrument.queried == [],
              f"a refused command reached the bus: {link.instrument.queried}")
        check(link.ask('*IDN?').startswith("Cryocon"), "a query was refused")

    # -- 3: a block read collects lines up to the semicolon -----------------
    def case_ask_block():
        lines = SAMPLE_BLOCK.split('\n')
        link = make_link(lines + ["should not be read"])
        text = link.ask_block("CALCUR? 15")
        check(link.instrument.written == ["CALCUR? 15"],
              link.instrument.written)
        check(text.strip().endswith(';'), text)
        check("should not be read" not in text,
              "reading did not stop at the semicolon")

    # -- 4: a CALCUR? reply is parsed, reading first ------------------------
    def case_parse_block():
        header, points = parse_calcur_block(SAMPLE_BLOCK, "slot 15")
        check(header['name'] == "X17680", header['name'])
        check(header['sensor_type'] == "R8K10UA", header['sensor_type'])
        check(header['multiplier'] == -1.0, header['multiplier'])
        check(header['units'] == "LOGOHM", header['units'])
        check(len(points) == 3, len(points))
        # Reading first, temperature second. Getting this backwards is the
        # single easiest mistake to make with a Cryo-con curve.
        check(points[0] == (1.64523, 325.0), points[0])
        check(header['point_texts'][0] == ("1.64523", "325.0"),
              header['point_texts'][0])

    # -- 5: an echoed command line is tolerated -----------------------------
    def case_parse_with_echo():
        header, points = parse_calcur_block("CALCUR? 15\n" + SAMPLE_BLOCK)
        check(header['name'] == "X17680", header['name'])
        check(len(points) == 3, len(points))

    # -- 6: a truncated reply is refused rather than half-shown -------------
    def case_parse_truncated():
        truncated = "\n".join(SAMPLE_BLOCK.split('\n')[:-1])
        try:
            parse_calcur_block(truncated, "slot 15")
        except CurveReadError as exc:
            check("semicolon" in str(exc), str(exc))
            return
        raise AssertionError("a block with no semicolon should be refused")

    # -- 7: unknown units are refused rather than assumed -------------------
    def case_parse_bad_units():
        bad = SAMPLE_BLOCK.replace("LOGOHM", "KELVIN")
        try:
            parse_calcur_block(bad, "slot 15")
        except CurveReadError as exc:
            check("units" in str(exc), str(exc))
            return
        raise AssertionError("KELVIN is not a Cryo-con curve unit")

    # -- 8: numbers are written in plain decimal ----------------------------
    def case_fmt6():
        check(fmt6(1.6452312) == "1.64523", fmt6(1.6452312))
        check(fmt6(325.0) == "325.0", fmt6(325.0))
        check(fmt6(-1.0) == "-1.0", fmt6(-1.0))
        check('e' not in fmt6(1.23e-7).lower(), fmt6(1.23e-7))
        check('.' in fmt6(4), fmt6(4))

    # -- 9: a .crv written here round-trips through the parser --------------
    def case_crv_round_trip():
        header, points = parse_calcur_block(SAMPLE_BLOCK, "slot 15")
        text = crv_file_text(build_crv_lines(header, points))
        again_header, again_points = parse_calcur_block(text, "the file")
        check(again_header['name'] == header['name'], again_header)
        check(again_header['units'] == header['units'], again_header)
        check(again_points == points, f"{again_points} != {points}")

    # -- 10: the .crv keeps the instrument's own numerals -------------------
    #  Not this module's re-rounding of them: what the instrument printed is
    #  the limit of what is known, and re-formatting would quietly invent
    #  digits or drop them.
    def case_crv_keeps_printed_digits():
        header, points = parse_calcur_block(
            "NAME\nACR\n-1.0\nOHMS\n1.2345678   300.0\n2.0   100.0\n;")
        text = crv_file_text(build_crv_lines(header, points))
        check("1.2345678" in text,
              f"the printed numeral was re-rounded:\n{text}")

    # -- 11: LOGOHM converts to ohms, VOLTS does not ------------------------
    def case_ohms():
        check(abs(reading_in_ohms(3.0, 'LOGOHM') - 1000.0) < 1e-9,
              reading_in_ohms(3.0, 'LOGOHM'))
        check(reading_in_ohms(1234.0, 'OHMS') == 1234.0,
              reading_in_ohms(1234.0, 'OHMS'))
        check(reading_in_ohms(1.5, 'VOLTS') is None,
              "volts have no resistance to report")

    # -- 12: empty and factory slot names -----------------------------------
    def case_slot_names():
        for name in ('', '  ', '.', 'NONE', 'none'):
            check(looks_like_empty_slot(name), f"{name!r} should read empty")
        check(not looks_like_empty_slot("X17680"), "X17680 is a real name")
        check(looks_like_factory_entry("Lakeshore 10"), "factory entry")
        check(not looks_like_factory_entry("X17680"), "user entry")

    # -- 13: statistics describe the curve, they do not judge it ------------
    def case_statistics():
        _, points = parse_calcur_block(SAMPLE_BLOCK)
        stats = curve_statistics(points, 'LOGOHM')
        check(stats['count'] == 3, stats)
        check(stats['temp_min'] == 4.0 and stats['temp_max'] == 325.0, stats)
        check(stats['readings_ascending'] is True, stats)
        check(stats['temperature_monotonic'] is True, stats)

    # -- 14: the CSV carries the header and one row per point ---------------
    def case_curve_csv():
        header, points = parse_calcur_block(SAMPLE_BLOCK)
        text = build_curve_csv(15, header, points, idn="Cryocon,34",
                               address="GPIB0::12")
        check("# Master Sensor Table index: 15" in text, text)
        check("Index,Reading_LOGOHM,Temperature_K,Resistance_ohm" in text,
              text)
        data_rows = [line for line in text.splitlines()
                     if line and not line.startswith('#')
                     and not line.startswith('Index')]
        check(len(data_rows) == 3, data_rows)
        check(data_rows[0].endswith("44.1804"),          # 10 ** 1.64523
              f"the ohms column looks wrong: {data_rows[0]}")

    # -- 15: the slot list CSV labels every slot it was given ---------------
    def case_catalogue_csv():
        entries = [{'index': 1, 'name': 'Lakeshore 10', 'type': 'SIDIODE',
                    'multiplier': '1.0'},
                   {'index': 15, 'name': 'X17680', 'type': 'R8K10UA',
                    'multiplier': '-1.0'},
                   {'index': 16, 'name': '', 'type': None,
                    'multiplier': None},
                   {'index': 17, 'name': None, 'type': None,
                    'multiplier': None}]
        text = build_catalogue_csv(entries)
        lines = [line for line in text.splitlines()
                 if line and not line.startswith('#')]
        check(len(lines) == 5, lines)          # header row plus four slots
        check(lines[1].endswith(",factory"), lines[1])
        check(lines[2].endswith(",user"), lines[2])
        check(lines[3].endswith(",empty"), lines[3])
        check(lines[4].endswith(",no answer"), lines[4])

    # -- 16: an empty user slot reads as empty, not as a fault --------------
    def case_parse_empty_slot():
        # What table index 18 answers on this firmware when nothing has been
        # written to it: four header lines and the terminator.
        empty = "User Sensor 4\nSiDiode\n-1.000000\nVolts\n;"
        header, points = parse_calcur_block(empty, "slot 18",
                                            allow_empty=True)
        check(points == [], points)
        check(header['name'] == "User Sensor 4", header['name'])
        check(header['units'] == "VOLTS", header['units'])
        # With the command echoed the line count clears the old threshold,
        # so the same slot used to parse or not depending on the interface.
        header, points = parse_calcur_block("CALCUR? 18\n" + empty,
                                            "slot 18", allow_empty=True)
        check(points == [] and header['name'] == "User Sensor 4",
              "the echo changed how an empty slot read")
        # A caller that has not asked for it still gets the refusal, so a
        # half-read curve is never shown as a whole one.
        try:
            parse_calcur_block(empty, "slot 18")
        except CurveReadError:
            pass
        else:
            raise AssertionError("allow_empty should be opt-in")

    # -- 17: the stored type is checked against the curve's units both ways --
    def case_type_against_units():
        # A diode type on a resistance curve: the firmware's silent
        # substitution, which this window already caught.
        check(type_unit_family('SiDiode') == 'V', 'SiDiode')
        check(type_unit_family('Diode') == 'V', 'Diode')
        check(type_unit_family('R8K10UA') == 'ohm', 'R8K10UA')
        check(type_unit_family('ACR') == 'ohm', 'ACR')
        check(type_unit_family('TC80') == 'V', 'TC80')
        # The mistake at the other end, which it did not: a DT-470 loaded on
        # top of the Cernox defaults gives a VOLTS curve on an 8 kohm range.
        check(UNITS_FAMILY['VOLTS'] == 'V', 'VOLTS')
        check(UNITS_FAMILY['LOGOHM'] == 'ohm', 'LOGOHM')
        check(type_unit_family('R8K10UA') != UNITS_FAMILY['VOLTS'],
              'a resistance type on a volts curve must not read as agreeing')
        check(type_unit_family('Diode') == UNITS_FAMILY['VOLTS'],
              'a diode type on a volts curve must read as agreeing')
        # An unknown name is reported as unchecked rather than assumed good.
        check(type_unit_family('WhatIsThis') is None, 'unknown type')
        check(type_unit_family('') is None, 'blank type')

    # -- the Master Sensor Table this lab's Model 34 really answered with, on
    # 15 Sep 2026. Used by the cases below so they are checked against an
    # instrument rather than against something invented to pass them.
    def real_table():
        rows = [
            (0, 'None', 'SNONE', '0.0'),
            (1, 'Lakeshore 10', 'SIDIODE', '-1.0'),
            (2, 'Lakeshore 11', 'SIDIODE', '-1.0'),
            (3, 'Cryocal D3', 'SIDIODE', '-1.0'),
            (4, 'SI 410', 'SIDIODE', '-1.0'),
            (5, 'Pt100 3902', 'R312R1MA', '1.0'),
            (6, 'Pt100 385', 'R312R1MA', '1.0'),
            (7, 'Pt1K 385', 'R2K100UA', '10.0'),
            (8, 'Pt1K 375', 'R2K100UA', '10.0'),
            (9, 'TC K Extern', 'TC80', '0.1'),
            (10, 'TC E Extern', 'TC80', '0.1'),
            (11, 'TC T Extern', 'TC80', '0.1'),
            (12, 'TC type K', 'TC80', '1.0'),
            (13, 'TC type E', 'TC80', '1.0'),
            (14, 'TC type T', 'TC80', '1.0'),
            (15, 'S700', 'SIDIODE', '-1.0'),
            (16, 'CX1030 X17680', 'R8K10UA', '-1.0'),
            (17, 'CX1030 X17681', 'R8K10UA', '-1.0'),
            (18, 'P17 R8K10UA', 'R8K10UA', '-1.0'),
            (19, 'DT470 STANDARD1', 'SIDIODE', '-1.0'),
        ]
        rows += [(20 + n, f"User Sensor {'6789ABC'[n]}", 'SIDIODE', '-1.0')
                 for n in range(7)]
        entries = [{'index': i, 'name': n, 'type': t, 'multiplier': m}
                   for i, n, t, m in rows]
        entries += [{'index': i, 'name': None, 'type': None,
                     'multiplier': None} for i in range(27, 32)]
        return entries

    # -- 18: the type vocabulary comes off the instrument -------------------
    def case_type_vocabulary():
        seen = summarise_observed_types(real_table())
        check(set(seen) == {'SNONE', 'SIDIODE', 'R312R1MA', 'R2K100UA',
                            'R8K10UA', 'TC80'},
              f"the six types this unit uses, not {sorted(seen)}")
        check(seen['R8K10UA'] == [16, 17, 18], "where R8K10UA is")
        # The whole point of the button: 'Diode' and 'ACR' are printed in the
        # manual and do not exist on the instrument. A report that let either
        # of them look confirmed would send the loader back to the header
        # that was silently discarded on 15 Sep.
        text = "\n".join(type_vocabulary_report(real_table()))
        check("not seen  Diode" in text, "'Diode' must be reported unseen")
        check("not seen  ACR" in text, "'ACR' must be reported unseen")
        check("SEEN      TC80" in text, "TC80 is on the instrument")
        check("SIDiode" in text, "the report must give the spelling to use")
        # An unconfirmed name is not a refused one, and must not be presented
        # as if the instrument had rejected it.
        check("UNCONFIRMED" in text, "unseen names are unconfirmed")
        check(normalise_type('SiDiode') == normalise_type('SIDIODE'),
              "one type printed two ways is one type")
        check(normalise_type('Si Diode') == 'SIDIODE', "spaces fold away")
        # An empty scan says so instead of reporting an empty vocabulary.
        check('No sensor types' in type_vocabulary_report([])[0], "empty scan")

    # -- 19: where the user block starts, derived ---------------------------
    def case_table_blocks():
        blocks = map_table_blocks(real_table())
        # This is the failure of 29 and 31 Aug: CALCUR 1 went to index 1,
        # 'Lakeshore 10', a factory entry that cannot be written, and the
        # write was discarded in silence. Appendix A's offset of 9 would put
        # user curve 1 at index 10, 'TC E Extern', which is also factory.
        check(blocks['offset'] == 14, f"offset {blocks['offset']}, not 14")
        check(blocks['user_first'] == 15 and blocks['user_last'] == 26,
              "user curves 1-12 are indices 15-26")
        check(blocks['factory_last'] == 14, "the factory block ends at 14")
        check(blocks['agreement'] == 7, f"{blocks['agreement']} placeholders")
        check(blocks['disagreement'] == 0, "the placeholders must agree")
        check(blocks['answered_last'] == 26, "26 is the last that answers")
        check(blocks['offset'] != APPENDIX_A_OFFSET,
              "this instrument disagrees with Appendix A, and must say so")
        # 'User Sensor C' is user curve 12, not user curve 'C'.
        check(user_slot_number('User Sensor C') == 12, "C is 12")
        check(user_slot_number('User Sensor 4') == 4, "4 is 4")
        check(user_slot_number('User Curve B') == 11, "curve/sensor both")
        check(user_slot_number('CX1030 X17680') is None, "a real curve")
        check(user_slot_number('DT470 STANDARD1') is None, "a real curve")
        # No placeholder left means no answer, not the manual's answer.
        filled = [e for e in real_table()
                  if user_slot_number(e['name']) is None]
        check(map_table_blocks(filled)['offset'] is None,
              "with no placeholder, nothing may be assumed")

    # -- the DT-470 that is really in slot 19, header and all ---------------
    def real_dt470():
        header = {'name': 'DT470 STANDARD1', 'sensor_type': 'SiDiode',
                  'multiplier': -1.0, 'multiplier_text': '-1.000000',
                  'units': 'VOLTS', 'no_points': False}
        points = [(0.079330, 480.0), (0.199610, 430.0), (0.458600, 325.0),
                  (0.975500, 100.0), (1.107020, 30.0), (1.502580, 7.5),
                  (1.704190, 1.0)]
        header['point_texts'] = [(f"{r:.6f}", f"{t:.6f}") for r, t in points]
        return header, points

    # -- 20: the good curve passes ------------------------------------------
    def case_audit_good_curve():
        header, points = real_dt470()
        problems, notes = audit_curve(19, header, points, real_table())
        check(not problems, f"the real curve must pass: {problems}")
        check(any('user curve 5' in note for note in notes),
              f"index 19 is user curve 5: {notes}")
        # An empty slot is a fact, not a fault.
        placeholder = {'name': 'User Sensor 6', 'sensor_type': 'SiDiode',
                       'multiplier': -1.0, 'units': 'VOLTS',
                       'no_points': True}
        problems, notes = audit_curve(20, placeholder, [], real_table())
        check(not problems, f"an empty slot is not a fault: {problems}")
        check(any('EMPTY' in note for note in notes), "and it says so")

    # -- 21: a substituted header is caught ---------------------------------
    def case_audit_bad_header():
        header, points = real_dt470()
        # This is slot 18 as it stands on the instrument today: the leftover
        # of a type probe, a VOLTS curve stored against an 8 kohm resistance
        # range. It does not fail on the instrument. It reads a plausible
        # wrong temperature, which is worse.
        wrong = dict(header, name='P17 R8K10UA', sensor_type='R8K10UA')
        problems, _ = audit_curve(18, wrong, points[:2], real_table())
        check(any('wrong excitation' in p for p in problems),
              f"a volts curve on a resistance range must be caught: "
              f"{problems}")
        # The multiplier's sign against what the points actually do.
        flipped = dict(header, multiplier=1.0)
        problems, _ = audit_curve(19, flipped, points, real_table())
        check(any('coefficient' in p for p in problems),
              f"a positive multiplier on a falling curve: {problems}")
        # A multiplier of 0 is what a header that did not land looks like.
        zeroed = dict(header, multiplier=0.0)
        problems, _ = audit_curve(19, zeroed, points, real_table())
        check(any('multiplier is 0' in p for p in problems), "zero multiplier")
        # The curve running off the full scale of its own input.
        over = dict(header, sensor_type='TC80')
        problems, _ = audit_curve(19, over, points, real_table())
        check(any('full scale' in p for p in problems),
              f"1.7 V does not fit an 80 mV thermocouple range: {problems}")
        # A write aimed into the factory block, which is discarded in silence.
        problems, _ = audit_curve(1, header, points, real_table())
        check(any('factory block' in p for p in problems),
              "index 1 is factory on this instrument")
        # Two points at the same reading have two temperatures and no way to
        # choose between them.
        doubled = points[:3] + [(points[2][0], 999.0)] + points[3:]
        problems, _ = audit_curve(19, header, doubled, real_table())
        check(any('more than once' in p for p in problems), "repeated reading")

    # -- 22: the .340 export round-trips ------------------------------------
    def case_lakeshore_340():
        header, points = real_dt470()
        text = build_lakeshore_340_text(header, points, index=19,
                                        idn="Cryocon Model 34")
        check("Data Format:    2      (V/K)" in text, "VOLTS is format 2")
        check("Temperature coefficient:  1 (Negative)" in text,
              "the coefficient comes from the points, not the multiplier")
        check(f"Number of Breakpoints:   {len(points)}" in text, "the count")
        # Read it back the way the Cryo-con loader's .340 reader does: a
        # breakpoint row is exactly three numeric tokens whose first is a
        # whole number. Anything this writes that accidentally matched that
        # shape would be read back as a point, so the count has to survive.
        rows = []
        for line in text.splitlines():
            tokens = line.split()
            if len(tokens) != 3:
                continue
            try:
                values = [float(token) for token in tokens]
            except ValueError:
                continue
            if values[0] != int(values[0]):
                continue
            rows.append((values[1], values[2]))
        check(len(rows) == len(points),
              f"{len(rows)} rows read back, {len(points)} written")
        check(rows == points, "every value must survive the round trip")
        # The header's own comment lines must not be readable as points.
        check(rows[0][1] == 480.0 and rows[-1][1] == 1.0,
              "the table is ascending in the reading, as a .340 is")
        # A curve with no Lake Shore format is refused rather than guessed.
        try:
            build_lakeshore_340_text(dict(header, units='WATTS'), points)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown units should be refused")

    # -- 23: REGRESSION. a one-point curve states no direction --------------
    def case_audit_single_point():
        # all() of an empty sequence is True, so before this a one-point
        # curve read as BOTH ascending and descending, and the multiplier
        # sign check fired against a direction nothing had stated.
        # The multiplier here is POSITIVE on purpose. With the bug, a single
        # point read as descending, the inferred coefficient was negative,
        # and a positive multiplier was reported as contradicting it -- a
        # complaint about a direction the curve never stated. A negative
        # multiplier would have agreed with the bug's guess by luck and this
        # test would have passed either way, which it did until 16 Sep.
        header = {'name': 'ONE POINT', 'sensor_type': 'SiDiode',
                  'multiplier': 1.0, 'units': 'VOLTS'}
        problems, _ = audit_curve(19, header, [(1.0, 300.0)])
        check(any('at least 2' in p or 'interpolate' in p for p in problems),
              f"one point is not a curve: {problems}")
        check(not any('coefficient' in p for p in problems),
              f"one point states no coefficient to disagree with: {problems}")
        # The same, the other way up, so neither sign passes by luck.
        problems, _ = audit_curve(
            19, dict(header, multiplier=-1.0), [(1.0, 300.0)])
        check(not any('coefficient' in p for p in problems),
              f"still no direction to disagree with: {problems}")
        # And an empty point list must not reach the direction check at all.
        problems, _ = audit_curve(
            19, dict(header, name='User Sensor 5', no_points=True), [])
        check(not any('coefficient' in p for p in problems), problems)
        # Two points do state one, and a wrong sign must still be caught.
        problems, _ = audit_curve(
            19, dict(header, multiplier=1.0),
            [(0.07933, 480.0), (1.70419, 1.0)])
        check(any('coefficient' in p for p in problems), problems)

    # -- 24: REGRESSION. a zero multiplier is caught whatever the points do -
    def case_audit_zero_multiplier():
        # This check used to sit inside the 'temperature is monotonic'
        # branch, so it never ran on a curve whose points were also
        # disordered -- which is the curve most likely to have a header the
        # firmware rewrote. A zero multiplier is evidence about the HEADER
        # and the points have no bearing on it.
        header = {'name': 'SCRAMBLED', 'sensor_type': 'SiDiode',
                  'multiplier': 0.0, 'units': 'VOLTS'}
        scrambled = [(0.1, 300.0), (0.2, 100.0), (0.3, 200.0)]
        problems, _ = audit_curve(19, header, scrambled)
        check(any('multiplier is 0' in p for p in problems),
              f"a zero multiplier must be caught here too: {problems}")
        check(any('one direction' in p for p in problems),
              "and the disordered points must still be reported")
        # A multiplier whose MAGNITUDE is not 1 is a note, not a fault:
        # 'Pt1K 385' on this instrument carries 10.0 and is a perfectly good
        # factory entry, a Pt100 table scaled by ten. The SIGN still has to
        # agree with the data, so the note is checked on a rising curve,
        # where +10 is right, rather than on a falling one where it is not.
        rising = [(50.0, 20.0), (500.0, 200.0)]
        platinum = {'name': 'PT1K 385', 'sensor_type': 'R2K100UA',
                    'multiplier': 10.0, 'units': 'OHMS'}
        problems, notes = audit_curve(7, platinum, rising)
        check(not any('multiplier' in p for p in problems),
              f"x10 with the right sign is not a fault: {problems}")
        check(any('not +-1' in note for note in notes),
              f"but it is worth saying: {notes}")
        # The same magnitude with the WRONG sign is still a fault, or this
        # note would be a way to smuggle an inverted sensor past the check.
        falling = [(0.07933, 480.0), (1.70419, 1.0)]
        problems, _ = audit_curve(19, dict(header, multiplier=10.0), falling)
        check(any('coefficient' in p for p in problems),
              f"+10 on a falling curve is still the wrong sign: {problems}")

    # -- 25: the .340 writer refuses what it cannot state -------------------
    def case_lakeshore_340_refusals():
        header = {'name': 'DT470 STANDARD1', 'sensor_type': 'SiDiode',
                  'multiplier': -1.0, 'units': 'VOLTS'}
        for bad, why in ((None, "no points"), ([], "no points"),
                         ([(1.0, 300.0)], "one point")):
            try:
                build_lakeshore_340_text(header, bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{why} should be refused")
        # A curve whose units have no Lake Shore data format is refused by
        # name rather than written with a guessed code.
        try:
            build_lakeshore_340_text(dict(header, units='AMPS'),
                                     [(0.1, 300.0), (0.2, 100.0)])
        except ValueError as exc:
            check('AMPS' in str(exc), f"say which unit: {exc}")
        else:
            raise AssertionError("unknown units should be refused")
        # The header field Lake Shore writes as a float must not come out as
        # a bare integer.
        text = build_lakeshore_340_text(header,
                                        [(0.07933, 480.0), (1.70419, 1.0)])
        limit = [line for line in text.splitlines()
                 if line.startswith('SetPoint Limit')][0]
        check('480.0' in limit, f"a temperature keeps its point: {limit}")

    # -- 26: nothing in a .340 this writes is mistaken for a breakpoint -----
    def case_lakeshore_340_is_not_self_confusing():
        # The Lake Shore readers in this suite take any line of exactly three
        # numeric tokens whose first is a whole number as a breakpoint. A
        # header or footer line that happened to match that shape would be
        # read back as an extra point, and the stated count would then
        # disagree and the whole file be refused.
        header = {'name': '312R 625', 'sensor_type': '3.1kR',
                  'multiplier': -1.0, 'units': 'OHMS'}
        points = [(43.761, 330.03), (100.0, 100.0), (977.25, 3.5913)]
        text = build_lakeshore_340_text(
            header, points, index=16, idn="Cryocon 34 1 2",
            address="GPIB0::23::INSTR")
        rows = []
        for line in text.splitlines():
            tokens = line.split()
            if len(tokens) != 3:
                continue
            try:
                values = [float(token) for token in tokens]
            except ValueError:
                continue
            if values[0] == int(values[0]):
                rows.append((values[1], values[2]))
        check(len(rows) == len(points),
              f"{len(rows)} rows read back from a {len(points)}-point file")
        check(rows == points, rows)
        # And the stated count is what a reader will actually find.
        stated = [line for line in text.splitlines()
                  if line.startswith('Number of Breakpoints')][0]
        check(str(len(points)) in stated, stated)

    # -- 27: a rising curve is written as a POSITIVE coefficient ------------
    def case_lakeshore_340_coefficient_follows_the_data():
        # Every curve in this lab is an NTC or a diode, so the negative case
        # is the only one that has ever been exercised. A Platinum RTD rises
        # with temperature, and writing '1 (Negative)' for one would invert
        # the sense of the sensor on whatever read the file back.
        platinum = {'name': 'PT100 385', 'sensor_type': 'R312R1MA',
                    'multiplier': 1.0, 'units': 'OHMS'}
        rising = [(20.0, 50.0), (100.0, 260.0), (200.0, 500.0)]
        text = build_lakeshore_340_text(platinum, rising)
        check("Temperature coefficient:  2 (Positive)" in text, text[:400])
        check("Data Format:    3      (Ohm/K)" in text, text[:400])
        # LOGOHM is format 4, and a Cernox is negative.
        cernox = {'name': 'CX1030 X17680', 'sensor_type': 'R8K10UA',
                  'multiplier': -1.0, 'units': 'LOGOHM'}
        text = build_lakeshore_340_text(
            cernox, [(1.64523, 325.0), (2.94699, 4.0)])
        check("Data Format:    4      (Log Ohm/K)" in text, text[:400])
        check("Temperature coefficient:  1 (Negative)" in text, text[:400])
        # The name splits into model and serial the way the Cryo-con loader
        # rejoins them, so a curve can go out and come back unchanged.
        check("Sensor Model:   CX1030" in text, text[:200])
        check("Serial Number:  X17680" in text, text[:200])

        # THE CASE THAT MATTERS, and the one nothing tested until 16 Sep:
        # the multiplier and the points DISAGREEING. Every curve above has
        # them agreeing, so reading the coefficient from the wrong one of
        # the two was invisible -- a mutation that swapped the source passed
        # every check in this module.
        #
        # The points win, and they have to. The multiplier is a header field
        # this firmware replaces with a default when it cannot identify one;
        # the points are what was measured. A .340 that took the header's
        # word would carry a silently substituted default out to whatever
        # read the file, and invert the sensor.
        lying = {'name': 'CX1030 X17680', 'sensor_type': 'R8K10UA',
                 'multiplier': 1.0, 'units': 'LOGOHM'}
        falling = [(1.64523, 325.0), (2.94699, 4.0)]
        text = build_lakeshore_340_text(lying, falling)
        check("Temperature coefficient:  1 (Negative)" in text,
              "the POINTS fall, so the file must say Negative whatever the "
              "multiplier claims: " + text[:300])
        # And the other way round, so neither answer is simply hard-coded.
        lying_up = {'name': 'PT100 385', 'sensor_type': 'R312R1MA',
                    'multiplier': -1.0, 'units': 'OHMS'}
        text = build_lakeshore_340_text(lying_up, [(50.0, 20.0),
                                                   (500.0, 200.0)])
        check("Temperature coefficient:  2 (Positive)" in text,
              "the POINTS rise, so the file must say Positive: " + text[:300])
        # A header carrying no multiplier at all must still produce a file,
        # because the points still state the coefficient on their own.
        text = build_lakeshore_340_text(
            {'name': 'NO MULTIPLIER', 'sensor_type': 'SiDiode',
             'units': 'VOLTS'}, [(0.07933, 480.0), (1.70419, 1.0)])
        check("Temperature coefficient:  1 (Negative)" in text, text[:300])

    # -- 28: the type report does not hard-code THIS instrument's answer ----
    def case_type_report_is_not_hard_coded():
        # The whole value of the button is that it reads the instrument in
        # front of it. A report that always says "use SIDiode" would be a
        # printed manual with extra steps, and wrong on the first unit that
        # disagrees.
        other = [{'index': 0, 'name': 'None', 'type': 'None',
                  'multiplier': '0.0'},
                 {'index': 1, 'name': 'LS DT-470', 'type': 'Diode',
                  'multiplier': '-1.0'},
                 {'index': 2, 'name': 'Cernox', 'type': 'ACR',
                  'multiplier': '-1.0'}]
        text = "\n".join(type_vocabulary_report(other))
        check("SEEN      Diode" in text, "this unit does use 'Diode'")
        check("SEEN      ACR" in text, "and 'ACR'")
        check("does not appear anywhere" not in text,
              "so the report must NOT claim otherwise")
        check("discarded whole" not in text,
              "and must not repeat the other unit's verdict")
        # A scan that reached nothing says so rather than reporting an empty
        # vocabulary as a finding.
        blank = [{'index': n, 'name': None, 'type': None,
                  'multiplier': None} for n in range(4)]
        check('No sensor types' in type_vocabulary_report(blank)[0],
              "a scan that answered nothing is not a vocabulary")

    # -- 29: placeholders that disagree are reported, not averaged ----------
    def case_table_blocks_disagreement():
        # Two different offsets in one table means something is wrong with
        # the instrument or the scan, and picking the popular one silently
        # would send a write into the factory block.
        entries = [{'index': 15, 'name': 'User Sensor 1', 'type': 'SIDIODE',
                    'multiplier': '-1.0'},
                   {'index': 16, 'name': 'User Sensor 2', 'type': 'SIDIODE',
                    'multiplier': '-1.0'},
                   {'index': 20, 'name': 'User Sensor 4', 'type': 'SIDIODE',
                    'multiplier': '-1.0'}]
        blocks = map_table_blocks(entries)
        check(blocks['offset'] == 14, blocks)
        check(blocks['agreement'] == 2 and blocks['disagreement'] == 1, blocks)
        lines = "\n".join(table_geometry_report(entries)[0])
        check('DIFFERENT offset' in lines, lines)
        # A table nothing answered gives no offset and says so.
        blocks = map_table_blocks([])
        check(blocks['offset'] is None and blocks['answered_last'] is None,
              blocks)
        check('cannot be derived' in table_geometry_report([])[0][0],
              table_geometry_report([])[0])

    # -- 30: the checks always name the slot they are about -----------------
    def case_checks_name_their_slot():
        # The Checks tab is read after the eye has already moved on from the
        # slot number, so every finding that could be mistaken for another
        # slot's has to carry the index with it.
        header = {'name': 'CX1030 X17680', 'sensor_type': 'R8K10UA',
                  'multiplier': -1.0, 'units': 'LOGOHM'}
        catalogue = [{'index': 19, 'name': 'User Sensor 5',
                      'type': 'SIDIODE', 'multiplier': '-1.0'}]
        problems, _ = audit_curve(1, header,
                                  [(1.64523, 325.0), (2.94699, 4.0)],
                                  catalogue)
        check(any('Index 1' in p and 'factory' in p for p in problems),
              f"a write into the factory block must name the index: "
              f"{problems}")
        _, notes = audit_curve(16, header,
                               [(1.64523, 325.0), (2.94699, 4.0)], catalogue)
        check(any('user curve 2' in note for note in notes),
              f"index 16 is user curve 2 with offset 14: {notes}")
        # Above the last user slot is out of range, not merely unusual.
        problems, _ = audit_curve(30, header,
                                  [(1.64523, 325.0), (2.94699, 4.0)],
                                  catalogue)
        check(any('above the last user slot' in p for p in problems),
              problems)

    # -- 31: a Cernox off the top of its own range is caught ----------------
    def case_audit_full_scale_both_families():
        # The check that a curve fits the input it is stored against, in
        # ohms as well as in volts. 10**3.95 = 8913 ohm, just over the
        # 8 kohm range a Cernox uses here.
        header = {'name': 'CX1030 COLD', 'sensor_type': 'R8K10UA',
                  'multiplier': -1.0, 'units': 'LOGOHM'}
        problems, _ = audit_curve(
            16, header, [(1.64523, 325.0), (3.95, 2.0)])
        check(any('full scale' in p for p in problems),
              f"8913 ohm does not fit an 8 kohm range: {problems}")
        # And the same curve inside the range passes.
        problems, _ = audit_curve(
            16, header, [(1.64523, 325.0), (3.04306, 4.0)])
        check(not problems, f"1104 ohm fits comfortably: {problems}")
        # A type with no single full scale skips the check rather than
        # inventing a number for it.
        problems, _ = audit_curve(
            16, dict(header, sensor_type='ACR'),
            [(1.64523, 325.0), (3.95, 2.0)])
        check(not any('full scale' in p for p in problems),
              f"an autoranging bridge has no full scale: {problems}")

    # -- 32: repeated and out-of-order readings ----------------------------
    def case_audit_reading_order():
        header = {'name': 'DT470 STANDARD1', 'sensor_type': 'SiDiode',
                  'multiplier': -1.0, 'units': 'VOLTS'}
        # Two temperatures at one reading: the instrument interpolates on
        # the reading, so it has two answers and no way to choose.
        repeated = [(0.07933, 480.0), (0.5, 300.0), (0.5, 200.0),
                    (1.70419, 1.0)]
        problems, _ = audit_curve(19, header, repeated)
        check(any('more than once' in p for p in problems), problems)
        # Descending readings: this firmware sorts a curve as it stores it,
        # so a stored curve out of order means the block did not land whole.
        backwards = [(1.70419, 1.0), (0.07933, 480.0)]
        problems, _ = audit_curve(19, header, backwards)
        check(any('ascending' in p for p in problems), problems)
        # Absolute zero and below is not a temperature.
        problems, _ = audit_curve(
            19, header, [(0.07933, 480.0), (1.70419, 0.0)])
        check(any('0 K' in p for p in problems), problems)

    # -- 33: the name rules, and the placeholder that should not have points
    def case_audit_name_rules():
        good = [(0.07933, 480.0), (1.70419, 1.0)]
        base = {'sensor_type': 'SiDiode', 'multiplier': -1.0,
                'units': 'VOLTS'}
        problems, _ = audit_curve(19, dict(base, name='ABC'), good)
        check(any('at least 4' in p for p in problems), problems)
        problems, _ = audit_curve(
            19, dict(base, name='SIXTEEN CHARS XX'), good)
        check(any('keeps 15' in p for p in problems), problems)
        # A slot holding the untouched-slot placeholder AND points is a
        # state that should not exist, and is worth saying so about rather
        # than quietly passing.
        problems, _ = audit_curve(
            19, dict(base, name='User Sensor 5'), good)
        check(any('placeholder' in p for p in problems), problems)
        # An empty slot with that name is perfectly normal, though.
        problems, notes = audit_curve(
            19, dict(base, name='User Sensor 5', no_points=True), [])
        check(not problems, problems)
        check(any('user curve 5' in note for note in notes), notes)

    return [
        ("read-only guard admits queries only", case_read_only_guard),
        ("both paths to the bus enforce it", case_link_refuses),
        ("a block read stops at the semicolon", case_ask_block),
        ("CALCUR? reply is parsed, reading first", case_parse_block),
        ("an echoed command line is tolerated", case_parse_with_echo),
        ("a truncated reply is refused", case_parse_truncated),
        ("unknown curve units are refused", case_parse_bad_units),
        ("numbers are written in plain decimal", case_fmt6),
        (".crv export round-trips", case_crv_round_trip),
        (".crv keeps the instrument's own numerals",
         case_crv_keeps_printed_digits),
        ("LOGOHM converts to ohms, VOLTS does not", case_ohms),
        ("empty and factory slot names", case_slot_names),
        ("curve statistics", case_statistics),
        ("curve CSV", case_curve_csv),
        ("slot list CSV", case_catalogue_csv),
        ("REGRESSION: an empty user slot reads as empty, not as a fault",
         case_parse_empty_slot),
        ("the stored type is checked against the curve units both ways",
         case_type_against_units),
        ("the type vocabulary is read off the instrument, not the manual",
         case_type_vocabulary),
        ("REGRESSION: the user block is derived from the placeholders",
         case_table_blocks),
        ("the audit passes the DT-470 that really is in slot 19",
         case_audit_good_curve),
        ("the audit catches a header the firmware substituted",
         case_audit_bad_header),
        (".340 export round-trips through the Lake Shore reader",
         case_lakeshore_340),
        ("REGRESSION: a one-point curve states no direction to check",
         case_audit_single_point),
        ("REGRESSION: a zero multiplier is caught whatever the points do",
         case_audit_zero_multiplier),
        ("the .340 writer refuses what it cannot state",
         case_lakeshore_340_refusals),
        ("no line of a written .340 is mistaken for a breakpoint",
         case_lakeshore_340_is_not_self_confusing),
        ("a rising curve is written as a positive coefficient",
         case_lakeshore_340_coefficient_follows_the_data),
        ("the type report reads the instrument, not this lab's answer",
         case_type_report_is_not_hard_coded),
        ("placeholders that disagree are reported, not averaged",
         case_table_blocks_disagreement),
        ("every finding names the slot it is about",
         case_checks_name_their_slot),
        ("a curve off the top of its own input range is caught",
         case_audit_full_scale_both_families),
        ("repeated, reversed and sub-zero points are caught",
         case_audit_reading_order),
        ("the name rules, and a placeholder that has points",
         case_audit_name_rules),
    ]


def run_self_test(report=print):
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
        report(f"{len(failures)} of {len(cases)} checks FAILED.")
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
    app = CurveViewerGUI(root)
    if not PYVISA_AVAILABLE:
        messagebox.showwarning(
            "PyVISA Not Installed",
            "PyVISA is not installed, so no curve can be read from an "
            "instrument.\n\nTo read from here:\n  pip install pyvisa "
            "pyvisa-py")
    root.mainloop()
