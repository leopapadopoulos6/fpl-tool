"""Effective ownership (EO) among the overall top-k, sampled from league 314.

EO(p) = mean over sampled entries of the pick multiplier (0 bench, 1 starter, 2 captain,
3 triple captain), as a percentage. ``owned`` is the share holding the player at all.
Picks come from the latest GW whose deadline has passed, so they lag transfers made since.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl.api import endpoints as ep
from fpl.api.client import FPLAPIError, FPLClient

OVERALL_LEAGUE = 314


def top_entries(c: FPLClient, top_k: int = 10_000) -> pd.DataFrame:
    rows, page = [], 1
    while len(rows) < top_k:
        d = ep.league_classic_standings(c, OVERALL_LEAGUE, page)
        res = d["standings"]["results"]
        rows += [{"entry": r["entry"], "rank": r["rank"]} for r in res]
        if not d["standings"]["has_next"] or not res:
            break
        page += 1
    return pd.DataFrame(rows[:top_k])


def effective_ownership(c: FPLClient, gw: int, top_k: int = 10_000,
                        sample: int = 500, seed: int = 0) -> tuple[pd.DataFrame, dict]:
    entries = top_entries(c, top_k)
    rng = np.random.default_rng(seed)
    pick = entries.sample(n=min(sample, len(entries)), random_state=int(rng.integers(1e9)))
    mult: dict[int, float] = {}
    held: dict[int, int] = {}
    n = 0
    for e in pick["entry"]:
        try:
            p = ep.entry_picks(c, int(e), gw)
        except FPLAPIError:
            continue
        n += 1
        for x in p.picks:
            mult[x.element] = mult.get(x.element, 0) + x.multiplier
            held[x.element] = held.get(x.element, 0) + 1
    df = pd.DataFrame({"element": list(held), "owned_pct": [100 * held[k] / n for k in held],
                       "eo_pct": [100 * mult.get(k, 0) / n for k in held]})
    meta = {"top_k": top_k, "sampled": n, "gw": gw}
    return df.sort_values("eo_pct", ascending=False).reset_index(drop=True), meta
