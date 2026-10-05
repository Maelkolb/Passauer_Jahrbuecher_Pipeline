"""Article boundary detection.

Two strategies, in priority order:

1. **TOC-driven** (preferred). If the volume has a parseable
   ``table-of-contents`` block, we trust it: each TOC entry becomes one
   article with the title, author, section, and printed start page taken
   from the TOC. The pipeline maps printed page → PDF page via a
   ``printed_page_offset`` that's either configured explicitly or inferred
   from page-header text on the volume's first numbered pages.

2. **Heuristic fallback** (existing notebook logic). If no usable TOC is
   found, fall back to: a section-header block in the upper third of a
   page anchors an article; pages between anchors form one article;
   anything before the first anchor is "Frontmatter".

The output is always the same list-of-dicts shape so downstream emitters
don't care which strategy fired.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from ..config import VolumeConfig
from .toc import (
    TocStructure, TocEntry,
    collect_toc_tokens, find_contributors, parse_toc_structure, find_toc_blocks,
)


# ---------------------------------------------------------------------------
# Printed-page → PDF-page offset
# ---------------------------------------------------------------------------

def _scan_running_numbers(unified_pages: list) -> List[Tuple[int, int]]:
    """Find PDF pages that have a plausible page number in the header or
    footer of the actual page. Returns ``[(pdf_page, printed_num), …]``.

    "Plausible" means: a ``page-header`` or ``page-footer`` block whose
    text is mostly a small integer, OR a number that appears at the very
    top/bottom of any text block.
    """
    pairs: List[Tuple[int, int]] = []
    num_only = re.compile(r"^\s*(\d{1,4})\s*$")
    for p in unified_pages:
        H = p["image_height"]
        for b in p["blocks"]:
            if b["type"] in ("page-header", "page-footer"):
                m = num_only.match(b["text"].strip())
                if m:
                    pairs.append((p["page_num"], int(m.group(1))))
                    continue
            # Numbers floating at the very top or bottom of a text region
            y_top = b["bbox"][1]
            y_bot = b["bbox"][3]
            for line in b["text"].splitlines():
                line = line.strip()
                if line.isdigit() and 1 <= int(line) <= 9999:
                    if y_top < H * 0.08 or y_bot > H * 0.92:
                        pairs.append((p["page_num"], int(line)))
                        break
    return pairs


def infer_printed_page_offset(unified_pages: list) -> Optional[int]:
    """Infer the constant offset ``printed = pdf_page - offset`` from the
    page-number stamps found on individual pages. Returns ``None`` if no
    robust offset emerges (e.g. TOC will then drive things differently)."""
    pairs = _scan_running_numbers(unified_pages)
    if len(pairs) < 3:
        return None

    # The offset is (pdf_page - printed_num). It should be constant on most
    # pages; pick the mode.
    from collections import Counter
    offsets = [pdf - printed for pdf, printed in pairs]
    most_common = Counter(offsets).most_common(1)[0]
    offset, support = most_common
    # Demand at least 3 supporting pages or 25% of seen numbers, whichever
    # is bigger.
    if support < max(3, len(offsets) // 4):
        return None
    return offset


def printed_page_mapper(unified_pages: list, offset: int):
    """Return ``f(printed) -> pdf_page`` that follows *local* offsets.

    Volumes with inserted plates (Tafeln) have different printed→PDF
    offsets before and after the plates (vol. 46: +1 up to p. 25, +6
    after), so one global offset sends the early articles to the wrong
    page. The page-number stamps give the local offset; a stamp only
    counts when a neighbouring page (±4) agrees with it, which drops
    stray numbers read from the text. Printed pages without a stamp use
    the offset of the nearest stamped page before them (after them for
    the very first pages); ``offset`` is the fallback when no stamp is
    usable at all.
    """
    # Prefer stamps from page-header/-footer blocks; a number at the foot
    # of a text block can be a footnote number.
    stamps = []
    num_only = re.compile(r"^\s*(\d{1,4})\s*$")
    for p in unified_pages:
        for b in p["blocks"]:
            if b["type"] in ("page-header", "page-footer"):
                m = num_only.match(b.get("text") or "")
                if m:
                    stamps.append((p["page_num"], int(m.group(1))))
    pairs = sorted(set(stamps if len(stamps) >= 20 else _scan_running_numbers(unified_pages)))
    good = []
    for i, (pdf, pr) in enumerate(pairs):
        off = pdf - pr
        if abs(off - offset) > 30:
            continue
        agree = sum(1 for q_pdf, q_pr in pairs[max(0, i - 6): i + 7]
                    if q_pdf != pdf and abs(q_pdf - pdf) <= 4 and q_pdf - q_pr == off)
        if agree >= 2:
            good.append((pr, pdf))
    good.sort()

    def to_pdf(printed: int) -> int:
        if not good:
            return printed + offset
        below = [g for g in good if g[0] <= printed]
        ref = below[-1] if below else good[0]
        return printed + (ref[1] - ref[0])
    return to_pdf


# ---------------------------------------------------------------------------
# TOC-driven article detection
# ---------------------------------------------------------------------------

def _find_anchor_on_pdf_page(
    page: dict,
    expected_title: str,
    *,
    top_third_only: bool = True,
) -> Optional[dict]:
    """Look for a section-header block on ``page`` whose text matches the
    first few words of ``expected_title``.

    Comparison is lowered, diacritic-stripped, and word-prefix-based — the
    OCR on the TOC line and the OCR on the article's own title page are
    rarely byte-identical (line breaks differ, hyphenation differs).
    """
    if not expected_title:
        return None

    def norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9äöüß ]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    target = norm(expected_title)
    target_prefix = " ".join(target.split()[:4])  # first 4 words

    H = page["image_height"]
    best = None
    best_score = 0
    for b in page["blocks"]:
        if b["type"] != "section-header":
            continue
        if top_third_only and b["bbox"][1] > H / 2:
            continue
        cand = norm(b["text"])
        if not cand:
            continue
        # crude prefix-match score
        if cand.startswith(target_prefix) or target_prefix.startswith(cand[:len(target_prefix)]):
            score = len(set(cand.split()) & set(target.split()))
            if score > best_score:
                best, best_score = b, score
    return best


# ---------------------------------------------------------------------------
# Block-level article starts
# ---------------------------------------------------------------------------
#
# Articles used to be cut at page granularity: every article got *all*
# blocks of every page from its start page to the page before the next
# article. Wherever two articles share a page — the end of one and the
# start of the next, or several book reviews on one page — that put the
# previous article's ending at the top of the next one and cut off the
# previous article's tail. Here each article start is located at block
# level (its title / byline / review citation on the start page) and the
# shared pages are split there.

_NON_BODY = ("page-header", "page-footer", "footnote")
_WORD = re.compile(r"[0-9a-zäöüßàáâèéêìíîòóôùúûçčšžřěýœæ]+")


def _norm_tokens(s: str) -> List[str]:
    return [t for t in _WORD.findall((s or "").lower()) if len(t) >= 2 or t.isdigit()]


def _target_tokens(entry: TocEntry) -> List[str]:
    """Distinctive words the article's first block should contain."""
    if getattr(entry, "is_review", False):
        title = entry.reviewed_title or entry.title
        toks = _norm_tokens(title)[:7]
        for n in list(entry.reviewed_authors) + list(entry.reviewed_editors):
            toks += _norm_tokens(n)[-1:]
        return toks
    return _norm_tokens(entry.title)[:8]


