"""Tests for novocontrol/Temprature_Scan_Passive_AlphaAN_GUI.py
(Alpha-AN + Lakeshore 350 as a passive thermometer).

Runnable as plain Python as well as under pytest. No VISA, no Tk window.
"""

import inspect
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
    FakeAlpha, FakeLakeshore, connected_backend, load_port,
    run_common_backend_checks,
)

mod, PATH = load_port("Temprature_Scan_Passive_AlphaAN_GUI.py")


# --- a recording Lakeshore 350 VISA session --------------------------------

class FakeL350Session:
    """Stands in for the pyvisa resource behind Lakeshore350_Backend."""

    def __init__(self, idn="LSCI,MODEL350,LSA1234,1.0"):
        self.idn = idn
        self.traffic = []
        self.timeout = 0
        self.closed = False

    def write(self, cmd):
        self.traffic.append(("write", cmd))

    def query(self, cmd):
        self.traffic.append(("query", cmd))
        if cmd == "*IDN?":
            return self.idn
        if cmd.startswith("KRDG?"):
            return "300.0"
        if cmd.startswith("HTR?"):
            return "0.0"
        return "0"

    def close(self):
        self.closed = True

    @property
    def writes(self):
        return [c for k, c in self.traffic if k == "write"]


class FakePyvisa:
    """Module-level pyvisa replacement: every open_resource returns a new
    FakeL350Session, all kept in .sessions."""

    def __init__(self):
        self.sessions = []

    def ResourceManager(self):
        outer = self

        class _RM:
            def open_resource(self, addr):
                s = FakeL350Session()
                outer.sessions.append(s)
                return s
        return _RM()


PARAMS = {"lakeshore_visa": "GPIB0::12::INSTR",
          "lcr_visa": "GPIB0::10::INSTR", "frequencies": [1000.0],
          "acv": 1.0, "mtm": 0.5, "wire_mode": "2", "zero_range": True,
          "delay": 0}


def _combined(fake_alpha=None, temps=(300.0,)):
    combined = mod.Combined_Backend()
    combined.lcr, _ = connected_backend(mod, fake_alpha)
    combined.lakeshore = FakeLakeshore(temps)
    return combined


# --- tests --------------------------------------------------------------------

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


def test_files_are_converted_with_the_actual_frequency():
    gui = mod.Integrated_CT_GUI.__new__(mod.Integrated_CT_GUI)
    seen = []
    gui.calculate_impedance_parameters = (
        lambda f, R, X: seen.append(f) or [0.0] * 18)
    gui._flush_pending_rows = lambda: None
    rows = []
    gui._write_or_buffer = lambda path, row: rows.append((path, row))
    gui.freq_filepaths = {1000.0: "f1000"}
    gui.windeta_filepaths = {}
    cycle = {"points": [(300.0, 1000.0, 1e5, -2e6, 2, 1001.5)]}
    gui._save_cycle_to_files(cycle)
    assert seen == [1001.5] and rows[0][0] == "f1000"
    assert len(rows[0][1].split("\t")) == 19     # T + 18 legacy columns


def test_a_disconnected_signal_source_aborts_the_sweep():
    fake = FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 6 1"})
    combined = _combined(fake)
    try:
        combined.measure_frequency_sweep([1000.0], 0)
    except mod.AlphaFatalStatusError as exc:
        assert "status 6" in str(exc)
    else:
        raise AssertionError("status 6 must abort the sweep")


class _WorkerHarness:
    _measurement_worker = mod.Integrated_CT_GUI._measurement_worker
    _queue_partial_cycle = mod.Integrated_CT_GUI._queue_partial_cycle

    def __init__(self, error):
        self.start_time = 0.0
        self.is_running = True
        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()
        self.frequencies = [1000.0]
        self.reconnects = []
        outer = self

        class _B:
            params = {"delay": 0}

            def measure_frequency_sweep(self, *a):
                if outer.reconnects:          # second pass: stop
                    outer.stop_event.set()
                    return {"points": []}
                raise error
        self.backend = _B()

    def _reconnect_with_backoff(self, attempt):
        self.reconnects.append(attempt)
        return True

    def drained(self):
        out = []
        while not self.data_queue.empty():
            out.append(self.data_queue.get_nowait())
        return out


def test_status_6_ends_the_run_instead_of_reconnecting_forever():
    h = _WorkerHarness(mod.AlphaFatalStatusError("Alpha-AN status 6"))
    h._measurement_worker()
    assert h.reconnects == []
    assert any(isinstance(x, mod.AlphaFatalStatusError) for x in h.drained())


def test_an_alpha_refusal_ends_the_run_instead_of_reconnecting():
    """'CR' (no load-short cal) or a non-OK ack: a *RST cannot fix it."""
    h = _WorkerHarness(RuntimeError("Instrument returned 'CR'"))
    h._measurement_worker()
    assert h.reconnects == []
    assert any(isinstance(x, RuntimeError) for x in h.drained())


def test_comm_errors_still_go_through_the_retry_forever_reconnect():
    h = _WorkerHarness(OSError("GPIB timeout"))
    h._measurement_worker()
    assert h.reconnects == [1]
    assert not any(isinstance(x, Exception) for x in h.drained())


def test_the_safety_kill_stops_before_measuring():
    fake = FakeAlpha()
    combined = _combined(fake, temps=(401.0,))
    cycle = combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    assert len(cycle["points"]) == 1
    assert math.isnan(cycle["points"][0][2])
    assert "MST" not in fake.commands
    assert ("set_heater_range", (1, "off")) in combined.lakeshore.calls


def test_settings_are_validated_before_either_instrument_is_opened():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    fake_visa = FakePyvisa()
    with patch.object(mod, "pyvisa", fake_visa):
        try:
            combined.initialize_instruments(
                {**PARAMS, "frequencies": [5e6], "acv": 3.0})
        except ValueError as exc:
            assert "Vrms" in str(exc)
        else:
            raise AssertionError("3 Vrms at 5 MHz is over the ZG4 ceiling")
    assert fake.commands == [] and combined.lakeshore is None
    assert fake_visa.sessions == []


def test_passive_init_never_resets_or_drives_the_controller():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    fake_visa = FakePyvisa()
    for zero_range, expected in ((True, ["RANGE 1,0"]), (False, [])):
        fake_visa.sessions.clear()
        with patch.object(mod, "pyvisa", fake_visa):
            combined.initialize_instruments(
                {**PARAMS, "zero_range": zero_range})
        assert fake_visa.sessions[0].writes == expected
    assert fake.kind_of("ACV=1") == "query"         # Alpha configured


