"""
Module:             Correction_OpenShort_E4980A_GUI.py
Purpose:            Guided OPEN / SHORT correction of the Keysight E4980A
                    LCR meter: inspect what the meter holds, back it up,
                    redo the correction one screen at a time, verify the
                    residuals, and keep a full record of every command.
Author:             Prathamesh Deshmukh (PICA); AI-assisted implementation
                    from the design brief of 2026-09-09.
Version:            V: 1.0

Design basis (Keysight E4980A/AL User's Guide, page numbers in brackets):
  * Two correction methods live inside SINGLE mode [112-121]:
      A. "all frequency points": MEAS OPEN / MEAS SHORT measure at 51 preset
         frequencies, other frequencies are interpolated [114-120]. DEFAULT.
      B. "user-specified spot points": up to 201 frequencies, used instead
         of A whenever the test frequency equals a spot frequency [113, 121,
         129]. OPT-IN (checkbox, off by default).
    MODE SINGLE/MULTI is about scanner channels (Option 301) and is never
    touched here [132, 337].
  * The cable-length value sets the calibration plane the correction is
    measured from [115, 119, 133]. A different cable/fixture/level after
    correction makes the data invalid with no warning from the meter
    [117, 120]. This module sets the length BEFORE correcting and stamps
    it into every file it writes.
  * Only spot-point data can be read back and written (1206 numbers,
    :CORR:USE:DATA:SING) [342]. The 51-point tables cannot be read out;
    their only backup is a state register, which this module never uses
    (no :MMEM commands, ever) [365].
  * Correction data survives *RST and power cycling; only :SYST:PRES
    erases it [87, 324, 377, 450-452]. This module never sends *RST
    (it would also zero the cable length) and sends :SYST:PRES only
    behind an explicit, attended confirmation.
  * When does stored correction data change? Only when the meter runs
    :CORR:OPEN / :CORR:SHOR / :CORR:SPOT<n>:OPEN|SHOR (it measures and
    overwrites its own table), or when the PC writes spot data with
    :CORR:USE:DATA:SING (Restore only). Every such moment is logged with
    a timestamp in the console record.

PICA rules honoured: self-contained script (no shared module), minimal
imports, plain-language screens with one big headline answer, advanced
parameters hidden, new behaviours opt-in, no modal dialog except for
attended confirmations (Restore, full preset).

Files written (one timestamp tag, chosen folder):
  <tag>_backup_before.txt   spot data + state as found  (restorable)
  <tag>_spotdata_after.txt  spot data + state after the new correction
  <tag>_residuals.txt       residual Cp/G (open) and Rs/Ls (short) per
                            frequency, correction OFF and ON, pass flags
  <tag>_log.txt             the module's own log (what you saw on screen)
  <tag>_console.txt         every command sent and every reply received
"""

# ===============================================================================
# IMPORTS
# ===============================================================================
import os
import sys
import time
import queue
import threading
import traceback
from datetime import datetime

import tkinter as tk
from tkinter import ttk, Label, LabelFrame, filedialog, messagebox, scrolledtext

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:            # pragma: no cover - depends on the machine
    pyvisa = None
    PYVISA_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:     # pragma: no cover
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:            # pragma: no cover
    PIL_AVAILABLE = False


# ===============================================================================
# CONSTANTS
# ===============================================================================
PROGRAM_VERSION = "1.0"
N_SPOTS = 201                     # SPOT No. 1..201 [121, 339]
VALUES_PER_SPOT = 6               # openA openB shortA shortB loadA loadB [342]
N_SPOT_VALUES = N_SPOTS * VALUES_PER_SPOT   # 1206

# Default frequencies for spot points and for the verify sweep: the
# Temprature_Scan_E4980A_GUI.py default list, so the correction is exact
# where the scans measure.
TSCAN_FREQS_HZ = [1000, 2000, 3000, 5000, 7000, 10000, 25000, 50000,
                  70000, 90000, 100000, 120000, 150000, 170000, 200000,
                  250000, 500000, 1000000, 1500000, 2000000]

# Proposed pass/fail thresholds (the manual gives none; derived from the
# few-mm oxide pellet sizes in the design brief). Editable in Advanced.
DEFAULT_THRESH = {
    "open_cp_F":   0.1e-12,   # |Cp| residual, open fixture, correction ON
    "open_g_S":    1e-6,      # |G|  residual, open fixture
    "short_rs_Ohm": 0.1,      # |Rs| residual, shorting bar, correction ON
    "short_ls_H":  100e-9,    # |Ls| residual, shorting bar
}

# Timeouts. An all-points MEAS OPEN/SHORT takes on the order of a minute.
TIMEOUT_NORMAL_MS = 15000
TIMEOUT_CORRECTION_MS = 240000


