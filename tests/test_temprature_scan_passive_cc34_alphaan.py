"""Tests for novocontrol/Temprature_Scan_Passive_CC34_AlphaAN_GUI.py
(Alpha-AN + Cryocon Model 34, passive temperature monitor).

Two halves:

  * The Alpha side follows the Alpha-AN port rules (read-every-ack, MST
    written directly, ZG4 ceiling checked before any instrument is opened,
    f_actual used for the conversion, status 6 stops the run, reconnect =
    the Alpha backend's connect + initialize_instrument).
  * The Cryocon side is the E4980A source's, unchanged, and is held to the
    same rules as tests/test_lcr_cc34_commands.py and
    tests/test_lcr_cc34_manual_conformance.py: STOP is the only write (opt-in
    at Start, unconditional at 400 K), every query is one the Model 34
    manual documents or the lab unit was measured answering, the heater
    read-back is probed once at Start, a sensor fault is NaN and not a dead
    worker, and no unattended handler opens a dialog.

No hardware, VISA session or Tk window. Runnable as plain Python as well as
under pytest:
    python tests/test_temprature_scan_passive_cc34_alphaan.py
"""

import importlib.util
import inspect
import math
import os
import queue
import re
import sys
import tempfile
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    REPO_ROOT, FakeAlpha, load_port, run_common_backend_checks,
)

import matplotlib                                             # noqa: E402
matplotlib.use("Agg")

mod, PATH = load_port("Temprature_Scan_Passive_CC34_AlphaAN_GUI.py")
SOURCE = open(PATH, encoding="utf-8").read()

KEYSIGHT = os.path.join(REPO_ROOT, "pica", "keysight")


def _load_keysight(alias, filename):
    """The E4980A original, under a private alias (see the CC34 tests)."""
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(KEYSIGHT, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


orig = _load_keysight("cc34_alphaan_port_source",
                      "Temprature_Scan_Passive_CC34_E4980A_GUI.py")

CRYOCON_ADDR = "GPIB0::23::INSTR"
ALPHA_ADDR = "GPIB0::10::INSTR"


# ===========================================================================
# The Model 34 manual as a whitelist (from test_lcr_cc34_manual_conformance)
# ===========================================================================

STRICT_MODEL_34 = (
    r"\*IDN\?", r"\*OPC\?", r"INPUT\? [A-D]", r"INPUT [A-D]:UNITS\?",
    r"LOOP [12]:HTRREAD\?", r"STOP",
)
# Not in the manual, measured on the lab unit (log_25_09_26).
INSTRUMENT_VERIFIED = (r"HEATER:OUTPWR\?", r"HEATER:SETPT\?")
# Tried once at Start as a fallback probe only.
TOLERATED_FALLBACKS = (r"LOOP [12]:OUTPWR\?", r"HEATER:HTRREAD\?")

_DOCUMENTED = [re.compile(p) for p in STRICT_MODEL_34 + INSTRUMENT_VERIFIED]
_FALLBACK = [re.compile(p) for p in TOLERATED_FALLBACKS]


def _is_documented(command):
    return any(p.fullmatch(command.strip()) for p in _DOCUMENTED)


def _is_tolerated(command):
    return any(p.fullmatch(command.strip()) for p in _FALLBACK)


# ===========================================================================
# Fakes
# ===========================================================================

class FakeVisaTimeout(IOError):
    """How a Cryo-con refuses a command: no reply at all."""

    def __init__(self, command=""):
        super().__init__("VI_ERROR_TMO (-1073807339): Timeout expired "
                         f"before operation completed. ({command!r})")


class StrictCryocon:
    """A Model 34 that answers only documented / lab-verified commands and
    times out on anything else. Records every write and query."""

    def __init__(self, temp="77.3500", units="K", heater="22%",
                 idn="Cryocon Model 34, Rev 3.03A", extra=(),
                 heater_query="HEATER:OUTPWR?"):
        self.idn = idn
        self.temp = temp
        self.units = units
        self.heater = heater
        self.heater_query = heater_query
        self.extra = tuple(extra)
        self.writes = []
        self.queries = []
        self.rejected = []
        self.closed = False
        self.timeout = None
        self.dead = False

    def _check(self, command):
        cmd = command.strip()
        if self.dead:
            raise FakeVisaTimeout(cmd)
        if _is_documented(cmd) or any(e in cmd for e in self.extra):
            return cmd
        self.rejected.append(cmd)
        raise FakeVisaTimeout(cmd)

    def write(self, command):
        self.writes.append(self._check(command))

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
        if cmd.split(":")[0].rstrip("?").split()[0] in (
                "LOOP", "HEATER", "AOUT"):
            if self.heater_query and cmd == self.heater_query:
                return self.heater
            self.rejected.append(cmd)
            raise FakeVisaTimeout(cmd)
        raise AssertionError(f"whitelisted but unanswered: {cmd!r}")

    def clear(self):
        pass

    def close(self):
        self.closed = True


class FakeBus:
    """A ResourceManager with a Cryocon at 23 and an Alpha at 10."""

    def __init__(self, cryocon=None, alpha=None):
        self.cryocon = cryocon or StrictCryocon()
        self.alpha = alpha or FakeAlpha()
        self.instruments = {CRYOCON_ADDR: self.cryocon,
                            ALPHA_ADDR: self.alpha}
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

    def __init__(self, bus):
        self.bus = bus

    def __enter__(self):
        self._old = mod.pyvisa
        bus = self.bus

        class _FakeVisa:
            ResourceManager = staticmethod(lambda: bus)

        mod.pyvisa = _FakeVisa
        self._old_sleep = mod.time.sleep
        mod.time.sleep = lambda *_a, **_k: None
        return bus

    def __exit__(self, *exc):
        mod.pyvisa = self._old
        mod.time.sleep = self._old_sleep
        return False


def _params(**overrides):
    params = {
        "cryocon_visa": CRYOCON_ADDR, "channel": "A", "stop_loops": False,
        "lcr_visa": ALPHA_ADDR, "acv": 1.0, "mtm": 0.5, "wire_mode": "2",
        "frequencies": [1e3, 1e4], "delay": 0.0, "sample_name": "port",
    }
    params.update(overrides)
    return params


def _started(bus, **overrides):
    """A Combined_Backend initialised on the fake bus (call inside patch)."""
    backend = mod.Combined_Backend(log=lambda m: None)
    backend.initialize_instruments(_params(**overrides))
    return backend


def _fake_link(cryocon):
    link = object.__new__(mod.CryoconLink)
    link.address = CRYOCON_ADDR
    link.timeout_ms = 10000
    link.instrument = cryocon
    link.idn = cryocon.idn
    link._log = lambda msg: None
    link._last_io = 0.0
    return link


def _passive_cryocon(cryocon):
    backend = object.__new__(mod.Cryocon34_Backend)
    backend.channel = "A"
    backend.log = lambda msg: None
    backend.link = _fake_link(cryocon)
    backend.idn = cryocon.idn
    backend.status_reports = 0
    backend.heater_query = None
    backend.heater_probed = False
    return backend


def _code_lines(source):
    """Executable lines only: no comments, no docstring-only lines."""
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def _io_lines(src):
    for line in _code_lines(src):
        if (".write(" in line or ".query(" in line or "_query(" in line
                or "_write(" in line):
            yield line


# ===========================================================================
# 1. Alpha side
# ===========================================================================

def test_common_alpha_backend_rules():
    run_common_backend_checks(mod, PATH)


def test_default_session_runs_end_to_end_on_both_fakes():
    bus = FakeBus(alpha=FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1001.5 2 1",
                                            "ZRE=1E5 -2E6 10002.0 2 1"]}))
    with patch_bus(bus):
        backend = _started(bus)
        cycle = backend.measure_frequency_sweep([1e3, 1e4], 0.0)
        backend.close_instruments()
    (t1, f1, _, _, s1, fa1), (t2, f2, _, _, s2, fa2) = cycle["points"]
    assert abs(t1 - 77.35) < 1e-9 and abs(t2 - 77.35) < 1e-9
    assert (f1, f2) == (1e3, 1e4)              # keys the per-freq file
    assert (fa1, fa2) == (1001.5, 10002.0)     # used for the conversion
    assert s1 == s2 == mod.STATUS_RESULT_VALID
    assert cycle["heater"] == 22.0
    cmds = bus.alpha.commands
    assert cmds[:2] == ["*IDN?", "INTTYP?"], cmds
    assert "MODE=IMP" in cmds and "ACV=1" in cmds and "MTM=0.5" in cmds
    assert "FRS=2" in cmds
    assert bus.alpha.kind_of("MST") == "write"
    # Close parks the generator before the reset.
    tail = cmds[cmds.index("ACV=0"):]
    assert tail[:3] == ["ACV=0", "ZCONSPL=0", "*RST"], tail
    assert bus.alpha.closed and bus.cryocon.closed


def test_settings_are_validated_before_either_instrument_is_opened():
    bus = FakeBus()
    with patch_bus(bus):
        backend = mod.Combined_Backend(log=lambda m: None)
        try:
            backend.initialize_instruments(
                _params(frequencies=[5e6], acv=3.0))
        except ValueError as exc:
            assert "Vrms" in str(exc)
        else:
            raise AssertionError("3 Vrms at 5 MHz is over the ZG4 ceiling")
    assert bus.opened == [], bus.opened
    assert bus.alpha.commands == [] and bus.cryocon.queries == []
    assert backend.cryocon is None


