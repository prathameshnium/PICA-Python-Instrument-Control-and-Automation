#-------------------------------------------------------------------------------
# Name:        Keithley6221
# Purpose:
#
# Author:      ketan
#
# Created:     13-11-2024
# Copyright:   (c) ketan 2024
# Licence:     <your licence>
#-------------------------------------------------------------------------------
import time
import numpy as np
import pandas as pd
import pyvisa
from pymeasure.instruments.keithley import Keithley6221

def main():
    try:

        keithley6221 = Keithley6221("GPIB::1")
        keithley6221.clear()
        time.sleep(0.5)

        # Use the keithley as an AC source
        keithley6221.waveform_function = "sine"   # Set a square waveform
        keithley6221.waveform_amplitude = 0.05      # Set the amplitude in Amps (valid  2e-12 to 0.105 Amps)
        keithley6221.waveform_offset = 0            # Set zero offset
        keithley6221.source_compliance = 10         # Set compliance (limit) in V (0.1 [V] to 105 [V])
        keithley6221.waveform_dutycycle = 50        # Set duty cycle of wave in %
        keithley6221.waveform_frequency = 347       # Set the frequency in Hz (valid 1e-3 to 1e5 Hz)
        keithley6221.waveform_ranging = "best"      # Set optimal output ranging
        #keithley6221.waveform_duration_cycles = 100 # Set duration of the waveform
        keithley6221.waveform_duration_set_infinity() #sets Set the waveform duration to infinity.



        # Link end of waveform to Service Request status bit
        keithley6221.operation_event_enabled = 128  # OSB listens to end of wave
        keithley6221.srq_event_enabled = 128        # SRQ listens to OSB

        keithley6221.waveform_arm()                 # Arm (load) the waveform

        keithley6221.waveform_start()               # Start the waveform

        keithley6221.adapter.wait_for_srq()         # Wait for the pulse to finish


    except Exception as e:
    print(f" error with Keithley6221 (current source)  : {e}")
    except KeyboardInterrupt:
    print("\n Measurement stopped ")
    keithley6221.waveform_abort()               # Disarm (unload) the waveform
    time.sleep(0.5)

    keithley6221.shutdown()                     # Disables output
    print("\n Instrument Closed ")


    pass

if __name__ == '__main__':
    main()
