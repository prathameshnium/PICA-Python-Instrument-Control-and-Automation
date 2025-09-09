# Name:        GPIB Test

'''
File:         gpib_temp_controller_interface.py
Author:       Prathamesh Deshmukh
Date:         04/03/2024
Licence:      MIT

Description:
This script provides a basic framework for communicating with and controlling a
temperature controller via a GPIB interface. It utilizes the pyvisa library
to discover connected instruments, establish a connection to a specific device,
and send Standard Commands for Programmable Instruments (SCPI) to query its
status and configure its settings, such as temperature ramps and heater output.

Dependencies:
- pyvisa: A Python package for instrument control.
- A VISA backend must be installed (e.g., NI-VISA).

Usage:
Modify the resource string in the rm1.open_resource() call
(e.g., "GPIB0::13::INSTR") to match the address of your specific instrument.
Run the script to perform the programmed control and query operations.
'''

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
