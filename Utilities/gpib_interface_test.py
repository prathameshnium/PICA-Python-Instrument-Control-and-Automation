# Name:         Simple GPIB/VISA Connection Check
# File:         gpib_interface_test.py
# Purpose:      Finds all connected instruments and prints their ID strings.

'''
File:         gpib_interface_test.py
Author:       Prathamesh Deshmukh
Date:         10/09/2025
Licence:      MIT

Description:
This is a simple, general-purpose script to verify connections with any
instrument recognized by VISA (e.g., via GPIB, USB, Ethernet). It automatically
scans for all connected devices and attempts to query each one for its
identification string (*IDN?).

The script will print the ID for each responsive instrument and report an
error for any that are unreachable, making it a quick diagnostic tool for
debugging instrument control setups.

Dependencies:
- pyvisa: A Python package for instrument control.
- A VISA backend must be installed (e.g., NI-VISA, Keysight VISA).

Usage:
Run the script directly from your terminal. No code modification is needed,
as it will automatically find and test all connected instruments.
  
  python simple_gpib_check.py
'''

import pyvisa

# Initialize the tool to find connected instruments
rm = pyvisa.ResourceManager()
instrument_addresses = rm.list_resources()

# Check if any instruments were found
if not instrument_addresses:
    print("No instruments found. Check connections and VISA installation.")
else:
    print(f"Found {len(instrument_addresses)} instrument(s). Checking them now...\n")

    # Loop through each instrument and try to get its ID
    for address in instrument_addresses:
        try:
            # Connect to the instrument (connection closes automatically)
            with rm.open_resource(address) as instrument:
                instrument.timeout = 2000  # Set a 2-second timeout
                idn = instrument.query('*IDN?')
                print(f"Address: {address}\n  ID: {idn.strip()}\n")
        except Exception as e:
            # If something goes wrong, print an error and continue
            print(f"Address: {address}\n  Error: Could not get ID. {e}\n")

print("Scan complete.")
