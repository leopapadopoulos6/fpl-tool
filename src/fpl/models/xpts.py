"""Expected points per player per fixture from underlying data (no raw past points, no FDR).

Per fixture row (summed per GW, so doubles and blanks fall out naturally):

- appearance: P(appear) * 1 + P(60+) * 1
- goals:      xG90 (shrunk) * E[minutes]/90 * opponent/venue factor * goal points (position)
- assists:    xA90 likewise * assist points
- clean sheet: P(60+) * exp(-lambda_against) * CS points (position)
- conceded:   GKP/DEF: P(start) * E[floor(G/2)], G ~ Poisson(lambda_against * min/90)
- saves:      GKP: P(start) * E[floor(S/3)], S ~ Poisson(saves90 * min/90 * factor)
- DefCon:     P(start) * P(count >= threshold) * 2, count ~ NegBin(rate90 * min/90, k)
- bonus:      linear model of bonus on realised components (goals, assists, CS, saves/3,
              DefCon hit, position), fitted on past starts, applied to expected components
- cards:      yellow90 (shrunk) * E[minutes]/90 * yellow points

Per-90 rates are shrunk toward a position-and-price prior (empirical Bayes with
``PRIOR_MINUTES`` pseudo-minutes). Opponent factor = fixture lambda_for / the team's average
model lambda_for over its past fixtures, so a player's rate keeps his team context.
Scoring values come from ``GameRules`` (the API's ``game_config.scoring``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression

from fpl.models.minutes import MinutesModel
from fpl.models.team_strength import TeamStrength
from fpl.rules import DEFCON_THRESHOLD, GameRules

PRIOR_MINUTES = {"xg": 450.0, "xa": 450.0, "saves": 450.0, "yellow": 900.0, "dc": 270.0}
DEFCON_DISPERSION = 6.0  # negative-binomial size; checked in the backtest (DefCon Brier)
ID_COLS = ("element", "position", "team", "opponent_team", "was_home", "fixture", "round",
           "kickoff_time", "event")
COMPONENTS = ("appearance", "goals", "assists", "clean_sheet", "conceded", "saves", "defcon",
              "cards", "bonus")
RATE_COLS = {"xg": "expected_goals", "xa": "expected_assists", "saves": "saves",
             "yellow": "yellow_cards"}


def _floor_div_mean(lam: np.ndarray, d: int, kmax: int = 25) -> np.ndarray:
    """E[floor(X/d)] for X ~ Poisson(lam), vectorised."""
    k = np.arange(kmax)[:, None]
    pmf = stats.poisson.pmf(k, np.clip(lam, 0, None)[None, :])
    return ((k // d) * pmf).sum(axis=0)


def _nb_tail(mean: np.ndarray, thr: np.ndarray, size: float) -> np.ndarray:
    """P(X >= thr) for X ~ NegBin with the given mean and size (vectorised)."""
    mean = np.clip(mean, 1e-6, None)
    p = size / (size + mean)
    return stats.nbinom.sf(thr - 1, size, p)


@dataclass
class PlayerRates:
    table: pd.DataFrame  # element -> xg90, xa90, saves90, yellow90, dc90, minutes


def fit_rates(train: pd.DataFrame) -> PlayerRates:
    """Shrunk per-90 rates from training matches (rounds before the target)."""
    g = train.groupby("element")
    mins = g["minutes"].sum()
    last = train.sort_values("round").groupby("element").tail(1).set_index("element")
    pos = last["position"]
    price = np.log(last["value"].clip(lower=35) / 10)
    out = pd.DataFrame({"minutes": mins, "position": pos, "log_price": price})
    dc_count = train["cbit"].where(train["position"] == "DEF", train["cbirt"])
    sums = {k: g[c].sum() for k, c in RATE_COLS.items()}
    sums["dc"] = dc_count.groupby(train["element"]).sum()
    for key, s in sums.items():
        raw90 = (s * 90 / mins.replace(0, np.nan)).fillna(0)
        prior = _price_prior(raw90, mins, pos, price)
        m0 = PRIOR_MINUTES[key]
        out[f"{key}90"] = (s.fillna(0) + prior * m0 / 90) * 90 / (mins + m0)
    return PlayerRates(out)


def _price_prior(raw90: pd.Series, mins: pd.Series, pos: pd.Series,
                 price: pd.Series) -> pd.Series:
    """Per-position minutes-weighted regression of the per-90 rate on log price."""
    prior = pd.Series(0.0, index=raw90.index)
    for p in pos.dropna().unique():
        idx = (pos == p) & (mins > 0)
        if idx.sum() < 5:
            prior[pos == p] = float(np.average(raw90[idx], weights=mins[idx])) if idx.any() else 0
            continue
        reg = LinearRegression().fit(price[idx].to_frame(), raw90[idx], sample_weight=mins[idx])
        prior[pos == p] = np.clip(reg.predict(price[pos == p].to_frame()), 0, None)
    return prior


@dataclass
class BonusModel:
    reg: LinearRegression
    cols: list[str]

    def predict(self, comp: pd.DataFrame) -> np.ndarray:
        return np.clip(self.reg.predict(comp[self.cols]), 0, 3)


def _bonus_frame(goals, assists, cs, saves3, dc_hit, position) -> pd.DataFrame:
    df = pd.DataFrame({"goals": goals, "assists": assists, "cs_def": cs, "saves3": saves3,
                       "dc_hit": dc_hit})
    for p in ("GKP", "DEF", "MID"):
        df[f"pos_{p}"] = (np.asarray(position) == p).astype(float)
        df[f"goals_{p}"] = df["goals"] * df[f"pos_{p}"]
    return df


def fit_bonus(train: pd.DataFrame) -> BonusModel:
    st = train[train["starts"] > 0]
    thr = st["position"].map(DEFCON_THRESHOLD).fillna(10_000)
    dc = st["cbit"].where(st["position"] == "DEF", st["cbirt"])
    cs = (st["clean_sheets"] > 0) & st["position"].isin(["GKP", "DEF"])
    X = _bonus_frame(st["goals_scored"], st["assists"], cs.astype(float),
                     st["saves"] // 3, (dc >= thr).astype(float), st["position"])
    reg = LinearRegression().fit(X, st["bonus"])
    return BonusModel(reg, list(X.columns))


def team_average_lambdas(team: TeamStrength, team_matches: pd.DataFrame) -> pd.DataFrame:
    """Each team's mean model lambda_for / lambda_against over its past fixtures."""
    rows = []
    for r in team_matches.itertuples():
        fx = team.fixture(r.team, r.opponent, bool(r.home))
        rows.append((r.team, fx["lambda_for"], fx["lambda_against"]))
    df = pd.DataFrame(rows, columns=["team", "lf", "la"])
    return df.groupby("team")[["lf", "la"]].mean()


