"""LLM-Wiki markdown emission (per-volume layer).

This stage runs after :mod:`pjb_pipeline.emit.graph` and writes one markdown
file per *wiki entity* in the volume:

* one ``volume.md`` — mirror of the ``PublicationVolume`` node, with an
  article-by-article TOC grouped by section
* one ``articles/<art-id>.md`` per detected article — the full body text
  rendered from blocks (page by page), with a frontmatter that **is** the
  ``ScholarlyArticle`` JSON-LD node (so the wiki and the graph stay
  byte-equivalent for structural fields)
* one ``people/<person-slug>.md`` per ``Person`` mentioned in the volume —
  a thin page listing this volume's articles by that author. The corpus-
  level merge (:mod:`scripts.add_volume`) is what aggregates these across
  volumes; per-volume we just emit the slice.
* one ``_context.json`` — a copy of the shared JSON-LD context, so each
  ``.md`` frontmatter can resolve its ``@context`` reference without
  reaching outside the volume directory.

Section and page and footnote and figure *nodes* still live in the graph
(``graph/<slug>.jsonld``) but are not given their own markdown files: they
are navigation artifacts, not wiki entities. Pages appear in the article
body as ``### Page N`` headings; footnotes appear as a ``## Footnotes``
section at the end of each article; figures appear inline as image refs.

The frontmatter is structured so that piping the YAML through
``yaml.safe_load`` and reading the result as a JSON-LD node Just Works.
That round-trip is what makes the strict frontmatter ↔ graph contract
real, rather than aspirational.
"""

from __future__ import annotations

import html as html_module
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from ..config import VolumeConfig
from ..structure.footnotes import Footnote, _PLAIN_DIGIT_REF
from ..structure.toc import TocStructure
from . import graph as graph_emitter
from .html.crops import VISUAL_BLOCK_TYPES
from .jsonld_context import CONTEXT


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

# The four sections an LLM agent is allowed to author (and that we must NOT
# regenerate on subsequent ``add-volume`` runs). The pipeline emits empty
# placeholders for these on first write; the corpus-level merge preserves
# whatever the agent wrote.
AGENT_OWNED_SECTIONS = ("Summary", "Mentions", "Notes")


def _yaml_dump_frontmatter(node: dict) -> str:
    """Dump ``node`` as a YAML frontmatter block.

    The dict is JSON-LD, so keys starting with ``@`` must be quoted to keep
    the YAML valid (PyYAML otherwise accepts them, but explicit quoting is
    safer and round-trips cleanly through other YAML parsers).
    """
    body = yaml.safe_dump(
        node,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,                # don't wrap long URIs
    )
    return f"---\n{body}---\n"


