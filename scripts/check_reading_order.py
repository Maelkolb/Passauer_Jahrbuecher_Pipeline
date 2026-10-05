#!/usr/bin/env python3
"""Audit reading order and block roles across a processed volume.

Runs the production layout analysis (typography calibration, block-role
correction, reading-order choice — see ``pjb_pipeline/structure/layout.py``)
on the cached OCR of a volume and reports:

* the calibrated print sizes (body vs footnote text),
* how many blocks were re-labelled (text → footnote / caption),
* on how many pages the reading order differs from Chandra's, and which
  candidate order won,
* **broken hyphenation joins** — a body block ending in "Wort-" whose
  successor in reading order (on the same page, or the first body block
  of the next page) does not continue it,
* **stranded continuations** — a body block starting lower-case right
  after a finished sentence.

The last two are the numbers that matter: a handful per volume is normal
(compound words like "Böhmerwald-Liedes", poems, OCR omissions); many, or
the same page over and over, means the output needs a look before it is
published.

Usage:
    python3 scripts/check_reading_order.py output/pjb-052-2010
    python3 scripts/check_reading_order.py output/pjb-052-2010 --page 18
    python3 scripts/check_reading_order.py output/pjb-052-2010 --list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pjb_pipeline.normalize import apply_layout, build_unified_page   # noqa: E402
from pjb_pipeline.structure.layout import (                            # noqa: E402
    _ABBREV_END, _ENUMERATOR, _HYPHEN_END, _TERMINAL, _clean_tail, _first_letter,
    calibrate, last_body_block,
)

PROSE = {"text", "list"}
VERSE = re.compile(r"\S {3,}\S.*\S {3,}\S")   # verse lines Chandra joins with wide gaps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("volume_dir", help="e.g. output/pjb-052-2010")
    ap.add_argument("--page", type=int, default=None, help="print one page's final order")
    ap.add_argument("--list", action="store_true", help="list every flagged join")
    args = ap.parse_args()

    interim = Path(args.volume_dir) / "interim"
    if not interim.exists():
        sys.exit(f"no interim dir at {interim}")
    files = sorted(interim.glob("page_*.json"))
    raws = [json.loads(f.read_text()) for f in files]
    pages = [build_unified_page(json.loads(json.dumps(r)), layout=False) for r in raws]

    typo = calibrate(pages)
    prev_tail = None
    prev_note = None
    reports = []
    for p in pages:
        apply_layout(p, typo, prev_tail=prev_tail, prev_footnote=prev_note)
        prev_tail = last_body_block(p) or prev_tail
        notes = [b for b in p["blocks"] if b["type"] == "footnote"]
        prev_note = notes[-1] if notes else None
        reports.append(p.pop("_layout"))

    if args.page is not None:
        for p, r in zip(pages, reports):
            if p["page_num"] != args.page:
                continue
            print(f"page {p['page_num']}: strategy {r['strategy']}, score {r['score']}, "
                  f"repaired {r['repaired']}")
            for b in p["blocks"]:
                t = (b.get("text") or "").replace("\n", " ")
                note = f"  [{b['role_note']}]" if b.get("role_note") else ""
                print(f"  {b['id']:12s} {b['type']:16s} {t[:70]!r}{note}")
        return

    hyph_bad, lc_bad = [], []
    verse_skipped = 0
    prev = None
    for p in pages:
        prose = [b for b in p["blocks"] if b["type"] in PROSE and (b.get("text") or "").strip()]
        # Song and verse editions (numbered stanzas, lines starting in lower
        # case) would flood the continuation count; they are not errors.
        stanza_numbers = sum(1 for b in prose if re.fullmatch(r"\s*\d{1,2}\.?\s*", b.get("text") or ""))
        verse_page = stanza_numbers >= 2 or sum(1 for b in prose if VERSE.search(b.get("text") or "")) >= 2
        seq = ([prev] if prev is not None else []) + prose
        for a, b in zip(seq, seq[1:]):
            at = _clean_tail(a.get("text") or "")
            f = _first_letter(b.get("text") or "")
            if not at or not f:
                continue
            if _HYPHEN_END.search(at) and not f.islower():
                hyph_bad.append((p["page_num"], a["id"], b["id"]))
            elif (f.islower() and _TERMINAL.search(at) and not _ABBREV_END.search(at)
                  and not _ENUMERATOR.match(b.get("text") or "")):
                if verse_page:
                    verse_skipped += 1
                else:
                    lc_bad.append((p["page_num"], a["id"], b["id"]))
        if prose:
            prev = prose[-1]

    reclass = Counter(c[2] for r in reports for c in r["reclassified"])
    moved = [r for r in reports if r["moved"]]
    strategies = Counter(r["strategy"] for r in moved)
    print(f"Pages scanned: {len(pages)}")
    print(f"Print size: body {typo.body:.2f}, footnotes {typo.small:.2f} chars/kpx² "
          f"(threshold {typo.threshold:.2f})")
    print(f"Blocks re-labelled: " + (", ".join(f"text → {k}: {v}" for k, v in reclass.items())
                                     or "none"))
    print(f"Pages where the reading order differs from Chandra's: {len(moved)}"
          + (f"  ({', '.join(f'{k} {v}' for k, v in strategies.most_common())})" if moved else ""))
    print(f"Broken hyphenation joins: {len(hyph_bad)}")
    print(f"Stranded continuations (lower-case start after a finished sentence): {len(lc_bad)}"
          + (f"  (+{verse_skipped} in verse/song editions, not counted)" if verse_skipped else ""))
    if args.list:
        for kind, rows in (("hyphen", hyph_bad), ("continuation", lc_bad)):
            for pn, a, b in rows:
                print(f"  {kind:12s} page {pn}: {a} -/-> {b}")
    elif hyph_bad or lc_bad:
        pages_flagged = sorted({pn for pn, _, _ in hyph_bad + lc_bad})
        print("  pages: " + ", ".join(str(x) for x in pages_flagged[:60])
              + (" …" if len(pages_flagged) > 60 else ""))
        print("  (run with --list for the block ids, --page N to see one page)")


if __name__ == "__main__":
    main()
