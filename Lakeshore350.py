
#-------------------------------------------------------------------------------
# Name:        #interfacing Lakeshore350_Temprature_Controller
# Purpose:
#
# Author:      Ketan
#
# Created:    3/3/24
# Changes_done:   V1
#-------------------------------------------------------------------------------#Importing packages ----------------------------------



import visa
import time
from lakeshore import Model350

try:

    temp_controller = Model350('GPIB0::1::INSTR')

    identification = temp_controller.query('*IDN?')
    print(f"Instrument identification: {identification}")
    #rm = visa.ResourceManager()
    #temp_controller = rm.open_resource(address)
    print(f"Connected to {temp_controller.query('*IDN?').strip()}")

    temp_controller.write('*RST')
    temp_controller.write('*CLS')

    temp_controller.write('RAMP 1')

    filename = 'temperature_data.txt'
    with open(filename, 'w') as file:
        file.write("Time (s)\tTemperature (K)\n")

except Exception as e:
    print(f"error with Lakeshore  : {e}")


def main():
    #instrument_address = 'GPIB0::1::INSTR'  # Replace with actual address
    #temp_controller = initialize_temperature_controller(instrument_address)


    try:
        start_time = time.time()
        while True:
            elapsed_time = time.time() - start_time
            temperature = temp_controller.query('KRDG? A').strip()
            print(f"Time: {elapsed_time:.2f} s, Temperature: {temperature} K")

            with open('temperature_data.txt', 'a') as file:
                file.write(f"{elapsed_time:.2f}\t{temperature}\n")

            time.sleep(2)
        temp_controller.close()
        print("Lakeshore closed")
    except Exception as e:
        print(f"error with Lakeshore  : {e}")
    except KeyboardInterrupt:
        print("\n Measurement stopped ")


if __name__ == "__main__":
    main()
