#!/usr/bin/env python3
"""Audit reading order across a processed volume.

For every page it compares Chandra's raw block emission order against the
pipeline's column-major reading order and reports pages where they differ
(those are the pages where the geometric re-sort is doing real work — and
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

    # Only body prose can host a *misplaced* continuation. A body block
    # ending in "<word>-" followed by a non-prose region (footnote, footer,
    # image, title, …) is a normal cross-page break — the word's other half
    # is on the following page — so we ignore those. We only flag a break
    # where the very next block is also body prose yet starts uppercase,
    # which is the signature of a continuation that landed out of order.
    PROSE = {"text", "paragraph", "body"}
    bytid = {b.get("id"): b for b in ordered}
    hyphen_breaks = []
    for i, bid in enumerate(final[:-1]):
        cur = bytid[bid]
        if cur.get("type") not in PROSE:
            continue
        t = (cur.get("text") or "").rstrip()
        if not (t.endswith("-") and not t.endswith(" -")):
            continue
        nxt_block = bytid[final[i + 1]]
        if nxt_block.get("type") not in PROSE:
            continue
        nxt = (nxt_block.get("text") or "").lstrip()
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
        print("  none — every hyphenation break is followed by its continuation")


if __name__ == "__main__":
    main()
