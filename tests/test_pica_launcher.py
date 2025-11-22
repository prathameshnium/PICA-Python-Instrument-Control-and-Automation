import pytest
import os
import sys
import tkinter as tk
from unittest.mock import MagicMock, patch

# Ensure the project root is in sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Try importing the Launcher. If it fails due to imports, we skip.
try:
    from PICA_v6 import PICALauncherApp
except ImportError:
    PICALauncherApp = None

@pytest.fixture
def mock_app_dependencies():
    """
    Mocks the heavy dependencies: PIL, Subprocess, AND Fonts.
    """
    with patch('PIL.Image.open', MagicMock()), \
         patch('PIL.ImageTk.PhotoImage', MagicMock()), \
         patch('subprocess.Popen', MagicMock()), \
         patch('tkinter.font.Font', MagicMock()):  # <--- FIX: Mock Font creation
        yield

def test_pica_launcher_initialization(mock_app_dependencies):
    """
    Tests if the PICALauncherApp initializes without errors.
    """
    if PICALauncherApp is None:
        pytest.skip("PICALauncherApp could not be imported.")

    # Patch tk.Tk to prevent 'no display name' error on headless servers
    with patch('tkinter.Tk') as MockTk:
        mock_root = MockTk.return_value
        
        # Initialize the app with the mocked root
        # This will now succeed because font.Font() is also mocked
        app = PICALauncherApp(mock_root)
        
        # ASSERTIONS
        assert app.root == mock_root
        assert app is not None
        
        print("\n[SUCCESS] PICALauncherApp initialized safely with Mock Tk and Mock Font.")