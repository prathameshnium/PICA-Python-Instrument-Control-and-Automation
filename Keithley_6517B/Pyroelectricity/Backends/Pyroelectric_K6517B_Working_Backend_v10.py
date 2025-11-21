# -----------------------------------------------------------------------
# Name:       Pyroelectricity measurement
# Purpose:    Interface with Lakeshore 350 Temperature Controller and
#             Keithley 6517B electrometer for pyroelectric measurements.
# Author:      Prathamesh K Deshmukh
# Created:    03/03/2024
# Updated:    22/11/2025 (Refactored for robustness and structure)
# -----------------------------------------------------------------------

import pyvisa
import time
from pymeasure.instruments.keithley import Keithley6517B
from datetime import datetime
import traceback


def main():
    """
    Main function to run the pyroelectric measurement experiment.
    """
    base_filename = 'E:/Prathamesh/Python Stuff/Py Pyroelectric/Test_data/Pyro_data_test'
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{base_filename}_{timestamp}.csv"
    print(f'Filename: {filename}')
    time.sleep(0.01)

    # --- Experiment Parameters ---
    T_final = 360  # Cutoff temperature in Kelvin
    Tset = 312     # Setpoint temperature in Kelvin
    ramp_rate = 5  # Ramp rate in K/min
    heater_range_code = 1  # 1=Low, 2=Medium, 3=High (refer to manual)

    temp_controller = None
    keithley = None

    try:
        # --- Instrument Initialization ---
        rm = pyvisa.ResourceManager()
        print("Available VISA resources:", rm.list_resources())
        time.sleep(1)

        # Connect and configure Lakeshore 350
        temp_controller = rm.open_resource("GPIB::12")
        print(f"\nConnected to {temp_controller.query('*IDN?').strip()}")
        temp_controller.write('*RST; *CLS')
        time.sleep(0.5)
        temp_controller.write(f'RAMP 1, 1, {ramp_rate}')
        time.sleep(0.5)
        temp_controller.write(f'RANGE {heater_range_code}')
        time.sleep(0.5)
        temp_controller.write(f'SETP 1,{Tset}')
        time.sleep(0.5)
        temp_controller.write(f'CLIMIT 1, {Tset}, 10, 0')
        time.sleep(0.5)
        print("Lakeshore 350 configured.")

        # Connect and configure Keithley 6517B
        keithley = Keithley6517B("GPIB0::27::INSTR")
        time.sleep(1)
        print(f"\nConnected to {keithley.id}")
        print("\n-----------------------------------------------------\n")
        keithley.measure_current()
        print("Keithley 6517B configured to measure current.")

        # --- Data File Setup ---
        with open(filename, 'w', newline='') as file:
            file.write("Time (s),Temperature (K),Current (A)\n")
        print(f"Data will be saved to {filename}")

        # --- Measurement Loop ---
        start_time = time.time()
        is_running = True
        print("\nStarting measurement loop... Press Ctrl+C to stop.")
        while is_running:
            elapsed_time = time.time() - start_time
            temperature_str = temp_controller.query('KRDG? A').strip()
            temperature = float(temperature_str)
            current = keithley.current

            print(f"Time: {elapsed_time:7.2f}s | Temp: {temperature:7.3f}K | Current: {current:.4e}A")

            with open(filename, 'a', newline='') as file:
                file.write(f"{elapsed_time:.2f},{temperature_str},{current}\n")

            if temperature >= T_final:
                print(f"\nFinal temperature of {T_final}K reached. Stopping.")
                is_running = False

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nMeasurement stopped by user (Ctrl+C).")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        traceback.print_exc()
    finally:
        # --- Safe Shutdown ---
        print("\n--- Shutting down instruments ---")
        if temp_controller:
            try:
                temp_controller.write('RANGE 0')  # Turn off heater
                time.sleep(1)
                temp_controller.close()
                print("Lakeshore 350 connection closed.")
            except Exception as e:
                print(f"Error during Lakeshore shutdown: {e}")
        if keithley:
            try:
                keithley.shutdown()  # Ramps down source and disables output
                time.sleep(1)
                keithley.clear()
                print("Keithley 6517B connection closed.")
            except Exception as e:
                print(f"Error during Keithley shutdown: {e}")
        print("---------------------------------")


if __name__ == "__main__":
    main()