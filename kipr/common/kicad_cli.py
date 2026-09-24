"""Locate and run KiCad's ``kicad-cli``.

Lookup order for :func:`find`: an explicit path, ``$KIPR_KICAD_CLI``, ``$KICAD_CLI``, then
``kicad-cli`` on ``PATH``. Every reviewer treats kicad-cli as optional and degrades without it.

:func:`run` is a single call; :class:`KicadCli` is for many calls against one executable: it
probes each command's ``--help`` once and drops options that the installed version doesn't know.
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


class KicadCli:
    """Runs kicad-cli commands; probes ``--help`` so options unknown to this version are dropped."""

    def __init__(self, exe: str, timeout: float = 900):
        self.exe = exe
        self.timeout = timeout
        self._help: dict[tuple, str] = {}
        self.version = self._version()

    def _version(self) -> str:
        try:
            r = subprocess.run([self.exe, "version"], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return "?"
        text = (r.stdout or r.stderr).strip()
        return text.splitlines()[-1] if text else "?"

    def help(self, *cmd: str) -> str:
        """``kicad-cli *cmd --help`` output (cached; empty if it cannot run)."""
        if cmd not in self._help:
            try:
                r = subprocess.run([self.exe, *cmd, "--help"], capture_output=True, text=True, timeout=120)
                self._help[cmd] = (r.stdout or "") + (r.stderr or "")
            except (OSError, subprocess.SubprocessError):
                self._help[cmd] = ""
        return self._help[cmd]

    def supports(self, cmd: tuple[str, ...], option: str) -> bool:
        return option in self.help(*cmd)

    def run(self, cmd: tuple[str, ...], options: list, target: str, cwd: str | None = None,
            env: dict | None = None) -> tuple[bool, str, int]:
        """Run ``kicad-cli *cmd [options] target``. ``options`` items are ``"--flag"`` or
        ``("--opt", value)``; ones the installed kicad-cli doesn't list in --help are skipped.
        ``env`` is added to the environment. Returns ``(ok, message, returncode)``."""
        args = [self.exe, *cmd]
        for o in options:
            name = o[0] if isinstance(o, tuple) else o
            if self.help(*cmd) and not self.supports(cmd, name):
                continue
            args.extend([o[0], str(o[1])] if isinstance(o, tuple) else [o])
        args.append(target)
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout, cwd=cwd,
                               env={**os.environ, **env} if env else None)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {self.timeout:.0f}s: {' '.join(cmd)}", -1
        except OSError as e:
            return False, str(e), -1
        msg = "\n".join(s for s in (r.stdout.strip(), r.stderr.strip()) if s)
        return r.returncode == 0, msg, r.returncode
