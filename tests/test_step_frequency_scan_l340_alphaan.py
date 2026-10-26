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

    def focus_set(self):
        self.focused = True


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
    gui.lcr_params["save_dir"] = gui.save_dir   # Start's snapshot
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


# --- 27 Oct 2026 review fixes (L340A-01 ... L340A-15) ------------------------

class _Widget:
    """Records config(state=...) / text, for set_ui_state and banners."""

    def __init__(self, text=""):
        self.state = "normal"
        self.text = text
        self.bg = None

    def config(self, **kw):
        self.state = kw.get("state", self.state)
        self.text = kw.get("text", self.text)
        self.bg = kw.get("bg", self.bg)

    configure = config

    def cget(self, key):
        return self.text if key == "text" else self.state


class _Root:
    def __init__(self):
        self.destroyed = False
        self.afters = []

    def after(self, ms, fn=None):
        self.afters.append((ms, fn))
        return "after#1"

    def destroy(self):
        self.destroyed = True


class _Box:
    """messagebox stand-in: records every call, answers askyesno 'no'."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _call(*args, **_kw):
            self.calls.append((name,) + args)
            return False
        return _call


def _with_messagebox(fn):
    saved = mod.messagebox
    box = mod.messagebox = _Box()
    try:
        fn(box)
    finally:
        mod.messagebox = saved
    return box


def _queue_gui(banner):
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.gui_queue = queue.Queue()
    gui.lbl_status = _Widget(banner)
    gui.worker_thread = None
    gui.root = _Root()
    gui.set_ui_state = lambda running: None
    gui._set_keep_awake = lambda enable: None
    gui.log = lambda msg: None
    gui._beep = lambda times=1: None
    gui._pending_progress = None
    gui.progress = {"value": 0, "maximum": 10}
    gui._scan_info = None
    gui._freq_plot_dirty = False
    return gui


def test_l340a_01_worker_done_keeps_the_end_of_run_banner():
    for final in ("COMPLETE", "ERROR", "SAFETY ABORT (MAX TEMP)",
                  "OVERTEMP ABORT",
                  "HEATER NOT TURNED OFF - check the Lakeshore"):
        gui = _queue_gui("STABILIZING 100 K")
        gui._put_gui_msg("status", text=final, color="red")
        gui._put_gui_msg("worker_done")
        gui._process_gui_queue()
        assert gui.lbl_status.text == final, final
    # Stop pressed: the mid-run banner does turn into IDLE
    gui = _queue_gui("STOPPING…")
    gui._put_gui_msg("worker_done")
    gui._process_gui_queue()
    assert gui.lbl_status.text == "IDLE"


def _ls_gui(**over):
    """Bare GUI whose temperature boxes hold the panel defaults."""
    values = {"tol": "0.5", "soak": "120", "drift": "0.10",
              "stab_timeout": "120", "delay": "1", "step_factor": "0.2",
              "first_factor": "0.5", "min_rate": "0.1", "max_rate": "5.0",
              "rate": "2.0", "app_band": "3.0", "app_rate": "2.0",
              "max_temp": "320"}
    values.update(over)
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.entries = {k: {"entry": _Entry(v), "lock": _Widget(),
                       "locked": False, "name": k.upper()}
                   for k, v in values.items()}
    gui.entries["app_rate"]["name"] = "Approach Rate (K/min)"
    gui.entries["delay"]["name"] = "Poll Delay (s)"
    gui.ls_cb = _Entry("GPIB0::19::INSTR  ->  LSCI,MODEL340")
    gui.heater_range_var = _Entry("5 (Max)")
    gui.sensor_var = _Entry("A")
    gui.ramp_mode_var = _Entry("adaptive")
    gui.var_auto_pid = _Entry(True)
    gui.approach_var = _Entry("Two-stage (default)")
    gui.ramp_table_var = _Entry("77:0.3, 100:0.5, else:5")
    gui.cmd_queue = queue.Queue()
    gui.log = lambda msg: None
    return gui


def test_l340a_02_a_non_ascii_sample_name_is_refused_at_start():
    for name in ("BaTiO3-δ", "Fe→Co", "Sµ"):
        gui, fake = _validate()
        gui.lcr_entries["sample_name"] = _Entry(name)
        try:
            gui._validate_lcr_params()
        except ValueError as exc:
            assert "ASCII" in str(exc), exc
        else:
            raise AssertionError(f"accepted sample name {name!r}")
        assert fake.commands == []
    gui, _ = _validate()
    gui.lcr_entries["sample_name"] = _Entry("BaTiO3-delta_1.5")
    assert gui._validate_lcr_params()["sample_name"] == "BaTiO3-delta_1.5"


def test_l340a_03_live_updates_get_the_start_range_checks():
    for key, bad in (("delay", "-1"), ("delay", "0"), ("delay", "nan"),
                     ("tol", "0"), ("max_temp", "500"), ("rate", "200")):
        gui = _ls_gui()
        gui.params = gui._validate_ls_params()
        gui.is_running = True
        gui.entries[key]["entry"] = _Entry(bad)
        box = _with_messagebox(lambda b: gui._send_live_updates())
        assert gui.cmd_queue.empty(), (key, bad)
        assert box.calls and box.calls[0][0] == "showerror", (key, bad)
    gui = _ls_gui()
    gui.params = gui._validate_ls_params()
    gui.is_running = True
    gui.entries["delay"]["entry"] = _Entry("2")
    box = _with_messagebox(lambda b: gui._send_live_updates())
    assert box.calls == []
    kind, updates = gui.cmd_queue.get_nowait()
    assert kind == "params" and updates["delay"] == 2.0


def _ui_gui():
    """Bare GUI with every widget set_ui_state touches."""
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    for name in ("start_button", "stop_button", "pause_button",
                 "skip_button", "approach_cb", "rb_adaptive", "rb_fixed",
                 "ramp_table_entry", "entry_start", "entry_end",
                 "entry_step", "entry_manual", "ls_cb", "sensor_cb",
                 "lcr_cb", "freq_cb", "custom_freq_text",
                 "list_maker_button", "wire_cb", "browse_button"):
        setattr(gui, name, _Widget())
    gui.entries = {"tol": {"entry": _Widget(), "lock": _Widget(),
                           "locked": False}}
    gui.lcr_entries = {k: _Widget() for k in (
        "sample_name", "ac_bias", "mtm", "delay", "geom_value",
        "thickness_mm")}
    gui.geom_radios = [_Widget(), _Widget()]
    gui.fmt_checks = [_Widget(), _Widget()]
    gui.log_freq_entries = {"start": _Widget()}
    gui._on_ramp_mode_changed = lambda: None
    gui._on_freq_list_change = lambda _event=None: None
    return gui


def test_l340a_04_and_14_run_settings_and_browse_are_locked_during_a_run():
    gui = _ui_gui()
    locked = ([gui.browse_button] + list(gui.lcr_entries.values())
              + gui.geom_radios + gui.fmt_checks)
    gui.set_ui_state(running=True)
    assert all(w.state == "disabled" for w in locked)
    assert gui.wire_cb.state == "disabled"
    gui.set_ui_state(running=False)
    assert all(w.state == "normal" for w in locked)
    assert gui.wire_cb.state == "readonly"


def test_l340a_04_the_sweep_writes_to_the_folder_taken_at_start():
    gui, _ = _gui(FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 2 1"}),
                  freqs=(1000.0,))
    other = tempfile.mkdtemp(prefix="pica_l340_alpha_other_")
    try:
        gui.save_dir = other        # Browse mid-run (now disabled anyway)
        gui._run_frequency_sweep(150.0, 0, 1)
        assert os.listdir(other) == []
        assert len(os.listdir(gui.lcr_params["save_dir"])) == 2
    finally:
        shutil.rmtree(other, ignore_errors=True)
        shutil.rmtree(gui.lcr_params["save_dir"], ignore_errors=True)


def test_l340a_05_bad_file_name_characters_are_refused_at_start():
    for name in ("S1/2", "run:A", "a*b", 'q"x', "p|q"):
        gui, fake = _validate()
        gui.lcr_entries["sample_name"] = _Entry(name)
        try:
            gui._validate_lcr_params()
        except ValueError as exc:
            assert "cannot contain" in str(exc), exc
        else:
            raise AssertionError(f"accepted sample name {name!r}")
        assert fake.commands == []


def test_l340a_05_a_missing_save_folder_is_refused_before_any_check():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.worker_thread = None
    gui.listbox = _Entry(["300.00"])
    gui.save_dir = os.path.join(tempfile.gettempdir(),
                                "pica_l340_no_such_folder_27oct")

    def _never():
        raise AssertionError("validation ran with no save folder")
    gui._validate_ls_params = _never
    box = _with_messagebox(lambda b: gui.start_sequence())
    assert box.calls and "choose it again" in box.calls[0][2]


def test_l340a_06_nan_and_inf_are_refused():
    for key in ("tol", "soak", "drift", "delay", "max_temp"):
        gui = _ls_gui(**{key: "nan"})
        try:
            gui._validate_ls_params()
        except ValueError as exc:
            assert "finite" in str(exc), exc
        else:
            raise AssertionError(f"accepted {key}=nan")
    for key, bad in (("thickness_mm", "nan"), ("geom_value", "inf"),
                     ("ac_bias", "nan"), ("delay", "inf")):
        gui, fake = _validate()
        gui.lcr_entries[key] = _Entry(bad)
        try:
            gui._validate_lcr_params()
        except ValueError as exc:
            assert "finite" in str(exc), exc
        else:
            raise AssertionError(f"accepted {key}={bad}")
        assert fake.commands == []
    params = dict(_lcr_params(), thickness_mm=float("nan"))
    try:
        mod.validate_output_settings(params)
    except ValueError as exc:
        assert "Thickness" in str(exc)
    else:
        raise AssertionError("validate_output_settings accepted nan")


def test_l340a_07_a_bad_number_names_its_box():
    gui = _ls_gui(app_rate="abc")
    try:
        gui._validate_ls_params()
    except ValueError as exc:
        assert str(exc) == 'Approach Rate (K/min): "abc" is not a number.'
    else:
        raise AssertionError("accepted Approach Rate 'abc'")
    assert gui.entries["app_rate"]["entry"].focused
    for key, bad, box in (("thickness_mm", "", "Thickness (mm)"),
                          ("ac_bias", "1,5", "AC Voltage (Vrms)")):
        gui, _ = _validate()
        gui.lcr_entries[key] = _Entry(bad)
        try:
            gui._validate_lcr_params()
        except ValueError as exc:
            assert str(exc).startswith(box + ":"), exc
        else:
            raise AssertionError(f"accepted {key}={bad!r}")


def test_l340a_08_close_after_stop_waits_for_the_worker():
    import threading
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    release = threading.Event()
    gui.worker_thread = threading.Thread(target=release.wait, daemon=True)
    gui.worker_thread.start()
    gui.is_running = False          # Stop already seen by the worker
    gui.root = _Root()
    gui.log = lambda msg: None
    gui.lcr_params = _lcr_params()
    gui.sweep_frequencies = [1000.0]
    try:
        box = _with_messagebox(lambda b: gui._on_closing())
        assert box.calls == []           # no askyesno: Stop already sent
        assert gui.root.destroyed is False
        assert gui.root.afters           # polling for the worker's exit
    finally:
        release.set()
        gui.worker_thread.join(2)
    gui._poll_worker_exit_then_destroy()
    assert gui.root.destroyed is True


def test_l340a_09_freq_delay_answers_stop_and_counts_in_the_close_wait():
    import threading
    import time
    gui, fake = _gui(FakeAlpha(), freqs=(1000.0,))
    gui.lcr_params["delay"] = 30.0
    threading.Timer(0.3, lambda: gui.cmd_queue.put(("stop",))).start()
    t0 = time.time()
    try:
        assert gui._run_frequency_sweep(150.0, 0, 1) == 0
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    assert time.time() - t0 < 5.0
    assert not any(c.startswith("GFR=") for c in fake.commands)
    no_delay = dict(gui.lcr_params, delay=0.0)
    wait_30 = gui._close_wait_s()
    gui.lcr_params = no_delay
    assert math.isclose(wait_30 - gui._close_wait_s(), 30.0)


def test_l340a_10_discarded_and_skipped_points_move_the_progress_bar():
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 3 1"})
    gui, _ = _gui(fake, freqs=(1000.0,))
    try:
        assert gui._run_frequency_sweep(150.0, 0, 1) == 1
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    msgs = [gui.gui_queue.get_nowait() for _ in range(gui.gui_queue.qsize())]
    assert [m["progress"] for m in msgs if m["type"] == "progress"] == [1]
    # Skip at the start of a 3-point sweep: all three count as done
    gui, _ = _gui(FakeAlpha(), freqs=(1000.0, 2000.0, 3000.0))
    gui.cmd_queue.put(("skip",))
    try:
        assert gui._run_frequency_sweep(150.0, 4, 7) == 7
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    # sequence_complete fills the bar whatever was counted
    q = _queue_gui("COMPLETE")
    q.progress["value"] = 3
    q._put_gui_msg("sequence_complete")
    q._process_gui_queue()
    assert q.progress["value"] == q.progress["maximum"]


def test_l340a_11_the_left_panel_scrolls_with_the_wheel():
    import inspect
    src = inspect.getsource(mod.CombinedGUI._populate_left)
    assert 'bind_all("<MouseWheel>"' in src and 'unbind_all("<MouseWheel>")' in src
    assert "e.delta / 120" in src and "tk.Listbox" in src


def test_l340a_12_frames_inside_panels_take_the_panel_colour():
    import inspect
    styles = inspect.getsource(mod.CombinedGUI.setup_styles)
    assert '"Panel.TFrame", background=self.CLR_FRAME_BG' in styles
    lcr = inspect.getsource(mod.CombinedGUI._create_lcr_settings_panel)
    assert lcr.count('style="Panel.TFrame"') == 5
    assert 'style="Bar.TLabel"' in inspect.getsource(
        mod.CombinedGUI._build_plots)


def test_l340a_13_start_shows_connecting_straight_away():
    import inspect
    src = inspect.getsource(mod.CombinedGUI.start_sequence)
    lock = src.index("self.set_ui_state(running=True)")
    banner = src.index('self._update_status_ui("CONNECTING')
    worker = src.index("self.worker_thread = threading.Thread")
    assert lock < banner < worker


def test_l340a_15_startup_log_names_the_real_version_and_list():
    import inspect
    src = inspect.getsource(mod.CombinedGUI.__init__)
    assert "v1.4" not in src and "40 Hz" not in src
    assert "self.PROGRAM_VERSION" in src and "log_list_label(" in src


# --- ported from the L350 Step module's 27 Oct 2026 fixes (S4, S6, S7) -------

def _status_texts(gui):
    out = []
    while not gui.gui_queue.empty():
        m = gui.gui_queue.get_nowait()
        if m["type"] == "status":
            out.append(m["text"])
    return out


class _WorkerL340:
    """Model 340 backend stand-in for the worker's start-up path."""

    MAX_CURRENT_CODES = {3: "1.0 A"}

    def __init__(self):
        self.ramps = []
        self.prepared = False

    def connect(self, visa):
        return "LSCI,MODEL340,0,0"

    def prepare_loop(self, sensor):
        self.prepared = True
        return ({"input": sensor}, 1,
                {"sp_limit": 325.0, "max_current": 3, "max_range": 5})

    def range_code(self, heater_range):
        return int(heater_range)

    def get_heater_status(self):
        return 0, "no error"

    def configure_ramp(self, *args):
        self.ramps.append(args)

    def shutdown(self):
        return True


