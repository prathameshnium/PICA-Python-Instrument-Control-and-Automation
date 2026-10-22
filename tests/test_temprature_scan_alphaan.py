"""Tests for novocontrol/Temprature_Scan_AlphaAN_GUI.py (Alpha-AN + L350).

Runnable as plain Python as well as under pytest.
"""

import math
import os
import queue
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    FakeAlpha, FakeLakeshore, FakeRM, connected_backend, load_port,
    run_common_backend_checks,
)

mod, PATH = load_port("Temprature_Scan_AlphaAN_GUI.py")


def _combined(fake_alpha=None, temps=(300.0,)):
    combined = mod.Combined_Backend()
    combined.lcr, _ = connected_backend(mod, fake_alpha)
    combined.lakeshore = FakeLakeshore(temps)
    return combined


def test_common_alpha_backend_rules():
    run_common_backend_checks(mod, PATH)


def test_every_point_carries_its_own_temperature_and_the_actual_frequency():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1001.5 2 1",
                               "ZRE=1E5 -2E6 2003.0 2 1"]})
    combined = _combined(fake, temps=(300.0, 301.0))
    cycle = combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    (t1, f1, _, _, s1, fa1), (t2, f2, _, _, s2, fa2) = cycle["points"]
    assert (t1, t2) == (300.0, 301.0)
    assert (f1, f2) == (1000.0, 2000.0)          # keys the per-freq file
    assert (fa1, fa2) == (1001.5, 2003.0)        # used for the conversion
    assert s1 == s2 == mod.STATUS_RESULT_VALID


def test_a_disconnected_signal_source_aborts_the_sweep():
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 6 1"})
    combined = _combined(fake)
    try:
        combined.measure_frequency_sweep([1000.0], 0)
    except RuntimeError as exc:
        assert "status 6" in str(exc)
    else:
        raise AssertionError("status 6 must abort the sweep")


def test_the_safety_kill_stops_before_measuring():
    fake = FakeAlpha()
    combined = _combined(fake, temps=(351.0,))
    cycle = combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    assert cycle["killed"] and len(cycle["points"]) == 1
    assert math.isnan(cycle["points"][0][2])
    assert "MST" not in fake.commands


def test_settings_are_validated_before_either_instrument_is_opened():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    params = {"lakeshore_visa": "GPIB0::12::INSTR",
              "lcr_visa": "GPIB0::10::INSTR", "frequencies": [5e6],
              "acv": 3.0, "mtm": 0.5, "wire_mode": "2"}
    try:
        combined.initialize_instruments(params)
    except ValueError as exc:
        assert "Vrms" in str(exc)
    else:
        raise AssertionError("3 Vrms at 5 MHz is over the ZG4 ceiling")
    assert fake.commands == [] and combined.lakeshore is None


def test_validator_refuses_bad_settings():
    good = dict(frequencies=[1e3], acv=1.0, mtm=0.5, wire_mode="2")
    mod.validate_alpha_parameters(**good)
    for bad in ({"frequencies": [30e6]}, {"acv": 0}, {"mtm": 0},
                {"wire_mode": "5"}, {"frequencies": []}):
        try:
            mod.validate_alpha_parameters(**{**good, **bad})
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


# --- pre-lab fixes (23 Oct 2026) -------------------------------------------

def _gui(**attrs):
    """A GUI object without Tk: only the attributes a test hands it."""
    gui = mod.Integrated_CT_GUI.__new__(mod.Integrated_CT_GUI)
    gui.logged = []
    gui.log = gui.logged.append
    for key, value in attrs.items():
        setattr(gui, key, value)
    return gui


class _NoDialogs:
    """Swaps the module's messagebox for one that fails on any call."""

    def __enter__(self):
        self.old = mod.messagebox
        self.calls = []
        calls = self.calls
        mod.messagebox = types.SimpleNamespace(
            showerror=lambda *a, **k: calls.append(("showerror", a)),
            showwarning=lambda *a, **k: calls.append(("showwarning", a)),
            showinfo=lambda *a, **k: calls.append(("showinfo", a)))
        return self

    def __exit__(self, *exc):
        mod.messagebox = self.old
        return False


