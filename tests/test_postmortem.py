"""Post-gameweek review on hand-built gameweeks (toy squads, live data and picks)."""

import pytest

from fpl.report.postmortem import (
    autosub,
    best_xi,
    formations,
    legal,
    lessons,
    review_gw,
    underlying_points,
)

# 15-man toy squad: 1-2 GKP, 3-7 DEF, 8-12 MID, 13-15 FWD
POSITIONS = {**{e: "GKP" for e in (1, 2)}, **{e: "DEF" for e in range(3, 8)},
             **{e: "MID" for e in range(8, 13)}, **{e: "FWD" for e in (13, 14, 15)},
             20: "MID", 21: "MID"}
NAMES = {e: f"p{e}" for e in POSITIONS}
TEAMS = {e: "AAA" for e in POSITIONS}
SQUAD = list(range(1, 16))


def live(points: dict[int, int], minutes: dict[int, int] | None = None,
         extra: dict[int, dict] | None = None) -> dict:
    els = []
    for e in POSITIONS:
        st = {"total_points": points.get(e, 0), "minutes": (minutes or {}).get(e, 90),
              "goals_scored": 0, "assists": 0, "clean_sheets": 0, "bonus": 0,
              "defensive_contribution": 0, "expected_goals": "0.00", "expected_assists": "0.00"}
        st.update((extra or {}).get(e, {}))
        els.append({"id": e, "stats": st})
    return {"elements": els}


def picks(xi: list[int], bench: list[int], captain: int, vice: int, chip=None, subs=(),
          points=None, bench_points=0, cost=0) -> dict:
    order = xi + bench
    return {"active_chip": chip,
            "automatic_subs": [{"element_in": i, "element_out": o} for i, o in subs],
            "entry_history": {"points": points, "points_on_bench": bench_points,
                              "event_transfers_cost": cost},
            "picks": [{"element": e, "position": k + 1, "multiplier": 1,
                       "is_captain": e == captain, "is_vice_captain": e == vice}
                      for k, e in enumerate(order)]}


def entry(pred: dict[int, float], options=None, rec=None, lineup=None, bench=None,
          captain=13, gw=11) -> dict:
    rec = rec or {"label": "Roll the transfer", "in": [], "out": [], "hits": 0,
                  "delta_vs_roll": 0.0}
    return {"gw": gw, "recommended": rec,
            "options": options or [{"label": "Roll the transfer", "in": [], "out": [],
                                    "hits": 0, "delta_vs_roll": 0.0}],
            "captain": captain, "vice": 14, "template_captain": captain,
            "lineup": lineup or [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15],
            "bench": bench or [2, 6, 7, 12], "names": {},
            "predictions": [{"element": e, "xpts": v, "p_start": 0.9} for e, v in pred.items()]}


XI = [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15]   # 3-4-3
BENCH = [2, 6, 7, 12]


def test_formations_are_the_legal_shapes(rules):
    shapes = {(f["DEF"], f["MID"], f["FWD"]) for f in formations(rules)}
    assert {(3, 4, 3), (4, 4, 2), (5, 4, 1), (3, 5, 2), (4, 5, 1), (5, 3, 2)} <= shapes
    assert all(d + m + f == 10 for d, m, f in shapes) and (2, 5, 3) not in shapes


def test_best_xi_is_legal_and_captains_the_top_scorer(rules):
    vals = {e: 1.0 for e in SQUAD} | {13: 9.0, 14: 8.0, 15: 7.0, 3: 6.0, 2: 0.0}
    t = best_xi(vals, SQUAD, POSITIONS, rules)
    assert legal(t["xi"], POSITIONS, rules) and len(t["xi"]) == 11
    assert t["captain"] == 13 and {13, 14, 15} <= set(t["xi"])
    assert t["total"] == pytest.approx(sum(vals[e] for e in t["xi"]) + 9.0)
    assert t["bench"][0] in (1, 2)  # goalkeeper first on the bench


def test_autosub_keeps_formation(rules):
    # a defender misses in a 3-4-3: the first bench outfielder is a MID, which would leave
    # two defenders, so the next legal sub (a DEF) comes on instead
    xi = autosub(XI, [2, 12, 6, 7], {3: 0} | {e: 90 for e in SQUAD if e != 3},
                 POSITIONS, rules)
    assert 3 not in xi and 6 in xi and 12 not in xi and legal(xi, POSITIONS, rules)


def test_underlying_points_swap_goals_for_xg(rules):
    row = {"points": 9, "goals": 1, "assists": 0, "xg": 0.25, "xa": 0.5}
    # MID goal = 5, assist = 3: 9 - 5 + 5*0.25 + 3*0.5
    assert underlying_points(row, "MID", rules) == pytest.approx(9 - 5 + 1.25 + 1.5)


def _base_review(rules, chip=None, subs=(), mins=None, **kw):
    pred = {e: 3.0 for e in POSITIONS} | {13: 6.0, 14: 5.0}
    pts = {e: 2 for e in SQUAD} | {13: 2, 14: 12, 12: 10}
    pk = picks(XI, BENCH, captain=13, vice=14, chip=chip, subs=subs, **kw)
    return review_gw(entry(pred), live(pts, mins, extra={14: {"goals_scored": 2,
                                                            "expected_goals": "0.20"}}),
                     pk, [], POSITIONS, NAMES, TEAMS, rules, average_points=50), pts


