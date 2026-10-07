"""Up / Down arrows solo a footprint layer in the 2D view (dev tool, needs `playwright`).

    python3 layer_keys.py --site <OUT with viewer copied in> [--shots <dir>]

On the first footprint with per-layer renders: ArrowDown with no solo layer shows the front-most layer
alone, ArrowUp / ArrowDown move up (toward the front) / down the stack and stop at the ends, the bar marks
the solo layer, the ticks stay, the page does not scroll; Esc or a tick goes back to the ticked layers;
another footprint with that layer keeps it; the arrows are ignored in the filter input, with Shift, in
Diff and on symbols (left to the page). Exit 1 on a problem.
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from screenshot import serve

STATE = """() => {
  const bar = document.querySelector('.view-box .layers');
  const order = [...bar.querySelectorAll('.layer-toggle')].map((l) => l.dataset.layer);
  const stacks = [...document.querySelectorAll('.view-box .stack[data-side]')].map((s) =>
    [...s.querySelectorAll('img[data-layer]')].filter((i) => !i.hidden).map((i) => i.dataset.layer).sort().join(','));
  return {
    order, stacks, hidden: bar.hidden,
    solo: [...bar.querySelectorAll('.layer-toggle.solo')].map((l) => l.dataset.layer),
    ticks: [...bar.querySelectorAll('input[type=checkbox]')].map((c) => c.checked).join(''),
    ticked: order.filter((n, i) => bar.querySelectorAll('input[type=checkbox]')[i].checked).sort().join(','),
    scrolled: document.querySelector('#main').scrollTop + scrollY,
    prevented: window.__prevented,
  };
}"""
# a window listener runs after the viewer's (document) one: did it prevent the default (scrolling)?
PROBE = """() => {
  window.__prevented = null;
  if (!window.__probe) addEventListener('keydown', (e) => { if (e.key.startsWith('Arrow')) window.__prevented = e.defaultPrevented; });
  window.__probe = true;
}"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, type=Path)
    ap.add_argument("--shots", type=Path)
    a = ap.parse_args(argv)
    if a.shots:
        a.shots.mkdir(parents=True, exist_ok=True)
    items = json.loads((a.site / "manifest.json").read_text())["items"]
    fps = [i for i in items if i["kind"] == "footprint" and len(((i.get("renders") or {}).get("head") or {}).get("layers") or {}) > 2]
    sym = next((i for i in items if i["kind"] == "symbol"), None)
    httpd, base = serve(a.site)
    problems = []

    def check(ok, msg):
        if not ok:
            problems.append(msg)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            item = fps[0]
            page.goto(f"{base}#{item['slug']}")
            page.wait_for_selector(".view-box .layers .layer-toggle")
            page.click(".view-box .seg-btn >> nth=0")
            page.wait_for_timeout(300)
            page.evaluate(PROBE)
            s0 = page.evaluate(STATE)
            front = s0["order"][::-1]  # the bar reads back to front
            check(not s0["solo"] and all(st == s0["ticked"] for st in s0["stacks"]), f"setup: {s0}")

            def press(key, want, tag=None):
                page.evaluate(PROBE)
                page.keyboard.press(key)
                page.wait_for_timeout(150)
                s = page.evaluate(STATE)
                tag = tag or key
                check(s["solo"] == ([want] if want else []), f"{tag}: solo {s['solo']}, expected {want}")
                want_stack = want if want else s0["ticked"]
                check(all(st == want_stack for st in s["stacks"]), f"{tag}: on show {s['stacks']}, expected {want_stack}")
                check(s["ticks"] == s0["ticks"], f"{tag}: the ticks changed")
                check(s["scrolled"] == 0, f"{tag}: the page scrolled")
                return s

            s = press("ArrowDown", front[0], "first ArrowDown (the front-most layer)")
            check(s["prevented"] is True, "ArrowDown not prevented")
            press("ArrowDown", front[1])
            press("ArrowDown", front[2])
            press("ArrowUp", front[1])
            if a.shots:
                page.screenshot(path=str(a.shots / "layer-solo.png"))
            press("ArrowUp", front[0])
            press("ArrowUp", front[0], "ArrowUp at the front (no wrap)")
            for _ in front:
                page.keyboard.press("ArrowDown")
            press("ArrowDown", front[-1], "ArrowDown at the back (no wrap)")
            press("Shift+ArrowUp", front[-1], "Shift+ArrowUp (ignored)")
            press("Escape", None, "Escape")
            press("ArrowUp", front[-1], "first ArrowUp (the back-most layer)")
            press("ArrowUp", front[-2])
            # another footprint with that layer keeps it
            other = next((i for i in fps[1:] if front[-2] in i["renders"]["head"]["layers"]), None)
            if other:
                page.goto(f"{base}#{other['slug']}")
                page.wait_for_selector(".view-box .layers .layer-toggle")
                page.wait_for_timeout(300)
                s = page.evaluate(STATE)
                check(s["solo"] == [front[-2]] and all(st == front[-2] for st in s["stacks"]), f"next footprint: solo {s['solo']}, on show {s['stacks']}")
                page.goto(f"{base}#{item['slug']}")
                page.wait_for_selector(".view-box .layers .layer-toggle")
                page.wait_for_timeout(300)
            # a tick goes back to the ticked layers (with that change)
            page.click(".view-box .layers input[type=checkbox] >> nth=0")
            page.wait_for_timeout(150)
            s = page.evaluate(STATE)
            check(not s["solo"] and all(st == s["ticked"] for st in s["stacks"]) and s["ticks"] != s0["ticks"], f"tick while solo: {s}")
            page.click(".view-box .layers input[type=checkbox] >> nth=0")  # untick again; focus stays on it
            press("ArrowDown", front[0], "ArrowDown from a checkbox")
            press("Escape", None, "Escape from a checkbox")
            # typing in the filter: ignored, not prevented
            page.focus("#sidebar input")
            page.evaluate(PROBE)
            page.keyboard.press("ArrowDown")
            s = page.evaluate(STATE)
            check(not s["solo"] and s["prevented"] is False, f"ArrowDown in the filter: solo {s['solo']}, prevented {s['prevented']}")
            page.evaluate("() => document.activeElement.blur()")
            # Diff: no layer bar, the arrows are left to the page
            if page.locator(".view-box .seg-btn[data-mode='diff']").count():
                page.click(".view-box .seg-btn[data-mode='diff']")
                page.wait_for_timeout(200)
                page.evaluate(PROBE)
                page.keyboard.press("ArrowDown")
                s = page.evaluate(STATE)
                check(s["hidden"] and s["prevented"] is False, f"diff: bar hidden {s['hidden']}, prevented {s['prevented']}")
                page.click(".view-box .seg-btn >> nth=0")
            if sym:
                page.goto(f"{base}#{sym['slug']}")
                page.wait_for_selector(".view-box .pane")
                page.evaluate(PROBE)
                page.keyboard.press("ArrowDown")
                check(page.evaluate("() => window.__prevented") is False, "symbol: ArrowDown prevented")
            browser.close()
    finally:
        httpd.shutdown()
    for pr in problems:
        print("PROBLEM", pr)
    print("layer keys:", "ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
