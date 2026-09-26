"""Tests for turning titles and URLs into paths."""

from __future__ import annotations

from pdf2md.naming import (
    MAX_COMPONENT_LENGTH,
    category_parts,
    sanitize_component,
    stem_from_title,
    stem_from_url,
    url_digest,
)


def test_unsafe_characters_are_replaced():
    assert sanitize_component('a/b\\c:d*e?f"g<h>i|j') == "a-b-c-d-e-f-g-h-i-j"


def test_whitespace_becomes_underscores_and_collapses():
    assert sanitize_component("  Learning   Frameworks\t– Lyceum ") == "Learning_Frameworks_-_Lyceum"


def test_greek_is_preserved():
    assert sanitize_component("Φυσική Αγωγή") == "Φυσική_Αγωγή"


def test_separator_characters_become_dashes():
    assert sanitize_component("THEORY · A · Aesthetics — LF") == "THEORY_-_A_-_Aesthetics_-_LF"


def test_empty_and_reserved_names():
    assert sanitize_component("") == "untitled"
    assert sanitize_component("...") == "untitled"
    assert sanitize_component("con") == "con_"
    assert sanitize_component("NUL") == "NUL_"


def test_long_names_are_trimmed():
    assert len(sanitize_component("x" * 300)) == MAX_COMPONENT_LENGTH


def test_stem_from_url_drops_the_extension_and_decodes_escapes():
    assert stem_from_url("https://e.org/a/b/ap_filosofia.pdf") == "ap_filosofia"
    assert stem_from_url("https://e.org/a/%CF%86%CF%85%CF%83.pdf") == "φυσ"
    assert stem_from_url("https://e.org/a/report.PDF?x=1") == "report"


def test_stem_from_url_falls_back_to_a_digest_for_pathless_urls():
    assert stem_from_url("https://e.org/") == url_digest("https://e.org/")


def test_stem_from_title_falls_back_to_the_url():
    assert stem_from_title("Term A", "https://e.org/x.pdf") == "Term_A"
    assert stem_from_title("...", "https://e.org/x.pdf") == "x"


def test_category_parts_skips_empty_categories():
    assert category_parts(("Physics", "", "Lyceum")) == ["Physics", "Lyceum"]


def test_url_digest_is_stable_and_short():
    assert url_digest("https://e.org/a.pdf") == url_digest("https://e.org/a.pdf")
    assert url_digest("https://e.org/a.pdf") != url_digest("https://e.org/b.pdf")
    assert len(url_digest("https://e.org/a.pdf")) == 8
