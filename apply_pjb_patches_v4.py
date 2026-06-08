#!/usr/bin/env python3
"""Apply the reading-order simplification on top of v1 + v2 + v3.

Diagnosis (from the raw Chandra block dump of vol-52 pp.18/20/26):
Chandra emits blocks in a *mostly* reading-ordered sequence, but on
multi-column pages it is not reliable — on p18 it read the top of the
LEFT column, jumped to the RIGHT column (continuation + all footnotes),
then RETURNED to finish the bottom of the LEFT column. So trusting
Chandra's raw order corrupts those pages, and the geometric re-sort is
load-bearing. This patch keeps the column-major re-sort but:

  * replaces the ~40-line explicit band-walk in ``reading_order`` with a
    single composite-key sort ``(band, phase, column, y)`` that is
    impossible to get out of sync between its "gather" and "emit" halves;
  * changes the no-columns fallback from a naive y-sort to trusting
    Chandra's own emission order — identical on genuine single-column
    pages, but a graceful degradation (rather than a column-interleave)
    if column detection ever bails on a two-column page.

Also adds scripts/check_reading_order.py — an audit tool that scans a
processed volume and flags pages where the order changed and any broken
hyphenation joins, so the whole corpus can be spot-checked at once.

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


def backup_once(path: pathlib.Path, tag: str = "bak5") -> None:
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


def create_once(path: str, content: str, what: str) -> None:
    f = REPO / path
    if f.exists():
        print(f"  {path}: skip ({what} already exists)")
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    print(f"  {path}: created ({what})")


if not in_repo():
    sys.exit("Run this from the Passauer_Jahrbuecher_Pipeline root.")

print(f"Applying patches in {REPO}\n")

# ---------------------------------------------------------------------------
# columns.py — bisect import
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/structure/columns.py",
    marker="import bisect",
    old='''from __future__ import annotations

from typing import List, Optional, Tuple''',
    new='''from __future__ import annotations

import bisect
from typing import List, Optional, Tuple''',
    what="add bisect import",
)

# ---------------------------------------------------------------------------
# columns.py — replace band-walk reading_order with composite-key sort
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/structure/columns.py",
    marker="single sort on the key",
    old='''def reading_order(blocks: List[dict], columns: List[Tuple[int, int]]) -> List[dict]:
    """Sort blocks into natural reading order.

    For a two-column page we now use a *band* layout: spanning blocks
    (section headers, page header/footer, wide figures) act as horizontal
    separators that split the page vertically. Inside each band, blocks
    are read column-by-column, top-to-bottom. The reading sequence is::

        [column-0 of band 1, column-1 of band 1, spanning block 1,
         column-0 of band 2, column-1 of band 2, spanning block 2, \u2026]

    This preserves the vertical position of section headers (and other
    spanning regions) instead of forcing them all to the top of the page.

    For a single-column page::

        plain top-to-bottom
    """
    if not columns:
        return sorted(blocks, key=lambda b: b["bbox"][1])

    n_cols = len(columns)

    spanning = sorted(
        (b for b in blocks if b.get("_column") is None),
        key=lambda b: b["bbox"][1],
    )
    columnar = sorted(
        (b for b in blocks if b.get("_column") is not None),
        key=lambda b: b["bbox"][1],
    )

    out: List[dict] = []
    col_cursor = 0
    for spn in spanning:
        spn_y = spn["bbox"][1]
        # Collect all column blocks whose top edge is above this spanning
        # block \u2014 they belong to the band sitting *above* it.
        band: List[dict] = []
        while (
            col_cursor < len(columnar)
            and columnar[col_cursor]["bbox"][1] < spn_y
        ):
            band.append(columnar[col_cursor])
            col_cursor += 1
        # Emit the band column-by-column, then the spanning block itself.
        for i in range(n_cols):
            for b in band:
                if b.get("_column") == i:
                    out.append(b)
        out.append(spn)

    # Trailing column blocks below the last spanning block.
    trailing = columnar[col_cursor:]
    for i in range(n_cols):
        for b in trailing:
            if b.get("_column") == i:
                out.append(b)

    return out''',
    new='''def reading_order(blocks: List[dict], columns: List[Tuple[int, int]]) -> List[dict]:
    """Sort blocks into natural reading order.

    Chandra emits blocks in a *mostly* reading-ordered sequence, but on
    multi-column pages it is not reliable: it sometimes reads part of the
    left column, jumps to the right column (and its footnotes), then
    returns to finish the left column. So a pure "trust Chandra's order"
    approach corrupts those pages (a sentence ending in "verlie-" at the
    bottom-left would be followed by its other half "henen\u2026" at the
    top-right only if the two halves happen to be adjacent in Chandra's
    emission \u2014 which they are not). We therefore re-derive the order
    geometrically for multi-column pages.

    The rule is a single sort on the key ``(band, phase, column, y)``:

    * **band** \u2014 how many full-width *spanning* blocks (page header/footer,
      title, TOC) sit above this block. Spanning blocks slice the page
      into horizontal bands; within a band you read column-by-column.
    * **phase / column** \u2014 inside a band, columnar blocks come first
      (phase 0, ordered by ``column`` index 0, 1, \u2026 then ``y``), and a
      spanning block comes last (phase 1) because it *closes* its band:
      a page header sits above the body that follows it, a page footer
      sits below the body that precedes it, and both fall out correctly
      from "spanning block ends the band whose content is above it."
    * **y** \u2014 top edge, so blocks within one column read top-to-bottom.

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

    return sorted(blocks, key=sort_key)''',
    what="simplify reading_order to composite-key sort + Chandra fallback",
)

# ---------------------------------------------------------------------------
# tests/test_columns.py — regression tests
# ---------------------------------------------------------------------------
_TESTS = '''

