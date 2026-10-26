"""Tests for the two command-line Alpha-AN diagnostic tools:

    pica/novocontrol/Alpha_GPIB_Diagnostic_32bit.py   (NI-488.2, ctypes)
    pica/novocontrol/Alpha_VISA_Diagnostic_64bit.py   (pyvisa)

Both send *RST (and the GPIB one, with --measure, set commands), so they
must only do that to a device that identifies as an Alpha, must report the
*RST recovery honestly, and must validate their options first.

Fake buses and a fake clock only; no hardware, no driver, no log files.
Runnable as plain Python as well as under pytest:
    python tests/test_alpha_diagnostic_tools.py
"""

import ctypes
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import types

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

FOLDER = os.path.join(project_root, "pica", "novocontrol")


def _load(alias, name):
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(FOLDER, name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


gpib = _load("alpha_gpib_diag_tool", "Alpha_GPIB_Diagnostic_32bit.py")
visa = _load("alpha_visa_diag_tool", "Alpha_VISA_Diagnostic_64bit.py")

# Keep every log line in memory (importing must not have opened a file).
assert gpib._log_file is None
gpib.LOG_PATH, gpib._log_file = "memory", io.StringIO()
visa.LOG_PATH = ""                      # console only


def _gpib_log():
    return gpib._log_file.getvalue()


class FakeClock:
    """Stands in for the time module: sleep() just advances the clock."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakeAlpha:
    """GPIB Alpha session. writes / reads are scripted results, used in
    order; once they run out every write succeeds and every read is OK."""

    def __init__(self, writes=(), reads=(), idn="ALPHA Analyzer, Novocontrol"):
        self.pad, self.ud = 10, 1
        self.writes = list(writes)
        self.reads = list(reads)
        self.idn = idn
        self.sent = []

    def write(self, cmd):
        self.sent.append(cmd)
        return self.writes.pop(0) if self.writes else (True, None)

    def read(self):
        return self.reads.pop(0) if self.reads else "OK"

    def query(self, cmd):
        self.sent.append(cmd)
        return {"*IDN?": self.idn, "INTTYP?": "5", "GFR?": "1000"}.get(
            cmd, "OK")

    def poll(self):
        return 0

    def wait_srq(self, code):
        return False

    def close(self):
        pass


def _with_clock(fn):
    saved, gpib.time = gpib.time, FakeClock()
    try:
        return fn()
    finally:
        gpib.time = saved


# ------------------------------------------------------------- GPIB tool

def test_gpib_reset_failed_rst_write_is_not_timed():
    alpha = FakeAlpha(writes=[(False, 2)])
    assert _with_clock(lambda: gpib.reset_test(alpha)) is False
    assert alpha.sent == ["*RST"]
    assert "*RST write failed" in _gpib_log()


def test_gpib_reset_keeps_polling_when_idn_read_fails():
    # *IDN? accepted but the read times out once, then it answers.
    alpha = FakeAlpha(reads=[None, "ALPHA Analyzer"])
    assert _with_clock(lambda: gpib.reset_test(alpha)) is True
    assert alpha.sent.count("*IDN?") == 2
    assert "(2 attempts)" in _gpib_log()


def test_gpib_reset_retries_after_eabo():
    alpha = FakeAlpha(writes=[(True, None), (False, 2), (False, 6),
                              (True, None)])
    assert _with_clock(lambda: gpib.reset_test(alpha)) is True
    assert alpha.sent.count("*IDN?") == 3


def test_gpib_identify_refuses_a_non_alpha():
    alpha = FakeAlpha(idn="Keysight Technologies,E4991B,MY1,A.01")
    assert gpib.identify(alpha) is False
    assert "not an Alpha" in _gpib_log()
    assert gpib.identify(FakeAlpha()) is True


def _args(**kw):
    a = dict(acv=0.1, freq=1000.0, mtm=0.5, wires="2", address=10, board=0,
             reset_test=False, measure=False, probe_unverified=False,
             all=False)
    a.update(kw)
    return types.SimpleNamespace(**a)


def test_gpib_measure_options_are_validated():
    nan = float("nan")
    assert gpib.measure_problem(_args()) is None
    for kw, opt in ((dict(acv=nan), "--acv"), (dict(acv=-1), "--acv"),
                    (dict(acv=0), "--acv"), (dict(acv=3.5), "--acv"),
                    (dict(freq=nan), "--freq"), (dict(freq=5e6), "--freq"),
                    (dict(mtm=nan), "--mtm"), (dict(mtm=0), "--mtm"),
                    (dict(mtm=400), "--mtm"), (dict(freq=0.001), "--freq")):
        problem = gpib.measure_problem(_args(**kw))
        assert problem and opt in problem, (kw, problem)


def test_gpib_invalid_measure_sends_nothing_and_asks_nothing():
    alpha = FakeAlpha()
    gpib.input = lambda prompt: (_ for _ in ()).throw(AssertionError(prompt))
    try:
        assert gpib.measure(alpha, _args(acv=float("nan"))) is False
    finally:
        del gpib.input
    assert alpha.sent == []


def _run_gpib_main(argv, answer, alpha=None):
    alpha = alpha or FakeAlpha()
    saved = {n: getattr(gpib, n) for n in (
        "check_environment", "load_driver", "check_board", "census", "Alpha")}
    gpib.check_environment = lambda: None
    gpib.load_driver = lambda: object()
    gpib.check_board = lambda g, b, p: True
    gpib.census = lambda g, b: [10, 17]
    gpib.Alpha = lambda g, b, p: alpha
    gpib.input = lambda prompt: answer
    old_argv, sys.argv = sys.argv, ["tool"] + argv
    try:
        _with_clock(gpib.main)
    finally:
        sys.argv = old_argv
        for n, v in saved.items():
            setattr(gpib, n, v)
        del gpib.input
    return alpha


def test_gpib_all_declined_still_runs_the_reset_test():
    alpha = _run_gpib_main(["--all"], "no")
    assert alpha.sent.count("*RST") == 1
    assert "MODE=IMP" not in alpha.sent


def test_gpib_all_accepted_runs_the_reset_once():
    alpha = _run_gpib_main(["--all"], "YES")
    assert alpha.sent.count("*RST") == 1
    assert "MODE=IMP" in alpha.sent


def test_gpib_non_alpha_gets_no_reset():
    alpha = FakeAlpha(idn="Keysight Technologies,E4991B")
    _run_gpib_main(["--address", "17", "--all"], "YES", alpha)
    assert "*RST" not in alpha.sent and "MBK" not in alpha.sent
    assert "MODE=IMP" not in alpha.sent


def test_gpib_read_escapes_non_ascii_bytes():
    raw = b"ALPHA\xff\x80\r\n"

    class G:
        def ibrd(self, ud, buf, n):
            ctypes.memmove(buf, raw, len(raw))
            return 0

        def cnt(self):
            return len(raw)

    alpha = object.__new__(gpib.Alpha)
    alpha.g, alpha.ud = G(), 0
    text = alpha.read()
    text.encode("ascii")                # must be pure ASCII
    assert text == "ALPHA\\xff\\x80"


def test_gpib_log_survives_a_cp1252_console():
    buf = io.BytesIO()
    console = io.TextIOWrapper(buf, encoding="cp1252")
    saved, sys.stdout = sys.stdout, console
    try:
        gpib.log("odd reply '�'")
    finally:
        sys.stdout = saved
        console.detach()                # flushes, keeps buf open
    assert b"\\ufffd" in buf.getvalue()
    assert "odd reply '�'" in _gpib_log()


def test_logs_fall_back_when_script_folder_is_unwritable():
    tmp = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        missing = os.path.join(tmp, "no_such_dir")
        # GPIB tool: open_log() tries HERE first; point HERE at a dead folder.
        saved = (gpib.HERE, gpib.LOG_PATH, gpib._log_file)
        gpib.HERE, gpib.LOG_PATH, gpib._log_file = missing, None, None
        try:
            path = gpib.open_log()
            assert path and os.path.dirname(path) == tmp
            gpib._log_file.close()
        finally:
            gpib.HERE, gpib.LOG_PATH, gpib._log_file = saved
        # VISA tool: an unwritable LOG_PATH moves to the working folder.
        visa.LOG_PATH = os.path.join(missing, "AlphaVisaDiag_x.log")
        try:
            visa.log("hello")
            assert visa.LOG_PATH == os.path.join(tmp, "AlphaVisaDiag_x.log")
            with open(visa.LOG_PATH, encoding="utf-8") as fh:
                assert "hello" in fh.read()
        finally:
            visa.LOG_PATH = ""
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------- VISA tool

class FakeInst:
    def __init__(self, idn="ALPHA Analyzer, Novocontrol\n", fail=False):
        self.idn, self.fail, self.sent = idn, fail, []
        self.closed = False

    def query(self, cmd):
        self.sent.append(cmd)
        if self.fail:
            raise OSError("fake timeout")
        return self.idn if cmd == "*IDN?" else "5\n"

    def write(self, cmd):
        self.sent.append(cmd)

    def read_stb(self):
        return 0

    def clear(self):
        pass

    def close(self):
        self.closed = True


class FakeRM:
    def __init__(self, inst):
        self.inst = inst

    def open_resource(self, address):
        return self.inst


def _run_visa_main(argv, inst):
    saved = (visa.check_environment, visa.open_visa)
    visa.check_environment = lambda: None
    visa.open_visa = lambda: FakeRM(inst)
    old_argv, sys.argv = sys.argv, ["tool"] + argv
    clock, saved_time = FakeClock(), visa.time
    visa.time = clock
    try:
        return visa.main()
    finally:
        sys.argv = old_argv
        visa.check_environment, visa.open_visa = saved
        visa.time = saved_time


def test_visa_non_alpha_is_not_reset():
    inst = FakeInst(idn="Keysight Technologies,E4991B,MY1,A.01\n")
    _run_visa_main(["--address", "GPIB0::17::INSTR", "--reset-test"], inst)
    assert "*RST" not in inst.sent and inst.closed


def test_visa_failed_idn_is_not_reset():
    inst = FakeInst(fail=True)
    _run_visa_main(["--reset-test"], inst)
    assert "*RST" not in inst.sent and inst.closed


def test_visa_alpha_is_reset():
    inst = FakeInst()
    assert _run_visa_main(["--reset-test"], inst) == 0
    assert inst.sent.count("*RST") == 1


def test_visa_bad_wait_or_poll_timeout_stops_before_the_bus():
    for argv in (["--wait", "0"], ["--wait", "-5"], ["--wait", "nan"],
                 ["--poll-timeout", "nan"], ["--poll-timeout", "0"],
                 ["--poll-timeout", "inf"]):
        inst = FakeInst()
        assert _run_visa_main(argv + ["--reset-test"], inst) == 1, argv
        assert inst.sent == [], argv


def test_visa_main_is_guarded():
    with open(os.path.join(FOLDER, "Alpha_VISA_Diagnostic_64bit.py"),
              encoding="utf-8") as fh:
        source = fh.read()
    tail = source.split('if __name__ == "__main__":', 1)[1]
    assert "except Exception" in tail and "traceback" in tail


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
