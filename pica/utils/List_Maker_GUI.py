'''
===============================================================================
 PROGRAM:      PICA List Maker
 PURPOSE:      Build a comma-separated list of numbers for pasting into any
               PICA scan program (temperature setpoints, frequencies, fields,
               bias steps ...), see it plotted, and check a list you already
               have.

 HOW IT WORKS (two stages):
   1. Base list  -- from START to STOP, spaced linearly, logarithmically or
                    evenly in 1/x (so the output is temperatures that sit
                    evenly on an Arrhenius axis). Defined either by a point
                    count or by a step size. Optional whole-numbers mode
                    rounds every value to the nearest integer and drops the
                    repeats that rounding creates.
   2. Pattern    -- applied on top of the base list:
                    none, loop (up then back down), sawtooth (repeat the
                    ramp), square (alternate start/stop), sine (smooth up and
                    back), hysteresis with 4 quadrants (+max -> -max -> +max)
                    or 5 quadrants (virgin curve 0 -> +max first), each with
                    a cycle count. Turning points are repeated by default so
                    a cool-then-warm list reads "..., 20, 20, ...", which is
                    what a scan program that holds at each setpoint expects.

 Rounding happens in stage 1, before the pattern, so the deliberate repeat
 at a turning point is never mistaken for a rounding duplicate.

 The "Check a list" tab reads a pasted list back and reports its count,
 range, spacing type and step.

 No instrument is touched. Nothing is written except the .txt the user asks
 for. Only tkinter, matplotlib and numpy are used; all three are already
 PICA dependencies.
===============================================================================
'''
import math
import os
import re
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

PROGRAM_VERSION = "1.0"

# A list longer than this is refused rather than built, so a mistyped step
# size (0.0001 instead of 0.1) cannot freeze the window mid-keystroke.
MAX_POINTS = 50000

SPACINGS = ("Linear", "Log", "Log, dense near 0", "1/x")

PATTERNS = (
    "None",
    "Loop: up then back down",
    "Sawtooth: ramp up, jump back, ramp again",
    "Square: hold at start, hold at stop",
    "Sine: smooth up and back",
    "Hysteresis, 4 quadrants (+max to -max to +max)",
    "Hysteresis, 5 quadrants (virgin curve first)",
)
PATTERN_KEYS = {
    PATTERNS[0]: "none",
    PATTERNS[1]: "loop",
    PATTERNS[2]: "sawtooth",
    PATTERNS[3]: "square",
    PATTERNS[4]: "sine",
    PATTERNS[5]: "hyst4",
    PATTERNS[6]: "hyst5",
}

SEPARATORS = {
    "comma": ",",
    "comma and space": ", ",
    "newline": "\n",
    "semicolon": ";",
    "space": " ",
    "tab": "\t",
}


# =============================================================================
#  Pure list logic (no Tk). Everything below here is what tests/ exercises.
# =============================================================================
class ListError(ValueError):
    """A list could not be built; the message is shown to the user as-is."""


def _check_count(n):
    if n > MAX_POINTS:
        raise ListError(f"That would be {n:,} points; the limit is {MAX_POINTS:,}. "
                        "Use a bigger step or fewer points.")


