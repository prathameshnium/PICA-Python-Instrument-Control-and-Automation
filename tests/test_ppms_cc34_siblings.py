"""Tests for the two Cryo-con Model 34 siblings of the PPMS dielectric
programs:

    pica/keysight/PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py
    pica/keysight/PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py

Both are derived from their Lakeshore 350 parents, which stay exactly as
they are. Two things therefore have to hold at once, and both are checked
here:

  1. PARITY. Only the thermometer changed. Every pure planning function -
     scan estimates, tolerance and dwell tables, the .seq generators and
     their validators, the protocol phase machine - must return byte-for-
     byte what the parent returns, so a plan written with one program is
     the same plan in the other.

  2. THE CRYO-CON RULES. Every fault already solved elsewhere in PICA
     (28-29 Aug 2026) must be solved here too, and must not creep back:

       a. The instrument is chosen by '*IDN?' identity, never by address.
          The Cryocon sits at GPIB0::12 and the Lakeshore 350 answers on
          GPIB1::12 - the Cryo-con's OWN factory address - so an address
          pick would index a whole multi-day run by the wrong
          instrument's temperature.
       b. The first '*IDN?' is retried. On a Rev 3.03A unit it can die
          inside viWrite with VI_ERROR_TMO right after a bus scan.
       c. Readings go through parse_cryocon_number(), not float():
          '77.350K' is a temperature, dashes are a sensor fault, dots are
          an off-curve reading.
       d. The channel is verified to be reporting Kelvin at Start, since
          INPUT? answers in each channel's own display units.
       e. Nothing is ever written to the Cryocon. No *RST (a ~15 s
          hardware reset on this instrument), no STOP, no CONTROL, no
          loop/heater/range/setpoint command - there is no write path on
          the backend at all.
       f. Serial resources are not probed, and the scan does not force
          "\\n" terminations: the Cryocon frames replies with EOI and no
          EOS character.

  3. THE ONE DELIBERATE DIVERGENCE. Elsewhere in PICA a faulted sensor
     raises. Here get_temperature() returns NaN instead, because these two
     programs run unattended for days behind a retry-forever reconnect
     loop: raising would send that loop spinning for the rest of the run
     on a fault no reconnect can cure. parse_cryocon_number() itself still
     raises, and verify_channel() still raises at Start.

Runnable as plain Python as well as under pytest:
    python tests/test_ppms_cc34_siblings.py
"""

import io
import math
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import matplotlib                                             # noqa: E402
matplotlib.use("Agg")

from pica.keysight import PPMS_Sync_Freq_Scan_E4980A_GUI as sync_ls    # noqa: E402
from pica.keysight import PPMS_Sync_Freq_Scan_CC34_E4980A_GUI as sync_cc  # noqa: E402
from pica.keysight import (                                   # noqa: E402
    PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI as master_ls)
from pica.keysight import (                                   # noqa: E402
    PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI as master_cc)

KEYSIGHT = os.path.join(project_root, "pica", "keysight")

PAIRS = {
    "sync": (sync_ls, sync_cc),
    "master": (master_ls, master_cc),
}
CC_MODULES = {"sync": sync_cc, "master": master_cc}
LS_MODULES = {"sync": sync_ls, "master": master_ls}

CC_PATHS = {
    "sync": os.path.join(KEYSIGHT, "PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py"),
    "master": os.path.join(
        KEYSIGHT, "PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py"),
}
LS_PATHS = {
    "sync": os.path.join(KEYSIGHT, "PPMS_Sync_Freq_Scan_E4980A_GUI.py"),
    "master": os.path.join(
        KEYSIGHT, "PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py"),
}


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


CC_SOURCES = {k: _read(p) for k, p in CC_PATHS.items()}
LS_SOURCES = {k: _read(p) for k, p in LS_PATHS.items()}

CRYOCON_IDN = "Cryocon Model 34, Rev 3.03A"
LAKESHORE_IDN = "LSCI,MODEL350,LSA2FKB/#######,1.7"


# ===========================================================================
# Fake VISA bus - the lab bus of 29 Aug 2026
# ===========================================================================

class FakeVisaTimeout(IOError):
    def __init__(self):
        super().__init__("VI_ERROR_TMO (-1073807339): Timeout expired "
                         "before operation completed.")


class FakeInstrument:
    """Answers the read-only subset these modules use, and records
    everything, so a stray write is impossible to miss."""

    def __init__(self, idn, temp="77.350K", units="K", write_timeouts=0,
                 query_timeouts=0):
        self.idn = idn
        self.temp = temp
        self.units = units
        self.writes = []
        self.queries = []
        self.closed = False
        self.cleared = 0
        self.timeout = None
        self._write_timeouts = write_timeouts
        self.query_timeouts = query_timeouts

    def write(self, command):
        self.writes.append(command)

    def clear(self):
        self.cleared += 1

    def query(self, command):
        if self._write_timeouts > 0:
            self._write_timeouts -= 1
            raise FakeVisaTimeout()
        if self.query_timeouts > 0:
            self.query_timeouts -= 1
            raise FakeVisaTimeout()
        self.queries.append(command)
        cmd = command.strip()
        if cmd == "*IDN?":
            return self.idn
        if cmd.startswith("INPUT") and cmd.endswith(":UNITS?"):
            return self.units
        if cmd.startswith("INPUT?"):
            return self.temp
        return "0"

    def close(self):
        self.closed = True


