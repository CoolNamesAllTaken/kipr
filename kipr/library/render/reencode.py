"""Tell a KiCad re-encode (a library file re-saved by a newer KiCad, no content change) from an edit.

A symbol library is one file, so a designer who edits one symbol with a newer KiCad re-saves every
symbol in it in the new format, and a footprint opened and saved in a newer KiCad changes text
everywhere. Those parts are not modified, only re-encoded. Telling the two apart must be
conservative: a real edit shown as "re-encoded" hides it from review, which is worse than noise.

The verdict, per changed symbol or footprint (``classify_symbol`` / ``classify_footprint``):

1. **Reference upgrade (the criterion).** The base file is upgraded with ``kicad-cli sym upgrade``
   / ``fp upgrade`` (KiCad's own loader and writer, the code the editor saves with) and the item
   in the result is compared with the item at head, after :func:`canonical`. That only ignores
   whitespace/line breaks, number spelling (``1`` = ``1.0``), uuids, and the file header
   (``version``/``generator``/``generator_version``, which are outside the compared item). It is
   only used when kicad-cli writes the head's file format (same ``version``), i.e. it is the
   KiCad the head was saved with.
2. **Semantic comparison (the safety net).** Both sides go through :func:`semantic`, which applies
   the explicit format normalisations in ``NORMALISATIONS`` (KiCad's defaults for absent
   attributes, flag spellings, where ``hide`` lives) and nothing else, then are compared field by
   field. Everything not covered by a rule must match exactly.

A part is ``re-encoded`` only if (1) says identical and (2) agrees. Without a usable kicad-cli,
symbols may still be ``re-encoded`` when (2) agrees and the base and head renders are pixel
identical (checked by the caller); footprints then stay ``modified``. In every other case the
part stays ``modified`` and :func:`describe` lists exactly what differs.
"""

from __future__ import annotations

import os
import re
import subprocess

from kipr.common import kicad_cli
from kipr.common.sexpr import Atom, Node, dumps, parse

# ---------------------------------------------------------------------------
# normalisations
# ---------------------------------------------------------------------------

#: Every normalisation :func:`semantic` applies, with why it cannot hide an edit. Tested one by one
#: in tests/library/test_library_reencode.py. The reference comparison applies none of them.
NORMALISATIONS = [
    ("whitespace", "Formatting, indentation and line breaks are ignored (s-expressions are compared "
                   "as trees). KiCad never gives whitespace a meaning outside quoted strings."),
    ("numbers", "Numbers are compared as values rounded to 1e-6 (`1` = `1.0` = `1.000000`, `-0` = `0`). "
                "KiCad stores library coordinates in 1e-4 mm (symbols) and 1e-6 mm (footprints)."),
    ("uuid", "`uuid`/`tstamp` nodes are dropped: they identify an object, they don't describe it. "
             "KiCad generates uuids for objects that had none when it loads an old file."),
    ("header", "The library file header (`version`, `generator`, `generator_version`) is not part "
               "of an item; the format change is reported separately."),
    ("property-id", "`(id N)` in symbol properties (KiCad 6/7) is dropped: KiCad 8 removed it and "
                    "identifies fields by name."),
    ("flag-spelling", "Bare flag atoms (`hide`, `bold`, `italic`, written by KiCad 6/7) mean the same "
                      "as `(hide yes)`, `(bold yes)`, `(italic yes)` (KiCad 8+)."),
    ("hide-location", "`(hide yes)` inside `(effects …)` (KiCad ≤ 9) and `(hide yes)` on the field "
                      "itself (KiCad 10) are the same visibility flag, so it is compared on the field."),
    ("defaults", "Attributes written with KiCad's default value are the same as absent ones: "
                 "`(hide no)`, `(bold no)`, `(italic no)`, `(show_name no)`, `(do_not_autoplace no)`, "
                 "`(exclude_from_sim no)`, `(in_bom yes)`, `(on_board yes)`, `(in_pos_files yes)`, "
                 "`(duplicate_pin_numbers_are_jumpers no)`, `(embedded_fonts no)`. A non-default "
                 "value is never dropped."),
    ("default-color", "`(color 0 0 0 0)` in a `stroke` or `fill` is KiCad's \"use the default colour\"; "
                      "KiCad 8+ omits it. Any other colour is compared."),
    ("ki-description", "A `ki_description` property (KiCad ≤ 7) is the `Description` field KiCad 8 converts "
                       "it to, if the symbol has no non-empty `Description` of its own. The text, position "
                       "and effects are still compared."),
    ("empty-description", "An empty `Description` field is the same as none: KiCad 8 adds one to every "
                          "symbol. An empty field draws nothing; a non-empty one is always compared."),
    ("arc-direction", "An arc start→mid→end is the same arc as end→mid→start (KiCad 8 reverses some). "
                      "All three points are still compared."),
    ("item-order", "The order of child nodes is ignored (KiCad sorts graphic items and pins by type "
                   "when it saves; attributes are keyed by name). Point lists (`pts`) keep their order, "
                   "and atoms (e.g. `justify left bottom`, pin type and shape) always keep theirs."),
]

