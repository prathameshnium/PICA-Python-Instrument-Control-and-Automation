"""
Module: every GUI window opens for real.

The rest of the suite instantiates the GUI modules with tkinter mocked
(conftest.mock_tkinter), so no window is ever drawn. This file closes that gap:
it runs every program the launcher can start EXACTLY as the launcher does
(runpy.run_path(script, run_name="__main__")), with a REAL tkinter, then lets
the window build, withdraws it, pumps the event loop briefly and destroys it.

What is covered (discovered from disk, nothing hand-maintained):
  * every pica/**/*_GUI.py
  * every other .py the launchers register via resource_path("...py")
    (e.g. utils/PE_plotter.py, the *_Step_GUI_advanced.py editions)
  * the two launchers themselves, pica/main.py and pica/main_v2.py

Why one subprocess per program:
  * Tk state is clean per program and one bad module cannot poison the rest.
  * Several Tk roots created/destroyed in one process is unreliable on Windows
    (the second root can fail with "can't find tk.tcl"), and the mocked tests
    in this suite replace sys.modules['tkinter'] / '_tkinter' while they run.
    That is why other Tk-root tests in this repo skip under a full pytest run.
    Here the pytest process never imports tkinter at all; every Tk root lives
    in its own fresh interpreter, so this file does not fall into that skip.
  * Programs start non-daemon threads and tk.after polling chains; the child
    ends with os._exit() after reporting, so nothing can keep it alive.
  The children run concurrently (bounded pool) to keep the wall time short.

Safety inside each child (see _HARNESS):
  * pyvisa.ResourceManager is replaced by a fake: list_resources() is empty and
    open_resource() raises VisaIOError(VI_ERROR_RSRC_NFOUND), the same thing a
    bus with nothing on it does. No instrument can be addressed.
  * Loading any GPIB DLL/shared library (gpib-32.dll, ni4882.dll, libgpib)
    through ctypes is refused, so the 32-bit editions cannot reach the bus.
  * TCP connect and serial.Serial are refused.
  * subprocess.Popen/run/call/check_call/check_output, os.system, os.startfile,
    webbrowser.open and multiprocessing.Process.start are inert (nothing is
    launched; 32-bit probes see "no interpreter"; the launcher's auto-start of
    the GPIB scanner is recorded, not run).
  * messagebox / filedialog / simpledialog / commondialog return defaults and
    are recorded; nothing modal can open.
  * Misc.mainloop is replaced: withdraw every root, pump update() for a short
    while, record what was built, destroy. wait_window/wait_variable are
    no-ops so a startup dialog cannot block.
  * HOME/USERPROFILE and the working directory point at a temp dir, so any
    settings file a program writes at startup lands there, not in the repo.

Pass criteria per program: the script ran to completion without raising,
mainloop() was reached on a real Tk root, the window holds real widgets, and
no Tk callback raised while the event loop was pumped.
"""
import concurrent.futures
import json
import os
import pathlib
import re
import signal
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PICA_ROOT = REPO_ROOT / "pica"

# Seconds a single program may take from interpreter start to destroy. The
# heavy ones import pandas + matplotlib + pymeasure; 120 s leaves room for a
# loaded CI runner while still catching a genuine hang.
CHILD_TIMEOUT_S = 120
# How long the event loop is pumped once mainloop() is reached.
PUMP_SECONDS = 0.6

# Programs that cannot be opened by this test for a structural reason, with a
# one-line reason each. test_exclusions_are_still_needed fails when one of
# these starts opening cleanly, so the list cannot rot.
EXCLUSIONS = {
    # (none at present)
}

# Programs whose __main__ path deliberately does not open its window on a
# machine without a particular driver. For these the harness imports the
# script (run_name != "__main__") and builds the named window class on a real
# Tk root instead. test_construct_directly_is_still_needed fails once the
# __main__ path opens the window by itself.
CONSTRUCT_DIRECTLY = {
    "pica/novocontrol/Frequency_Scan_AlphaAN_32bit_GUI.py": (
        "AlphaAN_FreqScan_GUI",
        "main() shows 'No GPIB driver' and returns unless gpib-32.dll loads; "
        "the harness refuses GPIB DLLs, so the window class is built directly",
    ),
}

