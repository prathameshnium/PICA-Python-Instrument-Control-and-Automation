"""
Purpose: Module-level GUI smoke tests.

What it does: Dynamically finds all files ending in _GUI.py, imports them, and tries to initialize their main class to ensure they don't crash on startup.
"""
import pytest
import os
import sys
import importlib
from unittest.mock import MagicMock

# 1. SETUP PROJECT PATH
# Ensure the test can see the project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

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
@pytest.mark.usefixtures("mock_tkinter")
def test_gui_module_initialization(module_path):
    """
    A parameterized test that attempts to import and instantiate the main
    application class from each discovered GUI module.
    """
    try:
        # Import the GUI module dynamically
        gui_module = importlib.import_module(module_path)

        # The main application class is assumed to have the same name as the file,
        base_name = module_path.split('.')[-1]
        
        # A more robust way to find the class.
        # We look for a class in the module that ends with 'GUI' and is defined in that module.
        gui_class = None
        for name, obj in gui_module.__dict__.items():
            if isinstance(obj, type) and obj.__module__ == gui_module.__name__:
                if name.endswith('GUI'):
                    gui_class = obj
                    break
        
        if not gui_class:
            pytest.fail(f"Could not find a suitable GUI class in module: {module_path}")


        # Get the class from the module and instantiate it
        mock_root = MagicMock()
        app_instance = gui_class(mock_root)

        assert app_instance is not None, "GUI class failed to instantiate."

    except (ImportError, AttributeError, Exception) as e:
        pytest.fail(f"Failed to initialize GUI module '{module_path}'.\nError: {e}")
