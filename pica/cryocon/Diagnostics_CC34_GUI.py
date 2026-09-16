"""
Module: Diagnostics_CC34_GUI.py
Purpose: Read-only diagnostic console for the Cryocon (Cryogenic Control
         Systems) Model 34 Cryogenic Temperature Controller.

         Asks the instrument what it actually does, instead of trusting a
         manual for a different model, and writes the answers to a log
         file you can carry away.

WHY THIS EXISTS

The Cryo-con SCPI set is shared across the 24C / 32 / 32B / 34 / 62
family, and PICA's Cryo-con modules were written against the Model 32/32B
manual because that is the manual that was to hand. The Model 34's own
manual (shipped as "The User Interface - Cryogenic Control Systems,
Inc..pdf") does not document all of it. Six mnemonics in daily use here
are absent from it:

    LOOP <n>:OUTPWR?     INPUT <ch>:SENPR?
    LOOP <n>:MAXPWR?     INPUT <ch>:ISENIX?
    LOOP <n>:MAXSET?     INPUT <ch>:USENIX?

On 17 Sep 2026 LOOP:OUTPWR? turned out to be absent from the 24C manual
as well, and it had been sitting in the dielectric temperature scan's
per-sweep heater read. What made that dangerous is HOW a Cryo-con refuses
a command it does not recognise: it does not answer. No error string, no
entry in the error queue - just silence, so the call surfaces as a VISA
timeout, indistinguishable at the call site from a dead bus. Inside a
measurement loop, in front of a retry-forever reconnect handler, that is
a night of reconnecting over a logging column.

Guessing is what put it there. This program stops the guessing.

WHAT IT DOES

    Core survey (always run, about a minute)
      - every query PICA's Cryo-con modules rely on, timed, with the
        reply recorded verbatim and a verdict per command
      - the Master Sensor Table swept index by index: every sensor type
        this controller supports, which user slots hold a calibrated
        curve, the type strings the firmware itself uses, and the curve
        each input is running on
      - how replies are framed on the wire, before anything strips them
      - whether the inter-command pacing gap is still needed
      - the error queue afterwards

    Deep measurements (tick them; each costs real time)
      - reading resolution and short-term noise
      - how often the reading actually changes, which is the shortest
        poll interval worth using
      - the lowest VISA timeout this unit still answers within
      - a soak test
      - how much SCPI syntax sloppiness the parser forgives

SAFETY

Every probe is a QUERY. This program has no write path at all: no *RST
(on a Cryo-con that is a ~15 s hardware reset), no CONTROL, no STOP, no
setpoint, loop, heater, range or configuration command. It is safe to run
against a controller that is driving a live experiment. The only state it
touches is its own VISA session timeout.

v1.0, 17 Sep 2026.
"""

import os
import queue
import re
import statistics
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import ttk, messagebox, scrolledtext, filedialog

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# CRYOCON LINK  (read-only; self-contained, as every PICA module is)
# ===============================================================================

CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON")
CRYOCON_ADDRESS_HINT = "::23::INSTR"     # a hint only; *IDN? decides
CRYOCON_INPUT_CHANNELS = ("A", "B", "C", "D")

CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before the first command
CRYOCON_MIN_GAP_S = 0.08            # minimum gap between consecutive operations
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries

# Replies that are status, not data. The front panel shows a run of dashes
# for a sensor fault and a run of dots for a reading off the sensor's
# calibration curve; over the bus those arrive as literal strings.
CRYOCON_STATUS_STRINGS = {
    '-------': "sensor fault: the sensor is open, disconnected or shorted",
    '.......': ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    'N/A': "the channel is disabled, or the value does not apply",
    'NACK': "the instrument did not acknowledge the command",
}

_CRYOCON_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
# Matched by shape, because SYSTEM:DRES sets how long the runs are.
_CRYOCON_FAULT_RE = re.compile(r'^-{2,}$')
_CRYOCON_RANGE_RE = re.compile(r'^\.{2,}$')


class CryoconStatusError(ValueError):
    """A query returned a Cryo-con status string where a number was
    expected. NOT a comm error: the instrument answered, the sensor did
    not, so reconnecting cannot cure it."""


def is_cryocon_idn(idn):
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


def parse_cryocon_number(raw, what, channel=None):
    """Turn a Cryo-con reply into a float, or say precisely why it is not.

    Handles the three things a plain float() call does not: status
    strings, a trailing unit character ('77.350K'), and multi-field
    replies, which come back separated by semicolons.
    """
    text = str(raw).strip()
    where = f" on channel {channel}" if channel else ""
    if ';' in text:
        text = text.split(';')[0].strip()
    if text in CRYOCON_STATUS_STRINGS:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS[text]}.")
    if _CRYOCON_FAULT_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS['-------']}.")
    if _CRYOCON_RANGE_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': sensor fault, no "
            f"sensor, or {CRYOCON_STATUS_STRINGS['.......']}.")
    if not text:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned an empty reply.")
    try:
        return float(text)
    except ValueError:
        match = _CRYOCON_NUMBER_RE.match(text)
        if match:
            return float(match.group(0))
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}', which is not a "
            "number this program recognises.")


