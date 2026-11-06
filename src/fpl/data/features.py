"""As-of features per player per fixture (M1). Every feature uses only matches *before* the
row's fixture (``shift(1)`` inside each player's history), so rows are safe for backtests.

Not available historically: injury/status flags and news (the API only serves current
values). Those enter the minutes model only from our own snapshots (``data/raw``) onwards.
"""

from __future__ import annotations

import pandas as pd

ROLL = (1, 3, 5)


def player_form_features(player_matches: pd.DataFrame) -> pd.DataFrame:
    """Add prior-only rolling minutes/start features and cumulative per-90 rates."""
    df = player_matches.copy()
    df["kickoff"] = pd.to_datetime(df["kickoff_time"], utc=True)
    df = df.sort_values(["element", "kickoff", "fixture"]).reset_index(drop=True)
    g = df.groupby("element", sort=False)

    started = (df["starts"] > 0).astype(float)
    played = (df["minutes"] > 0).astype(float)
    full = (df["minutes"] >= 60).astype(float)
    for n in ROLL:
        df[f"starts_last{n}"] = g["starts"].transform(
            lambda s, n=n: (s > 0).astype(float).shift(1).rolling(n, min_periods=1).sum())
        df[f"minutes_last{n}"] = g["minutes"].transform(
            lambda s, n=n: s.shift(1).rolling(n, min_periods=1).mean())
    df["prior_matches"] = g.cumcount()
    df["prior_start_rate"] = _cum_rate(df, started)
    df["prior_appear_rate"] = _cum_rate(df, played)
    df["prior_60_rate"] = _cum_rate(df, full)
    df["prior_minutes"] = g["minutes"].transform(lambda s: s.shift(1).cumsum()).fillna(0)
    for col, out in (("expected_goals", "xg90"), ("expected_assists", "xa90"),
                     ("cbit", "cbit90"), ("cbirt", "cbirt90")):
        tot = g[col].transform(lambda s: s.shift(1).cumsum()).fillna(0)
        df[f"prior_{out}"] = (tot * 90 / df["prior_minutes"]).where(df["prior_minutes"] > 0)
    df["started_prev"] = g["starts"].transform(lambda s: (s > 0).astype(float).shift(1))
    return df


def _cum_rate(df: pd.DataFrame, flag: pd.Series) -> pd.Series:
    g = flag.groupby(df["element"], sort=False)
    num = g.transform(lambda s: s.shift(1).cumsum())
    den = df.groupby("element", sort=False).cumcount()
    return (num / den).where(den > 0)


def team_congestion(team_matches: pd.DataFrame, fixtures: pd.DataFrame | None = None) -> pd.DataFrame:
    """Rest days before each team match (league fixtures only; cup/European games are not in
    the FPL API, so congestion is understated in weeks with midweek cup ties)."""
    tm = team_matches.copy()
    if fixtures is not None:
        tm = pd.concat([tm, fixtures], ignore_index=True)
    tm["kickoff"] = pd.to_datetime(tm["kickoff_time"], utc=True)
    tm = tm.sort_values(["team", "kickoff"])
    tm["rest_days"] = tm.groupby("team")["kickoff"].diff().dt.total_seconds() / 86400
    return tm


def build_player_features(player_matches: pd.DataFrame,
                          team_matches: pd.DataFrame) -> pd.DataFrame:
    """Form features plus the team's rest days before each fixture."""
    f = player_form_features(player_matches)
    rest = team_congestion(team_matches)[["team", "fixture", "rest_days"]]
    return f.merge(rest, on=["team", "fixture"], how="left")
