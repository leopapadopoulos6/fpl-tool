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


def test_manifest_lists_datasets_and_removals(tmp_path, datasets):
    write_datasets(datasets, tmp_path)
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert {d["id"] for d in m["datasets"]} == set(datasets)
    assert all(d["title"] and d["file"].endswith(".json") for d in m["datasets"])
    assert m["remove"] == []
    gated = {**datasets, "assumptions": [{"gate": "failed: x"}]}
    write_datasets(gated, tmp_path / "g")
    assert "decision" in json.loads((tmp_path / "g" / "manifest.json").read_text())["remove"]


def test_squad_card_fields(datasets, bootstrap):
    from fpl.ui.dashboard import TEAM_COLORS

    squad = datasets["squad"]
    assert {r["armband"] for r in squad} == {"Captain", "Vice-captain", ""}
    assert [r["element"] for r in squad if r["armband"] == "Captain"] == [411]
    assert all(r["team_color"].startswith("#") and r["team_color2"].startswith("#")
               for r in squad)
    assert all(r["next_opp"] and " · " not in r["next_opp"] for r in squad)  # next GW only
    assert {t.code for t in bootstrap.teams} <= set(TEAM_COLORS)


def test_fixture_ratings_order():
    import pandas as pd

    from fpl.report.build import fixture_ratings

    bf = pd.DataFrame({"element": range(10), "team": range(10), "fixture": range(10),
                       "event": 11, "opponent_team": 0, "was_home": True,
                       "lambda_for": [0.5 + 0.2 * i for i in range(10)],
                       "p_cs": [0.5 - 0.04 * i for i in range(10)]})
    r = fixture_ratings(bf).set_index("team")
    assert r["attack_rating"].between(1, 5).all() and r["defence_rating"].between(1, 5).all()
    assert r.at[9, "attack_rating"] == 1 and r.at[0, "attack_rating"] == 5  # most xG = easiest
    assert r.at[0, "defence_rating"] == 1 and r.at[9, "defence_rating"] == 5
    assert r["attack_rating"].is_monotonic_decreasing


def test_roles_and_sale_pairs():
    from types import SimpleNamespace

    from fpl.report.build import _gw_roles, _sale_pairs

    g = SimpleNamespace(lineup=[1, 2, 3], bench=[4, 5], captain=2, vice=3,
                        buys=[9, 8], sells=[5, 1])
    assert _gw_roles(g) == {1: "Starting", 2: "Captain", 3: "Vice-captain",
                            4: "Bench 1", 5: "Bench 2"}
    pos = {1: "MID", 5: "DEF", 8: "MID", 9: "DEF"}
    assert _sale_pairs(g, pos) == {5: 9, 1: 8}
