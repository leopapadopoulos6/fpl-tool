"""Reconstruct the manager's decision state from public endpoints only.

- Squad: picks for the latest started GW, with any already-confirmed transfers for the
  next GW applied on top. A chip pending for the next GW is not visible publicly.
- Purchase price: cost of the most recent *permanent* transfer in (Free Hit GW transfers
  are excluded, since the squad reverts). Players held since the manager's first GW use their
  price at that GW.
- Selling price: ``GameRules.selling_price``.
- Free transfers: replayed GW by GW from ``entry/{id}/history``, with a self-check that the
  derived hit cost equals the recorded ``event_transfers_cost`` in every GW.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fpl.api.models import Bootstrap, Entry, EntryHistory, Picks, Transfer
from fpl.rules import FT_PER_GW, FT_RETAINING_CHIPS, HIT_COST, ChipWindow, GameRules


@dataclass(frozen=True)
class SquadPlayer:
    element: int
    web_name: str
    position: str
    team: str
    purchase_price: int
    now_cost: int
    selling_price: int
    purchase_source: str  # "transfer GW<n>" | "initial squad"
    squad_slot: int | None  # 1-15 pick position at the latest GW (None if bought for next GW)


@dataclass(frozen=True)
class FTStep:
    event: int
    ft_available: int
    transfers: int
    chip: str | None
    derived_cost: int
    recorded_cost: int

    @property
    def consistent(self) -> bool:
        return self.chip in FT_RETAINING_CHIPS or self.derived_cost == self.recorded_cost


@dataclass
class ManagerState:
    entry_id: int
    entry_name: str
    latest_gw: int
    next_gw: int
    squad: list[SquadPlayer]
    bank: int
    free_transfers: int  # available for next_gw after any already-made next-GW transfers
    next_gw_transfers_made: int
    chips_used: list[tuple[str, int]]
    chips_available: list[ChipWindow]
    ft_replay: list[FTStep]
    overall_rank: int | None
    recorded_value: int | None  # history 'value' for latest GW (squad value + bank)
    warnings: list[str] = field(default_factory=list)

    @property
    def selling_value(self) -> int:
        return sum(p.selling_price for p in self.squad) + self.bank

    @property
    def market_value(self) -> int:
        return sum(p.now_cost for p in self.squad) + self.bank


def replay_free_transfers(
    history: EntryHistory, rules: GameRules, started_event: int
) -> tuple[list[FTStep], int]:
    """Return per-GW replay and the FT count entering the GW after the last recorded one."""
    chips = {c.event: c.name for c in history.chips}
    steps: list[FTStep] = []
    ft = 0
    for h in sorted(history.current, key=lambda r: r.event):
        if h.event in rules.ft_overrides:
            ft = rules.ft_overrides[h.event]
        chip = chips.get(h.event)
        if h.event == started_event:
            steps.append(FTStep(h.event, 0, h.event_transfers, chip, 0, h.event_transfers_cost))
            ft = FT_PER_GW
            continue
        if chip in FT_RETAINING_CHIPS:
            steps.append(FTStep(h.event, ft, h.event_transfers, chip, 0, h.event_transfers_cost))
            continue  # banked FTs retained, no accrual for the chip GW
        paid = max(0, h.event_transfers - ft)
        steps.append(
            FTStep(h.event, ft, h.event_transfers, chip, paid * HIT_COST, h.event_transfers_cost)
        )
        ft = min(rules.max_free_transfers, max(0, ft - h.event_transfers) + FT_PER_GW)
    last = max((h.event for h in history.current), default=started_event - 1)
    if last + 1 in rules.ft_overrides:
        ft = rules.ft_overrides[last + 1]
    return steps, ft


def available_chips(
    history: EntryHistory, rules: GameRules, from_gw: int
) -> tuple[list[tuple[str, int]], list[ChipWindow]]:
    used = [(c.name, c.event) for c in history.chips]
    avail = []
    for w in rules.chip_windows:
        spent = any(n == w.name and w.contains(e) for n, e in used)
        if not spent and w.stop_event >= from_gw:
            avail.append(w)
    avail.sort(key=lambda w: (w.start_event, w.name))
    return used, avail


def build_state(
    *,
    bootstrap: Bootstrap,
    entry: Entry,
    history: EntryHistory,
    picks: Picks,
    transfers: list[Transfer],
    rules: GameRules | None = None,
    initial_price_lookup: Callable[[int, int], int] | None = None,
) -> ManagerState:
    """``initial_price_lookup(element, gw)`` is only needed if the manager joined after GW1."""
    rules = rules or GameRules.from_bootstrap(bootstrap)
    els = bootstrap.elements_by_id()
    teams = {t.id: t.short_name for t in bootstrap.teams}
    warnings: list[str] = []

    latest_gw = picks.entry_history.event
    nxt = bootstrap.next_event()
    next_gw = nxt.id if nxt else latest_gw + 1

    fh_events = {c.event for c in history.chips if c.name == "freehit"}
    permanent = sorted(
        (t for t in transfers if t.event not in fh_events), key=lambda t: (t.event, t.time)
    )

    # Squad at end of latest GW, then apply already-made next-GW transfers.
    slot = {p.element: p.position for p in picks.picks}
    squad_ids = [p.element for p in sorted(picks.picks, key=lambda p: p.position)]
    pending = [t for t in permanent if t.event >= next_gw]
    bank = picks.entry_history.bank
    for t in pending:
        if t.element_out in squad_ids:
            squad_ids[squad_ids.index(t.element_out)] = t.element_in
            bank += t.element_out_cost - t.element_in_cost
        else:
            warnings.append(f"pending transfer out {t.element_out} not in squad")

    last_in: dict[int, Transfer] = {}
    for t in permanent:
        last_in[t.element_in] = t

    squad: list[SquadPlayer] = []
    for eid in squad_ids:
        el = els[eid]
        if eid in last_in:
            purchase, src = last_in[eid].element_in_cost, f"transfer GW{last_in[eid].event}"
        elif entry.started_event == 1:
            purchase, src = el.now_cost - el.cost_change_start, "initial squad"
        elif initial_price_lookup is not None:
            purchase, src = initial_price_lookup(eid, entry.started_event), "initial squad"
        else:
            purchase, src = el.now_cost, "unknown (assumed now_cost)"
            warnings.append(f"purchase price unknown for {el.web_name}")
        squad.append(
            SquadPlayer(
                element=eid,
                web_name=el.web_name,
                position=rules.position_by_type[el.element_type],
                team=teams[el.team],
                purchase_price=purchase,
                now_cost=el.now_cost,
                selling_price=rules.selling_price(purchase, el.now_cost),
                purchase_source=src,
                squad_slot=slot.get(eid),
            )
        )

    steps, ft_next = replay_free_transfers(history, rules, entry.started_event)
    bad = [s for s in steps if not s.consistent]
    if bad:
        warnings.append(
            "FT replay disagrees with recorded hit cost in GW "
            + ", ".join(str(s.event) for s in bad)
            + " (rule change or FT top-up? set GameRules.ft_overrides)"
        )
    n_pending = len(pending)
    ft_remaining = max(0, ft_next - n_pending)

    used, avail = available_chips(history, rules, next_gw)
    return ManagerState(
        entry_id=entry.id,
        entry_name=entry.name,
        latest_gw=latest_gw,
        next_gw=next_gw,
        squad=squad,
        bank=bank,
        free_transfers=ft_remaining,
        next_gw_transfers_made=n_pending,
        chips_used=used,
        chips_available=avail,
        ft_replay=steps,
        overall_rank=picks.entry_history.overall_rank,
        recorded_value=picks.entry_history.value,
        warnings=warnings,
    )
