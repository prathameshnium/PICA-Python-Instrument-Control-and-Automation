import unittest
from unittest.mock import patch, MagicMock, mock_open
import pyvisa # Added import for pyvisa

class TestFixes(unittest.TestCase):

    @patch('Keithley_2400.Backends.IV_K2400_Loop_Backend_v10.sleep', MagicMock())
    @patch('builtins.input', side_effect=['10', '2', 'test_output'])
    @patch('pyvisa.ResourceManager') # Patch pyvisa.ResourceManager
    @patch('matplotlib.pyplot.show')
    @patch('pandas.DataFrame.to_csv')
    def test_iv_k2400_fix(self, mock_to_csv, mock_plt_show, mock_rm, mock_input):
        """
        This test verifies that the Keithley2400 is correctly mocked in the
        IV_K2400_Loop_Backend_v10 script, preventing real hardware calls.
        """
        from Keithley_2400.Backends import IV_K2400_Loop_Backend_v10 as iv_backend

        mock_keithley_instance = MagicMock()
        mock_rm.return_value.open_resource.return_value = mock_keithley_instance
        mock_keithley_instance.query.return_value = "KEITHLEY INSTRUMENTS INC., MODEL 2400" # Simulate IDN query

        iv_backend.main()
        mock_rm.return_value.open_resource.assert_called_once_with("GPIB::4")


    @patch('tkinter.Tk')
    @patch('tkinter.filedialog.asksaveasfilename', return_value='test.csv')
    @patch('builtins.input', side_effect=['10', '20', '5', '30'])
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.pyvisa.ResourceManager') # Patch pyvisa.ResourceManager
    @patch('matplotlib.pyplot.show')
    @patch('builtins.open', new_callable=mock_open)
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.time.sleep', MagicMock())
    @patch('time.time', side_effect=[1000, 1002, 1004, 1006, 1008, 1010])
    def test_t_control_l350_fix(self, mock_time, mock_open_file, mock_plt_show, mock_rm, mock_input, mock_file_dialog, mock_tk):
        from Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10 import main

        mock_instrument_from_rm = MagicMock()
        mock_rm.return_value.open_resource.return_value = mock_instrument_from_rm
        mock_instrument_from_rm.query.return_value = "LSCI,MODEL350,12345,1.0"

        main()
        mock_rm.return_value.open_resource.assert_called_once_with("GPIB0::13::INSTR")