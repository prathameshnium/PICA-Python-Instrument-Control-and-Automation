"""Run path of IV_K6517B_GUI.py (Keithley 6517B: source V, measure R -> I).

Driven against a fake Keithley6517B that records every write, attribute set
and method call in order, so the things that matter are asserted directly:

  - the lab-verified V5 Core zero-correction sequence is sent verbatim and
    in order: *RST, resistance function, ZCHeck ON, ZCORrect:ACQuire,
    ZCHeck OFF, ZCORrect ON (reference manual p. 11-166);
  - the voltage source range is chosen from the sweep: 100 V when every
    point fits, 1000 V otherwise (*RST leaves 100 V, manual p. 11-112), and
    it is written BEFORE the output is enabled, which happens at 0 V;
  - a reading the driver cannot parse (None) or an overflow (+9.9e37,
    manual p. 11-59) becomes NaN instead of a TypeError/garbage current;
  - the worker thread owns the instrument: connect + zero-correct + sweep
    + close all run in it, and close runs exactly once on every exit path
    (finished, Stop during the settling delay, error in a reading, error
    during initialisation);
  - the GUI queue pump writes one row per point, re-enables Start on DONE
    and never opens a message box;
  - all four sweep types validate from the GUI entries: linear (the
    original Start/Stop/Points default), 0 to Max, Loop and Custom List
    (with the on-screen example), and a sweep above 1000 V is refused
    before the instrument is touched.

No hardware and no Tk root; time.sleep is stubbed inside the backend test so
the 7 s zero-correct sequence runs instantly. Runnable as plain Python as
well as under pytest.
"""

