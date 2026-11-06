import pandas as pd

from fpl.data.features import player_form_features


def test_features_use_only_prior_matches():
    rows = []
    for i, (mins, st, xg) in enumerate([(90, 1, 0.5), (0, 0, 0.0), (30, 0, 0.2), (90, 1, 1.0)]):
        rows.append(dict(element=7, fixture=i + 1, round=i + 1,
                         kickoff_time=f"2026-08-{10 + 7 * i:02d}T15:00:00Z", minutes=mins,
                         starts=st, expected_goals=xg, expected_assists=0.0, cbit=0, cbirt=0))
    f = player_form_features(pd.DataFrame(rows))
    assert pd.isna(f.loc[0, "starts_last1"]) or f.loc[0, "starts_last1"] == 0
    assert list(f["started_prev"].fillna(-1)) == [-1, 1, 0, 0]
    assert f.loc[3, "starts_last3"] == 1  # matches 1-3: one start
    assert f.loc[3, "prior_minutes"] == 120
    assert abs(f.loc[3, "prior_xg90"] - 0.7 * 90 / 120) < 1e-9
    assert f.loc[3, "prior_start_rate"] == 1 / 3
    # changing the current row's outcome must not change its own features
    rows[3]["minutes"], rows[3]["expected_goals"] = 0, 9.9
    g = player_form_features(pd.DataFrame(rows))
    assert g.loc[3, "prior_xg90"] == f.loc[3, "prior_xg90"]
