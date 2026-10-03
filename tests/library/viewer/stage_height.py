"""The 2D stage fills the window below the toolbars (dev tool, needs `playwright`).

    python3 stage_height.py --site <OUT with viewer copied in> --shots <dir>

At 1920x1080 and 2560x1440 the first footprint's stage must be at least 80% of (window height - its
top) and its view box must end inside the window; a smaller window shrinks it and re-fits (the zoom
readout changes). Exit 1 on a problem; one screenshot per size goes to --shots.
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from screenshot import serve

MEASURE = """() => {
  document.querySelector('#main').scrollTop = 0;
  const s = document.querySelector('.view-box .stage-wrap').getBoundingClientRect();
  return { top: s.top, h: s.height, vh: innerHeight, box: document.querySelector('.view-box').getBoundingClientRect().bottom,
           zoom: document.querySelector('.view-box .readout.zoom').textContent };
}"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, type=Path)
    ap.add_argument("--shots", required=True, type=Path)
    a = ap.parse_args(argv)
    a.shots.mkdir(parents=True, exist_ok=True)
    items = json.loads((a.site / "manifest.json").read_text())["items"]
    item = next((i for i in items if i["kind"] == "footprint"), items[0])
    httpd, base = serve(a.site)
    problems = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            for w, h in ((1920, 1080), (2560, 1440)):
                page = browser.new_page(viewport={"width": w, "height": h})
                page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
                page.goto(f"{base}#{item['slug']}")
                page.wait_for_selector(".view-box .stage-wrap .pane")
                page.wait_for_timeout(600)
                m = page.evaluate(MEASURE)
                print(f"{w}x{h}: {m}")
                page.screenshot(path=str(a.shots / f"stage-{w}x{h}.png"))
                if m["h"] < 0.8 * (m["vh"] - m["top"]):
                    problems.append(f"{w}x{h}: stage {m['h']:.0f} px is below 80% of the {m['vh'] - m['top']:.0f} px under it")
                if m["box"] > m["vh"] + 1:
                    problems.append(f"{w}x{h}: view box ends below the window ({m['box']:.0f})")
                page.set_viewport_size({"width": int(w * 0.6), "height": int(h * 0.6)})  # either side may limit the fit
                page.wait_for_timeout(500)
                s = page.evaluate(MEASURE)
                if not s["h"] < m["h"] - 50:
                    problems.append(f"{w}x{h}: stage did not shrink with the window ({m['h']} -> {s['h']})")
                if s["zoom"] == m["zoom"]:
                    problems.append(f"{w}x{h}: no re-fit on resize (zoom {m['zoom']} -> {s['zoom']})")
                page.close()
            browser.close()
    finally:
        httpd.shutdown()
    for pr in problems:
        print("PROBLEM", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
