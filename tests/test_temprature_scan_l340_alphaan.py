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
from unittest.mock import patch

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
    gui.status = []
    gui.status_var = types.SimpleNamespace(set=gui.status.append)
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
    assert kill.splitlines()[-1].startswith("4.240000E+02\t")  # kept


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
            < body.index("validate_save_location(")
            < body.index("c0_from_params(params)")
            < body.index("target=self._run_worker"))
    # Connect/initialise run on the worker, never on the Tk thread.
    assert "initialize_instruments" not in body


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
    # The banner never keeps saying 'Running.' (or the last run's end).
    assert gui.status == ["Starting...", "Start failed: MTM= refused"]


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

class _L340Session:
    """A recording VISA session behind Lakeshore340_Backend."""

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


def test_a_non_340_address_is_refused_before_any_write():
    session = _L340Session("LSCI,MODEL350,LSA1234,1.0")
    fake = FakeAlpha()
    combined = mod.Combined_Backend()
    combined.lcr = _bare_alpha(fake)
    rm = types.SimpleNamespace(open_resource=lambda addr: session)
    with patch.object(mod, "pyvisa",
                      types.SimpleNamespace(ResourceManager=lambda: rm)):
        try:
            combined.initialize_instruments(_params())
        except RuntimeError as exc:
            assert "not a Lake Shore Model 340" in str(exc)
        else:
            raise AssertionError("a Model 350 must be refused")
    combined.close_instruments()               # Start's error path
    assert session.traffic == [("query", "*IDN?")], session.traffic
    assert session.closed and combined.lakeshore is None
    assert fake.commands == []


def test_a_refused_alpha_address_gets_nothing_at_close_either():
    fake = FakeAlpha({"*IDN?": "LSCI,MODEL340,123456,061407"})
    backend = _bare_alpha(fake)
    try:
        backend.connect("GPIB0::19::INSTR")
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
    partial = err.partial_cycle
    assert [p[1] for p in partial["points"]] == [1000.0]
    assert partial["tstatus"] == [(0, "ok")]
    assert partial["last_valid_temp"] == 300.0

    def sweep(*a):
        raise err
    gui = _gui(backend=types.SimpleNamespace(
                   params={"delay": 0, "end_temp": 400.0, "rate": 1.0,
                           "sensor": "A"},
                   lakeshore=FakeLakeshore340(temps=(300.0,)),
                   measure_frequency_sweep=sweep),
               is_stabilizing=False, is_running=True, start_time=None,
               stop_event=threading.Event(), data_queue=queue.Queue(),
               frequencies=[1000.0, 2000.0])
    gui.backend.lakeshore.start_ramp = lambda *a: None
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
            "tstatus": [(0, "ok")], "heater": 5.0, "killed": False}, 12.0))
        gui.data_queue.put("BANNER:heater error")
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
    assert "heater error" in gui.logged
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
    class _BadClose(_L340Session):
        def close(self):
            raise OSError("VI_ERROR_CONN_LOST")
    session = _BadClose("LSCI,MODEL350,LSA1234,1.0")
    fake = FakeAlpha()
    combined = mod.Combined_Backend()
    combined.lcr = _bare_alpha(fake)
    rm = types.SimpleNamespace(open_resource=lambda addr: session)
    with patch.object(mod, "pyvisa",
                      types.SimpleNamespace(ResourceManager=lambda: rm)):
        try:
            combined.initialize_instruments(_params())
        except RuntimeError as exc:
            assert "not a Lake Shore Model 340" in str(exc)
        else:
            raise AssertionError("a Model 350 must be refused")
    assert combined.lakeshore is None
    combined.close_instruments()               # Start's error path
    assert session.traffic == [("query", "*IDN?")], session.traffic


# --- sibling bug hunt (27 Oct 2026) ------------------------------------------

