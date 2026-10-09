"""Launcher v2: regression tests for the hover cards and the search box.

These drive a REAL Tk launcher with real events -- the pointer entering and
leaving module rows, keys typed into the search box, clicks, Enter, Escape,
Ctrl+F, the Advanced Options window being closed -- and fail on any error a
Tk callback or a Tcl background job raises along the way.

Each test pins a fault that has been seen, or the design decision that
removed it:

* Advanced Options could not be closed. The hover timer was scheduled on a
  module row and cancelled through the root window, which left a dead Tcl
  command registered on the row; destroying the row then raised
  "can't delete Tcl command", Tk stopped tearing the window down half way
  (header, strip and a column of cards gone) and the window stayed open.
  The same fault fired whenever the card grid reflowed after a hover.
* The search results were a borderless popup WINDOW rebuilt on every key.
  On Windows such a window takes the keyboard focus when it appears, so the
  letters typed after the first were lost and the list flickered. The panel
  is now a frame placed inside the window that owns the box, built once.
* Every key ran the full ranking (~55 ms here, more on a lab PC). Typing is
  now debounced and the fuzzy tier runs once per query word.
* A hover card pushed back on screen near the right or bottom edge landed
  under the pointer, stole the <Leave> from the row, closed, re-armed and
  opened again in a loop.

The pure-Python checks are below. The live Tk scenarios are in
tests/launcher_v2_tk_scenarios.py and run in a fresh interpreter from the
last test in this file; they skip when no display is available.
"""
import importlib.util
import os
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

V2_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")


def _load_launcher():
    spec = importlib.util.spec_from_file_location("pica_main_v2_regress", V2_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pica_main_v2_regress"] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()
V2 = launcher.PICALauncherV2

from pica import module_info as mi  # noqa: E402


# =============================================================================
#  Pure Python: no display needed
# =============================================================================
SCREEN_W, SCREEN_H = 1920, 1080
CARD_W, CARD_H = 560, 340


def _covers(x, y, px, py, w=CARD_W, h=CARD_H):
    return x <= px <= x + w and y <= py <= y + h


@pytest.mark.parametrize("px", range(0, SCREEN_W + 1, 40))
@pytest.mark.parametrize("py", range(0, SCREEN_H + 1, 60))
def test_the_hover_card_never_lands_under_the_pointer(px, py):
    x, y = V2._hover_position(px, py, CARD_W, CARD_H, SCREEN_W, SCREEN_H)
    assert not _covers(x, y, px, py), (px, py, x, y)
    assert x >= 0 and y >= 0


def test_the_hover_card_flips_left_at_the_right_edge():
    x, _y = V2._hover_position(1800, 300, CARD_W, CARD_H, SCREEN_W, SCREEN_H)
    assert x + CARD_W < 1800


def test_the_hover_card_flips_up_at_the_bottom_edge():
    _x, y = V2._hover_position(300, 1000, CARD_W, CARD_H, SCREEN_W, SCREEN_H)
    assert y + CARD_H < 1000


def test_the_hover_card_sits_right_and_below_when_it_fits():
    x, y = V2._hover_position(300, 300, CARD_W, CARD_H, SCREEN_W, SCREEN_H)
    assert x > 300 and y > 300


INDEX = mi.build_search_index(launcher.CATALOG, launcher.QUICK_CATALOG,
                              V2.SCRIPT_PATHS, mi.UTILITY_TOOLS)


@pytest.mark.parametrize("query", [
    "temperature", "resistence", "dielectric cryocon", "zzqx",
    "lakeshore 340 step", "k2400 rt", "pyroelectric current l340",
])
def test_a_keystroke_search_stays_fast(query):
    """Every prefix of the query, as typing produces them, ranks quickly.

    The bound is generous (a slow CI runner must not flake): the old scorer
    took ~55 ms here, the current one ~3 ms.
    """
    worst = 0.0
    for i in range(1, len(query) + 1):
        start = time.perf_counter()
        mi.search_modules(INDEX, query[:i], limit=V2.SEARCH_LIMIT)
        worst = max(worst, time.perf_counter() - start)
    assert worst < 0.030, f"{query!r}: {worst * 1000:.1f} ms per key"


def test_fuzzy_matching_still_forgives_a_typo_after_the_speed_up():
    assert mi.search_modules(INDEX, "resistence")
    assert mi.search_modules(INDEX, "dielectic")
    assert mi.search_modules(INDEX, "temprature")
    assert mi.search_modules(INDEX, "lakshore")


def test_short_words_are_not_fuzzy_matched():
    assert mi._fuzzy_set("rt", {"rt", "at", "it"}) == frozenset()
    assert mi._fuzzy_set("lakeshore 350", {"lakeshore"}) == frozenset()


def test_no_timer_is_scheduled_on_a_short_lived_widget():
    """Every after() in the hover and search code is on self.root.

    A timer scheduled on a row or an entry and cancelled through the root
    leaves a dead command on the row, and destroying it then raises.
    """
    import inspect
    for name in ("_add_module_hover", "_search_typed", "_search_focus_out",
                 "_show_hover", "_cancel_hover", "_cancel_search_timer"):
        source = inspect.getsource(getattr(V2, name))
        for line in source.splitlines():
            if ".after(" in line or ".after_idle(" in line or "after_cancel(" in line:
                assert "self.root." in line, f"{name}: {line.strip()}"


# =============================================================================
#  Live Tk scenarios, in a fresh interpreter
# =============================================================================
SCENARIOS = os.path.join(REPO_ROOT, "tests", "launcher_v2_tk_scenarios.py")


def test_live_tk_scenarios_pass_in_a_fresh_interpreter():
    """Every scenario in tests/launcher_v2_tk_scenarios.py passes.

    They type, click, hover and close windows on a real Tk launcher, so they
    need the keyboard focus -- which a Tk interpreter left alive by an
    earlier test in this process would hold. See that file's docstring.
    """
    import subprocess
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider",
         # -rfEs: name failures and errors as well as skips in the
         # summary (-rs alone dropped the failing scenario's name).
         "-q", "-rfEs", "-o", "addopts=", SCENARIOS],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=900)
    out = proc.stdout + proc.stderr
    tail = "\n".join(out.splitlines()[-60:])
    if proc.returncode == 0 and " passed" not in out and " skipped" in out:
        pytest.skip("no usable Tk display for the live scenarios:\n" + tail)
    failed = [line for line in out.splitlines()
              if line.startswith(("FAILED", "ERROR"))]
    assert proc.returncode == 0, (
        "live Tk scenarios failed:\n" + "\n".join(failed) + "\n\n" + tail)
    assert " passed" in out, tail


def test_the_scenarios_file_is_not_collected_by_a_plain_run():
    """It must only ever run through the fresh-interpreter test above."""
    assert not os.path.basename(SCENARIOS).startswith("test_")
    assert os.path.exists(SCENARIOS)
