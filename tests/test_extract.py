"""Tests for link extraction and category tracking."""

from __future__ import annotations

import textwrap

from pdf2md.extract import PdfLink, clean_inline, extract_links, is_ambiguous, looks_like_pdf


def test_headings_become_categories():
    document = textwrap.dedent(
        """\
        # Library

        ## Physics

        ### Curriculum

        - Philosophy — [philosophy.pdf](https://example.org/a/philosophy.pdf)

        ### Learning Frameworks

        #### Lyceum

        - Term A — [term_a.pdf](https://example.org/b/term_a.pdf)
        """
    )
    links = extract_links(document, source="doc.md")
    assert [link.categories for link in links] == [
        ("Physics", "Curriculum"),
        ("Physics", "Learning Frameworks", "Lyceum"),
    ]
    assert [link.title for link in links] == ["Philosophy", "Term A"]
    assert links[0].source == "doc.md"
    assert links[0].line == 7


def test_level_one_heading_is_the_document_title_by_default():
    document = "# The Library\n\n## Maths\n\n- [a.pdf](https://e.org/a.pdf)\n"
    assert extract_links(document)[0].categories == ("Maths",)
    assert extract_links(document, include_top_heading=True)[0].categories == (
        "The Library",
        "Maths",
    )


def test_max_category_depth_limits_nesting():
    document = "## A\n\n### B\n\n#### C\n\n##### D\n\n- [x.pdf](https://e.org/x.pdf)\n"
    assert extract_links(document, max_category_depth=2)[0].categories == ("A", "B")
    assert extract_links(document, max_category_depth=0)[0].categories == ()


def test_table_links_get_a_title_from_row_and_column():
    document = textwrap.dedent(
        """\
        ## HAIRDRESSING

        ### Aesthetics

        | Direction | Year | Subject | LF | CF | Curriculum |
        |---|---|---|---|---|---|
        | THEORY | A | Limb Aesthetics | [PDF](https://e.org/5/lf_limbs.pdf) | [PDF](https://e.org/4/cf_limbs.pdf) | — |
        | THEORY | A | Missing Everything | — | — | — |
        """
    )
    links = extract_links(document)
    assert len(links) == 2
    assert links[0].title == "THEORY · A · Limb Aesthetics — LF"
    assert links[0].column == "LF"
    assert links[1].title == "THEORY · A · Limb Aesthetics — CF"
    assert links[0].categories == ("HAIRDRESSING", "Aesthetics")


def test_table_state_resets_after_the_table():
    document = textwrap.dedent(
        """\
        | A | B |
        |---|---|
        | row | [PDF](https://e.org/in_table.pdf) |

        Loose paragraph with [outside.pdf](https://e.org/outside.pdf) in it.
        """
    )
    links = extract_links(document)
    assert links[0].column == "B"
    assert links[1].column is None
    assert links[1].title == "Loose paragraph with"


def test_recognises_autolinks_bare_urls_and_html_anchors():
    document = textwrap.dedent(
        """\
        ## Mixed

        Autolink: <https://e.org/auto.pdf>

        Bare: https://e.org/bare.pdf and then a sentence.

        <a href="https://e.org/anchor.pdf">Anchor label</a>
        """
    )
    urls = [link.url for link in extract_links(document)]
    assert urls == [
        "https://e.org/auto.pdf",
        "https://e.org/bare.pdf",
        "https://e.org/anchor.pdf",
    ]
    assert extract_links(document)[2].title == "Anchor label"


def test_trailing_punctuation_is_not_part_of_the_url():
    links = extract_links("See https://e.org/report.pdf, and https://e.org/other.pdf.")
    assert [link.url for link in links] == ["https://e.org/report.pdf", "https://e.org/other.pdf"]


def test_ignores_non_pdf_links():
    document = textwrap.dedent(
        """\
        ## Subject

        - Indicators — [indicators.zip](https://e.org/indicators.zip)
        - Site: <https://subject.example.org/el/page>
        - Image — [shot.png](https://e.org/shot.png)
        - Real — [doc.pdf](https://e.org/doc.pdf)
        """
    )
    assert [link.url for link in extract_links(document)] == ["https://e.org/doc.pdf"]


def test_code_blocks_are_ignored():
    document = textwrap.dedent(
        """\
        ## Subject

        ```
        [not a link](https://e.org/fenced.pdf)
        ```

        - Real — [doc.pdf](https://e.org/doc.pdf)
        """
    )
    assert [link.url for link in extract_links(document)] == ["https://e.org/doc.pdf"]


def test_duplicate_url_in_same_category_is_kept_once_but_across_categories_twice():
    document = textwrap.dedent(
        """\
        ## A

        - one — [x.pdf](https://e.org/x.pdf)
        - again — [x.pdf](https://e.org/x.pdf)

        ## B

        - shared — [x.pdf](https://e.org/x.pdf)
        """
    )
    links = extract_links(document)
    assert len(links) == 2
    assert [link.categories for link in links] == [("A",), ("B",)]


def test_setext_headings_are_categories_too():
    document = "Physics\n=======\n\nCurriculum\n----------\n\n- [a.pdf](https://e.org/a.pdf)\n"
    assert extract_links(document)[0].categories == ("Curriculum",)


def test_title_falls_back_to_link_text_then_heading():
    document = textwrap.dedent(
        """\
        ## Fallbacks

        - [named_file.pdf](https://e.org/named_file.pdf)
        - [PDF](https://e.org/generic.pdf)
        """
    )
    links = extract_links(document)
    assert links[0].title == "named_file"
    assert links[1].title == "Fallbacks"


def test_query_strings_and_fragments_do_not_hide_the_extension():
    assert looks_like_pdf("https://e.org/a.pdf?download=1")
    assert looks_like_pdf("https://e.org/a.pdf#page=3")
    assert not looks_like_pdf("https://e.org/a.zip", "PDF")
    assert looks_like_pdf("https://e.org/download/1234", "PDF")


def test_ambiguous_links_only_appear_when_asked_for():
    document = "## A\n\n- [Download](https://e.org/files/5521)\n"
    assert extract_links(document) == []
    links = extract_links(document, include_ambiguous=True)
    assert [link.url for link in links] == ["https://e.org/files/5521"]
    assert is_ambiguous("https://e.org/files/5521")
    assert not is_ambiguous("https://e.org/files/5521.pdf")


def test_clean_inline_strips_markup():
    assert clean_inline("**Bold** and `code` and [a](b)") == "Bold and code and a"


def test_category_path_property():
    link = PdfLink(url="https://e.org/a.pdf", title="t", categories=("A", "B"))
    assert link.category_path == "A / B"


def test_escaped_pipes_do_not_split_table_cells():
    document = textwrap.dedent(
        """\
        | Subject | Doc |
        |---|---|
        | Maths \\| Algebra | [PDF](https://e.org/m.pdf) |
        """
    )
    link = extract_links(document)[0]
    assert link.title == "Maths | Algebra — Doc"
