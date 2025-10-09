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

PICA is designed with a clear separation between the user interface (frontend) and the instrument control logic (backend). This modular approach makes the system easy to maintain, extend, and debug.

<div align="center">
    <img src="_assets/Images/PICA_Launcher_v6.png" alt="PICA Launcher Screenshot" width="800" />
</div>

---

## Architecture

The core design philosophy of PICA is the separation of concerns, implemented through a distinct **Frontend-Backend** architecture for each measurement module.

-   **Frontend:** Each measurement has a dedicated GUI script (e.g., `IV_K2400_Frontend_v5.py`) built with `Tkinter`. It is responsible for all user interaction, parameter input, and data visualization (live plotting). It runs in the main process.

-   **Backend:** The instrument control logic is encapsulated in a separate class (e.g., `Keithley2400_Backend`). This class handles all `PyVISA` communication, instrument configuration, and data acquisition commands.

-   **Process Isolation:** When a measurement is started, the frontend launches its corresponding backend logic in a separate, isolated process using Python's `multiprocessing` library. This is the key to PICA's stability: a crash or error in one measurement script will not affect the main launcher or any other running experiments.

-   **Communication:** The frontend and backend communicate via `multiprocessing.Queue` for thread-safe data exchange. The backend performs a measurement and places the data into a queue, which the frontend then reads to update plots and save to a file.

## Table of Contents

- Core Features
- Tech Stack & Dependencies
- Available Scripts & Modules
- Getting Started
- Resources & Documentation
- Contributing
- Authors & Acknowledgments
- License

---

## Available Scripts & Modules

The PICA suite is organized into modules, each containing a frontend GUI application and its corresponding backend logic for instrument control.

---

#### Low Resistance (Keithley 6221 / 2182)
*   **Delta Mode I-V Sweep**
    *   **Frontend:** `IV_K6221_DC_Sweep_Frontend_V10.exe`
*   **Delta Mode R vs. T (Active Control)**
    *   **Frontend:** `Delta_RT_K6221_K2182_L350_T_Control_Frontend_v5.exe`
*   **Delta Mode R vs. T (Passive Sensing)**
    *   **Frontend:** `Delta_RT_K6221_K2182_L350_Sensing_Frontend_v5.exe`

#### Mid Resistance (Keithley 2400)
*   **I-V Sweep**
    *   **Frontend:** `IV_K2400_Frontend_v5.exe`
*   **R vs. T (Active Control)**
    *   **Frontend:** `RT_K2400_L350_T_Control_Frontend_v3.exe`
*   **R vs. T (Passive Sensing)**
    *   **Frontend:** `RT_K2400_L350_T_Sensing_Frontend_v4.exe`

#### Mid Resistance, High Precision (Keithley 2400 / 2182)
*   **I-V Sweep**
    *   **Frontend:** `IV_K2400_K2182_Frontend_v3.exe`
*   **R vs. T (Active Control)**
    *   **Frontend:** `RT_K2400_K2182_T_Control_Frontend_v3.exe`
*   **R vs. T (Passive Sensing)**
    *   **Frontend:** `RT_K2400_2182_L350_T_Sensing_Frontend_v2.exe`

#### High Resistance (Keithley 6517B)
*   **I-V Sweep**
    *   **Frontend:** `IV_K6517B_Frontend_v11.exe`
*   **R vs. T (Active Control)**
    *   **Frontend:** `RT_K6517B_L350_T_Control_Frontend_v13.exe`
*   **R vs. T (Passive Sensing)**
    *   **Frontend:** `RT_K6517B_L350_T_Sensing_Frontend_v14.exe`

#### Pyroelectric Measurement (Keithley 6517B)
*   **PyroCurrent vs. T**
    *   **Frontend:** `Pyroelectric_K6517B_L350_Frontend_v4.exe`

#### Capacitance (Keysight E4980A)
*   **C-V Measurement**
    *   **Frontend:** `CV_KE4980A_Frontend_v3.exe`

#### Temperature Utilities (Lakeshore 350)
*   **Temperature Ramp**
    *   **Frontend:** `T_Control_L350_RangeControl_Frontend_v8.exe`
*   **Temperature Monitor**
    *   **Frontend:** `T_Sensing_L350_Frontend_v4.exe`

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