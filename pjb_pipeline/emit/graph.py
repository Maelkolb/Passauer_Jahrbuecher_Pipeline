"""Knowledge-graph emission as JSON-LD.

Each volume produces a self-contained JSON-LD document describing:

* the **Series** (``Passauer Jahrbücher``) — the same node IRI in every
  volume's file, so a downstream merge step can deduplicate trivially
* the **Volume** (this volume, this year) — IRI ``pjb:vol/<slug>``
* one **Section** per TOC group (``Aufsätze`` / ``Berichte`` / …),
  scoped to this volume — IRI ``pjb:vol/<slug>/section/<slug>``
* one **Article** per detected article — IRI ``pjb:art/<art-id>``.
  Each Article carries an ordered ``hasPart`` list pointing at the
  ``WebPage`` nodes it contains.
* one **Person** per article author. Persons are emitted with a *stable*
  IRI derived from a normalised form of the name (lowered, no diacritics,
  punctuation stripped) so the same author appearing in multiple volumes
  produces the same node IRI and the downstream merge step gets a free
  cross-volume link.
* one **Page** per processed page, linked back to its facsimile image.
  Pages that belong to an article are anchored to that article through
  ``inArticle`` (the inverse of ``hasPart`` on the Article side) so the
  graph clusters each article's pages around the article node rather
  than around the volume node. Pages that don't belong to any article
  (frontmatter, blank back-matter) fall back to ``inVolume``.
* one **Figure** (``ImageObject``) per visual region on each page —
  figures, images, diagrams. Each carries ``inPage`` back to its page
  and the page lists them in ``hasPart``. So the chain is
  Volume → Article → Page → Figure. The crop file lives at
  ``<output_root>/<slug>/regions/<block_id>.png``.
* one **Comment** per footnote, anchored to its article via
  ``footnoteOf`` and to its page via ``inPage``.

The vocabulary uses Schema.org and a small custom namespace (``pjb:``).
Article-as-node sits at ``schema:ScholarlyArticle``; this is the hook
articles will hang off in the eventual full-graph.

The output is consumable directly by Apache Jena, rdflib, GraphDB, neosemantics,
or any other RDF tool. The plain-Python ``@graph`` shape also reads cleanly
as JSON if you want to consume it without an RDF library.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import List, Optional

from ..config import VolumeConfig
from ..structure.toc import TocStructure
from ..structure.footnotes import Footnote, FootnoteRef
from .html.crops import VISUAL_BLOCK_TYPES, region_crop_graph_url


# ---------------------------------------------------------------------------
# IRI helpers
# ---------------------------------------------------------------------------

# Base IRI under which all nodes live. Pick a domain you own; for now we use
# a placeholder that downstream tools can rewrite via SPARQL or sed.
BASE = "https://passauer-jahrbuecher.example/"

CONTEXT = {
    "@vocab":   "https://schema.org/",
    "pjb":      BASE,
    "schema":   "https://schema.org/",
    "dcterms":  "http://purl.org/dc/terms/",
    "tei":      "http://www.tei-c.org/ns/1.0#",
    # Local predicates we don't think belong in schema.org
    "facsimile":    {"@id": "pjb:facsimile",    "@type": "@id"},
    "pageStart":    {"@id": "schema:pageStart"},
    "pageEnd":      {"@id": "schema:pageEnd"},
    "inSection":    {"@id": "pjb:inSection",    "@type": "@id"},
    "inVolume":     {"@id": "pjb:inVolume",     "@type": "@id"},
    "inArticle":    {"@id": "pjb:inArticle",    "@type": "@id"},
    "inPage":       {"@id": "pjb:inPage",       "@type": "@id"},
    "inSeries":     {"@id": "schema:isPartOf",  "@type": "@id"},
    "hasPart":      {"@id": "schema:hasPart",   "@type": "@id"},
    "tocEntry":     {"@id": "pjb:tocEntry"},
    "footnoteOf":   {"@id": "pjb:footnoteOf",   "@type": "@id"},
    "refersTo":     {"@id": "pjb:refersTo",     "@type": "@id"},
    "rawType":      {"@id": "pjb:rawType"},
    # Visual-region predicates
    "contentUrl":   {"@id": "schema:contentUrl"},
    "bbox":         {"@id": "pjb:bbox"},
    "regionType":   {"@id": "pjb:regionType"},
}


def _slug(s: str) -> str:
    """URL-safe slug. NFD-decompose, drop diacritics, lower, collapse hyphens."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


def series_iri() -> str:
    return f"pjb:series/passauer-jahrbuecher"


def volume_iri(cfg: VolumeConfig) -> str:
    return f"pjb:vol/{cfg.slug}"


def article_iri(article_id: str) -> str:
    return f"pjb:art/{article_id}"


def section_iri(cfg: VolumeConfig, section_name: str) -> str:
    return f"pjb:vol/{cfg.slug}/section/{_slug(section_name)}"


def page_iri(cfg: VolumeConfig, page_num: int) -> str:
    return f"pjb:vol/{cfg.slug}/page/{page_num:04d}"


