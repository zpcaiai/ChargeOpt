from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        if os.environ.get("CHARGEOPT_SKIP_DB_MIGRATION") == "1":
            print("Skipping database migration because CHARGEOPT_SKIP_DB_MIGRATION=1.")
            return
        raise SystemExit("DATABASE_URL is required to run ChargeOpt database migrations.")

    import psycopg

    t_start = time.monotonic()
    # autocommit=False (the default) lets us use explicit transactions correctly.
    # We bootstrap the schema and migrations table outside a transaction using
    # separate autocommit connections so that CREATE SCHEMA / CREATE TABLE
    # cannot be rolled back by a later failure.
    with psycopg.connect(database_url, autocommit=True) as bootstrap:
        bootstrap.execute("CREATE SCHEMA IF NOT EXISTS chargeopt")
        bootstrap.execute(
            """
            CREATE TABLE IF NOT EXISTS chargeopt.schema_migrations (
                version     text        PRIMARY KEY,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )

    applied = 0
    with psycopg.connect(database_url) as conn:
        for migration_path in sorted(MIGRATIONS.glob("*.sql")):
            version = migration_path.stem
            row = conn.execute("SELECT 1 FROM chargeopt.schema_migrations WHERE version = %s", (version,)).fetchone()
            if row:
                print(f"  skip  {version} (already applied)")
                continue
            t_mig = time.monotonic()
            print(f"  apply {version} …", end=" ", flush=True)
            with conn.transaction():
                conn.execute(migration_path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO chargeopt.schema_migrations (version) VALUES (%s)", (version,))
            elapsed_ms = round((time.monotonic() - t_mig) * 1000)
            print(f"done ({elapsed_ms} ms)")
            applied += 1

    total_ms = round((time.monotonic() - t_start) * 1000)
    print(f"ChargeOpt migrations complete: {applied} applied in {total_ms} ms.")


if __name__ == "__main__":
    main()
