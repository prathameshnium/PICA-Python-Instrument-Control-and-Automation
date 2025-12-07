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
date: 27 November 2025
bibliography: paper.bib

---

# Summary

PICA (Python-based Instrument Control and Automation) is a modular, open-source software suite designed to automate complex characterisation experiments in condensed matter physics. Developed to operate a custom, laboratory-built measurement system, PICA provides a unified graphical user interface (GUI) for orchestrating high-precision instruments, specifically Keithley SourceMeters/Nanovoltmeters, Lakeshore Temperature Controllers, and Keysight LCR metres. The suite controls temperature setpoints to perform automated protocols, including temperature-dependent resistivity, current-voltage (I-V) characteristics, and pyroelectric current measurement.

# Statement of need

Advancements in experimental physics depend on the precise characterisation of material properties under extreme physical conditions. Researchers have to choose between expensive proprietary software like LabVIEW or developing a custom measurement script from scratch. While Python libraries such as PyVISA [@grecco2023pyvisa] and PyMeasure [pymeasure_2025] provide excellent low-level driver support, they primarily function as developer toolkits. They often require significant boilerplate code to handle data visualisation and error handling, effectively forcing physicists to become software engineers.
PICA fills this niche by providing a **"lab-ready" application layer** built on top of these libraries. It differentiates itself through the following unique features:
* **Accessibility:** A professional dashboard that allows researchers without coding experience to configure and run a complex measurement protocol immediately.
* **Physical Validation:** Unlike general-purpose drivers, PICA's protocols are actively used for cryogenic transport measurements (80K - 320K) at the UGC DAE Consortium for Scientific Research, Mumbai Centre, ensuring that the protocols are physically accurate and lab tested.
* **Process Isolation:** PICA deploys a `multiprocessing` architecture that runs instrumentation control logic in an isolated process. This ensures that hardware timeouts or driver crashes do not freeze the main dashboard, which is a common problem in single-threaded Python scripts.
*  **Modular CLI Architecture:** As demonstrated in the repository, measurement modules also contain a CLI measurement module that allows researchers to utilise PICA's measurement protocol and logic for headless automation or integration into other workflows without GUI overhead.
*  **Operational Transparency:** Unlike a Black box solution, PICA exposes the real-time command logs, aiding in debugging and ensuring scientific reproducibility.


# Design and Implementation

PICA is built on a modular architecture characterised by self-contained modules, ensuring future extensibility. This design allows individual measurement protocols to be modified independently or added without impacting the core system stability.

![The PICA Launcher Dashboard.](pica/assets/Images/PICA_Laucher.png)

### Process Isolation and Concurrency 

Unlike simple script-based automation, PICA decouples the User Interface (UI) from the instrumentation control logic. It utilises Python's standard 'multiprocessing' libraries to spawn isolated processes for measurement tasks.
* **Stability:** If an instrument hangs or a communication bus times out, the isolated process can be terminated safely without freezing the main  GUI or losing previous data.
* **Responsiveness:** The `tkinter`-based frontend remains responsive for live data plotting (using `matplotlib` [@hunter2007matplotlib] with blitting) even while the backend waits for hardware triggers. Numpy [@harris2020numpy] is utilised throughout this pipeline for efficient array manipulation and data validation during the real-time updates.
* **Data Integrity:** Experimental data integrity is prioritised through a "write on acquisition" strategy. Data is structured using `pandas` [@pandas2025] and is saved to a CSV file immediately after every acquisition point, preventing data loss in the event of a power failure or program/system crash.

### Hardware Abstraction Layer

PICA utilises **PyVISA** [@grecco2023pyvisa] to abstract the low-level communication protocols (GPIB, USB, Ethernet). The software implements a strict initialisation routine:
1. **Connection Verification:** A built-in "VISA Instrument Scanner" queries the bus (`*IDN?`) to map the connected instrument addresses.
2.  **State Sanitization:** To eliminate the influence of all previous experiments, any stored data, cache in buffers, and existing settings or configurations, the instruments are explicitly reset, thereby providing a clean initial state before each measurement.
3.  **Graceful Shutdown:** A "Safety Shutdown Routine" logic ensures that sources are ramped down to zero and heaters are disabled safely, even if the software is interrupted unexpectedly.

### Operational Transparency

To support the scientific reproducibility of experimental results, PICA rejects hidden automation and replaces the "black box" paradigm with real-time console logs. Each measurement module has a console that records time-stamped actions (e.g., `[10:05:25] Keithley 6221: Ramping current to 10 mA`), showing every command sent to the instrument. This allows researchers to verify measurement protocols and troubleshoot hardware instantly. 

### Testing and Simulation

To ensure measurement reliability, all of these modules were thoroughly tested with the corresponding hardware. Additionally, to facilitate development without constant access to physical instruments, PICA includes a testing suite that uses `pytest`. The suite employs `unittest.mock` to simulate VISA resources, allowing for the verification of backend logic streams, class structure, and command sequences in a continuous integration environment.


# Acknowledgements

We acknowledge the financial support provided under the SERB-GRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science and Technology (DST), Government of India.
# References