def test_reconnect_sends_nothing_to_the_controller():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    fake_visa = FakePyvisa()
    with patch.object(mod, "pyvisa", fake_visa):
        combined.initialize_instruments(dict(PARAMS))   # zero_range ticked
        first = fake_visa.sessions[0]
        fake.traffic.clear()
        combined.reconnect()
    assert first.writes == ["RANGE 1,0"]   # start only, not at reconnect
    assert first.closed
    second = fake_visa.sessions[1]
    assert second.traffic == [("query", "*IDN?")], second.traffic
    assert combined.lakeshore.instrument is second
    # Alpha: safe-state + close, then connect() + initialize_instrument()
    cmds = fake.commands
    assert cmds.index("ACV=0") < cmds.index("INTTYP?")
    assert "MODE=IMP" in cmds and "ACV=1" in cmds
    # No *RST on the way out (the Alpha would be off the bus when
    # connect() asks *IDN?); only initialize_instrument()'s own *RST.
    assert cmds.count("*RST") == 1
    assert cmds.index("*IDN?") < cmds.index("*RST")


def test_unattended_handlers_open_no_dialog():
    for fn in (mod.Integrated_CT_GUI._handle_kill_event,
               mod.Integrated_CT_GUI._handle_runtime_error):
        assert "messagebox" not in inspect.getsource(fn), fn.__name__
    src = open(PATH, encoding="utf-8").read()
    assert "os.fsync" in src and "SetThreadExecutionState" in src


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


def test_default_frequency_list_is_inside_the_alpha_ceiling():
    freqs = [float(x) for x in mod.Integrated_CT_GUI.DEFAULT_FREQS.split(",")]
    mod.validate_alpha_parameters(freqs, 1.0, 0.5, "2")


def _sweep_backend(fake):
    return _combined(fake, temps=(300.0, 301.0))

# --- pre-lab fixes (23 Oct 2026) ------------------------------------------

class _Recorder:
    """Accepts any attribute call (stop_button.config, ax.set_title...)."""

    def __getattr__(self, name):
        return lambda *a, **k: None


def _bare_gui():
    gui = mod.Integrated_CT_GUI.__new__(mod.Integrated_CT_GUI)
    gui.logged = []
    gui.log = gui.logged.append
    gui.statuses = []
    gui.lbl_status = types.SimpleNamespace(
        config=lambda **k: gui.statuses.append(k["text"]))
    return gui


def test_freq_tag_keeps_sub_hz_frequencies_apart():
    assert mod.freq_tag(0.1) == "0.1" and mod.freq_tag(0.5) == "0.5"
    assert mod.freq_tag(1000000.0) == "1000000"
    assert mod.freq_tag(1000.0) == "1000"       # old file names unchanged


def test_point_timeout_scales_with_mtm_and_period():
    t = mod.point_timeout_s
    assert t(1000.0, 0.5) == mod.SRQ_TIMEOUT_MEASURE_S
    assert t(0.001, 0.5) == 4 * 1000.0 + 60.0
    assert t(1000.0, 100.0) == 460.0
    backend, fake = connected_backend(mod)
    backend.params = {"mtm": 100.0}
    backend.measure_point(1000.0)
    assert ("wait_for_srq", 460000) in fake.traffic


def test_stop_waits_for_the_longest_point():
    gui = _bare_gui()
    gui._stopping, gui.is_running, gui.is_stabilizing = False, True, False
    gui.stop_event = threading.Event()
    gui.stop_button = _Recorder()
    gui.backend = types.SimpleNamespace(params={"mtm": 1000.0})
    gui.frequencies = [1000.0]
    gui._poll_worker_stopped = lambda from_user: None
    gui.stop_measurement(False)
    assert gui._stop_deadline - time.time() > 4000.0


def test_start_refuses_while_the_previous_worker_is_alive():
    gui = _bare_gui()
    gui.measurement_thread = types.SimpleNamespace(is_alive=lambda: True)

    def _never():
        raise AssertionError("must not parse or touch hardware")
    gui._parse_frequencies = _never
    gui.start_measurement()
    assert any("still finishing" in m for m in gui.logged)


def test_a_failed_start_closes_the_instruments():
    gui = _bare_gui()
    gui.measurement_thread = None
    closed = []
    gui.backend = types.SimpleNamespace(
        close_instruments=lambda: closed.append(True))

    def _fail():
        raise ValueError("refused MTM=")
    gui._parse_frequencies = _fail
    with patch.object(mod, "messagebox", _Recorder()):
        gui.start_measurement()
    assert closed == [True]


def test_plot_dropdown_maps_by_index_not_by_label():
    gui = _bare_gui()
    gui.frequencies = [0.1, 0.5]
    gui.plot_freq_cb = types.SimpleNamespace(current=lambda: 1,
                                             get=lambda: "0 Hz")
    gui._decade_ylims = {}
    gui.ax_main = _Recorder()
    gui._update_live_plots = lambda force=False: None
    gui._on_plot_freq_change()
    assert gui.plot_freq == 0.5


def test_non_valid_points_are_discarded_but_the_kill_row_is_kept():
    gui = _bare_gui()
    gui.calculate_impedance_parameters = lambda f, R, X: [1.0] * 18
    gui._flush_pending_rows = lambda: None
    rows = []
    gui._write_or_buffer = lambda path, row: rows.append(path)
    gui.freq_filepaths = {1.0: "f1", 2.0: "f2", 3.0: "f3"}
    gui.windeta_filepaths = {}
    nan = float("nan")
    cycle = {"heater": 0.0, "points": [
        (300.0, 1.0, 1e5, -2e6, 2, 1.0),       # valid
        (300.0, 2.0, 1e5, -2e6, 3, 2.0),       # V1 out of range
        (401.0, 3.0, nan, nan, -1, 3.0)]}      # safety-kill row
    gui._save_cycle_to_files(cycle)
    assert rows == ["f1", "f3"]

    gui.var_plot_thin = types.SimpleNamespace(get=lambda: False)
    gui.data_storage = {
        "time": [], "temperature": [],
        "cp": {f: {"T": [], "v": []} for f in (1.0, 2.0, 3.0)},
        "g": {f: {"T": [], "v": []} for f in (1.0, 2.0, 3.0)}}
    gui._update_data_storage(cycle, 1.0)
    assert gui.data_storage["cp"][2.0]["T"] == []
    assert len(gui.data_storage["cp"][1.0]["T"]) == 1

    for name in ("_save_cycle_to_files", "_append_t_log",
                 "_update_data_storage", "_update_live_plots"):
        setattr(gui, name, lambda *a, **k: None)
    gui._process_cycle(cycle, 1.0)
    warned = [m for m in gui.logged if "Point discarded" in m]
    assert len(warned) == 1 and "f=2 Hz" in warned[0]


