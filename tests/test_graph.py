"""Tests for ``pjb_pipeline.emit.graph`` — specifically the new
article-anchored page layout."""

from pjb_pipeline.config import VolumeConfig
from pjb_pipeline.emit.graph import build_volume_graph
from pjb_pipeline.structure.footnotes import collect_article_footnotes


def _cfg():
    return VolumeConfig(
        pdf_path="(unused)",
        volume_number=49,
        volume_number_roman="XLIX",
        volume_year=2007,
        slug="pjb-049-2007",
    )


def _page(pn, blocks=None):
    return {
        "page_num": pn,
        "image_filename": f"page_{pn:04d}.png",
        "image_width": 1400,
        "image_height": 2000,
        "blocks": blocks or [],
    }


def _block(bid, type_, bbox, text="", html=""):
    return {"id": bid, "type": type_, "bbox": bbox, "text": text, "html": html}


def _article(art_id, num, title, first, last, author="", section=None, pages=None):
    if pages is None:
        pages = [_page(pn) for pn in range(first, last + 1)]
    return {
        "id": art_id,
        "num": num,
        "title": title,
        "page_first": first,
        "page_last": last,
        "author": author,
        "section": section,
        "pages": pages,
    }


def _by_id(graph, iri):
    for n in graph:
        if n.get("@id") == iri:
            return n
    return None


def _pages(graph):
    return [n for n in graph if n.get("@type") == "WebPage"]


def _articles(graph):
    return [n for n in graph if n.get("@type") == "ScholarlyArticle"]


def _figures(graph):
    return [n for n in graph if n.get("@type") == "ImageObject"]


def _footnotes(graph):
    return [n for n in graph if n.get("@type") == "Comment"]


def test_pages_with_an_article_link_to_the_article_not_the_volume():
    cfg = _cfg()
    pages = [_page(pn) for pn in range(1, 16)]
    articles = [
        _article("frontmatter", 0, "Frontmatter", 1, 4),
        _article("pjb-049-2007-art01", 1, "First Article", 5, 10,
                 author="Erkens", section="Aufsätze"),
        _article("pjb-049-2007-art02", 2, "Second Article", 11, 15,
                 author="Wolff", section="Berichte"),
    ]

    doc = build_volume_graph(cfg, articles, pages, toc=None)
    graph = doc["@graph"]
    page_nodes = _pages(graph)

    # Frontmatter pages stay anchored to the volume (no owning article).
    for pn in range(1, 5):
        node = _by_id(graph, f"pjb:vol/pjb-049-2007/page/{pn:04d}")
        assert node is not None
        assert node.get("inVolume") == "pjb:vol/pjb-049-2007"
        assert "inArticle" not in node

    # Article-owned pages anchor to the article via inArticle, NOT the volume.
    for pn in range(5, 11):
        node = _by_id(graph, f"pjb:vol/pjb-049-2007/page/{pn:04d}")
        assert node.get("inArticle") == "pjb:art/pjb-049-2007-art01"
        assert "inVolume" not in node
    for pn in range(11, 16):
        node = _by_id(graph, f"pjb:vol/pjb-049-2007/page/{pn:04d}")
        assert node.get("inArticle") == "pjb:art/pjb-049-2007-art02"
        assert "inVolume" not in node

    # Sanity: total page count is preserved.
    assert len(page_nodes) == 15


def test_each_article_carries_hasPart_listing_its_pages_in_order():
    cfg = _cfg()
    pages = [_page(pn) for pn in range(1, 16)]
    articles = [
        _article("frontmatter", 0, "Frontmatter", 1, 4),
        _article("pjb-049-2007-art01", 1, "First Article", 5, 10),
        _article("pjb-049-2007-art02", 2, "Second Article", 11, 15),
    ]
    doc = build_volume_graph(cfg, articles, pages, toc=None)
    graph = doc["@graph"]

    art1 = _by_id(graph, "pjb:art/pjb-049-2007-art01")
    assert art1 is not None
    assert art1.get("hasPart") == [
        f"pjb:vol/pjb-049-2007/page/{pn:04d}" for pn in range(5, 11)
    ]
    # The article itself still links up to the volume.
    assert art1.get("inVolume") == "pjb:vol/pjb-049-2007"

    art2 = _by_id(graph, "pjb:art/pjb-049-2007-art02")
    assert art2.get("hasPart") == [
        f"pjb:vol/pjb-049-2007/page/{pn:04d}" for pn in range(11, 16)
    ]


def test_context_defines_inArticle_and_hasPart():
    cfg = _cfg()
    doc = build_volume_graph(cfg, [], [], toc=None)
    ctx = doc["@context"]
    assert "inArticle" in ctx
    assert ctx["inArticle"]["@id"] == "pjb:inArticle"
    assert ctx["inArticle"]["@type"] == "@id"
    assert "hasPart" in ctx
    assert ctx["hasPart"]["@id"] == "schema:hasPart"
    assert ctx["hasPart"]["@type"] == "@id"


