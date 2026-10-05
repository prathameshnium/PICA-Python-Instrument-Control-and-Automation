"""Run path of IV_K2400_GUI.py (Keithley 2400 alone: source I, measure V).

Driven against a fake Keithley2400 that records every attribute set and
method call in order, so the things that matter are asserted directly:

  - the 2400 is told to MEASURE VOLTS (measure_voltage) before the
    compliance is written: after *RST the sense function is CURRent only
    and :READ? returns +9.91E+37 for the voltage element (manual p. 18-59),
    which is exactly what the old module read on every point;
  - the source range follows the largest |set-point| of the sweep, the
    output is enabled at 0 A, and the compliance is the value typed;
  - compliance is read from :SENS:VOLT:PROT:TRIP? (manual p. 18-68), and an
    overflow reading (>= 9.9e37) becomes NaN instead of a bogus voltage;
  - shutdown never raises and forces OUTPUT OFF if the ramp fails;
  - the worker thread owns the instrument: shutdown runs exactly once on
    every exit path (finished, Stop during the settling delay, error in a
    reading, error in connect) and always AFTER the last data point;
  - the GUI queue pump writes one 4-column row per point, re-enables Start
    on DONE, and never opens a message box;
  - parameter validation converts uA to A, refuses a sweep above 1.05 A,
    a non-positive compliance and a missing save folder, all before the
    instrument is touched.

No hardware and no Tk root: the GUI object is built with object.__new__
and MagicMock widgets. Runnable as plain Python as well as under pytest.
"""

