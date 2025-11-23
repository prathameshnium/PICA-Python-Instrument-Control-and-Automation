## Instrument & Software Update Log
---
### Version 15 (Current)
*Released: November 22, 2025*
*Status: Tested and operational. Minor cosmetic updates pending.*
**JOSS Submission & Professionalization**
-   **Code Cleanup:** Comprehensive refactoring and cleanup of the codebase to meet professional standards for JOSS submission.
**A Note on Recent Updates and Testing:**
> This software suite is actively used for daily laboratory measurements and is regularly tested on the physical instruments. Recently, a suite of automated tests has been integrated to improve code quality and stability. While these tests validate the core logic, the changes made to support them require a new round of thorough manual testing on the hardware to identify and resolve any practical bugs that may have been introduced. This process is currently underway, and further updates will be provided upon its completion.

---

### Version 14.0 and 14.1
*Released: November 15, 2025*
-   **Improved "Getting Started" Guide:** Clarified installation and launch instructions.
-   **Enhanced Documentation:** Overhauled the main project `README.md` to provide a more comprehensive overview, including a new "Architecture" section that details the frontend-backend separation and the role of `multiprocessing`.
-   **Synchronized Executable README:** Updated `PICA_README.md` for the standalone executable.

**GUI Enhancements & New Measurement Modes**
-   **Major GUI Version Bumps:** Updated numerous frontend scripts to their latest stable versions.
-   **New Passive "Sensing" Modes:** Introduced "T-Sensing" modes for resistance logging during external temperature changes.
    -   New `Delta Mode R-T (T_Sensing)` module.
    -   New `K2400 R-T (T_Sensing)` module.
    -   New `K2400_2182 R-T (T_Sensing)` module.
    -   New `K6517B R-T (T_Sensing)` module.
-   **Plotter Utility Upgrade:** Enhanced `PlotterUtil_GUI_v3.py` to support simultaneous multi-file plotting.

---

### Version 13.10 (Lab Baseline)
*Released: October 10, 2025*

**Milestone: Base Laboratory Deployment**
-   **Status:** A stable base version for the laboratory is in place. Almost all core instrument communication and measurement loops are working perfectly.
-   **Context:** This release (`b545cef`) marked the completion of functional development before the focus shifted to cosmetic UI improvements and standardization.

---

### Version 13.9
*Released: October 09, 2025*

**Documentation & Launcher Synchronization**
-   **Documentation Overhaul:** Synchronized `README.md` and `Change_Logs.md`.
-   **Executable-Specific README:** Created `PICA_README.md`.
-   **Launcher Script Update:** Updated `PICA_v6.py` to reflect versioning.

---

### Version 13.8
*Released: October 09, 2025*

**Build System & Documentation Overhaul**
-   **New Build System:** Introduced `Picachu.py` for creating standalone Windows executables via Nuitka.
-   **Automated Releases:** Implemented GitHub Actions `build-exe.yml` for automated compilation.
-   **Build Script Refinements:** Optimized `resource_path` for bundled assets.

---

### Version 13.7
*Released: October 08, 2025*

**GUI Standardization & Modernization**
-   **Major UI/UX Overhaul:** Refactored key frontends (Delta Mode R-T, High-Resistance R-T, Lakeshore Control) to align with the modern, dark-themed UI standard.
-   **Backend Refinements:** Updated `IV_K6517B_L350_T_Control_Backend_v6.py` and `T_Control_L350_Simple_Backend_v10.py` with improved stability logic.

---

### Version 13.6
*Released: October 07, 2025*

**New Measurement GUIs & UI Standardization**
-   **New LCR C-V GUI:** Integrated `CV_KE4980A_GUI_v2.py`.
-   **New K2400/2182 Suites:** Added standardized I-V and R-T frontends.
-   **UI/UX Overhaul:** Standardized all new frontends with a consistent dark-themed design.

---

### Version 13.5
*Released: October 06, 2025*

**Project-Wide Refactoring**
-   **Major Refactoring:** Reorganized all scripts into instrument-specific folders (e.g., `Keithley_2400`, `Delta_mode_Keithley_6221_2182`).
-   **New Structure:** Separated logic into `Backends` and `GUI` sub-folders.

---

### Version 13.4
*Released: October 05, 2025*

**PICA Launcher & Script Integration**
-   **Enhancement:** Upgraded to `PICA_v6.py` with a two-column layout.
-   **New Feature:** Integrated markdown documentation viewer and automatic GPIB/VISA scanner.

---

### Version 13.3
*Released: October 05, 2025*

-   **Enhancement:** Updated launcher to distinguish between "Active" and "Passive" R-T modes.
-   **New Scripts:** Integrated specialized scripts for Keithley 2400 R-T measurements.

