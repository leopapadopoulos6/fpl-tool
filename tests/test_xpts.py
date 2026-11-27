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


def _match(rules):
    """One match: home team 1 (GKP, DEF, MID) v away team 2 (MID, FWD)."""
    from fpl.models.xpts import _floor_div_mean
    lf = {1: 1.4, 2: 1.1}
    rows = pd.DataFrame({
        "element": [1, 2, 3, 4, 5], "position": ["GKP", "DEF", "MID", "MID", "FWD"],
        "team": [1, 1, 1, 2, 2], "opponent_team": [2, 2, 2, 1, 1], "fixture": 7,
        "p_start": [1.0, 0.95, 0.9, 0.9, 0.95], "p_60": [1.0, 0.9, 0.8, 0.8, 0.85],
        "p_appear": [1.0, 0.97, 0.95, 0.95, 0.97], "e_minutes": [90.0, 84, 75, 75, 80],
        "e_goals": [0.0, 0.08, 0.3, 0.25, 0.55], "e_assists": [0.0, 0.07, 0.25, 0.2, 0.15],
        "p_defcon": [0.0, 0.3, 0.1, 0.1, 0.02], "pts_saves": [0.6, 0, 0, 0, 0],
        "pts_bonus": [0.3, 0.3, 0.4, 0.4, 0.5], "pts_cards": [-0.1] * 5})
    rows["lambda_for"] = rows["team"].map(lf)
    rows["lambda_against"] = rows["opponent_team"].map(lf)
    rows["p_cs"] = np.exp(-rows["lambda_against"])
    gc = -_floor_div_mean(rows["lambda_against"].to_numpy(), 2) * rows["p_start"]
    rows["pts_conceded"] = np.where(rows["position"].isin(["GKP", "DEF"]), gc, 0.0)
    return rows


def test_joint_simulation_keeps_each_players_mean(rules):
    from fpl.models.xpts import simulate_joint
    rows = _match(rules)
    ind = simulate(rows, rules, n=80_000).mean(axis=0)
    joint = simulate_joint(rows, rules, n=80_000).mean(axis=0)
    assert np.allclose(ind, joint, atol=0.08), (ind, joint)


def test_joint_simulation_correlates_players_in_the_same_match(rules):
    from fpl.models.xpts import simulate_joint
    rows = _match(rules)
    c = np.corrcoef(simulate_joint(rows, rules, n=40_000).T)
    ci = np.corrcoef(simulate(rows, rules, n=40_000).T)
    assert c[1, 4] < -0.05 and c[0, 4] < -0.05    # home DEF / GKP v away FWD: a hedge
    assert c[0, 1] > 0.2                          # home GKP + DEF: a stack
    assert c[2, 3] < 0                            # opposing midfielders
    assert abs(ci[1, 4]) < 0.02 and abs(ci[0, 1]) < 0.02  # independent draws: none
