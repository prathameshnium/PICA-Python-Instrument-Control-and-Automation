#-------------------------------------------------------------------------------
# Name:        Delta test 3:
# Purpose:     Perform a Delta mode measurement with proper shutdown
# Author:      Instrument-DSL
# Created:     01/09/2024
#-------------------------------------------------------------------------------
import pyvisa
from time import sleep
import time

# user input
DELTA_CURRENT=0.002 # in amps
temperature=300 # temprory
Volt=[]
try:
    rm1 = pyvisa.ResourceManager()
    print(rm1.list_resources())
    keithley_6221= rm1.open_resource("GPIB0::13::INSTR")
    print(f"ID is : {keithley_6221.query('*IDN?')}")
    sleep(0.5)
    #temp_controller= rm1.open_resource("GPIB::15") # Lakeshore 350 (new)
    time.sleep(0.5)
    #print(f"ID is : {temp_controller.query('*IDN?')}")
    time.sleep(0.5)

    #initilization
    keithley_6221.write("*rst; status:preset; *cls")
    sleep(1)
    keithley_6221.write("UNIT V")
    sleep(1)
    keithley_6221.write(f"SOUR:DELT:HIGH {DELTA_CURRENT}") # Current in Amps
    sleep(1)
    keithley_6221.write("SOUR:DELT:ARM")
    sleep(1)
    keithley_6221.write("INIT:IMM")
    sleep(1)


#-----------------------------------------------------------------------------

except Exception as e:
    print(f"Initialization error : {e}")
def IV_Measure(DELTA_CURRENT):

    try:
        elapsed_time = time.time() - start_time
        #print(elapsed_time)
        #keithley_6221.write("TRACe:CLEar")
        #sleep(0.1)

        V_fresh = keithley_6221.query('SENSe:DATA:FRESh?')
        Volt.append(V_fresh) #voltage  lisT
        print(f"Voltage: {V_fresh}")
        #resistance = float(V_fresh/DELTA_CURRENT)

        print(f"{DELTA_CURRENT} A |{elapsed_time:.2f} s| {temperature} K| {V_fresh} V")
        keithley_6221.write("TRACe:CLEar")
        sleep(0.1)


        #------------------------------------------------------------------------

    except Exception as e:
        print(f"error : {e}")
        Check=False

    except KeyboardInterrupt:
        keithley_6221.write(f"SOUR:DELT:HIGH 0") # Current in Amps
        sleep(0.1)
        keithley_6221.write("SOUR:CLE")
        sleep(0.1)
        #keithley_6221.write("CURRent 0")
        keithley_6221.write("OUTPut OFF")
        sleep(0.1)




global Check
Check=True
start_time = time.time()

try:
    while Check:
        IV_Measure(DELTA_CURRENT)

except Exception as e:
    print(f"Initialization error : {e}")
    Check=False



