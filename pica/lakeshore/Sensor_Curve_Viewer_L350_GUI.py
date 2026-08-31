"""
Module: Sensor_Curve_Viewer_L350_GUI.py
Purpose: Look at the calibration curves that are already inside a Lake Shore
         Model 350 -- list every slot, read the breakpoints of one slot, plot
         them, and write them out as a .340 file and a CSV.

         This is the read-only counterpart of the Cryocon curve tooling. It
         answers three questions that otherwise need the front panel and a
         lot of knob turning:

           - which of the 59 curve slots hold anything, and what;
           - what the breakpoints of a chosen slot actually are;
           - which curve each input channel is using.

===============================================================================
THIS MODULE IS PASSIVE. IT NEVER CHANGES THE INSTRUMENT.
===============================================================================

Every command it can send is a query. There is no CRVHDR set, no CRVPT set,
no CRVDEL, no CRVSAV, no INTYPE, no INCRV set, no setpoint, no ramp, no
heater range, no *RST and no *CLS. The instrument is asked for information
and nothing else, so this can be opened in the middle of a running cooldown
without touching it.

That is not left to good intentions. LakeshareReadOnlyLink.ask() is the only
method in this file that talks to the bus, and it refuses to transmit any
command that does not contain a '?'. A '?' is what makes a Lake Shore command
a query; every setting command is the same mnemonic without one. So the guard
is not a list of forbidden words that a new command could slip past -- it is
the opposite, a rule that only queries pass. Self-test case 1 is that rule.

The one thing a query can still do is take time. CRVPT? is asked once per
breakpoint, so reading a full 200-point curve is 200 queries. They are paced
and they are run off the Tk thread, and the progress bar says how far along
it is, but on a busy GPIB bus a full read is a few seconds. Nothing is
written while it happens.

===============================================================================
WHAT THE MANUAL SAYS  (Lake Shore Model 350 User's Manual, Rev. 2.x)
===============================================================================

Curve slots
  1 to 59. Slots 1 to 20 hold the standard Lake Shore curves (DT-470, DT-670,
  PT-100, RX-102A, thermocouples and so on) and are read-only in the
  instrument itself. Slots 21 to 59 are the 39 user curves. This module makes
  no distinction beyond labelling them, because a query works the same on
  both, and reading a standard curve is often exactly what is wanted -- it is
  how you find out what the instrument thinks a DT-670 is.

  A slot that holds nothing still answers CRVHDR?. It comes back with an
  empty or blank name; that is how an empty slot is recognised here. It is
  never inferred from a slot number.

CRVHDR? <curve>
  Reply: <name>,<serial>,<format>,<limit>,<coefficient>
    name         15 characters
    serial       10 characters
    format       1 = mV/K, 2 = V/K, 3 = Ohm/K, 4 = log Ohm/K
    limit        temperature limit in kelvin
    coefficient  1 = negative, 2 = positive
  These five are what a .340 file's header carries, which is why the export
  below can be re-loaded by the Lake Shore curve handler without anything
  being invented on the way out.

CRVPT? <curve>,<index>
  Reply: <units value>,<temperature value>. index is 1 to 200.
  Breakpoints beyond the end of a curve read back as zero for both fields.
  That is the documented end marker and it is what STOP_AFTER_ZERO_PAIRS
  below looks for. Two consecutive zero pairs are required rather than one,
  because a genuine breakpoint at exactly 0,0 is not physically meaningful
  but a single dropped reply is a thing that happens on GPIB.

INCRV? <input>
  Reply: the curve number that input A, B, C or D is using. Read-only here;
  the module reports it so the curve on screen can be tied to a channel, and
  offers no way to change it.

INTYPE? <input>
  Reply: <sensor type>,<autorange>,<range>,<compensation>,<units>. Reported
  as context for the channel, again read-only.

===============================================================================
WHAT IT WRITES TO DISK
===============================================================================

Two files, both optional, both to a path chosen in a save dialog:

  .340   The Lake Shore breakpoint file. Same header keys and same three
         columns as the files the Lake Shore curve handler and the Cryocon
         curve loader in this repository read, so a curve pulled out of the
         350 can go straight into another instrument. Written so that
         'Number of Breakpoints' always matches the rows that follow, because
         the loader in pica/cryocon refuses a file where it does not.

  .csv   Index, units value, temperature. A '#' preamble carries the header
         fields so the file is self-describing, then one plain column header
         line, then the numbers. For plotting, not for re-loading.

  The curve list can also be written as a CSV of all 59 slots, which is the
  quickest way to keep a record of what a given instrument holds.

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
# Each of these is a feature, not a requirement. The parsing, the .340 writer
# and the CSV writer all work with none of them installed.

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

MIN_CURVE = 1
MAX_CURVE = 59               # Model 350 and Model 336
FIRST_USER_CURVE = 21        # 1-20 are the standard Lake Shore curves
MAX_BREAKPOINTS = 200
INPUT_CHANNELS = ('A', 'B', 'C', 'D')

# Data Format codes, exactly as they are printed in a .340 header and
# returned by CRVHDR?. The third field is the column heading a .340 uses.
CURVE_FORMATS = {
    1: ("mV/K", "mV", "millivolts"),
    2: ("V/K", "V", "volts"),
    3: ("Ohm/K", "Ohm", "ohms"),
    4: ("Log Ohm/K", "log(Ohm)", "log10 of ohms"),
}

# Temperature coefficient codes.
COEFFICIENTS = {1: "Negative", 2: "Positive"}

# A CRVPT? reply of 0,0 marks the end of a curve. One of them could be a
# dropped reply; two in a row is the instrument saying there is nothing more.
STOP_AFTER_ZERO_PAIRS = 2


class CurveReadError(RuntimeError):
    """A curve could not be read back with certainty."""


class ReadOnlyViolation(RuntimeError):
    """Something tried to send a command that is not a query.

    Raised by the link before anything reaches the bus. If this is ever seen
    it is a bug in this module, not an instrument problem, and the operation
    that raised it sent nothing.
    """


def fmt_value(value, digits=6):
    """A number in plain decimal, never in exponent form.

    Exponent notation is avoided for the same reason the Cryocon loader
    avoids it: a value written '1e-05' that is read back as '1' is wrong by
    five orders of magnitude without looking wrong, and the programs that
    read .340 files are old.
    """
    if value is None or not math.isfinite(value):
        raise ValueError(f"{value!r} is not a finite number")
    text = f"{value:.{digits}g}"
    if 'e' in text or 'E' in text:
        text = f"{value:.12f}".rstrip('0').rstrip('.')
    if not text:
        text = "0"
    if '.' not in text:
        text += ".0"
    return text


def parse_crvhdr(reply, curve):
    """Turn a CRVHDR? reply into a dict, or raise.

    Reply shape: <name>,<serial>,<format>,<limit>,<coefficient>

    Fields that will not parse are kept as their raw text under a '_raw' key
    rather than guessed at, because a header field this module invented would
    end up in an exported .340 as if the instrument had said it.
    """
    raw = str(reply).strip()
    fields = [field.strip() for field in raw.split(',')]
    if len(fields) < 5:
        raise CurveReadError(
            f"CRVHDR? {curve} answered '{raw}', which is not the five "
            "comma-separated fields the manual documents "
            "(name, serial, format, limit, coefficient).")

    header = {
        'curve': curve,
        'name': fields[0].strip().strip('"'),
        'serial': fields[1].strip().strip('"'),
        'format_code': None,
        'limit': None,
        'coefficient_code': None,
        '_raw': raw,
    }
    try:
        header['format_code'] = int(float(fields[2]))
    except ValueError:
        pass
    try:
        header['limit'] = float(fields[3])
    except ValueError:
        pass
    try:
        header['coefficient_code'] = int(float(fields[4]))
    except ValueError:
        pass

    header['format_name'] = CURVE_FORMATS.get(
        header['format_code'], ("unknown", "?", "unknown"))[0]
    header['units_label'] = CURVE_FORMATS.get(
        header['format_code'], ("unknown", "?", "unknown"))[1]
    header['coefficient_name'] = COEFFICIENTS.get(
        header['coefficient_code'], "unknown")
    header['is_user_slot'] = curve >= FIRST_USER_CURVE
    header['is_empty'] = looks_empty(header['name'])
    return header


def looks_empty(name):
    """True if a curve name means 'nothing stored here'.

    An unused slot answers with a blank name or with a run of spaces. Some
    firmware answers a single dot. None of those is a curve anybody stored,
    and every one of them is treated the same way: the slot is listed, and it
    is listed as empty rather than skipped, because a gap in the list is
    itself worth seeing.
    """
    stripped = str(name or '').strip().strip('"').strip()
    return stripped == '' or stripped in ('.', '-', 'none', 'None')


def parse_crvpt(reply, curve, index):
    """Turn a CRVPT? reply into (units_value, temperature), or raise.

    Reply shape: <units value>,<temperature value>. Only the first two
    numeric fields are used, so a firmware revision that appends a field does
    not break the read.
    """
    raw = str(reply).strip()
    fields = [field.strip() for field in raw.replace(';', ',').split(',')]
    numbers = []
    for field in fields:
        try:
            numbers.append(float(field))
        except ValueError:
            continue
        if len(numbers) == 2:
            break
    if len(numbers) < 2:
        raise CurveReadError(
            f"CRVPT? {curve},{index} answered '{raw}', which is not a pair "
            "of numbers.")
    return numbers[0], numbers[1]


def curve_statistics(points, units_label):
    """Plain-language facts about a set of breakpoints.

    points is a list of (units_value, temperature) in the order the
    instrument stores them, which for a Lake Shore curve is ascending units
    value. Nothing here is a judgement; it is what is in the numbers.
    """
    if not points:
        return {}
    units = [pair[0] for pair in points]
    temps = [pair[1] for pair in points]
    monotonic_units = all(b > a for a, b in zip(units, units[1:]))
    ascending_t = all(b > a for a, b in zip(temps, temps[1:]))
    descending_t = all(b < a for a, b in zip(temps, temps[1:]))
    return {
        'count': len(points),
        'units_min': min(units),
        'units_max': max(units),
        'temp_min': min(temps),
        'temp_max': max(temps),
        'units_label': units_label,
        'units_ascending': monotonic_units,
        'temperature_monotonic': ascending_t or descending_t,
        'temperature_direction': ("rising with the sensor reading"
                                  if ascending_t else
                                  "falling as the sensor reading rises"
                                  if descending_t else
                                  "NOT monotonic"),
    }


# ---------------------------------------------------------------------------
# FILE WRITERS
# ---------------------------------------------------------------------------

def build_340_text(header, points):
    """A Lake Shore .340 breakpoint file for one curve.

    The header keys are the ones the Lake Shore curve handler writes and the
    ones pica/cryocon/Sensor_Curve_Loader_CC34_GUI.py reads, so a curve taken
    out of the 350 here can be put into the Cryocon there with no editing.

    'Number of Breakpoints' is written from the rows that follow, never from
    what the instrument claimed, because that loader refuses a file where the
    two disagree and a mismatch would make this export unusable.
    """
    if not points:
        raise ValueError("A .340 file needs at least one breakpoint.")
    if header.get('format_code') not in CURVE_FORMATS:
        raise ValueError(
            "This curve's Data Format code is "
            f"{header.get('format_code')!r}, which is not one of "
            f"{sorted(CURVE_FORMATS)}. A .340 file cannot say what the "
            "middle column holds, so it is not written. The CSV export "
            "carries the numbers with the raw header text alongside them.")

    format_code = header['format_code']
    format_name = CURVE_FORMATS[format_code][0]
    coefficient = header.get('coefficient_code')
    coefficient_name = COEFFICIENTS.get(coefficient, "unknown")
    limit = header.get('limit')

    lines = [
        f"Sensor Model:   {header.get('name') or 'UNKNOWN'}",
        f"Serial Number:  {header.get('serial') or 'UNKNOWN'}",
        f"Data Format:    {format_code}      ({format_name})",
        (f"SetPoint Limit: {fmt_value(limit)}      (Kelvin)"
         if limit is not None else
         "SetPoint Limit: 0.0      (Kelvin)"),
        (f"Temperature coefficient:  {coefficient} ({coefficient_name})"
         if coefficient is not None else
         "Temperature coefficient:  1 (Negative)"),
        f"Number of Breakpoints:   {len(points)}",
        "",
        "No.   Units      Temperature (K)",
        "",
    ]
    for number, (units_value, temperature) in enumerate(points, start=1):
        lines.append(f"{number:>3d}  {fmt_value(units_value):<13s}"
                     f"{fmt_value(temperature)}")
    text = "\n".join(lines) + "\n"
    text.encode('ascii')          # raises rather than writing a bad file
    return text


def build_curve_csv(header, points, idn="", address=""):
    """One curve as CSV: a '#' preamble, a column header, then the numbers."""
    units_label = header.get('units_label', '?')
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Lake Shore Model 350 calibration curve, read back over the bus",
        f"# Read on: {stamp}",
        f"# Instrument: {idn or 'unknown'}",
        f"# VISA address: {address or 'unknown'}",
        f"# Curve slot: {header.get('curve')}"
        f"  ({'user' if header.get('is_user_slot') else 'standard'} curve)",
        f"# Name: {header.get('name')}",
        f"# Serial: {header.get('serial')}",
        f"# Data format: {header.get('format_code')} "
        f"({header.get('format_name')})",
        f"# Temperature limit (K): {header.get('limit')}",
        f"# Temperature coefficient: {header.get('coefficient_code')} "
        f"({header.get('coefficient_name')})",
        f"# Breakpoints read: {len(points)}",
        "# Raw CRVHDR? reply: " + str(header.get('_raw', '')),
        f"Index,Units_{units_label.replace('/', '_per_')},Temperature_K",
    ]
    for number, (units_value, temperature) in enumerate(points, start=1):
        lines.append(f"{number},{fmt_value(units_value)},"
                     f"{fmt_value(temperature)}")
    return "\n".join(lines) + "\n"


def build_catalogue_csv(entries, idn="", address=""):
    """Every slot the catalogue scan reached, as CSV."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Lake Shore Model 350 curve list",
        f"# Read on: {stamp}",
        f"# Instrument: {idn or 'unknown'}",
        f"# VISA address: {address or 'unknown'}",
        "Slot,Kind,Name,Serial,FormatCode,FormatName,LimitK,"
        "CoefficientCode,CoefficientName,Empty",
    ]
    for entry in entries:
        if entry.get('error'):
            lines.append(f"{entry['curve']},"
                         f"{'user' if entry['curve'] >= FIRST_USER_CURVE else 'standard'},"
                         f"<no answer>,,,,,,,")
            continue
        kind = 'user' if entry.get('is_user_slot') else 'standard'
        lines.append(
            f"{entry.get('curve')},{kind},"
            f"\"{entry.get('name', '')}\",\"{entry.get('serial', '')}\","
            f"{entry.get('format_code', '')},{entry.get('format_name', '')},"
            f"{entry.get('limit', '')},{entry.get('coefficient_code', '')},"
            f"{entry.get('coefficient_name', '')},"
            f"{'yes' if entry.get('is_empty') else 'no'}")
    return "\n".join(lines) + "\n"


