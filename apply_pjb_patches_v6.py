#!/usr/bin/env python3
"""Fix article first-page byline/title misplacement (vol-56 p84/p104 class).

Diagnosis (from the raw block dump of vol-56 pp.84/104):
On the first page of an article a centred author byline and the article
title sit *above* the two-column body. Being centred, they straddle the
column gutter. The old code (a) let them corrupt gutter detection — on p84
the detected gutter was x=534 instead of the true ~693 — and (b) then
assigned them by centre-x to the right column, so they read AFTER an entire
column of body text. The article therefore rendered as: two body
paragraphs, then the author name and title, then the rest of the body.

Fix: recognise page *preamble* by position. ``_masthead_top`` finds the
highest y at which two blocks sit side by side (where the columnar body
begins); any block lying entirely above that line is full-width masthead.
Such blocks are (a) excluded from gutter detection, so the gutter is found
from the body alone, and (b) marked spanning, so they read first.

This is deliberately position-based, not type-based, so a section header
*inside* the columns (a mid-page subhead — the v3 case) stays in its
column. Verified: vol-52 pp.18/20/26 reading order is unchanged.

Idempotent. Adds regression tests in tests/test_columns.py.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(".").resolve()


def in_repo() -> bool:
    return (REPO / "pjb_pipeline" / "structure" / "columns.py").exists()


def backup_once(path: pathlib.Path, tag: str = "bak7") -> None:
    bak = path.with_suffix(f"{path.suffix}.{tag}")
    if not bak.exists():
        shutil.copy2(path, bak)


def replace_once(path: str, marker: str, old: str, new: str, what: str) -> None:
    f = REPO / path
    s = f.read_text(encoding="utf-8")
    if marker in s:
        print(f"  {path}: skip ({what} already applied)")
        return
    if old not in s:
        sys.exit(f"\n  ERROR: cannot find anchor for {what} in {path}.\n"
                 f"  Has the file drifted from the snapshot?\n")
    backup_once(f)
    f.write_text(s.replace(old, new, 1), encoding="utf-8")
    print(f"  {path}: applied ({what})")


def append_once(path: str, marker: str, addition: str, what: str) -> None:
    f = REPO / path
    s = f.read_text(encoding="utf-8")
    if marker in s:
        print(f"  {path}: skip ({what} already appended)")
        return
    backup_once(f)
    sep = "" if s.endswith("\n\n") else ("\n" if s.endswith("\n") else "\n\n")
    f.write_text(s + sep + addition, encoding="utf-8")
    print(f"  {path}: appended ({what})")


if not in_repo():
    sys.exit("Run this from the Passauer_Jahrbuecher_Pipeline root.")

print(f"Applying patches in {REPO}\n")

# ---------------------------------------------------------------------------
# columns.py — add the _masthead_top helper
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/structure/columns.py",
    marker="def _masthead_top(",
    old='''_SPANNING_TYPES = {
    "page-header",
    "page-footer",
    "table-of-contents",
    "title",
}


def detect_columns(''',
    new='''_SPANNING_TYPES = {
    "page-header",
    "page-footer",
    "table-of-contents",
    "title",
}


def _masthead_top(blocks: List[dict]) -> Optional[float]:
    """Y at which the multi-column body begins, or ``None`` if there is no
    side-by-side region (single-column page).

    Found as the highest point where two non-spanning blocks sit *side by
    side* — one entirely left of the other, overlapping vertically. That is
    where the columnar body starts. Anything whose whole height is above
    this line is page *preamble*: a centred author byline or article title
    sitting above the columns on the first page of an article. Such blocks
    are typically centred across the gutter, which both corrupts gutter
    detection and gets them mis-assigned to one column (so they read after
    a whole column of body text). Detecting them by position lets us span
    them instead.

    This is deliberately position-based, not type-based: a section header
    *inside* the columnar region (a mid-page subhead) is below this line
    and is left in its column, preserving the v3 behaviour.
    """
    top: Optional[float] = None
    n = len(blocks)
    for i in range(n):
        a = blocks[i]
        if a.get("type") in _SPANNING_TYPES:
            continue
        ax0, ay0, ax1, ay1 = a["bbox"][:4]
        for j in range(i + 1, n):
            b = blocks[j]
            if b.get("type") in _SPANNING_TYPES:
                continue
            bx0, by0, bx1, by1 = b["bbox"][:4]
            if min(ay1, by1) <= max(ay0, by0):   # no vertical overlap
                continue
            if ax1 <= bx0 or bx1 <= ax0:          # side by side
                pair_top = min(ay0, by0)
                if top is None or pair_top < top:
                    top = pair_top
    return top


def detect_columns(''',
    what="add _masthead_top helper",
)

# ---------------------------------------------------------------------------
# columns.py — exclude masthead from detect_columns candidates
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/structure/columns.py",
    marker="aren't page preamble",
    old='''    # Candidate column-resident blocks: things narrower than the threshold
    # that aren't already known to span.
    candidates = []
    for b in blocks:
        if b.get("type") in _SPANNING_TYPES:
            continue
        x1, _y1, x2, _y2 = b["bbox"][:4]
        if (x2 - x1) > W * width_threshold:
            continue
        candidates.append(b)''',
    new='''    # Candidate column-resident blocks: things narrower than the threshold
    # that aren't already known to span, and that aren't page preamble
    # (a centred byline/title above the body would otherwise straddle the
    # gutter and drag the detected midline off the true column boundary).
    split_y = _masthead_top(blocks)
    candidates = []
    for b in blocks:
        if b.get("type") in _SPANNING_TYPES:
            continue
        x1, _y1, x2, _y2 = b["bbox"][:4]
        if (x2 - x1) > W * width_threshold:
            continue
        if split_y is not None and b["bbox"][3] <= split_y:
            continue  # masthead preamble — not part of a column
        candidates.append(b)''',
    what="exclude masthead from gutter detection",
)

# ---------------------------------------------------------------------------
# columns.py — mark masthead spanning in assign_columns
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/structure/columns.py",
    marker="Masthead preamble (entirely above",
    old='''    W = page.get("image_width", 0)
    if not columns or W <= 0:
        return [{**b, "_column": None} for b in page.get("blocks", [])]

    out = []
    for b in page.get("blocks", []):
        # Spanning types always span
        if b.get("type") in _SPANNING_TYPES:
            out.append({**b, "_column": None})
            continue

        x1, _y1, x2, _y2 = b["bbox"][:4]''',
    new='''    W = page.get("image_width", 0)
    if not columns or W <= 0:
        return [{**b, "_column": None} for b in page.get("blocks", [])]

    # Page preamble (centred byline/title above the columnar body) spans the
    # full width and must read before either column, not be folded into one.
    split_y = _masthead_top(page.get("blocks", []))

    out = []
    for b in page.get("blocks", []):
        # Spanning types always span
        if b.get("type") in _SPANNING_TYPES:
            out.append({**b, "_column": None})
            continue

        # Masthead preamble (entirely above where the two columns begin)
        if split_y is not None and b["bbox"][3] <= split_y:
            out.append({**b, "_column": None})
            continue

        x1, _y1, x2, _y2 = b["bbox"][:4]''',
    what="mark masthead spanning in assign_columns",
)

# ---------------------------------------------------------------------------
# tests/test_columns.py — regression tests
# ---------------------------------------------------------------------------
_TESTS = '''

# ---------------------------------------------------------------------------
# Masthead preamble: byline + title above the body must span and read first
# ---------------------------------------------------------------------------

class TestMastheadPreamble:
    """Regression for the vol-56 p84/p104 pattern.

    On an article's first page a centred author byline and title sit above
    the two-column body. Being centred, they straddle the gutter; the old
    code let them corrupt gutter detection and then assigned them to the
    right column, so they read AFTER a whole column of body text. They must
    instead be recognised as full-width preamble (because they lie entirely
    above where the two columns begin) and read first.
    """

    def _first_page(self):
        def b(bid, typ, x0, y0, x1, y1):
            return {"id": bid, "type": typ, "bbox": [x0, y0, x1, y1],
                    "text": "", "html": ""}
        return {"image_width": 1470, "image_height": 2198, "blocks": [
            b("b001", "text",           480, 257,  906, 292),
            b("b002", "section-header", 504, 340,  883, 391),
            b("b003", "text",            66, 586,  685, 1261),
            b("b004", "text",            66, 1263, 685, 1938),
            b("b005", "text",           701, 589, 1331, 1063),
            b("b006", "text",           701, 1063, 1330, 1184),
            b("b007", "footnote",       701, 1327, 1330, 1386),
            b("b008", "page-footer",    945, 1978, 1330, 2019),
        ]}

    def test_byline_and_title_span_and_read_first(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns, reading_order,
        )
        page = self._first_page()
        cols = detect_columns(page)
        assert len(cols) == 2
        ann = assign_columns(page, cols)
        bycol = {b["id"]: b.get("_column") for b in ann}
        assert bycol["b001"] is None, "byline must span, not be in a column"
        assert bycol["b002"] is None, "title must span, not be in a column"
        assert bycol["b003"] == 0 and bycol["b005"] == 1
        order = [b["id"] for b in reading_order(ann, cols)]
        assert order[0] == "b001" and order[1] == "b002", \\
            f"byline+title must read first, got {order[:3]}"
        assert order.index("b003") < order.index("b005")

    def test_gutter_not_dragged_by_centred_preamble(self):
        from pjb_pipeline.structure.columns import detect_columns
        cols = detect_columns(self._first_page())
        (l0, l1), (r0, r1) = cols
        assert l1 < 700 and r0 > 690, f"gutter dragged off the body: {cols}"


class TestMidColumnSubheadStaysInColumn:
    """A section header *inside* the columnar region must remain
    column-resident — preserving the v3 fix. Distinguished from a masthead
    purely by vertical position."""

    def test_subhead_below_body_top_is_not_masthead(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns,
        )
        def b(bid, typ, x0, y0, x1, y1):
            return {"id": bid, "type": typ, "bbox": [x0, y0, x1, y1],
                    "text": "", "html": ""}
        page = {"image_width": 1459, "image_height": 2192, "blocks": [
            b("b001", "text",            80, 267,  690, 1100),
            b("b002", "section-header",  80, 1139, 690, 1223),
            b("b003", "text",            80, 1220, 690, 1939),
            b("b004", "text",           770, 267, 1380, 705),
            b("b005", "text",           770, 705, 1380, 986),
            b("b006", "footnote",       770, 1041, 1380, 1300),
            b("b007", "page-footer",    600, 1990, 860, 2023),
        ]}
        cols = detect_columns(page)
        ann = assign_columns(page, cols)
        bycol = {b["id"]: b.get("_column") for b in ann}
        assert bycol["b002"] == 0, \\
            f"mid-column subhead should stay in col 0, got {bycol['b002']}"
'''

append_once(
    "tests/test_columns.py",
    marker="class TestMastheadPreamble",
    addition=_TESTS,
    what="regression: masthead preamble + mid-column subhead",
)

# ---------------------------------------------------------------------------
# Run the test suite
# ---------------------------------------------------------------------------
print("\nAll patches applied. Running test suite...\n")
t0 = time.time()
proc = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "--no-header"],
    cwd=REPO, capture_output=True, text=True,
)
elapsed = time.time() - t0
print(proc.stdout.strip())
if proc.returncode != 0:
    print(proc.stderr.strip())
    sys.exit(f"\nTests failed after {elapsed:.1f}s. Backups are at *.bak7.")
print(f"\nTests passed in {elapsed:.1f}s. Backups of modified files are at *.bak7.")
