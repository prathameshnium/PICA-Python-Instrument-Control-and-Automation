"""Tests for novocontrol/Step_Frequency_Scan_L340_AlphaAN_GUI.py (Alpha-AN + L340).

The GUI object is built with __new__ (no Tk window) and only the attributes
the tested worker paths read are set. Runnable as plain Python as well as
under pytest.
"""

import math
import os
import queue
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    FakeAlpha, FakeRM, connected_backend, load_port,
    run_common_backend_checks,
)

mod, PATH = load_port("Step_Frequency_Scan_L340_AlphaAN_GUI.py")


# --- module-specific stand-ins ----------------------------------------------

class _Entry:
    """Stands in for a ttk.Entry / Combobox: only .get() is read."""

    def __init__(self, value):
        self.value = value

    def get(self, *_args):
        return self.value


class _Combo:
    """Records what the VISA scan puts into a combobox."""

    def __init__(self):
        self.items = {}
        self.value = ""

    def __setitem__(self, key, value):
        self.items[key] = value

    def set(self, value):
        self.value = value


class _FakeL340:
    """Model 340 backend stand-in: records whether it was ever opened."""

    def __init__(self):
        self.calls = []
        self.rm = None

    def connect(self, address):
        self.calls.append(("connect", address))
        return "LSCI,MODEL340,0,0"

    def shutdown(self):
        self.calls.append(("shutdown",))


def _lcr_params(**over):
    p = {"sample_name": "TestSample", "acv": 1.0, "mtm": 0.5,
         "wire_mode": "2", "delay": 0.0, "lcr_visa": "GPIB0::10::INSTR",
         "frequencies": [1000.0, 2000.0], "geometry_mode": "diameter",
         "diameter_mm": 9.68, "thickness_mm": 1.14, "save_pica": True,
         "save_windeta": True, "temp_controller": "Lakeshore 340"}
    p.update(over)
    return p


def _gui(fake_alpha=None, freqs=(1000.0, 2000.0), save_dir=None):
    """A CombinedGUI with no window, wired to a FakeAlpha."""
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.lcr_backend, fake = connected_backend(mod, fake_alpha)
    gui.lcr_params = _lcr_params(frequencies=list(freqs))
    gui.lcr_backend.params = gui.lcr_params
    gui.c0_farads = mod.c0_from_params(gui.lcr_params)
    gui.sweep_frequencies = np.array(freqs)
    gui.save_dir = save_dir or tempfile.mkdtemp(prefix="pica_l340_alpha_")
    gui.gui_queue = queue.Queue()
    gui.cmd_queue = queue.Queue()
    gui.is_running = True
    gui._paused = False
    gui._skip_requested = False
    gui._worker_phase = None
    gui.recoveries = []

    def _no_temp_log(target, measuring_flag):
        return None
    gui._log_temperature_point = _no_temp_log

    def _recover(context, err):
        gui.recoveries.append((context, err))
        return False
    gui._comm_recover = _recover
    return gui, fake


def _logs(gui):
    out = []
    while not gui.gui_queue.empty():
        m = gui.gui_queue.get_nowait()
        if m["type"] == "log":
            out.append(m["text"])
    return out


def _sweep_rows(gui):
    """PICA file: its first '#' row, the header and the parsed data rows
    (the WinDETA metadata rows in between are checked separately)."""
    names = [n for n in os.listdir(gui.save_dir)
             if n.endswith("_FreqScan_pica_format.dat")]
    assert len(names) == 1, names
    with open(os.path.join(gui.save_dir, names[0]), encoding="utf-8") as f:
        lines = f.read().splitlines()
    n_meta = sum(1 for ln in lines if ln.startswith("#"))
    lines = [lines[0]] + lines[n_meta:]
    return lines, [[float(v) for v in ln.split("\t")] for ln in lines[2:]]


# --- tests -------------------------------------------------------------------

def test_common_alpha_backend_rules():
    run_common_backend_checks(mod, PATH)


