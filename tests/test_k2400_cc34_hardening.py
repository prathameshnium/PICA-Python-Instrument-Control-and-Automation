"""Unattended-run hardening of the two GUI-thread Cryo-con R-T programs.

RT_K2400_CC34_T_Sensing_GUI.py and RT_K2400_K2182_CC34_T_Sensing_GUI.py
measure on the Tk main thread through a root.after() chain. v1.1 (25 Sep
2026) hardened both for overnight runs without moving them off that thread:

  1. every Cryo-con query is held CRYOCON_MIN_GAP_S (0.08 s) after the last;
  2. a comm error never ends a run: the instruments are re-opened after
     5, 10, 30, 60, 60 ... s until they answer, then logging resumes;
  3. data rows are fsync'd, and buffered in order on a disk error;
  4. no message box on any run, error, stop or completion path;
  5. Windows is kept awake while a run is live.

No hardware and no Tk root: the GUI objects are built with object.__new__
and a fake root whose after() callbacks are fired by hand, so the whole
reconnect state machine is walked one scheduled step at a time.

Runnable as plain Python as well as under pytest.
"""

import csv
import importlib.util
import math
import os
import sys
import tempfile
import types
from collections import deque

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

MODULE_PATHS = {
    "k2400": os.path.join(REPO_ROOT, "pica", "keithley", "k2400",
                          "RT_K2400_CC34_T_Sensing_GUI.py"),
    "k2400_2182": os.path.join(REPO_ROOT, "pica", "keithley", "k2400_2182",
                               "RT_K2400_K2182_CC34_T_Sensing_GUI.py"),
}
GUI_CLASS = {"k2400": "RT_GUI_Passive", "k2400_2182": "VT_GUI_Passive"}
BACKEND_CLASS = {"k2400": "RT_Backend_Passive",
                 "k2400_2182": "VT_Backend_Passive"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Private copies under their own names, so nothing here leaks into the other
# test files that load the same programs.
MODULES = {key: _load("k2400_hardening_" + key, path)
           for key, path in MODULE_PATHS.items()}

CRYOCON_IDN = "Cryocon Model 34, Rev 3.03A"
CRYOCON_ADDR = "GPIB0::23::INSTR"
K2400_ADDR = "GPIB1::4::INSTR"
K2182_ADDR = "GPIB0::7::INSTR"


# ------------------------------------------------------------------ fakes

class VirtualClock:
    """Stands in for a module's `time`: sleeps advance it instantly."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class swap:
    """Replace one module global for the duration of a with-block."""

    def __init__(self, module, name, value):
        self.module, self.name, self.value = module, name, value

    def __enter__(self):
        self._old = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.module, self.name, self._old)
        return False


class NoDialogs:
    """A messagebox that records and refuses every call.

    Recorded as well as raised: the non-fatal GUI handler in the tick
    catches Exception, so a raise alone could be swallowed and logged."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def refuse(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"messagebox.{name} opened on an "
                                 "unattended path")
        return refuse


class FakeSession:
    """A VISA session that stamps every query with the virtual time."""

    def __init__(self, clock, fail_first=False):
        self.clock = clock
        self.stamps = []
        self.timeout = 2000
        self.closed = False
        self.writes = []
        self._fail_next = fail_first

    def query(self, command):
        self.stamps.append(self.clock.now)
        if self._fail_next:
            self._fail_next = False
            raise IOError("VI_ERROR_TMO: timeout expired")
        return "77.350"

    def write(self, command):
        self.writes.append(command)

    def close(self):
        self.closed = True


class ReadOnlySession:
    """No write() at all, so a proxy has nothing to forward one to."""

    def query(self, command):
        return "77.350"


class FakeInstrument:
    """A Cryo-con or a K2182 behind a fake resource manager."""

    def __init__(self, idn, clock=None):
        self.idn = idn
        self.clock = clock
        self.writes = []
        self.queries = []
        self.stamps = []
        self.closed = False
        self.timeout = None

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        if self.clock is not None:
            self.stamps.append(self.clock.now)
        cmd = command.strip()
        if cmd == "*IDN?":
            return self.idn
        if cmd.endswith(":UNITS?"):
            return "K"
        if cmd.startswith("INPUT?"):
            return "77.350"
        return "0"

    def close(self):
        self.closed = True


