"""Turn titles and URLs into safe file and folder names.

Names keep their original alphabet — Greek headings stay Greek — but lose the
characters that break filesystems, shells or Windows shares.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import unquote, urlsplit

#: Characters no filesystem we care about accepts in a path component.
UNSAFE_CHARACTERS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
#: Names Windows reserves, whatever the extension.
RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{n}" for n in range(1, 10)]
    + [f"lpt{n}" for n in range(1, 10)]
)
MAX_COMPONENT_LENGTH = 100


def sanitize_component(name: str, *, max_length: int = MAX_COMPONENT_LENGTH) -> str:
    """Make ``name`` usable as a single file or folder name.

    Unsafe characters become ``-``, whitespace becomes ``_``, and the result is
    trimmed to ``max_length`` characters without cutting mid-character.
    """
    name = unicodedata.normalize("NFC", name or "")
    # Fold tabs and newlines into spaces first, so they end up as underscores
    # rather than as dashes from the control-character rule below.
    name = re.sub(r"\s+", " ", name)
    name = UNSAFE_CHARACTERS.sub("-", name)
    name = name.replace("·", "-").replace("—", "-").replace("–", "-")
    name = re.sub(r"\s+", "_", name.strip())
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("._-")
    if len(name) > max_length:
        name = name[:max_length].rstrip("._-")
    if name.lower() in RESERVED_NAMES:
        name = f"{name}_"
    return name or "untitled"


def category_parts(categories: tuple[str, ...] | list[str]) -> list[str]:
    """Sanitize each category into one path component."""
    return [sanitize_component(category) for category in categories if category]


def stem_from_url(url: str) -> str:
    """Filename stem taken from the URL, e.g. ``.../ap_filosofia.pdf`` -> ``ap_filosofia``."""
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    return sanitize_component(name) if name.strip() else url_digest(url)


def stem_from_title(title: str, url: str = "") -> str:
    """Filename stem taken from the link's title, falling back to the URL."""
    stem = sanitize_component(title)
    if stem == "untitled" and url:
        return stem_from_url(url)
    return stem


def url_digest(url: str, length: int = 8) -> str:
    """Short stable hash of a URL, used to break filename collisions."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:length]
