go through [releases](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases) and [tags](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/tags), for the finalised released versions
## Instrument & Software Update Log
---
### [1.0.6] - Upcoming (in testing)

- **Novocontrol Alpha-A Support (experimental)**: New broadband dielectric spectroscopy frequency-scan module (GUI + `Instrument_Control` CLI template) with WinDETA-compatible exports and a dedicated GPIB runbook (`docs/Novocontrol_GPIB_Runbook.md`).
- **AC Measurement Modules (experimental)**: Ten new four-probe AC modules built around the Keithley 6221 as the AC current source, in two matched sets of five — I-V (current-amplitude sweep at fixed frequency), frequency scan, R-T under Lakeshore 350 control, and passive R-T against the Lakeshore 350 and the Cryo-con 34.
  - **6221 + SR830** (`pica/lockin/sr830/`): phase-sensitive detection. `R = X / I_rms` with `I_rms = I_peak / sqrt(2)`; the 6221 phase marker on trigger-link line 3 is the SR830's reference, and every point checks `LIAS?` and compares `FREQ?` against the programmed frequency so an unplugged reference cable is reported as an error rather than as a plausible resistance.
  - **6221 + Keithley 197A** (`pica/keithley/k6221_k197a/`): the same measurement with a broadband true-RMS DMM in place of the lock-in. `R = V_rms / I_rms`, magnitude only — there is no reference, so nothing outside the drive frequency is rejected and the resistance is an **upper bound**. The spread across the averaged readings is logged next to the mean, a drive outside the meter's AC volts band is flagged on every point, and the 197-dialect command set carries the same UNVERIFIED warning as `Monitor_K197A_GUI.py`.
  - Both sets write the PICA commented-header `.dat` format with optional bar / van der Pauw resistivity, take the current off in the thread that put it on on every exit path (finished, stopped or thrown), and put the heater back to off with it in the T-Control modules. The passive modules never write to their thermometer.
- **SCPI Console**: Interactive console for raw SCPI commands to any VISA instrument.
- **Plotting Engine**: Removed blitting (fixes high-DPI scaling issues), movable legends, scroll fixes, AM/PM timestamps.
- **Launcher**: Tools categorised for easier navigation.
- **Launcher v2 (`python run_pica_v2.py`)**: Opens on a Quick Select screen — category / module / protocol, each with its own description — with the full module grid moved to an Advanced Options window (`Ctrl+Shift+A`). Status strip moved to the bottom of both windows; on Quick Select it carries the temperature, a pressure placeholder and an Instrument Status button. That window holds the per-instrument lights (with a dot key), the raw VISA/GPIB scan table, and a shortcut to the standalone scanner — which now starts half a second after the launcher's own scan instead of alongside it. An instrument that answers but is not in the known list appears as its own chip, named from its `*IDN?` reply and marked new. The console is now a log the launcher owns rather than a panel in the rail: Quick Select opens it from a Console button on the strip, Advanced Options carries one inline, and a view opened later still shows what happened at startup. Quick Select narrows the instruments it shows at each level — category, module, then protocol — each with its live status dot, shown on the status strip at reading size. Quick Select now gathers all frequency scans into one "Frequency Scan vs. Temperature" module, so the T-Control step scan is findable. The standalone VISA/GPIB scanner is no longer auto-opened at startup; it opens with Advanced Options instead. The Instrument Status window opens at startup once the first scan lands, and its scan table now accounts for every VISA resource — identified, silent, or skipped with the reason it was skipped. Launching a protocol repaints the status panel in every open window (no rescan while a module is starting). Advanced Options now opens maximised, reflows to four card columns on a wide screen, carries its family legend in the header, and uses the compact strip with the Instrument Status button. Cryo-con 34 protocols are offered in Quick Select under the LCR meter and the electrometer only, beside the Lakeshore 350 ones.
- **Launcher (fix)**: the pyroelectric current module was listed as having no temperature role in both launchers' catalogues; it drives the Lakeshore 350 (`SETP` + `RAMP`), so it is now marked T Control and its card names the Lakeshore alongside the K6517B.
- **PPMS Helper Utilities**: Plotter (transport & AC susceptibility channels), sequence visualizer, and time estimator.
- **Testing**: Frequency-scan outputs validated against reference WinDETA exports; new plot-refresh and UI regression tests.

---

### [1.0.5] - 2026-06-24  (Current)

- **Project Version**:v1.0.5: Lakeshore Enhancements, New Utilities & Core Fixes.

---

### [1.0.4] - 2026-03-15

- **Documentation Infrastructure**: Read the Docs integration added alongside the GitHub Pages site.
- **Preprint & Publications**: Preprint page launched with downloadable PDF via an automated LaTeX build workflow (GitHub Actions); Academia.edu link added.
- **CI/CD**: Workflows updated (Node.js 24 enforced, lint/docs workflow fixes, automated preprint build).
- **Maintenance**: Requirements/dependency updates and README badge refresh.

---

### [1.0.3] - 2026-01-26

- **GUI Theme**: Refreshed with the **Latte** theme — a lighter, warmer color palette for improved readability and reduced eye strain during long measurement sessions.
- **Distribution**: Published the official package to PyPI as `pica-suite`.
- **Documentation**: Full online documentation site launched via GitHub Pages.
- **Maintenance**: General bug fixes and performance improvements in measurement modules and real-time plotting logic.

---

### [1.0.0] - 2025-12-15 (Initial Public Release)

- **Versioning**: Version numbering has been reset from legacy development builds (v17.0) to v1.0.0 to standardize the package for public distribution and citation.
- **JOSS Submission**: Cleaned up documentation, performed fresh install tests, and completed other proofreading in preparation for submission to the Journal of Open Source Software (JOSS).

---

### [17.0] - 2025-12-02 

**Changed**

- **Directory Structure**: Refactored codebase into a professional project structure; moved numerous files to appropriate subdirectories for better organization.
- **Versioning**: Standardized version naming conventions. Adopted Semantic Versioning (v17.0).

**Research & Documentation**

- **Paper Draft**: Completed and presented the first draft of the research paper to Dr. Sudip Mukherjee.
- **Feedback**: Received critical feedback regarding the inclusion of ATMS (Advanced Transport Measurement Systems).

### [Community] - 2025-12-01

- **Launch**: PICA project posted on Hacker News.
---
### Version 15.0 
*Released: November 22, 2025*
*Status: Tested and operational. Minor cosmetic updates pending.*

**JOSS Submission & Professionalization**

-   **Code Cleanup**: Comprehensive refactoring and cleanup of the codebase to meet professional standards for JOSS submission.

**A Note on Recent Updates and Testing**:

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
-   **Plotter Utility Upgrade:** Enhanced `PlotterUtil_GUI.py` to support simultaneous multi-file plotting.

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
-   **Launcher Script Update:** Updated `PICA.py` to reflect versioning.

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
-   **Enhancement:** Upgraded to `PICA.py` with a two-column layout.
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