class FakeBus:
    """Cryocon on GPIB0::12, Lakeshore 350 on GPIB1::12 (the Cryo-con's
    own factory address), plus a serial port that must never be probed."""

    def __init__(self, cryocon_write_timeouts=0, cryocon_temp="77.350K",
                 cryocon_units="K"):
        self.instruments = {
            "GPIB0::12::INSTR": FakeInstrument(
                CRYOCON_IDN, temp=cryocon_temp, units=cryocon_units,
                write_timeouts=cryocon_write_timeouts),
            "GPIB1::12::INSTR": FakeInstrument(LAKESHORE_IDN),
            "GPIB0::17::INSTR": FakeInstrument("Keysight,E4980A,0,1.0"),
            "ASRL1::INSTR": FakeInstrument("something on a serial port"),
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
    """Swap in the fake bus and make time.sleep free, so the retry and
    pacing paths run at full speed."""

    def __init__(self, module, bus):
        self.module = module
        self.bus = bus

    def __enter__(self):
        self._old = getattr(self.module, "pyvisa", None)
        bus = self.bus

        class _FakeVisa:
            ResourceManager = staticmethod(lambda: bus)

        self.module.pyvisa = _FakeVisa
        self._old_sleep = self.module.time.sleep
        self.module.time.sleep = lambda *_a, **_k: None
        return bus

    def __exit__(self, *exc):
        self.module.pyvisa = self._old
        self.module.time.sleep = self._old_sleep
        return False


def _code_lines(source):
    """Source lines that are not comments and not inside the module
    docstring - i.e. lines that actually execute."""
    lines = source.splitlines()
    # The module docstring is the leading triple-quoted block.
    end = 0
    if lines and lines[0].startswith('"""'):
        for i in range(1, len(lines)):
            if lines[i].rstrip().endswith('"""'):
                end = i + 1
                break
    for line in lines[end:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


# ===========================================================================
# 0. The parents are untouched
# ===========================================================================

def test_the_lakeshore_parents_are_still_lakeshore_programs():
    """The whole point of a sibling: the original keeps working."""
    for key, module in LS_MODULES.items():
        assert hasattr(module.Probe_Thermometer_Backend, "get_temperature"), key
        assert not hasattr(module, "parse_cryocon_number"), (
            f"{key} parent grew Cryocon helpers - it must stay untouched")
        assert not hasattr(module, "open_cryocon_session"), key
        assert "KRDG?" in LS_SOURCES[key], key
        assert "INPUT? " not in LS_SOURCES[key], key


def test_the_siblings_are_separate_files_not_replacements():
    for key in PAIRS:
        assert os.path.isfile(LS_PATHS[key]), key
        assert os.path.isfile(CC_PATHS[key]), key
        assert LS_SOURCES[key] != CC_SOURCES[key], key


# ===========================================================================
# 1. Parity - only the thermometer changed
# ===========================================================================

def test_scan_time_estimates_are_identical():
    freqs = [40.0, 100.0, 1000.0, 20000.0, 500000.0, 2000000.0]
    for key, (ls, cc) in PAIRS.items():
        for aper in ("SHOR", "MED", "LONG"):
            assert (ls.estimate_scan_seconds(freqs, 0.2, aper)
                    == cc.estimate_scan_seconds(freqs, 0.2, aper)), (key, aper)


def test_tolerance_and_dwell_tables_are_identical():
    text = "20:3.0, 25:2.5, 100:0.8, 200:0.5"
    for key, (ls, cc) in PAIRS.items():
        t_ls = ls.parse_tol_table(text)
        t_cc = cc.parse_tol_table(text)
        assert t_ls == t_cc, key
        for target in (15.0, 20.0, 22.5, 100.0, 150.0, 300.0):
            assert (ls.tol_from_table(t_ls, target, 0.4)
                    == cc.tol_from_table(t_cc, target, 0.4)), (key, target)
        d = ls.parse_tol_table("200:30, 210:40, 310:45")
        for target in (150.0, 200.0, 205.0, 310.0, 400.0):
            assert (ls.dwell_from_table(d, target)
                    == cc.dwell_from_table(d, target)), (key, target)


def test_temperature_filenames_are_identical():
    for key, (ls, cc) in PAIRS.items():
        for T in (80.05, 300.0, 9.999, 1.5):
            assert ls.fmt_temp_p(T) == cc.fmt_temp_p(T), (key, T)


def test_sync_fscan_sequence_text_is_byte_identical():
    steps = [(25.0, 1.0, 3600.0), (30.0, 0.8, 1800.0), (50.0, 0.5, 2400.0)]
    for mode in (0, 1):
        a = sync_ls.render_fscan_seq("S", steps, mode, initial_note="note")
        b = sync_cc.render_fscan_seq("S", steps, mode, initial_note="note")
        assert a == b, mode
        # And it is still a valid MultiVu sequence in the sibling.
        assert sync_cc.validate_ppms_seq(b) == []


def test_sync_sequence_validator_agrees_with_the_parent():
    bad = "TMP TEMP 25.000000 99.000000 1\nWAI WAITFOR 3600 1 0 0 0 0\n"
    assert (sync_ls.validate_ppms_seq(bad)
            == sync_cc.validate_ppms_seq(bad))
    assert sync_cc.validate_ppms_seq(bad), "an over-rate ramp must be caught"


def make_master_cfg():
    """Mirrors the reference Dielectric_Tscan.seq fixture."""
    return {
        "sample": "TestSample",
        "runs": [
            {"label": "Run1_0Oe", "field_oe": 0.0,
             "cooldown_wait_s": 10800.0},
            {"label": "Run2_5000Oe", "field_oe": 5000.0,
             "cooldown_wait_s": 14400.0},
        ],
        "base_temp": 10.0,
        "top_temp": 310.0,
        "warm_rate": 1.0,
        "cool_rate": 3.0,
        "top_hold_s": 1800.0,
        "final_cooldown_s": 10800.0,
        "field_rate": 50.0,
        "fscan_rate": 1.0,
        "base_arm": 30.0,
        "rise_k": 2.0,
        "top_arm_off": 10.0,
        "fall_k": 2.0,
        "confirm_s": 180.0,
        "overdue_s": 1200.0,
        "tscan_freqs": [1000.0, 10000.0, 100000.0],
        "schedule": [25.0, 30.0, 50.0],
        "step_wait_s": 3600.0,
        "tscan_approach": 0,
        "fscan_approach": 1,
    }


def test_master_ppms_sequence_is_byte_identical():
    a = master_ls.generate_ppms_seq(make_master_cfg())
    b = master_cc.generate_ppms_seq(make_master_cfg())
    assert a == b
    assert master_cc.validate_ppms_seq(b) == []
    # The sequence must still be the ASCII MultiVu reads.
    b.encode("ascii")


def test_master_protocol_phases_are_identical():
    a = master_ls.build_protocol_phases(make_master_cfg())
    b = master_cc.build_protocol_phases(make_master_cfg())
    assert a == b
    assert [p["kind"] for p in b] == [p["kind"] for p in a]


def test_master_fscan_step_waits_are_identical():
    cfg = make_master_cfg()
    for target in (25.0, 30.0, 50.0, 205.0, 310.0):
        assert (master_ls.fscan_step_wait_s(cfg, target)
                == master_cc.fscan_step_wait_s(cfg, target)), target


def test_the_phase_detectors_behave_identically():
    """The turnaround detector drives every unattended transition."""
    trace = [35.0, 28.0, 24.0, 21.0, 20.5, 20.2, 20.1, 20.1,
             20.4, 20.9, 21.3, 21.8, 22.4, 23.0, 23.6, 24.2]
    for key, (ls, cc) in PAIRS.items():
        d_ls, d_cc = ls.TurnaroundDetector(), cc.TurnaroundDetector()
        for T in trace:
            d_ls.update(T)
            d_cc.update(T)
        assert (d_ls.warming_started(30.0, 2.0)
                == d_cc.warming_started(30.0, 2.0) is True), key
        assert d_ls.min_T == d_cc.min_T, key
        assert d_ls.max_T == d_cc.max_T, key


def test_glitch_thresholds_and_hardening_survived_the_derivation():
    for key, (ls, cc) in PAIRS.items():
        assert cc.GLITCH_LOW_K == ls.GLITCH_LOW_K == 1.0, key
        assert cc.GLITCH_JUMP_K == ls.GLITCH_JUMP_K == 20.0, key
        assert cc.GLITCH_CONFIRM_K == ls.GLITCH_CONFIRM_K == 2.0, key
    gui = {"sync": sync_cc.PPMSSyncGUI, "master": master_cc.PPMSMasterGUI}
    for key, cls in gui.items():
        assert callable(cls._reconnect_with_backoff), key   # HARD-2
        assert callable(cls._write_or_buffer), key          # HARD-1
        assert callable(cls._flush_pending_rows), key
        assert callable(cls._set_keep_awake), key           # HARD-3
        assert callable(cls._validate_probe_reading), key   # GLITCH-1
        assert cls.ES_CONTINUOUS == 0x80000000, key
        assert "CC34" in cls.PROGRAM_VERSION, key


def test_no_modal_dialog_in_the_queue_pump():
    """UNAT-1 must survive: a messagebox there blocks the whole GUI queue,
    and unattended runs have nobody in the lab to click OK."""
    import inspect
    for key, method in (("sync", sync_cc.PPMSSyncGUI._process_gui_queue),
                        ("master",
                         master_cc.PPMSMasterGUI._process_gui_queue)):
        for line in _code_lines(inspect.getsource(method)):
            assert "messagebox." not in line, (key, line)


# ===========================================================================
# 2. The Cryo-con helper set, as hardened elsewhere in PICA
# ===========================================================================

def test_both_siblings_carry_the_hardening_helpers():
    for key, module in CC_MODULES.items():
        for name in ("parse_cryocon_number", "is_cryocon_idn",
                     "open_cryocon_session", "identify_resources",
                     "CryoconStatusError", "CRYOCON_ADDRESS_HINT",
                     "PROBE_RESOURCE_PREFIXES"):
            assert hasattr(module, name), f"{key} is missing {name}"


def test_both_siblings_point_at_the_current_cryocon_address():
    for key, module in CC_MODULES.items():
        assert module.CRYOCON_ADDRESS_HINT == "GPIB0::12", key


def test_neither_sibling_selects_the_cryocon_by_the_old_address():
    """GPIB1::12 is the Lakeshore now; it must never appear in code."""
    for key, source in CC_SOURCES.items():
        for line in _code_lines(source):
            assert "GPIB1::12" not in line, (key, line)


def test_selection_is_by_identity_not_by_idn_substring():
    for key, source in CC_SOURCES.items():
        assert "is_cryocon_idn(idn)" in source, key
        assert '"350" in idn' not in source, (
            f"{key} still auto-selects on the Lakeshore IDN substring")


def test_every_cryocon_spelling_is_recognised():
    for key, module in CC_MODULES.items():
        for idn in ("Cryocon Model 34, Rev 3.03A",
                    "Cryo-con Model 34 Rev 3.18A",
                    "CRYOCON,34,204683,3.18A",
                    "Cryo con 34"):
            assert module.is_cryocon_idn(idn), (key, idn)
        for idn in (LAKESHORE_IDN, "Keysight,E4980A,0,1.0", "", "None"):
            assert not module.is_cryocon_idn(idn), (key, idn)


def test_a_reading_with_a_unit_suffix_is_a_temperature_not_a_fault():
    for key, module in CC_MODULES.items():
        assert abs(module.parse_cryocon_number(
            "77.350K", "temperature", channel="A") - 77.350) < 1e-9, key
        assert module.parse_cryocon_number("-1.5E+01", "temperature") == -15.0


def test_status_strings_are_named_not_swallowed():
    for key, module in CC_MODULES.items():
        for reply in ("-------", "........", "N/A", "NACK", ""):
            try:
                module.parse_cryocon_number(reply, "temperature", channel="A")
            except module.CryoconStatusError as exc:
                assert "channel A" in str(exc), (key, reply, exc)
            else:
                raise AssertionError(f"{key}: {reply!r} parsed as a number")


def test_a_semicolon_separated_reply_takes_the_first_field():
    for key, module in CC_MODULES.items():
        assert module.parse_cryocon_number(
            "77.350K;300.0K", "temperature") == 77.350, key


def test_a_write_timeout_on_the_first_idn_is_retried_not_fatal():
    for key, module in CC_MODULES.items():
        bus = FakeBus(cryocon_write_timeouts=1)
        with patch_bus(module, bus):
            inst, idn = module.open_cryocon_session("GPIB0::12::INSTR",
                                                    log=lambda m: None)
        assert "Cryocon" in idn, (key, idn)
        assert inst.writes == [], (key, inst.writes)


def test_a_silent_address_gives_up_after_the_configured_attempts():
    for key, module in CC_MODULES.items():
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


def test_the_lakeshore_on_the_factory_address_is_refused():
    for key, module in CC_MODULES.items():
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
        assert bus.lakeshore.closed is True, (
            f"{key}: the rejected session was left open")


def test_identify_resources_finds_the_cryocon_and_skips_serial():
    for key, module in CC_MODULES.items():
        assert module.PROBE_RESOURCE_PREFIXES == ("GPIB", "USB", "TCPIP"), key
        bus = FakeBus()
        with patch_bus(module, bus):
            found = module.identify_resources(bus, bus.list_resources())
        assert found["GPIB0::12::INSTR"] == CRYOCON_IDN, key
        assert found["GPIB1::12::INSTR"] == LAKESHORE_IDN, key
        assert "ASRL1::INSTR" not in found, key
        assert "ASRL1::INSTR" not in bus.opened, (key, bus.opened)
        cryocon = [r for r, idn in found.items() if module.is_cryocon_idn(idn)]
        assert cryocon == ["GPIB0::12::INSTR"], (key, cryocon)


# ===========================================================================
# 3. The read-only backend
# ===========================================================================

def _connected(module, bus, address="GPIB0::12::INSTR"):
    backend = module.Probe_Thermometer_Backend()
    backend.connect(address)
    return backend


def test_connecting_writes_nothing_at_all():
    for key, module in CC_MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)
        assert bus.cryocon.queries == ["*IDN?"], (key, bus.cryocon.queries)
        assert backend.idn == CRYOCON_IDN, key


def test_the_backend_has_no_write_path_at_all():
    """A write path that exists is a write path that can be called by
    mistake. The heater here belongs to whoever is driving the cryostat."""
    for key, module in CC_MODULES.items():
        backend = module.Probe_Thermometer_Backend()
        assert not hasattr(backend, "write"), key
        assert not hasattr(backend, "set_setpoint"), key
        assert not hasattr(backend, "control_stop"), key


def test_reading_a_temperature_is_a_single_query_and_no_write():
    for key, module in CC_MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            bus.cryocon.queries.clear()
            value = backend.get_temperature("A")
        assert abs(value - 77.350) < 1e-9, (key, value)
        assert bus.cryocon.queries == ["INPUT? A"], (key, bus.cryocon.queries)
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)