def test_freq_tag_keeps_sub_hz_frequencies_apart():
    assert mod.freq_tag(0.1) == "0.1"
    assert mod.freq_tag(0.5) == "0.5"
    assert mod.freq_tag(0.1) != mod.freq_tag(0.5)
    assert mod.freq_tag(1000000) == "1000000"
    assert mod.freq_tag(1e6) == "1000000"
    assert mod.freq_tag(1000.0) == "1000"        # old file names unchanged
    assert mod.freq_tag(12.5) == "12.5"


def test_point_timeout_scales_with_mtm_and_period():
    base = mod.SRQ_TIMEOUT_MEASURE_S
    assert mod.point_timeout_s(1000.0, 0.5) == base
    assert mod.point_timeout_s(0.01, 0.5) == 4.0 * 100.0 + 60.0
    assert mod.point_timeout_s(1000.0, 100.0) == 4.0 * 100.0 + 60.0
    assert mod.point_timeout_s(1e6, 0.0) >= base


def test_measure_point_waits_the_scaled_timeout_by_default():
    backend, fake = connected_backend(mod)
    backend.params = {"mtm": 100.0}
    backend.measure_point(1000.0)
    waits = [t for k, t in fake.traffic if k == "wait_for_srq"]
    assert waits == [int(mod.point_timeout_s(1000.0, 100.0) * 1000)]


def _bare_alpha(fake):
    backend = mod.AlphaAN_Backend.__new__(mod.AlphaAN_Backend)
    backend.instrument = None
    backend.params = {}
    backend.rm = FakeRM(fake)
    backend._task_may_be_running = False
    return backend


def test_a_wrong_idn_gets_no_shutdown_commands():
    fake = FakeAlpha({"*IDN?": "LSCI,MODEL340,123456,061407"})
    backend = _bare_alpha(fake)
    try:
        backend.connect("GPIB0::10::INSTR")
    except ConnectionError as exc:
        assert "not a Novocontrol Alpha" in str(exc)
    else:
        raise AssertionError("a non-Alpha IDN must be refused")
    assert fake.commands == ["*IDN?"], fake.commands
    assert fake.closed and backend.instrument is None


def test_an_alpha_failing_later_in_the_handshake_is_parked():
    fake = FakeAlpha({"INTTYP?": "INTTYP=3 284"})
    backend = _bare_alpha(fake)
    try:
        backend.connect("GPIB0::10::INSTR")
    except ConnectionError:
        pass
    else:
        raise AssertionError("a non-ZG4 interface must be refused")
    assert "ACV=0" in fake.commands and fake.closed
    assert backend.instrument is None


def _write_cycle(points):
    with tempfile.TemporaryDirectory() as tmp:
        paths = {f: os.path.join(tmp, f"S-{mod.freq_tag(f)}Hz.txt")
                 for f in (1000.0, 2000.0, 3000.0)}
        for p in paths.values():
            open(p, "w").close()
        storage = {
            "time": [], "temperature": [],
            "cp": {f: {"T": [], "v": []} for f in paths},
            "g": {f: {"T": [], "v": []} for f in paths},
        }
        gui = _gui(freq_filepaths=paths, data_storage=storage,
                   _update_live_plots=lambda *a, **k: None)
        gui._process_cycle({"points": points, "heater": 10.0,
                            "tstatus": [(0, "ok")] * len(points)}, 1.0)
        rows = {f: open(p).read().splitlines() for f, p in paths.items()}
    return gui, rows, storage


def test_non_valid_status_rows_are_discarded_not_written():
    valid = mod.STATUS_RESULT_VALID
    points = [(300.0, 1000.0, 1e5, -2e6, valid, 1000.0),
              (300.1, 2000.0, 1e5, -2e6, 3, 2000.0),
              (360.0, 3000.0, float("nan"), float("nan"), -1, 3000.0)]
    gui, rows, storage = _write_cycle(points)
    assert len(rows[1000.0]) == 1
    assert rows[2000.0] == []                  # status 3: discarded
    assert len(rows[3000.0]) == 1              # safety-kill row kept
    assert storage["cp"][2000.0]["T"] == []
    assert storage["cp"][1000.0]["T"] == [300.0]
    warned = [m for m in gui.logged if "Point discarded" in m]
    assert len(warned) == 1 and "f=2000 Hz" in warned[0]


