"""Tests for novocontrol/Step_Frequency_Scan_AlphaAN_GUI.py (Alpha-AN + L350,
temperature-stepped frequency sweeps).

The GUI object is built with __new__ (no Tk window); the per-setpoint sweep
runs against FakeAlpha and writes into a temporary directory.
Runnable as plain Python as well as under pytest.
"""

import math
import os
import queue
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    FakeAlpha, connected_backend, load_port, run_common_backend_checks,
)

mod, PATH = load_port("Step_Frequency_Scan_AlphaAN_GUI.py")


class _Entry:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _gui(fake=None, freqs=(1000.0, 2000.0), save_dir=None):
    """A CombinedGUI with just what _run_frequency_sweep touches."""
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.lcr_backend, fake = connected_backend(mod, fake)
    gui.lcr_params = {"sample_name": "TestSample", "acv": 1.0, "mtm": 0.5,
                      "delay": 0.0, "wire_mode": "2",
                      "lcr_visa": "GPIB0::10::INSTR"}
    gui.save_dir = save_dir or tempfile.mkdtemp(prefix="pica_stepfscan_")
    gui.sweep_frequencies = list(freqs)
    gui.is_running = True
    gui._paused = False
    gui._skip_requested = False
    gui._worker_phase = None
    gui.gui_queue = queue.Queue()
    gui.cmd_queue = queue.Queue()
    gui._process_cmd_queue = lambda: False
    gui._log_temperature_point = lambda target, measuring_flag: None
    gui.recoveries = []

    def _recover(context, err):
        gui.recoveries.append((context, err))
        return True
    gui._comm_recover = _recover
    return gui, fake


def _rows(gui):
    names = [n for n in os.listdir(gui.save_dir) if n.endswith("_FreqScan.txt")]
    assert len(names) == 1, names
    with open(os.path.join(gui.save_dir, names[0]), encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return lines, [[float(v) for v in ln.split("\t")] for ln in lines[2:]]


def _logs(gui):
    out = []
    while not gui.gui_queue.empty():
        msg = gui.gui_queue.get_nowait()
        if msg["type"] == "log":
            out.append(msg["text"])
    return out


def test_common_alpha_backend_rules():
    run_common_backend_checks(mod, PATH)


def test_sweep_rows_hold_the_actual_frequency_and_keep_the_columns():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1001.5 2 1",
                               "ZRE=1E5 -2E6 2003.0 2 1"]})
    gui, _ = _gui(fake)
    done = gui._run_frequency_sweep(150.0, 0, 2)
    assert done == 2
    lines, rows = _rows(gui)
    assert lines[1] == mod.AlphaAN_Backend.DATA_HEADER
    assert lines[1].split("\t")[0] == "Frequency"
    assert lines[1].split("\t")[-1] == "T_set(K)"
    assert len(lines[1].split("\t")) == 20 and all(len(r) == 20 for r in rows)
    assert [r[0] for r in rows] == [1001.5, 2003.0]           # f_actual
    # Omega (column 17) is computed from f_actual, not the request.
    assert math.isclose(rows[0][16], 2 * math.pi * 1001.5, rel_tol=1e-6)
    assert all(r[-1] == 150.0 for r in rows)
    # GFR carries the requested frequency; MST written, ZRE? queried.
    assert "GFR=1.000000e+03" in fake.commands
    assert fake.kind_of("MST") == "write"


def test_header_and_comment_line_are_ascii_and_name_the_alpha_settings():
    gui, _ = _gui(freqs=(1000.0,))
    gui._run_frequency_sweep(80.0, 0, 1)
    lines, _ = _rows(gui)
    for line in lines[:2]:
        line.encode("ascii")
    assert "ACV: 1.0 Vrms" in lines[0] and "MTM: 0.5 s" in lines[0]
    assert "DC" not in lines[0]


def test_status_6_aborts_the_run_without_writing_the_row():
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 6 1"})
    gui, _ = _gui(fake)
    try:
        gui._run_frequency_sweep(100.0, 0, 2)
    except RuntimeError as exc:
        assert "status 6" in str(exc)
    else:
        raise AssertionError("status 6 must abort the run")
    _, rows = _rows(gui)
    assert rows == [] and gui.recoveries == []
    assert fake.commands.count("MST") == 1


def test_a_non_valid_status_discards_the_row_and_is_logged():
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 3 1"})
    gui, _ = _gui(fake, freqs=(1000.0,))
    assert gui._run_frequency_sweep(100.0, 0, 1) == 1    # still counted
    _, rows = _rows(gui)
    assert rows == []
    assert any("status 3" in t and "Point discarded" in t
               for t in _logs(gui))


