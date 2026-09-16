"""
Module: PICA CLI
Purpose: CLI interface for instrument control modules (CLI's) in PICA (OLD SCRIPTS).
"""
import os
import sys
import subprocess
import time
from pathlib import Path

ALL_GUI_MODULES = [
    "pica.keithley.delta_mode.Delta_RT_K6221_K2182_L350_Sensing_GUI",
    "pica.keithley.delta_mode.Delta_RT_K6221_K2182_CC34_Sensing_GUI",
    "pica.keithley.delta_mode.Delta_RT_K6221_K2182_L350_T_Control_GUI",
    "pica.keithley.delta_mode.IV_K6221_DC_Sweep_GUI",
    "pica.keithley.k2400.IV_K2400_GUI",
    "pica.keithley.k2400.K2400_DirectControl_GUI",
    "pica.keithley.k2400.RT_K2400_L350_T_Control_GUI",
    "pica.keithley.k2400.RT_K2400_L350_T_Sensing_GUI",
    "pica.keithley.k2400.RT_K2400_CC34_T_Sensing_GUI",
    "pica.keithley.k2400_2182.IV_K2400_K2182_GUI",
    "pica.keithley.k2400_2182.RT_K2400_K2182_L350_T_Sensing_GUI",
    "pica.keithley.k2400_2182.RT_K2400_K2182_CC34_T_Sensing_GUI",
    "pica.keithley.k2400_2182.RT_K2400_K2182_T_Control_GUI",
    "pica.keithley.k6517b.High_Resistance.IV_K6517B_GUI",
    "pica.keithley.k6517b.High_Resistance.RT_K6517B_L350_T_Control_GUI",
    "pica.keithley.k6517b.High_Resistance.RT_K6517B_L350_T_Sensing_GUI",
    "pica.keithley.k6517b.High_Resistance.RT_K6517B_CC34_T_Sensing_GUI",
    "pica.keithley.k6517b.Pyroelectricity.Pyroelectric_K6517B_L350_GUI",
    "pica.keysight.CV_KE4980A_GUI",
    "pica.keysight.Field_Step_Freq_Scan_E4980A_GUI",
    "pica.keysight.Temprature_Scan_Passive_CC34_E4980A_GUI",
    "pica.novocontrol.Frequency_Scan_AlphaAN_GUI",
    "pica.lakeshore.T_Control_L350_RangeControl_GUI",
    "pica.lakeshore.T_Control_L350_Step_GUI",
    "pica.lakeshore.T_Control_L350_DirectControl_GUI",
    "pica.lakeshore.T_Sensing_L350_GUI",
    "pica.lakeshore.T_Control_L340_RangeControl_GUI",
    "pica.lakeshore.T_Control_L340_DirectControl_GUI",
    "pica.lakeshore.T_Sensing_L340_GUI",
    "pica.lakeshore.T_Control_L340_Step_GUI",
    "pica.keithley.delta_mode.Delta_RT_K6221_K2182_L340_Sensing_GUI",
    "pica.keithley.delta_mode.Delta_RT_K6221_K2182_L340_T_Control_GUI",
    "pica.keithley.k2400.RT_K2400_L340_T_Control_GUI",
    "pica.keithley.k2400.RT_K2400_L340_T_Sensing_GUI",
    "pica.keithley.k2400_2182.RT_K2400_K2182_L340_T_Sensing_GUI",
    "pica.keithley.k2400_2182.RT_K2400_K2182_L340_T_Control_GUI",
    "pica.keithley.k6517b.High_Resistance.RT_K6517B_L340_T_Control_GUI",
    "pica.keithley.k6517b.High_Resistance.RT_K6517B_L340_T_Sensing_GUI",
    "pica.keithley.k6517b.Pyroelectricity.Pyroelectric_K6517B_L340_GUI",
    "pica.keysight.Temprature_Scan_Passive_L340_E4980A_GUI",
    "pica.cryocon.T_Control_CC34_DirectControl_GUI",
    "pica.cryocon.T_Sensing_CC34_GUI",
    "pica.utils.GPIB_Instrument_Scanner_GUI",
    "pica.utils.PlotterUtil_GUI",
    "pica.utils.SCPI_Console_GUI",

    # --- Added to the test registry: instantiation-verified GUI modules. ---
    # Keep this list complete; tests/test_gui_module_registry.py fails if a
    # *_GUI.py module is neither listed here nor in its documented exclusions.
    # PPMS
    "pica.PPMS.PPMS_Plotter_GUI",
    "pica.PPMS.PPMS_SeqVisualizer_GUI",
    # cryocon
    "pica.cryocon.Diagnostics_CC34_GUI",
    "pica.cryocon.Sensor_Curve_Viewer_CC34_GUI",
    # keithley
    "pica.keithley.k197a.Monitor_K197A_GUI",
    "pica.keithley.k6221_k197a.Frequency_Scan_K6221_K197A_GUI",
    "pica.keithley.k6221_k197a.IV_AC_K6221_K197A_GUI",
    "pica.keithley.k6221_k197a.RT_AC_K6221_K197A_CC34_T_Sensing_GUI",
    "pica.keithley.k6221_k197a.RT_AC_K6221_K197A_L340_T_Control_GUI",
    "pica.keithley.k6221_k197a.RT_AC_K6221_K197A_L340_T_Sensing_GUI",
    "pica.keithley.k6221_k197a.RT_AC_K6221_K197A_L350_T_Control_GUI",
    "pica.keithley.k6221_k197a.RT_AC_K6221_K197A_L350_T_Sensing_GUI",
    "pica.keithley.k6517b.Pyroelectricity.Polling_K6517B_GUI",
    # keysight
    "pica.keysight.Correction_OpenShort_E4980A_GUI",
    "pica.keysight.Frequency_Scan_E4980A_GUI",
    "pica.keysight.Step_Frequency_Scan_E4980A_GUI",
    "pica.keysight.Step_Frequency_Scan_L340_E4980A_GUI",
    "pica.keysight.Temprature_Scan_E4980A_GUI",
    "pica.keysight.Temprature_Scan_L340_E4980A_GUI",
    "pica.keysight.Temprature_Scan_Passive_E4980A_GUI",
    # lakeshore
    "pica.lakeshore.Sensor_Curve_Loader_L340_L350_GUI",
    "pica.lakeshore.Sensor_Curve_Viewer_L340_GUI",
    "pica.lakeshore.Sensor_Curve_Viewer_L350_GUI",
    # lockin
    "pica.lockin.sr830.AC_Resistivity_K6221_SR830_GUI",
    "pica.lockin.sr830.Comms_SR830_GUI",
    "pica.lockin.sr830.Frequency_Scan_K6221_SR830_GUI",
    "pica.lockin.sr830.IV_AC_K6221_SR830_GUI",
    "pica.lockin.sr830.RT_AC_K6221_SR830_CC34_T_Sensing_GUI",
    "pica.lockin.sr830.RT_AC_K6221_SR830_L340_T_Control_GUI",
    "pica.lockin.sr830.RT_AC_K6221_SR830_L340_T_Sensing_GUI",
    "pica.lockin.sr830.RT_AC_K6221_SR830_L350_T_Control_GUI",
    "pica.lockin.sr830.RT_AC_K6221_SR830_L350_T_Sensing_GUI",
    # novocontrol
    "pica.novocontrol.Frequency_Scan_AlphaAN_32bit_GUI",
    # pfeiffer
    "pica.pfeiffer.Pressure_Log_TPG361_GUI",
    # tektronix
    "pica.tektronix.Function_Gen_AFG3022B_GUI",
    # utils
    "pica.utils.Diagnostics_Comms_Interfaces_GUI",
    "pica.utils.Diagnostics_Python_Env_GUI",
    "pica.utils.Diagnostics_System_Info_GUI",
    "pica.utils.GPIB_Scanner_32bit_GUI",
    "pica.utils.Quick_Calc_GUI",
]

