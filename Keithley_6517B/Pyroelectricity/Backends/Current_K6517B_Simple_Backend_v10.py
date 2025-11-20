#-------------------------------------------------------------------------------
# Name:        Keithley 6517B electrometer
# Purpose:     Current Measurement Backend
# Author:      Prathamesh K Deshmukh
# Created:     03-03-2024
# Updates:     V1.4 (Fixed DataFrame saving bug)
#-------------------------------------------------------------------------------

import time
import numpy as np
import pandas as pd
import pyvisa
from pymeasure.instruments.keithley import Keithley6517B

I = []
t = []

try:
    # Initialize Instrument
    keithley = Keithley6517B("GPIB0::27::INSTR")
    time.sleep(0.5)

    # Setup Measurement
    keithley.measure_current()
    time.sleep(0.5)

    print("Starting measurement... Press Ctrl+C to stop.")
    start_time = time.time()

    while True:
        elapsed_time = time.time() - start_time
        current = keithley.current  # Read current in Amps
        
        # Store data in lists
        t.append(elapsed_time)
        I.append(current)
        
        print("Time: " + str(elapsed_time) + "\t\t\t|\t\t\t Current: " + str(current) + " A")
        time.sleep(2)

except KeyboardInterrupt:
    # Graceful Exit on Ctrl+C
    print("\nMeasurement stopped by User.")
    
    # --- FIX: Create the DataFrame before saving ---
    if t and I:
        data_df = pd.DataFrame({"Timestamp": t, "Current (A)": I})
        data_df.to_csv("demo_data.dat", index=False)
        print(f"Data saved to file: demo_data.dat")
    else:
        print("No data collected to save.")

    # Shutdown Sequence
    try:
        time.sleep(0.5)
        keithley.shutdown()  # Ramps current to 0 and disables output
        print("Keithley closed.")
    except Exception as e:
        print(f"Error closing instrument: {e}")

except Exception as e:
    print(f"Error with Keithley: {e}")