def test_an_alpha_refusal_aborts_instead_of_reconnecting_forever():
    fake = FakeAlpha({"GFR=1.000000e+03": "IP"})
    gui, _ = _gui(fake, freqs=(1000.0,))
    try:
        gui._run_frequency_sweep(100.0, 0, 1)
    except RuntimeError as exc:
        assert "IP" in str(exc)
    else:
        raise AssertionError("a refused command must abort")
    assert gui.recoveries == []


def test_a_link_fault_recovers_and_retries_the_same_point():
    gui, fake = _gui(freqs=(1000.0,))
    real = gui.lcr_backend.measure_point
    calls = []

    def flaky(freq):
        calls.append(freq)
        if len(calls) == 1:
            raise TimeoutError("link down")
        return real(freq)
    gui.lcr_backend.measure_point = flaky
    gui._run_frequency_sweep(100.0, 0, 1)
    assert calls == [1000.0, 1000.0] and len(gui.recoveries) == 1
    _, rows = _rows(gui)
    assert len(rows) == 1


def test_reconnect_reopens_and_reconfigures_without_dc_bias():
    old = mod.RESET_RETRY_INTERVAL_S
    mod.RESET_RETRY_INTERVAL_S = 0.0
    try:
        backend, fake = connected_backend(mod)
        backend.params = {"lcr_visa": "GPIB0::10::INSTR", "acv": 1.0,
                          "mtm": 0.5, "wire_mode": "2"}
        backend.reconnect()
    finally:
        mod.RESET_RETRY_INTERVAL_S = old
    cmds = fake.commands
    assert cmds[:2] == ["ACV=0", "ZCONSPL=0"]          # parked first
    assert backend.rm.opened == ["GPIB0::10::INSTR"]
    for c in ("INTTYP?", "MODE=IMP", "FRS=2", "ACV=1", "MTM=0.5"):
        assert c in cmds, c
    assert cmds.count("*RST") == 1          # B1: init only, not on release
    assert not any(c.startswith(("DCV", "DCE")) for c in cmds)
    assert backend.instrument is fake


def _validate(**over):
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.lcr_backend, fake = connected_backend(mod)
    values = {"sample_name": "S", "acv": "1.0", "mtm": "0.5", "delay": "0"}
    values.update({k: v for k, v in over.items() if k in values})
    gui.lcr_entries = {k: _Entry(v) for k, v in values.items()}
    gui.wire_cb = _Entry(over.get("wire", "2"))
    gui.lcr_cb = _Entry("GPIB0::10::INSTR  ->  ALPHA Analyzer")
    gui.sweep_frequencies = over.get("freqs", [40.0, 1e3, 2e6])
    return gui, fake


def test_settings_are_validated_at_start_before_any_instrument_opens():
    gui, fake = _validate()
    p = gui._validate_lcr_params()
    assert p["lcr_visa"] == "GPIB0::10::INSTR" and p["wire_mode"] == "2"
    for bad in ({"acv": "3.5"}, {"acv": "0"}, {"mtm": "0"},
                {"wire": "5"}, {"delay": "-1"},
                {"acv": "3.0", "freqs": [1e3, 5e6]}):
        gui, fake = _validate(**bad)
        try:
            gui._validate_lcr_params()
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad}")
        assert fake.commands == [] and gui.lcr_backend.rm.opened == []


def _sweep_rows_for_fix(gui):
    return _rows(gui)


# --- pre-lab fixes (A1/A3/A5/A6/A8, B1-B3) ----------------------------------

def test_freq_tag_keeps_sub_hz_frequencies_apart():
    assert mod.freq_tag(0.1) == "0.1" and mod.freq_tag(0.5) == "0.5"
    assert mod.freq_tag(1000000) == "1000000"
    assert mod.freq_tag(1000.0) == "1000"


def test_point_timeout_scales_with_mtm_and_period():
    base = mod.SRQ_TIMEOUT_MEASURE_S
    assert mod.point_timeout_s(1e3, 0.5) == base
    assert mod.point_timeout_s(1e-3, 0.5) == 4.0 * 1000.0 + 60.0
    assert mod.point_timeout_s(1e3, 100.0) == 4.0 * 100.0 + 60.0
    backend, fake = connected_backend(mod)
    backend.params = {"mtm": 100.0}
    backend.measure_point(1e3)
    waits = [t for k, t in fake.traffic if k == "wait_for_srq"]
    assert waits == [int(460.0 * 1000)]