# --- output formats: PICA .dat + WinDETA Tscan .txt -------------------------

def _out_params(**over):
    """Start-time params as the output-format code reads them."""
    p = {"sample_name": "S", "acv": 0.5, "mtm": 0.5, "delay": 0.0,
         "wire_mode": "2", "frequencies": [1000.0, 2000.0],
         "lcr_visa": "GPIB0::10::INSTR", "lakeshore_visa": "GPIB0::12::INSTR",
         "zero_range": True, "geometry_mode": "diameter",
         "diameter_mm": 9.68, "thickness_mm": 1.14,
         "temp_program": "Passive - external ramp", "save_pica": True,
         "save_windeta": True}
    p.update(over)
    return p


def _files(tmp, **over):
    params = _out_params(**over)
    gui = _bare_gui()
    gui.file_location_path = tmp
    gui.frequencies = params["frequencies"]
    gui.c0_farads = mod.c0_from_params(params)
    gui._pending_rows = mod.deque(maxlen=20000)
    gui._write_error_logged = False
    gui._create_per_frequency_files(params)
    return gui


def test_per_frequency_files_do_not_collide_below_1_hz():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[0.1, 0.5, 1e6], save_windeta=False)
        names = sorted(os.path.basename(p)
                       for p in gui.freq_filepaths.values())
        t_log = os.path.basename(gui.t_log_path)
    assert names == ["S-0.1Hz_pica_format.dat", "S-0.5Hz_pica_format.dat",
                     "S-1000000Hz_pica_format.dat"]
    assert gui.windeta_filepaths == {}       # unticked format: no files
    assert t_log == "S_T-log.txt"            # T-log is still written


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


def test_a_buffered_windeta_row_keeps_its_crlf_framing():
    """A WinDETA row that hit a disk error goes through the same buffer-
    and-retry path as a PICA row, and lands as cp1252 + CRLF."""
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, save_pica=False)
        c0 = gui.c0_farads
        z = 1 / (1j * 2 * math.pi * 1000.0 * c0 * (13 - 2j))
        point = (423.15, 1000.0, z.real, z.imag,
                 mod.STATUS_RESULT_VALID, 1000.0)
        real_write = gui._durable_write

        def failing(path, text):
            raise OSError("share went away")
        gui._durable_write = failing
        gui._save_cycle_to_files({"points": [point]})
        assert len(gui._pending_rows) == 1
        assert any("WRITE ERROR" in m for m in gui.logged)
        gui._durable_write = real_write
        gui._save_cycle_to_files({"points": [point]})
        assert not gui._pending_rows
        raw = open(gui.windeta_filepaths[1000.0], "rb").read()
    row = b"1.50000e+02\t1.30000e+01\t2.00000e+00\t1.53846e-01\t-8.12538e+01"
    assert raw.split(b"\r\n")[4:] == [row, row, b""]
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert gui.freq_filepaths == {}


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
                 "Passive - external ramp", "GPIB0::10::INSTR"):
        assert want in joined, want


def test_start_describes_the_passive_thermometer_and_checks_output_first():
    """temp_program says PICA only reads the thermometer; geometry and
    format are checked before any instrument is opened."""
    source = inspect.getsource(mod.Integrated_CT_GUI.start_measurement)
    assert "Passive - external ramp" in source and "KRDG? A" in source
    assert "Ramp {" not in source            # no ramp of PICA's own
    assert (source.index("validate_output_settings(params)")
            < source.index("c0_from_params(params)")
            < source.index("target=self._run_worker"))
    # Connect/initialise run on the worker, never on the Tk thread.
    assert "initialize_instruments" not in source


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
    gui = _bare_gui()
    gui.entries = {"Geometry": _Entry("78.5"), "Thickness": _Entry("1.0")}
    gui._geom_values = {"area": "1.0", "diameter": "1.0"}
    gui.geom_mode_var = types.SimpleNamespace(get=lambda: mode.value)
    gui.lbl_geom = types.SimpleNamespace(
        config=lambda **k: labels.append(k["text"]))
    gui.lbl_c0 = types.SimpleNamespace(config=lambda **k: None)
    mode.value = "diameter"
    gui._on_geom_mode_change()
    assert gui.entries["Geometry"].get() == "1.0"
    gui.entries["Geometry"].text = "10"
    mode.value = "area"
    gui._on_geom_mode_change()
    assert gui.entries["Geometry"].get() == "78.5"
    assert gui._geom_values["diameter"] == "10"
    assert labels == ["Electrode Diameter (mm):", "Electrode Area (mm²):"]


def test_a_wrong_idn_gets_no_shutdown_commands():
    fake = FakeAlpha({"*IDN?": "LSCI,MODEL350,LSA1234,1.0"})
    backend, _ = connected_backend(mod, fake)
    backend.instrument = None
    try:
        backend.connect("GPIB0::12::INSTR")
    except ConnectionError as exc:
        assert "not a Novocontrol Alpha" in str(exc)
    else:
        raise AssertionError("a Lakeshore must not be accepted as the Alpha")
    assert fake.commands == ["*IDN?"] and fake.closed
    assert backend.instrument is None


def test_release_for_reconnect_parks_without_rst():
    backend, fake = connected_backend(mod)
    backend.release_for_reconnect()
    assert fake.commands == ["ACV=0", "ZCONSPL=0"] and fake.closed
    assert backend.instrument is None
    backend.release_for_reconnect()              # idempotent, never raises


class _ReconnectHarness:
    _reconnect_with_backoff = mod.Integrated_CT_GUI._reconnect_with_backoff

    def __init__(self, *outcomes):
        self.is_running = True
        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()
        self.outcomes = list(outcomes)
        self.closed = []
        outer = self

        class _B:
            def reconnect(self):
                o = outer.outcomes.pop(0)
                if callable(o):
                    o()
                elif o is not None:
                    raise o

            def close_instruments(self):
                outer.closed.append(True)
        self.backend = _B()

    def run(self):
        clock = iter(range(0, 10 ** 6, 100))     # skips the backoff waits
        with patch.object(mod, "time", types.SimpleNamespace(
                time=lambda: next(clock))):
            return self._reconnect_with_backoff(1)


