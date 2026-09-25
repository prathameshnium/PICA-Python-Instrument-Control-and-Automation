"""Unattended-run hardening of the two worker-thread Cryo-con siblings.

Delta_RT_K6221_K2182_CC34_Sensing_GUI.py (v8.2) and
RT_K6517B_CC34_T_Sensing_GUI.py (v4.4), 25 Sep 2026: the v1.3 pattern of
Temprature_Scan_Passive_CC34_E4980A_GUI.py, copied into each (every PICA
program is self-contained).

  1. Pacing. CRYOCON_MIN_GAP_S was declared and never used; the K6517B
     sent INPUT? and HEATER:OUTPWR? back to back on every point. The
     session open_cryocon_session() returns now holds consecutive queries
     that far apart, forwards everything else (the heater probe's timeout
     must still reach the real resource) and has no write() of its own.
  2. A comm error never ends a run: log, back off 5 -> 10 -> 30 -> 60 s,
     reconnect, resume. Stop cuts the backoff short.
  3. Every data row is fsync'd; a failed write is buffered and retried in
     order, and the row format is byte-identical to the csv rows of before.
  4. No modal dialog once a run has started: log line + banner + beep.
  5. Windows keep-awake on at Start, off when the run ends.

No hardware and no Tk root: GUI objects are built with object.__new__ and
given only what each path touches, the worker runs in the test thread, and
time is a virtual clock patched onto a private copy of each module.

Runnable as plain Python as well as under pytest.
"""

import collections
import csv
import importlib.util
import inspect
import os
import queue
import re
import shutil
import sys
import tempfile
import types
from datetime import datetime as _real_datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULE_PATHS = {
    "delta": ("pica", "keithley", "delta_mode",
              "Delta_RT_K6221_K2182_CC34_Sensing_GUI.py"),
    "k6517b": ("pica", "keithley", "k6517b", "High_Resistance",
               "RT_K6517B_CC34_T_Sensing_GUI.py"),
}


