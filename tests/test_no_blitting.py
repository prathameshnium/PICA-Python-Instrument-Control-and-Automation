# tests/test_no_blitting.py
"""Regression guard: matplotlib blitting must never return to the GUIs.

Blitting draws new data onto a cached background image that was captured
with the OLD axis limits, so ticks/gridlines/axis frames stop updating as
data grows.  It was removed from all live-plot GUIs (see k6517b commit
95e8357 and the k2400/k2400_2182/delta_mode fixes); this test fails if any
blitting API reappears anywhere under pica/.
"""
from pathlib import Path

PICA_DIR = Path(__file__).resolve().parent.parent / "pica"

FORBIDDEN_TOKENS = (
    "copy_from_bbox",
    "restore_region",
    "draw_artist",
    ".blit(",
    "set_animated",
    "animated=True",
)


def test_no_blitting_apis_in_gui_sources():
    gui_files = sorted(PICA_DIR.rglob("*GUI*.py"))
    assert gui_files, f"No GUI files found under {PICA_DIR} - broken scan?"

    violations = []
    for path in gui_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for token in FORBIDDEN_TOKENS:
                if token in line:
                    violations.append(
                        f"{path.relative_to(PICA_DIR.parent)}:{lineno} "
                        f"[{token}] {line.strip()}")

    assert not violations, (
        "Blitting APIs found in GUI sources. Blitting freezes axis "
        "limits/ticks against a stale cached background - use the "
        "_plot_dirty + _refresh_plot + draw_idle() pattern instead "
        "(see RT_K6517B_L350_T_Sensing_GUI.py):\n" + "\n".join(violations)
    )
