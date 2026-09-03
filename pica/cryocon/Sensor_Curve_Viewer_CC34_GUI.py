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

INPUT <ch>:SENIX?   (p.187, with local caveats)
  Which sensor index an input channel is using. The Rev 3.03A unit in this
  lab also answers ISENIX? and USENIX?, which Edition 4 does not list, and
  the three number differently. All three are asked and all three answers are
  shown, so the operator can see which scheme this firmware is using instead
  of this module picking one and being quietly wrong.

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

# Names that mean a slot is a factory entry rather than something an operator
# stored. Kept because the distinction matters when deciding what is safe to
# overwrite -- in the loader, not here -- and because it is useful to see.
FACTORY_NAME_MARKERS = ('LAKESHORE', 'LAKE SHORE', 'PLATINUM', 'PT-', 'PT1',
                        'RUOX', 'RO-', 'ROX', 'SI410', 'SI-410', 'DIODE',
                        'TYPE ', 'THERMOCOUPLE', 'CRYOCON', 'FACTORY')

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


def parse_calcur_block(text, source_name="the slot"):
    """Read the reply to a 'CALCUR? n' query.

    Returns (header, points) where points is a list of (reading, temperature)
    in the order the instrument printed them, which is the order it stores
    them: ascending sensor reading.

    The numerals are kept exactly as they were printed, under
    header['point_texts']. How many digits the instrument prints is the limit
    on how precisely anything read here can be quoted, and that is not
    recoverable once the text has become a float.
    """
    lines = [line.strip() for line in _clean_lines(text)]
    lines = [line for line in lines if line]
    if len(lines) < 6:
        raise CurveReadError(
            f"{source_name} answered {len(lines)} non-blank line(s). A curve "
            "is a header of four lines, at least two points and a "
            "semicolon.")

    # The reply may still carry the echoed command on some interfaces.
    if re.match(r'^CALCUR\??\s', lines[0], re.I):
        lines = lines[1:]

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
    if len(points) < 2:
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
        "read; CALCUR? takes about twelve seconds per slot on this firmware.",
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

# CALCUR? walks flash and takes about twelve seconds on the Rev 3.03A unit in
# this lab, so its own timeout is generous and separate from the ordinary one.
CURVE_READ_TIMEOUT_MS = 20000
CURVE_READ_MAX_LINES = MAX_CURVE_POINTS + 12

IDN_SCAN_TIMEOUT_MS = 1500
PROBE_RESOURCE_PREFIXES = ('GPIB', 'USB', 'TCPIP')