def _token_hit(t: str, bt: set) -> bool:
    if t in bt:
        return True
    if len(t) < 6:
        return False
    # OCR slips and inflection ("nachwuchsförderpreisträger" /
    # "nachwuchsförderpreis"): a long common prefix counts
    for b in bt:
        if len(b) >= 6 and b[:6] == t[:6]:
            n = 0
            for x, y in zip(t, b):
                if x != y:
                    break
                n += 1
            if n >= max(6, 0.75 * min(len(t), len(b))):
                return True
    return False


def _match_score(text: str, target: List[str]) -> float:
    if not target:
        return 0.0
    bt = set(_norm_tokens(text)[:80])
    return sum(1 for t in target if _token_hit(t, bt)) / len(target)


def _is_section_label(blk: dict, labels: Tuple[str, ...]) -> bool:
    t = re.sub(r"\s+", " ", blk.get("text") or "").strip()
    if not t or len(t) > 40:
        return False
    canon = lambda x: re.sub(r"[^A-ZÄÖÜ]", "", x.upper())
    if canon(t) in {canon(l) for l in labels}:
        return True
    letters = [c for c in t if c.isalpha()]
    return blk.get("type") == "section-header" and len(letters) >= 5 and all(
        c.isupper() for c in letters) and len(t.split()) <= 2


