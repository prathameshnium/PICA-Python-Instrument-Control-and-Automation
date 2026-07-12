---
myst:
  html_meta:
    description: "PICA is an open-source Python suite for laboratory instrument control and automation — GPIB/VISA and SCPI orchestration of Keithley, Keysight, Lakeshore, and Novocontrol instruments for cryogenic transport, resistivity, I-V, dielectric, and pyroelectric measurements."
    keywords: "Python instrument control, laboratory automation, GPIB, PyVISA, SCPI, Keithley, Keysight E4980A, Lakeshore 350, cryogenics, transport measurements"
---

# PICA Documentation

PICA (**Python Instrument Control and Automation**) is a modular, open-source Python suite for automating high-precision transport measurements. It supports a resistance range spanning **24 orders of magnitude** (10 nΩ – 10 PΩ), pyroelectric current detection down to 1 fA, and capacitance spectroscopy from 20 Hz – 2 MHz.

[![GitHub Release](https://img.shields.io/github/v/release/prathameshnium/PICA-Python-Instrument-Control-and-Automation)](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18377217.svg)](https://doi.org/10.5281/zenodo.18377217)
[![Read the Docs](https://readthedocs.org/projects/pica-python-instrument-control-and-automation/badge/?version=latest)](https://pica-python-instrument-control-and-automation.readthedocs.io/en/latest/)

---

## Quick Start

```bash
# Install
pip install .

# Launch GUI
pica-gui
```

---

## Key Features

- **Unified GUI** — Single launcher for all measurement modules
- **Wide resistance range** — 10 nΩ to 10 PΩ across four hardware configurations
- **Real-time plotting** — Live visualization during acquisition
- **Process isolation** — Backend crashes never affect the GUI
- **Open source** — MIT licensed, fully auditable SCPI commands

---

## Getting Help

- [Full User Manual](User_Manual.md)
- [GitHub Issues](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues)
- [Releases](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases)
- [Preprint](https://prathameshnium.github.io/PICA-Python-Instrument-Control-and-Automation/publications/)

---

```{toctree}
:maxdepth: 3
:caption: Contents

User_Manual
Instruments_Manuals_Lists
```