def base_list(start, stop, spacing="linear", n=None, step=None, near_zero=0.01):
    """Build the stage-1 list from START to STOP.

    spacing : 'linear' | 'log' | 'symlog' | 'recip'
    n       : number of points (inclusive of both ends), or
    step    : linear  -> step size in the list's own units
              log, symlog -> points per decade
              recip   -> step in 1/x units
    near_zero : symlog only -- the smallest non-zero magnitude. Points sit
              logarithmically from there out to each end, with an exact 0
              in between, so a range such as -5 to 5 is dense around zero.
    Exactly one of n / step must be given.
    """
    if (n is None) == (step is None):
        raise ListError("Give either a number of points or a step, not both.")
    if spacing not in ("linear", "log", "symlog", "recip"):
        raise ListError(f"Unknown spacing '{spacing}'.")
    start = float(start)
    stop = float(stop)
    if not (math.isfinite(start) and math.isfinite(stop)):
        raise ListError("Start and stop must be finite numbers.")

    if n is not None:
        n = int(n)
        if n < 1:
            raise ListError("Number of points must be at least 1.")
        _check_count(n)
        if n == 1 or start == stop:
            return [start] * n
    else:
        step = float(step)
        if not math.isfinite(step) or step <= 0:
            raise ListError("Step must be a positive number.")

    if spacing == "linear":
        if n is not None:
            return [float(v) for v in np.linspace(start, stop, n)]
        span = stop - start
        count = int(math.floor(abs(span) / step + 1e-9)) + 1
        _check_count(count)
        sign = 1.0 if span >= 0 else -1.0
        return [start + sign * step * i for i in range(count)]

    if spacing == "symlog":
        return _symlog_list(start, stop, n, step, near_zero)

    if spacing == "log":
        if start == 0 or stop == 0 or (start > 0) != (stop > 0):
            raise ListError("Log spacing needs start and stop on the same side of zero, "
                            "and neither can be zero.")
        if n is not None:
            return [float(v) for v in np.geomspace(start, stop, n)]
        decades = abs(math.log10(abs(stop)) - math.log10(abs(start)))
        count = int(math.floor(decades * step + 1e-9)) + 1
        _check_count(count)
        ratio = 10.0 ** (1.0 / step)
        if abs(stop) < abs(start):
            ratio = 1.0 / ratio
        return [start * ratio ** i for i in range(count)]

    # recip: even steps in 1/x, output in x
    if start == 0 or stop == 0 or (start > 0) != (stop > 0):
        raise ListError("1/x spacing needs start and stop on the same side of zero, "
                        "and neither can be zero.")
    r0, r1 = 1.0 / start, 1.0 / stop
    if n is not None:
        return [1.0 / float(r) for r in np.linspace(r0, r1, n)]
    span = r1 - r0
    count = int(math.floor(abs(span) / step + 1e-9)) + 1
    _check_count(count)
    sign = 1.0 if span >= 0 else -1.0
    return [1.0 / (r0 + sign * step * i) for i in range(count)]


def _symlog_list(start, stop, n, step, near_zero):
    """Log spacing that is allowed to touch or cross zero.

    One-sided range not touching zero -> ordinary log list.
    Range touching zero (0 to 5)      -> 0, then log from near_zero to 5.
    Range crossing zero (-5 to 5)     -> log in from -5 to -near_zero, 0,
                                         log out from near_zero to 5.
    With n points the sides share them; with a step (points per decade)
    each side gets that density.
    """
    try:
        eps = float(near_zero)
    except (TypeError, ValueError):
        raise ListError("'Closest to zero' must be a number.")
    if not math.isfinite(eps) or eps <= 0:
        raise ListError("'Closest to zero' must be a positive number.")
    for v in (start, stop):
        if v != 0 and abs(v) <= eps:
            raise ListError(f"'Closest to zero' ({eps:g}) must be smaller than both ends.")

    if start != 0 and stop != 0 and (start > 0) == (stop > 0):
        return base_list(start, stop, "log", n=n, step=step)

    def side(a, b, count):
        """Log list from a to b (same sign, nonzero), by count or by step."""
        if count is not None:
            return base_list(a, b, "log", n=count) if count > 0 else []
        return base_list(a, b, "log", step=step)

    if start == 0 or stop == 0:
        other = stop if start == 0 else start
        e = math.copysign(eps, other)
        if n is not None:
            if n < 2:
                return [0.0] * n
            ramp = side(e, other, n - 1)
        else:
            ramp = side(e, other, None)
        out = [0.0] + ramp if start == 0 else ramp[::-1] + [0.0]
        _check_count(len(out))
        return out

    # crossing zero
    e_start = math.copysign(eps, start)
    e_stop = math.copysign(eps, stop)
    if n is not None:
        if n < 3:
            raise ListError("A list crossing zero this way needs at least 3 points.")
        n_in = (n - 1) // 2
        n_out = n - 1 - n_in
        if abs(start) > abs(stop):
            n_in, n_out = n_out, n_in       # the bigger side gets the spare point
        inward = side(start, e_start, n_in)
        outward = side(e_stop, stop, n_out)
    else:
        inward = side(start, e_start, None)
        outward = side(e_stop, stop, None)
    out = inward + [0.0] + outward
    _check_count(len(out))
    return out


def round_half_away(v):
    """Round half away from zero (2.5 -> 3, -2.5 -> -3). Python's round()
    is banker's rounding (2.5 -> 2), which surprises people reading a list."""
    return math.copysign(math.floor(abs(v) + 0.5), v)


def round_integers(values):
    """Round to nearest whole number and drop the repeats rounding creates.

    Only *consecutive* repeats are dropped: a value that comes back later
    (as in a loop) is kept.
    """
    out = []
    for v in values:
        r = float(round_half_away(v))
        if r == 0.0:
            r = 0.0          # normalise -0.0
        if not out or out[-1] != r:
            out.append(r)
    return out


