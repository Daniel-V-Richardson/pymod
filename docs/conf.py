"""Sphinx configuration for bacsys-pymod docs."""

from __future__ import annotations

import sys
from pathlib import Path

# Make `import pymod` work during the build even without an editable install.
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))

import pymod  # noqa: E402

# -- Project information -----------------------------------------------------

project = "bacsys-pymod"
author = "Bacsys"
copyright = "2026, Bacsys"
release = pymod.__version__
version = ".".join(release.split(".")[:2])

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",       # NumPy-style docstrings
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",               # markdown support
    "sphinx_copybutton",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "milestones.md"]

# -- HTML output -------------------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = f"{project} {release}"
html_show_sourcelink = False

# -- Autodoc -----------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
autoclass_content = "both"
napoleon_numpy_docstring = True
napoleon_google_docstring = False

# -- Intersphinx -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# -- MyST --------------------------------------------------------------------

myst_enable_extensions = ["colon_fence", "deflist", "fieldlist", "tasklist"]
myst_heading_anchors = 3

# -- Misc --------------------------------------------------------------------

# Treat unresolved refs as warnings, not errors (so the build doesn't fail
# on stdlib types we haven't mapped). Tighten later if desired.
nitpicky = False
