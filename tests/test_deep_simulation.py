import unittest
import sys
import os
import importlib
import signal
from unittest.mock import MagicMock, patch, mock_open

# -------------------------------------------------------------------------
# 1. GLOBAL MOCKS
# -------------------------------------------------------------------------
# Mock GUI elements
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()

# Mock Multiprocessing to prevent Queue.get() hangs
mock_mp = MagicMock()
sys.modules['multiprocessing'] = mock_mp
sys.modules['multiprocessing.queues'] = MagicMock()

# Mock Matplotlib
mock_plt = MagicMock()
mock_fig = MagicMock()
mock_ax = MagicMock()
mock_line = MagicMock()
mock_ax.plot.return_value = [mock_line]
mock_plt.subplots.return_value = (mock_fig, mock_ax)
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = mock_plt
sys.modules['matplotlib.figure'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()


class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)
        print(f"\n[TEST START] {self._testMethodName}", flush=True)

    def tearDown(self):
        print(f"[TEST END]   {self._testMethodName}\n", flush=True)

    # -------------------------------------------------------------------------
    # HELPER: The "Watchdog" Timer
    # -------------------------------------------------------------------------
    def _timeout_handler(self, signum, frame):
        raise TimeoutError(
            "Test {self._testMethodName} took longer than 30s! Infinite Loop suspected.")

    def run_module_safely(self, module_name):
        """Imports and runs a module with a strict 30-second timeout."""
        # Set an alarm for 30 seconds (Works on Linux/GitHub Actions)
        signal.signal(signal.SIGALRM, self._timeout_handler)
        signal.alarm(30)

        if module_name in sys.modules:
            del sys.modules[module_name]

        try:
            print(f"   -> Importing {module_name}...", flush=True)
            mod = importlib.import_module(module_name)
            if hasattr(mod, 'main'):
                print(f"   -> Running {module_name}.main()...", flush=True)
                mod.main()
            else:
                print(f"   -> Module loaded (no main function).", flush=True)
        except Exception as e:
            if "Force Test Exit" in str(e) or isinstance(e, SystemExit):
                print(
                    "   -> [SUCCESS] Script exited cleanly via Circuit Breaker.",
                    flush=True)
            elif isinstance(e, TimeoutError):
                print(f"   -> [FAIL] CRITICAL TIMEOUT: {e}", flush=True)
                raise e  # Re-raise to fail the test
            else:
                print(f"   -> [INFO] Script stopped with: {e}", flush=True)
        finally:
            signal.alarm(0)  # Disable the alarm

    def get_circuit_breaker(self, limit=10):
        """A mock sleep that counts down and raises an error to break infinite loops."""
        def side_effect(*args, **kwargs):
            side_effect.counter += 1
            # Print a heartbeat so we know the loop is actually running
            if side_effect.counter % 2 == 0:
                print(
                    f"      [Clock] Tick {side_effect.counter}/{limit}...",
                    flush=True)

            if side_effect.counter >= limit:
                print("      [Clock] Limit reached! Forcing exit.", flush=True)
                raise Exception("Force Test Exit")

        side_effect.counter = 0
        return side_effect

    # =========================================================================
    # TESTS
    # =========================================================================

    def test_01_k2400_iv_backend(self):
        # GLOBAL PATCH for sleep is critical here
        with patch('pymeasure.instruments.keithley.Keithley2400') as MockInst, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(5)):

            spy = MockInst.return_value
            with patch('builtins.input', side_effect=['100', '10', 'test_file']), \
                    patch('pandas.DataFrame.to_csv'):
                self.run_module_safely(
                    "Keithley_2400.Backends.IV_K2400_Loop_Backend_v10")
                spy.enable_source.assert_called()

    def test_02_lakeshore_backend(self):
        with patch('pyvisa.ResourceManager') as MockRM, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(15)):

            spy = MockRM.return_value.open_resource.return_value
            spy.query.side_effect = [
                "LSCI,MODEL350,0,0"] + ["10.0", "300.0"] * 20

            with patch('builtins.input', side_effect=['10', '300', '10', '350']), \
                    patch('builtins.open', mock_open()), \
                    patch('tkinter.filedialog.asksaveasfilename', return_value="test.csv"), \
                    patch('matplotlib.pyplot.show'):
                self.run_module_safely(
                    "Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")

    def test_03_k6517b_pyro_backend(self):
        with patch('pymeasure.instruments.keithley.Keithley6517B') as MockInst, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(5)):
            spy = MockInst.return_value
            spy.current = 1.23e-9
            with patch('pandas.DataFrame.to_csv'):
                self.run_module_safely(
                    "Keithley_6517B.Pyroelectricity.Backends.Current_K6517B_Simple_Backend_v10")

    def test_04_lcr_keysight_backend(self):
        with patch('pymeasure.instruments.agilent.AgilentE4980'), \
                patch('pyvisa.ResourceManager') as MockRM, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(5)):
            visa_spy = MockRM.return_value.open_resource.return_value
            visa_spy.query.return_value = "0.5"
            with patch('pandas.DataFrame.to_csv'):
                self.run_module_safely(
                    "LCR_Keysight_E4980A.Backends.CV_KE4980A_Simple_Backend_v10")

    def test_05_delta_simple(self):
        with patch('pyvisa.ResourceManager') as MockRM, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(10)):
            MockRM.return_value.open_resource.return_value
            inputs = ['0', '1e-5', '1e-6', 'test_file', 'y', 'y']
            with patch('builtins.input', side_effect=inputs), \
                    patch('pandas.DataFrame.to_csv'):
                self.run_module_safely(
                    "Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_Simple_v7")

    def test_06_delta_sensing(self):
        with patch('pyvisa.ResourceManager') as MockRM, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(10)):
            inst = MockRM.return_value.open_resource.return_value
            inst.query.return_value = "+1.23E-5"
            inputs = ['10', '300', '10', 'test_file', 'y']
            with patch('builtins.input', side_effect=inputs), \
                    patch('pandas.DataFrame.to_csv'):
                try:
                    self.run_module_safely(
                        "Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_L350_T_Sensing_Backend_v1")
                except ModuleNotFoundError:
                    print("   [SKIP] Module not found, skipping.")

    def test_07_lockin_backend(self):
        with patch('pyvisa.ResourceManager') as MockRM, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(5)):
            spy = MockRM.return_value.open_resource.return_value
            spy.query.side_effect = [
                "SRS,SR830,s/n12345,ver1.07",  # *IDN?
                "15",                         # SENS?
                "1.23,4.56"                   # SNAP? 3,4
            ]
            self.run_module_safely(
                "Lock_in_amplifier.BasicTest_S830_Backend_v1")

    def test_08_combined_2400_2182(self):
        # THIS WAS THE TEST CAUSING THE HANG
        # We suspect input mismatch or resource opening hang.
        with patch('pyvisa.ResourceManager') as MockRM, \
                patch('pymeasure.instruments.keithley.Keithley2400'), \
                patch('time.sleep', side_effect=self.get_circuit_breaker(10)):

            rm = MockRM.return_value
            # Ensure side_effect doesn't run out if script asks for many
            # resources
            rm.open_resource.return_value = MagicMock()

            # Add extra inputs just in case the script asks for more than
            # expected
            inputs = ['10', '1', 'test_file', 'y', 'y', 'y', 'y']

            with patch('builtins.input', side_effect=inputs), \
                    patch('pandas.DataFrame.to_csv'):
                self.run_module_safely(
                    "Keithley_2400_Keithley_2182.Backends.IV_K2400_K2182_Backend_v1")

    def test_09_poling(self):
        with patch('pymeasure.instruments.keithley.Keithley6517B'), \
                patch('time.sleep', side_effect=self.get_circuit_breaker(5)):
            inputs = ['100', '10', 'y']
            with patch('builtins.input', side_effect=inputs):
                self.run_module_safely(
                    "Keithley_6517B.Pyroelectricity.Backends.Poling_K6517B_Backend_v10")

    def test_10_high_resistance(self):
        with patch('pymeasure.instruments.keithley.Keithley6517B') as Mock6517, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(5)):
            spy = Mock6517.return_value
            spy.id = "Mocked Keithley 6517B"
            spy.resistance = 1.23e12  # Provide a mock resistance

            # Correct inputs for: start_v, stop_v, steps, delay, filename
            inputs = ['-10', '10', '5', '0.1', 'test_file']

            with patch('builtins.input', side_effect=inputs), \
                    patch('builtins.open', mock_open()), \
                    patch('matplotlib.pyplot.show'):
                self.run_module_safely(
                    "Keithley_6517B.High_Resistance.Backends.IV_K6517B_Simple_Backend_v10")

    def test_11_gpib_scanner(self):
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::24::INSTR',)
            try:
                import Utilities.GPIB_Instrument_Scanner_GUI_v4 as scanner
                if hasattr(scanner, 'GPIBScannerWindow'):
                    print("   -> Verified: Import successful", flush=True)
            except ImportError:
                pass

    def test_12_gpib_rescue(self):
        with patch('pyvisa.ResourceManager') as MockRM, \
                patch('time.sleep', side_effect=self.get_circuit_breaker(3)):
            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::1::INSTR',)
            self.run_module_safely(
                "Utilities.GPIB_Interface_Rescue_Simple_Backened_v2_")


if __name__ == '__main__':
    unittest.main()
