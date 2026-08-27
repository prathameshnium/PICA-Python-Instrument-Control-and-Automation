"""Tests for the 32-bit-safe raw NI-488.2 GPIB bus scanner.

No hardware and no driver DLL are needed: the census logic is exercised
against a stand-in that records every call, which is also how the "gentle"
promise is checked -- the census must never write to an instrument.

Runnable as plain Python (``python tests/test_gpib_scanner_32bit.py``) as
well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(REPO_ROOT, "pica", "utils",
                           "GPIB_Scanner_32bit_CLI.py")
GUI_PATH = os.path.join(REPO_ROOT, "pica", "utils",
                        "GPIB_Scanner_32bit_GUI.py")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = _load_module("gpib_scanner_32bit", MODULE_PATH)


class FakeGpib:
    """Stand-in for RawGpib that logs calls and hosts listeners at LISTENERS."""

    LISTENERS = {5, 24}

    def __init__(self, broken_board=None):
        self.broken_board = broken_board
        self.calls = []

    def listener_at(self, board, pad):
        self.calls.append(("ibln", board, pad))
        if board == self.broken_board:
            return None, scanner.ERR
        return pad in self.LISTENERS, scanner.CMPL

    def error_code(self):
        return 0

    # Any use of these by census() would break the gentle-by-design promise.
    def open_device(self, *a):
        self.calls.append(("ibdev",) + a)
        return 1

    def write(self, handle, text):
        self.calls.append(("ibwrt", text))
        return scanner.CMPL

    def read(self, handle, max_len=65536):
        self.calls.append(("ibrd",))
        return b"FAKE,INSTRUMENT\x00\x10", scanner.CMPL | scanner.END

    def close_device(self, handle):
        self.calls.append(("ibonl",))


def test_census_finds_listeners():
    gpib = FakeGpib()
    assert scanner.census(gpib, 0) == [5, 24]


def test_census_sends_nothing_to_any_instrument():
    gpib = FakeGpib()
    scanner.census(gpib, 0)
    assert {call[0] for call in gpib.calls} == {"ibln"}


def test_census_never_probes_the_controller_or_untalk_address():
    gpib = FakeGpib()
    scanner.census(gpib, 0)
    probed = {call[2] for call in gpib.calls}
    assert 0 not in probed and 31 not in probed
    assert probed == set(range(1, 31))


def test_census_reports_an_unusable_board_without_scanning_it():
    gpib = FakeGpib(broken_board=1)
    assert scanner.census(gpib, 1) == []
    # One probe, then it gives up -- it does not sweep a dead board.
    assert len(gpib.calls) == 1


def test_identify_is_one_read_only_query():
    gpib = FakeGpib()
    assert scanner.identify(gpib, 0, 24, 3) == "FAKE,INSTRUMENT"
    written = [call[1] for call in gpib.calls if call[0] == "ibwrt"]
    assert written == ["*IDN?"]


def test_decode_strips_alpha_response_padding():
    assert scanner.decode(b"  NOVOCONTROL\r\n\x00\x10") == "NOVOCONTROL"


def test_iberr_table_includes_epwr():
    # EPWR was missing from an earlier lookup table and produced a bare code.
    assert "EPWR" in scanner.iberr_text(28)
    assert "unknown" in scanner.iberr_text(999)


def test_sta_text_names_the_error_bit():
    assert "ERR" in scanner.sta_text(scanner.ERR)
    assert "none" in scanner.sta_text(0)


def test_dll_candidates_include_syswow64():
    names = [os.path.basename(p).lower()
             for p in scanner.find_dll_candidates()]
    # Nothing may exist on a non-lab machine; the point is only that the
    # search never returns a duplicate path.
    assert len(names) == len(set(scanner.find_dll_candidates()))
    if os.name == "nt":
        directories = {os.path.basename(os.path.dirname(p)).lower()
                       for p in scanner.find_dll_candidates()}
        assert directories <= {"system32", "syswow64"}


def test_gui_shares_the_same_gentle_backend():
    """The 32-bit GUI is self-contained; its backend must still match the CLI."""
    gui = _load_module("gpib_scanner_32bit_gui", GUI_PATH)
    assert gui.SCAN_ADDRESSES == scanner.SCAN_ADDRESSES
    assert gui.RESPONSE_STRIP == scanner.RESPONSE_STRIP
    assert gui.IBERR_MEANINGS[28] == scanner.IBERR_MEANINGS[28]
    assert gui.decode(b"  NOVOCONTROL\r\n\x00\x10") == "NOVOCONTROL"
    # ibln is what makes the census gentle, so it is a required export.
    with open(GUI_PATH, encoding="utf-8") as handle:
        source = handle.read()
    assert '"ibdev", "ibonl", "ibwrt", "ibrd", "ibln"' in source


def test_gui_is_registered_in_both_launchers():
    key = "GPIB Scanner (32-bit)"
    with open(os.path.join(REPO_ROOT, "pica", "main.py"),
              encoding="utf-8") as handle:
        v1 = handle.read()
    with open(os.path.join(REPO_ROOT, "pica", "main_v2.py"),
              encoding="utf-8") as handle:
        v2 = handle.read()
    # v1 owns SCRIPT_PATHS; v2 imports it, so the path lives in v1 only.
    assert "utils/GPIB_Scanner_32bit_GUI.py" in v1
    assert v1.count(key) >= 2      # SCRIPT_PATHS entry + PICA Utils popup
    assert key in v2               # Tools menu + PICA Utils popup
    assert os.path.isfile(GUI_PATH)


if __name__ == "__main__":
    failures = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            else:
                print(f"ok   {name}")
    sys.exit(1 if failures else 0)
