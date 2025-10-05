'''
===============================================================================
 PROGRAM:      Temperature Dependent I-V Measurement

 PURPOSE:      Automates Voltage vs. Temperature characterization by integrating
               a Lakeshore 350 and a Keithley 2400.

 DESCRIPTION:  This script automates Voltage vs. Temperature (V-T)
               characterization by integrating a Lakeshore 350 Temperature
               Controller and a Keithley 2400 SourceMeter. The script sets a
               target temperature, waits for it to stabilize, and then
               performs a full I-V sweep at that temperature, repeating the
               process for a user-defined range of temperatures.

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre

 VERSION:      1.0
 LAST EDITED:  04/10/2025
===============================================================================
'''

import pyvisa
import pymeasure
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import time
import os
import tkinter as tk
from tkinter import filedialog
from pymeasure.instruments.keithley import Keithley2400
from datetime import datetime

# --- 1. Configuration Constants ---

# TODO: Verify and set the correct VISA addresses for your instruments
KEITHLEY_VISA_ADDRESS = "GPIB::4"
LAKESHORE_VISA_ADDRESS = "GPIB0::13::INSTR"

# Lakeshore heater and sensor configuration
SENSOR_INPUT = 'A'      # Sensor input to monitor (A, B, C, or D)
HEATER_OUTPUT = 1       # Control output loop for the heater (1 or 2)
# For 'HTRSET', resistance is an int code: 1=25Ω, 2=50Ω
HEATER_RESISTANCE_CODE = 1 # Using 25Ω setting
# For 'HTRSET', max_current is an int code: 1=0.707A, 2=1A, 3=1.414A, 4=1.732A
MAX_HEATER_CURRENT_CODE = 2 # Using 1A max
STABILITY_TOLERANCE_K = 0.1 # Temperature is stable if within this many Kelvin
STABILITY_DELAY_S = 5       # Wait this long between stability checks

# --- 2. Instrument Control Classes & Functions ---

