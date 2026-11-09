# CLAUDE.md: FPL decision engine

Read `docs/HANDOFF.md` first for the current state, decisions, and next steps.

## Purpose and user
A decision engine for FPL entry **1020085** ("HaramBall"). The manager is experienced: rank 85k after GW8 and 223k after a −4 in GW9 (2026/27). Set `FPL_TEAM_ID` or `FPL_ENTRY_ID`; the default is 1020085.

This is **not a beginner tool**. It exists to find edges the manager would miss, not to restate consensus. If an output would be equally true of the template, it is noise.

It recommends, over a multi-GW horizon: transfers (including hits vs. rolling), captain and vice, XI and bench order, and chip timing.

## Requirements (from the brief)
1. **xPts per player per GW** from underlying data: xG, xA, xGC, minutes, bonus/BPS drivers, and defensive contributions (CBIT/CBIRT). Never from raw past points or official FDR.
2. **Minutes model:** P(start), P(60+) and E[min] from rotation, flags and recent starts. It drives bench order and Bench Boost.
3. **Fixture strength** from our own xG-based team ratings, never FDR or `teams[].strength*`, which are null or 0 in 2026/27.
4. **Multi-GW MILP (HiGHS)** with the real bank, selling prices, free transfers, 3 per club and squad rules. It values rolling vs. hits.
5. **Chip planner:** expected gain per chip per GW.
6. **Backtest gate:** a model feeds the optimizer only after its backtest beats baselines.

## Output rules
- Decisions first, with the expected-points Δ of each option against rolling (e.g. "roll +0.0, X→Y +1.8, X→Y & A→B −4 +0.6 over 6 GWs").
- Suppress template moves unless the timing, or hit vs. roll, is the non-obvious part. Flag differentials and divergence from **top-10k effective ownership** (league 314 sample), not `selected_by_percent`.
- Show assumptions and confidence: bands, model version, horizon, decay, hurdles.
- **Never recommend an unaffordable player.** Affordability means selling prices + bank, across multi-transfer paths.
- History-based signals (splits) must show their sample size and Δ xPts, and only appear if they pass the split gate below.

## Rules: 2026/27 (verified 2026-11-05/06)
**Sources:**
- live `bootstrap-static` (`chips`, `game_config`, `element_types`, `game_settings`);
- the rule text in the FPL web-app bundle (`fantasy.premierleague.com/assets/index-*.js`);
- checks against this entry's history.

Read everything the API exposes at runtime via `fpl.rules.GameRules`. Re-verify every season.

**Squad and transfers**
- 15 players (2/5/5/3), 11 start; formation 1 GKP, 3–5 DEF, 2–5 MID, 1–3 FWD.
- Max 3 per club; budget £100.0m.
- +1 FT per GW, banked to a **max of 5**; each extra transfer costs −4.
- WC and FH **retain** banked FTs, and the chip GW adds no +1. Confirmed on this entry: GW5 FH, 1 FT before and after.
- `GameRules.ft_overrides` covers FT top-ups (none in 2026/27).
- Selling price: purchase + floor(50% of the rise) in £0.1m; a fall is taken in full.
- Prices change daily at midnight UK. FPL publishes official predictions: `price_change_percent`, `price_change_projections[{offset, projected_percent, likelihood}]`, `price_change_locked_until`, `price_change_calibrating`. Likelihood 5/4 means very likely / likely to rise; −4/−5 likely / very likely to drop.

**Chips**
- Two each of WC, FH, BB and TC. Set 1 runs GW1–19 (WC/FH from GW2); set 2 runs GW20–38.
- Set-1 chips are lost after the **GW19 deadline, 2027-01-01 18:30 UTC**.
- One chip per GW. FH is not allowed in consecutive GWs. No Assistant Manager chip. TC passes to the vice if the captain doesn't play.

**Scoring** (from `game_config.scoring`)
- Goals: GKP 10, DEF 6, MID 5, FWD 4. Assist 3.
- Clean sheet: GKP/DEF 4, MID 1. Goals conceded: −1 per 2 for GKP/DEF.
- Saves: 1 per 3. Pen save +5, pen miss −2. Yellow −1, red −3, own goal −2. Minutes: 1, or 2 for 60+.
- **DefCon** +2, once per match: DEF CBIT ≥ 10, MID/FWD CBIRT ≥ 12, GKP none. Thresholds are constants in `fpl/rules.py`.
- **API semantics:** `defensive_contribution` in element-summary/live is the **raw action count**. The thresholds reproduce all 33 GW9 awards.

