-----
\<div align="center"\>
\<img src="../assets/LOGO/PICA\_LOGO\_NBG.png" alt="PICA Logo" width="150"/\>
\<h1\>PICA User Manual\</h1\>
\<p\>\<strong\>Python-based Instrument Control and Automation Software Suite\</strong\>\</p\>
\<p\>\<em\>Comprehensive Guide for Version 15.0\</em\>\</p\>
\</div\>

-----

## 📖 Table of Contents

1.  [Overview](https://www.google.com/search?q=%231-overview)
2.  [Getting Started](https://www.google.com/search?q=%232-getting-started)
      * [Hardware Setup](https://www.google.com/search?q=%23hardware-setup)
      * [Software Installation](https://www.google.com/search?q=%23software-installation)
      * [Running the Software (GUI & CLI)](https://www.google.com/search?q=%23running-the-software)
      * [Connection Testing](https://www.google.com/search?q=%23connection-testing)
3.  [Software Architecture](https://www.google.com/search?q=%233-software-architecture)
4.  [Available Measurement Modules](https://www.google.com/search?q=%234-available-measurement-modules)
5.  [Testing & Validation](https://www.google.com/search?q=%235-testing--validation)
6.  [Technical Reference](https://www.google.com/search?q=%236-technical-reference)
      * [Instrument Specifications](https://www.google.com/search?q=%23instrument-specifications)
      * [GPIB Address Guide](https://www.google.com/search?q=%23gpib-address-guide)
      * [File Structure](https://www.google.com/search?q=%23file-structure)
7.  [Citation, Attribution & Funding](https://www.google.com/search?q=%237-citation-attribution--funding)
8.  [Version History](https://www.google.com/search?q=%238-version-history)

-----

## 1\. Overview

**PICA (Python-based Instrument Control and Automation)** is a modular software suite designed to provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research.

The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterization experiments. Built to streamline data acquisition and enhance experimental reproducibility, PICA leverages Python's `multiprocessing` library to ensure high stability by isolating each measurement process.

### Core Features

  * **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
  * **CLI Mode:** A new command-line interface for headless operation (e.g., via SSH or Raspberry Pi).
  * **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability.
  * **Integrated VISA Instrument Scanner:** An embedded utility for discovering and troubleshooting connections.
  * **Automated Testing:** Integrated CI/CD pipelines for logic verification.

> **⚠️ Important Note on Validation**
>
> While Version 15.0 introduces **Automated CI/CD Testing** that passes all logic checks, the refactoring required for packaging may introduce subtle timing differences on physical hardware. **A comprehensive round of manual validation on physical instruments is currently underway** to ensure full operational stability matches the simulations.

-----

## 2\. 🚀 Getting Started

### Hardware Setup

Before running the software, ensure your physical connections are established:

  * **USB to GPIB Converter:** Use a reliable interface cable (e.g., Keysight 82357B) to connect your computer to the instruments.
  * **Status Check:** Ensure the converter's status light is active (usually green).
  * **Instrument Config:** Enable GPIB communication on your physical instruments and note their addresses (e.g., 12, 24).

### Software Installation

**Prerequisites:**

  * **Python:** Version 3.10 or newer.
  * **NI-VISA Driver:** Install the National Instruments VISA Driver for your OS to enable communication.

**Installation Steps (Package Mode):**

PICA is now structured as a standard Python package. We recommend installing it in **editable mode**.

1.  **Clone the Repository:**

    ```bash
    git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create and Activate Virtual Environment:**

      * *Windows:*
        ```bash
        python -m venv venv
        venv\Scripts\activate
        ```
      * *macOS/Linux:*
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```

3.  **Install Dependencies:**
    Use the `-e` flag to install in editable mode. This installs PICA and dependencies (PyVISA, Pandas, etc.) while allowing you to modify code if necessary.

    ```bash
    pip install -e .
    ```

### Running the Software

You can now run PICA in two modes depending on your environment:

**1. Graphical Launcher (GUI)**
The standard dashboard for desktop users with a monitor.

```bash
python run_pica.py
```

**2. Command Line Interface (CLI)**
Use this for headless operation, automated environments, or running via SSH.

```bash
python pica_cli.py
```

### Connection Testing

You can quickly verify which instruments are connected and recognized by your system using the built-in scanner or this Python snippet:

```python
import pyvisa
rm = pyvisa.ResourceManager()
print(rm.list_resources())
# Output Example: ('GPIB0::12::INSTR', 'GPIB0::24::INSTR')
```

-----

## 3\. 🏗️ Software Architecture

The core design philosophy of PICA is the **separation of concerns**, implemented through a distinct Frontend-Backend architecture.

  * **Frontend (GUI):** Built with `Tkinter`, this layer handles user input, parameter validation, and live plotting. It runs in the main process to remain responsive.
  * **Backend (Logic):** The instrument control logic is encapsulated in a separate class. It handles all `PyVISA` communication and data acquisition.
  * **Process Isolation:** When a measurement starts, the frontend spawns the backend in a **separate, isolated process**. This ensures that a crash in the measurement script does not crash the main launcher.
  * **Communication:** Data flows from the backend to the frontend via a thread-safe `multiprocessing.Queue` for real-time visualization.

-----

## 4\. 🔬 Available Measurement Modules

The suite is organized into modules, each containing a specific experimental setup:

| Module | Configuration / Instrument | Use Case | Resistance Range |
| :--- | :--- | :--- | :--- |
| **Low-Resistance** | **Keithley 6221** + **2182** (Delta Mode) | Superconductors & metallic films | $10 n\Omega$ - $100 M\Omega$ |
| **Mid-Resistance** | **Keithley 2400** Source Meter | Semiconductors, oxides | $100 \mu\Omega$ - $200 M\Omega$ |
| **High-Precision** | **Keithley 2400** + **2182** | Subtle phase transitions | $1 \mu\Omega$ - $100 M\Omega$ |
| **High-Resistance** | **Keithley 6517B** Electrometer | Dielectrics, polymers, ceramics | $1 \Omega$ - $10^{16} \Omega$ |
| **Capacitance** | **Keysight E4980A** | C-V Analysis | 20 Hz - 2 MHz |
| **Temperature** | **Lakeshore 350** | Cryogenic Control | 1.4 K - 500 K |

-----

## 5\. 🧪 Testing & Validation

PICA now includes a robust test suite using `pytest`. It mocks hardware interactions, allowing the logic to be verified in a headless environment.

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

-----

## 6\. 📚 Technical Reference

### Instrument Specifications

Specifications for instruments used in the PICA project.

**Keithley 6221 + 2182A (Delta Mode)**

  * **Resistance:** \~10 nΩ to 200 MΩ
  * **Current Source:** 100 fA to 105 mA
  * **Voltage Measure:** 1 nV to 100 V

**Keithley 2400 SourceMeter**

  * **Resistance:** \< 0.2 Ω to \> 200 MΩ
  * **Current:** 10 pA to 1.05 A
  * **Voltage:** 1 µV to 210 V

**Keysight E4980A LCR Meter**

  * **Frequency:** 20 Hz to 2 MHz
  * **Basic Accuracy:** 0.05% (under optimal conditions)
  * **DC Bias:** -40 V to +40 V

**Lake Shore 350 Temperature Controller**

  * **Diode Range:** 1.4 K to 500 K (\< 0.1 mK resolution)
  * **RTD Range:** 14 K to 873 K (\< 1 mK resolution)

### GPIB Address Guide

Default addresses for PICA instruments. Use the **Test GPIB** utility in the GUI to confirm.

  * **Lakeshore 340:** `GPIB0::12::INSTR`
  * **Lakeshore 350:** `GPIB1::15::INSTR`
  * **Keithley 2400:** `GPIB1::4::INSTR`
  * **Keithley 6221:** `GPIB0::13::INSTR`
  * **Keithley 6517B:** `GPIB1::27::INSTR`
  * **Keithley 2182:** `GPIB0::7::INSTR`
  * **Keysight E4980A:** `GPIB0::17::INSTR`
  * **SRS SR830:** `GPIB0::8::INSTR`

### File Structure

Reference for the project directory layout (v15.0).

```text
PICA (Root Directory)/
    run_pica.py             <-- GUI Entry Point
    pica_cli.py             <-- CLI Entry Point
    setup.py                <-- Package installation script
    README.md
    CITATION.cff
    assets/                 <-- Images, Logos, Manuals
    docs/                   <-- Documentation
    tests/                  <-- Unit Tests (Pytest)
    Keithley_2400/          <-- Instrument Modules...
    Keithley_6517B/
    ...
```

-----

## 7\. 📄 Citation, Attribution & Funding

### Citation

If you use this software in your research, please cite it.

**BibTeX:**

```bibtex
@software{Deshmukh_PICA_2023,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  month        = sep,
  year         = 2023,
  publisher    = {GitHub},
  version      = {15.0.0},
  url          = {https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation}
}
```

### Authors

  * **Lead Developer:** Prathamesh Keshao Deshmukh
  * **Principal Investigator:** Dr. Sudip Mukherjee
  * **Institute:** UGC-DAE Consortium for Scientific Research, Mumbai Centre

### Funding

Financial support for this work was provided under **SERB-CRG project grant No. CRG/2022/005676** from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.

-----

## 8\. 📝 Version History
-----
[1.0.1] - 2025-12-02 (Current)
Changed

    Directory Structure: Refactored codebase into a professional project structure; moved numerous files to appropriate subdirectories for better organization.

    Versioning: Standardized version naming conventions. Adopted Semantic Versioning (v1.0.1).

Research & Documentation

    Paper Draft: Completed and presented the first draft of the research paper to Dr. Sudip Mukherjee.

    Feedback: Received critical feedback regarding the inclusion of ATMS (Advanced Trasport Measuremenet Systems).

[Community] - 2025-12-01

    Launch: PICA project posted on Hacker News.

**Version 15.0 (Current)**

  * **JOSS Submission:** Codebase refactored to meet open-source software standards.
  * **CI/CD:** Added automated testing pipelines via GitHub Actions.
  * **CLI Mode:** Added `pica_cli.py` for headless/command-line operation.
  * **Package Structure:** Migrated to `setup.py`/`pip install -e .` installation method.

**Version 14.1**

  * **Performance:** Optimized communication speeds; resolved UI lag.
  * **Documentation:** Added architecture details and code snippets for developers.
  * **Structure:** Cleaned up repository for initial release.

**Version 14.0**

  * **GUI Upgrade:** Updated frontend scripts to new standardized "Version 5" interfaces.
  * **New Module:** Added `RT_K2400_L350_T_Sensing_Frontend_v4.py` for passive monitoring.