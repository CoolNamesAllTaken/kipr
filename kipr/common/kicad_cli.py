"""Locate and run KiCad's ``kicad-cli``.

Lookup order for :func:`find`: an explicit path, ``$KIPR_KICAD_CLI``, ``$KICAD_CLI``, then
``kicad-cli`` on ``PATH``. Every reviewer treats kicad-cli as optional and degrades without it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    ok: bool
    returncode: int
    stdout: str
    stderr: str

    @property
    def message(self) -> str:
        """stderr, or stdout if stderr is empty, stripped (what to show in a warning)."""
        return (self.stderr or self.stdout).strip()


def find(explicit: str | None = None) -> str | None:
    """Path of a usable kicad-cli, or None."""
    for cand in (explicit, os.environ.get("KIPR_KICAD_CLI"), os.environ.get("KICAD_CLI")):
        if cand:
            p = shutil.which(cand)
            if p:
                return p
    return shutil.which("kicad-cli")


def run(kicad_cli: str, *args, timeout: float | None = None, cwd: str | None = None) -> Result:
    """Run ``kicad-cli args...``; never raises for a failing command (see ``Result.ok``)."""
    try:
        r = subprocess.run([kicad_cli, *args], capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Result(False, -1, "", str(e))
    return Result(r.returncode == 0, r.returncode, r.stdout, r.stderr)


def version(kicad_cli: str) -> str | None:
    """``kicad-cli version`` output (e.g. ``10.0.1``), or None if it cannot run."""
    r = run(kicad_cli, "version", timeout=60)
    return r.stdout.strip() or None if r.ok else None
