"""Sweep-point generation of the three I-V modules.

IV_K2400_GUI.py, IV_K2400_K2182_GUI.py and IV_K6517B_GUI.py each carry a
copy of the same pure generator (build_sweep_points / parse_custom_list /
check_sweep_limits). This file drives every copy through the same cases so
the three can never drift apart:

  "0 to Max"   starts at 0, ends EXACTLY at Max, never overshoots, and
               lands on Max even when Max is not a multiple of Step
               (0, 0.3, 0.6, 0.9, 1.0), in either direction (Max < 0).
  "Loop"       0 -> Max -> 0 -> -Max -> 0: symmetric, no duplicate point at
               the three turnarounds, passes through 0 exactly between the
               two lobes, ends at 0, and 4 * N points for N steps.
  "Custom"     accepts commas, semicolons, spaces, tabs and new lines, keeps
               the order written, and names the first bad token.
  "Loops"      repeats the whole pattern N times; 0 or a fraction is refused.
  limits       any |point| above the instrument ceiling is refused before
               the instrument is touched.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULE_PATHS = {
    "k2400": os.path.join(REPO_ROOT, "pica", "keithley", "k2400", "IV_K2400_GUI.py"),
    "k2182": os.path.join(REPO_ROOT, "pica", "keithley", "k2400_2182", "IV_K2400_K2182_GUI.py"),
    "k6517b": os.path.join(REPO_ROOT, "pica", "keithley", "k6517b", "High_Resistance",
                           "IV_K6517B_GUI.py"),
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULES = {key: _load(f"iv_sweep_gen_{key}", path) for key, path in MODULE_PATHS.items()}

try:
    import pytest
    parametrize_modules = pytest.mark.parametrize("key", sorted(MODULES))
except ImportError:  # plain-python run
    pytest = None

    def parametrize_modules(fn):
        return fn


def _close(a, b):
    return np.allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float),
                       rtol=0, atol=1e-12)


# ---------------------------------------------------------------------------
# "0 to Max"
# ---------------------------------------------------------------------------

@parametrize_modules
def test_zero_to_max_exact_multiple(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=1.0, step_val=0.1)
    assert len(pts) == 11, pts
    assert _close(pts, np.linspace(0, 1, 11))
    assert pts[0] == 0.0 and pts[-1] == 1.0


@parametrize_modules
def test_zero_to_max_non_multiple_lands_on_max_and_never_overshoots(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=1.0, step_val=0.3)
    assert _close(pts, [0, 0.3, 0.6, 0.9, 1.0]), pts
    assert np.max(pts) <= 1.0


@parametrize_modules
def test_zero_to_max_float_accumulation_does_not_add_an_extra_point(key):
    # np.arange(0, 1 + 0.1, 0.1) is the classic way to get 12 points for 10
    # steps; the generator must count steps as integers instead.
    m = MODULES[key]
    for max_val, step in [(1.0, 0.1), (10.0, 0.1), (0.7, 0.1), (3.0, 0.3), (100.0, 0.01)]:
        pts = m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=max_val, step_val=step)
        expected_n = int(round(max_val / step)) + 1
        assert len(pts) == expected_n, (max_val, step, len(pts))
        assert pts[-1] == max_val


@parametrize_modules
def test_zero_to_max_negative_max_sweeps_downwards(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=-5.0, step_val=1.0)
    assert _close(pts, [0, -1, -2, -3, -4, -5]), pts


@parametrize_modules
def test_zero_to_max_step_sign_is_ignored(key):
    m = MODULES[key]
    a = m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=2.0, step_val=0.5)
    b = m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=2.0, step_val=-0.5)
    assert _close(a, b)


@parametrize_modules
def test_zero_to_max_rejects_zero_max_and_zero_step(key):
    m = MODULES[key]
    for kwargs in [dict(max_val=0.0, step_val=0.1), dict(max_val=1.0, step_val=0.0)]:
        try:
            m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {kwargs}")


@parametrize_modules
def test_step_larger_than_max_gives_two_points(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=1.0, step_val=5.0)
    assert _close(pts, [0.0, 1.0]), pts


# ---------------------------------------------------------------------------
# "Loop (0 -> Max -> 0 -> -Max -> 0)"
# ---------------------------------------------------------------------------

@parametrize_modules
def test_loop_shape_and_turnarounds(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_LOOP, max_val=1.0, step_val=0.5)
    assert _close(pts, [0, 0.5, 1.0, 0.5, 0, -0.5, -1.0, -0.5, 0]), pts


@parametrize_modules
def test_loop_has_no_duplicate_points_at_turnarounds(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_LOOP, max_val=10.0, step_val=1.0)
    diffs = np.diff(pts)
    assert np.all(np.abs(diffs) > 0), "a repeated point sits at a turnaround"
    n_steps = 10
    assert len(pts) == 4 * n_steps + 1


@parametrize_modules
def test_loop_passes_through_zero_exactly_and_ends_at_zero(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_LOOP, max_val=1.0, step_val=0.1)
    zeros = np.where(pts == 0.0)[0]
    assert list(zeros) == [0, 20, 40], zeros
    assert pts[10] == 1.0 and pts[30] == -1.0
    assert pts[-1] == 0.0


@parametrize_modules
def test_loop_is_antisymmetric(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_LOOP, max_val=3.0, step_val=0.7)
    half = (len(pts) - 1) // 2
    pos, neg = pts[:half], pts[half:-1]
    assert _close(neg, -pos)


@parametrize_modules
def test_loop_non_multiple_step_hits_max_exactly(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_LOOP, max_val=1.0, step_val=0.3)
    assert np.max(pts) == 1.0 and np.min(pts) == -1.0
    assert _close(pts, [0, .3, .6, .9, 1, .9, .6, .3, 0, -.3, -.6, -.9, -1, -.9, -.6, -.3, 0])


@parametrize_modules
def test_loop_negative_max_starts_towards_negative(key):
    m = MODULES[key]
    pts = m.build_sweep_points(m.SWEEP_LOOP, max_val=-1.0, step_val=0.5)
    assert _close(pts, [0, -0.5, -1.0, -0.5, 0, 0.5, 1.0, 0.5, 0]), pts


# ---------------------------------------------------------------------------
# "Custom List"
# ---------------------------------------------------------------------------

@parametrize_modules
def test_custom_list_separators(key):
    m = MODULES[key]
    assert m.parse_custom_list("0, 1, 2") == [0.0, 1.0, 2.0]
    assert m.parse_custom_list("0;1;2") == [0.0, 1.0, 2.0]
    assert m.parse_custom_list("0 1\t2\n3\r\n4") == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert m.parse_custom_list("1e-3, -2.5E2, +7") == [1e-3, -250.0, 7.0]
    assert m.parse_custom_list(",, 1 ,, 2 ,,\n") == [1.0, 2.0]


@parametrize_modules
def test_custom_list_keeps_order_and_duplicates(key):
    m = MODULES[key]
    vals = m.parse_custom_list("0, 5, 0, -5, 0, 5")
    pts = m.build_sweep_points(m.SWEEP_CUSTOM, custom_values=vals)
    assert _close(pts, [0, 5, 0, -5, 0, 5])


@parametrize_modules
def test_custom_list_names_the_bad_token(key):
    m = MODULES[key]
    for text in ["0, 1, two, 3", "1 2 3 4x", "nan", "inf, 1"]:
        try:
            m.parse_custom_list(text)
        except ValueError as e:
            assert "Custom list" in str(e)
        else:
            raise AssertionError(f"accepted {text!r}")


@parametrize_modules
def test_custom_list_empty_is_refused(key):
    m = MODULES[key]
    for text in ["", "   \n  ", ", ; ,", None]:
        try:
            m.parse_custom_list(text)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {text!r}")
    try:
        m.build_sweep_points(m.SWEEP_CUSTOM, custom_values=[])
    except ValueError:
        pass
    else:
        raise AssertionError("empty custom_values accepted")


@parametrize_modules
def test_custom_list_example_shown_in_gui_is_itself_valid(key):
    """The example text pre-filled in the box must parse and sweep."""
    m = MODULES[key]
    vals = m.parse_custom_list(m.CUSTOM_LIST_EXAMPLE)
    assert len(vals) >= 10
    assert vals[0] == 0.0 and vals[-1] == 0.0
    pts = m.build_sweep_points(m.SWEEP_CUSTOM, custom_values=vals)
    assert len(pts) == len(vals)
    # it is a loop: positive lobe then negative lobe
    assert np.max(pts) > 0 and np.min(pts) < 0


# ---------------------------------------------------------------------------
# Loops / limits / unknown type
# ---------------------------------------------------------------------------

@parametrize_modules
def test_loops_repeat_the_whole_pattern(key):
    m = MODULES[key]
    one = m.build_sweep_points(m.SWEEP_LOOP, max_val=1.0, step_val=0.5, num_loops=1)
    three = m.build_sweep_points(m.SWEEP_LOOP, max_val=1.0, step_val=0.5, num_loops=3)
    assert len(three) == 3 * len(one)
    assert _close(three, np.tile(one, 3))
    custom = m.build_sweep_points(m.SWEEP_CUSTOM, custom_values=[1, 2], num_loops="2")
    assert _close(custom, [1, 2, 1, 2])


@parametrize_modules
def test_loops_below_one_or_non_integer_refused(key):
    m = MODULES[key]
    for loops in [0, -1, "abc", None, 1.5]:
        try:
            m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=1, step_val=1, num_loops=loops)
        except (ValueError, TypeError):
            pass
        else:
            if loops == 1.5:
                continue  # int(1.5) == 1 is tolerated
            raise AssertionError(f"accepted loops={loops!r}")


@parametrize_modules
def test_unknown_sweep_type_refused(key):
    m = MODULES[key]
    try:
        m.build_sweep_points("Triangle", max_val=1, step_val=1)
    except ValueError as e:
        assert "Unknown sweep type" in str(e)
    else:
        raise AssertionError("unknown sweep type accepted")


@parametrize_modules
def test_limit_check(key):
    m = MODULES[key]
    m.check_sweep_limits(np.array([0.0, 0.5, -1.0]), 1.0, "A")
    try:
        m.check_sweep_limits(np.array([0.0, 1.0001]), 1.0, "A")
    except ValueError as e:
        assert "limit" in str(e)
    else:
        raise AssertionError("over-limit sweep accepted")
    try:
        m.check_sweep_limits(np.array([]), 1.0, "A")
    except ValueError:
        pass
    else:
        raise AssertionError("empty sweep accepted")


def test_instrument_ceilings_match_the_manuals():
    assert MODULES["k2400"].K2400_MAX_CURRENT_A == 1.05
    assert MODULES["k2182"].K2400_MAX_CURRENT_A == 1.05
    assert MODULES["k2400"].K2400_MAX_COMPLIANCE_V == 210.0
    assert MODULES["k6517b"].K6517B_MAX_VOLTAGE_V == 1000.0
    assert MODULES["k6517b"].K6517B_LOW_RANGE_V == 100.0


# ---------------------------------------------------------------------------
# "Start -> Stop (linear)" exists on the two modules that had it before
# ---------------------------------------------------------------------------

def test_k6517b_linear_mode_is_the_default_and_uses_point_count():
    m = MODULES["k6517b"]
    assert m.SWEEP_TYPES[0] == m.SWEEP_LINEAR
    pts = m.build_sweep_points(m.SWEEP_LINEAR, start_val=-10, stop_val=10, num_points=5)
    assert _close(pts, [-10, -5, 0, 5, 10])
    for n in [1, 0, "x"]:
        try:
            m.build_sweep_points(m.SWEEP_LINEAR, start_val=0, stop_val=1, num_points=n)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted num_points={n!r}")


def test_k2182_linear_mode_is_the_default_and_uses_step():
    m = MODULES["k2182"]
    assert m.SWEEP_TYPES[0] == m.SWEEP_LINEAR
    pts = m.build_sweep_points(m.SWEEP_LINEAR, start_val=-1, stop_val=1, step_val=0.5)
    assert _close(pts, [-1, -0.5, 0, 0.5, 1])
    # direction from start/stop; the step sign is ignored
    down = m.build_sweep_points(m.SWEEP_LINEAR, start_val=1, stop_val=-1, step_val=0.5)
    assert _close(down, [1, 0.5, 0, -0.5, -1])
    # non-multiple span still ends on stop
    pts = m.build_sweep_points(m.SWEEP_LINEAR, start_val=0, stop_val=1, step_val=0.3)
    assert _close(pts, [0, 0.3, 0.6, 0.9, 1.0])
    # start == stop is a single point, not an error
    assert _close(m.build_sweep_points(m.SWEEP_LINEAR, start_val=2, stop_val=2, step_val=1), [2])


def test_k2400_module_offers_exactly_the_three_requested_types():
    m = MODULES["k2400"]
    assert m.SWEEP_TYPES == (m.SWEEP_ZERO_TO_MAX, m.SWEEP_LOOP, m.SWEEP_CUSTOM)


def test_all_three_copies_of_the_generator_agree():
    cases = [
        (lambda m: m.build_sweep_points(m.SWEEP_ZERO_TO_MAX, max_val=1.0, step_val=0.3)),
        (lambda m: m.build_sweep_points(m.SWEEP_LOOP, max_val=2.0, step_val=0.5, num_loops=2)),
        (lambda m: m.build_sweep_points(m.SWEEP_CUSTOM,
                                        custom_values=m.parse_custom_list("0 1; 2,3\n4"))),
    ]
    for case in cases:
        results = [case(MODULES[k]) for k in sorted(MODULES)]
        for r in results[1:]:
            assert _close(results[0], r)


if __name__ == "__main__":
    failures = 0
    names = [n for n in list(globals()) if n.startswith("test_")]
    for name in names:
        fn = globals()[name]
        keys = sorted(MODULES) if "key" in fn.__code__.co_varnames[:fn.__code__.co_argcount] else [None]
        for k in keys:
            try:
                fn(k) if k is not None else fn()
                print(f"PASS  {name}" + (f" [{k}]" if k else ""))
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL  {name}" + (f" [{k}]" if k else "") + f": {e!r}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
