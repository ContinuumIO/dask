#
# dask documentation build configuration file, created by
# sphinx-quickstart on Sun Jan  4 08:58:22 2015.
#
# This file is execfile()d with the current directory set to its containing dir.
#
# Note that not all possible configuration values are present in this
# autogenerated file.
#
# All configuration values have a default; values that are commented out
# serve to show the default.

from __future__ import annotations

import os

# Add any Sphinx extension module names here, as strings. They can be extensions
# coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
import sys

import sphinx_autosummary_accessors

# -- General configuration -----------------------------------------------------

# If your documentation needs a minimal Sphinx version, state it here.
# needs_sphinx = '1.0'


# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
sys.path.insert(0, os.path.abspath("../../"))

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",
    "sphinx.ext.mathjax",
    "sphinx.ext.intersphinx",
    "sphinx.ext.autosummary",
    "sphinx_autosummary_accessors",
    "sphinx.ext.extlinks",
    "sphinx.ext.viewcode",
    "numpydoc",
    "sphinx_click.ext",
    "dask_sphinx_theme.ext.dask_config_sphinx_ext",
    "sphinx_tabs.tabs",
    "sphinx_remove_toctrees",
    "IPython.sphinxext.ipython_console_highlighting",
    "IPython.sphinxext.ipython_directive",
]

# Turn on sphinx.ext.autosummary
autosummary_generate = [
    "array-api.rst",
    "bag-api.rst",
    "dataframe-api.rst",
    "delayed-api.rst",
]

# Add __init__ doc (ie. params) to class summaries
autoclass_content = "both"

# If no docstring, inherit from base class
autodoc_inherit_docstrings = True

# Include namespaces from class/method signatures
add_module_names = True

numpydoc_show_class_members = False

sphinx_tabs_disable_tab_closing = True

# Remove individual API pages from sphinx toctree to prevent long build times.
# See https://github.com/dask/dask/issues/8227.
remove_from_toctrees = ["generated/*"]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates", sphinx_autosummary_accessors.templates_path]

# The suffix of source filenames.
source_suffix = ".rst"

# The encoding of source files.
# source_encoding = 'utf-8-sig'

# The master toctree document.
master_doc = "index"

# General information about the project.
project = "Dask"
copyright = "2014-2018, Anaconda, Inc. and contributors"

# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
# language = None

# There are two options for replacing |today|: either, you set today to some
# non-false value, then it is used:
# today = ''
# Else, today_fmt is used as the format for a strftime call.
# today_fmt = '%B %d, %Y'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
exclude_patterns: list[str] = []

# The reST default role (used for this markup: `text`) to use for all documents.
# default_role = None

# If true, '()' will be appended to :func: etc. cross-reference text.
# add_function_parentheses = True

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
# add_module_names = True

# If true, sectionauthor and moduleauthor directives will be shown in the
# output. They are ignored by default.
# show_authors = False

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "default"

# A list of ignored prefixes for module index sorting.
# modindex_common_prefix = []


# -- Options for HTML output ---------------------------------------------------

html_theme = "dask_sphinx_theme"

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation.
html_theme_options = {"logo_only": True}

# Add any paths that contain custom themes here, relative to this directory.
# html_theme_path = []

# The name for this set of Sphinx documents.  If None, it defaults to
# "<project> v<release> documentation".
# html_title = None

# A shorter title for the navigation bar.  Default is the same as html_title.
# html_short_title = None

# The name of an image file (relative to this directory) to place at the top
# of the sidebar.
# html_logo = "images/dask_horizontal_white_no_pad.svg"


# The name of an image file (within the static path) to use as favicon of the
# docs.  This file should be a Windows icon file (.ico) being 16x16 or 32x32
# pixels large.
# html_favicon = None

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# If not '', a 'Last updated on:' timestamp is inserted at every page bottom,
# using the given strftime format.
# html_last_updated_fmt = '%b %d, %Y'

# If true, SmartyPants will be used to convert quotes and dashes to
# typographically correct entities.
# html_use_smartypants = True

# Custom sidebar templates, maps document names to template names.
# html_sidebars = {}

# Additional templates that should be rendered to pages, maps page names to
# template names.
# html_additional_pages = {}

# If false, no module index is generated.
# html_domain_indices = True

# If false, no index is generated.
# html_use_index = True

# If true, the index is split into individual pages for each letter.
# html_split_index = False

# If true, links to the reST sources are added to the pages.
# html_show_sourcelink = True

# If true, "Created using Sphinx" is shown in the HTML footer. Default is True.
# html_show_sphinx = True

