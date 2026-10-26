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
    Test that the pica-cli entry point (pica.cli.main) exits correctly when 'q' is entered.
    """
    mock_find_scripts.return_value = [('Dummy Script', '/path/to/dummy_script.py')] # Provide a dummy script
    mock_input.side_effect = ['q'] # Simulate user entering 'q'

    with pytest.raises(SystemExit) as e:
        import pica.cli
        pica.cli.main()
    
    assert e.type == SystemExit
    assert e.value.code == 0
    mock_print_banner.assert_called_once()
    mock_find_scripts.assert_called_once()
    mock_input.assert_called_once()

@patch('pica.main.tk.Tk')
@patch('pica.main.PICALauncherApp')
@patch('pica.main.multiprocessing.set_start_method')
@patch('pica.main.multiprocessing.freeze_support')
def test_legacy_launcher_main(mock_freeze_support, mock_set_start_method, mock_pica_launcher_app, mock_tk_tk):
    """
    Test that the legacy (v1, outdated) launcher's main(), pica.main.main,
    builds PICALauncherApp on a Tk root and runs the mainloop. (pica-gui and
    run_pica.py open launcher v2; see test_run_pica_script.)
    """
    mock_root = MagicMock()
    mock_tk_tk.return_value = mock_root

    # Ensure main() is called
    import pica.main
    pica.main.main()

    mock_tk_tk.assert_called_once()
    mock_pica_launcher_app.assert_called_once_with(mock_root)
    mock_root.mainloop.assert_called_once()

@pytest.mark.parametrize("script, target, kwargs", [
    ("run_pica.py", "pica.main_v2.main", {"advanced": True}),   # v2, Advanced Options
    ("run_pica_v2.py", "pica.main_v2.main", {}),                # v2, Quick Select
    ("legacy_stuff/run_pica_legacy.py", "pica.main.main", {}),             # outdated v1 launcher
])
@patch('multiprocessing.set_start_method')
def test_run_pica_script(_mock_set_start_method, script, target, kwargs):
    """
    Tests that each run_pica*.py script starts the launcher it names.
    We use runpy to execute the script file while patching the main function.
    """
    # Assuming tests/ is one level deep, so we go up one level
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', script))

    # Patch the launcher's main to prevent the real GUI from launching
    with patch(target) as mock_main:
        # Execute the script as __main__
        runpy.run_path(script_path, run_name="__main__")
        
        # Verify it attempted to start the PICA GUI
        mock_main.assert_called_once_with(**kwargs)


def test_pica_gui_entry_point_opens_advanced_options():
    path = os.path.join(os.path.dirname(__file__), '..', 'pyproject.toml')
    text = open(path, encoding='utf-8').read()
    assert 'pica-gui = "pica.main_v2:main_advanced"' in text


@patch('pica.main_v2.multiprocessing.set_start_method')
@patch('pica.main_v2.tk.Tk')
@patch('pica.main_v2.PICALauncherV2')
def test_main_v2_advanced_starts_in_advanced_options(mock_app, mock_tk, _mock_ssm):
    import pica.main_v2 as v2
    v2.main_advanced()
    mock_app.return_value.start_in_advanced.assert_called_once_with()
    mock_tk.return_value.mainloop.assert_called_once()
    mock_app.reset_mock()
    v2.main()                       # run_pica_v2.py: Quick Select
    mock_app.return_value.start_in_advanced.assert_not_called()