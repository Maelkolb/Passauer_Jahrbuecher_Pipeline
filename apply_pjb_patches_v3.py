#!/usr/bin/env python3
"""Apply two more pipeline fixes on top of v1 + v2:

  * Reading order — narrow section-headers inside a single column were
    being treated as page-spanning, which made the band-layout in
    ``reading_order`` split the page at the header's y position and
    emit col-2-top blocks between col-1 blocks. The fix removes
    ``section-header`` from ``_SPANNING_TYPES`` and lets the existing
    geometric width check decide; truly wide article titles still get
    flagged as spanning by the >60 %-of-page-width rule.

  * Footnote rendering — markdown ``[^id]: text`` definitions are
    hoisted by GitHub's renderer into one auto-generated section at
    the bottom of the page, leaving any per-page ``### Page N``
    subheaders empty. Switch to HTML anchors
    (``<a id="fn-p018-1"></a><sup>1</sup> text <a href="#fnref-p018-1">↩</a>``)
    so the per-page grouping survives the round-trip through GitHub
    and Obsidian.

Idempotent. Adds 3 regression tests in tests/test_columns.py.
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
        (REPO / "pjb_pipeline" / "structure" / "columns.py").exists()
        and (REPO / "pjb_pipeline" / "emit" / "wiki.py").exists()
    )


def backup_once(path: pathlib.Path, tag: str = "bak4") -> None:
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
# columns.py — section-header no longer in _SPANNING_TYPES
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/structure/columns.py",
    marker="defer the spanning vs in-column decision to geometry instead of type",
    old='''# Block types that should NEVER be confined to a single column \u2014 they
# always span the full page width.
_SPANNING_TYPES = {
    "section-header",
    "page-header",
    "page-footer",
    "table-of-contents",
    "title",
}''',
    new='''# Block types that should NEVER be confined to a single column \u2014 they
# always span the full page width regardless of geometry.
#
# Note: ``section-header`` is intentionally NOT in this set, even though
# Chandra often emits section headers near the top of a page that DO
# span both columns. Many narrow section headers live entirely inside
# one column ("Die Bedeutung der Fahnentiere \u2014 Kalender, G\u00f6tter,
# Regimentsgeschichte(n)" on p20 of vol 52 is the canonical example),
# and unconditionally treating them as spanning makes ``reading_order``
# split the page into bands at the wrong y, which scrambles which
# column-2 block follows which column-1 block. The width check in
# ``assign_columns`` (block wider than 60 % of the page \u2192 ``_column =
# None``) still catches the genuinely-spanning article titles, so we
# defer the spanning vs in-column decision to geometry instead of type.
_SPANNING_TYPES = {
    "page-header",
    "page-footer",
    "table-of-contents",
    "title",
}''',
    what="remove section-header from SPANNING_TYPES",
)

# ---------------------------------------------------------------------------
# emit/wiki.py \u2014 HTML anchor inline refs + HTML anchor definitions
# ---------------------------------------------------------------------------
replace_once(
    "pjb_pipeline/emit/wiki.py",
    marker='id="fnref-',
    old='''def _insert_footnote_refs(
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

    return _PLAIN_DIGIT_REF.sub(repl, text)''',
    new='''def _insert_footnote_refs(
    text: str,
    page_num: Optional[int],
    page_numbered: set,
) -> str:
    """Rewrite a body chunk so its inline footnote references become
    HTML anchor links to the matching definitions in the footnotes
    section.

    Why HTML anchors and not markdown ``[^id]`` syntax: GitHub\'s
    markdown renderer hoists every ``[^id]: text`` definition out of
    where it appears and consolidates them into one auto-generated
    "Footnotes" section at the bottom of the rendered page \u2014 leaving
    any per-page subheaders ``### Page 18``, ``### Page 19`` \u2026 sitting
    in the original location with nothing under them. Manual HTML
    anchors stay where we write them, so the per-page grouping of
    footnote definitions survives the round-trip through GitHub (and
    Obsidian).

    The rewrite is still conservative: it only fires on a plain digit
    that sits between a word and a sentence-end punctuation mark AND
    that matches a real numbered footnote on the same page. Years
    (four digits), citation lists (no spaces around the comma), and
    in-paragraph numbers on pages that happen to have no matching
    footnote all pass through untouched.
    """
    if not page_numbered:
        return text

    def repl(m: "re.Match[str]") -> str:
        n = int(m.group("n"))
        if n not in page_numbered:
            return m.group(0)
        fid = _footnote_id(page_num, n)
        return (
            f\'<sup><a id="fnref-{fid}" href="#fn-{fid}">{n}</a></sup>\'
        )

    return _PLAIN_DIGIT_REF.sub(repl, text)''',
    what="switch inline refs to HTML anchors",
)

replace_once(
    "pjb_pipeline/emit/wiki.py",
    marker='id="fn-{fid}"',
    old='''def _render_footnotes_section(notes: List[Footnote]) -> str:
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
    return "".join(parts)''',
    new='''def _render_footnotes_section(notes: List[Footnote]) -> str:
    """Render the article\'s footnotes, grouped by source page.

    Each numbered footnote becomes a paragraph with an HTML anchor
    target (``<a id="fn-p018-1"></a>``) and a back-link arrow
    (``\u21a9``) pointing to the inline reference. Unnumbered footnotes
    (the asterisk note, the abbreviation key list) get an anchor but
    no back-link since nothing in the body refers to them.

    The per-page ``### Page N`` subheaders keep their contents in
    place because HTML anchors are not subject to the hoisting that
    markdown ``[^id]: text`` definitions are.
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
            if fn.is_numbered:
                parts.append(
                    f\'<a id="fn-{fid}"></a><sup>{fn.n}</sup>\\u2003{text} \'
                    f\'<a href="#fnref-{fid}">\\u21a9</a>\\n\\n\'
                )
            else:
                parts.append(f\'<a id="fn-{fid}"></a>{text}\\n\\n\')
    return "".join(parts)''',
    what="switch footnote definitions to HTML anchors",
)

# ---------------------------------------------------------------------------
# Update stale tests
# ---------------------------------------------------------------------------
replace_once(
    "tests/test_wiki.py",
    marker='id="fn-p001-1"',
    old='''    assert "## Footnotes" in body
    # Footnote definitions use standard markdown footnote syntax now,
    # with per-page-scoped IDs, so an inline ``[^p001-1]`` ref in the
    # body resolves cleanly even across pages whose footnote numbers
    # restart.
    assert "[^p001-1]: First footnote text." in body
    assert "[^p001-2]: Second footnote text." in body''',
    new='''    assert "## Footnotes" in body
    # Footnote definitions use HTML anchors so per-page subheaders stay
    # populated (markdown ``[^id]: text`` syntax is hoisted by GitHub\'s
    # renderer into a single auto-generated section at the bottom,
    # which empties any per-page subheaders we\'d put around them).
    assert \'id="fn-p001-1"\' in body
    assert \'id="fn-p001-2"\' in body
    assert "First footnote text." in body
    assert "Second footnote text." in body''',
    what="update body+footnotes test to HTML expectations",
)

replace_once(
    "tests/test_wiki.py",
    marker='id="fn-p018-1"',
    old='''        # Markdown-footnote syntax with page-scoped IDs: two "1." entries
        # no longer collide because their IDs (p018-1 vs p019-1) are
        # distinct.
        page18 = out.split("### Page 19")[0]
        page19 = out.split("### Page 19")[1]
        assert "[^p018-1]: Stoll" in page18
        assert "[^p018-2]: Domaszewski" in page18
        assert "[^p019-1]: Ankersdorfer" in page19''',
    new='''        # HTML-anchor footnotes with page-scoped IDs: per-page
        # subheaders survive in the rendered output, and the two "1"
        # entries no longer collide because their anchor IDs
        # (p018-1 vs p019-1) are distinct.
        page18 = out.split("### Page 19")[0]
        page19 = out.split("### Page 19")[1]
        assert \'id="fn-p018-1"\' in page18
        assert "Stoll" in page18
        assert \'id="fn-p018-2"\' in page18
        assert "Domaszewski" in page18
        assert \'id="fn-p019-1"\' in page19
        assert "Ankersdorfer" in page19''',
    what="update grouping test to HTML expectations",
)

replace_once(
    "tests/test_wiki.py",
    marker="class TestHtmlAnchorFootnoteRefsInBody",
    old='''class TestMarkdownFootnoteRefsInBody:
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
        assert out == text''',
    new='''class TestHtmlAnchorFootnoteRefsInBody:
    """When a body block contains an inline plain-digit footnote ref AND
    a footnote with that number exists on the same page, the wiki
    emitter rewrites the digit as an HTML ``<sup><a>\u2026</a></sup>`` link
    to the matching definition. HTML anchors survive GitHub\'s
    markdown rendering without being hoisted, so the per-page
    grouping of footnote definitions stays intact."""

    def test_inline_ref_becomes_html_anchor(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Wie jede der r\u00f6mischen Legionen bezogen 2 . Sie waren ..."
        out = _insert_footnote_refs(text, page_num=18, page_numbered={1, 2, 3})
        assert \'href="#fn-p018-2"\' in out
        assert \'id="fnref-p018-2"\' in out
        assert "<sup>" in out
        assert "bezogen" in out and "Sie waren" in out
        assert "bezogen 2 ." not in out

    def test_unknown_number_passes_through(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Bei Punkt 7 wird es klar ."
        out = _insert_footnote_refs(text, page_num=18, page_numbered={1, 2})
        assert "<sup>" not in out
        assert "fn-" not in out
        assert "Punkt 7 wird" in out

    def test_empty_numbered_set_returns_text_unchanged(self):
        from pjb_pipeline.emit.wiki import _insert_footnote_refs
        text = "Etwas mit Zahlen 2 und 5 ."
        out = _insert_footnote_refs(text, page_num=18, page_numbered=set())
        assert out == text''',
    what="rename test class + update assertions",
)

# ---------------------------------------------------------------------------
# Append column-detection regression tests
# ---------------------------------------------------------------------------
_TEST_COLUMNS_NEW = '''

# ---------------------------------------------------------------------------
# Narrow section-header within a column must not split the page into bands
# ---------------------------------------------------------------------------

class TestNarrowSectionHeaderIsColumnResident:
    """Regression for the page-20 scenario in vol 52.

    Chandra labels in-column subheadings as ``section-header``. Before
    the fix, ``_SPANNING_TYPES`` listed section-header unconditionally,
    so any such block forced ``assign_columns`` to set ``_column=None``
    and ``reading_order`` then treated it as a band separator splitting
    the page at its y-position. With a header midway down column 1,
    the band-above-the-header would emit col-1-top + col-2-top, then
    the header, then band-below \u2014 putting column-2\'s top blocks
    *between* column-1\'s continuation, which is wrong.

    With the fix, ``section-header`` is no longer in ``_SPANNING_TYPES``;
    only blocks that genuinely span the page (>60 % page width or
    straddling both columns) get ``_column=None``. A narrow in-column
    section-header is assigned to its actual column, and reading order
    proceeds column-by-column without artificial band splits.
    """

    def _vol52_p20_page(self):
        # Synthesises page 20 of vol 52: two-column body with a narrow
        # ``section-header`` block midway down column 1.
        return {
            "image_width": 1400,
            "image_height": 2200,
            "blocks": [
                _block("p20_b001", "text",            [100, 200, 650, 400]),    # col 1 top
                _block("p20_b002", "text",            [100, 420, 650, 870]),    # col 1 mid
                _block("p20_b003", "section-header",  [100, 900, 600, 940]),    # narrow in-col-1
                _block("p20_b004", "text",            [100, 970, 650, 1500]),   # col 1 bottom
                _block("p20_b005", "text",            [750, 200, 1300, 700]),   # col 2 top
                _block("p20_b006", "text",            [750, 720, 1300, 1500]),  # col 2 bottom
            ],
        }

    def test_narrow_section_header_is_assigned_to_a_column(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns,
        )
        page = self._vol52_p20_page()
        cols = detect_columns(page)
        assert len(cols) == 2, "test page should detect as 2-column"
        assigned = assign_columns(page, cols)
        by_id = {b["id"]: b for b in assigned}
        sh = by_id["p20_b003"]
        assert sh["_column"] is not None, (
            "narrow section-header inside column 1 should be column-"
            "resident, not spanning"
        )
        assert sh["_column"] == 0

    def test_reading_order_does_not_split_around_narrow_header(self):
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns, reading_order,
        )
        page = self._vol52_p20_page()
        cols = detect_columns(page)
        assigned = assign_columns(page, cols)
        ordered = [b["id"] for b in reading_order(assigned, cols)]
        # column 0 reads top-to-bottom first, then column 1
        i_b001 = ordered.index("p20_b001")
        i_b002 = ordered.index("p20_b002")
        i_b003 = ordered.index("p20_b003")
        i_b004 = ordered.index("p20_b004")
        i_b005 = ordered.index("p20_b005")
        i_b006 = ordered.index("p20_b006")
        # column 0 in y order
        assert i_b001 < i_b002 < i_b003 < i_b004
        # column 1 in y order, contiguous (no col-0 block between)
        assert i_b005 < i_b006
        # all of column 0 before any of column 1
        assert max(i_b001, i_b002, i_b003, i_b004) < min(i_b005, i_b006)

    def test_wide_section_header_still_spans(self):
        # Article-title-style section header that genuinely spans both
        # columns (width > 60 % of page) must still be detected as
        # spanning by the geometric width check.
        from pjb_pipeline.structure.columns import (
            detect_columns, assign_columns,
        )
        page = {
            "image_width": 1400,
            "image_height": 2200,
            "blocks": [
                _block("title", "section-header", [150, 100, 1250, 180]),
                _block("a", "text", [100, 250, 650, 800]),
                _block("b", "text", [750, 250, 1300, 800]),
                _block("c", "text", [100, 820, 650, 1500]),
                _block("d", "text", [750, 820, 1300, 1500]),
            ],
        }
        cols = detect_columns(page)
        assigned = assign_columns(page, cols)
        title = next(b for b in assigned if b["id"] == "title")
        assert title["_column"] is None, (
            "wide section-header (>60 % page width) must still be"
            " treated as spanning"
        )
'''

append_once(
    "tests/test_columns.py",
    marker="class TestNarrowSectionHeaderIsColumnResident",
    addition=_TEST_COLUMNS_NEW,
    what="regression: narrow section-header column-residency",
)

# ---------------------------------------------------------------------------
# Run the test suite
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
    sys.exit(f"\nTests failed after {elapsed:.1f}s. Backups are at *.bak4.")
print(f"\nTests passed in {elapsed:.1f}s. Backups of modified files are at *.bak4.")
