#-------------------------------------------------------------------------------
# Name:         Combined Delta and Lakeshore Measurement
# Purpose:      Perform a Delta mode measurement with a Keithley 6221 while
#               simultaneously monitoring temperature with a Lakeshore 350.
# Author:       Prathamesh
# Created:      09/09/2025
# Version:      1.0
#-------------------------------------------------------------------------------
import pyvisa
import time

# --- Configuration ---
# Centralized configuration for easy modification.
KEITHLEY_6221_ADDRESS = "GPIB0::13::INSTR"
LAKESHORE_350_ADDRESS = "GPIB1::15::INSTR"
DELTA_CURRENT = 0.002  # Delta current in Amps
OUTPUT_FILENAME = 'Delta_Lakeshore_Data.csv'
TEMPERATURE_LIMIT = 302.5 # Temperature in Kelvin to stop the measurement

def run_combined_measurement():
    """
    Initializes, configures, and runs a synchronized measurement loop for
    a Keithley 6221 and a Lakeshore 350, ensuring a clean shutdown.
    """
    keithley_6221 = None
    lakeshore_350 = None

    try:
        # --- 1. Initialization ---
        print("--- Initializing Instruments ---")
        rm = pyvisa.ResourceManager()
        print(f"Available VISA resources: {rm.list_resources()}")

        # Connect to Keithley 6221
        print(f"Connecting to Keithley 6221 at {KEITHLEY_6221_ADDRESS}...")
        keithley_6221 = rm.open_resource(KEITHLEY_6221_ADDRESS)
        keithley_6221.timeout = 10000  # 10-second timeout
        keithley_6221.write_termination = '\n'
        keithley_6221.read_termination = '\n'
        print(f"Connected to: {keithley_6221.query('*IDN?').strip()}")

        # Connect to Lakeshore 350
        print(f"Connecting to Lakeshore 350 at {LAKESHORE_350_ADDRESS}...")
        lakeshore_350 = rm.open_resource(LAKESHORE_350_ADDRESS)
        print(f"Connected to: {lakeshore_350.query('*IDN?').strip()}")
        print("-" * 30)

        # --- 2. Instrument Configuration ---
        print("\n--- Configuring Instruments ---")
        # Configure Keithley 6221 for Delta Mode
        print("Configuring Keithley 6221...")
        keithley_6221.write("*rst; status:preset; *cls")
        time.sleep(1)
        keithley_6221.write("UNIT V")
        keithley_6221.write(f"SOUR:DELT:HIGH {DELTA_CURRENT}")
        keithley_6221.write("SOUR:DELT:ARM")
        time.sleep(1)
        keithley_6221.write("INIT:IMM")

        # Configure Lakeshore 350 for Temperature Control
        print("Configuring Lakeshore 350...")
        lakeshore_350.write('*RST')
        time.sleep(0.5)
        lakeshore_350.write('*CLS')
        time.sleep(1)
        # Add your specific Lakeshore setup commands here
        lakeshore_350.write('RAMP 2')
        lakeshore_350.write('SETP 1,310')
        lakeshore_350.write('RANGE 4')
        print("-" * 30)

        # --- 3. Data Logging Setup ---
        with open(OUTPUT_FILENAME, 'w', newline='') as file:
            file.write("Time (s),Current (A),Voltage (V),Resistance (Ohm),Temperature (K)\n")

        # --- 4. Measurement Loop ---
        print("\n--- Measurement Started ---")
        print("Press Ctrl+C to stop the measurement.")
        print("-" * 70)
        header = f"{'Time (s)':<12} | {'Current (A)':<12} | {'Voltage (V)':<15} | {'Resistance (Ohm)':<18} | {'Temperature (K)'}"
        print(header)
        print("-" * len(header))

        start_time = time.time()
        while True:
            # Get elapsed time
            elapsed_time = time.time() - start_time

            # Query Keithley 6221
            raw_data = keithley_6221.query('SENSe:DATA:FRESh?')
            data_points = raw_data.strip().split(',')
            voltage = float(data_points[0])
            resistance = voltage / DELTA_CURRENT

            # Query Lakeshore 350
            temperature_str = lakeshore_350.query('KRDG? A').strip()
            temperature = float(temperature_str)

            # Print to console
            print(f"{elapsed_time:<12.2f} | {DELTA_CURRENT:<12.3f} | {voltage:<15.9f} | {resistance:<18.9f} | {temperature:.3f}")

            # Append to file
            with open(OUTPUT_FILENAME, 'a', newline='') as file:
                file.write(f"{elapsed_time:.2f},{DELTA_CURRENT},{voltage},{resistance},{temperature}\n")

            # Check for temperature limit
            if temperature > TEMPERATURE_LIMIT:
                print(f"\nTemperature limit of {TEMPERATURE_LIMIT} K reached. Stopping measurement.")
                break

            # Wait before next measurement
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n--- User pressed Ctrl+C. Stopping measurement. ---")
    except pyvisa.errors.VisaIOError as e:
        print(f"\n--- VISA Communication Error: {e} ---")
        print("Please check instrument connections and power.")
    except Exception as e:
        print(f"\n--- An unexpected error occurred: {e} ---")

    finally:
        # --- 5. Graceful Shutdown ---
        print("\n--- Shutting down instruments ---")
        if keithley_6221:
            try:
                print("Shutting down Keithley 6221...")
                keithley_6221.clear()
                time.sleep(0.1)
                keithley_6221.write("SOUR:CLE:IMM") # Abort measurement and turn source off
                keithley_6221.write("*rst")
                keithley_6221.close()
                print("Keithley 6221 connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"Warning during Keithley 6221 shutdown: {e}")
        if lakeshore_350:
            try:
                print("Shutting down Lakeshore 350...")
                lakeshore_350.write('RANGE 0') # Turn off heater
                time.sleep(0.5)
                lakeshore_350.close()
                print("Lakeshore 350 connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"Warning during Lakeshore 350 shutdown: {e}")

        print("\n--- Measurement Complete ---")

# --- Main execution block ---
if __name__ == "__main__":
    run_combined_measurement()
