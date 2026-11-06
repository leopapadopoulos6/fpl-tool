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


def load_state(eid: int, offline: bool = False) -> tuple[ManagerState, GameRules]:
    ctx = load_context(eid, offline)
    return ctx.state, ctx.rules


@app.command("dashboard-data")
def dashboard_data(
    out: Annotated[Path, typer.Option("--out", help="Directory for the dataset JSON files")],
    entry: EntryOpt = None,
    offline: OfflineOpt = False,
) -> None:
    """Write the phone dashboard's datasets (summary, squad, chips, ft_history) as JSON."""
    eid = entry or get_settings().entry_id
    ctx = load_context(eid, offline)
    last = max(ctx.history.current, key=lambda r: r.event)
    data = build_datasets(
        ctx.bootstrap, ctx.state, ctx.fixtures, datetime.now(UTC),
        last_gw_points=last.points, total_points=last.total_points,
    )
    for p in write_datasets(data, out):
        console.print(f"wrote {p} ({len(data[p.stem])} rows)")
    for w in ctx.state.warnings:
        console.print(f"[yellow]warning:[/] {w}")


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
