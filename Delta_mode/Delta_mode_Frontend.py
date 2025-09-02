import tkinter as tk
from tkinter import Label, Entry, LabelFrame, Button, filedialog, messagebox
import numpy as np
import csv
import os
import time
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

class TemperatureSweepApp:
    """
    A GUI application for simulating and live-plotting a temperature-dependent
    resistance and voltage measurement.
    """
    # --- Styling Constants ---
    BG_COLOR = '#F0F0F0'
    FRAME_BG = '#2C3E50'
    FRAME_FG = '#ECF0F1'
    FONT_NORMAL = ('Helvetica', 12)
    FONT_BOLD = ('Helvetica', 12, 'bold')
    FONT_TITLE = ('Helvetica', 14, 'bold')
    COLOR_R = '#E74C3C'  # Red for Resistance
    COLOR_V = '#3498DB'  # Blue for Voltage

    def __init__(self, root):
        """Initializes the application, UI, and state variables."""
        self.root = root
        self.root.title("Delta Mode V-T Measurement")
        self.root.geometry("1100x800")
        self.root['background'] = self.BG_COLOR

        # --- State variables ---
        self.is_running = False
        self.start_time = None
        self.data_filepath = ""

        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.create_widgets()

    # --- GUI Setup ---

    def create_widgets(self):
        """Creates the main frames for the application."""
        self.create_input_frame()
        self.create_graph_frame()

    def create_input_frame(self):
        """Builds the left-side panel for user input and controls."""
        frame = LabelFrame(self.root, text='Experiment Parameters', bd=4, bg=self.FRAME_BG, fg=self.FRAME_FG, font=self.FONT_TITLE)
        frame.grid(row=0, column=0, padx=10, pady=10, sticky="ns")

        # --- Input Fields ---
        self.entries = {}
        fields = ["Sample Name", "Initial Temp (K)", "Final Temp (K)", "Ramp Rate (K/min)", "Apply Current (A)"]
        for i, field in enumerate(fields):
            self.entries[field] = self._create_labeled_entry(frame, field, i)

        # --- File Location ---
        row_after_fields = len(fields)
        Label(frame, text="Save Location:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=row_after_fields, column=0, padx=10, pady=8, sticky='w')
        self.file_location_button = Button(frame, text="Browse...", command=self._browse_file_location, font=('Helvetica', 10), bg=self.COLOR_V, fg=self.FRAME_FG)
        self.file_location_button.grid(row=row_after_fields, column=1, padx=10, pady=8, sticky='ew')

        # --- Control Buttons ---
        self.start_button = Button(frame, text="Start Measurement", command=self.start_measurement, height=2, font=self.FONT_BOLD, bg='#2ECC71', fg=self.FRAME_FG)
        self.start_button.grid(row=row_after_fields + 1, column=0, columnspan=2, padx=10, pady=20, sticky='ew')

        self.stop_button = Button(frame, text="Stop Measurement", command=self.stop_measurement, height=2, font=self.FONT_BOLD, bg=self.COLOR_R, fg=self.FRAME_FG, state='disabled')
        self.stop_button.grid(row=row_after_fields + 2, column=0, columnspan=2, padx=10, pady=5, sticky='ew')

        # --- Info Footer ---
        footer_frame = LabelFrame(frame, text="Info", bd=2, bg=self.FRAME_BG, fg=self.FRAME_FG)
        footer_frame.grid(row=row_after_fields + 3, column=0, columnspan=2, padx=10, pady=30, sticky='ew')
        Label(footer_frame, text="Institute: UGC DAE CSR Mumbai", font=('Helvetica', 11, 'italic'), fg=self.FRAME_FG, bg=self.FRAME_BG).pack(anchor='w')
        Label(footer_frame, text="Purpose: Delta mode V vs T", font=('Helvetica', 11, 'italic'), fg=self.FRAME_FG, bg=self.FRAME_BG).pack(anchor='w')

    def create_graph_frame(self):
        """Builds the right-side panel for the live graphs."""
        frame = LabelFrame(self.root, text='Live Graphs', bd=4, bg='white', fg=self.FRAME_BG, font=self.FONT_TITLE)
        frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        self.figure = Figure(figsize=(7, 6), dpi=100)
        self.ax1, self.ax2 = self.figure.subplots(2, 1, sharex=True)

        # --- Initialize empty plot lines ---
        self.line_r, = self.ax1.plot([], [], color=self.COLOR_R, marker='.', markersize=4) # Resistance line
        self.line_v, = self.ax2.plot([], [], color=self.COLOR_V, marker='.', markersize=4) # Voltage line

        self.ax1.set_ylabel("Resistance (Ohms)")
        self.ax1.grid(True)

        self.ax2.set_ylabel("Voltage (V)")
        self.ax2.set_xlabel("Temperature (K)")
        self.ax2.grid(True)

        self.figure.tight_layout(pad=2.0)
        self.canvas = FigureCanvasTkAgg(self.figure, frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # --- Measurement Logic ---

    def start_measurement(self):
        """Validates inputs, sets up files, and starts the measurement loop."""
        try:
            params = {key: float(entry.get()) for key, entry in self.entries.items() if key != "Sample Name"}
            self.sample_name = self.entries["Sample Name"].get()
            self.initial_temp = params["Initial Temp (K)"]
            self.final_temp = params["Final Temp (K)"]
            self.ramp_rate_k_per_sec = params["Ramp Rate (K/min)"] / 60.0
            self.apply_current = params["Apply Current (A)"]

            if not self.sample_name or not hasattr(self, 'file_location_path') or not self.file_location_path:
                raise ValueError("Sample Name and Save Location are required.")
            if self.initial_temp >= self.final_temp or self.ramp_rate_k_per_sec <= 0:
                raise ValueError("Invalid temperature or ramp rate settings.")

        except (ValueError, KeyError) as e:
            messagebox.showerror("Input Error", f"Please check your parameters.\n{e}")
            return

        # --- Setup the data file ---
        file_name = f"{self.sample_name}_Live_V-T_Sweep.dat"
        self.data_filepath = os.path.join(self.file_location_path, file_name)
        with open(self.data_filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([f"# Sample Name: {self.sample_name}"])
            writer.writerow([f"# Applied Current (A): {self.apply_current}"])
            writer.writerow(["Time (s)", "Temperature (K)", "Resistance (Ohms)", "Voltage (V)"])

        # --- Reset UI and start loop ---
        self.is_running = True
        self.start_time = time.time()
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')

        # Reset and configure plots once
        self.line_r.set_data([], [])
        self.line_v.set_data([], [])
        self.ax1.set_title(f"Sample: {self.sample_name} | Current: {self.apply_current} A")
        self.canvas.draw()

        self.root.after(1000, self._update_measurement_loop) # Start loop after 1s

    def stop_measurement(self):
        """Stops the measurement loop and resets the UI state."""
        if self.is_running:
            self.is_running = False
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            messagebox.showinfo("Info", "Measurement stopped.")

    def _update_measurement_loop(self):
        """Core loop: simulates data, saves it, updates graphs, and schedules next run."""
        if not self.is_running:
            return

        elapsed_time = time.time() - self.start_time
        current_temp = self.initial_temp + (elapsed_time * self.ramp_rate_k_per_sec)

        # --- Stop condition ---
        if current_temp >= self.final_temp:
            current_temp = self.final_temp # Clamp to final value
            self.is_running = False # Stop loop after this run
            messagebox.showinfo("Success", "Measurement complete!")

        # --- Simulate and save one new data point ---
        base_resistance = 500 * np.exp(-current_temp / 100) + np.random.normal(0, 5)
        voltage = base_resistance * self.apply_current
        with open(self.data_filepath, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([f"{elapsed_time:.2f}", f"{current_temp:.4f}", f"{base_resistance:.4f}", f"{voltage:.4f}"])

        # --- Read all data and update graph ---
        try:
            data = np.loadtxt(self.data_filepath, delimiter=',', skiprows=3)
            # Ensure data is 2D even if there's only one row
            if data.ndim == 1:
                data = data.reshape(1, -1)

            temps, resistances, voltages = data[:, 1], data[:, 2], data[:, 3]

            self.line_r.set_data(temps, resistances)
            self.line_v.set_data(temps, voltages)

            # Rescale axes
            self.ax1.relim()
            self.ax1.autoscale_view()
            self.ax2.relim()
            self.ax2.autoscale_view()

            self.canvas.draw()
        except Exception as e:
            print(f"Could not update graph: {e}")

        # --- Schedule next update or stop ---
        if self.is_running:
            self.root.after(1000, self._update_measurement_loop)
        else:
            self.stop_measurement()

    # --- Helpers ---

    def _create_labeled_entry(self, parent, text, row_index):
        """Helper to create a Label and an Entry widget, returning the Entry."""
        Label(parent, text=f"{text}:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=row_index, column=0, padx=10, pady=8, sticky='w')
        entry = Entry(parent, width=20, font=self.FONT_NORMAL)
        entry.grid(row=row_index, column=1, padx=10, pady=8)
        return entry

    def _browse_file_location(self):
        """Opens a dialog to select a save directory."""
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            display_path = path if len(path) <= 25 else f"...{path[-22:]}"
            self.file_location_button.config(text=display_path)

def main():
    """Main function to create the Tkinter window and run the application."""
    root = tk.Tk()
    app = TemperatureSweepApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
