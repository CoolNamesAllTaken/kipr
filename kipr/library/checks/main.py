"""Deterministic checks of KiCad footprints/symbols changed in a library PR.

Reads OUT/manifest.json (written by the render step) plus per-item assets and
writes OUT/review.json and OUT/review.md (see docs/library.md).

  kipr library checks --out cr-out [--repo .] [--klc-utils DIR] [--site-url URL]

Runs the KLC-style rules in kicad_checks.py and, with --klc-utils, KiCad's
official KLC checkers. No network access and no secrets are needed.

OUT must be self-contained (the privileged CI job has no PR checkout), so
everything is read from OUT; --repo is optional extra context. Nothing from
OUT is ever executed, and every path taken from the manifest is confined to OUT.
Exit status is 0 whenever review.json was written, even with findings.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys

from kipr.common import kicad_cli as kicad_cli_mod
from kipr.common import sexpr

from .. import layout as layoutmod
from . import kicad_checks as kc
from . import klc_utils

REENCODED = "re-encoded"   # manifest status of a part only re-saved by a newer KiCad
SEV_ORDER = {"error": 0, "warning": 1, "info": 2}
VERDICT_ORDER = {"fail": 0, "warn": 1, "pass": 2}
GENERATOR = "deterministic checks + KLC"


# ---------------------------------------------------------------------------
# paths & io
# ---------------------------------------------------------------------------

def safe_join(base: str | None, rel: str | None) -> str | None:
    """Join a manifest-provided relative path onto base, refusing escapes."""
    if not base or not rel or not isinstance(rel, str):
        return None
    if os.path.isabs(rel) or "\x00" in rel:
        return None
    base_real = os.path.realpath(base)
    path = os.path.realpath(os.path.join(base_real, rel))
    if path != base_real and not path.startswith(base_real + os.sep):
        return None
    return path


def read_text(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def slugify(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# per-item analysis (deterministic)
# ---------------------------------------------------------------------------

class Item:
    """Everything we know about one manifest item."""

    def __init__(self, raw: dict, out_dir: str, repo: str | None):
        self.raw = raw
        self.id = raw.get("id") or f"{raw.get('kind')}:{raw.get('library')}:{raw.get('name')}"
        self.kind = raw.get("kind", "")
        self.name = raw.get("name", "")
        self.library = raw.get("library", "")
        self.status = raw.get("status", "")
        self.path = raw.get("path")
        self.slug = slugify(raw.get("slug") or f"{self.kind}__{self.library}__{self.name}")
        lr = (raw.get("line_range") or {}).get("head")
        self.file_start = lr[0] if isinstance(lr, list) and lr else None
        self.file_end = lr[1] if isinstance(lr, list) and len(lr) > 1 else None
        self.out_dir = out_dir
        self.source_text, self.source_origin = self._load_source(out_dir, repo)
        self.node = None
        self.linemap = kc.LineMap(1, self.file_start)
        self.parse_error = None
        if self.source_text:
            try:
                root = sexpr.parse(self.source_text)
                self.node = kc.find_item_node(root, self.kind, self.name)
                if self.node is not None:
                    self.linemap = kc.LineMap(self.node.line, self.file_start)
                else:
                    self.parse_error = f"could not find {self.kind} `{self.name}` in its source"
            except sexpr.ParseError as e:
                self.parse_error = f"s-expression parse error: {e}"
        self.findings: list[dict] = []
        self.checks: list[dict] = []
        self.stats = {}
        self.pads: list[dict] = []
        self.pins: list[dict] = []
        self.paired: list[tuple[Item, bool]] = []  # (other item, exact match?)
        self.klc: dict | None = None  # official checker outcome; None when it didn't run

    def _load_source(self, out_dir, repo):
        src = (self.raw.get("source") or {}).get("head")
        text = read_text(safe_join(out_dir, src))
        if text is not None:
            return text, "out"
        if repo and self.path and self.file_start:
            full = read_text(safe_join(repo, self.path))
            if full is not None:
                lines = full.splitlines(keepends=True)
                end = self.file_end or len(lines)
                # keep the file's own coordinates: root node will open at file_start
                return "\n" * (self.file_start - 1) + "".join(lines[self.file_start - 1:end]), "repo"
        return None, None

    @property
    def props_head(self) -> dict:
        return ((self.raw.get("properties") or {}).get("head")) or {}

    def add(self, fs, cs=()):
        for f in fs:
            f.setdefault("path", self.path)
        self.findings.extend(fs)
        self.checks.extend(cs)

    def analyse(self, repo: str | None, klu_dir: str | None = None, models_dir: str = layoutmod.DEFAULT_3D,
                klc_error_severity: str = "warning", upgrader: "Upgrader | None" = None):
        if self.status == "deleted":
            return
        if self.node is None:
            self.add([kc.finding("error", f"Could not analyse item: {self.parse_error or 'source not available in OUT'}.")])
            return
        repo_exists = None
        if repo:
            def repo_exists(rel):
                p = safe_join(repo, rel)
                return os.path.exists(p) if p else None
        if self.kind == "footprint":
            self.pads = kc.parse_pads(self.node)
            self.stats = kc.footprint_stats(self.node)
            models = ((self.raw.get("model3d_by_side") or {}).get("head")) or self.raw.get("model3d")
            fs, cs = kc.check_footprint(self.node, self.linemap, models, repo_exists, models_dir=models_dir)
        else:
            self.pins = kc.parse_pins(self.node)
            self.stats = kc.symbol_stats(self.node)
            fs, cs = kc.check_symbol(self.node, self.linemap)
        self.add(fs, cs)
        if klc_utils.available(klu_dir):
            res = klc_utils.run(klu_dir, self.kind, self.library, self.name, self.source_text)
            upgraded = None
            # Only when the checker can't read the original: symbols it refuses for their file
            # version, footprints it can't load (legacy `module` files). Readable originals are
            # checked as they are: on an upgraded footprint the checker misses e.g. unlocked RefDes.
            if upgrader is not None and (res.version_mismatch if self.kind == "symbol" else res.parse_failed):
                res, upgraded = self.klc_on_upgraded_copy(klu_dir, upgrader, res)
            self.add_klc(res, klc_error_severity, upgraded)
        for w in self.raw.get("warnings") or []:
            self.add([kc.finding("info", f"Render: {w}")])

    def klc_on_upgraded_copy(self, klu_dir: str, upgrader: "Upgrader", first: "klc_utils.KlcResult"):
        """The checker can't read the original: check a KiCad-upgraded temporary copy instead.
        Returns (result, upgrade record or None). The repo is never touched."""
        old = _file_version(self.source_text) or "legacy (no version)"
        why = (f"{self.kind} file version {old} is not supported by the KLC checker ({first.version_mismatch[1]})"
               if first.version_mismatch else first.error)
        text, rec, err = upgrader.upgrade(self)
        if text is None:
            return klc_utils.KlcResult(error=f"{why}, and upgrading a copy failed: {err}",
                                       stderr_tail=first.stderr_tail), None
        res = klc_utils.run(klu_dir, self.kind, self.library, self.name, text)
        if res.ok:
            res.findings = [self.map_klc_line(f) for f in res.findings]
        else:
            res.error = f"{res.error} (on a copy upgraded by KiCad {rec.get('kicad')}; original: {why})"
        return res, rec

    def map_klc_line(self, f: dict) -> dict:
        """KLC findings carry no line. One that names exactly one pin of this symbol gets that
        pin's line in the original (pins are matched by number and name); anything else stays
        line-less and is shown at the item's first line."""
        if self.kind != "symbol":
            return f
        refs = {(m.group(2), m.group(1)) for m in _PIN_REF.finditer(f.get("message") or "")}
        if len(refs) == 1:
            (num, name), = refs
            hits = [p for p in self.pins if p["number"] == num and p["name"] == name]
            if len(hits) == 1 and hits[0].get("line"):
                f["line"] = self.linemap(hits[0]["line"])
        return f

    def add_klc(self, res: "klc_utils.KlcResult", error_severity: str = "warning", upgraded: dict | None = None):
        """Record the official checker's outcome. A run that didn't really check the item is
        never a pass: it becomes klc status "error" plus a finding saying so."""
        name = "KiCad KLC checker (kicad-library-utils)"
        if res.ok:
            self.klc = {"status": "ok", "attempts": res.attempts}
            detail = f"{len(res.findings)} violation(s)"
            if upgraded:
                self.klc["upgraded"] = upgraded
                detail += (f"; checked on a KiCad {upgraded.get('kicad')}-upgraded copy "
                           f"(file version {upgraded.get('from')} → {upgraded.get('to')})")
            if res.retried_because:
                detail += f" (retried once: {res.retried_because})"
                self.klc["retried_because"] = res.retried_because
            self.add(res.findings, [kc.check(name, "fail" if res.findings else "pass", detail)])
            return
        self.klc = {"status": "error", "reason": res.error, "attempts": res.attempts}
        msg = f"KLC could not check this item: {res.error}."
        if res.stderr_tail:  # one code span per line: the viewer/report/comment render `code`, not fences
            msg += "\nChecker output (last lines):\n" + "\n".join(
                f"`{l.strip().replace('`', chr(39))}`" for l in res.stderr_tail.splitlines() if l.strip())
        self.add([kc.finding(error_severity, msg, None,
                             "The official KLC rules were not applied to this item; check it by hand "
                             "or fix what stops the checker, then re-run.")],
                 [kc.check(name, "error", res.error)])

    def det_verdict(self) -> str:
        sev = {f["severity"] for f in self.findings}
        return "fail" if "error" in sev else "warn" if "warning" in sev else "pass"