def test_the_channel_is_verified_in_kelvin_by_query_only():
    for key, module in CC_MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            bus.cryocon.queries.clear()
            value = backend.verify_channel("b")
        assert abs(value - 77.350) < 1e-9, (key, value)
        assert bus.cryocon.queries == ["INPUT B:UNITS?", "INPUT? B"], (
            key, bus.cryocon.queries)
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)


def test_a_channel_left_in_celsius_is_rejected_not_logged():
    """INPUT? answers in the channel's own display units, so this is the
    check that stops a whole run being logged in the wrong scale."""
    for key, module in CC_MODULES.items():
        bus = FakeBus(cryocon_units="C")
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            try:
                backend.verify_channel("A")
            except ValueError as exc:
                assert "not " in str(exc) and "Kelvin" in str(exc), (key, exc)
            else:
                raise AssertionError(f"{key}: a Celsius channel was accepted")
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)


def test_a_faulted_sensor_is_an_invalid_reading_not_a_comm_error():
    """CC34-5, the one deliberate divergence from the other PICA Cryo-con
    modules. These two run unattended for days behind a retry-forever
    reconnect loop; raising here would spin that loop for the rest of the
    run on a fault no reconnect can cure. NaN feeds GLITCH-1 instead."""
    for key, module in CC_MODULES.items():
        for reply in ("-------", ".......", "N/A"):
            bus = FakeBus(cryocon_temp=reply)
            with patch_bus(module, bus):
                backend = _connected(module, bus)
                value = backend.get_temperature("A")
            assert math.isnan(value), (key, reply, value)
            # ...and GLITCH-1 must reject it, so it never becomes a
            # measured temperature, enters a stability window or plots.
            gui = (sync_cc.PPMSSyncGUI if key == "sync"
                   else master_cc.PPMSMasterGUI)
            assert gui._validate_probe_reading(gui, value) is False, key


