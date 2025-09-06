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
print(f"ID is : {keithley_6221.query('*IDN?')}")
#print(dir(keithley_6221))
keithley_6221.write("*rst; status:preset; *cls")

sleep(2)

keithley_6221.write("SOUR:CURR 0.002")

sleep(3)
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

keithley_6221.write("UNIT OHMS")
data_string = keithley_6221.query('SENSe:DATA:FRESh?')
print(f"Raw data: {data_string}")

results = data_string.strip().split(',')
voltage = float(results[0])
resistance = float(results[1])

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
