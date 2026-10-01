"""kipr.common.kicad_cli.upgrade_text (shared by the KLC check and re-encode detection).

    python -m pytest tests/library/test_kicad_cli_upgrade.py
The real-kicad-cli test runs when kicad-cli is on PATH or in $KIPR_KICAD_CLI / $KICAD_CLI.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import unittest

from kipr.common import kicad_cli

# Stand-in kicad-cli: records its argv and "upgrades" by copying input -> output with a
# rewritten (version ...). FAKE_MODE=fail exits 3, FAKE_MODE=noout succeeds without output.
FAKE = r"""#!/usr/bin/env python3
import os, re, shutil, sys
args = sys.argv[1:]
listing = sorted(os.listdir(args[-1])) if os.path.isdir(args[-1]) else []
open(os.environ["FAKE_ARGV"], "w").write("\n".join(args + ["LS"] + listing))
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "fail":
    print("Unable to load library", file=sys.stderr); sys.exit(3)
if mode == "noout":
    sys.exit(0)
dst, src = args[args.index("--output") + 1], args[-1]
up = lambda t: re.sub(r"\(version \d+\)", "(version 20990101)", t, count=1)
if args[0] == "sym":
    open(dst, "w").write(up(open(src).read()))
else:
    os.makedirs(dst)
    for f in os.listdir(src):
        open(os.path.join(dst, f), "w").write(up(open(os.path.join(src, f)).read()))
"""

SYM = '(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") (symbol "R" (in_bom yes) (on_board yes)))\n'
FP = '(footprint "Conn(51)" (version 20241229) (generator "pcbnew") (layer "F.Cu"))\n'


class FakeUpgrade(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.exe = os.path.join(self.td.name, "kicad-cli")
        with open(self.exe, "w") as f:
            f.write(FAKE)
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IEXEC)
        self.argv = os.path.join(self.td.name, "argv")
        os.environ["FAKE_ARGV"] = self.argv
        os.environ.pop("FAKE_MODE", None)

    def tearDown(self):
        os.environ.pop("FAKE_MODE", None)
        self.td.cleanup()

    def args(self):
        return open(self.argv).read().split("\n")

    def test_symbol(self):
        out, err = kicad_cli.upgrade_text(self.exe, "symbol", SYM)
        self.assertEqual(err, "")
        self.assertIn("(version 20990101)", out)
        a = self.args()
        self.assertEqual(a[:4], ["sym", "upgrade", "--force", "--output"])
        self.assertTrue(a[4].endswith("out.kicad_sym") and a[5].endswith("in.kicad_sym"))
        self.assertEqual(a[6:], ["LS"])

    def test_footprint_keeps_file_name(self):
        out, err = kicad_cli.upgrade_text(self.exe, "footprint", FP, "lib_fp/X.pretty/Conn(51).kicad_mod")
        self.assertEqual(err, "")
        self.assertIn("(version 20990101)", out)
        a = self.args()
        self.assertEqual(a[:4], ["fp", "upgrade", "--force", "--output"])
        self.assertTrue(a[4].endswith("out.pretty") and a[5].endswith("in.pretty"))
        # the input dir held the file under its own name (KiCad names the footprint after it)
        self.assertEqual(a[a.index("LS") + 1:], ["Conn(51).kicad_mod"])

    def test_footprint_name_fallbacks(self):
        for fn, name in ((None, "footprint.kicad_mod"), ("..", "footprint.kicad_mod"), ("lib_fp/x", "x.kicad_mod")):
            out, err = kicad_cli.upgrade_text(self.exe, "footprint", FP, fn)
            self.assertEqual(err, "", fn)
            self.assertIsNotNone(out)
            self.assertEqual(self.args()[-1], name)

    def test_failures(self):
        os.environ["FAKE_MODE"] = "fail"
        out, err = kicad_cli.upgrade_text(self.exe, "symbol", SYM)
        self.assertIsNone(out)
        self.assertEqual(err, "kicad-cli sym upgrade failed (exit 3): Unable to load library")
        os.environ["FAKE_MODE"] = "noout"
        out, err = kicad_cli.upgrade_text(self.exe, "footprint", FP, "a.kicad_mod")
        self.assertIsNone(out)
        self.assertEqual(err, "kicad-cli fp upgrade wrote no output")
        out, err = kicad_cli.upgrade_text(os.path.join(self.td.name, "missing"), "symbol", SYM)
        self.assertIsNone(out)
        self.assertIn("kicad-cli sym upgrade failed (exit -1)", err)
        with self.assertRaises(ValueError):
            kicad_cli.upgrade_text(self.exe, "board", SYM)


@unittest.skipUnless(kicad_cli.find(), "needs kicad-cli")
class RealUpgrade(unittest.TestCase):
    def test_symbol_and_footprint(self):
        exe = kicad_cli.find()
        sym = ('(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")\n'
               '  (symbol "R" (in_bom yes) (on_board yes)\n'
               '    (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
               '    (property "Value" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))))\n')
        out, err = kicad_cli.upgrade_text(exe, "symbol", sym)
        self.assertEqual(err, "")
        self.assertNotIn("(version 20241209)", out)
        self.assertIn('(symbol "R"', out)
        out, err = kicad_cli.upgrade_text(exe, "footprint", FP, "Conn(51).kicad_mod")
        self.assertEqual(err, "")
        self.assertRegex(out, r'^\(footprint "Conn\(51\)"')
        self.assertTrue(re.search(r"\(version (\d+)\)", out))
        out, err = kicad_cli.upgrade_text(exe, "symbol", "(garbage")
        self.assertIsNone(out)
        self.assertTrue(err.startswith("kicad-cli sym upgrade failed"), err)


if __name__ == "__main__":
    unittest.main()