---

### Version 13.2
*Released: October 05, 2025*

-   **Enhancement:** Integrated new professional frontends for the Keithley 2400/2182 measurement suite.

---

### Version 13.1
*Released: October 04, 2025*

-   **Enhancement:** Validated `Delta_Mode_Active_Temp_Control_V2.py` and `Delta_Mode_IV_Ambient.py`.

---

### Version 13.0 (Delta Mode Milestone)
*Released: October 03, 2025*

-   **New Program:** Developed `Delta_Mode_Active_Temp_Control.py` for automated temperature ramping.
-   **New Program:** Created `Delta_Mode_IV_Ambient.py`.

---
## Historical Development Archive

> **Note on Project Origins:**
> The PICA software suite underwent an extensive offline development phase on isolated laboratory instrument control systems before its full migration to GitHub. The timeline below reconstructs the development history from raw commit logs and code diffs.

### Phase 3: Expansion & Refinement (Sept 2025)
*Focus on High Resistance & Temperature Control*
-   **Sep 18, 2025:** Refined backend logic for High-Resistance Module (6517B).
-   **Sep 17, 2025:** Developed comprehensive front-end/back-end for High-Resistance R vs. T. Integrated linearized drivers for Lakeshore 350.
-   **Sep 10, 2025:** Major cleanup of `IV_Measurement` scripts and creation of `Lockin_Only.py`.
-   **Sep 06, 2025:** **First Major Refactor.** Reorganized loose scripts into categorized folders (`IV_2400_Only`, `Pyroelectricity`, `Temprature_Controller`).

### Phase 2: Modularization (July - Aug 2024)
*Transitioning from Scripts to Modules*
-   **Aug 15, 2024:** Added `Lakeshore_340_Continue_test.py` to support the older Lakeshore 340 model.
-   **July 27, 2024:** **Structural reorganization.** Moved root-level scripts into categorized folders (`Pyroelectricity`, `LCR Keysight E 4980 A`). Renamed `Keithley_6517B.py` to `Pyroelectricity/Keithley_6517B.py`.

### Phase 1: The "Bulk Upload" Era (Mar 2024)
*Migration of Offline Work*
-   **Mar 22, 2024:** Added Poling capabilities (`Poling_Keithley6517B.py`).
-   **Mar 12, 2024:** Updates to `IV_Measurement.py` and `IV_Combine_2400-2182.py` logic.
-   **Mar 11, 2024:** **Massive Feature Commit.** Added `LCR_CV.py` (Capacitance-Voltage), `Live_Data_Final-pyro.py`, and `IV_Measurement.py`.
-   **Mar 03, 2024:** **Core Drivers Added.** Initial upload of `Keithley_6517B.py`, `Lakeshore350.py`, and `Pyroelectric.py` drivers. This marks the end of the first major offline development block.

### Phase 0: Implementation & Testing (Dec 2023)
*Proof of Concept & Raw SCPI Implementation*
-   **Dec 09, 2023:** Updates to `IV_Front_End.py` and `README`.
-   **Dec 08, 2023:** **Project Birth (Version Control).**
    -   *Commit:* `963c8fd` - "Create Combine_2400-2182.py".
    -   *Technical Detail:* This initial script implemented raw SCPI buffer operations (`trace:data?`, `assert_trigger`).
    -   *Commit:* `f45dba8` - "Create GPIB_TEST.py". Established the first connectivity test for instrument communication.

### Phase -1: Inception & Feasibility (June 2022)
*Concept, Learning, and Prototyping*
-   **June 10, 2022:** **First Tangible Prototype.** Initial proof-of-concept scripts for I-V characterization and shared ("Emailing code for IV.pdf"). This marked the validation of the Python-based control approach before formal development began.
-   **June 09, 2022:** **Environment Setup & Skill Acquisition.** Due to the air-gapped nature of the laboratory computers, the initial Python environment was established by manually downloading dependencies (PyVISA, NumPy, Matplotlib) and installing them offline. This phase involved guided learning of instrument control concepts (e.g., "Python in Origin", SCPI basics) alongside specific training provided by the PI.

-   **Collaboration Note:** The project's realization was significantly aided by colleagues (from UGC-DAE CSR,Mumbai) who assisted in rectifying technical issues and developing the necessary cryogenic probes and hardware fixtures required for measurements.
-   **June 2022:** **Project Ideation.** Dr. Sudip Mukherjee (Principal Investigator) proposed the initiative for laboratory automation from manual methods to Python-based control. He provided critical roadmap materials, including reference videos, instrument handling protocols, and conceptual designs for the GUI layouts and hardware integration.

