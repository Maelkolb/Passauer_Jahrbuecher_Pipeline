"""Structured Table-of-Contents parsing.

The OCR puts the entire front-matter Inhalt page into a single
``table-of-contents`` block whose ``text`` is a noisy run-on string like::

    INHALT
    MITARBEITER ..... 7
    AUFSÄTZE
    Hartmut Wolff/Walter Wandling, Lateinische Inschriften... 9
    ...

This module turns that blob into a structured list of :class:`TocEntry`
records. Each entry has a ``section`` label, an ``author``, a ``title``,
and the printed page number where the article starts.

This data is later used by :mod:`pjb_pipeline.structure.articles` to anchor
article boundaries precisely — instead of guessing from section-header
positions on each page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Iterable, Set, Tuple

from .names import (
    NameResolver, base_given_names, paragraphs_from_block,
    parse_contributor_entries, split_entry,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TocEntry:
    """One real article in the TOC (not a header, not a title)."""
    raw_text:    str                    # the OCR chunk before cleanup
    title:       str
    author:      str
    page:        Optional[int]          # printed page number ("9" → 9)
    page_end:    Optional[int] = None   # if range "9-42"
    section:     str = ""               # "Aufsätze" / "Berichte" / …
    # Individual persons. ``author`` above is the display string
    # ("Hartmut Wolff / Walter Wandling"); ``authors`` is the list the
    # knowledge graph uses. For a book review the authors are the
    # *reviewers*, and the reviewed book's people sit in ``reviewed_*``.
    authors:          List[str] = field(default_factory=list)
    editors:          List[str] = field(default_factory=list)
    is_review:        bool = False
    reviewed_title:   str = ""
    reviewed_authors: List[str] = field(default_factory=list)
    reviewed_editors: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TocStructure:
    """Whole-document TOC. Renderers also use this."""
    title:      str = ""                # the top-of-page heading ("INHALT")
    entries:    List[TocEntry] = field(default_factory=list)
    # Parsed sections, in document order, with their entries grouped.
    # Useful for the knowledge graph (each section becomes a node).
    sections:   List[Tuple[str, List[TocEntry]]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "title":    self.title,
            "entries":  [e.as_dict() for e in self.entries],
            "sections": [(name, [e.as_dict() for e in es]) for name, es in self.sections],
        }


# ---------------------------------------------------------------------------
# Low-level parsing (also used by HTML renderer for the unstructured fallback)
# ---------------------------------------------------------------------------

# "...N" or ". . . . N" or page ranges "...N-M"
_PAGE_MARKER = re.compile(
    r"\s*(?:\.{2,}|(?:\.\s){2,}\.)\s*(\d{1,4}(?:[\u2013\-]\d{1,4})?)\s*"
)

# A bare page number that ends an entry: followed by the next entry's
# "Name Name:" or by the end of the text. Only used when a TOC block has
# no dot leaders at all.
_BARE_PAGE = re.compile(
    r"(?<=\S)\s+(\d{1,3})(?=\s+[A-ZÄÖÜ][\w'’\-]*\.?"
    r"(?:(?:\s+|,\s*|\s*/\s*)(?:[A-ZÄÖÜ][\w'’\-]*\.?|von|van|de|zu|der|und|u\.)){0,8}\s*:\s|\s*$)"
)

_BARE_NUM = re.compile(r"(?<=\S)\s+(\d{1,3})(?=\s+[A-ZÄÖÜ„»\"]|\s*$)")
_NUM_CONTEXT = re.compile(r"(?:Band|Bd\.|Bde\.|Heft|Nr\.|Teil|S\.|Folge|Jg\.|Jahrgang|Abb\.|Tafel|Kap\.)\s*$")


def _insert_leaders_at_bare_pages(work: str, weak: bool = True) -> str:
    """Turn bare page numbers that close a TOC entry into dot leaders.

    A number counts when it is followed by the next entry's "Name:" (the
    strong signal), or — failing that — when it continues the increasing
    page sequence, is followed by a capitalised word, and is not part of a
    citation ("Band 4", "Heft 1")."""
    out: List[str] = []
    pos = 0
    last = 0
    for m in _BARE_NUM.finditer(work):
        n = int(m.group(1))
        before = work[:m.start()]
        if re.search(r"\.\s?\.\s*$", before):
            continue  # already has dot leaders
        if before[-1:].isdigit() and not re.search(r"(?<!\d)\d{4}$", before):
            continue  # "1918/19 bis 1933" — only a year may precede the page
        strong = bool(_BARE_PAGE.match(work, m.start())) or (
            before[-1:].isdigit() and not work[m.end():].strip()[:1].isdigit())
        if not strong:
            if not weak or n < max(5, last) or _NUM_CONTEXT.search(work[:m.start()]):
                continue
            if last and n - last > 60:
                continue
        out.append(work[pos:m.start()])
        out.append(f" ..... {m.group(1)} ")
        pos = m.end()
        last = max(last, n)
    out.append(work[pos:])
    return "".join(out)


# An UPPERCASE run of 3+ chars at the start of a chunk, optionally with
# spaces, immediately followed (after optional whitespace) by a Title-Case
# word — i.e. the section label glued to the next entry.
_LEADING_HEADER = re.compile(
    r"^([A-ZÄÖÜ][A-ZÄÖÜ]{2,}(?:\s+[A-ZÄÖÜ][A-ZÄÖÜ]{1,})*)\s*(?=[A-ZÄÖÜ][a-zäöüß])"
)


def parse_toc_text(text: str) -> List[dict]:
    """Low-level TOC tokeniser used by the HTML renderer and by the
    higher-level :func:`parse_toc_structure` below.

    Returns dicts of one of three shapes::

        {"kind": "title",   "text": "INHALT",   "page": None}
        {"kind": "header",  "text": "AUFSÄTZE", "page": None}
        {"kind": "entry",   "text": "Wolff, Inschriften ...", "page": "9"}
    """
    if not text:
        return []
    out: List[dict] = []
    work = text

    # First line like "INHALT" → render as the TOC title
    head_split = work.split("\n", 1)
    if len(head_split) == 2:
        first = head_split[0].strip()
        # Only a bare heading ("INHALT") is the title — not the first entry
        # ("MITARBEITER ..... 7"), whose page number sits on the next line.
        if (first and first.isupper() and 2 <= len(first) <= 30
                and ".." not in first and not re.search(r"\d", first)):
            out.append({"kind": "title", "text": first, "page": None})
            work = head_split[1]

    # Some TOC lines lack dot leaders ("… Republik 303 Antje Hausold: …");
    # insert them where a bare page number closes an entry. When the block
    # has leaders elsewhere, only the strong "page + next 'Name:'" signal
    # counts.
    work = _insert_leaders_at_bare_pages(work, weak=not _PAGE_MARKER.search(work))

    pos = 0
    for m in _PAGE_MARKER.finditer(work):
        chunk = work[pos:m.start()].strip()
        page  = m.group(1)
        if chunk:
            hm = _LEADING_HEADER.match(chunk)
            if hm:
                out.append({"kind": "header",
                            "text": hm.group(1).strip(),
                            "page": None})
                chunk = chunk[hm.end():].strip()
            if chunk:
                out.append({"kind": "entry", "text": chunk, "page": page})
            else:
                if out and out[-1]["kind"] == "header":
                    h = out.pop()
                    out.append({"kind": "entry", "text": h["text"], "page": page})
        pos = m.end()

    tail = work[pos:].strip()
    if tail:
        if re.fullmatch(r"[A-ZÄÖÜ][A-ZÄÖÜ\s]{2,}", tail):
            out.append({"kind": "header", "text": tail, "page": None})
        else:
            out.append({"kind": "entry", "text": tail, "page": None})

    return out


# ---------------------------------------------------------------------------
# High-level: split each entry into (authors, title)
# ---------------------------------------------------------------------------
#
# Author shapes ("Hartmut Wolff/Walter Wandling, Title", "A, B und C: Title",
# "Katharina Weigand, Jörg Zedler (Hg.), Title", reviews "Book citation
# (Reviewer)") are handled by :mod:`pjb_pipeline.structure.names`.

# Section labels whose entries are book reviews.
REVIEW_SECTIONS = {"buchbesprechungen", "rezensionen", "besprechungen",
                   "buchbesprechung", "rezension"}


def is_review_section(label: str) -> bool:
    return (label or "").strip().lower() in REVIEW_SECTIONS


def format_authors(names: List[str]) -> str:
    """Display string for a list of persons, as the journal writes it."""
    return " / ".join(n for n in names if n)


def split_author_title(entry_text: str):
    """Split a TOC entry into (author, title). Back-compat wrapper around
    :func:`pjb_pipeline.structure.names.split_entry`; the author string
    joins several persons with " / ". Falls back to ("", entry_text)."""
    if not entry_text:
        return "", ""
    p = split_entry(entry_text)
    if p.is_review:
        return format_authors(p.authors), p.title
    return format_authors(p.authors), p.title


def _parse_page(p: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """``"9-42"`` → ``(9, 42)``; ``"9"`` → ``(9, None)``; missing → ``(None, None)``."""
    if not p:
        return None, None
    p = p.strip()
    if "-" in p or "\u2013" in p:
        parts = re.split(r"[-\u2013]", p)
        try:
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass
    try:
        return int(p), None
    except ValueError:
        return None, None


def parse_toc_structure(
    text: str = "",
    *,
    known_sections: Iterable[str] = (),
    tokens: Optional[List[dict]] = None,
    given_names: Optional[Set[str]] = None,
    resolver: Optional[NameResolver] = None,
) -> TocStructure:
    """Parse a raw TOC blob (or a pre-built token stream) into a structured
    object.

    ``known_sections`` is an optional list of expected uppercase section
    labels (``("AUFSÄTZE", "BERICHTE")`` etc.). The parser uses it to
    normalise capitalisation oddities like ``AUFSATZE`` (missing umlaut)
    or ``A U F S Ä T Z E`` (letter-spaced).

    ``tokens`` (from :func:`collect_toc_tokens`) replaces ``text`` when the
    TOC spans several blocks and pages. ``given_names`` extends the given-name
    lexicon, ``resolver`` maps surname-only reviewers ("(Heydenreuter)") to
    the full names of the volume's contributor list.
    """
    raw_tokens = tokens if tokens is not None else parse_toc_text(text)
    given = set(base_given_names()) | set(given_names or ())
    known_keys = resolver.keys() if resolver else None

    known_lookup = {_canon_label(s): s.title() for s in known_sections}

    structure = TocStructure()
    current_section = ""
    current_section_entries: List[TocEntry] = []

    def flush_section():
        if current_section and current_section_entries:
            structure.sections.append((current_section, list(current_section_entries)))
        current_section_entries.clear()

    for tok in raw_tokens:
        if tok["kind"] == "title":
            structure.title = tok["text"]
        elif tok["kind"] == "header":
            flush_section()
            current_section = section_label(tok["text"], known_lookup)
        elif tok["kind"] == "skip" or _FRONTMATTER_LIST.match(tok["text"].strip()):
            continue
        else:  # entry
            parsed = split_entry(tok["text"], given, known=known_keys,
                                 review_context=is_review_section(current_section))
            authors = list(parsed.authors)
            if resolver is not None:
                authors = [resolver.resolve(a) for a in authors]
                # Outside a review section a surname-only "reviewer" that is
                # not among the volume's contributors is no reviewer — the
                # parenthetical is something else ("(NDB)", a place name).
                if (parsed.is_review and known_keys
                        and not is_review_section(current_section)
                        and any(len(a.split()) == 1 for a in authors)):
                    parsed = split_entry(tok["text"], given, known=known_keys,
                                         allow_review=False)
                    authors = [resolver.resolve(a) for a in parsed.authors]
            page, page_end = _parse_page(tok.get("page"))
            section = current_section
            if tok.get("secondary") or _BIBLIOGRAPHY_TITLE.search(parsed.title):
                section = "Bibliographie"
            elif _REGISTER_TITLE.search(parsed.title):
                section = "Register"
            entry = TocEntry(
                raw_text=tok["text"],
                title=parsed.title,
                author=format_authors(authors),
                page=page,
                page_end=page_end,
                section=section,
                authors=authors,
                editors=list(parsed.editors),
                is_review=parsed.is_review,
                reviewed_title=parsed.reviewed_title,
                reviewed_authors=list(parsed.reviewed_authors),
                reviewed_editors=list(parsed.reviewed_editors),
            )
            structure.entries.append(entry)
            current_section_entries.append(entry)

    flush_section()
    return structure


def _canon_label(s: str) -> str:
    s = re.sub(r"\s+", "", s).upper()
    return s.replace("Ä", "A").replace("Ö", "O").replace("Ü", "U").replace("ß", "S")


def section_label(text: str, known_lookup: Dict[str, str]) -> str:
    """Canonical display label for a section heading."""
    label = re.sub(r"\s+", " ", text).strip()
    if _canon_label(label) in known_lookup:
        return known_lookup[_canon_label(label)]
    letters = [c for c in label if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return label.title()
    return label


# TOC lines that are front-matter lists, not articles.
_FRONTMATTER_LIST = re.compile(
    r"^(?:verzeichnis\s+der\s+(?:mitarbeiter|abbildungen|tafeln)|mitarbeiter(?:innen)?"
    r"|inhalt|inhaltsverzeichnis)\s*$", re.I)
# The annual regional bibliography and the registers close every volume;
# they are no part of the review section that precedes them in the TOC.
_BIBLIOGRAPHY_TITLE = re.compile(r"^Neuerscheinungen zur Geschichte", re.I)
_REGISTER_TITLE = re.compile(r"register\s*$", re.I)

# Headings that open a list on a TOC page which is *not* part of the
# article inventory (list of figures/plates).
_NON_ARTICLE_LISTS = re.compile(r"verzeichnis\s+der\s+(?:abbildungen|tafeln)", re.I)
_TOC_TITLE = re.compile(r"^\s*(?:inhalt|inhaltsverzeichnis)\s*$", re.I)


def _is_section_heading(text: str, known_lookup: Dict[str, str]) -> bool:
    """A section heading on a TOC page is an all-caps label (AUFSÄTZE,
    BUCHBESPRECHUNGEN) or a known label; mixed-case sub-headings
    ("Rezensionen", "Beiträge der Tagung …") are not sections."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t or len(t) > 40:
        return False
    if _canon_label(t) in known_lookup:
        return True
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def _is_blank_page(page: dict) -> bool:
    return not any((b.get("text") or "").strip() for b in page["blocks"]
                   if b["type"] not in ("image", "figure", "diagram",
                                        "page-header", "page-footer"))