class _Box:
    """Entry / combobox / variable stand-in: get() returns a fixed value."""

    def __init__(self, value):
        self.value = value

    def get(self, *a):
        return self.value


def _start_gui(folder, freqs="1000, 2000", **texts):
    values = {"Sample Name": "S", "Start Temp": "100", "End Temp": "300",
              "Rate": "1", "Cutoff": "350", "AC Bias": "1.0", "MTM": "0.5",
              "Delay": "0", "Geometry": "1.0", "Thickness": "1.0"}
    values.update(texts)
    inited = []
    gui = _gui(measurement_thread=None,
               entries={k: _Box(v) for k, v in values.items()},
               freq_text=_Box(freqs), wire_combobox=_Box("2"),
               sensor_combobox=_Box("A"),
               lakeshore_cb=_Box("GPIB0::19::INSTR"),
               lcr_cb=_Box("GPIB0::10::INSTR"), geom_mode_var=_Box("area"),
               fmt_pica_var=_Box(True), fmt_windeta_var=_Box(True),
               file_location_path=folder,
               backend=types.SimpleNamespace(
                   initialize_instruments=inited.append,
                   close_instruments=lambda: None))
    return gui, inited


def test_bad_inputs_are_refused_by_box_name_before_any_hardware():
    with tempfile.TemporaryDirectory() as tmp:
        cases = [
            (dict(Rate="30O"), "Ramp Rate (K/min): '30O' is not a number"),
            (dict(Thickness="nan"), "Sample Thickness (mm): 'nan'"),
            (dict(Geometry="inf"), "Electrode Area (mm^2): 'inf'"),
            (dict(Delay="inf"), "Delay per Freq (s): 'inf'"),
            (dict(Delay="1e7"), "Delay per Freq (s) must be 0 - 3600"),
            (dict(freqs="1000, 1e3x"), "Frequencies (Hz): '1e3x'"),
            (dict(**{"Sample Name": "S:1"}), "Sample Name: ':'"),
            (dict(**{"Sample Name": "SΩ"}), "cp1252"),
        ]
        for over, want in cases + [({}, None)]:
            folder = tmp if want is not None else os.path.join(tmp, "gone")
            gui, inited = _start_gui(folder, **over)
            with _NoDialogs() as dialogs:
                gui.start_measurement()
            (kind, (_title, message)), = dialogs.calls
            assert kind == "showerror"
            if want is None:
                want = "Destination folder not found"
            assert want in message, (over, message)
            assert inited == [], over          # nothing reached the bus
            assert gui.status[-1].startswith("Start failed"), gui.status


def test_save_location_checks():
    good = _out_params()
    with tempfile.TemporaryDirectory() as tmp:
        mod.validate_save_location(tmp, good)
        mod.validate_save_location(
            tmp, _out_params(sample_name="SΩ", save_windeta=False))
        for name in ("a/b", "x?y", "S:1", 'q"', "trail.", "trail "):
            try:
                mod.validate_save_location(tmp, _out_params(sample_name=name))
            except ValueError as exc:
                assert "Sample Name" in str(exc)
                continue
            raise AssertionError(f"accepted sample name {name!r}")
        try:
            mod.validate_save_location(os.path.join(tmp, "gone"), good)
        except ValueError as exc:
            assert "folder" in str(exc)
        else:
            raise AssertionError("a missing folder must be refused")


def test_nan_and_inf_geometry_are_refused():
    nan, inf = float("nan"), float("inf")
    for bad in (_out_params(thickness_mm=nan), _out_params(thickness_mm=inf),
                _out_params(diameter_mm=nan),
                _out_params(geometry_mode="area", area_mm2=inf)):
        try:
            mod.validate_output_settings(bad)
        except ValueError as exc:
            assert "finite" in str(exc)
            continue
        raise AssertionError(f"accepted {bad}")
    for fn, args in ((mod.compute_c0, (nan, 1.0)),
                     (mod.compute_c0, (1.0, inf)),
                     (mod.compute_c0_from_area, (inf, 1.0))):
        try:
            fn(*args)
        except ValueError:
            continue
        raise AssertionError(f"{fn.__name__}{args} accepted")


