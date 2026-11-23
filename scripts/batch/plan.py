#!/usr/bin/env python3
"""Plan a batch run: one subfolder per volume under a scan root.

    plan.py list   SCAN_ROOT [--order asc|desc]
        One tab-separated line per volume, sorted by year:
        tag  year  vol  roman  n_images  source_dir  title  subtitle

    plan.py config SCAN_ROOT YEAR OUT.yaml
        Write the volume config for YEAR (same template as the runbook).

The year is the first 4-digit number 1900-2099 in the subfolder name
(``1975``, ``PJB_1975``, ``2026-11-24_10-00_XVII_1975_finished`` -> 1975;
a leading scanner timestamp like ``2026-11-24`` is skipped). The volume
number defaults to year - 1958 (1999 = Vol 41). Exceptions go into
``volumes.tsv`` in the scan root or next to this script:

    # year  vol  roman  title  subtitle      (empty or missing = default)
    1975    17   XVII   Ostbairische Grenzmarken    Passauer Jahrbuch fuer ...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # repo root -> pjb_pipeline

DEFAULT_TITLE = "Passauer Jahrbuch"
DEFAULT_SUBTITLE = "Beitraege zur Geschichte und Kultur Ostbaierns"

CONFIG_TEMPLATE = """\
# Vol {roman} ({year})
source_path: "{source}"
volume_number: {vol}
volume_number_roman: "{roman}"
volume_year: {year}
volume_title: "{title}"
volume_subtitle: "{subtitle}"
publisher: "Verein fuer Ostbairische Heimatforschung"
editor: "Institut fuer Kulturraumforschung Ostbaierns und der Nachbarregionen"
language: "de"
series_name: "Passauer Jahrbuecher"
output_root: "./output"
render_dpi: 200
image_max_side: 2200
page_range: null
printed_page_offset: null
ocr:
  method: "vllm"
  vllm_url: "http://localhost:8000/v1"
  model: "datalab-to/chandra"
  batch_size: 1
toc_section_labels:
  - "AUFSÄTZE"
  - "BERICHTE"
  - "MISZELLE"
  - "REZENSIONEN"
  - "BUCHBESPRECHUNGEN"
  - "BIBLIOGRAPHIE"
  - "NACHRUFE"
  - "VEREINSCHRONIK"
  - "MITARBEITER"
crop_regions: true
"""


def roman(n: int) -> str:
    out = ""
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                 (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + s, n - v
    return out


def year_of(name: str) -> int | None:
    # skip a scanner timestamp prefix (YYYY-MM-DD[_HH-MM])
    name = re.sub(r"^\d{4}-\d{2}-\d{2}(_\d{2}-\d{2})?", "", name)
    m = re.search(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", name)
    return int(m.group(1)) if m else None


def overrides(root: Path) -> dict[int, list[str]]:
    table: dict[int, list[str]] = {}
    for f in (HERE / "volumes.tsv", root / "volumes.tsv"):  # scan root wins
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = line.split("\t")
            table[int(cols[0])] = [c.strip() for c in cols[1:]] + [""] * 4
    return table


def volumes(root: Path) -> list[dict]:
    from pjb_pipeline.render import resolve_source

    ov = overrides(root)
    vols, problems, seen = [], [], {}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        y = year_of(d.name)
        if y is None:
            problems.append(f"no year in folder name: {d.name}")
            continue
        if y in seen:
            problems.append(f"year {y} twice: {seen[y]} and {d.name}")
            continue
        seen[y] = d.name
        try:
            kind, _, images = resolve_source(d)
        except Exception as e:
            problems.append(f"{d.name}: {e}")
            continue
        o = ov.get(y, [""] * 4)
        vol = int(o[0]) if o[0] else y - 1958
        vols.append(dict(
            year=y, vol=vol, roman=o[1] or roman(vol),
            title=o[2] or DEFAULT_TITLE, subtitle=o[3] or DEFAULT_SUBTITLE,
            tag=f"pjb-{vol:03d}-{y}", source=str(d),
            n=len(images) if kind == "images" else "pdf",
        ))
    for p in problems:
        print(f"PROBLEM\t{p}", file=sys.stderr)
    if problems:
        raise SystemExit(1)
    return vols


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("list")
    a.add_argument("root", type=Path)
    a.add_argument("--order", choices=("asc", "desc"), default="asc")
    c = sub.add_parser("config")
    c.add_argument("root", type=Path)
    c.add_argument("year", type=int)
    c.add_argument("out", type=Path)
    args = ap.parse_args()

    vols = volumes(args.root.expanduser().resolve())
    if args.cmd == "list":
        for v in sorted(vols, key=lambda v: v["year"], reverse=args.order == "desc"):
            print("\t".join(str(v[k]) for k in
                            ("tag", "year", "vol", "roman", "n", "source", "title", "subtitle")))
    else:
        v = next((v for v in vols if v["year"] == args.year), None)
        if v is None:
            raise SystemExit(f"year {args.year} not in {args.root}")
        args.out.write_text(CONFIG_TEMPLATE.format(**v), encoding="utf-8")


if __name__ == "__main__":
    main()
