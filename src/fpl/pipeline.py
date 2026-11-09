"""Live forecast: xPts per player per upcoming GW, from all finished matches so far.

Steps: tables -> as-of features -> team strength (last-season prior) -> minutes model
(+ live FPL flags) -> shrunk rates, bonus model -> per-fixture xPts -> per-GW sums.

Flags (status / chance_of_playing) are applied fully to the first GW and fade for later
GWs (an absent player's start probability recovers halfway toward his model value each GW).
Return dates are not in the API, so this fade is an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl.api.models import Bootstrap, Fixture
from fpl.data.features import player_form_features, team_congestion
from fpl.models import minutes as mm
from fpl.models.team_strength import TeamStrength, fit
from fpl.models.xpts import expected_points, fit_bonus, fit_rates, team_average_lambdas
from fpl.rules import GameRules

TEAM_PARAMS_RIDGE = 100.0
MODEL_VERSION = "xpts-v1 (team: xG Poisson+2025/26 prior; minutes: logistic; DefCon NB k=6)"


@dataclass
class Forecast:
    by_fixture: pd.DataFrame  # one row per player per upcoming fixture
    by_gw: pd.DataFrame       # element x event: xpts, p_start, p_appear, fixtures
    team: TeamStrength
    gws: list[int]


def _future_rows(b: Bootstrap, fixtures: list[Fixture], gws: list[int]) -> pd.DataFrame:
    pos = {t.id: t.singular_name_short for t in b.element_types}
    rows = []
    for f in fixtures:
        if f.event not in gws:
            continue
        for team, opp, home in ((f.team_h, f.team_a, True), (f.team_a, f.team_h, False)):
            rows.append({"fixture": f.id, "event": f.event, "kickoff_time":
                         f.kickoff_time.isoformat() if f.kickoff_time else None,
                         "team": team, "opponent_team": opp, "was_home": home})
    fx = pd.DataFrame(rows)
    els = pd.DataFrame([{"element": e.id, "team": e.team, "position": pos[e.element_type],
                         "now_cost": e.now_cost, "status": e.status,
                         "chance": e.chance_of_playing_next_round} for e in b.elements
                        if not getattr(e, "removed", False)])
    return els.merge(fx, on="team", how="inner")


def _asof_features(pm: pd.DataFrame, b: Bootstrap) -> pd.DataFrame:
    """Features as of now: append one synthetic future row per player and read it back."""
    pos = {t.id: t.singular_name_short for t in b.element_types}
    last_ko = pd.to_datetime(pm["kickoff_time"], utc=True).max()
    fut = pd.DataFrame([{
        "element": e.id, "fixture": -1, "round": 99, "team": e.team,
        "kickoff_time": (last_ko + pd.Timedelta(days=365)).isoformat(),
        "minutes": 0, "starts": 0, "expected_goals": 0.0, "expected_assists": 0.0,
        "cbit": 0, "cbirt": 0, "value": e.now_cost, "position": pos[e.element_type],
    } for e in b.elements])
    f = player_form_features(pd.concat([pm, fut], ignore_index=True))
    return f[f["fixture"] == -1].set_index("element")


def forecast(b: Bootstrap, fixtures: list[Fixture], pm: pd.DataFrame, tm: pd.DataFrame,
             rules: GameRules, prior: dict | None, horizon: int = 6,
             start_gw: int | None = None) -> Forecast:
    nxt = start_gw or next(e.id for e in b.events if e.is_next)
    gws = [g for g in range(nxt, nxt + horizon) if g <= 38]

    team = fit(tm, _params(), prior=prior)
    team_avg = team_average_lambdas(team, tm)
    feats = _asof_features(pm, b)
    hist_feats = player_form_features(pm)
    minutes_model = mm.fit(hist_feats)
    rates, bonus = fit_rates(pm), fit_bonus(pm)

    rows = _future_rows(b, fixtures, gws)
    # rest days per future fixture (league fixtures only)
    fut_tm = rows[["team", "fixture", "kickoff_time"]].drop_duplicates()
    cong = team_congestion(tm[["team", "fixture", "kickoff_time"]], fut_tm)
    rows = rows.merge(cong[["team", "fixture", "rest_days"]], on=["team", "fixture"], how="left")

    f = feats.reindex(rows["element"]).reset_index()
    for c in ("rest_days",):
        f[c] = rows[c].to_numpy()
    f["position"] = rows["position"].to_numpy()
    f["value"] = rows["now_cost"].to_numpy()
    raw = minutes_model.predict(f)
    flagged = mm.apply_status(raw, rows["status"], rows["chance"])
    k = (rows["event"] - nxt).to_numpy()
    w = 0.5 ** k  # flag weight fades with GWs ahead
    mins = raw.copy()
    for c in ("p_start", "p_sub", "e_minutes"):
        mins[c] = w * flagged[c].to_numpy() + (1 - w) * raw[c].to_numpy()
    mins["p_appear"] = mins["p_start"] + (1 - mins["p_start"]) * mins["p_sub"]
    mins["p_60"] = mins["p_start"] * mins["p60_given_start"]

    xp = expected_points(rows, mins, rates, team, team_avg, bonus, rules)
    xp["now_cost"] = rows["now_cost"].to_numpy()
    by_gw = (xp.groupby(["element", "event"])
             .agg(xpts=("xpts", "sum"), p_start=("p_start", "max"),
                  p_appear=("p_appear", "max"), fixtures=("fixture", "count"))
             .reset_index())
    # blanks: players whose team has no fixture in a GW get explicit zero rows
    full = pd.MultiIndex.from_product([[e.id for e in b.elements], gws],
                                      names=["element", "event"]).to_frame(index=False)
    by_gw = full.merge(by_gw, on=["element", "event"], how="left").fillna(
        {"xpts": 0.0, "p_start": 0.0, "p_appear": 0.0, "fixtures": 0})
    return Forecast(xp, by_gw, team, gws)


def _params():
    from fpl.models.team_strength import TeamStrengthParams
    return TeamStrengthParams(ridge=TEAM_PARAMS_RIDGE)


def xpts_matrix(fc: Forecast) -> pd.DataFrame:
    """element x GW xPts (columns are GW ids)."""
    return fc.by_gw.pivot(index="element", columns="event", values="xpts").fillna(0.0)


def summary_stats(values: np.ndarray) -> dict[str, float]:
    return {"mean": float(np.mean(values)), "p10": float(np.percentile(values, 10)),
            "p50": float(np.percentile(values, 50)), "p90": float(np.percentile(values, 90))}
