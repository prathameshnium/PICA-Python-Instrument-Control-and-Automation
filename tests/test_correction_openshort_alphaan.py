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
    stop = threading.Event()
    stop.set()
    fake = _StbAlpha(stb=mod.STB_RQS | mod.STB_TASK_DONE)
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
