"""Source-level normalisation: one tidy table per source, before the ID map joins them.

- ``fpl_gw``: vaastav per-GW FPL history, every season, keyed by the stable FPL ``code``.
- ``understat_match``: Understat career rows from vaastav's mirror (all leagues), with the
  player's own team inferred per match and the league inferred from the match graph.
- ``ci_player_match`` / ``ci_team_match``: Core-Insights league + European/cup matches.

Each function reads from ``data/external`` and returns a DataFrame; ``build.py`` writes them.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD", 5: "MNG"}


def _read(path: Path, **kw) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False, **kw)
    except UnicodeDecodeError:
        return pd.read_csv(path, low_memory=False, encoding="latin-1", **kw)


# --------------------------------------------------------------------------- FPL (vaastav)
FPL_KEEP = {
    "minutes": "minutes", "starts": "starts", "goals_scored": "goals",
    "assists": "assists_fpl", "total_points": "fpl_points", "bonus": "bonus", "bps": "bps",
    "influence": "influence", "creativity": "creativity", "threat": "threat",
    "ict_index": "ict_index", "expected_goals": "xg_fpl", "expected_assists": "xa_fpl",
    "expected_goals_conceded": "xgc_fpl", "clean_sheets": "clean_sheets",
    "goals_conceded": "goals_conceded", "saves": "saves", "yellow_cards": "yellow_cards",
    "red_cards": "red_cards", "own_goals": "own_goals", "penalties_missed": "penalties_missed",
    "penalties_saved": "penalties_saved", "clearances_blocks_interceptions": "cbi",
    "tackles": "tackles", "recoveries": "recoveries", "defensive_contribution": "dc_count",
    "value": "value", "selected": "selected", "transfers_in": "transfers_in",
    "transfers_out": "transfers_out", "transfers_balance": "transfers_balance",
}


def fpl_gw(vaastav_root: Path) -> pd.DataFrame:
    data = vaastav_root / "data"
    teams = _read(data / "master_team_list.csv")
    team_name = {(r.season, int(r.team)): r.team_name for r in teams.itertuples()}
    for tf in data.glob("*/teams.csv"):  # per-season files cover seasons the master list lacks
        for r in _read(tf).itertuples():
            team_name[(tf.parent.name, int(r.id))] = r.name
    frames = []
    for season_dir in sorted(p for p in data.iterdir() if p.is_dir() and p.name[:2] == "20"):
        s = season_dir.name
        gw_path = season_dir / "gws" / "merged_gw.csv"
        raw_path = season_dir / "players_raw.csv"
        if not gw_path.exists() or not raw_path.exists():
            continue
        gw = _read(gw_path)
        pr = _read(raw_path)
        info = pr.set_index("id")[["code", "element_type", "team", "first_name", "second_name",
                                   "web_name"]]
        df = pd.DataFrame({"season": s, "element": gw["element"].astype(int)})
        df["gw"] = gw["GW"] if "GW" in gw else gw["round"]
        df["fixture"] = gw.get("fixture")
        df["kickoff_time"] = pd.to_datetime(gw["kickoff_time"], utc=True, errors="coerce")
        df["was_home"] = gw["was_home"].astype(str).str.lower().isin(["true", "1"])
        df["opponent_id"] = gw["opponent_team"].astype(int)
        df["team_h_score"] = gw.get("team_h_score")
        df["team_a_score"] = gw.get("team_a_score")
        for src, dst in FPL_KEEP.items():
            df[dst] = pd.to_numeric(gw[src], errors="coerce") if src in gw else np.nan
        df = df.join(info, on="element")
        df["position"] = df["element_type"].map(POS)
        df["team"] = [team_name.get((s, int(t))) if pd.notna(t) else None for t in df["team"]]
        if "team" in gw:  # vaastav's own team column (later seasons) is authoritative
            df["team"] = gw["team"].where(gw["team"].notna(), df["team"])
        df["opponent"] = [team_name.get((s, int(o))) for o in df["opponent_id"]]
        df["name"] = (df["first_name"].fillna("") + " " + df["second_name"].fillna("")).str.strip()
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["code"] = out["code"].astype("Int64")
    return out.drop(columns=["element_type", "first_name", "second_name"])


def current_season_fpl(pm: pd.DataFrame, bootstrap, season: str) -> pd.DataFrame:
    """Our own up-to-date current-season FPL table (``data/processed/player_matches``) in the
    ``fpl_gw`` shape, so the latest season is not limited by the vaastav mirror's lag."""
    els = {e.id: e for e in bootstrap.elements}
    teams = {t.id: t.name for t in bootstrap.teams}
    rename = {"total_points": "fpl_points", "goals_scored": "goals", "assists": "assists_fpl",
              "expected_goals": "xg_fpl", "expected_assists": "xa_fpl",
              "expected_goals_conceded": "xgc_fpl", "clearances_blocks_interceptions": "cbi",
              "defensive_contribution": "dc_count", "round": "gw"}
    df = pm.rename(columns=rename).copy()
    df["season"] = season
    df["code"] = df["element"].map(lambda e: els[e].code if e in els else None).astype("Int64")
    df["name"] = df["element"].map(lambda e: els[e].web_name if e in els else None)
    df["web_name"] = df["name"]
    df["opponent"] = df["opponent_team"].map(teams)
    df["team"] = df["team"].map(teams)
    df["kickoff_time"] = pd.to_datetime(df["kickoff_time"], utc=True)
    for c in FPL_KEEP.values():
        if c not in df:
            df[c] = np.nan
    return df[["season", "element", "gw", "fixture", "kickoff_time", "was_home", "opponent",
               "team", "position", "code", "name", "web_name", *FPL_KEEP.values()]]


