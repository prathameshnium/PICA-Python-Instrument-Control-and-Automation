import unittest
from unittest.mock import patch, MagicMock, mock_open
import pyvisa

import Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10 as ls_backend


class TestLakeshore350Class(unittest.TestCase):
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.pyvisa.ResourceManager')
    def setUp(self, mock_rm):
        # Mock the entire pyvisa resource manager and the instrument it returns
        self.mock_instrument = MagicMock()
        mock_rm.return_value.open_resource.return_value = self.mock_instrument
        self.mock_instrument.query.return_value = "LSCI,MODEL350,12345,1.0"

        # Instantiate the class, which will now use the mock instrument
        self.controller = ls_backend.Lakeshore350("GPIB0::13::INSTR")
        # Keep a reference to the mock for assertions
        self.controller.instrument = self.mock_instrument

    def test_initialization_success(self):
        # Test that the instrument is initialized and queried
        self.mock_instrument.query.assert_called_with('*IDN?')
        self.assertIsNotNone(self.controller.instrument)

    @patch('pyvisa.ResourceManager')
    def test_initialization_failure(self, mock_rm):
        # Test that a connection error is raised if pyvisa fails
        mock_rm.return_value.open_resource.side_effect = pyvisa.errors.VisaIOError(
            pyvisa.constants.VI_ERROR_RSRC_NFOUND)
        with self.assertRaises(ConnectionError):
            ls_backend.Lakeshore350("GPIB0::13::INSTR")

    def test_reset_and_clear(self):
        with patch('time.sleep') as mock_sleep:
            self.controller.reset_and_clear()
            self.mock_instrument.write.assert_any_call('*RST')
            self.mock_instrument.write.assert_any_call('*CLS')
            self.assertEqual(mock_sleep.call_count, 2)

    def test_setup_heater(self):
        self.controller.setup_heater(1, 1, 2)
        self.mock_instrument.write.assert_called_with('HTRSET 1,1,2,0,1')

    def test_setup_ramp(self):
        self.controller.setup_ramp(1, 10.0, ramp_on=True)
        self.mock_instrument.write.assert_called_with('RAMP 1,1,10.0')

    def test_set_setpoint(self):
        self.controller.set_setpoint(1, 150.0)
        self.mock_instrument.write.assert_called_with('SETP 1,150.0')

    def test_set_heater_range(self):
        self.controller.set_heater_range(1, 'high')
        self.mock_instrument.write.assert_called_with('RANGE 1,5')

    def test_get_temperature(self):
        self.mock_instrument.query.return_value = "300.123"
        temp = self.controller.get_temperature('A')
        self.mock_instrument.query.assert_called_with('KRDG? A')
        self.assertAlmostEqual(temp, 300.123)

    def test_get_heater_output(self):
        self.mock_instrument.query.return_value = "50.5"
        output = self.controller.get_heater_output(1)
        self.mock_instrument.query.assert_called_with('HTR? 1')
        self.assertAlmostEqual(output, 50.5)

    def test_close(self):
        self.controller.close()
        # Checks that heater is turned off
        self.mock_instrument.write.assert_called_with('RANGE 1,0')
        # Checks that the instrument connection is closed
        self.mock_instrument.close.assert_called_once()
        self.assertIsNone(self.controller.instrument)


class TestMainFunctionAndUserInput(unittest.TestCase):
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.pyvisa.ResourceManager')
    def setUp(self, mock_rm_manager):
        self.mock_controller = MagicMock()
        mock_rm_manager.return_value.open_resource.return_value = self.mock_controller
        # Simulate temperature readings to control the loop
        # Start at 10K, then ramp up to 21K to finish
        self.mock_controller.get_temperature.side_effect = [
            10.0, 10.0, 10.0, 15.0, 21.0]
        self.mock_controller.get_heater_output.return_value = 25.0

    @patch('builtins.input', side_effect=['100', '200', '10', '300',
                                          'not-a-number', '50', '350', '10', '400'])
    def test_get_user_parameters(self, mock_input):
        # First call: Valid input
        start, end, rate, cutoff = ls_backend.get_user_parameters()
        self.assertEqual((start, end, cutoff), (100, 200, 300))
        self.assertEqual(rate, 10)

        # Second call: Invalid text input, should retry and get the next valid ones
        start, end, rate, cutoff = ls_backend.get_user_parameters()
        self.assertEqual((start, end, cutoff), (50, 350, 400))
        self.assertEqual(rate, 10)

    @patch('tkinter.filedialog.asksaveasfilename', return_value='test.csv')
    @patch('builtins.input', side_effect=['10', '20', '5', '30'])
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.pyvisa.ResourceManager')
    @patch('matplotlib.pyplot.show')
    @patch('builtins.open', new_callable=mock_open)
    @patch('time.sleep', MagicMock())
    @patch('time.time', side_effect=[1000, 1002, 1004, 1006, 1008, 1010])
    @patch('matplotlib.pyplot.subplots')
    def test_main_runs_and_completes(self, mock_subplots, mock_time, mock_open_file, mock_plt_show,
                                     mock_rm_manager, mock_input, mock_file_dialog):
        # --- Configure mock_subplots ---
        mock_ax1 = MagicMock()
        mock_ax2 = MagicMock()
        mock_subplots.return_value = (mock_ax1, mock_ax2)
        mock_ax1.plot.return_value = [MagicMock()]
        mock_ax2.plot.return_value = [MagicMock()]

        # Configure mock_rm_manager to return a mock instrument
        mock_instrument = MagicMock()
        mock_rm_manager.return_value.open_resource.return_value = mock_instrument

        # --- RUN ---
        ls_backend.main()

        # --- ASSERTIONS ---
        # Check initialization
        mock_instrument.reset_and_clear.assert_called_once()
        mock_instrument.setup_heater.assert_called_once()

        # Check stabilization loop
        mock_instrument.set_setpoint.assert_any_call(1, 10)  # Set to start temp

        # Check main ramp loop
        mock_instrument.set_setpoint.assert_any_call(1, 20)  # Set to end temp
        self.assertTrue(mock_instrument.get_temperature.call_count >= 3)

        # Check file writing
        mock_open_file.assert_called_with('test.csv', 'a', newline='')
        handle = mock_open_file()
        # Header + 3 data rows
        self.assertEqual(handle.write.call_count, 1)  # only one writer created

        # Check shutdown
        mock_instrument.close.assert_called_once()
        mock_plt_show.assert_called_once()


if __name__ == '__main__':
    unittest.main()
