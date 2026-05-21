"""Column-structure detection for page layout.

Many Passauer Jahrbücher pages are typeset in two columns. To render the
transcription side of the facsimile view in the same shape as the original,
we need to know which column each block belongs to.

Heuristic, fast, no dependencies:

* Look at the horizontal centre of every body block on the page.
* If a comfortable majority sit clearly in the left half and another clear
  group sit in the right half, call the page two-column.
* Anything that spans most of the page width (section headers, figures,
  tables wider than one column, the TOC, page headers/footers) is marked
  as a *spanning* block — it gets rendered across both columns.

The output is a list of column dicts and a per-block ``_column`` assignment.
Downstream renderers can either flow blocks into ``<div class="col">``s or
just respect the assignment for ordering.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


# Block types that should NEVER be confined to a single column — they
# always span the full page width.
_SPANNING_TYPES = {
    "section-header",
    "page-header",
    "page-footer",
    "table-of-contents",
    "title",
}


def detect_columns(
    page: dict,
    *,
    min_blocks_per_column: int = 2,
    width_threshold: float = 0.6,
) -> List[Tuple[int, int]]:
    """Detect column ranges on a page.

    Returns a list of ``(x_start, x_end)`` pairs in pixel coordinates, or an
    empty list if the page is single-column (or has too few blocks to tell).

    ``width_threshold`` is the maximum fraction of page width a block can
    occupy and still count as "in a column" (anything wider is spanning).
    """
    blocks = page.get("blocks", [])
    if not blocks:
        return []

    W = page.get("image_width", 0)
    if W <= 0:
        return []

    # Candidate column-resident blocks: things narrower than the threshold
    # that aren't already known to span.
    candidates = []
    for b in blocks:
        if b.get("type") in _SPANNING_TYPES:
            continue
        x1, _y1, x2, _y2 = b["bbox"][:4]
        if (x2 - x1) > W * width_threshold:
            continue
        candidates.append(b)

    if len(candidates) < min_blocks_per_column * 2:
        return []

    # Group by centre x position into left vs right half.
    midline = W / 2
    left = [b for b in candidates if ((b["bbox"][0] + b["bbox"][2]) / 2) < midline]
    right = [b for b in candidates if ((b["bbox"][0] + b["bbox"][2]) / 2) >= midline]

    if len(left) < min_blocks_per_column or len(right) < min_blocks_per_column:
        return []

    # Use the actual edges of the detected groups to give the columns
    # their real bounds (so the rendered columns hug the content).
    left_start  = min(b["bbox"][0] for b in left)
    left_end    = max(b["bbox"][2] for b in left)
    right_start = min(b["bbox"][0] for b in right)
    right_end   = max(b["bbox"][2] for b in right)
    return [(int(left_start), int(left_end)), (int(right_start), int(right_end))]


def assign_columns(page: dict, columns: List[Tuple[int, int]]) -> List[dict]:
    """Annotate each block on ``page`` with a ``_column`` field.

    Values:
        ``None`` — spanning block (renders across both columns)
        ``0``    — first (left) column
        ``1``    — second (right) column

    The returned list is a copy of ``page['blocks']``; nothing is mutated
    in place.
    """
    W = page.get("image_width", 0)
    if not columns or W <= 0:
        return [{**b, "_column": None} for b in page.get("blocks", [])]

    out = []
    for b in page.get("blocks", []):
        # Spanning types always span
        if b.get("type") in _SPANNING_TYPES:
            out.append({**b, "_column": None})
            continue

        x1, _y1, x2, _y2 = b["bbox"][:4]
        # Wide blocks span
        if (x2 - x1) > W * 0.6:
            out.append({**b, "_column": None})
            continue

        # Otherwise assign by centre-x to the nearest column
        cx = (x1 + x2) / 2
        # Distance from centre-x to each column's centre
        best_col = None
        best_dist = float("inf")
        for i, (cs, ce) in enumerate(columns):
            ccx = (cs + ce) / 2
            d = abs(cx - ccx)
            if d < best_dist:
                best_dist = d
                best_col = i
        out.append({**b, "_column": best_col})
    return out


def reading_order(blocks: List[dict], columns: List[Tuple[int, int]]) -> List[dict]:
    """Sort blocks into natural reading order.

    For a two-column page::

        spanning blocks (in y order) at the top of their "section",
        then column 0 top-to-bottom,
        then column 1 top-to-bottom

    For a single-column page::

        plain top-to-bottom

    We don't try to interleave a spanning header *between* two paragraphs in
    the same column — that would require finding the y-level where each
    column "owns" the page, which is a deeper problem. Spanning blocks come
    first; column blocks follow. Good enough for the digital edition.
    """
    if not columns:
        return sorted(blocks, key=lambda b: b["bbox"][1])

    spanning = [b for b in blocks if b.get("_column") is None]
    col_blocks: List[List[dict]] = [[] for _ in columns]
    for b in blocks:
        col = b.get("_column")
        if col is None:
            continue
        if 0 <= col < len(columns):
            col_blocks[col].append(b)

    # Sort each column by y
    for col in col_blocks:
        col.sort(key=lambda b: b["bbox"][1])
    spanning.sort(key=lambda b: b["bbox"][1])

    # Spanning first, then columns left-to-right
    out = list(spanning)
    for col in col_blocks:
        out.extend(col)
    return out
