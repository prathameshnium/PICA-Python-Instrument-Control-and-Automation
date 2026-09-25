"""The Cryo-con side of the two AC resistance modules.

    pica/keithley/k6221_k197a/RT_AC_K6221_K197A_CC34_T_Sensing_GUI.py
    pica/lockin/sr830/RT_AC_K6221_SR830_CC34_T_Sensing_GUI.py

Until 25 Sep 2026 these were the thinnest-covered Cryo-con modules in
PICA: one parser test between them, and the cross-module mnemonic audit
read ZERO commands from either, because both build their queries with
'%' formatting (fixed in test_cryocon_safety.py). This file covers the
rest of what a lab run leans on:

  * opening the session - retried on a timeout, refused at once for the
    wrong instrument, never a write, and (v1.1) paced;
  * the monitor - channel and units checked before a point is taken;
  * the worker - a sensor fault logs a NaN point and never ends the run;
    only a real temperature outside the window does. Since v1.1 a comm
    error does not end it either (reconnect and resume).

The readers themselves (retry, NaN, comm error) are covered with every
other module in test_cryocon_sensor_fault_tolerance.py, and the v1.1
hardening (pacing, reconnect, durable rows, no dialogs, keep-awake) in
test_ac_cc34_hardening.py.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import queue
import sys
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
    name = "ac_cc34_thermo_" + key
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO_ROOT, *parts))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    # Private copies: the settle and retry pauses are switched off here so
    # the suite does not sit through them.
    module.CRYOCON_OPEN_SETTLE_S = 0
    module.CRYOCON_RETRY_WAIT_S = 0
    module.CRYOCON_READ_RETRY_S = 0
    module.CRYOCON_MIN_GAP_S = 0
    return module


MODULES = {key: _load(key, parts) for key, parts in PATHS.items()}

CRYOCON_IDN = "Cryocon Model 34, Rev 3.03A, 204683, 3.03"
LAKESHORE_IDN = "LSCI,MODEL350,LSA2FKB/#######,1.7"


class Timeout(IOError):
    """Stands in for pyvisa's VI_ERROR_TMO."""


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
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def write(self, command):
        self.writes.append(command)

    def close(self):
        self.closed = True


class FakeRM:
    """Hands out one scripted session per open_resource call."""

    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.opened = []

    def open_resource(self, address):
        session = self.sessions.pop(0)
        self.opened.append(session)
        return session


class FakeVisa:
    def __init__(self, rm):
        self._rm = rm

    def ResourceManager(self):
        return self._rm


class patched_visa:
    def __init__(self, module, sessions):
        self.module = module
        self.rm = FakeRM(sessions)

    def __enter__(self):
        self.saved = self.module.pyvisa
        self.module.pyvisa = FakeVisa(self.rm)
        return self.rm

    def __exit__(self, *exc):
        self.module.pyvisa = self.saved
        return False


def _cryocon(**extra):
    replies = {"*IDN?": CRYOCON_IDN, "INPUT A:UNITS?": "K",
               "INPUT? A": "77.350"}
    replies.update(extra)
    return FakeSession(replies)


# ---------------------------------------------------------- opening a session

def test_a_healthy_cryocon_opens_at_the_operating_timeout_with_no_writes():
    for key, mod in MODULES.items():
        session = _cryocon()
        with patched_visa(mod, [session]):
            inst, idn = mod.open_cryocon_session("GPIB1::23::INSTR")
        # v1.1: the session comes back inside the pacing proxy.
        assert isinstance(inst, mod._PacedCryoconSession), key
        assert inst._session is session and idn == CRYOCON_IDN, key
        assert session.timeout == mod.CRYOCON_TIMEOUT_MS == 10000, key
        assert session.queries == ["*IDN?"], (key, session.queries)
        assert session.writes == [], key


def test_a_first_idn_that_times_out_is_retried_on_a_fresh_session():
    """28 Aug 2026: the first *IDN? after a bus scan died in viWrite."""
    for key, mod in MODULES.items():
        dead = FakeSession({"*IDN?": Timeout()})
        good = _cryocon()
        with patched_visa(mod, [dead, good]) as rm:
            inst, _idn = mod.open_cryocon_session("GPIB1::23::INSTR",
                                                  log=lambda m: None)
        assert inst._session is good, key
        assert dead.closed and not good.closed, key
        assert len(rm.opened) == 2, key


