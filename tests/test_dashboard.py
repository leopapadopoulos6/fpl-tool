import json
from datetime import UTC, datetime

import pytest

from fpl.api.models import Element, Fixture, PriceProjection
from fpl.state import build_state
from fpl.ui.dashboard import (
    NO_PRICE_CHANGE,
    build_datasets,
    next_fixtures,
    price_status,
    write_datasets,
)
from tests.conftest import load

NOW = datetime(2026, 11, 6, 16, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def fixtures_upcoming() -> list[Fixture]:
    return [Fixture.model_validate(f) for f in load("fixtures_upcoming")]


@pytest.fixture(scope="module")
def datasets(bootstrap, entry, history, picks, transfers, rules, fixtures_upcoming):
    st = build_state(bootstrap=bootstrap, entry=entry, history=history, picks=picks,
                     transfers=transfers, rules=rules)
    return build_datasets(bootstrap, st, fixtures_upcoming, NOW,
                          last_gw_points=50, total_points=619)


def test_summary(datasets):
    (s,) = datasets["summary"]
    assert s["team"] == "HaramBall"
    assert s["bank_m"] == 3.8
    assert s["free_transfers"] == 1
    assert s["next_gw"] == 10
    assert s["deadline_utc"] == "2026-11-06T18:30:00Z"
    assert (s["hits_taken"], s["hit_points_season"]) == (1, 4)
    assert s["as_of_utc"] == "2026-11-06T16:30:00Z"


def test_squad_rows(datasets):
    squad = datasets["squad"]
    assert len(squad) == 15
    assert len({r["element"] for r in squad}) == 15  # row key is unique
    assert [r["role"] for r in squad].count("Starting") == 11
    assert {r["role"] for r in squad if r["role"] != "Starting"} == {
        "Bench 1", "Bench 2", "Bench 3", "Bench 4"}
    for r in squad:
        assert r["locked_gain_m"] == round(r["sell_m"] - r["bought_m"], 1)
        assert r["next_fixtures"].count(" · ") == 4


def test_chips(datasets):
    chips = {(c["chip"], c["set"]): c for c in datasets["chips"]}
    assert len(chips) == 8
    assert chips[("Free Hit", "First half")]["status"] == "Used GW5"
    assert chips[("Wildcard", "First half")]["status"] == "Available"
    assert chips[("Wildcard", "First half")]["use_by_utc"] == "2027-01-01T18:30:00Z"
    assert chips[("Bench Boost", "Second half")]["status"] == "Not yet available"


def test_ft_history_matches_hits(datasets):
    gw9 = next(r for r in datasets["ft_history"] if r["gw"] == 9)
    assert (gw9["ft_available"], gw9["transfers"], gw9["hit_points"]) == (2, 3, 4)
    assert next(r for r in datasets["ft_history"] if r["gw"] == 5)["chip"] == "Free Hit"


@pytest.mark.parametrize(("likelihood", "label"), [
    (5, "Very likely to rise"), (4, "Likely to rise"), (-4, "Likely to drop"),
    (-5, "Very likely to drop"), (3, NO_PRICE_CHANGE), (0, NO_PRICE_CHANGE)])
def test_price_status_labels(bootstrap, likelihood, label):
    el = bootstrap.elements[0].model_copy(update={
        "price_change_projections": [PriceProjection(offset=0, projected_percent=1,
                                                     likelihood=likelihood)],
        "price_change_locked_until": None, "price_change_calibrating": False})
    assert price_status(el) == label


def test_price_status_locked_and_calibrating(bootstrap):
    base: Element = bootstrap.elements[0]
    assert price_status(base.model_copy(update={"price_change_locked_until": NOW})) == \
        "Price locked"
    assert price_status(base.model_copy(update={
        "price_change_locked_until": None, "price_change_calibrating": True})) == "Calibrating"


def _fx(i, gw, h, a):
    return Fixture(id=i, event=gw, kickoff_time=None, team_h=h, team_a=a, finished=False)


def test_next_fixtures_blank_and_double():
    short = {1: "ARS", 2: "CHE", 3: "LIV"}
    fx = [_fx(1, 10, 1, 2), _fx(2, 12, 3, 1), _fx(3, 12, 1, 2), _fx(4, 14, 2, 1)]
    assert next_fixtures(1, 10, fx, short) == \
        "CHE (H) · Blank · LIV (A) + CHE (H) · Blank · CHE (A)"


def test_write_datasets(tmp_path, datasets):
    paths = write_datasets(datasets, tmp_path)
    assert {p.name for p in paths} == {"summary.json", "squad.json", "chips.json",
                                       "ft_history.json"}
    assert len(json.loads((tmp_path / "squad.json").read_text())) == 15
