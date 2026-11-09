import httpx
import pytest
import respx

from fpl.api.client import FPLAPIError, FPLClient, OfflineCacheMiss, cache_key
from fpl.config import Settings

BASE = "https://fantasy.premierleague.com/api"


@pytest.fixture
def settings(tmp_path):
    return Settings(entry_id=1, base_url=BASE, data_dir=tmp_path, auth_token=None, min_interval=0)


def test_cache_key():
    assert cache_key("entry/1020085/event/9/picks/") == "entry_1020085_event_9_picks"


@respx.mock
def test_snapshot_then_offline_read(settings):
    respx.get(f"{BASE}/bootstrap-static/").mock(return_value=httpx.Response(200, json={"a": 1}))
    with FPLClient(settings, backoff=0) as c:
        assert c.get("bootstrap-static/") == {"a": 1}
    assert list(settings.raw_dir.glob("*/bootstrap_static.json.gz"))
    with FPLClient(settings, offline=True) as c:
        assert c.get("bootstrap-static") == {"a": 1}


def test_offline_miss(settings):
    with FPLClient(settings, offline=True) as c, pytest.raises(OfflineCacheMiss):
        c.get("fixtures/")


@respx.mock
def test_retries_then_succeeds(settings):
    route = respx.get(f"{BASE}/fixtures/").mock(
        side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, json=[])]
    )
    with FPLClient(settings, backoff=0) as c:
        assert c.get("fixtures/") == []
    assert route.call_count == 3


@respx.mock
def test_non_retryable_raises(settings):
    respx.get(f"{BASE}/my-team/1/").mock(return_value=httpx.Response(403, json={"detail": "x"}))
    with FPLClient(settings, backoff=0) as c, pytest.raises(FPLAPIError, match="403"):
        c.get("my-team/1/")


@respx.mock
def test_params_in_cache_key(settings):
    respx.get(f"{BASE}/fixtures/", params={"event": 3}).mock(
        return_value=httpx.Response(200, json=[{"id": 3}])
    )
    with FPLClient(settings, backoff=0) as c:
        c.get("fixtures/", {"event": 3})
    assert list(settings.raw_dir.glob("*/fixtures_event3.json.gz"))