**BPS and finalisation**
- 26/27 BPS: CBI earns 1 BPS per 3. Big-chance save +1. The outside-box save metric is removed.
- Scores lock at 09:00 UK the day after the GW's last match. Bonus is provisional until `event-status` shows `bonus_added`.

## FPL API (base `https://fantasy.premierleague.com/api/`)
| Endpoint | Use |
|---|---|
| `bootstrap-static/` | players, teams, events, chips, scoring, rules, price predictions |
| `fixtures/` (`?event=N`) | schedule (blanks/doubles), scores, `finished`/`finished_provisional`, stats |
| `element-summary/{id}/` | per-match history (xG, xA, xGC, CBI, tackles, recoveries, DefCon count, BPS, `value`). **It lists a GW's fixtures with 0 minutes from the deadline on, until they are played. Keep only finished or provisionally finished fixtures** (`data/tables.py`) |
| `event/{gw}/live/` | per-player stats and points `explain` (grading ground truth) |
| `entry/{id}/`, `/history/`, `/event/{gw}/picks/`, `/transfers/` | bank, value, FT replay, picks (from the deadline on), transfers (include FH GW transfers: exclude them for purchase prices) |
| `leagues-classic/314/standings/` | top-10k sample for EO |
| `my-team/{id}/` | 403 without auth (optional `FPL_AUTH_TOKEN`, unverified) |

Quirks:
- Prices are integers in £0.1m.
- xG fields are numeric strings.
- `history.value` is the market value at that GW's prices plus bank.
- The API serves current state only, so as-of-deadline data exists only in our snapshots.

## Architecture and directory layout
```
src/fpl/
  config.py            Settings: entry_id (FPL_TEAM_ID|FPL_ENTRY_ID), data_dir (FPL_DATA_DIR,
                       default ./data), external_dir (data/external), history_dir (data/history)
  api/                 client.py (retries, throttle, gzip snapshots data/raw/<date>, --offline),
                       endpoints.py, models.py (pydantic, extra="allow")
  rules.py             GameRules + non-API constants (HIT_COST, DEFCON_THRESHOLD, FT retention)
  state.py             build_state(): squad, purchase/selling prices, bank, FT replay with
                       self-check, chips; next_gw override for reconstructing a past deadline
  data/tables.py       current-season player_matches/team_matches from element-summaries
  data/features.py     prior-only form features + rest days (no lookahead, tested)
  data/priors.py       2025/26 team ratings from the vaastav mirror (by team code)
  data/ownership.py    top-10k effective ownership (league 314 + sampled picks)
  models/team_strength.py  Poisson on team xG + home adv, ridge toward last-season prior
  models/minutes.py    logistic P(start); shrunk P(60|start), P(sub); live flag adjustment
  models/xpts.py       component xPts + Monte Carlo; fit_rates(train, prior=None) accepts the
                       history prior (xg90/xa90/k90 by element)
  pipeline.py          forecast(): per-fixture and per-GW xPts over the horizon
  optimize/milp.py     HiGHS: squad/XI/captain/transfers, selling prices, bank, FT banking,
                       hits, 3/club, formation, WC/FH/BB/TC (fixed `chips` dict or exact joint
                       `chip_search`), alternatives(), Plan.gap
  engine.py            run(): options with Δ vs roll; HIT_HURDLE=1.0; CAPTAIN_HURDLE=1.0
                       (apply_captain_hurdle keeps the top-10k majority captain unless beaten
                       by 1 xPt); captaincy() simulation
  chips/planner.py     single-chip scan (superseded on the dashboard)
  chips/joint.py       plan_joint(): WC/BB/TC timed together by coordinate search over fixed-chip
                       solves, horizon GW19+4; marginal gain per chip per GW
  backtest/            team_strength.py, minutes.py, xpts.py (predictions(..., rate_prior_fn)),
                       past_season.py (rebuild a finished season in engine format for full-season
                       backtests: season_tables(), team_prior_for())
  report/build.py      backtest gate + all dashboard datasets; saves the ledger entry
  report/ledger.py     forward-test ledger (make_entry/save/load_all/review/summarise)
  ui/dashboard.py      state datasets, DATASET_META, write_datasets() + manifest.json
  ui/dashboard_page.html  dashboard page (copy of the store's files/index.html)
  ui/app.py            Streamlit what-if app
  history/             historical data layer (below)
  cli.py               doctor | fetch | state | build-tables | backtest-teams | plan | review |
                       dashboard-data | ui | history {fetch,build,publish,report}
scripts/research/      history-prior research: build_events.py (neutral events -> data/research/ev.parquet),
                       tune_baseline.py (rate grid on 2024-25), season_backtest.py (2025-26 full season),
                       current_season_backtest.py (2026-27 GW4-9); outputs stay in gitignored data/research/
tests/                 offline suite (+ tests/history/), fixtures in tests/fixtures (GW9 state)
.github/workflows/snapshot.yml   daily + pre-deadline snapshots to the data branch (needs main)
docs/HANDOFF.md        session handoff: decisions, status, next steps
```
Data flow:
1. API → snapshots → current-season tables;
2. history layer → career priors;
3. features → team strength → minutes → xPts → **gate**;
4. MILP + joint chips + EO;
5. datasets → dashboard, plus the ledger for grading.

