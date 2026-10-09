'''
===============================================================================
 MODULE:       pica/module_info.py

 PURPOSE:      What every launchable PICA program does, in three short lines,
               and a fuzzy search over all of it.

               MODULE_INFO is read by the launcher v2 for two things:

                 * the hover card in Advanced Options -- rest the pointer on
                   a module row and a box says what the program measures,
                   which instruments it talks to and which fields it will ask
                   for, so a newcomer can tell the protocols apart before
                   launching one;
                 * the search box on the toolbar, which matches a query
                   against the module names, the catalogue descriptions, the
                   instrument names and these three lines.

               Nothing in here touches Tk or an instrument, so the whole file
               is testable with plain Python.

 WRITING AN ENTRY:
               Keyed by the SCRIPT_PATHS key in pica/main.py. Three lines,
               each a sentence or a short list, each kept short -- the card
               must fit beside the pointer, not replace the manual:
                 'what'        the measurement and the protocol (what moves,
                               what is read, who drives the temperature);
                 'instruments' every instrument the script opens, written out
                               in full ("Keithley 2400", not "K2400") because
                               the card is for someone who has not learned
                               the shorthand yet;
                 'inputs'      the fields the program asks for, in the order
                               its form shows them.
               An optional 'note' carries the one warning worth reading
               before launch (experimental, writes no file, needs a cable).

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre
===============================================================================
'''
import difflib
import os
import re

# -----------------------------------------------------------------------------
#  Shared phrases
# -----------------------------------------------------------------------------
# Most measurement modules come in three thermometer editions (Lakeshore 350,
# Lakeshore 340, Cryo-con 34) and two temperature roles (T Control: the module
# drives the controller; T Sensing: it only reads it). The sentences below are
# the pieces those editions share, so the entries say the same thing the same
# way and a correction lands in one place.
_L350 = "Lakeshore 350"
_L340 = "Lakeshore 340"
_CC34 = "Cryo-con 34"

_T_CONTROL = ("The module drives the {t} itself: it ramps from the start to "
              "the end temperature at the rate you set and reads the sample "
              "along the way. The heater is switched off when it finishes "
              "or is stopped.")
_T_SENSING = ("The {t} is only read, never commanded: the PPMS, a dewar "
              "warm-up or any other system drives the temperature and every "
              "point is stamped with the temperature it was taken at.")

_RAMP_INPUTS = ("start and end temperature (K); ramp rate (K/min); safety "
                "cutoff (K)")
_VISA = "VISA addresses (picked from a scan)"


def _t_control(t):
    return _T_CONTROL.format(t=t)


def _t_sensing(t):
    return _T_SENSING.format(t=t)


def _entry(what, instruments, inputs, note=None):
    info = {'what': what, 'instruments': instruments, 'inputs': inputs}
    if note:
        info['note'] = note
    return info


# -----------------------------------------------------------------------------
#  The entries
# -----------------------------------------------------------------------------
MODULE_INFO = {}


def _add(keys, what, instruments, inputs, note=None):
    """Register one entry under one or several SCRIPT_PATHS keys."""
    if isinstance(keys, str):
        keys = [keys]
    for key in keys:
        MODULE_INFO[key] = _entry(what, instruments, inputs, note)


# ---- Delta mode: Keithley 6221 + 2182 --------------------------------------
_DELTA = ("Keithley 6221 (current source), Keithley 2182 (nanovoltmeter, "
          "on the 6221's RS-232 link)")
_DELTA_WHAT = ("Delta mode: the current is reversed at every point and the "
               "two voltage readings are averaged, which cancels thermal "
               "EMFs in the leads and contacts. ")

_add("Delta Mode I-V Sweep",
     "I-V sweep at a fixed temperature. " + _DELTA_WHAT +
     "Run it first to check that the contacts are ohmic.",
     _DELTA,
     "sample name; 6221 GPIB address; sweep type (linear, log or custom "
     "list); start and stop current (µA) and number of points, or the list; "
     "step delay and initial settle delay (s); compliance (V)")

for _key, _t in (("Delta Mode R-T", _L350), ("Delta Mode R-T (L340)", _L340)):
    _add(_key,
         "Resistance against temperature. " + _DELTA_WHAT + _t_control(_t),
         _DELTA + ", " + _t,
         "sample name; " + _RAMP_INPUTS + "; applied current (A); "
         "compliance (V); " + _VISA +
         ("; Lakeshore input channel" if _t == _L340 else ""))

for _key, _t in (("Delta Mode R-T (T_Sensing)", _L350),
                 ("Delta Mode R-T (T_Sensing, L340)", _L340),
                 ("Delta Mode R-T (T_Sensing, CC34)", _CC34)):
    _add(_key,
         "Resistance against temperature. " + _DELTA_WHAT + _t_sensing(_t),
         _DELTA + ", " + _t,
         "sample name; applied current (A); compliance (V); " + _VISA +
         ("; sensor input channel" if _t != _L350 else ""))

# ---- Keithley 2400 alone ----------------------------------------------------
_K2400 = "Keithley 2400 SourceMeter"
_add("K2400 I-V",
     "I-V sweep at a fixed temperature: the Keithley 2400 sources the current "
     "and measures the voltage in the same instrument (two- or four-wire). "
     "Linear sweeps, hysteresis loops or a custom current list.",
     _K2400,
     "sample name; sweep type; max and step current (µA), or a custom list; "
     "number of loops; compliance (V); delay per point (s); 2400 VISA address")

_add("K2400 Direct Control",
     "Bench workbench, not a scan: hold one operating point on the Keithley "
     "2400, set every source and measure parameter by hand and watch a live "
     "reading.",
     _K2400,
     "VISA address; never-exceed limits (V, A); source mode (current or "
     "voltage) and level; compliance; source and measure ranges; "
     "integration (PLC); source delay; reading count",
     "Writes no data file.")

for _key, _t in (("K2400 R-T", _L350), ("K2400 R-T (L340)", _L340)):
    _add(_key,
         "Resistance against temperature with a single Keithley 2400 sourcing "
         "the current and reading the voltage. " + _t_control(_t),
         _K2400 + ", " + _t,
         "sample name; " + _RAMP_INPUTS + "; source current (mA); "
         "compliance (V); logging delay (s); " + _VISA +
         ("; Lakeshore input channel" if _t == _L340 else ""))

