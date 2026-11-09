import subprocess

import pandas as pd

from fpl.history import elo as E
from fpl.history.idmap import fold, name_score
from fpl.history.publish import publish


def test_name_folding_and_scores():
    assert fold("Martin Ødegaard") == ["martin", "odegaard"]
    assert fold("Matías Fernández-Pardo") == ["matias", "fernandez", "pardo"]
    # partial Understat names match fuller FPL names
    assert name_score("Bruno Fernandes", "Bruno Miguel Borges Fernandes", "B.Fernandes") == 1.0
    assert name_score("Son Heung-Min", "Heung-Min Son", "Son") == 1.0
    # unrelated names score low; same surname alone is partial
    assert name_score("Ben White", "Kyle Walker", "Walker") == 0.0
    assert 0 < name_score("Danny Ward", "Daniel Ward", "Ward") < 1.0


def _results(rows):
    return pd.DataFrame(rows, columns=["league", "season", "date", "home", "away",
                                       "home_goals", "away_goals"]).assign(
        date=lambda d: pd.to_datetime(d["date"]))


def test_elo_zero_sum_and_direction():
    r = _results([("EPL", "2024-25", "2024-08-10", "A", "B", 3, 0),
                  ("EPL", "2024-25", "2024-08-17", "B", "A", 0, 1)])
    e = E.run(r)
    first = e[e["date"] == "2024-08-10"].set_index("team")["elo_pre"]
    assert first["A"] == first["B"]  # both start equal
    second = e[e["date"] == "2024-08-17"].set_index("team")["elo_pre"]
    assert second["A"] > second["B"]  # the winner gained
    assert abs((second["A"] - 1450) + (second["B"] - 1450)) < 1e-6  # zero-sum


def test_elo_regresses_between_seasons():
    rows = [("EPL", "2024-25", f"2024-08-{10 + i:02d}", "A", "B", 3, 0) for i in range(10)]
    rows.append(("EPL", "2025-26", "2025-08-10", "A", "B", 0, 0))
    e = E.run(_results(rows), E.EloParams(season_regress=0.5))
    end_24 = e[(e["season"] == "2024-25") & (e["team"] == "A")]["elo_pre"].iloc[-1]
    start_25 = e[(e["season"] == "2025-26") & (e["team"] == "A")]["elo_pre"].iloc[0]
    assert start_25 < end_24 + 25  # pulled back toward the mean


def test_publish_keeps_branch_at_one_commit(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    wt = tmp_path / "data"
    subprocess.run(["git", "init", "-q", "-b", "data", str(wt)], check=True)
    subprocess.run(["git", "-C", str(wt), "remote", "add", "origin", str(remote)], check=True)
    env = ["-c", "user.name=t", "-c", "user.email=t@t"]
    (wt / "raw").mkdir()
    (wt / "raw" / "a.txt").write_text("snapshot")
    subprocess.run(["git", "-C", str(wt), *env, "add", "raw"], check=True)
    subprocess.run(["git", "-C", str(wt), *env, "commit", "-qm", "seed"], check=True)
    subprocess.run(["git", "-C", str(wt), "push", "-q", "origin", "data"], check=True)
    (wt / "history").mkdir()
    for i in range(3):
        (wt / "history" / "t.txt").write_text(f"v{i}")
        publish(wt, f"history v{i}")
    log = subprocess.run(["git", "-C", str(remote), "rev-list", "--count", "data"],
                         capture_output=True, text=True, check=True).stdout.strip()
    files = subprocess.run(["git", "-C", str(remote), "ls-tree", "-r", "--name-only", "data"],
                           capture_output=True, text=True, check=True).stdout.split()
    assert log == "1"
    assert {"raw/a.txt", "history/t.txt", ".gitignore"} <= set(files)