def test_validator_refuses_bad_settings():
    good = dict(frequencies=[1e3], acv=1.0, mtm=0.5, wire_mode="2")
    mod.validate_alpha_parameters(**good)
    for bad in ({"frequencies": [30e6]}, {"acv": 0}, {"mtm": 0},
                {"wire_mode": "5"}, {"frequencies": []}):
        try:
            mod.validate_alpha_parameters(**{**good, **bad})
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_a_disconnected_signal_source_aborts_the_sweep():
    bus = FakeBus(alpha=FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 6 1"}))
    with patch_bus(bus):
        backend = _started(bus)
        try:
            backend.measure_frequency_sweep([1e3, 1e4], 0.0)
        except mod.AlphaFatalStatusError as exc:
            assert "status 6" in str(exc)
        else:
            raise AssertionError("status 6 must abort the sweep")
        finally:
            backend.close_instruments()
    assert bus.alpha.commands.count("MST") == 1


def _worker_gui(sweep_error):
    calls = {"reconnect": 0}

    class _Backend:
        params = {"delay": 0.0}

        def measure_frequency_sweep(self, *_a):
            raise sweep_error

        def check_safety_kill(self, _t):
            return False

    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.backend = _Backend()
    gui.is_running = True
    gui.stop_event = threading.Event()
    gui.data_queue = queue.Queue()
    gui.frequencies = [1e3]

    def _reconnect(attempt):
        calls["reconnect"] += 1
        return False                     # as if Stop was pressed

    gui._reconnect_with_backoff = _reconnect
    return gui, calls


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def test_status_6_stops_the_run_instead_of_reconnecting():
    gui, calls = _worker_gui(mod.AlphaFatalStatusError("status 6"))
    gui._measurement_worker()
    items = _drain(gui.data_queue)
    errors = [i for i in items if isinstance(i, tuple) and i[0] == "ERROR"]
    assert len(errors) == 1, items
    assert isinstance(errors[0][1], mod.AlphaFatalStatusError)
    assert "AlphaFatalStatusError" in errors[0][2]      # traceback travels
    assert calls["reconnect"] == 0


def test_an_alpha_refusal_stops_the_run_instead_of_reconnecting():
    """'CR' (no load-short cal) or a non-OK ack: a *RST cannot fix it."""
    gui, calls = _worker_gui(RuntimeError("Instrument returned 'CR'"))
    gui._measurement_worker()
    errors = [i for i in _drain(gui.data_queue)
              if isinstance(i, tuple) and i[0] == "ERROR"]
    assert len(errors) == 1 and calls["reconnect"] == 0


def test_a_comm_error_still_goes_to_the_reconnect_loop():
    gui, calls = _worker_gui(IOError("VI_ERROR_TMO"))
    gui._measurement_worker()
    items = _drain(gui.data_queue)
    assert calls["reconnect"] == 1
    assert not any(isinstance(i, tuple) and i[0] == "ERROR" for i in items)
    assert any(isinstance(i, str) and "COMM ERROR" in i for i in items)


def test_reconnect_is_the_alpha_backends_connect_plus_initialize():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus)
        bus.alpha.traffic.clear()
        bus.alpha.closed = False
        backend.reconnect()
        cmds = list(bus.alpha.commands)
        backend.close_instruments()
    # Old session parked (no *RST: that would take the Alpha off the bus
    # right before connect()), then a fresh handshake + setup.
    assert cmds[:2] == ["ACV=0", "ZCONSPL=0"], cmds
    rest = cmds[2:]
    assert rest[:2] == ["*IDN?", "INTTYP?"], rest            # connect()
    assert rest[2:5] == ["*RST", "*IDN?", "MODE=IMP"], rest  # initialize
    assert "ACV=1" in rest and "MTM=0.5" in rest
    # The reconnect does not send STOP when the box is off.
    assert bus.cryocon.writes == [], bus.cryocon.writes


def test_freq_tag_keeps_sub_hz_frequencies_apart():
    assert mod.freq_tag(0.1) == "0.1" and mod.freq_tag(0.5) == "0.5"
    assert mod.freq_tag(1000000) == "1000000"
    assert mod.freq_tag(1e3) == "1000" and mod.freq_tag(12.5) == "12.5"
    assert "int(f)" not in "".join(_code_lines(
        inspect.getsource(mod.Integrated_CT_GUI)))


def test_point_timeout_scales_with_mtm_and_period():
    base = mod.SRQ_TIMEOUT_MEASURE_S
    assert mod.point_timeout_s(1e3, 0.5) == base
    assert mod.point_timeout_s(0.001, 0.5) == 4.0 * 1000.0 + 60.0
    assert mod.point_timeout_s(1e3, 100.0) == 4.0 * 100.0 + 60.0
    backend, fake = _connected_alpha()
    backend.params = {"mtm": 100.0}
    backend.measure_point(1e3)
    assert ("wait_for_srq", 460000) in fake.traffic, fake.traffic


def _connected_alpha(fake=None):
    fake = fake or FakeAlpha()
    backend = mod.AlphaAN_Backend.__new__(mod.AlphaAN_Backend)
    backend.instrument = fake
    backend.params = {}
    backend.rm = None
    backend._task_may_be_running = False
    return backend, fake


def test_a_wrong_idn_gets_no_shutdown_commands():
    fake = FakeAlpha({"*IDN?": "KEITHLEY INSTRUMENTS,MODEL 2400"})
    bus = FakeBus(alpha=fake)
    with patch_bus(bus):
        backend = mod.AlphaAN_Backend()
        try:
            backend.connect(ALPHA_ADDR)
        except ConnectionError as exc:
            assert "not a Novocontrol Alpha" in str(exc)
        else:
            raise AssertionError("a non-Alpha IDN was accepted")
    assert fake.commands == ["*IDN?"], fake.commands
    assert fake.closed and backend.instrument is None


def test_non_valid_points_are_discarded_but_the_kill_row_is_kept():
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui._pending_rows = mod.deque()
    gui._write_error_logged = False
    lines = []
    gui.log = lines.append
    gui.var_plot_thin = type("V", (), {"get": lambda self: False})()
    gui.data_storage = {"time": [], "temperature": [],
                        "cp": {1e3: {"T": [], "v": []},
                               1e4: {"T": [], "v": []},
                               1e5: {"T": [], "v": []}},
                        "g": {1e3: {"T": [], "v": []},
                              1e4: {"T": [], "v": []},
                              1e5: {"T": [], "v": []}}}
    nan = float("nan")
    cycle = {"points": [(80.0, 1e3, 1e5, -2e6, 2, 1e3),
                        (80.1, 1e4, 1e5, -2e6, 4, 1e4),
                        (400.5, 1e5, nan, nan, -1, 1e5)],
             "heater": 0.0}
    with tempfile.TemporaryDirectory() as tmpdir:
        gui.freq_filepaths = {
            f: os.path.join(tmpdir, f"x-{int(f)}Hz_pica_format.dat")
            for f in (1e3, 1e4, 1e5)}
        gui.windeta_filepaths = {}
        gui._save_cycle_to_files(cycle)
        assert os.path.exists(gui.freq_filepaths[1e3])
        assert not os.path.exists(gui.freq_filepaths[1e4])
        assert os.path.exists(gui.freq_filepaths[1e5])     # kill row kept
    gui._update_data_storage(cycle, 1.0)
    assert gui.data_storage["cp"][1e3]["T"] == [80.0]
    assert gui.data_storage["cp"][1e4]["T"] == []
    assert gui.data_storage["cp"][1e5]["T"] == [400.5]
    # The warning names the discard; the kill row gets no warning.
    gui._save_cycle_to_files = gui._append_t_log = lambda *a: None
    gui._update_data_storage = gui._update_live_plots = lambda *a: None
    mod.Integrated_CT_GUI._process_cycle(gui, cycle, 1.0)
    warns = [m for m in lines if m.startswith("WARNING")]
    assert len(warns) == 1 and "Point discarded" in warns[0], warns


def _reconnect_gui(backend):
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.backend = backend
    gui.is_running = True
    gui.stop_event = threading.Event()
    gui.data_queue = queue.Queue()
    return gui


def test_an_alpha_refusal_during_reconnect_ends_the_run():
    class _Backend:
        calls = 0

        def reconnect(self):
            _Backend.calls += 1
            raise RuntimeError("Command 'MTM=0.5' failed: answered 'E3'")

    gui = _reconnect_gui(_Backend())
    old_wait = gui.stop_event.wait
    gui.stop_event.wait = lambda *_a: False          # no real backoff
    old_time = mod.time.time
    ticks = iter(range(0, 10 ** 6, 100))
    mod.time.time = lambda: next(ticks)
    try:
        gui._reconnect_with_backoff(1)
    except RuntimeError:
        pass
    else:
        raise AssertionError("an Alpha refusal must not be retried")
    finally:
        mod.time.time = old_time
        gui.stop_event.wait = old_wait
    assert _Backend.calls == 1


def test_a_bus_error_during_reconnect_is_retried():
    class _Backend:
        calls = 0

        def reconnect(self):
            _Backend.calls += 1
            if _Backend.calls < 3:
                raise TimeoutError("VI_ERROR_TMO")

    gui = _reconnect_gui(_Backend())
    gui.stop_event.wait = lambda *_a: False
    old_time = mod.time.time
    ticks = iter(range(0, 10 ** 6, 100))
    mod.time.time = lambda: next(ticks)
    try:
        assert gui._reconnect_with_backoff(1) is True
    finally:
        mod.time.time = old_time
    assert _Backend.calls == 3


def test_stop_during_a_reconnect_closes_the_fresh_sessions():
    class _Backend:
        closed = 0

        def reconnect(self):
            gui.stop_event.set()          # Stop pressed mid-reconnect

        def close_instruments(self):
            _Backend.closed += 1

    gui = _reconnect_gui(_Backend())
    real_wait = gui.stop_event.wait
    gui.stop_event.wait = lambda *_a: False
    old_time = mod.time.time
    ticks = iter(range(0, 10 ** 6, 100))
    mod.time.time = lambda: next(ticks)
    try:
        assert gui._reconnect_with_backoff(1) is False
    finally:
        mod.time.time = old_time
        gui.stop_event.wait = real_wait
    assert _Backend.closed == 1
    assert not any("Resuming" in str(i) for i in _drain(gui.data_queue))


def test_release_for_reconnect_parks_without_reset():
    backend, fake = _connected_alpha()
    backend.release_for_reconnect()
    assert fake.commands == ["ACV=0", "ZCONSPL=0"], fake.commands
    assert fake.closed and backend.instrument is None
    backend.release_for_reconnect()               # idempotent, no raise


def test_points_are_converted_at_f_actual_and_filed_by_requested_f():
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui._pending_rows = mod.deque()
    gui._write_error_logged = False
    gui.log = lambda msg: None
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "x-1000Hz_pica_format.dat")
        gui.freq_filepaths = {1000.0: path}
        gui.windeta_filepaths = {}
        cycle = {"points": [(80.0, 1000.0, 1.0e5, -2.0e6, 2, 1001.5)],
                 "heater": 0.0}
        gui._save_cycle_to_files(cycle)
        row = open(path, encoding="utf-8").read().split("\t")
    omega = float(row[16])            # Temperature + 18; Omega is calc[15]
    assert abs(omega - 2 * math.pi * 1001.5) < 1e-3, omega