def _load(name, parts):
    path = os.path.join(REPO_ROOT, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Private copies under their own names: whatever is patched here cannot
# leak into any other test file.
MODULES = {key: _load("cc34_hardening_" + key, parts)
           for key, parts in MODULE_PATHS.items()}
DELTA = MODULES["delta"]
K6517B = MODULES["k6517b"]
GUI_CLASS = {"delta": DELTA.MeasurementAppGUI,
             "k6517b": K6517B.Integrated_RT_GUI}

# One healthy get_measurement() result per module, in its own order.
POINT = {"delta": (1.0e3, 1.0e-3, 77.35),               # R, V, T
         "k6517b": (77.35, 0.0, 1.0e-9, 1.0e10)}        # T, htr, I, R

CRYOCON_ADDR = "GPIB0::23::INSTR"
KEITHLEY_ADDR = "GPIB0::13::INSTR"
CRYOCON_IDN = "Cryocon Model 34, Rev 3.03A"


def _glitch():
    return IOError("VI_ERROR_TMO (-1073807339): Timeout expired before "
                   "operation completed.")


# ------------------------------------------------------------ test doubles

class patched:
    """Set attributes on one object for the duration of a with-block."""

    _MISSING = object()

    def __init__(self, target, **values):
        self.target = target
        self.values = values

    def __enter__(self):
        self.saved = {name: getattr(self.target, name, self._MISSING)
                      for name in self.values}
        for name, value in self.values.items():
            setattr(self.target, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is self._MISSING:
                delattr(self.target, name)
            else:
                setattr(self.target, name, value)
        return False


class VirtualClock:
    """Stands in for a module's `time`: time() reads the clock and sleep()
    advances it. Patched onto one private module copy, never onto the real
    time module every other test shares."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)


class VirtualEvent:
    """threading.Event on the virtual clock: wait(t) advances the clock by
    t instead of blocking. Optionally 'presses Stop' after n waits."""

    def __init__(self, clock, set_after_waits=None):
        self.clock = clock
        self.flag = False
        self.waits = []
        self.set_after_waits = set_after_waits

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, timeout=None):
        if self.flag:
            return True
        self.waits.append(timeout)
        self.clock.now += timeout or 0
        if (self.set_after_waits is not None
                and len(self.waits) >= self.set_after_waits):
            self.flag = True
        return self.flag


class FixedDatetime:
    """The module's `datetime`, frozen, so a row's timestamp is known."""
    NOW = _real_datetime(2026, 9, 25, 3, 4, 5)

    @classmethod
    def now(cls):
        return cls.NOW


class Anything:
    """Accepts any method call and records it (widgets, root, axes)."""

    def __init__(self):
        self.__dict__["calls"] = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


class NoDialogs:
    """Stands in for a module's messagebox: any dialog fails the test."""

    def __getattr__(self, name):
        def refuse(*args, **kwargs):
            raise AssertionError(f"messagebox.{name} opened: {args}")
        return refuse


class FakeEntry:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeSession:
    """A Cryo-con VISA resource on the virtual clock. Every query takes
    30 ms and is stamped (command, start, end, timeout in force). Answers
    only what the lab unit answers; anything else is refused the Cryo-con
    way, by silence (a VISA timeout)."""

    def __init__(self, clock, heater_query="HEATER:OUTPWR?", units="K"):
        self.clock = clock
        self.heater_query = heater_query
        self.units = units
        self.queries = []
        self.exchanges = []
        self.writes = []
        self.fail = set()
        self.closed = False
        self.timeout_history = []
        self._timeout = None

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value
        self.timeout_history.append(value)

    def query(self, command):
        start = self.clock.now
        self.clock.now += 0.03
        self.queries.append(command)
        self.exchanges.append((command, start, self.clock.now, self._timeout))
        if command in self.fail:
            raise _glitch()
        if command == "*IDN?":
            return CRYOCON_IDN
        if command.startswith("INPUT") and command.endswith(":UNITS?"):
            return self.units
        if command.startswith("INPUT?"):
            return "77.350"
        if self.heater_query and command == self.heater_query:
            return "0.0"
        raise _glitch()

    def write(self, command):
        self.writes.append(command)

    def close(self):
        self.closed = True


class Fake6221:
    """The Keithley 6221 session of the delta module. `dead` models a
    session that died with its instrument: everything raises."""

    def __init__(self):
        self.writes = []
        self.closed = False
        self.dead = False
        self.timeout = None

    def _check(self):
        if self.dead:
            raise IOError("VI_ERROR_CONN_LOST")

    def write(self, command):
        self._check()
        self.writes.append(command)

    def query(self, command):
        self._check()
        if command == "*IDN?":
            return "KEITHLEY INSTRUMENTS INC.,MODEL 6221,4101234,D03"
        if command == "SENSe:DATA:FRESh?":
            return "+1.000000E-03,+0.000000E+00"
        return "0"

    def close(self):
        self._check()
        self.closed = True


class LabBus:
    """Cryocon on ::23, Keithley 6221 on ::13; a fresh session per open,
    so a reconnect can be told apart from the session it replaced."""

    def __init__(self, clock, heater_query="HEATER:OUTPWR?", units=("K",)):
        self.clock = clock
        self.heater_query = heater_query
        self.units = list(units)
        self.cryocons = []
        self.keithleys = []

    def open_resource(self, address, **kwargs):
        if address == CRYOCON_ADDR:
            units = self.units.pop(0) if len(self.units) > 1 else self.units[0]
            session = FakeSession(self.clock, self.heater_query, units)
            self.cryocons.append(session)
            return session
        if address == KEITHLEY_ADDR:
            session = Fake6221()
            self.keithleys.append(session)
            return session
        raise IOError("VI_ERROR_RSRC_NFOUND")

    def list_resources(self):
        return (CRYOCON_ADDR, KEITHLEY_ADDR)

    def close(self):
        pass


def _fake_pyvisa(bus):
    class VisaIOError(Exception):
        pass
    return types.SimpleNamespace(
        ResourceManager=lambda: bus,
        errors=types.SimpleNamespace(VisaIOError=VisaIOError))


class FakeAdapter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeK6517B:
    """pymeasure's Keithley6517B, recording the configuration it is given."""
    instances = []

    def __init__(self, address):
        self.__dict__["calls"] = []
        self.__dict__["adapter"] = FakeAdapter()
        self.__dict__["address"] = address
        FakeK6517B.instances.append(self)

    def __setattr__(self, name, value):
        self.calls.append((name, value))
        self.__dict__[name] = value

    @property
    def id(self):
        return "KEITHLEY INSTRUMENTS INC.,MODEL 6517B,1234567,A13"

    @property
    def resistance(self):
        return 1.0e10

    def reset(self):
        self.calls.append("reset")

    def measure_resistance(self):
        self.calls.append("measure_resistance")

    def write(self, command):
        self.calls.append(("write", command))

    def enable_source(self):
        self.calls.append("enable_source")

    def shutdown(self):
        self.calls.append("shutdown")


class ScriptedBackend:
    """get_measurement() plays a script: a tuple is returned, an exception
    instance is raised. Serving the LAST entry presses Stop, so the worker
    ends on its own. reconnect() plays its own script (None = success, an
    exception = failure, a callable = run it, then succeed)."""

    def __init__(self, app, measurements, reconnects=()):
        self.app = app
        self.measurements = list(measurements)
        self.reconnects = list(reconnects)
        self.measure_calls = 0
        self.reconnect_calls = 0
        self.closed = 0
        self.params = {"source_voltage": 10.0}

    def get_measurement(self):
        self.measure_calls += 1
        item = self.measurements.pop(0)
        if not self.measurements:
            self.app.stop_event.set()
        if isinstance(item, BaseException):
            raise item
        return item

    def reconnect(self):
        self.reconnect_calls += 1
        outcome = self.reconnects.pop(0) if self.reconnects else None
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            outcome()

    def close_instruments(self):
        self.closed += 1


def _bare_app(key, clock, data_filepath=None):
    """A GUI object with no Tk behind it: only what the run paths touch.
    log, _beep and _set_keep_awake are recorders."""
    app = object.__new__(GUI_CLASS[key])
    app.is_running = True
    app.stop_event = VirtualEvent(clock)
    app.data_queue = queue.Queue()
    app.start_time = clock.now
    app.measurement_thread = None
    app._stopping = False
    app._close_after_stop = False
    app._pending_rows = collections.deque(maxlen=20000)
    app._write_error_logged = False
    app._plot_dirty = False
    app.logs = []
    app.log = app.logs.append
    app.beeps = []
    app._beep = lambda times=1: app.beeps.append(times)
    app.keep_awake = []
    app._set_keep_awake = app.keep_awake.append
    app.root = Anything()
    app.start_button = Anything()
    app.stop_button = Anything()
    app.canvas = Anything()
    app.banner_var = Anything()
    app.banner = Anything()
    app.header_frame = Anything()
    app.line_main = app.line_sub1 = app.line_sub2 = Anything()
    app.ax_main = app.ax_sub1 = app.ax_sub2 = Anything()
    app.log_scale_var = Anything()
    app.data_filepath = data_filepath
    if key == "delta":
        app.data_storage = {"time": [], "voltage": [], "resistance": [],
                            "temperature": []}
    else:
        app.data_storage = {"time": [], "temperature": [], "current": [],
                            "resistance": []}
    return app


def _drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def _is_point(item):
    return (isinstance(item, tuple) and item
            and not isinstance(item[0], Exception))


def _banners(app):
    return [call[1][0] for call in app.banner_var.calls
            if call[0] == "set" and call[1][0]]


# ===================================================== 1. paced Cryo-con bus

def test_the_bus_is_still_kept_slow():
    """The user's standing instruction: pacing gap and operating timeout
    untouched; only the probe wait (K6517B) is short."""
    for key, mod in MODULES.items():
        assert mod.CRYOCON_MIN_GAP_S == 0.08, key
        assert mod.CRYOCON_TIMEOUT_MS == 10000, key
    assert K6517B.CRYOCON_PROBE_TIMEOUT_MS == 3000
    assert K6517B.CRYOCON_HEATER_QUERIES == (
        "HEATER:OUTPWR?", "HEATER:HTRREAD?",
        "LOOP {loop}:HTRREAD?", "LOOP {loop}:OUTPWR?")


def test_back_to_back_queries_are_held_the_gap_apart():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock):
            real = FakeSession(clock)
            paced = mod._PacedCryoconSession(real)
            for _ in range(4):
                paced.query("INPUT? A")
        ex = real.exchanges
        # The first query of a session goes at once.
        assert ex[0][1] == 1000.0, (key, ex[0])
        for before, after in zip(ex, ex[1:]):
            gap = after[1] - before[2]
            assert gap >= mod.CRYOCON_MIN_GAP_S - 1e-9, (key, gap)
        assert len(clock.sleeps) == 3, (key, clock.sleeps)


def test_a_failed_query_still_starts_the_gap():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock):
            real = FakeSession(clock)
            real.fail.add("INPUT? A")
            paced = mod._PacedCryoconSession(real)
            try:
                paced.query("INPUT? A")
            except IOError:
                pass
            else:
                raise AssertionError(f"{key}: the timeout was swallowed")
            paced.query("INPUT A:UNITS?")
        first, second = real.exchanges
        assert second[1] - first[2] >= mod.CRYOCON_MIN_GAP_S - 1e-9, key


