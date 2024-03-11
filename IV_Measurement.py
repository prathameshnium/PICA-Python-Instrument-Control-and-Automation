import pymeasure
import numpy
import matplotlib.pyplot as plt
import pyvisa
#print(pymeasure.__version__)
from pymeasure.instruments.keithley import Keithley2400


rm1 = pyvisa.ResourceManager()


#print(dir(pyvisa.ResourceManager()))
#print(dir(pymeasure.instruments.list_resources))


keithley_2182= rm1.open_resource("GPIB::7")
keithley_2182.write("*rst; status:preset; *cls")




keithley = Keithley2400("GPIB::4")
lcur=[]
lvol=[]
interval_in_ms = 1
number_of_readings = 2


keithley.apply_current()                # Sets up to source current
keithley.source_current_range = 1e-3   # Sets the source current range to 1 mA
keithley.compliance_voltage = 10        # Sets the compliance voltage to 10 V
keithley.source_current = 0             # Sets the source current to 0 mA
keithley.enable_source()                # Enables the source output

keithley.measure_voltage()              # Sets up to measure voltage
#keithley.measure_current()

#for cur in numpy.linspace(5e-4,10e-4,5) :
for cur in numpy.linspace(5e-4,10e-4,1) :


    keithley.ramp_to_current(cur)

    b=keithley_2182.write("status:measurement:enable 512; *sre 1")
    c=keithley_2182.write("sample:count %d" % number_of_readings)
    d=keithley_2182.write("trigger:source bus")
    e=keithley_2182.write("trigger:delay %f" % (interval_in_ms))
    f=keithley_2182.write("trace:points %d" % number_of_readings)
    i=keithley_2182.write("trace:feed sense1; feed:control next")
    keithley_2182.write("initiate")
    keithley_2182.assert_trigger()
    keithley_2182.wait_for_srq()
    voltages = keithley_2182.query_ascii_values("trace:data?")
    print("Average voltage: ", sum(voltages) / len(voltages))
    print(voltages)
    keithley_2182.query("status:measurement?")
    keithley_2182.write("trace:clear; feed:control next")

    # Ramps the current to 0.5 mA]
    #lcur.append(keithley.current)
    #lvol.append(keithley.voltage)
    v_avr=sum(voltages) / len(voltages)
    #print(vavg)

    lvol.append(v_avr)



   # print(str(keithley.voltage) + "   " +str(keithley.current))
                # Prints the voltage in Volts
    #print(keithley.current)
#print(lcur)
print(cur)
print(lvol)

plt.plot(numpy.linspace(5e-4,10e-4,5),lvol)
plt.show()




keithley.shutdown()                     # Ramps the current to 0 mA and disables output

