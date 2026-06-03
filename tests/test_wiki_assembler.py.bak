"""Tests for ``pjb_pipeline.wiki_assembler``.

Two operations to lock down:

* ``init_wiki`` produces a complete, well-formed scaffolding from an
  empty directory: CLAUDE.md, README.md, _context.json, an empty
  corpus.jsonld with just the Series node, etc.

* ``add_volume`` is the workhorse — it copies article markdown with
  path rewrites, namespaces the figure crops, merges person pages
  across volumes (the cross-volume payoff), folds the volume's
  JSON-LD into the corpus graph, regenerates the index, and appends
  to the log. It must be idempotent (running it twice = no diff) and
  must preserve agent-owned section content across re-runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from pjb_pipeline.config import VolumeConfig
from pjb_pipeline.emit import graph as graph_emitter
from pjb_pipeline.emit import wiki
from pjb_pipeline.wiki_assembler import init_wiki, add_volume


# ---------------------------------------------------------------------------
# Helpers — re-used minimal page/article fixtures
# ---------------------------------------------------------------------------

def _block(bid, type_, text="", bbox=(0, 0, 100, 100)):
    return {"id": bid, "type": type_, "bbox": list(bbox),
            "text": text, "html": ""}


def _page(pn, blocks):
    return {"page_num": pn, "image_filename": f"page_{pn:04d}.png",
            "image_width": 1400, "image_height": 2000, "blocks": blocks}


def _article(art_id, num, title, first, last, author="", section=None, pages=None):
    return {"id": art_id, "num": num, "title": title,
            "page_first": first, "page_last": last,
            "author": author, "section": section, "pages": pages or []}


def _process_volume_into_output(tmp_path: Path, *,
                                volume_number: int, year: int, slug: str,
                                articles, sections_order=None):
    """Run the wiki + graph emitters into ``tmp_path/<slug>/`` exactly
    the way the real pipeline does, so we have a realistic input for
    add-volume."""
    roman = {48: "XLVIII", 49: "XLIX", 50: "L"}[volume_number]
    cfg = VolumeConfig(
        pdf_path="(unused)", volume_number=volume_number,
        volume_number_roman=roman, volume_year=year, slug=slug,
        output_root=str(tmp_path),
    )
    cfg.ensure_dirs()
    pages = []
    for a in articles:
        pages.extend(a["pages"])

    # Graph (so the .jsonld is there for add-volume to merge)
    graph_emitter.run(cfg, articles, pages, toc=None,
                      footnotes_by_article={}, refs_by_article={})
    # Wiki markdown
    wiki.run(cfg, articles, pages, toc=None,
             footnotes_by_article={}, refs_by_article={})
    # A placeholder region crop so the copy step has something to copy.
    (cfg.regions_dir / "p1_b001.png").write_bytes(b"fake png bytes")

    return cfg


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    return yaml.safe_load(text[4:end])


# ---------------------------------------------------------------------------
# init_wiki
# ---------------------------------------------------------------------------

def test_init_wiki_writes_full_scaffolding(tmp_path):
    wiki_root = tmp_path / "wiki"
    init_wiki(wiki_root)

    # Top-level files
    assert (wiki_root / "CLAUDE.md").exists()
    assert (wiki_root / "README.md").exists()
    assert (wiki_root / "_context.json").exists()
    assert (wiki_root / "series.md").exists()
    assert (wiki_root / "index.md").exists()
    assert (wiki_root / "log.md").exists()

    # Subdirs
    for sub in ("articles", "people", "volumes", "sections",
                "places", "topics", "regions", "_graph"):
        assert (wiki_root / sub).is_dir()

    # Corpus graph: bootstrapped with just the Series node
    corpus = json.loads((wiki_root / "_graph" / "corpus.jsonld").read_text(encoding="utf-8"))
    assert "@context" in corpus
    graph = corpus["@graph"]
    assert len(graph) == 1
    assert graph[0]["@type"] == "PublicationSeries"
    assert graph[0]["@id"] == "pjb:series/passauer-jahrbuecher"


def test_init_wiki_refuses_non_empty(tmp_path):
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "something.md").write_text("preexisting", encoding="utf-8")
    try:
        init_wiki(wiki_root)
    except SystemExit:
        pass
    else:
        raise AssertionError("init_wiki should refuse a non-empty target without --force")


def test_init_wiki_force_overrides(tmp_path):
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "something.md").write_text("preexisting", encoding="utf-8")
    init_wiki(wiki_root, force=True)
    assert (wiki_root / "CLAUDE.md").exists()
    # Pre-existing file is left alone (force rewrites scaffolding, not data)
    assert (wiki_root / "something.md").read_text() == "preexisting"


# ---------------------------------------------------------------------------
# add_volume — basic shape
# ---------------------------------------------------------------------------

def test_add_volume_writes_articles_people_volume_index(tmp_path):
    wiki_root = tmp_path / "wiki"
    init_wiki(wiki_root)

    output_root = tmp_path / "output"
    cfg = _process_volume_into_output(
        output_root, volume_number=48, year=2006, slug="pjb-048-2006",
        articles=[
            _article("pjb-048-2006-art01", 1, "Vilshofen", 1, 2,
                     author="Wolff, Jürgen", section="Aufsätze",
                     pages=[
                         _page(1, [_block("p1_b001", "text", "Body 1.")]),
                         _page(2, [_block("p2_b001", "text", "Body 2.")]),
                     ]),
        ],
    )

    add_volume(wiki_root, cfg.out_dir)

    # Volume page exists at the expected location
    assert (wiki_root / "volumes" / "pjb-048-2006.md").exists()
    # Article copied
    assert (wiki_root / "articles" / "pjb-048-2006-art01.md").exists()
    # Person copied
    assert (wiki_root / "people" / "wolff-jurgen.md").exists()
    # Region copied into a slug-scoped dir
    assert (wiki_root / "regions" / "pjb-048-2006" / "p1_b001.png").exists()
    # Corpus graph now includes the article
    corpus = json.loads((wiki_root / "_graph" / "corpus.jsonld").read_text())
    ids = [n["@id"] for n in corpus["@graph"]]
    assert "pjb:art/pjb-048-2006-art01" in ids
    assert "pjb:vol/pjb-048-2006" in ids
    # Index regenerated and lists the article
    index = (wiki_root / "index.md").read_text()
    assert "Vilshofen" in index
    assert "pjb:art/pjb-048-2006-art01" in index


def test_add_volume_rewrites_figure_paths_in_articles(tmp_path):
    wiki_root = tmp_path / "wiki"
    init_wiki(wiki_root)

    output_root = tmp_path / "output"
    cfg = _process_volume_into_output(
        output_root, volume_number=48, year=2006, slug="pjb-048-2006",
        articles=[
            _article("pjb-048-2006-art01", 1, "Fig Test", 1, 1,
                     author="A", section="Aufsätze",
                     pages=[_page(1, [
                         _block("p1_b001", "figure", "Caption text"),
                     ])]),
        ],
    )

    add_volume(wiki_root, cfg.out_dir)

    art_text = (wiki_root / "articles" / "pjb-048-2006-art01.md").read_text(encoding="utf-8")
    # The original per-volume ref was ../../regions/<id>.png. After
    # add-volume, it should point at ../regions/<slug>/<id>.png.
    assert "../regions/pjb-048-2006/p1_b001.png" in art_text
    assert "../../regions/" not in art_text


# ---------------------------------------------------------------------------
# add_volume — cross-volume person merge (the main payoff)
# ---------------------------------------------------------------------------

def test_add_volume_merges_person_pages_across_volumes(tmp_path):
    wiki_root = tmp_path / "wiki"
    init_wiki(wiki_root)
    output_root = tmp_path / "output"

    # Volume 48: Wolff contributes one article
    cfg1 = _process_volume_into_output(
        output_root, volume_number=48, year=2006, slug="pjb-048-2006",
        articles=[
            _article("pjb-048-2006-art01", 1, "Early Paper", 1, 5,
                     author="Wolff, Jürgen", section="Aufsätze",
                     pages=[_page(1, [_block("p1_b001", "text", "x")])]),
        ],
    )
    add_volume(wiki_root, cfg1.out_dir)

    # Volume 49: Wolff contributes another article
    cfg2 = _process_volume_into_output(
        output_root, volume_number=49, year=2007, slug="pjb-049-2007",
        articles=[
            _article("pjb-049-2007-art01", 1, "Late Paper", 20, 30,
                     author="Wolff, Jürgen", section="Aufsätze",
                     pages=[_page(20, [_block("p20_b001", "text", "y")])]),
        ],
    )
    add_volume(wiki_root, cfg2.out_dir)

    wolff = (wiki_root / "people" / "wolff-jurgen.md").read_text(encoding="utf-8")

    # Both appearances are present, sorted by year
    assert "Early Paper" in wolff
    assert "Late Paper" in wolff
    # Volume 48 (2006) should appear before volume 49 (2007) in the file
    assert wolff.find("Early Paper") < wolff.find("Late Paper")
    # Both volume headings are there
    assert "(2006)" in wolff
    assert "(2007)" in wolff


def test_add_volume_preserves_agent_summary_across_reruns(tmp_path):
    wiki_root = tmp_path / "wiki"
    init_wiki(wiki_root)
    output_root = tmp_path / "output"

    cfg = _process_volume_into_output(
        output_root, volume_number=48, year=2006, slug="pjb-048-2006",
        articles=[
            _article("pjb-048-2006-art01", 1, "Persistence", 1, 1,
                     author="Smith, J.", section="Aufsätze",
                     pages=[_page(1, [_block("p1_b001", "text", "Body.")])]),
        ],
    )
    add_volume(wiki_root, cfg.out_dir)

    art_path = wiki_root / "articles" / "pjb-048-2006-art01.md"
    original = art_path.read_text(encoding="utf-8")
    # Simulate the LLM filling in a summary
    edited = original.replace(
        "## Summary\n\n*To be added.*",
        "## Summary\n\nA careful analysis of an obscure topic.",
    )
    art_path.write_text(edited, encoding="utf-8")

    # Re-run add-volume; the summary must survive
    add_volume(wiki_root, cfg.out_dir)
    final = art_path.read_text(encoding="utf-8")
    assert "A careful analysis of an obscure topic." in final
    # Structural body still present (was regenerated)
    assert "### Page 1" in final
    assert "Body." in final


def test_add_volume_is_idempotent_on_same_volume(tmp_path):
    """Two add-volume calls with the same input must produce a stable
    state: no duplicate ``Appears in`` subsections, no graph-node
    duplication, no double log entry on the same day."""
    wiki_root = tmp_path / "wiki"
    init_wiki(wiki_root)
    output_root = tmp_path / "output"

    cfg = _process_volume_into_output(
        output_root, volume_number=48, year=2006, slug="pjb-048-2006",
        articles=[
            _article("pjb-048-2006-art01", 1, "Idem", 1, 1,
                     author="Smith, J.", section="Aufsätze",
                     pages=[_page(1, [_block("p1_b001", "text", "x")])]),
        ],
    )
    add_volume(wiki_root, cfg.out_dir)
    add_volume(wiki_root, cfg.out_dir)

    # Person page: only one ### subsection
    person = (wiki_root / "people" / "smith-j.md").read_text(encoding="utf-8")
    assert person.count("### Passauer Jahrbuch XLVIII (2006)") == 1

    # Corpus graph: one article node only
    corpus = json.loads((wiki_root / "_graph" / "corpus.jsonld").read_text())
    art_ids = [n["@id"] for n in corpus["@graph"]
               if n.get("@type") == "ScholarlyArticle"]
    assert art_ids == ["pjb:art/pjb-048-2006-art01"]

    # Log: not double-entered
    log = (wiki_root / "log.md").read_text(encoding="utf-8")
    assert log.count("## [") - log.count("[YYYY") <= 3  # init + 1 add-volume