import importlib.util
import math
import os
import queue
import sys
import tempfile
import threading
from unittest.mock import MagicMock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULE_PATH = os.path.join(REPO_ROOT, "pica", "keithley", "k2400", "IV_K2400_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


iv = _load("iv_k2400_under_test", MODULE_PATH)
SOURCE = open(MODULE_PATH, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeK2400:
    """Records every write-type action in order; answers the reads."""
    TRACKED = ("source_current_range", "compliance_voltage", "source_current",
               "measure_voltage_nplc")

    def __init__(self, address, **kwargs):
        object.__setattr__(self, "events", [])
        object.__setattr__(self, "address", address)
        object.__setattr__(self, "id", "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1,C32")
        object.__setattr__(self, "readings", [])
        object.__setattr__(self, "trip", "0")
        object.__setattr__(self, "fail_shutdown", False)
        object.__setattr__(self, "source_current", 0.0)

    def __setattr__(self, name, value):
        if name in self.TRACKED:
            self.events.append(("set", name, value))
        object.__setattr__(self, name, value)

    def _call(self, name, *args):
        self.events.append(("call", name, args))

    def reset(self):
        self._call("reset")

    def use_front_terminals(self):
        self._call("use_front_terminals")

    def apply_current(self, *a, **k):
        self._call("apply_current")

    def measure_voltage(self, nplc=1, voltage=21.0, auto_range=True):
        self._call("measure_voltage", nplc, auto_range)

    def enable_source(self):
        self._call("enable_source")

    def disable_source(self):
        self._call("disable_source")

    def ramp_to_current(self, target, steps=30, pause=20e-3):
        self._call("ramp_to_current", target)
        object.__setattr__(self, "source_current", target)

    @property
    def voltage(self):
        return self.readings.pop(0) if self.readings else 0.123

    def ask(self, cmd):
        self._call("ask", cmd)
        if "TRIP" in cmd:
            return self.trip
        return "0"

    def write(self, cmd):
        self._call("write", cmd)

    def shutdown(self):
        self._call("shutdown")
        if self.fail_shutdown:
            raise RuntimeError("VISA timeout during shutdown")


class FakeBackend:
    """Stands in for Keithley2400_IV_Backend inside the worker tests."""

    def __init__(self, gui=None, stop_after=None, fail_at=None, fail_connect=False):
        self.calls = []
        self.gui = gui
        self.stop_after = stop_after
        self.fail_at = fail_at
        self.fail_connect = fail_connect

    def connect_and_configure(self, visa, max_abs, compliance):
        self.calls.append(("connect", visa, max_abs, compliance))
        if self.fail_connect:
            raise ConnectionError("no instrument at " + visa)
        return "FAKE 2400"

    def measure_at_current(self, current, delay, wait=None):
        n = sum(1 for c in self.calls if c[0] == "measure")
        self.calls.append(("measure", current))
        if self.fail_at is not None and n == self.fail_at:
            raise IOError("GPIB read failed")
        if self.stop_after is not None and n == self.stop_after:
            self.gui.stop_event.set()        # user pressed Stop mid-delay
        if wait is not None and wait(0.0):   # Event.wait(0) -> True once set
            return None
        return (current * 1000.0, abs(current) > 0.9e-3)

    def shutdown(self):
        self.calls.append(("shutdown",))


def _install_fake_driver():
    iv.Keithley2400 = FakeK2400
    iv.PYMEASURE_AVAILABLE = True
    iv.pyvisa = None


def _make_gui(tmpdir, backend=None):
    """A MeasurementAppGUI without Tk: only the attributes the run path uses."""
    gui = object.__new__(iv.MeasurementAppGUI)
    gui.root = MagicMock()
    gui.root.after = MagicMock(return_value="after#1")
    gui.logs = []
    gui.log = lambda msg: gui.logs.append(str(msg))
    gui.beeps = []
    gui._beep = lambda times=2: gui.beeps.append(times)
    gui.is_running = True
    gui.stop_event = threading.Event()
    gui.data_queue = queue.Queue()
    gui.measurement_thread = None
    gui._pump_after_id = None
    gui.backend = backend if backend is not None else FakeBackend(gui)
    gui.file_location_path = tmpdir
    gui.data_filepath = os.path.join(tmpdir, "run_IV.dat")
    gui.params = {'sample_name': 'S1'}
    gui.sweep_points = []
    gui.data_storage = {'current': [], 'voltage': [], 'resistance': []}
    for name in ("progress_bar", "line_main", "line_resistance", "ax_vi", "ax_ri",
                 "canvas", "start_button", "stop_button", "figure"):
        setattr(gui, name, MagicMock())
    return gui


class _Entry:
    def __init__(self, text):
        self.text = text

    def get(self, *a):
        return self.text


def _make_param_gui(sweep_type, entries, custom="", visa="GPIB0::24::INSTR", save=None):
    gui = object.__new__(iv.MeasurementAppGUI)
    gui.sweep_type_var = _Entry(sweep_type)
    gui.entries = {k: _Entry(v) for k, v in entries.items()}
    gui.keithley_combobox = _Entry(visa)
    gui.custom_list_text = _Entry(custom)
    gui.file_location_path = save if save is not None else tempfile.gettempdir()
    return gui


BASE_ENTRIES = {"Sample Name": "S1", "Num Loops": "1", "Compliance": "10",
                "Delay": "0.1", "Max Current": "10", "Step Current": "2.5"}


# ---------------------------------------------------------------------------
# Backend against the fake driver
# ---------------------------------------------------------------------------

def test_configure_order_measure_volts_before_compliance_output_on_at_zero():
    _install_fake_driver()
    be = iv.Keithley2400_IV_Backend()
    be.connect_and_configure("GPIB0::24::INSTR", 10e-6, 10.0)
    ev = be.keithley.events
    names = [e[1] for e in ev]
    assert names[:4] == ["reset", "use_front_terminals", "apply_current", "measure_voltage"], names
    i_meas = names.index("measure_voltage")
    i_comp = names.index("compliance_voltage")
    i_rng = names.index("source_current_range")
    i_zero = names.index("source_current")
    i_on = names.index("enable_source")
    assert i_meas < i_comp, "volts must be enabled before the compliance sets the range"
    assert i_rng < i_comp < i_zero < i_on
    assert ev[i_rng][2] == 10e-6
    assert ev[i_comp][2] == 10.0
    assert ev[i_zero][2] == 0
    assert ev[i_meas][2] == (1, True)  # NPLC 1, auto range


def test_configure_range_zero_falls_back_to_a_real_range():
    _install_fake_driver()
    be = iv.Keithley2400_IV_Backend()
    be.connect_and_configure("GPIB0::24::INSTR", 0.0, 1.0)
    rng = [e for e in be.keithley.events if e[1] == "source_current_range"][0][2]
    assert rng > 0


def test_read_voltage_scalar_list_overflow_and_compliance():
    _install_fake_driver()
    be = iv.Keithley2400_IV_Backend()
    be.connect_and_configure("GPIB0::24::INSTR", 1e-3, 5.0)
    k = be.keithley
    k.readings = [0.5, [0.25, 1e-3, 250.0], 9.91e37, -9.91e37]
    v, t = be.read_voltage()
    assert v == 0.5 and t is False
    v, t = be.read_voltage()
    assert v == 0.25, "first element of a multi-element :READ? is the voltage"
    v, t = be.read_voltage()
    assert math.isnan(v), "overflow must become NaN"
    k.trip = "1"
    v, t = be.read_voltage()
    assert math.isnan(v) and t is True
    asks = [e for e in k.events if e[1] == "ask"]
    assert all(":SENS:VOLT:PROT:TRIP?" in e[2][0] for e in asks)
    assert len(asks) == 4


def test_measure_at_current_ramps_then_waits_then_reads_and_honours_stop():
    _install_fake_driver()
    be = iv.Keithley2400_IV_Backend()
    be.connect_and_configure("GPIB0::24::INSTR", 1e-3, 5.0)
    k = be.keithley
    k.readings = [1.0]
    waited = []

    def wait_ok(s):
        waited.append(s)
        return False

    out = be.measure_at_current(5e-4, 0.3, wait=wait_ok)
    assert out == (1.0, False)
    assert waited == [0.3]
    ramps = [e for e in k.events if e[1] == "ramp_to_current"]
    assert ramps[-1][2] == (5e-4,)
    # the ramp happened before the read: no 'ask' between ramp and wait
    i_ramp = len(k.events) - 1 - [e[1] for e in k.events][::-1].index("ramp_to_current")
    i_ask = len(k.events) - 1 - [e[1] for e in k.events][::-1].index("ask")
    assert i_ramp < i_ask

    k.readings = [2.0]
    out = be.measure_at_current(1e-4, 0.3, wait=lambda s: True)   # Stop pressed
    assert out is None
    assert k.readings == [2.0], "no reading may be taken after Stop"
    # zero delay never calls wait
    be.measure_at_current(1e-4, 0.0, wait=lambda s: (_ for _ in ()).throw(AssertionError()))


def test_shutdown_never_raises_and_forces_output_off():
    _install_fake_driver()
    be = iv.Keithley2400_IV_Backend()
    be.connect_and_configure("GPIB0::24::INSTR", 1e-3, 5.0)
    k = be.keithley
    k.fail_shutdown = True
    be.shutdown()                      # must not raise
    assert be.keithley is None
    assert ("call", "write", ("OUTPUT OFF",)) in k.events
    be.shutdown()                      # second call is a no-op
    assert sum(1 for e in k.events if e[1] == "shutdown") == 1


def test_connect_without_pymeasure_raises_before_touching_anything():
    _install_fake_driver()
    iv.PYMEASURE_AVAILABLE = False
    try:
        be = iv.Keithley2400_IV_Backend()
        try:
            be.connect_and_configure("GPIB0::24::INSTR", 1e-3, 5.0)
        except ImportError:
            pass
        else:
            raise AssertionError("connected without pymeasure")
        assert be.keithley is None
    finally:
        iv.PYMEASURE_AVAILABLE = True


# ---------------------------------------------------------------------------
# Worker thread lifecycle (run synchronously, no thread needed)
# ---------------------------------------------------------------------------

def _drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def _params(delay=0.0):
    return {'visa_address': 'GPIB0::24::INSTR', 'max_abs_current_A': 1e-3,
            'compliance_v': 5.0, 'delay_s': delay, 'sample_name': 'S1'}


def test_worker_finished_path_shuts_down_once_after_last_point():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        pts = [0.0, 1e-4, 2e-4]
        gui._measurement_worker(_params(), pts)
        items = _drain(gui.data_queue)
        kinds = [i[0] for i in items]
        assert kinds[-1] == "DONE" and items[-1][1] == "finished"
        data = [i for i in items if i[0] == "DATA"]
        assert [i[3] for i in data] == pts
        assert [i[1] for i in data] == [0, 1, 2] and all(i[2] == 3 for i in data)
        calls = gui.backend.calls
        assert calls[0][0] == "connect" and calls[0][1:] == ('GPIB0::24::INSTR', 1e-3, 5.0)
        assert calls.count(("shutdown",)) == 1
        assert calls[-1] == ("shutdown",)
        assert "ERROR" not in kinds


def test_worker_stop_mid_delay_ends_with_stopped_and_output_off():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, stop_after=1)
        gui._measurement_worker(_params(delay=5.0), [1e-4, 2e-4, 3e-4, 4e-4])
        items = _drain(gui.data_queue)
        assert items[-1] == ("DONE", "stopped")
        assert len([i for i in items if i[0] == "DATA"]) == 1
        assert gui.backend.calls[-1] == ("shutdown",)
        assert gui.backend.calls.count(("shutdown",)) == 1
        # no further point was sourced after Stop
        assert len([c for c in gui.backend.calls if c[0] == "measure"]) == 2


def test_worker_stop_before_first_point():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.stop_event.set()
        gui._measurement_worker(_params(), [1e-4, 2e-4])
        items = _drain(gui.data_queue)
        assert items[-1] == ("DONE", "stopped")
        assert not [i for i in items if i[0] == "DATA"]
        assert gui.backend.calls[-1] == ("shutdown",)


def test_worker_error_in_reading_reports_and_shuts_down():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, fail_at=2)
        gui._measurement_worker(_params(), [1e-4, 2e-4, 3e-4, 4e-4])
        items = _drain(gui.data_queue)
        kinds = [i[0] for i in items]
        assert kinds.count("DATA") == 2
        err = [i for i in items if i[0] == "ERROR"][0]
        assert "GPIB read failed" in err[1] and "Traceback" in err[1]
        assert items[-1] == ("DONE", "error")
        assert gui.backend.calls[-1] == ("shutdown",)


