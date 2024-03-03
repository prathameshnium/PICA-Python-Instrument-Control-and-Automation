#-----------------------------------------------------------------------
# Name:       Pyroelectricity measurement  #interfacing Lakeshore350_Temprature_Controller and Keithley 6517B electrometer
# Purpose:
#
# Author:      Ketan
#
# Created:    3/3/24
# Changes_done:   V1
#-----------------------------------------------------------------------



import visa
import pyvisa
import time
import numpy as np
import pandas as pd
from lakeshore import Model350
from pymeasure.instruments.keithley import Keithley6517B


try:
#initilization of Both Instrumnets

    temp_controller = Model350('GPIB0::1::INSTR')

    identification = temp_controller.query('*IDN?')
    print(f"Instrument identification: {identification}")
    #rm = visa.ResourceManager()
    #temp_controller = rm.open_resource(address)
    print(f"Connected to {temp_controller.query('*IDN?').strip()}")

    temp_controller.write('*RST')
    temp_controller.write('*CLS')

    temp_controller.write('RAMP 1')

    #---------------------------------------

    keithley = Keithley6517B("GPIB::1")
    #keithley.apply_current()  # Sets up to source current
    #keithley.current_range = 10e-3  # Sets the source current range to 10 mA
    #keithley.compliance_voltage = 10  # Sets the compliance voltage to 10 V
    keithley.enable_source()  # Enables the source output
    keithley.measure_current()

    #------------------------------------



    filename = 'temperature_data.txt'
    with open(filename, 'w') as file:
        file.write("Time (s)\tTemperature (K)\tCurrent (A)\n")

except Exception as e:
    print(f"Initialization error : {e}")


def main():

    try:
        start_time = time.time()
        while True:
            elapsed_time = time.time() - start_time
            temperature = temp_controller.query('KRDG? A').strip() # Temperature in K
            current = keithley.current  # Read current in Amps

            print(f"Time: {elapsed_time:.2f} s, Temperature: {temperature} K,Current: {current}")

            with open(filename, 'a') as file:
                file.write(f"{elapsed_time:.2f}\t{temperature}\t{current}\n")

            time.sleep(2)
        temp_controller.close()
        print("Lakeshore closed")

        keithley.clear()
        keithley.reset()
        keithley.shutdown()  # Ramps the current to 0 mA and disables output
        print("keithley closed")

    except Exception as e:
        print(f"error : {e}")
    except KeyboardInterrupt:
        print("\n Measurement stopped ")


if __name__ == "__main__":
    main()
