import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# Load data from CSV file


# Set up the plot
plt.style.use('fivethirtyeight')
fig, axs = plt.subplots(3, 1, figsize=(9, 12))

def animate(i):
    # Reload data from CSV (in case it has changed)
    data = pd.read_csv('E:/Prathamesh/Python Stuff/Py Pyroelectric/Test_data/Pyro_data.csv')
    x = data['Time (s)']
    y1 = data['Temperature (K)']
    y2 = data['Current (A)']


    # Clear previous plots
    for ax in axs:
        ax.clear()

    # Update subplots
    axs[0].plot(x, y1, label='T', color='b',linewidth=0.8)
    axs[0].scatter(x, y1, color='b')
    axs[0].set_title('T vs time',fontsize=13)
    axs[0].set_xlabel('Time (s)',fontsize=13)
    axs[0].set_ylabel('Temperature (K)',fontsize=13)
    axs[0].legend(loc='upper left')

    axs[1].plot(x, y2, label='I', color='g',linewidth=0.8)
    axs[1].scatter(x, y2, color='g')
    axs[1].set_title('I vs time',fontsize=13)
    axs[1].set_xlabel('Time (s)',fontsize=13)
    axs[1].set_ylabel('Current (A)',fontsize=13)
    axs[1].legend(loc='upper left')

    axs[2].plot(y1, y2, label='I vs T', color='r',linewidth=0.8)
    axs[2].scatter(y1, y2, color='r')
    axs[2].set_title('I vs T',fontsize=13)
    axs[2].set_xlabel('Temperature (K)',fontsize=13)
    axs[2].set_ylabel('Current (A)',fontsize=13)
    axs[2].legend(loc='upper left')


ani = FuncAnimation(plt.gcf(), animate, interval=1000,cache_frame_data=False)

plt.tight_layout()
plt.show()
