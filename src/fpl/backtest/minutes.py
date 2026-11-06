"""Rolling-origin backtest of the minutes model vs simple baselines.

Scored only on relevant rows (players who had appeared before), since never-used squad
players are trivially predicted by every method and would flatter the numbers.

Baselines for P(start): ``prev`` (started last match, clipped to 0.1/0.9) and ``rate3``
(share of the last three matches started, shrunk toward 0.5 by one pseudo-match).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl.models.minutes import fit, relevant


def _ll(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def predictions(features: pd.DataFrame, test_rounds: list[int]) -> pd.DataFrame:
    out = []
    for g in test_rounds:
        train = features[features["round"] < g]
        test = features[(features["round"] == g) & relevant(features)]
        if train.empty or test.empty:
            continue
        m = fit(train)
        pr = m.predict(test)
        n3 = np.minimum(test["prior_matches"].clip(lower=1), 3)
        out.append(pd.DataFrame({
            "round": g, "element": test["element"].to_numpy(),
            "position": test["position"].to_numpy(),
            "started": (test["starts"] > 0).astype(int).to_numpy(),
            "played60": (test["minutes"] >= 60).astype(int).to_numpy(),
            "minutes": test["minutes"].to_numpy(),
            "model_start": pr["p_start"].to_numpy(),
            "model_60": pr["p_60"].to_numpy(),
            "model_min": pr["e_minutes"].to_numpy(),
            "prev_start": np.clip(test["started_prev"].fillna(0).to_numpy(), 0.1, 0.9),
            "rate3_start": ((test["starts_last3"].fillna(0) + 0.5) / (n3 + 1)).to_numpy(),
            "naive_min": test["minutes_last3"].fillna(0).to_numpy(),
        }))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def score(pred: pd.DataFrame) -> pd.DataFrame:
    y = pred["started"].to_numpy()
    rows = []
    for name, col in (("model", "model_start"), ("prev", "prev_start"),
                      ("rate3", "rate3_start")):
        p = pred[col].to_numpy()
        rows.append({"predictor": name, "start_brier": float(np.mean((p - y) ** 2)),
                     "start_logloss": _ll(p, y), "n": len(pred)})
    s = pd.DataFrame(rows)
    s.loc[s.predictor == "model", "p60_brier"] = float(
        np.mean((pred["model_60"] - pred["played60"]) ** 2))
    s.loc[s.predictor == "model", "minutes_mae"] = float(
        np.mean(np.abs(pred["model_min"] - pred["minutes"])))
    s.loc[s.predictor == "rate3", "minutes_mae"] = float(
        np.mean(np.abs(pred["naive_min"] - pred["minutes"])))
    return s


def reliability(pred: pd.DataFrame, col: str = "model_start", target: str = "started",
                bins: int = 10) -> pd.DataFrame:
    b = pd.cut(pred[col], np.linspace(0, 1, bins + 1), include_lowest=True)
    r = pred.groupby(b, observed=True).agg(predicted=(col, "mean"), actual=(target, "mean"),
                                           n=(target, "size")).reset_index(drop=True)
    return r
