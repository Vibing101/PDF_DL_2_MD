"""Tests for the build-time OpenSSL alignment.

These matter because a Linux build never reaches the interesting branch:
cryptography links OpenSSL statically there, so only macOS hits the collision
this code exists to fix.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC_HELPER = Path(__file__).resolve().parent.parent / "desktop" / "scripts" / "bundle_openssl.py"

_spec = importlib.util.spec_from_file_location("bundle_openssl", SPEC_HELPER)
bundle_openssl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle_openssl)

align_tls_libraries = bundle_openssl.align_tls_libraries
is_tls_library = bundle_openssl.is_tls_library
ships_with_cryptography = bundle_openssl.ships_with_cryptography

# What the Intel runner actually collected: cryptography brought its own pair,
# and Python's _ssl brought an older libssl under the same name.
CRYPTOGRAPHY_SSL = "/py/site-packages/cryptography/.dylibs/libssl.3.dylib"
CRYPTOGRAPHY_CRYPTO = "/py/site-packages/cryptography/.dylibs/libcrypto.3.dylib"
SYSTEM_SSL = "/usr/local/opt/openssl@3/lib/libssl.3.dylib"
SYSTEM_CRYPTO = "/usr/local/opt/openssl@3/lib/libcrypto.3.dylib"

MACOS_BINARIES = [
    ("libssl.3.dylib", SYSTEM_SSL, "BINARY"),
    ("libcrypto.3.dylib", CRYPTOGRAPHY_CRYPTO, "BINARY"),
    ("cryptography/.dylibs/libssl.3.dylib", CRYPTOGRAPHY_SSL, "BINARY"),
    ("lib-dynload/_ssl.cpython-312-darwin.so", "/py/lib-dynload/_ssl.so", "EXTENSION"),
]


def sources_by_name(entries):
    return {Path(destination).name: source for destination, source, _ in entries}


def test_every_tls_library_ends_up_from_cryptography():
    aligned, substitutions = align_tls_libraries(MACOS_BINARIES)

    sources = sources_by_name(aligned)
    assert sources["libssl.3.dylib"] == CRYPTOGRAPHY_SSL
    assert sources["libcrypto.3.dylib"] == CRYPTOGRAPHY_CRYPTO
    # The mismatched pair is what broke the Intel build: one from each source.
    assert SYSTEM_SSL not in sources.values()
    assert [name for name, _, _ in substitutions] == ["libssl.3.dylib"]


def test_non_tls_binaries_are_untouched():
    aligned, _ = align_tls_libraries(MACOS_BINARIES)
    assert ("lib-dynload/_ssl.cpython-312-darwin.so", "/py/lib-dynload/_ssl.so", "EXTENSION") in aligned
    assert len(aligned) == len(MACOS_BINARIES)


def test_a_static_build_is_left_alone():
    """Linux: cryptography links OpenSSL in, so nothing should be rewritten."""
    linux = [
        ("libssl.so.3", "/usr/lib/x86_64-linux-gnu/libssl.so.3", "BINARY"),
        ("libcrypto.so.3", "/usr/lib/x86_64-linux-gnu/libcrypto.so.3", "BINARY"),
    ]
    aligned, substitutions = align_tls_libraries(linux)
    assert aligned == linux
    assert substitutions == []


def test_already_consistent_bundles_report_no_substitutions():
    consistent = [
        ("libssl.3.dylib", CRYPTOGRAPHY_SSL, "BINARY"),
        ("libcrypto.3.dylib", CRYPTOGRAPHY_CRYPTO, "BINARY"),
    ]
    aligned, substitutions = align_tls_libraries(consistent)
    assert aligned == consistent
    assert substitutions == []


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("libssl.3.dylib", True),
        ("libcrypto.so.3", True),
        ("cryptography/.dylibs/libssl.3.dylib", True),
        ("libcrypto-1234abcd.so.3", True),
        ("libpython3.12.dylib", False),
        ("_ssl.cpython-312-darwin.so", False),
    ],
)
def test_recognises_tls_libraries(destination, expected):
    assert is_tls_library(destination) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (CRYPTOGRAPHY_SSL, True),
        ("/py/site-packages/cryptography.libs/libssl-abc123.so.3", True),
        (SYSTEM_SSL, False),
        ("/py/site-packages/pypdfium2_raw/libpdfium.dylib", False),
    ],
)
def test_recognises_cryptography_s_own_libraries(source, expected):
    assert ships_with_cryptography(source) is expected
