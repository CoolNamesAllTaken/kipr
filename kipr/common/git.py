"""Read-only git access for reviewers: blobs at a revision, changed paths, merge bases.

Everything goes through ``git -C <repo>`` subprocesses; nothing is checked out, so a reviewer
can compare any two commits of a repository without touching its work tree.
"""

from __future__ import annotations

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
