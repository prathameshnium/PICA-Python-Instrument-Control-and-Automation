"""Tests for the Novocontrol Alpha-AN diagnostics window (24 Oct 2026):

    pica/novocontrol/Diagnostics_AlphaAN_GUI.py

It sits in the Diagnostic Tools list, so it must be strictly read-only:
only queries reach the instrument, and every session it opens is closed.
The checks run against a fake pyvisa; no Tk window is created.

No hardware. Runnable as plain Python as well as under pytest:
    python tests/test_alphaan_diagnostics.py
"""

import importlib.util
import os
import subprocess
import sys
import time

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

MODULE_PATH = os.path.join(project_root, "pica", "novocontrol",
                           "Diagnostics_AlphaAN_GUI.py")


def _load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


diag = _load("alphaan_diagnostics_module", MODULE_PATH)


class FakeVisaError(Exception):
    def __init__(self, code):
        super().__init__("fake VISA error")
        self.error_code = code


class FakeInstrument:
    def __init__(self, answers, fail_on=None):
        self.answers = answers
        self.fail_on = fail_on
        self.sent = []
        self.closed = False

    def query(self, cmd):
        self.sent.append(cmd)
        if cmd == self.fail_on:
            raise FakeVisaError(diag.VI_ERROR_TMO)
        return self.answers[cmd]

    def write(self, cmd):                       # must never be called
        self.sent.append(("write", cmd))

    def read_stb(self):                         # serial poll: never called
        self.sent.append(("read_stb",))
        return 0

    def close(self):
        self.closed = True


class FakeRM:
    visalib = "fake-visa"

    def __init__(self, inst, resources=("GPIB0::10::INSTR",)):
        self.inst = inst
        self.resources = resources
        self.closed = False

    def list_resources(self):
        return self.resources

    def open_resource(self, address):
        return self.inst

    def close(self):
        self.closed = True


class FakeVisa:
    __version__ = "0.0-fake"

    def __init__(self, rm):
        self.rm = rm

    def ResourceManager(self):
        return self.rm


ANSWERS = {"*IDN?": "NOVOCONTROL,ALPHA-AN,0,1.0\n", "INTTYP?": "ALPHA\n"}


def _run(inst, resources=("GPIB0::10::INSTR",), windows=()):
    rm = FakeRM(inst, resources)
    lines = []
    ok, headline = diag.run_checks("GPIB0::10::INSTR", lines.append,
                                   visa=FakeVisa(rm),
                                   find_windows=lambda: list(windows))
    return ok, headline, lines, rm


def test_alpha_that_answers_is_reported_in_one_sentence():
    inst = FakeInstrument(ANSWERS)
    ok, headline, lines, rm = _run(inst)
    assert ok
    assert "NOVOCONTROL,ALPHA-AN" in headline
    assert inst.sent == ["*IDN?", "INTTYP?"]
    assert inst.closed and rm.closed


def test_only_queries_reach_the_instrument():
    inst = FakeInstrument(ANSWERS)
    _run(inst)
    assert all(isinstance(c, str) and "?" in c for c in inst.sent)


def test_safe_query_refuses_a_non_query():
    inst = FakeInstrument(ANSWERS)
    for cmd in ("*RST", "MST", "ACV 1"):
        try:
            diag.safe_query(inst, cmd)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{cmd} was not refused")
    assert inst.sent == []


def test_timeout_says_to_close_winalpha_and_still_closes():
    inst = FakeInstrument(ANSWERS, fail_on="*IDN?")
    ok, headline, lines, rm = _run(inst)
    assert not ok
    assert "WinALPHA" in headline
    assert inst.closed and rm.closed


def test_address_missing_from_resource_list_is_noted():
    ok, headline, lines, rm = _run(FakeInstrument(ANSWERS), resources=())
    assert ok
    assert any("not in the resource list" in line for line in lines)


def test_no_visa_library_is_a_finding_not_a_crash():
    class NoVisa:
        __version__ = "0.0-fake"

        def ResourceManager(self):
            raise OSError("no library")

    lines = []
    ok, headline = diag.run_checks("GPIB0::10::INSTR", lines.append,
                                   visa=NoVisa())
    assert not ok and "VISA" in headline


def test_no_serial_poll_after_a_good_answer():
    # D1: read_stb() is a GPIB serial poll, not a "?" query; it clears RQS.
    inst = FakeInstrument(ANSWERS)
    ok, headline, lines, rm = _run(inst)
    assert ok
    assert ("read_stb",) not in inst.sent
    assert inst.sent == ["*IDN?", "INTTYP?"]


