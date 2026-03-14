# docs/conf.py

# -- Project information -----------------------------------------------------
project = 'PICA'
copyright = '2026, Prathamesh Deshmukh'
author = 'Prathamesh Deshmukh'
release = '1.0.3'

# -- General configuration ---------------------------------------------------
extensions = [
    'myst_parser',           # For Markdown support
    'sphinx_rtd_theme',      # For the ReadTheDocs theme
]

# Configure MyST-Parser to allow HTML images (the <img src="..."> tags in your manual)
myst_enable_extensions = [
    "html_image",
]

# Map file extensions to the parser
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'

# Add any paths that contain custom static files (such as style sheets)
html_static_path = ['_static']

# This ensures Sphinx doesn't complain about the pica folder 
# we manually move in the workflow
suppress_warnings = ['image.not_readable']