"""Render a PDF to per-page PNG images.

Stage 1 of the pipeline. PyMuPDF rasterises each page at ``cfg.render_dpi``
DPI and writes them to ``cfg.pages_dir``. Page indexing follows the user's
mental model: 1-based, inclusive on both ends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF
from tqdm.auto import tqdm

from .config import VolumeConfig


def render_pdf(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    page_range: Optional[Tuple[int, int]] = None,
) -> List[dict]:
    """Render ``pdf_path`` to ``out_dir``. Returns a list of page records.

    Each record is::

        {"page_num": int,
         "image_path": str,
         "image_filename": str,
         "width": int,
         "height": int}

    where ``page_num`` is the 1-based PDF page index (same as everywhere
    else in the pipeline).
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    n_total = doc.page_count
    if page_range is None:
        first, last = 1, n_total
    else:
        first, last = page_range
        last = min(last, n_total)
    n = last - first + 1
    print(f"PDF has {n_total} pages; rendering pages {first}-{last} "
          f"({n} pages) at {dpi} DPI")

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    records: List[dict] = []
    for idx in tqdm(range(first, last + 1), desc="render", unit="pg"):
        page = doc[idx - 1]  # PyMuPDF is 0-indexed
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out_path = out_dir / f"page_{idx:04d}.png"
        pix.save(str(out_path))
        records.append({
            "page_num":       idx,
            "image_path":     str(out_path),
            "image_filename": out_path.name,
            "width":          pix.width,
            "height":         pix.height,
        })
    doc.close()
    return records


def run(cfg: VolumeConfig) -> List[dict]:
    """Stage entry point. Renders pages and persists the page index."""
    cfg.ensure_dirs()
    pages = render_pdf(
        Path(cfg.pdf_path),
        cfg.pages_dir,
        cfg.render_dpi,
        cfg.page_range,
    )
    print(f"   {len(pages)} pages saved to {cfg.pages_dir}")
    (cfg.logs_dir / "pages_index.json").write_text(json.dumps(pages, indent=2))
    return pages
