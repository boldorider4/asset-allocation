#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add a user account to the SQLite database.

Usage::

    python scripts/add_users.py <asalloc.db> <user> [--password PW]

The account is written to the ``users`` table and gets a matching
``user_portfolios`` row when it has none yet. An account that already
exists keeps its password unless a new one is given; a new account gets
an empty password when none is given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.users import add_user_account  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add a user account to the SQLite database."
    )
    parser.add_argument("db_file", type=Path, help="SQLite database file.")
    parser.add_argument("user", help="User name (the account key).")
    parser.add_argument(
        "--password",
        default=None,
        help="Password for the account (default: empty for a new account, kept for an existing one).",
    )
    args = parser.parse_args(argv)

    user = args.user.strip()
    if not user:
        print("error: the user name must not be empty", file=sys.stderr)
        return 2
    try:
        created = add_user_account(args.db_file, user, args.password)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if created:
        print(f"added user {user!r} to {args.db_file}")
    else:
        print(f"user {user!r} already exists in {args.db_file} (password updated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
