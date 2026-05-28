"""Shared JSON-LD ``@context`` for the corpus.

Both the structural graph emitter (:mod:`pjb_pipeline.emit.graph`) and the
markdown wiki emitter (:mod:`pjb_pipeline.emit.wiki`) write nodes against
the same JSON-LD vocabulary. Keeping the context here, in one place,
guarantees that frontmatter in the wiki and node bodies in the corpus
graph are byte-identical and round-trippable.

If you add a new predicate, add it here. ``graph.py``, ``wiki.py``, and
``scripts/init_wiki.py`` all pick it up automatically.
"""

from __future__ import annotations


# Base IRI under which all nodes live. Pick a domain you own; for now we
# use a placeholder that downstream tools can rewrite via SPARQL or sed.
BASE = "https://passauer-jahrbuecher.example/"


CONTEXT = {
    "@vocab":   "https://schema.org/",
    "pjb":      BASE,
    "schema":   "https://schema.org/",
    "dcterms":  "http://purl.org/dc/terms/",
    "tei":      "http://www.tei-c.org/ns/1.0#",
    # Local predicates we don't think belong in schema.org
    "facsimile":    {"@id": "pjb:facsimile",    "@type": "@id"},
    "pageStart":    {"@id": "schema:pageStart"},
    "pageEnd":      {"@id": "schema:pageEnd"},
    "inSection":    {"@id": "pjb:inSection",    "@type": "@id"},
    "inVolume":     {"@id": "pjb:inVolume",     "@type": "@id"},
    "inArticle":    {"@id": "pjb:inArticle",    "@type": "@id"},
    "inPage":       {"@id": "pjb:inPage",       "@type": "@id"},
    "inSeries":     {"@id": "schema:isPartOf",  "@type": "@id"},
    "hasPart":      {"@id": "schema:hasPart",   "@type": "@id"},
    "tocEntry":     {"@id": "pjb:tocEntry"},
    "footnoteOf":   {"@id": "pjb:footnoteOf",   "@type": "@id"},
    "refersTo":     {"@id": "pjb:refersTo",     "@type": "@id"},
    "rawType":      {"@id": "pjb:rawType"},
    # Visual-region predicates
    "contentUrl":   {"@id": "schema:contentUrl"},
    "bbox":         {"@id": "pjb:bbox"},
    "regionType":   {"@id": "pjb:regionType"},
}
