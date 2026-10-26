"""Tests for novocontrol/Temprature_Scan_Passive_L340_AlphaAN_GUI.py
(Alpha-AN + Lakeshore 340 as a passive thermometer).

Runnable as plain Python as well as under pytest. No VISA, no Tk window.
"""

import inspect
import math
import os
import queue
import re
import sys
import tempfile
import threading
import time
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    FakeAlpha, connected_backend, load_port, run_common_backend_checks,
)

mod, PATH = load_port("Temprature_Scan_Passive_L340_AlphaAN_GUI.py")


# --- a recording Lakeshore 340 VISA session --------------------------------

class FakeL340Session:
    """Stands in for the pyvisa resource behind Lakeshore340_Backend."""

    def __init__(self, idn="LSCI,MODEL340,340123,061407", temps=(300.0,),
                 rdgst=0):
        self.idn = idn
        self.temps = list(temps)
        self.rdgst = rdgst
        self.range = 3
        self.traffic = []
        self.timeout = 0
        self.read_termination = None
        self.write_termination = None
        self.closed = False

    def write(self, cmd):
        self.traffic.append(("write", cmd))
        if cmd == "RANGE 0":
            self.range = 0

    def query(self, cmd):
        self.traffic.append(("query", cmd))
        if cmd == "*IDN?":
            return self.idn
        if cmd.startswith("KRDG?"):
            t = self.temps.pop(0) if len(self.temps) > 1 else self.temps[0]
            return f"{t}"
        if cmd.startswith("RDGST?"):
            return str(self.rdgst)
        if cmd == "HTR?":
            return "0.0"
        if cmd == "RANGE?":
            return str(self.range)
        return "0"

    def close(self):
        self.closed = True

    @property
    def writes(self):
        return [c for k, c in self.traffic if k == "write"]


class FakePyvisa:
    """Module-level pyvisa replacement handing out FakeL340Sessions."""

    def __init__(self, **session_kw):
        self.sessions = []
        self.session_kw = session_kw

    def ResourceManager(self):
        outer = self

        class _RM:
            def open_resource(self, addr):
                s = FakeL340Session(**outer.session_kw)
                outer.sessions.append(s)
                return s
        return _RM()


PARAMS = {"lakeshore_visa": "GPIB0::19::INSTR",
          "lcr_visa": "GPIB0::10::INSTR", "frequencies": [1000.0],
          "acv": 1.0, "mtm": 0.5, "wire_mode": "2", "zero_range": True,
          "sensor": "A", "delay": 0}


def _combined(fake_alpha=None, **session_kw):
    """Combined backend on a real Lakeshore340_Backend over a fake session."""
    combined = mod.Combined_Backend()
    combined.lcr, _ = connected_backend(mod, fake_alpha)
    fake_visa = FakePyvisa(**session_kw)
    with patch.object(mod, "pyvisa", fake_visa):
        combined.lakeshore = mod.Lakeshore340_Backend(PARAMS["lakeshore_visa"])
    combined.params = dict(PARAMS)
    return combined, fake_visa.sessions[0]


# --- tests --------------------------------------------------------------------

def test_common_alpha_backend_rules():
    run_common_backend_checks(mod, PATH)


def test_every_point_carries_its_own_temperature_and_the_actual_frequency():
    fake = FakeAlpha({"ZRE?": ["ZRE=1E5 -2E6 1001.5 2 1",
                               "ZRE=1E5 -2E6 2003.0 2 1"]})
    combined, ls = _combined(fake, temps=(300.0, 301.0))
    cycle = combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    (t1, f1, _, _, s1, fa1), (t2, f2, _, _, s2, fa2) = cycle["points"]
    assert (t1, t2) == (300.0, 301.0)
    assert (f1, f2) == (1000.0, 2000.0)          # keys the per-freq file
    assert (fa1, fa2) == (1001.5, 2003.0)        # used for the conversion
    assert s1 == s2 == mod.STATUS_RESULT_VALID
    assert ls.writes == []                       # a sweep only reads
    assert ("query", "RDGST? A") in ls.traffic


def test_reading_status_is_still_collected():
    combined, _ = _combined(FakeAlpha(), rdgst=2)
    cycle = combined.measure_frequency_sweep([1000.0], 0)
    assert cycle["reading_status"] == [(300.0, 1000.0, 2)]
    assert cycle["last_reading_status"] == 2


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
    gui._save_cycle_to_files(
        {"points": [(300.0, 1000.0, 1e5, -2e6, 2, 1001.5)]})
    assert seen == [1001.5] and rows[0][0] == "f1000"
    assert len(rows[0][1].split("\t")) == 19     # T + 18 legacy columns


def test_a_disconnected_signal_source_aborts_the_sweep():
    combined, _ = _combined(FakeAlpha({"ZRE?": "ZRE=1E5 -2E6 1000 6 1"}))
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
    combined, ls = _combined(fake, temps=(401.0,))
    cycle = combined.measure_frequency_sweep([1000.0, 2000.0], 0)
    assert len(cycle["points"]) == 1
    assert math.isnan(cycle["points"][0][2])
    assert "MST" not in fake.commands
    assert ls.writes == ["RANGE 0"]               # verified with RANGE?


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


def test_passive_init_sends_only_cls_and_the_optional_range_0():
    for zero_range, expected in ((True, ["*CLS", "RANGE 0"]),
                                 (False, ["*CLS"])):
        combined = mod.Combined_Backend()
        combined.lcr, fake = connected_backend(mod)
        fake_visa = FakePyvisa()
        with patch.object(mod, "pyvisa", fake_visa):
            combined.initialize_instruments(
                {**PARAMS, "zero_range": zero_range})
        assert fake_visa.sessions[0].writes == expected
        assert fake.kind_of("ACV=1") == "query"     # Alpha configured