_RESULT_MARKER = "PICA_SMOKE_RESULT "


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
def _rel(path):
    return path.relative_to(REPO_ROOT).as_posix()


def _launcher_registered_scripts():
    """Every .py the launchers point at via resource_path("...py")."""
    found = set()
    for launcher in ("main.py", "main_v2.py"):
        text = (PICA_ROOT / launcher).read_text(encoding="utf-8")
        for rel in re.findall(r"""resource_path\(\s*["']([^"']+\.py)["']\s*\)""", text):
            path = PICA_ROOT / rel
            if path.is_file():
                found.add(path)
    return found


def discover_programs():
    scripts = set(PICA_ROOT.rglob("*_GUI.py"))
    scripts |= _launcher_registered_scripts()
    scripts |= {PICA_ROOT / "main.py", PICA_ROOT / "main_v2.py"}
    return sorted(_rel(p) for p in scripts)


ALL_PROGRAMS = discover_programs()
TO_RUN = [p for p in ALL_PROGRAMS if p not in EXCLUSIONS]


# --------------------------------------------------------------------------
# The child harness (runs in a fresh interpreter per program)
# --------------------------------------------------------------------------
_HARNESS = r'''
import json, os, sys, time, traceback, threading, runpy
script, repo, pump_s, marker, gui_class = (sys.argv[1], sys.argv[2], float(sys.argv[3]),
                                           sys.argv[4], sys.argv[5])
T0 = time.monotonic()
sys.path.insert(0, repo)
sys.argv = [script]
R = {"script": script, "mainloop": 0, "roots": 0, "widgets": 0, "titles": [],
     "dialogs": [], "callback_errors": [], "thread_errors": [], "visa_opens": [],
     "launched": [], "error": None, "mode": "class " + gui_class if gui_class else "__main__"}

def report_and_exit(code):
    R["elapsed"] = round(time.monotonic() - T0, 2)
    sys.stdout.flush(); sys.stderr.flush()
    sys.__stdout__.write("\n" + marker + json.dumps(R) + "\n")
    sys.__stdout__.flush()
    os._exit(code)

# ---- instruments: no GPIB DLL, fake VISA, refuse TCP and serial -----------
# The 32-bit editions drive gpib-32.dll / ni4882.dll through ctypes, and
# linux-gpib is libgpib.so. Refuse any library with "gpib"/"4882" in its name;
# everything else (kernel32 for keep-awake, Tcl/Tk) loads normally.
import ctypes
_orig_cdll_init = ctypes.CDLL.__init__
def _guarded_cdll_init(self, name, *a, **k):
    low = os.path.basename(str(name or "")).lower()
    if "gpib" in low or "4882" in low:
        raise OSError("GPIB driver %r refused by smoke test" % (name,))
    _orig_cdll_init(self, name, *a, **k)
ctypes.CDLL.__init__ = _guarded_cdll_init

from unittest import mock
try:
    import pyvisa, pyvisa.highlevel, pyvisa.errors, pyvisa.constants
    def _nf(name):
        return pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_resource_not_found)
except Exception:
    pyvisa = None
    def _nf(name):
        return OSError("no VISA resource %r (smoke test)" % (name,))

class FakeResourceManager:
    def __init__(self, *a, **k):
        self.visalib = mock.MagicMock(name="visalib")
        self.visalib.library_path = "fake-visa (smoke test)"
    def list_resources(self, *a, **k): return ()
    def list_resources_info(self, *a, **k): return {}
    def resource_info(self, name, *a, **k): raise _nf(name)
    def open_resource(self, name="", *a, **k):
        R["visa_opens"].append(str(name)); raise _nf(name)
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def last_status(self): return 0
    def __getattr__(self, attr):
        return mock.MagicMock(name="FakeResourceManager." + attr)

if pyvisa is not None:
    pyvisa.ResourceManager = FakeResourceManager
    pyvisa.highlevel.ResourceManager = FakeResourceManager

import socket
def _refuse(self, *a, **k): raise ConnectionRefusedError("network refused by smoke test")
socket.socket.connect = _refuse
socket.socket.connect_ex = lambda self, *a, **k: 111
socket.create_connection = lambda *a, **k: _refuse(None)
try:
    import serial
    class _NoSerial:
        def __init__(self, *a, **k):
            raise serial.SerialException("serial refused by smoke test")
    serial.Serial = _NoSerial
except Exception:
    pass

# ---- nothing gets launched ------------------------------------------------
import subprocess, webbrowser
def _out(k):
    text = k.get("text") or k.get("universal_newlines") or k.get("encoding") or k.get("errors")
    return "" if text else b""
class FakePopen:
    def __init__(self, args=None, *a, **k):
        R["launched"].append(repr(args)[:200])
        self.args, self.pid, self.returncode = args, 0, 1
        self._o = _out(k)
        import io
        mk = io.StringIO if isinstance(self._o, str) else io.BytesIO
        self.stdout, self.stderr, self.stdin = mk(self._o), mk(self._o), None
    def communicate(self, *a, **k): return (self._o, self._o)
    def poll(self): return self.returncode
    def wait(self, *a, **k): return self.returncode
    def kill(self): pass
    def terminate(self): pass
    def send_signal(self, *a): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
def _run(args=None, *a, **k):
    R["launched"].append(repr(args)[:200]); o = _out(k)
    return subprocess.CompletedProcess(args, 1, o, o)
def _call(args=None, *a, **k):
    R["launched"].append(repr(args)[:200]); return 1
def _check_output(args=None, *a, **k):
    R["launched"].append(repr(args)[:200])
    raise subprocess.CalledProcessError(1, args, _out(k), _out(k))
def _check_call(args=None, *a, **k):
    R["launched"].append(repr(args)[:200]); raise subprocess.CalledProcessError(1, args)
subprocess.Popen = FakePopen
subprocess.run = _run
subprocess.call = _call
subprocess.check_call = _check_call
subprocess.check_output = _check_output
subprocess.getoutput = lambda *a, **k: ""
subprocess.getstatusoutput = lambda *a, **k: (1, "")
os.system = lambda *a, **k: (R["launched"].append(repr(a)[:200]) or 1)
if hasattr(os, "startfile"):
    os.startfile = lambda *a, **k: R["launched"].append(repr(a)[:200])
webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = (
    lambda *a, **k: R["launched"].append(repr(a)[:200]) or False)
# multiprocessing spawns via _winapi / _posixsubprocess, not subprocess.Popen:
# the launcher auto-starts the GPIB scanner this way 500 ms after opening, and
# that fresh interpreter would carry none of the guards above.
import multiprocessing.process
class _NoChild:
    returncode = 0; pid = 0; sentinel = None
    def poll(self, *a, **k): return 0
    def wait(self, *a, **k): return 0
    def terminate(self): pass
    def kill(self): pass
    def close(self): pass
def _no_start(self):
    R["launched"].append("multiprocessing.Process(target=%r, args=%r)"
                         % (getattr(self, "_target", None), getattr(self, "_args", ()))[:200])
    self._popen = _NoChild()
multiprocessing.process.BaseProcess.start = _no_start

# ---- Tk: real widgets, no dialogs, no blocking mainloop -------------------
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog, commondialog

def _dlg(kind, value):
    def f(*a, **k):
        R["dialogs"].append([kind, " | ".join(str(x) for x in a)[:300]])
        return value
    return f
for name, val in (("showinfo", "ok"), ("showwarning", "ok"), ("showerror", "ok"),
                  ("askquestion", "no"), ("askokcancel", False), ("askyesno", False),
                  ("askyesnocancel", None), ("askretrycancel", False)):
    setattr(messagebox, name, _dlg("messagebox." + name, val))
for name, val in (("askopenfilename", ""), ("askopenfilenames", ()),
                  ("asksaveasfilename", ""), ("askdirectory", ""),
                  ("askopenfile", None), ("askopenfiles", None),
                  ("asksaveasfile", None)):
    setattr(filedialog, name, _dlg("filedialog." + name, val))
for name in ("askstring", "askinteger", "askfloat"):
    setattr(simpledialog, name, _dlg("simpledialog." + name, None))
commondialog.Dialog.show = _dlg("commondialog", "")
tk.Misc.wait_window = lambda self, window=None: None
tk.Misc.wait_variable = lambda self, name="PY_VAR": None
tk.Misc.waitvar = tk.Misc.wait_variable
tk.Misc.grab_set = lambda self: None
tk.Misc.grab_set_global = lambda self: None

_roots = []
_orig_tk_init = tk.Tk.__init__
def _tk_init(self, *a, **k):
    _orig_tk_init(self, *a, **k)
    _roots.append(self)
    R["roots"] += 1
    self.report_callback_exception = lambda et, ev, tb: R["callback_errors"].append(
        "".join(traceback.format_exception(et, ev, tb))[-1500:])
tk.Tk.__init__ = _tk_init

def _count(w):
    n = 1
    for c in w.winfo_children():
        n += _count(c)
    return n

def _fake_mainloop(self, n=0):
    R["mainloop"] += 1
    root = self._root()
    try:
        root.withdraw()
    except tk.TclError:
        pass
    end = time.monotonic() + pump_s
    while time.monotonic() < end:
        try:
            root.update()
        except tk.TclError:
            break  # the program destroyed its own root; that is fine
        time.sleep(0.02)
    try:
        R["widgets"] = max(R["widgets"], _count(root) - 1)
        R["titles"].append(root.title())
        root.destroy()
    except tk.TclError:
        pass
tk.Misc.mainloop = _fake_mainloop
tk.mainloop = lambda n=0: _fake_mainloop(_roots[-1]) if _roots else None

threading.excepthook = lambda args: R["thread_errors"].append(
    "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))[-1500:])

try:
    if gui_class:
        ns = runpy.run_path(script, run_name="__pica_smoke__")
        _root = tk.Tk()
        ns[gui_class](_root)
        _root.mainloop()
    else:
        runpy.run_path(script, run_name="__main__")
except SystemExit as e:
    if e.code not in (None, 0):
        R["error"] = "SystemExit(%r)" % (e.code,)
except BaseException:
    R["error"] = traceback.format_exc()[-4000:]
# give any thread started at startup a moment to report a crash
time.sleep(0.1)
report_and_exit(0)
'''