import importlib.util
import math
import os
import queue
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULE_PATH = os.path.join(REPO_ROOT, "pica", "keithley", "k6517b", "High_Resistance",
                           "IV_K6517B_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


iv = _load("iv_k6517b_under_test", MODULE_PATH)
SOURCE = open(MODULE_PATH, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeK6517B:
    TRACKED = ("current_nplc", "source_voltage")

    def __init__(self, address, timeout=None, **kwargs):
        object.__setattr__(self, "events", [])
        object.__setattr__(self, "address", address)
        object.__setattr__(self, "timeout", timeout)
        object.__setattr__(self, "id", "KEITHLEY INSTRUMENTS INC.,MODEL 6517B,1,A09")
        object.__setattr__(self, "readings", [])
        object.__setattr__(self, "fail_shutdown", False)
        object.__setattr__(self, "fail_on_reset", False)
        object.__setattr__(self, "_sv", 0.0)

    def __setattr__(self, name, value):
        if name in self.TRACKED:
            self.events.append(("set", name, value))
        if name == "source_voltage":
            object.__setattr__(self, "_sv", value)
            return
        object.__setattr__(self, name, value)

    @property
    def source_voltage(self):
        return self._sv

    def _call(self, name, *args):
        self.events.append(("call", name, args))

    def reset(self):
        self._call("reset")
        if self.fail_on_reset:
            raise RuntimeError("instrument did not answer")

    def measure_resistance(self, *a, **k):
        self._call("measure_resistance")

    def write(self, cmd):
        self._call("write", cmd)

    def enable_source(self):
        self._call("enable_source")

    def disable_source(self):
        self._call("disable_source")

    @property
    def resistance(self):
        self._call("read_resistance")
        return self.readings.pop(0) if self.readings else 1e9

    def shutdown(self):
        self._call("shutdown")
        if self.fail_shutdown:
            raise RuntimeError("VISA timeout during shutdown")


class FakeBackend:
    def __init__(self, gui=None, stop_after=None, fail_at=None, fail_init=False):
        self.calls = []
        self.gui = gui
        self.stop_after = stop_after
        self.fail_at = fail_at
        self.fail_init = fail_init
        self.is_connected = False

    def initialize_instruments(self, params, log=print):
        self.calls.append(("init", params['keithley_visa'], params.get('max_abs_voltage')))
        log("fake init")
        if self.fail_init:
            raise ConnectionError("no 6517B")
        self.is_connected = True

    def measure_at_voltage(self, voltage, delay, wait=None):
        n = sum(1 for c in self.calls if c[0] == "measure")
        self.calls.append(("measure", voltage))
        if self.fail_at is not None and n == self.fail_at:
            raise IOError("GPIB read failed")
        if self.stop_after is not None and n == self.stop_after:
            self.gui.stop_event.set()
        if wait is not None and wait(0.0):
            return None
        r = 1e9
        return (r, voltage / r, voltage)

    def close_instruments(self, log=print):
        self.calls.append(("close",))
        self.is_connected = False


def _install_fake_driver():
    iv.Keithley6517B = FakeK6517B
    iv.VisaIOError = None
    iv.PYMEASURE_AVAILABLE = True


class _NoSleep:
    def __enter__(self):
        self._saved = time.sleep
        time.sleep = lambda s: None
        return self

    def __exit__(self, *a):
        time.sleep = self._saved


def _make_gui(tmpdir, backend=None):
    gui = object.__new__(iv.HighResistanceIV_GUI)
    gui.root = MagicMock()
    gui.root.after = MagicMock(return_value="after#1")
    gui.logs = []
    gui.log = lambda msg: gui.logs.append(str(msg))
    gui.beeps = []
    gui._beep = lambda times=2: gui.beeps.append(times)
    gui.is_running = True
    gui.start_time = time.time()
    gui.stop_event = threading.Event()
    gui.data_queue = queue.Queue()
    gui.measurement_thread = None
    gui._pump_after_id = None
    gui.backend = backend if backend is not None else FakeBackend(gui)
    gui.file_location_path = tmpdir
    gui.data_filepath = os.path.join(tmpdir, "run_IV.dat")
    gui.params = {'sample_name': 'S1'}
    gui.voltage_list = []
    gui.data_storage = {'time': [], 'voltage_applied': [], 'current_measured': [], 'resistance': []}
    for name in ("line_iv", "line_rv", "ax_iv", "ax_rv", "canvas", "start_button",
                 "stop_button", "figure"):
        setattr(gui, name, MagicMock())
    return gui


class _Entry:
    def __init__(self, text):
        self.text = text

    def get(self, *a):
        return self.text


BASE_ENTRIES = {"Sample Name": "S1", "Loops": "1", "Delay (s)": "0.5",
                "Start V": "-10", "Stop V": "10", "Steps": "5",
                "Max V": "50", "Step V": "10"}


def _make_param_gui(sweep_type, entries, custom="", visa="GPIB1::27::INSTR", save=None):
    gui = object.__new__(iv.HighResistanceIV_GUI)
    gui.sweep_type_var = _Entry(sweep_type)
    gui.entries = {k: _Entry(v) for k, v in entries.items()}
    gui.keithley_combobox = _Entry(visa)
    gui.custom_list_text = _Entry(custom)
    gui.file_location_path = save if save is not None else tempfile.gettempdir()
    return gui


def _params(delay=0.0, vmax=50.0):
    return {'keithley_visa': 'GPIB1::27::INSTR', 'max_abs_voltage': vmax,
            'delay_s': delay, 'sample_name': 'S1'}


def _drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


# ---------------------------------------------------------------------------
# Backend against the fake driver
# ---------------------------------------------------------------------------

def test_source_range_selection():
    assert iv.source_range_for(0) == 100
    assert iv.source_range_for(100) == 100
    assert iv.source_range_for(-100) == 100
    assert iv.source_range_for(100.001) == 1000
    assert iv.source_range_for(-500) == 1000
    assert iv.source_range_for(1000) == 1000


def test_initialize_sends_v5_zero_correct_sequence_then_range_then_output_on_at_zero():
    _install_fake_driver()
    be = iv.Keithley6517B_Backend()
    logs = []
    with _NoSleep():
        be.initialize_instruments(_params(vmax=50.0), log=logs.append)
    k = be.keithley
    assert k.timeout == 20000
    assert be.is_connected
    writes = [e[2][0] for e in k.events if e[1] == "write"]
    assert writes == [':SYSTem:ZCHeck ON', ':SYSTem:ZCORrect:ACQuire', ':SYSTem:ZCHeck OFF',
                      ':SYSTem:ZCORrect ON', ':SOURce:VOLTage:RANGe 100'], writes
    names = [e[1] for e in k.events]
    assert names[:2] == ["reset", "measure_resistance"]
    i_nplc = names.index("current_nplc")
    i_rng = [i for i, e in enumerate(k.events) if e[1] == "write" and "RANGe" in e[2][0]][0]
    i_sv = names.index("source_voltage")
    i_on = names.index("enable_source")
    assert i_nplc < i_rng < i_sv < i_on, names
    assert k.events[i_sv][2] == 0
    assert any("Zero Correction Complete" in m for m in logs)


def test_initialize_selects_1000_v_range_for_a_high_sweep():
    _install_fake_driver()
    be = iv.Keithley6517B_Backend()
    with _NoSleep():
        be.initialize_instruments(_params(vmax=500.0), log=lambda m: None)
    writes = [e[2][0] for e in be.keithley.events if e[1] == "write"]
    assert ':SOURce:VOLTage:RANGe 1000' in writes


def test_initialize_failure_closes_and_reraises():
    _install_fake_driver()
    created = []
    original = iv.Keithley6517B

    class Failing(FakeK6517B):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            object.__setattr__(self, "fail_on_reset", True)
            created.append(self)

    iv.Keithley6517B = Failing
    try:
        be = iv.Keithley6517B_Backend()
        with _NoSleep():
            try:
                be.initialize_instruments(_params(), log=lambda m: None)
            except RuntimeError:
                pass
            else:
                raise AssertionError("init error swallowed")
        assert be.keithley is None and not be.is_connected
        assert ("call", "shutdown", ()) in created[0].events
    finally:
        iv.Keithley6517B = original


def test_get_measurement_handles_none_overflow_and_zero():
    _install_fake_driver()
    be = iv.Keithley6517B_Backend()
    with _NoSleep():
        be.initialize_instruments(_params(), log=lambda m: None)
    k = be.keithley
    be.set_voltage(10.0)
    k.readings = [1e10, None, 9.9e37, 0.0, -2e9]
    r, i, v = be.get_measurement()
    assert (r, v) == (1e10, 10.0) and math.isclose(i, 1e-9)
    r, i, v = be.get_measurement()
    assert math.isnan(r) and math.isnan(i)
    r, i, v = be.get_measurement()
    assert math.isnan(r) and math.isnan(i), "overflow must become NaN"
    r, i, v = be.get_measurement()
    assert r == 0.0 and math.isnan(i), "zero resistance gives no current, not inf"
    r, i, v = be.get_measurement()
    assert r == -2e9 and math.isclose(i, -5e-9)


def test_measure_at_voltage_sets_then_waits_then_reads_and_honours_stop():
    _install_fake_driver()
    be = iv.Keithley6517B_Backend()
    with _NoSleep():
        be.initialize_instruments(_params(), log=lambda m: None)
    k = be.keithley
    k.readings = [5e8]
    waited = []
    out = be.measure_at_voltage(25.0, 2.0, wait=lambda s: waited.append(s) or False)
    assert waited == [2.0]
    assert out[2] == 25.0 and out[0] == 5e8 and math.isclose(out[1], 5e-8)
    sets = [e for e in k.events if e[1] == "source_voltage"]
    assert sets[-1][2] == 25.0
    k.readings = [1.0]
    assert be.measure_at_voltage(30.0, 2.0, wait=lambda s: True) is None
    assert k.readings == [1.0], "no reading after Stop"
    assert be.keithley.source_voltage == 30.0


def test_set_voltage_and_measure_refuse_when_not_connected():
    _install_fake_driver()
    be = iv.Keithley6517B_Backend()
    for fn in (lambda: be.set_voltage(1.0), be.get_measurement):
        try:
            fn()
        except ConnectionError:
            pass
        else:
            raise AssertionError("worked while disconnected")


def test_close_never_raises_and_forces_output_off():
    _install_fake_driver()
    be = iv.Keithley6517B_Backend()
    with _NoSleep():
        be.initialize_instruments(_params(), log=lambda m: None)
    k = be.keithley
    k.fail_shutdown = True
    logs = []
    be.close_instruments(log=logs.append)
    assert be.keithley is None and not be.is_connected
    assert ("call", "write", ("OUTPUT OFF",)) in k.events
    assert any("Warning" in m for m in logs)
    be.close_instruments(log=logs.append)   # no-op second time
    assert sum(1 for e in k.events if e[1] == "shutdown") == 1


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------

def test_worker_finished_path_inits_sweeps_closes_once():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        pts = [0.0, 10.0, 20.0]
        gui._measurement_worker(_params(), pts)
        items = _drain(gui.data_queue)
        kinds = [i[0] for i in items]
        assert items[-1] == ("DONE", "finished")
        assert ("STATE", "RUNNING") in items
        data = [i for i in items if i[0] == "DATA"]
        assert [i[3] for i in data] == pts
        calls = gui.backend.calls
        assert calls[0][0] == "init" and calls[0][1] == "GPIB1::27::INSTR"
        assert calls.count(("close",)) == 1 and calls[-1] == ("close",)
        assert "ERROR" not in kinds
        assert any("fake init" in i[1] for i in items if i[0] == "LOG")


def test_worker_stop_mid_delay():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, stop_after=1)
        gui._measurement_worker(_params(delay=5.0), [0, 10, 20, 30])
        items = _drain(gui.data_queue)
        assert items[-1] == ("DONE", "stopped")
        assert len([i for i in items if i[0] == "DATA"]) == 1
        assert gui.backend.calls[-1] == ("close",)
        assert len([c for c in gui.backend.calls if c[0] == "measure"]) == 2


def test_worker_stop_during_initialisation_sweeps_nothing():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.stop_event.set()
        gui._measurement_worker(_params(), [0, 10])
        items = _drain(gui.data_queue)
        assert items[-1] == ("DONE", "stopped")
        assert not [i for i in items if i[0] == "DATA"]
        assert ("STATE", "RUNNING") not in items
        assert gui.backend.calls[-1] == ("close",)


def test_worker_error_in_reading_and_in_init():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, fail_at=1)
        gui._measurement_worker(_params(), [0, 10, 20])
        items = _drain(gui.data_queue)
        assert [i[0] for i in items].count("DATA") == 1
        assert items[-1] == ("DONE", "error")
        err = [i for i in items if i[0] == "ERROR"][0][1]
        assert "GPIB read failed" in err and "Traceback" in err
        assert gui.backend.calls[-1] == ("close",)

        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, fail_init=True)
        gui._measurement_worker(_params(), [0, 10])
        items = _drain(gui.data_queue)
        assert items[-1] == ("DONE", "error")
        assert not [i for i in items if i[0] == "DATA"]
        assert ("close",) in gui.backend.calls


