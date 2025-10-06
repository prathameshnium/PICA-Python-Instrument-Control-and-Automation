'''
File:         lakeshore_350_ramp_control.py
Author:       Ketan (improved by Gemini)
Date:         10/09/2025
Version:      2.0

Description:
This script provides a robust framework for automating a linear temperature ramp
experiment using a Lakeshore Model 350 Temperature Controller.

The program connects to the instrument via its VISA address and interactively
prompts the user for all experiment parameters, including start/end temperatures,
ramp rate, and a safety cutoff. It uses the instrument's built-in setpoint
ramping feature to ensure a smooth, linear temperature rise.

During the experiment, it continuously logs the timestamp, elapsed time, and
temperature from a specified sensor to a user-selected CSV file. A live,
updating plot provides immediate visual feedback of the ramp's progress.

The experiment terminates safely under all conditions (completion, safety cutoff,
user interruption, or error), guaranteeing that the heater is turned off and the
instrument connection is properly closed.

Dependencies:
- pyvisa: For instrument communication.
- matplotlib: For live data plotting.
- tkinter: For the graphical file-save dialog.
- A VISA backend (e.g., NI-VISA) must be installed.

Configuration:
- All experiment parameters (temperatures, rate) are requested at runtime.
- The VISA_ADDRESS constant must be set to match your instrument's setup.
'''
#-------------------------------------------------------------------------------
# Name:         Interfacing Lakeshore 350 Temperature Controller
# Purpose:      Automate and log a linear temperature ramp with live plotting.
# Changes_done: V2.0 - Complete restructure for robustness and usability.
#-------------------------------------------------------------------------------

import pyvisa
import time
import csv
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog
from datetime import datetime

# --- Configuration Constants ---
# TODO: Change this to your instrument's actual VISA address
VISA_ADDRESS = "GPIB0::13::INSTR"
SENSOR_INPUT = 'A'        # Sensor input to monitor (A, B, C, or D)
HEATER_OUTPUT = 1         # Control output loop for the heater (1 or 2)

# Heater hardware configuration (refer to your setup)
# For 'HTRSET' command, resistance is an integer code: 1=25Ω, 2=50Ω
HEATER_RESISTANCE_CODE = 1 # Using 25Ω setting
# For 'HTRSET', max_current is an integer code: 1=0.707A, 2=1A, 3=1.414A, 4=1.732A
MAX_HEATER_CURRENT_CODE = 2 # Using 1A max

