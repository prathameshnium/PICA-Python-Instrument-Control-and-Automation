"""End-to-end simulation of IV_K2400_GUI.py and IV_K6517B_GUI.py through a
REAL Tk event loop (no hardware).

A fake instrument driver is installed in place of the pymeasure class, the
GUI is built exactly as a user sees it, entries are typed, Start is pressed,
the Tk loop is pumped, and the outcome is checked: data-file rows and
header, console text, button states, the title banner, the order of
instrument actions (configure, set-points, shutdown last, in the worker
thread), and that no message box opens during or after a run. Four runs
per module:
  run 1  finished sweep           run 3  Custom List with the on-screen example
  run 2  Stop pressed mid-sweep   run 4  instrument error mid-sweep

Needs a working Tcl/Tk; under pytest the module is skipped when a Tk root
cannot be created. Runnable as plain Python as well as under pytest.
"""
import importlib.util
import os
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import tkinter as tk
    _probe = tk.Tk()
    _probe.destroy()
    TK_OK = True
except Exception:  # noqa: BLE001
    tk = None
    TK_OK = False

try:
    import pytest
except ImportError:
    pytest = None

FAIL = []


class _TkUnavailable(Exception):
    """Raised when a Tk root cannot be created at run time. Under pytest
    with other GUI test modules collected, Tk reports "Can't find a usable
    tk.tcl" even though the same test passes alone and as plain Python."""


def _new_root():
    try:
        return tk.Tk()
    except Exception as e:  # noqa: BLE001
        raise _TkUnavailable(str(e))


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pump(root, app, timeout=30):
    t0 = time.time()
    while app.is_running and time.time() - t0 < timeout:
        root.update()
        time.sleep(0.02)
    root.update()
    return not app.is_running


