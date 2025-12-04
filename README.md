<p align="center">
  <img src="pica/assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="150"/>
</p>

<h1 align="center">PICA: Python-based Instrument Control and Automation</h1>

<p align="center">
  <strong>A modular software suite for automating laboratory measurements in physics research.</strong>
</p>

<div align="center">

  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/python-app.yml">
    <img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/python-app.yml/badge.svg" alt="CI Build Status">
  </a>
  <a href="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation">
    <img src="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation/branch/main/graph/badge.svg" alt="Code Coverage">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/Python-3.10+-brightgreen.svg?logo=python&logoColor=white" alt="Python Version">
  </a>

  <br>

  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
  </a>
  <a href="docs/User_Manual.md">
    <img src="https://img.shields.io/badge/Docs-User_Manual-blue.svg?logo=markdown" alt="Documentation">
  </a>
  <a href="CONTRIBUTING.md">
    <img src="https://img.shields.io/badge/Contributing-Guidelines-orange.svg" alt="Contributing Guidelines">
  </a>
  <a href="CODE_OF_CONDUCT.md">
    <img src="https://img.shields.io/badge/Code%20of%20Conduct-Contributor%20Covenant-ff69b4.svg" alt="Code of Conduct">
  </a>

  <br>
  <img src="https://img.shields.io/badge/Inception-June%202022-orange?style=flat-square&logo=calendar" alt="Inception Date">
  <img src="https://img.shields.io/badge/Project%20Age-3%2B%20Years-blueviolet?style=flat-square" alt="Project Age">
  
  <img src="https://img.shields.io/github/downloads/prathameshnium/PICA-Python-Instrument-Control-and-Automation/total?style=flat-square&color=blue" alt="Total Downloads">
  <img src="https://img.shields.io/github/forks/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&logo=github" alt="Forks">
  
  <img src="https://img.shields.io/github/commit-activity/y/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=success" alt="Commits (Yearly)">
  <img src="https://img.shields.io/github/languages/code-size/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=blue" alt="Code Size">
  <img src="https://img.shields.io/github/last-commit/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=critical" alt="Last Commit">

</div>

---

## Overview

PICA (Python-based Instrument Control and Automation) is a modular, open-source software suite specifically designed to automate complex characterisation experiments and provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research.

Developed to operate as a custom laboratory-built measurement system, PICA provides a unifying graphical user interface (GUI) for orchestrating high-precision instruments, specifically Keithley SourceMeters/Nanovoltmeters, Lakeshore Temperature Controllers, and Keysight LCR Meters. The suite regulates the cryogenic environment to perform automated protocols such as temperature-dependent resistivity, current-voltage (I-V) characteristics, and pyroelectric current measurements.

The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterisation experiments. Built to streamline data acquisition and enhance experimental reproducibility, PICA leverages Python's `multiprocessing` library to ensure high stability by isolating each measurement process.

## Table of Contents

