"""Team attack/defence ratings from xG (never official FDR or ``teams[].strength*``).

Model, per team-match row (the team's view of one fixture):

    log lambda_for = mu + home_adv * is_home + att[team] - def[opponent]

fitted by weighted Poisson likelihood on a target of
``(1 - goal_weight) * xG + goal_weight * goals`` (continuous targets are fine for the
Poisson score equations). Ratings are ridge-penalised toward the league average (0), which
is the shrinkage this small sample needs, and older matches are down-weighted with an
exponential half-life.

Outputs per fixture: lambda_for / lambda_against (expected goals) and P(clean sheet)
= exp(-lambda_against). With ``prior`` (see ``fpl.data.priors``) ratings shrink toward last
season's ratings instead of the league average; promoted teams get the mean of last season's
three weakest teams.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize


@dataclass(frozen=True)
class TeamStrengthParams:
    ridge: float = 5.0
    half_life_days: float | None = None  # None = no decay
    goal_weight: float = 0.0


@dataclass
class TeamStrength:
    params: TeamStrengthParams
    teams: list[int]
    mu: float
    home_adv: float
    att: dict[int, float]
    dfn: dict[int, float]

    def expected_goals(self, team: int, opponent: int, home: bool) -> float:
        return float(np.exp(self.mu + self.home_adv * home
                            + self.att.get(team, 0.0) - self.dfn.get(opponent, 0.0)))

    def fixture(self, team: int, opponent: int, home: bool) -> dict[str, float]:
        lf = self.expected_goals(team, opponent, home)
        la = self.expected_goals(opponent, team, not home)
        return {"lambda_for": lf, "lambda_against": la, "p_cs": float(np.exp(-la))}

    def ratings(self) -> pd.DataFrame:
        """Attack/defence as multipliers on league-average xG (1.0 = average)."""
        return pd.DataFrame({
            "team": self.teams,
            "attack": [float(np.exp(self.att[t])) for t in self.teams],
            "defence": [float(np.exp(self.dfn[t])) for t in self.teams],
        })


def fit(team_matches: pd.DataFrame, params: TeamStrengthParams | None = None,
        as_of: pd.Timestamp | None = None,
        prior: dict[int, tuple[float, float]] | None = None) -> TeamStrength:
    """``prior``: team -> (att, def) log-ratings to shrink toward (default 0 = league avg)."""
    p = params or TeamStrengthParams()
    tm = team_matches.dropna(subset=["xg_for"]).copy()
    teams = sorted(set(tm["team"]) | set(tm["opponent"]))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    pa = np.array([(prior or {}).get(t, (0.0, 0.0))[0] for t in teams])
    pd_ = np.array([(prior or {}).get(t, (0.0, 0.0))[1] for t in teams])
    ti = tm["team"].map(idx).to_numpy()
    oi = tm["opponent"].map(idx).to_numpy()
    home = tm["home"].astype(float).to_numpy()
    y = ((1 - p.goal_weight) * tm["xg_for"] + p.goal_weight * tm["goals_for"].fillna(0)).to_numpy()
    w = np.ones(len(tm))
    if p.half_life_days:
        ko = pd.to_datetime(tm["kickoff_time"], utc=True)
        ref = as_of if as_of is not None else ko.max()
        age = (ref - ko).dt.total_seconds().to_numpy() / 86400
        w = 0.5 ** (np.clip(age, 0, None) / p.half_life_days)

    def nll(theta: np.ndarray) -> tuple[float, np.ndarray]:
        mu, h, att, dfn = theta[0], theta[1], theta[2:2 + n], theta[2 + n:]
        eta = mu + h * home + att[ti] - dfn[oi]
        lam = np.exp(eta)
        da, dd = att - pa, dfn - pd_
        f = np.sum(w * (lam - y * eta)) + p.ridge * (da @ da + dd @ dd)
        r = w * (lam - y)
        g = np.zeros_like(theta)
        g[0] = r.sum()
        g[1] = (r * home).sum()
        g[2:2 + n] = np.bincount(ti, r, n) + 2 * p.ridge * da
        g[2 + n:] = -np.bincount(oi, r, n) + 2 * p.ridge * dd
        return f, g

    x0 = np.concatenate([[0.0, 0.0], pa, pd_])
    x0[0] = np.log(max(np.average(y, weights=w), 1e-3))
    res = minimize(nll, x0, jac=True, method="L-BFGS-B")
    th = res.x
    return TeamStrength(p, teams, float(th[0]), float(th[1]),
                        {t: float(th[2 + i]) for t, i in idx.items()},
                        {t: float(th[2 + n + i]) for t, i in idx.items()})