def test_sweep_rows_use_the_actual_frequency_and_keep_the_layout():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1001.5 2 1",
                               "ZRE=1E5 -2E6 2003.0 2 1"]})
    gui, _ = _gui(fake)
    try:
        done = gui._run_frequency_sweep(150.0, 0, 2)
        assert done == 2
        lines, rows = _sweep_rows(gui)
        assert lines[1] == mod.AlphaAN_Backend.DATA_HEADER
        assert len(lines[1].split("\t")) == 20
        assert "DC" not in lines[0] and "1.0 Vrms" in lines[0]
        assert [r[0] for r in rows] == [1001.5, 2003.0]
        assert all(r[-1] == 150.0 and len(r) == 20 for r in rows)
        expect = gui.calculate_impedance_parameters(1001.5, 1e5, -2e6)
        assert np.allclose(rows[0][1:-1], expect, rtol=1e-6)
        sent = [c for c in fake.commands if c.startswith("GFR=")]
        assert sent == ["GFR=1.000000e+03", "GFR=2.000000e+03"]
        assert not any(c.startswith(("DCV", "DCE")) for c in fake.commands)
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)


def test_status_6_aborts_the_run_without_a_reconnect():
    gui, _ = _gui(FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 6 1"}))
    try:
        gui._run_frequency_sweep(150.0, 0, 2)
    except RuntimeError as exc:
        assert "status 6" in str(exc)
    else:
        raise AssertionError("status 6 must abort the run")
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    assert gui.recoveries == []


def test_a_non_valid_status_is_logged_and_the_row_discarded():
    gui, _ = _gui(FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 3 1"}),
                  freqs=(1000.0,))
    try:
        assert gui._run_frequency_sweep(150.0, 0, 1) == 1   # still counted
        _, rows = _sweep_rows(gui)
        assert rows == []
        assert any("status 3" in t and "Point discarded" in t
                   for t in _logs(gui))
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)


def test_an_instrument_answered_error_is_not_treated_as_a_link_loss():
    gui, _ = _gui(FakeAlpha({"ZRE?": "CR"}), freqs=(1000.0,))
    try:
        gui._run_frequency_sweep(150.0, 0, 1)
    except RuntimeError as exc:
        assert "Recalibrate" in str(exc)
    else:
        raise AssertionError("'CR' must abort, not loop in the reconnect")
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    assert gui.recoveries == []


def test_a_missing_srq_reconnects_then_retries_the_same_point():
    fake = FakeAlpha(srq_raises=True)
    gui, _ = _gui(fake, freqs=(1000.0,))

    def _recover(context, err):
        gui.recoveries.append(context)
        fake.srq_raises = False          # link is back
        return True
    gui._comm_recover = _recover
    try:
        assert gui._run_frequency_sweep(150.0, 0, 1) == 1
        _, rows = _sweep_rows(gui)
        assert len(rows) == 1
        assert gui.recoveries == ["Alpha-AN measurement @ 1000 Hz"]
        assert "MBK" in fake.commands           # SRQ timeout aborted first
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)


def test_reconnect_is_connect_plus_initialize_and_never_dc_bias():
    backend, fake = connected_backend(mod)
    backend.params = _lcr_params()
    saved = mod.RESET_RETRY_INTERVAL_S
    mod.RESET_RETRY_INTERVAL_S = 0.0
    try:
        backend.reconnect()
    finally:
        mod.RESET_RETRY_INTERVAL_S = saved
    cmds = fake.commands
    assert backend.rm.opened == ["GPIB0::10::INSTR"]
    assert backend.instrument is fake
    first_idn = cmds.index("*IDN?")
    # B1: parked, session released WITHOUT *RST; the *RST comes from
    # initialize_instrument, which waits for the Alpha to rejoin the bus.
    assert cmds[:3] == ["ACV=0", "ZCONSPL=0", "*IDN?"]
    assert cmds.count("*RST") == 1
    for c in ("INTTYP?", "MODE=IMP", "FRS=2", "ACV=1", "MTM=0.5"):
        assert c in cmds[first_idn:], c
    assert fake.kind_of("ACV=1") == "query"                # ack read
    assert not any(c.startswith(("DCV", "DCE")) for c in cmds)
    assert fake.read_termination == "" and fake.send_end is True


