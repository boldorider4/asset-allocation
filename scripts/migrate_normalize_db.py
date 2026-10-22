#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Import the JSON stores into the normalized SQLite schema.

Tables written:

* ``user_portfolios`` — one row per user (portfolio name)
* ``user_positions``  — one row per position, keyed by
  ``(username, bucket, position_idx)``, with the ISIN preserved
* ``cache`` / ``isin_registry`` / ``users`` / ``sessions`` — as before

``user_assets`` is obsolete: it is never created or written, and an old
table is dropped. A legacy ``positions`` table (auto-increment ``id``)
is converted to ``user_positions``; the dead ``position_countries`` and
``position_sectors`` tables and the leftover ``sqlite_sequence`` are
removed as well.

Idempotent: every store is upserted, and each user/bucket's positions
are replaced wholesale, so running it again is safe.

Usage::

    python scripts/migrate_normalize_db.py --db-file ~/.local/asalloc/asalloc.db
    python scripts/migrate_normalize_db.py --db-file ~/.local/asalloc/asalloc.db --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.records import (  # noqa: E402
    DEFAULT_PORTFOLIO_NAME,
    CacheEntry,
    IsinRecord,
    PositionRecord,
    SessionRecord,
    UserPortfolioRecord,
    UserRecord,
)
from storage.repositories import PositionRepository  # noqa: E402
from storage.sqlite_storage import SqliteStorage  # noqa: E402

POSITION_COLUMNS = (
    "username, bucket, position_idx, name, short_name, isin, "
    "value, shares, broker, dmem, usavn, dmem_other"
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return raw


def _table_names(db_file: Path) -> set[str]:
    with sqlite3.connect(str(db_file)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _ensure_user_positions_has_isin(db_file: Path) -> None:
    """Add columns introduced after the first normalized schema, if needed."""
    with sqlite3.connect(str(db_file)) as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(user_positions)").fetchall()
        }
        if not columns:
            return
        for column, kind in (("short_name", "TEXT"), ("isin", "TEXT")):
            if column not in columns:
                conn.execute(f"ALTER TABLE user_positions ADD COLUMN {column} {kind}")


def _drop_sqlite_sequence(conn: sqlite3.Connection) -> None:
    """Remove the internal ``sqlite_sequence`` table when nothing needs it.

    SQLite refuses ``DROP TABLE sqlite_sequence``; with no AUTOINCREMENT
    table left, deleting its sqlite_master row is the only way to make it
    disappear from the schema.
    """
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone() is None:
        return
    still_needed = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND sql LIKE '%AUTOINCREMENT%'"
    ).fetchone()
    if still_needed is not None:
        return
    conn.execute("PRAGMA writable_schema=ON")
    try:
        conn.execute(
            "DELETE FROM sqlite_master "
            "WHERE type = 'table' AND name = 'sqlite_sequence'"
        )
    finally:
        conn.execute("PRAGMA writable_schema=OFF")


def _migrate_legacy_tables(db_file: Path) -> None:
    """Convert legacy ``positions`` rows and drop the obsolete tables.

    The old schema also had ``position_countries``/``position_sectors``
    (children of ``positions``) and an AUTOINCREMENT ``sqlite_sequence``;
    all of them are dead now. The old ``positions`` table used
    ``position_index`` instead of ``position_idx`` and may miss
    ``short_name``/``isin``; rows are copied into the composite-key table
    and then the legacy tables are removed.
    """
    tables = _table_names(db_file)
    with sqlite3.connect(str(db_file)) as conn:
        if "positions" in tables and "user_positions" in tables:
            cursor = conn.execute("SELECT * FROM positions")
            names = [str(desc[0]) for desc in cursor.description]
            for values in cursor.fetchall():
                row = dict(zip(names, values))
                index = row.get("position_idx", row.get("position_index", 0))
                conn.execute(
                    "INSERT OR IGNORE INTO user_positions "
                    f"({POSITION_COLUMNS}) VALUES ({', '.join('?' * 12)})",
                    (
                        row.get("username", ""),
                        row.get("bucket", ""),
                        index,
                        row.get("name", ""),
                        row.get("short_name", row.get("group")),
                        row.get("isin"),
                        row.get("value"),
                        row.get("shares"),
                        row.get("broker"),
                        row.get("dmem"),
                        row.get("usavn"),
                        row.get("dmem_other"),
                    ),
                )
            conn.execute("DROP TABLE positions")
        for obsolete in ("position_countries", "position_sectors", "user_assets"):
            conn.execute(f"DROP TABLE IF EXISTS {obsolete}")
        _drop_sqlite_sequence(conn)
        conn.commit()


