"""Tests for ``pjb_pipeline.structure.layout`` — print-size calibration,
block-role correction and reading order."""

from pjb_pipeline.structure.layout import (
    Typography, calibrate, continuity, order_page, reclassify_blocks, xy_cut,
)

TYPO = Typography(body=1.6, small=3.4, threshold=2.33)

# ~1.6 chars per 1000 px² at width 1500: a 600×500 box holds 480 chars of
# body text, the same box holds ~1000 chars of footnote print.
BODY = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
        "tempor incididunt ut labore et dolore magna aliqua. ") * 4
SMALL = ("12 Vgl. Egon BOSHOF, Die Regesten der Bischöfe von Passau, Bd. 1, "
         "München 1992, S. 45 (wie Anm. 3). ") * 10


def _page(blocks, w=1500, h=2200, pn=1):
    return {"page_num": pn, "image_filename": f"page_{pn:04d}.png",
            "image_width": w, "image_height": h, "blocks": blocks}


def _b(bid, typ, bbox, text="", html=""):
    return {"id": bid, "type": typ, "raw_type": typ, "bbox": bbox, "text": text, "html": html}


def _ids(page):
    return [b["id"] for b in page["blocks"]]


class TestCalibration:
    def test_two_print_sizes_are_separated(self):
        pages = []
        for i in range(15):
            pages.append(_page([
                _b(f"t{i}", "text", [100, 200, 700, 700], BODY),
                _b(f"f{i}", "footnote", [100, 1700, 700, 1900], SMALL[:430]),
            ]))
        typo = calibrate(pages)
        assert typo.body < typo.threshold < typo.small
        assert 1.3 < typo.body < 2.0
        assert 2.8 < typo.small < 4.5


class TestFootnoteReclassification:
    def test_small_numbered_text_at_column_foot_becomes_footnote(self):
        page = _page([
            _b("body1", "text", [100, 200, 700, 700], BODY),
            _b("body2", "text", [100, 720, 700, 1220], BODY),
            _b("note", "text", [100, 1700, 700, 1900], SMALL[:430]),
        ])
        changes = reclassify_blocks(page, TYPO)
        assert ("note", "text", "footnote") in changes
        assert page["blocks"][2]["type"] == "footnote"
        assert page["blocks"][2]["raw_type"] == "text"

    def test_block_quote_inside_the_column_stays_text(self):
        quote = ("Und so heißt es in der Urkunde des Klosters, dass alle Güter "
                 "an den Bischof fallen sollten. ") * 8
        page = _page([
            _b("body1", "text", [100, 200, 700, 700], BODY),
            _b("quote", "text", [140, 720, 660, 920], quote),
            _b("body2", "text", [100, 940, 700, 1440], BODY),
        ])
        reclassify_blocks(page, TYPO)
        assert page["blocks"][1]["type"] == "text"

    def test_quotation_after_colon_without_number_stays_text(self):
        quote = ("Und so heißt es in der Urkunde des Klosters, dass alle Güter "
                 "an den Bischof fallen sollten. ") * 8
        page = _page([
            _b("body1", "text", [100, 200, 700, 700], BODY[:-2] + " folgendes:"),
            _b("quote", "text", [140, 1700, 660, 1900], quote),
        ])
        reclassify_blocks(page, TYPO)
        assert page["blocks"][1]["type"] == "text"

    def test_bibliography_page_is_left_alone(self):
        entries = "712 Rauch, Alois, Geschichte von Bayern. – München 2010. 160 S. " * 9
        page = _page([_b(f"e{i}", "text", [100, 200 + i * 220, 700, 400 + i * 220], entries[:420])
                      for i in range(7)])
        assert reclassify_blocks(page, TYPO, prev_footnote=None) == []

    def test_page_of_notes(self):
        blocks = [_b(f"n{i}", "text", [100, 200 + i * 150, 700, 330 + i * 150],
                     f"{20 + i}  Vgl. MÜLLER (wie Anm. 3), S. {i + 10}. " * 6)
                  for i in range(6)]
        page = _page(blocks)
        reclassify_blocks(page, TYPO, prev_footnote={"text": "19 Ebd., S. 4."})
        assert all(b["type"] == "footnote" for b in page["blocks"])

    def test_caption_next_to_figure(self):
        page = _page([
            _b("fig", "image", [100, 200, 700, 800]),
            _b("cap", "text", [100, 810, 700, 860], "Abb. 3: Der Dom von Süden."),
        ])
        reclassify_blocks(page, TYPO)
        assert page["blocks"][1]["type"] == "caption"

    def test_model_description_on_blank_page(self):
        page = _page([_b("x", "text", [100, 200, 1300, 2000],
                         "Faint, illegible text covering the majority of the page.")])
        reclassify_blocks(page, TYPO)
        assert page["blocks"][0]["type"] == "image"
        assert page["blocks"][0]["text"] == ""