def test_no_articles_means_all_pages_link_to_volume():
    # Degenerate case: volume with no detected articles. Every page falls
    # back to the volume anchor (and the graph stays connected).
    cfg = _cfg()
    pages = [_page(pn) for pn in range(1, 4)]
    doc = build_volume_graph(cfg, [], pages, toc=None)
    graph = doc["@graph"]
    for n in _pages(graph):
        assert n.get("inVolume") == "pjb:vol/pjb-049-2007"
        assert "inArticle" not in n


# ---------------------------------------------------------------------------
# Figure / visual-region nodes
# ---------------------------------------------------------------------------

def test_visual_regions_become_image_object_nodes_anchored_to_their_page():
    cfg = _cfg()
    # Page 7 carries one figure and one image.
    p5 = _page(5)
    p6 = _page(6)
    p7 = _page(7, [
        _block("p7_b001", "figure", [200, 300, 1200, 1200], text="Fig. 1 — Map"),
        _block("p7_b002", "image",  [200, 1300, 1200, 1700], text="Portrait"),
        _block("p7_b003", "text",   [100, 1800, 1300, 1900], text="body"),
    ])
    articles = [
        _article("frontmatter", 0, "Frontmatter", 1, 4),
        _article("art01", 1, "Bishops", 5, 7, pages=[p5, p6, p7]),
    ]
    all_pages = [_page(pn) for pn in range(1, 5)] + [p5, p6, p7]
    doc = build_volume_graph(cfg, articles, all_pages, toc=None)
    graph = doc["@graph"]

    figs = _figures(graph)
    assert len(figs) == 2

    fig1 = _by_id(graph, "pjb:vol/pjb-049-2007/page/0007/figure/p7_b001")
    assert fig1 is not None
    assert fig1["@type"] == "ImageObject"
    assert fig1["regionType"] == "figure"
    assert fig1["inPage"] == "pjb:vol/pjb-049-2007/page/0007"
    assert fig1["pageStart"] == 7
    assert fig1["contentUrl"] == "regions/p7_b001.png"
    assert fig1["bbox"] == [200, 300, 1200, 1200]
    assert fig1["name"] == "Fig. 1 — Map"
    assert fig1["description"] == "Fig. 1 — Map"

    fig2 = _by_id(graph, "pjb:vol/pjb-049-2007/page/0007/figure/p7_b002")
    assert fig2["regionType"] == "image"

    # Page 7 lists both figures in hasPart so the page node "owns" them.
    p7_node = _by_id(graph, "pjb:vol/pjb-049-2007/page/0007")
    assert set(p7_node.get("hasPart", [])) == {
        "pjb:vol/pjb-049-2007/page/0007/figure/p7_b001",
        "pjb:vol/pjb-049-2007/page/0007/figure/p7_b002",
    }


def test_pages_without_figures_have_no_hasPart():
    cfg = _cfg()
    pages = [_page(1), _page(2)]  # No visual blocks
    doc = build_volume_graph(cfg, [], pages, toc=None)
    graph = doc["@graph"]
    p1 = _by_id(graph, "pjb:vol/pjb-049-2007/page/0001")
    assert "hasPart" not in p1


def test_figure_without_caption_gets_a_synthesised_name():
    cfg = _cfg()
    p = _page(3, [_block("p3_b001", "diagram", [10, 10, 100, 100], text="")])
    doc = build_volume_graph(cfg, [], [p], toc=None)
    graph = doc["@graph"]
    fig = _by_id(graph, "pjb:vol/pjb-049-2007/page/0003/figure/p3_b001")
    assert fig is not None
    assert fig["regionType"] == "diagram"
    assert fig["name"] == "Diagram on page 3"
    # An empty caption shouldn't produce a description field.
    assert "description" not in fig


# ---------------------------------------------------------------------------
# Footnotes are now also anchored to their page
# ---------------------------------------------------------------------------

def test_footnotes_carry_inPage_when_page_num_is_known():
    cfg = _cfg()
    p5 = _page(5, [
        _block("p5_b001", "text",     [100, 100, 1300, 1500], text="body 1^"),
    ])
    p6 = _page(6, [
        _block("p6_b001", "text",     [100, 100, 1300, 1500], text="body"),
        _block("p6_b002", "footnote", [100, 1700, 1300, 1800],
               text="1. A citation.", html="<p>1. A citation.</p>"),
    ])
    art = _article("art01", 1, "Some article", 5, 6, pages=[p5, p6])
    notes = collect_article_footnotes(art)
    doc = build_volume_graph(
        cfg, [art], [p5, p6], toc=None,
        footnotes_by_article={art["id"]: notes},
    )
    graph = doc["@graph"]
    fns = _footnotes(graph)
    assert len(fns) == 1
    fn = fns[0]
    assert fn["footnoteOf"] == "pjb:art/art01"
    # The new field — footnotes now also cluster around their page.
    assert fn["inPage"] == "pjb:vol/pjb-049-2007/page/0006"
    assert fn["pageStart"] == 6


def test_context_defines_visual_region_predicates():
    cfg = _cfg()
    doc = build_volume_graph(cfg, [], [], toc=None)
    ctx = doc["@context"]
    assert ctx["inPage"]["@id"] == "pjb:inPage"
    assert ctx["inPage"]["@type"] == "@id"
    assert "contentUrl" in ctx
    assert "regionType" in ctx
    assert "bbox" in ctx
