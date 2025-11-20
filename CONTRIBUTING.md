# Contributing to PICA

Thank you for your interest in PICA (Python-based Instrument Control and Automation)

As this is primarily a research project for the UGC-DAE CSR Mumbai Centre, our main goal is stability for our experiments. However, we welcome feedback and contributions from the scientific community.

## How to Report Bugs
If you encounter an error while running a measurement:
1.  Go to the [Issues tab](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues).
2.  Click "New Issue".
3.  Include the error message from the PICA console and which instrument you were using (e.g., Keithley 2400).

## How to Suggest Features
If you have a script for a new instrument (e.g., a lock-in amplifier) that you would like to include:
1.  Fork this repository.
2.  Add your script to a new folder (e.g., `SRS_830/`).
3.  Submit a Pull Request (PR) describing what the instrument does.

## Development Setup
To run the PICA launcher locally for development:
```bash
pip install -e .
python PICA_v6.py