def test_the_e4980a_only_fields_are_gone_and_wire_mode_is_offered():
    for gone in ("aper_combobox", "cable_len", "var_alc", "var_corr",
                 '"DC Bias"', "'dc_bias'", "perform_measurement",
                 "LCR_Backend"):
        assert gone not in SOURCE, gone
    assert "values=list(WIRE_MODES))" in SOURCE
    assert 'self.wire_combobox.set("2")' in SOURCE
    assert 'self.entries["MTM"].insert(0, "0.5")' in SOURCE


# ===========================================================================
# 2. Cryocon side: unchanged from the E4980A source
# ===========================================================================

def test_the_cryocon_side_is_byte_identical_to_the_e4980a_source():
    for name in ("CryoconLink", "Cryocon34_Backend", "CryoconStatusError",
                 "parse_cryocon_number", "is_cryocon_idn"):
        assert (inspect.getsource(getattr(mod, name))
                == inspect.getsource(getattr(orig, name))), name
    for name in dir(orig):
        if name.startswith(("CRYOCON_", "_CRYOCON_")) or \
                name == "ALLOW_DEVICE_CLEAR_ON_RETRY":
            assert getattr(mod, name) == getattr(orig, name), name
    assert (inspect.getsource(mod.Combined_Backend.check_safety_kill)
            == inspect.getsource(orig.Combined_Backend.check_safety_kill))
    assert mod.Combined_Backend.SAFETY_KILL_TEMP_K == 400.0


def test_the_only_cryocon_write_is_stop():
    forbidden = ("*RST", "CONTROL", "SETPT", ":RATE", "LOOP 1:RANGE",
                 "SYSTEM:NVSAVE", "*CLS")
    classes = (mod.CryoconLink, mod.Cryocon34_Backend)
    for cls in classes:
        for line in _io_lines(inspect.getsource(cls)):
            for cmd in forbidden:
                assert cmd not in line, (cls.__name__, cmd, line)
    writes = [line for cls in classes + (mod.Combined_Backend,)
              for line in _io_lines(inspect.getsource(cls))
              if ".write(" in line]
    assert writes == ["self.instrument.write(command)",
                      "self.link.write(CRYOCON_STOP_COMMAND)"], writes
    assert mod.CRYOCON_STOP_COMMAND == "STOP"


def test_no_lakeshore_command_survives():
    for line in _io_lines(SOURCE):
        for cmd in ("KRDG?", "SETP ", "RANGE ", "HTR?", "HTRSET", "RAMP "):
            assert cmd not in line, (cmd, line)


def test_every_cryocon_literal_is_in_the_manual_or_measured():
    pattern = re.compile(
        r"""(?:write|query|_write|_query)\(\s*f?(["'])([^"']+)\1""")
    seen = set()
    for cls in (mod.CryoconLink, mod.Cryocon34_Backend):
        for m in pattern.finditer(inspect.getsource(cls)):
            filled = m.group(2).replace("{ch}", "A").replace("{loop}", "1")
            if "{" not in filled:
                seen.add(filled)
    assert seen
    for command in seen:
        assert _is_documented(command) or _is_tolerated(command), command
    for candidate in mod.CRYOCON_HEATER_QUERIES:
        filled = candidate.replace("{loop}", "1")
        assert _is_documented(filled) or _is_tolerated(filled), candidate
    assert mod.CRYOCON_HEATER_QUERIES[0] == "HEATER:OUTPWR?"


def test_a_default_session_writes_nothing_and_asks_only_documented_queries():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus)
        backend.measure_frequency_sweep([1e3, 1e4], 0.0)
        backend.close_instruments()
    assert bus.cryocon.writes == [], bus.cryocon.writes
    assert bus.cryocon.rejected == [], bus.cryocon.rejected
    for command in bus.cryocon.queries:
        assert _is_documented(command), command
    assert "INPUT A:UNITS?" in bus.cryocon.queries


def test_the_opt_in_box_sends_exactly_one_stop_at_start():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus, stop_loops=True)
        backend.close_instruments()
    assert bus.cryocon.writes == ["STOP"], bus.cryocon.writes
    assert bus.cryocon.rejected == []


def test_the_stop_box_is_off_by_default_and_wired_into_params():
    assert "self.var_stop_loops = tk.BooleanVar(value=False)" in SOURCE
    assert "'stop_loops':    self.var_stop_loops.get()" in SOURCE
    assert "parameters.get('stop_loops', False)" in SOURCE


def test_the_400k_kill_switch_sends_stop_regardless_of_the_checkbox():
    cryo = StrictCryocon()
    backend = object.__new__(mod.Combined_Backend)
    backend.cryocon = _passive_cryocon(cryo)
    backend.params = {"stop_loops": False}
    assert backend.check_safety_kill(399.9) is False
    assert cryo.writes == []
    assert backend.check_safety_kill(400.0) is True
    assert cryo.writes == ["STOP"]


def test_the_kill_switch_inside_the_sweep_stops_before_measuring():
    bus = FakeBus(cryocon=StrictCryocon(temp="400.5"))
    with patch_bus(bus):
        backend = mod.Combined_Backend(log=lambda m: None)
        backend.cryocon = _passive_cryocon(bus.cryocon)
        backend.lcr.instrument = bus.alpha
        cycle = backend.measure_frequency_sweep([1e3, 1e4], 0.0)
    assert bus.cryocon.writes == ["STOP"]
    assert len(cycle["points"]) == 1
    temp, f, R, X, status, f_act = cycle["points"][0]
    assert temp == 400.5 and math.isnan(R) and status == -1 and f_act == f
    assert "MST" not in bus.alpha.commands
    for name in ("measure_frequency_sweep",):
        assert "check_safety_kill" in inspect.getsource(
            getattr(mod.Combined_Backend, name))
    assert "check_safety_kill" in inspect.getsource(
        mod.Integrated_CT_GUI._measurement_worker)


def test_a_failed_kill_write_is_retried_and_never_raises():
    cryo = StrictCryocon()
    cryo.dead = True
    backend = object.__new__(mod.Combined_Backend)
    backend.cryocon = _passive_cryocon(cryo)
    backend.params = {}
    old_sleep = mod.time.sleep
    mod.time.sleep = lambda *_a, **_k: None
    try:
        assert backend.check_safety_kill(450.0) is True
    finally:
        mod.time.sleep = old_sleep


def test_a_sensor_fault_is_nan_and_the_point_keeps_its_alpha_data():
    bus = FakeBus(cryocon=StrictCryocon(temp="......."))
    with patch_bus(bus):
        backend = mod.Combined_Backend(log=lambda m: None)
        backend.cryocon = _passive_cryocon(bus.cryocon)
        backend.lcr.instrument = bus.alpha
        cycle = backend.measure_frequency_sweep([1e3], 0.0)
    (temp, f, R, X, status, f_act), = cycle["points"]
    assert math.isnan(temp)
    assert (f, R, X, status, f_act) == (1e3, 1.0e5, -2.0e6, 2, 1e3)
    assert bus.cryocon.writes == []


def test_the_heater_probe_settles_once_at_start():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus)
        cryo = backend.cryocon
        assert cryo.heater_probed and cryo.heater_query == "HEATER:OUTPWR?"
        bus.cryocon.queries.clear()
        for _ in range(3):
            assert cryo.get_heater_output() == 22.0
        backend.close_instruments()
    assert bus.cryocon.queries == ["HEATER:OUTPWR?"] * 3


def test_a_heaterless_controller_logs_nan_and_stops_asking():
    bus = FakeBus(cryocon=StrictCryocon(heater_query=None))
    with patch_bus(bus):
        backend = _started(bus)
        before = len(bus.cryocon.queries)
        cycle = backend.measure_frequency_sweep([1e3], 0.0)
        heater_queries = [q for q in bus.cryocon.queries[before:]
                          if not q.startswith("INPUT")]
        backend.close_instruments()
    assert math.isnan(cycle["heater"])
    assert heater_queries == [], heater_queries


def test_a_heater_comm_failure_still_raises_for_the_reconnect_loop():
    cryo = StrictCryocon()
    backend = _passive_cryocon(cryo)
    assert backend.get_heater_output() == 22.0
    cryo.dead = True
    try:
        backend.get_heater_output()
    except IOError:
        pass
    else:
        raise AssertionError("a heater-read comm error was swallowed")


# ===========================================================================
# 3. Files, unattended policy, hardening
# ===========================================================================

def _gui_with_files(tmpdir, cryocon=None):
    bus = FakeBus(cryocon=cryocon)
    gui = object.__new__(mod.Integrated_CT_GUI)
    with patch_bus(bus):
        gui.backend = _started(bus)
        gui.file_location_path = tmpdir
        gui.frequencies = [1000.0]
        gui.freq_filepaths = {}
        gui.log = lambda msg: None
        params = _out_params(sample_name="port", frequencies=[1000.0])
        gui.c0_farads = mod.c0_from_params(params)
        gui._create_per_frequency_files(params)
        gui.backend.close_instruments()
    return gui


def test_the_data_files_keep_the_source_format_and_are_ascii():
    assert mod.Integrated_CT_GUI.DATA_HEADER == orig.Integrated_CT_GUI.DATA_HEADER
    with tempfile.TemporaryDirectory() as tmpdir:
        gui = _gui_with_files(tmpdir)
        data_raw = open(list(gui.freq_filepaths.values())[0], "rb").read()
        tlog_raw = open(gui.t_log_path, "rb").read()
    data_raw.decode("ascii")
    tlog_raw.decode("ascii")
    lines = data_raw.decode("ascii").splitlines()
    meta = [ln for ln in lines if ln.startswith("#")]
    header = lines[len(meta)]               # after the '#' metadata rows
    assert header == mod.Integrated_CT_GUI.DATA_HEADER
    assert len(header.split("\t")) == 19
    tlog = tlog_raw.decode("ascii").splitlines()
    assert "HEATER:OUTPWR?" in tlog[0] and "input channel A" in tlog[0]
    assert tlog[1] == "DateTime\tElapsed_s\tTemperature_K\tHeater_pct"


def test_the_t_log_says_so_when_there_is_no_heater_readback():
    with tempfile.TemporaryDirectory() as tmpdir:
        gui = _gui_with_files(tmpdir, StrictCryocon(heater_query=None))
        header = open(gui.t_log_path, encoding="utf-8").readline()
    assert "no heater read-back" in header and "NaN" in header