def _tables_to_create() -> list[tuple[str, type]]:
    return [
        ("user_portfolios", UserPortfolioRecord),
        ("user_positions", PositionRecord),
        ("cache", CacheEntry),
        ("isin_registry", IsinRecord),
        ("users", UserRecord),
        ("sessions", SessionRecord),
    ]


def _create_tables(db_file: Path) -> None:
    for table, factory in _tables_to_create():
        backend = SqliteStorage(db_file, table, factory)
        backend.open()
        backend.close()


def _count_assets(raw: dict[str, Any]) -> tuple[int, int]:
    """``(users, positions)`` in an assets.json document."""
    users = 0
    positions = 0
    for user_data in raw.values():
        if not isinstance(user_data, dict):
            continue
        users += 1
        buckets = user_data.get("positions")
        if not isinstance(buckets, dict):
            continue
        for rows in buckets.values():
            if isinstance(rows, list):
                positions += len(rows)
    return users, positions


def migrate(
    db_file: Path, source_dir: Path, *, dry_run: bool = False
) -> list[tuple[str, int]]:
    """Import every JSON store into the database; returns ``(table, rows)``."""
    assets = _read_json(source_dir / "assets.json")
    jcache = _read_json(source_dir / "cache.json")
    isin = _read_json(source_dir / "isin.json")
    users = _read_json(source_dir / "users.json")
    sessions = _read_json(source_dir / ".sessions.json")
    asset_users, asset_positions = _count_assets(assets)

    if dry_run:
        return [
            ("user_portfolios", asset_users),
            ("user_positions", asset_positions),
            ("cache", len(jcache)),
            ("isin_registry", len(isin)),
            ("users", len(users)),
            ("sessions", len(sessions)),
        ]

    db_file.parent.mkdir(parents=True, exist_ok=True)
    _create_tables(db_file)
    _ensure_user_positions_has_isin(db_file)
    _migrate_legacy_tables(db_file)

    imported: list[tuple[str, int]] = []

    # Plain stores: one row per key.
    for filename, table, row_type, document in (
        ("cache.json", "cache", CacheEntry, jcache),
        ("isin.json", "isin_registry", IsinRecord, isin),
        ("users.json", "users", UserRecord, users),
        (".sessions.json", "sessions", SessionRecord, sessions),
    ):
        backend = SqliteStorage(db_file, table, row_type)
        backend.open()
        count = 0
        for key, payload in document.items():
            try:
                row = row_type.from_dict(str(key), payload)
            except (TypeError, ValueError, KeyError) as exc:
                print(f"warning: skipping bad row {key!r} in {filename} ({exc})")
                continue
            backend.put(row)
            count += 1
        backend.persist()
        backend.close()
        imported.append((table, count))

    # Assets: portfolio names + positions (ISIN preserved, uppercase key
    # accepted from the JSON format).
    portfolios = SqliteStorage(
        db_file, "user_portfolios", UserPortfolioRecord
    )
    portfolios.open()
    positions_backend = SqliteStorage(
        db_file, "user_positions", PositionRecord
    )
    position_repo = PositionRepository(positions_backend)

    portfolio_count = 0
    position_count = 0
    for user, user_data in assets.items():
        if not isinstance(user_data, dict):
            print(f"warning: skipping bad user {user!r} in assets.json")
            continue
        name = user_data.get("portfolio_name", DEFAULT_PORTFOLIO_NAME)
        if not isinstance(name, str):
            name = DEFAULT_PORTFOLIO_NAME
        portfolios.put(UserPortfolioRecord(str(user), portfolio_name=name))
        portfolio_count += 1

        buckets = user_data.get("positions")
        if not isinstance(buckets, dict):
            continue
        for bucket, rows in buckets.items():
            if not isinstance(rows, list):
                print(f"warning: skipping bad bucket {bucket!r} for {user!r}")
                continue
            position_repo.batch_upsert_positions(str(user), str(bucket), rows)
            position_count += len(rows)

    portfolios.persist()
    position_repo.persist()
    portfolios.close()
    position_repo.close()

    imported.append(("user_portfolios", portfolio_count))
    imported.append(("user_positions", position_count))
    return imported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import the JSON stores into the normalized SQLite schema."
    )
    parser.add_argument(
        "--db-file",
        type=Path,
        required=True,
        help="The SQLite database file to migrate.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Where the JSON stores live (default: the directory of the db file).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would be imported (touches nothing).",
    )
    args = parser.parse_args(argv)

    source_dir = args.source_dir or args.db_file.parent
    try:
        imported = migrate(args.db_file, source_dir, dry_run=args.dry_run)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for table, count in imported:
        print(f"{table}: {count} row(s)")
    if args.dry_run:
        print("(dry run: nothing was written)")
    else:
        print(f"wrote {args.db_file}")
    print("the JSON files were left untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
