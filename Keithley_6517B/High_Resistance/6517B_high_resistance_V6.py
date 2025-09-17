#-------------------------------------------------------------------------------
# Name:         Keithley 6517B I-V Sweep Measurement
# Purpose:      Performs a voltage sweep, measures current, saves data, and plots the result.
# Author:       Prathamesh Deshmukh 
# Created:      17/09/2025
# Version:      3.0
#-------------------------------------------------------------------------------

import time
import csv
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

try:
    from pymeasure.instruments.keithley import Keithley6517B
    from pyvisa.errors import VisaIOError
except ImportError:
    print("Error: Required packages not found.")
    print("Please install them by running: pip install numpy matplotlib pymeasure")
    exit()

def get_user_input():
    """Gets all necessary measurement parameters from the user."""
    print("--- I-V Sweep Configuration ---")
    visa_address = input("Enter VISA address for Keithley 6517B (e.g., GPIB0::27::INSTR): ")
    start_v = float(input("Enter Start Voltage (V): "))
    stop_v = float(input("Enter Stop Voltage (V): "))
    steps = int(input("Enter Number of Steps: "))
    delay = float(input("Enter Settling Delay between points (s): "))
    filename = input("Enter the filename to save data (e.g., SampleA_IV.csv): ")

    if not filename.lower().endswith('.csv'):
        filename += '.csv'

    return visa_address, start_v, stop_v, steps, delay, filename

def run_iv_sweep():
    """Main function to connect, configure, measure, and save data."""
    keithley = None
    results = []

    try:
        # --- 1. GET PARAMETERS FROM USER ---
        visa_addr, start_v, stop_v, steps, delay, filename = get_user_input()
        voltage_sweep = np.linspace(start_v, stop_v, steps)

        # --- 2. CONNECT TO INSTRUMENT ---
        print(f"\nAttempting to connect to instrument at: {visa_addr}")
        keithley = Keithley6517B(visa_addr)
        print(f"Successfully connected to: {keithley.id}")

        # --- 3. CONFIGURE MEASUREMENT & ZERO CORRECTION ---
        print("\nConfiguring instrument...")
        keithley.reset()
        keithley.measure_current() # Set to source-voltage, measure-current mode

        print("Starting Zero Correction Procedure (this will take ~20 seconds)...")
        keithley.zero_check = True
        time.sleep(5)
        #keithley.acquire_zero_correction()
        time.sleep(5)
        keithley.zero_check = False
        time.sleep(5)
        keithley.zero_correct = True
        print("Zero Correction Complete.")

        # --- 4. SETUP AND PERFORM SWEEP ---
        keithley.source_voltage_range = max(abs(start_v), abs(stop_v))
        keithley.current_nplc = 1 # Set integration rate to 1 Power Line Cycle for good noise reduction

        print(f"\nStarting I-V sweep from {start_v}V to {stop_v}V...")
        keithley.enable_source()

        # Open file to save data
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            # Write header information
            writer.writerow([f"# Measurement Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
            writer.writerow([f"# Sweep Parameters: Start={start_v}V, Stop={stop_v}V, Steps={steps}, Delay={delay}s"])
            writer.writerow(["Timestamp", "Applied Voltage (V)", "Measured Current (A)", "Resistance (Ohm)"])

            start_time = time.time()
            for i, voltage in enumerate(voltage_sweep):
                keithley.source_voltage = voltage
                time.sleep(delay)
                current = keithley.current
                timestamp = time.time() - start_time

                # Calculate resistance, handle division by zero
                resistance = voltage / current if current != 0 else float('inf')

                # Print progress to console
                print(f"Step {i+1}/{steps}: V={voltage:.3f} V, I={current:.4e} A, R={resistance:.4e} Ω")

                # Store and write data
                data_point = [f"{timestamp:.3f}", f"{voltage:.4e}", f"{current:.4e}", f"{resistance:.4e}"]
                results.append(data_point)
                writer.writerow(data_point)

        print("\n--- I-V Sweep Complete ---")
        print(f"Data saved successfully to '{filename}'")

    except VisaIOError:
        print(f"\n[VISA Connection Error]")
        print(f"Could not connect to the instrument at '{visa_addr}'.")
        print("Please check the address, connections, and if the instrument is ON.")
    except ValueError:
        print("\n[Input Error] Please enter valid numbers for the sweep parameters.")
    except Exception as e:
        print(f"\n[An Unexpected Error Occurred] Details: {e}")

    finally:
        # --- 5. SAFELY SHUT DOWN ---
        if keithley:
            print("\nShutting down instrument...")
            keithley.shutdown()
            print("Voltage source OFF and instrument is safe.")
        return results

def plot_results(data):
    """Plots the I-V curve from the collected data."""
    if not data:
        print("\nNo data to plot.")
        return

    # Extract voltage and current for plotting
    voltages = [float(row[1]) for row in data]
    currents = [float(row[2]) for row in data]

    plt.figure(figsize=(8, 6))
    plt.plot(voltages, currents, 'o-', label='I-V Data', color='#003f5c')
    plt.title('I-V Measurement Curve', fontsize=16)
    plt.xlabel('Applied Voltage (V)', fontsize=12)
    plt.ylabel('Measured Current (A)', fontsize=12)
    plt.grid(True, which="both", ls="--", alpha=0.6)
    plt.ticklabel_format(style='sci', axis='y', scilimits=(0,0)) # Scientific notation for current axis
    plt.legend()
    plt.tight_layout()
    print("\nDisplaying I-V plot...")
    plt.show()


if __name__ == "__main__":
    # Run the measurement
    measurement_data = run_iv_sweep()

    # Plot the results
    plot_results(measurement_data)
