"""A line-delimited JSON service, so a GUI can drive the pipeline.

The desktop app spawns this module as a long-lived child process and talks to it
over stdin/stdout. One JSON object per line, in both directions.

Requests (stdin)::

    {"id": 1, "method": "ping"}
    {"id": 2, "method": "parse",   "params": {"path": "/…/library.md"}}
    {"id": 3, "method": "convert", "params": {"ids": [0, 1], "output_dir": "/…"}}
    {"id": 4, "method": "cancel"}
    {"id": 5, "method": "shutdown"}

Replies (stdout) are either a response to a request::

    {"id": 2, "ok": true,  "result": {…}}
    {"id": 2, "ok": false, "error": {"kind": "…", "message": "…"}}

or an unsolicited event::

    {"event": "progress", "data": {"done": 3, "total": 10, "status": "converted", …}}
    {"event": "log",      "data": {"level": "warning", "message": "…"}}

Conversion runs on its own thread, so ``cancel`` is still answered while it is
in progress. Logging goes to stderr as usual; warnings and errors are also
forwarded as ``log`` events so the app can show them.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from pdf2md import __version__
from pdf2md.convert import Converter
from pdf2md.download import Downloader
from pdf2md.extract import PdfLink
from pdf2md.naming import category_parts
from pdf2md.pipeline import Options, Pipeline, RunReport

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1


class ServiceError(Exception):
    """An error to report back to the caller, with a machine-readable kind."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class _Row:
    """One selectable row in the app: a link plus where it would be written."""

    link: PdfLink
    relative_path: str

    def as_dict(self, index: int) -> dict:
        return {
            "id": index,
            "url": self.link.url,
            "title": self.link.title,
            "categories": list(self.link.categories),
            "category_path": self.link.category_path,
            "document_type": self.link.column,
            "line": self.link.line,
            "source": self.link.source,
            "relative_path": self.relative_path,
        }


