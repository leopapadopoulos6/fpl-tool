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
