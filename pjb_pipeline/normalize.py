"""Build the unified per-page model.

Stage 3. Takes the raw per-page JSON written by ``ocr.run()`` and:

* normalises the layout block types (Chandra/Surya vocabulary → our
  canonical vocabulary used downstream by TEI, PageXML, HTML, and the
  knowledge graph)
* converts bboxes to pixel coordinates (Chandra returns them normalised
  to 0..1)
* falls back to a single text block per page if the layout parser had no
  output for that page, so the rest of the pipeline keeps working
"""

from __future__ import annotations

import html as html_module
import json
import re
from pathlib import Path
from typing import List, Optional

from tqdm.auto import tqdm

from .config import VolumeConfig
from .structure.layout import (
    DEFAULT_TYPOGRAPHY, Typography, calibrate, last_body_block, order_page,
    reclassify_blocks,
)

# Visual block types whose Chandra-generated alt-text description we want
# to surface as a first-class ``description`` field. The graph emitter and
# the wiki emitter both fall back to this when the block has no Chandra-
# extracted text caption of its own.
_VISUAL_TYPES_FOR_DESCRIPTION = {"image", "figure", "diagram"}

_IMG_ALT_RE = re.compile(r'<img\b[^>]*\balt="([^"]*)"', re.IGNORECASE | re.DOTALL)


def _extract_chandra_alt(html: str) -> str:
    """Pull the alt-text description out of an ``<img alt="…">`` tag.

    Chandra emits visual blocks with the layout-only text field empty and
    the actual description tucked into the ``alt`` attribute of an
    ``<img>`` element inside the block's ``html`` field. Without this,
    every figure in the wiki and every ImageObject in the JSON-LD graph
    would carry only a generic placeholder name. Returns ``""`` if no
    alt text is found.
    """
    if not html:
        return ""
    m = _IMG_ALT_RE.search(html)
    if not m:
        return ""
    return html_module.unescape(m.group(1)).strip()


# Canonical type vocabulary used by every downstream emitter.
# When Chandra adds a new label, add it here.
CANON_TYPES = {
    "text":              "text",
    "paragraph":         "text",
    "section-header":    "section-header",
    "header":            "section-header",
    "title":             "section-header",      # Chandra "Title"
    "caption":           "caption",
    "footnote":          "footnote",
    "table":             "table",
    "image":             "image",
    "figure":            "figure",
    "diagram":           "diagram",
    "picture":           "figure",              # Chandra/Surya "Picture"
    "equation-block":    "equation",
    "equation":          "equation",
    "formula":           "equation",
    "textinlinemath":    "text",                # inline math token
    "code-block":        "code",
    "code":              "code",
    "chemical-block":    "code",
    "bibliography":      "bibliography",
    "table-of-contents": "table-of-contents",
    "toc-entry":         "table-of-contents",
    "page-header":       "page-header",
    "page-footer":       "page-footer",
    "list-group":        "list",
    "list":              "list",
    "list-item":         "list",
    "form":              "form",
    "handwriting":       "text",
    "complex-block":     "text",
}


def canonical_type(t) -> str:
    """Map any Chandra/Surya block-type string to our canonical vocabulary.
    Tolerates enum-style ``"BlockType.Picture"`` strings that may live in
    cached interim JSON from older runs of the pipeline."""
    if t is None:
        return "text"
    s = str(t).lower().replace("_", "-").strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return CANON_TYPES.get(s, "text")


def to_pixel_bbox(bbox, w, h) -> List[int]:
    """Convert normalised bbox [0,1] (or any range) to pixel ``[x1,y1,x2,y2]``."""
    if not bbox or len(bbox) < 4:
        return [0, 0, w, h]
    x1, y1, x2, y2 = bbox[:4]
    # Heuristic: if all values are <= 1.5, assume normalised
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
        x1, y1, x2, y2 = x1 * w, y1 * h, x2 * w, y2 * h
    # Clamp & order
    x1, x2 = sorted((max(0, x1), min(w, x2)))
    y1, y2 = sorted((max(0, y1), min(h, y2)))
    return [int(round(v)) for v in (x1, y1, x2, y2)]


