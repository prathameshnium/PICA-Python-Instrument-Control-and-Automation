# PICA: Advanced High-Precision Transport Measurement Automation with Python

<p align="center">
  <img src="pica/assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="250">
</p>

<p align="center">
  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/test.yml"><img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/test.yml/badge.svg" alt="Automated Testing"></a>
  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/lint.yml"><img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/lint.yml/badge.svg" alt="Lint and Style Checks"></a>
  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/codeql.yml"><img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/codeql.yml/badge.svg" alt="CodeQL Analysis"></a>
  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/draft-pdf.yml"><img src="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/actions/workflows/draft-pdf.yml/badge.svg" alt="Draft PDF"></a>
  <a href="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation"><img src="https://codecov.io/gh/prathameshnium/PICA-Python-Instrument-Control-and-Automation/branch/main/graph/badge.svg" alt="Code Coverage"></a>
  <img src="https://img.shields.io/badge/Python-3.10+-brightgreen.svg?logo=python&logoColor=white&style=flat-square" alt="Python Version">
  <a href="https://doi.org/10.5281/zenodo.18377217"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.18377217.svg" alt="DOI"></a>
  <a href="https://joss.theoj.org/papers/444957f999023680f8abbbf0e75687fa"><img src="https://joss.theoj.org/papers/444957f999023680f8abbbf0e75687fa/status.svg"></a>
  <a href="https://pypi.org/project/pica-suite/"><img src="https://img.shields.io/pypi/v/pica-suite.svg?style=flat-square&label=PyPI&color=blue" alt="PyPI Version"></a>
  <a href="https://pypi.org/project/pica-suite/"><img src="https://img.shields.io/pypi/dm/pica-suite?style=flat-square&label=PyPI%20Downloads" alt="PyPI Downloads"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="License: MIT"></a>
  <br />
  <a href="https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/"><img src="https://img.shields.io/badge/Docs-Online%20Documentation-blue.svg?logo=readthedocs&style=flat-square" alt="Online Documentation"></a>
  <a href="docs/User_Manual.md"><img src="https://img.shields.io/badge/Docs-User_Manual-blue.svg?logo=markdown&style=flat-square" alt="Documentation"></a>
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/Contributing-Guidelines-orange.svg?style=flat-square" alt="Contributing Guidelines"></a>
  <a href="CODE_OF_CONDUCT.md"><img src="https://img.shields.io/badge/Code%20of%20Conduct-Contributor%20Covenant-ff69b4.svg?style=flat-square" alt="Code of Conduct"></a>
  <br />
  <img src="https://img.shields.io/badge/Project_Inception-June%202022-orange?style=flat-square&logo=calendar" alt="Project Inception Date">
  <img src="https://img.shields.io/badge/Project%20Age-3%2B%20Years-blueviolet?style=flat-square" alt="Project Age">
  <img src="https://img.shields.io/github/created-at/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square" alt="GitHub created-at">
  <img src="https://img.shields.io/github/downloads/prathameshnium/PICA-Python-Instrument-Control-and-Automation/total?style=flat-square&color=blue" alt="Total Downloads">
  <a href="https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/stargazers"><img src="https://img.shields.io/github/stars/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&logo=github" alt="Stars"></a>
  <img src="https://img.shields.io/github/forks/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&logo=github" alt="Forks">
  <img src="https://img.shields.io/github/languages/code-size/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=blue" alt="Code Size">
  <img src="https://img.shields.io/github/last-commit/prathameshnium/PICA-Python-Instrument-Control-and-Automation?style=flat-square&color=critical" alt="Last Commit">
</p>

---

> 🎉 **v1.0.3 is now live!** The GUI has been refreshed with a new **Latte** theme — lighter, warmer, and easier on the eyes during long measurement sessions.
> ```bash
> pip install --upgrade pica-suite
> ```

---

## Overview

High-precision, low-noise transport measurements are foundational to research in spintronics and materials characterisation. **PICA (Python-based Instrument Control and Automation)** is a modular, open-source software suite that automates advanced transport measurement workflows for electronic devices and material samples, running on any standard laboratory workstation.

