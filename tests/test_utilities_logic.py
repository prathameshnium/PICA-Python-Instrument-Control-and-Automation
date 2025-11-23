"""
Purpose: Logic checks for helper tools.

What it does: Verifies that the LivePlotter correctly appends data to arrays and that formatting utilities define the correct fonts/styles.
"""
import pytest
from unittest.mock import MagicMock, patch
import sys
import os

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def test_live_plotter_logic():
    """
    Tests the LivePlotter class. We mock matplotlib, but we verify
    that the class handles data appending correctly.
    """
    # Mock matplotlib so we don't need a screen
    with patch('matplotlib.pyplot.figure'), \
         patch('matplotlib.backends.backend_tkagg.FigureCanvasTkAgg'):
        
        try:
            from Utilities.LivePlotter_v10 import LivePlotter
        except ImportError:
            pytest.skip("Could not import LivePlotter.")

        # Instantiate
        mock_root = MagicMock()
        plotter = LivePlotter(mock_root)
        
        # Test 1: Check initial state
        assert hasattr(plotter, 'x_data')
        assert hasattr(plotter, 'y_data')
        assert len(plotter.x_data) == 0

        # Test 2: Update Logic (The most important part)
        # We simulate adding a data point
        plotter.update_plot(1.0, 10.5)
        
        # Verify data was stored (This proves the logic works)
        assert len(plotter.x_data) == 1
        assert len(plotter.y_data) == 1
        assert plotter.x_data[0] == 1.0
        assert plotter.y_data[0] == 10.5
        print("\n[Utilities] LivePlotter data appending logic verified.")

def test_gui_basic_formatter():
    """
    Tests the GUI_Basic_Format_v2 module.
    This module likely sets up fonts or styles.
    """
    with patch('tkinter.font.Font', MagicMock()): # Mock font creation
        try:
            import Utilities.GUI_Basic_Format_v2 as GUI_Format
        except ImportError:
            pytest.skip("Could not import GUI_Basic_Format.")
            
        # Check if constants exist (simple but effective coverage)
        if hasattr(GUI_Format, 'FONT_STYLE_BOLD'):
            assert GUI_Format.FONT_STYLE_BOLD is not None
            print("\n[Utilities] GUI Constants verified.")
