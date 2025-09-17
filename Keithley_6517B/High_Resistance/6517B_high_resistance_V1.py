import pyvisa
import time
import numpy as np
import pandas as pd
#from lakeshore import Model350
from pymeasure.instruments.keithley import Keithley6517B
from datetime import datetime

keithley = Keithley6517B("GPIB1::27::INSTR")
time.sleep(2)
print(f"\nConnected to {keithley.id}")
print(f"\n-----------------------------------------------------\n")


keithley.clear()
time.sleep(1)



time.sleep(1)
keithley.apply_voltage() # Sets up to source current
time.sleep(0.5)

keithley.source_voltage_range = 10 # Sets the source voltage
time.sleep(0.5)

keithley.source_voltage = 1 # Sets the source voltage to 20 V
time.sleep(0.5)

keithley.enable_source() # Enables the source output
time.sleep(0.5)

keithley.measure_resistance() # Sets up to measure resistance
time.sleep(0.5)

keithley.ramp_to_voltage(2) # Ramps the voltage to 50 V
time.sleep(0.5)


print(keithley.resistance) # Prints the resistance in Ohms
time.sleep(0.5)
keithley.measure_current()
print(keithley.current) # Prints the resistance in Ohms
time.sleep(0.5)
print(dir(keithley))
#print(keithley.charge) # Prints the resistance in Ohms

time.sleep(0.5)


time.sleep(1)
keithley.clear()
time.sleep(1)
keithley.shutdown()  # Ramps the current to 0 mA and disables output
print("keithley closed")