def test_worker_real_thread_real_backend_stop_interrupts_long_delay():
    _install_fake_driver()
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        be = iv.Keithley6517B_Backend()
        gui.backend = be
        with _NoSleep():
            t = threading.Thread(target=gui._measurement_worker,
                                 args=(_params(delay=30.0), [5.0, 10.0]), daemon=True)
            t.start()
            pause = threading.Event()
            for _ in range(500):
                k = be.keithley
                if k is not None and any(e[1] == "source_voltage" and e[2] == 5.0 for e in k.events):
                    break
                pause.wait(0.01)
            k = be.keithley
            gui.stop_event.set()
            t.join(timeout=5)
        assert not t.is_alive(), "worker did not return after Stop"
        items = _drain(gui.data_queue)
        assert items[-1] == ("DONE", "stopped")
        assert ("call", "shutdown", ()) in k.events
        assert be.keithley is None


# ---------------------------------------------------------------------------
# GUI queue pump + data file
# ---------------------------------------------------------------------------

def test_queue_pump_writes_rows_and_finishes_without_dialog():
    saved = iv.messagebox
    iv.messagebox = MagicMock()
    try:
        with tempfile.TemporaryDirectory() as d:
            gui = _make_gui(d)
            gui.params = {'sample_name': 'S1', 'sweep_type': iv.SWEEP_LOOP, 'keithley_visa': 'x',
                          'start_v': 0, 'stop_v': 0, 'steps': 0, 'max_v': 10, 'step_v': 5,
                          'custom_list_str': '', 'num_loops': 1, 'delay_s': 0.0,
                          'max_abs_voltage': 10.0}
            gui._write_file_header(gui.params, 9)
            gui._measurement_worker(_params(), [0.0, 5.0, 10.0])
            gui._process_data_queue()
            assert gui.is_running is False
            gui.start_button.config.assert_called_with(state='normal')
            assert gui.beeps == [2]
            assert not iv.messagebox.showinfo.called and not iv.messagebox.showerror.called
            assert gui.root.after.call_count == 0
            with open(gui.data_filepath, encoding="utf-8") as f:
                lines = f.read().splitlines()
            header = [ln for ln in lines if ln.startswith("#")]
            assert any("Sweep type: Loop" in ln for ln in header)
            assert any("source range: 100 V" in ln for ln in header)
            rows = [ln for ln in lines if not ln.startswith("#")]
            assert rows[0] == "Time (s),Applied Voltage (V),Measured Current (A),Resistance (Ohms)"
            body = [r.split(",") for r in rows[1:]]
            assert len(body) == 3
            assert [float(r[1]) for r in body] == [0.0, 5.0, 10.0]
            assert math.isclose(float(body[1][2]), 5e-9) and float(body[1][3]) == 1e9
            assert gui.data_storage['voltage_applied'] == [0.0, 5.0, 10.0]
            assert gui.line_iv.set_data.called and gui.line_rv.set_data.called
            assert any("SWEEP COMPLETE" in m for m in gui.logs)
    finally:
        iv.messagebox = saved


