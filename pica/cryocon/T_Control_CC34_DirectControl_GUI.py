"""
Module: T_Control_CC34_DirectControl_GUI.py
Purpose: Direct command workbench GUI for the Cryocon (Cryogenic Control
         Systems) Model 34 Cryogenic Temperature Controller.
         Sends individual SCPI commands for control-loop PID gains,
         setpoint, ramp rate, control type, heater range, heater load,
         maximum power/setpoint, manual power, input configuration
         (units, sensor index, name), alarms, over-temperature disconnect,
         keypad lockout, display settings and control engage/disengage.

         Cryocon equivalent of T_Control_L350_DirectControl_GUI.py.

         Non-destructive disconnect: the instrument retains all settings
         after the programme closes. Disconnect never sends STOP, never
         changes a loop, and never sends *RST.

SCPI commands verified against:
  - Cryo-con User's Guide, Model 32 / 32B (Remote Operation chapter).
    The Cryo-con SCPI command set is common across the product family
    (Models 24C / 32 / 32B / 34 / 62); the Model 34 adds a second control
    loop, four input channels and relays.
    https://pf18b.neocities.org/docu/Cryocon_32_Temperature_Controller.pdf

Instrument facts used here (from that manual):
  - Input channels are addressed as A, B, C, D (or CHA, CHB, ...)
  - INPUT? <ch> reports temperature in that channel's OWN display units
  - Loop 1 is the primary heater (50 W into 50 ohm / 25 W into 25 ohm),
    with ranges Hi / Mid / Low
  - LOOP:PGAIN 0-1000 (unitless), LOOP:IGAIN 0-1000 SECONDS,
    LOOP:DGAIN 0-1000 inverse seconds
  - LOOP:SETPT allowed 0 K to 1000 K, LOOP:RATE 0-100 units/min
  - CONTROL engages the loops, STOP disengages them and drops the heaters
  - Over Temperature Disconnect (OVERTEMP:*) is the safety cut-out
  - GPIB: factory address 12, EOI framing, no EOS terminator
  - *RST is a HARDWARE reset: the instrument is unreachable for ~15 s

v1.1, 28 Aug 2026. Changes after a failed session on a Model 34
Rev 3.03A at GPIB1::12:
  - the first '*IDN?' of a session could die inside viWrite with
    VI_ERROR_TMO even though the bus scan had just identified the
    instrument. connect() now settles after opening the session and retries
    the first query, which is what connecting a second time by hand did;
  - every VISA operation is paced, so commands are never sent back to back
    into a slow firmware revision;
  - the 1 Hz status poll used to fire more than thirty queries in one burst
    on the Tk main thread. It is now staged: each tick asks one small group
    and the panel cycles through the groups, which keeps the window
    responsive and stops the instrument being flooded;
  - readings that come back as Cryo-con status strings ('-------' for a
    sensor fault, '.......' for a reading off the calibration curve) are
    reported as those conditions instead of raising a bare number error.

NOTE on the integral term: on a Cryocon, I is a time in SECONDS and a
LARGER value means SLOWER integral action. This is the opposite sense to
the Lake Shore 350, where a larger I is faster. Lakeshore PID numbers must
not be copied across.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, Canvas
import os
import re
import time
import traceback
from datetime import datetime

# --- Optional Packages ---
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

import runpy
from multiprocessing import Process


# ---------------------------------------------------------------------------
# UTILITY LAUNCHERS (identical to reference programme)
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
# on its own. The logo and the sibling utility scripts used to be reached by
# a fixed number of "..", so a file that moved lost its logo and its Scan
# button, and in one place a missing logo crashed the window before the
# console existed to report it.
#
# find_pica_root() looks for the package in three ways and gives up quietly.
# Everything downstream treats a missing resource as a disabled feature, never
# as an error: no instrument code depends on any of it.

def find_pica_root():
    """Absolute path of the pica package directory, or None.

    Tried in order: the installed package, then each directory above this
    file, then a 'pica' directory inside each of those. A directory counts as
    the package root when it holds both 'assets' and 'utils'.
    """
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
    """Run a pica/utils script in its own process.

    Says plainly what is missing rather than reporting a path that was only
    ever a guess. Returns True if the process was started.
    """
    path = pica_utility(script_name)
    if not path:
        messagebox.showerror(
            f"{friendly_name} Not Available",
            f"{friendly_name} could not be found.\n\n"
            "This module is running outside the pica package, so the shared "
            "utilities in pica/utils are not reachable. Everything else in "
            "this window works normally; copy the file into pica/<folder>/ "
            "to get the utility buttons back.")
        return False
    try:
        Process(target=run_script_process, args=(path,)).start()
        return True
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch {friendly_name}: {e}")
        return False


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    launch_pica_utility("PlotterUtil_GUI.py", "Plotter Utility")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    launch_pica_utility("GPIB_Instrument_Scanner_GUI.py", "GPIB Scanner")


def pica_sibling(*parts):
    """Absolute path to a module inside the pica package, or ''.

    pica_utility() only reaches pica/utils. The curve loader is a Cryo-con
    module and lives beside this one, so it needs its own resolver rather
    than a hard-wired '..' that breaks the moment a file is moved.
    """
    if not PICA_ROOT:
        return ""
    path = os.path.join(PICA_ROOT, *parts)
    return path if os.path.exists(path) else ""


def launch_curve_loader():
    """Open the sensor curve loader in its own process.

    A Model 34 has no Cernox curve of its own, so a calibrated Cernox has to
    be installed as one of its twelve user curves before the Sensor Index
    field above can point at it. That is what the loader does; this window
    cannot, because sending a curve is a 130-line CALCUR transfer rather than
    a single command.

    Launched as a separate process, like every other utility here, so a
    curve transfer never shares a VISA session with the control panel.
    """
    path = pica_sibling("cryocon", "Sensor_Curve_Loader_CC34_GUI.py")
    if not path:
        messagebox.showerror(
            "Sensor Curve Loader Not Available",
            "Sensor_Curve_Loader_CC34_GUI.py could not be found.\n\n"
            "This module is running outside the pica package, so its sibling "
            "modules are not reachable. Everything else in this window works "
            "normally; copy the loader into pica/cryocon/ to get this button "
            "back.")
        return False
    try:
        Process(target=run_script_process, args=(path,)).start()
        return True
    except Exception as e:
        messagebox.showerror("Launch Error",
                             f"Failed to launch the Sensor Curve Loader: {e}")
        return False


# ---------------------------------------------------------------------------
# BACKEND: Cryocon Model 34 Instrument Control
# ---------------------------------------------------------------------------

# A Cryo-con replies to *IDN? with something like
# "Cryocon,Model 34,204683,3.18A". Both spellings of the maker name are in
# circulation, so both are accepted.
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON")

# VISA resource kinds worth probing during a scan. Serial ports are left
# alone: on a Windows rack ASRL1 is as likely to be a UPS or a Bluetooth port
# as an instrument, and a *IDN? at one of those blocks for the whole timeout.
PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")

# Factory address, used only as a last-resort hint. Identification is by
# *IDN? content, so a re-addressed Cryocon is still found.
# The lab's Cryocon 34 was moved to IEEE address 23 on 3 Sep 2026: 12 is the
# shared factory default of the Cryocon, the Lakeshore 340/350 and the 6221.
# Board-independent hint ("::23::INSTR" matches GPIB0 or GPIB1); *IDN? decides.
CRYOCON_ADDRESS_HINT = "::23::INSTR"

# Short timeout for the identification pass so one silent address cannot
# stall the whole scan.
IDN_SCAN_TIMEOUT_MS = 1200


# ===============================================================================
# CRYOCON LINK HARDENING  (read-only; inlined so each module stays standalone)
# ===============================================================================
#
# Two failures seen on a Cryo-con Model 34 Rev 3.03A at GPIB1::12, 28 Aug 2026:
#
#   1. The bus scan identified the instrument, and the very next session's
#      '*IDN?' died inside viWrite with VI_ERROR_TMO. Pressing Start again
#      connected normally. A timeout on the WRITE means the instrument stopped
#      accepting bytes for a moment, not that it is absent or at another
#      address, so the cure is to wait and ask again instead of giving up.
#      Handled by CRYOCON_OPEN_SETTLE_S plus the retry loop in CryoconLink.
#
#   2. A reading query answered with a Cryo-con status string instead of a
#      number and float() raised, which killed the worker thread. The front
#      panel shows seven dashes for a sensor fault and seven dots for a
#      reading that is inside the instrument's range but off the sensor's
#      calibration curve; over the bus those arrive as the literal strings
#      below. Handled by parse_cryocon_number(), which names the condition
#      instead of raising a bare ValueError.
#
# Nothing in this block writes to the instrument. GPIB device clear is an
# interface message rather than a SCPI command, but its effect is
# device-dependent and the Cryo-con guide does not document it, so it stays
# off unless ALLOW_DEVICE_CLEAR_ON_RETRY is turned on deliberately.

CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before the first command
CRYOCON_MIN_GAP_S = 0.08            # minimum gap between consecutive operations
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries
ALLOW_DEVICE_CLEAR_ON_RETRY = False # see note above

# Literal replies that are status, not data.
CRYOCON_STATUS_STRINGS = {
    '-------': "sensor fault: the sensor is open, disconnected or shorted",
    '.......': ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    'N/A': "the channel is disabled, or the value does not apply",
    'NACK': "the instrument did not acknowledge the command",
}

# Leading signed decimal, with or without an exponent. Used to peel a trailing
# unit character off replies such as '77.350K'.
_CRYOCON_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')

# The dash and dot runs are as long as the display resolution setting makes
# them, so they are matched by shape rather than by a fixed seven characters.
_CRYOCON_FAULT_RE = re.compile(r'^-{2,}$')
_CRYOCON_RANGE_RE = re.compile(r'^\.{2,}$')


class CryoconStatusError(ValueError):
    """A query returned a Cryo-con status string where a number was expected."""


def parse_cryocon_number(raw, what, channel=None):
    """Turn a Cryo-con reply into a float, or say precisely why it is not one.

    Handles three things the plain float() call did not: status strings, a
    trailing unit character, and multi-channel replies, which come back as
    fields separated by semicolons.
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
        pass
    match = _CRYOCON_NUMBER_RE.match(text)
    if match:
        return float(match.group(0))
    raise CryoconStatusError(
        f"Cryocon {what}{where} returned '{text}' "
        "(sensor fault, no sensor, or reading out of range).")


def is_cryocon_idn(idn):
    """True if a '*IDN?' reply came from a Cryo-con temperature instrument."""
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


