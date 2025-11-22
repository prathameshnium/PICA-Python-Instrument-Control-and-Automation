import unittest
from unittest.mock import patch, MagicMock, mock_open

class TestFixes(unittest.TestCase):

    @patch('Keithley_2400.Backends.IV_K2400_Loop_Backend_v10.sleep', MagicMock())  # Add mock for time.sleep to isolate the test
    @patch('builtins.input', side_effect=['10', '2', 'test_output'])
    @patch('Keithley_2400.Backends.IV_K2400_Loop_Backend_v10.Keithley2400')
    @patch('matplotlib.pyplot.show')
    @patch('pandas.DataFrame.to_csv')
    def test_iv_k2400_fix(self, mock_to_csv, mock_plt_show, mock_keithley_class, mock_input):
        """
        This test verifies that the Keithley2400 is correctly mocked in the
        IV_K2400_Loop_Backend_v10 script, preventing real hardware calls.
        """
        from Keithley_2400.Backends import IV_K2400_Loop_Backend_v10 as iv_backend
        iv_backend.main()
        mock_keithley_class.assert_called_once()

    @patch('tkinter.Tk')
    @patch('tkinter.filedialog.asksaveasfilename', return_value='test.csv')
    @patch('builtins.input', side_effect=['10', '20', '5', '30'])
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.Lakeshore350')
    @patch('matplotlib.pyplot.show')
    @patch('builtins.open', new_callable=mock_open)
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.sleep', MagicMock())
    @patch('time.time', side_effect=[1000, 1002, 1004, 1006, 1008, 1010])
    def test_t_control_l350_fix(self, mock_time, mock_open_file,
                                     mock_plt_show, mock_ls_class, mock_input, mock_file_dialog, mock_tk):
        from Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10 import main
        main()
        mock_ls_class.assert_called_once()

if __name__ == '__main__':
    unittest.main()