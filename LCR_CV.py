
import pyvisa
from pymeasure.instruments.agilent import AgilentE4980
import time
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

rm = pyvisa.ResourceManager()
my_instrument= rm.open_resource("GPIB::17")
LCR = AgilentE4980("GPIB::17")


#---------------------------------------------------------------
#user input

V=5 # volt for loop (V)
V_step=1 #interval between measurements (V)
freq=1000000 #freq in Hz
loop=1
name="new_prg_cap_test1122"

V_ac=1


#---------------------------------------------------------------


filename="E:/Prathamesh/Python Stuff/CV/CV_Measurements/"+str(name)+"_freq_"+str(freq)+"_volt_"+str(V)+"_V_step_"+str(V_step)+"_Loops"+str(loop)+".txt"
#---------------------------------------------------------------


loop_ind_new=0
protocol_list=[]
V_list=[]
C_list=[]
loop_list=[]


my_instrument. write( '*RST; *CLS' )
time. sleep( 1)

my_instrument. write( ':VOLT:LEVEL ' , str(V_ac))

time. sleep( 3)
LCR.frequency=freq
time. sleep( 3)
print(my_instrument.write( ':BIAS:STATe ON' ))


'''
print(dir(LCR))
#freq[10,20,30]
#LCR.freq_sweep([freq], False)
x= LCR.measurement(
        ":FETCH?",
        "Measured data A and B, according to :attr:`~.AgilentE4980.mode`",
        get_process=lambda x: x[:2])

print(x)
my_instrument.write( ':BIAS:VOLTage:LEVel '+str(0))
print(LCR.values(":FETCh:IMPedance:FORMatted?"))
time.sleep(1)
my_instrument.write( ':BIAS:VOLTage:LEVel '+str(1))
print(LCR.values(":FETCh:IMPedance:FORMatted?"))
time.sleep(2)

time.sleep(1)
my_instrument.write( ':BIAS:VOLTage:LEVel '+str(2))
print(LCR.values(":FETCh:IMPedance:FORMatted?"))
time.sleep(2)
V=5
V_step=0.5

for v_ind in np.arange(0,V+V_step,V_step) :
    my_instrument.write( ':BIAS:VOLTage:LEVel '+str(v_ind))
    print(LCR.values(":FETCh:IMPedance:FORMatted?"))
    time.sleep(2)

'''

def Proto_fcn():
    global loop_ind_new
    global protocol_list
    loop_ind_new+=1

    #first protocol 0 to V {A}
    for v_ind in np.arange(0,V+V_step,V_step) :
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("A")



    #Second protocol V to 0 {B}
    for v_ind in np.arange(V,0-V_step,-V_step) :
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("B")


    #Third protocol 0 to -V {C}
    for v_ind in np.arange(0,-V-V_step,-V_step) :
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("C")

    #Second protocol -V to 0 {D}
    for v_ind in np.arange(-V,0+V_step,V_step) :
        LCR_fcn(v_ind)
        loop_list.append(loop_ind_new)
        protocol_list.append("D")


# LCR_fcn for the actual measurements
def LCR_fcn(volt_ind):

    #C=V33+3*np.random.rand()


    #V_list.append(V33)
    #C_list.append(C)
    global v1
    global V_list
    global output1
    global C_list
    global Volt
    #v1=[]
    #V_list=[]
    #output1=[]
    #C_list=[]

    #my_instrument. write( ':VOLT:LEVEL ' , str(volt_ind))
    my_instrument.write( ':BIAS:VOLTage:LEVel '+str(volt_ind))

    time. sleep( 5)
    #my_instrument. write( ':INITiate[:IMMediate]')

    #output=my_instrument. query_ascii_values( ':MEM:READ? DBUF' )
    time. sleep( 2)

    #output1=LCR.freq_sweep([freq], False)
    output1=LCR.values(":FETCh:IMPedance:FORMatted?")

    C_list.append(output1[0])
    #v1=my_instrument.query( ':VOLT:LEVEL?' )
    v1=my_instrument.query( ':BIAS:VOLTage:LEVel?')
    V_list.append(float(v1))
    time. sleep( 4)

    print("Output: "+str(output1)+"    |  Volt : "+str(v1)+"   |  Cp : "+str(output1[0])+" | Loop: "+str(loop_ind_new)+"  |  ")


#Proto_fcn()

def Loop_fcn(loop):

    for loop_ind in range(loop):
        Proto_fcn()




Loop_fcn(loop)
dict = {'Volt': V_list, 'Cp':C_list,'Loop':loop_list,'Protocol':protocol_list}
df = pd.DataFrame(dict)
df.to_csv(filename, sep=',',index=False, encoding='utf-8')
time. sleep( 1)
LCR.shutdown()
time. sleep( 1)
print("Measurements Completed and Data saved ")


