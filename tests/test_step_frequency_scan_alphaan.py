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
    FakeAlpha, FakeRM, connected_backend, load_port,
    run_common_backend_checks,
)

mod, PATH = load_port("Step_Frequency_Scan_AlphaAN_GUI.py")


class _Entry:
    def __init__(self, value):
        self.value = value

    def get(self, *_args):
        return self.value


def _gui(fake=None, freqs=(1000.0, 2000.0), save_dir=None):
    """A CombinedGUI with just what _run_frequency_sweep touches."""
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.lcr_backend, fake = connected_backend(mod, fake)
    gui.lcr_params = {"sample_name": "TestSample", "acv": 1.0, "mtm": 0.5,
                      "delay": 0.0, "wire_mode": "2",
                      "lcr_visa": "GPIB0::10::INSTR",
                      "geometry_mode": "diameter", "diameter_mm": 9.68,
                      "thickness_mm": 1.14, "save_pica": True,
                      "save_windeta": True,
                      "temp_controller": "Lakeshore 350"}
    gui.c0_farads = mod.c0_from_params(gui.lcr_params)
    gui.save_dir = save_dir or tempfile.mkdtemp(prefix="pica_stepfscan_")
    gui.lcr_params["save_dir"] = gui.save_dir   # snapshotted at Start
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
    """PICA file: its first '#' row, the header and the parsed data rows
    (the WinDETA metadata rows in between are checked separately)."""
    names = [n for n in os.listdir(gui.save_dir)
             if n.endswith("_FreqScan_pica_format.dat")]
    assert len(names) == 1, names
    with open(os.path.join(gui.save_dir, names[0]), encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    n_meta = sum(1 for ln in lines if ln.startswith("#"))
    lines = [lines[0]] + lines[n_meta:]
    return lines, [[float(v) for v in ln.split("\t")] for ln in lines[2:]]


def _windeta_raw(gui):
    names = [n for n in os.listdir(gui.save_dir)
             if n.endswith("_FreqScan_windeta_format.txt")]
    assert len(names) <= 1, names
    if not names:
        return None
    with open(os.path.join(gui.save_dir, names[0]), "rb") as fh:
        return fh.read()


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
    values = {"sample_name": "S", "acv": "1.0", "mtm": "0.5", "delay": "0",
              "geom_value": "1.0", "thickness_mm": "1.0"}
    values.update({k: v for k, v in over.items() if k in values})
    gui.lcr_entries = {k: _Entry(v) for k, v in values.items()}
    gui.wire_cb = _Entry(over.get("wire", "2"))
    gui.lcr_cb = _Entry("GPIB0::10::INSTR  ->  ALPHA Analyzer")
    gui.geom_mode_var = _Entry(over.get("geom", "area"))
    gui.fmt_pica_var = _Entry(over.get("pica", True))
    gui.fmt_windeta_var = _Entry(over.get("windeta", True))
    freqs = over.get("freqs", [40.0, 1e3, 2e6])
    gui.freq_cb = _Entry(over.get("preset", mod.CUSTOM_FREQ_LABEL))
    gui.custom_freq_text = _Entry(", ".join(str(f) for f in freqs) + "\n")
    gui.log = lambda msg: None
    return gui, fake


def test_settings_are_validated_at_start_before_any_instrument_opens():
    gui, fake = _validate()
    p = gui._validate_lcr_params()
    assert p["lcr_visa"] == "GPIB0::10::INSTR" and p["wire_mode"] == "2"
    assert p["area_mm2"] == 1.0 and "diameter_mm" not in p
    assert math.isclose(gui.c0_farads, mod.compute_c0_from_area(1.0, 1.0))
    for bad in ({"acv": "3.5"}, {"acv": "0"}, {"mtm": "0"},
                {"wire": "5"}, {"delay": "-1"},
                {"acv": "3.0", "freqs": [1e3, 5e6]},
                {"freqs": ["1kHz"]}, {"freqs": [1e3, 3e7]},
                {"thickness_mm": "0"}, {"geom": "diameter", "geom_value": "-2"},
                {"pica": False, "windeta": False}):
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


# --- output formats (PICA metadata + WinDETA file per step) -----------------

def test_windeta_file_per_step_matches_the_frequency_scan_layout():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1001.5 2 1",
                               "ZRE=1E5 -2E6 2003.0 2 1"]})
    gui, _ = _gui(fake)
    gui._run_frequency_sweep(150.0, 0, 2)
    raw = _windeta_raw(gui)
    assert b"\n" not in raw.replace(b"\r\n", b"")          # CRLF only
    lines = raw.decode("cp1252").split("\r\n")
    assert lines[0].startswith("TestSample 150.00 K, ")
    assert lines[1] == "Fixed value(s) :  AC Volt  [Vrms]=1.0000e+00"
    assert lines[2] == mod.WINDETA_HEADER and lines[-1] == ""
    rows = [[float(v) for v in ln.split("\t")] for ln in lines[3:-1]]
    assert len(rows) == 2 and all(len(r) == 11 for r in rows)
    want = mod.windeta_row(
        mod.impedance_to_dielectric(1e5, -2e6, 1001.5, gui.c0_farads),
        mod.loss_phase_cp_g(1e5, -2e6, 1001.5))
    assert all(math.isclose(a, b, rel_tol=1e-5)
               for a, b in zip(rows[0], want))