# kicad-library-utils' pinString(): "Pin <name> (<number>)", then " @ (x,y)" / " in unit n" / end
_PIN_REF = re.compile(r"Pin (\S(?:[^()]*?\S)?) \(([^()\s]+)\)(?= @ \(| in unit |[\s:;,.]|$)")
UPGRADED_SUFFIX = ".klc-upgraded"


class Upgrader:
    """KiCad-upgraded copies of item sources the KLC checker can't read (symbol files at another
    file version, legacy footprints).

    With kicad-cli (the unprivileged CI job runs in the KiCad image) the copy is made with
    `kicad-cli sym upgrade` / `fp upgrade` and saved next to the item's source in OUT
    (items/<slug>/head.klc-upgraded.kicad_sym|.kicad_mod + .json). Without it (the privileged publish job,
    which never runs KiCad on PR data) a saved copy is used if its .json matches the source's
    sha256. Either way it is only data for the KLC checker, like the source itself."""

    def __init__(self, kicad_cli: str | None):
        self.exe = kicad_cli
        self.kicad = kicad_cli_mod.version(kicad_cli) if kicad_cli else None
        if kicad_cli and not self.kicad:
            self.exe = None

    def _paths(self, item: Item):
        rel = (item.raw.get("source") or {}).get("head")
        if item.source_origin != "out" or not isinstance(rel, str):
            return None, None
        stem, ext = os.path.splitext(rel)
        return safe_join(item.out_dir, stem + UPGRADED_SUFFIX + ext), safe_join(item.out_dir, stem + UPGRADED_SUFFIX + ".json")

    def upgrade(self, item: Item) -> tuple[str | None, dict, str]:
        """(upgraded text or None, record {from, to, kicad}, error)."""
        sha = hashlib.sha256(item.source_text.encode("utf-8")).hexdigest()
        copy, meta = self._paths(item)
        if copy and meta and os.path.isfile(copy) and os.path.isfile(meta):
            try:
                with open(meta, encoding="utf-8") as fh:
                    rec = json.load(fh)
                if isinstance(rec, dict) and rec.get("source_sha256") == sha:
                    text = read_text(copy)
                    if text:
                        return text, {k: str(rec.get(k)) for k in ("from", "to", "kicad")}, ""
            except (OSError, ValueError):
                pass
        if not self.exe:
            return None, {}, "kicad-cli is not available here (and no upgraded copy was saved with the item)"
        text, err = kicad_cli_mod.upgrade_text(self.exe, item.kind, item.source_text, f"{item.name}.kicad_mod")
        if text is None:
            return None, {}, err
        rec = {"from": _file_version(item.source_text) or "legacy (no version)", "to": _file_version(text),
               "kicad": self.kicad}
        if copy and meta:
            try:
                with open(copy, "w", encoding="utf-8") as fh:
                    fh.write(text)
                with open(meta, "w", encoding="utf-8") as fh:
                    json.dump({**rec, "source_sha256": sha}, fh)
            except OSError:
                pass  # the copy is a convenience for the publish job; the check itself has the text
        return text, rec, ""


