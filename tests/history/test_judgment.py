import numpy as np
import pandas as pd

from fpl.history.judgment import baseline as B
from fpl.history.judgment import splits as S


def _ev(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["n90"] = df["minutes"] / 90
    return df


def _player(code, season, start, n, rate, pos="FWD", team="A"):
    return [{"code": code, "season": season, "date": pd.Timestamp(start) + pd.Timedelta(days=7 * i),
             "minutes": 90, "xg_n": rate, "xa_n": 0.0, "shots_n": 2.0, "pos": pos,
             "position": pos, "team": team, "competition": "Premier League"}
            for i in range(n)]


def test_small_current_sample_regresses_to_career():
    rows = _player(1, "2024-25", "2024-08-10", 38, 0.6)
    rows += _player(1, "2025-26", "2025-08-10", 3, 0.1)
    rows += _player(2, "2025-26", "2025-08-10", 3, 0.3)  # population for the position mean
    b = B.baseline(_ev(rows), pd.Timestamp("2025-09-15"), "2025-26",
                   B.BaselineParams(kappa=0.5, k_max=20))
    r = b.loc[1]
    assert r["career_weight"] > 0.7  # 38 recency-decayed matches outweigh 3 current ones
    assert r["rate_xg"] > 0.45  # stays near the 0.6 career rate despite a 0.1 start


def test_large_current_sample_overrides_career():
    rows = _player(1, "2024-25", "2024-08-10", 38, 0.6)
    rows += _player(1, "2025-26", "2025-08-10", 38, 0.2)
    b = B.baseline(_ev(rows), pd.Timestamp("2026-06-01"), "2025-26",
                   B.BaselineParams(kappa=0.5, k_max=10))
    r = b.loc[1]
    assert r["career_weight"] < 0.25
    assert abs(r["rate_xg"] - 0.2) < 0.1


def test_team_change_discounts_prior():
    rows = _player(1, "2024-25", "2024-08-10", 38, 0.6, team="A") + _player(
        1, "2025-26", "2025-08-10", 3, 0.1, team="B")
    p = B.BaselineParams(kappa=0.5, k_max=20, team_change=0.5)
    b = B.baseline(_ev(rows), pd.Timestamp("2025-09-15"), "2025-26", p)
    undiscounted = min(0.5 * b.loc[1, "career_90s_eff"], 20)
    assert bool(b.loc[1, "team_changed"])
    assert abs(b.loc[1, "k"] - 0.5 * undiscounted) < 1e-9


def test_split_gate_silent_on_pure_noise():
    rng = np.random.default_rng(3)
    rows = []
    venues = [f"G{i}" for i in range(10)]
    for code in range(200):
        for i in range(60):
            rows.append({"code": code, "season": "2024-25", "date": pd.Timestamp("2024-08-01")
                         + pd.Timedelta(days=i), "minutes": 90, "competition": "Premier League",
                         "xg_c": rng.poisson(0.3), "xa_c": 0.0, "xg_n": 0.3, "xa_n": 0.0,
                         "venue": venues[i % 10], "team_elo_pre": 1500, "opponent_elo_pre": 1500})
    df = pd.DataFrame(rows)
    df["n90"] = 1.0
    t = S.shrink_and_test(S.split_effects(df, "venue", beta=0.0))
    assert len(t) == 2000
    assert int(t["bh_pass"].sum()) == 0


def test_split_gate_finds_a_common_large_effect():
    """A common, large effect with a large sample (30 matches per split) is found. With 12
    matches per split the same 4x effect does not pass: shrinkage plus FDR control demand
    large samples, by design."""
    rng = np.random.default_rng(4)
    rows = []
    for code in range(200):
        for i in range(300):
            v = f"G{i % 10}"
            lam = 0.3 * (4.0 if (code < 60 and v == "G0") else 1.0)
            rows.append({"code": code, "season": "2024-25", "date": pd.Timestamp("2024-08-01")
                         + pd.Timedelta(days=i), "minutes": 90, "competition": "Premier League",
                         "xg_c": rng.poisson(lam), "xa_c": 0.0, "xg_n": 0.3, "xa_n": 0.0,
                         "venue": v, "team_elo_pre": 1500, "opponent_elo_pre": 1500})
    df = pd.DataFrame(rows)
    df["n90"] = 1.0
    t = S.shrink_and_test(S.split_effects(df, "venue", beta=0.0))
    hits = t[t["bh_pass"]]
    planted = (hits["code"] < 60) & (hits["split_value"] == "G0")
    assert len(hits) >= 20
    assert planted.mean() >= 0.9  # precision
