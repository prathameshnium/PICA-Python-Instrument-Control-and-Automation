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

#---------------------------------


def LCR_fcn (volt_ind):

    global v1
    global V_list
    global output1
    global C_list
    global Volt
    v1=[]
    V_list=[]
    output1=[]
    C_list=[]

    #my_instrument. write( ':VOLT:LEVEL ' , str(volt_ind))
    print(my_instrument.write( ':BIAS:VOLTage[:LEVel] ',str(5)))
    time. sleep( 5)
    my_instrument. write( ':INITiate[:IMMediate]')

    #output=my_instrument. query_ascii_values( ':MEM:READ? DBUF' )

    output1=LCR.freq_sweep([1000], False)
    C_list+=output1[0]
    v1=my_instrument.query( ':VOLT:LEVEL?' )
    print(my_instrument.write( ':BIAS:VOLTage[:LEVel] ',str(volt_ind)))
    #:LIST:BIAS:VOLTage?

    V_list.append(v1)
    time. sleep( 2)



LCR_fcn(4)

time. sleep( 2)
print(C_list)
print(V_list)

my_instrument. write( ':MEM:CLE DBUF' )
my_instrument. write( ':DISP:PAGE MEAS' )
time. sleep( 1)
LCR.shutdown()


