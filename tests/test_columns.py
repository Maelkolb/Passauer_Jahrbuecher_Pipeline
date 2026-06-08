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
    # Band layout: the section-header "a" is above all body blocks, the
    # page-footer "f" is below all of them. So the natural reading order
    # walks the body left column (b, d), then right column (c, e), with
    # the spanning header at the top and the spanning footer at the end.
    assert [b["id"] for b in ordered] == ["a", "b", "d", "c", "e", "f"]


def test_reading_order_interleaves_mid_page_spanning_block():
    # A section-header that appears in the middle of a 2-column page
    # should split the columns into a band above it and a band below it.
    page = {
        "image_width": 1400,
        "image_height": 2000,
        "blocks": [
            _block("top",      "text",           [100,  100,  650,  600]),
            _block("topR",     "text",           [750,  100, 1300,  600]),
            _block("midHdr",   "section-header", [100,  700, 1300,  800]),
            _block("bot",      "text",           [100,  900,  650, 1500]),
            _block("botR",     "text",           [750,  900, 1300, 1500]),
        ],
    }
    cols = detect_columns(page)
    assert len(cols) == 2
    annotated = assign_columns(page, cols)
    ordered = reading_order(annotated, cols)
    # Within the upper band: left column ("top") then right column ("topR").
    # Then the spanning header. Then the lower band: bot, botR.
    assert [b["id"] for b in ordered] == ["top", "topR", "midHdr", "bot", "botR"]


def test_band_layout_groups_spanning_at_mid_page():
    from pjb_pipeline.structure.columns import band_layout
    page = {
        "image_width": 1400,
        "image_height": 2000,
        "blocks": [
            _block("top",    "text",           [100, 100,  650, 600]),
            _block("topR",   "text",           [750, 100, 1300, 600]),
            _block("midHdr", "section-header", [100, 700, 1300, 800]),
            _block("bot",    "text",           [100, 900,  650, 1500]),
            _block("botR",   "text",           [750, 900, 1300, 1500]),
        ],
    }
    cols = detect_columns(page)
    annotated = assign_columns(page, cols)
    bands = band_layout(annotated, cols)
    # Expect: columns band (upper), spanning band (midHdr), columns band (lower)
    kinds = [k for k, _ in bands]
    assert kinds == ["columns", "spanning", "columns"]
    assert {b["id"] for b in bands[0][1]} == {"top", "topR"}
    assert [b["id"] for b in bands[1][1]] == ["midHdr"]
    assert {b["id"] for b in bands[2][1]} == {"bot", "botR"}


def test_reading_order_single_column():
    page = _single_column_page()
    cols = detect_columns(page)
    annotated = assign_columns(page, cols)
    ordered = reading_order(annotated, cols)
    # Single column: just sorted by y
    assert [b["id"] for b in ordered] == ["a", "b", "c"]



# ---------------------------------------------------------------------------
# Narrow section-header within a column must not split the page into bands
# ---------------------------------------------------------------------------

class TestNarrowSectionHeaderIsColumnResident:
    """Regression for the page-20 scenario in vol 52.

    Chandra labels in-column subheadings as ``section-header``. Before
    the fix, ``_SPANNING_TYPES`` listed section-header unconditionally,
    so any such block forced ``assign_columns`` to set ``_column=None``
    and ``reading_order`` then treated it as a band separator splitting
    the page at its y-position. With a header midway down column 1,
    the band-above-the-header would emit col-1-top + col-2-top, then
    the header, then band-below — putting column-2's top blocks
    *between* column-1's continuation, which is wrong.

    With the fix, ``section-header`` is no longer in ``_SPANNING_TYPES``;
    only blocks that genuinely span the page (>60 % page width or
    straddling both columns) get ``_column=None``. A narrow in-column
    section-header is assigned to its actual column, and reading order
    proceeds column-by-column without artificial band splits.
    """

    def _vol52_p20_page(self):
        # Synthesises page 20 of vol 52: two-column body with a narrow
        # ``section-header`` block midway down column 1.
        return {
            "image_width": 1400,
            "image_height": 2200,
            "blocks": [
                _block("p20_b001", "text",            [100, 200, 650, 400]),    # col 1 top
                _block("p20_b002", "text",            [100, 420, 650, 870]),    # col 1 mid
                _block("p20_b003", "section-header",  [100, 900, 600, 940]),    # narrow in-col-1
                _block("p20_b004", "text",            [100, 970, 650, 1500]),   # col 1 bottom
                _block("p20_b005", "text",            [750, 200, 1300, 700]),   # col 2 top
                _block("p20_b006", "text",            [750, 720, 1300, 1500]),  # col 2 bottom
            ],
        }

    def test_narrow_section_header_is_assigned_to_a_column(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns,
        )
        page = self._vol52_p20_page()
        cols = detect_columns(page)
        assert len(cols) == 2, "test page should detect as 2-column"
        assigned = assign_columns(page, cols)
        by_id = {b["id"]: b for b in assigned}
        sh = by_id["p20_b003"]
        assert sh["_column"] is not None, (
            "narrow section-header inside column 1 should be column-"
            "resident, not spanning"
        )
        assert sh["_column"] == 0

    def test_reading_order_does_not_split_around_narrow_header(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns, reading_order,
        )
        page = self._vol52_p20_page()
        cols = detect_columns(page)
        assigned = assign_columns(page, cols)
        ordered = [b["id"] for b in reading_order(assigned, cols)]
        # column 0 reads top-to-bottom first, then column 1
        i_b001 = ordered.index("p20_b001")
        i_b002 = ordered.index("p20_b002")
        i_b003 = ordered.index("p20_b003")
        i_b004 = ordered.index("p20_b004")
        i_b005 = ordered.index("p20_b005")
        i_b006 = ordered.index("p20_b006")
        # column 0 in y order
        assert i_b001 < i_b002 < i_b003 < i_b004
        # column 1 in y order, contiguous (no col-0 block between)
        assert i_b005 < i_b006
        # all of column 0 before any of column 1
        assert max(i_b001, i_b002, i_b003, i_b004) < min(i_b005, i_b006)

    def test_wide_section_header_still_spans(self):
        # Article-title-style section header that genuinely spans both
        # columns (width > 60 % of page) must still be detected as
        # spanning by the geometric width check.
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns,
        )
        page = {
            "image_width": 1400,
            "image_height": 2200,
            "blocks": [
                _block("title", "section-header", [150, 100, 1250, 180]),
                _block("a", "text", [100, 250, 650, 800]),
                _block("b", "text", [750, 250, 1300, 800]),
                _block("c", "text", [100, 820, 650, 1500]),
                _block("d", "text", [750, 820, 1300, 1500]),
            ],
        }
        cols = detect_columns(page)
        assigned = assign_columns(page, cols)
        title = next(b for b in assigned if b["id"] == "title")
        assert title["_column"] is None, (
            "wide section-header (>60 % page width) must still be"
            " treated as spanning"
        )



