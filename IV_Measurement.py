
import pymeasure
import numpy
import pyvisa
import matplotlib.pyplot as plt
#print(pymeasure.__version__)



rm1 = pyvisa.ResourceManager()


#print(dir(pyvisa.ResourceManager()))
#print(dir(pymeasure.instruments.list_resources))


keithley = rm1.open_resource("GPIB::7")
keithley.write("*rst; status:preset; *cls")


interval_in_ms = 500
number_of_readings = 10

b=keithley.write("status:measurement:enable 512; *sre 1")
c=keithley.write("sample:count %d" % number_of_readings)
d=keithley.write("trigger:source bus")
e=keithley.write("trigger:delay %f" % (interval_in_ms / 1000.0))
f=keithley.write("trace:points %d" % number_of_readings)
i=keithley.write("trace:feed sense1; feed:control next")

keithley.write("initiate")
keithley.assert_trigger()
keithley.wait_for_srq()

voltages = keithley.query_ascii_values("trace:data?")
print("Average voltage: ", sum(voltages) / len(voltages))
vavg=sum(voltages) / len(voltages)
print(vavg)

print(voltages)

keithley.query("status:measurement?")
keithley.write("trace:clear; feed:control next")
