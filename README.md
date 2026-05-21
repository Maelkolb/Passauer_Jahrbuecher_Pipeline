# Passauer Jahrbücher · Pipeline

A modular pipeline that ingests one scanned PDF volume of *Passauer Jahrbücher*
(*Ostbairische Grenzmarken*), runs **Chandra 2** layout-aware OCR, reconstructs
page and article structure, and emits five artefacts:

| Artefact   | Format        | Purpose                                                 |
| ---------- | ------------- | ------------------------------------------------------- |
| Page PNGs  | `pages/`      | High-DPI rendered scans                                 |
| PageXML    | `pagexml/`    | PRImA 2019 schema, one file per page, region polygons   |
| TEI-XML    | `tei/`        | One file per volume, articles + facsimile + footnotes   |
| HTML       | `html/`       | Static site: cover, TOC, article view, facsimile view   |
| **JSON-LD**| `graph/`      | **Knowledge-graph fragment for the 50-volume corpus**   |

End goal: process all ~50 volumes with the same pipeline and merge their
JSON-LD graphs into a single corpus-wide knowledge graph where articles,
authors, sections, and volumes are nodes you can query.

---

## What changed from the notebook

The previous notebook did a great job of the OCR → HTML edition path but
the structure detection was acknowledged as "intentionally crude" and the
codebase wasn't easy to extend toward the knowledge graph. This refactor:

1. **Modularised into a real Python package** (`pjb_pipeline/`) with a CLI.
   No more "edit cell 1 and re-run all".
2. **TOC-driven article detection** — the OCR already finds the
   table-of-contents block on every volume; we now parse it into
   structured `(section, author, title, page)` entries and use those as
   ground truth for article boundaries, instead of guessing from
   section-header positions.
3. **Footnote-reference linking** — references inside body text
   (`1^`, Unicode superscripts) are detected and turned into HTML anchor
   links pointing to the resolved footnote element.
4. **JSON-LD knowledge graph** — each volume now emits a
   schema.org-flavoured graph fragment with stable IRIs for Series,
   Volume, Section, Article, Page, Person and Footnote. The same author
   appearing in multiple volumes shares an IRI, so a downstream merge
   step deduplicates trivially.
5. **vLLM-server backend** — the OCR module now reads
   `ocr.method: vllm` and `ocr.vllm_url` from config so the same code
   runs on Colab (local HF) and on your GPU host (remote vLLM).
6. **CSS/JS lifted out of Python strings** — they live in `assets/` and
   are copied into each volume's output. Edit one file, every edition
   updates.
7. **Per-volume YAML config** instead of editing cells — copy
   `configs/pjb-048-2006.yaml`, change four fields, run.

---

## Install

```bash
git clone https://github.com/Maelkolb/Passauer_Jahrbuecher_Pipeline.git
cd Passauer_Jahrbuecher_Pipeline
pip install -e ".[hf]"          # for the Colab / local-GPU path
# OR
pip install -e ".[vllm]"        # for the vLLM-server path (smaller install)
```

`pip install -e .` puts an editable install in your env and registers a
`pjb-pipeline` console script.

---

## Run a single volume

Edit `configs/pjb-048-2006.yaml` (paths, page range, OCR backend), then:

```bash
pjb-pipeline run configs/pjb-048-2006.yaml
```

Useful overrides (all of them shadow the matching YAML key):

```bash
# small page range for testing
pjb-pipeline run configs/pjb-048-2006.yaml --page-range 3-20

# point at a remote vLLM server
pjb-pipeline run configs/pjb-048-2006.yaml \
    --ocr-method vllm --vllm-url http://your-gpu-box:8000/v1

# different output root
pjb-pipeline run configs/pjb-048-2006.yaml --output-root ~/pjb-out
```

When OCR completes, the per-page interim JSON is cached, so re-running the
pipeline with the same config will skip inference and only re-run the
downstream stages. Handy when you're iterating on the HTML or TEI emitters.

---

## Run on Colab

The notebook in `notebooks/colab_quickstart.ipynb` is a 30-line shim that
clones the repo, mounts Drive, installs the `[hf]` extra, and calls the
pipeline.

```python
# inside the notebook, after upload + extraction:
!git clone https://github.com/Maelkolb/Passauer_Jahrbuecher_Pipeline.git
%cd Passauer_Jahrbuecher_Pipeline
!pip install -q -e ".[hf]"

from pjb_pipeline.config import VolumeConfig
from pjb_pipeline.pipeline import run

cfg = VolumeConfig.from_yaml("configs/pjb-048-2006.yaml")
cfg.pdf_path = "/content/drive/MyDrive/.../volume.pdf"
cfg.output_root = "/content/output"
run(cfg)
```

See the notebook for the working version including Drive mount.

---

## Run on your GPU host (vLLM server)

You said you already have vLLM serving Chandra. Two ways to use it from
this codebase:

### Option A — run the pipeline on the GPU host