# ===============================================================================
# INSTRUMENT LINK  --  QUERIES ONLY
# ===============================================================================

LAKESHORE_IDN_MARKERS = ("LSCI", "LAKESHORE", "LAKE SHORE")
LAKESHORE_ADDRESS_HINT = "GPIB1::12"     # where the L350 sits in this lab

LAKESHORE_TIMEOUT_MS = 10000
LAKESHORE_OPEN_SETTLE_S = 0.20
LAKESHORE_MIN_GAP_S = 0.05
LAKESHORE_CONNECT_ATTEMPTS = 3
LAKESHORE_RETRY_WAIT_S = 1.5

IDN_SCAN_TIMEOUT_MS = 1500
PROBE_RESOURCE_PREFIXES = ('GPIB', 'USB', 'TCPIP')

EVENT_POLL_MS = 50


def is_lakeshore_idn(idn):
    """True if a '*IDN?' reply came from a Lake Shore instrument."""
    return any(marker in str(idn).upper() for marker in LAKESHORE_IDN_MARKERS)


def is_query(command):
    """True if `command` is a Lake Shore query.

    A Lake Shore command is a query when it carries a '?'. Every setting
    command is the same mnemonic without one: CRVHDR? reads a header, CRVHDR
    writes one; INCRV? reads the assignment, INCRV writes it. So this single
    test separates the two completely, and it does it by admitting queries
    rather than by listing commands to forbid -- a new command added to this
    module later cannot slip past a list it was never added to.
    """
    return '?' in str(command)


