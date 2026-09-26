"""Keep one consistent OpenSSL in the frozen bundle.

Several packages can bring an OpenSSL along — Python's own ``_ssl``, Homebrew's
copy that ``cryptography``'s extension was built against — and PyInstaller
flattens every collected library into one directory. Its binary list is unique
by filename, so when two directories each offer a ``libssl.3.dylib`` the
collision is already resolved by the time anything here runs, and the survivors
can come from different places.

That is what broke the Intel build: ``libcrypto`` from Homebrew at 3.6.3 beside
a ``libssl`` from the Python framework at 3.0.x. ``cryptography``'s extension
resolves ``@rpath`` to the bundle root, found the older ``libssl``, and failed
with ``Symbol not found: _SSL_get0_group_name`` — taking pdfminer, and with it
markitdown's whole PDF backend, down.

The fix is to take every TLS library from a single directory: the one holding
the newest ``libcrypto``, since that is the OpenSSL the extension was built
against.

Kept out of the spec file so it can be tested, because a Linux build never
reaches this code — cryptography links OpenSSL statically there.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePath

#: PyInstaller entries are ``(destination, source, kind)`` triples.
TLS_PREFIXES = ("libssl", "libcrypto")
#: The banner OpenSSL compiles into its own libraries, e.g. "OpenSSL 3.6.3".
OPENSSL_VERSION = re.compile(rb"OpenSSL\s+(\d+)\.(\d+)\.(\d+)")


def is_tls_library(destination: str) -> bool:
    """True for a libssl/libcrypto entry, whatever version suffix it carries."""
    return PurePath(destination).name.startswith(TLS_PREFIXES)


def openssl_version(path: str | Path) -> tuple[int, ...]:
    """The newest OpenSSL version a library claims, or () if it claims none.

    Only ``libcrypto`` carries the banner; ``libssl`` generally does not, which
    is why the choice of directory is made on ``libcrypto``.
    """
    try:
        blob = Path(path).read_bytes()
    except OSError:
        return ()
    versions = {tuple(int(part) for part in match) for match in OPENSSL_VERSION.findall(blob)}
    return max(versions) if versions else ()


def align_tls_libraries(binaries):
    """Take every TLS library from the directory holding the newest libcrypto.

    Args:
        binaries: PyInstaller's ``(destination, source, kind)`` entries.

    Returns:
        ``(entries, substitutions)`` — the rewritten entries, and a list of
        ``(name, old_source, new_source)`` describing what changed. Entries are
        returned unchanged when there is nothing to decide: no TLS libraries, a
        single source directory, or no ``libcrypto`` to judge versions by.
    """
    tls = [
        (destination, source) for destination, source, _kind in binaries if is_tls_library(destination)
    ]
    if not tls:
        return list(binaries), []

    directories = {str(PurePath(source).parent) for _destination, source in tls}
    if len(directories) < 2:
        return list(binaries), []

    # Judge each directory by the libcrypto it holds: that is the OpenSSL the
    # extension linking against it was built for.
    ranked = []
    for directory in directories:
        for candidate in Path(directory).glob("libcrypto*"):
            version = openssl_version(candidate)
            if version:
                ranked.append((version, directory))
                break
    if not ranked:
        return list(binaries), []

    _version, chosen = max(ranked)

    rewritten, substitutions = [], []
    for destination, source, kind in binaries:
        if is_tls_library(destination) and str(PurePath(source).parent) != chosen:
            sibling = Path(chosen) / PurePath(source).name
            if sibling.exists():
                substitutions.append((PurePath(destination).name, source, str(sibling)))
                source = str(sibling)
        rewritten.append((destination, source, kind))
    return rewritten, substitutions
