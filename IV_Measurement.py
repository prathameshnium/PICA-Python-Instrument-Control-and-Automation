import pymeasure
import numpy
import matplotlib.pyplot as plt
#print(pymeasure.__version__)
from pymeasure.instruments.keithley import Keithley2400




keithley = Keithley2400("GPIB::4")
lcur=[]
lvol=[]

keithley.apply_current()                # Sets up to source current
keithley.source_current_range = 1e-3   # Sets the source current range to 1 mA
keithley.compliance_voltage = 10        # Sets the compliance voltage to 10 V
keithley.source_current = 0             # Sets the source current to 0 mA
keithley.enable_source()                # Enables the source output

keithley.measure_voltage()              # Sets up to measure voltage
keithley.measure_current()

for cur in numpy.linspace(-5e-4,+5e-4,20) :
    keithley.ramp_to_current(cur)          # Ramps the current to 0.5 mA]
    lcur.append(cur)
    print(cur)
    #lcur.append(keithley.current)
    lvol.append(keithley.voltage)

   # print(str(keithley.voltage) + "   " +str(keithley.current))
                # Prints the voltage in Volts
    #print(keithley.current)

print(lvol)

plt.plot(numpy.linspace(-5e-4,+5e-4,20),lvol)
plt.show()




keithley.shutdown()                     # Ramps the current to 0 mA and disables output

