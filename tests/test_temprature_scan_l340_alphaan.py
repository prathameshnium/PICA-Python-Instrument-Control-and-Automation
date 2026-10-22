"""Tests for novocontrol/Temprature_Scan_L340_AlphaAN_GUI.py (Alpha-AN + L340).

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

mod, PATH = load_port("Temprature_Scan_L340_AlphaAN_GUI.py")


class FakeLakeshore340(FakeLakeshore):
    """FakeLakeshore plus the Model 340 calls the combined backend makes."""

    MAX_CURRENT_CODES = {3: "1.0 A"}
    idn = "LSCI,MODEL340,123456,061407"

    def __init__(self, temps=(300.0,), rdgst=0, log=None):
        super().__init__(temps)
        self.rdgst = rdgst
        self.log = log if log is not None else []

    def get_reading_status(self, sensor):
        return self.rdgst, ("ok" if not self.rdgst else "invalid")

    def get_heater_status(self):
        return 0, "no error"

    def heater_off(self):
        self.calls.append(("heater_off", ()))

    def is_model_340(self):
        return True

    def prepare_loop(self, sensor):
        self.log.append("lakeshore.prepare_loop")
        return ({"input": sensor, "enabled": True}, 1,
                {"sp_limit": 475.0, "max_current": 3, "max_range": 5})

    def range_code(self, name):
        return {"off": 0, "low": 2, "medium": 4, "high": 5}[name]


def _combined(fake_alpha=None, temps=(300.0,), rdgst=0):
    combined = mod.Combined_Backend()
    combined.lcr, _ = connected_backend(mod, fake_alpha)
    combined.lakeshore = FakeLakeshore340(temps, rdgst)
    combined.params = {"sensor": "A"}
    return combined


def _params(**over):
    p = {"lakeshore_visa": "GPIB0::19::INSTR",
         "lcr_visa": "GPIB0::10::INSTR", "frequencies": [1e3, 1e4],
         "acv": 1.0, "mtm": 0.5, "wire_mode": "2", "sensor": "A",
         "start_temp": 100.0, "end_temp": 300.0}
    p.update(over)
    return p


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
    # RDGST? is still bound to every point, as in the E4980A source.
    assert cycle["tstatus"] == [(0, "ok"), (0, "ok")]
    assert cycle["last_valid_temp"] == 301.0


def test_a_flagged_reading_is_never_the_last_valid_temperature():
    combined = _combined(temps=(300.0,), rdgst=4)
    cycle = combined.measure_frequency_sweep([1000.0], 0)
    assert cycle["last_valid_temp"] is None
    assert cycle["tstatus"] == [(4, "invalid")]


def test_a_disconnected_signal_source_aborts_the_sweep():
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 6 1"})
    combined = _combined(fake)
    try:
        combined.measure_frequency_sweep([1000.0], 0)
    except RuntimeError as exc:
        assert "status 6" in str(exc)
    else:
        raise AssertionError("status 6 must abort the sweep")


def test_the_safety_kill_turns_the_340_heater_off_before_measuring():
    fake = FakeAlpha()
    combined = _combined(fake, temps=(351.0,))
    cycle = combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    assert cycle["killed"] and len(cycle["points"]) == 1
    point = cycle["points"][0]
    assert len(point) == 6 and math.isnan(point[2])
    assert ("heater_off", ()) in combined.lakeshore.calls
    assert "MST" not in fake.commands


def test_settings_are_validated_before_either_instrument_is_opened():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    try:
        combined.initialize_instruments(_params(frequencies=[5e6], acv=3.0))
    except ValueError as exc:
        assert "Vrms" in str(exc)
    else:
        raise AssertionError("3 Vrms at 5 MHz is over the ZG4 ceiling")
    assert fake.commands == [] and combined.lakeshore is None


def test_start_prepares_the_340_then_connects_and_configures_the_alpha():
    log = []
    original_cls = mod.Lakeshore340_Backend
    original_interval = mod.RESET_RETRY_INTERVAL_S
    mod.Lakeshore340_Backend = lambda visa: FakeLakeshore340(log=log)
    mod.RESET_RETRY_INTERVAL_S = 0
    try:
        combined = mod.Combined_Backend()
        combined.lcr, fake = connected_backend(mod)
        fake.query = (lambda q, _orig=fake.query:
                      (log.append(q), _orig(q))[1])
        combined.initialize_instruments(_params(wire_mode="4"))
    finally:
        mod.Lakeshore340_Backend = original_cls
        mod.RESET_RETRY_INTERVAL_S = original_interval
    assert log[0] == "lakeshore.prepare_loop"      # 340 first, as before
    assert combined.lcr.rm.opened == ["GPIB0::10::INSTR"]
    cmds = fake.commands
    for expected in ("INTTYP?", "*RST", "MODE=IMP", "FRS=4", "ACV=1",
                     "MTM=0.5"):
        assert expected in cmds, (expected, cmds)
    assert not any(c.startswith(("DCV", "DCE")) for c in cmds)


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


class _ScanDevice:
    def __init__(self, idn, seen):
        self.idn, self.seen = idn, seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, cmd):
        self.seen.append((self.read_termination, self.write_termination,
                          self.send_end))
        return self.idn


def test_scan_picks_the_340_at_19_and_the_alpha_with_eoi_framing():
    idns = {
        "GPIB0::12::INSTR": "LSCI,MODEL350,1,1",
        "GPIB0::7::INSTR": "LSCI,MODEL340,111,1",
        "GPIB0::19::INSTR": "LSCI,MODEL340,222,1\r\n",
        "GPIB0::10::INSTR": "ALPHA Analyzer, Novocontrol Technologies, x",
    }
    seen = []
    rm = types.SimpleNamespace(
        list_resources=lambda: tuple(idns),
        open_resource=lambda res: _ScanDevice(idns[res], seen))
    combo = type("Combo", (dict,), {"set": lambda self, v:
                                    self.__setitem__("chosen", v)})
    gui = types.SimpleNamespace(log=lambda m: None, lakeshore_cb=combo(),
                                lcr_cb=combo(), backend=None)
    old = mod.pyvisa, mod.PYVISA_AVAILABLE
    mod.pyvisa = types.SimpleNamespace(ResourceManager=lambda: rm)
    mod.PYVISA_AVAILABLE = True
    try:
        mod.Integrated_CT_GUI._scan_for_visa_instruments(gui)
    finally:
        mod.pyvisa, mod.PYVISA_AVAILABLE = old
    assert gui.lakeshore_cb["chosen"].startswith("GPIB0::19::INSTR")
    assert gui.lcr_cb["chosen"].startswith("GPIB0::10::INSTR")
    assert set(seen) == {("", "", True)}


def test_gui_drops_the_e4980a_only_fields():
    source = open(PATH, encoding="utf-8").read()
    for gone in ("DC Bias", "aper_combobox", "var_alc", "var_corr",
                 "cable_len", "perform_measurement", "LCR_Backend"):
        assert gone not in source, gone
    assert "Alpha-AN & L340" in source


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
        paths = {f: os.path.join(tmp,
                                 f"S-{mod.freq_tag(f)}Hz_pica_format.dat")
                 for f in (1000.0, 2000.0, 3000.0)}
        for p in paths.values():
            open(p, "w").close()
        storage = {
            "time": [], "temperature": [],
            "cp": {f: {"T": [], "v": []} for f in paths},
            "g": {f: {"T": [], "v": []} for f in paths},
        }
        gui = _gui(freq_filepaths=paths, windeta_filepaths={},
                   c0_farads=mod.compute_c0_from_area(1.0, 1.0),
                   data_storage=storage,
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


def _out_params(**over):
    """Start-time params as the output-format code reads them."""
    p = {"sample_name": "S", "acv": 0.5, "mtm": 0.5, "delay": 0.0,
         "wire_mode": "2", "frequencies": [1000.0, 2000.0],
         "lcr_visa": "GPIB0::10::INSTR", "geometry_mode": "diameter",
         "diameter_mm": 9.68, "thickness_mm": 1.14,
         "temp_program": "Ramp 300 -> 400 K", "save_pica": True,
         "save_windeta": True}
    p.update(over)
    return p


def _files(tmp, **over):
    params = _out_params(**over)
    gui = _gui(file_location_path=tmp, frequencies=params["frequencies"],
               c0_farads=mod.c0_from_params(params))
    gui._create_per_frequency_files(params)
    return gui


def test_per_frequency_files_do_not_collide_below_1_hz():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[0.1, 0.5, 1e6], save_windeta=False)
        names = sorted(os.path.basename(p)
                       for p in gui.freq_filepaths.values())
    assert names == ["S-0.1Hz_pica_format.dat", "S-0.5Hz_pica_format.dat",
                     "S-1000000Hz_pica_format.dat"]
    assert gui.windeta_filepaths == {}       # unticked format: no files


def test_area_is_in_mm2_and_eps_matches_an_ideal_capacitor():
    c0 = mod.compute_c0_from_area(100.0, 1.0)
    assert math.isclose(c0, mod.EPS0_SI * 100e-6 / 1e-3)
    assert math.isclose(mod.compute_c0(10.0, 1.0),
                        mod.compute_c0_from_area(math.pi * 25.0, 1.0))
    # eps_r = 12 with a loss of 0.5: Y = j*w*C0*(12 - 0.5j)
    f = 1000.0
    y = 1j * 2 * math.pi * f * c0 * (12 - 0.5j)
    z = 1 / y
    eps1, eps2 = mod.calculate_eps(f, z.real, z.imag, c0)
    assert math.isclose(eps1, 12.0) and math.isclose(eps2, 0.5)


def test_windeta_files_follow_the_sample_tscan_layout():
    ref = os.path.join(os.path.dirname(PATH), "data_file_for_ref",
                       "Sample_Tscan.TXT")
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp)
        c0 = gui.c0_farads
        y = 1j * 2 * math.pi * 1000.0 * c0 * (13 - 2j)
        z = 1 / y
        valid = mod.STATUS_RESULT_VALID
        gui._save_cycle_to_files({"points": [
            (423.15, 1000.0, z.real, z.imag, valid, 1000.0),
            (424.0, 2000.0, float("nan"), float("nan"), -1, 2000.0)]})
        path = gui.windeta_filepaths[1000.0]
        raw = open(path, "rb").read()
        empty = open(gui.windeta_filepaths[2000.0], "rb").read()
        kill = open(gui.freq_filepaths[2000.0], encoding="utf-8").read()
    assert os.path.basename(path) == "S-1000Hz_windeta_format.txt"
    lines = raw.split(b"\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")          # CRLF only
    assert lines[1] == (b"Diameter[mm] 9.680e+00  Thickness[mm] 1.140e+00"
                        b"  Spacer Capacity[pF] 0.000e+00  Spacer "
                        b"Area[mm^2] 0.000e+00")
    assert lines[2] == (b"Fixed value(s) :  Freq. [Hz]=1.000e+03  "
                        b"AC Volt  [Vrms]=5.000e-01")
    assert lines[3] == b" Temp. [\xb0C]\t Eps'    \t Eps''   "   # cp1252
    if os.path.exists(ref):
        assert lines[3] == open(ref, "rb").read().split(b"\r\n")[3]
    assert lines[0].startswith(b"S, ") and lines[4:] == [
        b"1.50000e+02\t1.30000e+01\t2.00000e+00", b""]
    assert empty.count(b"\r\n") == 4      # the NaN kill row is PICA-only
    assert kill.splitlines()[-1].startswith("4.240000E+02\t")  # kept


def test_area_mode_writes_the_equivalent_diameter():
    lines = mod.windeta_tscan_header_lines(
        _out_params(geometry_mode="area", area_mm2=math.pi * 25.0), 1e3,
        mod.datetime(2026, 1, 15, 11, 35))
    assert lines[0] == "S, 15.01.2026, 11:35"
    assert lines[1].startswith("Diameter[mm] 1.000e+01  ")


def test_pica_files_start_with_ascii_metadata_rows():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, geometry_mode="area", area_mm2=50.0)
        text = open(gui.freq_filepaths[2000.0], encoding="utf-8").read()
    lines = text.splitlines()
    meta = [ln for ln in lines if ln.startswith("#")]
    assert lines[len(meta)] == mod.Integrated_CT_GUI.DATA_HEADER
    assert all(ln.isascii() for ln in meta)
    joined = "\n".join(meta)
    for want in ("Fixed frequency: 2000 Hz", "A = 50.0 mm^2",
                 f"C0 = {gui.c0_farads:.5e} F", "Evaluation : Area electrode",
                 "ZREFMODE=-3", "ZSLCAL=1", "ZLLCOR=1", "DRS=0 0",
                 "Ramp 300 -> 400 K", "GPIB0::10::INSTR"):
        assert want in joined, want


def test_start_describes_the_340_ramp_and_checks_output_before_hardware():
    """Start builds temp_program from the 340 ramp settings and refuses a
    bad geometry / no-format choice before any instrument is opened."""
    source = open(PATH, encoding="utf-8").read()
    start = source.index("def start_measurement")
    body = source[start:source.index("def _on_geom_mode_change")]
    assert "Lakeshore 340 at" in body and "control input" in body
    assert (body.index("validate_output_settings(params)")
            < body.index("c0_from_params(params)")
            < body.index("initialize_instruments(params)"))


def test_output_settings_are_checked_before_start():
    for bad in (_out_params(save_pica=False, save_windeta=False),
                _out_params(thickness_mm=0.0),
                _out_params(geometry_mode="area", area_mm2=-1.0)):
        try:
            mod.validate_output_settings(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    mod.validate_output_settings(_out_params(save_pica=False))


def test_geometry_toggle_keeps_a_value_per_mode():
    class _Entry:
        def __init__(self, text):
            self.text = text

        def get(self):
            return self.text

        def delete(self, *a):
            self.text = ""

        def insert(self, _i, text):
            self.text += text
    labels = []
    mode = types.SimpleNamespace(value="area")
    gui = _gui(entries={"Geometry": _Entry("78.5"),
                        "Thickness": _Entry("1.0")},
               _geom_values={"area": "1.0", "diameter": "1.0"},
               geom_mode_var=types.SimpleNamespace(get=lambda: mode.value),
               lbl_geom=types.SimpleNamespace(
                   config=lambda **k: labels.append(k["text"])),
               lbl_c0=types.SimpleNamespace(config=lambda **k: None))
    mode.value = "diameter"
    gui._on_geom_mode_change()
    assert gui.entries["Geometry"].get() == "1.0"
    gui.entries["Geometry"].text = "10"
    mode.value = "area"
    gui._on_geom_mode_change()
    assert gui.entries["Geometry"].get() == "78.5"
    assert gui._geom_values["diameter"] == "10"
    assert labels == ["Electrode Diameter (mm):", "Electrode Area (mm²):"]


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
