## Instrument & Software Update Log
### Version 14.0 (Current)

**Frontend Enhancements & New Measurement Modes**

-   **Major Frontend Version Bumps:** Updated numerous frontend scripts to their latest, most stable versions. This includes significant improvements in reliability, UI consistency, and backend logic across all major measurement suites.
-   **New Passive "Sensing" Modes:** Introduced new "T-Sensing" (passive) measurement modes for several instrument combinations. These modules allow for resistance logging while the temperature changes externally (e.g., during natural cooling), complementing the existing "T-Control" (active) modes.
    -   New `Delta Mode R-T (T_Sensing)` module.
    -   New `K2400 R-T (T_Sensing)` module.
    -   New `K2400_2182 R-T (T_Sensing)` module.
    -   New `K6517B R-T (T_Sensing)` module.
-   **Launcher Script Synchronization:** Updated the main launcher (`PICA_v6.py`) and the executable build script (`Setup/Picachu.py`) to point to all the new and updated frontend versions, ensuring users always access the latest features.
-   **Plotter Utility Upgrade:** The standalone plotter (`PlotterUtil_Frontend_v3.py`) has been enhanced to support plotting multiple files simultaneously, improving comparative data analysis workflows.
-   **Documentation Refresh:** Updated `README.md` and `PICA_README.md` to reflect the current feature set, expanded measurement capabilities, and latest UI.
-   **Files Added/Modified:**
    -   `PICA_v6.py`
    -   `Setup/Picachu.py`
    -   `README.md`
    -   `PICA_README.md`
    -   `Change_Logs.md`
    -   `Delta_mode_Keithley_6221_2182/IV_K6221_DC_Sweep_Frontend_V10.py`
    -   `Delta_mode_Keithley_6221_2182/Delta_RT_K6221_K2182_L350_T_Control_Frontend_v5.py`
    -   `Delta_mode_Keithley_6221_2182/Delta_RT_K6221_K2182_L350_Sensing_Frontend_v5.py` (New)
    -   `Keithley_2400_Keithley_2182/IV_K2400_K2182_Frontend_v3.py`
    -   `Keithley_2400/RT_K2400_L350_T_Control_Frontend_v3.py`
    -   `Keithley_2400/RT_K2400_L350_T_Sensing_Frontend_v4.py`
    -   `Keithley_2400_Keithley_2182/RT_K2400_K2182_T_Control_Frontend_v3.py`
    -   `Keithley_2400_Keithley_2182/RT_K2400_2182_L350_T_Sensing_Frontend_v2.py`
    -   `Keithley_6517B/High_Resistance/IV_K6517B_Frontend_v11.py`
    -   `Keithley_6517B/High_Resistance/RT_K6517B_L350_T_Control_Frontend_v13.py`
    -   `Keithley_6517B/High_Resistance/RT_K6517B_L350_T_Sensing_Frontend_v14.py`
    -   `LCR_Keysight_E4980A/CV_KE4980A_Frontend_v3.py`
    -   `Utilities/PlotterUtil_Frontend_v3.py`

---

### Version 13.9 (Current)

**Documentation & Launcher Synchronization**

-   **Documentation Overhaul:** Updated and synchronized key documentation files (`README.md`, `Change_Logs.md`) to accurately reflect the latest project structure, features, and build process.
-   **Executable-Specific README:** Created `PICA_README.md`, a version of the main README tailored for inclusion in the Nuitka-built executable. This ensures users of the standalone application have access to relevant project information.
-   **Launcher Script Update:** Updated the main launcher script (`PICA_v6.py`) to reflect the latest versioning and ensure consistency with the project's documentation.
-   **Files Added/Modified:**
    -   `PICA_v6.py`
    -   `README.md`
    -   `PICA_README.md` (New File)
    -   `Change_Logs.md`

---

## Instrument & Software Update Log
### Version 13.8 (Current)

**Build System & Documentation Overhaul**

-   **New Build System:** Introduced `Picachu.py`, a dedicated build script for creating a standalone Windows executable using Nuitka. This separates the development launcher (`PICA_v6.py`) from the production build script.
-   **Automated Releases:** Implemented a GitHub Actions workflow (`build-exe.yml`) that automatically compiles, packages, and uploads the `Picachu.exe` as a release asset whenever a new release is created on GitHub.
-   **Build Script Refinements:**
    -   The `resource_path` function in `Picachu.py` was adapted to correctly locate bundled assets when running as a compiled executable.
    -   The Nuitka build configuration was optimized to include all necessary Python packages, data files (`_assets`, `LICENSE`), and documentation (`PICA_README.md`, `Updates.md`), ensuring the executable is fully self-contained.
-   **Documentation & Portfolio Update:**
    -   Updated the main project `README.md` and the personal portfolio website (`project-pica.html`, `computational-works.html`) to accurately reflect the latest project structure, features, and build process.
-   **Files Added/Modified:**
    -   `Setup/Picachu.py`
    -   `.github/workflows/build-exe.yml`
    -   `README.md`
 
