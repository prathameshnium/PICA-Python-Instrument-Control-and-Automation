#-------------------------------------------------------------------------------
# Name:        Delta test 3:
# Purpose:     Perform a Delta mode measurement with proper shutdown
# Author:      Instrument-DSL
# Created:     01/09/2024
#-------------------------------------------------------------------------------
import pyvisa
from time import sleep

rm1 = pyvisa.ResourceManager()
print(rm1.list_resources())

keithley_6221= rm1.open_resource("GPIB0::13::INSTR")
print(f"ID is : {keithley_6221.query('*IDN?')}")
#-----------------------------------------------------------------------------


# Delta mode measurement parameters
DELTA_CURRENT = 1  # 1 uA test current
DELTA_DELAY = 0.1     # 100 ms delay between current reversals
NUM_READINGS = 10     # Number of delta readings to take


#-----------------------------------------------------------------------------
print(f"ID is : {keithley_6221.query('*IDN?')}")
#print(dir(keithley_6221))
keithley_6221.write("*rst; status:preset; *cls")

sleep(2)
    #keithley_2182.write("trigger:delay %f" % (interval))

#keithley_6221.write("SOUR:CURR  0.002")
keithley_6221.write("SOUR:DELT:HIGH 0.002") # Current in Amps

sleep(5)
keithley_6221.write("SOUR:DELT:ARM")

sleep(3)
keithley_6221.write("INIT:IMM")

sleep(3)

#-----------------------------------------------------------------------------
# Retrieve the data
data_string = keithley_6221.query('TRAC:DATA?')
print(f"Raw data: {data_string}")

keithley_6221.write("UNIT V")


data_string = keithley_6221.query('SENSe:DATA:FRESh?')
print(f"Raw data: {data_string}")
sleep(0.03)
#keithley_6221.write("UNIT OHMS")
data_string = keithley_6221.query('SENSe:DATA:FRESh?')
print(f"Raw data: {data_string}")

results = data_string.strip().split(',')
voltage = float(results[0])
resistance = float(voltage/DELTA_CURRENT)

print("\n" + "="*30)
print("      RESULTS")
print("="*30)
print(f"  Voltage:    {voltage:.15f} V")
print(f"  Resistance: {resistance:.15f} Ohms")
print("="*30)

keithley_6221.write("TRACe:CLEar")
data_string = keithley_6221.query('TRAC:DATA?')
print(f"Raw data: {data_string}")


keithley_6221.write("SOUR:CLE")
#keithley_6221.write("CURRent 0")
keithley_6221.write("OUTPut OFF")
