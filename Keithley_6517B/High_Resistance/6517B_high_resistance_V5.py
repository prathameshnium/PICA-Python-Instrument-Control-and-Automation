#-----------------------------------------------------------------------
# Name:         Keithley 6517B High Resistance Measurement (V2.2 - Corrected)
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
TEST_VOLTAGE = 10  # Voltage to apply, in Volts
SETTLING_DELAY_S = 0.5   # Delay time in seconds after turning on voltage

# ----------------------------------------------------------------------

# Initialize instrument variable to None for the finally block
keithley = None

try:
    # --- 2. CONNECT TO INSTRUMENT ---
    print(f"Attempting to connect to instrument at: {VISA_ADDRESS}")
    keithley = Keithley6517B(VISA_ADDRESS)
    print(f"Successfully connected to: {keithley.id}")

    # --- 3. CONFIGURE MEASUREMENT ---
    print("\nConfiguring instrument for resistance measurement...")
    keithley.reset()
    # Set the function to resistance measurement. This also sets the instrument
    # to the source-voltage, measure-current mode, which is necessary for the
    # zero correction to be applied to the ammeter.
    keithley.measure_resistance()

    # --- 4. PERFORM ZERO CHECK & CORRECTION ---
    print("\nStarting zero correction procedure...")
    time.sleep(5)

    # Step 1: Enable Zero Check.
    print("Step 1: Enabling Zero Check mode...")
    keithley.write(':SYSTem:ZCHeck ON')
    time.sleep(5) # Allow time for relays to switch

    # Step 2: Acquire the zero measurement.
    print("Step 2: Acquiring zero correction value...")
    keithley.write(':SYSTem:ZCORrect:ACQuire')
    # This command can take a few seconds. The manual suggests waiting.
    time.sleep(5)

    # Step 3: Disable Zero Check.
    print("Step 3: Disabling Zero Check mode...")
    keithley.write(':SYSTem:ZCHeck OFF')
    time.sleep(5)

    # Step 4: Enable Zero Correction for subsequent measurements.
    print("Step 4: Enabling Zero Correction...")
    keithley.write(':SYSTem:ZCORrect ON')
    time.sleep(5)

    # --- 5. SETUP AND PERFORM MEASUREMENT ---
    print("\nSetting up measurement parameters...")
    # Set the source voltage
    keithley.source_voltage = TEST_VOLTAGE
    # Set integration rate for noise reduction (1 PLC is a good starting point)
    keithley.resistance_nplc = 1

    print(f"Configuration complete. Applying {TEST_VOLTAGE} V...")
    keithley.enable_source()
    print(f"Voltage source ON. Waiting {SETTLING_DELAY_S}s for settling...")

    # A delay is CRITICAL for high-resistance measurements to stabilize
    time.sleep(SETTLING_DELAY_S)

    print("Taking reading...")
    resistance = keithley.resistance

    # --- 6. DISPLAY RESULT ---
    # Check for an over-range condition (a very large number)
    if resistance > 1e37:
        print("\n--- Measurement Complete ---")
        print(f"Result: OVER RANGE (Resistance is too high to measure: {resistance:.4e} Ω)")
    else:
        print("\n--- Measurement Complete ---")
        print(f"Measured Resistance: {resistance:.4e} Ω")

except VisaIOError:
    print(f"\n[VISA Connection Error]")
    print(f"Could not connect to the instrument at '{VISA_ADDRESS}'.")
    print("Please check the address, cable connections, and if the instrument is on.")
except Exception as e:
    print(f"\n[An Unexpected Error Occurred] Details: {e}")

finally:
    # --- 7. SAFELY SHUT DOWN ---
    # This block ALWAYS runs, ensuring the instrument is left in a safe state
    if keithley:
        print("\nShutting down instrument...")
        keithley.shutdown()
        print("Voltage source OFF and instrument is safe.")