def test_no_wait_once_the_gap_has_already_passed():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock):
            paced = mod._PacedCryoconSession(FakeSession(clock))
            paced.query("INPUT? A")
            clock.now += 1.0            # a whole point later
            paced.query("INPUT? A")
        assert clock.sleeps == [], (key, clock.sleeps)


def test_the_gap_is_read_at_call_time():
    """So a test (or a future setting) can change it without re-opening."""
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock, CRYOCON_MIN_GAP_S=0):
            paced = mod._PacedCryoconSession(FakeSession(clock))
            for _ in range(3):
                paced.query("INPUT? A")
        assert clock.sleeps == [], (key, clock.sleeps)


def test_the_proxy_forwards_everything_but_query_and_writes_nothing():
    for key, mod in MODULES.items():
        assert "write" not in vars(mod._PacedCryoconSession), key
        clock = VirtualClock()
        real = FakeSession(clock)
        paced = mod._PacedCryoconSession(real)
        paced.timeout = 1234
        assert real.timeout == 1234, key          # reached the resource
        assert paced.timeout == 1234, key         # and reads back from it
        assert set(vars(paced)) == {"_session", "_last_io"}, vars(paced)
        # A stray write is forwarded, so a fake still sees - and a test
        # still catches - it. The proxy does not hide one.
        paced.write("STRAY")
        assert real.writes == ["STRAY"], key
        paced.close()
        assert real.closed, key
        try:
            paced.no_such_attribute
        except AttributeError:
            pass
        else:
            raise AssertionError(f"{key}: unknown attribute did not raise")


