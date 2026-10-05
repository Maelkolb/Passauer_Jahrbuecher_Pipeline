# Passauer Jahrbücher · Pipeline

A modular pipeline that ingests one scanned volume of *Passauer Jahrbücher*
(*Ostbairische Grenzmarken*) — either the whole book as one PDF or a folder of
individually scanned page images — runs **Chandra 2** layout-aware OCR,
reconstructs page and article structure, and emits five artefacts:

| Artefact   | Format        | Purpose                                                 |
| ---------- | ------------- | ------------------------------------------------------- |
| Page PNGs  | `pages/`      | High-DPI rendered scans                                 |
| Regions    | `regions/`    | Cropped figure/image/diagram PNGs (one per visual block) |
| PageXML    | `pagexml/`    | PRImA 2019 schema, one file per page, region polygons   |
| TEI-XML    | `tei/`        | One file per volume, articles + facsimile + footnotes   |
| HTML       | `html/`       | Static site: cover, TOC, article view, facsimile view   |
| **JSON-LD**| `graph/`      | **Knowledge-graph fragment for the 55-volume corpus**   |
| **Wiki**   | `wiki/`       | **Markdown source for the LLM-Wiki (one file per article / person / volume)** |



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

Edit `configs/pjb-048-2006.yaml` (source, page range, OCR backend), then:

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
downstream stages — the OCR server is not even contacted when every page is
cached, and pages already rendered from the same source are reused. Handy
when you're iterating on the structure detection or the emitters.

### Source: book PDF or page images

`source_path` (the older key `pdf_path` still works) points at one of

* **a book PDF** — rendered at `render_dpi` (200 by default);
* **a folder of page images** — JPEG, PNG, TIFF (multi-page TIFFs give one
  page per frame), WebP or BMP. The natural sort order of the file names is
  the page order (`page_2.jpg` before `page_10.jpg`). Images are rotated
  according to their EXIF orientation and scaled so that the longer side is at
  most `image_max_side` pixels (default 2200, the size of a 200 dpi render;
  `null` keeps full resolution);
* **a scanner output folder** — the pipeline looks inside it for the page
  images or the single PDF (e.g. `…_finished/pdf/…pdf` or `…_finished/jpg/`).

```yaml
source_path: "/home/tobias/2026-11-02_10-00_LXVII_2025_finished"   # folder or PDF
image_max_side: 2200
```

`pjb-pipeline run config.yaml --source /path/to/scans` overrides it.

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

## How structure is recovered

**Layout analysis** (`structure/layout.py`, run inside the normalise stage):

* *Print size* — character density per block (≈ 1 / font-size²) separates
  body text from footnote-size text in every volume. The two clusters are
  calibrated per volume. Small-print "text" blocks at the foot of a column that
  carry footnote evidence (a leading note number, an adjacent footnote, citation
  vocabulary like "wie Anm.", "Vgl.") become footnotes; whole pages of notes
  (endnotes) and footnotes running over a page are recognised too, while
  bibliography and register pages are left alone. "Abb. 3: …" text next to a
  figure becomes a caption.
* *Reading order* — several candidate orders are built (recursive XY-cut with
  columns first, XY-cut with rows first, the earlier column-band heuristic,
  Chandra's own order) and scored by text continuity (a block ending in
  "verlie-" must be followed by "henen…"; a lower-case block cannot follow a
  finished sentence), geometric plausibility and agreement with Chandra. The
  previous page's last block takes part, so the join across the page break
  counts as well. Footnotes are ordered after the body.

**Articles at block level** (`structure/articles.py`): every TOC entry is
located on its start page — title heading, author byline above or below it, or
for a book review its citation line — and pages shared by several articles
(the end of one and the start of the next, several reviews on one page) are
split at that block. Footnotes on a shared page go to the article whose text
cites them. Printed page numbers are mapped to PDF pages with the *local*
offset from the page-number stamps, so plates inserted in the middle of a
volume do not shift the articles after them.

**Authors** (`structure/names.py`): TOC entries are split into individual
persons — "A, B und C: Titel", "A/B, Titel", "A, B (Hg.), Titel",
"Franziska, Karl und Georg R. Rettenbacher" — with a given-name lexicon
(`data/first_names.txt`) telling names from capitalised title words. The
section headings of the TOC page are read even when Chandra puts them outside
the TOC block, and in book-review sections the *reviewer* (the name in brackets
after the citation, or before the colon in newer volumes) is the article's
author, while the reviewed book's authors and editors are kept separately
(`itemReviewed` in the graph). Surname-only reviewers of older volumes
("(Heydenreuter)") are completed from the volume's contributor list
("MITARBEITER"). The byline on the article's first page confirms and completes
the author list.

Check a processed volume with

```bash
python3 scripts/check_reading_order.py output/pjb-056-2014
```

It reports the calibrated print sizes, re-labelled blocks, pages whose order
differs from Chandra's, and the two error signals that matter: broken
hyphenation joins and stranded continuations (a handful per volume is normal —
compound words like "Böhmerwald-Liedes", poems, OCR omissions).

---

## Codebase layout

