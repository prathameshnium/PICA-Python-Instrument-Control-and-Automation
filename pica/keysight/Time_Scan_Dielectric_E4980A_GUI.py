"""
Module:             Time_Scan_Dielectric_E4980A_GUI.py
Purpose:            Continuous, time-stamped dielectric measurement with the
                    Keysight E4980A. Sweeps a frequency list over and over,
                    from Start until the user presses Stop.
Original Authors:   Prathamesh Deshmukh (template programs)
Base program:       Temprature_Scan_Passive_E4980A_GUI.py (v1.4)
Version:            V: 1.0

WHAT IT DOES
  - No temperature instrument is connected or read. Temperature comes from
    the PPMS's own log; the two are matched afterwards by time stamp.
  - Each point is time-stamped RIGHT AFTER its reading comes back from the
    LCR (after :FETC?), with local date-time (ms), Unix epoch seconds and
    elapsed seconds since Start.
  - Default list: 100 Hz to 2 kHz in 100 Hz steps = 20 points. A custom
    list (comma / space / semicolon / newline separated, e.g. pasted from
    the PICA List Maker) is used EXACTLY as typed: order and repeats kept.
  - Delay per frequency as in the T-scan (default 0.1 s); sweeps run back
    to back with no pause between them, as in the T-scan.

FILES (tab-separated, all in the chosen folder)
  <sample>-<f>Hz.txt   one per distinct frequency, T-scan layout with the
                       Temperature column replaced by three time columns:
                       DateTime  Epoch_s  Elapsed_s  + the 18 parameters
  <sample>_Master.txt  one row per measured point, in measurement order:
                       DateTime  Epoch_s  Elapsed_s  Sweep  Point
                       Frequency(Hz)  Status  + the 18 parameters
  <sample>_RunInfo.txt settings and the frequency list used for the run
  If any of these names already exists, a date-time tag is added to the
  sample name instead of overwriting.

  The 18 parameters and their formulas are UNCHANGED from the T-scan
  program (calculate_impedance_parameters, copied verbatim).

UNATTENDED-RUN SAFETY (carried over from the passive T-scan v1.4)
  - Every point is written and fsync'd to disk as soon as it is measured,
    by the worker thread, so a GUI stall or a power cut loses nothing
    already measured. A disk / network-share hiccup buffers rows in memory
    and retries on the next point.
  - VISA / GPIB errors never end the run: close, back off (5/10/30/60 s),
    re-initialise the LCR from the stored settings and repeat the SAME
    frequency point. Stop stays responsive during the back-off.
  - Windows keep-awake while running; console bounded; plot thinning ON
    by default (plot only; files keep every point); no modal dialogs on
    unattended events (console log + beeps only).
  - The worker thread OWNS the LCR for the whole run and is the only
    thing that closes it: on Stop / error / exit it flushes the buffer,
    ramps the DC bias to 0 V and switches it off. The GUI waits for it
    (no give-up deadline), Start stays refused until it has exited, and
    each run gets a fresh stop Event — so a slow reconnect can never
    leave the bias on, and two workers can never share the instrument.
  - Any failure during Start closes the LCR (bias off). After the final
    close at program exit, no reconnect can re-open the instrument.
  - Writes are all-or-nothing (rolled back on a failed write/fsync), so
    a retry never duplicates a row; a full buffer drops whole points
    from every file together and reports it while running.
"""

# ===============================================================================
# IMPORTS
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
# CONSTANTS
# ===============================================================================

PROGRAM_VERSION = "1.0"

E4980A_F_MIN = 20.0       # Hz
E4980A_F_MAX = 2.0e6      # Hz

# 100 Hz to 2 kHz in 100 Hz steps: (2000 - 100) / 100 + 1 = 20 points
DEFAULT_FREQ_LIST = [float(f) for f in range(100, 2001, 100)]
DEFAULT_FREQS_TEXT = ", ".join(str(int(f)) for f in DEFAULT_FREQ_LIST)

# The 18 derived parameters, same names and order as the T-scan header
PARAM_HEADER = (
    "Q\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\t"
    "theta\tchi\tR(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\t"
    "Cp''\tCs''"
)
N_PARAMS = 18

TIME_HEADER = "DateTime\tEpoch_s\tElapsed_s"
FREQ_FILE_HEADER = TIME_HEADER + "\t" + PARAM_HEADER                 # 21 cols
MASTER_FILE_HEADER = (TIME_HEADER + "\tSweep\tPoint\tFrequency(Hz)\tStatus\t"
                      + PARAM_HEADER)                                # 25 cols

# Index of Cp, D, G inside the 18-parameter list
IDX_D, IDX_G, IDX_CP = 1, 2, 4

RECONNECT_BACKOFFS_S = (5, 10, 30, 60)
INVALID_NAME_CHARS = set('\\/:*?"<>|')


# ===============================================================================
# UTILITY LAUNCH FUNCTIONS  (verbatim pattern from source programs)
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


