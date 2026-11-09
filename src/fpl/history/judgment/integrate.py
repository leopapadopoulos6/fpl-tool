"""Bridge from the judgment layer to the xPts model: per-element history priors as of a date.

The prior is the player's career rate (FPL scale, PL-equivalent, opponent-neutral) and its
strength k (in 90s, discounted for team/role change). The current-season evidence itself
stays inside ``models.xpts.fit_rates``, so nothing is counted twice.
"""

from __future__ import annotations

import pandas as pd

from fpl.history.judgment.baseline import BaselineParams, baseline


def prior_fn(ev: pd.DataFrame, season: str, element_to_code: dict[int, int],
             params: BaselineParams | None = None):
    code_to_elements: dict[int, list[int]] = {}
    for e, c in element_to_code.items():
        code_to_elements.setdefault(int(c), []).append(int(e))

    def fn(as_of) -> pd.DataFrame:
        ts = pd.Timestamp(as_of)
        ts = ts.tz_convert(None) if ts.tzinfo else ts
        b = baseline(ev, ts, season, params)
        rows = []
        for code, r in b.iterrows():
            if r["career_90s_eff"] <= 0:
                continue
            for e in code_to_elements.get(int(code), []):
                rows.append({"element": e, "xg90": r["career_rate_xg"],
                             "xa90": r["career_rate_xa"], "k90": r["k"]})
        return pd.DataFrame(rows).set_index("element") if rows else pd.DataFrame(
            columns=["xg90", "xa90", "k90"])
    return fn
