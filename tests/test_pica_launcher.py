import unittest
from unittest.mock import patch

# -----------------------------------------------------------------------------
# MOCK HELPER CLASSES for GUI Testing
# These classes simulate the necessary parts of the GUI environment (like Tkinter)
# -----------------------------------------------------------------------------
class MockTkWidget:
    """Mock object for Tkinter/PyQt widgets to simulate button interactions."""
    def __init__(self, command=None):
        self.command = command

    def get(self):
        """Not typically used for launcher buttons, but included for completeness."""
        return ''

    def invoke(self):
        """Simulates a button click (calls the attached command)."""
        if self.command:
            self.command()


class MockPicaLauncher:
    """
    Simulates the PICA_v6.py launcher class structure.
    NOTE: Adapt the button names and method names below to match your real PICA_v6.py file.
    """
    def __init__(self):
        # Attach MockTkWidget to simulate the 'command' binding for the button
        self.button_iv_sweep = MockTkWidget(command=self.open_iv_sweep)
        self.button_temp_control = MockTkWidget(command=self.open_temp_control)
        # Internal state tracker to confirm methods are called
        self.open_gui_calls = {'iv_sweep': 0, 'temp_control': 0}

    def open_iv_sweep(self):
        """Simulates the handler for the IV sweep button."""
        self.open_gui_calls['iv_sweep'] += 1

    def open_temp_control(self):
        """Simulates the handler for the Temperature Control button."""
        self.open_gui_calls['temp_control'] += 1


# -----------------------------------------------------------------------------
# THE ACTUAL TESTS for the Launcher
# -----------------------------------------------------------------------------

class TestPicaLauncher(unittest.TestCase):
    """
    Tests the main PICA Launcher (PICA_v6.py) to ensure correct routing logic.
    """
    
    # We patch the actual method that is called by the button to ensure it fires.
    @patch('test_pica_launcher.MockPicaLauncher.open_iv_sweep')
    def test_iv_sweep_button_click_calls_function(self, mock_open_iv_sweep):
        """Tests that clicking the IV Sweep button calls its launch function once."""
        launcher = MockPicaLauncher()
        # Simulate button click
        launcher.button_iv_sweep.invoke()

        # Assert the mocked function was called
        mock_open_iv_sweep.assert_called_once()
        # Assert the internal counter was incremented
        self.assertEqual(launcher.open_gui_calls['iv_sweep'], 1)

    @patch('test_pica_launcher.MockPicaLauncher.open_temp_control')
    def test_temp_control_button_click_calls_function(self, mock_open_temp_control):
        """Tests that clicking the Temp Control button calls its launch function once."""
        launcher = MockPicaLauncher()
        # Simulate button click
        launcher.button_temp_control.invoke()

        # Assert the mocked function was called
        mock_open_temp_control.assert_called_once()
        # Assert the internal counter was incremented
        self.assertEqual(launcher.open_gui_calls['temp_control'], 1)

    def test_multiple_clicks(self):
        """Tests that multiple clicks correctly register multiple calls."""
        launcher = MockPicaLauncher()
        # Simulate multiple clicks
        for _ in range(3):
            launcher.button_iv_sweep.invoke()
        self.assertEqual(launcher.open_gui_calls['iv_sweep'], 3)
        self.assertEqual(launcher.open_gui_calls['temp_control'], 0)  # Ensure others are not called


if __name__ == '__main__':
    unittest.main()