def test_the_dielectric_conversion_is_the_frequency_scan_one():
    fscan, _ = load_port("Frequency_Scan_AlphaAN_GUI.py")
    # Same 11 WinDETA columns as the Fscan (Tan(Delta) col 4, Phi last).
    assert mod.WINDETA_HEADER == fscan.WINDETA_HEADER
    for zr, zi, f, c0 in ((1e5, -2e6, 1e3, 1e-12), (3.0, 40.0, 1e6, 5e-13)):
        assert (mod.impedance_to_dielectric(zr, zi, f, c0)
                == fscan.impedance_to_dielectric(zr, zi, f, c0))
        assert (mod.loss_phase_cp_g(zr, zi, f)
                == fscan.loss_phase_cp_g(zr, zi, f))
        row = mod.impedance_to_dielectric(zr, zi, f, c0)
        extra = mod.loss_phase_cp_g(zr, zi, f)
        # Fscan file_rows' WinDETA half: Tan(Delta) after Eps'', Phi last.
        assert mod.windeta_row(row, extra) == \
            tuple(row[:3]) + (extra[0],) + tuple(row[3:]) + (extra[1],)
    assert mod.compute_c0_from_area(7.0, 0.5) == \
        fscan.compute_c0_from_area(7.0, 0.5)


def test_pica_file_carries_ascii_metadata_and_unticked_formats_are_skipped():
    gui, _ = _gui(freqs=(1000.0,))
    gui.lcr_params.update(save_windeta=False, geometry_mode="area",
                          area_mm2=50.0)
    gui._run_frequency_sweep(80.0, 0, 1)
    assert _windeta_raw(gui) is None
    names = os.listdir(gui.save_dir)
    with open(os.path.join(gui.save_dir, names[0]), encoding="utf-8") as fh:
        meta = [ln for ln in fh.read().splitlines() if ln.startswith("#")]
    assert all(ln.isascii() for ln in meta)
    joined = "\n".join(meta)
    for want in ("T_set = 80.0 K", "A = 50.0 mm^2", "Area electrode",
                 f"C0 = {gui.c0_farads:.5e} F", "ZREFMODE=-3", "ZSLCAL=1",
                 "ZLLCOR=1", "DRS=0 0", "Lakeshore 350", "1 pts"):
        assert want in joined, want

    gui, _ = _gui(freqs=(1000.0,))
    gui.lcr_params["save_pica"] = False
    gui._run_frequency_sweep(80.0, 0, 1)
    assert [n.endswith("_windeta_format.txt")
            for n in os.listdir(gui.save_dir)] == [True]


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


# --- an Alpha refusal inside the interleaved temperature log ends the run ---

def _temp_log_raising(gui, exc, clear_running):
    def _log(target, measuring_flag):
        if clear_running:
            gui.is_running = False
        raise exc
    gui._log_temperature_point = _log


def test_an_alpha_refusal_during_the_temp_log_reaches_the_worker():
    # _comm_recover re-raises the refusal with is_running still set: the
    # sweep must not swallow it (the setpoint loop would carry on with a
    # half-configured Alpha).
    gui, fake = _gui(freqs=(1000.0, 2000.0))
    _temp_log_raising(gui, RuntimeError("Command 'MTM=0.5' failed: 'IP'"),
                      clear_running=False)
    try:
        gui._run_frequency_sweep(100.0, 0, 2)
    except RuntimeError as exc:
        assert "IP" in str(exc)
    else:
        raise AssertionError("the refusal was swallowed by the sweep")
    assert fake.commands.count("MST") == 1          # no further point


