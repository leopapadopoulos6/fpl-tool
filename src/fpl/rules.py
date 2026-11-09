"""Game rules for the current season.

Everything the API exposes is read from ``bootstrap-static`` at runtime (scoring table,
squad composition, club limit, budget, chip windows, FT cap, sell-on fee). Rules that are
*not* in the API are encoded below as constants, each with its source. Re-verify them at
the start of every season (see CLAUDE.md, "Rules").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fpl.api.models import Bootstrap

# --- Rules not exposed by the API (verified 2026-11-05 from FPL web-app help text) ---------
HIT_COST = 4  # points per transfer beyond free transfers
FT_PER_GW = 1
# Banked FTs are retained when WC/FH is played; the chip GW itself does not accrue +1.
# Confirmed empirically on entry 1020085 (FH GW5: 1 FT before -> 1 FT in GW6).
FT_RETAINING_CHIPS = frozenset({"wildcard", "freehit"})
FREEHIT_NOT_CONSECUTIVE = True  # second FH cannot be played the GW after the first
ONE_CHIP_PER_GW = True
# WC/FH are unavailable in the manager's first GW (unlimited transfers anyway).
FIRST_GW_UNAVAILABLE_CHIPS = frozenset({"wildcard", "freehit"})
# Defensive contribution thresholds (API gives the +2 award, not the thresholds).
# DEF: clearances + blocks + interceptions + tackles (CBIT) >= 10
# MID/FWD: CBIT + recoveries (CBIRT) >= 12. GKP: not eligible. Award capped once per match.
DEFCON_THRESHOLD = {"GKP": None, "DEF": 10, "MID": 12, "FWD": 12}
# Price changes run daily at midnight UK time; selling price keeps floor(half the rise).

POSITIONS = ("GKP", "DEF", "MID", "FWD")


@dataclass(frozen=True)
class ChipWindow:
    name: str  # wildcard | freehit | bboost | 3xc
    chip_id: int
    start_event: int
    stop_event: int

    def contains(self, gw: int) -> bool:
        return self.start_event <= gw <= self.stop_event


@dataclass(frozen=True)
class GameRules:
    squad_size: int
    squad_play: int
    team_limit: int
    budget: int  # £0.1m
    max_free_transfers: int
    sell_on_fee: float
    squad_select: dict[str, int]  # per position, squad of 15
    min_play: dict[str, int]
    max_play: dict[str, int]
    scoring: dict[str, Any]
    chip_windows: tuple[ChipWindow, ...]
    position_by_type: dict[int, str]
    ft_overrides: dict[int, int] = field(default_factory=dict)  # GW -> forced FT count

    @classmethod
    def from_bootstrap(cls, b: Bootstrap, ft_overrides: dict[int, int] | None = None) -> GameRules:
        r = b.game_config.rules
        pos = {t.id: t.singular_name_short for t in b.element_types}
        return cls(
            squad_size=r["squad_squadsize"],
            squad_play=r["squad_squadplay"],
            team_limit=r["squad_team_limit"],
            budget=r["squad_total_spend"],
            max_free_transfers=1 + r["max_extra_free_transfers"],
            sell_on_fee=r["transfers_sell_on_fee"],
            squad_select={t.singular_name_short: t.squad_select for t in b.element_types},
            min_play={t.singular_name_short: t.squad_min_play for t in b.element_types},
            max_play={t.singular_name_short: t.squad_max_play for t in b.element_types},
            scoring=b.game_config.scoring,
            chip_windows=tuple(
                ChipWindow(c.name, c.id, c.start_event, c.stop_event) for c in b.chips
            ),
            position_by_type=pos,
            ft_overrides=dict(ft_overrides or {}),
        )

    def points(self, stat: str, position: str) -> float:
        """Points per unit of ``stat`` for ``position`` (handles per-position dicts)."""
        v = self.scoring.get(stat, 0)
        return v.get(position, 0) if isinstance(v, dict) else v

    def windows_for(self, chip: str) -> list[ChipWindow]:
        return [w for w in self.chip_windows if w.name == chip]

    def selling_price(self, purchase: int, now: int) -> int:
        """Selling price in £0.1m: keep floor(sell_on_fee * rise); full loss on a fall."""
        if now <= purchase:
            return now
        return purchase + int((now - purchase) * self.sell_on_fee)
