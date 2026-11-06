"""Datasets for the phone dashboard (a claude.ai Dashboard page).

The page itself lives in the dashboard's store and only renders these rows; every figure on
it comes from one of the datasets built here. ``fpl dashboard-data`` writes them as JSON
files, which a session uploads to the dashboard (see CLAUDE.md, "Phone dashboard").

v0 holds only real current state: no model output until each model passes its backtest.
Money is exported in £m with one decimal (the engine itself keeps integer £0.1m).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fpl.api.models import Bootstrap, Element, Fixture
from fpl.state import ManagerState

CHIP_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost",
              "3xc": "Triple Captain"}
STATUS_NAMES = {"a": "Available", "d": "Doubtful", "i": "Injured", "s": "Suspended",
                "u": "Unavailable", "n": "Not in squad"}
# FPL's own price-change status (web app: likelihood of the next projection).
PRICE_LABELS = {5: "Very likely to rise", 4: "Likely to rise", -4: "Likely to drop",
                -5: "Very likely to drop"}
NO_PRICE_CHANGE = "Unlikely to change"
NEXT_FIXTURES = 5


def m(tenths: int) -> float:
    """£0.1m integer -> £m float with one decimal."""
    return round(tenths / 10, 1)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def price_status(el: Element) -> str:
    if el.price_change_locked_until is not None:
        return "Price locked"
    if el.price_change_calibrating:
        return "Calibrating"
    proj = el.price_change_projections
    return PRICE_LABELS.get(proj[0].likelihood, NO_PRICE_CHANGE) if proj else NO_PRICE_CHANGE


def availability(el: Element) -> str:
    label = STATUS_NAMES.get(el.status, el.status)
    if el.status != "a" and el.chance_of_playing_next_round is not None:
        return f"{label} ({el.chance_of_playing_next_round}%)"
    return label


def next_fixtures(
    team_id: int, from_gw: int, fixtures: list[Fixture], short: dict[int, str]
) -> str:
    """'CHE (A) · BUR (H) + LIV (A) · Blank · ...' for the next NEXT_FIXTURES GWs."""
    by_gw: dict[int, list[str]] = defaultdict(list)
    for f in sorted(fixtures, key=lambda f: (f.event or 0, f.kickoff_time or datetime.max)):
        if f.event is None or not from_gw <= f.event < from_gw + NEXT_FIXTURES:
            continue
        if f.team_h == team_id:
            by_gw[f.event].append(f"{short[f.team_a]} (H)")
        elif f.team_a == team_id:
            by_gw[f.event].append(f"{short[f.team_h]} (A)")
    return " · ".join(
        " + ".join(by_gw[gw]) if by_gw[gw] else "Blank"
        for gw in range(from_gw, min(from_gw + NEXT_FIXTURES, 39))
    )


def build_datasets(
    bootstrap: Bootstrap, state: ManagerState, fixtures: list[Fixture], now: datetime,
    *, last_gw_points: int | None = None, total_points: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    els = bootstrap.elements_by_id()
    short = {t.id: t.short_name for t in bootstrap.teams}
    events = {e.id: e for e in bootstrap.events}
    nxt = events.get(state.next_gw)

    hits = [s for s in state.ft_replay if s.recorded_cost]
    summary = [{
        "team": state.entry_name,
        "entry_id": state.entry_id,
        "overall_rank": state.overall_rank,
        "last_gw": state.latest_gw,
        "last_gw_points": last_gw_points,
        "total_points": total_points,
        "next_gw": state.next_gw,
        "deadline_utc": iso(nxt.deadline_time) if nxt else None,
        "bank_m": m(state.bank),
        "free_transfers": state.free_transfers,
        "transfers_made_next_gw": state.next_gw_transfers_made,
        "selling_value_m": m(state.selling_value),
        "market_value_m": m(state.market_value),
        "hit_points_season": sum(s.recorded_cost for s in hits),
        "hits_taken": len(hits),
        "as_of_utc": iso(now),
    }]

    squad = []
    for p in sorted(state.squad, key=lambda p: p.squad_slot or 99):
        el = els[p.element]
        slot = p.squad_slot
        squad.append({
            "element": p.element,
            "slot": slot,
            "role": "New" if slot is None else ("Starting" if slot <= 11 else f"Bench {slot - 11}"),
            "player": p.web_name,
            "pos": p.position,
            "team": p.team,
            "bought_m": m(p.purchase_price),
            "now_m": m(p.now_cost),
            "sell_m": m(p.selling_price),
            "locked_gain_m": m(p.selling_price - p.purchase_price),
            "availability": availability(el),
            "news": el.news or "",
            "price_status": price_status(el),
            "price_progress_pct": el.price_change_percent,
            "next_fixtures": next_fixtures(el.team, state.next_gw, fixtures, short),
            "owned_by_pct": el.selected_by_percent,
        })

    available = {(w.name, w.start_event) for w in state.chips_available}
    chips = []
    for w in sorted(bootstrap.chips, key=lambda c: (c.start_event, c.id)):
        used_in = next((e for n, e in state.chips_used if n == w.name
                        and w.start_event <= e <= w.stop_event), None)
        if used_in is not None:
            status = f"Used GW{used_in}"
        elif (w.name, w.start_event) in available:
            status = "Available" if w.start_event <= state.next_gw else "Not yet available"
        else:
            status = "Expired"
        stop = events.get(w.stop_event)
        start = events.get(w.start_event)
        chips.append({
            "chip": CHIP_NAMES.get(w.name, w.name),
            "set": "First half" if w.stop_event < 38 else "Second half",
            "window": f"GW{w.start_event}–{w.stop_event}",
            "status": status,
            "available_from_utc": iso(start.deadline_time) if start else None,
            "use_by_utc": iso(stop.deadline_time) if stop else None,
        })

    ft_history = [{
        "gw": s.event,
        "ft_available": s.ft_available,
        "transfers": s.transfers,
        "hit_points": s.recorded_cost,
        "chip": CHIP_NAMES.get(s.chip, s.chip) if s.chip else "",
    } for s in state.ft_replay]

    return {"summary": summary, "squad": squad, "chips": chips, "ft_history": ft_history}


SOURCE_NOTE = "From the public FPL API, computed by the fpl-tool engine (entry 1020085)."
DATASET_META = {
    "summary": ("Team summary", "Rank, bank, free transfers, team value and hits."),
    "squad": ("Squad and prices", "Your 15 players with purchase, current and selling prices, "
              "availability, FPL price status and next five fixtures."),
    "chips": ("Chips", "Each chip in each half of the season, its window, and whether it is used."),
    "ft_history": ("Free transfers by gameweek", "Free transfers available, transfers made and "
                   "hit points per gameweek, replayed from your history."),
    "decision": ("Transfer options", "Each transfer option for the next gameweek with its "
                 "expected-points difference against rolling the transfer, over the planning "
                 "horizon, with ownership flags."),
    "captaincy": ("Captaincy simulation", "Simulated points this gameweek for the leading "
                  "captain options: average, 10th and 90th percentiles, chance of 10+, and "
                  "top-10k effective ownership."),
    "lineup": ("Line-up and bench", "Recommended starting eleven, captain, vice-captain and "
               "bench order, with expected points and start chance."),
    "plan": ("Transfer plan", "The recommended transfer path for each gameweek of the horizon."),
    "chip_gain": ("Chip gains", "Expected gain from each remaining first-half chip in each "
                  "gameweek up to GW19, versus not playing it."),
    "players": ("Player forecasts", "Expected points for the next six gameweeks for your players "
                "and the leading options per position, with start chance, price and top-10k "
                "ownership."),
    "fixtures_strength": ("Fixture ratings", "Our own expected goals for and against and "
                          "clean-sheet chance for each team's next fixtures, from xG-based "
                          "team ratings."),
    "model_health": ("Model health", "Backtest of each model on past gameweeks it had not seen, "
                     "against simple baselines."),
    "calibration": ("Calibration", "Predicted against actual values, grouped, for expected points "
                    "and start probability."),
    "chip_plan": ("Chip plan", "Best gameweek for each remaining first-half chip when the chips "
                  "are planned together, with the value of the next-best gameweek."),
    "chip_summary": ("Chip plan summary", "Total value of the chip plan, the next-best plan and "
                     "the planning horizon."),
    "review": ("Track record", "Each finished gameweek graded against the forecast and advice "
               "published before its deadline."),
    "review_summary": ("Track record summary", "Totals across all graded gameweeks."),
    "assumptions": ("Assumptions", "Model version, horizon, decay, hit hurdle, ownership "
                    "source and whether the backtest gate passed."),
}
MODEL_DATASETS = ("decision", "captaincy", "lineup", "plan", "chip_gain", "chip_plan",
                  "chip_summary", "players", "fixtures_strength")


def write_datasets(datasets: dict[str, list[dict[str, Any]]], out_dir: Path,
                   ledger_file: Path | None = None) -> list[Path]:
    """Write one JSON file per dataset plus ``manifest.json`` for the refresh routine:
    ``datasets`` (id, file, title, description) and ``remove`` (model datasets withheld
    because the backtest gate failed, so stale recommendations are taken down)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths, entries = [], []
    for name, rows in datasets.items():
        p = out_dir / f"{name}.json"
        p.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        paths.append(p)
        title, desc = DATASET_META.get(name, (name, ""))
        entries.append({"id": name, "file": p.name, "title": title,
                        "description": f"{desc} {SOURCE_NOTE}".strip()})
    gate_failed = "assumptions" in datasets and "decision" not in datasets
    manifest: dict[str, Any] = {"datasets": entries,
                                "remove": list(MODEL_DATASETS) if gate_failed else []}
    if ledger_file is not None:
        dest = out_dir / f"ledger_{ledger_file.stem}.json"
        dest.write_text(ledger_file.read_text())
        manifest["ledger"] = {"collection": "ledger", "doc_id": ledger_file.stem,
                              "file": dest.name}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return paths
