#-----------------------------------------------------------------------
# Name:       Temperature dependent Voltage measurement  #interfacing Lakeshore350_Temprature_Controller and Keithley 2400 and Keithley 2182
# Purpose: Keithley 2400 gives current and Keithley 2182 measures voltage as temprature sweeps
#
# Author:      Ketan
#
# Created:    22/10/24
# Updated:    01/7/25
# Changes_done:   V3
#-----------------------------------------------------------------------
#Importing packages ----------------------------------

import pymeasure
import numpy as np
import matplotlib.pyplot as plt
from time import sleep
import pyvisa
from pymeasure.instruments.keithley import Keithley2400
import pandas as pd
from datetime import datetime
#import visa
import time
# variable declration

I=[]
Volt=[]
interval = 1
number_of_readings = 2
#base_filename = 'E:/Prathamesh/Python Stuff/Py Pyroelectric/Test_data/Pyro_data_test'
base_filename = 'E:/Prathamesh/Python Stuff/T_dependent_V/test1/'
sample_name = input("enter the sample name: ")

# E:\Prathamesh\Python Stuff\T dependent V\test1
# Create a unique filename

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = f"{base_filename}{sample_name}_{timestamp}.csv"
print(f'Filename: {filename}')
time.sleep(1)  # Delay for 0.5 seconds
# user inputs ------------------------------------------------------------------------------------

current_input=1 # in mili amps
T_final=330 #cutoff
Tset=310 #setpoint
ramp=5 #K/min
range=3 # 3 is 1W , 4 is 100 W
#----------------------------------------------------------------------------------------------------

try:
        #object creation ----------------------------------
        rm1 = pyvisa.ResourceManager()
        keithley_2182= rm1.open_resource("GPIB::7")
        keithley_2182.write("*rst; status:preset; *cls")
        keithley_2400 = Keithley2400("GPIB::4")

        sleep(5)

        ''''
        #user input ----------------------------------
        I_range = float(input("Enter value of I: (in mA , Highest value of Current fror -I to I)"))
        I_step= float(input("Enter steps: (The step size , in mA) "))
        '''
        #filename = input("Enter filename:")

        #initial set up keithley_2400
        keithley_2400.apply_current()        # Sets up to source current
        keithley_2400.source_current_range = 1e-3   # Sets the source current range to 1 mA
        sleep(10)
        keithley_2400.compliance_voltage = 210      # Sets the compliance voltage to 210 V
        keithley_2400.source_current = 0            # Sets the source current to 0 mA
        keithley_2400.enable_source()              # Enables the source output
        sleep(15)

        # current loop voltage measured ------------------------------


        rm1 = pyvisa.ResourceManager()
        print(f"List of Inst: {rm1.list_resources()}\n")

        temp_controller= rm1.open_resource("GPIB::12")
        time.sleep(0.5)
        #------------------------------------------------------------------------

        rm1 = pyvisa.ResourceManager()
        print(rm1.list_resources())
        time.sleep(0.5)
        temp_controller.write('*RST')
        time.sleep(0.5)
        temp_controller.write('*CLS')
        time.sleep(1)
        temp_controller.write('RAMP 2')
        #temp_controller.write('HTRSET 1,1,1,0,1')
        temp_controller.write('SETP 1,310')
        time.sleep(2)
        #temp_controller.write('SETP 2,305')
        temp_controller.write('CLIMIT 1, 310.0, 10, 0')
        time.sleep(2)
        #temp_controller.write("INSET 305")
        temp_controller.write('RANGE 0') # 3 is 1W , 4 is 10 W
        #temp_controller.write('SETP 2,305')


        time.sleep(2)
        #print("Heating data" +str(temp_controller.query('HTRSET?')))
        time.sleep(2)
        #print("Heating data 1" +str(temp_controller.query('SETP?')))

        time.sleep(0.5)

        #------------------------------------------------------------------------
except Exception as e:
    print(f"Initialization error : {e}")

def IV_Measure(cur):

    try:
        elapsed_time = time.time() - start_time

        keithley_2400.ramp_to_current(cur*1e-3)

        sleep(1)
        keithley_2182.write("status:measurement:enable 512; *sre 1")
        keithley_2182.write("sample:count %d" % number_of_readings)
        keithley_2182.write("trigger:source bus")
        keithley_2182.write("trigger:delay %f" % (interval))
        keithley_2182.write("trace:points %d" % number_of_readings)
        keithley_2182.write("trace:feed sense1; feed:control next")
        keithley_2182.write("initiate")
        keithley_2182.assert_trigger()
        sleep(1)
        keithley_2182.wait_for_srq()
        sleep(1)
        voltages = keithley_2182.query_ascii_values("trace:data?")
        sleep(0.25)
        keithley_2182.query("status:measurement?")
        sleep(0.25)
        keithley_2182.write("trace:clear; feed:control next")

        v_avr=sum(voltages) / len(voltages)

        sleep(1)
        #I.append(keithley_2400.current) # actual current in 2400 (in Amps)
        I.append(cur*1e-3)

        sleep(1)
        temperature = temp_controller.query('KRDG? A').strip()
        sleep(2.5)
        #print(temperature)
        Volt.append(v_avr) #voltage avg list
        print(f"{cur*1e-3} A |{elapsed_time:.2f} s| {temperature} K| {v_avr} V")
        #print(str(cur*1e-3)+"  "+str(v_avr))
        sleep(0.5)
        keithley_2182.write("*rst; status:preset; *cls")
        sleep(0.25)

        keithley_2182.clear()
        sleep(1)
        with open(filename, 'a') as file:
            file.write(f"{cur*1e-3},{elapsed_time:.2f} s ,{temperature},{v_avr}\n")
        if float(temperature)>T_final:
                    temp_controller.write('RANGE 0')
                    time.sleep(2)
                    print(f"T larger than {T_final}")
                    Check=False


    except Exception as e:
        print(f"error : {e}")
        Check=False

    except KeyboardInterrupt:
        temp_controller.write('RANGE 0')
        time.sleep(1)
        print("\n Measurement stopped ")
        temp_controller.close()
        print("Lakeshore closed")
        time.sleep(4)


        keithley_2400.shutdown()
        sleep(2)                   # Ramps the current to 0 mA and disables output
        keithley_2182.clear()
        keithley_2182.close()
        print("keithley 2400 and 2182 closed")

        time.sleep(1)
        temp_controller.write('RANGE 0')
        time.sleep(0.5)
        temp_controller.close()
        time.sleep(0.5)
        print("lakeshore closed")
        Check=False


        #keithley.shutdown()  # Ramps the current to 0 mA and disables output





with open(filename, 'w') as file:
        file.write("Current (A),Time (s),Temperature (K),Voltage (V)\n")
#loop1---------------------------------------------
print("Measuremets started \n")
print("Current (A),Time (s),Temperature (K),Voltage (V)\n")

global Check
Check=True
start_time = time.time()

try:
    while Check:
        IV_Measure(current_input)

except Exception as e:
    print(f"Initialization error : {e}")
    Check=False






# turning of instrument ----------------------------
try:

    keithley_2400.shutdown()
    sleep(1)                   # Ramps the current to 0 mA and disables output
    keithley_2182.clear()
    keithley_2182.close()
    time.sleep(1)
    temp_controller.write('RANGE 0')
    temp_controller.close()
    time.sleep(0.5)
except Exception as e:
    print(f"already closed : {e}")

