"""
A one-time script to restructure the PICA project from a flat layout
to a professional, installable Python package structure.
"""
import os
import shutil
import subprocess
import sys
import re

# Configuration: Mapping Old Paths to New Professional Paths
# Format: "Old_Folder_Name": "new/path/inside/pica"
MAPPING = {
    "Keithley_2400": "pica/keithley/k2400",
    "Keithley_6517B": "pica/keithley/k6517b",
    "Keithley_2400_Keithley_2182": "pica/keithley/k2400_2182",
    "Delta_mode_Keithley_6221_2182": "pica/keithley/delta_mode",
    "Lakeshore_350_340": "pica/lakeshore",
    "LCR_Keysight_E4980A": "pica/keysight",
    "Lock_in_amplifier": "pica/lockin",
    "Utilities": "pica/utils",
    "assets": "pica/assets"  # Assets often go inside the package for GUI access
}

ROOT_FILES_TO_MOVE = {
    "PICA.py": "pica/main.py",
    "clean_filenames.py": "scripts/clean_filenames.py"
}

def run_command(command):
    """Runs a shell command."""
    try:
        subprocess.check_call(command, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {command}\n{e}")
        sys.exit(1)

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def create_init_files(root_dir):
    """Recursively creates __init__.py files to make directories packages."""
    print("Creating __init__.py files...")
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if "__init__.py" not in filenames:
            with open(os.path.join(dirpath, "__init__.py"), 'w') as f:
                f.write(f"# Auto-generated package init for {os.path.basename(dirpath)}\n")

def update_imports(file_path):
    """
    Reads a python file and updates old import paths to new pica.* paths.
    This is a regex-based 'best effort' refactor.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # Generic import fixer based on the MAPPING dictionary
    # This makes the script more robust to future changes
    replacements = {
        "from Utilities": "from pica.utils",
        "import Utilities": "import pica.utils",
        "from Keithley_2400": "from pica.keithley.k2400",
        "from Lakeshore_350_340": "from pica.lakeshore",
        "from Delta_mode_Keithley_6221_2182": "from pica.keithley.delta_mode",
        "from LCR_Keysight_E4980A": "from pica.keysight",
    }

    for old, new in replacements.items():
        content = re.sub(old, new, content)

    if content != original_content:
        print(f"  [Refactoring] Updated imports in {file_path}")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

def main():
    print("--- Starting Professional PICA Refactoring ---")

    # 1. Create Base Directories
    ensure_dir("pica")
    ensure_dir("scripts")

    # Check if this is a git repo
    is_git = os.path.exists(".git")
    if is_git:
        print("Git repository detected. Using 'git mv' to preserve history.")
    else:
        print("No git detected. Using standard file move.")

    # 2. Move Folders based on Mapping
    for old_name, new_path in MAPPING.items():
        if os.path.exists(old_name):
            print(f"Moving {old_name} -> {new_path}")
            # Ensure parent dir exists
            ensure_dir(os.path.dirname(new_path))

            if is_git:
                # git mv expects the parent directory of destination to exist
                run_command(f'git mv "{old_name}" "{new_path}"')
            else:
                shutil.move(old_name, new_path)
        else:
            print(f"Skipping {old_name} (Not found)")

    # 3. Move Root Files
    for old_file, new_path in ROOT_FILES_TO_MOVE.items():
        if os.path.exists(old_file):
            print(f"Moving {old_file} -> {new_path}")
            ensure_dir(os.path.dirname(new_path))
            if is_git:
                run_command(f'git mv "{old_file}" "{new_path}"')
            else:
                shutil.move(old_file, new_path)

    # 4. Create __init__.py files
    create_init_files("pica")

    # 5. Update Imports in all Python files
    print("Scanning for import statements to update...")
    for dirpath, _, filenames in os.walk("pica"):
        for filename in filenames:
            if filename.endswith(".py"):
                update_imports(os.path.join(dirpath, filename))

    # Also check tests
    if os.path.exists("tests"):
        for dirpath, _, filenames in os.walk("tests"):
            for filename in filenames:
                if filename.endswith(".py"):
                    update_imports(os.path.join(dirpath, filename))

    # 6. Create a new Launcher at root (Shim)
    print("Creating root-level launcher (run_pica.py)...")
    with open("run_pica.py", "w") as f:
        f.write("from pica.main import main\n\nif __name__ == '__main__':\n    main()\n")

    print("\n--- Refactoring Complete ---")
    print("1. Review the 'pica/' folder structure.")
    print("2. Run 'pip install -e .' to install the package in editable mode.")
    print("3. Try running 'python run_pica.py' to test.")
    print("4. Run your tests: 'pytest'")

if __name__ == "__main__":
    main()