"""Build every dashboard dataset from one engine run (and gate it on the backtests).

Output rules (CLAUDE.md): decisions first with the expected-points delta vs rolling;
template moves flagged rather than celebrated; differentials and divergence from top-10k
effective ownership flagged; assumptions and confidence attached; affordability is always
selling prices + bank (the optimizer enforces it; the players table marks it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fpl.api.models import Bootstrap, Fixture
from fpl.backtest import minutes as bt_min
from fpl.backtest import team_strength as bt_team
from fpl.backtest import xpts as bt_xpts
from fpl.chips.planner import CHIP_LABEL, plan_chips
from fpl.data.features import build_player_features
from fpl.data.priors import team_prior
from fpl.engine import CAPTAIN_HURDLE, HIT_HURDLE, EngineResult, apply_captain_hurdle, run
from fpl.models.team_strength import TeamStrengthParams
from fpl.optimize.milp import OptInput, OptSettings, candidate_pool
from fpl.pipeline import MODEL_VERSION, forecast, xpts_matrix
from fpl.rules import GameRules
from fpl.state import ManagerState

TEMPLATE_EO = 50.0      # top-10k EO at or above this: a template pick
DIFFERENTIAL_EO = 10.0  # below this: a differential


@dataclass
class Gate:
    passed: bool
    reasons: list[str]


def model_health(features: pd.DataFrame, tm: pd.DataFrame, rules: GameRules,
                 prior: dict) -> tuple[list[dict], list[dict], Gate]:
    rounds = sorted(features["round"].unique())
    test = [g for g in rounds if g >= 4]
    rows, calib = [], []
    xp = bt_xpts.predictions(features, tm, rules, test, prior)
    s = bt_xpts.score(xp).set_index("predictor")
    best_base_mae = min(s.loc["ppm", "mae"], s.loc["last3", "mae"])
    best_base_rho = max(s.loc["ppm", "spearman"], s.loc["last3", "spearman"])
    reasons = []
    if s.loc["model", "mae"] >= best_base_mae:
        reasons.append("xPts MAE not better than baselines")
    if s.loc["model", "spearman"] <= best_base_rho:
        reasons.append("xPts rank correlation not better than baselines")
    rows += [
        _h("xPts", "Average error (points)", s.loc["model", "mae"], best_base_mae, True),
        _h("xPts", "Rank correlation", s.loc["model", "spearman"], best_base_rho, False),
        _h("xPts", "Actual points of top 20 picks", s.loc["model", "top20_actual"],
           max(s.loc["ppm", "top20_actual"], s.loc["last3", "top20_actual"]), False),
    ]
    dc = bt_xpts.defcon_check(xp)
    rows.append(_h("DefCon", "Probability error (Brier)", dc["brier"], dc["base_brier"], True))
    for r in bt_xpts.calibration(xp).itertuples():
        calib.append({"model": "xPts", "predicted": round(r.predicted, 2),
                      "actual": round(r.actual, 2), "n": int(r.n)})
    mp = bt_min.predictions(features, test)
    ms = bt_min.score(mp).set_index("predictor")
    base = min(ms.loc["prev", "start_brier"], ms.loc["rate3", "start_brier"])
    if ms.loc["model", "start_brier"] >= base:
        reasons.append("P(start) not better than baselines")
    rows.append(_h("Minutes", "Start probability error (Brier)", ms.loc["model", "start_brier"],
                   base, True))
    for r in bt_min.reliability(mp).itertuples():
        calib.append({"model": "Start probability", "predicted": round(r.predicted, 3),
                      "actual": round(r.actual, 3), "n": int(r.n)})
    late = [g for g in rounds if g >= max(rounds) - 2]
    tp = bt_team.predictions(tm, TeamStrengthParams(ridge=100.0), late, prior)
    ts = bt_team.score(tp).set_index("predictor")
    rows.append(_h("Team strength", "Clean-sheet probability error (Brier)",
                   ts.loc["model", "cs_brier"], ts.loc["naive", "cs_brier"], True))
    rows.append(_h("Team strength", "Goals prediction loss", ts.loc["model", "goal_nll"],
                   ts.loc["naive", "goal_nll"], True))
    n_test = int(len(xp))
    for r in rows:
        r["sample"] = n_test if r["model"] in ("xPts", "DefCon") else (
            int(len(mp)) if r["model"] == "Minutes" else int(len(tp)))
        r["test_rounds"] = f"GW{min(test)}–{max(test)}" if r["model"] != "Team strength" \
            else f"GW{min(late)}–{max(late)}"
    return rows, calib, Gate(not reasons, reasons)


def _h(model, metric, value, baseline, lower_better) -> dict:
    better = value < baseline if lower_better else value > baseline
    close = abs(value - baseline) < 0.01 * abs(baseline)
    verdict = ("About equal" if close else
               "Better than baseline" if better else "Worse than baseline")
    return {"model": model, "metric": metric, "value": round(float(value), 3),
            "baseline": round(float(baseline), 3), "verdict": verdict}


def build(b: Bootstrap, fixtures: list[Fixture], pm: pd.DataFrame, tm: pd.DataFrame,
          state: ManagerState, rules: GameRules, data_dir: Path,
          eo: pd.DataFrame | None, eo_meta: dict | None, horizon: int = 6,
          chips: bool = True) -> dict[str, list[dict[str, Any]]]:
    prior = team_prior(b, data_dir)
    feats = build_player_features(pm, tm)
    health, calib, gate = model_health(feats, tm, rules, prior)
    out: dict[str, list[dict[str, Any]]] = {"model_health": health, "calibration": calib}
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    settings = OptSettings()
    out["assumptions"] = [{
        "model_version": MODEL_VERSION, "horizon_gws": horizon, "decay": settings.decay,
        "bench_weight": settings.bench_weight, "ft_value_end": settings.ft_value,
        "hit_hurdle": HIT_HURDLE, "captain_hurdle": CAPTAIN_HURDLE,
        "gate": "passed" if gate.passed else "failed: " + "; ".join(gate.reasons),
        "eo_source": (f"top {eo_meta['top_k']:,} overall, {eo_meta['sampled']} sampled, "
                      f"GW{eo_meta['gw']} picks") if eo_meta else "not sampled",
        "generated_utc": now,
    }]
    if not gate.passed:
        return out

    res = run(b, fixtures, pm, tm, state, rules, data_dir, horizon=horizon, settings=settings)
    names = res.players["name"].to_dict()
    eo_map = eo.set_index("element")["eo_pct"].to_dict() if eo is not None else {}
    cap_note = apply_captain_hurdle(res, eo_map)
    if cap_note:
        out["assumptions"][0]["captain_note"] = cap_note
    own_map = eo.set_index("element")["owned_pct"].to_dict() if eo is not None else {}
    out.update(_decisions(res, names, eo_map))
    out.update(_lineup(res, names, eo_map, own_map, b))
    out["plan"] = _plan(res, names)
    out["players"] = _players(res, state, eo_map, b)
    out["fixtures_strength"] = _fixtures(res, b)
    if chips:
        out["chip_gain"] = _chips(b, fixtures, pm, tm, state, rules, prior, settings)
    return out


def _fmt(ids, names) -> str:
    return ", ".join(names[i] for i in ids) if ids else ""


def _decisions(res: EngineResult, names: dict, eo: dict) -> dict:
    rows = []
    for i, o in enumerate(res.options):
        ins, outs = o.transfers_in, o.transfers_out
        tmpl_in = [names[p] for p in ins if eo.get(p, 0) >= TEMPLATE_EO]
        diff_in = [names[p] for p in ins if eo.get(p, 0) < DIFFERENTIAL_EO]
        sell_tmpl = [names[p] for p in outs if eo.get(p, 0) >= TEMPLATE_EO]
        flags = []
        if diff_in:
            flags.append("Differential in: " + ", ".join(diff_in))
        if sell_tmpl:
            flags.append("Selling a top-10k template player: " + ", ".join(sell_tmpl))
        if tmpl_in:
            flags.append("Template move (top-10k EO ≥ 50%): " + ", ".join(tmpl_in))
        rows.append({
            "option": i + 1, "label": o.label,
            "transfers_in": _fmt(ins, names) or "None",
            "transfers_out": _fmt(outs, names) or "None",
            "hits": o.hits, "delta_vs_roll": round(o.delta_vs_roll, 2),
            "next_gw_xpts": round(o.plan.gws[0].xpts - 4 * o.hits, 2),
            "horizon_xpts": round(o.plan.total_xpts, 1),
            "recommended": "Yes" if o.plan is res.best else "",
            "flags": " · ".join(flags),
        })
    return {"decision": rows}


def _lineup(res: EngineResult, names: dict, eo: dict, own: dict, b: Bootstrap) -> dict:
    g = res.best.gws[0]
    fc = res.forecast.by_gw[res.forecast.by_gw["event"] == g.gw].set_index("element")
    pos = res.players["position"].to_dict()
    rows = []
    for p in g.lineup + g.bench:
        role = ("Captain" if p == g.captain else "Vice-captain" if p == g.vice
                else "Starting" if p in g.lineup else f"Bench {g.bench.index(p) + 1}")
        rows.append({"element": p, "player": names[p], "pos": pos[p], "role": role,
                     "xpts": round(float(fc.at[p, "xpts"]), 2),
                     "p_start": round(float(fc.at[p, "p_start"]), 2),
                     "eo_top10k": round(eo.get(p, 0.0), 1),
                     "owned_top10k": round(own.get(p, 0.0), 1)})
    cap = res.captaincy.copy()
    caps = []
    for r in cap.head(6).itertuples():
        caps.append({"element": int(r.element), "player": names[r.element],
                     "mean": round(r.mean, 2), "p10": round(r.p10, 1), "p90": round(r.p90, 1),
                     "p_haul": round(r.p_haul, 3), "p_blank": round(r.p_blank, 3),
                     "eo_top10k": round(eo.get(int(r.element), 0.0), 1),
                     "chosen": "Captain" if r.element == g.captain else (
                         "Vice" if r.element == g.vice else "")})
    return {"lineup": rows, "captaincy": caps}


def _plan(res: EngineResult, names: dict) -> list[dict]:
    return [{"gw": g.gw, "free_transfers": g.free_transfers,
             "transfers_in": _fmt(g.buys, names) or "Roll",
             "transfers_out": _fmt(g.sells, names), "hits": g.hits,
             "captain": names[g.captain], "xpts": round(g.xpts - 4 * g.hits, 1),
             "bank_m": round(g.bank_after / 10, 1)} for g in res.best.gws]


def _players(res: EngineResult, state: ManagerState, eo: dict, b: Bootstrap) -> list[dict]:
    xp = xpts_matrix(res.forecast)
    gws = res.gws
    pl = res.players
    short = {t.id: t.short_name for t in b.teams}
    max_sell = {ps: max((p.selling_price for p in state.squad if p.position == ps), default=0)
                for ps in ("GKP", "DEF", "MID", "FWD")}
    first = res.forecast.by_gw[res.forecast.by_gw["event"] == gws[0]].set_index("element")
    tot = xp.sum(axis=1)
    keep = set(pl.index[pl["owned"]])
    for ps in ("GKP", "DEF", "MID", "FWD"):
        idx = pl.index[(pl["position"] == ps) & pl["available"]]
        keep |= set(tot.reindex(idx).sort_values(ascending=False).head(15).index)
    rows = []
    for p in sorted(keep, key=lambda p: -tot.get(p, 0)):
        r = pl.loc[p]
        affordable = bool(r["owned"]) or r["now_cost"] <= state.bank + max_sell[r["position"]]
        row = {"element": int(p), "player": r["name"], "team": short[r["team"]],
               "pos": r["position"], "price_m": round(r["now_cost"] / 10, 1),
               "owned": "Yes" if r["owned"] else "", "affordable": "Yes" if affordable else "No",
               "p_start": round(float(first.at[p, "p_start"]), 2) if p in first.index else 0.0,
               "eo_top10k": round(eo.get(p, 0.0), 1),
               "xpts_total": round(float(tot.get(p, 0)), 1)}
        for i, g in enumerate(gws):
            row[f"gw{i + 1}"] = round(float(xp.at[p, g]), 2) if p in xp.index else 0.0
        rows.append(row)
    return rows


def _fixtures(res: EngineResult, b: Bootstrap) -> list[dict]:
    fx = res.forecast.by_fixture.drop_duplicates(["team", "fixture"])
    short = {t.id: t.short_name for t in b.teams}
    return [{"team": short[r.team], "gw": int(r.event),
             "opponent": f"{short[r.opponent_team]} ({'H' if r.was_home else 'A'})",
             "xg_for": round(r.lambda_for, 2), "xg_against": round(r.lambda_against, 2),
             "p_clean_sheet": round(r.p_cs, 3)}
            for r in fx.sort_values(["team", "event", "fixture"]).itertuples()]


def _chips(b, fixtures, pm, tm, state, rules, prior, settings) -> list[dict]:
    first_half = [w for w in state.chips_available if w.stop_event <= 19]
    if not first_half:
        return []
    last = max(w.stop_event for w in first_half)
    fc = forecast(b, fixtures, pm, tm, rules, prior, horizon=last - state.next_gw + 1,
                  start_gw=state.next_gw)
    from fpl.engine import players_frame
    players = players_frame(b, state, rules)
    xp = xpts_matrix(fc)
    pool = candidate_pool(players, xp, {"GKP": 8, "DEF": 30, "MID": 30, "FWD": 18})
    inp = OptInput(players=players.loc[pool], xp=xp.loc[xp.index.intersection(pool)],
                   gws=fc.gws, bank=state.bank, free_transfers=state.free_transfers,
                   rules=rules, settings=settings)
    df, _ = plan_chips(inp, first_half)
    return [{"chip": CHIP_LABEL.get(r.chip, r.chip), "gw": int(r.gw),
             "gain": round(float(r.gain), 2), "gw_xpts": round(float(r.gw_xpts), 1)}
            for r in df.itertuples()]
