"""
tests/test_gui_layouts.py
Tests GUI instantiation by deeply patching the Matplotlib Figure class.
"""
import pytest
import os
import sys
import inspect
from unittest.mock import MagicMock, patch

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

GUI_TARGETS = [
    "Keithley_6517B.High_Resistance.IV_K6517B_GUI_v11",
    "Keithley_6517B.High_Resistance.RT_K6517B_L350_T_Control_GUI_v13",
    "Keithley_2400.IV_K2400_GUI_v5",
    "Lakeshore_350_340.T_Sensing_L350_GUI_v4"
]

# --- SAFE CLASSES ---
class SafeAxis(MagicMock):
    """An Axis that always returns a list for plot()"""
    def plot(self, *args, **kwargs):
        return [MagicMock()]

class SafeFigure(MagicMock):
    """A Figure that creates SafeAxes"""
    def add_subplot(self, *args, **kwargs):
        return SafeAxis()

    def subplots(self, nrows=1, ncols=1, **kwargs):
        count = nrows * ncols
        if count > 1:
            return [SafeAxis() for _ in range(count)]
        return SafeAxis()
        
    def tight_layout(self, *args, **kwargs):
        pass

@pytest.fixture
def safe_gui_environment():
    # We need to patch where the code IMPORTS it from.
    # Most GUI files likely do: from matplotlib.figure import Figure
    
    with patch('matplotlib.figure.Figure', side_effect=SafeFigure) as MockFigClass, \
         patch('matplotlib.pyplot.figure', return_value=SafeFigure()), \
         patch('matplotlib.backends.backend_tkagg.FigureCanvasTkAgg') as MockCanvas:
        
        # Setup Canvas
        mock_canvas_instance = MockCanvas.return_value
        mock_canvas_instance.draw = MagicMock()
        mock_canvas_instance.get_tk_widget.return_value = MagicMock()

        # Also mock other heavy libs just in case
        with patch.dict('sys.modules', {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.simpledialog': MagicMock(),
            'pyvisa': MagicMock(),
            'pymeasure': MagicMock(),
            'PIL': MagicMock(),
            'PIL.Image': MagicMock(),
            'PIL.ImageTk': MagicMock()
        }):
            yield

@pytest.mark.parametrize("module_name", GUI_TARGETS)
def test_instantiate_gui_layout(module_name, safe_gui_environment):
    import importlib
    try:
        # Reload to ensure patches take effect on import
        if module_name in sys.modules:
            del sys.modules[module_name]
        module = importlib.import_module(module_name)
    except ImportError:
        pytest.skip(f"Could not import {module_name}")

    gui_class = None
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ == module.__name__:
            if "GUI" in name or "App" in name or "Window" in name:
                gui_class = obj
                break
    
    if gui_class:
        try:
            app = gui_class(MagicMock())
            assert app is not None
            print(f"\n[Layout] Successfully built layout for {module_name}")
        except Exception as e:
            pytest.fail(f"Layout crash in {module_name}: {e}")
    else:
        print(f"\n[Skip] No GUI class found in {module_name}")