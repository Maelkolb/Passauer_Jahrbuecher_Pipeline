"""Passauer Jahrbücher digital edition pipeline.

A modular pipeline that ingests a scanned PDF volume, runs Chandra 2
layout-aware OCR, reconstructs page and article structure, and emits
TEI-XML, PageXML, a static HTML edition, and a JSON-LD knowledge-graph
fragment that can be merged with other volumes downstream.
"""

__version__ = "0.3.0"
