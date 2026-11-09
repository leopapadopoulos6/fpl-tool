"""Player ID map: FPL ``code`` (master key) <-> Understat id.

FPL ``code`` is stable across seasons; Core-Insights uses it natively; vaastav maps each
season's ``element`` to it via players_raw.csv. Understat links, in priority order:

1. ``id_overrides.csv`` (committed, manual): always wins.
2. vaastav ``id_dict.csv`` pairs (2021-22, 2022-23).
3. Scored candidates: for each Understat player-season-club in the EPL, every FPL player at
   that club that season is scored on
   - name similarity (accent-folded tokens; full name and web_name; partial-name subsets),
   - minutes agreement on the same dates (|Δ| <= 5 min), which separates namesakes
     (e.g. two Danny Wards) and handles mid-season moves.
   score = 0.35 * name + 0.65 * minutes. Accepted when score >= ACCEPT and the margin over
   the runner-up >= MARGIN; otherwise left for review (listed in the report).

A player's Understat id spans his whole career, so once linked his non-PL seasons follow.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ACCEPT = 0.6
MARGIN = 0.15
OVERRIDES = Path(__file__).with_name("id_overrides.csv")

TEAM_ALIAS = {"Manchester City": "Man City", "Manchester United": "Man Utd",
              "Newcastle United": "Newcastle", "Tottenham": "Spurs",
              "Wolverhampton Wanderers": "Wolves", "Nottingham Forest": "Nott'm Forest",
              "Sheffield United": "Sheffield Utd", "West Bromwich Albion": "West Brom"}


def fold(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ø", "o").replace("Ø", "o").replace("ß", "ss").replace("ł", "l")
    return [t for t in re.split(r"[^a-z]+", s.lower()) if t]


def name_score(us_name: str, full: str, web: str) -> float:
    u = set(fold(us_name))
    if not u:
        return 0.0
    best = 0.0
    for cand in (full, web):
        c = set(fold(cand))
        if not c:
            continue
        if u == c or ((u <= c or c <= u) and min(len(u), len(c)) >= 2):
            best = max(best, 1.0)
        elif u <= c or c <= u:  # one-token subset, e.g. a surname-only web_name
            best = max(best, 0.6)
        else:
            best = max(best, len(u & c) / len(u | c))
    return best


@dataclass
class IdMapResult:
    links: pd.DataFrame       # understat_id, code, method, score, margin, understat_name, fpl_name
    review: pd.DataFrame      # ambiguous candidates
    unmatched: pd.DataFrame   # understat ids without any candidate


def _fpl_season(start: int) -> str:
    return f"{start}-{str(start + 1)[-2:]}"


def build(fpl: pd.DataFrame, us: pd.DataFrame, vaastav_root: Path) -> IdMapResult:
    links: dict[int, dict] = {}
    # 1. overrides
    if OVERRIDES.exists():
        for r in pd.read_csv(OVERRIDES).itertuples():
            links[int(r.understat_id)] = {"code": int(r.code), "method": "override",
                                          "score": 1.0, "margin": 1.0}
    # 2. id_dict pairs
    for f in sorted((vaastav_root / "data").glob("*/id_dict.csv")):
        season = f.parent.name
        d = pd.read_csv(f)
        d.columns = [c.strip() for c in d.columns]
        pr = pd.read_csv(f.parent / "players_raw.csv").set_index("id")["code"]
        for r in d.itertuples():
            uid, code = int(r.Understat_ID), pr.get(int(r.FPL_ID))
            if code is not None and uid not in links:
                links[uid] = {"code": int(code), "method": f"id_dict {season}",
                              "score": 1.0, "margin": 1.0}
    # 3. scored candidates
    epl = us[us["league"] == "EPL"].copy()
    epl["season"] = epl["season_start"].map(_fpl_season)
    epl["fpl_team"] = epl["team"].map(lambda t: TEAM_ALIAS.get(t, t))
    epl["day"] = epl["date"].dt.tz_convert(None).dt.normalize()
    f = fpl[["season", "code", "team", "name", "web_name", "minutes", "kickoff_time"]].copy()
    f["day"] = f["kickoff_time"].dt.tz_convert(None).dt.normalize()
    by_club = {k: g for k, g in f.groupby(["season", "team"])}
    names = f.drop_duplicates("code").set_index("code")[["name", "web_name"]]
    scores: dict[tuple[int, int], list[float]] = {}
    for (uid, season, club), g in epl.groupby(["understat_id", "season", "fpl_team"]):
        pool = by_club.get((season, club))
        if pool is None:
            continue
        us_name = g["understat_name"].iloc[0]
        days = g[["day", "minutes"]]
        for code, pg in pool.groupby("code"):
            ns = name_score(us_name, pg["name"].iloc[0], pg["web_name"].iloc[0])
            if ns == 0:
                continue
            j = days.merge(pg[["day", "minutes"]], on="day", how="left", suffixes=("_us", "_f"))
            agree = ((j["minutes_us"] - j["minutes_f"]).abs() <= 5).mean()
            scores.setdefault((uid, int(code)), []).append(0.35 * ns + 0.65 * float(agree))
    rows = pd.DataFrame([(u, c, max(v), len(v)) for (u, c), v in scores.items()],
                        columns=["understat_id", "code", "score", "seasons"])
    review = []
    for uid, g in rows.sort_values("score", ascending=False).groupby("understat_id"):
        if uid in links:
            continue
        best = g.iloc[0]
        margin = best.score - (g.iloc[1].score if len(g) > 1 else 0.0)
        if best.score >= ACCEPT and margin >= MARGIN:
            links[int(uid)] = {"code": int(best.code), "method": "scored",
                               "score": round(float(best.score), 3),
                               "margin": round(float(margin), 3)}
        else:
            review.append(g.head(3).assign(margin=margin))
    us_names = us.drop_duplicates("understat_id").set_index("understat_id")["understat_name"]
    out = pd.DataFrame([{"understat_id": k, **v} for k, v in links.items()])
    out["understat_name"] = out["understat_id"].map(us_names)
    out["fpl_name"] = out["code"].map(names["name"])
    out["fpl_web_name"] = out["code"].map(names["web_name"])
    # a code must not be claimed by two Understat ids
    dup = out[out.duplicated("code", keep=False)]
    if not dup.empty:
        keep = dup.sort_values("score", ascending=False).drop_duplicates("code")
        out = pd.concat([out[~out["code"].isin(dup["code"])], keep], ignore_index=True)
        review.append(dup[~dup["understat_id"].isin(keep["understat_id"])].assign(margin=0.0))
    unmatched = us_names[~us_names.index.isin(out["understat_id"])
                         & ~us_names.index.isin(rows["understat_id"])]
    rev = pd.concat(review, ignore_index=True) if review else pd.DataFrame()
    return IdMapResult(out.sort_values("understat_id").reset_index(drop=True), rev,
                       unmatched.rename("understat_name").reset_index())


def match_report(fpl: pd.DataFrame, us: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    """Per FPL season: share of players / minutes linked to Understat, split into
    'has Understat data' (source coverage) and 'linked' (mapping quality)."""
    linked = set(links["code"])
    epl = us[us["league"] == "EPL"]
    rows = []
    for season, g in fpl.groupby("season"):
        start = int(season[:4])
        played = g[g["minutes"] > 0].groupby("code")["minutes"].sum()
        has_us = epl[epl["season_start"] == start]
        covered_us = len(has_us) > 0
        lk = played[played.index.isin(linked)]
        share = round(lk.sum() / played.sum(), 4) if played.sum() else 0.0
        rows.append({"season": season, "players": len(played), "minutes": int(played.sum()),
                     "linked_players": len(lk), "linked_minutes_share": share,
                     "understat_rows_this_season": int(len(has_us)),
                     "source_has_season": covered_us})
    return pd.DataFrame(rows)