FLAG_ATOMS = {"hide", "bold", "italic"}
FLAG_OWNERS = {"effects", "font", "pin", "pin_names", "pin_numbers", "property", "text", "fp_text",
               "name", "number"}
DEFAULTS = {
    "hide": "no", "bold": "no", "italic": "no", "show_name": "no", "do_not_autoplace": "no",
    "exclude_from_sim": "no", "in_bom": "yes", "on_board": "yes", "in_pos_files": "yes",
    "duplicate_pin_numbers_are_jumpers": "no", "embedded_fonts": "no",
}
IDENTITY = ("uuid", "tstamp")
HEADER = ("version", "generator", "generator_version")
SYM_GRAPHICS = {"rectangle", "circle", "arc", "polyline", "bezier", "text", "text_box"}


def canonical(node) -> str:
    """The reference comparison's canonical form: whitespace, number spelling and uuids ignored."""
    return dumps(node, drop=IDENTITY + HEADER)


def _copy(n):
    if isinstance(n, Node):
        c = Node(_copy(x) for x in n)
        c.start, c.end, c.line_start, c.line_end = n.start, n.end, n.line_start, n.line_end
        return c
    return n


def semantic(node: Node) -> Node:
    """A copy of ``node`` with the ``NORMALISATIONS`` applied (see the module docstring)."""
    return _sem(_copy(node))


def _sem(n: Node) -> Node:
    name = n.name
    out = Node([n[0]] if n else [])
    out.start, out.end, out.line_start, out.line_end = n.start, n.end, n.line_start, n.line_end
    for c in n[1:]:
        if isinstance(c, Node):
            if c.name in IDENTITY or c.name in HEADER:
                continue
            if c.name == "id" and name == "property":
                continue
            out.append(_sem(c))
        elif isinstance(c, Atom) and str(c) in FLAG_ATOMS and name in FLAG_OWNERS:
            out.append(Node([Atom(str(c)), Atom("yes")]))
        else:
            out.append(c)
    # hide-location: (effects (hide yes)) -> (hide yes) on the owner, unless the owner says itself
    eff = out.child("effects")
    if eff is not None and out.child("hide") is None:
        h = eff.child("hide")
        if h is not None:
            eff[:] = [x for x in eff if x is not h]
            out.append(h)
    # defaults: a value equal to KiCad's default is the same as no value
    out[1:] = [c for c in out[1:] if not (isinstance(c, Node) and c.name in DEFAULTS and len(c) == 2
                                            and str(c[1]) == DEFAULTS[c.name])]
    # default-color: (color 0 0 0 0) in a stroke or fill means "use the default colour"
    if name in ("stroke", "fill"):
        out[1:] = [c for c in out[1:] if not (isinstance(c, Node) and c.name == "color"
                                                and c.nums() == [0.0, 0.0, 0.0, 0.0])]
    if name == "symbol" and n.child("property") is not None:
        _description_field(out)
    if name == "arc":
        _arc_direction(out)
    return out


def _arc_direction(arc: Node):
    """arc-direction: start→mid→end is the same arc as end→mid→start (KiCad 8 flipped some)."""
    s, m, e = arc.child("start"), arc.child("mid"), arc.child("end")
    if s is None or m is None or e is None:
        return
    if [dumps(a) for a in e.atoms()] < [dumps(a) for a in s.atoms()]:
        s[1:], e[1:] = list(e[1:]), list(s[1:])