for _key, _t in (("K2400 R-T (T_Sensing)", _L350),
                 ("K2400 R-T (T_Sensing, L340)", _L340),
                 ("K2400 R-T (T_Sensing, CC34)", _CC34)):
    _add(_key,
         "Resistance against temperature with a single Keithley 2400 sourcing "
         "the current and reading the voltage. " + _t_sensing(_t),
         _K2400 + ", " + _t,
         "sample name; source current (mA); compliance (V); logging delay "
         "(s); " + _VISA + ("; sensor input channel" if _t != _L350 else ""))

# ---- Keithley 2400 + 2182 ---------------------------------------------------
_K2400_2182 = "Keithley 2400 (current source), Keithley 2182 (nanovoltmeter)"
_add("K2400_2182 I-V",
     "I-V sweep at a fixed temperature in a true four-wire arrangement: the "
     "Keithley 2400 sources the current and the Keithley 2182 reads the "
     "sample voltage with nanovolt resolution.",
     _K2400_2182,
     "sample name; sweep type; start, stop and step current (mA), or a "
     "custom list; number of loops; compliance (V); 2400 and 2182 VISA "
     "addresses")

for _key, _t in (("K2400_2182 R-T", _L350), ("K2400_2182 R-T (L340)", _L340)):
    _add(_key,
         "Resistance against temperature, four-wire, with the Keithley 2182 "
         "reading the voltage for better resolution than the 2400 alone "
         "(small features such as phase transitions). " + _t_control(_t),
         _K2400_2182 + ", " + _t,
         "sample name; " + _RAMP_INPUTS + "; source current (mA); "
         "compliance (V); logging delay (s); " + _VISA +
         ("; Lakeshore input channel" if _t == _L340 else ""))

for _key, _t in (("K2400_2182 R-T (T_Sensing)", _L350),
                 ("K2400_2182 R-T (T_Sensing, L340)", _L340),
                 ("K2400_2182 R-T (T_Sensing, CC34)", _CC34)):
    _add(_key,
         "Resistance against temperature, four-wire, with the Keithley 2182 "
         "reading the voltage. " + _t_sensing(_t),
         _K2400_2182 + ", " + _t,
         "sample name; source current (mA); compliance (V); logging delay "
         "(s); " + _VISA + ("; sensor input channel" if _t != _L350 else ""))

# ---- Keithley 6517B electrometer -------------------------------------------
_K6517B = "Keithley 6517B electrometer"
_add("K6517B I-V",
     "I-V sweep of a high-resistance sample at a fixed temperature: a "
     "voltage is applied and the leakage current, down to the pA and fA "
     "range, is read by the electrometer.",
     _K6517B,
     "sample name; sweep type; start and stop voltage (V) and number of "
     "points, or max and step voltage, or a custom list; number of loops; "
     "delay per point (s); 6517B VISA address")

for _key, _t in (("K6517B R-T", _L350), ("K6517B R-T (L340)", _L340)):
    _add(_key,
         "Resistance against temperature for insulators, ceramics and "
         "dielectric films: a fixed voltage is applied and the current is "
         "read after a settling delay. " + _t_control(_t),
         _K6517B + ", " + _t,
         "sample name; " + _RAMP_INPUTS + "; source voltage (V); settling "
         "delay (s); " + _VISA +
         ("; Lakeshore input channel" if _t == _L340 else ""))

for _key, _t in (("K6517B R-T (T_Sensing)", _L350),
                 ("K6517B R-T (T_Sensing, L340)", _L340),
                 ("K6517B R-T (T_Sensing, CC34)", _CC34)):
    _add(_key,
         "Resistance against temperature for high-resistance samples: a "
         "fixed voltage is applied and the current is logged. " +
         _t_sensing(_t),
         _K6517B + ", " + _t,
         "sample name; source voltage (V); logging delay (s); " + _VISA +
         ("; sensor input channel" if _t != _L350 else ""))

# ---- Pyroelectric -----------------------------------------------------------
for _key, _t in (("Pyroelectric Current", _L350),
                 ("Pyroelectric Current (L340)", _L340)):
    _add(_key,
         "Pyroelectric / TSDC current: the current a poled sample releases "
         "as it warms, read at zero bias down to the fA range. Integrating "
         "it gives the released charge and the remanent polarisation. " +
         _t_control(_t),
         _K6517B + ", " + _t,
         "sample name; " + _RAMP_INPUTS + "; " + _VISA +
         ("; Lakeshore input channel" if _t == _L340 else ""),
         "Pole the sample first with Voltage Polling (Bias). Shielding "
         "matters at these currents.")

_add("K6517B Polling (Bias)",
     "Poling step before a pyroelectric scan: hold a DC voltage on the sample "
     "and watch the leakage current settle against time. No temperature "
     "control.",
     _K6517B,
     "applied voltage (V); 6517B VISA address; Start (voltage on) and Stop "
     "(voltage off)")

# ---- Bench instruments ------------------------------------------------------
_add("K197A Monitor",
     "Passive logging of whatever the Keithley 197A bench multimeter is "
     "reading (DC/AC volts, amps or ohms) against time.",
     "Keithley 197A with its Model 1973A / 1972A IEEE-488 card",
     "sample name; VISA address; function; range")

_add("TPG361 Pressure Log",
     "Pressure against time from the Pfeiffer TPG 361 gauge controller, over "
     "USB (serial) or Ethernet. Not GPIB: the gauge never appears in the "
     "launcher's bus scan.",
     "Pfeiffer TPG 361 SingleGauge",
     "log file name; logging delay (s); gauge address (COM port, IP address "
     "or VISA string); baud rate (serial only)")

_add("AFG3022B Function Generator",
     "Direct control of the Tektronix AFG 3022B waveform generator: set each "
     "channel's waveform, frequency, amplitude and offset and switch the "
     "outputs on or off. A source for another module, not a measurement.",
     "Tektronix AFG 3022B (2 channels, 25 MHz)",
     "VISA address; channel; waveform; frequency (Hz); amplitude and its "
     "unit; offset (V); output load",
     "Writes no data file.")

# ---- Temperature utilities: Lakeshore 350 / 340 ------------------------------
_add("Lakeshore Temp Control",
     "Ramp the Lakeshore 350 to one target temperature at a set rate and log "
     "the temperature on the way. A utility for getting the sample where a "
     "measurement needs it.",
     _L350,
     "target temperature (K); ramp rate (K/min); heater range; logging "
     "delay (s); Lakeshore VISA address")

