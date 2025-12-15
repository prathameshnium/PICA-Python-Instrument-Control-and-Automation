import pytest
from unittest.mock import patch, MagicMock
import sys
import os
import runpy

sys.path.insert(0, os.path.abspath('.'))

@patch('pica.cli.subprocess.run')
@patch('builtins.input')
@patch('pica.cli.print_banner')
@patch('pica.cli.find_scripts')
def test_pica_cli_exit(mock_find_scripts, mock_print_banner, mock_input, mock_subprocess_run):
    """
    Test that pica_cli.py's main function exits correctly when 'q' is entered.
    """
    mock_find_scripts.return_value = [('Dummy Script', '/path/to/dummy_script.py')] # Provide a dummy script
    mock_input.side_effect = ['q'] # Simulate user entering 'q'

    with pytest.raises(SystemExit) as e:
        import pica_cli
        pica_cli.main()
    
    assert e.type == SystemExit
    assert e.value.code == 0
    mock_print_banner.assert_called_once()
    mock_find_scripts.assert_called_once()
    mock_input.assert_called_once()

@patch('pica.main.tk.Tk')
@patch('pica.main.PICALauncherApp')
@patch('pica.main.multiprocessing.set_start_method')
@patch('pica.main.multiprocessing.freeze_support')
def test_run_pica_main(mock_freeze_support, mock_set_start_method, mock_pica_launcher_app, mock_tk_tk):
    """
    Test that run_pica.py's main function initializes the GUI and
    sets multiprocessing start method.
    """
    mock_root = MagicMock()
    mock_tk_tk.return_value = mock_root

    # Ensure main() is called
    import pica.main
    pica.main.main()

    mock_tk_tk.assert_called_once()
    mock_pica_launcher_app.assert_called_once_with(mock_root)
    mock_root.mainloop.assert_called_once()

import run_pica

def test_run_pica_script():
    """
    Tests that run_pica.py correctly initializes the main GUI.
    We use runpy to execute the script file while patching the main function.
    """
    # Get the absolute path to run_pica.py
    # Assuming tests/ is one level deep, so we go up one level
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'run_pica.py'))

    # Patch pica.main.main to prevent the real GUI from launching
    with patch("pica.main.main") as mock_main:
        # Execute the script as __main__
        runpy.run_path(script_path, run_name="__main__")
        
        # Verify it attempted to start the PICA GUI
        mock_main.assert_called_once()