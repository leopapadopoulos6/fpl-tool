"""Rate-level tuning grid for BaselineParams on 2024-25 (deviance of blend vs current-only).

    uv run python scripts/research/tune_baseline.py
"""
import warnings; import itertools; warnings.filterwarnings("ignore")
from multiprocessing import Pool

import pandas as pd

EV = "data/research/ev.parquet"
_ev = None
def init():
    global _ev
    _ev = pd.read_parquet(EV)
def run(args):
    from fpl.history.judgment import baseline as B
    hl, kappa, kmax = args
    p = B.BaselineParams(half_life_days=hl, kappa=kappa, k_max=kmax)
    r = {"hl": hl, "kappa": kappa, "kmax": kmax}
    for rate in ("xg", "xa"):
        bt = B.rate_backtest(_ev, "2024-25", p, rate)
        r[f"{rate}_blend"] = round(bt["blend_dev"], 5); r[f"{rate}_cur"] = round(bt["current_dev"], 5)
    print(r, flush=True); return r
if __name__ == "__main__":
    grid = list(itertools.product((365, 730), (0.5, 1.0, 2.0), (20, 60)))
    with Pool(4, initializer=init) as pool:
        res = pool.map(run, grid)
    df = pd.DataFrame(res); df["score"] = df.xg_blend / df.xg_cur + df.xa_blend / df.xa_cur
    df.sort_values("score").to_csv(EV.replace("ev.parquet", "tune2.csv"), index=False)
    print(df.sort_values("score").head(6).to_string(index=False))
