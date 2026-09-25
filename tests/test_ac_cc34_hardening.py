"""Unattended-run hardening (v1.1, 25 Sep 2026) of the two AC modules.

    pica/keithley/k6221_k197a/RT_AC_K6221_K197A_CC34_T_Sensing_GUI.py
    pica/lockin/sr830/RT_AC_K6221_SR830_CC34_T_Sensing_GUI.py

Both run overnight with nobody in the lab. What is held here, for both:

  * pacing - consecutive Cryo-con queries at least CRYOCON_MIN_GAP_S
    apart, through a proxy that has no write() of its own;
  * comm errors - from the thermometer, the detector or the drive - are
    logged, the instruments re-opened and set up again, the drive put
    back, and logging resumes. Never 'failed'. The wait escalates 5, 10,
    30, 60, 60 s and Stop during it ends the run at once, current off;
  * rows are fsync'd, held in order while the disk refuses them, and
    written byte for byte as before (NaN temperature included);
  * no messagebox anywhere on the run, error, stop or completion paths,
    and the queue pump survives a GUI-side exception;
  * Windows is kept awake for exactly as long as the worker runs.

No hardware and no Tk root: the GUI object is built with object.__new__
and everything the worker touches is scripted, as in
test_ac_cc34_thermometry.py. Runnable as plain Python as well as under
pytest.
"""

import datetime as _dt
import importlib.util
import os
import queue
import shutil
import sys
import tempfile
import threading

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

PATHS = {
    "k197a": ("pica", "keithley", "k6221_k197a",
              "RT_AC_K6221_K197A_CC34_T_Sensing_GUI.py"),
    "sr830": ("pica", "lockin", "sr830",
              "RT_AC_K6221_SR830_CC34_T_Sensing_GUI.py"),
}


def _load(key, parts):
    name = "ac_cc34_hardening_" + key
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO_ROOT, *parts))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULES = {key: _load(key, parts) for key, parts in PATHS.items()}

# What the files declare, read before any test touches the private copies.
DECLARED = {key: (mod.CRYOCON_MIN_GAP_S, mod.CRYOCON_TIMEOUT_MS)
            for key, mod in MODULES.items()}
for _mod in MODULES.values():
    _mod.CRYOCON_OPEN_SETTLE_S = 0
    _mod.CRYOCON_RETRY_WAIT_S = 0
    _mod.CRYOCON_READ_RETRY_S = 0

CRYOCON_IDN = "Cryocon Model 34, Rev 3.03A, 204683, 3.03"
NAN = float("nan")


class Timeout(IOError):
    """Stands in for pyvisa's VI_ERROR_TMO."""


# ------------------------------------------------------------- fakes: time

class VirtualClock:
    """A stand-in for the module's `time`: sleeping advances it, nothing
    waits. `on_sleep` runs after every sleep (used to press Stop)."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)
        if self.on_sleep is not None:
            self.on_sleep(self)


class patched_attr:
    """Swap one module attribute for the duration of a with-block."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.saved = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.saved)
        return False


# ------------------------------------------------------------ fakes: VISA

class ClockedSession:
    """Records the virtual time of every query. No write() at all."""

    def __init__(self, clock, replies=None):
        self.clock = clock
        self.replies = dict(replies or {})
        self.query_times = []
        self.queries = []
        self.timeout = None
        self.closed = False

    def query(self, command):
        self.queries.append(command)
        self.query_times.append(self.clock.time())
        reply = self.replies.get(command.strip(), "77.350")
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, replies):
        self.replies = dict(replies)
        self.queries = []
        self.writes = []
        self.timeout = None
        self.closed = False

    def query(self, command):
        self.queries.append(command)
        reply = self.replies.get(command.strip(), Timeout())
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def write(self, command):
        self.writes.append(command)

    def close(self):
        self.closed = True


class FakeRM:
    def __init__(self, sessions):
        self.sessions = list(sessions)

    def open_resource(self, address):
        return self.sessions.pop(0)


class FakeVisa:
    def __init__(self, rm):
        self._rm = rm

    def ResourceManager(self):
        return self._rm


# ------------------------------------------------------ fakes: the worker

class Script:
    """Outcomes for successive calls: an exception instance is raised, a
    value is returned; once used up, `default` is returned for ever."""

    def __init__(self, outcomes=(), default=None):
        self.outcomes = list(outcomes)
        self.default = default
        self.calls = 0

    def __call__(self):
        self.calls += 1
        value = self.outcomes.pop(0) if self.outcomes else self.default
        if isinstance(value, BaseException):
            raise value
        return value


