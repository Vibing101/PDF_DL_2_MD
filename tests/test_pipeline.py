"""End-to-end tests: a document of links in, a Markdown tree out.

These run the real pipeline — real HTTP (to a local server), real markitdown —
so they cover the whole path a user takes.
"""

from __future__ import annotations

import json
import textwrap
import threading
from pathlib import Path

import pytest

from pdf2md.cli import main
from pdf2md.download import Downloader
from pdf2md.pipeline import (
    STATUS_CANCELLED,
    STATUS_CONVERTED,
    STATUS_DOWNLOAD_FAILED,
    STATUS_EMPTY,
    STATUS_SKIPPED,
    Options,
    Pipeline,
)
from tests.conftest import make_pdf

pytestmark = pytest.mark.usefixtures("no_proxy")


def write_document(tmp_path: Path, body: str, name: str = "library.md") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def run(tmp_path: Path, document: Path, **overrides) -> tuple[Pipeline, object]:
    options = Options(output_dir=tmp_path / "out", workers=2, **overrides)
    pipeline = Pipeline(options, downloader=Downloader(retries=0))
    return pipeline, pipeline.run([document])


def test_converts_into_category_subfolders(server, tmp_path):
    physics = server.add("/physics.pdf", make_pdf(["Physics philosophy"]))
    maths = server.add("/maths.pdf", make_pdf(["Maths curriculum"]))
    document = write_document(
        tmp_path,
        f"""\
        # Library

        ## Physics

        ### Curriculum

        - Philosophy — [physics.pdf]({physics})

        ## Maths

        - Curriculum — [maths.pdf]({maths})
        """,
    )
    _, report = run(tmp_path, document)

    physics_out = tmp_path / "out" / "Physics" / "Curriculum" / "physics.md"
    maths_out = tmp_path / "out" / "Maths" / "maths.md"
    assert physics_out.is_file() and maths_out.is_file()
    assert "Physics philosophy" in physics_out.read_text(encoding="utf-8")
    assert report.files_written == 2
    assert not report.failures
    assert {result.status for result in report.results} == {STATUS_CONVERTED}


