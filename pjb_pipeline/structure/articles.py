"""Article boundary detection.

Two strategies, in priority order:

1. **TOC-driven** (preferred). If the volume has a parseable
   ``table-of-contents`` block, we trust it: each TOC entry becomes one
   article with the title, author, section, and printed start page taken
   from the TOC. The pipeline maps printed page → PDF page via a
   ``printed_page_offset`` that's either configured explicitly or inferred
   from page-header text on the volume's first numbered pages.

2. **Heuristic fallback** (existing notebook logic). If no usable TOC is
   found, fall back to: a section-header block in the upper third of a
   page anchors an article; pages between anchors form one article;
   anything before the first anchor is "Frontmatter".

The output is always the same list-of-dicts shape so downstream emitters
don't care which strategy fired.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import List, Optional, Tuple

from ..config import VolumeConfig
from .toc import (
    TocStructure, TocEntry,
    parse_toc_structure, find_toc_blocks,
)


# ---------------------------------------------------------------------------
# Printed-page → PDF-page offset
# ---------------------------------------------------------------------------

def _scan_running_numbers(unified_pages: list) -> List[Tuple[int, int]]:
    """Find PDF pages that have a plausible page number in the header or
    footer of the actual page. Returns ``[(pdf_page, printed_num), …]``.

    "Plausible" means: a ``page-header`` or ``page-footer`` block whose
    text is mostly a small integer, OR a number that appears at the very
    top/bottom of any text block.
    """
    pairs: List[Tuple[int, int]] = []
    num_only = re.compile(r"^\s*(\d{1,4})\s*$")
    for p in unified_pages:
        H = p["image_height"]
        for b in p["blocks"]:
            if b["type"] in ("page-header", "page-footer"):
                m = num_only.match(b["text"].strip())
                if m:
                    pairs.append((p["page_num"], int(m.group(1))))
                    continue
            # Numbers floating at the very top or bottom of a text region
            y_top = b["bbox"][1]
            y_bot = b["bbox"][3]
            for line in b["text"].splitlines():
                line = line.strip()
                if line.isdigit() and 1 <= int(line) <= 9999:
                    if y_top < H * 0.08 or y_bot > H * 0.92:
                        pairs.append((p["page_num"], int(line)))
                        break
    return pairs


def infer_printed_page_offset(unified_pages: list) -> Optional[int]:
    """Infer the constant offset ``printed = pdf_page - offset`` from the
    page-number stamps found on individual pages. Returns ``None`` if no
    robust offset emerges (e.g. TOC will then drive things differently)."""
    pairs = _scan_running_numbers(unified_pages)
    if len(pairs) < 3:
        return None

    # The offset is (pdf_page - printed_num). It should be constant on most
    # pages; pick the mode.
    from collections import Counter
    offsets = [pdf - printed for pdf, printed in pairs]
    most_common = Counter(offsets).most_common(1)[0]
    offset, support = most_common
    # Demand at least 3 supporting pages or 25% of seen numbers, whichever
    # is bigger.
    if support < max(3, len(offsets) // 4):
        return None
    return offset


# ---------------------------------------------------------------------------
# TOC-driven article detection
# ---------------------------------------------------------------------------

def _find_anchor_on_pdf_page(
    page: dict,
    expected_title: str,
    *,
    top_third_only: bool = True,
) -> Optional[dict]:
    """Look for a section-header block on ``page`` whose text matches the
    first few words of ``expected_title``.

    Comparison is lowered, diacritic-stripped, and word-prefix-based — the
    OCR on the TOC line and the OCR on the article's own title page are
    rarely byte-identical (line breaks differ, hyphenation differs).
    """
    if not expected_title:
        return None

    def norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9äöüß ]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    target = norm(expected_title)
    target_prefix = " ".join(target.split()[:4])  # first 4 words

    H = page["image_height"]
    best = None
    best_score = 0
    for b in page["blocks"]:
        if b["type"] != "section-header":
            continue
        if top_third_only and b["bbox"][1] > H / 2:
            continue
        cand = norm(b["text"])
        if not cand:
            continue
        # crude prefix-match score
        if cand.startswith(target_prefix) or target_prefix.startswith(cand[:len(target_prefix)]):
            score = len(set(cand.split()) & set(target.split()))
            if score > best_score:
                best, best_score = b, score
    return best


def _toc_driven(
    unified_pages: list,
    toc: TocStructure,
    cfg: VolumeConfig,
) -> Optional[List[dict]]:
    """Try to build the article list from the TOC. Returns ``None`` if the
    TOC didn't carry enough page numbers to be useful."""
    entries_with_pages = [e for e in toc.entries if e.page is not None]
    if len(entries_with_pages) < 2:
        return None

    # Resolve printed → PDF page mapping.
    offset = cfg.printed_page_offset
    if offset is None:
        offset = infer_printed_page_offset(unified_pages)
    if offset is None:
        # Last resort: assume the first entry's printed page maps to the
        # first PDF page we processed.
        first_pdf = unified_pages[0]["page_num"]
        offset = first_pdf - entries_with_pages[0].page

    by_pn = {p["page_num"]: p for p in unified_pages}
    min_pdf = min(by_pn)
    max_pdf = max(by_pn)

    # Convert each TOC entry to a (pdf_page, entry) anchor, dropping ones
    # whose target page falls outside the processed range.
    anchors: List[Tuple[int, TocEntry]] = []
    for e in entries_with_pages:
        pdf_page = e.page + offset
        if pdf_page < min_pdf or pdf_page > max_pdf:
            continue
        anchors.append((pdf_page, e))
    if not anchors:
        return None

    # Sort by PDF page so weirdly-ordered TOC entries don't shuffle output.
    anchors.sort(key=lambda x: x[0])

    articles: List[dict] = []
    first_pdf = unified_pages[0]["page_num"]
    last_pdf = unified_pages[-1]["page_num"]

    # Frontmatter — everything before the first anchor
    if anchors[0][0] > first_pdf:
        articles.append({
            "id":          f"{cfg.slug}-frontmatter",
            "num":         0,
            "title":       "Frontmatter",
            "author":      "",
            "section":     "",
            "page_first":  first_pdf,
            "page_last":   anchors[0][0] - 1,
        })

    for i, (pdf_pn, entry) in enumerate(anchors):
        page_last = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else last_pdf
        # Refine the title via the in-page section-header anchor if we can
        # find one — it usually has the canonical capitalisation.
        title = entry.title
        page = by_pn.get(pdf_pn)
        if page is not None:
            anchor = _find_anchor_on_pdf_page(page, entry.title)
            if anchor and anchor.get("text"):
                first_line = anchor["text"].split("\n")[0].strip()
                if first_line:
                    title = first_line

        articles.append({
            "id":         f"{cfg.slug}-art{i + 1:02d}",
            "num":        i + 1,
            "title":      title,
            "author":     entry.author,
            "section":    entry.section,
            "page_first": pdf_pn,
            "page_last":  page_last,
            # keep the source TOC entry for the knowledge graph
            "_toc_entry": asdict(entry),
        })

    # Attach the per-page objects
    for a in articles:
        a["pages"] = [by_pn[pn] for pn in range(a["page_first"], a["page_last"] + 1) if pn in by_pn]
    return articles


