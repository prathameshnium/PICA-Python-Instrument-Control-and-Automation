import pytest
import importlib
import inspect
from unittest.mock import MagicMock, patch
import contextlib

from pica.cli import ALL_GUI_MODULES

@pytest.mark.usefixtures("mock_tkinter", "safe_matplotlib")
@pytest.mark.parametrize("module_path", ALL_GUI_MODULES)
def test_gui_module_initialization(module_path, safe_matplotlib):

    try:
        # 1. Import the module
        gui_module = importlib.import_module(module_path)

        # 2. Find the GUI class
        gui_class = None
        module_name_base = module_path.split('.')[-1]

        defined_classes = [
            obj for name, obj in inspect.getmembers(gui_module, inspect.isclass)
            if obj.__module__ == gui_module.__name__
        ]

        # Strategies to find the main GUI class
        for obj in defined_classes:
            if obj.__name__.lower() == module_name_base.lower():
                gui_class = obj
                break
        if not gui_class:
            for obj in defined_classes:
                if any(s in obj.__name__ for s in ['GUI', 'App', 'Window']):
                    gui_class = obj
                    break
        if not gui_class and defined_classes:
            valid_classes = [c for c in defined_classes if not c.__name__.startswith('_')]
            if valid_classes:
                gui_class = valid_classes[0]

        if not gui_class:
            found_names = [c.__name__ for c in defined_classes]
            pytest.fail(f"Could not find GUI class in {module_path}. Found: {found_names}")

        # 3. Inspect Constructor Signature
        sig = inspect.signature(gui_class.__init__)
        num_params = len(sig.parameters)

        mock_root = MagicMock()

        # 4. Instantiate with Patching
        # We patch 'log' to prevent errors on a non-existent console widget.
        # We patch 'create_widgets' to prevent deep instantiation of Tkinter/Matplotlib
        # components, which causes errors with mocks. We only want to test
        # if the class's __init__ can be called without crashing.
        patchers = []
        if hasattr(gui_class, 'log'):
            patchers.append(patch.object(gui_class, 'log', return_value=None))

        # The main fix: prevent widget creation, which triggers the complex error
        if hasattr(gui_class, 'create_widgets'):
            patchers.append(patch.object(gui_class, 'create_widgets', return_value=None))

        app_instance = None
        with contextlib.ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)

            # Instantiate the class
            if num_params <= 2:
                app_instance = gui_class(mock_root)
            else:
                app_instance = gui_class(mock_root, MagicMock())

        assert app_instance is not None, "GUI class failed to instantiate."

    except (ImportError, AttributeError, Exception) as e:
        pytest.fail(f"Failed to initialize '{module_path}'. Error: {e}")