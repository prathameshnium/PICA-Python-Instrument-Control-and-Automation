"""
Purpose: Verify the pure list logic of pica/utils/List_Maker_GUI.py
(no Tk, no hardware):

  - base_list: linear / log / 1/x spacing, by point count and by step,
    inclusive endpoints, refusals for bad input and oversize lists.
  - round_integers: half-away-from-zero rounding, consecutive repeats
    dropped, non-consecutive repeats kept.
  - apply_pattern: loop, sawtooth, square, sine, hysteresis 4 / 5
    quadrants, turning-point repeat on and off, cycle counts.
  - format_values / parse_list round trip.
  - analyze_list: recognises what base_list produced, even after rounding.

Runnable as plain python too:
    python tests/test_list_maker.py
"""

import math
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.utils import List_Maker_GUI as m  # noqa: E402


def _close(a, b, tol=1e-9):
    return len(a) == len(b) and all(abs(x - y) <= tol * max(1.0, abs(y)) for x, y in zip(a, b))


# ------------------------------------------------------------------
# base_list
# ------------------------------------------------------------------
def test_linear_by_points_includes_both_ends():
    assert _close(m.base_list(10, 300, 'linear', n=5), [10, 82.5, 155, 227.5, 300])


def test_linear_by_step_inclusive_and_descending():
    assert _close(m.base_list(10, 50, 'linear', step=10), [10, 20, 30, 40, 50])
    assert _close(m.base_list(50, 10, 'linear', step=10), [50, 40, 30, 20, 10])
    # step that does not divide the span: last point stays inside the range
    out = m.base_list(0, 10, 'linear', step=3)
    assert _close(out, [0, 3, 6, 9])


def test_log_points_per_decade_and_by_points():
    assert _close(m.base_list(1, 1000, 'log', step=1), [1, 10, 100, 1000])
    assert _close(m.base_list(1, 100, 'log', step=2), [1, 10 ** 0.5, 10, 10 ** 1.5, 100])
    assert _close(m.base_list(1000, 1, 'log', step=1), [1000, 100, 10, 1])
    out = m.base_list(20, 2e6, 'log', n=6)
    assert out[0] == 20 and abs(out[-1] - 2e6) < 1e-6 and len(out) == 6


def test_recip_is_even_in_one_over_x():
    out = m.base_list(10, 100, 'recip', n=4)
    recips = [1 / v for v in out]
    diffs = [b - a for a, b in zip(recips, recips[1:])]
    assert all(abs(d - diffs[0]) < 1e-12 for d in diffs)
    assert out[0] == 10 and abs(out[-1] - 100) < 1e-9
    # by step in 1/x
    out = m.base_list(10, 100, 'recip', step=0.03)
    assert _close(out, [10, 1 / 0.07, 1 / 0.04, 1 / 0.01])


def test_single_point_and_equal_ends():
    assert m.base_list(5, 300, 'linear', n=1) == [5.0]
    assert m.base_list(7, 7, 'log', n=3) == [7.0, 7.0, 7.0]


def test_base_list_refusals():
    import pytest
    with pytest.raises(m.ListError):
        m.base_list(0, 10, 'log', n=5)             # zero in log
    with pytest.raises(m.ListError):
        m.base_list(-1, 10, 'recip', n=5)          # sign change
    with pytest.raises(m.ListError):
        m.base_list(0, 10, 'linear', step=-1)
    with pytest.raises(m.ListError):
        m.base_list(0, 10, 'linear', n=3, step=1)  # both given
    with pytest.raises(m.ListError):
        m.base_list(0, 10, 'linear')               # neither given
    with pytest.raises(m.ListError):
        m.base_list(0, 1000, 'linear', step=1e-6)  # over MAX_POINTS


