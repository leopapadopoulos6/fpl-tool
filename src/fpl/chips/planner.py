"""Chip planner: expected gain of each remaining chip in each GW of its window.

For every (chip, GW) pair inside the horizon and the chip's window, re-solve the multi-GW
MILP with that chip forced in that GW and compare with the best no-chip plan over the same
horizon (same decay, same terminal FT value). Gain = objective difference, so it includes
everything the chip changes downstream (e.g. a Wildcard reshaping later GWs).

Simplifications: one chip at a time (interactions such as Wildcard-then-Bench-Boost are not
searched jointly), and price changes inside the horizon are ignored.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from fpl.optimize.milp import OptInput, solve
from fpl.rules import ChipWindow

CHIP_LABEL = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost",
              "3xc": "Triple Captain"}


def plan_chips(inp: OptInput, available: list[ChipWindow],
               time_limit: float = 30.0) -> tuple[pd.DataFrame, float]:
    base_inp = replace(inp, chips={}, settings=replace(inp.settings, time_limit=time_limit))
    base = solve(base_inp)
    rows = []
    for w in available:
        for gw in inp.gws:
            if not w.contains(gw):
                continue
            plan = solve(replace(base_inp, chips={gw: w.name}))
            if not plan.gws:
                continue
            g = next(x for x in plan.gws if x.gw == gw)
            rows.append({"chip": w.name, "label": CHIP_LABEL.get(w.name, w.name),
                         "window": f"GW{w.start_event}-{w.stop_event}", "gw": gw,
                         "gain": plan.objective - base.objective,
                         "gw_xpts": g.xpts, "transfers_in": g.buys, "transfers_out": g.sells})
    return pd.DataFrame(rows), base.objective
