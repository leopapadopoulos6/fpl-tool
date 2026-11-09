"""Multi-GW transfer / line-up optimizer (MILP, HiGHS via highspy).

Per player p and GW t (binaries): squad x, line-up y, captain c, buy, sell.
Per GW: free transfers ft (integer, banked up to the cap), paid transfers (hits), bank.

Constraints: 2/5/5/3 squad, formation min/max, <=3 per club, budget with real selling
prices for owned players and now_cost for buys, FT banking (WC/FH keep banked FTs and add
no +1 that GW), one chip per GW, Free Hit squad for its GW only.

Objective: sum_t decay^k * (line-up xPts + captain xPts [x2 extra with TC] + bench_weight *
bench xPts [full with BB]) - 4 * hits + ft_value * FTs banked at the horizon end.
Price changes inside the horizon are ignored (buy/sell at today's prices).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import highspy
import pandas as pd

from fpl.rules import HIT_COST, GameRules


@dataclass
class OptSettings:
    decay: float = 0.9
    bench_weight: float = 0.1
    gk_bench_weight: float = 0.02
    ft_value: float = 1.5
    max_hits_per_gw: int = 2
    time_limit: float = 60.0
    mip_gap: float = 0.002


@dataclass
class OptInput:
    players: pd.DataFrame  # index element: position, team, now_cost, sell_price, owned, name
    xp: pd.DataFrame       # index element, columns GW ids: expected points
    gws: list[int]
    bank: int
    free_transfers: int
    rules: GameRules
    chips: dict[int, str] = field(default_factory=dict)  # gw -> wildcard|freehit|bboost|3xc
    first_gw_transfers: int | None = None                 # force n transfers in first GW
    exclude_first_gw_buys: list[frozenset[int]] = field(default_factory=list)
    # joint chip search: chip -> GWs where the solver may play it (wildcard, bboost, 3xc)
    chip_search: dict[str, list[int]] = field(default_factory=dict)
    # no-good cuts on chip assignments: each dict {chip: gw} may not be repeated exactly
    chip_forbid: list[dict[str, int]] = field(default_factory=list)
    banned: set[int] = field(default_factory=set)
    locked: set[int] = field(default_factory=set)
    settings: OptSettings = field(default_factory=OptSettings)


@dataclass
class GWPlan:
    gw: int
    chip: str | None
    buys: list[int]
    sells: list[int]
    hits: int
    free_transfers: int  # available going into this GW
    lineup: list[int]
    bench: list[int]     # in auto-sub order: GK first, then outfield
    captain: int
    vice: int
    xpts: float          # line-up + captain bonus (+bench with BB), before hits
    bank_after: int


@dataclass
class Plan:
    status: str
    objective: float
    gws: list[GWPlan]
    gap: float = 0.0  # relative MIP gap at termination (0 = proven optimal)

    @property
    def total_xpts(self) -> float:
        return sum(g.xpts - HIT_COST * g.hits for g in self.gws)


def solve(inp: OptInput) -> Plan:
    s, r = inp.settings, inp.rules
    P = list(inp.players.index)
    T = inp.gws
    pl = inp.players
    xp = inp.xp.reindex(index=P, columns=T).fillna(0.0)
    h = highspy.Highs()
    h.silent()
    h.setOptionValue("time_limit", s.time_limit)
    h.setOptionValue("mip_rel_gap", s.mip_gap)

    def b(name):
        return h.addBinary(name=name)

    x = {(p, t): b(f"x_{p}_{t}") for p in P for t in T}
    y = {(p, t): b(f"y_{p}_{t}") for p in P for t in T}
    c = {(p, t): b(f"c_{p}_{t}") for p in P for t in T}
    buy = {(p, t): b(f"buy_{p}_{t}") for p in P for t in T}
    sell = {(p, t): b(f"sell_{p}_{t}") for p in P for t in T}
    ft = {i: h.addIntegral(lb=0, ub=r.max_free_transfers, name=f"ft_{i}")
          for i in range(len(T) + 1)}
    paid = {t: h.addIntegral(lb=0, ub=s.max_hits_per_gw, name=f"paid_{t}") for t in T}
    bank = {t: h.addVariable(lb=0, ub=10_000, name=f"bank_{t}") for t in T}
    h.addConstr(ft[0] == inp.free_transfers)

    if "freehit" in inp.chip_search:
        raise ValueError("Free Hit is not supported in the joint chip search")
    u = {(ch, t): b(f"chip_{ch}_{t}") for ch, gws in inp.chip_search.items()
         for t in gws if t in T}
    for ch in inp.chip_search:
        h.addConstr(h.qsum(v for (c_, _), v in u.items() if c_ == ch) <= 1)
    for t in T:
        here = [v for (_, t_), v in u.items() if t_ == t]
        if here:
            h.addConstr(h.qsum(here) <= (0 if inp.chips.get(t) else 1))
    for assign in inp.chip_forbid:
        keys = [(ch, gw) for ch, gw in assign.items()]
        if all(k in u for k in keys):
            h.addConstr(h.qsum(u[k] for k in keys) <= len(keys) - 1)
    big_m = r.squad_size

    fh_gw = next((g for g, ch in inp.chips.items() if ch == "freehit"), None)
    if fh_gw is not None:
        xf = {p: b(f"xf_{p}") for p in P}
        yf = {p: b(f"yf_{p}") for p in P}
        cf = {p: b(f"cf_{p}") for p in P}

    owned = {p: int(bool(pl.at[p, "owned"])) for p in P}
    sp = {p: int(pl.at[p, "sell_price"]) if owned[p] else int(pl.at[p, "now_cost"]) for p in P}
    cost = {p: int(pl.at[p, "now_cost"]) for p in P}
    pos = pl["position"].to_dict()
    club = pl["team"].to_dict()

    def squad_rules(sq, lu, cap):
        for ps, n in r.squad_select.items():
            h.addConstr(h.qsum(sq[p] for p in P if pos[p] == ps) == n)
        for tm in set(club.values()):
            h.addConstr(h.qsum(sq[p] for p in P if club[p] == tm) <= r.team_limit)
        h.addConstr(h.qsum(lu[p] for p in P) == r.squad_play)
        for ps in r.min_play:
            h.addConstr(h.qsum(lu[p] for p in P if pos[p] == ps) >= r.min_play[ps])
            h.addConstr(h.qsum(lu[p] for p in P if pos[p] == ps) <= r.max_play[ps])
        h.addConstr(h.qsum(cap[p] for p in P) == 1)
        for p in P:
            h.addConstr(lu[p] <= sq[p])
            h.addConstr(cap[p] <= lu[p])

    obj = 0
    for i, t in enumerate(T):
        chip = inp.chips.get(t)
        sq = {p: x[p, t] for p in P}
        squad_rules(sq, {p: y[p, t] for p in P}, {p: c[p, t] for p in P})
        prev_bank = inp.bank if i == 0 else bank[T[i - 1]]
        for p in P:
            prev = owned[p] if i == 0 else x[p, T[i - 1]]
            h.addConstr(x[p, t] == prev + buy[p, t] - sell[p, t])
            h.addConstr(buy[p, t] + sell[p, t] <= 1)
            if p in inp.banned:
                h.addConstr(buy[p, t] == 0)
            if p in inp.locked:
                h.addConstr(x[p, t] == 1)
        h.addConstr(bank[t] == prev_bank + h.qsum(sp[p] * sell[p, t] for p in P)
                    - h.qsum(cost[p] * buy[p, t] for p in P))
        n = h.qsum(buy[p, t] for p in P)
        if i == 0 and inp.first_gw_transfers is not None:
            h.addConstr(n == inp.first_gw_transfers)
        if chip in ("wildcard", "freehit"):
            h.addConstr(paid[t] == 0)
            h.addConstr(ft[i + 1] <= ft[i])
            if chip == "freehit":
                h.addConstr(n == 0)
        elif ("wildcard", t) in u:
            wc = u["wildcard", t]
            h.addConstr(paid[t] >= n - ft[i] - big_m * wc)
            h.addConstr(paid[t] <= n)
            h.addConstr(paid[t] <= s.max_hits_per_gw * (1 - wc))
            h.addConstr(ft[i + 1] <= ft[i] - (n - paid[t]) + 1 + big_m * wc)
            h.addConstr(ft[i + 1] <= ft[i] + 1 - wc)
        else:
            h.addConstr(paid[t] >= n - ft[i])
            h.addConstr(paid[t] <= n)
            h.addConstr(ft[i + 1] <= ft[i] - (n - paid[t]) + 1)

        w = s.decay ** i
        xpt = xp[t].to_dict()
        if chip == "freehit":
            squad_rules(xf, yf, cf)
            value = prev_bank + h.qsum(sp[p] * (owned[p] if i == 0 else x[p, T[i - 1]])
                                       for p in P)
            h.addConstr(h.qsum(cost[p] * xf[p] for p in P) <= value)
            lu, cp, sqd = yf, cf, xf
        else:
            lu, cp, sqd = ({p: y[p, t] for p in P}, {p: c[p, t] for p in P}, sq)
        cap_mult = 2 if chip == "3xc" else 1
        gw_obj = h.qsum(xpt[p] * lu[p] for p in P) + cap_mult * h.qsum(xpt[p] * cp[p] for p in P)
        if chip == "bboost":
            gw_obj = gw_obj + h.qsum(xpt[p] * (sqd[p] - lu[p]) for p in P)
        else:
            gw_obj = gw_obj + h.qsum(
                xpt[p] * (s.gk_bench_weight if pos[p] == "GKP" else s.bench_weight)
                * (sqd[p] - lu[p]) for p in P)
        if ("bboost", t) in u:
            ub = u["bboost", t]
            for p in P:
                v = b(f"bb_{p}_{t}")
                h.addConstr(v <= x[p, t] - y[p, t])
                h.addConstr(v <= ub)
                bw = s.gk_bench_weight if pos[p] == "GKP" else s.bench_weight
                gw_obj = gw_obj + xpt[p] * (1 - bw) * v
        if ("3xc", t) in u:
            ut = u["3xc", t]
            for p in P:
                v = b(f"tc_{p}_{t}")
                h.addConstr(v <= c[p, t])
                h.addConstr(v <= ut)
                gw_obj = gw_obj + xpt[p] * v
        obj = obj + w * gw_obj - HIT_COST * paid[t]

    first = T[0]
    for excl in inp.exclude_first_gw_buys:
        h.addConstr(h.qsum(buy[p, first] for p in excl if p in P) <= len(excl) - 1)
    obj = obj + s.ft_value * ft[len(T)]
    h.maximize(obj)
    status = str(h.getModelStatus()).split(".")[-1]
    if status not in ("kOptimal", "kTimeLimit", "kSolutionLimit", "kInterrupt"):
        return Plan(status, float("nan"), [])
    val = lambda v: h.val(v)  # noqa: E731

    chosen = {t: ch for (ch, t), v in u.items() if val(v) > 0.5}
    plans = []
    for i, t in enumerate(T):
        chip = inp.chips.get(t) or chosen.get(t)
        if chip == "freehit":
            sq_ids = [p for p in P if val(xf[p]) > 0.5]
            lu_ids = [p for p in P if val(yf[p]) > 0.5]
            cap = next(p for p in P if val(cf[p]) > 0.5)
        else:
            sq_ids = [p for p in P if val(x[p, t]) > 0.5]
            lu_ids = [p for p in P if val(y[p, t]) > 0.5]
            cap = next(p for p in P if val(c[p, t]) > 0.5)
        xpt = xp[t]
        bench = [p for p in sq_ids if p not in lu_ids]
        bench_gk = [p for p in bench if pos[p] == "GKP"]
        bench_out = sorted((p for p in bench if pos[p] != "GKP"), key=lambda p: -xpt[p])
        vice = max((p for p in lu_ids if p != cap), key=lambda p: xpt[p])
        pts = float(sum(xpt[p] for p in lu_ids) + xpt[cap] * (2 if chip == "3xc" else 1))
        if chip == "bboost":
            pts += float(sum(xpt[p] for p in bench))
        plans.append(GWPlan(
            gw=t, chip=chip,
            buys=[p for p in P if val(buy[p, t]) > 0.5],
            sells=[p for p in P if val(sell[p, t]) > 0.5],
            hits=int(round(val(paid[t]))), free_transfers=int(round(val(ft[i]))),
            lineup=sorted(lu_ids, key=lambda p: ("GKP", "DEF", "MID", "FWD").index(pos[p])),
            bench=bench_gk + bench_out, captain=cap, vice=vice, xpts=pts,
            bank_after=int(round(val(bank[t]))),
        ))
    info = h.getInfo()
    return Plan(status, float(info.objective_function_value), plans, float(info.mip_gap))


def candidate_pool(players: pd.DataFrame, xp: pd.DataFrame,
                   per_position: dict[str, int] | None = None) -> list[int]:
    """Owned players plus the top-N by horizon xPts per position (and the cheapest few,
    so bench-fodder options exist)."""
    per_position = per_position or {"GKP": 12, "DEF": 40, "MID": 45, "FWD": 25}
    tot = xp.sum(axis=1)
    keep = set(players.index[players["owned"]])
    for ps, n in per_position.items():
        idx = players.index[(players["position"] == ps) & players["available"]]
        keep |= set(tot.reindex(idx).sort_values(ascending=False).head(n).index)
        cheap = players.loc[idx].assign(t=tot.reindex(idx)).sort_values(["now_cost", "t"],
                                                                         ascending=[True, False])
        keep |= set(cheap.head(4).index)
    return sorted(keep)


def alternatives(inp: OptInput, max_first_gw_transfers: int = 2,
                 per_count: int = 2) -> list[tuple[int, Plan]]:
    """Best plan for each forced first-GW transfer count (roll, 1, 2...), plus the
    next-best distinct transfer sets for each count (no-good cuts)."""
    out = []
    for n in range(max_first_gw_transfers + 1):
        excl: list[frozenset[int]] = []
        for _ in range(per_count if n > 0 else 1):
            sub = OptInput(**{**inp.__dict__, "first_gw_transfers": n,
                              "exclude_first_gw_buys": list(excl)})
            plan = solve(sub)
            if not plan.gws:
                break
            out.append((n, plan))
            excl.append(frozenset(plan.gws[0].buys))
    return out