def test_a_non_340_is_refused_before_the_alpha_is_touched():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    fake_visa = FakePyvisa(idn="LSCI,MODEL350,X,1")
    with patch.object(mod, "pyvisa", fake_visa):
        try:
            combined.initialize_instruments(dict(PARAMS))
        except ConnectionError:
            pass
        else:
            raise AssertionError("a Model 350 must be refused")
    assert fake_visa.sessions[0].writes == [] and fake.commands == []


def test_reconnect_sends_nothing_to_the_controller():
    combined = mod.Combined_Backend()
    combined.lcr, fake = connected_backend(mod)
    fake_visa = FakePyvisa()
    with patch.object(mod, "pyvisa", fake_visa):
        combined.initialize_instruments(dict(PARAMS))   # RANGE 0 ticked
        first = fake_visa.sessions[0]
        fake.traffic.clear()
        combined.reconnect()
    assert first.writes == ["*CLS", "RANGE 0"]   # start only
    assert first.closed
    second = fake_visa.sessions[1]
    assert second.traffic == [("query", "*IDN?")], second.traffic
    assert combined.lakeshore.instrument is second
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


# Model 350 forms and *RST must never reach the 340 (same rule as
# tests/test_lakeshore_340_siblings.py, applied to this port).
_FORBIDDEN_350 = [
    re.compile(r"HTR\?\s*[12]\b"), re.compile(r"RANGE\s+[12]\s*,"),
    re.compile(r"RANGE\?\s*[12]\b"), re.compile(r"\bHTRSET\b"),
    re.compile(r"MODEL\s?350"),
]
_LAKESHORE_LINE = re.compile(
    r"(?i)lakeshore|lake_shore|_ls\b|\bls_|\bls\.|l340|\b340\b")


def test_only_340_forms_and_no_rst_to_the_lakeshore():
    src = open(PATH, encoding="utf-8").read()
    src = re.sub('"""[\\s\\S]*?"""', '""', src)
    assert "::19::" in src and "MODEL340" in src.upper().replace(" ", "")
    for i, line in enumerate(src.splitlines(), start=1):
        if line.strip().startswith("#"):
            continue
        code = line.split("  # ")[0]
        for rx in _FORBIDDEN_350:
            assert not rx.search(code), f"{i}: {code.strip()}"
        if "*RST" in code:
            assert not _LAKESHORE_LINE.search(code), f"{i}: {code.strip()}"


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


def _sweep_backend(fake):
    return _combined(fake)[0]

# --- pre-lab fixes (23 Oct 2026) ------------------------------------------

class _Recorder:
    """Accepts any attribute call (stop_button.config, ax.set_title...)."""

    def __getattr__(self, name):
        return lambda *a, **k: None


def _bare_gui():
    gui = mod.Integrated_CT_GUI.__new__(mod.Integrated_CT_GUI)
    gui.logged = []
    gui.log = gui.logged.append
    gui.status = []
    gui.lbl_status = types.SimpleNamespace(
        config=lambda **k: gui.status.append(k["text"]))
    gui.root = _Recorder()
    gui.start_button = gui.stop_button = _Recorder()
    gui._run_locked_widgets = []
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
    _kill_check_during_outage = (
        mod.Integrated_CT_GUI._kill_check_during_outage)

    def __init__(self, *outcomes):
        self.is_running = True
        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()
        self.outcomes = list(outcomes)
        self.closed = []
        outer = self

        class _B:
            lakeshore = None              # no Lakeshore session either
            params = {"sensor": "A"}

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
                time=lambda: next(clock), sleep=lambda s: None)):
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


# --- Output formats: PICA (.dat) and WinDETA Tscan (.txt) ------------------

L340_ADDR = "GPIB0::19::INSTR"
ALPHA_ADDR = "GPIB0::10::INSTR"


def _out_params(**over):
    p = {"sample_name": "S", "acv": 0.5, "mtm": 0.5, "delay": 0.0,
         "wire_mode": "2", "frequencies": [1000.0, 2000.0],
         "lcr_visa": ALPHA_ADDR, "lakeshore_visa": L340_ADDR,
         "sensor": "A", "zero_range": True,
         "geometry_mode": "diameter", "diameter_mm": 9.68,
         "thickness_mm": 1.14,
         "temp_program": "Passive, read only: Lakeshore 340 input A",
         "save_pica": True, "save_windeta": True}
    p.update(over)
    return p


def _files(tmp, **over):
    """A GUI shell with its per-frequency files created in tmp."""
    params = _out_params(**over)
    gui = _bare_gui()
    gui.file_location_path = tmp
    gui.frequencies = params["frequencies"]
    gui._pending_rows = mod.deque()
    gui._write_error_logged = False
    gui.c0_farads = mod.c0_from_params(params)
    gui._create_per_frequency_files(params)
    return gui


def test_per_frequency_files_do_not_collide_below_1_hz():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[0.1, 0.5, 1e6], save_windeta=False)
        names = sorted(os.path.basename(p)
                       for p in gui.freq_filepaths.values())
        listing = sorted(os.listdir(tmp))
        tlog = open(gui.t_log_path, encoding="utf-8").read()
    assert names == ["S-0.1Hz_pica_format.dat", "S-0.5Hz_pica_format.dat",
                     "S-1000000Hz_pica_format.dat"]
    assert gui.windeta_filepaths == {}       # unticked format: no files
    assert listing == names + ["S_T-log.txt"]          # T-log still made
    assert tlog.startswith("DateTime\tElapsed_s\tTemperature_K")


