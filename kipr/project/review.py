"""`kipr project`: build OUT/project-review.json (see docs/CONTRACT-project.md) for a base..head range."""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import tempfile
import time
import traceback
from dataclasses import dataclass, field

from .. import __version__
from . import diff_net, diff_pcb, diff_sch, discover, export, pcb, sch
from ..common import kicad_cli as kicad_cli_mod
from ..common.git import Git
from ..common.kicad_cli import KicadCli

SIDES = ("base", "head")

# Violation types that compare against the global libraries; meaningless with --fast-checks.
LIBRARY_CHECKS = {"lib_footprint_issues", "lib_footprint_mismatch", "footprint_link_issues",
                  "lib_symbol_issues", "lib_symbol_mismatch"}


def default_cache_dir() -> str:
    base = os.environ.get("KIPR_CACHE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "kipr")
    return os.path.join(base, "project-exports")


def layer_file(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


@dataclass
class Side:
    """One revision of one project, checked out under `root` (repo-relative layout)."""
    name: str  # base | head
    sha: str
    root: str | None = None
    blobs: dict = field(default_factory=dict)
    pro: str | None = None  # paths relative to root
    sch: str | None = None
    pcb: str | None = None
    board: pcb.Board | None = None
    schem: sch.SchematicSet | None = None
    futures: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)


class ProjectReview:
    def __init__(self, git: Git, proj: discover.Project, slug: str, out: str, tmp: str, shas: dict,
                 exporter: export.Exporter | None, step: bool, glb: bool, log, fast_checks: bool = False):
        self.git, self.proj, self.slug, self.out = git, proj, slug, out
        self.tmp, self.exporter, self.step, self.glb, self.log = tmp, exporter, step, glb, log
        self.fast_checks = fast_checks
        self.errors: list[str] = []
        self.timings: dict[str, float] = {}
        self.sides = {s: Side(s, shas[s]) for s in SIDES}
        self.pdir = proj.path

    # -- helpers ---------------------------------------------------------------------------
    def rel(self, *parts) -> str:
        return posixpath.join("p", self.slug, *parts)

    def err(self, msg: str):
        self.errors.append(msg)
        self.log(f"  ! {self.slug}: {msg}")

    def copy(self, src: str, rel: str) -> str:
        dst = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        return rel

    # -- stages ----------------------------------------------------------------------------
    def checkout_and_parse(self):
        t0 = time.monotonic()
        for s in self.sides.values():
            pro = self.proj.base_pro if s.name == "base" else self.proj.head_pro
            if pro is None:
                continue
            s.root = os.path.join(self.tmp, self.slug, s.name)
            try:
                paths = discover.checkout_paths(self.git, s.sha, self.proj, s.name)
                self.git.extract(s.sha, paths, s.root)
                s.blobs = self.git.ls_tree(s.sha, paths) if paths else {}
            except Exception as e:  # noqa: BLE001
                self.err(f"cannot check out {s.name}: {e}")
                s.root = None
                continue
            s.pro = pro
            stem = pro[: -len(".kicad_pro")]
            if os.path.isfile(os.path.join(s.root, stem + ".kicad_sch")):
                s.sch = stem + ".kicad_sch"
            if os.path.isfile(os.path.join(s.root, stem + ".kicad_pcb")):
                s.pcb = stem + ".kicad_pcb"
        self.timings["checkout"] = time.monotonic() - t0

    def start_exports(self):
        if self.exporter is None:
            return
        for s in self.sides.values():
            if s.root is None:
                continue
            layers = []
            if s.pcb:
                layers = self._quick_layers(os.path.join(s.root, s.pcb))
            for job in export.side_jobs(s.pcb, s.sch, layers, step=self.step, glb=self.glb,
                                        fast_checks=self.fast_checks):
                s.futures[job.name] = self.exporter.submit(job, s.root, s.blobs, self.pdir)

    @staticmethod
    def _quick_layers(path: str) -> list[str]:
        """Layer names from the board's (layers ...) header without a full parse."""
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(200_000)
        m = re.search(r"\(layers\s*((?:\(\s*\d+\s+\"[^\"]+\"[^()]*\)\s*)+)\)", head)
        names = re.findall(r"\(\s*\d+\s+\"([^\"]+)\"", m.group(1)) if m else []
        return names or ["F.Cu", "B.Cu", "F.Mask", "B.Mask", "F.SilkS", "B.SilkS", "F.Paste", "B.Paste",
                         "Edge.Cuts"]

    def parse(self):
        t0 = time.monotonic()
        for s in self.sides.values():
            if s.root is None:
                continue
            if s.pcb:
                try:
                    with open(os.path.join(s.root, s.pcb), encoding="utf-8", errors="replace") as fh:
                        s.board = pcb.load(fh.read())
                except Exception as e:  # noqa: BLE001
                    self.err(f"cannot parse {s.name} board {s.pcb}: {e}")
            if s.sch:
                pdir_abs = os.path.join(s.root, self.pdir)

                def read(rel, _d=pdir_abs):
                    p = os.path.join(_d, rel)
                    if not os.path.isfile(p):
                        return None
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        return fh.read()

                try:
                    s.schem = sch.load_hierarchy(read, posixpath.basename(s.sch), self.proj.name)
                    for e in s.schem.errors:
                        self.err(f"{s.name} schematic: {e}")
                except Exception as e:  # noqa: BLE001
                    self.err(f"cannot parse {s.name} schematic {s.sch}: {e}")
        self.timings["parse"] = time.monotonic() - t0

    def wait_exports(self):
        t0 = time.monotonic()
        for s in self.sides.values():
            for name, fut in s.futures.items():
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    r = export.JobResult(name, False, None, [], f"{e}")
                s.results[name] = r
                if not r.ok:
                    self.err(f"kicad-cli {name} failed for {s.name}: {r.message.strip()[-400:]}")
        self.timings["export_wait"] = time.monotonic() - t0

    def result_file(self, side: Side, job: str, name: str) -> str | None:
        r = side.results.get(job)
        if r is None or not r.ok or name not in r.files:
            return None
        return os.path.join(r.dir, name)

    # -- sections --------------------------------------------------------------------------
    def schematic(self):
        b, h = self.sides["base"], self.sides["head"]
        if b.schem is None and h.schem is None:
            return None
        sheets = diff_sch.diff_schematics(b.schem, h.schem)
        svg_maps = {}
        for s in (b, h):
            r = s.results.get("sch_svg")
            if s.schem is not None and r is not None and r.ok:
                stem = posixpath.basename(s.sch)[: -len(".kicad_sch")]
                svg_maps[s.name] = (r, export.sheet_file_map(r.files, stem, s.schem.sheets))
                missing = [x.id for x in s.schem.sheets if x.id not in svg_maps[s.name][1]]
                if missing:
                    self.err(f"no kicad-cli SVG found for {s.name} sheet(s): {', '.join(missing)}")
        for sh in sheets:
            for side in SIDES:
                path = None
                if side in svg_maps and sh[f"{side}_file"] is not None:
                    r, m = svg_maps[side]
                    f = m.get(sh["id"])
                    if f:
                        path = self.copy(os.path.join(r.dir, f), self.rel("sch", side, sh["id"] + ".svg"))
                sh[side] = path
            if sh["status"] == "unchanged" and sh["base"] and sh["head"]:
                same = export.same_content(os.path.join(self.out, sh["base"]), os.path.join(self.out, sh["head"]))
                if same is False:
                    sh["status"] = "modified"
                    sh["changes"].append({"kind": "other", "what": "modified", "bbox_mm": None,
                                          "detail": "rendered page differs (e.g. page count or text variables in the title block)"})
        return {"sheets": sheets}

    def pcb_section(self, changes):
        b, h = self.sides["base"], self.sides["head"]
        if b.board is None and h.board is None:
            return None
        ref = h.board or b.board
        eb = ref.edge_box
        board = {
            "size_mm": [round(eb[2] - eb[0], 4), round(eb[3] - eb[1], 4)] if eb else None,
            "origin_mm": [round(eb[0], 4), round(eb[1], 4)] if eb else None,
            "gerber_origin_mm": [0, 0],
            "thickness_mm": ref.thickness,
            "copper_layers": len(ref.copper),
            "mask_color": (ref.stackup.get("F.Mask") or {}).get("color", "").lower() or None,
            "silk_color": (ref.stackup.get("F.SilkS") or {}).get("color", "").lower() or None,
            "finish": ref.stackup.get("copper_finish"),
        }
        for s in (b, h):
            if s.board is not None:
                sb = s.board.edge_box
                board[s.name] = {"size_mm": [round(sb[2] - sb[0], 4), round(sb[3] - sb[1], 4)] if sb else None,
                                 "origin_mm": [round(sb[0], 4), round(sb[1], 4)] if sb else None,
                                 "thickness_mm": s.board.thickness, "copper_layers": len(s.board.copper)}
        # files per side
        files = {}
        for s in (b, h):
            if s.board is None:
                continue
            stem = posixpath.basename(s.pcb)[: -len(".kicad_pcb")]
            fs = {"gerber": {}, "svg": {}, "drill": {}}
            r = s.results.get("gerbers")
            if r is not None and r.ok:
                m = export.layer_file_map(r.files, stem, s.board.layers, ".gbr")
                renames = {}
                for lay, f in m.items():
                    fs["gerber"][lay] = self.copy(os.path.join(r.dir, f), self.rel("pcb", s.name, layer_file(lay) + ".gbr"))
                    renames[posixpath.basename(f)] = layer_file(lay) + ".gbr"
                job = next((f for f in r.files if f.endswith(".gbrjob")), None)
                if job:
                    fs["gbrjob"] = self._write_gbrjob(os.path.join(r.dir, job), renames, s.name)
            r = s.results.get("pcb_svg")
            if r is not None and r.ok:
                for lay, f in export.layer_file_map(r.files, stem, s.board.layers, ".svg").items():
                    fs["svg"][lay] = self.copy(os.path.join(r.dir, f), self.rel("pcb", s.name, layer_file(lay) + ".svg"))
            r = s.results.get("drill")
            if r is not None and r.ok:
                for f in r.files:
                    mm = re.search(r"-(PTH|NPTH)\.drl$", f) or re.search(r"()\.drl$", f)
                    if mm:
                        kind = mm.group(1) or "PTH"
                        fs["drill"][kind] = self.copy(os.path.join(r.dir, f), self.rel("pcb", s.name, f"{kind}.drl"))
            pos = self.result_file(s, "pos", "pos.csv")
            if pos:
                fs["pos"] = self.copy(pos, self.rel("pcb", s.name, "pos.csv"))
            files[s.name] = fs
        touched = diff_pcb.touched_layers(changes)
        order = []
        for s in (h, b):
            if s.board is not None:
                for ly in s.board.layers:
                    if ly["name"] not in order:
                        order.append(ly["name"])
        present = {s.name: {ly["name"] for ly in s.board.layers} if s.board else set() for s in (b, h)}
        layers = []

        def entry(lid, kind, side, sources):
            st_sem = lid in touched
            e = {"id": lid, "kind": kind, "side": side}
            inb, inh = lid in present["base"] or kind == "drill", lid in present["head"] or kind == "drill"
            if b.board is None or not inb:
                status = "added"
            elif h.board is None or not inh:
                status = "removed"
            else:
                same = None
                bf, hf = sources("base"), sources("head")
                if bf and hf:
                    same = export.same_content(os.path.join(self.out, bf), os.path.join(self.out, hf))
                status = ("modified" if not same else "unchanged") if same is not None else (
                    "modified" if st_sem else "unchanged")
            e["status"] = status
            e["semantic_changes"] = sum(1 for c in changes if lid in (c.get("layers") or [])
                                        or lid in (c.get("holes") or []))
            for sn in SIDES:
                fs = files.get(sn)
                if fs is None or (sn == "base" and status == "added") or (sn == "head" and status == "removed"):
                    e[sn] = None
                elif kind == "drill":
                    e[sn] = {"gerber": fs["drill"].get(lid), "svg": None}
                else:
                    e[sn] = {"gerber": fs["gerber"].get(lid), "svg": fs["svg"].get(lid)}
            return e

        for lid in order:
            layers.append(entry(lid, pcb.layer_kind(lid), pcb.layer_side(lid),
                                lambda sn, _l=lid: (files.get(sn) or {}).get("gerber", {}).get(_l)))
        for lid in ("PTH", "NPTH"):
            has = any(lid in (files.get(sn) or {}).get("drill", {}) for sn in SIDES) or any(
                s.board is not None and any(lid in f.holes for f in s.board.footprints) for s in (b, h)) or (
                lid == "PTH" and any(s.board is not None and any(i.kind == "via" for i in s.board.items)
                                     for s in (b, h)))
            if has:
                layers.append(entry(lid, "drill", "none",
                                    lambda sn, _l=lid: (files.get(sn) or {}).get("drill", {}).get(_l)))
        gbrjob = {sn: (files.get(sn) or {}).get("gbrjob") for sn in SIDES}
        pos = {sn: (files.get(sn) or {}).get("pos") for sn in SIDES}
        return {"board": board, "layers": layers, "gbrjob": gbrjob, "pos": pos, "changes": changes}

    def _write_gbrjob(self, src: str, renames: dict, side: str) -> str:
        rel = self.rel("pcb", side, "board.gbrjob")
        try:
            with open(src, encoding="utf-8") as fh:
                data = json.load(fh)
            for fa in data.get("FilesAttributes", []):
                fa["Path"] = renames.get(fa.get("Path"), fa.get("Path"))
            dst = os.path.join(self.out, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except (OSError, ValueError) as e:
            self.err(f"cannot rewrite {side} gbrjob: {e}")
            return self.copy(src, rel)
        return rel

    def pcba3d(self, components):
        b, h = self.sides["base"], self.sides["head"]
        if b.board is None and h.board is None:
            return None
        res = {}
        for s in (b, h):
            d = {}
            for job, fname, ext in (("glb", "board.glb", "glb"), ("step", "board.step", "step")):
                f = self.result_file(s, job, fname)
                if f:
                    d[ext] = self.copy(f, self.rel("3d", f"{s.name}.{ext}"))
            res[s.name] = d or None
        res["frame"] = {"units": "m", "up": "+y", "x": "kicad_x / 1000", "z": "kicad_y / 1000",
                        "origin_mm": [0, 0]}
        res["components"] = components
        return res

    def bom(self):
        b, h = self.sides["base"], self.sides["head"]
        if b.schem is None and h.schem is None:
            return None
        res = diff_sch.diff_bom(b.schem.components() if b.schem else None,
                                h.schem.components() if h.schem else None)
        res["csv"] = {}
        for s in (b, h):
            f = self.result_file(s, "bom", "bom.csv")
            res["csv"][s.name] = self.copy(f, self.rel("bom", f"{s.name}.csv")) if f else None
        return res

    def netlist(self):
        b, h = self.sides["base"], self.sides["head"]
        nets, files = {}, {}
        for s in (b, h):
            f = self.result_file(s, "netlist", "netlist.net")
            if f:
                files[s.name] = self.copy(f, self.rel("netlist", f"{s.name}.net"))
                try:
                    with open(f, encoding="utf-8", errors="replace") as fh:
                        nets[s.name] = diff_net.parse_kicad_netlist(fh.read())
                except Exception as e:  # noqa: BLE001
                    self.err(f"cannot parse {s.name} netlist: {e}")
        exists = {s.name: s.root is not None for s in (b, h)}
        source = "schematic"
        if not all(n in nets or not exists[n] for n in SIDES) or not nets:
            boards = {s.name: s.board for s in (b, h) if s.board is not None}
            if not boards:
                return None
            source = "board"
            nets = {n: diff_net.netlist_from_board(bd) for n, bd in boards.items()}
        res = diff_net.diff_netlists(nets.get("base"), nets.get("head"))
        res["source"] = source
        res["files"] = {n: files.get(n) for n in SIDES}
        res["base_count"] = len(nets.get("base") or {})
        res["head_count"] = len(nets.get("head") or {})
        return res

    def checks(self):
        out = {}
        for kind in ("erc", "drc"):
            parsed, report = {}, {}
            ok, scaled = True, False
            for s in self.sides.values():
                if s.root is None or (kind == "erc" and not s.sch) or (kind == "drc" and not s.pcb):
                    parsed[s.name] = []
                    report[s.name] = None
                    continue
                f = self.result_file(s, kind, f"{kind}.json")
                if f is None:
                    ok = False
                    report[s.name] = None
                    continue
                report[s.name] = self.copy(f, self.rel("checks", f"{kind}.{s.name}.json"))
                try:
                    with open(f, encoding="utf-8") as fh:
                        parsed[s.name] = diff_net.parse_report(fh.read(), kind)
                    if self.fast_checks:
                        parsed[s.name] = [v for v in parsed[s.name] if v["type"] not in LIBRARY_CHECKS]
                    if kind == "erc" and s.schem is not None and s.schem.sheets:
                        page = max(max(x.size_mm) for x in s.schem.sheets)
                        if diff_net.fix_erc_scale(parsed[s.name], page):
                            scaled = True
                except (ValueError, OSError) as e:
                    self.err(f"cannot read {s.name} {kind.upper()} report: {e}")
                    ok = False
            if not ok or all(report[n] is None for n in SIDES):
                out[kind] = None
                continue
            d = diff_net.check_delta(parsed.get("base"), parsed.get("head"))
            d["report"] = report
            if scaled:
                d["pos_scale_fixed"] = 100
            if self.fast_checks:
                d["libraries"] = "project"  # global libraries not loaded, library checks dropped
            out[kind] = d
        return out

    # -- main ------------------------------------------------------------------------------
    def run(self) -> dict:
        t0 = time.monotonic()
        self.checkout_and_parse()
        self.start_exports()
        self.parse()
        b, h = self.sides["base"], self.sides["head"]
        t1 = time.monotonic()
        pcb_changes, components = [], []
        if b.board is not None or h.board is not None:
            empty = pcb.Board([], [], {}, [], [], [], {}, None, {}, None, {})
            pcb_changes, components = diff_pcb.diff_boards(b.board or empty, h.board or empty)
        self.timings["diff"] = time.monotonic() - t1
        self.wait_exports()
        t2 = time.monotonic()
        doc = {"slug": self.slug, "name": self.proj.name, "path": self.proj.path, "status": self.proj.status,
               "reasons": self.proj.reasons}
        schematic = self.schematic()
        pcbs = self.pcb_section(pcb_changes)
        p3d = self.pcba3d(components)
        bom = self.bom()
        net = self.netlist()
        checks = self.checks()
        self.timings["assemble"] = time.monotonic() - t2
        self.timings["total"] = time.monotonic() - t0
        comp_count = {"added": 0, "removed": 0, "moved": 0, "changed": 0}
        for c in components:
            if c["status"] in ("moved", "rotated"):
                comp_count["moved"] += 1
            elif c["status"] in comp_count:
                comp_count[c["status"]] += 1
        if not components and bom:  # no board: count from the schematic
            for r in bom["rows"]:
                if r["status"] in comp_count:
                    comp_count[r["status"]] += 1
        doc["summary"] = {
            "sheets_changed": sum(1 for s in (schematic or {}).get("sheets", []) if s["status"] != "unchanged"),
            "layers_changed": sum(1 for ly in (pcbs or {}).get("layers", []) if ly["status"] != "unchanged"),
            "components": comp_count,
            "nets_changed": len((net or {}).get("changes", [])),
            "erc": {"new": len(checks["erc"]["new"]), "fixed": len(checks["erc"]["fixed"])} if checks.get("erc") else None,
            "drc": {"new": len(checks["drc"]["new"]), "fixed": len(checks["drc"]["fixed"])} if checks.get("drc") else None,
        }
        doc.update({"schematic": schematic, "pcb": pcbs, "pcba3d": p3d, "bom": bom, "netlist": net,
                    "checks": checks, "info": self.info(), "errors": self.errors,
                    "timings_s": {k: round(v, 3) for k, v in self.timings.items()},
                    "exports": self.export_stats()})
        return doc

    def info(self):
        out = {}
        for s in self.sides.values():
            tb = {}
            if s.schem is not None and s.schem.sheets:
                tb.update(s.schem.sheets[0].title)
            if s.board is not None:
                tb.update({k: v for k, v in s.board.title.items() if v})
            out[s.name] = tb or None
        return out

    def export_stats(self):
        out = {}
        for s in self.sides.values():
            for name, r in s.results.items():
                out.setdefault(name, {})[s.name] = {"ok": r.ok, "cached": r.cached, "seconds": round(r.seconds, 2)}
        return out


def unique_slug(name: str, used: set) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "project"
    s, i = base, 2
    while s in used:
        s, i = f"{base}-{i}", i + 1
    used.add(s)
    return s


def run(repo: str, base: str, head: str, out: str, patterns=None, kicad_cli: str | None = None,
        jobs: int = 4, cache_dir: str | None = None, step: bool = False, glb: bool = True,
        repo_url: str | None = None, no_export: bool = False, fast_checks: bool = False, log=print) -> dict:
    git = Git(repo)
    shas = {"base": git.rev(base), "head": git.rev(head)}
    os.makedirs(out, exist_ok=True)
    top_errors = []
    cli = None
    exe = None if no_export else kicad_cli_mod.find(kicad_cli)
    if exe:
        cli = KicadCli(os.path.abspath(exe))
    elif not no_export:
        top_errors.append("kicad-cli not found: no SVG/gerber/3D exports, netlist taken from the board, "
                          "no ERC/DRC (use --kicad-cli or $KIPR_KICAD_CLI)")
    exporter = export.Exporter(cli, cache_dir or default_cache_dir(), jobs) if cli else None
    projects = discover.find_projects(git, shas["base"], shas["head"], patterns)
    log(f"kipr project: {len(projects)} changed project(s) between {shas['base'][:7]} and {shas['head'][:7]}")
    url = repo_url or git.github_url()
    doc = {
        "version": 1,
        "tool": {"name": "kipr", "version": __version__, "kicad": cli.version if cli else None},
        "base": {"sha": shas["base"], "ref": base, "short": shas["base"][:7]},
        "head": {"sha": shas["head"], "ref": head, "short": shas["head"][:7]},
        "repo": {"url": url, "blob": f"{url}/blob/{{sha}}/{{path}}" if url else None},
        "projects": [],
        "errors": top_errors,
    }
    tmp = tempfile.mkdtemp(prefix="kipr-project-")
    used: set = set()
    try:
        for proj in projects:
            slug = unique_slug(proj.name, used)
            log(f"- {proj.path or '.'} ({proj.status}) -> p/{slug}")
            pr = ProjectReview(git, proj, slug, out, tmp, shas, exporter, step, glb, log, fast_checks)
            try:
                doc["projects"].append(pr.run())
            except Exception as e:  # noqa: BLE001  never crash the whole review for one project
                traceback.print_exc()
                doc["projects"].append({"slug": slug, "name": proj.name, "path": proj.path, "status": proj.status,
                                        "summary": None, "schematic": None, "pcb": None, "pcba3d": None,
                                        "bom": None, "netlist": None, "checks": {"erc": None, "drc": None},
                                        "errors": pr.errors + [f"internal error: {e!r}"]})
    finally:
        if exporter:
            exporter.close()
        shutil.rmtree(tmp, ignore_errors=True)
    with open(os.path.join(out, "project-review.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    return doc