class FakeBus:
    """Every open_resource hands back a FRESH instrument, as a resource
    re-opened after a power cycle would be."""

    def __init__(self, clock=None):
        self.clock = clock
        self.opened = []

    def open_resource(self, address, **kwargs):
        idn = (CRYOCON_IDN if "::23::" in address
               else "KEITHLEY INSTRUMENTS INC.,MODEL 2182")
        inst = FakeInstrument(idn, self.clock)
        self.opened.append((address, inst))
        return inst

    def list_resources(self):
        return (CRYOCON_ADDR, K2400_ADDR, K2182_ADDR)

    def of(self, address):
        return [inst for addr, inst in self.opened if addr == address]


def _fake_pyvisa(bus):
    return types.SimpleNamespace(ResourceManager=lambda: bus, errors=None)


class FakeAdapter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _k2400_factory(made):
    """A stand-in for pymeasure's Keithley2400 that records its setup."""

    class FakeK2400:
        def __init__(self, address):
            object.__setattr__(self, "calls", [])
            object.__setattr__(self, "address", address)
            object.__setattr__(self, "adapter", FakeAdapter())
            object.__setattr__(self, "id",
                               "KEITHLEY INSTRUMENTS INC.,MODEL 2400")
            made.append(self)

        def __setattr__(self, name, value):
            self.calls.append((name, value))
            object.__setattr__(self, name, value)

        def reset(self):
            self.calls.append("reset")

        def use_front_terminals(self):
            self.calls.append("use_front_terminals")

        def apply_current(self):
            self.calls.append("apply_current")

        def measure_voltage(self):
            self.calls.append("measure_voltage")

        def enable_source(self):
            self.calls.append("enable_source")

        def shutdown(self):
            self.calls.append("shutdown")

    return FakeK2400


class FakeRoot:
    """Records after() calls; callbacks are fired by hand with fire()."""

    def __init__(self):
        self.pending = {}          # after id -> (delay_ms, callback)
        self.cancelled = []
        self.titles = []
        self._count = 0

    def after(self, delay_ms, callback):
        self._count += 1
        after_id = f"after#{self._count}"
        self.pending[after_id] = (delay_ms, callback)
        return after_id

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)
        self.pending.pop(after_id, None)

    def only_pending(self):
        """(delay_ms, callback) of the ONE scheduled step. Two would mean
        two after() chains on one set of VISA sessions."""
        assert len(self.pending) == 1, self.pending
        return next(iter(self.pending.values()))

    def fire(self):
        (after_id, (delay_ms, callback)), = self.pending.items()
        del self.pending[after_id]
        callback()
        return delay_ms

    def title(self, text=None):
        if text is not None:
            self.titles.append(text)

    def bell(self):
        pass


class FakeWidget:
    def config(self, **kwargs):
        pass


class FakeCanvas:
    def __init__(self):
        self.draws = 0

    def draw(self):
        self.draws += 1

    def draw_idle(self):
        self.draws += 1


