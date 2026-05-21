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

import json
from pathlib import Path
from typing import List

from tqdm.auto import tqdm

from .config import VolumeConfig


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


def build_unified_page(raw_doc: dict) -> dict:
    """Turn the raw Chandra-per-page JSON into a unified page dict."""
    w, h = raw_doc["image_width"], raw_doc["image_height"]
    blocks = []
    for b in raw_doc.get("blocks", []):
        blocks.append({
            "id":       b["id"],
            "type":     canonical_type(b.get("type", "text")),
            "raw_type": b.get("type", "text"),
            "bbox":     to_pixel_bbox(b.get("bbox"), w, h),
            "text":     b.get("text", "").strip(),
            "html":     b.get("html", "").strip(),   # rich HTML content from Chandra
        })
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
    return {
        "page_num":       raw_doc["page_num"],
        "image_filename": raw_doc["image_filename"],
        "image_width":    w,
        "image_height":   h,
        "blocks":         blocks,
    }


def run(cfg: VolumeConfig, pages: List[dict]) -> List[dict]:
    """Stage entry point. Reads interim JSON per page, returns a list of
    unified page dicts."""
    unified: List[dict] = []
    for rec in tqdm(pages, desc="unify", unit="pg"):
        raw = json.loads((cfg.interim_dir / f"page_{rec['page_num']:04d}.json").read_text())
        unified.append(build_unified_page(raw))

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
