<div align="center">
  <img src="_assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="150"/>
  <h1>PICA: Python-based Instrument Control and Automation</h1>
  <p>A modular software suite for automating laboratory measurements in physics research.</p>
  
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
    <img src="_assets/Images/PICA_Launcher_v6.png" alt="PICA Launcher Screenshot" width="800"/>
</div>

---

## Table of Contents

- Core Features
- Tech Stack & Dependencies
- Available Measurement Modules
- Getting Started
- Resources & Documentation
- Contributing
- Authors & Acknowledgments
- License

---

## Core Features

- **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
- **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability and preventing resource conflicts.
- **Integrated VISA Instrument Scanner:** An embedded utility for discovering, identifying, and troubleshooting GPIB/VISA instrument connections.
- **Modular Architecture:** Each experimental setup is encapsulated in a self-contained module with direct access to its scripts and data directories.
- **Embedded Documentation:** In-application viewer for essential project documentation, such as this README and the software license.
- **System Console Log:** A real-time log provides status updates, confirmations, and error diagnostics for all operations.

---

## Tech Stack & Dependencies

The core of PICA is built with a stack of robust and widely-used Python libraries.

- **Primary Language:** **Python 3.9+**
- **Graphical User Interface:** **Tkinter**
- **Instrument Communication:** **PyVISA** (a Python wrapper for the NI-VISA library)
- **Numerical Operations:** **NumPy**
- **Data Structuring:** **Pandas**
- **Data Visualization:** **Matplotlib**
- **Concurrency:** **Multiprocessing** (a native Python library for process isolation)

---

## Available Measurement Modules

The PICA suite includes modules for a range of standard electrical and thermal transport measurements.

| Instrument Combination         | Measurement Type                     | Description                                                                 |
| ------------------------------ | ------------------------------------ | --------------------------------------------------------------------------- |
| **Keithley 6221 / 2182** | I-V Characterization (AC Delta)      | High-precision I-V sweeps for low-resistance samples.                       |
|                                | Resistance vs. Temperature (R-T)     | Automated R-T data acquisition with **active** (ramp/stabilize) or **passive** (sensing/logging) temperature profiles. |
| **Keithley 2400 SourceMeter** | Four-Probe I-V Characterization      | Standard I-V sweeps for materials like semiconductors.                      |
|                                | Four-Probe R-T Characterization      | Temperature-dependent resistance measurements with **active** or **passive** modes. |
| **Keithley 2400 / 2182** | High-Precision I-V                   | Enhanced voltage resolution using a nanovoltmeter.                          |
|                                | High-Precision R-T                   | Temperature-dependent measurements with enhanced voltage precision and **active** or **passive** modes. |
| **Keithley 6517B Electrometer**| High-Resistance I-V Characterization | For insulating materials, dielectrics, and high-impedance devices.          |
|                                | High-Resistance R-T                  | High-resistance measurements with **active** or **passive** temperature control.    |
|                                | Pyroelectric Current vs. Temperature | Quantifies pyroelectric current during a controlled temperature ramp.       |
| **Keysight E4980A LCR Meter** | Capacitance-Voltage (C-V) Sweeps     | Automated C-V measurements for semiconductor and dielectric analysis.       |
| **Lock-in Amplifier** | AC Resistance Measurement            | For measuring AC transport properties and contact impedance.                |
| **Lakeshore 340/350 Controller** | Temperature Control Utility          | A standalone module for defining and executing temperature profiles.        |
|                                | Temperature Monitoring Utility       | A passive data  for monitoring environmental temperature.             |

---

## Getting Started

### Prerequisites

1.  **NI-VISA Driver:** You must install the National Instruments VISA Driver for your operating system. This is required for the software to communicate with the instruments.

### Using the Application

This executable (`Picachu.exe`) is a standalone application. Simply run it to open the PICA Launcher. All required dependencies are bundled. For access to the source code, please visit the project's GitHub repository.

---

## 📚 Resources & Documentation

#### Included Manuals
A collection of official instrument manuals and software library documentation is provided within the `/_assets/Manuals/` directory (if included in the distribution) or can be accessed via the "Manuals" button in the launcher.

---

## 🤝 Contributing
This is a standalone executable. To contribute to the project, please visit the source code repository on GitHub.

---

## 🧑‍🔬 Authors & Acknowledgments

- **Lead Developer:** **Prathamesh Deshmukh**
- **Principal Investigator:** **Dr. Sudip Mukherjee**
- **Affiliation:** *UGC-DAE Consortium for Scientific Research, Mumbai Centre*

---

## License

This project is licensed under the terms of the MIT License. See the `LICENSE` file for full details.