def test_a_failing_sweep_does_not_kill_the_queue_pump():
    """A locked file or a plot error used to escape the after() callback:
    the pump died and COMPLETE (stop, heater off) was never handled."""
    def boom(cycle, elapsed):
        raise PermissionError(13, "Permission denied")
    handled, banners, afters = [], [], []
    q = queue.Queue()
    q.put(("CYCLE", {"points": []}, 1.0))
    q.put("COMPLETE")
    gui = _gui(data_queue=q, is_running=True, is_stabilizing=False,
               _process_cycle=boom,
               _handle_complete_event=lambda: handled.append("complete"),
               _banner=lambda m, beep=False: banners.append(m),
               root=types.SimpleNamespace(
                   after=lambda ms, fn: afters.append(ms)))
    gui._process_data_queue()
    assert handled == ["complete"] and afters == []
    assert any("PermissionError" in m for m in gui.logged)
    assert banners and "run continues" in banners[0]

    # No end-of-run event: the pump keeps itself alive.
    q.put(("CYCLE", {"points": []}, 2.0))
    gui._process_data_queue()
    assert afters == [200]

    # An end-of-run handler that raises still stops the run.
    stops = []
    q.put("CUTOFF")
    gui._handle_cutoff_event = lambda: boom(None, None)
    gui.stop_measurement = lambda from_user=True: stops.append(from_user)
    gui._process_data_queue()
    assert stops == [False]


def test_a_locked_file_loses_only_its_own_row():
    valid = mod.STATUS_RESULT_VALID
    real_open = open
    banners = []

    def locked_open(path, *a, **k):
        if str(path).endswith(".dat"):
            raise PermissionError(13, "Permission denied", path)
        return real_open(path, *a, **k)
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp)
        gui._banner = lambda m, beep=False: banners.append((m, beep))
        with patch.object(mod, "open", locked_open, create=True):
            gui._save_cycle_to_files({"points": [
                (300.0, 1000.0, 1e5, -2e6, valid, 1000.0),
                (300.1, 2000.0, 1e5, -2e6, valid, 2000.0)]})
        windeta = [real_open(gui.windeta_filepaths[f], "rb").read()
                   for f in (1000.0, 2000.0)]
    assert all(w.count(b"\r\n") == 5 for w in windeta)   # header + row
    assert sum("WRITE ERROR" in m for m in gui.logged) == 2
    assert len(banners) == 1 and banners[0][1] is True


class _StopDuringWait(threading.Event):
    """stop_event whose wait(timeout) behaves as if Stop was pressed
    during it (only for the given timeout)."""

    def __init__(self, on_timeout):
        super().__init__()
        self.on_timeout = on_timeout

    def wait(self, timeout=None):
        if self.on_timeout is None or timeout == self.on_timeout:
            self.set()
        return super().wait(0)


def test_stop_during_the_stabilized_wait_never_sets_is_running():
    def no_sweep(*a):
        raise AssertionError("no sweep after Stop")
    lakeshore = FakeLakeshore340(temps=(100.0,))
    lakeshore.start_ramp = lambda *a: None
    gui = _gui(backend=types.SimpleNamespace(
                   params={"start_temp": 100.0, "end_temp": 300.0,
                           "rate": 1.0, "delay": 0, "sensor": "A"},
                   lakeshore=lakeshore, measure_frequency_sweep=no_sweep),
               is_stabilizing=True, is_running=False, _last_htr_error=0,
               stop_event=_StopDuringWait(5), data_queue=queue.Queue(),
               frequencies=[1000.0])
    gui._measurement_worker()
    assert gui.is_running is False
    assert gui.data_queue.qsize() >= 1        # logs only, no exception
    while not gui.data_queue.empty():
        assert isinstance(gui.data_queue.get_nowait(), str)


