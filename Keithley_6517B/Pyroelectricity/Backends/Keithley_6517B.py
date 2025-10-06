#-------------------------------------------------------------------------------
# Name:        Keithley 6517B electrometer
# Purpose:
#
# Author:      ketan
#
# Created:     03-03-2024
# Copyright:   (c) ketan 2024
# updates: V1.3
#-------------------------------------------------------------------------------

import time
import numpy as np
import pandas as pd
import pyvisa
from pymeasure.instruments.keithley import Keithley6517B


I=[]
t=[]
#rm = pyvisa.ResourceManager()
#keithley = rm.open_resource("GPIB::1")

try:

    keithley = Keithley6517B("GPIB0::27::INSTR")
    time.sleep(0.5)

    #keithley.apply_current()  # Sets up to source current
    #keithley.current_range = 10e-3  # Sets the source current range to 10 mA
    #keithley.compliance_voltage = 10  # Sets the compliance voltage to 10 V
    #keithley.enable_source()  # Enables the source output
    keithley.measure_current()
    time.sleep(0.5)


    #data_columns = ["Timestamp", "Current (A)"]
    #data_df = pd.DataFrame(columns=data_columns)
    start_time = time.time()

    while True:
        elapsed_time = time.time() - start_time
        #timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        current = keithley.current  # Read current in Amps
        #data_df = data_df.append({"Timestamp": elapsed_time, "Current (A)": current}, ignore_index=True)
        t.append(elapsed_time)
        I.append(current)
        print("Time: " +str(elapsed_time)+"\t\t\t|\t\t\t Current: "+str(current)+" A")

        time.sleep(2)
    print("Measurement stopped...")

    data_df.to_csv("demo_data.dat", index=False)
    print(f"Data saved to file : demo_data.dat")




except Exception as e:
    print(f"error with keithley : {e}")

except KeyboardInterrupt:
    time.sleep(0.5)

    keithley.clear()
    #keithley.reset()
    time.sleep(0.5)

    keithley.shutdown()  # Ramps the current to 0 mA and disables output
    print("\n\nkeithley closed")
    print("\n Measurement stopped by User ")
