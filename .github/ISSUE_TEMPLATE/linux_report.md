---
name: Linux report
about: Report a problem (or a success) running PICA on Linux
title: '[Linux] <measurement module>: <short description>'
labels: linux
assignees: ''
---

Running PICA against real instruments from Linux has not been verified yet, so
both problems and successes are useful to hear about. Please fill in as much
as you can; the sections marked (required) are the ones we cannot work without.

## Measurement module (required)

- Measurement module: [the script file name, e.g. `RT_K2400_L350_T_Control_GUI.py`, or the launcher label, e.g. "K2400 R-T"; for a launcher or utility problem, name that instead]
- Instruments the module uses: [e.g. Keithley 2400 + Lakeshore 350]
- How it was started: [Launcher v1 `pica-gui` / Launcher v2 `run_pica_v2.py` / the script directly]
- Stage reached: [install / launcher opens / module window opens / instrument found / measurement started / measurement finished]

## What happened (required)

A clear description of what went wrong, or what worked.

## To reproduce

The steps you took, including the parameters you entered (sample name, temperature range, current, etc.).

## PICA console log (required for module problems)

Every measurement module has a scrolling console at the bottom of its control
window. Click in it, press Ctrl+A, Ctrl+C, and paste the text here. The
Diagnostics and SCPI Console tools have a "Save Log As..." button instead;
attach that file.

```text
paste the console log here
```

## Terminal output

Start PICA from a terminal so Python tracebacks are captured, then paste
everything the terminal printed:

```bash
source ~/pica-venv/bin/activate
pica-gui 2>&1 | tee pica-terminal.log
```

```text
paste the terminal output here
```

## System (required)

- Distribution and version: [e.g. Ubuntu 24.04, Fedora 41]
- Kernel: [output of `uname -r`]
- Desktop session: [e.g. GNOME on Wayland, KDE on X11, WSL2, headless with Xvfb]
- Python: [output of `python3 --version`]
- PICA version and install method: [output of `pip show pica-suite`; or git commit if installed from source]
- Tkinter present: [output of `python3 -c "import tkinter; print(tkinter.TkVersion)"`]

Optional but very helpful: open **Python Environment Diagnostics** and
**Communication Interface Diagnostics** from the launcher, click
"Save Log As..." in each, and attach the two files.

## VISA backend (required for instrument problems)

- Backend: [pyvisa-py / NI-VISA for Linux / other]
- Output of `pyvisa-info`:

```text
paste here
```

- Output of `python3 -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"`:

```text
paste here
```

## Instrument

- Model: [e.g. Keithley 2400, Lakeshore 350]
- Connection: [GPIB / USB-TMC / LAN / serial]
- For GPIB: linux-gpib version and card model, and whether `gpib_config` ran without errors
- For USB-TMC: whether a udev rule was added for the device
- For serial: the port used (e.g. `/dev/ttyUSB0`) and whether your user is in the `dialout` group (output of `groups`)

## Screenshots

If the problem is in how a window looks, a screenshot helps.