def test_reconnect_retries_bus_errors_but_not_alpha_refusals():
    h = _ReconnectHarness(TimeoutError("no SRQ"), OSError("ENOL"), None)
    assert h.run() is True and h.outcomes == []
    h = _ReconnectHarness(RuntimeError("Command 'MTM=1' failed: 'IP'"))
    try:
        h.run()
    except RuntimeError:
        pass
    else:
        raise AssertionError("an Alpha refusal must end the run")


def test_stop_during_reconnect_closes_the_fresh_sessions():
    h = _ReconnectHarness(None)
    h.outcomes = [lambda: h.stop_event.set()]    # Stop while reconnecting
    assert h.run() is False and h.closed == [True]


def test_points_measured_before_a_fatal_status_are_kept():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1000 2 1",
                               "ZRE=1E5 -2E6 2000 6 1"]})
    combined = _sweep_backend(fake)
    try:
        combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    except mod.AlphaFatalStatusError as exc:
        partial = exc.partial_cycle
    else:
        raise AssertionError("status 6 must abort the sweep")
    assert [p[1] for p in partial["points"]] == [1000.0]

    err = mod.AlphaFatalStatusError("Alpha-AN status 6")
    err.partial_cycle = partial
    h = _WorkerHarness(err)
    h._measurement_worker()
    out = h.drained()
    kinds = [x[0] if isinstance(x, tuple) else type(x).__name__
             for x in out if not isinstance(x, str)]
    assert kinds == ["CYCLE", "AlphaFatalStatusError"], kinds
    assert "Traceback" in out[-1].pica_traceback


# --- review fixes (23 Oct 2026): wrong address, cut-short sweeps -------------

class _IdnPyvisa(FakePyvisa):
    """FakePyvisa whose sessions answer *IDN? with the given strings."""

    def __init__(self, *idns):
        super().__init__()
        self.idns = list(idns)

    def ResourceManager(self):
        outer = self

        class _RM:
            def open_resource(self, addr):
                s = FakeL350Session(idn=outer.idns.pop(0))
                outer.sessions.append(s)
                return s
        return _RM()


def test_a_non_350_address_is_refused_before_any_write():
    for zero_range in (True, False):
        combined = mod.Combined_Backend()
        combined.lcr, fake = connected_backend(mod)
        combined.lcr.instrument = None
        fake_visa = _IdnPyvisa("LSCI,MODEL340,123456,061407")
        with patch.object(mod, "pyvisa", fake_visa):
            try:
                combined.initialize_instruments(
                    {**PARAMS, "zero_range": zero_range})
            except ConnectionError as exc:
                assert "not a Lake Shore Model 350" in str(exc)
            else:
                raise AssertionError("a Model 340 must be refused")
        combined.close_instruments()           # Start's error path
        session = fake_visa.sessions[0]
        assert session.traffic == [("query", "*IDN?")], session.traffic
        assert session.closed and combined.lakeshore is None
        assert fake.commands == []


def test_reconnect_refuses_an_address_that_is_no_longer_a_350():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    fake_visa = _IdnPyvisa("LSCI,MODEL350,LSA1234,1.0",
                           "ALPHA Analyzer, Novocontrol Technologies, x")
    with patch.object(mod, "pyvisa", fake_visa):
        combined.initialize_instruments(dict(PARAMS))
        try:
            combined.reconnect()
        except ConnectionError as exc:      # retried by the backoff loop
            assert "on reconnect" in str(exc)
        else:
            raise AssertionError("a non-350 must be refused on reconnect")
    second = fake_visa.sessions[1]
    assert second.traffic == [("query", "*IDN?")] and second.closed
    assert combined.lakeshore is None
    combined.close_instruments()
    assert second.traffic == [("query", "*IDN?")]


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

    def scan():
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

    gui, _ = scan()
    assert gui.lakeshore_cb["chosen"].startswith("GPIB0::12::INSTR")
    assert gui.lcr_cb["chosen"].startswith("GPIB0::10::INSTR")
    del idns["GPIB0::12::INSTR"]
    gui, logged = scan()                        # only a 340 on the bus
    assert any("Start will refuse" in m for m in logged)


def test_a_refused_alpha_address_gets_nothing_at_close_either():
    fake = FakeAlpha({"*IDN?": "LSCI,MODEL350,LSA1234,1.0"})
    backend, _ = connected_backend(mod, fake)
    backend.instrument = None
    try:
        backend.connect("GPIB0::12::INSTR")
    except ConnectionError:
        pass
    else:
        raise AssertionError("a non-Alpha IDN must be refused")
    backend.close_instrument()                 # Start's error path
    assert fake.commands == ["*IDN?"], fake.commands


def test_a_sweep_cut_short_by_stop_is_written_to_the_files():
    """The pump stops re-scheduling once Stop drops the run flags; the
    worker's last (partial) sweep arrives after that and must still be
    written, fsync'd, before the run ends."""
    valid = mod.STATUS_RESULT_VALID
    closed = []
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp)
        before = {f: open(p).read() for f, p in gui.freq_filepaths.items()}
        gui._update_data_storage = lambda *a: None
        gui._update_live_plots = lambda *a, **k: None
        gui._set_keep_awake = lambda on: None
        gui.data_queue = queue.Queue()
        gui.data_queue.put(("CYCLE", {
            "points": [(300.0, 1000.0, 1e5, -2e6, valid, 1000.0)],
            "heater": 5.0, "wall_time": mod.datetime.now()}, 12.0))
        gui.data_queue.put("KILL")
        gui.start_button = gui.scan_button = _Recorder()
        gui._set_inputs_enabled = lambda enabled: None
        gui.backend = types.SimpleNamespace(
            close_instruments=lambda: closed.append(True))
        gui._close_after_stop = False
        gui._stopping = True
        synced = []
        real_fsync = os.fsync
        with patch.object(mod.os, "fsync",
                          lambda fd: (synced.append(fd), real_fsync(fd))):
            gui._finalize_stop(False)
        after = {f: open(p).read() for f, p in gui.freq_filepaths.items()}
        windeta = open(gui.windeta_filepaths[1000.0], "rb").read()
        t_log = open(gui.t_log_path).read()
    added = after[1000.0][len(before[1000.0]):].splitlines()
    assert len(added) == 1 and added[0].startswith("3.000000E+02\t")
    assert len(added[0].split("\t")) == 19
    assert after[2000.0] == before[2000.0]
    assert windeta.endswith(b"\r\n") and windeta.count(b"\r\n") == 5
    assert "\t12.0\t300.0000\t5.00" in t_log
    assert len(synced) == 3                    # PICA, WinDETA, T-log
    assert any("SAFETY KILL" in m for m in gui.logged)
    assert gui.data_queue.empty() and closed == [True]
    assert gui._stopping is False