class Lakeshore350:
    """A class to control the Lakeshore Model 350 Temperature Controller."""

    def __init__(self, visa_address):
        """Initializes the connection to the instrument."""
        self.instrument = None
        try:
            print(f"Connecting to Lakeshore 350 at {visa_address}...")
            rm = pyvisa.ResourceManager()
            self.instrument = rm.open_resource(visa_address)
            self.instrument.timeout = 10000  # 10 second timeout
            idn = self.instrument.query('*IDN?').strip()
            print(f"Successfully connected to: {idn}")
        except pyvisa.errors.VisaIOError as e:
            print(f"LAKESHORE CONNECTION ERROR: {e}")
            print("Please check the address, connections, and VISA installation.")
            raise ConnectionError("Failed to connect to Lakeshore 350.") from e

    def setup_heater(self):
        """Configures the heater parameters using predefined constants."""
        command = f'HTRSET {HEATER_OUTPUT},{HEATER_RESISTANCE_CODE},{MAX_HEATER_CURRENT_CODE},0,1'
        print(f"Setting up heater: {command}")
        self.instrument.write(command)
        time.sleep(0.5)

    def set_temperature_and_stabilize(self, target_temp_k):
        """Sets a new temperature setpoint and waits for it to stabilize."""
        print(f"\nSetting temperature to {target_temp_k} K...")
        self.instrument.write(f'SETP {HEATER_OUTPUT},{target_temp_k}')
        self.instrument.write(f'RANGE {HEATER_OUTPUT},5') # Set to High range for heating

        while True:
            current_temp = self.get_temperature()
            if abs(current_temp - target_temp_k) < STABILITY_TOLERANCE_K:
                print(f"\nTemperature stabilized at {current_temp:.4f} K.")
                break
            print(f"Stabilizing... Current Temp: {current_temp:.4f} K", end='\r')
            time.sleep(STABILITY_DELAY_S)

    def get_temperature(self):
        """Queries the temperature from the primary sensor."""
        try:
            return float(self.instrument.query(f'KRDG? {SENSOR_INPUT}').strip())
        except (pyvisa.errors.VisaIOError, ValueError):
            print(f"Warning: Could not read temperature from sensor {SENSOR_INPUT}.")
            return float('nan')

    def close(self):
        """Safely shuts down the heater and closes the instrument connection."""
        if self.instrument:
            print("\n--- Safely shutting down Lakeshore 350 ---")
            try:
                print("Turning off heater...")
                self.instrument.write(f'RANGE {HEATER_OUTPUT},0') # Range 0 is OFF
                time.sleep(0.5)
                self.instrument.close()
                print("Lakeshore VISA connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"Error during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None


def setup_keithley(visa_address):
    """Connects to the Keithley 2400 and returns the instrument object."""
    try:
        print(f"Connecting to Keithley 2400 at {visa_address}...")
        keithley = Keithley2400(visa_address)
        keithley.reset()
        keithley.disable_buffer()
        print(f"Successfully connected to Keithley.")
        return keithley
    except Exception as e:
        print(f"KEITHLEY CONNECTION ERROR: {e}")
        raise ConnectionError("Failed to connect to Keithley 2400.") from e


def run_iv_sweep(keithley, max_current_ua, step_current_ua):
    """
    Performs a current sweep from 0 -> +max -> 0 and measures voltage.
    """
    print("Starting I-V sweep...")
    current_data, voltage_data = [], []
    max_current_a = max_current_ua * 1e-6
    step_current_a = step_current_ua * 1e-6

    # Configure the instrument for the sweep
    keithley.apply_current()
    keithley.source_current_range = max_current_a if max_current_a > 1e-6 else 1e-6
    keithley.compliance_voltage = 210
    keithley.source_current = 0
    keithley.enable_source()
    keithley.measure_voltage()
    time.sleep(1) # Wait for settings to apply

    # Define the full sweep pattern (0 -> +max -> 0)
    forward_sweep = np.arange(0, max_current_a + step_current_a, step_current_a)
    reverse_sweep = np.arange(max_current_a - step_current_a, 0 - step_current_a, -step_current_a)
    full_sweep = np.concatenate([forward_sweep, reverse_sweep])

    for i_set in full_sweep:
        keithley.ramp_to_current(i_set, steps=5, pause=0.05)
        time.sleep(0.5) # Dwell time for measurement stability
        v_meas = keithley.voltage
        current_data.append(i_set)
        voltage_data.append(v_meas)
        print(f"  I: {i_set:.3e} A -> V: {v_meas:.4f} V", end='\r')

    print("\nI-V sweep complete.")
    keithley.ramp_to_current(0) # Ramp back to zero at the end
    return current_data, voltage_data

# --- 3. User Interface and Main Logic ---

def get_user_parameters():
    """Prompts the user for all experiment parameters and validates them."""
    print("--- Experiment Configuration ---")
    try:
        # Temperature Parameters
        start_temp = float(input("Enter START temperature (K): "))
        end_temp = float(input("Enter END temperature (K): "))
        temp_step = float(input("Enter temperature STEP (K): "))

        # I-V Parameters
        i_range_ua = float(input("Enter MAX sweep current (in microAmps, e.g., 100): "))
        i_step_ua = float(input("Enter current STEP size (in microAmps, e.g., 2): "))

        if temp_step == 0 or i_step_ua <= 0:
            print("Error: Step values must be non-zero.")
            return None

        # Ensure correct direction for temperature sweep
        if start_temp > end_temp and temp_step > 0:
            temp_step = -abs(temp_step)
        if start_temp < end_temp and temp_step < 0:
            temp_step = abs(temp_step)

        return start_temp, end_temp, temp_step, i_range_ua, i_step_ua
    except ValueError:
        print("Invalid input. Please enter numeric values only.")
        return None

def main():
    """Main function to run the temperature-dependent I-V experiment."""
    # --- Get User Input and Data Folder ---
    params = get_user_parameters()
    if not params:
        return
    start_temp, end_temp, temp_step, i_range_ua, i_step_ua = params

    root = tk.Tk()
    root.withdraw()
    data_path = filedialog.askdirectory(title="Select a Folder to Save Data Files")
    if not data_path:
        print("No folder selected. Exiting.")
        return
    base_filename = input("Enter a base name for the data files: ")

    # --- Initialize Instruments ---
    lakeshore = None
    keithley = None
    try:
        lakeshore = Lakeshore350(LAKESHORE_VISA_ADDRESS)
        keithley = setup_keithley(KEITHLEY_VISA_ADDRESS)

        lakeshore.setup_heater()

        # --- Main Experiment Loop (Iterating through temperatures) ---
        # Adjust endpoint for np.arange to be inclusive
        temp_range = np.arange(start_temp, end_temp + (temp_step / 2), temp_step)

        for temp_setpoint in temp_range:
            # a) Set and stabilize temperature
            lakeshore.set_temperature_and_stabilize(temp_setpoint)
            actual_temp = lakeshore.get_temperature()

            # b) Perform I-V sweep
            currents, voltages = run_iv_sweep(keithley, i_range_ua, i_step_ua)

            # c) Save data to file
            filename = f"{base_filename}_{actual_temp:.2f}K.txt"
            full_path = os.path.join(data_path, filename)
            df = pd.DataFrame({
                'Current (A)': currents,
                'Voltage (V)': voltages,
                'Temperature (K)': actual_temp
            })
            df.to_csv(full_path, sep='\t', index=False, float_format='%.6e')
            print(f"Data for {actual_temp:.2f} K saved to: {filename}")

            # d) Generate and save a plot for this temperature
            plt.figure(figsize=(8, 6))
            plt.plot(currents, voltages, 'o-', color='teal', label=f'T = {actual_temp:.2f} K')
            plt.title(f'I-V Curve for {base_filename} at {actual_temp:.2f} K')
            plt.xlabel('Current (A)')
            plt.ylabel('Voltage (V)')
            plt.grid(True)
            plt.legend()
            plot_filename = f"{base_filename}_{actual_temp:.2f}K.png"
            plt.savefig(os.path.join(data_path, plot_filename))
            plt.close() # Close the figure to prevent it from displaying

    except (ConnectionError, KeyboardInterrupt, Exception) as e:
        if isinstance(e, KeyboardInterrupt):
            print("\n\nExperiment interrupted by user (Ctrl+C).")
        else:
            print(f"\n\nAN UNEXPECTED ERROR OCCURRED: {e}")
            print("Attempting to shut down instruments safely.")

    finally:
        # --- Guaranteed Safe Shutdown ---
        if keithley:
            keithley.shutdown()
            print("Keithley 2400 shutdown.")
        if lakeshore:
            lakeshore.close()
        print("\nExperiment finished.")


# --- 4. Program Entry Point ---
if __name__ == "__main__":
    main()
