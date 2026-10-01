"""Optional: run KiCad's official KLC checkers (gitlab.com/kicad/libraries/kicad-library-utils).

kicad-library-utils is not on PyPI; CI clones it at a pinned commit and passes
`--klc-utils DIR` (or CR_KLC_UTILS=DIR). The checkers derive the expected name
from the file name/dir, so each item's standalone source is copied into
`<Library>.pretty/<name>.kicad_mod` / `<Library>.kicad_sym` in a temp dir first.
Output is read from the JUnit report. They only parse the files (nothing from OUT
is executed).

Fail safe: a run only counts as checked when the JUnit report has a test case and
agrees with the exit code (0 = clean, 2 = warnings, 3 = errors). Anything else (a
crash/traceback, "Could not parse", e.g. the symbol checker only accepts its own
file version, a timeout, a killed process, a missing/unreadable/empty report) is
a KlcResult with `error` set, which the checks stage reports as "KLC could not
check this item" rather than as a pass. Results that look like a flake (killed,
or exit code and report disagree: check_symbol.py can lose a worker's results
when the worker exits right after posting them) are retried once; crashes and
parse errors are deterministic and are not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

TIMEOUT_S = 120
RETRIES = 1  # extra attempts after a result that looks like a flake

# Rules that conflict with this repo's conventions or cannot be evaluated here.
_IGNORE = (
    # the repo stores models in <models dir>/<Library>/ via ${KICAD_LIBS_DIR}, not <lib>.3dshapes
    re.compile(r"3D model directory is different from footprint directory"),
    re.compile(r"3D model path|\$\{KICAD\d*_3DMODEL_DIR\}", re.I),
    # needs the whole footprint library; pairing is checked by our own code
    re.compile(r"footprint existence is not going to be checked"),
    # duplicated by our own deterministic ${REFERENCE}-on-F.Fab check (which cites a line)
    re.compile(r"Second Reference Designator missing|Add RefDes to F.Fab"),
)
# Rules that are routinely (and legitimately) broken by vendor STEP models / connectors: info only.
_SOFT = re.compile(r"3D model (offset|rotation|name) is|More than one 3D model|anchor does not match", re.I)


def available(klu_dir: str | None) -> bool:
    return bool(klu_dir) and os.path.isfile(os.path.join(klu_dir, "klc-check", "check_footprint.py"))


@dataclass
class KlcResult:
    """Outcome of one item's KLC check. `error` is None only when the checker really ran."""
    findings: list[dict] = field(default_factory=list)
    error: str | None = None          # short reason when the item could not be checked
    stderr_tail: str = ""             # last lines of the checker's stderr/stdout, for the finding
    attempts: int = 1
    retried_because: str | None = None  # first attempt's reason when a retry then succeeded

    @property
    def ok(self) -> bool:
        return self.error is None


class _Failed(Exception):
    def __init__(self, reason: str, retry: bool, tail: str = ""):
        super().__init__(reason)
        self.reason, self.retry, self.tail = reason, retry, tail


def _tail(*texts: str, n: int = 4, limit: int = 600) -> str:
    """Last few meaningful lines of the first non-empty text (no traceback caret lines)."""
    for t in texts:
        lines = [l.rstrip() for l in (t or "").splitlines()
                 if l.strip() and not re.fullmatch(r"\s*[\^~]+\s*", l)]
        if lines:
            out = "\n".join(lines[-n:])
            return out if len(out) <= limit else "…" + out[-limit:]
    return ""


