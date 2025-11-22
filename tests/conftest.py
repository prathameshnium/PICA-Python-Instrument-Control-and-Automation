# tests/conftest.py
"""
This file contains shared fixtures for the PICA test suite.
Fixtures defined here are automatically available to all test files.
"""
import pytest
from unittest.mock import MagicMock, patch
import sys


@pytest.fixture
def mock_tkinter():
    """
    A comprehensive pytest fixture that mocks essential libraries (tkinter,
    matplotlib, pyvisa, etc.) to prevent any actual GUI rendering or hardware
    communication during tests. This is crucial for CI/CD environments.

    It correctly mocks matplotlib as a package to allow submodule imports
    like 'from matplotlib.figure import Figure'.
    """
    # Create a mock for matplotlib that acts like a package
    mock_matplotlib = MagicMock()
    mock_matplotlib.figure.Figure = MagicMock()
    mock_matplotlib.backends.backend_tkagg.FigureCanvasTkAgg = MagicMock(
        return_value=MagicMock(get_tk_widget=MagicMock())
    )

    # Create a mock for pymeasure that acts like a package
    mock_pymeasure = MagicMock()
    mock_pymeasure.instruments.keithley.Keithley2400 = MagicMock()
    mock_pymeasure.instruments.keithley.Keithley6517B = MagicMock()
    mock_pymeasure.instruments.agilent.AgilentE4980 = MagicMock()
    
    # Mock libraries that would otherwise create windows or require hardware
    mocked_modules = {
        'tkinter': MagicMock(),
        'tkinter.ttk': MagicMock(),
        'tkinter.messagebox': MagicMock(),
        'tkinter.filedialog': MagicMock(),
        'tkinter.simpledialog': MagicMock(),
        'tkinter.font': MagicMock(),
        'matplotlib': mock_matplotlib,
        'matplotlib.pyplot': MagicMock(),
        'matplotlib.figure': mock_matplotlib.figure,
        'matplotlib.backends': mock_matplotlib.backends,
        'matplotlib.backends.backend_tkagg': mock_matplotlib.backends.backend_tkagg,
        'pyvisa': MagicMock(), # Mock pyvisa
        'pymeasure': mock_pymeasure,
        'pymeasure.instruments': mock_pymeasure.instruments,
        'pymeasure.instruments.keithley': mock_pymeasure.instruments.keithley,
        'PIL': MagicMock(),
        'PIL.Image': MagicMock(),
        'PIL.ImageTk': MagicMock(),
    }
    with patch.dict('sys.modules', {
        **mocked_modules,
        'sys': sys.modules['sys'],
        'warnings': sys.modules['warnings'],
    }) as patched_modules:
        yield