def test_per_frequency_files_do_not_collide_below_1_hz():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _gui(file_location_path=tmp, frequencies=[0.1, 0.5, 1e6])
        gui._create_per_frequency_files("S")
        names = sorted(os.path.basename(p)
                       for p in gui.freq_filepaths.values())
    assert names == ["S-0.1Hz.txt", "S-0.5Hz.txt", "S-1000000Hz.txt"]


def test_plot_dropdown_maps_by_index_not_by_label():
    titles = []
    gui = _gui(frequencies=[0.1, 0.5],
               plot_freq_cb=types.SimpleNamespace(current=lambda: 1,
                                                  get=lambda: "0 Hz"),
               _decade_ylims={},
               ax_main=types.SimpleNamespace(
                   set_title=lambda t, **k: titles.append(t)),
               _update_live_plots=lambda *a, **k: None)
    gui._on_plot_freq_change()
    assert gui.plot_freq == 0.5
    assert titles == ["Cp vs. T @ 0.5 Hz"]


def test_stop_deadline_covers_the_longest_point():
    gui = _gui(_stopping=False, is_running=True, is_stabilizing=False,
               stop_event=threading.Event(),
               stop_button=types.SimpleNamespace(config=lambda **k: None),
               backend=types.SimpleNamespace(params={"mtm": 0.5}),
               frequencies=[0.01, 1000.0],
               _poll_worker_stopped=lambda from_user: None)
    before = time.time()
    gui.stop_measurement(True)
    budget = gui._stop_deadline - before
    assert budget >= mod.point_timeout_s(0.01, 0.5) + 15.0 - 1.0
    assert budget > 15.0 + mod.SRQ_TIMEOUT_MEASURE_S


def test_start_refused_while_the_previous_worker_is_alive():
    def boom():
        raise AssertionError("must not parse settings")
    gui = _gui(measurement_thread=types.SimpleNamespace(
        is_alive=lambda: True), _parse_frequencies=boom)
    with _NoDialogs() as dialogs:
        gui.start_measurement()
    assert dialogs.calls == []
    assert any("still finishing" in m for m in gui.logged)


def test_a_startup_failure_closes_the_instruments():
    closed = []

    def fail():
        raise RuntimeError("MTM= refused")
    gui = _gui(measurement_thread=None, _parse_frequencies=fail,
               backend=types.SimpleNamespace(
                   close_instruments=lambda: closed.append(True)))
    with _NoDialogs() as dialogs:
        gui.start_measurement()
    assert closed == [True]
    assert [c[0] for c in dialogs.calls] == ["showerror"]   # Start: fine


def test_worker_traceback_reaches_the_runtime_error_log():
    def fail(*a):
        raise OSError("bus fell over")
    lakeshore = types.SimpleNamespace(get_temperature=fail)
    gui = _gui(backend=types.SimpleNamespace(
                   params={"start_temp": 100.0, "sensor": "A"},
                   lakeshore=lakeshore),
               is_stabilizing=True, is_running=False,
               stop_event=threading.Event(), data_queue=queue.Queue())
    gui._measurement_worker()
    err = gui.data_queue.get_nowait()
    assert isinstance(err, OSError)
    assert "Traceback" in err.pica_traceback
    assert "bus fell over" in err.pica_traceback

    banners = []
    gui.stop_measurement = lambda from_user=True: None
    gui._banner = lambda msg, *a, **k: banners.append(msg)
    with _NoDialogs() as dialogs:
        gui._handle_runtime_error(err)
    assert dialogs.calls == []
    assert any("bus fell over" in m and "Traceback" in m
               for m in gui.logged)
    assert banners and "bus fell over" in banners[0]


def test_end_of_run_events_never_open_a_dialog():
    banners = []
    gui = _gui(_update_live_plots=lambda *a, **k: None,
               stop_measurement=lambda from_user=True: None,
               _banner=lambda msg, *a, **k: banners.append(msg))
    with _NoDialogs() as dialogs:
        gui._handle_complete_event()
        gui._handle_cutoff_event()
    assert dialogs.calls == []
    assert len(banners) == 2

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
