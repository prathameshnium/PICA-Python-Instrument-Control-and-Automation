
#-------------------------------------------------------------------------------
# Name:        #interfacing Keithley2400(current source) and Keithley2182(nano_voltmeter)
# Purpose:
#
# Author:      Instrument-DSL
#
# Created:     31/10/2022
# Changes_done:   delay_reduce
# Licence:     <your licence>
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
keithley_2400 = Keithley2400("GPIB::4")

sleep(5)

I=[]
I1=[]
Volt=[]
interval = 1
number_of_readings = 2


#user input ----------------------------------
I_range = float(input("Enter value of I: "))
I_step= float(input("Enter steps: "))
filename = input("Enter filename: ")

#initial set up keithley_2400
keithley_2400.apply_current()               # Sets up to source current
keithley_2400.source_current_range = 1e-3   # Sets the source current range to 1 mA
sleep(10)
keithley_2400.compliance_voltage = 210       # Sets the compliance voltage to 210 V
keithley_2400.source_current = 0            # Sets the source current to 0 mA
keithley_2400.enable_source()              # Enables the source output
sleep(15)

# current loop voltage measured ------------------------------



def IV_Measure(cur):

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

#loop1---------------------------------------------
print("In loop 1")
for i1 in np.arange(0,I_range+I_step,I_step):
    IV_Measure(i1)
#--------------------------------------------------
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
# data saving in file ----------------------------

df=pd.DataFrame()
df['I']=pd.DataFrame(I)
df['V']=pd.DataFrame(Volt)

print(df)

df.to_csv(r'E:/Python/Python output files/IV Output/Test_IV_data_at_RT_'+str(filename)+'.txt', index=None, sep='	', mode='w')

#graph ploting ----------------------------

plt.plot(I, Volt, marker='o', linestyle='-', color='g', label='Square')
plt.xlabel('I')
plt.ylabel('V')
plt.title('IV curve')
plt.legend('I')
plt.show()

# turning of instrument ----------------------------

keithley_2400.shutdown()
sleep(1)                   # Ramps the current to 0 mA and disables output
keithley_2182.clear()
keithley_2182.close()

