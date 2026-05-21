"""Crop visual-region bboxes out of page PNGs for inline display.

The HTML page-facsimile and article-reading views embed the actual cropped
image of each figure/image/diagram block. This module owns the cropping
step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

from ...config import VolumeConfig


REGIONS_DIR_NAME = "regions"
VISUAL_BLOCK_TYPES = {"figure", "image", "diagram"}


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
    fname = f"{blk['id']}.png"
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
    """Crop every visual block. Stores the resulting filename on
    ``blk["_crop"]`` so renderers can pick it up. Returns the count."""
    if not cfg.crop_regions:
        return 0
    out_dir = cfg.html_dir / REGIONS_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in unified:
        for blk in p["blocks"]:
            if blk["type"] not in VISUAL_BLOCK_TYPES:
                continue
            fname = crop_region(cfg, p, blk, out_dir)
            if fname:
                blk["_crop"] = fname
                n += 1
    return n
