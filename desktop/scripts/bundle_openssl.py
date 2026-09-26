"""Keep one consistent OpenSSL in the frozen bundle.

Both ``cryptography`` and Python's own ``_ssl`` can carry an OpenSSL, and
PyInstaller flattens every collected library into one directory — so two files
named ``libssl.3.dylib`` collide and one wins by collection order. On macOS that
produced a mismatched pair: ``libcrypto`` from cryptography at 3.6.3 beside an
older ``libssl``, after which cryptography's extension failed to load with
``Symbol not found: _SSL_get0_group_name``, taking pdfminer and the whole PDF
backend with it.

Kept out of the spec file so it can be tested: on Linux cryptography links
OpenSSL statically, so a Linux build never reaches the interesting branch.
"""

from __future__ import annotations

from pathlib import PurePath

#: PyInstaller entries are ``(destination, source, kind)`` triples.
TLS_PREFIXES = ("libssl", "libcrypto")


def is_tls_library(destination: str) -> bool:
    """True for a libssl/libcrypto entry, whatever version suffix it carries."""
    return PurePath(destination).name.startswith(TLS_PREFIXES)


def ships_with_cryptography(source: str) -> bool:
    """True when this file came from the cryptography package's own wheel.

    Covers both layouts: ``cryptography/.dylibs/…`` on macOS and a sibling
    ``cryptography.libs/…`` directory on Linux.
    """
    return any(part.startswith("cryptography") for part in PurePath(source).parts)


def align_tls_libraries(binaries):
    """Point every TLS library at cryptography's copy of it.

    Args:
        binaries: PyInstaller's ``(destination, source, kind)`` entries.

    Returns:
        ``(entries, substitutions)`` — the rewritten entries, and a list of
        ``(name, old_source, new_source)`` describing what changed. Both are
        returned unchanged when cryptography ships no TLS libraries of its own,
        which is what a statically linked build looks like.
    """
    preferred = {
        PurePath(destination).name: source
        for destination, source, _kind in binaries
        if is_tls_library(destination) and ships_with_cryptography(source)
    }
    if not preferred:
        return list(binaries), []

    rewritten, substitutions = [], []
    for destination, source, kind in binaries:
        replacement = preferred.get(PurePath(destination).name)
        if replacement is not None and replacement != source:
            substitutions.append((PurePath(destination).name, source, replacement))
            source = replacement
        rewritten.append((destination, source, kind))
    return rewritten, substitutions
