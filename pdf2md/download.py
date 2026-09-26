"""Download a PDF to a temporary file and check that it really is a PDF."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pdf2md.naming import url_digest

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "pdf2md/0.1 (+https://github.com/microsoft/markitdown)"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 90.0
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
#: How far into the file we look for the ``%PDF-`` signature; a few servers
#: prepend whitespace or a BOM.
SIGNATURE_WINDOW = 1024


class DownloadError(RuntimeError):
    """Raised when a URL cannot be fetched, or is not a PDF."""


@dataclass
class Download:
    """A PDF that now lives in a temporary file."""

    url: str
    final_url: str
    path: Path
    size: int
    sha256: str
    content_type: str | None


class Downloader:
    """Fetches PDFs over HTTP with retries, size limits and rate limiting."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
        retries: int = 3,
        delay: float = 0.0,
        user_agent: str = DEFAULT_USER_AGENT,
        verify: bool | str = True,
    ) -> None:
        self.timeout = (connect_timeout, read_timeout)
        self.max_bytes = max_bytes
        self.delay = delay
        self.verify = verify
        self.session = session or self._build_session(retries, user_agent)
        self._throttle = threading.Lock()
        self._last_request = 0.0

    @staticmethod
    def _build_session(retries: int, user_agent: str) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": user_agent, "Accept": "application/pdf,*/*"})
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=1.0,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _wait_turn(self) -> None:
        """Keep at least ``delay`` seconds between requests, across threads."""
        if self.delay <= 0:
            return
        with self._throttle:
            gap = time.monotonic() - self._last_request
            if gap < self.delay:
                time.sleep(self.delay - gap)
            self._last_request = time.monotonic()

    def looks_like_pdf_url(self, url: str) -> bool:
        """Ask the server whether a URL without a ``.pdf`` suffix is a PDF."""
        self._wait_turn()
        try:
            response = self.session.head(
                url, timeout=self.timeout, allow_redirects=True, verify=self.verify
            )
        except requests.RequestException as error:
            logger.debug("HEAD %s failed: %s", url, error)
            return False
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "application/pdf" in content_type or "application/x-pdf" in content_type:
            return True
        disposition = (response.headers.get("Content-Disposition") or "").lower()
        return ".pdf" in disposition

    def fetch(self, url: str, destination_dir: Path) -> Download:
        """Download ``url`` into ``destination_dir`` and return its metadata.

        Raises:
            DownloadError: on a network error, an HTTP error status, a response
                larger than ``max_bytes``, or a body that is not a PDF.
        """
        destination_dir.mkdir(parents=True, exist_ok=True)
        path = destination_dir / f"{url_digest(url, 16)}.pdf"
        self._wait_turn()
        try:
            with self.session.get(
                url, stream=True, timeout=self.timeout, allow_redirects=True, verify=self.verify
            ) as response:
                if response.status_code >= 400:
                    raise DownloadError(f"HTTP {response.status_code} {response.reason}")
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > self.max_bytes:
                    raise DownloadError(
                        f"file is {int(declared)} bytes, over the {self.max_bytes} byte limit"
                    )
                digest = hashlib.sha256()
                size = 0
                head = b""
                with path.open("wb") as handle:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise DownloadError(
                                f"download exceeded the {self.max_bytes} byte limit"
                            )
                        if len(head) < SIGNATURE_WINDOW:
                            head += chunk[: SIGNATURE_WINDOW - len(head)]
                        digest.update(chunk)
                        handle.write(chunk)
                content_type = response.headers.get("Content-Type")
                final_url = response.url
        except DownloadError:
            path.unlink(missing_ok=True)
            raise
        except requests.RequestException as error:
            path.unlink(missing_ok=True)
            raise DownloadError(str(error)) from error

        if size == 0:
            path.unlink(missing_ok=True)
            raise DownloadError("empty response body")
        if b"%PDF-" not in head:
            preview = head[:60].decode("utf-8", "replace").replace("\n", " ")
            path.unlink(missing_ok=True)
            raise DownloadError(
                f"not a PDF (Content-Type: {content_type or 'unknown'}; starts with {preview!r})"
            )
        logger.debug("downloaded %s (%d bytes)", url, size)
        return Download(
            url=url,
            final_url=final_url,
            path=path,
            size=size,
            sha256=digest.hexdigest(),
            content_type=content_type,
        )
