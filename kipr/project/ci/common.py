"""Shared helpers for the project-review CI tools.

In the privileged publish job, project-review.json comes from a pull-request run and is
UNTRUSTED: every value is type-checked here and every string is escaped (kipr.library.ci.common's
md_* helpers: no raw HTML, @-mentions or remote images) before it reaches markdown or the API.
"""
from __future__ import annotations

from pathlib import Path

from kipr.library.ci.common import (GitHub, check_repo, check_sha, load_json, log, md_inline,  # noqa: F401
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


def summary_table(doc: dict) -> str:
    """One markdown table row per project (all values escaped)."""
    rows = ["| Project | Status | Sheets | Layers | Components | Nets | ERC new / fixed | DRC new / fixed | Notes |",
            "|---|---|---|---|---|---|---|---|---|"]
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
            str(num(s.get("sheets_changed"))), str(num(s.get("layers_changed"))), " ".join(parts),
            str(num(s.get("nets_changed"))), checks[0], checks[1],
            f"⚠️ {errors} export/parse problem(s)" if errors else ""]) + " |")
    return "\n".join(rows)


def change_lines(p: dict, limit: int = 40) -> list[str]:
    """Bulleted, escaped list of the most relevant changes of one project."""
    out = []
    for sh in lst(d(p.get("schematic")).get("sheets")):
        if not isinstance(sh, dict) or sh.get("status") == "unchanged":
            continue
        title = md_inline(text(sh.get("title")) or text(sh.get("id")), 60)
        for c in lst(sh.get("changes")):
            if isinstance(c, dict) and not c.get("power") and not c.get("minor"):
                out.append(f"- sch {title}: {_change(c)}")
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
    head = " ".join(x for x in (md_inline(c.get("kind"), 20), md_inline(c.get("what"), 30)) if x)
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
    return (f"- {icon} {kind.upper()} {code(text(v.get('type')), 40)}: "
            f"{md_inline(text(v.get('description')), 160)}{at}" + (f" — {items}" if items else ""))
