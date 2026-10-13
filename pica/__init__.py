# The one place the PICA release number lives. pyproject.toml reads it
# (dynamic version), and the launchers, CLI and docs import it.
# CITATION.cff cannot import, so tests/test_release_metadata.py checks it.
__version__ = "1.0.7"
