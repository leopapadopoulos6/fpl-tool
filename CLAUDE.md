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
  - **API field semantics (verified):** `defensive_contribution` in element-summary history and in `event/{gw}/live` stats is the **raw action count** (equal to CBIT for DEF and CBIRT for MID/FWD), not points. Points come from the `explain` block. Applying the thresholds to the count reproduced every GW9 award (33 of 33).
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

## User interface (decided 2026-11-06: hybrid GUI)
The user interacts through a GUI, not the CLI. The CLI is plumbing for automation and development.

- **Read-only dashboard (phone):** a private claude.ai Dashboard page, eventually showing precomputed decisions, top-N alternative plans with Δ xPts, the chip-gain table, fixture grid, player table and model health. No server. See "Phone dashboard" below.
- **Streamlit app (local, `uv run fpl ui`):** live what-ifs. Lock or ban players, force a hit or roll, change horizon and decay, and re-solve the MILP. It imports the engine directly.

Planned pages, shared by both:
- **This GW:** decision, Δ xPts, captain/vice, XI and bench, confidence.
- **Planner:** multi-GW transfer timeline.
- **Chips:** expected gain per chip per GW, with the GW19 deadline marked.
- **Fixtures:** our attack and defence ratings by GW, highlighting blanks and doubles.
- **Players:** xPts by GW, P(start), price prediction, EO and differential flags, affordable only.
- **Model health:** backtest calibration.

Build pages alongside the models they display. No placeholder-number UI. Model health comes first.

## Phone dashboard (live since 2026-11-06; recommendations since GW10)
- **URL:** https://claude.ai/artifact/JU1PKMicRTv6Hbn9yng1PP ("HaramBall FPL"). It is private to the user's account. Open it in the Claude app or a signed-in browser, and pin it in the claude.ai sidebar.
- **Type:** it is built on the claude.ai **Dashboard** artifact type. The page lives in the dashboard's store as `files/index.html`; the repo copy is `src/fpl/ui/dashboard_page.html`. Every figure comes from a dataset, and the page has no hardcoded numbers.
- **Sections, in page order:**
  - **Model sections:** This gameweek (decision + Δ vs roll + flags, captaincy simulation, line-up/bench order), Planner, First-half chips, Players, Fixtures. These are marked `data-model-section` and hidden if `decision` is missing.
  - **Model health:** always shown.
  - **Your team now:** summary tiles, price watch, squad, chips status, FT history.
  - **About:** a `<details>` block with the assumptions.
- **Datasets:**
  - State datasets: `summary`, `squad`, `chips`, `ft_history` (`src/fpl/ui/dashboard.py`).
  - Model datasets: `decision`, `captaincy`, `lineup`, `plan`, `chip_gain`, `players`, `fixtures_strength`, `model_health`, `calibration`, `assumptions` (`src/fpl/report/build.py`).
  - `uv run fpl dashboard-data --out DIR` writes them all, plus `manifest.json` (ids, titles, descriptions, and a `remove` list when the gate fails).
- **Gate:** model datasets are produced only if the xPts backtest beats both baselines on MAE and rank correlation, and P(start) beats its baselines (`report/build.py: model_health`). On failure the manifest asks the routine to delete the model datasets, so stale advice disappears.
- **Refresh:** a GitHub Action cannot publish a claude.ai page, so a **Claude routine** does it ("FPL dashboard refresh", `trig_017HWLLYxFxVKABs973LRLWN`, daily 03:23 UK). It:
  1. runs `fpl dashboard-data` (~5–8 min: tables, EO sample, gate, forecast, optimizer, chips);
  2. uploads every file in the manifest;
  3. updates or creates each `datasets/<id>` (pinned with `if_version`);
  4. deletes the old assets and processes `remove`;
  5. if the next deadline is 3–27 h away, schedules a one-shot "pre-deadline refresh" 3 h before it.
