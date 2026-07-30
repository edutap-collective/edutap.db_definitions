"""Sphinx configuration."""

project = "edutap.db_definitions"
extensions = ["myst_parser"]
myst_enable_extensions = ["colon_fence", "deflist"]
exclude_patterns = ["_build", "superpowers"]
html_theme = "alabaster"
