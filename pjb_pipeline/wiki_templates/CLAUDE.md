# CLAUDE.md — Wiki operating instructions

This file tells an LLM agent (Claude Code or equivalent) how to work on
the *Passauer Jahrbücher* LLM-Wiki. Read it first in every session.

## What this wiki is

A persistent, interlinked markdown knowledge base for the *Passauer
Jahrbücher* — a 55-volume German-language scholarly journal on the
history and culture of Ostbaiern (Eastern Bavaria), published since
1948 by the Verein für Ostbairische Heimatforschung.

Each volume is processed by the [Passauer Jahrbücher Pipeline]
(scanned PDF → Chandra OCR → article boundaries → TEI/JSON-LD/HTML).
The pipeline emits **structural facts**; this wiki layers **content**
on top: summaries, themes, cross-references, the connections that
make a scholarly corpus actually navigable.

The wiki is the human-readable face of the JSON-LD knowledge graph
at `_graph/corpus.jsonld`. Every page has frontmatter that *is* a
JSON-LD node — pages and the graph are the same objects in two
serializations. Keep that contract.

## Directory layout

```
.
├── CLAUDE.md              ← this file
├── README.md              ← short orientation for human readers
├── index.md               ← catalog of every wiki page (regenerated)
├── log.md                 ← append-only history of operations
├── series.md              ← the PublicationSeries page (top-level)
├── _context.json          ← shared JSON-LD @context
├── _graph/
│   └── corpus.jsonld      ← the merged knowledge graph (the spine)
├── volumes/<slug>.md      ← one per PublicationVolume (e.g. pjb-048-2006)
├── articles/<art-id>.md   ← one per ScholarlyArticle — the heart of the wiki
├── people/<slug>.md       ← one per Person; cross-volume aggregations live here
├── sections/              ← per-volume TOC sections (added on demand)
├── places/                ← reserved — populated when NER lands
└── topics/                ← reserved — populated when NER lands
```

## Frontmatter contract

Every page has YAML frontmatter at the top. The frontmatter is JSON-LD
when parsed: it shares its `@context` (the file at `../_context.json`,
relative to the page) with `_graph/corpus.jsonld`.

**Pipeline-owned fields (DO NOT EDIT):**
- `@context`, `@id`, `@type`
- `name`, `pageStart`, `pageEnd`, `position`
- `inVolume`, `inSection`, `inLanguage`
- `author`, `hasPart`, `isPartOf`, `publisher`, `editor`
- `volumeNumber`, `datePublished`
- Anything emitted by the pipeline's graph stage

**Agent-owned fields (you may add and edit freely):**
- `summary` — short prose summary, ≤ 3 sentences
- `tags` — list of free-text tags
- `mentions` — list of `@id` references to other wiki entities
- `relatedTo` — list of `@id` references to thematically related pages
- Any field not in the pipeline-owned list

## Section contract

Each article page is structured:

```
# <Title>

**<Author>** · <Section> · pp. X–Y

## Summary       ← agent-owned. The single most valuable thing you write.
## Mentions      ← agent-owned. Bullet list of named entities (people, places, topics, works).
## Full Text     ← pipeline-owned. DO NOT EDIT — regenerated on every add-volume.
## Footnotes     ← pipeline-owned. DO NOT EDIT.
## Notes         ← agent-owned. Free-form thoughts, cross-refs, open questions.
```

The pipeline preserves Summary, Mentions, and Notes on re-emit. It will
regenerate Full Text and Footnotes. If you put content in the wrong
section it will be erased on the next `add-volume` run.

## Operations

### Ingest (after the pipeline adds a new volume)

The human runs `pjb-pipeline add-volume <wiki-root> output/<slug>/`.
This drops new article/person pages and refreshes `index.md`,
`_graph/corpus.jsonld`, and `log.md`. The new pages have empty
`## Summary` placeholders.

Your job after an ingest:

1. Open `log.md`, find the most recent `## [YYYY-MM-DD] add-volume` entry,
   read the list of new articles.
2. For each new article, read `articles/<art-id>.md` in full (the body is
   long but you need it). Write a 2–3 sentence summary in `## Summary`.
3. Identify the named entities (people, places, organisations, themes)
   mentioned in the article and list them in `## Mentions` as bullets,
   linking with markdown links to existing wiki pages where they exist
   (`[Vilshofen](../places/vilshofen.md)`). If a Person link in the
   author list isn't yet a real page, leave it — the pipeline owns those.
4. Update affected person pages with a `## Notes` line if the article
   reveals something noteworthy about that person.
5. When in doubt, prefer linking to existing pages over creating new
   ones. The `places/` and `topics/` directories will grow more usefully
   if entries accumulate around real recurrences rather than one-offs.

### Query

When the human asks a question:

1. Read `index.md` to find candidate pages.
2. Open the relevant pages (article, person, volume, future place/topic).
3. Synthesise the answer, citing each fact by the `@id` of the page it
   came from — not by file path, since `@id`s round-trip into the graph.
   Example: "(see *Die Vilshofener Stadtpfarrkirche*, `pjb:art/pjb-048-2006-art03`)".
4. If the answer involves a connection that doesn't yet exist as a
   wiki page (e.g. a Place that's mentioned in multiple articles but
   has no page), propose creating it — but ask before doing so, since
   page creation should be deliberate, not reflexive.

### Lint

Periodically (or on request), run a health-check pass:

1. **Person collisions**: scan `people/` for entries whose `name` field
   varies (e.g. "Wolff, Jürgen" vs "Wolff, J."). Merging them is a
   manual call — surface candidates, don't merge unilaterally.
2. **Orphan pages**: pages with no `## Mentions` references from any
   article. Either the article-side mentions need updating, or the
   page genuinely is an orphan.
3. **Stale summaries**: pages whose `## Summary` was written before more
   articles by the same person/place arrived. Re-read the new articles
   and update if warranted.
4. **Missing entities**: scan recent articles for proper nouns that
   appear three or more times across the corpus but have no wiki page.

## Forbidden moves

- Never edit `_graph/corpus.jsonld` directly. It is the output of
  `pjb-pipeline add-volume` and is regenerated each run.
- Never invent `@id`s. New `@id`s are minted by the pipeline; agent-
  authored pages (future Place / Topic pages) get `@id`s following the
  `pjb:place/<slug>` and `pjb:topic/<slug>` patterns and must be
  documented here when first introduced.
- Never edit frontmatter fields in the pipeline-owned list. If a value
  there looks wrong, it's a bug in the pipeline — flag it to the human,
  don't paper over it in the wiki.
- Never edit `## Full Text` or `## Footnotes`. They round-trip from the
  TEI; corrections go in the TEI, not here.

## Logging convention

Append one entry to `log.md` per session, even if you only added a few
summaries. The format:

```
## [YYYY-MM-DD] <action> | <one-line summary>
- bullet listing what changed, by `@id` or by filename
```

`grep "^## \[" log.md` should give a clean timeline. Don't break that.

## Future hooks

When NER lands, `places/` and `topics/` will be populated by a
deterministic enrichment step, not by you. At that point this section
will be replaced with their schema. Until then, treat them as empty
folders reserved for future use.