def test_queue_pump_keeps_polling_while_worker_alive_and_finish_outcomes():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.data_queue.put(("LOG", "hi"))
        gui._process_data_queue()
        assert "hi" in gui.logs and gui._pump_after_id == "after#1"
        for outcome, beeps, word in [("finished", 2, "COMPLETE"), ("stopped", 3, "STOPPED"),
                                     ("error", 3, "ABORTED")]:
            gui = _make_gui(d)
            gui._finish_run(outcome)
            assert gui.is_running is False and gui.beeps == [beeps]
            assert any(word in m for m in gui.logs)


def test_stop_sets_event_once_and_never_touches_backend():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.stop_measurement()
        gui.stop_measurement()
        assert gui.stop_event.is_set()
        assert sum(1 for m in gui.logs if "Stop requested" in m) == 1
        assert gui.backend.calls == []


# ---------------------------------------------------------------------------
# Parameter validation for every sweep type
# ---------------------------------------------------------------------------

def test_params_linear_default_behaviour_preserved():
    gui = _make_param_gui(iv.SWEEP_LINEAR, BASE_ENTRIES)
    params, pts = gui._collect_params()
    assert list(pts) == [-10, -5, 0, 5, 10]
    assert params['max_abs_voltage'] == 10.0 and params['delay_s'] == 0.5
    assert iv.source_range_for(params['max_abs_voltage']) == 100