_add("Lakeshore 340 Temp Control",
     "Ramp the Lakeshore 340 to a target temperature and hold it, with the "
     "heater range chosen by hand or by PID zones. Logs the temperature on "
     "the way.",
     _L340,
     "control input (A/B); target temperature (K); ramp rate (K/min); heater "
     "range; PID zones or manual PID; Lakeshore VISA address")

for _key, _t in (("Lakeshore Step Control", _L350),
                 ("Lakeshore 340 Step Control", _L340)):
    _add(_key,
         "Step-wise temperature sequence on the " + _t + ": visits a list of "
         "setpoints in order, declares each stable after a continuous dwell "
         "inside the tolerance band, then moves on. Steps can be added or "
         "removed while it runs.",
         _t,
         "start, end and step (K), or a manual list; order (up / down); "
         "heater range; live PID (preset or by hand); VISA address" +
         ("; control input" if _t == _L340 else ""))

for _key, _t in (("Lakeshore Step Control (Advanced)", _L350),
                 ("Lakeshore 340 Step Control (Advanced)", _L340)):
    _add(_key,
         "The step-wise sequence on the " + _t + " with an adaptive approach "
         "rate, per-setpoint automatic PID, low-temperature rate caps and a "
         "hard safety abort temperature. For unattended runs.",
         _t,
         "start, end and step (K), or a manual list; order; approach mode "
         "(adaptive or fixed rate) and low-T caps; heater range; auto PID "
         "per setpoint; safety abort temperature (K); save directory; VISA "
         "address" + ("; control input" if _t == _L340 else ""))

_add("Lakeshore Direct Control",
     "Front panel of the Lakeshore 350 on screen: setpoint, ramp, heater "
     "range, PID, control input, zone table, input configuration and the "
     "safety temperature limit, each applied as you set it.",
     _L350,
     "VISA address; output and control input; setpoint and ramp; heater "
     "range; PID or preset; input type, units and filter",
     "Changes the instrument as you go. The TLIMIT and *RST controls are "
     "marked as dangerous for a reason.")

_add("Lakeshore 340 Direct Control",
     "Front panel of the Lakeshore 340 on screen: control loop setup, "
     "setpoint, ramp, heater range, PID zones, control limits, display and "
     "input configuration, each applied as you set it.",
     _L340,
     "VISA address; loop, control input and mode; setpoint and ramp; heater "
     "range; zone table; control limits; display fields; input type and "
     "filter",
     "Changes the instrument as you go.")

for _key, _t in (("Lakeshore Temp Monitor", _L350),
                 ("Lakeshore 340 Temp Monitor", _L340)):
    _add(_key,
         "Passive temperature log from the " + _t + " against time. Reads "
         "only; nothing on the controller is changed" +
         (" (apart from the optional heater-off at start)." if _t == _L340
          else "."),
         _t,
         "log file name; logging delay (s); Lakeshore VISA address" +
         ("; sensor input; heater off at start" if _t == _L340 else ""))

_add("Lakeshore Sensor Curve Loader",
     "Load a sensor calibration curve (Cernox, RuOx, diode ...) from a file "
     "into a user curve slot of a Lakeshore 340 or 350, step by step, with a "
     "read-only map of the slots first and an optional assignment to an "
     "input afterwards.",
     "Lakeshore 350 or Lakeshore 340",
     "model; VISA address; curve file; curve name and serial number; data "
     "format; temperature limit and coefficient; target user curve slot; "
     "optional input to put the curve on",
     "Writes to the instrument's curve memory; the erase step is explicit.")

for _key, _t in (("Lakeshore Sensor Curve Viewer", _L350),
                 ("Lakeshore 340 Sensor Curve Viewer", _L340)):
    _add(_key,
         "Browse and export the calibration curves the " + _t + " holds, and "
         "ask which curve each input uses. Read only: the program can send "
         "nothing but queries.",
         _t,
         "VISA address; curve slot to read")

# ---- Temperature utilities: Cryo-con 34 -------------------------------------
_add("Cryocon Direct Control",
     "Front panel of the Cryo-con 34 on screen: control loops, setpoint and "
     "ramp, heater range and load, input units and curves, display options, "
     "the error queue and a read-only self-test, each applied as you set it.",
     _CC34,
     "VISA address; loop, control type and source input; setpoint and ramp; "
     "heater range and load (ohm); input units and curve; display settings",
     "Changes the instrument as you go. Save to flash and reset are "
     "explicit buttons.")

_add("Cryocon Temp Monitor",
     "Passive temperature log from the Cryo-con 34 against time. Reads only.",
     _CC34,
     "log file name; logging delay (s); sensor channel; Cryo-con VISA address")

_add("Cryocon Sensor Curve Loader",
     "Load a sensor calibration curve from a file into a user table of the "
     "Cryo-con 34, step by step, with a read-only map of the sensor tables "
     "first and an optional assignment to an input channel afterwards.",
     _CC34,
     "VISA address; curve file; curve name; curve type and input type; "
     "multiplier and units; target table index; optional input channel",
     "Writes to the instrument's curve memory.")

_add("Cryocon Sensor Curve Viewer",
     "Browse and export the sensor curves the Cryo-con 34 holds, and ask "
     "which curve each input uses. Read only.",
     _CC34,
     "VISA address; table index to read")

# ---- Diagnostics ------------------------------------------------------------
_add("Cryocon Diagnostics",
     "Read-only survey of a Cryo-con 34: asks the instrument what each SCPI "
     "command really does and writes a log. For when a command times out "
     "and the manual does not say why.",
     _CC34,
     "VISA address; whether to include the deeper measurements",
     "Safe to run beside a live experiment; the one writing test is opt-in.")

_add("Python Environment Diagnostics",
     "Checks the Python underneath PICA: interpreter version and 32/64-bit, "
     "every dependency against the minimum the project pins, and whether an "
     "installed pica_suite is shadowing this working tree.",
     "none (touches no instrument)",
     "nothing; press Run")

_add("System and Driver Diagnostics",
     "What this computer has installed for talking to instruments: NI-488.2, "
     "NI-VISA, Keysight IO Libraries, and the bit-ness of each driver DLL.",
     "none (touches no instrument)",
     "which sections to run")

_add("Communication Interface Diagnostics",
     "What is physically there: GPIB cards and USB-GPIB adapters, serial and "
     "USB-serial ports, USB controllers, network interfaces, and the VISA "
     "addresses they add up to. No port is opened and no *IDN? is sent.",
     "none (finds interfaces, identifies nothing)",
     "which sections to run")