def test_overtemp_and_stop_during_recovery_still_just_end_the_sweep():
    for msg in ("Hard overtemperature limit reached.",
                "Stopped during comm recovery."):
        gui, fake = _gui(freqs=(1000.0, 2000.0))
        _temp_log_raising(gui, RuntimeError(msg), clear_running=True)
        assert gui._run_frequency_sweep(100.0, 0, 2) == 0   # no raise
        assert fake.commands.count("MST") == 1
        assert gui.is_running is False


def test_a_soft_limit_still_reaches_the_worker():
    gui, _ = _gui(freqs=(1000.0,))
    _temp_log_raising(gui, mod.SoftLimitAbort("max"), clear_running=False)
    try:
        gui._run_frequency_sweep(100.0, 0, 1)
    except mod.SoftLimitAbort:
        pass
    else:
        raise AssertionError("SoftLimitAbort must propagate")


# --- Lakeshore 350 identity guard --------------------------------------------

def _ls_backend(idn):
    fake = FakeAlpha({"*IDN?": idn})       # any recording VISA stand-in
    ls = mod.Lakeshore_Backend.__new__(mod.Lakeshore_Backend)
    ls.lakeshore = None
    ls.last_visa = None
    ls.was_connected = False
    ls.rm = FakeRM(fake)
    return ls, fake


def test_lakeshore_connect_refuses_a_non_350_before_any_write():
    for idn in ("LSCI,MODEL340,123,1.0",
                "ALPHA Analyzer, Novocontrol Technologies, x",
                "KEITHLEY INSTRUMENTS,MODEL 2400,1,C30"):
        ls, fake = _ls_backend(idn)
        try:
            ls.connect("GPIB0::12::INSTR")
        except RuntimeError as exc:
            assert "not a Lake Shore Model 350" in str(exc)
        else:
            raise AssertionError(f"{idn} accepted as a 350")
        assert fake.commands == ["*IDN?"] and fake.closed
        assert ls.lakeshore is None
        ls.shutdown()                       # the worker's finally
        assert fake.commands == ["*IDN?"]   # still nothing written


def test_lakeshore_connect_accepts_a_350_and_clears_after_the_check():
    ls, fake = _ls_backend("LSCI,MODEL350,LSA1234,1.2")
    assert ls.connect("GPIB0::12::INSTR") == "LSCI,MODEL350,LSA1234,1.2"
    assert fake.commands == ["*IDN?", "*CLS"]
    assert ls.lakeshore is fake and not fake.closed


class _ClearingSession(FakeAlpha):
    """Recording VISA stand-in that also records a VISA device clear."""

    def clear(self):
        self.traffic.append(("clear", None))


def _reconnect_backend(idns):
    """A backend that was connected to a 350 at the right address and
    whose fresh session answers *IDN? with idns in turn."""
    ls, _ = _ls_backend("unused")
    fake = _ClearingSession({"*IDN?": list(idns)})
    ls.rm = FakeRM(fake)
    ls.last_visa = "GPIB0::12::INSTR"
    ls.was_connected = True
    return ls, fake


def test_a_stale_idn_on_reconnect_gets_a_clear_and_one_more_ask():
    ls, fake = _reconnect_backend(["+300.000", "LSCI,MODEL350,LSA1234,1.2"])
    assert ls.reconnect() == "LSCI,MODEL350,LSA1234,1.2"
    assert [k for k, _ in fake.traffic] == ["query", "clear", "query",
                                            "write"]
    assert fake.commands == ["*IDN?", "*IDN?", "*CLS"]
    assert ls.lakeshore is fake


def test_a_wrong_model_on_reconnect_is_still_refused_after_one_retry():
    ls, fake = _reconnect_backend(["+300.000", "LSCI,MODEL340,123456,061407"])
    try:
        ls.reconnect()
    except RuntimeError as exc:
        assert "not a Lake Shore Model 350" in str(exc)
    else:
        raise AssertionError("a non-350 must be refused")
    assert fake.commands == ["*IDN?", "*IDN?"] and fake.closed
    assert ls.lakeshore is None