class ScriptedThermometer:
    def __init__(self, temps):
        self.temps = list(temps)
        self.reads = 0

    def read_temperature(self):
        self.reads += 1
        value = self.temps.pop(0) if len(self.temps) > 1 else self.temps[0]
        if isinstance(value, BaseException):
            raise value
        return value


SETTLE_S = 0.7          # distinct from the interval and every backoff
INTERVAL_S = 0.3
FREQUENCY = 13.0
CURRENT_PEAK = 1.4142e-6


def _params(stop_low=1.0, stop_high=400.0):
    return {"temperature": {"interval": INTERVAL_S, "stop_low": stop_low,
                            "stop_high": stop_high},
            "frequency": FREQUENCY, "current_peak": CURRENT_PEAK,
            "settle": SETTLE_S}


class WorkerRun:
    """Drives the real _run_worker, _reconnect_with_backoff and the loop's
    comm-error handling; the instruments and the GUI side are scripted."""

    def __init__(self, key, temps, points=14, measure=(), drive=(),
                 reconnect=(), emit_error=None, real_sleep=False,
                 stop_low=1.0, stop_high=400.0):
        self.mod = MODULES[key]
        gui = object.__new__(self.mod.ACResistanceCC34SensingGUI)
        self.gui = gui
        gui.params = _params(stop_low, stop_high)
        gui.data_queue = queue.Queue()
        gui.io_lock = threading.Lock()
        gui.stop_requested = False
        gui.thermometer = ScriptedThermometer(temps)
        gui.source = None
        self.logged = []
        self.drives = []
        self.shutdowns = 0
        self.awake = []
        self.sleeps = []
        self.measure = Script(measure, default={"resistance": 1.0})
        self.drive = Script(drive)
        self.reconnect = Script(reconnect)
        gui._prepare_instruments = lambda: "out.dat"

        def apply_drive(frequency, current_peak):
            self.drives.append((frequency, current_peak))
            self.drive()
        gui._apply_drive = apply_drive
        gui._auto_functions = lambda first: False
        gui._reconnect_instruments = lambda: self.reconnect()

        def shutdown():
            self.shutdowns += 1
        gui._safe_shutdown = shutdown
        gui._set_keep_awake = lambda enable: self.awake.append(enable)
        gui._measure_point = lambda f, i: dict(self.measure())

        def emit(point, f, ip, ir, t):
            if emit_error is not None:
                raise emit_error
            self.logged.append(t)
        gui._emit_point = emit

        if not real_sleep:
            budget = [points]

            def sleep(seconds):
                self.sleeps.append(seconds)
                budget[0] -= 1
                return budget[0] > 0        # False = Stop was pressed
            gui._sleep_interruptibly = sleep

    def run(self):
        self.gui._run_worker()
        self.items = []
        while not self.gui.data_queue.empty():
            self.items.append(self.gui.data_queue.get_nowait())
        finals = [item for item in self.items
                  if item[0] in ("done", "failed")]
        assert len(finals) == 1, finals
        self.final = finals[0]
        return self

    @property
    def backoffs(self):
        return [s for s in self.sleeps if s >= 5]

    def logs(self):
        return [p for k, p in self.items if k == "log"]

    def statuses(self):
        return [p for k, p in self.items if k == "status"]


# ================================================================ pacing

def test_the_declared_gap_and_timeout():
    for key, (gap, timeout_ms) in DECLARED.items():
        assert gap == 0.08, (key, gap)
        assert timeout_ms == 10000, (key, timeout_ms)


def test_back_to_back_cryocon_queries_are_held_apart():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched_attr(mod, "time", clock):
            session = ClockedSession(clock)
            paced = mod._PacedCryoconSession(session)
            for _ in range(4):
                paced.query("INPUT? A")
        times = session.query_times
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert len(gaps) == 3, key
        assert all(g >= mod.CRYOCON_MIN_GAP_S - 1e-9 for g in gaps), \
            (key, gaps)
        # The first query of a fresh session is not held back.
        assert times[0] == 1000.0, (key, times)


def test_a_query_after_a_long_pause_is_not_delayed():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched_attr(mod, "time", clock):
            session = ClockedSession(clock)
            paced = mod._PacedCryoconSession(session)
            paced.query("INPUT? A")
            clock.now += 5.0
            before = len(clock.sleeps)
            paced.query("INPUT? A")
        assert len(clock.sleeps) == before, (key, clock.sleeps)


def test_a_failed_query_still_counts_for_the_pacing():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched_attr(mod, "time", clock):
            session = ClockedSession(clock, {"INPUT? B": Timeout()})
            paced = mod._PacedCryoconSession(session)
            try:
                paced.query("INPUT? B")
            except Timeout:
                pass
            else:
                raise AssertionError(key + ": the timeout was swallowed")
            paced.query("INPUT? A")
        gap = session.query_times[1] - session.query_times[0]
        assert gap >= mod.CRYOCON_MIN_GAP_S - 1e-9, (key, gap)