# --- Output formats: PICA (.dat) and WinDETA Tscan (.txt) ------------------

def _out_params(**over):
    p = {"sample_name": "S", "acv": 0.5, "mtm": 0.5, "delay": 0.0,
         "wire_mode": "2", "frequencies": [1000.0, 2000.0],
         "lcr_visa": ALPHA_ADDR, "cryocon_visa": CRYOCON_ADDR,
         "channel": "A", "stop_loops": False,
         "geometry_mode": "diameter", "diameter_mm": 9.68,
         "thickness_mm": 1.14,
         "temp_program": "Passive, read only: Cryocon 34 input A",
         "save_pica": True, "save_windeta": True}
    p.update(over)
    return p


def _files(tmp, **over):
    """A GUI shell with its per-frequency files created in tmp. The T-log
    header only needs the Cryocon's heater query and the channel."""
    params = _out_params(**over)
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.file_location_path = tmp
    gui.frequencies = params["frequencies"]
    gui.log = lambda msg: None
    gui._pending_rows = mod.deque()
    gui._write_error_logged = False
    gui.backend = types.SimpleNamespace(
        cryocon=types.SimpleNamespace(heater_query=None),
        params={"channel": params["channel"]})
    gui.c0_farads = mod.c0_from_params(params)
    gui._create_per_frequency_files(params)
    return gui


def test_per_frequency_files_do_not_collide_below_1_hz():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[0.1, 0.5, 1e6], save_windeta=False)
        names = sorted(os.path.basename(p)
                       for p in gui.freq_filepaths.values())
        listing = sorted(os.listdir(tmp))
    assert names == ["S-0.1Hz_pica_format.dat", "S-0.5Hz_pica_format.dat",
                     "S-1000000Hz_pica_format.dat"]
    assert gui.windeta_filepaths == {}       # unticked format: no files
    assert listing == names + ["S_T-log.txt"]          # T-log still made


def test_an_existing_file_of_either_format_gets_a_timestamped_tag():
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "S-1000Hz_windeta_format.txt"), "w").close()
        gui = _files(tmp)
        names = [os.path.basename(p) for p in
                 list(gui.freq_filepaths.values())
                 + list(gui.windeta_filepaths.values()) + [gui.t_log_path]]
    assert all(n.startswith("S_") for n in names), names


def test_area_is_in_mm2_and_eps_matches_an_ideal_capacitor():
    c0 = mod.compute_c0_from_area(100.0, 1.0)
    assert math.isclose(c0, mod.EPS0_SI * 100e-6 / 1e-3)
    assert math.isclose(mod.compute_c0(10.0, 1.0),
                        mod.compute_c0_from_area(math.pi * 25.0, 1.0))
    # eps_r = 12 with a loss of 0.5: Y = j*w*C0*(12 - 0.5j)
    f = 1000.0
    y = 1j * 2 * math.pi * f * c0 * (12 - 0.5j)
    z = 1 / y
    eps1, eps2 = mod.calculate_eps(f, z.real, z.imag, c0)
    assert math.isclose(eps1, 12.0) and math.isclose(eps2, 0.5)


def test_windeta_files_follow_the_sample_tscan_layout():
    ref = os.path.join(os.path.dirname(PATH), "data_file_for_ref",
                       "Sample_Tscan.TXT")
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp)
        c0 = gui.c0_farads
        y = 1j * 2 * math.pi * 1000.0 * c0 * (13 - 2j)
        z = 1 / y
        valid = mod.STATUS_RESULT_VALID
        gui._save_cycle_to_files({"points": [
            (423.15, 1000.0, z.real, z.imag, valid, 1000.0),
            (424.0, 2000.0, float("nan"), float("nan"), -1, 2000.0)]})
        path = gui.windeta_filepaths[1000.0]
        raw = open(path, "rb").read()
        empty = open(gui.windeta_filepaths[2000.0], "rb").read()
        kill = open(gui.freq_filepaths[2000.0], encoding="utf-8").read()
    assert os.path.basename(path) == "S-1000Hz_windeta_format.txt"
    lines = raw.split(b"\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")          # CRLF only
    assert lines[1] == (b"Diameter[mm] 9.680e+00  Thickness[mm] 1.140e+00"
                        b"  Spacer Capacity[pF] 0.000e+00  Spacer "
                        b"Area[mm^2] 0.000e+00")
    assert lines[2] == (b"Fixed value(s) :  Freq. [Hz]=1.000e+03  "
                        b"AC Volt  [Vrms]=5.000e-01")
    assert lines[3] == (b" Temp. [\xb0C]\t Eps'    \t Eps''   \t"
                        b" Tan(Delta)\t Phi [Deg]")              # cp1252
    if os.path.exists(ref):
        # The reference has T / eps' / eps'' only; Tan(Delta) and Phi are
        # appended after it, spelt as in the GUI Fscan WinDETA file.
        ref_header = open(ref, "rb").read().split(b"\r\n")[3]
        assert lines[3].startswith(ref_header + b"\t")
    assert lines[0].startswith(b"S, ") and lines[4:] == [
        b"1.50000e+02\t1.30000e+01\t2.00000e+00\t1.53846e-01\t-8.12538e+01",
        b""]
    assert empty.count(b"\r\n") == 4      # the NaN kill row is PICA-only
    assert kill.splitlines()[-1].startswith("4.240000E+02\tNAN")


def test_windeta_file_reads_back_tan_delta_and_phi_for_a_parallel_rc():
    """One point of a parallel RC (Rp || Cp), read back BY HEADER NAME:
    Tan(Delta) = 1/(w Rp Cp) and Phi is negative (capacitive)."""
    rp, cp, f = 1e6, 100e-12, 1000.0
    w = 2 * math.pi * f
    z = 1 / (1 / rp + 1j * w * cp)
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[f])
        c0 = gui.c0_farads
        gui._save_cycle_to_files({"points": [
            (300.0, f, z.real, z.imag, mod.STATUS_RESULT_VALID, f)]})
        raw = open(gui.windeta_filepaths[f], "rb").read()
    lines = raw.decode("cp1252").split("\r\n")
    names = [n.strip() for n in lines[3].split("\t")]
    assert names == ["Temp. [\u00b0C]", "Eps'", "Eps''", "Tan(Delta)",
                     "Phi [Deg]"]
    row = dict(zip(names, (float(v) for v in lines[4].split("\t"))))
    assert len(lines[4].split("\t")) == 5 and lines[5:] == [""]
    assert math.isclose(row["Temp. [\u00b0C]"], 300.0 - 273.15,
                        rel_tol=1e-5)
    assert math.isclose(row["Eps'"], cp / c0, rel_tol=1e-5)
    assert math.isclose(row["Eps''"], 1 / (w * rp * c0), rel_tol=1e-5)
    assert math.isclose(row["Tan(Delta)"], 1 / (w * rp * cp), rel_tol=1e-5)
    assert math.isclose(row["Tan(Delta)"], row["Eps''"] / row["Eps'"],
                        rel_tol=1e-4)
    assert row["Phi [Deg]"] < 0
    assert math.isclose(row["Phi [Deg]"],
                        -math.degrees(math.atan(w * rp * cp)), rel_tol=1e-5)


def test_a_windeta_write_failure_is_buffered_and_replayed_as_cp1252_crlf():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[1000.0])
        y = 1j * 2 * math.pi * 1000.0 * gui.c0_farads * (13 - 2j)
        z = 1 / y
        point = (300.0, 1000.0, z.real, z.imag, mod.STATUS_RESULT_VALID,
                 1000.0)
        real_write = gui._durable_write
        gui._durable_write = lambda *a, **k: (_ for _ in ()).throw(
            OSError("share gone"))
        gui._save_cycle_to_files({"points": [point]})
        assert len(gui._pending_rows) == 2             # PICA + WinDETA row
        assert [r[2] for r in gui._pending_rows] == [False, True]
        gui._durable_write = real_write
        gui._save_cycle_to_files({"points": []})       # next cycle flushes
        assert not gui._pending_rows
        raw = open(gui.windeta_filepaths[1000.0], "rb").read()
    assert raw.endswith(b"2.68500e+01\t1.30000e+01\t2.00000e+00"
                        b"\t1.53846e-01\t-8.12538e+01\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_area_mode_writes_the_equivalent_diameter():
    lines = mod.windeta_tscan_header_lines(
        _out_params(geometry_mode="area", area_mm2=math.pi * 25.0), 1e3,
        mod.datetime(2026, 1, 15, 11, 35))
    assert lines[0] == "S, 15.01.2026, 11:35"
    assert lines[1].startswith("Diameter[mm] 1.000e+01  ")


def test_pica_files_start_with_ascii_metadata_rows():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, geometry_mode="area", area_mm2=50.0)
        text = open(gui.freq_filepaths[2000.0], encoding="utf-8").read()
    lines = text.splitlines()
    meta = [ln for ln in lines if ln.startswith("#")]
    assert lines[len(meta)] == mod.Integrated_CT_GUI.DATA_HEADER
    assert all(ln.isascii() for ln in meta)
    joined = "\n".join(meta)
    for want in ("Fixed frequency: 2000 Hz", "A = 50.0 mm^2",
                 f"C0 = {gui.c0_farads:.5e} F", "Evaluation : Area electrode",
                 "ZREFMODE=-3", "ZSLCAL=1", "ZLLCOR=1", "DRS=0 0",
                 "Passive, read only: Cryocon 34 input A", ALPHA_ADDR):
        assert want in joined, want


