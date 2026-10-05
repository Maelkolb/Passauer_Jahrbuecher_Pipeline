"""Page layout analysis: typography, block roles and reading order.

Chandra gives us typed blocks with bounding boxes and a reading order of its
own. Both are mostly right, but the errors that remain are the ones that
make the wiki hard to read:

* **Footnotes labelled as body text.** In some volumes a third of the long
  "Text" blocks are really footnotes. They then sit in the middle of the
  article body instead of in its footnote section.
* **Columns read in the wrong order.** Two-column pages with a figure that
  straddles the gutter, letters/documents with many small blocks, pages
  where one column is a single block — the old centre-gap heuristic either
  missed the columns or found the wrong gutter.

This module fixes both with three independent signals.

**Typography.** The print size of a block is visible in its character
density (characters per pixel² of bounding box). Body text and footnote
text form two well-separated clusters in every volume (≈1.6 vs ≈3.5 at
the 200 dpi render), so a per-volume calibration (:func:`calibrate`) tells
footnote-size text from body text without looking at Chandra's label.
:func:`reclassify_blocks` then turns small-print blocks at the foot of a
column that carry footnote evidence (a leading note number, an adjacent
footnote block, citation vocabulary) into footnotes, and "Abb. 3: …" text
next to a figure into a caption.

**Geometry.** :func:`xy_cut` is a recursive XY-cut over the body blocks:
columns are found from whitespace gutters in the x-projection (robust to
one-block columns, narrow gutters and figures that cross the gutter, which
simply force a horizontal cut first).

**Text continuity.** German typesetting leaves strong traces of the true
order: a block that ends in "verlie-" continues with a lower-case "henen";
a block that ends mid-sentence continues with a lower-case word; a
lower-case block never follows a finished sentence. :func:`order_page`
builds several candidate orders (XY-cut with columns first, XY-cut with rows
first, the previous band heuristic, Chandra's own order), scores each by
these continuity cues plus a geometric plausibility term and agreement
with Chandra, and keeps the best — then tries a small local repair for any
continuation that is still stranded. The previous page's last body block
takes part in the scoring, so the cross-page join counts too.

Footnotes are ordered after the body, page headers first and page footers
last, which is what every emitter (wiki, TEI, PageXML) expects.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .columns import assign_columns, detect_columns, reading_order as band_order


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------

REF_WIDTH = 1500.0          # densities are normalised to this page width
_TEXTUAL = {"text", "list", "footnote"}
_VISUAL = {"figure", "image", "diagram", "table"}


def block_density(block: dict, page_width: float) -> Optional[float]:
    """Characters per 1000 px² of bounding box, at :data:`REF_WIDTH`.

    Roughly proportional to 1 / font-size². ``None`` for empty blocks.
    """
    text = block.get("text") or ""
    n = len(text.strip())
    if n == 0:
        return None
    x0, y0, x1, y1 = block["bbox"][:4]
    scale = REF_WIDTH / page_width if page_width else 1.0
    area = max(1.0, (x1 - x0) * scale) * max(1.0, (y1 - y0) * scale)
    return 1000.0 * n / area


@dataclass
class Typography:
    body: float          # typical density of body text
    small: float         # typical density of footnote-size text
    threshold: float     # boundary between the two

    def is_small(self, density: Optional[float]) -> bool:
        return density is not None and density > self.threshold

    def is_body(self, density: Optional[float]) -> bool:
        return density is not None and density <= self.threshold

    def as_dict(self) -> dict:
        return {"body": round(self.body, 3), "small": round(self.small, 3),
                "threshold": round(self.threshold, 3)}


DEFAULT_TYPOGRAPHY = Typography(body=1.65, small=3.5, threshold=2.4)


def calibrate(pages: Iterable[dict], *, min_chars: int = 120) -> Typography:
    """Fit body vs footnote print size for a volume (two-means on log density)."""
    vals: List[float] = []
    for p in pages:
        W = p.get("image_width") or REF_WIDTH
        for b in p.get("blocks", []):
            if b.get("type") not in _TEXTUAL or len(b.get("text") or "") < min_chars:
                continue
            d = block_density(b, W)
            if d:
                vals.append(math.log(d))
    if len(vals) < 20:
        return DEFAULT_TYPOGRAPHY
    vals.sort()
    lo = vals[int(0.2 * (len(vals) - 1))]
    hi = vals[int(0.8 * (len(vals) - 1))]
    if hi - lo < 0.05:
        hi = lo + 0.7
    for _ in range(30):
        mid = (lo + hi) / 2
        a = [v for v in vals if v <= mid]
        b = [v for v in vals if v > mid]
        if not a or not b:
            break
        nlo, nhi = statistics.median(a), statistics.median(b)
        if abs(nlo - lo) < 1e-4 and abs(nhi - hi) < 1e-4:
            break
        lo, hi = nlo, nhi
    body, small = math.exp(lo), math.exp(hi)
    if small / body < 1.35:
        # One print size only (no footnotes in this sample): put the
        # boundary where footnote print would start.
        body = math.exp(statistics.median(vals))
        return Typography(body=body, small=body * 2.1, threshold=body * 1.45)
    return Typography(body=body, small=small, threshold=math.sqrt(body * small))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _x_overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a["bbox"][2], b["bbox"][2]) - max(a["bbox"][0], b["bbox"][0]))


def _y_overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a["bbox"][3], b["bbox"][3]) - max(a["bbox"][1], b["bbox"][1]))


def _width(b: dict) -> float:
    return max(1.0, b["bbox"][2] - b["bbox"][0])


def _height(b: dict) -> float:
    return max(1.0, b["bbox"][3] - b["bbox"][1])


def _same_column(a: dict, b: dict) -> bool:
    return _x_overlap(a, b) > 0.3 * min(_width(a), _width(b))


# ---------------------------------------------------------------------------
# Block roles
# ---------------------------------------------------------------------------

_FN_NUMBER = re.compile(r"^\s*(?:(\d{1,3})\s*[.)]?\s+\S|[*†]\s*\S|<sup>\s*\d{1,3}\s*</sup>)")
_FN_VOCAB = re.compile(
    r"\b(?:[Vv]gl\.|[Ee]bd\.|[Ee]benda|a\.\s?a\.\s?O\.|wie Anm\.|Anm\.\s*\d|S\.\s*\d|"
    r"Sp\.\s*\d|Bd\.\s*\d|Nr\.\s*\d|[Hh]g\.|Hrsg\.|ders\.|dies\.|fol\.|Fasz\.|"
    r"Urk\.|Rep\.\s*\d|hier\s+\d|\d+\s*f{1,2}\.)"
)
# Citation vocabulary of notes that bibliographies and registers do not use.
_FN_STRONG = re.compile(
    r"wie Anm\.|Anm\.\s*\d|\b[Ee]bd\.|\b[Ee]benda\b|\b[Vv]gl\.|a\.\s?a\.\s?O\.|\bhier[:,]?\s+\d|"
    r"\bders\.|\bdies\.|\bzit\.\s+(?:nach|n\.)|\bSiehe Anm\.")
_DESCRIPTION_TEXT = re.compile(
    r"^\s*(?:Faint,? illegible text|This image (?:shows|is|displays)|The image (?:shows|displays|is)"
    r"|A blank (?:white )?page)\b")
_PAGE_COUNT = re.compile(r"\b\d{1,4}\s*S\.(?=[\s,;)]|$)")
_CAPTION_START = re.compile(
    r"^\s*(?:Abb\.|Abbildung|Fig\.|Tab\.|Tabelle|Karte|Taf\.|Tafel|Plan|Grafik|Diagramm)"
    r"\s*\d*[a-z]?\s*[:.)]", re.I)


def _fn_number(b: dict) -> Optional[int]:
    """Leading footnote number of a block ("117 Dazu vgl. …"), else None.
    ``0`` stands for an asterisk/dagger note."""
    t = b.get("text") or ""
    m = _FN_NUMBER.match(t)
    if m:
        if m.group(1):
            n = int(m.group(1))
            return n if 0 < n <= 400 else None
        return 0
    h = (b.get("html") or "").lstrip()
    m = re.match(r"^(?:<p>)?\s*<sup>\s*(\d{1,3})\s*</sup>", h)
    return int(m.group(1)) if m else None


def reclassify_blocks(
    page: dict,
    typo: Typography,
    *,
    prev_footnote: Optional[dict] = None,
) -> List[Tuple[str, str, str]]:
    """Fix Chandra block types in place. Returns ``(block_id, old, new)``.

    * small-print ``text`` at the foot of a column with footnote evidence →
      ``footnote``
    * "Abb. 3: …" ``text`` next to a figure → ``caption``

    ``prev_footnote`` is the previous page's last footnote block, if any —
    it lets a page that carries nothing but the continuation of a long
    footnote be recognised (such pages have no body text to compare with).

    The original label stays in ``raw_type``; ``role_note`` records why the
    type changed.
    """
    changes: List[Tuple[str, str, str]] = []
    blocks = page.get("blocks", [])
    W = page.get("image_width") or REF_WIDTH
    H = page.get("image_height") or REF_WIDTH * 1.47
    dens = {b["id"]: block_density(b, W) for b in blocks}
    # Density is a reliable print-size measure only for blocks of three or
    # more lines; a short block with a loose box reads too "large".
    def short(b: dict) -> bool:
        return _height(b) < 0.035 * H

    # --- OCR artefacts ----------------------------------------------------
    # On blank or bleed-through pages Chandra occasionally emits its image
    # description as a text block ("Faint, illegible text covering …").
    for b in blocks:
        if b.get("type") == "text" and _DESCRIPTION_TEXT.match(b.get("text") or ""):
            changes.append((b["id"], b["type"], "image"))
            b["type"] = "image"
            b["description"] = (b.get("text") or "").strip()
            b["text"] = ""
            b["role_note"] = "model description, not page text"

    # --- captions ---------------------------------------------------------
    visuals = [b for b in blocks if b.get("type") in _VISUAL]
    for b in blocks:
        if b.get("type") != "text" or not _CAPTION_START.match(b.get("text") or ""):
            continue
        if len(b.get("text") or "") > 700:
            continue
        near = any(
            (_x_overlap(b, v) > 0 and min(abs(b["bbox"][1] - v["bbox"][3]),
                                         abs(v["bbox"][1] - b["bbox"][3])) < 80)
            or (_y_overlap(b, v) > 0.5 * _height(b))
            for v in visuals
        )
        if near or typo.is_small(dens[b["id"]]):
            changes.append((b["id"], b["type"], "caption"))
            b["type"] = "caption"
            b["role_note"] = "caption-pattern"

    # --- footnotes --------------------------------------------------------
    def body_print(b: dict) -> bool:
        return (b.get("type") in ("text", "list") and len(b.get("text") or "") >= 60
                and typo.is_body(dens[b["id"]]) and not short(b))

    def small_print(b: dict) -> bool:
        d = dens[b["id"]]
        if d is None or len((b.get("text") or "").strip()) < 15:
            return False
        if d > typo.threshold * 1.05:
            return True
        # Short (1–2 line) and numbered blocks: their boxes are often loose,
        # so accept a lower density.
        numbered = _fn_number(b) is not None
        if short(b):
            return d > typo.body * (1.1 if numbered else 1.25)
        return numbered and d > typo.body * 1.2

    body_size = [b for b in blocks if body_print(b)]
    fn_blocks = [b for b in blocks if b.get("type") == "footnote"]
    small = [b for b in blocks if b.get("type") == "text" and small_print(b)]
    if not small:
        return changes

    def mark(b: dict, why: str) -> None:
        changes.append((b["id"], b["type"], "footnote"))
        b["type"] = "footnote"
        b["role_note"] = why

    if not body_size:
        # An all-small page is normally a bibliography or register page —
        # leave it alone. Two exceptions:
        # (a) a page of notes (endnotes, or footnotes running over a whole
        #     page): consecutive note numbers and note vocabulary ("wie
        #     Anm.", "Ebd.", "Vgl.") that bibliographies do not use;
        nums = [n for n in (_fn_number(b) for b in small) if n]
        consecutive = sum(1 for a, b in zip(sorted(nums), sorted(nums)[1:]) if b - a == 1)
        strong = sum(1 for b in small if _FN_STRONG.search(b.get("text") or ""))
        # bibliography entries end in a page count ("… 2010. 160 S."),
        # notes cite pages the other way round ("S. 160")
        page_counts = sum(1 for b in small if _PAGE_COUNT.search(b.get("text") or ""))
        if (len(nums) >= 3 and consecutive >= 0.6 * (len(nums) - 1)
                and page_counts < 0.25 * len(small)
                and (strong >= 0.4 * len(small)
                     or (strong >= 1 and prev_footnote is not None))):
            for b in blocks:
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    mark(b, "page of notes")
            return changes
        # (b) a page that only continues the previous page's footnotes: few
        #     blocks, note vocabulary, and either the continuation of an
        #     unfinished note at the top or the next note number.
        if prev_footnote is None or fn_blocks or len(small) > 6:
            return changes
        prev_n = _fn_number(prev_footnote)
        first = min(small, key=lambda b: (b["bbox"][0] > W / 2, b["bbox"][1]))
        n_first = _fn_number(first)
        continues = (n_first is None and not re.search(r"[.!?][\"'“”»«’)\]]*$",
                                                      _clean_tail(prev_footnote.get("text") or ""))) \
            or (n_first is not None and prev_n is not None and n_first == prev_n + 1)
        vocab = sum(1 for b in small if _FN_STRONG.search(b.get("text") or ""))
        if continues and vocab >= max(1, len(small) // 2):
            for b in small:
                mark(b, "footnote continuation page")
        return changes

    decided: Dict[str, bool] = {}
    # Walk bottom-up so a footnote zone grows upwards from its numbered notes.
    for b in sorted(small, key=lambda x: -x["bbox"][1]):
        text = (b.get("text") or "").strip()
        below_body = [c for c in body_size
                      if c["bbox"][1] >= b["bbox"][3] - 10 and _same_column(b, c)]
        if below_body:
            continue                       # in-flow small print (a block quote)
        num = _fn_number(b)
        score = 0.0
        if num is not None:
            score += 3
        adj = [f for f in fn_blocks + [s for s in small if decided.get(s["id"])]
               if f is not b and _same_column(b, f)
               and -15 <= f["bbox"][1] - b["bbox"][3] <= 60]
        if adj:
            score += 2
        if any(f is not b and _same_column(b, f) and abs(f["bbox"][3] - b["bbox"][1]) <= 40
               for f in fn_blocks):
            score += 1                     # a footnote right above it
        score += min(2, len(_FN_VOCAB.findall(text)))
        above = [c for c in body_size
                 if c["bbox"][3] <= b["bbox"][1] + 10 and _same_column(b, c)]
        if above:
            prev = max(above, key=lambda c: c["bbox"][3])
            if (prev.get("text") or "").rstrip().endswith(":") and num is None:
                score -= 3                 # "… heißt es:" + quotation
            col_x0 = min(c["bbox"][0] for c in above)
            col_x1 = max(c["bbox"][2] for c in above)
            if (b["bbox"][0] - col_x0 > 0.02 * W and col_x1 - b["bbox"][2] > 0.02 * W
                    and num is None):
                score -= 2                 # indented on both sides: a quotation
        if score >= 2:
            decided[b["id"]] = True
            mark(b, f"small-print footnote zone (score {score:g})")
    return changes


# ---------------------------------------------------------------------------
# XY-cut
# ---------------------------------------------------------------------------

def _gaps(intervals: List[Tuple[float, float]], min_gap: float) -> List[float]:
    """Cut positions between the merged ``intervals`` (gaps ≥ ``min_gap``)."""
    if not intervals:
        return []
    iv = sorted(intervals)
    cuts: List[float] = []
    cur_end = iv[0][1]
    for s, e in iv[1:]:
        if s - cur_end >= min_gap:
            cuts.append((s + cur_end) / 2)
        cur_end = max(cur_end, e)
    return cuts


def _shrunk(b: dict) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = b["bbox"][:4]
    dx = min(6.0, (x1 - x0) / 8)
    dy = min(8.0, (y1 - y0) / 4)
    return x0 + dx, y0 + dy, x1 - dx, y1 - dy


def _vcuts(blocks: List[dict], min_gutter: float) -> List[float]:
    return _gaps([(_shrunk(b)[0], _shrunk(b)[2]) for b in blocks], min_gutter)


def _hcuts(blocks: List[dict]) -> List[float]:
    return _gaps([(_shrunk(b)[1], _shrunk(b)[3]) for b in blocks], 0.5)


def _split(blocks: List[dict], cuts: List[float], axis: int) -> List[List[dict]]:
    groups: List[List[dict]] = [[] for _ in range(len(cuts) + 1)]
    for b in blocks:
        c = (b["bbox"][axis] + b["bbox"][axis + 2]) / 2
        k = sum(1 for cut in cuts if c > cut)
        groups[k].append(b)
    return [g for g in groups if g]


def _spanner_cuts(blocks: List[dict], min_gutter: float) -> List[float]:
    """Horizontal cuts around the blocks that cross a column gutter.

    When no vertical cut exists because something spans the columns (a
    title, a wide figure), cutting at *every* horizontal gap would also cut
    between paragraphs that happen to line up in both columns and read the
    page row by row. Instead the gutter is found from the narrow blocks
    only, and the region is cut just above and just below each block that
    crosses it.
    """
    x0 = min(b["bbox"][0] for b in blocks)
    x1 = max(b["bbox"][2] for b in blocks)
    narrow = [b for b in blocks if _width(b) < 0.6 * (x1 - x0)]
    if len(narrow) < 2:
        return []
    gutters = _vcuts(narrow, min_gutter)
    if not gutters:
        return []
    spanners = [b for b in blocks if any(b["bbox"][0] < g < b["bbox"][2] for g in gutters)]
    if not spanners:
        return []
    gaps = _hcuts(blocks)
    cuts = set()
    for sp in spanners:
        above = [g for g in gaps if g <= sp["bbox"][1] + 8]
        below = [g for g in gaps if g >= sp["bbox"][3] - 8]
        if above:
            cuts.add(max(above))
        if below:
            cuts.add(min(below))
    return sorted(cuts)


def xy_cut(blocks: List[dict], *, prefer: str = "columns",
           min_gutter: float = 4.0, _depth: int = 0) -> List[dict]:
    """Recursive XY-cut reading order.

    ``prefer="columns"`` splits on vertical gutters first (column-major
    reading) and, where something spans the columns, cuts horizontally only
    around that spanning block; ``prefer="rows"`` splits on every horizontal
    whitespace first (row-major reading — right for tables of small items,
    wrong for running columns; it is one of the candidates
    :func:`order_page` scores).
    """
    if len(blocks) <= 1 or _depth > 40:
        return list(blocks)

    def recurse(parts):
        return [x for part in parts
                for x in xy_cut(part, prefer=prefer, min_gutter=min_gutter,
                                _depth=_depth + 1)]

    if prefer == "columns":
        cuts = _vcuts(blocks, min_gutter)
        if cuts:
            parts = _split(blocks, cuts, 0)
            if len(parts) > 1:
                return recurse(parts)
        cuts = _spanner_cuts(blocks, min_gutter) or _hcuts(blocks)
        if cuts:
            parts = _split(blocks, cuts, 1)
            if len(parts) > 1:
                return recurse(parts)
    else:
        for kind in ("h", "v"):
            cuts = _hcuts(blocks) if kind == "h" else _vcuts(blocks, min_gutter)
            if cuts:
                parts = _split(blocks, cuts, 1 if kind == "h" else 0)
                if len(parts) > 1:
                    return recurse(parts)
    # No clean cut: overlapping blocks. Read top-to-bottom, left-to-right.
    return sorted(blocks, key=lambda b: (round(b["bbox"][1] / 10), b["bbox"][0]))


# ---------------------------------------------------------------------------
# Continuity scoring
# ---------------------------------------------------------------------------

_PROSE = {"text", "list"}
_ABBREV_END = re.compile(
    r"(?:\b[A-Za-zÄÖÜäöü]|\bbzw|\bvgl|\bca|\bNr|\bSt|\bDr|\bHl|\bhl|\busw|\bz\.\s?B|\bu\.\s?a"
    r"|\bd\.\s?h|\betc|\bS|\bAbb|\bAnm|\bProf|\bgeb|\bgest|\bsog|\bJh|\bJhs|\bBd|\bHg"
    r"|\bHrsg|\bebd|\bf|\bff|\bs\.\s?o|\bs\.\s?u|\b[IVXLC]{1,6}|\b\d{1,2})\.$")
_TERMINAL = re.compile(r"[.!?:][\"'“”»«’)\]]*$")
_HYPHEN_END = re.compile(r"[a-zäöüß]-$")


def _clean_tail(t: str) -> str:
    t = (t or "").rstrip()
    # drop a trailing footnote marker "… Ende 12" / "… Ende¹²"
    t = re.sub(r"(?<=\S)\s*[¹²³⁰-⁹]+$", "", t)
    t = re.sub(r"(?<=[^\d\s])\s+\d{1,3}$", "", t)
    return t.rstrip()


def _first_letter(t: str) -> str:
    for c in (t or "").lstrip()[:12]:
        if c.isalpha():
            return c
        if c.isdigit():
            return ""
    return ""


def continuity(a_text: str, b_text: str) -> float:
    """How well does ``b`` continue ``a``? Positive = plausible join."""
    a = _clean_tail(a_text)
    if not a:
        return 0.0
    f = _first_letter(b_text)
    if not f:
        return 0.0
    lower = f.islower()
    if _HYPHEN_END.search(a):
        return 4.0 if lower else -1.0
    if _TERMINAL.search(a) and not _ABBREV_END.search(a):
        return -3.0 if lower else 0.3
    if a[-1] in ",;" or a[-1].isalnum() or a[-1] in "“”\"'»«)":
        return 2.5 if lower else 0.0
    return 0.0


def _score(order: List[dict], prev_tail: Optional[dict],
           chandra_rank: Dict[str, int]) -> float:
    s = 0.0
    prose = [b for b in order if b.get("type") in _PROSE and (b.get("text") or "").strip()]
    seq = ([prev_tail] if prev_tail is not None else []) + prose
    for a, b in zip(seq, seq[1:]):
        s += continuity(a.get("text") or "", b.get("text") or "")
    for a, b in zip(order, order[1:]):
        # reading upwards inside the same column is implausible
        if b["bbox"][3] <= a["bbox"][1] + 5 and _same_column(a, b):
            s -= 1.5
        # jumping back to a column further left that sits above us
        elif b["bbox"][2] <= a["bbox"][0] and b["bbox"][3] <= a["bbox"][1] + 5:
            s -= 1.0
        ra, rb = chandra_rank.get(a["id"]), chandra_rank.get(b["id"])
        if ra is not None and rb is not None and rb == ra + 1:
            s += 0.3
    return s


def _repair(order: List[dict], chandra_rank: Dict[str, int]) -> List[dict]:
    """Re-attach a stranded hyphenation continuation.

    Only the strongest cue is trusted here: a body block ending in a
    hyphenated fragment ("verlie-") that is currently followed by a block
    starting upper-case, while another block on the same page starts
    lower-case and currently follows a finished sentence. The lower-case
    block is moved behind the hyphenated one if that does not make the
    reading jump upwards inside a column. Everything else is left to the
    candidate choice in :func:`order_page`.
    """
    for _ in range(3):
        prose = [b for b in order if b.get("type") in _PROSE and (b.get("text") or "").strip()]
        nxt = {prose[i]["id"]: prose[i + 1] for i in range(len(prose) - 1)}
        prv = {prose[i + 1]["id"]: prose[i] for i in range(len(prose) - 1)}
        moved = False
        for a in prose:
            if not _HYPHEN_END.search(_clean_tail(a.get("text") or "")):
                continue
            succ = nxt.get(a["id"])
            if succ is not None and _first_letter(succ.get("text") or "").islower():
                continue                      # already continued correctly
            for x in prose:
                if x is a or x is succ or not _first_letter(x.get("text") or "").islower():
                    continue
                p = prv.get(x["id"])
                if p is None or continuity(p.get("text") or "", x.get("text") or "") >= 0:
                    continue                  # x is not stranded
                if x["bbox"][3] <= a["bbox"][1] + 5 and _same_column(a, x):
                    continue                  # would read upwards in a column
                trial = [b for b in order if b is not x]
                trial.insert(trial.index(a) + 1, x)
                order = trial
                moved = True
                break
            if moved:
                break
        if not moved:
            break
    return order


# ---------------------------------------------------------------------------
# Page ordering
# ---------------------------------------------------------------------------

def order_page(page: dict, *, prev_tail: Optional[dict] = None,
               chandra_order: Optional[Sequence[str]] = None) -> dict:
    """Put ``page["blocks"]`` into reading order (in place) and return a
    small report ``{"strategy": …, "score": …, "repaired": bool}``.

    ``prev_tail`` is the last body block of the previous page (for the
    cross-page join); ``chandra_order`` the block ids in Chandra's emission
    order (defaults to the current order of ``page["blocks"]``).
    """
    blocks = list(page.get("blocks", []))
    if not blocks:
        return {"strategy": "empty", "score": 0.0, "repaired": False}
    chandra_order = list(chandra_order or [b["id"] for b in blocks])
    chandra_rank = {bid: i for i, bid in enumerate(chandra_order)}

    headers = [b for b in blocks if b.get("type") == "page-header"]
    footers = [b for b in blocks if b.get("type") == "page-footer"]
    notes = [b for b in blocks if b.get("type") == "footnote"]
    body = [b for b in blocks if b.get("type") not in ("page-header", "page-footer", "footnote")]

    by_rank = lambda bs: sorted(bs, key=lambda b: chandra_rank.get(b["id"], 1 << 30))

    candidates: List[Tuple[str, List[dict]]] = []
    if body:
        candidates.append(("xy-columns", xy_cut(body, prefer="columns")))
        legacy_page = {**page, "blocks": by_rank(body)}
        cols = detect_columns(legacy_page)
        legacy = band_order(assign_columns(legacy_page, cols), cols)
        by_id = {b["id"]: b for b in body}
        candidates.append(("bands", [by_id[b["id"]] for b in legacy]))
        candidates.append(("chandra", by_rank(body)))
        candidates.append(("xy-rows", xy_cut(body, prefer="rows")))

    report = {"strategy": "none", "score": 0.0, "repaired": False}
    best_body: List[dict] = []
    if candidates:
        scored = []
        for k, (name, order) in enumerate(candidates):
            scored.append((_score(order, prev_tail, chandra_rank), -k, name, order))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        best_score, _, name, best_body = scored[0]
        repaired = _repair(best_body, chandra_rank)
        report = {"strategy": name, "score": round(best_score, 2),
                  "repaired": [b["id"] for b in repaired] != [b["id"] for b in best_body]}
        best_body = repaired

    note_order = xy_cut(notes, prefer="columns") if notes else []
    page["blocks"] = (
        sorted(headers, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        + best_body
        + note_order
        + sorted(footers, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    )
    return report


def last_body_block(page: dict) -> Optional[dict]:
    """The last prose block of a page in reading order (for the next page's
    cross-page continuity)."""
    prose = [b for b in page.get("blocks", [])
             if b.get("type") in _PROSE and (b.get("text") or "").strip()]
    return prose[-1] if prose else None