# ---- Keysight E4980A LCR meter ---------------------------------------------
_LCR = "Keysight E4980A precision LCR meter"
_LCR_COMMON = ("aperture; auto level control (ALC); cable length (m); "
               "LCR VISA address")

_add("LCR C-V Measurement",
     "Capacitance against DC bias at a fixed frequency: ferroelectric "
     "butterfly loops, depletion profiling and tunability. Can cycle "
     "0 → +V → −V → 0.",
     _LCR,
     "sample name; frequency (Hz); AC level (V); DC bias stop and step (V); "
     "cycle mode; delay per step (s); " + _LCR_COMMON)

_add("LCR Open/Short Correction",
     "Guided OPEN / SHORT correction of the E4980A before a dielectric "
     "campaign: run each correction, inspect the result at the verify "
     "frequencies, redo if needed. Not a sample measurement.",
     _LCR,
     "LCR VISA address; test level (Vrms, must match the scans); cable "
     "length (m); verify frequencies (Hz); temperature (K) for the file "
     "name",
     "The full preset erases ALL correction data on the meter.")

_add("LCR Frequency Scan",
     "Frequency sweep at one temperature, recording capacitance, "
     "permittivity, loss and the R-X impedance from 20 Hz to 2 MHz.",
     _LCR,
     "sample name; start and stop frequency (Hz) and points; AC level (V); "
     "DC bias (V); delay per step (s); " + _LCR_COMMON)

for _key, _t in (("LCR Temp. Scan (T_Control)", _L350),
                 ("LCR Temp. Scan (T_Control, L340)", _L340)):
    _add(_key,
         "Dielectric response against temperature at a list of fixed "
         "frequencies, the meter read continuously as the temperature "
         "ramps. " + _t_control(_t),
         _LCR + ", " + _t,
         "sample name; " + _RAMP_INPUTS + "; AC and DC bias (V); "
         "frequencies (Hz, comma-separated); delay per frequency (s); live "
         "plot frequency; " + _LCR_COMMON + "; Lakeshore VISA address" +
         ("; Lakeshore input channel" if _t == _L340 else ""))

for _key, _t in (("LCR Temp. Scan (T_Sensing)", _L350),
                 ("LCR Temp. Scan (T_Sensing, L340)", _L340),
                 ("LCR Temp. Scan (T_Sensing, CC34)", _CC34)):
    _add(_key,
         "Dielectric response against temperature at a list of fixed "
         "frequencies while the PPMS or another external ramp warms or cools "
         "the sample. " + _t_sensing(_t),
         _LCR + ", " + _t,
         "sample name; AC and DC bias (V); frequencies (Hz, comma-separated); "
         "delay per frequency (s); live plot frequency; " + _LCR_COMMON +
         "; thermometer VISA address" +
         ("; sensor input channel; heater off at start" if _t != _L350
          else "; heater off at start"),
         ("The hardened edition: reconnects by itself and writes every point "
          "to disk as it goes." if _t == _CC34 else None))

for _key, _t in (("LCR Temp. Step Freq. Scan (T_Control)", _L350),
                 ("LCR Temp. Step Freq. Scan (T_Control, L340)", _L340)):
    _add(_key,
         "A full frequency sweep at each of a list of temperature setpoints. "
         "The module holds each setpoint on the " + _t + " until it is "
         "stable, sweeps, then moves to the next. Stand-alone, no PPMS.",
         _LCR + ", " + _t,
         "setpoints (start, end, step or a manual list) and order; approach "
         "mode and rate; heater range; auto PID; AC and DC bias (V); "
         "frequency delay (s); max temperature (K); save directory; " +
         _LCR_COMMON + "; Lakeshore VISA address" +
         ("; Lakeshore input channel" if _t == _L340 else ""))

_add("LCR Field Step Freq. Scan (PPMS, manual H)",
     "Magnetic-field-dependent dielectric scan on the PPMS: you set the "
     "temperature and field by hand in MultiVu, pick the field in the list "
     "and press Measure for one E4980A frequency sweep per field, one file "
     "per field. The thermometer, if any, is only read.",
     _LCR + "; optional Lakeshore 350 or Cryo-con 34 (read only)",
     "nominal PPMS temperature (K); the list of fields (Oe) you will visit; "
     + _LCR_COMMON + "; instruments picked from a scan")

for _key, _t in (("PPMS Sync Freq. Scan", _L350),
                 ("PPMS Sync Freq. Scan (L340)", _L340),
                 ("PPMS Sync Freq. Scan (CC34)", _CC34)):
    _add(_key,
         "Frequency sweeps synchronised to a PPMS temperature sequence: the "
         "module watches the " + _t + " for each plateau, detects stability "
         "from the probe thermometer alone, runs a dense 40 Hz – 2 MHz sweep "
         "there, then waits for the next plateau. Never commands the "
         "temperature.",
         _LCR + ", " + _t + " (read only)",
         "setpoint list (start, end, step or manual) and order; initial "
         "wait; base-arm temperature and tolerance; frequency delay (s); "
         "save directory; " + _LCR_COMMON + "; thermometer VISA address and "
         "input channel")

for _key, _t in (("PPMS Dielectric Master", _L350),
                 ("PPMS Dielectric Master (L340)", _L340),
                 ("PPMS Dielectric Master (CC34)", _CC34)):
    _add(_key,
         "Unattended multi-day PPMS campaign in one run: for each field in "
         "the run list, wait for base, then measure a multi-frequency "
         "dielectric cycle continuously while the PPMS warms (Tscan); "
         "finally, temperature-stepped frequency scans (Fscan). Phases are "
         "inferred from the " + _t + " reading alone; the PPMS and the "
         "thermometer are never commanded.",
         _LCR + ", " + _t + " (read only)",
         "run list: field tag (Oe) and cooldown (h:mm) per run; Tscan "
         "frequencies and live-plot frequency; Fscan setpoints (start, end, "
         "step or manual) and tolerance; frequency delay (s); save "
         "directory; " + _LCR_COMMON + "; thermometer VISA address and input "
         "channel; phase-detection thresholds (advanced)",
         "Mirrors the reference MultiVu sequences; run the matching "
         "sequence on the PPMS.")