def test_the_gap_is_read_at_call_time():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        with patched_attr(mod, "time", clock), \
                patched_attr(mod, "CRYOCON_MIN_GAP_S", 0):
            session = ClockedSession(clock)
            paced = mod._PacedCryoconSession(session)
            for _ in range(3):
                paced.query("INPUT? A")
        assert clock.sleeps == [], (key, clock.sleeps)


def test_the_proxy_forwards_timeout_and_close_and_has_no_write():
    for key, mod in MODULES.items():
        session = ClockedSession(VirtualClock())
        paced = mod._PacedCryoconSession(session)
        paced.timeout = 10000
        assert session.timeout == 10000 and paced.timeout == 10000, key
        assert "timeout" not in vars(paced), key
        paced.close()
        assert session.closed, key
        assert "write" not in vars(mod._PacedCryoconSession), key
        assert not hasattr(mod._PacedCryoconSession, "write"), key
        # Nothing to forward to, so nothing to write with.
        assert not hasattr(paced, "write"), key


def test_open_cryocon_session_returns_a_paced_session():
    for key, mod in MODULES.items():
        session = FakeSession({"*IDN?": CRYOCON_IDN, "INPUT A:UNITS?": "K",
                               "INPUT? A": "77.350"})
        with patched_attr(mod, "pyvisa", FakeVisa(FakeRM([session]))), \
                patched_attr(mod, "CRYOCON_MIN_GAP_S", 0):
            inst, idn = mod.open_cryocon_session("GPIB1::23::INSTR")
            assert isinstance(inst, mod._PacedCryoconSession), key
            assert inst._session is session and idn == CRYOCON_IDN, key
            assert session.timeout == 10000, key
            session.queries.clear()
            session.replies["*IDN?"] = CRYOCON_IDN
            with patched_attr(mod, "pyvisa",
                              FakeVisa(FakeRM([session]))):
                monitor = mod.Cryocon34Monitor("GPIB1::23::INSTR", "A")
            assert isinstance(monitor.instrument,
                              mod._PacedCryoconSession), key
            assert monitor.read_temperature() == 77.35, key
        assert session.writes == [], key


# ============================================== comm errors never end it

def test_a_thermometer_comm_error_reconnects_and_resumes():
    for key in MODULES:
        run = WorkerRun(key, [300.0, Timeout("VI_ERROR_TMO"), 301.0]).run()
        assert run.final == ("done", "Stopped."), (key, run.final)
        assert run.reconnect.calls == 1, key
        assert run.logged[0] == 300.0 and 301.0 in run.logged, \
            (key, run.logged)
        assert len(run.drives) == 2, (key, run.drives)
        assert any("COMM ERROR (failure #1)" in m and "Timeout" in m
                   for m in run.logs()), (key, run.logs())
        assert any("Reconnecting" in s for s in run.statuses()), key
        assert run.shutdowns == 1, key


def test_a_detector_comm_error_reconnects_and_resumes():
    for key in MODULES:
        run = WorkerRun(key, [300.0], measure=[Timeout("VI_ERROR_TMO")],
                        points=10).run()
        assert run.final == ("done", "Stopped."), (key, run.final)
        assert run.reconnect.calls == 1, key
        assert len(run.logged) >= 2, (key, run.logged)
        assert len(run.drives) == 2, (key, run.drives)


def test_a_drive_comm_error_reconnects_and_resumes():
    for key in MODULES:
        run = WorkerRun(key, [300.0], drive=[Timeout("VI_ERROR_TMO")],
                        points=10).run()
        assert run.final == ("done", "Stopped."), (key, run.final)
        assert run.reconnect.calls == 1, key
        assert len(run.drives) == 2, (key, run.drives)
        assert len(run.logged) >= 2, (key, run.logged)


def test_a_refused_setpoint_is_retried_and_never_fails_the_run():
    """_apply_drive raises RuntimeError when SYST:ERR? is non-zero. Any
    exception from the drive is treated as a comm error."""
    for key in MODULES:
        run = WorkerRun(key, [300.0], points=10, drive=[
            RuntimeError("The 6221 refused the setpoint: -222, x")]).run()
        assert run.final[0] == "done", (key, run.final)
        assert run.logged, key


def test_the_drive_goes_back_on_with_the_runs_own_setpoint():
    for key in MODULES:
        run = WorkerRun(key, [Timeout(), 300.0, Timeout(), 300.0],
                        points=16).run()
        assert run.reconnect.calls == 2, key
        assert run.drives == [(FREQUENCY, CURRENT_PEAK)] * 3, \
            (key, run.drives)
        # ...and the worker settles after every one of them.
        assert run.sleeps.count(SETTLE_S) == 3, (key, run.sleeps)


