# tests/conftest.py
import pytest
from unittest.mock import MagicMock, patch
import sys
import matplotlib

# Force Agg backend immediately when tests start
matplotlib.use('Agg')

@pytest.fixture
def safe_matplotlib():
    """
    Ensures plots are closed after test to free memory.
    """
    yield
    matplotlib.pyplot.close('all')

@pytest.fixture
def mock_tkinter():
    """
    A comprehensive pytest fixture that mocks essential libraries (tkinter,
    pyvisa, etc.) to prevent any actual GUI rendering or hardware
    communication during tests. This is crucial for CI/CD environments.
    """
    # Create a mock for pymeasure that acts like a package
    mock_pymeasure = MagicMock()
    mock_pymeasure.instruments.keithley.Keithley2400 = MagicMock()
    mock_pymeasure.instruments.keithley.Keithley6517B = MagicMock()
    mock_pymeasure.instruments.agilent.AgilentE4980 = MagicMock()
    
    # A more robust tkinter mock to fix the KeyError during canvas creation
    mock_tk_app = MagicMock()
    canvas_instance_mock = MagicMock()
    # This is the key fix: make the result of internal tk.call() a string,
    # so that winfo_toplevel() and nametowidget() don't fail on a mock object.
    canvas_instance_mock.tk.call.return_value = ".dummy.widget.path"
    mock_tk_app.Canvas.return_value = canvas_instance_mock
    
    # Mock libraries that would otherwise create windows or require hardware
    mocked_modules = {
        'tkinter': mock_tk_app,
        'tkinter.ttk': MagicMock(),
        'tkinter.messagebox': MagicMock(),
        'tkinter.filedialog': MagicMock(),
        'tkinter.simpledialog': MagicMock(),
        'tkinter.font': MagicMock(),
        'pyvisa': MagicMock(), # Mock pyvisa
        'pymeasure': mock_pymeasure,
        'pymeasure.instruments': mock_pymeasure.instruments,
        'pymeasure.instruments.keithley': mock_pymeasure.instruments.keithley,
        'pymeasure.instruments.agilent': mock_pymeasure.instruments.agilent,
        'PIL': MagicMock(),
        'PIL.Image': MagicMock(),
        'PIL.ImageTk': MagicMock(),
    }
    with patch.dict('sys.modules', {
        **mocked_modules,
        # Keep real sys and warnings
        'sys': sys.modules['sys'], 
        'warnings': sys.modules['warnings'],
    }) as patched_modules:
        yield