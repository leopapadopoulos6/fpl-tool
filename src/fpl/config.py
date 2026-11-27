"""Runtime configuration. Everything is overridable by environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENTRY_ID = 1020085
BASE_URL = "https://fantasy.premierleague.com/api"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    entry_id: int = field(default_factory=lambda: int(
        os.getenv("FPL_TEAM_ID") or os.getenv("FPL_ENTRY_ID") or DEFAULT_ENTRY_ID))
    base_url: str = field(default_factory=lambda: os.getenv("FPL_BASE_URL", BASE_URL))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("FPL_DATA_DIR", _repo_root() / "data"))
    )
    # Optional: bearer token for authenticated endpoints (my-team). Never committed.
    auth_token: str | None = field(default_factory=lambda: os.getenv("FPL_AUTH_TOKEN") or None)
    # Seconds between live requests (be polite to the FPL API).
    min_interval: float = field(default_factory=lambda: float(os.getenv("FPL_MIN_INTERVAL", 0.25)))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def external_dir(self) -> Path:
        """Raw third-party sources (never committed)."""
        return self.data_dir / "external"

    @property
    def history_dir(self) -> Path:
        """Processed historical tables (published to the data branch)."""
        return self.data_dir / "history"


def get_settings() -> Settings:
    return Settings()
