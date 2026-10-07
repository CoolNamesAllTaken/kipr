"""Colour check of the library 3D view (needs playwright; three.js, boarddd and occt-import-js are vendored).

    python3 color_check.py [--js <dir with view3d.js>]  -> prints JSON {view: [r, g, b]}, exit 1 on failure

Loads tests/library/fixtures/color/blue_board.step (one solid, COLOUR_RGB of the RP2040-Zero board,
0.090/0.224/0.420, which KiCad's 3D viewer shows as a mid blue, about 65/108/170) through view3d.js on a blank
page, looks at it from the top and from the bottom, and averages the middle of the canvas. The part must read
clearly blue and not near-black from both sides.
"""
import argparse
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PAGE = """<!doctype html><meta charset=utf-8><div id=s style="width:640px;height:480px"></div>
<script type=module>
import { create3DViewer } from '/JS/view3d.js';
const v = await create3DViewer(document.getElementById('s'), { onStatus: (s) => { if (s.errors?.length) window.__err = s.errors; } });
await v.load({ head: { geom: null, layers: null, models: [{ url: '/tests/library/fixtures/color/blue_board.step', label: 'blue' }] }, base: null });
window.__v = v;
</script>"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--js", default="web/library/js", help="viewer js dir, relative to the repo root")
    a = ap.parse_args(argv)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/color.html"):
                body = PAGE.replace("/JS/", f"/{a.js.strip('/')}/").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(ROOT)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    out, ok = {}, True
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            page = b.new_page(viewport={"width": 800, "height": 600})
            page.set_default_timeout(120000)
            page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/color.html", wait_until="domcontentloaded")
            page.wait_for_function("() => window.__v || window.__err", timeout=180000, polling=500)
            if page.evaluate("() => window.__err"):
                print("load error:", page.evaluate("() => window.__err"))
                return 1
            for view in ("top", "bottom"):
                page.evaluate(f"() => window.__v.setView('{view}')")
                page.wait_for_timeout(800)
                out[view] = page.evaluate("""() => {
                    const src = document.querySelector('#s canvas');
                    const c = document.createElement('canvas');
                    c.width = src.width; c.height = src.height;
                    const ctx = c.getContext('2d');
                    ctx.drawImage(src, 0, 0);
                    const w = Math.floor(c.width * 0.2), h = Math.floor(c.height * 0.2);
                    const d = ctx.getImageData((c.width - w) / 2, (c.height - h) / 2, w, h).data;
                    const s = [0, 0, 0];
                    for (let i = 0; i < d.length; i += 4) { s[0] += d[i]; s[1] += d[i + 1]; s[2] += d[i + 2]; }
                    const n = d.length / 4;
                    return s.map((v) => Math.round(v / n));
                }""")
            b.close()
    finally:
        httpd.shutdown()
    for view, (r, g, bl) in out.items():
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * bl
        good = bl > r + 40 and bl > g + 20 and lum > 80
        ok &= good
        print(f"{view}: rgb {r},{g},{bl} luminance {lum:.0f} {'ok' if good else 'FAIL (want B > R+40, B > G+20, luminance > 80)'}")
    print(json.dumps(out))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
