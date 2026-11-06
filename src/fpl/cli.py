"""Command-line entry point: ``uv run fpl --help``."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from fpl.api import endpoints as ep
from fpl.api.client import FPLAPIError, FPLClient
from fpl.api.models import Bootstrap, Entry, EntryHistory, Event, Fixture
from fpl.config import get_settings
from fpl.rules import GameRules
from fpl.state import ManagerState, build_state
from fpl.ui.dashboard import build_datasets, write_datasets

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()

EntryOpt = Annotated[
    int | None, typer.Option("--entry", help="FPL entry id (default FPL_ENTRY_ID)")
]
OfflineOpt = Annotated[bool, typer.Option("--offline", help="Read latest snapshots, no network")]


def _m(v: int) -> str:
    return f"£{v / 10:.1f}m"


@app.command()
def doctor(entry: EntryOpt = None) -> None:
    """Hit every endpoint the engine depends on and report status and key fields."""
    settings = get_settings()
    eid = entry or settings.entry_id
    failures = 0
    with FPLClient(settings) as c:
        b = ep.bootstrap_static(c)
        cur = b.current_event()
        gw = cur.id if cur else 1
        checks = {
            "bootstrap-static": lambda: (
                f"{len(b.elements)} players, {len(b.teams)} teams, current GW{gw}, "
                f"next deadline {b.next_event().deadline_time if b.next_event() else '-'}"
            ),
            "fixtures": lambda: (
                lambda f: f"{len(f)} fixtures, {sum(x.finished for x in f)} finished"
            )(ep.fixtures(c)),
            f"element-summary/{b.elements[0].id}": lambda: (
                lambda s: f"{len(s.history)} matches, last xG {s.history[-1].expected_goals}"
                if s.history else "no matches"
            )(ep.element_summary(c, b.elements[0].id)),
            f"event/{gw}/live": lambda: f"{len(ep.event_live(c, gw)['elements'])} elements",
            "event-status": lambda: str(ep.event_status(c)["leagues"]),
            f"entry/{eid}": lambda: (
                lambda e: f"{e.name}, OR {e.summary_overall_rank}, bank {_m(e.last_deadline_bank)}"
            )(ep.entry(c, eid)),
            f"entry/{eid}/history": lambda: (
                lambda h: f"{len(h.current)} GWs, chips {[(x.name, x.event) for x in h.chips]}"
            )(ep.entry_history(c, eid)),
            f"entry/{eid}/event/{gw}/picks": lambda: (
                lambda p: f"{len(p.picks)} picks, chip {p.active_chip}, "
                f"C={next(x.element for x in p.picks if x.is_captain)}"
            )(ep.entry_picks(c, eid, gw)),
            f"entry/{eid}/transfers": lambda: f"{len(ep.entry_transfers(c, eid))} transfers",
            "leagues-classic/314/standings": lambda: (
                lambda d: f"top entry rank {d['standings']['results'][0]['rank']}, "
                f"has_next {d['standings']['has_next']}"
            )(ep.league_classic_standings(c, 314)),
        }
        t = Table("endpoint", "status", "detail", title="FPL API doctor")
        for name, fn in checks.items():
            t0 = time.monotonic()
            try:
                detail = fn()
                t.add_row(name, f"[green]ok[/] {time.monotonic() - t0:.1f}s", detail)
            except (FPLAPIError, KeyError, IndexError, ValueError) as exc:
                failures += 1
                t.add_row(name, "[red]FAIL[/]", str(exc)[:120])
        console.print(t)
        console.print(f"snapshots written under {settings.raw_dir}")
    raise typer.Exit(code=1 if failures else 0)


def deadline_dir(data_dir: Path, gw: int) -> Path:
    return data_dir / "deadlines" / f"gw{gw:02d}"


@app.command()
def fetch(
    entry: EntryOpt = None,
    full: Annotated[
        bool, typer.Option(help="Also every finished GW's live data, all picks, all summaries")
    ] = False,
    pre_deadline_hours: Annotated[
        float,
        typer.Option(help="If the next deadline is within N hours, also file a tagged copy "
                          "under data/deadlines/gwNN/ (the as-of-deadline record)"),
    ] = 0.0,
) -> None:
    """Snapshot the time-sensitive state: prices, flags, ownership, price predictions, fixtures,
    the latest GW's live data and the entry's endpoints. Cheap enough to run several times a day."""
    settings = get_settings()
    eid = entry or settings.entry_id
    with FPLClient(settings) as c:
        b = ep.bootstrap_static(c)
        ep.fixtures(c)
        ep.event_status(c)
        finished = [e.id for e in b.events if e.finished]
        for gw in finished if full else finished[-1:]:
            ep.event_live(c, gw)
        ep.entry(c, eid)
        h = ep.entry_history(c, eid)
        for row in h.current if full else h.current[-1:]:
            ep.entry_picks(c, eid, row.event)
        ep.entry_transfers(c, eid)
        if full:
            with console.status("element summaries..."):
                for el in b.elements:
                    ep.element_summary(c, el.id)
        written = list(c.written)
    console.print(f"[green]snapshotted[/] {len(written)} files -> {settings.raw_dir}")

    nxt = b.next_event()
    if pre_deadline_hours and nxt:
        dest = file_pre_deadline_copy(
            written, settings.data_dir, nxt, datetime.now(UTC), pre_deadline_hours
        )
        hours_left = (nxt.deadline_time - datetime.now(UTC)).total_seconds() / 3600
        console.print(
            f"[green]pre-deadline copy[/] {nxt.name} -> {dest}" if dest
            else f"next deadline in {hours_left:.1f}h: no pre-deadline copy"
        )


def file_pre_deadline_copy(
    written: list[Path], data_dir: Path, next_event: Event, now: datetime, window_hours: float
) -> Path | None:
    """Copy this run's snapshot files to data/deadlines/gwNN/ if the deadline is within the
    window. Later runs inside the window overwrite, so the copy is the last one pre-deadline."""
    hours_left = (next_event.deadline_time - now).total_seconds() / 3600
    if not 0 < hours_left <= window_hours:
        return None
    dest = deadline_dir(data_dir, next_event.id)
    dest.mkdir(parents=True, exist_ok=True)
    for f in written:
        shutil.copy2(f, dest / f.name)
    (dest / "captured_at.txt").write_text(
        f"{now.isoformat()} ({hours_left:.2f}h before {next_event.name} deadline "
        f"{next_event.deadline_time.isoformat()})\n"
    )
    return dest


@dataclass
class Context:
    state: ManagerState
    rules: GameRules
    bootstrap: Bootstrap
    fixtures: list[Fixture]
    entry: Entry
    history: EntryHistory


def load_context(eid: int, offline: bool = False) -> Context:
    with FPLClient(offline=offline) as c:
        b = ep.bootstrap_static(c)
        e = ep.entry(c, eid)
        h = ep.entry_history(c, eid)
        latest = max(r.event for r in h.current)
        picks = ep.entry_picks(c, eid, latest)
        tr = ep.entry_transfers(c, eid)
        fx = ep.fixtures(c)
        rules = GameRules.from_bootstrap(b)

        def initial_price(element: int, gw: int) -> int:
            hist = ep.element_summary(c, element).history
            return next(r.value for r in hist if r.round == gw)

        st = build_state(
            bootstrap=b, entry=e, history=h, picks=picks, transfers=tr, rules=rules,
            initial_price_lookup=initial_price,
        )
    return Context(st, rules, b, fx, e, h)


@app.command("build-tables")
def build_tables_cmd(offline: OfflineOpt = False) -> None:
    """Fetch every player's match history and write player/team match tables (parquet)."""
    from fpl.data.tables import build_tables

    settings = get_settings()
    out = settings.data_dir / "processed"
    with FPLClient(settings, offline=offline) as c, console.status("element summaries..."):
        tables = build_tables(c, out)
    for name, df in tables.items():
        console.print(f"{name}: {len(df):,} rows -> {out / (name + '.parquet')}")


