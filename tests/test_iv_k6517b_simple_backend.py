import unittest
from unittest.mock import patch, MagicMock, mock_open

# Import the main function from the script we want to test
from Keithley_6517B.High_Resistance.Backends.IV_K6517B_Simple_Backend_v10 import main as iv_simple_main

class TestIVK6517BSimpleBackend(unittest.TestCase):

    @patch('builtins.input', side_effect=['-10', '10', '5', '0.1', 'test_iv_simple.csv'])
    @patch('pymeasure.instruments.keithley.Keithley6517B')
    @patch('builtins.open', new_callable=mock_open)
    @patch('matplotlib.pyplot.show')
    def test_full_run(self, mock_show, mock_file, mock_keithley_class, mock_input):
        """
        Tests a complete, successful run of the IV_K6517B_Simple_Backend script.
        """
        # --- Setup Mocks ---
        mock_instrument = MagicMock()
        # Set a mock ID for the connection message
        mock_instrument.id = "Mocked Keithley 6517B"
        # Simulate resistance measurement
        mock_instrument.resistance = 1.23e9
        mock_keithley_class.return_value = mock_instrument

        # --- Run the main function ---
        iv_simple_main()

        # --- Assertions ---
        # 1. Was the instrument initialized correctly?
        mock_keithley_class.assert_called_once_with("GPIB1::27::INSTR")

        # 2. Was the zero-check and correction sequence performed?
        mock_instrument.reset.assert_called_once()
        mock_instrument.measure_resistance.assert_called_once()
        mock_instrument.write.assert_any_call(':SYSTem:ZCHeck ON')
        mock_instrument.write.assert_any_call(':SYSTem:ZCORrect:ACQuire')
        mock_instrument.write.assert_any_call(':SYSTem:ZCHeck OFF')
        mock_instrument.write.assert_any_call(':SYSTem:ZCORrect ON')

        # 3. Was the source configured and enabled?
        self.assertEqual(mock_instrument.current_nplc, 1)
        mock_instrument.enable_source.assert_called_once()

        # 4. Was the voltage sweep performed correctly?
        # Based on inputs: start=-10, stop=10, steps=5 -> [-10., -5., 0., 5., 10.]
        self.assertEqual(mock_instrument.source_voltage_set.call_count, 5)
        mock_instrument.source_voltage_set.assert_any_call(-10.0)
        mock_instrument.source_voltage_set.assert_any_call(10.0)

        # 5. Was the data file written to?
        mock_file.assert_called_with('test_iv_simple.csv', 'w', newline='')
        # Header (2) + Data (5)
        self.assertTrue(mock_file().write.call_count >= 7)

        # 6. Was the instrument shut down safely?
        mock_instrument.shutdown.assert_called_once()

        # 7. Was the plot shown?
        mock_show.assert_called_once()