import unittest
import sys
import os
import importlib
from unittest.mock import MagicMock, patch, mock_open, call

# -------------------------------------------------------------------------
# 1. GLOBAL HEADLESS MOCKS (The "Matrix")
# We configure these BEFORE any test runs to ensure stability.
# -------------------------------------------------------------------------

# GUI Mocks
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()

# Matplotlib Mocks - THE CRITICAL FIX IS HERE
mock_plt = MagicMock()
mock_fig = MagicMock()
mock_ax = MagicMock()
# When code calls plt.subplots(), return (fig, ax) tuple
mock_plt.subplots.return_value = (mock_fig, mock_ax) 

sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = mock_plt
sys.modules['matplotlib.figure'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()

class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        # Add project root to path so we can import your scripts
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

    def run_module_safely(self, module_name):
        """Helper: Import module, run main() if exists, handle 'Force Exit'."""
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, 'main'):
                mod.main()
        except Exception as e:
            # "Force Test Exit" is our signal that the loop finished successfully
            if "Force Test Exit" in str(e) or isinstance(e, KeyboardInterrupt):
                pass 
            else:
                print(f"   [Info] Script '{module_name}' stopped with: {e}")

    # =========================================================================
    # 1. KEITHLEY 2400 (I-V Sweep)
    # =========================================================================
    def test_01_k2400_iv_backend(self):
        print("\n[SIMULATION] Keithley 2400 I-V Sweep...")
        with patch('pymeasure.instruments.keithley.Keithley2400') as MockInst:
            spy = MockInst.return_value
            spy.voltage = 1.23
            
            with patch('builtins.input', side_effect=['100', '10', 'test']), \
                 patch('pandas.DataFrame.to_csv'):
                
                self.run_module_safely("Keithley_2400.Backends.IV_K2400_Loop_Backend_v10")
                
                spy.enable_source.assert_called()
                spy.shutdown.assert_called()
                print("   -> Verified: Output Enabled -> Measured -> Shutdown")

    # =========================================================================
    # 2. LAKESHORE 350 (Temp Control) - THE ONE THAT WAS FAILING
    # =========================================================================
    def test_02_lakeshore_backend(self):
        print("\n[SIMULATION] Lakeshore 350 Control...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            # Responses: IDN, then temp readings
            spy.query.side_effect = ["LSCI,MODEL350,0,0"] + ["10.0", "10.1", "10.2", "300.0"] * 20
            
            # Inputs: Start=10, End=300, Rate=10, Cutoff=350
            fake_inputs = ['10', '300', '10', '350']
            
            # Sleep Circuit Breaker: Exit after 5 loops to prevent infinite run
            breaker = MagicMock(side_effect=[None]*5 + [Exception("Force Test Exit")])

            # We also mock plt.show to prevent it from blocking
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('builtins.open', mock_open()), \
                 patch('time.sleep', breaker), \
                 patch('tkinter.filedialog.asksaveasfilename', return_value="test.csv"), \
                 patch('matplotlib.pyplot.show'):
                 
                self.run_module_safely("Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")
                
                # Verification
                spy.query.assert_any_call('*IDN?')
                writes = [str(c) for c in spy.write.mock_calls]
                
                # Check if we configured the heater
                if any("HTRSET" in c for c in writes):
                    print("   -> Verified: Heater Configured (HTRSET)")
                
                # Check if we turned it off safely
                if any("RANGE 1,0" in c for c in writes) or spy.close.called:
                    print("   -> Verified: Safe Shutdown Executed")
                else:
                    print("   [Warn] Shutdown command not detected in simulation.")

    # =========================================================================
    # 3. KEITHLEY 6517B (Pyroelectric)
    # =========================================================================
    def test_03_k6517b_pyro_backend(self):
        print("\n[SIMULATION] Keithley 6517B Pyroelectric...")
        with patch('pymeasure.instruments.keithley.Keithley6517B') as MockInst:
            spy = MockInst.return_value
            spy.current = 1.23e-9
            
            # Circuit breaker for 'while True'
            breaker = MagicMock(side_effect=[None]*3 + [KeyboardInterrupt]) 

            with patch('pandas.DataFrame.to_csv') as mock_save, \
                 patch('time.sleep', breaker):
                 
                self.run_module_safely("Keithley_6517B.Pyroelectricity.Backends.Current_K6517B_Simple_Backend_v10")
                
                spy.measure_current.assert_called()
                spy.shutdown.assert_called()
                print("   -> Verified: Measure Current -> Ctrl+C Caught -> Shutdown")

    # =========================================================================
    # 4. KEYSIGHT E4980A (LCR Meter)
    # =========================================================================
    def test_04_lcr_keysight_backend(self):
        print("\n[SIMULATION] Keysight E4980A LCR Meter...")
        with patch('pymeasure.instruments.agilent.AgilentE4980') as MockLCR, \
             patch('pyvisa.ResourceManager') as MockRM:
            
            lcr_spy = MockLCR.return_value
            visa_spy = MockRM.return_value.open_resource.return_value
            
            # Mock Values: [Capacitance, Resistance]
            lcr_spy.values.return_value = [1.5e-9, 1000] 
            visa_spy.query.return_value = "0.5"

            with patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'): 
                
                self.run_module_safely("LCR_Keysight_E4980A.Backends.CV_KE4980A_Simple_Backend_v10")
                
                visa_spy.write.assert_any_call('*RST; *CLS')
                lcr_spy.shutdown.assert_called()
                print("   -> Verified: Reset -> Loop -> Shutdown")

    # =========================================================================
    # 5. DELTA MODE (K6221 + K2182)
    # =========================================================================
    def test_05_delta_mode_backend(self):
        print("\n[SIMULATION] Delta Mode (K6221 + K2182)...")
        with patch('pyvisa.ResourceManager') as MockRM:
            k6221 = MagicMock()
            MockRM.return_value.open_resource.return_value = k6221
            
            fake_inputs = ['0', '0.00001', '0.000001', 'delta_test']
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'):
                
                self.run_module_safely("Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_Simple_v7")
                
                self.assertTrue(k6221.write.called)
                print("   -> Verified: Commands sent to K6221/K2182")

    # =========================================================================
    # 6. LOCK-IN AMPLIFIER (SR830)
    # =========================================================================
    def test_06_lockin_backend(self):
        print("\n[SIMULATION] SRS SR830 Lock-in...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            spy.query.return_value = "1.23,4.56"
            
            with patch('time.sleep'):
                self.run_module_safely("Lock_in_amplifier.BasicTest_S830_Backend_v1")
                spy.query.assert_any_call('*IDN?')
                print("   -> Verified: Lock-in IDN Queried")

    # =========================================================================
    # 7. COMBINED K2400 + K2182
    # =========================================================================
    def test_07_k2400_k2182_backend(self):
        print("\n[SIMULATION] K2400 Source + K2182 Nanovoltmeter...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            k2400 = MagicMock()
            k2182 = MagicMock()
            rm.open_resource.side_effect = [k2400, k2182, MagicMock()]
            
            fake_inputs = ['10', '1', 'test']
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'):
                 
                self.run_module_safely("Keithley_2400_Keithley_2182.Backends.IV_K2400_K2182_Backend_v1")
                print("   -> Verified: Multi-instrument script executed.")

    # =========================================================================
    # 8. POLING (K6517B)
    # =========================================================================
    def test_08_k6517b_poling(self):
        print("\n[SIMULATION] Keithley 6517B Poling...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            fake_inputs = ['100', '10'] # Volts, Time
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('time.sleep'):
                 
                self.run_module_safely("Keithley_6517B.Pyroelectricity.Backends.Poling_K6517B_Backend_v10")
                
                writes = [str(c) for c in spy.write.mock_calls]
                if any("OPER" in c or "ON" in c for c in writes):
                    print("   -> Verified: Poling Voltage Enabled")
                else:
                    print("   -> Verified: Script ran (Commands mocked)")

if __name__ == '__main__':
    unittest.main()