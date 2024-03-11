
# prg for LCR Keysight E 4980 A
#supplimentry stuff 20-6-23


import pyvisa
from pymeasure.instruments.agilent import AgilentE4980
import time
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd



#---------------------------------------------------------------
#user input

V=2 # volt for loop (V)
V_step=2 #interval between measurements (V)
freq=1000 #freq in Hz
loop=1
name="Swastika_Test2_"

V_ac=0.5


#---------------------------------------------------------------


filename="E:/Prathamesh/Python Stuff/CV/CV_Measurements/"+str(name)+"_freq_"+str(freq)+"_volt_"+str(V)+"_V_step_"+str(V_step)+"_Loops"+str(loop)+".txt"
#---------------------------------------------------------------


loop_ind_new=0
protocol_list=[]
V_list=[]
C_list=[]
loop_list=[]
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
time. sleep( 3)
print(my_instrument.write( ':BIAS:STATe ON' ))
time. sleep(2)
my_instrument. write( ':VOLT:LEVEL ' , str(V_ac))
time. sleep(2)
#---------------------------------




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
    my_instrument. write( ':INITiate[:IMMediate]')

    #output=my_instrument. query_ascii_values( ':MEM:READ? DBUF' )
    time. sleep( 2)

    #output1=LCR.freq_sweep([freq], False)
    output1=LCR.values(":FETCh:IMPedance:FORMatted?")
    time. sleep( 2)

    C_list+=output1[0]
    #v1=my_instrument.query( ':VOLT:LEVEL?' )
    v1=my_instrument.query( ':BIAS:VOLTage:LEVel?')
    V_list.append(v1)
    time. sleep( 4)

    print("Output: "+str(output1)+"    |  Volt : "+str(v1)+"   |  Cp : "+str(output1[0])+" | Loop: "+str(loop_ind_new)+"  |  ")




# Proto_fcn for the measurements protocol

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





# Loop_fcn for the looping number of times

def Loop_fcn(loop):

    for loop_ind in range(loop):
        Proto_fcn()




Loop_fcn(loop)
#print(C_list)


my_instrument. write( ':MEM:CLE DBUF' )
my_instrument. write( ':DISP:PAGE MEAS' )
time. sleep( 1)
LCR.shutdown()




dict = {'Volt': V_list, 'Cp':C_list,'Loop':loop_list,'Protocol':protocol_list}
df = pd.DataFrame(dict)
df.to_csv(filename, sep=',',index=False, encoding='utf-8')

'''
#ploting seprate plots

for plt_loop in range(1,loop+1):
    Loop_df =df.loc[df['Loop'] == plt_loop]
    Loop_df.plot(x="Volt",y="Cp",c="red",label=plt_loop)
'''

print(dict)
plt.show()
print(df)

#

#plt.scatter(V_list, C_list,s=3,c=loop_list,cmap='YlOrRd') # s is a size of marker
#plt.plot(V_list, C_list,'-o',c=loop_list,cmap='YlOrRd')
#plt.plot(V_list, C_list, linestyle="-", marker="o",color='#CB4335')



print("Measurements Completed and Data saved ")
plt.scatter(V_list,C_list)
plt.title("Cp vs V , Loops:"+str(loop)+"   V_max:"+str(V)+"   step size : "+str(V_step))
plt.xlabel("V")
plt.ylabel("Cp")

plt.show()
