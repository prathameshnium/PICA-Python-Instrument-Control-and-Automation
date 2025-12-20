from setuptools import setup
from setuptools.command.build_py import build_py as build_py_orig
import os
import shutil

# --- Configuration ---
# Get the directory where setup.py is located
SETUP_DIR = os.path.dirname(os.path.abspath(__file__))
PICA_PKG_DIR = 'pica'

# Define files to be copied, relative to the project root
DATA_FILES_TO_COPY = {
    # Destination subdir in package: list of source files
    '': [
        'README.md',
        'LICENSE',
        'CHANGELOG.md',
        'CONTRIBUTING.md',
    ],
    'docs': [
        'docs/User_Manual.md',
        'docs/Instruments_Manuals_Lists.md',
    ]
}


class build_py(build_py_orig):
    """
    Custom build command to copy data files into the package source tree
    before the main build, ensuring they are included in the wheel.
    """
    def run(self):
        print("--- [Custom Build] Running robust data file copy ---")
        
        # Copy the files using absolute paths
        for dest_subdir, files in DATA_FILES_TO_COPY.items():
            # Destination directory inside the 'pica' package
            dest_dir = os.path.join(PICA_PKG_DIR, dest_subdir)
            
            if not os.path.exists(dest_dir):
                print(f"--- [Custom Build] Creating directory: {dest_dir}")
                os.makedirs(dest_dir)
            
            for file_name in files:
                # Construct absolute source path
                source_path = os.path.join(SETUP_DIR, file_name)
                
                if os.path.exists(source_path):
                    print(f"--- [Custom Build] Copying '{source_path}' to '{dest_dir}'")
                    shutil.copy(source_path, dest_dir)
                else:
                    # This warning is critical for debugging
                    print(f"--- [Custom Build] WARNING: Source file not found, cannot copy: {source_path}")
        
        # Then, run the original build_py command to continue the build
        print("--- [Custom Build] Handing over to original build process ---")
        build_py_orig.run(self)


# --- Main setup ---
# All project metadata is in pyproject.toml.
# We only use setup.py to inject our custom build command.
setup(
    cmdclass={'build_py': build_py}
)