def expected_points(
    rows: pd.DataFrame, minutes: pd.DataFrame, rates: PlayerRates, team: TeamStrength,
    team_avg: pd.DataFrame, bonus: BonusModel, rules: GameRules,
) -> pd.DataFrame:
    """``rows``: element, position, team, opponent_team, was_home (one per fixture).
    ``minutes``: output of ``MinutesModel.predict`` (or ``apply_status``), same index."""
    r = rows.reset_index(drop=True)
    mnt = minutes.reset_index(drop=True)
    rt = rates.table.reindex(r["element"]).reset_index(drop=True)
    pos = r["position"].to_numpy()
    lf = np.array([team.expected_goals(t, o, bool(h))
                   for t, o, h in zip(r["team"], r["opponent_team"], r["was_home"], strict=True)])
    la = np.array([team.expected_goals(o, t, not bool(h))
                   for t, o, h in zip(r["team"], r["opponent_team"], r["was_home"], strict=True)])
    avg = team_avg.reindex(r["team"]).reset_index(drop=True)
    f_att = lf / avg["lf"].fillna(pd.Series(lf)).to_numpy()
    f_def = la / avg["la"].fillna(pd.Series(la)).to_numpy()

    p_start, p_app = mnt["p_start"].to_numpy(), mnt["p_appear"].to_numpy()
    p60 = mnt["p_60"].to_numpy()
    emin, emin_s = mnt["e_minutes"].to_numpy(), mnt["e_minutes_if_start"].to_numpy()
    pts = lambda stat: np.array([rules.points(stat, p) for p in pos], dtype=float)  # noqa: E731

    e_goals = rt["xg90"].fillna(0).to_numpy() * emin / 90 * f_att
    e_assists = rt["xa90"].fillna(0).to_numpy() * emin / 90 * f_att
    p_cs = np.exp(-la)
    gc_pts = pts("goals_conceded")
    e_gc_units = p_start * _floor_div_mean(la * emin_s / 90, 2)
    saves_lam = rt["saves90"].fillna(0).to_numpy() * emin_s / 90 * f_def
    e_save_units = np.where(pos == "GKP", p_start * _floor_div_mean(saves_lam, 3), 0.0)
    thr = np.array([DEFCON_THRESHOLD.get(p) or 10_000 for p in pos], dtype=int)
    dc_mean = rt["dc90"].fillna(0).to_numpy() * emin_s / 90
    p_dc_start = np.where(thr < 10_000, _nb_tail(dc_mean, thr, DEFCON_DISPERSION), 0.0)
    p_dc = p_start * p_dc_start

    comp = pd.DataFrame({
        "appearance": p_app * rules.scoring.get("short_play", 1)
        + p60 * (rules.scoring.get("long_play", 2) - rules.scoring.get("short_play", 1)),
        "goals": e_goals * pts("goals_scored"),
        "assists": e_assists * pts("assists"),
        "clean_sheet": p60 * p_cs * pts("clean_sheets"),
        "conceded": e_gc_units * gc_pts,
        "saves": e_save_units * rules.points("saves", "GKP"),
        "defcon": p_dc * pts("defensive_contribution"),
        "cards": rt["yellow90"].fillna(0).to_numpy() * emin / 90
        * rules.points("yellow_cards", "MID"),
    })
    cs_def = np.where(np.isin(pos, ["GKP", "DEF"]), p60 * p_cs, 0.0)
    bonus_x = _bonus_frame(e_goals, e_assists, cs_def, e_save_units, p_dc, pos)
    comp["bonus"] = bonus.predict(bonus_x) * p_start
    comp = comp.add_prefix("pts_")
    comp["xpts"] = comp.sum(axis=1)
    ids = [c for c in ID_COLS if c in r.columns]
    out = pd.concat([r[ids], comp], axis=1)
    out["e_goals"], out["e_assists"], out["p_cs"], out["p_defcon"] = e_goals, e_assists, p_cs, p_dc
    out["lambda_for"], out["lambda_against"] = lf, la
    for c in ("p_start", "p_appear", "p_60", "e_minutes"):
        out[c] = mnt[c].to_numpy()
    return out


