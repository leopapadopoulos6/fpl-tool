"""Build every dashboard dataset from one engine run (and gate it on the backtests).

Output rules (CLAUDE.md): decisions first with the expected-points delta vs rolling;
template moves flagged rather than celebrated; differentials and divergence from top-10k
effective ownership flagged; assumptions and confidence attached; affordability is always
selling prices + bank (the optimizer enforces it; the players table marks it).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fpl.api.models import Bootstrap, Fixture
from fpl.backtest import minutes as bt_min
from fpl.backtest import team_risk as bt_risk
from fpl.backtest import team_strength as bt_team
from fpl.backtest import xpts as bt_xpts
from fpl.chips.joint import CHIP_DECAY, plan_joint
from fpl.chips.planner import CHIP_LABEL
from fpl.data.features import build_player_features
from fpl.data.priors import team_prior
from fpl.engine import (
    CAPTAIN_HURDLE,
    HIT_HURDLE,
    EngineResult,
    apply_captain_hurdle,
    risk_note,
    run,
    team_risk,
)
from fpl.models.team_strength import TeamStrengthParams
from fpl.optimize.milp import OptInput, OptSettings, candidate_pool
from fpl.pipeline import MODEL_VERSION, forecast, xpts_matrix
from fpl.report import ledger
from fpl.rules import GameRules
from fpl.state import ManagerState
from fpl.ui.dashboard import NEXT_FIXTURES, card_fields

TEMPLATE_EO = 50.0      # top-10k EO at or above this: a template pick
DIFFERENTIAL_EO = 10.0  # below this: a differential


@dataclass
class Gate:
    passed: bool
    reasons: list[str]
    risk_ok: bool = False  # shared-score team ranges beat independent ones (tie-break note)


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
    risk = bt_risk.coverage(xp, rules)
    rows += team_range_rows(risk)
    return rows, calib, Gate(not reasons, reasons, risk_ok=risk["joint_better"])


def team_range_rows(risk: dict) -> list[dict]:
    """Model-health rows for the team points range: one shared score per match (model)
    against independent players (baseline). Coverage is better the closer it is to 80%."""
    cj, ci = risk["coverage_joint"], risk["coverage_independent"]
    t = bt_risk.TARGET
    close = abs(abs(cj - t) - abs(ci - t)) < 0.005
    cov = {"model": "Team range", "metric": "Team totals inside the 10–90% range (target 0.8)",
           "value": round(cj, 3), "baseline": round(ci, 3),
           "verdict": "About equal" if close else (
               "Better than baseline" if abs(cj - t) < abs(ci - t) else "Worse than baseline")}
    crps = _h("Team range", "Team total forecast loss (CRPS)", risk["crps_joint"],
              risk["crps_independent"], True)
    for r in (cov, crps):
        r["sample"], r["test_rounds"] = risk["teams"], risk["rounds"]
    return [cov, crps]


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
          chips: bool = True, squad_rows: list[dict[str, Any]] | None = None,
          ) -> dict[str, list[dict[str, Any]]]:
    """``squad_rows`` (the state ``squad`` dataset) gain the model fields in place, so the
    dashboard's list and formation views read one set of rows."""
    prior = team_prior(b, data_dir)
    feats = build_player_features(pm, tm)
    health, calib, gate = model_health(feats, tm, rules, prior)
    out: dict[str, list[dict[str, Any]]] = {"model_health": health, "calibration": calib}
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    settings = OptSettings()
    out["assumptions"] = [{
        "model_version": MODEL_VERSION, "horizon_gws": horizon, "decay": settings.decay,
        "bench_weight": settings.bench_weight, "ft_value_end": settings.ft_value,
        "hit_hurdle": HIT_HURDLE, "captain_hurdle": CAPTAIN_HURDLE, "chip_decay": CHIP_DECAY,
        "gate": "passed" if gate.passed else "failed: " + "; ".join(gate.reasons),
        "eo_source": (f"top {eo_meta['top_k']:,} overall, {eo_meta['sampled']} sampled, "
                      f"GW{eo_meta['gw']} picks") if eo_meta else "not sampled",
        "generated_utc": now,
    }]
    if not gate.passed:
        return out

    chip_out = _chips(b, fixtures, pm, tm, state, rules, prior, settings) if chips else {}
    fixed = planned_chips(chip_out, state.next_gw)
    plan_horizon = max([horizon] + [g - state.next_gw + 1 for g in fixed])
    out["assumptions"][0].update({
        "horizon_gws": plan_horizon,
        "chips_in_plan": ", ".join(f"{CHIP_LABEL.get(c, c)} GW{g}"
                                   for g, c in sorted(fixed.items())) or "none"})
    res = run(b, fixtures, pm, tm, state, rules, data_dir, horizon=plan_horizon,
              settings=settings, chips=fixed)
    names = res.players["name"].to_dict()
    eo_map = eo.set_index("element")["eo_pct"].to_dict() if eo is not None else {}
    cap_note = apply_captain_hurdle(res, eo_map)
    if cap_note:
        out["assumptions"][0]["captain_note"] = cap_note
    own_map = eo.set_index("element")["owned_pct"].to_dict() if eo is not None else {}
    risk = team_risk(res.forecast, [o.plan.gws[0] for o in res.options], eo_map, rules, names)
    if not eo_map:
        for r in risk:
            r["vs_top10k"] = r["p_beat_top10k"] = None
    note = risk_note(res.options, risk, res.best) if gate.risk_ok and eo_map else None
    out["assumptions"][0]["team_range"] = (
        "one shared score per match" + ("; near-tie note on" if gate.risk_ok else
                                        "; near-tie note off (backtest not better)"))
    if note:
        out["assumptions"][0]["risk_note"] = note
    out.update(_decisions(res, names, eo_map, risk))
    ratings = fixture_ratings(res.forecast.by_fixture)
    out.update(_lineup(res, names, eo_map, own_map, b, fixtures, ratings))
    out["option_lineups"] = _option_lineups(res, names, eo_map, own_map, b, fixtures, ratings)
    out["plan"] = _plan(res, names)
    out["players"] = _players(res, state, eo_map, b, horizon)
    out["fixtures_strength"] = _fixtures(res, b, ratings)
    if squad_rows is not None:
        model = squad_model(res, state, eo_map, names, ratings)
        for row in squad_rows:
            row.update(model.get(row["element"], {}))
    out.update(chip_out)
    now = [{"chip": r["chip"], "gw": r["best_gw"], "gain": r["gain"]}
           for r in out.get("chip_plan", []) if r.get("now") == "Yes"]
    ledger.save(ledger.make_entry(res, eo_map, names, chips_now=now), data_dir / "ledger")
    return out


