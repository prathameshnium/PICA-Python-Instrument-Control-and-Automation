# -------------------------------------------------------------------------------
# Name:        Keithley 6517B electrometer
# Purpose:     Current Measurement Backend
# Author:      Prathamesh K Deshmukh
# -------------------------------------------------------------------------------

import time
import pandas as pd
from pymeasure.instruments.keithley import Keithley6517B

I = []
t = []

try:
    keithley = Keithley6517B("GPIB0::27::INSTR")
    time.sleep(0.5)
    keithley.measure_current()
    time.sleep(0.5)

    print("Starting measurement... Press Ctrl+C to stop.")
    start_time = time.time()

    while True:
        elapsed_time = time.time() - start_time
        current = keithley.current
        t.append(elapsed_time)
        I.append(current)
        print(f"Time: {elapsed_time:.2f} | Current: {current} A")
        time.sleep(2)

except KeyboardInterrupt:
    print("\nMeasurement stopped by User.")

    # --- THIS IS THE FIX ---
    if t and I:
        data_df = pd.DataFrame({"Timestamp": t, "Current (A)": I})
        data_df.to_csv("demo_data.dat", index=False)
        print("Data saved to file: demo_data.dat")

    time.sleep(0.5)
    keithley.shutdown()
    print("Keithley closed.")

except Exception as e:
    print(f"Error: {e}")
