"""
Module: Memristor_Analysis_GUI.py
Purpose: Read-only analysis of the files written by the three memristor
         measurement modules (Memristor_Sweep_K2400_GUI,
         Memristor_Retention_K2400_GUI, Memristor_Endurance_K2400_GUI),
         protocol "Memristor measurement modules for PICA" v1.1, Section 6.

v1.0 (11 Oct 2026) - first version
  * Strictly read-only: the raw files are opened for reading only, never
    renamed, moved or rewritten, and the module writes NO file of its own.
    Results appear in the Results box of each tab (and on the plot).
  * Sweep tab: I-V (linear and log|I|) of sweep files, retention _write
    files or one cycle of an endurance raw file; V set / V reset by rule 1
    (first compliance point, else current jump > F above a voltage floor;
    V reset = largest drop in |I|) or rule 2 (largest |dI/dV|). A loop
    where nothing passes reports 'none', never 0 or V max.
  * Retention tab: R against log t for LRS, HRS and control _reads files;
    ratio criterion (default 10) on a common log-time grid (linear
    interpolation in log t, common window only, stated on the plot) and a
    drift criterion (default 2x relative to R at t = 10 s). No
    extrapolation (Lanza 2019).
  * Endurance tab: R LRS, R HRS and window against cycle with a typed
    threshold and k-in-a-row rule (defaults 10 and 3); histogram of
    V_set_trip_V / V_reset_trip_V; raw-cycle I-V viewer; a warning when a
    sweep-mode and a pulse-mode summary are overlaid.
  * Files are chosen with the Add... button of each tab; nothing is
    preloaded. Command line: --tab sweep|retention|endurance only.
"""

import argparse
import csv
import math
import os
import sys
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

PROGRAM_VERSION = "1.0"
PROTOCOL_VERSION = "1.1"
TABS = ("sweep", "retention", "endurance")

COLOUR_LRS = "#4C72B0"
COLOUR_HRS = "#C44E52"
COLOUR_CONTROL = "#8C8C8C"
SEGMENT_COLOURS = ("#4C72B0", "#55A868", "#8172B2", "#937860")
MARK_SET = "#2A9D8F"
MARK_RESET = "#E76F51"


# ===============================================================================
# File reading (read-only) and header parsing
# ===============================================================================

def ascii_text(text):
    s = str(text)
    for src, dst in (("\u2192", "->"), ("\u00b5", "u"), ("\u03bc", "u"), ("\u03a9", "Ohm"),
                     ("\u2126", "Ohm"), ("\u2212", "-"), ("\u00b1", "+/-"), ("\u2014", "-"), ("\u2013", "-")):
        s = s.replace(src, dst)
    return s.encode("ascii", "replace").decode("ascii")


def parse_pica_csv(path):
    """Read one PICA memristor file. Returns (header_lines, columns, rows);
    rows is a list of dicts, numbers as float, empty cells as ''. The file
    is opened for reading only."""
    header, columns, rows = [], None, []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for parts in csv.reader(fh):
            if not parts:
                continue
            if parts[0].startswith("#"):
                header.append(",".join(parts))
                continue
            if columns is None:
                columns = [c.strip() for c in parts]
                continue
            row = {}
            for k, cell in zip(columns, parts):
                cell = cell.strip()
                if cell == "":
                    row[k] = ""
                else:
                    try:
                        row[k] = float(cell)
                    except ValueError:
                        row[k] = cell
            rows.append(row)
    return header, columns or [], rows


def header_blocks(header_lines):
    """'# [name] k=v | k=v' lines -> {'name': {k: v}}, plus 'module_line'
    (first line) and 'end' (from '# END reason=... | ...')."""
    out = {"module_line": "", "sample": {}, "instrument": {}, "protocol": {}, "end": {}}
    for line in header_lines:
        body = line.lstrip("#").strip()
        if not out["module_line"] and body.startswith("PICA memristor"):
            out["module_line"] = body
            continue
        name = None
        if body.startswith("[") and "]" in body:
            name, body = body[1:body.index("]")], body[body.index("]") + 1:].strip()
        elif body.startswith("END"):
            name, body = "end", body[3:].strip()
        if name is None:
            continue
        pairs = {}
        for item in body.split(" | "):
            if "=" in item:
                k, v = item.split("=", 1)
                pairs[k.strip()] = v.strip()
        out.setdefault(name, {}).update(pairs)
    return out


def file_kind(blocks, columns):
    module = blocks.get("protocol", {}).get("module", "")
    if module in ("sweep", "retention_write"):
        return "sweep"
    if module == "endurance_raw" or ("leg" in columns and "cycle" in columns and "V_set_V" in columns):
        return "endurance_raw"
    if module == "retention_reads" or "t_since_write_s" in columns:
        return "retention_reads"
    if module == "endurance" or "R_LRS_ohm" in columns:
        return "endurance_summary"
    if "segment" in columns and "V_set_V" in columns:
        return "sweep"
    return "unknown"


