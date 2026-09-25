"""3D model fallback for the exports: a board that names `X.wrl` when only `X.step` exists (or the
other way round) still gets its component bodies in the GLB/STEP export.

KiCad 10's stock 3D library ships STEP only, so older boards that reference
`${KICAD6_3DMODEL_DIR}/…/R_0603.wrl` export bare boards. `kicad-cli --subst-models` only helps
when the .wrl exists next to the .step. :func:`substitute` rewrites such paths, in the temporary
export checkout only: the semantic diffs read the original file, the user's files are untouched.
"""

from __future__ import annotations

import os
import re
import shutil
import sys

# (model "path" ...: the path as written in the board (KiCad writes it quoted)
MODEL_RE = re.compile(r'(\(model\s+)"((?:[^"\\]|\\.)*)"')
VAR_RE = re.compile(r"^\$(?:\{([A-Za-z0-9_]+)\}|\(([A-Za-z0-9_]+)\))(.*)$")
STOCK_VAR_RE = re.compile(r"^KICAD\d*_3DMODEL_DIR$")
ALTERNATIVES = {".wrl": (".step", ".stp"), ".wrz": (".step", ".stp"), ".step": (".wrl", ".stp"),
                ".stp": (".step", ".wrl")}


def stock_model_dirs(kicad_cli: str | None = None) -> list[str]:
    """Directories that may hold KiCad's stock 3D library (`${KICADn_3DMODEL_DIR}`), existing ones
    only, best first: $KIPR_KICAD_3DMODEL_DIR, $KICADn_3DMODEL_DIR, next to kicad-cli
    (<prefix>/share/kicad/3dmodels), then the usual install locations."""
    cands = [os.environ.get("KIPR_KICAD_3DMODEL_DIR")]
    cands += [v for k, v in sorted(os.environ.items(), reverse=True) if STOCK_VAR_RE.match(k)]
    if kicad_cli:
        exe = os.path.realpath(shutil.which(kicad_cli) or kicad_cli)
        prefix = os.path.dirname(os.path.dirname(exe))
        cands += [os.path.join(prefix, "share", "kicad", "3dmodels"),
                  os.path.join(prefix, "SharedSupport", "3dmodels")]  # macOS app bundle
    cands += ["/usr/share/kicad/3dmodels", "/usr/local/share/kicad/3dmodels",
              "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels"]
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        kd = os.path.join(pf, "KiCad")
        if os.path.isdir(kd):
            cands += [os.path.join(kd, v, "share", "kicad", "3dmodels") for v in sorted(os.listdir(kd), reverse=True)]
    out = []
    for c in cands:
        if c and os.path.isdir(c) and os.path.abspath(c) not in out:
            out.append(os.path.abspath(c))
    return out


def resolve(path: str, project_dir: str, stock_dirs: list[str]) -> list[str] | None:
    """Candidate absolute files for a model path as KiCad would look it up, or None when it depends
    on something we can't know (an unknown variable, an embedded model)."""
    if not path or "://" in path:
        return None
    m = VAR_RE.match(path)
    if m:
        var, rest = m.group(1) or m.group(2), m.group(3).lstrip("/\\")
        if var == "KIPRJMOD":
            return [os.path.join(project_dir, rest)]
        if STOCK_VAR_RE.match(var):
            return [os.path.join(d, rest) for d in stock_dirs] or None
        val = os.environ.get(var)
        return [os.path.join(val, rest)] if val else None
    if "$" in path:
        return None
    if os.path.isabs(path):
        return [path]
    return [os.path.join(project_dir, path)]


def substitute(text: str, project_dir: str, stock_dirs: list[str]) -> tuple[str, dict, dict]:
    """Rewrite model paths that don't resolve to the same path with another model extension that
    does. Returns (new text, {old path: new path}, counts {found, substituted, missing, unknown})
    over the distinct model paths of the board."""
    subs, counts, seen = {}, {"found": 0, "substituted": 0, "missing": 0, "unknown": 0}, set()
    for m in MODEL_RE.finditer(text):
        path = m.group(2)
        if path in seen:
            continue
        seen.add(path)
        cands = resolve(path, project_dir, stock_dirs)
        if cands is None:
            counts["unknown"] += 1
            continue
        if any(os.path.isfile(c) for c in cands):
            counts["found"] += 1
            continue
        stem, ext = os.path.splitext(path)
        new = None
        for alt in ALTERNATIVES.get(ext.lower(), ()):
            for variant in (alt, alt.upper()):
                if any(os.path.isfile(os.path.splitext(c)[0] + variant) for c in cands):
                    new = stem + variant
                    break
            if new:
                break
        if new:
            subs[path] = new
            counts["substituted"] += 1
        else:
            counts["missing"] += 1
    if subs:
        text = MODEL_RE.sub(lambda m: m.group(1) + '"' + subs.get(m.group(2), m.group(2)) + '"', text)
    return text, subs, counts
