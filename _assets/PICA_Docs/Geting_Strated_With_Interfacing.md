# Python Instrument Interfacing 
A quick guide to setting up and controlling laboratory instruments using Python.

---

## 1. Prerequisites & Setup

### Hardware
* **USB to GPIB Converter:** An interface cable (e.g., from Keysight) to connect your computer to the instrument. Ensure the converter's status light (often green) is on, indicating a proper connection.

### Software & Drivers
* **Python Packages:** Install the necessary libraries using pip.
    * `pyvisa`
    * `pymeasure`
    * `numpy`
    * `pandas`
    * `matplotlib`

* **Installation Command:** Open your Command Prompt (CMD) and run:
    ```bash
    pip install package_name
    ```

### Instrument Configuration
* On your instrument, ensure the **GPIB communication is turned on**.
* Take note of the instrument's **GPIB Address** (e.g., `12`, `24`).

---

## 2. Basic Connection Test

You can quickly verify which instruments are connected and recognized by your system.

* **List Connected Instruments:** Run the following Python code:
    ```python
    import pyvisa
    rm = pyvisa.ResourceManager()
    print(rm.list_resources())
    ```
    This will print a tuple of VISA resource strings (e.g., `'GPIB0::12::INSTR'`). Copy these resource strings for the next step.

---

## 3. Communicating with Instruments

### VISA Initialization
Use the resource string from the previous step to establish a connection with a specific instrument.

```python
# Initialize a connection to an instrument at GPIB address 12
keithley = rm.open_resource("GPIB::12")

# It's good practice to reset the instrument to a known state
keithley.write("*rst; status:preset; *cls")
