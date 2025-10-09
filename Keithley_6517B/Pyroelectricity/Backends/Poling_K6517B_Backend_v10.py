#-------------------------------------------------------------------------------
# Name:        Keithley 6517B Poling
# Purpose:
#
# Author:      Prathamesh K Deshmukh
#
# Created:     03-03-2024

# updates: V1.3
#-------------------------------------------------------------------------------

import time
from pymeasure.instruments.keithley import Keithley6517B


try:
    keithley = Keithley6517B( "GPIB0::27::INSTR")
    time.sleep(0.5)
    #keithley.apply_voltage() # Sets up to source current
    #keithley.source_voltage_range = 20 # Sets the source voltage
    # range to 200 V
    #keithley.source_voltage = 20 # Sets the source voltage to 20 V
    time.sleep(0.5)
     #keithley.enable_source() # Enables the source output
    time.sleep(0.5)
    keithley.source_voltage = 100
    #keithley.measure_resistance() # Sets up to measure resistance
    #keithley.ramp_to_voltage(10) # Ramps the voltage to 50 V
    #print(keithley.resistance) # Prints the resistance in Ohms
    time.sleep(20)
    print(f'Current is {(keithley.current)}')


    # and disables outpu
except Exception as e:
    print(f"error with Keithley6517B  : {e}")

except KeyboardInterrupt:
    #keithley.source_voltage = 0 # Ramps the voltage to 50 V
    keithley.shutdown() # Ramps the voltage to 0 V
    print("\n Poling stopped...")

    time.sleep(0.5)
    keithley.clear()
    time.sleep(0.5)
    keithley.reset()

    keithley.shutdown()  # Ramps the current to 0 mA and disables output


except Exception as e:
    print(f"error with keithley : {e}")
    keithley.shutdown()