def _file_version(text: str) -> str | None:
    m = re.search(r"\(version (\d+)\)", text or "")
    return m.group(1) if m else None


def pair_items(items: list[Item]) -> None:
    """Link symbols to footprints in this PR via the symbol's Footprint property."""
    fps = {i.id: i for i in items if i.kind == "footprint" and i.status != "deleted"}
    by_name = {}
    for f in fps.values():
        by_name.setdefault(f.name, []).append(f)
    for s in items:
        if s.kind != "symbol" or s.status == "deleted":
            continue
        ref = (s.props_head.get("Footprint") or "").strip()
        if not ref and s.node is not None:
            ref = next((p.atom(1, "") for p in s.node.children("property") if p.atom(0) == "Footprint"), "")
        if not ref or ":" not in ref:
            continue
        lib, name = ref.split(":", 1)
        exact = fps.get(f"footprint:{lib}:{name}")
        if exact:
            s.paired.append((exact, True))
            exact.paired.append((s, True))
            continue
        # near misses: same name in a different library, or a variant of a PR footprint's name
        cands = list(by_name.get(name, []))
        if not cands:
            cands = [f for f in fps.values() if name.startswith(f.name) or f.name.startswith(name)]
        for f in cands:
            s.paired.append((f, False))
            f.paired.append((s, False))
            line = None
            if s.node is not None:
                pn = next((p for p in s.node.children("property") if p.atom(0) == "Footprint"), None)
                line = s.linemap(pn.line) if pn else None
            s.add([kc.finding(
                "warning",
                f"`Footprint` property is `{ref}`, but this PR adds `{f.library}:{f.name}`. "
                "The default footprint does not point at the footprint added alongside this symbol.",
                line, f"Set Footprint to `{f.library}:{f.name}` if that is the intended package.",
                category="klc")])


