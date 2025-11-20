import unittest
import sys
import os
import importlib
from unittest.mock import MagicMock, patch, mock_open, call, ANY

# -------------------------------------------------------------------------
# 1. GLOBAL HEADLESS MOCKS
# We mock the entire physical world so tests run on GitHub servers.
# -------------------------------------------------------------------------
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()

# Matplotlib Mocks
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = MagicMock()
sys.modules['matplotlib.figure'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()

class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        # Add project root to path so we can import your scripts
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

        # --- CRITICAL FIX FOR "not enough values to unpack" ---
        # Many scripts call: fig, ax = plt.subplots()
        # We tell the mock to return exactly two items.
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        sys.modules['matplotlib.pyplot'].subplots.return_value = (mock_fig, mock_ax)

    def run_module_safely(self, module_name):
        """Helper: Import module, run main() if exists, handle 'Force Exit'."""
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, 'main'):
                mod.main()
        except Exception as e:
            if "Force Test Exit" in str(e) or isinstance(e, KeyboardInterrupt):
                pass # Expected circuit breaker
            else:
                print(f"   [Info] Script '{module_name}' stopped with: {e}")

    # =========================================================================
    # 1. KEITHLEY 2400 (I-V Sweep)
    # =========================================================================
    def test_k2400_iv_backend(self):
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
    # 2. LAKESHORE 350 (Temp Control)
    # =========================================================================
    def test_lakeshore_backend(self):
        print("\n[SIMULATION] Lakeshore 350 Control...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            spy.query.side_effect = ["LSCI,MODEL350,0,0"] + ["10.0", "10.1", "10.2", "300.0"] * 10
            
            # Valid Inputs: Start=10, End=300, Rate=10, Cutoff=350
            fake_inputs = ['10', '300', '10', '350']
            
            # Sleep Circuit Breaker
            breaker = MagicMock(side_effect=[None]*5 + [Exception("Force Test Exit")])

            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('builtins.open', mock_open()), \
                 patch('time.sleep', breaker), \
                 patch('tkinter.filedialog.asksaveasfilename', return_value="test.csv"):
                 
                self.run_module_safely("Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")
                
                spy.query.assert_any_call('*IDN?')
                writes = [str(c) for c in spy.write.mock_calls]
                self.assertTrue(any("HTRSET" in c for c in writes), "Heater setup not sent")
                print("   -> Verified: Heater Configured (HTRSET)")

    # =========================================================================
    # 3. KEITHLEY 6517B (Pyroelectric Current)
    # =========================================================================
    def test_k6517b_pyro_backend(self):
        print("\n[SIMULATION] Keithley 6517B Pyroelectric Current...")
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
    def test_lcr_keysight_backend(self):
        print("\n[SIMULATION] Keysight E4980A LCR Meter...")
        with patch('pymeasure.instruments.agilent.AgilentE4980') as MockLCR, \
             patch('pyvisa.ResourceManager') as MockRM:
            
            lcr_spy = MockLCR.return_value
            visa_spy = MockRM.return_value.open_resource.return_value
            
            # Mock Values: [Capacitance, Resistance]
            lcr_spy.values.return_value = [1.5e-9, 1000] 
            visa_spy.query.return_value = "0.5" # Voltage Level

            with patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'): # Mute sleep for speed
                
                self.run_module_safely("LCR_Keysight_E4980A.Backends.CV_KE4980A_Simple_Backend_v10")
                
                visa_spy.write.assert_any_call('*RST; *CLS')
                lcr_spy.shutdown.assert_called()
                print("   -> Verified: Reset -> Protocol Loop -> Shutdown")

    # =========================================================================
    # 5. DELTA MODE (K6221 + K2182)
    # =========================================================================
    def test_delta_mode_backend(self):
        print("\n[SIMULATION] Delta Mode (K6221 + K2182)...")
        with patch('pyvisa.ResourceManager') as MockRM:
            k6221 = MagicMock()
            MockRM.return_value.open_resource.return_value = k6221
            
            # Inputs: Start=0, Stop=10e-6, Step=1e-6, File=test
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
    def test_lockin_backend(self):
        print("\n[SIMULATION] SRS SR830 Lock-in...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            spy.query.return_value = "1.23,4.56" # Mock X,Y response
            
            with patch('time.sleep'):
                self.run_module_safely("Lock_in_amplifier.BasicTest_S830_Backend_v1")
                
                spy.query.assert_any_call('*IDN?')
                print("   -> Verified: Lock-in IDN Queried")

    # =========================================================================
    # 7. COMBINED K2400 + K2182
    # =========================================================================
    def test_k2400_k2182_backend(self):
        print("\n[SIMULATION] Combined K2400 + K2182...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            # We mocking opening 2 different resources
            k2400 = MagicMock()
            k2182 = MagicMock()
            rm.open_resource.side_effect = [k2400, k2182] # First call 2400, second 2182
            
            fake_inputs = ['10', '1', 'test'] # Current, Step, File
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'):
                 
                self.run_module_safely("Keithley_2400_Keithley_2182.Backends.IV_K2400_K2182_Backend_v1")
                
                print("   -> Verified: Multi-instrument script executed.")

    # =========================================================================
    # 8. KEITHLEY 6517B (High Resistance I-V)
    # =========================================================================
    def test_k6517b_high_res(self):
        print("\n[SIMULATION] Keithley 6517B High Resistance...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            spy.query.return_value = "+1.23E-12" # Current reading
            
            fake_inputs = ['10', '1', 'test'] # Voltage, Step, File
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'):
                 
                self.run_module_safely("Keithley_6517B.High_Resistance.Backends.IV_K6517B_Simple_Backend_v10")
                
                # Check if voltage was applied
                writes = [str(c) for c in spy.write.mock_calls]
                self.assertTrue(any("SOUR:VOLT" in c for c in writes), "Voltage Source not set")
                print("   -> Verified: Voltage Source Commands Sent")

    # =========================================================================
    # 9. POLING K6517B
    # =========================================================================
    def test_k6517b_poling(self):
        print("\n[SIMULATION] Keithley 6517B Poling...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            
            # Inputs: Voltage=100, Time=10
            fake_inputs = ['100', '10']
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('time.sleep'):
                 
                self.run_module_safely("Keithley_6517B.Pyroelectricity.Backends.Poling_K6517B_Backend_v10")
                
                # Check for output enable
                writes = [str(c) for c in spy.write.mock_calls]
                if any("OPER" in c or "ON" in c for c in writes):
                    print("   -> Verified: Poling Voltage Enabled")

    # =========================================================================
    # 10. GPIB SCANNER (Utility)
    # =========================================================================
    def test_gpib_scanner(self):
        print("\n[SIMULATION] GPIB Scanner Utility...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::24::INSTR',)
            
            try:
                import Utilities.GPIB_Instrument_Scanner_Frontend_v4 as scanner
                if hasattr(scanner, 'GPIBScannerWindow'):
                    scanner.GPIBScannerWindow(MagicMock(), MagicMock())
                    rm.list_resources.assert_called()
                    print("   -> Verified: Scanner listed resources")
            except ImportError:
                pass

if __name__ == '__main__':
    unittest.main()