"""PageXML emission (PRImA PAGE schema, 2019-07-15).

One file per page. Each canonical block becomes a ``TextRegion``,
``ImageRegion``, ``TableRegion``, or ``MathsRegion`` with a ``Coords``
polygon derived from its bbox.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import List

from tqdm.auto import tqdm

from ..config import VolumeConfig


PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"

# Canonical type → (PageXML element name, optional @type attribute)
PAGEXML_TYPE = {
    "text":              ("TextRegion",  "paragraph"),
    "section-header":    ("TextRegion",  "heading"),
    "caption":           ("TextRegion",  "caption"),
    "footnote":          ("TextRegion",  "footnote"),
    "page-header":       ("TextRegion",  "header"),
    "page-footer":       ("TextRegion",  "footer"),
    "list":              ("TextRegion",  "list-label"),
    "table-of-contents": ("TextRegion",  "TOC-entry"),
    "bibliography":      ("TextRegion",  "other"),
    "form":              ("TextRegion",  "other"),
    "table":             ("TableRegion", None),
    "image":             ("ImageRegion", None),
    "figure":            ("ImageRegion", None),
    "diagram":           ("ImageRegion", None),
    "equation":          ("MathsRegion", None),
    "code":              ("TextRegion",  "other"),
}


def _bbox_to_points(bbox) -> str:
    x1, y1, x2, y2 = bbox
    return f"{x1},{y1} {x2},{y1} {x2},{y2} {x1},{y2}"


def page_to_pagexml(page: dict, creator: str = "PJB Pipeline") -> str:
    ET.register_namespace("", PAGE_NS)
    root = ET.Element(f"{{{PAGE_NS}}}PcGts")

    meta = ET.SubElement(root, f"{{{PAGE_NS}}}Metadata")
    ET.SubElement(meta, f"{{{PAGE_NS}}}Creator").text = creator
    ET.SubElement(meta, f"{{{PAGE_NS}}}Created").text = datetime.utcnow().isoformat()
    ET.SubElement(meta, f"{{{PAGE_NS}}}LastChange").text = datetime.utcnow().isoformat()

    page_el = ET.SubElement(root, f"{{{PAGE_NS}}}Page", {
        "imageFilename": page["image_filename"],
        "imageWidth":    str(page["image_width"]),
        "imageHeight":   str(page["image_height"]),
    })

    for blk in page["blocks"]:
        kind, attr_type = PAGEXML_TYPE.get(blk["type"], ("TextRegion", "other"))
        attrs = {"id": blk["id"], "custom": f"chandra:{blk['raw_type']}"}
        if attr_type:
            attrs["type"] = attr_type
        region = ET.SubElement(page_el, f"{{{PAGE_NS}}}{kind}", attrs)
        ET.SubElement(region, f"{{{PAGE_NS}}}Coords", {"points": _bbox_to_points(blk["bbox"])})
        if kind == "TextRegion" and blk["text"]:
            te = ET.SubElement(region, f"{{{PAGE_NS}}}TextEquiv")
            uni = ET.SubElement(te, f"{{{PAGE_NS}}}Unicode")
            uni.text = blk["text"]

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def run(cfg: VolumeConfig, unified: List[dict]) -> None:
    creator = f"PJB Pipeline · {cfg.volume_title} {cfg.volume_number_roman}"
    for p in tqdm(unified, desc="pagexml", unit="pg"):
        out = cfg.pagexml_dir / f"page_{p['page_num']:04d}.xml"
        out.write_text(page_to_pagexml(p, creator=creator), encoding="utf-8")
    print(f"   {len(unified)} PageXML files in {cfg.pagexml_dir}")
