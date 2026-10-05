"""List Maker output -> "Custom List" input of the I-V modules.

pica/utils/List_Maker_GUI.py renders a list with format_values(): fixed
decimals, scientific or whole numbers, joined by one of six separators
(comma, comma+space, newline, semicolon, space, tab), copied to the
clipboard or saved as .txt. The Custom List box of IV_K2400_GUI.py (uA),
IV_K6517B_GUI.py (V) and IV_K2400_K2182_GUI.py (mA) must take every one of
those renderings back unchanged.

Checked here, for every spacing x pattern x separator x number format the
List Maker offers:
  - parse_custom_list() returns the List Maker's values (to the rendered
    precision), in order, including the deliberately repeated turning
    points of the loop / hysteresis patterns and the exact 0 of
    "Log, dense near 0";
  - build_sweep_points(SWEEP_CUSTOM) keeps every point (count and order);
  - the GUI-level validation (_collect_params / _validate_and_get_params)
    accepts the pasted text, scales it to A / V, and still refuses a list
    above the instrument limit;
  - a list saved by save_txt() (ASCII, trailing newline) reads back the
    same way; and, when a Tk root is available, the real clipboard path:
    List Maker "Copy" -> paste into the I-V custom box -> Start validation.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import itertools
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

PATHS = {
    "listmaker": os.path.join(REPO_ROOT, "pica", "utils", "List_Maker_GUI.py"),
    "k2400": os.path.join(REPO_ROOT, "pica", "keithley", "k2400", "IV_K2400_GUI.py"),
    "k2182": os.path.join(REPO_ROOT, "pica", "keithley", "k2400_2182", "IV_K2400_K2182_GUI.py"),
    "k6517b": os.path.join(REPO_ROOT, "pica", "keithley", "k6517b", "High_Resistance",
                           "IV_K6517B_GUI.py"),
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LM = _load("listmaker_for_iv_test", PATHS["listmaker"])
IV = {k: _load(f"iv_{k}_for_listmaker_test", PATHS[k]) for k in ("k2400", "k2182", "k6517b")}

try:
    import pytest
except ImportError:
    pytest = None

# Every rendering the List Maker can produce.
SEPARATORS = list(LM.SEPARATORS.values())                      # ",", ", ", "\n", ";", " ", "\t"
NUMBER_FORMATS = [
    dict(decimals=2, scientific=False, integers=False),
    dict(decimals=0, scientific=False, integers=False),
    dict(decimals=4, scientific=True, integers=False),
    dict(decimals=2, scientific=False, integers=True),
]
# (spacing, start, stop, kwargs) covering every spacing the List Maker has.
BASES = [
    ("linear", 0, 100, dict(n=11)),
    ("linear", -50, 50, dict(step=12.5)),
    ("log", 1, 1000, dict(step=3)),
    ("symlog", -10, 10, dict(n=9, near_zero=0.1)),
    ("recip", 10, 100, dict(n=6)),
]
PATTERNS = list(LM.PATTERN_KEYS.values())                      # none, loop, sawtooth, ...


def _render(values, fmt, sep):
    return LM.format_values(values, decimals=fmt["decimals"], scientific=fmt["scientific"],
                            separator=sep, integers=fmt["integers"] and not fmt["scientific"])


def _expected(values, fmt):
    """What the rendered text encodes: the values at the printed precision."""
    out = []
    for v in values:
        if fmt["integers"] and not fmt["scientific"]:
            out.append(float(int(LM.round_half_away(v))))
        elif fmt["scientific"]:
            out.append(float(f"{v:.{fmt['decimals']}e}"))
        else:
            out.append(float(f"{v:.{fmt['decimals']}f}"))
    return out


def _lists():
    """Every List Maker list used by the tests: (label, values)."""
    for spacing, a, b, kw in BASES:
        base = LM.base_list(a, b, spacing, **kw)
        for pattern in PATTERNS:
            if pattern in ("hyst4", "hyst5") and spacing in ("log", "recip"):
                vals = LM.apply_pattern(base, pattern, cycles=2)
            else:
                vals = LM.apply_pattern(base, pattern, cycles=2)
            yield f"{spacing} {a}..{b} {pattern}", vals
    yield "integers rounded", LM.round_integers(LM.base_list(0, 10, "linear", n=7))


# ---------------------------------------------------------------------------
# Parser fidelity for every rendering
# ---------------------------------------------------------------------------

def test_every_rendering_parses_back_in_every_iv_module():
    n_cases = 0
    for (label, vals), fmt, sep in itertools.product(_lists(), NUMBER_FORMATS, SEPARATORS):
        text = _render(vals, fmt, sep)
        want = _expected(vals, fmt)
        for key, m in IV.items():
            got = m.parse_custom_list(text)
            assert len(got) == len(want), (key, label, fmt, repr(sep), len(got), len(want))
            assert got == want, (key, label, fmt, repr(sep))
            pts = m.build_sweep_points(m.SWEEP_CUSTOM, custom_values=got)
            assert list(pts) == want
            n_cases += 1
    assert n_cases > 500


def test_repeated_turning_points_and_exact_zero_survive():
    base = LM.base_list(-10, 10, "symlog", n=9, near_zero=0.1)
    assert 0.0 in base, "symlog base must carry an exact 0"
    loop = LM.apply_pattern(base, "loop", cycles=1, repeat_turning_points=True)
    assert loop[len(base) - 1] == loop[len(base)] == 10.0, "turning point repeated by the List Maker"
    for m in IV.values():
        got = m.parse_custom_list(_render(loop, NUMBER_FORMATS[0], ", "))
        assert got.count(10.0) == 2, "the IV module must keep the repeated hold point"
        assert got.count(0.0) >= 2
        assert got == [float(f"{v:.2f}") for v in loop]


def test_minus_zero_is_dropped_by_list_maker_and_parsed_as_zero():
    text = LM.format_values([-0.0001, 0.0001, -5.0], decimals=2, separator=",")
    assert text == "0.00,0.00,-5.00"
    for m in IV.values():
        assert m.parse_custom_list(text) == [0.0, 0.0, -5.0]


def test_long_list_near_the_list_maker_limit():
    vals = LM.base_list(0, 100, "linear", n=5000)
    text = LM.format_values(vals, decimals=3, separator="\n")
    for m in IV.values():
        got = m.parse_custom_list(text)
        assert len(got) == 5000 and got[0] == 0.0 and got[-1] == 100.0


# ---------------------------------------------------------------------------
# Saved .txt round trip (save_txt writes ASCII with a trailing newline)
# ---------------------------------------------------------------------------

def test_saved_txt_reads_back():
    vals = LM.apply_pattern(LM.base_list(0, 50, "linear", step=10), "hyst5", cycles=1)
    for sep in SEPARATORS:
        text = LM.format_values(vals, decimals=1, separator=sep)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "list.txt")
            with open(path, "w", encoding="ascii", newline="") as fh:   # as save_txt() does
                fh.write(text)
                if not text.endswith("\n"):
                    fh.write("\n")
            with open(path, encoding="ascii") as fh:
                pasted = fh.read()
        for m in IV.values():
            assert m.parse_custom_list(pasted) == [float(f"{v:.1f}") for v in vals], repr(sep)


# ---------------------------------------------------------------------------
# GUI-level validation with the pasted text
# ---------------------------------------------------------------------------

class _Entry:
    def __init__(self, text):
        self.text = text

    def get(self, *a):
        return self.text


def _k2400_gui(custom):
    m = IV["k2400"]
    gui = object.__new__(m.MeasurementAppGUI)
    gui.sweep_type_var = _Entry(m.SWEEP_CUSTOM)
    gui.entries = {k: _Entry(v) for k, v in {"Sample Name": "LM", "Num Loops": "1",
                                              "Compliance": "10", "Delay": "0.1",
                                              "Max Current": "", "Step Current": ""}.items()}
    gui.keithley_combobox = _Entry("GPIB0::24::INSTR")
    gui.custom_list_text = _Entry(custom)
    gui.file_location_path = tempfile.gettempdir()
    return gui


def _k6517b_gui(custom):
    m = IV["k6517b"]
    gui = object.__new__(m.HighResistanceIV_GUI)
    gui.sweep_type_var = _Entry(m.SWEEP_CUSTOM)
    gui.entries = {k: _Entry(v) for k, v in {"Sample Name": "LM", "Loops": "1", "Delay (s)": "1",
                                              "Start V": "", "Stop V": "", "Steps": "",
                                              "Max V": "", "Step V": ""}.items()}
    gui.keithley_combobox = _Entry("GPIB1::27::INSTR")
    gui.custom_list_text = _Entry(custom)
    gui.file_location_path = tempfile.gettempdir()
    return gui


def _k2182_gui(custom):
    m = IV["k2182"]
    gui = object.__new__(m.IV_GUI)
    gui.sweep_type_var = _Entry(m.SWEEP_CUSTOM)
    gui.entries = {k: _Entry(v) for k, v in {"Sample Name": "LM", "Save Location": tempfile.gettempdir(),
                                              "Loops": "1", "Compliance (V)": "10", "Dwell Time (s)": "0.5",
                                              "Start Current (mA)": "", "Stop Current (mA)": "",
                                              "Max Current (mA)": "", "Step Current (mA)": ""}.items()}
    gui.k2400_cb = _Entry("GPIB0::24::INSTR")
    gui.k2182_cb = _Entry("GPIB0::7::INSTR")
    gui.custom_list_text = _Entry(custom)
    return gui


def test_k2400_accepts_a_list_maker_hysteresis_in_micro_amps():
    vals = LM.apply_pattern(LM.base_list(0, 100, "linear", step=25), "hyst5", cycles=1)
    text = LM.format_values(vals, decimals=2, separator=", ")
    params, pts = _k2400_gui(text)._collect_params()
    assert np.allclose(pts, np.array(vals) * 1e-6)
    assert math.isclose(params['max_abs_current_A'], 100e-6)
    assert params['sweep_type'] == IV["k2400"].SWEEP_CUSTOM


def test_k2400_refuses_a_list_maker_list_above_the_source_limit():
    text = LM.format_values(LM.base_list(0, 2e6, "linear", n=5), decimals=0, separator=",")  # 2 A in uA
    try:
        _k2400_gui(text)._collect_params()
    except ValueError as e:
        assert "limit" in str(e)
    else:
        raise AssertionError("2 A sweep accepted")


def test_k6517b_accepts_list_maker_log_and_symlog_volts():
    for vals in (LM.base_list(1, 1000, "log", step=2),
                 LM.apply_pattern(LM.base_list(-500, 500, "symlog", n=11, near_zero=1), "loop", cycles=1)):
        for sep in ("\n", ";", "\t"):
            text = LM.format_values(vals, decimals=3, separator=sep)
            params, pts = _k6517b_gui(text)._collect_params()
            assert len(pts) == len(vals)
            assert np.allclose(pts, [float(f"{v:.3f}") for v in vals])
            assert params['max_abs_voltage'] <= 1000
            rng = IV["k6517b"].source_range_for(params['max_abs_voltage'])
            assert rng == (1000 if params['max_abs_voltage'] > 100 else 100)


def test_k6517b_refuses_a_list_maker_list_above_1000_v():
    text = LM.format_values(LM.base_list(0, 1500, "linear", n=4), decimals=1, separator=",")
    try:
        _k6517b_gui(text)._collect_params()
    except ValueError as e:
        assert "limit" in str(e)
    else:
        raise AssertionError("1500 V sweep accepted")


def test_k2182_accepts_a_list_maker_list_in_milli_amps():
    vals = LM.apply_pattern(LM.base_list(0, 5, "linear", n=6), "hyst4", cycles=1)
    text = LM.format_values(vals, decimals=4, scientific=True, separator=" ")
    params, pts = _k2182_gui(text)._validate_and_get_params()
    assert np.allclose(pts, np.array(vals) * 1e-3)
    assert math.isclose(params['max_abs_current_A'], 5e-3)


def test_list_maker_parse_and_iv_parse_agree_on_iv_examples():
    """The List Maker's own "Check a list" parser reads the I-V example text
    the same way, so a list can travel in either direction."""
    for m in IV.values():
        assert LM.parse_list(m.CUSTOM_LIST_EXAMPLE) == m.parse_custom_list(m.CUSTOM_LIST_EXAMPLE)


# ---------------------------------------------------------------------------
# Real clipboard: List Maker "Copy" -> paste into the I-V custom box
# ---------------------------------------------------------------------------

def test_clipboard_copy_paste_between_the_two_windows():
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        if pytest is not None:
            pytest.skip(f"Tk root unavailable: {e}")
        print(f"SKIP clipboard check (no Tk: {e})")
        return
    root.withdraw()
    try:
        maker = LM.ListMakerApp(root) if hasattr(LM, "ListMakerApp") else None
        if maker is None:
            # find the GUI class generically
            for name in dir(LM):
                obj = getattr(LM, name)
                if isinstance(obj, type) and name.endswith(("App", "GUI")) and name != "ListError":
                    maker = obj(root)
                    break
        assert maker is not None, "List Maker GUI class not found"
        maker.start_var.set("0")
        maker.stop_var.set("40")
        maker.define_by_var.set("points")
        maker.points_var.set("5")
        maker.pattern_var.set(LM.PATTERNS[6])          # hysteresis, 5 quadrants
        maker.sep_var.set("comma and space")
        maker.recompute()
        root.update()
        assert maker.values, maker.error_var.get()
        maker.copy_to_clipboard()
        root.update()
        pasted = root.clipboard_get()

        for key, m in IV.items():
            got = m.parse_custom_list(pasted)
            assert got == [float(f"{v:.2f}") for v in maker.values], key
        # and through the real custom box of the K2400 and 6517B windows
        for key, cls in (("k2400", "MeasurementAppGUI"), ("k6517b", "HighResistanceIV_GUI")):
            m = IV[key]
            win = tk.Toplevel(root)
            win.withdraw()
            app = getattr(m, cls)(win)
            app.sweep_type_var.set(m.SWEEP_CUSTOM)
            app._on_sweep_type_change()
            app.custom_list_text.delete("1.0", tk.END)
            app.custom_list_text.insert("1.0", pasted)
            app.entries["Sample Name"].insert(0, "LM")
            if key == "k2400":
                app.entries["Compliance"].insert(0, "10")   # no default in the window
            app.file_location_path = tempfile.gettempdir()
            app.keithley_combobox['values'] = ("GPIB0::1::INSTR",)
            app.keithley_combobox.set("GPIB0::1::INSTR")
            params, pts = app._collect_params()
            scale = 1e-6 if key == "k2400" else 1.0
            assert np.allclose(pts, np.array([float(f"{v:.2f}") for v in maker.values]) * scale), key
            win.destroy()
    finally:
        root.destroy()


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
