
# PICA: Python Instrument Control & Automation

PICA is a collection of practical, ready-to-use Python scripts for controlling and automating common electrical and thermal characterization experiments in materials science and physics research. Each folder contains the necessary control logic and GUI for a specific measurement setup.

## ⚙️ Installation & Dependencies
To get started, clone the repository and install the required packages.

**Clone the repository:**
```bash
git clone [https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation.git)
cd PICA-Python-Instrument-Control-and-Automation
````

**Install the core dependencies:**
This project relies on a few key packages. Ensure they are installed in your Python environment.

```bash
pip install pyvisa pymeasure numpy pandas
```

## 🚀 Usage

Each directory is a self-contained measurement system. To run an experiment, navigate into the desired folder and execute the main Python script.

For example, to start the Low Resistance I-V measurement:

```bash
cd "Low Resistance (Delta Mode)"
python low_resistance_gui.py
```

*(Note: Script names may vary. Please see the specific folder for details.)*

## 🔬 Featured Measurement Systems

The following table summarizes the automated measurement systems included in this suite.

| Measurement Type          | Primary Instruments            | Key Measurements                  |
| ------------------------- | ------------------------------ | --------------------------------- |
| Low Resistance (Delta)    | Keithley 6221 / 2182A          | I-V, Resistance vs. Temperature   |
| Mid Resistance (4-Probe)  | Keithley 2400, Keithley 2182   | I-V, Resistance vs. Temperature   |
| High Resistance           | Keithley 6517B Electrometer    | I-V, Resistivity vs. Temperature  |
| LCR Measurements          | Keysight E4980A LCR Meter      | Capacitance vs. Voltage           |
| Pyroelectric Current      | Keithley 6517B, Lakeshore      | Pyro. Current vs. Temperature     |
| AC Transport              | Lock-in Amplifier (e.g., SR830)| AC Resistance                     |
| Temperature Control       | Lakeshore 340/350              | Ramping and environmental control |

## 📚 Manuals

A collection of official instrument manuals is available in the `_assets/Manuals` directory for reference.

## ✍️ Author & Acknowledgment

Developed by **Prathamesh Deshmukh** | Vision & Guidance by **Dr. Sudip Mukherjee**

*UGC-DAE Consortium for Scientific Research, Mumbai Centre*

## 📜 License

This project is licensed under the MIT License. See the `LICENSE` file for details.