def num(value):
    """Cell -> float or NaN."""
    if value is None or value == "":
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


# ===============================================================================
# Sweep analysis (protocol 6.2)
# ===============================================================================

def sweep_loops(rows, cycle=None):
    """{loop: {segment: [rows]}} for phase 'sweep' rows (or phase 'pulse'
    rows of one endurance cycle). For endurance raw files give the cycle."""
    loops = {}
    for r in rows:
        if r.get("phase") not in ("sweep", "pulse"):
            continue
        if cycle is not None and int(num(r.get("cycle"))) != int(cycle):
            continue
        loop = int(num(r.get("loop"))) if r.get("loop") not in ("", None) else 1
        seg = r.get("segment")
        seg = int(num(seg)) if seg not in ("", None) else 0
        loops.setdefault(loop, {}).setdefault(seg, []).append(r)
    return loops


def segment_is_outgoing(seg_rows):
    v = [num(r.get("V_set_V")) for r in seg_rows]
    v = [x for x in v if not math.isnan(x)]
    if len(v) < 2:
        return True
    return abs(v[-1]) > abs(v[0])


def segment_sign(seg_rows):
    v = [num(r.get("V_set_V")) for r in seg_rows]
    v = [x for x in v if not math.isnan(x) and x != 0]
    if not v:
        return 0
    return 1 if sum(v) > 0 else -1


def rule1_vset(seg_rows, factor, floor_v):
    """First compliance point on the segment, else first point where
    |I_k / I_(k-1)| > factor and |V| > floor. Returns (v, how) or (None, 'none')."""
    for r in seg_rows:
        if num(r.get("compliance_flag")) == 1:
            return num(r.get("V_set_V")), "first compliance point"
    for k in range(1, len(seg_rows)):
        i_prev, i_k = num(seg_rows[k - 1].get("I_A")), num(seg_rows[k].get("I_A"))
        v_k = num(seg_rows[k].get("V_set_V"))
        if math.isnan(i_prev) or math.isnan(i_k) or i_prev == 0 or math.isnan(v_k):
            continue
        if abs(v_k) > floor_v and abs(i_k / i_prev) > factor:
            return v_k, f"current jump > {factor:g}"
    return None, "none"


def rule1_vreset(seg_rows):
    """Point of the largest drop in |I| between neighbours."""
    best, best_v = 0.0, None
    for k in range(1, len(seg_rows)):
        i_prev, i_k = abs(num(seg_rows[k - 1].get("I_A"))), abs(num(seg_rows[k].get("I_A")))
        if math.isnan(i_prev) or math.isnan(i_k):
            continue
        drop = i_prev - i_k
        if drop > best:
            best, best_v = drop, num(seg_rows[k].get("V_set_V"))
    return (best_v, "largest drop in |I|") if best_v is not None else (None, "none")


def rule2_max_slope(seg_rows):
    """Point of the largest |dI/dV| (V_meas based) on the segment."""
    best, best_v = 0.0, None
    for k in range(1, len(seg_rows)):
        v0, v1 = num(seg_rows[k - 1].get("V_meas_V")), num(seg_rows[k].get("V_meas_V"))
        i0, i1 = num(seg_rows[k - 1].get("I_A")), num(seg_rows[k].get("I_A"))
        if any(math.isnan(x) for x in (v0, v1, i0, i1)) or v1 == v0:
            continue
        slope = abs((i1 - i0) / (v1 - v0))
        if slope > best:
            best, best_v = slope, num(seg_rows[k].get("V_set_V"))
    return (best_v, "largest |dI/dV|") if best_v is not None else (None, "none")


def estimate_switching(loop_segments, rule=1, factor=10.0, floor_v=1.0, set_sign=1):
    """V set and V reset for one loop. SET is searched on the outgoing
    segment with sign set_sign, RESET on the outgoing segment of the
    opposite sign; a unipolar loop searches RESET on the SET segment after
    the SET point. Returns a dict with values or None and 'how' texts."""
    outgoing = {s: rows for s, rows in loop_segments.items() if segment_is_outgoing(rows) and segment_sign(rows) != 0}
    set_segs = [s for s, rows in outgoing.items() if segment_sign(rows) == set_sign]
    reset_segs = [s for s, rows in outgoing.items() if segment_sign(rows) == -set_sign]
    result = {"v_set": None, "v_reset": None, "set_segment": None, "reset_segment": None,
              "how_set": "none", "how_reset": "none"}
    if set_segs:
        s = min(set_segs)
        rows = loop_segments[s]
        result["set_segment"] = s
        if rule == 1:
            result["v_set"], result["how_set"] = rule1_vset(rows, factor, floor_v)
        else:
            result["v_set"], result["how_set"] = rule2_max_slope(rows)
    if reset_segs:
        s = min(reset_segs)
        rows = loop_segments[s]
        result["reset_segment"] = s
        if rule == 1:
            result["v_reset"], result["how_reset"] = rule1_vreset(rows)
        else:
            result["v_reset"], result["how_reset"] = rule2_max_slope(rows)
    elif set_segs and result["v_set"] is not None:
        s = result["set_segment"]
        rows = loop_segments[s]
        after = [r for r in rows if abs(num(r.get("V_set_V"))) > abs(result["v_set"])]
        result["reset_segment"] = s
        if len(after) >= 2:
            if rule == 1:
                result["v_reset"], result["how_reset"] = rule1_vreset(after)
            else:
                result["v_reset"], result["how_reset"] = rule2_max_slope(after)
    return result