## Stack
Python ≥ 3.12 with uv. httpx, pydantic v2, pandas + pyarrow, numpy, scipy, scikit-learn, highspy, typer + rich, streamlit, tabulate. Dev: pytest, respx, ruff.

## Historical data layer (`src/fpl/history/`)
**Sources** (coverage checked 2026-11-09; details in `data/history/COVERAGE.md`):

| Source | Module | Access | Covers | Gaps |
|---|---|---|---|---|
| vaastav/Fantasy-Premier-League | `sources/vaastav.py` | sparse, blob-filtered **git** clone (GitHub API/web blocked) | FPL per-GW 2016-17→2026-27 (`gws/merged_gw.csv`); `players_raw.csv` element→`code`; `fixtures.csv`/`teams.csv` 2019-20+; **Understat career files per player** in the 2021-22…2024-25 `understat/` folders (each file = full Understat career, 2014+, all leagues); `id_dict.csv` 2021-23 | 2019-21 `understat/` folders hold team files only; no Understat 2025-26+; mirror's 2026-27 lags (22 Oct) |
| olbauday/FPL-Core-Insights | `sources/core_insights.py` | sparse git clone | 2025-26, 2026-27: per-GW `matches.csv` (team xG, Elo), `playermatchstats.csv` (FotMob-style xG/xA, defensive actions); **By Tournament**: UCL, UEL, UECL, EFL Cup, Community Shield, Super Cup, friendlies. Native FPL `player_id`/`player_code` | 2024-25 uses another layout (not fetched). Elo full for 2025-26, sparse for 2026-27. `home_team`/`away_team` are team **codes**. Blank (not 0) xG/counts when none. Mixed-offset kickoff strings |
| Understat live | `sources/understat.py` | HTTPS, but serving a maintenance page ("We will come back soon") | normally 2014→now, 6 leagues | down: adapter reports unavailable; mirror used |
| ClubElo | `sources/clubelo.py` | **unreachable** (HTTP-only API, proxy HTTPS-only; site behind a Cloudflare challenge, not bypassed) | — | replaced by our Elo |

**Pipeline:** `fpl history fetch` → `fpl history build` → `fpl history publish`, orchestrated by `history/build.py`.
- **`normalize.fpl_gw`:** vaastav rows keyed by FPL `code`. The current season is replaced by our own tables (`normalize.current_season_fpl`).
- **`normalize.understat_match`:** career rows. The player's own club is inferred per player-season by the most frequent side, iterated for mid-season moves. The league comes from connected components of the match graph, labelled by anchor clubs.
- **`normalize.ci_tables`:** competition from the `tournament` column, deduped on `match_id`, blanks set to 0 when the player played.
- **`elo.run`:** our Elo (K 20, home 60, goal-difference multiplier, 20% season regression, each league its own pool, promoted teams = relegated mean). r = 0.92 against Core-Insights Elo for 2025-26 (380/380 matches).
- **`idmap.build`** (FPL `code` ↔ Understat id):
  1. `history/id_overrides.csv`;
  2. vaastav `id_dict.csv`;
  3. a candidate score of 0.35·name + 0.65·minutes agreement on the same dates (same club and season). Accept at ≥ 0.6 with a margin ≥ 0.15.

  Name scoring folds accents. Identical token sets, or a ≥ 2-token subset, score 1.0; a one-token subset scores 0.6; otherwise Jaccard. A code can't be claimed twice. Outputs: `id_map_review.csv` and `id_map_report.csv` (linked share of players and minutes per season).
