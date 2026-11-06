"""Engine glue: real manager state + forecast -> optimizer runs -> decision objects.

Used by the CLI (``fpl plan``) and the dashboard datasets. Every recommendation carries its
expected-points delta against rolling the transfer, over the same horizon and decay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from fpl.api.models import Bootstrap, Fixture
from fpl.data.priors import team_prior
from fpl.models.xpts import simulate
from fpl.optimize.milp import OptInput, OptSettings, Plan, alternatives, candidate_pool
from fpl.pipeline import Forecast, forecast, xpts_matrix
from fpl.rules import GameRules
from fpl.state import ManagerState

HIT_HURDLE = 1.0  # extra xPts a hit must add over the cheaper option (model uncertainty)
CAPTAIN_HURDLE = 1.0  # xPts edge needed to captain against the top-10k majority captain
TEMPLATE_CAPTAIN_EO = 100.0  # top-10k EO at/above which most of them captain the player


def apply_captain_hurdle(res: EngineResult, eo: dict[int, float],
                         hurdle: float = CAPTAIN_HURDLE) -> str | None:
    """Keep the top-10k majority captain unless the model's pick beats him by ``hurdle``
    simulated points. Returns a note when the captain was changed back."""
    g = res.best.gws[0]
    template = max((p for p in g.lineup if eo.get(p, 0) >= TEMPLATE_CAPTAIN_EO),
                   key=lambda p: eo.get(p, 0), default=None)
    if template is None or template == g.captain:
        return None
    mean = res.captaincy.set_index("element")["mean"]
    edge = float(mean.get(g.captain, 0) - mean.get(template, 0))
    if edge >= hurdle:
        return None
    xp = res.forecast.by_gw[res.forecast.by_gw["event"] == g.gw].set_index("element")["xpts"]
    g.xpts += float(xp.get(template, 0) - xp.get(g.captain, 0))
    g.vice, g.captain = g.captain, template
    return f"model captain edge {edge:.2f} < hurdle {hurdle}: kept template captain"


def recommend(options: list[Option], hit_hurdle: float = HIT_HURDLE) -> Option:
    """Highest Δ after charging ``hit_hurdle`` per hit: a hit must clearly beat the
    best hit-free option, not just edge it."""
    return max(options, key=lambda o: o.delta_vs_roll - hit_hurdle * o.hits)


@dataclass
class Option:
    label: str
    transfers_in: list[int]
    transfers_out: list[int]
    hits: int
    plan: Plan
    delta_vs_roll: float = 0.0  # objective units (decayed xPts over horizon, hits included)


@dataclass
class EngineResult:
    forecast: Forecast
    players: pd.DataFrame
    options: list[Option]
    best: Plan
    captaincy: pd.DataFrame
    settings: OptSettings
    gws: list[int]
    notes: list[str] = field(default_factory=list)


def players_frame(b: Bootstrap, state: ManagerState, rules: GameRules) -> pd.DataFrame:
    owned = {p.element: p for p in state.squad}
    rows = []
    for e in b.elements:
        o = owned.get(e.id)
        rows.append({
            "element": e.id, "name": e.web_name, "team": e.team,
            "position": rules.position_by_type[e.element_type], "now_cost": e.now_cost,
            "sell_price": o.selling_price if o else e.now_cost, "owned": o is not None,
            "available": e.status in ("a", "d") and e.can_transact,
            "selected_by_percent": e.selected_by_percent, "status": e.status,
            "chance": e.chance_of_playing_next_round, "news": e.news,
        })
    return pd.DataFrame(rows).set_index("element")


def run(b: Bootstrap, fixtures: list[Fixture], pm: pd.DataFrame, tm: pd.DataFrame,
        state: ManagerState, rules: GameRules, data_dir: Path, horizon: int = 6,
        settings: OptSettings | None = None, max_first_gw_transfers: int = 2,
        hit_hurdle: float = HIT_HURDLE) -> EngineResult:
    settings = settings or OptSettings()
    fc = forecast(b, fixtures, pm, tm, rules, team_prior(b, data_dir), horizon=horizon,
                  start_gw=state.next_gw)
    players = players_frame(b, state, rules)
    xp = xpts_matrix(fc)
    pool = candidate_pool(players, xp)
    inp = OptInput(players=players.loc[pool], xp=xp.loc[xp.index.intersection(pool)],
                   gws=fc.gws, bank=state.bank, free_transfers=state.free_transfers,
                   rules=rules, settings=settings)
    alts = alternatives(inp, max_first_gw_transfers=max_first_gw_transfers)
    roll = next(p for n, p in alts if n == 0)
    options = []
    for n, plan in alts:
        g = plan.gws[0]
        label = "Roll the transfer" if n == 0 else (
            f"{n} transfer{'s' if n > 1 else ''}" + (f", -{4 * g.hits}" if g.hits else ""))
        options.append(Option(label, g.buys, g.sells, g.hits, plan,
                              delta_vs_roll=plan.objective - roll.objective))
    rec = recommend(options, hit_hurdle)
    best = rec.plan
    cap = captaincy(fc, best.gws[0].lineup, rules)
    return EngineResult(fc, players, options, best, cap, settings, fc.gws)


def captaincy(fc: Forecast, lineup: list[int], rules: GameRules, n: int = 6000) -> pd.DataFrame:
    """Simulated GW points for each starter (DGWs summed): mean and spread for captaincy."""
    g = fc.gws[0]
    rows = fc.by_fixture[(fc.by_fixture["event"] == g) & fc.by_fixture["element"].isin(lineup)]
    sims = simulate(rows.reset_index(drop=True), rules, n=n)
    df = pd.DataFrame(sims, columns=rows["element"].to_numpy())
    per = df.T.groupby(level=0).sum().T  # sum fixtures per player
    out = pd.DataFrame({
        "element": per.columns,
        "mean": per.mean().to_numpy(),
        "p10": np.percentile(per, 10, axis=0), "p90": np.percentile(per, 90, axis=0),
        "p_blank": (per <= 2).mean().to_numpy(), "p_haul": (per >= 10).mean().to_numpy(),
    }).sort_values("mean", ascending=False).reset_index(drop=True)
    return out
