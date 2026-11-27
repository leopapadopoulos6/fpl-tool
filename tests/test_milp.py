"""Hand-checkable optimizer cases on a toy universe (one GW unless stated)."""

import pandas as pd
import pytest

from fpl.optimize.milp import OptInput, OptSettings, solve

POS = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3


def toy(extra: list[dict], gws=(1,), bank=0, ft=1, base_xp=2.0, **kw):
    rows = []
    for i, ps in enumerate(POS):
        rows.append(dict(element=i + 1, position=ps, team=i % 8 + 1, now_cost=50,
                         sell_price=50, owned=True, available=True, name=f"own{i+1}"))
    rows += extra
    pl = pd.DataFrame(rows).set_index("element")
    xp = pd.DataFrame({g: [base_xp] * len(pl) for g in gws}, index=pl.index)
    for e in extra:
        for g in gws:
            xp.loc[e["element"], g] = e.get("xp", base_xp)
    return pl, xp


def cand(el, ps="MID", cost=50, xp=10.0, team=9):
    return dict(element=el, position=ps, team=team, now_cost=cost, sell_price=cost,
                owned=False, available=True, name=f"c{el}", xp=xp)


def run(pl, xp, gws=(1,), bank=0, ft=1, rules=None, **kw):
    return solve(OptInput(players=pl, xp=xp, gws=list(gws), bank=bank, free_transfers=ft,
                          rules=rules, settings=OptSettings(ft_value=0.0, decay=1.0), **kw))


@pytest.fixture
def R(rules):
    return rules


def test_free_transfer_used_for_clear_upgrade(R):
    pl, xp = toy([cand(100, xp=10.0)])
    plan = run(pl, xp, rules=R)
    g = plan.gws[0]
    assert g.buys == [100] and len(g.sells) == 1 and g.hits == 0
    assert g.captain == 100


def test_hit_only_when_gain_exceeds_four(R):
    # two upgrades, 1 FT: second upgrade gains 2.5 (< 4) -> only one transfer
    pl, xp = toy([cand(100, xp=10.0), cand(101, xp=4.5, team=10)])
    g = run(pl, xp, rules=R).gws[0]
    assert g.buys == [100] and g.hits == 0
    # make the second upgrade worth 6.5 -> hit taken
    pl, xp = toy([cand(100, xp=10.0), cand(101, xp=8.5, team=10)])
    g = run(pl, xp, rules=R).gws[0]
    assert sorted(g.buys) == [100, 101] and g.hits == 1


def test_budget_blocks_unaffordable(R):
    pl, xp = toy([cand(100, cost=60, xp=10.0)])
    assert run(pl, xp, bank=5, rules=R).gws[0].buys == []
    assert run(pl, xp, bank=10, rules=R).gws[0].buys == [100]


def test_selling_price_not_now_cost_funds_buys(R):
    pl, xp = toy([cand(100, cost=55, xp=10.0)])
    pl.loc[8, "sell_price"] = 55  # owned MID worth 5.5 to sell; others 5.0
    g = run(pl, xp, bank=0, rules=R).gws[0]
    assert g.buys == [100] and g.sells == [8] and g.bank_after == 0


def test_three_per_club(R):
    pl, xp = toy([cand(100, xp=10.0, team=1), cand(101, xp=10.0, team=1, ps="FWD")])
    # owned players 1 and 9 are team 1 already (i % 8 + 1); max 3 -> at most one buy from team 1
    g = run(pl, xp, ft=2, rules=R).gws[0]
    club1 = [p for p in g.lineup + g.bench if pl.at[p, "team"] == 1]
    assert len(club1) <= 3


def test_roll_banks_free_transfer(R):
    # no upgrade in GW1, upgrade worth 9 each in GW2 for two players -> roll, then 2 free
    pl, xp = toy([cand(100, xp=1.0), cand(101, xp=1.0, team=10)], gws=(1, 2))
    xp.loc[[100, 101], 2] = 11.0
    plan = run(pl, xp, gws=(1, 2), rules=R)
    g1, g2 = plan.gws
    assert g1.buys == [] and g2.free_transfers == 2
    assert sorted(g2.buys) == [100, 101] and g2.hits == 0


