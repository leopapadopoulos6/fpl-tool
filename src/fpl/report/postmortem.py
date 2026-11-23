"""Post-gameweek review: every decision for a finished gameweek, re-graded on what happened.

Inputs:
- the forward-test ledger entry saved before the deadline (``report/ledger.make_entry``):
  every player's predicted xPts and start chance, our options and line-up;
- the gameweek's live data (``event/{gw}/live``) for actual points and stats;
- the manager's picks for the gameweek (``entry/{id}/event/{gw}/picks``: XI, captain,
  multipliers, chip, automatic subs, points on bench) and transfers.

Outputs, per gameweek:
- ``summary``: score vs average, best possible from your own 15, points left on the table,
  forecast error vs luck for your XI;
- ``decisions``: transfers, captain, starting XI, bench, chip — yours against our
  recommendation and the alternatives, in actual points;
- ``players``: predicted vs actual for every relevant player, with the miss split into
  forecast error and luck.

"Underlying points" replace a player's goals and assists with his xG and xA (at the same
scoring values), so ``actual - predicted = (underlying - predicted) + (actual - underlying)``:
the first part is what the forecast got wrong (minutes, role, chances), the second is
finishing and assist variance.

Old ledger entries (before per-option line-ups were stored) still review: an option's team
is rebuilt from the pre-deadline squad and the stored predictions with ``best_xi``.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from typing import Any

from fpl.rules import GameRules

POS = ("GKP", "DEF", "MID", "FWD")
TRANSFER_WINDOW = 5    # gameweeks over which a transfer's running total is kept
WILDCARD_LAST_GW = 19  # set-1 Wildcard: graded as a running total up to its window's end
AS_EXPECTED = 2.0      # |actual - predicted| below this is "as expected"
CHIP_LABELS = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost",
               "3xc": "Triple Captain"}


# --- line-ups -----------------------------------------------------------------------------
def formations(rules: GameRules) -> list[dict[str, int]]:
    """Every legal XI shape from the rules (min/max per position, 11 players)."""
    out = []
    for d, m, f in itertools.product(*(range(rules.min_play[p], rules.max_play[p] + 1)
                                       for p in ("DEF", "MID", "FWD"))):
        if d + m + f == rules.squad_play - 1:
            out.append({"GKP": 1, "DEF": d, "MID": m, "FWD": f})
    return out


def best_xi(values: dict[int, float], squad: Iterable[int], positions: dict[int, str],
            rules: GameRules, captain_mult: int = 2) -> dict[str, Any]:
    """The formation-legal XI from ``squad`` with the highest total of ``values``, the
    captain counted ``captain_mult`` times. Works for predictions (the model's XI) and for
    actual points (the best possible XI in hindsight)."""
    squad = list(squad)
    by_pos = {p: sorted((e for e in squad if positions.get(e) == p),
                        key=lambda e: -values.get(e, 0.0)) for p in POS}
    best: dict[str, Any] | None = None
    for shape in formations(rules):
        if any(len(by_pos[p]) < n for p, n in shape.items()):
            continue
        xi = [e for p in POS for e in by_pos[p][:shape[p]]]
        cap = max(xi, key=lambda e: values.get(e, 0.0))
        total = sum(values.get(e, 0.0) for e in xi) + (captain_mult - 1) * values.get(cap, 0.0)
        if best is None or total > best["total"] + 1e-9:
            best = {"xi": xi, "captain": cap, "total": total}
    if best is None:
        return {"xi": [], "captain": None, "total": 0.0, "bench": squad}
    rest = [e for e in squad if e not in best["xi"]]
    gk = [e for e in rest if positions.get(e) == "GKP"]
    best["bench"] = gk + sorted((e for e in rest if e not in gk),
                                key=lambda e: -values.get(e, 0.0))
    return best


def legal(xi: list[int], positions: dict[int, str], rules: GameRules) -> bool:
    n = {p: sum(positions.get(e) == p for e in xi) for p in POS}
    return n["GKP"] == 1 and all(rules.min_play[p] <= n[p] <= rules.max_play[p]
                                 for p in ("DEF", "MID", "FWD"))


def autosub(xi: list[int], bench: list[int], minutes: dict[int, int],
            positions: dict[int, str], rules: GameRules) -> list[int]:
    """FPL automatic substitutions: each starter with no minutes is replaced by the first
    bench player (in order) who played and keeps the formation legal."""
    team, used = list(xi), set()
    for e in xi:
        if minutes.get(e, 0) > 0:
            continue
        for b in bench:
            if b in used or minutes.get(b, 0) == 0:
                continue
            trial = [b if x == e else x for x in team]
            if legal(trial, positions, rules):
                team = trial
                used.add(b)
                break
    return team


def team_points(xi: list[int], captain: int | None, vice: int | None,
                points: dict[int, int], minutes: dict[int, int], mult: int = 2) -> int:
    """XI points with the armband (vice takes it if the captain did not play)."""
    cap = captain if captain in xi and minutes.get(captain, 0) > 0 else (
        vice if vice in xi and minutes.get(vice, 0) > 0 else None)
    return int(sum(points.get(e, 0) for e in xi) + (mult - 1) * points.get(cap, 0))


# --- per-player -------------------------------------------------------------------------
def live_table(live: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out = {}
    for e in live.get("elements", []):
        s = e["stats"]
        out[int(e["id"])] = {
            "points": int(s.get("total_points", 0)), "minutes": int(s.get("minutes", 0)),
            "goals": int(s.get("goals_scored", 0)), "assists": int(s.get("assists", 0)),
            "clean_sheets": int(s.get("clean_sheets", 0)), "bonus": int(s.get("bonus", 0)),
            "defcon_count": int(s.get("defensive_contribution", 0)),
            "xg": float(s.get("expected_goals") or 0), "xa": float(s.get("expected_assists") or 0),
        }
    return out


def underlying_points(row: dict[str, Any], position: str, rules: GameRules) -> float:
    """Actual points with goals and assists replaced by xG and xA at the same values."""
    g, a = rules.points("goals_scored", position), rules.points("assists", position)
    return row["points"] + g * (row["xg"] - row["goals"]) + a * (row["xa"] - row["assists"])


def miss_label(pred: float, p_start: float, row: dict[str, Any], forecast_err: float,
               luck: float) -> str:
    if (p_start >= 0.6 and row["minutes"] == 0) or (p_start < 0.4 and row["minutes"] >= 60):
        return "minutes"
    if abs(row["points"] - pred) < AS_EXPECTED:
        return "as expected"
    return "finishing luck" if abs(luck) >= abs(forecast_err) else "forecast"


# --- the review ---------------------------------------------------------------------------
def _ids(xs: Iterable[Any]) -> list[int]:
    return [int(x) for x in xs]


def pre_deadline_squad(entry: dict[str, Any]) -> list[int]:
    if entry.get("squad_before"):
        return _ids(entry["squad_before"])
    rec = entry["recommended"]
    squad = set(_ids(entry["lineup"]) + _ids(entry["bench"]))
    return sorted((squad - set(_ids(rec["in"]))) | set(_ids(rec["out"])))


def option_team(entry: dict[str, Any], opt: dict[str, Any], pred: dict[int, float],
                positions: dict[int, str], rules: GameRules) -> dict[str, Any]:
    """The XI/captain/bench we'd have fielded with this option: stored if the entry has it,
    otherwise rebuilt from the predictions."""
    if opt.get("lineup"):
        return {"xi": _ids(opt["lineup"]), "bench": _ids(opt["bench"]),
                "captain": int(opt["captain"]), "vice": int(opt["vice"])}
    squad = (set(pre_deadline_squad(entry)) - set(_ids(opt["out"]))) | set(_ids(opt["in"]))
    t = best_xi(pred, squad, positions, rules)
    xi = t["xi"]
    if entry.get("captain") is not None and int(entry["captain"]) in xi:
        t["captain"] = int(entry["captain"])
    vice = max((e for e in xi if e != t["captain"]), key=lambda e: pred.get(e, 0.0), default=None)
    return {"xi": xi, "bench": t["bench"], "captain": t["captain"], "vice": vice}


def review_gw(entry: dict[str, Any], live: dict[str, Any], picks: dict[str, Any],
              transfers: list[tuple[int, int]], positions: dict[int, str],
              names: dict[int, str], teams: dict[int, str], rules: GameRules,
              average_points: int | None = None, status: str = "final",
              later: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """``picks``: the raw picks payload; ``transfers``: (in, out) pairs made for this GW;
    ``later``: {gw: {"live": ..., "entry": ledger entry or None}} for gameweeks after this one
    that are already finished (running totals for transfers and the Wildcard)."""
    gw = int(entry["gw"])
    act = live_table(live)
    pts = {e: r["points"] for e, r in act.items()}
    mins = {e: r["minutes"] for e, r in act.items()}
    pred = {int(p["element"]): float(p["xpts"]) for p in entry["predictions"]}
    pstart = {int(p["element"]): float(p["p_start"]) for p in entry["predictions"]}
    name = lambda e: names.get(e, str(e))  # noqa: E731

    # your gameweek, from the picks payload
    pk = sorted(picks["picks"], key=lambda p: p["position"])
    your_squad = [int(p["element"]) for p in pk]
    your_xi0 = [int(p["element"]) for p in pk if p["position"] <= rules.squad_play]
    your_bench = [int(p["element"]) for p in pk if p["position"] > rules.squad_play]
    your_cap = next((int(p["element"]) for p in pk if p["is_captain"]), None)
    your_vice = next((int(p["element"]) for p in pk if p["is_vice_captain"]), None)
    chip = picks.get("active_chip")
    hist = picks.get("entry_history", {})
    your_points = hist.get("points")
    hits_cost = int(hist.get("event_transfers_cost", 0) or 0)
    subs = [(int(s["element_in"]), int(s["element_out"])) for s in picks.get("automatic_subs", [])]
    your_xi = list(your_xi0)
    for i, o in subs:
        your_xi = [i if x == o else x for x in your_xi]
    if chip == "bboost":
        your_xi = your_xi + [e for e in your_bench if e not in your_xi]
    cap_mult = 3 if chip == "3xc" else 2
    calc_points = team_points(your_xi, your_cap, your_vice, pts, mins, cap_mult)
    if your_points is None:
        your_points = calc_points

    # best possible from your own 15 (hindsight), with your chip's captain multiplier
    best = best_xi({e: float(pts.get(e, 0)) for e in your_squad}, your_squad, positions, rules,
                   captain_mult=cap_mult)
    best_total = int(round(best["total"]))
    if chip == "bboost":
        best_total = int(sum(pts.get(e, 0) for e in your_squad)
                         + (cap_mult - 1) * max(pts.get(e, 0) for e in your_squad))
    best_cap_in_xi = max(your_xi, key=lambda e: pts.get(e, 0)) if your_xi else None

    # the model's XI for your actual 15 (stored predictions), with FPL autosubs
    m = best_xi(pred, your_squad, positions, rules)
    if entry.get("captain") is not None and int(entry["captain"]) in m["xi"]:
        m["captain"] = int(entry["captain"])  # the engine's armband (after the captain hurdle)
    m_xi = autosub(m["xi"], m["bench"], mins, positions, rules)
    m_vice = max((e for e in m["xi"] if e != m["captain"]), key=lambda e: pred.get(e, 0.0),
                 default=None)
    model_xi_points = team_points(m_xi, m["captain"], m_vice, pts, mins, cap_mult)
    if chip == "bboost":
        model_xi_points = int(sum(pts.get(e, 0) for e in your_squad)
                              + (cap_mult - 1) * pts.get(m["captain"], 0))

    # options: what each would have scored with the model's XI for that squad
    opts = list(entry.get("options") or [])
    rec = entry["recommended"]
    rows: list[dict[str, Any]] = []

    def row(decision: str, option: str, detail: str, points: float | None,
            verdict: str = "", running: float | None = None) -> None:
        rows.append({"gw": gw, "decision": decision, "option": option, "detail": detail,
                     "points": None if points is None else round(float(points), 1),
                     "running": None if running is None else round(float(running), 1),
                     "verdict": verdict})

    def opt_points(opt: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        t = option_team(entry, opt, pred, positions, rules)
        xi = autosub(t["xi"], t["bench"], mins, positions, rules)
        return team_points(xi, t["captain"], t.get("vice"), pts, mins) - 4 * int(opt["hits"]), t

    def moves(ins: list[int], outs: list[int]) -> str:
        return (", ".join(f"{name(o)} → {name(i)}" for i, o in zip(ins, outs, strict=False))
                or "No transfers")

    def running(ins: list[int], outs: list[int], start: int) -> float | None:
        if not later:
            return None
        tot, seen = 0.0, False
        for g in sorted(later):
            if start < g < start + TRANSFER_WINDOW:
                lp = {e: r["points"] for e, r in live_table(later[g]["live"]).items()}
                tot += sum(lp.get(i, 0) for i in ins) - sum(lp.get(o, 0) for o in outs)
                seen = True
        first = sum(pts.get(i, 0) for i in ins) - sum(pts.get(o, 0) for o in outs)
        return first + tot if seen else None

    yours_in = [i for i, _ in transfers]
    yours_out = [o for _, o in transfers]
    roll = next((o for o in opts if not o["in"]), {"in": [], "out": [], "hits": 0})
    roll_pts, _ = opt_points(roll)
    transfer_label = "Wildcard" if chip == "wildcard" else "Free Hit" if chip == "freehit" else ""
    your_net = your_points - hits_cost
    row("Transfers", "Yours", (f"{transfer_label}: " if transfer_label else "")
        + moves(yours_in, yours_out) + (f" (−{hits_cost} hits)" if hits_cost else ""),
        your_net, "", None if transfer_label else running(yours_in, yours_out, gw))
    for k, o in enumerate(opts, start=1):
        p, _ = opt_points(o)
        is_rec = sorted(_ids(o["in"])) == sorted(_ids(rec["in"])) and \
            sorted(_ids(o["out"])) == sorted(_ids(rec["out"]))
        label = ("Recommended" if is_rec else "Roll" if not o["in"] else f"Option {k}")
        row("Transfers", label, moves(_ids(o["in"]), _ids(o["out"]))
            + (f" (−{4 * o['hits']})" if o["hits"] else "") + " · model's XI",
            p, "better than yours" if p > your_net else "worse than yours" if p < your_net else
            "same as yours", running(_ids(o["in"]), _ids(o["out"]), gw) if o["in"] else None)

    # captain
    tmpl = entry.get("template_captain")
    eng_cap = entry.get("captain")
    row("Captain", "Yours", name(your_cap) if your_cap else "—", pts.get(your_cap, 0))
    if eng_cap is not None:
        row("Captain", "Engine", name(int(eng_cap)), pts.get(int(eng_cap), 0),
            _cmp(pts.get(int(eng_cap), 0), pts.get(your_cap, 0)))
    if tmpl is not None:
        row("Captain", "Top-10k template", name(int(tmpl)), pts.get(int(tmpl), 0),
            _cmp(pts.get(int(tmpl), 0), pts.get(your_cap, 0)))
    if best_cap_in_xi is not None:
        row("Captain", "Best in your XI", name(best_cap_in_xi), pts.get(best_cap_in_xi, 0),
            _cmp(pts.get(best_cap_in_xi, 0), pts.get(your_cap, 0)))

    # starting XI and bench
    row("Starting XI", "Yours", f"{len(your_xi)} players, autosubs: "
        + (", ".join(f"{name(o)} → {name(i)}" for i, o in subs) or "none"), your_points)
    row("Starting XI", "Model's XI for your 15",
        _swap(m["xi"], your_xi0, name) + f", captain {name(m['captain'])}",
        model_xi_points, _cmp(model_xi_points, your_points))
    row("Starting XI", "Best possible", "hindsight: " + _swap(best["xi"], your_xi, name)
        + f", captain {name(best['captain'])}", best_total, _cmp(best_total, your_points))
    bench_pts = int(hist.get("points_on_bench", 0) or 0)
    row("Bench", "Points on bench", ", ".join(f"{name(e)} {pts.get(e, 0)}" for e in your_bench),
        bench_pts)

    # chip
    if chip in ("3xc", "bboost", "wildcard", "freehit"):
        if chip == "3xc":
            row("Chip", "Triple Captain", f"on {name(your_cap)}", pts.get(your_cap, 0),
                "extra points from the third armband")
        elif chip == "bboost":
            row("Chip", "Bench Boost", "bench points counted",
                sum(pts.get(e, 0) for e in your_bench))
        else:
            pre = pre_deadline_squad(entry)
            t = best_xi(pred, pre, positions, rules)
            pre_xi = autosub(t["xi"], t["bench"], mins, positions, rules)
            pre_pts = team_points(pre_xi, t["captain"], None, pts, mins)
            run_tot = None
            if chip == "wildcard" and later:
                run_tot = your_points - pre_pts
                for g in sorted(later):
                    if g <= WILDCARD_LAST_GW and later[g].get("your_points") is not None:
                        lv = later[g]["live"]
                        lp = {e: r["points"] for e, r in live_table(lv).items()}
                        lm = {e: r["minutes"] for e, r in live_table(lv).items()}
                        lpred = ({int(p["element"]): float(p["xpts"])
                                  for p in later[g]["entry"]["predictions"]}
                                 if later[g].get("entry") else {e: float(v) for e, v in lp.items()})
                        tt = best_xi(lpred, pre, positions, rules)
                        xi2 = autosub(tt["xi"], tt["bench"], lm, positions, rules)
                        run_tot += later[g]["your_points"] - team_points(
                            xi2, tt["captain"], None, lp, lm)
            label = "Wildcard" if chip == "wildcard" else "Free Hit"
            row("Chip", label, "your team vs your pre-deadline 15 (model's XI, no transfers)",
                your_points - pre_pts, "gain" if your_points > pre_pts else "loss", run_tot)
    for c in entry.get("chips_now") or []:
        if c.get("chip") == CHIP_LABELS.get(chip or ""):
            row("Chip", f"Planner: play {c['chip']} now", f"forecast gain {c.get('gain')}",
                None, "followed")
        else:
            row("Chip", f"Planner: play {c.get('chip')} now",
                f"not played; forecast gain {c.get('gain')}", None, "not followed")

    # players
    rec_in, rec_out = set(_ids(rec["in"])), set(_ids(rec["out"]))
    involved = (set(your_squad) | rec_in | rec_out | set(yours_out)
                | {e for e in (eng_cap, tmpl) if e is not None})
    players = []
    luck_xi = forecast_xi = 0.0
    order = sorted(involved, key=lambda x: (x not in your_xi, x not in your_squad, -pts.get(x, 0)))
    for e in order:
        e = int(e)
        r = act.get(e)
        if r is None:
            continue
        pos = positions.get(e, "MID")
        p, ps = pred.get(e, 0.0), pstart.get(e, 0.0)
        und = underlying_points(r, pos, rules)
        fe, lk = und - p, r["points"] - und
        mult = (cap_mult if e == your_cap else 1) if e in your_xi else 0
        luck_xi += mult * lk
        forecast_xi += mult * fe
        role = ("Captain" if e == your_cap and e in your_xi else "XI" if e in your_xi
                else "Bench" if e in your_squad else "Sold" if e in yours_out
                else "Recommended, not bought" if e in rec_in else
                "Recommended to sell" if e in rec_out else "Other")
        players.append({"gw": gw, "element": e, "player": name(e), "team": teams.get(e, ""),
                        "pos": pos, "role": role, "pred_xpts": round(p, 2), "p_start": round(ps, 2),
                        "points": r["points"], "minutes": r["minutes"], "goals": r["goals"],
                        "assists": r["assists"], "clean_sheet": r["clean_sheets"],
                        "bonus": r["bonus"], "defcon_count": r["defcon_count"],
                        "xg": round(r["xg"], 2), "xa": round(r["xa"], 2),
                        "underlying": round(und, 2), "forecast_error": round(fe, 2),
                        "luck": round(lk, 2), "miss": round(r["points"] - p, 2),
                        "label": miss_label(p, ps, r, fe, lk)})

    pred_xi = sum(pred.get(e, 0.0) for e in your_xi) + (cap_mult - 1) * pred.get(your_cap, 0.0)
    summary = {
        "gw": gw, "status": status, "your_points": your_points, "hits": hits_cost,
        "average_points": average_points, "chip": chip or "",
        "best_possible": best_total, "left_on_table": best_total - your_points,
        "captain": name(your_cap) if your_cap else "", "captain_points": pts.get(your_cap, 0),
        "best_captain": name(best_cap_in_xi) if best_cap_in_xi else "",
        "best_captain_points": pts.get(best_cap_in_xi, 0) if best_cap_in_xi else 0,
        "captain_gap": (cap_mult - 1) * (pts.get(best_cap_in_xi, 0) - pts.get(your_cap, 0))
        if best_cap_in_xi else 0,
        "bench_points": bench_pts,
        "model_xi_points": model_xi_points, "roll_points": roll_pts,
        "predicted_xi_xpts": round(pred_xi, 1),
        "forecast_error_xi": round(forecast_xi, 1), "luck_xi": round(luck_xi, 1),
        "calc_points_check": calc_points,
    }
    summary["headline"] = headline(summary)
    return {"summary": summary, "decisions": rows, "players": players}


def _swap(xi: list[int], yours: list[int], name) -> str:
    ins = [name(e) for e in xi if e not in yours]
    outs = [name(e) for e in yours if e not in xi]
    return f"starts {', '.join(ins)} instead of {', '.join(outs)}" if ins else "same XI as yours"


def _cmp(a: float, b: float) -> str:
    return "better than yours" if a > b else "worse than yours" if a < b else "same as yours"


def headline(s: dict[str, Any]) -> str:
    avg = f" (average {s['average_points']})" if s.get("average_points") is not None else ""
    luck = s["luck_xi"]
    return (f"GW{s['gw']} {s['status']}: you scored {s['your_points']}{avg}; best possible from "
            f"your 15 was {s['best_possible']} ({s['left_on_table']} left on the table, "
            f"{s['captain_gap']} of it captaincy). Captain {s['captain']} "
            f"{s['captain_points']}, best in XI {s['best_captain']} {s['best_captain_points']}. "
            f"Your XI was forecast {s['predicted_xi_xpts']}: forecast error "
            f"{s['forecast_error_xi']:+.1f} (minutes, clean sheets, bonus, chances), "
            f"finishing luck {luck:+.1f}.")


def lessons(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Running totals across reviewed gameweeks (one ``review_gw`` result each)."""
    if not reviews:
        return {}
    sm = [r["summary"] for r in reviews]
    dec = [d for r in reviews for d in r["decisions"]]
    caps = [d for d in dec if d["decision"] == "Captain"]
    yours = {d["gw"]: d["points"] for d in caps if d["option"] == "Yours"}
    eng = {d["gw"]: d["points"] for d in caps if d["option"] == "Engine"}
    rec = {d["gw"]: d["points"] for d in dec if d["decision"] == "Transfers"
           and d["option"] == "Recommended"}
    you = {d["gw"]: d["points"] for d in dec if d["decision"] == "Transfers"
           and d["option"] == "Yours"}
    return {
        "gws_reviewed": len(sm),
        "points_left_on_table": int(sum(s["left_on_table"] for s in sm)),
        "captaincy_gap": int(sum(s["captain_gap"] for s in sm)),
        "luck_xi": round(sum(s["luck_xi"] for s in sm), 1),
        "forecast_error_xi": round(sum(s["forecast_error_xi"] for s in sm), 1),
        "engine_captain_minus_yours": round(sum(eng[g] - yours[g] for g in eng if g in yours), 1),
        "recommended_minus_yours": round(sum(rec[g] - you[g] for g in rec if g in you), 1),
    }
