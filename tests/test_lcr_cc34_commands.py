"""Command-level verification of the three E4980A + Cryocon Model 34 modules
against their Lakeshore 350 bases (10 Sep 2026):

    pica/keysight/PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py
    pica/keysight/Temprature_Scan_Passive_CC34_E4980A_GUI.py
    pica/keysight/PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py

What is pinned down here:

  1. The E4980A side did not change in the port. The LCR backend of every
     CC34 file is byte-identical to its base, and every SCPI command it
     sends is one the E4980A User's Guide documents, with the documented
     argument syntax. ':FETC?' is parsed as '<A>,<B>,<status>'.
  2. The Cryocon side uses the right equivalent at every Lakeshore call
     site: KRDG? -> INPUT?, HTR? -> LOOP 1:OUTPWR?, RANGE 1,0 -> STOP. No
     KRDG?/SETP/RANGE/HTR? survives in a CC34 file, and no *RST, CONTROL,
     SETPT, RATE or RANGE write exists anywhere.
  3. The passive scan's Cryocon writes: a default session (checkbox off)
     writes nothing; the opt-in box sends exactly one STOP at Start; the
     400 K kill switch sends STOP unconditionally and never raises.
  4. Unattended hardening: a status reply is NaN, not a dead worker; a
     heater-read comm error reaches the reconnect loop; NaN temperatures
     do not freeze the plot axis; the kill/runtime handlers open no
     dialog and no longer print 'NoneType: None'.
  5. Header conventions: the Module line names the file.

No hardware: everything talks to fake instruments. Runnable as plain
Python as well as under pytest:
    python tests/test_lcr_cc34_commands.py
"""

import inspect
import io
import math
import os
import re
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import matplotlib                                             # noqa: E402
matplotlib.use("Agg")

KEYSIGHT = os.path.join(project_root, "pica", "keysight")


