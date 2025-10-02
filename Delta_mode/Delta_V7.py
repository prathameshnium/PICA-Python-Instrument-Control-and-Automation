#-------------------------------------------------------------------------------
# Name:         Delta Mode Measurement (Improved)
# Purpose:      Perform a Delta mode measurement with robust error handling
#               and a guaranteed graceful shutdown.
# Author:       keithley_6221-DSL
# Created:      01/09/2024
#-------------------------------------------------------------------------------
import pyvisa
import time

# --- Configuration ---
# Use constants for easier changes later.
KEITHLEY_6221_ADDRESS = "GPIB0::13::INSTR"
DELTA_CURRENT = 1*10**(-11)  # Current in Amps
# Example 0.001

def run_delta_measurement():
    """
    Initializes the Keithley 6221, runs the measurement loop,
    and ensures a clean shutdown.
    """
    keithley_6221 = None  # Initialize to None to ensure 'finally' block doesn't fail
    voltages = []

    try:
        # --- 1. Initialization ---
        rm = pyvisa.ResourceManager()
        print(f"Available VISA resources: {rm.list_resources()}")

        keithley_6221 = rm.open_resource(KEITHLEY_6221_ADDRESS)
        keithley_6221.timeout = 10000  # Set a 10-second timeout (in milliseconds)
        keithley_6221.write_termination = '\n'
        keithley_6221.read_termination = '\n'

        print(f"Connected to: {keithley_6221.query('*IDN?').strip()}")

        # --- 2. keithley_6221 Configuration ---
        print("Configuring Keithley 6221 for Delta Mode...")
        keithley_6221.write("*rst; status:preset; *cls")  # Reset and clear status
        keithley_6221.write("CURR:COMP 50")  # Reset and clear status


        time.sleep(1)
        keithley_6221.write("UNIT V")                      # Set measurement unit to Volts
        keithley_6221.write(f"SOUR:DELT:HIGH {DELTA_CURRENT}") # Set delta current
        keithley_6221.write("SOUR:DELT:ARM")               # Arm the delta measurement sequence
        time.sleep(1)                                   # Give it a moment to arm
        keithley_6221.write("INIT:IMM")                    # Start (initiate) the measurement

        print("\n--- Measurement Started ---")
        print("Press Ctrl+Alt_F9 to stop the measurement.")
        start_time = time.time()

        # --- 3. Measurement Loop ---


        # --- 3. Measurement Loop (Corrected) ---
        while True:
            # Query for the latest data point.
            # The 6221 in delta mode returns a string like "Voltage,Resistance"
            raw_data = keithley_6221.query('SENSe:DATA:FRESh?')

            # Split the string at the comma to get a list of values
            data_points = raw_data.strip().split(',')

            # The first item is Voltage, the second is Resistance
            voltage = float(data_points[0])
            resistance = float(voltage/DELTA_CURRENT) # Use the resistance calculated by the keithley_6221

            voltages.append(voltage) # Still good to log the raw voltage

            elapsed_time = time.time() - start_time

            # Print the formatted output using the keithley_6221's resistance value
            print(f"Time: {elapsed_time} s |Current:{DELTA_CURRENT} A | Voltage: {voltage} V | Resistance: {resistance} Ohms")

            # Control how often you poll for a new reading
            time.sleep(1)


    except KeyboardInterrupt:
        # This block catches Ctrl+C
        print("\n--- User pressed Ctrl+C. Stopping measurement. ---")

    except pyvisa.errors.VisaIOError as e:
        # This block catches communication errors (e.g., timeout)
        print(f"\n--- VISA Communication Error: {e} ---")
        print("Please check keithley_6221 connection and ensure it is not in a remote-lock state.")

    except Exception as e:
        # This catches any other unexpected errors
        print(f"\n--- An unexpected error occurred: {e} ---")

    finally:
            # --- 4. Graceful Shutdown (Most Robust) ---
            # This block will ALWAYS run.
            if keithley_6221:
                print("\nShutting down keithley_6221...")
                try:
                    # 1. Clear the VISA/GPIB interface. THIS IS THE KEY FIX.
                    # This clears the keithley_6221's I/O buffers and resets its parser.
                    keithley_6221.clear()
                    time.sleep(0.1)
                    keithley_6221.write("*rst; status:preset; *cls")

                    print("VISA interface cleared.")

                    # 2. Turn off the current source. This is the most critical safety step.
                    #keithley_6221.write("SOUR:CLE:IMM")
                    print("keithley_6221 source is OFF.")
                    time.sleep(0.1)

                except pyvisa.errors.VisaIOError as e:
                    # If any command fails after a clear, there might be a deeper issue,
                    # but we print it without crashing the script.
                    print(f"Warning during shutdown sequence: {e}")

                # 4. Finally, close the connection to the keithley_6221.
                #keithley_6221.close()
                print("keithley_6221 connection closed.")

            print("--- Measurement Complete ---")
# --- Main execution block ---
if __name__ == "__main__":
    run_delta_measurement()