def rows_of(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    hdr = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#")]
    return hdr, body[0], body[1:]


# ---------------------------------------------------------------- fakes
class FakeK2400:
    R_SAMPLE = 1.0e6
    instances = []

    def __init__(self, address, **kw):
        self.events = []
        self.opened_in = threading.current_thread().name
        self.id = "KEITHLEY INSTRUMENTS INC.,MODEL 2400,FAKE,C32"
        self.level = 0.0
        self.fail_after_reads = None
        self.reads = 0
        FakeK2400.instances.append(self)

    def __getattr__(self, name):            # any other method: record and accept
        def f(*a, **k):
            self.events.append((name, a))
        return f

    def __setattr__(self, name, value):
        if name in ("source_current_range", "compliance_voltage", "source_current"):
            self.events.append(("set:" + name, value))
        object.__setattr__(self, name, value)

    def ramp_to_current(self, target, steps=30, pause=20e-3):
        self.events.append(("ramp", target))
        self.level = target

    @property
    def voltage(self):
        self.reads += 1
        if self.fail_after_reads is not None and self.reads > self.fail_after_reads:
            raise IOError("simulated GPIB timeout on :READ?")
        return self.level * self.R_SAMPLE

    def ask(self, cmd):
        return "1" if abs(self.level * self.R_SAMPLE) >= 4.9 else "0"

    def shutdown(self):
        self.events.append(("shutdown", ()))


class FakeK6517B:
    R_SAMPLE = 2e9
    instances = []

    def __init__(self, address, timeout=None, **kw):
        self.events = []
        self.opened_in = threading.current_thread().name
        self.id = "KEITHLEY INSTRUMENTS INC.,MODEL 6517B,FAKE,A09"
        self._sv = 0.0
        self.fail_after_reads = None
        self.reads = 0
        FakeK6517B.instances.append(self)

    def __getattr__(self, name):
        def f(*a, **k):
            self.events.append((name, a))
        return f

    def __setattr__(self, name, value):
        if name == "source_voltage":
            self.events.append(("set:source_voltage", value))
            object.__setattr__(self, "_sv", value)
            return
        if name == "current_nplc":
            self.events.append(("set:current_nplc", value))
        object.__setattr__(self, name, value)

    @property
    def source_voltage(self):
        return self._sv

    @property
    def resistance(self):
        self.reads += 1
        if self.fail_after_reads is not None and self.reads > self.fail_after_reads:
            raise IOError("simulated GPIB timeout on :READ?")
        return 9.9e37 if self._sv == 0 else self.R_SAMPLE   # instrument overflows at 0 V

    def shutdown(self):
        self.events.append(("shutdown", ()))


def run_case(title, app, root, start, fake_cls, expect, stop_after_points=None, mb=None):
    print(f"\n[{title}]")
    n_before = len(fake_cls.instances)
    start()
    check(app.is_running, "run started (is_running)")
    if stop_after_points is not None:
        t0 = time.time()
        while time.time() - t0 < 30:
            root.update()
            time.sleep(0.02)
            if len(app.data_storage[expect['key']]) >= stop_after_points:
                break
        (app.stop_measurement if hasattr(app, "stop_measurement") else app.stop_experiment)()
        check(str(app.stop_button['state']) == 'disabled', "Stop button disabled after press")
    check(pump(root, app), "run finished within the timeout")
    inst = fake_cls.instances[-1]
    check(len(fake_cls.instances) == n_before + 1, "exactly one instrument session opened")
    check(inst.opened_in != threading.main_thread().name, f"instrument opened in the worker thread ({inst.opened_in})")
    check(inst.events[-1][0] == "shutdown", f"last instrument action is shutdown ({inst.events[-1][0]})")
    check(str(app.start_button['state']) == 'normal', "Start button re-enabled")
    check(str(app.stop_button['state']) == 'disabled', "Stop button disabled")
    title_now = root.title()
    check(expect['banner'] in title_now, f"window title shows '{expect['banner']}' ({title_now!r})")
    check(mb is None or (not mb.showinfo.called and not mb.showerror.called),
          "no message box opened during/after the run")
    return inst


def console_text(app):
    w = getattr(app, "console_widget", None) or getattr(app, "console", None)
    return w.get("1.0", tk.END)


# ====================================================================== K2400
def sim_k2400(tmp):
    m = load("pica/keithley/k2400/IV_K2400_GUI.py", "e2e_k2400")
    m.Keithley2400 = FakeK2400
    m.PYMEASURE_AVAILABLE = True
    m.pyvisa = None
    m.messagebox = MagicMock()
    root = _new_root()
    root.withdraw()
    app = m.MeasurementAppGUI(root)
    root.update()
    app.entries["Sample Name"].insert(0, "SimK2400")
    app.file_location_path = tmp
    app.keithley_combobox['values'] = ("GPIB0::24::INSTR",)
    app.keithley_combobox.set("GPIB0::24::INSTR")

    def set_entry(key, val):
        app.entries[key].config(state='normal')
        app.entries[key].delete(0, tk.END)
        app.entries[key].insert(0, val)

    # ---- run 1: 0 to Max, 10 uA step 2 uA, loops 2, compliance 5 V
    set_entry("Max Current", "10"); set_entry("Step Current", "2"); set_entry("Num Loops", "2")
    set_entry("Compliance", "5"); set_entry("Delay", "0.02")
    app.sweep_type_var.set(m.SWEEP_ZERO_TO_MAX); app._on_sweep_type_change()
    inst = run_case("K2400 run 1: 0 to Max x2 loops, finished", app, root, app.start_measurement,
                    FakeK2400, {'key': 'current', 'banner': 'SWEEP COMPLETE'}, mb=m.messagebox)
    hdr, cols, body = rows_of(app.data_filepath)
    check(cols.split("\t") == ["Current (A)", "Voltage (V)", "Resistance (Ohm)", "Compliance"], "file columns")
    check(len(body) == 12, f"12 rows written ({len(body)})")
    cur = [float(r.split("\t")[0]) for r in body]
    vol = [float(r.split("\t")[1]) for r in body]
    check([round(c * 1e6, 9) for c in cur] == [0, 2, 4, 6, 8, 10] * 2, "set-points 0..10 uA twice")
    check(all(abs(v - c * 1e6) < 1e-9 for c, v in zip(cur, vol)), "V = I * R for every row")
    check(all(abs(float(r.split("\t")[2]) - 1e6) < 1e-3 for r in body[1:6]), "R column = 1 MOhm")
    check(body[0].split("\t")[2] == "nan", "R is nan at 0 A")
    names = [e[0] for e in inst.events]
    check(names[:4] == ["reset", "use_front_terminals", "apply_current", "measure_voltage"], "configure order")
    check(names.index("measure_voltage") < names.index("set:compliance_voltage"), "volts enabled before compliance")
    check(("set:source_current_range", 1e-05) in inst.events or
          any(e[0] == "set:source_current_range" and abs(e[1] - 1e-5) < 1e-15 for e in inst.events), "range = 10 uA")
    check(inst.events[names.index("enable_source") - 1] == ("set:source_current", 0), "output ON at 0 A")
    check(names.count("shutdown") == 1, "shutdown exactly once")
    ramps = [e[1] for e in inst.events if e[0] == "ramp"]
    check(len(ramps) == 12 and abs(ramps[-1] - 1e-5) < 1e-15, "12 ramps, last to 10 uA, then shutdown")
    check("SWEEP COMPLETE" in console_text(app), "console says SWEEP COMPLETE")
    check(app.progress_bar['value'] == 12, "progress bar full")
    check(any("Compliance: 5 V" in h for h in hdr) and any("Loops: 2" in h for h in hdr), "header lines")
    check("compliance reached" in console_text(app), "compliance warning logged (6..10 uA give 6..10 V > 5 V)")
    check([r.split("\t")[3] for r in body][:6] == ["0", "0", "0", "1", "1", "1"], "compliance column flags 6..10 uA")

    # ---- run 2: Loop, long delay, Stop after 2 points
    set_entry("Max Current", "4"); set_entry("Step Current", "1"); set_entry("Num Loops", "1")
    set_entry("Delay", "3")
    app.sweep_type_var.set(m.SWEEP_LOOP); app._on_sweep_type_change()
    t0 = time.time()
    inst = run_case("K2400 run 2: Loop, Stop pressed mid-sweep", app, root, app.start_measurement,
                    FakeK2400, {'key': 'current', 'banner': 'STOPPED'}, stop_after_points=2, mb=m.messagebox)
    check(time.time() - t0 < 12, f"stop honoured quickly despite 3 s delay ({time.time() - t0:.1f} s)")
    _, _, body = rows_of(app.data_filepath)
    check(2 <= len(body) <= 3, f"only the points before Stop were written ({len(body)})")
    check("STOPPED by user" in console_text(app), "console says STOPPED by user")

    # ---- run 3: Custom list with the on-screen example
    app.sweep_type_var.set(m.SWEEP_CUSTOM); app._on_sweep_type_change()
    set_entry("Delay", "0.01")
    check(app.custom_list_text.get("1.0", tk.END).strip() == m.CUSTOM_LIST_EXAMPLE.strip(), "example pre-filled")
    inst = run_case("K2400 run 3: Custom List (example), finished", app, root, app.start_measurement,
                    FakeK2400, {'key': 'current', 'banner': 'SWEEP COMPLETE'}, mb=m.messagebox)
    hdr, _, body = rows_of(app.data_filepath)
    n_expected = len(m.parse_custom_list(m.CUSTOM_LIST_EXAMPLE))
    check(len(body) == n_expected, f"{n_expected} rows for the example list ({len(body)})")
    check(any("Sweep type: Custom List" in h for h in hdr) and any("Custom list (uA): 0, 1, 2, 5," in h for h in hdr),
          "custom list recorded in header")
    cur = [round(float(r.split("\t")[0]) * 1e6, 9) for r in body]
    check(cur[:8] == [0, 1, 2, 5, 10, 20, 50, 100] and cur[-1] == 0, "example order preserved")

    # ---- run 4: instrument error on the 3rd read
    app.sweep_type_var.set(m.SWEEP_ZERO_TO_MAX); app._on_sweep_type_change()
    set_entry("Max Current", "5"); set_entry("Step Current", "1")
    orig_init = FakeK2400.__init__

    def failing_init(self, *a, **k):
        orig_init(self, *a, **k)
        self.fail_after_reads = 2
    FakeK2400.__init__ = failing_init
    try:
        inst = run_case("K2400 run 4: GPIB error mid-sweep", app, root, app.start_measurement,
                        FakeK2400, {'key': 'current', 'banner': 'ABORTED'}, mb=m.messagebox)
    finally:
        FakeK2400.__init__ = orig_init
    _, _, body = rows_of(app.data_filepath)
    check(len(body) == 2, f"2 rows before the error ({len(body)})")
    check("simulated GPIB timeout" in console_text(app) and "Traceback" in console_text(app), "traceback in console")

    # ---- pre-run validation still uses a dialog and never starts
    m.messagebox.reset_mock()
    set_entry("Compliance", "0")
    app.start_measurement()
    check(not app.is_running and m.messagebox.showerror.called, "bad compliance: dialog, no run")
    root.destroy()


# ====================================================================== K6517B
def sim_k6517b(tmp):
    m = load("pica/keithley/k6517b/High_Resistance/IV_K6517B_GUI.py", "e2e_k6517b")
    m.Keithley6517B = FakeK6517B
    m.VisaIOError = None
    m.PYMEASURE_AVAILABLE = True
    m.messagebox = MagicMock()
    real_sleep = time.sleep
    time.sleep = lambda s: real_sleep(min(s, 0.02))     # shorten the 7 s zero-correct
    root = _new_root()
    root.withdraw()
    app = m.HighResistanceIV_GUI(root)
    root.update()
    app.entries["Sample Name"].insert(0, "Sim6517B")
    app.file_location_path = tmp
    app.keithley_combobox['values'] = ("GPIB1::27::INSTR",)
    app.keithley_combobox.set("GPIB1::27::INSTR")

    def set_entry(key, val):
        app.entries[key].delete(0, tk.END)
        app.entries[key].insert(0, val)

    # ---- run 1: linear -10..10 V in 5 points (default mode)
    set_entry("Start V", "-10"); set_entry("Stop V", "10"); set_entry("Steps", "5")
    set_entry("Delay (s)", "0.02"); set_entry("Loops", "1")
    check(app.sweep_type_var.get() == m.SWEEP_LINEAR, "linear is the default sweep type")
    inst = run_case("K6517B run 1: linear -10..10 V, finished", app, root, app.start_measurement,
                    FakeK6517B, {'key': 'voltage_applied', 'banner': 'SWEEP COMPLETE'}, mb=m.messagebox)
    hdr, cols, body = rows_of(app.data_filepath)
    check(cols == "Time (s),Applied Voltage (V),Measured Current (A),Resistance (Ohms)", "file columns")
    check(len(body) == 5, f"5 rows ({len(body)})")
    vs = [float(r.split(",")[1]) for r in body]
    check(vs == [-10, -5, 0, 5, 10], f"applied voltages {vs}")
    cur = [r.split(",")[2] for r in body]
    check(abs(float(cur[0]) - (-5e-9)) < 1e-15 and cur[2] == "nan" and abs(float(cur[4]) - 5e-9) < 1e-15,
          "I = V/R, nan at 0 V (instrument overflow)")
    writes = [e[1][0] for e in inst.events if e[0] == "write"]
    check(writes == [':SYSTem:ZCHeck ON', ':SYSTem:ZCORrect:ACQuire', ':SYSTem:ZCHeck OFF',
                     ':SYSTem:ZCORrect ON', ':SOURce:VOLTage:RANGe 100'], f"zero-correct + range writes {writes}")
    names = [e[0] for e in inst.events]
    i_on = names.index("enable_source")
    check(inst.events[i_on - 1] == ("set:source_voltage", 0), "output ON at 0 V")
    check(names.index("reset") < names.index("measure_resistance") < names.index("set:current_nplc") < i_on, "init order")
    sets = [e[1] for e in inst.events if e[0] == "set:source_voltage"]
    check(sets == [0, -10, -5, 0, 5, 10], f"voltage set sequence {sets}")
    check(names.count("shutdown") == 1 and names[-1] == "shutdown", "shutdown once, last")
    check(any("source range: 100 V" in h for h in hdr), "header records the 100 V range")

    # ---- run 2: Loop 0..500 V step 250, 1000 V range, Stop after 2 points
    app.sweep_type_var.set(m.SWEEP_LOOP); app._on_sweep_type_change(); root.update()
    check(app.entries["Max V"].winfo_ismapped() and not app.entries["Start V"].winfo_ismapped(), "Loop shows Max/Step, hides Start/Stop")
    set_entry("Max V", "500"); set_entry("Step V", "250"); set_entry("Delay (s)", "3")
    t0 = time.time()
    inst = run_case("K6517B run 2: Loop to 500 V, Stop mid-sweep", app, root, app.start_measurement,
                    FakeK6517B, {'key': 'voltage_applied', 'banner': 'STOPPED'}, stop_after_points=2, mb=m.messagebox)
    check(time.time() - t0 < 12, f"stop honoured quickly despite 3 s delay ({time.time() - t0:.1f} s)")
    writes = [e[1][0] for e in inst.events if e[0] == "write"]
    check(':SOURce:VOLTage:RANGe 1000' in writes, "1000 V range selected for a 500 V sweep")
    _, _, body = rows_of(app.data_filepath)
    check(2 <= len(body) <= 3, f"only points before Stop written ({len(body)})")

    # ---- run 3: Custom list example
    app.sweep_type_var.set(m.SWEEP_CUSTOM); app._on_sweep_type_change(); root.update()
    set_entry("Delay (s)", "0.01")
    check(app.custom_list_text.get("1.0", tk.END).strip() == m.CUSTOM_LIST_EXAMPLE.strip(), "example pre-filled")
    inst = run_case("K6517B run 3: Custom List (example), finished", app, root, app.start_measurement,
                    FakeK6517B, {'key': 'voltage_applied', 'banner': 'SWEEP COMPLETE'}, mb=m.messagebox)
    hdr, _, body = rows_of(app.data_filepath)
    n_expected = len(m.parse_custom_list(m.CUSTOM_LIST_EXAMPLE))
    check(len(body) == n_expected, f"{n_expected} rows ({len(body)})")
    vs = [float(r.split(",")[1]) for r in body]
    check(vs[:8] == [0, 1, 2, 5, 10, 20, 50, 100] and vs[-1] == 0, "example order preserved")
    check(any("custom list: 0, 1, 2, 5," in h for h in hdr), "custom list in header")

    # ---- run 4: 0 to Max with an error on the 3rd read
    app.sweep_type_var.set(m.SWEEP_ZERO_TO_MAX); app._on_sweep_type_change(); root.update()
    set_entry("Max V", "50"); set_entry("Step V", "10")
    orig_init = FakeK6517B.__init__

    def failing_init(self, *a, **k):
        orig_init(self, *a, **k)
        self.fail_after_reads = 2
    FakeK6517B.__init__ = failing_init
    try:
        inst = run_case("K6517B run 4: GPIB error mid-sweep", app, root, app.start_measurement,
                        FakeK6517B, {'key': 'voltage_applied', 'banner': 'ABORTED'}, mb=m.messagebox)
    finally:
        FakeK6517B.__init__ = orig_init
    _, _, body = rows_of(app.data_filepath)
    check(len(body) == 2, f"2 rows before the error ({len(body)})")
    check("simulated GPIB timeout" in console_text(app), "error text in console")

    m.messagebox.reset_mock()
    set_entry("Max V", "1500")
    app.start_measurement()
    check(not app.is_running and m.messagebox.showerror.called, "1500 V refused with a dialog, no run")
    time.sleep = real_sleep
    root.destroy()


def _run(sim):
    if not TK_OK:
        if pytest is not None:
            pytest.skip("no usable Tcl/Tk on this machine")
        print("SKIP (no Tk)")
        return
    del FAIL[:]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sim(tmp)
    except _TkUnavailable as e:
        if pytest is not None:
            pytest.skip(f"Tk root unavailable in this process: {e}")
        print(f"SKIP (Tk root unavailable: {e})")
        return
    assert not FAIL, "failed checks: " + "; ".join(FAIL)


def test_k2400_gui_end_to_end():
    _run(sim_k2400)


def test_k6517b_gui_end_to_end():
    _run(sim_k6517b)


if __name__ == "__main__":
    failures = 0
    for name in ("test_k2400_gui_end_to_end", "test_k6517b_gui_end_to_end"):
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: {e!r}")
    print("ALL PASSED" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