class FakeBackend:
    """Scripted get_measurement() and reconnect() results.

    A measurement script's last item repeats for ever; an exception
    instance in either script is raised instead of returned."""

    def __init__(self, measurements=((77.35, 1.0e-3),), reconnects=()):
        self.measurements = list(measurements)
        self.reconnects = list(reconnects)
        self.connects = 0
        self.reconnect_calls = 0
        self.shutdowns = 0
        self.rm = object()

    def connect(self, *args):
        self.connects += 1

    def configure_instruments(self, *args):
        pass

    def get_measurement(self):
        if len(self.measurements) > 1:
            item = self.measurements.pop(0)
        else:
            item = self.measurements[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def reconnect(self):
        self.reconnect_calls += 1
        item = self.reconnects.pop(0) if self.reconnects else None
        if isinstance(item, BaseException):
            raise item

    def shutdown(self):
        self.shutdowns += 1


def _glitch():
    return IOError("VI_ERROR_TMO (-1073807339): Timeout expired before "
                   "operation completed.")


def _gui(key, backend, folder, stub_keep_awake=True):
    """A GUI object with just the attributes the run path touches."""
    mod = MODULES[key]
    gui = object.__new__(getattr(mod, GUI_CLASS[key]))
    gui.root = FakeRoot()
    gui.backend = backend
    gui.is_running = False
    gui._after_id = None
    gui._comm_failures = 0
    gui._pending_rows = deque(maxlen=20000)
    gui._write_error_logged = False
    gui._base_title = "R-T under test"
    gui.logs = []
    gui.log = gui.logs.append
    gui.beeps = []
    gui._beep = lambda times=1: gui.beeps.append(times)
    if stub_keep_awake:
        gui.keep_awake = []
        gui._set_keep_awake = gui.keep_awake.append
    figure = Figure()
    gui.ax_main = figure.add_subplot(111)
    gui.line_main, = gui.ax_main.plot([], [])
    gui.canvas = FakeCanvas()
    gui.data_storage = {'temperature': [], 'voltage': [], 'resistance': []}
    gui.start_button = gui.stop_button = FakeWidget()
    gui.entries = {"Sample Name": FakeWidget()}
    gui.cc_cb = gui.k2400_cb = gui.k2182_cb = FakeWidget()
    params = {'name': 'S1', 'save_path': folder, 'cc_visa': CRYOCON_ADDR,
              'current_ma': 1.0, 'compliance_v': 10.0, 'delay_s': 1.0,
              'k2400_visa': K2400_ADDR, 'k2182_visa': K2182_ADDR}
    gui._validate_and_get_params = lambda: dict(params)
    return gui


def _started(key, backend, folder, **kwargs):
    gui = _gui(key, backend, folder, **kwargs)
    gui.start_experiment()
    assert gui.is_running, (key, gui.logs)
    delay_ms, callback = gui.root.only_pending()
    assert (delay_ms, callback) == (100, gui._experiment_loop), key
    return gui


# ------------------------------------------------------ 1. bus pacing

def test_the_pacing_gap_and_timeout_are_left_alone():
    """The user wants the bus slow: 0.08 s gap, 10 s operation timeout."""
    for key, mod in MODULES.items():
        assert mod.CRYOCON_MIN_GAP_S == 0.08, key
        assert mod.CRYOCON_TIMEOUT_MS == 10000, key


def test_back_to_back_queries_are_held_the_minimum_gap_apart():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        session = FakeSession(clock)
        with swap(mod, "time", clock):
            paced = mod._PacedCryoconSession(session)
            for _ in range(3):
                assert paced.query("INPUT? A") == "77.350", key
        gaps = [b - a for a, b in zip(session.stamps, session.stamps[1:])]
        assert len(gaps) == 2, key
        for gap in gaps:
            assert gap >= mod.CRYOCON_MIN_GAP_S - 1e-9, (key, gaps)
        assert clock.sleeps, key


def test_no_wait_when_the_bus_has_been_quiet_long_enough():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        session = FakeSession(clock)
        with swap(mod, "time", clock):
            paced = mod._PacedCryoconSession(session)
            paced.query("INPUT? A")
            clock.now += 1.0
            before = len(clock.sleeps)
            paced.query("INPUT? A")
        assert len(clock.sleeps) == before, (key, clock.sleeps)


def test_a_failed_query_still_counts_toward_the_gap():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        session = FakeSession(clock, fail_first=True)
        with swap(mod, "time", clock):
            paced = mod._PacedCryoconSession(session)
            try:
                paced.query("INPUT? A")
            except IOError:
                pass
            else:
                raise AssertionError(f"{key}: the comm error was swallowed")
            paced.query("INPUT? A")
        gap = session.stamps[1] - session.stamps[0]
        assert gap >= mod.CRYOCON_MIN_GAP_S - 1e-9, (key, gap)


def test_the_gap_is_read_at_call_time():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        session = FakeSession(clock)
        with swap(mod, "time", clock), swap(mod, "CRYOCON_MIN_GAP_S", 0):
            paced = mod._PacedCryoconSession(session)
            paced.query("INPUT? A")
            paced.query("INPUT? A")
        assert clock.sleeps == [], (key, clock.sleeps)


def test_timeout_and_close_reach_the_real_session():
    for key, mod in MODULES.items():
        session = FakeSession(VirtualClock())
        paced = mod._PacedCryoconSession(session)
        paced.timeout = 5000
        assert session.timeout == 5000, key
        assert paced.timeout == 5000, key
        assert "timeout" not in paced.__dict__, key
        paced.close()
        assert session.closed, key


def test_the_proxy_has_no_write_of_its_own():
    for key, mod in MODULES.items():
        assert "write" not in vars(mod._PacedCryoconSession), key
        # Nothing to forward to: no write at all.
        assert not hasattr(mod._PacedCryoconSession(ReadOnlySession()),
                           "write"), key
        # A stray write is forwarded, so the fakes still see it.
        session = FakeSession(VirtualClock())
        mod._PacedCryoconSession(session).write("STRAY")
        assert session.writes == ["STRAY"], key


def test_open_cryocon_session_returns_a_paced_session():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        bus = FakeBus(clock)
        with swap(mod, "time", clock), swap(mod, "pyvisa", _fake_pyvisa(bus)):
            inst, idn = mod.open_cryocon_session(CRYOCON_ADDR,
                                                 log=lambda m: None)
            assert idn == CRYOCON_IDN, key
            assert isinstance(inst, mod._PacedCryoconSession), key
            assert inst.timeout == mod.CRYOCON_TIMEOUT_MS, key
            inst.query("INPUT A:UNITS?")
        raw = bus.of(CRYOCON_ADDR)[-1]
        assert raw.queries == ["*IDN?", "INPUT A:UNITS?"], (key, raw.queries)
        # The first paced query is held off the '*IDN?' as well.
        assert raw.stamps[1] - raw.stamps[0] >= \
            mod.CRYOCON_MIN_GAP_S - 1e-9, (key, raw.stamps)
        assert raw.writes == [], (key, raw.writes)


# ------------------------------------- 2. backend reconnect from scratch

def test_backend_reconnect_rebuilds_every_session_from_the_start_settings():
    for key, mod in MODULES.items():
        clock = VirtualClock()
        bus = FakeBus(clock)
        made = []
        with swap(mod, "time", clock), \
                swap(mod, "pyvisa", _fake_pyvisa(bus)), \
                swap(mod, "Keithley2400", _k2400_factory(made)), \
                swap(mod, "PYMEASURE_AVAILABLE", True):
            backend = object.__new__(getattr(mod, BACKEND_CLASS[key]))
            backend.k2400 = backend.cryocon = backend.k2182 = None
            backend.rm = bus
            if key == "k2400":
                backend.connect(K2400_ADDR, CRYOCON_ADDR)
            else:
                backend.connect(K2400_ADDR, K2182_ADDR, CRYOCON_ADDR)
            backend.configure_instruments(1.5, 10.0)
            first_k2400 = backend.k2400
            backend.reconnect()

        assert len(made) == 2, key
        assert backend.k2400 is made[1], key
        assert first_k2400.adapter.closed, key
        assert made[1].address == K2400_ADDR, key
        # The same configuration sequence as Start, output back on.
        assert made[1].calls == first_k2400.calls, (key, made[1].calls)
        assert ("source_current", 1.5e-3) in made[1].calls, key
        assert ("compliance_voltage", 10.0) in made[1].calls, key
        assert "enable_source" in made[1].calls, key
        assert made[1].calls[0] == "reset", key

        cryocons = bus.of(CRYOCON_ADDR)
        assert len(cryocons) == 2, key
        assert cryocons[0].closed, key
        assert isinstance(backend.cryocon, mod._PacedCryoconSession), key
        assert "INPUT A:UNITS?" in cryocons[1].queries, key
        for inst in cryocons:
            assert inst.writes == [], (key, inst.writes)

        if key == "k2400_2182":
            k2182s = bus.of(K2182_ADDR)
            assert len(k2182s) == 2, key
            assert k2182s[0].closed, key
            assert backend.k2182 is k2182s[1], key
            assert k2182s[1].writes == ["*rst; status:preset; *cls"], key


# --------------------------- 3. comm errors never end a run (GUI side)

def test_a_comm_error_backs_off_5_10_30_60_60_and_then_resumes():
    for key in MODULES:
        backend = FakeBackend(
            measurements=[(77.35, 1.0e-3), _glitch(), (77.40, 1.1e-3)],
            reconnects=[_glitch()] * 4 + [None])
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, backend, folder)
            origin = gui.start_time
            root = gui.root
            root.fire()                                 # a good point
            assert root.only_pending() == (1000, gui._experiment_loop), key

            root.fire()                                 # the comm error
            assert gui.is_running, key
            assert gui.beeps == [1], (key, gui.beeps)
            assert any(m.startswith("COMM ERROR") for m in gui.logs), key
            assert "RECONNECTING" in root.titles[-1], (key, root.titles)

            delays = [root.only_pending()[0]]
            for _ in range(4):                          # four failed tries
                assert root.only_pending()[1] == gui._attempt_reconnect, key
                root.fire()
                assert gui.is_running, key
                delays.append(root.only_pending()[0])
            assert delays == [5000, 10000, 30000, 60000, 60000], \
                (key, delays)

            root.fire()                                 # reconnect works
            assert backend.reconnect_calls == 5, key
            assert "Reconnected. Resuming measurement." in gui.logs, key
            assert root.only_pending() == (100, gui._experiment_loop), key
            assert root.titles[-1] == gui._base_title, (key, root.titles)

            root.fire()                                 # measuring again
            assert root.only_pending() == (1000, gui._experiment_loop), key
            assert gui._comm_failures == 0, key
            assert gui.start_time == origin, key
            assert len(gui.data_storage['temperature']) == 2, key
            with open(gui.data_filepath, newline='') as fh:
                rows = list(csv.reader(fh))
            assert len(rows) == 3, (key, rows)          # header + 2 points
            gui.stop_experiment()