def test_start_never_retries_or_clears_an_unknown_device():
    ls, fake = _ls_backend("+300.000")
    fake.clear = lambda: fake.traffic.append(("clear", None))
    try:
        ls.connect("GPIB0::12::INSTR")
    except RuntimeError:
        pass
    else:
        raise AssertionError("a non-IDN answer at Start must be refused")
    assert fake.traffic == [("query", "*IDN?")]


def test_shutdown_after_a_lost_session_reports_instead_of_crashing():
    ls, fake = _reconnect_backend(["LSCI,MODEL340,123456,061407", "LSCI,MODEL340,123456,061407"])
    try:
        ls.reconnect()
    except RuntimeError:
        pass
    assert ls.lakeshore is None
    assert ls.shutdown() is False           # no crash, heater-off not sent
    assert fake.commands == ["*IDN?", "*IDN?"]
    never, _ = _ls_backend("unused")
    assert never.shutdown() is True         # never connected: nothing lost


def test_the_worker_warns_when_heater_off_could_not_be_sent():
    import types
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.gui_queue = queue.Queue()
    gui.params, gui.lcr_params = {}, {}  # the worker fails at once
    gui.ls_backend = types.SimpleNamespace(shutdown=lambda: False)
    gui.lcr_backend = types.SimpleNamespace(close_instrument=lambda: None)
    gui._hardware_worker_loop()
    msgs = []
    while not gui.gui_queue.empty():
        msgs.append(gui.gui_queue.get_nowait())
    assert any(m["type"] == "log" and "could NOT be sent" in m["text"]
               for m in msgs)
    assert any(m["type"] == "beep" for m in msgs)
    assert msgs[-1]["type"] == "worker_done"


# --- frequency list (dropdown + custom list) ---------------------------------

# The list this module hard-coded before the dropdown existed (and the one
# the E4980A modules still sweep).
_E4980A_LIST = tuple(float(f) for f in (
    *range(40, 1000, 10), *range(1000, 10000, 100),
    *range(10000, 100000, 1000), *range(100000, 1000000, 10000),
    *range(1000000, 2000001, 100000)))


def test_default_frequency_list_is_the_old_e4980a_list():
    assert mod.DEFAULT_FREQ_PRESET == next(iter(mod.FREQ_PRESETS))
    assert mod.FREQ_PRESETS[mod.DEFAULT_FREQ_PRESET] == _E4980A_LIST
    assert len(_E4980A_LIST) == 377
    assert mod.CUSTOM_FREQ_LABEL not in mod.FREQ_PRESETS
    assert all(label.isascii() for label in mod.FREQ_PRESETS)


def test_presets_match_the_frequency_scan_module():
    fscan, _ = load_port("Frequency_Scan_AlphaAN_GUI.py")
    assert set(mod.FREQ_PRESETS) == set(fscan.FREQ_PRESETS)
    for label, freqs in fscan.FREQ_PRESETS.items():
        assert mod.FREQ_PRESETS[label] == freqs, label


def test_start_takes_the_chosen_preset():
    for label, freqs in mod.FREQ_PRESETS.items():
        gui, fake = _validate(preset=label)
        p = gui._validate_lcr_params()
        assert p["freq_preset"] == label
        assert tuple(gui.sweep_frequencies) == freqs
        assert fake.commands == []


def test_start_takes_a_custom_list_in_typed_order():
    gui, _ = _validate(freqs=[1e5, 20.0, 1e3])
    p = gui._validate_lcr_params()
    assert p["freq_preset"] == "Custom list (3 pts)"
    assert tuple(gui.sweep_frequencies) == (1e5, 20.0, 1e3)


def test_a_refused_custom_list_leaves_the_previous_list_alone():
    gui, _ = _validate(freqs=["1kHz"])
    gui.sweep_frequencies = mod.FREQ_PRESETS[mod.DEFAULT_FREQ_PRESET]
    try:
        gui._validate_lcr_params()
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a non-numeric frequency")
    assert gui.sweep_frequencies == _E4980A_LIST


# --- 27 Oct 2026 review fixes (S1 - S13) -------------------------------------

def _drain(gui):
    msgs = []
    while not gui.gui_queue.empty():
        msgs.append(gui.gui_queue.get_nowait())
    return msgs


