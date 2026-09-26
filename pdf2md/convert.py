"""Convert a local PDF to Markdown with Microsoft's markitdown.

See https://github.com/microsoft/markitdown.  The import is deferred so that
extraction and ``--dry-run`` keep working when markitdown is not installed.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

INSTALL_HINT = (
    "markitdown is required to convert PDFs. Install it with:\n"
    "    pip install 'markitdown[pdf]'\n"
    "(see https://github.com/microsoft/markitdown)"
)


class ConversionError(RuntimeError):
    """Raised when markitdown cannot turn a file into Markdown."""


class Converter:
    """Thin wrapper around :class:`markitdown.MarkItDown`.

    One instance is shared by all worker threads; markitdown itself is
    stateless per ``convert`` call.
    """

    def __init__(self) -> None:
        self._markitdown = None

    def _engine(self):
        if self._markitdown is None:
            try:
                from markitdown import MarkItDown
            except ImportError as error:  # pragma: no cover - depends on env
                raise ConversionError(INSTALL_HINT) from error
            self._markitdown = MarkItDown()
        return self._markitdown

    def convert(self, path: Path) -> str:
        """Return the Markdown for ``path``.

        An empty string means markitdown found no text — typically a scanned
        PDF with no OCR layer.
        """
        try:
            result = self._engine().convert(str(path))
        except Exception as error:  # markitdown raises a variety of types
            raise ConversionError(f"markitdown failed: {error}") from error
        markdown = getattr(result, "markdown", None) or getattr(result, "text_content", None)
        return (markdown or "").strip()


def convert_document_to_markdown(path: Path) -> str:
    """Convert an *input* document (``.docx``, ``.pdf``, ``.xlsx`` ...) to Markdown.

    Used so that the link list itself may be any format markitdown reads, not
    just Markdown.
    """
    return Converter().convert(path)
