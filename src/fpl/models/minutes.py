"""Minutes / start-probability model. Drives bench order, Bench Boost value and xPts.

Outputs per player per fixture:
- ``p_start``: logistic regression on prior-only form features (recent starts and minutes,
  season rates, position, price, team rest days).
- ``p60_given_start``: player's shrunk rate of 60+ minutes when starting (position prior).
- ``p_sub``: player's shrunk rate of coming off the bench when not starting.
- ``p_appear``, ``p_60``, ``e_minutes`` derived from those.

Injury/suspension flags are applied only for live predictions (``apply_status``): the API
does not keep historical flags, so this adjustment is an assumption, not backtested.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

POSITIONS = ("GKP", "DEF", "MID", "FWD")
SHRINK = 3.0  # pseudo-observations for per-player rates
SUB_MINUTES = 20.0


def design(df: pd.DataFrame) -> pd.DataFrame:
    """Model features from ``player_form_features`` output (+ optional ``rest_days``)."""
    n = df["prior_matches"].clip(lower=1)
    X = pd.DataFrame({
        "started_prev": df["started_prev"].fillna(0),
        "start_rate_3": df["starts_last3"].fillna(0) / np.minimum(n, 3),
        "start_rate_5": df["starts_last5"].fillna(0) / np.minimum(n, 5),
        "min_last1": df["minutes_last1"].fillna(0) / 90,
        "min_last3": df["minutes_last3"].fillna(0) / 90,
        "start_rate": df["prior_start_rate"].fillna(0),
        "rate_60": df["prior_60_rate"].fillna(0),
        "appear_rate": df["prior_appear_rate"].fillna(0),
        "log_price": np.log(df["value"].clip(lower=35) / 10),
        "few_matches": (df["prior_matches"] < 3).astype(float),
        "rest_days": df.get("rest_days", pd.Series(7.0, index=df.index)).fillna(7).clip(2, 14),
    }, index=df.index)
    for p in POSITIONS[:-1]:
        X[f"pos_{p}"] = (df["position"] == p).astype(float)
    return X


def relevant(df: pd.DataFrame) -> pd.Series:
    """Rows worth modelling/scoring: the player has appeared at least once before."""
    return (df["prior_matches"] >= 1) & (df["prior_appear_rate"].fillna(0) > 0)


@dataclass
class MinutesModel:
    clf: object
    pos_p60: dict[str, float]
    pos_min_start: dict[str, float]
    base_sub: float
    player_stats: pd.DataFrame  # element -> starts, n60, nostart, subs, mean_min_start

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        X = design(df)
        p_start = self.clf.predict_proba(X)[:, 1]
        p_start = np.where(relevant(df), p_start, 0.0)
        ps = self.player_stats.reindex(df["element"]).reset_index(drop=True)
        pos = df["position"].reset_index(drop=True)
        prior60 = pos.map(self.pos_p60).fillna(0.8)
        p60 = ((ps["n60"].fillna(0) + SHRINK * prior60)
               / (ps["starts"].fillna(0) + SHRINK)).to_numpy()
        psub = ((ps["subs"].fillna(0) + SHRINK * self.base_sub)
                / (ps["nostart"].fillna(0) + SHRINK)).to_numpy()
        psub = np.where(relevant(df).to_numpy(), psub, 0.0)
        min_start = ps["mean_min_start"].fillna(pos.map(self.pos_min_start)).to_numpy()
        out = pd.DataFrame({
            "p_start": p_start, "p60_given_start": p60, "p_sub": psub,
        }, index=df.index)
        out["p_appear"] = out["p_start"] + (1 - out["p_start"]) * out["p_sub"]
        out["p_60"] = out["p_start"] * out["p60_given_start"]
        out["e_minutes"] = (out["p_start"] * min_start
                            + (1 - out["p_start"]) * out["p_sub"] * SUB_MINUTES)
        out["e_minutes_if_start"] = min_start
        return out


def fit(train: pd.DataFrame) -> MinutesModel:
    tr = train[relevant(train)]
    y = (tr["starts"] > 0).astype(int)
    clf = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    clf.fit(design(tr), y)
    st = train[train["starts"] > 0]
    pos_p60 = (st["minutes"] >= 60).groupby(st["position"]).mean().to_dict()
    pos_min = st.groupby("position")["minutes"].mean().to_dict()
    ns = train[(train["starts"] == 0)]
    base_sub = float((ns["minutes"] > 0)[relevant(ns)].mean()) if len(ns) else 0.2
    g = train.groupby("element")
    stats = pd.DataFrame({
        "starts": g["starts"].apply(lambda s: (s > 0).sum()),
        "n60": g.apply(lambda d: ((d["starts"] > 0) & (d["minutes"] >= 60)).sum()),
        "nostart": g["starts"].apply(lambda s: (s == 0).sum()),
        "subs": g.apply(lambda d: ((d["starts"] == 0) & (d["minutes"] > 0)).sum()),
        "mean_min_start": g.apply(lambda d: d.loc[d["starts"] > 0, "minutes"].mean()),
    })
    return MinutesModel(clf, pos_p60, pos_min, base_sub, stats)


def apply_status(pred: pd.DataFrame, status: pd.Series, chance: pd.Series) -> pd.DataFrame:
    """Live-only adjustment for FPL flags (not backtestable: no historical flags).

    Available with no chance given: unchanged. A chance (e.g. 75) scales start/sub
    probabilities. Injured/suspended/unavailable with no chance: zero.
    """
    out = pred.copy()
    factor = chance.astype(float) / 100
    factor = factor.where(chance.notna(), np.where(status.isin(["i", "s", "u", "n"]), 0.0, 1.0))
    factor = factor.to_numpy()
    for col in ("p_start", "p_sub"):
        out[col] = out[col] * factor
    out["p_appear"] = out["p_start"] + (1 - out["p_start"]) * out["p_sub"]
    out["p_60"] = out["p_start"] * out["p60_given_start"]
    out["e_minutes"] = out["e_minutes"] * factor
    return out