def _attempt(klu_dir: str, script: str, target: str, junit: str):
    """Run the checker once; return the parsed JUnit root or raise _Failed."""
    if os.path.exists(junit):
        os.remove(junit)  # the checker appends to an existing report
    try:
        proc = subprocess.run(
            [sys.executable, script, "--nocolor", "-vv", "--junit", junit, target],
            cwd=os.path.join(klu_dir, "klc-check"), capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        out = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        raise _Failed(f"KLC checker timed out after {TIMEOUT_S} s", False, _tail(out))
    except OSError as e:
        raise _Failed(f"KLC checker failed to start: {e}", False)
    rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    tail = _tail(err, out)
    m = re.search(r"Could not parse [^\n]*", out)
    if m:
        why = re.search(r"\(([^()]*)\)\s*$", m.group(0))
        raise _Failed(f"KLC checker could not parse the item: {(why.group(1) if why else m.group(0))[:200]}",
                      False, tail)
    if rc < 0 or rc in (137, 143) or rc > 3:
        raise _Failed(f"KLC checker was killed or exited abnormally (exit {rc})", True, tail)
    if rc == 1:
        crashed = "Traceback (most recent call last)" in err
        raise _Failed("KLC checker crashed" + (f": {tail.splitlines()[-1][:200]}" if crashed and tail else
                                               f" (exit {rc})"), not crashed and not tail, tail)
    if not os.path.isfile(junit):
        raise _Failed(f"KLC checker wrote no report (exit {rc})", True, tail)
    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError as e:
        raise _Failed(f"KLC report unreadable: {e}", True, tail)
    if not any(True for _ in root.iter("testcase")):
        raise _Failed(f"KLC checker reported no result for the item (exit {rc}, empty report)", True, tail)
    types = [(f.get("type") or "").upper() for f in root.iter("failure")]
    n_err, n_warn = types.count("FAILURE"), types.count("WARNING")
    expected = 3 if n_err else 2 if n_warn else 0
    if rc != expected:
        raise _Failed(f"KLC checker exit {rc} disagrees with its report ({n_err} error(s), {n_warn} warning(s))",
                      True, tail)
    return root


def run(klu_dir: str, kind: str, library: str, name: str, source_text: str) -> KlcResult:
    """Check one item. Never reports a pass unless the checker demonstrably checked it."""
    script = "check_footprint.py" if kind == "footprint" else "check_symbol.py"
    with tempfile.TemporaryDirectory(prefix="cr-klc-") as tmp:
        safe_lib = re.sub(r"[^A-Za-z0-9._-]", "_", library) or "lib"
        safe_name = re.sub(r"[^A-Za-z0-9._+-]", "_", name) or "item"
        if kind == "footprint":
            d = os.path.join(tmp, f"{safe_lib}.pretty")
            os.makedirs(d)
            target = os.path.join(d, f"{safe_name}.kicad_mod")
        else:
            target = os.path.join(tmp, f"{safe_lib}.kicad_sym")
        with open(target, "w", encoding="utf-8") as f:
            f.write(source_text)
        junit = os.path.join(tmp, "out.xml")
        first = None
        for attempt in range(1, RETRIES + 2):
            try:
                root = _attempt(klu_dir, script, target, junit)
            except _Failed as e:
                first = first or e.reason
                if e.retry and attempt <= RETRIES:
                    continue
                reason = e.reason if attempt == 1 else f"{e.reason} (after {attempt} attempts)"
                tail = e.tail.replace(tmp, "<tmp>").replace(os.path.abspath(klu_dir), "<kicad-library-utils>")
                return KlcResult(error=reason, stderr_tail=tail, attempts=attempt)
            return KlcResult(findings=parse_junit(root), attempts=attempt,
                             retried_because=first if attempt > 1 else None)
    raise AssertionError("unreachable")


def parse_junit(root) -> list[dict]:
    out = []
    for fail in root.iter("failure"):
        text = (fail.text or fail.get("message") or "").strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        rule = lines[0].split(":", 1)[0]
        url = next((l for l in lines if l.startswith("https://klc.kicad.org")), None)
        details = [l for l in lines[1:] if not l.startswith("https://")]
        details = [d for d in details if not any(p.search(d) for p in _IGNORE)]
        # drop "- 3D model path: ..." style sub-lines that belonged to an ignored detail
        if not [d for d in details if not d.startswith("-")]:
            continue
        # KLC is the upstream convention; a violation is not necessarily wrong for this custom
        # library, so KLC errors map to warnings and KLC warnings to info.
        soft = all(_SOFT.search(d) for d in details if not d.startswith("-"))
        sev = "info" if soft or (fail.get("type") or "").upper() == "WARNING" else "warning"
        msg = f"KLC {rule}: " + "; ".join(details)
        if url:
            msg += f" ([{rule}]({url}))"
        out.append({"severity": sev, "category": "klc", "message": msg, "line": None})
    return out
