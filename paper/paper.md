---
title: 'PICA: Advanced Python Suite for High Precision Instrumentation and Transport Measurement Automation'
tags:
  - python
  - hardware control
  - automation
  - pyvisa
  - condensed matter physics
  - cryogenics
  - scpi
  - instrumentation
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
date: 10 December 2025
bibliography: paper.bib

---

# Summary

High-precision measurements are essential for advancing research in spintronics and materials characterization. To enable such progress, highly precise and accurate automation software is required.PICA (Python-based Instrument Control and Automation) is a modular, open-source software suite designed to automate advanced transport measurements for electronic devices and chemical samples. PICA is designed as a versatile framework capable of operating on any standard laboratory workstation. 
It provides an extensible, unified graphical user interface (GUI) for orchestrating high-precision instruments, specifically current source (DC/AC) units, nanovoltmeters, high resistance electrometers, impedance analyser, and temperature controllers. Built on the robust Python scientific ecosystem, PICA leverages community standard libraries as an alternative to licenced commercial software for instrument control.
By utilising `threading` and `multiprocessing` capabilities, PICA ensures that the entire hardware ecosystem functions seamlessly and as a single cohesive unit. This allows the system to perform automated protocols, including temperature-dependent wide range resistance measurement ($10^{-8}$ - $10^{16}$ Ω), current voltage (I-V) characterisation,  capacitance characterisation, and pyroelectric current measurement, and orchestrates measurements under varying magnetic fields and temperatures without requiring physical reconfiguration of the measurement setups. 


# Statement of need

Advancements in experimental physics and device manufacturing depend on the precise characterisation of material properties under extreme physical conditions (low temperature and high magnetic/electric fields). For automating experiments, researchers have to choose between expensive proprietary programming software or developing a custom measurement script from scratch.
While powerful ecosystem libraries such as PyVISA [@grecco2023pyvisa] and PyMeasure [@pymeasure_2025] provide the foundational drivers for instrumental communication, they are fundamentally software libraries that require the user to write and maintain code, which creates a barrier to entry for researchers requiring direct data acquisition without the overhead of developing and maintaining a custom codebase.

PICA addresses this gap by functioning as a turnkey application rather than as a library. It offers a "ready-to-run" graphical interface that abstracts the underlying control logic, allowing experimentalists to focus on data acquisition without needing to develop custom software scripts for the supported hardware configurations.
PICA’s architecture is designed to be highly configurable, enabling users to readily adapt it to their specific requirements and to implement user‑defined protocols in addition to the standard measurement protocols already provided. It eliminates the need for reconfiguring the measurement setup to achieve comprehensive characterisation, enabling continuous operation across the full range from Delta-mode low-resistance measurements (the current reversal technique effectively removes constant offsets and improves the signal-to-noise ratio) to high-impedance electrometric measurements, ranging from low-noise superconductors to high-band gap insulators (covering 24 orders of magnitude in resistance) using a single unified framework.
Pyroelectric measurement performed using an electrometer enable highly sensitive characterization of ferroelectric phase transitions by detecting extremely small pyroelectric currents, with a resolution on the order of $10^{-15}$ A.The impedance analyzer enables the characterization of dielectric anomalies over the frequency range from 20 Hz to 2 MHz and is utilized for magnetodielectric and photoinduced characterization across a wide variety of multiferroic systems.Thus, the primary objective of PICA is to serve as a robust software platform that enables advanced, state‑of‑the‑art, high‑precision characterisation of materials.

The system is currently validated with industry-standard hardware, including the AC-DC current source (Model: 6221, Keithley), the Nanovoltmeter (Model:2182, Keithley) , the Electrometer (Model:6517B,Keithley), the DC Source Measure Unit (Model:2400, Keithley), the impedance analyser (Model:E4980A, Keysight), and the temperature controller (Model: 350/340, Lakeshore). While the current implementation drives specific instruments, the underlying framework is hardware agnostic. Researchers using different hardware models need only replace the specific SCPI commands with their instrument equivalent commands to utilise the suite.

It differentiates itself through the following unique features:

* **Accessibility:** A professional GUI dashboard that allows researchers without coding experience to configure and run a complex measurement protocol immediately using the suite's pre-packaged measurement modules.

* **Operational Validation:** PICA's protocols are actively used for cryogenic transport measurements using a custom-designed, laboratory-built multifunctional cryostatic probe in-conjunction with the Physical Property Measurement System (PPMS, DynaCool, Quantum Design) (temperature range: 5-380 K, magnetic field: up to 14 tesla) at the UGC DAE Consortium for Scientific Research, Mumbai Centre, validating the software's core architecture in a real-world research environment and providing a stable, tested foundation for the university and researchers to build upon.

* **Fault Tolerance:** PICA prevents hardware timeouts or driver crashes from freezing the main dashboard by isolating control logic from the user interface, which is a critical advantage over single-threaded scripts.

*  **Modular CLI Architecture:** As demonstrated in the repository, measurement modules also contain CLI measurement module counterparts that allow researchers to utilise PICA's measurement protocol and logic for headless automation or integration into other workflows without GUI overhead.

*  **Operational Transparency:** Unlike a black box solution, PICA exposes real-time, time-stamped command logs for each measurement module, such as `[10:05:25] Keithley 6221: Ramping current to 10 mA`. Rejecting hidden automation and replacing the "black box" paradigm with transparent console logs that show every command sent to the instrument, thereby aiding debugging, ensuring the scientific reproducibility of experimental results, and allowing researchers to verify measurement protocols and troubleshoot hardware instantly.

*  **Open Source Extensibility:** PICA's modular design allows researchers to easily integrate new instrument drivers or experimental protocols by subclassing existing templates, fostering a community-driven ecosystem for instrument control. This ensures that the software remains adaptable, allowing researchers to extend support for their unique instrument configurations.



# Design and Implementation

PICA is built on a modular architecture characterised by self-contained modules, ensuring future extensibility. This design allows individual measurement protocols to be modified independently or added without impacting the core system stability.


### Process Isolation and Concurrency 

Unlike simple script-based automation, PICA decouples the User Interface (UI) from the instrumentation control logic. It utilises Python's standard `multiprocessing` libraries to spawn isolated processes for measurement tasks.

* **Stability:** If an instrument hangs or a communication bus times out, the isolated process can be terminated safely without freezing the main  GUI or losing previous data.

* **Responsiveness:** The `tkinter`-based frontend remains responsive for live data plotting (using `matplotlib` [@hunter2007matplotlib] with blitting) even while the backend waits for hardware triggers. Numpy [@harris2020numpy] is utilised throughout this pipeline for efficient array manipulation and data validation during real-time updates.

* **Data Integrity:** Experimental data integrity is prioritised through a "write on acquisition" strategy. Data is structured using `pandas` [@pandas2025] and is saved to a CSV file immediately after every acquisition point, preventing data loss in the event of a power failure or program/system crash.

### Hardware Abstraction Layer

PICA utilises **PyVISA** [@grecco2023pyvisa] to abstract the low-level communication protocols (GPIB, USB, Ethernet). The software implements a strict initialisation routine:

1. **Connection Verification:** A built-in "VISA (Virtual Instrument Software Architecture) Instrument Scanner" queries the bus (`*IDN?`) to map the connected instrument addresses.

2.  **Instrument Reset Protocol:** To eliminate the influence of all previous experiments, all stored data, instrument buffers, and existing settings or configurations are explicitly reset, thereby providing a clean initial state before each measurement.

3.  **Graceful Shutdown:** A "Safety Shutdown Routine" logic ensures that sources are ramped down to zero and heaters are disabled safely, even if the software is interrupted unexpectedly.

### Testing and Simulation

To ensure measurement reliability, all of these modules were thoroughly tested with the corresponding hardware. Additionally, to facilitate development without constant access to physical instruments, PICA includes a testing suite that uses `pytest`. The suite employs `unittest.mock` to simulate VISA resources, allowing for the verification of backend logic streams, class structure, and command sequences in a continuous integration environment.


# Acknowledgements

We acknowledge the financial support provided under the SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science and Technology (DST), Government of India.


# References