def test_finalize_stop_goes_idle_and_unlocks_the_inputs():
    class _Widget:
        def __init__(self):
            self.states = []

        def config(self, **k):
            self.states.append(k["state"])
    entry, combo = _Widget(), _Widget()
    button = types.SimpleNamespace(config=lambda **k: None)
    gui = _gui(_run_locked=[(entry, "normal"), (combo, "readonly")],
               is_running=True, is_stabilizing=True, start_button=button,
               scan_button=button, data_queue=queue.Queue(),
               backend=types.SimpleNamespace(close_instruments=lambda: None),
               _close_after_stop=False)
    gui._set_run_inputs_state(False)
    gui._finalize_stop(False)
    assert gui.is_running is False and gui.is_stabilizing is False
    assert entry.states == ["disabled", "normal"]
    assert combo.states == ["disabled", "readonly"]


def test_inputs_are_locked_for_the_run_source():
    source = open(PATH, encoding="utf-8").read()
    frame = source[source.index("def create_input_frame"):
                   source.index("def create_console_frame")]
    locked = frame[frame.index("self._run_locked = ("):]
    for widget in ("self.entries.values()", "self.freq_text",
                   "self.file_button", "geom_radios + fmt_checks",
                   "self.wire_combobox", "self.sensor_combobox",
                   "self.lakeshore_cb", "self.lcr_cb"):
        assert widget in locked, widget
    assert "plot_freq_cb" not in locked       # live plot choice stays live
    start = source[source.index("def start_measurement"):
                   source.index("def _on_geom_mode_change")]
    assert "_set_run_inputs_state(False)" in start
    assert "_set_run_inputs_state(True)" in start          # error path
    final = source[source.index("def _finalize_stop"):
                   source.index("def _check_heater_status")]
    assert "_set_run_inputs_state(True)" in final


def test_stop_during_the_per_point_delay_measures_nothing_more():
    fake = FakeAlpha()
    combined = _combined(fake)
    cycle = combined.measure_frequency_sweep(
        [1000.0, 2000.0], 30.0, _StopDuringWait(None))
    assert cycle["points"] == []
    assert "MST" not in fake.commands
    assert combined.lakeshore.calls == []      # no KRDG? after Stop either


def test_the_temperature_is_read_after_the_per_point_delay():
    class _Recorder(threading.Event):
        def wait(self, timeout=None):
            combined.lakeshore.calls.append(("wait", (timeout,)))
            return False
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1000 2 1",
                               "ZRE=1E5 -2E6 2000 2 1"]})
    combined = _combined(fake)
    combined.measure_frequency_sweep([1000.0, 2000.0], 3.0, _Recorder())
    order = [c[0] for c in combined.lakeshore.calls]
    assert order == ["wait", "get_temperature"] * 2, order


def test_a_failed_safety_kill_is_not_reported_as_heater_off():
    import contextlib
    import io
    combined = _combined(temps=(400.0,))

    def refuse():
        raise OSError("GPIB timeout")
    combined.lakeshore.heater_off = refuse
    out = io.StringIO()
    with patch.object(mod.time, "sleep", lambda s: None), \
            contextlib.redirect_stdout(out):
        assert combined.check_safety_kill(400.0) is True
    assert "NOT CONFIRMED OFF" in out.getvalue()
    assert "Heater OFF." not in out.getvalue()


class _Loop340(_L340Session):
    REPLIES = {"CSET? 1": "A,1,1,1", "CMODE? 1": "1", "RANGE?": "0",
               "CLIMIT? 1": "475,0,0,3,5"}

    def query(self, cmd):
        self.traffic.append(("query", cmd))
        return self.REPLIES.get(cmd, self.idn)


