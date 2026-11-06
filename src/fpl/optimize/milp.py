"""Multi-GW transfer/lineup optimizer (MILP, HiGHS via highspy). NOT BUILT YET.

Intended formulation over horizon T (typically 6-8 GWs), for each player p and GW t:
- binaries: squad x[p,t], lineup y[p,t], captain c[p,t], vice v[p,t], bench order b[p,t,k],
  buy[p,t], sell[p,t]; free-hit squad xf[p,t] separate from permanent squad.
- constraints: 2/5/5/3 squad, formation min/max from ``GameRules``, <=3 per club, budget
  using real selling prices for owned players and ``now_cost`` for buys (+ bank),
  FT bank state ft[t+1] = min(5, ft[t] - free_used[t] + 1) with WC/FH retention, hits = 4 *
  paid transfers, chip binaries with windows / one-per-GW / FH-not-consecutive.
- objective: sum_t decay^t * (lineup xPts + captain xPts + chip extras) - hits; decay reflects
  forecast uncertainty growth. Terminal value for banked FTs and ITB to avoid end-of-horizon
  myopia.
- outputs: best plan + top-N alternatives via no-good cuts so roll vs. 1FT vs. -4 deltas are
  explicit; robustness via re-solving under sampled xPts scenarios.
"""
