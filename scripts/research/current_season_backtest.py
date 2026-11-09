"""Current-season (2026-27 GW4-9) xPts backtest with vs without the history prior.
Needs data/processed/ (fpl build-tables).

    uv run python scripts/research/current_season_backtest.py HL KAPPA KMAX
"""
import warnings; warnings.filterwarnings("ignore")
import sys
from pathlib import Path

import pandas as pd

from fpl.api import endpoints as ep
from fpl.api.client import FPLClient
from fpl.backtest.xpts import by_position, predictions, score
from fpl.data.features import build_player_features
from fpl.data.priors import team_prior
from fpl.history.judgment.baseline import BaselineParams
from fpl.history.judgment.integrate import prior_fn
from fpl.rules import GameRules

hl, kappa, kmax = (float(x) for x in sys.argv[1:4]) if len(sys.argv) > 3 else (365, 0.5, 20)
b = ep.bootstrap_static(FPLClient(snapshot=False)); rules = GameRules.from_bootstrap(b)
pm = pd.read_parquet("data/processed/player_matches.parquet"); tm = pd.read_parquet("data/processed/team_matches.parquet")
f = build_player_features(pm, tm); prior = team_prior(b, Path("data"))
ev = pd.read_parquet("data/research/ev.parquet")
fn = prior_fn(ev, "2026-27", {e.id: e.code for e in b.elements}, BaselineParams(half_life_days=hl, kappa=kappa, k_max=kmax))
rounds = [4, 5, 6, 7, 8, 9]
base = predictions(f, tm, rules, rounds, prior)
hist = predictions(f, tm, rules, rounds, prior, rate_prior_fn=fn)
pd.set_option("display.width", 200)
print("params", hl, kappa, kmax)
print("WITHOUT history:"); print(score(base).round(4).to_string(index=False))
print("WITH history:"); print(score(hist).round(4).to_string(index=False))
bp, hp = by_position(base), by_position(hist)
print(bp[bp.predictor=="model"].merge(hp[hp.predictor=="model"], on="position", suffixes=("_base","_hist"))[["position","mae_base","mae_hist","spearman_base","spearman_hist","n_base"]].round(4).to_string(index=False))
early = [4,5,6]
print("early GW4-6 MAE base %.4f hist %.4f" % (score(base[base["round"].isin(early)]).set_index("predictor").loc["model","mae"], score(hist[hist["round"].isin(early)]).set_index("predictor").loc["model","mae"]))
