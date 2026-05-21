"""HTML renderers.

This is the bulk of the HTML emission code, ported out of the notebook.
The main upgrade over the notebook is: when a body block has detected
footnote references, those references are now rendered as anchor links
to their resolved footnote element, instead of just being wrapped in a
``<sup>`` tag with no link target.
"""

from __future__ import annotations

import html
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional

from tqdm.auto import tqdm

from ...config import VolumeConfig
from ...structure.toc import parse_toc_text
from ...structure.footnotes import (
    Footnote, FootnoteRef, find_refs_in_text,
)
from .chrome import page_chrome, html_escape
from .crops import REGIONS_DIR_NAME, VISUAL_BLOCK_TYPES, make_region_crops


# ---------------------------------------------------------------------------
# Inline sanitisation
# ---------------------------------------------------------------------------

_ALLOWED_INLINE_TAGS = {
    "i", "em", "b", "strong", "u", "s", "strike",
    "sup", "sub", "small", "br", "span", "mark",
}


class _InlineSanitizer(HTMLParser):
    """Pass-through allow-listed inline tags, escape the rest as text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list = []

    def handle_starttag(self, tag, attrs):
        if tag in _ALLOWED_INLINE_TAGS:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _ALLOWED_INLINE_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        if tag in _ALLOWED_INLINE_TAGS:
            self.parts.append(f"<{tag}/>")

    def handle_data(self, data):
        self.parts.append(html.escape(data, quote=False))


def sanitize_inline_html(text: str) -> str:
    """Render OCR text as safe HTML, preserving inline formatting tags."""
    if not text:
        return ""
    p = _InlineSanitizer()
    try:
        p.feed(text)
        p.close()
    except Exception:
        return html.escape(text, quote=False)
    return "".join(p.parts)


def alt_text(text: str) -> str:
    """Strip inline tags for an ``alt`` attribute."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", "", text)
    return html.escape(cleaned, quote=True)


# ---------------------------------------------------------------------------
# Text with footnote-reference linking
# ---------------------------------------------------------------------------

def _text_with_fn_refs(
    text: str,
    notes_by_n: Dict[int, Footnote],
) -> str:
    """Replace every footnote-ref marker in ``text`` with an anchor
    ``<sup class="fn-ref"><a href="#fn-id">n</a></sup>``. Other characters
    are HTML-escaped and inline tags are preserved.

    We do the replacement in two passes: first locate the marker char
    offsets in the *raw* text, then walk the raw text once and emit either
    sanitised-inline-html for plain spans or the anchor for ref spans.
    """
    if not text:
        return ""
    refs = find_refs_in_text(text)
    if not refs:
        return sanitize_inline_html(text).replace("\n", "<br>")

    parts: List[str] = []
    pos = 0
    for s, e, n in refs:
        if s < pos:
            continue
        if s > pos:
            parts.append(sanitize_inline_html(text[pos:s]).replace("\n", "<br>"))
        target = notes_by_n.get(n)
        if target:
            parts.append(
                f'<sup class="fn-ref"><a href="#{html.escape(target.html_id)}">'
                f'{n}</a></sup>'
            )
        else:
            parts.append(f'<sup class="fn-ref">{n}</sup>')
        pos = e
    if pos < len(text):
        parts.append(sanitize_inline_html(text[pos:]).replace("\n", "<br>"))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Structured TOC rendering for the per-page transcription column
# ---------------------------------------------------------------------------