def test_settings_are_validated_before_either_instrument_is_opened():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.ls_backend = _FakeL340()
    gui.lcr_backend = mod.AlphaAN_Backend.__new__(mod.AlphaAN_Backend)
    gui.lcr_backend.instrument = None
    gui.lcr_backend.params = {}
    gui.lcr_backend._task_may_be_running = False
    gui.lcr_backend.rm = FakeRM(FakeAlpha())
    gui.lcr_params = _lcr_params(acv=5.0)        # over the 3 Vrms ceiling
    gui.params = {"ls_visa": "GPIB0::19::INSTR", "sensor": "A"}
    gui.gui_queue = queue.Queue()
    gui.is_running = True
    gui._hardware_worker_loop()
    assert gui.ls_backend.calls == [("shutdown",)]   # never connected
    assert gui.lcr_backend.rm.opened == []
    assert any("Vrms" in t for t in _logs(gui))


def test_start_validation_reads_the_alpha_fields():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.freq_cb = _Entry(mod.CUSTOM_FREQ_LABEL)
    gui.custom_freq_text = _Entry("40, 2e6\n")
    gui.log = lambda msg: None
    gui.lcr_cb = _Entry("GPIB0::10::INSTR  ->  ALPHA Analyzer")
    gui.wire_cb = _Entry("4")
    gui.lcr_entries = {"sample_name": _Entry("S"), "ac_bias": _Entry("2.5"),
                       "mtm": _Entry("1"), "delay": _Entry("0"),
                       "geom_value": _Entry("10"),
                       "thickness_mm": _Entry("0.5")}
    gui.geom_mode_var = _Entry("diameter")
    gui.fmt_pica_var = _Entry(True)
    gui.fmt_windeta_var = _Entry(False)
    p = gui._validate_lcr_params()
    assert p["diameter_mm"] == 10.0 and p["save_windeta"] is False
    assert math.isclose(gui.c0_farads, mod.compute_c0(10.0, 0.5))
    assert (p["acv"], p["mtm"], p["wire_mode"]) == (2.5, 1.0, "4")
    assert p["lcr_visa"] == "GPIB0::10::INSTR"
    assert p["frequencies"] == [40.0, 2e6]
    assert "dc_bias" not in p
    for key, bad in (("ac_bias", "3.5"), ("mtm", "0"), ("delay", "-1"),
                     ("geom_value", "0"), ("thickness_mm", "-1")):
        saved = gui.lcr_entries[key]
        gui.lcr_entries[key] = _Entry(bad)
        try:
            gui._validate_lcr_params()
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {key}={bad}")
        finally:
            gui.lcr_entries[key] = saved


class _ScanDevice:
    def __init__(self, idn, log):
        self.idn, self.log = idn, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, command):
        self.log.append((self.write_termination, self.send_end, command))
        return self.idn


class _ScanRM:
    IDNS = {"GPIB0::10::INSTR": "ALPHA Analyzer, Novocontrol Technologies, x",
            "GPIB0::12::INSTR": "LSCI,MODEL350,1,1",
            "GPIB0::19::INSTR": "LSCI,MODEL340,1,1"}

    def __init__(self):
        self.log = []

    def list_resources(self):
        return tuple(self.IDNS)

    def open_resource(self, res):
        return _ScanDevice(self.IDNS[res], self.log)


def test_visa_scan_is_alpha_safe_and_picks_both_instruments():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    rm = _ScanRM()
    gui.ls_backend = _FakeL340()
    gui.ls_backend.rm = rm
    gui.lcr_backend, _ = connected_backend(mod)
    gui.ls_cb, gui.lcr_cb = _Combo(), _Combo()
    lines = []
    gui.log = lines.append
    saved = mod.PYVISA_AVAILABLE
    mod.PYVISA_AVAILABLE = True
    try:
        gui._scan_for_visa()
    finally:
        mod.PYVISA_AVAILABLE = saved
    assert gui.ls_cb.value.startswith("GPIB0::19::INSTR")
    assert gui.lcr_cb.value.startswith("GPIB0::10::INSTR")
    assert all(term == "" and end is True for term, end, _ in rm.log)


