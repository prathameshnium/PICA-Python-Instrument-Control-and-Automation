# PICA: Python-based Instrument Control and Automation

![PICA Logo](pica/assets/LOGO/PICA_LOGO_NBG.png)

**A modular, open-source framework for automating laboratory measurements in physics research.**

[![CI Build Status](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/python-app.yml/badge.svg)](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/python-app.yml)
[![Code Coverage](https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation/branch/main/graph/badge.svg)](https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation)
![Python Version](https://img.shields.io/badge/Python-3.10+-brightgreen.svg?logo=python&logoColor=white)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/Docs-User_Manual-blue.svg?logo=markdown)](docs/User_Manual.md)
[![Contributing Guidelines](https://img.shields.io/badge/Contributing-Guidelines-orange.svg)](CONTRIBUTING.md)
[![Code of Conduct](https://img.shields.io/badge/Code%20of%20Conduct-Contributor%20Covenant-ff69b4.svg)](CODE_OF_CONDUCT.md)

![Inception Date](https://img.shields.io/badge/Inception-June%202022-orange?style=flat-square&logo=calendar)
![Project Age](https://img.shields.io/badge/Project%20Age-3%2B%20Years-blueviolet?style=flat-square)
![Total Downloads](https://img.shields.io/github/downloads/prathameshnium/PICA-Python-Instrument-Control-and-Automation/total?style=flat-square&color=blue)
![Forks](https://img.shields.io/github/forks/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&logo=github)
![Commits (Yearly)](https://img.shields.io/github/commit-activity/y/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=success)
![Code Size](https://img.shields.io/github/languages/code-size/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=blue)
![Last Commit](https://img.shields.io/github/last-commit/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=critical)

---

## Overview

PICA (Python-based Instrument Control and Automation) is a modular software suite designed to democratize access to advanced experimental automation. It replaces expensive, rigid proprietary software with a flexible, open-source framework capable of orchestrating diverse laboratory instruments.

While originally developed for cryogenic transport characterization, PICA's architecture is **hardware-agnostic**. It provides a centralized dashboard - the **PICA Launcher** - that allows researchers in any facility to configure and execute complex measurement sequences without writing code.

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

| Module | Configuration / Instrument | Use Case | Resistance Range |
| :--- | :--- | :--- | :--- |
| **Low-Resistance (Delta)** | **Keithley 6221** + **K2182** | Superconductors & metallic films; cancels thermal EMFs via AC Delta method. | 10 nOhm - 100 MOhm |
| **Mid-Resistance (Standard)** | **Keithley 2400** SourceMeter | Semiconductors, oxides, general transport. | 100 uOhm - 200 MOhm |
| **Mid-Resistance (High-Precision)** | **Keithley 2400** + **K2182** | Detecting subtle phase transitions. | 1 uOhm - 100 MOhm |
| **High-Resistance** | **Keithley 6517B** Electrometer | Dielectrics, polymers, & ceramics. | 1 Ohm - 10 POhm |
| **Dielectric** | **Keysight E4980A** | C-V Analysis and Spectroscopy. | 20 Hz - 2 MHz |
| **Pyroelectric** | **K6517B** + **Temp Controller** | Current vs Temp (detecting Curie temperature). | pA - nA range |

---

## Getting Started

PICA is structured as a standard Python package. 

1.  **Clone the Repository**
    ```bash
    git clone [https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git)
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create Virtual Environment & Install**
    ```bash
    python -m venv venv
    # Activate venv (Windows: venv\Scripts\activate, Linux/Mac: source venv/bin/activate)
    pip install -r requirements.txt
    ```

    *Note: Ensure you have the NI-VISA drivers installed on your host machine to allow `PyVISA` to communicate with the hardware.*

## Running the Software

1.  **Graphical Launcher (Recommended)**
    The central dashboard for accessing all modules, the plotter, and the scanner.
    ```bash
    python run_pica.py
    ```

2.  **Command Line Interface (CLI)**
    Legacy support for headless operation (e.g., Raspberry Pi).
    ```bash
    python pica_cli.py
    ```

---

## Running Tests

To run the test suite locally:
```bash
pip install pytest pytest-cov flake8
python -m pytest
````

-----

## Project History

PICA evolved from simple offline scripts in 2022 to a full-stack automated suite.

  * **v17.0.0 (Current):** Professional directory restructuring, Semantic Versioning, and documentation overhaul.
  * **v15.0:** JOSS submission preparation, CI/CD integration.
  * **v13.0:** Transition to Multiprocessing and standardized GUI themes.

### 2022 - 2023: Inception & Prototyping

  - **2023 (Migration):** Moved from offline lab systems to GitHub; organized scripts into instrument modules (Keithley/Lake Shore).
  - **2022 (Origins):** Started in an air-gapped lab with `pyvisa` scripts replacing manual logging.
      - *Concept:* Proposed by Dr. Sudip Mukherjee to automate characterization workflows.
      - *Prototypes:* Built alongside hardware upgrades and cryogenic probe work at UGC-DAE CSR.

> **Lore:** For a detailed chronological log, see [`CHANGELOG.md`](CHANGELOG.md).

## Resources & Documentation

  * **User Manual:** Detailed physics and usage guides are available in [docs/User\_Manual.md](User_Manual.md).
  * **Instrument Manuals:** Original PDF manuals are located in `assets/Manuals/`.

-----

## Citation

If you use this software in your research, please cite it:

```bibtex
@software{Deshmukh_PICA_2025,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  month        = dec,
  year         = 2025,
  publisher    = {GitHub},
  version      = {17.0.0},
  url          = {[https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation)}
}
```

## Authors & Acknowledgments

  - **Lead Developer:** [**Prathamesh Deshmukh**](https://prathameshdeshmukh.site/)
  - **Principal Investigator:** [**Dr. Sudip Mukherjee**](https://www.google.com/search?q=https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)
  - **Affiliation:** [*UGC-DAE Consortium for Scientific Research, Mumbai Centre*](https://www.csr.res.in/Mumbai_Centre)

**Funding:**
Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

````