# If true, "(C) Copyright ..." is shown in the HTML footer. Default is True.
# html_show_copyright = True

# If true, an OpenSearch description file will be output, and all pages will
# contain a <link> tag referring to it.  The value of this option must be the
# base URL from which the finished HTML is served.
# html_use_opensearch = ''

# This is the file name suffix for HTML files (e.g. ".xhtml").
# html_file_suffix = None

# Output file base name for HTML help builder.
htmlhelp_basename = "daskdoc"


# -- Options for LaTeX output --------------------------------------------------

latex_elements: dict[str, str] = {
    # The paper size ('letterpaper' or 'a4paper').
    #'papersize': 'letterpaper',
    # The font size ('10pt', '11pt' or '12pt').
    #'pointsize': '10pt',
    # Additional stuff for the LaTeX preamble.
    #'preamble': '',
}

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title, author, documentclass [howto/manual]).
latex_documents = [
    (master_doc, "dask.tex", "dask Documentation", "Dask Development Team", "manual")
]

# The name of an image file (relative to this directory) to place at the top of
# the title page.
# latex_logo = None

# For "manual" documents, if this is true, then toplevel headings are parts,
# not chapters.
# latex_use_parts = False

# If true, show page references after internal links.
# latex_show_pagerefs = False

# If true, show URL addresses after external links.
# latex_show_urls = False

# Documents to append as an appendix to all manuals.
# latex_appendices = []

# If false, no module index is generated.
# latex_domain_indices = True


# -- Options for manual page output --------------------------------------------

# One entry per manual page. List of tuples
# (source start file, name, description, authors, manual section).
man_pages = [(master_doc, "dask", "dask Documentation", ["Dask Development Team"], 1)]

# If true, show URL addresses after external links.
# man_show_urls = False


# -- Options for Texinfo output ------------------------------------------------

# Grouping the document tree into Texinfo files. List of tuples
# (source start file, target name, title, author,
#  dir menu entry, description, category)
texinfo_documents = [
    (
        master_doc,
        "Dask",
        "dask Documentation",
        "Dask Development Team",
        "Dask",
        "One line description of project.",
        "Miscellaneous",
    )
]

# Documents to append as an appendix to all manuals.
# texinfo_appendices = []

# If false, no module index is generated.
# texinfo_domain_indices = True

# How to display URL addresses: 'footnote', 'no', or 'inline'.
# texinfo_show_urls = 'footnote'


# -- Options for Epub output ---------------------------------------------------

# Bibliographic Dublin Core info.
epub_title = "Dask"
epub_author = "Dask Development Team"
epub_publisher = "Anaconda Inc"
epub_copyright = "2014-2018, Anaconda, Inc. and contributors"

# The language of the text. It defaults to the language option
# or en if the language is not set.
# epub_language = ''

# The scheme of the identifier. Typical schemes are ISBN or URL.
# epub_scheme = ''

# The unique identifier of the text. This can be a ISBN number
# or the project homepage.
# epub_identifier = ''

# A unique identification for the text.
# epub_uid = ''

# A tuple containing the cover image and cover page html template filenames.
# epub_cover = ()

# HTML files that should be inserted before the pages created by sphinx.
# The format is a list of tuples containing the path and title.
# epub_pre_files = []

# HTML files that should be inserted after the pages created by sphinx.
# The format is a list of tuples containing the path and title.
# epub_post_files = []

# A list of files that should not be packed into the epub file.
# epub_exclude_files = []

# The depth of the table of contents in toc.ncx.
# epub_tocdepth = 3

# Allow duplicate toc entries.
# epub_tocdup = True

extlinks = {
    "issue": ("https://github.com/dask/dask/issues/%s", "GH#"),
    "pr": ("https://github.com/dask/dask/pull/%s", "GH#"),
}

#  --Options for sphinx extensions -----------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "pandas": (
        "https://pandas.pydata.org/pandas-docs/stable/",
        "https://pandas.pydata.org/pandas-docs/stable/objects.inv",
    ),
    "numpy": (
        "https://numpy.org/doc/stable/",
        "https://numpy.org/doc/stable/objects.inv",
    ),
    "asyncssh": (
        "https://asyncssh.readthedocs.io/en/latest/",
        "https://asyncssh.readthedocs.io/en/latest/objects.inv",
    ),
    "pyarrow": ("https://arrow.apache.org/docs/", None),
    "zarr": (
        "https://zarr.readthedocs.io/en/latest/",
        "https://zarr.readthedocs.io/en/latest/objects.inv",
    ),
    "skimage": ("https://scikit-image.org/docs/dev/", None),
}