def _queue_gui(*msgs):
    """A CombinedGUI whose _process_gui_queue can run headless."""
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.gui_queue = queue.Queue()
    for m in msgs:
        gui.gui_queue.put(m)
    gui.MAX_MSGS_PER_CYCLE = 100
    gui.worker_thread = None
    gui.log = lambda msg: None
    gui.statuses = []
    gui._update_status_ui = lambda text, color: gui.statuses.append(text)
    gui.set_ui_state = lambda running: None
    gui._set_keep_awake = lambda on: None
    gui._beep = lambda times=1: None
    gui._final_status = False
    gui._scan_info = None
    gui._freq_plot_dirty = False
    gui._pending_progress = None
    return gui


def test_s1_worker_done_keeps_the_final_banner():
    for text in ("SAFETY ABORT (MAX TEMP)", "COMPLETE", "ERROR",
                 "HEATER NOT TURNED OFF - check the Lakeshore"):
        gui = _queue_gui({"type": "status", "text": text, "color": "red",
                          "final": True}, {"type": "worker_done"})
        gui._process_gui_queue()
        assert gui.statuses == [text], gui.statuses
    # A transient banner (Stop pressed) still ends as IDLE.
    gui = _queue_gui({"type": "status", "text": "STOPPING", "color": "r"},
                     {"type": "worker_done"})
    gui._process_gui_queue()
    assert gui.statuses == ["STOPPING", "IDLE"]


def test_s1_the_worker_marks_its_error_banner_final():
    import types
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.gui_queue = queue.Queue()
    gui.params, gui.lcr_params = {}, {}  # the worker fails at once
    gui.ls_backend = types.SimpleNamespace(shutdown=lambda: True)
    gui.lcr_backend = types.SimpleNamespace(close_instrument=lambda: None)
    gui._hardware_worker_loop()
    status = [m for m in _drain(gui) if m["type"] == "status"]
    assert status[-1]["text"] == "ERROR" and status[-1]["final"] is True


def test_s7_a_cut_sweep_loses_its_measuring_title():
    gui = _queue_gui({"type": "worker_done"})
    gui._scan_info = ("measuring", 150.0)
    gui._process_gui_queue()
    assert gui._scan_info == ("partial", 150.0) and gui._freq_plot_dirty
    gui = _queue_gui({"type": "worker_done"})
    gui._scan_info = ("done", 150.0)              # a finished scan is held
    gui._process_gui_queue()
    assert gui._scan_info == ("done", 150.0)


def test_s2_s3_s8_bad_boxes_are_refused_at_start_and_named():
    for over, word in (({"sample_name": "BaTiO3-α"}, "Sample Name"),
                       ({"sample_name": "S→T"}, "Sample Name"),
                       ({"sample_name": "S1:heated"}, "Sample Name"),
                       ({"sample_name": "a/b"}, "Sample Name"),
                       ({"mtm": "0,5"}, "Integration"),
                       ({"acv": "abc"}, "AC Voltage"),
                       ({"delay": "nan"}, "Freq Delay"),
                       ({"thickness_mm": "nan"}, "Thickness"),
                       ({"geom_value": "inf"}, "Area")):
        gui, fake = _validate(**over)
        try:
            gui._validate_lcr_params()
        except ValueError as exc:
            assert word in str(exc), (over, str(exc))
        else:
            raise AssertionError(f"accepted {over}")
        assert fake.commands == [] and gui.lcr_backend.rm.opened == []


def _ls_validate(**over):
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    values = {"tol": "0.5", "soak": "120", "rate": "2.0", "delay": "1",
              "app_band": "3.0", "app_rate": "2.0", "drift": "0.10",
              "stab_timeout": "0", "max_temp": "320", "step_factor": "0.2",
              "first_factor": "0.5", "min_rate": "0.1", "max_rate": "5.0"}
    values.update(over)
    gui.entries = {k: {"entry": _Entry(v)} for k, v in values.items()}
    gui.ls_cb = _Entry("GPIB0::12::INSTR")
    gui.heater_range_var = _Entry("3 (High)")
    gui.ramp_mode_var = _Entry("adaptive")
    gui.var_auto_pid = _Entry(True)
    gui.approach_var = _Entry("")
    gui.ramp_table_var = _Entry("77:0.3, 100:0.5, else:5")
    return gui