- **Changing the page:** edit `src/fpl/ui/dashboard_page.html`, then write it to `files/index.html` (read it first: the user may have edited it). Follow the type's rules (one-writer): fixed text lives in the HTML; numbers go in `data-draw` elements marked with `data-source`/`data-field`. After a write, read `data/users/me` doc `check`.

## Data persistence
The container is ephemeral and `data/` is gitignored on code branches. Snapshots live on the **`data` branch**, written by `.github/workflows/snapshot.yml`:
- `raw/<UTC date>/` is written daily at 01:37 UTC, after the midnight-UK price changes.
- `deadlines/gwNN/` is the last hourly capture inside the 3 h before each deadline. This is the as-of-deadline record. GitHub cron can lag, so check `captured_at.txt`.
- Scheduled workflows only run from the **default branch**. The workflow must be on `main` to fire.

To use the snapshots locally:
```bash
git fetch origin data && git worktree add data data   # FPL_DATA_DIR then defaults to ./data
```

## Architecture
```
src/fpl/
  config.py          Settings from env: FPL_ENTRY_ID (1020085), FPL_DATA_DIR (./data),
                     FPL_AUTH_TOKEN, FPL_MIN_INTERVAL
  api/               client (retries, throttle, gzip snapshots, --offline), typed endpoints,
                     pydantic models (declared subset, extra="allow")
  rules.py           GameRules.from_bootstrap + non-API constants (hit cost, DefCon thresholds,
                     FT retention, FH-not-consecutive, one chip/GW)
  state.py           ManagerState from public data: squad (+ already-made next-GW transfers),
                     purchase/selling prices (FH transfers excluded), bank, FT replay with
                     self-check against recorded hits, chips remaining
  data/tables.py     player_matches (per player per fixture) + team_matches (xG per team-fixture)
  data/features.py   as-of (prior-only) form features + team rest days
  data/priors.py     2025/26 team ratings from the vaastav mirror (by team `code`); promoted =
                     mean of last season's 3 weakest
  data/ownership.py  top-10k effective ownership (league 314 standings + sampled picks)
  models/team_strength.py  Poisson on team xG, home advantage, ridge toward last-season prior
  models/minutes.py  logistic P(start); shrunk P(60|start), P(sub); live flag adjustment
  models/xpts.py     per-fixture component xPts + Monte Carlo simulation (variance)
  pipeline.py        live forecast: per-fixture and per-GW xPts over the horizon
  optimize/milp.py   HiGHS multi-GW MILP: squad/XI/captain/transfers, selling prices, bank,
                     FT banking (cap 5), hits, 3/club, formation, WC/FH/BB/TC; alternatives
  chips/planner.py   force each remaining chip in each GW, re-solve, gain vs no-chip plan
  engine.py          state + forecast -> options (Δ vs roll), hit hurdle, captaincy sims
  backtest/          team_strength.py, minutes.py, xpts.py (rolling origin vs baselines)
  report/build.py    gate + every dashboard dataset (decisions, flags, plan, chips, health)
  ui/dashboard.py    state datasets + DATASET_META + manifest writer
  ui/dashboard_page.html  dashboard page (copy of the store's files/index.html)
  ui/app.py          Streamlit what-if app (`uv run fpl ui`)
  cli.py             doctor | fetch | state | build-tables | backtest-teams | plan |
                     dashboard-data | ui
```
Data flow: API → snapshots (`data/raw`) → tables (`data/processed`) → features → team strength → minutes → xPts → **backtest gate** → MILP (+ chip planner, EO) → datasets → dashboard.

## Stack
Python ≥3.12 (3.13 here), managed with **uv**. Libraries: httpx, pydantic v2, pandas + pyarrow, numpy, scipy, scikit-learn, highspy (HiGHS), typer + rich, streamlit (UI). Dev: pytest, respx, ruff.

