"""
Module: T_Sensing_CC34_GUI.py
Purpose: GUI module for T Sensing CC34 GUI v1.1.

         v1.1, 28 Aug 2026. Three fixes, all driven by a failed run on a
         Model 34 Rev 3.03A at GPIB1::12:
           - the first '*IDN?' after a bus scan could time out inside
             viWrite; the session is now opened with a settle delay and the
             first query is retried, which is what pressing Start twice was
             doing by hand;
           - a worker-thread failure was logged as 'NoneType: None' because
             traceback.format_exc() ran in the GUI thread, where no exception
             is live. The worker now hands its own formatted traceback across
             the queue, so the real cause is printed;
           - the sensor channel is selectable (A, B, C or D) instead of being
             fixed at A, and both the units check and a trial reading happen
             at Start, so an empty or faulted channel is named there rather
             than one second later inside the logging loop.

         Cryocon Model 34 equivalent of T_Sensing_L350_GUI.py. Logs
         temperature vs. time from Cryocon input channel A with the same
         plot, console, CSV and file-handling logic as the Lakeshore
         version.

         The Cryocon is treated as READ ONLY. Unlike the Lakeshore version,
         which sends *RST/*CLS on connect, this module sends no *RST (a
         Cryocon *RST is a 15 s hardware reset to power-up defaults), no
         CONTROL/STOP and no heater, loop or configuration command. Whatever
         is driving the temperature keeps running untouched.

Cryocon SCPI verified against the Cryo-con User's Guide; the command set is
common to the Model 32/32B/34 family:
  - INPUT? <ch>          -> channel temperature in that channel's display units
  - INPUT <ch>:UNITS?    -> display units (K, C, F, V or O)
  - GPIB: factory address 12, EOI framing, no EOS terminator
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import os
import re
import time
import traceback
import threading
import queue
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

import runpy
from multiprocessing import Process


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

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

# A Cryo-con replies to *IDN? with something like
# "Cryocon,Model 34,204683,3.18A". Both spellings of the maker name are in
# circulation, so both are accepted.
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON")

# VISA resource kinds worth probing during a scan. Serial ports are left
# alone: on a Windows rack ASRL1 is as likely to be a UPS or a Bluetooth port
# as an instrument, and a *IDN? at one of those blocks for the whole timeout.
PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")

# How many times the logging loop will try to re-open a dropped link
# before it gives up. Set to 0 to stop at the first communication error.
CRYOCON_WORKER_RETRIES = 5

# Input channels on a Model 34.
CRYOCON_INPUT_CHANNELS = ('A', 'B', 'C', 'D')

# Factory address, used only as a last-resort hint. Identification is by
# *IDN? content, so a re-addressed Cryocon is still found.
CRYOCON_ADDRESS_HINT = "GPIB0::12"

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


def identify_resources(rm, resources):
    """Return {resource: idn} for every resource that answers *IDN?.

    Never raises: an address that is busy, silent or not SCPI simply does not
    appear in the result. Serial resources are not probed at all.
    """
    found = {}
    for res in resources:
        if not str(res).upper().startswith(PROBE_RESOURCE_PREFIXES):
            continue
        inst = None
        try:
            inst = rm.open_resource(res)
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
                # Let the address settle before the next one is addressed.
                time.sleep(0.05)
    return found


class Cryocon34_Backend:
    """A class to passively monitor the Cryocon Model 34 Temperature Monitor.

    Every method here is a query. Nothing in this class writes to the
    instrument, so its control loops, heater and settings are untouched.
    """

    def __init__(self, visa_address, channel='A', log=None):
        self.channel = (str(channel).strip().upper() or 'A')
        self.log = log if callable(log) else (lambda msg: print(msg))
        self.link = CryoconLink(visa_address, log=self.log)
        self.idn = self.link.idn
        # Confirm what actually answered before treating its numbers as
        # temperatures. GPIB addresses get changed; if this one now holds a
        # Lakeshore or a Keithley, its reply to INPUT? would be logged as a
        # sample temperature. Refuse rather than log the wrong instrument.
        if not is_cryocon_idn(self.idn):
            self.link.close()
            raise ValueError(
                f"{visa_address} is not a Cryo-con: it identifies itself as "
                f"'{self.idn}'. Scan the bus and pick the Cryocon's actual "
                f"address (it does not have to be {CRYOCON_ADDRESS_HINT}).")
        self.log(f"Cryocon connected: {self.idn}")

    @property
    def instrument(self):
        """Kept so older callers that poke at .instrument still work."""
        return self.link.instrument

    def configure_for_monitoring(self, sensor=None):
        """Confirm the channel reports Kelvin and can be read. Writes nothing.

        No *RST is sent: on a Cryocon that is a ~15 s hardware reset back to
        power-up defaults, which would disturb a running experiment.

        INPUT? returns the reading in the channel's own display units, so a
        channel left in C or F would silently log wrong numbers -- hence the
        units check. The trial reading is here so that an empty or faulted
        channel is reported at Start, by name, instead of killing the worker
        thread one second later.
        """
        ch = (str(sensor).strip().upper() if sensor else self.channel)
        self.channel = ch
        units = self.link.query(f'INPUT {ch}:UNITS?').upper()
        if not units.startswith('K'):
            raise ValueError(
                f"Cryocon channel {ch} is reporting in '{units}', not "
                "Kelvin. Either set that channel to K on the Cryocon front "
                "panel (this program never writes to it), or pick the "
                "channel your sensor is actually on.")
        self.log(
            f"Cryocon connected for passive monitoring on channel {ch} "
            "(units K). Heater and loop state are unchanged.")

    def probe_channel(self, sensor=None):
        """Take one trial reading so a dead channel is named at Start.

        Kept separate from configure_for_monitoring so that the units check
        stays a single query, and so this can be skipped without touching
        the units rule. This is the check that would have said 'channel A
        has no sensor' instead of letting the worker thread die a second
        into the run.
        """
        ch = (str(sensor).strip().upper() if sensor else self.channel)
        value = self.get_temperature(ch)
        self.log(f"  Trial reading on channel {ch}: {value:.4f} K.")
        return value

    def get_temperature(self, sensor=None):
        """Reads the temperature from a specified sensor."""
        ch = (str(sensor).strip().upper() if sensor else self.channel)
        raw = self.link.query(f'INPUT? {ch}')
        return parse_cryocon_number(raw, 'temperature reading', ch)

    def reconnect(self):
        """Re-open the session after a communication failure. Queries only."""
        self.link.reconnect()

    def close(self):
        """Closes the connection to the instrument."""
        if self.link:
            try:
                # Passive: close the session only. No STOP and no heater or
                # loop command, so the Cryocon carries on undisturbed.
                self.link.close()
            except Exception as e:
                print(f"Warning: Issue during Cryocon shutdown: {e}")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class TempMonitorGUI:
    PROGRAM_VERSION = "1.1"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    # Resolved wherever the file happens to live; '' when the package is
    # not reachable, which the header code treats as "draw the placeholder".
    LOGO_FILE_PATH = pica_asset("LOGO", "UGC_DAE_CSR_NBG.jpeg")

    # --- Modern Dark Theme (PICA Standard) ---
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 28, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("Cryocon 34 Passive Temperature Monitor")
        self.channel_cb = None
        try:
            self.root.state('zoomed')  # Launch maximized
        except tk.TclError:
            # 'zoomed' is a Windows-only window state; X11 (including the
            # xvfb display CI runs under) rejects it. Not being maximised is
            # not a reason to refuse to open.
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.start_time = None
        self.backend = None
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': []}
        self.logo_image = None
        self.console_widget = None
        self._pending_notes = []
        self.data_queue = queue.Queue()

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)

        # Custom style for the large status label
        style.configure(
            'Status.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_STATUS)

        # --- Style for Entry and Combobox widgets for better visibility ---
        # Use a light background with dark text for high contrast.
        style.configure('TEntry',
                        fieldbackground=self.CLR_GRAPH_BG,  # White background
                        foreground=self.CLR_TEXT_DARK,      # Dark text
                        insertcolor=self.CLR_TEXT_DARK,     # Dark cursor
                        borderwidth=0)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            arrowcolor=self.CLR_TEXT_DARK,
            selectbackground=self.CLR_ACCENT_GOLD,
            selectforeground=self.CLR_TEXT_DARK)

        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Start.TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])

        # Style for LabelFrames
        style.configure(
            'TLabelframe',
            background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER,
            borderwidth=1)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE)

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)
        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
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

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_status_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)

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

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="Cryocon 34: Passive Temperature Monitor",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(
            side='left',
            padx=20,
            pady=10)
        Label(
            header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE).pack(
            side='right',
            padx=20,
            pady=10)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception as e:
                # create_header runs before the console exists, so this used
                # to raise AttributeError inside the error handler and take
                # the whole window down over a missing image file.
                self._early_note(f"Failed to load logo: {e}")
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    text="LOGO\nERROR",
                    font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT,
                    justify='center')
        else:
            self._early_note("Logo not found; running outside the pica "
                             "package tree.")
            logo_canvas.create_text(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                text="LOGO\nMISSING",
                font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT,
                justify='center')

        # Institute Name (larger font)
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=0,
            column=1,
            padx=10,
            pady=(
                10,
                0),
            sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=1,
            column=1,
            padx=10,
            sticky='nw')

        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)

        # Program details
        details_text = ("Program Name: Temperature Monitor\n"
                        "Instrument: Cryocon 34 Controller (Channel A)\n"
                        "Measurement Range: Sensor Dependent")
        ttk.Label(
            frame,
            text=details_text,
            justify='left').grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        Label(
            frame,
            text="Log File Name:").grid(
            row=0,
            column=0,
            columnspan=2,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Sample Name"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=1, column=0, columnspan=2, padx=10, pady=(
                0, 10), sticky='ew')

        ttk.Label(
            frame,
            text="Logging Delay (s):").grid(
            row=2,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Delay"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].grid(
            row=3, column=0, padx=10, pady=(
                0, 5), sticky='ew')
        self.entries["Delay"].insert(0, "1.0")

        # Which input the sample sensor is wired to. A Model 34 has four.
        # Fixing this at A was why a correctly connected instrument still
        # produced a runtime error: channel A was not the one in use.
        ttk.Label(
            frame,
            text="Sensor Channel:").grid(
            row=2,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.channel_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=CRYOCON_INPUT_CHANNELS)
        self.channel_cb.set(CRYOCON_INPUT_CHANNELS[0])
        self.channel_cb.grid(
            row=3, column=1, padx=10, pady=(
                0, 5), sticky='ew')

        ttk.Label(
            frame,
            text="Cryocon 34 VISA:").grid(
            row=4,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.cryocon_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.cryocon_cb.grid(
            row=5, column=0, padx=10, pady=(
                0, 10), sticky='ew')

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=6,
            column=0,
            padx=10,
            pady=4,
            sticky='ew')  # Changed row
        self.file_button = ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location)
        self.file_button.grid(row=7, column=0, padx=10, pady=4, sticky='ew')

        control_frame = ttk.Frame(frame)
        control_frame.grid(
            row=8, column=0, padx=10, pady=(
                10, 10), sticky='ew')
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(
            control_frame,
            text="Start Logging",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.stop_button = ttk.Button(
            control_frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=(5, 0))

    def create_status_frame(self, parent):
        """Creates the frame for displaying live temperature."""
        frame = ttk.LabelFrame(parent, text='Live Status')
        frame.pack(pady=5, padx=10, fill='x')

        # This frame inherits the dark background
        status_inner_frame = ttk.Frame(frame, style='TFrame')
        status_inner_frame.pack(fill='x', expand=True, padx=5, pady=5)

        self.temp_label_var = tk.StringVar(value="--.---- K")
        # The label's style gives it the dark background and gold text
        status_label = ttk.Label(
            status_inner_frame,
            textvariable=self.temp_label_var,
            style='Status.TLabel',
            anchor='center',
            padding=(
                0,
                10))
        status_label.pack(pady=10, fill='x')

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(
            parent,
            text='Console Output',
            style='TLabelframe')
        frame.pack(pady=5, padx=10, fill='x', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0,
            relief='flat')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log(
            "Console initialized. Configure parameters and scan for instruments.")
        for note in getattr(self, '_pending_notes', []):
            self.log(note)
        self._pending_notes = []
        if not PICA_ROOT:
            self.log("Note: running outside the pica package. Measurement is "
                     "unaffected; the Plotter and Scanner utility buttons are "
                     "not available.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graph')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Temperature vs. Time", fontweight='bold')
        self.ax_main.set_xlabel("Elapsed Time (s)")
        self.ax_main.set_ylabel("Temperature (K)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _early_note(self, message):
        """Queue a note raised before the console widget exists."""
        if getattr(self, 'console_widget', None) is None:
            self._pending_notes = getattr(self, '_pending_notes', [])
            self._pending_notes.append(message)
        else:
            self.log(message)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'delay': float(self.entries["Delay"].get()),
                'cryocon_visa': self.cryocon_cb.get(),
                'channel': self.channel_cb.get(),
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError(
                    "All fields, VISA address, and save location are required.")

            self.backend = Cryocon34_Backend(
                params['cryocon_visa'], channel=params['channel'],
                log=self.log)
            self.backend.configure_for_monitoring()
            self.backend.probe_channel()
            self.log(f"Backend initialized for: {params['sample_name']} "
                     f"(Cryocon 34 at {params['cryocon_visa']}, channel "
                     f"{params['channel']})")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_Temp_passive.dat"
            self.data_filepath = os.path.join(
                self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Log File: {params['sample_name']}"])
                writer.writerow([f"# Instrument: Cryocon 34 at "
                                 f"{params['cryocon_visa']}, input channel "
                                 f"{params['channel']} (read only)"])
                writer.writerow(
                    ["Timestamp", "Elapsed Time (s)", "Temperature (K)"])
            self.log(
                f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(
                f"Temperature Log: {params['sample_name']}",
                fontweight='bold')
            self.canvas.draw()

            self.log("Starting passive data logging...")
            self.start_time = time.time()

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start logging.\n{e}")
            if self.backend:
                self.backend.close()

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement stopped by user.")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            if self.backend:
                self.backend.close()
            messagebox.showinfo(
                "Info", "Logging stopped and instrument disconnected.")

    def _measurement_worker(self):
        """Worker thread for handling blocking instrument calls.

        A dropped GPIB link is retried rather than treated as fatal: a
        temperature log that survives a cable knock is worth more than one
        that stops at the first timeout. A sensor fault is different, and
        stops the run, because the channel is telling us it has nothing to
        report.
        """
        delay_s = float(self.entries["Delay"].get())
        comm_failures = 0
        while self.is_running:
            try:
                temp = self.backend.get_temperature()
                comm_failures = 0
                elapsed = time.time() - self.start_time
                self.data_queue.put((elapsed, temp))
                time.sleep(delay_s)
            except CryoconStatusError as e:
                # The instrument answered; the channel has no valid reading.
                self.data_queue.put((e, traceback.format_exc()))
                break
            except Exception as e:
                comm_failures += 1
                if comm_failures > CRYOCON_WORKER_RETRIES:
                    self.data_queue.put((e, traceback.format_exc()))
                    break
                self.data_queue.put(
                    f"LOG:Communication error ({type(e).__name__}); "
                    f"reconnect attempt {comm_failures} of "
                    f"{CRYOCON_WORKER_RETRIES}.")
                try:
                    self.backend.reconnect()
                except Exception:
                    pass

    def _process_data_queue(self):
        """Processes data from the worker thread to update the GUI."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()

                # Plain string: a status line from the worker, not data.
                if isinstance(data, str) and data.startswith("LOG:"):
                    self.log(data[4:])
                    continue

                # The worker sends (exception, formatted traceback). Calling
                # traceback.format_exc() here would print 'NoneType: None',
                # because no exception is live in this thread -- that is
                # exactly what hid the original fault.
                if isinstance(data, tuple) and data and isinstance(
                        data[0], Exception):
                    exc, tb_text = data[0], data[1]
                    self.log(f"RUNTIME ERROR in worker thread: {exc}")
                    self.log(tb_text)
                    self.stop_measurement()
                    messagebox.showerror(
                        "Runtime Error", f"{exc}\n\nSee console for detail.")
                    return
                if isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR in worker thread: {data}")
                    self.stop_measurement()
                    messagebox.showerror(
                        "Runtime Error", f"{data}\n\nSee console for detail.")
                    return

                elapsed, temp = data
                self.temp_label_var.set(f"{temp:.4f} K")
                self.log(f"T:{temp:.3f} K")

                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([datetime.now().strftime(
                        '%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}", f"{temp:.4f}"])

                self.data_storage['time'].append(elapsed)
                self.data_storage['temperature'].append(temp)

                self.line_main.set_data(
                    self.data_storage['time'],
                    self.data_storage['temperature'])
                self.ax_main.relim()
                self.ax_main.autoscale_view()
                self.figure.tight_layout(pad=3.0)
                self.canvas.draw_idle()

        except queue.Empty:
            pass  # This is normal

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _scan_for_visa_instruments(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = list(rm.list_resources())
            if not resources:
                self.log("No VISA instruments found.")
                return
            self.cryocon_cb['values'] = resources

            # Identify by *IDN? rather than by address: the Cryocon does not
            # have to be at its factory address for this to find it, and a
            # different instrument sitting at that address cannot be picked
            # by mistake.
            identities = identify_resources(rm, resources)
            # Give the bus a moment after the identification pass: the first
            # command of the next session is the one that timed out.
            time.sleep(CRYOCON_OPEN_SETTLE_S)
            for res in resources:
                self.log(f"  {res}  ->  {identities.get(res, 'no reply')}")

            cryocon = next(
                (res for res in resources if is_cryocon_idn(identities.get(res, ''))),
                None)
            if cryocon:
                self.cryocon_cb.set(cryocon)
                self.log(f"Cryocon identified at {cryocon} and selected.")
                return

            # Nothing identified itself as a Cryo-con. Fall back to the
            # factory address as a hint only -- connect() checks *IDN? again
            # and refuses anything that is not a Cryo-con.
            hint = next(
                (res for res in resources if CRYOCON_ADDRESS_HINT in res), None)
            if hint:
                self.cryocon_cb.set(hint)
                self.log(f"WARNING: no Cryo-con answered *IDN?. Selected "
                         f"{hint} on the factory address alone — check the "
                         f"instrument is powered and in remote.")
            else:
                self.log("WARNING: no Cryo-con found on the bus. Pick an "
                         "address manually if you know it.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit",
                                   "Measurement running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            if self.backend:
                self.backend.close()
            self.root.destroy()


def main():
    root = tk.Tk()
    TempMonitorGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