# ---- Novocontrol Alpha-AN ---------------------------------------------------
for _key, _bits in (("Alpha-AN Freq. Scan", ""),
                    ("Alpha-AN Freq. Scan (32-bit)",
                     " This edition drives the GPIB card through a 32-bit "
                     "Python and needs no VISA.")):
    _add(_key,
         "Broadband dielectric spectroscopy at a fixed temperature: the "
         "Alpha-AN with its ZG4 sample interface sweeps a frequency list and "
         "records impedance, permittivity and loss, exported in a "
         "WinDETA-compatible layout." + _bits,
         "Novocontrol Alpha-AN analyser with ZG4 sample interface",
         "sample name; electrode geometry (area in cm² or diameter in mm) "
         "and thickness (mm); ZG4 wire mode; AC voltage (Vrms); frequency "
         "list; initial delay (s); Alpha-AN GPIB address",
         "Experimental. The launcher never probes the Alpha-AN; see the "
         "Novocontrol GPIB runbook before the first run.")

# ---- AC transport with the SR830 lock-in -----------------------------------
_SR830_SET = "Keithley 6221 (AC current source), Stanford Research SR830 lock-in"
_SR830_NOTE = ("Experimental. The 6221 must supply the reference to the SR830 "
               "(Trigger Link line 3 to REF IN) or the readings are "
               "meaningless.")
_SR830_FRONT = ("sensitivity and time constant (or auto gain / auto phase); "
                "compliance (V); optional sample geometry; 6221 and SR830 "
                "VISA addresses")

_add("SR830 Lock-in Comms",
     "Direct control of the SR830: sensitivity, time constant, phase and "
     "reference, with X, Y, R and theta shown live. Use it to get the lock-in "
     "locked before a measurement.",
     "Stanford Research SR830 lock-in",
     "SR830 VISA address; poll interval (s); sample and operator for the "
     "log; which traces to show")

_add("SR830 AC Resistivity",
     "Four-probe AC resistance: the 6221 drives a small AC current and the "
     "SR830 reads the in-phase voltage at the drive frequency, rejecting "
     "drift, 1/f noise and mains pickup. Continuous against time, or a "
     "current or frequency sweep, at a fixed temperature.",
     _SR830_SET,
     "sample name; measurement mode; current amplitude (A rms); frequency "
     "(Hz); " + _SR830_FRONT,
     _SR830_NOTE)

_add("SR830 AC I-V",
     "AC current-amplitude sweep at a fixed frequency. A straight line "
     "through the origin means an ohmic contact and shows which current to "
     "use for the temperature scans.",
     _SR830_SET,
     "sample name; start and stop current (A rms) and points; frequency "
     "(Hz); phase offset (deg); " + _SR830_FRONT,
     _SR830_NOTE)

_add("SR830 AC Freq. Scan",
     "Frequency sweep at a fixed AC current. A flat R(f) is a plain "
     "resistance; a roll-off points to cable capacitance or contact "
     "impedance.",
     _SR830_SET,
     "sample name; current amplitude (A rms); start and stop frequency (Hz) "
     "and points (linear or log); " + _SR830_FRONT,
     _SR830_NOTE)

for _key, _t in (("SR830 AC R-T", _L350), ("SR830 AC R-T (L340)", _L340)):
    _add(_key,
         "AC resistance against temperature with the lock-in. " +
         _t_control(_t),
         _SR830_SET + ", " + _t,
         "sample name; current amplitude (A rms); frequency (Hz); " +
         _RAMP_INPUTS + "; input channel; heater range; " + _SR830_FRONT,
         _SR830_NOTE)

for _key, _t in (("SR830 AC R-T (T_Sensing)", _L350),
                 ("SR830 AC R-T (T_Sensing, L340)", _L340),
                 ("SR830 AC R-T (T_Sensing, CC34)", _CC34)):
    _add(_key,
         "AC resistance against temperature with the lock-in. " +
         _t_sensing(_t),
         _SR830_SET + ", " + _t,
         "sample name; current amplitude (A rms); frequency (Hz); input "
         "channel; heater off at start; " + _SR830_FRONT,
         _SR830_NOTE)

# ---- AC transport without a lock-in (Keithley 197A) ------------------------
_K197A_SET = "Keithley 6221 (AC current source), Keithley 197A multimeter on AC volts"
_K197A_NOTE = ("Experimental. No reference and no phase: the result is a "
               "magnitude and an upper bound. Use only when the voltage is "
               "well above the meter's noise floor.")
_K197A_FRONT = ("voltmeter range; compliance (V); optional sample geometry; "
                "6221 and 197A VISA addresses")

_add("K197A AC I-V",
     "AC current-amplitude sweep at a fixed frequency, read as a voltage "
     "magnitude by the 197A. Start at a current that gives a voltage the "
     "meter can resolve.",
     _K197A_SET,
     "sample name; start and stop current (A rms) and points; frequency "
     "(Hz); " + _K197A_FRONT,
     _K197A_NOTE)

_add("K197A AC Freq. Scan",
     "Frequency sweep at a fixed AC current. A roll-off at the top of the "
     "range is usually the meter's own passband, not the sample.",
     _K197A_SET,
     "sample name; current amplitude (A rms); start and stop frequency (Hz) "
     "and points; " + _K197A_FRONT,
     _K197A_NOTE)

for _key, _t in (("K197A AC R-T", _L350), ("K197A AC R-T (L340)", _L340)):
    _add(_key,
         "AC resistance magnitude against temperature without a lock-in. " +
         _t_control(_t),
         _K197A_SET + ", " + _t,
         "sample name; current amplitude (A rms); frequency (Hz); " +
         _RAMP_INPUTS + "; input channel; heater range; " + _K197A_FRONT,
         _K197A_NOTE)

for _key, _t in (("K197A AC R-T (T_Sensing)", _L350),
                 ("K197A AC R-T (T_Sensing, L340)", _L340),
                 ("K197A AC R-T (T_Sensing, CC34)", _CC34)):
    _add(_key,
         "AC resistance magnitude against temperature without a lock-in. " +
         _t_sensing(_t),
         _K197A_SET + ", " + _t,
         "sample name; current amplitude (A rms); frequency (Hz); input "
         "channel; heater off at start; " + _K197A_FRONT,
         _K197A_NOTE)

