import pytest
import importlib
import inspect
from unittest.mock import MagicMock, patch # <--- Make sure 'patch' is added here
import tkinter as tk
from pica.cli import ALL_GUI_MODULES

@pytest.mark.parametrize("module_path", ALL_GUI_MODULES)
@pytest.mark.usefixtures("mock_tkinter", "safe_matplotlib")
def test_gui_module_initialization(module_path, safe_matplotlib):
    """
    Attempts to import and instantiate the main application class.
    Includes fixes for 'log' side effects and improved class discovery.
    """
    try:
        # 1. Import the module
        gui_module = importlib.import_module(module_path)

        # 2. Find the GUI class
        gui_class = None
        module_name_base = module_path.split('.')[-1]
        
        # Get all classes defined specifically in this module (not imported ones)
        defined_classes = [
            obj for name, obj in inspect.getmembers(gui_module, inspect.isclass)
            if obj.__module__ == gui_module.__name__
        ]

        # Strategy A: Exact Name Match (Case Insensitive)
        for obj in defined_classes:
            if obj.__name__.lower() == module_name_base.lower():
                gui_class = obj
                break
        
        # Strategy B: Common Suffixes
        if not gui_class:
            for obj in defined_classes:
                name = obj.__name__
                if name.endswith('GUI') or name.endswith('App') or name.endswith('Window'):
                    gui_class = obj
                    break

        # Strategy C: Fallback (Pick the first class that isn't private)
        if not gui_class and defined_classes:
            # Filter out generic names if possible, but take the first valid one
            valid_classes = [c for c in defined_classes if not c.__name__.startswith('_')]
            if valid_classes:
                gui_class = valid_classes[0]

        if not gui_class:
            # Print available classes to help debugging
            found_names = [c.__name__ for c in defined_classes]
            pytest.fail(f"Could not find GUI class in {module_path}. Found: {found_names}")

        # 3. Inspect Constructor Signature
        # This determines if we need to pass (root) or (root, manager)
        sig = inspect.signature(gui_class.__init__)
        # parameters includes 'self', so length of 2 means __init__(self, root)
        num_params = len(sig.parameters)

        mock_root = MagicMock()
        
        # 4. Instantiate with Patching
        # We patch 'log' because some GUIs try to write to a console_widget 
        # that doesn't exist yet during the test instantiation.
        # getattr(..., None) handles cases where the class might not have a log method.
        if hasattr(gui_class, 'log'):
            with patch.object(gui_class, 'log', return_value=None):
                if num_params <= 2:
                    app_instance = gui_class(mock_root)
                else:
                    app_instance = gui_class(mock_root, MagicMock())
        else:
            # No log method, instantiate normally
            if num_params <= 2:
                app_instance = gui_class(mock_root)
            else:
                app_instance = gui_class(mock_root, MagicMock())

        assert app_instance is not None, "GUI class failed to instantiate."

    except (ImportError, AttributeError, Exception) as e:
        pytest.fail(f"Failed to initialize '{module_path}'. Error: {e}")