# PICA User Manual

<p align="center">
  <img src="../pica/assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="250">
</p>


**Python-based Instrument Control and Automation Software Suite**

*Comprehensive Guide for Version 17.0.0*

<hr />

## Table of Contents

1. [Overview](#1-overview)
2. [Design Philosophy & Architecture](#2-design-philosophy--architecture)
3. [Installation & Setup](#3-installation--setup)
4. [Core Utilities](#4-core-utilities)
5. [Supported Measurement Modules](#5-supported-measurement-modules)
   * [Low Resistance (Delta Mode)](#51-low-resistance-delta-mode)
   * [General Transport (Standard I-V & R-T)](#52-general-transport-standard-i-v--r-t)
   * [High Precision Transport](#53-high-precision-transport)
   * [Electrometry & High Resistance](#54-electrometry--high-resistance)
   * [Pyroelectric Measurements](#55-pyroelectric-measurements)
   * [High Voltage Poling](#56-high-voltage-poling)
   * [Dielectric Spectroscopy](#57-dielectric-spectroscopy)
6. [Technical Reference](#6-technical-reference)
7. [Citation & Funding](#7-citation--funding)
8. [Future Development](#8-future-development)
9. [Appendix A: Project File Structure](#9-appendix-a-project-file-structure)

<hr />

## 1. Overview

PICA (Python-based Instrument Control and Automation) is a modular, open-source software suite designed to democratize advanced instrument automation in experimental physics and materials science.

While commercial solutions (e.g., LabVIEW) are powerful, they are often cost-prohibitive, closed-source, and difficult to customize for unique experimental setups. PICA bridges this gap by providing a **unified, adaptable framework** for orchestrating diverse instruments - such as SourceMeters, Nanovoltmeters, and Temperature Controllers.

Although originally developed for cryogenic transport systems, PICA's modular architecture allows it to be deployed in **any laboratory environment** utilizing supported VISA-compatible instruments. The suite is managed via a centralized **PICA Launcher**, an intuitive dashboard that enables researchers to configure and execute complex characterization experiments without writing a single line of code.

## 2. Design Philosophy & Architecture

PICA was constructed on a core philosophy of **robustness, modularity, and accessibility**, prioritizing open standards over proprietary "black box" solutions.

### 2.1 The Choice of Python
Python was selected as the foundational language for PICA due to its ubiquity in the scientific community:
* **Scientific Ecosystem:** Libraries like `NumPy` (array operations), `Pandas` (data structuring), and `Matplotlib` (publication-quality plotting) create a seamless workflow from acquisition to analysis.
* **PyVISA Integration:** The `PyVISA` library provides platform-independent wrappers for VISA drivers, allowing communication via simple, readable commands (e.g., ``instrument.query('*IDN?')``) rather than complex low-level protocols.
* **Cross-Platform:** PICA runs on Windows, Linux, and macOS with minimal modification, accommodating diverse lab environments.

### 2.2 The Case for GUIs
While early automation scripts often rely on Command Line Interfaces (CLIs), the final PICA suite prioritizes full-featured GUIs built with `Tkinter`. This strategic decision was guided by:
* **Error Prevention:** GUIs employ input validation and dropdown menus to restrict parameters to safe/logical ranges, preventing the "invalid command" errors common in CLI environments.
* **Real-Time Feedback:** Embedded `Matplotlib` plots provide immediate visualization of incoming data. This allows researchers to spot physical anomalies, noise, or connection issues instantly, potentially saving hours of wasted experimental time.
* **Workflow Visualization:** A visual interface helps new users and students mentally map the experimental workflow, reducing the learning curve.

### 2.3 Operational Transparency (No "Black Box")
To foster trust and reproducibility, PICA rejects the opaque nature of proprietary software. Each measurement module features an **Embedded Console Log**:
* **Status Streaming:** Displays a real-time stream of operations, such as "Ramping temperature to 300 K" or "Connecting to GPIB0::4::INSTR".
* **Immediate Diagnostics:** Instantly reports VISA timeouts or command errors, providing exact context for hardware failures rather than generic error codes.

### 2.4 Architecture: Process Isolation
PICA employs a multiprocess architecture using Python's `multiprocessing` library.
* **Frontend (GUI):** Handles user interaction and live plotting.
* **Backend (Logic):** Executes in a **separate, isolated process**.
This ensures that if a measurement script hangs due to a hardware timeout, it does not crash the main application, preserving the stability of the suite and other concurrent tasks.

## 3. Installation & Setup

### 3.1 System Prerequisites
1.  **Python 3.9+**: The core execution environment.
2.  **VISA Drivers:** A critical non-Python dependency. You must install vendor-supplied drivers (e.g., NI-VISA, Keysight IO Libraries) to provide the underlying communication layer between the OS and GPIB/USB hardware.
3.  **Dependencies:** Install via `pip install -r requirements.txt`.
 
### 3.2 Installation Procedure
```bash
# Clone the repository
git clone https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
cd PICA-Python-Instrument-Control-and-Automation

# Create virtual environment (Recommended)
python -m venv venv
# Activate: venv\Scripts\activate (Windows) or source venv/bin/activate (Linux/Mac)

# Install Python dependencies
pip install -r requirements.txt
```

### 3.3 Execution

Launch the main dashboard:

```bash
python run_pica.py
```

## 4\. Core Utilities

### 4.1 VISA Instrument Scanner

*File Reference: `pica/utils/GPIB_Instrument_Scanner_GUI.py`*

Automatically launched upon startup (and accessible within modules), this utility scans for connected hardware. It uses `ResourceManager.list_resources()` to find devices and sends a standard ``*IDN?`` query to verify communication. This allows users to verify their hardware configuration before starting any experiment.

### 4.2 PICA Plotter Utility

*File Reference: `pica/utils/PlotterUtil_GUI.py`*

A standalone, multiprocessing-enabled tool for detailed data analysis. Unlike the minimalist embedded plots in the measurement modules, this utility allows:

  * **Comparative Analysis:** Overlaying multiple `.csv` or `.dat` files.
  * **Live Updates:** Monitoring active experiments by auto-refreshing data from disk.
  * **Flexible Axis Control:** Toggling linear/log scales to analyze data spanning orders of magnitude.

### 4.3 Embedded Document Viewer

To ensure the software is self-contained (useful for offline lab computers), PICA includes an in-app viewer for project documentation, including this User Manual, the License, and the Changelog.

## 5\. Supported Measurement Modules

PICA is designed to be hardware-agnostic where possible, but optimized for specific classes of instruments. The following modules represent the core capabilities of the suite, supporting a resistance scale spanning **24 orders of magnitude** (10 nOhm to 10 POhm) depending on the hardware used.

### 5.1 Low Resistance (Delta Mode)

**Target Hardware:** Keithley 6221 (Current Source) + K2182 (Nanovoltmeter).
**Typical Range:** 10 nOhm to 100 MOhm.

  * **Scientific Objective:** Ideal for superconductors, metallic films, and low-impedance devices. It actively cancels thermal offsets (Seebeck EMFs) generated in leads and contacts.
  * **Principle:** Uses the **AC Delta Method**.
    1.  Source +I, measure V1.
    2.  Source -I, measure V2.
    3.  Compute V\_corr = (V1 - V2) / 2.
        The software synchronizes the source and voltmeter via a **hardware trigger link (RS-232)** for microsecond-level timing.

### 5.2 General Transport (Standard I-V & R-T)

**Target Hardware:** Keithley 2400 SourceMeter (or compatible SMU).
**Typical Range:** 100 uOhm to 200 MOhm.

  * **Scientific Objective:** General transport characterization for semiconductors, oxides, and devices.
  * **Capabilities:**
      * **I-V Sweep:** Linear sweeps, hysteresis loops, or custom current lists.
      * **R-T Active Control:** Applies constant DC current while coordinating with a temperature controller (e.g., Lake Shore 350) to ramp temperature.

### 5.3 High Precision Transport

**Target Hardware:** Keithley 2400 (Source) + K2182 (Nanovoltmeter).
**Typical Range:** 1 uOhm to 100 MOhm.

  * **Scientific Objective:** Detects subtle phase transitions in semiconductors and oxides where standard SMU resolution is insufficient.
  * **Advantage:** Combines the stable sourcing of the SMU with the nanovolt-level sensitivity of a dedicated voltmeter, utilizing a true 4-wire configuration to eliminate lead resistance errors.

### 5.4 Electrometry & High Resistance

**Target Hardware:** Keithley 6517B Electrometer (or compatible High-R meter).
**Typical Range:** 1 Ohm to 10 POhm (10^16 Ohm).

  * **Scientific Objective:** Characterization of dielectrics, polymers, and ceramics (Electrometry).
  * **Principle (Voltage Driven):** Applies a high voltage and measures the resulting leakage current (pA/fA range).
  * **Note:** PICA manages settling times to account for the capacitive nature of high-impedance setups, ensuring steady-state ohmic currents are recorded.

### 5.5 Pyroelectric Current Measurements

**Target Hardware:** Keithley 6517B Electrometer + Temperature Controller.
**Sensitivity:** Down to 1 fA (10^-15 A).

This module automates the measurement of pyroelectric currents (Ip) as a function of temperature, commonly used to characterize ferroelectric phase transitions and identify **Curie Temperatures** (Tc).

  * **Workflow:**
    1.  **Poling (Optional):** Apply bias field while cooling.
    2.  **Heating:** Remove bias; heat sample at a linear rate.
    3.  **Measurement:** Record the depolarization current peak indicative of phase transition.
  * **Best Practice:** For measurements in the fA range, ensure your setup utilizes proper shielding (e.g., double-layer Faraday cage).

### 5.6 High Voltage Poling

**Target Hardware:** Keithley 6517B (Voltage Source).
**Capabilities:** High Voltage Sourcing.

This utility provides a dedicated interface for **In-situ and ex-situ electrical poling** of materials.

  * **Objective:** Establish a uniform ferroelectric polarization state in samples before characterization.
  * **Applications:** Preparing samples for pyroelectric current measurements, converse magnetoelectric studies, and ex-situ neutron diffraction studies on poled materials.

### 5.7 Dielectric Spectroscopy

**Target Hardware:** Keysight E4980A Precision LCR Meter.
**Frequency Range:** Instrument dependent (typically 20 Hz to 2 MHz).

  * **Scientific Objective:** Measures Capacitance (C) and Loss Tangent (tan delta) as a function of frequency or DC bias voltage (C-V Analysis).

## 6\. Technical Reference

### File Naming Convention

To ensure data integrity and easy sorting, PICA automatically generates filenames using a standardized format. This allows for easier parsing by external analysis tools.
Format: `[SampleName]_[Timestamp]_[Identifier].dat`
Example: `SampleA_2025-12-04_1430_IV_Sweep.dat`

### GPIB Address Guide

PICA uses standard VISA resource strings. While the defaults below are common, users should verify their specific instrument addresses using the built-in **Instrument Scanner** or front-panel settings.

  * **Lake Shore 350:** `GPIB1::15::INSTR`
  * **Keithley 2400:** `GPIB1::4::INSTR`
  * **Keithley 6221:** `GPIB0::13::INSTR`
  * **Keithley 2182:** `GPIB0::7::INSTR`
  * **Keithley 6517B:** `GPIB1::27::INSTR`
  * **Keysight E4980A:** `GPIB0::17::INSTR`
  * **SRS SR830:** `GPIB0::8::INSTR`

## 7\. Citation & Funding

**Collaborative Ecosystem:**
PICA is open-source (MIT License) to foster transparency. By providing the source code, the measurement protocols become auditable, ensuring that experimental conditions are reproducible and not hidden behind a proprietary "black box." We encourage other research groups to adapt these scripts for their specific hardware configurations.

**Funding:**
Supported by SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), Govt. of India.

**Citation:**

```bibtex
@software{Deshmukh_PICA_2025,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Python-based Instrument Control and Automation Software Suite}},
  year         = 2025,
  publisher    = {GitHub},
  version      = {17.0.0},
  url          = {https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation}
}
```

## 8\. Future Development

The following modules and features are currently under active development and are slated for upcoming releases.

### 8.1 AC Resistivity (Lock-In)

*Status: Under Development*

  * **Instruments:** Keithley 6221 (AC Source) + SRS SR830 (DSP Lock-In Amplifier).
  * **Resistance Range:** \~ 20 nOhm to 1 MOhm.
  * **Scientific Objective:** Probes frequency-dependent transport phenomena.
  * **Use Case:** Useful for distinguishing between different conduction mechanisms by analyzing the frequency response of the sample's resistance.
  * **Workflow:** The Keithley 6221 provides a precise AC excitation current, while the Lock-In Amplifier (SR830) extracts the signal amplitude and phase with high noise rejection, allowing for measurements in high-noise environments.

### 8.2 Standalone Executables

In the future, I also plan to develop executable (`.exe`) versions of the PICA software suite. This will remove the need for users to manage Python environments and dependencies, further simplifying the setup process and facilitating rapid adoption in laboratories with strict IT policies or offline computers.


## Authors & Acknowledgments
<p align="center">
  <img src="pica/assets/LOGO/UGC_DAE_CSR_NBG.jpeg" alt="UGC DAE CSR Logo" width="250">
</p>
  - **Lead Developer:** [**Prathamesh Deshmukh**](https://prathameshdeshmukh.site/)
  - **Principal Investigator:** [**Dr. Sudip Mukherjee**](https://www.google.com/search?q=https://www.researchgate.net/lab/Sudip-Mukherjee-Lab)
  - **Affiliation:** [*UGC-DAE Consortium for Scientific Research, Mumbai Centre*](https://www.csr.res.in/Mumbai_Centre)

**Funding:**
Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF).

## License

This project is licensed under the MIT License - see the LICENSE file for details.
## 9\. Appendix A: Project File Structure

For developers and advanced users, the following reference outlines the PICA directory structure (v17.0.0).

```text
PICA (Root Directory)/
    .coveragerc
    .gitignore
    CHANGELOG.md
    CITATION.cff
    CODE_OF_CONDUCT.md
    CONTRIBUTING.md
    LICENSE
    README.md
    path.py
    pica_cli.py
    pyproject.toml
    requirements.txt
    run_pica.py
    .github/
        workflows/
            build_pdf.yml
            codeql.yml
            joss_tests.yml
            python-app.yml
    docs/
        Ranges_for_Measurements.md
        User_Manual.md
        paper.bib
        paper.md
    pica/
        __init__.py
        cli.py
        main.py
        assets/                 <-- Images, Logos
        keithley/
            delta_mode/         <-- Low Resistance (K6221 + K2182)
                Delta_RT_K6221_K2182_L350_Sensing_GUI.py
                Delta_RT_K6221_K2182_L350_T_Control_GUI.py
                IV_K6221_DC_Sweep_GUI.py
            k2400/              <-- Mid Resistance (K2400 Standard)
                IV_K2400_GUI.py
                RT_K2400_L350_T_Control_GUI.py
                RT_K2400_L350_T_Sensing_GUI.py
            k2400_2182/         <-- Mid Resistance (High Precision)
                IV_K2400_K2182_GUI.py
                RT_K2400_K2182_T_Control_GUI.py
            k6517b/             <-- High Resistance & Pyroelectric
                High_Resistance/
                    IV_K6517B_GUI.py
                    RT_K6517B_L350_T_Control_GUI.py
                Pyroelectricity/
                    Pyroelectric_K6517B_L350_GUI.py
        keysight/               <-- Dielectric (E4980A)
            CV_KE4980A_GUI.py
        lakeshore/              <-- Temperature Control
            T_Control_L350_RangeControl_GUI.py
            T_Sensing_L350_GUI.py
        lockin/                 <-- Lock-in Amplifiers (Experimental)
            BasicTest_S830_Instrument_Control.py
        utils/                  <-- Core Utilities
            GPIB_Instrument_Scanner_GUI.py
            PlotterUtil_GUI.py
    tests/                      <-- Automated Test Suite
```