def test_worker_connect_failure_still_calls_shutdown():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, fail_connect=True)
        gui._measurement_worker(_params(), [1e-4])
        items = _drain(gui.data_queue)
        assert items[-1] == ("DONE", "error")
        assert not [i for i in items if i[0] == "DATA"]
        assert ("shutdown",) in gui.backend.calls


def test_worker_in_a_real_thread_with_event_wait_is_interrupted_by_stop():
    """Real thread, real Event: Stop during a 30 s delay returns within a
    second and the worker still shuts the instrument down."""
    _install_fake_driver()
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        be = iv.Keithley2400_IV_Backend()
        gui.backend = be
        t = threading.Thread(target=gui._measurement_worker,
                             args=(_params(delay=30.0), [1e-4, 2e-4]), daemon=True)
        t.start()
        # wait until the first ramp has been issued
        deadline = threading.Event()
        for _ in range(200):
            if be.keithley is not None and any(e[1] == "ramp_to_current" for e in be.keithley.events):
                break
            deadline.wait(0.01)
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

def test_queue_pump_writes_rows_updates_plot_and_finishes_without_dialog():
    saved = iv.messagebox
    iv.messagebox = MagicMock()
    try:
        with tempfile.TemporaryDirectory() as d:
            gui = _make_gui(d)
            gui.params = {'sample_name': 'S1', 'sweep_type': iv.SWEEP_LOOP,
                          'visa_address': 'GPIB0::24::INSTR', 'custom_list_str': '',
                          'max_current_uA': 2, 'step_current_uA': 1, 'num_loops': 1,
                          'compliance_v': 5.0, 'delay_s': 0.0}
            gui._write_file_header(gui.params, 3)
            gui._measurement_worker(_params(), [0.0, 1e-6, 2e-6])
            gui._process_data_queue()
            assert gui.is_running is False
            gui.start_button.config.assert_called_with(state='normal')
            gui.stop_button.config.assert_called_with(state='disabled')
            assert gui.beeps == [2]
            assert not iv.messagebox.showinfo.called
            assert not iv.messagebox.showerror.called
            assert gui.root.after.call_count == 0, "pump must stop after DONE"

            with open(gui.data_filepath, encoding="utf-8") as f:
                lines = f.read().splitlines()
            header = [ln for ln in lines if ln.startswith("#")]
            # the arrow label must be written ASCII-safe (cp1252 lab PCs)
            assert any("Sweep type: Loop (0 -> Max -> 0 -> -Max -> 0)" in ln for ln in header)
            assert all(ord(ch) < 128 for ln in header for ch in ln)
            assert any("Compliance: 5 V" in ln for ln in header)
            rows = [ln for ln in lines if not ln.startswith("#")]
            assert rows[0].split("\t") == ["Current (A)", "Voltage (V)", "Resistance (Ohm)", "Compliance"]
            body = [r.split("\t") for r in rows[1:]]
            assert len(body) == 3
            assert float(body[0][0]) == 0.0 and body[0][2] == "nan"      # R undefined at 0 A
            assert float(body[1][0]) == 1e-6 and float(body[1][1]) == 1e-3
            assert abs(float(body[1][2]) - 1000.0) < 1e-6
            assert all(r[3] == "0" for r in body)
            assert gui.data_storage['current'] == [0.0, 1e-6, 2e-6]
            assert gui.line_main.set_data.called and gui.line_resistance.set_data.called
            assert any("SWEEP COMPLETE" in m for m in gui.logs)
    finally:
        iv.messagebox = saved


