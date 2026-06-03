"""Footnote detection and reference linking.

The pipeline produces three things here:

1. A per-page list of footnote blocks with their parsed leading number
   (``footnote_num``) so a renderer can use the *real* footnote number
   instead of a fresh sequential counter.

2. A list of *reference markers* found inside body text, each with the
   character offset, the matched number, and the resolved footnote block
   id (when found within the same article scope).

3. Helpers to rewrite text into HTML with anchor tags around the markers
   that link to the footnote element by id.

The notebook already wrapped ``\\d+\\^`` markers in ``<sup>`` tags but
didn't connect them to anything. This module does the linking.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple


# Patterns of footnote *references* (the marker inside body text).
# Chandra emits at least two styles; both are common in scanned scholarly text.
#
#   "1^"   – Chandra superscript token: digit followed by caret
#   "1."   – very common in old German typography
#   plain superscript at end of word: " word¹²"
# The first pattern is by far the most reliable, so we anchor on it and
# fall back to others only when it's absent on a page.
_REF_PATTERNS = [
    # Digit followed by literal "^" — the canonical Chandra marker
    re.compile(r"(?<!\d)(?P<n>\d{1,3})\^"),
    # Unicode superscript digits stuck onto a word boundary
    re.compile(r"(?<=\w)(?P<n>[\u00b9\u00b2\u00b3\u2070-\u2079]+)"),
]

# A footnote *body* usually starts with its own number, in one of these
# forms. We extract that number so the renderer can re-use it.
_FN_BODY_NUM = re.compile(r"^\s*(\d{1,3})[.\u00b0)\s]\s*")


_SUPERSCRIPT_DIGITS = {
    "\u00b9": "1", "\u00b2": "2", "\u00b3": "3",
    "\u2070": "0", "\u2074": "4", "\u2075": "5", "\u2076": "6",
    "\u2077": "7", "\u2078": "8", "\u2079": "9",
}


def _superscript_to_int(s: str) -> Optional[int]:
    digits = "".join(_SUPERSCRIPT_DIGITS.get(c, "") for c in s)
    return int(digits) if digits else None


@dataclass
class FootnoteRef:
    """A reference marker inside body text."""
    article_id: str
    block_id:   str           # body block containing the marker
    n:          int           # the marker's number
    start:      int           # char offset into block.text
    end:        int           # char offset into block.text
    target_id:  Optional[str] = None  # block id of the footnote (when resolved)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Footnote:
    """A footnote body block, with its parsed number and target HTML id."""
    article_id: str
    block_id:   str
    n:          int
    text:       str
    html_id:    str           # the anchor id used in HTML output
    page_num:   Optional[int] = None   # PDF page this footnote sits on

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def parse_footnote_body(text: str) -> Tuple[Optional[int], str]:
    """Pull the leading number out of a footnote body block.

    Returns ``(number, body_without_number)``. If no leading number is
    detected, returns ``(None, text)``.
    """
    if not text:
        return None, ""
    m = _FN_BODY_NUM.match(text)
    if not m:
        return None, text.strip()
    n = int(m.group(1))
    return n, text[m.end():].strip()


def collect_article_footnotes(article: dict) -> List[Footnote]:
    """Walk every page of an article, find footnote blocks, parse their
    leading number, and return a list ordered by appearance."""
    notes: List[Footnote] = []
    seq = 1
    for p in article.get("pages", []):
        for blk in p["blocks"]:
            if blk["type"] != "footnote":
                continue
            n, body = parse_footnote_body(blk.get("text", ""))
            if n is None:
                n = seq
            notes.append(Footnote(
                article_id=article["id"],
                block_id=blk["id"],
                n=n,
                text=body,
                html_id=f"fn-{article['id']}-{n}",
                page_num=p.get("page_num"),
            ))
            seq += 1
    return notes


def find_refs_in_text(text: str) -> List[Tuple[int, int, int]]:
    """Find footnote reference markers in ``text``.

    Returns ``[(start, end, number), …]`` sorted by ``start``. Each entry
    refers to the original ``text`` slice ``text[start:end]``.
    """
    found: List[Tuple[int, int, int]] = []
    for pat in _REF_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group("n")
            # ASCII digits use int(); Unicode superscripts ("¹²³") are
            # ``.isdigit() is True`` but ``int(...)`` raises — route those
            # through the superscript mapper.
            if raw.isascii() and raw.isdigit():
                n = int(raw)
            else:
                n = _superscript_to_int(raw) or 0
            if n <= 0:
                continue
            # Filter out matches where the number is clearly part of a year
            # ("1925^" is real; "1925" alone in a sentence shouldn't fire,
            # but the "^" anchor already protects us from that). For the
            # superscript path we additionally require n < 500 — anything
            # larger is almost certainly not a footnote ref.
            if pat is _REF_PATTERNS[1] and n >= 500:
                continue
            found.append((m.start(), m.end(), n))
    found.sort()
    # Deduplicate overlapping matches (the two patterns can both fire on
    # the same chars in pathological cases)
    deduped: List[Tuple[int, int, int]] = []
    for s, e, n in found:
        if deduped and s < deduped[-1][1]:
            continue
        deduped.append((s, e, n))
    return deduped


def link_article_footnotes(article: dict) -> Tuple[List[Footnote], List[FootnoteRef]]:
    """For one article, collect footnotes and find/resolve references to
    them inside the body text. Returns ``(notes, refs)``."""
    notes = collect_article_footnotes(article)
    notes_by_n: Dict[int, Footnote] = {fn.n: fn for fn in notes}

    refs: List[FootnoteRef] = []
    for p in article.get("pages", []):
        for blk in p["blocks"]:
            if blk["type"] in ("footnote", "page-header", "page-footer", "table-of-contents"):
                continue
            text = blk.get("text", "") or ""
            for s, e, n in find_refs_in_text(text):
                target = notes_by_n.get(n)
                refs.append(FootnoteRef(
                    article_id=article["id"],
                    block_id=blk["id"],
                    n=n,
                    start=s,
                    end=e,
                    target_id=target.html_id if target else None,
                ))
    return notes, refs


# ---------------------------------------------------------------------------
# Render helper: rewrite a text into HTML with linked refs
# ---------------------------------------------------------------------------

def rewrite_text_with_links(
    text: str,
    refs_in_block: List[FootnoteRef],
) -> str:
    """Build an HTML string from ``text`` with ``<sup class="fn-ref">``
    anchors wrapping each ref position. ``text`` may contain inline HTML
    that's already been sanitised; we tag-balance around it by working on
    the *original* string and escaping non-ref text.

    Refs are expected to come from the same block as ``text``, in
    ``find_refs_in_text`` order.
    """
    if not refs_in_block:
        return html.escape(text, quote=False)

    parts: List[str] = []
    pos = 0
    for ref in sorted(refs_in_block, key=lambda r: r.start):
        if ref.start < pos:
            continue  # overlap, skip
        parts.append(html.escape(text[pos:ref.start], quote=False))
        if ref.target_id:
            parts.append(
                f'<sup class="fn-ref"><a href="#{html.escape(ref.target_id)}">'
                f'{ref.n}</a></sup>'
            )
        else:
            parts.append(f'<sup class="fn-ref">{ref.n}</sup>')
        pos = ref.end
    parts.append(html.escape(text[pos:], quote=False))
    return "".join(parts)
