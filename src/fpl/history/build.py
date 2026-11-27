"""``fpl history fetch | build | report``: rebuild the historical layer from scratch.

fetch  -> data/external/ (sparse git clones; live Understat/ClubElo probed, optional)
build  -> data/history/*.parquet + id_map_review.csv + COVERAGE.md
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from fpl.history import elo as E
from fpl.history import idmap as I
from fpl.history import normalize as N
from fpl.history import tables as T
from fpl.history.sources import clubelo, core_insights, understat, vaastav

TABLES = ("player_match", "player_season", "team_season", "team_elo", "id_map", "team_map",
          "fpl_gw", "understat_match", "ci_team_match")


def fetch(external: Path) -> dict:
    status = {"vaastav": vaastav.fetch(external), "core_insights": core_insights.fetch(external),
              "understat": understat.probe(),
              "clubelo": clubelo.fetch(external, [datetime.now(UTC).strftime("%Y-%m-%d")])}
    (external / "fetch_status.json").write_text(json.dumps(status, indent=1, default=str))
    return status


def build(external: Path, out: Path, current: pd.DataFrame | None = None,
          bootstrap=None, fixtures=None) -> dict[str, pd.DataFrame]:
    fpl = N.fpl_gw(external / "vaastav")
    if current is not None and bootstrap is not None and len(current):
        season = _current_season_label(bootstrap)
        cur = N.current_season_fpl(current, bootstrap, season)
        fpl = pd.concat([fpl[fpl["season"] != season], cur], ignore_index=True)
    us = N.understat_match(external / "vaastav")
    us_team = N.understat_team_match(external / "vaastav")
    ci_pm, ci_tm = N.ci_tables(external / "core_insights")
    ids = I.build(fpl, us, external / "vaastav")
    results = N.fixtures_results(fpl)
    if fixtures is not None and bootstrap is not None:
        season = _current_season_label(bootstrap)
        results = pd.concat([results[results["season"] != season],
                             N.results_from_fixtures(fixtures, bootstrap, season)])
    results = pd.concat([results, N.understat_results(us, None)], ignore_index=True)
    elo = E.run(results)
    code_team = (fpl.sort_values("kickoff_time").groupby(["season", "code"])["team"].last()
                 .reset_index())
    pm = T.player_match(fpl, us, ids.links, ci_pm, ci_tm, code_team, elo)
    tables = {
        "player_match": pm,
        "player_season": T.player_season(pm),
        "team_season": T.team_season(results, pm, us_team, E.season_summary(elo)),
        "team_elo": elo,
        "id_map": ids.links,
        "team_map": N.team_map(fpl, us),
        "fpl_gw": fpl,
        "understat_match": us,
        "ci_team_match": ci_tm,
    }
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False, compression="zstd")
    ids.review.to_csv(out / "id_map_review.csv", index=False)
    rep = I.match_report(fpl, us, ids.links)
    rep.to_csv(out / "id_map_report.csv", index=False)
    (out / "COVERAGE.md").write_text(coverage_md(tables, rep, ids, external))
    return tables


def _current_season_label(bootstrap) -> str:
    first = min(e.deadline_time for e in bootstrap.events)
    y = first.year
    return f"{y}-{str(y + 1)[-2:]}"


def coverage_md(t: dict[str, pd.DataFrame], rep: pd.DataFrame, ids: I.IdMapResult,
                external: Path) -> str:
    status = {}
    sp = external / "fetch_status.json"
    if sp.exists():
        status = json.loads(sp.read_text())
    pm = t["player_match"]
    lines = ["# Historical data coverage", "",
             f"Built {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}.", "", "## Sources", ""]
    for k, v in status.items():
        lines.append(f"- **{k}**: " + ", ".join(f"{a}={b}" for a, b in v.items()))
    lines += ["", "## player_match rows by season and competition", "",
              pm.pivot_table(index="season", columns="competition", values="code",
                             aggfunc="size", fill_value=0).to_markdown(), "",
              "## Source mix in PL rows (1 FPL, 2 Understat, 4 Core-Insights)", "",
              pm[pm["competition"] == "Premier League"].pivot_table(
                  index="season", columns="source_mask", values="code", aggfunc="size",
                  fill_value=0).to_markdown(), "",
              "## ID map", "",
              f"{len(ids.links)} Understat players linked "
              f"({ids.links['method'].str.split().str[0].value_counts().to_dict()}); "
              f"{len(ids.review)} candidate rows for review; {len(ids.unmatched)} unmatched.", "",
              rep.to_markdown(index=False), "",
              "## Known gaps", "",
              "- Understat (live) unavailable at build time unless stated above: no Understat "
              "rows for 2025-26 onwards; pre-PL careers of players who joined since 2025 missing.",
              "- 2016-17 to 2019-20: Understat rows only for players still in the PL in "
              "2021-25 (the mirror's player files start in the 2021-22 folder).",
              "- ClubElo unreachable (HTTP-only); team Elo is our own (validated against "
              "Core-Insights Elo for 2025-26). Non-PL Elo pools are partial (only clubs/matches "
              "seen in tracked players' careers) and not linked across leagues.",
              "- Core-Insights: 2025-26 onward only; European/cup opponents are not FPL teams, "
              "so cup rows carry no opponent Elo.",
              "- FPL xG exists from 2022-23; defensive-contribution counts from 2025-26.", ""]
    return "\n".join(lines)
