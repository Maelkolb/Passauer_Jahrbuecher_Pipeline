#!/usr/bin/env python3
"""Apply two additional pipeline fixes on top of the prior patches:

  * Footnote linking — context-aware plain-digit detection plus standard
    markdown footnote syntax in the wiki, so inline body refs become
    clickable in Obsidian / Pandoc / GitHub.
  * Image descriptions — surface Chandra's <img alt="…"> description as a
    first-class ``description`` field on visual blocks, used by the wiki
    figure marker and the JSON-LD ImageObject node.

Plus updates two tests in tests/test_wiki.py that asserted the old
``1. text`` numbered-list footnote format (now superseded by markdown
footnote syntax) and appends regression tests for the new behaviour.

Idempotent: every patch step checks a marker before applying.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(".").resolve()


def in_repo() -> bool:
    return (
        (REPO / "pjb_pipeline" / "normalize.py").exists()
        and (REPO / "pjb_pipeline" / "emit" / "wiki.py").exists()
        and (REPO / "pjb_pipeline" / "emit" / "graph.py").exists()
        and (REPO / "pjb_pipeline" / "structure" / "footnotes.py").exists()
    )


def backup_once(path: pathlib.Path, tag: str = "bak3") -> None:
    bak = path.with_suffix(f"{path.suffix}.{tag}")
    if not bak.exists():
        shutil.copy2(path, bak)


def replace_once(path: str, marker: str, old: str, new: str, what: str) -> None:
    f = REPO / path
    s = f.read_text(encoding="utf-8")
    if marker in s:
        print(f"  {path}: skip ({what} already applied)")
        return
    if old not in s:
        sys.exit(f"\n  ERROR: cannot find anchor for {what} in {path}.\n"
                 f"  Has the file drifted from the snapshot?\n")
    backup_once(f)
    f.write_text(s.replace(old, new, 1), encoding="utf-8")
    print(f"  {path}: applied ({what})")


def append_once(path: str, marker: str, addition: str, what: str) -> None:
    f = REPO / path
    s = f.read_text(encoding="utf-8")
    if marker in s:
        print(f"  {path}: skip ({what} already appended)")
        return
    backup_once(f)
    sep = "" if s.endswith("\n\n") else ("\n" if s.endswith("\n") else "\n\n")
    f.write_text(s + sep + addition, encoding="utf-8")
    print(f"  {path}: appended ({what})")


if not in_repo():
    sys.exit("Run this from the Passauer_Jahrbuecher_Pipeline root.")

print(f"Applying patches in {REPO}\n")

# ---------------------------------------------------------------------------
# normalize.py — surface Chandra's alt-description on visual blocks
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/normalize.py",
    marker="def _extract_chandra_alt",
    old='''from __future__ import annotations

import json
from pathlib import Path
from typing import List

from tqdm.auto import tqdm

from .config import VolumeConfig
from .structure.columns import detect_columns, assign_columns, reading_order''',
    new='''from __future__ import annotations

import html as html_module
import json
import re
from pathlib import Path
from typing import List

from tqdm.auto import tqdm

from .config import VolumeConfig
from .structure.columns import detect_columns, assign_columns, reading_order

# Visual block types whose Chandra-generated alt-text description we want
# to surface as a first-class ``description`` field. The graph emitter and
# the wiki emitter both fall back to this when the block has no Chandra-
# extracted text caption of its own.
_VISUAL_TYPES_FOR_DESCRIPTION = {"image", "figure", "diagram"}

_IMG_ALT_RE = re.compile(r\'<img\\b[^>]*\\balt="([^"]*)"\', re.IGNORECASE | re.DOTALL)


def _extract_chandra_alt(html: str) -> str:
    """Pull the alt-text description out of an ``<img alt="…">`` tag.

    Chandra emits visual blocks with the layout-only text field empty and
    the actual description tucked into the ``alt`` attribute of an
    ``<img>`` element inside the block\'s ``html`` field. Without this,
    every figure in the wiki and every ImageObject in the JSON-LD graph
    would carry only a generic placeholder name. Returns ``""`` if no
    alt text is found.
    """
    if not html:
        return ""
    m = _IMG_ALT_RE.search(html)
    if not m:
        return ""
    return html_module.unescape(m.group(1)).strip()''',
    what="add Chandra-alt helper + visual-type set",
)

replace_once(
    "pjb_pipeline/normalize.py",
    marker="Surface Chandra's alt-text description",
    old='''    blocks = []
    for b in raw_doc.get("blocks", []):
        blocks.append({
            "id":       b["id"],
            "type":     canonical_type(b.get("type", "text")),
            "raw_type": b.get("type", "text"),
            "bbox":     to_pixel_bbox(b.get("bbox"), w, h),
            "text":     b.get("text", "").strip(),
            "html":     b.get("html", "").strip(),   # rich HTML content from Chandra
        })''',
    new='''    blocks = []
    for b in raw_doc.get("blocks", []):
        canon = canonical_type(b.get("type", "text"))
        html_field = b.get("html", "").strip()
        blk: dict = {
            "id":       b["id"],
            "type":     canon,
            "raw_type": b.get("type", "text"),
            "bbox":     to_pixel_bbox(b.get("bbox"), w, h),
            "text":     b.get("text", "").strip(),
            "html":     html_field,
        }
        # Surface Chandra\'s alt-text description for visual blocks as a
        # dedicated ``description`` field. Downstream emitters (wiki,
        # graph) read it when no human-extracted caption text is
        # available.
        if canon in _VISUAL_TYPES_FOR_DESCRIPTION:
            desc = _extract_chandra_alt(html_field)
            if desc:
                blk["description"] = desc
        blocks.append(blk)''',
    what="wire description into build_unified_page",
)

# ---------------------------------------------------------------------------
# structure/footnotes.py — is_numbered flag, plain-digit detector,
# per-page lookup, html_id scoping
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/structure/footnotes.py",
    marker="is_numbered: bool = True",
    old='''@dataclass
class Footnote:
    """A footnote body block, with its parsed number and target HTML id."""
    article_id: str
    block_id:   str
    n:          int
    text:       str
    html_id:    str           # the anchor id used in HTML output
    page_num:   Optional[int] = None   # PDF page this footnote sits on

    def as_dict(self) -> dict:
        return asdict(self)''',
    new='''@dataclass
class Footnote:
    """A footnote body block, with its parsed number and target HTML id."""
    article_id: str
    block_id:   str
    n:          int
    text:       str
    html_id:    str           # the anchor id used in HTML output
    page_num:   Optional[int] = None   # PDF page this footnote sits on
    is_numbered: bool = True  # ``True`` if ``n`` was parsed from the body
                              # text (a real footnote reference target);
                              # ``False`` if we had to auto-assign a
                              # sequence number because the block had no
                              # leading digit (asterisk notes, abbrevia-
                              # tion lists). Body refs only link to
                              # numbered footnotes — the unnumbered ones
                              # are bibliographic preamble that no inline
                              # ref points to.

    def as_dict(self) -> dict:
        return asdict(self)''',
    what="add is_numbered flag to Footnote",
)

replace_once(
    "pjb_pipeline/structure/footnotes.py",
    marker="unnumbered_seq_by_page",
    old='''def collect_article_footnotes(article: dict) -> List[Footnote]:
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
                html_id=f"fn-{article[\'id\']}-{n}",
                page_num=p.get("page_num"),
            ))
            seq += 1
    return notes''',
    new='''def collect_article_footnotes(article: dict) -> List[Footnote]:
    """Walk every page of an article, find footnote blocks, parse their
    leading number, and return a list ordered by appearance.

    A footnote block whose body starts with a digit (most footnotes) gets
    that digit as its ``n`` and is marked ``is_numbered=True`` — these
    are real footnotes that body refs can point at. A block whose body
    has no leading digit (the asterisk note at the bottom of the first
    page of an article, the abbreviation key list, etc.) is given a
    per-page sequence number and marked ``is_numbered=False``; nothing
    in the body references those.

    ``html_id`` is page-scoped because footnote numbers restart per page
    in this corpus, so two footnotes can legitimately share ``n``;
    without page scoping the anchors would collide in HTML output and
    the markdown footnote IDs in the wiki would be ambiguous.
    """
    notes: List[Footnote] = []
    unnumbered_seq_by_page: Dict[Optional[int], int] = {}
    for p in article.get("pages", []):
        page_num = p.get("page_num")
        for blk in p["blocks"]:
            if blk["type"] != "footnote":
                continue
            parsed_n, body = parse_footnote_body(blk.get("text", ""))
            if parsed_n is not None:
                n = parsed_n
                is_numbered = True
            else:
                seq = unnumbered_seq_by_page.get(page_num, 0) + 1
                unnumbered_seq_by_page[page_num] = seq
                n = seq
                is_numbered = False
            p_tag = "p???" if page_num is None else f"p{int(page_num):03d}"
            u_tag = "" if is_numbered else "u"
            notes.append(Footnote(
                article_id=article["id"],
                block_id=blk["id"],
                n=n,
                text=body,
                html_id=f"fn-{article[\'id\']}-{p_tag}-{u_tag}{n}",
                page_num=page_num,
                is_numbered=is_numbered,
            ))
    return notes''',
    what="track numbered vs unnumbered + scope html_id by page",
)

replace_once(
    "pjb_pipeline/structure/footnotes.py",
    marker="_PLAIN_DIGIT_REF = ",
    old='''def find_refs_in_text(text: str) -> List[Tuple[int, int, int]]:''',
    new='''# Plain ASCII-digit footnote references — the dominant style on this
# corpus, since Chandra emits "Regimentes 1 ." rather than "Regimentes\u00b9"
# or "Regimentes 1^". The pattern is intentionally tight to keep false-
# positives low:
#
#   word-char + space(s) + 1-3 digit number + space(s) + sentence-end punct
#
# Sentence-end punctuation means "." "," ";" ":" "!" "?" "-" ")" "]" or
# end-of-paragraph. Year numbers like "1825" are four digits and don\'t
# match. Citation lists like "Tac. ann. 1, 17, 6" have no space between
# the digit and the comma, so don\'t match. The match still has to clear
# an additional veto: the matched number must actually be present in
# the per-page numbered-footnote set passed to
# ``find_plain_digit_refs`` — without that, "Punkt 7 ." or "ist 2 mal
# so" would be linked even on pages where no footnote 7 or 2 exists.
_PLAIN_DIGIT_REF = re.compile(
    r"(?<=\\w)\\s+(?P<n>\\d{1,3})(?=\\s+[.,;:!?\\-)\\]]|\\s*$)"
)


def find_plain_digit_refs(
    text: str,
    numbered_set: set,
) -> List[Tuple[int, int, int]]:
    """Find plain-digit footnote refs in body text.

    Only emits matches whose number is in ``numbered_set`` — typically
    the set of footnote numbers actually present on the same page. This
    is the veto that keeps the detector from linking every stray
    sentence-end digit to an unrelated footnote elsewhere in the
    article.

    Match positions cover only the digit characters themselves, so a
    rewriter can replace just the digit with a markdown footnote marker
    and leave the surrounding whitespace and punctuation intact.
    """
    if not numbered_set:
        return []
    found: List[Tuple[int, int, int]] = []
    for m in _PLAIN_DIGIT_REF.finditer(text):
        n = int(m.group("n"))
        if n not in numbered_set:
            continue
        found.append((m.start("n"), m.end("n"), n))
    return found


def numbered_footnotes_by_page(notes: List[Footnote]) -> Dict[Optional[int], set]:
    """Build ``{page_num: {set of numbered footnote ns on that page}}``."""
    by_page: Dict[Optional[int], set] = {}
    for fn in notes:
        if fn.is_numbered:
            by_page.setdefault(fn.page_num, set()).add(fn.n)
    return by_page


def find_refs_in_text(text: str) -> List[Tuple[int, int, int]]:''',
    what="add plain-digit detector + per-page lookup helper",
)

replace_once(
    "pjb_pipeline/structure/footnotes.py",
    marker="notes_by_page_n: Dict",
    old='''def link_article_footnotes(article: dict) -> Tuple[List[Footnote], List[FootnoteRef]]:
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
    return notes, refs''',
    new='''def link_article_footnotes(article: dict) -> Tuple[List[Footnote], List[FootnoteRef]]:
    """For one article, collect footnotes and find/resolve references to
    them inside the body text. Returns ``(notes, refs)``.

    Resolution is scoped to the page the body block sits on — journal
    footnotes restart numbering per page in this corpus, so a body
    "bezogen 2 ." on page 19 must resolve to the footnote labelled "2"
    on page 19, not the one on page 18.
    """
    notes = collect_article_footnotes(article)
    notes_by_page_n: Dict[Tuple[Optional[int], int], Footnote] = {
        (fn.page_num, fn.n): fn for fn in notes if fn.is_numbered
    }
    numbered_by_page = numbered_footnotes_by_page(notes)

    refs: List[FootnoteRef] = []
    for p in article.get("pages", []):
        page_num = p.get("page_num")
        page_numbered = numbered_by_page.get(page_num, set())
        for blk in p["blocks"]:
            if blk["type"] in ("footnote", "page-header", "page-footer", "table-of-contents"):
                continue
            text = blk.get("text", "") or ""
            for s, e, n in find_refs_in_text(text):
                target = notes_by_page_n.get((page_num, n))
                refs.append(FootnoteRef(
                    article_id=article["id"],
                    block_id=blk["id"],
                    n=n,
                    start=s,
                    end=e,
                    target_id=target.html_id if target else None,
                ))
            for s, e, n in find_plain_digit_refs(text, page_numbered):
                target = notes_by_page_n.get((page_num, n))
                refs.append(FootnoteRef(
                    article_id=article["id"],
                    block_id=blk["id"],
                    n=n,
                    start=s,
                    end=e,
                    target_id=target.html_id if target else None,
                ))
    return notes, refs''',
    what="per-page footnote ref resolution + plain-digit matching",
)

# ---------------------------------------------------------------------------
# emit/wiki.py — figure description, footnote-ref insertion, markdown syntax
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/emit/wiki.py",
    marker="_PLAIN_DIGIT_REF",
    old='''from ..config import VolumeConfig
from ..structure.footnotes import Footnote
from ..structure.toc import TocStructure''',
    new='''from ..config import VolumeConfig
from ..structure.footnotes import Footnote, _PLAIN_DIGIT_REF
from ..structure.toc import TocStructure''',
    what="import plain-digit regex",
)

replace_once(
    "pjb_pipeline/emit/wiki.py",
    marker="Chandra-extracted caption text",
    old='''def _figure_md(blk: dict, label: str) -> str:
    """Inline image reference for a figure block.

    The image path is relative to the *article* markdown file\'s location
    (``wiki/articles/<art>.md``), which is two levels deep from the volume
    root, so we hop up two directories to reach ``regions/``.
    """
    desc = (blk.get("text") or "").strip()
    body = f"> **[{label} \u00b7 {blk[\'id\']}]**"
    if desc:
        body += f" {desc}"
    return body + "\\n"''',
    new='''def _figure_md(blk: dict, label: str) -> str:
    """Inline image reference for a figure block.

    Renders a quoted text marker so the wiki carries no binary image
    payload. If the block has no human-extracted caption text but Chandra
    produced a description (surfaced as ``description`` by
    :mod:`pjb_pipeline.normalize`), we use that — the journal\'s own
    caption block lives in a separate ``caption``-typed block and will
    follow this one in reading order anyway, so the two read together as
    *marker \u00b7 Chandra description / Journal caption*.
    """
    desc = (blk.get("text") or "").strip()
    if not desc:
        desc = (blk.get("description") or "").strip()
    body = f"> **[{label} \u00b7 {blk[\'id\']}]**"
    if desc:
        body += f" {desc}"
    return body + "\\n"''',
    what="use Chandra description in figure marker",
)

replace_once(
    "pjb_pipeline/emit/wiki.py",
    marker="def _footnote_id",
    old='''def _render_footnotes_section(notes: List[Footnote]) -> str:
    """Render the article\'s footnotes, grouped by source page.

    Journal footnotes in this corpus restart numbering per page, so a
    single flat ``1. \u2026 2. \u2026`` list ends up with multiple "1." entries
    once you have more than one page of footnotes. Grouping by source
    page preserves the original numbering scheme and makes the section
    legible:

        ## Footnotes

        ### Page 18

        1. Stoll, Integration und Abgrenzung 520 \u2026
        2. Zu Fahnentieren wichtig \u2026

        ### Page 19

        3. Stoll, Heer und Gesellschaft \u2026
    """
    if not notes:
        return ""
    by_page: Dict[Optional[int], List[Footnote]] = {}
    for fn in notes:
        by_page.setdefault(fn.page_num, []).append(fn)

    parts: List[str] = ["\\n## Footnotes\\n"]
    for page in sorted(by_page.keys(), key=lambda p: (p is None, p)):
        if page is not None:
            parts.append(f"\\n### Page {page}\\n\\n")
        else:
            parts.append("\\n### Unattributed\\n\\n")
        for fn in by_page[page]:
            text = (fn.text or "").strip().replace("\\n", " ")
            parts.append(f"{fn.n}. {text}\\n")
    return "".join(parts)


def _render_article_body(article: dict, footnotes: List[Footnote]) -> str:
    """Render the article\'s body (per-page) and footnotes (grouped by page).

    The body is structured as ``### Page N`` followed by the page\'s blocks
    in document order. Typographic hyphens that split a word across
    adjacent blocks within the same page are stitched back together
    (``verlie-`` + ``henen Stangenfeldzeichen`` \u2192 ``verliehenen Stangenfeldzeichen``).

    The first page typically opens with the article\'s section-header
    repeating the title (which the TEI emitter strips); we keep it here
    because the markdown reader has the article title only once, in the
    H1 above the body, and a duplicate in-flow heading is survivable.
    """
    parts: List[str] = []
    for pg in article.get("pages", []):
        parts.append(f"### Page {pg[\'page_num\']}\\n\\n")
        for blk in pg.get("blocks", []):
            chunk = _block_to_md(blk)
            if chunk:
                parts.append(chunk)
    body = _join_hyphenation("".join(parts)).rstrip() + "\\n"

    body += _render_footnotes_section(footnotes)
    return body''',
    new='''def _footnote_id(page_num: Optional[int], n: int, *, numbered: bool = True) -> str:
    """Markdown-footnote identifier scoped to the source page.

    Per-page numbering in the corpus means a flat ``[^1]`` would collide
    across pages \u2014 so the IDs are ``p018-1``, ``p019-1``, \u2026 for numbered
    footnotes, and ``p018-u1``, ``p018-u2``, \u2026 for unnumbered ones (the
    asterisk note, the abbreviation list).
    """
    p = "p???" if page_num is None else f"p{int(page_num):03d}"
    return f"{p}-{\'\' if numbered else \'u\'}{n}"


def _insert_footnote_refs(
    text: str,
    page_num: Optional[int],
    page_numbered: set,
) -> str:
    """Rewrite a body chunk so its inline footnote references become
    markdown footnote markers (``[^p018-2]``).

    The rewrite is conservative: it only fires on a plain digit that
    sits between a word and a sentence-end punctuation mark AND that
    matches a real numbered footnote on the same page. Years (four
    digits), citation lists (no spaces around the comma), and
    in-paragraph numbers on pages that happen to have no matching
    footnote all pass through untouched.

    The leading space the regex matches is removed so the marker
    attaches to the preceding word ("bezogen[^p018-2] ." rather than
    "bezogen [^p018-2] ."); the trailing space is preserved so the
    sentence-end punctuation isn\'t smushed against the marker.
    """
    if not page_numbered:
        return text

    def repl(m: "re.Match[str]") -> str:
        n = int(m.group("n"))
        if n not in page_numbered:
            return m.group(0)
        return f"[^{_footnote_id(page_num, n)}]"

    return _PLAIN_DIGIT_REF.sub(repl, text)


def _render_footnotes_section(notes: List[Footnote]) -> str:
    """Render the article\'s footnotes, grouped by source page.

    Real numbered footnotes use standard markdown footnote syntax \u2014
    ``[^p018-1]: \u2026`` \u2014 so a reader\'s markdown viewer (Obsidian, Pandoc,
    GitHub) renders the inline ``[^p018-1]`` in the body as a
    clickable superscript that jumps here. Unnumbered footnotes (the
    asterisk note, the abbreviation key list) get markdown footnote
    syntax too but with a ``u`` in the ID so it can\'t collide with a
    real footnote on the same page; nothing in the body references
    them, so they appear as standalone definitions at the end.
    """
    if not notes:
        return ""
    by_page: Dict[Optional[int], List[Footnote]] = {}
    for fn in notes:
        by_page.setdefault(fn.page_num, []).append(fn)

    parts: List[str] = ["\\n## Footnotes\\n"]
    for page in sorted(by_page.keys(), key=lambda p: (p is None, p)):
        if page is not None:
            parts.append(f"\\n### Page {page}\\n\\n")
        else:
            parts.append("\\n### Unattributed\\n\\n")
        for fn in by_page[page]:
            text = (fn.text or "").strip().replace("\\n", " ")
            fid = _footnote_id(fn.page_num, fn.n, numbered=fn.is_numbered)
            parts.append(f"[^{fid}]: {text}\\n")
    return "".join(parts)


def _render_article_body(article: dict, footnotes: List[Footnote]) -> str:
    """Render the article\'s body (per-page) and footnotes (grouped by page).

    Three text-level transforms run as part of body rendering:

    * **Hyphenation join** \u2014 typographic hyphens that split a word
      across adjacent blocks within the same page are stitched back
      together (``verlie-`` + ``henen Stangenfeldzeichen`` \u2192
      ``verliehenen Stangenfeldzeichen``).
    * **Footnote-ref insertion** \u2014 plain-digit footnote refs in body
      text (``bezogen 2 .``) are rewritten as markdown footnote markers
      (``bezogen[^p018-2] .``) so they become clickable in viewers
      that handle ``[^id]`` syntax.
    * **Page-grouped footnote definitions** \u2014 the ``## Footnotes``
      section at the end of the article uses ``[^id]: \u2026`` syntax,
      grouped by source page so the per-page numbering reads cleanly
      and the inline refs above resolve to a definition here.

    The first page typically opens with the article\'s section-header
    repeating the title (which the TEI emitter strips); we keep it here
    because the markdown reader has the article title only once, in the
    H1 above the body, and a duplicate in-flow heading is survivable.
    """
    from ..structure.footnotes import numbered_footnotes_by_page
    page_numbered = numbered_footnotes_by_page(footnotes)

    parts: List[str] = []
    for pg in article.get("pages", []):
        page_num = pg["page_num"]
        page_set = page_numbered.get(page_num, set())
        parts.append(f"### Page {page_num}\\n\\n")
        for blk in pg.get("blocks", []):
            chunk = _block_to_md(blk)
            if not chunk:
                continue
            # Only rewrite refs inside body text blocks \u2014 never inside
            # footnote bodies themselves (the leading "2 " of "2 Zu
            # Fahnentieren \u2026" would otherwise be eaten), nor inside
            # captions / tables / headers where digits often have other
            # meanings.
            if blk.get("type") in ("text", "list", "bibliography"):
                chunk = _insert_footnote_refs(chunk, page_num, page_set)
            parts.append(chunk)
    body = _join_hyphenation("".join(parts)).rstrip() + "\\n"

    body += _render_footnotes_section(footnotes)
    return body''',
    what="add _footnote_id + _insert_footnote_refs; switch to markdown footnote syntax",
)

# ---------------------------------------------------------------------------
# emit/graph.py — use Chandra description as ImageObject fallback
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/emit/graph.py",
    marker="chandra_desc =",
    old='''            fig_id = figure_iri(cfg, pn, blk["id"])
            figure_iris_on_page.append(fig_id)
            cap = (blk.get("text") or "").strip()
            fig_node: dict = {
                "@id":         fig_id,
                "@type":       "ImageObject",
                "name":        cap or f"{blk[\'type\'].title()} on page {pn}",
                "regionType":  blk["type"],
                "inPage":      page_iri(cfg, pn),
                "pageStart":   pn,
                # A volume-root-relative URL to the crop. Resolves once the
                # crop stage has run; the file lives at
                # ``<output_root>/<slug>/regions/<block_id>.png``.
                "contentUrl":  region_crop_graph_url(blk),
            }
            if cap:
                fig_node["description"] = cap''',
    new='''            fig_id = figure_iri(cfg, pn, blk["id"])
            figure_iris_on_page.append(fig_id)
            cap = (blk.get("text") or "").strip()
            # Chandra often supplies a description as the alt-text of an
            # <img> tag \u2014 surfaced by build_unified_page as
            # ``block["description"]``. Use it when the block has no
            # human-extracted caption text, so the ImageObject node
            # carries something more useful than "Image on page 18".
            chandra_desc = (blk.get("description") or "").strip()
            fig_node: dict = {
                "@id":         fig_id,
                "@type":       "ImageObject",
                "name":        cap or chandra_desc or f"{blk[\'type\'].title()} on page {pn}",
                "regionType":  blk["type"],
                "inPage":      page_iri(cfg, pn),
                "pageStart":   pn,
                # A volume-root-relative URL to the crop. Resolves once the
                # crop stage has run; the file lives at
                # ``<output_root>/<slug>/regions/<block_id>.png``.
                "contentUrl":  region_crop_graph_url(blk),
            }
            if cap:
                fig_node["description"] = cap
            elif chandra_desc:
                fig_node["description"] = chandra_desc''',
    what="use Chandra description in ImageObject node",
)

# ---------------------------------------------------------------------------
# Fix two stale wiki tests that asserted the old ``1. text`` format
# ---------------------------------------------------------------------------
replace_once(
    "tests/test_wiki.py",
    marker="[^p001-1]: First footnote text.",
    old='''    assert "## Footnotes" in body
    assert "1. First footnote text." in body
    assert "2. Second footnote text." in body''',
    new='''    assert "## Footnotes" in body
    # Footnote definitions use standard markdown footnote syntax now,
    # with per-page-scoped IDs, so an inline ``[^p001-1]`` ref in the
    # body resolves cleanly even across pages whose footnote numbers
    # restart.
    assert "[^p001-1]: First footnote text." in body
    assert "[^p001-2]: Second footnote text." in body''',
    what="update body+footnotes test for markdown footnote syntax",
)

replace_once(
    "tests/test_wiki.py",
    marker="[^p018-1]: Stoll",
    old='''        # Two footnotes "1." now \u2014 but they\'re under different page
        # subheaders, so the reader can tell them apart.
        page18 = out.split("### Page 19")[0]
        page19 = out.split("### Page 19")[1]
        assert "1. Stoll" in page18
        assert "1. Ankersdorfer" in page19''',
    new='''        # Markdown-footnote syntax with page-scoped IDs: two "1." entries
        # no longer collide because their IDs (p018-1 vs p019-1) are
        # distinct.
        page18 = out.split("### Page 19")[0]
        page19 = out.split("### Page 19")[1]
        assert "[^p018-1]: Stoll" in page18
        assert "[^p018-2]: Domaszewski" in page18
        assert "[^p019-1]: Ankersdorfer" in page19''',
    what="update grouping test for markdown footnote syntax",
)

# ---------------------------------------------------------------------------
# Append regression tests
# ---------------------------------------------------------------------------
_TEST_NORMALIZE_NEW = '''

class TestChandraAltDescription:
    """Regression for the image-description fix.

    Chandra emits a textual description for every image / figure /
    diagram, but it tucks it into the ``alt`` attribute of an ``<img>``
    tag inside the block\'s ``html`` field \u2014 the block\'s ``text`` field
    is empty. The unify stage now extracts that alt text into a
    first-class ``description`` field so the wiki and the JSON-LD graph
    can pick it up without each re-parsing HTML.
    """

    def test_image_block_gets_alt_text_as_description(self):
        raw = {
            "page_num": 18, "image_filename": "page_0018.png",
            "image_width": 1459, "image_height": 2192,
            "blocks": [
                {"id": "p18_b004", "type": "image",
                 "bbox": [77, 587, 319, 826], "text": "",
                 "html": \'<img alt="A circular seal showing a stork." />\'},
            ],
            "markdown": "",
        }
        u = build_unified_page(raw)
        assert len(u["blocks"]) == 1
        assert u["blocks"][0]["description"] == "A circular seal showing a stork."

    def test_caption_block_does_not_get_description(self):
        # ``caption`` is a separate block type and carries its content in
        # ``text``; we should NOT scrape an alt-tag for it.
        raw = {
            "page_num": 27, "image_filename": "page_0027.png",
            "image_width": 1459, "image_height": 2192,
            "blocks": [
                {"id": "p27_b004", "type": "caption",
                 "bbox": [776, 543, 1391, 600],
                 "text": "Abb. 1: Denare ...",
                 "html": "<p>Abb. 1: Denare ...</p>"},
            ],
            "markdown": "",
        }
        u = build_unified_page(raw)
        assert "description" not in u["blocks"][0]

    def test_image_without_alt_has_no_description(self):
        raw = {
            "page_num": 1, "image_filename": "page_0001.png",
            "image_width": 1000, "image_height": 1500,
            "blocks": [
                {"id": "b1", "type": "image", "bbox": [0, 0, 100, 100],
                 "text": "", "html": "<img />"},
            ],
            "markdown": "",
        }
        u = build_unified_page(raw)
        assert "description" not in u["blocks"][0]

    def test_html_entities_in_alt_are_decoded(self):
        raw = {
            "page_num": 1, "image_filename": "page_0001.png",
            "image_width": 1000, "image_height": 1500,
            "blocks": [
                {"id": "b1", "type": "figure", "bbox": [0, 0, 100, 100],
                 "text": "",
                 "html": \'<img alt="A &amp; B &quot;coin&quot;." />\'},
            ],
            "markdown": "",
        }
        u = build_unified_page(raw)
        assert u["blocks"][0]["description"] == \'A & B "coin".\'
'''

append_once(
    "tests/test_normalize.py",
    marker="class TestChandraAltDescription",
    addition=_TEST_NORMALIZE_NEW,
    what="regression: Chandra alt description",
)


_TEST_FOOTNOTES_NEW = '''

# ---------------------------------------------------------------------------
# Plain-digit ref detection + per-page numbered lookup
# ---------------------------------------------------------------------------

class TestPlainDigitFootnoteRefs:
    """Regression for the new context-aware footnote-ref detector.

    Chandra on this corpus emits inline footnote refs as plain digits
    surrounded by whitespace ("Regimentes 1 .") rather than the ``1^`` or
    Unicode-superscript styles the original detector handles. Adding a
    plain-digit pattern raw would have terrible precision (years,
    citations, list numbering all match). The new detector takes a
    per-page set of *actual* footnote numbers and only emits matches
    whose number is in that set.
    """

    def test_matches_simple_body_ref(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "auf vexilla, also Stoffstandarten, die sich auf die gesamte Einheit bezogen 2 ."
        refs = find_plain_digit_refs(text, {1, 2, 3})
        assert len(refs) == 1
        s, e, n = refs[0]
        assert n == 2
        assert text[s:e] == "2"

    def test_does_not_match_year(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Im Jahr 1825 wurde der Verein gegr\u00fcndet."
        refs = find_plain_digit_refs(text, {1, 2, 1825})
        assert refs == []

    def test_set_vetoes_unrelated_number(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Bei Punkt 7 wird es interessant."
        refs = find_plain_digit_refs(text, {1, 2})
        assert refs == []

    def test_does_not_match_citation_list(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Vgl. Tac. ann. 1, 17, 6 ."
        refs = find_plain_digit_refs(text, {1, 6, 17})
        ns = {n for _, _, n in refs}
        assert 1 not in ns
        assert 17 not in ns

    def test_match_at_end_of_paragraph(self):
        from pjb_pipeline.structure.footnotes import find_plain_digit_refs
        text = "Etwas ganz Besonderes 3"
        refs = find_plain_digit_refs(text, {3})
        assert len(refs) == 1
        assert refs[0][2] == 3


class TestFootnoteIsNumbered:
    """The asterisk footnote and bibliography abbreviation list at the
    bottom of the first page have no leading digit; we no longer treat
    those as referenceable footnotes."""

    def test_parsed_number_marks_numbered(self):
        from pjb_pipeline.structure.footnotes import collect_article_footnotes
        article = {
            "id": "art1",
            "pages": [{
                "page_num": 18,
                "blocks": [
                    {"id": "fn1", "type": "footnote", "text": "1 Real footnote one."},
                    {"id": "fn2", "type": "footnote", "text": "2 Real footnote two."},
                ],
            }],
        }
        notes = collect_article_footnotes(article)
        assert len(notes) == 2
        assert all(fn.is_numbered for fn in notes)
        assert [fn.n for fn in notes] == [1, 2]

    def test_no_leading_digit_marks_unnumbered_with_page_seq(self):
        from pjb_pipeline.structure.footnotes import collect_article_footnotes
        article = {
            "id": "art1",
            "pages": [{
                "page_num": 18,
                "blocks": [
                    {"id": "fnA", "type": "footnote",
                     "text": "* Im vorliegenden Beitrag werden ..."},
                    {"id": "fnB", "type": "footnote",
                     "text": "Ankersdorfer, Studien = ..."},
                ],
            }],
        }
        notes = collect_article_footnotes(article)
        assert len(notes) == 2
        assert all(not fn.is_numbered for fn in notes)
        assert [fn.n for fn in notes] == [1, 2]

    def test_per_page_numbering_does_not_collide(self):
        from pjb_pipeline.structure.footnotes import (
            collect_article_footnotes, numbered_footnotes_by_page,
        )
        article = {
            "id": "art1",
            "pages": [
                {"page_num": 18, "blocks": [
                    {"id": "fn18_1", "type": "footnote", "text": "1 P18 fn1"},
                    {"id": "fn18_2", "type": "footnote", "text": "2 P18 fn2"},
                ]},
                {"page_num": 19, "blocks": [
                    {"id": "fn19_1", "type": "footnote", "text": "1 P19 fn1"},
                    {"id": "fn19_2", "type": "footnote", "text": "2 P19 fn2"},
                ]},
            ],
        }
        notes = collect_article_footnotes(article)
        assert len(notes) == 4
        by_page = numbered_footnotes_by_page(notes)
        assert by_page[18] == {1, 2}
        assert by_page[19] == {1, 2}


class TestLinkArticleFootnotesPerPage:
    """The body resolver must scope to the source page of the body block,
    so per-page footnote numbering doesn\'t cross-link refs."""

    def test_body_ref_on_page_19_resolves_to_page_19_footnote(self):
        from pjb_pipeline.structure.footnotes import link_article_footnotes
        article = {
            "id": "art1",
            "pages": [
                {"page_num": 18, "blocks": [
                    {"id": "fn18_1", "type": "footnote", "text": "1 Page-18 fn1."},
                    {"id": "fn18_2", "type": "footnote", "text": "2 Page-18 fn2."},
                ]},
                {"page_num": 19, "blocks": [
                    {"id": "p19_body", "type": "text",
                     "text": "Etwas ganz Wichtiges bezogen 2 . Mehr Text."},
                    {"id": "fn19_1", "type": "footnote", "text": "1 Page-19 fn1."},
                    {"id": "fn19_2", "type": "footnote", "text": "2 Page-19 fn2."},
                ]},
            ],
        }
        notes, refs = link_article_footnotes(article)
        page19_refs = [r for r in refs if r.block_id == "p19_body" and r.n == 2]
        assert len(page19_refs) == 1
        target = next(fn for fn in notes
                      if fn.html_id == page19_refs[0].target_id)
        assert target.page_num == 19
        assert "Page-19 fn2" in target.text