def test_s8_lakeshore_boxes_are_named_and_nan_refused():
    assert _ls_validate()._validate_ls_params()["delay"] == 1.0
    for key, text, word in (("tol", "abc", "Tolerance"),
                            ("delay", "nan", "Poll Delay"),
                            ("max_temp", "inf", "Max Temp")):
        try:
            _ls_validate(**{key: text})._validate_ls_params()
        except ValueError as exc:
            assert word in str(exc) and text in str(exc), str(exc)
        else:
            raise AssertionError(f"accepted {key}={text}")


def test_s3_a_vanished_save_folder_is_refused_before_validation():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.worker_thread = None
    gui.listbox = type("LB", (), {"get": lambda self, *a: ["100"]})()
    gui.save_dir = os.path.join(tempfile.gettempdir(),
                                "pica_no_such_dir_s3", "gone")

    def _no_validation():
        raise AssertionError("validation must not run")
    gui._validate_ls_params = _no_validation
    warnings = []
    saved = mod.messagebox.showwarning
    mod.messagebox.showwarning = lambda *a, **k: warnings.append(a)
    try:
        gui.start_sequence()
    finally:
        mod.messagebox.showwarning = saved
    assert warnings and "no longer exists" in warnings[0][1]


class _WorkerLs:
    def __init__(self):
        self.ramps = []

    def connect(self, visa):
        return "LSCI,MODEL350"

    def configure_ramp(self, *args):
        self.ramps.append(args)

    def shutdown(self):
        return True


class _WorkerLcr:
    def __init__(self, on_init=None):
        self.connected = False
        self.on_init = on_init

    def connect(self, visa):
        self.connected = True
        return "ALPHA", 0

    def initialize_instrument(self, p):
        if self.on_init:
            self.on_init()

    def close_instrument(self):
        pass


def _worker_gui(save_dir):
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.gui_queue = queue.Queue()
    gui.cmd_queue = queue.Queue()
    gui.is_running = True
    gui._paused = False
    gui._skip_requested = False
    gui.params = {"ls_visa": "GPIB0::12::INSTR"}
    gui.lcr_params = {"lcr_visa": "GPIB0::10::INSTR", "sample_name": "S",
                      "save_dir": save_dir}
    gui.save_dir = save_dir
    gui.setpoint_floats = [100.0]
    gui.sweep_frequencies = [1000.0]
    gui.ls_backend = _WorkerLs()
    return gui


def test_s4_stop_during_connect_or_init_never_programs_the_heater():
    tmp = tempfile.mkdtemp(prefix="pica_stepfscan_s4_")
    try:
        # Stop pressed while the Lakeshore was connecting.
        gui = _worker_gui(tmp)
        gui.lcr_backend = _WorkerLcr()
        gui.cmd_queue.put(("stop",))
        gui._hardware_worker_loop()
        assert not gui.lcr_backend.connected
        assert gui.ls_backend.ramps == [] and os.listdir(tmp) == []
        assert _drain(gui)[-1]["type"] == "worker_done"
        # Stop pressed during the Alpha *RST / initialize waits.
        gui = _worker_gui(tmp)
        gui.lcr_backend = _WorkerLcr(
            on_init=lambda: gui.cmd_queue.put(("stop",)))
        gui._hardware_worker_loop()
        assert gui.lcr_backend.connected
        assert gui.ls_backend.ramps == [] and os.listdir(tmp) == []
        assert gui.is_running is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s5_discarded_and_skipped_points_move_the_progress_bar():
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 3 1"})
    gui, _ = _gui(fake, freqs=(1000.0,))
    try:
        assert gui._run_frequency_sweep(100.0, 4, 5) == 5
        assert [m["progress"] for m in _drain(gui)
                if m["type"] == "progress"] == [5]
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    gui, fake = _gui(freqs=(1000.0, 2000.0, 3000.0))
    gui._skip_requested = True
    try:
        assert gui._run_frequency_sweep(100.0, 3, 6) == 6
        assert [m["progress"] for m in _drain(gui)
                if m["type"] == "progress"] == [6]
        assert "MST" not in fake.commands
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    gui = _queue_gui({"type": "progress", "progress": 7})
    gui._process_gui_queue()
    assert gui._pending_progress == 7


