# tests/test_scrollable_panels.py
"""Regression guard: the live-measurement GUIs must keep the scrollable
left-panel pattern (Canvas + vertical Scrollbar + create_window + a
scrollregion <Configure> bind + sash auto-positioning), as established by
the k6517b / Keysight modules. A GUI that loses this pattern clips its
parameter controls on short windows with no way to reach them.
"""
from pathlib import Path

import pytest

PICA_DIR = Path(__file__).resolve().parent.parent / "pica"

# GUIs required to have the scrollable left panel. Extend this list when
# a new live-measurement GUI adopts the pattern.
SCROLLABLE_GUIS = [
    "keithley/k6517b/High_Resistance/RT_K6517B_L350_T_Sensing_GUI.py",
    "keithley/k6517b/High_Resistance/RT_K6517B_L350_T_Control_GUI.py",
    "keithley/k2400/RT_K2400_L350_T_Control_GUI.py",
    "keithley/k2400_2182/RT_K2400_K2182_T_Control_GUI.py",
    "keithley/delta_mode/Delta_RT_K6221_K2182_L350_Sensing_GUI.py",
    "keithley/delta_mode/Delta_RT_K6221_K2182_L350_T_Control_GUI.py",
    "keithley/delta_mode/IV_K6221_DC_Sweep_GUI.py",
]

# Each token is a structural ingredient of the pattern; all must appear.
REQUIRED_TOKENS = {
    "vertical scrollbar driving the canvas":
        'command=canvas.yview',
    "inner frame hosted in the canvas":
        'canvas.create_window',
    "scrollregion kept in sync with content":
        'scrollregion=canvas.bbox("all")',
    "inner frame width synced to viewport":
        "canvas.itemconfigure(window_id, width=e.width)",
    "canvas fed by the scrollbar":
        "yscrollcommand=scrollbar.set",
    "sash auto-positioning helper":
        "_set_default_sash_position",
    "left panel width fallback":
        "LEFT_PANEL_WIDTH",
    "container keeps requested width":
        "pack_propagate(False)",
}


@pytest.mark.parametrize("rel_path", SCROLLABLE_GUIS,
                         ids=lambda p: Path(p).stem)
def test_gui_has_scrollable_left_panel(rel_path):
    path = PICA_DIR / rel_path
    assert path.exists(), f"GUI file missing: {path}"
    text = path.read_text(encoding="utf-8", errors="replace")

    missing = [f"{desc} (expected: {token!r})"
               for desc, token in REQUIRED_TOKENS.items()
               if token not in text]

    assert not missing, (
        f"{rel_path} lost part of the scrollable left-panel pattern "
        f"(see the k6517b GUIs for the reference implementation):\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.parametrize("rel_path", SCROLLABLE_GUIS,
                         ids=lambda p: Path(p).stem)
def test_no_unscrollable_vertical_paned_left_panel(rel_path):
    """The old anti-pattern: left panel as a vertical PanedWindow (no
    scroll, content clips on short windows)."""
    text = (PICA_DIR / rel_path).read_text(encoding="utf-8", errors="replace")
    assert "orient='vertical'" not in text.replace('"', "'") or \
        "command=canvas.yview" in text, (
        f"{rel_path} appears to use a vertical PanedWindow left panel "
        "without a scrollable canvas."
    )