def test_a_comm_error_right_after_a_reconnect_keeps_escalating():
    """Only a GOOD measurement resets the backoff, so an instrument that
    reconnects but will not measure is not hammered every 5 s."""
    for key in MODULES:
        backend = FakeBackend(measurements=[_glitch()], reconnects=[None])
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, backend, folder)
            root = gui.root
            root.fire()                                 # error #1
            assert root.only_pending()[0] == 5000, key
            root.fire()                                 # reconnect works
            root.fire()                                 # error #2
            assert root.only_pending()[0] == 10000, key
            gui.stop_experiment()


def test_stop_during_backoff_cancels_the_pending_reconnect():
    for key in MODULES:
        backend = FakeBackend(measurements=[_glitch()])
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, backend, folder)
            root = gui.root
            root.fire()                                 # comm error
            pending_id = gui._after_id
            assert pending_id in root.pending, key
            gui.stop_experiment()
            assert pending_id in root.cancelled, key
            assert root.pending == {}, key
            assert not gui.is_running, key
            assert backend.shutdowns == 1, key
            # A stale callback that fires anyway must do nothing at all.
            gui._attempt_reconnect()
            gui._experiment_loop()
            assert backend.reconnect_calls == 0, key
            assert root.pending == {}, key


def test_stop_cancels_the_pending_measurement_tick():
    for key in MODULES:
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, FakeBackend(), folder)
            pending_id = gui._after_id
            gui.stop_experiment()
            assert pending_id in gui.root.cancelled, key
            assert gui.root.pending == {}, key


