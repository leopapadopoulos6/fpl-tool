"""Our own club Elo, computed match by match (ClubElo is unreachable from cloud sessions).

World-Football-Elo style: expected score from the rating gap plus home advantage, K scaled
by goal difference, and a partial regression to the league mean at each new season.
Each league is its own pool (Understat holds league matches only, so leagues are not
connected); promoted PL clubs enter at the mean rating of the clubs relegated that summer.
Non-PL coverage is partial: only matches involving a tracked player's club are seen.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BASE = 1500.0


@dataclass(frozen=True)
class EloParams:
    k: float = 20.0
    home_adv: float = 60.0
    season_regress: float = 0.2  # share pulled back to the league mean between seasons


def _gd_mult(gd: int) -> float:
    gd = abs(gd)
    return 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8


def expected(r_home: float, r_away: float, home_adv: float) -> float:
    return 1.0 / (1.0 + 10 ** (-(r_home + home_adv - r_away) / 400))


def run(results: pd.DataFrame, params: EloParams | None = None) -> pd.DataFrame:
    """``results``: league, season, date, home, away, home_goals, away_goals.
    Returns one row per team per match with the pre-match ratings."""
    p = params or EloParams()
    rows = []
    for league, lg in results.sort_values("date").groupby("league", sort=False):
        rating: dict[str, float] = {}
        season = None
        relegated_pool: list[float] = []
        for r in lg.sort_values("date").itertuples():
            if r.season != season:
                if season is not None and rating:
                    mean = float(np.mean(list(rating.values())))
                    seen = set(lg.loc[lg["season"] == r.season, ["home", "away"]].stack())
                    relegated_pool = [v for t, v in rating.items() if t not in seen]
                    rating = {t: v + p.season_regress * (mean - v) for t, v in rating.items()}
                season = r.season
            for t in (r.home, r.away):
                if t not in rating:
                    rating[t] = float(np.mean(relegated_pool)) if relegated_pool else BASE - 50
            rh, ra = rating[r.home], rating[r.away]
            e = expected(rh, ra, p.home_adv)
            s = 1.0 if r.home_goals > r.away_goals else 0.5 if r.home_goals == r.away_goals else 0.0
            delta = p.k * _gd_mult(int(r.home_goals - r.away_goals)) * (s - e)
            rating[r.home], rating[r.away] = rh + delta, ra - delta
            for team, opp, own, other, home in ((r.home, r.away, rh, ra, True),
                                                (r.away, r.home, ra, rh, False)):
                rows.append({"league": league, "season": r.season, "date": r.date,
                             "team": team, "opponent": opp, "is_home": home,
                             "elo_pre": round(own, 1), "opponent_elo_pre": round(other, 1)})
    return pd.DataFrame(rows)


def season_summary(elo: pd.DataFrame) -> pd.DataFrame:
    g = elo.sort_values("date").groupby(["league", "season", "team"])["elo_pre"]
    return pd.DataFrame({"elo_start": g.first(), "elo_end": g.last(),
                         "elo_mean": g.mean().round(1), "matches": g.size()}).reset_index()
