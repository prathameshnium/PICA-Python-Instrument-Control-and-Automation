<div align="center">
  <img src="https://raw.githubusercontent.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/main/_assets/LOGO/PICA_Logo.png" alt="PICA Logo" width="150"/>
  <h1>PICA: Python-based Instrument Control and Automation</h1>
  <p>
    A modular software suite for automating laboratory measurements in physics research.
  </p>
  
  <p>
    <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.9+-brightgreen.svg" alt="Python 3.9+"></a>
    <a href="#"><img src="https://img.shields.io/badge/Status-Active-success.svg" alt="Project Status: Active"></a>
  </p>
</div>

---

## Overview

**PICA (Python-based Instrument Control and Automation)** is a software suite designed to provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research. The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterization experiments. 

A key architectural feature is the use of isolated process execution for each measurement module via Python's `multiprocessing` library, ensuring high stability and preventing inter-script conflicts. This platform is built to streamline data acquisition, enhance experimental reproducibility, and accelerate research workflows.

<div align="center">
    <img src="https://raw.githubusercontent.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/main/_assets/screenshots/PICA_Launcher_Screenshot.png" alt="PICA Launcher Screenshot" width="800"/>
</div>

---

## Table of Contents

- [Core Features](#core-features)
- [Available Measurement Modules](#available-measurement-modules)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation Steps](#installation-steps)
- [Resources & Documentation](#resources--documentation)
- [Contributing](#contributing)
- [Authors & Acknowledgments](#authors--acknowledgments)
- [License](#license)

---

## Core Features

* **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
* **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability and preventing resource conflicts.
* **Integrated VISA Instrument Scanner:** An embedded utility for discovering, identifying, and troubleshooting GPIB/VISA instrument connections.
* **Modular Architecture:** Each experimental setup is encapsulated in a self-contained module with direct access to its scripts and data directories.
* **Embedded Documentation:** In-application viewer for essential project documentation, such as the README and software license.
* **System Console Log:** A real-time log provides status updates, confirmations, and error diagnostics for all operations.

---

## Available Measurement Modules

The PICA suite includes modules for a range of standard electrical and thermal transport measurements.

| Instrument Combination                  | Measurement Type                          | Description                                                                 |
| --------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------- |
| **Keithley 6221 / 2182A** | I-V Characterization (AC Delta)           | High-precision I-V sweeps for low-resistance samples.                       |
|                                         | Resistance vs. Temperature (R-T)          | Automated R-T data acquisition with active or passive temperature profiles. |
| **Keithley 2400 SourceMeter** | Four-Probe I-V Characterization           | Standard I-V sweeps for materials like semiconductors.                      |
|                                         | Four-Probe R-T Characterization           | Temperature-dependent resistance measurements.                              |
| **Keithley 2400 / 2182A** | High-Precision I-V                        | Enhanced voltage resolution using a nanovoltmeter.                          |
|                                         | High-Precision R-T                        | Temperature-dependent measurements with enhanced voltage precision.         |
| **Keithley 6517B Electrometer** | High-Resistance I-V Characterization      | For insulating materials, dielectrics, and high-impedance devices.          |
|                                         | Resistivity vs. Temperature               | High-resistance measurements with active or passive temperature control.    |
|                                         | Pyroelectric Current vs. Temperature      | Quantifies pyroelectric current during a controlled temperature ramp.       |
| **Keysight E4980A LCR Meter** | Capacitance-Voltage (C-V) Sweeps          | Automated C-V measurements for semiconductor and dielectric analysis.       |
| **Lock-in Amplifier (Generic)** | AC Resistance Measurement                 | For measuring AC transport properties and contact impedance.                |
| **Lakeshore 340/350 Controller** | Temperature Control Utility               | A standalone module for defining and executing temperature profiles.        |
|                                         | Temperature Monitoring Utility            | A passive data logger for monitoring environmental temperature.             |

---

## Getting Started

### Prerequisites

1.  **Python:** Python 3.9 or newer is recommended.
2.  **NI-VISA Driver:** You must install the [National Instruments VISA Driver](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html) for your operating system. This is required for Python's `pyvisa` library to communicate with the instruments.

### Installation Steps

1.  **Clone the Repository**
    ```bash
    git clone [https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git)
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create a Virtual Environment**
    It is highly recommended to use a virtual environment to manage dependencies and avoid conflicts.
    ```bash
    # Create the virtual environment
    python -m venv venv
    
    # Activate the environment
    # On Windows:
    venv\Scripts\activate
    # On macOS/Linux:
    source venv/bin/activate
    ```

3.  **Install Dependencies**
    This project uses a `requirements.txt` file to manage all necessary packages.
    ```bash
    pip install -r requirements.txt
    ```

4.  **Launch the Application**
    Execute the main launcher script from the project's root directory.
    ```bash
    python PICA_Launcher.py
    ```

---

## Resources & Documentation

#### Included Manuals
A collection of official instrument manuals and software library documentation is provided within the `/_assets/Manuals/` directory. These documents serve as valuable technical references.

#### Instrument Interfacing Guide
For a detailed technical guide on hardware setup, instrument configuration, and connection testing, please consult the **[Python Instrument Interfacing Guide](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/_assets/Manuals/README.md)**.

---

## Contributing
Contributions are welcome! If you have suggestions for improvements or want to add a new instrument module, please feel free to:
1.  Fork the repository.
2.  Create a new branch (`git checkout -b feature/YourFeature`).
3.  Commit your changes (`git commit -m 'Add some feature'`).
4.  Push to the branch (`git push origin feature/YourFeature`).
5.  Open a Pull Request.

Please open an issue first to discuss any major changes you would like to make.

---

## Authors & Acknowledgments

<img src="https://raw.githubusercontent.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/main/_assets/LOGO/UGC_DAE_CSR.jpeg" alt="UGC DAE CSR Logo" width="150">

* **Lead Developer:** **[Prathamesh Deshmukh](https://prathameshdeshmukh.site/)**
* **Principal Investigator:** **[Dr. Sudip Mukherjee](https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)**
* **Affiliation:** *[UGC-DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre)*

#### Funding
Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.

---

## License

This project is licensed under the terms of the MIT License. See the `LICENSE` file for full details.