# ------------------------------------------------------------------
# rounding
# ------------------------------------------------------------------
def test_round_integers_half_away_and_dedupe():
    assert m.round_integers([1.2, 1.4, 2.6, 2.5, 3.5]) == [1.0, 3.0, 4.0]
    assert m.round_integers([-2.5, -0.4, 0.4]) == [-3.0, 0.0]
    # non-consecutive repeats are kept (a loop comes back to the same value)
    assert m.round_integers([1, 2, 1]) == [1.0, 2.0, 1.0]
    assert m.round_integers([]) == []


# ------------------------------------------------------------------
# patterns
# ------------------------------------------------------------------
BASE = [0.0, 5.0, 10.0]


def test_pattern_none_and_loop():
    assert m.apply_pattern(BASE, 'none', 7) == BASE
    assert m.apply_pattern(BASE, 'loop', 1) == [0, 5, 10, 10, 5, 0]
    assert m.apply_pattern(BASE, 'loop', 1, repeat_turning_points=False) == [0, 5, 10, 5, 0]
    assert m.apply_pattern(BASE, 'loop', 2, repeat_turning_points=False) == [0, 5, 10, 5, 0, 5, 10, 5, 0]


def test_pattern_sawtooth_square():
    assert m.apply_pattern(BASE, 'sawtooth', 2) == [0, 5, 10, 0, 5, 10]
    assert m.apply_pattern(BASE, 'square', 2, hold=1) == [0, 10, 0, 10]
    assert m.apply_pattern(BASE, 'square', 1, hold=3) == [0, 0, 0, 10, 10, 10]
    # default hold: one cycle spends the base count, half low then half high
    assert m.apply_pattern(BASE, 'square', 1) == [0, 10, 10]
    four = [0.0, 1.0, 2.0, 3.0]
    assert m.apply_pattern(four, 'square', 2) == [0, 0, 3, 3, 0, 0, 3, 3]
    assert m.apply_pattern([0.0, 10.0], 'square', 3) == [0, 10, 0, 10, 0, 10]


def test_symlog_dense_near_zero():
    f = lambda L: [round(v, 6) for v in L]
    # crossing zero: log in, exact 0, log out; odd count puts 0 in the middle
    out = m.base_list(-5, 5, 'symlog', n=9, near_zero=0.05)
    assert out[4] == 0.0 and out[0] == -5 and abs(out[-1] - 5) < 1e-9
    assert f(out[:4]) == f([-5, -5 / 10 ** (2 / 3), -5 / 10 ** (4 / 3), -0.05])
    assert f(out[5:]) == f([0.05, 0.05 * 10 ** (2 / 3), 0.05 * 10 ** (4 / 3), 5])
    # the even case gives the spare point to the bigger side
    out = m.base_list(-1, 100, 'symlog', n=8, near_zero=0.1)
    assert out.count(0.0) == 1 and len([v for v in out if v > 0]) == 4
    # touching zero, either way round
    assert f(m.base_list(0, 5, 'symlog', n=4, near_zero=0.05)) == f([0, 0.05, 0.5, 5])
    assert f(m.base_list(5, 0, 'symlog', n=4, near_zero=0.05)) == f([5, 0.5, 0.05, 0])
    # by points per decade, each side gets that density
    out = m.base_list(-1, 1000, 'symlog', step=1, near_zero=0.1)
    assert f(out) == f([-1, -0.1, 0, 0.1, 1, 10, 100, 1000])
    # one-sided range away from zero is just a log list
    assert f(m.base_list(1, 100, 'symlog', n=3)) == f([1, 10, 100])
    # hysteresis on it: dense around zero, one turn down then up
    hy = m.apply_pattern(m.base_list(-5, 5, 'symlog', n=41, near_zero=0.01), 'hyst4', 1, False)
    info = m.analyze_list(hy)
    assert info['turning_points'] == 1 and hy[0] == 5 and hy[-1] == 5
    import pytest
    with pytest.raises(m.ListError):
        m.base_list(-5, 5, 'symlog', n=9, near_zero=10)     # bigger than the ends
    with pytest.raises(m.ListError):
        m.base_list(-5, 5, 'symlog', n=9, near_zero=0)
    with pytest.raises(m.ListError):
        m.base_list(-5, 5, 'symlog', n=2, near_zero=0.1)