def test_s6_resume_and_reconnect_restore_the_measuring_banner():
    gui, _ = _gui(freqs=(1000.0,))
    gui._paused = True
    calls = []

    def _resume():
        # call 1 is the loop-top check; Resume arrives inside the pause
        calls.append(1)
        if len(calls) >= 2:
            gui._paused = False
        return False
    gui._process_cmd_queue = _resume
    try:
        gui._run_frequency_sweep(150.0, 0, 1)
        texts = [m["text"] for m in _drain(gui) if m["type"] == "status"]
        assert texts[0].startswith("PAUSED")
        assert texts[-1] == "MEASURING AT 150.0 K"
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    # Reconnect: _comm_recover leaves a neutral banner, not COMM LOST.
    gui = _recover_gui(_FakeLcr())
    assert _with_clock(lambda: gui._comm_recover("test", OSError("x")))
    texts = [m["text"] for m in _drain(gui) if m["type"] == "status"]
    assert texts[0].startswith("COMM LOST")
    assert texts[-1] == "RECONNECTED - resuming"


def test_s9_stop_is_seen_during_a_long_freq_delay():
    import time as _time
    gui, fake = _gui(freqs=(1000.0, 2000.0))
    gui.lcr_params["delay"] = 30.0
    calls = []

    def _stop_on_second_call():
        calls.append(1)
        if len(calls) >= 2:
            gui.is_running = False
            return True
        return False
    gui._process_cmd_queue = _stop_on_second_call
    t0 = _time.time()
    try:
        assert gui._run_frequency_sweep(100.0, 0, 2) == 0
        assert _time.time() - t0 < 5.0
        assert "MST" not in fake.commands      # nothing measured
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    # Skip during the wait returns at once too.
    gui, _ = _gui()
    gui._skip_requested = True
    t0 = _time.time()
    assert gui._wait_responsive(30.0) is True
    assert _time.time() - t0 < 1.0
    shutil.rmtree(gui.save_dir, ignore_errors=True)


class _Widget:
    def __init__(self):
        self.state = None

    def config(self, **kw):
        if "state" in kw:
            self.state = kw["state"]


def test_s10_alpha_settings_are_locked_during_a_run():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    for name in ("start_button", "stop_button", "pause_button",
                 "skip_button", "approach_cb", "rb_adaptive", "rb_fixed",
                 "ramp_table_entry", "entry_start", "entry_end",
                 "entry_step", "entry_manual", "ls_cb", "lcr_cb", "freq_cb",
                 "custom_freq_text", "list_maker_button", "rb_geom_area",
                 "rb_geom_diam", "cb_fmt_pica", "cb_fmt_windeta",
                 "browse_button", "wire_cb"):
        setattr(gui, name, _Widget())
    gui.entries = {}
    gui.log_freq_entries = {}
    gui.lcr_entries = {k: _Widget() for k in (
        "sample_name", "acv", "mtm", "delay", "geom_value", "thickness_mm")}
    gui._on_ramp_mode_changed = lambda: None
    gui._on_freq_list_change = lambda: None
    locked = list(gui.lcr_entries.values()) + [
        gui.rb_geom_area, gui.rb_geom_diam, gui.cb_fmt_pica,
        gui.cb_fmt_windeta, gui.browse_button, gui.wire_cb]
    gui.set_ui_state(running=True)
    assert all(w.state == "disabled" for w in locked)
    gui.set_ui_state(running=False)
    assert all(w.state == "normal" for w in locked[:-1])
    assert gui.wire_cb.state == "readonly"


def test_s11_s13_wheel_scrolling_and_the_startup_line():
    with open(PATH, encoding="utf-8") as fh:
        src = fh.read()
    assert 'canvas.bind_all("<MouseWheel>", _wheel)' in src
    assert 'canvas.unbind_all("<MouseWheel>")' in src
    # The init line names the default list, not the old E4980A one.
    assert "40 Hz – 2 MHz sweep" not in src
    assert "Default sweep: " in src
    freqs = mod.build_log_frequency_list(
        *(float(v) for v in mod.LOG_FREQ_DEFAULTS))
    label = mod.log_list_label(freqs, float(mod.LOG_FREQ_DEFAULTS[2]))
    assert label.startswith(f"{len(freqs)} pts, 20 Hz - "), label


# --- cross-port of the L340 sibling's review fixes (L340A-*) -----------------

