---
myst:
  html_meta:
    description: "Complete user manual for PICA v1.0.5 — installation, VISA/GPIB setup, safety, and every measurement module: ultra-low resistance (delta mode), I-V, high-resistance electrometry, pyroelectric current, dielectric spectroscopy, and temperature control."
    keywords: "PICA user manual, PyVISA setup, NI-VISA, delta mode, Keithley 6221 2182, Keithley 2400, Keithley 6517B electrometer, Keysight E4980A LCR, Lakeshore 350, pyroelectric measurement, dielectric spectroscopy"
---

# PICA: Advanced High-Precision Transport Measurement Automation with Python

*Comprehensive Guide for Version 1.0.5*

---

:::{note}
**v1.0.5 is now live!** Lakeshore enhancements, new utilities & core fixes. Install or upgrade via pip:

```bash
pip install --upgrade pica-suite
```

**v1.0.6 is coming soon** — bringing Novocontrol Alpha-A broadband dielectric spectroscopy (experimental), an interactive SCPI console, and plotting engine improvements.
:::

---

## Table of Contents

1. [Overview](#1-overview)
2. [Design Philosophy & Architecture](#2-design-philosophy--architecture)
3. [Installation & Setup](#3-installation--setup)
4. [Safety Precautions](#4-safety-precautions)
5. [Core Utilities](#5-core-utilities)
6. [Supported Measurement Modules](#6-supported-measurement-modules)
   * [Ultra Low Resistance Measurements](#61-ultra-low-resistance-measurements)
   * [General Transport (Standard I-V & R-T)](#62-general-transport-standard-i-v--r-t)
   * [High Precision Transport](#63-high-precision-transport-mid-resistance-range)
   * [Electrometry & High Resistance](#64-electrometry--high-resistance)
   * [Pyroelectric Current Measurements](#65-pyroelectric-current-measurements)
   * [High Voltage Poling](#66-high-voltage-poling)
   * [Dielectric Spectroscopy](#67-dielectric-spectroscopy)
   * [Broadband Dielectric Spectroscopy](#68-broadband-dielectric-spectroscopy)
   * [Standalone Temperature Utilities](#69-standalone-temperature-utilities)
7. [Releases and Versions](#7-releases-and-versions)
8. [Common Issues & Troubleshooting](#8-common-issues--troubleshooting)
9. [Technical Reference](#9-technical-reference)
10. [Citation & Open Source](#10-citation--open-source)
   * [Preprint](#101-preprint)
11. [Future Development](#11-future-development)
12. [Adding a New Instrument](#12-adding-a-new-instrument)
13. [Authors & Acknowledgments](#13-authors--acknowledgments)
14. [License](#14-license)
15. [Appendix A: Project File Structure](#15-appendix-a-project-file-structure)

---

## 1. Overview

High-precision, low-noise transport measurements are essential for advancing research in spintronics and materials characterization. To enable such progress, highly precise and accurate automation software is required. PICA (Python-based Instrument Control and Automation) is a modular, open-source software suite designed to automate advanced transport measurements for electronic devices and material samples. PICA is designed as a versatile framework capable of operating on any standard laboratory workstation. 
It provides an extensible, unified graphical user interface (GUI) for orchestrating high-precision instruments, specifically current source (DC/AC) units, nanovoltmeters, high resistance electrometers, impedance analyser, and temperature controllers. Built on the robust Python scientific ecosystem, PICA leverages community standard libraries as an alternative to licensed commercial software for instrument control.
By utilising `threading` and `multiprocessing` capabilities, PICA ensures that the entire hardware ecosystem functions seamlessly and as a single cohesive unit. This allows the system to perform automated protocols, including temperature-dependent wide range resistance measurement (10<sup>-8</sup> - 10<sup>16</sup> Ω), current voltage (I-V) characterisation,  capacitance characterisation, and pyroelectric current measurement, and orchestrates measurements under varying magnetic fields and temperatures without requiring physical reconfiguration of the measurement setups.

## 2. Design Philosophy & Architecture

PICA was constructed on a core philosophy of **robustness, modularity, and accessibility**, prioritizing open standards over proprietary "black box" solutions.

### 2.1 The Choice of Python
Python was selected as the foundational language for PICA due to its ubiquity in the scientific community:
* **Scientific Ecosystem:** Libraries like `NumPy` (array operations), `Pandas` (data structuring), and `Matplotlib` (publication-quality plotting) create a seamless workflow from acquisition to analysis.
* **PyVISA Integration:** The [`PyVISA`](https://github.com/pyvisa/pyvisa) library provides platform-independent wrappers for VISA drivers, allowing communication via simple, readable commands (e.g., ``instrument.query('*IDN?')``) rather than complex low-level protocols.
* **Cross-Platform:** PICA runs on Windows, Linux, and macOS with minimal modification, accommodating diverse lab environments.

### 2.2 The Case for GUIs
While early automation scripts often rely on Command Line Interfaces (CLIs), the final PICA suite prioritizes full-featured GUIs built with `Tkinter`. This strategic decision was guided by:
* **Error Prevention:** GUIs employ input validation and dropdown menus to restrict parameters to safe/logical ranges, preventing the "invalid command" errors common in CLI environments.
* **Real-Time Feedback:** Embedded `Matplotlib` plots provide immediate visualization of incoming data. This allows researchers to spot physical anomalies, noise, or connection issues instantly, potentially saving hours of wasted experimental time.
* **Workflow Visualization:** A visual interface helps new users and students mentally map the experimental workflow, reducing the learning curve.

### 2.3 Operational Transparency (No "Black Box")
To foster trust and reproducibility, PICA rejects the opaque nature of proprietary software. measurement module features an **Embedded Console Log**:
* **Status Streaming:** Displays a real-time stream of operations, such as "Ramping temperature to 300 K" or "Connecting to GPIB0::4::INSTR".
* **Immediate Diagnostics:** Instantly reports VISA timeouts or command errors, providing exact context for hardware failures rather than generic error codes.

### 2.4 Architecture: Process Isolation
PICA employs a multiprocess architecture using Python's `multiprocessing` library.
* **Frontend (GUI):** Handles user interaction and live plotting.
* **Backend (Logic):** Executes in a **separate, isolated process**.
This ensures that if a measurement script hangs due to a hardware timeout, it does not crash the main application, preserving the stability of the suite and other concurrent tasks.

### 2.5 Self-Contained and Modular Programs
In PICA, each program is designed to be self-contained. This architectural choice reduces dependency chains and mitigates the excessive abstraction layers that can make other projects difficult to understand or modify. Each program (i.e., each Python file or module) can operate independently, thereby facilitating debugging and maintenance.

This approach, however, leads to a considerable degree of code repetition because there are relatively few abstractions. Nevertheless, this trade-off can be advantageous in experimental environments, where the primary objectives are comprehensibility, maintainability, usability, and stability over extended periods, rather than strict adherence to principles of code elegance or minimal redundancy. In such contexts, scripts may remain functional without requiring updates for many years.

## 3. Installation & Setup

### 3.1 System Prerequisites

#### System Requirements & Compatibility

**Supported Platform:** Windows 10 / 11
**Architecture:** x86_64

:::{important}
**Windows Only**
PICA is currently designed and validated exclusively for Windows environments.
Linux and macOS are **not currently supported** due to dependencies on Windows-specific GUI libraries and font rendering.
Attempting to run this software on non-Windows platforms may result in crashes or UI failures. Linux support is experimental for now.
:::

#### Software Dependencies
1.  **Python 3.10+**: The core execution environment.
2.  **Dependencies:** Install via `pip install -r requirements.txt`.

:::{warning}
**A VISA Backend is Required:** [`PyVISA`](https://github.com/pyvisa/pyvisa) is a Python wrapper, not a driver. For PICA to communicate with hardware, you **must** install a VISA backend on your system first. If you attempt to run the software on a clean machine without a VISA implementation, it will fail to find the instruments. This is the most common failure point for new instrument control setups.

Choose one of the following:
- **NI-VISA:** The industry standard from National Instruments. Download and install it from the [NI website](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html#575764).
- **PyVISA-py:** A backend written in pure Python. It can be used as a fallback but may have limitations compared to vendor-specific drivers like NI-VISA. For `pyvisa-py` to discover all resources and avoid warnings (e.g., for TCPIP or HiSLIP instruments), `psutil` and `zeroconf` might be needed. These packages are already included in PICA's dependencies (`requirements.txt`). Note that for direct GPIB communication via `pyvisa-py`, a separate GPIB library (e.g., from your GPIB adapter vendor) might still be required. [PyVISA-py GitHub](https://github.com/pyvisa/pyvisa-py)

**Before proceeding, verify your VISA installation.** For more details and troubleshooting, refer to the "[Common Issues & Troubleshooting](#8-common-issues--troubleshooting)" section.
:::

### 3.2 Getting Started

PICA is structured as a standard Python package. The following instructions are for the supported Windows platform.

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

### 3.3 Running the Software

1.  **Graphical Launcher (Recommended)**
    The central dashboard for accessing all modules, the plotter, and the scanner.
    ```bash
    pica-gui
    ```

    The PICA Launcher provides a centralized dashboard for all measurement suites. While each module can be launched directly from its card, the launcher also provides a quick way to access the underlying scripts for editing. For developer convenience, each measurement suite card includes a folder icon (📁) in the top-right corner. Clicking this provides a shortcut to the module's Python scripts, allowing for rapid modifications.

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

:::{important}
**Template Scripts:** The instrument control modules (CLI's) provided are designed as **template scripts**. Users are expected to modify these scripts programmatically to adapt them to their specific experimental requirements and custom workflows. They are also excellent for developing **custom measurement protocols, sequences, and for learning instrument automation**. These scripts are typically named with an 'Instrument_Control' suffix to denote their programmatic nature. This approach ensures maximum flexibility and customization for advanced research applications.
:::

:::{note}
**Legacy CLI Notice:** The PICA CLI (`pica-cli`) is retained to support legacy headless workflows. While fully functional for specific protocols, this interface is **less frequently maintained** and may not support recent features available in the GUI.

We **strongly recommend** new users utilize the PICA GUI for the most complete and supported experience.
:::

### 3.4 Development Dependencies for Testing (Optional)

If you plan to contribute to PICA or run the test suite, you will need to install the development dependencies:

```bash
pip install -r requirements-dev.txt
```

How to Check Coverage Locally

To see the coverage percentage on your local machine, run this command instead:

```powershell
python -B -m pytest --cov=pica --cov-report=term-missing -p no:cacheprovider
```

#### Experimental Linux Instructions

:::{warning}
**Experimental Support:** The following instructions are for experimental purposes only. PICA is not officially supported on Linux (for now), and you will likely encounter functional or UI-related issues.
:::

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
    Follow the standard installation steps outlined in section [3.2 Getting Started](#32-getting-started). While the commands should run, be aware that the application GUI may not function correctly.

## 4. Safety Precautions

:::{warning}
**Safety Instructions:** Always switch off the instrument and verify that the output current, voltage, and any other relevant parameters are set to zero before modifying the connections to the Device Under Test (DUT). Failure to follow appropriate safety procedures may result in electric shock or other hazards. Adopt a safety-first approach at all times, and ensure that all instrument parameters remain within the specified safe operating limits defined either by the instrument manufacturer or by your measurement setup.
:::

## 5. Core Utilities

### 5.1 VISA Instrument Scanner

*File Reference: `pica/utils/GPIB_Instrument_Scanner_GUI.py`*

Automatically launched upon startup (and accessible within modules), this utility scans for connected hardware. It uses `ResourceManager.list_resources()` to find devices and sends a standard `*IDN?` query to verify communication. This allows users to verify their hardware configuration before starting any experiment. The VISA scanner also includes an address guide, which can be edited by the user for quick reference.

In the main PICA launcher, the VISA/GPIB scanner is configured to execute automatically at application startup. This design choice is motivated by the fact that initiating a measurement without first verifying the instrument connection is highly likely to fail and may result in non-informative error messages, such as a VISA connection timeout.

:::{figure} Images/screenshots/01_GPIB_Scanner.png
:alt: GPIB Scanner
:width: 300px
:align: center

The PICA Instrument Scanner utility, which automatically detects and identifies connected instruments.
:::

### 5.2 PICA Plotter Utility

*File Reference: `pica/utils/PlotterUtil_GUI.py`*

A standalone, multiprocessing-enabled tool for detailed data analysis. Unlike the minimalist embedded plots in the measurement modules, this utility allows:

  * **Comparative Analysis:** Overlaying multiple `.csv` or `.dat` files.
  * **Live Updates:** Monitoring active experiments by auto-refreshing data from disk.
  * **Flexible Axis Control:** Toggling linear/log scales to analyze data spanning orders of magnitude.

:::{figure} Images/screenshots/02_Plotter_Utility.png
:alt: Plotter Utility
:width: 600px
:align: center

The PICA Plotter Utility, a tool for data visualization and comparative analysis of multiple datasets including live plotting.
:::

### 5.3 Embedded Document Viewer

To ensure the software is self-contained (useful for offline lab computers), PICA includes an in-app viewer for project documentation, including this User Manual, the Instrument Manuals List, the License, and the Changelog.

### 5.4 Measurement Module Interface

Each measurement module consists of two primary windows: the control window on the left and the plotter window on the right. The dimensions of both windows are resizable. Upon launching a measurement module, it is recommended to first enlarge the control window, as it is required for configuring all experimental parameters. Typical settings include specifying the sample file name, selecting the file storage location, choosing the instrument address via a selection box, and defining voltage and temperature step sizes, delays, and other experimental parameters.

The control window also contains a console located below the parameter settings. This console can be scrolled and provides a continuous log of all operations executed by the software. The right-hand plot window is a simplified plotting interface that can display one, two, or three plots, depending on the specific module. The plots are updated in real time, and some modules allow switching the axes to a logarithmic scale to improve data visualization. The plotting interface is intentionally kept minimalistic, with limited functionality, to reduce unnecessary user interaction with the measurement setup, and maintaining the measurement program in a simplified form by omitting nonessential features.

Above the plot area, there are two buttons providing access to the [VISA Instrument Scanner](#51-visa-instrument-scanner) and [PICA Plotter Utility](#52-pica-plotter-utility). These utilities are accessible from all modules to facilitate rapid testing and diagnostics. The VISA/GPIB scanner allows the user to quickly verify whether instruments are properly connected and recognized by the system, while the plotter utility offers additional plotting capabilities beyond those available in the default plot window.


## 6. Supported Measurement Modules

The system is currently validated with industry-standard hardware, covering a resistance range spanning 24 orders of magnitude, 10<sup>-15</sup> resolution pyroelectric current measurements, and capacitance characterisation from 20 Hz - 2 MHz.

| Module | Configuration / Instrument | Use Case | Range |
| :--- | :--- | :--- | :--- |
| **Ultra Low Resistance Measurements** | **Keithley 6221** + **K2182** + Lakeshore 350/340 | Superconductors & metallic films; cancels thermal EMFs via AC Delta method. | 10 nΩ - 100 MΩ |
| **Mid-Resistance (Standard)** | **Keithley 2400** SourceMeter + Lakeshore 350/340 | Semiconductors, oxides, general transport. | 100 µΩ - 200 MΩ |
| **Mid-Resistance (High-Precision)** | **Keithley 2400** + **K2182** + Lakeshore 350/340 | Detecting subtle phase transitions. | 1 µΩ - 100 MΩ |
| **High-Resistance** | **Keithley 6517B** Electrometer + Lakeshore 350/340 | High bandgap materials, polymers, & ceramics. | 1 Ω - 10 PΩ |
| **Capacitance Analysis** | **Keysight E4980A** + Lakeshore 350/340 | C-V Analysis and Magnetocapacitance characterization. | 20 Hz - 2 MHz |
| **Broadband Dielectric** | **Novocontrol Alpha-AN** + ZG4 | Permittivity, electric modulus & AC conductivity (WinDETA-compatible). | 3 µHz - 20 MHz |
| **Pyroelectric** | **K6517B** + Lakeshore 350/340 | Current vs Temp (detecting Curie temperature). | 10<sup>-15</sup> A Resolution |

*While the current implementation drives specific instruments, the underlying framework is highly customizable. Researchers need only replace specific SCPI commands to utilize the suite with different models.*

PICA is designed to be as versatile, while being optimized for specific classes of instruments. The following modules represent the core capabilities of the suite, supporting a resistance scale spanning **24 orders of magnitude** (10 nΩ to 10 PΩ) depending on the hardware used.
Pyroelectric measurement performed using an electrometer enables highly sensitive characterization of ferroelectric phase transitions by detecting extremely small pyroelectric currents, with a resolution on the order of 10<sup>-15</sup> A. The
impedance analyzer enables the characterization of capacitance anomalies over the frequency range from 20 Hz to 2 MHz and is utilized for magnetocapacitance and photoinduced characterization across a wide variety of multiferroic systems. 

### 6.1 Ultra Low Resistance Measurements

**Target Hardware:** Keithley 6221 (Current Source) + K2182 (Nanovoltmeter).
**Typical Range:** 10 nΩ to 100 MΩ.

  * **Scientific Objective:** Ideal for superconductors, metallic films, and low-impedance devices. It actively cancels thermal offsets (Seebeck EMFs) generated in leads and contacts.
  * **Principle:** Uses the **AC Delta Method**.

:::{note}
**Understanding "Delta Mode":** The term "Delta Mode" refers specifically to a technique used by Keithley Models 6220 and 6221 Current Sources in conjunction with the Model 2182/2182A Nanovoltmeter for very low resistance measurements. This method is described in detail in the [Keithley Low Level Measurements Handbook](https://www.tek.com/en/documents/product-article/keithley-low-level-measurements-handbook---7th-edition). In this documentation, "Ultra Low Resistance Measurements" is used as the general scientific term, while "Delta Mode" may appear when specifically referencing the Keithley-specific method or program files.

1. Source +I, measure V1.
2. Source -I, measure V2.
3. Compute V\_corr = (V1 - V2) / 2.

The software synchronizes the source and voltmeter via a **hardware trigger link (RS-232)** for microsecond-level timing.
:::

:::{figure} Images/screenshots/K6221_IV_Sweep.png
:alt: Delta Mode IV Sweep
:width: 600px
:align: center

I-V sweep measurement using the Sweep Mode, designed for low-resistance measurements with a Keithley 6221 and 2182.
:::

:::{figure} Images/screenshots/K6221_RT_Control.png
:alt: Delta Mode RT Control
:width: 600px
:align: center

Ultra Low Resistance Measurement R-T measurement with active temperature control, using a Keithley 6221, K2182, and a temperature controller.
:::

:::{figure} Images/screenshots/K6221_RT_Sensing.png
:alt: Delta Mode RT Sensing
:width: 600px
:align: center

Ultra Low Resistance Measurement R-T measurement in sensing mode, where the system logs resistance and temperature while an external system manages temperature.
:::

### 6.2 General Transport (Standard I-V & R-T)

**Target Hardware:** Keithley 2400 SourceMeter (SMU).
**Typical Range:** 100 µΩ to 200 MΩ.

  * **Scientific Objective:** General transport characterization for semiconductors, oxides, and devices.
  * **Capabilities:**
      * **I-V Sweep:** Linear sweeps, hysteresis loops, or custom current lists.
      * **R-T Active Control:** Applies constant DC current while coordinating with a temperature controller (e.g., Lake Shore 350) to ramp temperature.

:::{figure} Images/screenshots/K2400_IV_Sweep.png
:alt: K2400 IV Sweep
:width: 600px
:align: center

A standard I-V sweep performed with a Keithley 2400 SourceMeter, suitable for general-purpose device and sample characterization.
:::

:::{figure} Images/screenshots/K2400_RT_Control.png
:alt: K2400 RT Control
:width: 600px
:align: center

Resistance-Temperature (R-T) measurement with active temperature control, using a Keithley 2400 and a Lakeshore 350 controller.
:::

:::{figure} Images/screenshots/K2400_RT_Sensing.png
:alt: K2400 RT Sensing
:width: 600px
:align: center

Resistance-Temperature (R-T) measurement in sensing mode, where the system logs resistance and temperature while an external system/controller manages temperature.
:::

### 6.3 High Precision Transport (mid resistance range)

**Target Hardware:** Keithley 2400 (Source) + K2182 (Nanovoltmeter).
**Typical Range:** 1 µΩ to 100 MΩ.

  * **Scientific Objective:** Detects subtle phase transitions in semiconductors and oxides where standard SMU resolution is insufficient.
  * **Advantage:** Combines the stable sourcing of the SMU with the nanovolt-level sensitivity of a dedicated voltmeter, utilizing a true 4-wire configuration to eliminate lead resistance errors.

:::{figure} Images/screenshots/K2400_2182_IV.png
:alt: K2400_2182 IV
:width: 600px
:align: center

High-precision I-V characterization using a Keithley 2400 as a current source and a Keithley 2182 nanovoltmeter for sensitive mid-range resistance measurements.
:::

:::{figure} Images/screenshots/K2400_2182_RT_Control.png
:alt: K2400 2182 RT Control
:width: 600px
:align: center

High-precision R-T measurement with active temperature control, combining the K2400, K2182, and a L350 temperature controller.
:::

:::{figure} Images/screenshots/K2400_2182_RT_Sensing.png
:alt: K2400 2182 RT Sensing
:width: 600px
:align: center

High-precision R-T measurement in sensing mode, leveraging the K2400 and K2182 for enhanced accuracy for mid-range resistance measurements.
:::

### 6.4 Electrometry & High Resistance

**Target Hardware:** Keithley 6517B Electrometer (or compatible High-R meter).
**Typical Range:** 1 Ω to 10 PΩ (10<sup>16</sup> Ω).

  * **Scientific Objective:** Characterization of capacitances, polymers, and ceramics (Electrometry).
  * **Principle (Voltage Driven):** Applies a high voltage and measures the resulting leakage current (pA/fA range).
  * **Note:** PICA manages instrument settling times, allowing for a necessary initial delay for the system to stabilize. This is crucial in high-impedance setups to ensure steady-state ohmic currents are accurately recorded.

**A screencast demonstrating the high resistance IV module is available at [this link](https://drive.google.com/file/d/13W-Z4N-08t9m0xxuR30sjTLmUVG1VyQd/view?usp=sharing).**

:::{figure} Images/screenshots/K6517B_IV.png
:alt: K6517B IV
:width: 600px
:align: center

High-resistance I-V measurement performed with a Keithley 6517B Electrometer, designed for characterizing insulating materials.
:::

:::{figure} Images/screenshots/K6517B_RT_Control.png
:alt: K6517B RT Control
:width: 600px
:align: center

High-resistance R-T measurement with active temperature control using a Keithley 6517B.
:::

:::{figure} Images/screenshots/K6517B_RT_Sensing.png
:alt: K6517B RT Sensing
:width: 600px
:align: center

High-resistance R-T measurement in passive sensing mode using a Keithley 6517B.
:::

### 6.5 Pyroelectric Current Measurements

**Target Hardware:** Keithley 6517B Electrometer + Temperature Controller.
**Sensitivity:** Down to 1 fA (10<sup>-15</sup> A).

This module automates the measurement of pyroelectric currents (Ip) as a function of temperature, commonly used to characterize ferroelectric phase transitions and identify **Curie Temperatures** (Tc).

  * **Workflow:**
    1.  **Poling (Optional):** Apply bias field while cooling.
    2.  **Heating:** Remove bias; heat sample at a linear rate.
    3.  **Measurement:** Record the depolarization current peak indicative of phase transition.
  * **Best Practice:** For measurements in the fA range, ensure your setup utilises proper shielding (e.g., double-layer Faraday cage).

:::{figure} Images/screenshots/Pyroelectric_Current.png
:alt: Pyroelectric Current
:width: 600px
:align: center

Pyroelectric current measurement as a function of temperature, captured with a Keithley 6517B to identify ferroelectric phase transitions via measuring pyroelectric current.
:::

### 6.6 High Voltage Poling

**Target Hardware:** Keithley 6517B (Voltage Source).
**Capabilities:** High Voltage Sourcing.

This utility provides a dedicated interface for **In-situ and ex-situ electrical poling** of materials.

  * **Objective:** Establish a uniform ferroelectric polarization state in samples before characterization.
  * **Applications:** Preparing samples for pyroelectric current measurements, converse magnetoelectric studies, and ex-situ neutron diffraction studies on poled materials.

### 6.7 Dielectric Spectroscopy

**Target Hardware:** Keysight E4980A Precision LCR Meter.
**Frequency Range:** 20 Hz to 2 MHz
  * **Temperature Range:** 5 K – 380 K

  *   **Scientific Objective:** Measures Capacitance (C) and Loss Tangent (tan delta) as a function of frequency or DC bias voltage (C-V Analysis).

:::{figure} Images/screenshots/Keysight_CV.png
:alt: Keysight CV
:width: 600px
:align: center

Capacitance-Voltage (C-V) characterization of a device or a sample using a Keysight E4980A LCR meter.
:::

### 6.8 Broadband Dielectric Spectroscopy

**Target Hardware:** Novocontrol Alpha-AN Impedance Analyzer with a ZG4 sample interface, over direct GPIB.
**Frequency Range:** 3 µHz to 20 MHz (this module sweeps a fixed 20 Hz – 1 MHz logarithmic series, 115 points).

  *   **Scientific Objective:** Measures the complex permittivity (ε', ε''), electric modulus (M', M'') and AC conductivity (σ', σ'') of a dielectric sample at fixed temperature. Output is written in a **WinDETA-compatible** format so it opens directly in Novocontrol's WinFIT analysis tool, alongside a PICA `.dat` for the built-in Plotter.

  *   **Reference Calibration:** Use the dedicated **Run REF Calibration** button before a measurement session. It must run with the **sample disconnected** from the ZG4 (the GUI prompts you). Calibration data is stored inside the mainframe and survives resets, so a sweep runs on the most recent calibration; the GUI logs the calibration's age but never blocks on it.

:::{note}
This mainframe has **no DC bias hardware** — the module never transmits a bias command. A stale reference calibration shows up as a step or kink in ε'(f) at a reference-capacitor switch boundary rather than as uniform noise; re-run the REF calibration if you see one.
:::

### 6.9 Standalone Temperature Utilities

**Target Hardware:** Lake Shore 350 Temperature Controller.

PICA also includes standalone utilities for monitoring and controlling temperature, independent of other measurement modules.

  * **Temperature Monitor:** A simple interface for logging temperature from multiple types of sensors.
  * **Temperature Control:** A dedicated module for setting temperature ramps, controlling heater outputs, and managing control loops.
  * **Step-wise Control (Basic):** A step-sequence controller that ramps to each setpoint, waits for stabilization, and hands off to an external measurement.
  * **Step-wise Control (Advanced)** *(new in v1.0.5)*: A self-contained advanced version of the step-sequence controller. It adds an **adaptive ramp rate** (computed per step and hard-capped by an editable low-temperature rate table, taming overshoot on LN2-dewar probes below 100 K), an optional **approach-from-one-side** mode for hysteresis-sensitive measurements, configurable **stability criteria** (tolerance band, rolling window, drift limit, timeout), and per-setpoint summary logging alongside safety features such as a hard kill-switch temperature and a soft Max-Temp limit with graceful abort.

:::{figure} Images/screenshots/Lakeshore_Temp_Monitor.png
:alt: Lakeshore Temp Monitor
:width: 600px
:align: center

The standalone Temperature Monitor utility, used for logging data from a Lakeshore 350 controller.
:::

:::{figure} Images/screenshots/Lakeshore_Temp_Control.png
:alt: Lakeshore Temp Control
:width: 600px
:align: center

The standalone Temperature Control utility, providing a dedicated interface for managing temperature ramps and heater outputs on a Lakeshore 350.
:::

## 7. Releases and Versions

For downloadable release builds, please visit the [Releases page](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases).

## 8. Common Issues & Troubleshooting

This section covers the most common issues encountered when using PICA.

### 8.1 VISA Timeout Error or Resource Not Found

This is the most frequent issue and usually indicates a problem with the connection between the computer and the instrument. Follow these steps to resolve it:

1.  **Check Physical Connections:** Ensure all cables (GPIB, USB, Ethernet) are securely connected to both the instrument and the computer.
2.  **Use the VISA Scanner:** Run the **VISA Instrument Scanner** utility from the PICA launcher.
    *   If the instrument appears in the list, the connection is working. Note the correct VISA address.
    *   If the instrument does **not** appear, PICA cannot see it. Proceed to the next steps.
3.  **Power Cycle the Instrument:** Turn the instrument off, wait a few seconds, and turn it back on. This can often resolve temporary communication hangs.
4.  **Restart the Computer:** If the problem persists, a full restart can resolve driver or backend issues.
    *   Shut down the computer completely, leaving the instrument turned off.
    *   Start the computer.
    *   Once the system is fully booted, run the PICA VISA scanner.
    *   Turn on the instrument.
5.  **Check Drivers and Communication Mode:**
    *   Ensure you have the correct VISA backend installed (see the `A VISA Backend is Required` warning in the [Installation & Setup](#3-installation--setup) section).
    *   If using a different communication interface (e.g., switching from GPIB to USB), verify that the necessary drivers are installed and that the instrument is configured for that mode.

### 8.2 Instrument Control and Delays
An important parameter to consider during concurrent control of instruments is the delay. The time between each step should be sufficient to ensure that all instruments (whether two or three) have completed their commanded actions. Sending a new command before an instrument has had time to process the previous one will definitely cause errors. It is also important to introduce proper delays for the system to reach equilibrium. Furthermore, during the initial setup, instruments should be given adequate delay time for all their internal components to stabilize and enter a ready state. In PICA, sufficient internal delays are provided in all modules. However, it was observed that some systems might need more delay time. Therefore, a parameter for initial delay is available in those modules' GUI. Users should provide an appropriate initial delay time. This initial delay time parameter is in addition to the basic delay already contained in the module.

### 8.3 Forcing a Re-installation for Debugging
You can force a clean re-installation of the package from the source directory.

This command removes the old installation and replaces it with a fresh build.
```bash
pip install . --force-reinstall
```

## 9. Technical Reference

### 9.1 File Naming Convention

To ensure data integrity and easy sorting, PICA automatically generates filenames using a standardized format. This allows for easier parsing by external analysis tools.
Format: `[SampleName]_[Timestamp]_[Identifier].dat`
Example: `SampleA_2025-12-04_1430_IV_Sweep.dat`

### 9.2 GPIB Address Guide

PICA uses standard VISA resource strings. While the defaults below are common, users should verify their specific instrument addresses using the built-in **Instrument Scanner** or front-panel settings.

  * **Lake Shore 350:** `GPIB1::15::INSTR`
  * **Keithley 2400:** `GPIB1::4::INSTR`
  * **Keithley 6221:** `GPIB0::13::INSTR`
  * **Keithley 2182:** `GPIB0::7::INSTR`
  * **Keithley 6517B:** `GPIB1::27::INSTR`
  * **Keysight E4980A:** `GPIB0::17::INSTR`
  * **Novocontrol Alpha-AN:** `GPIB0::5::INSTR`

### 9.3 Repository Mirroring

This project is manually backed up weekly to a [GitLab repository](https://gitlab.com/prathameshnium/pica-python-instrument-control-and-automation).

### 9.4 Data File Format

The data file includes a commented header line (starting with `#`) that contains metadata, such as the sample name. This is followed by a header row with column names and then the data rows.

This structure makes it easy to import the data into various analysis programs like Origin, or to parse it programmatically with libraries like Pandas.

**Example: Ultra Low Resistance (I-V) Module**

```text
# Sample: Delta_Test_10ohm
Set Current (A),Measured Voltage (V),Resistance (Ohm)
-1.000000e-05,-1.037917e-04,1.037917e+01
-9.797980e-06,-1.017006e-04,1.037975e+01
```
Most other data files generated by PICA follow a similar structure.

## 10. Citation & Open Source

**Collaborative Ecosystem:**
PICA is open-source ([MIT License](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/LICENSE)) to foster transparency. By providing the source code, the measurement protocols become auditable, ensuring that experimental conditions are reproducible and not hidden behind a proprietary "black box." We encourage other research groups to adapt these scripts for their specific hardware configurations.

For the full preprint, please refer to the [Preprint section](#101-preprint).

### 10.1 Preprint

The full preprint of the PICA software suite, detailing its design, implementation, and applications, is available for review.

*   **Preprint Page:** [https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/)
*   **Direct PDF Download:** [https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/pica-paper.pdf](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/pica-paper.pdf) (alternate link at [Academia.edu](https://www.academia.edu/165176821/PICA_Advanced_High_Precision_Transport_Measurement_Automation_with_Python))


**Citation:**

```bibtex
@software{Deshmukh_PICA_2026,
  author       = {Deshmukh, Prathamesh Keshao and Mukherjee, Sudip},
  title        = {{PICA: Advanced High-Precision Transport Measurement Automation with Python}},
  month        = jan,
  day          = 26,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {1.0.5},
  url          = {https://doi.org/10.5281/zenodo.18377217}
}
```

## 11. Future Development

### 11.1 AC Resistivity (Lock-In)

*Status: Under Development*

  * **Instruments:** Keithley 6221 (AC Source) + SRS SR830 (DSP Lock-In Amplifier).
  * **Predicted Resistance Range:** \~ 20 nΩ to 1 MΩ.
  * **Scientific Objective:** Probes frequency-dependent transport phenomena.
  * **Use Case:** Useful for distinguishing between different conduction mechanisms by analyzing the frequency response of the sample's resistance.
  * **Workflow:** The Keithley 6221 provides a precise AC excitation current, while the Lock-In Amplifier (SR830) extracts the signal amplitude and phase with high noise rejection, allowing for accurate ac resistivity measurements.

### 11.2 Standalone Executables

In the future, we also plan to develop executable (`.exe`) versions of the PICA software suite. This will remove the need for users to manage Python environments and dependencies, further simplifying the setup process and facilitating rapid adoption in laboratories.

### 11.3 New Utilities and Analysis Modules
We plan to add more utility modules, such as a PID simulator for temperature controller PID values calibration and various simple data analysis modules. These additions will help to streamline the entire process from measurement to analysis, making PICA a more self-contained ecosystem.


## 12. Adding a New Instrument

The procedure for adding a new instrument module to PICA is described in the [CONTRIBUTING.md](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/CONTRIBUTING.md#adding-a-new-instrument-module) file. Please refer to that guide for detailed, step-by-step instructions.


## 13. Authors & Acknowledgments

:::{figure} LOGO/UGC_DAE_CSR_NBG.jpeg
:alt: UGC DAE CSR Logo
:width: 150px
:align: center
:::

- **Lead Developer:** [**Prathamesh Deshmukh**](https://www.researchgate.net/profile/Prathamesh-Deshmukh-6)
- **Principal Investigator:** [**Dr. Sudip Mukherjee**](https://www.csr.res.in/Faculty/profile/889/893/Dr.SudipMukherjee)
- **Affiliation:** [UGC DAE Consortium for Scientific Research, Mumbai Centre](https://www.csr.res.in/Mumbai_Centre), Bhabha Atomic Research Centre, Mumbai, 400 085, Maharashtra, India

### Funding
Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF).

## 14. License

This project is licensed under the MIT License - see the [LICENSE](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/LICENSE) file for details.

## 15. Appendix A: Project File Structure

For developers and advanced users, the following reference outlines the PICA directory structure (abridged, v1.0.5).

:::{note}
Adding a new module to the main launcher into the GUI requires modifying `pica/main.py`.
:::

```text
PICA (Root Directory)/
    .coveragerc
    .gitignore
    CHANGELOG.md
    CITATION.cff
    CODE_OF_CONDUCT.md
    CONTRIBUTING.md
    LICENSE
    MANIFEST.in
    README.md
    pica_cli.py
    pyproject.toml
    requirements-dev.txt
    requirements.txt
    run_pica.py
    .github/
        workflows/
            codeql.yml
            draft-pdf.yml
            lint.yml
            test.yml
    docs/
        Instruments_Manuals_Lists.md
        User_Manual.md
    examples/
        examples.md
    paper/
        paper.bib
        paper.md
    pica/
        __init__.py
        cli.py
        main.py
        assets/                 <-- Images, Logos, Icons
            Images/
            LOGO/
        keithley/
            delta_mode/         <-- Low Resistance (K6221 + K2182)
                Delta_RT_K6221_K2182_L350_Sensing_GUI.py
                Delta_RT_K6221_K2182_L350_T_Control_GUI.py
                IV_K6221_DC_Sweep_GUI.py
                Instrument_Control/
            k2400/              <-- Mid Resistance (K2400 Standard)
                IV_K2400_GUI.py
                RT_K2400_L350_T_Control_GUI.py
                RT_K2400_L350_T_Sensing_GUI.py
                Instrument_Control/
            k2400_2182/         <-- Mid Resistance (High Precision)
                IV_K2400_K2182_GUI.py
                RT_K2400_K2182_L350_T_Sensing_GUI.py
                RT_K2400_K2182_T_Control_GUI.py
                Instrument_Control/
            k6517b/             <-- High Resistance & Pyroelectric
                High_Resistance/
                    IV_K6517B_GUI.py
                    RT_K6517B_L350_T_Control_GUI.py
                    RT_K6517B_L350_T_Sensing_GUI.py
                    Instrument_Control/
                Pyroelectricity/
                    Pyroelectric_K6517B_L350_GUI.py
                    Instrument_Control/
        keysight/               <-- Capacitance (E4980A)
            CV_KE4980A_GUI.py
            Instrument_Control/
        novocontrol/            <-- Broadband Dielectric (Alpha-AN)
            Frequency_Scan_AlphaAN_GUI.py
            Instrument_Control/
        lakeshore/              <-- Temperature Control
            T_Control_L350_RangeControl_GUI.py
            T_Sensing_L350_GUI.py
            Instrument_Control/
        lockin/                 <-- Lock-in Amplifiers (Experimental)
            BasicTest_S830_Instrument_Control.py
        utils/                  <-- Core Utilities
            GPIB_Instrument_Scanner_GUI.py
            GUI_Basic_Format.py
            PlotterUtil_GUI.py
            parser.py
    tests/                      <-- Automated Test Suite
        conftest.py
        test_backends_logic.py
        test_deep_simulation.py
        test_entry_points.py
        test_full_stack_simulation.py
        test_gui_layouts.py
        test_gui_modules_initialization.py
        test_package_integrity.py
        test_pica_launcher.py
        test_utilities_logic.py
```