def _read_existing_zones(path: Path) -> Dict[str, str]:
    """Read an existing wiki page and return its agent-owned sections.

    Returns a dict mapping section heading (e.g. ``"Summary"``) to the
    section body (everything between this heading and the next ``##``).
    Used by the per-volume emitter to *preserve* whatever an LLM agent
    wrote in those sections on subsequent runs — the structural parts of
    the page are regenerated, the agent's content is not.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    # Skip the frontmatter — second `---\n` is the closer
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            text = text[end + 5:]
    zones: Dict[str, str] = {}
    # Match level-2 headings; capture name and the body up to the next ##
    pattern = re.compile(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        if name in AGENT_OWNED_SECTIONS:
            zones[name] = m.group(2).rstrip()
    return zones


def _render_agent_section(name: str, existing: Dict[str, str]) -> str:
    """Render one of the agent-owned sections. If the user / LLM has
    written content for it on a previous run, preserve verbatim; otherwise
    drop an italic placeholder so the agent sees the slot exists.
    """
    body = existing.get(name)
    if body and body.strip():
        return f"## {name}\n{body}\n"
    return f"## {name}\n\n*To be added.*\n"


# ---------------------------------------------------------------------------
# Block → markdown
# ---------------------------------------------------------------------------

# Block types that contribute body text. The rest (page-header, page-footer,
# table-of-contents, footnote) are filtered out and either dropped or
# routed elsewhere (footnotes are collected into a separate section).
_SKIP_TYPES = {"page-header", "page-footer", "table-of-contents"}


def _figure_md(blk: dict, label: str) -> str:
    """Inline image reference for a figure block.

    Renders a quoted text marker so the wiki carries no binary image
    payload. If the block has no human-extracted caption text but Chandra
    produced a description (surfaced as ``description`` by
    :mod:`pjb_pipeline.normalize`), we use that — the journal's own
    caption block lives in a separate ``caption``-typed block and will
    follow this one in reading order anyway, so the two read together as
    *marker · Chandra description / Journal caption*.
    """
    desc = (blk.get("text") or "").strip()
    if not desc:
        desc = (blk.get("description") or "").strip()
    body = f"> **[{label} · {blk['id']}]**"
    if desc:
        body += f" {desc}"
    return body + "\n"


def _block_to_md(blk: dict) -> str:
    """Render one block as a markdown fragment ending with a blank line."""
    btype = blk.get("type", "text")
    text = (blk.get("text") or "").strip()

    if btype in _SKIP_TYPES:
        return ""
    if btype == "footnote":
        # Footnotes are collected separately; do not emit inline.
        return ""
    if btype == "section-header":
        return f"#### {text}\n\n" if text else ""
    if btype == "caption":
        return f"*{text}*\n\n" if text else ""
    if btype in VISUAL_BLOCK_TYPES:  # figure / image / diagram
        return _figure_md(blk, btype.capitalize()) + "\n"
    if btype == "table":
        # Chandra often supplies HTML for tables; fall back to text.
        html = (blk.get("html") or "").strip()
        if html:
            return f"{html}\n\n"
        return f"{text}\n\n" if text else ""
    if btype == "equation":
        return f"$$\n{text}\n$$\n\n" if text else ""
    if btype == "bibliography":
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(f"- {ln}" for ln in lines) + "\n\n" if lines else ""
    if btype == "list":
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(f"- {ln}" for ln in lines) + "\n\n" if lines else ""
    # text and any unknown type → paragraph
    return f"{text}\n\n" if text else ""


def _footnote_id(page_num: Optional[int], n: int, *, numbered: bool = True) -> str:
    """Markdown-footnote identifier scoped to the source page.

    Per-page numbering in the corpus means a flat ``[^1]`` would collide
    across pages — so the IDs are ``p018-1``, ``p019-1``, … for numbered
    footnotes, and ``p018-u1``, ``p018-u2``, … for unnumbered ones (the
    asterisk note, the abbreviation list).
    """
    p = "p???" if page_num is None else f"p{int(page_num):03d}"
    return f"{p}-{'' if numbered else 'u'}{n}"


def _insert_footnote_refs(
    text: str,
    page_num: Optional[int],
    page_numbered: set,
) -> str:
    """Rewrite a body chunk so its inline footnote references become
    HTML anchor links to the matching definitions in the footnotes
    section.

    Why HTML anchors and not markdown ``[^id]`` syntax: GitHub's
    markdown renderer hoists every ``[^id]: text`` definition out of
    where it appears and consolidates them into one auto-generated
    "Footnotes" section at the bottom of the rendered page — leaving
    any per-page subheaders ``### Page 18``, ``### Page 19`` … sitting
    in the original location with nothing under them. Manual HTML
    anchors stay where we write them, so the per-page grouping of
    footnote definitions survives the round-trip through GitHub (and
    Obsidian).

    The rewrite is still conservative: it only fires on a plain digit
    that sits between a word and a sentence-end punctuation mark AND
    that matches a real numbered footnote on the same page. Years
    (four digits), citation lists (no spaces around the comma), and
    in-paragraph numbers on pages that happen to have no matching
    footnote all pass through untouched.
    """
    if not page_numbered:
        return text

    def repl(m: "re.Match[str]") -> str:
        n = int(m.group("n"))
        if n not in page_numbered:
            return m.group(0)
        fid = _footnote_id(page_num, n)
        return (
            f'<sup><a id="fnref-{fid}" href="#fn-{fid}">{n}</a></sup>'
        )

    return _PLAIN_DIGIT_REF.sub(repl, text)


_SUP_REF = re.compile(r"<sup>\s*(\d{1,3})\s*</sup>", re.I)
_BLOCK_TAGS = re.compile(r"</?(?:p|div|li|ul|ol|h[1-6]|blockquote)\b[^>]*>|<br\s*/?>", re.I)


def _text_with_sup_refs(blk: dict, page_num: Optional[int], page_numbered: set,
                        elsewhere: Optional[Dict[int, int]] = None) -> Optional[str]:
    """Body text of ``blk`` with Chandra's ``<sup>N</sup>`` note markers
    turned into links (or plain superscripts when no note N exists on the
    page). ``None`` when the block HTML has no superscripts — the caller
    then falls back to :func:`_insert_footnote_refs` on the plain text."""
    h = blk.get("html") or ""
    if "<sup" not in h.lower():
        return None
    h = _SUP_REF.sub(lambda m: f"\ue000{m.group(1)}\ue001", h)
    h = _BLOCK_TAGS.sub(" ", h)
    h = re.sub(r"<[^>]+>", "", h)
    t = html_module.unescape(h)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+(?=\ue000)", "", t)

    def link(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        if n in page_numbered:
            fid = _footnote_id(page_num, n)
            return f'<sup><a id="fnref-{fid}" href="#fn-{fid}">{n}</a></sup>'
        if elsewhere and n in elsewhere:
            # endnotes / a note printed on another page: the number is
            # unique in the article, so link it there
            fid = _footnote_id(elsewhere[n], n)
            return f'<sup><a href="#fn-{fid}">{n}</a></sup>'
        return f"<sup>{n}</sup>"
    return re.sub(r"\ue000(\d{1,3})\ue001", link, t)


def _render_footnotes_section(notes: List[Footnote]) -> str:
    """Render the article's footnotes, grouped by source page.

    Each numbered footnote becomes a paragraph with an HTML anchor
    target (``<a id="fn-p018-1"></a>``) and a back-link arrow
    (``↩``) pointing to the inline reference. Unnumbered footnotes
    (the asterisk note, the abbreviation key list) get an anchor but
    no back-link since nothing in the body refers to them.

    The per-page ``### Page N`` subheaders keep their contents in
    place because HTML anchors are not subject to the hoisting that
    markdown ``[^id]: text`` definitions are.
    """
    if not notes:
        return ""
    by_page: Dict[Optional[int], List[Footnote]] = {}
    for fn in notes:
        by_page.setdefault(fn.page_num, []).append(fn)

    parts: List[str] = ["\n## Footnotes\n"]
    for page in sorted(by_page.keys(), key=lambda p: (p is None, p)):
        if page is not None:
            parts.append(f"\n### Page {page}\n\n")
        else:
            parts.append("\n### Unattributed\n\n")
        for fn in by_page[page]:
            text = (fn.text or "").strip().replace("\n", " ")
            fid = _footnote_id(fn.page_num, fn.n, numbered=fn.is_numbered)
            if fn.is_numbered:
                parts.append(
                    f'<a id="fn-{fid}"></a><sup>{fn.n}</sup>\u2003{text} '
                    f'<a href="#fnref-{fid}">\u21a9</a>\n\n'
                )
            else:
                parts.append(f'<a id="fn-{fid}"></a>{text}\n\n')
    return "".join(parts)


# Block types that are *floats*: they may sit in the middle of a sentence
# that runs on in the next text block (a figure placed between two columns,
# a caption, a table). They are emitted after the paragraph they interrupt.
_FLOAT_TYPES = set(VISUAL_BLOCK_TYPES) | {"caption", "table", "equation", "code"}
_PROSE_TYPES = {"text", "list", "bibliography"}

_HYPHEN_END = re.compile(r"[a-zäöüß]-$")
_SENTENCE_END = re.compile(r"[.!?:][\"'“”»«’)\]]*$")


# Words a sentence fragment can end on but a heading cannot.
_CONTINUATION_WORDS = {
    "der", "die", "das", "dem", "den", "des", "ein", "eine", "einer", "eines",
    "einem", "einen", "und", "oder", "aber", "von", "vom", "zu", "zum", "zur",
    "im", "in", "an", "am", "mit", "auf", "für", "bei", "nach", "über", "unter",
    "vor", "durch", "als", "wie", "dass", "daß", "sich", "nicht", "auch", "so",
    "wurde", "wurden", "war", "waren", "ist", "sind", "hat", "hatte", "haben",
}


def page_marker(page_num: int) -> str:
    """Anchor + visible marker for the start of a (PDF) page."""
    return f'<a id="page-{int(page_num)}"></a>*[p. {int(page_num)}]*'


def _text_tail(md: str) -> str:
    """Last characters of a rendered chunk without footnote-ref markup."""
    t = re.sub(r"<sup>.*?</sup>", "", md).rstrip()
    t = re.sub(r"(?<=[^\d\s])\s+\d{1,3}$", "", t)   # trailing plain ref "… Ende 12"
    return t.rstrip()


def _first_alpha(md: str) -> str:
    for c in re.sub(r"<[^>]+>", "", md).lstrip()[:12]:
        if c.isalpha():
            return c
        if c.isdigit():
            return ""
    return ""


def _continues(prev_md: str, next_md: str) -> Optional[str]:
    """How ``next_md`` continues ``prev_md``: ``"hyphen"`` (join without
    space, drop the hyphen), ``"hyphen-keep"`` (compound like
    "Böhmerwald-" + "Liedes"), ``"space"`` (mid-sentence join) or ``None``
    (new paragraph)."""
    a = _text_tail(prev_md)
    if len(a) < 25 or not next_md.strip():
        return None
    f = _first_alpha(next_md)
    words = a.split()
    if (len(words) <= 6 and f.isupper() and not _HYPHEN_END.search(a)
            and words[-1].lower().strip(",;") not in _CONTINUATION_WORDS):
        return None   # a heading-like line ("Architektur und Baugeschichte")
    if _HYPHEN_END.search(a):
        if f.islower():
            return "hyphen"
        return "hyphen-keep" if f else None
    if _SENTENCE_END.search(a):
        return None
    core = a.rstrip("\"'“”»«’)]")      # "… und Pasterwiz'" / "… „Zitat“"
    last = core[-1] if core else ""
    if last.isalnum() or last in ",;–—-(":
        return "space"
    return None


def _render_article_body(article: dict, footnotes: List[Footnote]) -> str:
    """Render the article's running text, then its footnotes.

    The body is one continuous text, not one section per page:

    * **Paragraph stitching.** Chandra cuts a paragraph wherever a column
      or a page ends. Consecutive text blocks are joined when the first
      ends mid-sentence — a typographic hyphen is removed ("verlie-" +
      "henen" → "verliehenen"), a compound hyphen kept ("Böhmerwald-" +
      "Liedes").
    * **Inline page markers.** Each page start is marked with an anchor and
      ``*[p. N]*`` at the exact place in the text — inside a sentence if
      the sentence runs across the page break.
    * **Floats.** A figure, caption or table that interrupts a sentence is
      moved behind the end of that paragraph.
    * **Footnote refs** in body text become links to the per-page footnote
      list at the end (see :func:`_insert_footnote_refs`).

    The title heading and author byline at the start of the article are
    skipped — they are the page's H1 and author line already.
    """
    from ..structure.footnotes import numbered_footnotes_by_page
    page_numbered = numbered_footnotes_by_page(footnotes)
    skip_ids = set(article.get("_skip_block_ids") or ())
    # note number → page, for numbers that occur once in the article
    counts: Dict[int, int] = {}
    for fn in footnotes:
        if fn.is_numbered:
            counts[fn.n] = counts.get(fn.n, 0) + 1
    unique_note_page = {fn.n: fn.page_num for fn in footnotes
                        if fn.is_numbered and counts[fn.n] == 1 and fn.page_num is not None}

    paragraphs: List[str] = []      # finished markdown chunks
    open_par: Optional[str] = None  # current prose paragraph (may grow)
    deferred: List[str] = []        # floats waiting for open_par to close
    pending_marker: Optional[str] = None

    def close_paragraph():
        nonlocal open_par, deferred, pending_marker
        if open_par is not None:
            paragraphs.append(open_par)
            open_par = None
        if deferred:
            if pending_marker:
                # the page began among the held-back floats
                paragraphs.append(pending_marker)
                pending_marker = None
            paragraphs.extend(deferred)
            deferred = []

    for pg in article.get("pages", []):
        page_num = pg["page_num"]
        page_set = page_numbered.get(page_num, set())
        if pending_marker:
            # previous page had no text to carry its marker (floats only)
            (deferred if open_par is not None else paragraphs).append(pending_marker)
        pending_marker = page_marker(page_num)
        for blk in pg.get("blocks", []):
            if blk.get("id") in skip_ids:
                continue
            chunk = _block_to_md(blk).strip()
            if not chunk:
                continue
            btype = blk.get("type")
            if btype in _PROSE_TYPES:
                # Only rewrite refs inside body text blocks — never inside
                # footnote bodies, captions, tables or headings.
                sup = (_text_with_sup_refs(blk, page_num, page_set, unique_note_page)
                       if btype == "text" else None)
                chunk = sup if sup else _insert_footnote_refs(chunk, page_num, page_set)
                how = _continues(open_par, chunk) if (open_par is not None and btype == "text") else None
                if how:
                    marker = f" {pending_marker} " if pending_marker else " "
                    if how in ("hyphen", "hyphen-keep"):
                        # complete the word first, then mark the page:
                        # "Land-" + "gerichts …" → "Landgerichts *[p. 113]* …"
                        head = open_par.rstrip()
                        if how == "hyphen":
                            head = head[:-1]
                        word, _, rest = chunk.lstrip().partition(" ")
                        open_par = (head + word
                                    + (f" {pending_marker}" if pending_marker else "")
                                    + (" " + rest.lstrip() if rest.strip() else ""))
                    else:
                        open_par = open_par.rstrip() + marker + chunk.lstrip()
                    pending_marker = None
                    continue
                close_paragraph()
                if pending_marker:
                    paragraphs.append(pending_marker)
                    pending_marker = None
                open_par = chunk
                continue
            if btype in _FLOAT_TYPES and open_par is not None and \
                    _continues(open_par, "x") is not None:
                # the sentence runs on after this float: hold it back (the
                # page marker stays pending for the continuing text)
                deferred.append(chunk)
                continue
            close_paragraph()
            if pending_marker:
                paragraphs.append(pending_marker)
                pending_marker = None
            paragraphs.append(chunk)
    close_paragraph()

    body = "\n\n".join(p.strip() for p in paragraphs if p.strip()).rstrip() + "\n"
    body += _render_footnotes_section(footnotes)
    return body


# ---------------------------------------------------------------------------
# Volume / article / person markdown
# ---------------------------------------------------------------------------

def _write_context_copy(cfg: VolumeConfig) -> None:
    """Drop a copy of the shared JSON-LD ``@context`` at the wiki root.

    Each markdown file's frontmatter references ``../_context.json``;
    having it locally means the per-volume wiki is self-contained and can
    be inspected (or merged) without reaching back into the package.
    """
    out = cfg.wiki_dir / "_context.json"
    out.write_text(
        json.dumps({"@context": CONTEXT}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _node_by_id(graph_doc: dict, iri: str) -> Optional[dict]:
    for n in graph_doc.get("@graph", []):
        if n.get("@id") == iri:
            return n
    return None


def _nodes_by_type(graph_doc: dict, type_name: str) -> List[dict]:
    def has(n):
        t = n.get("@type")
        return t == type_name or (isinstance(t, list) and type_name in t)
    return [n for n in graph_doc.get("@graph", []) if has(n)]


def _strip_context(node: dict) -> dict:
    """Return a copy of ``node`` with our per-file ``@context`` reference
    prepended. We point ``@context`` at the local file rather than inline
    the whole dict in every frontmatter."""
    out = {"@context": "../_context.json"}
    out.update(node)
    return out


def _volume_frontmatter(cfg: VolumeConfig, vol_node: dict) -> dict:
    out = {"@context": "./_context.json"}
    out.update(vol_node)
    return out


def _write_volume_md(cfg: VolumeConfig, vol_node: dict, articles: List[dict],
                     sections_order: List[str]) -> None:
    """Write ``wiki/volume.md`` — the PublicationVolume page with TOC."""
    fm = _yaml_dump_frontmatter(_volume_frontmatter(cfg, vol_node))

    title_line = (
        f"# {cfg.volume_title} {cfg.volume_number_roman} "
        f"({cfg.volume_year})\n"
    )
    blurb = (
        f"\nVolume {cfg.volume_number_roman} ({cfg.volume_year}) of the "
        f"*{cfg.series_name}*, published by {cfg.publisher}.\n"
    )

    # TOC, grouped by section in the order seen in the volume
    by_section: Dict[str, List[dict]] = {}
    for a in articles:
        if a["title"] == "Frontmatter":
            continue
        sec = a.get("section") or "—"
        by_section.setdefault(sec, []).append(a)

    toc_parts: List[str] = ["\n## Articles\n\n"]
    seen = set()
    section_order: List[str] = []
    for sec in sections_order:
        if sec in by_section and sec not in seen:
            section_order.append(sec)
            seen.add(sec)
    for sec in by_section:
        if sec not in seen:
            section_order.append(sec)

    for sec in section_order:
        toc_parts.append(f"### {sec}\n\n")
        for a in by_section[sec]:
            author = _display_authors(a)
            byline = f" — {author}" if author else ""
            if a.get("review"):
                byline = f" — review by {author}" if author else " — review"
            pages = f"pp. {a['page_first']}–{a['page_last']}"
            toc_parts.append(
                f"{a['num']}. [{a['title']}](articles/{a['id']}.md){byline} · {pages}\n"
            )
        toc_parts.append("\n")

    existing = _read_existing_zones(cfg.wiki_dir / "volume.md")
    agent_sections = "\n" + "\n".join(
        _render_agent_section(name, existing) for name in AGENT_OWNED_SECTIONS
    )

    (cfg.wiki_dir / "volume.md").write_text(
        fm + "\n" + title_line + blurb + "".join(toc_parts) + agent_sections,
        encoding="utf-8",
    )


def _write_article_md(cfg: VolumeConfig, art_node: dict, article: dict,
                      footnotes: List[Footnote]) -> None:
    out_path = cfg.wiki_dir / "articles" / f"{article['id']}.md"
    fm = _yaml_dump_frontmatter(_strip_context(art_node))

    title = article["title"]
    author = _display_authors(article)
    section = article.get("section") or "—"
    page_span = f"pp. {article['page_first']}–{article['page_last']}"
    header = f"# {title}\n\n"
    meta_line = "**" + (author if author else "—") + f"** · {section} · {page_span}\n"
    rev = article.get("review")
    if rev:
        who = []
        if rev.get("authors"):
            who.append(" / ".join(rev["authors"]))
        if rev.get("editors"):
            who.append(" / ".join(rev["editors"]) + " (ed.)")
        reviewed = (", ".join(who) + ": " if who else "") + (rev.get("title") or "")
        meta_line += f"\nReview of: *{reviewed.strip()}*\n"
    elif article.get("editors"):
        meta_line += f"\nEdited by: {' / '.join(article['editors'])}\n"

    existing = _read_existing_zones(out_path)
    summary = _render_agent_section("Summary", existing)
    mentions = _render_agent_section("Mentions", existing)
    notes = _render_agent_section("Notes", existing)

    body = _render_article_body(article, footnotes)
    full_text = "## Full Text\n\n" + body

    parts = [
        fm,
        "\n",
        header,
        meta_line,
        "\n",
        summary,
        "\n",
        mentions,
        "\n",
        full_text,
        "\n",
        notes,
    ]
    out_path.write_text("".join(parts), encoding="utf-8")


def _write_person_md(cfg: VolumeConfig, person_node: dict,
                     articles_by_person: Dict[str, List[dict]]) -> None:
    """Write a per-volume Person page.

    The page lists this volume's articles by that author. The corpus
    merger (``add-volume``) will aggregate same-IRI Person pages across
    volumes — appending to the "Appears in" section while leaving the
    agent-owned sections alone.
    """
    person_iri = person_node["@id"]
    slug = person_iri.split("pjb:person/", 1)[-1]
    out_path = cfg.wiki_dir / "people" / f"{slug}.md"

    fm = _yaml_dump_frontmatter(_strip_context(person_node))
    name = person_node.get("name", slug)

    arts = articles_by_person.get(person_iri, [])
    appearances = [f"\n## Appears in\n\n"]
    appearances.append(
        f"### {cfg.volume_title} {cfg.volume_number_roman} ({cfg.volume_year})\n\n"
    )
    role_note = {
        "author": "",
        "editor": " — as editor",
        "reviewed-author": " — reviewed book",
        "reviewed-editor": " — reviewed book (editor)",
    }
    if arts:
        for a, role in arts:
            section = a.get("section") or "—"
            pages = f"pp. {a['page_first']}–{a['page_last']}"
            if a.get("review") and role == "author":
                note = " — review"
            else:
                note = role_note.get(role, "")
            appearances.append(
                f"- [{a['title']}](../articles/{a['id']}.md) "
                f"({section}, {pages}){note}\n"
            )
    else:
        appearances.append("*(no articles found — this should not happen)*\n")

    existing = _read_existing_zones(out_path)
    agent_sections = "\n" + "\n".join(
        _render_agent_section(n, existing) for n in AGENT_OWNED_SECTIONS
    )

    out_path.write_text(
        fm + "\n" + f"# {name}\n" + "".join(appearances) + agent_sections,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Person ↔ articles index
# ---------------------------------------------------------------------------

def _articles_by_person(articles: List[dict]) -> Dict[str, List[tuple]]:
    """Index ``(article, role)`` pairs by Person IRI.

    Uses :func:`pjb_pipeline.emit.graph.article_people` and
    :func:`pjb_pipeline.emit.graph.person_iri` so the IRIs match what the
    graph emitter produced — the wiki must use the same Person identity
    as the graph node, or the round-trip breaks.
    """
    idx: Dict[str, List[tuple]] = {}
    for a in articles:
        if a["title"] == "Frontmatter":
            continue
        seen = set()
        for name, role in graph_emitter.article_people(a):
            iri = graph_emitter.person_iri(name)
            if (iri, a["id"]) in seen:
                continue
            seen.add((iri, a["id"]))
            idx.setdefault(iri, []).append((a, role))
    return idx


def _display_authors(article: dict) -> str:
    return " / ".join(graph_emitter.article_authors(article))


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(
    cfg: VolumeConfig,
    articles: List[dict],
    pages: List[dict],
    toc: Optional[TocStructure] = None,
    footnotes_by_article: Optional[dict] = None,
    refs_by_article: Optional[dict] = None,
) -> None:
    """Emit the per-volume wiki tree.

    Builds the same JSON-LD graph as :func:`pjb_pipeline.emit.graph.run`
    (calling it directly) so the frontmatter on every markdown page is
    *the* node from the graph, not a parallel reconstruction.
    """
    # Ensure directories
    (cfg.wiki_dir / "articles").mkdir(parents=True, exist_ok=True)
    (cfg.wiki_dir / "people").mkdir(parents=True, exist_ok=True)

    # Drop the shared context next to the markdown files.
    _write_context_copy(cfg)

    # Build the graph (same call the graph emitter makes). Sharing this
    # call means every frontmatter is byte-identical to the graph node.
    doc = graph_emitter.build_volume_graph(
        cfg, articles, pages, toc,
        footnotes_by_article=footnotes_by_article,
        refs_by_article=refs_by_article,
    )

    # Index live articles by id so we can look them up when dispatching
    # ScholarlyArticle nodes.
    articles_by_id = {a["id"]: a for a in articles}
    arts_by_person = _articles_by_person(articles)

    # Sections order: keep the volume's TOC order (used for the volume
    # page's TOC). Falls back to the order articles were detected in.
    sections_order: List[str] = []
    if toc and getattr(toc, "sections", None):
        for name, _ in toc.sections:
            if name not in sections_order:
                sections_order.append(name)
    for a in articles:
        sec = a.get("section")
        if sec and sec not in sections_order:
            sections_order.append(sec)

    n_articles = 0
    n_people = 0

    # 1) Volume page
    vol_node = _node_by_id(doc, graph_emitter.volume_iri(cfg))
    if vol_node:
        _write_volume_md(cfg, vol_node, articles, sections_order)

    # 2) Article pages
    written = set()
    for node in _nodes_by_type(doc, "ScholarlyArticle"):
        art_id = node["@id"].split("pjb:art/", 1)[-1]
        art = articles_by_id.get(art_id)
        if art is None:
            continue
        notes = (footnotes_by_article or {}).get(art_id, []) or []
        _write_article_md(cfg, node, art, notes)
        written.add(cfg.wiki_dir / "articles" / f"{art_id}.md")
        n_articles += 1

    # 3) Person pages
    for node in _nodes_by_type(doc, "Person"):
        _write_person_md(cfg, node, arts_by_person)
        written.add(cfg.wiki_dir / "people" / f"{node['@id'].split('pjb:person/', 1)[-1]}.md")
        n_people += 1

    # Pages of an earlier run that this run no longer produces (changed
    # article boundaries, corrected author names) must not linger in the
    # staging directory — add-volume copies everything it finds there.
    for sub in ("articles", "people"):
        for f in (cfg.wiki_dir / sub).glob("*.md"):
            if f not in written:
                f.unlink()

    print(f"   wrote {cfg.wiki_dir}  "
          f"(volume.md + {n_articles} articles, {n_people} people)")
