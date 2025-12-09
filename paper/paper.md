---
title: 'PICA: A Python-based Instrument Control and Automation Suite for Material Characterisation'
tags:
  - python
  - hardware control
  - automation
  - pyvisa
  - condensed matter physics
  - cryogenics
  - scpi
authors:
  - name: Prathamesh Deshmukh
    orcid: 0009-0008-3278-0837
    affiliation: "1,2"
  - name: Sudip Mukherjee
    orcid: 0000-0003-4734-9157
    affiliation: "1,2"
    corresponding: true
    email: sudipm@csr.res.in
affiliations:
 - name: UGC-DAE Consortium for Scientific Research, Mumbai Centre, India
   index: 1
 - name: Savitribai Phule Pune University, Pune, India
   index: 2
date: 8 December 2025
bibliography: paper.bib

---

# Summary

PICA (Python-based Instrument Control and Automation) is a modular, open-source software suite designed to automate complex characterisation experiments in condensed matter physics. PICA is designed as a versatile framework capable of operating on any standard laboratory workstation. 
It provides an extensible unified graphical user interface (GUI) for orchestrating high-precision instruments, specifically source-measure units, nanovoltmeters, temperature controllers, and LCR metres. 

The suite supports characterisation across a vast resistance range of 24 orders of magnitude (10 nΩ to 10 PΩ) and orchestrates measurements under varying magnetic fields and temperatures.
Python was chosen for its free, open source nature, cross-platform capabilities, and extensive ecosystem of over 100,000 packages, offering a powerful, community-supported alternative to licensed commercial software for instrument control. 

By utilising threading and multiprocessing capabilities, PICA ensures that the entire hardware ecosystem functions as a single cohesive unit. This allows the system to perform automated protocols, including temperature-dependent resistivity current voltage (I-V) characterisation and pyroelectric current measurement, without requiring physical reconfiguration of the measurement setups.


# Statement of need

Advancements in experimental physics depend on the precise characterisation of material properties under extreme physical conditions. Researchers have to choose between expensive proprietary visual programming software or developing a custom measurement script from scratch.

While powerful ecosystem libraries such as PyVISA [@grecco2023pyvisa] and PyMeasure [@pymeasure_2025] provide the foundational drivers for instrumental communication, they are fundamentally software libraries that require the user to write and maintain code. This creates a technical barrier for researchers who lack programming expertise. PICA addresses this gap by functioning as a turnkey application rather than a library. It offers a "ready-to-run" graphical interface that abstracts the underlying control logic, allowing experimentalists to focus on data acquisition without needing to develop custom software scripts for the supported hardware configurations.

The software was specifically developed to control a multifunctional, custom-designed probe inserted into the Physical Property Measurement System (PPMS), enabling fully user-defined measurement protocols across the temperature range of 5 K to 350 K. It eliminates the need to alter the measurement setup for full characterisation, handling everything from Delta mode (low resistance) to high impedance electrometry. The primary goal of PICA was to facilitate the precise characterisation of materials ranging from low-noise superconductors to high-band gap insulators (covering 24 orders of magnitude in resistance) using a single unified framework.

The system is currently validated with industry-standard hardware, including the Keithley 6221 Current Source, Keithley 2182 Nanovoltmeter, and Keithley 6517B Electrometer.

While the current implementation drives specific instruments, the underlying framework is universal. Researchers using different hardware models need only replace the specific SCPI commands with their instrument equivalent commands to utilise the suite.

It differentiates itself through the following unique features:

* **Accessibility:** A professional dashboard that allows researchers without coding experience to configure and run a complex measurement protocol immediately using the suite's pre-packaged measurement modules.

* **Physical Validation:** PICA's protocols are actively used for cryogenic transport measurements (80K - 320K) at the UGC DAE Consortium for Scientific Research, Mumbai Centre, validating the software's core architecture in a real-world research environment and providing a stable, tested foundation for the university and researchers to build upon.

* **Process Isolation:** PICA deploys a `multiprocessing` architecture that runs instrumentation control logic in an isolated process. This ensures that hardware timeouts or driver crashes do not freeze the main dashboard, a common issue in single-threaded Python scripts.

*  **Modular CLI Architecture:** As demonstrated in the repository, measurement modules also contain CLI measurement module counterparts that allow researchers to utilise PICA's measurement protocol and logic for headless automation or integration into other workflows without GUI overhead.

*  **Operational Transparency:** Unlike a black box solution, PICA exposes real-time command logs, aiding in debugging and ensuring scientific reproducibility.

*  **Open Source Extensibility:** PICA's modular design allows researchers to easily integrate new instrument drivers or experimental protocols by subclassing existing templates, fostering a community-driven ecosystem for instrument control. This ensures that the software remains adaptable, allowing researchers to extend support for their unique instrument configurations.



# Design and Implementation

PICA is built on a modular architecture characterised by self-contained modules, ensuring future extensibility. This design allows individual measurement protocols to be modified independently or added without impacting the core system stability.


### Process Isolation and Concurrency 

Unlike simple script-based automation, PICA decouples the User Interface (UI) from the instrumentation control logic. It utilises Python's standard 'multiprocessing' libraries to spawn isolated processes for measurement tasks.

* **Stability:** If an instrument hangs or a communication bus times out, the isolated process can be terminated safely without freezing the main  GUI or losing previous data.

* **Responsiveness:** The `tkinter`-based frontend remains responsive for live data plotting (using `matplotlib` [@hunter2007matplotlib] with blitting) even while the backend waits for hardware triggers. Numpy [@harris2020numpy] is utilised throughout this pipeline for efficient array manipulation and data validation during real-time updates.

* **Data Integrity:** Experimental data integrity is prioritised through a "write on acquisition" strategy. Data is structured using `pandas` [@pandas2025] and is saved to a CSV file immediately after every acquisition point, preventing data loss in the event of a power failure or program/system crash.

### Hardware Abstraction Layer

PICA utilises **PyVISA** [@grecco2023pyvisa] to abstract the low-level communication protocols (GPIB, USB, Ethernet). The software implements a strict initialisation routine:

1. **Connection Verification:** A built-in "VISA Instrument Scanner" queries the bus (`*IDN?`) to map the connected instrument addresses.

2.  **Instrument Reset Protocol:** To eliminate the influence of all previous experiments, all stored data, cache in buffers, and existing settings or configurations are explicitly reset, thereby providing a clean initial state before each measurement.

3.  **Graceful Shutdown:** A "Safety Shutdown Routine" logic ensures that sources are ramped down to zero and heaters are disabled safely, even if the software is interrupted unexpectedly.


### Operational Transparency

To support the scientific reproducibility of experimental results, PICA rejects hidden automation and replaces the "black box" paradigm with real-time console logs. Each measurement module has a console that records time-stamped actions (e.g., `[10:05:25] Keithley 6221: Ramping current to 10 mA`), showing every command sent to the instrument. This allows researchers to verify measurement protocols and troubleshoot hardware instantly. 

### Testing and Simulation

To ensure measurement reliability, all of these modules were thoroughly tested with the corresponding hardware. Additionally, to facilitate development without constant access to physical instruments, PICA includes a testing suite that uses `pytest`. The suite employs `unittest.mock` to simulate VISA resources, allowing for the verification of backend logic streams, class structure, and command sequences in a continuous integration environment.


# Acknowledgements

We acknowledge the financial support provided under the SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science and Technology (DST), Government of India.


# References
