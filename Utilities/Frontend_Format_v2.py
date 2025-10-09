import tkinter as tk
from tkinter import Label, Entry, Scrollbar, Listbox, LabelFrame, Button, filedialog, messagebox
from PIL import ImageTk, Image
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

class IVMeasurementApp:
    def __init__(self, root):
        self.root = root
        self.root.title("IV Measurement")
        self.root.geometry("800x600")
        self.root['background'] = '#F0F0F0'

        self.create_widgets()

    def create_widgets(self):
        self.create_input_frame()
        self.create_output_frame()
        self.create_output_list_frame()
        self.create_graph_frame()

    def create_input_frame(self):
        self.frame_input = LabelFrame(self.root, text='Input', bd=4, bg='#2C3E50', fg='#ECF0F1', font=('Helvetica', 14, 'bold'))
        self.frame_input.grid(row=0, column=0, padx=10, pady=5, sticky="nsew")

        Label(self.frame_input, text="IV Measurement", font=('Helvetica', 18, 'bold'), fg='#ECF0F1', bg='#2C3E50').grid(
            row=0, column=0, columnspan=4, padx=10, pady=10)

        Label(self.frame_input, text="Enter Current", font=('Helvetica', 12), fg='#ECF0F1', bg='#2C3E50').grid(row=1, column=0, padx=10, pady=10)
        self.i_entry = Entry(self.frame_input, width=20, font=('Helvetica', 16))
        self.i_entry.grid(row=1, column=1, padx=20, pady=10)

        Label(self.frame_input, text="Enter Step Size", font=('Helvetica', 12), fg='#ECF0F1', bg='#2C3E50').grid(row=2, column=0, padx=10, pady=10)
        self.i_step_entry = Entry(self.frame_input, width=20, font=('Helvetica', 16))
        self.i_step_entry.grid(row=2, column=1, padx=20, pady=10)

        Label(self.frame_input, text="Select Location", font=('Helvetica', 12), fg='#ECF0F1', bg='#2C3E50').grid(row=3, column=0, padx=10, pady=10)
        self.file_location_button = Button(self.frame_input, text="Browse", command=self.browse_file_location, font=('Helvetica', 12), bg='#3498DB', fg='#ECF0F1')
        self.file_location_button.grid(row=3, column=1, padx=20, pady=10)

        Button(self.frame_input, text="Measure IV", command=self.my_measure, height=2, width=15, font=('Helvetica', 12), bg='#E74C3C', fg='#ECF0F1').grid(
            row=5, column=1, columnspan=1, padx=2, pady=10)

    def browse_file_location(self):
        file_location = filedialog.askdirectory()
        self.file_location_button.config(text=file_location)

    def create_output_frame(self):
        self.frame_output = LabelFrame(self.root, text='Data Entered', bd=4, bg='#2C3E50', fg='#ECF0F1', font=('Helvetica', 14, 'bold'))
        self.frame_output.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")

    def create_output_list_frame(self):
        self.frame_output_list = LabelFrame(self.root, text='Current        Voltage', bd=4, bg='#2C3E50', fg='#ECF0F1', font=('Helvetica', 14, 'bold'))
        self.frame_output_list.grid(row=1, column=1, padx=10, pady=5, sticky="nsew")

    def create_graph_frame(self):
        self.frame_graph = LabelFrame(self.root, text='Graph', bd=4, bg='#2C3E50', fg='#ECF0F1', font=('Helvetica', 14, 'bold'))
        self.frame_graph.grid(row=0, column=1, padx=10, pady=5, sticky="nsew")

    def my_measure(self):
        try:
            I_value = float(self.i_entry.get())
            I_step_value = float(self.i_step_entry.get())
            file_location = self.file_location_button.cget("text")

            Label(self.frame_output, text=f"I value: {I_value}, Step Size: {I_step_value}, Location: {file_location}", font=('Helvetica', 12), fg='#ECF0F1', bg='#2C3E50').grid(
                row=7, column=0, columnspan=3)

            scrollbar = Scrollbar(self.frame_output_list)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            list_current = Listbox(self.frame_output_list, yscrollcommand=scrollbar.set, font=('Helvetica', 12), bg='#ECF0F1')
            for i in range(int(I_value)):
                list_current.insert(tk.END, f"{i}                         {i}")

            list_current.pack(side=tk.LEFT, fill=tk.BOTH)
            scrollbar.config(command=list_current.yview)

            # graph putting out
            figure = Figure(figsize=(5, 5), dpi=100)
            plot = figure.add_subplot(1, 1, 1)
            x = range(int(I_value))
            y = np.sin(x)
            plot.plot(x, y, color="red", marker="o", linestyle="")
            canvas = FigureCanvasTkAgg(figure, self.frame_graph)
            canvas.get_tk_widget().grid(row=20, column=20)

            specified_name = "IV_Data.dat"  # Replace with your desired name
            file_path = f"{file_location}/{specified_name}"

            with open(file_path, 'w') as file:
                file.write(f"I value: {I_value}, Step Size: {I_step_value}\n")
                for i in range(int(I_value)):
                    file.write(f"{i}                         {i}\n")

            messagebox.showinfo("Success", f"Data saved successfully at:\n{file_path}")

        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric values for Current and Step Size.")

if __name__ == '__main__':
    root = tk.Tk()
    app = IVMeasurementApp(root)
    root.mainloop()
