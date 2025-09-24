#-------------------------------------------------------------------------------
# Name:           Integrated R-T Measurement Control
# Purpose:        Automate and log a Resistance vs. Temperature measurement.
# Author:         Prathamesh Deshmukh
# Date:           19/09/2025
# Version:        3.1 (Gemini-Enhanced Heating Logic)
# Changes_done:   V3.1 - Incorporated active heating/cooling logic for the
#                 stabilization phase from the GUI version for faster and
#                 more robust temperature settling.
#-------------------------------------------------------------------------------

import pyvisa
import time
import csv
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog
from datetime import datetime

try:
    from pymeasure.instruments.keithley import Keithley6517B
except ImportError:
    print("Error: The 'pymeasure' package is required but not found.")
    print("Please install it by running: pip install pymeasure")
    exit()

# --- Configuration Constants ---
# TODO: Change these to your instruments' actual VISA addresses
LAKESHORE_VISA = "GPIB1::15::INSTR"
KEITHLEY_VISA = "GPIB1::27::INSTR"

# Lakeshore hardware configuration
SENSOR_INPUT = 'A'      # Sensor input to monitor (A, B, C, or D)
HEATER_OUTPUT = 1       # Control output loop for the heater (1 or 2)
HEATER_RESISTANCE_CODE = 1 # 1=25Ω, 2=50Ω
MAX_HEATER_CURRENT_CODE = 2 # 1=0.707A, 2=1A, 3=1.414A, 4=1.732A

# --- Instrument Control Classes & Functions ---

