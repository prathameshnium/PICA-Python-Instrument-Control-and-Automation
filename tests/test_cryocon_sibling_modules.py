"""Tests for the four Cryo-con siblings outside pica/cryocon/.

RT_K6517B_CC34, RT_K2400_CC34, RT_K2400_K2182_CC34 and Delta_RT_K6221_K2182_CC34
all read a Cryo-con Model 34 passively for thermometry, and all four carried
the same faults the pica/cryocon/ modules were hardened against on
28 Aug 2026:

  1. No settle delay and no retry on the first '*IDN?', so a write timeout
     killed the connection outright.
  2. A plain float() on the reading, which rejects '77.350K' and reports a
     healthy sensor as a fault.
  3. The Cryocon picked by address. It is at GPIB0::12 as of 29 Aug 2026
     and the Lakeshore 350 answers on GPIB1::12 -- the Cryo-con's own
     factory address -- so address-based selection would log the wrong
     instrument's temperature against every data point.

The K6517B and delta-mode modules additionally had the 'NoneType: None'
reporting bug: they are the two of the four that measure on a worker
thread, and both called format_exc() on the GUI thread, where no exception
is live. k2400 and k2400_2182 run their loop on the GUI thread via
root.after, so their format_exc() is inside a live except block and works
correctly; they were left alone.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MODULE_PATHS = {
    "k6517b": os.path.join(REPO_ROOT, "pica", "keithley", "k6517b",
                           "High_Resistance",
                           "RT_K6517B_CC34_T_Sensing_GUI.py"),
    "k2400": os.path.join(REPO_ROOT, "pica", "keithley", "k2400",
                          "RT_K2400_CC34_T_Sensing_GUI.py"),
    "k2400_2182": os.path.join(REPO_ROOT, "pica", "keithley", "k2400_2182",
                               "RT_K2400_K2182_CC34_T_Sensing_GUI.py"),
    "delta": os.path.join(REPO_ROOT, "pica", "keithley", "delta_mode",
                          "Delta_RT_K6221_K2182_CC34_Sensing_GUI.py"),
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULES = {key: _load(f"cc34_sibling_{key}", path)
           for key, path in MODULE_PATHS.items()}
SOURCES = {key: open(path, encoding="utf-8").read()
           for key, path in MODULE_PATHS.items()}

CRYOCON_IDN = "Cryocon Model 34, Rev 3.03A"
LAKESHORE_IDN = "LSCI,MODEL350,LSA2FKB/#######,1.7"


class FakeVisaTimeout(IOError):
    def __init__(self):
        super().__init__("VI_ERROR_TMO (-1073807339): Timeout expired "
                         "before operation completed.")


class FakeInstrument:
    def __init__(self, idn, temp="77.350K", write_timeouts=0):
        self.idn = idn
        self.temp = temp
        self.writes = []
        self.queries = []
        self.closed = False
        self.timeout = None
        self._write_timeouts = write_timeouts

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        if self._write_timeouts > 0:
            self._write_timeouts -= 1
            raise FakeVisaTimeout()
        self.queries.append(command)
        cmd = command.strip()
        if cmd == "*IDN?":
            return self.idn
        if cmd.startswith("INPUT") and cmd.endswith(":UNITS?"):
            return "K"
        if cmd.startswith("INPUT?"):
            return self.temp
        if "OUTPWR?" in cmd:
            return "0.0"
        return "0"

    def close(self):
        self.closed = True


class FakeBus:
    """The lab bus of 29 Aug 2026: Cryocon on GPIB0::12, LS350 on GPIB1::12."""

    def __init__(self, cryocon_write_timeouts=0, cryocon_temp="77.350K"):
        self.instruments = {
            "GPIB0::12::INSTR": FakeInstrument(
                CRYOCON_IDN, temp=cryocon_temp,
                write_timeouts=cryocon_write_timeouts),
            "GPIB1::12::INSTR": FakeInstrument(LAKESHORE_IDN),
        }
        self.opened = []

    def list_resources(self):
        return tuple(self.instruments)

    def open_resource(self, resource, **kwargs):
        self.opened.append(resource)
        if resource not in self.instruments:
            raise FakeVisaTimeout()
        return self.instruments[resource]

    def close(self):
        pass

    @property
    def cryocon(self):
        return self.instruments["GPIB0::12::INSTR"]

    @property
    def lakeshore(self):
        return self.instruments["GPIB1::12::INSTR"]


class patch_bus:
    def __init__(self, module, bus):
        self.module = module
        self.bus = bus

    def __enter__(self):
        self._old = getattr(self.module, "pyvisa", None)
        bus = self.bus

        class _FakeVisa:
            ResourceManager = staticmethod(lambda: bus)
            errors = getattr(self._old, "errors", None)

        self.module.pyvisa = _FakeVisa
        self._old_sleep = self.module.time.sleep
        self.module.time.sleep = lambda *_a, **_k: None
        return bus

    def __exit__(self, *exc):
        self.module.pyvisa = self._old
        self.module.time.sleep = self._old_sleep
        return False


# ----------------------------------------------------- the shared hardening

def test_every_sibling_carries_the_hardening_helpers():
    for key, module in MODULES.items():
        for name in ("parse_cryocon_number", "is_cryocon_idn",
                     "open_cryocon_session", "identify_resources",
                     "CryoconStatusError"):
            assert hasattr(module, name), f"{key} is missing {name}"


def test_every_sibling_points_at_the_current_cryocon_address():
    """GPIB0::12 as of 29 Aug 2026, and only ever as a hint."""
    for key, module in MODULES.items():
        assert module.CRYOCON_ADDRESS_HINT == "GPIB0::12", key
        # Identity, not address, must be what actually decides.
        assert "is_cryocon_idn(identities" in SOURCES[key], key


def test_no_sibling_still_selects_the_cryocon_by_the_old_address():
    """GPIB1::12 is the Lakeshore now. It must not appear as a selector."""
    for key, source in SOURCES.items():
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue          # the comments explain the move; that is fine
            assert "GPIB1::12" not in stripped, (key, stripped)


# ------------------------------------------- fault 2: the reading parses

def test_a_reading_with_a_unit_suffix_is_a_temperature_not_a_fault():
    """'77.350K' used to be reported as a sensor fault by float()."""
    for key, module in MODULES.items():
        value = module.parse_cryocon_number("77.350K", "temperature",
                                            channel="A")
        assert abs(value - 77.350) < 1e-9, (key, value)


def test_status_strings_are_named_not_swallowed():
    for key, module in MODULES.items():
        for reply in ("-------", "........", "N/A", "NACK", ""):
            try:
                module.parse_cryocon_number(reply, "temperature", channel="A")
            except module.CryoconStatusError as exc:
                assert "channel A" in str(exc), (key, reply, exc)
            else:
                raise AssertionError(f"{key}: {reply!r} parsed as a number")


def test_a_semicolon_separated_reply_takes_the_first_field():
    for key, module in MODULES.items():
        assert module.parse_cryocon_number(
            "77.350K;300.0K", "temperature") == 77.350, key


# --------------------------------- fault 1: the retried first '*IDN?'

def test_a_write_timeout_on_the_first_idn_is_retried_not_fatal():
    for key, module in MODULES.items():
        bus = FakeBus(cryocon_write_timeouts=1)
        with patch_bus(module, bus):
            inst, idn = module.open_cryocon_session("GPIB0::12::INSTR",
                                                    log=lambda m: None)
        assert "Cryocon" in idn, (key, idn)
        assert inst.writes == [], (key, inst.writes)


def test_a_silent_address_gives_up_after_the_configured_attempts():
    for key, module in MODULES.items():
        bus = FakeBus(cryocon_write_timeouts=99)
        with patch_bus(module, bus):
            try:
                module.open_cryocon_session("GPIB0::12::INSTR",
                                            log=lambda m: None)
            except ConnectionError as exc:
                assert "after" in str(exc), (key, exc)
            else:
                raise AssertionError(f"{key}: a silent address connected")
        assert len(bus.opened) <= module.CRYOCON_CONNECT_ATTEMPTS, key


# ------------------------- fault 3: the Lakeshore on the factory address

def test_the_lakeshore_on_the_factory_address_is_refused():
    for key, module in MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            try:
                module.open_cryocon_session("GPIB1::12::INSTR",
                                            log=lambda m: None)
            except ConnectionError as exc:
                assert "not a Cryo-con" in str(exc), (key, exc)
            else:
                raise AssertionError(f"{key}: connected to the Lakeshore")
        assert bus.lakeshore.writes == [], (key, bus.lakeshore.writes)


def test_identify_resources_finds_the_cryocon_wherever_it_sits():
    for key, module in MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            found = module.identify_resources(bus, bus.list_resources())
        assert found["GPIB0::12::INSTR"] == CRYOCON_IDN, key
        assert found["GPIB1::12::INSTR"] == LAKESHORE_IDN, key
        cryocon = [r for r, idn in found.items() if module.is_cryocon_idn(idn)]
        assert cryocon == ["GPIB0::12::INSTR"], (key, cryocon)


def test_serial_ports_are_not_probed():
    for key, module in MODULES.items():
        assert module.PROBE_RESOURCE_PREFIXES == ("GPIB", "USB", "TCPIP"), key
        bus = FakeBus()
        with patch_bus(module, bus):
            module.identify_resources(bus, ["ASRL1::INSTR",
                                            "GPIB0::12::INSTR"])
        assert bus.opened == ["GPIB0::12::INSTR"], (key, bus.opened)


# ------------------------------------------ the Cryocon stays read-only

def test_a_passive_session_never_writes_to_the_cryocon():
    for key, module in MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            inst, _ = module.open_cryocon_session("GPIB0::12::INSTR",
                                                  log=lambda m: None)
            inst.query("INPUT A:UNITS?")
            inst.query("INPUT? A")
            inst.close()
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)


def test_no_sibling_ever_sends_rst_or_a_control_command_to_the_cryocon():
    forbidden = ("*RST", "STOP", "CONTROL", ":PGAIN", ":SETPT", ":PMAN")
    for key, source in SOURCES.items():
        for line in source.splitlines():
            if "cryocon" not in line.lower():
                continue
            if ".write(" not in line:
                continue
            for token in forbidden:
                assert token not in line, (key, line.strip())


# ------------------------- delta mode: the worker traceback reporting bug

def test_delta_mode_ships_the_worker_traceback_to_the_gui_thread():
    source = SOURCES["delta"]
    assert "self.data_queue.put((e, traceback.format_exc()))" in source
    # And the consumer must use what was sent rather than re-deriving it.
    assert "exc, tb_text = data" in source
    assert "RUNTIME ERROR in worker thread: {traceback.format_exc()}" \
        not in source


def test_the_k6517b_worker_also_ships_its_traceback():
    """It has a measurement worker too, so it had the same reporting bug."""
    source = SOURCES["k6517b"]
    assert "self.data_queue.put((e, traceback.format_exc()))" in source
    assert "exc, tb_text = data[0], data[1]" in source
    assert "RUNTIME ERROR: {traceback.format_exc()}" not in source


def test_the_two_gui_thread_modules_were_left_alone():
    """k2400 and k2400_2182 run the loop on the GUI thread via root.after.

    Their format_exc() sits inside a live except block on the same thread,
    so it prints the real traceback. There is no bug to fix, and no queue
    to fix it in.
    """
    for key in ("k2400", "k2400_2182"):
        assert "data_queue" not in SOURCES[key], key
        assert "threading.Thread" not in SOURCES[key], key
        assert "traceback.format_exc()" in SOURCES[key], key


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s).")
    sys.exit(1 if failures else 0)
