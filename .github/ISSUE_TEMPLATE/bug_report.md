---
name: Bug report (Windows)
about: Something in PICA does not work as expected on Windows. For Linux, use "Linux report" instead.
title: '[BUG] <measurement module>: <short description>'
labels: bug
assignees: ''
---

**This template is for Windows**, the platform PICA is validated on with real
instruments. **On Linux, please use the
[Linux report](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues/new?template=linux_report.md)
template instead**; it asks for the VISA backend and permission details that
Linux problems usually come down to.

Please fill in as much as you can; the sections marked (required) are the
ones we cannot work without.

## Measurement module (required)

- Measurement module: [the script file name, e.g. `RT_K2400_L350_T_Control_GUI.py`, or the launcher label, e.g. "K2400 R-T"; for a launcher or utility problem, name that instead]
- Instruments the module uses: [e.g. Keithley 2400 + Lakeshore 350]
- How it was started: [Launcher v1 `pica-gui` / Launcher v2 `run_pica_v2.py` / the script directly / portable `.exe`]
- Stage reached: [module window opens / instrument found / measurement started / measurement finished]

## Describe the bug (required)

A clear and concise description of what the bug is.

## To reproduce

The steps you took, including the parameters you entered (sample name, temperature range, current, etc.).

## Expected behaviour

What you expected to happen instead.

## PICA console log (required for module problems)

Every measurement module has a scrolling console at the bottom of its control
window. Click in it, press Ctrl+A, Ctrl+C, and paste the text here. The
Diagnostics and SCPI Console tools have a "Save Log As..." button instead;
attach that file.

```text
paste the console log here
```

## Terminal output

If PICA was started from a terminal or command prompt, paste everything it
printed, especially any Python traceback:

```text
paste the terminal output here
```

## System (required)

- Windows version: [e.g. Windows 11 23H2, Windows 10 22H2]
- Python: [output of `python --version`]
- PICA version and install method: [output of `pip show pica-suite`; or git commit if installed from source; or portable `.exe` version]
- VISA driver: [e.g. NI-VISA 2024 Q3, Keysight IO Libraries 2024, pyvisa-py]
- GPIB interface: [e.g. NI PCI-GPIB, NI GPIB-USB-HS, Keysight 82357B, none]

Optional but very helpful: open **Python Environment Diagnostics**,
**System and Driver Diagnostics** and **Communication Interface Diagnostics**
from the launcher, click "Save Log As..." in each, and attach the files.

## Instrument

- Model: [e.g. Keithley 2400, Lakeshore 350]
- Connection: [GPIB / USB / LAN / serial]
- VISA address as shown in the module's dropdown: [e.g. `GPIB0::24::INSTR`]

## Screenshots

If the problem is in how a window looks, a screenshot helps.
