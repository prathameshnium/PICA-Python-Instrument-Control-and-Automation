# -------------------------------------------------------------------------------
# Name:        Keithley 6517B Poling
# Purpose:     Apply a poling voltage to a sample.
# Author:      Prathamesh K Deshmukh
# Created:     03-03-2024
# Updated:     22-11-2025 (Refactored for robustness)
# -------------------------------------------------------------------------------

import time
from pymeasure.instruments.keithley import Keithley6517B


def main():
    """
    Connects to a Keithley 6517B, applies a fixed voltage for a duration,
    and then safely shuts down.
    """
    keithley = None
    try:
        # Initialize and configure the instrument
        keithley = Keithley6517B("GPIB0::27::INSTR")
        time.sleep(0.5)

        # Apply a poling voltage
        keithley.source_voltage = 100
        keithley.enable_source()
        print("Source enabled. Applying 100V for poling.")
        time.sleep(1)

        # Wait for the poling duration, periodically checking current
        print("Waiting for poling to complete (20s)... Press Ctrl+C to stop early.")
        for i in range(20):
            time.sleep(1)
            # You can uncomment the next line to monitor current during poling
            # print(f"  -> Time: {i+1}s, Current: {keithley.current:.3e} A", end='\r')

        print(f"\nPoling complete. Final current: {keithley.current:.3e} A")

    except KeyboardInterrupt:
        print("\nPoling stopped by user (Ctrl+C).")
    except Exception as e:
        print(f"An error occurred with the Keithley 6517B: {e}")
    finally:
        # Guaranteed shutdown logic
        if keithley:
            print("Ramping down voltage and shutting down source...")
            keithley.shutdown()
            print("Source is off.")
            # Optional: Reset the instrument to default state
            keithley.reset()


if __name__ == "__main__":
    main()