def test_start_drops_what_a_late_worker_left_in_the_queue():
    # A worker that outlived Stop's drain left a partial CYCLE and an
    # exception behind; neither belongs to the next run.
    gui = _bare_gui()
    gui.measurement_thread = None
    gui.data_queue = queue.Queue()
    gui.data_queue.put(("CYCLE", {"points": [], "killed": False}, 1.0))
    gui.data_queue.put(RuntimeError("late error from the previous run"))
    gui.backend = types.SimpleNamespace(close_instruments=lambda: None)

    def _fail():
        raise ValueError("stop the start here")
    gui._parse_frequencies = _fail
    with patch.object(mod, "messagebox", _Recorder()):
        gui.start_measurement()
    assert gui.data_queue.empty()


class _BadCloseSession(FakeL350Session):
    def close(self):
        raise OSError("VI_ERROR_CONN_LOST")


class _BadClosePyvisa(_IdnPyvisa):
    """_IdnPyvisa whose sessions raise on close()."""

    def ResourceManager(self):
        outer = self

        class _RM:
            def open_resource(self, addr):
                s = _BadCloseSession(idn=outer.idns.pop(0))
                outer.sessions.append(s)
                return s
        return _RM()


def test_a_refused_lakeshore_is_dropped_even_if_its_close_raises():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    combined.lcr.instrument = None
    fake_visa = _BadClosePyvisa("LSCI,MODEL340,123456,061407")
    with patch.object(mod, "pyvisa", fake_visa):
        try:
            combined.initialize_instruments(dict(PARAMS))
        except ConnectionError as exc:
            assert "not a Lake Shore Model 350" in str(exc)
        else:
            raise AssertionError("a Model 340 must be refused")
    assert combined.lakeshore is None
    combined.close_instruments()               # Start's error path
    assert fake_visa.sessions[0].traffic == [("query", "*IDN?")]
    assert fake.commands == []


def test_a_reconnect_refusal_survives_a_close_that_raises():
    combined = mod.Combined_Backend()
    combined.lcr, _fake = connected_backend(mod)
    fake_visa = _IdnPyvisa("LSCI,MODEL350,LSA1234,1.0")
    with patch.object(mod, "pyvisa", fake_visa):
        combined.initialize_instruments(dict(PARAMS))
    bad_visa = _BadClosePyvisa("ALPHA Analyzer, Novocontrol Technologies, x")
    with patch.object(mod, "pyvisa", bad_visa):
        try:
            combined.reconnect()
        except ConnectionError as exc:      # the refusal, not the OSError
            assert "on reconnect" in str(exc)
        else:
            raise AssertionError("a non-350 must be refused on reconnect")
    assert combined.lakeshore is None
    combined.close_instruments()
    assert bad_visa.sessions[0].traffic == [("query", "*IDN?")]


# --- bug hunt fixes (27 Oct 2026) -------------------------------------------

class _Text:
    """Entry / Text stand-in: get() returns the text, config() records."""

    def __init__(self, text=""):
        self.text = text
        self.states = []

    def get(self, *a):
        return self.text

    def config(self, **k):
        self.states.append(k.get("state"))


def _start_gui(tmp, **texts):
    """A bare GUI whose Start reaches the hardware step with the given box
    texts; initialize_instruments records instead of opening anything."""
    gui = _bare_gui()
    gui.measurement_thread = None
    gui.file_location_path = tmp
    boxes = {"Sample Name": "S", "AC Bias": "1.0", "MTM": "0.5",
             "Delay": "0", "Geometry": "1.0", "Thickness": "1.0"}
    boxes.update(texts)
    gui.entries = {k: _Text(v) for k, v in boxes.items()}
    gui._parse_frequencies = lambda: [1000.0]
    gui.var_zero_range = types.SimpleNamespace(get=lambda: True)
    gui.fmt_pica_var = gui.fmt_windeta_var = types.SimpleNamespace(
        get=lambda: True)
    gui.geom_mode_var = types.SimpleNamespace(get=lambda: "area")
    gui.wire_combobox = _Text("2")
    gui.lakeshore_cb = _Text("GPIB0::12::INSTR")
    gui.lcr_cb = _Text("GPIB0::10::INSTR")
    gui.inits = []
    gui.backend = types.SimpleNamespace(
        initialize_instruments=gui.inits.append,
        close_instruments=lambda: None)
    return gui


def _start_error(gui):
    shown = []
    box = types.SimpleNamespace(
        showerror=lambda title, msg: shown.append(msg))
    with patch.object(mod, "messagebox", box):
        gui.start_measurement()
    return shown[0] if shown else None


def test_bad_boxes_are_refused_by_name_before_any_instrument():
    cases = [({"Delay": "inf"}, "Delay per Freq"),
             ({"Delay": "nan"}, "Delay per Freq"),
             ({"Delay": "1e9"}, "Delay per Freq"),
             ({"AC Bias": "1,0"}, 'AC Voltage (Vrms): "1,0"'),
             ({"MTM": ""}, "Integration Time (s)"),
             ({"Thickness": "nan"}, "Sample Thickness"),
             ({"Geometry": "inf"}, "Electrode Area"),
             ({"Sample Name": "a/b"}, "Sample Name"),
             ({"Sample Name": "S:1"}, "Sample Name"),
             ({"Sample Name": "S1."}, "Sample Name"),
             ({"Sample Name": "S-α"}, "cp1252")]
    with tempfile.TemporaryDirectory() as tmp:
        for texts, want in cases:
            gui = _start_gui(tmp, **texts)
            msg = _start_error(gui)
            assert msg and want in msg, (texts, msg)
            assert gui.inits == [], texts
            assert gui.statuses[-1].startswith("Status: START FAILED")
        assert os.listdir(tmp) == []                 # no orphan files
    gui = _start_gui(os.path.join(tmp, "unplugged"))
    assert "Destination folder not found" in _start_error(gui)
    assert gui.inits == []


def test_a_bad_frequency_token_is_named():
    gui = _bare_gui()
    gui.freq_text = _Text("1000, 1e3x")
    try:
        gui._parse_frequencies()
    except ValueError as exc:
        assert '"1e3x"' in str(exc)
    else:
        raise AssertionError("accepted 1e3x")


