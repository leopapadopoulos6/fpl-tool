"""HTTP client for the public FPL API.

Every live response is snapshotted to ``data/raw/<UTC date>/<key>.json.gz``. The FPL API
only serves the *current* state (prices, flags, ownership, price predictions), so these
snapshots are the only record we will ever have of what was known before a given deadline.
Backtests that need as-of data must read snapshots, not live endpoints.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from fpl.config import Settings, get_settings

USER_AGENT = "fpl-tool/0.1 (personal decision engine)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class FPLAPIError(RuntimeError):
    pass


class OfflineCacheMiss(FPLAPIError):
    pass


def cache_key(path: str) -> str:
    """'entry/1020085/event/9/picks/' -> 'entry_1020085_event_9_picks'."""
    return re.sub(r"[^A-Za-z0-9]+", "_", path.strip("/")).strip("_")


class FPLClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        offline: bool = False,
        snapshot: bool = True,
        max_retries: int = 4,
        backoff: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.offline = offline
        self.snapshot = snapshot
        self.max_retries = max_retries
        self.backoff = backoff
        self._memo: dict[str, Any] = {}
        self._last_request = 0.0
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.settings.auth_token:
            headers["X-Api-Authorization"] = f"Bearer {self.settings.auth_token}"
        self._http = httpx.Client(
            base_url=self.settings.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            transport=transport,
        )

    # -- context manager -------------------------------------------------
    def __enter__(self) -> FPLClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- core ------------------------------------------------------------
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        path = path.lstrip("/")
        if not path.endswith("/"):
            path += "/"
        key = cache_key(path + ("_" + "_".join(f"{k}{v}" for k, v in sorted(params.items()))
                                if params else ""))
        if key in self._memo:
            return self._memo[key]
        data = self._read_latest_snapshot(key) if self.offline else self._fetch(path, params, key)
        self._memo[key] = data
        return data

    def _fetch(self, path: str, params: dict[str, Any] | None, key: str) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if self.snapshot:
                        self._write_snapshot(key, data)
                    return data
                if resp.status_code not in RETRY_STATUS:
                    raise FPLAPIError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
                last_exc = FPLAPIError(f"GET {path} -> {resp.status_code}")
            if attempt < self.max_retries:
                time.sleep(self.backoff * 2**attempt)
        raise FPLAPIError(f"GET {path} failed after {self.max_retries + 1} attempts") from last_exc

    def _throttle(self) -> None:
        wait = self.settings.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    # -- snapshots -------------------------------------------------------
    def _write_snapshot(self, key: str, data: Any) -> Path:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        out = self.settings.raw_dir / day / f"{key}.json.gz"
        out.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            json.dump(data, fh)
        return out

    def _read_latest_snapshot(self, key: str) -> Any:
        raw = self.settings.raw_dir
        candidates = sorted(raw.glob(f"*/{key}.json.gz")) if raw.exists() else []
        if not candidates:
            raise OfflineCacheMiss(f"no snapshot for {key!r} under {raw}")
        with gzip.open(candidates[-1], "rt", encoding="utf-8") as fh:
            return json.load(fh)
