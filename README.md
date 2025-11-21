<p align="center">
  <img src="assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="150"/>
</p>
<h1 align="center">PICA: Python-based Instrument Control and Automation</h1>
<p align="center">
  A modular software suite for automating laboratory measurements in physics research.
</p>
  
<p align="center">
    <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.9+-brightgreen.svg" alt="Python 3.9+"></a>
    <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/joss_tests.yml"><img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/joss_tests.yml/badge.svg" alt="Run PICA JOSS Tests"></a>
    <a href="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation"><img src="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation/branch/main/graph/badge.svg" alt="codecov"></a>
    <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/codeql.yml"><img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/codeql.yml/badge.svg" alt="CodeQL"></a>
</p>

---

## Overview

**PICA (Python-based Instrument Control and Automation)** is a software suite designed to provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research. The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterization experiments.

A key architectural feature is the use of isolated process execution for each measurement module via Python's `multiprocessing` library, ensuring high stability and preventing inter-script conflicts. This platform is built to streamline data acquisition, enhance experimental reproducibility, and accelerate research workflows.

PICA is designed with a clear separation between the user interface (GUI) and the instrument control logic (backend). This modular approach makes the system easy to maintain, extend, and debug.

<p align="center">
    <img src="assets/Images/PICA_Launcher_v6.png" alt="PICA Launcher Screenshot" width="800"/>
</p>

---

## Architecture

The core design philosophy of PICA is the separation of concerns, implemented through a distinct **GUI-Backend** architecture for each measurement module.

-   **GUI (Frontend):** Each measurement has a dedicated GUI script (e.g., `IV_K2400_GUI_v5.py`) built with `Tkinter`. It is responsible for all user interaction, parameter input, and data visualization (live plotting). It runs in the main process to remain responsive.
-   **Backend:** The instrument control logic is encapsulated in a separate class (e.g., `Keithley2400_Backend`). This class handles all `PyVISA` communication, instrument configuration, and data acquisition commands.
-   **Process Isolation:** When a measurement is started, the GUI launches its corresponding backend logic in a separate, isolated process using Python's `multiprocessing` library. This is the key to PICA's stability: a crash or error in one measurement script will not affect the main launcher or any other running experiments.
-   **Communication:** The frontend and backend communicate via `multiprocessing.Queue` for thread-safe data exchange. The backend performs a measurement and places the data into a queue, which the frontend then reads to update plots and save to a file.

---

## Table of Contents