def _description_field(sym: Node):
    """ki-description: KiCad 8 turned the ``ki_description`` property into the ``Description`` field."""
    kd = [c for c in sym[1:] if isinstance(c, Node) and c.name == "property" and str(c.arg(0, "")) == "ki_description"]
    desc = [c for c in sym[1:] if isinstance(c, Node) and c.name == "property" and str(c.arg(0, "")) == "Description"]
    if len(kd) != 1:
        # empty-description: KiCad 8 gives every symbol a Description field, empty if there was none
        if len(desc) == 1 and str(desc[0].arg(1, "")) == "":
            sym[:] = [x for x in sym if x is not desc[0]]
        return
    if len(desc) > 1 or (desc and str(desc[0].arg(1, "")) != ""):
        return
    if desc:
        sym[:] = [x for x in sym if x is not desc[0]]
    i = next(i for i, x in enumerate(kd[0]) if isinstance(x, str) and str(x) == "ki_description")
    kd[0][i] = "Description"


# ---------------------------------------------------------------------------
# field-by-field comparison
# ---------------------------------------------------------------------------

def _num(a) -> str:
    return dumps(a) if isinstance(a, Atom) else dumps(a)


def _short(node, limit: int = 120) -> str:
    s = dumps(node) if isinstance(node, Node) else _num(node)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _label(c: Node, parent_name: str) -> str:
    """Identity of a child item, used to pair base and head items."""
    n = c.name
    if n == "property":
        return f'property "{c.arg(0, "")}"'
    if n == "symbol":
        m = re.match(r".*_(\d+)_(\d+)$", str(c.arg(0, "")))
        return f"unit {m.group(1)} style {m.group(2)}" if m else f'symbol "{c.arg(0, "")}"'
    if n == "pin":
        num = c.child("number")
        return f'pin "{num.arg(0, "") if num is not None else "?"}"'
    if n == "pad":
        return f'pad "{c.arg(0, "")}"'
    if n == "model":
        return f'model "{c.arg(0, "")}"'
    if n == "fp_text":
        return f"fp_text {c.arg(0, '')}"
    return n


def _children(node: Node) -> dict[str, list]:
    out: dict[str, list] = {}
    for c in node[1:]:
        if isinstance(c, Node):
            out.setdefault(_label(c, node.name), []).append(c)
    return out


#: nodes whose children are a sequence (points of a polyline/polygon/bezier)
ORDERED = {"pts"}
MULTISET = SYM_GRAPHICS | {"fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve",
                           "pin", "pad", "zone", "group"}


def describe(base: Node, head: Node, path: str = "", limit: int = 60) -> list[str]:
    """Human-readable list of what differs between two (normalised or canonical) item trees."""
    out: list[str] = []
    _describe(base, head, path, out)
    if len(out) > limit:
        out = out[:limit] + [f"… {len(out) - limit} more difference(s)"]
    return out


def _describe(b: Node, h: Node, path: str, out: list[str]):
    where = f"{path}: " if path else ""
    ba, ha = b.atoms(), h.atoms()
    if [_num(a) for a in ba] != [_num(a) for a in ha]:
        what = "value" if b.name == "property" else "arguments"
        out.append(f"{where}{what} {' '.join(_num(a) for a in ba) or '(none)'} → "
                   f"{' '.join(_num(a) for a in ha) or '(none)'}")
    bc, hc = _children(b), _children(h)
    for key in list(dict.fromkeys(list(bc) + list(hc))):
        bl, hl = bc.get(key, []), hc.get(key, [])
        sub = f"{path} › {key}" if path else key
        if key.split(" ")[0] in MULTISET or (key.startswith("pin ") or key.startswith("pad ")):
            _describe_multiset(bl, hl, sub, out)
            continue
        for i in range(max(len(bl), len(hl))):
            bi = bl[i] if i < len(bl) else None
            hi = hl[i] if i < len(hl) else None
            label = sub if max(len(bl), len(hl)) == 1 else f"{sub} #{i + 1}"
            if bi is None:
                out.append(f"{label}: added {_short(hi)}")
            elif hi is None:
                out.append(f"{label}: removed {_short(bi)}")
            elif dumps(bi) != dumps(hi):
                if len(bi) <= 2 and len(hi) <= 2 and not any(isinstance(x, Node) for x in bi[1:] + hi[1:]):
                    out.append(f"{label}: {_short(bi)} → {_short(hi)}")
                else:
                    _describe(bi, hi, label, out)


