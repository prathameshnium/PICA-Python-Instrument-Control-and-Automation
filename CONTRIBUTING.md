# Contributing to PICA

Thank you for your interest in PICA (Python-based Instrument Control and Automation)
The UGC-DAE Consortium for Scientific Research has the overarching objective of facilitating researchers by providing convenient access to a wide range of advanced research facilities. In alignment with this mission, the present software has been developed to serve a similar purpose.

Originally, this project was conceived and implemented for our Advanced Transport Measurement System. We are now extending its scope beyond this specific system, with the intention of establishing a general software framework upon which users can contribute and integrate additional modules. All contributed modules are expected to be rigorously tested on the corresponding experimental instruments to ensure reliability and scientific validity.

We welcome requests for new features and are open to collaboration in developing modules tailored to the specific requirements of individual users. Feedback, contributions, and constructive criticism from both the scientific and software development communities are explicitly encouraged, as they are essential for the continuous improvement and robustness of this software platform.


## How to Report Bugs
If you encounter an error while running a measurement:
1.  Go to the [Issues tab](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues).
2.  Click "New Issue".
3.  Include the error message from the PICA console and which instrument you were using (e.g., Keithley 2400).

## How to Suggest Features
If you have a script for a new instrument (e.g., a lock-in amplifier) that you would like to include:
1.  Fork this repository.
2.  Add your script to a new folder in pica (e.g., `SRS_830/`).
3.  Submit a Pull Request (PR) describing what the instrument does.
