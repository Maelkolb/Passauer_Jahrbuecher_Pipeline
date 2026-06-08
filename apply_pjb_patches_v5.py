#!/usr/bin/env python3
"""Tighten the reading-order audit so it stops flagging cross-page breaks.

The v4 audit flagged any block ending in "<word>-" whose next block starts
uppercase. On vol 52 that produced 48 hits, every one of which was a false
positive: a body paragraph ending mid-word at a column/page bottom, followed
by a footnote, page-footer, image description, or new-article title — i.e. a
normal cross-page hyphenation, with the word's other half on the next page.

A genuine misplacement only looks like one thing: body prose ending mid-word
whose continuation is another body block that did NOT follow it. So this
patch restricts the hyphen-break check to body -> body transitions and
ignores breaks where the next block is a non-prose region (footnote, footer,
header, image, caption, title, TOC, page number). After this, a clean audit
run actually means clean.

Idempotent. Only touches scripts/check_reading_order.py.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

REPO = pathlib.Path(".").resolve()
TARGET = REPO / "scripts" / "check_reading_order.py"

OLD = '''    bytid = {b.get("id"): b for b in ordered}
    hyphen_breaks = []
    for i, bid in enumerate(final[:-1]):
        t = (bytid[bid].get("text") or "").rstrip()
        if t.endswith("-") and not t.endswith(" -"):
            nxt = (bytid[final[i + 1]].get("text") or "").lstrip()
            first = next((c for c in nxt if c.isalpha()), "")
            if first and not first.islower():
                hyphen_breaks.append((bid, final[i + 1]))'''

NEW = '''    # Only body prose can host a *misplaced* continuation. A body block
    # ending in "<word>-" followed by a non-prose region (footnote, footer,
    # image, title, \u2026) is a normal cross-page break \u2014 the word's other half
    # is on the following page \u2014 so we ignore those. We only flag a break
    # where the very next block is also body prose yet starts uppercase,
    # which is the signature of a continuation that landed out of order.
    PROSE = {"text", "paragraph", "body"}
    bytid = {b.get("id"): b for b in ordered}
    hyphen_breaks = []
    for i, bid in enumerate(final[:-1]):
        cur = bytid[bid]
        if cur.get("type") not in PROSE:
            continue
        t = (cur.get("text") or "").rstrip()
        if not (t.endswith("-") and not t.endswith(" -")):
            continue
        nxt_block = bytid[final[i + 1]]
        if nxt_block.get("type") not in PROSE:
            continue
        nxt = (nxt_block.get("text") or "").lstrip()
        first = next((c for c in nxt if c.isalpha()), "")
        if first and not first.islower():
            hyphen_breaks.append((bid, final[i + 1]))'''

if not TARGET.exists():
    sys.exit("scripts/check_reading_order.py not found \u2014 apply v4 first.")

s = TARGET.read_text(encoding="utf-8")
if "PROSE = {" in s:
    print("scripts/check_reading_order.py: skip (already tightened)")
    sys.exit(0)
if OLD not in s:
    sys.exit("ERROR: cannot find the v4 audit block to replace \u2014 has it drifted?")

bak = TARGET.with_suffix(".py.bak6")
if not bak.exists():
    shutil.copy2(TARGET, bak)
TARGET.write_text(s.replace(OLD, NEW, 1), encoding="utf-8")
print("scripts/check_reading_order.py: applied (body-prose-only hyphen check)")

# Sanity: import still works
sys.path.insert(0, str(REPO))
import importlib.util
spec = importlib.util.spec_from_file_location("_chk", TARGET)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print("scripts/check_reading_order.py: imports OK. Backup at *.py.bak6.")
