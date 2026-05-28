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

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from ..config import VolumeConfig
from ..structure.footnotes import Footnote
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


def _figure_md(blk: dict, alt: str) -> str:
    """Inline image reference for a figure block.

    The image path is relative to the *article* markdown file's location
    (``wiki/articles/<art>.md``), which is two levels deep from the volume
    root, so we hop up two directories to reach ``regions/``.
    """
    fname = (blk.get("_crop") or f"{blk['id']}.png")
    return f"![{alt}](../../regions/{fname})\n"


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
        alt = text or btype
        return _figure_md(blk, alt) + "\n"
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


def _render_article_body(article: dict, footnotes: List[Footnote]) -> str:
    """Render the article's body (per-page) and footnotes (collected).

    The body is structured as ``### Page N`` followed by the page's blocks
    in document order. The first page typically opens with the article's
    section-header repeating the title (which the TEI emitter strips); we
    keep it here because the markdown reader has the article title only
    once, in the H1 above the body, and a duplicate in-flow heading is
    survivable.
    """
    parts: List[str] = []
    for pg in article.get("pages", []):
        parts.append(f"### Page {pg['page_num']}\n\n")
        for blk in pg.get("blocks", []):
            chunk = _block_to_md(blk)
            if chunk:
                parts.append(chunk)
    body = "".join(parts).rstrip() + "\n"

    if footnotes:
        parts_fn = ["\n## Footnotes\n\n"]
        for fn in footnotes:
            text = (fn.text or "").strip().replace("\n", " ")
            parts_fn.append(f"{fn.n}. {text}\n")
        body += "".join(parts_fn)

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
    return [n for n in graph_doc.get("@graph", []) if n.get("@type") == type_name]


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
            author = a.get("author", "").strip()
            byline = f" — {author}" if author else ""
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
    author = (article.get("author") or "").strip()
    section = article.get("section") or "—"
    page_span = f"pp. {article['page_first']}–{article['page_last']}"
    header = f"# {title}\n\n"
    meta_line = "**" + (author if author else "—") + f"** · {section} · {page_span}\n"

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
    if arts:
        for a in arts:
            section = a.get("section") or "—"
            pages = f"pp. {a['page_first']}–{a['page_last']}"
            appearances.append(
                f"- [{a['title']}](../articles/{a['id']}.md) "
                f"({section}, {pages})\n"
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

def _articles_by_person(articles: List[dict]) -> Dict[str, List[dict]]:
    """Index articles by Person IRI.

    Uses :func:`pjb_pipeline.emit.graph._split_authors` and
    :func:`pjb_pipeline.emit.graph.person_iri` so the IRIs match what the
    graph emitter produced — the wiki must use the same Person identity
    as the graph node, or the round-trip breaks.
    """
    idx: Dict[str, List[dict]] = {}
    for a in articles:
        if a["title"] == "Frontmatter":
            continue
        for name in graph_emitter._split_authors(a.get("author", "")):
            iri = graph_emitter.person_iri(name)
            idx.setdefault(iri, []).append(a)
    return idx


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
    for node in _nodes_by_type(doc, "ScholarlyArticle"):
        art_id = node["@id"].split("pjb:art/", 1)[-1]
        art = articles_by_id.get(art_id)
        if art is None:
            continue
        notes = (footnotes_by_article or {}).get(art_id, []) or []
        _write_article_md(cfg, node, art, notes)
        n_articles += 1

    # 3) Person pages
    for node in _nodes_by_type(doc, "Person"):
        _write_person_md(cfg, node, arts_by_person)
        n_people += 1

    print(f"   wrote {cfg.wiki_dir}  "
          f"(volume.md + {n_articles} articles, {n_people} people)")
