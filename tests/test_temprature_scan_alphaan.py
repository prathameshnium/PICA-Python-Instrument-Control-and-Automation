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
from unittest.mock import patch

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
    gui.statuses = []                      # every status-line text, in order
    gui.lbl_status = types.SimpleNamespace(
        config=lambda **k: gui.statuses.append(k["text"]))
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


def _params(**over):
    p = {"sample_name": "S", "acv": 0.5, "mtm": 0.5, "delay": 0.0,
         "wire_mode": "2", "frequencies": [1000.0, 2000.0],
         "lcr_visa": "GPIB0::10::INSTR", "geometry_mode": "diameter",
         "diameter_mm": 9.68, "thickness_mm": 1.14,
         "temp_program": "Ramp 300 -> 400 K", "save_pica": True,
         "save_windeta": True}
    p.update(over)
    return p


def _files(tmp, **over):
    params = _params(**over)
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
    assert os.path.basename(path) == "S-1000Hz_windeta_format.txt"
    lines = raw.split(b"\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")          # CRLF only
    assert lines[1] == (b"Diameter[mm] 9.680e+00  Thickness[mm] 1.140e+00"
                        b"  Spacer Capacity[pF] 0.000e+00  Spacer "
                        b"Area[mm^2] 0.000e+00")
    assert lines[2] == (b"Fixed value(s) :  Freq. [Hz]=1.000e+03  "
                        b"AC Volt  [Vrms]=5.000e-01")
    assert lines[3] == (b" Temp. [\xb0C]\t Eps'    \t Eps''   \t"
                        b" Tan(Delta)\t Phi [Deg]")              # cp1252
    if os.path.exists(ref):
        # The reference has T / eps' / eps'' only; Tan(Delta) and Phi are
        # appended after it, spelt as in the GUI Fscan WinDETA file.
        ref_header = open(ref, "rb").read().split(b"\r\n")[3]
        assert lines[3].startswith(ref_header + b"\t")
    assert lines[0].startswith(b"S, ") and lines[4:] == [
        b"1.50000e+02\t1.30000e+01\t2.00000e+00\t1.53846e-01\t-8.12538e+01",
        b""]
    assert empty.count(b"\r\n") == 4      # the NaN kill row is PICA-only


def test_windeta_file_reads_back_tan_delta_and_phi_for_a_parallel_rc():
    """One point of a parallel RC (Rp || Cp), read back BY HEADER NAME:
    Tan(Delta) = 1/(w Rp Cp) and Phi is negative (capacitive)."""
    rp, cp, f = 1e6, 100e-12, 1000.0
    w = 2 * math.pi * f
    z = 1 / (1 / rp + 1j * w * cp)
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[f])
        c0 = gui.c0_farads
        gui._save_cycle_to_files({"points": [
            (300.0, f, z.real, z.imag, mod.STATUS_RESULT_VALID, f)]})
        raw = open(gui.windeta_filepaths[f], "rb").read()
    lines = raw.decode("cp1252").split("\r\n")
    names = [n.strip() for n in lines[3].split("\t")]
    assert names == ["Temp. [\u00b0C]", "Eps'", "Eps''", "Tan(Delta)",
                     "Phi [Deg]"]
    row = dict(zip(names, (float(v) for v in lines[4].split("\t"))))
    assert len(lines[4].split("\t")) == 5 and lines[5:] == [""]
    assert math.isclose(row["Temp. [\u00b0C]"], 300.0 - 273.15,
                        rel_tol=1e-5)
    assert math.isclose(row["Eps'"], cp / c0, rel_tol=1e-5)
    assert math.isclose(row["Eps''"], 1 / (w * rp * c0), rel_tol=1e-5)
    assert math.isclose(row["Tan(Delta)"], 1 / (w * rp * cp), rel_tol=1e-5)
    assert math.isclose(row["Tan(Delta)"], row["Eps''"] / row["Eps'"],
                        rel_tol=1e-4)
    assert row["Phi [Deg]"] < 0
    assert math.isclose(row["Phi [Deg]"],
                        -math.degrees(math.atan(w * rp * cp)), rel_tol=1e-5)


