"""Tests for ``pjb_pipeline.structure.columns``."""

from pjb_pipeline.structure.columns import (
    detect_columns, assign_columns, reading_order,
)


def _block(bid, type_, bbox):
    return {"id": bid, "type": type_, "bbox": bbox, "text": "", "html": ""}


def _two_column_page():
    return {
        "image_width": 1400,
        "image_height": 2000,
        "blocks": [
            _block("a", "section-header", [100, 50, 1300, 120]),
            _block("b", "text",            [100, 200, 650, 800]),
            _block("c", "text",            [750, 200, 1300, 800]),
            _block("d", "text",            [100, 820, 650, 1500]),
            _block("e", "text",            [750, 820, 1300, 1500]),
            _block("f", "page-footer",     [680, 1900, 720, 1950]),
        ],
    }


def _single_column_page():
    return {
        "image_width": 1400,
        "image_height": 2000,
        "blocks": [
            _block("a", "section-header", [100, 50, 1300, 120]),
            _block("b", "text",            [100, 200, 1300, 800]),
            _block("c", "text",            [100, 820, 1300, 1500]),
        ],
    }


def test_detect_two_columns():
    page = _two_column_page()
    cols = detect_columns(page)
    assert len(cols) == 2
    # First column starts around x=100, ends around x=650
    assert cols[0][0] == 100
    assert cols[0][1] == 650
    assert cols[1][0] == 750
    assert cols[1][1] == 1300


def test_no_columns_on_full_width_page():
    page = _single_column_page()
    # Body blocks span the full page width — no columns should be detected
    assert detect_columns(page) == []


def test_too_few_blocks_no_columns():
    page = {
        "image_width": 1400, "image_height": 2000,
        "blocks": [_block("a", "text", [100, 100, 650, 200])],
    }
    assert detect_columns(page) == []


def test_assign_columns_routes_spanning_and_body():
    page = _two_column_page()
    cols = detect_columns(page)
    annotated = assign_columns(page, cols)
    by_id = {b["id"]: b for b in annotated}
    # Section header and footer span both columns
    assert by_id["a"]["_column"] is None
    assert by_id["f"]["_column"] is None
    # Body blocks go to left/right column based on centre-x
    assert by_id["b"]["_column"] == 0
    assert by_id["d"]["_column"] == 0
    assert by_id["c"]["_column"] == 1
    assert by_id["e"]["_column"] == 1


def test_reading_order_two_columns():
    page = _two_column_page()
    cols = detect_columns(page)
    annotated = assign_columns(page, cols)
    ordered = reading_order(annotated, cols)
    # Expected: spanning first by y (a, f), then left col by y (b, d), then right col by y (c, e)
    assert [b["id"] for b in ordered] == ["a", "f", "b", "d", "c", "e"]


def test_reading_order_single_column():
    page = _single_column_page()
    cols = detect_columns(page)
    annotated = assign_columns(page, cols)
    ordered = reading_order(annotated, cols)
    # Single column: just sorted by y
    assert [b["id"] for b in ordered] == ["a", "b", "c"]