# ---------------------------------------------------------------------------
# reading_order: column-major linearization regardless of Chandra's emission
# ---------------------------------------------------------------------------

class TestReadingOrderColumnMajor:
    """Regression for the vol-52 p18 pattern.

    Chandra does not reliably emit blocks in reading order on multi-column
    pages: on p18 it read the top of the LEFT column, jumped to the RIGHT
    column (continuation + all footnotes), then RETURNED to finish the
    bottom of the LEFT column. The raw emission order therefore puts a
    right-column continuation ("henen\u2026", the second half of "verlie-")
    immediately after the left-column intro, which is wrong. reading_order
    must re-derive a column-major order: the entire LEFT column top to
    bottom, then the entire RIGHT column, then the page footer \u2014 no matter
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
'''

append_once(
    "tests/test_columns.py",
    marker="class TestReadingOrderColumnMajor",
    addition=_TESTS,
    what="regression: column-major order + Chandra fallback",
)

# ---------------------------------------------------------------------------
# scripts/check_reading_order.py — corpus audit tool
# ---------------------------------------------------------------------------
_AUDIT = '''#!/usr/bin/env python3
"""Audit reading order across a processed volume.

For every page it compares Chandra's raw block emission order against the
pipeline's column-major reading order and reports pages where they differ
(those are the pages where the geometric re-sort is doing real work \u2014 and
therefore the ones worth eyeballing in the wiki). It also flags broken
hyphenation joins: a block whose text ends in "<word>-" whose continuation
does not immediately follow in reading order.

Usage:
    python3 scripts/check_reading_order.py output/pjb-052-2010
    python3 scripts/check_reading_order.py output/pjb-052-2010 --page 18
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pjb_pipeline.normalize import build_unified_page          # noqa: E402
from pjb_pipeline.structure.columns import detect_columns      # noqa: E402


def audit_page(raw: dict) -> dict:
    chandra = [b.get("id") for b in raw.get("blocks", [])]
    # Run the exact production path (bbox conversion + column detection +
    # reading_order) so the comparison reflects what the emitters see.
    unified = build_unified_page(json.loads(json.dumps(raw)))
    ordered = unified["blocks"]
    final = [b.get("id") for b in ordered]
    cols = detect_columns(unified)

    bytid = {b.get("id"): b for b in ordered}
    hyphen_breaks = []
    for i, bid in enumerate(final[:-1]):
        t = (bytid[bid].get("text") or "").rstrip()
        if t.endswith("-") and not t.endswith(" -"):
            nxt = (bytid[final[i + 1]].get("text") or "").lstrip()
            first = next((c for c in nxt if c.isalpha()), "")
            if first and not first.islower():
                hyphen_breaks.append((bid, final[i + 1]))

    return {
        "columns": len(cols),
        "reordered": chandra != final,
        "n_moved": sum(1 for a, b in zip(chandra, final) if a != b),
        "hyphen_breaks": hyphen_breaks,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("volume_dir", help="e.g. output/pjb-052-2010")
    ap.add_argument("--page", type=int, default=None)
    args = ap.parse_args()

    interim = Path(args.volume_dir) / "interim"
    if not interim.exists():
        sys.exit(f"no interim dir at {interim}")

    files = sorted(interim.glob("page_*.json"))
    if args.page is not None:
        files = [f for f in files if f.stem == f"page_{args.page:04d}"]

    reordered_pages, flagged = [], []
    for f in files:
        raw = json.loads(f.read_text())
        pn = raw.get("page_num", int(f.stem.split("_")[1]))
        res = audit_page(raw)
        if res["reordered"]:
            reordered_pages.append(pn)
        if res["hyphen_breaks"]:
            flagged.append((pn, res["hyphen_breaks"]))

    print(f"Pages scanned: {len(files)}")
    print(f"Pages where reading_order changed Chandra's order: "
          f"{len(reordered_pages)}")
    if reordered_pages:
        print("  " + ", ".join(str(p) for p in reordered_pages))
    print(f"Pages with a broken hyphenation join (continuation not adjacent): "
          f"{len(flagged)}")
    for pn, breaks in flagged:
        print(f"  page {pn}:")
        for a, b in breaks:
            print(f"    {a} -/-> {b}")
    if not flagged:
        print("  none \u2014 every hyphenation break is followed by its continuation")


if __name__ == "__main__":
    main()
'''

create_once(
    "scripts/check_reading_order.py",
    _AUDIT,
    what="reading-order corpus audit tool",
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
    sys.exit(f"\nTests failed after {elapsed:.1f}s. Backups are at *.bak5.")
print(f"\nTests passed in {elapsed:.1f}s. Backups of modified files are at *.bak5.")
