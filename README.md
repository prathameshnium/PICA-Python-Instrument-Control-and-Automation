![PICA CI](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/python-app.yml/badge.svg)
[![codecov](https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation/branch/main/graph/badge.svg)](https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://www.python.org/downloads/)

<p align="center">
  <img src="./assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="150"/>
</p>

<h1 align="center">PICA: Python-based Instrument Control and Automation</h1>

<p align="center">
  <strong>A modular software suite for automating laboratory measurements in physics research.</strong>
</p>

---

## Overview

**PICA (Python-based Instrument Control and Automation)** is a software suite designed to provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research. The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterization experiments.

A key architectural feature is the use of **isolated process execution** for each measurement module via Python's `multiprocessing` library. This ensures high stability, prevents inter-script conflicts, and allows the main dashboard to remain responsive during long-running experiments.

> **⚠️ Important Note on Testing & Validation**
>
> This software is actively used for daily laboratory measurements and has been verified on physical instruments (Keithley, Lakeshore, etc.).
>
> Recently, significant updates were made to the codebase to integrate **Automated CI/CD Testing** (simulations and logic checks). While these automated tests pass successfully, the refactoring required for them may have introduced subtle timing or hardware-specific regressions. **A comprehensive round of manual validation on the physical instruments is currently underway** to ensure full operational stability.

---

## Table of Contents

- [Architecture](#architecture)
- [Core Features](#core-features)
- [Instrument Specifications](#instrument-specifications)
- [Getting Started](#-getting-started)
- [Running Tests](#-running-tests)
- [Resources & Documentation](#-resources--documentation)
- [Citation](#citation)
- [License](#license)
- [Authors & Acknowledgments](#authors--acknowledgments)

---

## Architecture

The core design philosophy of PICA is the separation of concerns, implemented through a distinct **GUI-Backend** architecture for each measurement module.

- **GUI (Frontend):** Each measurement has a dedicated GUI script (e.g., `IV_K2400_GUI_v5.py`) built with `Tkinter`. It is responsible for user interaction, parameter input, and real-time data visualization using `Matplotlib`.
- **Backend:** The instrument control logic is encapsulated in separate classes (e.g., `Keithley2400_Backend`). This layer handles all `PyVISA` communication, SCPI command parsing, and data retrieval.
- **Process Isolation:** When a measurement starts, the GUI launches the backend logic in a separate, isolated process. This prevents a hardware timeout or script error from crashing the entire application suite.
- **Inter-Process Communication:** The frontend and backend communicate via thread-safe `multiprocessing.Queues`, allowing for high-speed data transfer without race conditions.

---

## Core Features

- **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
- **Integrated VISA Instrument Scanner:** An embedded utility for identifying and troubleshooting GPIB/VISA connections via the NI-VISA backend.
- **Modular Design:** Each experimental setup is a self-contained module, making the codebase easy to extend.
- **Embedded Documentation:** In-application viewer for technical manuals and project guides.
- **System Console Log:** A real-time logging system that provides status updates and error diagnostics.

---

## Instrument Specifications

### Advanced Cryogenic Transport Measurement System

This software controls a facility designed for characterizing the full spectrum of electronic transport properties in cryogenic environments (80 K to 320 K). The setup integrates multiple high-precision instruments to cover a resistance range spanning 24 orders of magnitude.

| Module | Configuration / Instrument | Use Case | Resistance Range |
| :--- | :--- | :--- | :--- |
| **1. Low-Resistance (Delta Mode)** | **Keithley 6221** (Current Source) + **K2182** (Nanovoltmeter) | Superconductors & metallic films; actively cancels thermal EMFs. | $10 n\Omega$ to $100 M\Omega$ |
| **2. Mid-Resistance (Standard)** | **Keithley 2400** SourceMeter | Semiconductors, oxides, general transport. | $100 \mu\Omega$ to $200 M\Omega$ |
| **3. Mid-Resistance (High-Precision)** | **Keithley 2400** + **K2182** | Detecting subtle phase transitions. | $1 \mu\Omega$ to $100 M\Omega$ |
| **4. High-Resistance** | **Keithley 6517B** Electrometer | Dielectrics, polymers, & ceramics. | $1 \Omega$ to $10^{16} \Omega$ |

---

## 🚀 Getting Started

### Prerequisites

1.  **Python:** Python 3.10 or newer is recommended.
2.  **NI-VISA Driver:** You must install the [National Instruments VISA Driver](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html) or an equivalent backend. This is required for the `pyvisa` library to communicate with the instruments.

### Installation Steps

1.  **Clone the Repository**
    ```bash
    git clone [https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git)
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create a Virtual Environment**
    Recommended to verify dependencies and avoid conflicts.
    ```bash
    # Create the virtual environment
    python -m venv venv
    
    # Activate (Windows)
    venv\Scripts\activate
    # Activate (macOS/Linux)
    source venv/bin/activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Launch the Application**
    ```bash
    python PICA_v6.py
    ```

---

## 🧪 Running Tests

This repository includes a robust test suite using `pytest`. It mocks hardware interactions and GUI components, allowing the logic to be verified in a headless environment (CI).

To run the tests locally:

1.  **Install Test Dependencies:**
    ```bash
    pip install pytest pytest-cov flake8
    ```

2.  **Run the Test Suite:**
    ```bash
    python -m pytest
    ```

3.  **Generate Coverage Report:**
    ```bash
    # Generates an HTML report in the htmlcov/ directory
    python -m pytest --cov=. --cov-report=html
    ```

The testing pipeline verifies:
* **Package Integrity:** Ensures all scripts compile and import correctly.
* **GUI Layouts:** Validates that `Tkinter` and `Matplotlib` widgets initialize without errors (using mocked graphics).
* **Backend Logic:** Simulates measurement loops (e.g., Temperature Ramps) to verify logic flow without needing physical instruments connected.

---

## 📚 Resources & Documentation

* **User Manual:** Detailed setup and troubleshooting guides are available in [docs/User_Manual.md](docs/User_Manual.md).
* **Instrument Manuals:** Original PDF manuals for the supported hardware are located in `assets/Manuals/`.

---

## Citation

If you use this software in your research, please cite it using the following BibTeX entry:

```bibtex
@software{Deshmukh_PICA_2023,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  month        = sep,
  year         = 2023,
  publisher    = {GitHub},
  version      = {14.1.0},
  url          = {[https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation)}
}
````

Alternatively, refer to the `CITATION.cff` file in the root directory.

-----

## Authors & Acknowledgments

\<p align="center"\>
\<img src="./assets/LOGO/UGC\_DAE\_CSR\_NBG.jpeg" alt="UGC DAE CSR Logo" width="150"\>
\</p\>

  - **Lead Developer:** **[Prathamesh Deshmukh](https://prathameshdeshmukh.site/)**
  - **Principal Investigator:** **[Dr. Sudip Mukherjee](https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)**
  - **Affiliation:** *[UGC-DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre)*

**Funding:**
Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.

-----

## License

This project is licensed under the MIT License - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details.

