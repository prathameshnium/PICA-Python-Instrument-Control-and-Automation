#basic test

#import pymeasure
from time import sleep
import pyvisa
#from pymeasure.instruments.keithley import Keithley2400


rm = pyvisa.ResourceManager()
print(rm.list_resources())

#print(dir(pyvisa.ResourceManager()))
#print(dir(pymeasure.instruments.list_resources))
