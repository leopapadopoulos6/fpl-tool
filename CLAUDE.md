# CLAUDE.md: FPL decision engine

## Purpose and user
A decision engine for FPL entry **1020085** ("HaramBall"). The manager is experienced: overall rank was 85k after GW8 and 223k after a −4 in GW9 (2026/27).

This is **not a beginner tool**. Its job is to find edges the manager would miss, not to restate consensus. If an output would be equally true of the template, it is noise.

The engine recommends, over a multi-GW horizon:
- transfers, including hits vs. rolling
- captaincy and vice-captaincy
- starting XI and bench order
- chip timing

## Core requirements
1. **xPts model** per player per GW, built from underlying FPL API data: xG, xA, xGC, minutes, bonus/BPS drivers, CBIT/CBIRT and defensive contributions. Raw past points and official FDR are **not** inputs.
2. **Minutes model**: P(start), P(60+) and E[minutes], from rotation, injury/status flags and recent starts. This drives bench order and Bench Boost value.
3. **Fixture strength** from our own team attacking/defensive ratings (xG-based), never official FDR or `teams[].strength*`. The latter are null or 0 in 2026/27 anyway.
4. **Multi-GW MILP optimizer** (HiGHS). It must respect real bank, real selling prices, FTs, 3-per-club and squad rules, and value rolling a transfer vs. taking a hit.
5. **Chip planner**: simulate each remaining chip in each GW of its window and report expected gain per GW. Example: the best Bench Boost window before the first-half deadline.
6. **Backtest** the xPts and minutes models on this season's past GWs and report calibration **before** the optimizer is allowed to consume them.

## Output rules (apply to every report the engine produces)
- Lead with **decisions** and the **expected-points delta** of each option, e.g. "roll: +0.0, X→Y: +1.8, X→Y & A→B −4: +0.6 over 6 GWs".
- Suppress obvious or template moves unless the action itself is non-obvious, such as timing or hit vs. roll.
- Flag **differentials** and where the manager diverges from high-ownership picks. Prefer top-k effective ownership (sampled from league 314) over `selected_by_percent`.
- Show assumptions and confidence (bands, model version, horizon, decay).
- **Never recommend a player the manager can't afford**. Affordability is real selling prices + bank, including multi-transfer budget paths.

## Rules: 2026/27 season (verified 2026-11-05/06)
Sources:
- live `bootstrap-static` (`chips`, `game_config`, `element_types`, `game_settings`)
- the rule text shipped in the FPL web-app bundle (`fantasy.premierleague.com/assets/index-*.js`, help/chips namespaces)
- empirical checks against this entry's history

Article sites such as premierleague.com and ffscout are blocked by this environment's network policy. **Re-verify every season.** Anything readable from the API must be read at runtime (`fpl.rules.GameRules`), never hardcoded.

### Squad and transfers
- **Squad:** 15 players (2 GKP / 5 DEF / 5 MID / 3 FWD), 11 start. Formation: 1 GKP, 3–5 DEF, 2–5 MID, 1–3 FWD. Max 3 per club. Budget £100.0m.
- **Transfers:** +1 FT per GW, rollover up to a **max of 5**. Each extra transfer costs **−4**. Unlimited transfers before the manager's first deadline.
- **WC/FH and FTs:** banked FTs are **retained** across a Wildcard or Free Hit, and the chip GW does **not** add +1. This entry confirms it: 1 FT going into the GW5 FH, then 1 FT in GW6.
- **FT top-ups:** none announced this season. `GameRules.ft_overrides` exists if FPL grants one, as it did with the 2025/26 AFCON top-up.
- **Selling price:** purchase price + floor(50% of the rise) in £0.1m. A fall is taken in full. Example: bought £7.5m, now £7.8m, sells for £7.6m.
- **Price changes:** daily at midnight UK. The API exposes official predictions: `price_change_percent`, `price_change_hourly_rate`, `price_change_projections[{offset 0..2, projected_percent, likelihood}]`, `price_change_locked_until`, and `game_config.settings.price_change_deadlines`. Use these for short-horizon price awareness only.

