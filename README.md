# FPL snapshots (data branch)

Written by `.github/workflows/snapshot.yml` on the main code branch. Do not merge into code branches.

- `raw/<UTC date>/<endpoint>.json.gz`: daily snapshot (latest run of the day).
- `deadlines/gwNN/`: last capture in the 3h before GW NN's deadline. This is the as-of-deadline record that
  backtests must use for prices, flags, ownership and price predictions. `captured_at.txt` gives the exact time.

To use the snapshots locally: `git worktree add data data` from the code checkout, so `FPL_DATA_DIR` defaults to `./data`.
