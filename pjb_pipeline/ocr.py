"""Chandra 2 OCR + layout inference.

Two backends are supported, selected by ``cfg.ocr.method``:

* **hf** — load Chandra into local GPU memory. The Colab path.
* **vllm** — talk to a vLLM server. Chandra discovers the server through
  the ``VLLM_API_BASE`` and ``VLLM_MODEL_NAME`` environment variables, so
  we set those *before* importing the chandra runtime when ``cfg.ocr.vllm_url``
  is set. If you launched vLLM with ``chandra_vllm`` on the same host,
  defaults will work and you can leave ``vllm_url`` blank.

The stage is **resumable**: the per-page interim JSON contains the raw
Chandra output, and a second invocation will re-parse the cached layout
rather than re-run inference. Useful when iterating on the *downstream*
stages without paying the OCR cost again.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

from PIL import Image
from tqdm.auto import tqdm

from .config import VolumeConfig


# ---------------------------------------------------------------------------
# Lazy backend loader
# ---------------------------------------------------------------------------

def _load_backend(cfg: VolumeConfig):
    """Configure env vars then import + construct the Chandra manager.

    Returns ``(manager, parse_layout, BatchInputItem)``. ``parse_layout``
    may be ``None`` if the installed Chandra version doesn't expose it
    (older builds) — the pipeline degrades gracefully to markdown-only.
    """
    if cfg.ocr.method == "vllm":
        if cfg.ocr.vllm_url:
            os.environ["VLLM_API_BASE"] = cfg.ocr.vllm_url
        if cfg.ocr.model:
            # If the user pinned a model name, propagate it. Chandra defaults
            # to "chandra" if unset.
            os.environ.setdefault("VLLM_MODEL_NAME", cfg.ocr.model.split("/")[-1])

    # Local import so the chandra dependency is only required when actually
    # running OCR — handy for downstream tests / format conversion.
    from chandra.model import InferenceManager
    from chandra.model.schema import BatchInputItem

    try:
        from chandra.output import parse_layout
    except ImportError:
        parse_layout = None
        print("Note: chandra.output.parse_layout unavailable; "
              "falling back to markdown-only output.")

    print(f"Loading Chandra model (method={cfg.ocr.method}"
          + (f", url={os.environ['VLLM_API_BASE']}" if cfg.ocr.method == "vllm"
             and "VLLM_API_BASE" in os.environ else "")
          + ")…")
    t0 = time.time()
    manager = InferenceManager(method=cfg.ocr.method)
    print(f"Model ready in {time.time() - t0:.1f}s")

    return manager, parse_layout, BatchInputItem


# ---------------------------------------------------------------------------
# Per-page inference + layout parsing
# ---------------------------------------------------------------------------

def _coerce_btype(btype_raw) -> str:
    """Chandra/Surya sometimes return enum members (``BlockType.PICTURE``).
    ``str(enum)`` yields ``"BlockType.PICTURE"`` which is useless for
    downstream lookup. Prefer ``.value``, then ``.name``, then ``str()``."""
    return (getattr(btype_raw, "value", None)
            or getattr(btype_raw, "name", None)
            or str(btype_raw))


def _extract_blocks(layout_blocks, page_num: int) -> List[dict]:
    blocks: List[dict] = []
    for i, b in enumerate(layout_blocks):
        btype_raw = (getattr(b, "block_type", None) or getattr(b, "type", None)
                     or (b.get("type") if isinstance(b, dict) else None) or "text")
        btype = _coerce_btype(btype_raw)
        bbox = (getattr(b, "bbox", None)
                or (b.get("bbox") if isinstance(b, dict) else None) or [0, 0, 0, 0])
        text = (getattr(b, "text", None) or getattr(b, "content", None)
                or (b.get("text") if isinstance(b, dict) else None) or "")
        blocks.append({
            "id":   f"p{page_num}_b{i + 1:03d}",
            "type": str(btype).lower().replace("_", "-"),
            "bbox": [float(x) for x in bbox] if bbox else [0, 0, 0, 0],
            "text": str(text),
        })
    return blocks


def ocr_page(
    page_record: dict,
    manager,
    BatchInputItem,
    parse_layout,
    interim_dir: Path,
    *,
    force: bool = False,
) -> dict:
    """Run Chandra on one page. Resumable: if interim JSON already exists,
    re-parse the layout from cached raw output rather than re-running."""
    pn = page_record["page_num"]
    interim_path = interim_dir / f"page_{pn:04d}.json"
    img = Image.open(page_record["image_path"]).convert("RGB")

    cached_raw, cached_md, cached_elapsed = None, "", 0.0
    if interim_path.exists() and not force:
        try:
            cached = json.loads(interim_path.read_text())
            cached_raw = cached.get("raw") or None
            cached_md = cached.get("markdown", "")
            cached_elapsed = cached.get("ocr_seconds", 0.0)
        except Exception:
            cached_raw = None

    if cached_raw:
        raw, markdown, elapsed = cached_raw, cached_md, cached_elapsed
    else:
        item = BatchInputItem(image=img, prompt_type="ocr_layout")
        t0 = time.time()
        result = manager.generate([item])[0]
        elapsed = time.time() - t0
        raw = getattr(result, "raw", None) or getattr(result, "text", "") or ""
        markdown = getattr(result, "markdown", "") or ""

    blocks = []
    if parse_layout is not None and raw:
        try:
            layout_blocks = parse_layout(raw, img)
        except TypeError:
            try:
                layout_blocks = parse_layout(raw, image=img)
            except Exception as e:
                layout_blocks = []
                print(f"  parse_layout failed on page {pn}: {e}")
        except Exception as e:
            layout_blocks = []
            print(f"  parse_layout failed on page {pn}: {e}")
        blocks = _extract_blocks(layout_blocks, pn)

    return {
        "page_num":       pn,
        "image_filename": page_record["image_filename"],
        "image_width":    page_record["width"],
        "image_height":   page_record["height"],
        "ocr_seconds":    elapsed,
        "markdown":       markdown,
        "raw":            raw,
        "blocks":         blocks,
        "_resumed":       cached_raw is not None,
    }


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(cfg: VolumeConfig, pages: List[dict], timings: dict) -> None:
    """Run OCR over every rendered page. Writes interim JSON per page to
    ``cfg.interim_dir``. Records average OCR time in ``timings``."""
    manager, parse_layout, BatchInputItem = _load_backend(cfg)

    per_page_times: List[float] = []
    n_resumed = 0
    n_inferred = 0
    for rec in tqdm(pages, desc="ocr", unit="pg"):
        result = ocr_page(rec, manager, BatchInputItem, parse_layout, cfg.interim_dir)
        if result["_resumed"]:
            n_resumed += 1
        else:
            n_inferred += 1
            per_page_times.append(result["ocr_seconds"])
        (cfg.interim_dir / f"page_{rec['page_num']:04d}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
    print(f"   inferred: {n_inferred} pages, resumed from cache: {n_resumed} pages")
    if per_page_times:
        avg = sum(per_page_times) / len(per_page_times)
        print(f"   avg {avg:.1f} s/page  (min {min(per_page_times):.1f}, "
              f"max {max(per_page_times):.1f})")
        timings["_per_page_ocr_avg_s"] = avg
