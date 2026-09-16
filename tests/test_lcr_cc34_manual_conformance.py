"""Manual-conformance tests for the three E4980A + Cryocon Model 34
modules (17 Sep 2026):

    pica/keysight/Temprature_Scan_Passive_CC34_E4980A_GUI.py
    pica/keysight/PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py
    pica/keysight/PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py

WHY THIS FILE EXISTS, separately from test_lcr_cc34_commands.py:

    The fakes in the other CC34 test files were written to answer what
    the modules ask. A fake built that way agrees with the code by
    construction and can never disagree with the instrument, so it let
    'LOOP 1:OUTPWR?' through for weeks. OUTPWR is in NEITHER the Cryo-con
    Model 34 manual nor the 24C manual - the Model 34's own Remote
    Command Summary lists exactly one heater read-back, LOOP:HTRREAD?
    ("Queries the output current of the selected control loop", percent
    of full scale, example reply '22%'). An unrecognised command on a
    Cryo-con is not answered with an error string, it is not answered at
    all, so the call would have come back as a VISA timeout on EVERY
    sweep of an overnight run and fed the worker's retry-forever
    reconnect loop.

    So the fake here is the other way round: STRICT_MODEL_34 is a
    whitelist taken from the manual, and the fake Cryo-con times out on
    anything that is not on it. A command the manual does not document
    fails the test instead of passing it.

The E4980A side is covered the same way in test_lcr_cc34_commands.py,
against the E4980A User's Guide Chapter 10.

No hardware. Runnable as plain Python as well as under pytest:
    python tests/test_lcr_cc34_manual_conformance.py
"""

import importlib.util
import inspect
import math
import os
import re
import sys
import tempfile

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import matplotlib                                             # noqa: E402
matplotlib.use("Agg")

KEYSIGHT = os.path.join(project_root, "pica", "keysight")


