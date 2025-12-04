> This was a mini guide for those who want to try starting interfacing instruments, from Feb 15, 2024.

# Python Instrument Interfacing: A Beginner's Guide

This guide provides a basic introduction to interfacing with instruments using Python. It covers the necessary setup, basic commands, and provides example scripts for common tasks.

## 1. Hardware and Driver Setup

### USB to GPIB Converter
- **Converter:** Keysight USB to GPIB Converter
- **Indicator:** A green light on the converter indicates it's ready.

### Instrument Configuration
- **Enable GPIB:** Ensure GPIB is enabled on your instrument.
- **GPIB Address:** Note the GPIB address of your instrument.

### Driver Installation
Install the required Python packages using pip:
```bash
pip install pyvisa pymeasure numpy pandas matplotlib
```

## 2. Basic Communication Test

You can quickly test the connection to your instruments by listing the available resources.

```python
import pyvisa

# Initialize the resource manager
rm = pyvisa.ResourceManager()

# List all connected resources
print(rm.list_resources())
```
This will print a list of connected instrument IDs. Copy the ID for the instrument you want to communicate with.

## 3. Instrument Initialization

To start sending commands to an instrument, you need to initialize it using its VISA resource ID.

```python
import pyvisa

# Initialize the resource manager
rm = pyvisa.ResourceManager()

# Open a connection to the instrument
keithley = rm.open_resource("GPIB::12") # Replace "GPIB::12" with your instrument's ID

# Reset the instrument and clear its status
keithley.write("*rst; status:preset; *cls")
```

**Key Functions:**
- `keithley.write()`: Sends a command to the instrument. Refer to the instrument's manual for a list of commands.
- `keithley.query()`: Sends a command and returns the instrument's response as a string.

## 4. Using PyMeasure

PyMeasure provides a higher-level interface for many instruments, simplifying communication.

**Discovering Commands:**
You can see the available commands for a `pymeasure` instrument object using the `dir()` function.
```python
# Eg: print(dir(keithley_2400))
```

---

## 5. Example Scripts

### Example 1: Keithley 2400 Current Source Test

This script demonstrates how to interface with a Keithley 2400 to source current.

`2400_current_check.py`
```python
# Name: Interfacing Keithley 2400 (current source)
# Author: Prathamesh
# Created: 27/10/2022
# Copyright: (c) Instrument-DSL 2022

import pymeasure
import numpy as np
from time import sleep
from pymeasure.instruments.keithley import Keithley2400
import pandas as pd

# Object creation
keithley_2400 = Keithley2400("GPIB::4") # Replace with your instrument's ID

# Initial setup for Keithley 2400
keithley_2400.apply_current() # Set up to source current
keithley_2400.source_current_range = 1e-3 # Set the source current range
sleep(10)
keithley_2400.compliance_voltage = 210 # Set the compliance voltage to 210V
keithley_2400.source_current = 0 # Set the source current to 0A
keithley_2400.enable_source() # Enable the source output
sleep(15)

# Ramp to a specific current
cur = 1
keithley_2400.ramp_to_current(cur * 1e-3) # Ramp to 1mA
sleep(15)
print(f"Current set to: {cur * 1e-3} A")
sleep(180)

# Shutdown the instrument
keithley_2400.shutdown() # Ramp the current to 0A and disable output
```

### Example 2: Interfacing Keithley 2400 and Keithley 2182

This script shows how to use a Keithley 2400 as a current source and a Keithley 2182 as a nanovoltmeter to perform an I-V sweep.

`combine-2400-2182-Updated.py`
```python
# Name: Interfacing Keithley 2400 (current source) and Keithley 2182 (nanovoltmeter)
# Author: Instrument-DSL
# Created: 27/10/2022
# Copyright: (c) Instrument-DSL 2022

import pymeasure
import numpy as np
import matplotlib.pyplot as plt
from time import sleep
import pyvisa
from pymeasure.instruments.keithley import Keithley2400
import pandas as pd

# Object creation
rm1 = pyvisa.ResourceManager()
keithley_2182 = rm1.open_resource("GPIB::7") # Replace with your instrument's ID
keithley_2182.write("*rst; status:preset; *cls")
keithley_2400 = Keithley2400("GPIB::4") # Replace with your instrument's ID
sleep(5)

# Data storage
I = []
Volt = []
interval = 1
number_of_readings = 2

# User input
I_range = float(input("Enter value of I: "))
I_step = float(input("Enter steps: "))
filename = input("Enter filename: ")

# Initial setup for Keithley 2400
keithley_2400.apply_current() # Set up to source current
keithley_2400.source_current_range = 1e-6 # Set source current range to 1uA
sleep(10)
keithley_2400.compliance_voltage = 150 # Set compliance voltage to 150V
keithley_2400.source_current = 0 # Set source current to 0A
keithley_2400.enable_source() # Enable the source output
sleep(15)

# I-V sweep loop
for cur in np.arange(-I_range, I_range + I_step, I_step):
    keithley_2400.ramp_to_current(cur * 1e-6)
    sleep(15)
    
    # Configure and trigger Keithley 2182
    keithley_2182.write("status:measurement:enable 512; *sre 1")
    keithley_2182.write(f"sample:count {number_of_readings}")
    keithley_2182.write("trigger:source bus")
    keithley_2182.write(f"trigger:delay {interval}")
    keithley_2182.write(f"trace:points {number_of_readings}")
    keithley_2182.write("trace:feed sense1; feed:control next")
    keithley_2182.write("initiate")
    keithley_2182.assert_trigger()
    sleep(10)
    keithley_2182.wait_for_srq()
    sleep(20)
    
    # Read data
    voltages = keithley_2182.query_ascii_values("trace:data?")
    keithley_2182.query("status:measurement?")
    keithley_2182.write("trace:clear; feed:control next")
    v_avr = sum(voltages) / len(voltages)
    sleep(10)
    
    # Store data
    I.append(cur * 1e-6)
    Volt.append(v_avr)
    print(f"Current: {cur * 1e-6} A, Voltage: {v_avr} V")
    
    # Reset Keithley 2182
    keithley_2182.write("*rst; status:preset; *cls")
    keithley_2182.clear()
    sleep(15)

# Data saving
df = pd.DataFrame({'I': I, 'V': Volt})
print(df)
df.to_csv(f'E:/Python/Python output files/IV Output/Test_IV_data_at_RT_{filename}.txt', index=None, sep=' ', mode='w')

# Plotting
plt.plot(I, Volt, marker='o', linestyle='-', color='g', label='I-V Curve')
plt.xlabel('Current (A)')
plt.ylabel('Voltage (V)')
plt.title('I-V Curve')
plt.legend()
plt.show()

# Shutdown instruments
keithley_2400.shutdown()
keithley_2182.clear()
keithley_2182.close()

```

## 6. Troubleshooting and Tips

- **Restart Instrument:** If you encounter issues, try restarting the instrument.
- **Restart Program:** Restarting the Python script can also resolve connection problems.

## 7. References

- **PyVISA Documentation:** [https://pyvisa.readthedocs.io/en/latest/](https://pyvisa.readthedocs.io/en/latest/)
- **PyMeasure Documentation:** [https://pymeasure.readthedocs.io/en/latest/](https://pymeasure.readthedocs.io/en/latest/)

### Recommended Videos

- [https://www.youtube.com/watch?v=TLUTCDbt52I](https://www.youtube.com/watch?v=TLUTCDbt52I)
- [https://www.youtube.com/watch?v=DUJpL9pMy8Y](https://www.youtube.com/watch?v=DUJpL9pMy8Y)