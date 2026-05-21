"""HTML page chrome — the masthead/footer/asset-link envelope that wraps
every page in the edition."""

from __future__ import annotations

import html

from ...config import VolumeConfig


def html_escape(s: str) -> str:
    return html.escape(s or "", quote=True)


def page_chrome(
    cfg: VolumeConfig,
    title: str,
    body_html: str,
    extra_head: str = "",
    asset_prefix: str = "..",
) -> str:
    """Wrap ``body_html`` in the standard masthead + colophon shell.

    ``asset_prefix`` is the relative path from the rendered HTML file to
    ``html/`` — ``..`` for subfolders (articles, pages) and ``.`` for
    the index.
    """
    return f"""<!doctype html>
<html lang="{cfg.language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_escape(title)}</title>
<link rel="stylesheet" href="{asset_prefix}/assets/edition.css">
{extra_head}
</head>
<body>
<div class="site">
<header class="masthead">
  <div class="wordmark"><em>Passauer</em> Jahrbücher · digital</div>
  <nav>
    <a href="{asset_prefix}/index.html">Volume</a>
    <a href="{asset_prefix}/index.html#contents">Contents</a>
  </nav>
</header>
<main>
{body_html}
</main>
<footer class="colophon">
  <strong>{html_escape(cfg.volume_title)} {html_escape(cfg.volume_number_roman)}</strong>
  {cfg.volume_year} · {html_escape(cfg.publisher)} <span class="ornament">❦</span>
  Digital edition built by an automated pipeline.
</footer>
</div>
<script src="{asset_prefix}/assets/edition.js" defer></script>
</body>
</html>
"""
