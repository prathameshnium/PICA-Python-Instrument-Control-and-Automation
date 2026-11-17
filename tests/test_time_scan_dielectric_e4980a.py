"""
Tests for pica/keysight/Time_Scan_Dielectric_E4980A_GUI.py

No hardware: the E4980A is simulated as an ideal parallel R||C sample
(Rp = 1 MOhm, Cp = 100 pF), either at the backend level or at the raw
VISA/SCPI level. The GUI test opens a real Tk window and therefore only
runs with PICA_REAL_WINDOWS=1 (e.g. under xvfb-run), per tests/conftest.py.
"""

import math
import os
import queue
import random
import sys
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import Time_Scan_Dielectric_E4980A_GUI as ts  # noqa: E402

RP = 1.0e6       # ohm
CP = 100e-12     # F


def rc_parallel_RX(f, rp=RP, cp=CP):
    """Series R, X of an ideal parallel Rp||Cp at frequency f."""
    z = 1.0 / (1.0 / rp + 1j * 2 * math.pi * f * cp)
    return z.real, z.imag


def read_rows(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return lines[0].split("\t"), [ln.split("\t") for ln in lines[1:]]


# ---------------------------------------------------------------------------
# Frequency list
# ---------------------------------------------------------------------------

def test_default_list_is_20_points_100Hz_to_2kHz_step_100():
    assert len(ts.DEFAULT_FREQ_LIST) == 20
    assert ts.DEFAULT_FREQ_LIST[0] == 100.0
    assert ts.DEFAULT_FREQ_LIST[-1] == 2000.0
    assert np.allclose(np.diff(ts.DEFAULT_FREQ_LIST), 100.0)
    assert ts.parse_frequency_list(ts.DEFAULT_FREQS_TEXT) == ts.DEFAULT_FREQ_LIST


def test_custom_list_kept_exactly_order_and_repeats():
    text = "1000, 100\n500;100   2e3\t1.5E2"
    assert ts.parse_frequency_list(text) == [1000, 100, 500, 100, 2000, 150]


@pytest.mark.parametrize("bad", ["", "  ,, ", "100, abc", "10", "3e6",
                                 "nan", "inf", "100 Hz"])
def test_invalid_lists_rejected(bad):
    with pytest.raises(ValueError):
        ts.parse_frequency_list(bad)


def test_list_maker_output_pastes_straight_in():
    lm = pytest.importorskip("pica.utils.List_Maker_GUI")
    base = lm.base_list(100, 2000, "linear", step=100)
    for kw in ({}, {"integers": True}, {"scientific": True}):
        text = lm.format_values(base, **kw)
        assert np.allclose(ts.parse_frequency_list(text), base), (kw, text)
    looped = lm.apply_pattern(base, "loop", cycles=1)
    parsed = ts.parse_frequency_list(lm.format_values(looped))
    assert np.allclose(parsed, looped)          # repeats/order preserved


def test_freq_tag_never_merges_close_frequencies():
    assert ts.freq_tag(100) == "100"
    assert ts.freq_tag(2e6) == "2000000"
    assert ts.freq_tag(100.2) != ts.freq_tag(100.7)
    assert ts.freq_tag(150.5) == "150.5"


# ---------------------------------------------------------------------------
# Formulas: unchanged from the T-scan, and physically right
# ---------------------------------------------------------------------------

def _same(a, b):
    return all((math.isnan(x) and math.isnan(y)) or x == y
               for x, y in zip(a, b)) and len(a) == len(b)


def test_formulas_identical_to_passive_tscan():
    base = pytest.importorskip(
        "pica.keysight.Temprature_Scan_Passive_E4980A_GUI")
    ref = base.Integrated_CT_GUI.calculate_impedance_parameters
    rng = random.Random(1234)
    cases = [(100, 0.0, -1e5), (100, 1e3, 0.0), (1000, 0.0, 0.0),
             (2000, 5.0, 3.0), (20, -1.0, -2.0)]
    for _ in range(500):
        cases.append((rng.uniform(20, 2e6),
                      rng.uniform(-1e7, 1e7), rng.uniform(-1e7, 1e7)))
    for f, R, X in cases:
        assert _same(ts.calculate_impedance_parameters(f, R, X),
                     ref(None, f, R, X)), (f, R, X)


def test_formula_values_for_parallel_RC_sample():
    for f in ts.DEFAULT_FREQ_LIST:
        R, X = rc_parallel_RX(f)
        c = ts.calculate_impedance_parameters(f, R, X)
        w = 2 * math.pi * f
        assert c[ts.IDX_CP] == pytest.approx(CP, rel=1e-9)
        assert c[ts.IDX_G] == pytest.approx(1 / RP, rel=1e-9)
        assert c[13] == pytest.approx(RP, rel=1e-9)               # Rp
        assert c[ts.IDX_D] == pytest.approx(1 / (w * RP * CP), rel=1e-9)
        assert c[16] == pytest.approx(1 / (RP * w), rel=1e-9)     # Cp''
        assert c[15] == pytest.approx(w)                          # omega


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _rec(epoch, elapsed, sweep, point, f, status=0):
    return ts.make_record(epoch, elapsed, sweep, point, f, status)


def test_headers_and_one_file_per_distinct_frequency(tmp_path):
    freqs = [100.0, 200.0, 100.0, 150.5]
    w = ts.DataWriter(str(tmp_path), "S1", freqs)
    w.create_files("info\n")
    assert sorted(os.listdir(tmp_path)) == sorted([
        "S1-100Hz.txt", "S1-200Hz.txt", "S1-150.5Hz.txt",
        "S1_Master.txt", "S1_RunInfo.txt"])
    hdr, rows = read_rows(w.freq_paths[100.0])
    assert hdr[:3] == ["DateTime", "Epoch_s", "Elapsed_s"]
    assert hdr[3:] == ts.PARAM_HEADER.split("\t")
    assert len(hdr) == 21 and rows == []
    hdr, _ = read_rows(w.master_path)
    assert hdr[:7] == ["DateTime", "Epoch_s", "Elapsed_s", "Sweep", "Point",
                       "Frequency(Hz)", "Status"]
    assert len(hdr) == 25


def test_never_overwrites_existing_files(tmp_path):
    (tmp_path / "S1_Master.txt").write_text("OLD DATA")
    w = ts.DataWriter(str(tmp_path), "S1", [100.0])
    msgs = w.create_files()
    assert (tmp_path / "S1_Master.txt").read_text() == "OLD DATA"
    assert w.sample_tag.startswith("S1_") and msgs
    # A second run in the same second must not collide either
    w2 = ts.DataWriter(str(tmp_path), "S1", [100.0])
    w2.create_files()
    assert w2.master_path != w.master_path
    assert len(set(os.listdir(tmp_path))) == 7


def test_row_format_and_ms_precision(tmp_path):
    w = ts.DataWriter(str(tmp_path), "S", [100.0])
    w.create_files()
    epoch = 1794924202.4567
    R, X = rc_parallel_RX(100.0)
    calc = ts.calculate_impedance_parameters(100.0, R, X)
    w.write_point(_rec(epoch, 12.3456, 3, 1, 100.0, status=2), calc)
    _, rows = read_rows(w.freq_paths[100.0])
    assert len(rows) == 1 and len(rows[0]) == 21
    assert float(rows[0][1]) == pytest.approx(epoch, abs=1e-3)   # not 1e3 s
    assert rows[0][1] == "1794924202.457"
    assert rows[0][2] == "12.346"
    assert rows[0][0].endswith(".457")
    assert float(rows[0][3 + ts.IDX_CP]) == pytest.approx(CP, rel=1e-6)
    _, mrows = read_rows(w.master_path)
    assert mrows[0][3:7] == ["3", "1", "100", "2"]
    assert len(mrows[0]) == 25


def test_write_failure_buffers_in_order_and_recovers(tmp_path):
    w = ts.DataWriter(str(tmp_path), "S", [100.0, 200.0])
    w.create_files()
    calc = [1.0] * 18
    real = ts.DataWriter._durable_write
    fail = {"on": True}

    def flaky(path, text, mode="a"):
        if fail["on"]:
            raise OSError("network share gone")
        return real(path, text, mode)

    with patch.object(ts.DataWriter, "_durable_write", staticmethod(flaky)):
        m1 = w.write_point(_rec(1e9, 1.0, 1, 1, 100.0), calc)
        w.write_point(_rec(1e9 + 1, 2.0, 1, 2, 200.0), calc)
        assert any("WRITE ERROR" in m for m in m1)
        assert w.pending_count == 4 and w.rows_written == 0
        fail["on"] = False
        m3 = w.write_point(_rec(1e9 + 2, 3.0, 2, 1, 100.0), calc)
    assert any("recovered" in m for m in m3)
    assert w.pending_count == 0 and w.rows_written == 3
    _, mrows = read_rows(w.master_path)
    assert [r[2] for r in mrows] == ["1.000", "2.000", "3.000"]
    _, frows = read_rows(w.freq_paths[100.0])
    assert [r[2] for r in frows] == ["1.000", "3.000"]


# ---------------------------------------------------------------------------
# Acquisition loop with a simulated instrument
# ---------------------------------------------------------------------------

class FakeBackend:
    """Ideal R||C sample. fail_on: set of call numbers (1-based) that raise."""

    def __init__(self, fail_on=(), reconnect_failures=0, sleep=0.002):
        self.calls = 0
        self.fail_on = set(fail_on)
        self.reconnects = 0
        self.reconnect_failures = reconnect_failures
        self.sleep = sleep
        self.freqs = []
        self.return_times = []

    def measure_point(self, f, delay, stop_event=None):
        self.calls += 1
        time.sleep(self.sleep)
        if self.calls in self.fail_on:
            raise IOError("VI_ERROR_TMO (simulated GPIB timeout)")
        self.freqs.append(f)
        R, X = rc_parallel_RX(f)
        self.return_times.append(time.time())
        return R, X, 0

    def reconnect(self):
        self.reconnects += 1
        if self.reconnect_failures > 0:
            self.reconnect_failures -= 1
            raise ConnectionError("still down")

    closes = 0

    def close_instruments(self, final=False):
        self.closes += 1


def _run(tmp_path, freqs, backend, stop_after_points, backoffs=(0.01,),
         timeout=20.0, peek=None):
    q = queue.Queue()
    stop = threading.Event()
    w = ts.DataWriter(str(tmp_path), "RUN", freqs)
    w.create_files()
    runner = ts.TimeScanRunner(backend, w, freqs, 0.0, q, stop,
                               backoffs=backoffs)
    th = threading.Thread(target=runner.run, daemon=True)
    th.start()
    points, msgs = [], []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            item = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if isinstance(item, tuple) and item[0] == "POINT":
            points.append(item)
            if peek is not None:
                peek(w, item, points)
            if len(points) >= stop_after_points:
                break
        elif isinstance(item, str):
            msgs.append(item)
        elif isinstance(item, Exception):
            raise item
    t_stop = time.time()
    stop.set()
    th.join(5)
    stop_latency = time.time() - t_stop
    assert not th.is_alive()
    while not q.empty():
        item = q.get_nowait()
        if isinstance(item, tuple) and item[0] == "POINT":
            points.append(item)
        elif isinstance(item, str):
            msgs.append(item)
    return w, points, msgs, stop_latency


def test_runs_continuously_until_stop_saving_every_point(tmp_path):
    freqs = ts.DEFAULT_FREQ_LIST
    backend = FakeBackend()
    on_disk = []

    def peek(w, item, points):
        # The point is already on disk when the GUI hears about it (the
        # worker may already have written later points too)
        _, rows = read_rows(w.master_path)
        on_disk.append(len(rows) >= len(points))

    w, points, msgs, latency = _run(tmp_path, freqs, backend, 45, peek=peek)
    assert all(on_disk) and len(on_disk) == 45
    assert latency < 1.0

    _, mrows = read_rows(w.master_path)
    n = len(mrows)
    assert n == len(points) == backend.calls >= 45
    # three sweeps' worth, frequencies in list order, cycling
    assert [float(r[5]) for r in mrows] == [freqs[i % 20] for i in range(n)]
    assert [int(r[3]) for r in mrows] == [i // 20 + 1 for i in range(n)]
    assert [int(r[4]) for r in mrows] == [i % 20 + 1 for i in range(n)]
    # per-frequency files: one row per sweep that reached that frequency
    for k, f in enumerate(freqs):
        _, frows = read_rows(w.freq_paths[f])
        assert len(frows) == len([i for i in range(n) if i % 20 == k])
        for r in frows:
            assert float(r[3 + ts.IDX_CP]) == pytest.approx(CP, rel=1e-6)
            assert float(r[3 + ts.IDX_G]) == pytest.approx(1 / RP, rel=1e-6)


def test_timestamps_monotonic_and_taken_after_reading(tmp_path):
    backend = FakeBackend(sleep=0.003)
    w, points, _, _ = _run(tmp_path, [100.0, 200.0, 300.0], backend, 30)
    recs = [p[1] for p in points]
    epochs = [r["epoch"] for r in recs]
    elapsed = [r["elapsed"] for r in recs]
    assert all(b > a for a, b in zip(epochs, epochs[1:]))
    # >= not >: time.monotonic ticks every 15.6 ms on Windows, so two fast
    # simulated points can share one value
    assert all(b >= a for a, b in zip(elapsed, elapsed[1:]))
    for r, t_ret in zip(recs, backend.return_times):
        assert r["epoch"] >= t_ret                       # after the reading
        assert r["epoch"] - t_ret < 0.05
    span_epoch = epochs[-1] - epochs[0]
    span_elapsed = elapsed[-1] - elapsed[0]
    assert span_epoch == pytest.approx(span_elapsed, abs=0.05)
    _, mrows = read_rows(w.master_path)
    for row, r in zip(mrows, recs):
        assert row[0] == ts.format_datetime_ms(r["epoch"])


def test_comm_error_reconnects_and_repeats_same_point(tmp_path):
    freqs = [100.0, 200.0, 300.0, 400.0]
    backend = FakeBackend(fail_on={6, 7, 15})
    w, points, msgs, _ = _run(tmp_path, freqs, backend, 20)
    assert backend.reconnects == 3
    assert any("COMM ERROR" in m for m in msgs)
    assert any("Reconnected" in m for m in msgs)
    _, mrows = read_rows(w.master_path)
    # No point skipped or doubled: still a clean cycle through the list
    assert [float(r[5]) for r in mrows] == \
        [freqs[i % 4] for i in range(len(mrows))]


def test_reconnect_keeps_trying_until_instrument_returns(tmp_path):
    backend = FakeBackend(fail_on={3}, reconnect_failures=3)
    w, points, msgs, _ = _run(tmp_path, [100.0, 200.0], backend, 6)
    assert backend.reconnects == 4
    assert sum("Reconnect failed" in m for m in msgs) == 3


def test_stop_is_responsive_during_long_backoff(tmp_path):
    q = queue.Queue()
    stop = threading.Event()
    w = ts.DataWriter(str(tmp_path), "B", [100.0])
    w.create_files()
    backend = FakeBackend(fail_on=set(range(1, 1000)))
    runner = ts.TimeScanRunner(backend, w, [100.0], 0.0, q, stop,
                               backoffs=(60,))
    th = threading.Thread(target=runner.run, daemon=True)
    th.start()
    time.sleep(0.3)
    t0 = time.time()
    stop.set()
    th.join(5)
    assert not th.is_alive() and time.time() - t0 < 1.0
    assert backend.reconnects == 0


def test_calc_failure_writes_nans_and_run_continues(tmp_path):
    with patch.object(ts, "calculate_impedance_parameters",
                      side_effect=ZeroDivisionError("boom")):
        w, points, msgs, _ = _run(tmp_path, [100.0, 200.0], FakeBackend(), 4)
    assert any("Calc error" in m for m in msgs)
    _, mrows = read_rows(w.master_path)
    assert len(mrows) >= 4
    assert all(v.lower() == "nan" for v in mrows[0][7:])


def test_unexpected_bug_is_reported_and_data_kept(tmp_path):
    q = queue.Queue()
    stop = threading.Event()
    w = ts.DataWriter(str(tmp_path), "U", [100.0, 200.0])
    w.create_files()
    calls = {"n": 0}
    real = w.write_point

    def bad_write(rec, calc):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyError("bug")
        return real(rec, calc)

    w.write_point = bad_write
    runner = ts.TimeScanRunner(FakeBackend(), w, [100.0, 200.0], 0.0, q,
                               stop, backoffs=(0.01,))
    runner.run()                      # returns by itself
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert any(isinstance(i, KeyError) for i in items)
    assert any(isinstance(i, str) and "WORKER ERROR" in i for i in items)
    _, mrows = read_rows(w.master_path)
    assert len(mrows) == 2


# ---------------------------------------------------------------------------
# Real backend code against a simulated VISA / SCPI instrument
# ---------------------------------------------------------------------------

class FakeVisaError(Exception):
    pass


class FakeE4980A:
    """Minimal SCPI model of an E4980A with an R||C sample attached."""

    def __init__(self, log, fail_fetch_on=()):
        self.log = log
        self.freq = 1000.0
        self.bias = 0.0
        self.fetches = 0
        self.fail_fetch_on = set(fail_fetch_on)
        self.timeout = None
        self.read_termination = self.write_termination = None
        self.closed = False

    def write(self, cmd):
        self.log.append(cmd)
        if cmd.startswith(":FREQ "):
            self.freq = float(cmd.split()[1])
        elif cmd.startswith(":BIAS:VOLT "):
            self.bias = float(cmd.split()[1])

    def query(self, cmd):
        self.log.append(cmd)
        if cmd == "*IDN?":
            return "Keysight Technologies,E4980A,MY0000,A.06.15\n"
        if cmd == "*OPT?":
            return "0,0,0\n"
        if cmd == ":SYST:ERR?":
            return '+0,"No error"\n'
        if cmd == "*OPC?":
            return "1\n"
        if cmd == ":BIAS:VOLT?":
            return f"{self.bias}\n"
        return "\n"

    def query_ascii_values(self, cmd):
        self.log.append(cmd)
        self.fetches += 1
        if self.fetches in self.fail_fetch_on:
            raise FakeVisaError("VI_ERROR_TMO")
        R, X = rc_parallel_RX(self.freq)
        return [R, X, 0.0]

    def close(self):
        self.closed = True


class FakeRM:
    def __init__(self, fail_fetch_on=()):
        self.log = []
        self.sessions = []
        self.fail_fetch_on = fail_fetch_on

    def open_resource(self, addr):
        dev = FakeE4980A(self.log, self.fail_fetch_on if not self.sessions
                         else ())
        self.sessions.append(dev)
        return dev


PARAMS = {"lcr_visa": "GPIB0::17::INSTR", "ac_bias": 1.0, "dc_bias": 0.0,
          "aper": "LONG", "alc_enabled": True, "corr_enabled": True,
          "cable_len": "1", "delay": 0.0, "sample_name": "S"}


@pytest.fixture
def no_sleep():
    with patch.object(ts.time, "sleep", lambda s: None):
        yield


def test_backend_scpi_sequence_and_safe_close(no_sleep):
    rm = FakeRM()
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    b.initialize_instruments(PARAMS)
    assert "E4980A" in b.idn
    for cmd in (":FUNC:IMP RX", ":APER LONG", ":TRIG:SOUR BUS",
                ":CORR:OPEN:STAT ON", ":VOLT 1.0", ":BIAS:STAT OFF"):
        assert cmd in rm.log, cmd
    # no temperature instrument commands, ever
    assert not any(c.startswith(("KRDG", "RANGE", "SETP", "HTR"))
                   for c in rm.log)
    rm.log.clear()
    R, X, st = b.measure_point(500.0, 0.0)
    assert rm.log == [":FREQ 500.0", ":TRIG:IMM", "*OPC?", ":FETC?"]
    assert (R, X) == pytest.approx(rc_parallel_RX(500.0))
    b.close_instruments()
    assert ":BIAS:VOLT 0" in rm.log and ":BIAS:STAT OFF" in rm.log
    assert rm.sessions[0].closed and b.lcr.instrument is None


def test_full_stack_visa_timeout_reinitialises_and_resumes(tmp_path, no_sleep):
    rm = FakeRM(fail_fetch_on={4})
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    b.initialize_instruments(PARAMS)
    freqs = [100.0, 1000.0, 2000.0]
    q, stop = queue.Queue(), threading.Event()
    w = ts.DataWriter(str(tmp_path), "FS", freqs)
    w.create_files()
    runner = ts.TimeScanRunner(b, w, freqs, 0.0, q, stop, backoffs=(0.01,))
    th = threading.Thread(target=runner.run, daemon=True)
    th.start()
    deadline = time.time() + 10
    while w.rows_written < 9 and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    th.join(5)
    assert len(rm.sessions) == 2                 # reopened once
    assert rm.sessions[0].closed
    assert rm.log.count("*RST; *CLS") == 2       # full re-configuration
    _, mrows = read_rows(w.master_path)
    assert [float(r[5]) for r in mrows] == \
        [freqs[i % 3] for i in range(len(mrows))]
    for r in mrows:
        assert float(r[7 + ts.IDX_CP]) == pytest.approx(CP, rel=1e-9)


# ---------------------------------------------------------------------------
# GUI end-to-end (real Tk window; run with PICA_REAL_WINDOWS=1 + Xvfb)
# ---------------------------------------------------------------------------

def _pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


@pytest.mark.real_windows
def test_gui_start_runs_until_stop_and_writes_files(tmp_path):
    import tkinter as tk
    root = tk.Tk()
    try:
        app = ts.TimeScan_Dielectric_GUI(root)
        fake = FakeBackend(fail_on={8}, sleep=0.01)
        app.backend.initialize_instruments = lambda p: None
        app.backend.idn = "FAKE E4980A"
        app.backend.measure_point = fake.measure_point
        app.backend.reconnect = fake.reconnect
        closed = []
        app.backend.close_instruments = lambda: closed.append(1)

        assert "20 points per sweep" in app.freq_count_label.cget("text")
        app.freq_text.delete("1.0", "end")
        app.freq_text.insert("1.0", "1000, 100, 1000, 2000")
        app._update_freq_count()
        assert "4 points per sweep (3 distinct" in \
            app.freq_count_label.cget("text")

        app.entries["Sample Name"].insert(0, "GUI")
        app.entries["Delay"].delete(0, "end")
        app.entries["Delay"].insert(0, "0")
        app.lcr_cb.set("GPIB0::17::INSTR  ->  Keysight,E4980A")
        app.file_location_path = str(tmp_path)

        with patch.object(ts.TimeScanRunner.__init__, "__defaults__",
                          ((0.01,), time.time, time.monotonic, True)):
            app.start_measurement()
            assert app.is_running
            assert str(app.start_button.cget("state")) == "disabled"
            _pump(root, 1.5)
        assert app.points_done >= 8
        assert "Sweep" in app.status_label.cget("text")
        assert len(app.line_main.get_xdata()) >= 2

        with patch.object(ts.messagebox, "showinfo") as info:
            app.stop_measurement(from_user=True)
            deadline = time.time() + 10
            while app._stopping and time.time() < deadline:
                _pump(root, 0.05)
        assert not app._stopping and not app.is_running
        assert info.called and closed
        assert str(app.start_button.cget("state")) == "normal"
        assert not app.measurement_thread.is_alive()

        files = sorted(os.listdir(tmp_path))
        assert files == sorted(["GUI-1000Hz.txt", "GUI-100Hz.txt",
                                "GUI-2000Hz.txt", "GUI_Master.txt",
                                "GUI_RunInfo.txt"])
        _, mrows = read_rows(os.path.join(tmp_path, "GUI_Master.txt"))
        assert len(mrows) == app.points_done
        order = [1000.0, 100.0, 1000.0, 2000.0]
        assert [float(r[5]) for r in mrows] == \
            [order[i % 4] for i in range(len(mrows))]
        info_txt = open(os.path.join(tmp_path, "GUI_RunInfo.txt")).read()
        assert "1000, 100, 1000, 2000" in info_txt
    finally:
        root.destroy()


@pytest.mark.real_windows
def test_gui_rejects_bad_inputs_without_touching_instrument(tmp_path):
    import tkinter as tk
    root = tk.Tk()
    try:
        app = ts.TimeScan_Dielectric_GUI(root)
        touched = []
        app.backend.initialize_instruments = lambda p: touched.append(p)
        app.file_location_path = str(tmp_path)
        app.lcr_cb.set("GPIB0::17::INSTR")
        for name, delay in (("bad/name", "0.1"), ("ok", "-1"),
                            ("ok", "abc")):
            app.entries["Sample Name"].delete(0, "end")
            app.entries["Sample Name"].insert(0, name)
            app.entries["Delay"].delete(0, "end")
            app.entries["Delay"].insert(0, delay)
            with patch.object(ts.messagebox, "showerror") as err:
                app.start_measurement()
            assert err.called and not app.is_running
        assert touched == [] and os.listdir(tmp_path) == []
    finally:
        root.destroy()


# ===========================================================================
# Regression tests for the referee findings
# ===========================================================================

def test_parser_strict_ascii_and_bom():
    assert ts.parse_frequency_list("﻿100, 200") == [100.0, 200.0]
    for bad in ("1_000", "١٠٠", "0x64", "1,000.5.5"):
        with pytest.raises(ValueError):
            ts.parse_frequency_list(bad)


@pytest.mark.parametrize("sep", [",", ", ", ";", "\t", "\n", " "])
def test_every_list_maker_separator_parses(sep):
    lm = pytest.importorskip("pica.utils.List_Maker_GUI")
    base = lm.base_list(100, 2000, "log", n=20)
    for kw in ({"decimals": 4}, {"decimals": 12}, {"scientific": True},
               {"integers": True}):
        text = lm.format_values(base, separator=sep, **kw)
        parsed = ts.parse_frequency_list(text + "\n")
        assert len(parsed) == len(base)


def test_high_precision_frequencies_never_share_a_file(tmp_path):
    freqs = ts.parse_frequency_list("100.1234561, 100.1234564, 1999999.5")
    w = ts.DataWriter(str(tmp_path), "P", freqs)
    w.create_files()
    assert len(set(w.freq_paths.values())) == 3
    w.write_point(_rec(1e9, 1.0, 1, 3, freqs[2]), [1.0] * 18)
    _, mrows = read_rows(w.master_path)
    assert mrows[0][5] == "1999999.5"                # exact, not 2.000000E+06
    assert ts.freq_tag(freqs[0]) in os.path.basename(w.freq_paths[freqs[0]])


def test_datetime_and_epoch_columns_agree_at_second_boundary():
    r = ts.make_record(1794924202.9996, 0.0, 1, 1, 100.0, 0)
    assert r["epoch_text"] == "1794924203.000"
    assert r["datetime"].endswith(":23.000")


def test_fsync_failure_never_duplicates_a_row(tmp_path):
    w = ts.DataWriter(str(tmp_path), "F", [100.0])
    w.create_files()
    real = os.fsync
    state = {"fail": 1}

    def flaky(fd):
        if state["fail"]:
            state["fail"] -= 1
            raise OSError(5, "EIO on network share")
        return real(fd)

    with patch.object(ts.os, "fsync", flaky):
        for i in range(3):
            w.write_point(_rec(1e9 + i, float(i), 1, 1, 100.0), [1.0] * 18)
    for path in (w.freq_paths[100.0], w.master_path):
        _, rows = read_rows(path)
        assert [r[2] for r in rows] == ["0.000", "1.000", "2.000"], path


def test_every_point_is_fsynced(tmp_path):
    w = ts.DataWriter(str(tmp_path), "Y", [100.0, 200.0])
    w.create_files()
    with patch.object(ts.os, "fsync", wraps=os.fsync) as fs:
        w.write_point(_rec(1e9, 0.0, 1, 1, 100.0), [1.0] * 18)
    assert fs.call_count == 2          # frequency file + master file


def test_buffer_overflow_drops_whole_points_and_reports_live(tmp_path):
    w = ts.DataWriter(str(tmp_path), "D", [100.0])
    w.create_files()
    w.MAX_PENDING_POINTS = 3
    real = ts.DataWriter._durable_write
    fail = {"on": True}

    def flaky(path, text, mode="a"):
        if fail["on"]:
            raise OSError(28, "No space left on device")
        return real(path, text, mode)

    msgs = []
    with patch.object(ts.DataWriter, "_durable_write", staticmethod(flaky)):
        for i in range(6):
            msgs += w.write_point(_rec(1e9 + i, float(i), 1, 1, 100.0),
                                  [1.0] * 18)
        assert any("DATA LOSS" in m for m in msgs)   # reported during run
        fail["on"] = False
        msgs += w.flush_pending()
    assert w.dropped_points == 3
    _, frows = read_rows(w.freq_paths[100.0])
    _, mrows = read_rows(w.master_path)
    assert [r[2] for r in frows] == [r[2] for r in mrows] == \
        ["3.000", "4.000", "5.000"]                  # files stay consistent


def test_backlog_flush_is_batched(tmp_path):
    w = ts.DataWriter(str(tmp_path), "K", [100.0])
    w.create_files()
    w.FLUSH_BATCH_POINTS = 10
    real = ts.DataWriter._durable_write
    fail = {"on": True}

    def flaky(path, text, mode="a"):
        if fail["on"]:
            raise OSError(5, "EIO")
        return real(path, text, mode)

    with patch.object(ts.DataWriter, "_durable_write", staticmethod(flaky)):
        for i in range(50):
            w.write_point(_rec(1e9 + i, float(i), 1, 1, 100.0), [1.0] * 18)
        fail["on"] = False
        w.write_point(_rec(2e9, 99.0, 1, 1, 100.0), [1.0] * 18)
    assert w.rows_written == 10          # one bounded batch, run continues
    w.flush_pending()
    _, mrows = read_rows(w.master_path)
    assert len(mrows) == 51
    assert [float(r[2]) for r in mrows] == sorted(float(r[2]) for r in mrows)


class BiasE4980A(FakeE4980A):
    """Adds bias state, Option 001, slow / failing behaviour."""

    def __init__(self, log, state, slow_idn=0.0, scpi_error=False):
        super().__init__(log)
        self.state = state
        self.slow_idn = slow_idn
        self.scpi_error = scpi_error

    def write(self, cmd):
        super().write(cmd)
        if cmd.startswith(":BIAS:VOLT "):
            self.state["V"] = float(cmd.split()[1])
        elif cmd == ":BIAS:STAT ON":
            self.state["on"] = True
        elif cmd == ":BIAS:STAT OFF":
            self.state["on"] = False

    def query(self, cmd):
        if cmd == "*IDN?" and self.slow_idn:
            time.sleep(self.slow_idn)
        if cmd == "*OPT?":
            return "001\n"
        if cmd == ":BIAS:VOLT?":
            return f"{self.state['V']}\n"
        if cmd == ":SYST:ERR?" and self.scpi_error:
            self.scpi_error = False
            return '-222,"Data out of range"\n'
        return super().query(cmd)

    def close(self):
        super().close()
        self.state["open"] -= 1


class BiasRM:
    def __init__(self, **dev_kw):
        self.log, self.state = [], {"V": 0.0, "on": False, "open": 0}
        self.dev_kw = dev_kw
        self.sessions = []

    def open_resource(self, addr):
        kw = self.dev_kw.pop(0) if isinstance(self.dev_kw, list) and \
            self.dev_kw else (self.dev_kw if isinstance(self.dev_kw, dict)
                              else {})
        dev = BiasE4980A(self.log, self.state, **kw)
        self.state["open"] += 1
        self.sessions.append(dev)
        return dev


BIAS_PARAMS = dict(PARAMS, dc_bias=1.0)


def test_init_failure_after_bias_on_closes_and_zeroes_bias(no_sleep):
    rm = BiasRM(scpi_error=True)
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    with pytest.raises(RuntimeError):
        b.initialize_instruments(BIAS_PARAMS)
    assert rm.state["on"] is False and rm.state["V"] == 0.0
    assert rm.state["open"] == 0


def test_reinitialise_never_leaks_a_session(no_sleep):
    rm = BiasRM()
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    b.initialize_instruments(BIAS_PARAMS)
    b.initialize_instruments(BIAS_PARAMS)
    assert rm.state["open"] == 1
    b.close_instruments()
    assert rm.state["open"] == 0 and rm.state["on"] is False


def test_final_close_forbids_reopening(no_sleep):
    rm = BiasRM()
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    b.initialize_instruments(BIAS_PARAMS)
    b.close_instruments(final=True)
    with pytest.raises(ConnectionError):
        b.reconnect()
    assert rm.state["open"] == 0 and rm.state["on"] is False


def test_stop_during_slow_reconnect_leaves_bias_off(tmp_path):
    """Referee B critical #1: reconnect finishing AFTER Stop must not
    leave the DC bias on or a session open."""
    rm = BiasRM()
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    with patch.object(ts.time, "sleep", lambda s: None):
        b.initialize_instruments(BIAS_PARAMS)
    assert rm.state["on"] and rm.state["V"] == 1.0
    rm.sessions[0].fail_fetch_on = {2}
    rm.dev_kw = {"slow_idn": 1.0}          # reconnect takes > 1 s
    q, stop = queue.Queue(), threading.Event()
    w = ts.DataWriter(str(tmp_path), "R", [100.0])
    w.create_files()
    runner = ts.TimeScanRunner(b, w, [100.0], 0.0, q, stop, backoffs=(0.01,))
    th = threading.Thread(target=runner.run, daemon=True)
    th.start()
    deadline = time.time() + 5
    while len(rm.sessions) < 2 and time.time() < deadline:
        time.sleep(0.005)
    stop.set()                             # Stop while *IDN? is hanging
    th.join(10)
    assert not th.is_alive()
    assert rm.state["on"] is False and rm.state["V"] == 0.0
    assert rm.state["open"] == 0


def test_settle_delay_is_interruptible(tmp_path, no_sleep):
    rm = FakeRM()
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    b.initialize_instruments(PARAMS)
    q, stop = queue.Queue(), threading.Event()
    w = ts.DataWriter(str(tmp_path), "I", [100.0])
    w.create_files()
    runner = ts.TimeScanRunner(b, w, [100.0], 60.0, q, stop)
    th = threading.Thread(target=runner.run, daemon=True)
    th.start()
    time.sleep(0.2)
    t0 = time.time()
    stop.set()
    th.join(5)
    assert not th.is_alive() and time.time() - t0 < 1.0
    assert rm.sessions[0].closed                   # worker closed the LCR


def test_timestamp_taken_after_fetc_full_stack(tmp_path, no_sleep):
    rm = FakeRM()
    b = ts.TimeScan_Backend()
    b.lcr.rm = rm
    b.initialize_instruments(PARAMS)
    fetch_times = []
    dev = rm.sessions[0]
    orig = dev.query_ascii_values

    def timed(cmd):
        v = orig(cmd)
        fetch_times.append(time.time())
        return v

    dev.query_ascii_values = timed
    w, points, _, _ = _run(tmp_path, [100.0, 200.0], b, 6)
    for (_, rec, _), t_fetch in zip(points, fetch_times):
        assert rec["epoch"] >= t_fetch


@pytest.mark.real_windows
def test_gui_refuses_start_while_old_worker_alive_and_uses_fresh_event(
        tmp_path):
    import tkinter as tk
    root = tk.Tk()
    try:
        app = ts.TimeScan_Dielectric_GUI(root)
        assert app.entries["Delay"].get() == "0.1"     # same as T-scan
        assert app.var_plot_thin.get() is True
        hold = threading.Event()
        app.measurement_thread = threading.Thread(target=hold.wait,
                                                  daemon=True)
        app.measurement_thread.start()
        old_event = app.stop_event
        init_calls = []
        app.backend.initialize_instruments = lambda p: init_calls.append(p)
        app.entries["Sample Name"].insert(0, "Z")
        app.lcr_cb.set("GPIB0::17::INSTR")
        app.file_location_path = str(tmp_path)
        app.start_measurement()
        assert init_calls == [] and not app.is_running   # refused
        hold.set()
        app.measurement_thread.join(2)

        fake = FakeBackend(sleep=0.01)
        app.backend.measure_point = fake.measure_point
        app.backend.close_instruments = lambda final=False: None
        app.start_measurement()
        assert app.is_running and app.stop_event is not old_event
        _pump(root, 0.3)
        with patch.object(ts.messagebox, "showinfo"):
            app.stop_measurement(from_user=True)
            deadline = time.time() + 10
            while app._stopping and time.time() < deadline:
                _pump(root, 0.05)
        assert not app.measurement_thread.is_alive()
    finally:
        root.destroy()


@pytest.mark.real_windows
def test_gui_failed_start_closes_instrument(tmp_path):
    import tkinter as tk
    root = tk.Tk()
    try:
        app = ts.TimeScan_Dielectric_GUI(root)
        closed = []
        app.backend.initialize_instruments = lambda p: None
        app.backend.close_instruments = lambda final=False: closed.append(1)
        app.entries["Sample Name"].insert(0, "X")
        app.lcr_cb.set("GPIB0::17::INSTR")
        app.file_location_path = str(tmp_path / "does_not_exist")
        with patch.object(ts.messagebox, "showerror") as err:
            app.start_measurement()
        assert err.called and closed and not app.is_running
        assert str(app.start_button.cget("state")) == "normal"
    finally:
        root.destroy()
