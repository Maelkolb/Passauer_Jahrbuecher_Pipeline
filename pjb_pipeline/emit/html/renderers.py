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
# Rich-content sanitiser (for Chandra v2's HTML payload)
# ---------------------------------------------------------------------------

# Tags that may appear inside a Chandra ``content`` HTML fragment. Everything
# else gets unwrapped (its text content is kept, the tag is dropped).
_RICH_ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "colgroup", "col",
    "ul", "ol", "li",
    "sup", "sub", "small",
    "b", "i", "em", "strong", "u", "s",
    "br", "span", "mark",
    "img", "figure", "figcaption",
    "code", "pre", "blockquote",
    "math", "mrow", "mi", "mn", "mo", "mfrac", "msup", "msub", "msqrt",
    "div",
    "a",
}

# Per-tag allow-list of attributes. ``*`` is the default for unlisted tags.
_RICH_ALLOWED_ATTRS = {
    "img":  ["src", "alt", "loading", "class"],
    "a":    ["href", "class", "id"],
    "td":   ["colspan", "rowspan", "class"],
    "th":   ["colspan", "rowspan", "scope", "class"],
    "col":  ["span"],
    "sup":  ["class", "id"],
    "div":  ["class"],
    "span": ["class"],
    "*":    [],
}


def sanitize_block_html(html_fragment: str) -> str:
    """Run a Chandra-emitted HTML fragment through a tag/attr allow-list.

    Drops anything we don't recognise (script/style/iframe/event handlers)
    but keeps the structural content — `<h2>`, `<table>`, `<sup>`, etc."""
    if not html_fragment:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # No bs4 — fall back to plain escape (loses structure but is safe).
        return html.escape(html_fragment, quote=False)

    soup = BeautifulSoup(html_fragment, "html.parser")
    for tag in list(soup.find_all(True)):
        if tag.name not in _RICH_ALLOWED_TAGS:
            tag.unwrap()
            continue
        allowed = _RICH_ALLOWED_ATTRS.get(tag.name, _RICH_ALLOWED_ATTRS["*"])
        for attr in list(tag.attrs.keys()):
            if attr not in allowed:
                del tag.attrs[attr]
    return str(soup)


