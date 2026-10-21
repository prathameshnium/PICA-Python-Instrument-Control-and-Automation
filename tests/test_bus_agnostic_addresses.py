"""Modules pick instruments by GPIB primary address on any board.

Every module that pre-selects an instrument by address carries its own copy of
_is_gpib_address (modules stay self-contained). These checks keep the copies
identical, keep bus-pinned substring selectors out, and keep the defaults in
line with the scanner's Address Book.
"""

import ast
import glob
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pica.utils.GPIB_Instrument_Scanner_GUI import ADDRESS_BOOK  # noqa: E402

PICA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pica"))


def _sources():
    for path in glob.glob(os.path.join(PICA_DIR, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8", errors="replace") as fh:
            yield os.path.relpath(path, PICA_DIR), fh.read()


def _helper_source(text):
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_is_gpib_address":
            return ast.get_source_segment(text, node)
    return None


def _helper():
    for rel, text in _sources():
        src = _helper_source(text)
        if src:
            scope = {}
            exec(src, scope)
            return scope["_is_gpib_address"]
    raise AssertionError("no module defines _is_gpib_address")


def test_helper_matches_primary_address_on_any_board():
    match = _helper()
    assert match("GPIB0::19::INSTR", "::19::")
    assert match("GPIB1::19::INSTR", "::19::")
    assert match("GPIB1::23::INSTR", "::23::INSTR")
    assert match("GPIB1::12::INSTR", "GPIB1::12")


def test_helper_rejects_secondary_copies_and_neighbours():
    match = _helper()
    assert not match("GPIB1::12::19::INSTR", "::19::")   # L350 secondary copy
    assert not match("GPIB1::12::24::INSTR", "::24::")
    assert not match("GPIB1::124::INSTR", "::12::")
    assert not match("GPIB1::2::INSTR", "::24::")
    assert not match("ASRL3::INSTR", "::3::")
    assert not match("TCPIP0::10.0.0.5::inst0::INSTR", "::10::")


def test_every_copy_of_the_helper_is_identical():
    copies = {}
    for rel, text in _sources():
        if "_is_gpib_address(" in text:
            src = _helper_source(text)
            assert src, f"{rel} calls _is_gpib_address without defining it"
            copies[rel] = src
    assert len(copies) > 40
    assert len(set(copies.values())) == 1, sorted(copies)


def test_no_bus_pinned_substring_selectors():
    pinned = re.compile(r"""['"]GPIB\d?::\d+['"]\s+in\s+\w+""")
    hint_in = re.compile(r"\b\w*ADDRESS_HINT\s+in\s+\w+")
    for rel, text in _sources():
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            assert not pinned.search(line), f"{rel}:{i}: {line.strip()}"
            assert not hint_in.search(line), f"{rel}:{i}: {line.strip()}"


def test_selector_addresses_are_in_use_in_the_address_book():
    in_use = {row[0] for row in ADDRESS_BOOK if row[1] in ("In use", "Reserved")}
    call = re.compile(r"""_is_gpib_address\(\w+,\s*['"]::(\d+)::['"]\)""")
    for rel, text in _sources():
        for addr in call.findall(text):
            assert int(addr) in in_use, f"{rel}: selects address {addr}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
