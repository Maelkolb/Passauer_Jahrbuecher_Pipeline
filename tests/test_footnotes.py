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



# ---------------------------------------------------------------------------
# Plain-digit ref detection + per-page numbered lookup
# ---------------------------------------------------------------------------

class TestPlainDigitFootnoteRefs:
    """Regression for the new context-aware footnote-ref detector.

    Chandra on this corpus emits inline footnote refs as plain digits
    surrounded by whitespace ("Regimentes 1 .") rather than the ``1^`` or
    Unicode-superscript styles the original detector handles. Adding a
    plain-digit pattern raw would have terrible precision (years,
    citations, list numbering all match). The new detector takes a
    per-page set of *actual* footnote numbers and only emits matches
    whose number is in that set.
    """

    def test_matches_simple_body_ref(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "auf vexilla, also Stoffstandarten, die sich auf die gesamte Einheit bezogen 2 ."
        refs = find_plain_digit_refs(text, {1, 2, 3})
        assert len(refs) == 1
        s, e, n = refs[0]
        assert n == 2
        assert text[s:e] == "2"

    def test_does_not_match_year(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Im Jahr 1825 wurde der Verein gegründet."
        refs = find_plain_digit_refs(text, {1, 2, 1825})
        assert refs == []

    def test_set_vetoes_unrelated_number(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Bei Punkt 7 wird es interessant."
        refs = find_plain_digit_refs(text, {1, 2})
        assert refs == []

    def test_does_not_match_citation_list(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Vgl. Tac. ann. 1, 17, 6 ."
        refs = find_plain_digit_refs(text, {1, 6, 17})
        ns = {n for _, _, n in refs}
        assert 1 not in ns
        assert 17 not in ns

    def test_match_at_end_of_paragraph(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Etwas ganz Besonderes 3"
        refs = find_plain_digit_refs(text, {3})
        assert len(refs) == 1
        assert refs[0][2] == 3


class TestFootnoteIsNumbered:
    """The asterisk footnote and bibliography abbreviation list at the
    bottom of the first page have no leading digit; we no longer treat
    those as referenceable footnotes."""

    def test_parsed_number_marks_numbered(self):
        from pjb_pipeline.structure.footnotes import collect_article_footnotes
        article = {
            "id": "art1",
            "pages": [{
                "page_num": 18,
                "blocks": [
                    {"id": "fn1", "type": "footnote", "text": "1 Real footnote one."},
                    {"id": "fn2", "type": "footnote", "text": "2 Real footnote two."},
                ],
            }],
        }
        notes = collect_article_footnotes(article)
        assert len(notes) == 2
        assert all(fn.is_numbered for fn in notes)
        assert [fn.n for fn in notes] == [1, 2]

    def test_no_leading_digit_marks_unnumbered_with_page_seq(self):
        from pjb_pipeline.structure.footnotes import collect_article_footnotes
        article = {
            "id": "art1",
            "pages": [{
                "page_num": 18,
                "blocks": [
                    {"id": "fnA", "type": "footnote",
                     "text": "* Im vorliegenden Beitrag werden ..."},
                    {"id": "fnB", "type": "footnote",
                     "text": "Ankersdorfer, Studien = ..."},
                ],
            }],
        }
        notes = collect_article_footnotes(article)
        assert len(notes) == 2
        assert all(not fn.is_numbered for fn in notes)
        assert [fn.n for fn in notes] == [1, 2]

    def test_per_page_numbering_does_not_collide(self):
        from pjb_pipeline.structure.footnotes import (
            collect_article_footnotes, numbered_footnotes_by_page,
        )
        article = {
            "id": "art1",
            "pages": [
                {"page_num": 18, "blocks": [
                    {"id": "fn18_1", "type": "footnote", "text": "1 P18 fn1"},
                    {"id": "fn18_2", "type": "footnote", "text": "2 P18 fn2"},
                ]},
                {"page_num": 19, "blocks": [
                    {"id": "fn19_1", "type": "footnote", "text": "1 P19 fn1"},
                    {"id": "fn19_2", "type": "footnote", "text": "2 P19 fn2"},
                ]},
            ],
        }
        notes = collect_article_footnotes(article)
        assert len(notes) == 4
        by_page = numbered_footnotes_by_page(notes)
        assert by_page[18] == {1, 2}
        assert by_page[19] == {1, 2}


class TestLinkArticleFootnotesPerPage:
    """The body resolver must scope to the source page of the body block,
    so per-page footnote numbering doesn't cross-link refs."""

    def test_body_ref_on_page_19_resolves_to_page_19_footnote(self):
        from pjb_pipeline.structure.footnotes import link_article_footnotes
        article = {
            "id": "art1",
            "pages": [
                {"page_num": 18, "blocks": [
                    {"id": "fn18_1", "type": "footnote", "text": "1 Page-18 fn1."},
                    {"id": "fn18_2", "type": "footnote", "text": "2 Page-18 fn2."},
                ]},
                {"page_num": 19, "blocks": [
                    {"id": "p19_body", "type": "text",
                     "text": "Etwas ganz Wichtiges bezogen 2 . Mehr Text."},
                    {"id": "fn19_1", "type": "footnote", "text": "1 Page-19 fn1."},
                    {"id": "fn19_2", "type": "footnote", "text": "2 Page-19 fn2."},
                ]},
            ],
        }
        notes, refs = link_article_footnotes(article)
        page19_refs = [r for r in refs if r.block_id == "p19_body" and r.n == 2]
        assert len(page19_refs) == 1
        target = next(fn for fn in notes
                      if fn.html_id == page19_refs[0].target_id)
        assert target.page_num == 19
        assert "Page-19 fn2" in target.text
