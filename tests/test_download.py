"""Tests for the downloader, against a local HTTP server."""

from __future__ import annotations

import pytest

from pdf2md.download import Downloader, DownloadError

pytestmark = pytest.mark.usefixtures("no_proxy")


def test_fetch_writes_a_temp_file_with_metadata(server, pdf_bytes, tmp_path):
    url = server.add("/doc.pdf", pdf_bytes)
    download = Downloader(retries=0).fetch(url, tmp_path / "tmp")
    assert download.path.read_bytes() == pdf_bytes
    assert download.size == len(pdf_bytes)
    assert len(download.sha256) == 64
    assert download.content_type == "application/pdf"
    assert download.final_url == url


def test_http_error_is_reported(server, tmp_path):
    url = server.base_url + "/missing.pdf"
    with pytest.raises(DownloadError, match="HTTP 404"):
        Downloader(retries=0).fetch(url, tmp_path)
    assert not list(tmp_path.glob("*.pdf"))


def test_html_error_page_is_rejected(server, tmp_path):
    url = server.add("/trap.pdf", b"<html>Sorry, not here</html>", content_type="text/html")
    with pytest.raises(DownloadError, match="not a PDF"):
        Downloader(retries=0).fetch(url, tmp_path)
    assert not list(tmp_path.glob("*.pdf"))


def test_empty_body_is_rejected(server, tmp_path):
    url = server.add("/empty.pdf", b"")
    with pytest.raises(DownloadError, match="empty response"):
        Downloader(retries=0).fetch(url, tmp_path)


def test_oversized_file_is_refused_before_reading_it(server, pdf_bytes, tmp_path):
    url = server.add("/big.pdf", pdf_bytes)
    with pytest.raises(DownloadError, match="over the 10 byte limit"):
        Downloader(retries=0, max_bytes=10).fetch(url, tmp_path)


def test_leading_junk_before_the_signature_is_tolerated(server, pdf_bytes, tmp_path):
    url = server.add("/bom.pdf", b"\n\n" + pdf_bytes)
    assert Downloader(retries=0).fetch(url, tmp_path).size == len(pdf_bytes) + 2


def test_probe_uses_head_and_reads_the_content_type(server, pdf_bytes):
    downloader = Downloader(retries=0)
    pdf_url = server.add("/hidden/1", pdf_bytes)
    html_url = server.add("/hidden/2", b"<html></html>", content_type="text/html")
    assert downloader.looks_like_pdf_url(pdf_url)
    assert not downloader.looks_like_pdf_url(html_url)
    assert not downloader.looks_like_pdf_url(server.base_url + "/hidden/3")


def test_delay_is_enforced_between_requests(server, pdf_bytes, tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("pdf2md.download.time.sleep", sleeps.append)
    url = server.add("/a.pdf", pdf_bytes)
    downloader = Downloader(retries=0, delay=5.0)
    downloader.fetch(url, tmp_path)
    downloader.fetch(url, tmp_path)
    assert sleeps and sleeps[-1] > 4.0
