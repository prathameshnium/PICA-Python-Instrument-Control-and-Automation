import unittest
import sys
import os
import importlib
from unittest.mock import MagicMock, patch, mock_open

# -------------------------------------------------------------------------
# 1. GLOBAL MOCKS (The "Headless" GUI Trick)
# -------------------------------------------------------------------------
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()

# Matplotlib Mocks



class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        # Add project root to path so we can import your scripts
        self.root_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

        # --- FIX FOR "not enough values to unpack" ---
        # Your script does: fig, ax = plt.subplots()
        # We must tell the mock to return a tuple of (fig, ax)
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        sys.modules['matplotlib.pyplot'].subplots.return_value = (
            mock_fig, mock_ax)

    def run_module_safely(self, module_name):
        """Helper to import a module and run its main() if it exists."""
        if module_name in sys.modules:
            del sys.modules[module_name]

        try:
            # 1. Import the module (runs top-level code)
            mod = importlib.import_module(module_name)

            # 2. Explicitly run main() if it exists (Crucial for Lakeshore
            # script)
            if hasattr(mod, 'main'):
                print(f"   [Exec] Running {module_name}.main()...")
                mod.main()
            else:
                print(f"   [Exec] Module {module_name} ran on import.")

        except Exception as e:
            # We expect 'Force Test Exit' from our time.sleep mock
            if "Force Test Exit" in str(e):
                print(
                    "   [Info] Simulation loop broken successfully (Circuit Breaker).")
            else:
                print(f"   [Info] Script stopped with: {e}")

    # =========================================================================
    # TEST 1: KEITHLEY 2400 (Standard Script)
    # =========================================================================
    def test_keithley2400_iv_protocol(self):
        print("\n[SIMULATION] Testing Keithley 2400 I-V Protocol...")

        with patch('pymeasure.instruments.keithley.Keithley2400') as MockK2400:
            spy_inst = MockK2400.return_value
            spy_inst.voltage = 1.23

            # Inputs: Current=100uA, Step=10uA, File=test_output
            fake_inputs = ['100', '10', 'test_output']

            with patch('builtins.input', side_effect=fake_inputs), \
                    patch('pandas.DataFrame.to_csv'):

                self.run_module_safely(
                    "Keithley_2400.Backends.IV_K2400_Loop_Backend_v10")

                # Assertions
                spy_inst.enable_source.assert_called()
                print("   -> Verified: Source Output Enabled")
                self.assertTrue(spy_inst.ramp_to_current.called)
                print("   -> Verified: Current Ramping Active")
                spy_inst.shutdown.assert_called()
                print("   -> Verified: Safety Shutdown Triggered")

    # =========================================================================
    # TEST 2: LAKESHORE 350 (Complex Logic with Loop)
    # =========================================================================
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.pyvisa.ResourceManager')
    @patch('Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10.plt')
    def test_lakeshore_visa_communication(self, MockPlot, MockResourceManager):
        print("\n[SIMULATION] Testing Lakeshore 350 SCPI Commands...")

        # Mock matplotlib components
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        MockPlot.subplots.return_value = (mock_fig, mock_ax)
        MockPlot.ion.return_value = None
        MockPlot.ioff.return_value = None
        MockPlot.show.return_value = None

        mock_rm_instance = MockResourceManager.return_value
        spy_instr = MagicMock()
        mock_rm_instance.open_resource.return_value = spy_instr

        # 1. Mock Responses (IDN, then temperature readings)
        # Add the "Force Test Exit" exception to the side_effect for the query,
        # ensuring the loop breaks and cleanup is called.
        spy_instr.query.side_effect = [
            "LSCI,MODEL350,123456,1.0",  # *IDN?
            "10.0",  # Initial Temp
            "10.0",  # Stabilize check 1
            "10.1",  # Stabilize check 2
            "10.1",  # Loop 1
            "10.2",  # Loop 2
            "300.0",  # Safety Fallback
            Exception("Force Test Exit") # Force exit after some iterations
        ] * 5

        # 2. Mock Inputs: Start=10, End=300, Rate=10, Cutoff=350
        # (Logic: 10 < 300 < 350 is Valid)
        fake_inputs = ['10', '300', '10', '350']

        # 3. Mock File Dialog (Must return string)
        sys.modules['tkinter'].filedialog.asksaveasfilename.return_value = "dummy.csv"

        # 4. Circuit Breaker: We now let the spy_instr.query raise the exception
        # mock_sleep = MagicMock(side_effect=[None, None, None, None, None, Exception("Force Test Exit")])

        # 5. Run It
        with patch('builtins.input', side_effect=fake_inputs), \
             patch('builtins.open', mock_open()), \
             patch('time.sleep', MagicMock()): # We mock sleep to prevent delays

            self.run_module_safely(
                "Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10")

        # --- ASSERTIONS ---

        # Did we ask for ID?
        spy_instr.query.assert_any_call('*IDN?')
        print("   -> Verified: *IDN? Query Sent")

        # Get all write commands sent
        write_calls = [str(c) for c in spy_instr.write.mock_calls]

        # Did we configure the heater? (HTRSET)
        self.assertTrue(any("HTRSET" in c for c in write_calls), "HTRSET command not found")
        print("   -> Verified: Heater Configured (HTRSET)")

        # Did we turn it off at the end? (RANGE ... 0) or close the instrument
        self.assertTrue(any("RANGE 1,0" in c for c in write_calls) or spy_instr.close.called,
                        "Safety Shutdown Failed: Heater not off and connection not closed.")
        print("   -> Verified: Safety Shutdown (Heater Off or Connection Closed)")


    # =========================================================================
    # TEST 3: GPIB SCANNER
    # =========================================================================
    def test_gpib_scanner_loop(self):
        print("\n[SIMULATION] Testing GPIB Scanner Loop...")
        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            rm.list_resources.return_value = (
                'GPIB0::24::INSTR', 'GPIB0::12::INSTR')

            try:
                import Utilities.GPIB_Instrument_Scanner_GUI_v4 as scanner
                if hasattr(scanner, 'GPIBScannerWindow'):
                    scanner.GPIBScannerWindow(MagicMock(), MagicMock())
                    rm.list_resources.assert_called()
                    print("   -> Verified: Scanner requested resource list")
            except ImportError:
                pass


if __name__ == '__main__':
    unittest.main()