# --------------------------------------------------------------------------- Understat
US_FILE = re.compile(r"^(?P<name>.+)_(?P<uid>\d+)\.csv$")
ANCHORS = {"EPL": "Arsenal", "La_liga": "Real Madrid", "Bundesliga": "Bayern Munich",
           "Serie_A": "Juventus", "Ligue_1": "Paris Saint Germain",
           "RFPL": "Zenit St. Petersburg"}


def understat_files(vaastav_root: Path) -> dict[int, tuple[str, list[Path]]]:
    """understat_id -> (name, files across season folders)."""
    out: dict[int, tuple[str, list[Path]]] = {}
    for d in sorted((vaastav_root / "data").glob("*/understat")):
        for f in d.glob("*.csv"):
            m = US_FILE.match(f.name)
            if not m or f.name.startswith("understat_"):
                continue
            uid = int(m["uid"])
            name = m["name"].replace("_", " ")
            out.setdefault(uid, (name, []))[1].append(f)
    return out


def understat_match(vaastav_root: Path) -> pd.DataFrame:
    rows = []
    for uid, (name, files) in understat_files(vaastav_root).items():
        latest = max(files, key=lambda p: p.parent.parent.name)  # most recent mirror = longest
        df = _read(latest)
        if df.empty:
            continue
        df["understat_id"] = uid
        df["understat_name"] = name
        rows.append(df)
    df = pd.concat(rows, ignore_index=True).drop_duplicates(["understat_id", "id"])
    df = df.rename(columns={"id": "match_id", "time": "minutes", "xG": "xg", "xA": "xa",
                            "npxG": "npxg", "xGChain": "xgchain", "xGBuildup": "xgbuildup",
                            "assists": "assists_official", "position": "position_us"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["season_start"] = df["season"].astype(int)
    df = _infer_own_team(df)
    df["is_home"] = df["team"] == df["h_team"]
    df["opponent"] = np.where(df["is_home"], df["a_team"], df["h_team"])
    df["league"] = _infer_league(df)
    keep = ["understat_id", "understat_name", "match_id", "date", "season_start", "league",
            "team", "opponent", "is_home", "h_team", "a_team", "h_goals", "a_goals", "minutes",
            "goals", "assists_official", "shots", "key_passes", "xg", "npxg", "xa", "npg",
            "xgchain", "xgbuildup", "position_us"]
    return df[keep].sort_values(["understat_id", "date"]).reset_index(drop=True)


def _infer_own_team(df: pd.DataFrame) -> pd.DataFrame:
    """A player's own team appears in every match he plays for it; opponents vary. Per
    player-season, take the most frequent side; matches without it (after a mid-season
    move) get the most frequent side among the rest."""
    team = pd.Series(index=df.index, dtype=object)
    for _, g in df.groupby(["understat_id", "season_start"]):
        remaining = g
        while len(remaining):
            c = Counter(list(remaining["h_team"]) + list(remaining["a_team"]))
            own = c.most_common(1)[0][0]
            hit = (remaining["h_team"] == own) | (remaining["a_team"] == own)
            team.loc[remaining.index[hit]] = own
            remaining = remaining[~hit]
    df["team"] = team
    return df


def _infer_league(df: pd.DataFrame) -> pd.Series:
    """Understat holds league matches only, so within a season the team graph splits into
    one connected component per league. Label components by anchor clubs; components
    without an anchor are labelled by their best-connected anchor-free neighbour count."""
    def find(parent: dict[str, str], x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    league = pd.Series(index=df.index, dtype=object)
    for _, g in df.groupby("season_start"):
        parent: dict[str, str] = {}
        for h, a in zip(g["h_team"], g["a_team"], strict=True):
            parent[find(parent, h)] = find(parent, a)
        comp_label = {find(parent, anchor): lg for lg, anchor in ANCHORS.items()
                      if anchor in parent}
        league.loc[g.index] = [comp_label.get(find(parent, h), "unknown") for h in g["h_team"]]
    return league


def understat_team_match(vaastav_root: Path) -> pd.DataFrame:
    """Team-level Understat files (2019-20, 2020-21 folders): xG/xGA per PL match."""
    rows = []
    for f in sorted((vaastav_root / "data").glob("*/understat/understat_*.csv")):
        team = f.stem.removeprefix("understat_").replace("_", " ")
        df = _read(f)
        df["team"] = team
        df["season"] = f.parent.parent.name
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    return df[["season", "team", "date", "h_a", "xG", "xGA", "npxG", "npxGA", "scored",
               "missed"]].rename(columns={"xG": "xg_for", "xGA": "xg_against", "npxG": "npxg_for",
                                          "npxGA": "npxg_against", "scored": "goals_for",
                                          "missed": "goals_against"})


# --------------------------------------------------------------------------- Core-Insights
CI_PLAYER_COLS = {"minutes_played": "minutes", "goals": "goals", "assists": "assists_official",
                  "xg": "xg", "xa": "xa", "total_shots": "shots", "chances_created": "key_passes",
                  "tackles": "tackles", "interceptions": "interceptions",
                  "recoveries": "recoveries", "blocks": "blocks", "clearances": "clearances",
                  "touches_opposition_box": "box_touches", "xgot": "xgot",
                  "start_min": "start_min", "finish_min": "finish_min"}


TOURNAMENTS = {"prem": "Premier League", "champions-league": "Champions League",
               "europa-league": "Europa League", "conference-league": "Conference League",
               "efl-cup": "EFL Cup", "community-shield": "Community Shield",
               "uefa-super-cup": "Uefa Super Cup", "friendlies": "Friendlies",
               "fa-cup": "FA Cup", "friendly": "Friendlies"}


def _competition(row_tournament, path: Path) -> str:
    if isinstance(row_tournament, str) and row_tournament:
        return TOURNAMENTS.get(row_tournament, row_tournament)
    parts = path.parts
    if "By Tournament" in parts:
        return parts[parts.index("By Tournament") + 1]
    return "Premier League"


def ci_tables(ci_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    pm_rows, tm_rows = [], []
    for season_dir in sorted((ci_root / "data").glob("20*")):
        season = season_dir.name  # e.g. 2025-2026
        players = season_dir / "players.csv"
        code = _read(players).set_index("player_id")["player_code"] if players.exists() else {}
        for mf in season_dir.glob("By */*/**/matches.csv"):
            m = _read(mf)
            m["competition"] = [_competition(t, mf) for t in m.get("tournament", [None] * len(m))]
            m["season"] = season
            tm_rows.append(m)
            comp_of = dict(zip(m["match_id"], m["competition"], strict=True))
            pf = mf.with_name("playermatchstats.csv")
            if pf.exists():
                p = _read(pf)
                p["competition"] = p["match_id"].map(comp_of).fillna(_competition(None, mf))
                p["season"] = season
                p["player_code"] = p["player_id"].map(code)
                pm_rows.append(p)
    tm = pd.concat(tm_rows, ignore_index=True) if tm_rows else pd.DataFrame()
    pm = pd.concat(pm_rows, ignore_index=True) if pm_rows else pd.DataFrame()
    if not tm.empty:
        keep = ["season", "competition", "gameweek", "match_id", "kickoff_time", "home_team",
                "away_team", "home_score", "away_score", "home_team_elo", "away_team_elo",
                "home_expected_goals_xg", "away_expected_goals_xg", "finished"]
        tm = tm[[c for c in keep if c in tm]].drop_duplicates(["season", "match_id"])
        # mixed formats (with and without an offset): ISO8601 parses both
        tm["kickoff_time"] = pd.to_datetime(tm["kickoff_time"], utc=True, format="ISO8601",
                                            errors="coerce")
    if not pm.empty:
        pm = pm.rename(columns=CI_PLAYER_COLS)
        keep = ["season", "competition", "match_id", "player_id", "player_code",
                *CI_PLAYER_COLS.values()]
        pm = pm[[c for c in keep if c in pm]].drop_duplicates(["season", "match_id", "player_id"])
        pm = pm[pm["minutes"].fillna(0) > 0].copy()
        # Core-Insights leaves counts and xG blank (not 0) for players who registered none
        zero = [c for c in ("goals", "assists_official", "xg", "xa", "shots", "key_passes",
                            "tackles", "interceptions", "recoveries", "blocks", "clearances",
                            "box_touches", "xgot") if c in pm]
        pm[zero] = pm[zero].fillna(0)
    return pm, tm


def team_map(fpl: pd.DataFrame, us: pd.DataFrame) -> pd.DataFrame:
    """FPL team names (canonical) -> Understat names, for PL clubs. Curated aliases first,
    then exact matches."""
    alias = {"Man City": "Manchester City", "Man Utd": "Manchester United",
             "Newcastle": "Newcastle United", "Spurs": "Tottenham",
             "Wolves": "Wolverhampton Wanderers",
             "Nott'm Forest": "Nottingham Forest", "Sheffield Utd": "Sheffield United",
             "West Brom": "West Bromwich Albion", "Leicester": "Leicester",
             "Brighton": "Brighton", "Leeds": "Leeds", "Norwich": "Norwich",
             "Huddersfield": "Huddersfield", "Cardiff": "Cardiff", "Luton": "Luton",
             "Ipswich": "Ipswich", "Hull": "Hull", "Stoke": "Stoke", "Swansea": "Swansea",
             "Middlesbrough": "Middlesbrough", "Sunderland": "Sunderland",
             "Coventry": "Coventry"}
    us_names = set(us["h_team"]) | set(us["a_team"])
    rows = []
    for t in sorted(set(fpl["team"].dropna()) | set(fpl["opponent"].dropna())):
        u = alias.get(t, t)
        rows.append({"fpl_team": t, "understat_team": u if u in us_names else None})
    return pd.DataFrame(rows)


def fixtures_results(fpl: pd.DataFrame) -> pd.DataFrame:
    """One row per PL fixture with score, from the FPL per-GW history (all seasons)."""
    f = fpl.dropna(subset=["team_h_score", "team_a_score", "team", "opponent"])
    home = f[f["was_home"]].drop_duplicates(["season", "fixture"])
    return pd.DataFrame({
        "season": home["season"], "date": home["kickoff_time"], "league": "EPL",
        "home": home["team"], "away": home["opponent"],
        "home_goals": home["team_h_score"].astype(int),
        "away_goals": home["team_a_score"].astype(int),
    }).sort_values("date").reset_index(drop=True)


def results_from_fixtures(fixtures, bootstrap, season: str) -> pd.DataFrame:
    """Current-season PL results from the live FPL fixtures feed (finished matches)."""
    teams = {t.id: t.name for t in bootstrap.teams}
    rows = [{"season": season, "date": pd.Timestamp(f.kickoff_time), "league": "EPL",
             "home": teams[f.team_h], "away": teams[f.team_a],
             "home_goals": int(f.team_h_score), "away_goals": int(f.team_a_score)}
            for f in fixtures if (f.finished or getattr(f, "finished_provisional", False))
            and f.team_h_score is not None and f.kickoff_time is not None]
    return pd.DataFrame(rows)


def understat_results(us: pd.DataFrame, tmap: pd.DataFrame) -> pd.DataFrame:
    """Non-PL league results seen in Understat career rows (partial: only matches that
    involved a tracked player's team)."""
    m = us[us["league"] != "EPL"].drop_duplicates("match_id")
    return pd.DataFrame({"season": m["season_start"].astype(str), "date": m["date"],
                         "league": m["league"], "home": m["h_team"], "away": m["a_team"],
                         "home_goals": m["h_goals"], "away_goals": m["a_goals"]})


__all__ = ["ci_tables", "fixtures_results", "fpl_gw", "team_map", "understat_match",
           "understat_results", "understat_team_match"]
