"""Tests for ``pjb_pipeline.structure.names`` — author detection in TOC
entries, bylines and contributor lists."""

from pjb_pipeline.structure.names import (
    NameResolver, parse_byline, parse_contributor_entries, split_entry,
    titlecase_name, base_given_names,
)


GIVEN = set(base_given_names())


class TestSeveralAuthors:
    def test_colon_form_comma_list(self):
        p = split_entry("Sebastian Gassner, Nina Kunze, Thomas Maurer, Malte Rehbein: "
                        "Der Niedernburger Fingerring und die germanische Scheibenfibel")
        assert p.authors == ["Sebastian Gassner", "Nina Kunze", "Thomas Maurer", "Malte Rehbein"]
        assert p.title.startswith("Der Niedernburger Fingerring")

    def test_slash_comma_form(self):
        p = split_entry("Hartmut Wolff/Walter Wandling, Lateinische Inschriften aus dem Passauer Raum")
        assert p.authors == ["Hartmut Wolff", "Walter Wandling"]
        assert p.title == "Lateinische Inschriften aus dem Passauer Raum"

    def test_comma_list_before_title(self):
        p = split_entry("Marie-Christine Batke, Helmut Bender, Mario Bloier, Emmi Federhofer, "
                        "Andreas Schafitzl, Das römische Materialdepot von Essenbach")
        assert len(p.authors) == 5
        assert p.title == "Das römische Materialdepot von Essenbach"

    def test_und_join_in_colon_form(self):
        p = split_entry("Astrid Christl-Sorcan und Nicole Eller: Ein Titel")
        assert p.authors == ["Astrid Christl-Sorcan", "Nicole Eller"]

    def test_shared_surname(self):
        p = split_entry("Franziska, Karl und Georg R. Rettenbacher, Goldstickerei. "
                        "Ein Bilder- und Werkbuch. (Martin Ortmeier)", review_context=True)
        assert p.is_review
        assert p.reviewed_authors == ["Franziska Rettenbacher", "Karl Rettenbacher",
                                      "Georg R. Rettenbacher"]

    def test_unknown_given_name_in_slash_list(self):
        p = split_entry("Peter Morsbach / Wilkin Spitta, Dorfkirchen in der Oberpfalz (Weiß)",
                        review_context=True)
        assert p.reviewed_authors == ["Peter Morsbach", "Wilkin Spitta"]


class TestNotAName:
    def test_title_with_comma(self):
        assert split_entry("Alltag, der nicht alltäglich war. Passauer Schülerinnen").authors == []

    def test_capitalised_title_words(self):
        assert split_entry("Waldviertler Biographien, Bd. 1").authors == []

    def test_register(self):
        assert split_entry("Orts-, Personen-, Sachregister").authors == []

    def test_title_colon(self):
        p = split_entry("Albrecht Classen, Trauer müssen sie tragen: Postklassische Ästhetik")
        assert p.authors == ["Albrecht Classen"]
        assert p.title.startswith("Trauer müssen sie tragen")


class TestNameShapes:
    def test_non_ascii_given_name(self):
        assert split_entry("Lubomír Tyllner, Zur Erforschung der Ensembles").authors == ["Lubomír Tyllner"]

    def test_title_starting_with_a_year(self):
        p = split_entry("Heinz Kellermann, 1484 – Das erste Büchsenschießen in Passau")
        assert p.authors == ["Heinz Kellermann"]

    def test_nobiliary_particle(self):
        assert split_entry("Marc von Knorring: Akteure oder Zuschauer?").authors == ["Marc von Knorring"]

    def test_deceased_marker(self):
        p = split_entry("Willibald Ernst †: Die ehemaligen Adelssitze von Gangkofen")
        assert p.authors == ["Willibald Ernst"]

    def test_author_named_at_the_end(self):
        p = split_entry("Nachruf auf Willibald Ernst von Walter Hartinger")
        assert p.authors == ["Walter Hartinger"]