@app.command("backtest-teams")
def backtest_teams(
    tune: Annotated[str, typer.Option(help="Rounds to tune on, e.g. 2-6")] = "2-6",
    test: Annotated[str, typer.Option(help="Held-out rounds to score, e.g. 7-9")] = "7-9",
) -> None:
    """Tune and backtest the team-strength model vs naive baselines; write a JSON report."""
    import json

    import pandas as pd

    from fpl.backtest.team_strength import grid_search, predictions, score
    from fpl.data.priors import team_prior
    from fpl.models.team_strength import TeamStrengthParams as P

    def rng(s: str) -> list[int]:
        a, b = (int(x) for x in s.split("-"))
        return list(range(a, b + 1))

    settings = get_settings()
    tm = pd.read_parquet(settings.data_dir / "processed" / "team_matches.parquet")
    with FPLClient(settings) as c:
        prior = team_prior(ep.bootstrap_static(c), settings.data_dir)
    grid = [P(r, h, g) for r in (3.0, 10.0, 30.0, 100.0) for h in (None, 45.0)
            for g in (0.0, 0.3)]
    gs = grid_search(tm, rng(tune), grid, prior)
    b = gs.iloc[0]
    best = P(float(b.ridge), None if pd.isna(b.half_life_days) else float(b.half_life_days),
             float(b.goal_weight))
    held = score(predictions(tm, best, rng(test), prior))
    console.print(f"chosen on GW{tune}: {best}")
    console.print(held.round(4).to_string(index=False))
    out = settings.data_dir / "processed" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    report = {"model": "team_strength", "tune_rounds": tune, "test_rounds": test,
              "params": best.__dict__, "holdout": held.to_dict("records"),
              "created_utc": datetime.now(UTC).isoformat()}
    (out / "team_strength.json").write_text(json.dumps(report, indent=1))
    console.print(f"report -> {out / 'team_strength.json'}")