def test_the_backoff_escalates_5_10_30_60_then_holds():
    for key in MODULES:
        fail = ConnectionError("still no reply")
        run = WorkerRun(key, [Timeout(), 300.0], points=14,
                        reconnect=[fail, fail, fail, fail]).run()
        assert run.backoffs == [5, 10, 30, 60, 60], (key, run.backoffs)
        assert run.reconnect.calls == 5, key
        assert run.final == ("done", "Stopped."), (key, run.final)
        assert 300.0 in run.logged, key
        assert sum("Reconnect failed" in m for m in run.logs()) == 4, key


def test_back_to_back_failures_escalate_and_a_good_point_resets():
    for key in MODULES:
        run = WorkerRun(key, [Timeout(), Timeout(), 300.0, Timeout(),
                              300.0], points=16).run()
        assert run.backoffs == [5, 10, 5], (key, run.backoffs)


def test_the_elapsed_time_origin_is_not_touched_by_a_reconnect():
    for key in MODULES:
        run = WorkerRun(key, [Timeout(), 300.0], points=8)
        run.gui.start_time = 12345.0
        run.run()
        assert run.gui.start_time == 12345.0, key


def test_stop_during_the_backoff_ends_promptly_with_current_off():
    """Real _sleep_interruptibly on a virtual clock: Stop is pressed 2 s
    into the 5 s wait, and the worker is out within one 0.1 s slice."""
    for key, mod in MODULES.items():
        clock = VirtualClock()
        run = WorkerRun(key, [Timeout("VI_ERROR_TMO")], real_sleep=True)
        pressed = {}

        def press_stop(c):
            if not pressed and c.now >= 1002.0:
                run.gui.stop_requested = True
                pressed["at"] = c.now
        clock.on_sleep = press_stop
        with patched_attr(mod, "time", clock):
            run.run()
        assert run.final == ("done", "Stopped."), (key, run.final)
        assert run.reconnect.calls == 0, key
        assert run.shutdowns == 1, key
        assert clock.now - pressed["at"] <= 0.1 + 1e-9, \
            (key, clock.now - pressed["at"])
        assert max(clock.sleeps) <= 0.1 + 1e-9, key   # Stop polled often


def test_stop_during_the_settle_still_reports_done():
    """Before v1.1 a Stop during the settle returned without 'done', and
    the GUI stayed 'running' with the current already off."""
    for key in MODULES:
        run = WorkerRun(key, [300.0], points=1).run()
        assert run.final == ("done", "Stopped."), (key, run.final)
        assert run.logged == [], key
        assert run.shutdowns == 1, key


def test_a_sensor_fault_never_triggers_a_reconnect():
    for key in MODULES:
        run = WorkerRun(key, [NAN], points=8).run()
        assert run.final == ("done", "Stopped."), (key, run.final)
        assert run.reconnect.calls == 0, key
        assert run.logged and all(t != t for t in run.logged), key


def test_the_stop_window_still_ends_the_run_after_a_reconnect():
    for key in MODULES:
        run = WorkerRun(key, [300.0, Timeout(), NAN, 401.0]).run()
        assert run.final[0] == "done", (key, run.final)
        assert "rose to 401.000 K" in run.final[1], (key, run.final)
        assert run.reconnect.calls == 1, key


def test_only_a_genuine_bug_is_failed_and_it_carries_its_traceback():
    for key in MODULES:
        run = WorkerRun(key, [300.0],
                        emit_error=TypeError("a real bug")).run()
        assert run.final[0] == "failed", (key, run.final)
        assert isinstance(run.final[1], TypeError), key
        assert run.reconnect.calls == 0, key
        assert any(m.startswith("WORKER TRACEBACK:") and "TypeError" in m
                   and "NoneType: None" not in m
                   for m in run.logs()), (key, run.logs())
        assert run.shutdowns == 1, key
        assert run.awake == [True, False], key


# =========================================== the real reconnect, faked bus

class FakeSource:
    def __init__(self, address):
        self.address = address
        self.idn = "KEITHLEY INSTRUMENTS INC.,MODEL 6221,1,A"
        self.closed = False
        self.prepared = []

    def prepare(self, *args):
        self.prepared.append(args)

    def read_error(self):
        return 0, "No error"

    def close(self):
        self.closed = True


class FakeDetector:
    def __init__(self, address):
        self.address = address
        self.idn = "fake detector"
        self.closed = False
        self.configured = []

    def configure_acv(self, range_name):
        self.configured.append(range_name)
        return "F1R0X"

    def configure_for_external_reference(self, *args):
        self.configured.append(args)

    def read_settings(self):
        return {"phas": 0.0}

    def close(self):
        self.closed = True