# ---------------------------------------------------------------------------
# Heuristic fallback (the original notebook logic)
# ---------------------------------------------------------------------------

def _heuristic(unified_pages: list, cfg: VolumeConfig) -> List[dict]:
    anchors = []
    for p in unified_pages:
        H = p["image_height"]
        upper_headers = [
            b for b in p["blocks"]
            if b["type"] == "section-header" and b["bbox"][1] < H / 3
        ]
        if upper_headers:
            top = sorted(upper_headers, key=lambda b: b["bbox"][1])[0]
            anchors.append((p["page_num"], top))

    if not anchors:
        anchors = [(unified_pages[0]["page_num"], {"text": cfg.volume_title})]

    articles: List[dict] = []
    first_pdf = unified_pages[0]["page_num"]
    last_pdf = unified_pages[-1]["page_num"]

    if anchors[0][0] > first_pdf:
        articles.append({
            "id":         f"{cfg.slug}-frontmatter",
            "num":        0,
            "title":      "Frontmatter",
            "author":     "",
            "section":    "",
            "page_first": first_pdf,
            "page_last":  anchors[0][0] - 1,
        })

    for i, (pn, header) in enumerate(anchors):
        end = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else last_pdf
        title = (header.get("text") or "Untitled").strip()
        lines = [ln.strip() for ln in title.split("\n") if ln.strip()]
        clean_title = lines[0] if lines else title
        author = lines[1] if len(lines) > 1 else ""
        articles.append({
            "id":         f"{cfg.slug}-art{i + 1:02d}",
            "num":        i + 1,
            "title":      clean_title,
            "author":     author,
            "section":    "",
            "page_first": pn,
            "page_last":  end,
        })

    by_pn = {p["page_num"]: p for p in unified_pages}
    for a in articles:
        a["pages"] = [by_pn[pn] for pn in range(a["page_first"], a["page_last"] + 1) if pn in by_pn]
    return articles


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def detect_articles(unified_pages: list, cfg: VolumeConfig) -> Tuple[List[dict], Optional[TocStructure]]:
    """Detect article boundaries in ``unified_pages``.

    Returns ``(articles, toc_or_none)``. The TOC structure (if any) is
    returned so downstream emitters (HTML, knowledge graph) can use it
    directly for the volume contents.
    """
    # 1) Try TOC-driven
    toc_blocks = find_toc_blocks(unified_pages)
    toc: Optional[TocStructure] = None
    if toc_blocks:
        # Concatenate text from all detected TOC blocks (handles multi-page TOCs)
        joined = "\n".join(b["text"] for b in toc_blocks if b.get("text"))
        toc = parse_toc_structure(joined, known_sections=cfg.toc_section_labels)
        articles = _toc_driven(unified_pages, toc, cfg)
        if articles:
            print(f"   article detection: TOC-driven ({len(toc.entries)} TOC entries → "
                  f"{sum(1 for a in articles if a['title'] != 'Frontmatter')} articles)")
            return articles, toc
        else:
            print("   article detection: TOC parsed but unusable, falling back to heuristic")

    # 2) Heuristic
    articles = _heuristic(unified_pages, cfg)
    print(f"   article detection: heuristic "
          f"({sum(1 for a in articles if a['title'] != 'Frontmatter')} articles)")
    return articles, toc
