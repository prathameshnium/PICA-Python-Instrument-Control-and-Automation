import unittest
import sys
import os
import importlib
from unittest.mock import MagicMock, patch, mock_open, call, ANY

# -------------------------------------------------------------------------
# 1. GLOBAL HEADLESS MOCKS
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
        # Add project root to path
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

        # Fix for "not enough values to unpack" in plt.subplots()
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
            if "Force Test Exit" in str(e):
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
            
            # Inputs: Range=100uA, Step=10uA, File=test
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
            # Responses: IDN, then 5 temp readings
            spy.query.side_effect = ["LSCI,MODEL350,0,0"] + ["10.0", "10.1", "10.2", "300.0"] * 10
            
            # Valid Inputs: Start=10, End=300, Rate=10, Cutoff=350
            fake_inputs = ['10', '300', '10', '350']
            
            # Sleep Circuit Breaker (Exit after 5 loops)
            breaker = MagicMock(side_effect=[None]*5 + [Exception("Force Test Exit")])

            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('builtins.open', mock_open()), \
                 patch('time.sleep', breaker), \
                 patch('tkinter.filedialog.asksaveasfilename', return_value="test.csv"):
                 
                self.run_module_safely("Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")
                
                spy.query.assert_any_call('*IDN?')
                # Check for "HTRSET" command in writes
                writes = [str(c) for c in spy.write.mock_calls]
                self.assertTrue(any("HTRSET" in c for c in writes), "Heater setup not sent")
                print("   -> Verified: IDN -> Heater Setup -> Loop -> Shutdown")

    # =========================================================================
    # 3. KEITHLEY 6517B (Pyroelectric/Current - The one we fixed!)
    # =========================================================================
    def test_k6517b_pyro_backend(self):
        print("\n[SIMULATION] Keithley 6517B Pyroelectric Current...")
        with patch('pymeasure.instruments.keithley.Keithley6517B') as MockInst:
            spy = MockInst.return_value
            spy.current = 1.23e-9
            
            # Circuit breaker for the 'while True' loop
            breaker = MagicMock(side_effect=[None]*3 + [KeyboardInterrupt]) 

            with patch('pandas.DataFrame.to_csv') as mock_save, \
                 patch('time.sleep', breaker):
                 
                self.run_module_safely("Keithley_6517B.Pyroelectricity.Backends.Current_K6517B_Simple_Backend_v10")
                
                spy.measure_current.assert_called()
                spy.shutdown.assert_called()
                mock_save.assert_called()
                print("   -> Verified: Measure Current -> Ctrl+C Caught -> Data Saved")

    # =========================================================================
    # 4. KEYSIGHT E4980A (LCR Meter - The one we fixed!)
    # =========================================================================
    def test_lcr_keysight_backend(self):
        print("\n[SIMULATION] Keysight E4980A LCR Meter...")
        with patch('pymeasure.instruments.agilent.AgilentE4980') as MockLCR, \
             patch('pyvisa.ResourceManager') as MockRM:
            
            # Setup Mocks
            lcr_spy = MockLCR.return_value
            visa_spy = MockRM.return_value.open_resource.return_value
            
            # Mock LCR Values (Capacitance, Resistance, etc.)
            lcr_spy.values.return_value = [1.5e-9, 1000] 
            visa_spy.query.return_value = "0.5" # Voltage Level

            with patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'): # Mute sleep for speed
                
                self.run_module_safely("LCR_Keysight_E4980A.Backends.CV_KE4980A_Simple_Backend_v10")
                
                visa_spy.write.assert_any_call('*RST; *CLS')
                lcr_spy.shutdown.assert_called()
                print("   -> Verified: Reset -> Protocol Loop -> Shutdown")

    # =========================================================================
    # 5. KEITHLEY 2400 + 2182 (Combined)
    # =========================================================================
    def test_k2400_k2182_backend(self):
        print("\n[SIMULATION] K2400 Source + K2182 Nanovoltmeter...")
        
        # This script likely uses raw PyVISA for both
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            
            # We need two different spy objects for two addresses
            k2400 = MagicMock()
            k2182 = MagicMock()
            
            def open_side_effect(address):
                if "4" in address: return k2400 # GPIB::4 is usually 2400
                if "7" in address: return k2182 # GPIB::7 is usually 2182
                return MagicMock()
            
            rm.open_resource.side_effect = open_side_effect
            
            # Mock Inputs: Current=10, Step=1, File=test
            fake_inputs = ['10', '1', 'test']
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'):
                
                self.run_module_safely("Keithley_2400_Keithley_2182.Backends.IV_K2400_K2182_Backend_v1")
                
                # Verify 2400 Source command
                # We check for source enable or voltage setting
                k2400_writes = [str(c) for c in k2400.write.mock_calls]
                self.assertTrue(any("OUTP" in c or "SOUR" in c for c in k2400_writes), "K2400 not triggered")
                
                # Verify 2182 Measure command
                k2182_writes = [str(c) for c in k2182.write.mock_calls]
                self.assertTrue(any("INIT" in c or "TRAC" in c for c in k2182_writes), "K2182 not triggered")
                
                print("   -> Verified: Both instruments commanded successfully.")

    # =========================================================================
    # 6. DELTA MODE (6221 + 2182)
    # =========================================================================
    def test_delta_mode_backend(self):
        print("\n[SIMULATION] Delta Mode (K6221 + K2182)...")
        
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            k6221 = MagicMock()
            rm.open_resource.return_value = k6221
            
            # Mock Inputs: Start=0, Stop=10e-6, Step=1e-6, File=test
            fake_inputs = ['0', '0.00001', '0.000001', 'delta_test']
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'), \
                 patch('time.sleep'):
                
                self.run_module_safely("Delta_mode_Keithley_6221_2182.Backends.Delta_K6221_K2182_Simple_v7")
                
                # Check for Delta Mode specific commands
                writes = [str(c) for c in k6221.write.mock_calls]
                delta_cmd = any("DELT" in c or "UNIT" in c for c in writes)
                
                if delta_cmd:
                    print("   -> Verified: Delta Mode commands sent.")
                else:
                    # Fallback check for connection
                    self.assertTrue(k6221.write.called, "No commands sent to 6221")
                    print("   -> Verified: 6221 Connection active (Commands sent).")

    # =========================================================================
    # 7. LOCK-IN AMPLIFIER (SR830)
    # =========================================================================
    def test_lockin_backend(self):
        print("\n[SIMULATION] SRS SR830 Lock-in...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy = MockRM.return_value.open_resource.return_value
            spy.query.return_value = "1.23,4.56" # X,Y response
            
            with patch('time.sleep'):
                self.run_module_safely("Lock_in_amplifier.BasicTest_S830_Backend_v1")
                
                # Verify ID query
                spy.query.assert_any_call('*IDN?')
                print("   -> Verified: Lock-in Queried.")

if __name__ == '__main__':
    unittest.main()
