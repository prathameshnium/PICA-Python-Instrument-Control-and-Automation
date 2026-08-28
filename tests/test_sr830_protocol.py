"""Tests for the SR830 lock-in communication module (GUI and headless).

The code tables in this module are transcriptions from the SR830 manual
chapter 5. A transcription error there does not raise anything -- it silently
labels a setting wrong in the log, or sends a code that means something else.
So the tables are pinned here against the manual's own numbers, together with
the limits the module refuses to exceed and the identity check that stops
SLVL (sine amplitude into the sample) and AUXV (up to +/-10.5 V on the rear
outputs) reaching an instrument that is not an SR830.

Runnable as plain Python as well as under pytest.
"""

import ast
import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

GUI_PATH = os.path.join(REPO_ROOT, "pica", "lockin", "sr830",
                        "Comms_SR830_GUI.py")
CLI_PATH = os.path.join(REPO_ROOT, "pica", "lockin", "sr830",
                        "Instrument_Control",
                        "Comms_SR830_Instrument_Control.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gui = _load("sr830_gui_under_test", GUI_PATH)
cli = _load("sr830_cli_under_test", CLI_PATH)
EDITIONS = (("GUI", gui), ("CLI", cli))

GUI_SOURCE = open(GUI_PATH, encoding="utf-8").read()
CLI_SOURCE = open(CLI_PATH, encoding="utf-8").read()

SR830_IDN = "Stanford_Research_Systems,SR830,s/n12345,ver1.07"


class FakeLockin:
    def __init__(self, idn=SR830_IDN, answers=None):
        self.answers = {"*IDN?": idn}
        self.answers.update(answers or {})
        self.writes = []
        self.queries = []
        self.closed = False
        self.read_termination = None
        self.write_termination = None
        self.timeout = None

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        if command in self.answers:
            return self.answers[command]
        raise IOError(f"unexpected query {command!r}")

    def close(self):
        self.closed = True


class _PatchVisa:
    def __init__(self, module, lockin):
        self.module = module
        self.lockin = lockin

    def __enter__(self):
        self._old = self.module.pyvisa
        self._old_flag = self.module.PYVISA_AVAILABLE
        lockin = self.lockin

        class _FakeRM:
            def open_resource(self, address):
                return lockin

        class _FakeVisa:
            ResourceManager = staticmethod(_FakeRM)

        self.module.pyvisa = _FakeVisa
        self.module.PYVISA_AVAILABLE = True
        return lockin

    def __exit__(self, *exc):
        self.module.pyvisa = self._old
        self.module.PYVISA_AVAILABLE = self._old_flag
        return False


def _backend(module, lockin):
    with _PatchVisa(module, lockin):
        return module.SR830Backend("GPIB0::8::INSTR")


# ------------------------------------------- code tables against the manual

def test_reference_and_input_tables_match_the_manual():
    for label, m in EDITIONS:
        assert m.FMOD_LABELS == ["External", "Internal"], label
        assert m.RSLP_LABELS == ["Sine zero crossing", "TTL rising edge",
                                 "TTL falling edge"], label
        assert m.ICPL_LABELS == ["AC", "DC"], label
        assert m.IGND_LABELS == ["Float", "Ground"], label
        assert len(m.ISRC_LABELS) == 4, label
        assert m.ISRC_LABELS[0] == "A" and m.ISRC_LABELS[1] == "A-B", label
        assert len(m.ILIN_LABELS) == 4, label


def test_sensitivity_table_has_all_27_codes_from_2nv_to_1v():
    for label, m in EDITIONS:
        assert len(m.SENS_LABELS) == 27, label
        assert m.SENS_LABELS[0] == "2 nV/fA", label
        assert m.SENS_LABELS[26] == "1 V/uA", label


def test_time_constant_table_has_all_20_codes_from_10us_to_30ks():
    for label, m in EDITIONS:
        assert len(m.OFLT_LABELS) == 20, label
        assert m.OFLT_LABELS[0] == "10 us", label
        assert m.OFLT_LABELS[19] == "30 ks", label
        # 300 ms is code 9; getting this wrong mislabels every log header.
        assert m.OFLT_LABELS[9] == "300 ms", label


def test_filter_slope_table_is_6_12_18_24_db_per_octave():
    for label, m in EDITIONS:
        assert m.OFSL_DB == [6, 12, 18, 24], label
        assert len(m.OFSL_LABELS) == 4, label


def test_reserve_mode_order_is_high_normal_low():
    for label, m in EDITIONS:
        assert m.RMOD_LABELS == ["High Reserve", "Normal", "Low Noise"], label


def test_status_byte_tables_are_eight_bits_each():
    for label, m in EDITIONS:
        assert len(m.LIA_STATUS_BITS) == 8, label
        assert len(m.ERROR_STATUS_BITS) == 8, label
        assert "Reference unlock" in m.LIA_STATUS_BITS, label
        assert "GPIB error" in m.ERROR_STATUS_BITS, label


def test_the_two_editions_ship_identical_tables():
    for name in ("FMOD_LABELS", "RSLP_LABELS", "ISRC_LABELS", "ICPL_LABELS",
                 "IGND_LABELS", "ILIN_LABELS", "SYNC_LABELS", "SENS_LABELS",
                 "OFLT_LABELS", "OFSL_LABELS", "OFSL_DB", "RMOD_LABELS",
                 "LIA_STATUS_BITS", "ERROR_STATUS_BITS", "SETTINGS_QUERIES"):
        assert getattr(gui, name) == getattr(cli, name), name


def test_hardware_limits_match_the_manual():
    for label, m in EDITIONS:
        assert (m.FREQ_MIN, m.FREQ_MAX) == (0.001, 102000.0), label
        assert (m.SLVL_MIN, m.SLVL_MAX) == (0.004, 5.0), label
        assert (m.PHAS_MIN, m.PHAS_MAX) == (-360.0, 729.99), label
        assert (m.HARM_MIN, m.HARM_MAX) == (1, 19999), label
        assert (m.AUXV_MIN, m.AUXV_MAX) == (-10.5, 10.5), label


def test_every_settings_query_ends_in_a_question_mark():
    for label, m in EDITIONS:
        for key, command in m.SETTINGS_QUERIES:
            assert command.endswith("?"), (label, command)


def test_every_settable_key_has_a_matching_query():
    """A key that can be set but not read back cannot be verified."""
    for label, m in EDITIONS:
        queried = {key for key, _q in m.SETTINGS_QUERIES}
        for key in m.SETTABLE:
            if key.startswith("auxv"):
                continue     # AUXV is an output, not part of the state dump
            assert key in queried, (label, key)


# ------------------------------------------------------- command building

def test_an_enum_setting_becomes_mnemonic_plus_code():
    for label, m in EDITIONS:
        command, text = m.build_set_command("oflt", 9)
        assert command == "OFLT 9", label
        assert "300 ms" in text, label


def test_a_float_setting_is_formatted_not_stringified():
    for label, m in EDITIONS:
        command, _text = m.build_set_command("freq", 1000)
        assert command.startswith("FREQ "), label
        assert float(command.split()[1]) == 1000.0, label


def test_aux_outputs_use_the_comma_form_the_manual_requires():
    """AUXV takes 'AUXV i,x' -- a space instead of the comma is a syntax error."""
    for label, m in EDITIONS:
        command, _text = m.build_set_command("auxv2", -1.5)
        assert command.startswith("AUXV 2,"), (label, command)
        assert float(command.split(",")[1]) == -1.5, label


def test_out_of_range_values_are_refused_before_transmission():
    bad = [
        ("freq", 0.0), ("freq", 200000),
        ("slvl", 0.0), ("slvl", 5.1),
        ("phas", -361), ("phas", 730),
        ("harm", 0), ("harm", 20000),
        ("auxv1", -10.6), ("auxv1", 10.6),
        ("sens", -1), ("sens", 27),
        ("oflt", 20), ("ofsl", 4), ("rmod", 3),
        ("fmod", 2), ("icpl", 2), ("ignd", 2), ("ilin", 4),
    ]
    for label, m in EDITIONS:
        for key, value in bad:
            try:
                m.build_set_command(key, value)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{label}: {key}={value} was accepted")


def test_boundary_values_are_accepted():
    for label, m in EDITIONS:
        for key, value in (("slvl", 0.004), ("slvl", 5.0),
                           ("freq", 0.001), ("freq", 102000.0),
                           ("phas", -360.0), ("phas", 729.99),
                           ("harm", 1), ("harm", 19999),
                           ("auxv1", -10.5), ("auxv1", 10.5),
                           ("sens", 0), ("sens", 26), ("oflt", 19)):
            m.build_set_command(key, value)   # must not raise


def test_an_unknown_key_is_refused_with_the_list_of_known_ones():
    for label, m in EDITIONS:
        try:
            m.build_set_command("volume", 11)
        except ValueError as exc:
            assert "slvl" in str(exc), label
        else:
            raise AssertionError(f"{label}: an unknown key was accepted")


def test_non_numeric_input_is_refused_not_passed_through():
    for label, m in EDITIONS:
        for key in ("freq", "harm", "sens"):
            try:
                m.build_set_command(key, "loud")
            except ValueError:
                pass
            else:
                raise AssertionError(f"{label}: {key}='loud' was accepted")


def test_decode_status_byte_names_the_set_bits():
    for label, m in EDITIONS:
        assert m.decode_status_byte(0, m.LIA_STATUS_BITS) == ["none"], label
        assert m.decode_status_byte(1, m.LIA_STATUS_BITS) == \
            [m.LIA_STATUS_BITS[0]], label
        assert len(m.decode_status_byte(0xFF, m.LIA_STATUS_BITS)) == 8, label


# ---------------------------------------------------------- identity guard

def test_the_backend_refuses_anything_that_is_not_an_sr830():
    for label, m in EDITIONS:
        lockin = FakeLockin(idn="KEITHLEY INSTRUMENTS INC.,MODEL 2400,1,C32")
        with _PatchVisa(m, lockin):
            try:
                m.SR830Backend("GPIB0::8::INSTR")
            except ConnectionError as exc:
                assert "not an SR830" in str(exc), label
            else:
                raise AssertionError(f"{label}: drove a 2400 as a lock-in")
        assert lockin.closed is True, label
        # OUTX is the only thing sent before the check, and it is harmless.
        assert lockin.writes == ["OUTX 1"], (label, lockin.writes)


def test_a_real_sr830_is_accepted():
    for label, m in EDITIONS:
        lockin = FakeLockin()
        backend = _backend(m, lockin)
        assert backend.idn == SR830_IDN, label


def test_is_sr830_idn_is_case_insensitive_and_safe_on_junk():
    for label, m in EDITIONS:
        assert m.is_sr830_idn("stanford_research_systems,sr830,1,1"), label
        assert not m.is_sr830_idn(""), label
        assert not m.is_sr830_idn(None), label
        assert not m.is_sr830_idn("Stanford_Research_Systems,SR860,1,1"), label


def test_outx_1_goes_out_before_the_first_query():
    """The SR830 answers on ONE interface; without OUTX 1 queries time out."""
    for label, m in EDITIONS:
        lockin = FakeLockin()
        _backend(m, lockin)
        assert lockin.writes[0] == "OUTX 1", label
        assert lockin.queries[0] == "*IDN?", label


# --------------------------------------------------------- data transfer

def test_snap_takes_all_four_values_in_one_query():
    """Four separate OUTP? calls would sample four different moments."""
    for label, m in EDITIONS:
        lockin = FakeLockin(answers={"SNAP? 1,2,3,4": "1.0,2.0,3.0,4.0"})
        backend = _backend(m, lockin)
        lockin.queries.clear()
        assert backend.snap() == (1.0, 2.0, 3.0, 4.0), label
        assert lockin.queries == ["SNAP? 1,2,3,4"], label


def test_reading_the_whole_state_uses_the_canonical_order():
    for label, m in EDITIONS:
        answers = {command: "1" for _key, command in m.SETTINGS_QUERIES}
        lockin = FakeLockin(answers=answers)
        backend = _backend(m, lockin)
        lockin.queries.clear()
        settings = backend.read_settings()
        assert lockin.queries == [c for _k, c in m.SETTINGS_QUERIES], label
        assert set(settings) == {k for k, _c in m.SETTINGS_QUERIES}, label


def test_float_settings_stay_floats_and_codes_stay_ints():
    for label, m in EDITIONS:
        answers = {command: "9.0" for _key, command in m.SETTINGS_QUERIES}
        backend = _backend(m, FakeLockin(answers=answers))
        settings = backend.read_settings()
        for key, value in settings.items():
            if key in m.FLOAT_KEYS:
                assert isinstance(value, float), (label, key)
            else:
                assert isinstance(value, int), (label, key)


def test_reset_reselects_gpib_because_rst_clears_outx():
    for label, m in EDITIONS:
        lockin = FakeLockin()
        backend = _backend(m, lockin)
        lockin.writes.clear()
        backend.reset()
        assert lockin.writes == ["*RST", "OUTX 1"], (label, lockin.writes)


def test_close_releases_the_session_without_changing_settings():
    for label, m in EDITIONS:
        lockin = FakeLockin()
        backend = _backend(m, lockin)
        lockin.writes.clear()
        backend.close()
        assert lockin.writes == [], (label, lockin.writes)
        assert lockin.closed is True, label


# ------------------------------------------------------ command hygiene

def test_no_command_outside_the_documented_sr830_mnemonic_set():
    """Guards against a mnemonic being invented rather than transcribed."""
    known = {
        "OUTX", "*IDN?", "*RST", "LIAS?", "ERRS?", "SNAP?", "OAUX?", "OUTR?",
        "OUTP?", "AGAN", "ARSV", "APHS", "AOFF",
    } | {command for _k, command in cli.SETTINGS_QUERIES} \
      | {mnemonic.rstrip(" ,") for mnemonic, _kind, _t in cli.SETTABLE.values()}

    for label, source in (("GUI", GUI_SOURCE), ("CLI", CLI_SOURCE)):
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", "") not in ("write", "query"):
                continue
            for arg in node.args:
                if not (isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)):
                    continue
                head = arg.value.split()[0] if arg.value.split() else ""
                head = head.rstrip(",")
                assert head in known, (label, arg.value)


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
