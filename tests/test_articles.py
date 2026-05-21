"""Tests for ``pjb_pipeline.structure.articles``."""

import pytest

from pjb_pipeline.config import VolumeConfig
from pjb_pipeline.structure.articles import detect_articles, infer_printed_page_offset


def _cfg(slug="pjb-test-2026", page_range=None, offset=None):
    return VolumeConfig(
        pdf_path="(unused)",
        volume_number=99,
        volume_number_roman="XCIX",
        volume_year=2026,
        slug=slug,
        page_range=page_range,
        printed_page_offset=offset,
    )


def _page(pn, blocks, w=1000, h=1500):
    return {
        "page_num": pn,
        "image_filename": f"page_{pn:04d}.png",
        "image_width": w,
        "image_height": h,
        "blocks": blocks,
    }


def _block(bid, type_, bbox, text):
    return {"id": bid, "type": type_, "raw_type": type_, "bbox": bbox, "text": text}


class TestHeuristicFallback:
    def test_finds_articles_from_section_headers(self):
        # 3 pages, each starting with a section-header in the upper third
        pages = [
            _page(1, [_block("p1_b001", "section-header", [100, 50, 900, 150], "First Article"),
                      _block("p1_b002", "text", [100, 200, 900, 1400], "Body text...")]),
            _page(2, [_block("p2_b001", "text", [100, 50, 900, 1400], "Continuation...")]),
            _page(3, [_block("p3_b001", "section-header", [100, 50, 900, 150], "Second Article"),
                      _block("p3_b002", "text", [100, 200, 900, 1400], "Body...")]),
        ]
        articles, toc = detect_articles(pages, _cfg())
        assert toc is None  # No TOC blocks, heuristic fired
        # 2 articles (page 1-2 and 3)
        real = [a for a in articles if a["title"] != "Frontmatter"]
        assert len(real) == 2
        assert real[0]["title"] == "First Article"
        assert real[0]["page_first"] == 1
        assert real[0]["page_last"] == 2
        assert real[1]["title"] == "Second Article"
        assert real[1]["page_first"] == 3

    def test_no_headers_makes_single_article(self):
        pages = [
            _page(1, [_block("p1_b001", "text", [100, 50, 900, 1400], "Only text...")]),
        ]
        articles, _ = detect_articles(pages, _cfg())
        # Either a single article (or Frontmatter + 1 article), but always at least one block
        assert len(articles) >= 1


class TestTocDriven:
    def test_uses_toc_when_present(self):
        # Page 1: TOC block. Pages 9 and 43: article headers.
        # Offset: pdf_page = printed_page (both start at 1 in this synthetic
        # example since we'll provide an explicit offset = 0).
        toc_text = (
            "INHALT\n"
            "AUFSÄTZE"
            "Smith, First Article ...... 9"
            "Jones, Second Article ...... 43"
        )
        pages = [
            _page(1, [_block("p1_b001", "table-of-contents",
                             [100, 100, 900, 1400], toc_text)]),
            _page(9, [_block("p9_b001", "section-header",
                             [100, 50, 900, 150], "First Article")]),
            _page(43, [_block("p43_b001", "section-header",
                              [100, 50, 900, 150], "Second Article")]),
        ]
        cfg = _cfg(offset=0)
        articles, toc = detect_articles(pages, cfg)
        assert toc is not None
        assert len(toc.entries) == 2
        real = [a for a in articles if a["title"] != "Frontmatter"]
        assert len(real) == 2
        assert real[0]["title"] == "First Article"
        assert real[0]["page_first"] == 9
        assert real[0]["author"] == "Smith"
        # Second article inherits its section from the TOC
        assert real[1]["section"] in ("Aufsätze", "AUFSÄTZE")

    def test_unparseable_toc_falls_back_to_heuristic(self):
        # TOC block with no parseable pages → falls back to heuristic
        pages = [
            _page(1, [_block("p1_b001", "table-of-contents",
                             [100, 100, 900, 1400], "Just random text")]),
            _page(2, [_block("p2_b001", "section-header",
                             [100, 50, 900, 150], "Article One")]),
        ]
        articles, _ = detect_articles(pages, _cfg())
        # Should still produce at least one article via the heuristic
        real = [a for a in articles if a["title"] != "Frontmatter"]
        assert len(real) >= 1


class TestPageOffsetInference:
    def test_constant_offset_detected(self):
        # PDF page 5 has printed "1", page 6 has "2", page 7 has "3"
        # → offset = pdf - printed = 4
        pages = [
            _page(5, [_block("p5_b001", "page-header", [10, 10, 50, 30], "1")]),
            _page(6, [_block("p6_b001", "page-header", [10, 10, 50, 30], "2")]),
            _page(7, [_block("p7_b001", "page-header", [10, 10, 50, 30], "3")]),
            _page(8, [_block("p8_b001", "page-header", [10, 10, 50, 30], "4")]),
        ]
        offset = infer_printed_page_offset(pages)
        assert offset == 4

    def test_too_few_samples_returns_none(self):
        pages = [
            _page(5, [_block("p5_b001", "page-header", [10, 10, 50, 30], "1")]),
        ]
        offset = infer_printed_page_offset(pages)
        assert offset is None