def test_a_comm_failure_still_raises_so_the_reconnect_loop_runs():
    for key, module in CC_MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            bus.cryocon.query_timeouts = 99
            try:
                backend.get_temperature("A")
            except FakeVisaTimeout:
                pass
            except Exception as exc:                     # pragma: no cover
                raise AssertionError(f"{key}: wrong exception {exc!r}")
            else:
                raise AssertionError(f"{key}: a dead bus returned a value")
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)


def test_a_transient_comm_glitch_is_retried_and_recovers():
    for key, module in CC_MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            bus.cryocon.query_timeouts = 2        # under the retry budget
            value = backend.get_temperature("A")
        assert abs(value - 77.350) < 1e-9, (key, value)


def test_device_clear_stays_off_unless_turned_on_deliberately():
    """Its effect is device-dependent and the Cryo-con guide does not
    document it."""
    for key, module in CC_MODULES.items():
        assert module.ALLOW_DEVICE_CLEAR_ON_RETRY is False, key
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            bus.cryocon.query_timeouts = 2
            backend.get_temperature("A")
        assert bus.cryocon.cleared == 0, (key, bus.cryocon.cleared)


def test_reconnect_reopens_the_session_and_still_refuses_a_stranger():
    for key, module in CC_MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            first = bus.cryocon.closed
            backend.reconnect()
            assert backend.get_temperature("A") == 77.350, key
        assert first is False, key
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)
        # A power-cycled controller that comes back as something else is
        # still refused rather than silently logged.
        bus2 = FakeBus()
        with patch_bus(module, bus2):
            backend2 = module.Probe_Thermometer_Backend()
            backend2.visa_address = "GPIB1::12::INSTR"
            try:
                backend2.reconnect()
            except ConnectionError:
                pass
            else:
                raise AssertionError(f"{key}: reconnected to the Lakeshore")