def test_prepare_loop_turns_the_heater_off_around_cset():
    """A stale RANGE + setpoint must not heat through the Alpha connect."""
    ls = mod.Lakeshore340_Backend.__new__(mod.Lakeshore340_Backend)
    ls.instrument = _Loop340("LSCI,MODEL340,1,1")
    with patch.object(mod.time, "sleep", lambda s: None):
        ls.prepare_loop("A")
    traffic = ls.instrument.traffic
    cset = traffic.index(("write", "CSET 1,A,1,1"))
    assert traffic.index(("write", "RANGE 0")) < cset
    after = traffic[cset:]
    assert ("write", "RANGE 0") in after
    assert after.index(("query", "RANGE?")) > after.index(("write", "RANGE 0"))


def test_a_lakeshore_that_does_not_answer_is_closed():
    class _Silent(_L340Session):
        def query(self, cmd):
            raise TimeoutError("VI_ERROR_TMO")
    session = _Silent("")
    rm = types.SimpleNamespace(open_resource=lambda addr: session)
    with patch.object(mod, "pyvisa",
                      types.SimpleNamespace(ResourceManager=lambda: rm)):
        try:
            mod.Lakeshore340_Backend("GPIB0::19::INSTR")
        except TimeoutError:
            pass
        else:
            raise AssertionError("a silent address must raise")
    assert session.closed


def test_a_cleared_heater_error_replaces_the_banner_without_a_beep():
    gui = _gui(_last_htr_error=5, data_queue=queue.Queue())
    gui._check_heater_status((0, "No error"))
    msg = gui.data_queue.get_nowait()
    assert msg.startswith("STATUS:Running.") and "cleared" in msg
    banners = []
    gui.data_queue.put(msg)
    gui.is_running = gui.is_stabilizing = False
    gui._banner = lambda m, beep=False: banners.append((m, beep))
    gui._process_data_queue()
    assert banners == [(msg[7:], False)]


def test_a_single_temperature_still_sets_the_main_x_range():
    limits = []
    gui = _gui(plot_freq=1000.0,
               data_storage={"cp": {1000.0: {"T": [80.0], "v": [1e-12]}}},
               ax_main=types.SimpleNamespace(
                   set_xlim=lambda a, b: limits.append((a, b))),
               _apply_y_scale=lambda *a: False)
    gui._rescale_main_axis()
    assert limits == [(79.0, 81.0)]


def test_auto_scale_stays_linear_when_a_point_is_not_positive():
    class _Ax:
        def __init__(self):
            self.scales = []

        def set_yscale(self, s):
            self.scales.append(s)

        def relim(self):
            pass

        def set_autoscaley_on(self, on):
            pass

        def autoscale_view(self, **k):
            pass

        def set_ylim(self, *a):
            pass
    gui = _gui(y_scale_var=_Box("auto"), _decade_ylims={})
    ax = _Ax()
    assert gui._apply_y_scale(ax, [1e-12, 1e-9, -1e-12], "cp") is False
    assert ax.scales == ["linear"]
    assert gui._apply_y_scale(_Ax(), [1e-12, 1e-9], "cp") is True


def test_panel_scrolls_with_the_wheel_and_labels_match_the_panel():
    source = open(PATH, encoding="utf-8").read()
    assert 'canvas.bind_all("<MouseWheel>", _wheel)' in source
    assert "option_add('*Label.background', self.CLR_BG_DARK)" in source
    assert "style.configure('TRadiobutton'" in source
    assert "style='Graph.TRadiobutton'" in source


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


class _Init340(_L340Session):
    """A Model 340 that answers everything initialize_instruments asks."""
    REPLIES = {"CSET? 1": "A,1,1,1", "CMODE? 1": "1", "RANGE?": "0",
               "CLIMIT? 1": "475,0,0,3,5", "HTRST?": "0"}

    def query(self, cmd):
        self.traffic.append(("query", cmd))
        return self.REPLIES.get(cmd, self.idn)