def _inject_fn_refs_into_html(
    html_fragment: str,
    notes_by_n: Dict[int, Footnote],
) -> str:
    """Walk ``html_fragment``'s text nodes and replace each ``Nfootnote-marker``
    pattern with an anchor linking to the resolved footnote block.

    Handles two marker styles:
      * ``<sup>1</sup>`` — Chandra v2's typical encoding
      * inline ``1^`` in plain text — older chandra / our previous tests
    """
    if not html_fragment or not notes_by_n:
        return html_fragment
    try:
        from bs4 import BeautifulSoup, NavigableString
    except ImportError:
        return html_fragment

    soup = BeautifulSoup(html_fragment, "html.parser")

    # 1) Wrap each <sup>N</sup> in a link to the corresponding footnote
    for sup in list(soup.find_all("sup")):
        # If the user already wrapped it in <a>, leave it
        if sup.find("a"):
            continue
        inner = (sup.get_text() or "").strip()
        if not inner.isdigit():
            continue
        n = int(inner)
        target = notes_by_n.get(n)
        if not target:
            sup["class"] = (sup.get("class") or []) + ["fn-ref"]
            continue
        a = soup.new_tag("a", href=f"#{target.html_id}")
        a.string = str(n)
        sup.clear()
        sup["class"] = (sup.get("class") or []) + ["fn-ref"]
        sup.append(a)

    # 2) Replace the inline ``N^`` pattern in raw text nodes
    import re as _re
    pat = _re.compile(r"(?<!\d)(\d{1,3})\^")
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString):
            continue
        text = str(node)
        if "^" not in text:
            continue
        new_pieces = []
        last = 0
        any_match = False
        for m in pat.finditer(text):
            any_match = True
            new_pieces.append(text[last:m.start()])
            n = int(m.group(1))
            target = notes_by_n.get(n)
            sup_tag = soup.new_tag("sup", **{"class": "fn-ref"})
            if target:
                a = soup.new_tag("a", href=f"#{target.html_id}")
                a.string = str(n)
                sup_tag.append(a)
            else:
                sup_tag.string = str(n)
            new_pieces.append(sup_tag)
            last = m.end()
        if not any_match:
            continue
        new_pieces.append(text[last:])
        # Replace the text node with the new mixed-content sequence
        for piece in new_pieces:
            if isinstance(piece, str):
                if piece:
                    node.insert_before(NavigableString(piece))
            else:
                node.insert_before(piece)
        node.extract()

    return str(soup)


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
    """Render one block for the per-page transcription column.

    Strategy depends on whether Chandra returned rich HTML for this block:

    * **rich HTML available** (Chandra 2 path) — sanitise it and embed
      directly, so tables stay as ``<table>``, headers as ``<h2>``, etc.
      Footnote references get woven into the HTML in place.

    * **plain text only** (older Chandra / fallback) — apply the old
      pipeline: HTML-escape, replace newlines with ``<br>``, inject
      footnote refs.
    """
    bid = html_escape(blk["id"])
    btype = blk["type"]
    bbox = ",".join(str(v) for v in blk["bbox"])
    col = blk.get("_column")
    col_attr = f' data-column="{col}"' if col is not None else ' data-column="span"'
    notes_by_n = notes_by_n or {}

    # Visual regions: embed the actual crop with the rich caption HTML
    # alongside (Chandra 2's content for a Picture usually includes the
    # caption text inside the same fragment).
    if btype in VISUAL_BLOCK_TYPES:
        rich_html = blk.get("html") or ""
        rich_html = sanitize_block_html(rich_html)
        if blk.get("_crop"):
            rel = f"../../{REGIONS_DIR_NAME}/{blk['_crop']}"
            cap_text = blk.get("text", "") or ""
            alt = alt_text(cap_text) or html_escape(btype)
            inner = f'<img src="{rel}" alt="{alt}" loading="lazy">'
            if rich_html and rich_html.strip() not in ("", "<p></p>"):
                inner += f'<div class="region-caption">{rich_html}</div>'
            return (
                f'<div class="region" data-type="{btype}" data-bbox="{bbox}" '
                f'id="{bid}"{col_attr}>{inner}</div>'
            )
        # No crop saved — render whatever rich HTML chandra gave us
        if rich_html:
            return (
                f'<div class="region" data-type="{btype}" data-bbox="{bbox}" '
                f'id="{bid}"{col_attr}>{rich_html}</div>'
            )
        # Final fallback: a placeholder
        return (
            f'<div class="region" data-type="{btype}" data-bbox="{bbox}" '
            f'id="{bid}"{col_attr}><em>[{btype}]</em></div>'
        )

    # Footnote bodies — use the chandra HTML for typography preservation.
    # Chandra's rich HTML already contains the footnote number as part of
    # the transcribed text (e.g. "<p>1. ...</p>"); previous versions of
    # this renderer *also* prepended our own ``<span class="footnote-num">``
    # element, which caused every footnote to be numbered twice. We now
    # trust Chandra's numbering when rich HTML is available and only
    # synthesise our own marker on the plain-text fallback path (where
    # ``fn.text`` has the leading number stripped by
    # ``parse_footnote_body``).
    if btype == "footnote" and blk.get("_fn"):
        fn = blk["_fn"]
        rich = blk.get("html") or ""
        if rich:
            body_html = sanitize_block_html(rich)
            return (
                f'<div class="region" data-type="{btype}" data-bbox="{bbox}" '
                f'id="{html.escape(fn.html_id)}"{col_attr}>'
                f'{body_html}'
                f'</div>'
            )
        body_html = sanitize_inline_html(fn.text).replace("\n", "<br>")
        return (
            f'<div class="region" data-type="{btype}" data-bbox="{bbox}" '
            f'id="{html.escape(fn.html_id)}"{col_attr}>'
            f'<span class="footnote-num">{fn.n}</span> {body_html}'
            f'</div>'
        )

    # All other block types — TOC, section header, paragraph, table,
    # list, equation, code, page-header, page-footer, …
    rich_html = blk.get("html") or ""
    if rich_html:
        body = sanitize_block_html(rich_html)
        # Wire footnote refs into the rich HTML in place
        if notes_by_n:
            body = _inject_fn_refs_into_html(body, notes_by_n)
    else:
        body = _text_with_fn_refs(blk.get("text", ""), notes_by_n)

    extra_class = ""
    if btype == "table-of-contents":
        extra_class = " toc-block"
    return (
        f'<div class="region{extra_class}" data-type="{btype}" '
        f'data-bbox="{bbox}" id="{bid}"{col_attr}>{body}</div>'
    )


