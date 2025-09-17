import pyvisa
import time

# --- CONFIGURATION ---
# IMPORTANT: Replace with the VISA address of your non-responsive instrument.
# You can find the address by running: print(pyvisa.ResourceManager().list_resources())
INSTRUMENT_ADDRESS = 'YOUR_INSTRUMENT_VISA_ADDRESS_HERE'

def run_rescue(visa_address: str):
    """
    Connects to a hung instrument and attempts to clear its error state.
    """
    print(f"--- Instrument Rescue Script Initialized ---")
    rm = pyvisa.ResourceManager()
    instrument = None  # Initialize to None to ensure it exists for the 'finally' block

    try:
        print(f"Attempting to connect to {visa_address}...")
        # Open a connection to the instrument
        instrument = rm.open_resource(visa_address)
        # Set a short timeout so the script doesn't hang if the instrument is truly frozen
        instrument.timeout = 3000  # 3 seconds
        print("Connection successful. Starting recovery procedure.")

        # Step 1: Clear VISA I/O buffers.
        # This command clears the communication channel between the PC and the instrument.
        # It's often the most critical step to resolve a communication deadlock.
        instrument.clear()
        print("   - Step 1: VISA communication buffers cleared.")
        time.sleep(0.1) # Short pause

        # Step 2: Send the *CLS (Clear Status) command.
        # This is a standard SCPI command that clears the instrument's internal
        # error queue and status registers, getting it out of an error state.
        instrument.write('*CLS')
        print("   - Step 2: Instrument error queue cleared with *CLS.")
        time.sleep(0.1) # Short pause

        # Step 3: Repeatedly read the error queue until it reports "No error".
        # This confirms the instrument's state is clean.
        print("   - Step 3: Verifying instrument's error queue...")
        error_count = 0
        while True:
            # SYST:ERR? is the standard SCPI command to query the oldest error.
            error_string = instrument.query('SYST:ERR?')
            # The standard response for no errors contains '0,"No error"'.
            if '0,"No error"' in error_string:
                print("   Queue is empty. Instrument is clean.")
                break
            else:
                # If there was an error, print it. The query itself removes it from the queue.
                print(f"   - Found and cleared lingering error: {error_string.strip()}")
                error_count += 1

            # A safety break to prevent an infinite loop if the instrument is misbehaving.
            if error_count > 10:
                print("   - More than 10 errors found. Stopping to prevent infinite loop.")
                break

        print("\nRescue complete! The instrument should be responsive again.")

    except pyvisa.errors.VisaIOError as e:
        print(f"\nA VISA communication error occurred: {e}")
        print("   This could mean:")
        print("   1. The VISA address is incorrect.")
        print("   2. The instrument is too deeply frozen for a software reset.")
        print("   A manual power cycle may be necessary.")

    except Exception as e:
        print(f"\nAn unexpected script error occurred: {e}")

    finally:
        # This block ensures the connection is always closed, even if errors occur.
        if instrument:
            instrument.close()
            print("Connection closed.")


# --- Run the script ---
if __name__ == "__main__":
    if INSTRUMENT_ADDRESS == 'YOUR_INSTRUMENT_VISA_ADDRESS_HERE':
        print("Please update the INSTRUMENT_ADDRESS variable in the script first!")
    else:
        run_rescue(INSTRUMENT_ADDRESS)
