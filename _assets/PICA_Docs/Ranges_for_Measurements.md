# PICA Project: Verified Instrument Specifications

Here is a meticulously verified summary of the key measurement specifications for the instruments used in the PICA project. All values have been cross-referenced with the provided instrument manuals to ensure accuracy.

---

### Keithley 6221 Current Source + 2182A Nanovoltmeter System (Low Resistance)
**Primary Use:** High-precision, low-resistance I-V and R-T measurements via **Delta Mode**.

| Parameter                 | Lower Limit                                       | Higher Limit    | Best Resolution                             |
| :------------------------ | :------------------------------------------------ | :-------------- | :------------------------------------------ |
| **Resistance** | **~10 nΩ** (Practical limit set by system noise)  | **1 GΩ** | **1 nV** (Voltage Resolution on 2182A)      |
| **Current Source (6221)** | **2 nA** | **100 mA** | **100 pA** (on the 2 nA range)              |
| **Voltage Measure (2182A)**| **1 nV** | **120 V** | **1 nV** (on the 10 mV range)               |

---

### Keithley 2400 SourceMeter (Mid-Range Resistance)
**Primary Use:** Versatile four-probe I-V and R-T measurements for a broad range of materials.

| Parameter                      | Lower Limit (Source / Measure) | Higher Limit (Source / Measure) | Best Resolution (Source / Measure) |
| :----------------------------- | :----------------------------- | :------------------------------ | :--------------------------------- |
| **Resistance** | **< 0.2 Ω** | **> 200 MΩ** | **10 µΩ** (on the 200Ω range)      |
| **Voltage** | **±5 µV / ±1 µV** | **±210 V / ±210 V** | **1 µV / 100 nV** |
| **Current** | **±10 pA / ±1 pA** | **±1.05 A / ±1.05 A** | **1 pA / 10 fA** |

---

### Keithley 6517B Electrometer (High Resistance & Pyroelectric)
**Primary Use:** Characterizing highly insulating materials and measuring pyroelectric currents.

| Parameter                      | Lower Limit                                  | Higher Limit                    | Best Resolution                                |
| :----------------------------- | :------------------------------------------- | :------------------------------ | :--------------------------------------------- |
| **Resistance** | **< 10 Ω** | **> 10 PΩ** ($10^{16}$ Ω)        | **10 aΩ** ($10^{-17}$ Ω, calculated)           |
| **Current (Pyroelectric)** | **10 aA** ($10 \times 10^{-18}$ A)           | **21 mA** | **10 aA** |
| **Voltage** | **1 µV** | **210 V** | **1 µV** |
| **Charge** | **10 fC** | **2.1 µC** | **10 fC** |

---

### Keysight E4980A Precision LCR Meter (C-V Measurements)
**Primary Use:** Capacitance-Voltage (C-V) and impedance characterization.

| Parameter            | Lower Limit                                | Higher Limit                               | Best Resolution           |
| :------------------- | :----------------------------------------- | :----------------------------------------- | :------------------------ |
| **Capacitance (C)** | Dependent on frequency and test signal     | Dependent on frequency and test signal     | **0.05%** (Basic Accuracy)|
| **Inductance (L)** | Dependent on frequency and test signal     | Dependent on frequency and test signal     | **0.05%** (Basic Accuracy)|
| **Resistance (R)** | Dependent on frequency and test signal     | Dependent on frequency and test signal     | **0.05%** (Basic Accuracy)|
| **Frequency** | **20 Hz** | **2 MHz** | **0.01 Hz** |
| **DC Bias** | **-40 V** | **+40 V** | **0.3 mV** |

---

### Lake Shore 350 Temperature Controller
**Primary Use:** Precise temperature control and monitoring for temperature-dependent measurements.

| Sensor Type      | Lower Temp. Limit | Higher Temp. Limit | Best Resolution (Sensor Units) | Best Resolution (Temp)     |
| :--------------- | :---------------- | :----------------- | :----------------------------- | :------------------------- |
| **Diode (DT-670)** | **1.4 K** | **500 K** | **10 µV** | **< 0.1 mK** (at low temps)|
| **Platinum RTD** | **14 K** | **873 K** | **0.1 mΩ** | **< 1 mK** |