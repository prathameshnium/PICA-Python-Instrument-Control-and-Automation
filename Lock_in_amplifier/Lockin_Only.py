import pyvisa
import time

# --- Configuration ---
# TODO: IMPORTANT! Change this to your instrument's VISA resource address.
# Find this using NI-MAX or your VISA software. Default SR830 GPIB address is 8.
INSTRUMENT_ADDRESS = 'GPIB0::8::INSTR'


def main():
    """
    A basic program to connect to an SRS SR830, identify it,
    and read measurement data.
    """
    sr830 = None  # Initialize instrument variable
    
    # Use a try...except...finally block to ensure resources are cleaned up.
    try:
        # 1. Initialize the VISA Resource Manager
        # This is the main object that finds and manages instruments.
        resource_manager = pyvisa.ResourceManager()
        print(f"VISA library version: {resource_manager.version}")
        print(f"Available resources: {resource_manager.list_resources()}")

        # 2. Open a connection to the instrument
        print(f"\nAttempting to connect to instrument at: {INSTRUMENT_ADDRESS}")
        sr830 = resource_manager.open_resource(INSTRUMENT_ADDRESS)

        # Set communication termination characters. For the SR830, this is typically a line feed.
        sr830.read_termination = '\n'
        sr830.write_termination = '\n'
        
        # Set a timeout for read operations (in milliseconds)
        sr830.timeout = 5000  # 5 seconds

        print("Connection successful!")

        # 3. Query the instrument's identification string (*IDN?)
        # A good first step to verify communication.
        identity = sr830.query('*IDN?')
        print(f"Instrument ID: {identity.strip()}")

        # 4. Read a parameter: Get the current sensitivity
        # The 'SENS?' query returns an integer code.
        sens_code = int(sr830.query('SENS?'))
        print(f"Current sensitivity code: {sens_code} (See manual for V/A mapping)")
        
        # Give the instrument a moment to process, if needed.
        time.sleep(0.1) 

        # 5. Read measurement data
        # 'SNAP? 3, 4' is an efficient way to simultaneously get
        # the magnitude (R, parameter 3) and phase (Theta, parameter 4).
        print("\nRequesting Magnitude (R) and Phase (θ)...")
        values = sr830.query('SNAP? 3,4')
        
        # The result is a string like "1.234E-3,5.678E+1\n".
        # We need to split it and convert the parts to numbers.
        magnitude_str, phase_str = values.strip().split(',')
        
        magnitude_r = float(magnitude_str)
        phase_theta = float(phase_str)

        # 6. Print the results
        print("\n--- Measurement Results ---")
        print(f"Magnitude (R): {magnitude_r:.6f} V")
        print(f"Phase (θ):     {phase_theta:.3f}°")
        print("---------------------------")

    except pyvisa.errors.VisaIOError as e:
        # Handle VISA-specific errors, like instrument not found or timeout.
        print(f"VISA Error: Could not connect or communicate with the instrument.")
        print(f"   Details: {e}")
        print("   Troubleshooting tips:")
        print("   - Is the instrument powered on and connected?")
        print(f"   - Is the VISA address '{INSTRUMENT_ADDRESS}' correct?")
        print("   - Is the NI-VISA (or other) backend installed correctly?")

    except Exception as e:
        # Handle other potential Python errors
        print(f"An unexpected error occurred: {e}")

    finally:
        # 7. Close the connection
        # This is crucial! Always close the connection when you're done.
        if sr830:
            sr830.close()
            print("\nConnection closed.")


if __name__ == "__main__":
    main()
