"""Shared helpers for the project-review CI tools.

In the privileged publish job, project-review.json comes from a pull-request run and is
UNTRUSTED: every value is type-checked here and every string is escaped (kipr.library.ci.common's
md_* helpers: no raw HTML, @-mentions or remote images) before it reaches markdown or the API.
"""
from __future__ import annotations

from pathlib import Path

from kipr.library.ci.common import (SHA_RE, GitHub, check_repo, check_sha, load_json, log, md_inline,  # noqa: F401
                                    parse_pr_number, safe_http_url, safe_repo_path, truncate, write_outputs)

MARKER = "<!-- kipr-project-review -->"
CHECK_KINDS = ("erc", "drc")
STATUS_ICON = {"added": "🆕", "removed": "🗑️", "modified": "✏️", "unchanged": "·"}
SEVERITY_ICON = {"error": "🔴", "warning": "🟠"}


def code(v, maxlen: int = 120) -> str:
    """Identifier as <code>, escaped like any other text (a markdown code span would keep `<`, `|`)."""
    s = md_inline(v, maxlen)
    return f"<code>{s}</code>" if s else ""


def d(v) -> dict:
    return v if isinstance(v, dict) else {}


def lst(v) -> list:
    return v if isinstance(v, list) else []


def num(v) -> int:
    """A non-negative int from untrusted data (bools and junk count as 0)."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def text(v) -> str:
    return v if isinstance(v, str) else ""


def load_review(path: Path, max_bytes: int = 50_000_000) -> dict:
    """project-review.json with `projects` normalized to a list of dicts. Raises ValueError."""
    doc = load_json(Path(path), max_bytes=max_bytes)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: not a project-review.json object")
    doc["projects"] = [p for p in lst(doc.get("projects")) if isinstance(p, dict)]
    return doc


def kind_note(p: dict) -> str:
    """"▦ panel: 4× pic_programmer" / "board without a schematic" / "" (escaped)."""
    if p.get("kind") == "board":
        return "board without a schematic"
    if p.get("kind") != "panel":
        return ""
    srcs = [s for s in lst(d(p.get("panel")).get("sources")) if isinstance(s, dict) and text(s.get("path"))]
    what = ", ".join(f"{num(s.get('copies'))}× {md_inline(text(s.get('path')).rsplit('/', 1)[-1].removesuffix('.kicad_pcb'), 60)}"
                     for s in srcs[:4])
    return "▦ panel" + (f": {what}" if what else "")


def status_of(p: dict) -> str:
    s = p.get("status")
    return s if s in STATUS_ICON else "modified"


def check_counts(p: dict, kind: str) -> tuple[int, int] | None:
    """(new, fixed) from summary.<kind>, None when the check didn't run."""
    c = d(d(p.get("summary")).get(kind))
    if not c:
        return None
    return num(c.get("new")), num(c.get("fixed"))


def new_violations(p: dict, kind: str) -> list[dict]:
    return [v for v in lst(d(d(p.get("checks")).get(kind)).get("new")) if isinstance(v, dict)]


def grid_findings(p: dict) -> list[dict]:
    """checks.grid.items: schematic items off the connection grid (warnings)."""
    return [f for f in lst(d(d(p.get("checks")).get("grid")).get("items")) if isinstance(f, dict)]


def grid_cell(p: dict) -> str:
    g = d(d(p.get("summary")).get("grid"))
    if not g:
        return "n/a"
    n = num(g.get("count"))
    return f"🟠 {n}" if n else "0"


def grid_mil(p: dict) -> str:
    v = d(d(p.get("checks")).get("grid")).get("grid_mil")
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 10000 else "?"


def grid_line(f: dict, mil: str = "50") -> str:
    """One off-grid finding: kind, what (reference / label text), sheet and detail, escaped."""
    kind = md_inline(text(f.get("kind")).replace("_", " "), 30)
    who = text(f.get("ref")) or text(f.get("text"))
    sheet = md_inline(text(f.get("sheet")), 60)
    det = md_inline(text(f.get("detail")), 200)
    return (f"- {SEVERITY_ICON['warning']} off the {md_inline(mil, 8)} mil grid: {kind}"
            + (f" {code(who, 60)}" if who else "") + (f" on {sheet}" if sheet else "") + (f": {det}" if det else ""))


