#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time import of the JSON stores into the SQLite database.

The application can keep every store in a SQLite database (one file,
one table per store) instead of loose JSON files. This script moves
the data across:

============  =========================  =====================
JSON file     SQLite table              Row type
============  =========================  =====================
assets.json   user_assets               UserAssets
cache.json    cache                     CacheEntry
isin.json     isin_registry             IsinRecord
users.json    users                     UserRecord
.sessions.json  sessions                SessionRecord
============  =========================  =====================

Usage::

    python scripts/migrate_json_to_sqlite.py --db-file ~/.local/asalloc/asalloc.db
    python scripts/migrate_json_to_sqlite.py --db-file ... --dry-run

The JSON files are **left untouched**: they stay the fallback (and the
rollback) until you delete them yourself. The import is idempotent -
running it again overwrites the rows it finds.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.records import (  # noqa: E402
    CacheEntry,
    IsinRecord,
    SessionRecord,
    UserAssets,
    UserRecord,
)
from storage.sqlite_storage import SqliteStorage  # noqa: E402

#: (JSON file name, table name, row type) per store.
STORES: tuple[tuple[str, str, type], ...] = (
    ("assets.json", "user_assets", UserAssets),
    ("cache.json", "cache", CacheEntry),
    ("isin.json", "isin_registry", IsinRecord),
    ("users.json", "users", UserRecord),
    (".sessions.json", "sessions", SessionRecord),
)


def _read_json(path: Path) -> dict[str, Any]:
    """The JSON object in *path* (empty when the file is missing)."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return raw


def migrate(
    db_file: Path, source_dir: Path, *, dry_run: bool = False
) -> list[tuple[str, int]]:
    """Import every JSON store into the database; returns (table, rows)."""
    imported: list[tuple[str, int]] = []
    for filename, table, row_type in STORES:
        raw = _read_json(source_dir / filename)
        if dry_run:
            imported.append((table, len(raw)))
            continue
        backend = SqliteStorage(db_file, table, row_type)
        backend.open()
        count = 0
        for key, payload in raw.items():
            try:
                row = row_type.from_dict(str(key), payload)
            except (TypeError, ValueError, KeyError) as exc:
                # A bad row is skipped (like the lenient JSON loader),
                # so one broken record cannot abort the whole import.
                print(f"warning: skipping bad row {key!r} in {filename} ({exc})")
                continue
            backend.put(row)
            count += 1
        backend.persist()
        backend.close()
        imported.append((table, count))
    return imported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import the JSON stores into the SQLite database."
    )
    parser.add_argument(
        "--db-file",
        type=Path,
        required=True,
        help="The SQLite database file to write.",
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
        help="Only report what would be imported.",
    )
    args = parser.parse_args(argv)

    source_dir = args.source_dir or args.db_file.parent
    try:
        imported = migrate(args.db_file, source_dir, dry_run=args.dry_run)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
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
