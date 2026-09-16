"""
Module: GUI module registry coverage guard.

Two jobs, both aimed at the same failure mode: a new *_GUI.py module lands and
nothing ever imports it, so it sits at 0% coverage and no test would notice it
had been broken.

1. test_every_gui_module_imports  -- discovers every pica/**/*_GUI.py from disk
   (no hand-maintained list, so it cannot go stale) and imports it. This alone
   executes module-level code: constants, class bodies, instrument tables.

2. test_registry_is_complete -- fails when a discovered module is neither in
   cli.ALL_GUI_MODULES (the deeper instantiation test in
   test_gui_modules_initialization.py) nor in INSTANTIATION_EXCLUSIONS below.
"""
import contextlib
import importlib
import pathlib
import sys
from unittest.mock import MagicMock, patch

import matplotlib
import pytest

matplotlib.use("Agg")

from pica import cli

PICA_ROOT = pathlib.Path(__file__).resolve().parent.parent / "pica"

# Modules that import cleanly but cannot be instantiated by
# test_gui_modules_initialization.py. These are artifacts of that test's
# patching strategy, not defects in the modules:
#   - it patches out create_widgets(), so an __init__ that goes on to touch a
#     widget attribute built there raises AttributeError;
#   - it mocks pyvisa, so `except pyvisa.VisaIOError` raises TypeError because
#     a MagicMock is not a BaseException subclass.
# They are still import-covered by test_every_gui_module_imports.
INSTANTIATION_EXCLUSIONS = {
    # __init__ touches widgets that create_widgets() would have built
    "pica.PPMS.PPMS_TimeEstimator_GUI",
    "pica.cryocon.Sensor_Curve_Loader_CC34_GUI",
    "pica.utils.MD_Ratio_Calculator_GUI",
    "pica.utils.PID_Simulator_GUI",
    "pica.utils.Time_Utility_GUI",
    "pica.utils.Unit_Converter_GUI",
    # `except pyvisa.VisaIOError` against a mocked pyvisa
    "pica.keysight.PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI",
    "pica.keysight.PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI",
    "pica.keysight.PPMS_Dielectric_Master_Tscan_Fscan_L340_E4980A_GUI",
    "pica.keysight.PPMS_Sync_Freq_Scan_CC34_E4980A_GUI",
    "pica.keysight.PPMS_Sync_Freq_Scan_E4980A_GUI",
    "pica.keysight.PPMS_Sync_Freq_Scan_L340_E4980A_GUI",
}


def discover_gui_modules():
    """Every pica/**/*_GUI.py as a dotted module path."""
    return sorted(
        ".".join(p.relative_to(PICA_ROOT.parent).with_suffix("").parts)
        for p in PICA_ROOT.rglob("*_GUI.py")
    )


ALL_DISCOVERED = discover_gui_modules()


@pytest.fixture
def deep_mock_gui_env():
    """
    Like conftest's mock_tkinter, but also stubs _tkinter and the matplotlib Tk
    backends. Without those the import fails on any machine whose Tcl install
    is incomplete ("Failed to load Tcl_SetVar"), which masks real import errors.
    Kept local to this file so the existing fixtures keep their current behaviour.
    """
    mock_pymeasure = MagicMock()
    mock_tk_app = MagicMock()
    canvas_instance_mock = MagicMock()
    canvas_instance_mock.tk.call.return_value = ".dummy.widget.path"
    mock_tk_app.Canvas.return_value = canvas_instance_mock

    mocked_modules = {
        "_tkinter": MagicMock(),
        "tkinter": mock_tk_app,
        "tkinter.ttk": MagicMock(),
        "tkinter.messagebox": MagicMock(),
        "tkinter.filedialog": MagicMock(),
        "tkinter.simpledialog": MagicMock(),
        "tkinter.font": MagicMock(),
        "tkinter.colorchooser": MagicMock(),
        "tkinter.scrolledtext": MagicMock(),
        "matplotlib.backends.backend_tkagg": MagicMock(),
        "matplotlib.backends._backend_tk": MagicMock(),
        "pyvisa": MagicMock(),
        "pymeasure": mock_pymeasure,
        "pymeasure.instruments": mock_pymeasure.instruments,
        "pymeasure.instruments.keithley": mock_pymeasure.instruments.keithley,
        "pymeasure.instruments.agilent": mock_pymeasure.instruments.agilent,
        "PIL": MagicMock(),
        "PIL.Image": MagicMock(),
        "PIL.ImageTk": MagicMock(),
    }
    with patch.dict(
        "sys.modules",
        {
            **mocked_modules,
            "sys": sys.modules["sys"],
            "warnings": sys.modules["warnings"],
        },
    ):
        yield


def test_gui_modules_are_discoverable():
    assert ALL_DISCOVERED, f"No *_GUI.py modules found under {PICA_ROOT}"


@pytest.mark.parametrize("module_path", ALL_DISCOVERED)
def test_every_gui_module_imports(module_path, deep_mock_gui_env, safe_matplotlib):
    """Every GUI module must import without raising."""
    try:
        assert importlib.import_module(module_path) is not None
    except BaseException as exc:  # noqa: BLE001 - report the module that broke
        pytest.fail(f"Failed to import '{module_path}'. Error: {type(exc).__name__}: {exc}")


def test_registry_is_complete():
    """
    A new *_GUI.py must be added to cli.ALL_GUI_MODULES, or explicitly excluded
    here with a reason. Without this, new modules silently land at 0% coverage.
    """
    registered = set(cli.ALL_GUI_MODULES) | INSTANTIATION_EXCLUSIONS
    unregistered = sorted(set(ALL_DISCOVERED) - registered)
    assert not unregistered, (
        "These GUI modules are in neither cli.ALL_GUI_MODULES nor "
        "INSTANTIATION_EXCLUSIONS, so nothing instantiates them:\n  "
        + "\n  ".join(unregistered)
    )


def test_exclusions_are_not_stale():
    """An exclusion for a module that no longer exists should be removed."""
    ghosts = sorted(INSTANTIATION_EXCLUSIONS - set(ALL_DISCOVERED))
    assert not ghosts, f"INSTANTIATION_EXCLUSIONS names modules that no longer exist: {ghosts}"