# --------------------------------------------------------------------------
# Display detection and the concurrent runner
# --------------------------------------------------------------------------
def _display_problem():
    """None when Tk can open a window here, else a reason string."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "no DISPLAY (run under xvfb-run to exercise real Tk windows)"
    probe = subprocess.run(
        [sys.executable, "-c", "import tkinter; r = tkinter.Tk(); r.withdraw(); r.update(); r.destroy()"],
        capture_output=True, text=True, timeout=60,
    )
    if probe.returncode != 0:
        return "Tk cannot open a window: " + (probe.stderr.strip().splitlines() or ["?"])[-1]
    return None


def _child_env(home):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH")]))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["MPLBACKEND"] = "TkAgg"
    # Isolate settings files a program might write under ~ at startup...
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    # ...but keep matplotlib's existing font cache so no child rebuilds it.
    try:
        import matplotlib
        env.setdefault("MPLCONFIGDIR", matplotlib.get_configdir())
    except Exception:
        pass
    return env


def _run_one(rel, workdir, env, gui_class="", script=None):
    """Run one program in a fresh interpreter under the harness; return its record."""
    script = str(script or (REPO_ROOT / rel))
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", _HARNESS, script, str(REPO_ROOT),
         str(PUMP_SECONDS), _RESULT_MARKER, gui_class],
        cwd=str(workdir), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=CHILD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "(pipes still held open by a grandchild)"
        return {"script": rel, "timeout": True, "stderr": (stderr or "")[-3000:]}
    result = None
    for line in stdout.splitlines():
        if line.startswith(_RESULT_MARKER):
            result = json.loads(line[len(_RESULT_MARKER):])
    if result is None:
        result = {"script": rel, "error": "child produced no result (exit code %s)" % proc.returncode}
    result["returncode"] = proc.returncode
    result["stderr"] = stderr[-3000:]
    result["timeout"] = False
    return result


def _kill_tree(proc):
    """Kill a hung child and anything it started."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