```
pjb_pipeline/
├── config.py            ← VolumeConfig + YAML loader
├── stage.py             ← timing context manager
├── render.py            ← Stage 1: book PDF or page-image folder → page PNGs
├── ocr.py               ← Stage 2: Chandra (HF or vLLM)
├── normalize.py         ← Stage 3: canonical block model + layout analysis
├── structure/
│   ├── layout.py        ← print-size roles (footnotes!), reading order
│   ├── columns.py       ← column bands (HTML facsimile layout, order candidate)
│   ├── toc.py           ← parse TOC into (section, authors, title, page)
│   ├── names.py         ← author/reviewer/editor parsing, bylines, contributors
│   ├── articles.py      ← TOC-driven, block-level article boundaries
│   └── footnotes.py     ← detect refs in body, link to notes
├── data/first_names.txt ← given-name lexicon for author detection
├── emit/
│   ├── jsonld_context.py← shared JSON-LD @context (graph + wiki share this)
│   ├── pagexml.py       ← Stage 5: PRImA PageXML
│   ├── tei.py           ← Stage 6: TEI per volume
│   ├── graph.py         ← Stage 7: JSON-LD knowledge graph
│   ├── wiki.py          ← Stage 8: per-volume LLM-Wiki markdown
│   └── html/            ← Stage 9: static HTML edition
│       ├── chrome.py
│       ├── crops.py
│       └── renderers.py
├── wiki_assembler.py    ← init-wiki / add-volume (corpus-wide wiki ops)
├── wiki_templates/      ← CLAUDE.md, README.md for the wiki repo
├── pipeline.py          ← top-level orchestrator
└── cli.py               ← `pjb-pipeline run | merge-graphs | init-wiki | add-volume | rebuild-wiki`

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
| `ScholarlyArticle`  | `pjb:art/<slug>-art<NN>`                              | One per detected article; carries `hasPart` → its pages; a book review is also a `Review` whose `itemReviewed` names the book and its authors/editors |
| `WebPage`           | `pjb:vol/<slug>/page/<NNNN>`                          | One per processed page; `inArticle` (or `inVolume` for frontmatter); `hasPart` → its figures |
| `ImageObject`       | `pjb:vol/<slug>/page/<NNNN>/figure/<block-id>`        | One per figure/image/diagram region; `inPage` → its page; `contentUrl` → `regions/<block-id>.png` |
| `Person`            | `pjb:person/<slugified-name>`                         | **Stable across volumes**: same author → same IRI. One node per person, also for co-authors, editors and reviewed authors |
| `Comment`           | `pjb:art/<art-id>/fn/<n>`                             | Footnotes; `footnoteOf` → article, `inPage` → page          |

So the graph forms the chain **Series → Volume → Article → Page → {Figure, Footnote}** with bidirectional links (`hasPart` / `isPartOf`-style predicates) at each level.

To get the corpus-wide graph, run after all volumes have been processed:

```bash
pjb-pipeline merge-graphs corpus.jsonld output/*/graph/*.jsonld
```

This deduplicates nodes by IRI. Cross-volume authors land in a single
`Person` node with no extra work.

The output is a valid JSON-LD document loadable into Apache Jena, rdflib,
GraphDB, neosemantics, or anything else that speaks RDF.

---

## Building the LLM-Wiki

Alongside the structural JSON-LD, the pipeline emits markdown for each
volume's articles, people, and volume page under
`output/<slug>/wiki/`. These per-volume directories are *staging input*
for a corpus-wide wiki — a single markdown tree, hosted in its own git
repo, that is the human-readable face of the knowledge graph. Every page
has YAML frontmatter that *is* the JSON-LD node for that entity, so the
wiki and the graph round-trip cleanly.

The pattern follows Karpathy's
[llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
the pipeline writes structural pages, an LLM agent (e.g. Claude Code)
maintains the content — summaries, cross-references, thematic linking —
against the wiki directory.

### Workflow

The wiki lives in a **separate git repo** that you own. Bootstrap it
once:

```bash
pjb-pipeline init-wiki ../Passauer_Jahrbuecher_Wiki
cd ../Passauer_Jahrbuecher_Wiki && git init
```

After processing each volume, fold its output into the wiki:

```bash
pjb-pipeline run configs/pjb-049-2007.yaml
pjb-pipeline add-volume ../Passauer_Jahrbuecher_Wiki output/pjb-049-2007/
cd ../Passauer_Jahrbuecher_Wiki
git diff                                       # review what changed
git add . && git commit -m "Add vol. XLIX (2007)"
```

`add-volume` is **idempotent** — re-running with the same volume produces
a no-op diff. Re-adding a *re-processed* volume replaces that volume's pages
and graph nodes: article pages and person entries it no longer produces are
removed. After a pipeline change that affects every volume, regenerate the
whole wiki in one go:

```bash
pjb-pipeline rebuild-wiki ../Passauer_Jahrbuecher_Wiki output/pjb-*/
``` It also **preserves agent-authored content** across re-runs:
`## Summary`, `## Mentions`, and `## Notes` sections you (or an LLM) wrote
on previous visits stay put; only the structural parts of pages
(frontmatter, `## Full Text`, `## Footnotes`, `## Appears in`) are
regenerated. Person pages are merged across volumes so the same author
appearing in five volumes has one page listing five appearances.

The wiki repo itself ships an opinionated `CLAUDE.md` (the operating
schema for the LLM agent) explaining the directory layout, the
frontmatter contract, the agent-owned vs. pipeline-owned sections, and
the ingest / query / lint operations. Open the wiki directory in Claude
Code and the rest is conversation.

---

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```


## License

MIT. See `LICENSE`. Note that the Chandra model weights have their own
license — see <https://huggingface.co/datalab-to/chandra> for terms.
