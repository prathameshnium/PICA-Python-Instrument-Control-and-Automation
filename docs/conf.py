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
    'sphinx_sitemap',        # Generates sitemap.xml for search engines
    'sphinxext.opengraph',   # Open Graph / social-preview meta tags
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
# The Novocontrol runbook is an internal lab document — kept in the repo
# (linked from the README on GitHub) but not published on the docs sites
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store',
                    'Novocontrol_GPIB_Runbook.md']

# Sets <html lang="en"> — search engines use this for language targeting
language = 'en'

# -- SEO: canonical URL, sitemap, social previews -----------------------------
# The same conf builds two hosts: GitHub Pages (primary docs site) and Read
# the Docs. Each build declares itself as canonical so neither site is
# deranked as duplicate content, and each gets a correct sitemap.xml.
_GITHUB_PAGES_URL = (
    'https://prathameshnium.github.io/'
    'PICA-Python-Instrument-Control-and-Automation/'
)
_RTD_URL = (
    'https://pica-python-instrument-control-and-automation.'
    'readthedocs.io/en/latest/'
)

if os.environ.get('READTHEDOCS'):
    html_baseurl = os.environ.get('READTHEDOCS_CANONICAL_URL', _RTD_URL)
else:
    html_baseurl = _GITHUB_PAGES_URL

# sphinx-sitemap: html_baseurl already carries the language/version prefix on
# RTD, so don't add another {lang}/{version} segment to sitemap entries
sitemap_url_scheme = '{link}'

# sphinxext-opengraph: og:title/description are derived per page; the image
# lives at the site root because html_extra_path copies pica/assets there
ogp_site_url = html_baseurl
ogp_site_name = 'PICA — Python Instrument Control and Automation'
ogp_image = _GITHUB_PAGES_URL + 'LOGO/PICA_LOGO_NBG.png'
ogp_type = 'website'
ogp_description_length = 200
ogp_enable_meta_description = True

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'

# Descriptive, keyword-bearing suffix for every page's <title> tag
# (rendered as "<page title> — <html_title>")
html_title = 'Python Instrument Control and Automation'
html_short_title = 'PICA Documentation'

# Sidebar branding (paths relative to this conf.py; copied into _static)
html_logo = '../pica/assets/LOGO/PICA_LOGO_NBG.png'
html_favicon = '../pica/assets/LOGO/PICA_LOGO.ico'

# Show the full section tree in the sidebar from the landing page onwards,
# instead of collapsing navigation to top-level entries only
html_theme_options = {
    'collapse_navigation': False,
    'sticky_navigation': True,
    'navigation_depth': 4,
    'titles_only': False,
    'logo_only': False,
}

# Add any paths that contain custom static files (such as style sheets)
html_static_path = []

# This copies the pica/assets folder into the build output; robots.txt keeps
# the site's crawler policy consistent across GitHub Pages and Read the Docs
html_extra_path = ["../pica/assets", "../robots.txt"]

# This ensures Sphinx doesn't complain about the pica folder
# we manually move in the workflow. Image paths (Images/, LOGO/) resolve at
# runtime via html_extra_path, so the build-time readability check is noise.
suppress_warnings = ["etoc.toctree", "image.not_readable"]