def test_every_pattern_at_gui_defaults():
    """The window opens with 10..300, 30 points, linear, 2 cycles. Every
    pattern must give a sensible, non-trivial list from those defaults."""
    base = m.base_list(10, 300, 'linear', n=30)
    # sawtooth and square: up, (jump/step) down, up again = 2 direction changes
    expect_turns = {'none': 0, 'loop': 3, 'sawtooth': 2, 'square': 2, 'sine': 3, 'hyst4': 3, 'hyst5': 4}
    for key, turns in expect_turns.items():
        out = m.apply_pattern(base, key, 2)
        info = m.analyze_list(out)
        assert info['turning_points'] == turns, (key, info)
        assert info['max'] == 300 and len(out) >= 30, key
    assert len(m.apply_pattern(base, 'sawtooth', 2)) == 60
    assert len(m.apply_pattern(base, 'square', 2)) == 60
    assert len(m.apply_pattern(base, 'sine', 2)) == 2 * 2 * 29 + 1
    assert len(m.apply_pattern(base, 'loop', 2)) == 120


def test_pattern_sine_starts_at_start_peaks_at_stop():
    # 4-point base: each half-swing has 4 points, so one cycle is 7 points
    base = m.base_list(0, 10, 'linear', n=4)
    out = m.apply_pattern(base, 'sine', 1)
    assert _close(out, [0, 2.5, 7.5, 10, 7.5, 2.5, 0], tol=1e-9)
    out2 = m.apply_pattern(base, 'sine', 2)
    assert len(out2) == 13 and abs(out2[6]) < 1e-9 and abs(out2[9] - 10) < 1e-9
    # odd point counts still hit the exact peak
    odd = m.apply_pattern(m.base_list(-5, 5, 'linear', n=61), 'sine', 1)
    assert max(odd) == 5.0 and min(odd) == -5.0


def test_pattern_hysteresis():
    # 4 quadrants: start at +max, through zero to -max, back to +max
    assert m.apply_pattern(BASE, 'hyst4', 1, repeat_turning_points=False) == \
        [10, 5, 0, -5, -10, -5, 0, 5, 10]
    # with turning-point repeats: only the real reversals (+max, -max) repeat;
    # the zero crossing is a seam, not a turn, so no "0, 0" there
    assert m.apply_pattern(BASE, 'hyst4', 1) == \
        [10, 5, 0, -5, -10, -10, -5, 0, 5, 10]
    assert m.apply_pattern(BASE, 'hyst4', 2) == \
        [10, 5, 0, -5, -10, -10, -5, 0, 5, 10, 10, 5, 0, -5, -10, -10, -5, 0, 5, 10]
    # a one-sided range not starting at zero jumps across zero, but still no repeat at the seam
    assert m.apply_pattern([1.0, 5.0], 'hyst4', 1) == [5, 1, -1, -5, -5, -1, 1, 5]
    # 5 quadrants: virgin curve first, then the closed loop
    assert m.apply_pattern(BASE, 'hyst5', 1, repeat_turning_points=False) == \
        [0, 5, 10, 5, 0, -5, -10, -5, 0, 5, 10]
    # no "-0.0" anywhere
    assert all(math.copysign(1, v) > 0 for v in m.apply_pattern(BASE, 'hyst4', 1) if v == 0)
    # two cycles: virgin once, loop twice
    assert len(m.apply_pattern(BASE, 'hyst5', 2, repeat_turning_points=False)) == 3 + 2 * 8


