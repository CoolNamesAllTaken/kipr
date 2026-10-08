"""kicad-cli exports for one side of a project, cached by input blob hashes.

A job's cache key is the hash of: kicad-cli version, the command and options, the project path and
the git blob ids of the files that command reads. Identical keys (e.g. an unchanged schematic on
both sides, or a re-run) are exported once; results live in `cache_dir/<key>/`.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from kipr.common import fonts
from kipr.common.kicad_cli import KicadCli

CACHE_VERSION = "1"

SCH_INPUTS = (".kicad_sch", ".kicad_pro", ".kicad_sym", ".kicad_wks", "sym-lib-table")
PCB_INPUTS = (".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_wks")
MODEL_INPUTS = (".step", ".stp", ".stpz", ".wrl", ".vrml", ".igs", ".iges")
ALL_INPUTS = SCH_INPUTS + PCB_INPUTS + (".kicad_mod", "fp-lib-table")


@dataclass
class Job:
    name: str
    cmd: tuple[str, ...]
    options: list
    target: str  # file path relative to the side root
    output: str  # "dir" | file name
    inputs: tuple[str, ...]  # suffixes of the files this job depends on
    ok_codes: tuple[int, ...] = (0,)
    isolated_libs: bool = False  # run with empty global symbol/footprint library tables (fast checks)


@dataclass
class JobResult:
    name: str
    ok: bool
    dir: str | None  # cache dir holding the outputs
    files: list[str] = field(default_factory=list)
    message: str = ""
    seconds: float = 0.0
    cached: bool = False
    font_substitutions: dict = field(default_factory=dict)  # {face: substitute} kicad-cli reported


def side_jobs(board_file: str | None, sch_file: str | None, layers: list[str], step: bool = False,
              glb: bool = True, fast_checks: bool = False) -> list[Job]:
    """Jobs for one side. `board_file`/`sch_file` are paths relative to the side root."""
    jobs = []
    if sch_file:
        jobs += [
            Job("sch_svg", ("sch", "export", "svg"), [("--output", "{out}/")], sch_file, "dir", SCH_INPUTS),
            Job("netlist", ("sch", "export", "netlist"), [("--format", "kicadsexpr"), ("--output", "{out}/netlist.net")],
                sch_file, "netlist.net", SCH_INPUTS),
            Job("bom", ("sch", "export", "bom"),
                [("--fields", "Reference,Value,Footprint,${QUANTITY},${DNP},MPN,Manufacturer"),
                 ("--labels", "Refs,Value,Footprint,Qty,DNP,MPN,Manufacturer"),
                 ("--group-by", "Value,Footprint,MPN,${DNP}"), ("--output", "{out}/bom.csv")],
                sch_file, "bom.csv", SCH_INPUTS),
            Job("erc", ("sch", "erc"), [("--format", "json"), "--severity-all", ("--units", "mm"),
                                        ("--output", "{out}/erc.json")], sch_file, "erc.json", SCH_INPUTS),
        ]
    if board_file:
        lay = ",".join(layers)
        jobs += [
            Job("gerbers", ("pcb", "export", "gerbers"), [("--output", "{out}/"), ("--layers", lay), "--no-protel-ext"],
                board_file, "dir", PCB_INPUTS),
            Job("drill", ("pcb", "export", "drill"), [("--output", "{out}/"), ("--format", "excellon"),
                                                      ("--excellon-units", "mm"), "--excellon-separate-th",
                                                      ("--drill-origin", "absolute")],
                board_file, "dir", PCB_INPUTS),
            Job("pcb_svg", ("pcb", "export", "svg"), [("--output", "{out}/"), ("--layers", lay), "--mode-multi",
                                                      ("--page-size-mode", "0"), "--exclude-drawing-sheet"],
                board_file, "dir", PCB_INPUTS),
            Job("pos", ("pcb", "export", "pos"), [("--format", "csv"), ("--units", "mm"), ("--side", "both"),
                                                  ("--output", "{out}/pos.csv")], board_file, "pos.csv", PCB_INPUTS),
            Job("drc", ("pcb", "drc"), [("--format", "json"), "--severity-all", ("--units", "mm")]
                + (["--schematic-parity"] if sch_file else []) + [("--output", "{out}/drc.json")],
                board_file, "drc.json", ALL_INPUTS, ok_codes=(0, 5)),
        ]
        if glb:
            jobs.append(Job("glb", ("pcb", "export", "glb"), ["--subst-models", ("--user-origin", "0x0mm"), "--force",
                                                              ("--output", "{out}/board.glb")],
                            board_file, "board.glb", PCB_INPUTS + MODEL_INPUTS, ok_codes=(0, 2)))
        if step:
            jobs.append(Job("step", ("pcb", "export", "step"), ["--subst-models", ("--user-origin", "0x0mm"), "--force",
                                                                ("--output", "{out}/board.step")],
                            board_file, "board.step", PCB_INPUTS + MODEL_INPUTS, ok_codes=(0, 2)))
    if fast_checks:
        for j in jobs:
            if j.name in ("erc", "drc"):
                j.isolated_libs = True
    return jobs


# Global library tables kicad-cli would otherwise load in full (the slow part of ERC/DRC).
_EMPTY_TABLES = {"sym-lib-table": "(sym_lib_table\n  (version 7)\n)\n",
                 "fp-lib-table": "(fp_lib_table\n  (version 7)\n)\n"}


def isolated_home(parent: str) -> str:
    """A throw-away HOME whose KiCad config has empty global library tables."""
    home = tempfile.mkdtemp(prefix=".home-", dir=parent)
    for ver in ("9.0", "10.0"):
        d = os.path.join(home, ".config", "kicad", ver)
        os.makedirs(d)
        for name, text in _EMPTY_TABLES.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
    return home


class Exporter:
    def __init__(self, cli: KicadCli, cache_dir: str, jobs: int = 4):
        self.cli = cli
        self.cache_dir = cache_dir
        # set before the first submit: environment for every kicad-cli run (the fonts' FONTCONFIG_FILE)
        # and what it changes in the outputs (part of every cache key)
        self.env: dict[str, str] = {}
        self.env_salt = ""
        os.makedirs(cache_dir, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=max(1, jobs))
        self._inflight: dict[str, Future] = {}
        self._lock = threading.Lock()

    def close(self):
        self.pool.shutdown(wait=True)

    def key(self, job: Job, root_rel: str, blobs: dict[str, str], salt: str = "") -> str:
        """`blobs`: {path relative to the side root: git blob id} of the checked-out files; `salt`:
        anything else the export depends on (the 3D model fallbacks applied to the checkout)."""
        h = hashlib.sha256()
        h.update(f"{CACHE_VERSION}\0{self.cli.version}\0{job.cmd}\0{job.options}\0{job.target}\0{root_rel}\0"
                 f"{job.isolated_libs}\0".encode())
        if salt:
            h.update(f"salt\0{salt}\0".encode())
        if self.env_salt:
            h.update(f"env\0{self.env_salt}\0".encode())
        for p in sorted(blobs):
            if p.endswith(job.inputs) or posixpath.basename(p) in job.inputs:
                h.update(f"{p}\0{blobs[p]}\0".encode())
        return h.hexdigest()[:32]

    def submit(self, job: Job, side_root: str, blobs: dict[str, str], project_dir: str, salt: str = "") -> Future:
        k = self.key(job, project_dir, blobs, salt)
        with self._lock:
            fut = self._inflight.get(k)
            if fut is None:
                fut = self.pool.submit(self._run, job, side_root, k)
                self._inflight[k] = fut
            return fut

    def _run(self, job: Job, side_root: str, key: str) -> JobResult:
        final = os.path.join(self.cache_dir, key)
        manifest = os.path.join(final, ".kipr-job.json")
        if os.path.isfile(manifest):
            with open(manifest) as fh:
                m = json.load(fh)
            try:  # mark as used, so a CI cache can drop entries a run didn't need
                os.utime(manifest)
            except OSError:
                pass
            return JobResult(job.name, True, final, m["files"], m.get("message", ""), 0.0, cached=True,
                             font_substitutions=m.get("font_substitutions") or {})
        tmp = tempfile.mkdtemp(prefix=f".{key}-", dir=self.cache_dir)
        opts = [(o[0], o[1].replace("{out}", tmp)) if isinstance(o, tuple) else o for o in job.options]
        t0 = time.monotonic()
        env, home = dict(self.env) or None, None
        if job.isolated_libs:
            home = isolated_home(self.cache_dir)
            # HOME/XDG for a plain kicad-cli, KIPR_KICAD_HOME for the kipr-tools wrapper
            env = {**self.env, "HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"), "KIPR_KICAD_HOME": home}
        try:
            ok, msg, rc = self.cli.run(job.cmd, opts, os.path.join(side_root, job.target), cwd=side_root, env=env)
        finally:
            if home:
                shutil.rmtree(home, ignore_errors=True)
        dt = time.monotonic() - t0
        subs = fonts.substitutions(msg)
        files = sorted(os.path.relpath(os.path.join(d, f), tmp) for d, _, fs in os.walk(tmp) for f in fs)
        if job.output != "dir":
            ok = (ok or rc in job.ok_codes) and job.output in files
        elif not files:
            ok = False
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True)
            return JobResult(job.name, False, None, [], msg[-2000:] or f"exit code {rc}", dt, font_substitutions=subs)
        with open(os.path.join(tmp, ".kipr-job.json"), "w") as fh:
            json.dump({"job": job.name, "files": files, "message": msg[-2000:], "seconds": dt,
                       "font_substitutions": subs}, fh)
        try:
            os.replace(tmp, final)
        except OSError:  # another process won the race
            shutil.rmtree(tmp, ignore_errors=True)
        return JobResult(job.name, True, final, files, msg[-2000:], dt, font_substitutions=subs)


# --- mapping kicad-cli file names to layers / sheets -----------------------------------

def _san(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()


def layer_file_map(files: list[str], board_stem: str, layers: list[dict], ext: str) -> dict[str, str]:
    """kicad-cli plots `<board>-<layer name or user name, '.'->'_'>.<ext>` -> {layer: file}."""
    names = {}
    for ly in layers:
        for n in (ly.get("user_name"), ly["name"]):
            if n:
                names.setdefault(_san(n), ly["name"])
    out = {}
    for f in files:
        base = posixpath.basename(f)
        if not base.endswith(ext):
            continue
        stem = base[: -len(ext)]
        if stem.startswith(board_stem + "-"):
            stem = stem[len(board_stem) + 1:]
        lay = names.get(_san(stem))
        if lay:
            out[lay] = f
    return out


def sheet_file_map(files: list[str], root_stem: str, sheets) -> dict[str, str]:
    """Sheet SVGs are `<root stem>.svg` and `<root stem>-<name>-<name>….svg` -> {sheet id: file}."""
    by_norm = {}
    for f in files:
        if f.endswith(".svg"):
            by_norm.setdefault(_san(posixpath.basename(f)[:-4]), f)
    out = {}
    for s in sheets:
        cand = root_stem if s.id == "root" else root_stem + "-" + "-".join(s.names[1:])
        f = by_norm.get(_san(cand))
        if f:
            out[s.id] = f
    return out


# Creation timestamps (gerber %TF.CreationDate, drill headers, SVG <title>, gbrjob) differ per run;
# the project id attribute carries the board revision, which is metadata rather than layer content.
_VOLATILE = re.compile(rb"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?|"
                       rb"TF\.ProjectId[^*\n]*")


def same_content(a: str | None, b: str | None) -> bool | None:
    """Compare two plot files ignoring creation dates/tool banners. None if either is missing.
    Gerbers and drill files that differ only in the order of their objects (a regenerated board,
    e.g. a KiKit panel, gets new uuids and KiCad plots in uuid order) count as the same."""
    if not a or not b or not os.path.isfile(a) or not os.path.isfile(b):
        return None
    with open(a, "rb") as fa, open(b, "rb") as fb:
        da, db = _VOLATILE.sub(b"", fa.read()), _VOLATILE.sub(b"", fb.read())
    if da == db:
        return True
    norm = gerber_objects if a.endswith(".gbr") else drill_objects if a.endswith(".drl") else None
    if norm is None:
        return False
    try:
        return norm(da.decode("ascii", "replace")) == norm(db.decode("ascii", "replace"))
    except ValueError:
        return False


_STMT = re.compile(r"%[^%]*%|[^*%]*\*")
_COORD = re.compile(r"([XYIJ])([+-]?\d+)")


def gerber_objects(text: str) -> Counter:
    """The drawing of a gerber as a multiset of objects, each with the state it is drawn in
    (aperture definition, polarity, interpolation): independent of object order, aperture numbers
    and attributes. A region (G36..G37) is one object."""
    macros, apertures, objs = {}, {}, Counter()
    ap = pol = None
    interp, pt, region = "G01", (0, 0), None
    for st in _STMT.findall(text):
        st = st.strip()
        if st.startswith("%"):
            body = st[1:-1]
            if body.startswith("AM"):
                name, _, rest = body[2:].partition("*")
                macros[name] = rest
            elif body.startswith("AD"):
                m = re.match(r"ADD(\d+)([^,*]+)(.*)", body)
                if m:
                    apertures[m.group(1)] = (macros.get(m.group(2), m.group(2)), m.group(3).rstrip("*"))
            elif body.startswith("LP"):
                pol = body[2:3]
            continue
        st = st[:-1]
        if not st or st.startswith("G04") or st == "M02":
            continue
        if st in ("G01", "G02", "G03", "G75", "G74"):
            interp = st if st != "G75" and st != "G74" else interp
            continue
        if st == "G36":
            region = []
            continue
        if st == "G37":
            if region:
                objs[("region", pol, tuple(region))] += 1
            region = None
            continue
        m = re.match(r"^(G0[123])?(.*?)(D0?[123]|D\d{2,})?$", st)
        if not m:
            continue
        if m.group(1):
            interp = m.group(1)
        d = m.group(3)
        if d and len(d) > 2 and d.lstrip("D").lstrip("0") not in ("1", "2", "3"):
            ap = apertures.get(d[1:], d)
            continue
        vals = dict((k, int(v)) for k, v in _COORD.findall(m.group(2)))
        new = (vals.get("X", pt[0]), vals.get("Y", pt[1]))
        op = d[-1] if d else "1"
        if op == "1":
            seg = (interp, pt, new, vals.get("I"), vals.get("J"))
            if region is not None:
                region.append(seg)
            else:
                objs[("draw", ap, pol, seg)] += 1
        elif op == "3":
            objs[("flash", ap, pol, new)] += 1
        elif region is not None:
            region.append(("move", new))
        pt = new
    return objs


def drill_objects(text: str) -> Counter:
    """An Excellon file as a multiset of (tool diameter, command) lines, independent of order and
    tool numbers."""
    tools, objs, cur, body = {}, Counter(), None, False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        m = re.match(r"^T(\d+)C([\d.]+)", line)
        if m:
            tools[m.group(1)] = m.group(2)
            continue
        if line in ("%", "M95"):
            body = True
            continue
        if not body:
            continue
        m = re.match(r"^T(\d+)$", line)
        if m:
            cur = tools.get(m.group(1).lstrip("0") or "0", tools.get(m.group(1)))
            continue
        if line != "M30":
            objs[(cur, line)] += 1
    return objs


_FS = re.compile(r"%FS[LT]?[AI]?X(\d)(\d)Y(\d)(\d)\*%")
_XY = re.compile(r"(?:X([+-]?\d+))?(?:Y([+-]?\d+))?(?:I[+-]?\d+)?(?:J[+-]?\d+)?D0?([123])\*")


def gerber_extent(text: str, origin=(0.0, 0.0)) -> list[float] | None:
    """KiCad-frame box [x, y, w, h] (mm) of every coordinate a gerber names, or None if it draws nothing.

    Coordinates only (no aperture size): callers add a margin. The gerber frame is KiCad's with y
    negated around `origin` (board.gerber_origin_mm). Handles %FS…% decimals and %MOIN*% inches.
    """
    fs = _FS.search(text)
    scale = 10.0 ** -int(fs.group(2)) if fs else 1e-6
    if "%MOIN*%" in text:
        scale *= 25.4
    x = y = 0.0
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for m in _XY.finditer(text):
        if m.group(1) is not None:
            x = int(m.group(1)) * scale
        if m.group(2) is not None:
            y = int(m.group(2)) * scale
        x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
    if x0 == float("inf"):
        return None
    ox, oy = origin
    return [round(x0 + ox, 4), round(oy - y1, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def union_box(boxes) -> list[float] | None:
    """Union of [x, y, w, h] boxes (None entries ignored)."""
    bs = [b for b in boxes if b]
    if not bs:
        return None
    x0, y0 = min(b[0] for b in bs), min(b[1] for b in bs)
    x1, y1 = max(b[0] + b[2] for b in bs), max(b[1] + b[3] for b in bs)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]
