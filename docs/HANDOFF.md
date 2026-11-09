# HANDOFF: FPL decision engine (entry 1020085)

Last updated 2026-11-09 (dashboard nav + formation view added late that day). Read with `CLAUDE.md`, which describes the system; this file covers state, decisions and next steps.

- Code branch: `claude/fpl-engine-architecture-ldhmeh`. PR: https://github.com/leopapadopoulos6/fpl-tool/pull/1 (open, not merged; `main` lacks everything after the scaffold).
- Data branch: `data`, one parentless commit (raw/2026-11-06, deadlines/gw10, history/*.parquet).
- Dashboard: https://claude.ai/artifact/JU1PKMicRTv6Hbn9yng1PP.
- Refresh routine: `trig_017HWLLYxFxVKABs973LRLWN`.

## 1. Start a new session
```bash
uv sync
git fetch --depth 1 origin data && git worktree add -B data data FETCH_HEAD   # ./data = data branch
uv run pytest -q && uv run ruff check .                                     # 72 passed, clean
uv run fpl history fetch      # only if you need raw sources (data/external/, ~minutes, git sparse clones)
uv run fpl history build      # rebuild data/history/*.parquet (~1 min) if code changed
```
`data/history/*.parquet` comes with the data branch, so most work needs no fetch. `data/processed/` (current-season tables) is rebuilt by `uv run fpl build-tables`, which needs the FPL API.

## 2. Decisions and why

### Decided by the user

| Decision | Reason / user's words |
|---|---|
| **Hybrid UI.** A claude.ai Dashboard artifact for the phone, plus a local Streamlit what-if app (`fpl ui`) | The user wanted a GUI over a CLI. The dashboard is readable on a phone; Streamlit handles the interactive what-ifs. |
| **Snapshots and processed tables live on the `data` branch, not `main`** | Keeps the code history clean. |
| **The `data` branch is always ONE commit.** Each refresh writes a parentless commit and force-pushes it with a lease; sessions fetch with `--depth 1` | "Use the data branch, but keep it to a single commit — amend and force-push on each refresh instead of stacking commits, and have sessions fetch it with --depth 1. Code history on main stays clean and the data branch never grows." Implemented in `src/fpl/history/publish.py`; the snapshot workflow uses the same path (`fpl history publish --path raw --path deadlines --path README.md`). |
| **ClubElo: our own Elo plus a ClubElo adapter** | ClubElo is unreachable (see §6). `history/elo.py` is primary; `sources/clubelo.py` cross-checks only when reachable. |
| **Understat: mirror now, live later** | understat.com serves a maintenance page. The vaastav mirror has per-player Understat career files; `sources/understat.py` detects the maintenance page and reports the source unavailable. |
| **No FBref, no Opta** | FBref has dropped its advanced stats; Opta isn't free. |
| **Never commit raw dumps**; only compact parquet plus fetch scripts | User rule. The data-branch `.gitignore` covers `external/`, `processed/` and `ledger/`. |
| **Show the plan before writing files** for large features | User rule for the history layer. Follow it for comparable work. |

### Decided in-session (with reason)

| Decision | Reason |
|---|---|
| `HIT_HURDLE = 1.0` xPts (`engine.py`) | A −4 must beat rolling by a margin because of model noise. |
| `CAPTAIN_HURDLE = 1.0`: keep the top-10k majority captain unless another option beats him by ≥ 1 xPt | GW10: the raw model preferred Gabriel over Haaland by a sliver, which is noise against a near-universal Haaland EO. |
| Joint chips by coordinate search over fixed-chip MILP solves (`chips/joint.py`), not the exact joint MILP | `OptInput.chip_search` is correct on toy cases but had a 13% gap after 300 s at real size (weak relaxation). The coordinate search takes about 6 min, with every solve gap < 0.1%. |
| Dashboard view choice (Formation/List) kept in the page param `squad_view`, not localStorage | The Dashboard type forbids browser storage, cookies and network in page code. Whether params survive a reload is **unverified**; the fallback is the link `#squad-list` |
| Both squad views render the same `squad` rows; model fields merged into them by `report/build.build(squad_rows=…)` | The user asked that List and Formation never disagree. "Engine's pick" uses `lineup` rows with the same field names |
| Fixture difficulty = our own 1–5 quintile rating (`fixture_ratings`): attack = team xG for (MID/FWD), defence = clean-sheet chance (GKP/DEF) | No FPL FDR (CLAUDE.md rule). Quintiles are over all team fixtures in the 6-GW horizon |
| Team colours: a two-colour dot per club (`TEAM_COLORS` by team `code`) | The user asked for our own styling, with no FPL logos, kits or copied assets |
| Renamed anchors: `#this-gw`→`#transfers`, `#chip-gain`→`#chips`, `#chips`→`#chip-status`, `#transfers`→`#ft-history`, `#team-now`→`#team` | The user's examples (#transfers, #chips) should land on the decision sections. Old ids are aliased in the hash handler |
| Ledger stored in the artifact store (`ledger/gwNN`), restored by the routine | The container is ephemeral. The store survives, and `data/ledger/` is gitignored. |
| GW10 ledger entry reconstructed with `build_state(next_gw=10)` | The ledger started after the GW10 deadline. The reconstruction is the pre-deadline view: Barry for N.Jackson +2.09, C Haaland, VC Gabriel. |
| Element-summary rows: keep only finished or provisionally finished fixtures | After a deadline FPL lists that GW's fixtures with 0 minutes. This once corrupted minutes and form (fixed in `data/tables.py`, commit 56fac06). |
| `.gitignore` uses `/data/` (anchored) plus the `tests/test_repo.py` guard | An unanchored `data/` hid `src/fpl/data`. |
| Master player key = FPL `code`; Understat linked via `id_overrides.csv` → vaastav `id_dict.csv` → candidate scoring | `code` is stable across seasons and native in Core-Insights. |
| Name score: a one-token subset scores 0.6 (was 1.0) | Surname-only web names over-matched namesakes. |
| Canonical xG/xA: Understat > Core-Insights > FPL, all rescaled to FPL's scale per position (`baseline.source_scales`) | Understat xG runs about 20% higher than Opta/FPL. Mixing sources unscaled biases career rates. |
| League factors estimated from movers (`baseline.league_factors`), not assumed | The user asked for estimated, not assumed, adjustments. Only RFPL is thin (2 movers, shrunk). |
| Opponent adjustment by Elo slope β = 0.75 (`baseline.opponent_slope`, estimated) | History is put on a neutral-opponent basis; the forecast re-applies the fixture through the team model. |
| Split gate: BH on **exact Poisson tail p-values of the raw O vs E**, size from the **EB-shrunk** effect, se² = 1/(E + 0.5) | Three fixes in turn: the wrong se² formula; BH on shrunk z double-counted multiplicity; normal p-values were badly anti-conservative at small E. Now silent on noise; precision 0.98 at 12 matches per split and 0.91 at 30. |
| History prior is **not** in production until it passes the adoption gate (CLAUDE.md, Judgment layer) | Backtest gate rule: no model feeds the optimizer before it beats the baselines. |

## 3. Status

### Done and verified
- **Engine:**
  - all current-season models, the MILP and the engine, with toy tests for every constraint;
  - the gate in `report/build.model_health` (xPts GW4–9: MAE 1.70 vs 1.89, Spearman 0.56 vs 0.48; minutes Brier 0.124 vs 0.149).
- **Dashboard and ledger:**
  - the dashboard is live, refreshed daily by the routine; the ledger holds `ledger/gw10` and `ledger/gw11`;
  - `fpl review --gw N` works on fixtures.
- **History build** (`fpl history fetch && fpl history build`, ~1 min):
  - `player_match` has 303,031 rows; COVERAGE.md is on the data branch under `history/`;
  - ID map: 1,212/1,213 Understat players linked, ≥ 99.8% of PL minutes 2020-25, scorer-only links agree with id_dict 766/766;
  - our Elo vs Core-Insights Elo 2025-26: r = 0.921, Spearman 0.945, 380/380 matches.
- **Judgment estimates:**
  - source scales: xG DEF 0.822, MID 0.834, FWD 0.789, GKP 1.245; xA 0.539–0.777;
  - league factors: Bundesliga 0.823, La Liga 0.970, Ligue 1 0.847, Serie A 0.852, RFPL 0.902.
- **Rate-level backtest, 2024-25** (`rate_backtest`): the blend cuts deviance about 25–29% (xG) and 16–20% (xA) vs current-only.
- **Tuning grid finished** (12 sets: hl {365, 730} × κ {0.5, 1, 2} × k_max {20, 60}; `scripts/research/tune_baseline.py`). Score = blend/current deviance, xG + xA:

  | hl | κ | k_max | xG blend | xA blend | score |
  |---|---|---|---|---|---|
  | 365 | 2.0 | 60 | 0.17999 | 0.10288 | 1.5101 |
  | 365 | 2.0 | 20 | 0.18036 | 0.10284 | 1.5113 |
  | 365 | 1.0 | 60 | 0.18030 | 0.10331 | 1.5147 |
  | 365 | 0.5 | 20 (code default) | 0.18163 | 0.10435 | 1.5280 |

  Current-only: xG 0.25211, xA 0.12922. Higher κ keeps helping and κ 2.0 is the grid edge, so the optimum isn't bracketed yet. Earlier `tune.py` runs also covered hl 180 (worse).
- **Data branch:** a single commit, verified with `git rev-list --count origin/data` = 1 after `fpl history publish`.
- **Tests:** 72 offline tests pass; ruff is clean.

### Built but untested or not yet exercised
- **Snapshot workflow publish step** (`fpl history publish --path raw --path deadlines ...`): it has never run in Actions, because scheduled workflows run only from `main`, which isn't merged. `publish()` has been tested in one real local publish only.
- **`backtest/past_season.py`** (`season_tables`, `team_prior_for`) and `scripts/research/season_backtest.py`: written; the 2025-26 full-season run hasn't completed.
- **`context.ramp_up_curve`:** not run on real data, not used by any model.
- **`splits.signals()`:** the gate is tested on simulated data only. No signals dataset exists, and there's no `upcoming_attack_xpts` builder.
- **Live adapters:** `sources/understat.py` live paths (site down) and `sources/clubelo.py` (unreachable).
- **ID map after the name-score change:** the 0.6 one-token rule is unit-tested, but `fpl history build` hasn't been rerun since, so the published `id_map*.csv` may predate it.
- **Routine:** it has never run with the history layer; its prompt doesn't fetch `history/`.
- **History-prior plumbing:** `models/xpts.fit_rates(train, prior=...)` and `backtest/xpts.predictions(..., rate_prior_fn=...)` are wired and run, but `pipeline.forecast` doesn't use them.

### Dashboard nav + formation view (2026-11-09)
- **Done and verified:**
  - store page `files/index.html` written (version 6), from the repo copy `src/fpl/ui/dashboard_page.html`; `dash/params` set to `{squad_view: "formation"}`;
  - `uv run pytest -m ui` passes (Playwright via Node at 390/320/400/1280px, light and dark).
- **Not yet verified on the live dashboard:**
  - the type's `check` report: no `data/users/me` `check` document existed right after the write, because nobody had the page open. Read it after the user opens it;
  - whether `squad_view` survives a reload;
  - the real shell's scroll container: the sticky nav and scroll-spy were tested only in the local harness.
- **Live data:** the refresh routine must run once with this branch so `squad`/`lineup` carry the new fields. Until then cards show "—" for xPts, with no ratings, C/V badges or engine tags.

## 4. In progress: exactly where it stopped
1. **History-prior adoption gate.**
   - Current-season backtest, 2026-27 GW4–9, with code-default params (365, 0.5, 20), via `scripts/research/current_season_backtest.py`:
     - MAE 1.7044 → 1.7001 (better);
     - Spearman 0.5616 → 0.5613 (worse by 0.0003, so the gate **fails** on Spearman);
     - top-20 4.29 → 4.42; bias −0.047 → −0.072.
   - The tuned params (365, 2.0, 60) have **not** been tried at the xPts level.
   - The 2025-26 full-season run has **not** been done.
2. **Tuning:** the 12-set grid is done (above). Next, extend κ to {3, 5, 8} and k_max to {60, 120}.

## 5. Next steps, in order
1. **Rebuild cached events:**
   ```bash
   uv run python scripts/research/build_events.py
   ```
   This writes `data/research/ev.parquet`, ~15 MB, from `data/history/player_match.parquet`.
2. **Extend the grid:** edit the `grid` in `scripts/research/tune_baseline.py` to `(365,) × (2, 3, 5, 8) × (60, 120)`, run it, and pick by score.
3. **2025-26 full-season backtest**, best params, with and without the prior:
   ```bash
   uv run python scripts/research/season_backtest.py 365 <κ> <k_max>
   ```
   - Needs `data/external/vaastav` (`fpl history fetch`) and FPL API access for `GameRules`.
   - Reports overall, by position and by GW4–10 vs GW11–38.
   - Needed for the no-lookahead check: `prior_fn(ev, "2025-26", ...)` uses only seasons before 2025-26 and rows before `as_of`.
4. **Current-season backtest** with the same params:
   ```bash
   uv run python scripts/research/current_season_backtest.py 365 <κ> <k_max>
   ```
5. **Adoption gate:** MAE **and** Spearman must improve on both (3) and (4). If they pass:
   - set the `BaselineParams` defaults;
   - wire `integrate.prior_fn` into `pipeline.forecast` → `fit_rates(prior=...)`, behind a flag in `report/build.py`;
   - add with/without rows to the `model_health` dataset.

   If they fail, record it in `model_health`/`assumptions` and leave production unchanged.
6. **Rerun `uv run fpl history build`**, check `id_map_report.csv` (≥ 99% of PL minutes 2019-25) and `id_map_review.csv`, then `uv run fpl history publish`.
7. **Signals dataset:**
   - build `upcoming_attack_xpts` from `pipeline.forecast` (goal + assist xPts per fixture, keyed by code / venue / opponent / is_home);
   - run `split_effects` → `shrink_and_test` → `signals`;
   - add a dashboard section showing n, minutes, observed/expected and Δ xPts (CLAUDE.md output rule).
8. **Minutes model:**
   - add congestion features (Core-Insights cup/European minutes in the previous 3–7 days) and `ramp_up_curve` games-back;
   - keep each only if P(start) Brier improves (`backtest/minutes.py`).
9. **Routine prompt** (`update_trigger` on `trig_017HWLLYxFxVKABs973LRLWN`): make `git fetch --depth 1 origin data` and use `history/`, needed only once step 5 or 7 lands.
10. **Merge PR #1 to `main`** (user decision). The snapshot workflow and the routine's `main` preference depend on it. After merging, check that the first scheduled snapshot run publishes and that `data` stays at one commit.
11. **Grade GW10** once `event-status` shows `bonus_added` and scores are locked: `uv run fpl review --gw 10`. The routine also does this.
12. **Retry the live adapters** (Understat, ClubElo) when reachable; they'd fill 2025-26+ Understat and give an external Elo check.

## 6. Known issues, data gaps, open questions
- **Model weaknesses:**
  - GKP ranking is the weak spot (Spearman 0.41 vs 0.45 baseline).
  - Chip plan is provisional: it depends on the horizon's xPts (to GW19+4), and late-horizon fixtures carry little signal.
- **Understat coverage:**
  - no Understat data for 2025-26+; recent seasons rely on Core-Insights and FPL xG;
  - pre-2021 Understat careers exist only for players who appear in a 2021-25 `understat/` folder; players who left the PL before 2021 have FPL-only history;
  - pre-PL careers of players who joined in 2025+ are missing.
- **Core-Insights:**
  - the 2024-25 layout differs and isn't fetched;
  - Elo is sparse for 2026-27 (we use our own);
  - cup and European rows have no opponent Elo, so they're unadjusted.
- **Elo:** non-PL pools are partial (only matches with a linked player), so non-PL Elo is less accurate.
- **Assists:** FPL assists exceed official by about 23% on overlapping rows. Both are kept (`assists_fpl`, `assists_official`); FPL scoring uses `assists_fpl`.
- **`team_season` xG:** comes from mixed sources by era (FPL sums 2022+, Understat player sums 2021-22, Understat team files 2019-21), so the level differs by era.
- **Config:** `FPL_TEAM_ID` wasn't set in the session environment that built the layer; config falls back to `FPL_ENTRY_ID`, then 1020085.
- **Open questions for the user:**
  - merge PR #1?
  - watch PR #1 with PR-activity subscription? (asked, unanswered)

## 7. Environment constraints (cloud container)
- **Network:**
  - Egress goes through an HTTPS-CONNECT-only proxy. **Plain HTTP fails (405)**, so HTTP-only APIs are unusable.
  - **GitHub:**
    - the REST API and web pages (github.com HTML) are blocked;
    - the git protocol (clone/fetch/push) and raw.githubusercontent.com work, so sources use sparse, blob-filtered git clones (`history/sources/gitsparse.py`);
    - GitHub actions in-session go through the GitHub MCP tools; `gh` is for search only.
  - **understat.com:** reachable, but every URL (incl. robots.txt) returns the "We will come back soon" maintenance page.
  - **ClubElo:**
    - `api.clubelo.com` is HTTP-only (HTTPS fails: DNS or connection reset);
    - `clubelo.com` is behind a Cloudflare JS challenge. **Don't bypass it.**
  - **Other blocked sites:** premierleague.com, fantasyfootballscout.
  - **FPL API:** works. Use the throttled `FPLClient`; snapshots go to `data/raw/<date>`.
- **Policy:** requests with a spoofed browser user agent were denied as bot-check circumvention. Use the default client UA, never a browser UA.
- **Container:**
  - ephemeral: it restarts and loses `data/external`, `data/processed` and the scratchpad, so commit or push anything that matters;
  - 4 CPUs (the tuning grid runs with `Pool(4)`, about 2 min per parameter set);
  - scheduled GitHub workflows run only from the default branch (`main`).
- **Dashboard artifact rules:**
  - Dashboard type: text goes in the HTML; values go in `data-draw` elements fed by datasets.
  - After writing the page, read the store's `data/users/me` `check` document.
  - The routine is the single writer of datasets.
