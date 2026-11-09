"""Joined historical tables: player_match, player_season, team_season (+ team_elo).

``player_match``: one row per player per match, any competition.
- PL matches merge the FPL row (points, bonus, BPS, ICT, transfers, value, FPL xG), the
  Understat row (xG, npxG, xA, shots, key passes, official assists) and the Core-Insights
  row (FotMob-style xG/xA, defensive actions) on (code, date +/- 1 day).
- Non-PL league matches come from Understat; European and domestic cup matches from
  Core-Insights. These have no FPL columns.
- Each source keeps its own columns (``xg_us``, ``xg_fpl``, ``xg_ci``; ``assists_official``
  vs ``assists_fpl``); ``xg``/``xa`` are canonical picks (Understat > Core-Insights > FPL)
  with ``xg_source`` recording which.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from fpl.history.idmap import TEAM_ALIAS

STADIUMS = Path(__file__).with_name("stadiums.csv")
FPL_COLS = ["fpl_points", "bonus", "bps", "influence", "creativity", "threat", "ict_index",
            "value", "selected", "transfers_in", "transfers_out", "transfers_balance",
            "clean_sheets", "goals_conceded", "saves", "yellow_cards", "red_cards", "own_goals",
            "penalties_missed", "penalties_saved", "cbi", "tackles", "recoveries", "dc_count",
            "xgc_fpl"]


def _season_label(start: int) -> str:
    return f"{start}-{str(start + 1)[-2:]}"


def _ci_season(s: str) -> str:  # 2025-2026 -> 2025-26
    return f"{s[:4]}-{s[-2:]}"


def venue(home_team: pd.Series, dates: pd.Series) -> pd.Series:
    """Home team's ground at that date (stadium moves from stadiums.csv), else the home
    team's name, which identifies the ground for every club without a move."""
    st = pd.read_csv(STADIUMS, parse_dates=["from_date"]).sort_values("from_date")
    out = home_team.astype(object).copy()
    d = pd.to_datetime(dates, utc=True).dt.tz_convert(None)
    for team, g in st.groupby("team"):
        mask = home_team == team
        if not mask.any():
            continue
        idx = np.searchsorted(g["from_date"].to_numpy(), d[mask].to_numpy(), side="right") - 1
        out[mask] = [g["venue"].iloc[i] if i >= 0 else team for i in idx]
    return out