def _load(alias, filename):
    """Load a module from its file under a private alias.

    Deliberately not `from pica.keysight import ...`: that caches the
    module under its package name bound to the real tkinter, and the GUI
    initialisation tests later get that cached copy instead of one
    imported under their tkinter mock.
    """
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(KEYSIGHT, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


CC_FILES = {
    "passive": "Temprature_Scan_Passive_CC34_E4980A_GUI.py",
    "sync": "PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py",
    "master": "PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py",
}
MODULES = {key: _load("cc34_manual_" + key, name)
           for key, name in CC_FILES.items()}
passive = MODULES["passive"]
CC_SOURCES = {
    key: open(os.path.join(KEYSIGHT, name), encoding="utf-8").read()
    for key, name in CC_FILES.items()
}


# ===========================================================================
# The manual, as a whitelist
# ===========================================================================
#
# Every entry is a command the Cryo-con Model 34 manual documents, with
# the section it is documented in. Nothing else may reach the instrument.
# Source: "Cryo-con Model 34" manual (the file shipped as
# "The User Interface - Cryogenic Control Systems, Inc..pdf"), chapter
# "Remote Operation" and the Remote Command Summary at the back.

STRICT_MODEL_34 = {
    # IEEE common commands
    r"\*IDN\?":
        "*IDN? -> 'Cryocon Model 34 Rev <fw><hw>'",
    r"\*OPC\?":
        "*OPC? -> ASCII '1' when pending operations have completed",

    # Input channel commands. <channel> may be 0-3, CHA-CHD or A-D.
    r"INPUT\? [A-D]":
        "INPUT? <channel> -> temperature in the CHANNEL's display units",
    r"INPUT [A-D]:UNITS\?":
        "INPUT <channel>:UNITS? -> K | C | F | V | O",

    # Control loop commands.
    r"LOOP [12]:HTRREAD\?":
        "LOOP <n>:HTRREAD? -> loop output current, percent of full scale",

    # Control loop start/stop.
    r"STOP":
        "STOP -> disengage both control loops and disconnect the heater",
}

# Documented by neither the Model 34 nor the 24C manual. Tried once, at
# Start, only as a fallback for a firmware that answers the older
# mnemonic - never as the first or the only thing asked for.
TOLERATED_FALLBACKS = {
    r"LOOP [12]:OUTPWR\?":
        "not in the Model 34 or 24C manual; fallback probe only",
}

_STRICT = [re.compile(p) for p in STRICT_MODEL_34]
_FALLBACK = [re.compile(p) for p in TOLERATED_FALLBACKS]


def _is_documented(command):
    return any(p.fullmatch(command.strip()) for p in _STRICT)


def _is_tolerated(command):
    return any(p.fullmatch(command.strip()) for p in _FALLBACK)


# ===========================================================================
# Fake instruments
# ===========================================================================

class FakeVisaTimeout(IOError):
    """What a Cryo-con gives you for a command it does not recognise:
    no reply at all, so the read times out."""

    def __init__(self, command=""):
        super().__init__(
            "VI_ERROR_TMO (-1073807339): Timeout expired before operation "
            f"completed. (no reply to {command!r})")


class StrictCryocon:
    """A Model 34 that answers ONLY what its manual documents.

    `extra` adds mnemonics this particular firmware also happens to
    answer, so a unit that knows the older OUTPWR? can be simulated
    without pretending the manual documents it.
    """

    def __init__(self, temp="77.3500", units="K", heater="22%",
                 idn="Cryocon Model 34, Rev 3.03A", extra=(),
                 heater_query="HTRREAD?"):
        self.idn = idn
        self.temp = temp
        self.units = units
        self.heater = heater
        self.heater_query = heater_query    # None = no read-back at all
        self.extra = tuple(extra)
        self.writes = []
        self.queries = []
        self.rejected = []
        self.closed = False
        self.timeout = None
        self.dead = False

    # -- bus --

    def _check(self, command):
        cmd = command.strip()
        if self.dead:
            raise FakeVisaTimeout(cmd)
        if _is_documented(cmd) or any(e in cmd for e in self.extra):
            return cmd
        self.rejected.append(cmd)
        raise FakeVisaTimeout(cmd)

    def write(self, command):
        cmd = self._check(command)
        self.writes.append(cmd)

    def query(self, command):
        cmd = self._check(command)
        self.queries.append(cmd)
        if cmd == "*IDN?":
            return self.idn
        if cmd == "*OPC?":
            return "1"
        if cmd.endswith(":UNITS?"):
            return self.units
        if cmd.startswith("INPUT?"):
            return self.temp
        if cmd.startswith("LOOP"):
            if self.heater_query and cmd.endswith(self.heater_query):
                return self.heater
            self.rejected.append(cmd)
            raise FakeVisaTimeout(cmd)
        raise AssertionError(f"whitelisted but unanswered: {cmd!r}")

    def clear(self):
        pass

    def close(self):
        self.closed = True


class FakeE4980A:
    """Answers the setup and one-shot measurement traffic of the LCR
    backends. The E4980A side is checked against its own manual in
    test_lcr_cc34_commands.py; here it only has to be plausible."""

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
        self.cryocon = cryocon or StrictCryocon()
        self.lcr = lcr or FakeE4980A()
        self.instruments = {
            "GPIB0::23::INSTR": self.cryocon,
            "GPIB0::17::INSTR": self.lcr,
        }
        self.opened = []

    def list_resources(self):
        return tuple(self.instruments)

    def open_resource(self, resource, **kwargs):
        self.opened.append(resource)
        if resource not in self.instruments:
            raise FakeVisaTimeout(resource)
        return self.instruments[resource]


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


PASSIVE_PARAMS = {
    "cryocon_visa": "GPIB0::23::INSTR", "channel": "A", "stop_loops": False,
    "lcr_visa": "GPIB0::17::INSTR", "aper": "MED", "alc_enabled": False,
    "corr_enabled": False, "cable_len": "0", "ac_bias": 1.0,
    "dc_bias": 0.0, "sample_name": "conformance",
}


# ===========================================================================
# 1. Static: no undocumented Cryo-con mnemonic is even written down
# ===========================================================================

# The Cryo-con side of each module, by the name it carries there. The
# E4980A classes are deliberately left out: their commands are checked
# against the E4980A User's Guide in test_lcr_cc34_commands.py.
CRYOCON_CLASSES = {
    "passive": ("CryoconLink", "Cryocon34_Backend"),
    "sync": ("Probe_Thermometer_Backend",),
    "master": ("Probe_Thermometer_Backend",),
}


def _cryocon_sources(key):
    """Source text of the Cryo-con classes of one module."""
    module = MODULES[key]
    for name in CRYOCON_CLASSES[key]:
        yield name, inspect.getsource(getattr(module, name))


def test_every_cryocon_mnemonic_in_the_sources_is_in_the_manual():
    """Scrape the Cryo-con command literals out of all three modules and
    hold each one against the Model 34 manual."""
    # 'INPUT? {ch}' style f-string literals, with the placeholders filled
    # in with the values the modules can actually produce.
    substitutions = {
        "{ch}": "A", "{channel}": "A", "{loop}": "1",
        "{CRYOCON_HEATER_LOOP}": "1",
    }
    pattern = re.compile(
        r"""(?:write|query|_write|_query)\(\s*f?(["'])([^"']+)\1""")
    seen = {}
    for key in CC_FILES:
        for name, src in _cryocon_sources(key):
            for m in pattern.finditer(src):
                filled = m.group(2)
                for placeholder, value in substitutions.items():
                    filled = filled.replace(placeholder, value)
                if "{" in filled:
                    continue                  # built at run time; see part 2
                seen.setdefault(filled, set()).add(f"{key}.{name}")
    assert seen, "no Cryo-con command literals found in the CC34 sources"
    for command, keys in sorted(seen.items()):
        assert _is_documented(command) or _is_tolerated(command), (
            f"{sorted(keys)}: {command!r} is not a documented Cryo-con "
            "Model 34 command")


def test_outpwr_is_never_the_command_a_module_reaches_for_first():
    """The regression this file was written for. OUTPWR? may exist as a
    fallback; it may not be the first or the only heater read-back."""
    queries = passive.CRYOCON_HEATER_QUERIES
    assert queries[0] == "HTRREAD?", queries
    assert "OUTPWR?" in queries, queries    # kept, but only as a fallback
    for key, src in CC_SOURCES.items():
        if key == "passive":
            continue
        assert "OUTPWR" not in src, (
            f"{key} reads a heater at all - it is supposed to be a "
            "read-only thermometer link")


def test_the_manual_quote_is_in_the_source_so_the_next_reader_sees_it():
    src = CC_SOURCES["passive"]
    assert "HTRREAD" in src
    assert "Model 34 manual" in src
    assert "full scale" in src


# ===========================================================================
# 2. Live: a strict Model 34 answers every module end to end
# ===========================================================================

def test_the_passive_scan_runs_against_a_manual_only_model_34():
    bus = FakeBus()
    with patch_bus(passive, bus):
        backend = passive.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments(dict(PASSIVE_PARAMS))
        cycle = backend.measure_frequency_sweep([1e3, 1e4], 0.0)
        backend.close_instruments()
    assert bus.cryocon.rejected == [], bus.cryocon.rejected
    assert bus.cryocon.writes == [], bus.cryocon.writes
    assert cycle["heater"] == 22.0          # '22%' from the manual's example
    assert [p[1] for p in cycle["points"]] == [1e3, 1e4]
    assert all(abs(p[0] - 77.35) < 1e-9 for p in cycle["points"])


def test_every_query_the_passive_scan_makes_is_a_documented_one():
    bus = FakeBus()
    with patch_bus(passive, bus):
        backend = passive.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments(dict(PASSIVE_PARAMS))
        backend.measure_frequency_sweep([1e3], 0.0)
        backend.close_instruments()
    for command in bus.cryocon.queries:
        assert _is_documented(command), command


def test_the_stop_at_start_box_sends_only_the_documented_stop():
    bus = FakeBus()
    params = dict(PASSIVE_PARAMS, stop_loops=True)
    with patch_bus(passive, bus):
        backend = passive.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments(params)
        backend.close_instruments()
    assert bus.cryocon.writes == ["STOP"], bus.cryocon.writes
    assert bus.cryocon.rejected == [], bus.cryocon.rejected


def test_the_two_ppms_siblings_read_a_strict_model_34_without_writing():
    for key in ("sync", "master"):
        module = MODULES[key]
        bus = FakeBus()
        with patch_bus(module, bus):
            link = module.Probe_Thermometer_Backend()
            link.connect("GPIB0::23::INSTR")
            value = link.verify_channel("A")
            again = link.get_temperature("A")
            link.shutdown()
        assert abs(value - 77.35) < 1e-9, (key, value)
        assert abs(again - 77.35) < 1e-9, (key, again)
        assert bus.cryocon.writes == [], (key, bus.cryocon.writes)
        assert bus.cryocon.rejected == [], (key, bus.cryocon.rejected)
        for command in bus.cryocon.queries:
            assert _is_documented(command), (key, command)


# ===========================================================================
# 3. The heater probe: settled at Start, never a reconnect storm
# ===========================================================================

def _passive_cryocon(bus):
    """A connected Cryocon34_Backend on the fake bus."""
    return passive.Cryocon34_Backend(
        "GPIB0::23::INSTR", channel="A", log=lambda m: None)


def test_a_documented_firmware_settles_on_htrread_at_start():
    bus = FakeBus()
    with patch_bus(passive, bus):
        cryo = _passive_cryocon(bus)
        cryo.verify_channel()
        assert cryo.heater_probed is True
        assert cryo.heater_query == "HTRREAD?"
        bus.cryocon.queries.clear()
        assert cryo.get_heater_output() == 22.0
        cryo.close()
    assert bus.cryocon.queries == ["LOOP 1:HTRREAD?"], bus.cryocon.queries
    assert bus.cryocon.rejected == [], bus.cryocon.rejected


def test_an_older_firmware_that_only_knows_outpwr_is_still_read():
    """A unit that answers the undocumented mnemonic and not the
    documented one still gets its heater column - the fallback exists
    for exactly this - and it costs one extra query, once, at Start."""
    bus = FakeBus(cryocon=StrictCryocon(
        extra=("OUTPWR?",), heater_query="OUTPWR?"))
    with patch_bus(passive, bus):
        cryo = _passive_cryocon(bus)
        cryo.verify_channel()
        assert cryo.heater_query == "OUTPWR?", cryo.heater_query
        bus.cryocon.queries.clear()
        assert cryo.get_heater_output() == 22.0
        cryo.close()
    assert bus.cryocon.queries == ["LOOP 1:OUTPWR?"], bus.cryocon.queries


def test_a_controller_with_no_heater_readback_logs_nan_and_stops_asking():
    """THE regression. Before the fix the heater read was an
    unconditional 'LOOP 1:OUTPWR?' whose timeout raised out of
    get_heater_output() at the top of every sweep, straight into the
    worker's retry-forever reconnect loop - all night, over a logging
    column. Now the probe settles it once and the bus is left alone."""
    bus = FakeBus(cryocon=StrictCryocon(heater_query=None))
    with patch_bus(passive, bus):
        cryo = _passive_cryocon(bus)
        cryo.verify_channel()
        assert cryo.heater_probed is True
        assert cryo.heater_query is None
        before = len(bus.cryocon.queries)
        for _ in range(5):
            assert math.isnan(cryo.get_heater_output())
        cryo.close()
    # Not one further byte on the bus for the heater after the probe.
    assert len(bus.cryocon.queries) == before, bus.cryocon.queries[before:]


def test_a_heaterless_controller_does_not_stall_a_whole_sweep():
    """The same thing one level up: the sweep still returns points, with
    a NaN heater, instead of raising into the reconnect loop."""
    bus = FakeBus(cryocon=StrictCryocon(heater_query=None))
    with patch_bus(passive, bus):
        backend = passive.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments(dict(PASSIVE_PARAMS))
        cycle = backend.measure_frequency_sweep([1e3, 1e4], 0.0)
        backend.close_instruments()
    assert math.isnan(cycle["heater"])
    assert [p[1] for p in cycle["points"]] == [1e3, 1e4]
    assert all(abs(p[0] - 77.35) < 1e-9 for p in cycle["points"])


def test_the_probe_happens_at_start_not_in_the_middle_of_a_sweep():
    """verify_channel() runs at Start, where somebody is watching. By the
    time the first sweep asks for a heater number the guessing is over."""
    src = inspect.getsource(passive.Cryocon34_Backend.verify_channel)
    assert "probe_heater_command()" in src
    bus = FakeBus()
    with patch_bus(passive, bus):
        backend = passive.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments(dict(PASSIVE_PARAMS))
        assert backend.cryocon.heater_probed is True
        backend.close_instruments()


def test_a_real_comm_failure_after_the_probe_still_raises():
    """HARD-2 is not weakened by the fix: once the mnemonic is settled, a
    dead bus is a dead bus and the reconnect loop must still see it."""
    bus = FakeBus()
    with patch_bus(passive, bus):
        cryo = _passive_cryocon(bus)
        cryo.verify_channel()
        assert cryo.heater_query == "HTRREAD?"
        bus.cryocon.dead = True
        try:
            cryo.get_heater_output()
        except IOError:
            pass
        else:
            raise AssertionError("a heater-read comm error was swallowed")


def test_a_loop_that_reports_a_status_string_is_nan_not_a_raise():
    """A heater number that cannot be read is not a reason to interrupt a
    dielectric measurement."""
    bus = FakeBus(cryocon=StrictCryocon(heater="N/A"))
    with patch_bus(passive, bus):
        cryo = _passive_cryocon(bus)
        cryo.verify_channel()
        assert math.isnan(cryo.get_heater_output())
        bus.cryocon.instrument_heater = "-------"
        bus.cryocon.heater = "-------"
        assert math.isnan(cryo.get_heater_output())
        cryo.close()


def test_a_nack_is_a_rejection_not_an_answer():
    """Some Cryo-con firmware answers a command it does not take with
    'NACK' instead of silence. Settling on that query would mean a NaN
    heater column AND a pointless query on every sweep for the rest of
    the run, so a NACK moves the probe on to the next form."""
    bus = FakeBus(cryocon=StrictCryocon(
        extra=("OUTPWR?",), heater="NACK", heater_query="HTRREAD?"))
    with patch_bus(passive, bus):
        cryo = _passive_cryocon(bus)
        cryo.verify_channel()
        # HTRREAD? said NACK; OUTPWR? is not answered by this unit either.
        assert cryo.heater_query is None, cryo.heater_query
        before = len(bus.cryocon.queries)
        assert math.isnan(cryo.get_heater_output())
        cryo.close()
    assert len(bus.cryocon.queries) == before, bus.cryocon.queries[before:]


def test_the_probe_is_only_ever_run_once():
    bus = FakeBus()
    with patch_bus(passive, bus):
        cryo = _passive_cryocon(bus)
        cryo.verify_channel()
        bus.cryocon.queries.clear()
        for _ in range(4):
            cryo.get_heater_output()
        cryo.close()
    assert bus.cryocon.queries == ["LOOP 1:HTRREAD?"] * 4, bus.cryocon.queries


# ===========================================================================
# 4. What lands on disk
# ===========================================================================

def _run_gui_files(tmpdir, cryocon):
    """Drive the GUI's file setup with a connected backend, without ever
    building a Tk window."""
    bus = FakeBus(cryocon=cryocon)
    gui = object.__new__(passive.Integrated_CT_GUI)
    with patch_bus(passive, bus):
        gui.backend = passive.Combined_Backend(log=lambda m: None)
        gui.backend.initialize_instruments(dict(PASSIVE_PARAMS))
        gui.file_location_path = tmpdir
        gui.frequencies = [1000.0]
        gui.freq_filepaths = {}
        gui._create_per_frequency_files("conformance")
        gui.backend.close_instruments()
    return gui, bus


def test_the_t_log_header_names_the_query_that_actually_answered():
    with tempfile.TemporaryDirectory() as tmpdir:
        gui, _ = _run_gui_files(tmpdir, StrictCryocon())
        header = open(gui.t_log_path, encoding="utf-8").readline()
    assert "LOOP 1:HTRREAD?" in header, header
    assert "OUTPWR" not in header, header
    assert "input channel A" in header, header


def test_the_t_log_header_says_so_when_there_is_no_heater_readback():
    with tempfile.TemporaryDirectory() as tmpdir:
        gui, _ = _run_gui_files(tmpdir, StrictCryocon(heater_query=None))
        header = open(gui.t_log_path, encoding="utf-8").readline()
    assert "no heater read-back" in header, header
    assert "NaN" in header, header


def test_the_t_log_columns_are_unchanged():
    with tempfile.TemporaryDirectory() as tmpdir:
        gui, _ = _run_gui_files(tmpdir, StrictCryocon())
        lines = open(gui.t_log_path, encoding="utf-8").read().splitlines()
    assert lines[1] == "DateTime\tElapsed_s\tTemperature_K\tHeater_pct"


def test_the_per_frequency_files_keep_the_legacy_19_column_format():
    """The heater fix touched the T-log header. The per-frequency data
    files are the ones years of Origin templates read, so they must still
    be the Lakeshore base's 19 columns, byte for byte."""
    base = _load("cc34_manual_passive_base",
                 "Temprature_Scan_Passive_E4980A_GUI.py")
    expected = base.Integrated_CT_GUI.DATA_HEADER
    with tempfile.TemporaryDirectory() as tmpdir:
        gui, _ = _run_gui_files(tmpdir, StrictCryocon())
        path = list(gui.freq_filepaths.values())[0]
        header = open(path, encoding="utf-8").readline().rstrip("\n")
    assert header == expected, (header, expected)
    columns = header.split("\t")
    assert columns[0] == "Temperature", columns
    assert len(columns) == 19, columns


# ===========================================================================
# 5. All three are launchable, and the Lakeshore originals are untouched
# ===========================================================================

def test_all_three_cc34_lcr_modules_launch_from_both_launchers():
    from pica.main import PICALauncherApp
    from pica.main_v2 import CATALOG
    paths = PICALauncherApp.SCRIPT_PATHS
    keys = {
        "passive": "LCR Temp. Scan (T_Sensing, CC34)",
        "sync": "PPMS Sync Freq. Scan (CC34)",
        "master": "PPMS Dielectric Master (CC34)",
    }
    v2_keys = {entry[1] for suite in CATALOG for entry in suite["modules"]}
    for key, script_key in keys.items():
        assert script_key in paths, script_key
        assert os.path.basename(paths[script_key]) == CC_FILES[key], script_key
        assert os.path.isfile(paths[script_key]), paths[script_key]
        assert script_key in v2_keys, script_key


def test_the_lakeshore_originals_are_not_touched_by_the_heater_fix():
    """Three programs, three siblings. The Lakeshore files stay Lakeshore
    files: HTR? there, never a Cryo-con mnemonic."""
    for name in ("Temprature_Scan_Passive_E4980A_GUI.py",
                 "PPMS_Sync_Freq_Scan_E4980A_GUI.py",
                 "PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py"):
        src = open(os.path.join(KEYSIGHT, name), encoding="utf-8").read()
        assert "HTRREAD" not in src, name
        assert "OUTPWR" not in src, name
        assert "CRYOCON" not in src.upper(), name


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
