from collections import Counter

from fpl.api.models import EntryEventHistory, EntryHistory
from fpl.rules import GameRules
from fpl.state import build_state, replay_free_transfers


def _state(bootstrap, entry, history, picks, transfers, rules):
    return build_state(
        bootstrap=bootstrap, entry=entry, history=history, picks=picks,
        transfers=transfers, rules=rules,
    )


def test_ft_replay_matches_every_recorded_hit(history, rules, entry):
    steps, ft_next = replay_free_transfers(history, rules, entry.started_event)
    assert all(s.consistent for s in steps)
    gw9 = next(s for s in steps if s.event == 9)
    assert (gw9.ft_available, gw9.transfers, gw9.derived_cost) == (2, 3, 4)
    assert ft_next == 1


def test_free_hit_retains_without_accrual(history, rules, entry):
    steps, _ = replay_free_transfers(history, rules, entry.started_event)
    by = {s.event: s for s in steps}
    assert by[5].chip == "freehit"
    assert by[6].ft_available == by[5].ft_available == 1


def test_ft_cap_at_five(rules: GameRules):
    rows = [
        EntryEventHistory(event=g, points=0, total_points=0, overall_rank=None, bank=0,
                          value=1000, event_transfers=0, event_transfers_cost=0,
                          points_on_bench=0)
        for g in range(1, 12)
    ]
    _, ft = replay_free_transfers(EntryHistory(current=rows, past=[], chips=[]), rules, 1)
    assert ft == 5


def test_ft_override_hook(history, bootstrap, entry):
    rules = GameRules.from_bootstrap(bootstrap, ft_overrides={10: 5})
    _, ft = replay_free_transfers(history, rules, entry.started_event)
    assert ft == 5


def test_state_squad_and_prices(bootstrap, entry, history, picks, transfers, rules):
    st = _state(bootstrap, entry, history, picks, transfers, rules)
    assert len(st.squad) == 15
    assert Counter(p.position for p in st.squad) == {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    assert max(Counter(p.team for p in st.squad).values()) <= 3
    assert st.bank == 38
    assert st.free_transfers == 1
    assert st.next_gw == 10
    assert not st.warnings
    for p in st.squad:
        assert p.selling_price == rules.selling_price(p.purchase_price, p.now_cost)


def test_free_hit_transfers_excluded_from_purchase_prices(
    bootstrap, entry, history, picks, transfers, rules
):
    st = _state(bootstrap, entry, history, picks, transfers, rules)
    assert all(p.purchase_source != "transfer GW5" for p in st.squad)
    # Bogle bought GW9 for 4.7 (earlier non-FH purchase history must not leak in)
    gw9_in = {t.element_in: t.element_in_cost for t in transfers if t.event == 9}
    for p in st.squad:
        if p.element in gw9_in:
            assert p.purchase_price == gw9_in[p.element]


def test_recorded_value_definition(picks):
    # history 'value' == market value at that GW's prices + bank (verified live 2026-11-06:
    # sum of element-summary round-9 values 991 + bank 38 == 1029).
    assert picks.entry_history.value == 1029


def test_chips_available(bootstrap, entry, history, picks, transfers, rules):
    st = _state(bootstrap, entry, history, picks, transfers, rules)
    first_half = {w.name for w in st.chips_available if w.stop_event == 19}
    second_half = {w.name for w in st.chips_available if w.start_event == 20}
    assert first_half == {"wildcard", "bboost", "3xc"}  # FH1 used GW5
    assert second_half == {"wildcard", "freehit", "bboost", "3xc"}


def test_pending_next_gw_transfer_applied(bootstrap, entry, history, picks, transfers, rules):
    from datetime import UTC, datetime

    from fpl.api.models import Transfer

    out_el = picks.picks[14].element  # bench player
    owned = {p.element for p in picks.picks}
    in_el = next(
        e for e in bootstrap.elements
        if e.id not in owned and e.element_type == picks.picks[14].element_type
    )
    pending = Transfer(element_in=in_el.id, element_in_cost=in_el.now_cost, element_out=out_el,
                       element_out_cost=39, entry=entry.id, event=10, time=datetime.now(UTC))
    st = _state(bootstrap, entry, history, picks, [*transfers, pending], rules)
    assert in_el.id in {p.element for p in st.squad}
    assert out_el not in {p.element for p in st.squad}
    assert st.bank == 38 + 39 - in_el.now_cost
    assert st.free_transfers == 0 and st.next_gw_transfers_made == 1
    new = next(p for p in st.squad if p.element == in_el.id)
    assert new.pending_in and new.squad_slot == 15  # takes the sold player's slot


def test_captain_and_vice_from_latest_picks(bootstrap, entry, history, picks, transfers, rules):
    st = _state(bootstrap, entry, history, picks, transfers, rules)
    assert (st.captain, st.vice) == (411, 166)
    assert sorted(p.squad_slot for p in st.squad) == list(range(1, 16))
    assert not any(p.pending_in for p in st.squad)
