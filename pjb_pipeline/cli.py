"""Command-line interface.

Two entry points:

* ``pjb-pipeline run <config.yaml>`` — process one volume end-to-end.
* ``pjb-pipeline merge-graphs <out.jsonld> <vol1.jsonld> [vol2.jsonld …]``
  — merge per-volume JSON-LD graphs into one (so the same Person node
  appears once across the whole collection).

All flags on ``run`` override the matching YAML key, so the same config
file can be used on Colab and on the GPU host with one ``--ocr-method``
or ``--vllm-url`` flag.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import VolumeConfig

# Default location of CSS/JS assets, resolved without importing the
# pipeline module (which would pull in PyMuPDF and chandra eagerly).
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pjb-pipeline",
        description="Passauer Jahrbücher digital edition pipeline",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ---- run -------------------------------------------------------
    r = sub.add_parser("run", help="Process one volume end-to-end")
    r.add_argument("config", type=Path, help="Path to volume YAML config")
    r.add_argument("--pdf", type=str, default=None,
                   help="Override pdf_path from the config")
    r.add_argument("--output-root", type=str, default=None,
                   help="Override output_root from the config")
    r.add_argument("--page-range", type=str, default=None,
                   help='Override page_range as "first-last" (e.g. "3-20")')
    r.add_argument("--ocr-method", choices=("hf", "vllm"), default=None,
                   help="Override ocr.method")
    r.add_argument("--vllm-url", type=str, default=None,
                   help="Override ocr.vllm_url (e.g. http://10.0.0.42:8000/v1)")
    r.add_argument("--dpi", type=int, default=None,
                   help="Override render_dpi")
    r.add_argument("--no-crops", action="store_true",
                   help="Skip the (expensive) region cropping step")
    r.add_argument("--assets", type=Path, default=DEFAULT_ASSETS_DIR,
                   help="Directory with edition.css / edition.js "
                        f"(default: {DEFAULT_ASSETS_DIR})")

    # ---- merge-graphs ----------------------------------------------
    m = sub.add_parser("merge-graphs",
                       help="Merge per-volume JSON-LD graphs into one")
    m.add_argument("output", type=Path, help="Output .jsonld file")
    m.add_argument("inputs", type=Path, nargs="+", help="Input .jsonld files")

    return p


def _apply_overrides(cfg: VolumeConfig, args) -> VolumeConfig:
    if args.pdf:
        cfg.pdf_path = args.pdf
    if args.output_root:
        cfg.output_root = args.output_root
    if args.page_range:
        try:
            a, b = args.page_range.split("-")
            cfg.page_range = (int(a), int(b))
        except Exception:
            print(f"Invalid --page-range '{args.page_range}' "
                  "(expected 'first-last', e.g. '3-20')", file=sys.stderr)
            raise SystemExit(2)
    if args.ocr_method:
        cfg.ocr.method = args.ocr_method
    if args.vllm_url:
        cfg.ocr.vllm_url = args.vllm_url
    if args.dpi:
        cfg.render_dpi = args.dpi
    if args.no_crops:
        cfg.crop_regions = False
    return cfg


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "run":
        # Import here so --help and merge-graphs work without PyMuPDF/chandra
        from .pipeline import run as run_pipeline
        cfg = VolumeConfig.from_yaml(args.config)
        cfg = _apply_overrides(cfg, args)
        run_pipeline(cfg, assets_dir=args.assets)
        return 0

    if args.command == "merge-graphs":
        # Delegate to the script in scripts/merge_graphs.py
        from importlib import import_module
        # The merger lives under scripts/ but can also be imported lazily.
        # Re-implement the small bit inline so the CLI doesn't depend on
        # sys.path tricks.
        import json
        merged_nodes: dict = {}
        first_context = None
        for path in args.inputs:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            if first_context is None:
                first_context = doc.get("@context")
            for node in doc.get("@graph", []):
                nid = node.get("@id")
                if not nid:
                    continue
                if nid in merged_nodes:
                    # Merge author lists, etc. — naive deep-merge.
                    existing = merged_nodes[nid]
                    for k, v in node.items():
                        if k == "@id":
                            continue
                        if k not in existing:
                            existing[k] = v
                else:
                    merged_nodes[nid] = dict(node)
        out_doc = {
            "@context": first_context,
            "@graph":   list(merged_nodes.values()),
        }
        args.output.write_text(
            json.dumps(out_doc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Merged {len(args.inputs)} graphs → {args.output} "
              f"({len(merged_nodes)} unique nodes)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
