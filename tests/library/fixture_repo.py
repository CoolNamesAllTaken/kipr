"""Build a small two-commit KiCad library repository from tests/library/fixtures/kicad-libs.

    base  SOIC-8-1EP (older pads/value), TS32 switch (pad 2 moved, other model name),
          SOP-8 (deleted in head), Custom_Audio.kicad_sym (NS4168 with an older pin name)
    head  the kicad-libs files: SOIC-8-1EP and switch modified, buzzer and microSD added,
          SOP-8 deleted, NS4168 modified, buzzer STEP model + an unreferenced STEP added,
          a datasheet PDF added

``origin/main`` points at base and HEAD at head, like a PR checkout. ``dirs`` relocates the
library directories (``{"fp": "footprints", "sym": "symbols", "models": "3d"}``), rewriting the
footprints' ``${KICAD_LIBS_DIR}/lib_3d/`` model paths to match.

    python3 tests/library/fixture_repo.py DIR     # build it for manual runs
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kicad-libs"
DEFAULT_DIRS = {"fp": "lib_fp", "sym": "lib_sch", "models": "lib_3d", "datasheets": "datasheets"}
SOIC = "lib_fp/Custom_Package_SO.pretty/SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm.kicad_mod"
SWITCH = "lib_fp/Custom_Button_Switch_SMD.pretty/SW_SPST_Same-Sky_TS32_with-boss.kicad_mod"
SYM = "lib_sch/Custom_Audio.kicad_sym"
MINI_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def soic_base(t: str) -> str:
    t = t.replace("(size 2.41 3.3)", "(size 2.29 3)")
    t = re.sub(r'\(property "Value" "[^"]*"', '(property "Value" "SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.29x3mm"', t, count=1)
    return t.replace('(pad "1" smd roundrect', '(pad "1" smd rect', 1)


def switch_base(t: str) -> str:
    t = re.sub(r'(\(pad "2" smd \w+\s*\(at )(-?[\d.]+)', lambda m: f"{m.group(1)}{float(m.group(2)) - 0.2:g}", t, count=1)
    return t.replace("TS32-7-35-BK-B-260-RA-SMT-TR.STEP", "TS32-7-35-BK-B-260-RA-SMT-TR_old.STEP")


def symbol_base(t: str) -> str:
    return t.replace('"LRCLK"', '"WS"')


def _git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=kipr tests", "-c", "user.email=tests@kipr.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout


def _files(side: str) -> dict[str, bytes]:
    root = FIXTURES / side
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _relocate(files: dict[str, bytes], dirs: dict) -> dict[str, bytes]:
    out = {}
    for rel, data in files.items():
        top, rest = rel.split("/", 1)
        key = {"lib_fp": "fp", "lib_sch": "sym", "lib_3d": "models", "datasheets": "datasheets"}[top]
        new_top = dirs[key]
        if rel.endswith(".kicad_mod"):
            data = data.replace(b"${KICAD_LIBS_DIR}/lib_3d/",
                                ("${KICAD_LIBS_DIR}/" + (dirs["models"] + "/" if dirs["models"] else "")).encode())
        out[f"{new_top}/{rest}" if new_top else rest] = data
    return out


def _write(repo: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def build(dst, dirs: dict | None = None) -> tuple[str, str]:
    """Create the repo at dst; returns (base sha, head sha)."""
    dirs = {**DEFAULT_DIRS, **(dirs or {})}
    repo = Path(dst)
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    head = _files("head")
    head["datasheets/NS4168.pdf"] = MINI_PDF
    base = {**_files("base")}
    base[SOIC] = soic_base(head[SOIC].decode()).encode()
    base[SWITCH] = switch_base(head[SWITCH].decode()).encode()
    base[SYM] = symbol_base(head[SYM].decode()).encode()
    (repo / "README.md").write_text("kipr library review test fixture (parts from PantsForBirds/kicad-libs, MIT)\n")
    _write(repo, _relocate(base, dirs))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").strip()
    for rel in _relocate(base, dirs):
        (repo / rel).unlink()
    _write(repo, _relocate(head, dirs))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head: add/modify/delete parts")
    head_sha = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)
    return base_sha, head_sha


if __name__ == "__main__":
    b, h = build(sys.argv[1])
    print(f"base {b}\nhead {h}")
