"""Decision report. NOT BUILT YET.

Lead with decisions and the expected-points delta of each option (transfer vs. roll vs. hit,
captain, XI and bench order, chip). Suppress template moves unless timing or hit-vs-roll is
the non-obvious part. Flag differentials and divergence from high-ownership picks using
effective ownership estimated from a sampled top-k of league 314
(``leagues-classic/314/standings`` + their picks). Always show assumptions and confidence,
and never list a player the manager cannot afford with real selling prices + bank.
"""
