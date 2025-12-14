# PICA: Advanced Python Suite for High Precision Instrumentation and Transport Measurement Automation

<p align="center">
  <img src="pica/assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="250">
</p>

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

## Summary

High-precision measurements are essential for advancing research in spintronics and materials characterization. **PICA (Python-based Instrument Control and Automation)** is a modular, open-source software suite designed to automate advanced transport measurements for electronic devices and chemical samples. It operates as a versatile framework capable of running on any standard laboratory workstation.

PICA provides an extensible, unified graphical user interface (GUI) for orchestrating high-precision instruments, specifically current source (DC/AC) units, nanovoltmeters, high resistance electrometers, impedance analyzers, and temperature controllers. Built on the robust Python scientific ecosystem, PICA ensures that the entire hardware ecosystem functions seamlessly as a cohesive unit.

The suite performs automated protocols including:
* Temperature-dependent wide-range resistance measurement ($10^{-8}$ - $10^{16}$ Ω).
* Current-voltage (I-V) characterization.
* Capacitance characterization and magnetodielectric studies (20 Hz - 2 MHz).
* Pyroelectric current measurement (resolution $10^{-15}$ A).

<p align="center">
  <img src="pica/assets/Images/screenshots/00_PICA_Launcher.png" alt="PICA Launcher" width="800">
  <br>
  <em>PICA Launcher Interface for accessing all measurement modules.</em>
</p>

> **More details here:**
> For more information, go through the [User Manual](docs/User_Manual.md).

## Table of Contents