# ---- Utilities (PICA Utils) ------------------------------------------------
# Not measurements, but they are launchable and people look for them by
# name, so the search box finds them too. Grouped as in Tools > PICA Utils.
UTILITY_TOOLS = [
    # (label, SCRIPT_PATHS key, group)
    ("Plotter Utility", "Plotter Utility", "Plotting"),
    ("PPMS Plotter Utility", "PPMS Plotter Utility", "Plotting"),
    ("P-E Plotter", "PE Plotter", "Plotting"),
    ("Sequence Visualizer", "Sequence Visualizer", "PPMS Utilities"),
    ("PPMS Time Estimator", "PPMS Time Estimator", "PPMS Utilities"),
    ("MD Ratio Calculator", "MD Ratio Calculator", "PPMS Utilities"),
    ("GPIB / VISA Scanner", "GPIB Scanner", "Communication"),
    ("GPIB Scanner (32-bit)", "GPIB Scanner (32-bit)", "Communication"),
    ("SCPI Console", "SCPI Console", "Communication"),
    ("Quick Calc", "Quick Calc", "Calculators"),
    ("Time Utility", "Time Utility", "Calculators"),
    ("Unit Converter", "Unit Converter", "Calculators"),
    ("List Maker", "List Maker", "Calculators"),
]

_add("Plotter Utility",
     "Plot any PICA data file (or several) with the X and Y columns chosen "
     "from the header. The quick look at a finished run.",
     "none", "data files; X and Y columns")
_add("PPMS Plotter Utility",
     "Plot Quantum Design PPMS .dat files: M(T), M(H) and dielectric runs, "
     "with moment units and a sample mass for normalisation.",
     "none", "PPMS .dat files; plot mode; X and Y columns; moment units; "
     "sample mass")
_add("PE Plotter",
     "Plot ferroelectric P-E hysteresis loops from exported loop files.",
     "none", "loop files; X and Y columns")
_add("Sequence Visualizer",
     "Draw a PPMS MultiVu .seq sequence as temperature and field against "
     "time, with an estimate of when each step happens.",
     "none", "a .seq file; start time; initial temperature and field; "
     "per-step time estimates")
_add("PPMS Time Estimator",
     "Estimate how long a PPMS campaign will take: M(T), M(H) loops, a "
     "PPMS-synced dielectric scan or the full master protocol.",
     "none", "the measurement type and its parameters")
_add("MD Ratio Calculator",
     "Magnetodielectric ratio from two dielectric runs at different fields: "
     "loads the data read-only and writes the MD ratio to a new file.",
     "none", "a dielectric data file; frequency; which loss ratio to use")
_add("GPIB Scanner",
     "Scan the VISA / GPIB bus, send *IDN? to every address and list what "
     "answers, with the address guide. Also a place to send SCPI by hand.",
     "every instrument on the bus (read only)", "nothing; press Scan")
_add("GPIB Scanner (32-bit)",
     "The bus scanner for a 32-bit NI-488.2 install where VISA cannot load: "
     "drives the GPIB card directly through a 32-bit Python.",
     "every instrument on the bus (read only)", "nothing; press Scan")
_add("SCPI Console",
     "Type SCPI commands at one instrument and read the replies. For "
     "checking a command before it goes into a module.",
     "the one instrument you pick", "instrument address; timeout (ms); "
     "line terminator; the commands you type")
_add("Quick Calc",
     "A small calculator for the lab bench.", "none", "an expression")
_add("Time Utility",
     "Add, subtract and convert durations (h:m:s), for planning runs.",
     "none", "hours, minutes, seconds")
_add("Unit Converter",
     "Convert between units of the quantities PICA measures.",
     "none", "category; value; from and to units")
_add("List Maker",
     "Build a numeric list (linear, log, cycles, turning points) and paste it "
     "into any module's custom list field.",
     "none", "start, stop and point count; cycles; options; or any pasted "
     "list")


# -----------------------------------------------------------------------------
#  Look-up
# -----------------------------------------------------------------------------
def module_info(key, quick_catalog=None):
    """The MODULE_INFO entry for a SCRIPT_PATHS key, or an empty one.

    Never raises: a module that has been added to the catalogue but not yet
    described here still gets a hover card (with blanks) rather than a
    traceback in a Tk callback. Given the launcher's QUICK_CATALOG, a missing
    entry borrows the Quick Select description of the same script instead,
    so the two screens never contradict each other and nothing has to be
    written twice before a new module is at least described.
    """
    info = MODULE_INFO.get(key)
    if info is not None:
        return dict(info)
    fallback = {'what': "", 'instruments': "", 'inputs': ""}
    for qcat in (quick_catalog or []):
        for mod in qcat['modules']:
            for proto in mod['protocols']:
                if proto['key'] == key:
                    fallback['what'] = proto['desc']
                    fallback['instruments'] = ", ".join(mod['instruments'])
                    return fallback
    return fallback


# -----------------------------------------------------------------------------
#  Display names
# -----------------------------------------------------------------------------
# A catalogue label such as "I-V Sweep" or "R vs. T (T Control)" is clear
# inside its Advanced Options card, which names the instruments above it.
# Out of that context -- in the search results, or a card on its own -- it
# says nothing about which bench it runs on. display_name() puts the
# instruments in front, short but complete:
#
#     I-V Sweep            ->  Keithley 2400 — I-V Sweep
#     R vs. T (T Control)  ->  Keithley 2400 + 2182 — R vs. T (T Control, L350)
#     Temperature Ramp (L350)  ->  Lakeshore 350 — Temperature Ramp
#
# The thermometer is named once: in the label for a measurement module, in
# front for a temperature tool. Derived from the 'instruments' line of each
# MODULE_INFO entry, so a new module gets its name with nothing else to edit.
_THERMOMETERS = ("Lakeshore 350", "Lakeshore 340", "Cryo-con 34")
# How a name gives an instrument: maker and model, no more.
_SHORT_INSTRUMENT_NAMES = [
    ("Keysight E4980A precision LCR meter", "Keysight E4980A"),
    ("Keithley 2400 SourceMeter", "Keithley 2400"),
    ("Keithley 6517B electrometer", "Keithley 6517B"),
    ("Stanford Research SR830 lock-in", "SR830 lock-in"),
    ("Novocontrol Alpha-AN analyser with ZG4 sample interface",
     "Novocontrol Alpha-AN"),
    ("Keithley 197A multimeter on AC volts", "Keithley 197A"),
    ("Keithley 197A with its Model 1973A / 1972A IEEE-488 card",
     "Keithley 197A"),
    ("Pfeiffer TPG 361 SingleGauge", "Pfeiffer TPG 361"),
    ("Lakeshore 350 or Lakeshore 340", "Lakeshore 350 / 340"),
    ("optional Lakeshore 350 or Cryo-con 34", ""),
]
# A thermometer as a module label writes it. Longest first, so
# "L340 / L350" is taken whole rather than as "L340" and a stray "/ L350".
_LABEL_THERMOMETER = r"(L340 / L350|L350|L340|Cryocon 34|Cryo-con 34)"


