"""ClubElo adapter. api.clubelo.com serves plain HTTP only; cloud sessions here allow HTTPS
only, so this is optional: used (and cross-checked against our own Elo) where reachable."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pandas as pd

API = "http://api.clubelo.com"


def fetch_date(date: str) -> pd.DataFrame | None:
    try:
        r = httpx.get(f"{API}/{date}", timeout=30)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return pd.read_csv(io.StringIO(r.text))


def fetch(external: Path, dates: list[str]) -> dict:
    out = external / "clubelo"
    got = 0
    for d in dates:
        df = fetch_date(d)
        if df is None:
            return {"source": API, "available": False, "fetched": got,
                    "reason": "HTTP-only API not reachable from this environment"}
        out.mkdir(parents=True, exist_ok=True)
        df.to_csv(out / f"{d}.csv", index=False)
        got += 1
    return {"source": API, "available": True, "fetched": got}