# Redirects
# https://tech.signavio.com/2017/managing-sphinx-redirects
redirect_files = [
    # old html, new html
    ("bytes.html", "remote-data-services.html"),
    ("array-overview.html", "array.html"),
    ("array-ghost.html", "array-overlap.html"),
    ("dataframe-overview.html", "dataframe.html"),
    ("dataframe-performance.html", "dataframe-best-practices.html"),
    ("delayed-overview.html", "delayed.html"),
    ("educational-resources.html", "presentations.html"),
    ("scheduler-choice.html", "setup.html"),
    ("diagnostics.html", "diagnostics-local.html"),
    ("inspect.html", "graphviz.html"),
    ("funding.html", "https://dask.org/#supported-by"),
    ("examples-tutorials.html", "https://examples.dask.org"),
    ("examples/array-extend.html", "https://examples.dask.org"),
    ("examples/array-hdf5.html", "https://examples.dask.org"),
    ("examples/array-numpy.html", "https://examples.dask.org"),
    ("examples/array-random.html", "https://examples.dask.org"),
    ("examples/bag-json.html", "https://examples.dask.org"),
    ("examples/bag-word-count-hdfs.html", "https://examples.dask.org"),
    ("examples/dataframe-csv.html", "https://examples.dask.org"),
    ("examples/dataframe-hdf5.html", "https://examples.dask.org"),
    ("examples/delayed-array.html", "https://examples.dask.org"),
    ("examples/delayed-custom.html", "https://examples.dask.org"),
    ("docs.html", "index.html"),
    ("use-cases.html", "https://stories.dask.org"),
    ("bag-overview.html", "bag.html"),
    ("distributed.html", "https://distributed.dask.org"),
    ("institutional-faq.html", "faq.html"),
    ("cite.html", "faq.html#how-do-I-cite-dask"),
    ("remote-data-services.html", "how-to/connect-to-remote-data.html"),
    ("debugging.html", "how-to/debug.html"),
    ("setup.html", "deploying.html"),
    ("how-to/deploy-dask-clusters.html", "deploying.html"),
    ("setup/cli.html", "deploying-cli.html"),
    ("how-to/deploy-dask/cli.html", "deploying-cli.html"),
    ("setup/cloud.html", "deploying-cloud.html"),
    ("how-to/deploy-dask/cloud.html", "deploying-cloud.html"),
    ("setup/docker.html", "hdeploying-docker.html"),
    ("how-to/deploy-dask/docker.html", "deploying-docker.html"),
    ("setup/hpc.html", "deploying-hpc.html"),
    ("how-to/deploy-dask/hpc.html", "deploying-hpc.html"),
    ("setup/kubernetes.html", "deploying-kubernetes.html"),
    ("how-to/deploy-dask/kubernetes.html", "deploying-kubernetes.html"),
    ("setup/python-advanced.html", "deploying-python-advanced.html"),
    ("how-to/deploy-dask/python-advanced.html", "deploying-python-advanced.html"),
    ("setup/single-distributed.html", "deploying-python.html"),
    ("how-to/deploy-dask/single-distributed.html", "deploying-python.html"),
    ("setup/single-machine.html", "scheduling.html"),
    ("how-to/deploy-dask/single-machine.html", "scheduling.html"),
    ("setup/ssh.html", "deploying-ssh.html"),
    ("how-to/deploy-dask/ssh.html", "deploying-ssh.html"),
    ("setup/adaptive.html", "how-to/adaptive.html"),
    ("setup/custom-startup.html", "how-to/customize-initialization.html"),
    ("setup/environment.html", "how-to/manage-environments.html"),
    ("setup/prometheus.html", "how-to/setup-prometheus.html"),
]


redirect_template = """\
<html>
  <head>
    <meta http-equiv="refresh" content="1; url={new}" />
    <script>
      window.location.href = "{new}"
    </script>
  </head>
</html>
"""

# Rate limiting issue for github: https://github.com/sphinx-doc/sphinx/issues/7388
linkcheck_ignore = [
    r"^https?:\/\/(?:www\.)?github.com\/",
    r"^https?:\/\/localhost(?:[:\/].+)?$",
]

doctest_global_setup = """
import numpy as np
"""


def copy_legacy_redirects(app, docname):
    if app.builder.name == "html":
        for html_src_path, new in redirect_files:
            # add ../ to old nested paths
            new = f"{'../' * html_src_path.count('/')}{new}"
            page = redirect_template.format(new=new)
            target_path = app.outdir + "/" + html_src_path
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as f:
                f.write(page)


def setup(app):
    app.connect("build-finished", copy_legacy_redirects)
