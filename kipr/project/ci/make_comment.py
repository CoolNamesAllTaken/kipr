"""Render the sticky PR comment (markdown) from project-review.json.

    kipr project ci make-comment --data project-review.json [--run-url URL] [--site-url URL]
        [--report-url URL] [--data-url URL] [--head-sha SHA] [--note TEXT] > comment.md

The repository may be private (no GitHub Pages), so the comment links to the run and its
artifacts instead of a hosted viewer. All text from the JSON is escaped.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common import (CHECK_KINDS, MARKER, change_lines, check_counts, d, load_review, md_inline, new_violations,
                     safe_http_url, summary_table, text, truncate, violation_line)

MAX_COMMENT = 60000  # GitHub's hard limit is 65536 characters
TITLE = "## KiCad project review"


ARTIFACTS = (("report", "📄 project-review.html", "the report, opens in the browser"),
             ("site", "🧭 project-review-site", "interactive viewer (zip: unzip, then run serve.py or open index.html)"),
             ("data", "🗂️ project-review-data", "project-review.json"),
             ("run", "⚙️ workflow run", "logs and all artifacts"))


def links(run_url=None, site_url=None, report_url=None, data_url=None) -> list[str]:
    urls = {"run": run_url, "site": site_url, "report": report_url, "data": data_url}
    out = []
    for key, name, what in ARTIFACTS:
        u = safe_http_url(urls[key]) if urls[key] else None
        if u:
            out.append(f"- [**{name}**]({u}): {what}")
    return out


def build_comment(doc: dict, run_url=None, site_url=None, report_url=None, data_url=None, head_sha=None,
                  note=None, details: bool = True, marker: bool = True) -> str:
    projects = doc["projects"]
    base, head = d(doc.get("base")), d(doc.get("head"))
    sha = text(head_sha) or text(head.get("sha"))
    lines = [MARKER] if marker else []
    lines.append(TITLE)
    rng = ""
    if text(base.get("short")) and text(head.get("short")):
        rng = f" between `{md_inline(base.get('short'), 12)}` (merge base) and `{md_inline(head.get('short'), 12)}`"
    kicad = md_inline(d(doc.get("tool")).get("kicad"), 20)
    if not projects:
        lines.append(f"No KiCad project changed{rng}.")
    else:
        lines.append(f"{len(projects)} KiCad project(s) changed{rng}"
                     + (f" (KiCad {kicad})" if kicad else "") + ".")
        lines += ["", summary_table(doc)]
    if note:
        lines += ["", f"> [!NOTE]\n> {md_inline(note, 300)}"]
    errs = [md_inline(e, 300) for e in doc.get("errors") or [] if isinstance(e, str)]
    if errs:
        lines += ["", "> [!WARNING]"] + [f"> {e}" for e in errs[:5]]
    lk = links(run_url, site_url, report_url, data_url)
    if lk:
        lines += ["", "**Open the review** (artifacts of the workflow run; they expire):"] + lk
    if details:
        for p in projects:
            name = md_inline(text(p.get("name")) or text(p.get("slug")), 60)
            body = []
            for kind in CHECK_KINDS:
                vs = new_violations(p, kind)
                if vs:
                    body += [violation_line(v, kind) for v in vs[:15]]
                    if len(vs) > 15:
                        body.append(f"- … and {len(vs) - 15} more new {kind.upper()} violation(s)")
            fixed = [f"{kind.upper()} {c[1]}" for kind in CHECK_KINDS if (c := check_counts(p, kind)) and c[1]]
            if fixed:
                body.append(f"- ✅ fixed: {', '.join(fixed)}")
            ch = change_lines(p)
            if ch:
                body += ch
            perr = [md_inline(e, 300) for e in p.get("errors") or [] if isinstance(e, str)]
            if perr:
                body += [f"- ⚠️ {e}" for e in perr[:10]]
            if body:
                lines += ["", f"<details><summary><b>{name}</b>: changes and new ERC/DRC violations</summary>", ""]
                lines += body + ["", "</details>"]
    if sha:
        lines += ["", f"<sub>kipr project review for {md_inline(sha[:12], 12)}.</sub>"]
    body = "\n".join(lines) + "\n"
    if len(body) > MAX_COMMENT and details:
        return build_comment(doc, run_url, site_url, report_url, data_url, head_sha,
                             (note + " " if note else "") + "Details were too long for a comment; see the report.",
                             details=False, marker=marker)
    return truncate(body, MAX_COMMENT)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kipr project ci make-comment", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path, help="project-review.json")
    ap.add_argument("--run-url")
    ap.add_argument("--site-url")
    ap.add_argument("--report-url")
    ap.add_argument("--data-url")
    ap.add_argument("--head-sha")
    ap.add_argument("--note")
    a = ap.parse_args(argv)
    doc = load_review(a.data)
    sys.stdout.write(build_comment(doc, a.run_url, a.site_url, a.report_url, a.data_url, a.head_sha, a.note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
