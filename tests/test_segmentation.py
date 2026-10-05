"""Block-level article segmentation and the TOC token stream
(``pjb_pipeline.structure.articles`` / ``toc``)."""

from pjb_pipeline.config import VolumeConfig
from pjb_pipeline.structure.articles import detect_articles, printed_page_mapper
from pjb_pipeline.structure.toc import collect_toc_tokens, parse_toc_structure, parse_toc_text

LABELS = ("AUFSÄTZE", "BERICHTE", "REZENSIONEN", "BUCHBESPRECHUNGEN")


def _cfg():
    return VolumeConfig(volume_number=99, volume_number_roman="XCIX", volume_year=2026,
                        slug="pjb-099-2026", printed_page_offset=0,
                        toc_section_labels=LABELS)


def _page(pn, blocks, w=1500, h=2200):
    return {"page_num": pn, "image_filename": f"page_{pn:04d}.png",
            "image_width": w, "image_height": h, "blocks": blocks}


def _b(bid, typ, y0, y1, text="", html="", x0=100, x1=1400):
    return {"id": bid, "type": typ, "raw_type": typ, "bbox": [x0, y0, x1, y1],
            "text": text, "html": html}


def _texts(article):
    return [b["id"] for p in article["pages"] for b in p["blocks"]]


class TestTocTokens:
    def test_section_headings_outside_the_toc_block(self):
        pages = [
            _page(1, [
                _b("h", "section-header", 100, 140, "INHALT"),
                _b("t1", "table-of-contents", 200, 900,
                   "AUFSÄTZE Hans Huber: Ein Aufsatz ..... 3"),
            ]),
            _page(2, [
                _b("s", "section-header", 100, 140, "BUCHBESPRECHUNGEN"),
                _b("s2", "section-header", 150, 190, "Rezensionen"),
                _b("t2", "table-of-contents", 200, 900,
                   "Egon Boshof, Die Regesten der Bischöfe, Band 4. (Andreas Fohrer) ..... 5"),
            ]),
        ]
        toc = parse_toc_structure(tokens=collect_toc_tokens(pages, LABELS), known_sections=LABELS)
        assert [e.section for e in toc.entries] == ["Aufsätze", "Rezensionen"]
        review = toc.entries[1]
        assert review.is_review and review.authors == ["Andreas Fohrer"]
        assert review.reviewed_authors == ["Egon Boshof"]

    def test_bare_page_numbers(self):
        toks = parse_toc_text("Oliver Stoll: Johannes Prammer (Hg.), Siedlungsdynamik 303 "
                              "Antje Hausold: Florian Himmeler, Exploratio Danubiae 305")
        assert [t["page"] for t in toks if t["kind"] == "entry"] == ["303", "305"]

    def test_number_inside_a_title_is_not_a_page(self):
        toks = parse_toc_text("Gisa Schäffer-Huber: Nach 200 Jahren wieder ans Licht geholt: "
                              "Reliefs ..... 347")
        entries = [t for t in toks if t["kind"] == "entry"]
        assert len(entries) == 1 and entries[0]["page"] == "347"

    def test_spaced_dot_leaders(self):
        toks = parse_toc_text("VORWORT Franz-Reiner Erkens: Neunzig Jahre . . . 11 "
                              "AUFSÄTZE Egon Boshof: Die Synode ..... 15")
        assert [t["page"] for t in toks if t["kind"] == "entry"] == ["11", "15"]

    def test_first_entry_is_not_the_toc_title(self):
        toks = parse_toc_text("MITARBEITER ..... \n 7 \n AUFSÄTZE \n Hans Huber, Titel ..... 9")
        assert toks[0]["kind"] == "entry" and toks[0]["page"] == "7"


class TestBlockLevelArticles:
    def _volume(self):
        toc = _page(1, [_b("toc", "table-of-contents", 200, 1900,
                           "INHALT\nBUCHBESPRECHUNGEN Erika Muster, Die Burg im Wald. "
                           "(Hans Huber) ..... 2 Karl Beispiel, Das Kloster am Fluss. "
                           "(Paul Praxl) ..... 2")])
        p2 = _page(2, [
            _b("a_cit", "text", 200, 300, "Erika Muster, DIE BURG IM WALD, Passau 2010, 200 S."),
            _b("a_txt", "text", 320, 900, "Die Rezension der Burg beginnt hier und endet.",
               html="<p>Die Rezension der Burg<sup>1</sup> beginnt hier und endet.</p>"),
            _b("a_sig", "text", 910, 950, "Hans Huber"),
            _b("b_cit", "text", 1000, 1100, "Karl Beispiel, DAS KLOSTER AM FLUSS, Linz 2011, 90 S."),
            _b("b_txt", "text", 1120, 1800, "Die zweite Rezension läuft auf die nächste"),
            _b("fn1", "footnote", 1850, 1900, "1 Eine Anmerkung zur Burg."),
        ])
        p3 = _page(3, [_b("b_end", "text", 200, 600, "Seite weiter und endet dort."),
                       _b("b_sig", "text", 620, 660, "Paul Praxl")])
        return [toc, p2, p3]

    def test_shared_page_is_split_between_reviews(self):
        arts, _ = detect_articles(self._volume(), _cfg())
        real = [a for a in arts if a["title"] != "Frontmatter"]
        assert len(real) == 2
        a, b = real
        assert a["authors"] == ["Hans Huber"] and b["authors"] == ["Paul Praxl"]
        assert a["review"]["authors"] == ["Erika Muster"]
        assert _texts(a) == ["a_cit", "a_txt", "a_sig", "fn1"]
        assert _texts(b) == ["b_cit", "b_txt", "b_end", "b_sig"]
        assert (a["page_first"], a["page_last"]) == (2, 2)
        assert (b["page_first"], b["page_last"]) == (2, 3)
        assert a["start_matched"] and b["start_matched"]

    def test_byline_starts_the_article_and_is_not_repeated(self):
        pages = [
            _page(1, [_b("toc", "table-of-contents", 200, 900,
                         "INHALT\nAUFSÄTZE Hans Huber: Die Geschichte der Stadt ..... 2 "
                         "Paul Praxl: Ein zweiter Beitrag ..... 3")]),
            _page(2, [_b("by", "text", 200, 240, "HANS HUBER"),
                      _b("ti", "section-header", 260, 320, "Die Geschichte der Stadt"),
                      _b("tx", "text", 340, 1900, "Text des ersten Beitrags.")]),
            _page(3, [_b("ti2", "section-header", 260, 320, "Ein zweiter Beitrag"),
                      _b("tx2", "text", 340, 1900, "Text.")]),
        ]
        arts, _ = detect_articles(pages, _cfg())
        first = [a for a in arts if a["title"] != "Frontmatter"][0]
        assert _texts(first)[:2] == ["by", "ti"]
        assert set(first["_skip_block_ids"]) == {"by", "ti"}

    def test_local_page_offsets_around_plates(self):
        pages = []
        # printed = pdf - 1 up to pdf 26, then 5 plates, then printed = pdf - 6
        for pdf in range(2, 27):
            pages.append(_page(pdf, [_b(f"f{pdf}", "page-footer", 2100, 2130, str(pdf - 1))]))
        for pdf in range(27, 32):
            pages.append(_page(pdf, [_b(f"i{pdf}", "image", 200, 2000)]))
        for pdf in range(32, 60):
            pages.append(_page(pdf, [_b(f"f{pdf}", "page-footer", 2100, 2130, str(pdf - 6))]))
        to_pdf = printed_page_mapper(pages, offset=6)
        assert to_pdf(9) == 10
        assert to_pdf(40) == 46