def _fmt(ids, names) -> str:
    return ", ".join(names[i] for i in ids) if ids else ""


def _decisions(res: EngineResult, names: dict, eo: dict,
               risk: list[dict] | None = None) -> dict:
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
        if risk:
            k = risk[i]
            rows[-1].update({
                "p10": round(k["p10"], 1), "p90": round(k["p90"], 1),
                "p10_independent": round(k["p10_independent"], 1),
                "p90_independent": round(k["p90_independent"], 1),
                "vs_top10k": None if k["vs_top10k"] is None else round(k["vs_top10k"], 2),
                "p_beat_top10k": (None if k["p_beat_top10k"] is None
                                  else round(k["p_beat_top10k"], 3)),
                "pairings": k["pairings"],
            })
    return {"decision": rows}


def fixture_ratings(by_fixture: pd.DataFrame) -> pd.DataFrame:
    """Our own fixture difficulty, 1 (easiest) to 5 (hardest), for each team fixture in the
    horizon: quintiles of team expected goals for (``attack_rating``, for MID/FWD) and of
    clean-sheet chance (``defence_rating``, for GKP/DEF) across every team fixture."""
    fx = by_fixture.drop_duplicates(["team", "fixture"])[
        ["team", "fixture", "event", "opponent_team", "was_home", "lambda_for", "p_cs"]].copy()
    for col, out in (("lambda_for", "attack_rating"), ("p_cs", "defence_rating")):
        pct = fx[col].rank(pct=True, method="average")
        fx[out] = ((1 - pct) * 5).apply(lambda v: int(min(5, max(1, -(-v // 1)))))
    return fx.reset_index(drop=True)


def _rating_for(position: str) -> str:
    return "defence_rating" if position in ("GKP", "DEF") else "attack_rating"


def _diffs(ratings: pd.DataFrame, team: int, gw: int, position: str) -> str:
    r = ratings[(ratings["team"] == team) & (ratings["event"] == gw)].sort_values("fixture")
    return "+".join(str(v) for v in r[_rating_for(position)])


def _gw_roles(g) -> dict[int, str]:
    roles = {p: ("Captain" if p == g.captain else "Vice-captain" if p == g.vice else "Starting")
             for p in g.lineup}
    roles.update({p: f"Bench {i + 1}" for i, p in enumerate(g.bench)})
    return roles


def _sale_pairs(g, pos: dict[int, str]) -> dict[int, int]:
    """Pair each sale with the purchase of the same position (squad shape forces that)."""
    buys = list(g.buys)
    pairs = {}
    for s in g.sells:
        b = next((x for x in buys if pos.get(x) == pos.get(s)), None)
        if b is not None:
            buys.remove(b)
            pairs[s] = b
    return pairs


def squad_model(res: EngineResult, state: ManagerState, eo: dict, names: dict,
                ratings: pd.DataFrame) -> dict[int, dict[str, Any]]:
    """Model fields per owned player: next-GW xPts, start chance, top-10k EO, our fixture
    ratings (next GW and the next five, aligned with ``next_fixtures``) and the engine's role
    (``rec_role``: Captain / Vice-captain / Starting / Bench n / Sell)."""
    g = res.best.gws[0]
    fc = res.forecast.by_gw[res.forecast.by_gw["event"] == g.gw].set_index("element")
    pos = res.players["position"].to_dict()
    team = res.players["team"].to_dict()
    roles, pairs = _gw_roles(g), _sale_pairs(g, pos)
    span = range(state.next_gw, min(state.next_gw + NEXT_FIXTURES, 39))
    out = {}
    for p in state.squad:
        e = p.element
        diffs = [_diffs(ratings, team[e], gw, pos[e]) for gw in span]
        out[e] = {
            "xpts_next": round(float(fc.at[e, "xpts"]), 2) if e in fc.index else 0.0,
            "p_start": round(float(fc.at[e, "p_start"]), 2) if e in fc.index else 0.0,
            "eo_top10k": round(eo.get(e, 0.0), 1),
            "next_diff": diffs[0] if diffs else "",
            "next5_diff": " · ".join(d or "—" for d in diffs),
            "rec_role": roles.get(e, "Sell"),
            "rec_swap": names[pairs[e]] if e in pairs else "",
        }
    return out


def _xi_rows(g, res: EngineResult, names: dict, eo: dict, own: dict, b: Bootstrap,
             fixtures: list[Fixture], ratings: pd.DataFrame) -> list[dict]:
    """The 15 rows (XI in pick order, then the bench in auto-sub order) of one plan's first
    gameweek, with the fields a dashboard player card reads."""
    fc = res.forecast.by_gw[res.forecast.by_gw["event"] == g.gw].set_index("element")
    pos = res.players["position"].to_dict()
    team = res.players["team"].to_dict()
    els = b.elements_by_id()
    roles, pairs = _gw_roles(g), _sale_pairs(g, pos)
    replaces = {v: k for k, v in pairs.items()}
    rows = []
    for p in g.lineup + g.bench:
        role = roles[p]
        rows.append({"element": p, "player": names[p], "pos": pos[p], "role": role,
                     "xpts": round(float(fc.at[p, "xpts"]), 2) if p in fc.index else 0.0,
                     "p_start": round(float(fc.at[p, "p_start"]), 2) if p in fc.index else 0.0,
                     "eo_top10k": round(eo.get(p, 0.0), 1),
                     "owned_top10k": round(own.get(p, 0.0), 1),
                     **card_fields(els[p], b, fixtures, g.gw),
                     "next_diff": _diffs(ratings, team[p], g.gw, pos[p]),
                     "armband": role if role in ("Captain", "Vice-captain") else "",
                     "is_new": "Yes" if p in g.buys else "",
                     "replaces": names[replaces[p]] if p in replaces else ""})
    return rows


def _option_lineups(res: EngineResult, names: dict, eo: dict, own: dict, b: Bootstrap,
                    fixtures: list[Fixture], ratings: pd.DataFrame) -> list[dict]:
    """Every transfer option's own line-up for the coming gameweek; ``option`` matches the
    ``decision`` rows (1-based, in ``res.options`` order)."""
    rows = []
    for i, o in enumerate(res.options):
        for r in _xi_rows(o.plan.gws[0], res, names, eo, own, b, fixtures, ratings):
            rows.append({"option": i + 1, "option_label": o.label,
                         "recommended": "Yes" if o.plan is res.best else "", **r})
    return rows


def _lineup(res: EngineResult, names: dict, eo: dict, own: dict, b: Bootstrap,
            fixtures: list[Fixture], ratings: pd.DataFrame) -> dict:
    g = res.best.gws[0]
    rows = _xi_rows(g, res, names, eo, own, b, fixtures, ratings)
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


def _players(res: EngineResult, state: ManagerState, eo: dict, b: Bootstrap,
             horizon: int = 6) -> list[dict]:
    gws = res.gws[:horizon]  # the dashboard shows the next `horizon` GWs ("Next 6")
    xp = xpts_matrix(res.forecast)[gws]
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


def _fixtures(res: EngineResult, b: Bootstrap, ratings: pd.DataFrame) -> list[dict]:
    fx = res.forecast.by_fixture.drop_duplicates(["team", "fixture"]).merge(
        ratings[["team", "fixture", "attack_rating", "defence_rating"]], on=["team", "fixture"])
    short = {t.id: t.short_name for t in b.teams}
    return [{"team": short[r.team], "gw": int(r.event),
             "opponent": f"{short[r.opponent_team]} ({'H' if r.was_home else 'A'})",
             "xg_for": round(r.lambda_for, 2), "xg_against": round(r.lambda_against, 2),
             "p_clean_sheet": round(r.p_cs, 3), "attack_rating": int(r.attack_rating),
             "defence_rating": int(r.defence_rating)}
            for r in fx.sort_values(["team", "event", "fixture"]).itertuples()]


CHIP_TAIL_GWS = 4  # plan this many GWs past the chips' deadline (squad value after it)
PLAN_CHIP_WINDOW = 8  # Bench Boost / Triple Captain planned within this many GWs are fixed
#                       into the transfer plan (its horizon stretches to reach them)
PLANNED_CHIPS = {"Bench Boost": "bboost", "Triple Captain": "3xc"}


def planned_chips(chip_out: dict[str, list[dict]], next_gw: int) -> dict[int, str]:
    """{gw: chip} for the chip plan's Bench Boost and Triple Captain weeks that fall within
    PLAN_CHIP_WINDOW gameweeks. The Wildcard is not fixed: it stays a separate "play now"
    signal (a planned Wildcard would change every option's squad)."""
    out = {}
    for r in chip_out.get("chip_plan", []):
        c = PLANNED_CHIPS.get(r["chip"])
        if c and next_gw <= int(r["best_gw"]) < next_gw + PLAN_CHIP_WINDOW:
            out[int(r["best_gw"])] = c
    return out


def _chips(b, fixtures, pm, tm, state, rules, prior, settings) -> dict[str, list[dict]]:
    """Joint timing of the remaining first-half chips (Wildcard, Bench Boost, Triple
    Captain), planned to GW19 + CHIP_TAIL_GWS so a late Wildcard keeps its value."""
    first_half = [w for w in state.chips_available if w.stop_event <= 19]
    if not first_half:
        return {}
    end = min(38, max(w.stop_event for w in first_half) + CHIP_TAIL_GWS)
    fc = forecast(b, fixtures, pm, tm, rules, prior, horizon=end - state.next_gw + 1,
                  start_gw=state.next_gw)
    from fpl.engine import players_frame
    players = players_frame(b, state, rules)
    xp = xpts_matrix(fc)
    pool = candidate_pool(players, xp, {"GKP": 6, "DEF": 20, "MID": 20, "FWD": 12})
    from fpl.engine import p_start_matrix
    keep = xp.index.intersection(pool)
    inp = OptInput(players=players.loc[pool], xp=xp.loc[keep],
                   gws=fc.gws, bank=state.bank, free_transfers=state.free_transfers,
                   rules=rules, settings=replace(settings, mip_gap=0.005),
                   p_start=p_start_matrix(fc).reindex(keep))
    res = plan_joint(inp, first_half)
    gain = [{"chip": CHIP_LABEL.get(r["chip"], r["chip"]), "gw": int(r["gw"]),
             "gain": round(float(r["gain"]), 2), "gw_xpts": round(float(r["gw_xpts"]), 1)}
            for r in res.by_gw]
    plan_rows = []
    for chip, gw in sorted(res.assignment.items(), key=lambda kv: kv[1]):
        rows = [r for r in res.by_gw if r["chip"] == chip]
        best = next(r for r in rows if r["gw"] == gw)
        alt = max((r for r in rows if r["gw"] != gw), key=lambda r: r["gain"], default=None)
        plan_rows.append({
            "chip": CHIP_LABEL.get(chip, chip), "best_gw": gw,
            "gain": round(best["gain"], 2),
            "next_best_gw": alt["gw"] if alt else None,
            "cost_of_next_best": round(alt["gain"] - best["gain"], 2) if alt else None,
            "now": "Yes" if gw == fc.gws[0] else "",
        })
    summary = [{"chips_value": round(res.chips_value, 2),
                "horizon": f"GW{fc.gws[0]}–{fc.gws[-1]}",
                "runner_up": ", ".join(f"{CHIP_LABEL.get(c, c)} GW{g}" for c, g in
                                       sorted(res.runner_up[0].items(), key=lambda kv: kv[1]))
                if res.runner_up else "",
                "runner_up_cost": round(res.runner_up[1] - res.best.objective, 2)
                if res.runner_up else None,
                "solves": res.solves}]
    return {"chip_gain": gain, "chip_plan": plan_rows, "chip_summary": summary}