def test_nan_or_inf_geometry_is_refused():
    for bad in (_out_params(thickness_mm=float("nan")),
                _out_params(thickness_mm=float("inf")),
                _out_params(diameter_mm=float("nan")),
                _out_params(geometry_mode="area", area_mm2=float("inf"))):
        for fn in (mod.validate_output_settings, mod.c0_from_params):
            try:
                fn(bad)
            except ValueError:
                continue
            raise AssertionError(f"{fn.__name__} accepted {bad}")


class _StopDuringDelay:
    """stop_event whose wait() is where Stop arrives."""

    def __init__(self, log):
        self.log = log
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.log.append(("wait", timeout))
        self.stopped = True
        return True


def test_stop_during_the_delay_measures_nothing_more():
    fake = FakeAlpha()
    combined = _combined(fake)
    cycle = combined.measure_frequency_sweep(
        [1000.0, 2000.0], 30, _StopDuringDelay(combined.lakeshore.calls))
    assert cycle["points"] == []
    assert "MST" not in fake.commands


def test_each_point_reads_t_after_its_delay():
    class _Wait:
        def __init__(self, log):
            self.log = log

        def is_set(self):
            return False

        def wait(self, timeout):
            self.log.append(("wait", timeout))
            return False
    combined = _combined(FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 2 1"}))
    calls = combined.lakeshore.calls
    combined.measure_frequency_sweep([1000.0], 5, _Wait(calls))
    names = [c[0] for c in calls]
    assert names.index("wait") < names.index("get_temperature"), names


def test_a_failed_kill_is_reported_and_close_retries_range_0():
    fake_visa = FakePyvisa()
    with patch.object(mod, "pyvisa", fake_visa):
        ls = mod.Lakeshore350_Backend("GPIB0::12::INSTR", passive_mode=True)
    session = fake_visa.sessions[0]
    combined = mod.Combined_Backend()
    combined.lakeshore = ls
    good_write = session.write
    session.write = lambda cmd: (_ for _ in ()).throw(OSError("bus"))
    with patch.object(mod.time, "sleep", lambda s: None):
        assert combined.check_safety_kill(401.0) is True
        assert combined.heater_off_confirmed is False
        session.write = good_write
        ls.close()       # passive run, but a kill fired: RANGE 1,0 again
    assert session.writes == ["RANGE 1,0"]

    gui = _bare_gui()
    gui.backend = combined
    gui._update_live_plots = lambda force=False: None
    gui.stop_measurement = lambda from_user=True: None
    gui._beep = lambda times=1: None
    gui._handle_kill_event()
    assert any("NOT CONFIRMED" in m for m in gui.logged)
    assert "CHECK HEATER" in gui.statuses[-1]


def test_the_kill_switch_works_during_reconnect_backoff():
    h = _ReconnectHarness(OSError("Alpha off"))
    h.backend.lakeshore = FakeLakeshore((401.0,))
    h.backend.check_safety_kill = lambda t: t >= 400.0
    assert h.run() is False
    assert len(h.outcomes) == 1                  # no reconnect attempted
    assert h.data_queue.get_nowait() == "KILL"


def test_status_banner_keeps_how_the_run_ended():
    gui = _bare_gui()
    gui.backend = types.SimpleNamespace(SAFETY_KILL_TEMP_K=400.0,
                                        heater_off_confirmed=True,
                                        close_instruments=lambda: None)
    gui._update_live_plots = lambda force=False: None
    gui.stop_measurement = lambda from_user=True: None
    gui._beep = lambda times=1: None
    gui._handle_kill_event()
    assert gui.statuses[-1] == "Status: SAFETY KILL (400 K)"
    gui._handle_runtime_error(RuntimeError("status 6"))
    assert gui.statuses[-1] == "Status: ERROR - see console"
    # the finalize of an unattended stop never overwrites it
    gui._set_keep_awake = lambda on: None
    gui.start_button = gui.scan_button = _Recorder()
    gui._set_inputs_enabled = lambda enabled: None
    gui._drain_queue_at_stop = lambda: None
    gui._close_after_stop = False
    gui._finalize_stop(False)
    assert gui.statuses[-1] == "Status: ERROR - see console"


def test_run_settings_are_locked_during_a_run():
    gui = _bare_gui()
    gui.entries = {"Sample Name": _Text(), "Delay": _Text()}
    gui.freq_text = _Text()
    gui.geom_radios = [_Text(), _Text()]
    gui.fmt_checks = [_Text(), _Text()]
    gui.zero_range_check, gui.file_button = _Text(), _Text()
    gui.wire_combobox, gui.lakeshore_cb, gui.lcr_cb = (
        _Text(), _Text(), _Text())
    gui.plot_freq_cb = _Text()
    gui._set_inputs_enabled(False)
    gui._set_inputs_enabled(True)
    for w in (*gui.entries.values(), gui.freq_text, *gui.geom_radios,
              *gui.fmt_checks, gui.zero_range_check, gui.file_button):
        assert w.states == ["disabled", "normal"], w.states
    assert gui.lcr_cb.states == ["disabled", "readonly"]
    assert gui.plot_freq_cb.states == []         # live plot choice stays
    src = inspect.getsource(mod.Integrated_CT_GUI.start_measurement)
    assert "_set_inputs_enabled(False)" in src
    src = inspect.getsource(mod.Integrated_CT_GUI._finalize_stop)
    assert "_set_inputs_enabled(True)" in src


def test_closing_waits_for_a_worker_left_in_reconnect():
    gui = _bare_gui()
    gui.is_running = gui.is_stabilizing = gui._stopping = False
    alive = [True]
    gui.measurement_thread = types.SimpleNamespace(
        is_alive=lambda: alive[0])
    pending, destroyed, closed = [], [], []
    gui.root = types.SimpleNamespace(
        after=lambda ms, fn: pending.append(fn),
        destroy=lambda: destroyed.append(True))
    gui.backend = types.SimpleNamespace(
        close_instruments=lambda: closed.append(True))
    gui._on_closing()
    assert destroyed == [] and len(pending) == 1
    alive[0] = False
    pending.pop()()
    assert closed == [True] and destroyed == [True]


def test_a_same_name_t_log_is_never_truncated():
    with tempfile.TemporaryDirectory() as tmp:
        old = os.path.join(tmp, "S_T-log.txt")
        with open(old, "w") as fh:
            fh.write("run 1 history\n")
        gui = _files(tmp, frequencies=[100000.0])
        assert open(old).read() == "run 1 history\n"
        assert gui.t_log_path != old


def test_start_retries_last_runs_rows_before_dropping_them():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "old-1000Hz_pica_format.dat")
        gui = _start_gui(tmp)
        gui._pending_rows = mod.deque([(target, "row\n"),
                                       (os.path.join(tmp, "gone", "x"),
                                        "lost\n")])
        gui._write_error_logged = True
        gui.windeta_filepaths = {}

        def _stop_here(params):
            raise ValueError("stop the start here")
        gui._create_per_frequency_files = _stop_here
        # The retry now runs on CONNECTED, after the instruments answered.
        gui.stop_event = threading.Event()
        gui.is_stabilizing = True
        gui._run_params = {"sample_name": "S"}
        try:
            gui._on_connected()
        except ValueError:
            pass
        else:
            raise AssertionError("the file step was expected to stop it")
        assert open(target).read() == "row\n"
    assert not gui._pending_rows
    assert any("1 row(s) from the previous run" in m for m in gui.logged)