PICA provides a unified, extensible graphical user interface (GUI) for orchestrating high-precision instruments — including DC/AC current sources, nanovoltmeters, high-resistance electrometers, impedance analysers, and temperature controllers — built on the Python scientific ecosystem.

Automated protocols include:

- Temperature-dependent wide-range resistance measurement ($10^{-8}$ – $10^{16}$ Ω)
- Current–voltage (I–V) characterisation
- Capacitance characterisation and magnetocapacitance studies (20 Hz – 2 MHz)
- Pyroelectric current measurement (resolution $10^{-15}$ A)

<p align="center">
  <img src="pica/assets/Images/screenshots/00_PICA_Launcher.png" alt="PICA Launcher" width="800">
  <br>
  <em>PICA Launcher (v1.0.3, Latte theme).</em>
</p>

> **Full documentation:** [https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/)
> **Usage guides:** [User Manual](docs/User_Manual.md)

## Table of Contents

- [Overview](#overview)
- [What's New in v1.0.3](#whats-new-in-v103)
- [Motivation](#motivation)
- [Key Features](#key-features)
- [Design and Implementation](#design-and-implementation)
- [Supported Hardware Modules](#supported-hardware-modules)
- [Demonstration (Screencast)](#demonstration-of-pica-screencast)
- [Getting Started](#getting-started)
- [Running the Software](#running-the-software)
- [Resources & Documentation](#resources--documentation)
- [Citation](#citation)
- [Authors & Funding](#authors--funding)
- [License](#license)

---

## What's New in v1.0.3

**v1.0.3** is available on [PyPI](https://pypi.org/project/pica-suite/) and [GitHub Releases](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases).

```bash
pip install pica-suite==1.0.3
# or upgrade from a previous version:
pip install --upgrade pica-suite
```

### 🎨 Latte GUI Theme

The GUI has been redesigned with a **Latte** theme — a lighter, warmer colour palette replacing the previous darker interface, improving readability under typical laboratory lighting.

Changes across all measurement modules:

- Lighter background and widget colours
- Improved contrast for console log text
- Softer colour scheme for real-time plot backgrounds

For the full changelog, see [`CHANGELOG.md`](CHANGELOG.md).

---

## Motivation

Precise characterisation of material properties under extreme physical conditions is central to experimental physics and device research. Researchers typically face a binary choice: purchase expensive proprietary software, or build and maintain custom measurement scripts from scratch.

Libraries such as [**PyVISA**](https://github.com/pyvisa/pyvisa) and [**PyMeasure**](https://github.com/pymeasure/pymeasure) provide solid low-level instrument drivers, but require users to write significant control logic themselves. **PICA builds on these libraries** to deliver a turnkey application — a ready-to-run graphical interface that abstracts control logic, letting experimentalists focus on data acquisition rather than software development.

PICA spans the full measurement chain: from ultra-low-resistance measurements with thermal EMF cancellation, through mid-range transport characterisation, to high-impedance electrometric, pyroelectric, and capacitance measurements — all within a single unified framework.

---

## Key Features

- **Accessible GUI:** A professional dashboard (Latte theme) allows researchers without programming experience to configure and execute complex measurement protocols using pre-packaged modules.
- **Experimentally Validated:** Validated via cryogenic transport measurements using a custom-designed probe with a **Physical Property Measurement System (PPMS)** (5–380 K, up to 14 T) at the [UGC-DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre).
- **Fault Tolerance:** Instrument control logic runs in isolated processes. Hardware timeouts or driver crashes cannot freeze the main dashboard or corrupt acquired data.
- **Modular CLI:** Every measurement module includes a CLI counterpart for headless automation and integration into external workflows.
- **Operational Transparency:** PICA rejects the "black box" paradigm. A real-time, time-stamped command log (e.g., `[10:05:25] Keithley 6221: Ramping current to 10 mA`) exposes all SCPI commands sent to instruments, aiding debugging and ensuring scientific reproducibility.
- **Open-Source & Extensible:** New instrument drivers or experimental protocols can be added by subclassing existing templates, fostering a community-driven ecosystem.

---

## Design and Implementation

PICA uses a modular architecture of self-contained measurement modules, designed for extensibility without impacting core system stability.

### Process Isolation and Concurrency

PICA decouples the UI from instrument control using Python's `multiprocessing` library:

- **Stability:** A hung instrument process can be terminated without affecting the GUI or previously acquired data.
- **Responsiveness:** The `tkinter` frontend stays responsive for live plotting (via `matplotlib` with blitting) even while the backend awaits hardware triggers.
- **Data Integrity:** A "write-on-acquisition" strategy using `pandas` saves data to CSV after every acquisition point, preventing loss during power failures.

### Hardware Abstraction Layer

[**PyVISA**](https://github.com/pyvisa/pyvisa) abstracts low-level communication (GPIB, USB, Ethernet). PICA enforces a strict initialisation routine:

1. **Connection Verification:** A built-in VISA Instrument Scanner queries the bus (`*IDN?`) to map instrument addresses.
2. **Instrument Reset:** All stored data and buffers are explicitly cleared for a clean initial state.
3. **Graceful Shutdown:** Sources are ramped down and heaters disabled safely, even if the software is interrupted mid-measurement.

### Testing and Simulation

The test suite uses `pytest` and `unittest.mock` to simulate VISA resources, enabling verification of backend logic and command sequences without physical hardware access.

---

## Supported Hardware Modules

The system is validated with industry-standard hardware, covering a resistance range spanning 24 orders of magnitude.

| Module | Instruments | Use Case | Range |
| :--- | :--- | :--- | :--- |
| **Ultra-Low Resistance** | Keithley 6221 + K2182 + Lakeshore 350/340 | Superconductors & metallic films; thermal EMF cancellation via AC Delta method | 10 nΩ – 100 MΩ |
| **Mid-Resistance (Standard)** | Keithley 2400 + Lakeshore 350/340 | Semiconductors, oxides, general transport | 100 µΩ – 200 MΩ |
| **Mid-Resistance (High-Precision)** | Keithley 2400 + K2182 + Lakeshore 350/340 | Subtle phase transition detection | 1 µΩ – 100 MΩ |
| **High-Resistance** | Keithley 6517B + Lakeshore 350/340 | Wide-bandgap materials, polymers, ceramics | 1 Ω – 10 PΩ |
| **Capacitance Analysis** | Keysight E4980A + Lakeshore 350/340 | C–V analysis and magnetocapacitance studies | 20 Hz – 2 MHz |
| **Pyroelectric** | K6517B + Lakeshore 350/340 | Current vs. temperature; Curie temperature detection | $10^{-15}$ A resolution |

> The underlying framework is instrument-agnostic. Adapting PICA to different hardware typically requires only replacing the relevant SCPI commands.

> [!NOTE]
> **Delta Mode:** This term refers specifically to the technique used by Keithley 6220/6221 current sources in conjunction with the 2182/2182A nanovoltmeter for very low resistance measurements, as described in the [Keithley Low Level Measurements Handbook](https://www.tek.com/en/documents/product-article/keithley-low-level-measurements-handbook---7th-edition). In this documentation, "Ultra-Low Resistance Measurements" is the general scientific term; "Delta Mode" appears when specifically referencing the Keithley method or associated program files.

### Module Previews

<p align="center">
  <img src="pica/assets/Images/screenshots/K6221_RT_Control.png" alt="K6221 RT Control" width="600">
  <br>
  <em>R–T measurement interface using the K6221/2182, employing the Ultra-Low Resistance Measurement technique to cancel thermal EMFs.</em>
</p>

<p align="center">
  <img src="pica/assets/Images/screenshots/K6517B_IV.png" alt="K6517B IV" width="600">
  <br>
  <em>I–V characterisation interface for high-impedance materials using the Keithley 6517B electrometer.</em>
</p>

---

## Demonstration of PICA (Screencast)

A screencast demonstrating the high-resistance IV module is available **[here](https://drive.google.com/file/d/13W-Z4N-08t9m0xxuR30sjTLmUVG1VyQd/view?usp=sharing)**.

---

## Pre-requisites: VISA Driver

> [!WARNING]
> **A VISA backend is required.** [`PyVISA`](https://github.com/pyvisa/pyvisa) is a Python wrapper, not a standalone driver. Without a VISA implementation installed on your system, PICA will not be able to communicate with instruments. This is the most common failure point for new setups.
>
> Choose one of the following:
> - **NI-VISA** (recommended): The industry-standard backend from National Instruments. Download from the [NI website](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html#575764).
> - **PyVISA-py**: A pure-Python fallback installed automatically with PICA. Functional but may have limitations vs. vendor drivers. See [PyVISA-py on GitHub](https://github.com/pyvisa/pyvisa-py).
>
> Verify your VISA installation before proceeding.

---

## Getting Started

PICA is available on [PyPI](https://pypi.org/project/pica-suite/) as a standard Python package. Windows is the supported platform.

### Option 1: Install from PyPI (Recommended)

```bash
pip install pica-suite
```

To upgrade to the latest version (v1.0.3):

```bash
pip install --upgrade pica-suite
```

### Option 2: Install from Source

```bash
git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
cd PICA-Python-Instrument-Control-and-Automation

python -m venv venv
venv\Scripts\activate

pip install .
```

To upgrade after pulling new changes:

```bash
pip install --upgrade .
```

To force a clean reinstall:

```bash
pip install --force-reinstall .
```

> Ensure NI-VISA drivers are installed on your host machine before running PICA.

---

## Running the Software

### 1. Graphical Launcher (Recommended)

```bash
pica-gui
```

The central dashboard provides access to all measurement modules, the plotter, and the VISA scanner. Individual modules can also be launched directly:

```bash
python pica/keithley/k6517b/High_Resistance/IV_K6517B_GUI.py
```

### 2. Command Line Interface

```bash
pica-cli
```

> [!IMPORTANT]
> **Template Scripts:** CLI modules are designed as **template scripts** for programmatic adaptation. Users are expected to modify them to suit their specific experimental requirements. These scripts are well-suited for building custom measurement sequences and for learning instrument automation. They are typically suffixed with `_Instrument_Control` to reflect their programmatic nature.

> [!NOTE]
> **Legacy CLI Notice:** The PICA CLI is retained for legacy headless workflows and remains functional for specific protocols, but is less actively maintained than the GUI. New users are strongly encouraged to use the GUI for the most complete and supported experience.

---

## System Requirements

| | |
|:---|:---|
| **Platform** | Windows 10 / 11 |
| **Architecture** | x86\_64 |
| **Python** | 3.10+ |

> [!IMPORTANT]
> PICA is validated exclusively on Windows. Linux and macOS are **not officially supported** due to dependencies on Windows-specific GUI libraries and font rendering. Attempting to run on non-Windows platforms may result in crashes or UI failures. Linux support is experimental.

---

## Example Usage

Upon launching (`pica-gui`), select a measurement module from the dashboard. Each module presents a two-panel interface:

**1. Configuration Panel (left)**
- Set `Sample Name`, `File Storage Location`, and `Instrument Address` (GPIB/VISA).
- Define protocol parameters: voltage/current limits, temperature step sizes, delay times.
- A scrollable console provides a continuous, time-stamped log of all operations and SCPI commands.

**2. Visualisation Panel (right)**
- Displays up to three simultaneous real-time plots (e.g., R vs. T, V vs. I) with support for logarithmic axis scaling.
- Quick-access buttons for the **VISA Instrument Scanner** and **PICA Plotter Utility** (for post-measurement analysis and comparison).

The interface is intentionally minimal to reduce interaction overhead during active high-precision measurements.

---

## Running Tests Locally

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the test suite:

```bash
python -B -m pytest -p no:cacheprovider
```

Check coverage:

```powershell
python -B -m pytest --cov=pica --cov-report=term-missing -p no:cacheprovider
```

---

## Experimental Linux Support

> [!WARNING]
> The following is for experimental purposes only. PICA is not officially supported on Linux and may exhibit functional or UI issues.

**Prerequisites:**

Install `tkinter` (often absent by default):

```bash
# Debian/Ubuntu
sudo apt-get install python3-tk
```

Activate the virtual environment with:

```bash
source venv/bin/activate
```

Then follow the standard [Getting Started](#getting-started) installation steps.

---

## Project History

Full release builds are available on the [releases page](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases).

PICA evolved from simple offline scripts in 2022 into a full-stack automated measurement suite:

| Version | Highlights |
|:---|:---|
| **v1.0.3** (latest) | GUI refreshed with Latte theme; available on PyPI |
| **v1.0.0 – v1.0.1** | Initial public release; version reset from v17.0 for standardised packaging and citation |
| **v17.0** | Professional directory restructuring, semantic versioning, documentation overhaul |
| **v15.0** | JOSS submission, CI/CD integration |
| **v13.0** | Transition to multiprocessing, standardised GUI themes |

### 2022–2023: Inception & Prototyping

- **2023:** Migrated from air-gapped lab systems to GitHub; scripts organised into instrument modules (Keithley / Lake Shore).
- **2022:** Originated from `pyvisa` scripts replacing manual data logging in an air-gapped lab environment.
  - *Concept* proposed by Dr. Sudip Mukherjee to automate characterisation workflows.
  - *Prototypes* developed alongside hardware upgrades and cryogenic probe work at UGC-DAE CSR.

For the full chronological log, see [`CHANGELOG.md`](CHANGELOG.md).

---

## Resources & Documentation

| Resource | Link |
|:---|:---|
| Online Documentation | [https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/) |
| PyPI Package | [https://pypi.org/project/pica-suite/](https://pypi.org/project/pica-suite/) |
| User Manual | [docs/User_Manual.md](docs/User_Manual.md) |
| Instrument Manuals List | [docs/Instruments_Manuals_Lists.md](docs/Instruments_Manuals_Lists.md) |
| Project Web Page | [https://prathameshdeshmukh.site/pages/project-pica.html](https://prathameshdeshmukh.site/pages/project-pica.html) |
| GitLab Backup | [gitlab.com/prathameshnium/pica-python-instrument-control-and-automation](https://gitlab.com/prathameshnium/pica-python-instrument-control-and-automation) |
| Preprint | [Publications page](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/) · [PDF](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/pica-paper.pdf) · [Academia.edu](https://www.academia.edu/165176821/PICA_Advanced_High_Precision_Transport_Measurement_Automation_with_Python) |

---

## Citation

If PICA contributes to your research, please cite:

```bibtex
@software{Deshmukh_PICA_2026,
  author    = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title     = {{PICA: Advanced High-Precision Transport Measurement Automation with Python}},
  month     = jan,
  day       = 26,
  year      = 2026,
  publisher = {Zenodo},
  version   = {1.0.3},
  doi       = {10.5281/zenodo.18377217},
  url       = {https://doi.org/10.5281/zenodo.18377217}
}
```

---

## Authors & Funding

Developed by [**Prathamesh Deshmukh**](https://www.researchgate.net/profile/Prathamesh-Deshmukh-6) under the supervision of [**Dr. Sudip Mukherjee**](https://www.csr.res.in/Faculty/profile/889/893/Dr.SudipMukherjee) at the UGC-DAE Consortium for Scientific Research, Mumbai Centre, Bhabha Atomic Research Centre, Mumbai 400 085, Maharashtra, India.

Financial support provided under **SERB-CRG project grant No. CRG/2022/005676** from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science and Technology (DST), Government of India.

<p align="center">
  <img src="pica/assets/LOGO/UGC_DAE_CSR_NBG.jpeg" alt="UGC DAE CSR Logo" width="150">
</p>

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
