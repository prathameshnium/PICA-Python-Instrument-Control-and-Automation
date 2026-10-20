"""
Purpose: Verify the pure logic of pica/utils/Ohms_Law_Calculator_GUI.py
(no Tk):

  - solve: any two of V, I, R give the third; one or three values, and a
    zero divisor, are refused with a plain-language reason.
  - factor: every unit in every picker converts to the base unit.
  - parse_value / format_number / format_power.

Runnable as plain python too:
    python tests/test_ohms_law_calculator.py
"""

import math
import os
import sys

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.utils import Ohms_Law_Calculator_GUI as m


def _raises(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ValueError as e:
        return str(e)
    raise AssertionError(f"{fn.__name__}{args} did not raise")


def test_any_two_give_the_third():
    assert m.solve(i=2e-3, r=4.7e3) == ('V', 2e-3 * 4.7e3)
    key, value = m.solve(v=9.4, r=4.7e3)
    assert key == 'I' and math.isclose(value, 2e-3)
    key, value = m.solve(v=9.4, i=2e-3)
    assert key == 'R' and math.isclose(value, 4.7e3)


def test_negative_values_keep_their_sign():
    key, value = m.solve(v=-1.0, i=1e-3)
    assert key == 'R' and math.isclose(value, -1e3)


def test_one_or_three_values_are_refused():
    assert "two" in _raises(m.solve, v=1.0)
    assert "empty" in _raises(m.solve, v=1.0, i=1.0, r=1.0)


def test_a_zero_divisor_is_refused():
    assert "unbounded" in _raises(m.solve, v=1.0, r=0.0)
    assert "unbounded" in _raises(m.solve, v=1.0, i=0.0)
    assert m.solve(i=0.0, r=10.0) == ('V', 0.0)


def test_every_unit_converts_and_defaults_exist():
    assert math.isclose(m.factor('V', "mV"), 1e-3)
    assert math.isclose(m.factor('I', "µA"), 1e-6)
    assert math.isclose(m.factor('R', "MΩ"), 1e6)
    for key, units in m.UNITS.items():
        assert m.DEFAULT_UNIT[key] in dict(units)
        assert len({f for _, f in units}) == len(units)


def test_unit_round_trip_through_solve():
    # 5 V across 2.2 kOhm, read in mA.
    _, amps = m.solve(v=5 * m.factor('V', "V"), r=2.2 * m.factor('R', "kΩ"))
    assert math.isclose(amps / m.factor('I', "mA"), 5 / 2.2)


def test_parse_value():
    assert m.parse_value("") is None
    assert m.parse_value("  ") is None
    assert m.parse_value("4.7e3") == 4700.0
    assert m.parse_value("1,000") == 1000.0
    for bad in ("abc", "nan", "inf", "1..2"):
        _raises(m.parse_value, bad)


def test_formatting():
    assert m.format_number(0) == "0"
    assert m.format_number(4.7) == "4.7"
    assert m.format_number(1 / 3) == "0.333333"
    assert m.format_number(1.5e-9) == "1.5000e-09"
    assert m.format_power(0) == "0 W"
    assert m.format_power(4.7e-3) == "4.7 mW"
    assert m.format_power(-2e3) == "-2 kW"
    assert m.format_power(1.5) == "1.5 W"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
    print("\nALL TESTS PASSED")
