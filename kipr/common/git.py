"""Read-only git access for reviewers: blobs at a revision, changed paths, merge bases.

Everything goes through ``git -C <repo>`` subprocesses; the work tree is never touched, so a
reviewer can compare any two commits of a repository. :meth:`Git.extract` writes a subset of a
revision into a separate directory (``git archive``) for tools that need real files (kicad-cli).
"""

from __future__ import annotations

import os
import re
import subprocess

_GITHUB_URL_RE = re.compile(r"github\.com[:/]+([A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, repo: str):
        self.repo = repo
        self._cache: dict[tuple[str, str], bytes | None] = {}

    def run(self, *args, check=True) -> str:
        r = subprocess.run(["git", "-C", self.repo, *args], capture_output=True, text=True)
        if check and r.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout

    def rev(self, ref: str) -> str:
        """Full sha of a commit-ish."""
        return self.run("rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    def merge_base(self, a: str, b: str) -> str | None:
        """The merge base of two commits, or None if they share no history."""
        return self.run("merge-base", a, b, check=False).strip() or None

    def show(self, sha: str, path: str) -> bytes | None:
        """Blob ``path`` at ``sha`` (cached), or None if it does not exist there."""
        key = (sha, path)
        if key not in self._cache:
            r = subprocess.run(["git", "-C", self.repo, "show", f"{sha}:{path}"], capture_output=True)
            self._cache[key] = r.stdout if r.returncode == 0 else None
        return self._cache[key]

    def text(self, sha: str, path: str) -> str | None:
        b = self.show(sha, path)
        return b.decode("utf-8", errors="replace") if b is not None else None

    def exists(self, sha: str, path: str) -> bool:
        r = subprocess.run(["git", "-C", self.repo, "cat-file", "-e", f"{sha}:{path}"], capture_output=True)
        return r.returncode == 0

    def ls(self, sha: str, prefix: str) -> list[str]:
        """All file paths under ``prefix`` at ``sha``."""
        out = self.run("ls-tree", "-r", "--name-only", sha, "--", prefix, check=False)
        return [l for l in out.splitlines() if l]

    def changed_files(self, base: str, head: str, paths=()) -> list[tuple[str, str, str | None]]:
        """``[(status letter, path, old path or None)]`` between two commits, renames/copies detected."""
        out = self.run("diff", "--name-status", "-M", "-z", base, head, "--", *paths)
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

    def ls_tree(self, sha: str, paths=None) -> dict[str, str]:
        """``{path: blob sha}`` of all files under ``paths`` at ``sha`` (the whole tree when None)."""
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

    def extract(self, sha: str, paths, dest: str) -> None:
        """Write the repo ``paths`` (files or dirs) at ``sha`` into ``dest``, like a partial checkout."""
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

    def changed(self, base: str, head: str, paths=()) -> list[tuple[str, str]]:
        """``[(status letter, path)]`` from ``git diff --name-status --no-renames base head -- paths``."""
        out = self.run("diff", "--name-status", "--no-renames", base, head, "--", *paths)
        res = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                res.append((parts[0][0], parts[-1]))
        return res

    def github_repo(self, remote: str = "origin") -> str | None:
        """``owner/repo`` if ``remote`` points at github.com, else None."""
        url = self.run("remote", "get-url", remote, check=False).strip()
        m = _GITHUB_URL_RE.search(url)
        return m.group(1) if m else None

    def github_url(self, remote: str = "origin") -> str | None:
        """``https://github.com/owner/repo`` if ``remote`` points at github.com, else None."""
        r = self.github_repo(remote)
        return f"https://github.com/{r}" if r else None