# ===============================================================================
# BACKEND: E4980A with a full command record
# ===============================================================================
class E4980A_CorrectionBackend:
    """Thin PyVISA wrapper. Every write and every query is appended to
    `self.record` as (iso_time, kind, command, reply) and pushed to an
    optional `on_record` callback so the GUI can write the console file
    as it happens. Nothing here sends *RST, :SYST:PRES or :MMEM unless
    the named method is called explicitly."""

    def __init__(self, on_record=None):
        self.rm = None
        self.inst = None
        self.idn = ""
        self.record = []
        self.on_record = on_record
        self._lock = threading.Lock()

    # ------------------------------------------------------------ plumbing
    def _rec(self, kind, cmd, reply=""):
        entry = (datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                 kind, cmd, reply)
        self.record.append(entry)
        if self.on_record:
            try:
                self.on_record(entry)
            except Exception:
                pass

    def _w(self, cmd):
        with self._lock:
            self._rec("W", cmd)
            self.inst.write(cmd)

    def _q(self, cmd, timeout_ms=None):
        with self._lock:
            old = self.inst.timeout
            if timeout_ms:
                self.inst.timeout = timeout_ms
            try:
                reply = self.inst.query(cmd).strip()
            finally:
                self.inst.timeout = old
            self._rec("Q", cmd, reply if len(reply) < 200
                      else reply[:200] + f"... ({len(reply)} chars)")
            return reply

    def connect(self, visa_address):
        if not PYVISA_AVAILABLE:
            raise ConnectionError("PyVISA is not installed.")
        if self.rm is None:
            self.rm = pyvisa.ResourceManager()
        inst = self.rm.open_resource(visa_address)
        inst.timeout = TIMEOUT_NORMAL_MS
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self.inst = inst
        self.idn = self._q("*IDN?")
        if "E4980" not in self.idn:
            inst.close()
            self.inst = None
            raise ConnectionError(f"Not an E4980A: {self.idn}")
        return self.idn

    def close(self):
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
            self.inst = None

    def errors(self):
        """Drain :SYST:ERR? and return the non-zero entries."""
        out = []
        for _ in range(10):
            e = self._q(":SYST:ERR?")
            if e.startswith("+0") or e.startswith("0"):
                break
            out.append(e)
        return out

    # ------------------------------------------------------------ inspect
    def read_state(self, all_spots=False):
        """Read everything about the correction without changing anything.
        Returns a dict. Spot FREQ/STAT are queried for spots holding any
        non-zero data (fast); with all_spots=True every one of the 201
        spots is queried (402 queries, slow but complete)."""
        st = {
            "idn": self.idn,
            "open_on": self._q(":CORR:OPEN:STAT?").strip() in ("1", "ON"),
            "short_on": self._q(":CORR:SHOR:STAT?").strip() in ("1", "ON"),
            "load_on": self._q(":CORR:LOAD:STAT?").strip() in ("1", "ON"),
            "length_m": int(float(self._q(":CORR:LENG?"))),
            "method": self._q(":CORR:METH?"),
            "load_type": self._q(":CORR:LOAD:TYPE?"),
            "level_V": self._q(":VOLT?"),
            "alc": self._q(":AMPL:ALC?"),
        }
        st["spot_data"] = self.read_spot_data()
        spots = {}
        for n in range(1, N_SPOTS + 1):
            vals = st["spot_data"][(n - 1) * VALUES_PER_SPOT: n * VALUES_PER_SPOT]
            if all_spots or any(v != 0.0 for v in vals):
                freq = float(self._q(f":CORR:SPOT{n}:FREQ?"))
                on = self._q(f":CORR:SPOT{n}:STAT?").strip() in ("1", "ON")
                spots[n] = {"freq": freq, "on": on}
        st["spots"] = spots
        return st

    def read_spot_data(self):
        reply = self._q(":CORR:USE:DATA:SING?", timeout_ms=60000)
        vals = [float(x) for x in reply.split(",") if x.strip()]
        if len(vals) != N_SPOT_VALUES:
            raise ValueError(f"Expected {N_SPOT_VALUES} spot values, "
                             f"got {len(vals)}")
        return vals

    # ------------------------------------------------------------ settings
    def apply_settings(self, length_m, level_v, alc_on, aper="LONG"):
        """Settings only. Changes no correction data, but the cable length
        redefines the calibration plane [133]."""
        self._w(f":CORR:LENG {int(length_m)}")
        self._w(f":VOLT {float(level_v)}")
        self._w(":AMPL:ALC ON" if alc_on else ":AMPL:ALC OFF")
        self._w(f":APER {aper}")
        self._w(":FUNC:IMP:RANG:AUTO ON")
        self._w(":FORM ASC")
        self._w(":TRIG:SOUR BUS")
        self._w(":INIT:CONT ON")
        self._w(":DISP:ENAB ON")

    def set_switches(self, open_on, short_on):
        self._w(":CORR:OPEN:STAT ON" if open_on else ":CORR:OPEN:STAT OFF")
        self._w(":CORR:SHOR:STAT ON" if short_on else ":CORR:SHOR:STAT OFF")

    # ------------------------------------------------------------ the writes
    def execute_all_points(self, which):
        """which = 'OPEN' | 'SHOR'. THE MOMENT THE METER OVERWRITES ITS
        STORED TABLE [116, 119, 338]. Blocks until *OPC? returns."""
        cmd = ":CORR:OPEN" if which == "OPEN" else ":CORR:SHOR"
        self._w(cmd)
        self._q("*OPC?", timeout_ms=TIMEOUT_CORRECTION_MS)
        return self.errors()

    def setup_spot(self, n, freq_hz, on=True):
        self._w(f":CORR:SPOT{n}:FREQ {float(freq_hz)}")
        self._w(f":CORR:SPOT{n}:STAT {'ON' if on else 'OFF'}")

    def execute_spot(self, n, which):
        """which = 'OPEN' | 'SHOR'. Overwrites ONE spot's entry [340]."""
        self._w(f":CORR:SPOT{n}:{'OPEN' if which == 'OPEN' else 'SHOR'}")
        self._q("*OPC?", timeout_ms=TIMEOUT_CORRECTION_MS)
        return self.errors()

    def write_spot_data(self, values):
        """The ONLY command in this module that pushes correction numbers
        from the PC into the meter [342]. Used by Restore and by Clear."""
        if len(values) != N_SPOT_VALUES:
            raise ValueError(f"Need exactly {N_SPOT_VALUES} values")
        payload = ",".join(f"{v:.9g}" for v in values)
        self._w(":CORR:USE:DATA:SING " + payload)
        self._q("*OPC?", timeout_ms=60000)
        return self.errors()

    def system_preset(self):
        """CLEAR SET&CORR: erases correction data and backed-up items
        [87, 377]. Only ever called after an attended confirmation."""
        self._w(":SYST:PRES")
        time.sleep(3.0)
        self._q("*OPC?", timeout_ms=60000)

    # ------------------------------------------------------------ measure
    def measure(self, func, freq_hz):
        """One triggered reading. func 'CPG' -> (Cp, G); 'LSRS' -> (Ls, Rs)."""
        self._w(f":FUNC:IMP {func}")
        self._w(f":FREQ {float(freq_hz)}")
        time.sleep(0.15)
        self._w(":TRIG:IMM")
        self._q("*OPC?")
        reply = self._q(":FETC?")
        parts = [float(x) for x in reply.split(",")]
        a, b = parts[0], parts[1]
        status = int(parts[2]) if len(parts) > 2 else 0
        return a, b, status


# ===============================================================================
# FILE FORMAT for spot data (restorable)
# ===============================================================================
SPOT_HEADER = ("Spot\tFreq_Hz\tState\tOpen_A_S\tOpen_B_S\tShort_A_Ohm\t"
               "Short_B_Ohm\tLoad_A\tLoad_B")


def format_spot_file(state, meta):
    """Text block: '# key: value' lines, then one row per spot (all 201)."""
    lines = ["# E4980A correction spot data (PICA Correction_OpenShort v"
             f"{PROGRAM_VERSION})"]
    for k, v in meta.items():
        lines.append(f"# {k}: {v}")
    lines.append(f"# idn: {state.get('idn', '')}")
    lines.append(f"# open_on: {state.get('open_on')}")
    lines.append(f"# short_on: {state.get('short_on')}")
    lines.append(f"# load_on: {state.get('load_on')}")
    lines.append(f"# length_m: {state.get('length_m')}")
    lines.append(f"# method: {state.get('method')}")
    lines.append(f"# load_type: {state.get('load_type')}")
    lines.append("# note: all-points (51-frequency) tables cannot be read "
                 "from the meter and are NOT in this file [manual p.342]")
    lines.append(SPOT_HEADER)
    data = state["spot_data"]
    spots = state.get("spots", {})
    for n in range(1, N_SPOTS + 1):
        vals = data[(n - 1) * VALUES_PER_SPOT: n * VALUES_PER_SPOT]
        sp = spots.get(n, {"freq": 0.0, "on": False})
        row = [str(n), f"{sp['freq']:.6g}", "ON" if sp["on"] else "OFF"]
        row += [f"{v:.9g}" for v in vals]
        lines.append("\t".join(row))
    return "\n".join(lines) + "\n"