def cross_check_pairs(items: list[Item]) -> None:
    for s in items:
        if s.kind != "symbol":
            continue
        for f, _exact in s.paired:
            if not f.pads and f.node is None:
                continue
            fs, cs = kc.check_pairing(s.pins, f.pads, s.id, f.id, None)
            s.add(fs, cs)
            f.checks.extend(c for c in cs)


def datasheet_ref(item: Item, out_dir: str) -> str | None:
    """The item's datasheet: the local copy the render step put in OUT, else its URL."""
    ds = item.raw.get("datasheet") or {}
    rel = ds.get("file")
    if isinstance(rel, str) and (p := safe_join(out_dir, rel)) and os.path.isfile(p):
        return rel
    url = ds.get("url")
    return url if isinstance(url, str) and url else None


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------

def reencoded_phrase(items) -> str:
    """'3 symbols re-encoded by KiCad, no changes' for a list of Items (or manifest dicts)."""
    kinds: dict[str, int] = {}
    for i in items:
        k = i.kind if isinstance(i, Item) else str(i.get("kind") or "part")
        kinds[k] = kinds.get(k, 0) + 1
    what = " and ".join(f"{n} {k}{'s' if n != 1 else ''}" for k, n in sorted(kinds.items()))
    return f"{what} re-encoded by KiCad, no changes"