### Chips
- **Two of each chip:** Wildcard, Free Hit, Bench Boost, Triple Captain.
- **Windows:** set 1 runs GW1–19 (WC/FH from GW2); set 2 runs GW20–38.
- **No carry-over:** unused set-1 chips are lost after the **GW19 deadline (2027-01-01 18:30 UTC)**.
- **Usage limits:** one chip per GW. FH cannot be played in consecutive GWs (FH in GW19 means the next FH is GW21 at the earliest). WC/FH are unavailable in the manager's first GW.
- **No Assistant Manager chip** this season. It is absent from `chips`.
- **Triple Captain fallback:** if the captain doesn't play, the vice-captain gets the triple. If neither plays, the chip is spent.

### Scoring and finalisation
- **Scoring** (from `game_config.scoring`; read at runtime):
  - Goals: GKP 10, DEF 6, MID 5, FWD 4. Assists: 3.
  - Clean sheets: GKP/DEF 4, MID 1.
  - Goals conceded: −1 per 2 (GKP/DEF). Saves: 1 per 3. Penalty save: +5. Penalty miss: −2.
  - Cards: yellow −1, red −3. Own goal: −2.
  - Minutes: 1 for 1–59, 2 for 60+.
- **Defensive contribution (DefCon):** +2, awarded at most once per match. Thresholds are **not in the API**, so they are constants in `fpl/rules.py`:
  - DEF: CBIT ≥ 10 (clearances, blocks, interceptions, tackles)
  - MID/FWD: CBIRT ≥ 12 (CBIT plus recoveries)
  - GKP: not eligible
- **BPS changes in 26/27:** CBI gives 1 BPS per 3 (was per 2). A big-chance save gives +1 BPS. The outside-box save metric is removed. The "tackled" penalty was reportedly dropped (single source).
- **Score finalisation:** GW scores lock at 09:00 UK the day after the GW's last match. Bonus is provisional until `event-status` shows `bonus_added`.
- **Auto-subs:** processed at GW end in bench order, keeping a valid formation. A DGW player who plays in either match is not subbed.