def test_a_silent_address_gives_up_after_the_configured_attempts():
    for key, mod in MODULES.items():
        sessions = [FakeSession({"*IDN?": Timeout()})
                    for _ in range(mod.CRYOCON_CONNECT_ATTEMPTS)]
        with patched_visa(mod, sessions) as rm:
            try:
                mod.open_cryocon_session("GPIB1::23::INSTR",
                                         log=lambda m: None)
            except ConnectionError as exc:
                assert "RIO-Port" in str(exc), str(exc)
            else:
                raise AssertionError(f"{key}: connected to silence")
        assert len(rm.opened) == mod.CRYOCON_CONNECT_ATTEMPTS, key
        assert all(s.closed for s in sessions), key


def test_the_wrong_instrument_is_refused_at_once_and_never_retried():
    """The Lakeshore sits on the Cryo-con's factory address. Retrying will
    not change what answers, so it must fail on the first attempt."""
    for key, mod in MODULES.items():
        lakeshore = FakeSession({"*IDN?": LAKESHORE_IDN})
        spare = _cryocon()
        with patched_visa(mod, [lakeshore, spare]) as rm:
            try:
                mod.open_cryocon_session("GPIB1::12::INSTR")
            except ConnectionError as exc:
                assert "not a Cryo-con" in str(exc), str(exc)
                assert "LSCI" in str(exc), str(exc)
            else:
                raise AssertionError(f"{key}: accepted a Lakeshore")
        assert len(rm.opened) == 1, key
        assert lakeshore.closed and lakeshore.writes == [], key


def test_an_empty_identification_is_refused():
    for key, mod in MODULES.items():
        with patched_visa(mod, [FakeSession({"*IDN?": "   "})]):
            try:
                mod.open_cryocon_session("GPIB1::23::INSTR")
            except ConnectionError:
                continue
        raise AssertionError(f"{key}: accepted an empty *IDN?")


def test_no_pyvisa_is_a_plain_connection_error():
    for key, mod in MODULES.items():
        saved = mod.pyvisa
        mod.pyvisa = None
        try:
            mod.open_cryocon_session("GPIB1::23::INSTR")
        except ConnectionError as exc:
            assert "PyVISA" in str(exc), str(exc)
        else:
            raise AssertionError(key)
        finally:
            mod.pyvisa = saved


# ------------------------------------------------------------- the monitor

def test_an_unknown_channel_is_refused_before_the_bus_is_touched():
    for key, mod in MODULES.items():
        for channel in ("E", "a", "", "AB"):
            with patched_visa(mod, []) as rm:
                try:
                    mod.Cryocon34Monitor("GPIB1::23::INSTR", channel)
                except ValueError:
                    assert rm.opened == [], (key, channel)
                    continue
            raise AssertionError(f"{key}: accepted channel {channel!r}")


def test_a_channel_not_reporting_kelvin_is_refused_by_name():
    """INPUT? answers in the channel's own units: a channel left in C
    would log every point wrong by 273.15 K."""
    for key, mod in MODULES.items():
        for units in ("C", "F", "S", ""):
            session = _cryocon(**{"INPUT B:UNITS?": units})
            with patched_visa(mod, [session]):
                try:
                    mod.Cryocon34Monitor("GPIB1::23::INSTR", "B")
                except ValueError as exc:
                    assert "channel B" in str(exc), str(exc)
                    assert session.writes == [], key
                    continue
            raise AssertionError(f"{key}: accepted units {units!r}")


def test_kelvin_is_accepted_however_it_is_padded():
    for key, mod in MODULES.items():
        for units in ("K", " k\r\n", "K\n"):
            session = _cryocon(**{"INPUT A:UNITS?": units})
            with patched_visa(mod, [session]):
                monitor = mod.Cryocon34Monitor("GPIB1::23::INSTR", "A")
            assert monitor.read_temperature() == 77.35, (key, units)
            assert session.writes == [], key


