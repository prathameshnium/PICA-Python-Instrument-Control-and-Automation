import pymeasure
import numpy
import matplotlib.pyplot as plt
from time import sleep
import pyvisa
from pymeasure.instruments.keithley import Keithley2400


rm1 = pyvisa.ResourceManager()

keithley_2182= rm1.open_resource("GPIB::7")
keithley_2182.write("*rst; status:preset; *cls")




keithley_2400 = Keithley2400("GPIB::4")

sleep(5)

lcur=[]
lvol=[]
interval_in_ms = 1
number_of_readings = 2


keithley_2400.apply_current()                # Sets up to source current
keithley_2400.source_current_range = 1e-3   # Sets the source current range to 1 mA
keithley_2400.compliance_voltage = 10        # Sets the compliance voltage to 10 V
keithley_2400.source_current = 0             # Sets the source current to 0 mA
keithley_2400.enable_source()             # Enables the source output


sleep(5)
#keithley_2400.measure_voltage()              # Sets up to measure voltage
#keithley.measure_current()

for cur in numpy.linspace(-5e-4,5e-4,15) :




    keithley_2400.ramp_to_current(cur)

    sleep(5)

    b=keithley_2182.write("status:measurement:enable 512; *sre 1")
    c=keithley_2182.write("sample:count %d" % number_of_readings)
    d=keithley_2182.write("trigger:source bus")
    e=keithley_2182.write("trigger:delay %f" % (interval_in_ms))
    f=keithley_2182.write("trace:points %d" % number_of_readings)
    i=keithley_2182.write("trace:feed sense1; feed:control next")
    keithley_2182.write("initiate")
    keithley_2182.assert_trigger()
    keithley_2182.wait_for_srq()
    sleep(10)
    voltages = keithley_2182.query_ascii_values("trace:data?")
    keithley_2182.query("status:measurement?")
    keithley_2182.write("trace:clear; feed:control next")

    # Ramps the current to 0.5 mA]
    lcur.append(keithley_2400.current)
    #lvol.append(keithley.voltage)
    v_avr=sum(voltages) / len(voltages)

    lvol.append(v_avr)

    print(str(cur) + "   " +str(v_avr))
    sleep(5)




print(cur)
print(lvol)

plt.plot(numpy.linspace(-5e-4,5e-4,15),lvol)

plt.show()




keithley_2400.shutdown()                     # Ramps the current to 0 mA and disables output