def test_start_is_idempotent():
    for key in MODULES:
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, backend, folder)
            gui.start_experiment()
            assert backend.connects == 1, key
            gui.root.only_pending()                     # still ONE chain
            gui.root.fire()
            gui.start_experiment()
            assert backend.connects == 1, key
            gui.root.only_pending()
            gui.stop_experiment()


def test_a_sensor_fault_nan_is_not_a_comm_error():
    for key in MODULES:
        backend = FakeBackend(measurements=[(float('nan'), 1.0e-3)])
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, backend, folder)
            gui.root.fire()
            assert gui.root.only_pending() == \
                (1000, gui._experiment_loop), key
            assert backend.reconnect_calls == 0, key
            assert gui.beeps == [], key
            gui.stop_experiment()


def test_a_plot_fault_is_logged_not_reconnected():
    for key in MODULES:
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, FakeBackend(), folder)

            def broken_relim():
                raise RuntimeError("plot fault")
            gui.ax_main.relim = broken_relim
            gui.root.fire()
            assert gui.root.only_pending() == \
                (1000, gui._experiment_loop), key
            assert any(m.startswith("GUI ERROR") for m in gui.logs), key
            assert gui.backend.reconnect_calls == 0, key
            # The row was on disk before the plot failed.
            with open(gui.data_filepath, newline='') as fh:
                assert len(list(csv.reader(fh))) == 2, key
            gui.stop_experiment()


