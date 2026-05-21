"""Pipeline orchestration.

Runs every stage from PDF to bundled output, recording timings and writing
all artefacts to ``cfg.out_dir``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Optional

from . import render, ocr, normalize
from .config import VolumeConfig
from .emit import pagexml, tei, graph
from .emit.html import renderers as html_renderers
from .stage import stage, format_report
from .structure.articles import detect_articles
from .structure.footnotes import link_article_footnotes


# Absolute path to the canonical CSS/JS sources — assumed to live in the
# ``assets/`` directory next to the package. The CLI overrides this if the
# user supplies ``--assets``.
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def run(cfg: VolumeConfig, *, assets_dir: Optional[Path] = None) -> dict:
    """Run the whole pipeline for one volume. Returns the timings dict."""
    cfg.ensure_dirs()
    assets_dir = Path(assets_dir or DEFAULT_ASSETS_DIR)

    timings: dict = {}

    with stage("Render PDF → page images", timings):
        pages = render.run(cfg)

    with stage("OCR + layout (Chandra)", timings):
        ocr.run(cfg, pages, timings)

    with stage("Build unified per-page model", timings):
        unified = normalize.run(cfg, pages)

    with stage("Detect article boundaries", timings):
        articles, toc = detect_articles(unified, cfg)
        # Persist the articles for inspection
        (cfg.logs_dir / "articles.json").write_text(
            json.dumps(
                [{k: v for k, v in a.items() if k != "pages"} for a in articles],
                ensure_ascii=False, indent=2,
            )
        )
        if toc:
            (cfg.logs_dir / "toc.json").write_text(
                json.dumps(toc.as_dict(), ensure_ascii=False, indent=2)
            )
        for a in articles:
            if a["title"] == "Frontmatter":
                continue
            sec = a.get("section") or "?"
            print(f"   • {a['num']:>2}. p.{a['page_first']:>3}–{a['page_last']:<3} "
                  f"[{sec:<14s}]  {a['title'][:60]}")

    with stage("Link footnote references", timings):
        footnotes_by_article: dict = {}
        refs_by_article: dict = {}
        total_notes = 0
        total_refs = 0
        total_resolved = 0
        for art in articles:
            if art["title"] == "Frontmatter":
                continue
            notes, refs = link_article_footnotes(art)
            footnotes_by_article[art["id"]] = notes
            refs_by_article[art["id"]] = refs
            total_notes += len(notes)
            total_refs += len(refs)
            total_resolved += sum(1 for r in refs if r.target_id)
        print(f"   {total_notes} footnotes, {total_refs} refs in body "
              f"({total_resolved} resolved → linked, "
              f"{total_refs - total_resolved} unresolved)")

    with stage("Emit PageXML", timings):
        pagexml.run(cfg, unified)

    with stage("Emit TEI-XML", timings):
        tei.run(cfg, articles, unified, toc=toc)

    with stage("Emit knowledge graph (JSON-LD)", timings):
        graph.run(
            cfg, articles, unified, toc,
            footnotes_by_article=footnotes_by_article,
            refs_by_article=refs_by_article,
        )

    with stage("Build HTML edition", timings):
        html_renderers.run(
            cfg, articles, unified, assets_dir,
            footnotes_by_article=footnotes_by_article,
        )

    with stage("Bundle output", timings):
        bundle_path = Path(cfg.output_root) / f"{cfg.slug}.zip"
        if bundle_path.exists():
            bundle_path.unlink()
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in cfg.out_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(cfg.out_dir.parent))
        size_mb = bundle_path.stat().st_size / 1e6
        print(f"   wrote {bundle_path}  ({size_mb:.1f} MB)")

    header = f"{cfg.volume_title} {cfg.volume_number_roman} ({cfg.volume_year}) — pipeline complete"
    print("\n" + format_report(timings, header=header))

    (cfg.logs_dir / "timing.json").write_text(json.dumps(timings, indent=2))

    print("\nArtifacts:")
    print(f"  • TEI:      {cfg.tei_dir}/{cfg.slug}.xml")
    print(f"  • PageXML:  {cfg.pagexml_dir}/  ({len(list(cfg.pagexml_dir.glob('*.xml')))} files)")
    print(f"  • HTML:     {cfg.html_dir}/index.html")
    print(f"  • Graph:    {cfg.graph_dir}/{cfg.slug}.jsonld")
    print(f"  • Bundle:   {bundle_path}")

    return timings
