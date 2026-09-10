"""
 PROGRAM:      Field Step Frequency Scan (E4980A)
 FILE:         Field_Step_Freq_Scan_E4980A_GUI.py
 PURPOSE:      Magnetic-field-dependent dielectric spectroscopy on the PPMS
               with a Keysight E4980A LCR meter, one frequency scan per
               magnetic field, where the TEMPERATURE and the FIELD are set
               BY HAND on the PPMS. This program never talks to the PPMS.
 VERSION:      1.0

 HOW IT IS USED (one temperature at a time):
   1. Set a temperature on the PPMS and let the probe settle.
   2. Type that temperature into "Nominal temperature" here.
   3. Build the list of fields you intend to visit (e.g. 0 .. 90 kOe in
      steps, or a loop  0, +H, 0, -H, 0). Negative and decimal values are
      fine; the list keeps YOUR order.
   4. Set the first field on the PPMS. Pick that field in the list (it is
      the CURRENT field). Press "Measure".
   5. The E4980A runs the 40 Hz - 2 MHz sweep. One file per field:
          <sample>_T<nominal>K_H<field>Oe_<timestamp>.csv
      Measured temperature at the start and the end of the sweep is in
      the header comment lines, together with everything else needed to
      read the file on its own.
   6. When the sweep finishes the CURRENT marker moves to the next field
      (checkbox "advance automatically", on). Change the PPMS field, wait,
      press "Measure" again. Repeat for every field, then change the
      temperature and start again from step 2.

 THERMOMETER (read-only, selectable): Lake Shore 350 (KRDG? A),
   Cryo-con 34 (INPUT? A) or none. Nothing is ever written to either
   controller - no *RST, no setpoint, no heater command. A Cryo-con status
   reply (dots / dashes) is treated as "no reading", never as a crash.

 SCAN ENGINE: the E4980A control and the 18 derived impedance quantities
   are an embedded copy of Frequency_Scan_E4980A_GUI.py (R-X function,
   identical SCPI sequence, identical column set). PICA programs are
   self-contained on purpose: nothing here is imported from a sibling.

 UNATTENDED HARDENING (Temprature_Scan_Passive v1.3 pattern): every data
   row is flushed + fsync'd, connection failures are retried with backoff
   until Stop, a comm error mid-sweep reconnects and retries the same
   frequency point, no modal dialog is ever opened once a measurement is
   running (log + banner + beep only), the worker thread owns all VISA
   traffic and talks to Tk only through an event queue, Start is
   idempotent, and Windows is kept awake while a sweep runs.

 FILE SAFETY: files are never overwritten. Measuring the same field again
   gives a run index in the name (_run2, _run3 ...), and if a name is
   somehow taken anyway a _dup suffix is added.
"""

# ===============================================================================
# IMPORTS  - every import justified (PICA convention: minimal dependencies)
# ===============================================================================

import tkinter as tk                      # GUI toolkit (all PICA modules)
from tkinter import (                     # widgets used below:
    ttk,                                  #   themed widgets / Treeview
    filedialog,                           #   "Browse save folder"
    messagebox,                           #   start-time validation ONLY
    scrolledtext,                         #   the log panel
)
import os                                 # paths, fsync, os.replace
import re                                 # Cryocon reply / name sanitising
import time                               # settle delays, pacing, backoff
import math                               # impedance maths, log10 for axes
import queue                              # worker <-> Tk message queues
import threading                          # single instrument worker thread
import atexit                             # close the E4980A on any exit
import traceback                          # full tracebacks into the log
import platform                           # winsound only on Windows
import ctypes                             # Windows keep-awake during a sweep
from datetime import datetime             # timestamps in names and headers
from multiprocessing import Process       # launch plotter / GPIB scanner
import runpy                              # run those utilities as scripts
import numpy as np                        # frequency grid + sqrt/pi
from matplotlib.figure import Figure      # embedded live plot
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # Tk canvas
from matplotlib.ticker import EngFormatter, NullFormatter          # f axis
import matplotlib as mpl                  # rcParams for the house style

# --- winsound for audible alerts (Windows; optional) ---
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# --- Pillow for the logo image (optional) ---
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


PROGRAM_VERSION = "1.0"
PROGRAM_TITLE = "Field Step Frequency Scan (E4980A)"


# ===============================================================================
# UTILITY LAUNCHERS  (verbatim pattern from Frequency_Scan_E4980A_GUI.py)
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


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(
            script_dir, "..", "utils", "PlotterUtil_GUI.py"
        )
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}",
            )
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch Plotter Utility: {e}"
        )


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py"
        )
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}",
            )
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}"
        )


# ===============================================================================
# PURE HELPERS  (no Tk, no hardware - these are what tests/ exercises)
# ===============================================================================

FIELD_TOL_OE = 1e-6          # two fields closer than this are "the same"
DATA_EXT = ".txt"            # like every other PICA E4980A scan: TAB separated .txt
COL_SEP = "\t"               # same separator as every other PICA E4980A scan

# Column set of Frequency_Scan_E4980A_GUI.py, plus the two constants that
# identify the file (Step_Frequency_Scan appends T_set(K) the same way).
DATA_HEADER = (
    "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\t"
    "R(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''\tT_set(K)\tH_set(Oe)"
)


def ascii_only(text):
    """Replace anything outside 7-bit ASCII with '?' (headers, names)."""
    return str(text).encode("ascii", "replace").decode("ascii")


def sanitize_sample_name(name):
    """A sample name safe for a file name on every OS: ASCII letters,
    digits, '-', '.', '_' only; runs of anything else become one '_'."""
    name = ascii_only(name).strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return name or "Sample"


def format_field(value):
    """Field (or temperature) as a compact ASCII number for names:
    500 -> '500', -500 -> '-500', 11.5 -> '11.5', -2.25 -> '-2.25',
    -0.0 -> '0'. Six decimals maximum, trailing zeros removed."""
    s = f"{float(value):.6f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        s = "0"
    return s


def oe_to_tesla(field_oe):
    """1 T = 10 kOe."""
    return float(field_oe) / 1.0e4


def field_display(field_oe):
    """Human string for the GUI: '500 Oe (0.05 T)'."""
    return f"{format_field(field_oe)} Oe ({format_field(oe_to_tesla(field_oe))} T)"


def generate_field_list(start, end, step=None, points=None):
    """Fields from `start` to `end` (either direction, negatives fine).

    Give EITHER `step` (magnitude; sign is ignored, direction comes from
    start/end; `end` is included only if it lands on the grid, like the
    temperature step builders) OR `points` (number of equally spaced
    values including both ends). Values are rounded to 1e-6 Oe so float
    drift never produces 299.99999.
    """
    start = float(start)
    end = float(end)
    if points is not None:
        n = int(points)
        if n < 1:
            raise ValueError("Number of fields must be at least 1.")
        if n == 1 or start == end:
            return [round(start, 6)]
        vals = [start + (end - start) * i / (n - 1) for i in range(n)]
    elif step is not None:
        step = abs(float(step))
        if step <= 0:
            raise ValueError("Step must be a non-zero number.")
        if start == end:
            return [round(start, 6)]
        direction = 1.0 if end > start else -1.0
        n = int(math.floor(abs(end - start) / step + 1e-9)) + 1
        vals = [start + direction * step * i for i in range(n)]
    else:
        raise ValueError("Give a step or a number of fields.")
    return [round(v, 6) for v in vals]


def add_fields(existing, new_values, allow_repeats=False):
    """Append `new_values` to `existing` keeping order.

    Dedupe policy: by default a value already in the list (within
    FIELD_TOL_OE) is skipped, so 'Generate' twice does not double the
    list. With allow_repeats=True every value is kept - needed for
    hysteresis loops such as 0, +H, 0, -H, 0. Returns (merged, skipped).
    """
    merged = [float(v) for v in existing]
    skipped = 0
    for v in new_values:
        v = float(v)
        if not allow_repeats and any(abs(v - m) < FIELD_TOL_OE for m in merged):
            skipped += 1
            continue
        merged.append(v)
    return merged, skipped


def build_filename(sample, t_nominal, field_oe, timestamp, run_index=1,
                   ext=DATA_EXT):
    """<sample>_T<nominal>K_H<field>Oe[_run<k>]_<timestamp><ext>

    run_index 1 = first measurement of this (T, H) pair (no suffix);
    2, 3, ... = the same pair measured again in this session.
    """
    name = (f"{sanitize_sample_name(sample)}"
            f"_T{format_field(t_nominal)}K"
            f"_H{format_field(field_oe)}Oe")
    if int(run_index) > 1:
        name += f"_run{int(run_index)}"
    return ascii_only(f"{name}_{timestamp}{ext}")


