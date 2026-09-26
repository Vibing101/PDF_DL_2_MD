"""Command line interface for pdf2md."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pdf2md import __version__
from pdf2md.convert import ConversionError
from pdf2md.download import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_BYTES,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_USER_AGENT,
    Downloader,
)
from pdf2md.pipeline import (
    FAILURE_STATUSES,
    MANIFEST_NAME,
    Options,
    Pipeline,
    RunReport,
)

DESCRIPTION = """\
Find PDF links in a document, download each PDF to a temporary file, and convert
it to Markdown with Microsoft's markitdown. Output is written into subfolders
that mirror the document's own categories (its headings).
"""

EPILOG = """\
examples:
  # see what would be downloaded, without touching the network
  pdf2md library.md --dry-run

  # convert everything into ./output, keeping the PDFs too
  pdf2md library.md -o output --keep-pdfs pdfs

  # just one subject, 8 parallel downloads, half a second between requests
  pdf2md library.md --category-filter 'Physics' --workers 8 --delay 0.5
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "documents",
        nargs="+",
        type=Path,
        metavar="DOCUMENT",
        help="document(s) listing PDF links (.md, .txt, .html, or anything markitdown reads)",
    )
    parser.add_argument("--version", action="version", version=f"pdf2md {__version__}")

    output = parser.add_argument_group("output")
    output.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="where to write the Markdown tree (default: %(default)s)",
    )
    output.add_argument(
        "--keep-pdfs",
        type=Path,
        metavar="DIR",
        help="also keep the downloaded PDFs, in the same folder layout",
    )
    output.add_argument(
        "--flat",
        action="store_true",
        help="write every file straight into the output directory, no category subfolders",
    )
    output.add_argument(
        "--name-from",
        choices=("url", "title"),
        default="url",
        help="build filenames from the PDF's URL or from its link title (default: %(default)s)",
    )
    output.add_argument(
        "--manifest",
        type=Path,
        metavar="PATH",
        help=f"where to write the JSON run report (default: <output-dir>/{MANIFEST_NAME})",
    )
    output.add_argument(
        "--no-index",
        dest="write_index",
        action="store_false",
        help="do not write INDEX.md",
    )
    output.add_argument(
        "--overwrite",
        action="store_true",
        help="re-download and rewrite files that already exist (default: skip them)",
    )

    selection = parser.add_argument_group("which links to take")
    selection.add_argument(
        "--max-category-depth",
        type=int,
        default=3,
        help="how many heading levels become subfolders "
        "(default: %(default)s; 0 for none, -1 for unlimited)",
    )
    selection.add_argument(
        "--include-top-heading",
        action="store_true",
        help="use the level-1 heading as a category too (by default it is the document title)",
    )
    selection.add_argument(
        "--include-filter",
        metavar="REGEX",
        help="only take URLs matching this regular expression",
    )
    selection.add_argument(
        "--exclude-filter",
        metavar="REGEX",
        help="skip URLs matching this regular expression",
    )
    selection.add_argument(
        "--category-filter",
        metavar="REGEX",
        help="only take links whose category path matches this regular expression",
    )
    selection.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="stop after N unique PDFs (handy for a first trial run)",
    )
    selection.add_argument(
        "--probe",
        action="store_true",
        help="also consider links with no .pdf extension, asking each server with a HEAD request",
    )

    network = parser.add_argument_group("network")
    network.add_argument(
        "--workers",
        type=int,
        default=4,
        help="parallel downloads (default: %(default)s)",
    )
    network.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="minimum seconds between requests, to stay polite (default: %(default)s)",
    )
    network.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT,
        help="connect timeout in seconds (default: %(default)s)",
    )
    network.add_argument(
        "--read-timeout",
        type=float,
        default=DEFAULT_READ_TIMEOUT,
        help="read timeout in seconds (default: %(default)s)",
    )
    network.add_argument(
        "--retries",
        type=int,
        default=3,
        help="retries per request (default: %(default)s)",
    )
    network.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help="refuse PDFs larger than this (default: %(default)s)",
    )
    network.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="User-Agent header to send (default: %(default)s)",
    )
    network.add_argument(
        "--insecure",
        action="store_true",
        help="do not verify TLS certificates (last resort)",
    )

    mode = parser.add_argument_group("run mode")
    mode.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="list what would be downloaded and where it would go, then stop",
    )
    mode.add_argument("-v", "--verbose", action="store_true", help="log debug detail")
    mode.add_argument("-q", "--quiet", action="store_true", help="only log warnings and errors")
    return parser


def configure_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s", stream=sys.stderr)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)


def options_from_args(args: argparse.Namespace) -> Options:
    return Options(
        output_dir=args.output_dir,
        keep_pdf_dir=args.keep_pdfs,
        workers=args.workers,
        delay=args.delay,
        max_category_depth=args.max_category_depth,
        include_top_heading=args.include_top_heading,
        name_from=args.name_from,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        limit=args.limit,
        probe=args.probe,
        include_pattern=args.include_filter,
        exclude_pattern=args.exclude_filter,
        category_pattern=args.category_filter,
        write_index=args.write_index,
        manifest_path=args.manifest,
        flat=args.flat,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 on success, 1 if anything failed, 2 on bad usage."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose, args.quiet)

    missing = [str(document) for document in args.documents if not document.is_file()]
    if missing:
        parser.error("no such file: " + ", ".join(missing))
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    options = options_from_args(args)
    downloader = Downloader(
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        max_bytes=args.max_bytes,
        retries=args.retries,
        delay=args.delay,
        user_agent=args.user_agent,
        verify=not args.insecure,
    )
    pipeline = Pipeline(options, downloader=downloader)

    try:
        report = pipeline.run(list(args.documents))
    except ConversionError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("interrupted", file=sys.stderr)
        return 130

    print_summary(report, dry_run=args.dry_run)
    return 1 if report.failures else 0


def print_summary(report: RunReport, *, dry_run: bool) -> None:
    """Print the closing summary on stdout."""
    if dry_run:
        for result in report.results:
            for output in result.outputs:
                print(f"{output.path}\t{result.url}")
        print(
            f"\n{report.links_selected} link(s) selected of {report.links_found} found; "
            f"{len(report.results)} unique PDF(s) would be downloaded. Nothing was written.",
        )
        return

    counts = report.counts()
    print(f"\nDone in {report.started_at} → {report.finished_at}")
    print(f"  links found      : {report.links_found}")
    print(f"  links selected   : {report.links_selected}")
    print(f"  unique PDFs      : {len(report.results)}")
    print(f"  markdown written : {report.files_written}")
    for status in sorted(counts):
        marker = "!" if status in FAILURE_STATUSES else " "
        print(f"  {marker} {status:<18}: {counts[status]}")
    print(f"  output           : {report.options.output_dir}")
    failures = report.failures
    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures[:20]:
            print(f"  {failure.url}\n    {failure.status}: {failure.error}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more (see the manifest)", file=sys.stderr)
