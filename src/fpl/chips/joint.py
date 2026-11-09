"""Joint chip timing: Wildcard, Bench Boost and Triple Captain placed together.

Method: coordinate search over fixed-chip MILP solves. Start each chip at its best
single-chip GW; then repeatedly re-place one chip at a time over every GW of its window
(the other chips held where they are, one chip per GW) until nothing improves. Every solve
re-plans transfers around the chips, and solves are cached by chip assignment.

Why not one MILP with chip binaries (``OptInput.chip_search``)? It is exact and tested on
small cases, but at real size its LP relaxation is weak (13% gap after 300 s over 13 GWs),
while a fixed-chip solve over the same horizon takes seconds.

The horizon runs ``tail_gws`` past the chips' deadline (default 4) so a late Wildcard's
value to the squad after the deadline counts instead of being truncated. Free Hit is not
searched (planned separately once blanks are announced).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from fpl.optimize.milp import OptInput, Plan, solve
from fpl.rules import ChipWindow

SEARCHED = ("wildcard", "bboost", "3xc")


def chip_search_space(available: list[ChipWindow], gws: list[int]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for w in available:
        if w.name in SEARCHED:
            out.setdefault(w.name, [])
            out[w.name] += [g for g in gws if w.contains(g)]
    return {k: sorted(set(v)) for k, v in out.items() if v}


@dataclass
class JointResult:
    best: Plan
    assignment: dict[str, int]           # chip -> GW
    no_chips: Plan
    runner_up: tuple[dict[str, int], float] | None
    by_gw: list[dict] = field(default_factory=list)  # chip, gw, objective, gain, gain_vs_best
    solves: int = 0

    @property
    def chips_value(self) -> float:
        return self.best.objective - self.no_chips.objective

    def wildcard_now_vs_later(self, first_gw: int) -> tuple[float, float] | None:
        rows = [r for r in self.by_gw if r["chip"] == "wildcard"]
        now = [r["objective"] for r in rows if r["gw"] == first_gw]
        later = [r["objective"] for r in rows if r["gw"] != first_gw]
        return (now[0], max(later)) if now and later else None


def plan_joint(inp: OptInput, available: list[ChipWindow], time_limit: float = 30.0,
               max_rounds: int = 4) -> JointResult:
    search = chip_search_space(available, inp.gws)
    base = replace(inp, chips={}, chip_search={},
                   settings=replace(inp.settings, time_limit=time_limit))
    cache: dict[frozenset, Plan] = {}

    def run(assign: dict[str, int]) -> Plan:
        key = frozenset(assign.items())
        if key not in cache:
            cache[key] = solve(replace(base, chips={g: c for c, g in assign.items()}))
        return cache[key]

    none = run({})
    # start: each chip at its best single-chip GW, resolving clashes in chip order
    assign: dict[str, int] = {}
    for chip, gws in search.items():
        ranked = sorted(gws, key=lambda g: -run({chip: g}).objective)
        assign[chip] = next(g for g in ranked if g not in assign.values())
    best_obj = run(assign).objective
    rows: dict[tuple[str, int], float] = {}
    for _ in range(max_rounds):
        improved = False
        for chip, gws in search.items():
            others = {c: g for c, g in assign.items() if c != chip}
            for g in gws:
                if g in others.values():
                    continue
                obj = run({**others, chip: g}).objective
                rows[(chip, g)] = obj
                if obj > best_obj + 1e-6:
                    best_obj, assign, improved = obj, {**others, chip: g}, True
        if not improved:
            break
    best = run(assign)
    # final per-GW table: marginal value of each chip in each GW, the others at their best
    table = []
    for chip, gws in search.items():
        others = {c: g for c, g in assign.items() if c != chip}
        without = run(others).objective
        for g in gws:
            if g in others.values():
                continue
            plan = run({**others, chip: g})
            gw_plan = next((x for x in plan.gws if x.gw == g), None)
            table.append({"chip": chip, "gw": g, "objective": plan.objective,
                          "gain": plan.objective - without,
                          "gain_vs_best": plan.objective - best.objective,
                          "gw_xpts": gw_plan.xpts if gw_plan else 0.0})
    full = [(dict(k), p.objective) for k, p in cache.items()
            if len(k) == len(search) and dict(k) != assign and p.gws]
    runner = max(full, key=lambda x: x[1]) if full else None
    return JointResult(best, assign, none, runner, table, len(cache))
