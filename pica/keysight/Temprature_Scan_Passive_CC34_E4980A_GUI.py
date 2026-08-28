"""
Module:             Temprature_Scan_Passive_CC34_E4980A_GUI.py
Purpose:             GUI module for Temperature-Dependent Dielectric
                     Measurement (Keysight E4980A + Cryocon Model 34).
Original Authors:    Prathamesh Deshmukh (template programs)
Integrated by:       AI-assisted merge per design specification
Version:             V: 1.5  (v1.3 multi-day hardening: 400 K kill
                     switch, retry-forever comm recovery, fsync-per-point
                     writes, timestamped T-log, bounded console, optional
                     plot thinning, Windows keep-awake;
                     v1.4 unattended policy: the safety-kill and
                     runtime-error handlers no longer open modal dialogs
                     - console log + beeps only, so an unattended
                     overnight run never ends behind a messagebox nobody
                     is there to click. The user-clicked Stop info box is
                     kept: that action is attended by definition;
                     v1.5, 28 Aug 2026: Cryocon Model 34 in place of the
                     Lakeshore 350. Keep Temprature_Scan_Passive_E4980A_GUI.py
                     as it is for Lakeshore work; this file is the Cryocon
                     sibling, not a replacement.)

Differences from the Lakeshore 350 version, all forced by the instrument:

  1. The Cryocon is READ ONLY here. The Lakeshore version offers a
     "Set Range to Zero" checkbox and forces RANGE 1,0 at 400 K. Neither
     is possible without writing to a controller that something else may
     be driving, so this module writes nothing at all and the 400 K limit
     stops the measurement instead of touching the heater. There is no
     write path in the file to call by accident. Ask if you want the STOP
     write back; see the SAFETY note below.

  2. The sensor channel is chosen in the GUI. A Model 34 has four inputs
     and INPUT? reports in each channel's own display units, so the
     channel is picked explicitly and checked for Kelvin at Start.

  3. The link is opened with a settle delay and a retried first '*IDN?',
     and every operation is paced. On a Rev 3.03A unit the first command
     after a bus scan could otherwise time out inside viWrite.

  4. A worker-thread exception now travels with its own formatted
     traceback. The previous code called traceback.format_exc() on the
     GUI thread, where no exception is live, and printed 'NoneType: None'
     instead of the real fault.
"""

# ===============================================================================
# IMPORTS  (union of both source programs)
# ===============================================================================

import tkinter as tk
from tkinter import (
    ttk, Label, Entry, LabelFrame, filedialog, messagebox,
    scrolledtext, Canvas,
)
import threading
import queue
import os
import re
import time
import ctypes
import math
import platform
import traceback
import atexit
from datetime import datetime
from collections import deque
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- winsound for unattended-run alerts (Windows; optional) ---
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:
    PIL_AVAILABLE = False

# --- PyVISA for instrument communication ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# UTILITY LAUNCH FUNCTIONS  (verbatim from source programs)
# ===============================================================================

def run_script_process(script_path):
    """Wrapper to execute a script using runpy in its own directory."""
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
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


# ===============================================================================
# CRYOCON MODEL 34 CONSTANTS
# ===============================================================================

# A Cryo-con replies to *IDN? with something like
# "Cryocon Model 34, Rev 3.03A". Both spellings of the maker name are in
# circulation, so both are accepted.
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON")

# Input channels on a Model 34.
CRYOCON_INPUT_CHANNELS = ('A', 'B', 'C', 'D')

# Control loops. Loop 1 is the primary heater; its output power is read
# (never set) so the T-log keeps the heater column the Lakeshore file has.
CRYOCON_HEATER_LOOP = '1'

# Factory address, used only as a last-resort hint. Identification is by
# *IDN? content, so a re-addressed Cryocon is still found.
CRYOCON_ADDRESS_HINT = "GPIB1::12"

# --- SAFETY: what happens at the 400 K limit -----------------------------
# The Lakeshore version forces RANGE 1,0 and keeps going. The equivalent
# here would be writing STOP, which disengages BOTH Cryocon control loops
# and drops both heaters, on an instrument this module does not own and may
# not be the only client of.
#
# This module writes nothing, and there is no write path in it to call by
# mistake: CryoconLink here has no write() method at all. At 400 K the run
# stops, the console says so loudly, the beeper sounds, and every row is
# already fsync'd to disk. Whatever is driving the cryostat keeps doing what
# it was doing, which is the honest outcome when a read-only monitor decides
# it does not like the temperature.
#
# If you want the limit to disengage the loops instead, say so and it goes
# back: it needs a write() on CryoconLink and one 'STOP' in check_safety_kill.


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

    # There is deliberately no write() on this class. This module is a
    # passive monitor, and a write path that exists is a write path that can
    # be called by mistake. The direct-control module carries its own.

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


# ===============================================================================
# BACKEND: CRYOCON MODEL 34  (read only)
# ===============================================================================

class Cryocon34_Backend:
    """Passive temperature source for the dielectric scan.

    Every method is a query. No *RST (on a Cryocon that is a ~15 s hardware
    reset to power-up defaults), no CONTROL, no STOP, no loop, heater,
    range or setpoint command. Whatever is driving the cryostat is
    untouched from Start to Stop.
    """

    def __init__(self, visa_address, channel='A', log=None):
        self.channel = (str(channel).strip().upper() or 'A')
        self.log = log if callable(log) else (lambda msg: print(msg))
        self.link = CryoconLink(visa_address, log=self.log)
        self.idn = self.link.idn
        if not is_cryocon_idn(self.idn):
            self.link.close()
            raise ConnectionError(
                f"{visa_address} is not a Cryo-con: it identifies itself as "
                f"'{self.idn}'. Its reply to INPUT? would be logged as a "
                "sample temperature, so this refuses to continue. Scan the "
                "bus and pick the Cryocon's actual address (it does not have "
                f"to be {CRYOCON_ADDRESS_HINT}).")
        self.log(f"Cryocon connected: {self.idn}")

    def verify_channel(self):
        """Confirm the chosen channel reads Kelvin and has a live sensor.

        Both checks run at Start so a wrong or empty channel is named here,
        by channel letter, rather than killing the worker thread after the
        first sweep. INPUT? reports in the channel's own display units, so a
        channel left in C or F would silently log wrong numbers.
        """
        ch = self.channel
        units = self.link.query(f'INPUT {ch}:UNITS?').upper()
        if not units.startswith('K'):
            raise ValueError(
                f"Cryocon channel {ch} is reporting in '{units}', not "
                "Kelvin. Either set that channel to K on the Cryocon front "
                "panel (this program never writes to it), or pick the "
                "channel your sample sensor is actually on.")
        value = self.get_temperature()
        self.log(f"  Cryocon channel {ch}: {value:.4f} K, units K. "
                 "Heater and loop state unchanged.")
        return value

    def get_temperature(self, sensor=None):
        ch = (str(sensor).strip().upper() if sensor else self.channel)
        raw = self.link.query(f'INPUT? {ch}')
        return parse_cryocon_number(raw, 'temperature reading', ch)

    def get_heater_output(self, loop=CRYOCON_HEATER_LOOP):
        """Loop output power as a percentage of full scale. Query only.

        Kept so the T-log keeps its Heater_pct column. Returns nan rather
        than raising: a heater number that cannot be read is not a reason to
        interrupt a dielectric measurement.
        """
        try:
            raw = self.link.query(f'LOOP {loop}:OUTPWR?')
            return parse_cryocon_number(raw.rstrip('%'), 'LOOP OUTPWR')
        except Exception:
            return float('nan')

    def reconnect(self):
        self.link.reconnect()

    def close(self):
        """Close the session only. Nothing is written on the way out."""
        if self.link:
            try:
                self.link.close()
            except Exception as e:
                print(f"Warning: Issue during Cryocon shutdown: {e}")


