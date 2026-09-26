"""Shared test helpers: a minimal PDF writer and a local HTTP server."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from pdf2md.selftest import make_pdf

__all__ = ["make_pdf"]


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