def test_area_mode_writes_the_equivalent_diameter():
    lines = mod.windeta_tscan_header_lines(
        _params(geometry_mode="area", area_mm2=math.pi * 25.0), 1e3,
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


def test_output_settings_are_checked_before_start():
    for bad in (_params(save_pica=False, save_windeta=False),
                _params(thickness_mm=0.0),
                _params(geometry_mode="area", area_mm2=-1.0)):
        try:
            mod.validate_output_settings(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    mod.validate_output_settings(_params(save_pica=False))


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


# --- review fixes (23 Oct 2026): wrong address, cut-short sweeps -------------

class _L350Session:
    """A recording VISA session behind Lakeshore350_Backend."""

    def __init__(self, idn):
        self.idn = idn
        self.traffic = []
        self.timeout = 0
        self.closed = False

    def write(self, cmd):
        self.traffic.append(("write", cmd))

    def query(self, cmd):
        self.traffic.append(("query", cmd))
        return self.idn if cmd == "*IDN?" else "0"

    def close(self):
        self.closed = True


def _start_with_lakeshore_idn(idn):
    """initialize_instruments() against a Lakeshore answering idn; the
    Alpha connect is stubbed to stop the run right after the Lakeshore."""
    session = _L350Session(idn)
    fake = FakeAlpha()
    combined = mod.Combined_Backend()
    combined.lcr = _bare_alpha(fake)

    def stop_here(addr):
        raise ConnectionError("stopped before the Alpha")
    combined.lcr.connect = stop_here
    rm = types.SimpleNamespace(open_resource=lambda addr: session)
    with patch.object(mod, "pyvisa",
                      types.SimpleNamespace(ResourceManager=lambda: rm)), \
            patch.object(mod.time, "sleep", lambda s: None):
        try:
            combined.initialize_instruments(
                _params(lakeshore_visa="GPIB0::12::INSTR"))
        except Exception as exc:
            return session, fake, combined, exc
    raise AssertionError("initialize_instruments must not complete")


def test_a_non_350_address_is_refused_before_any_write():
    for idn in ("LSCI,MODEL340,123456,061407",
                "ALPHA Analyzer, Novocontrol Technologies, x"):
        session, fake, combined, err = _start_with_lakeshore_idn(idn)
        assert isinstance(err, RuntimeError), err
        assert "not a Lake Shore Model 350" in str(err)
        assert session.traffic == [("query", "*IDN?")], session.traffic
        assert session.closed and combined.lakeshore is None
        # Start's error path closes everything: still nothing is sent.
        combined.close_instruments()
        assert session.traffic == [("query", "*IDN?")]
        assert fake.commands == []


def test_a_model_350_still_gets_its_reset_and_heater_setup():
    session, _, combined, err = _start_with_lakeshore_idn(
        "LSCI,MODEL350,LSA1234,1.0")
    assert str(err) == "stopped before the Alpha"
    writes = [c for k, c in session.traffic if k == "write"]
    assert writes == ["*RST", "*CLS", "HTRSET 1,1,2,0,1"]
    assert combined.lakeshore.is_model_350()


def test_scan_picks_the_350_by_model_never_a_340():
    idns = {
        "GPIB0::19::INSTR": "LSCI,MODEL340,222,1",
        "GPIB0::12::INSTR": "LSCI,MODEL350,1,1\r\n",
        "GPIB0::10::INSTR": "ALPHA Analyzer, Novocontrol Technologies, x",
    }

    class _Dev:
        def __init__(self, res):
            self.res = res

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def query(self, cmd):
            return idns[self.res]

    def scan(idns):
        rm = types.SimpleNamespace(list_resources=lambda: tuple(idns),
                                   open_resource=_Dev)
        combo = type("Combo", (dict,), {"set": lambda self, v:
                                        self.__setitem__("chosen", v)})
        logged = []
        gui = types.SimpleNamespace(log=logged.append, lakeshore_cb=combo(),
                                    lcr_cb=combo(), backend=None)
        with patch.object(mod, "pyvisa",
                          types.SimpleNamespace(ResourceManager=lambda: rm)), \
                patch.object(mod, "PYVISA_AVAILABLE", True):
            mod.Integrated_CT_GUI._scan_for_visa_instruments(gui)
        return gui, logged

    gui, _ = scan(idns)
    assert gui.lakeshore_cb["chosen"].startswith("GPIB0::12::INSTR")
    assert gui.lcr_cb["chosen"].startswith("GPIB0::10::INSTR")
    del idns["GPIB0::12::INSTR"]
    gui, logged = scan(idns)                   # only a 340 on the bus
    assert any("Start will refuse" in m for m in logged)


def test_a_refused_alpha_address_gets_nothing_at_close_either():
    fake = FakeAlpha({"*IDN?": "LSCI,MODEL350,LSA1234,1.0"})
    backend = _bare_alpha(fake)
    try:
        backend.connect("GPIB0::12::INSTR")
    except ConnectionError:
        pass
    else:
        raise AssertionError("a non-Alpha IDN must be refused")
    backend.close_instrument()                 # Start's error path
    assert fake.commands == ["*IDN?"], fake.commands


def test_points_measured_before_an_error_are_saved_ahead_of_it():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1000 2 1",
                               "ZRE=1E5 -2E6 2000 6 1"]})
    combined = _combined(fake, temps=(300.0, 301.0))
    try:
        combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    except RuntimeError as exc:
        err = exc
    else:
        raise AssertionError("status 6 must abort the sweep")
    assert [p[1] for p in err.partial_cycle["points"]] == [1000.0]

    def sweep(*a):
        raise err
    lakeshore = types.SimpleNamespace(setup_ramp=lambda *a: None,
                                      set_setpoint=lambda *a: None,
                                      set_heater_range=lambda *a: None)
    gui = _gui(backend=types.SimpleNamespace(
                   params={"delay": 0, "end_temp": 400.0, "rate": 1.0},
                   lakeshore=lakeshore, measure_frequency_sweep=sweep),
               is_stabilizing=False, is_running=True, start_time=None,
               stop_event=threading.Event(), data_queue=queue.Queue(),
               frequencies=[1000.0, 2000.0])
    gui._measurement_worker()
    order = []
    gui._process_cycle = lambda cycle, elapsed: order.append(
        ("cycle", len(cycle["points"])))
    gui._handle_runtime_error = lambda e: order.append(("error", e))
    gui._handle_log_message = lambda m: None
    gui._process_data_queue()
    assert order == [("cycle", 1), ("error", err)], order