def simulate(xp: pd.DataFrame, rules: GameRules, n: int = 4000, seed: int = 0) -> np.ndarray:
    """Monte Carlo points per row (n draws): for captaincy / TC variance and quantiles.

    Components are drawn independently given the row's parameters (start, 60+, goals,
    assists, clean sheet, DefCon); bonus and cards enter at their expected values. Means
    match ``expected_points`` closely; tails are what this adds.
    """
    rng = np.random.default_rng(seed)
    k = len(xp)
    start = rng.random((n, k)) < xp["p_start"].to_numpy()
    p60s = np.where(xp["p_start"] > 0, xp["p_60"] / xp["p_start"].clip(lower=1e-9), 0)
    sub_p = np.where(xp["p_start"] < 1, (xp["p_appear"] - xp["p_start"])
                     / (1 - xp["p_start"]).clip(lower=1e-9), 0)
    played60 = start & (rng.random((n, k)) < p60s)
    sub = ~start & (rng.random((n, k)) < sub_p)
    appear = start | sub
    mins_frac = np.where(played60, 1.0, np.where(start, 0.6, np.where(sub, 0.25, 0.0)))
    scale = xp["e_minutes"].to_numpy() / 90
    scale = np.where(scale > 0, scale, 1e-9)
    g = rng.poisson(xp["e_goals"].to_numpy() / scale * mins_frac)
    a = rng.poisson(xp["e_assists"].to_numpy() / scale * mins_frac)
    cs = played60 & (rng.random((n, k)) < xp["p_cs"].to_numpy())
    pdc = np.where(xp["p_start"] > 0, xp["p_defcon"] / xp["p_start"].clip(lower=1e-9), 0)
    dc = start & (rng.random((n, k)) < pdc)
    pos = xp["position"].to_numpy()
    pts = lambda stat: np.array([rules.points(stat, p) for p in pos], dtype=float)  # noqa: E731
    total = (appear * 1.0 + played60 * 1.0 + g * pts("goals_scored") + a * pts("assists")
             + cs * pts("clean_sheets") + dc * pts("defensive_contribution"))
    total = total + (xp["pts_conceded"] + xp["pts_saves"] + xp["pts_bonus"]
                     + xp["pts_cards"]).to_numpy()
    return total


__all__ = ["MinutesModel", "expected_points", "fit_bonus", "fit_rates", "simulate",
           "team_average_lambdas"]
