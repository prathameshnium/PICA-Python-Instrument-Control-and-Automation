import unittest
from unittest.mock import patch, MagicMock

# The actual script paths from PICA_v6.py, for reference.
# These are hardcoded here to make the test self-contained and independent
# of the actual PICA_v6.py file's SCRIPT_PATHS for basic mock testing.
# In a more advanced setup, you might try to import and use PICALauncherApp.SCRIPT_PATHS
REAL_SCRIPT_PATHS = {
    "K2400 I-V": "Keithley_2400/IV_K2400_GUI_v5.py",
    "Lakeshore Temp Control": "Lakeshore_350_340/T_Control_L350_RangeControl_GUI_v8.py",
}

# Mapping from script_key to the text displayed on the button in PICA_v6.py
SCRIPT_KEY_TO_BUTTON_TEXT = {
    "K2400 I-V": "I-V Sweep",
    "Lakeshore Temp Control": "Temperature Ramp",
}


class MockTkWidget:
    """Mock object for Tkinter/PyQt widgets to simulate button interactions."""

    def __init__(self, command=None, text=""):
        self.command = command
        self.text = text

    def invoke(self):
        """Simulates a button click (calls the attached command)."""
        if self.command:
            self.command()


class MockPicaLauncher:
    """
    Simulates the PICALauncherApp's button creation and launching logic.
    """
    SCRIPT_PATHS = REAL_SCRIPT_PATHS  # Use the actual script paths for consistency

    def __init__(self):
        # This will store mock button objects, keyed by script_key
        self._mock_buttons = {}
        # Simulate the creation of a few buttons based on actual PICA_v6.py structure
        self._create_mock_buttons()

    def _create_mock_buttons(self):
        """Simulates the button creation logic of PICALauncherApp."""
        for script_key, script_path in self.SCRIPT_PATHS.items():
            button_text = SCRIPT_KEY_TO_BUTTON_TEXT.get(
                script_key, script_key)  # Fallback to key if text not mapped
            # The lambda captures the current script_key at definition time
            command_func = lambda key=script_key: self.launch_script(
                self.SCRIPT_PATHS[key])
            mock_button = MockTkWidget(command=command_func, text=button_text)
            self._mock_buttons[script_key] = mock_button

    def get_button_by_script_key(self, script_key):
        """Returns the mock button associated with a given script key."""
        return self._mock_buttons.get(script_key)

    def launch_script(self, script_path):
        """Mock method for launching script. This method will be patched by the tests."""
        pass


class TestPicaLauncher(unittest.TestCase):
    """
    Tests the main PICA Launcher's button-script launching logic
    using a mock PicaLauncher.
    """

    @patch('tests.test_pica_launcher.MockPicaLauncher.launch_script')
    def test_k2400_iv_button_launches_correct_script(self, mock_launch_script):
        """
        Tests that the 'K2400 I-V' button, when invoked, calls launch_script
        with the correct path.
        """
        launcher = MockPicaLauncher()
        button = launcher.get_button_by_script_key("K2400 I-V")
        self.assertIsNotNone(button, "Button for 'K2400 I-V' not found.")
        button.invoke()
        mock_launch_script.assert_called_once_with(
            REAL_SCRIPT_PATHS["K2400 I-V"])

    @patch('tests.test_pica_launcher.MockPicaLauncher.launch_script')
    def test_lakeshore_temp_control_button_launches_correct_script(
            self, mock_launch_script):
        """
        Tests that the 'Lakeshore Temp Control' button, when invoked,
        calls launch_script with the correct path.
        """
        launcher = MockPicaLauncher()
        button = launcher.get_button_by_script_key("Lakeshore Temp Control")
        self.assertIsNotNone(
            button, "Button for 'Lakeshore Temp Control' not found.")
        button.invoke()
        mock_launch_script.assert_called_once_with(
            REAL_SCRIPT_PATHS["Lakeshore Temp Control"])

    # Add more tests here for other relevant buttons if desired
    # For example, to test that a button for which script_key_to_button_text
    # is not defined still works, or to test other script types.
