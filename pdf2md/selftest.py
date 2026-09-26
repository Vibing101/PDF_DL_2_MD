"""Prove that this build can actually convert a PDF.

markitdown reports a missing PDF backend as a plain ``MissingDependencyException``
at conversion time, so a bundle can be built, start up and report its version
while being unable to convert anything. This module converts a PDF built in
memory, which turns that silent failure into a build failure.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

#: The PDF-reading packages markitdown imports before it will touch a PDF.
#: markitdown catches ImportError from any of them and reports the whole [pdf]
#: extra as missing, so each one is checked separately to name the real culprit.
PDF_DEPENDENCIES = ("pdfminer", "pdfminer.high_level", "pdfplumber")


class SelfTestError(RuntimeError):
    """Raised when the build cannot convert a PDF."""


def make_pdf(lines: list[str]) -> bytes:
    """Build a tiny but valid one-page PDF containing ``lines`` of text.

    The built-in Helvetica font is single-byte, so text outside Latin-1 is
    replaced rather than embedded — keep the text ASCII.
    """
    content = "BT /F1 18 Tf 72 720 Td 20 TL\n" + "".join(f"({line}) Tj T*\n" for line in lines)
    content += "ET\n"
    stream = content.encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start_xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start_xref,
    )
    return bytes(out)


def missing_dependencies() -> list[str]:
    """Return the PDF-reading modules that cannot be imported here."""
    import importlib

    missing = []
    for name in PDF_DEPENDENCIES:
        try:
            importlib.import_module(name)
        except Exception as error:  # ImportError, but a broken binary can raise anything
            missing.append(f"{name} ({error.__class__.__name__}: {error})")
    return missing


def _openssl_version(path: Path) -> str:
    """Read the version string OpenSSL compiles into its own library."""
    try:
        blob = path.read_bytes()
    except OSError as error:
        return f"unreadable ({error})"
    marker = b"OpenSSL "
    start = blob.find(marker)
    if start < 0:
        return "no version string found"
    end = blob.find(b"\x00", start)
    return blob[start : end if 0 < end < start + 120 else start + 60].decode("ascii", "replace")


def diagnostics() -> list[str]:
    """Describe the TLS libraries inside a frozen bundle.

    `cryptography`'s compiled extension links against a specific OpenSSL. When a
    different one ends up beside it in the bundle, the extension fails to load
    with a missing symbol and takes the whole PDF backend down with it, so this
    reports which libraries are actually there and where the extension looks.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle is None:
        return ["not running from a frozen bundle, so there is nothing to inspect"]

    root = Path(bundle)
    lines = [f"bundle: {root}"]

    libraries = sorted(
        {path for pattern in ("**/libssl*", "**/libcrypto*") for path in root.glob(pattern)}
    )
    if libraries:
        for path in libraries:
            size = path.stat().st_size if path.exists() else 0
            lines.append(
                f"  {path.relative_to(root)}  {size:,} bytes  [{_openssl_version(path)}]"
            )
    else:
        lines.append("  no libssl/libcrypto in the bundle")

    extensions = sorted(root.glob("cryptography/hazmat/bindings/_rust*"))
    for extension in extensions:
        lines.append(f"  {extension.relative_to(root)} links against:")
        try:
            output = subprocess.run(
                ["otool", "-L", str(extension)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError) as error:
            lines.append(f"    (otool unavailable: {error})")
            continue
        for line in output.splitlines()[1:]:
            if "ssl" in line or "crypto" in line:
                lines.append(f"    {line.strip()}")
    if not extensions:
        lines.append("  no cryptography extension in the bundle")
    return lines


def run() -> str:
    """Convert a generated PDF and return its text, or raise SelfTestError."""
    from pdf2md.convert import ConversionError, Converter

    marker = "pdf2md selftest ok"
    missing = missing_dependencies()
    if missing:
        raise SelfTestError(
            "this build cannot read PDFs — markitdown's PDF backend is incomplete.\n"
            "Missing or broken: "
            + "; ".join(missing)
            + "\n\nWhat the bundle actually contains:\n"
            + "\n".join(diagnostics())
        )

    with tempfile.TemporaryDirectory(prefix="pdf2md-selftest-") as directory:
        path = Path(directory) / "selftest.pdf"
        path.write_bytes(make_pdf([marker, "second line"]))
        try:
            markdown = Converter().convert(path)
        except ConversionError as error:
            raise SelfTestError(f"converting a PDF failed: {error}") from error

    if marker not in markdown:
        raise SelfTestError(
            "converting a PDF produced no usable text "
            f"(expected {marker!r}, got {markdown[:200]!r})"
        )
    return markdown