def _join(segments, repeat_turning_points):
    """Concatenate segments; optionally drop a leading value that repeats the
    previous segment's last value (the turning point)."""
    out = []
    for seg in segments:
        seg = list(seg)
        if not seg:
            continue
        if not repeat_turning_points and out and seg[0] == out[-1]:
            seg = seg[1:]
        out.extend(seg)
    return out


def _neg(values):
    return [0.0 if v == 0 else -v for v in values]


def apply_pattern(base, pattern="none", cycles=1, repeat_turning_points=True, hold=None):
    """Stage 2: lay a pattern over the base list.

    pattern : 'none' | 'loop' | 'sawtooth' | 'square' | 'sine' | 'hyst4' | 'hyst5'
    cycles  : how many times the pattern runs (ignored for 'none')
    hold    : 'square' only -- points held at each level. None (default)
              uses the base list's own count: half at start, half at stop,
              so one square cycle has as many points as one ramp.

    Every pattern spends one base-list's worth of points per cycle (loop and
    hysteresis per half-swing), so changing the point count in stage 1
    changes the resolution, and the cycle count changes the length.
    """
    base = [float(v) for v in base]
    if not base:
        return []
    if pattern == "none":
        return base
    cycles = int(cycles)
    if cycles < 1:
        raise ListError("Cycles must be at least 1.")
    up = base
    down = base[::-1]
    start, stop = base[0], base[-1]

    if pattern == "loop":
        segs = []
        for _ in range(cycles):
            segs += [up, down]
        out = _join(segs, repeat_turning_points)
    elif pattern == "sawtooth":
        out = []
        for _ in range(cycles):
            out.extend(up)
    elif pattern == "square":
        if hold is None:
            n_low = max(1, len(base) // 2)
            n_high = max(1, len(base) - n_low)
        else:
            hold = int(hold)
            if hold < 1:
                raise ListError("Square hold must be at least 1 point.")
            n_low = n_high = hold
        out = []
        for _ in range(cycles):
            out.extend([start] * n_low + [stop] * n_high)
    elif pattern == "sine":
        n = len(base)
        if n < 2:
            raise ListError("Sine needs at least 2 points in the base list.")
        # One half-swing (start -> stop) spends n points, like the ramp, so
        # start, the peak and the return are all hit exactly.
        mid = 0.5 * (start + stop)
        amp = 0.5 * (stop - start)
        half = n - 1
        total = cycles * 2 * half
        out = [mid - amp * math.cos(math.pi * k / half) for k in range(total + 1)]
        out = [0.0 if abs(v) < 1e-12 * max(1.0, abs(amp)) else v for v in out]
    elif pattern in ("hyst4", "hyst5"):
        if (start < 0 < stop) or (stop < 0 < start):
            # The range already spans zero (e.g. -5 to 5): the base list IS the
            # full swing between the two extremes, with its own spacing.
            #   closed loop  = +max -> -max -> +max
            #   virgin curve = 0 -> +max, taken from the list's positive side
            if stop > start:
                to_neg, to_pos = down, up        # down: stop(+) -> start(-)
            else:
                to_neg, to_pos = up, down        # up:   start(+) -> stop(-)
            virgin = [0.0] + [v for v in to_pos if v > 0]
            loop = [to_neg, to_pos]
        else:
            # The range stays on one side of zero (e.g. 0 to 5, or 0.1 to 1000
            # log): treat it as the quarter ramp and mirror it about zero.
            #   Q1  start -> stop        Q2  stop -> start
            #   Q3  -start -> -stop      Q4  -stop -> -start
            # Q2+Q3 and Q4+Q1 are each stitched into ONE full swing first, so
            # the seam at zero is never treated as a turning point (no
            # "0, 0" repeat there; a 0 shared by both quarters appears once).
            virgin = up
            to_neg = _join([down, _neg(up)], False)
            to_pos = _join([_neg(down), up], False)
            loop = [to_neg, to_pos]
        segs = []
        if pattern == "hyst5":
            segs.append(virgin)                  # virgin curve, once
        for _ in range(cycles):
            segs += loop
        out = _join(segs, repeat_turning_points)
    else:
        raise ListError(f"Unknown pattern '{pattern}'.")

    _check_count(len(out))
    return out


def format_values(values, decimals=2, scientific=False, separator=",", integers=False):
    """Render the list as text."""
    decimals = max(0, int(decimals))
    parts = []
    for v in values:
        if integers:
            parts.append(str(int(round_half_away(v))))
        elif scientific:
            parts.append(f"{v:.{decimals}e}")
        else:
            s = f"{v:.{decimals}f}"
            if s.startswith("-") and float(s) == 0.0:
                s = s[1:]            # no "-0.00"
            parts.append(s)
    return separator.join(parts)


_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_list(text):
    """Pull every number out of a pasted list, whatever the separator."""
    return [float(m) for m in _NUM_RE.findall(text or "")]


def analyze_list(values, rel_tol=0.02):
    """Describe a list: count, range, monotonic direction, spacing guess, step.

    The spacing guess tolerates rounded values (rel_tol on the step scatter).
    Returns a dict; 'spacing' is one of 'linear', 'log', '1/x', 'constant',
    'irregular', or 'n/a' for fewer than 3 points.
    """
    vals = [float(v) for v in values]
    info = {
        "count": len(vals),
        "min": min(vals) if vals else None,
        "max": max(vals) if vals else None,
        "spacing": "n/a",
        "step": None,
        "direction": "n/a",
        "turning_points": 0,
        "repeats": 0,
        "integers": bool(vals) and all(float(v).is_integer() for v in vals),
    }
    if len(vals) < 2:
        return info
    diffs = [b - a for a, b in zip(vals, vals[1:])]
    info["repeats"] = sum(1 for d in diffs if d == 0)
    signs = [1 if d > 0 else -1 for d in diffs if d != 0]
    info["turning_points"] = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    if not signs:
        info["direction"] = "constant"
        info["spacing"] = "constant"
        info["step"] = 0.0
        return info
    first = "rising" if signs[0] > 0 else "falling"
    if info["turning_points"]:
        info["direction"] = f"{first} first, {info['turning_points']} turn(s)"
    else:
        info["direction"] = first
    if len(vals) < 3 or info["turning_points"]:
        # Only classify the first monotonic run
        run_end = len(vals)
        for i, (a, b) in enumerate(zip(signs, signs[1:])):
            if a != b:
                run_end = i + 2
                break
        vals = vals[:run_end]
        diffs = diffs[:run_end - 1]
        if len(vals) < 3:
            info["spacing"] = "n/a"
            info["step"] = diffs[0] if diffs else None
            return info

    def _steady(seq):
        m = float(np.mean(seq))
        if m == 0:
            return False, m
        return (max(abs(s - m) for s in seq) <= rel_tol * abs(m)), m

    ok, m = _steady(diffs)
    if ok:
        info["spacing"], info["step"] = "linear", m
        return info
    if all(v != 0 for v in vals) and all((v > 0) == (vals[0] > 0) for v in vals):
        ratios = [b / a for a, b in zip(vals, vals[1:])]
        ok, m = _steady([math.log10(r) for r in ratios])
        if ok and m != 0:
            info["spacing"], info["step"] = "log", 1.0 / m   # points per decade
            return info
        rdiffs = [1.0 / b - 1.0 / a for a, b in zip(vals, vals[1:])]
        ok, m = _steady(rdiffs)
        if ok:
            info["spacing"], info["step"] = "1/x", m
            return info
    info["spacing"] = "irregular"
    info["step"] = float(np.mean(diffs))
    return info


# =============================================================================
#  GUI
# =============================================================================
class PICAListMakerApp:

    # --- PICA Theme Constants ---
    CLR_BG_DARK = '#B8A392'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ERROR = '#8B2E22'

    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SMALL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')
    FONT_HEADLINE = ('Segoe UI', FONT_SIZE_BASE + 8, 'bold')
    FONT_MONO = ('Consolas', 11)

    DEBOUNCE_MS = 250

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA List Maker v{PROGRAM_VERSION}")
        self.root.geometry("1180x760")
        self.root.minsize(980, 640)
        self.root.configure(bg=self.CLR_BG_DARK)

        # --- Stage 1: base list ---
        self.start_var = tk.StringVar(value="10")
        self.stop_var = tk.StringVar(value="300")
        self.spacing_var = tk.StringVar(value=SPACINGS[0])
        self.define_by_var = tk.StringVar(value="points")   # 'points' | 'step'
        self.points_var = tk.StringVar(value="30")
        self.step_var = tk.StringVar(value="10")
        self.integers_var = tk.BooleanVar(value=False)
        self.near_zero_var = tk.StringVar(value="0.01")

        # --- Stage 2: pattern ---
        self.pattern_var = tk.StringVar(value=PATTERNS[0])
        self.cycles_var = tk.StringVar(value="2")
        self.repeat_turn_var = tk.BooleanVar(value=True)

        # --- Output format ---
        self.decimals_var = tk.StringVar(value="2")
        self.sci_var = tk.BooleanVar(value=False)
        self.sep_var = tk.StringVar(value="comma")

        # --- Plot ---
        self.logy_var = tk.BooleanVar(value=False)

        # --- Check tab ---
        self.check_result_var = tk.StringVar(value="Paste a list on the left and press Check.")

        self.values = []
        self._after_id = None
        self._plot_source = "make"     # 'make' | 'check'
        self._check_values = []

        self.setup_styles()
        self.create_widgets()

        for var in (self.start_var, self.stop_var, self.spacing_var, self.define_by_var,
                    self.points_var, self.step_var, self.integers_var, self.near_zero_var, self.pattern_var,
                    self.cycles_var, self.repeat_turn_var, self.decimals_var,
                    self.sci_var, self.sep_var, self.logy_var):
            var.trace_add("write", self._schedule_recompute)

        self.root.after(50, self.recompute)

    # ------------------------------------------------------------------
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('Sub.TFrame', background=self.CLR_FRAME_BG)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('Sub.TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('Small.TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_SMALL)
        style.configure('Title.TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT_DARK, font=self.FONT_TITLE)
        style.configure('Headline.TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT_DARK, font=self.FONT_HEADLINE)
        style.configure('Error.TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_ERROR, font=self.FONT_SUBTITLE)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_BG_DARK, borderwidth=2, padding=10)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_FRAME_BG)])
        style.configure('TRadiobutton', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.map('TRadiobutton', background=[('active', self.CLR_FRAME_BG)])
        style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('TCombobox', fieldbackground=self.CLR_INPUT_BG, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('TNotebook', background=self.CLR_BG_DARK, borderwidth=0)
        style.configure('TNotebook.Tab', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE, padding=(12, 6))
        style.map('TNotebook.Tab', background=[('selected', self.CLR_FRAME_BG)])
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 5), foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_FRAME_BG, borderwidth=0)
        style.map('App.TButton', background=[('active', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK)])

    # ------------------------------------------------------------------
    def create_widgets(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill='both', expand=True)

        ttk.Label(outer, text="PICA List Maker", style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text="Build a list of numbers, see it plotted, copy it into any scan program.").pack(anchor='w', pady=(0, 8))

        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ---------------- Left: notebook (Make / Check) ----------------
        self.notebook = ttk.Notebook(body, width=520)
        self.notebook.grid(row=0, column=0, sticky='ns', padx=(0, 10))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        make_tab = ttk.Frame(self.notebook, style='Sub.TFrame', padding=8)
        check_tab = ttk.Frame(self.notebook, style='Sub.TFrame', padding=8)
        self.notebook.add(make_tab, text="Make a list")
        self.notebook.add(check_tab, text="Check a list")

        self._build_make_tab(make_tab)
        self._build_check_tab(check_tab)

        # ---------------- Right: plot + output ----------------
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky='nsew')
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        right.columnconfigure(0, weight=1)

        plot_frame = ttk.LabelFrame(right, text="Preview")
        plot_frame.grid(row=0, column=0, sticky='nsew', pady=(0, 8))
        plot_frame.rowconfigure(1, weight=1)
        plot_frame.columnconfigure(0, weight=1)

        plot_bar = ttk.Frame(plot_frame, style='Sub.TFrame')
        plot_bar.grid(row=0, column=0, sticky='ew')
        ttk.Checkbutton(plot_bar, text="Log y axis", variable=self.logy_var).pack(side='left')
        self.plot_note_var = tk.StringVar(value="")
        ttk.Label(plot_bar, textvariable=self.plot_note_var, style='Small.TLabel').pack(side='left', padx=12)

        self.figure = Figure(figsize=(6, 3.4), dpi=100, facecolor=self.CLR_FRAME_BG)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor(self.CLR_INPUT_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky='nsew')

        out_frame = ttk.LabelFrame(right, text="Your list")
        out_frame.grid(row=1, column=0, sticky='nsew')
        out_frame.rowconfigure(2, weight=1)
        out_frame.columnconfigure(0, weight=1)

        self.headline_var = tk.StringVar(value="")
        self.headline_label = ttk.Label(out_frame, textvariable=self.headline_var, style='Headline.TLabel')
        self.headline_label.grid(row=0, column=0, sticky='w')

        fmt = ttk.Frame(out_frame, style='Sub.TFrame')
        fmt.grid(row=1, column=0, sticky='ew', pady=(2, 6))
        ttk.Label(fmt, text="Show", style='Sub.TLabel').pack(side='left')
        ttk.Spinbox(fmt, from_=0, to=12, width=3, textvariable=self.decimals_var, font=self.FONT_BASE).pack(side='left', padx=4)
        ttk.Label(fmt, text="decimals,", style='Sub.TLabel').pack(side='left')
        ttk.Checkbutton(fmt, text="scientific,", variable=self.sci_var).pack(side='left', padx=(6, 0))
        ttk.Label(fmt, text="separated by", style='Sub.TLabel').pack(side='left', padx=(6, 4))
        ttk.Combobox(fmt, textvariable=self.sep_var, values=list(SEPARATORS), state='readonly', width=15,
                     font=self.FONT_BASE).pack(side='left')
        ttk.Button(fmt, text="Save .txt", style='App.TButton', command=self.save_txt).pack(side='right')
        ttk.Button(fmt, text="Copy", style='App.TButton', command=self.copy_to_clipboard).pack(side='right', padx=(0, 6))

        self.output = scrolledtext.ScrolledText(out_frame, wrap='word', height=6, font=self.FONT_MONO,
                                                bg=self.CLR_INPUT_BG, fg=self.CLR_TEXT, relief='flat')
        self.output.grid(row=2, column=0, sticky='nsew')

    # ------------------------------------------------------------------
    def _build_make_tab(self, tab):
        base = ttk.LabelFrame(tab, text="1. The list")
        base.pack(fill='x', pady=(0, 8))

        row = ttk.Frame(base, style='Sub.TFrame'); row.pack(fill='x', pady=2)
        ttk.Label(row, text="From", style='Sub.TLabel').pack(side='left')
        ttk.Entry(row, textvariable=self.start_var, width=10, font=self.FONT_BASE).pack(side='left', padx=4)
        ttk.Label(row, text="to", style='Sub.TLabel').pack(side='left')
        ttk.Entry(row, textvariable=self.stop_var, width=10, font=self.FONT_BASE).pack(side='left', padx=4)
        ttk.Label(row, text="spaced", style='Sub.TLabel').pack(side='left')
        ttk.Combobox(row, textvariable=self.spacing_var, values=list(SPACINGS), state='readonly', width=7,
                     font=self.FONT_BASE).pack(side='left', padx=4)

        row = ttk.Frame(base, style='Sub.TFrame'); row.pack(fill='x', pady=2)
        ttk.Radiobutton(row, text="using", variable=self.define_by_var, value="points").pack(side='left')
        ttk.Entry(row, textvariable=self.points_var, width=7, font=self.FONT_BASE).pack(side='left', padx=4)
        ttk.Label(row, text="points", style='Sub.TLabel').pack(side='left')

        row = ttk.Frame(base, style='Sub.TFrame'); row.pack(fill='x', pady=2)
        ttk.Radiobutton(row, text="or in", variable=self.define_by_var, value="step").pack(side='left')
        ttk.Entry(row, textvariable=self.step_var, width=7, font=self.FONT_BASE).pack(side='left', padx=4)
        self.step_unit_var = tk.StringVar(value="steps of this size")
        ttk.Label(row, textvariable=self.step_unit_var, style='Sub.TLabel').pack(side='left')

        row = ttk.Frame(base, style='Sub.TFrame'); row.pack(fill='x', pady=2)
        ttk.Label(row, text="closest to zero", style='Sub.TLabel').pack(side='left')
        self.near_zero_entry = ttk.Entry(row, textvariable=self.near_zero_var, width=7, font=self.FONT_BASE)
        self.near_zero_entry.pack(side='left', padx=4)
        ttk.Label(row, text="(only for 'Log, dense near 0')", style='Small.TLabel').pack(side='left')
        self.spacing_var.trace_add("write", self._update_spacing_controls)
        self._update_spacing_controls()

        ttk.Checkbutton(base, text="Whole numbers only (round to nearest, drop repeats)",
                        variable=self.integers_var).pack(anchor='w', pady=(4, 0))
        ttk.Label(base, text="Log, dense near 0: log steps out from 'closest to zero' on each side, with an exact 0 "
                             "in the middle, so -5 to 5 is fine for a hysteresis loop. "
                             "1/x: points sit evenly in 1/x, so a temperature list lands evenly on an Arrhenius axis.",
                  style='Small.TLabel', wraplength=470).pack(anchor='w', pady=(4, 0))

        pat = ttk.LabelFrame(tab, text="2. Then apply a pattern")
        pat.pack(fill='x', pady=(0, 8))

        row = ttk.Frame(pat, style='Sub.TFrame'); row.pack(fill='x', pady=2)
        ttk.Combobox(row, textvariable=self.pattern_var, values=list(PATTERNS), state='readonly', width=42,
                     font=self.FONT_BASE).pack(side='left')

        row = ttk.Frame(pat, style='Sub.TFrame'); row.pack(fill='x', pady=2)
        ttk.Label(row, text="for", style='Sub.TLabel').pack(side='left')
        ttk.Entry(row, textvariable=self.cycles_var, width=5, font=self.FONT_BASE).pack(side='left', padx=4)
        ttk.Label(row, text="cycle(s). Each cycle uses the point count from step 1.", style='Sub.TLabel').pack(side='left')

        ttk.Checkbutton(pat, text="Repeat the value at each turning point (e.g. ... 20, 20 ...)",
                        variable=self.repeat_turn_var).pack(anchor='w', pady=(4, 0))
        ttk.Label(pat, text="Hysteresis: 4 quadrants runs +max to -max and back; 5 quadrants adds the virgin "
                           "curve from 0 to +max first. A range spanning zero (-5 to 5) is the full swing; "
                           "a one-sided range (0 to 5) is mirrored about zero.",
                  style='Small.TLabel', wraplength=470).pack(anchor='w', pady=(4, 0))

        self.error_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.error_var, style='Error.TLabel', wraplength=480).pack(anchor='w', pady=(4, 0))

    def _build_check_tab(self, tab):
        ttk.Label(tab, text="Paste any list (commas, spaces, newlines all fine):", style='Sub.TLabel').pack(anchor='w')
        self.check_input = scrolledtext.ScrolledText(tab, wrap='word', height=12, font=self.FONT_MONO,
                                                     bg=self.CLR_INPUT_BG, fg=self.CLR_TEXT, relief='flat')
        self.check_input.pack(fill='both', expand=True, pady=(4, 6))
        row = ttk.Frame(tab, style='Sub.TFrame'); row.pack(fill='x')
        ttk.Button(row, text="Check", style='App.TButton', command=self.check_list).pack(side='left')
        ttk.Button(row, text="Use it as the list", style='App.TButton', command=self.adopt_checked).pack(side='left', padx=6)
        ttk.Label(tab, textvariable=self.check_result_var, style='Sub.TLabel', wraplength=480,
                  justify='left').pack(anchor='w', pady=(8, 0))

    # ------------------------------------------------------------------
    #  Recompute pipeline
    # ------------------------------------------------------------------
    def _schedule_recompute(self, *_):
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = self.root.after(self.DEBOUNCE_MS, self.recompute)

    def _update_spacing_controls(self, *_):
        """The 'closest to zero' box only means something for the dense-near-zero spacing."""
        entry = getattr(self, "near_zero_entry", None)
        if entry is None:
            return
        state = 'normal' if self.spacing_var.get() == "Log, dense near 0" else 'disabled'
        entry.configure(state=state)

    def _spacing_key(self):
        return {"Linear": "linear", "Log": "log", "Log, dense near 0": "symlog",
                "1/x": "recip"}[self.spacing_var.get()]

    def _read_float(self, var, name):
        txt = var.get().strip()
        try:
            return float(txt)
        except ValueError:
            raise ListError(f"'{txt}' is not a number for {name}.")

    def _read_int(self, var, name):
        txt = var.get().strip()
        try:
            return int(float(txt))
        except ValueError:
            raise ListError(f"'{txt}' is not a whole number for {name}.")

    def build_values(self):
        """Run both stages from the current controls. Raises ListError."""
        spacing = self._spacing_key()
        self.step_unit_var.set({"linear": "steps of this size",
                                "log": "points per decade",
                                "symlog": "points per decade (each side)",
                                "recip": "steps of this size in 1/x"}[spacing])
        start = self._read_float(self.start_var, "start")
        stop = self._read_float(self.stop_var, "stop")
        near_zero = self._read_float(self.near_zero_var, "closest to zero") if spacing == "symlog" else 0.01
        if self.define_by_var.get() == "points":
            vals = base_list(start, stop, spacing, n=self._read_int(self.points_var, "points"), near_zero=near_zero)
        else:
            vals = base_list(start, stop, spacing, step=self._read_float(self.step_var, "step"), near_zero=near_zero)
        if self.integers_var.get():
            vals = round_integers(vals)
        key = PATTERN_KEYS[self.pattern_var.get()]
        vals = apply_pattern(vals, key,
                             cycles=self._read_int(self.cycles_var, "cycles") if key != "none" else 1,
                             repeat_turning_points=self.repeat_turn_var.get())
        return vals

    def recompute(self):
        self._after_id = None
        try:
            self.values = self.build_values()
            self.error_var.set("")
        except ListError as e:
            self.error_var.set(str(e))
            self.values = []
        except Exception as e:  # never let a typo kill the window
            self.error_var.set(f"Could not build the list: {e}")
            self.values = []
        self._refresh_output()
        if self._plot_source == "make":
            self._plot(self.values, "Your list")

    def _refresh_output(self):
        vals = self.values
        if vals:
            self.headline_var.set(f"{len(vals)} points   ·   {self._fmt_one(min(vals))} to {self._fmt_one(max(vals))}")
        else:
            self.headline_var.set("No list yet")
        try:
            decimals = int(float(self.decimals_var.get()))
        except ValueError:
            decimals = 2
        text = format_values(vals, decimals=decimals, scientific=self.sci_var.get(),
                             separator=SEPARATORS.get(self.sep_var.get(), ","),
                             integers=self.integers_var.get() and not self.sci_var.get())
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)

    def _fmt_one(self, v):
        if float(v).is_integer():
            return str(int(v))
        return f"{v:.6g}"

    # ------------------------------------------------------------------
    #  Plot
    # ------------------------------------------------------------------
    def _plot(self, vals, title):
        self.ax.clear()
        self.ax.set_facecolor(self.CLR_INPUT_BG)
        self.ax.grid(True, alpha=0.3)
        self.ax.set_xlabel("index")
        self.ax.set_ylabel("value")
        self.ax.set_title(title, fontsize=10)
        note = ""
        if vals:
            idx = np.arange(len(vals))
            style = 'o-' if len(vals) <= 400 else '-'
            self.ax.plot(idx, vals, style, color=self.CLR_ACCENT_GOLD, markersize=4, linewidth=1.2)
            if self.logy_var.get():
                if all(v > 0 for v in vals):
                    self.ax.set_yscale('log')
                else:
                    note = "Log y needs all values > 0; showing linear."
        self.plot_note_var.set(note)
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        self.canvas.draw_idle()

    def _on_tab_changed(self, _event=None):
        try:
            tab = self.notebook.tab(self.notebook.select(), "text")
        except Exception:
            return
        if tab.startswith("Check"):
            self._plot_source = "check"
            self._plot(self._check_values, "Pasted list")
        else:
            self._plot_source = "make"
            self._plot(self.values, "Your list")

    # ------------------------------------------------------------------
    #  Output actions
    # ------------------------------------------------------------------
    def copy_to_clipboard(self):
        text = self.output.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        n = len(self.values)
        self.headline_var.set(f"Copied {n} points")
        self.root.after(1500, self._refresh_output)

    def save_txt(self):
        text = self.output.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("List Maker", "There is no list to save yet.")
            return
        path = filedialog.asksaveasfilename(title="Save list as", defaultextension=".txt",
                                            initialfile="list.txt",
                                            filetypes=[("Text", "*.txt"), ("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="ascii", newline="") as fh:
                fh.write(text)
                if not text.endswith("\n"):
                    fh.write("\n")
        except OSError as e:
            messagebox.showerror("List Maker", f"Could not save:\n{e}")

    # ------------------------------------------------------------------
    #  Check tab
    # ------------------------------------------------------------------
    def check_list(self):
        vals = parse_list(self.check_input.get("1.0", "end-1c"))
        self._check_values = vals
        if not vals:
            self.check_result_var.set("No numbers found.")
            self._plot([], "Pasted list")
            return
        info = analyze_list(vals)
        lines = [f"{info['count']} points, from {self._fmt_one(info['min'])} to {self._fmt_one(info['max'])}."]
        sp = info["spacing"]
        if sp == "linear":
            lines.append(f"Linear spacing, step {self._fmt_one(info['step'])}.")
        elif sp == "log":
            lines.append(f"Log spacing, about {info['step']:.3g} points per decade.")
        elif sp == "1/x":
            lines.append(f"Even in 1/x, step {abs(info['step']):.4g} per unit of 1/x.")
        elif sp == "constant":
            lines.append("Every value is the same.")
        elif sp == "irregular":
            lines.append(f"Irregular spacing; mean step {self._fmt_one(info['step'])}.")
        if info["turning_points"]:
            lines.append(f"Direction: {info['direction']}. Spacing judged on the first run only.")
        else:
            lines.append(f"Direction: {info['direction']}.")
        if info["repeats"]:
            lines.append(f"{info['repeats']} repeated neighbour(s).")
        lines.append("All whole numbers." if info["integers"] else "Contains non-integers.")
        self.check_result_var.set("\n".join(lines))
        self._plot_source = "check"
        self._plot(vals, "Pasted list")

    def adopt_checked(self):
        """Put the pasted list into the output box as-is (for reformatting, copy, save)."""
        vals = parse_list(self.check_input.get("1.0", "end-1c"))
        if not vals:
            self.check_result_var.set("No numbers found.")
            return
        self.values = vals
        self.error_var.set("Showing the pasted list. Change any control above to go back to building one.")
        self._refresh_output()
        self.notebook.select(0)


if __name__ == '__main__':
    root = tk.Tk()
    app = PICAListMakerApp(root)
    root.mainloop()
