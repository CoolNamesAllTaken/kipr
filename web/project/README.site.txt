kipr project review
===================

This folder is the interactive review of the KiCad projects (schematics, layout, 3D PCBA, BOM,
netlist, ERC/DRC) changed between two commits. Three ways to look at it:

1. Double-click index.html (Chrome, Edge or Firefox).
   Project list, schematic diffs, BOM / netlist / ERC-DRC tables work straight from disk. The
   layout tab shows the per-layer SVG exports there; the gerber render and the 3D view need
   option 2.

2. Run a tiny local web server (Python 3, no packages needed):

       python3 serve.py

   It listens on 127.0.0.1 only, picks a free port, prints the address and opens your browser.
   Everything works this way. Stop it with Ctrl-C.

3. Open project-review.html (if present): a single self-contained page with the summary,
   before/after/diff images and the change tables. No JavaScript, works anywhere, easy to attach.

The report content comes from the pull request. The viewer shows it as plain text and never
runs anything from it.
