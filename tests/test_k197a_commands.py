"""Tests for the Keithley 197A monitor (GUI and headless editions).

The 197A is pre-SCPI: it has no identify query, so a mis-set address cannot
be caught the usual way. The reference address table even has the 197A and a
Keithley 2182 both on GPIB0::7, so "wrong instrument at this address" is a
realistic failure, not a theoretical one. These tests pin down the guard that
catches it before any 197-dialect command is written, and pin the command
table itself so a function the meter does not have cannot creep back in.

Runnable as plain Python as well as under pytest.
"""

import ast
import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

GUI_PATH = os.path.join(REPO_ROOT, "pica", "keithley", "k197a",
                        "Monitor_K197A_GUI.py")
CLI_PATH = os.path.join(REPO_ROOT, "pica", "keithley", "k197a",
                        "Instrument_Control",
                        "Monitor_K197A_Instrument_Control.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gui = _load("k197a_gui_under_test", GUI_PATH)
cli = _load("k197a_cli_under_test", CLI_PATH)
EDITIONS = (("GUI", gui), ("CLI", cli))

GUI_SOURCE = open(GUI_PATH, encoding="utf-8").read()
CLI_SOURCE = open(CLI_PATH, encoding="utf-8").read()


class FakeMeter:
    """Records writes; replies with a canned string or raises."""

    def __init__(self, reply="NDCV+1.23456E+0", raise_on_read=None):
        self.reply = reply
        self.raise_on_read = raise_on_read
        self.writes = []
        self.reads = 0
        self.closed = False
        self.read_termination = None
        self.write_termination = None
        self.timeout = None

    def write(self, command):
        self.writes.append(command)

    def read(self):
        self.reads += 1
        if self.raise_on_read is not None:
            raise self.raise_on_read
        return self.reply

    def close(self):
        self.closed = True


class _PatchVisa:
    def __init__(self, module, meter):
        self.module = module
        self.meter = meter

    def __enter__(self):
        self._old = self.module.pyvisa
        meter = self.meter

        class _FakeRM:
            def open_resource(self, address):
                return meter

        class _FakeVisa:
            ResourceManager = staticmethod(_FakeRM)
            errors = self._old.errors if self._old else None

        self.module.pyvisa = _FakeVisa
        return meter

    def __exit__(self, *exc):
        self.module.pyvisa = self._old
        return False


def _backend(module, meter):
    with _PatchVisa(module, meter):
        return module.Keithley197A_Backend("GPIB0::7::INSTR")


# ------------------------------------------------------- the command table

def test_the_197a_has_no_four_wire_ohms_function():
    """The 197A is a 2-wire meter: there are no sense terminals to use."""
    for label, module in EDITIONS:
        assert "4-wire ohms" not in module.CMD["function"], label
        assert "4-wire ohms" not in module.FUNCTION_UNITS, label
        assert "4-wire ohms" not in module.FUNCTION_NAMES, label


def test_no_function_code_beyond_the_documented_range():
    for label, module in EDITIONS:
        for name, code in module.CMD["function"].items():
            assert code[0] == "F", (label, name, code)
            assert code[1:].isdigit(), (label, name, code)
            assert 0 <= int(code[1:]) <= 5, (label, name, code)


def test_range_codes_are_r0_through_r7_with_r0_as_autorange():
    for label, module in EDITIONS:
        assert module.CMD["range"]["auto"] == "R0", label
        for n in range(1, 8):
            assert module.CMD["range"][str(n)] == f"R{n}", label
        assert len(module.CMD["range"]) == 8, label


def test_function_codes_are_unique():
    for label, module in EDITIONS:
        codes = list(module.CMD["function"].values())
        assert len(codes) == len(set(codes)), (label, codes)


def test_every_function_has_a_unit_and_appears_in_the_dropdown():
    for label, module in EDITIONS:
        for name in module.CMD["function"]:
            assert name in module.FUNCTION_UNITS, (label, name)
            assert name in module.FUNCTION_NAMES, (label, name)
        assert set(module.FUNCTION_NAMES) == set(module.CMD["function"]), label


def test_only_ohm_functions_are_labelled_ohm():
    """A unit label that does not match the function is how data goes wrong."""
    for label, module in EDITIONS:
        for name, unit in module.FUNCTION_UNITS.items():
            if unit == "Ohm":
                assert "ohms" in name, (label, name)


def test_the_two_editions_ship_the_same_command_table():
    assert gui.CMD == cli.CMD
    assert gui.FUNCTION_UNITS == cli.FUNCTION_UNITS
    assert gui.FUNCTION_NAMES == cli.FUNCTION_NAMES
    assert gui.RANGE_NAMES == cli.RANGE_NAMES


def test_no_scpi_colon_command_anywhere_in_either_edition():
    """This interface predates SCPI; a colon command would be nonsense."""
    for label, source in (("GUI", GUI_SOURCE), ("CLI", CLI_SOURCE)):
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", "") not in ("write", "query"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert not arg.value.startswith(":"), (label, arg.value)
                    assert "*IDN" not in arg.value, (label, arg.value)
                    assert "*RST" not in arg.value, (label, arg.value)


# ------------------------------------------------------------- configure

def test_configure_concatenates_function_range_and_one_execute():
    for label, module in EDITIONS:
        meter = FakeMeter()
        backend = _backend(module, meter)
        command = backend.configure("DC volts", "auto")
        assert command == "F0R0X", label
        assert meter.writes == ["F0R0X"], label


def test_configure_ends_in_exactly_one_execute_character():
    for label, module in EDITIONS:
        meter = FakeMeter()
        backend = _backend(module, meter)
        for name in module.FUNCTION_NAMES:
            for range_name in module.RANGE_NAMES:
                command = backend.configure(name, range_name)
                assert command.endswith("X"), (label, command)
                assert command.count("X") == 1, (label, command)


def test_configure_rejects_a_function_the_meter_does_not_have():
    meter = FakeMeter()
    backend = _backend(gui, meter)
    try:
        backend.configure("4-wire ohms", "auto")
    except KeyError:
        pass
    else:
        raise AssertionError("a 4-wire function was accepted")
    assert meter.writes == []


# ------------------------------------------------- the wrong-instrument guard

def test_a_scpi_identity_reply_is_refused_as_the_wrong_instrument():
    """A Keithley 2182 sitting on the 197A's address must be caught."""
    for label, module in EDITIONS:
        meter = FakeMeter(
            reply="KEITHLEY INSTRUMENTS INC.,MODEL 2182,4321,C02")
        backend = _backend(module, meter)
        try:
            backend.probe()
        except module.WrongInstrumentError as exc:
            assert "2182" in str(exc), label
        else:
            raise AssertionError(f"{label}: a 2182 was accepted as a 197A")


def test_a_meter_style_reply_is_accepted():
    for label, module in EDITIONS:
        for reply in ("NDCV+1.23456E+0", "+1.23456E+0", "1.2345",
                      "-0.0001", "NOHM+9.9999E+9"):
            meter = FakeMeter(reply=reply)
            backend = _backend(module, meter)
            assert backend.probe() == reply, (label, reply)


def test_a_reply_with_no_number_at_all_is_refused():
    for label, module in EDITIONS:
        meter = FakeMeter(reply="OVERFLOW")
        backend = _backend(module, meter)
        try:
            backend.probe()
        except module.WrongInstrumentError:
            pass
        else:
            raise AssertionError(f"{label}: a non-numeric reply was accepted")


def test_the_probe_sends_only_the_bare_execute_character():
    for label, module in EDITIONS:
        meter = FakeMeter()
        backend = _backend(module, meter)
        backend.probe()
        assert meter.writes == ["X"], (label, meter.writes)


def test_a_silent_address_raises_no_response_not_wrong_instrument():
    import pyvisa
    for label, module in EDITIONS:
        meter = FakeMeter(raise_on_read=pyvisa.errors.VisaIOError(-1073807339))
        backend = _backend(module, meter)
        try:
            backend.probe()
        except module.NoResponseError as exc:
            assert "1973A" in str(exc), label
        else:
            raise AssertionError(f"{label}: a bus timeout was misreported")


def test_looks_like_reading_separates_readings_from_identity_strings():
    for label, module in EDITIONS:
        assert module.looks_like_reading("NDCV+1.23456E+0"), label
        assert module.looks_like_reading("0"), label
        assert not module.looks_like_reading(
            "KEITHLEY INSTRUMENTS INC.,MODEL 2182,4321,C02"), label
        assert not module.looks_like_reading("Cryocon,34,204683,3.18A"), label
        assert not module.looks_like_reading(""), label
        assert not module.looks_like_reading("no numbers here"), label


def test_probe_runs_before_configure_in_both_entry_points():
    """The guard is worthless if a command goes out before it runs."""
    for label, source in (("GUI", GUI_SOURCE), ("CLI", CLI_SOURCE)):
        probe_at = source.index(".probe()")
        configure_at = source.index(".configure(function_name")
        assert probe_at < configure_at, label


def test_both_entry_points_handle_the_wrong_instrument_error_explicitly():
    for label, source in (("GUI", GUI_SOURCE), ("CLI", CLI_SOURCE)):
        assert "except WrongInstrumentError" in source, label


# --------------------------------------------------------- reading parsing

def test_parse_reading_pulls_the_number_out_of_a_prefixed_reply():
    for label, module in EDITIONS:
        value, raw = module.parse_reading("NDCV+1.23456E+0")
        assert value == 1.23456, label
        assert raw == "NDCV+1.23456E+0", label


def test_parse_reading_returns_none_rather_than_raising():
    for label, module in EDITIONS:
        assert module.parse_reading(None) == (None, ""), label
        assert module.parse_reading("OVERFLOW")[0] is None, label
        assert module.parse_reading("")[0] is None, label


def test_parse_reading_keeps_the_sign():
    for label, module in EDITIONS:
        assert module.parse_reading("-1.5")[0] == -1.5, label
        assert module.parse_reading("NDCV-9.9999E+9")[0] == -9.9999e9, label


# ------------------------------------------------------------ poll rate

def test_the_poll_interval_is_clamped_to_the_meters_own_reading_rate():
    """The 197A answers at most 3 readings per second; faster returns stale."""
    for label, module in EDITIONS:
        assert abs(module.MIN_POLL_INTERVAL - 1 / 3) < 0.01, label
        assert module.DEFAULT_POLL_INTERVAL >= module.MIN_POLL_INTERVAL, label


def test_clamp_interval_raises_a_too_fast_request():
    assert cli.clamp_interval(0.01) == cli.MIN_POLL_INTERVAL
    assert cli.clamp_interval(2.0) == 2.0


# ------------------------------------------------------------- shutdown

def test_close_releases_the_session_without_writing():
    for label, module in EDITIONS:
        meter = FakeMeter()
        backend = _backend(module, meter)
        meter.writes.clear()
        backend.close()
        assert meter.writes == [], label
        assert meter.closed is True, label
        assert backend.instrument is None, label


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