def test_open_cryocon_session_returns_a_paced_session():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        bus = LabBus(clock)
        with patched(mod, time=clock, pyvisa=_fake_pyvisa(bus)):
            inst, idn = mod.open_cryocon_session(CRYOCON_ADDR,
                                                 log=lambda m: None)
            inst.query("INPUT A:UNITS?")
        real = bus.cryocons[0]
        assert isinstance(inst, mod._PacedCryoconSession), key
        assert idn == CRYOCON_IDN, key
        assert real.timeout == mod.CRYOCON_TIMEOUT_MS, key
        assert mod.CRYOCON_OPEN_SETTLE_S in clock.sleeps, key
        # '*IDN?' went through the proxy too, so the next query waits.
        (_, _, idn_end, _), (_, units_start, _, _) = real.exchanges
        assert units_start - idn_end >= mod.CRYOCON_MIN_GAP_S - 1e-9, key
        assert real.writes == [], key


def test_the_k6517b_heater_probe_still_times_the_real_session():
    """The probe shortens the timeout and restores it: through the proxy,
    both must land on the real VISA resource, and the candidate queries
    must really have gone out under the short one."""
    for heater_query in ("HEATER:OUTPWR?", "LOOP 1:OUTPWR?", None):
        clock = VirtualClock()
        bus = LabBus(clock, heater_query=heater_query)
        with patched(K6517B, time=clock, pyvisa=_fake_pyvisa(bus)):
            backend = K6517B.Cryocon34_Backend(CRYOCON_ADDR,
                                               log=lambda m: None)
            real = bus.cryocons[0]
            assert isinstance(backend.instrument, K6517B._PacedCryoconSession)
            real.timeout_history.clear()
            backend.probe_heater_command(1)
        assert real.timeout_history == [K6517B.CRYOCON_PROBE_TIMEOUT_MS,
                                        K6517B.CRYOCON_TIMEOUT_MS], (
            heater_query, real.timeout_history)
        assert real.timeout == K6517B.CRYOCON_TIMEOUT_MS
        assert set(vars(backend.instrument)) == {"_session", "_last_io"}
        probes = [e for e in real.exchanges
                  if "OUTPWR" in e[0] or "HTRREAD" in e[0]]
        assert probes, heater_query
        assert all(e[3] == K6517B.CRYOCON_PROBE_TIMEOUT_MS
                   for e in probes), probes
        assert backend.heater_query == heater_query
        assert real.writes == []


def test_the_k6517b_point_paces_input_and_heater_reads():
    """The two queries every point sends were back to back before."""
    clock = VirtualClock()
    bus = LabBus(clock)
    with patched(K6517B, time=clock, pyvisa=_fake_pyvisa(bus)):
        backend = K6517B.Cryocon34_Backend(CRYOCON_ADDR, log=lambda m: None)
        backend.probe_heater_command(1)
        real = bus.cryocons[0]
        real.exchanges.clear()
        backend.get_temperature("A")
        backend.get_heater_output(1)
    (cmd1, _, end1, _), (cmd2, start2, _, _) = real.exchanges
    assert (cmd1, cmd2) == ("INPUT? A", "HEATER:OUTPWR?")
    assert start2 - end1 >= K6517B.CRYOCON_MIN_GAP_S - 1e-9


