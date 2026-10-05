"""Pipeline orchestration.

Runs every stage from the scanned source (a book PDF or a folder of page
images) to bundled output, recording timings and writing all artefacts to
``cfg.out_dir``. Re-running a volume reuses rendered pages and cached OCR.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Optional

from . import render, ocr, normalize
from .config import VolumeConfig
from .emit import pagexml, tei, graph, wiki
from .emit.html import renderers as html_renderers
from .emit.html.crops import make_region_crops
from .stage import stage, format_report
from .structure.articles import detect_articles
from .structure.footnotes import link_article_footnotes


# Absolute path to the canonical CSS/JS sources — assumed to live in the
# ``assets/`` directory next to the package. The CLI overrides this if the
# user supplies ``--assets``.
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def bundle_output(cfg: VolumeConfig) -> Path:
    """Zip the volume's output directory to ``<output_root>/<slug>.zip``.

    Symlinked sub-directories are followed (``pages/`` may live on another
    disk), so the bundle always carries the page scans that the TEI
    facsimile, PageXML and graph ``facsimile`` links point to.
    """
    bundle_path = Path(cfg.output_root) / f"{cfg.slug}.zip"
    if bundle_path.exists():
        bundle_path.unlink()
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(cfg.out_dir, followlinks=True):
            dirs.sort()
            for name in sorted(files):
                p = Path(root) / name
                zf.write(p, p.relative_to(cfg.out_dir.parent))
    return bundle_path


def run(cfg: VolumeConfig, *, assets_dir: Optional[Path] = None) -> dict:
    """Run the whole pipeline for one volume. Returns the timings dict."""
    cfg.ensure_dirs()
    assets_dir = Path(assets_dir or DEFAULT_ASSETS_DIR)

    timings: dict = {}

    with stage("Prepare page images (PDF or image folder)", timings):
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
            flag = " " if a.get("start_matched", True) else "?"
            who = " / ".join(a.get("authors") or []) or "—"
            if a.get("review"):
                who = f"rev. {who}"
            print(f"   •{flag}{a['num']:>2}. p.{a['page_first']:>3}–{a['page_last']:<3} "
                  f"[{sec:<14s}] {who[:32]:<32s} {a['title'][:50]}")
        n_real = sum(1 for a in articles if a["title"] != "Frontmatter")
        n_matched = sum(1 for a in articles if a.get("start_matched"))
        if any("start_matched" in a for a in articles):
            print(f"   article starts located at block level: {n_matched}/{n_real} "
                  f"(? = title not found on its page; article starts at the page top)")

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

    with stage("Crop visual regions", timings):
        # Promoted from the HTML stage so the JSON-LD graph can reference
        # the cropped figure/image/diagram files as ImageObject nodes.
        # Annotates each visual block with ``_crop`` and ``_crop_url``.
        n_crops = make_region_crops(cfg, unified)
        print(f"   {n_crops} visual region crops written to {cfg.regions_dir}")

    with stage("Emit knowledge graph (JSON-LD)", timings):
        graph.run(
            cfg, articles, unified, toc,
            footnotes_by_article=footnotes_by_article,
            refs_by_article=refs_by_article,
        )

    with stage("Emit LLM-Wiki (markdown)", timings):
        wiki.run(
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
        bundle_path = bundle_output(cfg)
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
    print(f"  • Wiki:     {cfg.wiki_dir}/  "
          f"({len(list((cfg.wiki_dir / 'articles').glob('*.md')))} articles, "
          f"{len(list((cfg.wiki_dir / 'people').glob('*.md')))} people)")
    print(f"  • Bundle:   {bundle_path}")

    return timings
