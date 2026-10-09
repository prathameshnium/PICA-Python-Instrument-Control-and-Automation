"""Sweep generation and zero handling of IV_K6221_DC_Sweep_GUI.py (v1.7).

The K6221 + K2182 passthrough I-V module carries its own pure generator
(build_sweep_points / parse_custom_list / check_sweep_limits). Checked here:

  linear      N points from Start to Stop inclusive, ends exact, zero is a
              legal Start, Stop or interior point (the v1.6 truthiness bug
              refused "0" with "All fields ... are required");
  log         even in log10|I|, sign of Start kept, refuses 0 or a zero
              crossing with a message that names the Custom List way out;
  custom      every separator, order kept, exact 0 kept, first bad token
              named; parse_custom_list is byte-identical to the K2400 copy
              so List Maker output reads the same everywhere;
  limits      |I| above 105 mA refused before the instrument is touched;
  GUI         _collect_params accepts 0 uA / 0 s entries, scales uA -> A,
              and the resistance written for I = 0 is NaN, not inf.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import inspect
import math
import os
import sys
import tempfile

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

PATH = os.path.join(REPO_ROOT, "pica", "keithley", "delta_mode", "IV_K6221_DC_Sweep_GUI.py")
K2400_PATH = os.path.join(REPO_ROOT, "pica", "keithley", "k2400", "IV_K2400_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = _load("iv_k6221_sweep_gen", PATH)


def _raises(fn, *needle):
    try:
        fn()
    except ValueError as e:
        for n in needle:
            assert n.lower() in str(e).lower(), (n, str(e))
        return str(e)
    raise AssertionError("no ValueError raised")


# ---------------------------------------------------------------------------
# linear
# ---------------------------------------------------------------------------

def test_linear_ends_exact_and_passes_through_zero():
    pts = M.build_sweep_points(M.SWEEP_LINEAR, start_val=-10, stop_val=10, num_points=21)
    assert len(pts) == 21 and pts[0] == -10.0 and pts[-1] == 10.0
    assert 0.0 in list(pts)
    assert np.allclose(np.diff(pts), 1.0)


def test_linear_zero_start_or_stop_is_legal():
    assert list(M.build_sweep_points(M.SWEEP_LINEAR, 0, 10, 3)) == [0.0, 5.0, 10.0]
    assert list(M.build_sweep_points(M.SWEEP_LINEAR, 10, 0, 3)) == [10.0, 5.0, 0.0]


def test_linear_point_count_validation():
    _raises(lambda: M.build_sweep_points(M.SWEEP_LINEAR, 0, 1, 1), "2 or more")
    _raises(lambda: M.build_sweep_points(M.SWEEP_LINEAR, 0, 1, "x"), "whole number")


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

def test_log_sweep_is_even_in_decades_and_keeps_sign():
    pts = M.build_sweep_points(M.SWEEP_LOG, start_val=-1, stop_val=-1000, num_points=4)
    assert np.allclose(pts, [-1, -10, -100, -1000])
    assert pts[0] == -1.0 and pts[-1] == -1000.0


def test_log_sweep_refuses_zero_and_zero_crossing():
    for a, b in ((0, 10), (10, 0), (-1, 1)):
        msg = _raises(lambda: M.build_sweep_points(M.SWEEP_LOG, a, b, 5), "zero")
        assert "Custom List" in msg


# ---------------------------------------------------------------------------
# custom
# ---------------------------------------------------------------------------

def test_custom_list_every_separator_and_exact_zero():
    for text in ("0, 1, 2, 5", "0;1;2;5", "0 1 2 5", "0\t1\t2\t5", "0\n1\n2\n5", "0,\n 1 ;2\t5\n"):
        assert M.parse_custom_list(text) == [0.0, 1.0, 2.0, 5.0], repr(text)
    pts = M.build_sweep_points(M.SWEEP_CUSTOM, custom_values=[5, 0, -5, 0])
    assert list(pts) == [5.0, 0.0, -5.0, 0.0]


def test_custom_list_names_bad_token_and_refuses_empty():
    assert "'abc'" in _raises(lambda: M.parse_custom_list("0, abc, 1"), "not a number")
    _raises(lambda: M.parse_custom_list("   \n"), "empty")
    _raises(lambda: M.parse_custom_list("1, inf"), "finite")
    _raises(lambda: M.build_sweep_points(M.SWEEP_CUSTOM, custom_values=[]), "empty")


def test_custom_parser_identical_to_the_k2400_copy():
    """The List Maker is verified against the K2400 parser; the K6221 copy
    must be the same code so the verification carries over."""
    k2400 = _load("iv_k2400_for_k6221_test", K2400_PATH)
    assert inspect.getsource(M.parse_custom_list) == inspect.getsource(k2400.parse_custom_list)
    assert inspect.getsource(M.check_sweep_limits) == inspect.getsource(k2400.check_sweep_limits)
    assert inspect.getsource(M.ascii_label) == inspect.getsource(k2400.ascii_label)
    assert M.parse_custom_list(M.CUSTOM_LIST_EXAMPLE) == k2400.parse_custom_list(M.CUSTOM_LIST_EXAMPLE)


def test_unknown_sweep_type_refused():
    _raises(lambda: M.build_sweep_points("Delta", 0, 1, 3), "unknown")


# ---------------------------------------------------------------------------
# limits
# ---------------------------------------------------------------------------

def test_k6221_limit_is_105_ma():
    assert M.K6221_MAX_CURRENT_A == 0.105
    M.check_sweep_limits(np.array([0.105, -0.105]), M.K6221_MAX_CURRENT_A, "A")
    _raises(lambda: M.check_sweep_limits(np.array([0.1051]), M.K6221_MAX_CURRENT_A, "A"), "limit")
    _raises(lambda: M.check_sweep_limits(np.array([]), 1, "A"), "no points")


# ---------------------------------------------------------------------------
# GUI-level validation without a Tk root
# ---------------------------------------------------------------------------

class _Entry:
    def __init__(self, text):
        self.text = text

    def get(self, *a):
        return self.text


def _gui(sweep_type, start="-10", stop="10", points="21", custom="", delay="0.2",
         initial="2", compliance="10", visa="GPIB0::13::INSTR", save=None, name="S"):
    gui = object.__new__(M.Passthrough_IV_GUI)
    gui.sweep_type_var = _Entry(sweep_type)
    gui.entries = {"Sample Name": _Entry(name), "Start Current": _Entry(start),
                   "Stop Current": _Entry(stop), "Num Points": _Entry(points),
                   "Delay": _Entry(delay), "Initial Delay": _Entry(initial),
                   "Compliance": _Entry(compliance)}
    gui.k6221_cb = _Entry(visa)
    gui.custom_list_text = _Entry(custom)
    gui.save_path = tempfile.gettempdir() if save is None else save
    return gui


def test_collect_params_accepts_zero_start_and_zero_delays():
    params, pts = _gui(M.SWEEP_LINEAR, start="0", stop="10", points="11",
                       delay="0", initial="0")._collect_params()
    assert pts[0] == 0.0 and math.isclose(pts[-1], 10e-6)
    assert params['delay'] == 0.0 and params['initial_delay'] == 0.0
    assert math.isclose(params['max_abs_current_A'], 10e-6)

    with tempfile.TemporaryDirectory() as d:
        g = _gui(M.SWEEP_LINEAR, start="0", stop="10", points="11")
        g.data_filepath = os.path.join(d, "lin.dat")
        g._write_file_header(params, len(pts))
        with open(g.data_filepath, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    assert "# Start current (uA): 0" in lines and "# Stop current (uA): 10" in lines
    assert "# Sweep type: Start -> Stop (linear)" in lines


def test_collect_params_scales_micro_amps_and_checks_limit():
    _, pts = _gui(M.SWEEP_LINEAR, start="-1000", stop="1000", points="3")._collect_params()
    assert np.allclose(pts, [-1e-3, 0, 1e-3])
    _raises(lambda: _gui(M.SWEEP_LINEAR, start="0", stop="200000", points="3")._collect_params(), "limit")


def test_collect_params_custom_list_in_micro_amps():
    params, pts = _gui(M.SWEEP_CUSTOM, custom="0, 1, 2; 5\n0")._collect_params()
    assert np.allclose(pts, np.array([0, 1, 2, 5, 0]) * 1e-6)
    assert params['custom_list_str'].strip() == "0, 1, 2; 5\n0"


def test_collect_params_messages():
    _raises(lambda: _gui(M.SWEEP_LINEAR, name="")._collect_params(), "Sample Name")
    _raises(lambda: _gui(M.SWEEP_LINEAR, visa="")._collect_params(), "6221")
    _raises(lambda: _gui(M.SWEEP_LINEAR, save="")._collect_params(), "save location")
    _raises(lambda: _gui(M.SWEEP_LINEAR, delay="-1")._collect_params(), "negative")
    _raises(lambda: _gui(M.SWEEP_LINEAR, compliance="0")._collect_params(), "Compliance")
    _raises(lambda: _gui(M.SWEEP_LINEAR, compliance="200")._collect_params(), "105")
    _raises(lambda: _gui(M.SWEEP_LINEAR, points="1")._collect_params(), "2 or more")
    _raises(lambda: _gui(M.SWEEP_LOG, start="0", stop="10")._collect_params(), "zero")
    _raises(lambda: _gui(M.SWEEP_CUSTOM, custom="1, two")._collect_params(), "'two'")


# ---------------------------------------------------------------------------
# data file: header and the I = 0 row
# ---------------------------------------------------------------------------

class _Null:
    def __getattr__(self, name):
        return lambda *a, **k: None


def test_zero_current_row_is_nan_not_inf():
    gui = _gui(M.SWEEP_CUSTOM, custom="0, 1, 0")
    params, pts = gui._collect_params()
    with tempfile.TemporaryDirectory() as d:
        gui.data_filepath = os.path.join(d, "t.dat")
        gui._write_file_header(params, len(pts))
        gui.log = lambda *a, **k: None
        gui.data_storage = {'current': [], 'voltage': [], 'resistance': []}
        gui.line_main = gui.line_sub = gui.ax_main = gui.ax_sub = gui.figure = gui.canvas = _Null()
        gui._update_ui_with_point(0.0, 1e-6)
        gui._update_ui_with_point(1e-6, 2e-3)
        with open(gui.data_filepath, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    assert lines[0].startswith("# Program: K6221/2182 I-V Sweep v")
    assert any(l.startswith("# Sweep type: Custom List") for l in lines)
    assert any(l == "# Custom list (uA): 0, 1, 0" for l in lines)
    for key in ("# Number of points: 3", "# Max |current| (A): 1.000000e-06", "# Compliance (V): 10",
                "# Step delay (s): 0.2", "# Initial settle delay (s): 2",
                "# Columns: set current in A, measured voltage in V, resistance V/I in Ohm"):
        assert key in lines, key
    assert lines[lines.index("# Resistance is NaN where the set current is 0 A") + 1] ==         "Set Current (A),Measured Voltage (V),Resistance (Ohm)"
    assert all(ord(ch) < 128 for l in lines if l.startswith("#") for ch in l), "header must be ASCII"
    rows = [l for l in lines if l and not l.startswith("#")][1:]
    assert rows[0].split(",")[2] == "nan", rows[0]
    assert "inf" not in rows[0]
    assert math.isclose(float(rows[1].split(",")[2]), 2000.0)
    assert math.isnan(gui.data_storage['resistance'][0])


def test_header_sweep_type_label_is_ascii():
    assert M.ascii_label(M.SWEEP_LINEAR) == "Start -> Stop (linear)"
    assert M.ascii_label("Custom Current List (µA)") == "Custom Current List (uA)"


# ---------------------------------------------------------------------------
# PICA plotter (utils/PlotterUtil_GUI.py) must read the file as written
# ---------------------------------------------------------------------------

def test_data_file_loads_in_the_pica_plotter():
    plotter_mod = _load("plotter_for_k6221_test",
                        os.path.join(REPO_ROOT, "pica", "utils", "PlotterUtil_GUI.py"))
    cls = next(getattr(plotter_mod, n) for n in dir(plotter_mod)
               if isinstance(getattr(plotter_mod, n), type)
               and hasattr(getattr(plotter_mod, n), "_read_data_from_file"))
    plotter = object.__new__(cls)          # the reader needs no Tk widgets
    cases = ((M.SWEEP_CUSTOM, dict(custom="0, 1, 2, 5, 2, 1, 0")),
             (M.SWEEP_LINEAR, dict(start="-10", stop="10", points="5")))
    with tempfile.TemporaryDirectory() as d:
        for sweep_type, kw in cases:
            gui = _gui(sweep_type, **kw)
            params, pts = gui._collect_params()
            gui.data_filepath = os.path.join(d, "f.dat")
            gui._write_file_header(params, len(pts))
            for c in pts:
                v = 1000.0 * c
                gui._append_row(float(c), v, v / c if c else float('nan'))
            headers, delim, arr = plotter._read_data_from_file(gui.data_filepath)
            assert headers == ["Set Current (A)", "Measured Voltage (V)", "Resistance (Ohm)"]
            assert delim == ","
            assert arr.shape == (len(pts), 3)
            assert np.allclose(arr[:, 0], pts)
            zero = arr[:, 0] == 0
            assert np.all(np.isnan(arr[zero, 2])) and np.all(np.isfinite(arr[~zero, 2]))


if __name__ == "__main__":
    failures = 0
    for name in [n for n in list(globals()) if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: {e!r}")
    print("ALL PASSED" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
