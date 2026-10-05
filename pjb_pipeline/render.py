"""Stage 1: turn the scanned source into per-page PNG images.

Two kinds of source are supported (see ``VolumeConfig.source_path``):

* **a book PDF** — PyMuPDF rasterises each page at ``cfg.render_dpi``;
* **a folder of page images** — individually scanned pages (JPEG, PNG,
  TIFF incl. multi-page TIFF, WebP, BMP). Natural sort order of the file
  names is page order ("page_2" before "page_10"). Each image is
  EXIF-rotated, converted to RGB and scaled down to ``cfg.image_max_side``
  so OCR sees the same resolution as for a 200 dpi PDF render.

A scanner output folder (``…_finished/`` with a ``pdf/`` or ``jpg/``
subfolder) can be given directly: :func:`resolve_source` finds the images
or the single PDF inside it.

Either way the result is ``pages/page_NNNN.png`` plus a page index, and
page numbering is 1-based and inclusive, as everywhere in the pipeline.
Pages that were already rendered from the same source file are reused, so
re-running a volume does not re-render it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from tqdm.auto import tqdm

from .config import VolumeConfig

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".jp2"}


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------

def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def _images_in(folder: Path) -> List[Path]:
    return sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
         and not p.name.startswith(".")),
        key=_natural_key,
    )


def resolve_source(path) -> Tuple[str, Path, List[Path]]:
    """Classify the configured source.

    Returns ``(kind, path, images)`` with ``kind`` ``"pdf"`` (``path`` is
    the PDF) or ``"images"`` (``path`` is the folder, ``images`` the page
    files in page order).

    A directory is searched in this order: page images directly inside
    it; a single PDF inside it or one level down (the scanner's ``pdf/``
    folder); a single subfolder that holds the page images.
    """
    if not path:
        raise FileNotFoundError("No source configured: set source_path (or pdf_path).")
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {path}")
    if path.is_file():
        if path.suffix.lower() == ".pdf":
            return "pdf", path, []
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return "images", path.parent, [path]
        raise ValueError(f"Unsupported source file type: {path}")

    images = _images_in(path)
    if images:
        return "images", path, images
    pdfs = sorted(path.glob("*.pdf")) + sorted(path.glob("*/*.pdf"))
    if len(pdfs) == 1:
        return "pdf", pdfs[0], []
    subdirs = [d for d in sorted(path.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    with_images = [d for d in subdirs if _images_in(d)]
    if len(with_images) == 1 and not pdfs:
        return "images", with_images[0], _images_in(with_images[0])
    if len(pdfs) > 1:
        raise ValueError(f"{path} contains several PDFs; point source_path at one of them: "
                         + ", ".join(str(p) for p in pdfs[:5]))
    if len(with_images) > 1:
        raise ValueError(f"{path} has several image folders; point source_path at one of them: "
                         + ", ".join(str(d) for d in with_images[:5]))
    raise FileNotFoundError(f"No PDF and no page images found in {path}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _reusable(out_path: Path, old: Optional[dict], signature: dict) -> Optional[dict]:
    """The previous page record, if the PNG on disk came from the same source.

    Records written before source signatures existed (pipeline ≤ 0.3) are
    trusted when their PNG is still there, so existing volumes are not
    re-rendered after the upgrade; they get the signature added.
    """
    if not old or not out_path.exists():
        return None
    here = {"image_path": str(out_path), "image_filename": out_path.name}
    if "source" not in old and old.get("width") and old.get("height"):
        return {**old, **signature, **here}
    if all(old.get(k) == v for k, v in signature.items()):
        return {**old, **here}
    return None


def _load_previous_index(index_path: Path) -> dict:
    try:
        return {r["page_num"]: r for r in json.loads(index_path.read_text())}
    except Exception:
        return {}


def render_pdf(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    page_range: Optional[Tuple[int, int]] = None,
    previous: Optional[dict] = None,
) -> List[dict]:
    """Render ``pdf_path`` to ``out_dir``. Returns a list of page records.

    Each record is::

        {"page_num": int,
         "image_path": str,
         "image_filename": str,
         "width": int,
         "height": int,
         "source": str, …}

    where ``page_num`` is the 1-based PDF page index (same as everywhere
    else in the pipeline).
    """
    import fitz  # PyMuPDF — imported lazily so image folders don't need it

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    previous = previous or {}
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

    stat = pdf_path.stat()
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    records: List[dict] = []
    reused = 0
    for idx in tqdm(range(first, last + 1), desc="render", unit="pg"):
        out_path = out_dir / f"page_{idx:04d}.png"
        signature = {"source": pdf_path.name, "source_size": stat.st_size,
                     "source_page": idx, "dpi": dpi}
        old = _reusable(out_path, previous.get(idx), signature)
        if old:
            records.append(old)
            reused += 1
            continue
        page = doc[idx - 1]  # PyMuPDF is 0-indexed
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(str(out_path))
        records.append({
            "page_num":       idx,
            "image_path":     str(out_path),
            "image_filename": out_path.name,
            "width":          pix.width,
            "height":         pix.height,
            **signature,
        })
    doc.close()
    if reused:
        print(f"   reused {reused} already rendered pages")
    return records


def _iter_image_pages(files: List[Path]) -> Iterator[Tuple[Path, int, object]]:
    """Yield ``(file, frame_index, PIL.Image)`` for every page — a
    multi-page TIFF contributes one page per frame."""
    from PIL import Image
    for f in files:
        with Image.open(f) as im:
            n_frames = getattr(im, "n_frames", 1) if f.suffix.lower() in (".tif", ".tiff") else 1
            for k in range(n_frames):
                if n_frames > 1:
                    im.seek(k)
                yield f, k, im.copy()


def _normalise_image(im, max_side: Optional[int]):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(im)
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.point(lambda v: v * (1 / 256)).convert("L")
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if im.mode == "L":
        im = im.convert("RGB")
    if max_side and max(im.size) > max_side:
        scale = max_side / max(im.size)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return im


def render_images(
    files: List[Path],
    out_dir: Path,
    max_side: Optional[int],
    page_range: Optional[Tuple[int, int]] = None,
    previous: Optional[dict] = None,
) -> List[dict]:
    """Normalise a list of page images into ``out_dir/page_NNNN.png``."""
    previous = previous or {}
    # Count pages first (multi-page TIFFs) so page_range works on pages.
    from PIL import Image
    pages: List[Tuple[Path, int]] = []
    for f in files:
        n_frames = 1
        if f.suffix.lower() in (".tif", ".tiff"):
            with Image.open(f) as im:
                n_frames = getattr(im, "n_frames", 1)
        pages.extend((f, k) for k in range(n_frames))
    n_total = len(pages)
    first, last = (1, n_total) if page_range is None else (page_range[0], min(page_range[1], n_total))
    print(f"Image folder has {len(files)} files / {n_total} pages; "
          f"using pages {first}-{last} ({last - first + 1} pages)"
          + (f", longer side ≤ {max_side} px" if max_side else ""))

    records: List[dict] = []
    reused = 0
    for idx in tqdm(range(first, last + 1), desc="images", unit="pg"):
        f, frame = pages[idx - 1]
        out_path = out_dir / f"page_{idx:04d}.png"
        st = f.stat()
        signature = {"source": f.name, "source_size": st.st_size,
                     "source_frame": frame, "max_side": max_side}
        old = _reusable(out_path, previous.get(idx), signature)
        if old:
            records.append(old)
            reused += 1
            continue
        with Image.open(f) as im:
            if frame:
                im.seek(frame)
            img = _normalise_image(im.copy(), max_side)
        img.save(out_path, optimize=False)
        records.append({
            "page_num":       idx,
            "image_path":     str(out_path),
            "image_filename": out_path.name,
            "width":          img.width,
            "height":         img.height,
            **signature,
        })
    if reused:
        print(f"   reused {reused} already prepared pages")
    return records


def run(cfg: VolumeConfig) -> List[dict]:
    """Stage entry point. Renders/prepares pages and persists the page index."""
    cfg.ensure_dirs()
    index_path = cfg.logs_dir / "pages_index.json"
    previous = _load_previous_index(index_path)
    kind, path, images = resolve_source(cfg.source)
    if kind == "pdf":
        pages = render_pdf(path, cfg.pages_dir, cfg.render_dpi, cfg.page_range, previous)
    else:
        pages = render_images(images, cfg.pages_dir, cfg.image_max_side, cfg.page_range, previous)
    print(f"   {len(pages)} pages saved to {cfg.pages_dir}  (source: {kind} {path})")
    index_path.write_text(json.dumps(pages, indent=2))
    return pages
