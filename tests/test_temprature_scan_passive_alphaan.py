"""Tests for novocontrol/Temprature_Scan_Passive_AlphaAN_GUI.py
(Alpha-AN + Lakeshore 350 as a passive thermometer).

Runnable as plain Python as well as under pytest. No VISA, no Tk window.
"""

import inspect
import math
import os
import queue
import sys
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
