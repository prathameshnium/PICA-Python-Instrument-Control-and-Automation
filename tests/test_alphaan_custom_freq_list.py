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
