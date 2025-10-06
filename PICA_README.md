# PICA: Python Instrument Control & Automation
![alt text](_assets/LOGO/PICA_LOGO_NBG.png)
Developed by **Prathamesh Deshmukh**

A modular software suite for automating laboratory measurements in physics research, featuring a central dashboard for launching experiments in isolated, stable processes.

---

### How to Use This Launcher

1.  **Check Connections:** Ensure all instruments are powered on and physically connected to the GPIB/VISA interface.
2.  **Test Communication:** Run the **"Test GPIB Connection"** utility from the launcher to verify that all instruments are recognized by the system.
3.  **Launch Module:** Select and launch the desired measurement program. Each program runs independently to ensure stability.

---

### Available Measurement Modules

#### Keithley 6221 / 2182 (Low-Resistance Delta Mode)

This suite uses the noise-canceling Delta Mode for high-precision measurements.

* **I-V Characterization:** High-precision I-V sweeps for low-resistance samples.
* **Resistance vs. Temperature (R-T):** Automated R-T data acquisition with either active (ramp and stabilize) or passive temperature profiles.

#### Keithley 6517B Electrometer (High Resistance)

For insulating materials, dielectrics, and high-impedance devices. This suite provides:

* **High-Resistance I-V & R-T:** Characterization with active or passive temperature control.
* **Pyroelectric Current vs. Temperature:** Quantifies pyroelectric current during a controlled temperature ramp.

#### Keithley 2400 SourceMeter (Mid-Resistance & Precision)

Standard four-probe measurements for materials like semiconductors.

* **Four-Probe I-V Characterization:** Can be used standalone or with a Keithley 2182 for enhanced voltage resolution.
* **Four-Probe R-T Characterization:** Temperature-dependent resistance measurements.

#### Other Systems
 
* **Keysight E4980A LCR Meter:** Automated Capacitance-Voltage (C-V) measurements.
* **Lock-in Amplifier:** For measuring AC transport properties.
* **Lakeshore 340/350 Controller:** Standalone utilities for defining temperature profiles or passive monitoring.

---

### Authors & Acknowledgments

* **Lead Developer:** Prathamesh Deshmukh
* **Principal Investigator:** Dr. Sudip Mukherjee
* **Affiliation:** UGC-DAE Consortium for Scientific Research, Mumbai Centre

Financial support for this work was provided under SERB-CRG project grant No. CRG/2022/005676 from the Anusandhan National Research Foundation (ANRF), a statutory body of the Department of Science & Technology (DST), Government of India.

---

### License

This project is licensed under the terms of the **MIT License**. See the `LICENSE` file for full details.