class TestXYCut:
    def test_title_then_columns(self):
        blocks = [
            _b("r1", "text", [760, 400, 1400, 900], "right top"),
            _b("l1", "text", [100, 400, 740, 900], "left top"),
            _b("t", "section-header", [300, 200, 1200, 260], "Title"),
            _b("l2", "text", [100, 910, 740, 1500], "left bottom"),
            _b("r2", "text", [760, 910, 1400, 1500], "right bottom"),
        ]
        assert [b["id"] for b in xy_cut(blocks)] == ["t", "l1", "l2", "r1", "r2"]

    def test_one_block_per_column(self):
        blocks = [_b("r", "text", [760, 200, 1400, 2000]), _b("l", "text", [100, 200, 740, 2000])]
        assert [b["id"] for b in xy_cut(blocks)] == ["l", "r"]

    def test_figure_across_the_gutter(self):
        # vol. 55 p. 213: the figure crosses the gutter, its caption sits right
        blocks = [
            _b("fig", "figure", [180, 250, 990, 1400]),
            _b("cap", "caption", [1020, 1160, 1430, 1300], "Abb. 3: …"),
            _b("l1", "text", [170, 1450, 790, 1800], "left"),
            _b("r1", "text", [810, 1450, 1430, 1820], "right"),
        ]
        assert [b["id"] for b in xy_cut(blocks)] == ["fig", "cap", "l1", "r1"]


class TestContinuity:
    def test_hyphenation(self):
        assert continuity("Die vom Kaiser verlie-", "henen Feldzeichen") > 0
        assert continuity("Die vom Kaiser verlie-", "Das Feldzeichen") < 0

    def test_lowercase_after_full_stop(self):
        assert continuity("Damit endet der Satz.", "und dann geht es weiter") < 0

    def test_abbreviations_and_ordinals_do_not_end_sentences(self):
        assert continuity("Daher schien es Pius XI.", "im Zeitalter der Monarchien") >= 0
        assert continuity("vgl. dazu z. B.", "die Urkunde") >= 0

    def test_mid_sentence(self):
        assert continuity("und wurde im Jahr 1407 durch den", "bischöflichen Rat bestätigt") > 0


class TestOrderPage:
    def test_text_continuity_picks_the_right_candidate(self):
        # Chandra read the right column first; geometry and text agree it is wrong.
        page = _page([
            _b("hdr", "page-header", [700, 100, 760, 130], "12"),
            _b("r", "text", [760, 300, 1400, 1900], "henen Stangenfeldzeichen nahmen im Kultleben einen Platz ein."),
            _b("l", "text", [100, 300, 740, 1900], "Die vom Kaiser verlie-"),
            _b("fn", "footnote", [100, 1950, 740, 2000], "1 Vgl. Stoll."),
            _b("ftr", "page-footer", [700, 2100, 760, 2130], "12"),
        ])
        report = order_page(page)
        assert _ids(page) == ["hdr", "l", "r", "fn", "ftr"]
        assert report["strategy"] in ("xy-columns", "bands")

    def test_previous_page_tail_counts(self):
        page = _page([
            _b("a", "text", [100, 300, 1400, 900], "Neuer Absatz beginnt hier."),
            _b("b", "text", [100, 950, 1400, 1500], "weiter im Satz der vorigen Seite."),
        ])
        order_page(page, prev_tail={"text": "ein Satz, der auf der vorigen Seite"})
        # geometry wins: b is below a in the same column; a stays first
        assert _ids(page) == ["a", "b"]