class _WorkerAlpha:
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
    gui._current_target = None
    gui.params = {"ls_visa": "GPIB0::19::INSTR", "sensor": "A",
                  "approach_side": None, "heater_range": "3"}
    gui.lcr_params = _lcr_params(save_dir=save_dir)
    gui.setpoint_floats = [100.0]
    gui.sweep_frequencies = [1000.0, 2000.0]
    gui.ls_backend = _WorkerL340()
    return gui


def test_l340_s4_stop_during_connect_or_init_never_programs_the_heater():
    tmp = tempfile.mkdtemp(prefix="pica_l340_s4_")
    try:
        # Stop pressed while the Lakeshore was connecting.
        gui = _worker_gui(tmp)
        gui.lcr_backend = _WorkerAlpha()
        gui.cmd_queue.put(("stop",))
        gui._hardware_worker_loop()
        assert not gui.ls_backend.prepared and not gui.lcr_backend.connected
        assert gui.ls_backend.ramps == [] and os.listdir(tmp) == []
        msgs = [gui.gui_queue.get_nowait()
                for _ in range(gui.gui_queue.qsize())]
        assert msgs[-1]["type"] == "worker_done"
        assert not any(m["type"] == "status" and m["text"] == "ERROR"
                       for m in msgs)
        # Stop pressed during the Alpha *RST / initialize waits.
        gui = _worker_gui(tmp)
        gui.lcr_backend = _WorkerAlpha(
            on_init=lambda: gui.cmd_queue.put(("stop",)))
        gui._hardware_worker_loop()
        assert gui.lcr_backend.connected
        assert gui.ls_backend.ramps == [] and os.listdir(tmp) == []
        assert gui.is_running is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_l340_s4_the_setpoint_loop_checks_stop_before_the_ramp():
    import inspect
    src = inspect.getsource(mod.CombinedGUI._hardware_worker_loop)
    top = src.index("for i, target in enumerate(self.setpoint_floats):")
    check = src.index("if self._process_cmd_queue() or not self.is_running:",
                      top)
    assert check < src.index("self._ramp_and_stabilize(", top)