def build_unified_page(
    raw_doc: dict,
    *,
    typography: Optional[Typography] = None,
    prev_tail: Optional[dict] = None,
    layout: bool = True,
) -> dict:
    """Turn the raw Chandra-per-page JSON into a unified page dict.

    With ``layout=True`` (the default) the page also goes through layout
    analysis: block roles are corrected using ``typography`` (see
    :func:`pjb_pipeline.structure.layout.calibrate`; a corpus default is
    used when omitted) and the blocks are put into reading order, taking
    ``prev_tail`` — the previous page's last body block — into account.
    The layout report lands in ``page["_layout"]``.
    """
    w, h = raw_doc["image_width"], raw_doc["image_height"]
    blocks = []
    for b in raw_doc.get("blocks", []):
        canon = canonical_type(b.get("type", "text"))
        html_field = b.get("html", "").strip()
        blk: dict = {
            "id":       b["id"],
            "type":     canon,
            "raw_type": b.get("type", "text"),
            "bbox":     to_pixel_bbox(b.get("bbox"), w, h),
            "text":     b.get("text", "").strip(),
            "html":     html_field,
        }
        # Surface Chandra's alt-text description for visual blocks as a
        # dedicated ``description`` field. Downstream emitters (wiki,
        # graph) read it when no human-extracted caption text is
        # available.
        if canon in _VISUAL_TYPES_FOR_DESCRIPTION:
            desc = _extract_chandra_alt(html_field)
            if desc:
                blk["description"] = desc
        blocks.append(blk)
    # Fallback: dump the markdown into a single text block so the page
    # isn't completely empty when layout parsing failed.
    if not blocks and raw_doc.get("markdown"):
        blocks.append({
            "id":       f"p{raw_doc['page_num']}_b001",
            "type":     "text",
            "raw_type": "text",
            "bbox":     [0, 0, w, h],
            "text":     raw_doc["markdown"].strip(),
            "html":     "",
        })
    page = {
        "page_num":       raw_doc["page_num"],
        "image_filename": raw_doc["image_filename"],
        "image_width":    w,
        "image_height":   h,
        "blocks":         blocks,
    }
    if layout:
        apply_layout(page, typography or DEFAULT_TYPOGRAPHY, prev_tail=prev_tail)
    return page


def apply_layout(page: dict, typography: Typography, *,
                 prev_tail: Optional[dict] = None,
                 prev_footnote: Optional[dict] = None) -> dict:
    """Correct block roles and set the reading order of one unified page.

    Applied once here so every downstream emitter (wiki, TEI, PageXML,
    graph) reads correctly typed, correctly ordered blocks; the HTML
    facsimile re-detects columns for its own band layout.
    """
    chandra_ids = [b["id"] for b in page["blocks"]]
    changes = reclassify_blocks(page, typography, prev_footnote=prev_footnote)
    report = order_page(page, prev_tail=prev_tail, chandra_order=chandra_ids)
    report["reclassified"] = [list(c) for c in changes]
    report["moved"] = chandra_ids != [b["id"] for b in page["blocks"]]
    page["_layout"] = report
    return page


def run(cfg: VolumeConfig, pages: List[dict]) -> List[dict]:
    """Stage entry point. Reads interim JSON per page, returns a list of
    unified page dicts."""
    unified: List[dict] = []
    for rec in tqdm(pages, desc="unify", unit="pg"):
        raw = json.loads((cfg.interim_dir / f"page_{rec['page_num']:04d}.json").read_text())
        unified.append(build_unified_page(raw, layout=False))

    # Layout analysis: calibrate print sizes on the whole volume, then fix
    # block roles and reading order page by page (the previous page's last
    # body block takes part in the next page's ordering decision).
    typo = calibrate(unified)
    prev_tail = None
    prev_footnote = None
    layout_log = []
    for page in unified:
        apply_layout(page, typo, prev_tail=prev_tail, prev_footnote=prev_footnote)
        prev_tail = last_body_block(page) or prev_tail
        notes = [b for b in page["blocks"] if b["type"] == "footnote"]
        prev_footnote = notes[-1] if notes else None
        rep = page.pop("_layout")
        layout_log.append({"page_num": page["page_num"], **rep})
    n_fn = sum(1 for r in layout_log for c in r["reclassified"] if c[2] == "footnote")
    n_cap = sum(1 for r in layout_log for c in r["reclassified"] if c[2] == "caption")
    strategies: dict = {}
    for r in layout_log:
        if r["moved"]:
            strategies[r["strategy"]] = strategies.get(r["strategy"], 0) + 1
    print(f"   typography: body {typo.body:.2f}, footnote {typo.small:.2f} "
          f"chars/kpx² → threshold {typo.threshold:.2f}")
    print(f"   reclassified: {n_fn} text → footnote, {n_cap} text → caption")
    print(f"   reading order differs from Chandra on "
          f"{sum(1 for r in layout_log if r['moved'])} pages "
          f"({', '.join(f'{k}: {v}' for k, v in sorted(strategies.items()))}; "
          f"{sum(1 for r in layout_log if r['repaired'])} repaired by text continuity)")
    (cfg.logs_dir / "layout.json").write_text(json.dumps(
        {"typography": typo.as_dict(), "pages": layout_log},
        ensure_ascii=False, indent=1,
    ))

    (cfg.logs_dir / "unified.json").write_text(
        json.dumps(unified, ensure_ascii=False, indent=2)
    )
    n_regions = sum(len(p["blocks"]) for p in unified)
    print(f"   {n_regions} regions across {len(unified)} pages")

    # Diagnostic: histogram of (raw_type, canonical_type) so we can spot
    # any new Chandra label that's silently falling back to "text".
    seen: dict = {}
    for p in unified:
        for b in p["blocks"]:
            key = (b.get("raw_type", ""), b["type"])
            seen[key] = seen.get(key, 0) + 1
    if seen:
        print("   block-type histogram (raw -> canonical: count):")
        for (raw, canon), n in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f"     {raw!s:>28s}  ->  {canon:<22s} {n}")

    return unified