# =============================================== 2. comm errors never end it

def test_a_comm_error_mid_run_does_not_end_the_run():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock, datetime=FixedDatetime):
            app = _bare_app(key, clock)
            still_down = IOError("VI_ERROR_RSRC_NFOUND: still down")
            app.backend = ScriptedBackend(
                app,
                [POINT[key], _glitch(), POINT[key], _glitch(), POINT[key]],
                reconnects=[still_down] * 4 + [None, None])
            app._measurement_worker()
        items = _drain(app.data_queue)
        backend = app.backend
        points = [i for i in items if _is_point(i)]
        text = [i for i in items if isinstance(i, str)]

        assert len(points) == 3, (key, points)
        assert backend.measure_calls == 5, key
        assert backend.reconnect_calls == 6, key
        # 5, 10, 30, 60 then capped at 60 across the failed reconnects;
        # a good point resets it, so the next glitch waits 5 s again.
        delays = [int(m.group(1)) for m in
                  (re.search(r"Reconnect attempt in (\d+) s", s)
                   for s in text) if m]
        assert delays == [5, 10, 30, 60, 60, 5], (key, delays)
        assert sum("Reconnect failed" in s for s in text) == 4, key
        # The waits were real (virtual) seconds, in 1 s slices Stop can cut.
        assert points[1][-1] - points[0][-1] >= 165, (key, points)
        # Logged with a short traceback formatted in the worker.
        comm = [s for s in text if s.startswith("LOG:COMM ERROR")]
        assert len(comm) == 2, (key, comm)
        assert "failure #1" in comm[0] and "VI_ERROR_TMO" in comm[0], comm
        assert "Traceback" in comm[0], comm[0]
        assert any(s.startswith("BANNER:Comm error at 03:04:05")
                   for s in text), (key, text)
        assert any(s.startswith("BANNER:Reconnected") for s in text), key
        # Nothing on the queue would end the run, and the elapsed-time
        # origin was never touched.
        assert not any(isinstance(i, tuple) and i
                       and isinstance(i[0], Exception) for i in items), key
        assert app.start_time == 1000.0, key
        assert backend.closed == 0, key


def test_stop_during_the_backoff_returns_promptly():
    for key, mod in MODULES.items():
        # Stop after 3 of the first backoff's 5 one-second waits: no
        # reconnect is ever attempted.
        clock = VirtualClock()
        with patched(mod, time=clock):
            app = _bare_app(key, clock)
            app.stop_event = VirtualEvent(clock, set_after_waits=3)
            app.backend = ScriptedBackend(app, [_glitch(), POINT[key]])
            app._measurement_worker()
        assert app.backend.reconnect_calls == 0, key
        assert app.backend.measure_calls == 1, key
        assert len(app.stop_event.waits) == 3, (key, app.stop_event.waits)
        items = _drain(app.data_queue)
        assert not any(_is_point(i) for i in items), key

        # Stop in the SECOND backoff, after one failed reconnect: exactly
        # that one attempt, and none after Stop.
        clock = VirtualClock()
        with patched(mod, time=clock):
            app = _bare_app(key, clock)
            app.stop_event = VirtualEvent(clock, set_after_waits=5 + 2)
            app.backend = ScriptedBackend(app, [_glitch(), POINT[key]],
                                          reconnects=[_glitch()])
            app._measurement_worker()
        assert app.backend.reconnect_calls == 1, key
        assert len(app.stop_event.waits) == 7, (key, app.stop_event.waits)


def test_stop_during_a_reconnect_leaves_nothing_open():
    """Stop pressed while reconnect() is re-opening: the worker must close
    what it just opened (a re-armed 6221, a re-enabled 6517B source)."""
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock):
            app = _bare_app(key, clock)
            app.backend = ScriptedBackend(
                app, [_glitch(), POINT[key]],
                reconnects=[lambda: app.stop_event.set()])
            app._measurement_worker()
        assert app.backend.reconnect_calls == 1, key
        assert app.backend.closed == 1, key
        assert app.backend.measure_calls == 1, key
        text = [i for i in _drain(app.data_queue) if isinstance(i, str)]
        assert not any("Reconnected" in s for s in text), (key, text)


