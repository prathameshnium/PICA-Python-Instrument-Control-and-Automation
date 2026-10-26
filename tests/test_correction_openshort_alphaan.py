"""Tests for novocontrol/Correction_OpenShort_AlphaAN_GUI.py (Alpha-AN
calibration: REF, load-short, host-side open correction).

No hardware, VISA backend or Tk window. Runnable as plain Python as well as
under pytest.
"""

import ast
import math
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    FakeAlpha, connected_backend, load_port, run_common_backend_checks,
)

mod, PATH = load_port("Correction_OpenShort_AlphaAN_GUI.py")


class _NoSleep:
    """Skip the *RST settle sleeps while a test runs."""

    def __enter__(self):
        self._sleep = mod.time.sleep
        mod.time.sleep = lambda _s: None

    def __exit__(self, *exc):
        mod.time.sleep = self._sleep
        return False


class _TempHome:
    """Point ~ at a temporary folder, so ~/.pica/alphaan_cal.json is fake."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.dir
        return self.dir

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _calls_after(fake, start_cmd):
    """Traffic from the first occurrence of start_cmd onwards."""
    for n, (_kind, payload) in enumerate(fake.traffic):
        if payload == start_cmd:
            return fake.traffic[n:]
    raise AssertionError(f"{start_cmd} never sent: {fake.traffic}")


def test_common_alpha_backend_rules():
    run_common_backend_checks(mod, PATH)


def test_ref_calibration_is_the_frequency_scan_sequence():
    backend, fake = connected_backend(mod)
    with _NoSleep():
        backend.run_reference_calibration()
    assert fake.commands[:5] == ["*RST", "*IDN?", "MODE=IMP", "INTTYP?",
                                 "ZRUNCAL=REF_INIT"]
    assert fake.kind_of("ZRUNCAL=REF_INIT") == "query"     # immediate ack
    tail = [k for k, _ in _calls_after(fake, "ZRUNCAL=REF")]
    assert tail == ["write", "wait_for_srq", "read"]       # ack after SRQ
    assert not backend._task_may_be_running


def test_ref_calibration_sends_the_frequency_scan_bytes():
    # Was a verbatim-source check; the SRQ wait now differs (sliced,
    # cancellable), so the bytes on the bus are compared instead.
    fscan, _ = load_port("Frequency_Scan_AlphaAN_GUI.py")
    ours, fake_ours = connected_backend(mod)
    theirs, fake_theirs = connected_backend(fscan)
    with _NoSleep():                 # mod.time is the shared time module
        ours.run_reference_calibration()
        theirs.run_reference_calibration()
    assert fake_ours.commands == fake_theirs.commands


def test_ref_calibration_failure_ack_raises():
    backend, fake = connected_backend(mod, FakeAlpha({"__read__": "CA"}))
    with _NoSleep():
        try:
            backend.run_reference_calibration()
        except RuntimeError as exc:
            assert "CA" in str(exc)
        else:
            raise AssertionError("a non-OK REF ack must raise")


def test_ref_time_is_shared_with_the_frequency_scan_module():
    fscan, _ = load_port("Frequency_Scan_AlphaAN_GUI.py")
    with _TempHome() as home:
        stamp = mod.write_last_ref_cal()
        assert mod.cal_state_path().startswith(home)
        assert fscan.cal_state_path() == mod.cal_state_path()
        assert fscan.read_last_ref_cal() == stamp
        assert fscan.format_cal_age(stamp) == mod.format_cal_age(stamp)


def test_load_short_sequence():
    backend, fake = connected_backend(mod)
    with _NoSleep():
        backend.begin_load_short_calibration()
        backend.run_load_short_step("SL_SHORT")
        backend.run_load_short_step("SL_100")
    assert fake.commands[:5] == ["*RST", "*IDN?", "MODE=IMP", "INTTYP?",
                                 "ZRUNCAL=SL_INIT"]
    assert fake.kind_of("ZRUNCAL=SL_INIT") == "query"
    for step in ("ZRUNCAL=SL_SHORT", "ZRUNCAL=SL_100"):
        assert [k for k, _ in _calls_after(fake, step)][:3] == [
            "write", "wait_for_srq", "read"]
    assert fake.commands.index("ZRUNCAL=SL_SHORT") < fake.commands.index(
        "ZRUNCAL=SL_100")


def test_load_short_refuses_an_unknown_step_before_any_byte():
    backend, fake = connected_backend(mod)
    for bad in ("SL_OPEN", "ALL", "REF"):
        try:
            backend.run_load_short_step(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    assert fake.traffic == []


def test_load_short_failure_ack_raises():
    backend, _fake = connected_backend(mod, FakeAlpha({"__read__": "CR"}))
    try:
        backend.run_load_short_step("SL_SHORT")
    except RuntimeError as exc:
        assert "CR" in str(exc)
    else:
        raise AssertionError("a non-OK load-short ack must raise")


class _StbAlpha(FakeAlpha):
    """FakeAlpha whose SRQ never comes and whose status byte is chosen;
    `on_wait` runs at every SRQ wait slice."""

    def __init__(self, stb=0x00, on_wait=None, **kw):
        super().__init__(srq_raises=True, **kw)
        self.stb = stb
        self.on_wait = on_wait

    def read_stb(self):
        return self.stb

    def wait_for_srq(self, timeout_ms=25000):
        if self.on_wait:
            self.on_wait()
        return super().wait_for_srq(timeout_ms)


def _waits(fake):
    return [p for k, p in fake.traffic if k == "wait_for_srq"]


def test_srq_timeout_aborts_the_calibration_with_mbk():
    backend, fake = connected_backend(mod, _StbAlpha())
    try:
        backend.run_load_short_step("SL_100", timeout_s=0)
    except TimeoutError:
        pass
    else:
        raise AssertionError("an SRQ timeout must raise")
    assert "MBK" in fake.commands
    assert ("read", None) not in fake.traffic


def test_an_immediate_error_ack_is_read_at_once_not_after_an_hour():
    # ZRUNCAL refused at once: the answer sits in the buffer (MAV) and no
    # SRQ ever comes. One wait slice, then the ack is read and raised.
    fake = _StbAlpha(stb=mod.STB_MAV, answers={"__read__": "IP"})
    backend, _ = connected_backend(mod, fake)
    t0 = time.monotonic()
    try:
        backend.run_load_short_step("SL_SHORT")       # default 3600 s budget
    except RuntimeError as exc:
        assert "IP" in str(exc) and "SL_SHORT" in str(exc)
    else:
        raise AssertionError("an immediate error ack must raise")
    assert time.monotonic() - t0 < mod.CAL_WAIT_SLICE_S + 1.0
    assert len(_waits(fake)) == 1
    assert _waits(fake)[0] <= mod.CAL_WAIT_SLICE_S * 1000
    assert "MBK" not in fake.commands                 # nothing to abort
    assert not backend._task_may_be_running


def test_an_immediate_error_ack_on_ref_is_read_at_once():
    fake = _StbAlpha(stb=mod.STB_MAV, answers={"__read__": "EC"})
    backend, _ = connected_backend(mod, fake)
    with _NoSleep():
        try:
            backend.run_reference_calibration()
        except RuntimeError as exc:
            assert "EC" in str(exc)
        else:
            raise AssertionError("an immediate REF error ack must raise")
    assert len(_waits(fake)) == 1


def test_a_stale_task_bit_alone_does_not_end_the_wait():
    # Bit 0 may be left over from the immediately-acked *_INIT; without
    # RQS or MAV there is nothing to read, so the wait goes on (here: to
    # its timeout) instead of a read() that would hang.
    fake = _StbAlpha(stb=mod.STB_TASK_DONE)
    backend, _ = connected_backend(mod, fake)
    try:
        backend.run_load_short_step("SL_SHORT", timeout_s=0)
    except TimeoutError:
        pass
    else:
        raise AssertionError("bit 0 alone must not count as finished")
    assert ("read", None) not in fake.traffic


def test_cancel_aborts_the_task_with_mbk_and_reads_nothing():
    stop = threading.Event()
    fake = _StbAlpha(on_wait=stop.set)
    backend, _ = connected_backend(mod, fake)
    try:
        backend.run_load_short_step("SL_100", stop_event=stop)
    except mod.CalibrationCancelled as exc:
        assert "cancelled" in str(exc)
    else:
        raise AssertionError("Cancel must end the wait")
    assert fake.commands[-3:] == ["ZRUNCAL=SL_100", "MBK", "ZTSTAT?"]
    assert ("read", None) not in fake.traffic
    assert len(_waits(fake)) == 1                     # within one slice
    backend.safe_state()                              # the worker's park
    assert fake.commands[-2:] == ["ACV=0", "ZCONSPL=0"]


def test_cancel_works_on_the_ref_wait_too():
    stop = threading.Event()
    fake = _StbAlpha(on_wait=stop.set)
    backend, _ = connected_backend(mod, fake)
    with _NoSleep():
        try:
            backend.run_reference_calibration(stop_event=stop)
        except mod.CalibrationCancelled:
            pass
        else:
            raise AssertionError("Cancel must end the REF wait")
    assert "MBK" in fake.commands


class _ReadTimesOut(_StbAlpha):
    """Status byte says a response waits, but read() times out."""

    def read(self):
        self.traffic.append(("read", None))
        raise TimeoutError("VI_ERROR_TMO")


def test_a_failed_ack_read_still_aborts_the_task_before_parking():
    # Bit 4 meaning MAV is unconfirmed on the Alpha: if it ends the wait but
    # nothing comes, the task may still run, so the park must MBK first.
    fake = _ReadTimesOut(stb=mod.STB_MAV)
    backend, _ = connected_backend(mod, fake)
    try:
        backend.run_load_short_step("SL_SHORT")
    except TimeoutError:
        pass
    else:
        raise AssertionError("a timed-out ack read must raise")
    assert backend._task_may_be_running
    backend.safe_state()
    assert fake.commands[-3:] == ["MBK", "ACV=0", "ZCONSPL=0"]


def test_a_task_that_finished_as_cancel_was_pressed_is_read_not_aborted():
    # Cancel lands during the wait (a Cancel before the ZRUNCAL write now
    # stops before the task is sent at all).
    stop = threading.Event()
    fake = _StbAlpha(stb=mod.STB_RQS | mod.STB_TASK_DONE, on_wait=stop.set)
    backend, _ = connected_backend(mod, fake)
    backend.run_load_short_step("SL_100", stop_event=stop)   # no raise
    assert "MBK" not in fake.commands
    assert fake.traffic[-1] == ("read", None)
    assert not backend._task_may_be_running


def test_full_converter_calibration_is_never_sent():
    source = open(PATH, encoding="utf-8").read()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in (
                "write", "query", "_exec", "_exec_tolerant"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert not arg.value.startswith("ZRUNCAL=ALL"), arg.value


def test_open_settings_are_validated_before_any_byte():
    backend, fake = connected_backend(mod)
    params = {"frequencies": [5e6], "acv": 3.0, "mtm": 0.5, "wire_mode": "2"}
    try:
        backend.initialize_instrument(params)
    except ValueError as exc:
        assert "Vrms" in str(exc)
    else:
        raise AssertionError("3 Vrms at 5 MHz is over the ZG4 ceiling")
    assert fake.commands == []


def test_open_setup_matches_the_scan_modules():
    backend, fake = connected_backend(mod)
    params = {"frequencies": [1e3], "acv": 1.0, "mtm": 0.5, "wire_mode": "2"}
    with _NoSleep():
        backend.initialize_instrument(params)
    for cmd in ("ZREFMODE=-3", "ZLLCOR=1", "ZSLCAL=1", "FRS=2", "DRS=0 0",
                "ACV=1", "MTM=0.5"):
        assert fake.kind_of(cmd) == "query", cmd


def test_open_cell_uses_f_actual_and_keys_by_requested_frequency():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E9 -1.0E8 1001.0 2 1",
                               "ZRE=1E9 -1.0E8 2000.0 4 1"]})
    backend, _ = connected_backend(mod, fake)
    seen = []
    rows = mod.measure_open_cell(backend, [1000.0, 2000.0],
                                 on_point=lambda i, n, r: seen.append(i))
    assert seen == [1, 2]
    r1, r2 = rows
    assert (r1["f"], r1["f_actual"]) == (1000.0, 1001.0)
    y = 1.0 / complex(1e9, -1e8)
    assert math.isclose(r1["g"], y.real) and math.isclose(r1["b"], y.imag)
    assert math.isclose(r1["c"], y.imag / (2 * math.pi * 1001.0))
    assert r2["status"] == 4                     # kept, flagged in the file


def test_status_6_aborts_the_open_measurement():
    backend, _ = connected_backend(mod, FakeAlpha({"ZRE?": "ZRE=1 -1 1000 6 1"}))
    try:
        mod.measure_open_cell(backend, [1000.0, 2000.0])
    except RuntimeError as exc:
        assert "status 6" in str(exc)
    else:
        raise AssertionError("status 6 must abort")


def test_stop_event_ends_the_open_measurement_between_points():
    backend, fake = connected_backend(mod)
    stop = threading.Event()
    rows = mod.measure_open_cell(backend, [1e3, 2e3, 3e3],
                                 on_point=lambda i, n, r: stop.set(),
                                 stop_event=stop)
    assert len(rows) == 1 and fake.commands.count("MST") == 1


def test_open_file_round_trip_and_ascii():
    rows = [mod.open_row(1000.0, 1e9, -1e8, 1001.0, 2, 1),
            mod.open_row(2000.0, 5e8, -5e7, 2000.0, 4, 1)]
    meta = {"written": "2026-10-22T12:00:00", "idn": "ALPHA",
            "cell": mod.ascii_text("cell at 300 K, → open µ")}
    text = mod.format_open_file(rows, meta)
    text.encode("ascii")                         # cp1252-safe
    got_meta, got_rows = mod.parse_open_file(text)
    assert got_meta["format"] == mod.OPEN_FILE_FORMAT
    assert len(got_rows) == 2
    for a, b in zip(rows, got_rows):
        for k in a:
            assert math.isclose(a[k], b[k], rel_tol=1e-8), k
    try:
        mod.parse_open_file("# format: something else\n")
    except ValueError:
        pass
    else:
        raise AssertionError("a foreign file must be refused")


def test_apply_open_correction_recovers_the_sample():
    z_sample = complex(2e5, -3e6)
    z_open = complex(1e9, -2e8)
    z_mes = 1.0 / (1.0 / z_sample + 1.0 / z_open)   # open is in parallel
    zr, zi = mod.apply_open_correction(z_mes.real, z_mes.imag,
                                       z_open.real, z_open.imag)
    assert math.isclose(zr, z_sample.real, rel_tol=1e-9)
    assert math.isclose(zi, z_sample.imag, rel_tol=1e-9)


def test_recording_proxy_logs_and_forwards():
    entries = []
    fake = FakeAlpha()
    inst = mod.RecordingInstrument(fake, entries.append)
    inst.timeout = 1234
    assert fake.timeout == 1234
    assert inst.query("*IDN?").startswith("ALPHA")
    inst.write("MST")
    inst.wait_for_srq(1000)
    inst.read()
    kinds = [e[1] for e in entries]
    assert kinds == ["Q", "W", "SRQ", "SRQ", "R"]
    assert fake.commands == ["*IDN?", "MST"]


def test_wrong_idn_gets_no_shutdown_commands():
    fake = FakeAlpha({"*IDN?": "LSCI,MODEL340,123456,061407"})
    backend, _ = connected_backend(mod, fake)
    backend.instrument = None
    try:
        backend.connect("GPIB0::12::INSTR")
    except ConnectionError as e:
        assert "not a Novocontrol Alpha" in str(e)
    else:
        raise AssertionError("a non-Alpha IDN was accepted")
    assert fake.commands == ["*IDN?"]       # no ACV=0 / ZCONSPL=0 / *RST
    assert fake.closed and backend.instrument is None


def test_point_timeout_covers_slow_points():
    assert mod.point_timeout_s(1e3, 0.5) == mod.SRQ_TIMEOUT_MEASURE_S
    assert mod.point_timeout_s(0.01, 0.5) > 2 * 100.0
    assert mod.point_timeout_s(1e3, 100.0) > 2 * 100.0
    backend, fake = connected_backend(mod)
    backend.params = {"mtm": 100.0}
    backend.measure_point(1e3)
    waits = [p for k, p in fake.traffic if k == "wait_for_srq"]
    assert waits and waits[-1] >= 2 * 100.0 * 1000


class _GuiStub:
    """The CalibrationGUI methods under test, without a Tk window."""

    def __init__(self):
        self.gui = mod.CalibrationGUI.__new__(mod.CalibrationGUI)
        self.logs, self.advanced, self.after_calls = [], [], []
        g = self.gui
        g.busy = False
        g.sl_phase = 0
        g.sl_dirty = False
        g._close_requested = False
        g.log = self.logs.append
        g._advance = lambda step, outcome: self.advanced.append((step, outcome))
        g._set_headline = lambda *a, **k: None
        g._show_sl_phase = lambda: None
        stub = self

        class _Root:
            def bell(self):
                pass

            def after(self, ms, fn):
                stub.after_calls.append(ms)
                return "after#1"
        g.root = _Root()


def test_skip_is_refused_while_a_step_runs():
    s = _GuiStub()
    s.gui.busy = True
    s.gui._skip(s.gui.STEP_REF)
    assert s.advanced == [] and any("Busy" in m for m in s.logs)


def test_skip_after_the_short_part_is_recorded_as_incomplete():
    s = _GuiStub()
    s.gui.sl_phase = 1
    s.gui._skip(s.gui.STEP_SL)
    (step, outcome), = s.advanced
    assert step == s.gui.STEP_SL and outcome.startswith("INCOMPLETE")
    assert s.gui.sl_phase == 0
    s2 = _GuiStub()
    s2.gui._skip(s2.gui.STEP_SL)
    assert s2.advanced == [(s2.gui.STEP_SL, "skipped")]


def test_poll_chain_survives_a_handler_error():
    import queue as _queue
    s = _GuiStub()
    g = s.gui
    g.queue = _queue.Queue()
    g.queue.put(("log", "x"))
    g.queue.put(("idle", None))
    g.busy = True

    def boom(kind, payload):
        raise OSError("output drive gone")
    g._handle_queue_item = boom
    g._poll_queue()
    assert s.after_calls == [150]          # re-scheduled despite the error
    assert g.busy is False                 # idle still released the lock


def test_forced_close_during_ref_is_not_recorded_as_done():
    s = _GuiStub()
    g = s.gui
    import queue as _queue
    g.queue = _queue.Queue()
    g.progress_ref = None
    g._close_requested = True

    class _Backend:
        def run_reference_calibration(self, stop_event=None):
            pass
    g.backend = _Backend()
    g.stop_event = threading.Event()
    written = []
    saved = mod.write_last_ref_cal
    mod.write_last_ref_cal = lambda *a, **k: written.append(1)
    try:
        try:
            g._worker_ref()
        except RuntimeError:
            pass
        else:
            raise AssertionError("forced close was not reported")
    finally:
        mod.write_last_ref_cal = saved
    assert written == []


def _queued(g):
    out = []
    while not g.queue.empty():
        out.append(g.queue.get_nowait())
    return out


def _cancel_stub():
    import queue as _queue
    s = _GuiStub()
    g = s.gui
    g.queue = _queue.Queue()
    g.stop_event = threading.Event()
    g.progress_sl = g.progress_ref = None
    fake = _StbAlpha(on_wait=g.stop_event.set)
    g.backend, _ = connected_backend(mod, fake)
    return s, g, fake


def test_cancel_during_load_short_parks_and_reports_without_an_error():
    s, g, fake = _cancel_stub()
    g._worker_load_short(1)                 # SL_100 part; must not raise
    items = _queued(g)
    kinds = [k for k, _ in items]
    assert "error" not in kinds and "advance" not in kinds
    assert ("slphase", 0) in items          # load-short starts over
    assert any(k == "log" and "cancelled" in p for k, p in items)
    assert any(k == "headline" and p[1] is False for k, p in items)
    cmds = fake.commands
    assert cmds.index("MBK") < cmds.index("ACV=0") < cmds.index("ZCONSPL=0")


def test_cancel_during_ref_is_not_recorded_as_done():
    s, g, fake = _cancel_stub()
    written = []
    saved = mod.write_last_ref_cal
    mod.write_last_ref_cal = lambda *a, **k: written.append(1)
    try:
        with _NoSleep():
            g._worker_ref()                 # must not raise
    finally:
        mod.write_last_ref_cal = saved
    assert written == []
    kinds = [k for k, _ in _queued(g)]
    assert "advance" not in kinds and "refage" not in kinds
    assert "ACV=0" in fake.commands


def test_cancel_button_only_acts_while_a_step_runs():
    s, g, _ = _cancel_stub()
    g._cancel_cal()
    assert not g.stop_event.is_set()
    g.busy = True
    g._cancel_cal()
    assert g.stop_event.is_set() and any("Cancel" in m for m in s.logs)


# ---------------------------------------------------------------- review fixes

class _Btn:
    def __init__(self):
        self.state = None

    def config(self, **kw):
        self.state = kw.get("state", self.state)


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Worker:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class _CloseRoot:
    def __init__(self):
        self.after_calls, self.destroyed, self.cancelled = [], False, []

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))
        return f"after#{len(self.after_calls)}"

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def destroy(self):
        self.destroyed = True

    def bell(self):
        pass


class _CloseBackend:
    def __init__(self):
        self.closed = 0

    def close_instrument(self):
        self.closed += 1


def _close_stub(busy=True, alive=True):
    s = _GuiStub()
    g = s.gui
    g.root = _CloseRoot()
    g.backend = _CloseBackend()
    g.busy = busy
    g.worker = _Worker(alive)
    g.stop_event = threading.Event()
    g._close_warned = False
    g._release_pending = False
    g._poll_id = "poll#1"
    g._close_after_id = None
    g._close_deadline = 0.0
    return s, g


def test_forced_close_waits_for_the_worker_before_touching_the_analyzer():
    # F1: the Tk thread must not share the VISA session with a live worker.
    s, g = _close_stub()
    g._on_closing()                                  # first click: warning
    assert not g.stop_event.is_set() and not g._close_requested
    g._on_closing()                                  # second click: force
    assert g.stop_event.is_set() and g._close_requested
    assert g.backend.closed == 0 and not g.root.destroyed
    assert g.root.after_calls                        # polling the worker
    g._on_closing()                                  # third click: ignored
    g.worker.alive = False                           # worker parked and quit
    g._finish_close()
    assert g.backend.closed == 1 and g.root.destroyed
    assert "poll#1" in g.root.cancelled


def test_forced_close_gives_up_after_the_bound_without_sharing_the_session():
    s, g = _close_stub()
    g._close_warned = True
    g._on_closing()
    g._close_deadline = time.monotonic() - 1.0       # bound expired
    g._finish_close()
    assert g.backend.closed == 0 and g.root.destroyed


def test_idle_close_parks_at_once():
    s, g = _close_stub(busy=False, alive=False)
    g._on_closing()
    assert g.backend.closed == 1 and g.root.destroyed


def test_cancel_before_the_task_command_sends_no_zruncal():
    # F2: Cancel during the *RST/INTTYP preamble must not start the task.
    for call in ("ref", "sl_init", "sl_step"):
        stop = threading.Event()
        stop.set()
        backend, fake = connected_backend(mod)
        with _NoSleep():
            try:
                if call == "ref":
                    backend.run_reference_calibration(stop_event=stop)
                elif call == "sl_init":
                    backend.begin_load_short_calibration(stop_event=stop)
                else:
                    backend.run_load_short_step("SL_SHORT", stop_event=stop)
            except mod.CalibrationCancelled as exc:
                assert exc.before_start, call
            else:
                raise AssertionError(f"{call}: cancel was ignored")
        assert not any(c.startswith("ZRUNCAL") for c in fake.commands), call
        assert not backend._task_may_be_running


def test_cancel_between_ref_init_and_ref_does_not_start_ref():
    stop = threading.Event()
    fake = FakeAlpha()
    real_query = fake.query

    def query(cmd):
        out = real_query(cmd)
        if cmd == "ZRUNCAL=REF_INIT":
            stop.set()
        return out
    fake.query = query
    backend, _ = connected_backend(mod, fake)
    with _NoSleep():
        try:
            backend.run_reference_calibration(stop_event=stop)
        except mod.CalibrationCancelled:
            pass
        else:
            raise AssertionError("cancel after REF_INIT was ignored")
    assert "ZRUNCAL=REF" not in fake.commands


def test_load_short_cancelled_in_the_preamble_reports_data_unchanged():
    s, g, fake = _cancel_stub()
    g.stop_event.set()
    g.sl_dirty = False
    with _NoSleep():
        g._worker_load_short(0)                     # must not raise
    items = _queued(g)
    assert ("sldirty", True) not in items
    assert any(k == "log" and "unchanged" in p for k, p in items)
    assert not any(c.startswith("ZRUNCAL") for c in fake.commands)


def test_close_warning_alone_does_not_fail_a_good_ref():
    # F3: one click on X (then waiting) must not turn success into failure.
    s, g = _close_stub()
    import queue as _queue
    g.queue = _queue.Queue()
    g.progress_ref = None
    g._on_closing()                                  # warning only

    class _Backend:
        def run_reference_calibration(self, stop_event=None):
            pass
    g.backend = _Backend()
    written = []
    saved = mod.write_last_ref_cal
    mod.write_last_ref_cal = lambda *a, **k: written.append(1) or "2026-10-27T10:00:00"
    try:
        g._worker_ref()                              # must not raise
    finally:
        mod.write_last_ref_cal = saved
    assert written == [1]
    assert "advance" in [k for k, _ in _queued(g)]


def _sl_queue_apply(g, items):
    for k, p in items:
        if k == "sldirty":
            g.sl_dirty = p
        elif k == "slphase":
            g.sl_phase = p


def test_failed_short_part_is_skipped_as_incomplete():
    # F4: sl_phase is reset to 0 after a failure, but the data is dirty.
    import queue as _queue
    s = _GuiStub()
    g = s.gui
    g.queue = _queue.Queue()
    g.stop_event = threading.Event()
    g.progress_sl = None
    g.sl_dirty = False
    g.backend, _ = connected_backend(mod, FakeAlpha({"__read__": "CR"}))
    with _NoSleep():
        try:
            g._worker_load_short(0)
        except RuntimeError:
            pass
        else:
            raise AssertionError("a CR ack must fail the part")
    _sl_queue_apply(g, _queued(g))
    assert g.sl_phase == 0 and g.sl_dirty
    g._skip(g.STEP_SL)
    (step, outcome), = s.advanced
    assert outcome.startswith("INCOMPLETE")


def test_failed_100_ohm_part_is_skipped_as_incomplete():
    import queue as _queue
    s = _GuiStub()
    g = s.gui
    g.queue = _queue.Queue()
    g.stop_event = threading.Event()
    g.progress_sl = None
    g.sl_dirty = True                                # short part stored
    g.sl_phase = 1
    g.backend, _ = connected_backend(mod, FakeAlpha({"__read__": "CR"}))
    try:
        g._worker_load_short(1)
    except RuntimeError:
        pass
    _sl_queue_apply(g, _queued(g))
    assert g.sl_phase == 0
    g._skip(g.STEP_SL)
    assert s.advanced[0][1].startswith("INCOMPLETE")


def test_completed_load_short_clears_the_dirty_flag():
    import queue as _queue
    s = _GuiStub()
    g = s.gui
    g.queue = _queue.Queue()
    g.stop_event = threading.Event()
    g.progress_sl = None
    g.sl_dirty = True
    g.backend, _ = connected_backend(mod)
    g._worker_load_short(1)
    _sl_queue_apply(g, _queued(g))
    assert not g.sl_dirty


def _finish_stub(results):
    s = _GuiStub()
    g = s.gui
    heads = []
    g._set_headline = lambda text, ok=None: heads.append((text, ok))
    g._set_text = lambda w, t: None
    g._show_step = lambda i: None
    g._run_worker = lambda *a: None
    g.done_text = None
    g.results = dict(results)
    d = tempfile.mkdtemp()
    g.paths = {k: os.path.join(d, k + ".txt") for k in ("open", "log", "console")}
    with _TempHome():
        g._finish()
    return heads


def test_finish_keeps_warnings_red():
    # F5
    for outcome in ("INCOMPLETE: started, 100 Ohm load part not stored",
                    "50 points, median C_open = 1 pF (stopped)",
                    "50 points, median C_open = 1 pF, 3 not valid (Status column)"):
        (text, ok), = _finish_stub({"Load-short": outcome})
        assert ok is False and "warnings" in text, outcome
    (text, ok), = _finish_stub({"REF": "done, last REF calibration: just now",
                                "Load-short": "skipped"})
    assert ok is True


def test_open_error_keeps_the_points_already_measured():
    # F6
    import queue as _queue
    s = _GuiStub()
    g = s.gui
    g.queue = _queue.Queue()
    g.stop_event = threading.Event()
    g.progress_open = None
    g.open_rows = []
    g.idn = "ALPHA"
    d = tempfile.mkdtemp()
    g.paths = {"open": os.path.join(d, "x_open_Zopen.txt")}
    fake = FakeAlpha({"ZRE?": ["ZRE=1E9 -1.0E8 1000.0 2 1",
                               "ZRE=1E9 -1.0E8 2000.0 2 1",
                               "ZRE=1 -1 3000.0 6 1"]})
    g.backend, _ = connected_backend(mod, fake)
    params = {"frequencies": [1e3, 2e3, 3e3], "acv": 1.0, "mtm": 0.5,
              "wire_mode": "2"}
    with _NoSleep(), _TempHome():
        try:
            g._worker_open(params, "bench", "300")
        except RuntimeError as exc:
            assert "status 6" in str(exc)
        else:
            raise AssertionError("status 6 must still fail the step")
    meta, rows = mod.parse_open_file(open(g.paths["open"], encoding="utf-8").read())
    assert len(rows) == 2
    assert meta["complete"].startswith("no, error after 2 of 3")
    items = _queued(g)
    assert any(k == "progress" and "FAILED" in p[1] for k, p in items)
    assert "advance" not in [k for k, _ in items]
    assert fake.commands[-2:] == ["ACV=0", "ZCONSPL=0"]   # parked


def test_plan_and_browse_are_refused_while_busy():
    # F7
    s = _GuiStub()
    g = s.gui
    g.busy = True
    g.connected = True
    g.plan = [g.STEP_REF]
    g.do_ref_var = g.do_sl_var = g.do_open_var = _Var(True)
    g._go_plan()
    assert g.plan == [g.STEP_REF]
    saved = mod.filedialog.askdirectory
    mod.filedialog.askdirectory = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("folder dialog opened while busy"))
    try:
        g._browse()
    finally:
        mod.filedialog.askdirectory = saved
    assert sum("Busy" in m for m in s.logs) == 2


def test_idle_re_enables_the_ref_back_button():
    s = _GuiStub()
    g = s.gui
    for b in ("btn_open_stop", "btn_ref_cancel", "btn_sl_cancel", "btn_ref_back"):
        setattr(g, b, _Btn())
    g.btn_ref_back.state = 'disabled'
    g._release_pending = False
    g._close_warned = True
    g.busy = True
    g._handle_queue_item("idle", None)
    assert g.btn_ref_back.state == 'normal' and not g._close_warned
    source = open(PATH, encoding="utf-8").read()
    body = source.split("def _do_ref(self):", 1)[1].split("def _cancel_cal", 1)[0]
    assert "self.btn_ref_back.config(state='disabled')" in body


def _plan_stub(acv="1", mtm="0.5", temp="300"):
    s = _GuiStub()
    g = s.gui
    g.connected = True
    g.plan = []
    g.do_ref_var = g.do_sl_var = _Var(False)
    g.do_open_var = _Var(True)
    g.acv_var, g.mtm_var, g.temp_var = _Var(acv), _Var(mtm), _Var(temp)
    g.wire_var = _Var("2")
    g._selected_open_freqs = lambda: [1e3, 2e3]
    g._show_step = lambda i: None
    g._go_plan()
    return s, g


def test_input_errors_name_the_box_and_temperature_must_be_finite():
    # F8
    for kw, word in ((dict(acv="1,0"), "AC level"), (dict(mtm="abc"), "MTM"),
                     (dict(temp="abc"), "Temperature"),
                     (dict(temp="nan"), "Temperature"),
                     (dict(temp="inf"), "Temperature")):
        s, g = _plan_stub(**kw)
        assert g.plan == [], kw
        assert any(word in m for m in s.logs), (kw, s.logs)
    s, g = _plan_stub()
    assert g.plan == [g.STEP_OPEN]


def test_connect_with_a_vanished_or_unwritable_folder_is_reported():
    # F9
    s = _GuiStub()
    g = s.gui
    heads, started = [], []
    g._set_headline = lambda text, ok=None: heads.append(ok)
    g._run_worker = lambda *a: started.append(a)
    g._console_buffer = []
    g.paths = {}
    g.dest_folder = os.path.join(tempfile.mkdtemp(), "gone")
    g._do_connect()
    assert g.paths == {} and heads == [False] and started == []
    g.dest_folder = tempfile.mkdtemp()

    def refuse(path, text):
        raise PermissionError(13, "read-only", path)
    g._durable_write = refuse
    g._do_connect()
    assert g.paths == {} and heads == [False, False] and started == []
    assert any(g.dest_folder in m for m in s.logs)


class _CountingStb(_StbAlpha):
    def read_stb(self):
        self.traffic.append(("read_stb", None))
        return self.stb


def test_stop_aborts_an_open_point_within_one_slice():
    # F10: nothing is sent or polled between slices; Stop ends the point.
    stop = threading.Event()
    n = {"waits": 0}

    def on_wait():
        n["waits"] += 1
        if n["waits"] == 3:
            stop.set()
    fake = _CountingStb(on_wait=on_wait)
    backend, _ = connected_backend(mod, fake)
    backend.params = {"mtm": 0.5}
    try:
        backend.measure_point(1e-3, stop_event=stop)  # ~4000 s budget
    except mod.CalibrationCancelled:
        pass
    else:
        raise AssertionError("Stop must end the point")
    tail = [k for k, _ in _calls_after(fake, "MST")]
    assert tail[:4] == ["write", "wait_for_srq", "wait_for_srq", "wait_for_srq"]
    assert all(w <= mod.CAL_WAIT_SLICE_S * 1000 for w in _waits(fake))
    assert fake.commands[-2:] == ["MBK", "ZTSTAT?"]


def test_stop_during_a_point_drops_it_and_keeps_the_rest():
    stop = threading.Event()
    fake = FakeAlpha()
    backend, _ = connected_backend(mod, fake)
    calls = {"n": 0}
    real_wait = fake.wait_for_srq

    def wait(timeout_ms=25000):
        calls["n"] += 1
        if calls["n"] == 2:              # second point: Stop, no SRQ
            stop.set()
            raise TimeoutError("no SRQ")
        return real_wait(timeout_ms)
    fake.wait_for_srq = wait
    rows = mod.measure_open_cell(backend, [1e3, 2e3, 3e3], stop_event=stop)
    assert len(rows) == 1 and "MBK" in fake.commands
    assert fake.commands.count("MST") == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