- [Summary](#summary)
- [Statement of Need](#statement-of-need)
- [Key Features](#key-features)
- [Design and Implementation](#design-and-implementation)
- [Supported Hardware Modules](#supported-hardware-modules)
- [Getting Started](#getting-started)
- [Running the Software](#running-the-software)
- [Resources & Documentation](#resources--documentation)
- [Citation](#citation)
- [Authors & Acknowledgments](#authors--acknowledgments)
- [License](#license)

---

## Statement of Need

Advancements in experimental physics and device manufacturing depend on the precise characterization of material properties under extreme physical conditions. Researchers often face a binary choice: purchase expensive proprietary software or develop custom measurement scripts from scratch.

While libraries such as **PyVISA** and **PyMeasure** provide foundational drivers, requiring users to write and maintain low-level code, **PICA builds upon these powerful libraries** to offer a turnkey application. It provides a ready-to-run graphical interface that abstracts the underlying control logic, allowing experimentalists to focus on data acquisition without extensive software development overhead.

PICA enables continuous operation across the full range from Delta-mode low-resistance measurements (removing constant offsets) to high-impedance electrometric measurements using a single unified framework.

## Key Features

* **Accessibility:** A professional GUI dashboard allows researchers without coding experience to configure and run complex measurement protocols immediately using pre-packaged measurement modules.
* **Operational Validation:** Validated via cryogenic transport measurements using a custom-designed probe in conjunction with a **Physical Property Measurement System (PPMS)** (5-380 K, up to 14 Tesla) at the UGC DAE Consortium for Scientific Research, Mumbai Centre.
* **Fault Tolerance:** Control logic is isolated from the user interface. Hardware timeouts or driver crashes are prevented from freezing the main dashboard.
* **Modular CLI Architecture:** Measurement modules contain CLI counterparts, allowing researchers to utilize PICA's protocol logic for headless automation or integration into other workflows without GUI overhead.
* **Operational Transparency:** PICA rejects the "black box" paradigm by exposing real-time, time-stamped command logs (e.g., `[10:05:25] Keithley 6221: Ramping current to 10 mA`). This aids debugging, ensures scientific reproducibility, and allows verification of measurement protocols.
* **Open Source Extensibility:** Researchers can integrate new instrument drivers or experimental protocols by subclassing existing templates, fostering a community-driven ecosystem.

---

## Design and Implementation

PICA is built on a modular architecture characterized by self-contained modules, ensuring future extensibility without impacting core system stability.

### Process Isolation and Concurrency
Unlike simple script-based automation, PICA decouples the User Interface (UI) from the instrumentation control logic using Python's standard `multiprocessing` libraries.
* **Stability:** If an instrument hangs, the isolated process can be terminated safely without freezing the main GUI or losing previous data.
* **Responsiveness:** The `tkinter`-based frontend remains responsive for live data plotting (using `matplotlib` with blitting) even while the backend waits for hardware triggers.
* **Data Integrity:** A "write on acquisition" strategy using `pandas` saves data to CSV immediately after every acquisition point, preventing data loss during power failures.

### Hardware Abstraction Layer
PICA utilizes **PyVISA** to abstract low-level communication protocols (GPIB, USB, Ethernet). The software implements a strict initialization routine:
1.  **Connection Verification:** A built-in "VISA Instrument Scanner" queries the bus (`*IDN?`) to map instrument addresses.
2.  **Instrument Reset Protocol:** Explicitly resets all stored data and buffers to provide a clean initial state.
3.  **Graceful Shutdown:** Ensures sources are ramped down and heaters disabled safely, even if the software is interrupted.

### Testing and Simulation
PICA includes a testing suite using `pytest` and `unittest.mock` to simulate VISA resources, allowing verification of backend logic streams and command sequences without constant access to physical instruments.

---

## Supported Hardware Modules

The system is currently validated with industry-standard hardware, covering a resistance range spanning 24 orders of magnitude.

| Module | Configuration / Instrument | Use Case | Range |
| :--- | :--- | :--- | :--- |
| **Low-Resistance (Delta)** | **Keithley 6221** + **K2182** | Superconductors & metallic films; cancels thermal EMFs via AC Delta method. | 10 nΩ - 100 MΩ |
| **Mid-Resistance (Standard)** | **Keithley 2400** SourceMeter | Semiconductors, oxides, general transport. | 100 µΩ - 200 MΩ |
| **Mid-Resistance (High-Precision)** | **Keithley 2400** + **K2182** | Detecting subtle phase transitions. | 1 µΩ - 100 MΩ |
| **High-Resistance** | **Keithley 6517B** Electrometer | Dielectrics, polymers, & ceramics. | 1 Ω - 10 PΩ |
| **Dielectric Analysis** | **Keysight E4980A** | C-V Analysis and Magnetodielectric characterization. | 20 Hz - 2 MHz |
| **Pyroelectric** | **K6517B** + **Temp Controller** | Current vs Temp (detecting Curie temperature). | $10^{-15}$ A Resolution |

*While the current implementation drives specific instruments, the underlying framework is highly customizable. Researchers need only replace specific SCPI commands to utilize the suite with different models.*

### Module Previews

<p align="center">
  <img src="pica/assets/Images/screenshots/K6221_RT_Control.png" alt="K6221 RT Control" width="600">
  <br>
  <em>Automated R-T measurement interface using the K6221/2182 for low-resistance samples, employing the Delta Mode to cancel thermal EMFs.</em>
</p>
<p align="center">
  <img src="pica/assets/Images/screenshots/K6517B_IV.png" alt="K6517B IV" width="600">
  <br>
  <em>Interface for current-voltage (I-V) characterization of high-impedance materials using the Keithley 6517B Electrometer.</em>
</p>

---

## Pre-requisites: The VISA Driver

> [!WARNING]
> **A VISA Backend is Required:** `PyVISA` is a Python wrapper, not a driver. For PICA to communicate with hardware, you **must** install a VISA backend on your system first. If you attempt to run the software on a clean machine without a VISA implementation, it will fail to find the instruments. This is the most common failure point for new instrument control setups.
>
> Choose one of the following:
> - **NI-VISA:** The industry standard from National Instruments. Download and install it from the [NI website](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html#575764).
> - **PyVISA-py:** A backend written in pure Python that is installed automatically with PICA. It can be used as a fallback but may have limitations compared to vendor-specific drivers like NI-VISA.
>
> **Before proceeding, verify your VISA installation.**

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
    > [!NOTE]
    > **Legacy CLI Notice:** The PICA CLI (`pica-cli`) is retained to support legacy headless workflows. While fully functional for specific protocols, this interface is **no longer actively maintained** and may not support recent features available in the GUI. 
    >
    > We **strongly recommend** new users utilize the PICA GUI for the most complete and supported experience.

---
## Running Tests

To run the test suite locally, first install the development dependencies:
```bash
pip install -r requirements-dev.txt
```
Then, you can run the tests:
```bash
python -B -m pytest -p no:cacheprovider
```

---

## Project History

PICA evolved from simple offline scripts in 2022 to a full-stack automated suite.

  * **v1.0.0 (Initial Public Release):** Version numbering has been reset from legacy development builds (v17.0) to v1.0.0 to standardize the package for public distribution and citation.
  * **v17.0:** Professional directory restructuring, Semantic Versioning, and documentation overhaul.
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
  title        = {{PICA: Advanced Python Suite for High Precision Instrumentation and Transport Measurement Automation}},
  month        = dec,
  year         = 2025,
  publisher    = {GitHub},
  version      = {1.0.0},
  url          = {[https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation)}
}
````

## Authors & Acknowledgments

This project is led by [**Prathamesh K. Deshmukh**](https://prathameshdeshmukh.site/) under the supervision of [**Dr. Sudip Mukherjee**](https://www.csr.res.in/Sudip-Mukherjee) at the [*UGC-DAE Consortium for Scientific Research, Mumbai Centre*](https://www.csr.res.in/Mumbai_Centre).

We acknowledge the financial support provided under the **SERB-CRG project grant No. CRG/2022/005676** from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science and Technology (DST), Government of India.


<p align="center">
  <img src="pica/assets/LOGO/UGC_DAE_CSR_NBG.jpeg" alt="UGC DAE CSR Logo" width="150">
</p>

## License

This project is licensed under the MIT License - see the [LICENSE file](LICENSE) for details.
