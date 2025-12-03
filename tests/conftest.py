# tests/conftest.py
import pytest
import sys
from unittest.mock import MagicMock
import matplotlib

# Force Agg backend immediately when tests start
matplotlib.use('Agg')

@pytest.fixture
def mock_tkinter(monkeypatch):
    """
    Mocks tkinter to prevent GUI windows from opening.
    """
    mock_tk = MagicMock()
    # Mock specific attributes accessed by your GUIs
    mock_tk.Tk = MagicMock()
    mock_tk.Toplevel = MagicMock()
    mock_tk.Canvas = MagicMock()
    
    # Ensure variables return mocks that can be .get() or .set()
    mock_var = MagicMock()
    mock_var.get.return_value = 0
    mock_tk.StringVar.return_value = mock_var
    mock_tk.IntVar.return_value = mock_var
    mock_tk.BooleanVar.return_value = mock_var
    
    monkeypatch.setitem(sys.modules, 'tkinter', mock_tk)
    return mock_tk

@pytest.fixture
def safe_matplotlib():
    """
    Ensures plots are closed after test to free memory.
    """
    # We already set 'Agg' globally, so just handle cleanup here.
    yield
    matplotlib.pyplot.close('all')
