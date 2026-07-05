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

> 🎉 **v1.0.5 is now live!** This release includes general maintenance and bug fixes. Install or upgrade via pip: `pip install --upgrade pica-suite`

---

## Overview

High-precision, low-noise transport measurements are essential for advancing research in spintronics and materials characterisation. **PICA (Python-based Instrument Control and Automation)** is a modular, open-source software suite designed to automate advanced transport measurements for electronic devices and material samples. It operates as a versatile framework capable of running on any standard laboratory workstation.

PICA provides an extensible, unified graphical user interface (GUI) for orchestrating high-precision instruments, specifically current source (DC/AC) units, nanovoltmeters, high resistance electrometers, impedance analyser, and temperature controllers. Built on the robust Python scientific ecosystem, PICA ensures that the entire hardware ecosystem functions seamlessly as a cohesive unit.

The suite performs automated protocols including:
* Temperature-dependent wide-range resistance measurement ($10^{-8}$ - $10^{16}$ Ω).
* Current-voltage (I-V) characterisation.
* Capacitance characterisation and magnetocapacitance studies (20 Hz - 2 MHz).
* Pyroelectric current measurement (resolution $10^{-15}$ A).

<p align="center">
  <img src="pica/assets/Images/screenshots/00_PICA_Launcher.png" alt="PICA Launcher" width="800">
  <br>
  <em>PICA Launcher Interface (v1.0.3, Latte theme) for accessing all measurement modules.</em>
</p>

> **More details here:**
> Full online documentation is available at [https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/).
> For usage guides, see the [User Manual](docs/User_Manual.md).

## Table of Contents