# ===============================================================================
# BACKEND: KEYSIGHT E4980A LCR  (verbatim from freq-scan program)
# ===============================================================================

class LCR_Backend:
    """Handles all SCPI communication with the Keysight E4980A."""

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.has_opt001 = False
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    def _check_errors(self, context=""):
        """Drain SCPI error queue; raise on any error."""
        errors = []
        for _ in range(20):
            err = self.instrument.query(":SYST:ERR?").strip()
            if err.startswith("0,") or err.startswith("+0,"):
                break
            errors.append(err)
        if errors:
            raise RuntimeError(f"SCPI errors after {context}: {errors}")

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        """Safely ramps the DC bias to the target voltage."""
        current_v = float(self.instrument.query(":BIAS:VOLT?"))
        if abs(target_v - current_v) < 0.01:
            return
        if step <= 0:
            self.instrument.write(f":BIAS:VOLT {target_v:.3f}")
            return
        direction = 1 if target_v > current_v else -1
        ramp_points = np.arange(current_v, target_v, direction * step)
        ramp_points = np.append(ramp_points, target_v)
        for v in ramp_points:
            self.instrument.write(f":BIAS:VOLT {v:.3f}")
            time.sleep(dwell)

    def initialize_instrument(self, p):
        """Configures the instrument for a multi-frequency R-X measurement."""
        print("\n--- [Backend] Initializing Keysight E4980A ---")
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        inst = self.rm.open_resource(p["lcr_visa"])
        # 15 s: LONG-aperture point time is < 1 s, so this is generous
        # while letting the retry-forever loop detect a hung bus quickly.
        inst.timeout = 15000
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self.instrument = inst

        idn = inst.query("*IDN?").strip()
        if "E4980" not in idn:
            inst.close()
            raise ConnectionError(f"Not an E4980A: {idn}")

        self.has_opt001 = "001" in inst.query("*OPT?")

        # Strict Safety Ceilings: Hard cap at 2.0 V regardless of options
        v_bias_max = min(2.0, 40.0 if self.has_opt001 else 2.0)
        v_ac_max   = min(2.0, 20.0 if self.has_opt001 else 2.0)

        if abs(p["dc_bias"]) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V safety limit.")
        if not (0 < p["ac_bias"] <= v_ac_max):
            raise ValueError(
                f"AC level outside 0-{v_ac_max} Vrms safety limit.")

        # --- Instrument configuration ---
        inst.write("*RST; *CLS")
        time.sleep(1.0)
        inst.write(":DISP:ENAB ON")
        time.sleep(0.2)

        inst.write(":FUNC:IMP RX")
        inst.write(f":APER {p['aper']}")
        inst.write(":FUNC:IMP:RANG:AUTO ON")
        time.sleep(0.2)

        inst.write(":FORM ASC")

        inst.write(":FUNC:SMON:VAC ON")
        inst.write(":FUNC:SMON:IAC ON")
        inst.write(":FUNC:SMON:VDC OFF")
        inst.write(":FUNC:SMON:IDC OFF")
        time.sleep(0.2)

        if p["alc_enabled"]:
            inst.write(":AMPL:ALC ON")
        else:
            inst.write(":AMPL:ALC OFF")
        time.sleep(0.2)

        inst.write(f":CORR:LENG {p['cable_len']}")
        if p["corr_enabled"]:
            inst.write(":CORR:OPEN:STAT ON")
            inst.write(":CORR:SHOR:STAT ON")
        else:
            inst.write(":CORR:OPEN:STAT OFF")
            inst.write(":CORR:SHOR:STAT OFF")
        time.sleep(0.2)

        inst.write(f":VOLT {p['ac_bias']}")
        time.sleep(0.5)

        inst.write(":TRIG:SOUR BUS")
        inst.write(":INIT:CONT ON")
        time.sleep(0.2)

        # --- Conditional DC Bias Handling ---
        if abs(p["dc_bias"]) < 1e-9:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT OFF")
        else:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT ON")
            time.sleep(0.5)
            if self.has_opt001:
                print(f"  Ramping DC Bias to {p['dc_bias']} V...")
                self.safe_ramp_dc_bias(p["dc_bias"])
            else:
                if p["dc_bias"] not in (1.5, 2.0):
                    raise ValueError(
                        "Without Option 001, DC bias must be 0, 1.5 or 2 V.")
                inst.write(f":BIAS:VOLT {p['dc_bias']}")
                time.sleep(1.0)

        self._check_errors("configuration")
        print(f"  Connected & configured (RX mode): {idn}")

    def perform_measurement(self, freq, delay):
        """Set frequency, settle, trigger one measurement, fetch R, X, status."""
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")
        self.instrument.write(f":FREQ {freq}")
        time.sleep(delay)
        self.instrument.write(":TRIG:IMM")
        self.instrument.query("*OPC?")  # Wait for operation complete
        vals = self.instrument.query_ascii_values(":FETC?")
        R, X = vals[0], vals[1]
        status = int(vals[2]) if len(vals) > 2 else 0
        return R, X, status

    def close_instrument(self):
        print("--- [Backend] Closing LCR instrument connection. ---")
        if not self.instrument:
            return
        try:
            if self.has_opt001:
                print("  Ramping bias to zero and turning off...")
                self.safe_ramp_dc_bias(0.0)
            else:
                self.instrument.write(":BIAS:VOLT 0")
                time.sleep(0.5)
            self.instrument.write(":BIAS:STAT OFF")
            self.instrument.write(":DISP:PAGE MEAS")
            time.sleep(0.2)
        except Exception as e:
            print(f"  Warning during LCR shutdown: {e}")
        finally:
            try:
                self.instrument.close()
                print("  E4980A connection closed.")
            finally:
                self.instrument = None


# ===============================================================================
# BACKEND: COMBINED  (Appendix A.1 — per-point temperature binding)
#                  +  PASSIVE init + hardcoded 400 K safety kill switch
# ===============================================================================

