"""Player baselines: current season blended with career by sample size and recency.

All history is first put on one footing, each step estimated from the data:

1. **Source scale.** Understat xG/xA run higher than FPL's (Opta) values on the same
   matches; Core-Insights tracks FPL closely. Every row is converted to the FPL scale with
   per-position factors fitted on the overlap (``source_scales``).
2. **League scale.** Non-PL output is converted to PL-equivalent with factors estimated from
   players who moved between that league and the PL (``league_factors``): the ratio of
   per-90 rates in adjacent seasons, pooled and shrunk toward 1.
3. **Opponent.** Each match is neutralised for the Elo gap with a slope fitted on PL team
   xG (``opponent_slope``): neutral = observed / exp(beta * gap / 400).

Then, per player and rate (xG/90, xA/90, shots/90), as of a date T:

- career rate c: recency-weighted (half-life h) over matches *before the current season*,
  shrunk toward the position mean with m0 pseudo-90s;
- prior strength k = min(kappa * effective career 90s, k_max), discounted by ``team_change``
  and ``role_change`` factors when the player's club or FPL position changed;
- posterior = (current events + k * c) / (current 90s + k).

A proven scorer underperforming over a few games regresses toward career (small current
exposure versus k); a large-sample decline wins (current exposure dominates k). Nothing is
a fixed ratio: the weight on career is k / (current 90s + k), reported per player.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

RATES = ("xg", "xa", "shots")


@dataclass(frozen=True)
class BaselineParams:
    half_life_days: float = 365.0
    kappa: float = 0.5          # career 90s -> prior strength
    k_max: float = 20.0         # cap on prior strength (in 90s)
    m0: float = 5.0             # pseudo-90s shrinking career toward the position mean
    team_change: float = 0.6    # multiply k when the club changed vs the career
    role_change: float = 0.6    # multiply k when the FPL position changed


# ------------------------------------------------------------------ calibration steps
def source_scales(pm: pd.DataFrame) -> dict[tuple[str, str], float]:
    """(rate, position) -> factor converting Understat values to the FPL scale, fitted on
    PL rows that carry both (2022-23 onward). Core-Insights is taken as FPL scale (r=0.996)."""
    out = {}
    both = pm.dropna(subset=["xg_us", "xg_fpl", "position"])
    for pos, g in both.groupby("position"):
        out[("xg", pos)] = float(g["xg_fpl"].sum() / max(g["xg_us"].sum(), 1e-9))
        g2 = g.dropna(subset=["xa_us", "xa_fpl"])
        out[("xa", pos)] = float(g2["xa_fpl"].sum() / max(g2["xa_us"].sum(), 1e-9))
    return out


def canonical_events(pm: pd.DataFrame, scales: dict) -> pd.DataFrame:
    """Per-row xG, xA, shots on the FPL scale (FPL > Core-Insights > scaled Understat)."""
    df = pm.copy()
    pos = df["position"]
    # carry a player's most common FPL position onto his non-PL rows
    main_pos = df.dropna(subset=["position"]).groupby("code")["position"].agg(
        lambda s: s.value_counts().index[0])
    pos = pos.fillna(df["code"].map(main_pos))
    df["pos"] = pos
    for r in ("xg", "xa"):
        f = np.array([scales.get((r, p), 1.0) for p in pos])
        df[f"{r}_c"] = df[f"{r}_fpl"].combine_first(df[f"{r}_ci"]).combine_first(
            df[f"{r}_us"] * f)
    df["shots_c"] = df["shots_ci"].combine_first(df["shots_us"])
    df["n90"] = df["minutes"].fillna(0) / 90
    return df


def opponent_slope(pm: pd.DataFrame) -> float:
    """beta in team_xg ~ exp(beta * (team_elo - opp_elo) / 400), PL team-matches."""
    pl = pm[(pm["competition"] == "Premier League") & pm["team_elo_pre"].notna()]
    team = pl.groupby(["team", "date"]).agg(xg=("xg_fpl", "sum"), e=("team_elo_pre", "first"),
                                            o=("opponent_elo_pre", "first")).dropna()
    team = team[team["xg"] > 0]
    x = (team["e"] - team["o"]) / 400
    y = np.log(team["xg"])
    return float(np.polyfit(x, y, 1)[0]) if len(team) > 50 else 0.0


def league_factors(ev: pd.DataFrame, min_minutes: int = 900,
                   prior_n: float = 5.0) -> pd.DataFrame:
    """PL-equivalent factor per league from players who played >= min_minutes in league L
    and in the PL in adjacent seasons. factor = pooled PL per-90 / L per-90 (xG+xA),
    shrunk toward 1 with ``prior_n`` pseudo-players."""
    ev = ev.copy()
    ev["start"] = ev["season"].str[:4].astype(int)
    ps = ev.groupby(["code", "start", "league"]).agg(n90=("n90", "sum"), xg=("xg_c", "sum"),
                                                     xa=("xa_c", "sum")).reset_index()
    ps = ps[ps["n90"] * 90 >= min_minutes]
    ps["rate"] = (ps["xg"] + ps["xa"]) / ps["n90"]
    pl = ps[ps["league"] == "EPL"].set_index(["code", "start"])["rate"]
    rows = []
    for lg in sorted(set(ps["league"]) - {"EPL", None}):
        if not isinstance(lg, str):
            continue
        o = ps[ps["league"] == lg]
        pairs = []
        for r in o.itertuples():
            for dt in (1, -1):
                v = pl.get((r.code, r.start + dt))
                if v is not None and r.rate > 0:
                    pairs.append((v, r.rate))
        n = len(pairs)
        if n:
            ratio = float(np.sum([a for a, _ in pairs]) / np.sum([b for _, b in pairs]))
            factor = (n * ratio + prior_n * 1.0) / (n + prior_n)
        else:
            factor = 1.0
        rows.append({"league": lg, "movers": n, "raw_ratio": round(ratio, 3) if n else None,
                     "factor": round(factor, 3)})
    return pd.DataFrame(rows)


def neutral_events(ev: pd.DataFrame, factors: pd.DataFrame, beta: float) -> pd.DataFrame:
    """Apply league factors and the opponent adjustment."""
    df = ev.copy()
    lf = factors.set_index("league")["factor"].to_dict() if len(factors) else {}
    lfac = df["league"].map(lf).fillna(1.0).where(df["league"] != "EPL", 1.0)
    gap = (df["team_elo_pre"] - df["opponent_elo_pre"]).fillna(0) / 400
    opp = np.exp(beta * gap)
    for r in RATES:
        df[f"{r}_n"] = df[f"{r}_c"] * lfac / opp
    return df


# ------------------------------------------------------------------ the baseline
def _position_means(cur: pd.DataFrame) -> dict[tuple[str, str], float]:
    out = {}
    for pos, g in cur.groupby("pos"):
        for r in RATES:
            n = g["n90"].sum()
            out[(r, pos)] = float(g[f"{r}_n"].sum() / n) if n else 0.0
    return out


def baseline(ev: pd.DataFrame, as_of: pd.Timestamp, season: str,
             p: BaselineParams | None = None) -> pd.DataFrame:
    """Posterior rates per player as of ``as_of`` (only rows strictly before it)."""
    p = p or BaselineParams()
    past = ev[ev["date"] < as_of]
    cur = past[past["season"] == season]
    car = past[past["season"] < season]
    pos_mean = _position_means(past[past["season"] >= str(int(season[:4]) - 2)])
    age = (as_of - car["date"]).dt.days.clip(lower=0)
    w = 0.5 ** (age / p.half_life_days)
    car = car.assign(w=w, wn90=w * car["n90"])
    for r in RATES:
        car[f"w_{r}"] = car["w"] * car[f"{r}_n"].fillna(0)
    cg = car.groupby("code")
    career = pd.DataFrame({"career_90s_eff": cg["wn90"].sum(),
                           **{f"career_{r}": cg[f"w_{r}"].sum() for r in RATES}})
    last_team = car.sort_values("date").groupby("code")["team"].last()
    car_pos = car.dropna(subset=["position"]).groupby("code")["position"].agg(
        lambda s: s.value_counts().index[0])
    cg2 = cur.groupby("code")
    current = pd.DataFrame({"cur_90s": cg2["n90"].sum(),
                            **{f"cur_{r}": cg2[f"{r}_n"].sum() for r in RATES},
                            "team": cg2["team"].last(), "pos": cg2["pos"].last()})
    out = current.join(career, how="outer")
    out["pos"] = out["pos"].fillna(out.index.to_series().map(car_pos))
    out = out.fillna({"cur_90s": 0.0, "career_90s_eff": 0.0,
                      **{f"cur_{r}": 0.0 for r in RATES}, **{f"career_{r}": 0.0 for r in RATES}})
    team_changed = out["team"].notna() & out.index.to_series().map(last_team).notna() & (
        out["team"] != out.index.to_series().map(last_team))
    role_changed = out["pos"].notna() & out.index.to_series().map(car_pos).notna() & (
        out["pos"] != out.index.to_series().map(car_pos))
    k = np.minimum(p.kappa * out["career_90s_eff"], p.k_max)
    k = k * np.where(team_changed, p.team_change, 1.0) * np.where(role_changed, p.role_change, 1.0)
    out["k"] = k
    out["team_changed"] = team_changed
    out["role_changed"] = role_changed
    for r in RATES:
        mu = np.array([pos_mean.get((r, ps), 0.0) for ps in out["pos"]])
        career_rate = (out[f"career_{r}"] + p.m0 * mu) / (out["career_90s_eff"] + p.m0)
        out[f"career_rate_{r}"] = career_rate
        out[f"cur_rate_{r}"] = out[f"cur_{r}"] / out["cur_90s"].where(out["cur_90s"] > 0)
        out[f"rate_{r}"] = (out[f"cur_{r}"] + k * career_rate) / (out["cur_90s"] + k).where(
            out["cur_90s"] + k > 0)
        # players with neither history nor minutes fall back to the position mean
        out[f"rate_{r}"] = out[f"rate_{r}"].fillna(pd.Series(mu, index=out.index))
    out["career_weight"] = (k / (out["cur_90s"] + k)).where(out["cur_90s"] + k > 0, 0.0)
    return out


def current_only(ev: pd.DataFrame, as_of: pd.Timestamp, season: str,
                 m0: float = 5.0) -> pd.DataFrame:
    """Benchmark: the engine's present approach in rate terms (current season shrunk to the
    position mean with m0 pseudo-90s, no career)."""
    return baseline(ev, as_of, season, BaselineParams(kappa=0.0, m0=m0))


# ------------------------------------------------------------------ backtest of the blend
def rate_backtest(ev: pd.DataFrame, season: str, p: BaselineParams | None,
                  rate: str = "xg", min_minutes: int = 30) -> dict[str, float]:
    """Predict each PL appearance's FPL-scale events from the rate as of that GW
    (exposure = actual minutes, isolating rate quality from the minutes model).
    Returns Poisson deviance and MAE per appearance for the blend and current-only."""
    pl = ev[(ev["season"] == season) & (ev["competition"] == "Premier League")
            & (ev["minutes"] >= min_minutes)].copy()
    pl["gw_date"] = pl["date"]
    preds_b, preds_c, actual = [], [], []
    for _, g in pl.groupby(pl["date"].dt.to_period("W")):
        as_of = g["date"].min()
        b = baseline(ev, as_of, season, p)[f"rate_{rate}"]
        c = current_only(ev, as_of, season)[f"rate_{rate}"]
        exp = g["n90"].to_numpy()
        preds_b.append(b.reindex(g["code"]).fillna(b.median()).to_numpy() * exp)
        preds_c.append(c.reindex(g["code"]).fillna(c.median()).to_numpy() * exp)
        actual.append(g[f"{rate}_c"].fillna(0).to_numpy())
    yb, yc, y = np.concatenate(preds_b), np.concatenate(preds_c), np.concatenate(actual)

    def dev(mu):
        mu = np.clip(mu, 1e-6, None)
        return float(np.mean(2 * (np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0)
                                  - (y - mu))))
    return {"n": int(len(y)), "blend_dev": dev(yb), "current_dev": dev(yc),
            "blend_mae": float(np.mean(np.abs(yb - y))),
            "current_mae": float(np.mean(np.abs(yc - y)))}
