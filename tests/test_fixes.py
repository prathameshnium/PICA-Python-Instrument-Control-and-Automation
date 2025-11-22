import unittest
from unittest.mock import patch, MagicMock, mock_open
import pyvisa

class TestFixes(unittest.TestCase):

    @patch('Keithley_2400.Backends.IV_K2400_Loop_Backend_v10.sleep', MagicMock())
    @patch('builtins.input', side_effect=['10', '2', 'test_output'])
    @patch('Keithley_2400.Backends.IV_K2400_Loop_Backend_v10.Keithley2400')
    @patch('matplotlib.pyplot.show')
    @patch('pandas.DataFrame.to_csv')
    def test_iv_k2400_fix(self, mock_to_csv, mock_plt_show, mock_keithley_class, mock_input): # type: ignore
        """
        This test verifies that the Keithley2400 is correctly mocked in the
        IV_K2400_Loop_Backend_v10 script, preventing real hardware calls.
        """
        from Keithley_2400.Backends import IV_K2400_Loop_Backend_v10 as iv_backend

        mock_keithley_instance = MagicMock()
        mock_keithley_class.return_value = mock_keithley_instance
        mock_keithley_instance.query.return_value = "KEITHLEY INSTRUMENTS INC., MODEL 2400"

        iv_backend.main()
        mock_keithley_class.assert_called_once_with("GPIB::4")


    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.Lakeshore350')
    def test_t_control_l350_fix(self, mock_ls_class):
        from Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10 import main

        with patch('builtins.input', side_effect=['10', '20', '5', '30']), \
             patch('tkinter.filedialog.asksaveasfilename', return_value='test.csv'), \
             patch('matplotlib.pyplot.show'), \
             patch('builtins.open', mock_open()):
            main()
            mock_ls_class.assert_called_once_with("GPIB0::13::INSTR")