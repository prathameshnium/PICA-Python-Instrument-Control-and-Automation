"""
This script interfaces with a Keithley 2400 SourceMeter to perform Current-Voltage (I-V) characterization of a device.

The script prompts the user for a current range and step size, then configures the
instrument to source a current and measure the corresponding voltage. It sweeps
the current through a predefined pattern from a negative to a positive value and
back, collecting I-V data points. Finally, it saves the collected data to a
tab-separated .txt file and generates a plot of the I-V curve.
"""
# -------------------------------------------------------------------------------

# Name:         #interfacing only Keithley2400(current source) for  IV


# Last Update :27/09/2024

# Purpose: IV Measurement

#

# Author:       Instrument-DSL

#

# Created:      30/09/2024


# Changes_done:Working

# ------------------------------------------------------------------------

# -------------------------------------------------------------------------------
# Name:        #interfacing only Keithley2400(current source) for  IV

# Last Update :27/09/2024
# Purpose: IV Measurement
#
# Author:      Instrument-DSL
#
# Created:     30/09/2024

# Changes_done:Working
# ------------------------------------------------------------------------

import os
import numpy as np
import matplotlib.pyplot as plt
from time import sleep
# import pyvisa
from pymeasure.instruments.keithley import Keithley2400
import pandas as pd


def main():
    """
    Main function to run the I-V sweep measurement.
    """
    # object creation ----------------------------------
    keithley_2400 = Keithley2400("GPIB::4")
    keithley_2400.disable_buffer()
    sleep(2)

    i = 0
    current_values = []
    Volt = []

    # user input ----------------------------------
    I_range = float(input("Enter value of I: (in micro A, Highest value of Current from -I to I) "))
    I_step = float(input("Enter steps: (The step size, in micro A) "))
    filename = input("Enter filename:")

    print("Current (A) || Voltage(V) ")

    keithley_2400.apply_current()
    keithley_2400.source_current_range = 1e-6
    keithley_2400.compliance_voltage = 210
    keithley_2400.source_current = 0
    keithley_2400.enable_source()
    keithley_2400.measure_voltage()

    def IV_Measure(cur):
        nonlocal i
        keithley_2400.ramp_to_current(cur * 1e-6)
        sleep(1.5)
        v_meas = keithley_2400.voltage
        sleep(1)
        current_values.append(cur * 1e-6)  # Use the actual sourced value
        Volt.append(v_meas)
        print(f"{cur * 1e-6:.3e} A  {v_meas:.4f} V")
        i += 1

    print("In loop 1")
    for i1 in np.arange(0, I_range + I_step, I_step):
        IV_Measure(i1)
    
    df = pd.DataFrame({'I': current_values, 'V': Volt})
    print("\n--- Measurement Complete ---")
    print(df)
    
    save_path = os.path.join('C:/Users/Instrument-DSL/Desktop/LED_IV/', f"{filename}.txt")
    df.to_csv(save_path, index=None, sep='\t', mode='w')
    print(f"Data saved to {save_path}")
    
    sleep(0.5)
    keithley_2400.shutdown()
    print("Keithley 2400 shutdown complete.")
    
    plt.plot(current_values, Volt, marker='o', linestyle='-', color='g', label='I-V Data')
    plt.xlabel('Current (A)')
    plt.ylabel('Voltage (V)')
    plt.title('I-V Curve')
    plt.legend()
    plt.grid(True)
    plt.show()


if __name__ == "__main__":
    main()
