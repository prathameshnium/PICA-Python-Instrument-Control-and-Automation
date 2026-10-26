"""
Offline checks for the Alpha-AN custom frequency list, edition labels and
the REF calibration note, in BOTH editions (32-bit raw GPIB and VISA).

No instrument, no Tk window: the GUI methods run on bare objects with
stand-in widgets. Runs under pytest or as plain python.
"""
import importlib.util
import os
import sys
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOVO_DIR = os.path.join(REPO_ROOT, "pica", "novocontrol")
EDITIONS = {
    "32-bit": os.path.join(NOVO_DIR, "Frequency_Scan_AlphaAN_32bit_GUI.py"),
    "64-bit": os.path.join(NOVO_DIR, "Frequency_Scan_AlphaAN_GUI.py"),
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULES = {bits: _load(f"alphaan_custom_{bits[:2]}", path)
           for bits, path in EDITIONS.items()}


class _Box:
    """Stand-in for a ttk.Combobox / tk.Text: get() returns fixed text."""

    def __init__(self, value):
        self.value = value

    def get(self, *_args):
        return self.value


def _bare_gui(alpha, choice, text=""):
    gui = object.__new__(alpha.AlphaAN_FreqScan_GUI)
    gui.freq_combobox = _Box(choice)
    gui.custom_freq_text = _Box(text + "\n")
    return gui


def _raises(func, *args):
    try:
        func(*args)
    except ValueError as exc:
        return str(exc)
    raise AssertionError(f"{func.__name__}{args} did not raise ValueError")


def test_parser_accepts_commas_and_pasted_layouts():
    for alpha in MODULES.values():
        assert alpha.parse_frequency_list("20, 100,1e3 ,1E6") == (
            20.0, 100.0, 1000.0, 1e6)
        assert alpha.parse_frequency_list("20;100\n1000\t5e4") == (
            20.0, 100.0, 1000.0, 5e4)


def test_parser_keeps_the_typed_order():
    for alpha in MODULES.values():
        assert alpha.parse_frequency_list("1e6, 20, 1000") == (1e6, 20.0, 1e3)


def test_parser_refuses_anything_outside_the_alpha_span():
    for alpha in MODULES.values():
        over = alpha.FREQ_HW_MAX * 1.01
        msg = _raises(alpha.parse_frequency_list, f"20, {over}")
        assert "outside the Alpha-AN range" in msg
        assert "outside" in _raises(alpha.parse_frequency_list, "0, 20")
        assert "outside" in _raises(alpha.parse_frequency_list, "-50")
        # The exact limits themselves are allowed.
        assert alpha.parse_frequency_list(
            f"{alpha.FREQ_HW_MIN}, {alpha.FREQ_HW_MAX}") == (
            alpha.FREQ_HW_MIN, alpha.FREQ_HW_MAX)


def test_parser_refuses_empty_junk_and_non_finite():
    for alpha in MODULES.values():
        assert "empty" in _raises(alpha.parse_frequency_list, "  ,, \n")
        assert "'1kHz' is not a number" in _raises(
            alpha.parse_frequency_list, "20, 1kHz")
        assert "not a finite" in _raises(alpha.parse_frequency_list, "nan")
        assert "not a finite" in _raises(alpha.parse_frequency_list, "inf")


def test_parser_caps_the_point_count():
    for alpha in MODULES.values():
        too_many = ", ".join(["100"] * (alpha.CUSTOM_FREQ_MAX_POINTS + 1))
        assert "limit" in _raises(alpha.parse_frequency_list, too_many)


def test_custom_label_is_not_a_preset():
    for alpha in MODULES.values():
        assert alpha.CUSTOM_FREQ_LABEL not in alpha.FREQ_PRESETS


def test_selected_frequencies_uses_the_preset_when_chosen():
    for alpha in MODULES.values():
        gui = _bare_gui(alpha, alpha.DEFAULT_FREQ_PRESET, "junk")
        label, freqs = gui._selected_frequencies()
        assert label == alpha.DEFAULT_FREQ_PRESET
        assert freqs == alpha.FREQ_PRESETS[alpha.DEFAULT_FREQ_PRESET]


def test_selected_frequencies_parses_the_custom_box():
    for alpha in MODULES.values():
        gui = _bare_gui(alpha, alpha.CUSTOM_FREQ_LABEL, "20, 1000, 1e5")
        label, freqs = gui._selected_frequencies()
        assert label == "Custom list (3 pts)"
        assert label.isascii(), "goes into the .dat header"
        assert freqs == (20.0, 1000.0, 1e5)


def test_custom_list_still_meets_the_acv_ceiling():
    # A custom list reaching above 4 MHz lowers the AC ceiling to 2 Vrms;
    # validate_parameters must still refuse 3 Vrms for it.
    for alpha in MODULES.values():
        params = {
            "frequencies": alpha.parse_frequency_list("1000, 5e6"),
            "acv": 3.0, "mtm": 0.5, "wire_mode": "2",
            "geometry_mode": "area", "area_mm2": 100.0, "thickness_mm": 1.0,
            "delay": 1.0,
        }
        assert "safety limit" in _raises(alpha.validate_parameters, params)


def test_each_edition_names_its_bitness_in_the_window():
    for bits, path in EDITIONS.items():
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        assert f'Frequency Scan - {bits} (' in source, bits
        assert f"[{bits} edition," in source, bits


def test_ref_note_explains_and_cancel_sends_nothing():
    for alpha in MODULES.values():
        gui = object.__new__(alpha.AlphaAN_FreqScan_GUI)
        gui.is_running = gui.is_calibrating = False
        gui._selected_visa_address = lambda: "GPIB0::10::INSTR"
        with patch.object(alpha.messagebox, "askokcancel",
                          return_value=False) as ask:
            gui.run_reference_calibration()
        note = ask.call_args.args[1]
        for phrase in ("REF (reference) calibration", "GPIB0::10::INSTR",
                       "replacing the previous", "DISCONNECT",
                       "reconnect the sample", "Cancel"):
            assert phrase in note, phrase
        assert gui.is_calibrating is False
        assert not hasattr(gui, "cal_thread"), "Cancel must start nothing"


def test_e4980a_preset_is_offered_but_115_pt_stays_default():
    e4980a = tuple(float(f) for f in (
        *range(40, 1000, 10), *range(1000, 10000, 100),
        *range(10000, 100000, 1000), *range(100000, 1000000, 10000),
        *range(1000000, 2000001, 100000)))
    for alpha in MODULES.values():
        label = "377 pts, 40 Hz - 2 MHz (E4980A list)"
        assert alpha.FREQ_PRESETS[label] == e4980a
        assert len(e4980a) == 377
        assert alpha.DEFAULT_FREQ_PRESET == "115 pts, 20 Hz - 1 MHz (x1.1)"
        assert next(iter(alpha.FREQ_PRESETS)) == alpha.DEFAULT_FREQ_PRESET
        # Below 4 MHz throughout, so the full 3 Vrms stays allowed.
        assert max(e4980a) < alpha.ACV_FREQ_MID_HZ


# --- Start / End / Factor list (64-bit edition only, for now) ---------------

# Today's WinDETA Fscan list (mes_def LIST ORDERING, 26 Oct 2026):
# 20 Hz x1.4, End 3 MHz appended after 2.603222e6.
WINDETA_37PT_3MHZ = (
    2.000000e+01, 2.800000e+01, 3.920000e+01, 5.488000e+01, 7.683200e+01,
    1.075648e+02, 1.505907e+02, 2.108270e+02, 2.951578e+02, 4.132209e+02,
    5.785093e+02, 8.099130e+02, 1.133878e+03, 1.587430e+03, 2.222401e+03,
    3.111362e+03, 4.355907e+03, 6.098269e+03, 8.537577e+03, 1.195261e+04,
    1.673365e+04, 2.342711e+04, 3.279796e+04, 4.591714e+04, 6.428399e+04,
    8.999759e+04, 1.259966e+05, 1.763953e+05, 2.469534e+05, 3.457347e+05,
    4.840286e+05, 6.776401e+05, 9.486961e+05, 1.328175e+06, 1.859444e+06,
    2.603222e+06, 3.000000e+06,
)


def test_log_builder_reproduces_the_windeta_list_and_frozen_presets():
    alpha = MODULES["64-bit"]
    build = alpha.build_log_frequency_list
    assert build(20, 3e6, 1.4) == WINDETA_37PT_3MHZ
    assert build(20, 1e7, 1.4) == alpha.FREQUENCIES_40PT_10MHZ
    assert build(20, 1e6, 1.1) == alpha.FREQUENCIES_115PT_1MHZ


def test_log_builder_end_landing_on_the_series_is_not_duplicated():
    build = MODULES["64-bit"].build_log_frequency_list
    assert build(20, 2e6, 10) == (20.0, 200.0, 2e3, 2e4, 2e5, 2e6)


def test_log_builder_descends_when_start_is_above_end():
    build = MODULES["64-bit"].build_log_frequency_list
    assert build(3e6, 1e6, 1.4) == (3e6, 2.142857e6, 1.530612e6,
                                    1.093294e6, 1e6)


def test_log_builder_refuses_bad_inputs():
    alpha = MODULES["64-bit"]
    build = alpha.build_log_frequency_list
    assert "greater than 1" in _raises(build, 20, 3e6, 1.0)
    assert "same frequency" in _raises(build, 20, 20, 1.4)
    assert "outside" in _raises(build, 20, alpha.FREQ_HW_MAX * 2, 1.4)
    assert "finite" in _raises(build, float("nan"), 3e6, 1.4)
    assert "more than" in _raises(build, 20, 3e6, 1.001)
    assert "too close to 1" in _raises(build, 20, 20.00002, 1.0000002)


def test_selected_frequencies_builds_the_start_end_factor_list():
    alpha = MODULES["64-bit"]
    gui = _bare_gui(alpha, alpha.LOG_FREQ_LABEL)
    gui.log_freq_entries = {"start": _Box(" 20"), "end": _Box("3e6"),
                            "factor": _Box("1.4 ")}
    label, freqs = gui._selected_frequencies()
    assert freqs == WINDETA_37PT_3MHZ
    assert label == "37 pts, 20 Hz - 3e+06 Hz (x1.4)" and label.isascii()
    gui.log_freq_entries["factor"] = _Box("x")
    assert "Factor: 'x' is not a number" in _raises(gui._selected_frequencies)


def test_start_end_factor_is_the_default_choice():
    alpha = MODULES["64-bit"]
    assert alpha.DEFAULT_FREQ_CHOICE == alpha.LOG_FREQ_LABEL
    assert alpha.LOG_FREQ_DEFAULTS == ("20", "3e6", "1.4")


def test_hint_lists_every_point_or_shortens_until_clicked():
    alpha = MODULES["64-bit"]
    gui = object.__new__(alpha.AlphaAN_FreqScan_GUI)
    gui.hint_show_all = False
    assert "3e+06 Hz" in gui._point_list_text(WINDETA_37PT_3MHZ)
    long_list = alpha.FREQUENCIES_115PT_1MHZ
    short = gui._point_list_text(long_list)
    assert short.startswith("115 points:") and "click to show all" in short
    gui.hint_show_all = True
    assert len(gui._point_list_text(long_list).split(", ")) == 115


if __name__ == "__main__":
    failures = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            else:
                print(f"ok   {name}")
    sys.exit(1 if failures else 0)