class Service:
    """Reads requests from a stream, writes responses and events to another."""

    def __init__(self, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._rows: list[_Row] = []
        self._document: Path | None = None
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._stop = False
        self._converter: Converter | None = None
        #: Network settings the app may override per conversion.
        self.network: dict[str, float | int] = {
            "connect_timeout": 15.0,
            "read_timeout": 90.0,
            "retries": 3,
        }

    # ----------------------------------------------------------------- plumbing

    def _send(self, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False)
        with self._write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def emit(self, event: str, data: dict) -> None:
        """Send an unsolicited event, such as progress."""
        self._send({"event": event, "data": data})

    def serve(self) -> int:
        """Read requests until stdin closes or ``shutdown`` arrives."""
        self.emit("ready", {"version": __version__, "protocol": PROTOCOL_VERSION})
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            self._handle_line(line)
            if self._stop:
                break
        return 0

    def _handle_line(self, line: str) -> None:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            self._send({"id": None, "ok": False, "error": _error("bad_request", str(error))})
            return
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        try:
            result = self._dispatch(str(method), params, request_id)
        except ServiceError as error:
            self._send({"id": request_id, "ok": False, "error": _error(error.kind, str(error))})
        except Exception as error:  # a crash must not take the app down
            logger.exception("request %s failed", method)
            self._send(
                {"id": request_id, "ok": False, "error": _error("internal", str(error))}
            )
        else:
            if result is not _DEFERRED:
                self._send({"id": request_id, "ok": True, "result": result})

    def _dispatch(self, method: str, params: dict, request_id: Any) -> Any:
        if method == "ping":
            return {"version": __version__, "protocol": PROTOCOL_VERSION}
        if method == "parse":
            return self.parse(params)
        if method == "convert":
            return self.convert(params, request_id)
        if method == "cancel":
            self._cancel.set()
            return {"cancelling": True}
        if method == "shutdown":
            self._cancel.set()
            self._stop = True
            return {"bye": True}
        raise ServiceError(f"unknown method: {method}", kind="unknown_method")

    # ------------------------------------------------------------------ methods

    def parse(self, params: dict) -> dict:
        """Extract the PDF links from a document and remember them."""
        raw_path = params.get("path")
        if not raw_path:
            raise ServiceError("no document path given", kind="bad_request")
        document = Path(raw_path).expanduser()
        if not document.is_file():
            raise ServiceError(f"no such file: {document}", kind="not_found")

        options = Options(
            output_dir=Path(""),
            max_category_depth=int(params.get("max_category_depth", 3)),
            include_top_heading=bool(params.get("include_top_heading", False)),
            name_from=str(params.get("name_from", "url")),
            probe=bool(params.get("probe", False)),
        )
        pipeline = self._pipeline(options)
        try:
            found, selected = pipeline.collect([document])
        except UnicodeDecodeError as error:
            raise ServiceError(f"cannot read {document.name}: {error}", kind="unreadable") from error

        # plan() also resolves filename clashes, so the paths shown are the real ones.
        self._rows = [
            _Row(target.link, target.path.as_posix())
            for targets in pipeline.plan(selected).values()
            for target in targets
        ]
        self._rows.sort(key=lambda row: (row.link.categories, row.link.line))
        self._document = document
        return {
            "document": str(document),
            "document_name": document.name,
            "links_found": len(found),
            "count": len(self._rows),
            "categories": self._category_tree(),
            "links": [row.as_dict(index) for index, row in enumerate(self._rows)],
        }

    def convert(self, params: dict, request_id: Any) -> Any:
        """Start converting the selected rows; reply when the run finishes."""
        if not self._rows:
            raise ServiceError("nothing parsed yet", kind="no_selection")
        ids = params.get("ids")
        if ids is None:
            selection = list(range(len(self._rows)))
        else:
            selection = [int(value) for value in ids]
        unknown = [value for value in selection if not 0 <= value < len(self._rows)]
        if unknown:
            raise ServiceError(f"unknown link id(s): {unknown}", kind="bad_request")
        if not selection:
            raise ServiceError("no links selected", kind="no_selection")

        output_dir = params.get("output_dir")
        if not output_dir:
            raise ServiceError("no output directory given", kind="bad_request")

        for key in self.network:
            if key in params:
                self.network[key] = params[key]

        keep_pdfs = params.get("keep_pdfs")
        options = Options(
            output_dir=Path(str(output_dir)).expanduser(),
            keep_pdf_dir=Path(str(keep_pdfs)).expanduser() if keep_pdfs else None,
            workers=max(1, int(params.get("workers", 4))),
            delay=float(params.get("delay", 0.0)),
            max_category_depth=int(params.get("max_category_depth", 3)),
            name_from=str(params.get("name_from", "url")),
            overwrite=bool(params.get("overwrite", False)),
            write_index=bool(params.get("write_index", True)),
        )
        links = [self._rows[index].link for index in selection]

        if not self._busy.acquire(blocking=False):
            raise ServiceError("a conversion is already running", kind="busy")
        self._cancel.clear()
        thread = threading.Thread(
            target=self._run_conversion,
            args=(options, links, request_id),
            name="pdf2md-convert",
            daemon=True,
        )
        thread.start()
        return _DEFERRED

    def _run_conversion(self, options: Options, links: list[PdfLink], request_id: Any) -> None:
        try:
            pipeline = self._pipeline(
                options,
                on_progress=lambda progress: self.emit("progress", progress.as_dict()),
                cancel_event=self._cancel,
            )
            documents = [self._document] if self._document else []
            report = pipeline.run_links(links, documents=documents)
        except Exception as error:
            logger.exception("conversion failed")
            self._send(
                {"id": request_id, "ok": False, "error": _error("convert_failed", str(error))}
            )
        else:
            self._send({"id": request_id, "ok": True, "result": _report_summary(report, options)})
        finally:
            self._busy.release()

    # ------------------------------------------------------------------ helpers

    def _pipeline(self, options: Options, **kwargs) -> Pipeline:
        downloader = Downloader(
            delay=options.delay,
            connect_timeout=float(self.network["connect_timeout"]),
            read_timeout=float(self.network["read_timeout"]),
            retries=int(self.network["retries"]),
        )
        return Pipeline(options, downloader=downloader, converter=self.converter, **kwargs)

    @property
    def converter(self) -> Converter:
        """One converter for the whole process, so markitdown starts up once."""
        if self._converter is None:
            self._converter = Converter()
        return self._converter

    def _category_tree(self) -> list[dict]:
        """Counts per category path, for the app's grouping."""
        counts: dict[tuple[str, ...], int] = {}
        for row in self._rows:
            counts[row.link.categories] = counts.get(row.link.categories, 0) + 1
        return [
            {
                "categories": list(categories),
                "path": " / ".join(categories),
                "folder": "/".join(category_parts(categories)),
                "count": count,
            }
            for categories, count in sorted(counts.items())
        ]


class _Deferred:
    """Marker: the response will be sent later, from a worker thread."""


_DEFERRED = _Deferred()


class EventLogHandler(logging.Handler):
    """Forwards warnings and errors to the app as ``log`` events."""

    def __init__(self, service: Service, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self.service = service

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.service.emit(
                "log", {"level": record.levelname.lower(), "message": record.getMessage()}
            )
        except Exception:  # never let logging break the protocol
            self.handleError(record)


def _error(kind: str, message: str) -> dict:
    return {"kind": kind, "message": message}


def _report_summary(report: RunReport, options: Options) -> dict:
    return {
        "output_dir": str(options.output_dir),
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "links_selected": report.links_selected,
        "files_written": report.files_written,
        "counts": report.counts(),
        "items": [result.as_dict() for result in report.results],
        "failures": [result.as_dict() for result in report.failures],
    }


def main(argv: list[str] | None = None) -> int:
    """Run the service on stdin/stdout."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="pdf2md-service",
        description="Line-delimited JSON service used by the pdf2md desktop app.",
    )
    parser.add_argument("--version", action="version", version=f"pdf2md-service {__version__}")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="convert a generated PDF to check this build can read PDFs at all, then exit",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("PDF2MD_LOG_LEVEL", "INFO").upper(),
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="stderr log level; also settable with PDF2MD_LOG_LEVEL (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    if args.selftest:
        from pdf2md.selftest import SelfTestError, run

        try:
            run()
        except SelfTestError as error:
            print(f"pdf2md selftest FAILED: {error}", file=sys.stderr)
            return 1
        print(f"pdf2md selftest passed: {__version__} converts PDFs")
        return 0

    service = Service()
    logging.getLogger("pdf2md").addHandler(EventLogHandler(service))
    try:
        return service.serve()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130


if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