- **`tables.player_match`:** one row per player per match, any competition.
  - PL rows merge FPL + Understat + Core-Insights on (code, date ±1 day) via `_merge_by_date`.
  - Separate rows for non-PL Understat leagues and for Core-Insights cups/Europe.

**Tables** (written to `data/history/*.parquet`; published to the data branch under `history/`):
- **`player_match`:**
  - keys: code, season ("2024-25"), date, competition, league, team, opponent, is_home, venue (`stadiums.csv` handles moves), team_elo_pre, opponent_elo_pre
  - involvement: minutes, started, position
  - per-source output: xg_fpl, xa_fpl, xg_us, npxg_us, xa_us, shots_us, key_passes_us, xgchain, xgbuildup, xg_ci, xa_ci, shots_ci, interceptions_ci, clearances_ci, blocks_ci, xgot_ci, minutes_us, minutes_ci
  - canonical picks: **xg / xa** (Understat > Core-Insights > FPL) and **xg_source**
  - goals and assists: goals, **assists_fpl** and **assists_official**, delta_assists_fpl_vs_official
  - FPL columns: fpl_points, bonus, bps, ICT, value, selected, transfers, cards, saves, cbi, tackles, recoveries, dc_count, xgc_fpl
  - **source_mask** (1 FPL, 2 Understat, 4 Core-Insights)
- **`player_season`:** per code × season × competition × team: apps, minutes, starts, goals, both assist definitions, xg, npxg, xa, shots, fpl_points, bonus, per-90s, delta_assists.
- **`team_season`:** PL: matches, goals for/against, xg_for/xg_against (FPL sums 2022+, Understat player sums 2021-22, Understat team files 2019-21), elo_start/end/mean.
- **`team_elo`:** team × match: elo_pre, opponent_elo_pre, is_home, league, season.
- Also: **`id_map`**, **`team_map`**, and source tables `fpl_gw`, `understat_match`, `ci_team_match`.

**Known source differences (both kept):**
- Understat xG runs higher than FPL/Opta. Conversion factors FPL/Understat: xG DEF 0.82, MID 0.83, FWD 0.79; xA 0.54–0.78.
- Core-Insights ≈ FPL (r = 0.996).
- FPL assists exceed official assists by about 23% on overlapping rows (8,261 vs 6,728).

