"""Stage timing.

Tiny context manager that times a pipeline stage and writes the result
into a dict you control. Lifted out of the notebook so individual stages
can opt into timing without grabbing a global.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Dict


@contextmanager
def stage(name: str, timings: Dict[str, float]):
    """Time a pipeline stage and record the result in ``timings``.

    Usage::

        TIMING: dict = {}
        with stage("Render PDF", TIMING):
            ...
    """
    print(f"\n── {name} ".ljust(72, "─"))
    t0 = time.time()
    try:
        yield
    finally:
        dt = time.time() - t0
        timings[name] = dt
        m, s = divmod(dt, 60)
        print(f"   ⏱  {int(m):d}m {s:5.1f}s")


def format_report(timings: Dict[str, float], header: str = "") -> str:
    """Render the timing dict as a pretty text report."""
    lines = []
    if header:
        lines.append("═" * 72)
        lines.append(f"  {header}")
        lines.append("═" * 72)

    total = sum(v for k, v in timings.items() if not k.startswith("_"))
    for stage_name, dt in timings.items():
        if stage_name.startswith("_"):
            continue
        m, s = divmod(dt, 60)
        pct = 100 * dt / total if total else 0
        lines.append(f"  {stage_name:<40} {int(m):>3}m {s:5.1f}s  ({pct:>4.1f} %)")
    lines.append("─" * 72)
    m, s = divmod(total, 60)
    lines.append(f"  {'Total':<40} {int(m):>3}m {s:5.1f}s")

    if "_per_page_ocr_avg_s" in timings:
        avg = timings["_per_page_ocr_avg_s"]
        lines.append(f"  Avg OCR seconds/page: {avg:.1f}")
        proj_350 = avg * 350 / 60
        lines.append(f"  → Projected for a full 350-page volume: {proj_350:.0f} min OCR")

    return "\n".join(lines)