class CryoconLink:
    """One paced, read-only VISA session, opened with retries.

    There is deliberately no write() on this class. A write path that
    exists is a write path that can be called by mistake, and the whole
    value of this program is that it cannot disturb a running experiment.
    """

    def __init__(self, visa_address, timeout_ms=CRYOCON_TIMEOUT_MS, log=None):
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

    def _drop_session(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception:
                pass
            finally:
                self.instrument = None

    def _open_and_identify(self):
        """Open, settle, then ask '*IDN?' with retries.

        On a Rev 3.03A unit the first command of a session, seconds after
        a bus scan read the instrument perfectly well, could die inside
        viWrite with VI_ERROR_TMO. A timeout on the WRITE means the
        instrument stopped accepting bytes for a moment, not that it is
        absent, so the cure is to wait and ask again.
        """
        last_error = None
        for attempt in range(1, CRYOCON_CONNECT_ATTEMPTS + 1):
            try:
                self.instrument = self.rm.open_resource(self.address)
                self.instrument.timeout = self.timeout_ms
                # The Cryo-con GPIB port frames lines with EOI and no EOS
                # character, so the PyVISA defaults are left alone.
                time.sleep(CRYOCON_OPEN_SETTLE_S)
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
                    self._log(f"  No answer at {self.address} (attempt "
                              f"{attempt} of {CRYOCON_CONNECT_ATTEMPTS}): "
                              f"{type(exc).__name__}. Retrying.")
                    time.sleep(CRYOCON_RETRY_WAIT_S)
        raise ConnectionError(
            f"No reply from a Cryo-con at {self.address} after "
            f"{CRYOCON_CONNECT_ATTEMPTS} attempts. Last error: {last_error}. "
            "Check that the instrument is powered, that its SYS menu has "
            "RIO-Port set to GPIB rather than RS-232, and that RIO-Address "
            "matches this VISA address.")

    def _pace(self):
        gap = CRYOCON_MIN_GAP_S - (time.time() - self._last_io)
        if gap > 0:
            time.sleep(gap)

    def query(self, command):
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        self._pace()
        try:
            return self.instrument.query(command).strip()
        finally:
            self._last_io = time.time()

    @property
    def is_connected(self):
        return self.instrument is not None

    def close(self):
        """Close the session only. Nothing is written on the way out, so
        whatever is driving the cryostat carries on untouched."""
        self._drop_session()


# ===============================================================================
# THE SURVEY
# ===============================================================================
#
# Probes, as (group, command, status, note).
#
#   'DOC'    the Model 34 manual documents this command
#   'UNDOC'  it does not - these are the ones being settled
#   'FORM'   an alternate or short form the manual shows; checking that
#            this firmware really accepts it

CC34_PROBES = [
    # -- identity and firmware ------------------------------------------
    ("Identity", "*IDN?", "DOC", "manual: 'Cryocon Model 34 Rev <fw><hw>'"),
    ("Identity", "SYSTEM:HWREV?", "DOC", "hardware revision"),
    ("Identity", "SYSTEM:FWREV?", "DOC", "firmware revision"),
    ("Identity", "SYSTEM:NAME?", "DOC", "instrument name string"),
    ("Identity", "SYSTEM:ADRS?", "DOC", "IEEE-488/USB address"),
    ("Identity", "*OPC?", "DOC", "expect '1'"),
    ("Identity", "*ESR?", "DOC", "standard event register"),
    ("Identity", "*ESE?", "DOC", "standard event enable register"),
    ("Identity", "*STB?", "UNDOC",
     "the manual describes the status byte but never lists *STB? itself"),
    ("Identity", "SYSTEM:REMOTE?", "UNDOC",
     "the summary lists SYSTEM:REMOTE as set-only - is there a query?"),

    # -- how a reading actually comes back ------------------------------
    # The group behind the '77.350K' bug: a number with a trailing unit
    # character, which plain float() rejects.
    ("Reading shape", "INPUT? A", "DOC",
     "does the reply carry a trailing unit character?"),
    ("Reading shape", "INPUT A:UNITS?", "DOC", "expect K, C, F, V or O"),
    ("Reading shape", "INPUT A:TEMPER?", "DOC",
     "manual's alternate form of INPUT? A"),
    ("Reading shape", "INP? A", "FORM", "documented short form"),
    ("Reading shape", "INP A:TEMP?", "FORM", "documented short form"),
    ("Reading shape", "INPUT? 0", "DOC", "numeric channel form (0-3)"),
    ("Reading shape", "INPUT? CHA", "DOC", "channel-tag form"),
    ("Reading shape", "INP A:TEMP?;UNIT?", "FORM",
     "manual's compound-query example; expect '27.9906K' or two fields"),

    # -- heater read-back ------------------------------------------------
    ("Heater read-back", "LOOP 1:HTRREAD?", "DOC",
     "THE documented heater read-back; manual example reply '22%'"),
    ("Heater read-back", "LOOP 2:HTRREAD?", "DOC", "same, loop 2"),
    ("Heater read-back", "LOOP 1:HTRR?", "FORM", "documented short form"),
    ("Heater read-back", "LOOP 1:OUTPWR?", "UNDOC",
     "NOT in the Model 34 or 24C manual - does this firmware take it?"),
    ("Heater read-back", "LOOP 1:PMANUAL?", "DOC",
     "manual-mode output power setting"),
    ("Heater read-back", "LOOP 3:HTRREAD?", "UNDOC",
     "a Model 34 has two loops; a 24C has four. Expect this to be refused "
     "- it settles the loop count from the instrument itself"),

    # -- the mnemonics inherited from the 32B manual ---------------------
    ("Undocumented set", "LOOP 1:MAXPWR?", "UNDOC",
     "not in the Model 34 manual"),
    ("Undocumented set", "LOOP 1:MAXSET?", "UNDOC",
     "not in the Model 34 manual"),
    ("Undocumented set", "INPUT A:SENPR?", "UNDOC",
     "not in the Model 34 manual; raw sensor Volts/Ohms"),
    ("Undocumented set", "INPUT A:ISENIX?", "UNDOC",
     "not in the Model 34 manual; factory sensor index"),
    ("Undocumented set", "INPUT A:USENIX?", "UNDOC",
     "not in the Model 34 manual; user curve index"),
    ("Undocumented set", "INPUT A:SENIX?", "DOC",
     "the form the Model 34 manual DOES document"),

    # -- loop state, read only -------------------------------------------
    ("Loop state", "CONTROL?", "DOC", "are the loops engaged?"),
    ("Loop state", "SYSTEM:LOOP?", "DOC", "expect ON or OFF"),
    ("Loop state", "LOOP 1:SOURCE?", "DOC", "controlling input channel"),
    ("Loop state", "LOOP 1:SETPT?", "DOC", "setpoint"),
    ("Loop state", "LOOP 1:TYPE?", "DOC", "OFF/PID/MAN/TABLE/RAMPP/RAMPT"),
    ("Loop state", "LOOP 1:RANGE?", "DOC",
     "primary heater range - the manual's example sets it as '5.0W', the "
     "front panel calls the same thing Hi/Mid/Low. Which comes back?"),
    ("Loop state", "LOOP 1:LOAD?", "DOC", "25 or 50 ohm"),
    ("Loop state", "LOOP 1:RATE?", "DOC", "ramp rate, units/min"),
    ("Loop state", "LOOP 1:RAMP?", "DOC", "is a ramp in progress?"),
    ("Loop state", "LOOP 1:PGAIN?", "DOC", "P"),
    ("Loop state", "LOOP 1:IGAIN?", "DOC",
     "I - on a Cryo-con this is SECONDS and larger is SLOWER, the opposite "
     "sense to a Lakeshore 350. Lakeshore PID numbers never transfer"),
    ("Loop state", "LOOP 1:DGAIN?", "DOC", "D"),
    ("Loop state", "LOOP 1:TABLEIX?", "DOC", "PID table index"),
    ("Loop state", "LOOP 1:NAME?", "DOC", "loop name string"),
    ("Loop state", "LOOP 2:SOURCE?", "DOC", "loop 2 source channel"),
    ("Loop state", "LOOP 2:TYPE?", "DOC", "loop 2 control type"),
    ("Loop state", "PIDTABLE? 1", "DOC",
     "name of PID table 1 - what LOOP:TABLEIX selects"),
    ("Loop state", "PIDTABLE 1:NENTRY?", "DOC", "its number of entries"),
    ("Loop state", "HEATER:AUTOTUNE:STATUS?", "DOC",
     "autotune state; the manual prefixes autotune with HEATER or AOUT"),
    ("Loop state", "HEATER:AUTOTUNE:DELTAP?", "DOC",
     "autotune maximum power excursion"),

    # -- safety cut-out ---------------------------------------------------
    ("Safety", "OVERTEMP:ENABLE?", "DOC",
     "over-temperature disconnect - the Cryo-con equivalent of TLIMIT"),
    ("Safety", "OVERTEMP:SOURCE?", "DOC", "its source channel"),
    ("Safety", "OVERTEMP:TEMP?", "DOC", "its trip temperature"),
    ("Safety", "SYSTEM:LOCKOUT?", "DOC", "front-panel keypad lockout"),

    # -- system -----------------------------------------------------------
    ("System", "SYSTEM:AMBIENT?", "DOC", "internal reference temperature"),
    ("System", "SYSTEM:HTRHST?", "DOC", "heater heatsink temperature"),
    ("System", "SYSTEM:DISTC?", "DOC",
     "display filter time constant - it filters EVERY reported reading"),
    ("System", "SYSTEM:DRES?", "DOC",
     "display resolution - it sets the LENGTH of the '-------' fault run"),
    ("System", "SYSTEM:LINEFREQ?", "DOC", "AC line frequency setting"),
    ("System", "SYSTEM:CJTEMP?", "DOC", "cold-junction compensation temp"),
    ("System", "SYSTEM:REMLED?", "DOC", "remote LED state"),
    ("System", "SYSTEM:CONTRAST?", "DOC", "VFD contrast (Model 34/62 only)"),
    ("System", "RELAYS? 1", "DOC", "relay status (Model 34/62 only)"),
    ("System", "RELAYS? 2", "DOC", "relay 2"),
    ("System", "STATS:TIME?", "DOC",
     "minutes of accumulated input statistics - a free drift measure the "
     "passive modules could log without any extra plumbing"),
]

# Run for each of the four inputs.
CC34_CHANNEL_PROBES = [
    ("INPUT? {ch}", "DOC", "reading, in that channel's OWN display units"),
    ("INPUT {ch}:UNITS?", "DOC", "K, C, F, V or O"),
    ("INPUT {ch}:SENIX?", "DOC",
     "sensor index into the Master Sensor Table; 0 means no sensor"),
    ("INPUT {ch}:NAME?", "DOC", "channel name string"),
    ("INPUT {ch}:BIAS?", "DOC", "excitation bias type (Model 32/34 only)"),
    ("INPUT {ch}:ALARM?", "DOC", "alarm status: '--', 'SF', 'HI' or 'LO'"),
    ("INPUT {ch}:ALARM:HIGHEST?", "DOC", "high alarm setpoint"),
    ("INPUT {ch}:ALARM:LOWEST?", "DOC", "low alarm setpoint"),
    ("INPUT {ch}:ALARM:HIENA?", "DOC", "high alarm enable"),
    ("INPUT {ch}:ALARM:LOENA?", "DOC", "low alarm enable"),
    ("INPUT {ch}:ALARM:FAULT?", "DOC", "sensor-fault alarm enable"),
    ("INPUT {ch}:MINIMUM?", "DOC", "statistics since the last STATS:RESET"),
    ("INPUT {ch}:MAXIMUM?", "DOC", "statistics since the last STATS:RESET"),
    ("INPUT {ch}:VARIANCE?", "DOC", "statistics since the last STATS:RESET"),
    ("INPUT {ch}:SLOPE?", "DOC",
     "best-fit drift rate - would answer 'is it settled?' in one query"),
]


# -- sensor curves and sensor types -------------------------------------
#
# The Model 34 stores sensor types in a Master Sensor Table: a factory
# block that cannot be edited, then twelve user slots for calibrated
# sensors. A Cernox has to go in one of those - the Model 34 ships no
# Cernox curve of its own, its factory list stops at two RuOx entries.
#
# THE MANUAL CONTRADICTS ITSELF ABOUT WHERE THE USER SLOTS START.
# Appendix A's "Factory Installed Curves" runs index 0-13, ending at
# '13 AuFe 0.07%'. Its "User Installed Sensor Curves" on the very next
# page says 'Senix index 10 = User 1' through '21 = User 12'. Index 10
# cannot be both 'TC type K' and 'User Sensor 1'. Work on this firmware
# in Sep 2026 put the user block at 15-26, matching neither.
#
# So the map is read off the instrument, not out of the manual. Whatever
# comes back IS the map, and the type strings that come back ARE this
# firmware's vocabulary - which matters, because CALCUR silently discards
# a whole curve block whose type field it cannot identify.
CC34_SENSOR_TABLE_MAX_INDEX = 30
CC34_SENSOR_TABLE_DETAIL = ("SENTYPE {ix}:TYPE?", "SENTYPE {ix}:MULTIPLY?")

# Syntax the parser may or may not forgive. Every one of these is the
# same harmless reading query wearing different clothes. Worth knowing:
# the manual's OWN query syntax is written 'INPUT ? <channel>', with a
# space before the '?', while every worked example writes 'INPUT? B'.
CC34_SYNTAX_VARIANTS = [
    ("INPUT? A", "the form every PICA module sends"),
    ("INPUT ? A", "the manual's formal Query Syntax line"),
    ("INPUT?A", "no space before the channel"),
    ("input? a", "lower case"),
    ("  INPUT? A  ", "leading and trailing whitespace"),
    ("INP?A", "short form, no space"),
]

CC34_BURST_COMMAND = "INPUT? A"
CC34_BURST_N = 10

# Deep sections. Each is off by default because each costs real time.
CC34_NOISE_SAMPLES = 30             # repeated reads for resolution/noise
CC34_UPDATE_RATE_SECONDS = 6.0      # how long to watch for a value change
CC34_SOAK_QUERIES = 200             # soak-test length
CC34_TIMEOUT_LADDER_MS = (3000, 1000, 500, 200, 100, 50)
CC34_TIMEOUT_LADDER_TRIES = 5

# A shorter VISA timeout while the survey runs. A command this firmware
# does not know costs one whole timeout, and there are a dozen of those;
# at the normal 10 s the survey would be minutes of dead air.
CC34_SURVEY_TIMEOUT_MS = 3000

# CALCUR? is deliberately NOT sent. It returns a whole curve block - a
# header plus up to 200 points - and one query() reads one line, leaving
# the rest queued on the bus and desynchronising every reply after it.
# That is not a thing to do to a controller running an experiment. Curve
# contents have their own read-only window: Sensor_Curve_Viewer_CC34_GUI.
CC34_NOT_SENT = [
    ("CALCUR? <ix>",
     "returns a multi-line curve block; one query() would leave the rest "
     "on the bus. Use Sensor_Curve_Viewer_CC34_GUI.py instead."),
    ("*RST, *CLS, CONTROL, STOP, SYSTEM:NVSAVE",
     "writes, and *RST on a Cryo-con is a ~15 s hardware reset. This "
     "program has no write path at all."),
    ("SENTYPE <ix>:NAME <name>, CALCUR <n>, INPUT <ch>:SENIX <ix>",
     "writes. Curve loading belongs to Sensor_Curve_Loader_CC34_GUI.py, "
     "which asks before it changes anything."),
]

# The optional deep sections, as (key, label, rough cost, default).
CC34_DEEP_SECTIONS = [
    ("noise", "Reading resolution and short-term noise",
     f"{CC34_NOISE_SAMPLES} reads, ~5 s", False),
    ("update", "How often the reading actually changes",
     f"~{CC34_UPDATE_RATE_SECONDS:.0f} s", False),
    ("timeout", "Lowest VISA timeout this unit answers within",
     f"{len(CC34_TIMEOUT_LADDER_MS)} steps, ~15 s", False),
    ("syntax", "How much SCPI syntax sloppiness the parser forgives",
     f"{len(CC34_SYNTAX_VARIANTS)} variants, ~10 s", False),
    ("scan", "Cost of reading all four channels round-robin",
     "10 rounds, ~5 s", False),
    ("soak", "Soak test",
     f"{CC34_SOAK_QUERIES} queries, ~{CC34_SOAK_QUERIES * 0.1:.0f} s", False),
]


class CryoconSurvey:
    """Runs the survey against a live CryoconLink, on a worker thread.

    Results go onto a queue as ('line', text) / ('progress', a, b) /
    ('done', rows). Nothing here touches Tk: a worker that calls into the
    window, or into root.after(), raises 'main thread is not in main
    loop' as soon as the main thread is not sitting in mainloop(), and
    the message is lost. The window drains the queue from its own after()
    chain instead.
    """

    def __init__(self, link, out_queue, stop_event, deep=()):
        self.link = link
        self.queue = out_queue
        self.stop = stop_event
        self.deep = set(deep)
        self.rows = []          # (group, command, status, verdict, ms, raw)
        self.sensor_table = {}  # index -> (name, type, multiplier) or None

    # -- output --

    def _emit(self, text=""):
        self.queue.put(("line", text))

    def _progress(self, done, total):
        self.queue.put(("progress", done, total))

    def _heading(self, title):
        self._emit("")
        self._emit(f"--- {title} " + "-" * max(4, 70 - len(title)))

    # -- one probe --

    def _probe(self, group, command, status, note):
        """Send one query. Never raises: a probe that fails IS the result."""
        started = time.time()
        try:
            raw = self.link.query(command)
            elapsed = (time.time() - started) * 1000.0
            if raw == "":
                verdict = "EMPTY"
            elif raw.strip().upper().startswith("NACK"):
                verdict = "NACK"
            else:
                verdict = "OK"
        except Exception as exc:
            elapsed = (time.time() - started) * 1000.0
            raw = f"{type(exc).__name__}: {exc}"
            # A Cryo-con does not answer a command it does not know, so a
            # timeout here is the instrument saying "I do not take that",
            # not necessarily a bus fault.
            verdict = "TIMEOUT" if ("TMO" in raw or "imeout" in raw) else "ERROR"
        self.rows.append((group, command, status, verdict, elapsed, raw))
        flag = {"OK": "  ", "NACK": "!!", "EMPTY": "??",
                "TIMEOUT": "XX", "ERROR": "XX"}.get(verdict, "??")
        self._emit(f" {flag} {command:<26} {verdict:<8} "
                   f"{elapsed:7.0f} ms  {raw!r}")
        if note:
            self._emit(f"                                   -- {note}")
        return verdict

    # -- the run --

    def run(self):
        try:
            self._run()
        except Exception:
            # Formatted HERE, on the thread where the exception is live.
            # traceback.format_exc() on the Tk thread has no live
            # exception and prints 'NoneType: None'.
            self._emit("")
            self._emit("SURVEY ABORTED - unexpected fault:")
            for line in traceback.format_exc().splitlines():
                self._emit("    " + line)
            self.queue.put(("done", self.rows))

    def _run(self):
        channel_probes = [
            (f"Channel {ch}", template.format(ch=ch), status, note)
            for ch in CRYOCON_INPUT_CHANNELS
            for template, status, note in CC34_CHANNEL_PROBES
        ]
        all_probes = list(CC34_PROBES) + channel_probes
        total = len(all_probes)

        self._banner(total)

        last_group = None
        for index, (group, command, status, note) in enumerate(all_probes, 1):
            if self.stop.is_set():
                self._emit("")
                self._emit("-- stopped by the operator --")
                break
            if group != last_group:
                self._heading(group)
                last_group = group
            self._probe(group, command, status, note)
            self._progress(index, total)

        if not self.stop.is_set():
            self._sensor_table()
            self._framing()
            self._bus_timing()
            if "noise" in self.deep:
                self._noise()
            if "update" in self.deep:
                self._update_rate()
            if "scan" in self.deep:
                self._channel_scan()
            if "syntax" in self.deep:
                self._syntax()
            if "timeout" in self.deep:
                self._timeout_ladder()
            if "soak" in self.deep:
                self._soak()
            self._error_queue()
            self._not_sent()
        self._summary()
        self.queue.put(("done", self.rows))

    def _banner(self, total):
        self._emit("=" * 78)
        self._emit("CRYOCON MODEL 34 DIAGNOSTICS - read-only behaviour survey")
        self._emit("=" * 78)
        self._emit(f"Started        : {datetime.now():%Y-%m-%d %H:%M:%S}")
        self._emit(f"VISA address   : {self.link.address}")
        self._emit(f"*IDN?          : {self.link.idn}")
        self._emit(f"Probe timeout  : {CC34_SURVEY_TIMEOUT_MS} ms "
                   f"(PICA's normal operating timeout is "
                   f"{CRYOCON_TIMEOUT_MS} ms)")
        self._emit(f"Pacing gap     : {CRYOCON_MIN_GAP_S * 1000:.0f} ms "
                   "between operations")
        self._emit(f"Core probes    : {total}")
        deep = sorted(self.deep) or ["none"]
        self._emit(f"Deep sections  : {', '.join(deep)}")
        self._emit("")
        self._emit("Every probe is a QUERY. Nothing is written to the")
        self._emit("instrument, so its loops, heater and settings are")
        self._emit("untouched. Columns: verdict, round trip, raw reply.")
        self._emit("")
        self._emit("  OK       answered")
        self._emit("  TIMEOUT  no reply - on a Cryo-con this is how an")
        self._emit("           unrecognised command fails. There is no error")
        self._emit("           string and nothing lands in the error queue.")
        self._emit("  NACK     explicitly not acknowledged")
        self._emit("  EMPTY    answered with nothing")

    # -- sensor types and calibration curves --

    def _sensor_table(self):
        """Read the Master Sensor Table off the instrument, index by index.

        This is the section that says what sensor types this controller
        supports and which curves are loaded where. Swept rather than
        taken from the manual - see the note on
        CC34_SENSOR_TABLE_MAX_INDEX for why.
        """
        self._heading("Master Sensor Table")
        self._emit("  Every sensor type this controller knows, read from the")
        self._emit("  instrument. The factory block cannot be edited; the")
        self._emit("  user slots are where a calibrated Cernox or diode goes.")
        self._emit("")
        self._emit(f"  {'ix':>3}  {'name':<22} {'type':<12} multiplier")
        self._emit(f"  {'-' * 3}  {'-' * 22} {'-' * 12} {'-' * 10}")

        for index in range(0, CC34_SENSOR_TABLE_MAX_INDEX + 1):
            if self.stop.is_set():
                return
            try:
                name = self.link.query(f"SENTYPE? {index}")
            except Exception as exc:
                self.sensor_table[index] = None
                self._emit(f"  {index:>3}  -- no reply "
                           f"({type(exc).__name__}) --")
                # Past the end of the table every index behaves the same.
                # Two in a row is enough to stop asking.
                if (index >= 1
                        and self.sensor_table.get(index - 1, "x") is None):
                    self._emit(f"       (nothing answers from index "
                               f"{index - 1} on; end of table)")
                    return
                continue
            details = {}
            for template in CC34_SENSOR_TABLE_DETAIL:
                try:
                    details[template] = self.link.query(
                        template.format(ix=index))
                except Exception as exc:
                    details[template] = f"<{type(exc).__name__}>"
            stype = details.get("SENTYPE {ix}:TYPE?", "")
            mult = details.get("SENTYPE {ix}:MULTIPLY?", "")
            self.sensor_table[index] = (name, stype, mult)
            self._emit(f"  {index:>3}  {name:<22} {stype:<12} {mult}")

    # -- how replies are framed on the wire --

    def _framing(self):
        r"""The reply exactly as it arrives, before anything strips it.

        CryoconLink.query() returns reply.strip(), so a trailing '\r\n',
        a stray space or a unit character is invisible everywhere else in
        these programmes. The '77.350K' bug lived in that gap.
        """
        self._heading("Reply framing")
        instrument = self.link.instrument
        for attribute in ("read_termination", "write_termination",
                          "send_end", "timeout", "chunk_size"):
            try:
                self._emit(f"  session {attribute:<18} = "
                           f"{getattr(instrument, attribute, '<n/a>')!r}")
            except Exception as exc:
                self._emit(f"  session {attribute:<18} = <{exc}>")
        self._emit("  Replies below are UNSTRIPPED - what came off the bus:")
        for command in ("*IDN?", "INPUT? A", "INPUT A:UNITS?",
                        "LOOP 1:HTRREAD?", "SYSTEM:LOOP?"):
            if self.stop.is_set():
                return
            self._emit(f"    {command:<20} {self._raw(command)!r}")

    def _raw(self, command):
        """One unpaced, unstripped query straight at the resource.

        The link's own clock is kept honest so whatever runs after this
        still gets its pacing gap.
        """
        try:
            reply = self.link.instrument.query(command)
            self.link._last_io = time.time()
            return reply
        except Exception as exc:
            self.link._last_io = time.time()
            return f"<{type(exc).__name__}: {exc}>"

    # -- bus behaviour --

    def _bus_timing(self):
        """Is the pacing gap still needed on this unit?

        CRYOCON_MIN_GAP_S was added after a Rev 3.03A unit refused a
        write that followed too closely on the last one. Nobody has
        measured since, and it costs 80 ms on every single operation.
        """
        self._heading("Bus timing")
        self._emit(f"  {CC34_BURST_N} x {CC34_BURST_COMMAND!r}, paced then "
                   "unpaced. Queries only.")
        paced = self._burst(paced=True)
        self._emit(f"  paced   ({CRYOCON_MIN_GAP_S * 1000:.0f} ms gap): "
                   f"{paced['ok']}/{paced['n']} answered, "
                   f"min {paced['min']:.0f} / mean {paced['mean']:.0f} / "
                   f"max {paced['max']:.0f} ms")
        unpaced = self._burst(paced=False)
        self._emit(f"  unpaced (no gap)   : "
                   f"{unpaced['ok']}/{unpaced['n']} answered, "
                   f"min {unpaced['min']:.0f} / mean {unpaced['mean']:.0f} / "
                   f"max {unpaced['max']:.0f} ms")
        if unpaced["ok"] < unpaced["n"]:
            self._emit("  => back-to-back traffic DOES drop commands on this "
                       "unit. Keep the pacing gap.")
        else:
            self._emit("  => back-to-back traffic answered cleanly here. The "
                       f"pacing gap costs {CRYOCON_MIN_GAP_S * 1000:.0f} ms "
                       "on every operation.")
        for entry in unpaced["failures"]:
            self._emit(f"     failure: {entry}")

    def _burst(self, paced):
        command = CC34_BURST_COMMAND
        times, ok, failures = [], 0, []
        for _ in range(CC34_BURST_N):
            if self.stop.is_set():
                break
            started = time.time()
            try:
                if paced:
                    self.link.query(command)
                else:
                    self.link.instrument.query(command)
                    self.link._last_io = time.time()
                ok += 1
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
            times.append((time.time() - started) * 1000.0)
        if not times:
            times = [0.0]
        return {"n": len(times), "ok": ok, "failures": failures,
                "min": min(times), "max": max(times),
                "mean": sum(times) / len(times)}

    # -- deep section: resolution and noise --

    def _noise(self):
        """What is the smallest change this thermometer can report, and
        how much does a still reading wander?

        Both numbers set the floor for any tolerance window: asking a
        control loop to settle inside a band narrower than the noise is
        asking it to wait forever.
        """
        self._heading("Reading resolution and short-term noise")
        values, raws = [], []
        for _ in range(CC34_NOISE_SAMPLES):
            if self.stop.is_set():
                return
            try:
                raw = self.link.query("INPUT? A")
                raws.append(raw)
                values.append(parse_cryocon_number(raw, "reading", "A"))
            except CryoconStatusError as exc:
                self._emit(f"  status reply mid-sample: {exc}")
            except Exception as exc:
                self._emit(f"  read failed: {type(exc).__name__}: {exc}")
        if len(values) < 3:
            self._emit("  Not enough good readings to say anything.")
            return

        distinct = sorted(set(values))
        steps = [b - a for a, b in zip(distinct, distinct[1:]) if b > a]
        spread = max(values) - min(values)
        self._emit(f"  {len(values)} readings of INPUT? A on channel A")
        self._emit(f"  first / last  : {values[0]!r} / {values[-1]!r}")
        self._emit(f"  min / max     : {min(values)} / {max(values)}")
        self._emit(f"  peak-to-peak  : {spread:.6g}")
        self._emit(f"  std deviation : "
                   f"{statistics.pstdev(values):.6g}")
        self._emit(f"  distinct values: {len(distinct)} of {len(values)}")
        if steps:
            self._emit(f"  smallest step between distinct values: "
                       f"{min(steps):.6g}")
            self._emit("    That is the resolution actually reaching the bus;")
            self._emit("    it follows SYSTEM:DRES, not the sensor.")
        if len(distinct) == 1:
            self._emit("    Every reading identical. Either the cryostat is")
            self._emit("    very still, or the display filter (SYSTEM:DISTC)")
            self._emit("    is long enough to flatten the sample window.")
        self._emit(f"  a few raw replies: "
                   f"{', '.join(repr(r) for r in raws[:4])}")
        self._emit("")
        self._emit("  => a settle-band or drift limit tighter than the")
        self._emit(f"     peak-to-peak above ({spread:.6g}) can never be met.")

    # -- deep section: update rate --

    def _update_rate(self):
        """How often does the number behind INPUT? actually change?

        Polling faster than that returns the same value again and costs
        bus time for nothing. This is the measurement that says what the
        shortest useful dwell in a passive scan is.
        """
        self._heading("How often the reading actually changes")
        self._emit(f"  Reading INPUT? A as fast as the bus allows for "
                   f"{CC34_UPDATE_RATE_SECONDS:.0f} s.")
        samples = []
        started = time.time()
        while time.time() - started < CC34_UPDATE_RATE_SECONDS:
            if self.stop.is_set():
                return
            now = time.time()
            try:
                raw = self.link.instrument.query("INPUT? A")
                self.link._last_io = time.time()
                samples.append((now, raw.strip()))
            except Exception as exc:
                self._emit(f"  read failed: {type(exc).__name__}: {exc}")
                break
        if len(samples) < 2:
            self._emit("  Not enough samples.")
            return

        elapsed = samples[-1][0] - samples[0][0]
        rate = len(samples) / elapsed if elapsed > 0 else 0.0
        changes = [samples[i][0] for i in range(1, len(samples))
                   if samples[i][1] != samples[i - 1][1]]
        self._emit(f"  {len(samples)} queries in {elapsed:.1f} s "
                   f"= {rate:.1f} queries/s unpaced")
        self._emit(f"  the value changed {len(changes)} times")
        if len(changes) >= 2:
            intervals = [b - a for a, b in zip(changes, changes[1:])]
            mean_interval = sum(intervals) / len(intervals)
            self._emit(f"  mean interval between changes: "
                       f"{mean_interval * 1000:.0f} ms "
                       f"(min {min(intervals) * 1000:.0f}, "
                       f"max {max(intervals) * 1000:.0f})")
            self._emit("")
            self._emit(f"  => polling faster than about "
                       f"{mean_interval:.2f} s returns the same number")
            self._emit("     again. That is the shortest dwell worth using.")
        elif changes:
            self._emit("  Only one change seen - too still to measure the")
            self._emit("  update period. Try again during a ramp.")
        else:
            self._emit("  The value never changed in the sample window.")
            self._emit("  Either the cryostat is very still or the display")
            self._emit("  filter (SYSTEM:DISTC) is long. Check DISTC above.")

    # -- deep section: four-channel scan cost --

    def _channel_scan(self):
        """What does logging all four inputs actually cost per round?"""
        self._heading("Four-channel round-robin cost")
        rounds = []
        for _ in range(10):
            if self.stop.is_set():
                return
            started = time.time()
            failed = False
            for channel in CRYOCON_INPUT_CHANNELS:
                try:
                    self.link.query(f"INPUT? {channel}")
                except Exception:
                    failed = True
            rounds.append(((time.time() - started) * 1000.0, failed))
        times = [t for t, _f in rounds]
        bad = sum(1 for _t, f in rounds if f)
        self._emit(f"  10 rounds of INPUT? A/B/C/D, paced "
                   f"({CRYOCON_MIN_GAP_S * 1000:.0f} ms gap):")
        self._emit(f"    min {min(times):.0f} / mean "
                   f"{sum(times) / len(times):.0f} / max {max(times):.0f} ms "
                   "per round")
        if bad:
            self._emit(f"    {bad} round(s) had a failed read")
        self._emit("")
        self._emit(f"  => a four-channel poll costs about "
                   f"{sum(times) / len(times) / 1000:.2f} s. A one-second")
        self._emit("     poll that also reads a heater and a loop state is")
        self._emit("     tighter than it looks.")

    # -- deep section: syntax tolerance --

    def _syntax(self):
        """How fussy is the command parser?

        Worth knowing because the manual's own formal Query Syntax line
        is 'INPUT ? <channel>', with a space before the '?', while every
        worked example writes 'INPUT? B'. If both work the manual is just
        sloppy; if only one works, that is a trap for anyone building a
        command string by hand.
        """
        self._heading("Syntax tolerance")
        self._emit("  The same reading query in six spellings:")
        for variant, note in CC34_SYNTAX_VARIANTS:
            if self.stop.is_set():
                return
            started = time.time()
            try:
                reply = self.link.query(variant)
                verdict = "OK" if reply else "EMPTY"
            except Exception as exc:
                reply = f"{type(exc).__name__}"
                verdict = "REFUSED"
            self._emit(f"    {variant!r:<22} {verdict:<8} "
                       f"{(time.time() - started) * 1000:6.0f} ms  "
                       f"{reply!r}")
            self._emit(f"        -- {note}")

    # -- deep section: timeout ladder --

    def _timeout_ladder(self):
        """How low can the VISA timeout go and still be safe?

        It matters because a mnemonic this firmware does not know costs
        one whole timeout every time it is sent. PICA uses 10 s. If this
        unit answers reliably inside 200 ms, a wrong command costs 200 ms
        instead of ten seconds, and a stalled bus is noticed sooner.
        """
        self._heading("Lowest workable VISA timeout")
        original = self.link.instrument.timeout
        best = None
        try:
            for milliseconds in CC34_TIMEOUT_LADDER_MS:
                if self.stop.is_set():
                    return
                self.link.instrument.timeout = milliseconds
                ok = 0
                for _ in range(CC34_TIMEOUT_LADDER_TRIES):
                    try:
                        self.link.query("INPUT? A")
                        ok += 1
                    except Exception:
                        pass
                self._emit(f"    {milliseconds:>5} ms timeout: "
                           f"{ok}/{CC34_TIMEOUT_LADDER_TRIES} answered")
                if ok == CC34_TIMEOUT_LADDER_TRIES:
                    best = milliseconds
                else:
                    break
        finally:
            self.link.instrument.timeout = original
        self._emit("")
        if best is None:
            self._emit(f"  => nothing below {CC34_TIMEOUT_LADDER_MS[0]} ms")
            self._emit(f"     was reliable. Keep {CRYOCON_TIMEOUT_MS} ms.")
        else:
            self._emit(f"  => {best} ms was still 100% reliable over "
                       f"{CC34_TIMEOUT_LADDER_TRIES} tries.")
            self._emit("     This is a short sample on a quiet bus, so leave")
            self._emit("     a wide margin - but it shows the "
                       f"{CRYOCON_TIMEOUT_MS} ms PICA uses is")
            self._emit("     set for a worst case, not a typical one.")

    # -- deep section: soak --

    def _soak(self):
        """Does it stay up? A long run of ordinary reads, counting
        failures and recording every distinct error."""
        self._heading("Soak test")
        self._emit(f"  {CC34_SOAK_QUERIES} paced reads of INPUT? A.")
        ok, failures, statuses = 0, {}, {}
        times = []
        started_all = time.time()
        for index in range(CC34_SOAK_QUERIES):
            if self.stop.is_set():
                break
            started = time.time()
            try:
                raw = self.link.query("INPUT? A")
                ok += 1
                try:
                    parse_cryocon_number(raw, "reading", "A")
                except CryoconStatusError:
                    statuses[raw] = statuses.get(raw, 0) + 1
            except Exception as exc:
                key = f"{type(exc).__name__}: {exc}"
                failures[key] = failures.get(key, 0) + 1
            times.append((time.time() - started) * 1000.0)
            if index % 25 == 0:
                self._progress(index, CC34_SOAK_QUERIES)
        total = len(times)
        self._emit(f"  {ok}/{total} answered in "
                   f"{time.time() - started_all:.1f} s "
                   f"(mean {sum(times) / max(1, total):.0f} ms, "
                   f"max {max(times or [0]):.0f} ms)")
        for key, count in sorted(statuses.items()):
            self._emit(f"    status reply {key!r} x{count}")
        for key, count in sorted(failures.items()):
            self._emit(f"    FAILURE {key} x{count}")
        if not failures:
            self._emit("  => no comm failure over the whole run.")

    # -- error queue --

    def _error_queue(self):
        """Drain SYSTEM:ERROR? after the survey.

        The interesting question: do the commands that TIMED OUT above
        leave anything behind here, or does an unrecognised Cryo-con
        command vanish without trace? If it vanishes, a wrong mnemonic
        can only ever be caught by reading the manual - which is the
        whole reason this program exists.
        """
        self._heading("Error queue after the survey")
        for _ in range(10):
            if self.stop.is_set():
                return
            try:
                reply = self.link.query("SYSTEM:ERROR?")
            except Exception as exc:
                self._emit(f"  SYSTEM:ERROR? did not answer: "
                           f"{type(exc).__name__}: {exc}")
                return
            self._emit(f"  {reply!r}")
            if not reply or reply.strip() in ("0", "NO ERROR", "No Error",
                                              '0,"No error"'):
                break
        timed_out = sum(1 for r in self.rows if r[3] == "TIMEOUT")
        if timed_out:
            self._emit(f"  {timed_out} probe(s) timed out during this survey.")
            self._emit("  If the queue above is empty, an unrecognised")
            self._emit("  command leaves NO trace on this instrument - which")
            self._emit("  is exactly why a wrong mnemonic can live in a")
            self._emit("  measurement module for months.")

    def _not_sent(self):
        self._heading("Deliberately NOT sent")
        for command, why in CC34_NOT_SENT:
            self._emit(f"  {command}")
            self._emit(f"      {why}")

    # -- verdict --

    def _summary(self):
        by_verdict = {}
        for row in self.rows:
            by_verdict.setdefault(row[3], []).append(row)
        answered = len(by_verdict.get("OK", []))

        self._emit("")
        self._emit("=" * 78)
        self._emit("SUMMARY")
        self._emit("=" * 78)
        self._emit(f"  {answered} of {len(self.rows)} probes answered.")

        undoc = [r for r in self.rows if r[2] == "UNDOC"]
        if undoc:
            self._emit("")
            self._emit("  Mnemonics the Model 34 manual does NOT document:")
            for _group, command, _st, verdict, ms, _raw in undoc:
                taken = ("ACCEPTED by this unit" if verdict == "OK"
                         else f"REFUSED ({verdict})")
                self._emit(f"    {command:<26} {taken}  [{ms:.0f} ms]")
            self._emit("")
            self._emit("    ACCEPTED: safe on THIS controller, but still not")
            self._emit("    in the manual - prefer the documented form where")
            self._emit("    one exists, and do not assume the next unit or")
            self._emit("    the next firmware will take it.")
            self._emit("    REFUSED: must be replaced. Every send costs a")
            self._emit("    full VISA timeout and looks like a dead bus.")

        forms = [r for r in self.rows if r[2] == "FORM" and r[3] != "OK"]
        if forms:
            self._emit("")
            self._emit("  Documented short/alternate forms this firmware "
                       "did NOT take:")
            for _group, command, _st, verdict, _ms, _raw in forms:
                self._emit(f"    {command:<26} {verdict}")

        doc_failed = [r for r in self.rows if r[2] == "DOC" and r[3] != "OK"]
        if doc_failed:
            self._emit("")
            self._emit("  DOCUMENTED commands that did not answer (worth a")
            self._emit("  second look - an option, a wiring or a firmware "
                       "difference):")
            for _group, command, _st, verdict, _ms, _raw in doc_failed:
                self._emit(f"    {command:<26} {verdict}")

        self._sensor_summary()
        self._reading_chain_summary()

        slow = sorted(self.rows, key=lambda r: -r[4])[:5]
        self._emit("")
        self._emit("  Slowest probes:")
        for _group, command, _st, _verdict, ms, _raw in slow:
            self._emit(f"    {command:<26} {ms:7.0f} ms")
        self._emit("")
        self._emit(f"Finished       : {datetime.now():%Y-%m-%d %H:%M:%S}")
        self._emit("=" * 78)

    def _sensor_summary(self):
        """Where the factory block ends, where the user slots start, and
        which of them are in use - read off the instrument, not the
        manual, which contradicts itself here."""
        answered = {ix: v for ix, v in self.sensor_table.items()
                    if v is not None}
        if not answered:
            return
        self._emit("")
        self._emit("  Sensor table, as this controller reports it:")
        self._emit(f"    {len(answered)} entries, index {min(answered)} to "
                   f"{max(answered)}; nothing answers past {max(answered)}")

        # The user block is bracketed by the slots that still carry their
        # factory placeholder name. A loaded curve sits INSIDE that
        # bracket under its own name, so the bracket has to be found
        # first and the loaded slots read off inside it.
        placeholders = {ix for ix, v in answered.items()
                        if v[0].strip().lower().startswith("user sensor")}
        if placeholders:
            first, last = min(placeholders), max(placeholders)
            self._emit(f"    user curve slots are index {first}-{last}")
            self._emit(f"    => user curve n is table index n + {first - 1}")
            self._emit("       (Appendix A of the manual gives two different")
            self._emit("        answers for this and neither may be right)")
            loaded = {ix: answered[ix] for ix in range(first, last + 1)
                      if ix in answered and ix not in placeholders}
            if loaded:
                self._emit("    user slots with a curve loaded:")
                for ix in sorted(loaded):
                    name, stype, mult = loaded[ix]
                    self._emit(f"      index {ix} (user curve "
                               f"{ix - first + 1}): {name}")
                    self._emit(f"          type={stype} multiplier={mult}")
                self._emit("      A NEGATIVE multiplier means a negative")
                self._emit("      temperature coefficient (Cernox, RuOx); a")
                self._emit("      positive one a diode or Pt RTD. A curve")
                self._emit("      loaded with the wrong sign reads plausible")
                self._emit("      nonsense and nothing announces it.")
            else:
                self._emit("    every user slot still has its default name "
                           "- no calibrated curve is loaded")

        vocabulary = sorted({v[1].strip() for v in answered.values()
                             if v[1] and not v[1].startswith("<")})
        if vocabulary:
            self._emit("")
            self._emit("    Sensor TYPE strings this firmware actually uses:")
            self._emit(f"      {', '.join(vocabulary)}")
            self._emit("      These, not the manual's list, are the spellings")
            self._emit("      a CALCUR header must use. A type field the")
            self._emit("      firmware cannot identify makes it discard the")
            self._emit("      whole curve block without saying so.")

        in_use = {}
        for _group, command, _st, verdict, _ms, raw in self.rows:
            match = re.match(r"INPUT ([A-D]):SENIX\?$", command)
            if match and verdict == "OK":
                in_use[match.group(1)] = raw.strip()
        if in_use:
            self._emit("")
            self._emit("    Curve in use on each input:")
            for channel in sorted(in_use):
                index_text = in_use[channel]
                try:
                    entry = answered.get(int(float(index_text)))
                except (TypeError, ValueError):
                    entry = None
                label = entry[0] if entry else "<index not in the table>"
                if index_text.strip() in ("0", "0.0"):
                    label = "None - this input is switched OFF"
                self._emit(f"      input {channel}: SENIX {index_text} "
                           f"-> {label}")

    def _reading_chain_summary(self):
        """Settings that quietly shape every number this controller
        reports, and therefore every number PICA logs."""
        values = {}
        for _group, command, _st, verdict, _ms, raw in self.rows:
            if verdict == "OK":
                values[command] = raw.strip()
        distc = values.get("SYSTEM:DISTC?")
        dres = values.get("SYSTEM:DRES?")
        units = {ch: values.get(f"INPUT {ch}:UNITS?")
                 for ch in CRYOCON_INPUT_CHANNELS}
        non_kelvin = {ch: u for ch, u in units.items()
                      if u and not u.upper().startswith("K")}

        if not (distc or dres or non_kelvin):
            return
        self._emit("")
        self._emit("  What shapes the numbers this controller reports:")
        if distc:
            self._emit(f"    SYSTEM:DISTC = {distc} s display filter.")
            self._emit("      The manual is explicit that INPUT? is filtered")
            self._emit("      by this, so it is applied to EVERY temperature")
            self._emit("      PICA logs, not just the front panel.")
            try:
                seconds = float(re.sub(r"[^0-9.]", "", distc) or 0)
            except ValueError:
                seconds = 0.0
            if seconds >= 8:
                self._emit("      At this setting a temperature ramp is")
                self._emit("      visibly smeared and a step is delayed.")
                self._emit("      Worth reducing before ramp measurements.")
        if dres:
            self._emit(f"    SYSTEM:DRES = {dres} display resolution.")
            self._emit("      This sets how many dashes a sensor fault comes")
            self._emit("      back as, which is why the fault strings are")
            self._emit("      matched by shape ('-{2,}') and not by a fixed")
            self._emit("      seven characters.")
        if non_kelvin:
            self._emit("    CHANNELS NOT REPORTING KELVIN:")
            for channel, unit in sorted(non_kelvin.items()):
                self._emit(f"      input {channel} is in '{unit}'")
            self._emit("      INPUT? reports in each channel's OWN display")
            self._emit("      units. A channel left in C or F logs wrong")
            self._emit("      numbers against every point of a run, which is")
            self._emit("      why every PICA module checks units at Start.")


# ===============================================================================
# THE CONSOLE
# ===============================================================================

class DiagnosticsGUI:
    """A console: connect, pick what to measure, run, read, save.

    Deliberately plain. This is a tool you open when something does not
    add up, so what matters is that the log is complete and easy to hand
    to somebody else, not that the window is pretty.
    """

    PROGRAM_NAME = "Cryocon 34 Diagnostics"
    PROGRAM_VERSION = "1.0"
    POLL_MS = 60

    # House palette, identical to the other PICA modules (see
    # T_Control_CC34_DirectControl_GUI). A diagnostics console has no reason
    # to look like a different program from the one that drives the run.
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#F4EFEA'
    CLR_GRAPH_BG = '#F4EFEA'
    CLR_STATUS_OK = '#6B8E4E'
    CLR_STATUS_BAD = '#BA6B5E'

    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION} "
                        "(read-only)")
        self.root.geometry("1040x760")
        self.root.minsize(820, 560)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.link = None
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.after_id = None
        self.saved_timeout = None
        self.lines = []
        self.deep_vars = {}

        self.setup_styles()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._log_line(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self._log_line("")
        self._log_line("Read-only. This program has no write path: no *RST,")
        self._log_line("no CONTROL, no STOP, no setpoint, loop or heater")
        self._log_line("command. Safe to run against a live experiment.")
        self._log_line("")
        self._log_line("Scan, pick the Cryocon, Connect, then Run Survey.")
        if not PYVISA_AVAILABLE:
            self._log_line("")
            self._log_line("PyVISA is not installed - nothing can be reached.")
            self._log_line("  pip install pyvisa pyvisa-py")

    # -- styles --

    def setup_styles(self):
        """The shared PICA look: clam, warm panels on the darker backing."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        # The description and the status word sit directly on the backing,
        # not inside a panel, so they need its colour rather than a panel's.
        style.configure('Backing.TLabel', background=self.CLR_BG_DARK)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton',
            background=[('active', self.CLR_ACCENT_GOLD),
                        ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[('active', self.CLR_TEXT_DARK),
                        ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Connect.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Connect.TButton',
            background=[('active', '#8AB845'),
                        ('hover', '#8AB845')])
        style.configure(
            'Disconnect.TButton',
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Disconnect.TButton',
            background=[('active', '#D63C2A'),
                        ('hover', '#D63C2A')])
        style.configure(
            'TLabelframe',
            background=self.CLR_FRAME_BG,
            bordercolor=self.CLR_ACCENT_GOLD)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        style.configure(
            'TCheckbutton',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'TCheckbutton',
            background=[('active', self.CLR_FRAME_BG)])
        style.configure(
            'TEntry',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            insertcolor=self.CLR_TEXT_DARK)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK)
        style.configure(
            'TProgressbar',
            background=self.CLR_ACCENT_GREEN,
            troughcolor=self.CLR_INPUT_BG,
            bordercolor=self.CLR_ACCENT_GOLD)

    # -- layout --

    def _build(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(
            header,
            text="Cryocon Model 34 Diagnostics",
            style='Header.TLabel',
            font=('Segoe UI', self.FONT_BASE[1] + 4, 'bold'),
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)
        ttk.Label(
            header, text="read-only", style='Header.TLabel',
            foreground=self.CLR_STATUS_OK).pack(side='right', padx=20)

        head = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        head.pack(fill='x')
        ttk.Label(
            head,
            style='Backing.TLabel',
            text=("Asks the instrument what it actually does. Every probe "
                  "is a query - nothing is written, so this is safe to run "
                  "while the controller is driving an experiment."),
            wraplength=980, justify='left').pack(anchor='w')

        conn = ttk.LabelFrame(self.root, text="Connection",
                              padding=(10, 6))
        conn.pack(fill='x', padx=12, pady=(6, 4))
        ttk.Label(conn, text="VISA address:").pack(side='left')
        self.address_var = tk.StringVar(value=f"GPIB0{CRYOCON_ADDRESS_HINT}")
        self.address_box = ttk.Combobox(conn, textvariable=self.address_var,
                                        width=32)
        self.address_box.pack(side='left', padx=(6, 6))
        ttk.Button(conn, text="Scan", command=self._scan).pack(side='left')
        self.connect_btn = ttk.Button(conn, text="Connect",
                                      style='Connect.TButton',
                                      command=self._connect)
        self.connect_btn.pack(side='left', padx=(6, 0))
        self.disconnect_btn = ttk.Button(conn, text="Disconnect",
                                         style='Disconnect.TButton',
                                         command=self._disconnect,
                                         state='disabled')
        self.disconnect_btn.pack(side='left', padx=(6, 0))
        self.idn_var = tk.StringVar(value="not connected")
        ttk.Label(conn, textvariable=self.idn_var).pack(side='left',
                                                        padx=(12, 0))

        deep = ttk.LabelFrame(
            self.root,
            text="Deep measurements (the core survey always runs)",
            padding=(10, 6))
        deep.pack(fill='x', padx=12, pady=4)
        for column, (key, label, cost, default) in enumerate(
                CC34_DEEP_SECTIONS):
            var = tk.BooleanVar(value=default)
            self.deep_vars[key] = var
            ttk.Checkbutton(deep, text=f"{label}  ({cost})",
                            variable=var).grid(
                row=column // 2, column=column % 2, sticky='w',
                padx=(0, 20), pady=1)

        bar = ttk.Frame(self.root, padding=(12, 4))
        bar.pack(fill='x')
        self.run_btn = ttk.Button(bar, text="Run Survey", command=self._start,
                                  state='disabled')
        self.run_btn.pack(side='left')
        self.stop_btn = ttk.Button(bar, text="Stop", command=self._stop,
                                   state='disabled')
        self.stop_btn.pack(side='left', padx=(6, 0))
        self.save_btn = ttk.Button(bar, text="Save Log As...",
                                   command=self._save_as, state='disabled')
        self.save_btn.pack(side='left', padx=(6, 0))
        self.copy_btn = ttk.Button(bar, text="Copy All", command=self._copy,
                                   state='disabled')
        self.copy_btn.pack(side='left', padx=(6, 0))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status_var,
                  style='Backing.TLabel').pack(side='right')

        prog = ttk.Frame(self.root, padding=(12, 0))
        prog.pack(fill='x')
        self.progress = ttk.Progressbar(prog, mode='determinate')
        self.progress.pack(fill='x')

        self.console = scrolledtext.ScrolledText(
            self.root, wrap='none', font=self.FONT_CONSOLE,
            bg=self.CLR_CONSOLE_BG, fg=self.CLR_TEXT_DARK,
            insertbackground=self.CLR_TEXT_DARK,
            relief='flat', highlightthickness=1,
            highlightbackground=self.CLR_ACCENT_GOLD)
        self.console.pack(fill='both', expand=True, padx=12, pady=(6, 12))
        self.console.config(state='disabled')

    # -- console --

    def _log_line(self, text):
        self.lines.append(text)
        self.console.config(state='normal')
        self.console.insert('end', text + "\n")
        self.console.see('end')
        self.console.config(state='disabled')

    # -- connection --

    def _scan(self):
        """List VISA resources and pre-select anything that says Cryo-con.

        Identification is by *IDN? content: the factory address is shared
        with a Lakeshore 340/350 and a K6221, so picking by address alone
        is how you end up logging the wrong instrument's temperature.
        """
        if not PYVISA_AVAILABLE:
            messagebox.showerror("PyVISA Missing",
                                 "Install pyvisa and a VISA backend.")
            return
        self._log_line("")
        self._log_line("Scanning the bus...")
        try:
            rm = pyvisa.ResourceManager()
            resources = list(rm.list_resources())
        except Exception as exc:
            self._log_line(f"  VISA scan failed: {exc}")
            return
        self.address_box['values'] = resources
        found = None
        for resource in resources:
            if resource.upper().startswith("ASRL"):
                self._log_line(f"  {resource}: skipped (serial)")
                continue
            try:
                instrument = rm.open_resource(resource)
                instrument.timeout = 2000
                idn = instrument.query('*IDN?').strip()
                instrument.close()
            except Exception as exc:
                self._log_line(f"  {resource}: no answer "
                               f"({type(exc).__name__})")
                continue
            self._log_line(f"  {resource}: {idn}")
            if found is None and is_cryocon_idn(idn):
                found = resource
        if found:
            self.address_var.set(found)
            self._log_line(f"Cryo-con found at {found}.")
        else:
            self._log_line("No Cryo-con identified. Check the address and "
                           "that its SYS menu has RIO-Port set to GPIB.")

    def _connect(self):
        address = self.address_var.get().strip()
        if not address:
            messagebox.showerror("No Address", "Enter or scan for an address.")
            return
        self._log_line("")
        self._log_line(f"Connecting to {address}...")
        try:
            link = CryoconLink(address, log=self._log_line)
        except Exception as exc:
            self._log_line(f"  {exc}")
            messagebox.showerror("Connection Failed", str(exc))
            return
        if not is_cryocon_idn(link.idn):
            link.close()
            message = (f"{address} is not a Cryo-con: it identifies itself "
                       f"as '{link.idn}'. Its replies would be recorded as "
                       "Cryo-con behaviour, so this refuses to continue.")
            self._log_line(f"  {message}")
            messagebox.showerror("Wrong Instrument", message)
            return
        self.link = link
        self.idn_var.set(link.idn)
        self._log_line(f"  Connected: {link.idn}")
        self.connect_btn.config(state='disabled')
        self.disconnect_btn.config(state='normal')
        self.run_btn.config(state='normal')

    def _disconnect(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Survey Running",
                                "Stop the survey before disconnecting.")
            return
        if self.link:
            self.link.close()
            self.link = None
        self.idn_var.set("not connected")
        self._log_line("Disconnected. The instrument was not written to.")
        self.connect_btn.config(state='normal')
        self.disconnect_btn.config(state='disabled')
        self.run_btn.config(state='disabled')

    # -- running --

    def _start(self):
        if self.worker is not None and self.worker.is_alive():
            return
        if self.link is None or not self.link.is_connected:
            messagebox.showerror("Not Connected", "Connect first.")
            return
        deep = {key for key, var in self.deep_vars.items() if var.get()}

        # A probe that is not answered costs one whole timeout.
        try:
            self.saved_timeout = self.link.instrument.timeout
            self.link.instrument.timeout = CC34_SURVEY_TIMEOUT_MS
        except Exception:
            self.saved_timeout = None

        self.stop_event.clear()
        self.run_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.save_btn.config(state='disabled')
        self.copy_btn.config(state='disabled')
        self.disconnect_btn.config(state='disabled')
        self.progress.config(value=0, maximum=100)
        self.status_var.set("Running...")
        self._log_line("")

        survey = CryoconSurvey(self.link, self.queue, self.stop_event, deep)
        self.worker = threading.Thread(target=survey.run, daemon=True)
        self.worker.start()
        self._pump()

    def _stop(self):
        self.stop_event.set()
        self.status_var.set("Stopping...")

    def _pump(self):
        """Drain the worker's queue on the Tk thread."""
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "line":
                    self._log_line(item[1])
                elif kind == "progress":
                    done, total = item[1], item[2]
                    self.progress.config(value=done, maximum=max(1, total))
                    self.status_var.set(f"Probe {done} of {total}")
                elif kind == "done":
                    self._finish()
                    return
        except queue.Empty:
            pass
        self.after_id = self.root.after(self.POLL_MS, self._pump)

    def _finish(self):
        self.after_id = None
        self._restore_timeout()
        self.run_btn.config(state='normal')
        self.stop_btn.config(state='disabled')
        self.save_btn.config(state='normal')
        self.copy_btn.config(state='normal')
        self.disconnect_btn.config(state='normal')
        self.status_var.set("Finished.")
        self.progress.config(value=self.progress["maximum"])
        path = self._autosave()
        if path:
            self._log_line("")
            self._log_line(f"Log saved to: {path}")

    def _restore_timeout(self):
        if self.saved_timeout is None:
            return
        try:
            self.link.instrument.timeout = self.saved_timeout
        except Exception:
            pass
        self.saved_timeout = None

    # -- the log --

    def _text(self):
        return "\n".join(self.lines) + "\n"

    def _autosave(self):
        """Write the log out without being asked.

        The whole value of this program is the file that leaves the lab
        with you, and a save dialog at the end of a long session is one
        more thing to forget.
        """
        name = f"CC34_diagnostics_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path = os.path.join(os.path.expanduser("~"), name)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._text())
                handle.flush()
                os.fsync(handle.fileno())
            return path
        except Exception as exc:
            self._log_line(f"Could not auto-save the log: {exc}")
            return None

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
            initialfile=f"CC34_diagnostics_"
                        f"{datetime.now():%Y%m%d_%H%M%S}.txt")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._text())
            self._log_line(f"Log saved to {path}")
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc), parent=self.root)

    def _copy(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._text())
            self.status_var.set("Copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy Failed", str(exc), parent=self.root)

    # -- teardown --

    def _on_closing(self):
        """No confirmation dialog: this program writes nothing, so there
        is nothing to lose but the on-screen copy of a log that has
        already been saved to disk."""
        self.stop_event.set()
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        self._restore_timeout()
        if self.link:
            try:
                self.link.close()
            except Exception:
                pass
            self.link = None
        self.root.destroy()


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    root = tk.Tk()
    if not PYVISA_AVAILABLE:
        messagebox.showwarning(
            "PyVISA Missing",
            "PyVISA is not installed, so no instrument can be reached.\n\n"
            "  pip install pyvisa pyvisa-py\n\n"
            "The window will open so you can read what this tool does.")
    app = DiagnosticsGUI(root)
    root.mainloop()
