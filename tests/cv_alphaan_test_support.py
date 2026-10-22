"""Shared support for the Alpha-AN C-V module tests (test_cv_alphaan.py).

Loads novocontrol/CV_AlphaAN_GUI.py and its plain-script twin from disk and
provides FakeCVAlpha: a capacitor with a series resistance behind a fake
Novocontrol Alpha that answers the command subset the modules use (set
commands ack "OK", MST + SRQ + ZRE?, *RST), keeps the DC bias state from
DCV= / DCE=, records every byte in order, can return a chosen ZRE? status
on chosen points, fail ZRE? on demand, answer NA to DCV while the bias is
off and MR to bias commands while a task runs. The modules' time.sleep is
replaced by a no-op and pyvisa by a stand-in that hands out the fake, so
no hardware, VISA library or Tk root is needed. Not a test file itself.
"""

import importlib.util
import math
import os
import queue
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from alphaan_port_test_support import FakeAlpha, FakeRM  # noqa: E402

REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

NOVO_DIR = os.path.join(REPO_ROOT, "pica", "novocontrol")
GUI_PATH = os.path.join(NOVO_DIR, "CV_AlphaAN_GUI.py")
SCRIPT_PATH = os.path.join(NOVO_DIR, "Instrument_Control",
                           "CV_AlphaAN_Simple_Instrument_Control.py")