```bash
# on the GPU host:
git clone https://github.com/Maelkolb/Passauer_Jahrbuecher_Pipeline.git
cd Passauer_Jahrbuecher_Pipeline
pip install -e ".[vllm]"

# point at your already-running vLLM server in the config:
#   ocr:
#     method: vllm
#     vllm_url: "http://localhost:8000/v1"

pjb-pipeline run configs/pjb-048-2006.yaml
```

The pipeline talks to vLLM over HTTP, so no model is loaded into the
pipeline process — the OCR call is just a request. The whole pipeline
fits comfortably on a CPU-only box for everything except OCR.

### Option B — run the pipeline elsewhere, hit vLLM remotely

If you want to keep the pipeline on a laptop and only the OCR on the GPU
host, expose vLLM on a reachable port (or tunnel `ssh -L 8000:localhost:8000`),
and set:

```yaml
ocr:
  method: vllm
  vllm_url: "http://gpu-host:8000/v1"   # or http://localhost:8000/v1 over a tunnel
```

The `--vllm-url` CLI flag overrides this so the same config works from
either side.

---

## Codebase layout

```
pjb_pipeline/
├── config.py            ← VolumeConfig + YAML loader
├── stage.py             ← timing context manager
├── render.py            ← Stage 1: PDF → page PNGs
├── ocr.py               ← Stage 2: Chandra (HF or vLLM)
├── normalize.py         ← Stage 3: canonical block model
├── structure/
│   ├── toc.py           ← parse TOC into (section, author, title, page)
│   ├── articles.py      ← TOC-driven boundary detection + heuristic fallback
│   └── footnotes.py     ← detect refs in body, link to notes
├── emit/
│   ├── pagexml.py       ← Stage 5: PRImA PageXML
│   ├── tei.py           ← Stage 6: TEI per volume
│   ├── graph.py         ← Stage 7: JSON-LD knowledge graph
│   └── html/            ← Stage 8: static HTML edition
│       ├── chrome.py
│       ├── crops.py
│       └── renderers.py
├── pipeline.py          ← top-level orchestrator
└── cli.py               ← `pjb-pipeline run …` entry point

assets/                  ← canonical CSS + JS, copied into every output bundle
configs/                 ← one YAML per volume
scripts/                 ← merge graphs, batch helpers
tests/                   ← unit tests for the structural parsers
notebooks/               ← thin Colab launcher
```

Every stage has a `run(cfg, …)` function; the orchestrator in
`pipeline.py` just calls them in order and times each one.

---

## Building toward the knowledge graph

Each volume produces `output/<slug>/graph/<slug>.jsonld`. The graph
schema (in JSON-LD, schema.org-flavoured):

| Node type           | IRI shape                                             | Notes                                                       |
| ------------------- | ----------------------------------------------------- | ----------------------------------------------------------- |
| `PublicationSeries` | `pjb:series/passauer-jahrbuecher`                     | Same in every volume — merges trivially                     |
| `PublicationVolume` | `pjb:vol/pjb-048-2006`                                | One per volume                                              |
| `CreativeWorkSeason`| `pjb:vol/<slug>/section/<section-slug>`               | One per TOC section (Aufsätze, Berichte, …)                 |
| `ScholarlyArticle`  | `pjb:art/<slug>-art<NN>`                              | One per detected article                                    |
| `WebPage`           | `pjb:vol/<slug>/page/<NNNN>`                          | One per processed page                                      |
| `Person`            | `pjb:person/<slugified-name>`                         | **Stable across volumes**: same author → same IRI           |
| `Comment`           | `pjb:art/<art-id>/fn/<n>`                             | Footnotes                                                   |

To get the corpus-wide graph, run after all volumes have been processed:

```bash
pjb-pipeline merge-graphs corpus.jsonld output/*/graph/*.jsonld
```

This deduplicates nodes by IRI. Cross-volume authors land in a single
`Person` node with no extra work.

The output is a valid JSON-LD document loadable into Apache Jena, rdflib,
GraphDB, neosemantics, or anything else that speaks RDF.

### What to add next

The current graph covers the *structural* facts. Natural next steps:

- **NER** to add `mentions` edges from articles to people/places/works.
  Spacy + a German model would be a good baseline; the OCR text is
  already cleaned and structured by article in `output/<slug>/logs/`.
- **Bibliography parsing** of the `bibliography` block type to turn
  citations into `CreativeWork` nodes the article `cites`.
- **Reading-order normalisation** for multi-column pages — Chandra
  returns blocks but their order isn't always natural reading order.
  This is a known gap; see `pjb_pipeline.structure.articles` for where
  to plug in a column-aware sorter.

---

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

The tests cover the structure parsers (TOC, footnotes, article
detection) on small handcrafted examples — they're cheap to run and the
most useful place to catch regressions.

---

## License

MIT. See `LICENSE`. Note that the Chandra model weights have their own
license — see <https://huggingface.co/datalab-to/chandra> for terms.
