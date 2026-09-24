"""Build ONE self-contained HTML report (no JavaScript) from a `kipr project` OUT directory.

    python3 -m kipr.project.report --out OUT [--output FILE] [--max-mb 25]

Reads OUT/project-review.json (docs/CONTRACT-project.md) and the SVG exports it names, and writes
OUT/project-review.html: summary, per changed sheet before / after / ink-diff images with the change
table, per changed PCB layer before / after / diff images (cropped to the board), and the BOM,
netlist and ERC/DRC deltas. CSS is inline, every image is a PNG `data:` URI, nothing is loaded from
the network, all text is escaped, links are https only.

Images need Pillow and cairosvg (external references refused while rasterising). Without them the
report embeds the SVG exports as <img> data URIs (never as markup) and has no diff images. If the
page would exceed --max-mb, the layer images go first, then images are downscaled, then dropped,
and the report says so at the top.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import html
import io
import json
import re
import sys
from pathlib import Path

from kipr.project.site import confined_file, load_json, SLUG_RE

try:
    from PIL import Image, ImageChops, ImageFilter
except ImportError:  # pragma: no cover - exercised only without Pillow
    Image = None
try:
    import cairosvg
except (ImportError, OSError):  # OSError: cairosvg installed but libcairo missing
    cairosvg = None

MB = 1024 * 1024
MAX_SVG_BYTES = 20 * MB
MAX_ROWS = 400
# (sheet image width px, layer image width px or 0 = no layer images, note)
LEVELS = [
    (1600, 1000, None),
    (1600, 0, "per-layer PCB images left out to stay under the size limit"),
    (1100, 0, "per-layer PCB images left out; sheet images downscaled"),
    (700, 0, "per-layer PCB images left out; sheet images heavily downscaled"),
    (0, 0, "all images left out to stay under the size limit"),
]
DIFF = {"removed": (225, 40, 40, 255), "added": (30, 175, 70, 255), "common": (110, 110, 110, 150)}


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def fmt(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)[:300]
    return str(v)[:1000]


def https_url(u) -> str | None:
    if isinstance(u, str) and re.fullmatch(r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~%/+-]*)?", u.strip()):
        return u.strip().rstrip("/")
    return None


def sha_ok(s) -> str | None:
    return s if isinstance(s, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", s) else None


def d(v) -> dict:
    return v if isinstance(v, dict) else {}


def lst(v) -> list:
    return v if isinstance(v, list) else []


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# --------------------------------------------------------------------------- images

def _refuse_fetch(url, *a, **kw):
    raise ValueError(f"external reference refused: {url[:80]}")


def _fetch_kw() -> dict:
    """Refuse every fetch where this cairosvg version takes a url_fetcher. Newer versions don't, and with
    unsafe=False they only resolve data: URLs (cairosvg.url.safe_fetch), which is equally closed."""
    import inspect
    try:
        return {"url_fetcher": _refuse_fetch} if "url_fetcher" in inspect.signature(cairosvg.svg2png).parameters else {}
    except (TypeError, ValueError, AttributeError):
        return {}


_FETCH_KW = _fetch_kw() if cairosvg is not None else {}


class Images:
    """Rasterise SVG exports inside OUT to PNG and diff them. Cached per (file, width, crop)."""

    def __init__(self, out: Path):
        self.out = out
        self.can_raster = Image is not None and cairosvg is not None
        self._cache: dict = {}
        self.failed: list[str] = []

    def svg_path(self, rel, slug: str) -> Path | None:
        p = confined_file(self.out, rel, slug)
        if not p or p.suffix.lower() != ".svg" or p.stat().st_size > MAX_SVG_BYTES:
            return None
        return p

    def raster(self, rel, slug, width: int, crop=None):
        """RGBA PIL image of the SVG at `width` px across (the crop box in viewBox units if given)."""
        key = (rel, width, crop)
        if key in self._cache:
            return self._cache[key]
        img = None
        p = self.svg_path(rel, slug)
        if p and self.can_raster and width > 0:
            try:
                data = p.read_bytes()
                if crop and view_box(data):
                    data = with_view_box(data, crop)  # render only the board area, not the whole page
                png = cairosvg.svg2png(bytestring=data, output_width=width, unsafe=False, **_FETCH_KW)
                img = Image.open(io.BytesIO(png)).convert("RGBA")
            except Exception as e:  # noqa: BLE001 - a broken export must not break the report
                self.failed.append(f"{rel}: {type(e).__name__}")
                img = None
        self._cache[key] = img
        return img

    def diff(self, base, head, mode: str):
        """Coloured ink diff of two same-size RGBA images (either may be None)."""
        ref = base or head
        if ref is None:
            return None
        blank = Image.new("RGBA", ref.size, (0, 0, 0, 0))
        b = ink(base or blank, mode)
        h = ink(head or blank, mode)
        if b.size != h.size:
            h = h.resize(b.size)
        grow = ImageFilter.MaxFilter(3)
        removed = ImageChops.subtract(b, h.filter(grow))
        added = ImageChops.subtract(h, b.filter(grow))
        common = ImageChops.subtract(ImageChops.subtract(ImageChops.lighter(b, h), removed), added)
        out = Image.new("RGBA", b.size, (255, 255, 255, 0))
        for mask, color in ((common, DIFF["common"]), (removed, DIFF["removed"]), (added, DIFF["added"])):
            out.paste(Image.new("RGBA", b.size, color), (0, 0), mask)
        return out


def view_box(data: bytes):
    m = re.search(rb'viewBox\s*=\s*["\']\s*(-?[\d.eE+-]+)[\s,]+(-?[\d.eE+-]+)[\s,]+([\d.eE+-]+)[\s,]+([\d.eE+-]+)', data[:4096])
    if not m:
        return None
    v = tuple(float(x) for x in m.groups())
    return v if v[2] > 0 and v[3] > 0 else None


def with_view_box(data: bytes, box) -> bytes:
    """The SVG with its root viewBox (and width/height) replaced by `box` = (x, y, w, h) in viewBox units."""
    m = re.search(rb"<svg\b[^>]*>", data)
    if not m:
        return data
    tag = m.group(0)
    x, y, w, h = box
    new = re.sub(rb'\sviewBox\s*=\s*("[^"]*"|\'[^\']*\')', f' viewBox="{x:.4f} {y:.4f} {w:.4f} {h:.4f}"'.encode(), tag, count=1)
    new = re.sub(rb'\swidth\s*=\s*("[^"]*"|\'[^\']*\')', f' width="{w:.4f}mm"'.encode(), new, count=1)
    new = re.sub(rb'\sheight\s*=\s*("[^"]*"|\'[^\']*\')', f' height="{h:.4f}mm"'.encode(), new, count=1)
    return data[:m.start()] + new + data[m.end():]


def ink(img, mode: str):
    """'L' mask, 255 where the pixel is ink ('ink': dark on paper; 'alpha': any opaque pixel)."""
    a = img.getchannel("A").point(lambda v: 255 if v > 40 else 0)
    if mode == "alpha":
        return a
    over_white = Image.alpha_composite(Image.new("RGBA", img.size, (255, 255, 255, 255)), img).convert("L")
    dark = over_white.point(lambda v: 255 if v < 235 else 0)
    return ImageChops.multiply(a, dark)


def png_uri(img, background=None) -> str | None:
    if img is None:
        return None
    if background:
        img = Image.alpha_composite(Image.new("RGBA", img.size, background), img)
    buf = io.BytesIO()
    q = img.convert("RGBA").quantize(colors=64, method=Image.Quantize.FASTOCTREE)
    q.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def svg_uri(p: Path | None) -> str | None:
    """Fallback without Pillow/cairosvg: the export itself as an <img> data URI (never inline markup)."""
    if not p or p.stat().st_size > 3 * MB:
        return None
    return "data:image/svg+xml;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def img_tag(uri, alt: str) -> str:
    if not uri:
        return f'<div class="noimg">{esc(alt)}: no image</div>'
    return f'<img src="{uri}" alt="{esc(alt)}" loading="lazy">'


# --------------------------------------------------------------------------- sections

def status_badge(s) -> str:
    s = str(s or "")
    cls = re.sub(r"[^a-z]", "", s.lower())
    return f'<span class="b s-{cls}">{esc(s or "?")}</span>' if s else ""


def table(headers, rows, cls="") -> str:
    if not rows:
        return '<p class="muted">None.</p>'
    more = ""
    if len(rows) > MAX_ROWS:
        more = f'<p class="muted">First {MAX_ROWS} of {len(rows)} rows shown.</p>'
        rows = rows[:MAX_ROWS]
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}'


def change_rows(changes) -> list:
    rows = []
    for c in lst(changes):
        c = d(c)
        bb = c.get("bbox_mm")
        where = ", ".join(f"{x:g}" for x in bb[:2]) if isinstance(bb, list) and len(bb) == 4 and all(num(x) is not None for x in bb) else ""
        delta = f"{esc(fmt(c.get('base')))} → {esc(fmt(c.get('head')))}" if ("base" in c or "head" in c) else ""
        rows.append([esc(c.get("kind")), esc(c.get("ref") or c.get("net") or ""), esc(c.get("what")),
                     esc(c.get("layer") or ""), delta, esc(fmt(c.get("detail")) if c.get("detail") else ""), esc(where)])
    return rows


CHANGE_HEAD = ["Kind", "Ref", "What", "Layer", "Base → head", "Detail", "At (mm)"]


def triple(imgs: Images, slug, base_rel, head_rel, width, crop, mode, bg, alt) -> str:
    if width <= 0:
        return ""
    if imgs.can_raster:
        b = imgs.raster(base_rel, slug, width, crop) if base_rel else None
        h = imgs.raster(head_rel, slug, width, crop) if head_rel else None
        if b is None and h is None:
            return '<p class="muted">No image (export missing or unreadable).</p>'
        if b is not None and h is not None and b.size != h.size:
            h = h.resize(b.size)
        diff = imgs.diff(b, h, mode)
        figs = [("base", png_uri(b, bg) if b else None), ("head", png_uri(h, bg) if h else None), ("diff", png_uri(diff, bg))]
    else:
        figs = [("base", svg_uri(imgs.svg_path(base_rel, slug)) if base_rel else None),
                ("head", svg_uri(imgs.svg_path(head_rel, slug)) if head_rel else None)]
    return '<div class="trio">' + "".join(
        f'<figure><figcaption>{esc(n)}</figcaption>{img_tag(u, f"{alt} {n}")}</figure>' for n, u in figs) + "</div>"


def schematic_section(imgs, slug, sch, width) -> str:
    sheets = [d(s) for s in lst(d(sch).get("sheets"))]
    if not sheets:
        return ""
    out = ['<h3>Schematic</h3>']
    unchanged = [s for s in sheets if s.get("status") == "unchanged"]
    for s in sheets:
        if s.get("status") == "unchanged":
            continue
        out.append(f'<h4>{esc(s.get("title") or s.get("id"))} <span class="muted">{esc(s.get("file") or "")}</span> {status_badge(s.get("status"))}</h4>')
        out.append(table(CHANGE_HEAD, change_rows(s.get("changes"))))
        out.append(triple(imgs, slug, s.get("base"), s.get("head"), width, None, "ink", (245, 244, 239, 255), f"sheet {s.get('id')}"))
    if unchanged:
        out.append(f'<p class="muted">Unchanged sheets: {esc(", ".join(str(s.get("title") or s.get("id")) for s in unchanged))}</p>')
    return "\n".join(out)


def pcb_section(imgs, slug, pcb, width) -> str:
    pcb = d(pcb)
    if not pcb:
        return ""
    out = ['<h3>Layout</h3>', table(CHANGE_HEAD, change_rows(pcb.get("changes")))]
    b = d(pcb.get("board"))
    crop = None
    o, s = b.get("origin_mm"), b.get("size_mm")
    if isinstance(o, list) and isinstance(s, list) and len(o) == 2 and len(s) == 2 and all(num(x) is not None for x in o + s):
        m = max(2.0, max(s) * 0.03)
        crop = (o[0] - m, o[1] - m, s[0] + 2 * m, s[1] + 2 * m)
    layers = [d(l) for l in lst(pcb.get("layers"))]
    changed = [l for l in layers if l.get("status") not in (None, "unchanged") and l.get("kind") != "drill"]
    if changed and width > 0:
        for l in changed:
            out.append(f'<h4>{esc(l.get("id"))} {status_badge(l.get("status"))}</h4>')
            out.append(triple(imgs, slug, d(l.get("base")).get("svg"), d(l.get("head")).get("svg"), width, crop, "alpha",
                              (14, 17, 22, 255), f"layer {l.get('id')}"))
    elif changed:
        out.append(f'<p class="muted">Changed layers: {esc(", ".join(str(l.get("id")) for l in changed))}</p>')
    return "\n".join(out)


def bom_section(bom) -> str:
    rows = [d(r) for r in lst(d(bom).get("rows")) if d(r).get("status") not in (None, "unchanged")]
    if not bom:
        return ""
    def val(r, side, k):
        return d(r.get(side)).get(k)
    trs = []
    for r in rows:
        refs = ", ".join(str(x) for x in lst(r.get("refs"))) or str(r.get("key") or "")
        v = f"{esc(fmt(val(r, 'base', 'value')))} → {esc(fmt(val(r, 'head', 'value')))}"
        fp = f"{esc(fmt(val(r, 'base', 'footprint')))} → {esc(fmt(val(r, 'head', 'footprint')))}"
        trs.append([status_badge(r.get("status")), esc(refs), v, fp, esc(", ".join(str(x) for x in lst(r.get("what"))))])
    return "<h3>BOM changes</h3>" + table(["Status", "Refs", "Value", "Footprint", "What"], trs)


def netlist_section(nl) -> str:
    if not nl:
        return ""
    trs = [[status_badge(c.get("status")), f"<code>{esc(c.get('net'))}</code>" + (f' <span class="muted">was {esc(c.get("renamed_from"))}</span>' if c.get("renamed_from") else ""),
            esc(", ".join(str(x) for x in lst(c.get("added")))), esc(", ".join(str(x) for x in lst(c.get("removed"))))]
           for c in (d(x) for x in lst(d(nl).get("changes")))]
    return "<h3>Netlist changes</h3>" + table(["Status", "Net", "Pins added", "Pins removed"], trs)


def checks_section(checks) -> str:
    out = []
    for kind, label in (("drc", "DRC"), ("erc", "ERC")):
        c = d(d(checks).get(kind))
        if not c:
            continue
        rows = []
        for st, items in (("new", c.get("new")), ("fixed", c.get("fixed"))):
            for v in (d(x) for x in lst(items)):
                pos = v.get("pos_mm")
                at = ", ".join(f"{x:g}" for x in pos) if isinstance(pos, list) and len(pos) == 2 and all(num(x) is not None for x in pos) else ""
                rows.append([status_badge(st), esc(v.get("severity")), f"<code>{esc(v.get('type'))}</code>", esc(v.get("description")),
                             esc("; ".join(str(x) for x in lst(v.get("items")))), esc(at + (f" {v.get('sheet')}" if v.get("sheet") else ""))])
        out.append(f"<h3>{label} <span class=\"muted\">{esc(fmt(c.get('base_count')))} → {esc(fmt(c.get('head_count')))}</span></h3>"
                   + table(["", "Severity", "Type", "Description", "Items", "Where"], rows))
    return "\n".join(out)


def summary_row(p) -> list:
    s = d(p.get("summary"))
    c = d(s.get("components"))
    n = lambda v: esc(fmt(num(v))) if num(v) is not None else ""  # noqa: E731
    return [f'<a href="#p-{esc(p["slug"])}">{esc(p.get("name") or p["slug"])}</a>', status_badge(p.get("status")),
            n(s.get("sheets_changed")), n(s.get("layers_changed")), n(c.get("added")), n(c.get("removed")), n(c.get("moved")),
            n(c.get("changed")), n(s.get("nets_changed")), n(d(s.get("erc")).get("new")), n(d(s.get("drc")).get("new"))]


CSS = """
:root{color-scheme:light dark;--bg:#f6f7f9;--panel:#fff;--text:#1c2026;--muted:#667085;--border:#d9dde3;--accent:#2463d6;
--add:#1a7f37;--add-bg:#dafbe1;--del:#cf222e;--del-bg:#ffebe9;--chg-bg:#fff4c2;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--panel:#191d23;--text:#e3e7ec;--muted:#8b949e;--border:#30363d;--accent:#6ea8fe;
--add:#56d364;--add-bg:#12301b;--del:#ff7b72;--del-bg:#3d1618;--chg-bg:#3a3212;--warn:#e3b341}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1500px;margin:0 auto;padding:16px}a{color:var(--accent)}code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.92em}
h1{font-size:20px;margin:0 0 4px}h2{font-size:18px;margin:0 0 8px}h3{font-size:15px;margin:18px 0 6px}h4{font-size:14px;margin:14px 0 6px}
.muted{color:var(--muted)}.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin:14px 0}
.note{background:var(--chg-bg);padding:6px 10px;border-radius:6px}.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:3px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-size:12px}.b{display:inline-block;padding:0 7px;border-radius:10px;font-size:11px;font-weight:600;line-height:18px}
.s-added,.s-fixed{background:var(--add-bg);color:var(--add)}.s-removed,.s-deleted,.s-new{background:var(--del-bg);color:var(--del)}
.s-modified,.s-changed,.s-moved,.s-rotated,.s-renamed{background:var(--chg-bg);color:var(--warn)}.s-unchanged{color:var(--muted);border:1px solid var(--border)}
.trio{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:8px}figure{margin:0}figcaption{font-size:12px;font-weight:600;color:var(--muted)}
.trio img{width:100%;height:auto;border:1px solid var(--border);border-radius:6px;display:block}.noimg{padding:20px;border:1px dashed var(--border);color:var(--muted);text-align:center}
.legend span{margin-right:14px;font-size:12px}.k{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
"""


def build(out: Path, width_sheet: int, width_layer: int, note: str | None) -> tuple[str, Images]:
    review = load_json(out / "project-review.json")
    imgs = Images(out)
    if not isinstance(review, dict):
        return (f"<!doctype html><meta charset=utf-8><title>Project review</title><style>{CSS}</style>"
                "<main><h1>Project review</h1><p>No readable project-review.json.</p></main>"), imgs
    projects = [p for p in (d(x) for x in lst(review.get("projects"))) if isinstance(p.get("slug"), str) and SLUG_RE.match(p["slug"])]
    repo = https_url(d(review.get("repo")).get("url"))

    def side(s):
        s = d(s)
        sha = sha_ok(s.get("sha"))
        label = esc(s.get("short") or (sha or "?")[:8])
        link = f'<a href="{esc(repo)}/commit/{esc(sha)}"><code>{label}</code></a>' if repo and sha else f"<code>{label}</code>"
        return f"{esc(s.get('ref') or '')} {link}"

    tool = d(review.get("tool"))
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'>"
             f"<meta http-equiv=Content-Security-Policy content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
             f"<meta name=referrer content=no-referrer><title>Project review</title><style>{CSS}</style></head><body><main>",
             f"<h1>Project review</h1><p>{f'<a href={chr(34)}{esc(repo)}{chr(34)}>{esc(repo)}</a> · ' if repo else ''}"
             f"{side(review.get('base'))} → {side(review.get('head'))} · {esc(tool.get('name') or 'kipr')} {esc(tool.get('version') or '')}"
             f"{' · KiCad ' + esc(tool.get('kicad')) if tool.get('kicad') else ''} · generated {esc(_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}</p>"]
    if note:
        parts.append(f'<p class="note">{esc(note)}.</p>')
    if not imgs.can_raster:
        parts.append('<p class="note">Pillow and cairosvg are not installed: the SVG exports are shown as they are, without diff images.</p>')
    parts.append('<p class="legend"><span><i class="k" style="background:rgb(225,40,40)"></i>removed (base only)</span>'
                 '<span><i class="k" style="background:rgb(30,175,70)"></i>added (head only)</span>'
                 '<span><i class="k" style="background:rgba(110,110,110,.6)"></i>unchanged</span></p>')
    parts.append('<section class="card"><h2>Projects</h2>' + table(
        ["Project", "Status", "Sheets", "Layers", "Comp. +", "Comp. −", "Moved", "Changed", "Nets", "ERC new", "DRC new"],
        [summary_row(p) for p in projects]) + "</section>")
    for p in projects:
        slug = p["slug"]
        errs = "".join(f"<li>{esc(e)}</li>" for e in lst(p.get("errors")) if isinstance(e, str))
        parts.append(f'<section class="card" id="p-{esc(slug)}"><h2>{esc(p.get("name") or slug)} {status_badge(p.get("status"))} '
                     f'<span class="muted">{esc(p.get("path") or "")}</span></h2>'
                     + (f'<details class="note"><summary>Export problems</summary><ul>{errs}</ul></details>' if errs else ""))
        parts.append(schematic_section(imgs, slug, p.get("schematic"), width_sheet))
        parts.append(pcb_section(imgs, slug, p.get("pcb"), width_layer))
        parts.append(bom_section(p.get("bom")))
        parts.append(netlist_section(p.get("netlist")))
        parts.append(checks_section(p.get("checks")))
        parts.append("</section>")
    if imgs.failed:
        parts.append(f'<p class="muted">Could not rasterise: {esc(", ".join(imgs.failed[:20]))}</p>')
    parts.append("<p class='muted'>Interactive version: open index.html in this folder (or run python3 serve.py).</p></main></body></html>")
    return "\n".join(parts), imgs


def make_report(out, output=None, max_mb: float = 25.0) -> Path:
    out = Path(out)
    output = Path(output) if output else out / "project-review.html"
    page = ""
    for width_sheet, width_layer, note in LEVELS:
        page, _ = build(out, width_sheet, width_layer, note)
        if len(page.encode("utf-8")) <= max_mb * MB:
            break
    output.write_text(page, encoding="utf-8")
    return output


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path, help="the `kipr project` OUT dir")
    ap.add_argument("--output", type=Path, help="default: OUT/project-review.html")
    ap.add_argument("--max-mb", type=float, default=25.0)
    a = ap.parse_args(argv)
    try:
        path = make_report(a.out, a.output, a.max_mb)
    except OSError as e:
        print(f"kipr report: failed: {e}", file=sys.stderr)
        return 1
    print(f"kipr report: wrote {path} ({path.stat().st_size / MB:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
