"""Full-season backtest on a past season, rebuilt in the engine's own table format.

The current season has only a handful of gameweeks; a finished season (e.g. 2025-26) gives
~35 test rounds. Tables come from the vaastav mirror (``merged_gw.csv``, ``fixtures.csv``,
``teams.csv``, ``players_raw.csv``), and the team-strength prior from the season before.
The same ``backtest.xpts.predictions`` runs unchanged, with and without the history prior.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from fpl.api.models import Fixture
from fpl.data.priors import prior_season_team_matches
from fpl.data.tables import team_matches_frame
from fpl.models.team_strength import TeamStrengthParams, fit

POS = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}


def season_tables(vaastav_root: Path, season: str):
    d = vaastav_root / "data" / season
    gw = pd.read_csv(d / "gws" / "merged_gw.csv", low_memory=False)
    teams = pd.read_csv(d / "teams.csv")
    tid = dict(zip(teams["name"], teams["id"], strict=True))
    pr = pd.read_csv(d / "players_raw.csv")
    pm = pd.DataFrame({
        "element": gw["element"].astype(int), "fixture": gw["fixture"].astype(int),
        "round": gw["GW"].astype(int), "team": gw["team"].map(tid),
        "opponent_team": gw["opponent_team"].astype(int),
        "was_home": gw["was_home"].astype(str).str.lower().eq("true"),
        "kickoff_time": gw["kickoff_time"],
    })
    for c in ("minutes", "starts", "total_points", "goals_scored", "assists", "clean_sheets",
              "goals_conceded", "bonus", "bps", "expected_goals", "expected_assists",
              "expected_goals_conceded", "clearances_blocks_interceptions", "tackles",
              "recoveries", "defensive_contribution", "value", "saves", "yellow_cards",
              "red_cards", "own_goals", "penalties_saved", "penalties_missed"):
        pm[c] = pd.to_numeric(gw[c], errors="coerce").fillna(0)
    pm["position"] = gw["position"].map(POS)
    pm["cbit"] = pm["clearances_blocks_interceptions"] + pm["tackles"]
    pm["cbirt"] = pm["cbit"] + pm["recoveries"]
    pm = pm[pm["position"].notna()].sort_values(["round", "fixture", "element"])
    fx_raw = pd.read_csv(d / "fixtures.csv")
    fixtures = [Fixture(id=int(r.id), event=int(r.event), kickoff_time=r.kickoff_time,
                        team_h=int(r.team_h), team_a=int(r.team_a),
                        team_h_score=int(r.team_h_score), team_a_score=int(r.team_a_score),
                        finished=True)
                for r in fx_raw.dropna(subset=["event", "team_h_score"]).itertuples()]
    tm = team_matches_frame(pm, fixtures)
    element_to_code = dict(zip(pr["id"], pr["code"], strict=True))
    return pm.reset_index(drop=True), tm, element_to_code, teams


def team_prior_for(teams: pd.DataFrame, data_dir: Path, prior_season: str) -> dict:
    """Team prior from the season before, keyed by this season's team ids via codes."""
    last = fit(prior_season_team_matches(data_dir, prior_season), TeamStrengthParams(ridge=5.0))
    weak = sorted(last.teams, key=lambda t: last.att[t] + last.dfn[t])[:3]
    promoted = (sum(last.att[t] for t in weak) / 3, sum(last.dfn[t] for t in weak) / 3)
    boot = SimpleNamespace(teams=[SimpleNamespace(id=int(r.id), code=int(r.code))
                                  for r in teams.itertuples()])
    return {t.id: (last.att[t.code], last.dfn[t.code]) if t.code in last.att else promoted
            for t in boot.teams}
