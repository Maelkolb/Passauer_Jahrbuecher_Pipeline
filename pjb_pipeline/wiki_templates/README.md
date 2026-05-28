# Passauer Jahrbücher · LLM-Wiki

An LLM-maintained companion wiki to the digital edition of the
*Passauer Jahrbücher* (*Ostbairische Grenzmarken*), built incrementally
as the [Passauer_Jahrbuecher_Pipeline](https://github.com/Maelkolb/Passauer_Jahrbuecher_Pipeline)
processes each of the 55 volumes through Chandra OCR.

## What's in here

- `articles/` — one markdown page per scholarly article, with full text
- `people/` — one page per author, aggregating their appearances across volumes
- `volumes/` — one page per volume, with article TOC
- `_graph/corpus.jsonld` — the merged JSON-LD knowledge graph (the spine)
- `index.md` — the catalog
- `log.md` — append-only history
- `CLAUDE.md` — operating instructions for the LLM agent

## How it grows

```
pjb-pipeline run configs/pjb-049-2007.yaml
pjb-pipeline add-volume <this-wiki-root> output/pjb-049-2007/
# review with `git diff`, then commit
```

Each `add-volume` is idempotent: re-running with the same volume produces a
clean no-op diff. LLM-authored content (`## Summary`, `## Mentions`,
`## Notes` sections, plus any agent-added frontmatter fields) is preserved
across re-runs; the pipeline only regenerates the structural parts.

## How to read it

- Browse with any markdown viewer; recommended: [Obsidian](https://obsidian.md)
  pointed at this directory as the vault.
- The graph view in Obsidian, with `_graph/` opened, shows the
  cross-volume Person → Article → Volume links the JSON-LD encodes.
- For machine consumption, load `_graph/corpus.jsonld` into rdflib /
  Apache Jena / GraphDB / neosemantics directly.

## Conventions

Every page's frontmatter is a valid JSON-LD node, with the shared
`@context` at `_context.json`. The frontmatter and the graph node for
the same entity are byte-equivalent for all pipeline-owned fields.
See `CLAUDE.md` for the full contract.
