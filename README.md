

# PICA: Python-based Instrument Control and Automation

[](https://opensource.org/licenses/MIT)
[](https://www.python.org/downloads/)

## Abstract

**PICA (Python-based Instrument Control and Automation)** is a software suite designed to provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research. The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterization experiments. A key architectural feature is the use of isolated process execution for each measurement module via Python's `multiprocessing` library, ensuring high stability and preventing inter-script conflicts. This platform is intended to streamline data acquisition and improve experimental reproducibility.

-----

## Core Features

  * **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
  * **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability and preventing resource conflicts between concurrent tasks.
  * **Integrated VISA Instrument Scanner:** An embedded utility for discovering, identifying, and troubleshooting GPIB/VISA instrument connections.
  * **Modular Architecture:** Each experimental setup is encapsulated in a self-contained module with direct access to its scripts and data directories.
  * **Embedded Documentation:** Includes an in-application viewer for essential project documentation, such as the README and software license.
  * **System Console Log:** A real-time log provides status updates, confirmations, and error diagnostics for all operations.

-----

## Available Measurement Modules

The PICA suite includes modules for a range of standard electrical and thermal transport measurements:

#### Low-Resistance Measurements (Keithley 6221 / 2182A)

  * **Current-Voltage (I-V) Characterization:** High-precision I-V sweeps employing the AC Delta Mode for noise reduction.
  * **Resistance vs. Temperature (R-T):** Automated R-T data acquisition with active (controlled ramp) and passive (monitoring) temperature profiles.

#### Mid-Resistance Measurements (Keithley 2400 SourceMeter)

  * **Four-Probe I-V Characterization:** Standard I-V sweeps for materials like semiconductors.
  * **Four-Probe R-T Characterization:** Temperature-dependent resistance measurements.

#### Mid-Resistance, High-Precision (Keithley 2400 / 2182)

  * **High-Precision I-V:** Enhanced voltage resolution using a Keithley 2182 Nanovoltmeter.
  * **High-Precision R-T:** Temperature-dependent measurements with enhanced voltage precision.

#### High-Resistance Measurements (Keithley 6517B Electrometer)

  * **I-V Characterization:** For insulating materials, dielectrics, and high-impedance devices.
  * **Resistivity vs. Temperature:** High-resistance measurements with active or passive temperature control.

#### Dielectric & Pyroelectric Characterization (Keithley 6517B)

  * **Pyroelectric Current Measurement:** Quantifies pyroelectric current as a function of a controlled temperature ramp.

#### LCR Characterization (Keysight E4980A)

  * **Capacitance-Voltage (C-V) Sweeps:** Automated C-V measurements for semiconductor and dielectric analysis.

#### AC Transport Measurements (Lock-in Amplifier)

  * **AC Resistance:** For measuring AC transport properties and contact impedance.

#### Environmental Control (Lakeshore 340/350)

  * **Temperature Control Utility:** A standalone module for defining and executing temperature ramps and setpoints.
  * **Temperature Monitoring Utility:** A passive data logger for monitoring environmental temperature.

-----

## Installation and Execution

### Step 1: Clone the Repository

Clone the repository to your local machine using Git.

```bash
git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
cd PICA-Python-Instrument-Control-and-Automation
```

### Step 2: Establish Python Environment

It is strongly recommended to use a virtual environment to manage dependencies. Create a file named `requirements.txt` in the project's root directory with the following contents:

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

Install the required packages from the `requirements.txt` file.

```bash
# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows, use: venv\Scripts\activate

# Install all required packages
pip install -r requirements.txt
```

### Step 4: Launch the Application

Execute the main launcher script from the project's root directory.

```bash
python PICA_Launcher.py
```

-----

## Documentation and Technical Guides

#### Included Manuals

A collection of official instrument manuals and software library documentation is provided within the `/_assets/Manuals/` directory. These documents serve as valuable technical references.

#### Instrument Interfacing Guide

For a detailed technical guide on hardware setup, instrument configuration, and connection testing, please consult the **[Python Instrument Interfacing Guide](https://www.google.com/search?q=https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/_assets/Manuals/README.md)**.

-----

## Author and Acknowledgments

\<img src="https://www.google.com/search?q=https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/\_assets/LOGO/UGC\_DAE\_CSR.jpeg%3Fraw%3Dtrue" alt="UGC DAE CSR Logo" width="150"\>

  * **Lead Developer:** **[Prathamesh Deshmukh](https://prathameshdeshmukh.site/)**
  * **Principal Investigator:** **[Dr. Sudip Mukherjee](https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)**
  * **Affiliation:** *[UGC-DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre)*

#### Funding Acknowledgments

Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.

-----

## License

This project is licensed under the terms of the MIT License. Please see the `LICENSE` file for full details.