def test_the_delta_backend_reconnect_rearms_exactly_as_start():
    clock = VirtualClock()
    bus = LabBus(clock)
    params = {"sample_name": "S", "apply_current": 1e-6,
              "compliance_v": 10.0, "keithley_visa": KEITHLEY_ADDR,
              "cryocon_visa": CRYOCON_ADDR}
    with patched(DELTA, time=clock, pyvisa=_fake_pyvisa(bus)):
        backend = DELTA.Combined_Backend()
        backend.initialize_instruments(params)
        start_sequence = list(bus.keithleys[0].writes)
        # The instrument power-cycled: its old session is dead.
        bus.keithleys[0].dead = True
        backend.reconnect()
        res, volt, temp = backend.get_measurement()
    assert "SOUR:DELT:ARM" in start_sequence and "INIT:IMM" in start_sequence
    assert len(bus.keithleys) == 2
    assert bus.keithleys[1].writes == start_sequence, bus.keithleys[1].writes
    assert len(bus.cryocons) == 2
    assert bus.cryocons[0].closed, "the dead 6221 kept the Cryocon open"
    assert bus.cryocons[1].queries[:2] == ["*IDN?", "INPUT A:UNITS?"]
    assert all(c.writes == [] for c in bus.cryocons)
    assert isinstance(backend.cryocon, DELTA._PacedCryoconSession)
    assert abs(res - 1000.0) < 1e-6 and volt == 1e-3 and temp == 77.35, (
        res, volt, temp)


def test_the_k6517b_backend_reconnect_keeps_the_settled_heater_query():
    """Re-open and re-configure exactly as Start - but the heater read-back
    settled at Start is carried over and never probed again, even across
    a reconnect that itself fails half-way."""
    for heater_query in ("HEATER:OUTPWR?", None):
        clock = VirtualClock()
        # Units: K at Start, C on the first reconnect (fails), K after.
        bus = LabBus(clock, heater_query=heater_query, units=("K", "C", "K"))
        FakeK6517B.instances = []
        params = {"sample_name": "S", "source_voltage": 10.0, "delay": 0.0,
                  "cryocon_visa": CRYOCON_ADDR,
                  "keithley_visa": KEITHLEY_ADDR}
        with patched(K6517B, time=clock, pyvisa=_fake_pyvisa(bus),
                     Keithley6517B=FakeK6517B):
            backend = K6517B.Combined_Backend()
            backend.initialize_instruments(params)
            assert backend.cryocon.heater_query == heater_query
            try:
                backend.reconnect()
            except ValueError as exc:
                assert "not Kelvin" in str(exc) or "'C'" in str(exc), exc
            else:
                raise AssertionError("a channel in C was accepted")
            backend.reconnect()
            temp, htr, cur, res = backend.get_measurement()

        first_k, second_k = FakeK6517B.instances[0], FakeK6517B.instances[-1]
        assert len(FakeK6517B.instances) == 2, FakeK6517B.instances
        assert "shutdown" in first_k.calls and first_k.adapter.closed
        start_calls = [c for c in first_k.calls if c != "shutdown"]
        assert second_k.calls == start_calls, second_k.calls
        assert ("source_voltage", 10.0) in second_k.calls
        assert "enable_source" in second_k.calls
        assert ("write", ":SYSTem:ZCORrect ON") in second_k.calls

        assert len(bus.cryocons) == 3
        assert all(s.closed for s in bus.cryocons[:2])
        for session in bus.cryocons[1:]:
            asked = [q for q in session.queries
                     if "OUTPWR" in q or "HTRREAD" in q]
            assert asked == ([heater_query] if session is bus.cryocons[-1]
                             and heater_query else []), (heater_query, asked)
        assert backend.cryocon.heater_query == heater_query
        assert backend.cryocon.heater_probed is True
        assert temp == 77.35 and res == 1.0e10
        assert (htr == 0.0) if heater_query else (htr != htr), htr
        assert all(s.writes == [] for s in bus.cryocons)
    source = inspect.getsource(K6517B.Combined_Backend.reconnect)
    assert "probe_heater_command(" not in source


# ========================================= 3. durable, format-identical rows