def test_output_settings_are_checked_before_start():
    for bad in (_out_params(save_pica=False, save_windeta=False),
                _out_params(thickness_mm=0.0),
                _out_params(geometry_mode="area", area_mm2=-1.0)):
        try:
            mod.validate_output_settings(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    mod.validate_output_settings(_out_params(save_pica=False))


class _FakeThread:
    """threading.Thread stand-in: records the target, never runs it."""
    made = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args
        self.started = False
        _FakeThread.made.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return False


class _fake_threads:
    """Swap _FakeThread in for threading.Thread inside the module."""

    def __enter__(self):
        _FakeThread.made = []
        self._old = mod.threading.Thread
        mod.threading.Thread = _FakeThread
        return _FakeThread.made

    def __exit__(self, *exc):
        mod.threading.Thread = self._old
        return False


def _start_gui(geometry="1.0", save_pica=True, save_windeta=True,
               stop_loops=False, folder=None, **entries):
    """A GUI shell whose start_measurement runs up to the worker launch.
    Connect happens on the worker, so 'seen' holds the params handed to
    the (fake, never run) worker thread; a refusal leaves it empty. The
    backend's initialize_instruments must never be called from Start."""
    def _var(value):
        return types.SimpleNamespace(get=lambda: value)

    seen = []

    def _init(*_a, **_k):
        raise AssertionError("Start must not connect on the Tk thread")

    widget = types.SimpleNamespace(config=lambda **k: None,
                                   set_data=lambda *a: None,
                                   draw=lambda: None)
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.data_queue = queue.Queue()
    gui.data_storage = {}
    gui._pending_rows = mod.deque()
    gui._decade_ylims = {}
    gui.line_main = gui.line_sub1 = gui.line_sub2 = widget
    gui.canvas = widget
    gui.start_button = gui.stop_button = gui.scan_button = widget
    gui._set_inputs_enabled = lambda enabled: None
    gui.stop_event = threading.Event()
    gui._files_ready = threading.Event()
    gui.root = types.SimpleNamespace(after=lambda *a: None)
    gui.measurement_thread = None
    gui.log = lambda msg: None
    gui.file_location_path = folder or tempfile.gettempdir()
    gui._parse_frequencies = lambda: [1000.0]
    texts = {"Sample Name": "S", "AC Bias": "0.5", "MTM": "0.5",
             "Delay": "0", "Geometry": geometry, "Thickness": "1.0"}
    texts.update({k.replace("_", " "): v for k, v in entries.items()})
    gui.entries = {k: _var(v) for k, v in texts.items()}
    gui.channel_cb = _var("B")
    gui.var_stop_loops = _var(stop_loops)
    gui.wire_combobox = _var("2")
    gui.cryocon_cb = _var(CRYOCON_ADDR)
    gui.lcr_cb = _var(ALPHA_ADDR)
    gui.geom_mode_var = _var("area")
    gui.fmt_pica_var = _var(save_pica)
    gui.fmt_windeta_var = _var(save_windeta)
    gui.backend = types.SimpleNamespace(initialize_instruments=_init,
                                        close_instruments=lambda: None)
    shown = []
    old = mod.messagebox
    mod.messagebox = types.SimpleNamespace(
        showerror=lambda *a, **k: shown.append(a))
    try:
        with _fake_threads() as made:
            gui.start_measurement()
    finally:
        mod.messagebox = old
    seen.extend(t.args[0] for t in made)
    return gui, seen, shown


def test_start_checks_geometry_and_formats_before_any_instrument():
    for kw in ({"geometry": "0"}, {"save_pica": False,
                                   "save_windeta": False}):
        _, seen, shown = _start_gui(**kw)
        assert seen == [] and len(shown) == 1, kw    # refused at Start


def test_start_describes_the_passive_temperature_program_truthfully():
    gui, seen, _ = _start_gui()
    params = seen[0]
    assert params["area_mm2"] == 1.0 and "diameter_mm" not in params
    assert math.isclose(gui.c0_farads, mod.compute_c0_from_area(1.0, 1.0))
    text = params["temp_program"]
    assert text.isascii()
    assert "Cryocon 34 input B" in text and CRYOCON_ADDR in text
    assert "left untouched" in text and "Ramp" not in text
    _, seen, _ = _start_gui(stop_loops=True)
    assert "disengaged at Start (STOP, heaters off)" in seen[0]["temp_program"]


def test_geometry_toggle_keeps_a_value_per_mode():
    class _Entry:
        def __init__(self, text):
            self.text = text

        def get(self):
            return self.text

        def delete(self, *a):
            self.text = ""

        def insert(self, _i, text):
            self.text += text
    labels = []
    mode = types.SimpleNamespace(value="area")
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.entries = {"Geometry": _Entry("78.5"), "Thickness": _Entry("1.0")}
    gui._geom_values = {"area": "1.0", "diameter": "1.0"}
    gui.geom_mode_var = types.SimpleNamespace(get=lambda: mode.value)
    gui.lbl_geom = types.SimpleNamespace(
        config=lambda **k: labels.append(k["text"]))
    gui.lbl_c0 = types.SimpleNamespace(config=lambda **k: None)
    mode.value = "diameter"
    gui._on_geom_mode_change()
    assert gui.entries["Geometry"].get() == "1.0"
    gui.entries["Geometry"].text = "10"
    mode.value = "area"
    gui._on_geom_mode_change()
    assert gui.entries["Geometry"].get() == "78.5"
    assert gui._geom_values["diameter"] == "10"
    assert labels == ["Electrode Diameter (mm):", "Electrode Area (mm²):"]


def test_the_unattended_handlers_open_no_dialog():
    for name in ("_handle_kill_event", "_handle_runtime_error",
                 "_measurement_worker", "_reconnect_with_backoff",
                 "_process_data_queue", "_run_worker", "_on_connected",
                 "_handle_connect_failure"):
        src = inspect.getsource(getattr(mod.Integrated_CT_GUI, name))
        assert "messagebox" not in src, name


def test_the_kill_and_error_handlers_still_stop_and_beep():
    class FakeGui:
        def __init__(self):
            self.lines, self.stops, self.beeps = [], [], []
            self.backend = mod.Combined_Backend.__new__(mod.Combined_Backend)

        def log(self, msg):
            self.lines.append(msg)

        def stop_measurement(self, from_user=True):
            self.stops.append(from_user)

        def _beep(self, times=1):
            self.beeps.append(times)

        def _update_live_plots(self, force=False):
            pass

        def _set_status(self, text, color=None):
            self.status = text

    gui = FakeGui()
    mod.Integrated_CT_GUI._handle_kill_event(gui)
    assert gui.stops == [False] and gui.beeps == [5]
    assert gui.status.startswith("SAFETY KILL")        # banner persists
    assert any("STOP" in line for line in gui.lines)
    gui = FakeGui()
    mod.Integrated_CT_GUI._handle_runtime_error(
        gui, mod.AlphaFatalStatusError("status 6"))
    assert gui.stops == [False] and gui.beeps == [3]
    assert gui.status.startswith("ERROR")
    assert any("AlphaFatalStatusError: status 6" in line
               for line in gui.lines)
    assert not any("NoneType: None" in line for line in gui.lines)


def test_nan_temperatures_do_not_freeze_the_x_axis():
    class FakeAxis:
        xlim = None

        def set_xlim(self, lo, hi):
            self.xlim = (lo, hi)

    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.plot_freq = 1e3
    gui.ax_main = FakeAxis()
    gui._apply_y_scale = lambda ax, values, key: None
    gui.data_storage = {"cp": {1e3: {"v": [1.0, 2.0, 3.0],
                                     "T": [float("nan"), 80.0, 90.0]}}}
    mod.Integrated_CT_GUI._rescale_main_axis(gui)
    lo, hi = gui.ax_main.xlim
    assert lo < 80.0 and hi > 90.0


def test_hardening_and_headers_survived_the_port():
    assert "os.fsync(" in SOURCE
    assert "SetThreadExecutionState" in SOURCE
    assert "_reconnect_with_backoff" in SOURCE
    assert "if pt[0] == pt[0]" in SOURCE
    assert "self.data_queue.put(('ERROR', e, traceback.format_exc()))" in SOURCE
    first = SOURCE.splitlines()[1]
    assert "Temprature_Scan_Passive_CC34_AlphaAN_GUI.py" in first
    assert "Not yet run on the" in SOURCE and "SAFETY:" in SOURCE
    assert "E4980A &" not in SOURCE
    assert SOURCE.count("Alpha-AN & Cryocon 34") == 2



# --- review fixes (23 Oct 2026): refused Alpha, cut-short sweeps -------------

def test_a_refused_alpha_address_gets_nothing_at_close_either():
    fake = FakeAlpha({"*IDN?": "KEITHLEY INSTRUMENTS,MODEL 2400"})
    bus = FakeBus(alpha=fake)
    with patch_bus(bus):
        backend = mod.AlphaAN_Backend()
        try:
            backend.connect(ALPHA_ADDR)
        except ConnectionError:
            pass
        else:
            raise AssertionError("a non-Alpha IDN was accepted")
        backend.close_instrument()             # Start's error path
    assert fake.commands == ["*IDN?"], fake.commands


def _partial_sweep_error():
    bus = FakeBus(alpha=FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1000 2 1",
                                            "ZRE=1E5 -2E6 10000 6 1"]}))
    with patch_bus(bus):
        backend = _started(bus)
        try:
            backend.measure_frequency_sweep([1e3, 1e4], 0.0)
        except mod.AlphaFatalStatusError as exc:
            return exc
        finally:
            backend.close_instruments()
    raise AssertionError("status 6 must abort the sweep")


def test_points_measured_before_a_fatal_status_are_kept():
    err = _partial_sweep_error()
    assert [p[1] for p in err.partial_cycle["points"]] == [1e3]
    gui, calls = _worker_gui(err)
    gui._measurement_worker()
    kinds = [i[0] for i in _drain(gui.data_queue) if isinstance(i, tuple)]
    assert kinds == ["CYCLE", "ERROR"], kinds
    assert calls["reconnect"] == 0


def test_points_measured_before_a_comm_drop_are_kept():
    err = IOError("VI_ERROR_TMO")
    err.partial_cycle = _partial_sweep_error().partial_cycle
    gui, calls = _worker_gui(err)
    gui._measurement_worker()
    items = _drain(gui.data_queue)
    cycles = [i for i in items if isinstance(i, tuple) and i[0] == "CYCLE"]
    assert len(cycles) == 1 and len(cycles[0][1]["points"]) == 1
    comm = [n for n, i in enumerate(items)
            if isinstance(i, str) and "COMM ERROR" in i]
    assert items.index(cycles[0]) < comm[0]    # saved before the reconnect
    assert calls["reconnect"] == 1


