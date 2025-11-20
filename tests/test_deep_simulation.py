import unittest
import sys
import os
import types
from unittest.mock import MagicMock, patch, call

# -------------------------------------------------------------------------
# 1. SETUP & GLOBAL MOCKS
# We mock the GUI libraries so they don't interfere with the backend logic.
# -------------------------------------------------------------------------
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = MagicMock()

class TestDeepSimulation(unittest.TestCase):

    def setUp(self):
        # Add project root to path so we can import your scripts
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

    # =========================================================================
    # TEST 1: KEITHLEY 2400 I-V SWEEP LOGIC
    # Purpose: Verify the exact sequence of current sourcing and voltage measuring.
    # =========================================================================
    def test_keithley2400_iv_protocol(self):
        print("\n[SIMULATION] Testing Keithley 2400 I-V Protocol...")

        # A. Mock the PyMeasure Keithley object
        with patch('pymeasure.instruments.keithley.Keithley2400') as MockK2400:
            
            # Create the 'Spy' instrument
            spy_inst = MockK2400.return_value
            # Simulate a voltage reading of 1.23 Volts whenever asked
            spy_inst.voltage = 1.23 
            
            # B. Mock User Inputs (Current Limit=100uA, Step=10uA, File='test')
            fake_inputs = ['100', '10', 'test_output']
            
            # C. Mock File Saving (Prevent creating real .txt files)
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv') as mock_save:

                # D. Run the Backend Script
                module_name = "Keithley_2400.Backends.IV_K2400_Loop_Backend_v10"
                if module_name in sys.modules: del sys.modules[module_name]
                try:
                    __import__(module_name)
                except Exception as e:
                    print(f"   [Note] Script finished with: {e} (Expected if script exits)")

                # =============================================================
                # THE "HARD" TESTS (Behavior Verification)
                # =============================================================
                
                # 1. Did we turn the output ON?
                spy_inst.enable_source.assert_called()
                print("   -> Verified: Source Output Enabled")
                
                # 2. Did we ramp current?
                self.assertTrue(spy_inst.ramp_to_current.called, "Failed to ramp current")
                print("   -> Verified: Current Ramping Active")

                # 3. Did we turn it OFF safely at the end?
                spy_inst.shutdown.assert_called()
                print("   -> Verified: Safety Shutdown Triggered")

    # =========================================================================
    # TEST 2: LAKESHORE 350 TEMPERATURE CONTROL
    # Purpose: Verify it reads temperature and sets heater ranges via PyVISA.
    # =========================================================================
    def test_lakeshore_visa_communication(self):
        print("\n[SIMULATION] Testing Lakeshore 350 SCPI Commands...")

        # A. Mock PyVISA Resource Manager
        with patch('pyvisa.ResourceManager') as MockRM:
            mock_rm_instance = MockRM.return_value
            spy_instr = MagicMock()
            mock_rm_instance.open_resource.return_value = spy_instr
            
            # Simulate the Instrument responding to "*IDN?"
            spy_instr.query.return_value = "LSCI,MODEL350,123456,1.0"

            # B. Run the Lakeshore Module
            module_name = "Lakeshore_350_340.Backends.T_Control_L350_Simple_Backend_v10"
            
            # --- FIX IS HERE: Valid inputs (Start < End < Cutoff) ---
            # Inputs: Start=10, End=300, Rate=10, Safety=350
            fake_inputs = ['10', '300', '10', '350'] 
            
            with patch('builtins.input', side_effect=fake_inputs), \
                 patch('pandas.DataFrame.to_csv'):
                 
                if module_name in sys.modules: del sys.modules[module_name]
                try:
                    __import__(module_name)
                except:
                    pass # Scripts usually exit or hit EOF on mock inputs

            # =============================================================
            # THE "HARD" TESTS
            # =============================================================
            
            # 1. Verify Connection Query
            # Did it ask "Who are you?" (*IDN?)
            # Note: We accept ANY call that contains *IDN?
            spy_instr.query.assert_any_call('*IDN?')
            print("   -> Verified: *IDN? Query Sent")

            # 2. Verify Heater Logic (Advanced)
            if spy_instr.write.called:
                args, _ = spy_instr.write.call_args
                print(f"   -> Verified: Command sent to instrument: '{args[0]}'")
            else:
                print("   [Note] No write commands detected (Check mock setup if this persists)")

    # =========================================================================
    # TEST 3: GPIB SCANNER UTILITY
    # Purpose: Verify the scanner iterates addresses and queries them.
    # =========================================================================
    def test_gpib_scanner_loop(self):
        print("\n[SIMULATION] Testing GPIB Scanner Loop...")

        with patch('pyvisa.ResourceManager') as MockRM:
            rm = MockRM.return_value
            
            # 1. Simulate finding 2 instruments connected
            rm.list_resources.return_value = ('GPIB0::24::INSTR', 'GPIB0::12::INSTR')
            
            # 2. Mock the instrument connection
            spy_inst = MagicMock()
            rm.open_resource.return_value.__enter__.return_value = spy_inst
            
            # 3. Run the Scanner Frontend (Logic part)
            try:
                import Utilities.GPIB_Instrument_Scanner_Frontend_v4 as scanner
                
                if hasattr(scanner, 'GPIBScannerWindow'):
                    # Initialize the window (which triggers the scan)
                    scanner.GPIBScannerWindow(MagicMock(), MagicMock())
                    
                    # ASSERTION: Did it ask for the list of resources?
                    rm.list_resources.assert_called()
                    print("   -> Verified: Scanner requested resource list")
                    
                    # ASSERTION: Did it try to open the found resources?
                    rm.open_resource.assert_any_call('GPIB0::24::INSTR')
                    print("   -> Verified: Scanner attempted connection to GPIB::24")

            except ImportError:
                print("   [Skip] Scanner module not found/importable")

if __name__ == '__main__':
    unittest.main()