def test_pattern_hysteresis_range_spanning_zero():
    # -5 to 5 is the full swing: loop = +5 down to -5 and back up, no sawtooth
    span = [-5.0, -2.5, 0.0, 2.5, 5.0]
    assert m.apply_pattern(span, 'hyst4', 1, repeat_turning_points=False) == \
        [5, 2.5, 0, -2.5, -5, -2.5, 0, 2.5, 5]
    assert m.apply_pattern(span, 'hyst5', 1, repeat_turning_points=False) == \
        [0, 2.5, 5, 2.5, 0, -2.5, -5, -2.5, 0, 2.5, 5]
    # entered the other way round (5 to -5) gives the same loop
    assert m.apply_pattern(span[::-1], 'hyst4', 1, repeat_turning_points=False) == \
        [5, 2.5, 0, -2.5, -5, -2.5, 0, 2.5, 5]
    # a dense list with no exact zero: virgin curve still starts at 0, then only positives
    dense = m.base_list(-5, 5, 'linear', n=150)
    out = m.apply_pattern(dense, 'hyst5', 1)
    assert out[0] == 0.0 and all(v > 0 for v in out[1:75]) and abs(out[75] - 5) < 1e-9
    # the 4-quadrant loop is monotonic down then monotonic up: exactly one turn
    info = m.analyze_list(m.apply_pattern(dense, 'hyst4', 1, repeat_turning_points=False))
    assert info['turning_points'] == 1 and info['direction'].startswith('falling first')


def test_pattern_refusals():
    import pytest
    with pytest.raises(m.ListError):
        m.apply_pattern(BASE, 'loop', 0)
    with pytest.raises(m.ListError):
        m.apply_pattern(BASE, 'square', 1, hold=0)
    with pytest.raises(m.ListError):
        m.apply_pattern(BASE, 'wiggle', 1)
    assert m.apply_pattern([], 'loop', 3) == []


def test_rounding_before_pattern_keeps_turning_point_repeat():
    base = m.round_integers(m.base_list(1, 10, 'log', n=30))
    out = m.apply_pattern(base, 'loop', 1)
    # the deliberate repeat at the top survives
    assert out[len(base) - 1] == out[len(base)] == 10.0


# ------------------------------------------------------------------
# formatting / parsing / analysis
# ------------------------------------------------------------------
def test_format_and_parse_round_trip():
    vals = m.base_list(10, 300, 'linear', n=30)
    text = m.format_values(vals, decimals=3, separator=',')
    assert _close(m.parse_list(text), vals, tol=1e-3)
    assert m.format_values([1, 2.5, -0.001], 2, False, ', ') == '1.00, 2.50, 0.00'
    assert m.format_values([1234.5], 1, True, ',') == '1.2e+03'
    assert m.format_values([2.5, 3.49], integers=True) == '3,3'
    assert m.parse_list("1e3; -2.5\n 7 abc 0.5") == [1000.0, -2.5, 7.0, 0.5]
    assert m.parse_list("") == []


def test_analyze_recognises_each_spacing():
    lin = m.analyze_list(m.parse_list("10, 20, 30, 40"))
    assert lin['spacing'] == 'linear' and lin['step'] == 10 and lin['direction'] == 'rising'
    assert lin['integers'] is True

    log = m.analyze_list([round(v, 2) for v in m.base_list(1, 1000, 'log', n=13)])
    assert log['spacing'] == 'log' and abs(log['step'] - 4.0) < 0.05

    rec = m.analyze_list([round(v, 2) for v in m.base_list(10, 100, 'recip', n=10)])
    assert rec['spacing'] == '1/x' and abs(abs(rec['step']) - 0.01) < 1e-3

    loop = m.analyze_list([0, 5, 10, 10, 5, 0])
    assert loop['turning_points'] == 1 and loop['repeats'] == 1
    assert loop['spacing'] == 'linear' and loop['step'] == 5
    assert loop['direction'].startswith('rising first')

    assert m.analyze_list([3, 3, 3])['spacing'] == 'constant'
    assert m.analyze_list([1, 2, 7, 8])['spacing'] == 'irregular'
    assert m.analyze_list([])['count'] == 0 and m.analyze_list([4])['spacing'] == 'n/a'


if __name__ == '__main__':
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, '-v']))
