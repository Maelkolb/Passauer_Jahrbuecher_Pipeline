"""Crop visual-region bboxes out of page PNGs for downstream use.

The HTML page-facsimile and article-reading views embed the actual cropped
image of each figure/image/diagram block. The JSON-LD graph also references
the same crops as ``ImageObject`` nodes. So the crops are a *first-class
artifact*, written to ``<volume_root>/regions/`` once and shared by every
downstream emitter.

The HTML-side files refer to this directory via a relative path computed by
:func:`region_crop_url_for_html`; the graph references the volume-root
relative path ``regions/<filename>``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

from ...config import VolumeConfig


# Subdirectory name used both at the volume root (``<out_dir>/regions/``)
# and — for backwards-compatible HTML output — under ``html/`` if the html
# side ever needs a local copy. The volume-root copy is the canonical one.
REGIONS_DIR_NAME = "regions"
VISUAL_BLOCK_TYPES = {"figure", "image", "diagram"}


def region_crop_filename(blk: dict) -> str:
    """Deterministic filename for ``blk``'s crop. Used by every emitter
    so a graph node and an HTML <img> always point at the same file."""
    return f"{blk['id']}.png"


def region_crop_graph_url(blk: dict) -> str:
    """Volume-root-relative URL used inside the JSON-LD graph.

    Matches the convention already used for ``facsimile`` (``pages/…``):
    everything in the graph is relative to ``<output_root>/<slug>/``.
    """
    return f"{REGIONS_DIR_NAME}/{region_crop_filename(blk)}"


def crop_region(cfg: VolumeConfig, page: dict, blk: dict, out_dir: Path) -> Optional[str]:
    """Crop ``blk['bbox']`` out of ``page``'s rendered PNG. Returns the
    saved filename (relative to ``out_dir``), or None if the bbox is
    degenerate or the crop failed."""
    bbox = blk.get("bbox") or [0, 0, 0, 0]
    if len(bbox) < 4:
        return None
    x1, y1, x2, y2 = (int(round(v)) for v in bbox[:4])
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None

    src_path = cfg.pages_dir / page["image_filename"]
    fname = region_crop_filename(blk)
    out_path = out_dir / fname
    if out_path.exists():
        return fname  # resume-friendly

    try:
        with Image.open(src_path) as im:
            W, H = im.size
            x1c = max(0, min(W, x1)); x2c = max(0, min(W, x2))
            y1c = max(0, min(H, y1)); y2c = max(0, min(H, y2))
            if x2c - x1c < 4 or y2c - y1c < 4:
                return None
            crop = im.crop((x1c, y1c, x2c, y2c))
            crop.save(out_path, format="PNG", optimize=True)
    except Exception as e:
        print(f"  crop failed for {blk['id']} on page {page['page_num']}: {e}")
        return None
    return fname


def make_region_crops(cfg: VolumeConfig, unified: list) -> int:
    """Crop every visual block to ``cfg.regions_dir``.

    Annotates each cropped block with two attributes that downstream
    emitters use:

    * ``blk["_crop"]``      — the bare filename, e.g. ``"p12_b005.png"``.
      This is what the HTML renderers consume.
    * ``blk["_crop_url"]``  — the volume-root-relative URL,
      e.g. ``"regions/p12_b005.png"``. This is what the JSON-LD graph
      consumes as ``contentUrl``.

    Returns the number of crops written or skipped (i.e. blocks for which
    a crop file exists at the canonical location). When ``cfg.crop_regions``
    is False, returns 0 and leaves the blocks unannotated.
    """
    if not cfg.crop_regions:
        return 0
    out_dir = cfg.regions_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in unified:
        for blk in p["blocks"]:
            if blk["type"] not in VISUAL_BLOCK_TYPES:
                continue
            fname = crop_region(cfg, p, blk, out_dir)
            if fname:
                blk["_crop"] = fname
                blk["_crop_url"] = f"{REGIONS_DIR_NAME}/{fname}"
                n += 1
    return n
