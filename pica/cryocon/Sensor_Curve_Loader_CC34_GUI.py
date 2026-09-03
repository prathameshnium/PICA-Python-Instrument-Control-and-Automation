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
        <sensor type>        enumeration, see CALCUR_SENSOR_TYPES below
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

Sensor types: THE MANUAL USES TWO DIFFERENT VOCABULARIES, ONE PER COMMAND.
This was the cause of the 29 Aug 2026 failure and it is the reason this
module now keeps two separate lists instead of one.

  1. The CALCUR curve header. Printed p.173, under "CALCUR: Calibration
     Curve Set or Query":
        "<sensor type> is from the following list: Diode, ACR, 31kR, 3.1kR,
         312R, 625R, TC80, TC40 and None. If the sensor type cannot be
         identified, Diode is used."
     R8K10UA IS NOT ON THAT LIST. Putting it in a CALCUR header is by
     definition unidentifiable, and the documented consequence is the silent
     diode substitution. For an NTC resistor -- Cernox, Ruthenium-Oxide,
     Germanium, Carbon Glass, thermistors -- the CALCUR spelling is ACR.
     CALCUR_SENSOR_TYPES below is that list in full.

  2. The SENTYPE:TYPE input configuration. Printed p.187:
        "Diode for Silicon Diodes. R16K10UA, R8K10UA, R6K100UA, R2K100UA,
         R625R1MA and R312R1MA for resistor sensors. Snone to disable the
         input channel. TC80 ... TC40 ..."
     This is where R8K10UA belongs, and it is a separate command sent after
     the curve has landed and verified. SENTYPE_SENSOR_TYPES below.

  - Table 4, "Resistor Sensor Configuration" (printed p.15), gives the input
    configuration for our sensor:
        Cernox  ->  R8K10UA,  10 uA excitation,  (-) coefficient,  LogOhms
    X17680 runs from 43.76 ohm at 330 K to 977.25 ohm at 3.59 K, so the 8
    kohm full-scale range fits it with room to spare. That row describes the
    SENTYPE:TYPE step, not the CALCUR header.
  - So a Cernox takes TWO commands, in this order:
        CALCUR <n>   with header type ACR      (the curve itself)
        SENTYPE <n+9>:TYPE R8K10UA             (the input range and current)
    run_full_sequence() does them in that order and verifies each.
  - LOGOHM is the base-10 log of ohms. The manual asks for it on Cernox,
    Ruthenium-Oxide, Germanium, Carbon Glass and thermistors because their
    resistance curve is far more linear in log form, and the instrument
    interpolates between breakpoints.
  - DANGER, and the reason this module verifies: an unrecognised type is
    replaced silently, and a Cernox running on a diode input configuration
    reads nonsense. read_curve() reports the type the instrument actually
    kept, and the Rev 3.03A unit in this lab spells its diode type
    'SiDiode', which Edition 4 does not print anywhere.

What a failed readback means (added 31 Aug 2026, from a real failure):
  - If ONLY the sensor type differs, the block landed and the type was
    substituted. Fix the type and resend.
  - If the NAME, the UNITS and the TYPE all differ at once, the block did
    NOT land at all and the slot still holds whatever was there before.
    A short point count in that case is meaningless: it is the point count
    of somebody else's curve, not evidence that lines were lost, and
    changing the line ending will not help. classify_verify() draws that
    distinction so the console stops giving the wrong advice.

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
v1.1, 31 Aug 2026. The 29 Aug transfer to X17680 failed and this is what
      came out of it:
        - the CALCUR header type and the SENTYPE:TYPE input type are two
          different vocabularies in the manual and are now two separate
          lists and two separate fields. R8K10UA in a CALCUR header is now
          a blocking error, not a warning;
        - Cernox defaults are ACR for the header and R8K10UA for the input
          type, sent as two commands in that order;
        - the slot is read BEFORE the send, so an unchanged readback is
          reported as 'nothing was written' instead of as lost points;
        - classify_verify() replaces the old advice, which told the
          operator to change the line ending in exactly the case where
          the line ending was irrelevant;
        - run_full_sequence(): every instrument step in order, each checked
          before the next, stopping at the first failure, with a summary;
        - run_self_test(): 26 offline checks, several of them regressions on
          the above. Run with --selftest or from the Advanced panel.
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
MIN_CURVE_POINTS = 2
MAX_CURVE_POINTS = 200
MIN_NAME_CHARS = 4
MAX_NAME_CHARS = 15

# ---------------------------------------------------------------------------
# THE SENSOR TABLE IS MAPPED, NOT ASSUMED  (added v1.2, 31 Aug 2026)
# ---------------------------------------------------------------------------
#
# Appendix A of the Edition 4 manual contradicts itself and the instrument
# contradicts both halves of it.
#
#   * "Factory Installed Curves" (printed p.209) runs 0 to 13 and ends with
#     four thermocouples: 10 TC type K, 11 TC type E, 12 TC type T,
#     13 AuFe 0.07%.
#   * "User Installed Sensor Curves" on the next page claims Senix 10 to 21
#     for user curves 1 to 12. Those two tables both claim index 10.
#   * The Rev 3.03A unit in this lab answers SENTYPE? 10 with
#     'TC E Extern', type TC80, multiplier 0.1. That is a thermocouple, so
#     the user block does not start at 10 -- but it is the manual's index 11
#     entry, not its index 10 entry, so the factory table is off by one too.
#
# Three printed claims, none of which survives contact with the instrument.
# So SENIX_OFFSET_DEFAULT is a starting guess and nothing more, every use of
# it is labelled unconfirmed until scan_sensor_table() has been run, and the
# module refuses to write to an index the scan says is a factory entry.
SENIX_OFFSET_DEFAULT = 9     # what Appendix A claims; NOT trusted
SENIX_OFFSET = SENIX_OFFSET_DEFAULT      # kept for callers that predate v1.2
MASTER_TABLE_SCAN_MAX = 31   # how far up to walk when mapping the table

# Whether the number after CALCUR is the user-curve number or the Master
# Sensor Table index. The manual says the former (printed p.210). The
# 29 and 31 Aug failures are both consistent with the latter, where CALCUR 1
# addressed a protected factory diode curve and was discarded in silence,
# which is what p.172 says happens: "Factory installed calibration curves may
# not be changed or deleted with these commands." Neither reading is assumed;
# the operator picks, and the scan tells them which to pick.
CALCUR_ADDRESSING = {
    'user': "user curve number, 1 to 12 (what Appendix A says)",
    'senix': "Master Sensor Table index (what the failures suggest)",
}

# Factory names as Appendix A prints them, used ONLY to recognise a factory
# entry in a scan result. Not used to compute any index.
APPENDIX_A_FACTORY_NAMES = (
    'none', 'cryocon s700', 'ls dt-670', 'ls dt-470', 'si 410 diode',
    'pt100 385', 'pt1k 385', 'pt10k 385', 'ruox 1k ohm', 'ruox 2k ohm',
    'tc type k', 'tc type e', 'tc type t', 'aufe 0.07%',
)

# Substrings that mark an entry as factory-installed whatever it is called.
# 'Lakeshore 10' is the Curve 10 DT-470 diode table; 'TC E Extern' is this
# firmware's spelling of the type E thermocouple. Both were found in slots
# Appendix A promised were free.
FACTORY_NAME_MARKERS = (
    'lakeshore', 'cryocon', 'dt-470', 'dt-670', 'dt470', 'dt670',
    'si 410', 'si410', 'pt100', 'pt1k', 'pt10k', 'ruox', 'ro-105', 'ro-600',
    'rhfe', 'aufe', 'tc type', 'tc k', 'tc e', 'tc t', 'thermocouple',
    'extern', 'none',
)

# What an untouched user slot is called before anything is put in it.
USER_SLOT_NAME_RE = re.compile(
    r'^\s*user\s*(?:sensor|curve)\s*([0-9A-Ca-c])\s*$', re.I)

# Sensor units the CALCUR header will accept. LOGOHM is base-10 log of ohms.
CURVE_UNITS = ('LOGOHM', 'OHMS', 'VOLTS')

# ---------------------------------------------------------------------------
# THE TWO SENSOR-TYPE VOCABULARIES
# ---------------------------------------------------------------------------
#
# These are two different lists in the manual, for two different commands, and
# they are NOT interchangeable. Sending a name from one list to the other is
# what broke the 29 Aug 2026 transfer: R8K10UA went into a CALCUR header, the
# firmware could not identify it, and it silently substituted a diode type.
#
# fields: (full scale, unit of that full scale, what it is for)

# 1. What the CALCUR header will accept. Manual, printed p.173, verbatim:
#    "Diode, ACR, 31kR, 3.1kR, 312R, 625R, TC80, TC40 and None. If the sensor
#     type cannot be identified, Diode is used."
#    ACR is the entry for every NTC resistor: Cernox, Ruthenium-Oxide,
#    Germanium, Carbon Glass and thermistors. It has no single full scale
#    because the AC bridge autoranges, so the full-scale check is skipped for
#    it while the ohms-versus-volts check still runs.
CALCUR_SENSOR_TYPES = {
    'ACR':    (None,   'ohm',
               "AC resistance bridge - Cernox, RuOx, Germanium, Carbon "
               "Glass, thermistors. THE CALCUR SPELLING FOR AN NTC SENSOR"),
    'Diode':  (2.5,    'V',   "2.5 V FS - silicon and GaAlAs diodes"),
    '31kR':   (31.3e3, 'ohm', "31.3 kohm FS - Pt 10k / high-value resistors"),
    '3.1kR':  (3.13e3, 'ohm', "3.13 kohm FS - Platinum 1000 and similar"),
    '625R':   (625.0,  'ohm', "625 ohm FS - Platinum 100 above 800 K"),
    '312R':   (312.0,  'ohm', "312 ohm FS - Platinum 100 below 800 K"),
    'TC80':   (0.080,  'V',   "80 mV FS - thermocouple"),
    'TC40':   (0.040,  'V',   "40 mV FS - thermocouple"),
    'None':   (None,   '',    "no sensor; turns the entry off"),
}

# 2. What SENTYPE <index>:TYPE will accept. Manual, printed p.187:
#    "Diode ... R16K10UA, R8K10UA, R6K100UA, R2K100UA, R625R1MA and R312R1MA
#     for resistor sensors. Snone to disable the input channel. TC80 ...
#     TC40 ..."
#    This command sets the input RANGE and EXCITATION, which is what Table 4
#    (printed p.15) specifies for a Cernox. It is sent after the curve.
SENTYPE_SENSOR_TYPES = {
    'R8K10UA':  (8.0e3,  'ohm',
                 "8 kohm FS, 10 uA - Cernox, RuOx, Germanium, Carbon Glass, "
                 "thermistors (manual Table 4)"),
    'R16K10UA': (16.0e3, 'ohm', "16 kohm FS, 10 uA - Pt 10k / NTC resistors"),
    'R6K100UA': (6.25e3, 'ohm', "6.25 kohm FS, 100 uA - Platinum 1000"),
    'R2K100UA': (2.0e3,  'ohm', "2 kohm FS, 100 uA - Platinum 1000"),
    'R625R1MA': (625.0,  'ohm', "625 ohm FS, 1 mA - Platinum 100 above 800 K"),
    'R312R1MA': (312.0,  'ohm', "312 ohm FS, 1 mA - Platinum 100 below 800 K"),
    'Diode':    (2.5,    'V',   "2.5 V FS, 10 uA - silicon and GaAlAs diodes"),
    'TC80':     (0.080,  'V',   "80 mV FS - thermocouple"),
    'TC40':     (0.040,  'V',   "40 mV FS - thermocouple"),
    'Snone':    (None,   '',    "disable the input channel"),
}

# v1.3: the scan of 31 Aug reopened the type question. SENTYPE? reported
# SNONE, SIDIODE, R312R1MA, R2K100UA and TC80 across the factory table, so
# the R-name family IS this firmware's own vocabulary and 'ACR' appears
# nowhere in it. The 29 Aug send that used R8K10UA went to table index 1,
# 'Lakeshore 10', which is a factory entry that cannot be written, so that
# failure never tested the type at all. Neither name is now assumed to be
# right, both are offered for the CALCUR header, and probe_calcur_type()
# settles it against the instrument in about a second per candidate.
CALCUR_MANUAL_TYPE_LIST = tuple(CALCUR_SENSOR_TYPES)
CALCUR_SENSOR_TYPES.update(SENTYPE_SENSOR_TYPES)

# Header types worth trying for an NTC resistor, best first. The firmware's
# own spelling leads; the manual's CALCUR page follows.
CALCUR_TYPE_CANDIDATES = ('R8K10UA', 'ACR', 'Diode')

# Every name this module recognises anywhere, for full-scale lookups and for
# callers that predate the split. Membership in this does NOT mean a name is
# legal in a CALCUR header; only CALCUR_SENSOR_TYPES does.
SENSOR_TYPES = dict(SENTYPE_SENSOR_TYPES)
SENSOR_TYPES.update(CALCUR_SENSOR_TYPES)

# Type strings this firmware is known to report when it has substituted a
# diode for a type it could not identify. Edition 4 prints only 'Diode'; the
# Rev 3.03A unit in this lab answers 'SiDiode'.
DIODE_SUBSTITUTION_STRINGS = ('diode', 'sidiode', 'si diode', 'sidiod')

