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


def test_ref_calibration_method_is_a_verbatim_copy():
    fscan, _ = load_port("Frequency_Scan_AlphaAN_GUI.py")
    import inspect
    assert (inspect.getsource(mod.AlphaAN_Backend.run_reference_calibration)
            == inspect.getsource(fscan.AlphaAN_Backend.run_reference_calibration))


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


def test_srq_timeout_aborts_the_calibration_with_mbk():
    backend, fake = connected_backend(mod, FakeAlpha(srq_raises=True))
    try:
        backend.run_load_short_step("SL_100")
    except TimeoutError:
        pass
    else:
        raise AssertionError("an SRQ timeout must raise")
    assert "MBK" in fake.commands


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
        def run_reference_calibration(self):
            pass
    g.backend = _Backend()
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