def test_params_zero_to_max_and_loop():
    gui = _make_param_gui(iv.SWEEP_ZERO_TO_MAX, BASE_ENTRIES)
    _, pts = gui._collect_params()
    assert list(pts) == [0, 10, 20, 30, 40, 50]
    gui = _make_param_gui(iv.SWEEP_LOOP, dict(BASE_ENTRIES, **{"Loops": "2", "Max V": "-20"}))
    params, pts = gui._collect_params()
    assert len(pts) == 2 * 9 and pts[2] == -20 and pts[6] == 20
    assert params['max_abs_voltage'] == 20.0


def test_params_custom_example_and_high_voltage_range():
    gui = _make_param_gui(iv.SWEEP_CUSTOM, BASE_ENTRIES, custom=iv.CUSTOM_LIST_EXAMPLE)
    params, pts = gui._collect_params()
    assert params['max_abs_voltage'] == 100.0
    gui = _make_param_gui(iv.SWEEP_CUSTOM, BASE_ENTRIES, custom="0 250\n500; 750, 1000")
    params, pts = gui._collect_params()
    assert list(pts) == [0, 250, 500, 750, 1000]
    assert iv.source_range_for(params['max_abs_voltage']) == 1000


def test_params_refusals():
    cases = [
        ("above 1000 V linear", iv.SWEEP_LINEAR, dict(BASE_ENTRIES, **{"Stop V": "1001"}), ""),
        ("above 1000 V custom", iv.SWEEP_CUSTOM, BASE_ENTRIES, "0, 1500"),
        ("one point", iv.SWEEP_LINEAR, dict(BASE_ENTRIES, **{"Steps": "1"}), ""),
        ("text points", iv.SWEEP_LINEAR, dict(BASE_ENTRIES, **{"Steps": "five"}), ""),
        ("zero max", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Max V": "0"}), ""),
        ("zero step", iv.SWEEP_LOOP, dict(BASE_ENTRIES, **{"Step V": "0"}), ""),
        ("negative delay", iv.SWEEP_LINEAR, dict(BASE_ENTRIES, **{"Delay (s)": "-1"}), ""),
        ("zero loops", iv.SWEEP_LINEAR, dict(BASE_ENTRIES, **{"Loops": "0"}), ""),
        ("empty custom", iv.SWEEP_CUSTOM, BASE_ENTRIES, " "),
        ("bad custom", iv.SWEEP_CUSTOM, BASE_ENTRIES, "0, x"),
        ("no name", iv.SWEEP_LINEAR, dict(BASE_ENTRIES, **{"Sample Name": ""}), ""),
    ]
    for label, st, entries, custom in cases:
        gui = _make_param_gui(st, entries, custom=custom)
        try:
            gui._collect_params()
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted: {label}")
    for kw in ({"visa": ""}, {"save": ""},
               {"save": os.path.join(tempfile.gettempdir(), "no_such_dir_pica_6517")}):
        gui = _make_param_gui(iv.SWEEP_LINEAR, BASE_ENTRIES, **kw)
        try:
            gui._collect_params()
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted: {kw}")


def test_start_refuses_bad_params_without_touching_backend():
    saved = iv.messagebox
    iv.messagebox = MagicMock()
    try:
        gui = _make_param_gui(iv.SWEEP_LINEAR, dict(BASE_ENTRIES, **{"Steps": "1"}))
        gui.is_running = False
        gui.logs = []
        gui.log = lambda m: gui.logs.append(m)
        gui.backend = FakeBackend(gui)
        gui.start_measurement()
        assert gui.is_running is False and gui.backend.calls == []
        assert iv.messagebox.showerror.called
    finally:
        iv.messagebox = saved


# ---------------------------------------------------------------------------
# Source policy
# ---------------------------------------------------------------------------

def _body(func_name):
    start = SOURCE.index(f"    def {func_name}(")
    nxt = SOURCE.find("\n    def ", start + 1)
    return SOURCE[start:nxt if nxt > 0 else None]


def test_no_message_box_on_the_run_path():
    for fn in ("_measurement_worker", "_process_data_queue", "_handle_point",
               "_finish_run", "stop_measurement", "_update_plots"):
        assert "messagebox" not in _body(fn), f"{fn} opens a dialog"


def test_worker_owns_init_and_close_and_uses_interruptible_wait():
    body = _body("_measurement_worker")
    assert "initialize_instruments" in body
    assert "finally:" in body and "close_instruments" in body.split("finally:")[1]
    assert "wait=self.stop_event.wait" in body
    assert "initialize_instruments" not in _body("start_measurement"), \
        "the 7 s connect must not run on the GUI thread"


def test_gui_offers_all_four_sweep_types_with_the_example():
    assert iv.SWEEP_TYPES == (iv.SWEEP_LINEAR, iv.SWEEP_ZERO_TO_MAX, iv.SWEEP_LOOP, iv.SWEEP_CUSTOM)
    assert "CUSTOM_LIST_EXAMPLE" in _body("_on_sweep_type_change")
    assert "custom_list_hint" in SOURCE
    assert "self.sweep_type_cb.set(SWEEP_LINEAR)" in SOURCE
    assert iv.HighResistanceIV_GUI.PROGRAM_VERSION == "5.0"


if __name__ == "__main__":
    failures = 0
    for name in [n for n in list(globals()) if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: {e!r}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