def test_write_or_buffer_keeps_order_and_logs_once():
    for key, mod in MODULES.items():
        tmp = tempfile.mkdtemp()
        try:
            share = os.path.join(tmp, "share")      # not there yet
            path = os.path.join(share, "data.dat")
            spy = types.SimpleNamespace(fsynced=[])

            class OsSpy:
                def __getattr__(self, name):
                    return getattr(os, name)

                def fsync(self, fd):
                    spy.fsynced.append(fd)
                    return os.fsync(fd)

            clock = VirtualClock()
            app = _bare_app(key, clock, data_filepath=path)
            with patched(mod, os=OsSpy()):
                for row in ("r1\r\n", "r2\r\n", "r3\r\n"):
                    app._flush_pending_rows()
                    app._write_or_buffer(path, row)
                assert [r for _, r in app._pending_rows] == [
                    "r1\r\n", "r2\r\n", "r3\r\n"], key
                assert sum("WRITE ERROR" in m for m in app.logs) == 1, key
                assert spy.fsynced == [], key

                os.makedirs(share)                   # the share comes back
                app._flush_pending_rows()
                app._write_or_buffer(path, "r4\r\n")
            assert not app._pending_rows, key
            with open(path, "rb") as fh:
                assert fh.read() == b"r1\r\nr2\r\nr3\r\nr4\r\n", key
            assert len(spy.fsynced) == 4, (key, spy.fsynced)
            assert sum("recovered" in m for m in app.logs) == 1, key
            assert app._write_error_logged is False, key
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _old_csv_row(path, fields):
    """What the module wrote before 25 Sep 2026, verbatim."""
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)


def test_the_data_row_is_byte_identical_including_nan():
    nan = float("nan")
    stamp = "2026-09-25 03:04:05"
    cases = {
        "delta": ((1234.56, 1.23456e-3, nan, 12.5),
                  [stamp, "12.50", "nan", "1.234560e-03", "1.234560e+03"],
                  "2026-09-25 03:04:05,12.50,nan,1.234560e-03,"
                  "1.234560e+03\r\n"),
        "k6517b": ((nan, nan, 1.0e-9, 1.0e10, 12.5),
                   [stamp, "12.50", "nan", "nan", "1.0000e+01",
                    "1.0000e-09", "1.0000e+10"],
                   "2026-09-25 03:04:05,12.50,nan,nan,1.0000e+01,"
                   "1.0000e-09,1.0000e+10\r\n"),
    }
    for key, (data, old_fields, expected) in cases.items():
        mod = MODULES[key]
        tmp = tempfile.mkdtemp()
        try:
            new_path = os.path.join(tmp, "new.dat")
            old_path = os.path.join(tmp, "old.dat")
            app = _bare_app(key, VirtualClock(), data_filepath=new_path)
            app.backend = types.SimpleNamespace(
                params={"source_voltage": 10.0})
            with patched(mod, datetime=FixedDatetime):
                app._handle_new_data_point(data)
                app._handle_new_data_point(data)
            for _ in range(2):
                _old_csv_row(old_path, old_fields)
            with open(new_path, "rb") as fh:
                new_bytes = fh.read()
            with open(old_path, "rb") as fh:
                old_bytes = fh.read()
            assert new_bytes == old_bytes, (key, new_bytes, old_bytes)
            assert new_bytes == (expected * 2).encode("ascii"), key
            assert app._plot_dirty is True, key
            assert len(app.data_storage["time"]) == 2, key
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ======================================= 4. no dialogs once a run is going

RUN_PATHS = ("_measurement_worker", "_reconnect_with_backoff",
             "_process_data_queue", "_handle_new_data_point",
             "_handle_runtime_error", "stop_measurement",
             "_poll_worker_stopped", "_finalize_stop", "_write_or_buffer",
             "_flush_pending_rows", "_alert", "_show_banner")


def test_no_run_path_calls_a_messagebox():
    for key, cls in GUI_CLASS.items():
        for name in RUN_PATHS:
            source = inspect.getsource(getattr(cls, name))
            assert "messagebox." not in source, (key, name)


def test_a_worker_bug_stops_the_run_without_a_dialog():
    """The last-resort net: an unexpected (non-comm) bug. Its traceback is
    formatted in the worker; the pump stops the run with a log line, a
    banner and three beeps - no messagebox, sessions closed, sleep
    allowed again."""
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock, messagebox=NoDialogs()):
            app = _bare_app(key, clock)
            app.backend = ScriptedBackend(app, [POINT[key], POINT[key]])
            app.start_time = None          # elapsed -> TypeError: a bug
            app._measurement_worker()
            queued = list(app.data_queue.queue)
            errors = [i for i in queued if isinstance(i, tuple)
                      and isinstance(i[0], Exception)]
            assert len(errors) == 1, (key, queued)
            exc, tb_text = errors[0]
            assert isinstance(exc, TypeError), key
            assert "_measurement_worker" in tb_text, tb_text
            assert "NoneType: None" not in tb_text, key
            app._process_data_queue()
        assert app.is_running is False, key
        assert app.stop_event.is_set(), key
        assert app.keep_awake == [False], (key, app.keep_awake)
        assert app.backend.closed == 1, key
        assert app.beeps == [3], (key, app.beeps)
        assert any("unexpected error" in b for b in _banners(app)), key
        assert not [c for c in app.root.calls if c[0] == "after"], key
        assert app._stopping is False, key