def collect_toc_tokens(
    unified_pages: list,
    known_sections: Iterable[str] = (),
) -> List[dict]:
    """Build the TOC token stream from every block on the TOC pages.

    Chandra often puts the section headings of a TOC page ("BUCHBESPRECHUNGEN")
    into separate ``section-header`` blocks *outside* the ``table-of-contents``
    block, so reading only the TOC blocks loses them and the reviews end up
    filed under the previous section. Here the TOC pages are walked in
    reading order: section headings become ``header`` tokens, TOC blocks are
    tokenised with :func:`parse_toc_text`, and entries under a "Verzeichnis
    der Abbildungen" heading (a list of plates, not articles) are skipped.

    Only the *main* TOC run (the contiguous pages starting at the first TOC
    page) contributes headings; later TOC blocks (e.g. the bibliography's
    own "Inhaltsübersicht") only contribute entries, as before.
    """
    known_lookup = {_canon_label(s): s.title() for s in known_sections}
    toc_pages = [p for p in unified_pages
                 if any(b["type"] == "table-of-contents" for b in p["blocks"])]
    if not toc_pages:
        return []
    # The main TOC run: contiguous TOC pages, allowing blank pages between
    # them (vol. 52 has a blank verso between INHALT and its continuation).
    by_pn = {p["page_num"]: p for p in unified_pages}
    toc_pns = {p["page_num"] for p in toc_pages}
    main_run = {toc_pages[0]["page_num"]}
    pn = toc_pages[0]["page_num"] + 1
    while pn in by_pn and (pn in toc_pns or _is_blank_page(by_pn[pn])):
        if pn in toc_pns:
            main_run.add(pn)
        pn += 1

    tokens: List[dict] = []
    for p in toc_pages:
        in_main = p["page_num"] in main_run
        skipping = False
        for b in p["blocks"]:
            text = (b.get("text") or "").strip()
            if b["type"] == "section-header" and in_main:
                if _TOC_TITLE.match(text):
                    if not any(t["kind"] == "title" for t in tokens):
                        tokens.append({"kind": "title", "text": text, "page": None})
                    continue
                if _NON_ARTICLE_LISTS.search(text):
                    skipping = True
                    continue
                if _is_section_heading(text, known_lookup):
                    skipping = False
                    tokens.append({"kind": "header", "text": text, "page": None})
                continue
            if b["type"] != "table-of-contents" or not text:
                continue
            for tok in parse_toc_text(text):
                if tok["kind"] == "header" and _NON_ARTICLE_LISTS.search(tok["text"]):
                    skipping = True
                    continue
                if tok["kind"] == "header":
                    skipping = False
                    if not in_main:
                        continue
                if tok["kind"] == "title" and (not in_main or
                                               any(t["kind"] == "title" for t in tokens)):
                    continue
                if skipping and tok["kind"] == "entry":
                    tok = {**tok, "kind": "skip"}
                elif not in_main and tok["kind"] == "entry":
                    tok = {**tok, "secondary": True}
                tokens.append(tok)
    return tokens


