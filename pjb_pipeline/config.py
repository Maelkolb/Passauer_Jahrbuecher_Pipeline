"""Volume configuration.

A volume is described by a YAML file (see ``configs/pjb-048-2006.yaml``
for the canonical example). The file is loaded into a :class:`VolumeConfig`
dataclass which is then passed to every stage of the pipeline.

Why a dataclass and not just a dict: every consumer of the config gets
type checking, autocomplete, and a single place to add defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Tuple

import yaml


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

@dataclass
class OCRBackend:
    """How to talk to Chandra.

    Two methods are supported:

    * ``hf``    — load the model weights locally (the Colab path). Slow first
                  call because of the model download; cached afterwards.
    * ``vllm``  — talk to a running vLLM server over its OpenAI-compatible
                  HTTP API. Set ``vllm_url`` to the server's base URL
                  (e.g. ``http://10.0.0.42:8000/v1``).

    The two backends are wire-compatible from the pipeline's point of view:
    both return Chandra's structured output with raw layout + markdown.
    """

    method: str = "hf"                  # "hf" or "vllm"
    vllm_url: Optional[str] = None      # only when method == "vllm"
    model: str = "datalab-to/chandra"   # HF repo id; vLLM needs the model name too
    batch_size: int = 1                 # number of pages per inference call


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

@dataclass
class VolumeConfig:
    """One volume's worth of configuration."""

    # --- Source ----------------------------------------------------------
    pdf_path: str                         # absolute or relative path to the PDF
    volume_number: int                    # 48
    volume_number_roman: str              # "XLVIII"
    volume_year: int                      # 2006
    volume_title: str = "Passauer Jahrbuch"
    volume_subtitle: str = (
        "Beiträge zur Geschichte und Kultur Ostbaierns"
    )
    publisher: str = "Verein für Ostbairische Heimatforschung"
    editor: str = (
        "Institut für Kulturraumforschung Ostbaierns und der Nachbarregionen"
    )
    language: str = "de"                  # ISO 639-1 / 639-3
    series_name: str = "Passauer Jahrbücher"  # appears on every output

    # --- Slug + IDs -------------------------------------------------------
    # The slug is used as the volume's stable identifier in filenames,
    # TEI xml:id attributes, and knowledge-graph node IDs. Keep it short
    # and URL-safe — it's part of every downstream identifier.
    slug: Optional[str] = None            # auto-derived if absent

    # --- Output -----------------------------------------------------------
    output_root: str = "./output"

    # --- Rendering --------------------------------------------------------
    render_dpi: int = 200                 # 200 = balanced; 300 = sharper
    # Pages to process, 1-indexed inclusive. ``null`` = whole book.
    page_range: Optional[Tuple[int, int]] = None

    # --- Page-number offset ----------------------------------------------
    # The PDF's *physical* page N rarely equals the *printed* page N: there
    # are unnumbered cover sheets, frontmatter pages, blank versos. We need
    # this to map TOC page numbers ("AUFSÄTZE Wolff/Wandling ... 9") to PDF
    # pages. If you don't know yet, leave at None and the pipeline will try
    # to infer it from page-header/footer text.
    printed_page_offset: Optional[int] = None

    # --- OCR --------------------------------------------------------------
    ocr: OCRBackend = field(default_factory=OCRBackend)

    # --- Structure detection ---------------------------------------------
    # Section labels (uppercase headers) used by Passauer Jahrbücher to
    # group the TOC. Extend per volume if a new heading appears.
    toc_section_labels: tuple = (
        "AUFSÄTZE",
        "BERICHTE",
        "REZENSIONEN",
        "BUCHBESPRECHUNGEN",
        "BIBLIOGRAPHIE",
        "NACHRUFE",
        "VEREINSCHRONIK",
        "MITARBEITER",
    )

    # --- Misc -------------------------------------------------------------
    # Set False to skip the (expensive) per-page region cropping step.
    crop_regions: bool = True

    # ---- derived properties --------------------------------------------
    def __post_init__(self):
        if not self.slug:
            self.slug = f"pjb-{self.volume_number:03d}-{self.volume_year}"

    # ---- IO -------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path) -> "VolumeConfig":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        # Allow ${VAR} expansion for paths so configs can reference
        # ${DRIVE_ROOT}, etc. — handy when the same config is used on a
        # laptop and on a server with different mount points.
        def expand(v):
            if isinstance(v, str):
                return os.path.expandvars(os.path.expanduser(v))
            return v

        # OCR sub-section
        ocr_data = data.pop("ocr", {}) or {}
        ocr = OCRBackend(**ocr_data) if ocr_data else OCRBackend()

        # page_range may be a list in YAML; coerce to tuple
        if "page_range" in data and data["page_range"] is not None:
            pr = data["page_range"]
            data["page_range"] = (int(pr[0]), int(pr[1]))

        # toc_section_labels may be a list; coerce to tuple
        if "toc_section_labels" in data and data["toc_section_labels"] is not None:
            data["toc_section_labels"] = tuple(data["toc_section_labels"])

        # Expand path-like fields
        for key in ("pdf_path", "output_root"):
            if key in data:
                data[key] = expand(data[key])

        return cls(ocr=ocr, **data)

    def as_dict(self) -> dict:
        d = asdict(self)
        return d

    # ---- output paths --------------------------------------------------
    @property
    def out_dir(self) -> Path:
        return Path(self.output_root) / self.slug

    @property
    def pages_dir(self) -> Path:
        return self.out_dir / "pages"

    @property
    def interim_dir(self) -> Path:
        return self.out_dir / "interim"

    @property
    def pagexml_dir(self) -> Path:
        return self.out_dir / "pagexml"

    @property
    def tei_dir(self) -> Path:
        return self.out_dir / "tei"

    @property
    def html_dir(self) -> Path:
        return self.out_dir / "html"

    @property
    def graph_dir(self) -> Path:
        return self.out_dir / "graph"

    @property
    def logs_dir(self) -> Path:
        return self.out_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (
            self.out_dir, self.pages_dir, self.interim_dir,
            self.pagexml_dir, self.tei_dir, self.html_dir,
            self.graph_dir, self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
