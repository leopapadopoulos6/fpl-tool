"""Local what-if app: ``uv run fpl ui`` (Streamlit).

Re-solves the multi-GW optimizer live with your constraints: lock or ban players, force the
number of transfers this gameweek, play a chip in a chosen gameweek, change horizon, decay
and hit hurdle. Uses the same state, forecast and optimizer as the dashboard.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st

from fpl.cli import load_context
from fpl.config import get_settings
from fpl.data.priors import team_prior
from fpl.engine import players_frame
from fpl.optimize.milp import OptInput, OptSettings, candidate_pool, solve
from fpl.pipeline import forecast, xpts_matrix

st.set_page_config(page_title="HaramBall what-if", layout="wide")


@st.cache_resource(ttl=3600, show_spinner="Loading your team and the forecast...")
def load(horizon: int):
    s = get_settings()
    ctx = load_context(s.entry_id)
    proc = s.data_dir / "processed"
    pm = pd.read_parquet(proc / "player_matches.parquet")
    tm = pd.read_parquet(proc / "team_matches.parquet")
    fc = forecast(ctx.bootstrap, ctx.fixtures, pm, tm, ctx.rules,
                  team_prior(ctx.bootstrap, Path(s.data_dir)), horizon=horizon,
                  start_gw=ctx.state.next_gw)
    players = players_frame(ctx.bootstrap, ctx.state, ctx.rules)
    return ctx, fc, players


def main() -> None:
    st.title("What-if planner")
    with st.sidebar:
        horizon = st.slider("Horizon (gameweeks)", 1, 10, 6)
        decay = st.slider("Weight per week ahead", 0.6, 1.0, 0.9, 0.05)
        bench_w = st.slider("Bench weight", 0.0, 0.5, 0.1, 0.05)
        hurdle = st.slider("Hit hurdle (xPts per hit)", 0.0, 4.0, 1.0, 0.5)
    ctx, fc, players = load(horizon)
    state = ctx.state
    xp = xpts_matrix(fc)
    names = players["name"].to_dict()
    label = {p: f"{names[p]} ({players.at[p, 'position']}, £{players.at[p, 'now_cost'] / 10:.1f}m)"
             for p in players.index}
    with st.sidebar:
        forced = st.selectbox("Transfers this gameweek", ["Optimise", 0, 1, 2, 3])
        owned = list(players.index[players["owned"]])
        locked = st.multiselect("Keep (locked)", owned, format_func=label.get)
        pool_all = candidate_pool(players, xp)
        banned = st.multiselect("Never buy / sell if owned", pool_all, format_func=label.get)
        chip = st.selectbox("Play a chip", ["None", "wildcard", "freehit", "bboost", "3xc"])
        chip_gw = st.selectbox("Chip gameweek", fc.gws) if chip != "None" else None

    pool = sorted(set(pool_all) | set(locked))
    inp = OptInput(players=players.loc[pool], xp=xp.loc[xp.index.intersection(pool)],
                   gws=fc.gws, bank=state.bank, free_transfers=state.free_transfers,
                   rules=ctx.rules, chips={chip_gw: chip} if chip != "None" else {},
                   first_gw_transfers=None if forced == "Optimise" else int(forced),
                   banned=set(banned), locked=set(locked),
                   settings=OptSettings(decay=decay, bench_weight=bench_w))
    with st.spinner("Solving..."):
        plan = solve(inp)
        roll = solve(replace(inp, first_gw_transfers=0, chips={}))
    if not plan.gws:
        st.error(f"No feasible plan ({plan.status}). Loosen the locks or bans.")
        return
    g0 = plan.gws[0]
    delta = plan.objective - roll.objective - hurdle * g0.hits
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Δ vs rolling (after hurdle)", f"{delta:+.2f}")
    c2.metric(f"GW{g0.gw} xPts (after hits)", f"{g0.xpts - 4 * g0.hits:.1f}")
    c3.metric("Free transfers", state.free_transfers)
    c4.metric("Bank", f"£{state.bank / 10:.1f}m")
    st.subheader("Plan")
    st.dataframe(pd.DataFrame([{
        "GW": g.gw, "Chip": g.chip or "", "Free": g.free_transfers,
        "In": ", ".join(names[p] for p in g.buys) or "Roll",
        "Out": ", ".join(names[p] for p in g.sells), "Hits": g.hits,
        "Captain": names[g.captain], "Vice": names[g.vice], "xPts": round(g.xpts, 1),
        "Bank £m": g.bank_after / 10} for g in plan.gws]), hide_index=True)
    st.subheader(f"GW{g0.gw} line-up")
    fx = xp[g0.gw]
    st.dataframe(pd.DataFrame([{
        "Role": "Captain" if p == g0.captain else "Vice" if p == g0.vice
        else "Starting" if p in g0.lineup else f"Bench {g0.bench.index(p) + 1}",
        "Player": names[p], "Pos": players.at[p, "position"], "xPts": round(fx[p], 2)}
        for p in g0.lineup + g0.bench]), hide_index=True)
    st.subheader("Players")
    tbl = players.loc[pool, ["name", "position", "now_cost", "owned"]].copy()
    tbl["now_cost"] = tbl["now_cost"] / 10
    for g in fc.gws:
        tbl[f"GW{g}"] = xp.reindex(tbl.index)[g].round(2)
    tbl["Total"] = tbl[[f"GW{g}" for g in fc.gws]].sum(axis=1).round(1)
    st.dataframe(tbl.sort_values("Total", ascending=False), hide_index=True)


main()
