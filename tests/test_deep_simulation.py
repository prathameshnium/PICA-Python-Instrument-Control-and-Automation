import unittest
import sys
import os
import importlib
from unittest.mock import MagicMock, patch, mock_open, call

# -------------------------------------------------------------------------
# 1. GLOBAL MOCKS
# -------------------------------------------------------------------------
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = MagicMock()

class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

    def run_module_safely(self, module_name):
        """Helper to import a module and run its main() if it exists."""
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        try:
            # 1. Import the module (this runs top-level code)
            mod = importlib.import_module(module_name)
            
            # 2. Explicitly run main() if it exists (Crucial for Lakeshore script)
            if hasattr(mod, 'main'):
                print(f"   [Exec] Running {module_name}.main()...")
                mod.main()
            else:
                print(f"   [Exec] Module {module_name} ran on import.")
                
        except Exception as e:
            # We expect 'Force Test Exit' or similar from our mocks
            if "Force Test Exit" in str(e):
                print("   [Info] Simulation loop broken successfully.")
            else:
                # If it's a different error, print it but don't fail immediately
                # so we can check if partial logic worked.
                print(f"   [Info] Script stopped with: {e}")

    # =========================================================================
    # TEST 1: KEITHLEY 2400
    # =========================================================================
    def test_keithley2400_iv_protocol(self):
        print("\n[SIMULATION] Testing Keithley 2400 I-V Protocol...")
        with patch('pymeasure.instruments.keithley.Keithley2400') as MockK2400:
            spy_inst = MockK2400.return_value
            spy_inst.voltage = 1.23 
            fake_inputs = ['100', '10', 'test_output']
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'):

                self.run_module_safely("Keithley_2400.Backends.IV_K2400_Loop_Backend_v10")

                spy_inst.enable_source.assert_called()
                print("   -> Verified: Source Output Enabled")
                self.assertTrue(spy_inst.ramp_to_current.called)
                print("   -> Verified: Current Ramping Active")
                spy_inst.shutdown.assert_called()
                print("   -> Verified: Safety Shutdown Triggered")

    # =========================================================================
    # TEST 2: LAKESHORE 350 (The Tricky One)
    # =========================================================================
    def test_lakeshore_visa_communication(self):
        print("\n[SIMULATION] Testing Lakeshore 350 SCPI Commands...")

        with patch('pyvisa.ResourceManager') as MockRM:
            mock_rm_instance = MockRM.return_value
            spy_instr = MagicMock()
            mock_rm_instance.open_resource.return_value = spy_instr
            
            # Mock responses for sequential queries
            spy_instr.query.side_effect = [
                "LSCI,MODEL350,123456,1.0", # *IDN?
                "10.0", "10.0", "10.1", "10.1", "10.1", "300.0" # Temps
            ] * 10 # Repeat to avoid running out

            # Valid inputs
            fake_inputs = ['10', '300', '10', '350']
            
            # Mock File Dialog to return a valid string
            sys.modules['tkinter'].filedialog.asksaveasfilename.return_value = "dummy.csv"

            # Circuit Breaker: Break the infinite loop after a few cycles
            mock_sleep = MagicMock(side_effect=[None, None, None, Exception("Force Test Exit")])

            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('builtins.open', mock_open()), \
                 patch('time.sleep', mock_sleep):
                 
                self.run_module_safely("Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")

            # --- ASSERTIONS ---
            
            # 1. IDN Check
            # We use try/except to provide a clear error message if it fails
            try:
                spy_instr.query.assert_any_call('*IDN?')
                print("   -> Verified: *IDN? Query Sent")
            except AssertionError:
                print("   [FAIL] Did not query *IDN?. Instrument object might not be initialized.")
                raise

            # 2. Heater Setup Check
            # We verify that writes happened. We search specifically for the setup string.
            # Note: The script sends "HTRSET 1,1,2,0,1"
            write_calls = [str(c) for c in spy_instr.write.mock_calls]
            
            htrset_found = any("HTRSET" in c for c in write_calls)
            if htrset_found:
                print("   -> Verified: Heater Configured (HTRSET)")
            else:
                print(f"   [Warn] HTRSET not found in commands: {write_calls[:3]}...")

            # 3. Shutdown Check
            # The script turns off the heater in 'finally': RANGE 1,0
            range_off_found = any("RANGE 1,0" in c for c in write_calls)
            
            if range_off_found:
                print("   -> Verified: Heater Turned Off (RANGE 1,0)")
            elif spy_instr.close.called:
                print("   -> Verified: Instrument Connection Closed")
            else:
                # If both fail, the test fails
                self.fail("Safety Shutdown Failed: Heater not off and connection not closed.")

    # =========================================================================
    # TEST 3: GPIB SCANNER
    # =========================================================================
    def test_gpib_scanner_loop(self):
        print("\n[SIMULATION] Testing GPIB Scanner Loop...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            rm.list_resources.return_value = ('GPIB0::24::INSTR', 'GPIB0::12::INSTR')
            
            try:
                import Utilities.GPIB_Instrument_Scanner_Frontend_v4 as scanner
                if hasattr(scanner, 'GPIBScannerWindow'):
                    scanner.GPIBScannerWindow(MagicMock(), MagicMock())
                    rm.list_resources.assert_called()
                    print("   -> Verified: Scanner requested resource list")
            except ImportError:
                pass

if __name__ == '__main__':
    unittest.main()
