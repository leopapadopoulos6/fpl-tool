import json
from pathlib import Path

import pytest

from fpl.api.models import Bootstrap, Entry, EntryHistory, Picks, Transfer
from fpl.rules import GameRules

FIX = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


@pytest.fixture(scope="session")
def bootstrap() -> Bootstrap:
    return Bootstrap.model_validate(load("bootstrap_trimmed"))


@pytest.fixture(scope="session")
def rules(bootstrap) -> GameRules:
    return GameRules.from_bootstrap(bootstrap)


@pytest.fixture(scope="session")
def entry() -> Entry:
    return Entry.model_validate(load("entry"))


@pytest.fixture(scope="session")
def history() -> EntryHistory:
    return EntryHistory.model_validate(load("history"))


@pytest.fixture(scope="session")
def picks() -> Picks:
    return Picks.model_validate(load("picks_gw9"))


@pytest.fixture(scope="session")
def transfers() -> list[Transfer]:
    return [Transfer.model_validate(t) for t in load("transfers")]
