#-----------------------------------------------------------------------
# Name:       Pyroelectricity measurement  #interfacing Lakeshore350_Temprature_Controller and Keithley 6517B electrometer
# Purpose:
#
# Author:      Ketan
#
# Created:    3/3/24
# Changes_done:   V3
#-----------------------------------------------------------------------


#import visa
import pyvisa
import time
import numpy as np
import pandas as pd
#from lakeshore import Model350
from pymeasure.instruments.keithley import Keithley6517B
from datetime import datetime
base_filename = 'E:/Prathamesh/Python Stuff/Py Pyroelectric/Test_data/Pyro_data_test'
# Create a unique filename

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = f"{base_filename}_{timestamp}.csv"
print(f'Filename: {filename}')
time.sleep(0.01)  # Delay for 0.5 seconds

T_final=360 #cutoff
Tset=312 #setpoint
ramp=5 #K/min
range=1 # 3 is 1W , 4 is 100 W

try:

    rm1 = pyvisa.ResourceManager()
    print(rm1.list_resources())

    time.sleep(1)
    temp_controller= rm1.open_resource("GPIB::12")
    #initilization of Both Instrumnets

    #temp_controller = Model350('GPIB0::1::INSTR')

    #identification = temp_controller.query('*IDN?')
    #print(f"Instrument identification: {identification}")
    #rm = visa.ResourceManager()
    #temp_controller = rm.open_resource(address)
    print(f"\nConnected to {temp_controller.query('*IDN?').strip()}")
    time.sleep(1)


    temp_controller.write('*RST')
    time.sleep(0.5)
    temp_controller.write('*CLS')
    time.sleep(0.5)
    temp_controller.write(f'RAMP 1, 1, {ramp}') #third parameter is range in K/min
    time.sleep(0.5)
    temp_controller.write(f'RANGE {range}') # 3 is 1W , 4 is 100 W
    time.sleep(0.5)
    #temp_controller.write('MOUT 1, 100')
    #print(temp_controller.query('CLIMIT?'))
    time.sleep(1)
    temp_controller.write(f'SETP 1,{Tset}')
    time.sleep(0.5)
    #temp_controller.write('SETP 2,305')
    time.sleep(0.5)
    temp_controller.write(f'CLIMIT 1, {Tset}, 10, 0')
    time.sleep(0.5)
    #temp_controller.write('CLIMIT 1, 307')


    #---------------------------------------

    keithley = Keithley6517B("GPIB0::27::INSTR")
    time.sleep(2)
    print(f"\nConnected to {keithley.id}")
    print(f"\n-----------------------------------------------------\n")

    time.sleep(1)
    #keithley.apply_current()  # Sets up to source current
    #keithley.current_range = 10e-3  # Sets the source current range to 10 mA
    #keithley.compliance_voltage = 10  # Sets the compliance voltage to 10 V
    #keithley.enable_source()  # Enables the source output
    keithley.measure_current()

    #------------------------------------



    #filename = 'temperature_data.txt'
    with open(filename, 'w') as file:
        file.write("Time (s),Temperature (K),Current (A)\n")

except Exception as e:
    print(f"Initialization error : {e}")


def main():

    try:
        start_time = time.time()
        global Check
        Check=True
        while Check:
            elapsed_time = time.time() - start_time
            temperature = temp_controller.query('KRDG? A').strip() # Temperature in K
            current = keithley.current  # Read current in Amps


            print(f"Time: {elapsed_time:.2f} s, Temperature: {temperature} K,Current: {current}")

            with open(filename, 'a') as file:
                file.write(f"{elapsed_time:.2f},{temperature},{current}\n")
            if float(temperature)>T_final:
                temp_controller.write('RANGE 0')
                time.sleep(2)
                print(f"T larger than {T_final}")
                Check=False

            time.sleep(1) #old 0.2

    except Exception as e:
        print(f"error : {e}")
    except KeyboardInterrupt:
        temp_controller.write('RANGE 0')
        time.sleep(1)
        print("\n Measurement stopped ")
        temp_controller.close()
        print("Lakeshore closed")
        time.sleep(1)
        keithley.clear()
        #keithley.reset()

        time.sleep(2)
        keithley.shutdown()  # Ramps the current to 0 mA and disables output
        print("keithley closed")




if __name__ == "__main__":
    main()
