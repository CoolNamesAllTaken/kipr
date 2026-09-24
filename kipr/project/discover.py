"""Find the KiCad projects affected by a change and check out their files."""

from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field

from ._compat import Git

# Same noise filter as the old kiri workflow (.github/workflows/kicad-diff.yml in internal).
NOISE = re.compile(r"(^|/)\.history/|-backups/|(^|/)panelized/")
KICAD_EXT = (".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_sym", ".kicad_mod", ".kicad_dru",
             ".kicad_wks")
TABLES = ("sym-lib-table", "fp-lib-table", "design-block-lib-table")
MODEL_EXT = (".step", ".stp", ".wrl", ".vrml", ".stpz", ".igs", ".iges", ".glb")
MODEL_RE = re.compile(r'\(model\s+"([^"]+)"')
URI_RE = re.compile(r'\(uri\s+"([^"]+)"')


@dataclass
class Project:
    path: str  # repo-relative dir of the .kicad_pro ("" for the repo root)
    name: str  # .kicad_pro stem
    status: str  # added | removed | modified
    base_pro: str | None
    head_pro: str | None
    reasons: list[str] = field(default_factory=list)
    deps: dict[str, set[str]] = field(default_factory=dict)  # side -> repo paths outside the dir


def is_noise(path: str) -> bool:
    return bool(NOISE.search(path))


def resolve(ref: str, pdir: str) -> str | None:
    """Turn a lib-table uri / model path into a repo path (None if outside the repo / env-based)."""
    r = ref.replace("\\", "/")
    for var in ("${KIPRJMOD}", "$(KIPRJMOD)"):
        if r.startswith(var):
            r = r[len(var):].lstrip("/")
            break
    else:
        if r.startswith(("$", "/")) or re.match(r"^[A-Za-z]:", r):
            return None
    p = posixpath.normpath(posixpath.join(pdir, r)) if pdir else posixpath.normpath(r)
    if p.startswith("../") or p == "..":
        return None
    return p


def project_deps(git: Git, sha: str, pdir: str, files: list[str]) -> set[str]:
    """Repo paths a project loads that are outside its own directory (project libs, 3D models)."""
    deps = set()
    for f in files:
        base = posixpath.basename(f)
        if base in TABLES:
            text = git.show_text(sha, f) or ""
            for uri in URI_RE.findall(text):
                p = resolve(uri, pdir)
                if p:
                    deps.add(p)
        elif f.endswith(".kicad_pcb"):
            text = git.show_text(sha, f) or ""
            for m in set(MODEL_RE.findall(text)):
                p = resolve(m, pdir)
                if p:
                    deps.add(p)
    return deps


def _dir(p: str) -> str:
    return posixpath.dirname(p)


def find_projects(git: Git, base: str, head: str, patterns: list[str] | None = None) -> list[Project]:
    changes = git.changed_files(base, head)
    changed = set()
    for _st, path, old in changes:
        changed.add(path)
        if old:
            changed.add(old)
    changed = {p for p in changed if not is_noise(p)}
    tree_b, tree_h = git.ls_tree(base), git.ls_tree(head)
    pros = {}
    for side, tree in (("base", tree_b), ("head", tree_h)):
        for p in tree:
            if p.endswith(".kicad_pro") and not is_noise(p):
                pros.setdefault(_dir(p), {})[side] = p
    out = []
    for pdir, sides in sorted(pros.items()):
        if patterns and not any(fnmatch.fnmatch(pdir, pat) or fnmatch.fnmatch(posixpath.basename(pdir), pat)
                                for pat in patterns):
            continue
        name = posixpath.basename((sides.get("head") or sides.get("base")))[: -len(".kicad_pro")]
        prefix = pdir + "/" if pdir else ""
        reasons = []
        for c in sorted(changed):
            rel = c[len(prefix):] if c.startswith(prefix) else None
            if rel is None:
                continue
            if "/" not in rel and (rel.endswith(KICAD_EXT) or rel in TABLES):
                reasons.append(c)
            elif rel.endswith((".kicad_sym", ".kicad_mod", ".kicad_sch") + MODEL_EXT):
                reasons.append(c)  # project-local libraries / sub-sheets in subdirectories
        proj = Project(path=pdir, name=name,
                       status="added" if "base" not in sides else "removed" if "head" not in sides else "modified",
                       base_pro=sides.get("base"), head_pro=sides.get("head"), reasons=reasons)
        # dependencies outside the project dir (only worth computing if something outside changed)
        outside = [c for c in changed if not c.startswith(prefix)
                   and (c.endswith(KICAD_EXT + MODEL_EXT) or ".pretty/" in c)]
        for side, sha, tree in (("base", base, tree_b), ("head", head, tree_h)):
            if side not in sides:
                continue
            files = [p for p in tree if p.startswith(prefix) and "/" not in p[len(prefix):]]
            proj.deps[side] = project_deps(git, sha, pdir, files) if outside else set()
            for d in proj.deps[side]:
                for c in outside:
                    if c == d or c.startswith(d.rstrip("/") + "/"):
                        reasons.append(c)
        proj.reasons = sorted(set(reasons))
        if proj.reasons or proj.status != "modified":
            out.append(proj)
    return out


def checkout_paths(git: Git, sha: str, proj: Project, side: str) -> list[str]:
    """Repo paths to extract for one side: the project dir (non-recursive files + any subdirs
    with KiCad content) and its outside dependencies that exist at `sha`."""
    prefix = proj.path + "/" if proj.path else ""
    tree = git.ls_tree(sha, [proj.path] if proj.path else None)
    want = []
    for p in tree:
        rel = p[len(prefix):]
        if is_noise(p):
            continue
        if "/" not in rel or rel.endswith(KICAD_EXT + MODEL_EXT) or ".pretty/" in rel or \
                posixpath.basename(rel) in TABLES:
            want.append(p)
    deps = proj.deps.get(side) or project_deps(git, sha, proj.path,
                                               [p for p in tree if "/" not in p[len(prefix):]])
    if deps:
        existing = git.ls_tree(sha, sorted(deps))
        want.extend(existing)
    return sorted(set(want))