class Combined_Backend:
    """Manages the Cryocon Model 34 (read-only) and the Keysight E4980A."""

    # HARDCODED SAFETY KILL SWITCH — do not expose in UI
    SAFETY_KILL_TEMP_K = 400.0

    def __init__(self, log=None):
        self.cryocon = None
        self.lcr = LCR_Backend()
        self.params = {}
        self.log = log if callable(log) else (lambda msg: print(msg))

    def initialize_instruments(self, parameters):
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments (PASSIVE mode) ---")
        self.cryocon = Cryocon34_Backend(
            parameters['cryocon_visa'],
            channel=parameters['channel'],
            log=self.log)

        # NOTE: nothing is written to the Cryocon here or anywhere else.
        # No *RST (a Cryocon *RST is a ~15 s hardware reset), no CONTROL,
        # no STOP, no loop, heater or setpoint command.
        self.cryocon.verify_channel()

        self.lcr.initialize_instrument(parameters)

    def check_safety_kill(self, temperature_k):
        """Hardcoded limit at 400 K. Read-only by default.

        The Lakeshore sibling forces RANGE 1,0 here. This one stops the
        measurement and says so; see ALLOW_EMERGENCY_STOP_WRITE at the top
        of the file for why, and for how to change it.
        """
        if temperature_k >= self.SAFETY_KILL_TEMP_K:
            print(f"!!! SAFETY LIMIT: T={temperature_k:.3f} K.")
            print("    Measurement stopped. Nothing written to the "
                  "Cryocon: it is read-only in this module.")
            return True
        return False

    def measure_frequency_sweep(self, frequencies, delay, stop_event=None):
        """
        Appendix A.1 — CRITICAL:
        Reads the Cryocon temperature INSIDE the frequency loop so every
        single data point is bound to the exact temperature at which it
        was measured.  No cycle-average temperature is computed.
        """
        htr = self.cryocon.get_heater_output()
        points = []
        for f in frequencies:
            if stop_event is not None and stop_event.is_set():
                break                     # abort mid-sweep, cleanly
            temp = self.cryocon.get_temperature()   # T for THIS point
            if temp >= self.SAFETY_KILL_TEMP_K:
                self.check_safety_kill(temp)
                points.append((temp, f, float('nan'), float('nan'), -1))
                break
            R, X, status = self.lcr.perform_measurement(f, delay)
            points.append((temp, f, R, X, status))
        return {'heater': htr, 'points': points,
                'wall_time': datetime.now()}

    def reconnect(self):
        """Close both sessions and re-initialize from the stored params.
        Used by the worker's retry-forever loop after a comm failure;
        re-init restores the full LCR configuration (RX mode, aperture,
        ALC, corrections, bias re-ramp) even after an instrument
        power-cycle."""
        try:
            self.close_instruments()
        except Exception as e:
            print(f"  Pre-reconnect cleanup warning: {e}")
        self.initialize_instruments(self.params)

    def close_instruments(self):
        print("\n--- [Backend] Closing all instrument connections. ---")
        try:
            self.lcr.close_instrument()
        finally:
            if self.cryocon:
                self.cryocon.close()


# ===============================================================================
# FRONT END (GUI)
# ===============================================================================