# ----------------------------------------------- 4. no modal dialogs

def test_no_message_box_on_any_run_error_or_stop_path():
    for key, mod in MODULES.items():
        dialogs = NoDialogs()
        with swap(mod, "messagebox", dialogs), \
                tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(
                measurements=[(77.0, 1e-3), _glitch(), (77.1, 1e-3)],
                reconnects=[_glitch(), None])
            gui = _started(key, backend, folder)
            for _ in range(5):          # point, error, fail, ok, point
                gui.root.fire()
            gui._durable_write = _refuse_disk
            gui.root.fire()             # a point the disk refuses
            gui.stop_experiment("Application closed by user.")
            assert any("Reason: Application closed by user." in m
                       for m in gui.logs), key

            gui2 = _started(key, FakeBackend(), folder)
            gui2.root.fire()
            gui2.stop_experiment()      # user Stop, no reason
        assert dialogs.calls == [], (key, dialogs.calls)
        assert gui.beeps, key           # the error and the reasoned stop
        assert not any(m.startswith("GUI ERROR") for m in gui.logs), key


def test_the_old_runtime_error_dialog_and_finished_box_are_gone():
    for key, path in MODULE_PATHS.items():
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        assert 'showerror("Runtime Error"' not in source, key
        assert 'showinfo("Experiment Finished"' not in source, key
        assert "stop_experiment(\"Runtime Error\")" not in source, key


# ---------------------------------------------- 5. durable writes

def _refuse_disk(path, text):
    raise OSError(28, "No space left on device")


def test_write_or_buffer_keeps_order_logs_once_and_flushes_on_recovery():
    for key in MODULES:
        with tempfile.TemporaryDirectory() as folder:
            gui = _gui(key, FakeBackend(), folder)
            path = os.path.join(folder, "rows.csv")
            real_write = gui._durable_write
            disk = {"up": False}

            def flaky(p, text):
                if not disk["up"]:
                    raise OSError(5, "share unreachable")
                real_write(p, text)
            gui._durable_write = flaky

            for row in ("a\r\n", "b\r\n", "c\r\n"):
                gui._flush_pending_rows()
                gui._write_or_buffer(path, row)
            assert list(gui._pending_rows) == [
                (path, "a\r\n"), (path, "b\r\n"), (path, "c\r\n")], key
            assert sum(m.startswith("WRITE ERROR") for m in gui.logs) == 1, \
                (key, gui.logs)
            assert not os.path.exists(path), key

            disk["up"] = True
            gui._flush_pending_rows()
            gui._write_or_buffer(path, "d\r\n")
            with open(path, "rb") as fh:
                assert fh.read() == b"a\r\nb\r\nc\r\nd\r\n", key
            assert not gui._pending_rows, key
            assert sum("recovered" in m for m in gui.logs) == 1, key


def test_rows_are_fsynced():
    for key, mod in MODULES.items():
        synced = []

        class OsSpy:
            def __getattr__(self, name):
                return getattr(os, name)

            def fsync(self, fd):
                synced.append(fd)
                os.fsync(fd)

        with tempfile.TemporaryDirectory() as folder, \
                swap(mod, "os", OsSpy()):
            gui = _gui(key, FakeBackend(), folder)
            gui._durable_write(os.path.join(folder, "r.csv"), "1,2\r\n")
        assert len(synced) == 1, key


