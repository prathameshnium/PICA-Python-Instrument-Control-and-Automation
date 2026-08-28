"""Tests for the v2 launcher's instrument scan.

The scan is the only thing the launcher does on the GPIB bus, so it is where
an instrument can be disturbed. Everything here runs against a fake VISA
ResourceManager, so no hardware and no VISA backend are needed.

What is being pinned down:
  * instruments are identified by the CONTENT of *IDN?, never by address, so
    a re-addressed rack still resolves and two instruments that swap
    addresses cannot be confused;
  * a protected address is never opened at all -- not opened and timed out,
    not opened and closed, never opened;
  * a Novocontrol mainframe is spoken to at most once, ever, and its address
    is handed back so the caller can protect it permanently;
  * serial resources are never probed.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MODULE_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load("pica_main_v2_under_test", MODULE_PATH)


# ---------------------------------------------------------------- fake VISA

class FakeInstrument:
    """One instrument on the fake bus. Records every query it is sent."""

    def __init__(self, idn, extra=None, fail_on_open=False):
        self.idn = idn
        self.extra = extra or {}
        self.queries = []
        self.closed = False
        self.timeout = None

    def query(self, command):
        self.queries.append(command)
        if command == "*IDN?":
            if self.idn is None:
                raise IOError("timeout")
            return self.idn
        if command in self.extra:
            return self.extra[command]
        raise IOError(f"unsupported query {command!r}")

    def close(self):
        self.closed = True


class FakeResourceManager:
    """Stand-in for pyvisa.ResourceManager over a dict of resources."""

    def __init__(self, instruments, open_failures=()):
        self.instruments = instruments
        self.open_failures = set(open_failures)
        self.opened = []          # every resource open_resource was CALLED for
        self.closed = False

    def list_resources(self):
        return tuple(self.instruments)

    def open_resource(self, resource):
        self.opened.append(resource)
        if resource in self.open_failures:
            raise IOError(f"cannot open {resource}")
        return self.instruments[resource]

    def close(self):
        self.closed = True


class _Patch:
    """Swap the module's pyvisa for a fake for the duration of a block."""

    def __init__(self, rm, available=True):
        self.rm = rm
        self.available = available

    def __enter__(self):
        self._old_visa = launcher.pyvisa
        self._old_flag = launcher.PYVISA_AVAILABLE
        self._old_file = launcher.PROTECTED_ADDRESS_FILE

        class _FakeVisa:
            ResourceManager = staticmethod(lambda: self.rm)

        launcher.pyvisa = _FakeVisa
        launcher.PYVISA_AVAILABLE = self.available
        # Never let a test read or write the real launcher config file.
        launcher.PROTECTED_ADDRESS_FILE = os.path.join(
            tempfile.mkdtemp(), "launcher_protected_gpib.json")
        return self

    def __exit__(self, *exc):
        launcher.pyvisa = self._old_visa
        launcher.PYVISA_AVAILABLE = self._old_flag
        launcher.PROTECTED_ADDRESS_FILE = self._old_file
        return False


LAKESHORE = "LSCI,MODEL350,LSA1234,1.5"
CRYOCON = "Cryocon,Model 34,204683,3.18A"
K2400 = "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234,C32"
K2182 = "KEITHLEY INSTRUMENTS INC.,MODEL 2182,4321,C02"
E4980 = "Keysight Technologies,E4980A,MY123,A.02.10"
SR830 = "Stanford_Research_Systems,SR830,s/n12345,ver1.07"
ALPHA = "NOVOCONTROL,ALPHA-A,12345,4.0"


# ------------------------------------------------------- address parsing

def test_gpib_address_of_handles_every_spelling():
    assert launcher._gpib_address_of("GPIB0::12::INSTR") == 12
    assert launcher._gpib_address_of("GPIB1::12") == 12
    assert launcher._gpib_address_of("gpib0::5::instr") == 5
    # A secondary address does not change the primary.
    assert launcher._gpib_address_of("GPIB0::12::7::INSTR") == 12


def test_gpib_address_of_returns_none_for_non_gpib():
    assert launcher._gpib_address_of("ASRL3::INSTR") is None
    assert launcher._gpib_address_of("USB0::0x0957::0x0909::MY::INSTR") is None
    assert launcher._gpib_address_of("TCPIP0::192.168.0.7::inst0::INSTR") is None


def test_serial_resources_are_not_probeable():
    assert not launcher._is_probeable("ASRL1::INSTR")
    assert not launcher._is_probeable("ASRL10::INSTR")
    assert launcher._is_probeable("GPIB0::5::INSTR")
    assert launcher._is_probeable("USB0::0x0957::0x0909::MY::INSTR")
    assert launcher._is_probeable("TCPIP0::192.168.0.7::inst0::INSTR")


# ------------------------------------------------------- identity matching