def _describe_multiset(bl: list, hl: list, path: str, out: list[str]):
    bs = [dumps(x) for x in bl]
    hs = [dumps(x) for x in hl]
    rest_b = list(bs)
    added = []
    for s in hs:
        if s in rest_b:
            rest_b.remove(s)
        else:
            added.append(s)
    removed = rest_b
    if not added and not removed:
        return
    if len(added) == 1 and len(removed) == 1:
        # one item changed in place: say how
        _describe(bl[bs.index(removed[0])], hl[hs.index(added[0])], path, out)
        return
    for s in removed:
        out.append(f"{path}: removed {_short(bl[bs.index(s)])}")
    for s in added:
        out.append(f"{path}: added {_short(hl[hs.index(s)])}")


def same_semantics(base: Node, head: Node) -> tuple[bool, list[str]]:
    """(equal?, differences) of the two items after :func:`semantic`."""
    sb, sh = semantic(base), semantic(head)
    if _multiset_key(sb) == _multiset_key(sh):
        return True, []
    diffs = describe(sb, sh)
    return False, diffs or ["the items differ (order of properties or nested items)"]


def _multiset_key(n) -> str:
    """dumps() with child nodes sorted (order-insensitive), except where order is geometry."""
    if not isinstance(n, Node):
        return dumps(n)
    head = [dumps(a) for a in n if not isinstance(a, Node)]
    kids = [_multiset_key(c) for c in n if isinstance(c, Node)]
    if n.name not in ORDERED:
        kids.sort()
    return "(" + " ".join(head + kids) + ")"


# ---------------------------------------------------------------------------
# kicad-cli reference upgrade
# ---------------------------------------------------------------------------

def file_version(root: Node | None) -> str | None:
    v = root.value("version") if root is not None else None
    return str(v) if v is not None else None


def generator_version(root: Node | None) -> str | None:
    v = root.value("generator_version") if root is not None else None
    return str(v) if v is not None else None


class Upgrader:
    """Runs ``kicad-cli sym upgrade`` / ``fp upgrade`` on base texts; results are cached."""

    def __init__(self, exe: str | None, timeout: float = 300):
        self.exe = exe
        self.timeout = timeout
        self.version = None
        self._cache: dict[tuple, tuple] = {}
        if exe:
            try:
                r = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=120)
                self.version = (r.stdout.strip().splitlines() or ["?"])[-1] if r.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                self.version = None
            if self.version is None:
                self.exe = None

    @property
    def available(self) -> bool:
        return bool(self.exe)

    def symbol_library(self, text: str) -> tuple[str | None, str]:
        """(upgraded library text or None, error message)."""
        key = ("symbol", text, None)
        if key not in self._cache:
            self._cache[key] = kicad_cli.upgrade_text(self.exe, "symbol", text, timeout=self.timeout)
        return self._cache[key]

    def footprint(self, text: str, filename: str) -> tuple[str | None, str]:
        """(upgraded footprint text or None, error message); KiCad names it after ``filename``."""
        key = ("footprint", text, os.path.basename(filename))
        if key not in self._cache:
            self._cache[key] = kicad_cli.upgrade_text(self.exe, "footprint", text, filename, timeout=self.timeout)
        return self._cache[key]


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------

def _kicad_name(gv: str | None) -> str:
    return f"KiCad {gv}" if gv else "a newer KiCad"


def _result(reencoded: bool, method: str, base_root, head_root, differences=(), note: str = "",
            kicad_cli_version: str | None = None) -> dict:
    bv, hv = file_version(base_root), file_version(head_root)
    gv = generator_version(head_root)
    r = {"reencoded": reencoded, "method": method,
         "base_format": bv, "head_format": hv,
         "base_generator_version": generator_version(base_root), "head_generator_version": gv,
         "kicad_cli_version": kicad_cli_version,
         "differences": list(differences)}
    if reencoded:
        if bv and hv and bv != hv:
            r["explanation"] = f"file format upgraded {bv} → {hv} by {_kicad_name(gv)}; no content change"
        else:
            r["explanation"] = f"re-saved by {_kicad_name(gv)}; no content change"
    if note:
        r["note"] = note
    return r


