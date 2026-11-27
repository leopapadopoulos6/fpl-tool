"""Live smoke tests against the real FPL API. Run with: uv run pytest -m live"""

import pytest

from fpl.api import endpoints as ep
from fpl.api.client import FPLClient
from fpl.config import get_settings
from fpl.rules import GameRules
from fpl.state import build_state

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    with FPLClient(snapshot=False) as c:
        yield c


def test_bootstrap_and_fixtures(client):
    b = ep.bootstrap_static(client)
    assert len(b.teams) == 20 and len(b.events) == 38 and len(b.elements) > 500
    assert {c.name for c in b.chips} == {"wildcard", "freehit", "bboost", "3xc"}
    fx = ep.fixtures(client)
    assert len(fx) == 380


def test_entry_state(client):
    eid = get_settings().entry_id
    b = ep.bootstrap_static(client)
    e = ep.entry(client, eid)
    h = ep.entry_history(client, eid)
    latest = max(r.event for r in h.current)
    p = ep.entry_picks(client, eid, latest)
    tr = ep.entry_transfers(client, eid)
    st = build_state(bootstrap=b, entry=e, history=h, picks=p, transfers=tr,
                     rules=GameRules.from_bootstrap(b))
    assert len(st.squad) == 15
    assert all(s.consistent for s in st.ft_replay), "FT replay drifted from recorded hits"
    assert 0 <= st.free_transfers <= 5
