"""Checks on the GPIB scanner's Address Book table (data only, no windows)."""

import glob
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pica.utils.GPIB_Instrument_Scanner_GUI import (  # noqa: E402
    ADDRESS_BOOK, FREE_ADDRESSES_SAFEST_FIRST)

PICA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pica"))
STATUSES = {"In use", "Reserved", "Free", "Avoid", "Never"}


def _by_status(status):
    return {row[0] for row in ADDRESS_BOOK if row[1] == status}


def test_every_primary_address_listed_once_in_order():
    assert [row[0] for row in ADDRESS_BOOK] == list(range(31))


def test_rows_are_well_formed():
    for row in ADDRESS_BOOK:
        assert len(row) == 6, row
        assert row[1] in STATUSES, row
        if row[1] in ("In use", "Reserved"):
            assert row[2], f"address {row[0]} has no instrument"
        if row[1] in ("Free", "Avoid", "Never"):
            assert not row[2], f"address {row[0]} names an instrument"


def test_free_list_matches_the_table():
    assert sorted(FREE_ADDRESSES_SAFEST_FIRST) == sorted(_by_status("Free"))
    assert len(set(FREE_ADDRESSES_SAFEST_FIRST)) == len(FREE_ADDRESSES_SAFEST_FIRST)


def test_controller_addresses_are_never_assigned():
    assert {0, 21, 30} <= _by_status("Never")


def test_lab_bus_as_seen_21_oct_2026():
    in_use = {row[0]: row[2] for row in ADDRESS_BOOK if row[1] == "In use"}
    assert in_use == {
        8: "SRS SR830 lock-in", 11: "Tektronix AFG3022B", 12: "Lakeshore 350",
        13: "Keithley 6221", 17: "Keysight E4980A", 19: "Lakeshore 340",
        20: "Keithley 197A DMM", 23: "Cryocon 34", 24: "Keithley 2400",
        27: "Keithley 6517B"}


def test_module_address_hints_agree_with_the_book():
    in_use = {row[0]: row[2] for row in ADDRESS_BOOK if row[1] == "In use"}
    hint = re.compile(r'^(\w*ADDRESS_HINT)\s*=\s*"[^"]*::(\d+)', re.M)
    seen = 0
    for path in glob.glob(os.path.join(PICA_DIR, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for name, addr in hint.findall(fh.read()):
                seen += 1
                assert int(addr) in in_use, f"{path}: {name} -> {addr} not in use"
    assert seen > 0


def test_scanner_and_instrument_status_both_open_the_book():
    with open(os.path.join(PICA_DIR, "utils", "GPIB_Instrument_Scanner_GUI.py"),
              encoding="utf-8") as fh:
        scanner = fh.read()
    with open(os.path.join(PICA_DIR, "main_v2.py"), encoding="utf-8") as fh:
        launcher = fh.read()
    assert 'text="Address Book"' in scanner
    assert "show_address_book(self.root)" in scanner
    assert "Address Book\", style='Aux.TButton'" in launcher
    assert "command=self._open_address_book" in launcher
    assert "import show_address_book" in launcher


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
