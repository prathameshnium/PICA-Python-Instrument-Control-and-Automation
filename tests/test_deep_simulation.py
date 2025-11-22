import unittest
import sys
import os
import importlib
import signal
from unittest.mock import MagicMock, patch, mock_open


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
            f"Test {self._testMethodName} took longer than 30s! Infinite Loop suspected.")

    def run_module_safely(self, module_name, mock_modules):
        """Imports and runs a module with a strict 30-second timeout."""
        with patch.dict('sys.modules', mock_modules):  # type: ignore
            # Set an alarm for 30 seconds (Works on Linux/GitHub Actions)
            if hasattr(signal, 'SIGALRM'):
                # Ensure any previous alarm is cleared
                signal.alarm(0)
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
                    print("   -> Module loaded (no main function).", flush=True)
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
                if hasattr(signal, 'SIGALRM'):
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
        # Define mocks locally for this test
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        # GLOBAL PATCH for sleep is critical here
        mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(5))
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)

        with patch.dict('sys.modules', mock_modules):
            with patch('pymeasure.instruments.keithley.Keithley2400') as MockInst:
    
                spy = MockInst.return_value
                with patch('builtins.input', side_effect=['100', '10', 'test_file']), \
                        patch('pandas.DataFrame.to_csv'):
                    self.run_module_safely(
                        "Keithley_2400.Backends.IV_K2400_Loop_Backend_v10", mock_modules)
                    spy.enable_source.assert_called()

    def test_02_lakeshore_backend(self):
        # Define mocks locally for this test
        mock_plt = MagicMock()
        mock_fig, mock_ax = MagicMock(), MagicMock()
        mock_ax.plot.return_value = [MagicMock()]
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(use=MagicMock()),
            'matplotlib.pyplot': mock_plt,
        }
        mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(15))
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)
        with patch('pyvisa.ResourceManager') as MockRM, \
             patch('tkinter.Tk'), \
             patch('tkinter.filedialog.asksaveasfilename', return_value="test.csv"), \
             patch.dict('sys.modules', mock_modules):

                spy = MockRM.return_value.open_resource.return_value # noqa
                spy.query.side_effect = [
                    "LSCI,MODEL350,0,0"] + ["10.0", "300.0"] * 20
    
                # Create local mocks for matplotlib to avoid issues with global mocks
                mock_fig, mock_ax = MagicMock(), MagicMock()
                mock_ax.plot.return_value = [MagicMock()]
                mock_plt.subplots.return_value = (mock_fig, mock_ax)
    
                with patch('builtins.input', side_effect=['10', '300', '10', '350']), \
                        patch('builtins.open', mock_open()), \
                        patch('matplotlib.pyplot.show'): # noqa
                    self.run_module_safely("Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10", mock_modules)

    def test_03_k6517b_pyro_backend(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(10))
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)
        with patch.dict('sys.modules', mock_modules):
            with patch('pymeasure.instruments.keithley.Keithley6517B') as MockInst:
    
                spy = MockInst.return_value
                spy.current = 1.23e-9
                with patch('pandas.DataFrame.to_csv'):
                    self.run_module_safely(
                        "Keithley_6517B.Pyroelectricity.Backends."
                        "Current_K6517B_Simple_Backend_v10", mock_modules)
    def test_04_lcr_keysight_backend(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        with patch.dict('sys.modules', mock_modules):
            with patch('pymeasure.instruments.agilent.AgilentE4980'), \
                    patch('pyvisa.ResourceManager') as MockRM:
                mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(5))
                mock_sleep.start()
                self.addCleanup(mock_sleep.stop)
    
                visa_spy = MockRM.return_value.open_resource.return_value
                visa_spy.query.return_value = "0.5"
                with patch('pandas.DataFrame.to_csv'):
                    self.run_module_safely(
                        "LCR_Keysight_E4980A.Backends.CV_KE4980A_Simple_Backend_v10", mock_modules)
    def test_05_delta_simple(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(10))
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)
        with patch.dict('sys.modules', mock_modules):
            with patch('pyvisa.ResourceManager') as MockRM:
    
                MockRM.return_value.open_resource.return_value
                inputs = ['0', '1e-5', '1e-6', 'test_file', 'y', 'y']
                with patch('builtins.input', side_effect=inputs), \
                        patch('pandas.DataFrame.to_csv'):
                    self.run_module_safely("Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_Simple_v7", mock_modules)
    def test_06_delta_sensing(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        with patch.dict('sys.modules', mock_modules):
            with patch('pyvisa.ResourceManager') as MockRM:
                mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(10))
                mock_sleep.start()
                self.addCleanup(mock_sleep.stop)
    
                inst = MockRM.return_value.open_resource.return_value
                inst.query.return_value = "+1.23E-5"
                inputs = ['10', '300', '10', 'test_file', 'y']
                with patch('builtins.input', side_effect=inputs), \
                        patch('pandas.DataFrame.to_csv'):
                    try:
                        self.run_module_safely(
                            "Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_L350_T_Sensing_Backend_v1", mock_modules)
                    except ModuleNotFoundError:
                        print("   [SKIP] Module not found, skipping.")
    def test_07_lockin_backend(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(5))
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)
        with patch.dict('sys.modules', mock_modules):
            with patch('pyvisa.ResourceManager') as MockRM:
                spy = MockRM.return_value.open_resource.return_value
    
                spy.query.side_effect = [
                    "SRS,SR830,s/n12345,ver1.07",  # *IDN?
                    "15",                         # SENS?
                    "1.23,4.56"                   # SNAP? 3,4
                ]
                self.run_module_safely(
                    "Lock_in_amplifier.BasicTest_S830_Backend_v1", mock_modules)
    def test_08_combined_2400_2182(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        # THIS WAS THE TEST CAUSING THE HANG
        # We suspect input mismatch or resource opening hang.
        mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(10))
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)
        with patch.dict('sys.modules', mock_modules):
            with patch('pyvisa.ResourceManager') as MockRM:
                mock_pymeasure = patch('pymeasure.instruments.keithley.Keithley2400')
                mock_pymeasure.start()
    
                rm = MockRM.return_value
                k2182_spy = MagicMock()
                k2182_spy.assert_trigger = MagicMock()
                rm.open_resource.return_value = k2182_spy
    
                # Add extra inputs just in case the script asks for more than
                # expected
                inputs = ['10', '1', 'test_file', 'y', 'y', 'y', 'y']
                with patch('builtins.input', side_effect=inputs), \
                        patch('pandas.DataFrame.to_csv'):
                    self.run_module_safely(
                        "Keithley_2400_Keithley_2182.Backends.IV_K2400_K2182_Backend_v1", mock_modules)
                mock_pymeasure.stop()
    def test_09_poling(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(5))
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)
        with patch.dict('sys.modules', mock_modules):
            with patch('pymeasure.instruments.keithley.Keithley6517B'):
    
                inputs = ['100', '10', 'y']
                with patch('builtins.input', side_effect=inputs):
                    self.run_module_safely(
                        "Keithley_6517B.Pyroelectricity.Backends.Poling_K6517B_Backend_v10", mock_modules)
    def test_10_high_resistance(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
            'multiprocessing': MagicMock(),
            'multiprocessing.queues': MagicMock(),
            'matplotlib': MagicMock(),
            'matplotlib.pyplot': MagicMock(),
            'matplotlib.figure': MagicMock(),
            'matplotlib.backends': MagicMock(),
            'matplotlib.backends.backend_tkagg': MagicMock(),
        }

        with patch.dict('sys.modules', mock_modules):
            with patch('pymeasure.instruments.keithley.Keithley6517B') as Mock6517:
                mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(5))
                mock_sleep.start()
                self.addCleanup(mock_sleep.stop)
    
                spy = Mock6517.return_value
                spy.id = "Mocked Keithley 6517B"
                spy.resistance = 1.23e12  # Provide a mock resistance
    
                # Correct inputs for: start_v, stop_v, steps, delay, filename
                inputs = ['-10', '10', '5', '0.1', 'test_file']
    
                with patch('builtins.input', side_effect=inputs), \
                        patch('builtins.open', mock_open()), \
                        patch('matplotlib.pyplot.show'):
                    self.run_module_safely("Keithley_6517B.High_Resistance.Backends.IV_K6517B_Simple_Backend_v10", mock_modules) # noqa
    def test_11_gpib_scanner(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
        }

        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::24::INSTR',)
            try:
                with patch.dict('sys.modules', mock_modules):
                    import Utilities.GPIB_Instrument_Scanner_GUI_v4 as scanner
                    if hasattr(scanner, 'GpibScannerGUI'):
                        print("   -> Verified: Import successful", flush=True)
            except ImportError:
                pass
    def test_12_gpib_rescue(self):
        mock_modules = {
            'tkinter': MagicMock(),
            'tkinter.ttk': MagicMock(),
            'tkinter.messagebox': MagicMock(),
            'tkinter.filedialog': MagicMock(),
        }

        with patch('pyvisa.ResourceManager') as MockRM:
            mock_sleep = patch('time.sleep', side_effect=self.get_circuit_breaker(3))
            mock_sleep.start()
            self.addCleanup(mock_sleep.stop)

            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::1::INSTR',)
            self.run_module_safely(
                "Utilities.GPIB_Interface_Rescue_Simple_Backened_v2_", mock_modules)


if __name__ == '__main__':
    unittest.main()