def test_a_sweep_cut_short_by_stop_is_written_to_the_files():
    """The pump stops re-scheduling once Stop drops the run flags; the
    worker's last (partial) sweep arrives after that and must still be
    written, fsync'd, before the run ends."""
    valid = mod.STATUS_RESULT_VALID
    closed = []
    button = types.SimpleNamespace(config=lambda **k: None)
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp)
        before = {f: open(p).read() for f, p in gui.freq_filepaths.items()}
        gui.data_storage = {
            "time": [], "temperature": [],
            "cp": {f: {"T": [], "v": []} for f in gui.frequencies},
            "g": {f: {"T": [], "v": []} for f in gui.frequencies}}
        gui._update_live_plots = lambda *a, **k: None
        gui.data_queue = queue.Queue()
        gui.data_queue.put(("CYCLE", {
            "points": [(300.0, 1000.0, 1e5, -2e6, valid, 1000.0)],
            "heater": 5.0, "killed": False}, 12.0))
        gui.data_queue.put("COMPLETE")
        gui.start_button = gui.scan_button = button
        gui.backend = types.SimpleNamespace(
            close_instruments=lambda: closed.append(True))
        gui._close_after_stop = False
        synced = []
        real_fsync = os.fsync
        with _NoDialogs() as dialogs, patch.object(
                mod.os, "fsync",
                lambda fd: (synced.append(fd), real_fsync(fd))):
            gui._finalize_stop(False)
        after = {f: open(p).read() for f, p in gui.freq_filepaths.items()}
        windeta = open(gui.windeta_filepaths[1000.0], "rb").read()
    added = after[1000.0][len(before[1000.0]):].splitlines()
    assert len(added) == 1 and added[0].startswith("3.000000E+02\t")
    assert len(added[0].split("\t")) == 19
    assert after[2000.0] == before[2000.0]
    assert windeta.endswith(b"\r\n") and windeta.count(b"\r\n") == 5
    assert len(synced) == 2                       # PICA + WinDETA row
    assert gui.data_queue.empty() and closed == [True]
    assert dialogs.calls == []


def test_start_drops_what_a_late_worker_left_in_the_queue():
    # A worker that outlived Stop's drain left a partial CYCLE and an
    # exception behind; neither belongs to the next run.
    stale = queue.Queue()
    stale.put(("CYCLE", {"points": [], "heater": 0.0, "killed": False}, 1.0))
    stale.put(RuntimeError("late error from the previous run"))

    def fail():
        raise RuntimeError("stop the start here")
    gui = _gui(measurement_thread=None, data_queue=stale,
               _parse_frequencies=fail,
               backend=types.SimpleNamespace(close_instruments=lambda: None))
    with _NoDialogs():
        gui.start_measurement()
    assert gui.data_queue.empty()