class FakeMonitor:
    def __init__(self, address, channel="A", log=None):
        self.address = address
        self.channel = channel
        self.closed = False

    def close(self):
        self.closed = True


DETECTOR_CLASS = {"k197a": "Keithley197AMeter", "sr830": "SR830Lockin"}


def _reconnect_gui(key):
    mod = MODULES[key]
    gui = object.__new__(mod.ACResistanceCC34SensingGUI)
    gui.io_lock = threading.Lock()
    gui.data_queue = queue.Queue()
    gui.params = {
        "compliance": 10.0, "pmark_line": 3, "pmark_phase": 0.0,
        "detector": {"range_name": "auto", "averages": 5,
                     "read_interval": 0.4, "harmonic": 1, "phase": 12.5,
                     "codes": {"isrc": 0, "icpl": 0, "ignd": 0, "ilin": 3,
                               "sync": 1, "sens": 20, "oflt": 9,
                               "ofsl": 3, "rmod": 1}},
    }
    gui.source = FakeSource("GPIB0::13::INSTR")
    gui.detector = FakeDetector("GPIB0::7::INSTR")
    gui.thermometer = FakeMonitor("GPIB0::23::INSTR", "C")

    def no_new_file(settings):
        raise AssertionError("a reconnect opened a new output file")
    gui._open_log_file = no_new_file
    return mod, gui


def test_reconnect_reopens_every_instrument_and_configures_it_again():
    for key in MODULES:
        mod, gui = _reconnect_gui(key)
        old = (gui.source, gui.detector, gui.thermometer)
        held = []
        real_configure = gui._configure_instruments

        def configure():
            held.append(gui.io_lock.locked())
            return real_configure()
        gui._configure_instruments = configure
        with patched_attr(mod, "K6221WaveSource", FakeSource), \
                patched_attr(mod, DETECTOR_CLASS[key], FakeDetector), \
                patched_attr(mod, "Cryocon34Monitor", FakeMonitor):
            gui._reconnect_instruments()
        assert all(o.closed for o in old), key
        new = (gui.source, gui.detector, gui.thermometer)
        assert all(n is not o for n, o in zip(new, old)), key
        assert [n.address for n in new] == [o.address for o in old], key
        assert gui.thermometer.channel == "C", key
        assert held == [True], (key, held)     # set up under io_lock
        assert gui.source.prepared == [(10.0, mod.USE_PHASE_MARKER, 3,
                                        0.0)], key
        assert len(gui.detector.configured) == 1, key
        assert not gui.io_lock.locked(), key


def test_a_reconnect_that_fails_part_way_is_retried_cleanly():
    """A 6221 re-opened but a detector still dead: the next attempt closes
    the new 6221 and starts again, and the addresses are never lost."""
    for key in MODULES:
        mod, gui = _reconnect_gui(key)

        class DeadDetector:
            def __init__(self, address):
                raise Timeout("VI_ERROR_TMO")
        with patched_attr(mod, "K6221WaveSource", FakeSource), \
                patched_attr(mod, DETECTOR_CLASS[key], DeadDetector), \
                patched_attr(mod, "Cryocon34Monitor", FakeMonitor):
            try:
                gui._reconnect_instruments()
            except Timeout:
                pass
            else:
                raise AssertionError(key + ": a dead detector reconnected")
        half_open = gui.source
        assert not gui.io_lock.locked(), key
        with patched_attr(mod, "K6221WaveSource", FakeSource), \
                patched_attr(mod, DETECTOR_CLASS[key], FakeDetector), \
                patched_attr(mod, "Cryocon34Monitor", FakeMonitor):
            gui._reconnect_instruments()
        assert half_open.closed, key
        assert gui.detector.address == "GPIB0::7::INSTR", key


# ======================================================= durable writes

class GuiLog:
    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)


def _writer_gui(key):
    gui = object.__new__(MODULES[key].ACResistanceCC34SensingGUI)
    gui._pending_rows = MODULES[key].deque(maxlen=20000)
    gui._write_error_logged = False
    gui.log = GuiLog()
    return gui