def test_an_existing_file_of_either_format_gets_a_timestamped_tag():
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "S-1000Hz_windeta_format.txt"), "w").close()
        gui = _files(tmp)
        names = [os.path.basename(p) for p in
                 list(gui.freq_filepaths.values())
                 + list(gui.windeta_filepaths.values()) + [gui.t_log_path]]
    assert all(n.startswith("S_") for n in names), names


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
    assert kill.splitlines()[-1].startswith("4.240000E+02\tNAN")


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


def test_a_windeta_write_failure_is_buffered_and_replayed_as_cp1252_crlf():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[1000.0])
        y = 1j * 2 * math.pi * 1000.0 * gui.c0_farads * (13 - 2j)
        z = 1 / y
        point = (300.0, 1000.0, z.real, z.imag, mod.STATUS_RESULT_VALID,
                 1000.0)
        real_write = gui._durable_write
        gui._durable_write = lambda *a, **k: (_ for _ in ()).throw(
            OSError("share gone"))
        gui._save_cycle_to_files({"points": [point]})
        assert len(gui._pending_rows) == 2             # PICA + WinDETA row
        assert [r[2] for r in gui._pending_rows] == [False, True]
        gui._durable_write = real_write
        gui._save_cycle_to_files({"points": []})       # next cycle flushes
        assert not gui._pending_rows
        raw = open(gui.windeta_filepaths[1000.0], "rb").read()
    assert raw.endswith(b"2.68500e+01\t1.30000e+01\t2.00000e+00"
                        b"\t1.53846e-01\t-8.12538e+01\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_area_mode_writes_the_equivalent_diameter():
    lines = mod.windeta_tscan_header_lines(
        _out_params(geometry_mode="area", area_mm2=math.pi * 25.0), 1e3,
        mod.datetime(2026, 1, 15, 11, 35))
    assert lines[0] == "S, 15.01.2026, 11:35"
    assert lines[1].startswith("Diameter[mm] 1.000e+01  ")


def test_pica_files_start_with_ascii_metadata_rows():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, geometry_mode="area", area_mm2=50.0)
        raw = open(gui.freq_filepaths[2000.0], "rb").read()
    raw.decode("ascii")
    lines = raw.decode("ascii").splitlines()
    meta = [ln for ln in lines if ln.startswith("#")]
    assert lines[len(meta)] == mod.Integrated_CT_GUI.DATA_HEADER
    assert len(lines[len(meta)].split("\t")) == 19
    joined = "\n".join(meta)
    for want in ("Fixed frequency: 2000 Hz", "A = 50.0 mm^2",
                 f"C0 = {gui.c0_farads:.5e} F", "Evaluation : Area electrode",
                 "ZREFMODE=-3", "ZSLCAL=1", "ZLLCOR=1", "DRS=0 0",
                 "Passive, read only: Lakeshore 340 input A", ALPHA_ADDR):
        assert want in joined, want


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


def _never_on_the_tk_thread(*a, **k):
    raise AssertionError("Start must not connect on the Tk thread")


def _run_start(gui):
    """Run Start with a fake Thread and recorded dialogs. Returns
    (params handed to the worker, showerror calls)."""
    for name in ("line_main", "line_sub1", "line_sub2", "ax_main",
                 "canvas"):
        setattr(gui, name, _Recorder())
    gui.data_storage, gui._decade_ylims = {}, {}
    gui._settle_previous_run_rows = lambda: None
    gui.stop_event, gui._files_ready = threading.Event(), threading.Event()
    _FakeThread.made = []
    shown = []
    with patch.object(mod, "messagebox", types.SimpleNamespace(
            showerror=lambda *a, **k: shown.append(a))),             patch.object(mod.threading, "Thread", _FakeThread):
        gui.start_measurement()
    return [t.args[0] for t in _FakeThread.made], shown


def _start_gui(geometry="1.0", save_pica=True, save_windeta=True,
               zero_range=True):
    """A GUI shell whose start_measurement runs up to the worker launch;
    seen = the params the worker thread is handed (nothing connects)."""
    def _var(value):
        return types.SimpleNamespace(get=lambda: value)

    gui = _bare_gui()
    gui.measurement_thread = None
    gui.file_location_path = tempfile.gettempdir()
    gui._parse_frequencies = lambda: [1000.0]
    gui.entries = {"Sample Name": _var("S"), "AC Bias": _var("0.5"),
                   "MTM": _var("0.5"), "Delay": _var("0"),
                   "Geometry": _var(geometry), "Thickness": _var("1.0")}
    gui.var_zero_range = _var(zero_range)
    gui.sensor_var = _var("B")
    gui.wire_combobox = _var("2")
    gui.lakeshore_cb = _var(L340_ADDR)
    gui.lcr_cb = _var(ALPHA_ADDR)
    gui.geom_mode_var = _var("area")
    gui.fmt_pica_var = _var(save_pica)
    gui.fmt_windeta_var = _var(save_windeta)
    gui.backend = types.SimpleNamespace(
        initialize_instruments=_never_on_the_tk_thread,
        close_instruments=lambda: None)
    seen, shown = _run_start(gui)
    return gui, seen, shown


def test_start_checks_geometry_and_formats_before_any_instrument():
    for kw in ({"geometry": "0"}, {"save_pica": False,
                                   "save_windeta": False}):
        _, seen, shown = _start_gui(**kw)
        assert seen == [] and len(shown) == 1, kw    # refused at Start