def test_a_sweep_cut_short_by_stop_is_written_to_the_files():
    """The pump stops re-scheduling once Stop drops the run flags; the
    worker's last (partial) sweep arrives after that and must still be
    written before the run ends."""
    valid = mod.STATUS_RESULT_VALID
    closed = []
    logged = []
    button = types.SimpleNamespace(config=lambda **k: None)
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp)
        gui.log = logged.append
        before = {f: open(p).read() for f, p in gui.freq_filepaths.items()}
        gui._update_data_storage = lambda *a: None
        gui._update_live_plots = lambda *a, **k: None
        gui._set_keep_awake = lambda on: None
        unlocked = []
        gui._set_inputs_enabled = unlocked.append
        gui.data_queue = queue.Queue()
        gui.data_queue.put(("CYCLE", {
            "points": [(300.0, 1000.0, 1e5, -2e6, valid, 1000.0)],
            "heater": 5.0, "wall_time": mod.datetime.now()}, 12.0))
        gui.data_queue.put(("ERROR", OSError("late"), "Traceback: late"))
        gui.start_button = gui.scan_button = button
        gui.backend = types.SimpleNamespace(
            close_instruments=lambda: closed.append(True))
        gui._close_after_stop = False
        gui._stopping = True
        gui._finalize_stop(False)
        after = {f: open(p).read() for f, p in gui.freq_filepaths.items()}
        windeta = open(gui.windeta_filepaths[1000.0], "rb").read()
        t_log = open(gui.t_log_path).read()
    added = after[1000.0][len(before[1000.0]):].splitlines()
    assert len(added) == 1 and added[0].startswith("3.000000E+02\t")
    assert len(added[0].split("\t")) == 19
    assert after[2000.0] == before[2000.0]
    assert windeta.endswith(b"\r\n") and windeta.count(b"\r\n") == 5
    assert "\t12.0\t300.0000\t5.00" in t_log
    assert any("Traceback: late" in m for m in logged)
    assert gui.data_queue.empty() and closed == [True]
    assert unlocked == [True]                  # inputs usable again


def test_start_drops_what_a_late_worker_left_in_the_queue():
    # A worker that outlived Stop's drain left a partial CYCLE and an
    # exception behind; neither belongs to the next run.
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.measurement_thread = None
    gui.log = lambda msg: None
    gui.data_queue = queue.Queue()
    gui.data_queue.put(("CYCLE", {"points": [], "killed": False}, 1.0))
    gui.data_queue.put(("ERROR", RuntimeError("late"), "traceback"))
    gui.backend = types.SimpleNamespace(close_instruments=lambda: None)

    def _fail():
        raise ValueError("stop the start here")
    gui._parse_frequencies = _fail
    old = mod.messagebox
    mod.messagebox = types.SimpleNamespace(showerror=lambda *a, **k: None)
    try:
        gui.start_measurement()
    finally:
        mod.messagebox = old
    assert gui.data_queue.empty()


# --- bug hunt (27 Oct 2026): Start checks, Stop/close races, plots ----------

def _refused_at_start(**kw):
    """start_measurement refuses before any hardware call; the dialog text."""
    _, seen, shown = _start_gui(**kw)
    assert seen == [] and len(shown) == 1, (kw, seen, shown)
    return shown[0][1]


def test_nan_and_inf_geometry_are_refused_naming_the_box():
    for over, box in (({"thickness_mm": float("nan")}, "Thickness"),
                      ({"thickness_mm": float("inf")}, "Thickness"),
                      ({"diameter_mm": float("nan")}, "Diameter"),
                      ({"geometry_mode": "area", "area_mm2": float("inf")},
                       "Area")):
        try:
            mod.validate_output_settings(_out_params(**over))
        except ValueError as exc:
            assert box in str(exc), (over, exc)
        else:
            raise AssertionError(f"accepted {over}")
    assert "Thickness" in _refused_at_start(Thickness="nan")
    assert "Electrode Area" in _refused_at_start(geometry="inf")


def test_a_bad_number_names_its_box():
    assert "Integration Time (s): '0,5'" in _refused_at_start(MTM="0,5")
    assert "AC Voltage (Vrms): ''" in _refused_at_start(AC_Bias="")
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.freq_text = types.SimpleNamespace(get=lambda *a: "1000, 1e3x")
    try:
        gui._parse_frequencies()
    except ValueError as exc:
        assert "'1e3x'" in str(exc)
    else:
        raise AssertionError("a bad frequency token was accepted")


def test_an_infinite_nan_or_huge_delay_is_refused_at_start():
    for text in ("inf", "nan", "1e9", "-1"):
        assert "Delay per Freq" in _refused_at_start(Delay=text), text
    _, seen, _ = _start_gui(Delay="3600")
    assert seen and seen[0]["delay"] == 3600.0


def test_folder_and_sample_name_are_checked_before_any_instrument():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "unplugged")
        assert "does not exist" in _refused_at_start(folder=missing)
        for name in ("a/b", "S:1", "x?y", "S1.", "S1 "):
            assert "Sample Name" in _refused_at_start(
                folder=tmp, Sample_Name=name), name
        # Not cp1252: refused only while a WinDETA file would be written.
        assert "cp1252" in _refused_at_start(folder=tmp,
                                             Sample_Name="SΩ")
        _, seen, _ = _start_gui(folder=tmp, Sample_Name="SΩ",
                                save_windeta=False)
        assert seen and seen[0]["sample_name"] == "SΩ"


class _StopAfterDelay:
    """stop_event stand-in: wait() records when it ran, then answers."""

    def __init__(self, bus, stop):
        self.bus, self.stop, self.marks = bus, stop, []

    def is_set(self):
        return False

    def wait(self, timeout=None):
        self.marks.append(len(self.bus.cryocon.queries))
        return self.stop


def test_stop_during_the_per_point_delay_measures_nothing_more():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus)
        bus.alpha.traffic.clear()
        cycle = backend.measure_frequency_sweep(
            [1e3, 1e4], 30.0, _StopAfterDelay(bus, stop=True))
        backend.close_instruments()
    assert cycle["points"] == []
    assert "MST" not in bus.alpha.commands, bus.alpha.commands


def test_each_points_temperature_is_read_after_its_settle_delay():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus)
        start = len(bus.cryocon.queries)
        ev = _StopAfterDelay(bus, stop=False)
        backend.measure_frequency_sweep([1e3], 30.0, ev)
        reads = [n for n, q in enumerate(bus.cryocon.queries)
                 if n >= start and q.startswith("INPUT?")]
        backend.close_instruments()
    assert len(ev.marks) == 1 and reads and reads[0] >= ev.marks[0]


def test_a_mid_run_reconnect_never_resends_the_start_stop():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus, stop_loops=True)
        assert bus.cryocon.writes == ["STOP"]
        bus.alpha.closed = False
        backend.reconnect()
        backend.close_instruments()
    assert bus.cryocon.writes == ["STOP"], bus.cryocon.writes


def test_a_second_run_never_truncates_the_first_runs_t_log():
    with tempfile.TemporaryDirectory() as tmp:
        first = _files(tmp, frequencies=[1000.0])
        with open(first.t_log_path, "a", encoding="utf-8") as fh:
            fh.write("history\n")
        second = _files(tmp, frequencies=[2000.0])
        kept = open(first.t_log_path, encoding="utf-8").read()
        assert second.t_log_path != first.t_log_path
    assert kept.endswith("history\n")


def _kill_backend(temp, connected=True):
    cryo = StrictCryocon(temp=temp)
    backend = object.__new__(mod.Combined_Backend)
    backend.cryocon = _passive_cryocon(cryo)
    if not connected:
        backend.cryocon.link.instrument = None
    backend.params = {}
    backend.reconnects = 0

    def _reconnect():
        backend.reconnects += 1
        raise IOError("Alpha still off")
    backend.reconnect = _reconnect
    return backend, cryo


def test_the_400k_kill_still_fires_while_the_alpha_is_unreachable():
    backend, cryo = _kill_backend("401.0")
    gui = _reconnect_gui(backend)
    gui.stop_event.wait = lambda *_a: False
    old_time = mod.time.time
    ticks = iter(range(0, 10 ** 6, 100))
    mod.time.time = lambda: next(ticks)
    try:
        assert gui._reconnect_with_backoff(1) is False
    finally:
        mod.time.time = old_time
    assert cryo.writes == ["STOP"] and backend.reconnects == 0
    assert "KILL" in _drain(gui.data_queue)
    # A closed Cryocon session is left to the reconnect, not queried.
    backend, cryo = _kill_backend("401.0", connected=False)
    gui = _reconnect_gui(backend)
    assert gui._kill_check_while_reconnecting() is False
    assert cryo.queries == []


def test_start_retries_last_runs_rows_and_counts_any_it_drops():
    def _var(value):
        return types.SimpleNamespace(get=lambda: value)

    widget = types.SimpleNamespace(config=lambda **k: None)
    lines = []
    with tempfile.TemporaryDirectory() as tmp:
        ok_path = os.path.join(tmp, "old-run.dat")
        gui = object.__new__(mod.Integrated_CT_GUI)
        gui.measurement_thread = None
        gui.log = lines.append
        gui.file_location_path = tmp
        gui._parse_frequencies = lambda: [1000.0]
        gui.entries = {"Sample Name": _var("S"), "AC Bias": _var("0.5"),
                       "MTM": _var("0.5"), "Delay": _var("0"),
                       "Geometry": _var("1.0"), "Thickness": _var("1.0")}
        gui.channel_cb = _var("A")
        gui.var_stop_loops = _var(False)
        gui.wire_combobox = _var("2")
        gui.cryocon_cb = _var(CRYOCON_ADDR)
        gui.lcr_cb = _var(ALPHA_ADDR)
        gui.geom_mode_var = _var("area")
        gui.fmt_pica_var = _var(True)
        gui.fmt_windeta_var = _var(True)
        gui.backend = types.SimpleNamespace(
            close_instruments=lambda: None)
        gui.freq_filepaths, gui.windeta_filepaths = {}, {}
        gui._create_per_frequency_files = lambda p: None
        gui._populate_plot_freq_dropdown = lambda: None
        gui.plot_freq = 1000.0
        gui.start_button = gui.stop_button = gui.scan_button = widget
        locked = []
        gui._set_inputs_enabled = locked.append
        statuses = []
        gui._set_status = lambda text, color=None: statuses.append(text)
        gui.data_storage = {}
        gui._decade_ylims = {}
        gui.line_main = gui.line_sub1 = gui.line_sub2 = types.SimpleNamespace(
            set_data=lambda *a: None)
        gui.ax_main = types.SimpleNamespace(set_title=lambda *a, **k: None)
        gui.canvas = types.SimpleNamespace(draw=lambda: None)
        gui.stop_event = threading.Event()
        gui._files_ready = threading.Event()
        gui._set_keep_awake = lambda on: None
        gui.root = types.SimpleNamespace(after=lambda *a: None)
        gui._write_error_logged = True
        gui._pending_rows = mod.deque([
            (ok_path, "row 1\n", False),
            (os.path.join(tmp, "gone", "x.dat"), "row 2\n", False)])
        with _fake_threads():
            gui.start_measurement()
        written = open(ok_path, encoding="utf-8").read()
        assert statuses[-1].startswith("CONNECTING"), statuses
        gui._on_connected()                       # what CONNECTED does
    assert written == "row 1\n"                  # retried, not discarded
    assert any("1 row(s) from the previous run" in m for m in lines), lines
    assert not gui._pending_rows and locked == [False]
    assert statuses[-1] == "RUNNING"
    assert gui.is_running and not gui.is_stabilizing
    assert gui._files_ready.is_set()


