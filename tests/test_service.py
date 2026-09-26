"""Tests for the JSON service the desktop app talks to.

The service is driven the way the app drives it: as a real child process, one
JSON object per line.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from queue import Empty, Queue

import pytest

from tests.conftest import make_pdf

pytestmark = pytest.mark.usefixtures("no_proxy")
TIMEOUT = 60


class ServiceClient:
    """Speaks the line-delimited JSON protocol to a child `pdf2md.service`."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "pdf2md.service", "--log-level", "ERROR"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self._lines: Queue[dict | None] = Queue()
        self.events: list[dict] = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if line:
                self._lines.put(json.loads(line))
        self._lines.put(None)

    def next_message(self, timeout: float = TIMEOUT) -> dict:
        try:
            message = self._lines.get(timeout=timeout)
        except Empty:
            raise AssertionError("the service sent nothing in time") from None
        if message is None:
            raise AssertionError("the service closed its output")
        return message

    def send(self, request_id: int, method: str, **params) -> None:
        assert self.process.stdin is not None
        request = {"id": request_id, "method": method}
        if params:
            request["params"] = params
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()

    def call(self, request_id: int, method: str, **params) -> dict:
        """Send a request and return its response, collecting events meanwhile."""
        self.send(request_id, method, **params)
        return self.wait(request_id)

    def wait(self, request_id: int, timeout: float = TIMEOUT) -> dict:
        """Read until the response to ``request_id`` arrives, keeping events."""
        while True:
            message = self.next_message(timeout)
            if message.get("id") == request_id:
                return message
            self.events.append(message)

    def wait_event(self, name: str, timeout: float = TIMEOUT) -> dict:
        """Read until an event of this kind arrives, and return its data."""
        while True:
            message = self.next_message(timeout)
            self.events.append(message)
            if message.get("event") == name:
                return message["data"]

    def send_raw(self, text: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def progress(self) -> list[dict]:
        return [event["data"] for event in self.events if event.get("event") == "progress"]

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send(9999, "shutdown")
            except (BrokenPipeError, OSError):
                pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.process.kill()


@pytest.fixture
def client():
    service = ServiceClient()
    assert service.next_message().get("event") == "ready"
    try:
        yield service
    finally:
        service.close()


def write_document(tmp_path: Path, body: str, name: str = "library.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_ping_reports_the_version_and_protocol(client):
    response = client.call(1, "ping")
    assert response["ok"] is True
    assert response["result"]["protocol"] == 1
    assert response["result"]["version"]


def test_parse_returns_rows_with_categories_and_target_paths(client, tmp_path):
    document = write_document(
        tmp_path,
        "# Library\n\n## Physics\n\n### Curriculum\n\n"
        "- Philosophy — [philosophy.pdf](https://e.org/philosophy.pdf)\n"
        "- Skip me — [notes.zip](https://e.org/notes.zip)\n",
    )
    result = client.call(2, "parse", path=str(document))["result"]

    assert result["count"] == 1
    assert result["document_name"] == "library.md"
    row = result["links"][0]
    assert row["id"] == 0
    assert row["title"] == "Philosophy"
    assert row["categories"] == ["Physics", "Curriculum"]
    assert row["relative_path"] == "Physics/Curriculum/philosophy.md"
    assert result["categories"] == [
        {
            "categories": ["Physics", "Curriculum"],
            "path": "Physics / Curriculum",
            "folder": "Physics/Curriculum",
            "count": 1,
        }
    ]


def test_parse_reports_a_missing_file(client, tmp_path):
    response = client.call(2, "parse", path=str(tmp_path / "nope.md"))
    assert response["ok"] is False
    assert response["error"]["kind"] == "not_found"


def test_parse_requires_a_path(client):
    assert client.call(2, "parse")["error"]["kind"] == "bad_request"


def test_convert_selected_rows_end_to_end(client, server, tmp_path):
    first = server.add("/a.pdf", make_pdf(["First document"]))
    second = server.add("/b.pdf", make_pdf(["Second document"]))
    document = write_document(
        tmp_path,
        f"## Physics\n\n- A — [a.pdf]({first})\n\n## Maths\n\n- B — [b.pdf]({second})\n",
    )
    parsed = client.call(2, "parse", path=str(document))["result"]
    assert parsed["count"] == 2
    chosen = next(row["id"] for row in parsed["links"] if row["categories"] == ["Maths"])

    out = tmp_path / "out"
    response = client.call(3, "convert", ids=[chosen], output_dir=str(out), workers=1)

    assert response["ok"] is True
    summary = response["result"]
    assert summary["files_written"] == 1
    assert summary["counts"] == {"converted": 1}
    assert (out / "Maths" / "b.md").is_file()
    assert not (out / "Physics").exists()
    assert "Second document" in (out / "Maths" / "b.md").read_text(encoding="utf-8")
    assert server.hits == ["/b.pdf"]

    steps = client.progress()
    assert steps and steps[-1]["done"] == steps[-1]["total"] == 1
    assert steps[-1]["status"] == "converted"


def test_convert_all_rows_when_no_ids_are_given(client, server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Everything"]))
    document = write_document(tmp_path, f"## Physics\n\n- A — [a.pdf]({url})\n")
    client.call(2, "parse", path=str(document))
    summary = client.call(3, "convert", output_dir=str(tmp_path / "out"))["result"]
    assert summary["files_written"] == 1


def test_convert_reports_failures_without_stopping(client, server, tmp_path):
    good = server.add("/good.pdf", make_pdf(["Good"]))
    bad = server.base_url + "/missing.pdf"
    document = write_document(
        tmp_path, f"## Physics\n\n- Good — [g.pdf]({good})\n- Bad — [b.pdf]({bad})\n"
    )
    client.call(2, "parse", path=str(document))
    summary = client.call(3, "convert", output_dir=str(tmp_path / "out"), retries=0)["result"]

    assert summary["files_written"] == 1
    assert len(summary["failures"]) == 1
    assert summary["failures"][0]["status"] == "download_failed"


def test_convert_needs_a_parse_first(client, tmp_path):
    response = client.call(2, "convert", output_dir=str(tmp_path / "out"))
    assert response["error"]["kind"] == "no_selection"


def test_convert_rejects_unknown_ids(client, server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["A"]))
    document = write_document(tmp_path, f"## Physics\n\n- A — [a.pdf]({url})\n")
    client.call(2, "parse", path=str(document))
    response = client.call(3, "convert", ids=[7], output_dir=str(tmp_path / "out"))
    assert response["error"]["kind"] == "bad_request"


def test_convert_needs_an_output_directory(client, server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["A"]))
    document = write_document(tmp_path, f"## Physics\n\n- A — [a.pdf]({url})\n")
    client.call(2, "parse", path=str(document))
    assert client.call(3, "convert", ids=[0])["error"]["kind"] == "bad_request"


def test_cancel_stops_the_remaining_downloads(client, server, tmp_path):
    body = make_pdf(["Cancelled run"])
    links = []
    for index in range(12):
        links.append(f"- Doc {index} — [d{index}.pdf]({server.add(f'/d{index}.pdf', body)})\n")
    document = write_document(tmp_path, "## Physics\n\n" + "".join(links))
    client.call(2, "parse", path=str(document))

    client.send(3, "convert", output_dir=str(tmp_path / "out"), workers=1, delay=0.4)
    client.wait_event("progress")  # the run is under way
    assert client.call(4, "cancel")["result"] == {"cancelling": True}

    summary = client.wait(3)["result"]  # the deferred response of the cancelled run
    assert summary["counts"].get("cancelled", 0) > 0
    assert summary["files_written"] < 12


def test_a_second_conversion_is_refused_while_one_runs(client, server, tmp_path):
    body = make_pdf(["Busy"])
    links = [f"- D{i} — [d{i}.pdf]({server.add(f'/d{i}.pdf', body)})\n" for i in range(6)]
    document = write_document(tmp_path, "## Physics\n\n" + "".join(links))
    client.call(2, "parse", path=str(document))

    client.send(3, "convert", output_dir=str(tmp_path / "out"), workers=1, delay=0.3)
    response = client.call(4, "convert", output_dir=str(tmp_path / "out2"))
    assert response["error"]["kind"] == "busy"

    client.call(5, "cancel")
    client.wait(3)  # drain the first run's response


def test_unknown_method_and_malformed_json_are_reported(client):
    assert client.call(2, "frobnicate")["error"]["kind"] == "unknown_method"
    client.send_raw("{not json")
    message = client.next_message()
    assert message["ok"] is False
    assert message["error"]["kind"] == "bad_request"


def test_shutdown_ends_the_process(client):
    assert client.call(2, "shutdown")["result"] == {"bye": True}
    assert client.process.wait(timeout=10) == 0