class LakeshoreReadOnlyLink:
    """One paced VISA session to a Lake Shore 350 that can only ask questions.

    There is deliberately no write() method. ask() is the only way anything
    reaches the bus, and it refuses a command without a '?' before opening
    its mouth. Nothing in this module can change a setting, a curve, a
    setpoint or a heater range, and nothing added to it later can either
    without removing this guard on purpose.
    """

    def __init__(self, visa_address, timeout_ms=LAKESHORE_TIMEOUT_MS,
                 log=None):
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
        for attempt in range(1, LAKESHORE_CONNECT_ATTEMPTS + 1):
            try:
                self.instrument = self.rm.open_resource(self.address)
                self.instrument.timeout = self.timeout_ms
                time.sleep(LAKESHORE_OPEN_SETTLE_S)
                self.idn = self.ask('*IDN?')
                if not self.idn:
                    raise ConnectionError(
                        f"{self.address} accepted the command but sent no "
                        "identification.")
                if attempt > 1:
                    self._log(f"  Lakeshore answered on attempt {attempt}.")
                return
            except ReadOnlyViolation:
                raise
            except Exception as exc:
                last_error = exc
                self._drop_session()
                if attempt < LAKESHORE_CONNECT_ATTEMPTS:
                    self._log(
                        f"  Lakeshore did not answer at {self.address} "
                        f"(attempt {attempt} of "
                        f"{LAKESHORE_CONNECT_ATTEMPTS}): "
                        f"{type(exc).__name__}. Retrying in "
                        f"{LAKESHORE_RETRY_WAIT_S:.1f} s.")
                    time.sleep(LAKESHORE_RETRY_WAIT_S)
        raise ConnectionError(
            f"No reply from a Lake Shore at {self.address} after "
            f"{LAKESHORE_CONNECT_ATTEMPTS} attempts. Last error: "
            f"{last_error}. Check that the instrument is powered and that "
            "its GPIB address matches this VISA address.")

    # -- the only way to the bus --

    def _pace(self, gap=None):
        """Hold a minimum gap between operations.

        The gap is looked up when it is needed rather than bound as a default
        argument, so slowing the bus down for a sulky firmware revision -- or
        speeding it up under test -- is a matter of changing the module
        constant and nothing else.
        """
        gap = LAKESHORE_MIN_GAP_S if gap is None else gap
        wait = gap - (time.time() - self._last_io)
        if wait > 0:
            time.sleep(wait)

    def ask(self, command):
        """Send one query and return its reply.

        Refuses anything that is not a query, before any I/O happens.
        """
        if not is_query(command):
            raise ReadOnlyViolation(
                f"This module is read-only and {command!r} is not a query. "
                "Only commands carrying a '?' are ever transmitted. Nothing "
                "was sent.")
        if self.instrument is None:
            raise ConnectionError("Not connected to the Lakeshore.")
        self._pace()
        try:
            reply = self.instrument.query(command)
        finally:
            self._last_io = time.time()
            self.commands_sent += 1
        return reply.strip()

    @property
    def is_connected(self):
        return self.instrument is not None

    def close(self):
        """Close the session only. No *RST, no *CLS, no setpoint, heater or
        curve command, so whatever is driving the cryostat carries on."""
        self._drop_session()


