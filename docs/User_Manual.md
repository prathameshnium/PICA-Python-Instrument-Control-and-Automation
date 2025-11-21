
<div align="center">
  <img src="../assets/LOGO/PICA_LOGO_NBG.png" alt="PICA Logo" width="150"/>
  <h1>PICA User Manual</h1>
  <p><strong>Python-based Instrument Control and Automation Software Suite</strong></p>
  <p><em>Comprehensive Guide for Version 14.1</em></p>
</div>

---

## 📖 Table of Contents
1.  [Overview](#1-overview)
2.  [Getting Started](#2-getting-started)
    * [Hardware Setup](#hardware-setup)
    * [Software Installation](#software-installation)
    * [Connection Testing](#connection-testing)
3.  [Software Architecture](#3-software-architecture)
4.  [Available Measurement Modules](#4-available-measurement-modules)
5.  [Technical Reference](#5-technical-reference)
    * [Instrument Specifications](#instrument-specifications)
    * [GPIB Address Guide](#gpib-address-guide)
    * [File Structure](#file-structure)
6.  [Citation & Attribution](#6-citation--attribution)
7.  [Version History](#7-version-history)

---

## 1. Overview

**PICA (Python-based Instrument Control and Automation)** is a modular software suite designed to provide a robust framework for automating laboratory instruments in materials science and condensed matter physics research.

The suite features a central graphical user interface (GUI), the **PICA Launcher**, which serves as a dashboard for managing and executing a variety of characterization experiments. Built to streamline data acquisition and enhance experimental reproducibility, PICA leverages Python's `multiprocessing` library to ensure high stability by isolating each measurement process.

### Core Features
* **Centralized Control Dashboard:** A comprehensive GUI for launching all measurement modules.
* **Isolated Process Execution:** Each script operates in a discrete process, guaranteeing application stability.
* **Integrated VISA Instrument Scanner:** An embedded utility for discovering and troubleshooting connections.
* **Modular Architecture:** Each experimental setup is encapsulated in a self-contained module.

---

## 2. 🚀 Getting Started

### Hardware Setup
Before running the software, ensure your physical connections are established:
* **USB to GPIB Converter:** Use a reliable interface cable (e.g., Keysight 2400) to connect your computer to the instruments.
* **Status Check:** Ensure the converter's status light is on (usually green), indicating a proper connection.
* **Instrument Config:** Turn on GPIB communication on your physical instruments and note their addresses (e.g., 12, 24).

### Software Installation

**Prerequisites:**
* **Python:** Version 3.9 or newer.
* **NI-VISA Driver:** Install the National Instruments VISA Driver for your OS to enable communication.

**Installation Steps:**
1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git)
    cd PICA-Python-Instrument-Control-and-Automation
    ```
2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Launch PICA:**
    ```bash
    python PICA_v6.py
    ```

### Connection Testing
You can quickly verify which instruments are connected and recognized by your system using the built-in scanner or this Python snippet:

```python
import pyvisa
rm = pyvisa.ResourceManager()
print(rm.list_resources())
# Output Example: ('GPIB0::12::INSTR', 'GPIB0::24::INSTR')
````

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
| **High-Resistance** | **Keithley 6517B** Electrometer | Dielectrics, polymers, ceramics | $1 \Omega$ - $10 P\Omega$ |
| **Capacitance** | **Keysight E4980A** | C-V Analysis | 20 Hz - 2 MHz |
| **Temperature** | **Lakeshore 350** | Cryogenic Control | 1.4 K - 500 K |

-----

## 5\. 📚 Technical Reference

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

Default addresses for PICA instruments. Use the **Test GPIB** utility to confirm.

  * **Lakeshore 340:** `GPIB0::12::INSTR`
  * **Lakeshore 350:** `GPIB1::15::INSTR`
  * **Keithley 2400:** `GPIB1::4::INSTR`
  * **Keithley 6221:** `GPIB0::13::INSTR`
  * **Keithley 6517B:** `GPIB1::27::INSTR`
  * **Keithley 2182:** `GPIB0::7::INSTR`
  * **Keysight E4980A:** `GPIB0::17::INSTR`
  * **SRS SR830:** `GPIB0::8::INSTR`

### File Structure

Reference for the project directory layout.

```text
PICA (Root Directory)/
    PICA_v6.py              <-- Main Entry Point
    README.md
    requirements.txt
    assets/                 <-- Images, Logos, Manuals
    deployment/             <-- Build scripts (Picachu.py)
    docs/                   <-- Documentation
    paper/                  <-- JOSS Submission Files
    tests/                  <-- Unit Tests
    Keithley_2400/          <-- Instrument Modules...
    Keithley_6517B/
    ...
```

-----

## 6\. 📄 Citation & Attribution

If you use this software in your research, please cite it.

**BibTeX:**

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
```

**Authors:**

  * **Lead Developer:** Prathamesh Keshao Deshmukh
  * **Principal Investigator:** Dr. Sudip Mukherjee
  * **Institute:** UGC-DAE Consortium for Scientific Research, Mumbai Centre

-----

## 7\. 📝 Version History

**Version 14.1 (Current)**

  * **Performance:** Optimized communication speeds; resolved UI lag.
  * **Documentation:** Added architecture details and code snippets for developers.
  * **Structure:** Cleaned up repository for open-source release.

**Version 14.0**

  * **GUI Upgrade:** Updated frontend scripts to new standardized "Version 5" interfaces.
  * **New Module:** Added `RT_K2400_L350_T_Sensing_Frontend_v4.py` for passive monitoring.

**September 17, 2025**

  * **Keithley 6517B:** New High-Resistance R vs. T and Pyroelectric measurements.
  * **Keithley 2400:** Added V vs. T measurement capability.
  * **General:** Deployed new GPIB interface tester.

<!-- end list -->

```
```