def test_shutdown_closes_the_session_and_sends_nothing():
    for key, module in CC_MODULES.items():
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            before = list(bus.cryocon.queries)
            backend.shutdown()
        assert bus.cryocon.queries == before, (key, bus.cryocon.queries)
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)
        assert bus.cryocon.closed is True, key
        assert backend.cryocon is None, key


def test_no_sibling_ever_sends_rst_or_a_control_command():
    """*RST on a Cryocon is a ~15 s hardware reset to power-up defaults;
    STOP disengages both control loops. Neither belongs in a read-only
    monitor of an instrument something else may be driving."""
    import ast
    import inspect
    for key, module in CC_MODULES.items():
        for owner in (module.Probe_Thermometer_Backend,
                      module.open_cryocon_session,
                      module.identify_resources):
            tree = ast.parse(inspect.getsource(owner))
            called, commands = set(), []
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)):
                    continue
                called.add(node.func.attr)
                if node.func.attr not in ("query", "_query") or not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant):
                    commands.append(str(arg.value))
                elif isinstance(arg, ast.JoinedStr):
                    # An f-string: keep the literal parts, which is where
                    # a command name would have to live.
                    commands.append("".join(
                        v.value for v in arg.values
                        if isinstance(v, ast.Constant)))
            # There is no write path on the Cryocon side at all.
            assert "write" not in called, (key, owner.__name__, sorted(called))
            # Every command this code can put on the bus is a read.
            assert commands, (key, owner.__name__)
            for cmd in commands:
                assert cmd.strip().upper().startswith(("*IDN?", "INPUT")), (
                    key, owner.__name__, cmd)
            # Device clear is the one non-SCPI action, and it is gated off.
            if "clear" in called:
                assert module.ALLOW_DEVICE_CLEAR_ON_RETRY is False, key
        # Nor does any Cryocon-facing line elsewhere in the file write.
        for line in _code_lines(CC_SOURCES[key]):
            if "cryocon" not in line.lower():
                continue
            assert ".write(" not in line, (key, line)
        # The E4980A is still the one instrument this program configures.
        assert "*RST" in CC_SOURCES[key], (
            f"{key}: the E4980A init went missing")


