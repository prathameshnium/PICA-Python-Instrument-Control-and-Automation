import os
import shutil
from setuptools import setup


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

for dest_subdir, files in DATA_FILES_TO_COPY.items():
    dest_dir = os.path.join(PICA_PKG_DIR, dest_subdir)
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    
    for file_path in files:
        if os.path.exists(file_path):
            shutil.copy(file_path, dest_dir)


setup()
