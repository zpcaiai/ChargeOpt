from __future__ import annotations

import os
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

    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS chargeopt")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chargeopt.schema_migrations (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                version = migration.stem
                cursor.execute("SELECT 1 FROM chargeopt.schema_migrations WHERE version = %s", (version,))
                if cursor.fetchone():
                    print(f"Migration {version} already applied.")
                    continue
                print(f"Applying migration {version}...")
                with connection.transaction():
                    cursor.execute(migration.read_text(encoding="utf-8"))
                    cursor.execute(
                        "INSERT INTO chargeopt.schema_migrations (version) VALUES (%s)",
                        (version,),
                    )
    print("ChargeOpt database migrations complete.")


if __name__ == "__main__":
    main()
