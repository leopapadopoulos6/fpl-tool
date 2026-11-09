import pandas as pd
import pytest

from fpl.report.ledger import load_all, review, save, summarise


def _entry():
    return {
        "gw": 10, "recommended": {"label": "1 transfer", "in": [5], "out": [6], "hits": 0,
                                  "delta_vs_roll": 2.1},
        "captain": 1, "vice": 2, "template_captain": 1, "lineup": [1, 2, 3], "bench": [4],
        "names": {"1": "A", "2": "B", "3": "C", "4": "D", "5": "In", "6": "Out"},
        "predictions": [{"element": e, "xpts": x, "p_start": 0.9}
                        for e, x in [(1, 6.0), (2, 4.0), (3, 2.0), (4, 1.0), (5, 5.0), (6, 3.0)]],
    }


def _live(points):
    return {"elements": [{"id": e, "stats": {"total_points": p, "minutes": 90}}
                         for e, p in points.items()]}


def _pm():
    rows = []
    for e, pts in {1: [2, 2], 2: [2, 2], 3: [2, 2], 4: [2, 2], 5: [2, 2], 6: [2, 2]}.items():
        for r, p in zip((8, 9), pts, strict=True):
            rows.append({"element": e, "round": r, "total_points": p})
    return pd.DataFrame(rows)


def test_review_grades_forecast_move_and_captain():
    live = _live({1: 8, 2: 4, 3: 2, 4: 1, 5: 6, 6: 1})
    r = review(_entry(), live, _pm(), manager_points=60, average_points=50,
               manager_transfers=[(5, 6)])
    # forecast tracks the outcomes; flat 2-point baselines cannot rank anything
    assert r["model_mae"] < r["baseline_mae"]
    assert r["model_beat_baselines"] == "Yes"
    assert r["recommended_move_points"] == 5.0 and r["followed"] == "Yes"
    assert (r["captain"], r["captain_points"], r["best_captain"]) == ("A", 8, "A")
    assert r["actual_xi_points"] == 8 + 4 + 2 + 8
    assert r["predicted_xi_xpts"] == pytest.approx(6 + 4 + 2 + 6)


def test_not_followed_and_summary(tmp_path):
    live = _live({1: 2, 2: 9, 3: 2, 4: 1, 5: 1, 6: 7})
    r = review(_entry(), live, _pm(), 40, 50, manager_transfers=[])
    assert r["followed"] == "No" and r["recommended_move_points"] == -6.0
    assert r["best_captain"] == "B"
    save(_entry(), tmp_path)
    assert set(load_all(tmp_path)) == {10}
    s = summarise([r])
    assert s["gws_reviewed"] == 1 and s["recommended_moves_net_points"] == -6.0
