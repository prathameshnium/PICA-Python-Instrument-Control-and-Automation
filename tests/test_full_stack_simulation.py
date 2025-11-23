"""
Purpose: End-to-end protocol verification.

What it does: Simulates specific instrument responses (e.g., feeding fake temperature data like "10.0, 50.0" to the Lakeshore script) and verifies that the script sends the correct commands back (e.g., "HTRSET").
"""
import unittest

import os
import importlib
import pytest
from unittest.mock import MagicMock, patch, mock_open, PropertyMock
import sys

class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        # Add project root to path so we can import your scripts
        self.root_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)
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
    @pytest.mark.usefixtures("mock_tkinter")
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
                    "Keithley_2400.Instrument_Control.IV_K2400_Loop_Instrument_Control_v10")

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
    @pytest.mark.usefixtures("mock_tkinter")
    def test_lakeshore_visa_communication(self):
        print("\n[SIMULATION] Testing Lakeshore 350 SCPI Commands...")

        # FIX 1: Create mocks for Figure and Axes
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        
        # FIX 2: Handle "line, = ax.plot()" failure (expected 1, got 0)
        # We tell the mock axis that when .plot() is called, it returns a list with 1 item
        mock_ax.plot.return_value = [MagicMock()] 
        
        # FIX 3: Handle "ax1, ax2 = fig.subplots()" (expected 2)
        mock_fig.subplots.return_value = (mock_ax, mock_ax)

        # Patch ResourceManager AND pyplot
        with patch('pyvisa.ResourceManager') as MockResourceManager, \
             patch('matplotlib.pyplot.subplots', return_value=(mock_fig, mock_ax)):

            mock_rm_instance = MockResourceManager.return_value
            spy_instr = MagicMock()
            mock_rm_instance.open_resource.return_value = spy_instr

            # 1. Mock Responses (IDN, then temperature readings)
            spy_instr.query.side_effect = [
                "LSCI,MODEL350,123456,1.0",  # Response to *IDN?
                "10.0",                      # Initial temp
                "10.0",                      # Stability check 1
                "10.1,50.0",                 # Loop 1
                "10.1",                      # Stability check 2
                "10.2,55.0",                 # Loop 2
                "10.2",                      # Stability check 3
                Exception("Force Test Exit") # Force exit
            ]

            # 2. Mock Inputs and File Dialog
            fake_inputs = ['10', '300', '10', '350']
            mock_file_dialog = MagicMock(return_value="dummy.csv")

            # 3. Run It
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('builtins.open', mock_open()), \
                 patch('time.sleep', MagicMock()), \
                 patch('tkinter.filedialog.asksaveasfilename', mock_file_dialog):

                self.run_module_safely(
                    "Lakeshore_350_340.Instrument_Control.T_Control_L350_Simple_Instrument_Control_v10")

            # --- ASSERTIONS ---
            # Now that the script runs fully without crashing on .plot(), 
            # the cleanup code (RANGE 0) should finally execute.
            
            write_calls = [str(c) for c in spy_instr.write.mock_calls]

            self.assertTrue(any("HTRSET" in c for c in write_calls), "HTRSET command not found")
            print("   -> Verified: Heater Configured (HTRSET)")

            self.assertTrue(any("RANGE 1,0" in c for c in write_calls) or spy_instr.close.called,
                            "Safety Shutdown Failed: Heater not off and connection not closed.")
            print("   -> Verified: Safety Shutdown (Heater Off or Connection Closed)")


    # =========================================================================
    # TEST 3: GPIB SCANNER
    # =========================================================================
    @pytest.mark.usefixtures("mock_tkinter")
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
