"""Page and volume structure reconstruction.

These modules sit between the unified per-page model produced by
``normalize.py`` and the format-specific emitters in ``emit/``. They add:

* :mod:`pjb_pipeline.structure.toc` — parse a TOC block (the OCR's
  ``table-of-contents`` region) into structured entries (section + title +
  author + printed page number).
* :mod:`pjb_pipeline.structure.articles` — TOC-driven article boundary
  detection with heuristic fallback when no TOC was recognised.
* :mod:`pjb_pipeline.structure.footnotes` — find inline footnote
  references in body text and link them to footnote regions.
"""
