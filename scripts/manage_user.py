"""Provision, rotate, or disable a ChargeOpt database user without exposing passwords."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import string
from collections.abc import Sequence

import psycopg

from chargeopt.auth import ROLE_PERMISSIONS, hash_password


def validate_password(password: str) -> None:
    checks = (
        (len(password) >= 16, "at least 16 characters"),
        (any(char.islower() for char in password), "a lowercase letter"),
        (any(char.isupper() for char in password), "an uppercase letter"),
        (any(char.isdigit() for char in password), "a digit"),
        (any(char in string.punctuation for char in password), "a symbol"),
    )
    missing = [description for passed, description in checks if not passed]
    if missing:
        raise ValueError("Password must contain " + ", ".join(missing) + ".")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--tenant-id")
    parser.add_argument("--display-name", default="ChargeOpt Administrator")
    parser.add_argument("--role", choices=sorted(ROLE_PERMISSIONS), default="tenant_admin")
    parser.add_argument("--disable", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required.")
    if args.role != "platform_admin" and not args.tenant_id:
        raise SystemExit("--tenant-id is required for tenant-scoped roles.")

    with psycopg.connect(database_url) as conn, conn.transaction():
        conn.execute("SELECT set_config('chargeopt.tenant_id', '*', true)")
        existing = conn.execute(
            "SELECT id FROM chargeopt.users WHERE lower(email) = lower(%s)",
            (args.email,),
        ).fetchone()
        if args.disable:
            if existing is None:
                raise SystemExit("User does not exist.")
            conn.execute("UPDATE chargeopt.users SET active = false WHERE id = %s", (existing[0],))
            conn.execute(
                "UPDATE chargeopt.sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
                (existing[0],),
            )
            print(f"Disabled {args.email} and revoked active sessions.")
            return

        password = getpass.getpass("New password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if not secrets.compare_digest(password, confirmation):
            raise SystemExit("Passwords do not match.")
        validate_password(password)
        salt = secrets.token_hex(16)
        user_id = str(existing[0]) if existing else f"usr-{secrets.token_hex(16)}"
        conn.execute(
            """
            INSERT INTO chargeopt.users (
                id, tenant_id, email, display_name, role, password_salt, password_hash, active
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,true)
            ON CONFLICT (email) DO UPDATE SET
                tenant_id=EXCLUDED.tenant_id,
                display_name=EXCLUDED.display_name,
                role=EXCLUDED.role,
                password_salt=EXCLUDED.password_salt,
                password_hash=EXCLUDED.password_hash,
                active=true
            """,
            (
                user_id,
                args.tenant_id,
                args.email.lower(),
                args.display_name,
                args.role,
                salt,
                hash_password(password, salt),
            ),
        )
        if existing:
            conn.execute(
                "UPDATE chargeopt.sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
                (user_id,),
            )
    print(f"Provisioned {args.email}; existing sessions were revoked.")


if __name__ == "__main__":
    main()
