"""Historical data layer: multi-source ingest, player ID map, normalised tables, judgment.

Raw sources are fetched into ``data/external/`` (never committed). Processed tables are
written to ``data/history/`` and published to the ``data`` branch as a single amended
commit (see CLAUDE.md, "Historical data layer").
"""