def _sweep_rows_for_fix(gui):
    return _sweep_rows(gui)


# --- output formats (PICA metadata + WinDETA file per step) -----------------

def _windeta_raw(gui):
    names = [n for n in os.listdir(gui.save_dir)
             if n.endswith("_FreqScan_windeta_format.txt")]
    assert len(names) <= 1, names
    if not names:
        return None
    with open(os.path.join(gui.save_dir, names[0]), "rb") as fh:
        return fh.read()


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
    assert len(rows) == 2 and all(len(r) == 9 for r in rows)
    want = mod.impedance_to_dielectric(1e5, -2e6, 1001.5, gui.c0_farads)
    assert all(math.isclose(a, b, rel_tol=1e-5)
               for a, b in zip(rows[0], want))


def test_the_dielectric_conversion_is_the_frequency_scan_one():
    fscan, _ = load_port("Frequency_Scan_AlphaAN_GUI.py")
    assert mod.WINDETA_HEADER == fscan.WINDETA_HEADER
    for zr, zi, f, c0 in ((1e5, -2e6, 1e3, 1e-12), (3.0, 40.0, 1e6, 5e-13)):
        assert (mod.impedance_to_dielectric(zr, zi, f, c0)
                == fscan.impedance_to_dielectric(zr, zi, f, c0))
    assert mod.compute_c0_from_area(7.0, 0.5) == \
        fscan.compute_c0_from_area(7.0, 0.5)


def test_pica_file_carries_ascii_metadata_and_unticked_formats_are_skipped():
    gui, _ = _gui(freqs=(1000.0,))
    gui.lcr_params.update(save_windeta=False, geometry_mode="area",
                          area_mm2=50.0)
    gui._run_frequency_sweep(80.0, 0, 1)
    assert _windeta_raw(gui) is None
    lines, _ = _sweep_rows(gui)
    assert lines[0].startswith("# Sample: TestSample | T_set = 80.0 K | AC:")
    names = os.listdir(gui.save_dir)
    with open(os.path.join(gui.save_dir, names[0]), encoding="utf-8") as fh:
        meta = [ln for ln in fh.read().splitlines() if ln.startswith("#")]
    assert all(ln.isascii() for ln in meta)
    joined = "\n".join(meta)
    for want in ("A = 50.0 mm^2", "Area electrode",
                 f"C0 = {gui.c0_farads:.5e} F", "ZREFMODE=-3", "ZSLCAL=1",
                 "ZLLCOR=1", "DRS=0 0", "Lakeshore 340", "1 pts"):
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
    gui, fake = _gui()
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
        gui, fake = _gui()
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


# --- Lakeshore 340 identity guard --------------------------------------------

def _ls_backend(idn):
    fake = FakeAlpha({"*IDN?": idn})       # any recording VISA stand-in
    ls = mod.Lakeshore_Backend.__new__(mod.Lakeshore_Backend)
    ls.lakeshore = None
    ls.last_visa = None
    ls.was_connected = False
    ls.idn = ""
    ls.rm = FakeRM(fake)
    return ls, fake


def test_lakeshore_connect_refuses_a_non_340_before_any_write():
    for idn in ("LSCI,MODEL350,LSA1234,1.2",
                "ALPHA Analyzer, Novocontrol Technologies, x",
                "KEITHLEY INSTRUMENTS,MODEL 2400,1,C30"):
        ls, fake = _ls_backend(idn)
        try:
            ls.connect("GPIB0::19::INSTR")
        except RuntimeError as exc:
            assert "not a Lake Shore Model 340" in str(exc)
        else:
            raise AssertionError(f"{idn} accepted as a 340")
        assert fake.commands == ["*IDN?"] and fake.closed
        assert ls.lakeshore is None
        ls.shutdown()                       # the worker's finally
        assert fake.commands == ["*IDN?"]   # still nothing written