def test_start_on_the_tk_thread_never_connects():
    _FakeThread.made = []
    with tempfile.TemporaryDirectory() as tmp:
        gui, inited = _start_gui(tmp)
        afters, locks = [], []
        for name, value in dict(
                data_queue=queue.Queue(), data_storage={}, _decade_ylims={},
                line_main=_Recorder(), line_sub1=_Recorder(),
                line_sub2=_Recorder(), canvas=_Recorder(),
                start_button=_Recorder(), stop_button=_Recorder(),
                scan_button=_Recorder(), stop_event=threading.Event(),
                _files_ready=threading.Event(),
                root=types.SimpleNamespace(
                    after=lambda ms, fn: afters.append(fn))).items():
            setattr(gui, name, value)
        gui._parse_frequencies = lambda: [1000.0, 2000.0]
        gui._set_run_inputs_state = locks.append
        with _NoDialogs() as dialogs, \
                patch.object(mod.threading, "Thread", _FakeThread):
            gui.start_measurement()
        files = os.listdir(tmp)
    assert inited == [], "Start must not connect on the Tk thread"
    assert files == [], "files are created only after CONNECTED"
    assert dialogs.calls == []
    (thread,) = _FakeThread.made
    assert thread.started and thread.target == gui._run_worker
    assert thread.args[0]["lcr_visa"] == "GPIB0::10::INSTR"
    assert gui.status[-1].startswith("CONNECTING"), gui.status
    assert locks == [False]
    assert gui.is_stabilizing and not gui.is_running
    assert gui.start_button.calls[-1] == {"state": "disabled"}
    assert gui.stop_button.calls[-1] == {"state": "normal"}
    assert afters == [gui._process_data_queue]


def test_stop_during_the_alpha_reset_wait_ends_the_worker_quickly():
    from alphaan_port_test_support import ALPHA_IDN, FakeVisaError
    session = _Init340("LSCI,MODEL340,123456,061407")
    fake = FakeAlpha({"*IDN?": [ALPHA_IDN] + [
        FakeVisaError(mod.VI_ERROR_TMO)] * 100000})
    combined = mod.Combined_Backend()
    combined.lcr = _bare_alpha(fake)
    rm = types.SimpleNamespace(open_resource=lambda addr: session)
    gui = _gui(backend=combined, stop_event=threading.Event(),
               _files_ready=threading.Event(), data_queue=queue.Queue())
    params = _params(start_temp=300.0, end_temp=310.0, rate=1.0)
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
        # prepare_loop only: heater off, loop set up, ramp off.
        assert writes == ["*CLS", "RANGE 0", "CSET 1,A,1,1", "CMODE 1,1",
                          "RAMP 1,0,0", "RANGE 0"], writes
        assert "MODE=IMP" not in fake.commands
        msgs = []
        while not gui.data_queue.empty():
            msgs.append(gui.data_queue.get_nowait())
        assert all(isinstance(m, str) for m in msgs), msgs
        assert any("Stopped while connecting" in m for m in msgs), msgs
        # The normal stop path then closes what was opened, safely.
        combined.close_instruments()
    writes = [c for k, c in session.traffic if k == "write"]
    assert writes[-2:] == ["RAMP 1,0,0", "RANGE 0"] and session.closed
    assert not any(w.startswith(("SETP", "RAMP 1,1"))
                   or (w.startswith("RANGE ") and w != "RANGE 0")
                   for w in writes), writes
    # The Alpha was still rejoining: nothing is sent to it after *RST
    # (only the rejoin *IDN? polls), the session is just closed.
    after_rst = fake.commands[fake.commands.index("*RST") + 1:]
    assert set(after_rst) == {"*IDN?"}, after_rst
    assert fake.closed and combined.lcr.instrument is None