def test_the_visa_scan_does_not_force_terminations_on_the_bus():
    """The Cryocon frames lines with EOI and no EOS character."""
    import inspect
    gui = {"sync": sync_cc.PPMSSyncGUI, "master": master_cc.PPMSMasterGUI}
    for key, cls in gui.items():
        src = inspect.getsource(cls._scan_for_visa)
        assert "read_termination" not in src, key
        assert "write_termination" not in src, key
        assert "identify_resources(" in src, key
        # The parent forced both terminations in its own scan loop.
        parent = (sync_ls.PPMSSyncGUI if key == "sync"
                  else master_ls.PPMSMasterGUI)
        assert "read_termination" in inspect.getsource(
            parent._scan_for_visa), key


# ===========================================================================
# 3b. CC34-8 - a bad reading is retried, not spent
# ===========================================================================

class FlakyInstrument(FakeInstrument):
    """Answers INPUT? from a scripted sequence, so a fault that clears
    after N reads can be reproduced exactly."""

    def __init__(self, idn, replies, **kw):
        super().__init__(idn, **kw)
        self.replies = list(replies)
        self.reads = 0

    def query(self, command):
        if command.strip().startswith("INPUT?"):
            self.reads += 1
            reply = (self.replies.pop(0) if self.replies else self.temp)
            self.queries.append(command)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return super().query(command)


def _flaky_bus(replies):
    bus = FakeBus()
    inst = bus.instruments["GPIB0::12::INSTR"]
    bus.instruments["GPIB0::12::INSTR"] = FlakyInstrument(
        inst.idn, replies, temp=inst.temp, units=inst.units)
    return bus


def test_a_status_reply_is_re_read_inside_one_poll_before_giving_up():
    """The gap this closed: a range switch shows dashes for a moment, and
    that used to cost a data point with no retry at all."""
    for key, module in CC_MODULES.items():
        assert module.CRYOCON_READ_RETRIES >= 1, key
        bus = _flaky_bus(["-------", "77.350K"])
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            value = backend.get_temperature("A")
        assert abs(value - 77.350) < 1e-9, (key, value)
        assert bus.cryocon.reads == 2, (key, bus.cryocon.reads)


def test_a_status_reply_that_never_clears_still_ends_as_nan():
    """Bounded, so the backend cannot block the worker for ever on a
    sensor that is genuinely gone."""
    for key, module in CC_MODULES.items():
        bus = _flaky_bus(["-------"] * 20)
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            value = backend.get_temperature("A")
        assert math.isnan(value), (key, value)
        assert bus.cryocon.reads == module.CRYOCON_READ_RETRIES + 1, (
            key, bus.cryocon.reads)
        assert "sensor" in (backend.last_status_error or "").lower(), key


def test_a_comm_glitch_and_a_status_reply_are_retried_the_same_way():
    for key, module in CC_MODULES.items():
        bus = _flaky_bus([FakeVisaTimeout(), "-------", "77.350K"])
        with patch_bus(module, bus):
            backend = _connected(module, bus)
            value = backend.get_temperature("A")
        assert abs(value - 77.350) < 1e-9, (key, value)


class RetryHarness:
    """The worker's CC34-8 loop on a scripted reading sequence, with no
    Tk and no hardware."""

    def __init__(self, module, readings):
        self.module = module
        cls = (sync_cc.PPMSSyncGUI if module is sync_cc
               else master_cc.PPMSMasterGUI)
        self._retry_invalid_reading = cls._retry_invalid_reading.__get__(self)
        self._validate_probe_reading = \
            cls._validate_probe_reading.__get__(self)
        self.readings = list(readings)
        self.reads = 0
        self.is_running = True
        self.msgs = []
        self.params = {"channel": "A"}
        self._last_temp = float("nan")
        self._glitch_candidate = None
        self._invalid_streak = 0
        self._invalid_recoveries = 0
        self._sensor_down_logged = False
        self.thermo_backend = self

    # -- the bits the loop leans on --
    def get_temperature(self, channel):
        self.reads += 1
        value = (self.readings.pop(0) if self.readings else float("nan"))
        if isinstance(value, Exception):
            raise value
        return value

    def _put_gui_msg(self, kind, **kw):
        self.msgs.append(kw.get("text", kind))

    def _process_cmd_queue(self):
        return not self.is_running