@pytest.fixture(scope="module")
def real_display():
    """Skip (Linux without a display) or fail (Windows) when Tk cannot open a window."""
    reason = _display_problem()
    if reason:
        if sys.platform == "win32":
            pytest.fail("Real Tk windows must work on Windows. " + reason)
        pytest.skip(reason)


@pytest.fixture(scope="module")
def window_results(real_display, tmp_path_factory):
    """Run every program once, concurrently; tests then read their result."""
    base = tmp_path_factory.mktemp("gui_open")
    home = base / "home"
    home.mkdir()
    env = _child_env(home)
    workers = max(2, min(8, (os.cpu_count() or 2)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for rel in ALL_PROGRAMS:
            wd = base / re.sub(r"[^A-Za-z0-9_]+", "_", rel)
            wd.mkdir()
            futures[rel] = pool.submit(_run_one, rel, wd, env, "")
            if rel in CONSTRUCT_DIRECTLY:
                wd2 = base / (wd.name + "__class")
                wd2.mkdir()
                futures[rel + "::class"] = pool.submit(
                    _run_one, rel, wd2, env, CONSTRUCT_DIRECTLY[rel][0])
        return {key: fut.result() for key, fut in futures.items()}


def _problems(res):
    """Human-readable reasons this program did not open, empty when it did."""
    if res.get("timeout"):
        return ["timed out after %d s (a blocking call or a hang at startup)\n%s"
                % (CHILD_TIMEOUT_S, res.get("stderr", ""))]
    out = []
    if res.get("error"):
        out.append("raised while starting:\n" + res["error"])
    if not res.get("mainloop"):
        out.append("never reached mainloop() (startup was aborted or swallowed)")
    if not res.get("roots"):
        out.append("no tk.Tk() root was created")
    if res.get("mainloop") and res.get("widgets", 0) < 1:
        out.append("the window has no widgets")
    for tb in res.get("callback_errors", []):
        out.append("a Tk callback raised while the event loop ran:\n" + tb)
    for d in res.get("dialogs", []):
        if d[0] == "messagebox.showerror":
            out.append("showed an error dialog at startup: " + d[1])
    if out and res.get("stderr"):
        out.append("child stderr (tail):\n" + res["stderr"])
    return out


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
def test_programs_are_discovered():
    assert len(ALL_PROGRAMS) >= 100, ALL_PROGRAMS
    for must in ("pica/main.py", "pica/main_v2.py", "pica/utils/PE_plotter.py"):
        assert must in ALL_PROGRAMS


def test_exclusions_name_real_programs():
    ghosts = sorted((set(EXCLUSIONS) | set(CONSTRUCT_DIRECTLY)) - set(ALL_PROGRAMS))
    assert not ghosts, f"EXCLUSIONS/CONSTRUCT_DIRECTLY name programs that no longer exist: {ghosts}"
    both = sorted(set(EXCLUSIONS) & set(CONSTRUCT_DIRECTLY))
    assert not both, f"listed in both EXCLUSIONS and CONSTRUCT_DIRECTLY: {both}"


@pytest.mark.parametrize("program", TO_RUN)
def test_gui_window_opens_for_real(program, window_results):
    key = program + "::class" if program in CONSTRUCT_DIRECTLY else program
    res = window_results[key]
    problems = _problems(res)
    assert not problems, f"{program} did not open cleanly:\n\n" + "\n\n".join(problems)
    # Opening a window must not address an instrument. (The fake refused it,
    # but on a lab PC the real call would have reached the bus.)
    assert not res.get("visa_opens"), f"{program} tried to open VISA resources: {res['visa_opens']}"


def test_exclusions_are_still_needed(request):
    if not EXCLUSIONS:
        return  # nothing excluded, nothing to check
    results = request.getfixturevalue("window_results")
    stale = [p for p in sorted(EXCLUSIONS) if not _problems(results[p])]
    assert not stale, ("these now open cleanly; remove them from EXCLUSIONS: "
                       + ", ".join(f"{p} (reason was: {EXCLUSIONS[p]})" for p in stale))


@pytest.mark.parametrize("program", sorted(CONSTRUCT_DIRECTLY))
def test_construct_directly_is_still_needed(program, window_results):
    res = window_results[program]  # the plain __main__ run
    if res.get("mainloop") and not res.get("error"):
        pytest.fail(f"{program} now opens through __main__; remove it from "
                    f"CONSTRUCT_DIRECTLY (reason was: {CONSTRUCT_DIRECTLY[program][1]})")
    # It must still fail politely (no traceback), exactly as on a lab PC
    # without the driver.
    assert not res.get("error"), f"{program} __main__ raised: {res['error']}"
    assert not res.get("timeout"), f"{program} __main__ hung"


# --------------------------------------------------------------------------
# The harness itself: prove it really detects a broken window and really
# keeps instruments and child programs out of reach.
# --------------------------------------------------------------------------
_SELF_TEST_SCRIPTS = {
    "good": """
import tkinter as tk
root = tk.Tk()
tk.Label(root, text="hello").pack()
root.mainloop()
""",
    "raises_before_window": """
import tkinter as tk
root = tk.Tk()
raise RuntimeError("boom at startup")
""",
    "callback_raises": """
import tkinter as tk
root = tk.Tk()
tk.Label(root, text="x").pack()
root.after(10, lambda: 1 / 0)
root.mainloop()
""",
    "never_reaches_mainloop": """
import tkinter as tk
from tkinter import messagebox
root = tk.Tk()
messagebox.showerror("Dependency Error", "something is missing")
""",
    "touches_instruments": """
import subprocess, multiprocessing, tkinter as tk
import pyvisa
from tkinter import filedialog
rm = pyvisa.ResourceManager()
assert rm.list_resources() == ()
try:
    rm.open_resource("GPIB0::12::INSTR")
except Exception as exc:
    opened = type(exc).__name__
subprocess.Popen(["this-program-does-not-exist-pica"])
multiprocessing.Process(target=print).start()
assert filedialog.askopenfilename() == ""
root = tk.Tk()
tk.Label(root, text=opened).pack()
root.mainloop()
""",
}


def test_harness_detects_failures_and_guards_instruments(real_display, tmp_path):
    env = _child_env(tmp_path)
    paths = {}
    for name, code in _SELF_TEST_SCRIPTS.items():
        paths[name] = tmp_path / f"{name}.py"
        paths[name].write_text(code, encoding="utf-8")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(paths)) as pool:
        futs = {n: pool.submit(_run_one, n, tmp_path, env, "", p) for n, p in paths.items()}
        res = {n: f.result() for n, f in futs.items()}

    assert not _problems(res["good"]), _problems(res["good"])
    assert res["good"]["widgets"] == 1 and res["good"]["titles"]

    assert "boom at startup" in (res["raises_before_window"].get("error") or "")
    assert _problems(res["raises_before_window"])

    assert any("ZeroDivisionError" in tb for tb in res["callback_raises"]["callback_errors"])
    assert _problems(res["callback_raises"])

    bad = res["never_reaches_mainloop"]
    assert any("never reached mainloop" in p for p in _problems(bad))
    assert any("error dialog" in p for p in _problems(bad))

    guarded = res["touches_instruments"]
    assert not guarded.get("error"), guarded.get("error")
    assert guarded["visa_opens"] == ["GPIB0::12::INSTR"]
    assert any("this-program-does-not-exist-pica" in x for x in guarded["launched"])
    assert any("multiprocessing.Process" in x for x in guarded["launched"])
    assert guarded["dialogs"] and guarded["dialogs"][0][0] == "filedialog.askopenfilename"