def test_queue_pump_logs_compliance_warning_and_marks_the_row():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui._write_file_header({'sample_name': 'S1', 'sweep_type': iv.SWEEP_CUSTOM,
                                'visa_address': 'x', 'custom_list_str': '0 1000',
                                'max_current_uA': 0, 'step_current_uA': 0, 'num_loops': 1,
                                'compliance_v': 1.0, 'delay_s': 0}, 2)
        gui._measurement_worker(_params(), [1e-4, 1e-3])   # fake trips above 0.9 mA
        gui._process_data_queue()
        assert any("compliance reached" in m for m in gui.logs)
        with open(gui.data_filepath, encoding="utf-8") as f:
            body = [ln.split("\t") for ln in f.read().splitlines() if not ln.startswith("#")][1:]
        assert [r[3] for r in body] == ["0", "1"]


def test_queue_pump_keeps_polling_while_worker_is_alive():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.data_queue.put(("LOG", "hello"))
        gui._process_data_queue()
        assert "hello" in gui.logs
        gui.root.after.assert_called_once()
        assert gui._pump_after_id == "after#1"


def test_finish_run_outcomes():
    with tempfile.TemporaryDirectory() as d:
        for outcome, beeps, word in [("finished", 2, "COMPLETE"), ("stopped", 3, "STOPPED"),
                                     ("error", 3, "ABORTED")]:
            gui = _make_gui(d)
            gui._finish_run(outcome)
            assert gui.is_running is False
            assert gui.beeps == [beeps]
            assert any(word in m for m in gui.logs)
            assert "Output is OFF" in gui.logs[0] or outcome == "finished"