class Lakeshore350:
    """A class to control the Lakeshore Model 350 Temperature Controller."""

    def __init__(self, visa_address):
        """
        Initializes the connection to the instrument.
        Args:
            visa_address (str): The VISA resource string for the instrument.
        """
        self.instrument = None
        try:
            print("Connecting to instrument...")
            rm = pyvisa.ResourceManager()
            self.instrument = rm.open_resource(visa_address)
            self.instrument.timeout = 10000  # 10 second timeout
            idn = self.instrument.query('*IDN?').strip()
            print(f"Successfully connected to: {idn}")
        except pyvisa.errors.VisaIOError as e:
            print(f"Connection Error: Could not connect to instrument at {visa_address}")
            print(f"VISA Error: {e}")
            print("Please check the address, connections, and VISA installation.")
            raise ConnectionError("Failed to connect to Lakeshore 350.") from e

    def reset_and_clear(self):
        """Resets the instrument and clears the status registers."""
        print("Resetting instrument to a known state...")
        self.instrument.write('*RST')
        time.sleep(0.5)
        self.instrument.write('*CLS')
        time.sleep(1)

    def setup_heater(self, output, resistance_code, max_current_code):
        """
        Configures the heater parameters using the HTRSET command.
        
        Args:
            output (int): The heater output to configure (e.g., 1 or 2).
            resistance_code (int): Code for heater resistance (1=25Ω, 2=50Ω).
            max_current_code (int): Code for max current limit.
        """
        # Command format for 350: HTRSET <output>,<resistance>,<max current>,<max user current>,<display>
        # We'll use 0 for max user current and 1 for current display.
        command = f'HTRSET {output},{resistance_code},{max_current_code},0,1'
        print(f"Setting up heater: {command}")
        self.instrument.write(command)
        time.sleep(0.5)

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        """
        Configures the setpoint ramp feature for linear temperature changes.
        
        Args:
            output (int): The control loop output (e.g., 1 or 2).
            rate_k_per_min (float): The desired ramp rate in Kelvin per minute.
            ramp_on (bool): True to enable ramping, False to disable.
        """
        on_off_state = 1 if ramp_on else 0
        command = f'RAMP {output},{on_off_state},{rate_k_per_min}'
        print(f"Setting ramp parameters: {command}")
        self.instrument.write(command)
        time.sleep(0.5)
        
    def set_setpoint(self, output, temperature_k):
        """
        Sets the target temperature setpoint.
        
        Args:
            output (int): The control loop output.
            temperature_k (float): The target temperature in Kelvin.
        """
        command = f'SETP {output},{temperature_k}'
        print(f"Setting setpoint: {command}")
        self.instrument.write(command)

    def set_heater_range(self, output, heater_range):
        """
        Sets the heater range, which enables or disables the heater output.
        
        Args:
            output (int): The heater output (1 or 2).
            heater_range (str): 'off', 'low', 'medium', 'high'.
        """
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        if heater_range.lower() not in range_map:
            raise ValueError("Invalid heater range. Must be 'off', 'low', 'medium', or 'high'.")
        
        range_code = range_map[heater_range.lower()]
        command = f'RANGE {output},{range_code}'
        print(f"Setting heater range: {command}")
        self.instrument.write(command)
        
    def get_temperature(self, sensor):
        """
        Queries the temperature from a specified sensor.
        
        Args:
            sensor (str): The sensor input ('A', 'B', 'C', or 'D').
            
        Returns:
            float: The temperature in Kelvin, or float('nan') on error.
        """
        try:
            temp_str = self.instrument.query(f'KRDG? {sensor}').strip()
            return float(temp_str)
        except (pyvisa.errors.VisaIOError, ValueError) as e:
            print(f"Warning: Could not read temperature from sensor {sensor}. Error: {e}")
            return float('nan') # Return Not-a-Number on error

    def get_heater_output(self, output):
        """
        Queries the current heater output percentage.
        
        Args:
            output (int): The heater output to query.
            
        Returns:
            float: Heater output in percent, or float('nan') on error.
        """
        try:
            output_str = self.instrument.query(f'HTR? {output}').strip()
            return float(output_str)
        except (pyvisa.errors.VisaIOError, ValueError) as e:
            print(f"Warning: Could not read heater output {output}. Error: {e}")
            return float('nan')

    def close(self):
        """Safely shuts down the heater and closes the instrument connection."""
        if self.instrument:
            print("\n--- Safely shutting down instrument ---")
            try:
                print("Turning off heater...")
                self.set_heater_range(HEATER_OUTPUT, 'off')
                time.sleep(0.5)
                self.instrument.close()
                print("VISA connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"Error during shutdown: {e}")
            finally:
                self.instrument = None


def get_user_parameters():
    """Prompts the user for experiment parameters and validates them."""
    while True:
        try:
            start_temp = float(input("Enter START temperature (K): "))
            end_temp = float(input("Enter END temperature (K): "))
            rate = float(input("Enter ramp rate (K/min): "))
            safety_cutoff = float(input("Enter SAFETY CUTOFF temperature (K): "))

            if not (start_temp < end_temp < safety_cutoff):
                print("Error: Temperatures must be in ascending order (start < end < cutoff). Please try again.")
                continue
            if rate <= 0:
                print("Error: Ramp rate must be positive. Please try again.")
                continue
            return start_temp, end_temp, rate, safety_cutoff
        except ValueError:
            print("Invalid input. Please enter numeric values.")


def main():
    """Main function to run the temperature ramp experiment."""
    # --- 1. Get User Input and Filename ---
    root = tk.Tk()
    root.withdraw() # Hide the main window
    filename = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        title="Select file to save temperature data"
    )
    if not filename:
        print("No file selected. Exiting.")
        return

    start_temp, end_temp, rate, safety_cutoff = get_user_parameters()
    
    controller = None
    try:
        # --- 2. Initialize Instrument and Plot ---
        controller = Lakeshore350(VISA_ADDRESS)
        controller.reset_and_clear()
        controller.setup_heater(HEATER_OUTPUT, HEATER_RESISTANCE_CODE, MAX_HEATER_CURRENT_CODE)

        # Setup live plot
        plt.ion()
        fig, ax = plt.subplots()
        line, = ax.plot([], [], 'r-o') # Empty plot
        ax.set_xlabel('Elapsed Time (s)')
        ax.set_ylabel('Temperature (K)')
        ax.set_title('Live Temperature Ramp')
        ax.grid(True)
        time_data, temp_data = [], []
        
        # --- 3. Go to Start Temperature and Stabilize ---
        print(f"\nMoving to start temperature of {start_temp} K...")
        controller.set_setpoint(HEATER_OUTPUT, start_temp)
        controller.set_heater_range(HEATER_OUTPUT, 'medium')
        
        while True:
            current_temp = controller.get_temperature(SENSOR_INPUT)
            print(f"Stabilizing... Current Temp: {current_temp:.4f} K", end='\r')
            if abs(current_temp - start_temp) < 0.1: # Stabilization tolerance
                print(f"\nStabilized at {start_temp} K.")
                break
            time.sleep(2)

        # --- 4. Start Ramp and Data Logging ---
        heating_on = 1 # Variable to control heating state
        if heating_on:
            controller.setup_ramp(HEATER_OUTPUT, rate)
            controller.set_setpoint(HEATER_OUTPUT, end_temp)
            print(f"Ramp started towards {end_temp} K at {rate} K/min.")
        else:
            print("Heating is disabled by variable. Exiting.")
            return

        start_time = time.time()
        last_temp = start_temp
        last_time = start_time
        
        # Write header to CSV
        with open(filename, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Heater Output (%)"])

        # Main experiment loop
        while True:
            elapsed_time = time.time() - start_time
            current_temp = controller.get_temperature(SENSOR_INPUT)
            heater_output = controller.get_heater_output(HEATER_OUTPUT)
            
            # Calculate instantaneous rate
            inst_rate = 0.0
            if elapsed_time > 0 and (time.time() - last_time) > 0.1:
                rate_k_per_s = (current_temp - last_temp) / (time.time() - last_time)
                inst_rate = rate_k_per_s * 60
            last_temp, last_time = current_temp, time.time()

            # Print status
            status_str = (
                f"Time: {elapsed_time:8.2f}s | "
                f"Temp: {current_temp:8.4f}K | "
                f"Rate: {inst_rate:6.3f}K/min | "
                f"Heater: {heater_output:5.1f}%"
            )
            print(status_str, end='\r')

            # Log data
            with open(filename, 'a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
                                 f"{elapsed_time:.2f}", 
                                 f"{current_temp:.4f}", 
                                 f"{heater_output:.2f}"])

            # Update plot
            time_data.append(elapsed_time)
            temp_data.append(current_temp)
            line.set_data(time_data, temp_data)
            ax.relim()
            ax.autoscale_view()
            fig.canvas.draw()
            fig.canvas.flush_events()

            # --- 5. Check for End Conditions ---
            if current_temp >= safety_cutoff:
                print(f"\n!!! SAFETY CUTOFF REACHED at {current_temp:.4f} K (Limit: {safety_cutoff} K) !!!")
                break
            
            if current_temp >= end_temp:
                print(f"\nTarget temperature of {end_temp} K reached.")
                break
            
            time.sleep(2) # Data logging interval

    except ConnectionError:
        print("\nCould not start experiment due to connection failure.")
    except KeyboardInterrupt:
        print("\nExperiment stopped by user (Ctrl+C).")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
    finally:
        # --- 6. Guaranteed Safe Shutdown ---
        if controller:
            controller.close()
        plt.ioff()
        print("\nExperiment finished. Plot window can be closed.")
        plt.show() # Keep plot window open until user closes it

if __name__ == "__main__":
    main()
