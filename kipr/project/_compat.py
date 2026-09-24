"""Generic helpers the project reviewer needs that `kipr.common` doesn't have (yet).

- the s-expression parser is re-exported from `kipr.common.sexpr`;
- `Git` adds what `kipr.common.git` lacks: name-status diffs with renames, `ls-tree` with blob
  hashes (export cache keys) and `git archive` extraction of a subset of paths;
- `KicadCli` wraps kicad-cli with `--help` probing so options unknown to the installed version
  are dropped instead of failing the command.
Candidates for moving into `kipr.common` later.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from kipr.common.sexpr import Atom, Node, ParseError, dumps, parse, parse_all  # noqa: F401


def parse_file(path: str) -> Node:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse(fh.read())


# --- git -------------------------------------------------------------------------------


class GitError(RuntimeError):
    pass


class Git:
    """Thin wrapper around the git CLI for one repository."""

    def __init__(self, repo: str):
        self.repo = os.path.abspath(repo)

    def run(self, *args: str, input: bytes | None = None, text: bool = True) -> str | bytes:
        r = subprocess.run(["git", "-C", self.repo, *args], input=input, capture_output=True,
                           text=False)
        if r.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
        return r.stdout.decode("utf-8", errors="replace") if text else r.stdout

    def rev_parse(self, ref: str) -> str:
        return self.run("rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    def changed_files(self, base: str, head: str) -> list[tuple[str, str, str | None]]:
        """[(status, path, old_path)] between two commits, renames detected."""
        out = self.run("diff", "--name-status", "-M", "-z", base, head)
        parts = out.split("\0")
        res, i = [], 0
        while i < len(parts) and parts[i]:
            st = parts[i]
            if st[0] in "RC":
                res.append((st[0], parts[i + 2], parts[i + 1]))
                i += 3
            else:
                res.append((st[0], parts[i + 1], None))
                i += 2
        return res

    def ls_tree(self, sha: str, paths: list[str] | None = None) -> dict[str, str]:
        """{path: blob sha} of all files under `paths` (whole tree when None)."""
        out = self.run("ls-tree", "-r", "-z", "--full-tree", sha, "--", *(paths or []))
        res = {}
        for rec in out.split("\0"):
            if not rec:
                continue
            meta, path = rec.split("\t", 1)
            _mode, typ, obj = meta.split()
            if typ == "blob":
                res[path] = obj
        return res

    def show(self, sha: str, path: str) -> bytes | None:
        try:
            return self.run("show", f"{sha}:{path}", text=False)
        except GitError:
            return None

    def show_text(self, sha: str, path: str) -> str | None:
        b = self.show(sha, path)
        return None if b is None else b.decode("utf-8", errors="replace")

    def extract(self, sha: str, paths: list[str], dest: str) -> None:
        """Write the given repo paths (files or dirs) at `sha` into `dest` (like a checkout)."""
        os.makedirs(dest, exist_ok=True)
        if not paths:
            return
        archive = subprocess.run(["git", "-C", self.repo, "archive", "--format=tar", sha, "--", *paths],
                                 capture_output=True)
        if archive.returncode != 0:
            raise GitError(f"git archive: {archive.stderr.decode(errors='replace').strip()}")
        tar = subprocess.run(["tar", "-x", "-C", dest], input=archive.stdout, capture_output=True)
        if tar.returncode != 0:
            raise GitError(f"tar: {tar.stderr.decode(errors='replace').strip()}")

    def remote_url(self) -> str | None:
        try:
            url = self.run("remote", "get-url", "origin").strip()
        except GitError:
            return None
        return github_https(url)


def github_https(url: str) -> str | None:
    """git@github.com:o/r.git / https://…/o/r(.git) -> https://github.com/o/r (other hosts: None)."""
    u = url.strip()
    for pre in ("git@github.com:", "ssh://git@github.com/", "https://github.com/", "http://github.com/"):
        if u.startswith(pre):
            rest = u[len(pre):]
            if rest.endswith(".git"):
                rest = rest[:-4]
            return "https://github.com/" + rest.strip("/")
    return None


# --- kicad-cli -------------------------------------------------------------------------


def find_kicad_cli(explicit: str | None = None) -> str | None:
    for cand in (explicit, os.environ.get("KIPR_KICAD_CLI"), shutil.which("kicad-cli")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)
    return None


class KicadCli:
    """Runs kicad-cli commands; probes `--help` so options unknown to this version are dropped."""

    def __init__(self, exe: str, timeout: float = 900):
        self.exe = exe
        self.timeout = timeout
        self._help: dict[tuple, str] = {}
        self.version = self._version()

    def _version(self) -> str:
        try:
            r = subprocess.run([self.exe, "version"], capture_output=True, text=True, timeout=120)
            return (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "?"
        except (OSError, subprocess.SubprocessError):
            return "?"

    def help(self, *cmd: str) -> str:
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
            env: dict | None = None):
        """Run `kicad-cli *cmd [options] target`. `options` items are "--flag" or ("--opt", value);
        ones the installed kicad-cli doesn't list in --help are skipped. Returns (ok, message, rc)."""
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