def _byline_names(blk: dict, entry: TocEntry, given) -> Optional[List[str]]:
    """Names in ``blk`` if it is the article's byline ("HEINZ KELLERMANN")."""
    from .names import parse_byline, surname_key
    text = (blk.get("text") or "").strip()
    if not text or len(text) > 160 or blk.get("type") not in ("text", "section-header"):
        return None
    names = parse_byline(text, given)
    if not names:
        return None
    toc_surnames = {surname_key(a) for a in (entry.authors or [])}
    if toc_surnames and not any(_similar_key(t, surname_key(n))
                                for t in toc_surnames for n in names):
        return None
    return names


def _similar_key(a: str, b: str) -> bool:
    """Surname keys equal up to one OCR slip ("boshof" / "boshoff")."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 1:
        return False
    # one substitution, insertion or deletion
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) == len(b)):] == b[i + 1:]


def _find_start(page: dict, entry: TocEntry, labels: Tuple[str, ...], given,
                min_score: float = 0.6) -> Optional[dict]:
    """Locate the first block of ``entry``'s article on ``page``.

    Returns ``{"index", "title_index", "score", "byline"}`` (indices into
    ``page["blocks"]``) or ``None``.
    """
    target = _target_tokens(entry)
    if not target:
        return None
    blocks = page["blocks"]
    best = None
    usable = [i for i, b in enumerate(blocks)
              if b["type"] not in _NON_BODY
              and b["type"] not in ("figure", "image", "diagram", "table")
              and (b.get("text") or "").strip() and len(b.get("text") or "") <= 1500]
    for k, i in enumerate(usable):
        b = blocks[i]
        text = b.get("text") or ""
        sc = _match_score(text, target)
        end = i
        # A title set as headline + subtitle comes as two heading blocks.
        if k + 1 < len(usable) and len(text) < 300:
            nb = blocks[usable[k + 1]]
            if len(nb.get("text") or "") < 300:
                sc2 = _match_score(text + " " + (nb.get("text") or ""), target) - 0.02
                if sc2 > sc:
                    sc, end = sc2, usable[k + 1]
        # a heading is a better anchor than a paragraph that merely
        # mentions the same words; short blocks beat long ones
        bonus = 0.1 if b["type"] == "section-header" else 0.0
        bonus -= min(0.15, len(text) / 8000)
        if sc >= min_score and (best is None or sc + bonus > best[0] + 1e-9):
            best = (sc + bonus, i, sc, end)
    if best is None:
        # No title match: a byline naming the entry's author still marks
        # the start ("Mario Puhane" above a differently worded chronicle).
        if entry.authors and not entry.is_review:
            for i in usable:
                if _byline_names(blocks[i], entry, given):
                    start = i
                    for j in range(i - 1, max(-1, i - 3), -1):
                        if blocks[j]["type"] in _NON_BODY:
                            continue
                        if _is_section_label(blocks[j], labels):
                            start = j
                            continue
                        break
                    return {"index": start, "title_index": None, "score": 0.0,
                            "byline": _byline_names(blocks[i], entry, given)}
        return None
    _, ti, sc, title_end = best
    start = ti
    byline = None
    # Walk back over the byline and the section label above the title.
    for j in range(ti - 1, max(-1, ti - 4), -1):
        b = blocks[j]
        if b["type"] in _NON_BODY:
            continue
        # A review is signed at its *end*; a name right above a review's
        # citation is the previous review's reviewer, not a byline.
        names = None if entry.is_review else _byline_names(b, entry, given)
        if names:
            byline = names
            start = j
            continue
        if _is_section_label(b, labels):
            start = j
            continue
        break
    if byline is None and not entry.is_review:
        # byline below the title ("Titel" / "von HEINZ KELLERMANN")
        for j in range(title_end + 1, min(len(blocks), title_end + 3)):
            if blocks[j]["type"] in _NON_BODY:
                continue
            names = _byline_names(blocks[j], entry, given)
            if names:
                byline = names
            break
    return {"index": start, "title_index": ti, "title_end": title_end, "score": sc,
            "byline": byline}


_SUP_REF = r"<sup>\s*{n}\s*</sup>"


def _cites_footnote(blocks: List[dict], n: int) -> bool:
    pat_html = re.compile(_SUP_REF.format(n=n))
    pat_text = re.compile(rf"(?<=\w)\s+{n}(?=\s+[.,;:!?)\]]|\s*$)|(?<=\w){n}\^")
    for b in blocks:
        if pat_html.search(b.get("html") or "") or pat_text.search(b.get("text") or ""):
            return True
    return False


def _split_page(page: dict, cuts: List[Tuple[int, str]], last_note_n: Dict[str, int]
                ) -> Dict[str, List[dict]]:
    """Distribute the blocks of one page over the articles that share it.

    ``cuts`` are ``(block_index, article_id)`` in reading order; the first
    cut may be index 0. Body blocks go to the article whose cut precedes
    them; footnotes go to the article whose body cites them (by note
    number), else continue the numbering of the article before, else to
    the article that starts here; page headers/footers stay with the
    article that owns the top of the page.
    """
    blocks = page["blocks"]
    owner_of: List[Optional[str]] = [None] * len(blocks)
    cut_iter = sorted(cuts)
    for i, b in enumerate(blocks):
        if b["type"] in _NON_BODY:
            continue
        cur = None
        for idx, aid in cut_iter:
            if idx <= i:
                cur = aid
        owner_of[i] = cur
    ids_in_order = [aid for _, aid in cut_iter]
    first_owner = ids_in_order[0]
    body_by_art: Dict[str, List[dict]] = {aid: [] for aid in ids_in_order}
    for i, b in enumerate(blocks):
        if owner_of[i] is not None:
            body_by_art[owner_of[i]].append(b)
    from .layout import _fn_number
    for i, b in enumerate(blocks):
        if b["type"] in ("page-header", "page-footer"):
            owner_of[i] = first_owner
        elif b["type"] == "footnote":
            n = _fn_number(b)
            target = None
            if n:
                citing = [aid for aid in ids_in_order if _cites_footnote(body_by_art[aid], n)]
                if len(citing) == 1:
                    target = citing[0]
                else:
                    for aid in ids_in_order:
                        if last_note_n.get(aid) == n - 1:
                            target = aid
                            break
                    if target is None and n == 1:
                        target = ids_in_order[-1]
            elif n == 0 and len(ids_in_order) > 1:
                target = ids_in_order[-1]   # "*" note on an article's first page
            owner_of[i] = target or ids_in_order[0]
            if n:
                last_note_n[owner_of[i]] = n
    out: Dict[str, List[dict]] = {aid: [] for aid in ids_in_order}
    for i, b in enumerate(blocks):
        if owner_of[i] is not None:
            out[owner_of[i]].append(b)
    return out


def _toc_driven(
    unified_pages: list,
    toc: TocStructure,
    cfg: VolumeConfig,
) -> Optional[List[dict]]:
    """Try to build the article list from the TOC. Returns ``None`` if the
    TOC didn't carry enough page numbers to be useful."""
    from .names import base_given_names
    entries_with_pages = [e for e in toc.entries if e.page is not None]
    if len(entries_with_pages) < 2:
        return None

    # Resolve printed → PDF page mapping.
    offset = cfg.printed_page_offset
    if offset is None:
        offset = infer_printed_page_offset(unified_pages)
    if offset is None:
        # Last resort: assume the first entry's printed page maps to the
        # first PDF page we processed.
        first_pdf = unified_pages[0]["page_num"]
        offset = first_pdf - entries_with_pages[0].page

    by_pn = {p["page_num"]: p for p in unified_pages}
    min_pdf = min(by_pn)
    max_pdf = max(by_pn)
    labels = tuple(cfg.toc_section_labels or ())
    given = set(base_given_names())

    # Convert each TOC entry to an anchor: the PDF page and the block where
    # the article starts. The title is looked up on the expected page,
    # then on its neighbours (inserted plates shift the offset locally).
    to_pdf = printed_page_mapper(unified_pages, offset)
    anchors: List[dict] = []
    for order, e in enumerate(entries_with_pages):
        pdf_page = to_pdf(e.page)
        if pdf_page < min_pdf or pdf_page > max_pdf:
            continue
        found = None
        for delta, need in ((0, 0.6), (1, 0.75), (-1, 0.75), (2, 0.8)):
            pg = by_pn.get(pdf_page + delta)
            if pg is None:
                continue
            hit = _find_start(pg, e, labels, given, min_score=need)
            if hit:
                found = (pdf_page + delta, hit)
                break
        if found:
            anchors.append({"entry": e, "page": found[0], "index": found[1]["index"],
                            "title_index": found[1]["title_index"],
                            "title_end": found[1].get("title_end", found[1]["title_index"]),
                            "byline": found[1]["byline"], "matched": True, "order": order})
        else:
            # Title not found: start at the top of the page (old behaviour).
            pg = by_pn[pdf_page]
            idx = next((i for i, b in enumerate(pg["blocks"]) if b["type"] not in _NON_BODY), 0)
            anchors.append({"entry": e, "page": pdf_page, "index": idx, "title_index": None,
                            "byline": None, "matched": False, "order": order})
    if not anchors:
        return None

    # Document order; TOC order breaks ties (several reviews per page).
    anchors.sort(key=lambda a: (a["page"], a["index"], a["order"]))
    # Two entries may resolve to the same block (identical review titles);
    # an unmatched entry must not start above a matched one on its page.
    cleaned: List[dict] = []
    for a in anchors:
        if cleaned and (a["page"], a["index"]) <= (cleaned[-1]["page"], cleaned[-1]["index"]):
            if a["matched"] and cleaned[-1]["page"] == a["page"] and not cleaned[-1]["matched"]:
                cleaned[-1]["index"] = 0
            a = {**a, "index": max(a["index"], cleaned[-1]["index"])}
        cleaned.append(a)
    anchors = cleaned

    articles: List[dict] = []
    first_pdf = unified_pages[0]["page_num"]
    last_pdf = unified_pages[-1]["page_num"]

    # Frontmatter — everything before the first article start
    if (anchors[0]["page"], anchors[0]["index"]) > (first_pdf, 0):
        articles.append({
            "id":          f"{cfg.slug}-frontmatter",
            "num":         0,
            "title":       "Frontmatter",
            "author":      "",
            "authors":     [],
            "section":     "",
            "_start":      (first_pdf, 0),
        })

    for i, a in enumerate(anchors):
        entry = a["entry"]
        title = (entry.title or "").strip().rstrip(", .;:")
        # Refine the title via the in-page heading, but only when the
        # on-page text is at least as long as the TOC title. The on-page
        # version is often just the short headline ("Warum der Storch?")
        # while the subtitle lives in the TOC entry.
        page = by_pn.get(a["page"])
        if page is not None and not entry.is_review:
            anchor = _find_anchor_on_pdf_page(page, entry.title)
            if anchor and anchor.get("text"):
                anchor_text = " ".join(anchor["text"].split()).rstrip(", .;:")
                if len(anchor_text) >= len(title):
                    title = anchor_text
        authors = list(entry.authors or [])
        byline = a.get("byline") or []
        if byline:
            from .names import surname_key
            def _toc_form(n: str) -> str:
                for x in authors:
                    if _similar_key(surname_key(x), surname_key(n)):
                        return x
                return n
            merged = [_toc_form(n) for n in byline]
            if not authors or len(merged) > len(authors):
                authors = merged
        # Blocks the wiki/HTML need not repeat: the title heading and the
        # byline (they are the page's H1 and author line). Only when the
        # title block is really just the title.
        skip_ids: List[str] = []
        if a["matched"] and a.get("title_index") is not None and not entry.is_review and page:
            t_end = a.get("title_end") or a["title_index"]
            title_text = " ".join((page["blocks"][j].get("text") or "")
                                  for j in range(a["title_index"], t_end + 1))
            if len(title_text) <= 1.6 * len(entry.title or "") + 40:
                for j in range(a["index"], t_end + 1):
                    if page["blocks"][j]["type"] not in _NON_BODY:
                        skip_ids.append(page["blocks"][j]["id"])
                for j in range(t_end + 1, min(len(page["blocks"]), t_end + 3)):
                    b = page["blocks"][j]
                    if b["type"] in _NON_BODY:
                        continue
                    if _byline_names(b, entry, given):
                        skip_ids.append(b["id"])
                    break
        art = {
            "id":         f"{cfg.slug}-art{i + 1:02d}",
            "num":        i + 1,
            "title":      title,
            "author":     " / ".join(authors),
            "authors":    authors,
            "section":    entry.section,
            "_start":     (a["page"], a["index"]),
            "start_block": by_pn[a["page"]]["blocks"][a["index"]]["id"]
                           if by_pn[a["page"]]["blocks"] else None,
            "start_matched": a["matched"],
            "_skip_block_ids": skip_ids,
            # keep the source TOC entry for the knowledge graph
            "_toc_entry": asdict(entry),
        }
        if entry.is_review:
            art["review"] = {
                "title":   entry.reviewed_title or entry.title,
                "authors": list(entry.reviewed_authors),
                "editors": list(entry.reviewed_editors),
            }
        if entry.editors and not entry.is_review:
            art["editors"] = list(entry.editors)
        articles.append(art)

    _assign_blocks(articles, unified_pages, labels)
    _complete_reviewers_from_signatures(articles, given)

    # A TOC end page ("9-42") caps an article whose next anchor is far away
    # (back matter that is not in the TOC).
    for art in articles:
        te = art.get("_toc_entry") or {}
        if te.get("page_end") is not None:
            end_pdf = to_pdf(te["page_end"])
            if art["page_first"] <= end_pdf < art["page_last"]:
                art["pages"] = [p for p in art["pages"] if p["page_num"] <= end_pdf]
                art["page_last"] = end_pdf
    return articles


