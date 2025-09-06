
#-------------------------------------------------------------------------------
# Name:        #interfacing Keithley2400(current source) and Keithley2182(nano_voltmeter)

# Last Update :26-05-23
# Purpose: IV Measurement
#
# Author:      Instrument-DSL
#
# Created:     31/10/2022

# Changes_done:
#-------------------------------------------------------------------------------#Importing packages ----------------------------------

import pymeasure
import numpy as np
import matplotlib.pyplot as plt
from time import sleep
import pyvisa
from pymeasure.instruments.keithley import Keithley2400
import pandas as pd

#object creation ----------------------------------
rm1 = pyvisa.ResourceManager()
keithley_2182= rm1.open_resource("GPIB::7")
keithley_2182.write("*rst; status:preset; *cls")

sleep(5)

I=[]
I1=[]
Volt=[]
interval = 1
number_of_readings = 2

''''
#user input ----------------------------------
I_range = float(input("Enter value of I: (in mA , Highest value of Current fror -I to I)"))
I_step= float(input("Enter steps: (The step size , in mA) "))
'''
filename = input("Enter filename:")


# current loop voltage measured ------------------------------



def IV_Measure(cur):

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
    sleep(1)
# [0.0005,0.001,0.0015,0.002,0.0025,0.003,0.0035,0.004,0.0045,0.005,0.0055,0.006,0.0065,0.007,0.0075,0.008,0.0085,0.009,0.0095,0.01,0.011,0.012,0.013,0.014,0.015,0.016,0.017,0.018,0.019,0.020,0.021,0.022,0.023,0.024,0.025]
#loop1---------------------------------------------
print("In loop 1")
for i1 in np.arange(0,1,0.01) :
    IV_Measure(i1)
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

print(df)

#df.to_csv(r'C:/Users/Instrument-DSL/Desktop/IV_data_26-05-23'+str(filename)+'.txt', index=None, sep='	', mode='w')
df.to_csv(r'C:/Users/Instrument-DSL/Desktop/Swastik_IV/'+str(filename)+'.txt', index=None, sep='	', mode='w')





#graph ploting ----------------------------

plt.plot(Volt,I, marker='o', linestyle='-', color='g', label='Square')
plt.xlabel('V')
plt.ylabel('I')
plt.title('IV curve')
plt.legend('I')
plt.show()

# turning of instrument ----------------------------

sleep(1)                   # Ramps the current to 0 mA and disables output
keithley_2182.clear()
keithley_2182.close()

