# PICA: Python-based Instrument Control and Automation

<p align="center">
  <img src="pica/assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="250">
</p>


**A modular, open-source framework for automating laboratory measurements in physics research.**

<p align="center">
  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/ci.yml"><img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/ci.yml/badge.svg" alt="CI Build Status"></a>
  <a href="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation"><img src="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation/branch/main/graph/badge.svg" alt="Code Coverage"></a>
  <img src="https://img.shields.io/badge/Python-3.10+-brightgreen.svg?logo=python&logoColor=white&style=flat-square" alt="Python Version">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="License: MIT"></a>
  <br />
  <a href="docs/User_Manual.md"><img src="https://img.shields.io/badge/Docs-User_Manual-blue.svg?logo=markdown&style=flat-square" alt="Documentation"></a>
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/Contributing-Guidelines-orange.svg?style=flat-square" alt="Contributing Guidelines"></a>
  <a href="CODE_OF_CONDUCT.md"><img src="https://img.shields.io/badge/Code%20of%20Conduct-Contributor%20Covenant-ff69b4.svg?style=flat-square" alt="Code of Conduct"></a>
  <br />
  <img src="https://img.shields.io/badge/Inception-June%202022-orange?style=flat-square&logo=calendar" alt="Inception Date">
  <img src="https://img.shields.io/badge/Project%20Age-3%2B%20Years-blueviolet?style=flat-square" alt="Project Age">
  <img src="https://img.shields.io/github/downloads/prathameshnium/PICA-Python-Instrument-Control-and-Automation/total?style=flat-square&color=blue" alt="Total Downloads">
  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/stargazers"><img src="https://img.shields.io/github/stars/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&logo=github" alt="Stars"></a>
  <img src="https://img.shields.io/github/forks/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&logo=github" alt="Forks">
  <img src="https://img.shields.io/github/commit-activity/y/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=success" alt="Commits (Yearly)">
  <img src="https://img.shields.io/github/languages/code-size/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=blue" alt="Code Size">
  <img src="https://img.shields.io/github/last-commit/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=critical" alt="Last Commit">
</p>

---

## Overview

PICA (Python-based Instrument Control and Automation) is a modular software suite designed to democratize access to advanced experimental automation. It replaces expensive, rigid proprietary software with a flexible, open-source framework capable of orchestrating diverse laboratory instruments.

While originally developed for cryogenic transport characterization, PICA's architecture is highly **versatile**, providing a centralized dashboard - the **PICA Launcher** - that allows researchers in any facility to configure and execute complex measurement sequences without writing code.

<p align="center">
  <img src="pica/assets/Images/screenshots/00_PICA_Launcher.png" alt="PICA Launcher" width="800">
</p>

> **More details here:**
> For more information, go through the [User Manual](docs/User_Manual.md) and the [Quick Interfacing Guide](docs/python_instrument_interfacing.md).

## Table of Contents

