"""vaastav/Fantasy-Premier-League: FPL per-GW history (2016-17 onwards) and the Understat
career files it mirrors (2019-20 to 2024-25 folders)."""

from __future__ import annotations

from pathlib import Path

from fpl.history.sources.gitsparse import sparse_checkout

URL = "https://github.com/vaastav/Fantasy-Premier-League"
PATTERNS = [
    "/data/*/gws/merged_gw.csv",
    "/data/*/players_raw.csv",
    "/data/*/fixtures.csv",
    "/data/*/teams.csv",
    "/data/*/id_dict.csv",
    "/data/*/player_idlist.csv",
    "/data/*/understat/*",
    "/data/master_team_list.csv",
]


def fetch(external: Path) -> dict:
    return sparse_checkout(URL, external / "vaastav", PATTERNS)


def seasons(external: Path) -> list[str]:
    root = external / "vaastav" / "data"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name[:2] == "20")