def fnum(v):
    """A finite float from untrusted data, else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return None
    return float(v)


def impedance_counts(p: dict) -> dict | None:
    """summary.impedance (rows, violations, new_violations, stackup_shifts, width_changes), None when not run."""
    c = d(d(p.get("summary")).get("impedance"))
    if not c:
        return None
    out = {k: num(c.get(k)) for k in ("rows", "violations", "new_violations", "stackup_shifts", "width_changes")}
    out["length_out_mm"] = num(c.get("length_out_mm"))
    return out


def impedance_cell(p: dict) -> str:
    """Summary-table cell: out of tolerance / checked, 🔴 when something is newly out of tolerance."""
    c = impedance_counts(p)
    if c is None:
        return "n/a"
    if not c["rows"]:
        return "–"
    icon = "🔴 " if c["new_violations"] else "🟠 " if c["violations"] or c["stackup_shifts"] or c["width_changes"] else ""
    return f"{icon}{c['violations']} / {c['rows']}"


def impedance_total(projects: list[dict]) -> dict | None:
    """The impedance counts summed over the projects (None when no project ran the check)."""
    cs = [c for p in projects if (c := impedance_counts(p))]
    if not cs:
        return None
    out = {k: sum(c[k] or 0 for c in cs) for k in cs[0]}
    out["length_out_mm"] = round(out["length_out_mm"], 3)
    return out


def impedance_solver(projects: list[dict]) -> str:
    """How the numbers were made, for the comment: field solver, closed form, or both (per project)."""
    s = {text(d(d(p.get("checks")).get("impedance")).get("solver") or d(d(p.get("summary")).get("impedance")).get("solver"))
         for p in projects if impedance_counts(p)}
    s.discard("")
    if s == {"field"}:
        return "boarddd field solver"
    if "field" in s:
        return "boarddd field solver / closed-form estimate"
    return "closed-form estimate, not a field solve"


def impedance_summary(projects: list[dict]) -> str:
    """One markdown line for the comment ("" when no class has a target)."""
    t = impedance_total(projects)
    if not t or not t["rows"]:
        return ""
    parts = [f"{t['rows']} class × layer checked", f"{t['violations']} out of tolerance"
             + (f" (🔴 {t['new_violations']} new)" if t["new_violations"] else "")
             + (f", {t['length_out_mm']:g} mm of track" if t["length_out_mm"] else "")]
    if t["stackup_shifts"]:
        parts.append(f"{t['stackup_shifts']} shifted by a stackup change")
    if t["width_changes"]:
        parts.append(f"{t['width_changes']} with a width/gap change")
    return f"**Impedance** ({impedance_solver(projects)}): " + ", ".join(parts) + "."


IMPEDANCE_FLAG_TEXT = {"new_violation": "newly out of tolerance", "violation": "out of tolerance",
                       "fixed": "back in tolerance", "stackup_shift": "stackup change", "width_change": "width/gap change",
                       "target_change": "target changed"}


def impedance_lines(p: dict, limit: int = 15) -> list[str]:
    """Rows worth a look (bad/warn), one escaped bullet each."""
    rows = [r for r in lst(d(d(p.get("checks")).get("impedance")).get("rows"))
            if isinstance(r, dict) and r.get("severity") in ("bad", "warn")]
    rows.sort(key=lambda r: (r.get("severity") != "bad", text(r.get("class")), text(r.get("layer"))))
    out = []
    for r in rows[:limit]:
        t, b, h = d(r.get("target")), d(r.get("base")), d(r.get("head"))
        key = "Zdiff" if t.get("kind") == "differential" else "Z0"
        tz, tol = fnum(t.get("target")), fnum(t.get("tolerance_pct"))
        zb, zh = fnum(b.get("Z")), fnum(h.get("Z"))
        z = (f"{zb:.1f} → {zh:.1f} Ω" if zb is not None and zh is not None and abs(zh - zb) >= 0.05
             else f"{zh:.1f} Ω" if zh is not None else f"{zb:.1f} Ω (removed)" if zb is not None else "no Z")
        dev = fnum(h.get("deviation_pct"))
        out_mm, ctl = fnum(h.get("length_out_mm")), fnum(h.get("length_mm"))
        worst = fnum(h.get("worst_deviation_pct"))
        w = fnum(h.get("width")) if h else fnum(b.get("width"))
        geo = f"w {w:g} mm" if w is not None else ""
        gp = fnum(h.get("gap"))
        if gp is not None:
            geo += f", gap {gp:g} mm"
        flags = [IMPEDANCE_FLAG_TEXT[f] for f in lst(r.get("flags")) if isinstance(f, str) and f in IMPEDANCE_FLAG_TEXT]
        sp = fnum(r.get("shift_pct"))
        if sp is not None:
            flags = [f"stackup change {sp:+.1f} %" if f == "stackup change" else f for f in flags]
        icon = SEVERITY_ICON["error"] if r.get("severity") == "bad" else SEVERITY_ICON["warning"]
        out.append(f"- {icon} {key} {code(text(r.get('class')), 40)} on {md_inline(text(r.get('layer')), 20)}"
                   f" ({md_inline(text(h.get('structure') or b.get('structure')), 20)}{', ' + geo if geo else ''}): {z}"
                   + (f" vs {tz:g} Ω ±{tol:g} % ({dev:+.1f} %)" if tz is not None and tol is not None and dev is not None else "")
                   + (f"; {out_mm:g} of {ctl:g} mm out of tolerance" + (f" (worst {worst:+.1f} %)" if worst is not None and dev is not None and abs(worst - dev) >= 0.05 else "")
                      if out_mm and ctl is not None else "")
                   + (f": {md_inline(', '.join(flags), 120)}" if flags else ""))
    if len(rows) > limit:
        out.append(f"- … and {len(rows) - limit} more impedance row(s)")
    return out


def missing_fonts(doc: dict) -> list[str]:
    """fonts.missing: faces kicad-cli had to substitute (strings only, at most 20)."""
    return [f for f in lst(d(doc.get("fonts")).get("missing")) if isinstance(f, str) and f][:20]


def font_warning(doc: dict) -> str:
    """The missing-font warning as escaped markdown ("" when every face was available)."""
    faces = missing_fonts(doc)
    if not faces:
        return ""
    one = len(faces) == 1
    names = ", ".join(f"'{md_inline(f, 60)}'" for f in faces)
    return (f"Font{'' if one else 's'} {names} {'is' if one else 'are'} not available in CI; KiCad substituted "
            f"{'it' if one else 'them'}, so silkscreen text sizes and text-dependent DRC results "
            "(silk_edge_clearance, silk_overlap, text clearance…) may differ from the designer's machine. "
            "DRC violations involving that text are marked *font-dependent*. Commit the font files and pass "
            "them with the <code>fonts</code> workflow input.")


def summary_table(doc: dict) -> str:
    """One markdown table row per project (all values escaped)."""
    rows = ["| Project | Status | Sheets | Layers | Components | Nets | ERC new / fixed | DRC new / fixed | Off grid | Z out / checked | Notes |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in doc["projects"]:
        s = d(p.get("summary"))
        comp = d(s.get("components"))
        parts = [f"+{num(comp.get('added'))}", f"−{num(comp.get('removed'))}",
                 f"↔{num(comp.get('moved'))}", f"~{num(comp.get('changed'))}"]
        if num(comp.get("minor")):
            parts.append(f"(+{num(comp.get('minor'))} minor)")
        checks = []
        for kind in CHECK_KINDS:
            c = check_counts(p, kind)
            if c is None:
                checks.append("n/a")
            else:
                checks.append(f"{'🔴 ' if c[0] else ''}{c[0]} / {c[1]}")
        errors = len(lst(p.get("errors")))
        st = status_of(p)
        rows.append("| " + " | ".join([
            f"{code(text(p.get('name')) or text(p.get('slug')), 60)}<br><sub>{md_inline(text(p.get('path')) or '.', 120)}</sub>",
            f"{STATUS_ICON[st]} {st}",
            str(num(s.get("sheets_changed"))) + (f" (+{num(s.get('sheets_moved'))} moved)" if num(s.get("sheets_moved")) else ""),
            str(num(s.get("layers_changed"))), " ".join(parts),
            str(num(s.get("nets_changed"))), checks[0], checks[1], grid_cell(p), impedance_cell(p),
            "<br>".join(x for x in (kind_note(p), f"⚠️ {errors} export/parse problem(s)" if errors else "") if x)]) + " |")
    return "\n".join(rows)


def change_lines(p: dict, limit: int = 40) -> list[str]:
    """Bulleted, escaped list of the most relevant changes of one project."""
    out = []
    for sh in lst(d(p.get("schematic")).get("sheets")):
        if not isinstance(sh, dict) or sh.get("status") == "unchanged":
            continue
        title = md_inline(text(sh.get("title")) or text(sh.get("id")), 60)
        for c in lst(sh.get("changes")):
            if isinstance(c, dict) and not c.get("power") and not c.get("minor") and not c.get("move_only"):
                out.append(f"- sch {title}: {_change(c)}")
        moved = sum(1 for c in lst(sh.get("changes")) if isinstance(c, dict) and c.get("move_only"))
        if moved:  # smart diff: moved with the same connections, counted only
            out.append(f"- sch {title}: {moved} item(s) moved, same connections")
    minor: dict = {}
    bulk: dict = {}
    for c in lst(d(p.get("pcb")).get("changes")):
        if isinstance(c, dict) and c.get("minor"):
            minor.setdefault(text(c.get("detail")) or text(c.get("what")) or "minor", []).append(text(c.get("ref")) or "?")
        elif isinstance(c, dict) and c.get("group") in ("routing", "properties"):
            key = f"{text(c.get('kind'))} {text(c.get('what'))}" if c.get("group") == "routing" else "footprint fields/attributes only"
            bulk[key] = bulk.get(key, 0) + 1
        elif isinstance(c, dict):
            out.append(f"- pcb: {_change(c)}")
    if len(out) > limit:
        more = len(out) - limit
        out = out[:limit] + [f"- … and {more} more (see the viewer or report)"]
    if bulk:  # routing and property-only changes: counts only (the viewer and report list them)
        out.append("- pcb: " + ", ".join(f"{n}× {md_inline(k, 60)}" for k, n in sorted(bulk.items(), key=lambda kv: -kv[1])))
    # minor changes (3D model format / footprint library name only): one line per kind, refs folded
    for label, refs in sorted(minor.items(), key=lambda kv: -len(kv[1])):
        shown = ", ".join(md_inline(r, 20) for r in refs[:30]) + (", …" if len(refs) > 30 else "")
        out.append(f"- pcb (minor): {len(refs)} part(s): {md_inline(label, 120)}"
                   f"<details><summary>parts</summary>{shown}</details>")
    return out


def _change(c: dict) -> str:
    who = text(c.get("ref")) or text(c.get("net"))
    head = " ".join(x for x in (md_inline(c.get("kind"), 20), md_inline(c.get("feature"), 20), md_inline(c.get("what"), 30)) if x)
    if who:
        head += " " + code(who, 60)
    det = md_inline(text(c.get("detail")), 160)
    return head + (f": {det}" if det else "")


def violation_line(v: dict, kind: str) -> str:
    icon = SEVERITY_ICON.get(text(v.get("severity")), "🔵")
    items = "; ".join(md_inline(i, 100) for i in lst(v.get("items"))[:3] if isinstance(i, str))
    pos = lst(v.get("pos_mm"))
    at = ""
    if len(pos) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pos):
        at = f" @ ({pos[0]:.2f}, {pos[1]:.2f}) mm"
    faces = [f for f in lst(v.get("font_dependent")) if isinstance(f, str)][:3]
    fd = f" ⚠️ *font-dependent* ({', '.join(md_inline(f, 40) for f in faces)} missing)" if faces else ""
    return (f"- {icon} {kind.upper()} {code(text(v.get('type')), 40)}: "
            f"{md_inline(text(v.get('description')), 160)}{at}" + (f" — {items}" if items else "") + fd)