class CurveViewerBackend:
    """Everything this module says to a Lake Shore 350. All of it queries."""

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
        self.link = LakeshoreReadOnlyLink(visa_address, log=self.log)
        idn = self.link.idn
        # Nothing here can damage a foreign instrument, but CRVHDR? on
        # something that is not a Lake Shore returns whatever that
        # instrument makes of it, and reading a stranger's reply as a curve
        # header would put invented numbers on the screen.
        if not is_lakeshore_idn(idn):
            self.disconnect()
            raise ConnectionError(
                f"{visa_address} is not a Lake Shore: it identifies itself "
                f"as '{idn}'. Scan the bus and pick the 350's actual address "
                f"(it does not have to be {LAKESHORE_ADDRESS_HINT}).")
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

    def read_header(self, curve):
        """One CRVHDR? query, parsed."""
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        if not (MIN_CURVE <= curve <= MAX_CURVE):
            raise ValueError(
                f"Curve number must be {MIN_CURVE} to {MAX_CURVE}, "
                f"not {curve}.")
        return parse_crvhdr(self.link.ask(f"CRVHDR? {curve}"), curve)

    def scan_catalogue(self, first=MIN_CURVE, last=MAX_CURVE, progress=None,
                       should_stop=None):
        """Read every curve header in a range. One query per slot.

        A slot that will not answer is included with an 'error' key rather
        than dropped, because a gap in the list is itself informative.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        entries = []
        total = last - first + 1
        for offset, curve in enumerate(range(first, last + 1), start=1):
            if should_stop is not None and should_stop():
                break
            try:
                entry = self.read_header(curve)
            except Exception as exc:
                entry = {'curve': curve,
                         'is_user_slot': curve >= FIRST_USER_CURVE,
                         'is_empty': True,
                         'name': '', 'serial': '',
                         'error': f"{type(exc).__name__}: {exc}"}
            entries.append(entry)
            if progress:
                progress(offset, total, entry)
        return entries

    def read_points(self, curve, limit=MAX_BREAKPOINTS, progress=None,
                    should_stop=None):
        """Read the breakpoints of one curve with CRVPT?.

        Returns (points, notes). points is a list of (units, temperature) in
        the order the instrument stores them. notes is a list of strings
        describing anything that had to be decided, so the caller can print
        it rather than have this function decide quietly.

        Stops at the documented end marker: STOP_AFTER_ZERO_PAIRS consecutive
        replies of 0,0. Trailing zero pairs are not returned as points.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        points = []
        notes = []
        zero_run = 0
        for index in range(1, limit + 1):
            if should_stop is not None and should_stop():
                notes.append(f"Stopped by the operator at breakpoint {index}.")
                break
            try:
                units_value, temperature = parse_crvpt(
                    self.link.ask(f"CRVPT? {curve},{index}"), curve, index)
            except CurveReadError as exc:
                notes.append(str(exc))
                break
            if units_value == 0.0 and temperature == 0.0:
                zero_run += 1
                if zero_run >= STOP_AFTER_ZERO_PAIRS:
                    break
                # Held back: if the next reply is real, these zeros were a
                # gap in the stored curve and belong in the list.
                continue
            if zero_run:
                notes.append(
                    f"{zero_run} zero breakpoint(s) before index {index} were "
                    "followed by real data, so they are gaps inside the "
                    "curve, not its end. They are not in the table below.")
                zero_run = 0
            points.append((units_value, temperature))
            if progress:
                progress(index, limit, (units_value, temperature))
        if len(points) == limit:
            notes.append(
                f"The curve filled all {limit} breakpoints, so there may be "
                "more that this read did not ask for.")
        return points, notes

    def read_channel_curves(self):
        """Which curve each input is using, and what sensor type it is set to.

        Both are queries. INCRV? gives the curve number, INTYPE? the sensor
        configuration. Neither is changed here; they are reported so a curve
        on screen can be tied to a channel.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        result = {}
        for channel in INPUT_CHANNELS:
            entry = {}
            for key, command in (('curve', f"INCRV? {channel}"),
                                 ('intype', f"INTYPE? {channel}")):
                try:
                    entry[key] = self.link.ask(command)
                except Exception as exc:
                    entry[key] = f"<no answer: {type(exc).__name__}>"
            result[channel] = entry
        return result


# ===============================================================================
# GUI
# ===============================================================================

class CurveViewerGUI:
    """Browse and export the curves already inside a Lake Shore 350.

    The left panel is the job in order: connect, list what is there, read one
    slot, save it. The right panel always shows the slot that was read, as a
    plot and as every breakpoint, so nothing is exported unseen.
    """

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Lakeshore 350 Sensor Curve Viewer"

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

        self.catalogue = []           # every header the last scan reached
        self.header = None            # the slot on screen
        self.points = []              # its breakpoints, (units, temperature)
        self.read_notes = []

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
                  text=(f"Lake Shore Model 350 | curve slots {MIN_CURVE}-"
                        f"{MAX_CURVE}, {MAX_BREAKPOINTS} breakpoints each\n"
                        f"Slots {MIN_CURVE}-{FIRST_USER_CURVE - 1} are the "
                        f"standard curves; {FIRST_USER_CURVE}-{MAX_CURVE} "
                        "are the user curves."),
                  background=self.CLR_FRAME_BG, justify='left').grid(
            row=2, column=0, columnspan=2, padx=10, pady=(0, 4), sticky='w')
        ttk.Label(frame,
                  text=("This module only ever asks questions. It sends no\n"
                        "CRVHDR, CRVPT, CRVDEL, CRVSAV, INTYPE, INCRV,\n"
                        "SETP, RAMP, RANGE or *RST -- only the query forms.\n"
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
            text=("One CRVHDR? per slot: the name, serial, data format,\n"
                  "temperature limit and coefficient of all "
                  f"{MAX_CURVE - MIN_CURVE + 1} slots.\n"
                  "Around a second in total, and it writes nothing."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=0, column=0, sticky='w',
                                 padx=10, pady=(6, 4))

        self.user_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text=f"User slots only ({FIRST_USER_CURVE}-{MAX_CURVE})",
            variable=self.user_only_var).grid(
            row=1, column=0, sticky='w', padx=10, pady=(0, 4))

        self.catalogue_btn = ttk.Button(
            frame, text="List every curve slot",
            command=self._scan_catalogue)
        self.catalogue_btn.grid(row=2, column=0, sticky='ew',
                                padx=10, pady=(0, 6))

        self.catalogue_label = ttk.Label(
            frame, text="Not listed yet.", font=('Segoe UI', 9, 'italic'),
            background=self.CLR_FRAME_BG, foreground=self.CLR_STATUS_WARN,
            wraplength=480, justify='left')
        self.catalogue_label.grid(row=3, column=0, sticky='w',
                                  padx=10, pady=(0, 8))

    def _create_read_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Step 3  ·  Read one slot')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Curve slot:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.slot_cb = ttk.Combobox(frame, font=self.FONT_BASE,
                                    state='readonly', width=44)
        self.slot_cb['values'] = self._slot_choices()
        self.slot_cb.current(FIRST_USER_CURVE - MIN_CURVE)
        self.slot_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        self.read_btn = ttk.Button(
            frame, text="Read this slot's curve", style='Read.TButton',
            command=self._read_slot)
        self.read_btn.grid(row=1, column=0, columnspan=2, sticky='ew',
                           padx=10, pady=(4, 4))

        self.progress = ttk.Progressbar(frame, mode='determinate')
        self.progress.grid(row=2, column=0, columnspan=2, sticky='ew',
                           padx=10, pady=(0, 4))

        self.stop_btn = ttk.Button(frame, text="Stop reading",
                                   state='disabled', command=self._request_stop)
        self.stop_btn.grid(row=3, column=0, columnspan=2, sticky='ew',
                           padx=10, pady=(0, 4))

        ttk.Label(
            frame,
            text=("A full curve is up to 200 CRVPT? queries, so a long one\n"
                  "takes a few seconds. The read stops by itself at the\n"
                  "instrument's own end marker (a breakpoint of 0, 0)."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=4, column=0, columnspan=2, sticky='w',
                                 padx=10, pady=(0, 8))

    def _create_export_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Step 4  ·  Export')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Button(frame, text="Save this curve as a Lake Shore .340 file",
                   command=self._export_340).grid(
            row=0, column=0, sticky='ew', padx=10, pady=(8, 4))
        ttk.Button(frame, text="Save this curve as CSV",
                   command=self._export_curve_csv).grid(
            row=1, column=0, sticky='ew', padx=10, pady=(0, 4))
        ttk.Button(frame, text="Save the curve list as CSV",
                   command=self._export_catalogue_csv).grid(
            row=2, column=0, sticky='ew', padx=10, pady=(0, 4))

        ttk.Label(
            frame,
            text=("The .340 carries the same header keys the Lake Shore\n"
                  "curve handler writes, so it can be loaded back into an\n"
                  "instrument or fed to the Cryocon curve loader in this\n"
                  "suite. The CSV is for plotting and record-keeping."),
            background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
            justify='left').grid(row=3, column=0, sticky='w',
                                 padx=10, pady=(0, 8))

    def _create_channel_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Which curve is each input using?')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Button(frame, text="Ask the four inputs (read only)",
                   command=self._read_channels).grid(
            row=0, column=0, sticky='ew', padx=10, pady=(8, 4))
        self.channel_label = ttk.Label(
            frame, text="Not asked yet.", background=self.CLR_FRAME_BG,
            font=('Consolas', 9), justify='left', wraplength=480)
        self.channel_label.grid(row=1, column=0, sticky='w',
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
            summary, text="Connect, list the slots, then read one.",
            background=self.CLR_FRAME_BG, wraplength=900, justify='left')
        self.detail_label.grid(row=1, column=0, sticky='w',
                               padx=12, pady=(0, 4))
        self.problem_label = ttk.Label(
            summary, text="", background=self.CLR_FRAME_BG,
            wraplength=900, justify='left', foreground=self.CLR_STATUS_WARN)
        self.problem_label.grid(row=2, column=0, sticky='w',
                                padx=12, pady=(0, 10))

        # The notebook keeps the curve list and the breakpoints of one slot
        # side by side without either pushing the other off the screen.
        self.right_tabs = ttk.Notebook(panel)
        self.right_tabs.grid(row=1, column=0, rowspan=2, sticky='nsew',
                             padx=5, pady=5)

        curve_tab = ttk.Frame(self.right_tabs)
        self.right_tabs.add(curve_tab, text='  The curve  ')
        curve_tab.grid_columnconfigure(0, weight=1)
        curve_tab.grid_rowconfigure(0, weight=1)
        curve_tab.grid_rowconfigure(1, weight=1)

        plot_frame = ttk.LabelFrame(curve_tab, text='Breakpoints, plotted')
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
                      "drawn.\nThe table below shows every breakpoint that "
                      "was read."),
                background=self.CLR_FRAME_BG, justify='left').grid(
                row=0, column=0, padx=15, pady=15, sticky='w')

        table_frame = ttk.LabelFrame(
            curve_tab,
            text='Every breakpoint, in the order the instrument stores them')
        table_frame.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        columns = ('n', 'units', 'temperature', 'ohms')
        self.table = ttk.Treeview(table_frame, columns=columns,
                                  show='headings', height=8)
        for column, heading, width in (
                ('n', '#', 60),
                ('units', 'Sensor reading', 220),
                ('temperature', 'Temperature / K', 200),
                ('ohms', 'Resistance / ohm', 200)):
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
        columns = ('slot', 'kind', 'name', 'serial', 'format', 'limit',
                   'coefficient')
        self.catalogue_table = ttk.Treeview(list_tab, columns=columns,
                                            show='headings')
        for column, heading, width in (
                ('slot', 'Slot', 60),
                ('kind', 'Kind', 90),
                ('name', 'Name', 200),
                ('serial', 'Serial', 140),
                ('format', 'Data format', 160),
                ('limit', 'Limit / K', 100),
                ('coefficient', 'Coefficient', 120)):
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
            self._show_curve(event[1], event[2], event[3])
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
        if not PYVISA_AVAILABLE:
            self.log("PyVISA is not installed, so nothing can be read. The "
                     "file writers still work on data already loaded.")
        if not MATPLOTLIB_AVAILABLE:
            self.log("Matplotlib is not installed, so curves are shown as a "
                     "table only.")

    @staticmethod
    def _slot_choices():
        labels = []
        for curve in range(MIN_CURVE, MAX_CURVE + 1):
            kind = "user" if curve >= FIRST_USER_CURVE else "standard"
            labels.append(f"{curve:2d}  ({kind})")
        return labels

    def _selected_slot(self):
        raw = self.slot_cb.get().strip()
        match = re.match(r'\s*(\d+)', raw)
        if not match:
            return None
        value = int(match.group(1))
        return value if MIN_CURVE <= value <= MAX_CURVE else None

    def _require_connection(self):
        if not self.is_connected or not self.backend.is_connected:
            self.log("Not connected to the instrument.")
            messagebox.showerror("Not Connected",
                                 "Connect to the Lake Shore 350 first "
                                 "(step 1).")
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
        self.log("Stop requested. The read will end after the query that is "
                 "already in flight.")

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
        lakeshore = next((r for r in resources
                          if is_lakeshore_idn(identities.get(r, ''))), None)
        if lakeshore:
            self.visa_cb.set(lakeshore)
            self.log(f"Lake Shore identified at {lakeshore} and selected.")
            return
        hint = next((r for r in resources
                     if LAKESHORE_ADDRESS_HINT in r), None)
        if hint:
            self.visa_cb.set(hint)
            self.log(f"WARNING: no Lake Shore answered *IDN?. Selected {hint} "
                     "on the usual address alone.")
        else:
            self.log("WARNING: no Lake Shore found on the bus.")

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

        A full curve read is up to 200 paced queries, which is several
        seconds; done on the main thread the window would look frozen for the
        whole of it.
        """
        if self.busy:
            self.log("Another instrument job is still running.")
            return
        self._stop_flag.clear()
        self._post('busy', True)

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
                         name=f"l350-viewer-{description}").start()

    # -----------------------------------------------------------------------
    # STEP 2: THE CATALOGUE
    # -----------------------------------------------------------------------

    def _scan_catalogue(self):
        if not self._require_connection():
            return
        first = FIRST_USER_CURVE if self.user_only_var.get() else MIN_CURVE

        def job():
            self.log(f"Listing curve slots {first} to {MAX_CURVE} "
                     "(one CRVHDR? each, read only)...")

            def progress(done, total, entry):
                self._post('progress', done, total)

            entries = self.backend.scan_catalogue(
                first=first, last=MAX_CURVE, progress=progress,
                should_stop=self._stop_flag.is_set)
            occupied = [e for e in entries
                        if not e.get('is_empty') and not e.get('error')]
            self.log(f"  {len(entries)} slots read; {len(occupied)} hold a "
                     "curve.")
            for entry in occupied:
                self.log(f"    slot {entry['curve']:2d}  "
                         f"{entry['name']:<16s} {entry['serial']:<12s} "
                         f"format {entry.get('format_code')} "
                         f"({entry.get('format_name')})  "
                         f"limit {entry.get('limit')} K  "
                         f"{entry.get('coefficient_name')}")
            unreachable = [e for e in entries if e.get('error')]
            for entry in unreachable:
                self.log(f"    slot {entry['curve']:2d}  no answer: "
                         f"{entry['error']}")
            self._post('catalogue', entries)

        self._run_in_worker("Listing the curve slots", job)

    def _show_catalogue(self, entries):
        self.catalogue = entries
        for row in self.catalogue_table.get_children():
            self.catalogue_table.delete(row)
        occupied = 0
        for entry in entries:
            kind = 'user' if entry.get('is_user_slot') else 'standard'
            if entry.get('error'):
                values = (entry['curve'], kind, '<no answer>', '', '', '', '')
                tags = ('empty',)
            elif entry.get('is_empty'):
                values = (entry['curve'], kind, '(empty)', '', '', '', '')
                tags = ('empty',)
            else:
                occupied += 1
                values = (entry['curve'], kind, entry.get('name', ''),
                          entry.get('serial', ''),
                          f"{entry.get('format_code')} "
                          f"({entry.get('format_name')})",
                          entry.get('limit', ''),
                          entry.get('coefficient_name', ''))
                tags = ()
            self.catalogue_table.insert('', 'end', values=values, tags=tags)
        self.catalogue_label.config(
            text=(f"{occupied} of {len(entries)} slots hold a curve. "
                  "Double-click a row to read it."),
            foreground=self.CLR_STATUS_OK if occupied else self.CLR_STATUS_WARN)
        self.right_tabs.select(1)

    def _catalogue_double_click(self, _event):
        selection = self.catalogue_table.selection()
        if not selection:
            return
        values = self.catalogue_table.item(selection[0], 'values')
        try:
            curve = int(values[0])
        except (IndexError, ValueError):
            return
        self.slot_cb.current(curve - MIN_CURVE)
        self._read_slot()

    # -----------------------------------------------------------------------
    # STEP 3: READ ONE SLOT
    # -----------------------------------------------------------------------

    def _read_slot(self):
        if not self._require_connection():
            return
        curve = self._selected_slot()
        if curve is None:
            messagebox.showerror("No Slot",
                                 "Choose a curve slot first (step 3).")
            return

        def job():
            self.log(f"Reading curve slot {curve} (read only)...")
            header = self.backend.read_header(curve)
            self.log(f"  CRVHDR? {curve} -> {header['_raw']}")
            if header['is_empty']:
                self.log(f"  Slot {curve} is empty: it has no curve name. "
                         "No breakpoints were asked for.")
                self._post('curve', header, [],
                           ["This slot holds no curve."])
                return
            self.log(f"  Name '{header['name']}', serial "
                     f"'{header['serial']}', data format "
                     f"{header['format_code']} ({header['format_name']}), "
                     f"limit {header['limit']} K, coefficient "
                     f"{header['coefficient_name']}.")

            def progress(done, total, point):
                self._post('progress', done, total)

            points, notes = self.backend.read_points(
                curve, progress=progress, should_stop=self._stop_flag.is_set)
            self.log(f"  {len(points)} breakpoints read.")
            for note in notes:
                self.log(f"  Note: {note}")
            self._post('curve', header, points, notes)

        self._run_in_worker("Reading the curve", job)

    def _show_curve(self, header, points, notes):
        self.header = header
        self.points = points
        self.read_notes = list(notes or [])

        for row in self.table.get_children():
            self.table.delete(row)

        format_code = header.get('format_code')
        for number, (units_value, temperature) in enumerate(points, start=1):
            if format_code == 4:
                ohms = fmt_value(10.0 ** units_value)
            elif format_code == 3:
                ohms = fmt_value(units_value)
            else:
                ohms = "-"
            self.table.insert('', 'end',
                              values=(number, fmt_value(units_value),
                                      fmt_value(temperature), ohms))

        kind = 'user' if header.get('is_user_slot') else 'standard'
        if header.get('is_empty'):
            self.headline_label.config(
                text=f"Slot {header['curve']} is empty.")
            self.detail_label.config(
                text=(f"This is a {kind} slot. The instrument answered "
                      f"'{header.get('_raw')}', which carries no curve name, "
                      "so there is nothing stored here."))
            self.problem_label.config(text="")
            self._draw_plot(header, [])
            self.right_tabs.select(0)
            return

        stats = curve_statistics(points, header.get('units_label', '?'))
        self.headline_label.config(
            text=(f"Slot {header['curve']}  ·  {header['name']}  "
                  f"·  {stats.get('count', 0)} breakpoints"))
        if stats:
            self.detail_label.config(
                text=(f"{kind} curve, serial '{header.get('serial')}'. "
                      f"Data format {header.get('format_code')} "
                      f"({header.get('format_name')}), so the sensor column "
                      f"is in {header.get('units_label')}. "
                      f"Temperature runs "
                      f"{fmt_value(stats['temp_min'])} K to "
                      f"{fmt_value(stats['temp_max'])} K; the sensor column "
                      f"runs {fmt_value(stats['units_min'])} to "
                      f"{fmt_value(stats['units_max'])}. "
                      f"Setpoint limit {header.get('limit')} K, "
                      f"coefficient {header.get('coefficient_name')}. "
                      f"Temperature is {stats['temperature_direction']}."))
        else:
            self.detail_label.config(
                text=(f"{kind} curve, but no breakpoints came back. See the "
                      "console."))

        problems = list(self.read_notes)
        if stats and not stats.get('temperature_monotonic'):
            problems.append(
                "The temperature column is not monotonic. A calibration "
                "curve normally is, so check the read before using this.")
        if stats and not stats.get('units_ascending'):
            problems.append(
                "The sensor column is not strictly ascending, which is the "
                "order a Lake Shore stores breakpoints in.")
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
            values = [pair[0] for pair in points]
            axes.plot(temperatures, values, marker='o', markersize=3,
                      linewidth=1.2, color='#8A5A44')
            axes.set_xlabel("Temperature (K)")
            axes.set_ylabel(f"Sensor reading "
                            f"({header.get('units_label', '?')})")
            axes.set_title(f"Slot {header.get('curve')}  ·  "
                           f"{header.get('name')}")
            # A resistance curve spans decades; a log x-axis is the only way
            # the low-temperature end is visible at all. Only used when every
            # temperature is positive, so nothing is silently dropped.
            if min(temperatures) > 0 and max(temperatures) / min(
                    temperatures) > 50:
                axes.set_xscale('log')
            axes.grid(True, which='both', alpha=0.3)
        else:
            axes.text(0.5, 0.5, "no breakpoints", ha='center', va='center',
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
            self.log("Asking each input which curve it uses (read only)...")
            answers = self.backend.read_channel_curves()
            for channel, entry in answers.items():
                self.log(f"  Input {channel}: INCRV? -> {entry['curve']}   "
                         f"INTYPE? -> {entry['intype']}")
            self._post('channels', answers)

        self._run_in_worker("Reading the channel assignments", job)

    def _show_channels(self, answers):
        lines = []
        for channel in INPUT_CHANNELS:
            entry = answers.get(channel, {})
            curve = str(entry.get('curve', '?')).strip()
            name = ""
            try:
                number = int(float(curve))
                match = next((e for e in self.catalogue
                              if e.get('curve') == number), None)
                if match and not match.get('is_empty'):
                    name = f"  {match.get('name')}"
            except (TypeError, ValueError):
                pass
            lines.append(f"Input {channel}:  curve {curve:<4s}{name}")
        if not self.catalogue:
            lines.append("")
            lines.append("(List the slots to see the curve names here.)")
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
        return f"L350_slot{header.get('curve', 0):02d}_{name}"

    def _write(self, path, text, description):
        try:
            with open(path, 'w', encoding='ascii', newline='\n') as handle:
                handle.write(text)
        except Exception as exc:
            self.log(f"Could not write {path}: {type(exc).__name__}: {exc}")
            messagebox.showerror("Save Failed",
                                 f"Could not write {path}:\n{exc}")
            return False
        self.log(f"{description} written to {path}")
        return True

    def _export_340(self):
        if not self._have_curve():
            return
        try:
            text = build_340_text(self.header, self.points)
        except ValueError as exc:
            self.log(f"Cannot write a .340 for this curve: {exc}")
            messagebox.showwarning("Cannot Write .340", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="Save this curve as a Lake Shore .340 file",
            defaultextension=".340",
            initialfile=f"{self._default_stem()}.340",
            filetypes=[("Lake Shore breakpoint curve", "*.340"),
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
        text = build_curve_csv(self.header, self.points,
                               idn=self.backend.idn,
                               address=self.backend.address)
        self._write(path, text, "Curve CSV")

    def _export_catalogue_csv(self):
        if not self.catalogue:
            messagebox.showerror("Nothing To Save",
                                 "List the curve slots first (step 2).")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="Save the curve list as CSV", defaultextension=".csv",
            initialfile=f"L350_curve_list_{stamp}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        text = build_catalogue_csv(self.catalogue, idn=self.backend.idn,
                                   address=self.backend.address)
        self._write(path, text, "Curve list CSV")

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
#     python Sensor_Curve_Viewer_L350_GUI.py --selftest
#
# Case 1 is the one that matters most: it is the proof that the read-only
# guard actually refuses a setting command.


def _selftest_cases():
    """Yield (name, callable) pairs. Each callable raises on failure."""

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    # -- 1: the read-only guard admits queries and nothing else -------------
    def case_read_only_guard():
        for command in ('*IDN?', 'CRVHDR? 21', 'CRVPT? 21,1', 'INCRV? A',
                        'INTYPE? B'):
            check(is_query(command), f"{command} should be allowed")
        for command in ('*RST', '*CLS', 'CRVDEL 21', 'CRVSAV',
                        'CRVHDR 21,NAME,SN,4,325.0,1',
                        'CRVPT 21,1,1.5,300.0', 'INCRV A,21',
                        'INTYPE A,3,1,0,0,1', 'SETP 1,300', 'RANGE 1,3',
                        'RAMP 1,1,0.5'):
            check(not is_query(command), f"{command} must be refused")

    # -- 2: the guard is enforced by the link, not just by the helper -------
    def case_link_refuses():
        class FakeInstrument:
            def __init__(self):
                self.seen = []

            def query(self, command):
                self.seen.append(command)
                return "LSCI,MODEL350,1234,1.0"

            def close(self):
                pass

        link = LakeshoreReadOnlyLink.__new__(LakeshoreReadOnlyLink)
        link.instrument = FakeInstrument()
        link._last_io = 0.0
        link.commands_sent = 0
        try:
            link.ask('CRVDEL 21')
        except ReadOnlyViolation:
            pass
        else:
            raise AssertionError("ask() accepted a non-query")
        check(link.instrument.seen == [],
              f"a refused command still reached the bus: {link.instrument.seen}")
        check(link.ask('*IDN?').startswith("LSCI"), "a query was refused")

    # -- 3: CRVHDR? parsing --------------------------------------------------
    def case_parse_header():
        header = parse_crvhdr("CX-1030-SD  ,X17680    ,4,325.0,1", 21)
        check(header['name'] == "CX-1030-SD", header['name'])
        check(header['serial'] == "X17680", header['serial'])
        check(header['format_code'] == 4, header['format_code'])
        check(header['units_label'] == "log(Ohm)", header['units_label'])
        check(header['limit'] == 325.0, header['limit'])
        check(header['coefficient_name'] == "Negative",
              header['coefficient_name'])
        check(header['is_user_slot'] is True, "21 is a user slot")
        check(header['is_empty'] is False, "this slot is not empty")

    # -- 4: an empty slot is recognised, not guessed at ---------------------
    def case_empty_slot():
        header = parse_crvhdr("            ,          ,0,0.0,1", 45)
        check(header['is_empty'] is True, "a blank name means empty")
        check(header['format_name'] == "unknown", header['format_name'])

    # -- 5: a short reply is refused rather than half-read ------------------
    def case_short_header():
        try:
            parse_crvhdr("DT-670,,3", 2)
        except CurveReadError:
            return
        raise AssertionError("a three-field header should be refused")

    # -- 6: CRVPT? parsing, including an extra field ------------------------
    def case_parse_point():
        check(parse_crvpt("1.64523,325.000", 21, 1) == (1.64523, 325.0),
              "plain reply")
        check(parse_crvpt("+1.64523,+325.000,0", 21, 1) == (1.64523, 325.0),
              "a third field is ignored")
        try:
            parse_crvpt("no data", 21, 1)
        except CurveReadError:
            pass
        else:
            raise AssertionError("a non-numeric reply should be refused")

    # -- 7: numbers are written in plain decimal ----------------------------
    def case_fmt_value():
        check(fmt_value(1.6452312) == "1.64523", fmt_value(1.6452312))
        check(fmt_value(325.0) == "325.0", fmt_value(325.0))
        check('e' not in fmt_value(1.23e-7).lower(), fmt_value(1.23e-7))
        check('.' in fmt_value(4), fmt_value(4))

    # -- 8: the .340 export says how many breakpoints it really has ---------
    def case_build_340():
        header = parse_crvhdr("CX-1030-SD,X17680,4,325.0,1", 21)
        points = [(1.0, 300.0), (2.0, 100.0), (3.0, 4.0)]
        text = build_340_text(header, points)
        check("Data Format:    4" in text, text)
        check("Number of Breakpoints:   3" in text, text)
        check("Temperature coefficient:  1 (Negative)" in text, text)
        rows = [line for line in text.splitlines()
                if re.match(r'^\s*\d+\s+[-\d.]+\s+[-\d.]+\s*$', line)]
        check(len(rows) == 3, f"{len(rows)} data rows")

    # -- 9: a .340 written here reads back with the same numbers ------------
    #  The reader is the one in pica/cryocon/Sensor_Curve_Loader_CC34_GUI.py,
    #  reimplemented here in three lines so this file stays self-contained.
    def case_340_round_trip():
        header = parse_crvhdr("CX-1030-SD,X17680,4,325.0,1", 21)
        points = [(1.0, 300.0), (2.0, 100.0), (3.0, 4.0)]
        text = build_340_text(header, points)
        stated = int(re.search(r'Number of Breakpoints:\s*(\d+)',
                               text).group(1))
        read = []
        for line in text.splitlines():
            tokens = line.split()
            if len(tokens) != 3:
                continue
            try:
                index, units, temperature = (float(t) for t in tokens)
            except ValueError:
                continue
            if index != int(index):
                continue
            read.append((units, temperature))
        check(stated == len(read) == 3, f"{stated} stated, {len(read)} read")
        check(read == points, f"{read} != {points}")

    # -- 10: a format code the instrument did not give is refused ----------
    def case_340_refuses_unknown_format():
        header = parse_crvhdr("SOMETHING,SN,9,325.0,1", 21)
        try:
            build_340_text(header, [(1.0, 300.0)])
        except ValueError as exc:
            check("Data Format" in str(exc), str(exc))
            return
        raise AssertionError("format 9 should not produce a .340")

    # -- 11: statistics describe the curve, they do not judge it ------------
    def case_statistics():
        stats = curve_statistics([(1.0, 4.0), (2.0, 100.0), (3.0, 300.0)],
                                 "log(Ohm)")
        check(stats['count'] == 3, stats)
        check(stats['temp_min'] == 4.0 and stats['temp_max'] == 300.0, stats)
        check(stats['temperature_monotonic'] is True, stats)
        check(stats['units_ascending'] is True, stats)
        wobbly = curve_statistics([(1.0, 4.0), (2.0, 300.0), (3.0, 100.0)],
                                  "log(Ohm)")
        check(wobbly['temperature_monotonic'] is False, wobbly)

    # -- 12: the CSV carries the header and one row per breakpoint ---------
    def case_curve_csv():
        header = parse_crvhdr("CX-1030-SD,X17680,4,325.0,1", 21)
        text = build_curve_csv(header, [(1.0, 300.0), (2.0, 100.0)],
                               idn="LSCI,MODEL350", address="GPIB1::12")
        check("# Curve slot: 21" in text, text)
        check("Index,Units_log(Ohm),Temperature_K" in text, text)
        data_rows = [line for line in text.splitlines()
                     if line and not line.startswith('#')
                     and not line.startswith('Index')]
        check(len(data_rows) == 2, data_rows)

    # -- 13: the catalogue CSV lists every slot it was given ---------------
    def case_catalogue_csv():
        entries = [parse_crvhdr("DT-670,,2,500.0,2", 2),
                   parse_crvhdr("      ,      ,0,0.0,1", 45)]
        text = build_catalogue_csv(entries, idn="LSCI", address="GPIB1::12")
        lines = [line for line in text.splitlines()
                 if line and not line.startswith('#')]
        check(len(lines) == 3, lines)          # header row plus two slots
        check(",standard," in lines[1], lines[1])
        check(lines[2].endswith(",yes"), lines[2])

    return [
        ("read-only guard admits queries only", case_read_only_guard),
        ("the link itself refuses a setting command", case_link_refuses),
        ("CRVHDR? reply is parsed", case_parse_header),
        ("an empty slot is recognised", case_empty_slot),
        ("a short header reply is refused", case_short_header),
        ("CRVPT? reply is parsed", case_parse_point),
        ("numbers are written in plain decimal", case_fmt_value),
        (".340 export counts its own breakpoints", case_build_340),
        (".340 export round-trips", case_340_round_trip),
        (".340 export refuses an unknown format", case_340_refuses_unknown_format),
        ("curve statistics", case_statistics),
        ("curve CSV", case_curve_csv),
        ("catalogue CSV", case_catalogue_csv),
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
