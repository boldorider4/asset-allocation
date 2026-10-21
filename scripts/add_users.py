#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add a user account to the users store (users.json).

Usage::

    python scripts/add_users.py <users.json> <user> [--password PW]
                                [--backend json|postgres] [--postgres-dsn DSN]

The store is keyed by user name — the same key a database table would
use — and holds the password::

    {"default": {"password": ""}}

An account that already exists keeps its password unless a new one is
given; a new account gets an empty password when none is given.

The backend decides how the account is written: ``json`` (the default)
modifies the file, ``postgres`` is a stub that does not work yet (the
intended statement is in ``cli/users.py::add_user_postgres``).

The script touches **only** the file you pass.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.users import add_user_account  # noqa: E402
from storage.wiring import (  # noqa: E402
    BACKEND_JSON,
    DEFAULT_POSTGRES_DSN,
    SUPPORTED_BACKENDS,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add a user account to the users store."
    )
    parser.add_argument("users_file", type=Path, help="Users JSON file to add the account to.")
    parser.add_argument("user", help="User name (the account key).")
    parser.add_argument(
        "--password",
        default=None,
        help="Password for the account (default: empty for a new account, kept for an existing one).",
    )
    parser.add_argument(
        "--backend",
        choices=list(SUPPORTED_BACKENDS),
        default=BACKEND_JSON,
        help="Store backend (default: json; postgres is a stub that does not work yet).",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=DEFAULT_POSTGRES_DSN,
        help="Postgres connection string (only used by the postgres backend).",
    )
    args = parser.parse_args(argv)

    user = args.user.strip()
    if not user:
        print("error: the user name must not be empty", file=sys.stderr)
        return 2
    try:
        created = add_user_account(
            args.users_file,
            user,
            args.password,
            backend=args.backend,
            postgres_dsn=args.postgres_dsn,
        )
    except NotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if created:
        print(f"added user {user!r} to {args.users_file}")
    else:
        print(f"user {user!r} already exists in {args.users_file} (password updated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