def person_iri(name: str) -> str:
    """Stable IRI for a person derived from a normalised form of the name.

    Two authors with the same normalised name will collide — that's fine
    for a first pass and is exactly what enables cross-volume merging. A
    later disambiguation pass can split them by inspecting the volumes/years
    they appear in.
    """
    return f"pjb:person/{_slug(name)}"


def footnote_iri(article_id: str, n: int) -> str:
    return f"pjb:art/{article_id}/fn/{n}"


def figure_iri(cfg: VolumeConfig, page_num: int, block_id: str) -> str:
    """IRI for a visual region (figure/image/diagram) on a specific page.

    The region's block id (something like ``p12_b005``) is appended so the
    IRI is stable across pipeline runs as long as the layout pass returns
    the same block ids.
    """
    return f"pjb:vol/{cfg.slug}/page/{page_num:04d}/figure/{block_id}"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _person_nodes(authors: list) -> List[dict]:
    """Split combined author strings ('Wolff/Wandling', 'Smith & Jones') and
    return one Person node per individual."""
    nodes = []
    seen = set()
    for a in authors:
        if not a:
            continue
        # Split on common separators used in scholarly bylines
        parts = re.split(r"\s*[/&]\s*|\s+und\s+|\s+and\s+", a.strip())
        for p in parts:
            p = p.strip(" ,.;")
            if not p or p in seen:
                continue
            seen.add(p)
            nodes.append({
                "@id":   person_iri(p),
                "@type": "Person",
                "name":  p,
            })
    return nodes


def _split_authors(combined: str) -> List[str]:
    return [
        p.strip(" ,.;")
        for p in re.split(r"\s*[/&]\s*|\s+und\s+|\s+and\s+", (combined or "").strip())
        if p.strip(" ,.;")
    ]


