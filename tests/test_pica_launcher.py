# tests/test_pica_launcher.py
"""
Unit tests for the main PICA Launcher GUI application.

These tests are designed to run in a CI environment, so they use mocking
to prevent the GUI from entering its main loop and blocking the test runner.
"""

import tkinter as tk
from unittest.mock import patch, MagicMock
import pytest

# The PICA_v6.py file contains the main PICALauncherApp
from PICA_v6 import PICALauncherApp


@pytest.fixture
def mock_app_dependencies():
    """
    Pytest fixture to mock dependencies that would block or interfere with
    a non-interactive test run.
    """
    # Mock methods that would create new windows, run blocking loops, or
    # perform file I/O.
    with patch('tkinter.Tk.mainloop', MagicMock()), \
         patch('PICA_v6.PICALauncherApp.run_gpib_test', MagicMock()), \
         patch('PICA_v6.PICALauncherApp._load_logo', MagicMock()), \
         patch('PICA_v6.PICALauncherApp._pre_cache_markdown_files', MagicMock()):
        yield


def test_pica_launcher_initialization(mock_app_dependencies):
    """
    Tests if the PICALauncherApp initializes without errors.

    It confirms that the main window is created and that key widgets
    like the console are initialized.
    """
    root = tk.Tk()
    app = PICALauncherApp(root)

    # --- Assertions ---
    assert app is not None, "PICALauncherApp failed to initialize."
    assert app.root.title() == f"PICA Launcher v{app.PROGRAM_VERSION}", "Window title was not set correctly."
    assert app.console_widget is not None, "Console widget was not created during initialization."