# ploter for the Live_plots_Temperature_dependendent_V
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from tkinter import filedialog
import tkinter as tk


def live_plot_from_csv(selected_file):
    """
    Creates a live-updating plot from a specified CSV file.
    The plot will refresh every 1000 milliseconds.
    """
    # Set up the plot
    plt.style.use('fivethirtyeight')
    fig, axs = plt.subplots(3, 1, figsize=(9, 12))

    def animate(i):
        # Reload data from CSV (in case it has changed)
        data = pd.read_csv(selected_file)
        x = data['Time (s)']
        y1 = data['Temperature (K)']
        y2 = data['Voltage (V)']

        # Clear previous plots
        for ax in axs:
            ax.clear()

        # Update subplots
        axs[0].plot(x, y1, label='T', color='b', linewidth=0.8)
        axs[0].scatter(x, y1, color='b')
        axs[0].set_title('Temperature vs Time', fontsize=13)
        axs[0].set_xlabel('Time (s)', fontsize=13)
        axs[0].set_ylabel('Temperature (K)', fontsize=13)
        axs[0].legend(loc='upper left')

        axs[1].plot(x, y2, label='V', color='g', linewidth=0.8)
        axs[1].scatter(x, y2, color='g')
        axs[1].set_title('Voltage vs Time', fontsize=13)
        axs[1].set_xlabel('Time (s)', fontsize=13)
        axs[1].set_ylabel('Voltage (V)', fontsize=13)
        axs[1].legend(loc='upper left')

        axs[2].plot(y1, y2, label='V vs T', color='r', linewidth=0.8)
        axs[2].scatter(y1, y2, color='r')
        axs[2].set_title('Voltage vs Temperature', fontsize=13)
        axs[2].set_xlabel('Temperature (K)', fontsize=13)
        axs[2].set_ylabel('Voltage (V)', fontsize=13)
        axs[2].legend(loc='upper left')

    # Keep a reference to the animation object to prevent it from being garbage
    # collected.
    # noqa: F841 is used to suppress the flake8 warning about the unused variable.
    ani = FuncAnimation(fig, animate, interval=1000, cache_frame_data=False)  # noqa: F841

    plt.tight_layout()
    plt.show()


def main():
    """Main function to select a file and start the plotting."""
    # Use tkinter to create a file selection dialog
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    selected_file = filedialog.askopenfilename(
        title="Select a CSV data file",
        filetypes=(("CSV files", "*.csv"),
                   ("Data files", "*.dat"),
                   ("All files", "*.*"))
    )

    if not selected_file:
        print("No file selected. Exiting.")
        return

    print(f"Selected file: {selected_file}")
    live_plot_from_csv(selected_file)


if __name__ == '__main__':
    main()
