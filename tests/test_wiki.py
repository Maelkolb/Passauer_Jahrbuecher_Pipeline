"""Tests for ``pjb_pipeline.emit.wiki``.

Three things we want to lock in:

1.  The frontmatter on every emitted page is valid YAML that, when
    parsed, reproduces the corresponding JSON-LD node (modulo the
    ``@context`` reference we splice in). This is the strict-mode
    round-trip contract.

2.  TEI-derived body content lands in the article markdown, page by
    page, with footnotes collected at the end.

3.  Re-running the emitter on the same volume preserves the
    agent-owned ``## Summary`` / ``## Mentions`` / ``## Notes``
    sections — the pipeline is allowed to regenerate the structural
    parts, never the agent's prose.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from pjb_pipeline.config import VolumeConfig
from pjb_pipeline.emit import graph as graph_emitter
from pjb_pipeline.emit import wiki
from pjb_pipeline.structure.footnotes import Footnote


# ---------------------------------------------------------------------------
# Tiny test-bed fixtures (mirrors tests/test_graph.py)
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> VolumeConfig:
    cfg = VolumeConfig(
        pdf_path="(unused)",
        volume_number=48,
        volume_number_roman="XLVIII",
        volume_year=2006,
        slug="pjb-048-2006",
        output_root=str(tmp_path),
    )
    cfg.ensure_dirs()
    return cfg


def _block(bid, type_, text="", bbox=(0, 0, 100, 100)):
    return {
        "id": bid, "type": type_, "bbox": list(bbox),
        "text": text, "html": "",
    }


def _page(pn, blocks):
    return {
        "page_num": pn,
        "image_filename": f"page_{pn:04d}.png",
        "image_width": 1400,
        "image_height": 2000,
        "blocks": blocks,
    }


def _article(art_id, num, title, first, last, author="", section=None, pages=None):
    return {
        "id": art_id, "num": num, "title": title,
        "page_first": first, "page_last": last,
        "author": author, "section": section,
        "pages": pages or [],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"missing frontmatter opener in {path}"
    end = text.find("\n---\n", 4)
    assert end > 0, f"missing frontmatter closer in {path}"
    return yaml.safe_load(text[4:end])


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    return text[end + 5:]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_wiki_dir_layout(tmp_path):
    cfg = _cfg(tmp_path)

    articles = [
        _article(
            "pjb-048-2006-art01", 1, "Test Article", 5, 6,
            author="Wolff, Jürgen", section="Aufsätze",
            pages=[
                _page(5, [
                    _block("p5_b001", "section-header", "Test Article"),
                    _block("p5_b002", "text", "First paragraph of the article."),
                ]),
                _page(6, [
                    _block("p6_b001", "text", "Second paragraph here."),
                ]),
            ],
        ),
    ]
    pages = articles[0]["pages"]

    wiki.run(cfg, articles, pages, toc=None,
             footnotes_by_article={}, refs_by_article={})

    # Directory skeleton
    assert (cfg.wiki_dir / "volume.md").exists()
    assert (cfg.wiki_dir / "_context.json").exists()
    assert (cfg.wiki_dir / "articles" / "pjb-048-2006-art01.md").exists()
    assert (cfg.wiki_dir / "people" / "wolff-jurgen.md").exists()


def test_article_frontmatter_is_jsonld_node(tmp_path):
    cfg = _cfg(tmp_path)

    art = _article(
        "pjb-048-2006-art01", 1, "Vilshofen", 9, 10,
        author="Wolff, Jürgen / Wandling, Anton", section="Aufsätze",
        pages=[
            _page(9, [_block("p9_b001", "text", "Body text page 9.")]),
            _page(10, [_block("p10_b001", "text", "Body text page 10.")]),
        ],
    )
    wiki.run(cfg, [art], art["pages"], toc=None)

    fm = _read_frontmatter(cfg.wiki_dir / "articles" / "pjb-048-2006-art01.md")

    # Structural contract: every key the graph emits must be present in
    # the frontmatter, and the @id must match the graph's IRI exactly.
    assert fm["@type"] == "ScholarlyArticle"
    assert fm["@id"] == "pjb:art/pjb-048-2006-art01"
    assert fm["name"] == "Vilshofen"
    assert fm["pageStart"] == 9
    assert fm["pageEnd"] == 10
    assert fm["position"] == 1
    # IRI-typed predicates are emitted as bare strings; the @context
    # declares them as "@type": "@id", so they round-trip through any
    # JSON-LD processor as references.
    assert fm["inVolume"] == "pjb:vol/pjb-048-2006"
    assert fm["inSection"] == "pjb:vol/pjb-048-2006/section/aufsatze"
    # Combined byline split into two Person refs
    assert {"@id": "pjb:person/wolff-jurgen"} in fm["author"]
    assert {"@id": "pjb:person/wandling-anton"} in fm["author"]
    # Pages are listed in hasPart (bare IRI strings)
    assert "pjb:vol/pjb-048-2006/page/0009" in fm["hasPart"]
    assert "pjb:vol/pjb-048-2006/page/0010" in fm["hasPart"]


def test_frontmatter_roundtrips_against_graph_node(tmp_path):
    """The strict-mode promise: piping frontmatter through yaml.safe_load
    and stripping ``@context`` reproduces the node the graph emitter
    would put in the JSON-LD."""
    cfg = _cfg(tmp_path)

    art = _article(
        "pjb-048-2006-art01", 1, "Roundtrip Test", 1, 2,
        author="Smith, Jane", section="Aufsätze",
        pages=[
            _page(1, [_block("p1_b001", "text", "x")]),
            _page(2, [_block("p2_b001", "text", "y")]),
        ],
    )

    # What the graph emitter would write
    graph_doc = graph_emitter.build_volume_graph(
        cfg, [art], art["pages"], None,
        footnotes_by_article={}, refs_by_article={},
    )
    expected = next(
        n for n in graph_doc["@graph"]
        if n.get("@id") == "pjb:art/pjb-048-2006-art01"
    )

    # What the wiki emitter writes
    wiki.run(cfg, [art], art["pages"], toc=None)
    fm = _read_frontmatter(cfg.wiki_dir / "articles" / "pjb-048-2006-art01.md")
    fm.pop("@context", None)   # the wiki spliced this in; the graph doesn't

    assert fm == expected


def test_article_body_has_per_page_sections_and_footnotes(tmp_path):
    cfg = _cfg(tmp_path)

    art = _article(
        "pjb-048-2006-art01", 1, "FN Test", 1, 1,
        author="Smith, J.", section="Aufsätze",
        pages=[
            _page(1, [
                _block("p1_b001", "text", "Body of the article."),
            ]),
        ],
    )
    notes = [
        Footnote(article_id="pjb-048-2006-art01", block_id="p1_fn1",
                 n=1, text="First footnote text.",
                 html_id="fn_p1_fn1", page_num=1),
        Footnote(article_id="pjb-048-2006-art01", block_id="p1_fn2",
                 n=2, text="Second footnote text.",
                 html_id="fn_p1_fn2", page_num=1),
    ]
    wiki.run(cfg, [art], art["pages"], toc=None,
             footnotes_by_article={"pjb-048-2006-art01": notes})

    body = _body(cfg.wiki_dir / "articles" / "pjb-048-2006-art01.md")
    assert "## Full Text" in body
    assert "### Page 1" in body
    assert "Body of the article." in body
    assert "## Footnotes" in body
    # Footnote definitions use HTML anchors so per-page subheaders stay
    # populated (markdown ``[^id]: text`` syntax is hoisted by GitHub's
    # renderer into a single auto-generated section at the bottom,
    # which empties any per-page subheaders we'd put around them).
    assert 'id="fn-p001-1"' in body
    assert 'id="fn-p001-2"' in body
    assert "First footnote text." in body
    assert "Second footnote text." in body


def test_agent_owned_sections_are_preserved_on_rerun(tmp_path):
    cfg = _cfg(tmp_path)
    art = _article(
        "pjb-048-2006-art01", 1, "Persistence Test", 1, 1,
        author="Smith, J.", section="Aufsätze",
        pages=[_page(1, [_block("p1_b001", "text", "Body.")])],
    )
    wiki.run(cfg, [art], art["pages"], toc=None)

    art_path = cfg.wiki_dir / "articles" / "pjb-048-2006-art01.md"
    original = art_path.read_text(encoding="utf-8")

    # Simulate the LLM editing the agent-owned section
    edited = original.replace(
        "## Summary\n\n*To be added.*",
        "## Summary\n\nThis article discusses something interesting and important.",
    )
    art_path.write_text(edited, encoding="utf-8")

    # Re-run the emitter; the Summary content must survive
    wiki.run(cfg, [art], art["pages"], toc=None)

    final = art_path.read_text(encoding="utf-8")
    assert "This article discusses something interesting and important." in final
    # And the structural body is still there (was regenerated)
    assert '<a id="page-1"></a>*[p. 1]*' in final
    assert "Body." in final


def test_person_page_lists_articles_in_this_volume(tmp_path):
    cfg = _cfg(tmp_path)
    art1 = _article(
        "pjb-048-2006-art01", 1, "First Paper", 1, 5,
        author="Wolff, Jürgen", section="Aufsätze",
        pages=[_page(1, [_block("p1_b001", "text", "x")])],
    )
    art2 = _article(
        "pjb-048-2006-art02", 2, "Second Paper", 10, 12,
        author="Wolff, Jürgen / Wandling, Anton", section="Aufsätze",
        pages=[_page(10, [_block("p10_b001", "text", "y")])],
    )
    wiki.run(cfg, [art1, art2], art1["pages"] + art2["pages"], toc=None)

    wolff = (cfg.wiki_dir / "people" / "wolff-jurgen.md").read_text(encoding="utf-8")
    assert "First Paper" in wolff
    assert "Second Paper" in wolff
    assert "Wandling, Anton" not in wolff   # only this person's appearances

    wandling = (cfg.wiki_dir / "people" / "wandling-anton.md").read_text(encoding="utf-8")
    assert "Second Paper" in wandling
    assert "First Paper" not in wandling


def test_volume_md_has_article_toc_grouped_by_section(tmp_path):
    cfg = _cfg(tmp_path)
    arts = [
        _article("pjb-048-2006-art01", 1, "Essay 1", 1, 5,
                 author="A", section="Aufsätze",
                 pages=[_page(1, [_block("p1_b001", "text", "x")])]),
        _article("pjb-048-2006-art02", 2, "Report 1", 100, 110,
                 author="B", section="Berichte",
                 pages=[_page(100, [_block("p100_b001", "text", "y")])]),
    ]
    wiki.run(cfg, arts, arts[0]["pages"] + arts[1]["pages"], toc=None)

    body = (cfg.wiki_dir / "volume.md").read_text(encoding="utf-8")
    # Both sections show up, with article links under each
    assert "### Aufsätze" in body
    assert "### Berichte" in body
    assert "[Essay 1](articles/pjb-048-2006-art01.md)" in body
    assert "[Report 1](articles/pjb-048-2006-art02.md)" in body



# ---------------------------------------------------------------------------
# Hyphenation joining + footnote grouping (regressions for the wiki emit fixes)
# ---------------------------------------------------------------------------

class TestFootnoteGroupingByPage:
    """Regression for the page-grouped footnotes fix.

    Footnotes in this corpus typically restart numbering per page, so a
    flat ``1. … 2. …`` list ends up with multiple "1." entries once you
    have more than one page. The new emitter groups by source page.
    """

    def test_footnotes_grouped_by_page_with_subheaders(self):
        from pjb_pipeline.emit.wiki import _render_footnotes_section
        from pjb_pipeline.structure.footnotes import Footnote

        notes = [
            Footnote(article_id="a1", block_id="p18_fn1", n=1,
                     text="Stoll, Integration und Abgrenzung 520.",
                     html_id="fn-a1-1", page_num=18),
            Footnote(article_id="a1", block_id="p18_fn2", n=2,
                     text="Domaszewski, Die Tierbilder der Signa.",
                     html_id="fn-a1-2", page_num=18),
            Footnote(article_id="a1", block_id="p19_fn1", n=1,
                     text="Ankersdorfer, Studien 44.",
                     html_id="fn-a1-3", page_num=19),
        ]
        out = _render_footnotes_section(notes)
        assert "## Footnotes" in out
        assert "### Page 18" in out
        assert "### Page 19" in out
        # The Page 18 block precedes Page 19
        assert out.index("### Page 18") < out.index("### Page 19")
        # HTML-anchor footnotes with page-scoped IDs: per-page
        # subheaders survive in the rendered output, and the two "1"
        # entries no longer collide because their anchor IDs
        # (p018-1 vs p019-1) are distinct.
        page18 = out.split("### Page 19")[0]
        page19 = out.split("### Page 19")[1]
        assert 'id="fn-p018-1"' in page18
        assert "Stoll" in page18
        assert 'id="fn-p018-2"' in page18
        assert "Domaszewski" in page18
        assert 'id="fn-p019-1"' in page19
        assert "Ankersdorfer" in page19

    def test_empty_footnotes_renders_nothing(self):
        from pjb_pipeline.emit.wiki import _render_footnotes_section
        assert _render_footnotes_section([]) == ""



# ---------------------------------------------------------------------------
# Markdown footnote-ref insertion + image alt-text in figure rendering
# (regressions for the wiki-side parts of the footnote-linking and
# image-description fixes)
# ---------------------------------------------------------------------------

class TestHtmlAnchorFootnoteRefsInBody:
    """When a body block contains an inline plain-digit footnote ref AND
    a footnote with that number exists on the same page, the wiki
    emitter rewrites the digit as an HTML ``<sup><a>…</a></sup>`` link
    to the matching definition. HTML anchors survive GitHub's
    markdown rendering without being hoisted, so the per-page
    grouping of footnote definitions stays intact."""

    def test_inline_ref_becomes_html_anchor(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Wie jede der römischen Legionen bezogen 2 . Sie waren ..."
        out = _insert_footnote_refs(text, page_num=18, page_numbered={1, 2, 3})
        assert 'href="#fn-p018-2"' in out
        assert 'id="fnref-p018-2"' in out
        assert "<sup>" in out
        assert "bezogen" in out and "Sie waren" in out
        assert "bezogen 2 ." not in out

    def test_unknown_number_passes_through(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Bei Punkt 7 wird es klar ."
        out = _insert_footnote_refs(text, page_num=18, page_numbered={1, 2})
        assert "<sup>" not in out
        assert "fn-" not in out
        assert "Punkt 7 wird" in out

    def test_empty_numbered_set_returns_text_unchanged(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Etwas mit Zahlen 2 und 5 ."
        out = _insert_footnote_refs(text, page_num=18, page_numbered=set())
        assert out == text


class TestFigureRendersChandraAltDescription:
    """When a figure block has no caption text of its own but Chandra
    surfaced a description (via the alt attribute on its <img> tag),
    the wiki emitter should use that description in the figure
    marker."""

    def test_figure_marker_includes_chandra_alt(self):
        from pjb_pipeline.emit.wiki import _figure_md
        blk = {
            "id": "p18_b004",
            "type": "image",
            "text": "",
            "description": "A circular seal showing a stork.",
        }
        out = _figure_md(blk, "Image")
        assert "[Image · p18_b004]" in out
        assert "A circular seal showing a stork." in out

    def test_text_caption_takes_precedence_over_description(self):
        from pjb_pipeline.emit.wiki import _figure_md
        blk = {
            "id": "p27_b003",
            "type": "image",
            "text": "Abb. 1: Denare des Septimius Severus.",
            "description": "Generic alt text that should not appear.",
        }
        out = _figure_md(blk, "Image")
        assert "Abb. 1: Denare" in out
        assert "Generic alt text" not in out

    def test_figure_without_either_still_renders_marker(self):
        from pjb_pipeline.emit.wiki import _figure_md
        blk = {"id": "p1_b1", "type": "image", "text": "", "html": ""}
        out = _figure_md(blk, "Image")
        assert "[Image · p1_b1]" in out


# ---------------------------------------------------------------------------
# Continuous body text (paragraph stitching, page markers, floats)
# ---------------------------------------------------------------------------

class TestContinuousBody:
    def _render(self, pages, notes=()):
        art = _article("pjb-048-2006-art01", 1, "T", pages[0]["page_num"],
                       pages[-1]["page_num"], pages=pages)
        return wiki._render_article_body(art, list(notes))

    def test_hyphen_join_across_a_page_break(self):
        out = self._render([
            _page(18, [_block("a", "text", "Ein Satz über die vom Kaiser verlie-")]),
            _page(19, [_block("b", "text", "henen Stangenfeldzeichen der Legion.")]),
        ])
        assert 'verliehenen <a id="page-19"></a>*[p. 19]* Stangenfeldzeichen' in out
        assert out.count("\n\n") >= 1
        assert "verlie-" not in out

    def test_compound_hyphen_is_kept(self):
        out = self._render([_page(5, [
            _block("a", "text", "Zum hundertsten Geburtstag des Böhmerwald-"),
            _block("b", "text", "Liedes wurde ein Fest gefeiert."),
        ])])
        assert "Böhmerwald-Liedes" in out

    def test_mid_sentence_join_and_new_paragraph(self):
        out = self._render([_page(5, [
            _block("a", "text", "Die Bürger der Stadt versammelten sich im Rathaus und"),
            _block("b", "text", "beschlossen eine neue Ordnung."),
            _block("c", "text", "Ein neuer Absatz beginnt hier mit einem ganzen Satz."),
        ])])
        assert "Rathaus und beschlossen" in out
        assert "Ordnung.\n\nEin neuer Absatz" in out

    def test_figure_inside_a_sentence_follows_the_paragraph(self):
        out = self._render([_page(5, [
            _block("a", "text", "Der Turm wurde im Jahr 1407 von dem Baumeister"),
            _block("f", "image", ""),
            _block("c", "caption", "Abb. 1: Der Turm."),
            _block("b", "text", "Hans Krumenauer errichtet."),
        ])])
        assert out.index("Baumeister Hans Krumenauer errichtet.") < out.index("[Image")
        assert out.index("[Image") < out.index("Abb. 1")

    def test_title_and_byline_are_not_repeated(self):
        art = _article("pjb-048-2006-art01", 1, "Die Geschichte", 5, 5, pages=[_page(5, [
            _block("by", "text", "HANS HUBER"),
            _block("ti", "section-header", "Die Geschichte"),
            _block("tx", "text", "Erster Satz."),
        ])])
        art["_skip_block_ids"] = ["by", "ti"]
        out = wiki._render_article_body(art, [])
        assert "HANS HUBER" not in out and "#### Die Geschichte" not in out
        assert "Erster Satz." in out

    def test_superscript_refs_from_html_are_linked(self):
        blk = _block("a", "text", "Würdigungen 6  und Wirken 7  bleiben")
        blk["html"] = "<p>Würdigungen<sup>6</sup> und Wirken<sup>7</sup> bleiben</p>"
        notes = [Footnote(article_id="x", block_id="f6", n=6, text="Note six.",
                          html_id="fn6", page_num=31)]
        out = self._render([_page(31, [blk])], notes)
        assert 'Würdigungen<sup><a id="fnref-p031-6" href="#fn-p031-6">6</a></sup> und' in out
        # no note 7 on the page: plain superscript, no dangling link
        assert "Wirken<sup>7</sup> bleiben" in out


def test_review_page_names_reviewer_and_reviewed_book(tmp_path):
    cfg = _cfg(tmp_path)
    art = _article("pjb-048-2006-art05", 5, "Egon Boshof, Die Regesten", 290, 290,
                   section="Rezensionen", pages=[_page(290, [_block("t", "text", "Text.")])])
    art["authors"] = ["Andreas Fohrer"]
    art["review"] = {"title": "Die Regesten", "authors": ["Egon Boshof"], "editors": []}
    wiki.run(cfg, [art], art["pages"], toc=None)
    body = _body(cfg.wiki_dir / "articles" / "pjb-048-2006-art05.md")
    assert "**Andreas Fohrer** · Rezensionen" in body
    assert "Review of: *Egon Boshof: Die Regesten*" in body
    fm = _read_frontmatter(cfg.wiki_dir / "articles" / "pjb-048-2006-art05.md")
    assert fm["@type"] == ["ScholarlyArticle", "Review"]
    assert fm["author"] == [{"@id": "pjb:person/andreas-fohrer"}]
    assert fm["itemReviewed"]["author"] == [{"@id": "pjb:person/egon-boshof"}]
    boshof = (cfg.wiki_dir / "people" / "egon-boshof.md").read_text(encoding="utf-8")
    assert "reviewed book" in boshof


def test_several_authors_get_one_person_page_each(tmp_path):
    cfg = _cfg(tmp_path)
    art = _article("pjb-048-2006-art02", 2, "Gemeinsam", 9, 9,
                   pages=[_page(9, [_block("t", "text", "Text.")])])
    art["authors"] = ["Sebastian Gassner", "Nina Kunze", "Malte Rehbein"]
    wiki.run(cfg, [art], art["pages"], toc=None)
    people = sorted(p.name for p in (cfg.wiki_dir / "people").glob("*.md"))
    assert people == ["malte-rehbein.md", "nina-kunze.md", "sebastian-gassner.md"]
    body = _body(cfg.wiki_dir / "articles" / "pjb-048-2006-art02.md")
    assert "**Sebastian Gassner / Nina Kunze / Malte Rehbein**" in body