def _chain(lib: dict, node: Node) -> list[Node]:
    """The symbol and its 'extends' ancestors (a derived symbol is drawn from its parent)."""
    out, seen = [node], {str(node.arg(0, ""))}
    while True:
        ext = out[-1].value("extends")
        if not ext or ext in seen or ext not in lib:
            return out
        seen.add(ext)
        out.append(lib[ext])


def _names(chain: list[Node]) -> list[str]:
    return [str(n.arg(0, "")) for n in chain]


def _chain_diffs(a: list[Node], b: list[Node], same) -> list[str]:
    """Differences between two 'extends' chains; ``same(x, y)`` -> (equal?, differences)."""
    if _names(a) != _names(b):
        return [f"`extends`: parent symbol {' → '.join(_names(a)[1:]) or '(none)'} → "
                f"{' → '.join(_names(b)[1:]) or '(none)'}"]
    out = []
    for i, (x, y) in enumerate(zip(a, b)):
        ok, d = same(x, y)
        if not ok:
            pre = "" if i == 0 else f"parent symbol `{y.arg(0, '')}`: "
            out += [pre + s for s in d]
    return out


def _same_canonical(x: Node, y: Node) -> tuple[bool, list[str]]:
    if canonical(x) == canonical(y):
        return True, []
    return False, describe(x, y) or ["the item text differs"]


def _upgraded_lib(upgrader: Upgrader, text: str) -> tuple[Node | None, dict, str]:
    from .sym import parse_library
    up_text, err = upgrader.symbol_library(text)
    if not up_text:
        return None, {}, err
    try:
        root = parse(up_text)
    except Exception as e:  # noqa: BLE001 - a broken upgrade counts as unavailable
        return None, {}, f"cannot parse the kicad-cli output: {e}"
    return root, parse_library(root), ""


def classify_symbol(name: str, base_root: Node, base_lib: dict, head_root: Node, head_lib: dict,
                    base_text: str, upgrader: Upgrader | None, render_identical=None,
                    head_text: str | None = None) -> dict:
    """Verdict for a symbol present at base and head whose text changed.

    ``render_identical``: callable returning True/False/None (pixel-identical base/head renders);
    only consulted when KiCad's reference upgrade is not available."""
    bchain, hchain = _chain(base_lib, base_lib[name]), _chain(head_lib, head_lib[name])
    sem_diffs = _chain_diffs(bchain, hchain, same_semantics)
    cli_ver = upgrader.version if upgrader else None
    hv = file_version(head_root)
    res = lambda ok, method, diffs=(), note="": _result(  # noqa: E731
        ok, method, base_root, head_root, diffs, note=note, kicad_cli_version=cli_ver)
    roundtrip_diffs = None
    if upgrader is not None and upgrader.available:
        up_root, up_lib, err = _upgraded_lib(upgrader, base_text)
        if up_root is not None and file_version(up_root) == hv:
            # (1) the reference: KiCad's own upgrade of the base vs the head
            if name not in up_lib:
                return res(False, "reference-upgrade", sem_diffs or [f"`{name}` is missing from KiCad's upgrade of the base"])
            ref_diffs = _chain_diffs(_chain(up_lib, up_lib[name]), hchain, _same_canonical)
            if ref_diffs:
                return res(False, "reference-upgrade", ref_diffs)
            if sem_diffs:  # (2) the safety net disagrees with KiCad: never call that a re-encode
                return res(False, "reference-upgrade", sem_diffs,
                           "identical to KiCad's upgrade of the base, but the semantic comparison found "
                           "differences; kept as modified")
            return res(True, "reference-upgrade")
        if up_root is not None:
            err = (f"kicad-cli {cli_ver} writes symbol format {file_version(up_root)}, the head is "
                   f"{hv} (saved by {_kicad_name(generator_version(head_root))})")
            # not the head's KiCad: still let this KiCad load and re-save both sides; any
            # difference keeps the symbol modified (this can only make the verdict stricter)
            if head_text is not None:
                h_root, h_lib, _e = _upgraded_lib(upgrader, head_text)
                if h_root is not None and name in up_lib and name in h_lib:
                    roundtrip_diffs = _chain_diffs(_chain(up_lib, up_lib[name]), _chain(h_lib, h_lib[name]),
                                                   _same_canonical)
        note = f"reference upgrade unavailable: {err}"
    else:
        note = "reference upgrade unavailable: no kicad-cli"
    if sem_diffs:
        return res(False, "semantic", sem_diffs, note)
    if roundtrip_diffs:
        return res(False, "semantic", roundtrip_diffs,
                   note + f"; kicad-cli {cli_ver} re-saves base and head differently")
    pix = render_identical() if render_identical else None
    if pix is True:
        return res(True, "semantic+render", (), note)
    return res(False, "semantic",
               ["no field differs after the format normalisations, but "
                + ("the base and head renders differ" if pix is False else "the renders could not be compared")
                + " and KiCad's reference upgrade was not available"], note)