# ---------------------------------------------------------------------------
# reading_order: column-major linearization regardless of Chandra's emission
# ---------------------------------------------------------------------------

class TestReadingOrderColumnMajor:
    """Regression for the vol-52 p18 pattern.

    Chandra does not reliably emit blocks in reading order on multi-column
    pages: on p18 it read the top of the LEFT column, jumped to the RIGHT
    column (continuation + all footnotes), then RETURNED to finish the
    bottom of the LEFT column. The raw emission order therefore puts a
    right-column continuation ("henen…", the second half of "verlie-")
    immediately after the left-column intro, which is wrong. reading_order
    must re-derive a column-major order: the entire LEFT column top to
    bottom, then the entire RIGHT column, then the page footer — no matter
    what order Chandra emitted the blocks in.
    """

    def _p18(self):
        L, R = (80, 690), (770, 1390)
        def b(bid, typ, y0, y1, col):
            x0, x1 = (L if col == "L" else R)
            return {"id": bid, "type": typ, "bbox": [x0, y0, x1, y1],
                    "text": "", "html": ""}
        return {"image_width": 1459, "image_height": 2192, "blocks": [
            b("b001", "text", 247, 284, "L"),
            b("b002", "section-header", 333, 379, "L"),
            b("b003", "text", 418, 506, "L"),
            b("b004", "image", 587, 826, "L"),
            b("b005", "text", 583, 863, "L"),
            b("b006", "text", 583, 745, "R"),
            b("b007", "footnote", 800, 861, "R"),
            b("b015", "footnote", 1457, 1937, "R"),
            b("b016", "text", 1376, 1457, "L"),
            b("b017", "text", 1457, 1937, "L"),
            b("b018", "page-footer", 1974, 2016, "L"),
        ]}

    def test_left_column_reads_fully_before_right(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns, reading_order,
        )
        page = self._p18()
        cols = detect_columns(page)
        assert len(cols) == 2
        order = [b["id"] for b in reading_order(assign_columns(page, cols), cols)]
        i16 = order.index("b016")
        i17 = order.index("b017")
        i06 = order.index("b006")
        assert i16 < i06, "left-column b016 must precede right-column b006"
        assert i17 < i06, "left-column b017 must precede right-column b006"
        assert order[-1] == "b018"
        assert order.index("b005") < i16 < i17

    def test_footer_is_last_not_first(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns, reading_order,
        )
        page = self._p18()
        cols = detect_columns(page)
        order = [b["id"] for b in reading_order(assign_columns(page, cols), cols)]
        assert order[-1] == "b018"
        assert order[0] == "b001"


class TestReadingOrderFallbackTrustsChandra:
    """When column detection bails (single-column page, or a page too
    sparse to split), reading_order returns blocks in Chandra's own
    emission order rather than re-sorting by y. On a genuine
    single-column page this is identical to a y-sort; the point is that a
    two-column page where detection failed degrades to Chandra's
    mostly-correct order instead of a y-sort that interleaves columns."""

    def test_no_columns_preserves_input_order(self):
        from pjb_pipeline.structure.columns import reading_order
        blocks = [
            {"id": "a", "type": "text", "bbox": [100, 500, 600, 600]},
            {"id": "b", "type": "text", "bbox": [100, 100, 600, 200]},
            {"id": "c", "type": "text", "bbox": [100, 300, 600, 400]},
        ]
        out = [b["id"] for b in reading_order(blocks, [])]
        assert out == ["a", "b", "c"]
