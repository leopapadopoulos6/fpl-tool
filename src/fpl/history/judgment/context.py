"""Context adjustments estimated from history (not assumed).

- ``ramp_up_curve``: minutes after an injury-type absence, by game since return, relative
  to the player's pre-absence norm. An absence is >= 21 days and >= 2 team matches with no
  minutes, for a player who started >= 60% of his previous 6 matches (so dropped squad
  players are not counted as injured). Feeds the minutes model as a cap/feature.
- League change: ``baseline.league_factors`` (movers' per-90 ratios, shrunk).
- Team / role change: ``baseline.BaselineParams.team_change / role_change`` discounts on the
  career prior strength, tuned in the rate backtest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ramp_up_curve(pm: pd.DataFrame, games: int = 5) -> pd.DataFrame:
    pl = pm[(pm["competition"] == "Premier League") & pm["fpl_points"].notna()]
    pl = pl.sort_values(["code", "date"])
    rows = []
    for _, g in pl.groupby("code"):
        mins = g["minutes"].fillna(0).to_numpy()
        dates = g["date"].to_numpy()
        started = (mins >= 60).astype(float)  # 'starts' only exists from 2022-23
        i = 6
        while i < len(g) - 1:
            if mins[i] > 0:
                i += 1
                continue
            j = i
            while j < len(g) and mins[j] == 0:
                j += 1
            if j >= len(g):
                break
            gap_days = (dates[j] - dates[i - 1]) / np.timedelta64(1, "D")
            regular = started[i - 6:i].mean() >= 0.6
            if regular and j - i >= 2 and gap_days >= 21:
                norm = mins[i - 6:i][mins[i - 6:i] > 0].mean()
                for k in range(games):
                    if j + k < len(g):
                        rows.append({"game_back": k + 1, "ratio": mins[j + k] / norm,
                                     "started60": mins[j + k] >= 60, "absence_days": gap_days})
            i = j + 1
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.groupby("game_back").agg(n=("ratio", "size"),
                                       minutes_vs_norm=("ratio", "mean"),
                                       share_60_plus=("started60", "mean")).round(3).reset_index()