def test_l340a_03_live_updates_run_the_start_range_checks():
    gui = _ls_validate()
    gui.is_running = True
    gui.params = gui._validate_ls_params()
    gui.cmd_queue = queue.Queue()
    gui.log = lambda msg: None
    for w in gui.entries.values():
        w["lock"], w["locked"] = object(), True
    gui.entries["delay"]["locked"] = False
    errors = []
    saved = mod.messagebox.showerror
    mod.messagebox.showerror = lambda *a, **k: errors.append(a)
    try:
        for bad in ("-1", "0", "nan", "abc"):
            gui.entries["delay"]["entry"] = _Entry(bad)
            gui._send_live_updates()
            assert gui.cmd_queue.empty(), bad
            assert "Poll Delay" in errors[-1][1] or "Poll delay" in \
                errors[-1][1], errors[-1]
        gui.entries["delay"]["entry"] = _Entry("2")
        n = len(errors)
        gui._send_live_updates()
        assert len(errors) == n
        kind, updates = gui.cmd_queue.get_nowait()
        assert kind == "params" and updates["delay"] == 2.0
    finally:
        mod.messagebox.showerror = saved


def test_l340a_04_the_worker_writes_to_the_folder_chosen_at_start():
    gui, _ = _gui(freqs=(1000.0,))
    start_dir = gui.save_dir
    gui.save_dir = tempfile.mkdtemp(prefix="pica_stepfscan_moved_")
    try:
        gui._run_frequency_sweep(100.0, 0, 1)
        assert os.listdir(gui.save_dir) == []
        assert any(n.endswith("_pica_format.dat")
                   for n in os.listdir(start_dir))
    finally:
        shutil.rmtree(start_dir, ignore_errors=True)
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    with open(PATH, encoding="utf-8") as fh:
        src = fh.read()
    assert 'self.lcr_params["save_dir"] = self.save_dir' in src
    assert "self.save_dir," not in src   # no file path from the live label


class _Thread:
    def __init__(self, alive):
        self.alive = alive

    def is_alive(self):
        return self.alive


class _Root:
    def __init__(self):
        self.destroyed = False
        self.afters = []

    def destroy(self):
        self.destroyed = True

    def after(self, ms, fn):
        self.afters.append(fn)


def test_l340a_08_close_after_stop_waits_for_the_worker():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.is_running = False                 # Stop already handled
    gui.worker_thread = _Thread(True)      # ...but still shutting down
    gui.root = _Root()
    gui.log = lambda msg: None
    gui.lcr_params = {"mtm": 0.5, "delay": 30.0}
    gui.sweep_frequencies = [1000.0]
    asked = []
    saved = mod.messagebox.askyesno
    mod.messagebox.askyesno = lambda *a, **k: asked.append(a) or True
    try:
        gui._on_closing()
    finally:
        mod.messagebox.askyesno = saved
    assert asked == [] and not gui.root.destroyed and gui.root.afters
    assert gui._close_wait_s() >= (30.0 + mod.RESET_SETTLE_S
                                   + mod.RESET_READY_TIMEOUT_S)
    gui.worker_thread.alive = False
    gui.root.afters[-1]()
    assert gui.root.destroyed
    # No worker at all: close at once.
    gui.worker_thread = None
    gui.root = _Root()
    gui._on_closing()
    assert gui.root.destroyed


def test_l340a_10_sequence_complete_fills_the_progress_bar():
    gui = _queue_gui({"type": "sequence_complete"})
    gui.progress = {"value": 3, "maximum": 10}
    gui._pending_progress = 3
    gui._process_gui_queue()
    assert gui.progress["value"] == 10 and gui._pending_progress is None


def test_l340a_12_13_15_panel_frames_banner_and_startup_version():
    with open(PATH, encoding="utf-8") as fh:
        src = fh.read()
    assert 'style.configure("Panel.TFrame", background=self.CLR_FRAME_BG)' \
        in src
    for frame in ("gf", "ff", "fl", "bf", "lf"):
        assert f'{frame} = ttk.Frame(frame)' not in src, frame
    assert 'ttk.Frame(fl, style="Panel.TFrame")' in src
    assert 'style="Bar.TLabel"' in src and 'style="Bar.TRadiobutton"' in src
    start = src[src.index("    def start_sequence(self):"):
                src.index("    def stop_sequence(")]
    assert start.index('"CONNECTING') > start.index(
        "self.set_ui_state(running=True)")
    assert "GUI v{self.PROGRAM_VERSION} initialized" in src
    assert "GUI v1.0 initialized" not in src

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
