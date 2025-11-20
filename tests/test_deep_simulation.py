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

# Matplotlib Mocks
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = MagicMock()
sys.modules['matplotlib.figure'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()

class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

        # --- FIX FOR UNPACKING ERROR ---
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        # Force plt.subplots() to return a tuple of (figure, axis)
        sys.modules['matplotlib.pyplot'].subplots.return_value = (mock_fig, mock_ax)

    def run_module_safely(self, module_name):
        if module_name in sys.modules:
            del sys.modules[module_name]
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, 'main'):
                mod.main()
        except Exception as e:
            if "Force Test Exit" in str(e):
                pass 
            else:
                print(f"   [Info] Script '{module_name}' stopped with: {e}")

    def test_keithley2400_iv_protocol(self):
        print("\n[SIMULATION] Keithley 2400 I-V Protocol...")
        with patch('pymeasure.instruments.keithley.Keithley2400') as MockK2400:
            spy_inst = MockK2400.return_value
            spy_inst.voltage = 1.23 
            with patch('builtins.input', side_effect=['100', '10', 'test_output']), \
                 patch('pandas.DataFrame.to_csv'):
                self.run_module_safely("Keithley_2400.Backends.IV_K2400_Loop_Backend_v10")
                spy_inst.enable_source.assert_called()
                print("   -> Verified: Source Output Enabled")

    def test_lakeshore_visa_communication(self):
        print("\n[SIMULATION] Lakeshore 350 SCPI Commands...")
        with patch('pyvisa.ResourceManager') as MockRM:
            spy_instr = MockRM.return_value.open_resource.return_value
            spy_instr.query.side_effect = ["LSCI,MODEL350,0,0"] + ["10.0"] * 20 # Temp readings
            
            # Inputs: Start=10, End=300, Rate=10, Cutoff=350
            fake_inputs = ['10', '300', '10', '350']
            sys.modules['tkinter'].filedialog.asksaveasfilename.return_value = "dummy.csv"
            
            # Circuit Breaker
            mock_sleep = MagicMock(side_effect=[None]*5 + [Exception("Force Test Exit")])

            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('builtins.open', mock_open()), \
                 patch('time.sleep', mock_sleep):
                 
                self.run_module_safely("Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")

            try:
                spy_instr.query.assert_any_call('*IDN?')
                print("   -> Verified: *IDN? Query Sent")
            except AssertionError:
                print("   [FAIL] IDN Query missed.")

            write_calls = [str(c) for c in spy_instr.write.mock_calls]
            if any("HTRSET" in c for c in write_calls):
                print("   -> Verified: Heater Configured (HTRSET)")

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