def test_non_valid_status_rows_are_discarded_but_counted():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1000 3 1",
                               "ZRE=1E5 -2E6 2000 2 1"]})
    gui, _ = _gui(fake, freqs=(1000.0, 2000.0))
    try:
        assert gui._run_frequency_sweep(150.0, 0, 2) == 2
        _, rows = _sweep_rows_for_fix(gui)
        assert [r[0] for r in rows] == [2000.0]
        msgs = []
        while not gui.gui_queue.empty():
            msgs.append(gui.gui_queue.get_nowait())
        assert [m["freq"] for m in msgs if m["type"] == "scan_point"] \
            == [2000.0]
        assert any("status 3" in m.get("text", "")
                   and "Point discarded" in m.get("text", "")
                   for m in msgs if m["type"] == "log")
        assert fake.commands.count("MST") == 2
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)


def test_a_wrong_idn_is_refused_without_shutdown_commands():
    fake = FakeAlpha({"*IDN?": "KEITHLEY INSTRUMENTS,MODEL 2400,1,C30"})
    backend, _ = connected_backend(mod, fake)
    backend.instrument = None
    try:
        backend.connect("GPIB0::10::INSTR")
    except ConnectionError as exc:
        assert "not a Novocontrol Alpha" in str(exc)
    else:
        raise AssertionError("a non-Alpha IDN must be refused")
    assert fake.commands == ["*IDN?"]
    assert fake.closed and backend.instrument is None


def test_reconnect_releases_without_rst_then_resets_with_rejoin_wait():
    backend, fake = connected_backend(mod)
    backend.params = {"lcr_visa": "GPIB0::10::INSTR", "acv": 1.0,
                      "mtm": 0.5, "wire_mode": "2"}
    saved = mod.RESET_RETRY_INTERVAL_S
    mod.RESET_RETRY_INTERVAL_S = 0.0
    try:
        backend.reconnect()
    finally:
        mod.RESET_RETRY_INTERVAL_S = saved
    cmds = fake.commands
    assert cmds[:3] == ["ACV=0", "ZCONSPL=0", "*IDN?"]
    assert cmds.count("*RST") == 1                  # init only
    assert cmds.index("*RST") > cmds.index("*IDN?")
    assert backend.instrument is fake


class _Clock:
    """Replaces the module's time so the backoff waits take no time."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        self.t += 10.0
        return self.t

    def sleep(self, s):
        pass


class _FakeLs:
    def reconnect(self):
        return "LSCI"


class _FakeLcr:
    def __init__(self, errors=(), on_success=None):
        self.errors = list(errors)
        self.calls = 0
        self.closed = 0
        self.on_success = on_success

    def reconnect(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        if self.on_success:
            self.on_success()

    def close_instrument(self):
        self.closed += 1


def _recover_gui(lcr):
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.ls_backend = _FakeLs()
    gui.lcr_backend = lcr
    gui.gui_queue = queue.Queue()
    gui.cmd_queue = queue.Queue()
    gui.is_running = True
    gui._paused = False
    gui._skip_requested = False
    return gui


def _with_clock(fn):
    saved = mod.time
    mod.time = _Clock()
    try:
        return fn()
    finally:
        mod.time = saved


def test_an_alpha_refusal_during_reconnect_ends_the_run():
    lcr = _FakeLcr(errors=[RuntimeError("Command 'MTM=0.5' failed: 'IP'")])
    gui = _recover_gui(lcr)
    try:
        _with_clock(lambda: gui._comm_recover("test", OSError("x")))
    except RuntimeError as exc:
        assert "IP" in str(exc)
    else:
        raise AssertionError("a refusal must not be retried")
    assert lcr.calls == 1


def test_a_bus_error_during_reconnect_is_retried():
    lcr = _FakeLcr(errors=[TimeoutError("bus"), OSError("visa")])
    gui = _recover_gui(lcr)
    assert _with_clock(lambda: gui._comm_recover("test", OSError("x")))
    assert lcr.calls == 3 and lcr.closed == 0


def test_stop_during_reconnect_closes_the_fresh_session():
    gui = _recover_gui(None)
    gui.lcr_backend = _FakeLcr(
        on_success=lambda: gui.cmd_queue.put(("stop",)))
    assert _with_clock(
        lambda: gui._comm_recover("test", OSError("x"))) is False
    assert gui.lcr_backend.closed == 1 and gui.is_running is False


def test_start_is_refused_while_the_previous_worker_is_alive():
    import threading
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    release = threading.Event()
    gui.worker_thread = threading.Thread(target=release.wait, daemon=True)
    gui.worker_thread.start()
    lines = []
    gui.log = lines.append
    try:
        gui.start_sequence()        # must not touch the (absent) widgets
    finally:
        release.set()
    assert any("still finishing" in ln for ln in lines)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
