# docs/conf.py

# Make Sphinx aware of the repo root so relative paths resolve
import os
import sys
sys.path.insert(0, os.path.abspath('..'))

# -- Project information -----------------------------------------------------
project = 'PICA'
copyright = '2026, Prathamesh Deshmukh'
author = 'Prathamesh Deshmukh'
release = '1.0.5'

# -- General configuration ---------------------------------------------------
extensions = [
    'myst_parser',           # For Markdown support
    'sphinx_rtd_theme',      # For the ReadTheDocs theme
]

# Configure MyST-Parser to allow HTML images (the <img src="..."> tags in your manual)
myst_enable_extensions = [
    "html_image",
    "colon_fence",
]

# Auto-generate GitHub-style anchors for headings (h1-h4) so the manual's
# in-page Table of Contents links (#1-overview, #61-ultra-low-resistance-...)
# resolve on Read the Docs, not just on GitHub
myst_heading_anchors = 4

# Map file extensions to the parser
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'

# Show the full section tree in the sidebar from the landing page onwards,
# instead of collapsing navigation to top-level entries only
html_theme_options = {
    'collapse_navigation': False,
    'sticky_navigation': True,
    'navigation_depth': 4,
    'titles_only': False,
}

# Add any paths that contain custom static files (such as style sheets)
html_static_path = []

# This copies the pica/assets folder into the build output
html_extra_path = ["../pica/assets"]

# This ensures Sphinx doesn't complain about the pica folder
# we manually move in the workflow. Image paths (Images/, LOGO/) resolve at
# runtime via html_extra_path, so the build-time readability check is noise.
suppress_warnings = ["etoc.toctree", "image.not_readable"]