## How to run
```bash
uv sync                          # install
uv run fpl doctor                # hit every endpoint; non-zero exit on any failure
uv run fpl state                 # squad, purchase/selling prices, bank, FTs, chips, FT self-check
uv run fpl fetch [--full] [--pre-deadline-hours 3]   # snapshot time-sensitive state
uv run fpl build-tables          # all element-summaries -> data/processed/{player,team}_matches.parquet
uv run fpl backtest-teams        # tune GW2-6, score held-out GW7-9 -> data/processed/reports/
uv run fpl plan [--horizon 6] [--refresh-tables]     # terminal decision report
uv run fpl dashboard-data --out DIR [--no-refresh-tables] [--eo-sample 0] [--no-chips]
uv run fpl ui                    # local Streamlit what-if app
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

Session 2 (2026-11-06):
- Hybrid GUI decided.
- `data` branch seeded with the 2026-11-06 daily snapshot and the GW10 pre-deadline capture (taken 1.95 h before the deadline).
- Snapshot workflow added. It needs merging to `main` to run.
- Phone dashboard v0 published (real state only), with a daily refresh routine.

Session 3 (2026-11-06): M1 started.
- `fpl build-tables` written and run: 5,890 player-match rows and 180 team-match rows (GW1–9).
- DefCon field semantics verified.

Session 4 (2026-11-06): M1 done, team strength built and backtested.

Team strength backtest. Tuned on GW2–6 (chosen: ridge 100, no decay, xG-only target); scored on held-out GW7–9 (n=60 team-matches):

| Predictor | Goal NLL | CS Brier | xG MAE |
|---|---|---|---|
| Model | 1.399 | 0.235 | 0.636 |
| Naive season averages | 1.393 | 0.236 | 0.635 |
| League average | 1.426 | 0.244 | 0.675 |

- The model beats league average clearly. It **ties** naive season averages: the 90% bootstrap CIs on the differences include 0.
- In GW2–3 it is far better than naive (xG MAE 0.69 vs 0.93), because last season's priors carry early-season information.
- Without priors it lost to naive. Lesson: shrink toward last season, not toward league average.
- Status: accepted as the team-strength input (as good as naive late, far better early, calibrated λ and P(CS)). It is **not** an edge on its own. Revisit once more GWs exist.

Session 5 (2026-11-06): all milestones built; recommendations live for GW10.

Backtests on held-out GW4–9 (2,550 player-fixtures; refit before every round, no lookahead):

| Model | Measure | Model | Best baseline |
|---|---|---|---|
| xPts | MAE | 1.70 | 1.89 |
| xPts | Spearman | 0.56 | 0.48 |
| xPts | Actual points of top-20 picks | 4.29 | 3.93 |
| DefCon | Brier | 0.068 | 0.081 |
| Minutes | P(start) Brier | 0.124 | 0.149 |

- Calibration of xPts holds by decile (top decile: 4.08 predicted vs 3.96 actual).
- **Weak spot:** goalkeepers rank slightly worse than points-per-match (Spearman 0.41 vs 0.45).
- **Optimizer:**
  - 6-GW solve ≈ 2 s; chip grid to GW19 ≈ 2 min.
  - Hit hurdle is 1 xPt per hit: on GW10 the −4 option beat the free transfer by only 0.07, so it was not recommended.
  - Captain hurdle is 1 xPt: the model's captain must beat the top-10k majority captain (top-10k EO ≥ 100%) by at least that, or the template captain is kept and the model's pick becomes vice. On GW10 Gabriel's edge over Haaland was 0.52, so Haaland stays (`engine.apply_captain_hurdle`).
- **Top-10k EO** (500 sampled, GW9 picks): Haaland 186%, N.Jackson 92%. Moves selling template players are flagged.

**Known limitations / next:**
1. Cup and European fixtures are not in the FPL API, so rotation in congested weeks is understated.
2. Injury return dates are unknown: flags fade 50% per GW ahead (an assumption).
3. Chips are searched one at a time. Interactions (WC then BB) are not jointly optimised, and WC value late in the GW19 horizon is understated.
4. Price changes inside the horizon are ignored.
5. Player priors from last season (per-player rates) are not used yet; only team priors are. This would mostly help early next season.
6. Merge to `main` so the snapshot workflow runs; then backtests can use true as-of-deadline flags.
