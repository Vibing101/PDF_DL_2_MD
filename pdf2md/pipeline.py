"""Run the whole job: extract links, download PDFs, convert, write the tree."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pdf2md import __version__
from pdf2md.convert import ConversionError, Converter, convert_document_to_markdown
from pdf2md.download import Download, Downloader, DownloadError
from pdf2md.extract import PdfLink, extract_links, is_ambiguous
from pdf2md.naming import category_parts, stem_from_title, stem_from_url, url_digest

logger = logging.getLogger(__name__)

#: Input files we read as text; anything else is handed to markitdown first.
TEXT_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".txt", ".text", ".html", ".htm", ".rst"})
MANIFEST_NAME = "_manifest.json"
INDEX_NAME = "INDEX.md"

STATUS_CONVERTED = "converted"
STATUS_EMPTY = "converted_empty"
STATUS_SKIPPED = "skipped_existing"
STATUS_PLANNED = "planned"
STATUS_DOWNLOAD_FAILED = "download_failed"
STATUS_CONVERT_FAILED = "convert_failed"
STATUS_WRITE_FAILED = "write_failed"
STATUS_CANCELLED = "cancelled"
FAILURE_STATUSES = frozenset({STATUS_DOWNLOAD_FAILED, STATUS_CONVERT_FAILED, STATUS_WRITE_FAILED})


@dataclass
class Target:
    """One output file to write for a link."""

    link: PdfLink
    path: Path


@dataclass
class Progress:
    """One step of a run, handed to a caller's progress callback."""

    done: int
    total: int
    status: str
    url: str
    title: str
    path: str
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "done": self.done,
            "total": self.total,
            "status": self.status,
            "url": self.url,
            "title": self.title,
            "path": self.path,
            "error": self.error,
        }


@dataclass
class OutputRecord:
    """One Markdown file written for a URL, with the title it was listed under."""

    path: str
    title: str
    categories: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"path": self.path, "title": self.title, "categories": list(self.categories)}


@dataclass
class ItemResult:
    """What happened to one unique URL."""

    url: str
    status: str
    outputs: list[OutputRecord] = field(default_factory=list)
    title: str = ""
    categories: tuple[str, ...] = ()
    size: int | None = None
    sha256: str | None = None
    final_url: str | None = None
    error: str | None = None
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = {
            "url": self.url,
            "status": self.status,
            "title": self.title,
            "categories": list(self.categories),
            "outputs": [output.as_dict() for output in self.outputs],
            "sources": self.sources,
        }
        if self.size is not None:
            data["bytes"] = self.size
        if self.sha256:
            data["sha256"] = self.sha256
        if self.final_url and self.final_url != self.url:
            data["final_url"] = self.final_url
        if self.error:
            data["error"] = self.error
        return data


@dataclass
class Options:
    """Everything the pipeline can be told to do differently."""

    output_dir: Path = Path("output")
    keep_pdf_dir: Path | None = None
    workers: int = 4
    delay: float = 0.0
    max_category_depth: int = 3
    include_top_heading: bool = False
    name_from: str = "url"  # "url" or "title"
    overwrite: bool = False
    dry_run: bool = False
    limit: int | None = None
    probe: bool = False
    include_pattern: str | None = None
    exclude_pattern: str | None = None
    category_pattern: str | None = None
    write_index: bool = True
    manifest_path: Path | None = None
    flat: bool = False