- [Overview](#overview)
- [Why PICA?](#why-pica)
- [Architecture & Design](#architecture--design)
- [Supported Hardware Modules](#supported-hardware-modules)
- [Getting Started](#getting-started)
- [Running the Software](#running-the-software)
- [Running Tests](#running-tests)
- [Project History](#project-history)
- [Resources & Documentation](#resources--documentation)
- [Citation](#citation)
- [Authors & Acknowledgments](#authors--acknowledgments)
- [License](#license)

## Why PICA?

* **Open & Adaptable:** Unlike "black box" commercial tools, PICA is fully transparent. Researchers can inspect, modify, and extend the underlying Python code to suit unique experimental needs.
* **Unified Workflow:** It unifies control for instruments from different vendors (Keithley, Lake Shore, Keysight) into a single, cohesive interface.
* **Data Integrity:** Automated protocols ensure reproducibility, reducing human error associated with manual data logging.

## Architecture & Design

PICA is built on a philosophy of **robustness, modularity, and accessibility**.

* **Tech Stack:** Python 3.9+ serves as the core, utilizing `Tkinter` for GUIs, `PyVISA` for instrument communication, `Matplotlib` for real-time visualization, and `Multiprocessing` for concurrency.
* **Operational Transparency:** Every GUI includes an **Embedded Console Log** that streams real-time SCPI commands and status updates, allowing researchers to verify exactly what the hardware is doing.
* **Process Isolation:** Each measurement runs in a discrete process. If a specific instrument driver crashes or times out, the main launcher remains stable.

## Supported Hardware Modules

PICA includes built-in support for the following instrument configurations, covering a resistance range spanning 24 orders of magnitude.

| Module | Configuration / Instrument | Use Case | Range |
| :--- | :--- | :--- | :--- |
| **Low-Resistance (Delta)** | **Keithley 6221** + **K2182** | Superconductors & metallic films; cancels thermal EMFs via AC Delta method. | 10 nΩ - 100 MΩ |
| **Mid-Resistance (Standard)** | **Keithley 2400** SourceMeter | Semiconductors, oxides, general transport. | 100 µΩ - 200 MΩ |
| **Mid-Resistance (High-Precision)** | **Keithley 2400** + **K2182** | Detecting subtle phase transitions. | 1 µΩ - 100 MΩ |
| **High-Resistance** | **Keithley 6517B** Electrometer | Dielectrics, polymers, & ceramics. | 1 Ω - 10 PΩ |
| **Dielectric** | **Keysight E4980A** | C-V Analysis. | 20 Hz - 2 MHz |
| **Pyroelectric** | **K6517B** + **Temp Controller** | Current vs Temp (detecting Curie temperature). | pA - nA range |

## Module Previews

Here are a few examples of the measurement modules available in PICA:

<p align="center">
  <img src="pica/assets/Images/screenshots/K6221_RT_Control.png" alt="K6221 RT Control" width="600">
  <br>
  <em>K6221 RT Control</em>
</p>
<p align="center">
  <img src="pica/assets/Images/screenshots/K6517B_IV.png" alt="K6517B IV" width="600">
  <br>
  <em>K6517B IV</em>
</p>

---
## Getting Started

PICA is structured as a standard Python package. 

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create Virtual Environment & Install**
    ```bash
    python -m venv venv
    # Activate venv (Windows: venv\Scripts\activate, Linux/Mac: source venv/bin/activate)
    pip install .
    ```

    *Note: Ensure you have the NI-VISA drivers installed on your host machine to allow `PyVISA` to communicate with the hardware.*

## Running the Software

1.  **Graphical Launcher (Recommended)**
    The central dashboard for accessing all modules, the plotter, and the scanner.
    ```bash
    pica-gui
    ```

2.  **Command Line Interface (CLI)**
    For headless operation (e.g., Raspberry Pi).
    ```bash
    pica-cli
    ```

---
## Running Tests

To run the test suite locally:
```bash
pip install pytest pytest-cov flake8
python -m pytest
```

---

## Project History

PICA evolved from simple offline scripts in 2022 to a full-stack automated suite.

  * **v17.0 (Current):** Professional directory restructuring, Semantic Versioning, and documentation overhaul.
  * **v15.0:** JOSS submission preparation, CI/CD integration.
  * **v13.0:** Transition to Multiprocessing and standardized GUI themes.

### 2022 - 2023: Inception & Prototyping

  - **2023 (Migration):** Moved from offline lab systems to GitHub; organized scripts into instrument modules (Keithley/Lake Shore).
  - **2022 (Origins):** Started in an air-gapped lab with `pyvisa` scripts replacing manual logging.
      - *Concept:* Proposed by Dr. Sudip Mukherjee to automate characterization workflows.
      - *Prototypes:* Built alongside hardware upgrades and cryogenic probe work at UGC-DAE CSR.

> **Background:** For a detailed chronological log, see [`CHANGELOG.md`](CHANGELOG.md).

## Resources & Documentation

  * **User Manual:** Detailed physics and usage guides are available in the [User Manual](docs/User_Manual.md).
  * **Instrument Manuals:** A list of instrument manuals is available in [docs/Instruments_Manuals_Lists.md](docs/Instruments_Manuals_Lists.md).

---

## Citation

If you use this software in your research, please cite it:

```bibtex
@software{Deshmukh_PICA_2025,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  month        = dec,
  year         = 2025,
  publisher    = {GitHub},
  version      = {17.0},
  url          = {https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation}
}
```

## Authors & Acknowledgments

This project is led by [**Prathamesh K. Deshmukh**](https://prathameshdeshmukh.site/) under the supervision of [**Dr. Sudip Mukherjee**](https://www.csr.res.in/Sudip-Mukherjee) at the [*UGC-DAE Consortium for Scientific Research, Mumbai Centre*](https://www.csr.res.in/Mumbai_Centre).

Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF).

<p align="center">
  <img src="pica/assets/LOGO/UGC_DAE_CSR_NBG.jpeg" alt="UGC DAE CSR Logo" width="150">
</p>

## License

This project is licensed under the MIT License - see the [LICENSE file](LICENSE) for details.
