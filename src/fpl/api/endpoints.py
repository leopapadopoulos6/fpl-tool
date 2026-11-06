"""Typed fetchers for every FPL endpoint the engine uses.

Raw dicts are returned by the ``*_raw`` helpers; parsed pydantic models by the rest.
"""

from __future__ import annotations

from typing import Any

from fpl.api.client import FPLClient
from fpl.api.models import (
    Bootstrap,
    ElementSummary,
    Entry,
    EntryHistory,
    Fixture,
    Picks,
    Transfer,
)


def bootstrap_static(c: FPLClient) -> Bootstrap:
    return Bootstrap.model_validate(c.get("bootstrap-static/"))


def fixtures(c: FPLClient, event: int | None = None) -> list[Fixture]:
    params = {"event": event} if event is not None else None
    return [Fixture.model_validate(f) for f in c.get("fixtures/", params)]


def element_summary(c: FPLClient, element_id: int) -> ElementSummary:
    return ElementSummary.model_validate(c.get(f"element-summary/{element_id}/"))


def event_live(c: FPLClient, event: int) -> dict[str, Any]:
    return c.get(f"event/{event}/live/")


def event_status(c: FPLClient) -> dict[str, Any]:
    return c.get("event-status/")


def entry(c: FPLClient, entry_id: int) -> Entry:
    return Entry.model_validate(c.get(f"entry/{entry_id}/"))


def entry_history(c: FPLClient, entry_id: int) -> EntryHistory:
    return EntryHistory.model_validate(c.get(f"entry/{entry_id}/history/"))


def entry_picks(c: FPLClient, entry_id: int, event: int) -> Picks:
    return Picks.model_validate(c.get(f"entry/{entry_id}/event/{event}/picks/"))


def entry_transfers(c: FPLClient, entry_id: int) -> list[Transfer]:
    return [Transfer.model_validate(t) for t in c.get(f"entry/{entry_id}/transfers/")]


def league_classic_standings(c: FPLClient, league_id: int, page: int = 1) -> dict[str, Any]:
    """League 314 is the overall league; used later to sample top-k ownership (EO)."""
    return c.get(f"leagues-classic/{league_id}/standings/", {"page_standings": page})


def my_team(c: FPLClient, entry_id: int) -> dict[str, Any]:
    """Authenticated only (403 without FPL_AUTH_TOKEN). Exact selling prices + FT count.

    Optional cross-check for the public reconstruction in ``fpl.state``; auth flow unverified.
    """
    return c.get(f"my-team/{entry_id}/")
