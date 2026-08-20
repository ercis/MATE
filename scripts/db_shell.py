"""Interactive DuckDB shell with SQLite metadata attached.

Run:  uv run python scripts/db_shell.py
Then: sql("select id, user_id, name, status from meta.process_logs limit 5")  # SQLite under meta.*
      sql("select * from meta.users")
      sql("select * from read_parquet('data/users/<uid>/event_logs/<log>/events.parquet') limit 5")
      logs()                                                # list parquet event logs on disk
      tables()                                              # list all metadata tables
"""

from __future__ import annotations

import code
from pathlib import Path

import duckdb

DATA = Path("data")
SQLITE = DATA / "metadata.db"

con = duckdb.connect(":memory:")
con.execute("INSTALL sqlite; LOAD sqlite;")
# Attach SQLite metadata read-only as schema `meta`.
con.execute(f"ATTACH '{SQLITE}' AS meta (TYPE sqlite, READ_ONLY);")


def sql(query: str):
    """Run a query, print it as a table."""
    con.sql(query).show()


def tables():
    """List SQLite metadata tables."""
    sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_catalog='meta' ORDER BY table_name"
    )


def logs():
    """List on-disk Parquet event logs as (user_id, log_id, path)."""
    rows = sorted(DATA.glob("users/*/event_logs/*/events.parquet"))
    for p in rows:
        uid, log_id = p.parts[-4], p.parts[-2]
        print(f"{uid}  {log_id}  {p}")
    print(f"\n{len(rows)} log(s)")


if __name__ == "__main__":
    banner = (
        "DuckDB + SQLite(meta.*) ready.\n"
        "  sql('...')   run query\n"
        "  tables()     list metadata tables\n"
        "  logs()       list parquet event logs\n"
        "  con          raw duckdb connection"
    )
    code.interact(banner=banner, local=dict(globals()), exitmsg="bye")