def test_a_user_stop_is_a_banner_and_a_beep_not_a_dialog():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched(mod, time=clock, messagebox=NoDialogs()):
            app = _bare_app(key, clock)
            app.backend = ScriptedBackend(app, [])
            app.stop_measurement()
            app.stop_measurement()          # a second click is a no-op
        assert app.keep_awake == [False], key
        assert app.backend.closed == 1, key
        assert app.beeps == [1], key
        assert any("stopped by user" in b for b in _banners(app)), key
        assert app.stop_event.is_set(), key


def test_the_pump_survives_a_gui_side_error():
    for key, mod in MODULES.items():
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "data.dat")
            clock = VirtualClock()
            app = _bare_app(key, clock, data_filepath=path)
            app.backend = types.SimpleNamespace(
                params={"source_voltage": 10.0})
            good = {"delta": (1000.0, 1e-3, 77.35, 1.0),
                    "k6517b": (77.35, 0.0, 1e-9, 1e10, 1.0)}[key]
            app.data_queue.put(("garbage",))
            app.data_queue.put("LOG:hello")
            app.data_queue.put(good)
            with patched(mod, messagebox=NoDialogs(),
                         datetime=FixedDatetime):
                app._process_data_queue()
            assert any("GUI ERROR (non-fatal)" in m for m in app.logs), key
            assert "hello" in app.logs, key
            with open(path, "rb") as fh:
                assert fh.read().count(b"\r\n") == 1, key
            after = [c for c in app.root.calls if c[0] == "after"]
            assert after and after[-1][1][1] == app._process_data_queue, key
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 5. keep-awake

def test_set_keep_awake_sends_the_right_flags_and_never_raises():
    for key, mod in MODULES.items():
        calls = []
        kernel32 = types.SimpleNamespace(
            SetThreadExecutionState=calls.append)
        fake_ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(kernel32=kernel32))
        app = object.__new__(GUI_CLASS[key])
        with patched(mod, ctypes=fake_ctypes):
            app._set_keep_awake(True)
            app._set_keep_awake(False)
        assert calls == [0x80000001, 0x80000000], (key, calls)
        with patched(mod, ctypes=types.SimpleNamespace()):   # not Windows
            app._set_keep_awake(True)


class _StartBackend:
    def __init__(self):
        self.params = {}
        self.closed = 0

    def initialize_instruments(self, params):
        self.params = params

    def close_instruments(self):
        self.closed += 1


def _start_app(key, tmp):
    app = _bare_app(key, VirtualClock())
    app.is_running = False
    app.file_location_path = tmp
    app.backend = _StartBackend()
    app._measurement_worker = lambda: None      # the thread runs nothing
    if key == "delta":
        app.entries = {"Sample Name": FakeEntry("S1"),
                       "Apply Current": FakeEntry("1E-6"),
                       "Compliance Voltage": FakeEntry("10")}
    else:
        app.entries = {"Sample Name": FakeEntry("S1"),
                       "Source Voltage": FakeEntry("10"),
                       "Delay": FakeEntry("1.0")}
    app.keithley_cb = FakeEntry(KEITHLEY_ADDR)
    app.cryocon_cb = FakeEntry(CRYOCON_ADDR)
    return app


def test_keep_awake_is_on_for_the_run_and_off_when_it_ends():
    for key, mod in MODULES.items():
        tmp = tempfile.mkdtemp()
        try:
            app = _start_app(key, tmp)
            # Left over from an earlier run: must not reach the new one.
            app.data_queue.put("LOG:stale")
            app.data_queue.put(None)
            app._pending_rows.append(("x", "stale row"))
            app.stop_event.set()
            with patched(mod, messagebox=NoDialogs()):
                app.start_measurement()
                app.measurement_thread.join(5)
                assert app.is_running, (key, app.logs)
                assert app.keep_awake == [True], key
                assert app.data_queue.empty(), key
                assert not app._pending_rows, key
                assert not app.stop_event.is_set(), key
                app.stop_measurement()
            assert app.keep_awake == [True, False], key
            assert app.backend.closed == 1, key
            with open(app.data_filepath, "rb") as fh:
                header = fh.read().split(b"\r\n")
            assert header[0].startswith(b"# Sample: S1"), header
            assert header[1].startswith(b"Timestamp,Elapsed Time (s)"), key
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def test_versions_were_bumped():
    assert DELTA.MeasurementAppGUI.PROGRAM_VERSION == "8.2"
    assert K6517B.Integrated_RT_GUI.PROGRAM_VERSION == "4.4"


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