# What the Cernox needs. Two commands, two vocabularies, in this order.
#   sensor_type   goes in the CALCUR header       (manual p.173 list)
#   sentype_type  goes in SENTYPE <senix>:TYPE    (manual p.187 list, Table 4)
CERNOX_DEFAULTS = {
    'sensor_type': 'R8K10UA',
    'multiplier': '-1.0',
    'units': 'LOGOHM',
    'sentype_type': 'R8K10UA',
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
    texts = []
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
        texts.append((tokens[0], tokens[1]))

    if not terminated:
        raise CurveFileError(
            f"{source_name} does not end with the semicolon line that marks "
            "the end of a Cryo-con curve, so it may be truncated.")

    header = {
        'name': name,
        'sensor_type': sensor_type,
        'multiplier': multiplier,
        'units': units,
        # The numerals exactly as they were printed, reading then temperature.
        # Kept because how many digits the instrument prints is the limit on
        # how closely a readback can be checked, and that is not recoverable
        # once the text has been turned into a float. compare_curves() uses
        # them so the check is made at the precision the instrument actually
        # offers rather than at one this module assumed.
        'point_texts': texts,
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


def extend_curve(primary, extra, primary_units, extra_units):
    """Extend a curve at its ends with points from a second file.

    Written for the lab Cernox, where neither Lake Shore file alone is the
    best answer:

      X17680.340  129 breakpoints Lake Shore placed for an interpolating
                  controller, over the certified 4.000-325.000 K.
      X17680.dat  the 71 raw measured points, reaching 3.5913 K and 330.03 K
                  but sparser in between.

    Taking the .340 as the curve and adding the .dat points that lie beyond
    its ends gives the dense certified table AND the extra reach at the cold
    end, which is what matters on a CCR running to 3-5 K.

    The rules are deliberately narrow, because merging two derivations of the
    same calibration is a good way to build a curve that is worse than either:

      * every point of `primary` is kept exactly as it is;
      * a point from `extra` is added ONLY if it lies outside `primary`'s
        temperature span. Nothing is interleaved, so the interpolation inside
        the certified range is still purely Lake Shore's table;
      * an added point must also lie beyond `primary`'s span in SENSOR
        READING. For a monotonic sensor that is automatic; if it fails, the
        two files disagree about the same sensor and the merge is refused
        rather than papered over.

    Returns (points, added, notes) where `added` is the list of points taken
    from `extra`, so the caller can say exactly what came from where.
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
        beyond_reading = reading < r_low or reading > r_high
        if not beyond_reading:
            raise CurveFileError(
                f"The second file has a point at {temperature:.6g} K, "
                f"outside the main curve's {t_low:.6g}-{t_high:.6g} K span, "
                f"whose sensor reading {reading:.6g} falls back INSIDE the "
                f"main curve's {r_low:.6g}-{r_high:.6g}. The two files "
                "disagree about this sensor, so they are not merged. Use "
                "one file on its own.")
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
            f"reaches {min(cold):.6g} K instead of {t_low:.6g} K.")
    if hot:
        notes.append(
            f"{len(hot)} point(s) added above the main curve: it now "
            f"reaches {max(hot):.6g} K instead of {t_high:.6g} K.")
    notes.append(
        "Nothing inside the main curve's own span was changed, so the "
        "interpolation there is still exactly the main file's.")
    return merged, added, notes


def thin_points(points, limit):
    """Reduce a curve to at most `limit` points, keeping both ends.

    The instrument holds 200 entries. Points are dropped at even spacing
    through the list rather than by any cleverness, so what survives is a
    plain subset of the calibration and nothing is invented by interpolation.
    """
    if limit < MIN_CURVE_POINTS:
        raise ValueError(
            f"A curve cannot be thinned to {limit} points; the instrument "
            f"needs at least {MIN_CURVE_POINTS}.")
    count = len(points)
    if count <= limit:
        return list(points), 0
    keep_indices = sorted({round(i * (count - 1) / (limit - 1))
                           for i in range(limit)})
    thinned = [points[i] for i in keep_indices]
    return thinned, count - len(thinned)


def analyse_curve(points, units, sensor_type, multiplier, name,
                  working_range=None, sentype_type=None):
    """Check a curve against everything the manual requires of one.

    `sentype_type` is the input type that will be sent afterwards with
    SENTYPE <index>:TYPE. It is what actually sets the input range and
    excitation, so it, not the CALCUR header type, is what the curve's peak
    resistance has to fit inside. Splitting the two vocabularies moved that
    check here; without it, an ACR header would sail past a curve that is
    off the top of an 8 kohm input.

    `working_range` is an optional (lowest, highest) pair of temperatures in
    Kelvin that the sensor will actually be used over. Where it is given, the
    curve's coverage is checked against it. This is not a Cryo-con rule; it
    is here because a curve that simply stops before the cold end of a run is
    not a fault the instrument reports as one. Below its lowest breakpoint a
    Cryo-con shows a run of dots -- "reading is within the instrument's range
    but outside the sensor's calibration curve" -- and if nobody was told to
    expect it, that is discovered on a cold cryostat at two in the morning.

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
    if units == 'OHMS' and any(reading <= 0 for reading in readings):
        # LOGOHM is deliberately not checked this way: its values are
        # logarithms, so a negative one only means a resistance below 1 ohm.
        errors.append("The curve contains a resistance of zero or below.")

    # Duplicate sensor readings make the curve ambiguous: the instrument
    # interpolates on the reading, so two temperatures at one reading has no
    # single answer. The instrument would keep both and sort them adjacent.
    # Judged on the six digits fmt6() sends, not on the floats: two readings
    # that differ only past the sixth digit go out identical.
    printed = [fmt6(reading) if math.isfinite(reading) else repr(reading)
               for reading in readings]
    duplicates = [readings[i] for i in range(1, len(readings))
                  if printed[i] == printed[i - 1]]
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
    if sensor_type in SENTYPE_SENSOR_TYPES and \
            sensor_type not in CALCUR_MANUAL_TYPE_LIST:
        warnings.append(
            f"'{sensor_type}' is this firmware's own type name, reported by "
            "SENTYPE? across the factory table, but the manual's CALCUR page "
            "(printed p.173) prints a different list for the header: "
            f"{', '.join(CALCUR_MANUAL_TYPE_LIST)}. Which of the two the "
            "header parser wants is not settled. Nothing here can decide it; "
            "the readback will, and the type probe settles it in one press.")
    if sensor_type not in CALCUR_SENSOR_TYPES:
        errors.append(
                f"'{sensor_type}' is not one of the types the CALCUR header "
                f"accepts ({', '.join(CALCUR_SENSOR_TYPES)}, manual printed "
                "p.173). The manual says an unidentified type is silently "
                "replaced by Diode, so this would install the curve on a "
                "diode input configuration.")
    else:
        full_scale, full_scale_unit, _ = CALCUR_SENSOR_TYPES[sensor_type]
        # The ohms-versus-volts check does not need a full-scale number, so it
        # runs for ACR too, where the AC bridge autoranges and there is none.
        if full_scale_unit:
            if units == 'VOLTS' and full_scale_unit != 'V':
                errors.append(
                    f"The curve is in VOLTS but sensor type {sensor_type} is "
                    "a resistance input.")
            if units in ('OHMS', 'LOGOHM') and full_scale_unit != 'ohm':
                errors.append(
                    f"The curve is in {units} but sensor type "
                    f"{sensor_type} is a voltage input.")


    # Input range headroom. The CALCUR header type does not set a range on
    # this instrument -- SENTYPE:TYPE does -- so the curve's peak resistance
    # is checked against the input type that will be sent afterwards. Where
    # no input type is named, the check is skipped and said to be skipped
    # rather than quietly passed.
    peak_ohms = None
    if units == 'OHMS':
        peak_ohms = stats['r_max']
    elif units == 'LOGOHM':
        peak_ohms = 10.0 ** stats['r_max']
    if peak_ohms is not None:
        stats['peak_ohms'] = peak_ohms
    if peak_ohms is not None and sentype_type in SENTYPE_SENSOR_TYPES:
        range_scale, range_unit, _ = SENTYPE_SENSOR_TYPES[sentype_type]
        if range_unit != 'ohm':
            errors.append(
                f"The curve is in {units} but the input type "
                f"{sentype_type} is not a resistance input. The channel "
                "would be configured for the wrong kind of sensor.")
        elif range_scale is not None:
            stats['full_scale'] = range_scale
            if peak_ohms > range_scale:
                errors.append(
                    f"The curve reaches {peak_ohms:,.1f} ohm but the input "
                    f"type {sentype_type} has a full scale of "
                    f"{range_scale:,.0f} ohm. The coldest part of the curve "
                    "would be off the top of the input range.")
            elif peak_ohms > 0.9 * range_scale:
                warnings.append(
                    f"The curve reaches {peak_ohms:,.1f} ohm, which is "
                    f"{100 * peak_ohms / range_scale:.0f}% of the "
                    f"{range_scale:,.0f} ohm full scale of {sentype_type}. "
                    "There is little headroom below the coldest calibrated "
                    "point.")
    elif peak_ohms is not None and units in ('OHMS', 'LOGOHM'):
        warnings.append(
            f"The curve peaks at {peak_ohms:,.1f} ohm, but no input type was "
            "named, so nothing here has checked that it fits the input "
            "range. Pick an input type (SENTYPE) to have that checked.")

    if units == 'OHMS' and stats['r_max'] > 0 and stats['r_min'] > 0 and \
            stats['r_max'] / stats['r_min'] > 20:
        warnings.append(
            "This resistance curve spans more than a factor of 20. The "
            "manual recommends LOGOHM for curves like this: the instrument "
            "interpolates between breakpoints, and in plain ohms the curve "
            "is steep enough for that to lose accuracy at the cold end.")

    if working_range:
        wanted_low, wanted_high = working_range
        stats['working_range'] = (wanted_low, wanted_high)
        if wanted_low < stats['t_min']:
            warnings.append(
                f"This curve stops at {stats['t_min']:.4g} K, but the sensor "
                f"is to be used down to {wanted_low:.4g} K. Between "
                f"{wanted_low:.4g} K and {stats['t_min']:.4g} K the "
                "instrument has no curve to read: the channel will show a "
                "run of dots, not a temperature. Nothing here can invent "
                "that data. Either use a source file that reaches lower, "
                "have the sensor recalibrated, or use a second thermometer "
                "for the cold end.")
        if wanted_high > stats['t_max']:
            warnings.append(
                f"This curve stops at {stats['t_max']:.4g} K, but the sensor "
                f"is to be used up to {wanted_high:.4g} K. Above "
                f"{stats['t_max']:.4g} K the channel will show a run of "
                "dots, not a temperature.")

    return errors, warnings, stats


def looks_like_factory_entry(name):
    """True if this Master Sensor Table name is a factory curve.

    Deliberately generous. A false positive costs one refused write and a
    question to the operator; a false negative overwrites a factory curve, or
    tries to and is discarded in silence, which is the failure this whole
    version exists to stop.
    """
    text = str(name or '').strip().lower()
    if not text:
        return False
    if USER_SLOT_NAME_RE.match(text):
        return False
    if text in APPENDIX_A_FACTORY_NAMES:
        return True
    return any(marker in text for marker in FACTORY_NAME_MARKERS)


def looks_like_empty_user_slot(name):
    """True if this is an untouched user slot, e.g. 'User Sensor 3'."""
    return bool(USER_SLOT_NAME_RE.match(str(name or '').strip()))


def analyse_sensor_table(entries):
    """Work out the shape of the Master Sensor Table from a scan.

    `entries` is what scan_sensor_table() returns: a list of dicts with
    'index' and 'name', and optionally 'type' and 'multiplier'.

    Returns a dict:
        user_block_start   index of user curve 1, or None if not established
        senix_offset       user_block_start - 1, or None
        factory_indices    every index that looks factory-installed
        user_indices       every index that looks like an untouched user slot
        named_indices      indices holding something that is neither
        confidence         'confirmed' | 'partial' | 'unknown'
        notes              plain-language findings, in reading order

    'confirmed' means a run of untouched user slots was found whose numbers
    count up from 1 in step with their indices. Anything less says so.
    """
    result = {'user_block_start': None, 'senix_offset': None,
              'factory_indices': [], 'user_indices': [], 'named_indices': [],
              'confidence': 'unknown', 'notes': []}
    if not entries:
        result['notes'].append("The scan returned nothing.")
        return result

    numbered = {}
    for entry in entries:
        index, name = entry.get('index'), entry.get('name')
        if index is None or name is None:
            continue
        match = USER_SLOT_NAME_RE.match(str(name).strip())
        if match:
            result['user_indices'].append(index)
            token = match.group(1).upper()
            # User Sensor 1..9 then A, B, C for 10, 11, 12 (Appendix A).
            number = int(token) if token.isdigit() else 10 + ord(token) - 65
            numbered[index] = number
        elif looks_like_factory_entry(name):
            result['factory_indices'].append(index)
        else:
            result['named_indices'].append(index)

    if result['factory_indices']:
        result['notes'].append(
            f"Factory entries at index "
            f"{_render_index_run(result['factory_indices'])}.")
    if result['named_indices']:
        result['notes'].append(
            f"Index {_render_index_run(result['named_indices'])} "
            "hold something that is neither a factory name nor an untouched "
            "user slot. Those are most likely user slots somebody has "
            "already filled, but nothing here can tell that from a factory "
            "curve this module does not recognise, so they are left alone.")

    if numbered:
        # Every untouched slot implies where user curve 1 sits. If they all
        # agree, that is as good as this can get without writing anything.
        implied = {index - number + 1 for index, number in numbered.items()}
        if len(implied) == 1:
            start = implied.pop()
            result['user_block_start'] = start
            result['senix_offset'] = start - 1
            result['confidence'] = 'confirmed'
            result['notes'].append(
                f"User curve 1 is Master Sensor Table index {start}: "
                f"{len(numbered)} untouched user slot(s) all agree, so the "
                f"offset is {start - 1}, not the {SENIX_OFFSET_DEFAULT} "
                "Appendix A gives.")
        else:
            result['confidence'] = 'partial'
            result['notes'].append(
                "The untouched user slots disagree about where the block "
                "starts: they imply " +
                ", ".join(str(value) for value in sorted(implied)) +
                ". Nothing is assumed from that. Read the front panel "
                "Sensors list and say which index is user curve 1.")
    else:
        result['notes'].append(
            "No untouched 'User Sensor n' entry was found anywhere in the "
            "scanned range, so where the user block starts cannot be "
            "established from names alone. Either every slot is already "
            "filled, or this firmware names them differently. Read the "
            "front panel Sensors list before writing anything.")

    if result['senix_offset'] is not None and \
            result['senix_offset'] != SENIX_OFFSET_DEFAULT:
        result['notes'].append(
            f"This does NOT match the manual. Appendix A gives an offset of "
            f"{SENIX_OFFSET_DEFAULT}; this instrument uses "
            f"{result['senix_offset']}. The instrument wins.")
    return result


def _render_index_run(indices):
    """'0-9, 13, 15-17' from a list of integers. Cosmetic only."""
    if not indices:
        return "(none)"
    ordered = sorted(set(indices))
    runs, start, previous = [], ordered[0], ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        runs.append((start, previous))
        start = previous = value
    runs.append((start, previous))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


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


# Half a unit in the last place of a 32-bit float, relative. The manual says
# curve values are stored as 32-bit floats, so nothing read back can be
# closer to what was sent than this.
FLOAT32_HALF_ULP = 2.0 ** -24


def printed_tolerance(text):
    """Half a unit in the last decimal place a number was printed to.

    A readback can only be checked as closely as the instrument prints. If it
    answers '1.6452' for a value sent as '1.64523', the two agree as well as
    that reply can express, and calling it a mismatch would be wrong. If it
    answers '325' for 325.0 K, then this check cannot see an error smaller
    than half a Kelvin, and saying so is more use than a tolerance invented
    here.

    Returns None for exponent notation, where the last-place argument does
    not hold; the caller falls back to a relative tolerance.
    """
    body = text.strip()
    if 'e' in body.lower():
        return None
    decimals = len(body.split('.', 1)[1]) if '.' in body else 0
    return 0.5 * 10.0 ** (-decimals)


def compare_curves(sent_points, read_points, read_texts=None,
                   relative_tolerance=1e-6):
    """Compare the curve that was sent with the curve read back.

    Both lists are sorted by sensor reading before comparing, because the
    instrument sorts the curve itself and stores it that way regardless of
    the order the points arrived in.

    Where `read_texts` is given -- the numerals as the instrument printed
    them, which parse_crv_text() returns in header['point_texts'] -- each
    point is checked against the precision of its own reply rather than
    against a fixed tolerance. This matters in both directions:

      * too tight a fixed tolerance fails a perfectly good curve whenever the
        instrument prints fewer digits than were sent, and a verification
        that cries wolf is one the operator learns to ignore;
      * too loose a fixed tolerance passes a real error. A relative tolerance
        of 1e-4 on a log-ohm value sounds tiny, but at 300 K on this Cernox
        it corresponds to about 0.18 K, which is not a rounding artefact by
        any standard worth applying to a thermometer.

    'worst_reading_limit' and 'worst_temperature_limit' are the loosest
    tolerances that were applied anywhere in the curve, so the caller can say
    how closely the transfer was actually confirmed. Note that a trailing
    zero stripped from an exact value ('325.0' for 325) widens the nominal
    last place without losing anything, so these are a floor on what the
    check could see, not a claim that the instrument is imprecise.
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
            f"{len(sent)} points were sent but {len(read)} came back. The "
            "instrument deletes entries whose numeric fields it cannot "
            "parse, so a shortfall means some lines did not arrive intact.")
        return result

    texts = read_texts if read_texts and len(read_texts) == len(read) else None
    if texts is not None:
        # read_texts arrives in the order the lines were read; the points were
        # sorted, so the texts are sorted the same way to stay paired with
        # them. Sorting on the parsed value keeps the two in step even if the
        # instrument returned the curve in some other order.
        texts = [pair for _, pair in
                 sorted(zip([r for _, r in read_points], read_texts),
                        key=lambda item: item[0])]

    worst_overall = -1.0
    for index in range(len(sent)):
        sent_t, sent_r = sent[index]
        # What went on the wire is fmt6() of these: six significant digits.
        # The floats behind them can carry fifteen -- every point converted
        # from ohms to log ohms does -- so judged against the float, a
        # correctly stored point sits up to half a unit in the sixth digit
        # away from what the instrument echoes, which is more than the
        # tolerance its printed digits allow. The problem line then prints
        # both sides through fmt6() and they look identical. This is what
        # made a good transfer of the extended Cernox curve read as
        # "points differ". Compare against what was actually sent.
        wire_t, wire_r = float(fmt6(sent_t)), float(fmt6(sent_r))
        read_t, read_r = read[index]
        # A point matches if it agrees with what went on the wire OR with
        # the original: the smaller of the two gaps is the real error.
        reading_gap = min(abs(sent_r - read_r), abs(wire_r - read_r))
        temperature_gap = min(abs(sent_t - read_t), abs(wire_t - read_t))

        reading_limit = temperature_gap_limit = None
        if texts is not None:
            reading_limit = printed_tolerance(texts[index][0])
            temperature_gap_limit = printed_tolerance(texts[index][1])
        if reading_limit is None:
            reading_limit = abs(sent_r) * relative_tolerance
        if temperature_gap_limit is None:
            temperature_gap_limit = abs(sent_t) * relative_tolerance
        # The instrument stores 32-bit floats. A firmware that prints more
        # digits than that holds cannot be checked more closely than half
        # a float32 unit in the last place, whatever it printed.
        reading_limit = max(reading_limit, abs(wire_r) * FLOAT32_HALF_ULP)
        temperature_gap_limit = max(temperature_gap_limit,
                                    abs(wire_t) * FLOAT32_HALF_ULP)

        # The loosest tolerance applied anywhere, so the caller can state
        # how closely the curve was actually confirmed instead of implying
        # the comparison was exact.
        result['worst_reading_limit'] = max(
            result['worst_reading_limit'], reading_limit)
        result['worst_temperature_limit'] = max(
            result['worst_temperature_limit'], temperature_gap_limit)

        reading_error = reading_gap / max(abs(sent_r), 1e-12)
        temperature_error = temperature_gap / max(abs(sent_t), 1e-12)
        result['worst_reading_error'] = max(
            result['worst_reading_error'], reading_error)
        result['worst_temperature_error'] = max(
            result['worst_temperature_error'], temperature_error)
        if max(reading_error, temperature_error) > worst_overall:
            worst_overall = max(reading_error, temperature_error)
            result['worst_point'] = index + 1

        if reading_gap > reading_limit or temperature_gap > temperature_gap_limit:
            if len(result['problems']) < 8:
                result['problems'].append(
                    f"Point {index + 1}: sent "
                    f"{fmt6(sent_r)} -> {fmt6(sent_t)} K, "
                    f"read back {fmt6(read_r)} -> {fmt6(read_t)} K.")

    result['matched'] = not result['problems']
    return result


def compare_headers(expected, header):
    """Which of the four CALCUR header fields survived the round trip.

    Returns a dict of field -> (sent, read, matched). The multiplier is
    compared numerically, the three strings case-insensitively after
    stripping, because the instrument is free to echo its own casing.
    """
    fields = {}

    sent_name = str(expected.get('name', '')).strip()
    read_name = str(header.get('name', '')).strip()
    fields['name'] = (sent_name, read_name,
                      sent_name.upper() == read_name.upper())

    sent_type = str(expected.get('sensor_type', '')).strip()
    read_type = str(header.get('sensor_type', '')).strip()
    type_matched = sent_type.upper() == read_type.upper()
    # The Rev 3.03A firmware echoes 'SiDiode' for a header that said
    # 'Diode'. That is the same type in its own spelling, not the silent
    # substitution, which only means something when a NON-diode was sent.
    if (not type_matched and sent_type.lower() == 'diode'
            and read_type.lower() in DIODE_SUBSTITUTION_STRINGS):
        type_matched = True
    fields['sensor_type'] = (sent_type, read_type, type_matched)

    sent_units = str(expected.get('units', '')).strip()
    read_units = str(header.get('units', '')).strip()
    fields['units'] = (sent_units, read_units,
                       sent_units.upper() == read_units.upper())

    sent_mult_text = str(expected.get('multiplier', '')).strip()
    read_mult = header.get('multiplier')
    try:
        sent_mult = float(sent_mult_text)
        matched = (read_mult is not None and
                   abs(float(read_mult) - sent_mult) <= 1e-4)
        sent_shown = f"{sent_mult:+g}"
    except (TypeError, ValueError):
        # An unparseable multiplier is a form problem, not a transfer
        # problem, and the form checks catch it. Do not report it as a
        # mismatch here and send the operator after the wrong thing.
        matched, sent_shown = True, sent_mult_text
    read_shown = f"{float(read_mult):+g}" if read_mult is not None else ""
    fields['multiplier'] = (sent_shown, read_shown, matched)
    return fields


def _classify_points(comparison):
    """The 'points' verdict: header right, points wrong or missing."""
    if comparison['read_count'] != comparison['sent_count']:
        headline = (
            f"The header is correct but {comparison['sent_count']} "
            f"points were sent and {comparison['read_count']} came back.")
        advice = (
            "The header arrived, so the block is being parsed and the "
            "loss is in the point lines themselves. The instrument "
            "deletes entries whose numeric fields it cannot parse. Try "
            "another line ending under Advanced, and raise "
            "CURVE_LINE_GAP_S if this firmware is dropping bytes under "
            "back-to-back traffic.")
    else:
        headline = ("The header is correct but some points differ from "
                    "what was sent.")
        advice = ("The differences are listed above. Do not use this "
                  "sensor: an interpolation table that is wrong in the "
                  "middle gives a plausible temperature that is not the "
                  "right one.")
    return ('points', headline, advice)


def classify_verify(expected, header, comparison, baseline_header=None):
    """Work out WHAT went wrong, not just THAT something did.

    This exists because of the 29 Aug 2026 failure, where the console
    reported '129 points sent, 124 read back' and advised changing the line
    ending. Both were wrong. All four header fields had come back as a
    different curve entirely, which meant the block never landed and the slot
    still held its previous contents. The 124 points were that other curve's
    points, not 129 of ours with five lost, and no line ending would have
    changed anything.

    `baseline_header` is what the slot held BEFORE the send, where the caller
    read it first. When the readback equals the baseline the conclusion is no
    longer an inference at all, and the verdict says so.

    Returns (verdict, headline, advice) where verdict is one of:
        'verified'      everything matched
        'not_written'   the slot holds a different curve; nothing landed
        'type_only'     the block landed, the sensor type was substituted
        'points'        the header is right but points differ or are missing
        'mixed'         some header fields differ, no clean story
    """
    fields = compare_headers(expected, header)
    header_ok = all(matched for _, _, matched in fields.values())
    points_ok = comparison['matched']
    read_type = fields['sensor_type'][1].strip().lower()
    substituted = read_type in DIODE_SUBSTITUTION_STRINGS

    if header_ok and points_ok:
        return ('verified', "", "")

    if header_ok and not points_ok:
        # The header is exactly what was sent, so the block was parsed and
        # landed. Judged before the baseline test: a resend of the same
        # name, type and units into a slot that already held them would
        # otherwise read as "identical to the baseline, nothing written",
        # and a lost point line would be blamed on the header fields.
        return _classify_points(comparison)

    identical_to_baseline = False
    if baseline_header:
        identical_to_baseline = all(
            str(baseline_header.get(key, '')).strip().upper() ==
            str(header.get(key, '')).strip().upper()
            for key in ('name', 'sensor_type', 'units'))

    wholly_different = (not fields['name'][2] and
                        not fields['sensor_type'][2] and
                        not fields['units'][2])

    if identical_to_baseline or wholly_different:
        if identical_to_baseline:
            headline = (
                "NOTHING WAS WRITTEN. The slot reads back exactly as it did "
                "before the send: same name, same type, same units. The "
                "instrument discarded the whole block.")
        else:
            headline = (
                "NOTHING WAS WRITTEN. The name, the sensor type and the "
                "units all came back different, so the slot still holds "
                "another curve entirely and the block was discarded.")
        advice = (
            "The point count is NOT evidence here: those points belong to "
            "the curve that was already in the slot, so a shortfall means "
            "nothing and changing the line ending will not help. Check, in "
            "this order: (1) the sensor type is one the CALCUR header "
            "accepts -- the manual prints "
            + ", ".join(CALCUR_MANUAL_TYPE_LIST) +
            "; this firmware may want its own SENTYPE names instead, and the "
            "type probe (step 4) settles which; (2) the name is 4 to 15 "
            "printable ASCII characters; (3) the units are OHMS, VOLTS or "
            "LOGOHM. Only if all three are already right is the line ending "
            "worth trying.")
        return ('not_written', headline, advice)

    if substituted and fields['name'][2] and fields['units'][2]:
        headline = (
            f"The curve landed, but the sensor type came back as "
            f"'{fields['sensor_type'][1]}' where "
            f"'{fields['sensor_type'][0]}' was sent. The name and the units "
            "survived, so the block was parsed and only the type was "
            "replaced.")
        advice = (
            "That is the documented silent substitution: the firmware could "
            "not identify the type and used a diode instead. A Cernox on a "
            "diode input configuration reads nonsense. Run the type probe "
            "(step 4) to find the spelling this firmware keeps, put that in "
            "the curve type box, and resend; then set the input range "
            "separately with SENTYPE <index>:TYPE R8K10UA.")
        return ('type_only', headline, advice)

    differing = [name for name, (_, _, matched) in fields.items()
                 if not matched]
    headline = ("Header fields that did not survive: " +
                ", ".join(differing) + ".")
    advice = ("This does not match either known failure mode, so nothing "
              "here should be guessed at. Read the slot back with the "
              "inspect button, compare it with the .crv on disk, and check "
              "the raw reply printed above before sending anything else.")
    return ('mixed', headline, advice)


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
# The lab's Cryocon 34 was moved to IEEE address 23 on 3 Sep 2026: 12 is the
# shared factory default of the Cryocon, the Lakeshore 340/350 and the 6221.
# Board-independent hint ("::23::INSTR" matches GPIB0 or GPIB1); *IDN? decides.
CRYOCON_ADDRESS_HINT = "::23::INSTR"
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
# CALCUR? walks flash and the first line can take about twelve seconds on the
# Rev 3.03A unit in this lab (the viewer measured it). Four seconds here made
# the readback "sent nothing back" while the whole curve was still on its way,
# and the reply then arrived as the answer to the next query.
CURVE_READ_TIMEOUT_MS = 20000       # per line of a CALCUR? reply
CURVE_READ_MAX_LINES = MAX_CURVE_POINTS + 12

# How often the window drains the worker-event queue. Fast enough that the
# console keeps up with a curve going out line by line, slow enough to be
# invisible.
EVENT_POLL_MS = 50

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

    def _pace(self, gap=None):
        """Hold a minimum gap between operations.

        The gap is looked up when it is needed, not bound as a default
        argument at import: a default would freeze the module constant at the
        value it had when this file was first read, so slowing the bus down
        for a sulky firmware revision -- or speeding it up under test --
        would silently do nothing.
        """
        gap = CRYOCON_MIN_GAP_S if gap is None else gap
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

    def write_line(self, line, ending, gap=None):
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
        self._pace(CURVE_LINE_GAP_S if gap is None else gap)
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
        # v1.2: the number after CALCUR is no longer assumed to be 1-12. On
        # this firmware it may be a Master Sensor Table index, which runs
        # higher, so the range check is against the table rather than the
        # user-curve count. The caller works out the number; refuse_factory
        # is what stops it being a protected slot.
        if not (0 <= index <= MASTER_TABLE_SCAN_MAX):
            raise ValueError(
                f"CALCUR index must be 0 to {MASTER_TABLE_SCAN_MAX}, "
                f"not {index}.")
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
        if not (0 <= index <= MASTER_TABLE_SCAN_MAX):
            raise ValueError(
                f"CALCUR index must be 0 to {MASTER_TABLE_SCAN_MAX}, "
                f"not {index}.")

        instrument = self.link.instrument
        previous_timeout = instrument.timeout
        collected = []
        try:
            instrument.timeout = CURVE_READ_TIMEOUT_MS
            self.link.write(f"CALCUR? {index}")
            for _ in range(CURVE_READ_MAX_LINES):
                try:
                    chunk = self.link.read_line()
                except Exception as exc:
                    # A timeout AFTER some lines is how a Cryo-con says "that
                    # was the last line"; the caller decides from the text
                    # whether the semicolon came. A timeout before the FIRST
                    # line is not that: nothing has been read, and if the
                    # reply is still on its way it lands on the next query.
                    if not collected:
                        self.log(f"  CALCUR? {index}: no reply within "
                                 f"{CURVE_READ_TIMEOUT_MS / 1000:.0f} s "
                                 f"({type(exc).__name__}).")
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
    def senix_for_user_curve(index, offset=None):
        """Master Sensor Table index for user curve `index`.

        `offset` comes from a scan where one has been run. Where it has not,
        this falls back to the Appendix A value, which this lab's Rev 3.03A
        is known to disagree with, so every caller that uses the fallback
        must say so rather than print the number as if it were established.
        """
        return index + (SENIX_OFFSET_DEFAULT if offset is None else offset)

    def scan_sensor_table(self, limit=MASTER_TABLE_SCAN_MAX, progress=None):
        """Walk the Master Sensor Table and report what is in it.

        READ-ONLY. Three queries per index and not one write: no CALCUR, no
        SENTYPE set, no loop, setpoint, heater or reset command. Safe to run
        with a cryostat controlling.

        CALCUR? is deliberately NOT used here. On this firmware one of those
        takes about twelve seconds, so thirty-two of them is six minutes on
        the bus; SENTYPE? answers in well under a second and gives the name,
        which is what identifies a slot. Read the curve itself afterwards,
        at the one or two indices that matter.

        Returns a list of dicts with 'index', 'name', 'type', 'multiplier'.
        An index that will not answer is included with None fields rather
        than dropped, because a gap in the table is itself informative.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        entries = []
        for index in range(0, limit + 1):
            entry = {'index': index}
            for key, command in (('name', f"SENTYPE? {index}"),
                                 ('type', f"SENTYPE {index}:TYPE?"),
                                 ('multiplier', f"SENTYPE {index}:MULTIPLY?")):
                try:
                    value = self.link.query(command)
                    entry[key] = value if value else None
                except Exception:
                    entry[key] = None
            entries.append(entry)
            if progress:
                progress(index + 1, limit + 1, entry)
        return entries

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

    def set_sensor_type(self, senix, stype):
        """Set the input range and excitation for a user sensor.

        This is the SECOND half of installing a Cernox and it is a different
        command with a different vocabulary from the CALCUR header. The
        manual (printed p.187) lists R16K10UA, R8K10UA, R6K100UA, R2K100UA,
        R625R1MA, R312R1MA, Diode, Snone, TC80 and TC40 here. Table 4
        (printed p.15) gives R8K10UA for a Cernox: 8 kohm full scale, 10 uA.

        Writes, then reads back, then returns what the instrument says. It
        does not judge the answer; the caller compares, because a firmware
        revision that spells its types differently should be reported rather
        than worked around silently.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        if stype not in SENTYPE_SENSOR_TYPES:
            raise ValueError(
                f"'{stype}' is not one of the SENTYPE:TYPE names the manual "
                f"lists ({', '.join(SENTYPE_SENSOR_TYPES)}).")
        self.link.write(f"SENTYPE {senix}:TYPE {stype}")
        time.sleep(CRYOCON_MIN_GAP_S)
        return self.link.query(f"SENTYPE {senix}:TYPE?")

    def probe_calcur_type(self, index, candidates, units, multiplier,
                          points, line_ending, log=None):
        """Find out which header type spelling this firmware actually keeps.

        Sends a MINIMAL two-point curve for each candidate to `index` and
        reads the header back. Two points is the manual's minimum, so each
        round is seven writes rather than a hundred and thirty, and the whole
        probe takes seconds. The two points are the real curve's own ends, so
        the values are in range and in the right units.

        This DOES write, to `index` only, and the caller is responsible for
        having confirmed that index is a user slot. Whatever ends up there is
        rubbish either way and is meant to be overwritten by the real curve
        immediately afterwards.

        Returns a list of (candidate, kept_type, name_survived, units_kept),
        in the order tried.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        ordered = sorted(points, key=lambda pair: pair[1])
        ends = [ordered[0], ordered[-1]]
        results = []
        # A fixed probe name cannot tell "this block landed" from "the
        # previous probe's block is still there", and a discarded resend
        # then reads as ACCEPTED. Two digits from the clock, checked
        # against what the slot holds now, keep every run distinct.
        nonce = int(time.time()) % 90 + 10
        try:
            held, _, _ = self.read_slot_curve(index)
            while held is not None and str(held.get('name', '')).strip() \
                    .startswith(f"P{nonce:02d} "):
                nonce = nonce % 90 + 11
        except Exception:
            pass
        for candidate in candidates:
            probe_name = f"P{nonce:02d} " + candidate[:9]
            lines = build_crv_lines(probe_name, candidate, multiplier,
                                    units, ends)
            if log:
                log(f"  Trying header type '{candidate}' as "
                    f"'{probe_name}' ...")
            self.send_curve(index, lines, line_ending)
            header, _, raw = self.read_slot_curve(index)
            if header is None:
                results.append((candidate, None, False, None))
                if log:
                    log("    nothing readable came back.")
                continue
            kept = header['sensor_type'].strip()
            name_ok = header['name'].strip() == probe_name
            units_kept = header['units'].strip()
            results.append((candidate, kept, name_ok, units_kept))
            if log:
                verdict = ("the block landed" if name_ok
                           else "the block was DISCARDED")
                log(f"    name back as '{header['name'].strip()}', type "
                    f"'{kept}', units '{units_kept}' -- {verdict}.")
        return results

    def read_slot_curve(self, index):
        """Read a slot and parse it. Returns (header, points, raw_text).

        header and points are None when the slot is empty or the reply is not
        a curve; raw_text is always whatever came back, so the caller can
        print it. Used for the before-and-after snapshots that let
        classify_verify() say 'nothing was written' as a fact rather than an
        inference.
        """
        text = self.read_curve(index)
        if not text.strip():
            return None, None, text
        try:
            header, points = parse_crv_text(text, f"slot {index}")
        except CurveFileError:
            return None, None, text
        return header, points, text

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

    PROGRAM_VERSION = "1.3"
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

        # Everything a worker thread wants the window to do goes through
        # this queue and is carried out by _drain_events() on the Tk thread.
        # Tkinter is not thread-safe, and root.after() is not an escape from
        # that: called from a worker it raises 'main thread is not in main
        # loop' unless the main thread happens to be inside mainloop(), so a
        # window driven any other way loses the callback outright. The
        # sibling Cryocon modules queue for the same reason.
        self._events = queue.Queue()
        self.backend = CurveLoaderBackend(log=self.log)
        self.logo_image = None
        self.is_connected = False
        self.busy = False

        # The loaded file, and the curve derived from it.
        self.source = None            # whatever load_sensor_file returned
        self.source_path = ""
        # Optional second file, used only to extend the ends of the first.
        self.second_source = None
        self.second_source_path = ""
        self.merge_notes = []
        self.curve_points = []        # (temperature, reading) in target units
        self.curve_lines = []         # the CALCUR block body
        # v1.2 sensor-table state. senix_offset stays None until a scan has
        # established it, so nothing prints an offset as fact that came from
        # a manual this instrument disagrees with.
        self.table_map = None
        self.senix_offset = None
        self.curve_errors = []
        self.curve_warnings = []
        self.curve_stats = {}
        self.dropped_points = 0
        self.last_saved_path = ""

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

        ttk.Separator(frame, orient='horizontal').grid(
            row=3, column=0, sticky='ew', padx=10, pady=4)
        ttk.Label(
            frame,
            text=("You can use both files together. Pick the .340 above,\n"
                  "then add the .dat here: its measured points BEYOND the\n"
                  "ends of the .340 are added, and nothing inside the .340\n"
                  "is touched. For X17680 that gives the dense certified\n"
                  "table plus reach down to 3.59 K, which is what a CCR\n"
                  "running to 3-5 K needs."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=4, column=0, sticky='w',
                                 padx=10, pady=(4, 4))
        second_row = ttk.Frame(frame)
        second_row.grid(row=5, column=0, sticky='ew', padx=10, pady=(0, 4))
        second_row.grid_columnconfigure(0, weight=1)
        ttk.Button(second_row, text="Extend the ends from another file…",
                   command=self._choose_second_file).grid(
            row=0, column=0, sticky='ew')
        ttk.Button(second_row, text="Clear",
                   command=self._clear_second_file, width=7).grid(
            row=0, column=1, sticky='e', padx=(6, 0))
        self.second_file_label = ttk.Label(
            frame, text="Not using a second file.",
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9, 'italic'),
            wraplength=480, justify='left')
        self.second_file_label.grid(row=6, column=0, sticky='w',
                                    padx=10, pady=(0, 8))

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

        ttk.Label(frame, text="Curve type (CALCUR):").grid(
            row=2, column=0, sticky='w', padx=10, pady=4)
        self.type_var = tk.StringVar(value=CERNOX_DEFAULTS['sensor_type'])
        type_combo = ttk.Combobox(frame, textvariable=self.type_var,
                                  values=list(CALCUR_SENSOR_TYPES),
                                  state='normal')
        type_combo.grid(row=2, column=1, sticky='ew', padx=10, pady=4)
        type_combo.bind('<<ComboboxSelected>>',
                        lambda *_: self._rebuild_curve())
        self.type_var.trace_add('write', lambda *_: self._rebuild_curve())

        self.type_hint = ttk.Label(
            frame, text="", background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9, 'italic'), wraplength=480, justify='left')
        self.type_hint.grid(row=3, column=0, columnspan=2, sticky='w',
                            padx=10, pady=(0, 4))

        ttk.Label(frame, text="Input type (SENTYPE):").grid(
            row=31, column=0, sticky='w', padx=10, pady=4)
        self.sentype_var = tk.StringVar(
            value=CERNOX_DEFAULTS['sentype_type'])
        sentype_combo = ttk.Combobox(
            frame, textvariable=self.sentype_var,
            values=['(leave alone)'] + list(SENTYPE_SENSOR_TYPES),
            state='readonly')
        sentype_combo.grid(row=31, column=1, sticky='ew', padx=10, pady=4)
        # The full-scale headroom check keys on THIS box, so a change here
        # has to re-run the checks like every other header field does.
        # Without it a 1104 ohm curve could be sent under a 625 ohm input.
        sentype_combo.bind('<<ComboboxSelected>>',
                           lambda *_: self._rebuild_curve())
        self.sentype_var.trace_add('write', lambda *_: self._rebuild_curve())
        ttk.Label(
            frame,
            text=("Two different lists, two different commands. The curve\n"
                  "type above goes in the CALCUR header and its list is\n"
                  "Diode, ACR, 31kR, 3.1kR, 312R, 625R, TC80, TC40, None.\n"
                  "The input type here goes in SENTYPE <index>:TYPE and sets\n"
                  "the range and excitation; Table 4 gives R8K10UA for a\n"
                  "Cernox. Sending a SENTYPE name in a CALCUR header is what\n"
                  "makes the instrument silently store a diode instead."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=32, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 6))

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
            text=("Optional. Fill this in and the curve's coverage is\n"
                  "checked against it, so a gap at the cold end is said\n"
                  "here rather than found on the cryostat."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=7, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 6))

        ttk.Label(frame, text="Target table index:").grid(
            row=8, column=0, sticky='w', padx=10, pady=4)
        # v1.3: the target is a Master Sensor Table index, not a user curve
        # number. Both CALCUR? 1 and CALCUR? 15 came back holding the entry
        # at that table index, so the index is what the instrument speaks,
        # and the user-curve number is only a label for it. The list is
        # rebuilt from the scan so the operator picks a slot by its name.
        self.slot_var = tk.StringVar(value="16")
        self.slot_combo = ttk.Combobox(
            frame, textvariable=self.slot_var,
            values=[str(n) for n in range(0, MASTER_TABLE_SCAN_MAX + 1)],
            state='readonly', width=34)
        self.slot_combo.grid(row=8, column=1, sticky='w', padx=10, pady=4)
        self.slot_combo.bind('<<ComboboxSelected>>',
                             lambda *_: self._refresh_slot())

        self.slot_hint = ttk.Label(
            frame,
            text=("Master Sensor Table index. Map the table to see what each "
                  "one holds. Sending overwrites whatever is in it."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9, 'italic'),
            wraplength=480, justify='left')
        self.slot_hint.grid(row=9, column=0, columnspan=2, sticky='w',
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

        ttk.Separator(frame, orient='horizontal').grid(
            row=4, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        ttk.Label(
            frame,
            text=("Appendix A of the manual contradicts itself about where\n"
                  "the user curves live, and this firmware disagrees with\n"
                  "both halves of it. Map the table before writing anything.\n"
                  "This sends queries only: no CALCUR, no SENTYPE set, no\n"
                  "loop, setpoint or heater command. Safe with a cryostat\n"
                  "controlling."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=5, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 4))
        ttk.Button(frame, text="Map the Master Sensor Table (read only)",
                   command=self._scan_table).grid(
            row=6, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        self.table_label = ttk.Label(
            frame, text="Not mapped. The offset below is the manual's guess.",
            font=('Segoe UI', 9, 'italic'), background=self.CLR_FRAME_BG,
            foreground=self.CLR_STATUS_WARN, wraplength=480, justify='left')
        self.table_label.grid(row=7, column=0, columnspan=2, sticky='w',
                              padx=10, pady=(0, 6))

        ttk.Label(
            frame,
            text=("Settled 31 Aug: CALCUR? 1 returned the entry at table\n"
                  "index 1 and CALCUR? 15 returned the entry at index 15, so\n"
                  "the number after CALCUR is a Master Sensor Table index,\n"
                  "not a user curve number. Appendix A is wrong about this.\n"
                  "The target is picked by index in step 2."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=8, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 8))

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

        ttk.Separator(frame, orient='horizontal').grid(
            row=3, column=0, sticky='ew', padx=10, pady=6)
        ttk.Label(
            frame,
            text=("Or run every step in order, each one checked before the\n"
                  "next, stopping at the first failure. This is the one to\n"
                  "use: it reads the slot BEFORE sending, so a readback that\n"
                  "comes back unchanged is reported as 'nothing was written'\n"
                  "rather than as a point mismatch."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=4, column=0, sticky='w',
                                 padx=10, pady=(0, 4))

        self.sequence_assign_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text=("Include the channel step: put the curve on the channel "
                  "chosen in step 6"),
            variable=self.sequence_assign_var).grid(
            row=5, column=0, sticky='w', padx=10, pady=(0, 4))

        ttk.Button(frame,
                   text="Probe which header type this firmware accepts",
                   command=self._probe_type).grid(
            row=55, column=0, sticky='ew', padx=10, pady=(0, 6))
        ttk.Label(
            frame,
            text=("Writes a throwaway two-point curve to the target index,\n"
                  "once per candidate spelling, and reads the header back.\n"
                  "Seconds, not minutes. Overwrite it with the real curve\n"
                  "immediately afterwards."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=56, column=0, sticky='w',
                                 padx=10, pady=(0, 6))

        self.sequence_btn = ttk.Button(
            frame, text="Run the whole sequence and check every step",
            style='Send.TButton', command=self._run_full_sequence)
        self.sequence_btn.grid(row=6, column=0, sticky='ew', padx=10,
                               pady=(0, 6))

        self.progress = ttk.Progressbar(frame, mode='determinate')
        self.progress.grid(row=7, column=0, sticky='ew', padx=10, pady=(0, 8))

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

        ttk.Separator(self.advanced_frame, orient='horizontal').grid(
            row=4, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        ttk.Button(self.advanced_frame,
                   text="Run the built-in checks (no instrument needed)",
                   command=self._run_self_test).grid(
            row=5, column=0, columnspan=2, sticky='ew', padx=10, pady=4)
        ttk.Label(
            self.advanced_frame,
            text=("Twenty-six checks on the file readers, the unit maths, "
                  "the two sensor-type\nlists and the readback classifier, "
                  "run on made-up data. Worth a press after\nany edit to "
                  "this file, and before a session on a cold cryostat."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=6, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 8))

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
                       "At least one built-in check failed. The console "
                       "names them. Do not send a curve until this is "
                       "resolved.")

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
        elif kind == 'table_mapped':
            analysis = event[1]
            self._repopulate_slot_choices()
            self._refresh_slot()
            if analysis['confidence'] == 'confirmed':
                self.table_label.config(
                    text=(f"Mapped. User curve 1 is index "
                          f"{analysis['user_block_start']}, offset "
                          f"{analysis['senix_offset']}."),
                    foreground=self.CLR_STATUS_OK)
            else:
                self.table_label.config(
                    text=("Mapped, but where the user block starts is still "
                          "unsettled. See the console."),
                    foreground=self.CLR_STATUS_WARN)
        elif kind == 'dialog':
            _, level, title, text = event
            {'info': messagebox.showinfo,
             'warning': messagebox.showwarning,
             'error': messagebox.showerror}[level](title, text)

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
        # Disconnect under a running job leaves a curve half written, so the
        # button goes grey with the rest and comes back only if connected.
        try:
            self.disconnect_btn.config(
                state='disabled' if busy or not self.is_connected
                else 'normal')
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
            # The name follows the file unless the operator has typed their
            # own: X17681.340 loaded after X17680.340 must not go into the
            # instrument labelled X17680.
            suggested = self._suggest_name(name, source['meta'])
            current_name = self.name_var.get().strip()
            if not current_name or current_name == getattr(
                    self, '_suggested_name', None):
                self.name_var.set(suggested)
            elif current_name != suggested:
                self.log(f"  The name field still says '{current_name}'; "
                         f"this file would suggest '{suggested}'. Check it.")
            self._suggested_name = suggested
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
                ("Lake Shore 340 breakpoint curve", "*.340"),
                ("Lake Shore table", "*.tbl"),
                ("Cryo-con curve", "*.crv"),
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

    def _apply_cernox_defaults(self):
        """The Cernox settings from the manual, in one press.

        Two fields from two different pages: the CALCUR header type from the
        curve-transfer page, and the input type from Table 4. They are not
        the same name and putting one where the other belongs is what makes
        the instrument store a diode instead.
        """
        self.type_var.set(CERNOX_DEFAULTS['sensor_type'])
        self.multiplier_var.set(CERNOX_DEFAULTS['multiplier'])
        self.units_var.set(CERNOX_DEFAULTS['units'])
        self.sentype_var.set(CERNOX_DEFAULTS['sentype_type'])
        self.log("Applied the manual's Cernox settings: CALCUR header type "
                 f"{CERNOX_DEFAULTS['sensor_type']}, multiplier "
                 f"{CERNOX_DEFAULTS['multiplier']}, "
                 f"{CERNOX_DEFAULTS['units']}; input type "
                 f"{CERNOX_DEFAULTS['sentype_type']} for SENTYPE:TYPE "
                 "(Table 4, 8 kohm full scale, 10 uA excitation).")
        self._rebuild_curve()

    def _update_type_hint(self):
        chosen = self.type_var.get().strip()
        if chosen in CALCUR_SENSOR_TYPES:
            self.type_hint.config(text=CALCUR_SENSOR_TYPES[chosen][2])
        elif chosen in SENTYPE_SENSOR_TYPES:
            self.type_hint.config(
                text=(f"'{chosen}' is a SENTYPE:TYPE name and the CALCUR "
                      "header will not recognise it, so the instrument would "
                      "silently store a diode. Use ACR here for an NTC "
                      f"sensor and put {chosen} in the input type field "
                      "below."))
        else:
            self.type_hint.config(
                text=("Not one of the types the CALCUR header accepts "
                      f"({', '.join(CALCUR_SENSOR_TYPES)}). The manual says "
                      "an unidentified type is silently replaced by Diode."))

    # -----------------------------------------------------------------------
    # WHERE THE SLOT ACTUALLY IS  (v1.2)
    # -----------------------------------------------------------------------

    def _check_target_before_send(self, slot):
        """Refuse a write that the scan says would hit a factory entry.

        Returns the CALCUR index to use, or None if the operator should not
        send. Also warns, but does not block, when no scan has been run:
        blocking there would be wrong, because the scan needs a connection
        and the operator may have mapped the table in an earlier session.
        """
        calcur_index = self._calcur_index_for(slot)
        reason = self._target_is_protected(calcur_index)
        if reason:
            self.log("REFUSED. " + reason)
            self._post('dialog', 'error', "Protected Slot",
                       reason + "\n\nNothing was sent. Map the table, pick "
                       "an index the scan calls an empty user slot, or "
                       "switch how the CALCUR index is interpreted.")
            return None
        if not self.table_map:
            if not messagebox.askyesno(
                    "Table Not Mapped",
                    "The Master Sensor Table has not been mapped in this "
                    "session, so where the user curves live is taken from "
                    "Appendix A, which this firmware is known to disagree "
                    "with.\n\nThe worst case is a write that is silently "
                    "discarded, which is recoverable and is exactly what the "
                    "readback will report.\n\nSend anyway?"):
                self.log("Send cancelled: the table has not been mapped.")
                return None
        return calcur_index

    @staticmethod
    def _slot_index(raw):
        """The table index out of whatever the picker is showing.

        The combobox shows '16 - User Sensor 2 [empty user slot]' once the
        table has been mapped, so the number is taken off the front rather
        than assuming the whole string is one.
        """
        return int(str(raw).strip().split()[0])

    def _senix_for(self, slot):
        """The Master Sensor Table index. In v1.3 that IS the target.

        Earlier versions computed this from a user-curve number and an
        offset. That was the wrong shape: the instrument answers CALCUR? and
        SENTYPE? on the same table index, so the index is the real address
        and the user-curve number is a name for it. Kept as a method so the
        rest of the module reads the same.
        """
        return self._slot_index(slot)

    def _user_curve_number_for(self, index):
        """Which user curve an index is, where the scan established that."""
        if self.senix_offset is None:
            return None
        number = self._slot_index(index) - self.senix_offset
        return number if MIN_USER_CURVE <= number <= MAX_USER_CURVE else None

    def _offset_is_confirmed(self):
        return self.senix_offset is not None

    def _calcur_index_for(self, slot):
        """The number to put after CALCUR for this user curve.

        Two readings of the manual, and the operator picks. 'user' is what
        Appendix A says; 'senix' is what both failed transfers are consistent
        with, where CALCUR 1 addressed a protected factory diode and was
        discarded without a word.
        """
        return self._senix_for(slot)

    def _target_is_protected(self, calcur_index):
        """Why this index must not be written to, or '' if it is fine.

        Checked against the scan, so it can only speak about indices the scan
        covered. An index nothing is known about returns '' and the baseline
        read in the sequence is the next line of defence.
        """
        if not self.table_map:
            return ""
        for entry in self.table_map:
            if entry.get('index') != calcur_index:
                continue
            name = entry.get('name')
            if looks_like_factory_entry(name):
                return (f"Master Sensor Table index {calcur_index} holds "
                        f"'{name}', which is a factory entry. The manual "
                        "(printed p.172) says factory curves cannot be "
                        "changed or deleted by these commands, and this "
                        "firmware discards such a write without reporting "
                        "an error. That is what happened on 29 and 31 "
                        "August.")
            return ""
        return ""

    def _scan_table(self):
        """Map the Master Sensor Table. Queries only, no writes."""
        if not self._require_connection():
            return
        limit = MASTER_TABLE_SCAN_MAX

        def job():
            self.log("")
            self.log(f"Mapping the Master Sensor Table, index 0 to {limit}. "
                     "This sends queries only: no CALCUR, no SENTYPE set, no "
                     "loop, setpoint or heater command.")
            entries = self.backend.scan_sensor_table(
                limit,
                progress=lambda done, total, entry: self._set_progress(
                    done, total))
            self.table_map = entries
            for entry in entries:
                name = entry.get('name')
                if name is None:
                    self.log(f"  {entry['index']:>3}  (no answer)")
                    continue
                mark = "factory" if looks_like_factory_entry(name) else \
                       ("empty user slot"
                        if looks_like_empty_user_slot(name) else "in use")
                self.log(f"  {entry['index']:>3}  {name:<16} "
                         f"type={entry.get('type')}  "
                         f"mult={entry.get('multiplier')}   [{mark}]")
            analysis = analyse_sensor_table(entries)
            self.log("")
            for note in analysis['notes']:
                self.log(f"  {note}")
            self.senix_offset = analysis['senix_offset']
            self._post('table_mapped', analysis)

        self._run_in_worker("Mapping the sensor table", job)

    def _probe_type(self):
        """Settle the header-type question against the instrument."""
        if not self.curve_points:
            messagebox.showerror(
                "Nothing Loaded",
                "Load the sensor file first. The probe uses that curve's own "
                "end points so the values it sends are in range.")
            return
        if not self._require_connection():
            return
        index = self._check_target_before_send(
            self.slot_var.get())
        if index is None:
            return
        candidates = list(CALCUR_TYPE_CANDIDATES)
        if not messagebox.askyesno(
                "Probe the header type?",
                f"This writes a throwaway two-point curve to table index "
                f"{index}, once for each of {', '.join(candidates)}, and "
                "reads the header back each time.\n\nWhichever spelling "
                "comes back with its name intact is the one this firmware "
                "accepts. Whatever is left in the slot afterwards is "
                "rubbish and must be overwritten by the real curve.\n\n"
                "No loop, setpoint, heater or reset command is sent.\n\n"
                "Go ahead?"):
            self.log("Type probe cancelled.")
            return

        units = self.units_var.get().strip()
        try:
            multiplier = float(self.multiplier_var.get())
        except ValueError:
            multiplier = -1.0
        points = list(self.curve_points)
        ending = LINE_ENDINGS[self.ending_var.get()]

        def job():
            self.log("")
            self.log(f"Probing which CALCUR header type index {index} "
                     "accepts.")
            results = self.backend.probe_calcur_type(
                index, candidates, units, multiplier, points, ending,
                log=self.log)
            landed = [row for row in results if row[2]]
            self.log("")
            self.log("PROBE RESULT")
            for candidate, kept, name_ok, units_kept in results:
                mark = "ACCEPTED" if name_ok else "discarded"
                self.log(f"  {candidate:<10} -> {mark}"
                         + (f", stored as type '{kept}', units "
                            f"'{units_kept}'" if kept else ""))
            if not landed:
                self.log("  None of them landed. The header type is not the "
                         "obstacle: the block is being rejected for some "
                         "other reason, or this index cannot be written. Try "
                         "another index the scan calls an empty user slot, "
                         "and if that also fails, try a different line "
                         "ending under Advanced.")
                self._post('dialog', 'warning', "No Candidate Accepted",
                           "None of the header types landed. The console "
                           "says what to try next. Nothing usable is in the "
                           "slot.")
                return
            # A block that landed with its type substituted is not a win:
            # prefer the candidate the firmware kept as sent, and fall back
            # to the first that landed only if none did.
            best = next((row for row in landed
                         if str(row[1]).strip().upper() == row[0].upper()),
                        landed[0])
            self.log(f"  Use '{best[0]}' in the curve type box. The "
                     f"instrument stored it as '{best[1]}'.")
            if best[1] and best[1].strip().lower() in \
                    DIODE_SUBSTITUTION_STRINGS and \
                    best[0].strip().lower() not in ('diode',):
                self.log("  Note: the block landed but the type was still "
                         "substituted for a diode, so the curve would be "
                         "stored on a diode input configuration. Set the "
                         "input type with SENTYPE afterwards and check it "
                         "reads back.")
            self._post('dialog', 'info', "Probe Complete",
                       f"'{best[0]}' is accepted by this firmware.\n\n"
                       f"Put it in the curve type box, then send the real "
                       f"curve to index {index} to overwrite the probe "
                       "leftovers.")

        self._run_in_worker("Probing the header type", job)

    def _repopulate_slot_choices(self):
        """Show each table index with what it holds, so the pick is informed.

        Factory entries stay in the list rather than being hidden. Hiding
        them would make an operator wonder where index 1 went; showing them
        marked, and refusing the write, says why.
        """
        if not self.table_map:
            return
        values = []
        for entry in self.table_map:
            name = entry.get('name')
            if name is None:
                continue
            mark = ("factory" if looks_like_factory_entry(name) else
                    "empty user slot" if looks_like_empty_user_slot(name)
                    else "in use")
            values.append(f"{entry['index']}  {name}  [{mark}]")
        if values:
            self.slot_combo['values'] = values

    def _refresh_slot(self):
        try:
            slot = self._slot_index(self.slot_var.get())
        except (ValueError, IndexError):
            return
        senix = calcur = slot
        held, kind = "", ""
        for entry in (self.table_map or []):
            if entry.get('index') == slot:
                held = entry.get('name') or "(no answer)"
                kind = ("a FACTORY entry, which cannot be written"
                        if looks_like_factory_entry(held) else
                        "an untouched user slot"
                        if looks_like_empty_user_slot(held) else
                        "already holding a curve somebody put there")
                break
        number = self._user_curve_number_for(slot)
        label = (f" (user curve {number})" if number else "")
        if held:
            text = (f"CALCUR {calcur} addresses table index {senix}{label}, "
                    f"which currently holds '{held}': {kind}. Sending "
                    "overwrites it.")
        else:
            text = (f"CALCUR {calcur} addresses table index {senix}{label}. "
                    "The table has not been mapped, so nothing here knows "
                    "what is in it. Map it before sending.")
        self.slot_hint.config(text=text)

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
        self.merge_notes = []

        if not self.source:
            self._render_summary()
            return

        target_units = self.units_var.get()
        try:
            points = convert_units(self.source['points'],
                                   self.source['units'], target_units)
            if self.second_source:
                points, added, self.merge_notes = extend_curve(
                    points, self.second_source['points'],
                    target_units, self.second_source['units'])
                second_name = os.path.basename(self.second_source_path)
                if added:
                    self.second_file_label.config(
                        text=(f"{second_name}: {len(added)} point(s) added "
                              f"beyond the ends of the main curve."))
                else:
                    self.second_file_label.config(
                        text=(f"{second_name}: adds nothing, every point is "
                              "inside the main curve."))
        except (CurveFileError, OverflowError) as exc:
            self.curve_errors = [str(exc) or "A reading is too large to "
                                 "convert between OHMS and LOGOHM."]
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
            points, target_units, sensor_type, multiplier, name,
            working_range=self._working_range(),
            sentype_type=self.sentype_var.get().strip())
        for note in self.merge_notes:
            warnings.append(note)
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
        if self.busy:
            self.log("Not scanning: an instrument operation is still "
                     "running. A scan would open a second session to the "
                     "same address in the middle of it.")
            return
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
        if self.busy:
            self.log("Not connecting: an instrument operation is still "
                     "running.")
            return
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
        if self.busy:
            self.log("Not disconnecting: an instrument operation is still "
                     "running. Closing the session under it would leave a "
                     "curve half written. Wait for it to finish.")
            return
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
                # The message is copied out of the exception here, on this
                # thread, and the traceback is formatted here too. Python
                # unbinds the name in 'except ... as exc' when the block ends,
                # so a lambda that closed over 'exc' and ran later on the Tk
                # thread would raise NameError instead of showing the dialog,
                # losing the very report it was meant to deliver. Same reason
                # the traceback is formatted here: format_exc() on the Tk
                # thread has no live exception and prints 'NoneType: None'.
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
    # STEP 5: SEND AND VERIFY
    # -----------------------------------------------------------------------

    def _inspect_slot(self):
        if not self._require_connection():
            return
        slot = self._slot_index(self.slot_var.get())

        def job():
            senix = self._senix_for(slot)
            self.log(f"Reading user curve slot {slot} "
                     f"(sensor table index {senix}) ...")
            entry = self.backend.get_sensor_table_entry(senix)
            for key in ('name', 'type', 'multiplier'):
                self.log(f"  SENTYPE {key}: {entry.get(key)}")
                if entry.get(f"{key}_error"):
                    self.log(f"    ({entry[f'{key}_error']})")
            text = self.backend.read_curve(self._calcur_index_for(slot))
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

        slot = self._slot_index(self.slot_var.get())
        senix = self._senix_for(slot)
        calcur_index = self._check_target_before_send(slot)
        if calcur_index is None:
            return
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
        # Snapshot on this, the Tk thread. See _expected_header().
        expected = self._expected_header()

        def job():
            # Snapshot the slot first. Without it a readback that comes back
            # as somebody else's curve can only be inferred to mean the send
            # failed; with it, that is a comparison. It costs one query.
            baseline_header = None
            try:
                baseline_header, baseline_points, _ = \
                    self.backend.read_slot_curve(calcur_index)
            except Exception as exc:
                self.log(f"  Could not read the slot before sending: {exc}. "
                         "Carrying on; the check afterwards will be a little "
                         "less definite.")
            if baseline_header is not None:
                self.log(f"  Slot {slot} currently holds "
                         f"'{baseline_header['name']}', "
                         f"{baseline_header['sensor_type']}, "
                         f"{baseline_header['units']}, "
                         f"{len(baseline_points)} points. That is what is "
                         "about to be overwritten.")
            self.log(f"Sending {len(lines) + 1} lines to user curve {slot} "
                     f"(CALCUR {calcur_index}) ...")
            self.backend.send_curve(
                calcur_index, lines, ending,
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
                self._verify_against(points, slot, expected,
                                     baseline_header=baseline_header)
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
        slot = self._slot_index(self.slot_var.get())
        points = list(self.curve_points)
        expected = self._expected_header()
        self._run_in_worker(
            "Reading the curve back",
            lambda: self._verify_against(points, slot, expected))

    def _expected_header(self):
        """The four header fields, read off the form. MAIN THREAD ONLY.

        Tk variables belong to the thread running the event loop: reading one
        from a worker raises 'main thread is not in main loop'. The
        verification runs on a worker, so it is handed a plain dict captured
        here instead of reaching back into the widgets. Snapshotting also
        means the check compares against what was actually sent, not against
        whatever the operator has since typed into the form.
        """
        return {
            'name': self.name_var.get().strip(),
            'sensor_type': self.type_var.get().strip(),
            'multiplier': self.multiplier_var.get().strip(),
            'units': self.units_var.get().strip(),
            'sentype_type': self.sentype_var.get().strip(),
        }

    def _verify_against(self, sent_points, slot, expected,
                        baseline_header=None):
        """Read the slot back and compare it with what was sent.

        Runs on the worker thread, so it touches no widget: `expected` is the
        snapshot taken by _expected_header() before the job started.

        `baseline_header` is what the slot held BEFORE the send, where the
        caller took that snapshot. With it, "nothing was written" stops being
        an inference and becomes a comparison.

        Returns the verdict string from classify_verify() so a caller running
        a sequence can decide whether to carry on.
        """
        senix = self._senix_for(slot)
        index = self._calcur_index_for(slot)
        self.log(f"Reading table index {index} back with "
                 f"CALCUR? {index} ...")
        text = self.backend.read_curve(self._calcur_index_for(slot))
        if not text.strip():
            self.log("VERIFY FAILED: the instrument sent nothing back. The "
                     "curve may not have been stored. Check the front panel "
                     "(Sensors key) before using this sensor.")
            self._post('dialog', 'error', "Curve Not Verified",
                       f"CALCUR? {index} answered nothing. The curve may not "
                       "have been stored. Nothing has confirmed what the "
                       "instrument holds; check the front panel (Sensors "
                       "key) before using this sensor.")
            return 'empty'
        try:
            header, read_points = parse_crv_text(text, f"slot {slot}")
        except CurveFileError as exc:
            self.log(f"VERIFY FAILED: the reply could not be read as a "
                     f"curve: {exc}")
            self.log(f"  Raw reply, first 400 characters:\n{text[:400]}")
            self._post('dialog', 'error', "Curve Not Verified",
                       f"CALCUR? {index} answered, but not with a readable "
                       f"curve: {exc}\n\nThe raw reply is in the console. "
                       "Nothing has confirmed what the instrument holds.")
            return 'unreadable'

        self.log(f"  The instrument reports: name '{header['name']}', type "
                 f"'{header['sensor_type']}', multiplier "
                 f"{header['multiplier']:+g}, units {header['units']}.")

        # Field by field, so the console says which ones survived rather than
        # only that something did not.
        fields = compare_headers(expected, header)
        for label, key in (("name", 'name'), ("sensor type", 'sensor_type'),
                           ("multiplier", 'multiplier'), ("units", 'units')):
            sent_value, read_value, matched = fields[key]
            mark = "ok  " if matched else "DIFF"
            self.log(f"  [{mark}] {label}: sent '{sent_value}', "
                     f"read '{read_value}'")

        # Checked against the precision the instrument printed, not against a
        # tolerance chosen here; see compare_curves().
        comparison = compare_curves(sent_points, read_points,
                                    read_texts=header.get('point_texts'))
        self.log(f"  {comparison['sent_count']} points sent, "
                 f"{comparison['read_count']} read back.")
        if comparison['read_count'] == comparison['sent_count']:
            self.log("  Largest difference in any sensor reading: "
                     f"{comparison['worst_reading_error']:.2e} relative; "
                     "in any temperature: "
                     f"{comparison['worst_temperature_error']:.2e} "
                     f"(worst at point {comparison['worst_point']}).")
            self.log(
                "  Each point was compared at the precision its own reply "
                "was printed to; the loosest that got anywhere in this "
                f"curve was {comparison['worst_temperature_limit']:.3g} K "
                "in temperature and "
                f"{comparison['worst_reading_limit']:.3g} in the sensor "
                "reading.")

        verdict, headline, advice = classify_verify(
            expected, header, comparison, baseline_header=baseline_header)

        if verdict == 'verified':
            self.log(f"VERIFIED. User curve {slot} on the instrument matches "
                     "what was sent: every point, the name, the sensor type, "
                     "the multiplier and the units.")
            self.log(f"  To use it, set an input channel to sensor index "
                     f"{senix} (step 6, or the Sensors key on the front "
                     "panel).")
            caveat = (
                "\n\nEach point was compared at the precision its own reply "
                f"was printed to, the loosest being "
                f"{comparison['worst_temperature_limit']:.3g} K.")
            self._post('dialog', 'info', "Curve Verified",
                       f"User curve {slot} matches what was sent, point for "
                       f"point.\n\nSet an input channel to sensor index "
                       f"{senix} to use it.{caveat}")
            return verdict

        # Point-level differences are only worth printing when the header
        # matched. When the slot holds another curve entirely they are a list
        # of differences between two unrelated tables, which reads like
        # evidence and is not.
        if verdict != 'not_written':
            for message in comparison['problems']:
                self.log(f"  {message}")

        self.log("VERIFY FAILED. " + headline)
        self.log("  " + advice)
        self.log("  Do not use this sensor until this is resolved.")
        self._post(
            'dialog', 'error', "Curve Not Verified",
            f"User curve {slot} does not match what was sent.\n\n"
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
        buttons is the BASELINE. Reading the slot before the send is what
        turns "the readback does not match" into "the slot is unchanged, so
        nothing was written", and those two call for opposite actions.

        Order:
            1  identify the instrument and confirm it is a Cryo-con
            2  read the target slot as it stands now, and keep it
            3  read the sensor-table entry for the same slot
            4  send the CALCUR block
            5  read it back and classify what happened
            6  set SENTYPE <senix>:TYPE and confirm it took
            7  optionally put the curve on a channel and read a temperature

        Any step that fails stops the sequence. Steps 6 and 7 are skipped
        when 5 did not verify, because configuring an input range for a curve
        that is not there would only make the fault harder to see.
        """
        if not self.curve_lines:
            messagebox.showerror(
                "Nothing to Send",
                "There is no valid curve yet. Load a file and clear the "
                "problems listed on the right first.")
            return
        if not self._require_connection():
            return

        slot = self._slot_index(self.slot_var.get())
        senix = self._senix_for(slot)
        calcur_index = self._check_target_before_send(slot)
        if calcur_index is None:
            return
        expected = self._expected_header()
        sentype_type = expected['sentype_type']
        assign = self.sequence_assign_var.get()
        channel = self.channel_var.get()
        wants_sentype = sentype_type in SENTYPE_SENSOR_TYPES

        warning_text = ""
        if self.curve_warnings:
            warning_text = ("\n\nWarnings on this curve:\n  - " +
                            "\n  - ".join(self.curve_warnings))
        steps = [
            "1  identify the instrument",
            f"2  read CALCUR {calcur_index} as it stands now, and keep it "
            "as a baseline",
            f"3  read sensor-table entry {senix} and check it is not a "
            "factory slot",
            f"4  send the curve to CALCUR {calcur_index}",
            "5  read it back and compare, field by field and point by point",
        ]
        if wants_sentype:
            steps.append(f"6  set SENTYPE {senix}:TYPE {sentype_type} "
                         "and confirm")
        if assign:
            steps.append(f"7  set input {channel} to sensor index {senix} "
                         "and read a temperature")
        if not messagebox.askyesno(
                "Run the whole sequence?",
                f"This overwrites user curve {slot} (sensor table index "
                f"{senix}) on the Cryocon.\n\n" +
                "\n".join(steps) +
                f"\n\nName:       {expected['name']}"
                f"\nCurve type: {expected['sensor_type']}"
                f"\nMultiplier: {expected['multiplier']}"
                f"\nUnits:      {expected['units']}"
                f"\nPoints:     {len(self.curve_points)}\n\n"
                "No loop, setpoint, heater or reset command is sent at any "
                "point."
                f"{warning_text}\n\nRun it?"):
            self.log("Sequence cancelled.")
            return

        lines = list(self.curve_lines)
        points = list(self.curve_points)
        ending = LINE_ENDINGS[self.ending_var.get()]
        also_name = self.set_name_var.get()

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
                        f"User curve {slot} is installed and verified, and "
                        "every step in the sequence passed. The console has "
                        "the record.")

            # -- 1: identify -------------------------------------------------
            self.log("")
            self.log("=" * 62)
            self.log(f"SEQUENCE START  ·  user curve {slot}  ·  "
                     f"CALCUR {calcur_index}  ·  sensor table index {senix}"
                     + ("" if self._offset_is_confirmed()
                        else "  (offset UNCONFIRMED)"))
            self.log("=" * 62)
            try:
                idn = self.backend.link.query('*IDN?')
            except Exception as exc:
                record(1, "identify the instrument", False,
                       f"*IDN? failed: {exc}")
                return finish()
            if not is_cryocon_idn(idn):
                record(1, "identify the instrument", False,
                       f"'{idn}' is not a Cryo-con; refusing to send a curve")
                return finish()
            record(1, "identify the instrument", True, idn)

            # -- 2: baseline -------------------------------------------------
            try:
                baseline_header, baseline_points, baseline_text = \
                    self.backend.read_slot_curve(calcur_index)
            except Exception as exc:
                record(2, f"read CALCUR {calcur_index} baseline", False, str(exc))
                return finish()
            if baseline_header is None:
                if baseline_text.strip():
                    detail = ("the slot replied but not with a readable "
                              "curve; raw reply kept in the console")
                    self.log(f"  Raw reply, first 400 characters:\n"
                             f"{baseline_text[:400]}")
                else:
                    detail = "the slot is empty"
                record(2, f"read CALCUR {calcur_index} baseline", True, detail)
            else:
                detail = (f"holds '{baseline_header['name']}', "
                          f"{baseline_header['sensor_type']}, "
                          f"{baseline_header['units']}, "
                          f"{len(baseline_points)} points")
                record(2, f"read CALCUR {calcur_index} baseline", True, detail)
                self.log("  This is what will be overwritten. If the "
                         "readback in step 5 still shows it, the send did "
                         "not take.")

            # -- 3: sensor table entry --------------------------------------
            entry = self.backend.get_sensor_table_entry(senix)
            rendered = ", ".join(
                f"{key}={entry.get(key)}"
                for key in ('name', 'type', 'multiplier'))
            if looks_like_factory_entry(entry.get('name')):
                record(3, f"read sensor-table entry {senix}", False,
                       f"{rendered}. That is a FACTORY entry, not user curve "
                       f"{slot}. Appendix A is wrong about this instrument. "
                       "Map the table and use the index the scan calls an "
                       "empty user slot. Nothing was written.")
                return finish()
            record(3, f"read sensor-table entry {senix}", True, rendered)

            # -- 4: send -----------------------------------------------------
            try:
                self.log(f"Sending {len(lines) + 1} lines to user curve "
                         f"{slot} (CALCUR {calcur_index}) ...")
                self.backend.send_curve(
                    calcur_index, lines, ending,
                    progress=lambda done, total, line: self._set_progress(
                        done, total))
            except Exception as exc:
                record(4, f"send the curve to CALCUR {calcur_index}", False, str(exc))
                return finish()
            record(4, f"send the curve to CALCUR {calcur_index}", True,
                   f"{len(lines) + 1} lines, then {CURVE_SETTLE_S:.1f} s for "
                   "the flash write")

            if also_name:
                try:
                    reported = self.backend.set_sensor_name(
                        senix, expected['name'])
                    self.log(f"  SENTYPE {senix}:NAME set; the instrument "
                             f"now reports '{reported}'.")
                except Exception as exc:
                    self.log(f"  SENTYPE {senix}:NAME was refused: {exc}. "
                             "The name in the CALCUR header still stands.")

            # -- 5: verify ---------------------------------------------------
            verdict = self._verify_against(points, slot, expected,
                                           baseline_header=baseline_header)
            if verdict != 'verified':
                record(5, "read the curve back and compare", False,
                       f"verdict: {verdict}")
                return finish()
            record(5, "read the curve back and compare", True,
                   "every field and every point")

            # -- 6: input type ----------------------------------------------
            if wants_sentype:
                try:
                    reported = self.backend.set_sensor_type(
                        senix, sentype_type)
                except Exception as exc:
                    record(6, f"set SENTYPE {senix}:TYPE {sentype_type}",
                           False, str(exc))
                    return finish()
                if str(reported).strip().upper() != sentype_type.upper():
                    record(6, f"set SENTYPE {senix}:TYPE {sentype_type}",
                           False,
                           f"read back as '{reported}'. This firmware does "
                           "not accept that name. The curve is installed and "
                           "correct; set the input type from the front panel "
                           "(Sensors key) before using the sensor.")
                    return finish()
                record(6, f"set SENTYPE {senix}:TYPE {sentype_type}", True,
                       f"read back as '{reported}'")

            # -- 7: channel --------------------------------------------------
            if assign:
                try:
                    reported = self.backend.assign_curve_to_channel(
                        channel, senix)
                except Exception as exc:
                    record(7, f"set input {channel} to index {senix}", False,
                           str(exc))
                    return finish()
                try:
                    matched = int(float(reported)) == senix
                except (TypeError, ValueError):
                    matched = False
                if not matched:
                    record(7, f"set input {channel} to index {senix}", False,
                           f"the channel reads back as '{reported}', not "
                           f"{senix}. This firmware may number user curves "
                           "differently from the Edition 4 manual; it also "
                           "answers ISENIX and USENIX. Nothing further was "
                           "sent. Set the sensor from the front panel.")
                    return finish()
                reading = self.backend.read_channel_temperature(channel)
                note = self._describe_reading(reading)
                record(7, f"set input {channel} to index {senix}", True,
                       f"reads {reading}{note}")

            finish()

        self._run_in_worker("Running the sequence", job)

    @staticmethod
    def _describe_reading(reading):
        """Turn a channel reply into a short plain-language note, or ''."""
        text = str(reading).strip()
        for marker, meaning in CRYOCON_STATUS_STRINGS.items():
            if marker in text:
                return f"  ({meaning})"
        return ""

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
        slot = self._slot_index(self.slot_var.get())
        senix = self._senix_for(slot)
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
                self._post('dialog', 'warning', "Assignment Not Confirmed",
                           f"Input {channel} reads back as {reported}, not "
                           f"{senix}.\n\nNo further command was sent. Set "
                           "the sensor from the front panel instead.")
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


# ===============================================================================
# OFFLINE SELF-TEST
# ===============================================================================
#
# Everything here runs on made-up data with no instrument, no VISA and no Tk,
# so it can be run on the measurement PC before a session or anywhere else:
#
#     python Sensor_Curve_Loader_CC34_GUI.py --selftest
#
# It is not a substitute for tests/test_cryocon_curve_loader.py; it is the
# subset that has to hold before this module is allowed near the Cryocon, and
# it exists mainly so the 29 Aug 2026 failure cannot come back unnoticed.
# Cases 9, 10, 14 and 15 are that regression, written as tests.


def _selftest_cases():
    """Yield (name, callable) pairs. Each callable raises on failure."""

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    # -- 1: six significant digits, never in exponent form -------------------
    def case_fmt6():
        check(fmt6(1.6452312) == "1.64523", fmt6(1.6452312))
        check(fmt6(325.0) == "325.0", fmt6(325.0))
        check(fmt6(-1.0) == "-1.0", fmt6(-1.0))
        check('e' not in fmt6(1.23e-7).lower(), fmt6(1.23e-7))
        check('.' in fmt6(4), fmt6(4))

    # -- 2: a Lake Shore .340 is already reading-first -----------------------
    def case_parse_340():
        text = ("Sensor Model:   CX-1030-SD-4L\n"
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
        points, units, meta = _parse_lakeshore_340(
            _clean_lines(text), "X17680.340")
        check(units == 'LOGOHM', units)
        check(len(points) == 3, len(points))
        check(abs(points[0][0] - 325.0) < 1e-9, points[0])
        check(abs(points[0][1] - 1.64523) < 1e-9, points[0])
        check(meta['serial'] == 'X17680', meta)
        check(meta['stated_multiplier'] == -1.0, meta)

    # -- 3: a Lake Shore .dat is temperature-first and gets swapped ----------
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

    # -- 5: OHMS and LOGOHM interconvert, VOLTS never -----------------------
    def case_units():
        converted = convert_units([(4.0, 100.0)], 'OHMS', 'LOGOHM')
        check(abs(converted[0][1] - 2.0) < 1e-12, converted)
        back = convert_units(converted, 'LOGOHM', 'OHMS')
        check(abs(back[0][1] - 100.0) < 1e-9, back)
        try:
            convert_units([(4.0, 100.0)], 'OHMS', 'VOLTS')
        except CurveFileError:
            return
        raise AssertionError("ohms were converted to volts")

    # -- 6: thinning keeps both ends and invents nothing --------------------
    def case_thin():
        original = [(float(i), float(i)) for i in range(1, 51)]
        thinned, dropped = thin_points(original, 10)
        check(len(thinned) == 10, len(thinned))
        check(thinned[0] == original[0], thinned[0])
        check(thinned[-1] == original[-1], thinned[-1])
        check(dropped == 40, dropped)
        check(all(point in original for point in thinned), "invented a point")

    # -- 7: extending adds only beyond the ends -----------------------------
    def case_extend():
        primary = [(10.0, 1.0), (20.0, 2.0), (30.0, 3.0)]
        extra = [(5.0, 0.5), (25.0, 2.5), (40.0, 4.0)]
        merged, added, _ = extend_curve(primary, extra, 'LOGOHM', 'LOGOHM')
        check(len(added) == 2, added)
        check(all(t in (5.0, 40.0) for t, _ in added), added)
        check(len(merged) == 5, merged)

    # -- 8: a built block parses back to the same numbers -------------------
    def case_round_trip():
        points = [(325.0, 1.64523), (100.0, 2.0), (4.0, 2.94699)]
        lines = build_crv_lines("CX1030 X17680", "ACR", -1.0,
                                "LOGOHM", points)
        header, read_back = parse_crv_text("\n".join(lines) + "\n", "test")
        check(header['name'] == "CX1030 X17680", header)
        check(header['sensor_type'] == "ACR", header)
        check(header['units'] == "LOGOHM", header)
        check(len(read_back) == 3, read_back)
        comparison = compare_curves(points, read_back,
                                    read_texts=header['point_texts'])
        check(comparison['matched'], comparison['problems'])

    # -- 9: a SENTYPE name in a CALCUR header warns, and does not block ------
    def case_sentype_name_warns():
        # v1.2 made this an error on the strength of the manual. The 31 Aug
        # scan showed the firmware's own vocabulary IS the R-names, and that
        # the 29 Aug failure was a protected slot, not the type. So it warns
        # and says the question is open, rather than deciding it.
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        errors, warnings, _ = analyse_curve(points, 'LOGOHM', 'R8K10UA', -1.0,
                                            "CX1030 X17680")
        check(not errors, errors)
        check(any('not settled' in message for message in warnings), warnings)
        # a name in neither list is still an error
        errors, _, _ = analyse_curve(points, 'LOGOHM', 'NOSUCHTYPE', -1.0,
                                     "CX1030 X17680")
        check(errors, "an unknown type was accepted")

    # -- 10: REGRESSION. ACR is accepted for a log-ohm NTC curve ------------
    def case_acr_accepted():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        errors, _, _ = analyse_curve(points, 'LOGOHM', 'ACR', -1.0,
                                     "CX1030 X17680")
        check(not errors, errors)

    # -- 10b: the input-range check now keys on the SENTYPE type ------------
    def case_range_headroom():
        # X17681's cold end, 3.04306 logohm = 1104 ohm, fits 8 kohm.
        points = [(325.0, 1.65330), (4.0, 3.04306)]
        errors, warnings, stats = analyse_curve(
            points, 'LOGOHM', 'ACR', -1.0, "CX1030 X17681",
            sentype_type='R8K10UA')
        check(not errors, errors)
        check(abs(stats['peak_ohms'] - 1104.23) < 0.5, stats)
        # the same curve on a 625 ohm input is off the top of the range
        errors, _, _ = analyse_curve(points, 'LOGOHM', 'ACR', -1.0,
                                     "CX1030 X17681",
                                     sentype_type='R625R1MA')
        check(any('full scale' in message for message in errors), errors)
        # and with no input type named, the gap is stated, not passed silently
        _, warnings, _ = analyse_curve(points, 'LOGOHM', 'ACR', -1.0,
                                       "CX1030 X17681")
        check(any('no input type was named' in message
                  for message in warnings), warnings)

    # -- 11: a volts curve on a resistance type is an error -----------------
    def case_units_type_clash():
        points = [(325.0, 0.5), (4.0, 1.6)]
        errors, _, _ = analyse_curve(points, 'VOLTS', 'ACR', -1.0,
                                     "Some Diode")
        check(any('VOLTS' in message for message in errors), errors)

    # -- 12: a positive multiplier on an NTC curve is an error --------------
    def case_multiplier_sign():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        errors, _, _ = analyse_curve(points, 'LOGOHM', 'ACR', +1.0,
                                     "CX1030 X17680")
        check(any('multiplier' in message for message in errors), errors)

    # -- 13: a curve that stops short of the working range warns ------------
    def case_coverage():
        points = [(325.0, 1.64523), (4.0, 2.94699)]
        _, warnings, _ = analyse_curve(points, 'LOGOHM', 'ACR', -1.0,
                                       "CX1030 X17680",
                                       working_range=(2.0, 300.0))
        check(any('2 K' in message or '2.0' in message or 'dots' in message
                  for message in warnings), warnings)

    # -- 14: REGRESSION. All three fields different means nothing landed ----
    def case_classify_not_written():
        expected = {'name': 'CX1030 X17680', 'sensor_type': 'ACR',
                    'multiplier': '-1.0', 'units': 'LOGOHM'}
        header = {'name': 'Lakeshore 10', 'sensor_type': 'SiDiode',
                  'multiplier': -1.0, 'units': 'VOLTS'}
        comparison = {'matched': False, 'sent_count': 129, 'read_count': 124,
                      'problems': []}
        verdict, headline, advice = classify_verify(expected, header,
                                                    comparison)
        check(verdict == 'not_written', verdict)
        check('line ending' in advice, advice)
        check('NOT evidence' in advice, advice)
        # And with a baseline it is a comparison, not an inference.
        baseline = dict(header)
        verdict2, headline2, _ = classify_verify(expected, header, comparison,
                                                 baseline_header=baseline)
        check(verdict2 == 'not_written', verdict2)
        check('before the send' in headline2, headline2)

    # -- 15: REGRESSION. Only the type different means a substitution -------
    def case_classify_type_only():
        expected = {'name': 'CX1030 X17680', 'sensor_type': 'R8K10UA',
                    'multiplier': '-1.0', 'units': 'LOGOHM'}
        header = {'name': 'CX1030 X17680', 'sensor_type': 'SiDiode',
                  'multiplier': -1.0, 'units': 'LOGOHM'}
        comparison = {'matched': True, 'sent_count': 129, 'read_count': 129,
                      'problems': []}
        verdict, _, advice = classify_verify(expected, header, comparison)
        check(verdict == 'type_only', verdict)
        check('type probe' in advice, advice)

    # -- 16: a good readback verifies -------------------------------------
    def case_classify_verified():
        expected = {'name': 'CX1030 X17680', 'sensor_type': 'ACR',
                    'multiplier': '-1.0', 'units': 'LOGOHM'}
        header = {'name': 'CX1030 X17680', 'sensor_type': 'ACR',
                  'multiplier': -1.0, 'units': 'LOGOHM'}
        comparison = {'matched': True, 'sent_count': 129, 'read_count': 129,
                      'problems': []}
        verdict, _, _ = classify_verify(expected, header, comparison)
        check(verdict == 'verified', verdict)

    # -- 17: a right header with lost points points at the line ending ------
    def case_classify_points():
        expected = {'name': 'CX1030 X17680', 'sensor_type': 'ACR',
                    'multiplier': '-1.0', 'units': 'LOGOHM'}
        header = {'name': 'CX1030 X17680', 'sensor_type': 'ACR',
                  'multiplier': -1.0, 'units': 'LOGOHM'}
        comparison = {'matched': False, 'sent_count': 129, 'read_count': 124,
                      'problems': []}
        verdict, _, advice = classify_verify(expected, header, comparison)
        check(verdict == 'points', verdict)
        check('line ending' in advice, advice)

    # -- 18: the readback is judged at the precision it was printed to ------
    def case_printed_precision():
        sent = [(325.0, 1.645231)]
        read = [(325.0, 1.6452)]
        loose = compare_curves(sent, read,
                               read_texts=[("1.6452", "325.000")])
        check(loose['matched'], loose['problems'])
        tight = compare_curves(sent, read,
                               read_texts=[("1.64520000", "325.000")])
        check(not tight['matched'], "an under-printed value passed a tight "
                                    "tolerance")
        # REGRESSION (2 Sep 2026): a point converted from ohms to log ohms
        # carries fifteen digits; fmt6() sent six; the instrument echoed
        # those six. Judged against the fifteen-digit float the gap was up
        # to half a unit in the sixth digit, over the tolerance the printed
        # reply allows, and a correct transfer read as "points differ" with
        # a problem line whose two sides looked identical.
        converted = [(3.5913, math.log10(3901.2345678)),      # 3.59125...
                     (3.75, math.log10(3550.987654))]         # 3.55035...
        echoed = [(3.5913, float(fmt6(converted[0][1]))),
                  (3.75, float(fmt6(converted[1][1])))]
        texts = [(fmt6(converted[0][1]), "3.5913"),
                 (fmt6(converted[1][1]), "3.75")]
        result = compare_curves(converted, echoed, read_texts=texts)
        check(result['matched'], f"a six-digit echo of a fifteen-digit "
                                 f"float must match: {result['problems']}")
        # 'Diode' echoed as 'SiDiode' is the firmware's spelling, not the
        # substitution: a correct diode transfer must verify.
        fields = compare_headers(
            {'name': 'DT-670', 'sensor_type': 'Diode',
             'multiplier': '1.0', 'units': 'VOLTS'},
            {'name': 'DT-670', 'sensor_type': 'SiDiode',
             'multiplier': 1.0, 'units': 'VOLTS'})
        check(fields['sensor_type'][2], "SiDiode is Diode in this firmware")
        # ... while ACR coming back as SiDiode still is the substitution.
        fields = compare_headers(
            {'name': 'X17680', 'sensor_type': 'ACR',
             'multiplier': '-1.0', 'units': 'LOGOHM'},
            {'name': 'X17680', 'sensor_type': 'SiDiode',
             'multiplier': -1.0, 'units': 'LOGOHM'})
        check(not fields['sensor_type'][2], "ACR -> SiDiode is a substitution")
        # A resend of the same header into a slot that already held it, with
        # one point line lost, is a POINTS problem, not "nothing written".
        same = {'name': 'X17680', 'sensor_type': 'R8K10UA',
                'multiplier': '-1.0', 'units': 'LOGOHM'}
        held = {'name': 'X17680', 'sensor_type': 'R8K10UA',
                'multiplier': -1.0, 'units': 'LOGOHM'}
        verdict, headline, _ = classify_verify(
            same, held, {'matched': False, 'sent_count': 135,
                         'read_count': 134, 'problems': ['short']},
            baseline_header=held)
        check(verdict == 'points', verdict)
        check('134 came back' in headline, headline)
        # And the name survives the instrument's own casing.
        fields = compare_headers(
            {'name': 'X17680', 'sensor_type': 'R8K10UA',
             'multiplier': '-1.0', 'units': 'LOGOHM'},
            {'name': 'x17680', 'sensor_type': 'r8k10ua',
             'multiplier': -1.0, 'units': 'logohm'})
        check(all(matched for _, _, matched in fields.values()),
              f"casing is not a mismatch: {fields}")

    # -- 19: the two vocabularies really are different ----------------------
    def case_vocabularies():
        # v1.3: both vocabularies are offered for the header, because the
        # firmware uses the R-names and the manual prints the old ones, and
        # nothing here can decide between them. What must stay true is that
        # the MANUAL's printed list is still recorded separately, so the
        # warning can name the discrepancy instead of hiding it.
        check('R8K10UA' not in CALCUR_MANUAL_TYPE_LIST,
              "R8K10UA is in the manual's printed CALCUR list")
        check('ACR' in CALCUR_MANUAL_TYPE_LIST, "ACR missing from the manual")
        check('R8K10UA' in CALCUR_SENSOR_TYPES, "R8K10UA not offered")
        check('ACR' in CALCUR_SENSOR_TYPES, "ACR not offered")
        check(CALCUR_TYPE_CANDIDATES[0] == 'R8K10UA', CALCUR_TYPE_CANDIDATES)
        check('R8K10UA' in SENTYPE_SENSOR_TYPES, "R8K10UA missing SENTYPE")
        check('ACR' not in SENTYPE_SENSOR_TYPES, "ACR is in the SENTYPE list")
        check(CERNOX_DEFAULTS['sensor_type'] in CALCUR_SENSOR_TYPES,
              CERNOX_DEFAULTS)
        check(CERNOX_DEFAULTS['sentype_type'] in SENTYPE_SENSOR_TYPES,
              CERNOX_DEFAULTS)

    # -- 19b: REGRESSION. The observed table, mapped from names --------------
    def case_table_map_observed():
        # What this lab's Rev 3.03A actually answered on 31 Aug: index 10 is
        # a thermocouple, not user curve 1. Suppose the user block starts at
        # 14, which is where Appendix A's own factory table (0-13) implies.
        entries = (
            [{'index': i, 'name': n} for i, n in enumerate(
                ['None', 'Cryocon S700', 'LS DT-670', 'LS DT-470',
                 'SI 410 Diode', 'Pt100 385', 'Pt1K 385', 'Pt10K 385',
                 'RuOx 1K Ohm', 'TC K Extern', 'TC E Extern', 'TC T Extern',
                 'AuFe 0.07%', 'Lakeshore 10'])] +
            [{'index': 14 + i, 'name': f'User Sensor {i + 1}'}
             for i in range(9)] +
            [{'index': 23 + i, 'name': f'User Sensor {c}'}
             for i, c in enumerate('ABC')])
        analysis = analyse_sensor_table(entries)
        check(analysis['confidence'] == 'confirmed', analysis)
        check(analysis['user_block_start'] == 14, analysis)
        check(analysis['senix_offset'] == 13, analysis)
        check(any('does NOT match the manual' in note
                  for note in analysis['notes']), analysis['notes'])

    # -- 19c: a factory name is recognised, an empty user slot is not --------
    def case_factory_recognition():
        for name in ('Lakeshore 10', 'TC E Extern', 'LS DT-470',
                     'Cryocon S700', 'RuOx 2K Ohm', 'None'):
            check(looks_like_factory_entry(name),
                  f"{name} not seen as factory")
        for name in ('User Sensor 1', 'User Sensor B', 'CX1030 X17680'):
            check(not looks_like_factory_entry(name),
                  f"{name} wrongly seen as factory")
        check(looks_like_empty_user_slot('User Sensor 3'), 'User Sensor 3')
        check(not looks_like_empty_user_slot('CX1030 X17680'), 'CX1030')

    # -- 19d: a table with no untouched slot refuses to guess ----------------
    def case_table_map_refuses_to_guess():
        entries = [{'index': i, 'name': 'Lakeshore 10'} for i in range(0, 12)]
        analysis = analyse_sensor_table(entries)
        check(analysis['confidence'] == 'unknown', analysis)
        check(analysis['senix_offset'] is None, analysis)
        check(any('cannot be established' in note
                  for note in analysis['notes']), analysis['notes'])
        mixed = [{'index': 10, 'name': 'User Sensor 1'},
                 {'index': 15, 'name': 'User Sensor 3'}]
        analysis = analyse_sensor_table(mixed)
        check(analysis['confidence'] == 'partial', analysis)
        check(analysis['senix_offset'] is None, analysis)

    # -- 19e: REGRESSION. The table as this instrument really reported it ---
    def case_real_table_31aug():
        names = ['None', 'Lakeshore 10', 'Lakeshore 11', 'Cryocal D3',
                 'SI 410', 'Pt100 3902', 'Pt100 385', 'Pt1K 385',
                 'Pt1K 375', 'TC K Extern', 'TC E Extern', 'TC T Extern',
                 'TC type K', 'TC type E', 'TC type T', 'S700']
        entries = [{'index': i, 'name': n} for i, n in enumerate(names)]
        entries += [{'index': 16 + i, 'name': f'User Sensor {c}'}
                    for i, c in enumerate('23456789ABC')]
        analysis = analyse_sensor_table(entries)
        check(analysis['confidence'] == 'confirmed', analysis)
        check(analysis['user_block_start'] == 15, analysis)
        check(analysis['senix_offset'] == 14, analysis)
        # index 1 is a factory entry and must be refused
        check(looks_like_factory_entry('Lakeshore 10'), 'Lakeshore 10')
        # 16 and 17 are the two targets and must not be
        check(looks_like_empty_user_slot('User Sensor 2'), 'User Sensor 2')
        check(looks_like_empty_user_slot('User Sensor 3'), 'User Sensor 3')
        check(not looks_like_factory_entry('User Sensor 2'), 'User Sensor 2')

    # -- 20: slot and index arithmetic matches Appendix A -------------------
    def case_senix():
        # These are the Appendix A numbers, which this firmware disagrees
        # with. They are the FALLBACK, used only until a scan runs, so what
        # is checked is that the fallback is what it claims to be, not that
        # the manual is right.
        check(CurveLoaderBackend.senix_for_user_curve(1) == 10,
              "the unconfirmed fallback for user curve 1 should be 10")
        check(CurveLoaderBackend.senix_for_user_curve(1, 13) == 14,
              "with a scanned offset of 13, user curve 1 should be index 14")
        check(CurveLoaderBackend.senix_for_user_curve(12, 13) == 25,
              "with a scanned offset of 13, user curve 12 should be index 25")
        check(MAX_USER_CURVE == 12 and MIN_USER_CURVE == 1,
              "the Model 34 has twelve user curves, numbered 1 to 12")

    # -- 21: the manual's line-ending rule, per interface -------------------
    def case_line_ending():
        backend = CurveLoaderBackend.__new__(CurveLoaderBackend)

        class _Stub:
            def __init__(self, address):
                self.address = address

        backend.link = _Stub("GPIB0::12::INSTR")
        check(backend.resolve_line_ending(None) == b'', "GPIB got a byte")
        backend.link = _Stub("USB0::0x1234::INSTR")
        check(backend.resolve_line_ending(None) == b'', "USB got a byte")
        backend.link = _Stub("ASRL3::INSTR")
        check(backend.resolve_line_ending(None) == b'\n', "RS-232 got none")
        backend.link = _Stub("GPIB0::12::INSTR")
        check(backend.resolve_line_ending(b'\r\n') == b'\r\n',
              "an explicit choice was overridden")

    return [
        ("fmt6 writes six digits, never an exponent", case_fmt6),
        ("a .340 file is read reading-first", case_parse_340),
        ("a .dat file is read temperature-first and swapped", case_parse_dat),
        ("a headerless file is refused, not guessed", case_headerless_refused),
        ("ohms and log-ohms interconvert, volts never", case_units),
        ("thinning keeps both ends and invents nothing", case_thin),
        ("extending adds only beyond the ends", case_extend),
        ("a built block parses back to the same numbers", case_round_trip),
        ("a SENTYPE name in a CALCUR header warns without blocking",
         case_sentype_name_warns),
        ("REGRESSION: ACR is accepted for a log-ohm NTC curve",
         case_acr_accepted),
        ("the input-range check keys on the SENTYPE type",
         case_range_headroom),
        ("a volts curve on a resistance type is an error",
         case_units_type_clash),
        ("a positive multiplier on an NTC curve is an error",
         case_multiplier_sign),
        ("a curve short of the working range warns", case_coverage),
        ("REGRESSION: three fields differing means nothing landed",
         case_classify_not_written),
        ("REGRESSION: only the type differing means a substitution",
         case_classify_type_only),
        ("a good readback verifies", case_classify_verified),
        ("a right header with lost points blames the line ending",
         case_classify_points),
        ("the readback is judged at the precision it was printed to",
         case_printed_precision),
        ("the CALCUR and SENTYPE vocabularies are separate",
         case_vocabularies),
        ("REGRESSION: the observed sensor table maps to offset 13",
         case_table_map_observed),
        ("REGRESSION: the real 31 Aug table maps to offset 14",
         case_real_table_31aug),
        ("factory names and empty user slots are told apart",
         case_factory_recognition),
        ("a table with no untouched slot refuses to guess",
         case_table_map_refuses_to_guess),
        ("the Appendix A offset is only the unconfirmed fallback",
         case_senix),
        ("the line-ending rule follows the interface", case_line_ending),
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
        report(f"{len(failures)} of {len(cases)} checks FAILED. Do not send "
               "a curve with this build.")
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