def test_stop_measurement_sets_event_once_and_never_touches_backend():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.stop_measurement()
        gui.stop_measurement()
        assert gui.stop_event.is_set()
        assert sum(1 for m in gui.logs if "Stop requested" in m) == 1
        assert gui.backend.calls == []           # the worker does the shutdown
        gui2 = _make_gui(d)
        gui2.is_running = False
        gui2.stop_measurement()
        assert not gui2.stop_event.is_set()


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

def test_collect_params_zero_to_max_converts_micro_amps():
    gui = _make_param_gui(iv.SWEEP_ZERO_TO_MAX, BASE_ENTRIES)
    params, pts = gui._collect_params()
    assert [round(p * 1e6, 9) for p in pts] == [0, 2.5, 5, 7.5, 10]
    assert math.isclose(params['max_abs_current_A'], 10e-6, rel_tol=1e-12)
    assert params['compliance_v'] == 10.0 and params['delay_s'] == 0.1


def test_collect_params_loop_with_loops_and_custom_example():
    e = dict(BASE_ENTRIES, **{"Num Loops": "2", "Max Current": "1", "Step Current": "0.5"})
    gui = _make_param_gui(iv.SWEEP_LOOP, e)
    _, pts = gui._collect_params()
    assert len(pts) == 2 * 9
    gui = _make_param_gui(iv.SWEEP_CUSTOM, BASE_ENTRIES, custom=iv.CUSTOM_LIST_EXAMPLE)
    params, pts = gui._collect_params()
    assert math.isclose(params['max_abs_current_A'], 100e-6, rel_tol=1e-12)
    assert len(pts) == len(iv.parse_custom_list(iv.CUSTOM_LIST_EXAMPLE))


