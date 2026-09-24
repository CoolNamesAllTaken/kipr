#!/usr/bin/env python3
"""Headless browser check + screenshots of the 3D PCBA viewer (needs the `playwright` package and
its chromium).

    python3 tests/web-3d/screenshots.py [--out tests/web-3d/out/mock] [--shots tests/web-3d/out/shots]
                                        [--project demo] [--case NAME ...]

Serves the repository root on a local port, opens web/project/pcba3d/demo.html for each case, fails
(exit 1) on any page error, console error, failed request or a component the viewer could not find
in the GLB, and writes one PNG per case to --shots. Also times loading of the biggest mock board.
"""
import argparse
import functools
import os
import http.server
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]

# name -> query parameters (plus an optional action run after load)
CASES = {
    "side": {"mode": "side"},
    "overlay": {"mode": "overlay"},
    "highlight": {"mode": "highlight"},
    "focus": {"mode": "side", "focus": "@first-change"},
    "hover": {"mode": "highlight", "hover": True},
    "explode": {"mode": "highlight", "explode": "0.7"},
    "dark-overlay": {"mode": "overlay", "theme": "dark"},
    "bottom": {"mode": "side", "view": "Bottom"},
    "no-board": {"mode": "highlight", "toggle": "board"},
    "narrow": {"mode": "side", "viewport": (640, 900)},
    "cycle": {"mode": "overlay", "cycle": True},
    "copper-diff": {"mode": "overlay", "view": "Top", "toggle": ["components", "markers"]},
    "glb-board": {"mode": "side", "toggle_board": "glb"},
}