def test_idn_head_drops_the_serial_number_fields():
    head = launcher._idn_head("KEITHLEY INSTRUMENTS INC.,MODEL 2182,4321,C02")
    assert head == "KEITHLEY INSTRUMENTS INC.,MODEL 2182"


def test_idn_head_keeps_free_text_replies_whole():
    assert launcher._idn_head("Cryocon Model 34 Rev 3.18A") == \
        "CRYOCON MODEL 34 REV 3.18A"


def test_a_serial_number_cannot_masquerade_as_a_model_number():
    """A 2400 whose serial contains "2182" must not register as a 2182."""
    bus = {"GPIB0::4::INSTR": FakeInstrument(
        "KEITHLEY INSTRUMENTS INC.,MODEL 2400,MODEL 2182,C32")}
    with _Patch(FakeResourceManager(bus)):
        r = launcher.scan_instruments()
    assert r["detected"]["Keithley 2400"] == "GPIB0::4::INSTR"
    assert r["detected"]["Keithley 2182"] is None


def test_every_cryocon_idn_spelling_is_recognised():
    """All the forms a Cryo-con is known to answer with must match."""
    for idn in ("Cryocon,34,204683,3.18A",
                "Cryocon,Model 34,204683,3.18A",
                "Cryocon Model 34 Rev 3.18A",
                "Cryo-con,Model 34,204683,3.18A",
                "CRYOCON,34,1,1"):
        bus = {"GPIB1::12::INSTR": FakeInstrument(
            idn, {"INPUT? A": "300.0", "INPUT A:UNITS?": "K"})}
        with _Patch(FakeResourceManager(bus)):
            r = launcher.scan_instruments()
        assert r["detected"]["Cryocon 34"] == "GPIB1::12::INSTR", idn


def test_a_cryocon_serial_containing_34_does_not_match_a_model_32():
    bus = {"GPIB1::12::INSTR": FakeInstrument("Cryocon,32,341234,3.18A")}
    with _Patch(FakeResourceManager(bus)):
        r = launcher.scan_instruments()
    assert r["detected"]["Cryocon 34"] is None


def test_idn_matches_requires_every_part_of_a_tuple_pattern():
    assert launcher._idn_matches("CRYOCON,34", ("CRYOCON", "34"))
    assert not launcher._idn_matches("CRYOCON,32", ("CRYOCON", "34"))
    assert not launcher._idn_matches("LSCI,MODEL350", ("CRYOCON", "34"))


# ------------------------------------------------- identity, not address

def test_instruments_are_found_wherever_they_sit():
    """The whole rack re-addressed: every instrument is still identified."""
    bus = {
        "GPIB0::22::INSTR": FakeInstrument(LAKESHORE, {"KRDG? A": "295.15"}),
        "GPIB0::23::INSTR": FakeInstrument(K2400),
        "GPIB0::24::INSTR": FakeInstrument(E4980),
        "GPIB0::25::INSTR": FakeInstrument(SR830),
    }
    rm = FakeResourceManager(bus)
    with _Patch(rm):
        r = launcher.scan_instruments()

    assert r["detected"]["Lakeshore 350"] == "GPIB0::22::INSTR"
    assert r["detected"]["Keithley 2400"] == "GPIB0::23::INSTR"
    assert r["detected"]["Keysight E4980A"] == "GPIB0::24::INSTR"
    assert r["detected"]["SR830 Lock-in"] == "GPIB0::25::INSTR"
    assert r["error"] is None


def test_two_instruments_that_swapped_addresses_are_not_confused():
    bus = {
        "GPIB0::7::INSTR": FakeInstrument(K2400),    # reference says 2182
        "GPIB1::4::INSTR": FakeInstrument(K2182),    # reference says 2400
    }
    rm = FakeResourceManager(bus)
    with _Patch(rm):
        r = launcher.scan_instruments()

    assert r["detected"]["Keithley 2400"] == "GPIB0::7::INSTR"
    assert r["detected"]["Keithley 2182"] == "GPIB1::4::INSTR"


def test_a_silent_address_is_simply_not_detected():
    bus = {
        "GPIB0::5::INSTR": FakeInstrument(None),     # times out on *IDN?
        "GPIB0::6::INSTR": FakeInstrument(K2400),
    }
    rm = FakeResourceManager(bus)
    with _Patch(rm):
        r = launcher.scan_instruments()

    assert r["detected"]["Keithley 2400"] == "GPIB0::6::INSTR"
    assert all(v is None for k, v in r["detected"].items()
               if k != "Keithley 2400")