class fake_clock:
    """Virtual time for the retry loops: sleep ADVANCES the clock instead
    of being skipped, so a six-second window is exercised in a dozen
    iterations rather than millions of real ones."""

    def __init__(self, module, start=1000.0):
        self.module = module
        self.t = start

    def __enter__(self):
        self._old_time = self.module.time.time
        self._old_sleep = self.module.time.sleep

        def _sleep(seconds):
            self.t += float(seconds)

        self.module.time.time = lambda: self.t
        self.module.time.sleep = _sleep
        return self

    def __exit__(self, *exc):
        self.module.time.time = self._old_time
        self.module.time.sleep = self._old_sleep
        return False


def _no_sleep(module):
    return fake_clock(module)


def test_the_worker_re_reads_an_invalid_point_until_it_recovers():
    """A bad poll must not become a lost point when the next read is
    fine - which is the usual case for a transient fault."""
    for key, module in CC_MODULES.items():
        h = RetryHarness(module, [float("nan"), float("nan"), 77.35])
        with _no_sleep(module):
            temp, valid = h._retry_invalid_reading(float("nan"))
        assert valid is True, key
        assert abs(temp - 77.35) < 1e-9, (key, temp)
        assert h.reads == 3, (key, h.reads)
        assert h._invalid_streak == 0, key
        assert h._invalid_recoveries == 1, key
        assert any("recovered" in m for m in h.msgs), (key, h.msgs)


def test_the_retry_window_is_bounded_so_a_dead_sensor_cannot_stall_a_run():
    """Temperature is read between frequency points; an unbounded wait
    would stretch every sweep and desync the scanner from the PPMS."""
    for key, module in CC_MODULES.items():
        assert module.INVALID_READ_RETRY_S > 0, key
        h = RetryHarness(module, [float("nan")] * 500)
        with _no_sleep(module):
            temp, valid = h._retry_invalid_reading(float("nan"))
        assert valid is False, key
        assert math.isnan(temp), (key, temp)
        assert h._invalid_streak == 1, key
        expected = module.INVALID_READ_RETRY_S / module.INVALID_READ_POLL_S
        assert h.reads <= expected + 2, (key, h.reads, expected)


def test_a_sustained_fault_degrades_to_one_quick_re_read_per_poll():
    """After the streak the sensor is treated as down: still re-read on
    every poll, but no longer paying the full window on every point."""
    for key, module in CC_MODULES.items():
        h = RetryHarness(module, [float("nan")] * 5000)
        with _no_sleep(module):
            for _ in range(module.INVALID_READ_STREAK_MAX):
                h._retry_invalid_reading(float("nan"))
            before = h.reads
            h._retry_invalid_reading(float("nan"))
            degraded = h.reads - before
        assert h._sensor_down_logged is True, key
        assert degraded <= 3, (key, degraded)
        assert any("treating the sensor as down" in m for m in h.msgs), key


def test_a_sensor_that_comes_back_resets_the_degraded_state_by_itself():
    """Nobody has to be in the lab for the readings to resume."""
    for key, module in CC_MODULES.items():
        h = RetryHarness(module, [float("nan")] * 5000)
        with _no_sleep(module):
            # The streak is counted on the way out, so the degraded state
            # is entered on the poll AFTER it reaches the maximum.
            for _ in range(module.INVALID_READ_STREAK_MAX + 1):
                h._retry_invalid_reading(float("nan"))
            assert h._sensor_down_logged is True, key
            h.readings = [77.35]
            temp, valid = h._retry_invalid_reading(float("nan"))
        assert valid is True, (key, temp)
        assert h._sensor_down_logged is False, key
        assert h._invalid_streak == 0, key
        assert any("reading again" in m for m in h.msgs), key


def test_stop_stays_responsive_while_re_reading():
    for key, module in CC_MODULES.items():
        h = RetryHarness(module, [float("nan")] * 500)
        h.is_running = False                 # Stop already requested
        with _no_sleep(module):
            temp, valid = h._retry_invalid_reading(float("nan"))
        assert valid is False, key
        assert h.reads == 0, (key, h.reads)


def test_a_comm_failure_mid_recovery_goes_back_to_the_reconnect_loop():
    """CC34-8 must not grow a second copy of the HARD-2 retry-forever
    logic; a comm error belongs to _reconnect_with_backoff."""
    for key, module in CC_MODULES.items():
        h = RetryHarness(module, [FakeVisaTimeout()] * 20)
        with _no_sleep(module):
            temp, valid = h._retry_invalid_reading(float("nan"))
        assert valid is False, key
        assert h.reads == 1, (key, h.reads)
        assert any("handing over" in m for m in h.msgs), (key, h.msgs)


def test_a_real_temperature_step_is_confirmed_by_the_re_read():
    """GLITCH-1 needs two agreeing readings to accept a big jump. The
    re-read supplies the second one at once, instead of costing a whole
    poll."""
    for key, module in CC_MODULES.items():
        h = RetryHarness(module, [305.0])
        h._last_temp = 80.0
        with _no_sleep(module):
            # First read: a 225 K jump, rejected and held as a candidate.
            assert h._validate_probe_reading(305.2) is False, key
            temp, valid = h._retry_invalid_reading(305.2)
        assert valid is True, (key, temp)
        assert abs(temp - 305.0) < 1e-9, (key, temp)