def test_collect_params_refusals():
    cases = [
        ("above 1.05 A", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Max Current": "2000000"}), "", {}),
        ("zero compliance", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Compliance": "0"}), "", {}),
        ("compliance > 210", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Compliance": "250"}), "", {}),
        ("negative delay", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Delay": "-1"}), "", {}),
        ("zero loops", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Num Loops": "0"}), "", {}),
        ("text in max", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Max Current": "ten"}), "", {}),
        ("zero max", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Max Current": "0"}), "", {}),
        ("zero step", iv.SWEEP_LOOP, dict(BASE_ENTRIES, **{"Step Current": "0"}), "", {}),
        ("empty custom", iv.SWEEP_CUSTOM, BASE_ENTRIES, "  \n ", {}),
        ("bad custom token", iv.SWEEP_CUSTOM, BASE_ENTRIES, "0, 1, abc", {}),
        ("no sample name", iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Sample Name": "  "}), "", {}),
        ("no visa", iv.SWEEP_ZERO_TO_MAX, BASE_ENTRIES, "", {"visa": ""}),
        ("no save path", iv.SWEEP_ZERO_TO_MAX, BASE_ENTRIES, "", {"save": ""}),
        ("missing folder", iv.SWEEP_ZERO_TO_MAX, BASE_ENTRIES, "",
         {"save": os.path.join(tempfile.gettempdir(), "no_such_dir_pica_iv")}),
    ]
    for label, st, entries, custom, extra in cases:
        gui = _make_param_gui(st, entries, custom=custom, **extra)
        try:
            gui._collect_params()
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted: {label}")


def test_start_measurement_refuses_bad_params_without_touching_backend():
    saved = iv.messagebox
    iv.messagebox = MagicMock()
    try:
        gui = _make_param_gui(iv.SWEEP_ZERO_TO_MAX, dict(BASE_ENTRIES, **{"Compliance": "0"}))
        gui.is_running = False
        gui.logs = []
        gui.log = lambda m: gui.logs.append(m)
        gui.backend = FakeBackend(gui)
        gui.start_measurement()
        assert gui.is_running is False
        assert gui.backend.calls == []
        assert iv.messagebox.showerror.called      # pre-run dialog is allowed
        assert any("Cannot start" in m for m in gui.logs)
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


def test_worker_owns_shutdown_in_finally_and_uses_interruptible_wait():
    body = _body("_measurement_worker")
    assert "finally:" in body and "self.backend.shutdown()" in body.split("finally:")[1]
    assert "wait=self.stop_event.wait" in body


def test_gui_shows_the_custom_list_example():
    assert "CUSTOM_LIST_EXAMPLE" in _body("_on_sweep_type_change")
    assert "custom_list_hint" in SOURCE
    assert iv.MeasurementAppGUI.PROGRAM_VERSION == "13.0"


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
