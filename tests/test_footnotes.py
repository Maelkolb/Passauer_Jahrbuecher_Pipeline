"""Tests for ``pjb_pipeline.structure.footnotes``."""

import pytest

from pjb_pipeline.structure.footnotes import (
    find_refs_in_text,
    parse_footnote_body,
    collect_article_footnotes,
    link_article_footnotes,
)


class TestFindRefsInText:
    def test_chandra_caret_marker(self):
        text = "See note 1^ for details."
        refs = find_refs_in_text(text)
        assert len(refs) == 1
        assert refs[0][2] == 1  # number

    def test_multiple_refs(self):
        text = "First 1^, second 2^, third 23^."
        refs = find_refs_in_text(text)
        assert [r[2] for r in refs] == [1, 2, 23]

    def test_unicode_superscript(self):
        text = "Mit Anmerkung\u00b9 und auch\u00b2."
        refs = find_refs_in_text(text)
        assert [r[2] for r in refs] == [1, 2]

    def test_empty_text(self):
        assert find_refs_in_text("") == []
        assert find_refs_in_text("no refs here at all.") == []

    def test_year_in_text_not_a_footnote(self):
        # "1925" alone shouldn't fire (no caret, no superscript glyph)
        text = "In 1925 something happened."
        assert find_refs_in_text(text) == []

    def test_refs_sorted_by_position(self):
        text = "End 2^ and beginning 1^"
        refs = find_refs_in_text(text)
        # Sorted by start position, not by number
        assert refs[0][2] == 2
        assert refs[1][2] == 1


class TestParseFootnoteBody:
    def test_leading_number_with_period(self):
        n, body = parse_footnote_body("1. Citation goes here.")
        assert n == 1
        assert body == "Citation goes here."

    def test_leading_number_with_space(self):
        n, body = parse_footnote_body("12 Citation here.")
        assert n == 12
        assert body == "Citation here."

    def test_no_leading_number(self):
        n, body = parse_footnote_body("Just a body with no number.")
        assert n is None
        assert body == "Just a body with no number."

    def test_empty(self):
        n, body = parse_footnote_body("")
        assert n is None
        assert body == ""


class TestLinkArticleFootnotes:
    def _make_article(self):
        return {
            "id": "test-art-01",
            "title": "Test Article",
            "page_first": 1,
            "page_last": 2,
            "pages": [
                {
                    "page_num": 1,
                    "image_filename": "page_0001.png",
                    "image_width": 1000,
                    "image_height": 1500,
                    "blocks": [
                        {"id": "p1_b001", "type": "text",
                         "bbox": [100, 200, 900, 800],
                         "text": "This is body text with a marker 1^ and another 2^."},
                        {"id": "p1_b002", "type": "footnote",
                         "bbox": [100, 1200, 900, 1300],
                         "text": "1. First footnote body."},
                        {"id": "p1_b003", "type": "footnote",
                         "bbox": [100, 1310, 900, 1400],
                         "text": "2. Second footnote body."},
                    ],
                },
            ],
        }

    def test_collects_footnotes(self):
        article = self._make_article()
        notes = collect_article_footnotes(article)
        assert len(notes) == 2
        assert notes[0].n == 1
        assert notes[1].n == 2
        assert "First footnote" in notes[0].text
        # Each footnote should remember which page it sat on, so the
        # graph emitter can link it back to its page node.
        assert notes[0].page_num == 1
        assert notes[1].page_num == 1

    def test_links_refs_to_notes(self):
        article = self._make_article()
        notes, refs = link_article_footnotes(article)
        assert len(refs) == 2
        # Both refs should be resolved to a footnote target
        assert refs[0].target_id is not None
        assert refs[1].target_id is not None
        # Targets should match the corresponding note's html_id
        notes_by_n = {fn.n: fn for fn in notes}
        for r in refs:
            assert r.target_id == notes_by_n[r.n].html_id

    def test_unresolved_ref_when_no_matching_note(self):
        article = {
            "id": "test-art-02",
            "pages": [
                {
                    "page_num": 1,
                    "image_filename": "p.png",
                    "image_width": 1000,
                    "image_height": 1500,
                    "blocks": [
                        {"id": "p1_b001", "type": "text", "bbox": [0, 0, 100, 100],
                         "text": "Reference to nothing 7^."},
                    ],
                },
            ],
        }
        notes, refs = link_article_footnotes(article)
        assert notes == []
        assert len(refs) == 1
        assert refs[0].n == 7
        assert refs[0].target_id is None