def serve(root):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    handler = functools.partial(Quiet, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "tests/web-3d/out/mock"))
    ap.add_argument("--shots", default=str(REPO / "tests/web-3d/out/shots"))
    ap.add_argument("--project", default="demo")
    ap.add_argument("--case", action="append", help="only these cases")
    ap.add_argument("--extra-project", action="append", default=[], help="also screenshot these projects in side mode")
    ap.add_argument("--prefix", default="", help="prefix for PNG names")
    ap.add_argument("--file", action="store_true",
                    help="open demo.html from disk (file://); needs build_offline.mjs --out OUT first")
    ap.add_argument("--allow-missing", action="store_true", help="do not fail on components without 3D geometry")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    shots = Path(args.shots).resolve()
    shots.mkdir(parents=True, exist_ok=True)
    review = json.loads((out / "project-review.json").read_text())
    # Serve the nearest directory holding both the viewer and OUT (OUT may live outside the repo).
    root = Path(os.path.commonpath([REPO, out]))
    httpd, root_url = serve(root)
    demo_dir = REPO / "web/project/pcba3d"
    out_rel = os.path.relpath(out, demo_dir) + "/"
    demo_url = (demo_dir / "demo.html").as_uri() + "?" if args.file else root_url + str(demo_dir.relative_to(root)) + "/demo.html?"
    failures = []
    written = []

    def first_change(slug):
        project = next(p for p in review["projects"] if p["slug"] == slug)
        order = ["moved", "changed", "rotated", "added", "removed"]
        comps = sorted((c for c in project["pcba3d"]["components"] if c["status"] != "unchanged"),
                       key=lambda c: order.index(c["status"]))
        return comps[0]["ref"] if comps else None

    cases = [(name, dict(spec), args.project) for name, spec in CASES.items() if not args.case or name in args.case]
    for slug in args.extra_project:
        cases.append((f"{slug}-side", {"mode": "side"}, slug))
        cases.append((f"{slug}-highlight", {"mode": "highlight"}, slug))

    slugs = {p["slug"] for p in review["projects"]}
    unknown = sorted({slug for _, _, slug in cases} - slugs)
    if unknown:
        print(f"no such project in {out}: {', '.join(unknown)} (has: {', '.join(sorted(slugs))})")
        return 2

    with sync_playwright() as p:
        # kipr-tools/bin/pw-env sets PW_CHROMIUM_ARGS (and the libraries chromium needs).
        chromium_args = os.environ.get("PW_CHROMIUM_ARGS", "--use-angle=swiftshader --enable-unsafe-swiftshader").split()
        browser = p.chromium.launch(args=chromium_args)
        for name, spec, slug in cases:
            viewport = spec.pop("viewport", (1400, 820))
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            problems = []
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}")
                    if m.type == "error" or "WebGL" in m.text else None)
            aborted = []
            # A big GLB read through a stream while the main thread parses can be reported as
            # net::ERR_ABORTED when the page closes, although every byte arrived; the viewer's own
            # error list (checked below) is the authority for those.
            page.on("requestfailed", lambda r: (aborted if "ERR_ABORTED" in (r.failure or "") and r.url.endswith(".glb")
                                                else problems).append(f"requestfailed: {r.url} {r.failure}"))
            hover = spec.pop("hover", False)
            view = spec.pop("view", None)
            toggle = spec.pop("toggle", None)
            cycle = spec.pop("cycle", False)
            board_source = spec.pop("toggle_board", None)
            if spec.get("focus") == "@first-change":
                spec["focus"] = first_change(slug)
            query = {"out": out_rel, "project": slug, **spec}
            url = demo_url + urlencode(query)
            t0 = time.time()
            page.goto(url)
            page.wait_for_function("document.body.dataset.ready", timeout=120000)
            load_s = time.time() - t0
            if page.evaluate("document.body.dataset.ready") == "error":
                problems.append("demo: " + page.inner_text("#err"))
            status = page.inner_text(".kp3d-status") if page.query_selector(".kp3d-status") else ""
            viewer_errors = page.evaluate("window.kipr3d ? kipr3d.errors : ['viewer did not mount']")
            problems.extend(f"viewer: {e}" for e in viewer_errors)
            if view:
                page.evaluate(f"kipr3d.view.fit('{view.lower()}')")
            for t in ([toggle] if isinstance(toggle, str) else toggle or []):
                page.click(f"input[data-toggle={t}]")
            if board_source:
                page.click(f"button[data-board={board_source}]")
            if cycle:
                # Mount/dispose through every project twice (what the shell does on tab changes):
                # nothing may throw and WebGL contexts must not pile up.
                slugs = [p["slug"] for p in review["projects"]] * 2 + [slug]
                for other in slugs:
                    page.select_option("#project", other)
                    page.wait_for_function("document.body.dataset.ready === '1'", timeout=120000)
                    if page.evaluate("document.querySelectorAll('canvas.kp3d-canvas').length") != 1:
                        problems.append(f"cycle: stale canvases after mounting {other}")
            if hover:
                # Hover the first changed component: project its box centre to the screen.
                ref = first_change(slug)
                pt = page.evaluate("""(ref) => {
                    const v = kipr3d.view; v.focus(ref);
                    const box = v.boxOf(ref); const c = box.getCenter(box.min.clone());
                    c.project(v.camera); const r = v.canvas.getBoundingClientRect();
                    return {x: r.left + (c.x + 1) / 2 * r.width, y: r.top + (1 - c.y) / 2 * r.height};
                }""", ref)
                page.mouse.move(pt["x"], pt["y"])
                page.wait_for_timeout(300)
                if page.evaluate("document.querySelector('.kp3d-tip').hidden"):
                    problems.append("hover: no hover card shown")
            page.wait_for_timeout(400)
            path = shots / f"{args.prefix}{name}.png"
            page.screenshot(path=str(path))
            written.append(str(path))
            # Everything listed must have been found in the GLB (the mock has geometry for all).
            missing = page.evaluate("[...document.querySelectorAll('.kp3d-rows .nomesh')].map(e => e.closest('li').dataset.ref)")
            if missing and not args.allow_missing:
                problems.append(f"components without 3D geometry: {missing}")
            # Cost of one frame with everything on screen (the view only draws when something moves).
            frame_ms = page.evaluate("""() => { const v = kipr3d.view; v.render(); const n = 20, t = performance.now();
                for (let i = 0; i < n; i++) { v.camera.rotateZ(0.01); v.render(); } v.renderer.getContext().finish();
                return (performance.now() - t) / n; }""")
            print(f"{name:14s} load {load_s:4.1f}s  frame {frame_ms:5.1f}ms (swiftshader)  {status.splitlines()[0] if status else ''}")
            for pr in problems:
                print("   !", pr)
            if problems:
                failures.append(name)
            page.close()
        browser.close()
    httpd.shutdown()
    print("screenshots:", *written, sep="\n  ")
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