def test_a_refused_lakeshore_is_dropped_even_if_its_close_raises():
    class _BadClose(_L350Session):
        def close(self):
            raise OSError("VI_ERROR_CONN_LOST")
    session = _BadClose("LSCI,MODEL340,123456,061407")
    fake = FakeAlpha()
    combined = mod.Combined_Backend()
    combined.lcr = _bare_alpha(fake)
    rm = types.SimpleNamespace(open_resource=lambda addr: session)
    with patch.object(mod, "pyvisa",
                      types.SimpleNamespace(ResourceManager=lambda: rm)):
        try:
            combined.initialize_instruments(
                _params(lakeshore_visa="GPIB0::19::INSTR"))
        except RuntimeError as exc:
            assert "not a Lake Shore Model 350" in str(exc)
        else:
            raise AssertionError("a Model 340 must be refused")
    assert combined.lakeshore is None
    combined.close_instruments()               # Start's error path
    assert session.traffic == [("query", "*IDN?")], session.traffic


# --- Tscan bug hunt (27 Oct 2026) --------------------------------------------

class _Event:
    """stop_event stand-in: Stop is 'pressed' during the first wait()."""

    def __init__(self, order=None):
        self.flag = False
        self.order = order if order is not None else []

    def is_set(self):
        return self.flag

    def wait(self, timeout=None):
        self.order.append(("wait", timeout))
        self.flag = True
        return True


def test_a_save_error_ends_the_run_instead_of_killing_the_pump():
    # A .dat locked by Excel used to escape the after() callback: the pump
    # died, COMPLETE was never read and the heater stayed on.
    def locked(cycle, elapsed):
        raise PermissionError("file is open in Excel")
    handled = []
    gui = _gui(data_queue=queue.Queue(), _process_cycle=locked,
               _handle_log_message=lambda m: None,
               _handle_complete_event=lambda: handled.append("complete"),
               _handle_runtime_error=lambda e: handled.append(e),
               is_running=True, is_stabilizing=False)
    gui.data_queue.put(("CYCLE", {"points": []}, 1.0))
    gui.data_queue.put("COMPLETE")
    gui._process_data_queue()
    assert len(handled) == 1 and isinstance(handled[0], PermissionError)
    assert "Traceback" in handled[0].pica_traceback
    assert gui.data_queue.get_nowait() == "COMPLETE"   # left for the drain


def test_a_plot_error_does_not_end_the_run():
    def broken(*a, **k):
        raise ValueError("matplotlib glitch")
    saved = []
    gui = _gui(_save_cycle_to_files=saved.append,
               _update_data_storage=lambda c, e: None,
               _update_live_plots=broken)
    cycle = {"points": [(300.0, 1e3, 1e5, -2e6,
                         mod.STATUS_RESULT_VALID, 1e3)], "heater": 1.0}
    gui._process_cycle(cycle, 1.0)
    assert saved == [cycle]
    assert any("matplotlib glitch" in m for m in gui.logged)


class _Field:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _start(tmp, **over):
    """start_measurement() on fake widgets; returns (gui, init calls,
    dialog calls). Every check must run before initialize_instruments."""
    fields = {"Sample Name": "S", "Start Temp": "300", "End Temp": "310",
              "Rate": "1", "Cutoff": "320", "AC Bias": "1", "MTM": "0.5",
              "Delay": "0", "Thickness": "1", "Geometry": "1"}
    fields.update({k: v for k, v in over.items() if k in fields})
    inits = []
    gui = _gui(measurement_thread=None,
               _parse_frequencies=lambda: [1000.0],
               entries={k: _Field(v) for k, v in fields.items()},
               wire_combobox=_Field("2"),
               lakeshore_cb=_Field("GPIB0::12::INSTR"),
               lcr_cb=_Field("GPIB0::10::INSTR"),
               geom_mode_var=_Field(over.get("mode", "area")),
               fmt_pica_var=_Field(True), fmt_windeta_var=_Field(True),
               file_location_path=over.get("folder", tmp),
               backend=types.SimpleNamespace(
                   initialize_instruments=inits.append,
                   close_instruments=lambda: None))
    gui._set_inputs_locked = lambda locked: gui.logged.append(
        f"locked={locked}")
    with _NoDialogs() as dialogs:
        gui.start_measurement()
    return gui, inits, dialogs.calls


def _start_error(**over):
    with tempfile.TemporaryDirectory() as tmp:
        gui, inits, calls = _start(tmp, **over)
        assert inits == [], f"{over} reached the instruments"
        assert [c[0] for c in calls] == ["showerror"], calls
        assert os.listdir(tmp) == []              # no orphan files
    return calls[0][1][1], gui


def test_bad_sample_name_or_folder_is_refused_before_any_instrument():
    for name in ("A/B", "S:1", "x?y"):
        msg, _ = _start_error(**{"Sample Name": name})
        assert "Sample Name" in msg and "file name" in msg, msg
    msg, _ = _start_error(**{"Sample Name": "SΩ"})
    assert "cp1252" in msg, msg
    msg, _ = _start_error(folder=os.path.join(tempfile.gettempdir(),
                                              "pica-no-such-dir-xyz"))
    assert "folder not found" in msg, msg


