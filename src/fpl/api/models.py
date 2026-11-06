"""Pydantic models for the FPL API.

Only fields the engine relies on are declared; everything else is kept via ``extra="allow"``
so new API fields are never silently dropped. Prices are integers in £0.1m (62 == £6.2m).
Numeric strings from the API (e.g. ``expected_goals: "0.35"``) are coerced to float.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- bootstrap-static ------------------------------------------------------
class Event(_M):
    id: int
    name: str
    deadline_time: datetime
    finished: bool
    data_checked: bool
    is_previous: bool
    is_current: bool
    is_next: bool


class Chip(_M):
    id: int
    name: str  # wildcard | freehit | bboost | 3xc
    number: int
    start_event: int
    stop_event: int
    chip_type: str


class Team(_M):
    id: int
    name: str
    short_name: str
    strength: int | None = None  # null in 2026/27; we never use official strength anyway


class ElementType(_M):
    id: int
    singular_name_short: str  # GKP | DEF | MID | FWD
    squad_select: int
    squad_min_play: int
    squad_max_play: int


class PriceProjection(_M):
    offset: int
    projected_percent: float
    likelihood: int


class Element(_M):
    id: int
    web_name: str
    team: int
    element_type: int
    now_cost: int
    cost_change_start: int
    status: str
    chance_of_playing_next_round: int | None = None
    chance_of_playing_this_round: int | None = None
    news: str = ""
    news_added: datetime | None = None
    selected_by_percent: float
    minutes: int
    starts: int
    total_points: int
    expected_goals: float
    expected_assists: float
    expected_goals_conceded: float
    defensive_contribution: int
    penalties_order: int | None = None
    price_change_percent: float | None = None
    price_change_projections: list[PriceProjection] = []
    price_change_locked_until: datetime | None = None
    price_change_calibrating: bool = False
    can_transact: bool = True


class GameConfig(_M):
    settings: dict[str, Any]
    rules: dict[str, Any]
    scoring: dict[str, Any]


class Bootstrap(_M):
    events: list[Event]
    chips: list[Chip]
    teams: list[Team]
    element_types: list[ElementType]
    elements: list[Element]
    game_settings: dict[str, Any]
    game_config: GameConfig
    total_players: int

    def current_event(self) -> Event | None:
        return next((e for e in self.events if e.is_current), None)

    def next_event(self) -> Event | None:
        return next((e for e in self.events if e.is_next), None)

    def elements_by_id(self) -> dict[int, Element]:
        return {e.id: e for e in self.elements}


# --- fixtures ----------------------------------------------------------------
class Fixture(_M):
    id: int
    event: int | None
    kickoff_time: datetime | None
    team_h: int
    team_a: int
    team_h_score: int | None = None
    team_a_score: int | None = None
    finished: bool
    started: bool | None = None
    stats: list[dict[str, Any]] = []


# --- element-summary ----------------------------------------------------------
class ElementHistory(_M):
    element: int
    fixture: int
    round: int
    opponent_team: int
    was_home: bool
    minutes: int
    starts: int
    total_points: int
    goals_scored: int
    assists: int
    clean_sheets: int
    goals_conceded: int
    bonus: int
    bps: int
    expected_goals: float
    expected_assists: float
    expected_goals_conceded: float
    clearances_blocks_interceptions: int
    tackles: int
    recoveries: int
    defensive_contribution: int
    value: int


class ElementSummary(_M):
    history: list[ElementHistory]
    fixtures: list[dict[str, Any]]
    history_past: list[dict[str, Any]]


# --- entry -----------------------------------------------------------------------
class Entry(_M):
    id: int
    name: str
    started_event: int
    current_event: int | None
    last_deadline_bank: int | None
    last_deadline_value: int | None
    summary_overall_points: int | None
    summary_overall_rank: int | None


class EntryEventHistory(_M):
    event: int
    points: int
    total_points: int
    overall_rank: int | None
    bank: int
    value: int
    event_transfers: int
    event_transfers_cost: int
    points_on_bench: int


class ChipPlay(_M):
    name: str
    event: int
    time: datetime


class EntryHistory(_M):
    current: list[EntryEventHistory]
    past: list[dict[str, Any]]
    chips: list[ChipPlay]


class Pick(_M):
    element: int
    position: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool
    element_type: int


class Picks(_M):
    active_chip: str | None
    automatic_subs: list[dict[str, Any]]
    entry_history: EntryEventHistory
    picks: list[Pick]


class Transfer(_M):
    element_in: int
    element_in_cost: int
    element_out: int
    element_out_cost: int
    entry: int
    event: int
    time: datetime