## FPL API (public, no auth unless noted). Base `https://fantasy.premierleague.com/api/`
| Endpoint | What we use it for |
|---|---|
| `bootstrap-static/` | players (prices, status/news, season xG/xA/xGC/DefCon, price predictions), teams, events/deadlines, chips, scoring, rules |
| `fixtures/` (`?event=N`) | schedule (blanks/doubles), results, per-fixture stats incl. `defensive_contribution` |
| `element-summary/{id}/` | per-match history: minutes, starts, xG, xA, xGI, xGC, CBI, tackles, recoveries, DefCon, BPS, `value` (price that GW); upcoming fixtures |
| `event/{gw}/live/` | per-player per-fixture stats + points `explain` (backtest ground truth) |
| `event-status/` | points/bonus finalisation status |
| `entry/{id}/` | bank, value, current_event, started_event |
| `entry/{id}/history/` | per-GW points, transfers, hit cost, bank, `value` (= market value at that GW's prices + bank, verified), chips played |
| `entry/{id}/event/{gw}/picks/` | squad, captaincy, multipliers, active chip, auto-subs (available from the GW deadline onwards) |
| `entry/{id}/transfers/` | every transfer with in/out cost, **including Free Hit GW transfers** (must be excluded for purchase prices) |
| `leagues-classic/314/standings/?page_standings=N` | overall league, for sampling top-k entries for effective ownership |
| `my-team/{id}/` | **403 without auth.** Exact selling prices, FT count and pending chip. Optional cross-check via `FPL_AUTH_TOKEN` (auth flow unverified) |

Quirks:
- Prices are integers in £0.1m.
- xG-type fields are numeric strings; the pydantic models coerce them to float.
- The API only serves *current* state. Prices, flags, news and ownership as of a past deadline are lost unless we snapshotted them, so **run `fpl fetch` before every deadline**.
- Past seasons' per-GW data, for priors, can be taken from the `vaastav/Fantasy-Premier-League` GitHub mirror (reachable from this environment).

## Architecture
```
src/fpl/
  config.py          Settings from env: FPL_ENTRY_ID (1020085), FPL_DATA_DIR (./data),
                     FPL_AUTH_TOKEN, FPL_MIN_INTERVAL
  api/client.py      httpx client: retries/backoff on 429/5xx, throttle, in-run memo,
                     gzip snapshots to data/raw/<UTC date>/<key>.json.gz, --offline reads latest
  api/endpoints.py   typed fetchers for every endpoint above
  api/models.py      pydantic v2 models (declared subset, extra="allow")
  rules.py           GameRules.from_bootstrap + non-API constants (hit cost, DefCon thresholds,
                     FT retention, FH-not-consecutive, one chip/GW)
  state.py           ManagerState from public data:
                     - squad (latest picks + already-made next-GW transfers)
                     - purchase/selling prices (FH transfers excluded)
                     - bank
                     - FT replay with per-GW self-check against recorded hit costs
                     - chips remaining
  cli.py             `fpl doctor | fetch | state`
  models/            team_strength.py, minutes.py, xpts.py         (design docstrings only)
  optimize/milp.py   HiGHS multi-GW MILP                            (design docstring only)
  chips/planner.py   per-chip, per-GW forced-chip re-solves          (design docstring only)
  backtest/xpts.py   rolling-origin calibration report + gate        (design docstring only)
  report/render.py   decision-first report, EO/differential flags    (design docstring only)
```
Data flow:
1. snapshots (`data/raw`)
2. feature tables (parquet, `data/processed`)
3. team strength → minutes → xPts distributions
4. backtest gate
5. MILP and chip planner
6. report

## Stack
- **Now:** Python ≥3.12 (3.13 in this environment), managed with **uv**. Libraries: httpx, pydantic v2, pandas + pyarrow, typer + rich. Dev: pytest, respx, ruff.
- **Add when building:** `highspy` (HiGHS MILP), numpy/scipy, statsmodels and/or scikit-learn. Add them with `uv add`.

## How to run
```bash
uv sync                          # install
uv run fpl doctor                # hit every endpoint; non-zero exit on any failure
uv run fpl state                 # squad, purchase/selling prices, bank, FTs, chips, FT self-check
uv run fpl state --offline       # same, from latest snapshots only
uv run fpl fetch [--summaries]   # snapshot everything (run before every deadline)
uv run fpl --help
```

## How to test
```bash
uv run pytest            # offline suite (fixtures in tests/fixtures are trimmed real responses)
uv run pytest -m live    # live smoke tests against the real API
uv run ruff check .      # lint (ruff format for formatting)
```
- `tests/fixtures/` holds trimmed real responses for entry 1020085 as of GW9. Personal fields are stripped. Refresh them deliberately; tests assert GW9-specific facts.
- Every new model must ship with a backtest/calibration report. Every new optimizer constraint must ship with a small hand-checkable test case.

## Conventions and guardrails
- All money is in integer £0.1m. Never use floats for prices or budget.
- Read scoring and squad rules from `GameRules`. Never hardcode anything the API exposes.
- Purchase prices exclude Free Hit GW transfers. Affordability always uses selling prices + bank.
- The FT replay self-check must pass. A mismatch means a rule change or an FT top-up: investigate, then set `ft_overrides`. Never silence it.
- Models produce distributions, not just means. Captaincy, TC, BB and EO decisions need variance.
- **Backtest gate:** the optimizer does not consume a model version without a generated and reviewed calibration report.
- Everything time-dependent (flags, prices, ownership) must come from snapshots taken before the relevant deadline when backtesting. Never use leaked future info.
- Be polite to the API: throttled client, snapshots reused, no tight loops over element-summary without need.

## Status
Session 1 (2026-11-06): rules and endpoints researched, architecture fixed, scaffold built. Verified `doctor` (all endpoints 200), `state` (1 FT for GW10, bank £3.8m, chips WC1/BB1/TC1 plus all of set 2), and offline and live tests.

**Next:**
1. Feature tables from element-summary/live.
2. Team strength.
3. Minutes model.
4. xPts.
5. Backtest report.
6. MILP.
7. Chip planner.
8. Report.