def test_the_banner_shows_start_failures_and_user_stops():
    statuses = []
    gui, _, _ = _start_gui(geometry="0")
    # _start_gui has no banner widget: _set_status must be a no-op there.
    assert gui.lbl_status is None
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.lbl_status = types.SimpleNamespace(
        config=lambda **k: statuses.append(k["text"]),
        update_idletasks=lambda: None)
    gui._set_status("ERROR - see console")
    assert statuses == ["ERROR - see console"]
    src = inspect.getsource(mod.Integrated_CT_GUI._finalize_stop)
    assert src.index('"STOPPED"') > src.index("if from_user:")
    start = inspect.getsource(mod.Integrated_CT_GUI.start_measurement)
    assert 'self._set_status("Start failed - see console")' in start


def test_closing_waits_for_a_worker_that_outlived_stop():
    alive = [True]
    after, events = [], []
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.is_running = gui.is_stabilizing = gui._stopping = False
    gui._close_after_stop = False
    gui.log = lambda msg: None
    gui.measurement_thread = types.SimpleNamespace(
        is_alive=lambda: alive[0])
    gui.backend = types.SimpleNamespace(
        close_instruments=lambda: events.append("close"))
    gui.root = types.SimpleNamespace(
        after=lambda ms, fn: after.append(fn),
        destroy=lambda: events.append("destroy"))
    gui._on_closing()
    gui._on_closing()                          # a second click: one chain
    assert events == [] and len(after) == 1
    alive[0] = False
    after.pop()()
    assert events == ["close", "destroy"]


class _FakeAxis:
    def __init__(self):
        self.scale, self.xlim = None, None

    def set_yscale(self, scale):
        self.scale = scale

    def relim(self):
        pass

    def set_autoscaley_on(self, on):
        pass

    def autoscale_view(self, **k):
        pass

    def set_ylim(self, lo, hi):
        pass

    def set_xlim(self, lo, hi):
        self.xlim = (lo, hi)


def test_auto_scale_never_hides_negative_points():
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.y_scale_var = types.SimpleNamespace(get=lambda: "auto")
    gui._decade_ylims = {}
    ax = _FakeAxis()
    assert gui._apply_y_scale(ax, [1e-12, 1e-9, -1e-12], "cp") is False
    assert ax.scale == "linear"
    assert gui._apply_y_scale(ax, [1e-12, 1e-9], "cp") is True
    assert ax.scale == "log"


def test_a_single_temperature_still_gets_an_x_range_around_it():
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.plot_freq = 1e3
    gui.ax_main = _FakeAxis()
    gui._apply_y_scale = lambda ax, values, key: None
    gui.data_storage = {"cp": {1e3: {"v": [1.0], "T": [80.0]}}}
    mod.Integrated_CT_GUI._rescale_main_axis(gui)
    lo, hi = gui.ax_main.xlim
    assert lo < 80.0 < hi


def test_panel_wheel_colours_and_run_lock_are_wired():
    assert 'canvas.bind_all("<MouseWheel>", _wheel)' in SOURCE
    assert 'option_add("*Label.background", self.CLR_BG_DARK)' in SOURCE
    assert "style.configure('TRadiobutton'" in SOURCE
    assert "style='Graph.TRadiobutton'" in SOURCE
    start = inspect.getsource(mod.Integrated_CT_GUI.start_measurement)
    assert "self._set_inputs_enabled(False)" in start
    lock = inspect.getsource(mod.Integrated_CT_GUI._set_inputs_enabled)
    for name in ("self.entries", "self.freq_text", "self.geom_radios",
                 "self.fmt_checks", "self.stop_loops_check",
                 "self.file_button", "self.cryocon_cb", "self.lcr_cb"):
        assert name in lock, name
    assert "plot_freq_cb" not in lock and "var_plot_thin" not in lock


# --- connect/initialise on the worker (27 Oct 2026) --------------------------

class _NoDialogs:
    """Record messagebox calls instead of opening windows."""

    def __enter__(self):
        self.calls = []
        self._old = mod.messagebox
        rec = self.calls
        mod.messagebox = types.SimpleNamespace(
            showerror=lambda *a, **k: rec.append(("showerror", a)),
            showinfo=lambda *a, **k: rec.append(("showinfo", a)),
            showwarning=lambda *a, **k: rec.append(("showwarning", a)),
            askyesno=lambda *a, **k: rec.append(("askyesno", a)) or True)
        return self

    def __exit__(self, *exc):
        mod.messagebox = self._old
        return False


def _connect_gui(backend, **attrs):
    """A GUI shell with what the worker, the pump and the stop path use."""
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.backend = backend
    gui.stop_event = threading.Event()
    gui._files_ready = threading.Event()
    gui.data_queue = queue.Queue()
    gui.logged, gui.statuses, gui.beeps, gui.afters = [], [], [], []
    gui.log = gui.logged.append
    gui._set_status = lambda text, color=None: gui.statuses.append(text)
    gui._beep = lambda times=1: gui.beeps.append(times)
    gui._set_keep_awake = lambda on: None
    gui.root = types.SimpleNamespace(
        after=lambda ms, fn: gui.afters.append(fn))
    for k, v in attrs.items():
        setattr(gui, k, v)
    return gui


def test_start_on_the_tk_thread_never_connects():
    with tempfile.TemporaryDirectory() as tmp:
        gui, seen, shown = _start_gui(folder=tmp)
        files = os.listdir(tmp)
    assert files == [], "files are created only after CONNECTED"
    assert shown == []
    assert len(seen) == 1 and seen[0]["lcr_visa"] == ALPHA_ADDR
    assert gui.is_stabilizing and not gui.is_running
    assert gui._run_params is seen[0]
    assert not gui.stop_event.is_set() and not gui._files_ready.is_set()
    src = inspect.getsource(mod.Integrated_CT_GUI.start_measurement)
    assert "initialize_instruments" not in src
    assert "_create_per_frequency_files" not in src
    assert "target=self._run_worker, args=(params,)" in src
    assert 'self._set_status("CONNECTING... (Stop is available)"' in src


def test_stop_during_the_alpha_reset_wait_ends_the_worker_quickly():
    from alphaan_port_test_support import ALPHA_IDN, FakeVisaError
    alpha = FakeAlpha({"*IDN?": [ALPHA_IDN] + [
        FakeVisaError(mod.VI_ERROR_TMO)] * 100000})
    bus = FakeBus(alpha=alpha)
    with patch_bus(bus):
        combined = mod.Combined_Backend(log=lambda m: None)
        gui = _connect_gui(combined)
        worker = threading.Thread(target=gui._run_worker,
                                  args=(_params(),), daemon=True)
        worker.start()
        deadline = mod.time.monotonic() + 10.0
        while (alpha.commands.count("*IDN?") < 3
               and mod.time.monotonic() < deadline):
            threading.Event().wait(0.01)   # time.sleep is patched out
        assert "*RST" in alpha.commands, "never reached the *RST wait"
        stopped_at = mod.time.monotonic()
        gui.stop_event.set()                       # Stop pressed
        worker.join(5.0)
        took = mod.time.monotonic() - stopped_at
        assert not worker.is_alive() and took < 2.0, took
        assert "MODE=IMP" not in alpha.commands
        assert "MST" not in alpha.commands
        msgs = _drain(gui.data_queue)
        assert all(isinstance(m, str) for m in msgs), msgs
        assert any("Stopped while connecting" in m for m in msgs), msgs
        # The normal stop path then closes what was opened, safely.
        combined.close_instruments()
    assert bus.cryocon.writes == [], bus.cryocon.writes   # read only
    # Stopped inside the *RST rejoin: the Alpha gets nothing more at close
    # (no ACV=0 / *RST into a reset still in progress), only the session
    # is released.
    after_reset = alpha.commands[alpha.commands.index("*RST") + 1:]
    assert set(after_reset) == {"*IDN?"}, after_reset
    assert alpha.closed
    assert bus.cryocon.closed


def test_a_connect_failure_resets_the_ui_and_shows_error():
    def fail(params, reconnecting=False, stop_event=None):
        raise ConnectionError("The Alpha did not answer")
    closed = []
    button = types.SimpleNamespace(config=lambda **k: None)
    unlocked = []
    gui = _connect_gui(
        types.SimpleNamespace(initialize_instruments=fail, params={},
                              close_instruments=lambda: closed.append(True)),
        is_running=False, is_stabilizing=True, _stopping=False,
        _close_after_stop=False, frequencies=[1000.0],
        start_button=button, stop_button=button, scan_button=button,
        _pending_rows=mod.deque(), _set_inputs_enabled=unlocked.append)
    gui.measurement_thread = threading.Thread(
        target=gui._run_worker, args=(_params(),), daemon=True)
    gui.measurement_thread.start()
    gui.measurement_thread.join(5.0)
    assert not gui.measurement_thread.is_alive()
    with _NoDialogs() as dialogs:
        gui._process_data_queue()
    assert any(s.startswith("ERROR") and "did not answer" in s
               for s in gui.statuses), gui.statuses
    assert gui.statuses[-1].startswith("ERROR")
    assert any("ERROR during startup" in m and "Traceback" in m
               for m in gui.logged)
    assert unlocked == [True] and closed == [True] and gui.beeps == [3]
    assert not (gui.is_running or gui.is_stabilizing)
    assert gui.stop_event.is_set() and not gui._files_ready.is_set()
    assert [c[0] for c in dialogs.calls] == ["showerror"]   # at Start
    assert "did not answer" in dialogs.calls[0][1][1]
    assert gui.afters == [], "the pump must not keep running"


