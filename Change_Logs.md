## Instrument & Software Update Log
### Version 13.5 (Current)

**Project-Wide Refactoring & Reorganization**

-   **Major Refactoring:** Implemented a major project-wide restructuring to improve modularity and maintainability. All measurement scripts have been reorganized into instrument-specific folders (e.g., `Keithley_2400`, `Delta_mode_Keithley_6221_2182`).
-   **New Structure:** Within each instrument directory, a `Backends` sub-folder has been created to house the instrument control logic, separating it from the `Frontend` GUI files. This promotes code reuse and simplifies development.
-   **Path Updates:** The main PICA launcher (`PICA_v6.py`) has been updated to reflect all new script paths, ensuring that all measurement suites launch correctly from their new locations.
-   **File Cleanup:** This reorganization standardizes the location of all measurement scripts, making the project easier to navigate and manage.
-   **Files Modified:** `PICA_v6.py`, and all frontend/backend scripts moved to new locations.

---

### Version 13.4

**PICA Launcher & Script Integration**

-   **Enhancement:** Upgraded the PICA Launcher to `PICA_v6.py` (internally version 5.3), featuring a significant UI/UX overhaul for a more professional and organized layout.
-   **Refinement:** Re-structured the launcher layout into two columns for better readability and grouping of related measurement suites (Low, Mid, High Resistance).
-   **New Feature:** Added an integrated markdown parser to the documentation viewer (`README`, `Updates.md`) for improved readability with styled headers, lists, and bold text.
-   **New Feature:** The GPIB/VISA scanner now automatically starts a scan upon opening and includes a quick-reference "Address Guide" for common instruments.
-   **Script Update:** Updated script paths in the launcher to point to the latest, most stable versions across all modules, including:
    -   `Delta_Mode_Frontend_Active_Temp_Control_V3.py`
    -   `Delta_Lakeshore_Frontend_Passive_V2.py`
    -   `Keithley_6517B_IV_Frontend_V9.py`
    -   `6517B_high_resistance_lakeshore_RT_Frontend_V11p2_5Always.py`
    -   `6517B_high_resistance_lakeshore_RT_Frontend_V12_Passive.py`
    -   `lakeshore350_temp_ramp_Frontend_V6.py`
    -   `lakeshore350_passive_monitor_Frontend_V2.py`
-   **Files Modified:** `PICA_v6.py`.

---

### Version 13.3 (10/05/2025)

**PICA Launcher & K2400 / K2400-2182 Suites**

-   **Enhancement:** Updated `PICA_Launcher_V5p1.py` to correctly point to the latest and most specific scripts for R-T measurements, distinguishing between "Active" (temperature ramp) and "Passive" (temperature logging) modes.
-   **New Scripts Integrated:**
    -   `Frontend_Keithley_2400_Lakeshore_350_V_vs_T_V1.py`: For automated R-T sweeps with active temperature ramping using a Keithley 2400.
    -   `Keithley_2400_Lakeshore_350_V_vs_T_Passive_V2.py`: For passive R-T data logging using a Keithley 2400.
    -   `VT_Sweep_Keithley_2400_2182_Frontend_Active_V1.py`: For automated R-T sweeps with active temperature ramping using the Keithley 2400/2182 pair.
    -   `VT_Sweep_K2400_2182_Passive_V1.py`: For passive R-T data logging using the Keithley 2400/2182 pair.
-   **Refinement:** The launcher now provides a clearer, more organized structure for selecting the correct measurement type.
-   **Files Modified:** `PICA_Launcher_V5.3.py`.

---

### Version 13.2 (10/05/2025)

**PICA Launcher & K2400/2182 Suite**

-   **Enhancement:** Updated `PICA_Launcher_V5.py` to integrate the new professional frontends for the Keithley 2400/2182 measurement suite.
-   **New Launchers Added:**
    -   `IV_Sweep_Keithley_2400_2182_Frontend_V1.py`: For high-precision I-V sweeps.
    -   `VT_Sweep_Keithley_2400_2182_Frontend_V1.py`: For automated V-T measurements.
-   **Files Modified:** `PICA_Launcher_V5.2.py`.

---

### Version 13.1 (04/10/2025)

*This version number is being retroactively assigned to the previous update for better version tracking.*

### October 4, 2025
**New Features & Enhancements:**

**PICA Launcher & Delta Mode Suite**

-   **Enhancement:** Updated `PICA_Launcher_V5.py` to resolve duplicate script paths and ensure it correctly launches the latest versions of all measurement programs.
-   **Enhancement:** Successfully tested and validated the latest Delta Mode scripts:
    -   `Delta_Mode_Active_Temp_Control_V2.py`: Active R-T measurements confirmed to be stable and accurate.
    -   `Delta_Mode_IV_Ambient.py`: I-V sweep functionality verified.
-   **Files Modified:** `PICA_Launcher_V5.1.py`, `Delta_Mode_Active_Temp_Control_V2.py`, `Delta_Mode_IV_Ambient.py`.

---

### October 3, 2025

**Delta Mode Measurement Suite (Keithley 6221/2182)**

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