- [Overview](#overview)
- [What's New in v1.0.3](#whats-new-in-v103)
- [Motivation](#motivation)
- [Key Features](#key-features)
- [Design and Implementation](#design-and-implementation)
- [Supported Hardware Modules](#supported-hardware-modules)
- [Demonstration of PICA (Screencast)](#demonstration-of-pica-screencast)
- [Getting Started](#getting-started)
- [Running the Software](#running-the-software)
- [Resources & Documentation](#resources--documentation)
- [Citation](#citation)
- [Authors & Acknowledgments](#authors--acknowledgments)
- [License](#license)

---

## What's New in v1.0.5

**v1.0.5** is the latest release and is available on PyPI and GitHub Releases.

```bash
pip install pica-suite==1.0.5
# or upgrade from a previous version:
pip install --upgrade pica-suite
```

### 🎨 Latte GUI Theme
The GUI has been redesigned with a **Latte** theme — a lighter, warmer colour palette that replaces the previous darker interface. This makes PICA more comfortable for extended lab sessions and improves readability under typical laboratory lighting conditions.

Key visual changes:
* Lighter background and widget colours throughout all measurement modules.
* Improved contrast for console log text.
* Softer colour scheme for real-time plot backgrounds.

For the full changelog, see [`CHANGELOG.md`](CHANGELOG.md).

---

## Motivation

Advancements in experimental physics and device manufacturing depend on the precise characterisation of material properties under extreme physical conditions. Researchers often face a binary choice: purchase expensive proprietary software or develop custom measurement scripts from scratch.

While libraries such as [**PyVISA**](https://github.com/pyvisa/pyvisa) and [**PyMeasure**](https://github.com/pymeasure/pymeasure) provide foundational drivers, requiring users to write and maintain low-level code, **PICA builds upon these powerful libraries** to offer a turnkey application. It provides a ready-to-run graphical interface that abstracts the underlying control logic, allowing experimentalists to focus on data acquisition without extensive software development overhead.

PICA enables continuous operation across a full range of measurements, from ultra low resistance measurements (removing constant offsets) to high-impedance, electrometric, pyroelectric, and capacitance measurements, all within a single, unified framework.

## Key Features

* **Accessibility:** A professional GUI dashboard (now with the Latte theme) allows researchers without coding experience to configure and run complex measurement protocols immediately using pre-packaged measurement modules.
* **Operational Validation:** Validated via cryogenic transport measurements using a custom-designed probe in conjunction with a **Physical Property Measurement System (PPMS)** (5-380 K, up to 14 Tesla) at the [UGC DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre).
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
PICA utilizes [**PyVISA**](https://github.com/pyvisa/pyvisa) to abstract low-level communication protocols (GPIB, USB, Ethernet). The software implements a strict initialization routine:
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
| **Ultra Low Resistance Measurements** | **Keithley 6221** + **K2182** + Lakeshore 350/340 | Superconductors & metallic films; cancels thermal EMFs via AC Delta method. | 10 nΩ - 100 MΩ |
| **Mid-Resistance (Standard)** | **Keithley 2400** SourceMeter + Lakeshore 350/340 | Semiconductors, oxides, general transport. | 100 µΩ - 200 MΩ |
| **Mid-Resistance (High-Precision)** | **Keithley 2400** + **K2182** + Lakeshore 350/340 | Detecting subtle phase transitions. | 1 µΩ - 100 MΩ |
| **High-Resistance** | **Keithley 6517B** Electrometer + Lakeshore 350/340 | High bandgap materials, polymers, & ceramics. | 1 Ω - 10 PΩ |
| **Capacitance Analysis** | **Keysight E4980A** + Lakeshore 350/340 | C-V Analysis and Magnetocapacitance characterisation. | 20 Hz - 2 MHz |
| **Pyroelectric** | **K6517B** + Lakeshore 350/340 | Current vs Temp (detecting Curie temperature). | $10^{-15}$ A Resolution |

*While the current implementation drives specific instruments, the underlying framework is highly customizable. Researchers need only replace specific SCPI commands to utilize the suite with different models.*

> [!NOTE]
> **Understanding "Delta Mode":** The term "Delta Mode" refers specifically to a technique used by Keithley Models 6220 and 6221 Current Sources in conjunction with the Model 2182/2182A Nanovoltmeter for very low resistance measurements. This method is described in detail in the [Keithley Low Level Measurements Handbook](https://www.tek.com/en/documents/product-article/keithley-low-level-measurements-handbook---7th-edition). In this documentation, "Ultra Low Resistance Measurements" is used as the general scientific term, while "Delta Mode" may appear when specifically referencing the Keithley-specific method or program files.

### Advanced Temperature Step Control (Lakeshore 350)

`pica/lakeshore/T_Control_L350_Step_GUI_advanced.py` is a self-contained, advanced version of the classic step-sequence controller (`T_Control_L350_Step_GUI.py`, which remains unchanged for existing workflows). It embeds the field-tested v1.2 stabilization logic (two-stage approach, rolling-window tolerance + drift criterion, stabilization timeout) and adds:

- **Adaptive ramp rate** — the rate is computed per step as `|ΔT| × Step Factor`, then hard-capped by an editable low-temperature table (`30:0.3, 100:0.5, else:5` = max 0.3 K/min below 30 K, 0.5 K/min below 100 K, otherwise 5 K/min). This suppresses the large overshoot seen below 100 K on LN2-dewar probes, where even a small heater input raises the temperature substantially. The first setpoint of a run is approached extra slowly (**First-Step ×**, default 0.5). Untick *Adaptive ramp rate* to use a fixed manual rate.
- **Approach from one side** — optional "always from below/above" mode for hysteresis-sensitive measurements: the slow final ramp always enters the setpoint from the chosen side.
- **Stability criteria** — configurable ±tolerance band, rolling window length, drift limit (K/min), and a max-wait timeout with a loud warning if stabilization fails.
- **Safety** — hardcoded 340 K kill switch, user-set soft Max-Temp limit with graceful abort (ramp stopped, heater off), heater-range checks, and crash-safe shutdown on window close or error.
- **Run control & logging** — Pause/Resume, Skip Step, live-editable sequence, per-poll CSV (with ramp rate + phase columns), a per-setpoint summary CSV (rate used, stabilization time, outcome), and a live plot showing the setpoint and its tolerance band. After each stabilization the program beeps and waits for the **Proceed** button (external-measurement handshake).

The combined dielectric-scan program `pica/keysight/Step_Frequency_Scan_E4980A_GUI.py` (v1.3) carries the same advanced control set — adaptive ramp rate, approach-from-one-side, Pause/Skip, soft Max-Temp limit, tolerance band, and stabilization-summary CSV — as an embedded copy (every PICA program stays self-contained). Being an unattended overnight program, on stabilization timeout it logs a loud warning and proceeds with the frequency sweep instead of waiting.

### Module Previews

<p align="center">
  <img src="pica/assets/Images/screenshots/K6221_RT_Control.png" alt="K6221 RT Control" width="600">
  <br>
  <em>R-T measurement interface using the K6221/2182 for low-resistance samples, employing the Ultra Low Resistance Measurement technique to cancel thermal EMFs.</em>
</p>
<p align="center">
  <img src="pica/assets/Images/screenshots/K6517B_IV.png" alt="K6517B IV" width="600">
  <br>
  <em>Interface for current-voltage (I-V) characterisation of high-impedance materials using the Keithley 6517B Electrometer.</em>
</p>

---

## Demonstration of PICA (Screencast)

**A screencast demonstrating the high resistance IV module is available at [this link](https://drive.google.com/file/d/13W-Z4N-08t9m0xxuR30sjTLmUVG1VyQd/view?usp=sharing).**

---

## Pre-requisites: The VISA Driver

> [!WARNING]
> **A VISA Backend is Required:** [`PyVISA`](https://github.com/pyvisa/pyvisa) is a Python wrapper, not a driver. For PICA to communicate with hardware, you **must** install a VISA backend on your system first. If you attempt to run the software on a clean machine without a VISA implementation, it will fail to find the instruments. This is the most common failure point for new instrument control setups.
>
> Choose one of the following:
> - **NI-VISA:** The industry standard from National Instruments. Download and install it from the [NI website](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html#575764).
> - **PyVISA-py:** A backend written in pure Python that is installed automatically with PICA. It can be used as a fallback but may have limitations compared to vendor-specific drivers like NI-VISA. [PyVISA-py GitHub](https://github.com/pyvisa/pyvisa-py)
>
> **Before proceeding, verify your VISA installation.**

---

## Getting Started

PICA is available on [PyPI](https://pypi.org/project/pica-suite/) and structured as a standard Python package. The following instructions are for the supported Windows platform.

### Option 1: Install from PyPI (Recommended)

```bash
pip install pica-suite
```

To upgrade to the latest version (currently **v1.0.3**):

```bash
pip install --upgrade pica-suite
```

### Option 2: Install from Source

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create Virtual Environment & Install**
    ```bash
    # Create and activate a virtual environment
    python -m venv venv
    venv\Scripts\activate

    # Install the package and its dependencies
    pip install .
    ```

    To update PICA to the latest version, run the following command in the project directory:
    ```bash
    pip install --upgrade .
    ```
    If you are already in your virtual environment and want to force Python to reinstall the package (overwriting the old one), run this in your root project folder:
    ```bash
    pip install --force-reinstall .
    ```

    *Note: Ensure you have the NI-VISA drivers installed on your host machine to allow [`PyVISA`](https://github.com/pyvisa/pyvisa) to communicate with the hardware.*

---

## Running the Software

1.  **Graphical Launcher (Recommended)**
    The central dashboard for accessing all modules, the plotter, and the scanner.
    ```bash
    pica-gui
    ```
    > You can also run any of the individual GUI measurement modules independently. This is useful for quickly accessing a specific measurement without opening the main launcher. To do this, simply run the Python script for the desired module.
    > For example:
    > ```bash
    > python pica/keithley/k6517b/High_Resistance/IV_K6517B_GUI.py
    > ```

2.  **Command Line Interface (CLI)**
    For headless operation (e.g., Raspberry Pi).
    ```bash
    pica-cli
    ```

    > [!IMPORTANT]
    > **Template Scripts:** The instrument control modules (CLI's) provided are designed as **template scripts**. Users are expected to modify these scripts programmatically to adapt them to their specific experimental requirements and custom workflows. They are also excellent for developing **custom measurement protocols, sequences, and for learning instrument automation**. These scripts are typically named with an 'Instrument_Control' suffix to denote their programmatic nature. This approach ensures maximum flexibility and customization for advanced research applications.

    > [!NOTE]
    > **Legacy CLI Notice:** The PICA CLI (`pica-cli`) is retained to support legacy headless workflows. While fully functional for specific protocols, this interface is **less frequently maintained** and may not support recent features available in the GUI.
    >
    > We **strongly recommend** new users utilize the PICA GUI for the most complete and supported experience.

## System Requirements & Compatibility

**Supported Platform:** Windows 10 / 11
**Architecture:** x86_64

> [!IMPORTANT]
> **Windows Only**
> PICA is currently designed and validated exclusively for Windows environments.
> Linux and macOS are **not currently supported** due to dependencies on Windows-specific GUI libraries and font rendering.
> Attempting to run this software on non-Windows platforms may result in crashes or UI failures. Linux support is experimental for now.

## Example Usage: Running a Measurement

Upon launching PICA (`pica-gui`), select your desired measurement module from the main dashboard. Each module opens as a unified interface divided into two primary sections:

1.  **Configuration (Control Window - Left):**
    * **Setup:** Enlarge this window first to view all experimental parameters.
    * **Parameters:** Specify the `Sample Name`, select the `File Storage Location`, and choose the `Instrument Address` (GPIB/VISA) via the dropdown selection box.
    * **Protocol:** Define measurement specific settings such as voltage/current limits, temperature step sizes, and delay times.
    * **Console:** A scrollable log at the bottom provides a continuous, time-stamped stream of all operations and SCPI commands sent to the instruments.

2.  **Visualization (Plotter Window - Right):**
    * **Real-Time Monitoring:** Displays up to three simultaneous plots (e.g., Resistance vs. Temperature, Voltage vs. Current) updated in real-time.
    * **Data Analysis:** Includes options to switch axes to logarithmic scales for better visualization of wide-range data.
    * **Utilities:** Two buttons above the plot area provide instant access to:
        * **VISA Instrument Scanner:** To verify instrument connections and addresses.
        * **PICA Plotter Utility:** For post or during measurements comparison and detailed analysis.

*The interface is designed to be minimalistic to reduce unnecessary user interaction during active high-precision, low-noise transport measurements.*

---

## Running Tests Locally

To run the test suite locally, first install the development dependencies:
```bash
pip install -r requirements-dev.txt
```
Then, you can run the tests:
```bash
python -B -m pytest -p no:cacheprovider
```

**How to Check Coverage Locally**

To see the coverage percentage on your local machine, run this command instead:

```powershell
python -B -m pytest --cov=pica --cov-report=term-missing -p no:cacheprovider
```

### Experimental Linux Instructions

> [!WARNING]
> **Experimental Support:** The following instructions are for experimental purposes only. PICA is not officially supported on Linux (for now), and you will likely encounter functional or UI-related issues.

For users who wish to experiment with PICA on Linux, please be aware of the following:

1.  **Prerequisites:**
    *   **Tkinter Dependency:** On Linux, you must ensure `tkinter` is installed, as it is often not included by default.
        -   On Debian/Ubuntu: `sudo apt-get install python3-tk`
        -   For other distributions, use your package manager to install `python3-tk`.
    *   **Virtual Environment Activation:** To activate the virtual environment, use:
        ```bash
        source venv/bin/activate
        ```

2.  **Installation:**
    Follow the standard installation steps outlined in the [Getting Started](#getting-started) section. While the commands should run, be aware that the application GUI may not function correctly.

---

## Project History

For downloadable release builds, please visit the [releases page](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases).

PICA evolved from simple offline scripts in 2022 to a full-stack automated suite.

  * **v1.0.3 (Latest):** GUI refreshed with the **Latte** theme — lighter, warmer, and less dark. Available on [PyPI](https://pypi.org/project/pica-suite/).
  * **v1.0.1 / v1.0.0 (Initial Public Release):** Version numbering was reset from legacy development builds (v17.0) to v1.0.0 to standardize the package for public distribution and citation.
  * **v17.0:** Professional directory restructuring, Semantic Versioning, and documentation overhaul.
  * **v15.0:** JOSS submission preparation, CI/CD integration.
  * **v13.0:** Transition to Multiprocessing and standardized GUI themes.

The project's earlier history and foundational development are outlined below:

### 2022 - 2023: Inception & Prototyping

  - **2023 (Migration):** Moved from offline lab systems to GitHub; organized scripts into instrument modules (Keithley/Lake Shore).
  - **2022 (Origins):** Started in an air-gapped lab with `pyvisa` scripts replacing manual logging.
      - *Concept:* Proposed by Dr. Sudip Mukherjee to automate characterisation workflows.
      - *Prototypes:* Built alongside hardware upgrades and cryogenic probe work at UGC-DAE CSR.

> **Background:** For a detailed chronological log, see [`CHANGELOG.md`](CHANGELOG.md).

## Resources & Documentation

* **Online Documentation:** Full documentation site at [https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/).
* **PyPI Package:** Install directly via [https://pypi.org/project/pica-suite/](https://pypi.org/project/pica-suite/).
* **User Manual:** Details on the project and usage guides are available in the [User Manual](docs/User_Manual.md).
* **Instrument Manuals:** A list of instrument manuals is available in [docs/Instruments_Manuals_Lists.md](docs/Instruments_Manuals_Lists.md).
* **GitLab Backup:** This project is manually backed up weekly to a [GitLab repository](https://gitlab.com/prathameshnium/pica-python-instrument-control-and-automation).
* **Project Web Page:** [https://prathameshdeshmukh.site/pages/project-pica.html](https://prathameshdeshmukh.site/pages/project-pica.html).
* **Preprint:** Access the full preprint and related materials at [https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/) or download the [PDF directly](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/pica-paper.pdf) (alternate link at [Academia.edu](https://www.academia.edu/165176821/PICA_Advanced_High_Precision_Transport_Measurement_Automation_with_Python)).


---

## Citation

If you use this software in your research, please cite it:

```bibtex
@software{Deshmukh_PICA_2026,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Advanced High-Precision Transport Measurement Automation with Python}},
  month        = jan,
  day          = 26,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {1.0.3},
  doi          = {10.5281/zenodo.18377217},
  url          = {https://doi.org/10.5281/zenodo.18377217}
}
```

## Authors & Funding

This project is led by [**Prathamesh Deshmukh**](https://www.researchgate.net/profile/Prathamesh-Deshmukh-6) under the supervision of [**Dr. Sudip Mukherjee**](https://www.csr.res.in/Faculty/profile/889/893/Dr.SudipMukherjee) at the UGC-DAE Consortium for Scientific Research, Mumbai Centre, Bhabha Atomic Research Centre, Mumbai, 400 085, Maharashtra, India.

We acknowledge the financial support provided under the **SERB-CRG project grant No. CRG/2022/005676** from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science and Technology (DST), Government of India.

<p align="center">
  <img src="pica/assets/LOGO/UGC_DAE_CSR_NBG.jpeg" alt="UGC DAE CSR Logo" width="150">
</p>

## License

This project is licensed under the MIT License - see the [LICENSE file](LICENSE) for details.
