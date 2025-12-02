-----
\<div align="center"\>
\<img src="../assets/LOGO/PICA\_LOGO\_NBG.png" alt="PICA Logo" width="150"/\>
\<h1\>PICA User Manual\</h1\>
\<p\>\<strong\>Python-based Instrument Control and Automation Software Suite\</strong\>\</p\>
\<p\>\<em\>Comprehensive Guide for Version 15.0\</em\>\</p\>
\</div\>

-----

## �� Table of Contents

1.  [Overview](https://www.google.com/search?q=%231-overview)
2.  [Statement of Need](#2-statement-of-need)
3.  [Getting Started](#3--getting-started)
    *   [Hardware Setup](#hardware-setup)
    *   [Software Installation](#software-installation)
    *   [Running the Software](#running-the-software)
    *   [Connection Testing](#connection-testing)
4.  [Software Architecture](#4--️-software-architecture)
5.  [Available Measurement Modules](#5--available-measurement-modules)
6.  [Testing & Validation](#6-testing--validation)
7.  [Technical Reference](#7--technical-reference)
    *   [Instrument Specifications](#instrument-specifications)
    *   [GPIB Address Guide](#gpib-address-guide)
    *   [File Structure](#file-structure)
8.  [Citation, Attribution & Funding](#8--citation-attribution--funding)
9.  [Version History](#9--version-history)

-----

## 1\. Overview
PICA (Python-based Instrument Control and Automation) is a modular, open-source software suite specifically designed to automate complex characterisation experiments and provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research.
Developed to operate as a custom laboratory-built measurement system, PICA provides a unifying graphical user interface (GUI) for orchestrating high-precision instruments, specifically Keithley SourceMeters/Nanovoltmeters, Lakeshore Temperature Controllers, and Keysight LCR Meters. The suite regulates the cryogenic environment to perform automated protocols such as temperature-dependent resistivity, current-voltage (I-V) characteristics, and pyroelectric current measurements.
The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterisation experiments. Built to streamline data acquisition and enhance experimental reproducibility, PICA leverages Python's `multiprocessing` library to ensure high stability by isolating each measurement process.


## 2\. Statement of Need

Advancements in experimental physics critically depend on the accurate characterization of material properties under extreme physical conditions. Such experiments typically require the coordinated operation and precise control of multiple instruments sourced from different vendors.

Researchers are often faced with a choice between using expensive proprietary software platforms such as LabVIEW or developing custom measurement scripts from scratch. Python libraries such as PyVISA and PyMeasure provide robust low-level driver support and are essential for instrument communication and control. However, these libraries primarily function as developer-oriented toolkits: they generally require users to possess detailed knowledge of measurement protocols and, in the case of PyVISA, familiarity with SCPI (Standard Commands for Programmable Instruments). Furthermore, users must design and implement their own graphical user interfaces (GUIs) tailored to specific experimental workflows. These libraries also do not natively provide comprehensive multithreading support, thereby requiring users to understand and manage concurrency and other computational aspects themselves.

By contrast, LabVIEW offers a graphical programming paradigm that simplifies certain aspects of instrument control but introduces other limitations. Its visual programming model is difficult to integrate with modern software engineering practices such as version control, and its proprietary nature constrains the extent to which users can modify low-level behaviour compared to Python-based solutions.

This situation reveals a clear gap for an open-source, laboratory-ready framework that provides well-tested measurement protocols together with an intuitive user interface, enabling experimentalists to perform sophisticated measurements without directly interacting with source code. At the same time, being implemented in open-source Python would preserve the ability for advanced users to modify virtually any component of the system and to contribute enhancements back to the project. Such a framework would foster a more open and collaborative scientific ecosystem, facilitating reproducibility, extensibility, and community-driven development in experimental physics research.

PICA was developed at the UGC–DAE Consortium for Scientific Research, Mumbai Centre, a research institute under the Government of India. The mandate of the institute is to support universities, including those in remote locations, in conducting advanced research. The open-source nature of PICA is fully aligned with this mandate, as it facilitates broader access to computational tools and thereby promotes and enhances the research capabilities of the scientific community.

We tried to reduce dependencies, making it suitable for the laboratory systems that are not connected to the internet by providing stable, scientifically validated protocols and a robust user interface for conducting measurements on our ATMS (Advanced Transport Measurement System), a laboratory-built platform comprising custom-developed hardware and software.

Although PICA was initially designed for our in-house ATMS infrastructure, the underlying code and framework are sufficiently generalizable for use in a wide range of experimental setups. We particularly encourage researchers in developing countries, where access to advanced measurement systems may be limited, to adopt and adapt this tool. Furthermore, the architecture allows for modular extensions, enabling the integration of additional functionalities as required by users. 

### Core Features

  * **Accessibility:** PICA provides a professional dashboard that enables researchers without programming experience to configure and execute complex measurements.
  * **Physical Validation:** PICA protocols are routinely employed for cryogenic transport measurements in the temperature range 80–320 K at the UGC–DAE Consortium for Scientific Research, Mumbai Centre. Particular emphasis is placed on ensuring that the protocols are physically valid and that any artifacts arising from instrument output start-up transients, synchronization errors, or other physical anomalies are identified and eliminated.
 
  *  **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
  * **CLI Mode:** A new command-line interface for headless operation (e.g., via SSH or Raspberry Pi).
  * **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability.
  * **Integrated VISA Instrument Scanner:** An embedded utility for discovering and troubleshooting connections.
  * **Operational Transparency:** Unlike black-box solutions, PICA exposes real-time logs that facilitate debugging in the event of errors or anomalies, thereby enhancing scientific reproducibility.
  * **Automated Testing:** Integrated CI/CD pipelines for logic verification.

## 3\.  Getting Started

### Hardware Setup
Prior to executing the software, verify that all physical connections between the measurement instruments and the computer hosting PICA are correctly and securely established. [1]

There are multiple methods for establishing communication between an instrument and a computer, including direct USB, LAN (Ethernet), or via a USB‑to‑GPIB converter. Use a reliable, compliant interface cable (e.g., Keysight 82357B) to connect the computer to the instruments.

If a converter (e.g., GPIB‑to‑USB) is used, confirm that its status indicator is active (typically a green LED). Similarly, for LAN connections, check the link/activity indicators on the instrument and network interface. Many instruments also provide a “REMOTE” or similar status indication on their front panel to show that a remote communication session has been established.

Instrument configuration: enable the appropriate communication interface (GPIB, USB, or LAN) on each physical instrument and record their corresponding addresses. If necessary, modify the instrument address and related communication parameters through the instrument’s configuration or setup menu..

### Software Installation

**Prerequisites:**

*   **Python:** Version 3.10 or newer.
*   **NI-VISA Driver:** Install the National Instruments VISA Driver for your OS to enable communication.

Install the required dependencies as specified in the `requirements.txt` file.

**Installation Steps (Package Mode):**

PICA is now structured as a standard Python package. We recommend installing it in **editable mode**.

1.  **Clone the Repository:**

    ```bash
    git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
    cd PICA-Python-Instrument-Control-and-Automation
    ```

2.  **Create and Activate Virtual Environment:**

    *   *Windows:*
        ```bash
        python -m venv venv
        .\venv\Scripts\activate
        ```
    *   *macOS/Linux:*
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

PICA can be executed in two modes: a graphical user interface (GUI) for interactive use and a command-line interface (CLI) for headless or automated environments.

#### 1. Graphical Launcher (GUI)
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



## 4\. ️ Software Architecture

The core design philosophy of PICA is the **separation of concerns**, implemented through a distinct Frontend-Backend architecture.

  * **Frontend (GUI):** Built with `Tkinter`, this layer handles user input, parameter validation, and live plotting. It runs in the main process to remain responsive.
  * **Backend (Logic):** The instrument control logic is encapsulated in a separate class. It handles all `PyVISA` communication and data acquisition.
  * **Process Isolation:** When a measurement starts, the frontend spawns the backend in a **separate, isolated process**. This ensures that a crash in the measurement script does not crash the main launcher.
  * **Communication:** Data flows from the backend to the frontend via a thread-safe `multiprocessing.Queue` for real-time visualization.

-----

## 5\.  Available Measurement Modules

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

# Example usage
# Functionality

-----

## 7\.  Technical Reference

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

Default communication addresses for selected instruments. Use the **Test GPIB** utility available in the graphical user interface (GUI) to verify proper connectivity and configuration.
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

## 8\.  Citation, Attribution & Funding

### Citation

If you use this software in your research, please cite it.

**BibTeX:**

```bibtex
@software{Deshmukh_PICA_2025,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  month        = sep,
  year         = 2025,
  publisher    = {GitHub},
  version      = {1.0.1},
  url          = {https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation}
}
```


-----

## 9\.  Version History
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
# tests
To ensure measurement reliability and correct software operation, comprehensive testing is required. To this end, all modules were thoroughly tested in conjunction with the corresponding hardware. In addition to automated procedures, extensive manual testing was performed directly on the instrument, with particular emphasis on identifying limitations and edge conditions. In practice, each individual measurement effectively serves as a distinct test case. [1]

Recognizing that full validation of the software suite is difficult without access to the complete set of instrument hardware, a limited set of automated tests has been implemented to provide at least a baseline level of verification.

For manual testing of the PICA graphical user interface (GUI), the user can interact with different components, such as resizing the measurement panel and the plotting panel, and launching multiple instances of PICA modules (e.g., different measurement modules, the plotting utility, and the launcher). On modern hardware, the system has been empirically verified not to hang under such multitasking conditions.

A particularly accessible component for testing is the plotting utility, <#plotterutil#>. It can be evaluated by repeatedly plotting data and modifying the data points to confirm that all visual elements update in real time. The performance of the measurement modules can likewise be assessed under concurrent operation to ensure there are no issues with multitasking. In addition, one should verify that all links and buttons from the main dashboard to the individual modules function correctly and lead to the intended targets. 
# Automated tests
## 6\. Testing & Validation
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
# Community guidelines
We encourage the community to contribute to PICA, either by developing new modules or by correcting existing issues. Individuals interested in contributing are invited to report problems or feature requests via the GitHub “Issues” tab. We are available to provide support with the installation and configuration of PICA, as well as with the setup of custom measurement systems; therefore, prospective users should not hesitate to contact us.
Additional details regarding the code of conduct for contributors are available at:

### Authors

  * **Lead Developer:** Prathamesh Keshao Deshmukh
  * **Principal Investigator:** Dr. Sudip Mukherjee
  * **Institute:** UGC-DAE Consortium for Scientific Research, Mumbai Centre

### Funding

Financial support for this work was provided under **SERB-CRG project grant No. CRG/2022/005676** from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.