EVENT_POLL_MS = 50


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
                  progress=None):
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
        firmware one of those takes about twelve seconds, so thirty-two of
        them is six minutes on the bus, while SENTYPE? answers in well under
        a second and gives the name, which is what identifies a slot. Read
        the curve itself afterwards, at the one or two indices that matter.

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

    def read_slot_curve(self, index, progress=None):
        """Read one slot with CALCUR? and parse it.

        Returns (header, points, raw_text). header and points are None when
        the slot is empty or the reply is not a curve; raw_text is always
        whatever came back, so the caller can print it rather than have this
        function decide quietly that nothing was there.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        if not (MIN_TABLE_INDEX <= index <= MAX_TABLE_INDEX):
            raise ValueError(
                f"Table index must be {MIN_TABLE_INDEX} to "
                f"{MAX_TABLE_INDEX}, not {index}.")
        text = self.link.ask_block(f"CALCUR? {index}", progress=progress)
        if not text.strip():
            return None, None, text
        try:
            header, points = parse_calcur_block(text, f"slot {index}")
        except CurveReadError as exc:
            # Say WHY, or a timed-out read looks exactly like an empty slot.
            self.log(f"  Slot {index} answered, but not with a curve: {exc}")
            return None, None, text
        return header, points, text

    def read_channel_sensors(self):
        """Which sensor index each input is using, and what it reads.

        Edition 4 documents INPUT <ch>:SENIX. The Rev 3.03A unit in this lab
        also answers ISENIX and USENIX, which that manual does not list, and
        the three number differently. All three are asked and all three
        answers are returned, so the operator can see which scheme this
        firmware is actually using instead of this module picking one.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        result = {}
        for channel in INPUT_CHANNELS:
            entry = {}
            for key, command in (
                    ('SENIX', f"INPUT {channel}:SENIX?"),
                    ('ISENIX', f"INPUT {channel}:ISENIX?"),
                    ('USENIX', f"INPUT {channel}:USENIX?"),
                    ('reading', f"INPUT? {channel}")):
                try:
                    entry[key] = self.link.ask(command)
                except ReadOnlyViolation:
                    raise
                except Exception as exc:
                    entry[key] = f"<no answer: {type(exc).__name__}>"
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

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Cryocon 34 Sensor Curve Viewer"

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

        self.catalogue = []           # every SENTYPE? entry the scan reached
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
                  "entry. A few seconds in total, and it writes nothing.\n"
                  "The curve points themselves are not read here, because\n"
                  "CALCUR? takes about twelve seconds per slot on this\n"
                  "firmware. Read the one slot that matters in step 3."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(6, 4))

        self.catalogue_btn = ttk.Button(
            frame, text="List the Master Sensor Table",
            command=self._scan_catalogue)
        self.catalogue_btn.grid(row=1, column=0, sticky='ew',
                                padx=10, pady=(0, 6))

        self.catalogue_label = ttk.Label(
            frame, text="Not listed yet.", font=('Segoe UI', 9, 'italic'),
            background=self.CLR_FRAME_BG, foreground=self.CLR_STATUS_WARN,
            wraplength=480, justify='left')
        self.catalogue_label.grid(row=2, column=0, sticky='w',
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

        self.stop_btn = ttk.Button(frame, text="Stop the slot list",
                                   state='disabled',
                                   command=self._request_stop)
        self.stop_btn.grid(row=3, column=0, columnspan=2, sticky='ew',
                           padx=10, pady=(0, 4))

        ttk.Label(
            frame,
            text=("One CALCUR? query. On the Rev 3.03A unit here it takes\n"
                  "about twelve seconds while the instrument walks its\n"
                  "flash; the window stays responsive throughout. Reading\n"
                  "cannot be interrupted part-way, so the stop button above\n"
                  "applies to the slot list in step 2, not to this."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=4, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 8))

    def _create_export_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 4  ·  Export')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Button(frame, text="Save this curve as a Cryo-con .crv file",
                   command=self._export_crv).grid(
            row=0, column=0, sticky='ew', padx=10, pady=(8, 4))
        ttk.Button(frame, text="Save this curve as CSV",
                   command=self._export_curve_csv).grid(
            row=1, column=0, sticky='ew', padx=10, pady=(0, 4))
        ttk.Button(frame, text="Save the slot list as CSV",
                   command=self._export_catalogue_csv).grid(
            row=2, column=0, sticky='ew', padx=10, pady=(0, 4))

        ttk.Label(
            frame,
            text=("The .crv carries the numerals the instrument printed,\n"
                  "not this module's rounding of them, so it is what the\n"
                  "instrument holds. It can be loaded by the Cryo-con\n"
                  "utility software or by the Sensor Curve Loader in this\n"
                  "suite. The CSV is for plotting and record-keeping."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=3, column=0, sticky='w',
                                 padx=10, pady=(0, 8))

    def _create_channel_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Which sensor is each input using?')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text=("SENIX, ISENIX and USENIX number differently on this\n"
                  "firmware and Edition 4 lists only the first. All three\n"
                  "are asked and all three answers shown, so nothing here\n"
                  "picks one and is quietly wrong."),
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
        elif kind == 'catalogue':
            self._show_catalogue(event[1])
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
        self.log("Stop requested. The slot list will end after the query "
                 "that is already in flight.")

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

        A CALCUR? read is twelve seconds on this firmware and a table scan is
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

        threading.Thread(target=worker, daemon=True,
                         name=f"cc34-viewer-{description}").start()

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
            self._post('catalogue', entries)

        self._run_in_worker("Listing the sensor table", job)

    def _show_catalogue(self, entries):
        self.catalogue = entries
        for row in self.catalogue_table.get_children():
            self.catalogue_table.delete(row)
        named = 0
        for entry in entries:
            name = entry.get('name')
            if name is None:
                values = (entry['index'], '<no answer>', '', '', '')
                tags = ('empty',)
            elif looks_like_empty_slot(name):
                values = (entry['index'], name, entry.get('type') or '',
                          entry.get('multiplier') or '', 'empty')
                tags = ('empty',)
            else:
                named += 1
                looks = 'factory' if looks_like_factory_entry(name) else 'user'
                values = (entry['index'], name, entry.get('type') or '',
                          entry.get('multiplier') or '', looks)
                tags = ()
            self.catalogue_table.insert('', 'end', values=values, tags=tags)
        self.catalogue_label.config(
            text=(f"{named} of {len(entries)} indices carry a name. "
                  "Double-click a row to read its curve "
                  "(about twelve seconds)."),
            foreground=self.CLR_STATUS_OK if named else self.CLR_STATUS_WARN)
        self.right_tabs.select(1)

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
                     "takes about twelve seconds on this firmware...")
            started = time.time()

            def progress(done, total):
                self._post('progress', done, total)

            header, points, raw = self.backend.read_slot_curve(
                index, progress=progress)
            elapsed = time.time() - started
            self.log(f"  Reply in {elapsed:.1f} s, "
                     f"{len(raw.splitlines())} line(s).")
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

        if header is None:
            self.headline_label.config(
                text=f"Slot {index} holds no readable curve.")
            self.detail_label.config(
                text=("The instrument answered, but the reply is not the "
                      "four header lines, points and semicolon a Cryo-con "
                      "curve is made of. That normally means the slot is "
                      "empty. The whole reply is on the 'What the "
                      "instrument said' tab."))
            self.problem_label.config(text="")
            self._draw_plot(None, [])
            self.right_tabs.select(0)
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

        problems = []
        if stats and not stats.get('temperature_monotonic'):
            problems.append(
                "The temperature column is not monotonic. A calibration "
                "curve normally is, so check the read before using this.")
        if stats and not stats.get('readings_ascending'):
            problems.append(
                "The sensor column is not strictly ascending, which is the "
                "order a Cryo-con sorts its stored curves into.")
        if header.get('sensor_type', '').strip().lower() in (
                'diode', 'sidiode', 'si diode') and units in ('OHMS',
                                                              'LOGOHM'):
            problems.append(
                "The sensor type reads as a diode while the curve units are "
                "resistive. That is the signature of the silent diode "
                "substitution the manual warns about: a header type the "
                "firmware could not identify is replaced with Diode rather "
                "than reported. The curve data itself is still what is "
                "shown.")
        self.problem_label.config(text="\n".join(problems))

        self._draw_plot(header, points)
        self.right_tabs.select(0)

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
        for channel in INPUT_CHANNELS:
            entry = answers.get(channel, {})
            lines.append(
                f"{channel}: SENIX {str(entry.get('SENIX', '?')):>6s}  "
                f"ISENIX {str(entry.get('ISENIX', '?')):>6s}  "
                f"USENIX {str(entry.get('USENIX', '?')):>6s}")
            lines.append(f"   reads {entry.get('reading', '?')}")
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
        # close. It is stopped and the session is dropped.
        self._stop_flag.set()
        if self.is_connected:
            self.backend.disconnect()
        self.root.destroy()


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
