#!/usr/bin/env python3
"""Merge per-volume JSON-LD knowledge-graph fragments into one corpus graph.

Each volume run writes a JSON-LD document to ``output/<slug>/graph/<slug>.jsonld``.
Once all 50 volumes have been processed, run this to deduplicate nodes by
``@id`` and produce a single corpus-wide graph. Same-named authors across
volumes collapse to one ``Person`` node automatically (their IRIs are
derived from a normalised form of the name).

The CLI subcommand ``pjb-pipeline merge-graphs`` does the same thing — this
script is here so you can import ``merge`` directly from Python if you
want to customise the merge logic (e.g. add provenance, fold in NER output).

Usage:

    python scripts/merge_graphs.py corpus.jsonld output/*/graph/*.jsonld
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


def merge(inputs: Iterable[Path]) -> dict:
    """Merge a list of JSON-LD documents. Returns the merged doc.

    Strategy: index every node by its ``@id`` and union the values of
    matching keys. We *don't* try to be clever about list merging here —
    the goal is the simple case where the per-volume graphs share Series
    and Person nodes with byte-identical fields.
    """
    merged_nodes: dict = {}
    first_context = None

    for path in inputs:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if first_context is None:
            first_context = doc.get("@context")
        for node in doc.get("@graph", []):
            nid = node.get("@id")
            if not nid:
                continue
            if nid in merged_nodes:
                existing = merged_nodes[nid]
                for k, v in node.items():
                    if k == "@id":
                        continue
                    if k not in existing:
                        existing[k] = v
            else:
                merged_nodes[nid] = dict(node)

    return {
        "@context": first_context,
        "@graph":   list(merged_nodes.values()),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("output", type=Path, help="Output .jsonld file")
    p.add_argument("inputs", type=Path, nargs="+", help="Input .jsonld files")
    args = p.parse_args(argv)

    out_doc = merge(args.inputs)
    args.output.write_text(
        json.dumps(out_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    n_nodes = len(out_doc["@graph"])
    print(f"Merged {len(args.inputs)} graphs → {args.output} ({n_nodes} unique nodes)")

    # A small breakdown by @type
    counts: dict = {}
    for node in out_doc["@graph"]:
        t = node.get("@type")
        if isinstance(t, list):
            for tt in t:
                counts[tt] = counts.get(tt, 0) + 1
        else:
            counts[t] = counts.get(t, 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k or '(no @type)':<24s} {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
