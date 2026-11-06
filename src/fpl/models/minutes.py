"""Minutes / start-probability model. NOT BUILT YET. Drives bench order and Bench Boost.

Intended design:
- Targets: P(start), P(sub appearance), P(60+ | start), E[minutes].
- Features: starts/minutes in last 1/3/6 GWs, minutes trend, ``status`` and
  ``chance_of_playing_next_round``, news age (``news_added``), price/position, team fixture
  congestion (gaps between kickoffs), returning-from-injury ramp, manager rotation tendency.
- Fit on this season's per-match history (element-summary); calibrate with Brier score and
  reliability curves in the backtest. Flags are only known as-of snapshot time: historical
  backtests can only use flags from our own ``data/raw`` snapshots.
- Output a distribution, not a point: auto-sub and BB value depend on joint P(no-show).
"""
