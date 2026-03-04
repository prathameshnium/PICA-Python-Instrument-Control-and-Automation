project = 'PICA (Python Instrument Control and Automation)'
copyright = '2026, Prathamesh Deshmukh'
author = 'Prathamesh Deshmukh'

# Activate the MyST Parser to enable Markdown support alongside standard extensions
extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
]

# Instruct Sphinx to read both Markdown and reStructuredText formats
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

# Utilize the standard Read the Docs visual theme
html_theme = 'sphinx_rtd_theme'

myst_enable_extensions = [
    "html_image",       # Allows raw HTML <img> tags to render
    "html_admonition",  # Allows raw HTML blocks
    "colon_fence",      # Enables ::: fenced directives
]