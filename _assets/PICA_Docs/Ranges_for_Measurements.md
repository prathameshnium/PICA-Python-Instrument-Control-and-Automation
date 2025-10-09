# PICA Project: Verified Instrument Specifications

Here is a meticulously verified summary of the key measurement specifications for the instruments used in the PICA project. All values have been cross-referenced with the provided instrument manuals to ensure accuracy.

---

### Keithley 6221 Current Source + 2182A Nanovoltmeter System (Low Resistance)
**Primary Use:** High-precision, low-resistance I-V and R-T measurements via **Delta Mode**.

| Parameter                 | Lower Limit                                       | Higher Limit    | Best Resolution                             |
| :------------------------ | :------------------------------------------------ | :-------------- | :------------------------------------------ |
| **Resistance**            | **~10 nΩ** (Practical limit set by system noise)  | **1 GΩ**        | **1 nV** (Voltage Resolution on 2182A)      |
| **Current Source (6221)**   | **100 fA**                                        | **105 mA**      | **100 fA** (on the 2 nA range)              |
| **Voltage Measure (2182A)** | **1 nV**                                          | **100 V**       | **1 nV** (on the 10 mV range)               |

---

### Keithley 2400 SourceMeter (Mid-Range Resistance)
**Primary Use:** Versatile four-probe I-V and R-T measurements for a broad range of materials.

| Parameter                      | Lower Limit (Source / Measure) | Higher Limit (Source / Measure) | Best Resolution (Source / Measure)   |
| :----------------------------- | :----------------------------- | :------------------------------ | :----------------------------------- |
| **Resistance**                 | **&lt; 0.2 Ω**                    | **&gt; 200 MΩ**                    | **100 µΩ** (on the 20Ω range)        |
| **Voltage**                    | **5 µV / 1 µV**                | **±210 V / ±210 V**             | **5 µV / 100 nV** (6.5-digit mode) |
| **Current**                    | **50 pA / 10 pA**              | **±1.05 A / ±1.05 A**           | **50 pA / 10 pA**                    |

---

### Keithley 6517B Electrometer (High Resistance & Pyroelectric)
**Primary Use:** Characterizing highly insulating materials and measuring pyroelectric currents.

| Parameter                      | Lower Limit                               | Higher Limit                    | Best Resolution                                |
| :----------------------------- | :---------------------------------------- | :------------------------------ | :--------------------------------------------- |
| **Resistance**                 | **1 Ω**                                   | **&gt; 10 PΩ** ($10^{16}$ Ω)        | **Derived** (from I-measure)                   |
| **Current (Pyroelectric)**     | **10 aA** ($10 \times 10^{-18}$ A)         | **20 mA**                       | **10 aA**                                      |
| **Voltage**                    | **1 µV**                                  | **200 V**                       | **1 µV**                                       |
| **Charge**                     | **10 fC**                                 | **2 µC**                        | **10 fC**                                      |

---

### Keysight E4980A Precision LCR Meter (C-V Measurements)
**Primary Use:** Capacitance-Voltage (C-V) and impedance characterization.

| Parameter            | Lower Limit                                | Higher Limit                               | Best Resolution                            |
| :------------------- | :----------------------------------------- | :----------------------------------------- | :----------------------------------------- |
| **Basic Accuracy**   | Varies with conditions                     | Varies with conditions                     | **0.05%** (under optimal conditions)       |
| **Frequency**        | **20 Hz**                                  | **2 MHz**                                  | **Tiered** (e.g., 0.01 Hz at low freq.)    |
| **DC Bias**          | **-40 V**                                  | **+40 V**                                  | **0.3 mV** (Requires Option 001)           |

---

### Lake Shore 350 Temperature Controller
**Primary Use:** Precise temperature control and monitoring for temperature-dependent measurements.

| Sensor Type      | Lower Temp. Limit | Higher Temp. Limit | Best Resolution (Temp)     | Notes                                      |
| :--------------- | :---------------- | :----------------- | :------------------------- | :----------------------------------------- |
| **Diode (DT-670)** | **1.4 K**         | **500 K**          | **&lt; 0.1 mK**               | At cryogenic temperatures                  |
| **Platinum RTD**   | **14 K**          | **873 K**          | **&lt; 1 mK**                 | At cryogenic temperatures                  |