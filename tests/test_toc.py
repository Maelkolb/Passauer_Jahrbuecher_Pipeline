"""Tests for ``pjb_pipeline.structure.toc``."""

from pjb_pipeline.structure.toc import (
    parse_toc_text, split_author_title, parse_toc_structure,
)


# A realistic-ish blob of the kind Chandra produces for a Passauer Jahrbücher TOC.
# Real TOCs use German typography and have section headers glued to entries.
EXAMPLE_TOC = (
    "INHALT\n"
    "MITARBEITER .....7"
    "AUFSÄTZE"
    "Hartmut Wolff/Walter Wandling, Lateinische Inschriften "
    "aus dem Passauer Raum ...... 9-42"
    "Karl Schmotz, Archäologische Ausgrabungen in Deggendorf ...... 43-78"
    "BERICHTE"
    "Maria Müller, Bericht zur Tagung 2005 ...... 215"
)


class TestParseTocText:
    def test_extracts_title(self):
        out = parse_toc_text("INHALT\nMITARBEITER .....7")
        assert out[0]["kind"] == "title"
        assert out[0]["text"] == "INHALT"

    def test_extracts_page_numbers(self):
        out = parse_toc_text("Smith, A paper ...... 42")
        assert out[0]["kind"] == "entry"
        assert out[0]["page"] == "42"

    def test_separates_section_headers_from_entries(self):
        out = parse_toc_text(
            "AUFSÄTZE"
            "Wolff, Inschriften ...... 9"
            "BERICHTE"
            "Müller, Bericht ...... 215"
        )
        kinds = [tok["kind"] for tok in out]
        assert kinds == ["header", "entry", "header", "entry"]
        assert out[0]["text"] == "AUFSÄTZE"
        assert out[2]["text"] == "BERICHTE"

    def test_handles_page_ranges(self):
        out = parse_toc_text("Wolff, Lateinische Inschriften ...... 9-42")
        assert out[0]["page"] == "9-42"


class TestSplitAuthorTitle:
    def test_single_author(self):
        author, title = split_author_title("Hartmut Wolff, Lateinische Inschriften")
        assert author == "Hartmut Wolff"
        assert title == "Lateinische Inschriften"

    def test_two_authors_with_slash(self):
        author, title = split_author_title(
            "Hartmut Wolff/Walter Wandling, Lateinische Inschriften aus dem Passauer Raum"
        )
        assert author == "Hartmut Wolff/Walter Wandling"
        assert title == "Lateinische Inschriften aus dem Passauer Raum"

    def test_no_comma_returns_full_title(self):
        author, title = split_author_title("Ein Buch ohne Autor")
        # No author: better to keep the full title than mis-attribute
        assert author == ""
        assert title == "Ein Buch ohne Autor"

    def test_empty_input(self):
        assert split_author_title("") == ("", "")

    def test_compound_german_names(self):
        author, title = split_author_title(
            "Karl-Heinz Müller-Bauer, Untersuchungen zur Geschichte"
        )
        assert "Müller-Bauer" in author
        assert title == "Untersuchungen zur Geschichte"


class TestParseTocStructure:
    def test_parses_full_example(self):
        toc = parse_toc_structure(EXAMPLE_TOC)
        # Should detect at least 3 article entries
        assert len(toc.entries) >= 3
        # Sections should be picked up
        section_names = {name for name, _ in toc.sections}
        # Note: parser title-cases the section names, so they become "Aufsätze" etc.
        assert any("Aufsätze" in n or "AUFSÄTZE" in n.upper() for n in section_names)
        assert any("Berichte" in n or "BERICHTE" in n.upper() for n in section_names)

    def test_entries_have_pages(self):
        toc = parse_toc_structure(EXAMPLE_TOC)
        entries_with_pages = [e for e in toc.entries if e.page is not None]
        assert len(entries_with_pages) >= 3

    def test_entries_have_authors(self):
        toc = parse_toc_structure(EXAMPLE_TOC)
        # At least one entry should have a recognised author
        with_author = [e for e in toc.entries if e.author]
        assert len(with_author) >= 1

    def test_known_sections_normalises_label(self):
        toc = parse_toc_structure(
            "AUFSATZE"      # missing umlaut
            "Smith, A paper ...... 9",
            known_sections=("AUFSÄTZE",),
        )
        # The detected section should normalise to "Aufsätze" via the known-list match
        section_names = {name for name, _ in toc.sections}
        # Normaliser strips diacritics, so AUFSATZE matches AUFSÄTZE
        assert "Aufsätze" in section_names

    def test_empty_input_returns_empty_structure(self):
        toc = parse_toc_structure("")
        assert toc.entries == []
        assert toc.sections == []
