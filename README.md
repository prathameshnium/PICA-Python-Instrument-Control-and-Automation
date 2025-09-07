# PICA: Python Instrument Control & Automation 

A suite of Python scripts using PyVISA to control and automate laboratory instruments (Keithley, Lakeshore, Keysight) for materials science and physics research.

<p align="center">
  <img src="https://img.shields.io/github/license/prathameshnium/PICA-Python-Instrument-Control-Automation?style=for-the-badge" alt="License"/>
  <img src="https://img.shields.io/github/last-commit/prathameshnium/PICA-Python-Instrument-Control-Automation?style=for-the-badge&color=blue" alt="Last Update"/>
  <img src="https://img.shields.io/github/repo-size/prathameshnium/PICA-Python-Instrument-Control-Automation?style=for-the-badge&color=brightgreen" alt="Repo Size"/>
</p>

---

## Core Dependencies

These scripts rely on a few key Python packages for communication and data handling. Ensure you have them installed in your environment.

<p align="center">
  <a href="https://pyvisa.readthedocs.io/" target="_blank" rel="noreferrer"> 
    <img src="https://raw.githubusercontent.com/pyvisa/pyvisa-logo/master/pyvisa-logo-light.png" alt="pyvisa" width="120"/> 
  </a>
  &nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://numpy.org/" target="_blank" rel="noreferrer"> 
    <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/numpy/numpy-original.svg" alt="numpy" width="50" height="50"/> 
  </a>
  &nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://pandas.pydata.org/" target="_blank" rel="noreferrer"> 
    <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/pandas/pandas-original.svg" alt="pandas" width="50" height="50"/> 
  </a>
</p>

---

## About This Repository

This collection provides practical, ready-to-use Python scripts for automating common electrical and thermal characterization experiments.

### Instrument Scripts & Drivers
Each folder is dedicated to a specific instrument or measurement setup, containing the necessary control logic and experimental procedures.

### Manuals & Guides  invaluable for Refrences
A collection of official instrument manuals and programming guides are available in the **[assets/Manuals](assets/Manuals)** directory. These are invaluable references for understanding instrument commands and capabilities.

---

## Featured Measurement Systems

### Electrical Characterization
* **IV (Current-Voltage) Measurement**
    * *Current Source:* `Keithley 2400`
    * *Nanovoltmeter:* `Keithley 2182A`
* **LCR (Inductance, Capacitance, Resistance) Measurement**
    * *LCR Meter:* `Keysight E4980A` (Used for CV measurements)
* **Delta Mode Resistivity Measurement**
    * *Current Source:* `Keithley 6221`
    * *Nanovoltmeter:* `Keithley 2182A`
* **High Resistance Measurement**
    * *Electrometer:* `Keithley 6517B`

### Specialized & Thermal Systems
* **Pyroelectric Measurements**
    * *Electrometer:* `Keithley 6517B`
    * *Temperature Controller:* `Lakeshore 340`
* **Environmental Control**
    * *Temperature Controllers:* `Lakeshore 350`, `Lakeshore 340`

---

## Author & Acknowledgment

This software was developed by **Prathamesh Deshmukh** during his PhD tenure.

The work was conducted at the **Mumbai Centre** of the **UGC-DAE Consortium for Scientific Research (CSR)**, a facility dedicated to providing advanced research infrastructure to the academic community. The development took place within the [Sudip Mukherjee Materials Physics Lab](https://www.researchgate.net/lab/Sudip-Mukherjee-Materials-Physics-Lab-Sudip-Mukherjee).

---

## License

This project is licensed under the **MIT License**. See the `LICENSE` file for details.
