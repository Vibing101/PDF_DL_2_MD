"""pdf2md — find PDF links in a document, download them, convert them to Markdown.

The package is organised as a small pipeline:

* :mod:`pdf2md.extract`  — parse an input document into :class:`~pdf2md.extract.PdfLink` records
* :mod:`pdf2md.download` — fetch a link into a temporary file and validate it is a PDF
* :mod:`pdf2md.convert`  — hand the temporary file to Microsoft's ``markitdown``
* :mod:`pdf2md.pipeline` — glue the above together and write the category tree
* :mod:`pdf2md.cli`      — command line entry point
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