APP_NAME = "PICA Command Line Interface"
VERSION = "1.0.3"
AUTHORS = "Prathamesh Deshmukh, Sudip Mukherjee"
AFFILIATION = "UGC-DAE Consortium for Scientific Research, Mumbai Centre"
LICENSE = "MIT License"
TERMS = """

TERMS OF SERVICE / DISCLAIMER:
This software is provided "as is", without warranty of any kind.
The authors are not responsible for any damage to hardware instruments
(Keithley, Lakeshore, etc.) caused by improper configuration or
misuse of these control scripts.
Always verify safety limits (Compliance, Max Voltage) before execution.
"""

def print_banner():
    print("\033[H\033[J")
    print("="*60)
    print(f"   {APP_NAME} (v{VERSION})")
    print(f"   {AFFILIATION}")
    print("-" * 60)
    print(f"   Authors: {AUTHORS}")
    print(f"   License: {LICENSE}")
    print("="*60)
    print(TERMS)
    print("="*60)
    print("\n")
    print("*"*80)
    print("**Legacy CLI Notice:** The PICA CLI (`pica-cli`) is retained to support legacy headless workflows. While fully functional for specific protocols, this interface is **less frequently maintained** and may not support recent features available in the GUI. We **strongly recommend** new users utilize the PICA GUI for the most complete and supported experience.")
    print("*"*80)
    print("\n")


