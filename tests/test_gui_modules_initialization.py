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
    Recursively finds all GUI modules in the project, excluding non-GUI files.
    Returns them in a format suitable for importlib (e.g., 'Keithley_2400.IV_K2400_GUI_v5').
    """
    gui_files = []
    for root, _, files in os.walk(project_root):
        # Exclude directories that are not part of the main source code
        if 'tests' in root or '.git' in root or 'venv' in root or '__pycache__' in root or 'assets' in root or 'Setup' in root:
            continue
        for file in files:
            if file.endswith('_GUI_v' or '_GUI.py') and file != '__init__.py':
                full_path = os.path.join(root, file)
                # Convert file path to module path
                # e.g., F:\...\PICA\Keithley_2400\IV_K2400_GUI_v5.py -> Keithley_2400.IV_K2400_GUI_v5
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
        # but without the version suffix (e.g., 'IV_K2400_GUI' from 'IV_K2400_GUI_v5.py')
        # This logic needs to be robust to handle different naming conventions.
        base_name = module_path.split('.')[-1]
        if '_GUI_v' in base_name:
            class_name = base_name.split('_GUI_v')[0] + "_GUI"
        elif base_name.endswith('_GUI'):
            class_name = base_name
        else:
             pytest.fail(f"Could not determine class name from module path: {module_path}")


        # Get the class from the module and instantiate it
        AppClass = getattr(gui_module, class_name)
        mock_root = MagicMock()
        app_instance = AppClass(mock_root)

        assert app_instance is not None, "GUI class failed to instantiate."

    except (ImportError, AttributeError, Exception) as e:
        pytest.fail(f"Failed to initialize GUI module '{module_path}'.\nError: {e}")