def _launch_util(filename, title):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, "..", "utils", filename)
        if not os.path.exists(path):
            messagebox.showerror(
                "File Not Found",
                f"{title} not found at expected path:\n{path}")
            return
        Process(target=run_script_process, args=(path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch {title}: {e}")


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    _launch_util("PlotterUtil_GUI.py", "Plotter utility")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    _launch_util("GPIB_Instrument_Scanner_GUI.py", "GPIB Scanner")


def launch_list_maker():
    """Finds and launches the PICA List Maker in a new process."""
    _launch_util("List_Maker_GUI.py", "List Maker")


# ===============================================================================
# PURE HELPERS  (no Tk, no VISA — unit-testable)
# ===============================================================================

def calculate_impedance_parameters(f, R, X):
    """
    VERBATIM copy of calculate_impedance_parameters() from
    Temprature_Scan_Passive_E4980A_GUI.py — formulas deliberately unchanged.

    Calculates all 18 parameters from measured R (series resistance)
    and X (reactance).

    Complex impedance:  Z = R + jX
    Complex admittance: Y = 1/Z = (R - jX) / |Z|^2
                       G = Re(Y) = R / |Z|^2
                       B = Im(Y) = -X / |Z|^2

    Returns list of 18 values in PARAM_HEADER column order:
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


_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$",
                        re.ASCII)


def normalize_freq(f):
    """Round to 12 significant digits (far below the E4980A's own
    frequency resolution) so that every distinct value has a distinct,
    exact text form for file names and the Frequency column."""
    return float(f"{float(f):.12g}")


def parse_frequency_list(text):
    """Parse a frequency list EXACTLY as typed: order and repeats are kept.

    Separators: comma, semicolon, space, tab or newline (so a List Maker
    output pastes straight in). Plain ASCII decimal or scientific numbers
    only. Every value must lie in the E4980A range 20 Hz - 2 MHz.
    Raises ValueError with a readable message."""
    text = text.replace("﻿", "")            # BOM from a saved .txt
    tokens = [t for t in re.split(r"[,;\s]+", text.strip()) if t]
    if not tokens:
        raise ValueError("Frequency list is empty.")
    freqs = []
    for tok in tokens:
        if not _NUMBER_RE.match(tok):
            raise ValueError(f"'{tok}' is not a number (frequencies in Hz).")
        f = float(tok)
        if not math.isfinite(f) or not (E4980A_F_MIN <= f <= E4980A_F_MAX):
            raise ValueError(
                f"Frequency {tok} Hz outside E4980A range (20 Hz - 2 MHz).")
        freqs.append(normalize_freq(f))
    return freqs


def unique_in_order(values):
    """Distinct values, first-seen order (one file per distinct frequency)."""
    seen, out = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def freq_tag(f):
    """Exact text form of a (normalised) frequency, used for file names
    and the Frequency(Hz) column: 100 -> '100', 150.5 -> '150.5',
    2e6 -> '2000000'. 12 significant digits; never an exponent in the
    20 Hz - 2 MHz range. (Never int(): 100.2 and 100.7 must not merge.)"""
    f = float(f)
    if f.is_integer():
        return str(int(f))
    return f"{f:.12g}"


def epoch_ms_parts(epoch_s):
    """(datetime_text, epoch_text) from ONE millisecond rounding, so the
    DateTime and Epoch_s columns can never disagree."""
    total_ms = int(round(epoch_s * 1000.0))
    sec, ms = divmod(total_ms, 1000)
    dt = datetime.fromtimestamp(sec)
    return (dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{ms:03d}",
            f"{sec}.{ms:03d}")


def format_datetime_ms(epoch_s):
    """Local date-time with milliseconds, from Unix epoch seconds."""
    return epoch_ms_parts(epoch_s)[0]


def format_params(calc):
    return "\t".join("{:.6E}".format(v) for v in calc)


def format_freq_row(rec, calc):
    """<sample>-<f>Hz.txt row: DateTime Epoch_s Elapsed_s + 18 params.
    Epoch/Elapsed use fixed 3 decimals (ms): %.6E would round an epoch
    (~1.8e9 s) to the nearest 1000 s, useless for matching the PPMS log."""
    return (f"{rec['datetime']}\t{rec['epoch_text']}\t{rec['elapsed']:.3f}\t"
            f"{format_params(calc)}\n")


def format_master_row(rec, calc):
    """<sample>_Master.txt row: time cols, Sweep, Point, Frequency, Status
    + 18 params. Frequency is written exactly (same text as the file
    name), not %.6E, so it never loses digits."""
    return (f"{rec['datetime']}\t{rec['epoch_text']}\t{rec['elapsed']:.3f}\t"
            f"{rec['sweep']:d}\t{rec['point']:d}\t{freq_tag(rec['freq'])}\t"
            f"{rec['status']:d}\t{format_params(calc)}\n")


def make_record(epoch, elapsed, sweep, point, freq, status, R=None, X=None):
    dt_text, epoch_text = epoch_ms_parts(epoch)
    return {"datetime": dt_text, "epoch_text": epoch_text, "epoch": epoch,
            "elapsed": elapsed, "sweep": sweep, "point": point,
            "freq": freq, "status": int(status), "R": R, "X": X}


def safe_calc(f, R, X):
    """Run the (unchanged) formulas; NaNs instead of an exception."""
    try:
        return calculate_impedance_parameters(f, R, X), None
    except Exception as e:
        return [float("nan")] * N_PARAMS, e


# ===============================================================================
# DURABLE DATA WRITER  (used by the worker thread only; never touches Tk)
# ===============================================================================

class DataWriter:
    """Creates the run's files and appends one point at a time, each
    write fsync'd. On OSError (disk full, network share dropped) the
    point is buffered in memory and retried, oldest first, before every
    following point. Methods return a list of log messages instead of
    logging, so this class never touches Tk (worker-thread safe).

    Guarantees:
      - A failed write/fsync is rolled back (file truncated to its size
        before the write), so a retry can never duplicate or splice a row.
      - Per-file row order is preserved.
      - The buffer holds whole POINTS: if it is full, the oldest whole
        point is dropped from every file together (per-frequency and
        master files stay consistent), and the drop is reported at once.
    """

    MAX_PENDING_POINTS = 100000   # ~50 MB of text (~18 h at 1.5 pt/s)
    FLUSH_BATCH_POINTS = 2000     # backlog retried per measured point
    STILL_FAILING_EVERY = 500     # repeat the warning every N points

    def __init__(self, folder, sample_name, frequencies):
        self.folder = folder
        self.frequencies = list(frequencies)
        self.distinct = unique_in_order(self.frequencies)
        self.sample_tag = sample_name
        self.freq_paths = {}
        self.master_path = None
        self.info_path = None
        # each entry is one point: [[path, row, written_flag], ...]
        self._pending = deque()
        self._waiting = {}        # path -> number of unwritten rows queued
        self._error_active = False
        self._points_while_failing = 0
        self.dropped_points = 0
        self.rows_written = 0     # master rows confirmed on disk

    def _paths_for(self, tag):
        freq_paths = {
            f: os.path.join(self.folder, f"{tag}-{freq_tag(f)}Hz.txt")
            for f in self.distinct}
        master = os.path.join(self.folder, f"{tag}_Master.txt")
        info = os.path.join(self.folder, f"{tag}_RunInfo.txt")
        return freq_paths, master, info

    def create_files(self, run_info_text=""):
        """Create all files with headers. Never overwrites: if any name
        exists, a date-time tag is appended to the sample name. Returns
        log messages."""
        msgs = []
        tag = self.sample_tag
        freq_paths, master, info = self._paths_for(tag)

        def taken(fp, m, i):
            return any(os.path.exists(p) for p in list(fp.values()) + [m, i])

        if taken(freq_paths, master, info):
            base = f"{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            tag, n = base, 1
            freq_paths, master, info = self._paths_for(tag)
            while taken(freq_paths, master, info):
                n += 1
                tag = f"{base}_{n}"
                freq_paths, master, info = self._paths_for(tag)
            msgs.append(f"Existing files detected. Using unique sample "
                        f"tag: {tag}")
        self.sample_tag = tag
        self.freq_paths, self.master_path, self.info_path = \
            freq_paths, master, info

        for path in self.freq_paths.values():
            self._durable_write(path, FREQ_FILE_HEADER + "\n", mode="w")
        self._durable_write(self.master_path, MASTER_FILE_HEADER + "\n",
                            mode="w")
        self._durable_write(self.info_path, run_info_text, mode="w")
        return msgs

    @staticmethod
    def _durable_write(path, text, mode="a"):
        """Append (or create, mode='w') text and fsync it to disk.
        All-or-nothing: on any OSError the file is truncated back to its
        previous size before the error is re-raised. Line endings follow
        the platform, as text-mode writes in the T-scan do."""
        data = text.replace("\n", os.linesep).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
        flags |= os.O_TRUNC if mode == "w" else os.O_APPEND
        fd = os.open(path, flags, 0o666)
        try:
            start = os.lseek(fd, 0, os.SEEK_END)
            try:
                view = memoryview(data)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                os.fsync(fd)
            except OSError:
                try:
                    os.ftruncate(fd, start)
                except OSError:
                    pass
                raise
        finally:
            os.close(fd)

    @property
    def pending_count(self):
        """Rows (not points) still waiting for the disk."""
        return sum(self._waiting.values())

    def _mark_written(self, item, queued=True):
        item[2] = True
        if queued:
            self._waiting[item[0]] -= 1
        if item[0] == self.master_path:
            self.rows_written += 1

    def _enqueue(self, group):
        self._pending.append(group)
        for it in group:
            if not it[2]:
                self._waiting[it[0]] = self._waiting.get(it[0], 0) + 1

    def _drop_oldest(self):
        for it in self._pending.popleft():
            if not it[2]:
                self._waiting[it[0]] -= 1

    def _flush_batch(self, limit):
        """Write up to `limit` oldest buffered points: one write + fsync
        per file. Returns True if every attempted file succeeded."""
        by_path = {}
        for i, group in enumerate(self._pending):
            if i >= limit:
                break
            for item in group:
                if not item[2]:
                    by_path.setdefault(item[0], []).append(item)
        ok = True
        for path, items in by_path.items():
            try:
                self._durable_write(path, "".join(it[1] for it in items))
            except OSError:
                ok = False
                continue
            for it in items:
                self._mark_written(it)
        while self._pending and all(it[2] for it in self._pending[0]):
            self._pending.popleft()
        return ok

    def flush_pending(self, limit=None):
        """Retry buffered points (oldest first). limit=None: everything
        (used at Stop). Returns log messages."""
        msgs = []
        while self._pending:
            before = len(self._pending)
            ok = self._flush_batch(limit or self.FLUSH_BATCH_POINTS)
            if not ok or limit is not None or len(self._pending) == before:
                break
        if self._error_active and not self._pending:
            self._error_active = False
            self._points_while_failing = 0
            msgs.append("Write path recovered; buffered rows flushed.")
        return msgs

    def write_point(self, rec, calc):
        """Append one point to its frequency file and the master file.
        Returns log messages."""
        msgs = self.flush_pending(limit=self.FLUSH_BATCH_POINTS)
        group = [[self.freq_paths[rec["freq"]], format_freq_row(rec, calc),
                  False],
                 [self.master_path, format_master_row(rec, calc), False]]
        failed = None
        for item in group:
            # Keep per-file order: if older rows for this file are still
            # buffered, this row must wait behind them.
            if self._waiting.get(item[0], 0):
                continue
            try:
                self._durable_write(item[0], item[1])
                self._mark_written(item, queued=False)
            except OSError as e:
                failed = e
        if all(it[2] for it in group):
            return msgs

        if len(self._pending) >= self.MAX_PENDING_POINTS:
            self._drop_oldest()
            self.dropped_points += 1
            if self.dropped_points == 1 or self.dropped_points % 1000 == 0:
                msgs.append(f"DATA LOSS: write buffer full; "
                            f"{self.dropped_points} oldest points dropped "
                            "so far (disk still unavailable).")
        self._enqueue(group)
        if failed is not None and not self._error_active:
            self._error_active = True
            msgs.append(f"WRITE ERROR: {failed} — buffering points in "
                        "memory and retrying before every point.")
        if self._error_active:
            self._points_while_failing += 1
            if self._points_while_failing % self.STILL_FAILING_EVERY == 0:
                msgs.append(f"WRITE STILL FAILING: {len(self._pending)} "
                            "points waiting in memory.")
        return msgs


# ===============================================================================
# BACKEND: KEYSIGHT E4980A LCR  (verbatim from the passive T-scan program)
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
            self.instrument = None
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
        return idn

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
            except Exception as e:
                print(f"  Warning closing VISA session: {e}")
            finally:
                self.instrument = None


class TimeScan_Backend:
    """E4980A only. No temperature instrument is opened or read.

    All instrument access goes through one lock, so the worker thread and
    an exit-time close can never interleave SCPI commands. After a FINAL
    close (program exit) no re-initialisation is allowed, so a worker
    still inside a reconnect cannot switch the DC bias back on."""

    FINAL_CLOSE_LOCK_WAIT_S = 40.0

    def __init__(self):
        self.lcr = LCR_Backend()
        self.params = {}
        self.idn = ""
        self._lock = threading.RLock()
        self._shut_down = False

    def initialize_instruments(self, parameters):
        with self._lock:
            if self._shut_down:
                raise ConnectionError("Program is closing; not re-opening "
                                      "the LCR.")
            self.params = parameters
            if self.lcr.instrument is not None:
                self.lcr.close_instrument()     # never leak a session
            try:
                self.idn = self.lcr.initialize_instrument(parameters) or ""
            except Exception:
                # Init can fail AFTER :BIAS:STAT ON / the bias ramp:
                # always ramp the bias down and close the session.
                self.lcr.close_instrument()
                raise

    def measure_point(self, freq, delay, stop_event=None):
        """One point: returns (R, X, status), or None if Stop arrived
        during the settle delay. Same SCPI sequence as the T-scan's
        perform_measurement, but the delay is interruptible."""
        with self._lock:
            lcr = self.lcr
            if not lcr.instrument:
                raise ConnectionError("Instrument is not connected.")
            lcr.instrument.write(f":FREQ {freq}")
            if stop_event is not None:
                if stop_event.wait(delay):
                    return None
            else:
                time.sleep(delay)
            lcr.instrument.write(":TRIG:IMM")
            lcr.instrument.query("*OPC?")  # Wait for operation complete
            vals = lcr.instrument.query_ascii_values(":FETC?")
            R, X = vals[0], vals[1]
            status = int(vals[2]) if len(vals) > 2 else 0
            return R, X, status

    def reconnect(self):
        """Close and re-initialise from the stored settings (restores RX
        mode, aperture, ALC, corrections, bias) — works even after the
        LCR was power-cycled."""
        with self._lock:
            try:
                self.lcr.close_instrument()
            except Exception as e:
                print(f"  Pre-reconnect cleanup warning: {e}")
            self.initialize_instruments(self.params)

    def close_instruments(self, final=False):
        """Ramp DC bias to 0, switch it off, close the session.
        final=True (program exit): also forbid any later re-open."""
        if final:
            self._shut_down = True
            got = self._lock.acquire(timeout=self.FINAL_CLOSE_LOCK_WAIT_S)
            try:
                self.lcr.close_instrument()
            finally:
                if got:
                    self._lock.release()
            return
        with self._lock:
            self.lcr.close_instrument()


# ===============================================================================
# ACQUISITION LOOP  (worker thread; no Tk — unit-testable)
# ===============================================================================

class TimeScanRunner:
    """Measures the frequency list over and over until stop_event is set.

    The worker OWNS the instrument for the whole run: when run() returns,
    it has already flushed every buffered row it could and closed the LCR
    (DC bias ramped to 0 V and switched off). The GUI therefore never
    closes the instrument while this thread is alive.

    Messages put on out_queue:
        "LOG:<text>"
        ('POINT', rec, calc)        after the point is already on disk
        ('SWEEP_DONE', sweep, duration_s)
        Exception                   unexpected (non-comm) failure; run ends
    """

    def __init__(self, backend, writer, frequencies, delay, out_queue,
                 stop_event, backoffs=RECONNECT_BACKOFFS_S,
                 clock=time.time, monotonic=time.monotonic,
                 close_on_exit=True):
        self.backend = backend
        self.writer = writer
        self.frequencies = list(frequencies)
        self.delay = float(delay)
        self.out = out_queue
        self.stop_event = stop_event
        self.backoffs = tuple(backoffs)
        self.clock = clock
        self.monotonic = monotonic
        self.close_on_exit = close_on_exit
        self.start_epoch = None
        self.start_mono = None
        self.sweep = 1
        self.index = 0

    def _log(self, text):
        self.out.put("LOG:" + text)

    def run(self):
        try:
            self.start_epoch = self.clock()
            self.start_mono = self.monotonic()
            self._log(f"Continuous measurement started at "
                      f"{format_datetime_ms(self.start_epoch)}. "
                      "Measuring until Stop.")
            n = len(self.frequencies)
            failures = 0
            sweep_t0 = self.start_mono
            while not self.stop_event.is_set():
                f = self.frequencies[self.index]
                try:
                    result = self.backend.measure_point(
                        f, self.delay, self.stop_event)
                    # Time stamp RIGHT AFTER the reading came back
                    epoch = self.clock()
                    mono = self.monotonic()
                except Exception:
                    failures += 1
                    self._log(f"COMM ERROR at {freq_tag(f)} Hz (failure "
                              f"#{failures}): "
                              f"{traceback.format_exc(limit=3)}")
                    if not self._reconnect_with_backoff(failures):
                        break      # Stop requested
                    continue       # repeat the SAME point
                if result is None:
                    break          # Stop arrived during the settle delay
                R, X, status = result
                failures = 0

                calc, err = safe_calc(f, R, X)
                if err is not None:
                    self._log(f"Calc error at {freq_tag(f)} Hz: {err}. "
                              "Writing NaNs.")
                rec = make_record(epoch, mono - self.start_mono, self.sweep,
                                  self.index + 1, f, status, R, X)
                for msg in self.writer.write_point(rec, calc):
                    self._log(msg)
                self.out.put(("POINT", rec, calc))

                self.index += 1
                if self.index >= n:
                    self.out.put(("SWEEP_DONE", self.sweep, mono - sweep_t0))
                    self.index = 0
                    self.sweep += 1
                    sweep_t0 = mono
        except Exception as e:
            # Unexpected (non-comm) bug only; comm errors retry above.
            self._log(f"WORKER ERROR: {traceback.format_exc(limit=5)}")
            self.out.put(e)
        finally:
            self._shutdown()

    def _shutdown(self):
        """Final flush + instrument close, from the worker itself."""
        notes = []
        try:
            notes += self.writer.flush_pending()
            if self.writer.pending_count:
                notes.append(f"WARNING: {self.writer.pending_count} rows "
                             "could not be written to disk (disk/share "
                             "still unavailable at Stop).")
            if self.writer.dropped_points:
                notes.append(f"WARNING: {self.writer.dropped_points} points "
                             "were dropped during the run (write buffer "
                             "full).")
        except Exception as e:
            notes.append(f"WARNING: final flush failed: {e!r}")
        if self.close_on_exit:
            try:
                self.backend.close_instruments()
                notes.append("LCR closed (DC bias 0 V, OFF).")
            except Exception as e:
                notes.append(f"WARNING: error closing LCR: {e!r}")
        for msg in notes:
            print(msg)            # also on stdout, in case the window is gone
            self._log(msg)

    def _reconnect_with_backoff(self, attempt):
        """Close and re-open the LCR, waiting 5 -> 10 -> 30 -> 60 s
        between tries. Loops until reconnected; returns False if Stop was
        requested while waiting or reconnecting (the caller then closes
        the instrument, so a reconnect that finished after Stop never
        leaves the DC bias on)."""
        while not self.stop_event.is_set():
            delay_s = self.backoffs[min(attempt - 1, len(self.backoffs) - 1)]
            self._log(f"Reconnect attempt in {delay_s} s "
                      "(Stop stays responsive)...")
            if self.stop_event.wait(delay_s):
                return False
            try:
                self.backend.reconnect()
            except Exception as e:
                attempt += 1
                self._log(f"Reconnect failed: {e}")
                continue
            if self.stop_event.is_set():
                return False
            self._log("Reconnected. Resuming at the same frequency point.")
            return True
        return False


# ===============================================================================
# FRONT END (GUI)
# ===============================================================================

class TimeScan_Dielectric_GUI:
    """
    Continuous time-stamped dielectric measurement (Keysight E4980A only).
    Temperature is NOT read here — it comes from the PPMS log and is
    matched afterwards by time stamp.
    """

    PROGRAM_VERSION = PROGRAM_VERSION
    LOGO_SIZE = 110
    CONSOLE_MAX_LINES = 2000   # bound console growth on multi-day runs
    PLOT_MAX_POINTS = 10000    # halve plot buffers at this size (if enabled)

    # SetThreadExecutionState flags (Windows keep-awake during a run)
    ES_CONTINUOUS      = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    DEFAULT_FREQS = DEFAULT_FREQS_TEXT

    # --- Robust logo path finding ---
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "..", "..", "assets", "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
        if not os.path.exists(LOGO_FILE_PATH):
            LOGO_FILE_PATH = os.path.join(
                SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theme constants (identical to the T-scan programs) ---
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

    LEFT_PANEL_WIDTH = 500

    TITLE = "E4980A: Dielectric vs. Time (Continuous, PPMS-logged T)"

    # ------------------------------------------------------------------
    def __init__(self, root):
        self.root = root
        self.root.title(self.TITLE)
        self.root.geometry("1600x980")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 880)

        # --- State flags ---
        self.is_running = False
        self._stopping = False
        self._close_after_stop = False
        self._last_draw_time = 0.0
        self._redraw_interval = 1.0

        # --- Backend ---
        self.backend = TimeScan_Backend()
        atexit.register(self.backend.close_instruments, final=True)

        # --- File / frequency state ---
        self.file_location_path = ""
        self.frequencies = []
        self.writer = None
        self.runner = None
        self.plot_freq = None

        # --- Run counters (GUI side) ---
        self.points_done = 0
        self.last_sweep_s = None

        # --- Plot data: {freq: {'t': [...min], 'cp': [...], 'd': [...]}} ---
        self.data_storage = {}
        self.spec_current = ([], [])    # (freqs, Cp) of the running sweep
        self.spec_previous = ([], [])   # last completed sweep

        self.y_scale_var = tk.StringVar(value="auto")
        self.logo_image = None
        self._decade_ylims = {}

        # --- Threading ---
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self.stop_event = threading.Event()

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

        left_panel_container = ttk.Frame(self.main_pane,
                                         width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK,
                        highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container, orient="vertical",
                                  command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=scrollable_frame,
                                         anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
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
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

    # ------------------------------------------------------------------
    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        Label(header_frame, text=self.TITLE,
              bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD,
              font=font_title_main).pack(side='left', padx=20, pady=10)

        ttk.Button(header_frame, text="📈", command=launch_plotter_utility,
                   width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header_frame, text="📟", command=launch_gpib_scanner,
                   width=3).pack(side='right', padx=(0, 5), pady=5)

        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}",
              bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT,
              font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    # ------------------------------------------------------------------
    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE,
                             height=self.LOGO_SIZE,
                             bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              RESAMPLE_FILTER)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE / 2,
                                         self.LOGO_SIZE / 2,
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
                  font=institute_font, background=self.CLR_BG_DARK).grid(
                      row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font,
                  background=self.CLR_BG_DARK).grid(
                      row=1, column=1, padx=10, sticky='nw')
        ttk.Separator(frame, orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = (
            "Program Name: Dielectric vs. Time (Continuous)\n"
            "Instrument: Keysight E4980A (temperature from PPMS log)\n"
            "Function: FUNC:IMP RX, frequency list repeated until Stop\n"
            "Each point time-stamped; saved to disk immediately")
        ttk.Label(frame, text=details_text, justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')
        return frame

    # ------------------------------------------------------------------
    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters',
                           relief='groove', bg=self.CLR_BG_DARK,
                           fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)
        padx_val = 10
        r = 0

        Label(frame, text="Sample Name:").grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=pady_val,
            sticky='w')
        r += 1
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=(0, 10),
            sticky='ew')
        r += 1

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

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Enable Auto Level Control (ALC)",
                        variable=self.var_alc).grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=2, sticky='w')
        r += 1
        ttk.Checkbutton(frame, text="Enable Open/Short Corrections",
                        variable=self.var_corr).grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=2, sticky='w')
        r += 1

        # ON by default here (continuous multi-day runs): plot-only,
        # the files always keep every point.
        self.var_plot_thin = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame, text="Thin plot points on long runs (halve when full)",
            variable=self.var_plot_thin).grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=2, sticky='w')
        r += 1

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

        # --- Frequency list ---
        Label(frame, text="Frequencies (Hz) — used exactly as typed:").grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=(5, 0),
            sticky='w')
        r += 1
        self.freq_text = tk.Text(frame, font=self.FONT_BASE, height=3,
                                 wrap='word')
        self.freq_text.insert('1.0', self.DEFAULT_FREQS)
        self.freq_text.grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=(0, 2),
            sticky='ew')
        self.freq_text.bind("<KeyRelease>", self._update_freq_count)
        self.freq_text.bind("<<Paste>>", lambda e: self.root.after(
            10, self._update_freq_count))
        r += 1
        self.freq_count_label = Label(frame, text="", bg=self.CLR_BG_DARK,
                                      fg=self.CLR_FG_LIGHT,
                                      font=self.FONT_SUB_LABEL)
        self.freq_count_label.grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=(0, 4),
            sticky='w')
        r += 1
        self.default_list_button = ttk.Button(
            frame, text="Default list (100 Hz - 2 kHz, 20 pts)",
            command=self._reset_default_freqs)
        self.default_list_button.grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.list_maker_button = ttk.Button(
            frame, text="Open List Maker", command=launch_list_maker)
        self.list_maker_button.grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1
        self._update_freq_count()

        Label(frame, text="Live Plot Frequency:").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.plot_freq_cb = ttk.Combobox(frame, font=self.FONT_BASE,
                                         state='disabled')
        self.plot_freq_cb.grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=(0, 10),
            sticky='ew')
        self.plot_freq_cb.bind("<<ComboboxSelected>>",
                               self._on_plot_freq_change)
        r += 1

        Label(frame, text="LCR (E4980A) VISA:").grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=pady_val,
            sticky='w')
        r += 1
        self.lcr_cb = ttk.Combobox(frame, font=self.FONT_BASE,
                                   state='readonly')
        self.lcr_cb.grid(row=r, column=0, columnspan=2, padx=padx_val,
                         pady=(0, 10), sticky='ew')
        r += 1

        self.scan_button = ttk.Button(
            frame, text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=r, column=0, columnspan=2, padx=padx_val,
                              pady=4, sticky='ew')
        r += 1

        self.file_button = ttk.Button(
            frame, text="Browse Destination Folder...",
            command=self._browse_file_location)
        self.file_button.grid(row=r, column=0, columnspan=2, padx=padx_val,
                              pady=4, sticky='ew')
        r += 1

        self.start_button = ttk.Button(
            frame, text="Start Measurement", command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(row=r, column=0, padx=padx_val,
                               pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(
            frame, text="Stop", command=self.stop_measurement,
            style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=r, column=1, padx=padx_val,
                              pady=(10, 10), sticky='ew')
        return frame

    def _reset_default_freqs(self):
        self.freq_text.delete('1.0', 'end')
        self.freq_text.insert('1.0', self.DEFAULT_FREQS)
        self._update_freq_count()

    def _update_freq_count(self, event=None):
        try:
            freqs = parse_frequency_list(self.freq_text.get('1.0', 'end'))
            n_distinct = len(unique_in_order(freqs))
            txt = f"{len(freqs)} points per sweep"
            if n_distinct != len(freqs):
                txt += f" ({n_distinct} distinct frequencies / files)"
        except ValueError as e:
            txt = f"List error: {e}"
        self.freq_count_label.config(text=txt)

    # ------------------------------------------------------------------
    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0,
            height=10)
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
            bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        self.status_label = tk.Label(
            top_bar, text="Idle", bg=self.CLR_GRAPH_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        self.status_label.pack(side='left', padx=5)
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

        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG,
                             layout='constrained')
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        # Main: Cp vs elapsed time (selected frequency)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3,
            linestyle='-')
        self.ax_main.set_title("Cp vs. Time", fontweight='bold')
        self.ax_main.set_xlabel("Elapsed time (min)")
        self.ax_main.set_ylabel("Capacitance, Cp (F)")
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        # Sub 1: D vs elapsed time (selected frequency)
        self.line_sub1, = self.ax_sub1.plot(
            [], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3,
            linestyle='-')
        self.ax_sub1.set_xlabel("Elapsed time (min)")
        self.ax_sub1.set_ylabel("Dissipation, D")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)

        # Sub 2: Cp vs frequency (previous + current sweep)
        self.line_spec_prev, = self.ax_sub2.plot(
            [], [], color='#9A9A9A', marker='.', markersize=3,
            linestyle='-', label='previous sweep')
        self.line_spec_cur, = self.ax_sub2.plot(
            [], [], color=self.CLR_ACCENT_GREEN, marker='o', markersize=3,
            linestyle='-', label='current sweep')
        self.ax_sub2.set_xscale('log')
        self.ax_sub2.set_xlabel("Frequency (Hz)")
        self.ax_sub2.set_ylabel("Cp (F)")
        self.ax_sub2.legend(loc='best', fontsize=8)
        self.ax_sub2.grid(True, which='both', linestyle='--', alpha=0.6)

        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5,
                                         pady=5)

    # ==================================================================
    # IMPEDANCE CALCULATIONS  (unchanged formulas; see module function)
    # ==================================================================
    def calculate_impedance_parameters(self, f, R, X):
        return calculate_impedance_parameters(f, R, X)

    # ==================================================================
    # LOGGING
    # ==================================================================
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
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
        """Stop Windows from sleeping mid-run. No-op elsewhere."""
        try:
            flags = self.ES_CONTINUOUS | (
                self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _beep(self, times=1):
        """Audible alert (main/Tk thread only); never a modal dialog on
        unattended events."""
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

    # ==================================================================
    # START / STOP
    # ==================================================================
    def _gather_params(self):
        def num(key, label):
            try:
                v = float(self.entries[key].get())
            except ValueError:
                raise ValueError(f"{label} must be a number.")
            if not math.isfinite(v):
                raise ValueError(f"{label} must be a finite number.")
            return v

        params = {
            'sample_name':  self.entries["Sample Name"].get().strip(),
            'ac_bias':      num("AC Bias", "AC Bias Voltage"),
            'dc_bias':      num("DC Bias", "DC Bias Voltage"),
            'delay':        num("Delay", "Delay per Freq"),
            'aper':         self.aper_combobox.get(),
            'alc_enabled':  self.var_alc.get(),
            'corr_enabled': self.var_corr.get(),
            'cable_len':    self.cable_len_combobox.get(),
            'lcr_visa':     self._extract_visa(self.lcr_cb.get()),
        }
        if not params['sample_name'] or not params['lcr_visa'] \
                or not self.file_location_path:
            raise ValueError("Sample Name, LCR VISA address and a "
                             "destination folder are required.")
        bad = INVALID_NAME_CHARS.intersection(params['sample_name'])
        if bad:
            raise ValueError("Sample Name contains characters not allowed "
                             f"in file names: {' '.join(sorted(bad))}")
        if params['delay'] < 0:
            raise ValueError("Delay per Freq must be >= 0 s.")
        return params

    def _run_info_text(self, params):
        lines = [
            f"Program\tTime_Scan_Dielectric_E4980A_GUI.py v{PROGRAM_VERSION}",
            f"Created\t{format_datetime_ms(time.time())}",
            f"Sample\t{params['sample_name']}",
            f"LCR VISA\t{params['lcr_visa']}",
            f"LCR IDN\t{self.backend.idn}",
            f"AC level (V)\t{params['ac_bias']}",
            f"DC bias (V)\t{params['dc_bias']}",
            f"Delay per freq (s)\t{params['delay']}",
            f"Aperture\t{params['aper']}",
            f"ALC\t{'ON' if params['alc_enabled'] else 'OFF'}",
            f"Open/Short correction\t"
            f"{'ON' if params['corr_enabled'] else 'OFF'}",
            f"Cable length (m)\t{params['cable_len']}",
            f"Points per sweep\t{len(self.frequencies)}",
            "Frequency list (Hz, measurement order)\t"
            + ", ".join(freq_tag(f) for f in self.frequencies),
            "Time stamp\ttaken right after each reading returns from the "
            "LCR (:FETC?)",
            "Elapsed_s\tseconds since Start (monotonic clock)",
            "Epoch_s\tUnix time, seconds (UTC based)",
            "DateTime\tlocal PC time, ms",
            f"Per-frequency file columns\t{FREQ_FILE_HEADER}",
            f"Master file columns\t{MASTER_FILE_HEADER}",
        ]
        return "\n".join(lines) + "\n"

    def start_measurement(self):
        if self.is_running or self._stopping:
            return
        old = self.measurement_thread
        if old is not None and old.is_alive():
            # The previous worker still owns the LCR (e.g. stuck in a
            # VISA timeout). Never run two workers on one instrument.
            self.log("Previous run is still closing the instrument; "
                     "Start is refused until it has finished.")
            return
        instrument_opened = False
        try:
            self.frequencies = parse_frequency_list(
                self.freq_text.get('1.0', 'end'))
            params = self._gather_params()

            if params['corr_enabled']:
                self.log(
                    f"WARNING: Ensure physical Open/Short calibration was "
                    f"performed with {params['cable_len']} m cable before "
                    f"enabling corrections!")

            instrument_opened = True   # init may fail half-way: close
            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: "
                     f"{params['sample_name']}")

            self.writer = DataWriter(self.file_location_path,
                                     params['sample_name'],
                                     self.frequencies)
            for msg in self.writer.create_files(self._run_info_text(params)):
                self.log(msg)
            n_files = len(self.writer.freq_paths)
            self.log(f"{len(self.frequencies)} points per sweep; "
                     f"{n_files} frequency files + master file created in "
                     f"{self.file_location_path}")
            self.log(f"Master file: {os.path.basename(self.writer.master_path)}")

            self._populate_plot_freq_dropdown()

            # --- Reset state ---
            self.is_running = True
            self.points_done = 0
            self.last_sweep_s = None
            self.data_storage = {f: {'t': [], 'cp': [], 'd': []}
                                 for f in self.writer.distinct}
            self.spec_current = ([], [])
            self.spec_previous = ([], [])
            self._decade_ylims.clear()
            self._set_controls_running(True)

            for line in (self.line_main, self.line_sub1,
                         self.line_spec_prev, self.line_spec_cur):
                line.set_data([], [])
            self._set_main_title()
            self.canvas.draw()

            # Fresh queue AND fresh Event per run: an old worker keeps
            # its own (already set) Event and can never be revived.
            self.data_queue = queue.Queue()
            self.stop_event = threading.Event()
            self.runner = TimeScanRunner(
                self.backend, self.writer, self.frequencies,
                params['delay'], self.data_queue, self.stop_event)
            self.measurement_thread = threading.Thread(
                target=self.runner.run, daemon=True)
            self.measurement_thread.start()
            instrument_opened = False   # the worker owns it from here on
            self._set_keep_awake(True)
            self._update_status_label()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.is_running = False
            self._set_controls_running(False)
            if instrument_opened:
                try:
                    self.backend.close_instruments()
                    self.log("LCR closed after failed start "
                             "(DC bias 0 V, OFF).")
                except Exception as ce:
                    self.log(f"WARNING: error closing LCR: {ce}")
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error",
                                 f"Could not start measurement.\n{e}")

    def _set_controls_running(self, running):
        self.start_button.config(state='disabled' if running else 'normal')
        self.stop_button.config(state='normal' if running else 'disabled')
        for w in (self.scan_button, self.file_button,
                  self.default_list_button):
            w.config(state='disabled' if running else 'normal')
        self.freq_text.config(state='disabled' if running else 'normal')

    def _extract_visa(self, combo_val):
        """Strips the '  ->  IDN' suffix added by the identity-aware scan."""
        if "  ->  " in combo_val:
            return combo_val.split("  ->  ")[0].strip()
        return combo_val.strip()

    def _populate_plot_freq_dropdown(self):
        self._plot_choices = list(self.writer.distinct)
        self.plot_freq_cb.config(state='readonly')
        self.plot_freq_cb['values'] = [f"{freq_tag(f)} Hz"
                                       for f in self._plot_choices]
        self.plot_freq_cb.current(0)
        self.plot_freq = self._plot_choices[0]

    def _set_main_title(self):
        if self.plot_freq is not None:
            self.ax_main.set_title(
                f"Cp vs. Time @ {freq_tag(self.plot_freq)} Hz",
                fontweight='bold')

    def _on_plot_freq_change(self, event=None):
        idx = self.plot_freq_cb.current()
        choices = getattr(self, "_plot_choices", [])
        if not (0 <= idx < len(choices)):
            return
        self.plot_freq = choices[idx]
        self._decade_ylims.clear()
        self._set_main_title()
        self._update_live_plots(force=True)

    # ------------------------------------------------------------------
    def stop_measurement(self, from_user=True):
        if self._stopping or not self.is_running:
            return
        self._stopping = True
        self.is_running = False
        self.stop_event.set()
        self.stop_button.config(state='disabled')
        if from_user:
            self.log("Stop requested; finishing the current point...")
        self._stop_requested_at = time.time()
        self._stop_next_notice = self._stop_requested_at + 20.0
        self._poll_worker_stopped(from_user)

    def _poll_worker_stopped(self, from_user):
        """Wait (without blocking Tk) for the worker to exit. No
        deadline: the worker itself closes the LCR, so the GUI must never
        give up and touch the instrument while it is still alive. A VISA
        timeout or a reconnect in progress can take up to ~1 min."""
        t = self.measurement_thread
        if t is not None and t.is_alive():
            self._drain_queue_items()          # keep the console live
            now = time.time()
            if now >= self._stop_next_notice:
                self._stop_next_notice = now + 20.0
                self.log(f"Still waiting for the instrument to finish "
                         f"(VISA timeout / reconnect in progress, "
                         f"{now - self._stop_requested_at:.0f} s)...")
            self.root.after(200, lambda: self._poll_worker_stopped(from_user))
            return
        self._finalize_stop(from_user)

    def _finalize_stop(self, from_user):
        self._set_keep_awake(False)
        # Last messages from the worker (final points, flush report)
        late_error = self._drain_queue_items()
        if late_error is not None:
            self.log(f"Worker reported an error while stopping: "
                     f"{late_error!r}")
        self._set_controls_running(False)
        # The worker has exited and already closed the LCR; this second
        # close is a harmless no-op kept as a belt-and-braces safety.
        try:
            self.backend.close_instruments()
        except Exception as e:
            self.log(f"WARNING: error closing instruments: {e}")
        self._stopping = False
        self._update_live_plots(force=True)
        self._update_status_label(final=True)

        if self._close_after_stop:
            self.root.destroy()
            return

        if from_user:
            rows = self.writer.rows_written if self.writer else 0
            self.log(f"Measurement stopped. {rows} points on disk; "
                     "instrument disconnected.")
            messagebox.showinfo(
                "Info", f"Measurement stopped.\n{rows} points saved.")
        elif self.writer is not None:
            self.log(f"Measurement stopped. "
                     f"{self.writer.rows_written} points on disk.")

    # ==================================================================
    # QUEUE PROCESSING (main thread)
    # ==================================================================
    def _drain_queue_items(self):
        """Handle everything currently queued. Returns a terminal
        Exception if the worker reported one."""
        terminal = None
        while True:
            try:
                data = self.data_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if isinstance(data, str) and data.startswith("LOG:"):
                    self.log(data[4:])
                elif isinstance(data, Exception):
                    terminal = data
                elif isinstance(data, tuple) and data[0] == 'POINT':
                    self._process_point(data[1], data[2])
                elif isinstance(data, tuple) and data[0] == 'SWEEP_DONE':
                    self._process_sweep_done(data[1], data[2])
            except Exception:
                try:
                    self.log("GUI ERROR (non-fatal): "
                             f"{traceback.format_exc()}")
                except Exception:
                    pass
        return terminal

    def _process_data_queue(self):
        terminal = None
        try:
            terminal = self._drain_queue_items()
            self._update_live_plots()
        except Exception:
            try:
                self.log(f"GUI ERROR (non-fatal): {traceback.format_exc()}")
            except Exception:
                pass
        finally:
            try:
                if isinstance(terminal, Exception):
                    self._handle_runtime_error(terminal)
                elif self.is_running:
                    self.root.after(200, self._process_data_queue)
            except tk.TclError:
                pass

    def _process_point(self, rec, calc):
        self.points_done += 1
        f = rec['freq']
        if rec['status'] != 0:
            self.log(f"WARNING: {freq_tag(f)} Hz status={rec['status']} "
                     "(non-zero = overload/ALC issue) "
                     f"at t={rec['elapsed']:.1f} s")
        store = self.data_storage.get(f)
        if store is not None:
            store['t'].append(rec['elapsed'] / 60.0)
            store['cp'].append(calc[IDX_CP])
            store['d'].append(calc[IDX_D])
            if self.var_plot_thin.get() and \
                    len(store['t']) >= self.PLOT_MAX_POINTS:
                keep = slice(None, None, 2)
                last = len(store['t']) % 2 == 0
                for k in ('t', 'cp', 'd'):
                    store[k] = store[k][keep] + (store[k][-1:] if last else [])
        if rec['point'] == 1:
            self.spec_current = ([], [])
        self.spec_current[0].append(f)
        self.spec_current[1].append(calc[IDX_CP])
        self._update_status_label(rec)

    def _process_sweep_done(self, sweep, duration_s):
        self.last_sweep_s = duration_s
        self.spec_previous = (list(self.spec_current[0]),
                              list(self.spec_current[1]))
        self.log(f"Sweep {sweep} done in {duration_s:.1f} s "
                 f"({self.points_done} points total).")

    def _update_status_label(self, rec=None, final=False):
        if final:
            txt = f"Stopped — {self.points_done} points measured"
        elif rec is None:
            txt = "Running — waiting for first point"
        else:
            txt = (f"Sweep {rec['sweep']} | point {rec['point']}/"
                   f"{len(self.frequencies)} | {self.points_done} points "
                   f"| elapsed {rec['elapsed'] / 60:.1f} min")
        if self.last_sweep_s is not None:
            txt += f" | last sweep {self.last_sweep_s:.1f} s"
        try:
            self.status_label.config(text=txt)
        except tk.TclError:
            pass

    # ==================================================================
    # PLOTTING
    # ==================================================================
    def _apply_y_scale(self, ax, values, key):
        """Same adaptive y-scale as the T-scan ('auto'|'log'|'linear')."""
        mode = self.y_scale_var.get()
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        use_log = bool(pos) and mode != 'linear'
        if use_log and mode == 'auto':
            span = math.log10(max(pos)) - math.log10(min(pos))
            use_log = span >= 1.0
        if not use_log:
            ax.set_yscale('linear')
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
            ax.set_yscale('log')
            ax.set_ylim(lo, hi)
        return True

    def _on_y_scale_change(self):
        self._decade_ylims.clear()
        self._update_live_plots(force=True)
        self.canvas.draw_idle()

    def _update_live_plots(self, force=False):
        now = time.time()
        if not force and (now - self._last_draw_time) < self._redraw_interval:
            return
        self._last_draw_time = now

        fq = self.plot_freq
        store = self.data_storage.get(fq) if fq is not None else None
        if store is not None:
            self.line_main.set_data(store['t'], store['cp'])
            self.line_sub1.set_data(store['t'], store['d'])
            for ax in (self.ax_main, self.ax_sub1):
                ax.relim()
                ax.autoscale_view(scalex=True, scaley=False)
            self._apply_y_scale(self.ax_main, store['cp'], "cp")
            self._apply_y_scale(self.ax_sub1, store['d'], "d")

        self.line_spec_prev.set_data(*self.spec_previous)
        self.line_spec_cur.set_data(*self.spec_current)
        self.ax_sub2.relim()
        self.ax_sub2.autoscale_view()
        self.canvas.draw_idle()

    # ==================================================================
    # EVENT HANDLERS
    # ==================================================================
    def _handle_runtime_error(self, exception):
        # UNATTENDED POLICY: no modal dialog; log + beep; data on disk.
        self.log(f"RUNTIME ERROR: {exception!r}")
        self.stop_measurement(False)
        self.log(f"A critical error occurred: {exception}. Measurement "
                 "stopped; all written data is on disk.")
        self._beep(times=3)

    # ==================================================================
    # VISA SCAN (identity-aware)
    # ==================================================================
    def _scan_for_visa_instruments(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA is not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
        except Exception as e:
            self.log(f"ERROR: Cannot create VISA Resource Manager: {e}")
            return

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        try:
            resources = rm.list_resources()
        except Exception as e:
            self.log(f"ERROR: VISA resource listing failed: {e}")
            return
        if not resources:
            self.log("No VISA instruments found.")
            return

        found, lcr_label = [], None
        for res in resources:
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
            self.log(f"  {label}")
            if lcr_label is None and "E4980" in idn:
                lcr_label = label

        self.lcr_cb['values'] = found
        if lcr_label:
            self.lcr_cb.set(lcr_label)
            self.log("E4980A auto-selected.")
        elif found:
            self.lcr_cb.set(found[0])
            self.log("WARNING: No E4980A found; defaulted to first device.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Destination folder set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit",
                                   "Measurement is running. Stop and exit?"):
                self._close_after_stop = True
                self.stop_measurement(from_user=False)
        elif self._stopping:
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
            "PyVISA is not installed.\n\nPlease run:\npip install pyvisa")
        return
    root = tk.Tk()
    TimeScan_Dielectric_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
