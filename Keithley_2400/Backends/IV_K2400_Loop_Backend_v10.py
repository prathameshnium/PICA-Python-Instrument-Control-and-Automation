"""
This script interfaces with a Keithley 2400 SourceMeter to perform Current-Voltage (I-V) characterization of a device.

The script prompts the user for a current range and step size, then configures the
instrument to source a current and measure the corresponding voltage. It sweeps
the current through a predefined pattern from a negative to a positive value and
back, collecting I-V data points. Finally, it saves the collected data to a
tab-separated .txt file and generates a plot of the I-V curve.
"""
#-------------------------------------------------------------------------------

# Name:         #interfacing only Keithley2400(current source) for  IV


# Last Update :27/09/2024

# Purpose: IV Measurement

#

# Author:       Instrument-DSL

#

# Created:      30/09/2024


# Changes_done:Working

#-------------------------------------------------------------------------------#Importing packages ----------------------------------

#-------------------------------------------------------------------------------
# Name:        #interfacing only Keithley2400(current source) for  IV

# Last Update :27/09/2024
# Purpose: IV Measurement
#
# Author:      Instrument-DSL
#
# Created:     30/09/2024

# Changes_done:Working
#-------------------------------------------------------------------------------#Importing packages ----------------------------------

import pymeasure
import numpy as np
import matplotlib.pyplot as plt
from time import sleep
#import pyvisa
from pymeasure.instruments.keithley import Keithley2400
import pandas as pd

#object creation ----------------------------------
#rm1 = pyvisa.ResourceManager()
#keithley_2182= rm1.open_resource("GPIB::7")
#keithley_2182.write("*rst; status:preset; *cls")
keithley_2400 = Keithley2400("GPIB::4")
keithley_2400.disable_buffer()

sleep(10)

I=[]
#I1=[]
Volt=[]
#interval = 1
#number_of_readings = 2

i=0
#user input ----------------------------------
I_range = float(input("Enter value of I: (in micro A , Highest value of Current fror -I to I) "))
I_step= float(input("Enter steps: (The step size , in micro A) "))
filename = input("Enter filename:")


print ("Current (A) || Voltage(V) ")

keithley_2400.apply_current() # Sets up to source current
keithley_2400.source_current_range = 1e-6 # Sets the source current range to 1 mA
keithley_2400.compliance_voltage = 210 # Sets the compliance voltage to 210 V
keithley_2400.source_current = 0 # Sets the source current to 0 mA
keithley_2400.enable_source() # Enables the source output
keithley_2400.measure_voltage()
'''


#initial set up keithley_2400
keithley_2400.apply_current()               # Sets up to source current
keithley_2400.source_current_range = 1e-3   # Sets the source current range to 1 mA
sleep(10)
keithley_2400.compliance_voltage = 210       # Sets the compliance voltage to 210 V
keithley_2400.source_current = 0            # Sets the source current to 0 mA
keithley_2400.enable_source()              # Enables the source output
sleep(15)
keithley_2400.measure_voltage()
sleep(1)
# current loop voltage measured ------------------------------

'''

def IV_Measure(cur):

    keithley_2400.ramp_to_current(cur*1e-6)

    sleep(1.5)
    v_meas=keithley_2400.voltage
    sleep(1)
    #I.append(keithley_2400.current) # actual current in 2400 (in Amps)
    I.append(cur*1e-3)

    Volt.append(v_meas) #voltage




    print(str(cur*1e-6)+"  "+str(Volt[i]))



    '''
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
    keithley_2182.query("status:measurement?")
    keithley_2182.write("trace:clear; feed:control next")

    v_avr=sum(voltages) / len(voltages)

    sleep(1)
    #I.append(keithley_2400.current) # actual current in 2400 (in Amps)
    I.append(cur*1e-3)
    Volt.append(v_avr) #voltage avg list
    print(str(cur*1e-3)+"  "+str(v_avr))
    keithley_2182.write("*rst; status:preset; *cls")

    keithley_2182.clear()

    '''
    sleep(1)

#loop1---------------------------------------------
print("In loop 1")
for i1 in np.arange(0,I_range+I_step,I_step):
    IV_Measure(i1)
    i=i+1
#--------------------------------------------------

'''
#loop2---------------------------------------------
print("In loop 2")
for i2 in np.arange(I_range,0-I_step,-I_step):
    IV_Measure(i2)
#--------------------------------------------------
#loop3---------------------------------------------
print("In loop 3")
for i3 in np.arange(0,-I_range-I_step,-I_step):
    IV_Measure(i3)
#--------------------------------------------------
#loop4---------------------------------------------
print("In loop 4")
for i4 in np.arange(-I_range,0+I_step,I_step):
    IV_Measure(i4)
#--------------------------------------------------
#loop5---------------------------------------------
print("In loop 5")
for i5 in np.arange(0,I_range+I_step,I_step):
    IV_Measure(i5)
#--------------------------------------------------
'''
# data saving in file ----------------------------

df=pd.DataFrame()
df['I']=pd.DataFrame(I)
df['V']=pd.DataFrame(Volt)
print ("Current (A) || Voltage(V) \n")

print(df)

#df.to_csv(r'E:\Prathamesh\Python Stuff\IV Only 2400\'str(filename)+str(filename)'+'.txt', index=None, sep='	', mode='w')
df.to_csv(r'C:/Users/Instrument-DSL/Desktop/LED_IV/'+str(filename)+'.txt', index=None, sep='	', mode='w')


# turning of instrument ----------------------------
sleep(0.5)
keithley_2400.shutdown()
print("keithley_2400.shutdown")
sleep(0.5)               # Ramps the current to 0 mA and disables output
#keithley_2182.clear()
#keithley_2182.close()

#graph ploting ----------------------------

plt.plot(I, Volt, marker='o', linestyle='-', color='g', label='Square')
plt.xlabel('I')
plt.ylabel('V')
plt.title('IV curve')
plt.legend('I')
plt.show()





