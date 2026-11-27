"""Backtest of team-level points ranges: one shared score per match vs independent players.

For each test round, many eleven-player teams are drawn from the round's predictions (no
outcome used: players with P(start) >= 0.5, weighted by xPts, highest xPts captained), and
each team's 10th-90th percentile range is simulated both ways from the same rows. A
calibrated range holds the actual total 80% of the time. The proper score (CRPS, lower is
better) rewards a distribution that is both calibrated and sharp.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl.models.xpts import simulate, simulate_joint
from fpl.rules import GameRules

TARGET = 0.8


def _crps(sims: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Sample CRPS per column: E|X - y| - E|X - X'| / 2 (X' an independent copy)."""
    a = np.abs(sims - actual).mean(axis=0)
    s = np.sort(sims, axis=0)
    m = len(s)
    k = np.arange(1, m + 1)[:, None]
    b = ((2 * k - m - 1) * s).sum(axis=0) / (m * m)  # E|X - X'| / 2
    return a - b


def coverage(pred: pd.DataFrame, rules: GameRules, teams_per_round: int = 300,
             n: int = 2000, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    res = {"joint": [], "independent": []}
    crps = {"joint": [], "independent": []}
    sd = {"joint": [], "independent": []}
    n_teams = 0
    for g, d in pred.groupby("round"):
        d = d.reset_index(drop=True)
        sims = {"joint": simulate_joint(d, rules, n=n, seed=seed + int(g)),
                "independent": simulate(d, rules, n=n, seed=seed + int(g))}
        el = d.groupby("element")
        xp = el["xpts"].sum()
        pool = xp[(el["p_start"].max() >= 0.5) & (xp > 0)]
        if len(pool) < 11:
            continue
        p = (pool / pool.sum()).to_numpy()
        teams = np.zeros((len(d), teams_per_round))
        for t in range(teams_per_round):
            xi = rng.choice(pool.index.to_numpy(), size=11, replace=False, p=p)
            cap = pool.loc[xi].idxmax()
            w = d["element"].map(lambda e, xi=xi, cap=cap: 2.0 if e == cap else
                                 (1.0 if e in xi else 0.0)).to_numpy()
            teams[:, t] = w
        actual = d["actual"].to_numpy() @ teams
        n_teams += teams_per_round
        for k, s in sims.items():
            tot = s @ teams
            lo, hi = np.percentile(tot, 10, axis=0), np.percentile(tot, 90, axis=0)
            res[k].append(((actual >= lo) & (actual <= hi)).astype(float))
            crps[k].append(_crps(tot, actual))
            sd[k].append(tot.std(axis=0))
    out = {"teams": n_teams, "rounds": f"GW{pred['round'].min()}–{pred['round'].max()}"}
    for k in res:
        out[f"coverage_{k}"] = float(np.concatenate(res[k]).mean()) if res[k] else float("nan")
        out[f"crps_{k}"] = float(np.concatenate(crps[k]).mean()) if crps[k] else float("nan")
        out[f"sd_{k}"] = float(np.concatenate(sd[k]).mean()) if sd[k] else float("nan")
    out["joint_better"] = bool(
        res["joint"] and out["crps_joint"] < out["crps_independent"]
        and abs(out["coverage_joint"] - TARGET) <= abs(out["coverage_independent"] - TARGET))
    return out
