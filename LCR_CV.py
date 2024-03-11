#-------------------------------------------------------------------------------
# Name:        module3
# Purpose:
#
# Author:      Instrument-DSL
#
# Created:     21/06/2023
# Copyright:   (c) Instrument-DSL 2023
# Licence:     <your licence>
#-------------------------------------------------------------------------------

def main():
    pass

if __name__ == '__main__':
    main()
import pymeasure
import time

import pyvisa
rm = pyvisa.ResourceManager()

print( rm. list_resources())

my_instrument= rm.open_resource("GPIB::17")
my_instrument.timeout = 100000

output_signals=[]

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





for ind in range(5):
    my_instrument. write( ':VOLT:LEVEL ' , str(ind))
    time. sleep( 2)
    my_instrument. write( ':INITiate[:IMMediate]')


    #output=my_instrument. query_ascii_values( ':MEM:READ? DBUF' )
    print(my_instrument.query( ':VOLT:LEVEL?' ))


    print(output)
    output_signals.append(output[0])

print(output_signals)
time. sleep( 2)

my_instrument. write( ':MEM:CLE DBUF' )
my_instrument. write( ':DISP:PAGE MEAS' )



