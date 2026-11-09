"""Forward-test ledger: record what we advised before each deadline, grade it afterwards.

- ``make_entry``: from an engine run, the recommendation (option, Δ, transfers, captain,
  vice, template captain) and every player's forecast for the target GW.
- Entries are keyed by GW. Each refresh overwrites the entry for the upcoming GW, so the
  last run before the deadline is the one graded. The refresh routine persists entries in
  the dashboard's store (collection ``ledger``) and restores them to ``data/ledger/`` first.
- ``review``: once the GW is finished and FPL has checked the data, compare the forecast
  with actual points (vs the same baselines as the backtest), and the advice with what
  actually happened (recommended move, our captain vs the template captain, your score).

This is out-of-sample in the strictest sense: the numbers were published before kickoff.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from fpl.pipeline import MODEL_VERSION

MIN_P_START = 0.05


def make_entry(res: Any, eo: dict[int, float], names: dict[int, str]) -> dict[str, Any]:
    g = res.best.gws[0]
    gw = g.gw
    rec = next(o for o in res.options if o.plan is res.best)
    fc = res.forecast.by_gw[res.forecast.by_gw["event"] == gw]
    template = max(g.lineup, key=lambda p: eo.get(p, 0.0)) if eo else None
    return {
        "gw": gw,
        "generated_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "model_version": MODEL_VERSION,
        "recommended": {"label": rec.label, "in": rec.transfers_in, "out": rec.transfers_out,
                        "hits": rec.hits, "delta_vs_roll": round(rec.delta_vs_roll, 3)},
        "options": [{"label": o.label, "in": o.transfers_in, "out": o.transfers_out,
                     "hits": o.hits, "delta_vs_roll": round(o.delta_vs_roll, 3)}
                    for o in res.options],
        "captain": g.captain, "vice": g.vice, "template_captain": template,
        "lineup": g.lineup, "bench": g.bench,
        "names": {str(p): names[p] for p in set(g.lineup + g.bench + rec.transfers_in
                                                  + rec.transfers_out)},
        "predictions": [{"element": int(r.element), "xpts": round(float(r.xpts), 3),
                         "p_start": round(float(r.p_start), 3)}
                        for r in fc.itertuples() if r.p_start >= MIN_P_START or r.xpts > 0.5],
    }


def save(entry: dict[str, Any], ledger_dir: Path) -> Path:
    ledger_dir.mkdir(parents=True, exist_ok=True)
    p = ledger_dir / f"gw{entry['gw']:02d}.json"
    p.write_text(json.dumps(entry))
    return p


def load_all(ledger_dir: Path) -> dict[int, dict[str, Any]]:
    if not ledger_dir.exists():
        return {}
    out = {}
    for p in sorted(ledger_dir.glob("gw*.json")):
        e = json.loads(p.read_text())
        out[int(e["gw"])] = e
    return out


def actual_points(live: dict[str, Any]) -> pd.DataFrame:
    rows = [{"element": e["id"], "points": e["stats"]["total_points"],
             "minutes": e["stats"]["minutes"]} for e in live["elements"]]
    return pd.DataFrame(rows).set_index("element")


def baselines(player_matches: pd.DataFrame, gw: int) -> pd.DataFrame:
    """Season-to-date points per match and last-3 average, using matches before ``gw``."""
    prior = player_matches[player_matches["round"] < gw].sort_values(["element", "round"])
    g = prior.groupby("element")["total_points"]
    return pd.DataFrame({"ppm": g.mean(), "last3": g.apply(lambda s: s.tail(3).mean())})


def review(entry: dict[str, Any], live: dict[str, Any], player_matches: pd.DataFrame,
           manager_points: int | None, average_points: int | None,
           manager_transfers: list[tuple[int, int]]) -> dict[str, Any]:
    gw = entry["gw"]
    act = actual_points(live)
    pred = pd.DataFrame(entry["predictions"]).set_index("element")
    base = baselines(player_matches, gw)
    df = pred.join(act, how="inner").join(base, how="left").fillna({"ppm": 0.0, "last3": 0.0})
    df = df[(df["p_start"] >= MIN_P_START) | (df["minutes"] > 0)]

    def metrics(col: str) -> dict[str, float]:
        err = df[col] - df["points"]
        rho = float(spearmanr(df[col], df["points"]).statistic) if df[col].nunique() > 1 else 0.0
        return {"mae": float(err.abs().mean()), "spearman": 0.0 if np.isnan(rho) else rho}

    m, b1, b2 = metrics("xpts"), metrics("ppm"), metrics("last3")
    pts = act["points"].to_dict()
    rec = entry["recommended"]
    move_pts = float(sum(pts.get(p, 0) for p in rec["in"]) - sum(pts.get(p, 0) for p in rec["out"]))
    followed = sorted(i for i, _ in manager_transfers) == sorted(rec["in"]) and \
        sorted(o for _, o in manager_transfers) == sorted(rec["out"])
    cap, tmpl = entry["captain"], entry.get("template_captain")
    lineup = entry["lineup"]
    best_cap = max(lineup, key=lambda p: pts.get(p, 0))
    names = {int(k): v for k, v in entry["names"].items()}
    return {
        "gw": gw, "n_players": int(len(df)),
        "model_mae": round(m["mae"], 3), "baseline_mae": round(min(b1["mae"], b2["mae"]), 3),
        "model_spearman": round(m["spearman"], 3),
        "baseline_spearman": round(max(b1["spearman"], b2["spearman"]), 3),
        "recommended": rec["label"] + (": " + ", ".join(names.get(p, str(p)) for p in rec["in"])
                                       + " for " + ", ".join(names.get(p, str(p))
                                                            for p in rec["out"])
                                       if rec["in"] else ""),
        "recommended_move_points": move_pts - 4 * rec["hits"],
        "followed": "Yes" if followed else "No",
        "captain": names.get(cap, str(cap)), "captain_points": int(pts.get(cap, 0)),
        "template_captain": names.get(tmpl, "") if tmpl else "",
        "template_captain_points": int(pts.get(tmpl, 0)) if tmpl else None,
        "best_captain": names.get(best_cap, str(best_cap)),
        "best_captain_points": int(pts.get(best_cap, 0)),
        "your_points": manager_points, "average_points": average_points,
        "predicted_xi_xpts": round(float(sum(pred["xpts"].get(p, 0.0) for p in lineup)
                                         + pred["xpts"].get(cap, 0.0)), 1),
        "actual_xi_points": int(sum(pts.get(p, 0) for p in lineup) + pts.get(cap, 0)),
        "model_beat_baselines": "Yes" if (m["mae"] < min(b1["mae"], b2["mae"])
                                           and m["spearman"] > max(b1["spearman"],
                                                                   b2["spearman"])) else "No",
    }


def summarise(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    if not reviews:
        return {}
    r = pd.DataFrame(reviews)
    return {"gws_reviewed": int(len(r)),
            "model_mae": round(float(np.average(r["model_mae"], weights=r["n_players"])), 3),
            "baseline_mae": round(float(np.average(r["baseline_mae"], weights=r["n_players"])), 3),
            "gws_model_beat_baselines": int((r["model_beat_baselines"] == "Yes").sum()),
            "recommended_moves_net_points": float(r["recommended_move_points"].sum())}
