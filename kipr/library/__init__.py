"""Library review: symbols, footprints and 3D models changed between two commits.

Stages (each also a ``kipr library <stage>`` subcommand)::

    render   kipr.library.render.main   manifest.json + per-item renders/sources
    checks   kipr.library.checks.main   review.json + review.md (deterministic + KLC)
    site     kipr.library.site          copies the static viewer (web/library) into OUT
    report   kipr.library.report        one self-contained HTML report

``kipr.library.ci`` holds the GitHub glue (job summary, sanitizing, gh-pages, PR comment).
"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
"""The static viewer (``web/library`` in the source tree, shipped as package data)."""
