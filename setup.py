"""Build hook only: all project metadata lives in pyproject.toml.

The launcher's Help menu shows the README, license, change log and user
manual, so copies of those files have to ship inside the ``pica`` package.
They are kept once, at the repository root, and copied into ``pica/`` here
at build time (the copies are gitignored). ``[tool.setuptools.package-data]``
in pyproject.toml lists them so the wheel picks them up at their copied
location, and MANIFEST.in excludes the copies from the sdist so a stale
local copy can never ship.
"""
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py as build_py_orig

SETUP_DIR = os.path.dirname(os.path.abspath(__file__))
PICA_PKG_DIR = os.path.join(SETUP_DIR, "pica")

DATA_FILES_TO_COPY = {
    "": ["README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md"],
    "docs": ["docs/User_Manual.md", "docs/Instruments_Manuals_Lists.md"],
}


class build_py(build_py_orig):
    """Copy the root documentation into pica/ before the normal build."""

    def run(self):
        for dest_subdir, files in DATA_FILES_TO_COPY.items():
            dest_dir = os.path.join(PICA_PKG_DIR, dest_subdir)
            os.makedirs(dest_dir, exist_ok=True)
            for rel in files:
                src = os.path.join(SETUP_DIR, rel)
                if os.path.exists(src):
                    shutil.copy(src, dest_dir)
                else:
                    print(f"[build_py] WARNING: {src} not found; not copied")
        build_py_orig.run(self)


setup(cmdclass={"build_py": build_py})