'''

append_once(
    "tests/test_footnotes.py",
    marker="class TestPlainDigitFootnoteRefs",
    addition=_TEST_FOOTNOTES_NEW,
    what="regression: plain-digit footnote refs + per-page resolution",
)


_TEST_WIKI_NEW = '''

# ---------------------------------------------------------------------------
# Markdown footnote-ref insertion + image alt-text in figure rendering
# (regressions for the wiki-side parts of the footnote-linking and
# image-description fixes)
# ---------------------------------------------------------------------------

class TestMarkdownFootnoteRefsInBody:
    """When a body block contains an inline plain-digit footnote ref AND
    a footnote with that number exists on the same page, the wiki
    emitter rewrites the digit as ``[^pXXX-N]`` markdown footnote
    syntax. Obsidian / Pandoc render this as a clickable superscript."""

    def test_inline_ref_becomes_markdown_footnote_marker(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Wie jede der r\u00f6mischen Legionen bezogen 2 . Sie waren ..."
        out = _insert_footnote_refs(text, page_num=18, page_numbered={1, 2, 3})
        assert "[^p018-2]" in out
        assert "bezogen" in out and "Sie waren" in out
        assert "bezogen 2 ." not in out

    def test_unknown_number_passes_through(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Bei Punkt 7 wird es klar ."
        out = _insert_footnote_refs(text, page_num=18, page_numbered={1, 2})
        assert "[^" not in out
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
        assert "[Image \u00b7 p18_b004]" in out
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
        assert "[Image \u00b7 p1_b1]" in out
'''

append_once(
    "tests/test_wiki.py",
    marker="class TestMarkdownFootnoteRefsInBody",
    addition=_TEST_WIKI_NEW,
    what="regression: markdown footnote refs + Chandra alt in figures",
)

# ---------------------------------------------------------------------------
# Done \u2014 run the test suite
# ---------------------------------------------------------------------------
print("\nAll patches applied. Running test suite...\n")
t0 = time.time()
proc = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "--no-header"],
    cwd=REPO,
    capture_output=True,
    text=True,
)
elapsed = time.time() - t0
print(proc.stdout.strip())
if proc.returncode != 0:
    print(proc.stderr.strip())
    sys.exit(f"\nTests failed after {elapsed:.1f}s. Backups are at *.bak3.")
print(f"\nTests passed in {elapsed:.1f}s. Backups of modified files are at *.bak3.")