def unique_path(directory, filename):
    """Never overwrite: if `filename` exists in `directory`, add _dup2,
    _dup3, ... before the extension until the name is free."""
    path = os.path.join(directory, filename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(filename)
    k = 2
    while True:
        candidate = os.path.join(directory, f"{stem}_dup{k}{ext}")
        if not os.path.exists(candidate):
            return candidate
        k += 1


def _fmt_T(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "not recorded"
    return f"{float(value):.4f}"


def build_header_lines(info):
    """Comment lines ('# key: value') followed by the column header.

    `info` keys (missing ones print as 'n/a'): sample, t_nominal, field_oe,
    controller, controller_idn, controller_channel, lcr_idn, t_start,
    t_end, t_min, t_max, ac_bias, dc_bias, aper, alc, corr, cable_len,
    delay, n_points, f_min, f_max, started, finished, filename, status,
    run_index. Every line is forced to ASCII.
    """
    g = lambda k, d="n/a": info.get(k, d) if info.get(k) is not None else d
    field = float(g("field_oe", 0.0))
    lines = [
        f"# Program: {PROGRAM_TITLE} v{PROGRAM_VERSION} (PICA)",
        f"# File: {g('filename')}",
        f"# Sample: {g('sample')}",
        f"# T_nominal(K): {format_field(g('t_nominal', 0.0))}   (set by hand on the PPMS)",
        f"# H_set(Oe): {format_field(field)}   (= {format_field(oe_to_tesla(field))} T, set by hand on the PPMS)",
        f"# Run_index: {g('run_index', 1)}   (1 = first scan of this T/H pair in the session)",
        f"# Thermometer: {g('controller')} | channel {g('controller_channel', 'A')} | IDN: {g('controller_idn')}",
        f"# T_measured_start(K): {_fmt_T(info.get('t_start'))}",
        f"# T_measured_end(K): {_fmt_T(info.get('t_end'))}",
        f"# T_measured_during_scan_min(K): {_fmt_T(info.get('t_min'))} | max(K): {_fmt_T(info.get('t_max'))}",
        f"# LCR meter IDN: {g('lcr_idn')}",
        f"# Function: RX | AC level(Vrms): {g('ac_bias')} | DC bias(V): {g('dc_bias')} | Aperture: {g('aper')}",
        f"# ALC: {g('alc')} | Open/Short corrections: {g('corr')} | Cable(m): {g('cable_len')} | Delay per point(s): {g('delay')}",
        f"# Frequency points: {g('n_points')} | f_min(Hz): {g('f_min')} | f_max(Hz): {g('f_max')}",
        f"# Started: {g('started')} | Finished: {g('finished')} | Status: {g('status', 'in progress')}",
        "# Columns are TAB separated. Units: Hz, F, S, ohm, H, rad/deg, K, Oe.",
        DATA_HEADER,
    ]
    return [ascii_only(line) for line in lines]


def parse_fetch_reply(text):
    """':FETC?' reply '<A>,<B>,<status>' -> (A, B, status).

    A and B are the two function values (R and X in RX mode). A missing
    status field counts as 0 (normal). Raises ValueError on garbage.
    """
    parts = [p.strip() for p in str(text).strip().split(",") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"Unexpected FETCh reply: {text!r}")
    a = float(parts[0])
    b = float(parts[1])
    status = int(float(parts[2])) if len(parts) > 2 else 0
    return a, b, status


def parse_lakeshore_temperature(raw):
    """'KRDG? A' reply -> float, or None when it is not a number.
    Accepts '+2.95000E+02', '295.0', a trailing unit character, and a
    multi-channel reply (first field). The 350 reports 0 for a sensor
    fault; that is returned as 0.0 and flagged by the caller."""
    text = str(raw).strip()
    if "," in text:
        text = text.split(",")[0].strip()
    if not text:
        return None
    m = _NUMBER_RE.match(text)
    return float(m.group(0)) if m else None


# --- Cryo-con reply handling (copied from T_Sensing_CC34_GUI.py) ---------
CRYOCON_STATUS_STRINGS = {
    "-------": "sensor fault: the sensor is open, disconnected or shorted",
    ".......": ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    "N/A": "the channel is disabled, or the value does not apply",
    "NACK": "the instrument did not acknowledge the command",
}
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CRYOCON_FAULT_RE = re.compile(r"^-{2,}$")
_CRYOCON_RANGE_RE = re.compile(r"^\.{2,}$")


def parse_cryocon_temperature(raw):
    """'INPUT? A' reply -> float, or None for any status string.

    The front panel shows dashes for a sensor fault and dots for a reading
    off the calibration curve; over the bus those arrive as literal runs of
    '-' or '.'. A trailing unit ('77.350K') is peeled off; a multi-channel
    reply (';' separated) yields the first field.
    """
    text = str(raw).strip()
    if ";" in text:
        text = text.split(";")[0].strip()
    if (not text or text in CRYOCON_STATUS_STRINGS
            or _CRYOCON_FAULT_RE.match(text) or _CRYOCON_RANGE_RE.match(text)):
        return None
    try:
        return float(text)
    except ValueError:
        pass
    m = _NUMBER_RE.match(text)
    return float(m.group(0)) if m else None


def calculate_impedance_parameters(f, R, X):
    """18 derived quantities from measured R (series resistance) and X
    (reactance) - verbatim from Frequency_Scan_E4980A_GUI.py, including
    the legacy LabVIEW conventions (|Ls|, |Lp|, Cp''=G/omega, Cs''=D*Cs).

    Returns [Q, D, G, B, Cp, Lp, Cs, Ls, |Z|, theta(rad), chi, Rs,
             theta(deg), Rp, 1/|Z|, omega, Cp'', Cs'']
    """
    omega = 2 * np.pi * f
    omega_safe = omega if omega != 0 else 1e-20

    Z_mag = np.sqrt(R ** 2 + X ** 2)
    Z_mag_safe = Z_mag if Z_mag != 0 else 1e-20
    Z_mag_sq = Z_mag_safe ** 2

    G = R / Z_mag_sq
    B = -X / Z_mag_sq

    G_safe = G if G != 0 else 1e-20
    B_safe = B if B != 0 else 1e-20
    X_safe = X if X != 0 else 1e-20

    Rp = 1.0 / G_safe
    Cp = B / omega_safe
    Cs = -1.0 / (omega_safe * X_safe)
    Ls = abs(X / omega_safe)            # legacy: magnitude only
    Lp = abs(-1.0 / (omega_safe * B_safe))

    D = G_safe / B_safe
    D_safe = D if D != 0 else 1e-20
    Q = 1.0 / D_safe

    theta_rad = math.atan2(X, R)
    theta_deg = math.degrees(theta_rad)
    Y_mag = 1.0 / Z_mag_safe
    Cp_double_prime = G / omega_safe
    Cs_double_prime = D * Cs

    return [Q, D, G, B, Cp, Lp, Cs, Ls, float(Z_mag), theta_rad, X, R,
            theta_deg, Rp, Y_mag, omega, Cp_double_prime, Cs_double_prime]


def default_sweep_frequencies():
    """40 Hz .. 2 MHz grid - unchanged from Frequency_Scan_E4980A_GUI.py."""
    return np.concatenate([
        np.arange(40, 1000, 10),
        np.arange(1000, 10000, 100),
        np.arange(10000, 100000, 1000),
        np.arange(100000, 1000000, 10000),
        np.arange(1000000, 2000001, 100000),
    ])


# ===============================================================================
# THERMOMETER LINKS  (read-only; copied from the T_Sensing modules)
# ===============================================================================

THERMO_NONE = "none"
THERMO_LS350 = "ls350"
THERMO_CC34 = "cc34"
THERMO_NAMES = {THERMO_NONE: "no thermometer",
                THERMO_LS350: "Lake Shore 350",
                THERMO_CC34: "Cryo-con 34"}
THERMO_DEFAULT_ADDR = {THERMO_LS350: "GPIB0::12::INSTR",
                       THERMO_CC34: "GPIB0::23::INSTR"}


class Lakeshore350_Link:
    """Passive Lake Shore 350 session: *IDN? once, then KRDG? only.

    T_Sensing_L350_GUI.py sends *RST/*CLS at connect; that is NOT done here
    because this program runs beside a PPMS sequence and must not disturb
    anything the controller is doing. Query-only, no reset.
    """
    NAME = "Lake Shore 350"

    def __init__(self, visa_address, channel="A", log=None):
        if pyvisa is None:
            raise ConnectionError("PyVISA is not available.")
        self.address = visa_address
        self.channel = (str(channel).strip().upper() or "A")
        self._log = log if callable(log) else print
        self.instrument = None
        self.idn = ""
        self.rm = pyvisa.ResourceManager()
        self._open()

    def _open(self):
        self.instrument = self.rm.open_resource(self.address)
        self.instrument.timeout = 10000
        self.idn = self.instrument.query("*IDN?").strip()

    def read_temperature(self):
        """-> (kelvin or None, raw reply)."""
        if self.instrument is None:
            raise ConnectionError("Not connected to the Lake Shore 350.")
        raw = self.instrument.query(f"KRDG? {self.channel}").strip()
        return parse_lakeshore_temperature(raw), raw

    def reconnect(self):
        self.close()
        time.sleep(1.0)
        self._open()
        return self.idn

    def close(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception:
                pass
            finally:
                self.instrument = None


CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before the first command
CRYOCON_MIN_GAP_S = 0.08            # minimum gap between consecutive operations
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries
CRYOCON_READ_RETRIES = 3            # extra tries before a reading becomes None
CRYOCON_READ_RETRY_S = 0.3
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON")


class CryoconLink:
    """One paced, read-only VISA session to a Cryo-con 34, opened with
    retries. NEVER sends *RST (a 15 s hardware reset on this instrument).
    Copied from T_Sensing_CC34_GUI.py."""
    NAME = "Cryo-con 34"

    def __init__(self, visa_address, channel="A", log=None):
        if pyvisa is None:
            raise ConnectionError("PyVISA is not available.")
        self.address = visa_address
        self.channel = (str(channel).strip().upper() or "A")
        self.timeout_ms = CRYOCON_TIMEOUT_MS
        self.instrument = None
        self.idn = ""
        self._log = log if callable(log) else print
        self._last_io = 0.0
        self.rm = pyvisa.ResourceManager()
        self._open_and_identify()
        if not any(m in self.idn.upper() for m in CRYOCON_IDN_MARKERS):
            self.close()
            raise ValueError(
                f"{visa_address} is not a Cryo-con: it identifies itself as "
                f"'{self.idn}'.")

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
                # Cryocon GPIB frames with EOI and no EOS: leave the
                # PyVISA termination defaults alone.
                time.sleep(CRYOCON_OPEN_SETTLE_S)
                self.idn = self.query("*IDN?")
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
            f"{CRYOCON_CONNECT_ATTEMPTS} attempts. Last error: {last_error}.")

    def _pace(self):
        gap = CRYOCON_MIN_GAP_S - (time.time() - self._last_io)
        if gap > 0:
            time.sleep(gap)

    def query(self, command):
        if self.instrument is None:
            raise ConnectionError("Not connected to the Cryocon.")
        if "?" not in command:
            raise ValueError("CryoconLink is read-only; refused: " + command)
        self._pace()
        try:
            reply = self.instrument.query(command)
        finally:
            self._last_io = time.time()
        return reply.strip()

    def read_temperature(self):
        """-> (kelvin or None, raw reply). A status reply (dots/dashes) is
        retried in place a few times, then reported as None."""
        raw = ""
        for attempt in range(CRYOCON_READ_RETRIES + 1):
            raw = self.query(f"INPUT? {self.channel}")
            value = parse_cryocon_temperature(raw)
            if value is not None:
                return value, raw
            if attempt < CRYOCON_READ_RETRIES:
                time.sleep(CRYOCON_READ_RETRY_S)
        return None, raw

    def reconnect(self):
        self._drop_session()
        time.sleep(CRYOCON_RETRY_WAIT_S)
        self._open_and_identify()
        return self.idn

    def close(self):
        self._drop_session()


def open_thermometer(kind, address, channel="A", log=None):
    """Factory for the selected controller. Returns None for 'none'."""
    if kind == THERMO_LS350:
        return Lakeshore350_Link(address, channel, log)
    if kind == THERMO_CC34:
        return CryoconLink(address, channel, log)
    return None


# ===============================================================================
# E4980A BACKEND  (copied from Frequency_Scan_E4980A_GUI.py; same SCPI)
# ===============================================================================

class LCR_Backend:
    """Handles all SCPI communication with the Keysight E4980A."""

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.has_opt001 = False
        self.idn = ""
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
        """Configures the instrument for a Frequency sweep using R-X function."""
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        inst = self.rm.open_resource(p["lcr_visa"])
        inst.timeout = 60000  # 60 s; low-freq + LONG + autorange can be slow
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self.instrument = inst

        idn = inst.query("*IDN?").strip()
        if "E4980" not in idn:
            inst.close()
            self.instrument = None
            raise ConnectionError(f"Not an E4980A: {idn}")
        self.idn = idn

        self.has_opt001 = "001" in inst.query("*OPT?")

        # Strict Safety Ceilings: Hard cap at 2.0 V regardless of options
        v_bias_max = min(2.0, 40.0 if self.has_opt001 else 2.0)
        v_ac_max = min(2.0, 20.0 if self.has_opt001 else 2.0)
        if abs(p["dc_bias"]) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V safety limit.")
        if not (0 < p["ac_bias"] <= v_ac_max):
            raise ValueError(f"AC level outside 0-{v_ac_max} Vrms safety limit.")

        inst.write("*RST; *CLS")
        time.sleep(1.0)  # Graceful reset
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
        time.sleep(0.5)  # Let AC level settle

        inst.write(":TRIG:SOUR BUS")
        inst.write(":INIT:CONT ON")
        time.sleep(0.2)

        if abs(p["dc_bias"]) < 1e-9:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT OFF")
        else:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT ON")
            time.sleep(0.5)
            if self.has_opt001:
                self.safe_ramp_dc_bias(p["dc_bias"])
            else:
                if p["dc_bias"] not in (1.5, 2.0):
                    raise ValueError(
                        "Without Option 001, DC bias must be 0, 1.5 or 2 V.")
                inst.write(f":BIAS:VOLT {p['dc_bias']}")
                time.sleep(1.0)

        self._check_errors("configuration")
        return idn

    def reconnect(self):
        """Close and fully re-initialise with the last parameters."""
        self.close_instrument()
        time.sleep(1.0)
        return self.initialize_instrument(self.params)

    def perform_measurement(self, freq, delay):
        """Set frequency, settle, trigger one measurement, fetch R, X, status."""
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")
        self.instrument.write(f":FREQ {freq}")
        time.sleep(delay)
        self.instrument.write(":TRIG:IMM")
        self.instrument.query("*OPC?")
        reply = self.instrument.query(":FETC?")
        return parse_fetch_reply(reply)

    def close_instrument(self):
        if not self.instrument:
            return
        try:
            if self.has_opt001:
                self.safe_ramp_dc_bias(0.0)
            else:
                self.instrument.write(":BIAS:VOLT 0")
                time.sleep(0.5)
            self.instrument.write(":BIAS:STAT OFF")
            self.instrument.write(":DISP:PAGE MEAS")
            time.sleep(0.2)
        except Exception as e:
            print(f"  Warning during E4980A shutdown: {e}")
        finally:
            try:
                self.instrument.close()
            except Exception:
                pass
            finally:
                self.instrument = None


# ===============================================================================
# THE GUI
# ===============================================================================

class FieldStepFreqScanGUI:
    PROGRAM_VERSION = PROGRAM_VERSION
    LOGO_SIZE = 90
    LEFT_PANEL_WIDTH = 520
    EVENT_POLL_MS = 100        # Tk-side event pump period
    T_POLL_MS = 2000           # live temperature while idle
    T_POLL_IN_SCAN_S = 2.0     # live temperature between frequency points
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    RECONNECT_BACKOFF_S = (5, 10, 30, 60)

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theme (keysight siblings) ---
    CLR_BG_DARK = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG_LIGHT = "#2C2825"
    CLR_TEXT_DARK = "#1A1A1A"
    CLR_INPUT_BG = "#F4EFEA"
    CLR_ACCENT_GOLD = "#BA6B5E"
    CLR_ACCENT_GREEN = "#B68B6E"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_OK_GREEN = "#8AB845"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    CLR_MEAS = "#2A6B3A"
    FONT_SIZE_BASE = 11
    FONT_BASE = ("Segoe UI", FONT_SIZE_BASE)
    FONT_TITLE = ("Segoe UI", FONT_SIZE_BASE + 2, "bold")
    FONT_CONSOLE = ("Consolas", 10)
    FONT_HEADLINE = ("Segoe UI", 24, "bold")
    FONT_BIG = ("Segoe UI", 15, "bold")
    FONT_SMALL = ("Segoe UI", 10)

    def __init__(self, root):
        self.root = root
        self.root.title(PROGRAM_TITLE)
        self.root.geometry("1650x980")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        # --- state ---
        self.is_running = False           # a sweep is in progress
        self.stop_event = threading.Event()
        self.fields = []                  # list of floats (Oe), user order
        self.current_idx = None           # index into self.fields
        self.run_counts = {}              # (T, H) -> times measured
        self.records = []                 # "measured so far" rows
        self.save_dir = ""
        self.sweep_frequencies = default_sweep_frequencies()
        self._log_lines = []              # full log for "Save log"
        self.logo_image = None
        self.y_scale_var = tk.StringVar(master=self.root, value="auto")
        self._decade_ylims = {}
        self.plot_data = {"freq": [], "cp": [], "g": []}
        self._plot_dirty = False
        self._last_T = None
        self._thermo_kind_connected = THERMO_NONE
        self._thermo_idn = ""
        self._close_requested = False
        self._poll_pending = False

        # --- worker thread (owns ALL VISA traffic) ---
        self.cmd_queue = queue.Queue()
        self._events = queue.Queue()
        self.lcr = LCR_Backend()
        self.thermo = None                # link object, worker-owned
        self._worker_quit = threading.Event()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        atexit.register(self._atexit_cleanup)

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.worker.start()
        self._drain_events()
        self._schedule_T_poll()
        self.log(f"{PROGRAM_TITLE} v{PROGRAM_VERSION} ready. "
                 f"Sweep grid: {len(self.sweep_frequencies)} points, "
                 f"{self.sweep_frequencies[0]:g} Hz - "
                 f"{self.sweep_frequencies[-1]:g} Hz.")
        self.log("This program never talks to the PPMS. Set T and H by "
                 "hand, pick the field in the list, press Measure.")

    # ------------------------------------------------------------------
    # Styles (copied from the keysight siblings)
    # ------------------------------------------------------------------
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.CLR_BG_DARK)
        style.configure("TPanedWindow", background=self.CLR_BG_DARK)
        style.configure("TLabel", background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure("TCheckbutton", background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure("TRadiobutton", background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure("TLabelframe", background=self.CLR_BG_DARK,
                        bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure("TLabelframe.Label", background=self.CLR_BG_DARK,
                        foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)
        style.configure("TButton", font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor="none")
        style.map("TButton",
                  background=[("active", self.CLR_ACCENT_GOLD),
                              ("hover", self.CLR_ACCENT_GOLD)],
                  foreground=[("active", self.CLR_TEXT_DARK),
                              ("hover", self.CLR_TEXT_DARK)])
        style.configure("Start.TButton", background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK,
                        font=("Segoe UI", 13, "bold"))
        style.configure("Stop.TButton", background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.configure("green.Horizontal.TProgressbar",
                        background=self.CLR_ACCENT_GREEN)
        style.configure("Treeview", font=self.FONT_SMALL, rowheight=22,
                        background=self.CLR_INPUT_BG,
                        fieldbackground=self.CLR_INPUT_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook", background=self.CLR_BG_DARK)
        style.configure("TNotebook.Tab", font=self.FONT_BASE, padding=(12, 6))
        mpl.rcParams.update({
            "font.family": "Segoe UI",
            "font.size": self.FONT_SIZE_BASE,
            "axes.titlesize": self.FONT_SIZE_BASE + 2,
            "axes.labelsize": self.FONT_SIZE_BASE,
            "figure.facecolor": self.CLR_GRAPH_BG,
        })

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side="top", fill="x")
        tk.Label(header, text=f"Keysight E4980A: {PROGRAM_TITLE}",
                 bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT,
                 font=("Segoe UI", self.FONT_SIZE_BASE + 2, "bold", "italic")
                 ).pack(side="left", padx=20, pady=10)
        ttk.Button(header, text="Plotter", command=launch_plotter_utility
                   ).pack(side="right", padx=10, pady=5)
        ttk.Button(header, text="GPIB scanner", command=launch_gpib_scanner
                   ).pack(side="right", padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left.pack_propagate(False)
        self.main_pane.add(left, weight=0)
        right = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right, weight=1)

        # scrollable left column (sibling pattern)
        canvas = tk.Canvas(left, bg=self.CLR_BG_DARK, highlightthickness=0)
        sb = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win_id = canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win_id, width=e.width))
        self.left_scrollable_frame = sf
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._create_info_frame(sf).pack(fill="x", padx=10, pady=5)
        self._create_sample_frame(sf).pack(fill="x", padx=10, pady=5)
        self._create_field_frame(sf).pack(fill="x", padx=10, pady=5)
        self._create_thermo_frame(sf).pack(fill="x", padx=10, pady=5)
        self._create_action_frame(sf).pack(fill="x", padx=10, pady=5)
        self._create_advanced_frame(sf).pack(fill="x", padx=10, pady=5)
        self._create_log_frame(sf).pack(fill="both", expand=True, padx=10, pady=5)

        self._create_right_panel(right)
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Information")
        frame.grid_columnconfigure(1, weight=1)
        lc = tk.Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE,
                       bg=self.CLR_BG_DARK, highlightthickness=0)
        lc.grid(row=0, column=0, rowspan=2, padx=(15, 10), pady=8)
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (self.LOGO_SIZE, self.LOGO_SIZE), RESAMPLE_FILTER)
                self.logo_image = ImageTk.PhotoImage(img)
                lc.create_image(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                                image=self.logo_image)
            except Exception:
                pass
        f = ("Segoe UI", self.FONT_SIZE_BASE + 1, "bold")
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=f).grid(row=0, column=1, padx=10, pady=(8, 0), sticky="sw")
        ttk.Label(frame, text="Mumbai Centre", font=f
                  ).grid(row=1, column=1, padx=10, sticky="nw")
        return frame

    def _create_sample_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="What are you measuring?")
        frame.grid_columnconfigure(1, weight=1)
        ttk.Label(frame, text="The sample is called").grid(
            row=0, column=0, padx=10, pady=4, sticky="w")
        self.ent_sample = tk.Entry(frame, font=self.FONT_BASE)
        self.ent_sample.insert(0, "Sample")
        self.ent_sample.grid(row=0, column=1, padx=10, pady=4, sticky="ew")
        self.ent_sample.bind("<KeyRelease>", lambda e: self._refresh_headline())

        ttk.Label(frame, text="The PPMS is set to (K)").grid(
            row=1, column=0, padx=10, pady=4, sticky="w")
        self.ent_tnom = tk.Entry(frame, font=self.FONT_BASE, width=10)
        self.ent_tnom.insert(0, "300")
        self.ent_tnom.grid(row=1, column=1, padx=10, pady=4, sticky="w")
        self.ent_tnom.bind("<KeyRelease>", lambda e: self._refresh_headline())
        ttk.Label(frame, text="(nominal temperature; goes into the file name)",
                  font=self.FONT_SMALL).grid(
            row=2, column=0, columnspan=2, padx=10, pady=(0, 4), sticky="w")

        ttk.Label(frame, text="Save the files in").grid(
            row=3, column=0, padx=10, pady=4, sticky="w")
        row = ttk.Frame(frame)
        row.grid(row=3, column=1, padx=10, pady=4, sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        self.lbl_savedir = ttk.Label(row, text="(not chosen yet)",
                                     font=self.FONT_SMALL)
        self.lbl_savedir.grid(row=0, column=0, sticky="w")
        ttk.Button(row, text="Browse...", command=self._browse_save_dir
                   ).grid(row=0, column=1, sticky="e")
        return frame

    def _create_field_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Fields you will visit (Oe)")
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)

        lf = ttk.Frame(frame)
        lf.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=10, pady=5)
        sb = ttk.Scrollbar(lf, orient="vertical")
        self.listbox = tk.Listbox(lf, height=8, selectmode=tk.EXTENDED,
                                  font=self.FONT_CONSOLE, bg=self.CLR_INPUT_BG,
                                  fg=self.CLR_TEXT_DARK, yscrollcommand=sb.set,
                                  exportselection=False)
        sb.config(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda e: self._set_current_from_selection())

        ttk.Label(frame, text="Go from").grid(row=1, column=0, sticky="e", padx=2)
        self.ent_fstart = ttk.Entry(frame, width=9); self.ent_fstart.insert(0, "0")
        self.ent_fstart.grid(row=1, column=1, sticky="w", padx=2)
        ttk.Label(frame, text="to").grid(row=1, column=2, sticky="e", padx=2)
        self.ent_fend = ttk.Entry(frame, width=9); self.ent_fend.insert(0, "50000")
        self.ent_fend.grid(row=1, column=3, sticky="w", padx=2)

        self.gen_mode = tk.StringVar(value="step")
        ttk.Radiobutton(frame, text="in steps of", value="step",
                        variable=self.gen_mode).grid(row=2, column=0, sticky="e", padx=2)
        self.ent_fstep = ttk.Entry(frame, width=9); self.ent_fstep.insert(0, "10000")
        self.ent_fstep.grid(row=2, column=1, sticky="w", padx=2)
        ttk.Radiobutton(frame, text="using N fields:", value="points",
                        variable=self.gen_mode).grid(row=2, column=2, sticky="e", padx=2)
        self.ent_fpoints = ttk.Entry(frame, width=9); self.ent_fpoints.insert(0, "6")
        self.ent_fpoints.grid(row=2, column=3, sticky="w", padx=2)

        ttk.Button(frame, text="Add these fields to the list",
                   command=self._generate_fields).grid(
            row=3, column=0, columnspan=4, sticky="ew", padx=10, pady=(4, 2))

        ttk.Label(frame, text="Add one field:").grid(row=4, column=0, sticky="e", padx=2, pady=4)
        self.ent_fmanual = ttk.Entry(frame, width=9)
        self.ent_fmanual.grid(row=4, column=1, sticky="w", padx=2, pady=4)
        self.ent_fmanual.bind("<Return>", lambda e: self._add_manual_field())
        ttk.Button(frame, text="Add", command=self._add_manual_field
                   ).grid(row=4, column=2, sticky="ew", padx=2, pady=4)
        ttk.Button(frame, text="Remove selected", command=self._remove_fields
                   ).grid(row=4, column=3, sticky="ew", padx=2, pady=4)

        ttk.Button(frame, text="Move up", command=lambda: self._move_field(-1)
                   ).grid(row=5, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(frame, text="Move down", command=lambda: self._move_field(+1)
                   ).grid(row=5, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(frame, text="Reverse order", command=self._reverse_fields
                   ).grid(row=5, column=2, sticky="ew", padx=2, pady=2)
        ttk.Button(frame, text="Clear all", command=self._clear_fields
                   ).grid(row=5, column=3, sticky="ew", padx=2, pady=2)

        ttk.Button(frame, text="I am AT the selected field now (set as current)",
                   command=self._set_current_from_selection).grid(
            row=6, column=0, columnspan=4, sticky="ew", padx=10, pady=(6, 2))

        self.var_allow_repeats = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Allow the same field more than once "
                                    "(hysteresis loops: 0, +H, 0, -H, 0)",
                        variable=self.var_allow_repeats).grid(
            row=7, column=0, columnspan=4, sticky="w", padx=10, pady=(2, 6))
        ttk.Label(frame, text="Negative and decimal values are fine "
                              "(e.g. -500, 11.5). 10 kOe = 1 T.",
                  font=self.FONT_SMALL).grid(
            row=8, column=0, columnspan=4, sticky="w", padx=10, pady=(0, 4))
        return frame

    def _create_thermo_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Which thermometer reads the sample?")
        frame.grid_columnconfigure(1, weight=1)
        self.thermo_kind = tk.StringVar(value=THERMO_LS350)
        for r, (kind, text) in enumerate(((THERMO_LS350, "Lake Shore 350 (KRDG? A)"),
                                          (THERMO_CC34, "Cryo-con 34 (INPUT? A)"),
                                          (THERMO_NONE, "None - just measure"))):
            ttk.Radiobutton(frame, text=text, value=kind,
                            variable=self.thermo_kind,
                            command=self._on_thermo_kind_changed).grid(
                row=r, column=0, columnspan=2, sticky="w", padx=10, pady=2)
        ttk.Label(frame, text="at VISA address").grid(
            row=3, column=0, sticky="w", padx=10, pady=4)
        self.ent_thermo_addr = tk.Entry(frame, font=self.FONT_BASE)
        self.ent_thermo_addr.insert(0, THERMO_DEFAULT_ADDR[THERMO_LS350])
        self.ent_thermo_addr.grid(row=3, column=1, sticky="ew", padx=10, pady=4)
        row = ttk.Frame(frame)
        row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(2, 6))
        row.grid_columnconfigure(0, weight=1)
        self.btn_thermo = ttk.Button(row, text="Connect thermometer",
                                     command=self._connect_thermo)
        self.btn_thermo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(row, text="Disconnect", command=self._disconnect_thermo
                   ).grid(row=0, column=1, sticky="ew")
        ttk.Label(frame, text="Read-only: this program never writes to the "
                              "controller (no reset, no setpoint).",
                  font=self.FONT_SMALL).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))
        return frame

    def _create_action_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Measure")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)
        self.btn_measure = ttk.Button(frame, text="Measure this field now",
                                      command=self.start_measurement,
                                      style="Start.TButton")
        self.btn_measure.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="ew")
        self.btn_stop = ttk.Button(frame, text="Stop", command=self.stop_measurement,
                                   style="Stop.TButton", state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=(5, 10), pady=10, sticky="ew")
        self.var_auto_advance = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="After a scan, move the marker to the next field "
                                    "automatically", variable=self.var_auto_advance
                        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=2)
        self.var_beep = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Beep when a scan finishes or something goes wrong",
                        variable=self.var_beep).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))
        return frame

    def _create_advanced_frame(self, parent):
        outer = ttk.LabelFrame(parent, text="Advanced")
        outer.grid_columnconfigure(0, weight=1)
        self.var_show_adv = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text="Show the E4980A settings (defaults match the "
                                    "Frequency Scan module)",
                        variable=self.var_show_adv,
                        command=self._toggle_advanced).grid(
            row=0, column=0, sticky="w", padx=10, pady=4)
        adv = ttk.Frame(outer)
        adv.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        adv.grid_remove()
        self.adv_frame = adv
        for i in range(2):
            adv.grid_columnconfigure(i, weight=1)
        self.entries = {}

        def add(text, key, r, c, default):
            ttk.Label(adv, text=text).grid(row=r, column=c, padx=8, pady=(2, 0), sticky="w")
            e = tk.Entry(adv, font=self.FONT_BASE)
            e.insert(0, default)
            e.grid(row=r + 1, column=c, padx=8, pady=(0, 6), sticky="ew")
            self.entries[key] = e

        add("AC level (Vrms)", "ac_bias", 0, 0, "1.0")
        add("DC bias (V)", "dc_bias", 0, 1, "0.0")
        add("Delay per frequency point (s)", "delay", 2, 0, "0.2")
        ttk.Label(adv, text="Aperture (:APER)").grid(row=2, column=1, padx=8, pady=(2, 0), sticky="w")
        self.aper_combobox = ttk.Combobox(adv, font=self.FONT_BASE, state="readonly",
                                          values=["SHOR", "MED", "LONG"])
        self.aper_combobox.set("MED")
        self.aper_combobox.grid(row=3, column=1, padx=8, pady=(0, 6), sticky="ew")
        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(adv, text="Auto Level Control (ALC)", variable=self.var_alc
                        ).grid(row=4, column=0, columnspan=2, padx=8, pady=2, sticky="w")
        ttk.Checkbutton(adv, text="Open/Short corrections", variable=self.var_corr
                        ).grid(row=5, column=0, columnspan=2, padx=8, pady=2, sticky="w")
        ttk.Label(adv, text="Cable length (m)").grid(row=6, column=0, padx=8, pady=(4, 0), sticky="w")
        self.cable_len_combobox = ttk.Combobox(adv, font=self.FONT_BASE, state="readonly",
                                               values=["0", "1", "2", "4"])
        self.cable_len_combobox.set("1")
        self.cable_len_combobox.grid(row=7, column=0, padx=8, pady=(0, 6), sticky="ew")
        ttk.Label(adv, text="Thermometer channel").grid(row=6, column=1, padx=8, pady=(4, 0), sticky="w")
        self.ent_channel = tk.Entry(adv, font=self.FONT_BASE, width=6)
        self.ent_channel.insert(0, "A")
        self.ent_channel.grid(row=7, column=1, padx=8, pady=(0, 6), sticky="w")

        ttk.Label(adv, text="LCR meter VISA address").grid(
            row=8, column=0, columnspan=2, padx=8, pady=(6, 0), sticky="w")
        self.lcr_combobox = ttk.Combobox(adv, font=self.FONT_BASE)
        self.lcr_combobox.set("GPIB0::17::INSTR")
        self.lcr_combobox.grid(row=9, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="ew")
        self.btn_scan = ttk.Button(adv, text="Scan for instruments (*IDN?)",
                                   command=self._scan_for_visa)
        self.btn_scan.grid(row=10, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="ew")
        return outer

    def _toggle_advanced(self):
        if self.var_show_adv.get():
            self.adv_frame.grid()
        else:
            self.adv_frame.grid_remove()

    def _create_log_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Log")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            frame, state="disabled", bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap="word", bd=0, height=10)
        self.console.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        ttk.Button(frame, text="Save log to a text file", command=self._save_log
                   ).grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 5))
        ttk.Button(frame, text="Save 'measured so far' table", command=self._save_table
                   ).grid(row=1, column=1, sticky="ew", padx=5, pady=(0, 5))
        return frame

    def _create_right_panel(self, parent):
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        head = tk.Frame(parent, bg=self.CLR_HEADER)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        head.grid_columnconfigure(1, weight=1)

        self.lbl_temp = tk.Label(head, text="Sample temperature: -- K",
                                 font=self.FONT_HEADLINE, bg=self.CLR_HEADER,
                                 fg=self.CLR_TEXT_DARK, anchor="w")
        self.lbl_temp.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 0))
        self.lbl_temp_sub = tk.Label(head, text="no thermometer connected",
                                     font=self.FONT_SMALL, bg=self.CLR_HEADER,
                                     fg=self.CLR_FG_LIGHT, anchor="w")
        self.lbl_temp_sub.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16)

        self.lbl_field = tk.Label(head, text="Field selected: (none)",
                                  font=self.FONT_HEADLINE, bg=self.CLR_HEADER,
                                  fg=self.CLR_MEAS, anchor="w")
        self.lbl_field.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 0))
        self.lbl_next = tk.Label(head, text="Next field: (none)",
                                 font=self.FONT_BIG, bg=self.CLR_HEADER,
                                 fg=self.CLR_FG_LIGHT, anchor="w")
        self.lbl_next.grid(row=2, column=1, sticky="ew", padx=16, pady=(8, 0))

        self.lbl_file = tk.Label(head, text="Will write: --",
                                 font=self.FONT_BIG, bg=self.CLR_HEADER,
                                 fg=self.CLR_TEXT_DARK, anchor="w")
        self.lbl_file.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(6, 0))

        self.lbl_status = tk.Label(head, text="READY - pick a field, press Measure",
                                   font=("Segoe UI", 16, "bold"), bg=self.CLR_INPUT_BG,
                                   fg=self.CLR_TEXT_DARK, pady=8)
        self.lbl_status.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 4))
        self.progress = ttk.Progressbar(head, orient="horizontal", mode="determinate",
                                        style="green.Horizontal.TProgressbar")
        self.progress.grid(row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))

        nb = ttk.Notebook(parent)
        nb.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        plot_tab = tk.Frame(nb, bg=self.CLR_GRAPH_BG)
        table_tab = ttk.Frame(nb)
        nb.add(plot_tab, text="Live scan")
        nb.add(table_tab, text="Measured so far")
        self._create_graph(plot_tab)
        self._create_table(table_tab)

    def _create_graph(self, parent):
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_cp = self.figure.add_subplot(2, 1, 1)
        self.line_cp, = self.ax_cp.plot([], [], color="#C00000", marker="o",
                                        markersize=3, linestyle="-")
        self.ax_cp.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp.set_xscale("log")
        self.ax_cp.grid(True, linestyle="--", alpha=0.7)
        self.ax_g = self.figure.add_subplot(2, 1, 2, sharex=self.ax_cp)
        self.line_g, = self.ax_g.plot([], [], color=self.CLR_MEAS, marker="s",
                                      markersize=3, linestyle="-")
        self.ax_g.set_xlabel("Frequency (Hz)", fontsize=self.FONT_SIZE_BASE + 3,
                             fontweight="bold")
        self.ax_g.set_ylabel("Conductance, G (S)")
        self.ax_g.set_xscale("log")
        self.ax_g.grid(True, linestyle="--", alpha=0.7)
        for ax in (self.ax_cp, self.ax_g):
            ax.xaxis.set_major_formatter(EngFormatter(sep=""))
            ax.xaxis.set_minor_formatter(NullFormatter())
            ax.tick_params(axis="x", which="major", labelsize=self.FONT_SIZE_BASE + 1)
            ax.grid(True, which="major", axis="x", linestyle="-", linewidth=1.0, alpha=0.8)
        self.ax_cp.set_title("No scan yet")
        self.figure.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.09, hspace=0.24)

        bar = ttk.Frame(parent)
        bar.pack(anchor="w", padx=5, pady=(5, 0))
        ttk.Label(bar, text="Y scale:").pack(side="left")
        for text, val in (("Auto", "auto"), ("Log", "log"), ("Linear", "linear")):
            ttk.Radiobutton(bar, text=text, value=val, variable=self.y_scale_var,
                            command=self._on_y_scale_change).pack(side="left", padx=(8, 0))
        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _create_table(self, parent):
        cols = ("n", "field", "tnom", "tstart", "tend", "file", "status")
        heads = ("#", "Field (Oe)", "T nominal (K)", "T start (K)", "T end (K)",
                 "File", "Status")
        widths = (40, 100, 110, 110, 110, 420, 140)
        self.table = ttk.Treeview(parent, columns=cols, show="headings", height=12)
        for c, h, w in zip(cols, heads, widths):
            self.table.heading(c, text=h)
            self.table.column(c, width=w, anchor="w", stretch=(c == "file"))
        sb = ttk.Scrollbar(parent, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=sb.set)
        self.table.pack(side="left", fill="both", expand=True, padx=(5, 0), pady=5)
        sb.pack(side="right", fill="y", pady=5)

    # ------------------------------------------------------------------
    # Logging, banner, beep, keep-awake  (main thread only)
    # ------------------------------------------------------------------
    def log(self, message):
        """Thread-safe: queues the line; the pump writes it on the Tk thread."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._events.put(("log", f"[{ts}] {message}"))

    def _append_log_line(self, line):
        self._log_lines.append(line)
        self.console.config(state="normal")
        self.console.insert("end", line + "\n")
        try:  # bounded widget; the full text stays in self._log_lines
            if int(self.console.index("end-1c").split(".")[0]) > 5000:
                self.console.delete("1.0", "1000.0")
        except Exception:
            pass
        self.console.see("end")
        self.console.config(state="disabled")

    def _set_status(self, text, color=None):
        self.lbl_status.config(text=text, bg=color or self.CLR_INPUT_BG)

    def _beep(self, times=1):
        """MAIN THREAD ONLY (Tk is not thread-safe). winsound blocks, so it
        runs in a helper thread; root.bell() is the fallback."""
        if not self.var_beep.get():
            return
        if HAS_WINSOUND and platform.system() == "Windows":
            def _do():
                for _ in range(max(1, times)):
                    winsound.Beep(1000, 400)
                    time.sleep(0.2)
            threading.Thread(target=_do, daemon=True).start()
        else:
            try:
                self.root.bell()
            except Exception:
                pass

    def _set_keep_awake(self, enable):
        try:
            flags = self.ES_CONTINUOUS | (self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Field list handling
    # ------------------------------------------------------------------
    def _allow_repeats(self):
        return bool(self.var_allow_repeats.get())

    def _generate_fields(self):
        try:
            start = float(self.ent_fstart.get())
            end = float(self.ent_fend.get())
            if self.gen_mode.get() == "points":
                new = generate_field_list(start, end, points=int(float(self.ent_fpoints.get())))
            else:
                new = generate_field_list(start, end, step=float(self.ent_fstep.get()))
        except ValueError as e:
            messagebox.showerror("Field list", f"Please check the numbers: {e}")
            return
        self.fields, skipped = add_fields(self.fields, new, self._allow_repeats())
        if skipped:
            self.log(f"{skipped} field(s) were already in the list and were skipped "
                     "(tick 'Allow the same field more than once' for loops).")
        self.log(f"Added {len(new) - skipped} field(s): "
                 + ", ".join(format_field(v) for v in new) + " Oe")
        if self.current_idx is None and self.fields:
            self.current_idx = 0
        self._render_field_list()

    def _add_manual_field(self):
        try:
            v = float(self.ent_fmanual.get())
        except ValueError:
            messagebox.showerror("Field list", "Enter a field in Oe, e.g. -500 or 11.5.")
            return
        self.fields, skipped = add_fields(self.fields, [v], self._allow_repeats())
        if skipped:
            self.log(f"{format_field(v)} Oe is already in the list (skipped).")
        else:
            self.log(f"Added {format_field(v)} Oe.")
        self.ent_fmanual.delete(0, tk.END)
        if self.current_idx is None and self.fields:
            self.current_idx = 0
        self._render_field_list()

    def _remove_fields(self):
        sel = sorted(self.listbox.curselection(), reverse=True)
        if not sel:
            return
        for i in sel:
            del self.fields[i]
            if self.current_idx is not None:
                if i < self.current_idx:
                    self.current_idx -= 1
                elif i == self.current_idx:
                    self.current_idx = None
        if self.current_idx is None and self.fields:
            self.current_idx = 0
        if self.current_idx is not None and self.current_idx >= len(self.fields):
            self.current_idx = len(self.fields) - 1 if self.fields else None
        self._render_field_list()

    def _move_field(self, delta):
        sel = self.listbox.curselection()
        if len(sel) != 1:
            return
        i = sel[0]
        j = i + delta
        if not (0 <= j < len(self.fields)):
            return
        self.fields[i], self.fields[j] = self.fields[j], self.fields[i]
        if self.current_idx == i:
            self.current_idx = j
        elif self.current_idx == j:
            self.current_idx = i
        self._render_field_list(select=j)

    def _reverse_fields(self):
        if not self.fields:
            return
        n = len(self.fields)
        self.fields.reverse()
        if self.current_idx is not None:
            self.current_idx = n - 1 - self.current_idx
        self._render_field_list()

    def _clear_fields(self):
        self.fields = []
        self.current_idx = None
        self._render_field_list()
        self.log("Field list cleared.")

    def _set_current_from_selection(self):
        sel = self.listbox.curselection()
        if len(sel) != 1:
            messagebox.showinfo("Current field", "Select exactly one field in the list first.")
            return
        self.current_idx = sel[0]
        self.log(f"Current field set to #{sel[0] + 1}: "
                 f"{field_display(self.fields[sel[0]])}")
        self._render_field_list()

    def _render_field_list(self, select=None):
        self.listbox.delete(0, tk.END)
        for i, v in enumerate(self.fields):
            if i == self.current_idx:
                mark = ">> "
                tail = "   <- CURRENT (PPMS is at this field)"
            elif self.current_idx is not None and i == self.current_idx + 1:
                mark = " n "
                tail = "   <- next"
            else:
                mark = "   "
                tail = ""
            self.listbox.insert(tk.END, f"{mark}{i + 1:>3}.  {format_field(v):>10} Oe"
                                        f"  ({format_field(oe_to_tesla(v))} T){tail}")
        if self.current_idx is not None:
            self.listbox.itemconfig(self.current_idx, bg="#D8E8C8")
            if self.current_idx + 1 < len(self.fields):
                self.listbox.itemconfig(self.current_idx + 1, bg="#EDE4D0")
            self.listbox.see(self.current_idx)
        if select is not None:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(select)
        self._refresh_headline()

    # ------------------------------------------------------------------
    # Headline block
    # ------------------------------------------------------------------
    def _current_field(self):
        if self.current_idx is None or not (0 <= self.current_idx < len(self.fields)):
            return None
        return self.fields[self.current_idx]

    def _next_field(self):
        if self.current_idx is None:
            return None
        j = self.current_idx + 1
        return self.fields[j] if j < len(self.fields) else None

    def _nominal_T(self):
        try:
            return float(self.ent_tnom.get())
        except ValueError:
            return None

    def _refresh_headline(self):
        f = self._current_field()
        nxt = self._next_field()
        n = len(self.fields)
        if f is None:
            self.lbl_field.config(text="Field selected: (none - build the list)")
        else:
            self.lbl_field.config(text=f"Field selected: {field_display(f)}"
                                       f"   [{self.current_idx + 1} of {n}]")
        if nxt is None:
            self.lbl_next.config(text="Next field: (last one in the list)" if f is not None
                                 else "Next field: --")
        else:
            self.lbl_next.config(text=f"Next field: {field_display(nxt)}")
        t = self._nominal_T()
        if f is None or t is None:
            self.lbl_file.config(text="Will write: (choose a field and a nominal temperature)")
        else:
            key = (format_field(t), format_field(f))
            run_index = self.run_counts.get(key, 0) + 1
            name = build_filename(self.ent_sample.get(), t, f, "<time>", run_index)
            self.lbl_file.config(text=f"Will write: {name}")

    def _update_temp_headline(self, value, raw=""):
        self._last_T = value
        name = THERMO_NAMES.get(self._thermo_kind_connected, "")
        if self._thermo_kind_connected == THERMO_NONE:
            self.lbl_temp.config(text="Sample temperature: -- K (no thermometer)")
            self.lbl_temp_sub.config(text="no thermometer connected")
            return
        if value is None:
            self.lbl_temp.config(text="Sample temperature: no reading", fg=self.CLR_ACCENT_RED)
            self.lbl_temp_sub.config(text=f"{name} answered '{raw}' (sensor fault / out of range)")
        else:
            self.lbl_temp.config(text=f"Sample temperature: {value:.3f} K", fg=self.CLR_TEXT_DARK)
            self.lbl_temp_sub.config(
                text=f"{name} channel {self.ent_channel.get().strip().upper() or 'A'}, "
                     f"read {datetime.now().strftime('%H:%M:%S')}  |  {self._thermo_idn}")

    # ------------------------------------------------------------------
    # Thermometer handling (Tk side)
    # ------------------------------------------------------------------
    def _on_thermo_kind_changed(self):
        kind = self.thermo_kind.get()
        if kind in THERMO_DEFAULT_ADDR:
            self.ent_thermo_addr.delete(0, tk.END)
            self.ent_thermo_addr.insert(0, THERMO_DEFAULT_ADDR[kind])

    def _connect_thermo(self):
        kind = self.thermo_kind.get()
        addr = self.ent_thermo_addr.get().strip()
        ch = self.ent_channel.get().strip().upper() or "A"
        if kind == THERMO_NONE:
            self.cmd_queue.put(("disconnect_thermo",))
            self.log("Thermometer set to none.")
            return
        if not addr:
            messagebox.showerror("Thermometer", "Enter the VISA address of the controller.")
            return
        self.log(f"Connecting to {THERMO_NAMES[kind]} at {addr} (channel {ch})...")
        self.cmd_queue.put(("connect_thermo", kind, addr, ch))

    def _disconnect_thermo(self):
        self.cmd_queue.put(("disconnect_thermo",))

    def _schedule_T_poll(self):
        if self._close_requested:
            return
        if not self.is_running and self.thermo is not None:
            # only one outstanding poll at a time
            if not getattr(self, "_poll_pending", False):
                self._poll_pending = True
                self.cmd_queue.put(("poll_T",))
        self.root.after(self.T_POLL_MS, self._schedule_T_poll)

    # ------------------------------------------------------------------
    # Save folder / VISA scan / log saving
    # ------------------------------------------------------------------
    def _browse_save_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.save_dir = path
            self.lbl_savedir.config(text=path)
            self.log(f"Files will be saved in: {path}")

    def _scan_for_visa(self):
        if self.is_running:
            return
        self.cmd_queue.put(("scan_visa",))

    def _save_log(self):
        if not self._log_lines:
            return
        directory = self.save_dir or os.getcwd()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{sanitize_sample_name(self.ent_sample.get())}_FieldStep_log_{stamp}.txt"
        path = unique_path(directory, name)
        try:
            with open(path, "w", encoding="ascii", errors="replace") as fh:
                fh.write("\n".join(self._log_lines) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self.log(f"Log saved: {path}")
        except Exception as e:
            self.log(f"ERROR: could not save the log: {e}")

    def _save_table(self):
        if not self.records:
            return
        directory = self.save_dir or os.getcwd()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{sanitize_sample_name(self.ent_sample.get())}_FieldStep_summary_{stamp}.txt"
        path = unique_path(directory, name)
        try:
            with open(path, "w", encoding="ascii", errors="replace") as fh:
                fh.write("N\tH_set(Oe)\tT_nominal(K)\tT_start(K)\tT_end(K)\tFile\tStatus\n")
                for r in self.records:
                    fh.write(COL_SEP.join([str(r["n"]), format_field(r["field"]),
                                           format_field(r["t_nominal"]),
                                           _fmt_T(r["t_start"]), _fmt_T(r["t_end"]),
                                           r["file"], r["status"]]) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self.log(f"Summary table saved: {path}")
        except Exception as e:
            self.log(f"ERROR: could not save the table: {e}")

    # ------------------------------------------------------------------
    # Start / Stop  (idempotent)
    # ------------------------------------------------------------------
    def _collect_lcr_params(self):
        visa_val = self.lcr_combobox.get()
        visa_addr = visa_val.split("  ->  ")[0].strip() if "  ->  " in visa_val else visa_val.strip()
        return {
            "ac_bias": float(self.entries["ac_bias"].get()),
            "dc_bias": float(self.entries["dc_bias"].get()),
            "delay": float(self.entries["delay"].get()),
            "aper": self.aper_combobox.get(),
            "alc_enabled": bool(self.var_alc.get()),
            "corr_enabled": bool(self.var_corr.get()),
            "cable_len": self.cable_len_combobox.get(),
            "lcr_visa": visa_addr,
        }

    def start_measurement(self):
        if self.is_running:
            return                                   # idempotent
        field = self._current_field()
        t_nom = self._nominal_T()
        problems = []
        if field is None:
            problems.append("pick the field you are at in the list")
        if t_nom is None:
            problems.append("enter the nominal temperature in K")
        if not self.save_dir:
            problems.append("choose the folder to save in")
        try:
            params = self._collect_lcr_params()
            if not params["lcr_visa"]:
                problems.append("enter the E4980A VISA address (Advanced)")
        except ValueError as e:
            problems.append(f"check the E4980A settings ({e})")
            params = None
        if problems:
            messagebox.showerror("Not ready yet", "Before measuring, please:\n- "
                                 + "\n- ".join(problems))
            return

        key = (format_field(t_nom), format_field(field))
        run_index = self.run_counts.get(key, 0) + 1
        job = {
            "sample": self.ent_sample.get(),
            "t_nominal": t_nom,
            "field_oe": field,
            "field_idx": self.current_idx,
            "run_index": run_index,
            "save_dir": self.save_dir,
            "params": params,
            "frequencies": [float(f) for f in self.sweep_frequencies],
            "thermo_kind": self._thermo_kind_connected,
            "thermo_channel": self.ent_channel.get().strip().upper() or "A",
        }
        if run_index > 1:
            self.log(f"NOTE: {key[1]} Oe at T={key[0]} K was measured before in this "
                     f"session; this will be run {run_index} (new file, nothing overwritten).")
        if self.thermo is None and self.thermo_kind.get() != THERMO_NONE:
            self.log("WARNING: thermometer selected but not connected - the file "
                     "will say 'not recorded' for the measured temperature. "
                     "Press 'Connect thermometer' if you want it.")

        self.is_running = True
        self.stop_event.clear()
        self.btn_measure.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_scan.config(state="disabled")
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.sweep_frequencies)
        for k in self.plot_data:
            self.plot_data[k].clear()
        self._decade_ylims.clear()
        self.line_cp.set_data([], [])
        self.line_g.set_data([], [])
        self.ax_cp.set_title(f"Scanning: H = {format_field(field)} Oe, "
                             f"T nominal = {format_field(t_nom)} K")
        self.canvas.draw_idle()
        self._set_keep_awake(True)
        self._set_status(f"MEASURING  H = {format_field(field)} Oe  at  T = {format_field(t_nom)} K",
                         self.CLR_OK_GREEN)
        self.log(f"=== Measure pressed: field #{self.current_idx + 1} = "
                 f"{field_display(field)}, T nominal = {format_field(t_nom)} K, "
                 f"run {run_index} ===")
        self.cmd_queue.put(("measure", job))

    def stop_measurement(self):
        if not self.is_running:
            return
        self.stop_event.set()
        self.btn_stop.config(state="disabled")
        self._set_status("STOPPING after the current point...", self.CLR_ACCENT_GOLD)
        self.log("Stop requested by user.")

    def _finish_run_ui(self):
        self.is_running = False
        self._set_keep_awake(False)
        self.btn_measure.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.btn_scan.config(state="normal")

    # ------------------------------------------------------------------
    # Tk-side event pump  (Sensor_Curve_Viewer _post/_drain_events pattern)
    # ------------------------------------------------------------------
    def _post(self, *event):
        """Queue one request for the Tk thread. Safe from any thread."""
        self._events.put(event)

    def _drain_events(self):
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
                print(f"event {event[0]!r} failed: {exc}")
        if self._plot_dirty:
            self._plot_dirty = False
            self._refresh_plot()
        if not self._close_requested:
            try:
                self.root.after(self.EVENT_POLL_MS, self._drain_events)
            except tk.TclError:
                pass

    def _apply_event(self, event):
        kind = event[0]
        if kind == "log":
            self._append_log_line(event[1])
        elif kind == "status":
            self._set_status(event[1], event[2] if len(event) > 2 else None)
        elif kind == "beep":
            self._beep(event[1])
        elif kind == "temp":
            self._poll_pending = False
            self._update_temp_headline(event[1], event[2])
        elif kind == "thermo_connected":
            self._thermo_kind_connected = event[1]
            self._thermo_idn = event[2]
            self._poll_pending = False
            if event[1] == THERMO_NONE:
                self._update_temp_headline(None, "")
        elif kind == "visa_list":
            self.lcr_combobox["values"] = event[1]
            if event[2]:
                self.lcr_combobox.set(event[2])
        elif kind == "scan_started":
            self.lbl_file.config(text=f"Writing: {os.path.basename(event[1])}")
        elif kind == "point":
            _idx, f, cp, g = event[1], event[2], event[3], event[4]
            self.plot_data["freq"].append(f)
            self.plot_data["cp"].append(cp)
            self.plot_data["g"].append(g)
            self.progress["value"] = _idx + 1
            self._plot_dirty = True
            self._set_status(f"MEASURING  point {_idx + 1} / {len(self.sweep_frequencies)}"
                             f"   f = {f:,.0f} Hz", self.CLR_OK_GREEN)
        elif kind == "scan_done":
            self._on_scan_finished(event[1])
        elif kind == "scan_failed":
            self._on_scan_failed(event[1])

    def _on_scan_finished(self, rec):
        self._finish_run_ui()
        key = (format_field(rec["t_nominal"]), format_field(rec["field"]))
        self.run_counts[key] = self.run_counts.get(key, 0) + 1
        rec["n"] = len(self.records) + 1
        self.records.append(rec)
        self.table.insert("", "end", values=(
            rec["n"], format_field(rec["field"]), format_field(rec["t_nominal"]),
            _fmt_T(rec["t_start"]), _fmt_T(rec["t_end"]),
            os.path.basename(rec["file"]), rec["status"]))
        self.ax_cp.set_title(f"Last scan: H = {format_field(rec['field'])} Oe, "
                             f"T nominal = {format_field(rec['t_nominal'])} K "
                             f"({rec['status']})")
        self._plot_dirty = True
        stopped = rec["status"] != "complete"
        if stopped:
            self._set_status(f"STOPPED - {rec['points']} points saved to "
                             f"{os.path.basename(rec['file'])}", self.CLR_ACCENT_GOLD)
            self._beep(2)
        else:
            self._beep(1)
            if self.var_auto_advance.get() and rec.get("field_idx") == self.current_idx:
                if self.current_idx + 1 < len(self.fields):
                    self.current_idx += 1
                    nf = self.fields[self.current_idx]
                    self._set_status(f"DONE. Now set the PPMS to {format_field(nf)} Oe, "
                                     f"wait, then press Measure", self.CLR_INPUT_BG)
                    self.log(f"Marker moved to field #{self.current_idx + 1}: "
                             f"{field_display(nf)}. Set the PPMS there and press Measure.")
                else:
                    self._set_status("DONE - that was the LAST field in the list. "
                                     "Change T (and the nominal T box) or build a new list.",
                                     self.CLR_INPUT_BG)
                    self.log("All fields in the list are measured at this temperature.")
            else:
                self._set_status(f"DONE - saved {os.path.basename(rec['file'])}",
                                 self.CLR_INPUT_BG)
        self._render_field_list()

    def _on_scan_failed(self, message):
        self._finish_run_ui()
        self._set_status("SCAN FAILED - see the log", self.CLR_ACCENT_RED)
        self._beep(3)
        self._render_field_list()

    # ------------------------------------------------------------------
    # Plot
    # ------------------------------------------------------------------
    def _on_y_scale_change(self):
        self._decade_ylims.clear()
        self._refresh_plot()

    def _apply_y_scale(self, ax, values, key):
        """Adaptive Y scale (copied from Frequency_Scan_E4980A_GUI.py)."""
        mode = self.y_scale_var.get()
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        use_log = bool(pos) and mode != "linear"
        if use_log and mode == "auto":
            span = math.log10(max(pos)) - math.log10(min(pos))
            use_log = span >= 1.0
        if not use_log:
            ax.set_yscale("linear")
            ax.relim()
            ax.set_autoscaley_on(True)
            ax.autoscale_view(scaley=True)
            self._decade_ylims.pop(key, None)
            return False
        lo = 10.0 ** math.floor(math.log10(min(pos)))
        hi = 10.0 ** math.ceil(math.log10(max(pos)))
        if hi <= lo:
            hi = lo * 10.0
        cur = self._decade_ylims.get(key)
        if cur is not None:
            lo, hi = min(lo, cur[0]), max(hi, cur[1])
        if cur != (lo, hi):
            self._decade_ylims[key] = (lo, hi)
            ax.set_yscale("log")
            ax.set_ylim(lo, hi)
        return True

    def _refresh_plot(self):
        self.line_cp.set_data(self.plot_data["freq"], self.plot_data["cp"])
        self.line_g.set_data(self.plot_data["freq"], self.plot_data["g"])
        for ax, key in ((self.ax_cp, "cp"), (self.ax_g, "g")):
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)
            self._apply_y_scale(ax, self.plot_data[key], key)
        self.canvas.draw_idle()

    # ==================================================================
    # WORKER THREAD  - owns the E4980A and the thermometer
    # ==================================================================
    def _worker_loop(self):
        while not self._worker_quit.is_set():
            try:
                cmd = self.cmd_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            kind = cmd[0]
            try:
                if kind == "connect_thermo":
                    self._w_connect_thermo(cmd[1], cmd[2], cmd[3])
                elif kind == "disconnect_thermo":
                    self._w_disconnect_thermo()
                elif kind == "poll_T":
                    self._w_poll_T()
                elif kind == "scan_visa":
                    self._w_scan_visa()
                elif kind == "measure":
                    self._w_measure(cmd[1])
                elif kind == "quit":
                    break
            except Exception as e:
                self.log(f"ERROR in worker ({kind}): {e}\n{traceback.format_exc()}")
                if kind == "measure":
                    try:
                        self.lcr.close_instrument()
                    except Exception:
                        pass
                    self._post("scan_failed", str(e))
                elif kind == "poll_T":
                    self._post("temp", None, f"comm error: {e}")
        try:
            self.lcr.close_instrument()
        except Exception:
            pass
        if self.thermo is not None:
            try:
                self.thermo.close()
            except Exception:
                pass

    # --- thermometer ---
    def _w_connect_thermo(self, kind, addr, channel):
        if self.thermo is not None:
            try:
                self.thermo.close()
            except Exception:
                pass
            self.thermo = None
        try:
            link = open_thermometer(kind, addr, channel, log=self.log)
        except Exception as e:
            self.log(f"ERROR: could not connect to {THERMO_NAMES[kind]} at {addr}: {e}")
            self._post("thermo_connected", THERMO_NONE, "")
            self._post("beep", 2)
            return
        self.thermo = link
        self.log(f"Connected: {THERMO_NAMES[kind]} at {addr} -> {link.idn}")
        self._post("thermo_connected", kind, link.idn)
        value, raw = self._w_read_T_tolerant()
        self._post("temp", value, raw)
        if value is None:
            self.log(f"WARNING: {THERMO_NAMES[kind]} answered '{raw}' - no valid reading.")
        else:
            self.log(f"First reading: {value:.4f} K")

    def _w_disconnect_thermo(self):
        if self.thermo is not None:
            try:
                self.thermo.close()
            except Exception:
                pass
            self.thermo = None
            self.log("Thermometer disconnected (session closed only).")
        self._post("thermo_connected", THERMO_NONE, "")

    def _w_read_T_tolerant(self):
        """(kelvin or None, raw). Comm errors -> one reconnect attempt,
        then None. Never raises (a thermometer glitch must not kill a scan)."""
        if self.thermo is None:
            return None, ""
        for attempt in (1, 2):
            try:
                return self.thermo.read_temperature()
            except Exception as e:
                self.log(f"Thermometer read failed ({type(e).__name__}: {e})"
                         + (" - reconnecting once." if attempt == 1 else "."))
                if attempt == 1:
                    try:
                        self.thermo.reconnect()
                        self.log("Thermometer session re-opened.")
                    except Exception as e2:
                        self.log(f"Thermometer reconnect failed: {e2}")
                        return None, f"comm error: {e2}"
        return None, "comm error"

    def _w_poll_T(self):
        value, raw = self._w_read_T_tolerant()
        self._post("temp", value, raw)

    # --- VISA scan ---
    def _w_scan_visa(self):
        if not PYVISA_AVAILABLE or self.lcr.rm is None:
            self.log("ERROR: PyVISA/VISA manager unavailable.")
            self._post("visa_list", [], None)
            return
        self.log("Scanning for VISA instruments (querying *IDN?)...")
        rm = self.lcr.rm
        found, e4980_label = [], None
        try:
            for res in rm.list_resources():
                if not str(res).upper().startswith(("GPIB", "USB", "TCPIP")):
                    continue
                idn = "Unknown / no response"
                try:
                    with rm.open_resource(res) as dev:
                        dev.timeout = 2000
                        dev.read_termination = "\n"
                        dev.write_termination = "\n"
                        idn = dev.query("*IDN?").strip()
                except Exception:
                    pass
                label = f"{res}  ->  {idn}"
                found.append(label)
                if "E4980" in idn and e4980_label is None:
                    e4980_label = label
                self.log(f"  {label}")
        except Exception as e:
            self.log(f"ERROR during scan: {e}")
        if e4980_label:
            self.log("E4980A auto-selected.")
        elif not found:
            self.log("No VISA instruments found.")
        self._post("visa_list", found, e4980_label)

    # --- E4980A connection with retry ---
    def _w_wait_backoff(self, attempt):
        delay = self.RECONNECT_BACKOFF_S[min(attempt - 1, len(self.RECONNECT_BACKOFF_S) - 1)]
        self._post("status", f"E4980A NOT ANSWERING - retry #{attempt} in {delay} s "
                             "(Stop to give up)", self.CLR_ACCENT_RED)
        deadline = time.time() + delay
        while time.time() < deadline:
            if self.stop_event.is_set():
                return False
            time.sleep(0.5)
        return True

    def _w_connect_lcr(self, params):
        """Retry until the E4980A is configured or Stop is pressed."""
        attempt = 1
        while not self.stop_event.is_set():
            try:
                idn = self.lcr.initialize_instrument(params)
                self.log(f"E4980A connected and configured (RX mode): {idn}")
                return True
            except ValueError as e:
                # a safety-limit refusal is a settings error, not a comm error
                self.log(f"ERROR: E4980A refused the settings: {e}")
                try:
                    self.lcr.close_instrument()
                except Exception:
                    pass
                return False
            except Exception as e:
                self.log(f"E4980A connection attempt #{attempt} failed: {e}")
                try:
                    self.lcr.close_instrument()
                except Exception:
                    pass
                if attempt == 1:
                    self._post("beep", 2)
                if not self._w_wait_backoff(attempt):
                    return False
                attempt += 1
        return False

    def _w_comm_recover(self, context, err):
        """Retry-forever reconnect of the E4980A (full re-configuration,
        bias re-ramped) with escalating backoff. False only on Stop."""
        self.log(f"COMM ERROR during {context}: {err} - reconnecting "
                 "(Stop stays responsive).")
        self._post("beep", 2)
        attempt = 1
        while not self.stop_event.is_set():
            if not self._w_wait_backoff(attempt):
                return False
            try:
                idn = self.lcr.reconnect()
                self.log(f"E4980A reconnected after {attempt} attempt(s): {idn}. "
                         "Retrying the same frequency point.")
                return True
            except Exception as e:
                self.log(f"Reconnect attempt #{attempt} failed: {e}")
                attempt += 1
        return False

    # --- the measurement itself ---
    @staticmethod
    def _durable_write(fh, text):
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())

    def _w_measure(self, job):
        p = job["params"]
        freqs = job["frequencies"]
        field = job["field_oe"]
        t_nom = job["t_nominal"]
        started = datetime.now()
        stamp = started.strftime("%Y%m%d_%H%M%S")

        self._post("status", "CONNECTING to the E4980A...", self.CLR_ACCENT_GOLD)
        if not self._w_connect_lcr(p):
            self._post("scan_failed", "E4980A not connected")
            self._post("status", "NOT MEASURED - E4980A not connected (see log)",
                       self.CLR_ACCENT_RED)
            return

        t_start, raw = self._w_read_T_tolerant()
        if self.thermo is not None:
            self._post("temp", t_start, raw)
        self.log(f"Measured T at start: {_fmt_T(t_start)} K"
                 + (f"  (raw '{raw}')" if t_start is None and raw else ""))

        fname = build_filename(job["sample"], t_nom, field, stamp, job["run_index"])
        path = unique_path(job["save_dir"], fname)
        info = {
            "sample": ascii_only(job["sample"]), "t_nominal": t_nom, "field_oe": field,
            "run_index": job["run_index"],
            "controller": THERMO_NAMES.get(job["thermo_kind"], "none"),
            "controller_idn": (self.thermo.idn if self.thermo is not None else "n/a"),
            "controller_channel": job["thermo_channel"],
            "lcr_idn": self.lcr.idn,
            "t_start": t_start, "t_end": None, "t_min": None, "t_max": None,
            "ac_bias": p["ac_bias"], "dc_bias": p["dc_bias"], "aper": p["aper"],
            "alc": p["alc_enabled"], "corr": p["corr_enabled"],
            "cable_len": p["cable_len"], "delay": p["delay"],
            "n_points": len(freqs), "f_min": f"{freqs[0]:g}", "f_max": f"{freqs[-1]:g}",
            "started": started.strftime("%Y-%m-%d %H:%M:%S"), "finished": "in progress",
            "filename": os.path.basename(path), "status": "in progress",
        }
        with open(path, "w", encoding="ascii", errors="replace") as fh:
            self._durable_write(fh, "\n".join(build_header_lines(info)) + "\n")
        self.log(f"File created: {path}")
        self._post("scan_started", path)

        rows = []
        t_min = t_max = t_start
        last_T_poll = time.time()
        idx = 0
        status_word = "complete"
        with open(path, "a", encoding="ascii", errors="replace") as fh:
            while idx < len(freqs):
                if self.stop_event.is_set():
                    status_word = f"stopped after {idx} of {len(freqs)} points"
                    break
                f = freqs[idx]
                try:
                    R, X, st = self.lcr.perform_measurement(f, p["delay"])
                except Exception as e:
                    if self._w_comm_recover(f"E4980A measurement @ {f:g} Hz", e):
                        continue                       # retry this point
                    status_word = f"stopped during reconnect after {idx} points"
                    break
                if st != 0:
                    self.log(f"WARNING: f={f:g} Hz returned status {st} "
                             "(0=normal, non-zero=overload/ALC issue).")
                try:
                    vals = calculate_impedance_parameters(f, R, X)
                except Exception as calc_err:
                    self.log(f"Calc error at {f:g} Hz: {calc_err}; NaN for derived values.")
                    vals = [float("nan")] * 18
                row = [f] + vals + [t_nom, field]
                row_str = COL_SEP.join(f"{v:.6E}" for v in row)
                rows.append(row_str)
                self._durable_write(fh, row_str + "\n")     # fsync per point
                self._post("point", idx, f, vals[4], vals[2])
                idx += 1
                # live temperature between points (cheap query)
                if self.thermo is not None and time.time() - last_T_poll >= self.T_POLL_IN_SCAN_S:
                    last_T_poll = time.time()
                    tv, traw = self._w_read_T_tolerant()
                    self._post("temp", tv, traw)
                    if tv is not None:
                        t_min = tv if t_min is None else min(t_min, tv)
                        t_max = tv if t_max is None else max(t_max, tv)

        t_end, raw = self._w_read_T_tolerant()
        if self.thermo is not None:
            self._post("temp", t_end, raw)
        if t_end is not None:
            t_min = t_end if t_min is None else min(t_min, t_end)
            t_max = t_end if t_max is None else max(t_max, t_end)
        self.log(f"Measured T at end: {_fmt_T(t_end)} K"
                 + (f"  (raw '{raw}')" if t_end is None and raw else ""))

        finished = datetime.now()
        info.update({"t_end": t_end, "t_min": t_min, "t_max": t_max,
                     "finished": finished.strftime("%Y-%m-%d %H:%M:%S"),
                     "status": status_word})
        # Rewrite the file with the completed header (T_end etc.), atomically:
        # write a temp file, fsync, then replace. Rows are already on disk in
        # the original, so a crash here loses nothing.
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="ascii", errors="replace") as fh:
                self._durable_write(fh, "\n".join(build_header_lines(info)) + "\n"
                                    + "\n".join(rows) + ("\n" if rows else ""))
            os.replace(tmp, path)
        except Exception as e:
            self.log(f"WARNING: could not update the file header with T_end: {e} "
                     "(data rows are intact).")

        try:
            self.lcr.close_instrument()
            self.log("E4980A: bias off, session closed.")
        except Exception as e:
            self.log(f"WARNING while closing the E4980A: {e}")

        self.log(f"Scan {status_word}: {idx} points, H = {format_field(field)} Oe, "
                 f"T nominal = {format_field(t_nom)} K, T measured "
                 f"{_fmt_T(t_start)} -> {_fmt_T(t_end)} K, file {os.path.basename(path)}")
        self._post("scan_done", {
            "field": field, "field_idx": job["field_idx"], "t_nominal": t_nom,
            "t_start": t_start, "t_end": t_end, "file": path,
            "status": status_word, "points": idx,
        })

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def _atexit_cleanup(self):
        try:
            self.lcr.close_instrument()
        except Exception:
            pass

    def _on_closing(self):
        if self.is_running:
            if not messagebox.askyesno("Exit", "A scan is running. Stop it and exit?"):
                return
            self.stop_event.set()
        self._close_requested = True
        self._worker_quit.set()
        self.cmd_queue.put(("quit",))
        self._close_deadline = time.time() + 15.0
        self._poll_worker_exit_then_destroy()

    def _poll_worker_exit_then_destroy(self):
        if self.worker.is_alive() and time.time() < self._close_deadline:
            self.root.after(200, self._poll_worker_exit_then_destroy)
            return
        self.root.destroy()


# ===============================================================================
# ENTRY POINT
# ===============================================================================

def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Dependency Error",
                             "PyVISA is not installed.\n\npip install pyvisa")
        return
    root = tk.Tk()
    FieldStepFreqScanGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
