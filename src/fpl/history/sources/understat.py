"""Live Understat adapter (league, player and match pages).

A plain client: normal requests, throttled, no bot-check workarounds. Understat embeds its
data as ``JSON.parse('...')`` strings in the page; if the page is the maintenance screen
("We will come back soon"), the adapter reports the source as unavailable instead of
failing, and the build falls back to the vaastav mirror.
"""

from __future__ import annotations

import codecs
import json
import re
import time
from pathlib import Path

import httpx

BASE = "https://understat.com"
LEAGUES = ("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1", "RFPL")
MAINTENANCE = "We will come back soon"


class UnderstatUnavailable(RuntimeError):
    pass


def _get(client: httpx.Client, path: str) -> str:
    r = client.get(BASE + path)
    r.raise_for_status()
    if MAINTENANCE in r.text:
        raise UnderstatUnavailable(f"maintenance page at {path}")
    return r.text


def _embedded(page: str, var: str):
    m = re.search(rf"var {var}\s*=\s*JSON\.parse\('(.*?)'\)", page, re.S)
    if not m:
        raise UnderstatUnavailable(f"no {var} in page (layout changed?)")
    return json.loads(codecs.decode(m.group(1), "unicode_escape"))


def probe() -> dict:
    with httpx.Client(timeout=30) as c:
        try:
            _get(c, "/league/EPL")
            return {"available": True}
        except (UnderstatUnavailable, httpx.HTTPError) as e:
            return {"available": False, "reason": str(e)[:200]}


def fetch(external: Path, seasons: list[int], min_interval: float = 1.0) -> dict:
    """League players + their match histories for the given season start years."""
    out = external / "understat"
    status = probe()
    if not status["available"]:
        return {"source": BASE, "available": False, "reason": status["reason"]}
    n = 0
    with httpx.Client(timeout=30) as c:
        for league in LEAGUES:
            for s in seasons:
                dest = out / "league" / f"{league}_{s}.json"
                if dest.exists():
                    continue
                page = _get(c, f"/league/{league}/{s}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps({"players": _embedded(page, "playersData"),
                                            "dates": _embedded(page, "datesData")}))
                n += 1
                time.sleep(min_interval)
    return {"source": BASE, "available": True, "league_pages": n}