def test_front_matter_records_the_source(server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Body text"]))
    document = write_document(tmp_path, f"## Subject\n\n- Term A — [a.pdf]({url})\n")
    run(tmp_path, document)

    text = (tmp_path / "out" / "Subject" / "a.md").read_text(encoding="utf-8")
    header = text.split("---")[1]
    assert 'title: "Term A"' in header
    assert f'source_url: "{url}"' in header
    assert 'categories: ["Subject"]' in header
    assert 'source_document: "library.md"' in header
    assert "pdf_sha256:" in header
    assert "Body text" in text


def test_a_pdf_listed_twice_is_downloaded_once_and_written_twice(server, tmp_path):
    url = server.add("/shared.pdf", make_pdf(["Shared document"]))
    document = write_document(
        tmp_path,
        f"""\
        ## Cooking

        - Computing — [shared.pdf]({url})

        ## Hairdressing

        - Computing — [shared.pdf]({url})
        """,
    )
    _, report = run(tmp_path, document)

    assert (tmp_path / "out" / "Cooking" / "shared.md").is_file()
    assert (tmp_path / "out" / "Hairdressing" / "shared.md").is_file()
    assert len(report.results) == 1
    assert report.files_written == 2
    assert server.hits.count("/shared.pdf") == 1


def test_same_filename_in_one_folder_from_different_urls_does_not_clash(server, tmp_path):
    first = server.add("/a/report.pdf", make_pdf(["First report"]))
    second = server.add("/b/report.pdf", make_pdf(["Second report"]))
    document = write_document(
        tmp_path,
        f"""\
        ## Subject

        - First — [report.pdf]({first})
        - Second — [report.pdf]({second})
        """,
    )
    run(tmp_path, document)

    written = sorted(path.name for path in (tmp_path / "out" / "Subject").glob("*.md"))
    assert len(written) == 2
    assert "report.md" in written
    # The second URL keeps the same stem plus a short hash of its URL.
    assert any(name != "report.md" and name.startswith("report-") for name in written)
    bodies = {
        (tmp_path / "out" / "Subject" / name).read_text(encoding="utf-8") for name in written
    }
    assert any("First report" in body for body in bodies)
    assert any("Second report" in body for body in bodies)


def test_existing_files_are_skipped_unless_overwrite(server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Original text"]))
    document = write_document(tmp_path, f"## Subject\n\n- A — [a.pdf]({url})\n")
    run(tmp_path, document)
    assert server.hits.count("/a.pdf") == 1

    _, second = run(tmp_path, document)
    assert [result.status for result in second.results] == [STATUS_SKIPPED]
    assert server.hits.count("/a.pdf") == 1

    _, third = run(tmp_path, document, overwrite=True)
    assert [result.status for result in third.results] == [STATUS_CONVERTED]
    assert server.hits.count("/a.pdf") == 2


def test_failures_are_recorded_and_do_not_stop_the_run(server, tmp_path):
    good = server.add("/good.pdf", make_pdf(["Good document"]))
    broken = server.add("/broken.pdf", b"<html>not a pdf</html>", content_type="text/html")
    missing = server.base_url + "/missing.pdf"
    document = write_document(
        tmp_path,
        f"""\
        ## Subject

        - Good — [good.pdf]({good})
        - Broken — [broken.pdf]({broken})
        - Missing — [missing.pdf]({missing})
        """,
    )
    _, report = run(tmp_path, document)

    assert (tmp_path / "out" / "Subject" / "good.md").is_file()
    statuses = {result.url: result.status for result in report.results}
    assert statuses[good] == STATUS_CONVERTED
    assert statuses[broken] == STATUS_DOWNLOAD_FAILED
    assert statuses[missing] == STATUS_DOWNLOAD_FAILED
    assert len(report.failures) == 2


def test_scanned_pdf_with_no_text_is_flagged_but_still_written(server, tmp_path):
    url = server.add("/scan.pdf", make_pdf([]))
    document = write_document(tmp_path, f"## Subject\n\n- Scan — [scan.pdf]({url})\n")
    _, report = run(tmp_path, document)

    output = tmp_path / "out" / "Subject" / "scan.md"
    assert report.results[0].status == STATUS_EMPTY
    assert "no text" in output.read_text(encoding="utf-8")


def test_temporary_pdfs_are_cleaned_up_and_keep_pdfs_mirrors_the_tree(server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Kept"]))
    document = write_document(tmp_path, f"## Subject\n\n- A — [a.pdf]({url})\n")
    run(tmp_path, document, keep_pdf_dir=tmp_path / "pdfs")

    kept = tmp_path / "pdfs" / "Subject" / "a.pdf"
    assert kept.is_file() and kept.read_bytes().startswith(b"%PDF-")
    assert not list(tmp_path.glob("pdf2md-*"))


def test_manifest_and_index_describe_the_run(server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Indexed"]))
    document = write_document(tmp_path, f"## Φυσική\n\n- Όρος Α — [a.pdf]({url})\n")
    run(tmp_path, document)

    manifest = json.loads((tmp_path / "out" / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["links_found"] == 1
    assert manifest["counts"] == {STATUS_CONVERTED: 1}
    assert manifest["items"][0]["categories"] == ["Φυσική"]
    assert manifest["items"][0]["outputs"][0]["title"] == "Όρος Α"
    assert manifest["items"][0]["sha256"]

    index = (tmp_path / "out" / "INDEX.md").read_text(encoding="utf-8")
    assert "## Φυσική" in index
    assert "Όρος Α" in index


def test_filters_and_limit_narrow_the_selection(server, tmp_path):
    keep = server.add("/keep/a.pdf", make_pdf(["Keep"]))
    drop = server.add("/drop/b.pdf", make_pdf(["Drop"]))
    document = write_document(
        tmp_path,
        f"""\
        ## Physics

        - A — [a.pdf]({keep})

        ## Maths

        - B — [b.pdf]({drop})
        """,
    )
    _, report = run(tmp_path, document, category_pattern="Physics")
    assert [result.url for result in report.results] == [keep]

    _, excluded = run(tmp_path, document, exclude_pattern="/drop/", overwrite=True)
    assert [result.url for result in excluded.results] == [keep]

    _, limited = run(tmp_path, document, limit=1, overwrite=True)
    assert len(limited.results) == 1


def test_flat_and_name_from_title(server, tmp_path):
    url = server.add("/x.pdf", make_pdf(["Flat"]))
    document = write_document(tmp_path, f"## Physics\n\n- Term A — [x.pdf]({url})\n")
    run(tmp_path, document, flat=True, name_from="title")
    assert (tmp_path / "out" / "Term_A.md").is_file()


def test_dry_run_touches_neither_network_nor_disk(server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Never fetched"]))
    document = write_document(tmp_path, f"## Subject\n\n- A — [a.pdf]({url})\n")
    _, report = run(tmp_path, document, dry_run=True)

    assert server.hits == []
    assert not (tmp_path / "out").exists()
    assert [output.path for output in report.results[0].outputs] == [
        str(tmp_path / "out" / "Subject" / "a.md")
    ]


def test_probe_keeps_only_the_links_that_serve_pdfs(server, tmp_path):
    pdf_url = server.add("/files/1", make_pdf(["Behind a redirect-style URL"]))
    page_url = server.add("/files/2", b"<html>page</html>", content_type="text/html")
    document = write_document(
        tmp_path,
        f"""\
        ## Subject

        - Real — [Download]({pdf_url})
        - Page — [Download]({page_url})
        """,
    )
    _, report = run(tmp_path, document, probe=True)
    assert [result.url for result in report.results] == [pdf_url]
    assert report.files_written == 1


def test_cli_end_to_end_returns_zero_and_writes_files(server, tmp_path, capsys):
    url = server.add("/a.pdf", make_pdf(["Via the CLI"]))
    document = write_document(tmp_path, f"## Subject\n\n- A — [a.pdf]({url})\n")
    out = tmp_path / "cli-out"

    code = main([str(document), "-o", str(out), "--quiet", "--retries", "0"])

    assert code == 0
    assert "Via the CLI" in (out / "Subject" / "a.md").read_text(encoding="utf-8")
    assert "markdown written : 1" in capsys.readouterr().out


def test_cli_reports_failures_with_exit_code_one(server, tmp_path):
    url = server.base_url + "/nope.pdf"
    document = write_document(tmp_path, f"## Subject\n\n- A — [a.pdf]({url})\n")
    assert main([str(document), "-o", str(tmp_path / "out"), "--quiet", "--retries", "0"]) == 1


def test_cli_rejects_a_missing_document(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main([str(tmp_path / "nope.md")])
    assert error.value.code == 2
    assert "no such file" in capsys.readouterr().err


def test_real_sample_documents_are_parsed(samples_dir, tmp_path):
    documents = sorted(samples_dir.glob("*.md"))
    if not documents:
        pytest.skip("no sample documents checked in")
    options = Options(output_dir=tmp_path / "out", dry_run=True)
    pipeline = Pipeline(options, downloader=Downloader(retries=0))
    found, selected = pipeline.collect(documents)
    assert len(found) > 1000
    assert all(link.categories for link in selected)
    assert all(link.url.lower().endswith(".pdf") for link in selected)
    plan = pipeline.plan(selected)
    assert len({target.path for targets in plan.values() for target in targets}) == sum(
        len(targets) for targets in plan.values()
    )


def test_index_lists_each_copy_under_its_own_title(server, tmp_path):
    url = server.add("/shared.pdf", make_pdf(["Shared"]))
    document = write_document(
        tmp_path,
        f"""\
        ## Cooking

        - Computing for cooks — [shared.pdf]({url})

        ## Hairdressing

        - Computing for stylists — [shared.pdf]({url})
        """,
    )
    run(tmp_path, document)

    index = (tmp_path / "out" / "INDEX.md").read_text(encoding="utf-8")
    assert "Computing for cooks" in index
    assert "Computing for stylists" in index


def test_index_link_targets_keep_non_ascii_readable(server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Body"]))
    document = write_document(tmp_path, f"## Φυσική\n\n- Όρος — [a.pdf]({url})\n")
    run(tmp_path, document)

    index = (tmp_path / "out" / "INDEX.md").read_text(encoding="utf-8")
    assert "(Φυσική/a.md)" in index


def test_run_links_converts_an_explicit_selection_and_reports_progress(server, tmp_path):
    kept = server.add("/keep.pdf", make_pdf(["Kept"]))
    skipped = server.add("/skip.pdf", make_pdf(["Not asked for"]))
    document = write_document(
        tmp_path,
        f"## Physics\n\n- Keep — [k.pdf]({kept})\n- Skip — [s.pdf]({skipped})\n",
    )
    steps = []
    options = Options(output_dir=tmp_path / "out", workers=1)
    pipeline = Pipeline(options, downloader=Downloader(retries=0), on_progress=steps.append)
    _, links = pipeline.collect([document])

    report = pipeline.run_links([link for link in links if link.url == kept])

    assert report.links_selected == 1
    assert report.files_written == 1
    assert (tmp_path / "out" / "Physics" / "keep.md").is_file()
    assert not (tmp_path / "out" / "Physics" / "skip.md").exists()
    assert [(step.done, step.total, step.status) for step in steps] == [(1, 1, "converted")]
    assert steps[0].as_dict()["url"] == kept


def test_a_set_cancel_event_stops_the_run_before_downloading(server, tmp_path):
    url = server.add("/a.pdf", make_pdf(["Never fetched"]))
    document = write_document(tmp_path, f"## Physics\n\n- A — [a.pdf]({url})\n")
    cancel = threading.Event()
    cancel.set()
    options = Options(output_dir=tmp_path / "out", workers=1)
    pipeline = Pipeline(options, downloader=Downloader(retries=0), cancel_event=cancel)

    report = pipeline.run([document])

    assert [result.status for result in report.results] == [STATUS_CANCELLED]
    assert report.files_written == 0
    assert server.hits == []