def test_review_splits_miss_into_forecast_and_luck(rules):
    r, pts = _base_review(rules)
    s = r["summary"]
    # your XI: 11 starters, captain p13 (2 pts) doubled
    assert s["calc_points_check"] == sum(pts[e] for e in XI) + 2
    assert s["captain_gap"] == 12 - 2  # p14 scored 12 as vice
    p14 = next(p for p in r["players"] if p["element"] == 14)
    assert p14["forecast_error"] + p14["luck"] == pytest.approx(p14["points"] - p14["pred_xpts"])
    assert p14["label"] == "finishing luck"  # two goals off 0.2 xG: finishing
    # p12 scored 10 on the bench: best possible starts him
    assert s["best_possible"] > s["calc_points_check"]
    caps = {d["option"]: d["points"] for d in r["decisions"] if d["decision"] == "Captain"}
    assert caps["Best in your XI"] == 12 and caps["Yours"] == 2


def test_triple_captain_and_bench_boost(rules):
    tc, pts = _base_review(rules, chip="3xc")
    assert tc["summary"]["calc_points_check"] == sum(pts[e] for e in XI) + 2 * 2
    assert any(d["option"] == "Triple Captain" and d["points"] == 2 for d in tc["decisions"])
    bb, _ = _base_review(rules, chip="bboost")
    assert bb["summary"]["calc_points_check"] == sum(pts[e] for e in SQUAD) + 2
    assert any(d["option"] == "Bench Boost" and d["points"] == sum(pts[e] for e in BENCH)
               for d in bb["decisions"])


def test_vice_takes_the_armband_when_captain_does_not_play(rules):
    mins = {e: 90 for e in POSITIONS} | {13: 0}
    r, pts = _base_review(rules, subs=[(12, 13)], mins=mins)
    # p12 (MID) replaces p13; vice p14 (12 pts) gets the armband
    xi = [12 if e == 13 else e for e in XI]
    assert r["summary"]["calc_points_check"] == sum(pts[e] for e in xi) + 12
    assert next(p for p in r["players"] if p["element"] == 13)["label"] == "minutes"


def test_wildcard_compared_with_pre_deadline_squad_and_running_total(rules):
    # before the deadline you held 1-15; you wildcarded p11, p12 out for p20, p21
    pred = {e: 3.0 for e in POSITIONS}
    new_squad = [e for e in SQUAD if e not in (11, 12)] + [20, 21]
    xi = [1, 3, 4, 5, 8, 9, 10, 20, 13, 14, 15]
    bench = [2, 6, 7, 21]
    pts = {e: 2 for e in POSITIONS} | {20: 10, 11: 1}
    e = entry(pred, lineup=XI, bench=BENCH)
    later = {12: {"live": live({e2: 3 for e2 in POSITIONS} | {20: 8}), "entry": None,
                  "your_points": 50}}
    r = review_gw(e, live(pts), picks(xi, bench, 13, 14, chip="wildcard", points=None),
                  [(20, 11), (21, 12)], POSITIONS, NAMES, TEAMS, rules, later=later)
    wc = next(d for d in r["decisions"] if d["option"] == "Wildcard")
    yours = r["summary"]["calc_points_check"]
    assert sorted(new_squad) == sorted(xi + bench)
    assert wc["points"] > 0 and wc["verdict"] == "gain"
    assert wc["running"] is not None and wc["running"] != wc["points"]
    assert yours == sum(pts[x] for x in xi) + pts[13]


def test_old_entry_rebuilds_option_teams_and_tracks_transfer_running_total(rules):
    pred = {e: 3.0 for e in POSITIONS} | {20: 5.0}
    opt = {"label": "1 transfer", "in": [20], "out": [12], "hits": 0, "delta_vs_roll": 1.0}
    roll = {"label": "Roll the transfer", "in": [], "out": [], "hits": 0, "delta_vs_roll": 0.0}
    e = entry(pred, options=[roll, opt], rec=opt,
              lineup=[1, 3, 4, 5, 8, 9, 10, 20, 13, 14, 15], bench=[2, 6, 7, 11])
    pts = {x: 2 for x in POSITIONS} | {20: 9, 12: 1}
    xi = [1, 3, 4, 5, 8, 9, 10, 20, 13, 14, 15]
    later = {12: {"live": live({x: 2 for x in POSITIONS} | {20: 6, 12: 2}), "entry": None,
                  "your_points": 40},
             13: {"live": live({x: 2 for x in POSITIONS} | {20: 1, 12: 5}), "entry": None,
                  "your_points": 40}}
    r = review_gw(e, live(pts), picks(xi, [2, 6, 7, 11], 13, 14), [(20, 12)], POSITIONS,
                  NAMES, TEAMS, rules, later=later)
    t = {d["option"]: d for d in r["decisions"] if d["decision"] == "Transfers"}
    assert set(t) == {"Yours", "Recommended", "Roll"}
    assert t["Recommended"]["points"] > t["Roll"]["points"]
    # running total: (9-1) + (6-2) + (1-5) = 8
    assert t["Yours"]["running"] == 8 and t["Recommended"]["running"] == 8
    assert lessons([r])["gws_reviewed"] == 1