def test_lakeshore_connect_accepts_a_340_and_clears_after_the_check():
    ls, fake = _ls_backend("LSCI,MODEL340,123456,061407")
    assert ls.connect("GPIB0::19::INSTR") == "LSCI,MODEL340,123456,061407"
    assert fake.commands == ["*IDN?", "*CLS"]
    assert ls.lakeshore is fake and not fake.closed


class _ClearingSession(FakeAlpha):
    """Recording VISA stand-in that also records a VISA device clear."""

    def clear(self):
        self.traffic.append(("clear", None))


def _reconnect_backend(idns):
    """A backend that was connected to a 340 at the right address and
    whose fresh session answers *IDN? with idns in turn."""
    ls, _ = _ls_backend("unused")
    fake = _ClearingSession({"*IDN?": list(idns)})
    ls.rm = FakeRM(fake)
    ls.last_visa = "GPIB0::19::INSTR"
    ls.was_connected = True
    return ls, fake


def test_a_stale_idn_on_reconnect_gets_a_clear_and_one_more_ask():
    ls, fake = _reconnect_backend(["+300.000", "LSCI,MODEL340,123456,061407"])
    assert ls.reconnect() == "LSCI,MODEL340,123456,061407"
    assert [k for k, _ in fake.traffic] == ["query", "clear", "query",
                                            "write"]
    assert fake.commands == ["*IDN?", "*IDN?", "*CLS"]
    assert ls.lakeshore is fake


def test_a_wrong_model_on_reconnect_is_still_refused_after_one_retry():
    ls, fake = _reconnect_backend(["+300.000", "LSCI,MODEL350,LSA1234,1.2"])
    try:
        ls.reconnect()
    except RuntimeError as exc:
        assert "not a Lake Shore Model 340" in str(exc)
    else:
        raise AssertionError("a non-340 must be refused")
    assert fake.commands == ["*IDN?", "*IDN?"] and fake.closed
    assert ls.lakeshore is None


def test_start_never_retries_or_clears_an_unknown_device():
    ls, fake = _ls_backend("+300.000")
    fake.clear = lambda: fake.traffic.append(("clear", None))
    try:
        ls.connect("GPIB0::19::INSTR")
    except RuntimeError:
        pass
    else:
        raise AssertionError("a non-IDN answer at Start must be refused")
    assert fake.traffic == [("query", "*IDN?")]


def test_shutdown_after_a_lost_session_reports_instead_of_crashing():
    ls, fake = _reconnect_backend(["LSCI,MODEL350,LSA1234,1.2", "LSCI,MODEL350,LSA1234,1.2"])
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

def _validate(preset=None, freqs=(40.0, 1e3, 2e6)):
    """Bare GUI with valid Alpha fields and a chosen frequency list."""
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.lcr_backend, fake = connected_backend(mod)
    gui.freq_cb = _Entry(preset or mod.CUSTOM_FREQ_LABEL)
    gui.custom_freq_text = _Entry(", ".join(str(f) for f in freqs) + "\n")
    gui.log = lambda msg: None
    gui.lcr_cb = _Entry("GPIB0::10::INSTR  ->  ALPHA Analyzer")
    gui.wire_cb = _Entry("2")
    gui.lcr_entries = {"sample_name": _Entry("S"), "ac_bias": _Entry("1.0"),
                       "mtm": _Entry("0.5"), "delay": _Entry("0"),
                       "geom_value": _Entry("1.0"),
                       "thickness_mm": _Entry("1.0")}
    gui.geom_mode_var = _Entry("area")
    gui.fmt_pica_var = _Entry(True)
    gui.fmt_windeta_var = _Entry(True)
    return gui, fake


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
        assert p["frequencies"] == list(freqs)
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