# ---------------------------------------------------------------------------
# Contributor list ("MITARBEITER" / "Verzeichnis der Mitarbeiter")
# ---------------------------------------------------------------------------

_CONTRIB_HEADING = re.compile(r"mitarbeiter|autorinnen|autoren\s+(?:dieses|des)", re.I)


def find_contributors(unified_pages: list) -> NameResolver:
    """Collect the volume's contributor list into a :class:`NameResolver`.

    The list ("Becker, Winfried, Prof. em. Dr. phil., …") sits on a page
    headed "MITARBEITER" in the front matter. Its full names let the parser
    resolve surname-only reviewers of older volumes ("(Heydenreuter)" →
    "Reinhard Heydenreuter") and validate author names.
    """
    given = set(base_given_names())
    resolver = NameResolver()
    for p in unified_pages:
        heads = [b for b in p["blocks"] if b["type"] in ("section-header", "text")
                 and _CONTRIB_HEADING.search(b.get("text") or "")
                 and len(b.get("text") or "") < 60]
        if not heads:
            continue
        if any(b["type"] == "table-of-contents" for b in p["blocks"]):
            continue
        y0 = min(h["bbox"][1] for h in heads)
        paras: List[str] = []
        for b in p["blocks"]:
            if b["type"] in ("list", "text", "table") and b["bbox"][1] >= y0 - 5:
                paras.extend(paragraphs_from_block(b))
        for c in parse_contributor_entries(paras, given):
            resolver.add(c.name)
    return resolver


def find_toc_blocks(unified_pages: list) -> List[dict]:
    """Return all ``table-of-contents`` blocks in document order.

    A volume occasionally has the TOC on two pages; we keep all of them and
    concatenate downstream.
    """
    blocks = []
    for p in unified_pages:
        for b in p["blocks"]:
            if b["type"] == "table-of-contents":
                blocks.append({**b, "_page_num": p["page_num"]})
    return blocks