def _complete_reviewers_from_signatures(articles: List[dict], given) -> None:
    """A review ends with its reviewer's signature ("Reinhard Heydenreuter").
    Use it to complete reviewers the TOC names by surname only."""
    from .names import parse_byline, surname_key
    for art in articles:
        if not art.get("review") or not art.get("authors"):
            continue
        if all(len(n.split()) > 1 for n in art["authors"]):
            continue
        tail = [b for p in art.get("pages", []) for b in p["blocks"]
                if b["type"] not in _NON_BODY][-4:]
        signed: List[str] = []
        for b in tail:
            t = (b.get("text") or "").strip()
            if 0 < len(t) <= 80:
                signed += parse_byline(t, given) or []
        new = []
        for name in art["authors"]:
            if len(name.split()) == 1:
                hit = [n for n in signed if len(n.split()) > 1
                       and _similar_key(surname_key(n), surname_key(name))]
                name = hit[-1] if hit else name
            new.append(name)
        art["authors"] = new
        art["author"] = " / ".join(new)


def _assign_blocks(articles: List[dict], unified_pages: list, labels: Tuple[str, ...]) -> None:
    """Give every article its block-level slice of the volume.

    Sets ``pages`` (page views holding only the article's blocks),
    ``page_first`` and ``page_last`` on each article.
    """
    by_pn = {p["page_num"]: p for p in unified_pages}
    starts = [(a["_start"], a["id"]) for a in articles]
    starts.sort()
    order = [aid for _, aid in starts]
    cuts_by_page: Dict[int, List[Tuple[int, str]]] = {}
    for (pn, idx), aid in starts:
        cuts_by_page.setdefault(pn, []).append((idx, aid))

    pieces: Dict[str, List[Tuple[int, List[dict]]]] = {aid: [] for aid in order}
    current: Optional[str] = None
    last_note_n: Dict[str, int] = {}
    for p in unified_pages:
        pn = p["page_num"]
        cuts = list(cuts_by_page.get(pn, []))
        if current is not None and (not cuts or min(c[0] for c in cuts) > 0):
            cuts.insert(0, (-1, current))
        if not cuts:
            continue
        split = _split_page(p, [(max(0, i) if i >= 0 else -1, aid) for i, aid in cuts],
                            last_note_n)
        for aid, blks in split.items():
            # a section label ("BUCHBESPRECHUNGEN") is navigation, not text
            # of the article that follows (the front matter keeps its TOC
            # page headings)
            if not aid.endswith("-frontmatter"):
                blks = [b for b in blks
                        if not (_is_section_label(b, labels) and b["type"] == "section-header")]
            if blks:
                pieces[aid].append((pn, blks))
        current = max(cuts, key=lambda c: c[0])[1]

    art_by_id = {a["id"]: a for a in articles}
    for aid in order:
        art = art_by_id[aid]
        view_pages = []
        for pn, blks in pieces[aid]:
            full = by_pn[pn]
            has_content = any(b["type"] not in ("page-header", "page-footer") for b in blks)
            if not has_content and view_pages:
                continue
            view = {k: v for k, v in full.items() if k != "blocks"}
            view["blocks"] = blks
            if len(blks) != len(full["blocks"]):
                view["_partial"] = True
            view_pages.append(view)
        # drop leading/trailing pages that only hold running heads
        while view_pages and not any(b["type"] not in ("page-header", "page-footer")
                                     for b in view_pages[-1]["blocks"]):
            view_pages.pop()
        art["pages"] = view_pages
        start_pn = art["_start"][0]
        art["page_first"] = view_pages[0]["page_num"] if view_pages else start_pn
        art["page_last"] = view_pages[-1]["page_num"] if view_pages else start_pn
        art.pop("_start", None)


