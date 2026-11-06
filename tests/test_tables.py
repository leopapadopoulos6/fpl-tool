from fpl.api.models import ElementHistory, Fixture
from fpl.data.tables import player_matches_frame, team_matches_frame


def _h(el, fixture, home, xg, mins=90, cbi=0, tk=0, rec=0, dc=0):
    return ElementHistory(
        element=el, fixture=fixture, round=1, opponent_team=0, was_home=home, minutes=mins,
        starts=1, total_points=2, goals_scored=0, assists=0, clean_sheets=0, goals_conceded=0,
        bonus=0, bps=0, expected_goals=xg, expected_assists=0.0, expected_goals_conceded=0.0,
        clearances_blocks_interceptions=cbi, tackles=tk, recoveries=rec,
        defensive_contribution=dc, value=50)


def test_player_and_team_tables(bootstrap):
    # two players from the trimmed bootstrap, one per side of fixture 1
    a, b = bootstrap.elements[0], bootstrap.elements[1]
    fx = [Fixture(id=1, event=1, kickoff_time=None, team_h=a.team, team_a=99, finished=True,
                  team_h_score=2, team_a_score=1)]
    hist = [_h(a.id, 1, True, 0.7, cbi=6, tk=4, rec=3, dc=10),
            _h(b.id, 1, False, 0.4)]
    pm = player_matches_frame(hist, fx, bootstrap)
    assert set(pm.team) == {a.team, 99}
    row = pm[pm.element == a.id].iloc[0]
    assert (row.cbit, row.cbirt) == (10, 13)

    tm = team_matches_frame(pm, fx)
    home = tm[tm.home].iloc[0]
    away = tm[~tm.home].iloc[0]
    assert (home.xg_for, home.xg_against, home.goals_for) == (0.7, 0.4, 2)
    assert (away.xg_for, away.xg_against, away.goals_against) == (0.4, 0.7, 2)


def test_unfinished_fixtures_excluded(bootstrap):
    fx = [Fixture(id=2, event=10, kickoff_time=None, team_h=1, team_a=2, finished=False)]
    pm = player_matches_frame([], fx, bootstrap)
    assert team_matches_frame(pm, fx).empty


def test_unplayed_fixture_rows_are_dropped(bootstrap):
    a = bootstrap.elements[0]
    fx = [Fixture(id=1, event=9, kickoff_time=None, team_h=a.team, team_a=99, finished=True,
                  team_h_score=1, team_a_score=0),
          Fixture(id=2, event=10, kickoff_time=None, team_h=a.team, team_a=98, finished=False,
                  finished_provisional=False)]
    pm = player_matches_frame([_h(a.id, 1, True, 0.4), _h(a.id, 2, True, 0.0, mins=0)],
                              fx, bootstrap)
    assert list(pm["fixture"]) == [1]
