'''
===============================================================================
 PROGRAM:      Keithley 6517B I-V Sweep Measurement

 PURPOSE:      Perform a voltage sweep and measure current to generate an I-V
               curve using a Keithley 6517B.

 DESCRIPTION:  This script automates an I-V sweep measurement using a Keithley
               6517B electrometer. It prompts the user for sweep parameters
               (start/stop voltage, steps, delay), performs a zero-check
               correction, executes the voltage sweep while measuring current,
               saves the data to a CSV file, and displays a plot of the
               resulting I-V curve.

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre

 VERSION:      5.1
 LAST EDITED:  04/10/2025
===============================================================================
'''

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

# --- 1. USER CONFIGURATION ---
# The VISA address is fixed as it was in V5.
VISA_ADDRESS = "GPIB1::27::INSTR"

def get_sweep_parameters():
    """Gets I-V sweep parameters from the user."""
    print("--- I-V Sweep Configuration ---")
    start_v = float(input("Enter Start Voltage (V): "))
    stop_v = float(input("Enter Stop Voltage (V): "))
    steps = int(input("Enter Number of Steps: "))
    delay = float(input("Enter Settling Delay between points (s): "))
    filename = input("Enter the filename to save data (e.g., SampleA_IV.csv): ")

    if not filename.lower().endswith('.csv'):
        filename += '.csv'
    return start_v, stop_v, steps, delay, filename

def plot_results(data):
    """Plots the I-V curve from the collected data."""
    if not data:
        print("\nNo data to plot.")
        return

    voltages = [float(row[1]) for row in data]
    currents = [float(row[2]) for row in data]

    plt.figure(figsize=(8, 6))
    plt.plot(voltages, currents, 'o-', label='I-V Data', color='#003f5c')
    plt.title('I-V Measurement Curve', fontsize=16)
    plt.xlabel('Applied Voltage (V)', fontsize=12)
    plt.ylabel('Measured Current (A)', fontsize=12)
    plt.grid(True, which="both", ls="--", alpha=0.6)
    plt.ticklabel_format(style='sci', axis='y', scilimits=(0,0))
    plt.legend()
    plt.tight_layout()
    print("\nDisplaying I-V plot...")
    plt.show()

# --- Main Execution ---
keithley = None
results = []

try:
    # Get sweep parameters from the user
    start_v, stop_v, steps, delay, filename = get_sweep_parameters()
    voltage_sweep = np.linspace(start_v, stop_v, steps)

    # --- 2. CONNECT TO INSTRUMENT (V5 Logic) ---
    print(f"\nAttempting to connect to instrument at: {VISA_ADDRESS}")
    keithley = Keithley6517B(VISA_ADDRESS)
    print(f"Successfully connected to: {keithley.id}")

    # --- 3. CONFIGURE MEASUREMENT (V5 Logic) ---
    print("\nConfiguring instrument for measurement...")
    keithley.reset()
    # Set the function to resistance to ensure the ammeter is configured for zero correction.
    keithley.measure_resistance()

    # --- 4. PERFORM ZERO CHECK & CORRECTION (Exact V5 Logic) ---
    print("\nStarting zero correction procedure...")
    time.sleep(5)
    print("Step 1: Enabling Zero Check mode...")
    keithley.write(':SYSTem:ZCHeck ON')
    time.sleep(5)
    print("Step 2: Acquiring zero correction value...")
    #keithley.write(':SYSTem:ZCORrect:ACQuire')
    time.sleep(5)
    print("Step 3: Disabling Zero Check mode...")
    keithley.write(':SYSTem:ZCHeck OFF')
    time.sleep(5)
    print("Step 4: Enabling Zero Correction...")
    keithley.write(':SYSTem:ZCORrect ON')
    time.sleep(5)
    print("Zero Correction Complete.")

    # --- 5. SETUP AND PERFORM I-V SWEEP ---
    print(f"\nStarting I-V sweep from {start_v}V to {stop_v}V...")
    keithley.current_nplc = 1 # Set integration rate for noise reduction

    keithley.enable_source()

    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([f"# Measurement Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
        writer.writerow([f"# Sweep Parameters: Start={start_v}V, Stop={stop_v}V, Steps={steps}, Delay={delay}s"])
        writer.writerow(["Timestamp (s)", "Applied Voltage (V)", "Measured Current (A)", "Resistance (Ohm)"])

        start_time = time.time()
        for i, voltage in enumerate(voltage_sweep):
            keithley.source_voltage = voltage
            time.sleep(delay)
            resistance = keithley.resistance
            timestamp = time.time() - start_time
            #resistance = keithley.resistance
            current = voltage/resistance if resistance != 0 else float('inf')

            print(f"Step {i+1}/{steps}: V={voltage:.3f} V, I={current:.4e} A, R={resistance:.4e} Ω")

            data_point = [f"{timestamp:.3f}", f"{voltage:.4e}", f"{current:.4e}", f"{resistance:.4e}"]
            results.append(data_point)
            writer.writerow(data_point)

    print("\n--- I-V Sweep Complete ---")
    print(f"Data saved successfully to '{filename}'")

except VisaIOError:
    print(f"\n[VISA Connection Error]")
    print(f"Could not connect to the instrument at '{VISA_ADDRESS}'.")
    print("Please check the address, cable connections, and if the instrument is on.")
except ValueError:
    print("\n[Input Error] Please enter valid numbers for the sweep parameters.")
except Exception as e:
    print(f"\n[An Unexpected Error Occurred] Details: {e}")

finally:
    # --- 7. SAFELY SHUT DOWN (V5 Logic) ---
    if keithley:
        print("\nShutting down instrument...")
        keithley.shutdown()
        print("Voltage source OFF and instrument is safe.")

# --- 8. PLOT RESULTS ---
plot_results(results)
