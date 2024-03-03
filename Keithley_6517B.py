#-------------------------------------------------------------------------------
# Name:        Keithley 6517B electrometer
# Purpose:
#
# Author:      ketan
#
# Created:     03-03-2024
# Copyright:   (c) ketan 2024
# updates: V1.2
#-------------------------------------------------------------------------------

import time
import numpy as np
import pandas as pd
import pyvisa
from pymeasure.instruments.keithley import Keithley6517B


#rm = pyvisa.ResourceManager()
#keithley = rm.open_resource("GPIB::1")


keithley = Keithley6517B("GPIB::1")
#keithley.apply_current()  # Sets up to source current
#keithley.current_range = 10e-3  # Sets the source current range to 10 mA
#keithley.compliance_voltage = 10  # Sets the compliance voltage to 10 V
keithley.enable_source()  # Enables the source output
keithley.measure_current()

I=[]
t=[]

#data_columns = ["Timestamp", "Current (A)"]
#data_df = pd.DataFrame(columns=data_columns)

try:
    while True:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        current = keithley.current  # Read current in Amps
        #data_df = data_df.append({"Timestamp": timestamp, "Current (A)": current}, ignore_index=True)
        t.append(timestamp)
        I.append(current)
        
        print("Time: " +str(timestamp)+"\t\t\t|\t\t\t Current: "+str(current)+" A")

        time.sleep(2)
    print("Measurement stopped...")

data_df.to_csv("demo_data.dat", index=False)
print(f"Data saved to file : demo_data.dat")


keithley.clear()
keithley.reset()

keithley.shutdown()  # Ramps the current to 0 mA and disables output
