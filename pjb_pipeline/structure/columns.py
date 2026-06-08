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

import bisect
from typing import List, Optional, Tuple


# Block types that should NEVER be confined to a single column — they
# always span the full page width regardless of geometry.
#
# Note: ``section-header`` is intentionally NOT in this set, even though
# Chandra often emits section headers near the top of a page that DO
# span both columns. Many narrow section headers live entirely inside
# one column ("Die Bedeutung der Fahnentiere — Kalender, Götter,
# Regimentsgeschichte(n)" on p20 of vol 52 is the canonical example),
# and unconditionally treating them as spanning makes ``reading_order``
# split the page into bands at the wrong y, which scrambles which
# column-2 block follows which column-1 block. The width check in
# ``assign_columns`` (block wider than 60 % of the page → ``_column =
# None``) still catches the genuinely-spanning article titles, so we
# defer the spanning vs in-column decision to geometry instead of type.
_SPANNING_TYPES = {
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
    min_gap_ratio: float = 0.06,
) -> List[Tuple[int, int]]:
    """Detect column ranges on a page.

    Returns a list of ``(x_start, x_end)`` pairs in pixel coordinates, or an
    empty list if the page is single-column (or has too few blocks to tell).

    ``width_threshold`` is the maximum fraction of page width a block can
    occupy and still count as "in a column" (anything wider is spanning).

    ``min_gap_ratio`` controls how clear the gutter between the two columns
    must be (as a fraction of page width) before we trust the detection.
    Pages whose body blocks are evenly distributed across the page width
    won't pass this and fall back to single-column rendering.

    The midline between the two columns is determined dynamically by
    finding the *largest gap* in the sorted block-centre positions, rather
    than naively assuming ``W/2``. This makes the detector robust to pages
    where the columns are offset, asymmetric, or where one column is
    physically narrower than the other.
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

    # Find the natural gutter between the two columns by looking at the
    # largest gap between sorted block-centre positions. Fall back to W/2
    # if no obvious gap exists.
    centres = sorted((b["bbox"][0] + b["bbox"][2]) / 2 for b in candidates)
    best_gap_size = 0.0
    best_gap_mid = W / 2
    for i in range(1, len(centres)):
        gap = centres[i] - centres[i - 1]
        if gap > best_gap_size:
            best_gap_size = gap
            best_gap_mid = (centres[i] + centres[i - 1]) / 2

    # Need a clear gutter (in page-width terms) to call this a 2-column
    # page. Loosely-laid-out single-column pages can have a small gap by
    # accident and shouldn't be promoted.
    if best_gap_size < W * min_gap_ratio:
        return []

    midline = best_gap_mid
    left = [b for b in candidates if ((b["bbox"][0] + b["bbox"][2]) / 2) < midline]
    right = [b for b in candidates if ((b["bbox"][0] + b["bbox"][2]) / 2) >= midline]

    if len(left) < min_blocks_per_column or len(right) < min_blocks_per_column:
        return []

    # Use the actual edges of the detected groups to give the columns
    # their real bounds (so the rendered columns hug the content).
    # Clamp each side at the detected gutter midline so stray blocks
    # (an author byline at the top of the page, a centred caption,
    # anything narrow that's not truly in one column) can't drag a
    # column's edge across the gutter. Without this, a single centred
    # block above the body — extremely common on the first page of an
    # article — trips the overlap check and disables column detection
    # for the whole page, sending everything back to plain y-sorting.
    mid = int(midline)
    left_start  = min(b["bbox"][0] for b in left)
    left_end    = min(max(b["bbox"][2] for b in left), mid)
    right_start = max(min(b["bbox"][0] for b in right), mid)
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

        # A narrow block that nonetheless straddles both detected columns
        # is still spanning. We check by horizontal overlap: if the block
        # overlaps significantly with each column's range (more than 30%
        # of the block's own width in each), call it spanning. This
        # catches things like wide figures or pull-quotes that sit between
        # the columns.
        if len(columns) == 2:
            (l_start, l_end), (r_start, r_end) = columns
            block_width = max(1, x2 - x1)
            l_ov = max(0, min(x2, l_end) - max(x1, l_start))
            r_ov = max(0, min(x2, r_end) - max(x1, r_start))
            if l_ov >= block_width * 0.3 and r_ov >= block_width * 0.3:
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

    Chandra emits blocks in a *mostly* reading-ordered sequence, but on
    multi-column pages it is not reliable: it sometimes reads part of the
    left column, jumps to the right column (and its footnotes), then
    returns to finish the left column. So a pure "trust Chandra's order"
    approach corrupts those pages (a sentence ending in "verlie-" at the
    bottom-left would be followed by its other half "henen…" at the
    top-right only if the two halves happen to be adjacent in Chandra's
    emission — which they are not). We therefore re-derive the order
    geometrically for multi-column pages.

    The rule is a single sort on the key ``(band, phase, column, y)``:

    * **band** — how many full-width *spanning* blocks (page header/footer,
      title, TOC) sit above this block. Spanning blocks slice the page
      into horizontal bands; within a band you read column-by-column.
    * **phase / column** — inside a band, columnar blocks come first
      (phase 0, ordered by ``column`` index 0, 1, … then ``y``), and a
      spanning block comes last (phase 1) because it *closes* its band:
      a page header sits above the body that follows it, a page footer
      sits below the body that precedes it, and both fall out correctly
      from "spanning block ends the band whose content is above it."
    * **y** — top edge, so blocks within one column read top-to-bottom.

    This is equivalent to the older explicit band-walk but expressed as
    one comparison key, which is easier to reason about and impossible to
    get out of sync between the "gather the band" and "emit the band"
    halves of the previous implementation.

    For a single-column page (or any page where column detection bailed),
    we trust Chandra's own emission order rather than re-sorting by ``y``.
    On a genuine single-column page Chandra already reads top-to-bottom,
    so this is equivalent; on a two-column page where detection failed it
    degrades to Chandra's mostly-correct order instead of a naive y-sort
    that would interleave the columns line by line.
    """
    if not columns:
        return list(blocks)

    spanning_ys = sorted(
        b["bbox"][1] for b in blocks if b.get("_column") is None
    )

    def sort_key(b: dict):
        y = b["bbox"][1]
        col = b.get("_column")
        # ``band`` = number of spanning blocks whose top edge is above this
        # block (bisect_left, so a spanning block does not count itself).
        band = bisect.bisect_left(spanning_ys, y)
        if col is None:
            # Spanning block closes its band: it comes after that band's
            # columnar content (phase 1) and before the next band.
            return (band, 1, 0, y)
        # Columnar block: phase 0, ordered by column index then y.
        return (band, 0, col, y)

    return sorted(blocks, key=sort_key)


