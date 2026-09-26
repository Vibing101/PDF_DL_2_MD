"""Tests for the build-time OpenSSL alignment.

These matter because a Linux build never reaches the interesting branch —
cryptography links OpenSSL statically there — so only macOS hits the collision
this code exists to fix. The fixtures reproduce what the Intel runner actually
collected: libcrypto from Homebrew at 3.6.3, libssl from the Python framework
at 3.0.x, flattened into one bundle where they do not work together.
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
openssl_version = bundle_openssl.openssl_version


def write_library(path: Path, version: str | None) -> Path:
    """A stand-in library carrying the banner OpenSSL compiles into its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    banner = f"OpenSSL {version} 1 Jan 2026".encode() if version else b"no banner here"
    path.write_bytes(b"\x00\x01binary padding\x00" + banner + b"\x00more padding")
    return path


@pytest.fixture
def macos_layout(tmp_path):
    """The two directories the Intel runner drew its TLS libraries from."""
    homebrew = tmp_path / "homebrew" / "opt" / "openssl@3" / "lib"
    framework = tmp_path / "Python.framework" / "Versions" / "3.12" / "lib"
    write_library(homebrew / "libcrypto.3.dylib", "3.6.3")
    write_library(homebrew / "libssl.3.dylib", None)
    write_library(framework / "libcrypto.3.dylib", "3.0.13")
    write_library(framework / "libssl.3.dylib", None)
    return homebrew, framework


def sources_by_name(entries):
    return {Path(destination).name: source for destination, source, _ in entries}


def test_both_libraries_come_from_the_newest_libcrypto_s_directory(macos_layout):
    homebrew, framework = macos_layout
    # Exactly the mismatch that failed: libcrypto from one place, libssl the other.
    binaries = [
        ("libcrypto.3.dylib", str(homebrew / "libcrypto.3.dylib"), "BINARY"),
        ("libssl.3.dylib", str(framework / "libssl.3.dylib"), "BINARY"),
        ("lib-dynload/_ssl.so", "/py/lib-dynload/_ssl.so", "EXTENSION"),
    ]

    aligned, substitutions = align_tls_libraries(binaries)

    sources = sources_by_name(aligned)
    assert sources["libcrypto.3.dylib"] == str(homebrew / "libcrypto.3.dylib")
    assert sources["libssl.3.dylib"] == str(homebrew / "libssl.3.dylib")
    assert [name for name, _, _ in substitutions] == ["libssl.3.dylib"]


def test_the_older_directory_never_wins(macos_layout):
    homebrew, framework = macos_layout
    binaries = [
        ("libcrypto.3.dylib", str(framework / "libcrypto.3.dylib"), "BINARY"),
        ("libssl.3.dylib", str(homebrew / "libssl.3.dylib"), "BINARY"),
    ]
    aligned, _ = align_tls_libraries(binaries)
    assert all(str(homebrew) in source for _, source, _ in aligned)


def test_non_tls_binaries_are_untouched(macos_layout):
    homebrew, framework = macos_layout
    other = ("pypdfium2_raw/libpdfium.dylib", "/py/pypdfium2_raw/libpdfium.dylib", "BINARY")
    binaries = [
        ("libcrypto.3.dylib", str(homebrew / "libcrypto.3.dylib"), "BINARY"),
        ("libssl.3.dylib", str(framework / "libssl.3.dylib"), "BINARY"),
        other,
    ]
    aligned, _ = align_tls_libraries(binaries)
    assert other in aligned
    assert len(aligned) == len(binaries)


def test_one_source_directory_is_left_alone(macos_layout):
    homebrew, _ = macos_layout
    consistent = [
        ("libcrypto.3.dylib", str(homebrew / "libcrypto.3.dylib"), "BINARY"),
        ("libssl.3.dylib", str(homebrew / "libssl.3.dylib"), "BINARY"),
    ]
    aligned, substitutions = align_tls_libraries(consistent)
    assert aligned == consistent
    assert substitutions == []


def test_a_static_build_with_no_tls_libraries_is_left_alone():
    """Linux: cryptography links OpenSSL in, so there is nothing to align."""
    binaries = [("pdfminer/cmap.gz", "/py/pdfminer/cmap.gz", "DATA")]
    aligned, substitutions = align_tls_libraries(binaries)
    assert aligned == binaries
    assert substitutions == []


def test_nothing_is_moved_when_no_libcrypto_declares_a_version(tmp_path):
    """Without a version to compare, guessing a directory would be worse."""
    first = write_library(tmp_path / "a" / "libssl.3.dylib", None)
    second = write_library(tmp_path / "b" / "libcrypto.3.dylib", None)
    binaries = [
        ("libssl.3.dylib", str(first), "BINARY"),
        ("libcrypto.3.dylib", str(second), "BINARY"),
    ]
    aligned, substitutions = align_tls_libraries(binaries)
    assert aligned == binaries
    assert substitutions == []


def test_a_missing_sibling_is_not_invented(tmp_path):
    """If the chosen directory lacks the file, leave the entry as collected."""
    chosen = write_library(tmp_path / "new" / "libcrypto.3.dylib", "3.6.3")
    odd = write_library(tmp_path / "old" / "libssl.1.1.dylib", None)
    binaries = [
        ("libcrypto.3.dylib", str(chosen), "BINARY"),
        ("libssl.1.1.dylib", str(odd), "BINARY"),
    ]
    aligned, substitutions = align_tls_libraries(binaries)
    assert sources_by_name(aligned)["libssl.1.1.dylib"] == str(odd)
    assert substitutions == []


def test_openssl_version_reads_the_banner(tmp_path):
    assert openssl_version(write_library(tmp_path / "libcrypto.so", "3.6.3")) == (3, 6, 3)
    assert openssl_version(write_library(tmp_path / "plain.so", None)) == ()
    assert openssl_version(tmp_path / "missing.so") == ()


def test_openssl_version_takes_the_newest_when_several_appear(tmp_path):
    path = tmp_path / "many.so"
    path.write_bytes(b"OpenSSL 3.0.13\x00padding\x00OpenSSL 3.6.3\x00")
    assert openssl_version(path) == (3, 6, 3)


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