def test_auto_scale_never_hides_negative_points():
    class _Ax:
        def __init__(self):
            self.scales = []

        def set_yscale(self, s):
            self.scales.append(s)

        def __getattr__(self, name):
            return lambda *a, **k: None
    gui = _bare_gui()
    gui._decade_ylims, gui._log_hidden_warned = {}, set()
    gui.y_scale_var = types.SimpleNamespace(get=lambda: "auto")
    ax = _Ax()
    assert gui._apply_y_scale(ax, [1e-12, 1e-9, -1e-11], "cp") is False
    assert ax.scales == ["linear"]
    gui.y_scale_var = types.SimpleNamespace(get=lambda: "log")
    assert gui._apply_y_scale(ax, [1e-12, 1e-9, -1e-11], "cp") is True
    gui._apply_y_scale(ax, [1e-12, 1e-9, -1e-11], "cp")
    assert len([m for m in gui.logged if "hides 1 point" in m]) == 1


def test_one_temperature_still_gets_a_visible_x_range():
    limits = []
    gui = _bare_gui()
    gui.plot_freq = 1000.0
    gui.data_storage = {"cp": {1000.0: {"T": [80.0], "v": [1e-12]}}}
    gui._apply_y_scale = lambda *a: False
    gui.ax_main = types.SimpleNamespace(
        set_xlim=lambda lo, hi: limits.append((lo, hi)))
    gui._rescale_main_axis()
    assert limits == [(79.5, 80.5)]


def test_panel_theme_and_wheel_are_wired():
    src = open(PATH, encoding="utf-8").read()
    assert 'option_add("*Label.background"' in src
    assert "'TRadiobutton'" in src and "'Graph.TRadiobutton'" in src
    assert 'bind_all("<MouseWheel>"' in src


# --- connect / initialise on the worker thread (27 Oct 2026) ---------------

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


def _connect_gui(**attrs):
    """A bare GUI with the widgets the Start / stop paths touch."""
    gui = _bare_gui()
    gui.stop_event = threading.Event()
    gui._files_ready = threading.Event()
    gui.data_queue = queue.Queue()
    gui._pending_rows = mod.deque()
    gui._write_error_logged = False
    gui._stopping = gui._close_after_stop = False
    gui.is_running = gui.is_stabilizing = False
    gui.frequencies = [1000.0]
    gui.locks, gui.awake = [], []
    gui._set_inputs_enabled = gui.locks.append
    gui._set_keep_awake = gui.awake.append
    gui._beep = lambda times=1: None
    gui.start_button, gui.stop_button, gui.scan_button = (
        _Text(), _Text(), _Text())
    for k, v in attrs.items():
        setattr(gui, k, v)
    return gui


def test_start_on_the_tk_thread_never_connects():
    _FakeThread.made = []
    afters = []
    with tempfile.TemporaryDirectory() as tmp:
        gui = _start_gui(tmp)
        gui.stop_event = threading.Event()
        gui._files_ready = threading.Event()
        gui.data_storage = {}
        gui._decade_ylims, gui._log_hidden_warned = set(), set()
        gui.ax_main = gui.line_main = gui.line_sub1 = gui.line_sub2 = (
            _Recorder())
        gui.canvas = _Recorder()
        gui.start_button, gui.stop_button, gui.scan_button = (
            _Text(), _Text(), _Text())
        gui.locks, gui.awake = [], []
        gui._set_inputs_enabled = gui.locks.append
        gui._set_keep_awake = gui.awake.append
        gui.root = types.SimpleNamespace(
            after=lambda ms, fn: afters.append(fn))
        with patch.object(mod.threading, "Thread", _FakeThread), \
                patch.object(mod, "messagebox", None):
            gui.start_measurement()
        files = os.listdir(tmp)
    assert gui.inits == [], "Start must not connect on the Tk thread"
    assert files == [], "files are created only after CONNECTED"
    (thread,) = _FakeThread.made
    assert thread.started and thread.target == gui._run_worker
    assert thread.args[0]["lcr_visa"] == "GPIB0::10::INSTR"
    assert gui.statuses[-1].startswith("Status: CONNECTING")
    assert gui.locks == [False] and gui.awake == [True]
    assert gui.is_stabilizing and not gui.is_running
    assert gui.start_button.states[-1] == "disabled"
    assert gui.stop_button.states[-1] == "normal"
    assert afters == [gui._process_data_queue]


def test_stop_during_the_alpha_reset_wait_ends_the_worker_quickly():
    from alphaan_port_test_support import ALPHA_IDN, FakeRM, FakeVisaError
    fake = FakeAlpha({"*IDN?": [ALPHA_IDN] + [
        FakeVisaError(mod.VI_ERROR_TMO)] * 100000})
    combined = mod.Combined_Backend()
    combined.lcr = mod.AlphaAN_Backend.__new__(mod.AlphaAN_Backend)
    combined.lcr.instrument = None
    combined.lcr.params = {}
    combined.lcr.rm = FakeRM(fake)
    combined.lcr._task_may_be_running = False
    visa = FakePyvisa()
    gui = _connect_gui(backend=combined)
    with patch.object(mod, "pyvisa", visa), \
            patch.object(mod.time, "sleep", lambda s: None), \
            patch("builtins.print", lambda *a, **k: None):
        worker = threading.Thread(target=gui._run_worker,
                                  args=(dict(PARAMS),), daemon=True)
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
        (session,) = visa.sessions
        assert session.writes == ["RANGE 1,0"], session.writes  # zero range
        assert "MODE=IMP" not in fake.commands
        msgs = []
        while not gui.data_queue.empty():
            msgs.append(gui.data_queue.get_nowait())
        assert all(isinstance(m, str) for m in msgs), msgs
        assert any("Stopped while connecting" in m for m in msgs), msgs
        # The normal stop path then closes what was opened, safely.
        combined.close_instruments()
    assert session.writes == ["RANGE 1,0", "RANGE 1,0"] and session.closed
    assert not any(w.startswith(("*RST", "HTRSET", "RAMP", "SETP"))
                   for w in session.writes), session.writes
    # The Alpha was still rejoining: nothing but the rejoin *IDN? polls
    # goes to it after the *RST, and the session is simply closed.
    after = fake.commands[fake.commands.index("*RST") + 1:]
    assert set(after) == {"*IDN?"}, after
    assert fake.closed and combined.lcr.instrument is None