def find_scripts(base_path):
    scripts = []
    base = Path(base_path)

    # Exclude non-main measurement scripts.
    exclude_list = [
        "GPIB_VISA_InterfaceTest_Simple_Instrument_Control.py",
    ]

    for path in base.rglob("*Instrument_Control.py"):
        if path.name in exclude_list:
            continue

        # Create readable name from filename.
        name = path.stem.replace("_Instrument_Control", "").replace("_", " ")

        # Get relative or absolute path.
        scripts.append((name, str(path)))

    return sorted(scripts)


def run_script(script_path):
    print(f"\n[INFO] Module: {os.path.basename(script_path)}")
    print("[INFO] Enter arguments below, or press ENTER for defaults.")

    args = input("Arguments > ").strip()
    cmd = [sys.executable, script_path]
    if args:
        cmd.extend(args.split())

    try:
        print("-" * 60)
        subprocess.run(cmd)
        print("-" * 60)
        print("\n[SUCCESS] Execution finished.")
    except KeyboardInterrupt:
        print("\n[WARN] Execution interrupted by user.")
    except Exception as e:
        print(f"\n[ERROR] Failed to run script: {e}")

    input("\nPress ENTER to return to menu...")


def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))

    while True:
        print_banner()
        print("Scanning for available measurement modules...\n")

        scripts = find_scripts(current_dir)

        if not scripts:
            print("[ERROR] No 'Instrument_Control.py' scripts found in pica/ directory.")
            sys.exit(1)

        print(f"{'No.':<4} | {'Module Name'}")
        print("-" * 40)

        for idx, (name, path) in enumerate(scripts, 1):
            print(f"{idx:<4} | {name}")

        print("-" * 40)
        print(f"{'Q':<4} | Quit CLI")

        choice = input("\nSelect a module number: ").strip().lower()

        if choice == 'q':
            print("Exiting PICA CLI. Goodbye!")
            sys.exit(0)

        try:
            idx = int(choice)
            if 1 <= idx <= len(scripts):
                selected_name, selected_path = scripts[idx-1]
                run_script(selected_path)
            else:
                print(f"[ERROR] Please enter a number between 1 and {len(scripts)}.")
                time.sleep(1.5)
        except ValueError:
            print("[ERROR] Invalid input. Enter a number or 'Q'.")
            time.sleep(1.5)

if __name__ == "__main__":
    main()
