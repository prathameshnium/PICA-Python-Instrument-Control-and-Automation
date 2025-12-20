from setuptools import setup
from setuptools.command.build_py import build_py as build_py_orig
import os
import shutil

PICA_PKG_DIR = 'pica'
DATA_FILES_TO_COPY = {
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
    Custom build command to copy data files into the package source tree.
    """
    def run(self):
        # First, copy the files
        print("--- Custom build_py: Copying data files ---")
        for dest_subdir, files in DATA_FILES_TO_COPY.items():
            dest_dir = os.path.join(PICA_PKG_DIR, dest_subdir)
            if not os.path.exists(dest_dir):
                print(f"Creating directory: {dest_dir}")
                os.makedirs(dest_dir)
            
            for file_path in files:
                if os.path.exists(file_path):
                    print(f"Copying {file_path} to {dest_dir}")
                    shutil.copy(file_path, dest_dir)
                else:
                    print(f"WARNING: File not found, cannot copy: {file_path}")
        
        # Then run the original build_py command
        print("--- Custom build_py: Running original build command ---")
        build_py_orig.run(self)

setup(
    cmdclass={'build_py': build_py}
)