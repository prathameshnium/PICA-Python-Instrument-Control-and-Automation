#-----------------------------------------------------------------------
# Name:         Keithley 6517B High Resistance Measurement (V2.1 - User Style)
# Purpose:      A robust, single-file script for a specific resistance measurement.
# Author:       Prathamesh
# Created:      15/09/2025
#-----------------------------------------------------------------------

import time
from pymeasure.instruments.keithley import Keithley6517B
from pyvisa.errors import VisaIOError

# --- 1. USER CONFIGURATION ---
# Set your measurement parameters here
VISA_ADDRESS = "GPIB1::27::INSTR"
TEST_VOLTAGE = 100.0  # Voltage to apply, in Volts
SETTLING_DELAY_S = 5   # Delay time in seconds after turning on voltage

# ----------------------------------------------------------------------

# Initialize instrument variable to None for the finally block
keithley = None

try:
    # --- 2. CONNECT TO INSTRUMENT ---
    print(f"Attempting to connect to instrument at: {VISA_ADDRESS}")
    keithley = Keithley6517B(VISA_ADDRESS)
    print(f"Successfully connected to: {keithley.id}")

    # --- 3. CONFIGURE MEASUREMENT ---
    print("Configuring instrument...")
    keithley.reset()                # Reset to a known default state
    keithley.apply_voltage()        # Set instrument to source voltage, measure current
    keithley.source_voltage = TEST_VOLTAGE
    keithley.resistance_autoranging() # Enable autorange
    keithley.resistance_nplc = 1      # Set integration rate for noise reduction


    # --- 4. PERFORM MEASUREMENT ---
    print(f"Configuration complete. Applying {TEST_VOLTAGE} V...")
    keithley.enable_source()
    print(f"Voltage source ON. Waiting for {SETTLING_DELAY_S}s to settle...")

    # A delay is CRITICAL for high-resistance measurements to stabilize
    time.sleep(SETTLING_DELAY_S)

    print("Taking reading...")
    resistance = keithley.resistance

    # --- 5. DISPLAY RESULT ---
    # Check for an over-range condition (a very large number)
    if resistance > 1e37:
        print("\n--- Measurement Complete ---")
        print("Result: OVER RANGE (Resistance is too high to measure)")
    else:
        print("\n--- Measurement Complete ---")
        print(f"Measured Resistance: {resistance:.4e} Ω")

except VisaIOError:
    print("\n[VISA Connection Error]")
    print(f"Could not connect to the instrument at '{VISA_ADDRESS}'.")
    print("Please check the address, cable connections, and if the instrument is on.")
except Exception as e:
    print(f"\n[An Unexpected Error Occurred] Details: {e}")

finally:
    # --- 6. SAFELY SHUT DOWN ---
    # This block ALWAYS runs, ensuring the instrument is left in a safe state
    if keithley:
        print("\nShutting down instrument...")
        keithley.shutdown()
        print("Voltage source OFF and instrument is safe.")