def test_a_connect_failure_resets_the_ui_and_shows_error():
    def fail(params, stop_event=None):
        raise ConnectionError("The Alpha did not answer")
    closed, afters, locks = [], [], []
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
               root=types.SimpleNamespace(
                   after=lambda ms, fn: afters.append(fn),
                   bell=lambda: None))
    gui._set_run_inputs_state = locks.append
    gui.measurement_thread = threading.Thread(
        target=gui._run_worker, args=(_params(),), daemon=True)
    gui.measurement_thread.start()
    gui.measurement_thread.join(5.0)
    assert not gui.measurement_thread.is_alive()
    with _NoDialogs() as dialogs:
        gui._process_data_queue()
    assert gui.status[-1].startswith("ERROR - could not start"), gui.status
    assert "did not answer" in gui.status[-1]
    assert any("ERROR during startup" in m and "Traceback" in m
               for m in gui.logged)
    assert locks == [True] and closed == [True]
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


class _GoneAfterReset(FakeAlpha):
    """An Alpha that stops answering once *RST is written: every later
    write or query hangs (as on 64-bit Keysight VISA) and then times out."""

    HANG_S = 0.5

    def __init__(self):
        super().__init__()
        self.reset = False

    def write(self, command):
        if self.reset:
            self.traffic.append(("write", command))
            threading.Event().wait(self.HANG_S)
            raise _tmo_error()
        super().write(command)
        if command == "*RST":
            self.reset = True

    def query(self, command):
        if self.reset:
            self.traffic.append(("query", command))
            threading.Event().wait(self.HANG_S)
            raise _tmo_error()
        return super().query(command)


def _tmo_error():
    from alphaan_port_test_support import FakeVisaError
    return FakeVisaError(mod.VI_ERROR_TMO)


def test_stop_during_the_rst_rejoin_sends_nothing_more_at_shutdown():
    fake = _GoneAfterReset()
    backend = _bare_alpha(fake)
    backend.instrument = fake
    backend.stop_event = threading.Event()
    assert backend.__dict__.get("_alpha_rebooting") is None  # __new__-built
    timer = threading.Timer(0.2, backend.stop_event.set)    # Stop pressed
    with patch("builtins.print", lambda *a, **k: None):
        timer.start()
        try:
            backend._reset_and_set_impedance_mode()
        except mod.StopRequested:
            pass
        else:
            raise AssertionError("Stop must end the rejoin wait")
        finally:
            timer.cancel()
        assert backend._alpha_rebooting
        n_sent = len(fake.commands)
        lines = []
        with patch("builtins.print", lambda *a, **k: lines.append(
                " ".join(str(x) for x in a))):
            began = time.monotonic()
            backend.close_instrument()
            took = time.monotonic() - began
    assert took < 0.3, took
    assert fake.commands[n_sent:] == [], fake.commands[n_sent:]
    assert fake.closed and backend.instrument is None
    assert not backend._alpha_rebooting
    assert any("Alpha still resetting after *RST - closing the session "
               "without shutdown commands" in ln for ln in lines), lines


def test_combined_close_during_the_rejoin_still_turns_the_340_heater_off():
    combined = _combined(_GoneAfterReset())
    fake = combined.lcr.instrument
    fake.reset = True
    combined.lcr._alpha_rebooting = True
    with patch("builtins.print", lambda *a, **k: None):
        began = time.monotonic()
        combined.close_instruments()
        took = time.monotonic() - began
    assert took < 0.3, took
    assert fake.commands == [] and fake.closed
    # The Lakeshore close (heater off) still runs.
    assert ("close", ()) in combined.lakeshore.calls


def test_an_answered_rejoin_clears_the_flag_so_close_parks_the_alpha():
    backend, fake = connected_backend(mod)
    with patch.object(mod.time, "sleep", lambda s: None),             patch("builtins.print", lambda *a, **k: None):
        backend._reset_and_set_impedance_mode()
        assert backend._alpha_rebooting is False
        backend.close_instrument()
    assert "ACV=0" in fake.commands and fake.commands[-1] == "*RST"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