def parse_spot_file(text):
    """Inverse of format_spot_file. Returns (meta_dict, values[1206],
    spots{n: {'freq','on'}})."""
    meta, values, spots = {}, [0.0] * N_SPOT_VALUES, {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("# ").strip()
            if ":" in body:
                k, v = body.split(":", 1)
                meta[k.strip()] = v.strip()
            continue
        if line.startswith("Spot\t"):
            continue
        parts = line.split("\t")
        if len(parts) != 3 + VALUES_PER_SPOT:
            raise ValueError(f"Bad spot row: {line[:60]}")
        n = int(parts[0])
        if not 1 <= n <= N_SPOTS:
            raise ValueError(f"Spot number out of range: {n}")
        freq = float(parts[1])
        on = parts[2].upper() == "ON"
        vals = [float(x) for x in parts[3:]]
        values[(n - 1) * VALUES_PER_SPOT: n * VALUES_PER_SPOT] = vals
        if freq > 0 or on or any(v != 0 for v in vals):
            spots[n] = {"freq": freq, "on": on}
    return meta, values, spots


def judge_residuals(rows, thresh):
    """rows: list of dicts with keys f, open_cp_on, open_g_on, short_rs_on,
    short_ls_on (None when not measured). Returns (all_pass, worst_text)."""
    all_pass, worst, worst_ratio = True, "", 0.0
    for r in rows:
        checks = [("open Cp", r.get("open_cp_on"), thresh["open_cp_F"], "F"),
                  ("open G", r.get("open_g_on"), thresh["open_g_S"], "S"),
                  ("short Rs", r.get("short_rs_on"), thresh["short_rs_Ohm"], "Ohm"),
                  ("short Ls", r.get("short_ls_on"), thresh["short_ls_H"], "H")]
        for name, val, lim, unit in checks:
            if val is None:
                continue
            ratio = abs(val) / lim if lim > 0 else float("inf")
            if ratio > 1.0:
                all_pass = False
            if ratio > worst_ratio:
                worst_ratio = ratio
                worst = (f"{name} at {r['f']:g} Hz = {abs(val):.3g} {unit} "
                         f"(limit {lim:.3g} {unit})")
    return all_pass, worst


# ===============================================================================
# GUI
# ===============================================================================
class CorrectionGUI:
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    CLR_PASS = '#2E7D32'
    CLR_WARN = '#B23A2E'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_HEADLINE = ('Segoe UI', FONT_SIZE_BASE + 7, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    CONSOLE_MAX_LINES = 4000
    LOGO_SIZE = 90

    STEPS = ["1  Connect & inspect", "2  Choose", "3  Open", "4  Short",
             "5  Verify", "6  Done"]

    def __init__(self, root):
        self.root = root
        self.root.title("E4980A: Open/Short Correction (guided)")
        self.root.geometry("1500x940")
        self.root.minsize(1200, 800)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = E4980A_CorrectionBackend(on_record=self._on_record)
        self.connected = False
        self.state_before = None
        self.dest_folder = ""
        self.tag = ""
        self.paths = {}
        self._console_buffer = []          # records before the file exists
        self.worker = None
        self.busy = False
        self.queue = queue.Queue()
        self._poll_id = None
        self._live_id = None
        self.live_func = "CPG"
        self.residual_rows = []
        self.thresh = dict(DEFAULT_THRESH)
        self.step = 0
        self.spot_map = {}                 # spot n -> freq used this run
        self.logo_image = None

        self._setup_styles()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._poll_id = self.root.after(150, self._poll_queue)

    # ------------------------------------------------------------ styles
    def _setup_styles(self):
        s = ttk.Style(self.root)
        s.theme_use('clam')
        s.configure('TFrame', background=self.CLR_BG_DARK)
        s.configure('TLabel', background=self.CLR_BG_DARK,
                    foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        s.configure('TCheckbutton', background=self.CLR_BG_DARK,
                    foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        s.configure('TRadiobutton', background=self.CLR_BG_DARK,
                    foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        s.configure('TButton', font=self.FONT_BASE, padding=(10, 8),
                    foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        s.map('TButton', background=[('active', self.CLR_ACCENT_GOLD)],
              foreground=[('active', self.CLR_TEXT_DARK)])
        s.configure('Go.TButton', font=('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold'),
                    background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK,
                    padding=(14, 10))
        s.configure('Danger.TButton', background=self.CLR_ACCENT_RED,
                    foreground=self.CLR_FG_LIGHT)
        s.configure('TCombobox', font=self.FONT_BASE)
        s.configure('TNotebook', background=self.CLR_BG_DARK)

    # ------------------------------------------------------------ layout
    def _build(self):
        self._build_header()
        body = tk.Frame(self.root, bg=self.CLR_BG_DARK)
        body.pack(fill='both', expand=True, padx=8, pady=6)

        left = tk.Frame(body, bg=self.CLR_BG_DARK, width=640)
        left.pack(side='left', fill='both', expand=False)
        left.pack_propagate(False)
        right = tk.Frame(body, bg=self.CLR_BG_DARK)
        right.pack(side='left', fill='both', expand=True, padx=(8, 0))

        # headline banner
        self.headline = tk.Label(left, text="Not connected.", anchor='w',
                                 justify='left', wraplength=600,
                                 bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT,
                                 font=self.FONT_HEADLINE, padx=12, pady=10)
        self.headline.pack(fill='x', pady=(0, 6))

        # step strip
        strip = tk.Frame(left, bg=self.CLR_BG_DARK)
        strip.pack(fill='x')
        self.step_labels = []
        for i, name in enumerate(self.STEPS):
            lbl = tk.Label(strip, text=name, bg=self.CLR_BG_DARK,
                           fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL,
                           padx=6, pady=3)
            lbl.pack(side='left')
            self.step_labels.append(lbl)

        # stacked screens
        self.screen_host = tk.Frame(left, bg=self.CLR_BG_DARK)
        self.screen_host.pack(fill='both', expand=True, pady=(6, 0))
        self.screens = []
        for builder in (self._screen_connect, self._screen_choose,
                        self._screen_open, self._screen_short,
                        self._screen_verify, self._screen_done):
            fr = tk.Frame(self.screen_host, bg=self.CLR_BG_DARK)
            builder(fr)
            self.screens.append(fr)

        # right: plot + console
        self._build_plot(right)
        self._build_console(right)
        self._show_step(0)

    def _build_header(self):
        hf = tk.Frame(self.root, bg=self.CLR_HEADER)
        hf.pack(side='top', fill='x')
        self._try_logo(hf)
        Label(hf, text="E4980A: Open / Short Correction",
              bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD,
              font=('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
              ).pack(side='left', padx=16, pady=10)
        Label(hf, text=f"Version: {PROGRAM_VERSION}", bg=self.CLR_HEADER,
              fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL
              ).pack(side='right', padx=20, pady=10)

    def _try_logo(self, parent):
        if not PIL_AVAILABLE:
            return
        here = os.path.dirname(os.path.abspath(__file__))
        for rel in (("..", "..", "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"),
                    ("..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")):
            p = os.path.join(here, *rel)
            if os.path.exists(p):
                try:
                    img = Image.open(p)
                    img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), RESAMPLE_FILTER)
                    self.logo_image = ImageTk.PhotoImage(img)
                    Label(parent, image=self.logo_image, bg=self.CLR_HEADER
                          ).pack(side='left', padx=(12, 0), pady=4)
                except Exception:
                    pass
                return

    def _build_plot(self, parent):
        box = LabelFrame(parent, text='Residuals after correction', bg=self.CLR_BG_DARK,
                         fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        box.pack(fill='both', expand=True)
        self.fig = Figure(figsize=(7, 4.6), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_open = self.fig.add_subplot(121)
        self.ax_short = self.fig.add_subplot(122)
        for ax, t in ((self.ax_open, "Open fixture: |Cp| residual"),
                      (self.ax_short, "Shorting bar: |Rs| residual")):
            ax.set_title(t, fontsize=10)
            ax.set_xscale('log')
            ax.set_yscale('log')
            ax.set_xlabel("Frequency (Hz)")
            ax.grid(True, which='both', alpha=0.3)
        self.ax_open.set_ylabel("|Cp| (F)")
        self.ax_short.set_ylabel("|Rs| (Ohm)")
        self.fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.fig, master=box)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=4, pady=4)

    def _build_console(self, parent):
        box = LabelFrame(parent, text='Log', bg=self.CLR_BG_DARK,
                         fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        box.pack(fill='both', expand=False)
        self.console = scrolledtext.ScrolledText(
            box, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', bd=0, height=13)
        self.console.pack(fill='both', expand=True, padx=4, pady=4)
        self.show_cmds = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="Show every command and reply in this log "
                        "(always written to the console file)",
                        variable=self.show_cmds).pack(anchor='w', padx=6, pady=(0, 4))
        self.log("Ready. Choose a folder, pick the meter, press Connect & inspect.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not installed. pip install pyvisa")

    # ------------------------------------------------------------ screens
    def _screen_connect(self, fr):
        Label(fr, text="Where should the files go?", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(2, 8))
        self.folder_var = tk.StringVar(value="(no folder chosen)")
        ttk.Label(row, textvariable=self.folder_var, font=self.FONT_SUB_LABEL,
                  wraplength=430).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text="Browse…", command=self._browse).pack(side='right')

        Label(fr, text="Which instrument is the LCR meter?", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        row2 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row2.pack(fill='x', pady=(2, 8))
        self.visa_cb = ttk.Combobox(row2, width=52, font=self.FONT_SUB_LABEL)
        self.visa_cb.pack(side='left', fill='x', expand=True)
        self.visa_cb.set("GPIB0::17::INSTR")
        ttk.Button(row2, text="Scan", command=self._scan_visa).pack(side='right', padx=(6, 0))

        self.all_spots_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text="Inspect all 201 spot points, not only the ones "
                        "holding data (slow, ~400 queries)",
                        variable=self.all_spots_var).pack(anchor='w', pady=(0, 6))

        self.btn_connect = ttk.Button(fr, text="Connect & inspect  ▶", style='Go.TButton',
                                      command=self._do_connect)
        self.btn_connect.pack(anchor='w', pady=(4, 10))

        Label(fr, text="Inspecting reads the switches, cable length and spot data. "
              "It changes nothing in the meter and saves a backup file first.",
              font=self.FONT_SUB_LABEL, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w')

        self.state_text = tk.Text(fr, height=9, width=70, font=self.FONT_CONSOLE,
                                  bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
                                  bd=0, state='disabled', wrap='word')
        self.state_text.pack(fill='x', pady=(6, 6))

        row3 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row3.pack(fill='x', pady=(4, 0))
        self.btn_next1 = ttk.Button(row3, text="Redo the correction  ▶", style='Go.TButton',
                                    command=lambda: self._show_step(1), state='disabled')
        self.btn_next1.pack(side='left')
        self.btn_restore = ttk.Button(row3, text="Restore spot data from a file…",
                                      command=self._do_restore, state='disabled')
        self.btn_restore.pack(side='left', padx=(10, 0))

    def _screen_choose(self, fr):
        Label(fr, text="Cable length the scans will use", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x')
        self.length_var = tk.StringVar(value="1")
        for v in ("0", "1", "2", "4"):
            ttk.Radiobutton(row, text=f"{v} m", value=v, variable=self.length_var
                            ).pack(side='left', padx=(0, 14))
        Label(fr, text="All PICA scan modules default to 1 m. The manual says a "
              "different cable length after correction makes the correction "
              "invalid without warning (p. 117), so correct at the value you "
              "will measure with.", font=self.FONT_SUB_LABEL, wraplength=600,
              justify='left', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT
              ).pack(anchor='w', pady=(2, 10))

        Label(fr, text="Method", font=self.FONT_TITLE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        Label(fr, text="All frequency points (the meter measures 51 preset "
              "frequencies and interpolates between them). Always done.",
              font=self.FONT_SUB_LABEL, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w')
        self.spots_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text="Also add spot points at these frequencies "
                        "(exact there, and exportable to a file):",
                        variable=self.spots_var, command=self._toggle_spots
                        ).pack(anchor='w', pady=(4, 0))
        self.spot_freq_entry = tk.Text(fr, height=3, width=70, font=self.FONT_CONSOLE,
                                       bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, bd=0)
        self.spot_freq_entry.insert('1.0', ", ".join(str(f) for f in TSCAN_FREQS_HZ))
        self.spot_freq_entry.pack(fill='x', pady=(2, 8))
        self.spot_freq_entry.config(state='disabled')

        Label(fr, text="Where is the fixture right now?", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        row2 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row2.pack(fill='x')
        self.where_var = tk.StringVar(value="Probe assembled, room temperature")
        self.where_cb = ttk.Combobox(row2, textvariable=self.where_var, width=36,
                                     values=["Probe assembled, room temperature",
                                             "Probe inside PPMS / dewar, cold",
                                             "Bench fixture at the meter",
                                             "Other (type)"])
        self.where_cb.pack(side='left')
        ttk.Label(row2, text="   Temperature (K):").pack(side='left')
        self.temp_var = tk.StringVar(value="300")
        ttk.Entry(row2, textvariable=self.temp_var, width=8).pack(side='left')

        self.clear_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text="Clear the spot data first (writes zeros; the "
                        "all-points tables are simply overwritten by the new "
                        "measurement)", variable=self.clear_var
                        ).pack(anchor='w', pady=(10, 0))

        # advanced (hidden)
        self.adv_shown = False
        self.btn_adv = ttk.Button(fr, text="Advanced ▸", command=self._toggle_adv)
        self.btn_adv.pack(anchor='w', pady=(10, 0))
        self.adv = tk.Frame(fr, bg=self.CLR_BG_DARK)
        g = self.adv
        r = 0
        self.level_var = tk.StringVar(value="1.0")
        self.alc_var = tk.BooleanVar(value=True)
        ttk.Label(g, text="Test level (Vrms), must match the scans:").grid(row=r, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.level_var, width=8).grid(row=r, column=1, sticky='w')
        ttk.Checkbutton(g, text="ALC on", variable=self.alc_var).grid(row=r, column=2, sticky='w', padx=8)
        r += 1
        self.th_vars = {}
        for key, label in (("open_cp_F", "Open |Cp| limit (F)"),
                           ("open_g_S", "Open |G| limit (S)"),
                           ("short_rs_Ohm", "Short |Rs| limit (Ohm)"),
                           ("short_ls_H", "Short |Ls| limit (H)")):
            ttk.Label(g, text=label + ":").grid(row=r, column=0, sticky='w')
            var = tk.StringVar(value=f"{DEFAULT_THRESH[key]:g}")
            self.th_vars[key] = var
            ttk.Entry(g, textvariable=var, width=10).grid(row=r, column=1, sticky='w')
            r += 1
        ttk.Label(g, text="Verify frequencies (Hz):").grid(row=r, column=0, sticky='nw')
        self.verify_freq_entry = tk.Text(g, height=2, width=48, font=self.FONT_CONSOLE,
                                         bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, bd=0)
        self.verify_freq_entry.insert('1.0', ", ".join(str(f) for f in TSCAN_FREQS_HZ))
        self.verify_freq_entry.grid(row=r, column=1, columnspan=2, sticky='w')
        r += 1
        ttk.Label(g, text="Load correction: not implemented. It needs a standard "
                  "of known value at every frequency (manual p. 211); none is "
                  "available. Open + short is the manual's 'precise "
                  "measurements' case.", wraplength=560, font=self.FONT_SUB_LABEL
                  ).grid(row=r, column=0, columnspan=3, sticky='w', pady=(6, 0))
        r += 1
        ttk.Button(g, text="Full preset (:SYST:PRES) — erases ALL correction data",
                   style='Danger.TButton', command=self._do_preset
                   ).grid(row=r, column=0, columnspan=3, sticky='w', pady=(8, 0))

        row3 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row3.pack(fill='x', pady=(14, 0))
        ttk.Button(row3, text="◀ Back", command=lambda: self._show_step(0)).pack(side='left')
        ttk.Button(row3, text="Go to the Open step  ▶", style='Go.TButton',
                   command=self._go_open).pack(side='left', padx=(10, 0))

    def _screen_open(self, fr):
        Label(fr, text="OPEN", font=self.FONT_HEADLINE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        Label(fr, text=("Remove the sample. Leave the terminals open exactly as they "
                        "will be during the measurement, with the same cable and "
                        "fixture. Then keep your hands away from the fixture "
                        "(manual p. 210)."),
              font=self.FONT_BASE, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w', pady=(4, 8))
        self.live_open = tk.Label(fr, text="Live reading: —", font=self.FONT_TITLE,
                                  bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, padx=10, pady=8,
                                  anchor='w', justify='left')
        self.live_open.pack(fill='x', pady=(0, 8))
        Label(fr, text=("Nothing has been written to the meter's correction yet. When you "
                        "press Continue the meter measures the open fixture at its 51 "
                        "preset frequencies (about a minute) and OVERWRITES its stored "
                        "open table. The old table cannot be recovered; the backup from "
                        "step 1 holds the spot data only."),
              font=self.FONT_SUB_LABEL, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w', pady=(0, 10))
        self.progress_open = ttk.Label(fr, text="", font=self.FONT_SUB_LABEL)
        self.progress_open.pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(10, 0))
        ttk.Button(row, text="◀ Back", command=lambda: self._show_step(1)).pack(side='left')
        self.btn_open_go = ttk.Button(row, text="Continue: measure OPEN now  ▶", style='Go.TButton',
                                      command=self._do_open)
        self.btn_open_go.pack(side='left', padx=(10, 0))

    def _screen_short(self, fr):
        Label(fr, text="SHORT", font=self.FONT_HEADLINE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        Label(fr, text=("Put the shorting bar between the high and low terminals at the "
                        "sample position. Use a clean, high-conductivity metal plate that "
                        "does not corrode (manual p. 210). Hands away."),
              font=self.FONT_BASE, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w', pady=(4, 8))
        self.live_short = tk.Label(fr, text="Live reading: —", font=self.FONT_TITLE,
                                   bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, padx=10, pady=8,
                                   anchor='w', justify='left')
        self.live_short.pack(fill='x', pady=(0, 8))
        Label(fr, text=("Continue makes the meter measure the short at its 51 preset "
                        "frequencies and OVERWRITE its stored short table. Afterwards the "
                        "module switches open and short correction ON."),
              font=self.FONT_SUB_LABEL, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w', pady=(0, 10))
        self.progress_short = ttk.Label(fr, text="", font=self.FONT_SUB_LABEL)
        self.progress_short.pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(10, 0))
        self.btn_short_go = ttk.Button(row, text="Continue: measure SHORT now  ▶", style='Go.TButton',
                                       command=self._do_short)
        self.btn_short_go.pack(side='left')

    def _screen_verify(self, fr):
        Label(fr, text="VERIFY", font=self.FONT_HEADLINE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        self.verify_instr = Label(fr, text="", font=self.FONT_BASE, wraplength=600,
                                  justify='left', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT)
        self.verify_instr.pack(anchor='w', pady=(4, 8))
        Label(fr, text=("The meter cannot check its own correction data (manual p. 117). "
                        "This step measures the residuals at the verify frequencies with "
                        "correction OFF and then ON. The difference is what the correction "
                        "removed; the ON value is what the thresholds judge."),
              font=self.FONT_SUB_LABEL, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w', pady=(0, 10))
        self.progress_verify = ttk.Label(fr, text="", font=self.FONT_SUB_LABEL)
        self.progress_verify.pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(10, 0))
        self.btn_verify_go = ttk.Button(row, text="Continue  ▶", style='Go.TButton',
                                        command=self._do_verify_step)
        self.btn_verify_go.pack(side='left')
        self.verify_phase = "short"

    def _screen_done(self, fr):
        Label(fr, text="DONE", font=self.FONT_HEADLINE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        self.done_text = tk.Text(fr, height=16, width=70, font=self.FONT_CONSOLE,
                                 bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
                                 bd=0, state='disabled', wrap='word')
        self.done_text.pack(fill='x', pady=(6, 6))
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(6, 0))
        ttk.Button(row, text="Inspect again (new file set)",
                   command=self._restart).pack(side='left')
        ttk.Button(row, text="Open the folder", command=self._open_folder
                   ).pack(side='left', padx=(10, 0))

    # ------------------------------------------------------------ helpers
    def _show_step(self, i):
        self._stop_live()
        self.step = i
        for fr in self.screens:
            fr.pack_forget()
        self.screens[i].pack(fill='both', expand=True)
        for j, lbl in enumerate(self.step_labels):
            lbl.config(bg=self.CLR_HEADER if j == i else self.CLR_BG_DARK,
                       fg=self.CLR_ACCENT_GOLD if j == i else self.CLR_FG_LIGHT)
        if i == 2:
            self.live_func = "CPG"
            self._start_live(self.live_open)
        elif i == 3:
            self.live_func = "LSRS"
            self._start_live(self.live_short)
        elif i == 4:
            self.verify_phase = "short"
            self.verify_instr.config(
                text="Leave the shorting bar in place. Press Continue to measure "
                     "the short residuals.")
            self.btn_verify_go.config(text="Continue: measure with the bar  ▶")

    def _set_headline(self, text, ok=None):
        fg = self.CLR_FG_LIGHT if ok is None else (self.CLR_PASS if ok else self.CLR_WARN)
        self.headline.config(text=text, fg=fg)

    def _set_text(self, widget, text):
        widget.config(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.config(state='disabled')

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self.console.config(state='normal')
        self.console.insert('end', line + "\n")
        try:
            n = int(self.console.index('end-1c').split('.')[0])
            if n > self.CONSOLE_MAX_LINES:
                self.console.delete('1.0', f'{n - self.CONSOLE_MAX_LINES + 1}.0')
        except tk.TclError:
            pass
        self.console.see('end')
        self.console.config(state='disabled')
        if self.paths.get("log"):
            self._durable_append(self.paths["log"],
                                 datetime.now().strftime("%Y-%m-%d ") + line + "\n")

    def _on_record(self, entry):
        """Backend callback (may run in the worker thread): queue it."""
        self.queue.put(("record", entry))

    def _write_record(self, entry):
        ts, kind, cmd, reply = entry
        line = f"{ts}\t{kind}\t{cmd}\t{reply}\n"
        if self.paths.get("console"):
            self._durable_append(self.paths["console"], line)
        else:
            self._console_buffer.append(line)
        if self.show_cmds.get():
            self.log(f"  {kind} {cmd}" + (f"  ->  {reply}" if reply else ""))

    @staticmethod
    def _durable_append(path, text):
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def _durable_write(self, path, text):
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def _browse(self):
        p = filedialog.askdirectory()
        if p:
            self.dest_folder = p
            self.folder_var.set(p)
            self.log(f"Folder: {p}")

    def _visa_addr(self):
        return self.visa_cb.get().split("  ->")[0].strip()

    def _scan_visa(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            res = rm.list_resources()
        except Exception as e:
            self.log(f"ERROR: VISA scan failed: {e}")
            return
        found, pick = [], None
        for r in res:
            idn = "no response"
            try:
                with rm.open_resource(r) as dev:
                    dev.timeout = 2000
                    dev.read_termination = "\n"
                    dev.write_termination = "\n"
                    idn = dev.query("*IDN?").strip()
            except Exception:
                pass
            label = f"{r}  ->  {idn}"
            found.append(label)
            self.log("  " + label)
            if pick is None and "E4980" in idn:
                pick = label
        self.visa_cb['values'] = found
        if pick:
            self.visa_cb.set(pick)
            self.log("E4980A auto-selected.")
        elif not found:
            self.log("No VISA instruments found.")

    def _parse_freqs(self, widget):
        raw = widget.get('1.0', 'end').replace("\n", ",").replace(";", ",")
        out = []
        for tok in raw.split(","):
            tok = tok.strip()
            if tok:
                f = float(tok)
                if not 20 <= f <= 2e6:
                    raise ValueError(f"{f:g} Hz is outside 20 Hz to 2 MHz")
                out.append(f)
        if not out:
            raise ValueError("no frequencies given")
        return out

    def _toggle_spots(self):
        self.spot_freq_entry.config(state='normal' if self.spots_var.get() else 'disabled')

    def _toggle_adv(self):
        self.adv_shown = not self.adv_shown
        if self.adv_shown:
            self.adv.pack(fill='x', pady=(6, 0))
            self.btn_adv.config(text="Advanced ▾")
        else:
            self.adv.pack_forget()
            self.btn_adv.config(text="Advanced ▸")

    # ------------------------------------------------------------ worker plumbing
    def _run_worker(self, fn, *args):
        if self.busy:
            self.log("Busy: wait for the current step to finish.")
            return
        self.busy = True
        self._stop_live()

        def target():
            try:
                fn(*args)
            except Exception as e:
                self.queue.put(("error", f"{e}\n{traceback.format_exc()}"))
            finally:
                self.queue.put(("idle", None))
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "record":
                    self._write_record(payload)
                elif kind == "log":
                    self.log(payload)
                elif kind == "headline":
                    self._set_headline(*payload)
                elif kind == "progress":
                    widget, text = payload
                    widget.config(text=text)
                elif kind == "state":
                    self._show_state(payload)
                elif kind == "step":
                    self._show_step(payload)
                elif kind == "plot":
                    self._update_plot()
                elif kind == "done":
                    self._show_done(payload)
                elif kind == "live":
                    widget, text = payload
                    widget.config(text=text)
                elif kind == "error":
                    self.log("ERROR: " + payload.splitlines()[0])
                    for ln in payload.splitlines()[1:]:
                        self.log("    " + ln)
                    self._set_headline("Something went wrong. See the log.", ok=False)
                    try:
                        self.root.bell()
                    except Exception:
                        pass
                elif kind == "idle":
                    self.busy = False
                    if self.step in (2, 3) and self.connected:
                        self._start_live(self.live_open if self.step == 2 else self.live_short)
        except queue.Empty:
            pass
        self._poll_id = self.root.after(150, self._poll_queue)

    # ------------------------------------------------------------ live reading
    def _start_live(self, widget):
        self._stop_live()
        if not self.connected or self.busy:
            return
        self._live_widget = widget
        self._live_id = self.root.after(300, self._live_tick)

    def _stop_live(self):
        if self._live_id is not None:
            try:
                self.root.after_cancel(self._live_id)
            except Exception:
                pass
            self._live_id = None

    def _live_tick(self):
        self._live_id = None
        if not self.connected or self.busy or self.step not in (2, 3):
            return
        try:
            f = 100000.0
            a, b, _ = self.backend.measure(self.live_func, f)
            if self.live_func == "CPG":
                txt = f"Live at 100 kHz, correction as currently set:  Cp = {a*1e12:.3f} pF    G = {b*1e6:.3f} µS"
            else:
                txt = f"Live at 100 kHz, correction as currently set:  Rs = {b*1e3:.3f} mΩ    Ls = {a*1e9:.2f} nH"
            self._live_widget.config(text=txt)
        except Exception as e:
            self._live_widget.config(text=f"Live reading failed: {e}")
        self._live_id = self.root.after(900, self._live_tick)

    # ------------------------------------------------------------ step 1
    def _do_connect(self):
        if not self.dest_folder:
            self.log("Choose a folder first: every step writes a file.")
            return
        if self.connected:
            self.backend.close()
            self.connected = False
        self.tag = "E4980A_Correction_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(self.dest_folder, self.tag)
        self.paths = {
            "backup": base + "_backup_before.txt",
            "after": base + "_spotdata_after.txt",
            "residuals": base + "_residuals.txt",
            "log": base + "_log.txt",
            "console": base + "_console.txt",
        }
        self._durable_write(self.paths["console"], "Time\tW/Q\tCommand\tReply\n")
        for ln in self._console_buffer:
            self._durable_append(self.paths["console"], ln)
        self._console_buffer.clear()
        self._durable_write(self.paths["log"], f"# {self.tag} log\n")
        self.log(f"File set: {self.tag}_*.txt in {self.dest_folder}")
        self._run_worker(self._worker_connect, self._visa_addr(), self.all_spots_var.get())

    def _worker_connect(self, addr, all_spots):
        q = self.queue
        q.put(("headline", ("Connecting…", None)))
        idn = self.backend.connect(addr)
        q.put(("log", f"Connected: {idn}"))
        q.put(("log", "Inspecting (read-only)…"))
        st = self.backend.read_state(all_spots=all_spots)
        self.state_before = st
        meta = {"file": "backup_before", "written": datetime.now().isoformat(timespec='seconds'),
                "visa": addr}
        self._durable_write(self.paths["backup"], format_spot_file(st, meta))
        q.put(("log", f"Backup written: {os.path.basename(self.paths['backup'])}"))
        q.put(("state", st))

    def _show_state(self, st):
        self.connected = True
        n_on = sum(1 for s in st["spots"].values() if s["on"])
        n_data = sum(1 for n in range(1, N_SPOTS + 1)
                     if any(v != 0 for v in st["spot_data"][(n-1)*6:n*6]))
        sw = []
        sw.append("OPEN " + ("on" if st["open_on"] else "off"))
        sw.append("SHORT " + ("on" if st["short_on"] else "off"))
        sw.append("LOAD " + ("on" if st["load_on"] else "off"))
        head = (f"The meter holds correction switches {', '.join(sw)}, cable length "
                f"{st['length_m']} m, {n_on} spot point{'s' if n_on != 1 else ''} enabled. "
                "Backed up.")
        self._set_headline(head)
        lines = [f"IDN:            {st['idn']}",
                 f"Switches:       {', '.join(sw)}",
                 f"Cable length:   {st['length_m']} m",
                 f"Mode:           {st['method']}  (must stay SINGLE)",
                 f"Load type:      {st['load_type']}",
                 f"Level / ALC:    {st['level_V']} V / {st['alc']}",
                 f"Spot points:    {n_on} enabled, {n_data} holding data",
                 "All-points (51-frequency) tables: present or not, the meter",
                 "cannot report them; only their effect is measurable (step 5)."]
        for n, s in sorted(st["spots"].items()):
            if s["on"] or s["freq"] > 0:
                lines.append(f"   spot {n:3d}: {s['freq']:>12g} Hz  {'ON ' if s['on'] else 'off'}")
        self._set_text(self.state_text, "\n".join(lines))
        self.log(head)
        if st["method"].upper().startswith("MULT"):
            self.log("WARNING: correction mode is MULTI (scanner). This module "
                     "expects SINGLE and will not change the mode itself.")
        self.btn_next1.config(state='normal')
        self.btn_restore.config(state='normal')

    # ------------------------------------------------------------ step 2 -> 3
    def _go_open(self):
        try:
            self.level = float(self.level_var.get())
            if not 0 < self.level <= 2.0:
                raise ValueError("level must be 0 < V <= 2")
            self.length = int(self.length_var.get())
            self.spot_freqs = self._parse_freqs(self.spot_freq_entry) if self.spots_var.get() else []
            if len(self.spot_freqs) > N_SPOTS:
                raise ValueError(f"at most {N_SPOTS} spot frequencies")
            self.verify_freqs = self._parse_freqs(self.verify_freq_entry)
            for k, var in self.th_vars.items():
                self.thresh[k] = float(var.get())
            float(self.temp_var.get())
        except ValueError as e:
            self.log(f"Check the inputs: {e}")
            return
        self._run_worker(self._worker_prepare)

    def _worker_prepare(self):
        q = self.queue
        q.put(("log", f"Applying settings: cable {self.length} m, {self.level} Vrms, "
                      f"ALC {'on' if self.alc_var.get() else 'off'}, aperture LONG. "
                      "Settings only, no correction data changed."))
        self.backend.apply_settings(self.length, self.level, self.alc_var.get())
        if self.state_before and self.state_before["length_m"] != self.length:
            q.put(("log", f"NOTE: cable length changed from {self.state_before['length_m']} m "
                          f"to {self.length} m. The stored correction is now for the wrong "
                          "plane until the new one is measured (manual p. 117)."))
        if self.clear_var.get():
            q.put(("log", "Clearing spot data (writing 1206 zeros)…"))
            errs = self.backend.write_spot_data([0.0] * N_SPOT_VALUES)
            for n in sorted(self.state_before["spots"]):
                self.backend.setup_spot(n, self.state_before["spots"][n]["freq"] or 20.0, on=False)
            if errs:
                q.put(("log", f"Meter errors after clear: {errs}"))
            q.put(("log", "Spot data cleared. All-points tables will be overwritten "
                          "by the measurement itself."))
        q.put(("headline", (f"Settings applied at {self.length} m. Ready for OPEN.", None)))
        q.put(("step", 2))

    # ------------------------------------------------------------ step 3
    def _do_open(self):
        self._run_worker(self._worker_correct, "OPEN")

    def _do_short(self):
        self._run_worker(self._worker_correct, "SHOR")

    def _worker_correct(self, which):
        q = self.queue
        name = "OPEN" if which == "OPEN" else "SHORT"
        prog = self.progress_open if which == "OPEN" else self.progress_short
        q.put(("headline", (f"Measuring {name} at 51 preset frequencies… do not touch the fixture.", None)))
        q.put(("progress", (prog, f"All-points {name} running (about a minute)…")))
        t0 = time.time()
        q.put(("log", f"WRITE MOMENT: sending {':CORR:OPEN' if which == 'OPEN' else ':CORR:SHOR'} "
                      f"— the meter now overwrites its stored {name} table."))
        errs = self.backend.execute_all_points(which)
        dt = time.time() - t0
        if errs:
            q.put(("log", f"Meter reported errors during {name}: {errs}. "
                          "Manual p. 117/120: check the fixture, ALC and level, then redo."))
            q.put(("headline", (f"{name} finished with meter errors. See the log.", False)))
        else:
            q.put(("log", f"All-points {name} done in {dt:.0f} s. Stored {name} table replaced."))
        if self.spot_freqs:
            q.put(("log", f"Spot {name} at {len(self.spot_freqs)} frequencies…"))
            for i, f in enumerate(self.spot_freqs, start=1):
                self.spot_map[i] = f
                self.backend.setup_spot(i, f, on=True)
                e2 = self.backend.execute_spot(i, which)
                q.put(("progress", (prog, f"Spot {name} {i}/{len(self.spot_freqs)}: {f:g} Hz")))
                if e2:
                    q.put(("log", f"  spot {i} ({f:g} Hz) errors: {e2}"))
            q.put(("log", f"Spot {name} done."))
        q.put(("progress", (prog, f"{name} complete.")))
        if which == "OPEN":
            q.put(("headline", ("OPEN stored. Now the shorting bar.", True)))
            q.put(("step", 3))
        else:
            self.backend.set_switches(True, True)
            q.put(("log", "Correction switches OPEN and SHORT set ON. From now on every "
                          "reading, here and in every PICA scan, uses the new data."))
            q.put(("headline", ("SHORT stored, corrections ON. Now verify.", True)))
            q.put(("step", 4))

    # ------------------------------------------------------------ step 5
    def _do_verify_step(self):
        if self.verify_phase == "short":
            self._run_worker(self._worker_verify, "short")
        else:
            self._run_worker(self._worker_verify, "open")

    def _worker_verify(self, phase):
        q = self.queue
        fs = self.verify_freqs
        if phase == "short":
            self.residual_rows = [{"f": f} for f in fs]
            func, ka, kb = "LSRS", "short_ls", "short_rs"
            q.put(("headline", ("Measuring short residuals, correction OFF then ON…", None)))
        else:
            func, ka, kb = "CPG", "open_cp", "open_g"
            q.put(("headline", ("Measuring open residuals, correction OFF then ON…", None)))
        for sw, suffix in ((False, "_off"), (True, "_on")):
            self.backend.set_switches(sw, sw)
            time.sleep(0.3)
            for i, row in enumerate(self.residual_rows, start=1):
                a, b, status = self.backend.measure(func, row["f"])
                row[ka + suffix] = a
                row[kb + suffix] = b
                row[phase + "_status" + suffix] = status
                q.put(("progress", (self.progress_verify,
                                    f"{phase} residuals, correction {'ON' if sw else 'OFF'}: "
                                    f"{i}/{len(fs)}  {row['f']:g} Hz")))
        self.backend.set_switches(True, True)
        q.put(("plot", None))
        if phase == "short":
            q.put(("log", "Short residuals measured (OFF and ON). Corrections left ON."))
            q.put(("headline", ("Short residuals done. Remove the bar and leave the terminals open.", None)))
            self.verify_phase = "open"
            self.queue.put(("progress", (self.verify_instr,
                                         "Remove the shorting bar and leave the terminals open, "
                                         "as in step 3. Press Continue to measure the open residuals.")))
            self.queue.put(("progress", (self.btn_verify_go, "Continue: measure open  ▶")))
        else:
            ok, worst = judge_residuals(self.residual_rows, self.thresh)
            self._save_results(ok, worst)
            q.put(("done", (ok, worst)))

    def _save_results(self, ok, worst):
        meta = {"file": "spotdata_after",
                "written": datetime.now().isoformat(timespec='seconds'),
                "where": self.where_var.get(), "temperature_K": self.temp_var.get(),
                "spot_frequencies_Hz": ",".join(f"{f:g}" for f in self.spot_freqs) or "none",
                "verdict": "PASS" if ok else "ATTENTION", "worst": worst}
        st_after = self.backend.read_state(all_spots=False)
        self.state_after = st_after
        self._durable_write(self.paths["after"], format_spot_file(st_after, meta))
        lines = [f"# E4980A open/short correction residuals  (PICA Correction_OpenShort v{PROGRAM_VERSION})",
                 f"# Tag: {self.tag}",
                 f"# IDN: {self.backend.idn}",
                 f"# Cable: {self.length} m | Level: {self.level} Vrms | ALC: {self.alc_var.get()} | Aperture: LONG",
                 f"# Method: all-points{' + spot points' if self.spot_freqs else ''} | Mode: {st_after['method']}",
                 f"# Where: {self.where_var.get()} | Temperature_K: {self.temp_var.get()}",
                 f"# Thresholds: open |Cp|<{self.thresh['open_cp_F']:g} F, |G|<{self.thresh['open_g_S']:g} S; "
                 f"short |Rs|<{self.thresh['short_rs_Ohm']:g} Ohm, |Ls|<{self.thresh['short_ls_H']:g} H",
                 f"# Verdict: {'PASS' if ok else 'ATTENTION'} | Worst: {worst}",
                 "Frequency_Hz\tOpen_Cp_F_corrOFF\tOpen_G_S_corrOFF\tOpen_Cp_F_corrON\tOpen_G_S_corrON\t"
                 "Short_Rs_Ohm_corrOFF\tShort_Ls_H_corrOFF\tShort_Rs_Ohm_corrON\tShort_Ls_H_corrON\tPass"]
        for r in self.residual_rows:
            p_ok, _ = judge_residuals([r], self.thresh)
            lines.append("\t".join([
                f"{r['f']:g}",
                f"{r.get('open_cp_off', float('nan')):.6g}", f"{r.get('open_g_off', float('nan')):.6g}",
                f"{r.get('open_cp_on', float('nan')):.6g}", f"{r.get('open_g_on', float('nan')):.6g}",
                f"{r.get('short_rs_off', float('nan')):.6g}", f"{r.get('short_ls_off', float('nan')):.6g}",
                f"{r.get('short_rs_on', float('nan')):.6g}", f"{r.get('short_ls_on', float('nan')):.6g}",
                "1" if p_ok else "0"]))
        self._durable_write(self.paths["residuals"], "\n".join(lines) + "\n")

    def _update_plot(self):
        for ax in (self.ax_open, self.ax_short):
            ax.cla()
            ax.set_xscale('log')
            ax.set_yscale('log')
            ax.set_xlabel("Frequency (Hz)")
            ax.grid(True, which='both', alpha=0.3)
        rows = self.residual_rows
        f = [r["f"] for r in rows]

        def series(key):
            vals = [abs(r[key]) if r.get(key) is not None else float('nan') for r in rows]
            return [max(v, 1e-18) for v in vals]
        if rows and "open_cp_on" in rows[0]:
            self.ax_open.plot(f, series("open_cp_off"), '--', color='gray', label='correction OFF')
            self.ax_open.plot(f, series("open_cp_on"), 'o-', color=self.CLR_ACCENT_GOLD, label='correction ON')
            self.ax_open.axhline(self.thresh["open_cp_F"], color=self.CLR_WARN, lw=1, label='limit')
            self.ax_open.legend(fontsize=8)
        self.ax_open.set_title("Open fixture: |Cp| residual", fontsize=10)
        self.ax_open.set_ylabel("|Cp| (F)")
        if rows and "short_rs_on" in rows[0]:
            self.ax_short.plot(f, series("short_rs_off"), '--', color='gray', label='correction OFF')
            self.ax_short.plot(f, series("short_rs_on"), 'o-', color=self.CLR_ACCENT_GOLD, label='correction ON')
            self.ax_short.axhline(self.thresh["short_rs_Ohm"], color=self.CLR_WARN, lw=1, label='limit')
            self.ax_short.legend(fontsize=8)
        self.ax_short.set_title("Shorting bar: |Rs| residual", fontsize=10)
        self.ax_short.set_ylabel("|Rs| (Ohm)")
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _show_done(self, payload):
        ok, worst = payload
        if ok:
            self._set_headline("PASS. Residuals within limits at every verify frequency.", ok=True)
        else:
            self._set_headline(f"ATTENTION. Worst: {worst}", ok=False)
        txt = [f"Verdict: {'PASS' if ok else 'ATTENTION'}",
               f"Worst residual: {worst or 'none'}",
               "",
               f"Cable length {self.length} m, {self.level} Vrms, ALC {'on' if self.alc_var.get() else 'off'}",
               "Method: all-points" + (f" + {len(self.spot_freqs)} spot points" if self.spot_freqs else ""),
               f"Where: {self.where_var.get()} at {self.temp_var.get()} K",
               "",
               "Files:"]
        for k in ("backup", "after", "residuals", "log", "console"):
            txt.append("  " + os.path.basename(self.paths[k]))
        txt += ["",
                "The correction is stored in the meter and switched ON. It stays",
                "valid until the cable length, fixture or level changes, or a",
                "correction is measured again. Scan modules re-apply it at Start."]
        self._set_text(self.done_text, "\n".join(txt))
        self.log(f"Done: {'PASS' if ok else 'ATTENTION'}. {worst}")
        self._show_step(5)

    # ------------------------------------------------------------ restore / preset
    def _do_restore(self):
        p = filedialog.askopenfilename(title="Spot-data file to restore",
                                       initialdir=self.dest_folder,
                                       filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not p:
            return
        try:
            meta, values, spots = parse_spot_file(open(p, encoding='utf-8').read())
        except Exception as e:
            self.log(f"Cannot read {os.path.basename(p)}: {e}")
            return
        n_on = sum(1 for s in spots.values() if s["on"])
        length = meta.get("length_m", "?")
        if not messagebox.askyesno(
                "Restore spot data",
                f"Write {N_SPOT_VALUES} spot values from\n{os.path.basename(p)}\n\n"
                f"{n_on} spot points enabled, cable length {length} m, "
                f"switches OPEN {meta.get('open_on')} / SHORT {meta.get('short_on')}.\n\n"
                "This is the only action in which the PC writes correction numbers "
                "into the meter. The all-points tables are not in the file and stay "
                "as they are.\n\nProceed?"):
            return
        self._run_worker(self._worker_restore, meta, values, spots)

    def _worker_restore(self, meta, values, spots):
        q = self.queue
        q.put(("headline", ("Restoring spot data…", None)))
        try:
            length = int(float(meta.get("length_m", self.state_before["length_m"])))
        except (TypeError, ValueError):
            length = self.state_before["length_m"]
        self.backend._w(f":CORR:LENG {length}")
        q.put(("log", f"WRITE MOMENT: :CORR:USE:DATA:SING with {N_SPOT_VALUES} values from file."))
        errs = self.backend.write_spot_data(values)
        for n in range(1, N_SPOTS + 1):
            sp = spots.get(n)
            if sp and sp["freq"] >= 20:
                self.backend.setup_spot(n, sp["freq"], on=sp["on"])
            elif n in self.state_before["spots"] and self.state_before["spots"][n]["on"]:
                self.backend._w(f":CORR:SPOT{n}:STAT OFF")
        open_on = str(meta.get("open_on", "True")).lower() == "true"
        short_on = str(meta.get("short_on", "True")).lower() == "true"
        self.backend.set_switches(open_on, short_on)
        if errs:
            q.put(("log", f"Meter errors during restore: {errs}"))
        st = self.backend.read_state(all_spots=False)
        self.state_before = st
        q.put(("log", "Restore complete. Re-inspected; verify next if you want residuals."))
        q.put(("state", st))

    def _do_preset(self):
        if not self.connected or self.busy:
            self.log("Connect first (and wait for any running step).")
            return
        if not messagebox.askyesno(
                "Full preset",
                "Send :SYST:PRES (CLEAR SET&CORR)?\n\nThis erases ALL correction data in the "
                "meter, all-points tables included, and resets its settings (manual p. 87). "
                "The backup file holds spot data only.\n\nAre you sure?"):
            return
        if not messagebox.askyesno("Full preset", "Second confirmation: erase all correction data now?"):
            return
        self._run_worker(self._worker_preset)

    def _worker_preset(self):
        self.queue.put(("log", "WRITE MOMENT: :SYST:PRES — erasing all correction data."))
        self.backend.system_preset()
        st = self.backend.read_state(all_spots=False)
        self.state_before = st
        self.queue.put(("log", "Preset done. Meter re-inspected. Redo the correction now."))
        self.queue.put(("state", st))
        self.queue.put(("step", 0))

    # ------------------------------------------------------------ misc
    def _restart(self):
        self.residual_rows = []
        self.spot_map = {}
        self._update_plot()
        self._show_step(0)
        self.btn_next1.config(state='disabled')
        self.btn_restore.config(state='disabled')
        self._set_headline("Press Connect & inspect for a fresh file set.")

    def _open_folder(self):
        if not self.dest_folder:
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.dest_folder)   # noqa
            else:
                import subprocess
                subprocess.Popen(["xdg-open", self.dest_folder])
        except Exception as e:
            self.log(f"Cannot open folder: {e}")

    def _on_closing(self):
        self._stop_live()
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
        if self.busy:
            self.log("A step is still running on the meter; closing anyway after it "
                     "finishes would be safer. Close again to force.")
            self.busy = False   # second click closes
            return
        try:
            self.backend.close()
        finally:
            self.root.destroy()


# ===============================================================================
def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Dependency Error",
                             "PyVISA is not installed.\n\nPlease run:\npip install pyvisa")
        return
    root = tk.Tk()
    CorrectionGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
