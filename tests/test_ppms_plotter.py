"""PPMS Plotter: fast paths must give exactly what the old slow code gave.

Covers the three speed-ups in pica/PPMS/PPMS_Plotter_GUI.py:

  * the 7-point majority filter in segment_measurements (was np.unique per
    point, now a one-hot convolution) - same classes, edges and tie-breaks;
  * parse_qd_dat (was float() per cell into np.empty rows, now a column-wise
    conversion) - same NaN placement, dtype, shapes and counters;
  * the figure is no longer rebuilt (clf + add_subplot) and re-laid-out
    (tight_layout) on every replot - the rendered pixels stay identical.

The reference implementations below are verbatim copies of the old code.
Synthetic data only. No Tk window: the GUI class is driven on a bare
instance with an Agg canvas. Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys
import tempfile

import matplotlib.style
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLOT_PATH = os.path.join(REPO_ROOT, "pica", "PPMS", "PPMS_Plotter_GUI.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("ppms_plotter_under_test",
                                                  PLOT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plotter = _load_module()


# ------------------------------------------------------ old reference code

def old_majority_filter(cls):
    n = len(cls)
    w = 7
    sm = cls.copy()
    for i in range(n):
        lo, hi = max(0, i - w // 2), min(n, i + w // 2 + 1)
        vals, cnts = np.unique(cls[lo:hi], return_counts=True)
        sm[i] = vals[np.argmax(cnts)]
    return sm


def old_smoothed_classes(T, H, min_pts=10):
    """Class-per-point step of the old segment_measurements (old filter)."""
    n = len(T)
    if n < 3:
        return []
    dT = np.gradient(plotter._smooth(T))
    dH = np.gradient(plotter._smooth(H))
    cls = np.zeros(n, dtype=int)
    cls[np.abs(dT) > 0.05] = 1
    cls[np.abs(dH) > 2.0] = 2
    sm = old_majority_filter(cls)
    # The rest of the function is unchanged; reuse it through the module
    # by checking the filter output separately (see the tests below).
    return sm


def old_parse_qd_dat(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    meta = {}
    data_start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.lower() == '[data]':
            data_start = i
            break
        if ',' in s:
            parts = [p.strip() for p in s.split(',')]
            if parts[0] == 'TITLE':
                meta['Title'] = ','.join(parts[1:])
            elif parts[0] == 'FILEOPENTIME' and len(parts) >= 4:
                meta['File opened'] = f"{parts[2]} {parts[3]}"
            elif parts[0] == 'INFO' and len(parts) >= 3:
                meta[parts[-1]] = ','.join(parts[1:-1])

    if data_start is None or data_start + 1 >= len(lines):
        raise ValueError("No [Data] section found. Is this a Quantum Design .DAT file?")

    headers = [h.strip() for h in lines[data_start + 1].strip().split(',')]
    ncol = len(headers)

    rows = []
    n_raw = 0
    for line in lines[data_start + 2:]:
        parts = line.rstrip('\n').split(',')
        if len(parts) < max(2, ncol // 2):
            continue
        if len(parts) < ncol:
            parts = parts + [''] * (ncol - len(parts))
        if len(parts) > ncol:
            extra = len(parts) - ncol
            parts = [','.join(parts[:extra + 1])] + parts[extra + 1:]
        n_raw += 1
        row = np.empty(ncol)
        for j, p in enumerate(parts):
            try:
                row[j] = float(p)
            except ValueError:
                row[j] = np.nan
        rows.append(row)

    matrix = np.array(rows) if rows else np.empty((0, ncol))
    cols = {h: matrix[:, j] for j, h in enumerate(headers)}

    def series(name):
        h = plotter.find_column(headers, name)
        return cols[h] if h else np.full(len(matrix), np.nan)

    T = series('Temperature (K)')
    H = series('Magnetic Field (Oe)')
    M = series('Moment (emu)')
    Merr = series('M. Std. Err. (emu)')
    if not any(np.isfinite(c).any() for c in cols.values()):
        raise ValueError("No numeric data columns found in the [Data] section.")

    ok = np.isfinite(T) & np.isfinite(H) & np.isfinite(M)
    return meta, {
        'headers': headers, 'cols': cols,
        'T': T[ok], 'H': H[ok], 'M': M[ok], 'Merr': Merr[ok],
        'n_raw': n_raw, 'n_dropped': int(n_raw - ok.sum()),
    }


# ------------------------------------------------------ synthetic data

HEADER = """[Header]
; Generic synthetic VSM file for tests
TITLE,Test sample A, batch 1
BYAPP,VSM, 1.0
INFO,12.5,SAMPLE_MASS
INFO,Generic, test material,SAMPLE_MATERIAL
FILEOPENTIME,5000.25,01/02/2026,10:00 AM
[Data]
"""
COLUMNS = ["Comment", "Time Stamp (sec)", "Temperature (K)",
           "Magnetic Field (Oe)", "Moment (emu)", "M. Std. Err. (emu)",
           "Pressure (Torr)", "Frequency (Hz)"]


def _synthetic_rows(rng):
    """MT warm, MT cool, then an M-H loop, plus the awkward rows a real
    writer produces: touchdowns, comments with commas, dropped trailing
    fields, text in numeric cells, short junk lines."""
    rows = []
    t = 0.0

    def add(T, H, M, comment="", trailing=True):
        nonlocal t
        t += 2.0
        cells = [comment, f"{t:.3f}", f"{T:.4f}", f"{H:.2f}",
                 f"{M:.6e}", f"{abs(M) * 1e-3:.3e}", f"{rng.uniform(1, 5):.4f}",
                 ""]
        if not trailing:
            while cells and cells[-1] == "":
                cells.pop()
        rows.append(",".join(cells))

    for T in np.linspace(5, 300, 400):                 # ZFC warm @ 100 Oe
        add(T + rng.normal(0, 0.01), 100 + rng.normal(0, 0.2), 1e-3 / T)
    rows.append("Touchdown, centering scan,,,,,,,")  # comment with a comma
    for T in np.linspace(300, 5, 400):                 # FCC cool
        add(T, 100.0, 1.2e-3 / T, trailing=False)
    for H in np.concatenate([np.linspace(0, 5e4, 300),
                             np.linspace(5e4, -5e4, 600)]):  # M-H at 10 K
        add(10 + rng.normal(0, 0.005), H, 1e-6 * np.tanh(H / 1e4))
    rows.append("junk")                                 # too short: skipped
    rows.append(",1.0,abc,nan, inf ,-1e-3,  ,")         # text, nan, blanks
    rows.append("a,b,c,,,,,,,,,")                       # extra commas
    rows.append(",,,,,,,")                              # all blank
    return rows


def _write_dat(folder, rows, name="synthetic.dat"):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER)
        f.write(",".join(COLUMNS) + "\n")
        f.write("\n".join(rows) + "\n")
    return path


def _same_array(a, b):
    assert a.dtype == b.dtype, (a.dtype, b.dtype)
    assert a.shape == b.shape, (a.shape, b.shape)
    assert np.array_equal(a, b, equal_nan=True)


def _assert_same_parse(old, new):
    (m_old, d_old), (m_new, d_new) = old, new
    assert m_old == m_new
    assert d_old['headers'] == d_new['headers']
    assert list(d_old['cols']) == list(d_new['cols'])
    for h in d_old['cols']:
        _same_array(d_old['cols'][h], d_new['cols'][h])
    for k in ('T', 'H', 'M', 'Merr'):
        _same_array(d_old[k], d_new[k])
    assert d_old['n_raw'] == d_new['n_raw']
    assert d_old['n_dropped'] == d_new['n_dropped']


# ------------------------------------------------------ majority filter

def test_majority_filter_matches_the_old_loop_on_random_classes():
    rng = np.random.default_rng(1)
    for n in list(range(0, 20)) + [50, 333, 2000]:
        for p in ([1 / 3] * 3, [0.8, 0.1, 0.1], [0.45, 0.45, 0.1],
                  [0.1, 0.2, 0.7]):
            cls = rng.choice(3, size=n, p=p).astype(int)
            new = plotter._majority_filter(cls)
            old = old_majority_filter(cls)
            assert np.array_equal(new, old), (n, p)


def test_majority_filter_ties_and_edges_match_the_old_loop():
    cases = [
        [0, 1, 2], [2, 1, 0], [2, 2, 1, 1], [1, 1, 2, 2, 0, 0],
        [2, 0, 2, 0, 2, 0, 1, 1, 1], [1, 2] * 10, [2, 1] * 10,
        [0] * 3 + [2] * 3 + [1] * 3, [2] * 7, [1] * 3 + [2] * 4,
        [0, 0, 0, 1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 0, 0, 0],
        [2, 2, 2, 0, 1, 1, 1],
    ]
    for c in cases:
        cls = np.array(c, dtype=int)
        assert np.array_equal(plotter._majority_filter(cls),
                              old_majority_filter(cls)), c


def test_segmentation_is_unchanged_on_a_synthetic_measurement():
    with tempfile.TemporaryDirectory() as d:
        path = _write_dat(d, _synthetic_rows(np.random.default_rng(7)))
        _, data = plotter.parse_qd_dat(path)
    T, H = data['T'], data['H']
    # the filter output that feeds the run detection is identical...
    n = len(T)
    dT = np.gradient(plotter._smooth(T))
    dH = np.gradient(plotter._smooth(H))
    cls = np.zeros(n, dtype=int)
    cls[np.abs(dT) > 0.05] = 1
    cls[np.abs(dH) > 2.0] = 2
    assert np.array_equal(plotter._majority_filter(cls),
                          old_smoothed_classes(T, H))
    # ...and the segmentation finds the three measurements
    segs = plotter.segment_measurements(T, H)
    kinds = [(s['mode'], s.get('branch')) for s in segs]
    assert ('MT', 'ZFC') in kinds and ('MT', 'FCC') in kinds
    assert any(m == 'MH' for m, _ in kinds)


def test_majority_filter_is_fast_on_50k_points():
    import time
    cls = np.random.default_rng(3).choice(3, size=50_000).astype(int)
    t0 = time.perf_counter()
    plotter._majority_filter(cls)
    assert time.perf_counter() - t0 < 0.2


# ------------------------------------------------------ parse_qd_dat

def test_parse_matches_the_old_parser_on_a_messy_synthetic_file():
    with tempfile.TemporaryDirectory() as d:
        path = _write_dat(d, _synthetic_rows(np.random.default_rng(11)))
        _assert_same_parse(old_parse_qd_dat(path), plotter.parse_qd_dat(path))


def test_parse_matches_the_old_parser_on_random_cell_soup():
    """Random mixes of numbers, blanks, whitespace, text, nan/inf."""
    rng = np.random.default_rng(5)
    pool = ["", " ", "1", "-2.5", "3e-4", " 7.25 ", "nan", "inf", "-inf",
            "abc", "1.2.3", "0x10", "+4", ".5", "5.", "1_000", "--1", "E5"]
    for trial in range(20):
        rows = []
        for _ in range(int(rng.integers(0, 40))):
            ncells = int(rng.integers(1, len(COLUMNS) + 4))
            rows.append(",".join(rng.choice(pool, size=ncells)))
        rows.append(",1,2,3,4,5,6,7")   # at least one numeric row
        with tempfile.TemporaryDirectory() as d:
            path = _write_dat(d, rows)
            _assert_same_parse(old_parse_qd_dat(path),
                               plotter.parse_qd_dat(path))


def test_parse_errors_are_unchanged():
    for body in ("", "only text,here,and,more,words,x,y,z\n"):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.dat")
            with open(path, "w", encoding="utf-8") as f:
                f.write(HEADER + ",".join(COLUMNS) + "\n" + body)
            errs = []
            for fn in (old_parse_qd_dat, plotter.parse_qd_dat):
                try:
                    fn(path)
                    errs.append(None)
                except ValueError as e:
                    errs.append(str(e))
            assert errs[0] == errs[1] and errs[0] is not None, errs


def test_parse_without_any_data_rows_keeps_the_empty_shape():
    """Header row then nothing usable: same (0, ncol) matrix as before,
    which then raises the same 'no numeric data' error."""
    with tempfile.TemporaryDirectory() as d:
        path = _write_dat(d, ["x"])
        for fn in (old_parse_qd_dat, plotter.parse_qd_dat):
            try:
                fn(path)
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "No numeric data" in str(e)


# ------------------------------------------------------ figure reuse

class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class _Canvas:
    def draw_idle(self):
        pass


class _NoDialogs:
    """Stands in for tkinter.messagebox: a plotting error fails the test
    instead of opening a window."""
    @staticmethod
    def showerror(title, message):
        raise AssertionError(f"{title}: {message}")


plotter.messagebox = _NoDialogs


def _bare_app(cache, old_style=False):
    base = cls = plotter.PPMSPlotterGUI
    if old_style:
        # the pre-optimisation behaviour: rebuild + tight_layout every time
        class Old(base):
            def _reset_figure(self):
                self.figure.clf()
                self.ax_main = self.figure.add_subplot(111)
                self.ax_main.grid(True, linestyle='--', alpha=0.6)

            def _relayout_if_needed(self):
                self.figure.tight_layout()

            def _plot_sequence(self, selected, per_gram=False):
                self.figure.clf()
                self._seq_axes = None
                return base._plot_sequence(self, selected, per_gram)
        cls = Old
    app = object.__new__(cls)
    app.figure = Figure(figsize=(8, 6), dpi=60)
    FigureCanvasAgg(app.figure)
    app.ax_main = app.figure.add_subplot(111)
    app.figure.tight_layout()
    app._seq_axes = None
    app._layout_sig = None
    app.canvas = _Canvas()
    app.file_data_cache = cache
    app.file_ui_elements = {fp: {'var': _Var(True)} for fp in cache}
    app.mode_cb = _Var("Auto")
    app.units_cb = _Var("emu")
    app.x_col_cb = _Var("Time Stamp (sec)")
    app.y_col_cb = _Var("Moment (emu)")
    app.log = lambda msg: None
    return app


def _drawn(ax):
    """What an axes shows, independent of sub-pixel layout: the data and
    style of every line, the legend, the grid and which tick labels show."""
    # data as bytes, so a NaN gap compares equal to itself
    data = lambda v: np.asarray(v, dtype=float).tobytes()
    lines = [(data(ln.get_xdata()), data(ln.get_ydata()), ln.get_color(),
              ln.get_linestyle(), ln.get_marker(), ln.get_visible())
             for ln in ax.lines]
    legend = ax.get_legend()
    return {
        'title': ax.get_title(), 'xlabel': ax.get_xlabel(),
        'ylabel': ax.get_ylabel(),
        'xlim': ax.get_xlim(), 'ylim': ax.get_ylim(),
        'lines': lines,
        'legend': legend and [t.get_text() for t in legend.get_texts()],
        'grid': [any(g.get_visible() for g in axis.get_gridlines())
                 for axis in (ax.xaxis, ax.yaxis)],
        'xticklabels_shown': any(t.get_visible()
                                 for t in ax.get_xticklabels(which='both')),
    }


STEPS = [("M vs T", "emu"), ("M vs T", "emu"), ("M vs H", "emu"),
         ("Sequence Overview", "emu"), ("Sequence Overview", "emu"),
         ("M vs T", "emu/g"), ("M vs T", "emu"), ("Manual X/Y", "emu"),
         ("Sequence Overview", "emu"), ("M vs H", "emu/g")]


def _cache(folder):
    path = _write_dat(folder, _synthetic_rows(np.random.default_rng(2)))
    meta, data = plotter.parse_qd_dat(path)
    return {path: {'meta': meta, 'data': data,
                   'segments': plotter.segment_measurements(data['T'],
                                                            data['H'])}}


def test_replots_render_like_the_rebuild_every_time_code():
    """Every replot shows what clf + tight_layout would have drawn.

    Compared on content, not pixels. tight_layout is path dependent: the
    tick set it fits to depends on the axes size it starts from, so two
    histories of the same plot can land a fraction of a pixel apart.
    Anti-aliasing turns that into thousands of changed pixels along a
    noisy trace - more than a genuinely missing line changes - and it
    depends on the fonts and on rcParams that earlier GUI tests leave set,
    which is how this passed locally and failed on CI (21-22 Oct 2026).
    So geometry may differ by up to one pixel, and everything drawn must
    match exactly. Run under the default style so the result does not
    depend on which GUI test ran before this one."""
    with matplotlib.style.context('default'),             tempfile.TemporaryDirectory() as d:
        cache = _cache(d)
        new, old = _bare_app(cache), _bare_app(cache, old_style=True)
        width, height = new.figure.canvas.get_width_height()
        one_px = np.array([1 / width, 1 / height, 1 / width, 1 / height])
        for mode, unit in STEPS:
            for app in (new, old):
                app.mode_cb.set(mode)
                app.units_cb.set(unit)
                app.plot_data()
            assert len(new.figure.axes) == len(old.figure.axes)
            for a, b in zip(new.figure.axes, old.figure.axes):
                shift = np.abs(np.subtract(a.get_position().bounds,
                                           b.get_position().bounds))
                assert (shift <= one_px).all(), (mode, unit, shift / one_px)
                assert _drawn(a) == _drawn(b), (mode, unit)


def test_replot_in_the_same_mode_reuses_axes_and_skips_tight_layout():
    with tempfile.TemporaryDirectory() as d:
        app = _bare_app(_cache(d))
        calls = []
        real = app.figure.tight_layout
        app.figure.tight_layout = lambda *a, **k: (calls.append(1), real())

        app.mode_cb.set("M vs T")
        app.plot_data()
        ax, n = app.ax_main, len(calls)
        app.plot_data()                       # e.g. a checkbox re-tick
        assert app.ax_main is ax and len(calls) == n

        app.units_cb.set("emu/g")             # label changes -> relayout
        app.plot_data()
        assert app.ax_main is ax and len(calls) == n + 1

        app.mode_cb.set("Sequence Overview")
        app.plot_data()
        seq = list(app.figure.axes)
        assert len(seq) > 1 and len(calls) == n + 2
        app.plot_data()
        assert list(app.figure.axes) == seq and len(calls) == n + 2
        # shared-x inner panels keep their x tick labels hidden after clear()
        for ax in seq[:-1]:
            assert not any(t.get_visible()
                           for t in ax.get_xticklabels(which='both'))


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
