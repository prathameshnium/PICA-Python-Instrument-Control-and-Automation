import pytest
import sys
import importlib
from unittest.mock import MagicMock, patch

# ============================================================================ 
# 1. Test Lakeshore 350 Driver Class
# Target: pica/keithley/k6517b/High_Resistance/Instrument_Control/IV_K6517B_L350_T_Control_Instrument_Control.py
# ============================================================================ 
def test_lakeshore_driver_logic():
    """
    Tests the Lakeshore350 class logic without connecting to a real instrument.
    """
    module_name = "pica.keithley.k6517b.High_Resistance.Instrument_Control.IV_K6517B_L350_T_Control_Instrument_Control"
    
    # Import the module
    module = importlib.import_module(module_name)
    
    # Mock the pyvisa resource manager so we don't need a real GPIB cable
    with patch("pyvisa.ResourceManager") as MockRM:
        mock_instr = MagicMock()
        MockRM.return_value.open_resource.return_value = mock_instr
        mock_instr.query.return_value = "LSCI,MODEL350,12345,1.0" # Fake IDN response

        # Instantiate the class
        lakeshore = module.Lakeshore350("GPIB::12")
        
        lakeshore.reset_and_clear()
        mock_instr.write.assert_any_call('*RST')
        
        lakeshore.set_setpoint(1, 300)
        mock_instr.write.assert_any_call('SETP 1,300')
        
        lakeshore.setup_heater(1, 1, 2)
        lakeshore.setup_ramp(1, 10, True)
        
        # Test getters
        mock_instr.query.return_value = "295.5"
        temp = lakeshore.get_temperature('A')
        assert temp == 295.5
        
        lakeshore.close()

# ============================================================================ 
# 2. Test Pyroelectric Measurement Script
# Target: pica/keithley/k6517b/Pyroelectricity/Instrument_Control/Pyroelectric_K6517B_Working_Instrument_Control.py
# ============================================================================ 
def test_pyroelectric_script_import():
    """
    Tests that the pyroelectric control script can be imported and main() exists.
    We don't run main() fully because it contains an infinite loop.
    """
    module_name = "pica.keithley.k6517b.Pyroelectricity.Instrument_Control.Pyroelectric_K6517B_Working_Instrument_Control"
    
    # Just importing it covers the definitions
    module = importlib.import_module(module_name)
    assert hasattr(module, 'main')

# ============================================================================ 
# 3. Test Visualization Script (Tricky: Runs on Import)
# Target: pica/keithley/k6517b/Pyroelectricity/Instrument_Control/PyroDataVisualization_Simple_Instrument_Control.py
# ============================================================================
def test_visualization_script_safe_run():
    """
    This script runs code immediately upon import (it has no 'if __name__ == main' guard).
    We must mock tkinter and file dialogs BEFORE importing to prevent the test from hanging.
    """
    module_name = "pica.keithley.k6517b.Pyroelectricity.Instrument_Control.PyroDataVisualization_Simple_Instrument_Control"
    
    with patch("tkinter.Tk"), \
         patch("tkinter.filedialog.askopenfilename", return_value="dummy_data.csv"), \
         patch("pandas.read_csv") as mock_read, \
         patch("matplotlib.pyplot.show"), \
         patch("matplotlib.pyplot.subplots", return_value=(MagicMock(), [MagicMock(), MagicMock(), MagicMock()])):
        
        # Mock pandas data so the plotting logic doesn't crash
        mock_df = MagicMock()
        mock_df.__getitem__.return_value = [1, 2, 3] # Fake columns
        mock_read.return_value = mock_df

        # If the module was already imported by another test, reload it to trigger execution
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
        else:
            importlib.import_module(module_name)

# ============================================================================ 
# 4. Test GPIB Scanner Script (Runs on Import)
# Target: pica/utils/GPIB_VISA_InterfaceTest_Simple_Instrument_Control.py
# ============================================================================ 
def test_gpib_scanner_script():
    """
    This script also runs immediately on import. We mock PyVISA to prevent errors.
    """
    module_name = "pica.utils.GPIB_VISA_InterfaceTest_Simple_Instrument_Control"
    
    with patch("pyvisa.ResourceManager") as MockRM:
        # Simulate finding one instrument
        mock_instr = MagicMock()
        mock_instr.query.return_value = "Keithley Instruments, Model 2400"
        
        instance = MockRM.return_value
        instance.list_resources.return_value = ["GPIB0::24::INSTR"]
        instance.open_resource.return_value.__enter__.return_value = mock_instr
        
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
        else:
            importlib.import_module(module_name)
