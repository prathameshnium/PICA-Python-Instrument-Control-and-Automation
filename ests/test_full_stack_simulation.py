import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# -------------------------------------------------------------------------
# GLOBAL MOCKS (The "Headless" GUI Trick)
# -------------------------------------------------------------------------
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = MagicMock()
sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()
sys.modules['pyvisa'] = MagicMock()
sys.modules['pymeasure'] = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = MagicMock()
sys.modules['PIL.ImageTk'] = MagicMock()

class TestFullStack(unittest.TestCase):

    def setUp(self):
        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)

    def test_launcher_buttons(self):
        """
        Verify that the Launcher buttons point to valid script paths 
        and attempt to launch a process.
        """
        print("\n[FULL-STACK] Testing Launcher Integration...")
        
        try:
            import PICA_v6 as launcher
        except ImportError:
            self.skipTest("Could not import PICA_v6.py")

        # We spy on the 'Process' class to see if it gets called
        with patch('multiprocessing.Process') as MockProcess:
            
            # Initialize the App (Headless)
            app = launcher.PICALauncherApp(MagicMock())
            
            # Pick a button to test (e.g., "K2400 I-V")
            script_key = "K2400 I-V"
            if script_key in app.SCRIPT_PATHS:
                # Simulate the button click action
                target_script = app.SCRIPT_PATHS[script_key]
                app.launch_script(target_script)
                
                # ASSERTION 1: Did we try to spawn a process?
                MockProcess.assert_called()
                
                # ASSERTION 2: Did we pass the correct script?
                _, kwargs = MockProcess.call_args
                args = kwargs.get('args', [])
                if args:
                    # The first arg to the process should be the script path
                    self.assertIn("IV_K2400_Frontend", str(args[0]))
                    print(f"   -> Verified: Launcher targeted correct script: {os.path.basename(str(args[0]))}")
                
                # ASSERTION 3: Did we start it?
                MockProcess.return_value.start.assert_called()
                print("   -> Verified: Process launched successfully.")
            else:
                self.fail(f"Key '{script_key}' not found in Launcher config.")

if __name__ == '__main__':
    unittest.main()
