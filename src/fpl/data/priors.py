"""Last-season priors from the vaastav/Fantasy-Premier-League mirror (past FPL API data).

Team ratings are fitted on the previous season's per-fixture team xG and mapped to the
current season by the stable FPL team ``code``. Teams without a prior (promoted) get the mean
rating of the previous season's three weakest teams (by attack + defence; a higher defence
rating means fewer goals conceded).
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pandas as pd

from fpl.api.models import Bootstrap
from fpl.models.team_strength import TeamStrengthParams, fit

MIRROR = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
PRIOR_SEASON = "2025-26"


def _get_csv(url: str, cache: Path) -> pd.DataFrame:
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        cache.write_bytes(r.content)
    return pd.read_csv(io.BytesIO(cache.read_bytes()))


def prior_season_team_matches(data_dir: Path, season: str = PRIOR_SEASON) -> pd.DataFrame:
    ext = data_dir / "external" / season
    gw = _get_csv(f"{MIRROR}/{season}/gws/merged_gw.csv", ext / "merged_gw.csv")
    teams = _get_csv(f"{MIRROR}/{season}/teams.csv", ext / "teams.csv")
    fixtures = _get_csv(f"{MIRROR}/{season}/fixtures.csv", ext / "fixtures.csv")
    code = dict(zip(teams["id"], teams["code"], strict=True))
    name_to_id = dict(zip(teams["name"], teams["id"], strict=True))
    gw["team_id"] = gw["team"].map(name_to_id)
    xg = gw.groupby(["fixture", "team_id"])["expected_goals"].sum()
    rows = []
    for f in fixtures[fixtures["finished"]].itertuples():
        for t, o, home, gf, ga in ((f.team_h, f.team_a, True, f.team_h_score, f.team_a_score),
                                   (f.team_a, f.team_h, False, f.team_a_score, f.team_h_score)):
            rows.append({"fixture": f.id, "round": f.event, "kickoff_time": f.kickoff_time,
                         "team": code[t], "opponent": code[o], "home": home,
                         "goals_for": gf, "goals_against": ga,
                         "xg_for": xg.get((f.id, t), 0.0), "xg_against": xg.get((f.id, o), 0.0)})
    return pd.DataFrame(rows)


def team_prior(bootstrap: Bootstrap, data_dir: Path,
               params: TeamStrengthParams | None = None) -> dict[int, tuple[float, float]]:
    """Current team id -> (att, def) log-ratings from last season (keyed via team code)."""
    last = fit(prior_season_team_matches(data_dir), params or TeamStrengthParams(ridge=5.0))
    weak = sorted(last.teams, key=lambda t: last.att[t] + last.dfn[t])[:3]
    promoted = (sum(last.att[t] for t in weak) / 3, sum(last.dfn[t] for t in weak) / 3)
    out = {}
    for t in bootstrap.teams:
        c = getattr(t, "code", None)
        out[t.id] = (last.att[c], last.dfn[c]) if c in last.att else promoted
    return out