def test_rows_are_held_in_order_while_the_disk_refuses_them():
    for key in MODULES:
        gui = _writer_gui(key)
        root = tempfile.mkdtemp()
        try:
            folder = os.path.join(root, "share")
            path = os.path.join(folder, "run.dat")
            gui._write_or_buffer(path, "row 1\n")     # folder missing
            gui._write_or_buffer(path, "row 2\n")
            assert [t for _p, t in gui._pending_rows] == ["row 1\n",
                                                           "row 2\n"], key
            errors = [m for m in gui.log.lines if "WRITE ERROR" in m]
            assert len(errors) == 1, (key, gui.log.lines)
            gui._flush_pending_rows()                  # still missing
            assert len(gui._pending_rows) == 2, key
            os.makedirs(folder)                        # the share is back
            gui._flush_pending_rows()
            assert not gui._pending_rows, key
            gui._write_or_buffer(path, "row 3\n")
            with open(path) as handle:
                assert handle.read() == "row 1\nrow 2\nrow 3\n", key
            recovered = [m for m in gui.log.lines if "recovered" in m]
            assert len(recovered) == 1, (key, gui.log.lines)
            assert len([m for m in gui.log.lines
                        if "WRITE ERROR" in m]) == 1, key
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_a_new_row_joins_the_queue_while_rows_are_pending():
    for key in MODULES:
        gui = _writer_gui(key)
        written = []
        gui._durable_write = lambda p, t: written.append(t)
        gui._pending_rows.append(("x.dat", "old\n"))
        gui._write_or_buffer("x.dat", "new\n")
        assert written == [], key          # never written ahead of "old"
        assert [t for _p, t in gui._pending_rows] == ["old\n", "new\n"], key


def test_every_row_is_fsynced():
    for key, mod in MODULES.items():
        gui = _writer_gui(key)
        synced = []
        real_fsync = mod.os.fsync

        def fsync(fd):
            synced.append(fd)
            return real_fsync(fd)
        root = tempfile.mkdtemp()
        try:
            with patched_attr(mod.os, "fsync", fsync):
                gui._durable_write(os.path.join(root, "a.dat"), "row\n")
            assert len(synced) == 1, key
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------- the data line, byte for byte

class _Var:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class _Line:
    def set_data(self, x, y):
        pass


class _Axis:
    def relim(self):
        pass

    def autoscale_view(self):
        pass


class _Canvas:
    def draw_idle(self):
        pass


def _record_gui(key, path):
    gui = _writer_gui(key)
    gui.data_filepath = path
    gui.readout_vars = {k: _Var() for k in (
        "resistance", "voltage", "spread", "freq", "temperature", "theta")}
    gui.data_storage = {"x": [], "y": [], "sub_x": [], "sub_y": []}
    gui.line_main, gui.line_sub = _Line(), _Line()
    gui.ax_main, gui.ax_sub = _Axis(), _Axis()
    gui.canvas = _Canvas()
    return gui


POINTS = {
    "k197a": {"voltage": 1.234e-3, "spread": 5e-6, "count": 5,
              "resistance": 123.4, "magnitude": 123.4},
    "sr830": {"locked_hz": 133.0001, "x": 1.2e-4, "y": -3e-6,
              "r_volts": 1.2004e-4, "theta": -1.4321, "resistance": 16.97,
              "magnitude": 16.98, "voltage": 1.2e-4},
}

# Written out by hand from the v1.0 row code, so a format change fails here.
EXPECTED = {
    "k197a": ("2026-09-25 03:00:00,3600.500,nan,133.000000,1.414200E-05,"
              "1.000000E-05,1.234000E-03,5.000000E-06,5,1.234000E+02,,,"
              "ok\n"),
    "sr830": ("2026-09-25 03:00:00,3600.500,nan,133.000000,1.414200E-05,"
              "1.000000E-05,133.000100,1.200000E-04,-3.000000E-06,"
              "1.200400E-04,-1.4321,1.697000E+01,1.698000E+01,,,"
              "ok\n"),
}


class _FixedNow(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 25, 3, 0, 0)


def test_the_data_line_is_unchanged_including_a_nan_temperature():
    for key, mod in MODULES.items():
        root = tempfile.mkdtemp()
        try:
            path = os.path.join(root, "run.dat")
            gui = _record_gui(key, path)
            point = dict(POINTS[key])
            point.update({"elapsed": 3600.5, "frequency": 133.0,
                          "current_peak": 1.4142e-5, "current_rms": 1e-5,
                          "temperature": NAN, "resistivity": None,
                          "sheet": None, "problems": []})
            with patched_attr(mod, "datetime", _FixedNow):
                gui._record_point(point)
            with open(path, "rb") as handle:
                raw = handle.read()
            want = EXPECTED[key].replace("\n", os.linesep).encode("ascii")
            assert raw == want, (key, raw, want)
            columns = mod.DATA_COLUMNS.split(",")
            assert len(EXPECTED[key].rstrip("\n").split(",")) \
                == len(columns), key
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_the_row_reaches_disk_even_if_the_readout_fails():
    for key in MODULES:
        root = tempfile.mkdtemp()
        try:
            path = os.path.join(root, "run.dat")
            gui = _record_gui(key, path)
            gui.readout_vars = {}               # every readout will fail
            point = dict(POINTS[key])
            point.update({"elapsed": 1.0, "frequency": 133.0,
                          "current_peak": 1.4142e-5, "current_rms": 1e-5,
                          "temperature": 300.0, "resistivity": None,
                          "sheet": None, "problems": ["one, two"]})
            try:
                gui._record_point(point)
            except KeyError:
                pass
            with open(path) as handle:
                line = handle.read()
            assert line.endswith(",one; two\n"), (key, line)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ============================================ no dialogs, a live pump

