"""Find links to PDF files in a document, remembering the section they sit in.

The extractor walks a document line by line while keeping track of the current
heading stack, so every link it yields carries the categories (subject, type,
level, ...) it was listed under.  Those categories become output subfolders.

Recognised link shapes:

* Markdown inline links — ``[label](https://host/file.pdf)``
* Markdown autolinks — ``<https://host/file.pdf>``
* Bare URLs — ``https://host/file.pdf``
* HTML anchors — ``<a href="https://host/file.pdf">label</a>``

Links found inside Markdown tables get their title from the row's label cells
plus the column header, which is what makes a table of bare ``[PDF]`` links
usable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

#: Link texts that carry no information and should not be used as a title.
GENERIC_LINK_TEXTS = frozenset(
    {
        "",
        "-",
        "--",
        "pdf",
        "pdf file",
        "[pdf]",
        "download",
        "download pdf",
        "link",
        "here",
        "view",
        "open",
        "doc",
        "document",
        "file",
        "αρχείο",
        "λήψη",
        "εδώ",
        "σύνδεσμος",
        "έγγραφο",
        "📄",
        "📎",
        "⬇",
        "⬇️",
    }
)

#: Extensions that mean "definitely not a PDF", even if the link text says PDF.
NON_PDF_EXTENSIONS = frozenset(
    {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz",
        ".doc", ".docx", ".rtf", ".odt",
        ".xls", ".xlsx", ".ods", ".csv",
        ".ppt", ".pptx", ".odp",
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tif", ".tiff",
        ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".mkv", ".wav",
        ".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".xml", ".json", ".txt",
        ".js", ".css", ".exe", ".dmg",
    }
)

LINK_PATTERN = re.compile(
    r"""
      !?\[(?P<mdtext>[^\[\]]*)\]\(\s*<?(?P<mdurl>[^()<>\s]+)>?
        (?:\s+(?:"[^"]*"|'[^']*'|\([^()]*\)))?\s*\)
    | <a\b[^>]*?href\s*=\s*(?P<q>["'])(?P<aurl>[^"']+)(?P=q)[^>]*>(?P<atext>.*?)</a\s*>
    | <(?P<angle>[a-zA-Z][a-zA-Z0-9+.\-]*://[^>\s]+)>
    | (?P<bare>(?<![\w@/(<"'=])[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s<>()\[\]{}"'`]+)
    """,
    re.VERBOSE | re.IGNORECASE | re.DOTALL,
)

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT_UNDERLINE_PATTERN = re.compile(r"^\s*(=+|-{2,})\s*$")
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
BULLET_PATTERN = re.compile(r"^\s*(?:[-*+•]\s+|\d+[.)]\s+|>\s*)+")
INLINE_MARKUP_PATTERN = re.compile(r"[*`~]{1,3}")
#: Underscores only count as emphasis at a word boundary, so that
#: filenames such as ``ap_filosofia.pdf`` survive cleaning intact.
EMPHASIS_UNDERSCORE_PATTERN = re.compile(r"(?<!\w)_{1,3}|_{1,3}(?!\w)")
EMPTY_CELL_VALUES = frozenset({"", "-", "--", "—", "–", "n/a", "na", "χ", "x"})
TRAILING_PUNCTUATION = ")]}>,.;:!?'\"»”’"


@dataclass(frozen=True)
class PdfLink:
    """A single PDF link together with the section it was found in."""

    url: str
    title: str
    categories: tuple[str, ...] = ()
    line: int = 0
    link_text: str = ""
    source: str | None = None
    column: str | None = None

    @property
    def category_path(self) -> str:
        """Categories joined for display, e.g. ``Physics / Curriculum``."""
        return " / ".join(self.categories)


@dataclass
class _Table:
    """Header cells of the Markdown table currently being read."""

    headers: list[str] = field(default_factory=list)


@dataclass
class _Link:
    """A raw link match, before PDF filtering."""

    url: str
    text: str
    start: int
    end: int


def find_links(line: str) -> list[_Link]:
    """Return every link in ``line``, in order of appearance."""
    links: list[_Link] = []
    for match in LINK_PATTERN.finditer(line):
        if match.group("mdurl") is not None:
            url, text = match.group("mdurl"), match.group("mdtext") or ""
        elif match.group("aurl") is not None:
            url, text = match.group("aurl"), _strip_tags(match.group("atext") or "")
        elif match.group("angle") is not None:
            url, text = match.group("angle"), ""
        else:
            url, text = match.group("bare"), ""
        url = _clean_url(url)
        if url:
            links.append(_Link(url, clean_inline(text), match.start(), match.end()))
    return links


def looks_like_pdf(url: str, link_text: str = "") -> bool:
    """Heuristic: does this link point at a PDF file?

    True when the URL path ends in ``.pdf`` (query strings and fragments are
    ignored), or when the link text advertises a PDF and the URL does not
    already carry a different file extension.
    """
    path = urlsplit(url).path
    if path.lower().endswith(".pdf"):
        return True
    extension = _extension(path)
    if extension in NON_PDF_EXTENSIONS:
        return False
    if re.search(r"\.pdf\b", url, re.IGNORECASE):
        return True
    text = link_text.strip().lower().rstrip(".:;")
    return text.endswith("pdf") or text.startswith("pdf")


def is_ambiguous(url: str) -> bool:
    """True when a link has no file extension, so only the server can tell.

    Such links are candidates for ``--probe``: a HEAD request decides whether
    they are PDFs.
    """
    path = urlsplit(url).path
    return not _extension(path)


def extract_links(
    text: str,
    *,
    source: str | None = None,
    max_category_depth: int = 3,
    include_top_heading: bool = False,
    include_ambiguous: bool = False,
) -> list[PdfLink]:
    """Extract PDF links from the Markdown/HTML/plain text in ``text``.

    Args:
        text: document contents.
        source: label stored on every record (usually the input file name).
        max_category_depth: how many heading levels become subfolders.
        include_top_heading: treat a level-1 heading as a category too.  Off by
            default because an ``# H1`` is normally the document title.
        include_ambiguous: also return extension-less links, to be confirmed
            later with a HEAD request.

    Returns:
        PDF links in document order, de-duplicated per (URL, categories) pair.
    """
    lines = text.splitlines()
    heading_stack: list[tuple[int, str]] = []
    fence: str | None = None
    table: _Table | None = None
    found: list[PdfLink] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    index = 0
    while index < len(lines):
        line = lines[index]
        number = index + 1
        stripped = line.strip()

        fence_match = FENCE_PATTERN.match(line)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0]:
                fence = None
            index += 1
            continue
        if fence_match:
            fence = fence_match.group(1)
            index += 1
            continue

        heading = HEADING_PATTERN.match(line)
        if heading:
            _push_heading(heading_stack, len(heading.group(1)), clean_inline(heading.group(2)))
            table = None
            index += 1
            continue

        # Setext heading: a line of text underlined with === or ---.
        if (
            stripped
            and index + 1 < len(lines)
            and SETEXT_UNDERLINE_PATTERN.match(lines[index + 1])
            and not _is_table_row(line)
        ):
            level = 1 if lines[index + 1].strip().startswith("=") else 2
            _push_heading(heading_stack, level, clean_inline(stripped))
            table = None
            index += 2
            continue

        if not stripped:
            table = None
            index += 1
            continue

        # A table header row is the row directly above the |---|---| separator.
        if (
            _is_table_row(line)
            and index + 1 < len(lines)
            and TABLE_SEPARATOR_PATTERN.match(lines[index + 1])
        ):
            table = _Table([clean_inline(cell) for cell, _ in _split_row(line)])
            index += 2
            continue
        if TABLE_SEPARATOR_PATTERN.match(line):
            index += 1
            continue
        if not _is_table_row(line):
            table = None

        for link in find_links(line):
            if not looks_like_pdf(link.url, link.text) and not (
                include_ambiguous and is_ambiguous(link.url)
            ):
                continue
            categories = _categories(heading_stack, max_category_depth, include_top_heading)
            key = (link.url, categories)
            if key in seen:
                continue
            seen.add(key)
            title, column = _title_for(line, link, table, heading_stack)
            found.append(
                PdfLink(
                    url=link.url,
                    title=title,
                    categories=categories,
                    line=number,
                    link_text=link.text,
                    source=source,
                    column=column,
                )
            )
        index += 1

    logger.debug("extracted %d PDF links from %s", len(found), source or "<text>")
    return found


def clean_inline(text: str) -> str:
    """Strip inline Markdown/HTML markup, keeping the human-readable words."""
    text = re.sub(r"!?\[([^\[\]]*)\]\([^()]*\)", r"\1", text)
    text = re.sub(r"<[^<>]+>", " ", text)
    text = INLINE_MARKUP_PATTERN.sub("", text)
    text = EMPHASIS_UNDERSCORE_PATTERN.sub("", text)
    text = text.replace("\\", "")
    return re.sub(r"\s+", " ", text).strip()


def _push_heading(stack: list[tuple[int, str]], level: int, title: str) -> None:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, title))


def _categories(
    stack: list[tuple[int, str]], max_depth: int, include_top: bool
) -> tuple[str, ...]:
    names = [title for level, title in stack if (include_top or level > 1) and title]
    if max_depth >= 0:
        names = names[:max_depth]
    return tuple(names)


def _title_for(
    line: str,
    link: _Link,
    table: _Table | None,
    heading_stack: list[tuple[int, str]],
) -> tuple[str, str | None]:
    """Build a human-readable title for a link, plus its table column name."""
    text = link.text.strip()
    informative = text.lower() not in GENERIC_LINK_TEXTS

    if table is not None and _is_table_row(line):
        cells = _split_row(line)
        column: str | None = None
        labels: list[str] = []
        for position, (cell, start) in enumerate(cells):
            end = start + len(cell)
            if start <= link.start < end or start < link.end <= end:
                if position < len(table.headers):
                    column = table.headers[position] or None
                continue
            value = clean_inline(cell)
            if value.lower() in EMPTY_CELL_VALUES or find_links(cell):
                continue
            labels.append(value)
        parts = [" · ".join(labels)]
        if column:
            parts.append(column)
        if informative:
            parts.append(_pretty_filename(text))
        title = " — ".join(part for part in parts if part)
        if title:
            return title, column

    label = clean_inline(BULLET_PATTERN.sub("", line[: link.start]))
    label = label.rstrip(" \t:—–-·|>")
    if label:
        return label, None
    if informative:
        return _pretty_filename(text), None

    trailing = clean_inline(line[link.end :]).lstrip(" \t:—–-·|").rstrip(" \t.")
    if trailing:
        return trailing, None
    if heading_stack:
        return heading_stack[-1][1], None
    return _pretty_filename(_url_basename(link.url)), None


def _pretty_filename(text: str) -> str:
    """Drop a trailing ``.pdf`` so titles read as titles, not filenames."""
    return re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE).strip() or text


def _url_basename(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^<>]*>", " ", text)


def _clean_url(url: str) -> str:
    """Trim punctuation that belongs to the sentence, not to the URL."""
    url = url.strip().strip("<>")
    while url and url[-1] in TRAILING_PUNCTUATION:
        # A closing paren that balances an opening one is part of the URL.
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        url = url[:-1]
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://\S", url):
        return ""
    return url


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped:
        return False
    return stripped.startswith("|") or stripped.count("|") >= 2


def _split_row(line: str) -> list[tuple[str, int]]:
    """Split a table row into ``(cell, offset_in_line)`` pairs.

    Offsets let the caller work out which cell a link match landed in.  Escaped
    pipes (``\\|``) do not split cells.
    """
    cells: list[tuple[str, int]] = []
    current: list[str] = []
    start = 0
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line) and line[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append(("".join(current), start))
            index += 1
            start = index
            current = []
            continue
        current.append(char)
        index += 1
    cells.append(("".join(current), start))
    if cells and not cells[0][0].strip():
        cells.pop(0)
    if cells and not cells[-1][0].strip():
        cells.pop()
    return cells
