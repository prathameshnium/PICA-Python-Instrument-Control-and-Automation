# build.py
'''
===============================================================================
 PROGRAM:      PICA Build Script

 PURPOSE:      Compiles the PICA suite into a standalone Windows executable.

 DESCRIPTION:  This script automates a two-stage build process using PyInstaller.
               
               STAGE 1: Compiles each individual measurement frontend (.py) into
               its own standalone executable (.exe). These are the "sub-exes".

               STAGE 2: Compiles the main launcher, Picachu.py, into the final
               Picachu.exe. It bundles all the sub-exes from Stage 1 into a
               'programs' folder, along with all other necessary assets
               (logos, documentation), creating a fully self-contained application.

 USAGE:        Run from the project root directory:
               > python build.py

 AUTHOR:       Prathamesh K Deshmukh
===============================================================================
'''
import os
import subprocess
import shutil
import sys

# --- Configuration ---
PYINSTALLER_PATH = "pyinstaller" # Use 'pyinstaller' if it's in your PATH
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")

# This is a temporary directory to store the compiled sub-programs before they are bundled.
SUB_PROGRAMS_TEMP_DIR = os.path.join(BUILD_DIR, "programs")
SPECS_DIR = os.path.join(PROJECT_ROOT, "Setup", "specs")

# --- NEW: Path to the application icon ---
ICON_FILE = os.path.join(PROJECT_ROOT, "_assets", "LOGO", "PICA_LOGO.ico")

# --- NEW: Get version from Picachu.py for the zip file name ---
# Add Setup directory to path for the import below. This also helps linters like Pylance.
setup_dir = os.path.join(PROJECT_ROOT, "Setup")
if setup_dir not in sys.path:
    sys.path.insert(0, setup_dir)
from Picachu import PICALauncherApp
APP_VERSION = PICALauncherApp.PROGRAM_VERSION

# List of all frontend scripts to compile into sub-EXEs.
# Format: ("path/to/script.py", "FinalExeName.exe")
SUB_PROGRAMS = [
    ("Delta_mode_Keithley_6221_2182/IV_K6221_DC_Sweep_Frontend_V10.py", "IV_K6221_DC_Sweep_Frontend_V10.exe"),
    ("Delta_mode_Keithley_6221_2182/Delta_RT_K6221_K2182_L350_T_Control_Frontend_v5.py", "Delta_RT_K6221_K2182_L350_T_Control_Frontend_v5.exe"),
    ("Delta_mode_Keithley_6221_2182/Delta_RT_K6221_K2182_L350_Sensing_Frontend_v5.py", "Delta_RT_K6221_K2182_L350_Sensing_Frontend_v5.exe"),
    ("Keithley_2400/IV_K2400_Frontend_v3.py", "IV_K2400_Frontend_v3.exe"),
    ("Keithley_2400/RT_K2400_L350_T_Control_Frontend_v3.py", "RT_K2400_L350_T_Control_Frontend_v3.exe"),
    ("Keithley_2400/RT_K2400_L350_T_Sensing_Frontend_v4.py", "RT_K2400_L350_T_Sensing_Frontend_v4.exe"),
    ("Keithley_2400_Keithley_2182/IV_K2400_K2182_Frontend_v3.py", "IV_K2400_K2182_Frontend_v3.exe"),
    ("Keithley_2400_Keithley_2182/RT_K2400_K2182_T_Control_Frontend_v3.py", "RT_K2400_K2182_T_Control_Frontend_v3.exe"),
    ("Keithley_2400_Keithley_2182/RT_K2400_2182_L350_T_Sensing_Frontend_v2.py", "RT_K2400_2182_L350_T_Sensing_Frontend_v2.exe"),
    ("Keithley_6517B/High_Resistance/IV_K6517B_Frontend_v11.py", "IV_K6517B_Frontend_v11.exe"),
    ("Keithley_6517B/High_Resistance/RT_K6517B_L350_T_Control_Frontend_v13.py", "RT_K6517B_L350_T_Control_Frontend_v13.py"),
    ("Keithley_6517B/High_Resistance/RT_K6517B_L350_T_Sensing_Frontend_v14.py", "RT_K6517B_L350_T_Sensing_Frontend_v14.exe"),
    ("Keithley_6517B/Pyroelectricity/Pyroelectric_K6517B_L350_Frontend_v4.py", "Pyroelectric_K6517B_L350_Frontend_v4.exe"),
    ("Lakeshore_350_340/T_Control_L350_RangeControl_Frontend_v8.py", "T_Control_L350_RangeControl_Frontend_v8.exe"),
    ("Lakeshore_350_340/T_Sensing_L350_Frontend_v4.py", "T_Sensing_L350_Frontend_v4.exe"),
    ("LCR_Keysight_E4980A/CV_KE4980A_Frontend_v3.py", "CV_KE4980A_Frontend_v3.exe"),
    ("Lock_in_amplifier/AC_Measurement_S830_Frontend_v1.py", "AC_Measurement_S830_Frontend_v1.exe"),
    # Note: The Plotter and GPIB Scanner are now launched from within other frontends,
    # but we still compile them here so they exist as executables.
    ("Utilities/PlotterUtil_Frontend_v3.py", "PlotterUtil_Frontend_v3.exe"),
    ("Utilities/GPIB_Instrument_Scanner_Frontend_v4.py", "GPIB_Instrument_Scanner_Frontend_v4.exe"),
]

PICACHU_SCRIPT = "Setup/Picachu.py"

def run_command(command):
    """Executes a command and prints it, raising an error if it fails."""
    print(f"--- Running: {' '.join(command)} ---")
    try:
        subprocess.run(command, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"--- ERROR ---", file=sys.stderr)
        print(e.stdout, file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        raise

def build():
    print(">>> Cleaning previous builds...")
    if os.path.exists(DIST_DIR): shutil.rmtree(DIST_DIR)
    if os.path.exists(BUILD_DIR): shutil.rmtree(BUILD_DIR)
    os.makedirs(SUB_PROGRAMS_TEMP_DIR, exist_ok=True)

    print("\n>>> STAGE 1: Compiling sub-program executables...")
    for script_path, exe_name in SUB_PROGRAMS:
        full_script_path = os.path.join(PROJECT_ROOT, script_path)
        print(f"\nCompiling {exe_name}...")

        spec_filename = f"{os.path.splitext(exe_name)[0]}.spec"
        spec_path = os.path.join(SPECS_DIR, spec_filename)

        # Compile each sub-program. Using a temporary distpath for each prevents conflicts.
        run_command([
            PYINSTALLER_PATH, "--noconfirm", "--clean",
            f"--distpath={SUB_PROGRAMS_TEMP_DIR}",
            spec_path
        ])

    print("\n>>> STAGE 2: Compiling the main Picachu launcher...")
    picachu_spec_path = os.path.join(SPECS_DIR, "Picachu.spec")
    run_command([
        PYINSTALLER_PATH, "--noconfirm", "--clean",
        picachu_spec_path
    ])

    print("\n>>> STAGE 3: Creating distributable ZIP file...")
    final_app_dir = os.path.join(DIST_DIR, "Picachu")
    zip_filename = f"PICA_v{APP_VERSION}_Windows"
    shutil.make_archive(os.path.join(DIST_DIR, zip_filename), 'zip', final_app_dir)

    print("\n--- Build Complete! ---")
    print(f"The final application folder is located at:")
    print(f"  {final_app_dir}")
    print(f"\nA distributable ZIP file has been created at:")
    print(f"  {os.path.join(DIST_DIR, zip_filename + '.zip')}")

if __name__ == "__main__":
    build()