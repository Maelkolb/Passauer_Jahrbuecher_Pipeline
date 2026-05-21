"""Tests for ``pjb_pipeline.normalize``."""

import pytest

from pjb_pipeline.normalize import canonical_type, to_pixel_bbox, build_unified_page


class TestCanonicalType:
    def test_known_types_pass_through(self):
        assert canonical_type("text") == "text"
        assert canonical_type("section-header") == "section-header"
        assert canonical_type("footnote") == "footnote"
        assert canonical_type("table-of-contents") == "table-of-contents"

    def test_aliases_collapse_to_canonical(self):
        # Picture → figure (Chandra/Surya quirk)
        assert canonical_type("Picture") == "figure"
        # Header → section-header (so a TOC heading and an article heading
        # both produce a recognisable anchor)
        assert canonical_type("Title") == "section-header"
        # Equation variants
        assert canonical_type("formula") == "equation"
        assert canonical_type("equation-block") == "equation"

    def test_enum_string_form(self):
        # Older runs sometimes left "BlockType.PICTURE" in cached JSON.
        # canonical_type must tolerate that.
        assert canonical_type("BlockType.PICTURE") == "figure"
        assert canonical_type("BlockType.section_header") == "section-header"

    def test_unknown_falls_back_to_text(self):
        assert canonical_type("definitely-not-a-real-block-type") == "text"

    def test_none_is_text(self):
        assert canonical_type(None) == "text"


class TestToPixelBbox:
    def test_normalised_bbox(self):
        # Bbox in 0..1 range gets scaled
        assert to_pixel_bbox([0.0, 0.0, 1.0, 1.0], 100, 200) == [0, 0, 100, 200]
        assert to_pixel_bbox([0.1, 0.2, 0.5, 0.6], 100, 200) == [10, 40, 50, 120]

    def test_pixel_bbox_unchanged(self):
        # Bbox already in pixels (values > 1.5) passes through with clamping
        assert to_pixel_bbox([10, 20, 90, 180], 100, 200) == [10, 20, 90, 180]

    def test_clamps_to_image_bounds(self):
        # Out-of-bounds bbox gets clamped, not rejected
        assert to_pixel_bbox([-5, -10, 200, 300], 100, 200) == [0, 0, 100, 200]

    def test_empty_bbox_returns_full_page(self):
        assert to_pixel_bbox([], 100, 200) == [0, 0, 100, 200]
        assert to_pixel_bbox(None, 100, 200) == [0, 0, 100, 200]

    def test_swapped_coords_get_sorted(self):
        # x2 < x1 should be sorted so x1 <= x2
        assert to_pixel_bbox([90, 180, 10, 20], 100, 200) == [10, 20, 90, 180]


class TestBuildUnifiedPage:
    def test_unified_page_basic(self):
        raw = {
            "page_num": 5,
            "image_filename": "page_0005.png",
            "image_width": 1000,
            "image_height": 1500,
            "blocks": [
                {"id": "p5_b001", "type": "Paragraph",
                 "bbox": [0.1, 0.1, 0.5, 0.2], "text": "Hello"},
                {"id": "p5_b002", "type": "Picture",
                 "bbox": [0.1, 0.3, 0.9, 0.6], "text": ""},
            ],
            "markdown": "Hello",
        }
        u = build_unified_page(raw)
        assert u["page_num"] == 5
        assert len(u["blocks"]) == 2
        assert u["blocks"][0]["type"] == "text"
        assert u["blocks"][1]["type"] == "figure"   # Picture → figure
        # bbox got scaled to pixels
        assert u["blocks"][0]["bbox"][0] == 100   # 0.1 * 1000

    def test_falls_back_to_markdown_when_no_blocks(self):
        raw = {
            "page_num": 7,
            "image_filename": "page_0007.png",
            "image_width": 1000,
            "image_height": 1500,
            "blocks": [],
            "markdown": "Some recovered text",
        }
        u = build_unified_page(raw)
        assert len(u["blocks"]) == 1
        assert u["blocks"][0]["text"] == "Some recovered text"
        assert u["blocks"][0]["type"] == "text"

    def test_empty_page_with_no_markdown(self):
        raw = {
            "page_num": 9,
            "image_filename": "page_0009.png",
            "image_width": 1000,
            "image_height": 1500,
            "blocks": [],
            "markdown": "",
        }
        u = build_unified_page(raw)
        # No blocks, no markdown → empty page (not crashed)
        assert u["blocks"] == []