def render_toc_block(blk) -> str:
    """Turn a TOC block into a structured HTML fragment with proper title,
    section, row layout, and dot leaders."""
    parts = []
    for e in parse_toc_text(blk.get("text", "")):
        text = sanitize_inline_html(e["text"])
        if e["kind"] == "title":
            parts.append(f'<div class="toc-block-title">{text}</div>')
        elif e["kind"] == "header":
            parts.append(f'<div class="toc-block-section">{text}</div>')
        else:
            page = e.get("page")
            page_html = (
                f'<span class="toc-block-page">{html_escape(page)}</span>'
                if page else ""
            )
            parts.append(
                f'<div class="toc-block-row">'
                f'<span class="toc-block-text">{text}</span>'
                f'<span class="toc-block-leader" aria-hidden="true"></span>'
                f'{page_html}'
                f'</div>'
            )
    if not parts:
        parts.append(
            f'<div class="toc-block-row"><span class="toc-block-text">'
            f'{sanitize_inline_html(blk.get("text", ""))}</span></div>'
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Per-block HTML renderers
# ---------------------------------------------------------------------------

def render_block_html(
    blk,
    notes_by_n: Optional[Dict[int, Footnote]] = None,
) -> str:
    """Render one block for the per-page transcription column."""
    bid = html_escape(blk["id"])
    btype = blk["type"]
    bbox = ",".join(str(v) for v in blk["bbox"])
    notes_by_n = notes_by_n or {}

    # Visual regions: embed the actual crop.
    if btype in VISUAL_BLOCK_TYPES and blk.get("_crop"):
        rel = f"../{REGIONS_DIR_NAME}/{blk['_crop']}"
        cap_text = blk.get("text", "") or ""
        cap_html = sanitize_inline_html(cap_text).replace("\n", "<br>") if cap_text else ""
        alt = alt_text(cap_text) or html_escape(btype)
        inner = f'<img src="{rel}" alt="{alt}" loading="lazy">'
        if cap_html:
            inner += f'<span class="region-caption">{cap_html}</span>'
        return (
            f'<div class="region" data-type="{btype}" data-bbox="{bbox}" id="{bid}">'
            f'{inner}'
            f'</div>'
        )

    if btype == "table-of-contents":
        return (
            f'<div class="region toc-block" data-type="{btype}" data-bbox="{bbox}" id="{bid}">'
            f'{render_toc_block(blk)}'
            f'</div>'
        )

    # Footnote bodies get a stable html_id so refs can link to them.
    if btype == "footnote" and blk.get("_fn"):
        fn = blk["_fn"]
        body_html = sanitize_inline_html(fn.text).replace("\n", "<br>")
        return (
            f'<div class="region" data-type="{btype}" data-bbox="{bbox}" id="{html.escape(fn.html_id)}">'
            f'<span class="footnote-num">{fn.n}</span> {body_html}'
            f'</div>'
        )

    # Body text — apply footnote-ref linking
    text_html = _text_with_fn_refs(blk["text"], notes_by_n)
    return (
        f'<div class="region" data-type="{btype}" data-bbox="{bbox}" id="{bid}">'
        f'{text_html}'
        f'</div>'
    )


def render_inline_block_html(
    blk,
    notes_by_n: Optional[Dict[int, Footnote]] = None,
) -> str:
    """Render a block as part of the continuous article-reading view."""
    btype = blk["type"]
    notes_by_n = notes_by_n or {}
    text = sanitize_inline_html(blk["text"])

    if btype == "section-header":
        return f'<h2>{text}</h2>'
    if btype == "caption":
        return f'<figure><figcaption>{text}</figcaption></figure>'
    if btype == "footnote":
        return ""  # collected separately
    if btype in ("page-header", "page-footer"):
        return ""  # suppress in reading view
    if btype == "table":
        return f'<table><tr><td>{text}</td></tr></table>'
    if btype == "bibliography":
        items = "".join(
            f"<li>{sanitize_inline_html(line)}</li>"
            for line in blk["text"].split("\n") if line.strip()
        )
        return f'<ol class="bibl">{items}</ol>'
    if btype == "table-of-contents":
        return f'<div class="toc-block">{render_toc_block(blk)}</div>'
    if btype in VISUAL_BLOCK_TYPES:
        cap_text = blk.get("text", "") or ""
        cap_html = sanitize_inline_html(cap_text) if cap_text else btype.title()
        alt = alt_text(cap_text) or html_escape(btype)
        if blk.get("_crop"):
            rel = f"../{REGIONS_DIR_NAME}/{blk['_crop']}"
            return (
                f'<figure class="visual-region {btype}">'
                f'<img src="{rel}" alt="{alt}" loading="lazy">'
                f'<figcaption>{cap_html}</figcaption>'
                f'</figure>'
            )
        return f'<figure><figcaption><em>[{btype}]</em> {cap_html}</figcaption></figure>'
    if btype == "equation":
        return f'<p class="equation">{text}</p>'
    if btype == "list":
        items = "".join(
            f"<li>{sanitize_inline_html(line)}</li>"
            for line in blk["text"].split("\n") if line.strip()
        )
        return f'<ul>{items}</ul>'

    # Default paragraph with footnote-ref linking
    text_html = _text_with_fn_refs(blk["text"], notes_by_n)
    return f'<p>{text_html}</p>'


# ---------------------------------------------------------------------------
# Index / Article / Facsimile builders
# ---------------------------------------------------------------------------

def build_volume_index(cfg: VolumeConfig, articles: list, unified: list) -> str:
    toc_items = []
    for a in articles:
        if a["title"] == "Frontmatter":
            continue
        toc_items.append(f'''
        <li class="toc-item">
          <span class="toc-num">{a["num"]:02d}</span>
          <div class="toc-body">
            <span class="toc-title"><a href="articles/{a["id"]}.html">{html_escape(a["title"])}</a></span>
            <span class="toc-author">{html_escape(a["author"])}</span>
          </div>
          <span class="toc-pages">S. {a["page_first"]}–{a["page_last"]}</span>
        </li>''')

    total_pages = sum(len(a.get("pages", [])) for a in articles)
    total_articles = sum(1 for a in articles if a["title"] != "Frontmatter")

    body = f'''
<section class="cover">
  <div class="eyebrow">{html_escape(cfg.editor)}</div>
  <h1 class="title">{html_escape(cfg.volume_title)}</h1>
  <div class="subtitle">{html_escape(cfg.volume_subtitle)}</div>
  <div class="roman">{html_escape(cfg.volume_number_roman)}</div>
  <div class="year">Anno {cfg.volume_year}</div>
</section>

<section class="meta-strip">
  <span>Volume <strong>№ {cfg.volume_number}</strong></span>
  <span>Articles <strong>{total_articles}</strong></span>
  <span>Pages <strong>{total_pages}</strong></span>
  <span>Publisher <strong>{html_escape(cfg.publisher)}</strong></span>
</section>

<section class="toc" id="contents">
  <h2 class="toc-heading">Inhalt · Contents</h2>
  <ol class="toc-list">
    {"".join(toc_items)}
  </ol>
  <hr class="fleuron">
  <p style="text-align:center; font-family: var(--ui); font-size: 0.78rem; letter-spacing: 0.18em; text-transform: uppercase; color: var(--ink-faint);">
    Browse facsimile · <a href="pages/page-{unified[0]["page_num"]:04d}.html">first page</a>
  </p>
</section>
'''
    return page_chrome(
        cfg,
        f"{cfg.volume_title} {cfg.volume_number_roman} ({cfg.volume_year})",
        body,
        asset_prefix=".",
    )


def build_article_html(
    cfg: VolumeConfig,
    article: dict,
    articles_list: list,
    notes_for_article: List[Footnote],
) -> Optional[str]:
    if article["title"] == "Frontmatter":
        return None

    real = [a for a in articles_list if a["title"] != "Frontmatter"]
    idx = real.index(article)
    prev_a = real[idx - 1] if idx > 0 else None
    next_a = real[idx + 1] if idx + 1 < len(real) else None

    notes_by_n: Dict[int, Footnote] = {fn.n: fn for fn in notes_for_article}

    parts: List[str] = []
    for p in article.get("pages", []):
        parts.append(f'<span class="pb" id="pb-{p["page_num"]}">— Seite {p["page_num"]} —</span>')
        for blk in p["blocks"]:
            if (blk["type"] == "section-header"
                and p["page_num"] == article["page_first"]
                and blk["text"].strip()[:30] == article["title"][:30]):
                continue
            if blk["type"] == "footnote":
                continue
            parts.append(render_inline_block_html(blk, notes_by_n))

    fn_html = ""
    if notes_for_article:
        rows = []
        for fn in notes_for_article:
            rows.append(
                f'<div class="footnote" id="{html.escape(fn.html_id)}">'
                f'<span class="footnote-num">{fn.n}</span>'
                f'<span class="footnote-body">{sanitize_inline_html(fn.text)}</span>'
                f'</div>'
            )
        fn_html = (
            f'<aside class="footnotes" aria-label="Anmerkungen">'
            f'<div class="footnotes-heading">Anmerkungen</div>'
            f'{"".join(rows)}'
            f'</aside>'
        )

    nav_prev = (
        f'<a href="{prev_a["id"]}.html" rel="prev">'
        f'<span class="nav-label">← previous</span>'
        f'<span class="nav-title">{html_escape(prev_a["title"])}</span></a>'
    ) if prev_a else '<span></span>'
    nav_next = (
        f'<a href="{next_a["id"]}.html" rel="next">'
        f'<span class="nav-label">next →</span>'
        f'<span class="nav-title">{html_escape(next_a["title"])}</span></a>'
    ) if next_a else '<span></span>'

    section_html = (
        f'<div class="article-eyebrow">{html_escape(article["section"])}'
        f' · {cfg.volume_title} · {cfg.volume_number_roman} · {cfg.volume_year}</div>'
        if article.get("section")
        else f'<div class="article-eyebrow">{cfg.volume_title} · {cfg.volume_number_roman} · {cfg.volume_year}</div>'
    )

    body = f'''
<article class="article">
  {section_html}
  <h1 class="article-title">{html_escape(article["title"])}</h1>
  {f'<div class="article-author">{html_escape(article["author"])}</div>' if article["author"] else ''}
  <div class="article-body">
    {"".join(parts)}
  </div>
  {fn_html}
</article>
<nav class="article-nav">
  <div class="prev">{nav_prev}</div>
  <div class="next">{nav_next}</div>
</nav>
'''
    return page_chrome(
        cfg,
        f"{article['title']} — {cfg.volume_title} {cfg.volume_number_roman}",
        body,
    )


def _region_overlay_html(page) -> str:
    W = page.get("image_width") or 0
    H = page.get("image_height") or 0
    if W <= 0 or H <= 0:
        return ""
    boxes = []
    for blk in page["blocks"]:
        bb = blk.get("bbox") or [0, 0, 0, 0]
        if len(bb) < 4:
            continue
        x1, y1, x2, y2 = bb[:4]
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue
        left   = max(0.0, min(100.0, x1 / W * 100.0))
        top    = max(0.0, min(100.0, y1 / H * 100.0))
        width  = max(0.0, min(100.0 - left, (x2 - x1) / W * 100.0))
        height = max(0.0, min(100.0 - top,  (y2 - y1) / H * 100.0))
        btype = blk["type"]
        boxes.append(
            f'<a class="region-box" data-type="{html_escape(btype)}" '
            f'data-id="{html_escape(blk["id"])}" '
            f'href="#{html_escape(blk["id"])}" '
            f'style="left:{left:.3f}%;top:{top:.3f}%;'
            f'width:{width:.3f}%;height:{height:.3f}%;" '
            f'title="{html_escape(btype)}">'
            f'<span class="region-box-label">{html_escape(btype)}</span>'
            f'</a>'
        )
    return "".join(boxes)


def build_page_facsimile_html(
    cfg: VolumeConfig,
    page: dict,
    pages: list,
    notes_by_n_by_page: Dict[int, Dict[int, Footnote]],
) -> str:
    pn = page["page_num"]
    idx = next((i for i, p in enumerate(pages) if p["page_num"] == pn), None)
    prev_p = pages[idx - 1] if idx is not None and idx > 0 else None
    next_p = pages[idx + 1] if idx is not None and idx + 1 < len(pages) else None

    notes_by_n = notes_by_n_by_page.get(pn, {})
    blocks_html = "".join(render_block_html(b, notes_by_n) for b in page["blocks"])
    overlay_html = _region_overlay_html(page)

    nav_prev = (
        f'<a href="page-{prev_p["page_num"]:04d}.html" rel="prev" aria-label="previous">←</a>'
    ) if prev_p else '<span aria-hidden="true">←</span>'
    nav_next = (
        f'<a href="page-{next_p["page_num"]:04d}.html" rel="next" aria-label="next">→</a>'
    ) if next_p else '<span aria-hidden="true">→</span>'

    body = f'''
<section class="facsimile-view">
  <div class="facsimile">
    <div class="facsimile-stage" data-show-regions="off">
      <img class="facsimile-img" src="../pages-img/{page["image_filename"]}" alt="Page {pn} facsimile">
      <div class="region-overlay">{overlay_html}</div>
      <button class="region-toggle" type="button" aria-pressed="false"
              aria-label="Toggle layout regions">Show regions</button>
    </div>
  </div>
  <div class="transcription">
    <div class="page-header">
      <div class="page-num">{pn}</div>
      <div class="page-loc">facsimile + transcription</div>
    </div>
    {blocks_html}
  </div>
</section>
<div class="page-nav">
  {nav_prev}
  <span class="current">p. {pn} / {pages[-1]["page_num"]}</span>
  {nav_next}
</div>
'''
    return page_chrome(
        cfg,
        f"Page {pn} — {cfg.volume_title} {cfg.volume_number_roman}",
        body,
    )


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def write_assets(cfg: VolumeConfig, assets_src: Path) -> None:
    """Copy the canonical CSS/JS into the volume's html/assets directory."""
    out_dir = cfg.html_dir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("edition.css", "edition.js"):
        src = assets_src / fname
        dst = out_dir / fname
        shutil.copy(src, dst)

    # Copy page images for facsimile views
    img_dir = cfg.html_dir / "pages-img"
    img_dir.mkdir(parents=True, exist_ok=True)
    for src in cfg.pages_dir.glob("*.png"):
        dst = img_dir / src.name
        if not dst.exists():
            shutil.copy(src, dst)


def run(
    cfg: VolumeConfig,
    articles: list,
    unified: list,
    assets_src: Path,
    footnotes_by_article: Optional[dict] = None,
) -> None:
    write_assets(cfg, assets_src)

    n_crops = make_region_crops(cfg, unified)
    print(f"   {n_crops} visual region crops written")

    # Build the footnote lookup that renderers need: per-page dict of
    # {fn_number → Footnote} and per-block annotation.
    footnotes_by_article = footnotes_by_article or {}
    notes_by_n_by_page: Dict[int, Dict[int, Footnote]] = {}
    # Annotate footnote blocks so render_block_html can use their html_id.
    for art in articles:
        notes = footnotes_by_article.get(art["id"], [])
        notes_by_block_id = {fn.block_id: fn for fn in notes}
        notes_by_n = {fn.n: fn for fn in notes}
        for p in art.get("pages", []):
            for blk in p["blocks"]:
                if blk["type"] == "footnote" and blk["id"] in notes_by_block_id:
                    blk["_fn"] = notes_by_block_id[blk["id"]]
            notes_by_n_by_page.setdefault(p["page_num"], {}).update(notes_by_n)

    # Volume index
    (cfg.html_dir / "index.html").write_text(
        build_volume_index(cfg, articles, unified), encoding="utf-8"
    )

    # Articles
    arts_dir = cfg.html_dir / "articles"
    arts_dir.mkdir(parents=True, exist_ok=True)
    for a in articles:
        notes = footnotes_by_article.get(a["id"], [])
        h = build_article_html(cfg, a, articles, notes)
        if h:
            (arts_dir / f"{a['id']}.html").write_text(h, encoding="utf-8")

    # Page facsimile views
    pages_dir = cfg.html_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for p in tqdm(unified, desc="html-pages", unit="pg"):
        (pages_dir / f"page-{p['page_num']:04d}.html").write_text(
            build_page_facsimile_html(cfg, p, unified, notes_by_n_by_page),
            encoding="utf-8",
        )
    print(f"   index, {len([a for a in articles if a['title'] != 'Frontmatter'])} articles, "
          f"{len(unified)} facsimile pages → {cfg.html_dir}")
