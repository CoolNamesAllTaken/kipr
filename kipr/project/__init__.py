"""Project review: KiCad projects (schematic, layout, 3D PCBA) changed between two commits.

Stages (each also a ``kipr project <stage>`` subcommand)::

    review   kipr.project.review   kicad-cli exports + semantic diffs -> project-review.json
    site     kipr.project.site     copies the static viewer (web/project) into OUT
    report   kipr.project.report   one self-contained HTML report (no JavaScript)

``kipr.project.ci`` holds the GitHub glue (job summary, size budget, PR comment).
"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
"""The static viewer (``web/project`` in the source tree, shipped as package data)."""