class TestReviews:
    def test_reviewer_is_the_author(self):
        p = split_entry("Egon Boshof, Die Regesten der Bischöfe von Passau, Band 4. (Andreas Fohrer)",
                        review_context=True)
        assert p.is_review
        assert p.authors == ["Andreas Fohrer"]
        assert p.reviewed_authors == ["Egon Boshof"]
        assert p.reviewed_title.startswith("Die Regesten")

    def test_editors_of_reviewed_book(self):
        p = split_entry("Katharina Weigand, Jörg Zedler, Florian Schuller (Hg.), "
                        "Die Prinzregentenzeit. (Hans-Christof Kraus)", review_context=True)
        assert p.authors == ["Hans-Christof Kraus"]
        assert p.reviewed_editors == ["Katharina Weigand", "Jörg Zedler", "Florian Schuller"]
        assert p.reviewed_authors == []

    def test_newer_reviewer_colon_format(self):
        p = split_entry("Franz-Reiner Erkens: Siegfried Haider, Die Traditionsurkunden "
                        "des Klosters Garsten", review_context=True)
        assert p.is_review
        assert p.authors == ["Franz-Reiner Erkens"]
        assert p.reviewed_authors == ["Siegfried Haider"]

    def test_embedded_editor(self):
        p = split_entry("Die Bischöfe des Heiligen Römischen Reiches 1198 bis 1448, hrsg. von "
                        "Erwin Gatz (Boshof)", review_context=True)
        assert p.authors == ["Boshof"]
        assert p.reviewed_editors == ["Erwin Gatz"]

    def test_surname_only_reviewer_is_resolved(self):
        r = NameResolver(["Reinhard Heydenreuter", "Egon Boshof", "Marc von Knorring",
                          "Friedrich Ulf Röhre-Ertl"])
        assert r.resolve("Heydenreuter") == "Reinhard Heydenreuter"
        assert r.resolve("R. Heydenreuter") == "Reinhard Heydenreuter"
        assert r.resolve("von Knorring") == "Marc von Knorring"
        assert r.resolve("Mark v. Knorring") == "Marc von Knorring"
        assert r.resolve("Unbekannt") == "Unbekannt"
        # a complete name is never rewritten (the contributor list has an OCR slip)
        assert r.resolve("Friedrich Ulf Röhrer-Ertl") == "Friedrich Ulf Röhrer-Ertl"
        assert r.resolve("Egon Boshoff") == "Egon Boshoff"

    def test_obituary_author_in_brackets(self):
        p = split_entry("Alfred Fuchs zum Gedenken (P. Praxl)")
        assert not p.is_review
        assert p.authors == ["P. Praxl"]


class TestBylineAndContributors:
    def test_titlecase(self):
        assert titlecase_name("HANS-WERNER EROMS") == "Hans-Werner Eroms"
        assert titlecase_name("MARC VON KNORRING") == "Marc von Knorring"
        assert titlecase_name("Already Mixed") == "Already Mixed"

    def test_byline_several_names(self):
        assert parse_byline("SEBASTIAN GASSNER, NINA KUNZE", GIVEN) == ["Sebastian Gassner", "Nina Kunze"]

    def test_byline_rejects_title(self):
        assert parse_byline("Die Stadtgemeinde Passau im Ersten Weltkrieg", GIVEN) is None

    def test_contributor_list(self):
        paras = [
            "Becker, Winfried, Prof. em. Dr. phil.,\nMax-Matheis-Str. 46, 94036 Passau",
            "Huber, Markus T., Dr. phil. Dipl.-Ing.,\nwissenschaftlicher Mitarbeiter",
            "Knorring, Marc von, Dr. phil.,\nUniversität Passau",
            "E-Mail: someone@example.org",
        ]
        names = [c.name for c in parse_contributor_entries(paras, GIVEN)]
        assert names == ["Winfried Becker", "Markus T. Huber", "Marc von Knorring"]