def test_comm_errors_still_retry_forever_not_just_within_the_window():
    """HARD-2 is untouched: the caller's reconnect loop runs while
    self.is_running, with no attempt ceiling."""
    import inspect
    for key, cls in (("sync", sync_cc.PPMSSyncGUI),
                     ("master", master_cc.PPMSMasterGUI)):
        assert "while self.is_running:" in inspect.getsource(
            cls._reconnect_with_backoff), key
        loop = inspect.getsource(cls._log_temperature_point)
        assert "_reconnect_with_backoff(" in loop, key
        assert "_retry_invalid_reading(" in loop, key


# ===========================================================================
# 4. The live GUI on the fake lab bus
# ===========================================================================

def _headless_root():
    """A real Tk root, or None when there is no display."""
    try:
        import tkinter as tk
    except ImportError:
        return None
    try:
        root = tk.Tk()
    except Exception:
        return None
    root.withdraw()
    return root


def _skip(reason):
    try:
        import pytest
        pytest.skip(reason)
    except ImportError:
        print(f"SKIP: {reason}")
    return None


GUI_CLASSES = {"sync": (sync_cc, "PPMSSyncGUI"),
               "master": (master_cc, "PPMSMasterGUI")}


def test_the_gui_scan_picks_the_cryocon_off_the_real_lab_bus():
    """The end of the address story: on this bus the factory address holds
    the Lakeshore, so only an identity-based scan can get it right."""
    for key, (module, cls_name) in GUI_CLASSES.items():
        root = _headless_root()
        if root is None:
            return _skip("no display for a Tk root")
        try:
            bus = FakeBus()
            with patch_bus(module, bus):
                gui = getattr(module, cls_name)(root)
                gui.thermo_backend.rm = bus
                gui.lcr_backend.rm = bus
                gui._scan_for_visa()
                assert gui.ls_cb.get().startswith("GPIB0::12::INSTR"), (
                    key, gui.ls_cb.get())
                assert gui.lcr_cb.get().startswith("GPIB0::17::INSTR"), (
                    key, gui.lcr_cb.get())
            # The serial port was listed but never opened, and nothing on
            # the bus was written to.
            assert "ASRL1::INSTR" not in bus.opened, (key, bus.opened)
            for res, inst in bus.instruments.items():
                assert inst.writes == [], (key, res, inst.writes)
        finally:
            try:
                root.destroy()
            except Exception:
                pass


def test_the_thermometer_panel_is_wired_for_a_cryocon():
    for key, (module, cls_name) in GUI_CLASSES.items():
        root = _headless_root()
        if root is None:
            return _skip("no display for a Tk root")
        try:
            gui = getattr(module, cls_name)(root)
            root.update_idletasks()
            assert tuple(gui.channel_cb["values"]) ==                 module.CRYOCON_INPUT_CHANNELS, key
            assert gui.channel_cb.get() == "A", key
            assert "Cryo-con" in root.title(), (key, root.title())
            assert "CC34" in gui.PROGRAM_VERSION, key
            log = gui.console.get("1.0", "end")
            assert "Cryo-con Model 34 is READ-ONLY" in log, (key, log[:400])
            assert "INPUT?" in log, (key, log[:400])
        finally:
            try:
                root.destroy()
            except Exception:
                pass


# ===========================================================================
# 5. Launcher registration
# ===========================================================================

def test_the_launcher_knows_both_siblings():
    from pica.main import PICALauncherApp
    paths = PICALauncherApp.SCRIPT_PATHS
    for script_key, expected in (
            ("PPMS Sync Freq. Scan (CC34)",
             "PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py"),
            ("PPMS Dielectric Master (CC34)",
             "PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py")):
        assert script_key in paths, script_key
        assert os.path.basename(paths[script_key]) == expected, script_key
        assert os.path.isfile(paths[script_key]), paths[script_key]
    # The Lakeshore entries must still be there and still point at the
    # untouched originals.
    assert os.path.basename(paths["PPMS Sync Freq. Scan"]) == \
        "PPMS_Sync_Freq_Scan_E4980A_GUI.py"
    assert os.path.basename(paths["PPMS Dielectric Master"]) == \
        "PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py"


def test_the_v2_catalogue_entries_resolve_to_real_scripts():
    from pica.main import PICALauncherApp
    from pica.main_v2 import CATALOG
    paths = PICALauncherApp.SCRIPT_PATHS
    labels = []
    for suite in CATALOG:
        for entry in suite["modules"]:
            labels.append(entry[0])
            assert entry[1] in paths, entry
    for wanted in ("Temp. Step Freq. Scan (PPMS, T Sensing, Cryocon 34)",
                   "Dielectric Master Tscan+Fscan (PPMS, Cryocon 34)",
                   "Temp. Step Freq. Scan (PPMS, T Sensing, L350)",
                   "Dielectric Master Tscan+Fscan (PPMS, L350)"):
        assert wanted in labels, wanted


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
