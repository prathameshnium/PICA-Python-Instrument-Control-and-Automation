'''
===============================================================================
 PROGRAM:      Simple GPIB/VISA Connection Check

 PURPOSE:      Finds all connected instruments and prints their ID strings.

 DESCRIPTION:  This is a simple, general-purpose script to verify connections with any
               instrument recognized by VISA (e.g., via GPIB, USB, Ethernet). It
               automatically scans for all connected devices and attempts to query
               each one for its identification string (*IDN?). The script will print
               the ID for each responsive instrument and report an error for any
               that are unreachable, making it a quick diagnostic tool for
               debugging instrument control setups.

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre

 VERSION:      1.0
 LAST EDITED:  04/10/2025
===============================================================================
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
