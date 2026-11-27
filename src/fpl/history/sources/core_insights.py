"""olbauday/FPL-Core-Insights: current-season match stats (FotMob-style), team Elo, and
European / domestic cup matches ("By Tournament"). Uses FPL player ids/codes natively."""

from __future__ import annotations

from pathlib import Path

from fpl.history.sources.gitsparse import sparse_checkout

URL = "https://github.com/olbauday/FPL-Core-Insights"
PATTERNS = [
    "/data/*/players.csv",
    "/data/*/teams.csv",
    "/data/*/team_history.csv",
    "/data/*/By Gameweek/*/matches.csv",
    "/data/*/By Gameweek/*/playermatchstats.csv",
    "/data/*/By Tournament/**/matches.csv",
    "/data/*/By Tournament/**/playermatchstats.csv",
    "/data/*/By Tournament/**/players.csv",
    "/data/*/By Tournament/**/teams.csv",
]


def fetch(external: Path) -> dict:
    return sparse_checkout(URL, external / "core_insights", PATTERNS)


def seasons(external: Path) -> list[str]:
    root = external / "core_insights" / "data"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name[:2] == "20")