- [Core Features](#core-features)
- [Tech Stack & Dependencies](#tech-stack--dependencies)
- [Available Measurement Modules](#available-measurement-modules)
- [Instrument Specifications](#instrument-specifications)
- [Getting Started](#getting-started)
- [Resources & Documentation](#resources--documentation)
- [How to Cite](#how-to-cite)
- [Contributing](#contributing)
- [Authors & Acknowledgments](#authors--acknowledgments)
- [License](#license)

---

## Core Features

- **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
- **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability and preventing resource conflicts.
- **Integrated VISA Instrument Scanner:** An embedded utility for discovering, identifying, and troubleshooting GPIB/VISA instrument connections.
- **Modular Architecture:** Each experimental setup is encapsulated in a self-contained module with direct access to its scripts and data directories.
- **Embedded Documentation:** In-application viewer for essential project documentation.
- **System Console Log:** A real-time log provides status updates, confirmations, and error diagnostics for all operations.

---

## Tech Stack & Dependencies

The core of PICA is built with a stack of robust and widely-used Python libraries.

- **Primary Language:** **Python 3.9+**
- **Graphical User Interface:** **Tkinter**
- **Instrument Communication:** **PyVISA** (a Python wrapper for the NI-VISA library)
- **Numerical Operations:** **NumPy**
- **Data Visualization:** **Matplotlib**
- **Concurrency:** **Multiprocessing** (a native Python library for process isolation)

All required packages are listed in the `requirements.txt` file for easy one-step installation.

---
## Available Measurement Modules

The PICA suite is organized into modules, each containing a frontend GUI application and its corresponding backend logic for instrument control.

#### Low Resistance (Keithley 6221 / 2182)
* **Delta Mode I-V Sweep**
* **Delta Mode R vs. T (Active Control)**
* **Delta Mode R vs. T (Passive Sensing)**

#### Mid Resistance (Keithley 2400)
* **I-V Sweep**
* **R vs. T (Active Control)**
* **R vs. T (Passive Sensing)**

#### Mid Resistance, High Precision (Keithley 2400 / 2182)
* **I-V Sweep**
* **R vs. T (Active Control)**
* **R vs. T (Passive Sensing)**

#### High Resistance (Keithley 6517B)
* **I-V Sweep**
* **R vs. T (Active Control)**
* **R vs. T (Passive Sensing)**

#### Pyroelectric Measurement (Keithley 6517B)
* **PyroCurrent vs. T**

#### Capacitance (Keysight E4980A)
* **C-V Measurement**

#### Temperature Utilities (Lakeshore 350)
* **Temperature Ramp**
* **Temperature Monitor**

---

## Instrument Specifications


### Advanced Cryogenic Transport Measurement System

**Overview**
This facility provides users with a comprehensive, modular system for characterizing the full spectrum of electronic transport properties in cryogenic environments. The setup integrates multiple high-precision instruments to cover the entire resistance scale, from superconductors to perfect insulators.

* **Temperature Range:** 80 K to 320 K.
* **Complete Resistance Range:** $10 n\Omega$ to $10 P\Omega$, i.e., covering 24-orders-of-magnitude.

**Measurement Modules**

| Module | Configuration / Instrument | Use Case | Resistance Range |
| :--- | :--- | :--- | :--- |
| **1. Low-Resistance (Delta Mode)** | **Keithley 6221** (Current Source) + **K2182** (Nanovoltmeter) | For superconductors & metallic films; actively cancels thermal offsets. | $10 n\Omega$ to $100 M\Omega$ |
| **2. Mid-Resistance (Standard)** | **Keithley 2400** Source Meter | For semiconductors, oxides. | $100 \mu\Omega$ to $200 M\Omega$ |
| **3. Mid-Resistance (High-Precision)** | **Keithley 2400** (Source) + **K2182** (Nanovoltmeter) | Detects subtle phase transitions in semiconductors, oxides. | $1 \mu\Omega$ to $100 M\Omega$ |
| **4. High-Resistance** | **Keithley 6517B** Electrometer | Measures dielectrics, polymers, & ceramics. | $1 \Omega$ to $10 P\Omega$ ($10^{16}\Omega$) |

---

## 🚀 Getting Started

### Prerequisites

1.  **Python:** Python 3.9 or newer is recommended.
2.  **NI-VISA Driver:** You must install the [National Instruments VISA Driver](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html) for your operating system. This is required for Python's `pyvisa` library to communicate with the instruments.

### Installation Steps

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create a Virtual Environment**
    Using a virtual environment is strongly recommended to avoid conflicts with other Python projects.
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
    python PICA_v6.py
    ```

---

## 📚 Resources & Documentation

**Comprehensive Documentation:**
For detailed setup instructions, hardware interfacing guides, and troubleshooting, please refer to the **[PICA User Manual](docs/User_Manual.md)** located in the `docs/` directory.

**Instrument Manuals:**
A collection of official instrument manuals is provided within the `assets/Manuals/` directory for technical reference.

---

## How to Cite

If you use this software in your research, please cite it. This helps to credit the work involved in creating and maintaining this resource.

#### BibTeX Entry

```bibtex
@software{Deshmukh_PICA_2023,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  month        = sep,
  year         = 2023,
  publisher    = {GitHub},
  version      = {14.1.0},
  url          = {https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation}
}
```

Alternatively, you can use the `CITATION.cff` file in the root of the repository for automatic parsing by modern reference managers.

---

## 🤝 How to Contribute

Contributions are welcome! If you have suggestions for improvements or want to add a new instrument module, please feel free to:

1.  Fork the repository.
2.  Create a new branch (`git checkout -b feature/YourFeature`).
3.  Commit your changes (`git commit -m 'Add some feature'`).
4.  Push to the branch (`git push origin feature/YourFeature`).
5.  Open a Pull Request.

Please open an issue first to discuss any major changes you would like to make.

---

## Authors & Acknowledgments

<p align="center">
  <img src="assets/LOGO/UGC_DAE_CSR_NBG.jpeg" alt="UGC DAE CSR Logo" width="150">
</p>

- **Lead Developer:** **[Prathamesh Deshmukh](https://prathameshdeshmukh.site/)**
- **Principal Investigator:** **[Dr. Sudip Mukherjee](https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)**
- **Affiliation:** *[UGC-DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre)*

#### Funding
Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.