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
import sys

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

    def read_stb(self):
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


def _run(inst, resources=("GPIB0::10::INSTR",)):
    rm = FakeRM(inst, resources)
    lines = []
    ok, headline = diag.run_checks("GPIB0::10::INSTR", lines.append,
                                   visa=FakeVisa(rm))
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