class Integrated_CT_GUI:
    """
    Main GUI application for Temperature-Dependent Dielectric Measurement.
    Combines Cryocon Model 34 temperature sensing with E4980A
    multi-frequency LCR measurement.  The Cryocon is a PASSIVE temperature
    sensor and nothing is ever written to it; the heater is never ramped or
    setpoint-driven from this GUI.  A hardcoded 400 K limit stops the run.
    Built for unattended multi-day runs: comm errors auto-reconnect
    forever, every data row is fsync'd to disk immediately, and heating/
    cooling direction is never assumed — whatever temperature profile is
    thrown at it is recorded as-is.
    """

    PROGRAM_VERSION = "1.4"
    LOGO_SIZE = 110
    CONSOLE_MAX_LINES = 2000   # bound console growth on multi-day runs
    PLOT_MAX_POINTS = 10000    # halve plot buffers at this size (if enabled)

    # SetThreadExecutionState flags (Windows keep-awake during a run)
    ES_CONTINUOUS      = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    # --- Default frequency list (Section 4 of original instructions) ---
    DEFAULT_FREQS = (
        "1000, 2000, 3000, 5000, 7000, 10000, 25000, 50000, "
        "70000, 90000, 100000, 120000, 150000, 170000, 200000, "
        "250000, 500000, 1000000, 1500000, 2000000"
    )

    # --- Appendix A.2: exact 19-column header (Temperature + 18 derived) ---
    DATA_HEADER = (
        "Temperature\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\t"
        "theta\tchi\tR(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\t"
        "Cp''\tCs''"
    )

    # Resolved wherever the file happens to live; '' when the package is
    # not reachable, which the header code treats as "draw the placeholder".
    LOGO_FILE_PATH = pica_asset("LOGO", "UGC_DAE_CSR_NBG.jpeg")

    # --- Theme constants (identical to both source programs) ---
    CLR_BG_DARK     = '#B8A392'
    CLR_HEADER      = '#E5DCD3'
    CLR_FG_LIGHT    = '#2C2825'
    CLR_TEXT_DARK   = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN= '#B68B6E'
    CLR_ACCENT_RED  = '#BA6B5E'
    CLR_CONSOLE_BG  = '#E5DCD3'
    CLR_GRAPH_BG    = '#F4EFEA'
    FONT_SIZE_BASE  = 11
    FONT_BASE       = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL  = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE      = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE    = ('Consolas', 10)

    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    # ------------------------------------------------------------------
    def __init__(self, root):
        self.root = root
        self.root.title(
            "E4980A & Cryocon 34: Dielectric vs. Temperature (Passive T-Monitor)")
        self.root.geometry("1600x980")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 880)

        # --- State flags ---
        self.is_running = False
        self.is_stabilizing = False   # retained but always False (legacy)
        self._stopping = False          # re-entrancy guard for stop
        self._close_after_stop = False  # destroy window once worker exits
        self.start_time = None
        self._last_draw_time = 0.0
        self._redraw_interval = 1.0    # seconds; 1 Hz is ample at 1-2 K/min

        # --- Backend ---
        self.backend = Combined_Backend(log=self.log)
        atexit.register(self.backend.close_instruments)

        # --- File / frequency state ---
        self.file_location_path = ""
        self.frequencies = []
        self.freq_filepaths = {}
        self.t_log_path = None
        self.plot_freq = None

        # Rows that failed to reach disk (network share / disk hiccup);
        # retried every cycle so measured data is never silently dropped.
        self._pending_rows = deque(maxlen=20000)
        self._write_error_logged = False

        # --- Data storage (Appendix A.4: keyed per frequency) ---
        # Plain lists: when plot thinning is enabled they are halved in
        # place at PLOT_MAX_POINTS (plot-only; files keep every point).
        self.data_storage = {
            'time': [],
            'temperature': [],
            'cp': {},   # {freq: {'T': list, 'v': list}}
            'g':  {},   # {freq: {'T': list, 'v': list}}
        }

        # --- UI variables ---
        # Y-scale mode: 'auto' uses decade-snapped log when the data
        # spans >= 1 decade and linear otherwise; 'log'/'linear' force it.
        self.y_scale_var = tk.StringVar(value="auto")
        self.logo_image = None

        # Decade log autoscale state (LabVIEW-style): current snapped
        # y-limits per axis key ("cp" / "g")
        self._decade_ylims = {}

        # --- Threading ---
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self.stop_event = threading.Event()

        # --- Build the GUI ---
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ==================================================================
    # STYLES
    # ==================================================================
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TCheckbutton', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TLabelframe', background=self.CLR_BG_DARK,
                        bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK,
                        foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER,
                        borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover',  self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover',  self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', font=self.FONT_BASE,
                        padding=(10, 9),
                        background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton',
                  background=[('active', '#8AB845'),
                              ('hover',  '#8AB845')])
        style.configure('Stop.TButton', font=self.FONT_BASE,
                        padding=(10, 9),
                        background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton',
                  background=[('active', '#D63C2A'),
                              ('hover',  '#D63C2A')])

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    # ==================================================================
    # LAYOUT
    # ==================================================================
    def create_widgets(self):
        self.create_header()

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # --- Left panel (scrollable) ---
        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK,
                        highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container,
                                  orient="vertical",
                                  command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=scrollable_frame,
                             anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame).pack(
            fill='x', padx=10, pady=5)
        self.create_input_frame(scrollable_frame).pack(
            fill='x', padx=10, pady=5)
        self.create_console_frame(scrollable_frame).pack(
            fill='both', expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

        # sashpos() has no effect until the PanedWindow is actually mapped and
        # laid out — an early call fails SILENTLY. So we (a) wait for the
        # window to be drawn, (b) measure the real required width of the
        # left-panel content instead of guessing, and (c) retry until the
        # sash position verifiably sticks.
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()  # force geometry to be computed

            # Measure the actual content width: inner scrollable frame +
            # vertical scrollbar + a little breathing room. Falls back to
            # LEFT_PANEL_WIDTH if measurement isn't ready yet.
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30  # scrollbar (~15px) + padding
            else:
                target = self.LEFT_PANEL_WIDTH

            self.main_pane.sashpos(0, target)

            # Verify it stuck; if not (widget not mapped yet), retry.
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    # ------------------------------------------------------------------
    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        Label(header_frame,
              text="E4980A & Cryocon 34: Dielectric vs. Temperature (Passive T-Monitor)",
              bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD,
              font=font_title_main).pack(side='left', padx=20, pady=10)

        ttk.Button(header_frame, text="📈",
                   command=launch_plotter_utility,
                   width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header_frame, text="📟",
                   command=launch_gpib_scanner,
                   width=3).pack(side='right', padx=(0, 5), pady=5)

        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}",
              bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT,
              font=self.FONT_SUB_LABEL).pack(
                  side='right', padx=20, pady=10)

    # ------------------------------------------------------------------
    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE,
                             height=self.LOGO_SIZE,
                             bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3,
                         padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              RESAMPLE_FILTER)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception:
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    text="LOGO\nERROR", font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT, justify='center')
        else:
            logo_canvas.create_text(
                self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                text="LOGO\nMISSING", font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT, justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 6, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font,
                  background=self.CLR_BG_DARK).grid(
                      row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre",
                  font=institute_font,
                  background=self.CLR_BG_DARK).grid(
                      row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = (
            "Program Name: Dielectric vs. Temperature (Passive T-Monitor)\n"
            "Instruments: Cryocon Model 34 (read-only), Keysight E4980A\n"
            "Function: FUNC:IMP RX, multi-frequency scan per T point\n"
            "Safety: hardcoded 400 K limit; the run stops and nothing "
            "is written to the Cryocon")
        ttk.Label(frame, text=details_text, justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10),
            sticky='w')

        return frame

    # ------------------------------------------------------------------
    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters',
                           relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)
        padx_val = 10
        r = 0

        # --- Sample Name (span 2) ---
        Label(frame, text="Sample Name:").grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=pady_val, sticky='w')
        r += 1
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(0, 10), sticky='ew')
        r += 1

        # --- AC Bias | DC Bias ---
        Label(frame, text="AC Bias Voltage (V):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="DC Bias Voltage (V):").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["AC Bias"] = Entry(frame, font=self.FONT_BASE)
        self.entries["AC Bias"].insert(0, "1.0")
        self.entries["AC Bias"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.entries["DC Bias"] = Entry(frame, font=self.FONT_BASE)
        self.entries["DC Bias"].insert(0, "0.0")
        self.entries["DC Bias"].grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Delay | Aperture ---
        Label(frame, text="Delay per Freq (s):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="Aperture (:APER):").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].insert(0, "0.1")
        self.entries["Delay"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.aper_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=["SHOR", "MED", "LONG"])
        self.aper_combobox.set("LONG")
        self.aper_combobox.grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Checkbuttons: ALC, Corrections ---
        self.var_alc  = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Enable Auto Level Control (ALC)",
                        variable=self.var_alc).grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=2, sticky='w')
        r += 1
        ttk.Checkbutton(frame, text="Enable Open/Short Corrections",
                        variable=self.var_corr).grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=2, sticky='w')
        r += 1

        # --- Cryocon sensor channel --------------------------------------
        # Replaces the Lakeshore version's "Set Range to Zero" checkbox:
        # this module never writes to the controller, so there is no range
        # to zero. What it does need is which of the four inputs the sample
        # sensor is on, since INPUT? reports in that channel's own units.
        Label(frame, text="Cryocon Sensor Channel:").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.channel_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=CRYOCON_INPUT_CHANNELS, width=6)
        self.channel_cb.set(CRYOCON_INPUT_CHANNELS[0])
        self.channel_cb.grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='w')
        r += 1

        # --- Plot thinning for very long runs (plot-only; files keep all) ---
        self.var_plot_thin = tk.BooleanVar(value=False)   # OFF by default
        ttk.Checkbutton(
            frame,
            text="Thin plot points on long runs (halve when full)",
            variable=self.var_plot_thin).grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=2,
            sticky='w')
        r += 1

        # --- Cable Length ---
        Label(frame, text="Cable Length (m):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.cable_len_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=["0", "1", "2", "4"])
        self.cable_len_combobox.set("1")
        self.cable_len_combobox.grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        r += 1

        # --- Frequency list text box (Section 4) ---
        Label(frame, text="Frequencies (Hz, comma-separated):").grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(5, 0), sticky='w')
        r += 1
        self.freq_text = tk.Text(frame, font=self.FONT_BASE,
                                 height=3, wrap='word')
        self.freq_text.insert('1.0', self.DEFAULT_FREQS)
        self.freq_text.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(0, 10), sticky='ew')
        r += 1

        # --- Plot Frequency dropdown (Appendix A.4) ---
        Label(frame, text="Live Plot Frequency:").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.plot_freq_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='disabled')
        self.plot_freq_cb.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(0, 10), sticky='ew')
        self.plot_freq_cb.bind(
            "<<ComboboxSelected>>", self._on_plot_freq_change)
        r += 1

        # --- Cryocon VISA | LCR VISA ---
        Label(frame, text="Cryocon 34 VISA:").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="LCR (E4980A) VISA:").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.cryocon_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.cryocon_cb.grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.lcr_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lcr_cb.grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Scan for Instruments ---
        self.scan_button = ttk.Button(
            frame, text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=4, sticky='ew')
        r += 1

        # --- Browse Destination Folder ---
        self.file_button = ttk.Button(
            frame, text="Browse Destination Folder...",
            command=self._browse_file_location)
        self.file_button.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=4, sticky='ew')
        r += 1

        # --- Start | Stop ---
        self.start_button = ttk.Button(
            frame, text="Start Measurement",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=r, column=0, padx=padx_val, pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(
            frame, text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton', state='disabled')
        self.stop_button.grid(
            row=r, column=1, padx=padx_val, pady=(10, 10), sticky='ew')

        return frame

    # ------------------------------------------------------------------
    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled',
            bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', bd=0, height=10)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan "
                 "for instruments.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found.")
        return frame

    # ------------------------------------------------------------------
    def create_graph_frame(self, parent):
        graph_container = LabelFrame(
            parent, text='Live Graphs', relief='groove',
            bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK,
            font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        # --- Y-scale selector ---
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        scale_bar = tk.Frame(top_bar, bg=self.CLR_GRAPH_BG)
        scale_bar.pack(side='right', padx=5)
        tk.Label(scale_bar, text="Y scale:",
                 bg=self.CLR_GRAPH_BG).pack(side='left')
        for text, val in (("Auto", "auto"), ("Log", "log"),
                          ("Linear", "linear")):
            ttk.Radiobutton(scale_bar, text=text, value=val,
                            variable=self.y_scale_var,
                            command=self._on_y_scale_change).pack(
                side='left', padx=(8, 0))

        # --- Matplotlib figure with 3 axes ---
        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG,
                             layout='constrained')
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        # Main: Cp vs Temperature
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED,
            marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Cp vs. Temperature", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Capacitance, Cp (F)")
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        # Sub 1: G vs Temperature
        self.line_sub1, = self.ax_sub1.plot(
            [], [], color=self.CLR_ACCENT_GOLD,
            marker='.', markersize=3, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Conductance, G (S)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)

        # Sub 2: Temperature vs Time
        self.line_sub2, = self.ax_sub2.plot(
            [], [], color=self.CLR_ACCENT_GREEN,
            marker='.', markersize=3, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)

        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ==================================================================
    # IMPEDANCE CALCULATIONS  (verbatim from freq-scan program)
    # ==================================================================
    def calculate_impedance_parameters(self, f, R, X):
        """
        Calculates all 18 parameters from measured R (series resistance)
        and X (reactance).

        Complex impedance:  Z = R + jX
        Complex admittance: Y = 1/Z = (R - jX) / |Z|^2
                           G = Re(Y) = R / |Z|^2
                           B = Im(Y) = -X / |Z|^2

        Returns list of 18 values in DATA_HEADER column order:
            Q, D, G, B, Cp, Lp, Cs, Ls, |Z|, theta(rad), chi, Rs,
            theta(deg), Rp, 1/|Z|, omega, Cp'', Cs''
        """
        omega = 2 * np.pi * f
        omega_safe = omega if omega != 0 else 1e-20

        Z_mag = np.sqrt(R ** 2 + X ** 2)
        Z_mag_safe = Z_mag if Z_mag != 0 else 1e-20
        Z_mag_sq = Z_mag_safe ** 2

        # Admittance components
        G = R / Z_mag_sq    # conductance (real part of Y)
        B = -X / Z_mag_sq   # susceptance (imaginary part of Y)

        G_safe = G if G != 0 else 1e-20
        B_safe = B if B != 0 else 1e-20
        X_safe = X if X != 0 else 1e-20

        # Derived parameters
        Rp = 1.0 / G_safe
        Cp = B / omega_safe
        Cs = -1.0 / (omega_safe * X_safe)
        Ls = X / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        # ------------------------------------------------------------------
        # LEGACY COMPATIBILITY:
        # The older LabVIEW program reports inductances (Ls and Lp) as
        # absolute (positive) magnitudes only, regardless of whether the
        # DUT is inductive (+X) or capacitive (-X). To keep this Python
        # implementation drop-in compatible with that legacy data format
        # (so downstream plotting/analysis tools can consume both files
        # interchangeably), we force Ls and Lp to be non-negative here.
        # NOTE: This discards the sign information. If signed inductance
        # is ever needed, derive it back from X (Ls_signed = X/omega).
        # ------------------------------------------------------------------
        Ls = abs(Ls)
        Lp = abs(Lp)

        D = G_safe / B_safe   # dissipation factor
        D_safe = D if D != 0 else 1e-20
        Q = 1.0 / D_safe

        theta_rad = math.atan2(X, R)
        theta_deg = math.degrees(theta_rad)

        chi = X       # reactance
        Rs = R        # series resistance (directly measured)

        Y_mag = 1.0 / Z_mag_safe

        # Complex capacitance C* = C' - jC''
        Cp_double_prime = G / omega_safe
        Cs_double_prime = D * Cs  # Corrected from Cp_double_prime

        return [
            Q,                  # 0
            D,                  # 1
            G,                  # 2
            B,                  # 3
            Cp,                 # 4
            Lp,                 # 5   (absolute value — legacy convention)
            Cs,                 # 6
            Ls,                 # 7   (absolute value — legacy convention)
            Z_mag,              # 8
            theta_rad,          # 9
            chi,                # 10
            Rs,                 # 11
            theta_deg,          # 12
            Rp,                 # 13
            Y_mag,              # 14
            omega,              # 15
            Cp_double_prime,    # 16
            Cs_double_prime,    # 17
        ]

    # ==================================================================
    # FREQUENCY PARSING  (Section 4)
    # ==================================================================
    def _parse_frequencies(self):
        raw = self.freq_text.get('1.0', 'end').replace('\n', ' ')
        freqs = []
        for tok in raw.split(','):
            tok = tok.strip()
            if not tok:
                continue
            f = float(tok)
            if not (20 <= f <= 2e6):
                raise ValueError(
                    f"Frequency {f} Hz outside E4980A range "
                    f"(20 Hz - 2 MHz).")
            freqs.append(f)
        if not freqs:
            raise ValueError("Frequency list is empty.")
        return sorted(set(freqs))

    # ==================================================================
    # LOGGING
    # ==================================================================
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        # Trim to the last CONSOLE_MAX_LINES: an unbounded Text widget is
        # the main cause of Tk slowdown/freeze on multi-day runs.
        try:
            line_count = int(
                self.console_widget.index('end-1c').split('.')[0])
            if line_count > self.CONSOLE_MAX_LINES:
                self.console_widget.delete(
                    '1.0', f'{line_count - self.CONSOLE_MAX_LINES + 1}.0')
        except tk.TclError:
            pass
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _set_keep_awake(self, enable):
        """Stop Windows from sleeping mid-run (display may still sleep).
        Best-effort no-op on other platforms."""
        try:
            flags = self.ES_CONTINUOUS | (
                self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _beep(self, times=1):
        """Audible alert (main/Tk thread only). Beeps in a daemon thread
        so the GUI never blocks; falls back to the Tk bell. Used instead
        of modal dialogs on unattended-run events (kill switch, runtime
        error) — a messagebox nobody is there to click must never linger
        over a multi-day run."""
        if HAS_WINSOUND and platform.system() == "Windows":
            def _do_beep():
                for _ in range(max(1, times)):
                    winsound.Beep(1000, 500)
                    time.sleep(0.2)
            threading.Thread(target=_do_beep, daemon=True).start()
        else:
            try:
                self.root.bell()
            except Exception:
                pass

    def _handle_log_message(self, message):
        self.log(message)

    # ==================================================================
    # START / STOP
    # ==================================================================
    def start_measurement(self):
        try:
            # --- Parse frequencies ---
            self.frequencies = self._parse_frequencies()

            # --- Gather parameters ---
            params = {
                'sample_name':   self.entries["Sample Name"].get(),
                'channel':       self.channel_cb.get(),
                'ac_bias':       float(self.entries["AC Bias"].get()),
                'dc_bias':       float(self.entries["DC Bias"].get()),
                'delay':         float(self.entries["Delay"].get()),
                'aper':          self.aper_combobox.get(),
                'alc_enabled':   self.var_alc.get(),
                'corr_enabled':  self.var_corr.get(),
                'cable_len':     self.cable_len_combobox.get(),
                'cryocon_visa':  self._extract_visa(
                                      self.cryocon_cb.get()),
                'lcr_visa':      self._extract_visa(
                                      self.lcr_cb.get()),
            }

            # --- Validation ---
            if not all([params['sample_name'],
                        params['cryocon_visa'],
                        params['channel'],
                        params['lcr_visa']]) or not self.file_location_path:
                raise ValueError(
                    "Sample Name, both VISA addresses, and a destination "
                    "folder are required.")

            if params['corr_enabled']:
                self.log(
                    f"WARNING: Ensure physical Open/Short calibration was "
                    f"performed with {params['cable_len']} m cable before "
                    f"enabling corrections!")

            # --- Initialize hardware ---
            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: "
                     f"{params['sample_name']}")

            # --- Appendix A.2: create one file per frequency ---
            self._create_per_frequency_files(params['sample_name'])
            self.log(
                f"Number of frequencies entered: {len(self.frequencies)}. "
                f"{len(self.frequencies)} output files created in "
                f"{self.file_location_path}")

            # --- Appendix A.4: populate plot-frequency dropdown ---
            self._populate_plot_freq_dropdown()

            # --- Reset state ---
            self.is_stabilizing, self.is_running = False, True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.scan_button.config(state='disabled')

            self.data_storage['time'] = []
            self.data_storage['temperature'] = []
            self.data_storage['cp'] = {
                f: {'T': [], 'v': []} for f in self.frequencies}
            self.data_storage['g'] = {
                f: {'T': [], 'v': []} for f in self.frequencies}
            self._pending_rows.clear()
            self._write_error_logged = False
            self._decade_ylims.clear()  # fresh dataset re-snaps decades

            for line in (self.line_main, self.line_sub1, self.line_sub2):
                line.set_data([], [])
            self.ax_main.set_title(
                f"Cp vs. T @ {int(self.plot_freq)} Hz", fontweight='bold')
            self.canvas.draw()

            self.stop_event.clear()
            self._set_keep_awake(True)   # multi-day run: no system sleep
            self.log("Starting passive temperature-sensing measurement...")

            # --- Launch worker thread ---
            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start measurement.\n{e}")

    # ------------------------------------------------------------------
    def _extract_visa(self, combo_val):
        """Strips the '  ->  IDN' suffix added by the identity-aware scan."""
        if "  ->  " in combo_val:
            return combo_val.split("  ->  ")[0].strip()
        return combo_val.strip()

    # ------------------------------------------------------------------
    def _create_per_frequency_files(self, sample_name):
        """
        Appendix A.2 / A.3:
        One .txt file per frequency, header pre-written.
        If any target file already exists, appends a timestamp to the
        sample name to avoid silently overwriting previous data.
        """
        candidate_paths = {
            f: os.path.join(self.file_location_path,
                            f"{sample_name}-{int(f)}Hz.txt")
            for f in self.frequencies
        }
        if any(os.path.exists(p) for p in candidate_paths.values()):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sample_name = f"{sample_name}_{ts}"
            candidate_paths = {
                f: os.path.join(self.file_location_path,
                                f"{sample_name}-{int(f)}Hz.txt")
                for f in self.frequencies
            }
            self.log(f"Existing files detected. Using unique sample "
                     f"tag: {sample_name}")

        self.freq_filepaths = candidate_paths
        for f, path in self.freq_filepaths.items():
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(self.DATA_HEADER + "\n")
                fh.flush()
                os.fsync(fh.fileno())

        # Timestamped temperature log (separate file so the legacy
        # 19-column per-frequency format stays untouched).
        self.t_log_path = os.path.join(
            self.file_location_path, f"{sample_name}_T-log.txt")
        with open(self.t_log_path, 'w', encoding='utf-8') as fh:
            fh.write("# Cryocon Model 34, input channel "
                     f"{self.backend.params.get('channel', '?')}, read only; "
                     "Heater_pct is LOOP 1:OUTPWR?\n")
            fh.write("DateTime\tElapsed_s\tTemperature_K\tHeater_pct\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ------------------------------------------------------------------
    def _populate_plot_freq_dropdown(self):
        """
        Appendix A.4:
        Populate the plot-frequency dropdown.  Default selection is the
        lowest frequency (index 0) — the sweep measures lowest-first, so
        the live plot shows data as soon as the measurement starts.
        """
        vals = [f"{int(f)} Hz" for f in self.frequencies]
        self.plot_freq_cb.config(state='readonly')
        self.plot_freq_cb['values'] = vals
        default_index = 0
        self.plot_freq_cb.current(default_index)
        self.plot_freq = self.frequencies[default_index]
        self.log(
            f"Default live-plot frequency: {int(self.plot_freq)} Hz "
            f"(lowest of {len(self.frequencies)}).")

    # ------------------------------------------------------------------
    def _on_plot_freq_change(self, event=None):
        """
        Appendix A.4:
        Main-thread-only redraw.  Never touches the worker thread or the
        instruments, so switching frequency cannot cause measurement lag,
        errors, or a hang.
        """
        sel = self.plot_freq_cb.get().replace(" Hz", "")
        self.plot_freq = float(sel)
        self._decade_ylims.clear()  # new frequency re-snaps decades
        self.ax_main.set_title(
            f"Cp vs. T @ {int(self.plot_freq)} Hz", fontweight='bold')
        self._update_live_plots(force=True)

    # ------------------------------------------------------------------
    def stop_measurement(self, from_user=True):
        # Wait for worker to finish before touching VISA. The wait is a
        # non-blocking root.after() poll so the GUI never freezes (was a
        # join(15) that stalled the window on Stop).
        if self._stopping or not (self.is_running or self.is_stabilizing):
            return
        self._stopping = True
        self.is_running, self.is_stabilizing = False, False
        self.stop_event.set()
        self.stop_button.config(state='disabled')
        if from_user:
            self.log("Stop requested; waiting for worker to finish...")

        self._stop_deadline = time.time() + 15.0
        self._poll_worker_stopped(from_user)

    def _poll_worker_stopped(self, from_user):
        t = self.measurement_thread
        if (
            t is not None
            and t.is_alive()
            and time.time() < self._stop_deadline
        ):
            self.root.after(
                200, lambda: self._poll_worker_stopped(from_user))
            return
        if t is not None and t.is_alive():
            self.log("WARNING: worker did not exit in 15 s; "
                     "closing sessions anyway.")
        self._finalize_stop(from_user)

    def _finalize_stop(self, from_user):
        self._set_keep_awake(False)  # allow system sleep again
        self.start_button.config(state='normal')
        self.scan_button.config(state='normal')
        try:
            self.backend.close_instruments()
        except Exception as e:
            self.log(f"WARNING: error closing instruments: {e}")
        self._stopping = False

        if self._close_after_stop:
            self.root.destroy()
            return

        if from_user:
            self.log("Measurement stopped and instruments disconnected.")
            messagebox.showinfo(
                "Info", "Measurement stopped and instruments disconnected.")

    # ==================================================================
    # WORKER THREAD  (Passive monitoring — no stabilization, no ramp,
    #                 no direction assumption; comm errors retry forever)
    # ==================================================================
    def _measurement_worker(self):
        params = self.backend.params
        try:
            self.start_time = time.time()
            self.data_queue.put("LOG:Passive monitoring started. "
                                "Measuring until stopped by user.")
            comm_failures = 0
            while self.is_running and not self.stop_event.is_set():
                try:
                    cycle = self.backend.measure_frequency_sweep(
                        self.frequencies, params['delay'], self.stop_event)
                    comm_failures = 0
                except Exception:
                    # Comm glitch (GPIB/VISA/instrument power blip):
                    # never give up — log, back off, reconnect, resume.
                    comm_failures += 1
                    self.data_queue.put(
                        f"LOG:COMM ERROR (failure #{comm_failures}): "
                        f"{traceback.format_exc(limit=3)}")
                    if not self._reconnect_with_backoff(comm_failures):
                        break   # stop requested during backoff
                    continue

                elapsed = time.time() - self.start_time

                if cycle['points']:
                    self.data_queue.put(('CYCLE', cycle, elapsed))
                else:
                    break   # stop_event aborted the sweep pre-first-point

                # Hardcoded 400 K kill switch (checks max T in cycle)
                max_temp = max(pt[0] for pt in cycle['points'])
                if self.backend.check_safety_kill(max_temp):
                    self.data_queue.put("KILL")
                    break
        except Exception as e:
            # Last-resort safety net for unexpected (non-comm) bugs only;
            # comm errors are handled by the retry loop above.
            # The traceback is formatted HERE, in the thread where the
            # exception is live. Formatting it on the GUI thread printed
            # 'NoneType: None' and lost the fault.
            self.data_queue.put(('ERROR', e, traceback.format_exc()))

    def _reconnect_with_backoff(self, attempt):
        """Worker-thread helper: close and re-open both instruments,
        escalating the wait between tries (5 -> 10 -> 30 -> 60 s cap).
        Loops until reconnected; returns False only if Stop was
        requested while waiting or reconnecting."""
        backoffs = (5, 10, 30, 60)
        while self.is_running and not self.stop_event.is_set():
            delay_s = backoffs[min(attempt - 1, len(backoffs) - 1)]
            self.data_queue.put(
                f"LOG:Reconnect attempt in {delay_s} s "
                "(Stop stays responsive)...")
            deadline = time.time() + delay_s
            while time.time() < deadline:
                if self.stop_event.wait(1.0):
                    return False
            try:
                self.backend.reconnect()
                self.data_queue.put(
                    "LOG:Reconnected. Resuming measurement.")
                return True
            except Exception as e:
                attempt += 1
                self.data_queue.put(f"LOG:Reconnect failed: {e}")
        return False

    # ==================================================================
    # QUEUE PROCESSING (main thread)
    # ==================================================================
    def _process_data_queue(self):
        terminal = None
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()

                try:
                    if isinstance(data, str) and data.startswith("LOG:"):
                        self._handle_log_message(data[4:])
                    elif isinstance(data, str) and data == "KILL":
                        terminal = "KILL"          # defer; keep draining
                    elif isinstance(data, tuple) and data[0] == 'ERROR':
                        _, exc, tb_text = data
                        self.log(f"WORKER TRACEBACK:\n{tb_text}")
                        terminal = exc
                    elif isinstance(data, Exception):
                        terminal = data
                    elif isinstance(data, tuple) and data[0] == 'CYCLE':
                        _, cycle, elapsed = data
                        self._process_cycle(cycle, elapsed)
                except Exception:
                    # A GUI-side failure (plot/log/file) must never kill
                    # this pump: acquisition continues in the worker and
                    # the next items still get processed.
                    try:
                        self.log("GUI ERROR (non-fatal): "
                                 f"{traceback.format_exc()}")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        finally:
            # Re-scheduling lives in a finally so the pump survives any
            # unexpected error above — a frozen-looking-but-alive window
            # silently dropping data is the worst multi-day failure mode.
            try:
                if terminal == "KILL":
                    self._handle_kill_event()
                elif isinstance(terminal, Exception):
                    self._handle_runtime_error(terminal)
                elif self.is_running or self.is_stabilizing:
                    self.root.after(200, self._process_data_queue)
            except tk.TclError:
                pass   # window already destroyed

    # ------------------------------------------------------------------
    def _process_cycle(self, cycle, elapsed):
        # Log non-zero status warnings
        for (temp, f, R, X, status) in cycle['points']:
            if status != 0:
                self.log(
                    f"WARNING: f={int(f)} Hz status={status} "
                    f"(non-zero = overload/ALC issue) "
                    f"at T={temp:.3f} K")

        last_temp = cycle['points'][-1][0]
        self.log(
            f"Cycle @ t={elapsed:.1f}s | last T={last_temp:.3f} K | "
            f"Htr={cycle['heater']:.1f}%")

        # Appendix A.3: safe per-point file writing
        self._save_cycle_to_files(cycle)

        # One timestamped T-log row per sweep (multi-day thermal history)
        self._append_t_log(cycle, elapsed)

        # Update in-memory storage for plotting
        self._update_data_storage(cycle, elapsed)

        # Redraw plots (throttled)
        self._update_live_plots()

    # ==================================================================
    # Appendix A.3: Safe per-point file writing (open / append / close)
    # + fsync so a sudden power cut cannot lose OS-buffered rows
    # ==================================================================
    def _durable_write(self, path, text):
        """Append text and force it to the physical disk immediately."""
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def _write_or_buffer(self, path, row_str):
        """Write a row durably; on disk/share hiccup, buffer it for
        retry next cycle so measured data is never silently dropped.
        While rows are pending, new rows go straight to the buffer to
        preserve per-file ordering."""
        if self._pending_rows:
            self._pending_rows.append((path, row_str))
            return
        try:
            self._durable_write(path, row_str)
        except OSError as e:
            self._pending_rows.append((path, row_str))
            if not self._write_error_logged:
                self._write_error_logged = True
                self.log(f"WRITE ERROR: {e} — buffering rows and "
                         "retrying every cycle.")

    def _flush_pending_rows(self):
        while self._pending_rows:
            path, row = self._pending_rows[0]
            try:
                self._durable_write(path, row)
            except OSError:
                return   # still failing; keep buffer, retry next cycle
            self._pending_rows.popleft()
        if self._write_error_logged:
            self._write_error_logged = False
            self.log("Write path recovered; buffered rows flushed.")

    def _save_cycle_to_files(self, cycle):
        self._flush_pending_rows()
        for (temp, f, R, X, status) in cycle['points']:
            try:
                calc = self.calculate_impedance_parameters(f, R, X)
            except Exception as calc_err:
                self.log(f"Calc error at {int(f)} Hz: {calc_err}. "
                         f"Writing NaNs.")
                calc = [float('nan')] * 18

            # Every value — including temperature — formatted as %.6E
            row_vals = [temp] + calc
            row_str = "\t".join("{:.6E}".format(v) for v in row_vals)
            self._write_or_buffer(self.freq_filepaths[f], row_str + "\n")

    def _append_t_log(self, cycle, elapsed):
        """One row per sweep: wall-clock, elapsed s, T, heater %."""
        if not self.t_log_path:
            return
        wall = cycle.get('wall_time') or datetime.now()
        last_temp = cycle['points'][-1][0]
        row = (f"{wall.strftime('%Y-%m-%d %H:%M:%S')}\t{elapsed:.1f}\t"
               f"{last_temp:.4f}\t{cycle['heater']:.2f}\n")
        self._write_or_buffer(self.t_log_path, row)

    # ==================================================================
    # DATA STORAGE UPDATE (Appendix A.4: per-frequency keyed)
    # ==================================================================
    @staticmethod
    def _thin_pair(a, b):
        """Halve two aligned plot lists (drop every other point) while
        always keeping the newest point. Plot-only: the data files keep
        every measured point. Both lists get the identical slice so
        they stay aligned."""
        if len(a) % 2 == 0:
            return a[::2] + a[-1:], b[::2] + b[-1:]
        return a[::2], b[::2]

    def _update_data_storage(self, cycle, elapsed):
        thin = self.var_plot_thin.get()
        for (temp, f, R, X, status) in cycle['points']:
            try:
                calc = self.calculate_impedance_parameters(f, R, X)
                cp, g = calc[4], calc[2]
            except Exception:
                cp, g = float('nan'), float('nan')

            for key, val in (('cp', cp), ('g', g)):
                store = self.data_storage[key][f]
                store['T'].append(temp)
                store['v'].append(val)
                if thin and len(store['T']) >= self.PLOT_MAX_POINTS:
                    store['T'], store['v'] = self._thin_pair(
                        store['T'], store['v'])

        self.data_storage['time'].append(elapsed)
        self.data_storage['temperature'].append(cycle['points'][-1][0])
        if thin and len(self.data_storage['time']) >= self.PLOT_MAX_POINTS:
            self.data_storage['time'], self.data_storage['temperature'] = \
                self._thin_pair(self.data_storage['time'],
                                self.data_storage['temperature'])

    # ==================================================================
    # PLOTTING (Appendix A.4: decoupled from acquisition)
    # ==================================================================
    def _apply_y_scale(self, ax, values, key):
        """Adaptive Y-scale driven by self.y_scale_var ('auto'|'log'|'linear').

        log:    LabVIEW-style decade autoscale — snap y-limits to
                [10^floor(log10(min_pos)), 10^ceil(log10(max_pos))],
                expand only (cached in self._decade_ylims[key]) so the
                scale never jitters per point.
        linear: plain relim/autoscale_view.
        auto:   log when the positive data spans >= 1 decade, else
                linear, so narrow-span data is not squashed onto a
                single decade band. Data only accumulates during a
                sweep, so the span is monotone and auto cannot flicker
                back from log to linear mid-sweep.
        Falls back to linear whenever there is no positive finite data.
        Returns True when a log scale was applied."""
        mode = self.y_scale_var.get()
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        use_log = bool(pos) and mode != 'linear'
        if use_log and mode == 'auto':
            span = math.log10(max(pos)) - math.log10(min(pos))
            use_log = span >= 1.0  # at least one full decade of data
        if not use_log:
            ax.set_yscale('linear')
            ax.relim()
            ax.set_autoscaley_on(True)  # set_ylim in log mode disables it
            ax.autoscale_view(scaley=True)
            self._decade_ylims.pop(key, None)
            return False
        lo = 10.0 ** math.floor(math.log10(min(pos)))
        hi = 10.0 ** math.ceil(math.log10(max(pos)))
        if hi <= lo:
            hi = lo * 10.0
        cur = self._decade_ylims.get(key)
        if cur is not None:
            lo, hi = min(lo, cur[0]), max(hi, cur[1])  # expand only
        if cur != (lo, hi):
            self._decade_ylims[key] = (lo, hi)
            ax.set_yscale('log')
            ax.set_ylim(lo, hi)
        return True

    # ------------------------------------------------------------------
    def _on_y_scale_change(self):
        self._decade_ylims.clear()  # re-snap from scratch on mode change
        self._update_live_plots(force=True)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def _update_live_plots(self, force=False):
        fq = self.plot_freq
        if fq is None or fq not in self.data_storage['cp']:
            return

        # Update line data for the currently selected frequency
        self.line_main.set_data(
            self.data_storage['cp'][fq]['T'],
            self.data_storage['cp'][fq]['v'])
        self.line_sub1.set_data(
            self.data_storage['g'][fq]['T'],
            self.data_storage['g'][fq]['v'])
        self.line_sub2.set_data(
            self.data_storage['time'],
            self.data_storage['temperature'])

        # Throttle expensive redraws
        now = time.time()
        if not force and (now - self._last_draw_time) < self._redraw_interval:
            return
        self._last_draw_time = now

        self._rescale_main_axis()
        self.ax_sub1.relim()
        self.ax_sub1.autoscale_view(scalex=True, scaley=False)
        self._apply_y_scale(
            self.ax_sub1, self.data_storage['g'][fq]['v'], "g")
        self.ax_sub2.relim()
        self.ax_sub2.autoscale_view()

        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def _rescale_main_axis(self):
        """Autoscale the main Cp axis, safely handling log y-scale."""
        fq = self.plot_freq
        if fq is None or fq not in self.data_storage['cp']:
            return
        vals = self.data_storage['cp'][fq]['v']
        temps = self.data_storage['cp'][fq]['T']
        if not vals:
            return

        self._apply_y_scale(self.ax_main, vals, "cp")

        if temps:
            xlo, xhi = min(temps), max(temps)
            if xhi > xlo:
                pad = (xhi - xlo) * 0.05
                self.ax_main.set_xlim(xlo - pad, xhi + pad)

    # ==================================================================
    # EVENT HANDLERS
    # ==================================================================
    def _handle_kill_event(self):
        # UNATTENDED POLICY: no modal dialog — runs execute overnight
        # with nobody at the PC. Loud console log + beeps only; the run
        # is already stopped and every row is fsync'd on disk.
        kill_t = self.backend.SAFETY_KILL_TEMP_K
        self.log(f"!!! HARDCODED SAFETY LIMIT ({kill_t:.0f} K) REACHED — "
                 "measurement stopped !!!")
        self._update_live_plots(force=True)
        self.stop_measurement(False)
        self.log(f"Temperature reached {kill_t:.0f} K. Measurement stopped "
                 "and data is on disk. NOTHING was written to the Cryocon: "
                 "whatever is driving the cryostat is still driving it. "
                 "Check the controller.")
        self._beep(times=5)

    # ------------------------------------------------------------------
    def _handle_runtime_error(self, exception):
        # UNATTENDED POLICY: no modal dialog (see _handle_kill_event).
        self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
        self.stop_measurement(False)
        self.log(f"A critical error occurred: {exception}. Measurement "
                 "stopped; all written data is on disk.")
        self._beep(times=3)

    # ==================================================================
    # VISA SCAN (identity-aware, for BOTH instruments)
    # ==================================================================
    def _scan_for_visa_instruments(self):
        """
        Identity-aware instrument scan (from freq-scan program, extended
        to auto-select both the Cryocon 34 and the E4980A).
        Queries *IDN? on every discovered VISA resource, then auto-selects
        the device reporting 'E4980' → LCR combobox and the device
        reporting 'Cryocon' or 'Cryo-con' → Cryocon combobox.
        Displays 'address  ->  IDN' in both comboboxes.
        """
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA is not installed.")
            return

        try:
            rm = pyvisa.ResourceManager()
        except Exception as e:
            self.log(f"ERROR: Cannot create VISA Resource Manager: {e}")
            return

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        resources = rm.list_resources()
        if not resources:
            self.log("No VISA instruments found.")
            return

        found = []
        cryocon_label = None
        lcr_label = None

        for res in resources:
            idn = "Unknown / no response"
            try:
                with rm.open_resource(res) as dev:
                    dev.timeout = 2000
                    # Terminations are left at the PyVISA defaults. The
                    # Cryocon GPIB port frames lines with EOI and no EOS
                    # character, and forcing "\n" here is what the Lakeshore
                    # sibling needs, not this one.
                    idn = dev.query("*IDN?").strip()
            except Exception:
                pass  # busy / non-SCPI / timeout — skip silently
            time.sleep(0.05)   # let the address settle before the next one

            label = f"{res}  ->  {idn}"
            found.append(label)
            self.log(f"  {label}")

            # Auto-select the Cryocon
            if cryocon_label is None and is_cryocon_idn(idn):
                cryocon_label = label

            # Auto-select E4980A
            if lcr_label is None and "E4980" in idn:
                lcr_label = label

        self.cryocon_cb['values'] = found
        self.lcr_cb['values'] = found

        if cryocon_label:
            self.cryocon_cb.set(cryocon_label)
            self.log("Cryocon 34 auto-selected.")
        elif found:
            self.cryocon_cb.set(found[0])
            self.log("WARNING: No Cryo-con answered *IDN?; defaulted to the "
                     "first device. Start will refuse it unless it really "
                     "is a Cryo-con.")

        if lcr_label:
            self.lcr_cb.set(lcr_label)
            self.log("E4980A auto-selected.")
        elif found:
            self.lcr_cb.set(found[0])
            self.log("WARNING: No E4980A found; "
                     "defaulted to first device.")

    # ==================================================================
    # FILE BROWSE  (Appendix A.2: askdirectory)
    # ==================================================================
    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Destination folder set to: {path}")

    # ==================================================================
    # WINDOW CLOSE
    # ==================================================================
    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno(
                    "Exit",
                    "Measurement is running. Stop and exit?"):
                # Destroy is deferred to _finalize_stop so the worker
                # can exit cleanly without freezing the GUI.
                self._close_after_stop = True
                self.stop_measurement(from_user=False)
        elif self._stopping:
            # Stop already in progress — just close once it finishes.
            self._close_after_stop = True
        else:
            self.root.destroy()


# ===============================================================================
# MAIN ENTRY POINT
# ===============================================================================

def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\nPlease run:\n"
            "pip install pyvisa")
        return

    root = tk.Tk()
    Integrated_CT_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()