def test_wildcard_no_hits_and_bench_boost_counts_bench(R):
    pl, xp = toy([cand(100 + i, xp=6.0, team=9 + i % 3) for i in range(4)])
    g = run(pl, xp, ft=1, chips={1: "wildcard"}, rules=R).gws[0]
    assert g.hits == 0 and len(g.buys) >= 3
    pl, xp = toy([])
    g = run(pl, xp, chips={1: "bboost"}, rules=R).gws[0]
    assert g.xpts == pytest.approx(15 * 2.0 + 2.0)  # all 15 + captain bonus


def test_triple_captain(R):
    pl, xp = toy([])
    xp.loc[13, 1] = 9.0
    g = run(pl, xp, chips={1: "3xc"}, rules=R).gws[0]
    assert g.captain == 13 and g.xpts == pytest.approx(10 * 2.0 + 9.0 * 3)


def test_forced_first_gw_count(R):
    pl, xp = toy([cand(100, xp=10.0)])
    assert run(pl, xp, rules=R, first_gw_transfers=0).gws[0].buys == []


def test_hit_hurdle_prefers_free_option():
    from fpl.engine import Option, recommend

    class P:  # minimal plan stand-in
        objective = 0.0
    free = Option("1 transfer", [1], [2], 0, P(), delta_vs_roll=2.09)
    hit = Option("2 transfers, -4", [1, 3], [2, 4], 1, P(), delta_vs_roll=2.16)
    big = Option("2 transfers, -4", [1, 5], [2, 6], 1, P(), delta_vs_roll=3.5)
    assert recommend([free, hit]) is free
    assert recommend([free, hit, big]) is big


def test_captain_hurdle_keeps_template_captain():
    from types import SimpleNamespace

    import pandas as pd

    from fpl.engine import apply_captain_hurdle
    from fpl.optimize.milp import GWPlan

    g = GWPlan(gw=10, chip=None, buys=[], sells=[], hits=0, free_transfers=1,
               lineup=[1, 2, 3], bench=[], captain=2, vice=1, xpts=20.0, bank_after=0)
    by_gw = pd.DataFrame({"element": [1, 2, 3], "event": [10] * 3, "xpts": [5.2, 5.7, 3.0]})
    res = SimpleNamespace(best=SimpleNamespace(gws=[g]), forecast=SimpleNamespace(by_gw=by_gw),
                          captaincy=pd.DataFrame({"element": [2, 1], "mean": [5.7, 5.2]}))
    eo = {1: 186.0, 2: 12.0}
    assert apply_captain_hurdle(res, eo) is not None
    assert (g.captain, g.vice) == (1, 2) and abs(g.xpts - 19.5) < 1e-9
    # a large edge keeps the model's captain
    g2 = GWPlan(10, None, [], [], 0, 1, [1, 2, 3], [], 2, 1, 20.0, 0)
    res.best.gws = [g2]
    res.captaincy = pd.DataFrame({"element": [2, 1], "mean": [7.0, 5.2]})
    assert apply_captain_hurdle(res, eo) is None and g2.captain == 2


def test_joint_chip_search_places_each_chip_once(R):
    # GW3 has one standout captain, so Triple Captain must go there and Bench Boost elsewhere
    pl, xp = toy([], gws=(1, 2, 3))
    xp.loc[13, 3] = 15.0
    plan = run(pl, xp, gws=(1, 2, 3), rules=R,
               chip_search={"bboost": [1, 2, 3], "3xc": [1, 2, 3]})
    chips = {g.gw: g.chip for g in plan.gws if g.chip}
    assert sorted(chips.values()) == ["3xc", "bboost"]
    assert len(chips) == 2  # never two chips in one GW
    assert chips[3] == "3xc"


def test_joint_wildcard_makes_transfers_free(R):
    pl, xp = toy([cand(100 + i, xp=6.0, team=9 + i % 3) for i in range(4)], gws=(1, 2))
    plan = run(pl, xp, gws=(1, 2), rules=R, chip_search={"wildcard": [1, 2]})
    g = next(g for g in plan.gws if g.chip == "wildcard")
    assert g.hits == 0 and len(g.buys) >= 3


def test_chip_forbid_gives_next_best_assignment(R):
    pl, xp = toy([], gws=(1, 2))
    xp.loc[13, 2] = 15.0
    best = run(pl, xp, gws=(1, 2), rules=R, chip_search={"3xc": [1, 2]})
    assert next(g.gw for g in best.gws if g.chip == "3xc") == 2
    nxt = run(pl, xp, gws=(1, 2), rules=R, chip_search={"3xc": [1, 2]},
              chip_forbid=[{"3xc": 2}])
    assert all(g.chip != "3xc" or g.gw == 1 for g in nxt.gws)
    assert nxt.objective < best.objective


