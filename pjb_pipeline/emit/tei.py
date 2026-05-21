"""TEI-XML emission.

One ``<TEI>`` document per volume, with:

* ``<teiHeader>`` — bibliographic metadata for the volume
* ``<facsimile>`` — one ``<surface>`` per page, linking to the rendered
  PNG, with one ``<zone>`` per region
* ``<text><body>`` — one ``<div type="article">`` per detected article,
  with section type and inline elements (``<head>``, ``<p>``, ``<note>``,
  ``<figure>``, …) and ``<pb/>`` page breaks
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from itertools import count
from typing import List, Optional

from ..config import VolumeConfig
from ..structure.toc import TocStructure


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _t(tag): return f"{{{TEI_NS}}}{tag}"
def _xmlid(eid): return {f"{{{XML_NS}}}id": eid}


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _build_header(cfg: VolumeConfig) -> ET.Element:
    header = ET.Element(_t("teiHeader"))
    fileDesc = ET.SubElement(header, _t("fileDesc"))

    titleStmt = ET.SubElement(fileDesc, _t("titleStmt"))
    ET.SubElement(titleStmt, _t("title"), {"type": "main"}).text = (
        f"{cfg.volume_title} {cfg.volume_number_roman}"
    )
    ET.SubElement(titleStmt, _t("title"), {"type": "sub"}).text = cfg.volume_subtitle
    ET.SubElement(titleStmt, _t("editor")).text = cfg.editor

    publicationStmt = ET.SubElement(fileDesc, _t("publicationStmt"))
    ET.SubElement(publicationStmt, _t("publisher")).text = cfg.publisher
    ET.SubElement(publicationStmt, _t("date"), {"when": str(cfg.volume_year)}).text = str(cfg.volume_year)
    ET.SubElement(publicationStmt, _t("availability")).text = (
        "Digital edition produced for research and personal use."
    )

    sourceDesc = ET.SubElement(fileDesc, _t("sourceDesc"))
    bibl = ET.SubElement(sourceDesc, _t("bibl"))
    bibl.text = f"{cfg.volume_title} {cfg.volume_number_roman} ({cfg.volume_year}). {cfg.publisher}."

    encodingDesc = ET.SubElement(header, _t("encodingDesc"))
    appInfo = ET.SubElement(encodingDesc, _t("appInfo"))
    application = ET.SubElement(appInfo, _t("application"),
                                {"ident": "PJB-Pipeline", "version": "0.2"})
    ET.SubElement(application, _t("label")).text = (
        "Passauer Jahrbücher Digital Edition Pipeline"
    )

    profileDesc = ET.SubElement(header, _t("profileDesc"))
    langUsage = ET.SubElement(profileDesc, _t("langUsage"))
    ET.SubElement(langUsage, _t("language"), {"ident": cfg.language}).text = "German"

    return header


# ---------------------------------------------------------------------------
# Facsimile
# ---------------------------------------------------------------------------

def _build_facsimile(pages: List[dict]) -> ET.Element:
    facs = ET.Element(_t("facsimile"))
    for p in pages:
        surf_id = f"page_{p['page_num']:04d}"
        surf = ET.SubElement(facs, _t("surface"), {
            "n": str(p["page_num"]),
            "ulx": "0", "uly": "0",
            "lrx": str(p["image_width"]), "lry": str(p["image_height"]),
            **_xmlid(surf_id),
        })
        ET.SubElement(surf, _t("graphic"), {
            "url":    f"pages/{p['image_filename']}",
            "width":  f"{p['image_width']}px",
            "height": f"{p['image_height']}px",
        })
        for b in p["blocks"]:
            x1, y1, x2, y2 = b["bbox"]
            ET.SubElement(surf, _t("zone"), {
                "ulx": str(x1), "uly": str(y1),
                "lrx": str(x2), "lry": str(y2),
                "rendition": b["type"],
                **_xmlid(f"z_{b['id']}"),
            })
    return facs


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------

def _emit_block_tei(parent, block, footnote_counter, footnote_collector):
    btype = block["type"]
    facs_ref = f"#z_{block['id']}"
    text = block["text"]

    if btype == "section-header":
        el = ET.SubElement(parent, _t("head"), {"facs": facs_ref})
        el.text = text
    elif btype in ("page-header", "page-footer"):
        el = ET.SubElement(parent, _t("fw"), {"type": btype, "facs": facs_ref})
        el.text = text
    elif btype == "footnote":
        n = next(footnote_counter)
        note_id = f"fn_{block['id']}"
        el = ET.SubElement(parent, _t("note"), {
            "place": "foot",
            "n": str(n),
            "facs": facs_ref,
            **_xmlid(note_id),
        })
        el.text = text
        footnote_collector.append((n, note_id, text))
    elif btype == "caption":
        fig = ET.SubElement(parent, _t("figure"), {"facs": facs_ref})
        ET.SubElement(fig, _t("figDesc")).text = text
    elif btype in ("figure", "image", "diagram"):
        fig = ET.SubElement(parent, _t("figure"), {"facs": facs_ref, "type": btype})
        ET.SubElement(fig, _t("graphic"), {"url": f"#{facs_ref[1:]}"})
        if text:
            ET.SubElement(fig, _t("figDesc")).text = text
    elif btype == "table":
        tbl = ET.SubElement(parent, _t("table"), {"facs": facs_ref})
        row = ET.SubElement(tbl, _t("row"))
        ET.SubElement(row, _t("cell")).text = text
    elif btype == "bibliography":
        lb = ET.SubElement(parent, _t("listBibl"), {"facs": facs_ref})
        for line in text.split("\n"):
            line = line.strip()
            if line:
                ET.SubElement(lb, _t("bibl")).text = line
    elif btype == "table-of-contents":
        ls = ET.SubElement(parent, _t("list"), {"type": "contents", "facs": facs_ref})
        for line in text.split("\n"):
            line = line.strip()
            if line:
                ET.SubElement(ls, _t("item")).text = line
    elif btype == "list":
        ls = ET.SubElement(parent, _t("list"), {"facs": facs_ref})
        for line in text.split("\n"):
            line = line.strip()
            if line:
                ET.SubElement(ls, _t("item")).text = line
    elif btype == "equation":
        el = ET.SubElement(parent, _t("formula"),
                           {"facs": facs_ref, "notation": "TeX"})
        el.text = text
    else:  # text and fallback
        el = ET.SubElement(parent, _t("p"), {"facs": facs_ref})
        el.text = text


def _build_body(articles: List[dict]) -> ET.Element:
    text_el = ET.Element(_t("text"))
    body = ET.SubElement(text_el, _t("body"))

    for art in articles:
        attrs = {"type": "article", **_xmlid(art["id"])}
        if art.get("section"):
            # Use the @subtype slot for the TOC section label
            # ("Aufsätze" / "Berichte" / ...).
            attrs["subtype"] = art["section"]
        div = ET.SubElement(body, _t("div"), attrs)

        if art["title"] and art["title"] != "Frontmatter":
            ET.SubElement(div, _t("head"), {"type": "main"}).text = art["title"]
            if art.get("author"):
                byline = ET.SubElement(div, _t("byline"))
                ET.SubElement(byline, _t("docAuthor")).text = art["author"]

        footnote_counter = count(1)
        footnote_collector = []

        for p in art.get("pages", []):
            ET.SubElement(div, _t("pb"), {
                "n":    str(p["page_num"]),
                "facs": f"#page_{p['page_num']:04d}",
            })
            for blk in p["blocks"]:
                # Skip the section-header on the first page that duplicates the title
                if (blk["type"] == "section-header"
                    and p["page_num"] == art["page_first"]
                    and blk["text"].strip().startswith(art["title"][:30])):
                    continue
                _emit_block_tei(div, blk, footnote_counter, footnote_collector)

    return text_el


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(
    cfg: VolumeConfig,
    articles: List[dict],
    pages: List[dict],
    toc: Optional[TocStructure] = None,
) -> None:
    ET.register_namespace("", TEI_NS)
    root = ET.Element(_t("TEI"))
    root.append(_build_header(cfg))
    root.append(_build_facsimile(pages))
    root.append(_build_body(articles))
    ET.indent(root, space="  ")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")

    out_path = cfg.tei_dir / f"{cfg.slug}.xml"
    out_path.write_text(xml_str, encoding="utf-8")
    print(f"   wrote {out_path}  ({len(xml_str):,} chars)")