def _load(alias, filename):
    """Load a module from its file under a private name.

    Deliberately NOT `from pica.keysight import ...`: that caches the module
    under its package name at collection time, bound to the real tkinter,
    and tests/test_gui_modules_initialization.py later gets that cached
    copy instead of one imported under its tkinter mock and fails with
    "Too early to create variable".
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(KEYSIGHT, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


sync_ls = _load("lcr_cc34_test_sync_ls", "PPMS_Sync_Freq_Scan_E4980A_GUI.py")
sync_cc = _load("lcr_cc34_test_sync_cc",
                "PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py")
master_ls = _load("lcr_cc34_test_master_ls",
                  "PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py")
master_cc = _load("lcr_cc34_test_master_cc",
                  "PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py")
passive_ls = _load("lcr_cc34_test_passive_ls",
                   "Temprature_Scan_Passive_E4980A_GUI.py")
passive_cc = _load("lcr_cc34_test_passive_cc",
                   "Temprature_Scan_Passive_CC34_E4980A_GUI.py")

PAIRS = {
    "sync": (sync_ls, sync_cc),
    "passive": (passive_ls, passive_cc),
    "master": (master_ls, master_cc),
}
CC_FILES = {
    "sync": "PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py",
    "passive": "Temprature_Scan_Passive_CC34_E4980A_GUI.py",
    "master": "PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py",
}
LS_FILES = {
    "sync": "PPMS_Sync_Freq_Scan_E4980A_GUI.py",
    "passive": "Temprature_Scan_Passive_E4980A_GUI.py",
    "master": "PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py",
}


def _read(name):
    with io.open(os.path.join(KEYSIGHT, name), encoding="utf-8") as fh:
        return fh.read()


CC_SOURCES = {k: _read(v) for k, v in CC_FILES.items()}
LS_SOURCES = {k: _read(v) for k, v in LS_FILES.items()}


def _code_lines(source):
    """Executable lines only: no comments, no module docstring."""
    lines = source.splitlines()
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
# Fake instruments
# ===========================================================================

class FakeVisaTimeout(IOError):
    def __init__(self):
        super().__init__("VI_ERROR_TMO (-1073807339): Timeout expired "
                         "before operation completed.")


class FakeCryocon:
    """Answers the Cryocon subset the LCR modules use and records every
    write, so a stray one cannot hide."""

    def __init__(self, temp="77.350K", units="K", heater="12.5%",
                 idn="Cryocon Model 34, Rev 3.03A",
                 heater_query="HTRREAD?"):
        self.idn = idn
        self.temp = temp
        self.units = units
        self.heater = heater
        # Which heater read-back this firmware answers. The Model 34
        # manual documents LOOP:HTRREAD? and nothing else; anything else
        # is left unanswered, which on a real Cryo-con is a VISA timeout,
        # not an error string. None = no heater read-back at all.
        self.heater_query = heater_query
        self.writes = []
        self.queries = []
        self.closed = False
        self.timeout = None
        self.dead = False

    def write(self, command):
        if self.dead:
            raise FakeVisaTimeout()
        self.writes.append(command)

    def query(self, command):
        if self.dead:
            raise FakeVisaTimeout()
        self.queries.append(command)
        cmd = command.strip()
        if cmd == "*IDN?":
            return self.idn
        if cmd.startswith("INPUT") and cmd.endswith(":UNITS?"):
            return self.units
        if cmd.startswith("INPUT?"):
            return self.temp
        if cmd.startswith("LOOP"):
            if self.heater_query and cmd.endswith(self.heater_query):
                return self.heater
            raise FakeVisaTimeout()     # unknown mnemonic: no reply
        return "0"

    def clear(self):
        pass

    def close(self):
        self.closed = True


class FakeE4980A:
    """A Keysight E4980A that answers the setup and one-shot measurement
    traffic of the LCR backends."""

    def __init__(self, fetch=(1.0e3, -2.0e5, 0)):
        self.writes = []
        self.queries = []
        self.fetch = list(fetch)
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.closed = False

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        cmd = command.strip()
        if cmd == "*IDN?":
            return "Keysight Technologies,E4980A,MY46200001,A.06.17"
        if cmd == "*OPT?":
            return "0"
        if cmd == ":SYST:ERR?":
            return '+0,"No error"'
        if cmd == "*OPC?":
            return "1"
        if cmd == ":BIAS:VOLT?":
            return "0"
        return "0"

    def query_ascii_values(self, command):
        self.queries.append(command)
        return list(self.fetch)

    def close(self):
        self.closed = True


class FakeBus:
    def __init__(self, cryocon=None, lcr=None):
        self.instruments = {
            "GPIB0::23::INSTR": cryocon or FakeCryocon(),
            "GPIB0::17::INSTR": lcr or FakeE4980A(),
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
        return self.instruments["GPIB0::23::INSTR"]

    @property
    def lcr(self):
        return self.instruments["GPIB0::17::INSTR"]


class patch_bus:
    """Swap in the fake bus and make time.sleep free."""

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


def _fake_link(cryocon):
    """A CryoconLink of the passive scan bound to a fake instrument,
    without opening anything."""
    link = object.__new__(passive_cc.CryoconLink)
    link.address = "GPIB0::23::INSTR"
    link.timeout_ms = 10000
    link.instrument = cryocon
    link.idn = cryocon.idn
    link._log = lambda msg: None
    link._last_io = 0.0
    return link


def _passive_backend(cryocon):
    backend = object.__new__(passive_cc.Cryocon34_Backend)
    backend.channel = "A"
    backend.log = lambda msg: None
    backend.link = _fake_link(cryocon)
    backend.idn = cryocon.idn
    backend.status_reports = 0
    backend.heater_query = None
    backend.heater_probed = False
    return backend


# ===========================================================================
# 1. The E4980A side is untouched and manual-conformant
# ===========================================================================

def test_the_lcr_backend_is_byte_identical_to_the_base_in_every_pair():
    for key, (ls, cc) in PAIRS.items():
        ls_src = inspect.getsource(ls.LCR_Backend)
        cc_src = inspect.getsource(cc.LCR_Backend)
        assert ls_src == cc_src, f"{key}: LCR_Backend diverged from the base"


# Every E4980A command the backends send, with the argument syntax the
# User's Guide (Chapter 10, "SCPI Command Reference") documents for it.
E4980A_COMMAND_SYNTAX = [
    (r"\*IDN\?", "*IDN? (identification)"),
    (r"\*OPT\?", "*OPT? (installed options; 001 = DC bias)"),
    (r"\*RST; \*CLS", "IEEE 488.2 reset + clear"),
    (r":DISP:ENAB ON", ":DISPlay:ENABle {ON|OFF|1|0}"),
    (r":FUNC:IMP RX", ":FUNCtion:IMPedance[:TYPE] {...|RX|...}"),
    (r":APER \{p\['aper'\]\}", ":APERture {SHORt|MEDium|LONG}[,<numeric>]"),
    (r":FUNC:IMP:RANG:AUTO ON", ":FUNCtion:IMPedance:RANGe:AUTO {ON|OFF|1|0}"),
    (r":FORM ASC", ":FORMat[:DATA] {ASCii|REAL[,64]}"),
    (r":FUNC:SMON:VAC ON", ":FUNCtion:SMONitor:VAC[:STATe] {ON|OFF|1|0}"),
    (r":FUNC:SMON:IAC ON", ":FUNCtion:SMONitor:IAC[:STATe] {ON|OFF|1|0}"),
    (r":FUNC:SMON:VDC OFF", ":FUNCtion:SMONitor:VDC[:STATe] {ON|OFF|1|0}"),
    (r":FUNC:SMON:IDC OFF", ":FUNCtion:SMONitor:IDC[:STATe] {ON|OFF|1|0}"),
    (r":AMPL:ALC ON", ":AMPLitude:ALC {ON|OFF|1|0}"),
    (r":AMPL:ALC OFF", ":AMPLitude:ALC {ON|OFF|1|0}"),
    (r":CORR:LENG \{p\['cable_len'\]\}", ":CORRection:LENGth {0|1|2|4}"),
    (r":CORR:OPEN:STAT ON", ":CORRection:OPEN:STATe {ON|OFF|1|0}"),
    (r":CORR:SHOR:STAT ON", ":CORRection:SHORt:STATe {ON|OFF|1|0}"),
    (r":CORR:OPEN:STAT OFF", ":CORRection:OPEN:STATe {ON|OFF|1|0}"),
    (r":CORR:SHOR:STAT OFF", ":CORRection:SHORt:STATe {ON|OFF|1|0}"),
    (r":VOLT \{p\['ac_bias'\]\}", ":VOLTage[:LEVel] <numeric>"),
    (r":TRIG:SOUR BUS", ":TRIGger:SOURce {INTernal|HOLD|EXTernal|BUS}"),
    (r":INIT:CONT ON", ":INITiate:CONTinuous {ON|OFF|1|0}"),
    (r":BIAS:VOLT 0", ":BIAS:VOLTage[:LEVel] <numeric>"),
    (r":BIAS:VOLT \{target_v:\.3f\}", ":BIAS:VOLTage[:LEVel] <numeric>"),
    (r":BIAS:VOLT \{v:\.3f\}", ":BIAS:VOLTage[:LEVel] <numeric>"),
    (r":BIAS:VOLT \{p\['dc_bias'\]\}", ":BIAS:VOLTage[:LEVel] <numeric>"),
    (r":BIAS:STAT OFF", ":BIAS:STATe {ON|OFF|1|0}"),
    (r":BIAS:STAT ON", ":BIAS:STATe {ON|OFF|1|0}"),
    (r":FREQ \{freq\}", ":FREQuency[:CW] <numeric>"),
    (r":TRIG:IMM", ":TRIGger[:IMMediate]"),
    (r"\*OPC\?", "*OPC? (1 when all pending operations complete)"),
    (r":FETC\?", ":FETCh[:IMPedance][:FORMatted]? -> <A>,<B>,<status>"),
    (r":SYST:ERR\?", ":SYSTem:ERRor?"),
    (r":BIAS:VOLT\?", ":BIAS:VOLTage[:LEVel]?"),
    (r":DISP:PAGE MEAS", ":DISPlay:PAGE {MEASurement|...}"),
]

def test_every_e4980a_command_in_the_cc34_backends_is_documented():
    documented = [re.compile(p) for p, _ in E4980A_COMMAND_SYNTAX]
    for key, (_, cc) in PAIRS.items():
        src = inspect.getsource(cc.LCR_Backend)
        literals = set()
        for m in re.finditer(
                r"""(?:write|query|query_ascii_values)\(\s*f?(["'])(.+?)\1""",
                src):
            literals.add(m.group(2))
        assert literals, f"{key}: no SCPI literals found"
        for lit in literals:
            assert any(d.fullmatch(lit) for d in documented), \
                f"{key}: E4980A command {lit!r} is not in the documented set"


def test_the_gui_only_offers_manual_legal_aperture_and_cable_values():
    for key, src in CC_SOURCES.items():
        assert 'values=["SHOR", "MED", "LONG"]' in src, key
        assert 'values=["0", "1", "2", "4"]' in src, key


def test_fetc_reply_is_parsed_as_a_b_status():
    for key, (_, cc) in PAIRS.items():
        lcr = object.__new__(cc.LCR_Backend)
        lcr.instrument = FakeE4980A(fetch=(1.5e3, -2.5e5, 0))
        R, X, status = lcr.perform_measurement(1000.0, 0.0)
        assert (R, X, status) == (1.5e3, -2.5e5, 0), key
        # The trigger sequence the manual requires for :TRIG:SOUR BUS.
        assert lcr.instrument.writes == [":FREQ 1000.0", ":TRIG:IMM"], key
        assert lcr.instrument.queries == ["*OPC?", ":FETC?"], key
        # A non-zero status is carried, not dropped.
        lcr.instrument = FakeE4980A(fetch=(1.0, 2.0, 4))
        assert lcr.perform_measurement(1e4, 0.0)[2] == 4, key
        # A two-field reply (no status) degrades to status 0, not a crash.
        lcr.instrument = FakeE4980A(fetch=(1.0, 2.0))
        assert lcr.perform_measurement(1e4, 0.0)[2] == 0, key


def test_e4980a_setup_sequence_is_the_same_on_the_bus_in_every_pair():
    params = {"lcr_visa": "GPIB0::17::INSTR", "aper": "MED",
              "alc_enabled": True, "corr_enabled": True, "cable_len": "1",
              "ac_bias": 1.0, "dc_bias": 0.0, "sample_name": "x"}
    traffic = {}
    for key, (ls, cc) in PAIRS.items():
        for tag, mod in (("ls", ls), ("cc", cc)):
            bus = FakeBus()
            with patch_bus(mod, bus):
                lcr = mod.LCR_Backend()
                lcr.initialize_instrument(dict(params))
            traffic[(key, tag)] = (bus.lcr.writes, bus.lcr.queries)
        assert traffic[(key, "ls")] == traffic[(key, "cc")], key
    # And every module agrees on the E4980A setup, not just each pair.
    first = traffic[("sync", "cc")]
    for key in PAIRS:
        assert traffic[(key, "cc")] == first, key


# ===========================================================================
# 2. Cryocon equivalents at every Lakeshore call site
# ===========================================================================

LAKESHORE_ONLY = ("KRDG?", "SETP ", "RANGE ", "HTR?", "HTRSET", "RAMP ")
CRYOCON_FORBIDDEN_WRITES = ("*RST", "CONTROL", "SETPT", ":RATE", "LOOP 1:RANGE",
                            "SYSTEM:NVSAVE", "*CLS")


def _io_lines(src):
    """Executable lines that talk to an instrument."""
    for line in _code_lines(src):
        if ".write(" in line or ".query(" in line or "_query(" in line \
                or "_write(" in line:
            yield line


def test_no_lakeshore_command_survives_in_a_cc34_file():
    for key, src in CC_SOURCES.items():
        for line in _io_lines(src):
            for cmd in LAKESHORE_ONLY:
                assert cmd not in line, (key, cmd, line)


CRYOCON_CLASSES = {
    "sync": (sync_cc.Probe_Thermometer_Backend,),
    "master": (master_cc.Probe_Thermometer_Backend,),
    "passive": (passive_cc.CryoconLink, passive_cc.Cryocon34_Backend,
                passive_cc.Combined_Backend),
}


def test_the_only_cryocon_write_anywhere_is_the_passive_scans_stop():
    """The E4980A backend legitimately sends *RST; the Cryocon classes
    must never. On the Cryocon side the passive scan's STOP is the only
    write in all three files, and the two read-only PPMS programs have
    no write path at all."""
    for key, classes in CRYOCON_CLASSES.items():
        for cls in classes:
            src = inspect.getsource(cls)
            for line in _io_lines(src):
                for cmd in CRYOCON_FORBIDDEN_WRITES:
                    assert cmd not in line, (key, cls.__name__, cmd, line)
    for mod in (sync_cc, master_cc):
        assert not hasattr(mod.Probe_Thermometer_Backend, "write"), mod.__name__
        assert ".write(" not in inspect.getsource(mod.Probe_Thermometer_Backend)
        assert ".write(" not in inspect.getsource(mod.open_cryocon_session)
    assert passive_cc.CRYOCON_STOP_COMMAND == "STOP"
    writes = [line for cls in CRYOCON_CLASSES["passive"]
              for line in _io_lines(inspect.getsource(cls))
              if ".write(" in line]
    assert writes == ["self.instrument.write(command)",
                      "self.link.write(CRYOCON_STOP_COMMAND)"], writes


def test_temperature_is_read_with_input_query_in_every_cc34_module():
    for key, src in CC_SOURCES.items():
        assert 'INPUT? {ch}' in src, key
        assert 'INPUT {ch}:UNITS?' in src, key
    # The heater read-back the Model 34 manual documents (LOOP:HTRREAD?)
    # is the one the passive scan asks for first. OUTPWR? is in neither
    # the Model 34 nor the 24C manual and survives only as a fallback.
    assert passive_cc.CRYOCON_HEATER_QUERIES[0] == "HTRREAD?"
    assert "LOOP {loop}:{query}" in CC_SOURCES["passive"]


def test_the_passive_scan_reads_the_heater_like_the_base_read_htr():
    cryo = FakeCryocon(heater="12.5%")
    backend = _passive_backend(cryo)
    assert backend.get_heater_output() == 12.5
    # One probe, then the settled command; both are the documented one.
    assert cryo.queries == ["LOOP 1:HTRREAD?", "LOOP 1:HTRREAD?"]
    assert cryo.writes == []
    cryo.queries.clear()
    # A status reply is NaN (a heater that cannot be read is not a
    # reason to stop a dielectric scan) ...
    cryo.heater = "N/A"
    assert math.isnan(backend.get_heater_output())
    cryo.heater = "-------"
    assert math.isnan(backend.get_heater_output())
    # ... but a comm failure raises, exactly as 'HTR? 1' did in the
    # base, so the worker's reconnect loop sees it.
    cryo.dead = True
    try:
        backend.get_heater_output()
    except IOError:
        pass
    else:
        raise AssertionError("a heater-read comm error was swallowed")


def test_stop_is_written_paced_and_leaves_nothing_unread():
    cryo = FakeCryocon()
    backend = _passive_backend(cryo)
    backend.stop_control_loops()
    assert cryo.writes == ["STOP"]
    assert cryo.queries == []     # STOP has no reply; nothing read back


def test_a_default_session_writes_nothing_to_the_cryocon():
    """Checkbox off (the default): open, verify, sweep, close - zero writes."""
    bus = FakeBus()
    with patch_bus(passive_cc, bus):
        backend = passive_cc.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments({
            "cryocon_visa": "GPIB0::23::INSTR", "channel": "A",
            "stop_loops": False,
            "lcr_visa": "GPIB0::17::INSTR", "aper": "MED",
            "alc_enabled": False, "corr_enabled": False, "cable_len": "0",
            "ac_bias": 1.0, "dc_bias": 0.0, "sample_name": "x"})
        cycle = backend.measure_frequency_sweep([1e3, 1e4], 0.0)
        backend.close_instruments()
    assert bus.cryocon.writes == [], bus.cryocon.writes
    assert cycle["heater"] == 12.5
    assert [p[1] for p in cycle["points"]] == [1e3, 1e4]
    assert all(abs(p[0] - 77.350) < 1e-9 for p in cycle["points"])
    assert bus.cryocon.closed


def test_the_opt_in_checkbox_sends_exactly_one_stop_at_start():
    bus = FakeBus()
    with patch_bus(passive_cc, bus):
        backend = passive_cc.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments({
            "cryocon_visa": "GPIB0::23::INSTR", "channel": "A",
            "stop_loops": True,
            "lcr_visa": "GPIB0::17::INSTR", "aper": "MED",
            "alc_enabled": False, "corr_enabled": False, "cable_len": "0",
            "ac_bias": 1.0, "dc_bias": 0.0, "sample_name": "x"})
        backend.close_instruments()
    assert bus.cryocon.writes == ["STOP"], bus.cryocon.writes
    # Verified in Kelvin BEFORE the write, as the base ordered its checks.
    assert bus.cryocon.queries.index("INPUT A:UNITS?") < len(bus.cryocon.queries)


def test_the_checkbox_is_off_by_default_and_wired_into_params():
    src = CC_SOURCES["passive"]
    assert "self.var_stop_loops = tk.BooleanVar(value=False)" in src
    assert "'stop_loops':    self.var_stop_loops.get()" in src
    assert "parameters.get('stop_loops', False)" in src


def test_the_400k_kill_switch_sends_stop_regardless_of_the_checkbox():
    cryo = FakeCryocon()
    backend = object.__new__(passive_cc.Combined_Backend)
    backend.cryocon = _passive_backend(cryo)
    backend.params = {"stop_loops": False}
    assert backend.check_safety_kill(399.9) is False
    assert cryo.writes == []
    assert backend.check_safety_kill(400.0) is True
    assert cryo.writes == ["STOP"]


def test_a_failed_kill_write_is_retried_three_times_and_never_raises():
    cryo = FakeCryocon()
    cryo.dead = True
    backend = object.__new__(passive_cc.Combined_Backend)
    backend.cryocon = _passive_backend(cryo)
    backend.params = {}
    old_sleep = passive_cc.time.sleep
    passive_cc.time.sleep = lambda *_a, **_k: None
    try:
        assert backend.check_safety_kill(450.0) is True
    finally:
        passive_cc.time.sleep = old_sleep


def test_the_kill_switch_matches_the_base_call_sites():
    """The base checks the limit inside the sweep and again on the cycle
    maximum; the port keeps both call sites."""
    for mod in (passive_ls, passive_cc):
        sweep = inspect.getsource(mod.Combined_Backend.measure_frequency_sweep)
        assert "check_safety_kill" in sweep
        worker = inspect.getsource(mod.Integrated_CT_GUI._measurement_worker)
        assert "check_safety_kill" in worker


# ===========================================================================
# 3. Unattended hardening
# ===========================================================================

def test_a_sensor_fault_mid_sweep_is_nan_and_the_point_keeps_its_lcr_data():
    cryo = FakeCryocon(temp=".......")
    backend = object.__new__(passive_cc.Combined_Backend)
    backend.cryocon = _passive_backend(cryo)
    backend.lcr = object.__new__(passive_cc.LCR_Backend)
    backend.lcr.instrument = FakeE4980A(fetch=(3.0, 4.0, 0))
    backend.params = {}
    old_sleep = passive_cc.time.sleep
    passive_cc.time.sleep = lambda *_a, **_k: None
    try:
        cycle = backend.measure_frequency_sweep([1e3], 0.0)
    finally:
        passive_cc.time.sleep = old_sleep
    (temp, f, R, X, status), = cycle["points"]
    assert math.isnan(temp)
    assert (f, R, X, status) == (1e3, 3.0, 4.0, 0)
    assert cryo.writes == []


def test_a_nan_temperature_is_left_out_of_the_kill_check():
    src = inspect.getsource(passive_cc.Integrated_CT_GUI._measurement_worker)
    assert "if pt[0] == pt[0]" in src
    assert "real_temps and self.backend.check_safety_kill" in src


def test_the_worker_traceback_travels_with_the_exception():
    src = inspect.getsource(passive_cc.Integrated_CT_GUI._measurement_worker)
    assert "self.data_queue.put(('ERROR', e, traceback.format_exc()))" in src
    handler = inspect.getsource(passive_cc.Integrated_CT_GUI._handle_runtime_error)
    assert "format_exc" not in handler.replace(
        "format_exc() here would only print", "")


def test_the_terminal_handlers_open_no_dialog():
    for name in ("_handle_kill_event", "_handle_runtime_error",
                 "_measurement_worker", "_reconnect_with_backoff",
                 "_process_data_queue"):
        src = inspect.getsource(getattr(passive_cc.Integrated_CT_GUI, name))
        assert "messagebox" not in src, name
    for mod in (sync_cc, master_cc):
        for name in ("_log_temperature_point", "_reconnect_with_backoff",
                     "_retry_invalid_reading", "_hardware_worker_loop"):
            src = inspect.getsource(getattr(mod.PPMSSyncGUI
                                            if hasattr(mod, "PPMSSyncGUI")
                                            else _gui_class(mod), name))
            assert "messagebox" not in src, (mod.__name__, name)


def _gui_class(mod):
    for name, obj in vars(mod).items():
        if inspect.isclass(obj) and hasattr(obj, "_hardware_worker_loop"):
            return obj
    raise AssertionError(f"{mod.__name__}: no worker GUI class")


def test_the_kill_and_error_handlers_still_stop_and_beep():
    class FakeLog:
        def __init__(self):
            self.lines = []
            self.stops = []
            self.beeps = []
            self.backend = passive_cc.Combined_Backend.__new__(
                passive_cc.Combined_Backend)

        def log(self, msg):
            self.lines.append(msg)

        def stop_measurement(self, from_user=True):
            self.stops.append(from_user)

        def _beep(self, times=1):
            self.beeps.append(times)

        def _update_live_plots(self, force=False):
            pass

    gui = FakeLog()
    passive_cc.Integrated_CT_GUI._handle_kill_event(gui)
    assert gui.stops == [False] and gui.beeps == [5]
    assert any("STOP" in line for line in gui.lines)
    gui = FakeLog()
    passive_cc.Integrated_CT_GUI._handle_runtime_error(gui, ValueError("boom"))
    assert gui.stops == [False] and gui.beeps == [3]
    assert any("ValueError: boom" in line for line in gui.lines)
    assert not any("NoneType: None" in line for line in gui.lines)


def test_nan_temperatures_do_not_freeze_the_x_axis():
    class FakeAxis:
        def __init__(self):
            self.xlim = None

        def set_xlim(self, lo, hi):
            self.xlim = (lo, hi)

    gui = object.__new__(passive_cc.Integrated_CT_GUI)
    gui.plot_freq = 1e3
    gui.ax_main = FakeAxis()
    gui._apply_y_scale = lambda ax, values, key: None
    nan = float("nan")
    gui.data_storage = {"cp": {1e3: {"v": [1.0, 2.0, 3.0],
                                     "T": [nan, 80.0, 90.0]}}}
    passive_cc.Integrated_CT_GUI._rescale_main_axis(gui)
    assert gui.ax_main.xlim is not None
    lo, hi = gui.ax_main.xlim
    assert lo < 80.0 and hi > 90.0
    # The base's own logic on the same clean data gives the same limits.
    gui.data_storage["cp"][1e3]["T"] = [80.0, 90.0]
    gui.ax_main = FakeAxis()
    passive_cc.Integrated_CT_GUI._rescale_main_axis(gui)
    assert gui.ax_main.xlim == (lo, hi)


def test_hardening_helpers_survived_the_port():
    for key, src in CC_SOURCES.items():
        assert "os.fsync(" in src, key
        assert "SetThreadExecutionState" in src, key
        assert "_reconnect_with_backoff" in src, key


# ===========================================================================
# 4. Header conventions
# ===========================================================================

def test_the_module_line_names_the_file():
    for key, name in CC_FILES.items():
        assert f"Module:             {name}" in CC_SOURCES[key].splitlines()[1] \
            or f"Module: {name}" in CC_SOURCES[key].splitlines()[1], (key, name)


def test_the_version_line_marks_the_cc34_sibling():
    assert "1.8-PPMS-Sync-CC34" in CC_SOURCES["sync"]
    assert '"1.5-CC34"' in CC_SOURCES["master"]
    assert "V: 1.7" in CC_SOURCES["passive"]
    assert '"1.7-CC34"' in CC_SOURCES["passive"]


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
