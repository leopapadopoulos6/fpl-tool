"""Rolling-origin backtest of the team-strength model against naive baselines.

For each test round g, fit on rounds < g only, predict every team-match in round g, and score:
- ``xg_mae``: |predicted lambda_for - actual xG for| (does it track underlying attack?)
- ``goal_nll``: Poisson negative log-likelihood of actual goals (does it predict scorelines?)
- ``cs_brier``: Brier score of P(clean sheet) = exp(-lambda_against) vs actual clean sheets

Baselines:
- ``league``: every team gets the training-set league-average xG
- ``naive``: mean of the team's own xG-for average and the opponent's xG-against average
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd
from scipy.special import gammaln

from fpl.models.team_strength import TeamStrengthParams, fit


def _poisson_nll(lam: np.ndarray, k: np.ndarray) -> np.ndarray:
    lam = np.clip(lam, 1e-6, None)
    return lam - k * np.log(lam) + gammaln(k + 1)


def predictions(team_matches: pd.DataFrame, params: TeamStrengthParams,
                test_rounds: list[int],
                prior: dict[int, tuple[float, float]] | None = None) -> pd.DataFrame:
    out = []
    for g in test_rounds:
        train = team_matches[team_matches["round"] < g]
        test = team_matches[team_matches["round"] == g]
        if train.empty or test.empty:
            continue
        as_of = pd.to_datetime(test["kickoff_time"], utc=True).min()
        m = fit(train, params, as_of=as_of, prior=prior)
        lg = train["xg_for"].mean()
        tf = train.groupby("team")["xg_for"].mean()
        ta = train.groupby("team")["xg_against"].mean()
        for r in test.itertuples():
            fx = m.fixture(r.team, r.opponent, bool(r.home))
            naive_for = np.nanmean([tf.get(r.team, lg), ta.get(r.opponent, lg)])
            naive_against = np.nanmean([tf.get(r.opponent, lg), ta.get(r.team, lg)])
            out.append({
                "round": g, "fixture": r.fixture, "team": r.team, "opponent": r.opponent,
                "home": r.home, "xg_for": r.xg_for, "goals_for": r.goals_for,
                "cs": int(r.goals_against == 0),
                "model_for": fx["lambda_for"], "model_against": fx["lambda_against"],
                "league_for": lg, "league_against": lg,
                "naive_for": naive_for, "naive_against": naive_against,
            })
    return pd.DataFrame(out)


def score(pred: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name in ("model", "naive", "league"):
        lf, la = pred[f"{name}_for"].to_numpy(), pred[f"{name}_against"].to_numpy()
        rows.append({
            "predictor": name,
            "xg_mae": float(np.mean(np.abs(lf - pred["xg_for"]))),
            "goal_nll": float(np.mean(_poisson_nll(lf, pred["goals_for"].to_numpy()))),
            "cs_brier": float(np.mean((np.exp(-la) - pred["cs"]) ** 2)),
            "n": len(pred),
        })
    return pd.DataFrame(rows)


def grid_search(team_matches: pd.DataFrame, test_rounds: list[int],
                grid: list[TeamStrengthParams],
                prior: dict[int, tuple[float, float]] | None = None) -> pd.DataFrame:
    rows = []
    for p in grid:
        s = score(predictions(team_matches, p, test_rounds, prior)).set_index("predictor")
        rows.append({**asdict(p), **{k: s.loc["model", k] for k in ("xg_mae", "goal_nll",
                                                                     "cs_brier")}})
    return pd.DataFrame(rows).sort_values("goal_nll").reset_index(drop=True)


DEFAULT_GRID = [
    TeamStrengthParams(ridge=r, half_life_days=h, goal_weight=gw)
    for r in (1.0, 3.0, 10.0, 30.0)
    for h in (None, 60.0)
    for gw in (0.0, 0.3)
]