class NoDialogs:
    """Every messagebox function raises: a dialog on these paths is a bug."""

    def __getattr__(self, name):
        def refuse(*args, **kwargs):
            raise AssertionError("messagebox.%s opened: %r" % (name, args))
        return refuse


class _Button:
    def __init__(self):
        self.state = None

    def config(self, **kwargs):
        self.state = kwargs.get("state", self.state)


class _Root:
    def __init__(self):
        self.after_calls = []
        self.bells = 0

    def after(self, ms, func):
        self.after_calls.append(ms)

    def bell(self):
        self.bells += 1


def _pump_gui(key):
    gui = _writer_gui(key)
    gui.root = _Root()
    gui.status_var = _Var()
    gui.start_button, gui.stop_button, gui.disconnect_button = (
        _Button(), _Button(), _Button())
    gui.data_queue = queue.Queue()
    gui.is_running = True
    gui.stop_requested = False
    gui.beeps = []
    gui._beep = lambda times=1: gui.beeps.append(times)
    return gui


def test_the_worker_never_opens_a_dialog_on_error_stop_or_completion():
    for key, mod in MODULES.items():
        with patched_attr(mod, "messagebox", NoDialogs()):
            WorkerRun(key, [300.0, Timeout(), 300.0]).run()   # comm error
            WorkerRun(key, [300.0], points=3).run()           # Stop
            WorkerRun(key, [300.0, 500.0]).run()              # the window
            WorkerRun(key, [300.0], emit_error=TypeError()).run()   # bug


def test_the_end_of_a_run_is_a_log_a_status_and_a_beep_never_a_dialog():
    for key, mod in MODULES.items():
        for final, beeps, status in (
                (("done", "Temperature rose to 401.000 K, above the 400.000 "
                  "K stop."), [1], "rose to 401.000 K"),
                (("done", "Stopped."), [1], "Stopped."),
                (("failed", RuntimeError("boom")), [3], "Stopped on an "
                 "error")):
            gui = _pump_gui(key)
            gui.data_queue.put(("log", "a line before the end"))
            gui.data_queue.put(final)
            with patched_attr(mod, "messagebox", NoDialogs()):
                gui._process_data_queue()
            assert status in gui.status_var.value, (key, final)
            assert gui.beeps == beeps, (key, final, gui.beeps)
            assert gui.is_running is False, key
            assert gui.start_button.state == "normal", key
            assert gui.root.after_calls == [], key     # the pump stops
            assert "a line before the end" in gui.log.lines, key


def test_stop_run_opens_no_dialog():
    for key, mod in MODULES.items():
        gui = _pump_gui(key)
        with patched_attr(mod, "messagebox", NoDialogs()):
            gui.stop_run()
        assert gui.stop_requested is True, key


def test_the_pump_survives_a_gui_side_exception():
    for key, mod in MODULES.items():
        gui = _pump_gui(key)

        def broken(point):
            raise ValueError("plot blew up")
        gui._record_point = broken
        gui.data_queue.put(("point", {}))
        gui.data_queue.put(("log", "still alive"))
        with patched_attr(mod, "messagebox", NoDialogs()):
            gui._process_data_queue()
        assert any(m.startswith("GUI ERROR (non-fatal)")
                   and "plot blew up" in m for m in gui.log.lines), \
            (key, gui.log.lines)
        assert "still alive" in gui.log.lines, key
        assert gui.root.after_calls == [200], key   # rescheduled


def test_the_pump_reschedules_even_when_the_log_itself_fails():
    for key in MODULES:
        gui = _pump_gui(key)

        def dead_log(message):
            raise RuntimeError("console gone")
        gui.log = dead_log
        gui.data_queue.put(("log", "x"))
        gui._process_data_queue()
        assert gui.root.after_calls == [200], key


def test_rows_still_held_at_the_end_are_flushed_or_reported():
    for key in MODULES:
        gui = _pump_gui(key)
        written = []
        gui._durable_write = lambda p, t: written.append(t)
        gui._pending_rows.append(("x.dat", "held\n"))
        gui._write_error_logged = True
        gui.data_queue.put(("done", "Stopped."))
        gui._process_data_queue()
        assert written == ["held\n"], key


