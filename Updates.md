## Instrument & Software Update Log
### Version 13.2 (05/10/2025)

**PICA Launcher & K2400/2182 Suite**

-   **Enhancement:** Updated `PICA_Launcher_V5.py` to integrate the new professional frontends for the Keithley 2400/2182 measurement suite.
-   **New Launchers Added:**
    -   `IV_Sweep_Keithley_2400_2182_Frontend_V1.py`: For high-precision I-V sweeps.
    -   `VT_Sweep_Keithley_2400_2182_Frontend_V1.py`: For automated V-T measurements.
-   **Files Modified:** `PICA_Launcher_V5.py`.

---

### Version 13.1 (05/10/2025)

*This version number is being retroactively assigned to the previous update for better version tracking.*

### October 4, 2025
**New Features & Enhancements:**

**PICA Launcher & Delta Mode Suite**

-   **Enhancement:** Updated `PICA_Launcher_V5.py` to resolve duplicate script paths and ensure it correctly launches the latest versions of all measurement programs.
-   **Enhancement:** Successfully tested and validated the latest Delta Mode scripts:
    -   `Delta_Mode_Active_Temp_Control_V2.py`: Active R-T measurements confirmed to be stable and accurate.
    -   `Delta_Mode_IV_Ambient.py`: I-V sweep functionality verified.
-   **Files Modified:** `PICA_Launcher_V5.py`, `Delta_Mode_Active_Temp_Control_V2.py`, `Delta_Mode_IV_Ambient.py`.

---

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
*   **New K2400/2182 I-V Frontend:**
    *   Created `IV_Sweep_Keithley_2182_Frontend_V1.py`, a new professional GUI for I-V sweeps using the Keithley 2400 and 2182.
    *   Features a modern UI consistent with other PICA modules, live plotting, and robust instrument control.
*   **New K2400/2182 V-T Frontend:**
    *   Created `VT_Sweep_Keithley_2400_2182_Frontend_V1.py`, a dedicated GUI for V-T measurements.
    *   Provides a user-friendly interface for the existing V-T measurement script, including temperature control and live data visualization.