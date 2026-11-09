"""Per-match player and team tables: the inputs to every model.

- ``player_matches``: one row per player per fixture (element-summary history): minutes,
  starts, xG, xA, xGC, CBI, tackles, recoveries, DefCon, BPS, bonus, points, price that GW.
  The player's own team in that fixture is derived from the fixture and ``was_home``, so
  mid-season moves are handled.
- ``team_matches``: one row per team per finished fixture: xG for/against (summed player xG),
  goals for/against, home flag. Inputs to team strength.

Written as parquet to ``data/processed/``. Raw responses are snapshotted by the client.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from fpl.api import endpoints as ep
from fpl.api.client import FPLClient
from fpl.api.models import Bootstrap, ElementHistory, Fixture

PLAYER_COLS = [
    "element", "fixture", "round", "team", "opponent_team", "was_home", "kickoff_time",
    "minutes", "starts", "total_points", "goals_scored", "assists", "clean_sheets",
    "goals_conceded", "bonus", "bps", "expected_goals", "expected_assists",
    "expected_goals_conceded", "clearances_blocks_interceptions", "tackles", "recoveries",
    "defensive_contribution", "value", "saves", "yellow_cards", "red_cards", "own_goals",
    "penalties_saved", "penalties_missed",
]


def player_matches_frame(
    histories: Iterable[ElementHistory], fixtures: list[Fixture], bootstrap: Bootstrap
) -> pd.DataFrame:
    fx = {f.id: f for f in fixtures}
    pos = {t.id: t.singular_name_short for t in bootstrap.element_types}
    etype = {e.id: e.element_type for e in bootstrap.elements}
    rows = []
    for h in histories:
        f = fx.get(h.fixture)
        # FPL lists a GW's fixtures in every player's history from the deadline on, with
        # 0 minutes until they are played: keep only matches that are over
        if f is None or not (f.finished or getattr(f, "finished_provisional", False)):
            continue
        d = h.model_dump()
        d["team"] = (f.team_h if h.was_home else f.team_a) if f else None
        d["kickoff_time"] = d.get("kickoff_time")
        rows.append({c: d.get(c) for c in PLAYER_COLS})
    df = pd.DataFrame(rows, columns=PLAYER_COLS)
    df["position"] = df["element"].map(etype).map(pos)
    df["cbit"] = df["clearances_blocks_interceptions"] + df["tackles"]
    df["cbirt"] = df["cbit"] + df["recoveries"]
    return df.sort_values(["round", "fixture", "element"]).reset_index(drop=True)


def team_matches_frame(player_matches: pd.DataFrame, fixtures: list[Fixture]) -> pd.DataFrame:
    """Per team per finished fixture. xG is summed from players (includes penalties)."""
    xg = (player_matches.groupby(["fixture", "team"], as_index=False)["expected_goals"].sum())
    xg_map = {(r.fixture, r.team): r.expected_goals for r in xg.itertuples()}
    rows = []
    for f in fixtures:
        if f.event is None or not (f.finished or getattr(f, "finished_provisional", False)):
            continue
        for team, opp, home, gf, ga in (
            (f.team_h, f.team_a, True, f.team_h_score, f.team_a_score),
            (f.team_a, f.team_h, False, f.team_a_score, f.team_h_score),
        ):
            rows.append({
                "fixture": f.id, "round": f.event, "kickoff_time": f.kickoff_time,
                "team": team, "opponent": opp, "home": home,
                "goals_for": gf, "goals_against": ga,
                "xg_for": round(xg_map.get((f.id, team), 0.0), 3),
                "xg_against": round(xg_map.get((f.id, opp), 0.0), 3),
            })
    cols = ["fixture", "round", "kickoff_time", "team", "opponent", "home", "goals_for",
            "goals_against", "xg_for", "xg_against"]
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values(["round", "fixture", "home"]).reset_index(drop=True)


def fetch_histories(c: FPLClient, bootstrap: Bootstrap) -> list[ElementHistory]:
    out: list[ElementHistory] = []
    for el in bootstrap.elements:
        out.extend(ep.element_summary(c, el.id).history)
    return out


def build_tables(c: FPLClient, out_dir: Path) -> dict[str, pd.DataFrame]:
    b = ep.bootstrap_static(c)
    fixtures = ep.fixtures(c)
    pm = player_matches_frame(fetch_histories(c, b), fixtures, b)
    tm = team_matches_frame(pm, fixtures)
    out_dir.mkdir(parents=True, exist_ok=True)
    pm.to_parquet(out_dir / "player_matches.parquet", index=False)
    tm.to_parquet(out_dir / "team_matches.parquet", index=False)
    return {"player_matches": pm, "team_matches": tm}
