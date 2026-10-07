"""Controlled-impedance net classes for the impedance check (checks.impedance). Plain python3, text edits only.

    python3 scripts/impedance_classes.py base   # pic_programmer: class SE_50_MS on Net-(D8-A)
    python3 scripts/impedance_classes.py head   # pic_programmer: D8-A 0.5 -> 0.4 mm, core 1.51 -> 1.2 mm;
                                                # complex_hierarchy: class SE_50_MS on /status_led/LED_A

The class name follows the convention boarddd reads (SE_<ohms>_<structure>: single-ended 50 Ω microstrip).
Neither board is impedance-controlled, so every row is out of tolerance: pic_programmer's is an existing
violation whose width and stackup change, complex_hierarchy's a new one.
"""
import copy
import json
import re
import sys


def add_class(pro: str, name: str, pattern: str, track_width: float):
    d = json.load(open(pro))
    ns = d["net_settings"]
    c = copy.deepcopy(next(x for x in ns["classes"] if x["name"] == "Default"))
    c.update(name=name, track_width=track_width, priority=0)
    ns["classes"].append(c)
    ns.setdefault("netclass_patterns", []).append({"netclass": name, "pattern": pattern})
    with open(pro, "w") as fh:
        json.dump(d, fh, indent=2)
        fh.write("\n")


def sub1(text: str, pattern: str, repl: str, count: int = 1, flags=0) -> str:
    out, n = re.subn(pattern, repl, text, flags=flags)
    assert n == count, f"{pattern!r}: {n} matches, expected {count}"
    return out


if sys.argv[1] == "base":
    add_class("pic_programmer/pic_programmer.kicad_pro", "SE_50_MS", "Net-(D8-A)", 0.5)
elif sys.argv[1] == "head":
    pcb = "pic_programmer/pic_programmer.kicad_pcb"
    t = open(pcb).read()
    # every D8-A track: (width 0.5)(layer "B.Cu")(net "Net-(D8-A)")
    t = sub1(t, r'\(width 0\.5\)(\s*\(layer "B\.Cu"\)\s*\(net "Net-\(D8-A\)"\))', r"(width 0.4)\1", count=3)
    t = sub1(t, r'(\(layer "dielectric 1"\s*\(type "core"\)\s*\(thickness )1\.51\)', r"\g<1>1.2)")
    open(pcb, "w").write(t)
    add_class("complex_hierarchy/complex_hierarchy.kicad_pro", "SE_50_MS", "/status_led/LED_A", 0.15)
else:
    sys.exit("usage: impedance_classes.py base|head")