class Lakeshore350:
    """A class to control the Lakeshore Model 350 Temperature Controller."""
    def __init__(self, visa_address):
        self.instrument = None
        try:
            print("Connecting to Lakeshore 350...")
            rm = pyvisa.ResourceManager()
            self.instrument = rm.open_resource(visa_address)
            self.instrument.timeout = 10000  # 10 second timeout
            idn = self.instrument.query('*IDN?').strip()
            print(f"Successfully connected to: {idn}")
        except pyvisa.errors.VisaIOError as e:
            print(f"Connection Error: Could not connect to Lakeshore at {visa_address}")
            print(f"VISA Error: {e}")
            raise ConnectionError("Failed to connect to Lakeshore 350.") from e

    def reset_and_clear(self):
        print("Resetting Lakeshore to a known state...")
        self.instrument.write('*RST')
        time.sleep(0.5)
        self.instrument.write('*CLS')
        time.sleep(1)

    def setup_heater(self, output, resistance_code, max_current_code):
        command = f'HTRSET {output},{resistance_code},{max_current_code},0,1'
        print(f"Setting up Lakeshore heater: {command}")
        self.instrument.write(command)
        time.sleep(0.5)

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        on_off_state = 1 if ramp_on else 0
        command = f'RAMP {output},{on_off_state},{rate_k_per_min}'
        print(f"Setting ramp parameters: {command}")
        self.instrument.write(command)
        time.sleep(0.5)

    def set_setpoint(self, output, temperature_k):
        command = f'SETP {output},{temperature_k}'
        print(f"Setting setpoint: {command}")
        self.instrument.write(command)

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        if heater_range.lower() not in range_map:
            raise ValueError("Invalid heater range. Must be 'off', 'low', 'medium', or 'high'.")
        range_code = range_map[heater_range.lower()]
        command = f'RANGE {output},{range_code}'
        # To avoid spamming the console, we'll only print this when it's a meaningful change
        # print(f"Setting heater range: {command}")
        self.instrument.write(command)

    def get_temperature(self, sensor):
        try:
            temp_str = self.instrument.query(f'KRDG? {sensor}').strip()
            return float(temp_str)
        except (pyvisa.errors.VisaIOError, ValueError) as e:
            print(f"Warning: Could not read temperature from sensor {sensor}. Error: {e}")
            return float('nan')

    def get_heater_output(self, output):
        try:
            output_str = self.instrument.query(f'HTR? {output}').strip()
            return float(output_str)
        except (pyvisa.errors.VisaIOError, ValueError) as e:
            print(f"Warning: Could not read heater output {output}. Error: {e}")
            return float('nan')

    def close(self):
        if self.instrument:
            print("\n--- Safely shutting down Lakeshore ---")
            try:
                print("Turning off heater...")
                self.set_heater_range(HEATER_OUTPUT, 'off')
                time.sleep(0.5)
                self.instrument.close()
                print("Lakeshore VISA connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"Error during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None


def get_user_parameters():
    """Prompts the user for all experiment parameters and validates them."""
    print("\n--- Experiment Configuration ---")
    while True:
        try:
            start_temp = float(input("Enter START temperature (K): "))
            end_temp = float(input("Enter END temperature (K): "))
            rate = float(input("Enter ramp rate (K/min): "))
            safety_cutoff = float(input("Enter SAFETY CUTOFF temperature (K): "))
            print("-" * 20)
            source_voltage = float(input("Enter Keithley SOURCE voltage for R measurement (V): "))
            delay = float(input("Enter Keithley settling delay between points (s): "))

            if not (start_temp < end_temp < safety_cutoff):
                print("\nError: Temperatures must be in ascending order (start < end < cutoff). Please try again.")
                continue
            if rate <= 0:
                print("\nError: Ramp rate must be positive. Please try again.")
                continue
            if delay < 0:
                print("\nError: Settling delay cannot be negative. Please try again.")
                continue

            return start_temp, end_temp, rate, safety_cutoff, source_voltage, delay
        except ValueError:
            print("\nInvalid input. Please enter numeric values only.")

def perform_keithley_zero_check(keithley):
    """Performs the zero check and correction procedure on the Keithley 6517B."""
    print("\n--- Starting Keithley Zero Correction ---")
    keithley.reset()
    keithley.measure_resistance()
    print("Step 1: Enabling Zero Check mode (shorts the input)...")
    keithley.write(':SYSTem:ZCHeck ON')
    time.sleep(2)
    print("Step 2: Acquiring zero correction value...")
    keithley.write(':SYSTem:ZCORrect:ACQuire')
    time.sleep(3)
    print("Step 3: Disabling Zero Check mode...")
    keithley.write(':SYSTem:ZCHeck OFF')
    time.sleep(1)
    print("Step 4: Enabling Zero Correction for all measurements...")
    keithley.write(':SYSTem:ZCORrect ON')
    time.sleep(1)
    print("Zero Correction Complete.")

# --- Main Program Execution ---
def main():
    """Main function to run the R-T experiment."""
    root = tk.Tk()
    root.withdraw()
    filename = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        title="Select file to save Resistance-Temperature data"
    )
    if not filename:
        print("No file selected. Exiting.")
        return

    params = get_user_parameters()
    start_temp, end_temp, rate, safety_cutoff, source_voltage, delay = params

    lakeshore = None
    keithley = None
    try:
        # --- Initialize Instruments ---
        lakeshore = Lakeshore350(LAKESHORE_VISA)
        lakeshore.reset_and_clear()
        lakeshore.setup_heater(HEATER_OUTPUT, HEATER_RESISTANCE_CODE, MAX_HEATER_CURRENT_CODE)

        print(f"\nAttempting to connect to Keithley at: {KEITHLEY_VISA}")
        keithley = Keithley6517B(KEITHLEY_VISA)
        print(f"Successfully connected to: {keithley.id}")
        perform_keithley_zero_check(keithley)

        keithley.source_voltage = source_voltage
        keithley.current_nplc = 1
        keithley.enable_source()
        print(f"\nKeithley source enabled and set to {source_voltage} V.")

        # --- Setup Live Plot ---
        plt.ion()
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 8))
        fig.suptitle('Live R-T Measurement', fontsize=16)
        line1, = ax1.plot([], [], 'b-o', markersize=3)
        ax1.set_xlabel('Elapsed Time (s)')
        ax1.set_ylabel('Temperature (K)')
        ax1.set_title('Temperature Ramp Profile')
        ax1.grid(True, linestyle=':')
        line2, = ax2.plot([], [], 'r-s', markersize=3)
        ax2.set_xlabel('Temperature (K)')
        ax2.set_ylabel('Resistance (Ω)')
        ax2.set_title('Resistance vs. Temperature')
        ax2.grid(True, linestyle=':')
        ax2.set_yscale('log')
        fig.tight_layout(rect=[0, 0.03, 1, 0.95])
        time_data, temp_data, res_data = [], [], []

        # --- NEW: Go to Start Temp and Stabilize with Active Control ---
        print(f"\nMoving to start temperature of {start_temp} K using active control...")
        while True:
            current_temp = lakeshore.get_temperature(SENSOR_INPUT)

            # Active heating/cooling logic
            if current_temp > start_temp + 0.2:  # System is too warm
                print(f"Cooling... Current: {current_temp:.4f} K > Target: {start_temp} K", end='\r')
                lakeshore.set_heater_range(HEATER_OUTPUT, 'off')
            else:  # System is too cold or within tolerance
                print(f"Heating... Current: {current_temp:.4f} K <= Target: {start_temp} K", end='\r')
                lakeshore.set_heater_range(HEATER_OUTPUT, 'medium')
                lakeshore.set_setpoint(HEATER_OUTPUT, start_temp)

            # Check for stabilization
            if abs(current_temp - start_temp) < 0.1:  # Stabilization tolerance
                print(f"\nStabilized at {current_temp:.4f} K. Waiting 5 seconds before starting ramp.")
                time.sleep(5)
                break
            time.sleep(2) # Interval for checking stabilization status

        # --- Start Ramp and Data Logging ---
        lakeshore.setup_ramp(HEATER_OUTPUT, rate)
        lakeshore.set_setpoint(HEATER_OUTPUT, end_temp)
        lakeshore.set_heater_range(HEATER_OUTPUT, 'medium') # Ensure heater is on for the ramp
        print(f"Ramp started towards {end_temp} K at {rate} K/min.")

        start_time = time.time()

        with open(filename, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([
                "Timestamp", "Elapsed Time (s)", "Temperature (K)", "Heater Output (%)",
                "Applied Voltage (V)", "Measured Current (A)", "Resistance (Ohm)"
            ])

        # --- Main experiment loop ---
        while True:
            elapsed_time = time.time() - start_time
            current_temp = lakeshore.get_temperature(SENSOR_INPUT)
            heater_output = lakeshore.get_heater_output(HEATER_OUTPUT)

            time.sleep(delay)
            resistance = keithley.resistance
            current = abs(source_voltage / resistance) if resistance != 0 else float('inf')

            status_str = (
                f"Time: {elapsed_time:7.2f}s | "
                f"Temp: {current_temp:8.4f}K | "
                f"Resistance: {resistance:9.3e} Ω"
            )
            print(status_str, end='\r')

            with open(filename, 'a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    f"{elapsed_time:.2f}", f"{current_temp:.4f}", f"{heater_output:.2f}",
                    f"{source_voltage:.4e}", f"{current:.4e}", f"{resistance:.4e}"
                ])

            time_data.append(elapsed_time)
            temp_data.append(current_temp)
            res_data.append(resistance)

            line1.set_data(time_data, temp_data)
            ax1.relim(); ax1.autoscale_view()
            line2.set_data(temp_data, res_data)
            ax2.relim(); ax2.autoscale_view()
            fig.canvas.draw(); fig.canvas.flush_events()

            # --- Check for End Conditions ---
            if current_temp >= safety_cutoff:
                print(f"\n\n!!! SAFETY CUTOFF REACHED at {current_temp:.4f} K (Limit: {safety_cutoff} K) !!!")
                break
            if current_temp >= end_temp:
                print(f"\n\nTarget temperature of {end_temp} K reached.")
                break
            
            # The main data logging interval. Should be independent of Keithley delay.
            time.sleep(2)

    except ConnectionError as e:
        print(f"\nCould not start experiment due to a connection failure: {e}")
    except KeyboardInterrupt:
        print("\n\nExperiment stopped by user (Ctrl+C).")
    except Exception as e:
        print(f"\n\nAn unexpected error occurred: {e}")
    finally:
        # --- Guaranteed Safe Shutdown ---
        print("\n--- Initiating Safe Shutdown of All Instruments ---")
        if keithley:
            keithley.shutdown()
            print("Keithley voltage source is OFF and connection is closed.")
        if lakeshore:
            lakeshore.close()

        plt.ioff()
        print(f"\nExperiment finished. Data saved to '{filename}'.")
        print("Plot window can now be closed.")
        plt.show()

if __name__ == "__main__":
    main()
