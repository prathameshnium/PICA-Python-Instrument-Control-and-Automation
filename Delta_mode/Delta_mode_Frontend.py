# Delta Mode
################################################################################
# Packages for Front end
import tkinter as tk
from tkinter import Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext
import numpy as np
import csv
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

################################################################################
# Packages for Back end
# (This is where you would import packages for your backend logic)
################################################################################

class DELTA_Backend:
    """
    A dedicated class to handle all backend instrument communication.
    This is the block where all hardware-specific code will go.
    """
    def __init__(self):
        self.params = {}
        # You would initialize instrument objects here, e.g., self.keithley = None

    def initialize_instruments(self, parameters):
        """Receives all parameters from the GUI and configures the instruments."""
        print("\n--- [DELTA Backend] Initializing Instruments ---")
        self.params = parameters
        print(f"  Sample Name: {self.params['sample_name']}")
        print(f"  File Path: {self.params['data_filepath']}")
        print(f"  Initial Temp (K): {self.params['initial_temp']}")
        print(f"  Final Temp (K): {self.params['final_temp']}")
        print(f"  Ramp Rate (K/s): {self.params['ramp_rate_k_per_sec']}")
        print(f"  Applied Current (A): {self.params['apply_current']}")
        print("--- [DELTA Backend] Instruments Configured ---")

    def get_measurement(self, current_temp):
        """Performs a single measurement at a given temperature."""
        # --- Simulation Block (for now) ---
        resistance = 500 * np.exp(-current_temp / 100) + np.random.normal(0, 5)
        voltage = resistance * self.params['apply_current']
        return resistance, voltage

    def close_instruments(self):
        """A placeholder to safely disconnect from instruments."""
        print("--- [DELTA Backend] Closing instrument connections. ---")


