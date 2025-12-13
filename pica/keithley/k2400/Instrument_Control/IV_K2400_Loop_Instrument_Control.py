"""
This script interfaces with a Keithley 2400 SourceMeter to perform
Current-Voltage (I-V) characterization of a device.

"""

import os
import numpy as np
import matplotlib.pyplot as plt
from time import sleep
from pymeasure.instruments.keithley import Keithley2400
import pandas as pd
import argparse


def main():
    """
    Main function to run the I-V sweep measurement.
    """
    parser = argparse.ArgumentParser(description="Keithley 2400 I-V Sweep")
    parser.add_argument("--filename", required=True, help="Name of the file to save the data.")
    parser.add_argument("--path", help="Path to save the data file. Defaults to a 'data' folder.")
    parser.add_argument("--range", type=float, required=True, help="Highest value of Current (in micro A).")
    parser.add_argument("--step", type=float, required=True, help="The step size (in micro A).")
    parser.add_argument("--gpib-address", default="GPIB::4", help="GPIB address of the instrument.")
    args = parser.parse_args()

    # object creation ----------------------------------
    keithley_2400 = Keithley2400(args.gpib_address)
    keithley_2400.disable_buffer()
    sleep(2)

    i = 0
    current_values = []
    Volt = []

    # user input ----------------------------------
    I_range = args.range
    I_step = args.step
    filename = args.filename

    print("Current (A) || Voltage(V) ")

    keithley_2400.source_mode = 'current'
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
        print(f"{cur * 1e-6:.3e} A  {v_meas} V")
        i += 1

    print("In loop 1")
    num_steps = int(I_range / I_step)
    for i1 in np.linspace(0, I_range, num_steps + 1):
        IV_Measure(i1)

    df = pd.DataFrame({'I': current_values, 'V': Volt})
    print("\n--- Measurement Complete ---")
    print(df)

    if args.path:
        save_dir = args.path
    else:
        save_dir = "data"
    
    if not os.path.isdir(save_dir):
        os.makedirs(save_dir)

    save_path = os.path.join(save_dir, f"{filename}.txt")
    df.to_csv(save_path, index=None, sep='\t', mode='w')
    print(f"Data saved to {save_path}")

    sleep(0.5)
    keithley_2400.shutdown()
    print("Keithley 2400 shutdown complete.")

    plt.plot(current_values, Volt, marker='o',
             linestyle='-', color='g', label='I-V Data')
    plt.xlabel('Current (A)')
    plt.ylabel('Voltage (V)')
    plt.title('I-V Curve')
    plt.legend()
    plt.grid(True)
    plt.show()

if __name__ == "__main__":
    main()
