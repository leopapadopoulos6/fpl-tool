# fpl-tool

FPL decision engine for entry 1020085: xPts from underlying data, minutes model, own fixture
ratings, multi-GW MILP optimizer (HiGHS), chip planner, top-10k effective ownership, all
backtested before use.

- **Phone:** the private dashboard at https://claude.ai/artifact/JU1PKMicRTv6Hbn9yng1PP,
  refreshed daily and 3 h before each deadline by a Claude routine.
- **Laptop:** `uv run fpl ui` for live what-ifs.

```bash
uv sync
uv run fpl doctor        # verify API access
uv run fpl plan          # decision report in the terminal
uv run fpl ui            # what-if app
uv run pytest
```

See [CLAUDE.md](CLAUDE.md) for requirements, 2026/27 rules, API endpoints, architecture, backtest
results and conventions.