def test_start_describes_the_passive_temperature_program_truthfully():
    gui, seen, _ = _start_gui()
    params = seen[0]
    assert params["area_mm2"] == 1.0 and "diameter_mm" not in params
    assert math.isclose(gui.c0_farads, mod.compute_c0_from_area(1.0, 1.0))
    text = params["temp_program"]
    assert text.isascii()
    assert "Lakeshore 340 input B" in text and L340_ADDR in text
    assert "turned off at Start (RANGE 0)" in text and "Ramp" not in text
    _, seen, _ = _start_gui(zero_range=False)
    assert "heater left as found" in seen[0]["temp_program"]


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


# --- review fixes (23 Oct 2026): refused Alpha, cut-short sweeps -------------

def test_a_refused_alpha_address_gets_nothing_at_close_either():
    fake = FakeAlpha({"*IDN?": "LSCI,MODEL340,123456,061407"})
    backend, _ = connected_backend(mod, fake)
    backend.instrument = None
    try:
        backend.connect(L340_ADDR)
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
            "heater": 5.0, "reading_status": [],
            "last_reading_status": 0,
            "wall_time": mod.datetime.now()}, 12.0))
        gui.data_queue.put("KILL")
        gui.start_button = gui.scan_button = _Recorder()
        gui.heater_off_cb = gui.sensor_cb = _Recorder()
        gui.backend = types.SimpleNamespace(
            close_instruments=lambda: closed.append(True),
            kill_heater_confirmed=True)
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
    assert "\t12.0\t300.0000\t5.00\t0" in t_log
    assert len(synced) == 3                    # PICA, WinDETA, T-log
    assert any("SAFETY KILL" in m for m in gui.logged)
    assert gui.data_queue.empty() and closed == [True]


def test_a_disk_hiccup_row_is_retried_once_more_at_stop():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp)
        path = gui.freq_filepaths[1000.0]
        gui._pending_rows.append((path, "buffered\n", False))
        gui.data_queue = queue.Queue()
        gui._drain_queue_at_stop()
        text = open(path).read()
    assert text.endswith("buffered\n") and not gui._pending_rows


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


# --- sibling bug hunt (27 Oct 2026) -----------------------------------------

def _start_refusal(**entries):
    """Run Start with some entries overridden; (gui, inits, dialogs)."""
    return _start_gui_with(entries)


def _start_gui_with(entries, folder=None, sample="S", windeta=True):
    def _var(value):
        return types.SimpleNamespace(get=lambda: value)
    gui = _bare_gui()
    gui.measurement_thread = None
    gui.file_location_path = folder or tempfile.gettempdir()
    gui._parse_frequencies = lambda: [1000.0]
    values = {"Sample Name": sample, "AC Bias": "0.5", "MTM": "0.5",
              "Delay": "0", "Geometry": "1.0", "Thickness": "1.0"}
    values.update(entries)
    gui.entries = {k: _var(v) for k, v in values.items()}
    gui.var_zero_range = _var(True)
    gui.sensor_var = _var("A")
    gui.wire_combobox = _var("2")
    gui.lakeshore_cb = _var(L340_ADDR)
    gui.lcr_cb = _var(ALPHA_ADDR)
    gui.geom_mode_var = _var("area")
    gui.fmt_pica_var = _var(True)
    gui.fmt_windeta_var = _var(windeta)
    gui.backend = types.SimpleNamespace(
        initialize_instruments=_never_on_the_tk_thread,
        close_instruments=lambda: None)
    seen, shown = _run_start(gui)
    return gui, seen, shown


def test_delay_inf_nan_and_huge_are_refused_before_any_instrument():
    for text in ("inf", "nan", "1e10", "-1"):
        _, seen, shown = _start_refusal(Delay=text)
        assert seen == [] and len(shown) == 1, text
        assert "Delay per Freq" in shown[0][1], shown
    _, seen, _ = _start_refusal(Delay="3600")
    assert len(seen) == 1                       # the cap itself is fine


