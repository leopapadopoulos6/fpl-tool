from fpl.rules import GameRules


def test_squad_rules(rules: GameRules):
    assert rules.squad_size == 15
    assert rules.squad_play == 11
    assert rules.team_limit == 3
    assert rules.budget == 1000
    assert rules.squad_select == {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    assert rules.min_play == {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
    assert rules.max_free_transfers == 5


def test_chip_windows_two_halves(rules: GameRules):
    for chip in ("wildcard", "freehit", "bboost", "3xc"):
        wins = sorted(rules.windows_for(chip), key=lambda w: w.start_event)
        assert len(wins) == 2, chip
        assert wins[0].stop_event == 19
        assert wins[1].start_event == 20 and wins[1].stop_event == 38
    assert {w.name for w in rules.chip_windows} == {"wildcard", "freehit", "bboost", "3xc"}


def test_scoring_read_from_api(rules: GameRules):
    assert rules.points("goals_scored", "MID") == 5
    assert rules.points("goals_scored", "GKP") == 10
    assert rules.points("clean_sheets", "MID") == 1
    assert rules.points("clean_sheets", "FWD") == 0
    assert rules.points("defensive_contribution", "DEF") == 2
    assert rules.points("defensive_contribution", "GKP") == 0
    assert rules.points("assists", "FWD") == 3


def test_selling_price(rules: GameRules):
    assert rules.selling_price(75, 78) == 76  # FPL help example: £7.5m -> £7.8m sells £7.6m
    assert rules.selling_price(75, 77) == 76
    assert rules.selling_price(75, 76) == 75
    assert rules.selling_price(75, 75) == 75
    assert rules.selling_price(75, 72) == 72  # full loss on a fall
