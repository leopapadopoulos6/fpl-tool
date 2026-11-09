"""Full-season xPts backtest on 2025-26 (vaastav mirror), with vs without the history prior.
Needs data/external/vaastav (fpl history fetch) and the FPL API for GameRules.

    uv run python scripts/research/season_backtest.py HL KAPPA KMAX
"""
import warnings; import sys; import time; warnings.filterwarnings("ignore")

import pandas as pd

from fpl.api import endpoints as ep
from fpl.api.client import FPLClient
from fpl.backtest.past_season import season_tables, team_prior_for
from fpl.backtest.xpts import by_position, predictions, score
from fpl.config import get_settings
from fpl.data.features import build_player_features
from fpl.history.judgment.baseline import BaselineParams
from fpl.history.judgment.integrate import prior_fn
from fpl.rules import GameRules

hl, kappa, kmax = (float(x) for x in sys.argv[1:4]) if len(sys.argv) > 3 else (365, 0.5, 20)
t0 = time.time()
s = get_settings(); rules = GameRules.from_bootstrap(ep.bootstrap_static(FPLClient(snapshot=False)))
pm, tm, e2c, teams = season_tables(s.external_dir / "vaastav", "2025-26")
f = build_player_features(pm, tm)
prior = team_prior_for(teams, s.data_dir, "2024-25")
ev = pd.read_parquet("data/research/ev.parquet")
fn = prior_fn(ev, "2025-26", e2c, BaselineParams(half_life_days=hl, kappa=kappa, k_max=kmax))
rounds = list(range(4, 39))
base = predictions(f, tm, rules, rounds, prior)
hist = predictions(f, tm, rules, rounds, prior, rate_prior_fn=fn)
pd.set_option("display.width", 200)
print("params", hl, kappa, kmax, "secs", round(time.time()-t0))
print("WITHOUT:"); print(score(base).round(4).to_string(index=False))
print("WITH:"); print(score(hist).round(4).to_string(index=False))
bp, hp = by_position(base), by_position(hist)
print(bp[bp.predictor=="model"].merge(hp[hp.predictor=="model"], on="position", suffixes=("_base","_hist"))[["position","mae_base","mae_hist","spearman_base","spearman_hist","n_base"]].round(4).to_string(index=False))
for lab, rr in (("GW4-10", range(4,11)), ("GW11-38", range(11,39))):
    b_, h_ = score(base[base["round"].isin(rr)]).set_index("predictor").loc["model"], score(hist[hist["round"].isin(rr)]).set_index("predictor").loc["model"]
    print(lab, "MAE %.4f -> %.4f, Spearman %.4f -> %.4f" % (b_.mae, h_.mae, b_.spearman, h_.spearman))
base.to_parquet("data/research/bt_base_2526.parquet"); hist.to_parquet("data/research/bt_hist_2526.parquet")