@app.command()
def ui() -> None:
    """Open the local what-if app (Streamlit) in your browser."""
    import subprocess
    import sys

    app_path = Path(__file__).parent / "ui" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)], check=False)


def load_state(eid: int, offline: bool = False) -> tuple[ManagerState, GameRules]:
    ctx = load_context(eid, offline)
    return ctx.state, ctx.rules


def _engine_inputs(refresh_tables: bool, offline: bool):
    import pandas as pd

    from fpl.data.tables import build_tables

    settings = get_settings()
    proc = settings.data_dir / "processed"
    if refresh_tables and not offline:
        with FPLClient(settings) as c, console.status("refreshing match tables..."):
            build_tables(c, proc)
    return (pd.read_parquet(proc / "player_matches.parquet"),
            pd.read_parquet(proc / "team_matches.parquet"))


def _eo(gw: int, sample: int, offline: bool):
    """Sample top-10k EO, or reuse the last sample when offline / sample=0."""
    import json

    import pandas as pd

    from fpl.data.ownership import effective_ownership

    settings = get_settings()
    path = settings.data_dir / "processed" / "eo.parquet"
    meta_path = path.with_suffix(".json")
    if sample and not offline:
        with FPLClient(settings) as c, console.status(f"sampling top-10k picks ({sample})..."):
            eo, meta = effective_ownership(c, gw, sample=sample)
        eo.to_parquet(path)
        meta_path.write_text(json.dumps(meta))
        return eo, meta
    if path.exists():
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
        return pd.read_parquet(path), meta
    return None, None