# ===============================================================================
# Retention analysis (protocol 6.3)
# ===============================================================================

def read_curve(rows):
    """(t, R) arrays of the phase 'read' rows with t > 0 and finite R > 0."""
    t, r = [], []
    for row in rows:
        if row.get("phase") != "read":
            continue
        tt, rr = num(row.get("t_since_write_s")), num(row.get("R_ohm"))
        if math.isnan(tt) or math.isnan(rr) or tt <= 0 or rr == 0:
            continue
        t.append(tt)
        r.append(abs(rr))
    order = np.argsort(t)
    return np.asarray(t, dtype=float)[order], np.asarray(r, dtype=float)[order]


def interp_log(t_grid, t_src, r_src):
    """Linear interpolation of log10 R in log10 t; NaN outside the source."""
    if len(t_src) < 2:
        return np.full(len(t_grid), np.nan)
    lt, lr = np.log10(t_src), np.log10(r_src)
    out = np.interp(np.log10(t_grid), lt, lr, left=np.nan, right=np.nan)
    return 10.0 ** out


def ratio_criterion(t_lrs, r_lrs, t_hrs, r_hrs, threshold):
    """Pair the two runs on the LRS instants inside the common window and
    return (t_fire, window, grid_t, ratio). t_fire is the first instant
    where R_HRS / R_LRS < threshold, or None."""
    if len(t_lrs) < 2 or len(t_hrs) < 2:
        return None, None, np.array([]), np.array([])
    lo, hi = max(t_lrs[0], t_hrs[0]), min(t_lrs[-1], t_hrs[-1])
    if hi <= lo:
        return None, None, np.array([]), np.array([])
    grid = t_lrs[(t_lrs >= lo) & (t_lrs <= hi)]
    r_l = interp_log(grid, t_lrs, r_lrs)
    r_h = interp_log(grid, t_hrs, r_hrs)
    ratio = r_h / r_l
    fire = None
    for tt, rr in zip(grid, ratio):
        if not math.isnan(rr) and rr < threshold:
            fire = float(tt)
            break
    return fire, (float(lo), float(hi)), grid, ratio


def drift_criterion(t, r, t_ref, factor):
    """First t after t_ref where R / R(t_ref) leaves [1/factor, factor];
    R(t_ref) is interpolated in log t. Returns (t_fire, r_ref) or (None, r_ref)."""
    if len(t) < 2 or t_ref < t[0] or t_ref > t[-1]:
        return None, None
    r_ref = float(interp_log(np.array([t_ref]), t, r)[0])
    for tt, rr in zip(t, r):
        if tt <= t_ref:
            continue
        ratio = rr / r_ref
        if ratio > factor or ratio < 1.0 / factor:
            return float(tt), r_ref
    return None, r_ref


# ===============================================================================
# Endurance analysis (protocol 6.4)
# ===============================================================================

def first_failure_cycle(cycles, windows, threshold, k):
    """First cycle from which the window stays below threshold for k cycles
    in a row (an empty window counts as below). None if never."""
    k = max(1, int(k))
    below = [(math.isnan(w) or w < threshold) for w in windows]
    for start in range(0, len(below) - k + 1):
        if all(below[start:start + k]):
            return int(cycles[start])
    return None


def fmt_v(v):
    return "none" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.4g}"


# ===============================================================================
# GUI
# ===============================================================================