def test_chip_timing_is_not_biased_toward_early_gameweeks(R):
    # The same captain is worth 10.0 in GW1 and 10.5 in GW3. The engine's 0.9 weekly decay
    # would make GW1 look better (10.0 vs 10.5 * 0.81 = 8.5), but a chip can be held at no
    # cost, so the planner must compare undiscounted values and pick GW3.
    from fpl.chips.joint import plan_joint
    from fpl.rules import ChipWindow

    pl, xp = toy([], gws=(1, 2, 3))
    xp.loc[13, 1], xp.loc[13, 3] = 10.0, 10.5
    inp = OptInput(players=pl, xp=xp, gws=[1, 2, 3], bank=0, free_transfers=1, rules=R,
                   settings=OptSettings(ft_value=0.0, decay=0.9))
    res = plan_joint(inp, [ChipWindow("3xc", 3, 1, 3)])
    assert res.assignment == {"3xc": 3}
    gains = {r["gw"]: r["gain"] for r in res.by_gw}
    assert gains[3] == pytest.approx(10.5, abs=1e-6)  # gain in undiscounted points
    assert gains[1] == pytest.approx(10.0, abs=1e-6)


def _gk_case(gws=(1,), gk1=(2.0, 0.5), new_gk=(3.5, 0.95)):
    """GK1 (element 1) doubtful, GK2 (element 2) never plays, a cheap playing GK (100) to buy."""
    # outfielders at 4.0 so the captain is never a goalkeeper
    pl, xp = toy([cand(100, ps="GKP", xp=new_gk[0], team=9)], gws=gws, base_xp=4.0)
    for g in gws:
        xp.loc[1, g], xp.loc[2, g] = gk1[0], 0.0
    ps = pd.DataFrame({g: 0.9 for g in gws}, index=xp.index)
    ps.loc[1], ps.loc[2], ps.loc[100] = gk1[1], 0.0, new_gk[1]
    return pl, xp, ps


def test_doubtful_keeper_keeps_cover_and_sells_the_non_playing_backup(R):
    pl, xp, ps = _gk_case()
    g = run(pl, xp, rules=R, p_start=ps).gws[0]
    # start GK1 (2.0) with GK100 covering half the time (0.5 * 3.5) = 3.75 beats starting
    # GK100 (3.5 + 0.05 * 2.0 = 3.6): keep the doubtful starter, sell the backup who never plays
    assert g.buys == [100] and g.sells == [2]
    assert 1 in g.lineup and g.bench[0] == 100


def test_no_cover_value_when_the_starter_always_plays(R):
    pl, xp, ps = _gk_case(gk1=(3.0, 1.0), new_gk=(3.0, 1.0))
    keep = run(pl, xp, rules=R, p_start=ps, first_gw_transfers=0)
    swap = run(pl, xp, rules=R, p_start=ps, first_gw_transfers=1, banned=set(range(3, 16)))
    assert swap.objective == pytest.approx(keep.objective, abs=1e-6)


def test_options_differ_by_sale_not_just_purchase(R):
    from fpl.optimize.milp import alternatives

    pl, xp, ps = _gk_case()
    inp = OptInput(players=pl, xp=xp, gws=[1], bank=0, free_transfers=1, rules=R,
                   settings=OptSettings(ft_value=0.0, decay=1.0), p_start=ps)
    moves = {(tuple(p.gws[0].buys), tuple(p.gws[0].sells))
             for n, p in alternatives(inp, max_first_gw_transfers=1) if n == 1}
    assert ((100,), (2,)) in moves and ((100,), (1,)) in moves


def test_bench_boost_week_wants_a_playing_second_keeper(R):
    pl, xp, ps = _gk_case(gws=(1, 2), gk1=(3.0, 1.0), new_gk=(3.0, 1.0))
    outfield = set(range(3, 16))
    bb = run(pl, xp, gws=(1, 2), rules=R, p_start=ps, chips={2: "bboost"}, banned=outfield)
    no_gk = run(pl, xp, gws=(1, 2), rules=R, p_start=ps, chips={2: "bboost"},
                banned=outfield | {100})
    assert 100 in {i for g in bb.gws for i in g.buys}
    assert 2 in {o for g in bb.gws for o in g.sells}
    # the playing second keeper is worth his full 3.0 in the Bench Boost week
    assert bb.objective - no_gk.objective == pytest.approx(3.0, abs=1e-6)