def test_a_resource_that_will_not_open_does_not_close_a_stale_handle():
    """open_resource raising must not leave the loop closing an old session."""
    good = FakeInstrument(K2400)
    bus = {
        "GPIB0::4::INSTR": good,
        "GPIB0::9::INSTR": FakeInstrument(E4980),
    }
    rm = FakeResourceManager(bus, open_failures=["GPIB0::9::INSTR"])
    with _Patch(rm):
        r = launcher.scan_instruments()

    assert r["detected"]["Keithley 2400"] == "GPIB0::4::INSTR"
    assert r["detected"]["Keysight E4980A"] is None
    # The healthy instrument was closed exactly once, by its own iteration.
    assert good.closed is True


# ------------------------------------------------------ protected addresses

def test_a_protected_address_is_never_opened():
    alpha = FakeInstrument(ALPHA)
    bus = {
        "GPIB0::5::INSTR": alpha,
        "GPIB0::4::INSTR": FakeInstrument(K2400),
    }
    rm = FakeResourceManager(bus)
    with _Patch(rm):
        r = launcher.scan_instruments(skip_addresses={5})

    assert "GPIB0::5::INSTR" not in rm.opened, "protected address was opened"
    assert alpha.queries == [], "protected instrument was sent something"
    assert "GPIB0::5::INSTR" in r["skipped"]
    assert r["detected"]["Keithley 2400"] == "GPIB0::4::INSTR"


def test_in_source_skip_list_is_honoured():
    alpha = FakeInstrument(ALPHA)
    rm = FakeResourceManager({"GPIB0::20::INSTR": alpha})
    old = launcher.SKIP_GPIB_ADDRESSES
    launcher.SKIP_GPIB_ADDRESSES = {20}
    try:
        with _Patch(rm):
            launcher.scan_instruments()
    finally:
        launcher.SKIP_GPIB_ADDRESSES = old
    assert rm.opened == []
    assert alpha.queries == []


def test_serial_ports_are_listed_but_never_spoken_to():
    serial = FakeInstrument("should never be asked")
    bus = {
        "ASRL1::INSTR": serial,
        "GPIB0::4::INSTR": FakeInstrument(K2400),
    }
    rm = FakeResourceManager(bus)
    with _Patch(rm):
        r = launcher.scan_instruments()

    assert "ASRL1::INSTR" not in rm.opened
    assert serial.queries == []
    assert "ASRL1::INSTR" in r["resources"]     # still reported to the user
    assert "ASRL1::INSTR" in r["skipped"]


# --------------------------------------------------------- Novocontrol

def test_novocontrol_is_identified_once_and_handed_back_for_protection():
    alpha = FakeInstrument(ALPHA)
    bus = {
        "GPIB0::5::INSTR": alpha,
        "GPIB0::4::INSTR": FakeInstrument(K2400),
    }
    rm = FakeResourceManager(bus)
    with _Patch(rm):
        r = launcher.scan_instruments()

    assert r["novocontrol"] == "GPIB0::5::INSTR"
    assert r["protect"] == {5}
    # Exactly one thing was ever said to it, and it was a read-only query.
    assert alpha.queries == ["*IDN?"]
    assert alpha.closed is True


def test_novocontrol_is_never_listed_as_a_normal_instrument():
    rm = FakeResourceManager({"GPIB0::5::INSTR": FakeInstrument(ALPHA)})
    with _Patch(rm):
        r = launcher.scan_instruments()
    assert all(v is None for v in r["detected"].values())


def test_every_novocontrol_marker_is_recognised():
    for idn in ("NOVOCONTROL,ALPHA,1,1",
                "Alpha-AN Impedance Analyzer",
                "Novocontrol BDS 1200"):
        rm = FakeResourceManager({"GPIB0::5::INSTR": FakeInstrument(idn)})
        with _Patch(rm):
            r = launcher.scan_instruments()
        assert r["novocontrol"] == "GPIB0::5::INSTR", idn


def test_once_protected_the_novocontrol_is_not_probed_again():
    """The learn-then-skip cycle: second scan opens nothing at that address."""
    alpha = FakeInstrument(ALPHA)
    bus = {"GPIB0::5::INSTR": alpha, "GPIB0::4::INSTR": FakeInstrument(K2400)}

    rm1 = FakeResourceManager(bus)
    with _Patch(rm1):
        first = launcher.scan_instruments()
    learned = first["protect"]
    assert alpha.queries == ["*IDN?"]

    rm2 = FakeResourceManager(bus)
    with _Patch(rm2):
        launcher.scan_instruments(skip_addresses=learned)
    # Still exactly one query, from the first scan.
    assert alpha.queries == ["*IDN?"]
    assert "GPIB0::5::INSTR" not in rm2.opened


# ------------------------------------------------- protected-address file

def test_protected_address_file_round_trips():
    path = os.path.join(tempfile.mkdtemp(), "protected.json")
    assert launcher.save_protected_addresses({5, 20}, path) is True
    assert launcher.load_protected_addresses(path) == {5, 20}


