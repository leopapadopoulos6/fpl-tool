"""Publish processed tables to the ``data`` branch as a single commit.

The data branch never accumulates history: each publish
  1. fetches the branch tip (depth 1) and starts from *that* tree, so files another writer
     added (e.g. the snapshot workflow) are kept;
  2. stages ``history/`` (and the branch's .gitignore) from the local data worktree;
  3. writes a commit with **no parent** from that tree;
  4. pushes with ``--force-with-lease`` pinned to the tip it started from, retrying from
     step 1 if another writer moved the branch in between.
Raw dumps (external/, processed/, ledger/) are ignored on the branch and never committed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

BRANCH = "data"
IGNORE = "external/\nprocessed/\nledger/\n__pycache__/\n"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:400]}")
    return r


def publish(data_dir: Path, message: str, paths: tuple[str, ...] = ("history",),
            retries: int = 3) -> str:
    """``data_dir`` is a worktree of the data branch. Returns the new commit sha."""
    gi = data_dir / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    if not all(line in existing for line in IGNORE.split()):
        gi.write_text(existing.rstrip("\n") + ("\n" if existing else "") + IGNORE)
    for _ in range(retries):
        fetched = _git(data_dir, "fetch", "--depth", "1", "origin", BRANCH, check=False)
        if fetched.returncode == 0:
            base = _git(data_dir, "rev-parse", "FETCH_HEAD").stdout.strip()
            _git(data_dir, "read-tree", "FETCH_HEAD")  # index = remote tree (keeps others' files)
        else:  # branch does not exist yet
            base = ""
            _git(data_dir, "read-tree", "--empty")
        _git(data_dir, "add", "-f", ".gitignore", *paths)
        tree = _git(data_dir, "write-tree").stdout.strip()
        new = _git(data_dir, "commit-tree", tree, "-m", message).stdout.strip()
        lease = f"--force-with-lease=refs/heads/{BRANCH}:{base}"
        pushed = _git(data_dir, "push", lease, "origin", f"{new}:refs/heads/{BRANCH}", check=False)
        if pushed.returncode == 0:
            _git(data_dir, "update-ref", f"refs/heads/{BRANCH}", new)
            _git(data_dir, "reset", "--quiet", new)  # index/HEAD to the published commit
            return new
    raise RuntimeError("could not publish: the data branch kept moving (lease failed)")
