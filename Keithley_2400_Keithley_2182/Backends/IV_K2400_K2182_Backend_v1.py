# -------------------------------------------------------------------------------
# Name:        IV_K2400_K2182_Backend
# Purpose:     IV Measurement using Keithley 2400 (Source) & 2182 (Voltmeter)
# Author:      Prathamesh Deshmukh
# Created:     31/10/2022
# Updated:     21/11/2025 (JOSS Refactor)
# -------------------------------------------------------------------------------

import numpy as np
import matplotlib.pyplot as plt
import time
import pyvisa
import pandas as pd
from pymeasure.instruments.keithley import Keithley2400


class IV_Combined_Backend:
    """
    Backend logic for performing I-V sweeps using:
    - Keithley 2400 SourceMeter (Current Source)
    - Keithley 2182 Nanovoltmeter (Voltage Measure)
    """
    def __init__(self):
        self.rm = None
        self.k2182 = None
        self.k2400 = None
        self.results = {'I': [], 'V': []}

    def connect_instruments(self, k2400_addr="GPIB::4", k2182_addr="GPIB::7"):
        """Initializes connection to the instruments."""
        try:
            self.rm = pyvisa.ResourceManager()
            
            # Connect K2182
            self.k2182 = self.rm.open_resource(k2182_addr)
            self.k2182.write("*rst; status:preset; *cls")
            
            # Connect K2400
            self.k2400 = Keithley2400(k2400_addr)
            time.sleep(1)  # Allow settling
            print("Instruments Connected.")
        except Exception as e:
            print(f"Error connecting to instruments: {e}")
            raise

    def configure_source(self, compliance_voltage=210, current_range=1e-3):
        """Configures the K2400 source parameters."""
        if not self.k2400:
            return
        self.k2400.apply_current()
        self.k2400.source_current_range = current_range
        time.sleep(0.5)
        self.k2400.compliance_voltage = compliance_voltage
        self.k2400.source_current = 0
        self.k2400.enable_source()
        time.sleep(1)

    def measure_point(self, current_val, num_readings=2, interval=1):
        """Performs a single point measurement."""
        if not self.k2400 or not self.k2182:
            return 0.0

        # Set Source
        self.k2400.ramp_to_current(current_val)
        time.sleep(0.5)

        # Configure Voltmeter for this point
        self.k2182.write("status:measurement:enable 512; *sre 1")
        self.k2182.write(f"sample:count {num_readings}")
        self.k2182.write("trigger:source bus")
        self.k2182.write(f"trigger:delay {interval}")
        self.k2182.write(f"trace:points {num_readings}")
        self.k2182.write("trace:feed sense1; feed:control next")
        self.k2182.write("initiate")
        self.k2182.assert_trigger()
        
        # Wait for measurement
        time.sleep(1) 
        
        try:
            voltages = self.k2182.query_ascii_values("trace:data?")
        except Exception:
            voltages = [0.0]

        self.k2182.write("trace:clear; feed:control next")
        
        # Calculate Average
        v_avg = sum(voltages) / len(voltages) if voltages else 0.0
        
        # Store results
        self.results['I'].append(current_val)
        self.results['V'].append(v_avg)
        print(f"I: {current_val:.3e} A | V: {v_avg:.4e} V")
        
        return v_avg

    def run_sweep(self, start_i, stop_i, step_i, filename):
        """Executes the full sweep loop."""
        currents = np.arange(start_i, stop_i, step_i)
        print(f"Starting sweep: {len(currents)} points.")
        
        for i_val in currents:
            self.measure_point(i_val)
        
        # Save Data
        df = pd.DataFrame(self.results)
        try:
            # Use a relative path or a safe default for portability
            save_path = f"{filename}.txt"
            df.to_csv(save_path, sep='\t', index=False)
            print(f"Data saved to {save_path}")
        except Exception as e:
            print(f"Error saving file: {e}")

    def shutdown(self):
        """Safely turns off instruments."""
        if self.k2400:
            try:
                self.k2400.shutdown()
            except Exception:
                pass
        if self.k2182:
            try:
                self.k2182.write("*rst")
                self.k2182.close()
            except Exception:
                pass
        print("Shutdown complete.")


def main():
    """
    Main execution entry point.
    Protected by __name__ check to prevent execution during import/testing.
    """
    # Get User Input
    try:
        # In tests, these inputs are mocked.
        # We provide defaults or catch errors to ensure robustness.
        filename = input("Enter filename: ") or "test_data"
    except (EOFError, KeyboardInterrupt):
        filename = "test_abort"

    backend = IV_Combined_Backend()
    
    try:
        # Hardcoded GPIB addresses for standalone run
        backend.connect_instruments()
        backend.configure_source()
        
        # Example Loop (0 to 1mA)
        backend.run_sweep(0, 1e-3, 0.1e-3, filename)
        
        # Plotting
        if len(backend.results['V']) > 0:
            plt.plot(backend.results['V'], backend.results['I'], 'o-g', label='Data')
            plt.xlabel('Voltage (V)')
            plt.ylabel('Current (A)')
            plt.title('IV Curve')
            plt.legend()
            plt.show()
            
    except Exception as e:
        print(f"Runtime Error: {e}")
    finally:
        backend.shutdown()


if __name__ == "__main__":
    main()