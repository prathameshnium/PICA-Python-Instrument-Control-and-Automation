#-----------------------------------------------------------------------
# Name:       Lakeshore340_Temprature_Controller Continue Measurement
# Purpose: Testing 340
#
# Author:      Ketan
#
# Created:    15/08/24
# Changes_done:   V1
#-----------------------------------------------------------------------


#import visa
import pyvisa
import time
#from lakeshore import Model350
from datetime import datetime
base_filename = 'E:/Prathamesh/Python Stuff/Lakeshore340_tests/Test1'        # Create a unique filename (without_extension)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = f"{base_filename}_{timestamp}.csv"
print(f'Filename: {filename}')
time.sleep(0.01)

try:

    rm1 = pyvisa.ResourceManager()
    print(rm1.list_resources())

    time.sleep(2)
    temp_controller= rm1.open_resource("GPIB::12")
    time.sleep(0.5)

    print(f"\nConnected to {temp_controller.query('*IDN?').strip()}")
    time.sleep(1)
    temp_controller.write('*RST')
    time.sleep(0.5)
    temp_controller.write('*CLS')
    time.sleep(1)
    #---------------------------------------

    #filename = 'temperature_data.txt'
    with open(filename, 'w') as file:
        file.write("Exact_time,Time (s),Temperature (K)\n")

except Exception as e:
    print(f"Initialization error : {e}")


def main():

    try:
        start_time = time.time()
        global Check
        Check=True
        while Check:
            elapsed_time = time.time() - start_time
            temperature = temp_controller.query('KRDG? A').strip() # Temperature in K (from A channel)

            print(f"Time: {elapsed_time:.2f} s  ,  Temperature: {temperature} K")

            with open(filename, 'a') as file:
                file.write(f"{datetime.now()},{elapsed_time:.2f},{temperature}\n")

            time.sleep(1) #old 0.2 (Time after each measurement)

    except Exception as e:
        print(f"error : {e}")
    except KeyboardInterrupt:
        time.sleep(1)
        print("\n Measurement stopped ")
        temp_controller.close()
        print("Lakeshore closed")
        time.sleep(2)


if __name__ == "__main__":
    main()