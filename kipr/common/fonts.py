"""Fonts named by KiCad files, and making them available to kicad-cli.

KiCad 7+ text can use any installed font: ``(effects (font (face "Poppins") ...))``. kicad-cli
looks the face up with fontconfig and, when it is missing, logs ``Font 'Poppins' not found;
substituting 'DejaVu Sans Bold'.`` and lays the text out in the substitute. Text sizes change,
so silkscreen plots, 3D exports and text-dependent DRC results (silk_edge_clearance,
silk_overlap, text clearance, ...) differ from the designer's machine.

This module

* collects the faces used by .kicad_pcb / .kicad_sch / .kicad_wks / .kicad_sym / .kicad_mod text
  (:func:`faces_in`), minus the ones the same file embeds (KiCad 10 ``(embedded_files (file
  (name "X.ttf") (type font) (data ...)))``; kicad-cli uses an embedded font whether or not
  ``(embedded_fonts yes)`` is set, so only the font files count, see :func:`embedded_families`);
* checks each face with ``fc-match`` against the fontconfig that kicad-cli will use;
* installs missing ones from font directories the caller provides (``--fonts DIR``) and, unless
  disabled, from Google Fonts (google/fonts at a pinned commit, ``ofl/`` and ``apache/`` families
  only, see :class:`GoogleFonts`) into a private directory, exposed to kicad-cli through a
  generated ``FONTCONFIG_FILE`` that includes the previous configuration;
* reads kicad-cli's own "not found; substituting" messages (:func:`substitutions`), the
  authoritative answer, and marks DRC violations whose items are text in a missing face
  (:func:`mark_font_dependent`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")
TEXT_SUFFIXES = (".kicad_pcb", ".kicad_sch", ".kicad_wks", ".kicad_sym", ".kicad_mod")
BUILTIN = {"kicadfont", ""}  # KiCad's stroke font; never looked up with fontconfig

FACE_RE = re.compile(r'\(face\s+"((?:[^"\\]|\\.)*)"\s*\)')
EMBEDDED_RE = re.compile(r'\(file\s+\(name\s+"((?:[^"\\]|\\.)*)"\s*\)\s*\(type\s+font\s*\)'
                         r'(?:\s*\(data\s+(\|[^|]*\|))?')
SUBST_RE = re.compile(r"Font '(.+?)' not found; substituting '(.*?)'")

GOOGLE_REF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "google_fonts_ref.txt")
# families of google/fonts with a license we accept: (directory, METADATA.pb license, license file)
GOOGLE_LICENSES = (("ofl", "OFL", "OFL.txt"), ("apache", "APACHE2", "LICENSE.txt"))
# words that a face may add to a family name ("Poppins ExtraBold" is in ofl/poppins)
STYLE_WORDS = {"thin", "hairline", "extra", "ultra", "semi", "demi", "light", "extralight", "ultralight",
               "regular", "normal", "book", "medium", "semibold", "demibold", "bold", "extrabold",
               "ultrabold", "black", "heavy", "italic", "oblique", "condensed", "expanded", "narrow"}
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_FAMILY_BYTES = 120 * 1024 * 1024
MAX_FETCHES = 16


def norm(s: str) -> str:
    """Lower-case letters and digits only: how faces and family names are compared."""
    return re.sub(r"[^0-9a-z]", "", s.lower())


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def faces_in(text: str) -> set[str]:
    """Outline font faces named anywhere in a KiCad file (KiCad's stroke font excluded)."""
    return {f for f in (_unescape(m.group(1)) for m in FACE_RE.finditer(text)) if norm(f) not in BUILTIN}


# --- embedded fonts -----------------------------------------------------------------------------

def font_names(data: bytes) -> set[str]:
    """Family, typographic family and full names (name ids 1, 4, 16) of a TrueType/OpenType font
    or collection. Empty for anything else."""
    out: set[str] = set()
    try:
        offsets = [0]
        if data[:4] == b"ttcf":
            n = struct.unpack(">I", data[8:12])[0]
            offsets = list(struct.unpack(f">{n}I", data[12:12 + 4 * n]))
        for off in offsets:
            ntab = struct.unpack(">H", data[off + 4:off + 6])[0]
            for i in range(ntab):
                rec = off + 12 + 16 * i
                tag, _, toff, _ = struct.unpack(">4sIII", data[rec:rec + 16])
                if tag != b"name":
                    continue
                _, count, sto = struct.unpack(">HHH", data[toff:toff + 6])
                for j in range(count):
                    pid, eid, _, nid, ln, so = struct.unpack(">HHHHHH", data[toff + 6 + 12 * j:toff + 18 + 12 * j])
                    if nid not in (1, 4, 16):
                        continue
                    raw = data[toff + sto + so:toff + sto + so + ln]
                    s = raw.decode("utf-16-be", "replace") if pid in (0, 3) else raw.decode("latin-1")
                    if s.strip():
                        out.add(s.strip())
    except (struct.error, IndexError, ValueError):
        return set()
    return out


def _decode(data: str) -> bytes | None:
    try:
        raw = base64.b64decode(data.strip("|"))
        if raw[:4] == b"\x28\xb5\x2f\xfd":
            import zstandard
            return zstandard.ZstdDecompressor().decompressobj().decompress(raw)
        return raw
    except Exception:  # noqa: BLE001  (bad base64/zstd, zstandard missing)
        return None


def embedded_families(text: str) -> set[str]:
    """Normalized names of the font files embedded in a KiCad file. Read from the font itself
    when the payload decodes; the file name stem otherwise ("Poppins-Bold.ttf" -> "poppinsbold")."""
    out: set[str] = set()
    for m in EMBEDDED_RE.finditer(text):
        names = font_names(_decode(m.group(2)) or b"") if m.group(2) else set()
        out |= {norm(n) for n in names}
        stem = os.path.splitext(_unescape(m.group(1)))[0]
        out.add(norm(stem))
        if not names:  # "Poppins-Bold" also stands for the family "Poppins"
            out.add(norm(re.split(r"[-_]", stem)[0]))
    return out


def needed_faces(texts: dict[str, str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``texts``: {file path: KiCad file text}. Returns ({face: files that need it installed},
    {face: files that embed it}). A file's embedded fonts cover only that file."""
    need: dict[str, list[str]] = {}
    emb: dict[str, list[str]] = {}
    for path, text in sorted(texts.items()):
        if not text or "(face" not in text:
            continue
        faces = faces_in(text)
        if not faces:
            continue
        have = embedded_families(text) if "(type font)" in text else set()
        for f in sorted(faces):
            (emb if norm(f) in have else need).setdefault(f, []).append(path)
    return need, emb


# --- fontconfig -------------------------------------------------------------------------------

def base_fontconfig() -> str:
    """The fontconfig file kicad-cli would load without us: $FONTCONFIG_FILE (relative to
    $FONTCONFIG_PATH), else the system default."""
    f = os.environ.get("FONTCONFIG_FILE")
    if f:
        if not os.path.isabs(f) and os.environ.get("FONTCONFIG_PATH"):
            f = os.path.join(os.environ["FONTCONFIG_PATH"].split(os.pathsep)[0], f)
        return os.path.abspath(f)
    return "/etc/fonts/fonts.conf"


def _fc_escape(s: str) -> str:
    return re.sub(r"([\\\-:,])", r"\\\1", s)


def fc_match(face: str, fontconfig_file: str | None = None) -> bool | None:
    """True if fontconfig resolves ``face`` to a font of that family (or full name), False if it
    substitutes, None when fc-match can't run."""
    exe = shutil.which("fc-match")
    if not exe:
        return None
    env = dict(os.environ)
    if fontconfig_file:
        env["FONTCONFIG_FILE"] = fontconfig_file
    try:
        r = subprocess.run([exe, "--format", "%{family}\n%{fullname}\n", _fc_escape(face)], capture_output=True,
                           text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    names = {norm(x) for line in r.stdout.splitlines() for x in line.split(",")}
    return norm(face) in names


def substitutions(message: str) -> dict[str, str]:
    """{face: substitute} from kicad-cli output ("Font 'X' not found; substituting 'Y'.")."""
    return {m.group(1): m.group(2) for m in SUBST_RE.finditer(message or "")}


# --- Google Fonts -----------------------------------------------------------------------------

def google_fonts_ref() -> str:
    with open(GOOGLE_REF_FILE, encoding="utf-8") as fh:
        return fh.read().split()[0]


def default_cache_dir() -> str:
    base = os.environ.get("KIPR_CACHE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "kipr")
    return os.path.join(base, "fonts")


def candidates(face: str) -> list[tuple[str, list[str]]]:
    """google/fonts directory names to try for a face, with the words dropped to get there:
    "Poppins ExtraBold" -> [("poppinsextrabold", []), ("poppins", ["ExtraBold"])]. Only style
    words may be dropped."""
    words = re.findall(r"[A-Za-z0-9]+", face)
    out = []
    for n in range(len(words), 0, -1):
        dropped = words[n:]
        if all(w.lower() in STYLE_WORDS for w in dropped):
            out.append(("".join(words[:n]).lower(), dropped))
    return out


def parse_metadata(text: str) -> dict:
    """The fields we use from a google/fonts METADATA.pb (text protobuf)."""
    top = re.sub(r"\n\w+ \{.*?\n\}", "", "\n" + text, flags=re.S)  # drop nested blocks
    name = re.search(r'^name:\s*"([^"]*)"', top, re.M)
    lic = re.search(r'^license:\s*"([^"]*)"', top, re.M)
    files = re.findall(r'^\s*filename:\s*"([^"]+)"', text, re.M)
    return {"name": name.group(1) if name else "", "license": lic.group(1) if lic else "",
            "files": list(dict.fromkeys(files))}


_SAFE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.,\[\]+-]{0,120}\.(ttf|otf)")


class NotFound(Exception):
    pass


class GoogleFonts:
    """Downloads font families from github.com/google/fonts at a pinned commit (only ``ofl/`` and
    ``apache/`` families; the METADATA.pb license must agree) into ``cache_dir/google-<ref>/``.
    ``$KIPR_GOOGLE_FONTS_URL`` replaces the base URL (tests use a file:// mirror)."""

    def __init__(self, cache_dir: str | None = None, ref: str | None = None, base_url: str | None = None,
                 timeout: float = 60):
        self.ref = ref or google_fonts_ref()
        self.base_url = (base_url or os.environ.get("KIPR_GOOGLE_FONTS_URL")
                         or f"https://raw.githubusercontent.com/google/fonts/{self.ref}").rstrip("/")
        self.cache = os.path.join(cache_dir or default_cache_dir(), f"google-{self.ref[:12]}")
        self.timeout = timeout
        self.fetches = 0

    def _get(self, rel: str, limit: int = MAX_FILE_BYTES) -> bytes:
        url = f"{self.base_url}/{urllib.parse.quote(rel)}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:  # noqa: S310  fixed host/scheme
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(rel) from e
            raise
        except urllib.error.URLError as e:
            if isinstance(e.reason, FileNotFoundError):
                raise NotFound(rel) from e
            raise
        if len(data) > limit:
            raise OSError(f"{rel} is larger than {limit // (1024 * 1024)} MB")
        return data

    def fetch(self, face: str) -> dict | None:
        """{"family", "license", "dir", "files", "source"} for the family that provides ``face``, from
        the cache or downloaded; None if google/fonts has no such family. Raises OSError for
        network problems."""
        for slug, dropped in candidates(face):
            for lic_dir, lic_name, lic_file in GOOGLE_LICENSES:
                d = os.path.join(self.cache, lic_dir, slug)
                manifest = os.path.join(d, ".kipr-font.json")
                if os.path.isfile(manifest):
                    with open(manifest, encoding="utf-8") as fh:
                        m = json.load(fh)
                    if m.get("missing"):
                        continue
                    if self._accept(face, m["family"], dropped):
                        return {**m, "dir": d}
                    continue
                if self.fetches >= MAX_FETCHES:
                    raise OSError(f"more than {MAX_FETCHES} font families to download")
                self.fetches += 1
                try:
                    meta = parse_metadata(self._get(f"{lic_dir}/{slug}/METADATA.pb", 1024 * 1024).decode("utf-8", "replace"))
                except NotFound:
                    self._write_manifest(d, {"missing": True})
                    continue
                if meta["license"].upper() != lic_name:
                    self._write_manifest(d, {"missing": True, "why": f"license {meta['license']!r}"})
                    continue
                files = [f for f in meta["files"] if _SAFE_FILE.fullmatch(f)]
                if not files:
                    self._write_manifest(d, {"missing": True, "why": "no font files"})
                    continue
                m = self._download(d, lic_dir, slug, meta["name"], lic_name, lic_file, files)
                if self._accept(face, m["family"], dropped):
                    return {**m, "dir": d}
        return None

    @staticmethod
    def _accept(face: str, family: str, dropped: list[str]) -> bool:
        words = re.findall(r"[A-Za-z0-9]+", face)
        return norm(family) == norm("".join(words[:len(words) - len(dropped)]))

    def _download(self, d, lic_dir, slug, family, lic_name, lic_file, files) -> dict:
        tmp = tempfile.mkdtemp(prefix=f".{slug}-", dir=self._mkdir(os.path.dirname(d)))
        try:
            total = 0
            for f in files + [lic_file]:
                data = self._get(f"{lic_dir}/{slug}/{f}")
                total += len(data)
                if total > MAX_FAMILY_BYTES:
                    raise OSError(f"{lic_dir}/{slug} is larger than {MAX_FAMILY_BYTES // (1024 * 1024)} MB")
                with open(os.path.join(tmp, f), "wb") as fh:
                    fh.write(data)
            m = {"family": family, "license": lic_name, "files": files, "license_file": lic_file,
                 "source": f"google/fonts@{self.ref[:12]}:{lic_dir}/{slug}"}
            with open(os.path.join(tmp, ".kipr-font.json"), "w", encoding="utf-8") as fh:
                json.dump(m, fh)
            shutil.rmtree(d, ignore_errors=True)
            try:
                os.replace(tmp, d)
            except OSError:  # another process won the race
                pass
            return m
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _mkdir(d: str) -> str:
        os.makedirs(d, exist_ok=True)
        return d

    def _write_manifest(self, d: str, m: dict):
        self._mkdir(d)
        with open(os.path.join(d, ".kipr-font.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh)


# --- the private font directory -----------------------------------------------------------------

def font_files(path: str) -> list[str]:
    """Font files in a directory (recursive) or the file itself."""
    if os.path.isfile(path):
        return [path] if path.lower().endswith(FONT_SUFFIXES) else []
    out = []
    for d, dirs, files in os.walk(path):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        out += [os.path.join(d, f) for f in sorted(files) if f.lower().endswith(FONT_SUFFIXES)]
    return out


class FontDir:
    """Fonts kipr adds for kicad-cli: a directory plus a fonts.conf that includes the previous
    fontconfig and adds the directory. ``env`` is what kicad-cli runs with."""

    def __init__(self, root: str, base: str | None = None):
        self.root = root
        self.dir = os.path.join(root, "fonts")
        self.conf = os.path.join(root, "fonts.conf")
        self.base = base or base_fontconfig()
        os.makedirs(self.dir, exist_ok=True)
        self.files: dict[str, str] = {}  # installed name -> sha256
        self.write_conf()

    def add(self, src: str, subdir: str) -> str:
        name = os.path.join(re.sub(r"[^A-Za-z0-9_.-]", "_", subdir), os.path.basename(src))
        dst = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        with open(dst, "rb") as fh:
            self.files[name] = hashlib.sha256(fh.read()).hexdigest()
        return dst

    def write_conf(self):
        def x(s):
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        with open(self.conf, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
                     f'  <cachedir>{x(os.path.join(self.root, "cache"))}</cachedir>\n'
                     f'  <include ignore_missing="yes">{x(self.base)}</include>\n'
                     f'  <dir>{x(self.dir)}</dir>\n</fontconfig>\n')

    def refresh(self):
        """Build the fontconfig cache once, so parallel kicad-cli runs don't all scan."""
        exe = shutil.which("fc-cache")
        if exe and self.files:
            try:
                subprocess.run([exe, self.dir], env={**os.environ, "FONTCONFIG_FILE": self.conf},
                               capture_output=True, timeout=300)
            except (OSError, subprocess.SubprocessError):
                pass

    @property
    def env(self) -> dict[str, str]:
        return {"FONTCONFIG_FILE": self.conf} if self.files else {}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(sorted(self.files.items())).encode()).hexdigest()[:16] if self.files else ""


@dataclass
class FontSetup:
    """Result of :func:`setup`; ``report()`` is the ``fonts`` object of the review JSON."""
    faces: dict[str, dict] = field(default_factory=dict)  # face -> {"status", "files", ...}
    fontdir: FontDir | None = None
    errors: list[str] = field(default_factory=list)
    checked: bool = True  # False: fc-match unavailable, availability unknown until kicad-cli reports

    @property
    def env(self) -> dict[str, str]:
        return self.fontdir.env if self.fontdir else {}

    @property
    def fingerprint(self) -> str:
        return self.fontdir.fingerprint() if self.fontdir else ""

    def missing(self) -> list[str]:
        return sorted(f for f, v in self.faces.items() if v["status"] == "missing")

    def mark_missing(self, face: str, substitute: str | None = None):
        """kicad-cli reported the face as not found (whatever fc-match said)."""
        v = self.faces.setdefault(face, {"files": []})
        v["status"] = "missing"
        if substitute:
            v["substitute"] = substitute

    def report(self) -> dict:
        return {"faces": [{"face": f, **{k: v for k, v in sorted(d.items())}} for f, d in sorted(self.faces.items())],
                "missing": self.missing(), "errors": self.errors, "checked": self.checked}


def setup(need: dict[str, list[str]], embedded: dict[str, list[str]], root: str, font_dirs=(),
          fetch: bool = True, cache_dir: str | None = None, log=print, fetcher: GoogleFonts | None = None) -> FontSetup:
    """Make the faces in ``need`` ({face: files}) available to kicad-cli: what fontconfig already
    has is left alone; the rest comes from ``font_dirs`` (all their font files are installed),
    then, with ``fetch``, from Google Fonts. ``root`` holds the private font dir and fonts.conf.
    Status per face: system | provided | fetched | embedded | missing."""
    res = FontSetup()
    for f, files in embedded.items():
        res.faces[f] = {"status": "embedded", "files": files}
    if not need and not font_dirs:
        return res
    fd = FontDir(root)
    res.fontdir = fd
    for d in font_dirs:
        files = font_files(d)
        if not files:
            res.errors.append(f"no font files (.ttf/.otf/.ttc) in {d}")
        sub = "provided-" + hashlib.sha256(os.path.abspath(d).encode()).hexdigest()[:8]
        for src in files:
            fd.add(src, sub)
    fd.refresh()
    for face, files in sorted(need.items()):
        prev = res.faces.get(face)
        res.faces[face] = {"status": "system", "files": files}
        if prev:  # embedded by some files, needed by others
            res.faces[face]["embedded_in"] = prev["files"]
    pending = []
    for face in sorted(need):
        ok_base = fc_match(face, fd.base if os.path.exists(fd.base) else None)
        if ok_base is None:
            res.checked = False
        if ok_base:
            continue
        ok_now = fc_match(face, fd.conf) if fd.files else ok_base
        if ok_now:
            res.faces[face]["status"] = "provided"
        else:
            pending.append(face)
    if pending and fetch:
        g = fetcher or GoogleFonts(cache_dir)
        got = {}
        for face in pending:
            try:
                m = g.fetch(face)
            except OSError as e:
                res.errors.append(f"cannot download font '{face}' from Google Fonts: {e}")
                continue
            if m is None:
                continue
            if m["dir"] not in got:
                for f in m["files"]:
                    fd.add(os.path.join(m["dir"], f), "google-" + os.path.basename(m["dir"]))
                got[m["dir"]] = m
            res.faces[face].update({"status": "fetched", "family": m["family"], "license": m["license"],
                                    "source": m["source"]})
            log(f"  font '{face}': {len(m['files'])} file(s) of {m['family']} from {m['source']} ({m['license']})")
        if got:
            fd.refresh()
    for face in pending:
        if res.faces[face]["status"] == "fetched" and res.checked:
            if fc_match(face, fd.conf) is False:
                res.faces[face]["status"] = "missing"
                res.errors.append(f"font '{face}' was downloaded but fontconfig still substitutes it")
        elif res.faces[face]["status"] != "fetched":
            res.faces[face]["status"] = "missing" if res.checked else "unknown"
    return res


# --- DRC: violations that depend on a missing font ----------------------------------------------

TEXT_NODES = {"gr_text", "fp_text", "property", "gr_text_box", "fp_text_box", "text", "dimension",
              "table_cell", "pcb_text"}


def text_index(board_text: str, faces) -> dict:
    """{"uuids": {uuid: face}, "texts": {text: face}} of the board's text items set in one of
    ``faces`` (footprint texts inherit nothing: KiCad stores the face on each text)."""
    wanted = {norm(f): f for f in faces}
    out = {"uuids": {}, "texts": {}}
    if not wanted or "(face" not in board_text:
        return out
    from boarddd.io.kicad.sexpr import parse
    root = parse(board_text)
    stack = [root]
    while stack:
        n = stack.pop()
        for c in n[1:]:
            if not hasattr(c, "name"):
                continue
            stack.append(c)
            if c.name not in TEXT_NODES:
                continue
            eff = c.child("effects")
            font = eff.child("font") if eff is not None else None
            face = font.value("face") if font is not None else None
            if face is None or norm(str(face)) not in wanted:
                continue
            f = wanted[norm(str(face))]
            u = c.value("uuid") or c.value("tstamp")
            if u:
                out["uuids"][str(u).lower()] = f
            atoms = [a for a in c.atoms() if isinstance(a, str)]
            if c.name == "property" and len(atoms) >= 2:
                out["texts"][str(atoms[1])] = f
            elif atoms:
                out["texts"][str(atoms[0])] = f
    return out


_QUOTED = re.compile(r"'(.*)'")


def mark_font_dependent(violations: list[dict], index: dict) -> int:
    """Set ``font_dependent: [faces]`` on violations whose items are text in a missing face (by
    uuid, else by the quoted text in the item description). Returns how many were marked."""
    n = 0
    for v in violations:
        hit = set()
        for u in v.get("uuids") or []:
            f = index["uuids"].get(str(u).lower())
            if f:
                hit.add(f)
        if not hit:
            for it in v.get("items") or []:
                m = _QUOTED.search(it) if "text" in it.lower() else None
                if m and m.group(1) in index["texts"]:
                    hit.add(index["texts"][m.group(1)])
        if hit:
            v["font_dependent"] = sorted(hit)
            n += 1
    return n


def warning_text(faces: list[str]) -> str:
    """The one warning shown in the job summary, report, viewer and PR comment."""
    if not faces:
        return ""
    names = ", ".join(f"'{f}'" for f in faces)
    one = len(faces) == 1
    return (f"Font{'' if one else 's'} {names} {'is' if one else 'are'} not available in CI; KiCad substituted "
            f"{'it' if one else 'them'}, so silkscreen text sizes and text-dependent DRC results "
            "(silk_edge_clearance, silk_overlap, text clearance…) may differ from the designer's machine. "
            "Commit the font files and pass them with the `fonts` input (--fonts DIR).")
