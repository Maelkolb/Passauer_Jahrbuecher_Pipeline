"""Knowledge-graph emission as JSON-LD.

Each volume produces a self-contained JSON-LD document describing:

* the **Series** (``Passauer Jahrbücher``) — the same node IRI in every
  volume's file, so a downstream merge step can deduplicate trivially
* the **Volume** (this volume, this year) — IRI ``pjb:vol/<slug>``
* one **Section** per TOC group (``Aufsätze`` / ``Berichte`` / …),
  scoped to this volume — IRI ``pjb:vol/<slug>/section/<slug>``
* one **Article** per detected article — IRI ``pjb:art/<art-id>``
* one **Person** per article author. Persons are emitted with a *stable*
  IRI derived from a normalised form of the name (lowered, no diacritics,
  punctuation stripped) so the same author appearing in multiple volumes
  produces the same node IRI and the downstream merge step gets a free
  cross-volume link.
* one **Page** per processed page, linked back to its facsimile image

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
    "inSeries":     {"@id": "schema:isPartOf",  "@type": "@id"},
    "tocEntry":     {"@id": "pjb:tocEntry"},
    "footnoteOf":   {"@id": "pjb:footnoteOf",   "@type": "@id"},
    "refersTo":     {"@id": "pjb:refersTo",     "@type": "@id"},
    "rawType":      {"@id": "pjb:rawType"},
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

    # --- Pages --------------------------------------------------------
    for p in pages:
        graph.append({
            "@id":       page_iri(cfg, p["page_num"]),
            "@type":     "WebPage",
            "name":      f"Page {p['page_num']}",
            "pageStart": p["page_num"],
            "inVolume":  volume_iri(cfg),
            "facsimile": f"pages/{p['image_filename']}",
        })

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