def instrument_names(key):
    """The instruments a program opens, as short names.

    'Keithley 2400 (current source), Keithley 2182 (nanovoltmeter),
    Lakeshore 350'  ->  ['Keithley 2400 + 2182', 'Lakeshore 350'].
    Empty for a program that touches no instrument.
    """
    text = re.sub(r"\([^)]*\)", "", MODULE_INFO.get(key, {}).get('instruments', ""))
    for long_name, short_name in _SHORT_INSTRUMENT_NAMES:
        text = text.replace(long_name, short_name)
    parts = [p.strip() for p in re.split(r"[,;]", text) if p.strip()]
    if not parts or parts[0].lower().startswith(("none", "every", "the one")):
        return []
    names, last_maker = [], None
    for part in parts:
        words = part.split()
        maker = words[0] if len(words) > 1 else None
        if maker and maker == last_maker and names:
            names[-1] += " + " + " ".join(words[1:])     # Keithley 6221 + 2182
        else:
            names.append(part)
        last_maker = maker
    return names


def _without_thermometer(label):
    """'Step-wise Control (Basic, L350)' -> 'Step-wise Control (Basic)'.

    Also a leading one: 'Cryocon 34 Diagnostics' -> 'Diagnostics'.
    """
    text = re.sub(r"^" + _LABEL_THERMOMETER + r"\s+", "", label)
    text = re.sub(r"\s*,\s*" + _LABEL_THERMOMETER + r"\b", "", text)
    text = re.sub(r"\(\s*" + _LABEL_THERMOMETER + r"\s*,\s*", "(", text)
    text = re.sub(r"\s*\(\s*" + _LABEL_THERMOMETER + r"\s*\)", "", text)
    return text.strip()


def display_name(key, label, family=None):
    """A short, complete name for one program: instruments, then label."""
    label = label.rstrip("…").strip()
    names = instrument_names(key)
    measuring = [n for n in names if not n.startswith(_THERMOMETERS)]
    thermometers = [n for n in names if n.startswith(_THERMOMETERS)]
    if measuring:
        shown = label
        # A label that names no thermometer uses the Lakeshore 350; say so.
        if (thermometers and thermometers[0] == "Lakeshore 350"
                and not re.search(_LABEL_THERMOMETER, shown)):
            if "(T Control)" in shown:
                shown = shown.replace("(T Control)", "(T Control, L350)")
            elif family in ('control', 'sensing', 'master'):
                shown += " (L350)"
        # "Keithley 197A — 197A Reading Monitor": say the model once.
        first, _, rest = shown.partition(" ")
        if rest and any(first in n.split() for n in measuring):
            shown = rest
        return f"{' + '.join(measuring)} — {shown}"
    if thermometers:
        return f"{thermometers[0]} — {_without_thermometer(label)}"
    return label


# -----------------------------------------------------------------------------
#  Fuzzy search
# -----------------------------------------------------------------------------
# A small scorer rather than a dependency: the whole corpus is a hundred
# entries of a few hundred characters, so a word-by-word comparison is
# instant, and a launcher that needs an extra package to find its own modules
# would be a step backwards.
#
# Scoring, per query word, against one entry:
#   * substring of the entry's TITLE (its display name and label)  -> 100
#   * substring of the rest of its NAME (category, key, script)    ->  90
#     ("pyro" ranks PyroCurrent above the Voltage Polling module that
#     merely shares the Pyroelectric category)
#   * substring of the name with spaces and punctuation removed     ->  80
#     ("k2400rt" finds "K2400 R-T")
#   * substring of the entry's full TEXT (descriptions, inputs ...) ->  60
#   * close spelling of a name word (difflib ratio >= 0.8)         ->  55
#     ("resistence" finds "resistance", "cryocon" finds "cryo-con")
#   * close spelling of any other word                              ->  40
#   * characters in order within the squashed LABEL                 ->  30
#     ("dmrt" finds "Delta Mode R vs. T"; the label only, because a
#     four-letter query is a subsequence of almost any whole record)
# Every query word must score (AND), the entry's score is the sum, and the
# name-level hits carry a small bonus for shorter labels so the specific
# module outranks the category that merely mentions the same word.

_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)

# A few spellings that mean the same thing on this rack. Applied to the
# query, so "lcr" finds the E4980A modules and "ppms" the T Sensing ones.
SEARCH_ALIASES = {
    'lcr': ['e4980a', 'dielectric'],
    'e4980': ['e4980a'],
    'lockin': ['lock-in', 'sr830'],
    'nanovoltmeter': ['2182'],
    'sourcemeter': ['2400'],
    'electrometer': ['6517b'],
    'k2182': ['2182'],
    'k2400': ['2400'],
    'k6221': ['6221'],
    'k6517b': ['6517b'],
    'k197a': ['197a'],
    'ls350': ['lakeshore 350'],
    'ls340': ['lakeshore 340'],
    'l350': ['lakeshore 350'],
    'l340': ['lakeshore 340'],
    'cc34': ['cryocon', 'cryo-con'],
    'rt': ['r vs. t', 'r-t'],
    'iv': ['i-v'],
    'cv': ['c-v'],
    'resistivity': ['resistance'],
    'permittivity': ['dielectric'],
    'capacitance': ['dielectric', 'c-v'],
    'ferroelectric': ['pyroelectric', 'c-v'],
    'passive': ['t sensing'],
    'sensing': ['t sensing'],
    'control': ['t control'],
}


def _norm(text):
    """Lower-case, with the separators PICA's names use read as spaces."""
    text = (text or "").lower()
    text = text.replace("-", " ").replace("_", " ").replace("/", " ")
    text = text.replace("·", " ").replace("–", " ")
    return " ".join(text.split())


def _squash(text):
    """Letters and digits only: 'K2400 R-T' -> 'k2400rt'."""
    return _WORD_RE.sub("", (text or "").lower())


def _words(text):
    return [w for w in _WORD_RE.split((text or "").lower()) if len(w) >= 2]


def _is_subsequence(needle, hay):
    it = iter(hay)
    return all(ch in it for ch in needle)