- [Overview](#overview)
- [What's the Need for PICA](#whats-the-need-for-pica)
- [Architecture](#architecture)
- [Core Features](#core-features)
- [Instrument Support](#instrument-support)
- [Instrument Specifications](#instrument-specifications)
- [Getting Started](#getting-started)
- [Running the Software](#running-the-software)
- [Running Tests](#running-tests)
- [Project History & Evolution](#project-history--evolution)
- [Resources & Documentation](#resources--documentation)
- [Citation](#citation)
- [Authors & Acknowledgments](#authors--acknowledgments)
- [License](#license)

## What's the Need for PICA

PICA tries to fill a clear gap for an open-source, laboratory-ready framework that provides well-tested measurement protocols together with an intuitive user interface, enabling experimentalists to perform sophisticated measurements without directly interacting with the source code. At the same time, implementing it in open-source Python would preserve the ability for advanced users to modify virtually any component of the system and contribute enhancements back to the project. Such a framework would foster a more open and collaborative scientific ecosystem, facilitating reproducibility, extensibility, and community-driven development in experimental physics research.

## Architecture

The core design philosophy of PICA is the separation of concerns, implemented through a distinct **GUI-Backend** architecture for each measurement module.

- **GUI (Frontend):** Each measurement has a dedicated GUI script (e.g., `IV_K6221_DC_Sweep_GUI.py`) built with `tkinter`. It is responsible for user interaction, parameter input, and real-time data visualisation using `matplotlib`.
- **Backend:** The instrument control logic is encapsulated in separate classes. This layer handles all `pyvisa` communication, SCPI command parsing, and data retrieval.
- **Process Isolation:** When a measurement starts, the GUI launches the backend logic in a separate, isolated process. This prevents a hardware timeout or script error from crashing the entire application suite.
- **Inter-Process Communication:** The frontend and backend communicate via thread-safe `multiprocessing.Queue`, allowing for high-speed data transfer without race conditions.

---

## Core Features

* **Accessibility:** PICA provides a professional dashboard that enables researchers without programming experience to configure and execute complex measurements.
* **Physical Validation:** PICA protocols are routinely employed for cryogenic transport measurements in the temperature range of 80–320 K at the UGC–DAE Consortium for Scientific Research, Mumbai Centre. Particular emphasis is placed on ensuring that the protocols are physically valid and that any artefacts arising from instrument output start‑up transients, synchronisation errors, or other physical anomalies are identified and eliminated.
* **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
* **CLI Mode:** A new command-line interface for headless operation (e.g., via SSH or Raspberry Pi).
* **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability.
* **Integrated VISA Instrument Scanner:** An embedded utility for discovering and troubleshooting connections.
* **Operational Transparency:** Unlike black-box solutions, PICA exposes real-time logs that facilitate debugging in the event of errors or anomalies, thereby enhancing scientific reproducibility.
* **Automated Testing:** Integrated CI/CD pipelines for logic verification.

---

## Instrument Support

These are the instruments currently supported by PICA. We are working to integrate additional devices and to extend the range of measurement protocols available for the existing instruments.

## Instrument Specifications

### Advanced Cryogenic Transport Measurement System

This software controls a facility designed for characterising the full spectrum of electronic transport properties in cryogenic environments (80 K to 320 K). The setup integrates multiple high-precision instruments to cover a resistance range spanning 24 orders of magnitude.

| Module | Configuration / Instrument | Use Case | Resistance Range |
| :--- | :--- | :--- | :--- |
| **1. Low-Resistance (Delta Mode)** | **Keithley 6221** (Current Source) + **K2182** (Nanovoltmeter) | Superconductors & metallic films; actively cancels thermal EMFs. | $10\,\text{n}\Omega$ to $100\,\text{M}\Omega$ |
| **2. Mid-Resistance (Standard)** | **Keithley 2400** SourceMeter | Semiconductors, oxides, general transport. | $100\,\mu\Omega$ to $200\,\text{M}\Omega$ |
| **3. Mid-Resistance (High-Precision)** | **Keithley 2400** + **K2182** | Detecting subtle phase transitions. | $1\,\mu\Omega$ to $100\,\text{M}\Omega$ |
| **4. High-Resistance** | **Keithley 6517B** Electrometer | Dielectrics, polymers, & ceramics. | $1\,\Omega$ to $10^{16}\,\Omega$ |

---

## Getting Started

PICA is structured as a standard Python package. Follow these steps to install it in editable mode, which allows you to modify code and see changes immediately.

1.  **Clone the Repository**
    ```bash
    git clone [https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git)
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create a Virtual Environment**
    ```bash
    # Create the virtual environment
    python -m venv venv
    venv\Scripts\activate

    # macOS/Linux
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install the Package**
    Use the `-e` (editable) flag. This installs PICA and all its dependencies (PyVISA, Pandas, etc.) linked to your current folder.
    ```bash
    pip install -e .
    ```

## Running the Software

You can now run PICA in two modes: the standard Graphical User Interface (GUI) or the older Command Line Interface (CLI) for headless operation.

I strongly recommend using the graphical user interface (GUI) version, as it represents the finalized protocols and provides laboratory-ready applications. By contrast, the command-line interface (CLI) tools correspond to earlier prototype scripts that were used during protocol development prior to completion of the full-stack program. Consequently, the CLI tools are outdated and no longer actively maintained. They are included here primarily for the sake of completeness and may still be useful for users who wish to learn about the underlying interfacing mechanisms.

In the future, I also plan to develop executable (`.exe`) versions in order to further simplify setup and facilitate rapid adoption.

1.  **Graphical Launcher (GUI)**
    The standard dashboard for desktop users.
    ```bash
    python run_pica.py
    ```

2.  **Command Line Interface (CLI)**
    In v.17.0: A text-based menu for running measurements via SSH, on Raspberry Pis, or in automated environments without a monitor.
    ```bash
    python pica_cli.py
    ```

---

## Running Tests

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

---

## Project History & Evolution

PICA has grown from offline utility scripts into a modular, automated, asynchronous control suite, moving from manual instrument handling to fully automated operation.

> **Project Lore:** For a full chronological development log, including offline prototyping and detailed version history, see [`CHANGELOG.md`](CHANGELOG.md).

### v17.0: Naming & Folder Standardization
*2025-12-02 (Current)*

- **Directory Structure:** Refactored into a professional layout with organized subdirectories.
- **Versioning:** Standardized names and adopted Semantic Versioning (v17.0.0).

**Research & Documentation**
- **Paper Draft:** First research paper draft completed and presented to Dr. Sudip Mukherjee.
- **Feedback:** Received key feedback on integrating ATMS (Advanced Transport Measurement Systems).

**[Community] - 2025-12-01**
- **Launch:** PICA announced on Hacker News.

### v15.0: JOSS Submission & Professionalization
*Released November 2025*

- **CI/CD:** Added automated tests via GitHub Actions.
- **Refactoring:** Reorganized code to meet JOSS standards.
- **Validation:** Ongoing physical validation of hardware timing.

### v13.0 – v14.1 (2025): Architecture Modernization
*Major Release*

- **Architecture:** Introduced GUI–backend isolation.
- **Multiprocessing:** Used `multiprocessing` to separate UI from instrument control loops.
- **UI:** Standardized dark theme across modules.
- **New Modes:** Added Passive Sensing for R–T and integrated plotting tools.

### 2022 – 2024: Inception & Prototyping

- **2024 (Migration):** Moved from offline lab systems to GitHub; organized scripts into instrument modules (Keithley/Lakeshore).
- **2022 (Origins):** Started in an air-gapped lab with `pyvisa` scripts replacing manual logging.
  - *Concept:* Proposed by Dr. Sudip Mukherjee to automate characterization workflows.
  - *Prototypes:* Built alongside hardware upgrades and cryogenic probe work at UGC-DAE CSR.

---

## Resources & Documentation

* **User Manual:** Detailed setup and troubleshooting guides are available in [docs/User_Manual.md](docs/User_Manual.md).
* **Instrument Manuals:** Original PDF manuals for the supported hardware are located in `assets/Manuals/`.

---

## Citation

If you use this software in your research, please cite it using the following BibTeX entry:

```bibtex
@software{Deshmukh_PICA_2025,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  month        = dec,
  year         = 2025,
  publisher    = {GitHub},
  version      = {17.0},
  url          = {[https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation)}
}
````

Alternatively, refer to the `CITATION.cff` file in the root directory.

-----

## Authors & Acknowledgments

\<p align="center"\>
\<img src="pica/assets/LOGO/UGC\_DAE\_CSR\_NBG.jpeg" alt="UGC DAE CSR Logo" width="150"\>
\</p\>

  - **Lead Developer:** **[Prathamesh Deshmukh](https://prathameshdeshmukh.site/)**
  - **Principal Investigator:** **[Dr. Sudip Mukherjee](https://www.google.com/search?q=https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)**
  - **Affiliation:** *[UGC-DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre)*

**Funding:**
Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.

-----

## License

This project is licensed under the MIT License - see the [LICENSE](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/LICENSE) file for details.

```
```
