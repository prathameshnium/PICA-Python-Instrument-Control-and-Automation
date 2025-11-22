"""
Module: CV_KE4980A_Simple_Backend_v10.py
Purpose: GUI module for CV KE4980A Simple Backend v10.
"""

# prg for LCR Keysight E 4980 A
# supplimentry stuff 20-6-23

import pyvisa
from pymeasure.instruments.agilent import AgilentE4980
import time
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

# ---------------------------------------------------------------
# User Input
V = 2       # volt for loop (V)
V_step = 2  # interval between measurements (V)
freq = 1000  # freq in Hz
loop = 1
name = "Swastika_Test2_"
V_ac = 0.5
# ---------------------------------------------------------------

filename = "E:/Prathamesh/Python Stuff/CV/CV_Measurements/" + str(name) + "_freq_" + str(
    freq) + "_volt_" + str(V) + "_V_step_" + str(V_step) + "_Loops" + str(loop) + ".txt"

loop_ind_new = 0
protocol_list = []
V_list = []
C_list = []
loop_list = []

# ---------------------------------
try:
    rm = pyvisa.ResourceManager()
    my_instrument = rm.open_resource("GPIB::17")
    LCR = AgilentE4980("GPIB::17")

    my_instrument.timeout = 100000
    my_instrument.read_termination = '\n'
    my_instrument.write_termination = '\n'

    my_instrument.write('*RST; *CLS')
    my_instrument.write(':DISP:ENAB')
    time.sleep(2)
    my_instrument.write(':INIT:CONT')
    my_instrument.write(':TRIG:SOUR EXT')
    time.sleep(2)
    my_instrument.write(':APER MED')
    my_instrument.write(':FUNC:IMP:RANGE:AUTO ON')
    time.sleep(2)
    my_instrument.write(':MMEM EXT')
    time.sleep(2)
    my_instrument.write(':MEM:DIM DBUF, ' + str(100))
    time.sleep(1)
    my_instrument.write(':MEM:FILL DBUF')
    time.sleep(2)
    my_instrument.write(':MEM:CLE DBUF')
    time.sleep(3)
    print(my_instrument.write(':BIAS:STATe ON'))
    time.sleep(2)
    my_instrument.write(':VOLT:LEVEL ' + str(V_ac))
    time.sleep(2)
except Exception as e:
    print(f"Initialization error: {e}")
    # Continue for simulation purposes, or exit in real usage

# ---------------------------------

# LCR_fcn for the actual measurements


def LCR_fcn(volt_ind):
    # --- FIX: Removed unused globals ---
    global v1
    global output1

    my_instrument.write(':BIAS:VOLTage:LEVel ' + str(volt_ind))
    time.sleep(5)
    my_instrument.write(':INITiate[:IMMediate]')
    time.sleep(2)

    output1 = LCR.values(":FETCh:IMPedance:FORMatted?")
    time.sleep(2)

    if output1:
        C_list.append(output1[0])

    v1 = my_instrument.query(':BIAS:VOLTage:LEVel?')
    V_list.append(v1)
    time.sleep(4)

    print(
        "Output: " +
        str(output1) +
        "    |  Volt : " +
        str(v1) +
        "   |  Loop: " +
        str(loop_ind_new))

# Proto_fcn for the measurements protocol


def Proto_fcn():
    global loop_ind_new
    loop_ind_new += 1

    # Protocol Steps
    for v_ind in np.arange(0, V + V_step, V_step):
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("A")

    for v_ind in np.arange(V, 0 - V_step, -V_step):
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("B")

    for v_ind in np.arange(0, -V - V_step, -V_step):
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("C")

    for v_ind in np.arange(-V, 0 + V_step, V_step):
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("D")

# Loop_fcn for the looping number of times


def Loop_fcn(loop):
    for loop_ind in range(loop):
        Proto_fcn()


if __name__ == "__main__":
    try:
        Loop_fcn(loop)

        my_instrument.write(':MEM:CLE DBUF')
        my_instrument.write(':DISP:PAGE MEAS')
        time.sleep(1)
        LCR.shutdown()

        data_dict = {
            'Volt': V_list,
            'Cp': C_list,
            'Loop': loop_list,
            'Protocol': protocol_list}
        df = pd.DataFrame(data_dict)

        try:
            df.to_csv(filename, sep=',', index=False, encoding='utf-8')
            print(f"Data saved to {filename}")
        except Exception:
            df.to_csv("LCR_Backup.csv", index=False)
            print("Saved to LCR_Backup.csv")

        if V_list and C_list:
            plt.scatter(V_list, C_list)
            plt.title("Cp vs V")
            plt.show()

    except Exception as e:
        print(f"Error: {e}")
