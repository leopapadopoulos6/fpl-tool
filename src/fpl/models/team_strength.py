"""Fixture strength from team attacking/defensive ratings (NOT official FDR). NOT BUILT YET.

Intended design:
- Per-fixture team xG for/against, aggregated from player ``expected_goals`` in
  ``event/{gw}/live`` (own-goal-free, penalty-inclusive; consider npxG + pen model later).
- Fit log-linear Poisson (Dixon-Coles-style) ratings: log lambda_home = mu + home + att_h - def_a,
  with exponential time decay and shrinkage toward a prior (last season ratings from the
  vaastav mirror; promoted teams get a promoted-team prior). Blend xG and goals (mostly xG).
- Output per future fixture: lambda_for, lambda_against, P(clean sheet) = exp(-lambda_against),
  plus uncertainty from the posterior/bootstrap.
- Team-level DefCon context: opponent possession/shot volume proxies, since CBIT opportunity
  depends on how much the opponent attacks.
"""
