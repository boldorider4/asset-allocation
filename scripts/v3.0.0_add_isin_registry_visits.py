#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add the ``visits`` counter to ``isin_registry``.

Usage::

    python scripts/v3.0.0_add_isin_registry_visits.py --db-file asalloc.db

Existing rows receive ``visits = 0``. The migration is idempotent: when
the column is already present it changes nothing and exits successfully.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TABLE = "isin_registry"
_COLUMN = "visits"


def add_visits_column(db_file: Path, *, dry_run: bool = False) -> bool:
    """Add the column when missing; return True when it was absent."""
    if not db_file.is_file():
        raise FileNotFoundError(f"database not found: {db_file}")
    conn = sqlite3.connect(str(db_file))
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = ?",
            (_TABLE,),
        ).fetchone()
        if table is None:
            raise ValueError(f"table '{_TABLE}' not found in {db_file}")
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
        }
        if _COLUMN in columns:
            return False
        if dry_run:
            return True
        conn.execute(
            f"ALTER TABLE {_TABLE} ADD COLUMN {_COLUMN} "
            "INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
        return True
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add the isin_registry.visits counter column."
    )
    parser.add_argument(
        "--db-file",
        required=True,
        type=Path,
        help="SQLite database file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report whether the column is missing without changing the database.",
    )
    args = parser.parse_args(argv)

    try:
        changed = add_visits_column(args.db_file, dry_run=args.dry_run)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not changed:
        print("isin_registry.visits already exists; nothing to do")
    elif args.dry_run:
        print("isin_registry.visits is missing; would add it")
    else:
        print("added isin_registry.visits (default 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
