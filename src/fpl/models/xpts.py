"""Expected points per player per GW, built from underlying data. NOT BUILT YET.

Intended design (per fixture, summed over a GW so DGWs/BGWs are handled naturally):
- appearance: P(play) * 1 + P(60+) * 1 (scoring table from ``GameRules.scoring``).
- goals: player's share of team xG (shrunk per-90 rate toward a position/price prior,
  Bayesian with minutes as evidence) * team lambda_for * E[minutes]/90 * goal points.
- assists: same with xA share * 3.
- clean sheet / goals conceded: P(CS | lambda_against) * P(60+) for CS points; E[floor(GC/2)]
  for GKP/DEF.
- saves (GKP): shots-faced proxy from opponent lambda; 1 pt per 3 saves.
- DefCon: P(CBIT >= 10) for DEF, P(CBIRT >= 12) for MID/FWD via an over-dispersed count model
  (negative binomial) of per-90 rates scaled by minutes and opponent attacking volume; +2.
- bonus: regression from predicted BPS drivers (goals, assists, CS, saves, CBI/3 under 26/27
  BPS) to expected bonus; cards/pens from historical rates and ``penalties_order``.
- Emit mean and a sample-able distribution (needed for captaincy variance, TC, EO).
"""