def render_inline_block_html(
    blk,
    notes_by_n: Optional[Dict[int, Footnote]] = None,
) -> str:
    """Render a block as part of the continuous article-reading view.

    Same strategy as ``render_block_html``: prefer Chandra's rich HTML when
    we have it (preserves tables, headers, inline emphasis), fall back to
    the plain-text path otherwise.
    """
    btype = blk["type"]
    notes_by_n = notes_by_n or {}

    if btype == "footnote":
        return ""  # collected separately
    if btype in ("page-header", "page-footer"):
        return ""  # suppress in reading view

    rich_html = blk.get("html") or ""

    # Visual regions — prefer the saved crop; fall back to chandra's HTML.
    if btype in VISUAL_BLOCK_TYPES:
        cap_text = blk.get("text", "") or ""
        cap_html = sanitize_inline_html(cap_text) if cap_text else btype.title()
        alt = alt_text(cap_text) or html_escape(btype)
        if blk.get("_crop"):
            rel = f"../../{REGIONS_DIR_NAME}/{blk['_crop']}"
            return (
                f'<figure class="visual-region {btype}">'
                f'<img src="{rel}" alt="{alt}" loading="lazy">'
                f'<figcaption>{cap_html}</figcaption>'
                f'</figure>'
            )
        if rich_html:
            return f'<figure class="visual-region {btype}">{sanitize_block_html(rich_html)}</figure>'
        return f'<figure><figcaption><em>[{btype}]</em> {cap_html}</figcaption></figure>'

    # All other block types: use chandra's rich HTML when available.
    if rich_html:
        body = sanitize_block_html(rich_html)
        if notes_by_n:
            body = _inject_fn_refs_into_html(body, notes_by_n)
        # Suppress the table-of-contents block in the article reading view;
        # it's already linked from the volume index.
        if btype == "table-of-contents":
            return ""
        return body

    # Plain-text fallback path (older chandra)
    text = sanitize_inline_html(blk.get("text", ""))
    if btype == "section-header":
        return f"<h2>{text}</h2>"
    if btype == "caption":
        return f"<figure><figcaption>{text}</figcaption></figure>"
    if btype == "table":
        return f"<table><tr><td>{text}</td></tr></table>"
    if btype == "bibliography":
        items = "".join(
            f"<li>{sanitize_inline_html(line)}</li>"
            for line in blk["text"].split("\n") if line.strip()
        )
        return f'<ol class="bibl">{items}</ol>'
    if btype == "table-of-contents":
        return ""  # suppress; volume index already has the structured TOC
    if btype == "equation":
        return f'<p class="equation">{text}</p>'
    if btype == "list":
        items = "".join(
            f"<li>{sanitize_inline_html(line)}</li>"
            for line in blk["text"].split("\n") if line.strip()
        )
        return f"<ul>{items}</ul>"

    text_html = _text_with_fn_refs(blk.get("text", ""), notes_by_n)
    return f"<p>{text_html}</p>"


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


def _render_transcription_body(
    page: dict,
    notes_by_n: Dict[int, Footnote],
) -> str:
    """Render the transcription side of the facsimile view, using a 2-column
    layout when the page's blocks suggest the original was typeset in two
    columns.

    When the page has a mix of column blocks and spanning blocks (section
    headers, page header/footer, wide figures), we use a *band* layout:
    each band of column content gets its own ``<div class="trans-columns">``
    grid, and spanning blocks slot in at their original vertical position
    between two such bands. That way a section header in the middle of the
    page stays in the middle, instead of being pinned to the top.
    """
    from ...structure.columns import (
        detect_columns, assign_columns, band_layout,
    )

    columns = detect_columns(page)
    annotated = assign_columns(page, columns)

    if not columns:
        # Single-column: just stack everything top-to-bottom
        ordered = sorted(annotated, key=lambda b: b["bbox"][1])
        return "".join(render_block_html(b, notes_by_n) for b in ordered)

    n_cols = len(columns)
    parts: List[str] = []
    for kind, band_blocks in band_layout(annotated, columns):
        if kind == "spanning":
            parts.append('<div class="trans-span">')
            for b in band_blocks:
                parts.append(render_block_html(b, notes_by_n))
            parts.append("</div>")
        else:  # "columns"
            col_groups: List[List[dict]] = [[] for _ in range(n_cols)]
            for b in band_blocks:
                col = b.get("_column")
                if col is None or not (0 <= col < n_cols):
                    # Defensive: shouldn't happen since band_layout already
                    # separated spanning blocks out.
                    continue
                col_groups[col].append(b)
            parts.append('<div class="trans-columns">')
            for i, cb in enumerate(col_groups):
                parts.append(f'<div class="trans-col" data-col="{i}">')
                for b in cb:
                    parts.append(render_block_html(b, notes_by_n))
                parts.append("</div>")
            parts.append("</div>")
    return "".join(parts)


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
    blocks_html = _render_transcription_body(page, notes_by_n)
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
    <div class="facsimile-stage" data-show-regions="on">
      <img class="facsimile-img" src="../pages-img/{page["image_filename"]}" alt="Page {pn} facsimile">
      <div class="region-overlay">{overlay_html}</div>
      <button class="region-toggle" type="button" aria-pressed="true"
              aria-label="Toggle layout regions">Hide regions</button>
    </div>
  </div>
  <div class="transcription">
    <div class="page-header">
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