def test_nan_or_inf_geometry_never_reaches_c0():
    for key in ("Geometry", "Thickness"):
        for text in ("nan", "inf"):
            _, seen, shown = _start_refusal(**{key: text})
            assert seen == [] and len(shown) == 1, (key, text)
    for bad in (_out_params(thickness_mm=float("nan")),
                _out_params(thickness_mm=float("inf")),
                _out_params(diameter_mm=float("nan")),
                _out_params(geometry_mode="area", area_mm2=float("inf"))):
        try:
            mod.validate_output_settings(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    for fn in (mod.compute_c0, mod.compute_c0_from_area):
        try:
            fn(1.0, float("nan"))
        except ValueError:
            continue
        raise AssertionError(fn.__name__)


def test_a_bad_number_names_its_box():
    for key, label in (("MTM", "Integration Time"),
                       ("Thickness", "Sample Thickness"),
                       ("AC Bias", "AC Voltage"),
                       ("Geometry", "Electrode Area")):
        _, seen, shown = _start_refusal(**{key: "1,5"})
        assert seen == [] and label in shown[0][1], (key, shown)
    gui = _bare_gui()
    gui.freq_text = types.SimpleNamespace(get=lambda *a: "1000, 1e3x")
    try:
        gui._parse_frequencies()
    except ValueError as exc:
        assert '"1e3x"' in str(exc)
    else:
        raise AssertionError("a bad frequency token must be named")


def test_save_folder_and_sample_name_are_checked_before_any_instrument():
    with tempfile.TemporaryDirectory() as tmp:
        gone = os.path.join(tmp, "unplugged")
        cases = ({"folder": gone}, {"sample": "S1/annealed"},
                 {"sample": "S:1"}, {"sample": "S1."},
                 {"sample": "S\u03c81"})
        for kw in cases:
            _, seen, shown = _start_gui_with({}, **{"folder": tmp, **kw})
            assert seen == [] and len(shown) == 1, kw
        assert os.listdir(tmp) == []            # nothing half-created
        # a non-cp1252 name is fine when no WinDETA file is written
        _, seen, _ = _start_gui_with({}, folder=tmp, sample="S\u03c81",
                                     windeta=False)
        assert len(seen) == 1


def test_a_failed_start_shows_it_in_the_status_banner():
    gui, _, _ = _start_refusal(Delay="inf")
    assert gui.status[0].startswith("STARTING")
    assert gui.status[-1].startswith("START FAILED")
    assert not gui.is_running


def test_stop_during_the_per_point_delay_measures_nothing():
    class _StopsDuringWait(threading.Event):
        def wait(self, timeout=None):
            self.set()
            return True
    fake = FakeAlpha()
    combined, ls = _combined(fake)
    cycle = combined.measure_frequency_sweep([1000.0], 5.0,
                                             _StopsDuringWait())
    assert cycle["points"] == [] and "MST" not in fake.commands
    assert not any(c.startswith("KRDG?") for _, c in ls.traffic)


def test_the_temperature_is_read_after_the_per_point_delay():
    fake = FakeAlpha()
    combined, ls = _combined(fake)

    class _Marks(threading.Event):
        def wait(self, timeout=None):
            ls.traffic.append(("wait", timeout))
            return False
    combined.measure_frequency_sweep([1000.0], 2.0, _Marks())
    kinds = [c for _, c in ls.traffic]
    assert kinds.index(2.0) < kinds.index("KRDG? A"), ls.traffic


def test_a_failed_kill_says_the_heater_is_not_confirmed_off():
    combined, _ = _combined(FakeAlpha())

    def _broken():
        raise OSError("GPIB gone")
    combined.lakeshore.heater_off = _broken
    with patch.object(mod.time, "sleep", lambda s: None):
        assert combined.check_safety_kill(401.0) is True
    assert combined.kill_heater_confirmed is False
    gui = _bare_gui()
    gui.backend = combined
    assert "NOT CONFIRMED OFF" in gui._kill_heater_text()
    combined2, _ = _combined(FakeAlpha())
    combined2.check_safety_kill(401.0)
    assert combined2.kill_heater_confirmed is True
    gui.backend = combined2
    assert "RANGE? = 0" in gui._kill_heater_text()


def test_the_kill_switch_still_runs_while_the_alpha_is_unreachable():
    combined, ls = _combined(FakeAlpha(), temps=(405.0,))
    h = _ReconnectHarness(OSError("Alpha off"))
    h.backend.lakeshore = combined.lakeshore
    h.backend.params = {"sensor": "A"}
    h.backend.check_safety_kill = combined.check_safety_kill
    assert h.run() is False
    assert len(h.outcomes) == 1                 # no reconnect attempted
    assert "KILL" in [h.data_queue.get_nowait()
                      for _ in range(h.data_queue.qsize())]
    assert ls.writes == ["RANGE 0"]


def test_start_retries_then_reports_rows_left_by_the_previous_run():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _files(tmp, frequencies=[1000.0])
        path = gui.freq_filepaths[1000.0]
        gui._pending_rows.append((path, "recovered\n", False))
        gui._settle_previous_run_rows()          # share is back: written
        assert open(path).read().endswith("recovered\n")
        assert not any("dropped" in m for m in gui.logged)
        gone = os.path.join(tmp, "unplugged", "old.dat")
        gui._pending_rows.extend([(gone, "a\n", False),
                                  (gone, "b\n", False)])
        gui._settle_previous_run_rows()          # still gone: say so
    assert not gui._pending_rows
    assert any("2 buffered row(s)" in m and "dropped" in m
               for m in gui.logged)
    assert "_settle_previous_run_rows()" in inspect.getsource(
        mod.Integrated_CT_GUI.start_measurement)


def test_a_rerun_never_truncates_the_old_t_log():
    with tempfile.TemporaryDirectory() as tmp:
        first = _files(tmp, frequencies=[1000.0])
        with open(first.t_log_path, "a", encoding="utf-8") as fh:
            fh.write("old history\n")
        second = _files(tmp, frequencies=[5000.0])
        old = open(first.t_log_path, encoding="utf-8").read()
    assert "old history" in old
    assert second.t_log_path != first.t_log_path


def test_auto_y_scale_stays_linear_when_any_value_is_not_positive():
    calls = []
    ax = types.SimpleNamespace(
        set_yscale=calls.append, relim=lambda: None,
        set_autoscaley_on=lambda on: None,
        autoscale_view=lambda **k: None, set_ylim=lambda *a: None)
    gui = _bare_gui()
    gui._decade_ylims = {}
    gui.y_scale_var = types.SimpleNamespace(get=lambda: "auto")
    assert gui._apply_y_scale(ax, [1e-12, 1e-9, -1e-11], "cp") is False
    assert calls == ["linear"]
    assert gui._apply_y_scale(ax, [1e-12, 1e-9], "cp") is True


def test_a_single_temperature_gets_its_own_x_window():
    lims = []
    gui = _bare_gui()
    gui.plot_freq = 1000.0
    gui.data_storage = {"cp": {1000.0: {"T": [80.0], "v": [1e-11]}}}
    gui._apply_y_scale = lambda *a: False
    gui.ax_main = types.SimpleNamespace(set_xlim=lambda a, b: lims.append(
        (a, b)))
    gui._rescale_main_axis()
    assert lims == [(79.5, 80.5)]


def test_inputs_are_locked_during_a_run_and_unlocked_after():
    states = {}

    class _W:
        def __init__(self, name):
            self.name = name

        def config(self, state):
            states[self.name] = state
    gui = _bare_gui()
    combo = mod.ttk.Combobox.__new__(mod.ttk.Combobox)
    combo.config = lambda state: states.__setitem__("combo", state)
    gui._run_locked_widgets = [_W("entry"), combo]
    gui._set_inputs_locked(True)
    assert states == {"entry": "disabled", "combo": "disabled"}
    gui._set_inputs_locked(False)
    assert states == {"entry": "normal", "combo": "readonly"}
    src = inspect.getsource(mod.Integrated_CT_GUI.create_input_frame)
    for w in ("self.file_button", "self.freq_text", "geom_radios",
              "fmt_boxes", "self.lcr_cb", "self.wire_combobox"):
        assert w in src.split("_run_locked_widgets = (")[1], w
    assert "_set_inputs_locked(False)" in inspect.getsource(
        mod.Integrated_CT_GUI._finalize_stop)


def test_end_of_run_banners_are_set_and_not_overwritten():
    src = inspect.getsource
    gui_cls = mod.Integrated_CT_GUI
    assert "_set_status" in src(gui_cls._handle_kill_event)
    assert "_set_status" in src(gui_cls._handle_runtime_error)
    fin = src(gui_cls._finalize_stop)
    # only a user Stop (or a failed connect, which re-sets its ERROR line
    # over STOPPING) writes a banner in _finalize_stop
    assert fin.index("if from_user:") < fin.index('_set_status("STOPPED')
    assert fin.count("_set_status") == 2
    assert fin.index("startup_error is not None") < fin.index(
        '_set_status(f"ERROR - could not start')


def test_close_after_a_timed_out_stop_waits_for_the_worker():
    gui = _bare_gui()
    gui.is_running = gui.is_stabilizing = gui._stopping = False
    gui._close_after_stop = False
    alive = [True]
    gui.measurement_thread = types.SimpleNamespace(is_alive=lambda: alive[0])
    pending, events = [], []
    gui.root = types.SimpleNamespace(
        after=lambda ms, fn: pending.append(fn),
        destroy=lambda: events.append("destroy"))
    gui.backend = types.SimpleNamespace(
        close_instruments=lambda: events.append("close"))
    gui._on_closing()
    assert events == [] and len(pending) == 1
    gui._on_closing()                            # second click: no new chain
    assert len(pending) == 1
    alive[0] = False
    pending.pop()()
    assert events == ["close", "destroy"]


def test_lakeshore_session_is_closed_when_idn_fails():
    class _Silent(FakeL340Session):
        def query(self, cmd):
            raise OSError("timeout")
    sessions = []

    class _V:
        def ResourceManager(self):
            return types.SimpleNamespace(
                open_resource=lambda a: sessions.append(_Silent()) or
                sessions[-1])
    with patch.object(mod, "pyvisa", _V()):
        try:
            mod.Lakeshore340_Backend(L340_ADDR)
        except OSError:
            pass
        else:
            raise AssertionError("a silent address must raise")
    assert sessions[0].closed


def test_panel_scrolls_with_the_wheel_and_labels_match_the_panel():
    src = open(PATH, encoding="utf-8").read()
    assert 'bind_all("<MouseWheel>"' in src
    assert "option_add('*Label.background', self.CLR_BG_DARK)" in src
    assert "style.configure('TRadiobutton'" in src
    assert "style='Graph.TRadiobutton'" in src



# --- connect/initialise on the worker (27 Oct 2026) --------------------------

class _Buttons:
    """A button stand-in that records every config() call."""

    def __init__(self):
        self.calls = []

    def config(self, **k):
        self.calls.append(k)


class _Dialogs:
    """messagebox stand-in that records every dialog."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def test_start_on_the_tk_thread_never_connects():
    with tempfile.TemporaryDirectory() as tmp:
        gui, seen, shown = _start_gui_with({}, folder=tmp)
        files = os.listdir(tmp)
    assert files == [], "files are created only after CONNECTED"
    assert shown == []
    (thread,) = _FakeThread.made
    assert thread.started and thread.target == gui._run_worker
    assert seen[0]["lcr_visa"] == ALPHA_ADDR
    assert gui.status[-1].startswith("CONNECTING")
    assert gui.is_stabilizing and not gui.is_running
    assert not gui.stop_event.is_set() and not gui._files_ready.is_set()


def test_stop_during_the_alpha_reset_wait_ends_the_worker_quickly():
    from alphaan_port_test_support import ALPHA_IDN, FakeVisaError
    fake = FakeAlpha({"*IDN?": [ALPHA_IDN] + [
        FakeVisaError(mod.VI_ERROR_TMO)] * 100000})
    combined = mod.Combined_Backend()
    combined.lcr, _ = connected_backend(mod, fake)
    combined.lcr.instrument = None              # connect() opens it
    fake_visa = FakePyvisa()
    gui = _bare_gui()
    gui.backend = combined
    gui.stop_event, gui._files_ready = threading.Event(), threading.Event()
    gui.data_queue = queue.Queue()
    with patch.object(mod, "pyvisa", fake_visa), \
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
        (ls,) = fake_visa.sessions
        assert ls.writes == ["*CLS", "RANGE 0"], ls.writes   # passive
        assert "MODE=IMP" not in fake.commands
        msgs = []
        while not gui.data_queue.empty():
            msgs.append(gui.data_queue.get_nowait())
        assert all(isinstance(m, str) for m in msgs), msgs
        assert "CONNECTED" not in msgs
        assert any("Stopped while connecting" in m for m in msgs), msgs
        # The normal stop path then closes what was opened, safely. The
        # Alpha is still off the bus mid-*RST, so it is sent nothing.
        sent_before_close = len(fake.commands)
        combined.close_instruments()
    assert ls.writes == ["*CLS", "RANGE 0"] and ls.closed  # nothing at close
    assert len(fake.commands) == sent_before_close and fake.closed
    assert "ACV=0" not in fake.commands


def test_a_connect_failure_resets_the_ui_and_shows_error():
    def fail(params, stop_event=None):
        raise ConnectionError("The Alpha did not answer")
    closed, afters = [], []
    gui = _bare_gui()
    gui.backend = types.SimpleNamespace(
        initialize_instruments=fail, params={},
        close_instruments=lambda: closed.append(True))
    gui.stop_event, gui._files_ready = threading.Event(), threading.Event()
    gui.data_queue = queue.Queue()
    gui.is_running, gui.is_stabilizing = False, True
    gui._stopping = gui._close_after_stop = False
    gui._startup_error = None
    gui.frequencies = [1000.0]
    gui._pending_rows = mod.deque()
    gui._set_keep_awake = lambda enable: None
    gui._beep = lambda times=1: None
    gui.start_button, gui.stop_button = _Buttons(), _Buttons()
    gui.root = types.SimpleNamespace(after=lambda ms, fn: afters.append(fn))
    gui._set_inputs_locked = lambda locked: gui.logged.append(
        f"locked={locked}")
    gui.measurement_thread = threading.Thread(
        target=gui._run_worker, args=(dict(PARAMS),), daemon=True)
    gui.measurement_thread.start()
    gui.measurement_thread.join(5.0)
    assert not gui.measurement_thread.is_alive()
    dialogs = _Dialogs()
    with patch.object(mod, "messagebox", dialogs):
        gui._process_data_queue()
    assert any(st.startswith("ERROR") and "did not answer" in st
               for st in gui.status), gui.status
    assert gui.status[-1].startswith("ERROR")
    assert any("ERROR during startup" in m and "Traceback" in m
               for m in gui.logged)
    assert "locked=False" in gui.logged and closed == [True]
    assert gui.start_button.calls[-1] == {"state": "normal"}
    assert not (gui.is_running or gui.is_stabilizing)
    assert gui.stop_event.is_set() and not gui._files_ready.is_set()
    assert [c[0] for c in dialogs.calls] == ["showerror"]   # at Start
    assert "did not answer" in dialogs.calls[0][1][1]
    assert afters == [], "the pump must not keep running"


def test_stop_before_connected_is_handled_creates_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        gui = _bare_gui()
        gui.stop_event, gui._files_ready = (threading.Event(),
                                            threading.Event())
        gui.is_stabilizing = False
        gui.file_location_path = tmp
        gui.stop_event.set()
        gui._on_connected()
        assert os.listdir(tmp) == []
    assert not gui._files_ready.is_set()


def test_on_connected_creates_the_files_then_releases_the_worker():
    with tempfile.TemporaryDirectory() as tmp:
        params = _out_params(frequencies=[1000.0])
        gui = _bare_gui()
        gui.file_location_path = tmp
        gui.frequencies = params["frequencies"]
        gui.c0_farads = mod.c0_from_params(params)
        gui._run_params = params
        gui.stop_event, gui._files_ready = (threading.Event(),
                                            threading.Event())
        gui.is_stabilizing, gui.is_running = True, False
        gui.backend = types.SimpleNamespace(
            lakeshore=types.SimpleNamespace(idn="LSCI,MODEL340,0,0"),
            heater_range_at_start=0)
        gui._populate_plot_freq_dropdown = lambda: None
        gui.plot_freq = 1000.0
        gui.ax_main, gui.canvas = _Recorder(), _Recorder()
        gui._set_keep_awake = lambda enable: None
        gui._on_connected()
        files = os.listdir(tmp)
    assert len(files) == 3, files               # PICA, WinDETA, T-log
    assert gui._files_ready.is_set()
    assert gui.is_running and not gui.is_stabilizing
    assert gui.status[-1].startswith("RUNNING")


def test_the_worker_waits_for_the_files_before_the_first_sweep():
    order = []
    gui = _bare_gui()
    gui.backend = types.SimpleNamespace(
        initialize_instruments=lambda p, ev: order.append("init"))
    gui.stop_event, gui._files_ready = threading.Event(), threading.Event()
    gui.data_queue = queue.Queue()
    gui._measurement_worker = lambda: order.append("run")
    worker = threading.Thread(target=gui._run_worker, args=({},),
                              daemon=True)
    worker.start()
    assert gui.data_queue.get(timeout=5.0) == "CONNECTED"
    assert order == ["init"]                  # no sweep before the files
    gui._files_ready.set()
    worker.join(5.0)
    assert order == ["init", "run"] and not worker.is_alive()


def test_a_stop_while_waiting_for_the_files_never_sweeps():
    swept = []
    gui = _bare_gui()
    gui.backend = types.SimpleNamespace(
        initialize_instruments=lambda p, ev: None)
    gui.stop_event, gui._files_ready = threading.Event(), threading.Event()
    gui.data_queue = queue.Queue()
    gui._measurement_worker = lambda: swept.append(True)
    worker = threading.Thread(target=gui._run_worker, args=({},),
                              daemon=True)
    worker.start()
    assert gui.data_queue.get(timeout=5.0) == "CONNECTED"
    gui.stop_event.set()
    worker.join(5.0)
    assert not worker.is_alive() and swept == []


# --- Stop during the Alpha *RST rejoin (27 Oct 2026) ------------------------

class _GoneAfterReset(FakeAlpha):
    """An Alpha that stops answering once *RST lands. Every later command
    hangs (here 0.3 s) and then times out, like 64-bit Keysight VISA."""

    HANG_S = 0.3

    def __init__(self):
        super().__init__()
        self.reset_sent = False

    def write(self, command):
        super().write(command)
        if self.reset_sent:
            threading.Event().wait(self.HANG_S)
            raise self._tmo()
        if command == "*RST":
            self.reset_sent = True

    def query(self, command):
        if self.reset_sent:
            self.traffic.append(("query", command))
            threading.Event().wait(self.HANG_S)
            raise self._tmo()
        return super().query(command)

    @staticmethod
    def _tmo():
        from alphaan_port_test_support import FakeVisaError
        return FakeVisaError(mod.VI_ERROR_TMO)


def _stop_during_rejoin():
    """Backend whose *RST wait was cut short by Stop. Returns (backend,
    fake, number of commands sent up to and including *RST)."""
    fake = _GoneAfterReset()
    backend, _ = connected_backend(mod, fake)    # __new__-built backend
    backend.stop_event = threading.Event()
    backend.stop_event.set()                    # Stop already pressed
    with patch("builtins.print", lambda *a, **k: None):
        try:
            backend.initialize_instrument(dict(PARAMS))
        except mod.StopRequested:
            pass
        else:
            raise AssertionError("Stop during the rejoin must raise")
    assert fake.commands[-1] == "*RST", fake.commands
    return backend, fake, len(fake.commands)


def test_stop_during_the_rejoin_closes_without_shutdown_commands():
    backend, fake, upto_rst = _stop_during_rejoin()
    assert backend._alpha_rebooting
    printed = []
    started = time.monotonic()
    with patch("builtins.print", lambda *a, **k: printed.append(a[0])):
        backend.close_instrument()
    took = time.monotonic() - started
    assert len(fake.commands) == upto_rst, fake.commands[upto_rst:]
    assert fake.closed and backend.instrument is None
    assert took < 0.25, took                    # one hang would be 0.3 s
    assert any("Alpha still resetting after *RST - closing the session "
               "without shutdown commands" in m for m in printed), printed


def test_stop_during_the_rejoin_then_combined_close_is_prompt():
    backend, fake, upto_rst = _stop_during_rejoin()
    combined = mod.Combined_Backend()
    combined.lcr = backend
    with patch.object(mod, "pyvisa", FakePyvisa()):
        combined.lakeshore = mod.Lakeshore340_Backend(PARAMS["lakeshore_visa"])
    ls = combined.lakeshore.instrument
    started = time.monotonic()
    with patch("builtins.print", lambda *a, **k: None):
        combined.close_instruments()
        # safe_state alone (reconnect cleanup) also stays quiet.
        backend.instrument = fake
        backend.safe_state()
        backend.instrument = None
    assert time.monotonic() - started < 0.25
    assert len(fake.commands) == upto_rst and fake.closed and ls.closed


def test_a_completed_rejoin_clears_the_flag_and_close_shuts_down_normally():
    backend, fake = connected_backend(mod)
    with patch.object(mod.time, "sleep", lambda s: None),             patch("builtins.print", lambda *a, **k: None):
        backend.initialize_instrument(dict(PARAMS))
        assert backend._alpha_rebooting is False
        backend.close_instrument()
    after_init = fake.commands[fake.commands.index("MTM=0.5") + 1:]
    assert after_init[:3] == ["ACV=0", "ZCONSPL=0", "*RST"], after_init


def test_a_failed_rst_write_does_not_mark_the_alpha_as_resetting():
    class _RstFails(FakeAlpha):
        def write(self, command):
            super().write(command)
            if command == "*RST":
                raise OSError("bus error")
    backend, fake = connected_backend(mod, _RstFails())
    try:
        backend._reset_and_set_impedance_mode()
    except OSError:
        pass
    else:
        raise AssertionError("the write error must propagate")
    assert backend._alpha_rebooting is False


def test_window_close_during_a_run_waits_for_a_late_worker():
    """Closed during a run, stop deadline expired, worker still alive
    (e.g. inside a reconnect): no destroy until it exits, then the
    sessions it may have reopened are closed again before destroy."""
    gui = _bare_gui()
    alive = [True]
    gui.measurement_thread = types.SimpleNamespace(is_alive=lambda: alive[0])
    pending, events = [], []
    gui.root = types.SimpleNamespace(
        after=lambda ms, fn: pending.append(fn),
        destroy=lambda: events.append("destroy"))
    gui.backend = types.SimpleNamespace(
        close_instruments=lambda: events.append("close"))
    gui._set_keep_awake = lambda on: None
    gui._set_inputs_locked = lambda locked: None
    gui._drain_queue_at_stop = lambda: None
    gui._close_after_stop = True
    gui._stopping = True
    gui._finalize_stop(False)
    assert events == ["close"] and len(pending) == 1, events
    pending.pop()()                              # still alive: keep waiting
    assert events == ["close"] and len(pending) == 1
    alive[0] = False
    pending.pop()()
    assert events == ["close", "close", "destroy"] and not pending


def test_window_close_gives_up_waiting_after_the_close_deadline():
    gui = _bare_gui()
    gui.measurement_thread = types.SimpleNamespace(is_alive=lambda: True)
    pending, events = [], []
    gui.root = types.SimpleNamespace(
        after=lambda ms, fn: pending.append(fn),
        destroy=lambda: events.append("destroy"))
    gui.backend = types.SimpleNamespace(
        close_instruments=lambda: events.append("close"))
    gui._destroy_when_worker_gone(time.time() - 1.0)
    assert events == ["close", "destroy"] and not pending

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
