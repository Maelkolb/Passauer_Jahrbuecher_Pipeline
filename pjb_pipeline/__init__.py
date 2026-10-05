"""Passauer Jahrbücher digital edition pipeline.

A modular pipeline that ingests a scanned volume (one book PDF, or a
folder of individually scanned page images), runs Chandra 2
layout-aware OCR, reconstructs page and article structure, and emits
TEI-XML, PageXML, a static HTML edition, and a JSON-LD knowledge-graph
fragment that can be merged with other volumes downstream.
"""

__version__ = "0.4.0"