def classify_footprint(path: str, base_root: Node, head_root: Node, base_text: str,
                       upgrader: Upgrader | None) -> dict:
    """Verdict for a footprint file present at base and head whose content changed.

    Footprints are only ever ``re-encoded`` on KiCad's reference upgrade (no fallback)."""
    ok, sem_diffs = same_footprint_facts(base_root, head_root)
    cli_ver = upgrader.version if upgrader else None
    hv = file_version(head_root)
    res = lambda ok_, method, diffs=(), note="": _result(  # noqa: E731
        ok_, method, base_root, head_root, diffs, note=note, kicad_cli_version=cli_ver)
    unconfirmed = ["nothing differs in the footprint's geometry, pads, texts or properties, but "
                   "KiCad's reference upgrade was not available to confirm a pure re-encode"]
    if upgrader is None or not upgrader.available:
        return res(False, "semantic", sem_diffs or unconfirmed, "reference upgrade unavailable: no kicad-cli")
    up_text, err = upgrader.footprint(base_text, path)
    up_root = None
    if up_text:
        try:
            up_root = parse(up_text)
        except Exception as e:  # noqa: BLE001
            err = f"cannot parse the kicad-cli output: {e}"
    if up_root is None or file_version(up_root) != hv:
        if up_root is not None:
            err = (f"kicad-cli {cli_ver} writes footprint format {file_version(up_root)}, the head is "
                   f"{hv} (saved by {_kicad_name(generator_version(head_root))})")
        return res(False, "semantic", sem_diffs or unconfirmed, f"reference upgrade unavailable: {err}")
    same, ref_diffs = _same_canonical(up_root, head_root)
    if not same:
        return res(False, "reference-upgrade", ref_diffs)
    if not ok:
        return res(False, "reference-upgrade", sem_diffs,
                   "identical to KiCad's upgrade of the base, but the footprint comparison found "
                   "differences; kept as modified")
    return res(True, "reference-upgrade")


# ---------------------------------------------------------------------------
# footprint safety net: the renderer's version-independent model
# ---------------------------------------------------------------------------

#: Footprint properties KiCad treats as empty when absent (KiCad 8 writes them, 9+ may drop them).
FP_OPTIONAL_PROPS = ("Footprint", "Datasheet", "Description")

#: What the footprint safety net (``footprint_facts``) normalises on top of kipr's footprint parser.
#: The net can only keep a footprint ``modified``; it never makes one ``re-encoded`` on its own
#: (that needs KiCad's reference upgrade), so these only decide how often it raises a false alarm.
FP_NORMALISATIONS = [
    ("fp-name", "The footprint's name is not compared: KiCad names a footprint after its file, and "
                "base and head are the same file."),
    ("fp-empty-props", "Empty `Footprint`/`Datasheet`/`Description` properties are the same as absent ones."),
    ("fp-pad-layers", "A pad's layer list is a set (`F.Cu F.Paste F.Mask` = `F.Cu F.Mask F.Paste`)."),
    ("fp-arc-direction", "An arc start→mid→end is the same arc as end→mid→start."),
    ("fp-text-angle", "Text angles are compared modulo 360° (`-180` = `180`)."),
    ("fp-text-vars", "`%R`/`%V` (KiCad 5) are `${REFERENCE}`/`${VALUE}`."),
    ("fp-unlocked", "The `unlocked` editing flag of texts is not compared (KiCad 8 changed its default; "
                    "it only affects editing in KiCad)."),
    ("fp-attr-tht", "No footprint type in `attr` (KiCad 5) is a through-hole footprint."),
    ("fp-closed-poly", "A zone outline whose last point repeats the first is the same outline without it."),
    ("item-order", "Graphics, texts, pads and zones are compared as multisets."),
]


