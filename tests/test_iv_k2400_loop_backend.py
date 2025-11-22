import unittest
from unittest.mock import patch, MagicMock

import Keithley_2400.Backends.IV_K2400_Loop_Backend_v10 as iv_backend


class TestIVK2400LoopBackend(unittest.TestCase):

    @patch('builtins.input', side_effect=['10', '2', 'test_output'])
    @patch.object(iv_backend, 'Keithley2400')
    @patch('matplotlib.pyplot.show')
    @patch('pandas.DataFrame.to_csv')
    def test_main_full_run(self, mock_to_csv, mock_plt_show, mock_keithley_class, mock_input):
        """
        Test the main function to ensure it runs through the full I-V sweep process.
        """
        # --- MOCK SETUP for Keithley2400 ---
        # Mock the instrument instance
        mock_keithley_instance = MagicMock()
        mock_keithley_class.return_value = mock_keithley_instance

        # Simulate the voltage measurement
        # Let's return a voltage that is proportional to the current
        def voltage_side_effect():
            # A simple linear relationship for testing
            return mock_keithley_instance.source_current * 10

        # We can't directly access the source_current from ramp_to_current,
        # so we will use a side effect to track it
        latest_current = [0]

        def ramp_side_effect(current):
            latest_current[0] = current
            # Also update the mock's internal state
            mock_keithley_instance.source_current = current

        mock_keithley_instance.ramp_to_current.side_effect = ramp_side_effect

        # When .voltage is accessed, return the calculated value
        mock_voltage_property = unittest.mock.PropertyMock(
            side_effect=voltage_side_effect)
        type(mock_keithley_instance).voltage = mock_voltage_property

        # --- EXECUTE SCRIPT ---
        iv_backend.main()

        # --- ASSERTIONS ---
        # 1. Check instrument initialization
        mock_keithley_class.assert_called_once_with("GPIB::4")
        mock_keithley_instance.disable_buffer.assert_called_once()

        # 2. Check instrument configuration
        self.assertEqual(mock_keithley_instance.source_mode, 'current')
        self.assertEqual(mock_keithley_instance.source_current_range, 1e-6)
        self.assertEqual(mock_keithley_instance.compliance_voltage, 210)
        mock_keithley_instance.enable_source.assert_called_once()
        mock_keithley_instance.measure_voltage.assert_called_once()

        # 3. Check the ramping logic
        # Based on input: I_range=10, I_step=2.
        # np.linspace(0, 10, int(10/2) + 1) -> linspace(0, 10, 6)
        # The values will be [0., 2., 4., 6., 8., 10.]
        expected_currents_uA = [0., 2., 4., 6., 8., 10.]
        expected_calls = [unittest.mock.call(
            c * 1e-6) for c in expected_currents_uA]
        mock_keithley_instance.ramp_to_current.assert_has_calls(
            expected_calls)

        # 4. Verify the number of measurements taken
        self.assertEqual(mock_keithley_instance.ramp_to_current.call_count, 6)
        # Voltage is read once per loop
        self.assertEqual(mock_voltage_property.call_count, 6)

        # 5. Check data saving
        self.assertEqual(mock_to_csv.call_count, 1)
        # Check the path construction
        call_args = mock_to_csv.call_args
        save_path = call_args[0][0]
        self.assertIn('test_output.txt', save_path)

        # 6. Check that the instrument was shut down
        mock_keithley_instance.shutdown.assert_called_once()

        # 7. Check that the plot was displayed
        mock_plt_show.assert_called_once()


if __name__ == '__main__':
    unittest.main()
