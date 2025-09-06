#-------------------------------------------------------------------------------
# Name:        GPIB Test
# Purpose:
#
# Author:      Instrument-DSL
#
# Created:     04/03/2024
# Copyright:   (c) Instrument-DSL 2024
# Licence:     <your licence>
#-------------------------------------------------------------------------------
import pyvisa
rm1 = pyvisa.ResourceManager()
print(rm1.list_resources())

temp_controller= rm1.open_resource("GPIB0::13::INSTR")
print(f"ID is : {temp_controller.query('*IDN?')}")

"""
print(f"ID is : {temp_controller.query('*IDN?')}")
print(temp_controller.query('INTYPE?'))

temp_controller.write('RAMP 2')

print(temp_controller.query('KRDG? B').strip())
print(temp_controller.query("HTR?"))

temp_controller.write('HTRSET 2,1,2,0,1')

temp_controller.write("RAMP 1,1,10.5")


print(temp_controller.query("HTRST?"))

"""