@app.command("dashboard-data")
def dashboard_data(
    out: Annotated[Path, typer.Option("--out", help="Directory for the dataset JSON files")],
    entry: EntryOpt = None,
    offline: OfflineOpt = False,
    engine: Annotated[bool, typer.Option(help="Run models, gate, optimizer, chips")] = True,
    refresh_tables: Annotated[bool, typer.Option(help="Re-fetch match tables first")] = True,
    eo_sample: Annotated[int, typer.Option(help="Top-10k entries to sample (0 = reuse)")] = 500,
    chips: Annotated[bool, typer.Option(help="Run the chip planner (~2 min)")] = True,
) -> None:
    """Write every dashboard dataset as JSON: current state, and (if the backtest gate
    passes) decisions, line-up, captaincy, plan, players, fixtures and chip gains."""
    eid = entry or get_settings().entry_id
    ctx = load_context(eid, offline)
    last = max(ctx.history.current, key=lambda r: r.event)
    data = build_datasets(
        ctx.bootstrap, ctx.state, ctx.fixtures, datetime.now(UTC),
        last_gw_points=last.points, total_points=last.total_points,
    )
    if engine:
        from fpl.report.build import build

        pm, tm = _engine_inputs(refresh_tables, offline)
        cur = ctx.bootstrap.current_event()
        eo, meta = _eo(cur.id if cur else 1, eo_sample, offline)
        with console.status("backtests, forecast, optimizer, chips..."):
            data.update(build(ctx.bootstrap, ctx.fixtures, pm, tm, ctx.state, ctx.rules,
                              get_settings().data_dir, eo, meta, chips=chips))
        gate = data["assumptions"][0]["gate"]
        console.print(f"backtest gate: {gate}")
        reviews = _reviews(ctx, pm, offline)
        if reviews:
            from fpl.report.ledger import summarise

            data["review"] = reviews
            data["review_summary"] = [summarise(reviews)]
            console.print(f"graded GWs: {[r['gw'] for r in reviews]}")
    ledger_file = get_settings().data_dir / "ledger" / f"gw{ctx.state.next_gw:02d}.json"
    for p in write_datasets(data, out, ledger_file if ledger_file.exists() else None):
        console.print(f"wrote {p} ({len(data[p.stem])} rows)")
    for w in ctx.state.warnings:
        console.print(f"[yellow]warning:[/] {w}")


def _reviews(ctx: Context, pm, offline: bool) -> list[dict]:
    """Grade every finished, data-checked GW that has a ledger entry."""
    from fpl.report.ledger import load_all, review

    entries = load_all(get_settings().data_dir / "ledger")
    events = {e.id: e for e in ctx.bootstrap.events}
    hist = {h.event: h for h in ctx.history.current}
    fh = {c.event for c in ctx.history.chips if c.name == "freehit"}
    rows = []
    with FPLClient(offline=offline) as c:
        transfers = ep.entry_transfers(c, ctx.entry.id)
        for gw, entry in sorted(entries.items()):
            ev = events.get(gw)
            if ev is None or not (ev.finished and ev.data_checked):
                continue
            moves = [(t.element_in, t.element_out) for t in transfers
                     if t.event == gw and gw not in fh]
            rows.append(review(entry, ep.event_live(c, gw), pm,
                               hist[gw].points if gw in hist else None,
                               getattr(ev, "average_entry_score", None), moves))
    return rows


@app.command()
def review(gw: Annotated[int, typer.Option(help="Gameweek to grade")]) -> None:
    """Grade one finished GW's published forecast and advice (needs data/ledger/gwNN.json)."""
    import json

    import pandas as pd

    ctx = load_context(get_settings().entry_id)
    pm = pd.read_parquet(get_settings().data_dir / "processed" / "player_matches.parquet")
    rows = [r for r in _reviews(ctx, pm, False) if r["gw"] == gw]
    if not rows:
        console.print(f"GW{gw}: no ledger entry, or the gameweek is not finished and checked")
        raise typer.Exit(1)
    console.print_json(json.dumps(rows[0]))


