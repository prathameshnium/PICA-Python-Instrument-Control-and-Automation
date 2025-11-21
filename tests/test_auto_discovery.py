import unittest
import sys
import os
import importlib.util
from unittest.mock import MagicMock

# -------------------------------------------------------------------------
# GLOBAL HEADLESS MOCKS
# We mock everything before we even look for files.
# -------------------------------------------------------------------------
MOCK_MODULES = [
    'tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'tkinter.filedialog',
    'tkinter.scrolledtext', 'tkinter.font', 'tkinter.Canvas',
    'pyvisa', 'pyvisa.errors', 'pyvisa.resources',
    'pymeasure', 'pymeasure.instruments', 'pymeasure.instruments.keithley',
    'matplotlib', 'matplotlib.pyplot', 'matplotlib.backends',
    'matplotlib.backends.backend_tkagg', 'matplotlib.figure',
    'matplotlib.gridspec',  # <--- ADDED THIS (Fixes your error)
    'matplotlib.ticker',   # <--- ADDED THIS (Prevent future errors)
    'PIL', 'PIL.Image', 'PIL.ImageTk',
    'pandas', 'numpy', 'gpib_ctypes'
]

# Apply the mocks to sys.modules
for mod in MOCK_MODULES:
    sys.modules[mod] = MagicMock()


class TestDynamicDiscovery(unittest.TestCase):

    def setUp(self):
        # Point to the root directory of the repo
        self.root_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..'))

    def load_module_from_path(self, file_path):
        """
        Dynamically loads a Python file as a module given its path.
        This effectively 'runs' the top-level code (imports, class definitions).
        """
        spec = importlib.util.spec_from_file_location(
            "dynamic_module", file_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules["dynamic_module"] = module
            spec.loader.exec_module(module)
            return module
        return None

    def test_all_frontends_and_launchers(self):
        """
        Walks through the entire repository.
        If a file looks like a GUI script, try to import it.
        """
        print("\n[AUTO-DISCOVERY] Scanning repository for GUI modules...")

        found_count = 0
        failure_count = 0

        # Walk through all folders starting from root
        for root, dirs, files in os.walk(self.root_dir):

            # Skip the 'tests', 'Setup', and hidden folders
            if "tests" in root or "Setup" in root or ".__" in root:
                continue

            for file in files:
                # DEFINITION OF A GUI MODULE:
                # 1. Ends with .py
                # 2. Contains 'GUI' OR starts with 'PICA_v' (your launcher)
                if file.endswith(".py") and (
                        "GUI" in file or file.startswith("PICA_v")):

                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.root_dir)

                    print(f" -> Testing: {rel_path}", end=" ... ")

                    try:
                        # Try to load it
                        self.load_module_from_path(full_path)
                        print("OK")
                        found_count += 1
                    except Exception as e:
                        print(f"FAIL\n    Error: {e}")
                        failure_count += 1

        print(
            f"\n[SUMMARY] Tested {found_count + failure_count} modules. Passed {found_count}. Failed {failure_count}.")

        # Fail the test if anything broke
        self.assertEqual(
            failure_count,
            0,
            f"{failure_count} modules failed to load.")


if __name__ == '__main__':
    unittest.main()