class _GoneAfterReset(FakeAlpha):
    """An Alpha that stops answering once *RST is written: every later
    transfer hangs (64-bit Keysight VISA) for 0.5 s and then times out."""

    def __init__(self):
        super().__init__()
        self.reset = False

    def write(self, command):
        if self.reset:
            self.traffic.append(("write", command))
            time.sleep(0.5)
            from alphaan_port_test_support import FakeVisaError
            raise FakeVisaError(mod.VI_ERROR_TMO)
        super().write(command)
        if command == "*RST":
            self.reset = True

    def query(self, command):
        if self.reset:
            self.traffic.append(("query", command))
            from alphaan_port_test_support import FakeVisaError
            if command != "*IDN?":
                time.sleep(0.5)
            raise FakeVisaError(mod.VI_ERROR_TMO)
        return super().query(command)


def test_stop_during_the_reset_rejoin_closes_without_talking_to_the_alpha():
    fake = _GoneAfterReset()
    backend, _ = connected_backend(mod, fake)
    backend.stop_event = threading.Event()
    timer = threading.Timer(0.2, backend.stop_event.set)    # Stop pressed
    timer.start()
    lines = []
    try:
        with patch("builtins.print", lambda *a, **k: lines.append(
                " ".join(str(x) for x in a))):
            try:
                backend.initialize_instrument({})
            except mod.StopRequested:
                pass
            else:
                raise AssertionError("Stop must end the *RST rejoin wait")
            assert backend._alpha_rebooting
            started = time.monotonic()
            backend.close_instrument()
            took = time.monotonic() - started
    finally:
        timer.cancel()
    assert took < 0.3, took
    assert fake.commands == ["*RST"], fake.commands  # nothing after *RST
    assert fake.closed and backend.instrument is None
    assert not backend._alpha_rebooting
    assert any("Alpha still resetting after *RST - closing the session "
               "without shutdown commands" in m for m in lines), lines
    backend.close_instrument()                  # idempotent, never raises


def test_a_completed_rejoin_gets_the_normal_shutdown():
    backend, fake = connected_backend(mod)
    with patch.object(mod.time, "sleep", lambda s: None),             patch("builtins.print", lambda *a, **k: None):
        backend._reset_and_set_impedance_mode()
        assert not backend._alpha_rebooting
        backend.close_instrument()
    after = fake.commands[fake.commands.index("MODE=IMP") + 1:]
    assert after == ["ACV=0", "ZCONSPL=0", "*RST"], after
    assert fake.closed


def test_a_connect_failure_resets_the_ui_and_shows_error():
    def fail(params, stop_event=None):
        raise ConnectionError("The Alpha did not answer")
    closed, afters = [], []
    gui = _connect_gui(
        backend=types.SimpleNamespace(
            initialize_instruments=fail, params={},
            close_instruments=lambda: closed.append(True)),
        is_stabilizing=True,
        root=types.SimpleNamespace(after=lambda ms, fn: afters.append(fn)))
    gui.measurement_thread = threading.Thread(
        target=gui._run_worker, args=(dict(PARAMS),), daemon=True)
    gui.measurement_thread.start()
    gui.measurement_thread.join(5.0)
    assert not gui.measurement_thread.is_alive()
    shown = []
    box = types.SimpleNamespace(
        showerror=lambda *a: shown.append(("showerror", a)),
        showinfo=lambda *a: shown.append(("showinfo", a)))
    with patch.object(mod, "messagebox", box):
        gui._process_data_queue()
    assert any(s.startswith("Status: ERROR") and "did not answer" in s
               for s in gui.statuses), gui.statuses
    assert gui.statuses[-1].startswith("Status: ERROR")
    assert any("ERROR during startup" in m and "Traceback" in m
               for m in gui.logged)
    assert gui.locks == [True] and closed == [True]
    assert gui.awake == [False]
    assert gui.start_button.states[-1] == "normal"
    assert not (gui.is_running or gui.is_stabilizing)
    assert gui.stop_event.is_set() and not gui._files_ready.is_set()
    assert [c[0] for c in shown] == ["showerror"]   # at Start
    assert "did not answer" in shown[0][1][1]
    assert afters == [], "the pump must not keep running"


def test_stop_before_connected_is_handled_creates_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _connect_gui(file_location_path=tmp)
        gui.stop_event.set()
        gui._on_connected()
        assert os.listdir(tmp) == []
    assert not gui._files_ready.is_set() and not gui.is_running


def test_the_worker_waits_for_the_files_before_the_first_sweep():
    order = []
    gui = _connect_gui(backend=types.SimpleNamespace(
        initialize_instruments=lambda p, ev: order.append("init")))
    gui._measurement_worker = lambda: order.append("run")
    worker = threading.Thread(target=gui._run_worker, args=({},),
                              daemon=True)
    worker.start()
    assert gui.data_queue.get(timeout=5.0) == "CONNECTED"
    assert order == ["init"]                  # no sweep before the files
    gui._files_ready.set()
    worker.join(5.0)
    assert order == ["init", "run"] and not worker.is_alive()


def test_connected_creates_the_files_and_starts_the_run():
    params = _out_params()
    with tempfile.TemporaryDirectory() as tmp:
        gui = _connect_gui(file_location_path=tmp, is_stabilizing=True,
                           _run_params=params,
                           frequencies=params["frequencies"],
                           c0_farads=mod.c0_from_params(params),
                           plot_freq=1000.0, ax_main=_Recorder(),
                           canvas=_Recorder())
        gui._populate_plot_freq_dropdown = lambda: None
        gui._on_connected()
        assert gui._files_ready.is_set()
        assert gui.is_running and not gui.is_stabilizing
        assert gui.statuses[-1] == "Status: RUNNING"
        assert len(gui.freq_filepaths) == 2
        assert all(os.path.exists(p) for p in gui.freq_filepaths.values())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
