import numpy as np
import pandas as pd

from fpl.models.xpts import _floor_div_mean, _nb_tail, simulate


def test_floor_div_mean_matches_simulation():
    rng = np.random.default_rng(0)
    lam = np.array([0.5, 1.5, 3.0])
    sim = (rng.poisson(lam, (200_000, 3)) // 2).mean(axis=0)
    assert np.allclose(_floor_div_mean(lam, 2), sim, atol=0.01)


def test_nb_tail_monotone_in_mean():
    t = _nb_tail(np.array([4.0, 8.0, 12.0]), np.array([10, 10, 10]), 6.0)
    assert t[0] < t[1] < t[2] and t[0] > 0 and t[2] < 1


def test_simulation_mean_close_to_expectation(rules):
    xp = pd.DataFrame({
        "position": ["MID", "DEF"], "p_start": [0.9, 0.95], "p_60": [0.8, 0.9],
        "p_appear": [0.95, 0.97], "e_minutes": [75.0, 84.0], "e_goals": [0.4, 0.1],
        "e_assists": [0.2, 0.05], "p_cs": [0.35, 0.35], "p_defcon": [0.1, 0.3],
        "pts_conceded": [0.0, -0.4], "pts_saves": [0.0, 0.0], "pts_bonus": [0.4, 0.3],
        "pts_cards": [-0.1, -0.1]})
    sims = simulate(xp, rules, n=40_000)
    analytic = (xp["p_appear"] + xp["p_60"] + xp["e_goals"] * [5, 6] + xp["e_assists"] * 3
                + xp["p_60"] * xp["p_cs"] * [1, 4] + xp["p_defcon"] * 2 + xp["pts_conceded"]
                + xp["pts_bonus"] + xp["pts_cards"])
    assert np.allclose(sims.mean(axis=0), analytic, atol=0.15)