class TemperatureSweepApp:
    """
    The main GUI application class (Front End). It communicates with the DELTA_Backend.
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
    COLOR_T = '#2ECC71'  # Green for Temperature vs. Time

    def __init__(self, root):
        self.root = root
        self.root.title("Delta Mode V-T Measurement")
        self.root.geometry("1100x900")
        self.root['background'] = self.BG_COLOR

        self.is_running = False
        self.start_time = None

        self.backend = DELTA_Backend()

        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # --- GUI Setup ---

    def create_widgets(self):
        left_panel = tk.Frame(self.root, bg=self.BG_COLOR)
        left_panel.grid(row=0, column=0, sticky="ns", padx=(10,0))
        self.create_input_frame(left_panel)
        self.create_console_frame(left_panel)
        self.create_graph_frame()

    def create_input_frame(self, parent):
        """Builds the top-left panel for user input and controls."""
        frame = LabelFrame(parent, text='Experiment Parameters', bd=4, bg=self.FRAME_BG, fg=self.FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10)

        self.entries = {}
        fields = ["Sample Name", "Initial Temp (K)", "Final Temp (K)", "Ramp Rate (K/min)", "Apply Current (A)"]
        for i, field_text in enumerate(fields):
            Label(frame, text=f"{field_text}:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=i, column=0, padx=10, pady=8, sticky='w')
            entry = Entry(frame, width=20, font=self.FONT_NORMAL)
            entry.grid(row=i, column=1, padx=10, pady=8)
            self.entries[field_text] = entry

        row_after_fields = len(fields)
        Label(frame, text="Save Location:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=row_after_fields, column=0, padx=10, pady=8, sticky='w')
        self.file_location_button = Button(frame, text="Browse...", command=self._browse_file_location, font=('Helvetica', 10), bg=self.COLOR_V, fg=self.FRAME_FG)
        self.file_location_button.grid(row=row_after_fields, column=1, padx=10, pady=8, sticky='ew')

        self.start_button = Button(frame, text="Start Measurement", command=self.start_measurement, height=2, font=self.FONT_BOLD, bg='#2ECC71', fg=self.FRAME_FG)
        self.start_button.grid(row=row_after_fields + 1, column=0, columnspan=2, padx=10, pady=20, sticky='ew')

        self.stop_button = Button(frame, text="Stop Measurement", command=self.stop_measurement, height=2, font=self.FONT_BOLD, bg=self.COLOR_R, fg=self.FRAME_FG, state='disabled')
        self.stop_button.grid(row=row_after_fields + 2, column=0, columnspan=2, padx=10, pady=5, sticky='ew')

        footer_frame = LabelFrame(frame, text="Info", bd=2, bg=self.FRAME_BG, fg=self.FRAME_FG)
        footer_frame.grid(row=row_after_fields + 3, column=0, columnspan=2, padx=10, pady=15, sticky='ew')
        Label(footer_frame, text="Institute: UGC DAE CSR Mumbai", font=('Helvetica', 11, 'italic'), fg=self.FRAME_FG, bg=self.FRAME_BG).pack(anchor='w')
        Label(footer_frame, text="Purpose: Delta mode V vs T", font=('Helvetica', 11, 'italic'), fg=self.FRAME_FG, bg=self.FRAME_BG).pack(anchor='w')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', bd=4, bg=self.FRAME_BG, fg=self.FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x', expand=False)
        self.console_widget = scrolledtext.ScrolledText(frame, height=10, state='disabled', bg='#1C2833', fg='#EAECEE', font=('Consolas', 10))
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Ready for measurement.")

    def create_graph_frame(self):
        """Builds the right-side panel for the live graphs with three subplots."""
        frame = LabelFrame(self.root, text='Live Graphs', bd=4, bg='white', fg=self.FRAME_BG, font=self.FONT_TITLE)
        frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        # === CHANGE: Create 3 subplots instead of 2 ===
        self.figure = Figure(figsize=(7, 8), dpi=100)
        self.ax1, self.ax2, self.ax3 = self.figure.subplots(3, 1)

        # --- Initialize three empty plot lines ---
        self.line_r, = self.ax1.plot([], [], color=self.COLOR_R, marker='.', markersize=4)
        self.line_v, = self.ax2.plot([], [], color=self.COLOR_V, marker='.', markersize=4)
        self.line_t, = self.ax3.plot([], [], color=self.COLOR_T, marker='.', markersize=4)

        # --- Configure all three axes ---
        self.ax1.set_ylabel("Resistance (Ohms)")
        self.ax1.set_xlabel("Temperature (K)")
        self.ax1.grid(True)

        self.ax2.set_ylabel("Voltage (V)")
        self.ax2.set_xlabel("Temperature (K)")
        self.ax2.grid(True)

        self.ax3.set_ylabel("Temperature (K)")
        self.ax3.set_xlabel("Time (s)")
        self.ax3.grid(True)

        self.figure.tight_layout(pad=2.0)
        self.canvas = FigureCanvasTkAgg(self.figure, frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # --- Logging ---
    def log(self, message):
        print(message)
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', message + '\n')
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    # --- Measurement Logic (Front End) ---
    def start_measurement(self):
        try:
            params = {}
            params['sample_name'] = self.entries["Sample Name"].get()
            params['initial_temp'] = float(self.entries["Initial Temp (K)"].get())
            params['final_temp'] = float(self.entries["Final Temp (K)"].get())
            params['ramp_rate_k_per_sec'] = float(self.entries["Ramp Rate (K/min)"].get()) / 60.0
            params['apply_current'] = float(self.entries["Apply Current (A)"].get())

            if not params['sample_name'] or not hasattr(self, 'file_location_path') or not self.file_location_path:
                raise ValueError("Sample Name and Save Location are required.")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_V-T_Sweep.dat"
            params['data_filepath'] = os.path.join(self.file_location_path, file_name)

            self.backend.initialize_instruments(params)

            with open(params['data_filepath'], 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample Name: {params['sample_name']}"])
                writer.writerow([f"# Applied Current (A): {params['apply_current']}"])
                writer.writerow(["Time (s)", "Temperature (K)", "Resistance (Ohms)", "Voltage (V)"])
            self.log(f"Output file created at: {params['data_filepath']}")

            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')

            # === CHANGE: Reset the third plot line ===
            self.line_r.set_data([], []); self.line_v.set_data([], []); self.line_t.set_data([], [])
            self.ax1.set_title(f"Sample: {params['sample_name']} | Current: {params['apply_current']} A")
            self.canvas.draw()

            self.log("GUI measurement loop started.")
            self.root.after(1000, self._update_measurement_loop)

        except (ValueError, KeyError) as e:
            messagebox.showerror("Input Error", f"Please check your parameters.\n{e}")
            self.log(f"ERROR: Input validation failed. {e}")
            return

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement loop stopped by user.")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            messagebox.showinfo("Info", "Measurement stopped.")

    def _update_measurement_loop(self):
        if not self.is_running: return

        try:
            elapsed_time = time.time() - self.start_time
            current_temp = self.backend.params['initial_temp'] + (elapsed_time * self.backend.params['ramp_rate_k_per_sec'])

            if current_temp >= self.backend.params['final_temp']:
                current_temp = self.backend.params['final_temp']
                self.is_running = False
                self.log("\nFinal temperature reached.")

            resistance, voltage = self.backend.get_measurement(current_temp)

            with open(self.backend.params['data_filepath'], 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"{elapsed_time:.2f}", f"{current_temp:.4f}", f"{resistance:.4f}", f"{voltage:.4f}"])

            data = np.loadtxt(self.backend.params['data_filepath'], delimiter=',', skiprows=3)
            if data.ndim == 1: data = data.reshape(1, -1)

            # === CHANGE: Extract time data and update all three plots ===
            times, temps, resistances, voltages = data[:, 0], data[:, 1], data[:, 2], data[:, 3]

            self.line_r.set_data(temps, resistances)
            self.line_v.set_data(temps, voltages)
            self.line_t.set_data(times, temps)

            self.ax1.relim(); self.ax1.autoscale_view()
            self.ax2.relim(); self.ax2.autoscale_view()
            self.ax3.relim(); self.ax3.autoscale_view()
            self.canvas.draw()

        except Exception:
            self.log("\n--- AN ERROR OCCURRED ---")
            self.log(traceback.format_exc())
            self.stop_measurement()
            messagebox.showerror("Runtime Error", "An unexpected error occurred. Check console for details.")

        if self.is_running:
            self.root.after(1000, self._update_measurement_loop)
        else:
            self.stop_measurement()
            self.log("Measurement complete.")
            messagebox.showinfo("Success", "Measurement complete!")


    # --- GUI Helpers ---
    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            display_path = path if len(path) <= 25 else f"...{path[-22:]}"
            self.file_location_button.config(text=display_path)
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        """Handles the window close event to safely stop measurement."""
        if self.is_running:
            if messagebox.askyesno("Exit", "A measurement is running. Are you sure you want to exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = TemperatureSweepApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
