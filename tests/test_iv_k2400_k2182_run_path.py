"""Run path of IV_K2400_K2182_GUI.py (2400 sources I, 2182 measures V).

Driven against a fake Keithley2400 driver and a fake 2182 VISA resource
that record every action in order:

  - parameter validation: a Start Current of 0 mA is accepted (the old
    all(params.values()) refused it), the step sign follows the direction,
    both VISA addresses are required and may not be the same, and a sweep
    above 1.05 A or a compliance outside (0, 210] V is refused;
  - the 2400 source range follows the largest |set-point| of the sweep
    (not the Stop current), the output is enabled at 0 A, and the 2182 is
    reset after the 2400 is configured;
  - the 2182 read sequence is the one the R-T modules of this family use,
    verbatim and in order, and the voltage is the mean of the 2 samples;
  - compliance comes from :SENS:VOLT:PROT:TRIP? on the 2400;
  - the single worker thread owns both instruments: shutdown runs exactly
    once on every exit path and both the 2400 ramp-down and the 2182
    *rst/close happen even when one of them fails;
  - the GUI queue pump writes one 4-column row per point and never opens a
    message box.

No hardware and no Tk root. Runnable as plain Python as well as under pytest.
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

MODULE_PATH = os.path.join(REPO_ROOT, "pica", "keithley", "k2400_2182", "IV_K2400_K2182_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


iv = _load("iv_k2400_k2182_under_test", MODULE_PATH)
SOURCE = open(MODULE_PATH, encoding="utf-8").read()

K2182_SEQUENCE = [
    "status:measurement:enable 512; *sre 1",
    "sample:count 2",
    "trigger:source bus",
    "trigger:delay 0.1",
    "trace:points 2",
    "trace:feed sense1; feed:control next",
    "initiate",
    "<assert_trigger>",
    "<wait_for_srq>",
    "trace:data?",
    "status:measurement?",
    "trace:clear; feed:control next",
]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeK2400:
    TRACKED = ("source_current_range", "compliance_voltage", "source_current")

    def __init__(self, address, **kwargs):
        object.__setattr__(self, "events", [])
        object.__setattr__(self, "id", "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1,C32")
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

    def apply_current(self, *a, **k):
        self._call("apply_current")

    def enable_source(self):
        self._call("enable_source")

    def ramp_to_current(self, target, steps=30, pause=20e-3):
        self._call("ramp_to_current", target)
        object.__setattr__(self, "source_current", target)

    def ask(self, cmd):
        self._call("ask", cmd)
        return self.trip if "TRIP" in cmd else "0"

    def write(self, cmd):
        self._call("write", cmd)

    def shutdown(self):
        self._call("shutdown")
        if self.fail_shutdown:
            raise RuntimeError("VISA timeout")


class FakeK2182:
    def __init__(self):
        self.actions = []
        self.samples = [1.0e-3, 1.2e-3]
        self.fail_close = False

    def write(self, cmd):
        self.actions.append(cmd)
        if cmd == "*rst" and self.fail_close:
            raise RuntimeError("2182 gone")

    def query(self, cmd):
        self.actions.append(cmd)
        return "KEITHLEY INSTRUMENTS INC.,MODEL 2182,1,A01" if "IDN" in cmd else "0"

    def assert_trigger(self):
        self.actions.append("<assert_trigger>")

    def wait_for_srq(self, timeout=None):
        self.actions.append("<wait_for_srq>")

    def query_ascii_values(self, cmd):
        self.actions.append(cmd)
        return list(self.samples)

    def close(self):
        self.actions.append("<close>")


class FakeRM:
    def __init__(self):
        self.k2182 = FakeK2182()
        self.opened = []

    def open_resource(self, addr):
        self.opened.append(addr)
        return self.k2182


class FakeBackend:
    def __init__(self, gui=None, stop_after=None, fail_at=None, fail_connect=False):
        self.calls = []
        self.gui = gui
        self.stop_after = stop_after
        self.fail_at = fail_at
        self.fail_connect = fail_connect

    def connect(self, a, b, log=print):
        self.calls.append(("connect", a, b))
        if self.fail_connect:
            raise ConnectionError("no 2182")

    def configure_instruments(self, compliance, rng):
        self.calls.append(("configure", compliance, rng))

    def measure_voltage_at_current(self, current, delay, wait=None):
        n = sum(1 for c in self.calls if c[0] == "measure")
        self.calls.append(("measure", current))
        if self.fail_at is not None and n == self.fail_at:
            raise IOError("SRQ timeout")
        if self.stop_after is not None and n == self.stop_after:
            self.gui.stop_event.set()
        if wait is not None and wait(0.0):
            return None
        return (current * 10.0, False)

    def shutdown(self, log=print):
        self.calls.append(("shutdown",))


def _real_backend():
    iv.Keithley2400 = FakeK2400
    iv.PYMEASURE_AVAILABLE = True
    iv.pyvisa = None
    be = iv.IV_Backend()
    be.rm = FakeRM()
    return be


def _make_gui(tmpdir, backend=None):
    gui = object.__new__(iv.IV_GUI)
    gui.root = MagicMock()
    gui.root.after = MagicMock(return_value="after#1")
    gui.logs = []
    gui.log = lambda msg: gui.logs.append(str(msg))
    gui.beeps = []
    gui._beep = lambda times=2: gui.beeps.append(times)
    gui.is_running = True
    gui.stop_event = threading.Event()
    gui.result_queue = queue.Queue()
    gui.measurement_thread = None
    gui._pump_after_id = None
    gui.backend = backend if backend is not None else FakeBackend(gui)
    gui.data_filepath = os.path.join(tmpdir, "run_IV.csv")
    gui.params = {'name': 'S1'}
    gui.current_points = []
    gui.data_storage = {'current': [], 'voltage': [], 'resistance': []}
    gui.entries = {"Sample Name": MagicMock(), "Save Location": MagicMock()}
    for name in ("line_main", "ax_main", "canvas", "start_button", "stop_button",
                 "custom_list_text", "sweep_type_cb", "k2400_cb", "k2182_cb", "figure"):
        setattr(gui, name, MagicMock())
    return gui


class _Entry:
    def __init__(self, text):
        self.text = text

    def get(self, *a):
        return self.text


BASE = {"Sample Name": "S1", "Save Location": tempfile.gettempdir(), "Loops": "1",
        "Compliance (V)": "10", "Dwell Time (s)": "0.5", "Start Current (mA)": "-1",
        "Stop Current (mA)": "1", "Max Current (mA)": "1", "Step Current (mA)": "0.5"}


def _make_param_gui(sweep_type, entries, custom="", k2400="GPIB0::24::INSTR",
                    k2182="GPIB0::7::INSTR"):
    gui = object.__new__(iv.IV_GUI)
    gui.sweep_type_var = _Entry(sweep_type)
    gui.entries = {k: _Entry(v) for k, v in entries.items()}
    gui.k2400_cb = _Entry(k2400)
    gui.k2182_cb = _Entry(k2182)
    gui.custom_list_text = _Entry(custom)
    return gui


def _params(delay=0.0):
    return {'name': 'S1', 'k2400_visa': 'GPIB0::24::INSTR', 'k2182_visa': 'GPIB0::7::INSTR',
            'compliance_v': 10.0, 'max_abs_current_A': 1e-3, 'delay_s': delay}


def _drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

def test_zero_start_current_is_accepted():
    gui = _make_param_gui(iv.SWEEP_LINEAR, dict(BASE, **{"Start Current (mA)": "0"}))
    params, pts = gui._validate_and_get_params()
    assert [round(p * 1e3, 9) for p in pts] == [0, 0.5, 1.0]
    assert math.isclose(params['max_abs_current_A'], 1e-3)


def test_linear_direction_and_range_from_largest_point():
    gui = _make_param_gui(iv.SWEEP_LINEAR, dict(BASE, **{"Start Current (mA)": "-5",
                                                           "Stop Current (mA)": "1",
                                                           "Step Current (mA)": "1"}))
    params, pts = gui._validate_and_get_params()
    assert [round(p * 1e3, 9) for p in pts] == [-5, -4, -3, -2, -1, 0, 1]
    assert math.isclose(params['max_abs_current_A'], 5e-3), "range must cover -5 mA, not 1 mA"
    gui = _make_param_gui(iv.SWEEP_LINEAR, dict(BASE, **{"Start Current (mA)": "1",
                                                           "Stop Current (mA)": "-1"}))
    _, pts = gui._validate_and_get_params()
    assert [round(p * 1e3, 9) for p in pts] == [1, 0.5, 0, -0.5, -1], "positive step, downward sweep"


def test_zero_to_max_loop_and_custom_from_entries():
    gui = _make_param_gui(iv.SWEEP_ZERO_TO_MAX, BASE)
    _, pts = gui._validate_and_get_params()
    assert [round(p * 1e3, 9) for p in pts] == [0, 0.5, 1]
    gui = _make_param_gui(iv.SWEEP_LOOP, dict(BASE, **{"Loops": "3"}))
    _, pts = gui._validate_and_get_params()
    assert len(pts) == 3 * 9
    gui = _make_param_gui(iv.SWEEP_CUSTOM, BASE, custom=iv.CUSTOM_LIST_EXAMPLE)
    params, pts = gui._validate_and_get_params()
    assert math.isclose(params['max_abs_current_A'], 10e-3)
    assert len(pts) == len(iv.parse_custom_list(iv.CUSTOM_LIST_EXAMPLE))


def test_refusals():
    cases = [
        ("above 1.05 A", iv.SWEEP_ZERO_TO_MAX, dict(BASE, **{"Max Current (mA)": "1100"}), "", {}),
        ("zero compliance", iv.SWEEP_LINEAR, dict(BASE, **{"Compliance (V)": "0"}), "", {}),
        ("compliance > 210", iv.SWEEP_LINEAR, dict(BASE, **{"Compliance (V)": "211"}), "", {}),
        ("zero step", iv.SWEEP_LINEAR, dict(BASE, **{"Step Current (mA)": "0"}), "", {}),
        ("zero max", iv.SWEEP_LOOP, dict(BASE, **{"Max Current (mA)": "0"}), "", {}),
        ("negative dwell", iv.SWEEP_LINEAR, dict(BASE, **{"Dwell Time (s)": "-0.1"}), "", {}),
        ("zero loops", iv.SWEEP_LINEAR, dict(BASE, **{"Loops": "0"}), "", {}),
        ("text", iv.SWEEP_LINEAR, dict(BASE, **{"Stop Current (mA)": "one"}), "", {}),
        ("empty custom", iv.SWEEP_CUSTOM, BASE, "", {}),
        ("no name", iv.SWEEP_LINEAR, dict(BASE, **{"Sample Name": " "}), "", {}),
        ("no save", iv.SWEEP_LINEAR, dict(BASE, **{"Save Location": ""}), "", {}),
        ("no 2182", iv.SWEEP_LINEAR, BASE, "", {"k2182": ""}),
        ("same address", iv.SWEEP_LINEAR, BASE, "", {"k2182": "GPIB0::24::INSTR"}),
    ]
    for label, st, entries, custom, extra in cases:
        gui = _make_param_gui(st, entries, custom=custom, **extra)
        try:
            gui._validate_and_get_params()
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted: {label}")


# ---------------------------------------------------------------------------
# Backend against the fakes
# ---------------------------------------------------------------------------

def test_connect_and_configure_order_and_values():
    be = _real_backend()
    logs = []
    be.connect("GPIB0::24::INSTR", "GPIB0::7::INSTR", log=logs.append)
    assert be.rm.opened == ["GPIB0::7::INSTR"]
    assert any("K2400 Connected" in m for m in logs) and any("K2182 Connected" in m for m in logs)
    saved = time.sleep
    time.sleep = lambda s: None
    try:
        be.configure_instruments(10.0, 5e-3)
    finally:
        time.sleep = saved
    names = [e[1] for e in be.k2400.events]
    assert names == ["reset", "apply_current", "source_current_range", "compliance_voltage",
                     "source_current", "enable_source"], names
    vals = {e[1]: e[2] for e in be.k2400.events if e[0] == "set"}
    assert vals == {"source_current_range": 5e-3, "compliance_voltage": 10.0, "source_current": 0}
    assert be.rm.k2182.actions[-1] == "*rst; status:preset; *cls"


def test_read_voltage_sequence_mean_and_compliance():
    be = _real_backend()
    be.connect("a", "b", log=lambda m: None)
    be.rm.k2182.actions.clear()
    v, tripped = be.read_voltage()
    assert be.rm.k2182.actions == K2182_SEQUENCE, be.rm.k2182.actions
    assert math.isclose(v, 1.1e-3) and tripped is False
    be.k2400.trip = "1"
    be.rm.k2182.samples = []
    v, tripped = be.read_voltage()
    assert math.isnan(v) and tripped is True
    asks = [e for e in be.k2400.events if e[1] == "ask"]
    assert len(asks) == 2 and all("PROT:TRIP?" in e[2][0] for e in asks)


def test_measure_ramps_waits_reads_and_honours_stop():
    be = _real_backend()
    be.connect("a", "b", log=lambda m: None)
    waited = []
    out = be.measure_voltage_at_current(2e-3, 0.7, wait=lambda s: waited.append(s) or False)
    assert waited == [0.7] and math.isclose(out[0], 1.1e-3)
    ramps = [e for e in be.k2400.events if e[1] == "ramp_to_current"]
    assert ramps[-1][2] == (2e-3,)
    be.rm.k2182.actions.clear()
    assert be.measure_voltage_at_current(3e-3, 0.7, wait=lambda s: True) is None
    assert be.rm.k2182.actions == [], "no 2182 read after Stop"


def test_shutdown_handles_both_instruments_and_failures():
    be = _real_backend()
    be.connect("a", "b", log=lambda m: None)
    k2400, k2182 = be.k2400, be.rm.k2182
    k2400.fail_shutdown = True
    k2182.fail_close = True
    logs = []
    be.shutdown(log=logs.append)           # must not raise
    assert be.k2400 is None and be.k2182 is None
    assert ("call", "write", ("OUTPUT OFF",)) in k2400.events
    assert "*rst" in k2182.actions
    assert any("Warning" in m for m in logs)
    be.shutdown(log=logs.append)           # no-op
    assert sum(1 for e in k2400.events if e[1] == "shutdown") == 1

    be = _real_backend()
    be.connect("a", "b", log=lambda m: None)
    k2182 = be.rm.k2182
    be.shutdown(log=lambda m: None)
    assert k2182.actions[-2:] == ["*rst", "<close>"]


# ---------------------------------------------------------------------------
# Worker lifecycle and queue pump
# ---------------------------------------------------------------------------

def test_worker_paths():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui._measurement_worker(_params(), [0.0, 1e-3])
        items = _drain(gui.result_queue)
        assert items[-1] == ("DONE", "finished")
        assert [i[3] for i in items if i[0] == "DATA"] == [0.0, 1e-3]
        c = gui.backend.calls
        assert c[0][0] == "connect" and c[1] == ("configure", 10.0, 1e-3)
        assert c.count(("shutdown",)) == 1 and c[-1] == ("shutdown",)

        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, stop_after=0)
        gui._measurement_worker(_params(delay=9.0), [1e-3, 2e-3])
        items = _drain(gui.result_queue)
        assert items[-1] == ("DONE", "stopped")
        assert not [i for i in items if i[0] == "DATA"]
        assert gui.backend.calls[-1] == ("shutdown",)

        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, fail_at=1)
        gui._measurement_worker(_params(), [1e-3, 2e-3, 3e-3])
        items = _drain(gui.result_queue)
        assert items[-1] == ("DONE", "error")
        assert "SRQ timeout" in [i for i in items if i[0] == "ERROR"][0][1]
        assert gui.backend.calls[-1] == ("shutdown",)

        gui = _make_gui(d)
        gui.backend = FakeBackend(gui, fail_connect=True)
        gui._measurement_worker(_params(), [1e-3])
        items = _drain(gui.result_queue)
        assert items[-1] == ("DONE", "error") and ("shutdown",) in gui.backend.calls


def test_worker_real_thread_stop_interrupts_long_dwell():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        be = _real_backend()
        gui.backend = be
        saved = time.sleep
        time.sleep = lambda s: None
        try:
            t = threading.Thread(target=gui._measurement_worker,
                                 args=(_params(delay=30.0), [1e-3, 2e-3]), daemon=True)
            t.start()
            pause = threading.Event()
            for _ in range(500):
                if be.k2400 is not None and any(e[1] == "ramp_to_current" for e in be.k2400.events):
                    break
                pause.wait(0.01)
            k2400 = be.k2400
            gui.stop_event.set()
            t.join(timeout=5)
        finally:
            time.sleep = saved
        assert not t.is_alive()
        assert _drain(gui.result_queue)[-1] == ("DONE", "stopped")
        assert ("call", "shutdown", ()) in k2400.events and be.k2400 is None


def test_queue_pump_rows_and_no_dialog():
    saved = iv.messagebox
    iv.messagebox = MagicMock()
    try:
        with tempfile.TemporaryDirectory() as d:
            gui = _make_gui(d)
            gui.params = dict(_params(), sweep_type=iv.SWEEP_LINEAR, start_mA=0, stop_mA=1,
                              step_mA=0.5, max_mA=0, custom_list_str='', num_loops=1)
            gui._write_file_header(gui.params, 3)
            gui._measurement_worker(_params(), [0.0, 5e-4, 1e-3])
            gui._process_queue()
            assert gui.is_running is False
            assert gui.beeps == [2]
            assert not iv.messagebox.showinfo.called and not iv.messagebox.showerror.called
            assert gui.root.after.call_count == 0
            with open(gui.data_filepath, encoding="utf-8") as f:
                lines = f.read().splitlines()
            assert any(ln.startswith("# Sweep type:") for ln in lines)
            rows = [ln for ln in lines if not ln.startswith("#")]
            assert rows[0] == "Current (A),Voltage (V),Resistance (Ohm),Compliance"
            body = [r.split(",") for r in rows[1:]]
            assert len(body) == 3 and body[0][2] == "nan"
            assert float(body[1][0]) == 5e-4 and math.isclose(float(body[1][1]), 5e-3)
            assert math.isclose(float(body[1][2]), 10.0) and body[1][3] == "0"
            assert any("SWEEP COMPLETE" in m for m in gui.logs)
    finally:
        iv.messagebox = saved


def test_stop_only_sets_event():
    with tempfile.TemporaryDirectory() as d:
        gui = _make_gui(d)
        gui.stop_experiment()
        gui.stop_experiment("again")
        assert gui.stop_event.is_set() and gui.backend.calls == []
        assert sum(1 for m in gui.logs if "Stop" in m) == 1


# ---------------------------------------------------------------------------
# Source policy
# ---------------------------------------------------------------------------

def _body(func_name):
    start = SOURCE.index(f"    def {func_name}(")
    nxt = SOURCE.find("\n    def ", start + 1)
    return SOURCE[start:nxt if nxt > 0 else None]


def test_no_message_box_on_the_run_path():
    for fn in ("_measurement_worker", "_process_queue", "_handle_point",
               "_finish_run", "stop_experiment"):
        assert "messagebox" not in _body(fn), f"{fn} opens a dialog"


def test_worker_owns_shutdown_and_uses_interruptible_wait():
    body = _body("_measurement_worker")
    assert "finally:" in body and "self.backend.shutdown(" in body.split("finally:")[1]
    assert "wait=self.stop_event.wait" in body
    assert "self.backend.connect(" not in _body("start_experiment")


def test_gui_offers_sweep_types_with_example_and_default_linear():
    assert iv.SWEEP_TYPES == (iv.SWEEP_LINEAR, iv.SWEEP_ZERO_TO_MAX, iv.SWEEP_LOOP, iv.SWEEP_CUSTOM)
    assert "CUSTOM_LIST_EXAMPLE" in _body("_on_sweep_type_change")
    assert "self.sweep_type_cb.set(SWEEP_LINEAR)" in SOURCE
    assert iv.IV_GUI.PROGRAM_VERSION == "3.0"


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