def test_l340_s6_resume_and_reconnect_restore_the_measuring_banner():
    gui, _ = _gui(FakeAlpha(), freqs=(1000.0,))
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
        texts = _status_texts(gui)
        assert texts[0].startswith("PAUSED")
        assert texts[-1] == "MEASURING AT 150.0 K"
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)
    # Reconnect: _comm_recover leaves a neutral banner, not COMM LOST.
    gui = _recover_gui(_FakeLcr())
    assert _with_clock(lambda: gui._comm_recover("test", OSError("x")))
    texts = _status_texts(gui)
    assert texts[0].startswith("COMM LOST")
    assert texts[-1] == "RECONNECTED - resuming"


def test_l340_s6_a_reconnect_mid_sweep_restores_the_measuring_banner():
    fake = FakeAlpha()
    gui, _ = _gui(fake, freqs=(1000.0,))
    real_measure = gui.lcr_backend.measure_point
    fails = [OSError("bus")]

    def _measure(freq):
        if fails:
            raise fails.pop()
        return real_measure(freq)
    gui.lcr_backend.measure_point = _measure
    gui._comm_recover = lambda context, err: True
    try:
        assert gui._run_frequency_sweep(150.0, 0, 1) == 1
        assert _status_texts(gui) == ["MEASURING AT 150.0 K"]
    finally:
        shutil.rmtree(gui.save_dir, ignore_errors=True)


