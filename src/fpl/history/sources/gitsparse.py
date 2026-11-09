"""Shallow, blob-filtered, sparse git checkouts: fetch only the files we use.

The GitHub REST API and web pages are not reachable from cloud sessions, but the git
protocol is, so public repos are read with git. Re-running updates in place (depth 1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:500]}")
    return r.stdout


def sparse_checkout(url: str, dest: Path, patterns: list[str], branch: str | None = None) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        args = ["clone", "--depth", "1", "--filter=blob:none", "--no-checkout"]
        if branch:
            args += ["--branch", branch]
        _git(*args, url, str(dest))
    else:
        _git("fetch", "--depth", "1", "origin", cwd=dest)
        _git("reset", "--hard", "FETCH_HEAD", cwd=dest)
    _git("sparse-checkout", "init", "--no-cone", cwd=dest)
    (dest / ".git" / "info" / "sparse-checkout").write_text("\n".join(patterns) + "\n")
    _git("read-tree", "-mu", "HEAD", cwd=dest)
    commit = _git("log", "-1", "--format=%H %cs", cwd=dest).split()
    files = int(_git("ls-files", cwd=dest).count("\n"))
    return {"source": url, "commit": commit[0], "commit_date": commit[1], "files": files}
