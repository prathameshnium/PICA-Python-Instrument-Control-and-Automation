## Instrument & Software Update Log

### October 3, 2025

**Delta Mode Measurement Suite (Keithley 6221/2182A)**

-   **New Program:** Developed `Delta_Mode_Active_Temp_Control.py` for automated measurements. The software now ramps to a user-defined temperature, waits for thermal stability, and then begins the Delta Mode measurement sequence. Includes critical safety logic to disable the heater on completion, error, or exit.
-   **New Program:** Created `Delta_Mode_IV_Ambient.py` to perform high-precision I-V sweeps at ambient temperature using the Delta Mode for each data point.
-   **Enhancement:** Merged modern GUI, plotting, and passive temperature sensing features into `Delta_Lakeshore_Front_end_Passive.py` for improved usability and data logging.

---

### September 18, 2025

**General Updates**

-   **High-Resistance Module (6517B):** Refined backend logic for improved stability and updated the front-end with a more intuitive color scheme for control buttons.
-   **PICA:** Standardized and updated all relevant library paths for the PICA software.

---

### September 17, 2025

**Keithley 6517B Electrometer Suite**

-   **New Measurement Capability:** Developed a comprehensive front-end and back-end for high-resistance R vs. T measurements.
-   **New Measurement Capability:** Integrated a linearized driver for the temperature controller to enable pyroelectric current measurements as a function of temperature.
-   **Enhancement:** Overhauled the user interface for the I-V measurement module.

**Keithley 2400 SourceMeter Suite**

-   **New Measurement Capability:** Created a new, dedicated front-end and back-end for V vs. T measurements, including a linearized driver for the Lake Shore 350.
-   **Enhancement:** Simplified the communication protocol for the I-V measurement back-end and updated the corresponding front-end.

**General Enhancements**

-   **GPIB:** Deployed a new interface for testing instrument connections.
-   **PICA:** Improved the graphical user interface.