import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_source_file_is_gitignored():
    """Guard against ignore rules swallowing code (an unanchored `data/` once hid
    src/fpl/data from every commit)."""
    files = [str(p.relative_to(ROOT)) for d in ("src", "tests")
             for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    r = subprocess.run(["git", "check-ignore", "--no-index", *files], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.stdout.strip() == "", f"ignored source files:\n{r.stdout}"
