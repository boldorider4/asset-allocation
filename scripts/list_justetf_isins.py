#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""List every ISIN whose registry issuer is ``justetf``.

Usage::

    python scripts/list_justetf_isins.py --db-file ~/.local/asalloc/asalloc.db

The database is opened read-only. A missing database or missing
``isin_registry`` table is an error; neither is created. Each matching
ISIN is printed on its own line, sorted, so the output can be piped into
``scripts/rescan_isin_issuers.py``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def list_justetf_isins(db_file: Path) -> list[str]:
    """Return the registry's ``justetf`` ISINs in sorted order."""
    if not db_file.is_file():
        raise FileNotFoundError(f"database not found: {db_file}")
    conn = sqlite3.connect(_read_only_uri(db_file), uri=True)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'isin_registry'"
        ).fetchone()
        if table is None:
            raise ValueError(f"table 'isin_registry' not found in {db_file}")
        rows = conn.execute(
            "SELECT isin FROM isin_registry WHERE issuer = ? ORDER BY isin",
            ("justetf",),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List ISINs whose registry issuer is justetf."
    )
    parser.add_argument(
        "--db-file",
        required=True,
        type=Path,
        help="SQLite database file.",
    )
    args = parser.parse_args(argv)

    try:
        isins = list_justetf_isins(args.db_file)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for isin in isins:
        print(isin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
