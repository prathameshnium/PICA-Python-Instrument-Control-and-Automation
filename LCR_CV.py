#LCR meter prg Jugad 22-6-23 (working)

import pyvisa
from pymeasure.instruments.agilent import AgilentE4980
import time
import numpy as np
import matplotlib.pyplot as plt

#---------------------------------
rm = pyvisa.ResourceManager()
my_instrument= rm.open_resource("GPIB::17")
LCR = AgilentE4980("GPIB::17")



my_instrument.timeout = 100000
my_instrument.read_termination = '\n'
my_instrument.write_termination = '\n'


my_instrument. write( '*RST; *CLS' )
my_instrument. write( ':DISP:ENAB' )

time. sleep( 2)

my_instrument. write( ':INIT:CONT' )
my_instrument. write( ':TRIG:SOUR EXT' )

time. sleep( 2)

my_instrument. write( ':APER MED' )
my_instrument. write( ':FUNC:IMP:RANGE:AUTO ON' )

time. sleep( 2)

my_instrument. write( ':MMEM EXT' )
time. sleep( 2)

my_instrument. write( ':MEM:DIM DBUF, ' , str(100))
time. sleep( 1)

my_instrument. write( ':MEM:FILL DBUF' )
time. sleep( 2)
my_instrument. write( ':MEM:CLE DBUF' )
time. sleep( 2)

#-----------------------------------------------------------------
#print(my_instrument.write( ':BIAS:VOLTage[:LEVel] ',str(5)))
time. sleep( 5)
my_instrument. write( ':INITiate[:IMMediate]')

#output=my_instrument. query_ascii_values( ':MEM:READ? DBUF' )

#output1=LCR.freq_sweep([1000], False)
#C_list+=output1[0]
print(my_instrument.query( ':BIAS:STATe?' ))

#print(my_instrument.write( ':BIAS:STATe?' ,str(1)))
print(my_instrument.write( ':BIAS:STATe ON' ))

#print(my_instrument.write( ':BIAS:VOLTage:LEVel 3'))
print(my_instrument.write( ':BIAS:VOLTage:LEVel '+str(2.1)))

#print(my_instrument.write( ':FUNCtion:SMONitor:VDC[:STATe] ON'))

print(my_instrument.query( ':BIAS:VOLTage:LEVel?'))
print(my_instrument.query( ':BIAS:STATe?' ))

#print(my_instrument.write( ':BIAS:VOLTage[:LEVel] ',str(volt_ind)))
