"""Team points ranges with one shared score per match, the near-tie note, and the backtest."""

from types import SimpleNamespace

import numpy as np
import pandas as pd

from fpl.backtest.team_risk import coverage
from fpl.engine import Option, pairings, risk_note, team_risk
from tests.test_xpts import _match

NAMES = {1: "Keeper", 2: "Def", 3: "Mid", 4: "OppMid", 5: "OppFwd"}


def _fc(rules, matches: int = 1):
    rows = []
    for m in range(matches):
        r = _match(rules).copy()
        r["element"] += 10 * m
        r["team"] += 2 * m
        r["opponent_team"] += 2 * m
        r["fixture"] = 7 + m
        rows.append(r)
    bf = pd.concat(rows, ignore_index=True)
    bf["event"] = 12
    from fpl.models.xpts import simulate
    bf["xpts"] = simulate(bf, rules, n=40_000).mean(axis=0)
    return SimpleNamespace(by_fixture=bf)


def _gw(lineup, captain, bench=(), chip=None):
    return SimpleNamespace(gw=12, lineup=list(lineup), bench=list(bench), captain=captain,
                           chip=chip)


def test_team_risk_mean_counts_the_captain_twice(rules):
    fc = _fc(rules)
    xp = fc.by_fixture.set_index("element")["xpts"]
    r = team_risk(fc, [_gw([1, 2, 3], 3)], {}, rules, n=20_000)[0]
    assert abs(r["mean"] - (xp[1] + xp[2] + 2 * xp[3])) < 0.15
    tc = team_risk(fc, [_gw([1, 2, 3], 3, chip="3xc")], {}, rules, n=20_000)[0]
    assert abs(tc["mean"] - r["mean"] - xp[3]) < 0.15


def test_facing_players_narrow_the_range_and_stacks_widen_it(rules):
    fc = _fc(rules)
    hedge, stack = team_risk(fc, [_gw([2, 5], 5), _gw([1, 2], 2)], {}, rules, n=40_000)
    assert hedge["p90"] - hedge["p10"] < hedge["p90_independent"] - hedge["p10_independent"]
    assert stack["p90"] - stack["p10"] > stack["p90_independent"] - stack["p10_independent"]


def test_owning_the_top10k_team_is_no_gain_on_it(rules):
    fc = _fc(rules)
    eo = {1: 100.0, 2: 100.0, 3: 200.0}
    r = team_risk(fc, [_gw([1, 2, 3], 3)], eo, rules, n=5_000)[0]
    assert abs(r["vs_top10k"]) < 1e-9 and r["p_beat_top10k"] == 0.0


def test_pairings_name_the_hedges_and_the_stacks(rules):
    fc = _fc(rules)
    text = pairings(fc.by_fixture, [1, 2, 5], NAMES)
    assert "Facing each other: Keeper, Def v OppFwd" in text
    assert "Sharing a clean sheet: Keeper + Def" in text
    assert pairings(fc.by_fixture, [3], NAMES) == ""


def test_risk_note_only_for_near_ties_with_a_better_chance():
    plan_a, plan_b, plan_c = object(), object(), object()
    opts = [Option("Roll", [], [], 0, plan_a, 0.0), Option("1 transfer", [1], [2], 0, plan_b, 0.6),
            Option("2 transfers, -4", [1, 3], [2, 4], 1, plan_c, 1.2)]
    risk = [{"p_beat_top10k": p} for p in (0.48, 0.40, 0.70)]
    # recommended = 1 transfer (0.6); roll is 0.6 behind (a near tie) and beats more often;
    # the hit option scores 1.2 - 1.0 = 0.2 after the hurdle, also within 1.0, and is best
    note = risk_note(opts, risk, plan_b)
    assert note and "'2 transfers, -4'" in note and "70%" in note
    assert risk_note(opts, [{"p_beat_top10k": p} for p in (0.3, 0.5, 0.3)], plan_b) is None


def test_coverage_backtest_reports_both_methods(rules):
    rng = np.random.default_rng(1)
    rounds = []
    for g in (4, 5):
        d = _fc(rules, matches=3).by_fixture.copy()
        d["round"] = g
        d["actual"] = rng.poisson(d["xpts"].clip(lower=0.1))
        rounds.append(d)
    out = coverage(pd.concat(rounds, ignore_index=True), rules, teams_per_round=40, n=500)
    assert out["teams"] == 80 and out["rounds"] == "GW4–5"
    for k in ("joint", "independent"):
        assert 0 <= out[f"coverage_{k}"] <= 1 and out[f"crps_{k}"] > 0
    assert isinstance(out["joint_better"], bool)
