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
from typing import List, Optional, Iterable, Tuple


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
    r"\s*\.{2,}\s*(\d{1,4}(?:[\u2013\-]\d{1,4})?)\s*"
)

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
        if first and first.isupper() and 2 <= len(first) <= 30:
            out.append({"kind": "title", "text": first, "page": None})
            work = head_split[1]

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
# High-level: split each entry into (author, title)
# ---------------------------------------------------------------------------

# Author tokens at the start of an entry follow one of these shapes:
#   "Hartmut Wolff/Walter Wandling, Title"           (comma; older volumes)
#   "Helmut Böhm: Title"                              (colon; newer volumes)
#   "Astrid Christl-Sorcan und Nicole Eller: Title"   (colon + German "und")
#   "Helmut W. Schaller: Title"                       (middle initial)
#   "Hans v. Aufseß, Title"                           (nobiliary particle)
#
# A *name token* is an initial ("W.") or a capitalised, possibly hyphenated
# word ("Christl-Sorcan"). A *person* is one or more name tokens with an
# optional nobiliary particle (von/van/de). Multiple authors are joined by
# "/", "&", "und", or "u.".  The colon form requires the leading author to
# be >= 2 name tokens so a title with an early colon isn't mistaken for one.
_NAME = r"(?:[A-ZÄÖÜ]\.|[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)"
_VONS = r"(?:v\.|von|van|de|d')"
_PERSON  = rf"{_NAME}(?:\s+(?:{_VONS}\s+)?{_NAME})*"
_PERSON2 = rf"{_NAME}(?:\s+(?:{_VONS}\s+)?{_NAME})+"
_JOIN = r"(?:\s*[/&]\s*|\s+und\s+|\s+u\.\s+)"
_AUTHORS       = rf"{_PERSON}(?:{_JOIN}{_PERSON})*"
_AUTHORS_COLON = rf"{_PERSON2}(?:{_JOIN}{_PERSON})*"

_COMMA_RE = re.compile(rf"^(?P<author>{_AUTHORS})\s*,\s+(?P<title>.+)$", re.DOTALL)
_COLON_RE = re.compile(rf"^(?P<author>{_AUTHORS_COLON})\s*:\s+(?P<title>.+)$", re.DOTALL)
_AUTHOR_RE = _COMMA_RE  # back-compat alias


def split_author_title(entry_text: str):
    """Split a TOC entry into (author, title). Handles both the comma form
    used by older volumes and the colon form used by newer ones. Falls back
    to ("", entry_text) when neither matches."""
    if not entry_text:
        return "", ""
    text = re.sub(r"\s+", " ", entry_text).strip()
    for rx in (_COMMA_RE, _COLON_RE):
        m = rx.match(text)
        if m:
            return m.group("author").strip(" ,.;"), m.group("title").strip()
    return "", text


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
    text: str,
    *,
    known_sections: Iterable[str] = (),
) -> TocStructure:
    """Parse a raw TOC blob into a structured object.

    ``known_sections`` is an optional list of expected uppercase section
    labels (``("AUFSÄTZE", "BERICHTE")`` etc.). The parser uses it to
    normalise capitalisation oddities like ``AUFSATZE`` (missing umlaut)
    or ``A U F S Ä T Z E`` (letter-spaced).
    """
    raw_tokens = parse_toc_text(text)

    # Build a fast lookup of "label without diacritics & whitespace" →
    # canonical label.
    def canon(s: str) -> str:
        s = re.sub(r"\s+", "", s).upper()
        s = s.replace("Ä", "A").replace("Ö", "O").replace("Ü", "U").replace("ß", "S")
        return s
    known_lookup = {canon(s): s.title() for s in known_sections}

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
            label = tok["text"].strip()
            current_section = known_lookup.get(canon(label), label.title())
        else:  # entry
            author, title = split_author_title(tok["text"])
            page, page_end = _parse_page(tok.get("page"))
            entry = TocEntry(
                raw_text=tok["text"],
                title=title,
                author=author,
                page=page,
                page_end=page_end,
                section=current_section,
            )
            structure.entries.append(entry)
            current_section_entries.append(entry)

    flush_section()
    return structure


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