def _fuzzy_set(word, vocabulary, cutoff=0.8):
    """Every word in the vocabulary spelt close to `word` (difflib >= cutoff).

    Run once per query word against the whole index's vocabulary, rather
    than once per entry: the corpus has about a thousand distinct words, so
    this is a single short pass per key press. The two cheap upper bounds
    (real_quick_ratio, quick_ratio) throw out almost every candidate before
    the full comparison runs. Words under four letters are left to the exact
    and initials tiers; a one-letter slip in "rt" or "iv" is a different
    word, not a typo.
    """
    if len(word) < 4 or " " in word:
        return frozenset()
    matcher = difflib.SequenceMatcher(None)
    matcher.set_seq2(word)
    close = set()
    for cand in vocabulary:
        if abs(len(cand) - len(word)) > 3:
            continue
        matcher.set_seq1(cand)
        if (matcher.real_quick_ratio() >= cutoff
                and matcher.quick_ratio() >= cutoff
                and matcher.ratio() >= cutoff):
            close.add(cand)
    return frozenset(close)


def build_search_index(catalog, quick_catalog, script_paths,
                       utility_tools=None):
    """One searchable record per launchable program.

    catalog       -- launcher v2 CATALOG (the Advanced Options cards)
    quick_catalog -- launcher v2 QUICK_CATALOG (carries the plain-language
                     descriptions, which are searched too)
    script_paths  -- PICALauncherApp.SCRIPT_PATHS (key -> file)
    utility_tools -- UTILITY_TOOLS, or None to leave the utilities out

    Each record: key, label, category, family, instruments, experimental,
    snippet, and the pre-computed lower-case fields the scorer reads.
    """
    # Every description Quick Select has for a script key, so a search for
    # "butterfly" lands on the C-V module even though no card says it.
    quick_text = {}
    for qcat in quick_catalog:
        for mod in qcat['modules']:
            for proto in mod['protocols']:
                quick_text.setdefault(proto['key'], []).extend(
                    [qcat['category'], mod['name'], mod['desc'],
                     proto['label'], proto['desc']])

    index = []
    seen = set()

    def add(label, key, category, family, cat_instruments, group,
            experimental=False):
        if key in seen:
            return
        seen.add(key)
        info = module_info(key)
        script = os.path.basename(script_paths.get(key, "") or "")
        shown = display_name(key, label, family)
        name_parts = [shown, label, category, key, script]
        text_parts = name_parts + [cat_instruments, group,
                                   info['what'], info['instruments'],
                                   info['inputs'], info.get('note', "")]
        text_parts += quick_text.get(key, [])
        if family:
            text_parts.append({'control': "T Control",
                               'sensing': "T Sensing",
                               'master': "Master Sequence"}[family])
        if experimental:
            text_parts.append("experimental")
        name = _norm(" ".join(name_parts))
        title = _norm(shown + " " + label)
        text = _norm(" ".join(text_parts))
        index.append({
            'key': key,
            'label': label,
            'name': shown,
            'category': category,
            'family': family,
            'group': group,
            'instruments': info['instruments'] or cat_instruments,
            'experimental': experimental,
            'snippet': info['what'],
            'script': script,
            '_name': name,
            '_title': title,
            '_name_squashed': _squash(name),
            '_label_squashed': _squash(label),
            '_shown_squashed': _squash(shown),
            '_name_words': frozenset(_words(name)),
            '_text': text,
            '_text_words': frozenset(_words(text)),
        })

    for cat in catalog:
        for label, key, family in cat['modules']:
            add(label, key, cat['category'], family, cat['instruments'],
                "Modules", bool(cat.get('experimental')))
    for label, key, group in (utility_tools or []):
        add(label, key, "PICA Utils · " + group, None, "", "Utilities")
    return index


def _expand_query(query):
    """Query words plus their aliases: a list of (word, alternatives)."""
    # "r-t", "i-v", "c-v": single letters joined by a hyphen are one word
    # here, not two letters too short to search on.
    query = re.sub(r"(?<![a-z0-9])([a-z])-([a-z])(?![a-z0-9])", r"\1\2",
                   (query or "").lower())
    terms = []
    for word in _words(_norm(query)):
        alts = [word]
        for alias in SEARCH_ALIASES.get(word, []):
            alts.append(_norm(alias))
        terms.append(alts)
    return terms


def _contains(word, text):
    """Substring test, stricter for short words and phrases.

    A word of one or two letters ("iv", "rt") must start a word, so "iv"
    does not find "drivers". A phrase ("r t", "r vs. t", from the alias
    table) must stand as whole words at both ends, so "r t" does not find
    "monitor t sensing". Anything else may sit anywhere ("2400" finds
    "k2400").
    """
    if " " in word:
        pattern = r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    if len(word) <= 2:
        return re.search(r"(?<![a-z0-9])" + re.escape(word), text) is not None
    return word in text


def _score_word(alts, entry, fuzzy):
    best = 0
    for word in alts:
        squashed = _squash(word)
        close = fuzzy.get(word, frozenset())
        if _contains(word, entry['_title']):
            score = 100
        elif _contains(word, entry['_name']):
            score = 90
        # Punctuation-blind ("k2400rt" finds "K2400 R-T"), so only for words
        # of three letters or more: "iv" squashed into "drivers" is noise.
        elif len(squashed) >= 3 and squashed in entry['_name_squashed']:
            score = 80
        elif _contains(word, entry['_text']):
            score = 60
        elif close & entry['_name_words']:
            score = 55
        elif close & entry['_text_words']:
            score = 40
        elif len(squashed) >= 3 and (
                _is_subsequence(squashed, entry['_label_squashed'])
                or _is_subsequence(squashed, entry['_shown_squashed'])):
            score = 30
        else:
            score = 0
        best = max(best, score)
    return best


def search_modules(index, query, limit=12):
    """Rank the index against a free-text query.

    Returns up to `limit` records (copies of the index entries, plus a
    'score'), best first. An empty or whitespace query returns []. Every
    word of the query must match somewhere in a record for it to appear.
    """
    terms = _expand_query(query)
    if not terms:
        return []
    vocabulary = set()
    for entry in index:
        vocabulary |= entry['_text_words']
    fuzzy = {word: _fuzzy_set(word, vocabulary)
             for alts in terms for word in alts}
    hits = []
    for entry in index:
        total = 0
        for alts in terms:
            s = _score_word(alts, entry, fuzzy)
            if s == 0:
                total = 0
                break
            total += s
        if total:
            # Shorter labels first among equals: the module itself, not the
            # longer sibling that happens to contain the same words.
            hits.append((total, -len(entry['name']), entry))
    hits.sort(key=lambda h: (-h[0], -h[1]))
    out = []
    for total, _neg, entry in hits[:limit]:
        rec = {k: v for k, v in entry.items() if not k.startswith('_')}
        rec['score'] = total
        out.append(rec)
    return out