def det_summary(item: Item) -> str:
    n = {s: sum(f["severity"] == s for f in item.findings) for s in SEV_ORDER}
    if item.status == "deleted":
        return "Deleted in this PR; nothing to review."
    return (f"Deterministic checks: {n['error']} error(s), {n['warning']} warning(s), {n['info']} info. "
            + ("" if not item.paired else "Cross-checked against " + ", ".join(f"`{o.id}`" for o, _ in item.paired) + "."))


def item_entry(item: Item, verdict: str, summary: str, datasheet_used: str | None) -> dict:
    # severity first; within a severity, line-cited findings (actionable) before uncited ones
    findings = sorted(item.findings, key=lambda f: (SEV_ORDER.get(f["severity"], 3), f.get("line") is None, f.get("line") or 0))
    e = {"verdict": verdict, "summary": summary, "datasheet_used": datasheet_used,
         "findings": findings, "checks": item.checks}
    if item.klc is not None:
        e["klc"] = item.klc  # additive: {"status": "ok"|"error", "reason"?, "attempts", "retried_because"?}
    return e


def _md_cell(s: str, limit: int = 160) -> str:
    s = re.sub(r" \(\[[A-Z]\d+\.\d+\]\(https://klc\.kicad\.org/[^)]*\)\)", "", s or "")  # KLC rule links
    s = re.sub(r"\s+", " ", s).replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render_markdown(review: dict, items: list[Item], site_url: str | None) -> str:
    icon = {"pass": "✅ pass", "warn": "⚠️ warn", "fail": "❌ fail"}
    sev_icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
    lines = ["## Component review", "", review["summary_markdown"], "",
             "| Component | Status | Verdict | Top findings |", "|---|---|---|---|"]
    order = sorted(items, key=lambda i: (VERDICT_ORDER[review["items"][i.id]["verdict"]], i.id))
    for it in order:
        e = review["items"][it.id]
        top = [f for f in e["findings"] if f["severity"] != "info"][:3] or e["findings"][:1]
        cells = "<br>".join(f"{sev_icon[f['severity']]} {_md_cell(f['message'])}"
                            + (f" (L{f['line']})" if f.get("line") else "") for f in top) or "—"
        more = len(e["findings"]) - len(top)
        if more > 0:
            cells += f"<br>… +{more} more"
        name = f"`{it.library}:{it.name}` ({it.kind})"
        if site_url:
            name = f"[{name}]({site_url.rstrip('/')}/#{it.slug})"
        lines.append(f"| {name} | {it.status} | {icon[e['verdict']]} | {cells} |")
    if review.get("pr_findings"):
        lines += ["", "**PR-level findings**", ""]
        lines += [f"- {sev_icon[f['severity']]} {_md_cell(f['message'], 300)}" for f in review["pr_findings"]]
    lines += ["", f"<sub>Checks: {review['generator']}. Line numbers refer to the PR head.</sub>"]
    md = "\n".join(lines) + "\n"
    return md if len(md) < 60000 else md[:59000] + "\n\n… (truncated; see review.json)\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def add_arguments(ap):
    ap.add_argument("--out", required=True, help="render output dir containing manifest.json")
    ap.add_argument("--repo", default=None, help="optional repo checkout (3D model existence fallback; never required)")
    ap.add_argument("--site-url", default=os.environ.get("CR_SITE_URL"), help="viewer URL to link from review.md")
    ap.add_argument("--klc-utils", default=os.environ.get("CR_KLC_UTILS"),
                    help="path to a kicad-library-utils checkout; runs its KLC checkers too (optional)")
    ap.add_argument("--kicad-cli", default=None,
                    help="kicad-cli for upgrading symbol files the KLC checker can't read (default: "
                         "$KIPR_KICAD_CLI, $KICAD_CLI or PATH; optional)")
    ap.add_argument("--klc-error-severity", choices=tuple(SEV_ORDER),
                    default=os.environ.get("CR_KLC_ERROR_SEVERITY") or "warning",
                    help="severity of the 'KLC could not check this item' finding (default warning, "
                         "i.e. verdict warn; also CR_KLC_ERROR_SEVERITY)")
    ap.add_argument("--only", action="append", help="only check item ids matching this substring (repeatable)")
    # accepted and ignored so older callers keep working
    ap.add_argument("--no-llm", "--no-download", action="store_true", help=argparse.SUPPRESS)
    layoutmod.add_arguments(ap, models_only=True)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="kipr library checks", description=__doc__.split("\n\n")[0])
    add_arguments(ap)
    return ap.parse_args(argv)