def test_number_errors_name_the_box_and_refuse_non_finite_values():
    for key, text, box in (("Rate", "30O", "Ramp Rate (K/min)"),
                           ("Delay", "inf", "Delay per Freq (s)"),
                           ("Delay", "nan", "Delay per Freq (s)"),
                           ("Thickness", "nan", "Sample Thickness (mm)"),
                           ("Geometry", "", "Electrode Area (mm²)"),
                           ("MTM", "0,5", "Integration Time (s)")):
        msg, _ = _start_error(**{key: text})
        assert box in msg, (key, text, msg)
    msg, _ = _start_error(Delay="1e7")
    assert "Delay per Freq" in msg and "3600" in msg, msg


def test_a_failed_start_shows_it_and_unlocks_the_inputs():
    _, gui = _start_error(Rate="x")
    assert gui.statuses[0].startswith("Status: STARTING")
    assert gui.statuses[-1] == "Status: START FAILED - see console"
    assert gui.logged.count("locked=False") == 1


def test_nan_or_inf_geometry_is_refused():
    for bad in (_params(thickness_mm=float("nan")),
                _params(thickness_mm=float("inf")),
                _params(diameter_mm=float("nan")),
                _params(geometry_mode="area", area_mm2=float("inf"))):
        try:
            mod.validate_output_settings(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    for fn in (mod.compute_c0, mod.compute_c0_from_area):
        for args in ((1.0, float("inf")), (float("nan"), 1.0)):
            try:
                fn(*args)
            except ValueError:
                continue
            raise AssertionError(f"{fn.__name__}{args} accepted")


def test_stop_during_the_stabilized_wait_leaves_the_run_stopped():
    lakeshore = types.SimpleNamespace(
        get_temperature=lambda s: 300.0, set_heater_range=lambda *a: None,
        set_setpoint=lambda *a: None,
        setup_ramp=lambda *a: (_ for _ in ()).throw(
            AssertionError("ramp armed after Stop")))
    gui = _gui(backend=types.SimpleNamespace(
                   params={"start_temp": 300.0}, lakeshore=lakeshore),
               is_stabilizing=False, is_running=False,
               stop_event=_Event(), data_queue=queue.Queue())
    gui.is_stabilizing = True       # Stop clears it while wait(5) runs
    real_wait = gui.stop_event.wait

    def wait(timeout=None):
        gui.is_stabilizing = gui.is_running = False   # stop_measurement
        return real_wait(timeout)
    gui.stop_event.wait = wait
    gui._measurement_worker()
    assert gui.is_running is False and gui.is_stabilizing is False


def test_stop_during_the_delay_measures_nothing_more():
    fake = FakeAlpha()
    combined = _combined(fake, temps=(300.0,))
    cycle = combined.measure_frequency_sweep([1000.0, 2000.0], 30.0,
                                             _Event())
    assert cycle["points"] == []
    assert "MST" not in fake.commands


def test_each_point_reads_t_after_the_delay():
    order = []
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 2 1"})
    combined = _combined(fake)
    read_t = combined.lakeshore.get_temperature
    combined.lakeshore.get_temperature = lambda *a: (
        order.append("T"), read_t(*a))[1]
    event = _Event(order)
    event.wait = lambda timeout=None: (order.append("wait"), False)[1]
    combined.measure_frequency_sweep([1000.0], 5.0, event)
    assert order == ["wait", "T"], order


def test_a_failed_kill_does_not_claim_the_heater_is_off():
    def fail(*a):
        raise OSError("bus down")
    combined = mod.Combined_Backend()
    combined.lakeshore = types.SimpleNamespace(set_heater_range=fail)
    out = []
    with patch.object(mod.time, "sleep", lambda s: None), \
            patch("builtins.print", lambda *a, **k: out.append(
                " ".join(map(str, a)))):
        assert combined.check_safety_kill(400.0) is True
    kill = [line for line in out if "SAFETY KILL" in line]
    assert kill and "KILL FAILED" in kill[0] and "Heater OFF" not in kill[0]


def test_run_inputs_are_locked_and_restored():
    states = []
    widget = types.SimpleNamespace(state=states.append)
    gui = _gui(_run_locked_widgets=[widget])
    gui._set_inputs_locked(True)
    gui._set_inputs_locked(False)
    assert states == [["disabled"], ["!disabled"]]
    src = open(PATH, encoding="utf-8").read()
    start = src[src.index("    def start_measurement"):
                src.index("    def _on_geom_mode_change")]
    assert "self._set_inputs_locked(True)" in start
    for var in ("self.file_button", "self.freq_text", "fmt_checks",
                "geom_radios", "self.lcr_cb"):
        assert var in src[src.index("self._run_locked_widgets = ("):][:400]


def test_status_line_keeps_how_the_run_ended():
    button = types.SimpleNamespace(config=lambda **k: None)
    gui = _gui(_update_live_plots=lambda *a, **k: None,
               stop_measurement=lambda from_user=True: None,
               _banner=lambda msg: None, start_button=button,
               scan_button=button, _close_after_stop=False,
               backend=types.SimpleNamespace(close_instruments=lambda: None),
               _drain_queue_at_stop=lambda: None)
    for handler, word in ((gui._handle_complete_event, "COMPLETE"),
                          (gui._handle_cutoff_event, "CUTOFF"),
                          (lambda: gui._handle_runtime_error(
                              OSError("x")), "ERROR")):
        handler()
        gui._finalize_stop(False)          # end of run: status kept
        assert gui.statuses[-1].startswith(f"Status: {word}"), gui.statuses
    with _NoDialogs():
        gui._finalize_stop(True)
    assert gui.statuses[-1].startswith("Status: STOPPED")


def test_auto_scale_stays_linear_when_a_point_is_not_positive():
    calls = []
    ax = types.SimpleNamespace(
        set_yscale=calls.append, relim=lambda: None,
        set_autoscaley_on=lambda on: None,
        autoscale_view=lambda **k: None, set_ylim=lambda *a: None)
    gui = _gui(y_scale_var=_Field("auto"), _decade_ylims={})
    assert gui._apply_y_scale(ax, [1e-12, 1e-9, 1e-6], "cp") is True
    assert gui._apply_y_scale(ax, [1e-12, 1e-9, -1e-12], "cp") is False
    assert calls[-1] == "linear"


def test_the_first_point_of_a_run_is_on_screen():
    xlims = []
    gui = _gui(plot_freq=1e3, _apply_y_scale=lambda *a: None,
               ax_main=types.SimpleNamespace(
                   set_xlim=lambda lo, hi: xlims.append((lo, hi))),
               data_storage={"cp": {1e3: {"T": [80.0], "v": [1e-12]}}})
    gui._rescale_main_axis()
    assert xlims and xlims[-1][0] < 80.0 < xlims[-1][1]


def test_panel_scrolls_with_the_wheel_and_labels_match_the_panel():
    src = open(PATH, encoding="utf-8").read()
    assert 'canvas.bind_all("<MouseWheel>", _wheel)' in src
    assert 'option_add("*Label.background", self.CLR_BG_DARK)' in src
    assert "style.configure('TRadiobutton'" in src
    assert "style='Graph.TRadiobutton'" in src


# --- connect/initialise on the worker (27 Oct 2026) --------------------------

class _Recorder:
    """A widget stand-in that records every config() call."""

    def __init__(self):
        self.calls = []

    def config(self, **k):
        self.calls.append(k)

    def set_data(self, *a):
        pass

    def draw(self):
        pass


class _FakeThread:
    """threading.Thread stand-in: records the target, never runs it."""
    made = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args
        self.started = False
        _FakeThread.made.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return False


def test_start_on_the_tk_thread_never_connects():
    _FakeThread.made = []
    with tempfile.TemporaryDirectory() as tmp:
        fields = {"Sample Name": "S", "Start Temp": "300", "End Temp": "310",
                  "Rate": "1", "Cutoff": "320", "AC Bias": "1", "MTM": "0.5",
                  "Delay": "0", "Thickness": "1", "Geometry": "1"}
        afters = []
        inits = []
        gui = _gui(measurement_thread=None, data_queue=queue.Queue(),
                   _parse_frequencies=lambda: [1000.0],
                   entries={k: _Field(v) for k, v in fields.items()},
                   wire_combobox=_Field("2"),
                   lakeshore_cb=_Field("GPIB0::12::INSTR"),
                   lcr_cb=_Field("GPIB0::10::INSTR"),
                   geom_mode_var=_Field("area"),
                   fmt_pica_var=_Field(True), fmt_windeta_var=_Field(True),
                   file_location_path=tmp,
                   backend=types.SimpleNamespace(
                       initialize_instruments=lambda *a: inits.append(a),
                       close_instruments=lambda: None),
                   data_storage={}, _decade_ylims={},
                   line_main=_Recorder(), line_sub1=_Recorder(),
                   line_sub2=_Recorder(), canvas=_Recorder(),
                   start_button=_Recorder(), stop_button=_Recorder(),
                   scan_button=_Recorder(),
                   stop_event=threading.Event(),
                   _files_ready=threading.Event(),
                   root=types.SimpleNamespace(
                       after=lambda ms, fn: afters.append(fn)))
        gui._set_inputs_locked = lambda locked: gui.logged.append(
            f"locked={locked}")
        with _NoDialogs() as dialogs, \
                patch.object(mod.threading, "Thread", _FakeThread):
            gui.start_measurement()
        files = os.listdir(tmp)
    assert inits == [], "Start must not connect on the Tk thread"
    assert files == [], "files are created only after CONNECTED"
    assert dialogs.calls == []
    (thread,) = _FakeThread.made
    assert thread.started and thread.target == gui._run_worker
    assert thread.args[0]["lcr_visa"] == "GPIB0::10::INSTR"
    assert gui.statuses[-1].startswith("Status: CONNECTING")
    assert "locked=True" in gui.logged
    assert gui.is_stabilizing and not gui.is_running
    assert gui.start_button.calls[-1] == {"state": "disabled"}
    assert gui.stop_button.calls[-1] == {"state": "normal"}
    assert afters == [gui._process_data_queue]


def test_stop_during_the_alpha_reset_wait_ends_the_worker_quickly():
    from alphaan_port_test_support import ALPHA_IDN, FakeVisaError
    session = _L350Session("LSCI,MODEL350,LSA1234,1.0")
    fake = FakeAlpha({"*IDN?": [ALPHA_IDN] + [
        FakeVisaError(mod.VI_ERROR_TMO)] * 100000})
    combined = mod.Combined_Backend()
    combined.lcr = _bare_alpha(fake)
    rm = types.SimpleNamespace(open_resource=lambda addr: session)
    gui = _gui(backend=combined, stop_event=threading.Event(),
               _files_ready=threading.Event(), data_queue=queue.Queue())
    params = _params(lakeshore_visa="GPIB0::12::INSTR", start_temp=300.0,
                     end_temp=310.0, rate=1.0)
    with patch.object(mod, "pyvisa",
                      types.SimpleNamespace(ResourceManager=lambda: rm)), \
            patch.object(mod.time, "sleep", lambda s: None), \
            patch("builtins.print", lambda *a, **k: None):
        worker = threading.Thread(target=gui._run_worker, args=(params,),
                                  daemon=True)
        worker.start()
        deadline = time.monotonic() + 10.0
        while (fake.commands.count("*IDN?") < 3
               and time.monotonic() < deadline):
            threading.Event().wait(0.01)   # time.sleep is patched out
        assert "*RST" in fake.commands, "never reached the *RST wait"
        stopped_at = time.monotonic()
        gui.stop_event.set()                      # Stop pressed
        worker.join(5.0)
        took = time.monotonic() - stopped_at
        assert not worker.is_alive() and took < 2.0, took
        writes = [c for k, c in session.traffic if k == "write"]
        assert writes == ["*RST", "*CLS", "HTRSET 1,1,2,0,1"], writes
        assert "MODE=IMP" not in fake.commands
        msgs = []
        while not gui.data_queue.empty():
            msgs.append(gui.data_queue.get_nowait())
        assert all(isinstance(m, str) for m in msgs), msgs
        assert any("Stopped while connecting" in m for m in msgs), msgs
        # The normal stop path then closes what was opened, safely. The
        # Alpha is still away after its *RST, so it gets nothing more.
        sent = len(fake.commands)
        combined.close_instruments()
    writes = [c for k, c in session.traffic if k == "write"]
    assert writes[-1] == "RANGE 1,0" and session.closed
    assert not any(w.startswith(("RAMP", "SETP", "RANGE 1,5"))
                   for w in writes), writes
    assert fake.commands[sent:] == [] and fake.closed


def test_a_connect_failure_resets_the_ui_and_shows_error():
    def fail(params, stop_event=None):
        raise ConnectionError("The Alpha did not answer")
    closed, afters = [], []
    buttons = {n: _Recorder() for n in ("start", "stop", "scan")}
    gui = _gui(backend=types.SimpleNamespace(
                   initialize_instruments=fail, params={},
                   close_instruments=lambda: closed.append(True)),
               stop_event=threading.Event(), _files_ready=threading.Event(),
               data_queue=queue.Queue(), is_running=False,
               is_stabilizing=True, _stopping=False,
               _close_after_stop=False, frequencies=[1000.0],
               start_button=buttons["start"], stop_button=buttons["stop"],
               scan_button=buttons["scan"],
               _banner=lambda msg, *a, **k: None,
               root=types.SimpleNamespace(
                   after=lambda ms, fn: afters.append(fn)))
    gui._set_inputs_locked = lambda locked: gui.logged.append(
        f"locked={locked}")
    gui.measurement_thread = threading.Thread(
        target=gui._run_worker, args=(_params(),), daemon=True)
    gui.measurement_thread.start()
    gui.measurement_thread.join(5.0)
    assert not gui.measurement_thread.is_alive()
    with _NoDialogs() as dialogs:
        gui._process_data_queue()
    assert any(s.startswith("Status: ERROR") and "did not answer" in s
               for s in gui.statuses), gui.statuses
    assert gui.statuses[-1].startswith("Status: ERROR")
    assert any("ERROR during startup" in m and "Traceback" in m
               for m in gui.logged)
    assert "locked=False" in gui.logged and closed == [True]
    assert buttons["start"].calls[-1] == {"state": "normal"}
    assert not (gui.is_running or gui.is_stabilizing)
    assert gui.stop_event.is_set() and not gui._files_ready.is_set()
    assert [c[0] for c in dialogs.calls] == ["showerror"]   # at Start
    assert "did not answer" in dialogs.calls[0][1][1]
    assert afters == [], "the pump must not keep running"


def test_stop_before_connected_is_handled_creates_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _gui(stop_event=threading.Event(), is_stabilizing=False,
                   _files_ready=threading.Event(), file_location_path=tmp)
        gui.stop_event.set()
        gui._on_connected()
        assert os.listdir(tmp) == []
    assert not gui._files_ready.is_set()


def test_the_worker_waits_for_the_files_before_the_heater():
    order = []
    gui = _gui(backend=types.SimpleNamespace(
                   initialize_instruments=lambda p, ev: order.append("init")),
               stop_event=threading.Event(), _files_ready=threading.Event(),
               data_queue=queue.Queue())
    gui._measurement_worker = lambda: order.append("run")
    worker = threading.Thread(target=gui._run_worker, args=({},),
                              daemon=True)
    worker.start()
    assert gui.data_queue.get(timeout=5.0) == "CONNECTED"
    assert order == ["init"]                  # no run before the files
    gui._files_ready.set()
    worker.join(5.0)
    assert order == ["init", "run"] and not worker.is_alive()



class _SilentAfterReset(FakeAlpha):
    """An Alpha that stops answering once *RST is written (it is away
    reinitializing). Every later write/query waits a little and then times
    out, the way 64-bit Keysight VISA does - only much shorter."""

    def __init__(self):
        super().__init__()
        self.reset_seen = False

    def _away(self):
        time.sleep(0.05)
        exc = Exception("VI_ERROR_TMO: Timeout expired")
        exc.error_code = mod.VI_ERROR_TMO
        raise exc

    def write(self, command):
        super().write(command)
        if self.reset_seen:
            self._away()
        if command == "*RST":
            self.reset_seen = True

    def query(self, command):
        if self.reset_seen:
            self.traffic.append(("query", command))
            self._away()
        return super().query(command)

def test_stop_during_the_reset_rejoin_closes_without_shutdown_commands():
    # Stop in the settle wait (no *IDN? sent yet) and in the *IDN? retry
    # loop (settle shortened to 0) must both leave the Alpha alone at close.
    for settle in (mod.RESET_SETTLE_S, 0.0):
        fake = _SilentAfterReset()
        backend, _ = connected_backend(mod, fake)
        backend.stop_event = threading.Event()
        threading.Timer(0.2, backend.stop_event.set).start()
        with patch.object(mod, "RESET_SETTLE_S", settle):
            try:
                backend._reset_and_set_impedance_mode()
            except mod.StopRequested:
                pass
            else:
                raise AssertionError("Stop must end the rejoin wait")
        assert backend._alpha_rebooting
        sent = len(fake.commands)
        combined = mod.Combined_Backend()
        combined.lcr = backend
        combined.lakeshore = None
        started = time.monotonic()
        combined.close_instruments()      # runs on the Tk thread in the GUI
        assert time.monotonic() - started < 0.5
        assert fake.commands[sent:] == [], fake.commands[sent:]
        assert "*RST" in fake.commands and "MODE=IMP" not in fake.commands
        assert fake.closed and backend.instrument is None
        assert not backend._alpha_rebooting


def test_a_completed_reset_rejoin_keeps_the_normal_shutdown():
    backend, fake = connected_backend(mod)
    with patch.object(mod, "RESET_SETTLE_S", 0.0),             patch.object(mod.time, "sleep", lambda s: None):
        backend._reset_and_set_impedance_mode()
        assert not backend._alpha_rebooting
        backend.close_instrument()
    tail = fake.commands[fake.commands.index("MODE=IMP") + 1:]
    assert tail == ["ACV=0", "ZCONSPL=0", "*RST"], tail
    assert fake.closed


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