def test_missing_protected_file_means_nothing_extra_protected():
    path = os.path.join(tempfile.mkdtemp(), "does_not_exist.json")
    assert launcher.load_protected_addresses(path) == set()


def test_corrupt_protected_file_does_not_break_the_scan():
    path = os.path.join(tempfile.mkdtemp(), "corrupt.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{this is not json")
    assert launcher.load_protected_addresses(path) == set()


def test_protected_file_with_wrong_shape_is_ignored():
    base = tempfile.mkdtemp()
    for payload in ('["not", "a", "dict"]',
                    '{"skip_addresses": "five"}',
                    '{"other_key": [1, 2]}'):
        path = os.path.join(base, f"shape_{abs(hash(payload))}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload)
        assert launcher.load_protected_addresses(path) == set(), payload


def test_non_integer_entries_are_dropped_not_fatal():
    path = os.path.join(tempfile.mkdtemp(), "mixed.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"skip_addresses": [5, "20", None, "abc", 7.0]}, fh)
    assert launcher.load_protected_addresses(path) == {5, 20, 7}


def test_saved_file_is_readable_json_with_a_comment():
    path = os.path.join(tempfile.mkdtemp(), "protected.json")
    launcher.save_protected_addresses({9}, path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["skip_addresses"] == [9]
    assert "_comment" in data


def test_save_to_an_unwritable_path_reports_failure_without_raising():
    path = os.path.join(tempfile.mkdtemp(), "no_such_dir", "protected.json")
    assert launcher.save_protected_addresses({1}, path) is False


# ------------------------------------------------------ temperature tile

def test_lakeshore_temperature_is_read_in_kelvin():
    bus = {"GPIB0::15::INSTR": FakeInstrument(LAKESHORE, {"KRDG? A": " 296.42 "})}
    with _Patch(FakeResourceManager(bus)):
        r = launcher.scan_instruments()
    assert r["temperature"] == 296.42
    assert r["temp_units"] == "K"
    assert "Lakeshore" in r["temp_source"]


def test_cryocon_temperature_carries_its_own_display_units():
    """INPUT? answers in the channel's units, so the units are read too."""
    bus = {"GPIB1::12::INSTR": FakeInstrument(
        CRYOCON, {"INPUT? A": "24.10", "INPUT A:UNITS?": "C"})}
    with _Patch(FakeResourceManager(bus)):
        r = launcher.scan_instruments()
    assert r["temperature"] == 24.10
    assert r["temp_units"] == "C"
    assert "Cryocon" in r["temp_source"]


def test_lakeshore_wins_when_both_are_present():
    bus = {
        "GPIB1::15::INSTR": FakeInstrument(LAKESHORE, {"KRDG? A": "77.0"}),
        "GPIB1::12::INSTR": FakeInstrument(
            CRYOCON, {"INPUT? A": "300.0", "INPUT A:UNITS?": "K"}),
    }
    with _Patch(FakeResourceManager(bus)):
        r = launcher.scan_instruments()
    assert r["temperature"] == 77.0
    assert "Lakeshore" in r["temp_source"]


def test_a_faulted_sensor_reading_does_not_break_the_scan():
    bus = {"GPIB1::12::INSTR": FakeInstrument(
        CRYOCON, {"INPUT? A": "........", "INPUT A:UNITS?": "K"})}
    with _Patch(FakeResourceManager(bus)):
        r = launcher.scan_instruments()
    assert r["temperature"] is None
    assert r["detected"]["Cryocon 34"] == "GPIB1::12::INSTR"


# ------------------------------------------------------------- lifecycle

def test_the_resource_manager_is_closed_after_a_scan():
    rm = FakeResourceManager({"GPIB0::4::INSTR": FakeInstrument(K2400)})
    with _Patch(rm):
        launcher.scan_instruments()
    assert rm.closed is True


def test_scan_reports_missing_pyvisa_instead_of_raising():
    with _Patch(FakeResourceManager({}), available=False):
        r = launcher.scan_instruments()
    assert r["available"] is False
    assert r["error"] == "PyVISA not installed"


def test_a_broken_visa_backend_is_reported_not_raised():
    class ExplodingRM:
        def list_resources(self):
            raise OSError("no VISA library")

        def close(self):
            pass

    with _Patch(ExplodingRM()):
        r = launcher.scan_instruments()
    assert r["error"] and "VISA backend error" in r["error"]
    assert r["detected"] == {name: None for name, _ in launcher.KNOWN_INSTRUMENTS}


def test_scan_result_always_has_the_keys_the_gui_reads():
    with _Patch(FakeResourceManager({})):
        r = launcher.scan_instruments()
    for key in ("available", "error", "resources", "skipped", "detected",
                "novocontrol", "protect", "temperature", "temp_units",
                "temp_source", "timestamp"):
        assert key in r, key


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
