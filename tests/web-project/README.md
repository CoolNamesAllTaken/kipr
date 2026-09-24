# Project viewer tests

```sh
cd tests/web-project && npm install          # playwright (dev only)
source /workspace/projects/kipr-tools/bin/pw-env   # fleet container: Chromium libraries + args
bash run_all.sh [--quick] [--real OUT]       # everything below; SHOTS=dir keeps the screenshots
```

| file | what |
|---|---|
| `make_mock.py` | synthetic contract-conformant OUT: 3 projects (modified / added / removed), A4 sheets, RS-274X gerbers, Excellon drills, per-layer SVGs, BOM, netlist, ERC/DRC. `--site` also copies the viewer in. Nothing from a real design. |
| `unit.test.mjs` | `node --test`: routing, ink diff + regions, board frame maths, layer order, pan/zoom maths, URL filters, table filters, view helpers |
| `test_site.py` | `python3 -m unittest`: site copy, file:// bundle, data.js escaping, pack confinement, report content/size cap/ink diff |
| `screens.mjs` | playwright smoke test: every view in light + dark, desktop + narrow; fails on page errors, console errors, failed requests; `--mode file` opens from disk; for any other OUT (`--site`) it derives the views from its JSON |
| `xss_check.mjs` | hostile strings and paths in every field; viewer (http + file://), data.js / packs and report |
| `harness.mjs` | static server, Chromium launcher (`PW_CHROMIUM_ARGS`), settle helper |
