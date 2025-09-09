'''
This script automates a temperature ramp experiment using a Lakeshore Model 350
Temperature Controller.

The program connects to the instrument via GPIB and configures it for a
heating ramp with a setpoint of 310 K. It then continuously logs the elapsed
time and temperature from sensor 'A' to a specified CSV file.

The experiment runs until one of two conditions is met:
1. The temperature automatically exceeds a predefined threshold (302.5 K),
   at which point the heater is turned off and the script terminates.
2. The user manually stops the script with a KeyboardInterrupt (Ctrl+C),
   which also safely shuts down the heater and closes the connection.

Configuration:
- `filename`: Set the full path for the output data CSV file.
- `rm1.open_resource("GPIB1::15::INSTR")`: Change the GPIB address to match
  your instrument's setup.
- `temp_controller.write('SETP 1,310')`: Adjust the temperature setpoint as needed.
'''
#-------------------------------------------------------------------------------
# Name:        #interfacing Lakeshore350_Temprature_Controller
# Purpose:
#
# Author:      Ketan
#
# Created:    3/3/24
# Changes_done:   V1.2
#-------------------------------------------------------------------------------



#import visa
import time
#from lakeshore import Model350
import pyvisa
rm1 = pyvisa.ResourceManager()
print(f"List of Inst: {rm1.list_resources()}\n")

temp_controller= rm1.open_resource("GPIB1::15::INSTR")
time.sleep(0.5)
filename = 'E:\Prathamesh/Python Stuff/ready_to_use/Delta/test-data/Temprature_350.csv'

try:

    #temp_controller = Model350('GPIB0::12::INSTR')

    identification = temp_controller.query('*IDN?')
    print(f"Instrument ID : {identification}")
    #rm = visa.ResourceManager()
    #temp_controller = rm.open_resource(address)
    print(f"Connected to {temp_controller.query('*IDN?').strip()}")
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
    temp_controller.write('RANGE 4')
    #temp_controller.write('SETP 2,305')


    time.sleep(2)
    #print("Heating data" +str(temp_controller.query('HTRSET?')))
    time.sleep(2)
    #print("Heating data 1" +str(temp_controller.query('SETP?')))

    time.sleep(0.5)

    #for ramping the temprature 1K/Min

    #temp_controller.write('RAMP 1')

    with open(filename, 'w') as file:
        file.write("Time (s),Temperature (K)\n")

except Exception as e:
    print(f"Initialization error with Lakeshore  : {e}")


def main():
    #instrument_address = 'GPIB0::1::INSTR'  # Replace with actual address
    #temp_controller = initialize_temperature_controller(instrument_address)


    try:
        start_time = time.time()
        while True:
            elapsed_time = time.time() - start_time
            temperature = temp_controller.query('KRDG? A').strip()
            print(f"Time: {elapsed_time:.2f} s\t\t|\t\t Temperature: {temperature} K")
            if float(temperature)>302.5:
                temp_controller.write('RANGE 0')
                time.sleep(0.5)
                print("T larger than 302.5")
                break


            #print(float(temperature)>302)

            with open(filename, 'a') as file:
                file.write(f"{elapsed_time:.2f},{temperature}\n")

            time.sleep(2)

    except Exception as e:
        print(f"error with Lakeshore  : {e}")
    except KeyboardInterrupt:
        temp_controller.write('RANGE 0')
        time.sleep(1)
        temp_controller.close()
        time.sleep(0.5)
        print("\n\nLakeshore closed")
        print("\n Measurement stopped by User ")


if __name__ == "__main__":
    main()