def run(args) -> int:
    out_dir = os.path.abspath(args.out)
    manifest_path = os.path.join(out_dir, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"checks: cannot read {manifest_path}: {e}", file=sys.stderr)
        return 2
    if manifest.get("schema") != 1:
        print(f"checks: warning: manifest schema {manifest.get('schema')!r}, expected 1", file=sys.stderr)
    repo = os.path.abspath(args.repo) if args.repo else None

    items = [Item(r, out_dir, repo) for r in manifest.get("items") or [] if isinstance(r, dict)]
    if args.only:
        items = [i for i in items if any(s in i.id for s in args.only)]
    # parts only re-encoded by a newer KiCad have no change to review: no checks, no verdict
    reencoded = [i for i in items if i.status == REENCODED]
    items = [i for i in items if i.status != REENCODED]
    klu_dir = args.klc_utils if klc_utils.available(args.klc_utils) else None
    if args.klc_utils and not klu_dir:
        print(f"checks: warning: --klc-utils {args.klc_utils} is not a kicad-library-utils checkout; skipping KLC checker",
              file=sys.stderr)
    if args.klc_error_severity not in SEV_ORDER:
        print(f"checks: bad KLC error severity {args.klc_error_severity!r} (CR_KLC_ERROR_SEVERITY); "
              f"use one of {', '.join(SEV_ORDER)}", file=sys.stderr)
        return 2
    models_dir = layoutmod.Layout(models=getattr(args, "lib_3d", layoutmod.DEFAULT_3D)).models
    upgrader = Upgrader(kicad_cli_mod.find(args.kicad_cli)) if klu_dir else None
    for it in items:
        it.analyse(repo, klu_dir, models_dir, args.klc_error_severity, upgrader)
    pr_findings = [
        {"severity": "warning", "category": "3d-model", "path": p, "line": None,
         "message": f"3D model file `{p}` is added/changed in this PR but no footprint references it.",
         "suggestion": "Reference it from the footprint it belongs to, or drop it from the PR."}
        for p in manifest.get("unreferenced_changed_3d_files") or [] if isinstance(p, str)]
    pair_items(items)
    cross_check_pairs(items)

    review_items = {it.id: item_entry(it, it.det_verdict(), det_summary(it), datasheet_ref(it, out_dir))
                    for it in items}
    n = {v: sum(e["verdict"] == v for e in review_items.values()) for v in VERDICT_ORDER}
    summary = [f"Reviewed **{len(items)}** item(s): {n['fail']} fail, {n['warn']} warn, {n['pass']} pass."]
    if not klu_dir:
        summary.append("The KiCad KLC checker (kicad-library-utils) was not run.")
    else:
        unchecked = [it for it in items if (it.klc or {}).get("status") == "error"]
        if unchecked:
            summary.append(f"**KLC could not check {len(unchecked)} item(s)**; see their findings.")
    if pr_findings:
        summary.append(f"{len(pr_findings)} PR-level finding(s) (unreferenced 3D model files).")
    if reencoded:
        summary.append(f"{reencoded_phrase(reencoded)} (not reviewed).")
    review = {"schema": 1, "generator": GENERATOR if klu_dir else "deterministic checks", "generated_at": _now(),
              "summary_markdown": " ".join(summary), "items": review_items,
              # additive to the contract: findings not tied to one item
              "pr_findings": pr_findings}

    with open(os.path.join(out_dir, "review.json"), "w", encoding="utf-8") as f:
        json.dump(review, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, "review.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(review, items, args.site_url))
    print(f"checks: wrote {os.path.join(out_dir, 'review.json')} ({len(items)} items; {review['summary_markdown']})")
    return 0


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
