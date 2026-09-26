"""Shared test helpers: a minimal PDF writer and a local HTTP server."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest


def make_pdf(lines: list[str]) -> bytes:
    """Build a tiny but valid one-page PDF containing ``lines`` of text.

    The built-in Helvetica font is single-byte, so text outside Latin-1 is
    replaced rather than embedded — keep test text ASCII.
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


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves the routes registered on the server instance."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep the test output clean
        pass

    def _route(self):
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        return self.server.routes.get(self.path)  # type: ignore[attr-defined]

    def do_HEAD(self) -> None:
        route = self._route()
        if route is None:
            self.send_error(404, "not found")
            return
        status, content_type, body = route
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def do_GET(self) -> None:
        route = self._route()
        if route is None:
            self.send_error(404, "not found")
            return
        status, content_type, body = route
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalServer:
    """A throwaway HTTP server whose routes the test controls."""

    def __init__(self) -> None:
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.routes = {}  # type: ignore[attr-defined]
        self._httpd.hits = []  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def hits(self) -> list[str]:
        return self._httpd.hits  # type: ignore[attr-defined]

    def add(
        self,
        path: str,
        body: bytes,
        *,
        content_type: str = "application/pdf",
        status: int = 200,
    ) -> str:
        self._httpd.routes[path] = (status, content_type, body)  # type: ignore[attr-defined]
        return self.base_url + path

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def server():
    """A local HTTP server; no traffic leaves the machine."""
    instance = LocalServer()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def no_proxy(monkeypatch):
    """Stop requests from sending localhost traffic through an HTTP proxy."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


@pytest.fixture
def pdf_bytes() -> bytes:
    return make_pdf(["Curriculum philosophy", "Second line of the document"])


@pytest.fixture
def samples_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "samples"
