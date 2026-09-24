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
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

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
        os.makedirs(cache_dir, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=max(1, jobs))
        self._inflight: dict[str, Future] = {}
        self._lock = threading.Lock()

    def close(self):
        self.pool.shutdown(wait=True)

    def key(self, job: Job, root_rel: str, blobs: dict[str, str]) -> str:
        """`blobs`: {path relative to the side root: git blob id} of the checked-out files."""
        h = hashlib.sha256()
        h.update(f"{CACHE_VERSION}\0{self.cli.version}\0{job.cmd}\0{job.options}\0{job.target}\0{root_rel}\0"
                 f"{job.isolated_libs}\0".encode())
        for p in sorted(blobs):
            if p.endswith(job.inputs) or posixpath.basename(p) in job.inputs:
                h.update(f"{p}\0{blobs[p]}\0".encode())
        return h.hexdigest()[:32]

    def submit(self, job: Job, side_root: str, blobs: dict[str, str], project_dir: str) -> Future:
        k = self.key(job, project_dir, blobs)
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
            return JobResult(job.name, True, final, m["files"], m.get("message", ""), 0.0, cached=True)
        tmp = tempfile.mkdtemp(prefix=f".{key}-", dir=self.cache_dir)
        opts = [(o[0], o[1].replace("{out}", tmp)) if isinstance(o, tuple) else o for o in job.options]
        t0 = time.monotonic()
        env, home = None, None
        if job.isolated_libs:
            home = isolated_home(self.cache_dir)
            # HOME/XDG for a plain kicad-cli, KIPR_KICAD_HOME for the kipr-tools wrapper
            env = {"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"), "KIPR_KICAD_HOME": home}
        try:
            ok, msg, rc = self.cli.run(job.cmd, opts, os.path.join(side_root, job.target), cwd=side_root, env=env)
        finally:
            if home:
                shutil.rmtree(home, ignore_errors=True)
        dt = time.monotonic() - t0
        files = sorted(os.path.relpath(os.path.join(d, f), tmp) for d, _, fs in os.walk(tmp) for f in fs)
        if job.output != "dir":
            ok = (ok or rc in job.ok_codes) and job.output in files
        elif not files:
            ok = False
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True)
            return JobResult(job.name, False, None, [], msg[-2000:] or f"exit code {rc}", dt)
        with open(os.path.join(tmp, ".kipr-job.json"), "w") as fh:
            json.dump({"job": job.name, "files": files, "message": msg[-2000:], "seconds": dt}, fh)
        try:
            os.replace(tmp, final)
        except OSError:  # another process won the race
            shutil.rmtree(tmp, ignore_errors=True)
        return JobResult(job.name, True, final, files, msg[-2000:], dt)


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
    """Compare two plot files ignoring creation dates/tool banners. None if either is missing."""
    if not a or not b or not os.path.isfile(a) or not os.path.isfile(b):
        return None
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return _VOLATILE.sub(b"", fa.read()) == _VOLATILE.sub(b"", fb.read())
