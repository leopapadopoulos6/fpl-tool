"""Build neutral (source-scaled, league-scaled, opponent-adjusted) events from
data/history/player_match.parquet and cache them for the tuning/backtest scripts.

    uv run python scripts/research/build_events.py [out=data/research/ev.parquet]
"""
import sys
import warnings
from pathlib import Path

import pandas as pd

from fpl.history.judgment import baseline as B

warnings.filterwarnings("ignore")
out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/research/ev.parquet")
out.parent.mkdir(parents=True, exist_ok=True)
pm = pd.read_parquet("data/history/player_match.parquet")
sc = B.source_scales(pm)
print("source scales (FPL/Understat):", {k: round(v, 3) for k, v in sc.items()})
ev = B.canonical_events(pm, sc)
beta = B.opponent_slope(ev)
print("opponent slope beta:", round(beta, 3))
lf = B.league_factors(ev)
print(lf.to_string(index=False))
B.neutral_events(ev, lf, beta).to_parquet(out)
print("wrote", out)
