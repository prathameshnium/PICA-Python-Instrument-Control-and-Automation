from tkinter import *
from PIL import ImageTk,Image
import numpy as np
import matplotlib.pyplot as plt
import time
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

root = Tk()
root.title("IV measurement")
root.geometry("600x600")
root.state('zoomed')
root['background']='#5D6D7E'
frame_input= LabelFrame(root, text = 'Input',bd=4)
frame_input.grid(row=0, column=0, padx=10, pady=5)
frame_output= LabelFrame(root, text = 'data entered',bd=4)
frame_output.grid(row=1, column=0, padx=10, pady=5)
frame_output_list= LabelFrame(root, text = 'Current        Voltage',bd=4)
frame_output_list.grid(row=1, column=1, padx=10, pady=5)
frame_graph= LabelFrame(root, text = 'graph')
frame_graph.grid(row=0, column=1, padx=10, pady=5)
Label_file_name = Label(frame_input, text="Program: IV Measurement \n Backend Program V:-----").grid(row=0, column=0,columnspan=4, padx=10, pady=10)


Label_cur = Label(frame_input, text="Enter Current ").grid(row=1, column=0, padx=10, pady=10)

i_entry = Entry(frame_input, width=20, font=('Monospace', 20))
i_entry.grid(row=1, column=1, padx=80, pady=3)

Label_cur_step = Label(frame_input, text="Enter Current step size").grid(row=2, column=0, padx=10, pady=10)

i_step_entry = Entry(frame_input, width=20, font=('Monospace', 20))
i_step_entry.grid(row=2, column=1, padx=80, pady=3)

Label_file_name = Label(frame_input, text="Enter file name ").grid(row=3, column=0, padx=10, pady=10)

entry_file_name = Entry(frame_input, width=20, font=('Monospace', 20))
entry_file_name.grid(row=3, column=1, padx=80, pady=3)

def my_measure():
    global I_value
    global I_step_value
    global file_name
    
    I_value=float(i_entry.get())
    I_step_value=float(i_step_entry.get())
    file_name=str(entry_file_name.get())
    #Label_1=Label(frame_output,text=" data entered ").grid(row=5,column=0,columnspan=4)
    Label_2_Label=Label(frame_output,text="I value: "+str(I_value)).grid(row=7,column=0)
    Label_3=Label(frame_output,text=" , I step: "+str(I_step_value)).grid(row=7,column=1)
    Label_4=Label(frame_output,text=" ,  file name: "+file_name).grid(row=7,column=2)

    scrollbar = Scrollbar(frame_output_list)
    scrollbar.pack( side = RIGHT, fill = Y )

    list_current = Listbox(frame_output_list, yscrollcommand = scrollbar.set) 
    for i in range(int(I_value)):
       list_current.insert(END, str(i)+"                         "+str(i))
  
    list_current.pack( side = LEFT, fill = BOTH )
    scrollbar.config( command = list_current.yview )
     
#graph putting out
    figure = Figure(figsize=(3, 3), dpi=100)
    # Define the points for plotting the figure
    plot = figure.add_subplot(1, 1, 1)
    # Define Data points for x and y axis
    x = range(int(I_value))
    y = np.sin(x)
    plot.plot(x, y,color="red", marker="o", linestyle="")
    # Add a canvas widget to associate the figure with canvas
    canvas = FigureCanvasTkAgg(figure,frame_graph)
    canvas.get_tk_widget().grid(row=20, column=20)



myButton=Button(frame_input,text=" Measure IV data ",command=my_measure,height=3,width=13).grid(row=5,column=1,columnspan=1,padx=2,pady=1)





root.mainloop()


