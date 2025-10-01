# PICA: Python Instrument Control & Automation

A suite of Python scripts using PyVISA to control and automate laboratory instruments for materials science and physics research.

---

### About This Project

This collection provides practical, ready-to-use Python scripts for automating common electrical and thermal characterization experiments. Each folder is dedicated to a specific instrument or measurement setup, containing the necessary control logic and GUI.

A collection of official instrument manuals is available in the `_assets/Manuals` directory for reference.

### Core Dependencies
This project relies on a few key packages. Ensure they are installed in your Python environment:
* PyVISA
* PyMeasure
* NumPy
* Pandas

---

### Featured Measurement Systems

* **Low Resistance (Delta Mode)**
    * *Instruments:* Keithley 6221/2182A
    * *Measurements:* I-V, Resistance vs. Temperature
* **Mid Resistance (Four-Probe)**
    * *Instruments:* Keithley 2400, Keithley 2182
    * *Measurements:* I-V, Resistance vs. Temperature
* **High Resistance**
    * *Instrument:* Keithley 6517B Electrometer
    * *Measurements:* I-V, Resistivity vs. Temperature
* **LCR Measurements**
    * *Instrument:* Keysight E4980A LCR Meter
    * *Measurement:* Capacitance vs. Voltage
* **Pyroelectric Measurements**
    * *Instruments:* Keithley 6517B, Lakeshore Temp. Controller
    * *Measurement:* Pyroelectric Current vs. Temperature
* **AC Transport**
    * *Instrument:* Lock-in Amplifier (e.g., SR830)
    * *Measurement:* AC Resistance
* **Temperature Control**
    * *Instrument:* Lakeshore 340/350
    * *Function:* Environmental control and temperature ramping.

---

### Author & Acknowledgment

Developed by **Prathamesh Deshmukh** | Vision & Guidance by **Dr. Sudip Mukherjee**
<br>
*UGC-DAE Consortium for Scientific Research, Mumbai Centre*

---

### License

This project is licensed under the **MIT License**. See the `LICENSE` file for details.
