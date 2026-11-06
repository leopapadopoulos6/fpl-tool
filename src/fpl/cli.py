"""Command-line entry point: ``uv run fpl --help``."""

from __future__ import annotations

import time
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from fpl.api import endpoints as ep
from fpl.api.client import FPLAPIError, FPLClient
from fpl.config import get_settings
from fpl.rules import GameRules
from fpl.state import ManagerState, build_state

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


@app.command()
def fetch(
    entry: EntryOpt = None,
    summaries: Annotated[bool, typer.Option(help="Also fetch element-summary for every player")]
    = False,
) -> None:
    """Snapshot bootstrap, fixtures, all finished GW live data, and the entry's endpoints."""
    settings = get_settings()
    eid = entry or settings.entry_id
    with FPLClient(settings) as c:
        b = ep.bootstrap_static(c)
        ep.fixtures(c)
        finished = [e.id for e in b.events if e.finished]
        for gw in finished:
            ep.event_live(c, gw)
        ep.entry(c, eid)
        h = ep.entry_history(c, eid)
        for row in h.current:
            ep.entry_picks(c, eid, row.event)
        ep.entry_transfers(c, eid)
        if summaries:
            with console.status("element summaries..."):
                for el in b.elements:
                    ep.element_summary(c, el.id)
    console.print(f"[green]snapshotted[/] {len(finished)} live GWs -> {settings.raw_dir}")


def load_state(eid: int, offline: bool = False) -> tuple[ManagerState, GameRules]:
    with FPLClient(offline=offline) as c:
        b = ep.bootstrap_static(c)
        e = ep.entry(c, eid)
        h = ep.entry_history(c, eid)
        latest = max(r.event for r in h.current)
        picks = ep.entry_picks(c, eid, latest)
        tr = ep.entry_transfers(c, eid)
        rules = GameRules.from_bootstrap(b)

        def initial_price(element: int, gw: int) -> int:
            hist = ep.element_summary(c, element).history
            return next(r.value for r in hist if r.round == gw)

        st = build_state(
            bootstrap=b, entry=e, history=h, picks=picks, transfers=tr, rules=rules,
            initial_price_lookup=initial_price,
        )
    return st, rules


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