class CryoconLink:
    """One paced VISA session to a Cryo-con, opened with retries.

    Every method is a query unless the caller explicitly asks for write().
    The monitor and the dielectric scan never call write().
    """

    def __init__(self, visa_address, timeout_ms=CRYOCON_TIMEOUT_MS,
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
        for attempt in range(1, CRYOCON_CONNECT_ATTEMPTS + 1):
            try:
                self.instrument = self.rm.open_resource(self.address)
                self.instrument.timeout = self.timeout_ms
                # The Cryocon GPIB port frames lines with EOI and no EOS
                # character, so the PyVISA termination defaults are left alone.
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

    def _pace(self):
        """Hold a minimum gap between operations. Rev 3.03A firmware is slow
        and back-to-back traffic is what provoked the write timeout."""
        gap = CRYOCON_MIN_GAP_S - (time.time() - self._last_io)
        if gap > 0:
            time.sleep(gap)

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
        """Only the direct-control module calls this."""
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        self._pace()
        try:
            self.instrument.write(command)
        finally:
            self._last_io = time.time()

    def reconnect(self):
        """Drop the session and open a fresh one. Sends no SCPI beyond
        '*IDN?'; used after a comm failure mid-run."""
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


class Cryocon34Backend:
    """
    Backend for Cryocon Model 34 communication.

    All SCPI commands are verified against the Cryo-con User's Guide
    (Remote Operation chapter); the command set is shared across the
    Cryo-con family.

    Disconnect is non-destructive: it only closes the VISA resource.
    No *RST, no STOP, no loop/heater/setpoint changes on disconnect.
    The instrument continues operating autonomously with its current
    settings.
    """

    # --- Input channels on a Model 34 ---
    INPUT_CHANNELS = ['A', 'B', 'C', 'D']

    # --- Control loops (Loop 1 is the primary heater) ---
    LOOPS = ['1', '2']

    # --- Display units for INPUT <ch>:UNITS ---
    DISPLAY_UNITS = {
        'K': "Kelvin",
        'C': "Celsius",
        'F': "Fahrenheit",
        'S': "Sensor units (V or Ohms)",
    }

    # --- Control types for LOOP <n>:TYPE ---
    CONTROL_TYPES = {
        'Off': "Loop disabled",
        'PID': "Closed loop PID",
        'Man': "Manual power (uses PMANUAL)",
        'Table': "PID table lookup",
        'RampP': "PID with setpoint ramping (uses RATE)",
    }

    # --- Loop 1 heater ranges (LOOP 1:RANGE) ---
    HEATER_RANGES = {
        'Hi': "Hi  - 50 W into 50 ohm / 25 W into 25 ohm",
        'Mid': "Mid - 5 W into 50 ohm / 2.5 W into 25 ohm",
        'Low': "Low - 0.5 W into 50 ohm / 0.25 W into 25 ohm",
    }

    # --- Display filter time constants (SYSTEM:DISTC), seconds ---
    DISPLAY_TIME_CONSTANTS = ['0.5', '1', '2', '4', '8', '16', '32', '64']

    def __init__(self, log=None):
        self.link = None
        self.rm = None
        self.log = log if callable(log) else (lambda msg: print(msg))
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            print("PyVISA not available.")

    @property
    def cryocon(self):
        """The live VISA resource, or None. Kept for older callers."""
        return self.link.instrument if self.link else None

    # -- Connection management --

    def connect(self, visa_address):
        """
        Open VISA connection and query *IDN?.
        Returns the identification string.
        """
        if not self.rm:
            raise ConnectionError(
                "PyVISA ResourceManager not available. "
                "Install pyvisa and a VISA backend (NI-VISA or pyvisa-py).")
        # CryoconLink opens the session, waits for it to settle and retries
        # the first '*IDN?'. That first command is the one that timed out
        # inside viWrite when a bus scan had just finished.
        self.link = CryoconLink(visa_address, log=self.log)
        idn = self.link.idn
        # This module drives heaters, setpoints and control loops. GPIB
        # addresses get changed, so confirm what actually answered before
        # sending it a LOOP or CONTROL command; refuse anything that is not a
        # Cryo-con rather than write control commands into a stranger.
        if not is_cryocon_idn(idn):
            self.disconnect()
            raise ConnectionError(
                f"{visa_address} is not a Cryo-con: it identifies itself as "
                f"'{idn}'. Refusing to send control commands. Scan the bus "
                f"and pick the Cryocon's actual address (it does not have to "
                f"be {CRYOCON_ADDRESS_HINT}).")
        return idn

    def identify_resources(self, resources):
        """Return {resource: idn} for every resource that answers *IDN?.

        Never raises: an address that is busy, silent or not SCPI simply does
        not appear in the result. Serial resources are not probed at all.
        """
        found = {}
        if not self.rm:
            return found
        for res in resources:
            if not str(res).upper().startswith(PROBE_RESOURCE_PREFIXES):
                continue
            inst = None
            try:
                inst = self.rm.open_resource(res)
                inst.timeout = IDN_SCAN_TIMEOUT_MS
                idn = inst.query('*IDN?').strip()
                if idn:
                    found[res] = idn
            except Exception:
                pass
            finally:
                if inst is not None:
                    try:
                        inst.close()
                    except Exception:
                        pass
        return found

    def disconnect(self):
        """
        Non-destructive disconnect.
        Only closes the VISA resource handle. Does NOT:
          - send *RST
          - send STOP or disengage the control loops
          - change PID, range, setpoint, or any setting
        The instrument retains all current settings and continues
        operating autonomously.
        """
        if self.link:
            try:
                self.link.close()
            except Exception as e:
                print(f"  Warning during disconnect: {e}")
            finally:
                self.link = None

    @property
    def is_connected(self):
        return self.link is not None and self.link.is_connected

    # -- Low-level command helpers --

    def _write(self, command):
        """Send a SCPI write command. Raises if not connected.

        Paced: consecutive operations are held apart by
        CRYOCON_MIN_GAP_S, because back-to-back traffic is what left this
        firmware revision refusing to accept the next byte.
        """
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        self.link.write(command)

    def _query(self, command):
        """Send a SCPI query and return stripped response."""
        if not self.link:
            raise ConnectionError("Not connected to instrument.")
        return self.link.query(command)

    @staticmethod
    def _to_float(raw, what):
        """Parse a numeric response, with a readable error on junk.

        Percent signs are stripped first; the rest is handed to
        parse_cryocon_number, which knows the instrument's status strings
        and tolerates a trailing unit character.
        """
        return parse_cryocon_number(str(raw).strip().rstrip('%'), what)

    @staticmethod
    def _check_loop(loop):
        if str(loop) not in Cryocon34Backend.LOOPS:
            raise ValueError(f"Loop must be 1 or 2, got {loop}")
        return str(loop)

    # -- PID gains (Manual: LOOP:PGAIN / IGAIN / DGAIN) --
    # P: 0 to 1000, unitless
    # I: 0 to 1000 SECONDS   (larger = slower integral action)
    # D: 0 to 1000 inverse seconds (usually 0 for cryogenic work)

    def set_pid(self, loop, p, i, d):
        """Set the three gain terms for the given loop (1 or 2)."""
        loop = self._check_loop(loop)
        if not (0 <= p <= 1000):
            raise ValueError(f"P must be 0-1000, got {p}")
        if not (0 <= i <= 1000):
            raise ValueError(f"I must be 0-1000 seconds, got {i}")
        if not (0 <= d <= 1000):
            raise ValueError(f"D must be 0-1000 /second, got {d}")
        cmd = f"LOOP {loop}:PGAIN {p};IGAIN {i};DGAIN {d}"
        self._write(cmd)
        return cmd

    def get_pid(self, loop):
        """Query the gain terms. Returns (P, I, D) as floats."""
        loop = self._check_loop(loop)
        p = self._to_float(self._query(f"LOOP {loop}:PGAIN?"), "PGAIN?")
        i = self._to_float(self._query(f"LOOP {loop}:IGAIN?"), "IGAIN?")
        d = self._to_float(self._query(f"LOOP {loop}:DGAIN?"), "DGAIN?")
        return p, i, d

    # -- Setpoint (Manual: LOOP:SETPT, LOOP:RATE, LOOP:TYPE) --
    # SETPT allowed values are 0 K to 1000 K, in the display units of the
    # loop's source channel. RATE is in units/minute, 0 to 100.

    def set_setpoint_immediate(self, loop, value):
        """
        Set the setpoint with no ramping.
        Sends LOOP <n>:TYPE PID (leaving ramp mode if it was active) then
        LOOP <n>:SETPT <value>.
        """
        loop = self._check_loop(loop)
        if not (0 <= value <= 1000):
            raise ValueError(f"Setpoint must be 0-1000 K, got {value}")
        cmd = f"LOOP {loop}:TYPE PID;SETPT {value}"
        self._write(cmd)
        return cmd

    def set_setpoint_with_ramp(self, loop, value, rate_per_min):
        """
        Set the setpoint with a controlled ramp.
        Sends LOOP <n>:RATE <rate>, LOOP <n>:TYPE RampP, then SETPT.
        Rate is in display units per minute, 0 to 100.
        """
        loop = self._check_loop(loop)
        if not (0 <= value <= 1000):
            raise ValueError(f"Setpoint must be 0-1000 K, got {value}")
        if not (0 < rate_per_min <= 100):
            raise ValueError(
                f"Ramp rate must be >0 and <=100 K/min, got {rate_per_min}")
        cmd = f"LOOP {loop}:RATE {rate_per_min};TYPE RampP;SETPT {value}"
        self._write(cmd)
        return cmd

    def get_setpoint(self, loop):
        """Query the current setpoint for the given loop."""
        loop = self._check_loop(loop)
        return self._to_float(self._query(f"LOOP {loop}:SETPT?"), "SETPT?")

    def set_rate(self, loop, rate_per_min):
        """Set the ramp rate (units/minute) without touching the setpoint."""
        loop = self._check_loop(loop)
        if not (0 <= rate_per_min <= 100):
            raise ValueError(
                f"Ramp rate must be 0-100 K/min, got {rate_per_min}")
        cmd = f"LOOP {loop}:RATE {rate_per_min}"
        self._write(cmd)
        return cmd

    def get_rate(self, loop):
        """Query the ramp rate in units/minute."""
        loop = self._check_loop(loop)
        return self._to_float(self._query(f"LOOP {loop}:RATE?"), "RATE?")

    def get_ramp_status(self, loop):
        """Query whether a ramp is in progress. Returns 'ON' or 'OFF'."""
        loop = self._check_loop(loop)
        return self._query(f"LOOP {loop}:RAMP?")

    # -- Control type and source channel (Manual: LOOP:TYPE / LOOP:SOURCE) --

    def set_loop_type(self, loop, control_type):
        """Set the loop control type (Off / PID / Man / Table / RampP)."""
        loop = self._check_loop(loop)
        if control_type not in self.CONTROL_TYPES:
            raise ValueError(
                f"Control type must be one of "
                f"{list(self.CONTROL_TYPES)}, got {control_type}")
        cmd = f"LOOP {loop}:TYPE {control_type}"
        self._write(cmd)
        return cmd

    def get_loop_type(self, loop):
        """Query the loop control type."""
        loop = self._check_loop(loop)
        return self._query(f"LOOP {loop}:TYPE?")

    def set_loop_source(self, loop, channel):
        """Set which input channel the loop controls from."""
        loop = self._check_loop(loop)
        if channel not in self.INPUT_CHANNELS:
            raise ValueError(
                f"Channel must be one of {self.INPUT_CHANNELS}, "
                f"got {channel}")
        cmd = f"LOOP {loop}:SOURCE CH{channel}"
        self._write(cmd)
        return cmd

    def get_loop_source(self, loop):
        """Query the loop's controlling input channel (e.g. 'CHA')."""
        loop = self._check_loop(loop)
        return self._query(f"LOOP {loop}:SOURCE?")

    # -- Heater range and load (Manual: LOOP 1:RANGE / LOOP 1:LOAD) --
    # Both are Loop 1 (primary heater) commands only.

    def set_range(self, range_value):
        """Set the Loop 1 heater range (Hi / Mid / Low)."""
        if range_value not in self.HEATER_RANGES:
            raise ValueError(
                f"Range must be one of {list(self.HEATER_RANGES)}, "
                f"got {range_value}")
        cmd = f"LOOP 1:RANGE {range_value}"
        self._write(cmd)
        return cmd

    def get_range(self):
        """Query the Loop 1 heater range."""
        return self._query("LOOP 1:RANGE?")

    def set_load(self, ohms):
        """Set the Loop 1 heater load resistance (50 or 25 ohm)."""
        if str(ohms) not in ('50', '25'):
            raise ValueError(f"Load must be 50 or 25 ohm, got {ohms}")
        cmd = f"LOOP 1:LOAD {ohms}"
        self._write(cmd)
        return cmd

    def get_load(self):
        """Query the Loop 1 heater load resistance."""
        return self._query("LOOP 1:LOAD?")

    # -- Output limits (Manual: LOOP:MAXPWR / LOOP:MAXSET) --

    def set_max_power(self, loop, percent):
        """Set the loop's maximum output power, in percent of full scale."""
        loop = self._check_loop(loop)
        if not (0 <= percent <= 100):
            raise ValueError(
                f"Maximum power must be 0-100 %, got {percent}")
        cmd = f"LOOP {loop}:MAXPWR {percent}"
        self._write(cmd)
        return cmd

    def get_max_power(self, loop):
        """Query the loop's maximum output power in percent."""
        loop = self._check_loop(loop)
        return self._to_float(
            self._query(f"LOOP {loop}:MAXPWR?"), "MAXPWR?")

    def set_max_setpoint(self, loop, value):
        """Set the loop's maximum allowed setpoint."""
        loop = self._check_loop(loop)
        if not (0 <= value <= 1000):
            raise ValueError(
                f"Maximum setpoint must be 0-1000 K, got {value}")
        cmd = f"LOOP {loop}:MAXSET {value}"
        self._write(cmd)
        return cmd

    def get_max_setpoint(self, loop):
        """Query the loop's maximum allowed setpoint."""
        loop = self._check_loop(loop)
        return self._to_float(
            self._query(f"LOOP {loop}:MAXSET?"), "MAXSET?")

    # -- Manual power (Manual: LOOP:PMANUAL) --
    # Only used while the loop's TYPE is Man.

    def set_manual_power(self, loop, percent):
        """Set the manual output power (percent of full scale)."""
        loop = self._check_loop(loop)
        if not (0 <= percent <= 100):
            raise ValueError(
                f"Manual power must be 0-100 %, got {percent}")
        cmd = f"LOOP {loop}:PMANUAL {percent}"
        self._write(cmd)
        return cmd

    def get_manual_power(self, loop):
        """Query the manual output power setting in percent."""
        loop = self._check_loop(loop)
        return self._to_float(
            self._query(f"LOOP {loop}:PMANUAL?"), "PMANUAL?")

    # -- Output readback (Manual: LOOP:OUTPWR? / LOOP:HTRREAD?) --

    def get_output_power(self, loop):
        """Commanded output power of the loop, percent of full scale."""
        loop = self._check_loop(loop)
        return self._to_float(
            self._query(f"LOOP {loop}:OUTPWR?"), "OUTPWR?")

    def get_heater_readback(self, loop):
        """Measured heater output from the independent read-back circuit."""
        loop = self._check_loop(loop)
        return self._query(f"LOOP {loop}:HTRREAD?")

    # -- Temperature readings (Manual: INPUT? / INPUT:SENPR?) --
    # INPUT? reports in the CHANNEL's display units, not always Kelvin.

    def get_temperature(self, channel):
        """Query the channel reading in its own display units."""
        raw = self._query(f"INPUT? {channel}")
        return self._to_float(raw, f"INPUT? {channel}")

    def get_sensor_reading(self, channel):
        """Query the raw sensor reading (Volts or Ohms)."""
        raw = self._query(f"INPUT {channel}:SENPR?")
        return self._to_float(raw, f"INPUT {channel}:SENPR?")

    def get_alarm_status(self, channel):
        """Query the channel alarm status ('--', 'SF', 'HI' or 'LO')."""
        return self._query(f"INPUT {channel}:ALARM?")

    # -- Input configuration (Manual: INPUT:UNITS / ISENIX / USENIX / NAME) --

    def set_units(self, channel, units):
        """Set the channel display units (K, C, F or S)."""
        if units not in self.DISPLAY_UNITS:
            raise ValueError(
                f"Units must be one of {list(self.DISPLAY_UNITS)}, "
                f"got {units}")
        cmd = f"INPUT {channel}:UNITS {units}"
        self._write(cmd)
        return cmd

    def get_units(self, channel):
        """Query the channel display units."""
        return self._query(f"INPUT {channel}:UNITS?")

    def set_factory_sensor_index(self, channel, index):
        """Assign a factory-installed sensor curve (ISENIX). 0 disables."""
        if index < 0:
            raise ValueError(f"Sensor index must be >= 0, got {index}")
        cmd = f"INPUT {channel}:ISENIX {index}"
        self._write(cmd)
        return cmd

    def get_factory_sensor_index(self, channel):
        """Query the factory sensor index (-1 if invalid)."""
        return self._query(f"INPUT {channel}:ISENIX?")

    def set_user_sensor_index(self, channel, index):
        """Assign a user-installed sensor curve (USENIX), index 0-3."""
        if not (0 <= index <= 3):
            raise ValueError(
                f"User sensor index must be 0-3, got {index}")
        cmd = f"INPUT {channel}:USENIX {index}"
        self._write(cmd)
        return cmd

    def get_user_sensor_index(self, channel):
        """Query the user sensor index (-1 if outside 0-3)."""
        return self._query(f"INPUT {channel}:USENIX?")

    def set_input_name(self, channel, name):
        """Set a custom name for an input channel (15 chars max)."""
        cmd = f'INPUT {channel}:NAME "{name}"'
        self._write(cmd)
        return cmd

    def get_input_name(self, channel):
        """Query the name of an input channel."""
        return self._query(f"INPUT {channel}:NAME?")

    # -- Alarms (Manual: INPUT:ALARM:*) --

    def set_alarm(self, channel, high, low, high_enable, low_enable):
        """Set the high/low alarm setpoints and their enables."""
        cmd = (f"INPUT {channel}:ALARM:HIGHEST {high};"
               f"LOWEST {low};"
               f"HIENA {'YES' if high_enable else 'NO'};"
               f"LOENA {'YES' if low_enable else 'NO'}")
        self._write(cmd)
        return cmd

    def get_alarm(self, channel):
        """Query high setpoint, low setpoint and both enables."""
        high = self._query(f"INPUT {channel}:ALARM:HIGHEST?")
        low = self._query(f"INPUT {channel}:ALARM:LOWEST?")
        hiena = self._query(f"INPUT {channel}:ALARM:HIENA?")
        loena = self._query(f"INPUT {channel}:ALARM:LOENA?")
        return high, low, hiena, loena

    # -- Over Temperature Disconnect (Manual: OVERTEMP:*) --
    # Safety: if the source channel exceeds the limit, the heater is
    # disconnected. This is the Cryocon analogue of Lake Shore TLIMIT.

    def set_overtemp(self, enable, channel, temperature):
        """Configure the over-temperature disconnect."""
        if channel not in self.INPUT_CHANNELS:
            raise ValueError(
                f"Channel must be one of {self.INPUT_CHANNELS}, "
                f"got {channel}")
        if not (0 <= temperature <= 1000):
            raise ValueError(
                f"Over-temperature limit must be 0-1000 K, "
                f"got {temperature}")
        cmd = (f"OVERTEMP:SOURCE CH{channel};"
               f"TEMP {temperature};"
               f"ENABLE {'ON' if enable else 'OFF'}")
        self._write(cmd)
        return cmd

    def get_overtemp(self):
        """Query the over-temperature disconnect (enable, source, temp)."""
        enable = self._query("OVERTEMP:ENABLE?")
        source = self._query("OVERTEMP:SOURCE?")
        temp = self._query("OVERTEMP:TEMP?")
        return enable, source, temp

    # -- Control loop engage / disengage (Manual: CONTROL / STOP) --

    def control_engage(self):
        """CONTROL: activate every enabled control loop."""
        self._write("CONTROL")
        return "CONTROL"

    def control_stop(self):
        """STOP: disengage all control loops and disconnect the heaters."""
        self._write("STOP")
        return "STOP"

    def get_control_status(self):
        """Query whether the control loops are engaged ('ON' / 'OFF')."""
        return self._query("CONTROL?")

    # -- System settings (Manual: SYSTEM:*) --

    def set_lockout(self, enable):
        """Lock or unlock the front-panel keypad."""
        cmd = f"SYSTEM:LOCKOUT {'ON' if enable else 'OFF'}"
        self._write(cmd)
        return cmd

    def get_lockout(self):
        """Query the front-panel keypad lockout state."""
        return self._query("SYSTEM:LOCKOUT?")

    def set_display_resolution(self, resolution):
        """Set the front-panel display resolution (FULL, 1, 2 or 3)."""
        if str(resolution).upper() not in ('FULL', '1', '2', '3'):
            raise ValueError(
                f"Display resolution must be FULL, 1, 2 or 3, "
                f"got {resolution}")
        cmd = f"SYSTEM:DRES {resolution}"
        self._write(cmd)
        return cmd

    def get_display_resolution(self):
        """Query the front-panel display resolution."""
        return self._query("SYSTEM:DRES?")

    def set_display_time_constant(self, tc):
        """Set the display filter time constant in seconds."""
        if str(tc) not in self.DISPLAY_TIME_CONSTANTS:
            raise ValueError(
                f"Time constant must be one of "
                f"{self.DISPLAY_TIME_CONSTANTS} s, got {tc}")
        cmd = f"SYSTEM:DISTC {tc}"
        self._write(cmd)
        return cmd

    def get_display_time_constant(self):
        """Query the display filter time constant in seconds."""
        return self._query("SYSTEM:DISTC?")

    def save_to_flash(self):
        """SYSTEM:NVSAVE - persist the current configuration to flash."""
        self._write("SYSTEM:NVSAVE")
        return "SYSTEM:NVSAVE"

    def get_ambient(self):
        """Internal reference temperature, e.g. '+25C'."""
        return self._query("SYSTEM:AMBIENT?")

    def get_error_queue(self):
        """Query the instrument error queue."""
        return self._query("SYSTEM:ERROR?")

    # -- Common SCPI commands --

    def clear_status(self):
        """Send *CLS to clear status registers."""
        self._write("*CLS")

    def reset(self):
        """Send *RST. DANGEROUS: a ~15 s hardware reset to power-up
        defaults, during which the instrument answers nothing."""
        self._write("*RST")

    def get_idn(self):
        """Query *IDN? for instrument identification."""
        return self._query("*IDN?")

    # -- Scan for VISA resources --

    def scan_resources(self):
        """List available VISA resources."""
        if not self.rm:
            return []
        return self.rm.list_resources()


# ---------------------------------------------------------------------------
# FRONTEND: Direct Control GUI
# ---------------------------------------------------------------------------

class DirectControlGUI:
    """
    GUI for the Cryocon 34 Direct Control Utility.

    Each control panel sends SCPI commands independently.
    Disconnect is non-destructive - instrument settings persist.
    """

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Cryocon 34 Direct Control Utility"

    # Color scheme (identical to reference programme)
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

    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 12, 'bold')

    LEFT_PANEL_WIDTH = 520  # default sash position so the left panel starts fully visible

    # PID starting points. On a Cryocon, I is in SECONDS and larger means
    # SLOWER, so these are NOT the Lake Shore numbers and must be tuned
    # against the actual cryostat before use.
    PID_PRESETS = {
        'Gentle (P=5, I=60 s, D=0)': (5.0, 60.0, 0.0),
        'Moderate (P=20, I=20 s, D=0)': (20.0, 20.0, 0.0),
        'Aggressive (P=50, I=8 s, D=0)': (50.0, 8.0, 0.0),
    }

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x950")
        self.root.minsize(1200, 750)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = Cryocon34Backend(log=self.log)
        # Display units per input channel, refreshed on connect and after
        # a units change, so the 1 s poll does not re-query them every tick.
        self.channel_units = {
            ch: '' for ch in Cryocon34Backend.INPUT_CHANNELS}
        self.logo_image = None
        self.is_connected = False
        self.polling_active = False
        # Staged polling: which group the next tick refreshes, and the
        # last message logged per field so a recurring fault is reported
        # once rather than every tick.
        self._poll_stage = 0
        self._poll_notes = {}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # -----------------------------------------------------------------------
    # STYLES
    # -----------------------------------------------------------------------

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
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
            bordercolor='#BA6B5E')
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        style.configure(
            'TEntry',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            insertcolor=self.CLR_TEXT_DARK)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK)

    # -----------------------------------------------------------------------
    # WIDGET CREATION
    # -----------------------------------------------------------------------

    def create_widgets(self):
        # --- Header ---
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = (
            'Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(
            header,
            text=self.PROGRAM_NAME,
            style='Header.TLabel',
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)

        plotter_button = ttk.Button(
            header,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)
        gpib_button = ttk.Button(
            header,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        # --- Main paned window ---
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # pack_propagate(False) makes the requested width stick; weight=0
        # keeps the left panel from being squeezed as the window resizes,
        # while the right panel absorbs all extra space.
        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)

        # sashpos() has no effect until the PanedWindow is actually mapped
        # and laid out — an early call fails SILENTLY. So we (a) wait for the
        # window to be drawn, (b) measure the real required width of the
        # left-panel content instead of guessing, and (c) retry until the
        # sash position verifiably sticks.
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()  # force geometry to be computed

            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30  # scrollbar (~15px) + padding
            else:
                target = self.LEFT_PANEL_WIDTH

            self.main_pane.sashpos(0, target)

            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

    # -- Left panel (controls) --

    def _populate_left_panel(self, panel):
        """Create a scrollable left panel containing all control sections."""
        canvas = tk.Canvas(
            panel,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            panel, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(
                scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window(
            (0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            '<Configure>',
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scroll_frame

        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        def _on_mousewheel(event):
            canvas.yview_scroll(
                int(-1 * (event.delta / 120)), 'units')

        canvas.bind_all('<MouseWheel>', _on_mousewheel)
        canvas.bind(
            '<Enter>',
            lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind(
            '<Leave>',
            lambda e: canvas.unbind_all('<MouseWheel>'))

        scroll_frame.grid_columnconfigure(0, weight=1)
        scroll_frame.grid_rowconfigure(99, weight=1)

        self._create_info_panel(scroll_frame, 0)
        self._create_connection_panel(scroll_frame, 1)
        self._create_engage_panel(scroll_frame, 2)
        self._create_pid_panel(scroll_frame, 3)
        self._create_setpoint_panel(scroll_frame, 4)
        self._create_range_panel(scroll_frame, 5)
        self._create_manual_output_panel(scroll_frame, 6)
        self._create_limits_panel(scroll_frame, 7)
        self._create_input_config_panel(scroll_frame, 8)
        self._create_alarm_panel(scroll_frame, 9)
        self._create_advanced_panel(scroll_frame, 10)
        self._create_overtemp_panel(scroll_frame, 11)
        self._create_console_panel(scroll_frame, 99)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        LOGO_SIZE = 90
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2,
                         padx=10, pady=10)

        try:
            logo_path = pica_asset("LOGO", "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and logo_path:
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE),
                    Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2,
                    image=self.logo_image)
        except Exception:
            pass  # Logo is optional

        institute_font = (
            'Segoe UI', self.FONT_BASE[1] + 1, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=0, column=1, padx=10, pady=(20, 0), sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=1, column=1, padx=10, pady=(0, 5), sticky='nw')
        ttk.Label(
            frame,
            text="Cryocon Model 34 | Loop 1: 50 W Heater",
            background=self.CLR_FRAME_BG).grid(
            row=2, column=0, columnspan=2,
            padx=10, pady=(0, 10), sticky='w')

    def _create_connection_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="VISA Address:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.visa_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.visa_cb.grid(row=0, column=1, sticky='ew',
                          padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=1, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.connect_btn = ttk.Button(
            btn_frame,
            text="Connect",
            style='Connect.TButton',
            command=self._do_connect)
        self.connect_btn.grid(row=0, column=0, sticky='ew', padx=5)

        self.disconnect_btn = ttk.Button(
            btn_frame,
            text="Disconnect",
            style='Disconnect.TButton',
            state='disabled',
            command=self._do_disconnect)
        self.disconnect_btn.grid(
            row=0, column=1, sticky='ew', padx=5)

        ttk.Button(
            btn_frame,
            text="Scan",
            command=self._scan_visa).grid(
            row=0, column=2, sticky='ew', padx=5)

        self.status_label = ttk.Label(
            frame,
            text="● Not Connected",
            font=self.FONT_STATUS,
            foreground=self.CLR_STATUS_BAD,
            background=self.CLR_FRAME_BG)
        self.status_label.grid(
            row=2, column=0, columnspan=2,
            sticky='w', padx=10, pady=(0, 5))

    def _create_engage_panel(self, parent, grid_row):
        """CONTROL / STOP plus the loop control type and source channel."""
        frame = ttk.LabelFrame(parent, text='Control Loop Engage')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("CONTROL engages every enabled loop; STOP\n"
                  "disengages all loops and drops the heaters.\n"
                  "Set a loop's type to Off to disable it alone."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 5))

        self.type_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.type_loop_var,
            values=Cryocon34Backend.LOOPS, state='readonly',
            width=5).grid(row=1, column=1, sticky='w',
                          padx=10, pady=5)

        ttk.Label(frame, text="Control Type:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.loop_type_var = tk.StringVar(value='PID')
        ttk.Combobox(
            frame, textvariable=self.loop_type_var,
            values=list(Cryocon34Backend.CONTROL_TYPES),
            state='readonly').grid(
            row=2, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(frame, text="Source Input:").grid(
            row=3, column=0, sticky='w', padx=10, pady=5)
        self.loop_source_var = tk.StringVar(value='A')
        ttk.Combobox(
            frame, textvariable=self.loop_source_var,
            values=Cryocon34Backend.INPUT_CHANNELS,
            state='readonly',
            width=5).grid(row=3, column=1, sticky='w',
                          padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Type + Source",
            command=self._send_loop_type).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Type + Source",
            command=self._read_loop_type).grid(
            row=0, column=1, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="CONTROL (engage loops)",
            style='Connect.TButton',
            command=self._send_control).grid(
            row=1, column=0, sticky='ew', padx=5, pady=(5, 0))
        ttk.Button(
            btn_frame,
            text="STOP (all heaters off)",
            style='Disconnect.TButton',
            command=self._send_stop).grid(
            row=1, column=1, sticky='ew', padx=5, pady=(5, 0))

    def _create_pid_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='PID Gains')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("Cryocon I is a TIME in seconds: larger = slower\n"
                  "integral action. This is the opposite sense to a\n"
                  "Lake Shore 350 -- do not copy L350 numbers here.\n"
                  "D adds noise and is normally left at 0."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 5))

        self.pid_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.pid_loop_var,
            values=Cryocon34Backend.LOOPS, state='readonly',
            width=5).grid(row=1, column=1, sticky='w',
                          padx=10, pady=5)

        ttk.Label(frame, text="Preset:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.pid_preset_var = tk.StringVar()
        pid_preset_cb = ttk.Combobox(
            frame, textvariable=self.pid_preset_var,
            values=list(self.PID_PRESETS.keys()) + ['Custom'],
            state='readonly')
        pid_preset_cb.grid(row=2, column=1, sticky='ew',
                           padx=10, pady=5)
        pid_preset_cb.bind(
            '<<ComboboxSelected>>',
            self._on_pid_preset_change)

        self.pid_p_entry = self._make_entry(
            frame, "P (0-1000, unitless)", "20", 3)
        self.pid_i_entry = self._make_entry(
            frame, "I (0-1000 seconds)", "20", 4)
        self.pid_d_entry = self._make_entry(
            frame, "D (0-1000 /second)", "0", 5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=6, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send PID",
            command=self._send_pid).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read PID",
            command=self._read_pid).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_setpoint_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Setpoint Control')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("Setpoint units follow the display units of the\n"
                  "loop's source channel. Sending a setpoint also\n"
                  "sets the loop type: PID, or RampP when ramping."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 5))

        self.setp_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.setp_loop_var,
            values=Cryocon34Backend.LOOPS, state='readonly',
            width=5).grid(row=1, column=1, sticky='w',
                          padx=10, pady=5)

        self.setp_entry = self._make_entry(
            frame, "Setpoint (K)", "300", 2)

        self.ramp_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable Ramp (LOOP:TYPE RampP)",
            variable=self.ramp_enabled_var,
            command=self._toggle_ramp_fields).grid(
            row=3, column=0, columnspan=2, sticky='w',
            padx=10, pady=2)

        self.ramp_rate_entry = self._make_entry(
            frame, "Ramp Rate (K/min, 0-100)", "2.0", 4)
        self.ramp_rate_entry.config(state='disabled')

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Setpoint",
            command=self._send_setpoint).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Setpoint",
            command=self._read_setpoint).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_range_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='Heater Range and Load (Loop 1)')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("RANGE and LOAD apply to Loop 1, the primary\n"
                  "heater. Changing LOAD tells the instrument what\n"
                  "resistor is fitted; it does not change wiring."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 5))

        ttk.Label(frame, text="Range:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        self.range_var = tk.StringVar(value='Mid')
        ttk.Combobox(
            frame, textvariable=self.range_var,
            values=list(Cryocon34Backend.HEATER_RANGES),
            state='readonly').grid(
            row=1, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(frame, text="Load (ohm):").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.load_var = tk.StringVar(value='50')
        ttk.Combobox(
            frame, textvariable=self.load_var,
            values=['50', '25'], state='readonly',
            width=6).grid(row=2, column=1, sticky='w',
                          padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Range",
            command=self._send_range).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Range",
            command=self._read_range).grid(
            row=0, column=1, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Send Load",
            command=self._send_load).grid(
            row=1, column=0, sticky='ew', padx=5, pady=(5, 0))
        ttk.Button(
            btn_frame,
            text="Read Load",
            command=self._read_load).grid(
            row=1, column=1, sticky='ew', padx=5, pady=(5, 0))

    def _create_manual_output_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Manual Output')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("PMANUAL is only used while the loop's control\n"
                  "type is Man. It may be set at any time."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 5))

        self.mout_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.mout_loop_var,
            values=Cryocon34Backend.LOOPS, state='readonly',
            width=5).grid(row=1, column=1, sticky='w',
                          padx=10, pady=5)

        self.mout_entry = self._make_entry(
            frame, "Manual Power (%)", "0", 2)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send PMANUAL",
            command=self._send_pmanual).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read PMANUAL",
            command=self._read_pmanual).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_limits_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Output Limits')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.limits_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.limits_loop_var,
            values=Cryocon34Backend.LOOPS, state='readonly',
            width=5).grid(row=0, column=1, sticky='w',
                          padx=10, pady=5)

        self.maxpwr_entry = self._make_entry(
            frame, "Max Power (% of full scale)", "100", 1)
        self.maxset_entry = self._make_entry(
            frame, "Max Setpoint (K)", "320", 2)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Limits",
            command=self._send_limits).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Limits",
            command=self._read_limits).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_input_config_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Input Configuration')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("Sensor index selects the calibration curve.\n"
                  "Factory curves use ISENIX (0 disables the\n"
                  "channel); user curves use USENIX, index 0-3.\n\n"
                  "A user curve has to be installed before an index\n"
                  "can point at it. The Model 34 has no Cernox curve\n"
                  "of its own, so a calibrated Cernox needs loading\n"
                  "first — that is what the button below does."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 5))

        ttk.Label(frame, text="Input:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        self.incfg_ch_var = tk.StringVar(value='A')
        ttk.Combobox(
            frame, textvariable=self.incfg_ch_var,
            values=Cryocon34Backend.INPUT_CHANNELS,
            state='readonly',
            width=5).grid(row=1, column=1, sticky='w',
                          padx=10, pady=5)

        ttk.Label(frame, text="Display Units:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.incfg_units_var = tk.StringVar(value='K')
        ttk.Combobox(
            frame, textvariable=self.incfg_units_var,
            values=list(Cryocon34Backend.DISPLAY_UNITS),
            state='readonly',
            width=5).grid(row=2, column=1, sticky='w',
                          padx=10, pady=5)

        ttk.Label(frame, text="Curve Source:").grid(
            row=3, column=0, sticky='w', padx=10, pady=5)
        self.incfg_curve_src_var = tk.StringVar(value='Factory (ISENIX)')
        ttk.Combobox(
            frame, textvariable=self.incfg_curve_src_var,
            values=['Factory (ISENIX)', 'User (USENIX)'],
            state='readonly').grid(
            row=3, column=1, sticky='ew', padx=10, pady=5)

        self.incfg_index_entry = self._make_entry(
            frame, "Sensor Index", "0", 4)
        self.incfg_name_entry = self._make_entry(
            frame, "Channel Name", "", 5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=6, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Units",
            command=self._send_units).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Send Sensor Index",
            command=self._send_sensor_index).grid(
            row=0, column=1, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Send Name",
            command=self._send_input_name).grid(
            row=1, column=0, sticky='ew', padx=5, pady=(5, 0))
        ttk.Button(
            btn_frame,
            text="Read Input Config",
            command=self._read_input_config).grid(
            row=1, column=1, sticky='ew', padx=5, pady=(5, 0))
        # Opens in its own process and holds its own VISA session, so a
        # 130-line curve transfer never shares this window's connection.
        ttk.Button(
            btn_frame,
            text="Load a Sensor Curve (Cernox, RuOx ...)",
            command=launch_curve_loader).grid(
            row=2, column=0, columnspan=2, sticky='ew',
            padx=5, pady=(8, 0))

    def _create_alarm_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Input Alarms')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("Alarms annunciate only; they do not cut the\n"
                  "heater. Use the Over-Temperature Disconnect\n"
                  "panel below for a protective shutdown."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 5))

        ttk.Label(frame, text="Input:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        self.alarm_ch_var = tk.StringVar(value='A')
        ttk.Combobox(
            frame, textvariable=self.alarm_ch_var,
            values=Cryocon34Backend.INPUT_CHANNELS,
            state='readonly',
            width=5).grid(row=1, column=1, sticky='w',
                          padx=10, pady=5)

        self.alarm_high_entry = self._make_entry(
            frame, "High Alarm (K)", "320", 2)
        self.alarm_low_entry = self._make_entry(
            frame, "Low Alarm (K)", "0", 3)

        self.alarm_high_en_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable high alarm",
            variable=self.alarm_high_en_var).grid(
            row=4, column=0, columnspan=2, sticky='w',
            padx=10, pady=2)
        self.alarm_low_en_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable low alarm",
            variable=self.alarm_low_en_var).grid(
            row=5, column=0, columnspan=2, sticky='w',
            padx=10, pady=2)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=6, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Alarms",
            command=self._send_alarm).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Alarms",
            command=self._read_alarm).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_advanced_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Advanced / System')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Keypad Lockout:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.lockout_var = tk.StringVar(value='Off')
        ttk.Combobox(
            frame, textvariable=self.lockout_var,
            values=['Off', 'On'], state='readonly',
            width=6).grid(row=0, column=1, sticky='w',
                          padx=10, pady=5)
        ttk.Button(
            frame,
            text="Send Lockout",
            command=self._send_lockout).grid(
            row=1, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

        ttk.Label(frame, text="Display Resolution:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.dres_var = tk.StringVar(value='FULL')
        ttk.Combobox(
            frame, textvariable=self.dres_var,
            values=['FULL', '1', '2', '3'], state='readonly',
            width=6).grid(row=2, column=1, sticky='w',
                          padx=10, pady=5)

        ttk.Label(frame, text="Display Filter (s):").grid(
            row=3, column=0, sticky='w', padx=10, pady=5)
        self.distc_var = tk.StringVar(value='2')
        ttk.Combobox(
            frame, textvariable=self.distc_var,
            values=Cryocon34Backend.DISPLAY_TIME_CONSTANTS,
            state='readonly',
            width=6).grid(row=3, column=1, sticky='w',
                          padx=10, pady=5)
        ttk.Button(
            frame,
            text="Send Display Settings",
            command=self._send_display_settings).grid(
            row=4, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

        ttk.Button(
            frame,
            text="Read Error Queue (SYSTEM:ERROR?)",
            command=self._read_errors).grid(
            row=5, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

        ttk.Button(
            frame,
            text="Save Config to Flash (SYSTEM:NVSAVE)",
            command=self._save_to_flash).grid(
            row=6, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

        ttk.Button(
            frame,
            text="⚠ Hardware Reset (*RST)",
            command=self._factory_reset).grid(
            row=7, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

    def _create_overtemp_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent,
            text='⚠ DANGER: Over-Temperature Disconnect (OVERTEMP)')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text="THIS IS NOT THE CONTROL SETPOINT!",
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_ACCENT_RED,
            font=('Segoe UI', 10, 'bold')).grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 0))
        ttk.Label(
            frame,
            text=("Protective shutdown: if the source channel\n"
                  "exceeds this temperature, the heater is\n"
                  "disconnected. The limit is in the source\n"
                  "channel's own display units.\n"
                  "To set a target temperature, use the\n"
                  "'Setpoint Control' panel above instead."),
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_ACCENT_RED,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=1, column=0, columnspan=2,
            sticky='w', padx=10, pady=(0, 5))

        ttk.Label(frame, text="Source Input:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.otd_ch_var = tk.StringVar(value='A')
        ttk.Combobox(
            frame, textvariable=self.otd_ch_var,
            values=Cryocon34Backend.INPUT_CHANNELS,
            state='readonly',
            width=5).grid(row=2, column=1, sticky='w',
                          padx=10, pady=5)

        self.otd_temp_entry = self._make_entry(
            frame, "Disconnect Above (K)", "320", 3)

        self.otd_enable_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable over-temperature disconnect",
            variable=self.otd_enable_var).grid(
            row=4, column=0, columnspan=2, sticky='w',
            padx=10, pady=2)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send OVERTEMP",
            command=self._send_overtemp).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read OVERTEMP",
            command=self._read_overtemp).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew',
                   pady=5, padx=10)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.console = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            borderwidth=0,
            height=8)
        self.console.grid(row=0, column=0, sticky='nsew',
                          padx=5, pady=5)
        self.log("Console initialized. Scan for instruments, "
                 "then connect.")

    # -- Right panel (live status monitor) --

    def _populate_right_panel(self, panel):
        # One row, two columns. Give more space to the status panel.
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=2)
        panel.grid_columnconfigure(1, weight=1)

        container = ttk.LabelFrame(
            panel, text='Live Instrument Status')
        container.grid(row=0, column=0, sticky='nsew',
                       padx=5, pady=5)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(
            container,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            container, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(
                scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame,
                             anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side='left', fill='both', expand=True,
                    padx=5, pady=5)
        scrollbar.pack(side='right', fill='y')

        # --- Status display widgets ---
        self.status_labels = {}
        row = 0

        ttk.Label(
            scroll_frame,
            text="── Temperatures ──",
            font=self.FONT_TITLE,
            background=self.CLR_FRAME_BG).grid(
            row=row, column=0, columnspan=2,
            sticky='w', pady=(10, 5))
        row += 1

        for ch in Cryocon34Backend.INPUT_CHANNELS:
            ttk.Label(
                scroll_frame,
                text=f"Input {ch}:",
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w', padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                font=self.FONT_STATUS,
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w', padx=20, pady=2)
            self.status_labels[f'temp_{ch}'] = lbl
            row += 1

        for ch in Cryocon34Backend.INPUT_CHANNELS:
            ttk.Label(
                scroll_frame,
                text=f"Input {ch} alarm:",
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w', padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w', padx=20, pady=2)
            self.status_labels[f'alarm_{ch}'] = lbl
            row += 1

        ttk.Label(
            scroll_frame,
            text="── Heater ──",
            font=self.FONT_TITLE,
            background=self.CLR_FRAME_BG).grid(
            row=row, column=0, columnspan=2,
            sticky='w', pady=(15, 5))
        row += 1

        for loop in Cryocon34Backend.LOOPS:
            ttk.Label(
                scroll_frame,
                text=f"Loop {loop} output (%):",
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w',
                padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                font=self.FONT_STATUS,
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w',
                     padx=20, pady=2)
            self.status_labels[f'outpwr_{loop}'] = lbl
            row += 1

        for loop in Cryocon34Backend.LOOPS:
            ttk.Label(
                scroll_frame,
                text=f"Loop {loop} read-back:",
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w',
                padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w',
                     padx=20, pady=2)
            self.status_labels[f'htrread_{loop}'] = lbl
            row += 1

        ttk.Label(
            scroll_frame,
            text="── Control Parameters ──",
            font=self.FONT_TITLE,
            background=self.CLR_FRAME_BG).grid(
            row=row, column=0, columnspan=2,
            sticky='w', pady=(15, 5))
        row += 1

        for label_text, key in [
            ("Control loops:", 'control_status'),
            ("Setpoint 1:", 'setpoint_1'),
            ("Setpoint 2:", 'setpoint_2'),
            ("PID 1 (P, I, D):", 'pid_1'),
            ("PID 2 (P, I, D):", 'pid_2'),
            ("Type 1 / Source 1:", 'type_1'),
            ("Type 2 / Source 2:", 'type_2'),
            ("Ramp 1 (state, rate):", 'ramp_1'),
            ("Ramp 2 (state, rate):", 'ramp_2'),
            ("Heater range / load:", 'range_1'),
            ("Over-temp disconnect:", 'overtemp'),
            ("Keypad lockout:", 'lockout'),
        ]:
            ttk.Label(
                scroll_frame,
                text=label_text,
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w',
                padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w',
                     padx=20, pady=2)
            self.status_labels[key] = lbl
            row += 1

        # Polling control
        row += 1
        ttk.Separator(scroll_frame).grid(
            row=row, column=0, columnspan=2,
            sticky='ew', padx=20, pady=10)
        row += 1

        self.poll_btn = ttk.Button(
            scroll_frame,
            text="Start Polling",
            command=self._toggle_polling)
        self.poll_btn.grid(row=row, column=0, columnspan=2,
                           sticky='ew', padx=20, pady=5)

        # --- Notes Panel ---
        notes_frame = ttk.LabelFrame(panel, text="Notes & Guides")
        notes_frame.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
        notes_frame.grid_columnconfigure(0, weight=1)
        notes_frame.grid_rowconfigure(0, weight=1)

        notes_text_widget = scrolledtext.ScrolledText(
            notes_frame,
            wrap='word',
            bg=self.CLR_FRAME_BG,
            fg=self.CLR_TEXT_DARK,
            font=('Consolas', 10),
            borderwidth=0,
            relief='flat'
        )
        notes_text_widget.pack(fill='both', expand=True, padx=5, pady=5)

        # --- Build guide tables with fixed-width columns ---
        def _build_table(headers, rows, widths):
            def fmt(cells):
                return (
                    "│ "
                    + " │ ".join(
                        c.ljust(w) for c, w in zip(cells, widths)
                    )
                    + " │"
                )

            seg = ["─" * (w + 2) for w in widths]
            top = "┌" + "┬".join(seg) + "┐"
            mid = "├" + "┼".join(seg) + "┤"
            bot = "└" + "┴".join(seg) + "┘"

            lines = [top, fmt(headers), mid]
            lines += [fmt(r) for r in rows]
            lines.append(bot)
            return "\n".join(lines)

        range_table = _build_table(
            ("Range", "50 ohm load", "25 ohm load", "Full-scale current"),
            [
                ("Hi", "50 W", "25 W", "1.0 A"),
                ("Mid", "5 W", "2.5 W", "0.333 A"),
                ("Low", "0.5 W", "0.25 W", "0.1 A"),
            ],
            (6, 12, 12, 19),
        )

        type_table = _build_table(
            ("LOOP:TYPE", "Meaning"),
            [
                ("Off", "Loop disabled"),
                ("PID", "Closed loop PID"),
                ("Man", "Manual power, uses PMANUAL"),
                ("Table", "PID table lookup, uses TABLEIX"),
                ("RampP", "PID with setpoint ramp, uses RATE"),
            ],
            (10, 38),
        )

        notes_content = (
            "Instrument facts confirmed from the Cryo-con manual:\n"
            "  • Input channels A, B, C, D\n"
            "  • Loop 1 is the primary heater; Loop 2 is the\n"
            "    secondary output\n"
            "  • LOOP:PGAIN 0-1000 (unitless)\n"
            "  • LOOP:IGAIN 0-1000 SECONDS\n"
            "  • LOOP:DGAIN 0-1000 inverse seconds\n"
            "  • LOOP:SETPT 0 K to 1000 K\n"
            "  • LOOP:RATE 0 to 100 units/minute\n"
            "  • CONTROL engages loops, STOP drops all heaters\n"
            "  • OVERTEMP:* is the protective disconnect\n"
            "  • GPIB: factory address 12, EOI framing, no EOS\n"
            "  • *RST is a ~15 s HARDWARE reset\n\n"
            "IMPORTANT - the integral term:\n"
            "  On a Cryocon, I is a TIME in seconds and a LARGER\n"
            "  value means SLOWER integral action. On a Lake Shore\n"
            "  350 a larger I is FASTER. Lakeshore PID numbers do\n"
            "  not transfer; retune against the cryostat.\n\n"
            "Loop 1 heater ranges:\n\n"
            + range_table
            + "\n\n"
            "Control types:\n\n"
            + type_table
            + "\n\n"
            "Reading units:\n"
            "  INPUT? reports in the CHANNEL's own display units.\n"
            "  Set a channel to K in 'Input Configuration' before\n"
            "  trusting a Kelvin reading, and note that the loop\n"
            "  setpoint follows the units of its source channel.\n\n"
            "Persistence:\n"
            "  Settings sent here are live immediately but are only\n"
            "  restored after a power cycle if you use 'Save Config\n"
            "  to Flash (SYSTEM:NVSAVE)'.\n\n"
            "Disconnect is non-destructive: closing this program\n"
            "leaves the instrument controlling exactly as it was."
        )

        notes_text_widget.insert('1.0', notes_content)
        notes_text_widget.config(state='disabled')

    # -----------------------------------------------------------------------
    # HELPER METHODS
    # -----------------------------------------------------------------------

    def _make_entry(self, parent, label_text, default, row):
        """Create a labeled entry widget. Returns the entry."""
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row, column=0, sticky='w', padx=10, pady=5)
        entry = ttk.Entry(parent, font=self.FONT_BASE)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=5)
        entry.insert(0, default)
        return entry

    def log(self, message):
        """Append a timestamped message to the console."""
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

    def _require_connection(self):
        """Check if connected. Returns True/False, shows error if not."""
        if not self.is_connected or not self.backend.is_connected:
            self.log("ERROR: Not connected to instrument.")
            messagebox.showerror(
                "Not Connected",
                "Please connect to the instrument first.")
            return False
        return True

    def _safe_command(self, description, func, *args, **kwargs):
        """
        Execute a backend command with full error handling.
        Logs the command, response, and any errors.
        """
        if not self._require_connection():
            return None
        try:
            self.log(f">>> {description}")
            result = func(*args, **kwargs)
            if result:
                self.log(f"    SENT: {result}")
            self.log(f"    OK: {description} completed.")
            return result
        except ValueError as e:
            self.log(f"    VALIDATION ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
        except Exception as e:
            err_detail = traceback.format_exc()
            self.log(f"    ERROR: {err_detail}")
            messagebox.showerror("Command Failed", str(e))
        return None

    def _read_float_entry(self, entry, label):
        """Parse an entry as a float, raising a readable ValueError."""
        try:
            return float(entry.get())
        except ValueError:
            raise ValueError(f"{label} must be a numeric value.")

    # -----------------------------------------------------------------------
    # CONNECTION HANDLERS
    # -----------------------------------------------------------------------

    def _scan_visa(self):
        """Scan for VISA resources and populate the combobox."""
        if not self.backend.rm:
            self.log("ERROR: PyVISA library not available.")
            messagebox.showerror(
                "PyVISA Missing",
                "PyVISA is not installed. "
                "Run: pip install pyvisa pyvisa-py")
            return

        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.scan_resources()
        except Exception as e:
            self.log(f"Scan error: {e}")
            return

        resources = list(resources)
        if not resources:
            self.log("No VISA instruments found.")
            return

        self.log(f"Found {len(resources)} resource(s):")
        self.visa_cb['values'] = resources

        # Identify by *IDN? rather than by address: the Cryocon does not have
        # to be at its factory address for this to find it, and a different
        # instrument sitting at that address cannot be picked by mistake.
        identities = self.backend.identify_resources(resources)
        for r in resources:
            self.log(f"  {r}  ->  {identities.get(r, 'no reply')}")

        cryocon = next(
            (r for r in resources if is_cryocon_idn(identities.get(r, ''))),
            None)
        if cryocon:
            self.visa_cb.set(cryocon)
            self.log(f"Cryocon identified at {cryocon} and selected.")
            return

        # Nothing identified itself as a Cryo-con. Offer the factory address
        # as a hint only -- connect() checks *IDN? again and refuses anything
        # that is not a Cryo-con, so this can never start driving a heater on
        # the wrong instrument.
        hint = next(
            (r for r in resources if CRYOCON_ADDRESS_HINT in r), None)
        if hint:
            self.visa_cb.set(hint)
            self.log(f"WARNING: no Cryo-con answered *IDN?. Selected {hint} "
                     f"on the factory address alone — check the instrument "
                     f"is powered and in remote.")
        else:
            self.log("WARNING: no Cryo-con found on the bus. Pick an address "
                     "manually if you know it.")

    def _do_connect(self):
        """Connect to the selected VISA address."""
        visa_addr = self.visa_cb.get()
        if not visa_addr:
            self.log("ERROR: No VISA address selected.")
            messagebox.showerror(
                "No Address",
                "Please scan and select a VISA address.")
            return

        try:
            self.log(f"Connecting to {visa_addr}...")
            idn = self.backend.connect(visa_addr)
            self.is_connected = True
            self.log(f"Connected: {idn}")
            if 'cryocon' not in idn.lower():
                self.log(
                    "WARNING: *IDN? does not mention Cryocon. Check that "
                    "this address is the Model 34 before sending commands.")
            self._refresh_channel_units()
            self.status_label.config(
                text="● Connected",
                foreground=self.CLR_STATUS_OK)
            self.connect_btn.config(state='disabled')
            self.disconnect_btn.config(state='normal')
            self.visa_cb.config(state='disabled')
            # Auto-start polling
            self._start_polling()
        except Exception as e:
            err_detail = traceback.format_exc()
            self.log(f"CONNECT ERROR: {err_detail}")
            messagebox.showerror(
                "Connection Failed",
                f"Could not connect to {visa_addr}:\n{e}")

    def _do_disconnect(self):
        """
        Non-destructive disconnect.
        Only closes the VISA resource. Instrument retains all settings.
        """
        self._stop_polling()
        self.log("Disconnecting (non-destructive)...")
        self.log("  Instrument settings will be retained.")
        self.backend.disconnect()
        self.is_connected = False
        self.log("Disconnected. Instrument continues operating "
                 "with current settings.")
        self.status_label.config(
            text="● Not Connected",
            foreground=self.CLR_STATUS_BAD)
        self.connect_btn.config(state='normal')
        self.disconnect_btn.config(state='disabled')
        self.visa_cb.config(state='readonly')
        for key in self.status_labels:
            self.status_labels[key].config(text="---")

    # -----------------------------------------------------------------------
    # CONTROL ENGAGE HANDLERS
    # -----------------------------------------------------------------------

    def _send_loop_type(self):
        """Send the loop control type and its source channel."""
        loop = self.type_loop_var.get()
        ctype = self.loop_type_var.get()
        channel = self.loop_source_var.get()
        if self._safe_command(
                f"Set Loop {loop} source to input {channel}",
                self.backend.set_loop_source, loop, channel) is None:
            return
        self._safe_command(
            f"Set Loop {loop} control type to {ctype}",
            self.backend.set_loop_type, loop, ctype)

    def _read_loop_type(self):
        """Read the loop control type and source channel."""
        if not self._require_connection():
            return
        try:
            loop = self.type_loop_var.get()
            ctype = self.backend.get_loop_type(loop)
            source = self.backend.get_loop_source(loop)
            self.log(f"Read Loop {loop}: type={ctype}, source={source}")
        except Exception as e:
            self.log(f"ERROR reading loop type: {e}")
            messagebox.showerror("Read Failed", str(e))

    def _send_control(self):
        """Engage the control loops (CONTROL)."""
        if not self._require_connection():
            return
        if not messagebox.askyesno(
                "Engage Control Loops",
                "CONTROL will activate every enabled control loop "
                "and start driving the heater.\n\n"
                "Check the setpoint, heater range and over-temperature "
                "disconnect first.\n\nEngage now?"):
            self.log("CONTROL cancelled.")
            return
        self._safe_command(
            "Engage control loops (CONTROL)",
            self.backend.control_engage)

    def _send_stop(self):
        """Disengage the control loops (STOP)."""
        self._safe_command(
            "Disengage control loops (STOP)",
            self.backend.control_stop)

    # -----------------------------------------------------------------------
    # PID HANDLERS
    # -----------------------------------------------------------------------

    def _on_pid_preset_change(self, event=None):
        """Update P/I/D entries when a preset is selected."""
        preset = self.pid_preset_var.get()
        if preset in self.PID_PRESETS:
            p, i, d = self.PID_PRESETS[preset]
            for entry, value in (
                    (self.pid_p_entry, p),
                    (self.pid_i_entry, i),
                    (self.pid_d_entry, d)):
                entry.delete(0, 'end')
                entry.insert(0, str(value))

    def _send_pid(self):
        """Send PID gain values to the instrument."""
        try:
            loop = self.pid_loop_var.get()
            p = self._read_float_entry(self.pid_p_entry, "P")
            i = self._read_float_entry(self.pid_i_entry, "I")
            d = self._read_float_entry(self.pid_d_entry, "D")
        except ValueError as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
            return

        self._safe_command(
            f"Set PID (Loop {loop}): P={p}, I={i} s, D={d}",
            self.backend.set_pid, loop, p, i, d)

    def _read_pid(self):
        """Read current PID gain values from the instrument."""
        if not self._require_connection():
            return
        try:
            loop = self.pid_loop_var.get()
            p, i, d = self.backend.get_pid(loop)
            self.log(f"Read PID (Loop {loop}): P={p}, I={i} s, D={d}")
            for entry, value in (
                    (self.pid_p_entry, p),
                    (self.pid_i_entry, i),
                    (self.pid_d_entry, d)):
                entry.delete(0, 'end')
                entry.insert(0, str(value))
        except Exception as e:
            self.log(f"ERROR reading PID: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # SETPOINT HANDLERS
    # -----------------------------------------------------------------------

    def _toggle_ramp_fields(self):
        """Enable/disable ramp rate entry based on checkbox."""
        if self.ramp_enabled_var.get():
            self.ramp_rate_entry.config(state='normal')
        else:
            self.ramp_rate_entry.config(state='disabled')

    def _send_setpoint(self):
        """Send the setpoint, with or without a ramp."""
        try:
            loop = self.setp_loop_var.get()
            setpoint = self._read_float_entry(
                self.setp_entry, "Setpoint")
            if self.ramp_enabled_var.get():
                rate = self._read_float_entry(
                    self.ramp_rate_entry, "Ramp rate")
            else:
                rate = None
        except ValueError as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
            return

        if rate is None:
            self._safe_command(
                f"Set setpoint (Loop {loop}): {setpoint} K, no ramp",
                self.backend.set_setpoint_immediate, loop, setpoint)
        else:
            self._safe_command(
                f"Set setpoint (Loop {loop}): {setpoint} K "
                f"ramping at {rate} K/min",
                self.backend.set_setpoint_with_ramp,
                loop, setpoint, rate)

    def _read_setpoint(self):
        """Read the setpoint, ramp rate and ramp state."""
        if not self._require_connection():
            return
        try:
            loop = self.setp_loop_var.get()
            setpoint = self.backend.get_setpoint(loop)
            rate = self.backend.get_rate(loop)
            ramping = self.backend.get_ramp_status(loop)
            self.log(
                f"Read Loop {loop}: setpoint={setpoint}, "
                f"rate={rate} /min, ramping={ramping}")
            self.setp_entry.delete(0, 'end')
            self.setp_entry.insert(0, str(setpoint))
            was_disabled = str(self.ramp_rate_entry['state']) == 'disabled'
            if was_disabled:
                self.ramp_rate_entry.config(state='normal')
            self.ramp_rate_entry.delete(0, 'end')
            self.ramp_rate_entry.insert(0, str(rate))
            if was_disabled:
                self.ramp_rate_entry.config(state='disabled')
        except Exception as e:
            self.log(f"ERROR reading setpoint: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # HEATER RANGE / LOAD HANDLERS
    # -----------------------------------------------------------------------

    def _send_range(self):
        """Send the Loop 1 heater range."""
        range_value = self.range_var.get()
        self._safe_command(
            f"Set Loop 1 heater range to {range_value}",
            self.backend.set_range, range_value)

    def _read_range(self):
        """Read the Loop 1 heater range."""
        if not self._require_connection():
            return
        try:
            value = self.backend.get_range()
            self.log(f"Read Loop 1 heater range: {value}")
        except Exception as e:
            self.log(f"ERROR reading heater range: {e}")
            messagebox.showerror("Read Failed", str(e))

    def _send_load(self):
        """Send the Loop 1 heater load resistance."""
        load = self.load_var.get()
        self._safe_command(
            f"Set Loop 1 heater load to {load} ohm",
            self.backend.set_load, load)

    def _read_load(self):
        """Read the Loop 1 heater load resistance."""
        if not self._require_connection():
            return
        try:
            value = self.backend.get_load()
            self.log(f"Read Loop 1 heater load: {value} ohm")
        except Exception as e:
            self.log(f"ERROR reading heater load: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # MANUAL POWER HANDLERS
    # -----------------------------------------------------------------------

    def _send_pmanual(self):
        """Send the manual output power."""
        try:
            loop = self.mout_loop_var.get()
            percent = self._read_float_entry(
                self.mout_entry, "Manual power")
        except ValueError as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
            return

        self._safe_command(
            f"Set manual power (Loop {loop}): {percent} %",
            self.backend.set_manual_power, loop, percent)

    def _read_pmanual(self):
        """Read the manual output power."""
        if not self._require_connection():
            return
        try:
            loop = self.mout_loop_var.get()
            percent = self.backend.get_manual_power(loop)
            self.log(f"Read manual power (Loop {loop}): {percent} %")
            self.mout_entry.delete(0, 'end')
            self.mout_entry.insert(0, str(percent))
        except Exception as e:
            self.log(f"ERROR reading manual power: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # OUTPUT LIMIT HANDLERS
    # -----------------------------------------------------------------------

    def _send_limits(self):
        """Send the maximum power and maximum setpoint."""
        try:
            loop = self.limits_loop_var.get()
            max_pwr = self._read_float_entry(
                self.maxpwr_entry, "Max power")
            max_set = self._read_float_entry(
                self.maxset_entry, "Max setpoint")
        except ValueError as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
            return

        if self._safe_command(
                f"Set max power (Loop {loop}): {max_pwr} %",
                self.backend.set_max_power, loop, max_pwr) is None:
            return
        self._safe_command(
            f"Set max setpoint (Loop {loop}): {max_set} K",
            self.backend.set_max_setpoint, loop, max_set)

    def _read_limits(self):
        """Read the maximum power and maximum setpoint."""
        if not self._require_connection():
            return
        try:
            loop = self.limits_loop_var.get()
            max_pwr = self.backend.get_max_power(loop)
            max_set = self.backend.get_max_setpoint(loop)
            self.log(
                f"Read Loop {loop}: max power={max_pwr} %, "
                f"max setpoint={max_set}")
            self.maxpwr_entry.delete(0, 'end')
            self.maxpwr_entry.insert(0, str(max_pwr))
            self.maxset_entry.delete(0, 'end')
            self.maxset_entry.insert(0, str(max_set))
        except Exception as e:
            self.log(f"ERROR reading output limits: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # INPUT CONFIGURATION HANDLERS
    # -----------------------------------------------------------------------

    def _refresh_channel_units(self):
        """Cache each input channel's display units."""
        for ch in Cryocon34Backend.INPUT_CHANNELS:
            try:
                self.channel_units[ch] = self.backend.get_units(ch)
            except Exception:
                self.channel_units[ch] = ''

    def _send_units(self):
        """Send the display units for the selected input."""
        channel = self.incfg_ch_var.get()
        units = self.incfg_units_var.get()
        if self._safe_command(
                f"Set input {channel} display units to {units}",
                self.backend.set_units, channel, units) is not None:
            self._refresh_channel_units()

    def _send_sensor_index(self):
        """Send the sensor calibration curve index."""
        channel = self.incfg_ch_var.get()
        try:
            index = int(float(self.incfg_index_entry.get()))
        except ValueError:
            self.log("ERROR: Sensor index must be an integer.")
            messagebox.showerror(
                "Invalid Input", "Sensor index must be an integer.")
            return

        if self.incfg_curve_src_var.get().startswith('User'):
            self._safe_command(
                f"Set input {channel} user sensor index to {index}",
                self.backend.set_user_sensor_index, channel, index)
        else:
            self._safe_command(
                f"Set input {channel} factory sensor index to {index}",
                self.backend.set_factory_sensor_index, channel, index)

    def _send_input_name(self):
        """Send a custom name for the selected input."""
        channel = self.incfg_ch_var.get()
        name = self.incfg_name_entry.get().strip()
        if not name:
            self.log("ERROR: Channel name is empty.")
            messagebox.showerror(
                "Invalid Input", "Enter a channel name first.")
            return
        if len(name) > 15:
            self.log("ERROR: Channel name exceeds 15 characters.")
            messagebox.showerror(
                "Invalid Input",
                "Channel name must be 15 characters or fewer.")
            return
        self._safe_command(
            f"Set input {channel} name to \"{name}\"",
            self.backend.set_input_name, channel, name)

    def _read_input_config(self):
        """Read units, sensor indices and name for the selected input."""
        if not self._require_connection():
            return
        try:
            channel = self.incfg_ch_var.get()
            units = self.backend.get_units(channel)
            isenix = self.backend.get_factory_sensor_index(channel)
            usenix = self.backend.get_user_sensor_index(channel)
            name = self.backend.get_input_name(channel)
            self.log(
                f"Read input {channel}: units={units}, "
                f"ISENIX={isenix}, USENIX={usenix}, name={name}")
            self.channel_units[channel] = units
            self.incfg_name_entry.delete(0, 'end')
            self.incfg_name_entry.insert(0, name.strip('"'))
        except Exception as e:
            self.log(f"ERROR reading input config: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # ALARM HANDLERS
    # -----------------------------------------------------------------------

    def _send_alarm(self):
        """Send the alarm setpoints and enables for the selected input."""
        try:
            channel = self.alarm_ch_var.get()
            high = self._read_float_entry(
                self.alarm_high_entry, "High alarm")
            low = self._read_float_entry(
                self.alarm_low_entry, "Low alarm")
        except ValueError as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
            return

        if low > high:
            self.log("ERROR: Low alarm is above the high alarm.")
            messagebox.showerror(
                "Invalid Input",
                "The low alarm setpoint must not exceed the high one.")
            return

        self._safe_command(
            f"Set input {channel} alarms: high={high}, low={low}, "
            f"high_en={self.alarm_high_en_var.get()}, "
            f"low_en={self.alarm_low_en_var.get()}",
            self.backend.set_alarm, channel, high, low,
            self.alarm_high_en_var.get(),
            self.alarm_low_en_var.get())

    def _read_alarm(self):
        """Read the alarm setpoints and enables for the selected input."""
        if not self._require_connection():
            return
        try:
            channel = self.alarm_ch_var.get()
            high, low, hiena, loena = self.backend.get_alarm(channel)
            self.log(
                f"Read input {channel} alarms: high={high}, low={low}, "
                f"high_enable={hiena}, low_enable={loena}")
            self.alarm_high_entry.delete(0, 'end')
            self.alarm_high_entry.insert(0, high)
            self.alarm_low_entry.delete(0, 'end')
            self.alarm_low_entry.insert(0, low)
            self.alarm_high_en_var.set(hiena.upper().startswith('Y'))
            self.alarm_low_en_var.set(loena.upper().startswith('Y'))
        except Exception as e:
            self.log(f"ERROR reading alarms: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # OVER-TEMPERATURE DISCONNECT HANDLERS
    # -----------------------------------------------------------------------

    def _send_overtemp(self):
        """Send the over-temperature disconnect configuration."""
        try:
            channel = self.otd_ch_var.get()
            temperature = self._read_float_entry(
                self.otd_temp_entry, "Over-temperature limit")
        except ValueError as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
            return

        enable = self.otd_enable_var.get()
        if not enable and not messagebox.askyesno(
                "Disable Over-Temperature Protection",
                "The 'Enable over-temperature disconnect' box is "
                "unticked, so sending this will DISABLE the "
                "protective heater cut-out.\n\nSend anyway?"):
            self.log("OVERTEMP send cancelled.")
            return

        self._safe_command(
            f"Set over-temperature disconnect: source={channel}, "
            f"limit={temperature} K, enable={enable}",
            self.backend.set_overtemp, enable, channel, temperature)

    def _read_overtemp(self):
        """Read the over-temperature disconnect configuration."""
        if not self._require_connection():
            return
        try:
            enable, source, temp = self.backend.get_overtemp()
            self.log(
                f"Read over-temperature disconnect: enable={enable}, "
                f"source={source}, limit={temp}")
            self.otd_temp_entry.delete(0, 'end')
            self.otd_temp_entry.insert(0, temp)
            self.otd_enable_var.set(
                enable.upper().startswith(('Y', 'O')) and
                enable.upper() != 'OFF')
        except Exception as e:
            self.log(f"ERROR reading over-temperature disconnect: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # ADVANCED / SYSTEM HANDLERS
    # -----------------------------------------------------------------------

    def _send_lockout(self):
        """Send the front-panel keypad lockout setting."""
        enable = self.lockout_var.get() == 'On'
        self._safe_command(
            f"Set keypad lockout {'ON' if enable else 'OFF'}",
            self.backend.set_lockout, enable)

    def _send_display_settings(self):
        """Send the display resolution and filter time constant."""
        resolution = self.dres_var.get()
        tc = self.distc_var.get()
        if self._safe_command(
                f"Set display resolution to {resolution}",
                self.backend.set_display_resolution,
                resolution) is None:
            return
        self._safe_command(
            f"Set display filter time constant to {tc} s",
            self.backend.set_display_time_constant, tc)

    def _read_errors(self):
        """Read the instrument error queue."""
        if not self._require_connection():
            return
        try:
            errors = self.backend.get_error_queue()
            self.log(f"Error queue: {errors}")
        except Exception as e:
            self.log(f"ERROR reading error queue: {e}")
            messagebox.showerror("Read Failed", str(e))

    def _save_to_flash(self):
        """Persist the current configuration to flash memory."""
        if not self._require_connection():
            return
        if not messagebox.askyesno(
                "Save to Flash",
                "SYSTEM:NVSAVE writes the CURRENT instrument "
                "configuration to flash, so it becomes the power-up "
                "state.\n\nSave now?"):
            self.log("NVSAVE cancelled.")
            return
        self._safe_command(
            "Save configuration to flash (SYSTEM:NVSAVE)",
            self.backend.save_to_flash)

    def _factory_reset(self):
        """Send *RST after two confirmations."""
        if not self._require_connection():
            return
        if not messagebox.askyesno(
                "Hardware Reset",
                "*RST performs a HARDWARE reset of the Cryocon.\n\n"
                "The instrument stops responding on every remote "
                "interface for about 15 seconds and comes back at its "
                "last power-up settings. Any running control is "
                "interrupted.\n\nContinue?"):
            self.log("Hardware reset cancelled.")
            return
        if not messagebox.askyesno(
                "Confirm Hardware Reset",
                "Are you sure? Nothing else should be relying on this "
                "controller right now."):
            self.log("Hardware reset cancelled.")
            return

        self._stop_polling()
        self._safe_command(
            "HARDWARE RESET (*RST)",
            self.backend.reset)
        self.log(
            "Reset sent. The instrument is unreachable for about 15 s; "
            "restart polling once it answers again.")

    # -----------------------------------------------------------------------
    # POLLING / LIVE STATUS
    # -----------------------------------------------------------------------

    def _toggle_polling(self):
        """Start or stop the live status polling."""
        if self.polling_active:
            self._stop_polling()
        else:
            self._start_polling()

    def _start_polling(self):
        """Start the 1-second polling loop."""
        if not self._require_connection():
            return
        self.polling_active = True
        self._poll_stage = 0
        self._poll_notes = {}
        self.poll_btn.config(text="Stop Polling")
        self.log(f"Live status polling started ({self.POLL_STAGE_COUNT} "
                 f"groups at {self.POLL_STAGE_MS} ms; full refresh every "
                 f"{self.POLL_STAGE_COUNT * self.POLL_STAGE_MS / 1000:.1f} s).")
        self._poll_loop()

    def _stop_polling(self):
        """Stop the polling loop."""
        self.polling_active = False
        self.poll_btn.config(text="Start Polling")
        if self.is_connected:
            self.log("Live status polling stopped.")

    def _poll_loop(self):
        """Refresh one group of status fields, then schedule the next.

        The original version asked for everything at once: four channel
        temperatures, four alarms, two output powers, two heater read-backs,
        the control status, two setpoints, two PID triplets, two loop types
        and sources, two ramp states and rates, the range and load, the
        over-temperature settings and the lockout. That is more than thirty
        queries fired back to back on the Tk main thread once a second. It
        froze the window and it is the traffic pattern that left this
        firmware revision refusing the next command. Each tick now refreshes
        one group and the panel cycles through them.
        """
        if not self.polling_active or not self.is_connected:
            return
        stage = getattr(self, '_poll_stage', 0) % self.POLL_STAGE_COUNT
        self._poll_stage = (stage + 1) % self.POLL_STAGE_COUNT
        try:
            self._poll_stage_dispatch(stage)
        except Exception as e:
            self.log(f"Polling error: {e}")
        if self.polling_active:
            self.root.after(self.POLL_STAGE_MS, self._poll_loop)

    # -- polling stages -----------------------------------------------------

    POLL_STAGE_MS = 400        # gap between groups
    POLL_STAGE_COUNT = 7       # so a full refresh takes about 2.8 s

    def _poll_stage_dispatch(self, stage):
        if stage == 0:
            self._poll_temperatures()
        elif stage == 1:
            self._poll_power()
        elif stage == 2:
            self._poll_control_and_setpoints()
        elif stage == 3:
            self._poll_pid()
        elif stage == 4:
            self._poll_type_and_source()
        elif stage == 5:
            self._poll_ramp()
        else:
            self._poll_range_and_safety()

    def _poll_temperatures(self):
        if True:
            # Temperatures and alarm flags (all channels). The reading is
            # in each channel's own display units, so the units are shown
            # alongside rather than assumed to be Kelvin.
            for ch in Cryocon34Backend.INPUT_CHANNELS:
                try:
                    temp = self.backend.get_temperature(ch)
                    self.status_labels[f'temp_{ch}'].config(
                        text=f"{temp:.3f} {self.channel_units[ch]}".strip())
                except CryoconStatusError as e:
                    # Name the condition instead of showing a bare 'Error':
                    # an unused channel reads as a sensor fault, and that is
                    # worth seeing on the panel.
                    self.status_labels[f'temp_{ch}'].config(text="no sensor")
                    self._poll_note(f'temp_{ch}', str(e))
                except Exception as e:
                    self.status_labels[f'temp_{ch}'].config(
                        text="Error")
                    self._poll_note(f'temp_{ch}', f"{type(e).__name__}: {e}")
                try:
                    self.status_labels[f'alarm_{ch}'].config(
                        text=self.backend.get_alarm_status(ch))
                except Exception:
                    pass

    def _poll_power(self):
        if True:
            # Loop output power and heater read-back
            for loop in Cryocon34Backend.LOOPS:
                try:
                    pwr = self.backend.get_output_power(loop)
                    self.status_labels[f'outpwr_{loop}'].config(
                        text=f"{pwr:.1f} %")
                except Exception:
                    self.status_labels[f'outpwr_{loop}'].config(
                        text="Error")
                try:
                    self.status_labels[f'htrread_{loop}'].config(
                        text=self.backend.get_heater_readback(loop))
                except Exception:
                    pass

    def _poll_control_and_setpoints(self):
        if True:
            # Control engage status
            try:
                self.status_labels['control_status'].config(
                    text=self.backend.get_control_status())
            except Exception:
                pass

            # Setpoints
            for loop in Cryocon34Backend.LOOPS:
                try:
                    sp = self.backend.get_setpoint(loop)
                    self.status_labels[f'setpoint_{loop}'].config(
                        text=f"{sp:.3f}")
                except Exception:
                    pass

    def _poll_pid(self):
        if True:
            # PID gains
            for loop in Cryocon34Backend.LOOPS:
                try:
                    p, i, d = self.backend.get_pid(loop)
                    self.status_labels[f'pid_{loop}'].config(
                        text=f"P={p}, I={i} s, D={d}")
                except Exception:
                    pass

    def _poll_type_and_source(self):
        if True:
            # Control type and source channel
            for loop in Cryocon34Backend.LOOPS:
                try:
                    ctype = self.backend.get_loop_type(loop)
                    source = self.backend.get_loop_source(loop)
                    self.status_labels[f'type_{loop}'].config(
                        text=f"{ctype} / {source}")
                except Exception:
                    pass

    def _poll_ramp(self):
        if True:
            # Ramp state and rate
            for loop in Cryocon34Backend.LOOPS:
                try:
                    state = self.backend.get_ramp_status(loop)
                    rate = self.backend.get_rate(loop)
                    self.status_labels[f'ramp_{loop}'].config(
                        text=f"{state}, {rate} /min")
                except Exception:
                    pass

    def _poll_range_and_safety(self):
        if True:
            # Loop 1 heater range and load
            try:
                self.status_labels['range_1'].config(
                    text=f"{self.backend.get_range()} / "
                         f"{self.backend.get_load()} ohm")
            except Exception:
                pass

            # Over-temperature disconnect
            try:
                enable, source, temp = self.backend.get_overtemp()
                self.status_labels['overtemp'].config(
                    text=f"{enable}, {source}, {temp}")
            except Exception:
                pass

            # Keypad lockout
            try:
                self.status_labels['lockout'].config(
                    text=self.backend.get_lockout())
            except Exception:
                pass

    def _poll_note(self, key, message):
        """Log a polling problem once per field, not once per second."""
        if getattr(self, '_poll_notes', None) is None:
            self._poll_notes = {}
        if self._poll_notes.get(key) != message:
            self._poll_notes[key] = message
            self.log(f"  Poll: {message}")

    # -----------------------------------------------------------------------
    # WINDOW CLOSE
    # -----------------------------------------------------------------------

    def _on_closing(self):
        """Handle window close event."""
        if self.is_connected:
            if messagebox.askyesno(
                    "Exit",
                    "You are still connected to the instrument.\n\n"
                    "Disconnecting is non-destructive - the "
                    "instrument will retain all settings and "
                    "continue controlling.\n\n"
                    "Disconnect and exit?"):
                self._stop_polling()
                self.backend.disconnect()
                self.root.destroy()
        else:
            self.root.destroy()


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if not pyvisa:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\n"
            "Please run:\n"
            "  pip install pyvisa pyvisa-py\n\n"
            "For NI-VISA backend, install NI-VISA runtime\n"
            "from national instruments.")
        root.destroy()
    else:
        root = tk.Tk()
        app = DirectControlGUI(root)
        root.mainloop()