## Judgment layer (`src/fpl/history/judgment/`)
- **`baseline.py`** (estimated, not assumed):
  - **Source scale:** `source_scales`, per position, so all events are on FPL's scale (`canonical_events`).
  - **League factors from movers:** `league_factors`. Shrunk ratio of PL per-90 to the other league's, with ≥ 900 minutes in adjacent seasons. Values: Bundesliga 0.82, La Liga 0.97, Ligue 1 0.85, Serie A 0.85, RFPL 0.90 (2 movers).
  - **Opponent adjustment:** `opponent_slope`, β = 0.75 for team xG per 400 Elo points. `neutral_events` divides each match by exp(β·gap/400).
  - **`baseline(ev, as_of, season, BaselineParams)`:**
    - career rate = recency-weighted (half-life `half_life_days`) over seasons before the current one, shrunk toward the position mean with `m0` pseudo-90s;
    - k = min(`kappa` · effective career 90s, `k_max`) × `team_change` (club changed) × `role_change` (FPL position changed);
    - posterior = (current events + k·career) / (current 90s + k); `career_weight` = k / (current 90s + k).
  - **`rate_backtest`** compares against `current_only` (the engine's present approach). Code defaults: hl 365, κ 0.5, k_max 20, m0 5, discounts 0.6. The 2024-25 rate grid (12 sets) favours **hl 365, κ 2.0, k_max 60** (xG deviance 0.1800 vs 0.2521 current-only, xA 0.1029 vs 0.1292). κ 2.0 is the grid edge, so tuning continues and the defaults are not changed yet (see HANDOFF).
- **`context.py`:** `ramp_up_curve`, minutes vs pre-absence norm by game back after an absence of ≥ 21 days and ≥ 2 matches, regulars only. League and team/role change are handled in `baseline`.
- **`splits.py`** (venue / opponent / is_home):
  - effect = log((O+0.5)/(E+0.5)), where E = own season rate × minutes × opponent factor.
  - Empirical-Bayes shrinkage with τ² across all splits.
  - **A signal requires all of:** n ≥ 6 matches and ≥ 450 minutes; posterior P(sign) ≥ 0.9; **Benjamini–Hochberg 10% on exact Poisson tail p-values** of O given E (via `scipy.special.gammainc`); and |shrunk Δ xPts| ≥ 0.3 for the upcoming fixture (`signals()`).
  - Tested: silent on pure noise. At 12 or 30 matches per split it finds common 4× effects at 0.98 or 0.91 precision. Rare effects shrink away by design.
- **`integrate.prior_fn(ev, season, element_to_code, params)`:** returns `fn(as_of)`, a DataFrame by element with xg90, xa90 (career rates) and k90, for `fit_rates(prior=…)`.
- **Adoption rule:** the history prior is used in production only if the xPts backtest improves MAE **and** Spearman on held-out 2025-26 (full season, via `backtest/past_season.py`) **and** on 2026-27 GW4+. **It is not wired into `pipeline.forecast` yet.**

## Engine design (current-season models)
- **Team strength:** ridge 100 toward the 2025/26 prior. It ties naive season averages late in the season and is far better early.
- **Minutes:** logistic P(start). Brier 0.124 vs 0.149 for baselines.
- **xPts components** (`models/xpts.py`):
  - appearance; goals = xG90 (shrunk) × E[min] × opponent factor;
  - assists; clean sheet = P(60) · exp(−λ_against);
  - goals conceded and saves as E[floor()] of Poisson;
  - DefCon = negative-binomial tail (k = 6) of CBIT/CBIRT per 90;
  - bonus = linear regression on components; cards.
- **xPts backtest, GW4–9:** MAE 1.70 vs 1.89 and Spearman 0.56 vs 0.48 against baselines; calibrated by decile. Weak spot: GKP ranking (0.41 vs 0.45).
- **MILP:** 6-GW solve about 2 s, decay 0.9, bench weight 0.1, end value 1.5 per banked FT. Options are forced first-GW transfer counts plus no-good cuts.
- **Chips:** `chips/joint.py` takes about 6 minutes per run on real data, with every solve gap < 0.1%. The exact joint MILP (`OptInput.chip_search`) is correct on toys but has a 13% gap after 300 s at real size, so it isn't used.
- **Gate** (`report/build.model_health`): xPts must beat both baselines on MAE and Spearman, and P(start) must beat its baselines. On failure the manifest `remove` list takes model datasets off the dashboard.

## Dashboard, refresh, ledger
- **Dashboard:** https://claude.ai/artifact/JU1PKMicRTv6Hbn9yng1PP ("HaramBall FPL", private), built on the claude.ai Dashboard type.
  - The page is `files/index.html` in its store; the repo copy is `src/fpl/ui/dashboard_page.html`.
  - Follow the type's one-writer rules: text in the HTML, values in `data-draw` elements with `data-source`/`data-field`.
  - After writing the page, read the `data/users/me` `check` document.
- **Sections:**
  - **Model sections** (`data-model-section`, hidden if `decision` is missing): This gameweek, Planner, First-half chips, Players, Fixtures.
  - **Model health.**
  - **Track record** (`data-review-section`).
  - **Your team now.**
  - **About.**
- **Datasets:** state (`summary`, `squad`, `chips`, `ft_history`); model (`decision`, `captaincy`, `lineup`, `plan`, `chip_gain`, `chip_plan`, `chip_summary`, `players`, `fixtures_strength`, `model_health`, `calibration`, `assumptions`); track record (`review`, `review_summary`).
- **Refresh routine** `trig_017HWLLYxFxVKABs973LRLWN` ("FPL dashboard refresh", daily 03:23 Europe/London, a fresh session each time). It:
  1. clones the repo;
  2. restores store `ledger/*` documents to `data/ledger/`;
  3. runs `fpl dashboard-data`;
  4. uploads each manifest file, updates or creates `datasets/<id>`, and deletes the old assets;
  5. processes `remove`;
  6. saves `ledger/gwNN`;
  7. schedules a one-shot pre-deadline run 3 h before a deadline that is 3–27 h away.

  It prefers `main` once `main` contains `src/fpl/report/ledger.py`.
- **Ledger:**
  - The store keeps `ledger/gw10`, reconstructed with `build_state(next_gw=10)`: Barry for N.Jackson +2.09, C Haaland, VC Gabriel.
  - `ledger/gw11` is also stored.
  - `fpl review --gw N` grades a finished, data-checked GW.

## Data persistence (decided by the user)
- **The `data` branch is always ONE commit.** `fpl history publish` (`history/publish.py`):
  1. fetches the tip with `--depth 1` and reads its tree, keeping other writers' files;
  2. stages the given paths (default `history/`) and `.gitignore`;
  3. writes a parentless commit and pushes with `--force-with-lease` pinned to the fetched tip, with 3 retries.
- The snapshot workflow publishes `raw/`, `deadlines/` and `README.md` the same way.
- **Never commit raw dumps:** the branch `.gitignore` ignores `external/`, `processed/` and `ledger/`.
- **New sessions:**
  ```bash
  git fetch --depth 1 origin data
  git worktree add data FETCH_HEAD   # or -B data data FETCH_HEAD
  ```
  `FPL_DATA_DIR` defaults to `./data`. Rebuild raw sources with `fpl history fetch`.
- The data branch currently holds `raw/2026-11-06/`, `deadlines/gw10/`, `history/*.parquet` (about 20 MB), `COVERAGE.md`, `id_map_report.csv` and `id_map_review.csv`.

## How to run
```bash
uv sync
uv run fpl doctor                     # every endpoint; non-zero exit on failure
uv run fpl state                      # squad, prices, bank, FTs, chips, FT self-check
uv run fpl build-tables               # current-season tables -> data/processed/
uv run fpl plan [--horizon 6]         # terminal decision report
uv run fpl review --gw N              # grade a finished GW from data/ledger/gwNN.json
uv run fpl dashboard-data --out DIR [--no-refresh-tables] [--eo-sample 0] [--no-chips]
uv run fpl ui                         # Streamlit what-if app
uv run fpl history fetch              # raw sources -> data/external/ (git sparse clones)
uv run fpl history build              # -> data/history/*.parquet + COVERAGE.md (~1 min)
uv run fpl history publish            # data branch, single commit
uv run fpl history report             # print COVERAGE.md
```

## How to test
```bash
uv run pytest            # offline suite (72 tests); live: uv run pytest -m live
uv run ruff check .
```
- `tests/fixtures/` is the GW9 state of entry 1020085 (personal fields stripped).
- `tests/test_repo.py` fails if any source or test file is gitignored. An unanchored `data/` rule once hid `src/fpl/data`; the rule is now `/data/`.
- Every model ships with a backtest; every optimizer constraint ships with a toy test (`tests/test_milp.py`).

## Conventions and guardrails
- Money is integer £0.1m.
- Rules come from `GameRules`; never hardcode what the API exposes.
- Purchase prices exclude FH GW transfers. Affordability is selling prices + bank.
- The FT replay self-check must pass. A mismatch means a rule change or top-up: set `ft_overrides`, never silence it.
- Models produce distributions. Captaincy, TC, BB and EO need variance.
- **No lookahead.** Backtests refit before each round; history priors use only seasons before the target season, and only rows before `as_of`.
- After a deadline, unplayed fixtures appear in element-summary with 0 minutes; `data/tables.py` filters them.
- Be polite to APIs (throttled clients, snapshots reused). **No bot-check circumvention** (no spoofed browser user agents).
