#-------------------------------------------------------------------------------
# Name:        Delta test 2 :ground up
# Purpose:
#
# Author:      Instrument-DSL
#
# Created:     04/03/2024
# Copyright:   (c) Instrument-DSL 2024
# Licence:     <your licence>
#-------------------------------------------------------------------------------
import pyvisa
from time import sleep

rm1 = pyvisa.ResourceManager()
print(rm1.list_resources())

keithley_6221= rm1.open_resource("GPIB0::13::INSTR")
print(f"ID is : {keithley_6221.query('*IDN?')}")
#-----------------------------------------------------------------------------
keithley_6221.write("*rst; status:preset; *cls")

sleep(2)

keithley_6221.write("SOUR:CURR 0.002")

sleep(3)
keithley_6221.write("SOUR:DELT:ARM")

sleep(3)
keithley_6221.write("INIT:IMM")

sleep(3)
print(f"data: {keithley_6221.query('TRAC:DATA?')}")

sleep(5)

#keithley_6221.write("SOUR:CURR:STAT OFF")

sleep(1)                   # Ramps the current to 0 mA and disables output