def band_layout(
    blocks: List[dict],
    columns: List[Tuple[int, int]],
) -> List[Tuple[str, List[dict]]]:
    """Group blocks into alternating bands for layout rendering.

    Returns a list of ``(kind, blocks)`` tuples where ``kind`` is either
    ``"columns"`` (a band of multi-column content; ``blocks`` carry the
    ``_column`` annotation) or ``"spanning"`` (a single full-width block).

    The output is what an HTML renderer needs to emit a faithful
    reconstruction of the original layout — spanning blocks slot in at
    their natural y position instead of all being grouped at the top.

    For a single-column (or columns-less) page, returns a single
    ``("columns", blocks)`` band with the blocks sorted top-to-bottom.
    """
    if not columns:
        return [("columns", sorted(blocks, key=lambda b: b["bbox"][1]))]

    spanning = sorted(
        (b for b in blocks if b.get("_column") is None),
        key=lambda b: b["bbox"][1],
    )
    columnar = sorted(
        (b for b in blocks if b.get("_column") is not None),
        key=lambda b: b["bbox"][1],
    )

    out: List[Tuple[str, List[dict]]] = []
    col_cursor = 0
    for spn in spanning:
        spn_y = spn["bbox"][1]
        band: List[dict] = []
        while (
            col_cursor < len(columnar)
            and columnar[col_cursor]["bbox"][1] < spn_y
        ):
            band.append(columnar[col_cursor])
            col_cursor += 1
        if band:
            out.append(("columns", band))
        out.append(("spanning", [spn]))

    trailing = columnar[col_cursor:]
    if trailing:
        out.append(("columns", trailing))

    return out
