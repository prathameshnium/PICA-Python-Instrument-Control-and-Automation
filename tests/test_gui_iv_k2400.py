import unittest
from unittest.mock import patch, MagicMock
import tkinter as tk

# Import the GUI class we want to test
from Keithley_2400.IV_K2400_GUI_v5 import MeasurementAppGUI


class TestIVK2400GUI(unittest.TestCase):

    def setUp(self):
        """
        Set up a root Tk window and instantiate the GUI class.
        This runs before each test.
        """
        # We need a root window for the GUI to be instantiated, but we don't need to see it.
        self.root = tk.Tk()
        self.root.withdraw()  # Hide the window

    def tearDown(self):
        """
        Destroy the root window after each test.
        """
        self.root.destroy()

    @patch('Keithley_2400.IV_K2400_GUI_v5.Keithley2400_IV_Backend')
    @patch('matplotlib.figure.Figure.subplots')
    def test_start_measurement_logic(self, mock_fig_subplots, MockBackend):
        """
        Tests the core logic of the 'Start' button click.
        Verifies that parameters are read from the UI and passed to the backend correctly.
        """
        # --- Setup ---
        # Configure the mock for subplots to return two mock axes
        mock_ax_vi = MagicMock()
        mock_ax_ri = MagicMock()
        mock_fig_subplots.return_value = (mock_ax_vi, mock_ax_ri)
        # Instantiate the GUI. This also creates all the tk widgets.
        app = MeasurementAppGUI(self.root)

        # Mock the backend instance that the GUI will create
        mock_backend_instance = MockBackend.return_value
        # --- Simulate User Input ---
        # We directly set the values that would be entered into the GUI's Entry widgets.
        app.entries["Sample Name"].insert(0, "TestSample")
        app.entries["Max Current"].insert(0, "100")  # 100 µA
        app.entries["Step Current"].insert(0, "10")  # 10 µA
        app.entries["Compliance"].insert(0, "20")    # 20 V
        app.entries["Delay"].insert(0, "0.5")    # 0.5 s
        app.keithley_combobox.set("GPIB0::24::INSTR")
        app.file_location_path = "/fake/path"  # Simulate browsing for a file

        # --- Trigger the Action ---
        # Call the method that the "Start" button is connected to.
        app.start_measurement()

        # --- Assertions ---
        # 1. Did the GUI try to connect and configure the backend?
        mock_backend_instance.connect_and_configure.assert_called_once()

        # 2. Were the parameters from the UI passed correctly to the backend?
        # The `connect_and_configure` method is called with (visa_address, params_dict)
        call_args, _ = mock_backend_instance.connect_and_configure.call_args
        passed_params = call_args[1]

        self.assertEqual(passed_params['compliance_v'], 20.0)
        # Max current is converted from µA to A
        self.assertAlmostEqual(passed_params['max_current'], 100e-6)

        # 3. Did the GUI generate the correct sweep points?
        mock_backend_instance.generate_sweep_points.assert_called_once()
        