def test_l340_s6_resume_restores_the_ramp_phase_banner():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.gui_queue = queue.Queue()
    gui.cmd_queue = queue.Queue()
    gui.is_running = True
    gui._skip_requested = False
    gui._paused = False
    gui._last_sample_valid = True
    gui.params = {"app_rate": 0.5, "app_band": 3.0, "tol": 0.5,
                  "approach_side": None, "delay": 0.0, "soak": 120.0,
                  "drift": 0.1, "stab_timeout": 0.0, "heater_range": "3",
                  "ramp_caps": [], "ramp_default_cap": 5.0}
    gui.ls_backend = _WorkerL340()
    polls = []

    def _temp(target, measuring_flag):
        polls.append(1)
        gui._paused = len(polls) == 2          # one paused poll
        if len(polls) >= 4:
            gui.is_running = False             # end the loop
        return 50.0, 0.0, 0
    gui._log_temperature_point = _temp
    gui._ramp_and_stabilize(100.0, 2.0)
    texts = _status_texts(gui)
    ramping = "RAMPING TO 100.0 K (pre: 97.0 K)"
    assert texts[0] == ramping
    assert texts[1].startswith("PAUSED")
    assert texts[2] == ramping


def test_l340_s7_a_cut_sweep_loses_its_measuring_title():
    gui = _queue_gui("STOPPING…")
    gui._scan_info = ("measuring", 150.0)
    gui._put_gui_msg("worker_done")
    gui._process_gui_queue()
    assert gui._scan_info == ("partial", 150.0) and gui._freq_plot_dirty
    gui = _queue_gui("COMPLETE")
    gui._scan_info = ("done", 150.0)              # a finished scan is held
    gui._put_gui_msg("worker_done")
    gui._process_gui_queue()
    assert gui._scan_info == ("done", 150.0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
