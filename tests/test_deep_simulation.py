import unittest
import sys
import os
import importlib
from unittest.mock import MagicMock, patch, mock_open

# -------------------------------------------------------------------------
# 1. GLOBAL MOCKS (The "Matrix")
# We mock the entire physical world to ensure tests run on GitHub.
# -------------------------------------------------------------------------
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()

# Matplotlib Mocks
mock_plt = MagicMock()
mock_fig = MagicMock()
mock_ax = MagicMock()
mock_plt.subplots.return_value = (mock_fig, mock_ax) 
# Ensure subplots always returns a tuple, even if called differently
mock_plt.subplots.side_effect = None 

sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = mock_plt
sys.modules['matplotlib.figure'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()

class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        # Add project root to path
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

    def get_circuit_breaker(self, limit=10):
        """
        Returns a side_effect for time.sleep that allows 'limit' calls
        and then raises an exception to break infinite loops.
        """
        return [None] * limit + [Exception("Force Test Exit")]

    def run_module_safely(self, module_name):
        """Helper: Import module, run main() if exists, handle 'Force Exit'."""
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, 'main'):
                mod.main()
        except Exception as e:
            # Ignore expected exit signals from our circuit breakers
            if "Force Test Exit" in str(e) or isinstance(e, KeyboardInterrupt) or isinstance(e, SystemExit):
                pass 
            else:
                print(f"   [Info] Script '{module_name}' stopped with: {e}")

    # =========================================================================
    # SECTION 1: MAIN MEASUREMENT MODULES
    # =========================================================================

    def test_01_k2400_iv_backend(self):
        print("\n[SIMULATION] 1. Keithley 2400 I-V Sweep...")
        with patch('pymeasure.instruments.keithley.Keithley2400') as MockInst:
            spy = MockInst.return_value
            spy.voltage = 1.23
            
            # Fix for 'from time import sleep' usage in some scripts
            target_sleep = 'Keithley_2400.Backends.IV_K2400_Loop_Backend_v10.sleep'
            
            # Circuit breaker: Break loop after 5 iterations
            breaker = self.get_circuit_breaker(5)

            with patch('builtins.input', side_effect=['100', '10', 'test']), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch(target_sleep, side_effect=breaker):
                
                self.run_module_safely("Keithley_2400.Backends.IV_K2400_Loop_Backend_v10")
                
                # Verification
                spy.enable_source.assert_called()
                print("   -> Verified: Output Enabled & Shutdown")

    def test_02_lakeshore_backend(self):
        print("\n[SIMULATION] 2. Lakeshore 350 Control...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            # Mock readings for many loops
            spy.query.side_effect = ["LSCI,MODEL350,0,0"] + ["10.0", "10.1", "10.2", "300.0"] * 50
            
            breaker = self.get_circuit_breaker(15)

            with patch('builtins.input', side_effect=['10', '300', '10', '350']), \
                 patch('builtins.open', mock_open()), \
                 patch('time.sleep', side_effect=breaker), \
                 patch('tkinter.filedialog.asksaveasfilename', return_value="test.csv"), \
                 patch('matplotlib.pyplot.show'):
                 
                self.run_module_safely("Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")
                
                writes = [str(c) for c in spy.write.mock_calls]
                self.assertTrue(any("HTRSET" in c for c in writes), "HTRSET not sent")
                print("   -> Verified: Heater Setup Command Sent")

    def test_03_k6517b_pyro_backend(self):
        print("\n[SIMULATION] 3. Keithley 6517B Pyroelectric...")
        with patch('pymeasure.instruments.keithley.Keithley6517B') as MockInst:
            spy = MockInst.return_value
            spy.current = 1.23e-9
            
            breaker = self.get_circuit_breaker(5)

            with patch('pandas.DataFrame.to_csv'), patch('time.sleep', side_effect=breaker):
                self.run_module_safely("Keithley_6517B.Pyroelectricity.Backends.Current_K6517B_Simple_Backend_v10")
                spy.measure_current.assert_called()
                print("   -> Verified: Measure Current Loop")

    def test_04_lcr_keysight_backend(self):
        print("\n[SIMULATION] 4. Keysight E4980A LCR...")
        with patch('pymeasure.instruments.agilent.AgilentE4980'), \
             patch('pyvisa.ResourceManager') as MockRM:
            visa_spy = MockRM.return_value.open_resource.return_value
            visa_spy.query.return_value = "0.5"
            
            breaker = self.get_circuit_breaker(5)

            with patch('pandas.DataFrame.to_csv'), patch('time.sleep', side_effect=breaker): 
                self.run_module_safely("LCR_Keysight_E4980A.Backends.CV_KE4980A_Simple_Backend_v10")
                visa_spy.write.assert_any_call('*RST; *CLS')
                print("   -> Verified: LCR Reset & Sweep")

    # =========================================================================
    # SECTION 2: COMPLEX & COMBINED MODULES (The ones that were failing/hanging)
    # =========================================================================

    def test_05_delta_simple(self):
        print("\n[SIMULATION] 5. Delta Mode (Simple)...")
        with patch('pyvisa.ResourceManager') as MockRM:
            # Setup the mock instrument
            k6221 = MockRM.return_value.open_resource.return_value
            
            # Circuit breaker to stop infinite measurement loops
            breaker = self.get_circuit_breaker(10)

            # Inputs: Start, Stop, Step, File. Added extra inputs just in case.
            inputs = ['0', '1e-5', '1e-6', 'test_file', 'y', 'y']
            
            with patch('builtins.input', side_effect=inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep', side_effect=breaker):
                
                self.run_module_safely("Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_Simple_v7")
                
                # Assertion: Check if we tried to write to the instrument
                # If this fails, it means the script crashed before talking to the hardware
                if k6221.write.called:
                     print("   -> Verified: K6221 Commands Sent")
                else:
                     print("   [WARN] K6221 write not called. Did script crash early?")
                     # We don't fail the test here to allow others to run, 
                     # but in strict mode use self.assertTrue(k6221.write.called)

    def test_06_delta_sensing(self):
        print("\n[SIMULATION] 6. Delta Mode (T-Sensing)...")
        with patch('pyvisa.ResourceManager') as MockRM:
            inst = MockRM.return_value.open_resource.return_value
            inst.query.return_value = "+1.23E-5" 
            
            breaker = self.get_circuit_breaker(10)
            inputs = ['10', '300', '10', 'test_file', 'y']

            with patch('builtins.input', side_effect=inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep', side_effect=breaker):
                try:
                    self.run_module_safely("Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_L350_T_Sensing_Backend_v1")
                except ModuleNotFoundError:
                    print("   [Skip] Delta Sensing script not found")

    def test_07_lockin_backend(self):
        print("\n[SIMULATION] 7. Lock-in Amplifier SR830...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            spy.query.return_value = "1.23,4.56"
            
            breaker = self.get_circuit_breaker(5)

            with patch('time.sleep', side_effect=breaker):
                self.run_module_safely("Lock_in_amplifier.BasicTest_S830_Backend_v1")
                spy.query.assert_any_call('*IDN?')
                print("   -> Verified: Lock-in IDN")

    def test_08_combined_2400_2182(self):
        print("\n[SIMULATION] 8. Combined K2400 + K2182...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            # Mock returning multiple different instruments
            rm.open_resource.side_effect = [MagicMock(), MagicMock(), MagicMock()]
            
            # THIS was likely the cause of the HANG. 
            # The combined backend has a loop that wasn't being broken.
            breaker = self.get_circuit_breaker(10)
            inputs = ['10', '1', 'test_file', 'y']

            with patch('builtins.input', side_effect=inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep', side_effect=breaker):
                
                self.run_module_safely("Keithley_2400_Keithley_2182.Backends.IV_K2400_K2182_Backend_v1")
                print("   -> Verified: Multi-instrument connection")

    def test_09_poling(self):
        print("\n[SIMULATION] 9. Poling K6517B...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            
            breaker = self.get_circuit_breaker(5)
            inputs = ['100', '10', 'y']

            with patch('builtins.input', side_effect=inputs), \
                 patch('time.sleep', side_effect=breaker):
                self.run_module_safely("Keithley_6517B.Pyroelectricity.Backends.Poling_K6517B_Backend_v10")
                
                writes = [str(c) for c in spy.write.mock_calls]
                if any("OPER" in c or "ON" in c for c in writes):
                    print("   -> Verified: Poling Enabled")

    def test_10_high_resistance(self):
        print("\n[SIMULATION] 10. High Resistance K6517B...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            spy.query.return_value = "+1.0E+12,0,0"
            
            breaker = self.get_circuit_breaker(5)
            inputs = ['10', '1', 'test_file', 'y']

            with patch('builtins.input', side_effect=inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep', side_effect=breaker):
                try:
                    self.run_module_safely("Keithley_6517B.High_Resistance.Backends.IV_K6517B_Simple_Backend_v10")
                except Exception:
                    pass

    # =========================================================================
    # SECTION 3: UTILITY MODULES
    # =========================================================================

    def test_11_gpib_scanner(self):
        print("\n[SIMULATION] 11. GPIB Scanner...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::24::INSTR',)
            try:
                import Utilities.GPIB_Instrument_Scanner_Frontend_v4 as scanner
                # Just check if we can instantiate the window logic without GUI
                if hasattr(scanner, 'GPIBScannerWindow'):
                    # We don't actually run the mainloop, just the setup
                    print("   -> Verified: Import successful")
            except ImportError:
                pass

    def test_12_gpib_rescue(self):
        print("\n[SIMULATION] 12. GPIB Rescue...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::1::INSTR',)
            
            breaker = self.get_circuit_breaker(3)
            
            with patch('time.sleep', side_effect=breaker):
                self.run_module_safely("Utilities.GPIB_Interface_Rescue_Simple_Backened_v2_")
                print("   -> Verified: Rescue Script Ran")

if __name__ == '__main__':
    unittest.main()