def test_other_alpha_window_open_sends_nothing():
    # D3: a PICA scan in another process may be mid ack exchange.
    inst = FakeInstrument(ANSWERS)
    ok, headline, lines, rm = _run(
        inst, windows=["Novocontrol Alpha-AN Frequency Scan - 64-bit (VISA)"])
    assert not ok
    assert "Another Alpha-AN window" in headline
    assert inst.sent == []
    assert any("Frequency Scan" in line for line in lines)


def test_pica_alpha_window_titles_are_recognised():
    for title in ("Novocontrol Alpha-AN Frequency Scan - 64-bit (VISA)",
                  "Novocontrol Alpha-AN Frequency Scan - 32-bit "
                  "(NI-488.2 GPIB)",
                  "Alpha-AN & L350: T-Control + Freq Sweep v1.8 - 64-bit "
                  "(VISA)",
                  "Alpha-AN: Calibration (guided) - 64-bit (VISA)"):
        assert diag.is_pica_alpha_window(title), title
    for title in ("PICA - Advanced Options", "Frequency_Scan_AlphaAN_GUI.py",
                  "Alpha-AN manual.pdf"):
        assert not diag.is_pica_alpha_window(title), title


def test_window_scan_runs_and_skips_this_process():
    others = diag.other_alpha_windows()
    if os.name == "nt":
        assert isinstance(others, list)
    else:
        assert others is None


def test_tasklist_has_no_console_and_survives_odd_names():
    # D4: no console flash under pythonw; OEM bytes must not raise.
    seen = {}

    class Done:
        stdout = '"WinALPHA.exe","1"\n"Pr\ufffdgram.exe","2"\n'

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return Done()

    real_run = subprocess.run
    subprocess.run = fake_run
    try:
        hits = diag.gpib_processes()
    finally:
        subprocess.run = real_run
    if os.name != "nt":
        assert hits is None
        return
    assert hits == ["WinALPHA.exe"]
    assert seen.get("errors") == "replace"
    assert seen.get("encoding") == "oem"
    assert seen.get("creationflags") == subprocess.CREATE_NO_WINDOW
    assert "text" not in seen


class _Widget:
    def __init__(self):
        self.options = {}

    def config(self, **kw):
        self.options.update(kw)


class _Var:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class _Root:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []
        self.destroyed = False

    def after(self, ms, fn):
        self.scheduled.append(fn)
        return f"after#{len(self.scheduled)}"

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def destroy(self):
        self.destroyed = True


class _Worker:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


def _headless_gui():
    gui = object.__new__(diag.AlphaANDiagnosticsGUI)
    gui.root = _Root()
    gui.worker = None
    gui.after_id = "after#drain"
    gui.close_deadline = None
    gui.run_btn, gui.save_btn = _Widget(), _Widget()
    gui.headline, gui.headline_var = _Widget(), _Var()
    return gui


def test_closing_mid_check_waits_for_the_worker():
    # D2: the worker must reach its finally (closes inst / rm) first.
    gui = _headless_gui()
    gui.worker = _Worker()
    gui._on_closing()
    assert not gui.root.destroyed
    assert gui.root.cancelled == ["after#drain"]
    assert gui.run_btn.options["state"] == "disabled"
    assert "Finishing" in gui.headline_var.value
    assert gui.root.scheduled == [gui._on_closing]
    gui._on_closing()                           # still busy: one chain only
    assert not gui.root.destroyed and len(gui.root.scheduled) == 2
    gui.worker.alive = False
    gui._on_closing()
    assert gui.root.destroyed


def test_closing_gives_up_after_the_deadline():
    gui = _headless_gui()
    gui.worker = _Worker()
    gui._on_closing()
    gui.close_deadline = time.monotonic() - 1
    gui._on_closing()
    assert gui.root.destroyed


def test_closing_when_idle_is_immediate():
    gui = _headless_gui()
    gui._on_closing()
    assert gui.root.destroyed and gui.root.scheduled == []


def test_headline_wraps_at_its_own_width():
    # D6: at the 760 px minimum a fixed 920 px wrap clipped the advice.
    gui = _headless_gui()

    class Event:
        width = 732
    gui._fit_headline(Event())
    assert gui.headline.options["wraplength"] == 728
    source = open(MODULE_PATH, encoding="utf-8").read()
    assert "self.headline.bind('<Configure>', self._fit_headline)" in source


def test_both_launchers_point_at_the_window():
    for launcher in ("main.py", "main_v2.py"):
        text = open(os.path.join(project_root, "pica", launcher),
                    encoding="utf-8").read()
        assert 'resource_path("novocontrol/Alpha_VISA_Diagnostic_64bit.py")' \
            not in text, launcher
    main = open(os.path.join(project_root, "pica", "main.py"),
                encoding="utf-8").read()
    assert ('"Alpha-AN Diagnostics": resource_path('
            '"novocontrol/Diagnostics_AlphaAN_GUI.py")') in main


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
