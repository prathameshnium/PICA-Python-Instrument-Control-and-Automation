import os
import sys
import subprocess
import time
from pathlib import Path

# Metadata & Terms
APP_NAME = "PICA Command Line Interface"
VERSION = "1.0.1"
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
    """Prints the professional header."""
    print("\033[H\033[J") # Clear screen
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

def find_scripts(base_path):
    """
    Recursively finds all python files ending with 'Instrument_Control.py'.
    Returns a list of tuples: (Display Name, Full Path)
    """
    scripts = []
    base = Path(base_path)
    
    # Exclude development/utility scripts that are not main measurement modules
    exclude_list = [
        "BasicTest_S830_Instrument_Control.py",
        "GPIB_InterfaceTest_Simple_Instrument_Control.py",
    ]

    for path in base.rglob("*Instrument_Control.py"):
        if path.name in exclude_list:
            continue
            
        # Create a readable name from the filename
        # e.g., 'IV_K2400_Loop_Instrument_Control.py' -> 'IV K2400 Loop'
        name = path.stem.replace("_Instrument_Control", "").replace("_", " ")
        
        # Get path relative to the run location for clarity, or absolute
        scripts.append((name, str(path)))
        
    return sorted(scripts)

def run_script(script_path):
    """Runs the selected script using the current python interpreter."""
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
    # Detect where the 'pica' package is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    while True:
        print_banner()
        print("Scannning for available measurement modules...\n")
        
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
                print(f"[ERROR] Please enter a number between 1 and {len(scripts)}")
                time.sleep(1.5)
        except ValueError:
            print("[ERROR] Invalid input. Enter a number or 'Q'.")
            time.sleep(1.5)

if __name__ == "__main__":
    main()