class _TabBase:
    CLR_BG, CLR_FG = '#B8A392', '#2C2825'
    FONT = ('Segoe UI', 10)
    FONT_HINT = ('Segoe UI', 9, 'italic')
    FONT_TITLE = ('Segoe UI', 11, 'bold')

    def __init__(self, app, notebook, title):
        self.app = app
        self.frame = tk.Frame(notebook, bg=self.CLR_BG)
        notebook.add(self.frame, text=title)
        self.files = []          # list of (path, header_blocks, columns, rows)
        self.params = {}
        self.results = []
        left = tk.Frame(self.frame, bg=self.CLR_BG, width=520)
        left.pack(side='left', fill='y')
        left.pack_propagate(False)
        right = tk.Frame(self.frame, bg='white')
        right.pack(side='left', fill='both', expand=True)
        self._build_files_box(left)
        self._build_params(left)
        self._build_results(left)
        self.figure = Figure(figsize=(8, 6.5), dpi=100, facecolor='white', layout='constrained')
        self.build_axes()
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

    # -- widgets ----------------------------------------------------------
    def _build_files_box(self, parent):
        box = LabelFrame(parent, text="Files (opened read-only)", bg=self.CLR_BG, fg=self.CLR_FG, font=self.FONT_TITLE)
        box.pack(fill='x', padx=8, pady=(8, 4))
        self.listbox = tk.Listbox(box, height=5, font=('Consolas', 9), selectmode='extended')
        self.listbox.pack(fill='x', padx=6, pady=4)
        # Buttons share the frame width (grid, equal columns) so none can be
        # clipped by the fixed-width left panel.
        row = tk.Frame(box, bg=self.CLR_BG)
        row.pack(fill='x', padx=6, pady=(0, 6))
        for c in range(3):
            row.grid_columnconfigure(c, weight=1, uniform='btn')
        ttk.Button(row, text="Add...", command=self.add_files_dialog).grid(row=0, column=0, sticky='ew', padx=(0, 4), pady=2)
        ttk.Button(row, text="Remove", command=self.remove_selected).grid(row=0, column=1, sticky='ew', padx=(0, 4), pady=2)
        ttk.Button(row, text="Clear", command=self.clear_files).grid(row=0, column=2, sticky='ew', pady=2)
        ttk.Button(row, text="Compute", command=self.compute).grid(row=1, column=0, columnspan=3, sticky='ew', pady=2)

    def _build_params(self, parent):
        self.param_box = LabelFrame(parent, text="Criteria (typed by you, echoed in the Results box)",
                                    bg=self.CLR_BG, fg=self.CLR_FG, font=self.FONT_TITLE)
        self.param_box.pack(fill='x', padx=8, pady=4)
        self.param_box.grid_columnconfigure(2, weight=1)
        self.build_params(self.param_box)

    def add_param(self, parent, row, label, key, default, hint=None, width=10):
        Label(parent, text=label, bg=self.CLR_BG, fg=self.CLR_FG, font=self.FONT).grid(row=row, column=0, sticky='w', padx=6, pady=2)
        var = tk.StringVar(value=str(default))
        Entry(parent, textvariable=var, width=width, font=self.FONT).grid(row=row, column=1, sticky='w', padx=4, pady=2)
        if hint:
            Label(parent, text=hint, bg=self.CLR_BG, fg=self.CLR_FG, font=self.FONT_HINT).grid(row=row, column=2, sticky='w')
        self.params[key] = var
        return var

    def add_param_combo(self, parent, row, label, key, values, default, hint=None, width=10, command=None):
        Label(parent, text=label, bg=self.CLR_BG, fg=self.CLR_FG, font=self.FONT).grid(row=row, column=0, sticky='w', padx=6, pady=2)
        var = tk.StringVar(value=str(default))
        combo = ttk.Combobox(parent, textvariable=var, values=list(values), state='readonly', width=width, font=self.FONT)
        combo.grid(row=row, column=1, sticky='w', padx=4, pady=2)
        if hint:
            Label(parent, text=hint, bg=self.CLR_BG, fg=self.CLR_FG, font=self.FONT_HINT).grid(row=row, column=2, sticky='w')
        if command:
            combo.bind("<<ComboboxSelected>>", lambda e: command())
        self.params[key] = var
        return var

    def pf(self, key, label, minimum=None):
        text = self.params[key].get().strip()
        try:
            v = float(text)
        except ValueError:
            raise ValueError(f"{label}: '{text}' is not a number.")
        if minimum is not None and v < minimum:
            raise ValueError(f"{label} must be at least {minimum:g}.")
        return v

    def _build_results(self, parent):
        box = LabelFrame(parent, text="Results", bg=self.CLR_BG, fg=self.CLR_FG, font=self.FONT_TITLE)
        box.pack(fill='both', expand=True, padx=8, pady=4)
        self.text = scrolledtext.ScrolledText(box, height=14, font=('Consolas', 9), wrap='none', state='disabled',
                                              bg='#E5DCD3', fg=self.CLR_FG)
        self.text.pack(fill='both', expand=True, padx=6, pady=6)

    def say(self, lines, clear=False):
        try:
            self.text.config(state='normal')
            if clear:
                self.text.delete("1.0", "end")
            for line in lines:
                self.text.insert('end', ascii_text(line) + "\n")
            self.text.see('end')
            self.text.config(state='disabled')
        except Exception:
            pass

    # -- files --------------------------------------------------------------
    def add_files_dialog(self):
        paths = filedialog.askopenfilenames(title="Add files", initialdir=self.app.last_dir or os.getcwd(),
                                            filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        for p in paths:
            self.add_file(p)
        if paths:
            self.app.last_dir = os.path.dirname(paths[-1])

    def add_file(self, path):
        if any(f[0] == path for f in self.files):
            return
        try:
            header, columns, rows = parse_pica_csv(path)
        except Exception as exc:
            self.say([f"Could not read {path}: {exc}"])
            return
        blocks = header_blocks(header)
        kind = file_kind(blocks, columns)
        if not self.accepts(kind):
            self.say([f"{os.path.basename(path)}: file kind '{kind}' does not belong on this tab."])
            return
        self.files.append((path, blocks, columns, rows))
        self.listbox.insert('end', f"[{kind}] {os.path.basename(path)}")
        self.on_files_changed()

    def remove_selected(self):
        for idx in sorted(self.listbox.curselection(), reverse=True):
            self.listbox.delete(idx)
            del self.files[idx]
        self.on_files_changed()

    def clear_files(self):
        self.listbox.delete(0, 'end')
        self.files = []
        self.on_files_changed()

    def on_files_changed(self):
        pass

    def accepts(self, kind):
        return True

    def build_params(self, parent):
        pass

    def build_axes(self):
        pass

    def compute(self):
        pass

    def redraw(self):
        self.canvas.draw_idle()


class SweepTab(_TabBase):
    def accepts(self, kind):
        return kind in ("sweep", "endurance_raw")

    def build_params(self, parent):
        self.add_param_combo(parent, 0, "Rule", "rule", ("1", "2"), "1",
                             "1: compliance / current jump; 2: largest |dI/dV|")
        self.add_param(parent, 1, "F (jump factor)", "factor", "10", "rule 1 fallback |I_k / I_k-1| > F")
        self.add_param(parent, 2, "V floor (V)", "floor", "1", "ignore jumps below this |V|")
        self.add_param_combo(parent, 3, "SET polarity", "set_sign", ("+", "-"), "+", "segment where SET is searched")
        self.add_param(parent, 4, "Cycle (raw files)", "cycle", "1", "endurance raw: one cycle at a time")

    def build_axes(self):
        self.ax_lin = self.figure.add_subplot(1, 2, 1)
        self.ax_log = self.figure.add_subplot(1, 2, 2)

    def compute(self):
        try:
            rule = int(self.params["rule"].get())
            factor = self.pf("factor", "F", 1.0)
            floor_v = self.pf("floor", "V floor", 0.0)
            set_sign = 1 if self.params["set_sign"].get().strip() == "+" else -1
            cycle = int(self.pf("cycle", "Cycle", 1))
        except ValueError as exc:
            messagebox.showerror("Check the criteria", str(exc))
            return
        self.results = []
        lines = [f"rule {rule} | F {factor:g} | floor {floor_v:g} V | SET polarity {'+' if set_sign > 0 else '-'}"]
        self.ax_lin.clear()
        self.ax_log.clear()
        for path, blocks, columns, rows in self.files:
            kind = file_kind(blocks, columns)
            loops = sweep_loops(rows, cycle if kind == "endurance_raw" else None)
            name = os.path.basename(path)
            if not loops:
                lines.append(f"{name}: no sweep rows" + (f" for cycle {cycle}" if kind == "endurance_raw" else ""))
                continue
            for loop in sorted(loops):
                segs = loops[loop]
                res = estimate_switching(segs, rule, factor, floor_v, set_sign)
                self.results.append((name, loop, res))
                lines.append(f"{name} loop {loop}: V set = {fmt_v(res['v_set'])} V ({res['how_set']}), "
                             f"V reset = {fmt_v(res['v_reset'])} V ({res['how_reset']})")
                for seg in sorted(segs):
                    srows = segs[seg]
                    v = [num(r.get("V_meas_V")) for r in srows]
                    i = [num(r.get("I_A")) for r in srows]
                    colour = SEGMENT_COLOURS[(max(seg, 1) - 1) % 4]
                    self.ax_lin.plot(v, i, '.-', ms=3, lw=0.8, color=colour)
                    self.ax_log.plot(v, [abs(x) if x else float('nan') for x in i], '.-', ms=3, lw=0.8, color=colour)
                for key, colour, label in (("v_set", MARK_SET, "V set"), ("v_reset", MARK_RESET, "V reset")):
                    vv = res[key]
                    if vv is not None:
                        seg = res["set_segment" if key == "v_set" else "reset_segment"]
                        pt = [r for r in segs.get(seg, []) if num(r.get("V_set_V")) == vv]
                        if pt:
                            self.ax_lin.plot([num(pt[0].get("V_meas_V"))], [num(pt[0].get("I_A"))], 'o', ms=9,
                                             mfc='none', mew=1.5, color=colour, label=f"{label} {vv:.3g} V")
        for ax in (self.ax_lin, self.ax_log):
            ax.set_xlabel("V (V)")
            ax.grid(True, alpha=0.3)
        self.ax_lin.set_ylabel("I (A)")
        self.ax_log.set_ylabel("|I| (A)")
        self.ax_log.set_yscale("log")
        handles, labels = self.ax_lin.get_legend_handles_labels()
        if labels:
            self.ax_lin.legend(fontsize=8, loc='best')
        self.figure.suptitle(f"Sweep | rule {rule}", fontsize=10)
        self.say(lines, clear=True)
        self.redraw()



class RetentionTab(_TabBase):
    def accepts(self, kind):
        return kind == "retention_reads"

    def build_params(self, parent):
        self.add_param(parent, 0, "Ratio threshold", "ratio", "10", "R_HRS / R_LRS below this = lost")
        self.add_param(parent, 1, "Drift factor", "drift", "2", "R / R(t ref) outside [1/f, f] = drifted")
        self.add_param(parent, 2, "t ref (s)", "t_ref", "10", "reference instant for the drift rule")

    def build_axes(self):
        self.ax = self.figure.add_subplot(1, 1, 1)

    def state_of(self, blocks, path):
        state = blocks.get("protocol", {}).get("state", "")
        if not state:
            base = os.path.basename(path)
            for s in ("LRS", "HRS", "control"):
                if f"_{s}_" in base:
                    state = s
        return state or "unknown"

    def compute(self):
        try:
            ratio_thr = self.pf("ratio", "Ratio threshold", 0)
            drift = self.pf("drift", "Drift factor", 1.0)
            t_ref = self.pf("t_ref", "t ref", 0)
        except ValueError as exc:
            messagebox.showerror("Check the criteria", str(exc))
            return
        self.ax.clear()
        curves = {}
        lines = [f"ratio threshold {ratio_thr:g} | drift factor {drift:g} | t ref {t_ref:g} s"]
        self.results = []
        for path, blocks, columns, rows in self.files:
            state = self.state_of(blocks, path)
            t, r = read_curve(rows)
            curves.setdefault(state, []).append((path, t, r, blocks))
            colour = {"LRS": COLOUR_LRS, "HRS": COLOUR_HRS}.get(state, COLOUR_CONTROL)
            if len(t):
                self.ax.plot(t, r, 'o-', ms=3, lw=0.8, color=colour, label=f"{state}: {os.path.basename(path)}")
            fire, r_ref = drift_criterion(t, r, t_ref, drift)
            end = blocks.get("end", {}).get("reason", "")
            t_stop = blocks.get("protocol", {}).get("t_stop_s", "")
            verdict = f"drift {drift:g}x fires at t = {fire:.4g} s" if fire is not None else f"drift not reached by t stop ({t_stop} s, end {end or 'n/a'})"
            if r_ref is None:
                verdict = f"drift rule not evaluable (t ref {t_ref:g} s outside the data)"
            lines.append(f"{state} {os.path.basename(path)}: {len(t)} read points, {verdict}")
            self.results.append((os.path.basename(path), state, "drift", fire, r_ref, "", ""))
        if curves.get("LRS") and curves.get("HRS"):
            p_l, t_l, r_l, _ = curves["LRS"][0]
            p_h, t_h, r_h, _ = curves["HRS"][0]
            fire, window, grid, ratio = ratio_criterion(t_l, r_l, t_h, r_h, ratio_thr)
            if window is None:
                lines.append("ratio rule: no common time window between the LRS and HRS runs")
                self.results.append(("LRS+HRS", "pair", "ratio", None, None, "", ""))
            else:
                verdict = f"ratio < {ratio_thr:g} first at t = {fire:.4g} s" if fire is not None else "ratio not reached in the common window"
                lines.append(f"ratio rule (HRS interpolated onto the LRS instants in log t, common window "
                             f"{window[0]:.4g} to {window[1]:.4g} s): {verdict}")
                self.results.append(("LRS+HRS", "pair", "ratio", fire, None, window[0], window[1]))
                ax2 = self.ax.twinx()
                ax2.plot(grid, ratio, '--', lw=1.0, color=SEGMENT_COLOURS[2], label="R_HRS / R_LRS (interp.)")
                ax2.set_ylabel("ratio")
                ax2.set_yscale("log")
                ax2.axhline(ratio_thr, ls=':', lw=0.8, color='#777777')
                ax2.legend(fontsize=8, loc='lower right')
                self.ax.set_title(f"ratio on common window {window[0]:.3g} to {window[1]:.3g} s, linear in log t; no extrapolation",
                                  fontsize=9)
        self.ax.set_xscale("log")
        self.ax.set_yscale("log")
        self.ax.set_xlabel("t since write (s)")
        self.ax.set_ylabel("R (ohm)")
        self.ax.grid(True, alpha=0.3, which='both')
        if self.files:
            self.ax.legend(fontsize=8, loc='best')
        self.figure.suptitle("Retention", fontsize=10)
        self.say(lines, clear=True)
        self.redraw()



class EnduranceTab(_TabBase):
    def accepts(self, kind):
        return kind in ("endurance_summary", "endurance_raw")

    def build_params(self, parent):
        self.add_param(parent, 0, "Window threshold", "threshold", "10", "R_HRS / R_LRS below this = failed")
        self.add_param(parent, 1, "k in a row", "k", "3", "cycles in a row below threshold")
        self.add_param_combo(parent, 2, "Raw cycle", "raw_cycle", (), "", "I-V of one saved cycle", width=8,
                             command=self.compute)

    def build_axes(self):
        gs = self.figure.add_gridspec(2, 2)
        self.ax_r = self.figure.add_subplot(gs[0, 0])
        self.ax_w = self.figure.add_subplot(gs[1, 0])
        self.ax_h = self.figure.add_subplot(gs[0, 1])
        self.ax_iv = self.figure.add_subplot(gs[1, 1])

    def on_files_changed(self):
        cycles = set()
        for path, blocks, columns, rows in self.files:
            if file_kind(blocks, columns) == "endurance_raw":
                cycles.update(int(num(r.get("cycle"))) for r in rows if r.get("phase") in ("sweep", "pulse"))
        values = [str(c) for c in sorted(cycles)]
        try:
            combo = [w for w in self.param_box.winfo_children() if isinstance(w, ttk.Combobox)][0]
            combo['values'] = values
            if values and self.params["raw_cycle"].get() not in values:
                self.params["raw_cycle"].set(values[0])
        except Exception:
            pass

    def compute(self):
        try:
            threshold = self.pf("threshold", "Window threshold", 0)
            k = int(self.pf("k", "k in a row", 1))
        except ValueError as exc:
            messagebox.showerror("Check the criteria", str(exc))
            return
        for ax in (self.ax_r, self.ax_w, self.ax_h, self.ax_iv):
            ax.clear()
        lines = [f"threshold {threshold:g} | k {k}"]
        self.results = []
        modes = set()
        summaries = [(p, b, c, r) for p, b, c, r in self.files if file_kind(b, c) == "endurance_summary"]
        raws = [(p, b, c, r) for p, b, c, r in self.files if file_kind(b, c) == "endurance_raw"]
        for path, blocks, columns, rows in summaries:
            mode = blocks.get("protocol", {}).get("mode", "unknown")
            modes.add(mode)
            cyc = np.array([num(r.get("cycle")) for r in rows])
            lrs = np.array([num(r.get("R_LRS_ohm")) for r in rows])
            hrs = np.array([num(r.get("R_HRS_ohm")) for r in rows])
            win = np.array([num(r.get("window")) for r in rows])
            name = os.path.basename(path)
            self.ax_r.plot(cyc, np.abs(lrs), 'o-', ms=3, lw=0.8, color=COLOUR_LRS, label=f"R LRS {name}")
            self.ax_r.plot(cyc, np.abs(hrs), 's-', ms=3, lw=0.8, color=COLOUR_HRS, label=f"R HRS {name}")
            self.ax_w.plot(cyc, np.abs(win), 'o-', ms=3, lw=0.8, color=SEGMENT_COLOURS[2], label=name)
            fail = first_failure_cycle(cyc, win, threshold, k)
            n_ret = int(np.nansum([num(r.get("retried")) == 1 for r in rows]))
            verdict = f"window < {threshold:g} for {k} cycles from cycle {fail}" if fail is not None else "not reached"
            lines.append(f"{name} ({mode}, {len(rows)} cycles, {n_ret} retried): {verdict}")
            self.results.append((name, mode, len(rows), fail, n_ret))
            vs = [num(r.get("V_set_trip_V")) for r in rows]
            vr = [num(r.get("V_reset_trip_V")) for r in rows]
            vs = [v for v in vs if not math.isnan(v)]
            vr = [abs(v) for v in vr if not math.isnan(v)]
            if vs:
                self.ax_h.hist(vs, bins=min(20, max(3, len(set(vs)))), color=MARK_SET, alpha=0.7, label=f"|V set trip| n={len(vs)}")
            if vr:
                self.ax_h.hist(vr, bins=min(20, max(3, len(set(vr)))), color=MARK_RESET, alpha=0.7, label=f"|V reset trip| n={len(vr)}")
            if not vs and not vr:
                lines.append(f"{name}: no compliance trips recorded (histogram empty)")
        if len(modes) > 1:
            lines.append("WARNING: sweep-mode and pulse-mode summaries are overlaid; Lanza 2021: the integrated "
                         "stress differs by orders of magnitude, the two are not comparable.")
            self.ax_w.set_title("WARNING: sweep and pulse summaries overlaid, not comparable", fontsize=9, color='#8B3A2F')
        self.ax_w.axhline(threshold, ls='--', lw=0.8, color='#777777')
        self.ax_r.set_xlabel("cycle")
        self.ax_r.set_ylabel("R (ohm)")
        self.ax_r.set_yscale("log")
        self.ax_w.set_xlabel("cycle")
        self.ax_w.set_ylabel("window")
        self.ax_w.set_yscale("log")
        self.ax_h.set_xlabel("|V trip| (V)")
        self.ax_h.set_ylabel("count")
        for ax in (self.ax_r, self.ax_w, self.ax_h):
            ax.grid(True, alpha=0.3)
            handles, labels = ax.get_legend_handles_labels()
            if labels:
                ax.legend(fontsize=7, loc='best')
        cycle_text = self.params["raw_cycle"].get().strip()
        self.ax_iv.set_xlabel("V (V)")
        self.ax_iv.set_ylabel("I (A)")
        self.ax_iv.grid(True, alpha=0.3)
        if raws and cycle_text:
            cycle = int(float(cycle_text))
            for path, blocks, columns, rows in raws:
                loops = sweep_loops(rows, cycle)
                for loop, segs in loops.items():
                    for seg, srows in sorted(segs.items()):
                        leg = srows[0].get("leg", "")
                        colour = COLOUR_LRS if leg == "set" else COLOUR_HRS
                        self.ax_iv.plot([num(r.get("V_meas_V")) for r in srows], [num(r.get("I_A")) for r in srows],
                                        '.-', ms=3, lw=0.8, color=colour, label=f"{leg} seg {seg}" if seg in (1, 0) else None)
                        hollow = [r for r in srows if num(r.get("compliance_flag")) == 1]
                        if hollow:
                            self.ax_iv.plot([num(r.get("V_meas_V")) for r in hollow], [num(r.get("I_A")) for r in hollow],
                                            'o', ms=5, mfc='none', color=colour)
            self.ax_iv.set_title(f"raw cycle {cycle}", fontsize=9)
            handles, labels = self.ax_iv.get_legend_handles_labels()
            if labels:
                self.ax_iv.legend(fontsize=7, loc='best')
        self.figure.suptitle("Endurance", fontsize=10)
        self.say(lines, clear=True)
        self.redraw()



class MemristorAnalysisGUI:
    CLR_BG, CLR_HEADER, CLR_FG, CLR_GOLD = '#B8A392', '#E5DCD3', '#2C2825', '#BA6B5E'

    def __init__(self, root, tab=None):
        self.root = root
        self.last_dir = os.getcwd()      # where the Add... dialog opens next
        self.tabs = {}
        try:
            self.root.title("Memristor Analysis (read-only)")
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{min(1500, sw - 20)}x{min(900, sh - 90)}+0+0")
            self.root.minsize(1100, 650)
            self.root.configure(bg=self.CLR_BG)
            mpl.rcParams['font.family'] = 'Segoe UI'
        except Exception:
            pass
        self.create_widgets()
        try:
            if isinstance(tab, str) and tab in self.tabs:
                self.notebook.select(self.tabs[tab].frame)
        except Exception:
            pass

    def create_widgets(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        Label(header, text="Memristor Analysis (read-only, writes nothing)", bg=self.CLR_HEADER, fg=self.CLR_GOLD,
              font=('Segoe UI', 15, 'bold')).pack(side='left', padx=20, pady=8)
        Label(header, text=f"Version {PROGRAM_VERSION} | protocol v{PROTOCOL_VERSION}", bg=self.CLR_HEADER,
              fg=self.CLR_FG, font=('Segoe UI', 10)).pack(side='right', padx=20)
        Label(header, text="writes no files: results stay in the Results box", bg=self.CLR_HEADER, fg=self.CLR_FG,
              font=('Segoe UI', 9, 'italic')).pack(side='right', padx=6)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=8)
        self.tabs["sweep"] = SweepTab(self, self.notebook, "Sweep")
        self.tabs["retention"] = RetentionTab(self, self.notebook, "Retention")
        self.tabs["endurance"] = EnduranceTab(self, self.notebook, "Endurance")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="PICA memristor analysis (read-only)")
    parser.add_argument("--tab", choices=TABS, default=None)
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = tk.Tk()
    MemristorAnalysisGUI(root, tab=args.tab)
    root.mainloop()


if __name__ == '__main__':
    main()
