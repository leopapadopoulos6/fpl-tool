"""Venue / opponent / home-away splits, surfaced only when they clear a statistical bar.

For each player and split key (a ground, an opponent, home vs away) over PL history:
  observed O = sum of FPL-scale xG+xA in those matches,
  expected E = the player's own season rate (opponent-neutral) x minutes x opponent factor,
  effect = log((O + a) / (E + a)), se^2 ~ 1/(E + a)  (E is an expectation, O the count).

Empirical Bayes: the between-split variance tau^2 is estimated across *all* eligible
splits (method of moments). Each effect is shrunk by tau^2 / (tau^2 + se^2); if splits
are noise, tau^2 ~ 0 and every effect shrinks to nothing.

A split becomes a signal only if all hold:
  n >= MIN_MATCHES and minutes >= MIN_MINUTES;
  posterior P(effect has that sign) >= P_SIGN (on the shrunk estimate);
  Benjamini-Hochberg at FDR_Q across all tested splits, on exact Poisson tail p-values of
  O given E (a normal approximation of log(O/E) is badly anti-conservative at small E)
  (shrinkage already handles multiplicity in the size; testing the shrunk z too would
  double-count it);
  the *shrunk* shift moves the player's upcoming-fixture xPts by >= MIN_DELTA_XPTS.
Every signal carries its sample size and its Δ xPts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import gammainc, gammaincc
from scipy.stats import norm

MIN_MATCHES = 6
MIN_MINUTES = 450
P_SIGN = 0.9
FDR_Q = 0.10
MIN_DELTA_XPTS = 0.3
PSEUDO = 0.5


def split_effects(ev: pd.DataFrame, key: str, beta: float) -> pd.DataFrame:
    """``ev``: neutral events (baseline.neutral_events). ``key``: venue | opponent | is_home."""
    pl = ev[(ev["competition"] == "Premier League") & (ev["minutes"] > 0)].copy()
    pl["out"] = pl["xg_c"].fillna(0) + pl["xa_c"].fillna(0)
    pl["out_n"] = pl["xg_n"].fillna(0) + pl["xa_n"].fillna(0)
    season_rate = pl.groupby(["code", "season"]).apply(
        lambda g: g["out_n"].sum() / max(g["n90"].sum(), 1e-9)).rename("srate")
    pl = pl.join(season_rate, on=["code", "season"])
    gap = (pl["team_elo_pre"] - pl["opponent_elo_pre"]).fillna(0) / 400
    pl["exp"] = pl["srate"] * pl["n90"] * np.exp(beta * gap)
    g = pl.groupby(["code", key])
    s = g.agg(n=("out", "size"), minutes=("minutes", "sum"), O=("out", "sum"),
              E=("exp", "sum")).reset_index()
    s = s[(s["n"] >= MIN_MATCHES) & (s["minutes"] >= MIN_MINUTES)].copy()
    s["effect"] = np.log((s["O"] + PSEUDO) / (s["E"] + PSEUDO))
    # E is an expectation, not a count: sampling variance of log(O/E) is ~ 1/E
    s["se2"] = 1 / (s["E"] + PSEUDO)
    return s.rename(columns={key: "split_value"}).assign(split=key)


def shrink_and_test(s: pd.DataFrame) -> pd.DataFrame:
    if s.empty:
        return s.assign(tau2=0.0, post_mean=0.0, post_sd=0.0, p_sign=0.5, p=1.0, bh_pass=False)
    tau2 = max(0.0, float(s["effect"].var() - s["se2"].mean()))
    shrink = tau2 / (tau2 + s["se2"]) if tau2 > 0 else 0.0 * s["se2"]
    s = s.assign(tau2=tau2, post_mean=s["effect"] * shrink,
                 post_sd=np.sqrt(np.where(tau2 > 0, tau2 * s["se2"] / (tau2 + s["se2"]), 0.0)))
    # posterior sign probability (from the shrunk estimate)
    zp = np.nan_to_num(np.where(s["post_sd"] > 0,
                                s["post_mean"].abs() / s["post_sd"].replace(0, np.nan), 0.0))
    s["p_sign"] = norm.cdf(zp)
    # FDR control on the raw evidence: shrinkage already handles multiplicity in the
    # *size* estimate, so testing the shrunk z as well would double-count it
    # exact Poisson tails (regularised incomplete gamma; works for non-integer xG totals):
    # P(X >= O | E) = gammainc(O, E), P(X <= O | E) = gammaincc(O + 1, E)
    o, e = s["O"].to_numpy(), np.maximum(s["E"].to_numpy(), 1e-9)
    upper = np.where(o > 0, gammainc(np.maximum(o, 1e-9), e), 1.0)
    lower = gammaincc(o + 1, e)
    s["p"] = np.minimum(1.0, 2 * np.minimum(upper, lower))
    order = np.argsort(s["p"].to_numpy())
    m = len(s)
    thresh = FDR_Q * (np.arange(1, m + 1) / m)
    passed = np.zeros(m, dtype=bool)
    sorted_p = s["p"].to_numpy()[order]
    ok = np.where(sorted_p <= thresh)[0]
    if len(ok):
        passed[order[: ok.max() + 1]] = True
    s["bh_pass"] = passed & (s["p_sign"] >= P_SIGN) & (s["post_sd"] > 0)
    return s


def signals(tested: pd.DataFrame, upcoming_attack_xpts: pd.DataFrame) -> pd.DataFrame:
    """``upcoming_attack_xpts``: code, split, split_value, attack_xpts (goal + assist points
    expected in the upcoming fixture that matches this split). Δ xPts = attack_xpts *
    (exp(post_mean) - 1). Returns only splits that pass every gate."""
    if tested.empty:
        return tested
    t = tested[tested["bh_pass"]].merge(upcoming_attack_xpts,
                                        on=["code", "split", "split_value"], how="inner")
    t["delta_xpts"] = t["attack_xpts"] * (np.exp(t["post_mean"]) - 1)
    t = t[t["delta_xpts"].abs() >= MIN_DELTA_XPTS]
    return t[["code", "split", "split_value", "n", "minutes", "O", "E", "effect", "post_mean",
              "p_sign", "delta_xpts"]].rename(columns={"O": "observed", "E": "expected"})