def player_match(fpl: pd.DataFrame, us: pd.DataFrame, links: pd.DataFrame,
                 ci_pm: pd.DataFrame, ci_tm: pd.DataFrame, ci_code_team: pd.DataFrame,
                 elo: pd.DataFrame) -> pd.DataFrame:
    code_of = links.set_index("understat_id")["code"]
    # --- FPL base (PL)
    f = fpl.copy()
    f["date"] = f["kickoff_time"].dt.tz_convert(None).dt.normalize()
    pl = pd.DataFrame({
        "code": f["code"], "season": f["season"], "date": f["date"], "kickoff": f["kickoff_time"],
        "competition": "Premier League", "league": "EPL", "team": f["team"],
        "opponent": f["opponent"], "is_home": f["was_home"], "minutes": f["minutes"],
        "started": f["starts"], "goals": f["goals"], "assists_fpl": f["assists_fpl"],
        "xg_fpl": f["xg_fpl"], "xa_fpl": f["xa_fpl"], "position": f["position"],
        **{c: f[c] for c in FPL_COLS},
    })
    # --- Understat
    u = us.copy()
    u["code"] = u["understat_id"].map(code_of).astype("Int64")
    u["date"] = u["date"].dt.tz_convert(None).dt.normalize()
    u_cols = {"xg": "xg_us", "npxg": "npxg_us", "xa": "xa_us", "shots": "shots_us",
              "key_passes": "key_passes_us", "assists_official": "assists_official_us",
              "xgchain": "xgchain", "xgbuildup": "xgbuildup", "minutes": "minutes_us",
              "goals": "goals_us", "position_us": "position_us"}
    u_epl = u[(u["league"] == "EPL") & u["code"].notna()]
    pl = _merge_by_date(pl, u_epl[["code", "date", *u_cols]].rename(columns=u_cols))
    # Understat PL matches with no FPL row (seasons before FPL data, or unmatched dates)
    seen = set(zip(pl.loc[pl["xg_us"].notna(), "code"], pl.loc[pl["xg_us"].notna(), "date"],
                   strict=True))
    fpl_seasons = set(pl["season"])
    orphan = u_epl[[(c, d) not in seen for c, d in zip(u_epl["code"], u_epl["date"], strict=True)]]
    orphan = orphan[~orphan["season_start"].map(_season_label).isin(fpl_seasons)]
    us_pl = pd.DataFrame({
        "code": orphan["code"], "season": orphan["season_start"].map(_season_label),
        "date": orphan["date"], "competition": "Premier League", "league": "EPL",
        "team": orphan["team"].map(lambda t: TEAM_ALIAS.get(t, t)),
        "opponent": orphan["opponent"].map(lambda t: TEAM_ALIAS.get(t, t)),
        "is_home": orphan["is_home"], "minutes": orphan["minutes"], "goals": orphan["goals"],
        "started": (orphan["position_us"] != "Sub").astype(float),
        **{v: orphan[k] for k, v in u_cols.items() if k not in ("minutes", "goals")},
    })
    pl = pd.concat([pl, us_pl], ignore_index=True)
    # --- Core-Insights (PL rows)
    c = ci_pm.copy()
    c["season"] = c["season"].map(_ci_season)
    tm = ci_tm.copy()
    tm["season"] = tm["season"].map(_ci_season)
    meta = tm.set_index(["season", "match_id"])[["kickoff_time", "competition", "home_team",
                                                 "away_team", "home_score", "away_score"]]
    c = c.join(meta, on=["season", "match_id"], rsuffix="_m")
    c["date"] = c["kickoff_time"].dt.tz_convert(None).dt.normalize()
    c["code"] = c["player_code"].astype("Int64")
    ci_cols = {"xg": "xg_ci", "xa": "xa_ci", "shots": "shots_ci", "assists_official": "assists_ci",
               "key_passes": "key_passes_ci", "interceptions": "interceptions_ci",
               "clearances": "clearances_ci", "blocks": "blocks_ci", "xgot": "xgot_ci",
               "box_touches": "box_touches_ci", "start_min": "start_min_ci",
               "minutes": "minutes_ci"}
    c_pl = c[c["competition"] == "Premier League"]
    pl = _merge_by_date(pl, c_pl[["code", "date", *ci_cols]].rename(columns=ci_cols))
    # --- non-PL: Understat other leagues
    u_other = u[(u["league"] != "EPL") & u["code"].notna()]
    other = pd.DataFrame({
        "code": u_other["code"], "season": u_other["season_start"].map(_season_label),
        "date": u_other["date"], "competition": u_other["league"], "league": u_other["league"],
        "team": u_other["team"], "opponent": u_other["opponent"], "is_home": u_other["is_home"],
        "minutes": u_other["minutes"], "goals": u_other["goals"],
        "started": (u_other["position_us"] != "Sub").astype(float),
        **{v: u_other[k] for k, v in u_cols.items() if k not in ("minutes", "goals")},
    })
    # --- non-PL: Core-Insights European / cup matches (PL clubs' players)
    c_cup = c[(c["competition"] != "Premier League") & c["code"].notna()].copy()
    team_of = ci_code_team.set_index(["season", "code"])["team"]
    c_cup["team"] = [team_of.get((s, k)) for s, k in zip(c_cup["season"], c_cup["code"],
                                                          strict=True)]
    cup = pd.DataFrame({
        "code": c_cup["code"], "season": c_cup["season"], "date": c_cup["date"],
        "competition": c_cup["competition"], "league": None, "team": c_cup["team"],
        "minutes": c_cup["minutes"], "goals": c_cup["goals"],
        "started": (c_cup["start_min"] == 0).astype(float),
        **{v: c_cup[k] for k, v in ci_cols.items() if k != "minutes"},
    })
    df = pd.concat([pl, other, cup], ignore_index=True)
    # canonical picks and FPL-vs-official differences
    df["xg"] = df["xg_us"].combine_first(df["xg_ci"]).combine_first(df["xg_fpl"])
    df["xa"] = df["xa_us"].combine_first(df["xa_ci"]).combine_first(df["xa_fpl"])
    df["xg_source"] = np.select([df["xg_us"].notna(), df["xg_ci"].notna(), df["xg_fpl"].notna()],
                                ["understat", "core_insights", "fpl"], default="none")
    df["assists_official"] = df["assists_official_us"].combine_first(df["assists_ci"])
    df["delta_assists_fpl_vs_official"] = df["assists_fpl"] - df["assists_official"]
    # venue + Elo
    home_team = np.where(df["is_home"].fillna(False).astype(bool), df["team"], df["opponent"])
    df["venue"] = venue(pd.Series(home_team, index=df.index), df["date"])
    e = elo.copy()
    e["date"] = pd.to_datetime(e["date"], utc=True).dt.tz_convert(None).dt.normalize()
    e = e.drop_duplicates(["team", "date"])[["team", "date", "elo_pre", "opponent_elo_pre"]]
    df = df.merge(e.rename(columns={"elo_pre": "team_elo_pre"}), on=["team", "date"],
                  how="left")
    src = np.zeros(len(df), dtype=int)
    src |= df["fpl_points"].notna().to_numpy() * 1
    src |= df["minutes_us"].notna().to_numpy() * 2 if "minutes_us" in df else 0
    src |= df["minutes_ci"].notna().to_numpy() * 4 if "minutes_ci" in df else 0
    df["source_mask"] = src  # 1 FPL, 2 Understat, 4 Core-Insights
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def _merge_by_date(base: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """Left-join ``extra`` on (code, date) allowing +/- 1 day (time zones, late kickoffs)."""
    extra = extra.dropna(subset=["code"]).drop_duplicates(["code", "date"])
    extra = extra.assign(_hit=True)
    out = base.merge(extra, on=["code", "date"], how="left")
    cols = [c for c in extra.columns if c not in ("code", "date")]
    miss = out["_hit"].isna()
    for shift in (-1, 1):
        if not miss.any():
            break
        e2 = extra.assign(date=extra["date"] + pd.Timedelta(days=shift))
        fill = out.loc[miss, ["code", "date"]].reset_index().merge(e2, on=["code", "date"],
                                                                     how="inner")
        if fill.empty:
            continue
        fill = fill.drop_duplicates("index").set_index("index")
        out.loc[fill.index, cols] = fill[cols]
        miss = out["_hit"].isna()
    return out.drop(columns="_hit")


def player_season(pm: pd.DataFrame) -> pd.DataFrame:
    g = pm.groupby(["code", "season", "competition", "team"], dropna=False)
    agg = g.agg(apps=("minutes", lambda m: int((m > 0).sum())), minutes=("minutes", "sum"),
                starts=("started", "sum"), goals=("goals", "sum"),
                assists_official=("assists_official", "sum"), assists_fpl=("assists_fpl", "sum"),
                xg=("xg", "sum"), npxg=("npxg_us", "sum"), xa=("xa", "sum"),
                shots=("shots_us", "sum"), fpl_points=("fpl_points", "sum"),
                bonus=("bonus", "sum"), position=("position", "first")).reset_index()
    for c in ("goals", "xg", "npxg", "xa", "shots"):
        agg[f"{c}_per90"] = (agg[c] * 90 / agg["minutes"].where(agg["minutes"] > 0)).round(4)
    agg["delta_assists_fpl_vs_official"] = agg["assists_fpl"] - agg["assists_official"]
    return agg


def team_season(results: pd.DataFrame, pm: pd.DataFrame, us_team: pd.DataFrame,
                elo_summary: pd.DataFrame) -> pd.DataFrame:
    """PL team-seasons: results, xG for/against (FPL xG sums 2022+, Understat player sums
    2021-22, Understat team files 2019-21), Elo start/end/mean."""
    r = results[results["league"] == "EPL"]
    rows = []
    for (season, team), _ in pd.concat([r.assign(team=r["home"]), r.assign(team=r["away"])]) \
            .groupby(["season", "team"]):
        h = r[(r["season"] == season) & (r["home"] == team)]
        a = r[(r["season"] == season) & (r["away"] == team)]
        rows.append({"season": season, "team": team, "matches": len(h) + len(a),
                     "goals_for": int(h["home_goals"].sum() + a["away_goals"].sum()),
                     "goals_against": int(h["away_goals"].sum() + a["home_goals"].sum())})
    ts = pd.DataFrame(rows)
    plm = pm[pm["competition"] == "Premier League"]
    fpl_xg = plm.groupby(["season", "team"])["xg_fpl"].sum(min_count=1).rename("xg_for_fpl")
    us_xg = plm.groupby(["season", "team"])["xg_us"].sum(min_count=1).rename("xg_for_us")
    fpl_xga = plm.groupby(["season", "opponent"])["xg_fpl"].sum(min_count=1).rename(
        "xg_against_fpl")
    fpl_xga.index = fpl_xga.index.set_names(["season", "team"])
    ts = ts.join(fpl_xg, on=["season", "team"]).join(us_xg, on=["season", "team"]) \
        .join(fpl_xga, on=["season", "team"])
    if not us_team.empty:
        ut = us_team.groupby(["season", "team"])[["xg_for", "xg_against"]].sum()
        ut.columns = ["xg_for_us_team", "xg_against_us_team"]
        ts = ts.join(ut, on=["season", "team"])
    ts["xg_for"] = ts["xg_for_fpl"].where(ts["season"] >= "2022-23")
    ts["xg_for"] = ts["xg_for"].combine_first(ts["xg_for_us"].where(ts["season"] >= "2021-22"))
    ts["xg_against"] = ts["xg_against_fpl"].where(ts["season"] >= "2022-23")
    if "xg_for_us_team" in ts:
        ts["xg_for"] = ts["xg_for"].combine_first(ts["xg_for_us_team"])
        ts["xg_against"] = ts["xg_against"].combine_first(ts["xg_against_us_team"])
    e = elo_summary[elo_summary["league"] == "EPL"].drop(columns=["league", "matches"])
    return ts.merge(e, on=["season", "team"], how="left")