@dataclass
class RunReport:
    """Outcome of a whole run."""

    options: Options
    documents: list[str] = field(default_factory=list)
    links_found: int = 0
    links_selected: int = 0
    results: list[ItemResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def failures(self) -> list[ItemResult]:
        return [result for result in self.results if result.status in FAILURE_STATUSES]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    @property
    def files_written(self) -> int:
        return sum(
            len(result.outputs)
            for result in self.results
            if result.status in {STATUS_CONVERTED, STATUS_EMPTY}
        )


def read_document(path: Path) -> str:
    """Read an input document as text, converting non-text formats first."""
    if path.suffix.lower() in TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace")
    logger.info("converting %s to Markdown to read its links", path.name)
    return convert_document_to_markdown(path)


class Pipeline:
    """Extract, download and convert, writing one Markdown file per PDF."""

    def __init__(
        self,
        options: Options,
        *,
        downloader: Downloader | None = None,
        converter: Converter | None = None,
        on_progress: Callable[[Progress], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.options = options
        self.downloader = downloader or Downloader(delay=options.delay)
        self.converter = converter or Converter()
        self.on_progress = on_progress
        self.cancel_event = cancel_event or threading.Event()
        self._counter_lock = threading.Lock()
        self._done = 0

    # ------------------------------------------------------------------ plan

    def collect(self, documents: list[Path]) -> tuple[list[PdfLink], list[PdfLink]]:
        """Return ``(all_links, selected_links)`` for the given documents."""
        found: list[PdfLink] = []
        for document in documents:
            found.extend(
                extract_links(
                    read_document(document),
                    source=document.name,
                    max_category_depth=self.options.max_category_depth,
                    include_top_heading=self.options.include_top_heading,
                    include_ambiguous=self.options.probe,
                )
            )
        selected = self._filter(found)
        if self.options.probe:
            selected = self._drop_non_pdfs(selected)
        if self.options.limit is not None:
            selected = self._apply_limit(selected, self.options.limit)
        return found, selected

    def _filter(self, links: list[PdfLink]) -> list[PdfLink]:
        include = re.compile(self.options.include_pattern) if self.options.include_pattern else None
        exclude = re.compile(self.options.exclude_pattern) if self.options.exclude_pattern else None
        category = (
            re.compile(self.options.category_pattern) if self.options.category_pattern else None
        )
        kept = []
        for link in links:
            if include and not include.search(link.url):
                continue
            if exclude and exclude.search(link.url):
                continue
            if category and not category.search(link.category_path):
                continue
            kept.append(link)
        return kept

    def _drop_non_pdfs(self, links: list[PdfLink]) -> list[PdfLink]:
        """HEAD every extension-less URL and keep only the ones serving PDFs."""
        unknown = OrderedDict(
            (link.url, None) for link in links if is_ambiguous(link.url)
        )
        if not unknown:
            return links
        logger.info("probing %d link(s) without a .pdf extension", len(unknown))
        verdicts: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.options.workers)) as pool:
            futures = {
                pool.submit(self.downloader.looks_like_pdf_url, url): url for url in unknown
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    verdicts[url] = bool(future.result())
                except Exception as error:  # pragma: no cover - network dependent
                    logger.debug("probe of %s failed: %s", url, error)
                    verdicts[url] = False
        return [link for link in links if verdicts.get(link.url, True)]

    def _apply_limit(self, links: list[PdfLink], limit: int) -> list[PdfLink]:
        """Keep links for the first ``limit`` unique URLs."""
        urls: list[str] = []
        seen: set[str] = set()
        for link in links:
            if link.url not in seen:
                seen.add(link.url)
                urls.append(link.url)
            if len(urls) >= limit:
                break
        allowed = set(urls)
        return [link for link in links if link.url in allowed]

    def plan(self, links: list[PdfLink]) -> OrderedDict[str, list[Target]]:
        """Group links by URL and decide each output path.

        A URL listed under several categories is downloaded and converted once,
        then written into each of those categories.
        """
        groups: OrderedDict[str, list[Target]] = OrderedDict()
        used: dict[Path, str] = {}
        for link in links:
            directory = self.options.output_dir
            if not self.options.flat:
                for part in category_parts(link.categories):
                    directory = directory / part
            stem = (
                stem_from_title(link.title, link.url)
                if self.options.name_from == "title"
                else stem_from_url(link.url)
            )
            path = directory / f"{stem}.md"
            owner = used.get(path)
            if owner is not None and owner != link.url:
                path = directory / f"{stem}-{url_digest(link.url)}.md"
            used.setdefault(path, link.url)
            targets = groups.setdefault(link.url, [])
            if all(existing.path != path for existing in targets):
                targets.append(Target(link, path))
        return groups

    # ------------------------------------------------------------------- run

    def run(self, documents: list[Path]) -> RunReport:
        """Extract links from every document, then process them."""
        found, selected = self.collect(documents)
        return self._execute(selected, documents=documents, links_found=len(found))

    def run_links(
        self, links: list[PdfLink], *, documents: list[Path] | None = None
    ) -> RunReport:
        """Process an explicit list of links — what the desktop app selected."""
        return self._execute(links, documents=documents or [], links_found=len(links))

    def _execute(
        self, selected: list[PdfLink], *, documents: list[Path], links_found: int
    ) -> RunReport:
        report = RunReport(
            options=self.options,
            documents=[str(document) for document in documents],
            started_at=_now(),
        )
        report.links_found = links_found
        report.links_selected = len(selected)
        groups = self.plan(selected)
        logger.info(
            "%d PDF link(s) found, %d selected, %d unique file(s) to fetch",
            links_found,
            len(selected),
            len(groups),
        )

        if self.options.dry_run:
            report.results = [
                ItemResult(
                    url=url,
                    status=STATUS_PLANNED,
                    outputs=_records(targets),
                    title=targets[0].link.title,
                    categories=targets[0].link.categories,
                    sources=_sources(targets),
                )
                for url, targets in groups.items()
            ]
            report.finished_at = _now()
            return report

        self._done = 0
        total = len(groups)
        with tempfile.TemporaryDirectory(prefix="pdf2md-") as temporary:
            temporary_dir = Path(temporary)
            workers = max(1, self.options.workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._process, url, targets, temporary_dir, total): url
                    for url, targets in groups.items()
                }
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        report.results.append(future.result())
                    except Exception as error:  # pragma: no cover - defensive
                        logger.exception("unexpected failure for %s", url)
                        report.results.append(
                            ItemResult(url=url, status=STATUS_CONVERT_FAILED, error=str(error))
                        )

        order = {url: position for position, url in enumerate(groups)}
        report.results.sort(key=lambda result: order.get(result.url, 0))
        report.finished_at = _now()

        if self.options.write_index:
            write_index(self.options.output_dir, report)
        write_manifest(self.options.manifest_path or self.options.output_dir / MANIFEST_NAME, report)
        return report

    def _process(
        self, url: str, targets: list[Target], temporary_dir: Path, total: int
    ) -> ItemResult:
        first = targets[0].link
        if self.cancel_event.is_set():
            return ItemResult(
                url=url,
                status=STATUS_CANCELLED,
                title=first.title,
                categories=first.categories,
                sources=_sources(targets),
            )
        result = ItemResult(
            url=url,
            status=STATUS_CONVERTED,
            title=first.title,
            categories=first.categories,
            sources=_sources(targets),
        )
        pending = [
            target
            for target in targets
            if self.options.overwrite or not target.path.exists()
        ]
        if not pending:
            result.status = STATUS_SKIPPED
            result.outputs = _records(targets)
            self._progress(total, STATUS_SKIPPED, first, targets[0].path)
            return result

        download: Download | None = None
        try:
            download = self.downloader.fetch(url, temporary_dir)
        except DownloadError as error:
            result.status = STATUS_DOWNLOAD_FAILED
            result.error = str(error)
            self._progress(total, STATUS_DOWNLOAD_FAILED, first, targets[0].path, str(error))
            return result

        try:
            result.size = download.size
            result.sha256 = download.sha256
            result.final_url = download.final_url
            try:
                markdown = self.converter.convert(download.path)
            except ConversionError as error:
                result.status = STATUS_CONVERT_FAILED
                result.error = str(error)
                self._progress(total, STATUS_CONVERT_FAILED, first, targets[0].path, str(error))
                return result

            if not markdown:
                result.status = STATUS_EMPTY
                logger.warning(
                    "%s produced no text — it is probably a scanned PDF without OCR", url
                )
            if self.options.keep_pdf_dir is not None:
                self._keep_pdf(download, targets[0])
            for target in pending:
                try:
                    write_output(target, markdown, download)
                except OSError as error:
                    result.status = STATUS_WRITE_FAILED
                    result.error = f"{target.path}: {error}"
                    self._progress(total, STATUS_WRITE_FAILED, first, target.path, str(error))
                    return result
            result.outputs = _records(targets)
            self._progress(total, result.status, first, targets[0].path)
            return result
        finally:
            if download is not None:
                download.path.unlink(missing_ok=True)

    def _keep_pdf(self, download: Download, target: Target) -> None:
        keep_dir = self.options.keep_pdf_dir
        assert keep_dir is not None
        relative = target.path.relative_to(self.options.output_dir).with_suffix(".pdf")
        destination = keep_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(download.path, destination)

    def _progress(
        self,
        total: int,
        status: str,
        link: PdfLink,
        path: Path,
        detail: str | None = None,
    ) -> None:
        with self._counter_lock:
            self._done += 1
            position = self._done
        label = f"[{position}/{total}] {status}: {path}"
        if status in FAILURE_STATUSES:
            logger.error("%s — %s (%s)", label, detail or "failed", link.url)
        else:
            logger.info("%s", label)
        if self.on_progress is not None:
            self.on_progress(
                Progress(
                    done=position,
                    total=total,
                    status=status,
                    url=link.url,
                    title=link.title,
                    path=str(path),
                    error=detail,
                )
            )


def write_output(target: Target, markdown: str, download: Download | None) -> None:
    """Write one converted PDF, with YAML front matter describing its source."""
    target.path.parent.mkdir(parents=True, exist_ok=True)
    body = markdown or _EMPTY_NOTE
    target.path.write_text(front_matter(target.link, download) + body + "\n", encoding="utf-8")


_EMPTY_NOTE = (
    "> **Note:** markitdown extracted no text from this PDF. "
    "It is most likely a scan without an OCR text layer.\n"
)


def front_matter(link: PdfLink, download: Download | None) -> str:
    """Build the YAML front matter block for a converted PDF."""
    fields: list[tuple[str, object]] = [
        ("title", link.title),
        ("source_url", link.url),
    ]
    if download is not None and download.final_url != link.url:
        fields.append(("resolved_url", download.final_url))
    fields.append(("categories", list(link.categories)))
    if link.column:
        fields.append(("document_type", link.column))
    if link.source:
        fields.append(("source_document", link.source))
    if link.line:
        fields.append(("source_line", link.line))
    if download is not None:
        fields.append(("pdf_bytes", download.size))
        fields.append(("pdf_sha256", download.sha256))
    fields.append(("converted_at", _now()))
    fields.append(("converter", f"markitdown (via pdf2md {__version__})"))

    lines = ["---"]
    for key, value in fields:
        lines.append(f"{key}: {_yaml_value(value)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_manifest(path: Path, report: RunReport) -> Path:
    """Write a JSON record of the run next to the output tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tool": "pdf2md",
        "version": __version__,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "documents": report.documents,
        "output_dir": str(report.options.output_dir),
        "links_found": report.links_found,
        "links_selected": report.links_selected,
        "counts": report.counts(),
        "items": [result.as_dict() for result in report.results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_index(output_dir: Path, report: RunReport) -> Path:
    """Write ``INDEX.md``: every converted file, grouped by category."""
    by_category: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for result in report.results:
        if result.status not in {STATUS_CONVERTED, STATUS_EMPTY, STATUS_SKIPPED}:
            continue
        for output in result.outputs:
            relative = Path(output.path)
            try:
                relative = relative.relative_to(output_dir)
            except ValueError:
                pass
            categories = output.categories or tuple(relative.parts[:-1])
            by_category.setdefault(categories, []).append((output.title, relative.as_posix()))

    lines = [
        "# Converted PDF library",
        "",
        f"Generated by pdf2md {__version__} on {report.finished_at or _now()}.",
        "",
        f"Source document(s): {', '.join(Path(doc).name for doc in report.documents) or '—'}",
        "",
    ]
    failures = report.failures
    counts = report.counts()
    summary = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items()))
    lines += [f"Files: {report.files_written} · {summary}", ""]
    for categories in sorted(by_category, key=lambda parts: [part.lower() for part in parts]):
        heading = " / ".join(categories) if categories else "(uncategorised)"
        lines.append(f"## {heading}")
        lines.append("")
        for title, relative in sorted(by_category[categories], key=lambda item: item[1]):
            lines.append(f"- [{title or Path(relative).stem}]({_link(relative)})")
        lines.append("")
    if failures:
        lines += ["## Not converted", ""]
        for failure in failures:
            lines.append(f"- {failure.url} — {failure.status}: {failure.error}")
        lines.append("")

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / INDEX_NAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


#: Only the characters that would break a Markdown link target are escaped, so
#: that non-ASCII folder names stay readable in INDEX.md.
_LINK_ESCAPES = {" ": "%20", "(": "%28", ")": "%29", "<": "%3C", ">": "%3E", '"': "%22"}


def _link(relative: str) -> str:
    return "".join(_LINK_ESCAPES.get(char, char) for char in relative)


def _records(targets: list[Target]) -> list[OutputRecord]:
    return [
        OutputRecord(str(target.path), target.link.title, target.link.categories)
        for target in targets
    ]


def _sources(targets: list[Target]) -> list[str]:
    sources: list[str] = []
    for target in targets:
        if target.link.source and target.link.source not in sources:
            sources.append(target.link.source)
    return sources


def _yaml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_yaml_value(item) for item in value) + "]"
    # json.dumps emits a double-quoted scalar, which is valid YAML.
    return json.dumps(str(value), ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