---

## Instrument & Software Update Log
### Version 13.7

**GUI Standardization & Modernization**

-   **Major UI/UX Overhaul:** Refactored and redesigned several key measurement frontends to align with the modern, dark-themed, and professional UI standard established in Version 13.6. This creates a consistent user experience across the entire PICA suite.
-   **New Delta Mode R-T Frontend:** Integrated a new, robust GUI for Delta Mode R-T measurements (`Delta_RT_K6221_K2182_L350_T_Control_Frontend_v4.py`). This frontend now includes the advanced temperature stabilization and hardware ramp logic, along with the standardized live plotting and console layout.
-   **New High-Resistance R-T Frontend:** Deployed a new GUI for High-Resistance R-T sweeps (`RT_K6517B_L350_T_Control_Frontend_v12.py`). It replaces the previous version with the standardized UI, featuring improved graph layouts and more reliable instrument control.
-   **New Lakeshore Temp Control Frontend:** Released a modernized GUI for the standalone Lakeshore 350 temperature ramp utility (`T_Control_L350_RangeControl_Frontend_v7.py`), bringing its appearance and functionality in line with other modules.
-   **New Delta Mode I-V Frontend:** Updated the Delta Mode I-V sweep GUI (`IV_K6221_DC_Sweep_Frontend_V9.py`) with the standardized modern theme, improving usability and visual consistency.
-   **Backend Refinements:**
    -   The backend for the High-Resistance R-T module (`IV_K6517B_L350_T_Control_Backend_v6.py`) was updated with a more robust active stabilization logic.
    -   The Lakeshore 350 backend (`T_Control_L350_Simple_Backend_v10.py`) was improved for clarity and reliability.
-   **PICA Launcher Update:** The main launcher (`PICA_v6.py`) has been updated to point to all the new and improved frontend scripts.
-   **Files Added/Modified:**
    -   `PICA_v6.py`
    -   `Delta_mode_Keithley_6221_2182/Delta_RT_K6221_K2182_L350_T_Control_Frontend_v4.py`
    -   `Delta_mode_Keithley_6221_2182/IV_K6221_DC_Sweep_Frontend_V9.py`
    -   `Keithley_6517B/High_Resistance/RT_K6517B_L350_T_Control_Frontend_v12.py`
    -   `Keithley_6517B/High_Resistance/Backends/IV_K6517B_L350_T_Control_Backend_v6.py`
    -   `Lakeshore_350_340/T_Control_L350_RangeControl_Frontend_v7.py`
    -   `Lakeshore_350_340/Backends/T_Control_L350_Simple_Backend_v10.py`

---

### Version 13.6

**New Measurement Frontends & UI Standardization**

-   **New LCR C-V Frontend:** Integrated a new professional GUI for C-V measurements using the Keysight E4980A (`CV_KE4980A_Frontend_v2.py`). It features a modern, dark-themed UI, live plotting, and robust instrument control logic separated into a backend class.
-   **New K2400/2182 I-V Frontend:** Added a new professional GUI for high-precision 4-probe I-V sweeps using the Keithley 2400 and 2182 (`IV_K2400_K2182_Frontend_v2.py`). This replaces older versions with a standardized, more reliable interface.
-   **New K2400/2182 R-T Frontends:**
    -   Integrated an **active** R-T sweep frontend (`RT_K2400_K2182_T_Control_Frontend_v2.py`) that automates temperature stabilization and ramping with the Lakeshore 350.
    -   Integrated a **passive** R-T logging frontend (`RT_K2400_2182_L350_T_Sensing_Frontend_v1.py`) for monitoring resistance while temperature changes externally.
-   **New K2400 R-T (Passive) Frontend:** Added a dedicated GUI for passive R-T logging using a single Keithley 2400 and a Lakeshore 350 (`RT_K2400_L350_T_Sensing_Frontend_v3.py`).
-   **UI/UX Overhaul:** All new frontends share a consistent, modern, dark-themed design with scrollable control panels, live Matplotlib graphs, and integrated logging consoles, significantly improving usability and professional appearance.
-   **PICA Launcher Update:** The main launcher (`PICA_v6.py`) has been updated to point to all these new, versioned frontend scripts, ensuring users always launch the latest and most stable versions.
-   **Files Added/Modified:**
    -   `PICA_v6.py`
    -   `LCR_Keysight_E4980A/CV_KE4980A_Frontend_v2.py`
    -   `Keithley_2400_Keithley_2182/IV_K2400_K2182_Frontend_v2.py`
    -   `Keithley_2400_Keithley_2182/RT_K2400_K2182_T_Control_Frontend_v2.py`
    -   `Keithley_2400_Keithley_2182/RT_K2400_2182_L350_T_Sensing_Frontend_v1.py`
    -   `Keithley_2400/RT_K2400_L350_T_Sensing_Frontend_v3.py`

---

### Version 13.5

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