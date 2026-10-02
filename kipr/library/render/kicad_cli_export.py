"""Optional: reference SVGs exported with kicad-cli (only when --use-kicad-cli and kicad-cli is on PATH).

These are *extra* files (items/<slug>/kicad_cli_<side>.svg); kicad-cli picks its own page frame,
so they do not share the built-in renders' viewBox and are not used for the overlay/diff.

Text in an outline font (``(face "X")``) is laid out by kicad-cli with fontconfig: the faces the
changed items use are installed first (``--fonts DIR``, Google Fonts unless ``--no-fetch-fonts``;
see kipr.common.fonts) and an item whose face kicad-cli still had to substitute gets a warning.
"""

from __future__ import annotations

import glob
import os
import shutil
import tempfile

from kipr.common import fonts, kicad_cli


def _run(cmd, env=None):
    r = kicad_cli.run(cmd[0], *cmd[1:], env=env)
    return r.ok, r.message


def export(kc, git, head_sha, base_sha, items, entries, out, font_dirs=(), fetch_fonts=True, font_cache=None,
           log=print) -> dict | None:
    """Writes the reference SVGs; returns the fonts report (kipr.common.fonts.FontSetup.report) or None."""
    by_id = {e["id"]: e for e in entries}
    texts = {f"{side}:{it.id}": t for it in items for side, t in (("head", it.head_text), ("base", it.base_text)) if t}
    need, embedded = fonts.needed_faces(texts)
    font_root = tempfile.mkdtemp(prefix="kipr-fonts-")
    try:
        setup = fonts.setup(need, embedded, font_root, list(font_dirs or ()), fetch_fonts, font_cache, log)
        _export(kc, items, by_id, out, setup)
        missing = set(setup.missing())
        for it in items:  # not every kicad-cli command reports the substitution itself
            e = by_id.get(it.id)
            used = {f for f, files in need.items() for x in files if x.split(":", 1)[1] == it.id}
            for face in sorted(used & missing):
                w = (f"kicad-cli: font '{face}' is not available, the reference SVGs use another font "
                     "(text sizes differ from KiCad with the font installed)")
                if e is not None and not any(f"font '{face}'" in x for x in e.get("warnings", [])):
                    e.setdefault("warnings", []).append(w)
        if not setup.faces:
            return None
        rep = setup.report()
        rep["warning"] = fonts.warning_text(setup.missing()) or None
        return rep
    finally:
        shutil.rmtree(font_root, ignore_errors=True)


def _export(kc, items, by_id, out, setup):
    env = setup.env
    for it in items:
        e = by_id.get(it.id)
        if e is None:
            continue
        for side, text in (("head", it.head_text), ("base", it.base_text)):
            if text is None:
                continue
            with tempfile.TemporaryDirectory() as td:
                if it.kind == "footprint":
                    lib = os.path.join(td, f"{it.library}.pretty")
                    os.makedirs(lib)
                    with open(os.path.join(lib, f"{it.name}.kicad_mod"), "w", encoding="utf-8") as fh:
                        fh.write(text)
                    ok, msg = _run([kc, "fp", "export", "svg", "--fp", it.name, "--output", td, lib], env)
                else:
                    src = os.path.join(out, e["source"][side]) if e.get("source", {}).get(side) else None
                    if not src:
                        continue
                    ok, msg = _run([kc, "sym", "export", "svg", "--symbol", it.name, "--output", td, src], env)
                for face, sub in fonts.substitutions(msg).items():
                    setup.mark_missing(face, sub)
                    if not any(f"font '{face}'" in x for x in e.get("warnings", [])):
                        e.setdefault("warnings", []).append(
                            f"kicad-cli: font '{face}' is not available, the reference SVGs use '{sub}' "
                            "(text sizes differ from KiCad with the font installed)")
                svgs = sorted(glob.glob(os.path.join(td, "*.svg")))
                if not ok or not svgs:
                    e.setdefault("warnings", []).append(f"kicad-cli ({side}): {msg[:300]}")
                    continue
                dst = os.path.join(out, "items", e["slug"], f"kicad_cli_{side}.svg")
                shutil.copyfile(svgs[0], dst)
                e.setdefault("renders_kicad_cli", {})[side] = os.path.relpath(dst, out).replace(os.sep, "/")