PLOTTER_PATH = os.path.join(REPO_ROOT, "pica", "utils", "PlotterUtil_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gui = _load("cv_alphaan_gui_under_test", GUI_PATH)
script = _load("cv_alphaan_script_under_test", SCRIPT_PATH)
MODULES = (gui, script)

# The backend sleeps through time.sleep (the *RST rejoin wait, settles):
# give both modules a time namespace whose sleep is a no-op.
_fast_time = types.SimpleNamespace(
    sleep=lambda s: None, perf_counter=time.perf_counter,
    monotonic=time.monotonic, time=time.time)
for _m in MODULES:
    _m.time = _fast_time


class FakeCVAlpha(FakeAlpha):
    """Capacitor `c_f` with series resistance `r_ohm` on a ZG4.

    status_at: {measurement index: ZRE? status}; fail_at: ZRE? indices
    that raise (bus error); f_offset: f_actual = GFR * (1 + f_offset);
    na_when_off: DCV answers NA while DCE is 0; mr_when_running: DCV/DCE
    answer MR while a task runs; on_measure(idx): called at each ZRE?.
    """

    def __init__(self, c_f=1e-9, r_ohm=100.0, status_at=None, fail_at=(),
                 f_offset=1e-3, na_when_off=False, mr_when_running=False,
                 srq_raises=False):
        super().__init__(srq_raises=srq_raises)
        self.c = c_f
        self.r = r_ohm
        self.status_at = dict(status_at or {})
        self.fail_at = set(fail_at)
        self.f_offset = f_offset
        self.na_when_off = na_when_off
        self.mr_when_running = mr_when_running
        self.v = 0.0
        self.bias_on = False
        self.freq = 1000.0
        self.acv = 0.0
        self.task_running = False
        self.measurements = 0
        self.on_measure = None
        self.bias_history = []          # (v, bias_on) after every accepted bias command
        self.dead = False               # every transfer raises (link lost)

    def _check_alive(self):
        if self.dead:
            raise IOError("VI_ERROR_TMO: timeout")

    def write(self, command):
        super().write(command)
        self._check_alive()
        if command == "MST":
            self.task_running = True
        elif command == "*RST":
            self.v, self.bias_on, self.acv = 0.0, False, 0.0
            self.task_running = False

    def wait_for_srq(self, timeout_ms=25000):
        super().wait_for_srq(timeout_ms)
        self.task_running = False

    def query(self, command):
        self.traffic.append(("query", command))
        self._check_alive()
        cmd = command.strip()
        if cmd.startswith(("DCV=", "DCE=")):
            if self.mr_when_running and self.task_running:
                return "MR"
            if cmd.startswith("DCV="):
                if self.na_when_off and not self.bias_on:
                    return "NA"
                self.v = float(cmd[4:])
            else:
                self.bias_on = cmd[4:] == "1"
            self.bias_history.append((self.v, self.bias_on))
            return "OK"
        if cmd.startswith("GFR="):
            self.freq = float(cmd[4:])
            return "OK"
        if cmd.startswith("ACV="):
            self.acv = float(cmd[4:])
            return "OK"
        if cmd == "MBK":
            self.task_running = False
            return "OK"
        if cmd == "ZRE?":
            idx = self.measurements
            self.measurements += 1
            if self.on_measure is not None:
                self.on_measure(idx)
            if idx in self.fail_at:
                raise IOError("VI_ERROR_TMO: timeout")
            f_act = self.freq * (1.0 + self.f_offset)
            x = -1.0 / (2 * math.pi * f_act * self.c)
            status = self.status_at.get(idx, 2)
            return f"ZRE={self.r:.9E} {x:.9E} {f_act:.9E} {status} 1"
        if cmd in self.answers:
            return self.answers[cmd]
        return "OK"


def install_pyvisa(mod, fake):
    """Point the module's pyvisa at a stand-in that hands out `fake`.
    Returns the list the ResourceManagers created are appended to."""
    created = []

    def resource_manager():
        rm = FakeRM(fake)
        created.append(rm)
        return rm
    mod.pyvisa = types.SimpleNamespace(ResourceManager=resource_manager)
    return created


def make_link(mod, fake):
    link = mod.AlphaANLink("GPIB0::10::INSTR", sleep=lambda s: None)
    link.rms = install_pyvisa(mod, fake)
    link.fake = fake
    return link


def base_params(folder, **extra):
    p = dict(sample_name="Sample_A", ac_bias=1.0, v_start=-1.0, v_stop=1.0, v_step=0.5,
             n_cycles=1, cycle_mode=True, shape=gui.SHAPE_POS_FIRST, freq_list="1000",
             delay=0.0, mtm=0.5, wire_mode="2", lcr_visa="GPIB0::10::INSTR", folder=folder)
    p.update(extra)
    return p


def run_worker(worker, timeout=60):
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "worker did not finish"
    msgs = []
    while True:
        try:
            msgs.append(worker.q.get_nowait())
        except queue.Empty:
            break
    return msgs


def run_cv(fake, folder, mod=gui, stop_at_measure=None, worker_sleep=None, params=None, **extra):
    """Validate params, run the worker against the fake; returns
    (worker, messages, data file paths in order, log path, link)."""
    p = params if params is not None else mod.validate_params(base_params(folder, **extra))
    stop = threading.Event()
    link = make_link(mod, fake)
    if stop_at_measure is not None:
        fake.on_measure = lambda idx: stop.set() if idx >= stop_at_measure else None
    worker = mod.CVWorker(p, link, stop, queue.Queue(), sleep=worker_sleep or (lambda s: None))
    msgs = run_worker(worker)
    return worker, msgs, list(worker.files), worker.log_path, link


def read_data_file(path):
    """(header lines without '#', column names, rows as lists of str, trailing comment lines)."""
    header, columns, rows, trailer = [], None, [], []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.rstrip("\n")
            if not ln:
                continue
            if ln.startswith("#"):
                (trailer if columns is not None else header).append(ln[1:].strip())
            elif columns is None:
                columns = ln.split("\t")
            else:
                rows.append(ln.split("\t"))
    return header, columns, rows, trailer


def column(rows, columns, name, cast=float):
    i = columns.index(name)
    return [cast(r[i]) for r in rows]


def log_lines(log_path):
    with open(log_path, encoding="utf-8") as fh:
        out = []
        for ln in fh:
            iso, t_s, text = ln.rstrip("\n").split(" | ", 2)
            assert "T" in iso and t_s.startswith("t_s="), ln
            out.append(text)
        return out


def plotter_reads(path):
    """Parse `path` exactly as PlotterUtil_GUI does; returns (headers, array)."""
    plotter = _load("plotter_under_test_alphaan_cv", PLOTTER_PATH)
    cls = None
    for obj in vars(plotter).values():
        if isinstance(obj, type) and hasattr(obj, "_read_data_from_file"):
            cls = obj
            break
    assert cls is not None, "PlotterUtil_GUI has no class with _read_data_from_file"
    inst = object.__new__(cls)
    headers, _delim, data = inst._read_data_from_file(path)
    return headers, data


def bias_commands(fake):
    """Every DCV/DCE command the fake received, in order."""
    return [c for c in fake.commands if c.startswith(("DCV=", "DCE="))]


def last_index(seq, item):
    return len(seq) - 1 - seq[::-1].index(item)


def run_module_tests(namespace):
    """Plain-python runner: python tests/test_cv_alphaan.py"""
    failures = 0
    for name, fn in sorted(namespace.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("FAIL ", name, "->", type(exc).__name__, exc)
    sys.exit(1 if failures else 0)