def test_shutdown_sends_nothing_and_leaves_the_cryocon_running():
    for key, mod in MODULES.items():
        session = _cryocon()
        with patched_visa(mod, [session]):
            monitor = mod.Cryocon34Monitor("GPIB1::23::INSTR", "A")
        before = list(session.queries)
        monitor.shutdown()
        assert session.writes == [], key
        assert session.queries == before, key


# -------------------------------------------------------------- the worker

class ScriptedThermometer:
    def __init__(self, temps):
        self.temps = list(temps)

    def read_temperature(self):
        value = self.temps.pop(0) if len(self.temps) > 1 else self.temps[0]
        if isinstance(value, BaseException):
            raise value
        return value


def _run_worker(key, temps, points=12, stop_low=1.0, stop_high=400.0):
    """Drive the real _run_worker with everything but the stop logic
    stubbed. Returns (('done' or 'failed', payload), temperatures logged)."""
    mod = MODULES[key]
    gui = object.__new__(mod.ACResistanceCC34SensingGUI)
    gui.params = {"temperature": {"interval": 0, "stop_low": stop_low,
                                  "stop_high": stop_high},
                  "frequency": 13.0, "current_peak": 1.4142e-6,
                  "settle": 0}
    gui.data_queue = queue.Queue()
    gui.io_lock = threading.Lock()
    gui.stop_requested = False
    gui.thermometer = ScriptedThermometer(temps)
    gui.source = None
    logged = []
    gui._prepare_instruments = lambda: "out.txt"
    gui._apply_drive = lambda f, i: None
    gui._auto_functions = lambda first: False
    gui._safe_shutdown = lambda: None
    gui._reconnect_instruments = lambda: None       # v1.1: always recovers
    gui._set_keep_awake = lambda enable: None
    gui._measure_point = lambda f, i: {"resistance": 1.0}
    gui._emit_point = lambda point, f, ip, ir, t: logged.append(t)
    budget = [points]

    def sleep(seconds):
        budget[0] -= 1
        return budget[0] > 0            # False = the operator pressed Stop
    gui._sleep_interruptibly = sleep
    gui._run_worker()
    final = None
    while not gui.data_queue.empty():
        kind, payload = gui.data_queue.get_nowait()
        if kind in ("done", "failed"):
            final = (kind, payload)
    return final, logged


def test_a_sensor_fault_never_ends_an_ac_run():
    """The docstring's claim, held to: NaN is outside no window."""
    nan = float("nan")
    for key in MODULES:
        final, logged = _run_worker(key, [nan], points=10)
        assert final == ("done", "Stopped."), (key, final)
        assert len(logged) >= 9, (key, len(logged))
        assert all(t != t for t in logged), key


def test_a_real_temperature_outside_the_window_still_ends_it():
    nan = float("nan")
    for key in MODULES:
        final, logged = _run_worker(key, [300.0, nan, nan, 401.0, 300.0])
        assert final[0] == "done" and "rose to 401.000 K" in final[1], \
            (key, final)
        assert len(logged) == 4, (key, logged)
        final, _ = _run_worker(key, [300.0, nan, 0.5])
        assert final[0] == "done" and "fell to 0.500 K" in final[1], \
            (key, final)


def test_the_window_edges_are_inclusive():
    for key in MODULES:
        final, _ = _run_worker(key, [400.0])
        assert "rose to 400.000 K" in final[1], (key, final)
        final, _ = _run_worker(key, [1.0])
        assert "fell to 1.000 K" in final[1], (key, final)


def test_a_comm_failure_no_longer_ends_the_run():
    """Until v1.1 a dead bus ended the run with ('failed', exc). Now the
    instruments are re-opened and logging resumes: only Stop (here, the
    sleep budget running out) ends it. The details of the reconnect are in
    test_ac_cc34_hardening.py."""
    for key in MODULES:
        final, logged = _run_worker(
            key, [300.0, Timeout("VI_ERROR_TMO"), 301.0])
        assert final == ("done", "Stopped."), (key, final)
        assert logged[0] == 300.0, (key, logged)
        assert len(logged) >= 2 and set(logged[1:]) == {301.0}, \
            (key, logged)


def _run_all():
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