# ---------------------------------------------------------------------------
# Heuristic fallback (the original notebook logic)
# ---------------------------------------------------------------------------

def _heuristic(unified_pages: list, cfg: VolumeConfig) -> List[dict]:
    anchors = []
    for p in unified_pages:
        H = p["image_height"]
        upper_headers = [
            b for b in p["blocks"]
            if b["type"] == "section-header" and b["bbox"][1] < H / 3
        ]
        if upper_headers:
            top = sorted(upper_headers, key=lambda b: b["bbox"][1])[0]
            anchors.append((p["page_num"], top))

    if not anchors:
        anchors = [(unified_pages[0]["page_num"], {"text": cfg.volume_title})]

    articles: List[dict] = []
    first_pdf = unified_pages[0]["page_num"]
    last_pdf = unified_pages[-1]["page_num"]

    if anchors[0][0] > first_pdf:
        articles.append({
            "id":         f"{cfg.slug}-frontmatter",
            "num":        0,
            "title":      "Frontmatter",
            "author":     "",
            "authors":    [],
            "section":    "",
            "page_first": first_pdf,
            "page_last":  anchors[0][0] - 1,
        })

    for i, (pn, header) in enumerate(anchors):
        end = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else last_pdf
        title = (header.get("text") or "Untitled").strip()
        lines = [ln.strip() for ln in title.split("\n") if ln.strip()]
        clean_title = lines[0] if lines else title
        author = lines[1] if len(lines) > 1 else ""
        articles.append({
            "id":         f"{cfg.slug}-art{i + 1:02d}",
            "num":        i + 1,
            "title":      clean_title,
            "author":     author,
            "authors":    [author] if author else [],
            "section":    "",
            "page_first": pn,
            "page_last":  end,
        })

    by_pn = {p["page_num"]: p for p in unified_pages}
    for a in articles:
        a["pages"] = [by_pn[pn] for pn in range(a["page_first"], a["page_last"] + 1) if pn in by_pn]
    return articles


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def detect_articles(unified_pages: list, cfg: VolumeConfig) -> Tuple[List[dict], Optional[TocStructure]]:
    """Detect article boundaries in ``unified_pages``.

    Returns ``(articles, toc_or_none)``. The TOC structure (if any) is
    returned so downstream emitters (HTML, knowledge graph) can use it
    directly for the volume contents.
    """
    # 1) Try TOC-driven
    toc_blocks = find_toc_blocks(unified_pages)
    toc: Optional[TocStructure] = None
    if toc_blocks:
        # Walk every TOC page (TOC blocks *and* the section headings Chandra
        # puts outside them), resolving names against the contributor list.
        resolver = find_contributors(unified_pages)
        tokens = collect_toc_tokens(unified_pages, cfg.toc_section_labels)
        toc = parse_toc_structure(
            tokens=tokens,
            known_sections=cfg.toc_section_labels,
            given_names=resolver.given_names(),
            resolver=resolver,
        )
        articles = _toc_driven(unified_pages, toc, cfg)
        if articles:
            print(f"   article detection: TOC-driven ({len(toc.entries)} TOC entries → "
                  f"{sum(1 for a in articles if a['title'] != 'Frontmatter')} articles)")
            return articles, toc
        else:
            print("   article detection: TOC parsed but unusable, falling back to heuristic")

    # 2) Heuristic
    articles = _heuristic(unified_pages, cfg)
    print(f"   article detection: heuristic "
          f"({sum(1 for a in articles if a['title'] != 'Frontmatter')} articles)")
    return articles, toc