def test_stop_before_connected_is_handled_creates_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _connect_gui(None, is_stabilizing=False,
                           file_location_path=tmp)
        gui.stop_event.set()
        gui._on_connected()
        assert os.listdir(tmp) == []
    assert not gui._files_ready.is_set() and not gui.logged


def test_the_worker_waits_for_the_files_before_the_first_sweep():
    order = []
    gui = _connect_gui(types.SimpleNamespace(
        initialize_instruments=lambda p, stop_event=None:
            order.append(("init", stop_event))))
    gui._measurement_worker = lambda: order.append("run")
    worker = threading.Thread(target=gui._run_worker, args=({},),
                              daemon=True)
    worker.start()
    assert gui.data_queue.get(timeout=5.0) == "CONNECTED"
    assert order == [("init", gui.stop_event)]   # no sweep before files
    gui._files_ready.set()
    worker.join(5.0)
    assert order[-1] == "run" and not worker.is_alive()


def test_a_file_error_after_connect_ends_the_run():
    errors = []
    gui = _connect_gui(None, is_running=False, is_stabilizing=True)
    gui._on_connected = lambda: (_ for _ in ()).throw(
        PermissionError("folder is read only"))
    gui._handle_runtime_error = errors.append
    gui.data_queue.put("CONNECTED")
    gui._process_data_queue()
    assert len(errors) == 1 and isinstance(errors[0], PermissionError)
    assert not gui._files_ready.is_set()


def test_a_mid_run_reconnect_keeps_the_stop_event():
    bus = FakeBus()
    event = threading.Event()
    with patch_bus(bus):
        backend = mod.Combined_Backend(log=lambda m: None)
        backend.initialize_instruments(_params(), stop_event=event)
        assert backend.lcr.stop_event is event
        bus.alpha.traffic.clear()
        bus.alpha.closed = False
        event.set()                                # Stop during reconnect
        try:
            backend.reconnect()
        except mod.StopRequested:
            pass
        else:
            raise AssertionError("a Stop must end the reconnect")
        backend.close_instruments()
    assert backend.stop_event is event
    # Only the park ran: no fresh connect, no *RST, no Cryocon STOP.
    assert bus.alpha.commands[:2] == ["ACV=0", "ZCONSPL=0"], bus.alpha.commands
    assert "*IDN?" not in bus.alpha.commands[:3]
    assert bus.cryocon.writes == []


# --- Stop during the Alpha *RST rejoin (27 Oct 2026 review) ------------------

class _RebootingAlpha(FakeAlpha):
    """An Alpha that stops answering once *RST is written: every *IDN?
    times out, and any other command hangs (as a write to a non-listening
    Alpha does through 64-bit Keysight VISA) before timing out."""

    HANG_S = 2.0

    def __init__(self):
        from alphaan_port_test_support import ALPHA_IDN
        super().__init__({"*IDN?": ALPHA_IDN})
        self.resetting = False

    def _hang(self, command):
        from alphaan_port_test_support import FakeVisaError
        if command != "*IDN?":
            threading.Event().wait(self.HANG_S)   # time.sleep is patched
        raise FakeVisaError(mod.VI_ERROR_TMO)

    def write(self, command):
        if self.resetting:
            self.traffic.append(("write", command))
            self._hang(command)
        super().write(command)
        if command == "*RST":
            self.resetting = True

    def query(self, command):
        if self.resetting:
            self.traffic.append(("query", command))
            self._hang(command)
        return super().query(command)


def test_stop_during_the_rejoin_sends_nothing_after_the_reset():
    alpha = _RebootingAlpha()
    bus = FakeBus(alpha=alpha)
    with patch_bus(bus):
        combined = mod.Combined_Backend(log=lambda m: None)
        gui = _connect_gui(combined)
        worker = threading.Thread(target=gui._run_worker,
                                  args=(_params(),), daemon=True)
        worker.start()
        deadline = mod.time.monotonic() + 10.0
        while (alpha.commands.count("*IDN?") < 3
               and mod.time.monotonic() < deadline):
            threading.Event().wait(0.01)
        assert alpha.resetting, "never reached the *RST rejoin"
        assert combined.lcr._alpha_rebooting
        gui.stop_event.set()                       # Stop pressed
        worker.join(5.0)
        assert not worker.is_alive()
        assert combined.lcr._alpha_rebooting, "Stop must leave the flag set"
        sent_before_close = len(alpha.commands)
        started = mod.time.monotonic()
        combined.close_instruments()               # the Tk-thread stop path
        took = mod.time.monotonic() - started
    assert took < 0.5, f"close blocked for {took:.2f} s"
    assert alpha.commands[sent_before_close:] == [], alpha.commands
    assert set(alpha.commands[alpha.commands.index("*RST") + 1:]) == {
        "*IDN?"}
    assert alpha.closed and combined.lcr.instrument is None
    assert bus.cryocon.closed and bus.cryocon.writes == []


def test_the_rebooting_flag_follows_the_rejoin_and_a_new_connect():
    bus = FakeBus()
    with patch_bus(bus):
        backend = _started(bus)
        assert backend.lcr._alpha_rebooting is False   # rejoin answered
        backend.lcr._alpha_rebooting = True            # a stale flag ...
        backend.lcr.instrument = None                  # (session gone)
        backend.reconnect()                            # ... cleared by connect
        assert backend.lcr._alpha_rebooting is False
        bus.alpha.traffic.clear()
        backend.close_instruments()
    # A normal close still parks and resets the Alpha.
    assert bus.alpha.commands[:2] == ["ACV=0", "ZCONSPL=0"]
    assert "*RST" in bus.alpha.commands


def test_a_backend_built_without_init_still_closes_normally():
    lcr = object.__new__(mod.AlphaAN_Backend)
    lcr.instrument = FakeAlpha()
    lcr._task_may_be_running = False
    fake = lcr.instrument
    lcr.close_instrument()
    assert fake.commands[:2] == ["ACV=0", "ZCONSPL=0"] and fake.closed


def test_safe_state_sends_nothing_while_the_alpha_reboots():
    lcr = object.__new__(mod.AlphaAN_Backend)
    fake = lcr.instrument = FakeAlpha()
    lcr._task_may_be_running = True
    lcr._alpha_rebooting = True
    lcr.safe_state()
    lcr.release_for_reconnect()
    assert fake.commands == [] and fake.closed and lcr.instrument is None


# --- close-after-stop waits for the worker (27 Oct 2026 review) --------------

def _closing_gui(events, thread):
    button = types.SimpleNamespace(config=lambda **k: None)
    gui = _connect_gui(
        types.SimpleNamespace(
            close_instruments=lambda: events.append("close")),
        _close_after_stop=True, _stopping=True, _pending_rows=mod.deque(),
        start_button=button, scan_button=button,
        _set_inputs_enabled=lambda on: None, measurement_thread=thread)
    gui.root.destroy = lambda: events.append("destroy")
    return gui


def test_close_after_a_timed_out_stop_waits_for_the_worker():
    alive = [True]
    events = []
    gui = _closing_gui(events, types.SimpleNamespace(
        is_alive=lambda: alive[0]))
    with _NoDialogs() as dialogs:
        gui._finalize_stop(from_user=False)
        assert events == ["close"], events          # no destroy yet
        assert len(gui.afters) == 1
        gui.afters.pop()()                           # worker still alive
        assert events == ["close"] and len(gui.afters) == 1
        alive[0] = False                             # worker leaves its I/O
        gui.afters.pop()()
    # The sessions the worker may have reopened are closed, then destroy.
    assert events == ["close", "close", "destroy"], events
    assert dialogs.calls == []
    src = inspect.getsource(mod.Integrated_CT_GUI._finalize_stop)
    assert "self.root.destroy()" not in src


def test_close_after_stop_destroys_at_once_when_the_worker_is_gone():
    events = []
    gui = _closing_gui(events, None)
    with _NoDialogs():
        gui._finalize_stop(from_user=False)
    assert events == ["close", "close", "destroy"] and gui.afters == []


# --- log() from the worker never touches Tk (27 Oct 2026 review) -------------

class _ThreadCheckingConsole:
    """A Text stand-in that records lines and which thread touched it."""

    def __init__(self):
        self.lines, self.threads = [], set()

    def _touch(self):
        self.threads.add(threading.current_thread())

    def config(self, **k):
        self._touch()

    def insert(self, index, text):
        self._touch()
        self.lines.append(text)

    def index(self, what):
        self._touch()
        return f"{len(self.lines) + 1}.0"

    def delete(self, a, b):
        self._touch()

    def see(self, what):
        self._touch()


def test_log_from_a_worker_thread_reaches_the_console_via_the_pump():
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.console_widget = _ThreadCheckingConsole()
    gui.data_queue = queue.Queue()
    gui.is_running = gui.is_stabilizing = False
    gui.root = types.SimpleNamespace(after=lambda ms, fn: None)
    worker = threading.Thread(
        target=gui.log, args=("Cryocon connected: test",), daemon=True)
    worker.start()
    worker.join(5.0)
    assert gui.console_widget.threads == set(), "worker touched the widget"
    assert gui.console_widget.lines == []
    gui._process_data_queue()                       # the Tk-thread poll
    assert any("Cryocon connected: test" in line
               for line in gui.console_widget.lines)
    assert gui.console_widget.threads == {threading.current_thread()}
    # A Tk-thread call still writes directly.
    gui.log("direct")
    assert gui.console_widget.lines[-1].endswith("direct\n")
    assert gui.data_queue.empty()


def test_the_backend_log_from_the_worker_at_start_is_queued():
    """initialize_instruments runs on the worker and the Cryocon backend
    logs through the GUI's log(): every line goes through the queue."""
    bus = FakeBus()
    gui = object.__new__(mod.Integrated_CT_GUI)
    gui.console_widget = _ThreadCheckingConsole()
    gui.data_queue = queue.Queue()
    with patch_bus(bus):
        backend = mod.Combined_Backend(log=gui.log)
        worker = threading.Thread(
            target=backend.initialize_instruments, args=(_params(),),
            daemon=True)
        worker.start()
        worker.join(10.0)
        backend.close_instruments()
    assert not worker.is_alive()
    assert gui.console_widget.threads == set()
    msgs = _drain(gui.data_queue)
    assert msgs and all(m.startswith("LOG:") for m in msgs), msgs

if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s).")
    sys.exit(1 if failures else 0)