def build_volume_graph(
    cfg: VolumeConfig,
    articles: List[dict],
    pages: List[dict],
    toc: Optional[TocStructure],
    footnotes_by_article: Optional[dict] = None,
    refs_by_article: Optional[dict] = None,
) -> dict:
    """Build the volume's full JSON-LD document."""
    graph: List[dict] = []

    # --- Series + Volume + (optional) periodical metadata -------------
    graph.append({
        "@id":   series_iri(),
        "@type": "PublicationSeries",
        "name":  cfg.series_name,
        "publisher": {
            "@type": "Organization",
            "name": cfg.publisher,
        },
    })
    graph.append({
        "@id":           volume_iri(cfg),
        "@type":         ["PublicationVolume", "Book"],
        "name":          f"{cfg.volume_title} {cfg.volume_number_roman}",
        "volumeNumber":  cfg.volume_number,
        "datePublished": str(cfg.volume_year),
        "inLanguage":    cfg.language,
        "inSeries":      series_iri(),
        "publisher":     {"@type": "Organization", "name": cfg.publisher},
        "editor":        {"@type": "Organization", "name": cfg.editor},
    })

    # --- Sections (one node per distinct TOC section in this volume) --
    sections_in_volume = {}
    if toc and toc.sections:
        for name, _entries in toc.sections:
            sections_in_volume[name] = section_iri(cfg, name)
    # Also pick up sections mentioned on articles even if TOC didn't carry them
    for a in articles:
        sec = a.get("section")
        if sec and sec not in sections_in_volume:
            sections_in_volume[sec] = section_iri(cfg, sec)
    for name, iri in sections_in_volume.items():
        graph.append({
            "@id":     iri,
            "@type":   "CreativeWorkSeason",  # closest schema.org match for "section of a volume"
            "name":    name,
            "inVolume": volume_iri(cfg),
        })

    # --- Build page → article reverse map ------------------------------
    # Each article carries its own ordered list of pages (see
    # ``structure.articles``). We invert it so that, when we emit a page
    # node below, it can be anchored to its owning article instead of
    # being pinned to the volume. Pages that don't belong to any real
    # article — typically Frontmatter — still link to the volume so the
    # graph stays connected.
    page_to_article: dict = {}
    article_pages: dict = {}    # article_id → ordered list of page_nums
    for art in articles:
        if art["title"] == "Frontmatter":
            continue
        ordered_pns = [p["page_num"] for p in art.get("pages", [])]
        article_pages[art["id"]] = ordered_pns
        for pn in ordered_pns:
            # If two articles claim the same page (e.g. overlapping
            # boundary at the page break), the first one wins. This
            # matches how the HTML emitter treats the same situation.
            page_to_article.setdefault(pn, art["id"])

    # --- Pages + figures ----------------------------------------------
    # Each page is a node. Any visual region on the page (figure / image
    # / diagram, as defined by ``VISUAL_BLOCK_TYPES``) is emitted as its
    # own ``ImageObject`` node, attached to the page via ``inPage`` and
    # listed in the page's ``hasPart``. The result is a small Page →
    # Figure subgraph that lets a viewer cluster a page's visual content
    # around the page node — analogous to how Articles cluster their
    # pages.
    for p in pages:
        pn = p["page_num"]

        # Collect figure nodes for this page first so we can list them
        # in the page's ``hasPart``.
        figure_iris_on_page: List[str] = []
        figure_nodes: List[dict] = []
        for blk in p.get("blocks", []):
            if blk.get("type") not in VISUAL_BLOCK_TYPES:
                continue
            fig_id = figure_iri(cfg, pn, blk["id"])
            figure_iris_on_page.append(fig_id)
            cap = (blk.get("text") or "").strip()
            fig_node: dict = {
                "@id":         fig_id,
                "@type":       "ImageObject",
                "name":        cap or f"{blk['type'].title()} on page {pn}",
                "regionType":  blk["type"],
                "inPage":      page_iri(cfg, pn),
                "pageStart":   pn,
                # A volume-root-relative URL to the crop. Resolves once the
                # crop stage has run; the file lives at
                # ``<output_root>/<slug>/regions/<block_id>.png``.
                "contentUrl":  region_crop_graph_url(blk),
            }
            if cap:
                fig_node["description"] = cap
            bbox = blk.get("bbox")
            if bbox and len(bbox) >= 4:
                # Store as a list of four ints so SPARQL / JSON consumers
                # can read the layout coordinates back out.
                fig_node["bbox"] = [int(v) for v in bbox[:4]]
            figure_nodes.append(fig_node)

        node = {
            "@id":       page_iri(cfg, pn),
            "@type":     "WebPage",
            "name":      f"Page {pn}",
            "pageStart": pn,
            "facsimile": f"pages/{p['image_filename']}",
        }
        owning_article = page_to_article.get(pn)
        if owning_article:
            # Anchor the page to its owning article, not to the volume.
            # The article itself is the one that ``inVolume``s; the page
            # reaches the volume transitively through the article.
            node["inArticle"] = article_iri(owning_article)
        else:
            node["inVolume"] = volume_iri(cfg)
        if figure_iris_on_page:
            node["hasPart"] = figure_iris_on_page
        graph.append(node)
        # Figure nodes are emitted right after their page so the JSON is
        # easier to read by hand.
        graph.extend(figure_nodes)

    # --- Articles + Persons -------------------------------------------
    all_authors: List[str] = []
    for art in articles:
        if art["title"] == "Frontmatter":
            continue
        author_list = _split_authors(art.get("author", ""))
        all_authors.extend(author_list)

        # Refs (outbound from this article to footnotes)
        article_refs = (refs_by_article or {}).get(art["id"], [])

        node = {
            "@id":       article_iri(art["id"]),
            "@type":     "ScholarlyArticle",
            "name":      art["title"],
            "pageStart": art["page_first"],
            "pageEnd":   art["page_last"],
            "position":  art["num"],
            "inVolume":  volume_iri(cfg),
            "inLanguage": cfg.language,
        }
        if art.get("section"):
            node["inSection"] = sections_in_volume.get(art["section"])
        if author_list:
            node["author"] = [{"@id": person_iri(a)} for a in author_list]
        # Attach the article's pages explicitly. ``hasPart`` is the inverse
        # of the per-page ``inArticle`` link above, so a graph visualiser
        # (or a SPARQL query) can hop in either direction and the pages
        # naturally cluster around their article node.
        pns_for_art = article_pages.get(art["id"], [])
        if pns_for_art:
            node["hasPart"] = [page_iri(cfg, pn) for pn in pns_for_art]
        if art.get("_toc_entry"):
            node["tocEntry"] = art["_toc_entry"]
        graph.append(node)

        # --- Footnotes for this article -------------------------------
        notes = (footnotes_by_article or {}).get(art["id"], [])
        for fn in notes:
            fn_node = {
                "@id":       footnote_iri(art["id"], fn.n),
                "@type":     "Comment",     # closest schema.org match
                "name":      f"Footnote {fn.n}",
                "text":      fn.text,
                "footnoteOf": article_iri(art["id"]),
                "position":  fn.n,
            }
            # If we know which page this footnote sits on, attach an
            # ``inPage`` link so a graph viewer can also cluster footnotes
            # around their page (and not only their article).
            if getattr(fn, "page_num", None) is not None:
                fn_node["inPage"] = page_iri(cfg, fn.page_num)
                fn_node["pageStart"] = fn.page_num
            graph.append(fn_node)

    # --- Person nodes (deduped across the volume) ---------------------
    graph.extend(_person_nodes(all_authors))

    return {
        "@context": CONTEXT,
        "@graph":   graph,
    }


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(
    cfg: VolumeConfig,
    articles: List[dict],
    pages: List[dict],
    toc: Optional[TocStructure],
    footnotes_by_article: Optional[dict] = None,
    refs_by_article: Optional[dict] = None,
) -> None:
    doc = build_volume_graph(
        cfg, articles, pages, toc,
        footnotes_by_article=footnotes_by_article,
        refs_by_article=refs_by_article,
    )
    out = cfg.graph_dir / f"{cfg.slug}.jsonld"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # Counts for the log
    counts: dict = {}
    for node in doc["@graph"]:
        t = node.get("@type")
        if isinstance(t, list):
            for tt in t:
                counts[tt] = counts.get(tt, 0) + 1
        else:
            counts[t] = counts.get(t, 0) + 1
    pretty = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    print(f"   wrote {out}  ({pretty})")
