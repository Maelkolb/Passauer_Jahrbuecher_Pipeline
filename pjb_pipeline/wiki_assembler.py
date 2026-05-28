"""Corpus-wiki assembler.

Two operations, both meant to be called by the user via the CLI:

* :func:`init_wiki` — bootstrap an empty wiki repo. Run once, on a fresh
  directory (or one containing only ``.git``). Writes the static
  scaffolding: ``CLAUDE.md``, ``index.md``, ``log.md``, ``series.md``,
  ``_context.json``, an empty ``_graph/corpus.jsonld``, and the directory
  skeleton.

* :func:`add_volume` — fold one processed volume's wiki + graph into the
  corpus wiki. Idempotent: re-running with the same volume produces a
  no-op diff. Preserves agent-owned sections (``## Summary`` and friends)
  on every page that already exists in the target.

The wiki repo is owned by the user (typically a separate git repo from
the pipeline). The assembler treats it as a target directory and never
runs git commands itself — the user reviews ``git diff`` and commits.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .emit.jsonld_context import BASE, CONTEXT
from .emit.wiki import AGENT_OWNED_SECTIONS, _read_existing_zones


# Where the template files live inside the installed package.
TEMPLATES_DIR = Path(__file__).resolve().parent / "wiki_templates"

# Default series-level metadata used by ``init_wiki`` to bootstrap the
# ``series.md`` page. Matches the defaults in ``VolumeConfig``.
SERIES_DEFAULTS = {
    "name":        "Passauer Jahrbücher",
    "publisher":   "Verein für Ostbairische Heimatforschung",
    "editor":      "Institut für Kulturraumforschung Ostbaierns und der Nachbarregionen",
    "description": (
        "Annual scholarly journal on the history and culture of Ostbaiern "
        "(Eastern Bavaria), published since 1948."
    ),
}

# Directories that exist in a bootstrapped wiki, including placeholders for
# future NER-driven content (places/topics).
WIKI_SUBDIRS = (
    "articles", "people", "volumes", "sections",
    "places", "topics", "regions", "_graph",
)


# ---------------------------------------------------------------------------
# Bootstrap (init-wiki)
# ---------------------------------------------------------------------------

def init_wiki(wiki_root: Path, *, force: bool = False) -> None:
    """Bootstrap ``wiki_root`` as a fresh LLM-Wiki.

    Refuses to overwrite an existing non-empty target unless ``force`` is
    set. The intent is that the first call is on an empty directory you
    have just ``git init``'d.
    """
    wiki_root = Path(wiki_root)
    if wiki_root.exists() and any(
        p for p in wiki_root.iterdir() if p.name not in (".git", ".gitignore")
    ) and not force:
        raise SystemExit(
            f"Refusing to init: {wiki_root} is not empty. "
            "Pass --force to overwrite scaffolding (will preserve "
            "existing articles/, people/, etc., but rewrite "
            "CLAUDE.md and the like)."
        )

    wiki_root.mkdir(parents=True, exist_ok=True)
    for sub in WIKI_SUBDIRS:
        (wiki_root / sub).mkdir(exist_ok=True)

    # Shared JSON-LD context
    _write_context_json(wiki_root / "_context.json")

    # Templated docs
    _copy_template(wiki_root / "CLAUDE.md", "CLAUDE.md")
    _copy_template(wiki_root / "README.md", "README.md")

    # Empty corpus graph — just the Series node, so the graph file is
    # never genuinely empty (RDF tools dislike empty @graph arrays).
    series_node = _series_node()
    corpus_doc = {"@context": CONTEXT, "@graph": [series_node]}
    (wiki_root / "_graph" / "corpus.jsonld").write_text(
        json.dumps(corpus_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # series.md (deterministic; same content for the whole project)
    _write_series_md(wiki_root, series_node)

    # index.md (empty catalog)
    (wiki_root / "index.md").write_text(_render_index(corpus_doc), encoding="utf-8")

    # log.md (one bootstrap entry)
    today = date.today().isoformat()
    (wiki_root / "log.md").write_text(
        "# Log\n\n"
        f"## [{today}] init-wiki | bootstrap\n"
        "- created empty wiki structure\n"
        "- wrote CLAUDE.md, README.md, series.md, _context.json\n"
        "- initialised _graph/corpus.jsonld with the Series node\n",
        encoding="utf-8",
    )

    print(f"Initialised wiki at {wiki_root}")
    print("Next: run `pjb-pipeline add-volume <wiki_root> output/<slug>/` "
          "to ingest a processed volume.")


def _series_node() -> dict:
    """The single ``PublicationSeries`` node that anchors the graph."""
    return {
        "@id":   "pjb:series/passauer-jahrbuecher",
        "@type": "PublicationSeries",
        "name":  SERIES_DEFAULTS["name"],
        "description": SERIES_DEFAULTS["description"],
        "publisher": {
            "@type": "Organization",
            "name":  SERIES_DEFAULTS["publisher"],
        },
    }


def _write_context_json(path: Path) -> None:
    """Dump the shared ``@context`` as a standalone JSON file."""
    path.write_text(
        json.dumps({"@context": CONTEXT}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _copy_template(dest: Path, name: str) -> None:
    src = TEMPLATES_DIR / name
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _write_series_md(wiki_root: Path, series_node: dict) -> None:
    fm = _yaml_frontmatter({"@context": "./_context.json", **series_node})
    body = (
        f"# {SERIES_DEFAULTS['name']}\n\n"
        f"{SERIES_DEFAULTS['description']}\n\n"
        f"**Publisher.** {SERIES_DEFAULTS['publisher']}.\n"
        f"**Editorial body.** {SERIES_DEFAULTS['editor']}.\n\n"
        "## Volumes\n\n"
        "*(populated as volumes are added with `pjb-pipeline add-volume`)*\n\n"
        "## Summary\n\n*To be added.*\n\n"
        "## Notes\n\n*To be added.*\n"
    )
    (wiki_root / "series.md").write_text(fm + "\n" + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Add one volume to the wiki (add-volume)
# ---------------------------------------------------------------------------

def add_volume(wiki_root: Path, volume_output_root: Path) -> None:
    """Merge one processed volume into the corpus wiki at ``wiki_root``.

    ``volume_output_root`` is something like ``output/pjb-048-2006/`` — the
    directory the pipeline writes to. We pick up:

    * ``output/<slug>/wiki/volume.md``               → ``volumes/<slug>.md``
    * ``output/<slug>/wiki/articles/*.md``           → ``articles/*.md``
    * ``output/<slug>/wiki/people/*.md``             → ``people/*.md``   (merged)
    * ``output/<slug>/regions/*.png``                → ``regions/<slug>/*.png``
    * ``output/<slug>/graph/<slug>.jsonld``          → merged into ``_graph/corpus.jsonld``

    All path rewrites and content merges happen here. Idempotent: a
    second call with the same volume should produce no diff.
    """
    wiki_root = Path(wiki_root).resolve()
    vol_root = Path(volume_output_root).resolve()

    if not (wiki_root / "_context.json").exists():
        raise SystemExit(
            f"{wiki_root} is not an initialised wiki "
            "(no _context.json). Run `pjb-pipeline init-wiki` first."
        )

    src_wiki = vol_root / "wiki"
    if not src_wiki.exists():
        raise SystemExit(f"No wiki/ directory under {vol_root}. Has the volume been processed?")

    # Slug = the volume's directory name. Could also come from the
    # frontmatter of volume.md, but the directory name is canonical.
    slug = vol_root.name

    # Recreate the directory skeleton — a cloned wiki has no empty dirs.
    for sub in WIKI_SUBDIRS:
        (wiki_root / sub).mkdir(parents=True, exist_ok=True)

    # 1) Merge the graph first — index.md regeneration needs it.
    new_corpus_doc, counts = _merge_volume_graph(wiki_root, vol_root, slug)

    # 2) Copy the volume page (with path rewrites)
    _copy_volume_md(wiki_root, src_wiki, slug)

    # 3) Copy article pages (with figure-path rewrites)
    n_articles = _copy_article_mds(wiki_root, src_wiki, slug)

    # 4) Copy the region crops, namespaced by slug
    n_regions = _copy_regions(wiki_root, vol_root, slug)

    # 5) Merge person pages
    n_people_new, n_people_merged = _merge_person_mds(wiki_root, src_wiki)

    # 6) Regenerate the index
    (wiki_root / "index.md").write_text(_render_index(new_corpus_doc), encoding="utf-8")

    # 7) Append to the log
    _append_log_entry(wiki_root, slug, n_articles, n_people_new,
                      n_people_merged, n_regions)

    print(f"add-volume {slug} → {wiki_root}:")
    print(f"  articles: {n_articles}")
    print(f"  people:   {n_people_new} new, {n_people_merged} merged")
    print(f"  regions:  {n_regions}")
    print(f"  graph:    {counts['added']} new nodes, "
          f"{counts['updated']} existing nodes updated, "
          f"{counts['total']} total")


# --- graph merge -----------------------------------------------------------

def _merge_volume_graph(wiki_root: Path, vol_root: Path, slug: str) -> Tuple[dict, dict]:
    """Merge the volume's ``.jsonld`` into the corpus graph and write back.

    Returns ``(merged_doc, counts)`` where counts is ``{added, updated,
    total}``. Same dedupe semantics as ``scripts/merge_graphs.py``: nodes
    are merged by ``@id``; for collisions, missing keys are filled in
    from the incoming node, existing keys are kept.
    """
    corpus_path = wiki_root / "_graph" / "corpus.jsonld"
    vol_jsonld_path = vol_root / "graph" / f"{slug}.jsonld"

    if not vol_jsonld_path.exists():
        raise SystemExit(f"No graph file at {vol_jsonld_path}.")

    corpus_doc = json.loads(corpus_path.read_text(encoding="utf-8"))
    vol_doc = json.loads(vol_jsonld_path.read_text(encoding="utf-8"))

    by_id: "OrderedDict[str, dict]" = OrderedDict()
    for n in corpus_doc.get("@graph", []):
        if n.get("@id"):
            by_id[n["@id"]] = n

    added = updated = 0
    for n in vol_doc.get("@graph", []):
        nid = n.get("@id")
        if not nid:
            continue
        if nid in by_id:
            updated += 1
            existing = by_id[nid]
            for k, v in n.items():
                if k == "@id":
                    continue
                if k not in existing:
                    existing[k] = v
        else:
            added += 1
            by_id[nid] = dict(n)

    merged = {"@context": CONTEXT, "@graph": list(by_id.values())}
    corpus_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged, {"added": added, "updated": updated, "total": len(by_id)}


# --- volume page -----------------------------------------------------------

_RE_ARTICLES_LINK = re.compile(r"\]\(articles/([^)]+)\)")

def _copy_volume_md(wiki_root: Path, src_wiki: Path, slug: str) -> None:
    """Copy ``wiki/volume.md`` to ``volumes/<slug>.md``, rewriting links.

    The source file links to ``articles/<art>.md`` (same depth); the
    target lives at ``volumes/<slug>.md`` so its links to articles
    become ``../articles/<art>.md``. The ``@context`` reference moves
    from ``./_context.json`` to ``../_context.json``.
    """
    src = src_wiki / "volume.md"
    if not src.exists():
        return
    text = src.read_text(encoding="utf-8")
    text = _rewrite_context_ref(text, "./_context.json", "../_context.json")
    text = _RE_ARTICLES_LINK.sub(r"](../articles/\1)", text)

    dest = wiki_root / "volumes" / f"{slug}.md"
    # Preserve any agent edits in the volume page across re-runs.
    existing = _read_existing_zones(dest)
    if existing:
        # Re-splice agent-owned sections into the freshly written text.
        text = _replace_agent_sections(text, existing)
    dest.write_text(text, encoding="utf-8")


# --- article pages ---------------------------------------------------------

_RE_FIGURE_REF = re.compile(r"!\[([^\]]*)\]\(\.\./\.\./regions/([^)]+)\)")

def _copy_article_mds(wiki_root: Path, src_wiki: Path, slug: str) -> int:
    """Copy article markdown into ``articles/`` with path rewrites and
    preservation of agent-owned sections."""
    src_dir = src_wiki / "articles"
    if not src_dir.exists():
        return 0
    dst_dir = wiki_root / "articles"
    dst_dir.mkdir(parents=True, exist_ok=True)

    n = 0
    for src in sorted(src_dir.glob("*.md")):
        text = src.read_text(encoding="utf-8")
        # Rewrite figure refs:
        #   ![alt](../../regions/<block>.png)
        #     ↓  (regions live under <wiki>/regions/<slug>/)
        #   ![alt](../regions/<slug>/<block>.png)
        text = _RE_FIGURE_REF.sub(
            lambda m: f"![{m.group(1)}](../regions/{slug}/{m.group(2)})",
            text,
        )
        # @context stays at ../_context.json — same depth in both layouts.

        dest = dst_dir / src.name
        # Preserve agent-owned sections from a previous add-volume run
        existing = _read_existing_zones(dest)
        if existing:
            text = _replace_agent_sections(text, existing)
        dest.write_text(text, encoding="utf-8")
        n += 1
    return n


# --- regions (figure crops) -----------------------------------------------

def _copy_regions(wiki_root: Path, vol_root: Path, slug: str) -> int:
    """Copy ``output/<slug>/regions/*.png`` into ``regions/<slug>/``."""
    src_dir = vol_root / "regions"
    if not src_dir.exists():
        return 0
    dst_dir = wiki_root / "regions" / slug
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in src_dir.glob("*.png"):
        dest = dst_dir / src.name
        # shutil.copy2 is idempotent on identical files; we don't need a hash check.
        if not dest.exists() or src.stat().st_size != dest.stat().st_size:
            shutil.copy2(src, dest)
        n += 1
    return n


# --- person pages ----------------------------------------------------------

_RE_SUB_VOLUME = re.compile(r"^### ([^\n]+)\n(.*?)(?=^### |^## |\Z)",
                            re.MULTILINE | re.DOTALL)
_RE_ART_ID_IN_LINK = re.compile(r"\(\.\./articles/([a-z0-9-]+)\.md\)")
_RE_SLUG_YEAR = re.compile(r"pjb-\d{3}-(\d{4})")


def _merge_person_mds(wiki_root: Path, src_wiki: Path) -> Tuple[int, int]:
    """Merge per-volume person pages into ``people/``.

    For each person page in ``output/<slug>/wiki/people/``:

    * If ``people/<slug>.md`` doesn't exist in the target, copy it.
    * Otherwise merge: keep all existing ``### <volume>`` subsections
      under ``## Appears in``, replace the one matching this volume's
      slug (if any) with the incoming subsection, sort by year, write
      back. Agent-owned sections (``## Summary``/``## Mentions``/
      ``## Notes``) are preserved.

    Returns ``(n_new, n_merged)``.
    """
    src_dir = src_wiki / "people"
    if not src_dir.exists():
        return (0, 0)
    dst_dir = wiki_root / "people"
    dst_dir.mkdir(parents=True, exist_ok=True)

    n_new = n_merged = 0
    for src in sorted(src_dir.glob("*.md")):
        dest = dst_dir / src.name
        if not dest.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            n_new += 1
        else:
            _merge_one_person_page(src, dest)
            n_merged += 1
    return (n_new, n_merged)


def _merge_one_person_page(src: Path, dest: Path) -> None:
    """Merge one incoming per-volume person page into an existing target."""
    incoming_text = src.read_text(encoding="utf-8")
    existing_text = dest.read_text(encoding="utf-8")

    # Frontmatter & H1 come from the incoming page (they're identical or
    # the same shape; the @id is by construction the same).
    in_fm, in_body = _split_frontmatter(incoming_text)
    _, ex_body = _split_frontmatter(existing_text)

    # Title line
    m = re.match(r"#\s+[^\n]+\n", in_body)
    title_line = m.group(0) if m else ""

    # Collect "Appears in" subsections from both
    incoming_subsections = _parse_appears_subsections(in_body)
    existing_subsections = _parse_appears_subsections(ex_body)

    merged: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()
    # Start with the existing ones (in their stored order)
    for slug, payload in existing_subsections.items():
        merged[slug] = payload
    # Overwrite/add with the incoming one(s) — incoming wins for that slug
    for slug, payload in incoming_subsections.items():
        merged[slug] = payload

    # Sort by year, then by slug as a tiebreaker
    def _year(slug_):
        m_ = _RE_SLUG_YEAR.search(slug_)
        return (int(m_.group(1)) if m_ else 9999, slug_)
    merged = OrderedDict(sorted(merged.items(), key=lambda kv: _year(kv[0])))

    appears_block = "## Appears in\n\n"
    for slug, (heading, items) in merged.items():
        appears_block += f"### {heading}\n\n{items}\n\n"

    # Agent-owned sections — preserve from existing
    existing_zones = _read_existing_zones(dest)
    agent_block = "".join(
        _render_existing_or_placeholder(s, existing_zones) for s in AGENT_OWNED_SECTIONS
    )

    out = in_fm + "\n" + title_line + "\n" + appears_block + agent_block
    dest.write_text(out, encoding="utf-8")


def _parse_appears_subsections(body: str) -> "OrderedDict[str, Tuple[str, str]]":
    """Return ordered ``{slug: (heading, items)}`` for ``## Appears in``.

    The slug used as the key is extracted from the first article link in
    the subsection — robust because every per-volume person page lists
    only articles from that volume.
    """
    # Find the Appears in section
    m = re.search(r"^## Appears in\n(.*?)(?=^## |\Z)", body,
                  re.MULTILINE | re.DOTALL)
    if not m:
        return OrderedDict()
    section_body = m.group(1)

    out: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()
    for sm in _RE_SUB_VOLUME.finditer(section_body):
        heading = sm.group(1).strip()
        items = sm.group(2).strip()
        slug_match = _RE_ART_ID_IN_LINK.search(items)
        if not slug_match:
            continue
        # Article id looks like "pjb-048-2006-art03"; the volume slug is
        # the first three dashes.
        art_id = slug_match.group(1)
        slug = "-".join(art_id.split("-")[:3])
        out[slug] = (heading, items)
    return out


# --- helpers ---------------------------------------------------------------

def _yaml_frontmatter(node: dict) -> str:
    body = yaml.safe_dump(
        node, sort_keys=False, allow_unicode=True,
        default_flow_style=False, width=10_000,
    )
    return f"---\n{body}---\n"


def _split_frontmatter(text: str) -> Tuple[str, str]:
    """Return ``(frontmatter_block_including_dashes, body_after_closer)``."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[:end + 5], text[end + 5:]


def _rewrite_context_ref(text: str, old: str, new: str) -> str:
    return text.replace(f'"@context": {old}', f'"@context": {new}') \
               .replace(f"'@context': {old}", f"'@context': {new}") \
               .replace(f'@context: {old}', f'@context: {new}')


def _render_existing_or_placeholder(name: str, existing: Dict[str, str]) -> str:
    body = existing.get(name)
    if body and body.strip():
        return f"## {name}\n{body}\n"
    return f"## {name}\n\n*To be added.*\n\n"


_RE_LEVEL2 = re.compile(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)",
                        re.MULTILINE | re.DOTALL)


def _replace_agent_sections(text: str, agent_content: Dict[str, str]) -> str:
    """Splice agent-owned section content into ``text`` (which has placeholders).

    Walks the level-2 headings in ``text``; whenever one matches an
    agent-owned section name, replaces its body with the agent_content
    version (which we read from the previous file on disk before
    overwriting).
    """
    def _sub(m: re.Match) -> str:
        name = m.group(1).strip()
        if name in agent_content:
            body = agent_content[name]
            return f"## {name}\n{body}\n"
        return m.group(0)
    return _RE_LEVEL2.sub(_sub, text)


# ---------------------------------------------------------------------------
# Index regeneration
# ---------------------------------------------------------------------------

def _render_index(corpus_doc: dict) -> str:
    """Render ``index.md`` from the corpus graph.

    Catalog-style: a section per top-level node type, with one line per
    page. ``@id`` is shown alongside the markdown link so the agent can
    cite by `@id`.
    """
    by_type: Dict[str, List[dict]] = {}
    for n in corpus_doc.get("@graph", []):
        t = n.get("@type", "?")
        if isinstance(t, list):
            t = t[0]
        by_type.setdefault(t, []).append(n)

    out: List[str] = []
    out.append("# Index\n")
    out.append("\nA catalog of every page in the wiki, by entity type. "
               "Citations should use the `@id` shown — those round-trip into "
               "`_graph/corpus.jsonld`.\n\n")

    # Series + Volumes first
    if "PublicationSeries" in by_type:
        out.append("## Series\n\n")
        for n in by_type["PublicationSeries"]:
            out.append(f"- [{n.get('name', '?')}](series.md) · `{n['@id']}`\n")
        out.append("\n")

    if "PublicationVolume" in by_type:
        vols = sorted(
            by_type["PublicationVolume"],
            key=lambda n: n.get("datePublished") or n.get("volumeNumber") or "",
        )
        out.append(f"## Volumes ({len(vols)})\n\n")
        for n in vols:
            slug = n["@id"].split("pjb:vol/", 1)[-1]
            name = n.get("name") or slug
            year = n.get("datePublished")
            # ``name`` from the graph already includes the roman numeral
            # (e.g. "Passauer Jahrbuch XLVIII") so we just append the year.
            label = f"{name} ({year})" if year else name
            out.append(f"- [{label}](volumes/{slug}.md) · `{n['@id']}`\n")
        out.append("\n")

    if "ScholarlyArticle" in by_type:
        arts = sorted(by_type["ScholarlyArticle"], key=lambda n: n["@id"])
        out.append(f"## Articles ({len(arts)})\n\n")
        for n in arts:
            art_id = n["@id"].split("pjb:art/", 1)[-1]
            name = n.get("name", art_id)
            out.append(f"- [{name}](articles/{art_id}.md) · `{n['@id']}`\n")
        out.append("\n")

    if "Person" in by_type:
        persons = sorted(by_type["Person"], key=lambda n: n.get("name", n["@id"]))
        out.append(f"## People ({len(persons)})\n\n")
        for n in persons:
            slug = n["@id"].split("pjb:person/", 1)[-1]
            out.append(f"- [{n.get('name', slug)}](people/{slug}.md) · `{n['@id']}`\n")
        out.append("\n")

    # Lower-tier graph nodes — list counts only, no per-node links (they
    # don't have dedicated wiki pages in v1).
    other_counts = []
    for t in ("CreativeWorkSeason", "WebPage", "ImageObject", "Comment"):
        if t in by_type:
            other_counts.append((t, len(by_type[t])))
    if other_counts:
        out.append("## Other graph nodes (in `_graph/corpus.jsonld`)\n\n")
        for t, n in other_counts:
            out.append(f"- {t}: {n}\n")
        out.append("\n")

    return "".join(out)


# ---------------------------------------------------------------------------
# Log append
# ---------------------------------------------------------------------------

def _append_log_entry(wiki_root: Path, slug: str,
                      n_articles: int, n_people_new: int,
                      n_people_merged: int, n_regions: int) -> None:
    today = date.today().isoformat()
    entry = (
        f"\n## [{today}] add-volume | {slug}\n"
        f"- articles added: {n_articles}\n"
        f"- people: {n_people_new} new, {n_people_merged} merged\n"
        f"- regions copied: {n_regions}\n"
        f"- _graph/corpus.jsonld merged\n"
    )
    log_path = wiki_root / "log.md"
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
        # Idempotency hint: if this exact slug already has an entry today,
        # don't double-log. (We always rewrite content; the log is a
        # human-readable diary, not the source of truth.)
        if f"## [{today}] add-volume | {slug}\n" in existing:
            return
        log_path.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")
    else:
        log_path.write_text(f"# Log\n{entry}", encoding="utf-8")
