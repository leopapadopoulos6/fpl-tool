import numpy as np
import pandas as pd

from fpl.backtest.team_strength import predictions, score
from fpl.models.team_strength import TeamStrengthParams, fit


def synthetic(n_rounds=30, seed=0):
    """Round-robin-ish league of 6 teams with known attack/defence and home advantage."""
    rng = np.random.default_rng(seed)
    att = {1: 0.4, 2: 0.2, 3: 0.0, 4: 0.0, 5: -0.2, 6: -0.4}
    dfn = {1: 0.3, 2: 0.1, 3: 0.0, 4: -0.1, 5: -0.1, 6: -0.2}
    mu, h = np.log(1.3), 0.15
    rows, fid = [], 0
    teams = list(att)
    for r in range(1, n_rounds + 1):
        perm = rng.permutation(teams)
        for a, b in zip(perm[::2], perm[1::2], strict=True):
            fid += 1
            la = np.exp(mu + h + att[a] - dfn[b])
            lb = np.exp(mu + att[b] - dfn[a])
            ga, gb = rng.poisson(la), rng.poisson(lb)
            ko = pd.Timestamp("2026-08-01", tz="UTC") + pd.Timedelta(days=7 * r)
            for t, o, home, xf, xa, gf, gaa in ((a, b, True, la, lb, ga, gb),
                                                (b, a, False, lb, la, gb, ga)):
                rows.append(dict(fixture=fid, round=r, kickoff_time=ko.isoformat(), team=t,
                                 opponent=o, home=home, xg_for=xf, xg_against=xa,
                                 goals_for=gf, goals_against=gaa))
    return pd.DataFrame(rows), att, dfn, h


def test_recovers_known_ratings():
    tm, att, dfn, h = synthetic()
    m = fit(tm, TeamStrengthParams(ridge=0.01))
    assert abs(m.home_adv - h) < 0.02
    # ratings are identified up to a shared constant: compare centred values
    ca = np.mean(list(m.att.values()))
    for t in att:
        assert abs((m.att[t] - ca) - (att[t] - np.mean(list(att.values())))) < 0.02
    assert m.dfn[1] > m.dfn[6]


def test_ridge_shrinks_toward_prior():
    tm, *_ = synthetic(n_rounds=3)
    prior = {t: (1.0, 0.0) for t in range(1, 7)}
    loose = fit(tm, TeamStrengthParams(ridge=0.01), prior=prior)
    tight = fit(tm, TeamStrengthParams(ridge=1e4), prior=prior)
    assert all(abs(tight.att[t] - 1.0) < 0.02 for t in range(1, 7))
    assert max(abs(loose.att[t] - 1.0) for t in range(1, 7)) > 0.1


def test_fixture_outputs_consistent():
    tm, *_ = synthetic()
    m = fit(tm)
    fx = m.fixture(1, 6, home=True)
    assert np.isclose(fx["p_cs"], np.exp(-fx["lambda_against"]))
    assert fx["lambda_for"] > m.fixture(6, 1, home=True)["lambda_for"]


def test_backtest_uses_only_past_rounds():
    tm, *_ = synthetic(n_rounds=8)
    # poison the future: if round >= test round leaked into training, predictions would move
    pred_clean = predictions(tm, TeamStrengthParams(), [5])
    poisoned = tm.copy()
    poisoned.loc[poisoned["round"] >= 5, "xg_for"] = 50.0
    pred_poison = predictions(poisoned, TeamStrengthParams(), [5])
    assert np.allclose(pred_clean.model_for, pred_poison.model_for)
    s = score(pred_clean)
    assert set(s.predictor) == {"model", "naive", "league"}