@app.command()
def plan(
    entry: EntryOpt = None,
    horizon: Annotated[int, typer.Option(help="GWs to plan over")] = 6,
    refresh_tables: Annotated[bool, typer.Option(help="Re-fetch match tables first")] = False,
) -> None:
    """Decision report in the terminal: options with Δ vs rolling, captaincy, line-up."""
    from fpl import engine as eng

    eid = entry or get_settings().entry_id
    ctx = load_context(eid)
    pm, tm = _engine_inputs(refresh_tables, False)
    eo, meta = _eo(0, 0, True)
    eo_map = eo.set_index("element")["eo_pct"].to_dict() if eo is not None else {}
    res = eng.run(ctx.bootstrap, ctx.fixtures, pm, tm, ctx.state, ctx.rules,
                  get_settings().data_dir, horizon=horizon)
    nm = res.players["name"].to_dict()
    console.print(f"[bold]GW{res.gws[0]} decision[/] (horizon GW{res.gws[0]}–{res.gws[-1]}, "
                  f"decay {res.settings.decay}; Δ in decayed xPts incl. hits)")
    t = Table("option", "in", "out", "Δ vs roll", "next GW xPts", "top-10k EO in/out")
    for o in sorted(res.options, key=lambda o: -o.delta_vs_roll):
        t.add_row(o.label, ", ".join(nm[p] for p in o.transfers_in) or "-",
                  ", ".join(nm[p] for p in o.transfers_out) or "-",
                  f"{o.delta_vs_roll:+.2f}", f"{o.plan.gws[0].xpts - 4 * o.hits:.1f}",
                  " / ".join(f"{eo_map.get(p, 0):.0f}%" for p in o.transfers_in + o.transfers_out))
    console.print(t)
    g = res.best.gws[0]
    c = Table("player", "mean", "p10–p90", "P(10+)", "top-10k EO", title="Captaincy")
    for r in res.captaincy.head(5).itertuples():
        c.add_row(nm[r.element], f"{r.mean:.2f}", f"{r.p10:.0f}–{r.p90:.0f}",
                  f"{r.p_haul:.0%}", f"{eo_map.get(int(r.element), 0):.0f}%")
    console.print(c)
    console.print(f"Captain {nm[g.captain]}, vice {nm[g.vice]}. Bench: "
                  + ", ".join(nm[p] for p in g.bench))
    if meta:
        console.print(f"EO: top {meta['top_k']:,}, {meta['sampled']} sampled, GW{meta['gw']} picks")


@app.command()
def state(entry: EntryOpt = None, offline: OfflineOpt = False) -> None:
    """Print squad, purchase/selling prices, bank, free transfers and remaining chips."""
    eid = entry or get_settings().entry_id
    st, _ = load_state(eid, offline)
    console.print(
        f"[bold]{st.entry_name}[/] ({st.entry_id}) · after GW{st.latest_gw} · "
        f"OR {st.overall_rank:,} · planning GW{st.next_gw}"
    )
    t = Table("slot", "player", "pos", "team", "bought", "now", "sell", "source")
    for p in sorted(st.squad, key=lambda p: p.squad_slot or 99):
        t.add_row(
            str(p.squad_slot or "new"), p.web_name, p.position, p.team, _m(p.purchase_price),
            _m(p.now_cost), _m(p.selling_price), p.purchase_source,
        )
    console.print(t)
    console.print(
        f"bank {_m(st.bank)} · selling value {_m(st.selling_value)} · market value "
        f"{_m(st.market_value)} · recorded value {_m(st.recorded_value or 0)}"
    )
    console.print(
        f"free transfers for GW{st.next_gw}: [bold]{st.free_transfers}[/]"
        + (f" ({st.next_gw_transfers_made} already made)" if st.next_gw_transfers_made else "")
    )
    console.print("chips used: " + ", ".join(f"{n} GW{e}" for n, e in st.chips_used))
    console.print(
        "chips available: "
        + ", ".join(f"{w.name} (GW{w.start_event}-{w.stop_event})" for w in st.chips_available)
    )
    bad = [s for s in st.ft_replay if not s.consistent]
    console.print(
        f"FT replay self-check: {'[green]ok[/]' if not bad else '[red]MISMATCH[/]'} "
        f"over {len(st.ft_replay)} GWs"
    )
    for w in st.warnings:
        console.print(f"[yellow]warning:[/] {w}")


if __name__ == "__main__":
    app()
