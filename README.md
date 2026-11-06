# fpl-tool

FPL decision engine: xPts, minutes, own fixture strength, multi-GW MILP optimizer, chip planner.

```bash
uv sync
uv run fpl doctor   # verify API access
uv run fpl state    # squad, selling prices, bank, free transfers, chips
uv run pytest
```

See [CLAUDE.md](CLAUDE.md) for requirements, 2026/27 rules, API endpoints, architecture and conventions.
