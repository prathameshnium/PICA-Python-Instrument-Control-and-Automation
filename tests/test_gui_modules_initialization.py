"""
Purpose: Module-level GUI smoke tests.

What it does: Dynamically finds all files ending in _GUI.py, imports them, and tries to initialize their main class to ensure they don't crash on startup.
"""
import pytest
import os
import sys
import inspect
import importlib
from unittest.mock import MagicMock, patch

# 1. SETUP PROJECT PATH
# Ensure the test can see the project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- SAFE MOCKING CLASSES for Matplotlib ---
class SafeAxis(MagicMock):
    """An Axis that always returns a list for plot() to allow unpacking."""
    def plot(self, *args, **kwargs):
        # Return a list with a MagicMock so it can be unpacked (e.g., `line, = ax.plot(...)`)
        return [MagicMock()]

class SafeFigure(MagicMock):
    """A Figure that creates SafeAxes and handles various subplot calls."""
    def add_subplot(self, *args, **kwargs):
        return SafeAxis()

    def subplots(self, nrows=1, ncols=1, **kwargs):
        count = nrows * ncols
        if count > 1:
            # Return a list of axes if multiple are requested
            return [SafeAxis() for _ in range(count)]
        # Return a single axis otherwise
        return SafeAxis()
        
    def tight_layout(self, *args, **kwargs):
        pass

@pytest.fixture
def safe_matplotlib():
    """A fixture that patches matplotlib to prevent ValueErrors during unpacking."""
    with patch('matplotlib.figure.Figure', side_effect=SafeFigure), \
         patch('matplotlib.pyplot.figure', return_value=SafeFigure()):
        yield

def find_gui_modules():
    """
    Recursively finds all GUI modules in the `pica` directory.
    Returns them in a format suitable for importlib (e.g., 'pica.keithley.k2400.IV_K2400_GUI').
    """
    gui_files = []
    pica_dir = os.path.join(project_root, 'pica')
    for root, _, files in os.walk(pica_dir):
        for file in files:
            if file.endswith('_GUI.py') and not file.startswith('__'):
                full_path = os.path.join(root, file)
                # Convert file path to module path
                relative_path = os.path.relpath(full_path, project_root)
                module_path = os.path.splitext(relative_path)[0].replace(os.path.sep, '.')
                gui_files.append(module_path)
    return gui_files

# Cache the list so we don't scan disk multiple times
ALL_GUI_MODULES = find_gui_modules()

@pytest.mark.parametrize("module_path", ALL_GUI_MODULES)
@pytest.mark.usefixtures("mock_tkinter", "safe_matplotlib")
def test_gui_module_initialization(module_path, safe_matplotlib):
    """
    A parameterized test that attempts to import and instantiate the main
    application class from each discovered GUI module.
    """
    try:
        # Import the GUI module dynamically
        gui_module = importlib.import_module(module_path)

        # The main application class is assumed to have the same name as the file,
        # A more robust way to find the class.
        # We look for a class in the module that ends with 'GUI' and is defined in that module.
        gui_class = None
        for name, obj in inspect.getmembers(gui_module, inspect.isclass):
            if isinstance(obj, type) and obj.__module__ == gui_module.__name__:
                # Accept classes ending in GUI, App, or Window
                if name.endswith('GUI') or name.endswith('App') or name.endswith('Window'):
                    gui_class = obj
                    break
        
        if not gui_class:
            pytest.fail(f"Could not find a suitable GUI class in module: {module_path}")


        # Get the class from the module and instantiate it
        mock_root = MagicMock()
        # Some constructors might take more than one argument, so we use a more
        # robust instantiation by providing another MagicMock if needed.
        app_instance = gui_class(mock_root, MagicMock())

        assert app_instance is not None, "GUI class failed to instantiate."

    except (ImportError, AttributeError, Exception) as e:
        pytest.fail(f"Failed to initialize GUI module '{module_path}'.\nError: {e}")
