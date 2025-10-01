
#     PICA-Python-Instrument-Control-and-Automation 

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![UI Framework](https://img.shields.io/badge/UI-Tkinter-red.svg)](https://docs.python.org/3/library/tkinter.html)

**PICA Launcher** is the central graphical interface for the **Python Instrument Control & Automation (PICA)** suite. It provides a robust, user-friendly dashboard to launch and manage various materials science and physics measurement scripts.

Its core feature is launching each measurement module in a **completely isolated process** using Python's `multiprocessing` library. This ensures that individual experiments run independently, preventing crashes or errors in one module from affecting the main launcher or other running experiments.

---

## Key Features

* **Centralized Dashboard:** A single, intuitive interface to access all measurement modules.
* **Isolated Process Launching:** Each script runs in its own process, ensuring stability and preventing resource conflicts between different GUIs (e.g., `tkinter`, `PySide6`).
* **Integrated GPIB/VISA Scanner:** A built-in tool to scan for, identify, and troubleshoot connections with laboratory instruments without leaving the application.
* **Direct Folder Access:** Quickly open the directory for any measurement module to view scripts or saved data.
* **Built-in Documentation Viewer:** Read the project's README and LICENSE files directly within the launcher's UI.
* **Real-time Console Log:** A console in the main window provides status updates, launch confirmations, and error messages.

---

## Available Measurement Modules

The launcher provides access to the following instrument control systems:

#### Low Resistance (Keithley 6221 / 2182A)
* **I-V Measurement:** High-precision I-V sweeps using Delta Mode.
* **R vs. T (Active):** Resistance vs. Temperature measurements with active temperature control.
* **R vs. T (Passive):** Resistance vs. Temperature measurements while monitoring a passive temperature change.

#### Mid Resistance (Keithley 2400 & 2400/2182)
* **I-V Measurement (K2400):** Standard four-probe I-V sweeps.
* **R vs. T Measurement (K2400):** Four-probe Resistance vs. Temperature.
* **I-V Measurement (K2400/2182):** Higher precision I-V sweeps using the 2182 Nanovoltmeter.
* **R vs. T Measurement (K2400/2182):** Higher precision Resistance vs. Temperature.

#### High Resistance (Keithley 6517B Electrometer)
* **I-V Measurement:** For high-resistance materials and insulators.
* **R vs. T (Active):** High-resistance measurements with controlled temperature ramps.
* **R vs. T (Passive):** High-resistance measurements during passive temperature changes.

#### Dielectric & Pyroelectric Properties (Keithley 6517B)
* **Pyroelectric Current vs. Temp:** Measures pyroelectric current during a temperature ramp.

#### LCR Meter (Keysight E4980A)
* **C-V Measurement:** Automates Capacitance vs. Voltage sweeps.

#### Lock-in Amplifier
* **AC Measurement:** For AC transport and susceptibility measurements.

#### Temperature Control (Lakeshore 340/350)
* **Temperature Ramp:** A standalone utility to control temperature ramps.
* **Temperature Monitor:** A passive monitor for logging temperature.

---

## Installation & Usage

### Step 1: Clone the Repository
```bash
git clone [https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git)
cd PICA-Python-Instrument-Control-and-Automation
````

### Step 2: Create `requirements.txt`

Create a file named `requirements.txt` in the main project directory and paste the following content into it:

```
numpy==1.26.4
pandas
matplotlib
pymeasure
pyvisa
pyvisa-py
pyqtgraph
pillow
PySide6
```

### Step 3: Install Dependencies

It's highly recommended to use a Python virtual environment.

```bash
# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows, use: venv\Scripts\activate

# Install all required packages from the file
pip install -r requirements.txt
```

### Step 4: Run the Launcher

Execute the main launcher script from the root directory of the project.

```bash
python PICA_Launcher.py
```

*(Note: Ensure the launcher script is named `PICA_Launcher.py` or adjust the command accordingly.)*

-----

## Author & Acknowledgment

  * Developed by **Prathamesh Deshmukh**
  * Vision & Guidance by **[Dr. Sudip Mukherjee](https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)**
  * *UGC-DAE Consortium for Scientific Research, Mumbai Centre*

-----

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.