def test_a_disk_error_during_a_run_never_triggers_a_reconnect():
    for key in MODULES:
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as folder:
            gui = _started(key, backend, folder)
            gui._durable_write = _refuse_disk
            gui.root.fire()
            gui.root.fire()
            assert gui.root.only_pending() == \
                (1000, gui._experiment_loop), key
            assert backend.reconnect_calls == 0, key
            assert gui._comm_failures == 0, key
            assert gui.beeps == [], key
            assert len(gui._pending_rows) == 2, key
            assert sum(m.startswith("WRITE ERROR") for m in gui.logs) == 1, \
                key
            gui.stop_experiment()
            # The buffer could not be emptied, and the stop says so.
            assert any("could not be written" in m for m in gui.logs), key


# ------------------------------------------------- 6. keep-awake

def test_keep_awake_is_on_for_the_run_and_off_at_stop():
    for key, mod in MODULES.items():
        flags = []
        kernel32 = types.SimpleNamespace(
            SetThreadExecutionState=flags.append)
        fake_ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(kernel32=kernel32))
        cls = getattr(mod, GUI_CLASS[key])
        with swap(mod, "ctypes", fake_ctypes), \
                tempfile.TemporaryDirectory() as folder:
            gui = _started(key, FakeBackend(), folder,
                           stub_keep_awake=False)
            assert flags == [cls.ES_CONTINUOUS | cls.ES_SYSTEM_REQUIRED], \
                (key, flags)
            gui.root.fire()
            gui.stop_experiment("Application closed by user.")
        assert flags[-1] == cls.ES_CONTINUOUS, (key, flags)
        assert cls.ES_CONTINUOUS == 0x80000000, key
        assert cls.ES_SYSTEM_REQUIRED == 0x00000001, key


def test_keep_awake_is_a_silent_no_op_without_windll():
    for key, mod in MODULES.items():
        with swap(mod, "ctypes", types.SimpleNamespace()):
            gui = object.__new__(getattr(mod, GUI_CLASS[key]))
            gui._set_keep_awake(True)
            gui._set_keep_awake(False)


# ------------------------------------------ 7. the data file format

def _old_style_file(path, points):
    """What v1.0 wrote: a csv header, then one csv.writer row per point."""
    with open(path, 'w', newline='') as f:
        csv.writer(f).writerow(["Temperature (K)", "Voltage (V)",
                                "Resistance (Ohm)", "Elapsed Time (s)"])
    for temp, voltage, resistance, elapsed in points:
        with open(path, 'a', newline='') as f:
            csv.writer(f).writerow(
                [f"{temp:.4f}", f"{voltage:.6e}", f"{resistance:.6e}",
                 f"{elapsed:.2f}"])


def test_the_data_file_is_byte_identical_to_the_old_format():
    for key, mod in MODULES.items():
        samples = [(float('nan'), 1.234e-3), (77.35, -2.5e-6),
                   (300.125, 12.0)]
        backend = FakeBackend(measurements=list(samples))
        clock = VirtualClock()
        with swap(mod, "time", clock), \
                tempfile.TemporaryDirectory() as folder:
            gui = _started(key, backend, folder)
            origin = gui.start_time
            expected_points = []
            for (temp, voltage), step in zip(samples, (12.345, 0.9, 1.07)):
                clock.now += step
                gui.root.fire()
                elapsed = clock.now - origin
                expected_points.append(
                    (temp, voltage, voltage / (1.0 * 1e-3), elapsed))
            gui.stop_experiment()

            old_path = os.path.join(folder, "old_format.csv")
            _old_style_file(old_path, expected_points)
            with open(old_path, "rb") as fh:
                want = fh.read()
            with open(gui.data_filepath, "rb") as fh:
                got = fh.read()
        assert got == want, (key, got, want)
        assert b"\r\nnan,1.234000e-03,1.234000e+00,12.35\r\n" in got, \
            (key, got)
        assert math.isnan(gui.data_storage['temperature'][0]), key


# ------------------------------------------------------------- runner

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
    print(f"\n{failures} failure(s).")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