def _r(v):
    if isinstance(v, float):
        v = round(v, 6) + 0.0
        return 0.0 if v == 0 else v
    if isinstance(v, (list, tuple)):
        return [_r(x) for x in v]
    if isinstance(v, dict):
        return {k: _r(x) for k, x in sorted(v.items()) if k != "line"}
    return v


def footprint_facts(root: Node) -> dict:
    """What a footprint draws and means, from kipr's own parser (reads KiCad 5-10 alike)."""
    import json

    from .fp import Footprint
    m = Footprint(root)
    props = {k: v for k, v in m.properties.items() if not (k in FP_OPTIONAL_PROPS and v == "")}
    texts = []
    for t in m.texts:
        if t["hidden"] and t["text"] == "":
            continue
        t = dict(t)
        t.pop("unlocked", None)
        t["angle"] = round(t.get("angle", 0.0) % 360.0, 6) % 360.0
        t["text"] = t["text"].replace("%R", "${REFERENCE}").replace("%V", "${VALUE}")
        texts.append(t)
    graphics = []
    for g in m.graphics:
        g = dict(g)
        if g.get("arc"):
            a = _r(list(g["arc"]))
            g["arc"] = min(a, a[::-1])
        graphics.append(g)
    pads = [dict(p, layers=sorted(p["layers"])) for p in m.pads]
    zones = []
    for z in m.zones:
        polys = [list(p[:-1]) if len(p) > 2 and _r(p[0]) == _r(p[-1]) else list(p) for p in z["polys"]]
        zones.append(dict(z, polys=polys, layers=sorted(z["layers"])))
    attr = sorted(m.attr)
    if not {"smd", "through_hole"} & set(attr):
        attr = sorted(attr + ["through_hole"])
    ms = lambda xs: sorted(json.dumps(_r(x), sort_keys=True) for x in xs)  # noqa: E731
    return {
        "attr": attr, "descr": m.descr, "tags": m.tags, "properties": props,
        "texts": ms(texts), "graphics": ms(graphics), "pads": ms(pads), "zones": ms(zones),
        "models": [json.dumps(_r(x), sort_keys=True) for x in m.models],
        "embedded": {k: v.get("data") for k, v in sorted(m.embedded.items())},
    }


def _field_diff(a: str, b: str) -> str:
    import json
    da, db = json.loads(a), json.loads(b)
    keys = [k for k in dict.fromkeys(list(da) + list(db)) if da.get(k) != db.get(k)]
    return ", ".join(f"{k} {json.dumps(da.get(k))[:60]} → {json.dumps(db.get(k))[:60]}" for k in keys)


def same_footprint_facts(base: Node, head: Node) -> tuple[bool, list[str]]:
    fb, fh = footprint_facts(base), footprint_facts(head)
    out = []
    for k in fb:
        a, b = fb[k], fh[k]
        if a == b:
            continue
        if isinstance(a, list) and k != "attr":
            ra, rb = list(a), list(b)
            for x in a:
                if x in rb:
                    rb.remove(x)
                    ra.remove(x)
            if len(ra) == len(rb):
                out += [f"{k[:-1]}: {_field_diff(x, y)}" for x, y in zip(ra, rb)]
            else:
                out += [f"{k[:-1]} removed: {x[:160]}" for x in ra] + [f"{k[:-1]} added: {x[:160]}" for x in rb]
        elif isinstance(a, dict):
            for key in dict.fromkeys(list(a) + list(b)):
                if a.get(key) != b.get(key):
                    out.append(f'{k} "{key}": {str(a.get(key))[:80]!r} → {str(b.get(key))[:80]!r}')
        else:
            out.append(f"{k}: {str(a)[:80]!r} → {str(b)[:80]!r}")
    return not out, out
