"""Rolling-origin backtest of the full xPts model (team strength + minutes + components).

For each test round g everything is refitted on rounds < g only (team ratings with the
last-season prior, minutes model, shrunk rates, bonus model), then every relevant player's
fixture in g is predicted and compared with the actual FPL points.

Baselines: ``ppm`` (season-to-date points per match, all matches) and ``last3`` (mean
points over the last three matches). Raw points are used only as baselines and targets,
never as model inputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from fpl.models import minutes as mm
from fpl.models.team_strength import TeamStrengthParams, fit
from fpl.models.xpts import expected_points, fit_bonus, fit_rates, team_average_lambdas
from fpl.rules import DEFCON_THRESHOLD, GameRules

TEAM_PARAMS = TeamStrengthParams(ridge=100.0)


def predictions(features: pd.DataFrame, team_matches: pd.DataFrame, rules: GameRules,
                test_rounds: list[int], prior: dict | None = None,
                rate_prior_fn=None) -> pd.DataFrame:
    """``rate_prior_fn(as_of) -> DataFrame`` (by element: xg90, xa90, k90) supplies the
    history-based player prior as of each round (no lookahead)."""
    f = features.sort_values(["element", "kickoff", "fixture"]).copy()
    gp = f.groupby("element")["total_points"]
    f["ppm"] = gp.transform(lambda s: s.shift(1).expanding().mean())
    f["last3"] = gp.transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    out = []
    for g in test_rounds:
        tr = f[f["round"] < g]
        tm_tr = team_matches[team_matches["round"] < g]
        test = f[(f["round"] == g) & mm.relevant(f)]
        if tr.empty or test.empty:
            continue
        as_of = pd.to_datetime(test["kickoff_time"], utc=True).min()
        team = fit(tm_tr, TEAM_PARAMS, as_of=as_of, prior=prior)
        mins = mm.fit(tr).predict(test)
        rp = rate_prior_fn(as_of) if rate_prior_fn is not None else None
        xp = expected_points(test, mins, fit_rates(tr, rp), team,
                             team_average_lambdas(team, tm_tr), fit_bonus(tr), rules)
        xp["round"] = g
        xp["actual"] = test["total_points"].to_numpy()
        xp["ppm"] = test["ppm"].fillna(0).to_numpy()
        xp["last3"] = test["last3"].fillna(0).to_numpy()
        thr = test["position"].map(DEFCON_THRESHOLD).fillna(10_000).to_numpy()
        dc = test["cbit"].where(test["position"] == "DEF", test["cbirt"]).to_numpy()
        xp["dc_hit"] = (dc >= thr).astype(int)
        xp["started"] = (test["starts"] > 0).astype(int).to_numpy()
        out.append(xp)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def score(pred: pd.DataFrame, top_k: int = 20) -> pd.DataFrame:
    rows = []
    for name, col in (("model", "xpts"), ("ppm", "ppm"), ("last3", "last3")):
        err = pred[col] - pred["actual"]
        topk = (pred.sort_values(col, ascending=False).groupby("round").head(top_k)
                .groupby("round")["actual"].mean().mean())
        rows.append({"predictor": name, "mae": float(err.abs().mean()),
                     "rmse": float(np.sqrt((err ** 2).mean())),
                     "spearman": float(spearmanr(pred[col], pred["actual"]).statistic),
                     f"top{top_k}_actual": float(topk), "bias": float(err.mean()),
                     "n": len(pred)})
    return pd.DataFrame(rows)


def by_position(pred: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pos, d in pred.groupby("position"):
        for name, col in (("model", "xpts"), ("ppm", "ppm"), ("last3", "last3")):
            rows.append({"position": pos, "predictor": name,
                         "mae": float((d[col] - d["actual"]).abs().mean()),
                         "spearman": float(spearmanr(d[col], d["actual"]).statistic),
                         "n": len(d)})
    return pd.DataFrame(rows)


def calibration(pred: pd.DataFrame, col: str = "xpts", bins: int = 8) -> pd.DataFrame:
    q = pd.qcut(pred[col], bins, duplicates="drop")
    return pred.groupby(q, observed=True).agg(predicted=(col, "mean"), actual=("actual", "mean"),
                                              n=("actual", "size")).reset_index(drop=True)


def defcon_check(pred: pd.DataFrame) -> dict:
    d = pred[pred["position"] != "GKP"]
    return {"brier": float(((d["p_defcon"] - d["dc_hit"]) ** 2).mean()),
            "predicted_rate": float(d["p_defcon"].mean()), "actual_rate": float(d["dc_hit"].mean()),
            "base_brier": float(((d["dc_hit"].mean() - d["dc_hit"]) ** 2).mean())}