def test_beep_falls_back_to_the_tk_bell_without_winsound():
    for key, mod in MODULES.items():
        gui = object.__new__(mod.ACResistanceCC34SensingGUI)
        gui.root = _Root()
        with patched_attr(mod, "HAS_WINSOUND", False):
            gui._beep(times=3)
        assert gui.root.bells == 1, key


# ============================================================ keep-awake

class _Kernel32:
    def __init__(self):
        self.flags = []

    def SetThreadExecutionState(self, flags):
        self.flags.append(flags)
        return 1


class _Ctypes:
    def __init__(self):
        self.windll = type("windll", (), {})()
        self.windll.kernel32 = _Kernel32()


def test_keep_awake_is_on_for_the_run_and_off_after_it_whatever_ends_it():
    for key in MODULES:
        for run in (WorkerRun(key, [300.0], points=4),               # Stop
                    WorkerRun(key, [300.0, 401.0]),                  # window
                    WorkerRun(key, [Timeout(), 300.0], points=6),    # comm
                    WorkerRun(key, [300.0], emit_error=TypeError())):  # bug
            run.run()
            assert run.awake == [True, False], (key, run.final, run.awake)


def test_keep_awake_sends_the_right_flags_and_never_raises():
    for key, mod in MODULES.items():
        gui = object.__new__(mod.ACResistanceCC34SensingGUI)
        fake = _Ctypes()
        with patched_attr(mod, "ctypes", fake):
            gui._set_keep_awake(True)
            gui._set_keep_awake(False)
        assert fake.windll.kernel32.flags == [0x80000001, 0x80000000], key
        with patched_attr(mod, "ctypes", object()):   # no windll at all
            gui._set_keep_awake(True)


# ================================================================ version

def test_both_modules_say_they_are_v1_1():
    for key, mod in MODULES.items():
        assert mod.PROGRAM_VERSION == "1.1", key
        assert mod.ACResistanceCC34SensingGUI.PROGRAM_VERSION == "1.1", key
        assert "V: 1.1" in mod.__doc__ and "25 Sep 2026" in mod.__doc__, key


# ================================= SR830: a reconnect must not move the phase
#
# Found in review, 26 Sep 2026: after a reconnect the lock-in is restored
# from the run's stored parameters, i.e. the phase typed in the GUI, and
# Auto Phase then re-zeroed at whatever the temperature was by then. With
# Auto Phase on, X and Y would step at every recovery. The phase Auto Phase
# finds at Start is now read back once and kept as the run's phase.

class PhaseLockin:
    def __init__(self, found_phase):
        self.found_phase = found_phase
        self.aphs = 0
        self.agan = 0

    def auto_phase(self):
        self.aphs += 1

    def auto_gain(self):
        self.agan += 1

    def read_phase(self):
        return self.found_phase


def _phase_run(auto_phase, auto_gain, found_phase=37.5):
    run = WorkerRun("sr830", [300.0, IOError("VI_ERROR_TMO"), 300.0],
                    points=8)
    gui = run.gui
    gui.params["detector"] = {"auto_phase": auto_phase,
                              "auto_gain": auto_gain, "phase": 0.0}
    gui.detector = PhaseLockin(found_phase)
    real = run.mod.ACResistanceCC34SensingGUI
    gui._auto_functions = real._auto_functions.__get__(gui)
    gui._keep_auto_phase = real._keep_auto_phase.__get__(gui)
    phases_at_reconnect = []

    def reconnect():
        phases_at_reconnect.append(gui.params["detector"]["phase"])
    gui._reconnect_instruments = reconnect
    run.run()
    return run, gui, phases_at_reconnect


def test_auto_phase_runs_once_and_every_reconnect_restores_its_phase():
    run, gui, phases = _phase_run(auto_phase=True, auto_gain=False)
    assert run.final == ("done", "Stopped."), run.final
    assert phases == [37.5], phases              # restored, not 0.0
    assert gui.detector.aphs == 1, gui.detector.aphs
    assert gui.params["detector"]["auto_phase"] is False
    assert any("37.50 deg" in line for line in run.logs()), run.logs()


def test_auto_gain_still_reruns_after_a_reconnect():
    """Gain changes the range, not the value: rerunning it is safe."""
    run, gui, phases = _phase_run(auto_phase=False, auto_gain=True)
    assert gui.detector.agan == 2, gui.detector.agan
    assert gui.detector.aphs == 0
    assert phases == [0.0], phases               # the entered phase, kept


def test_the_k197a_module_has_no_phase_to_keep():
    assert not hasattr(MODULES["k197a"].ACResistanceCC34SensingGUI,
                       "_keep_auto_phase")


def _run_all():
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
