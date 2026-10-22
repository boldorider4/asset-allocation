#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Normalize existing SQLite database from blob storage to normalized schema.

Run once after upgrading to the new schema version.
Idempotent: safe to run multiple times.

Usage::
    python scripts/migrate_normalize_db.py --db-file ~/.local/asalloc/asalloc.db
    python scripts/migrate_normalize_db.py --db-file ~/.local/asalloc/asalloc.db --dry-run
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
    CountryRecord,
    IsinRecord,
    PositionRecord,
    SectorRecord,
    SessionRecord,
    UserAssets,
    UserPortfolioRecord,
    UserRecord,
)
from storage.sqlite_storage import SqliteStorage  # noqa: E402


def _fallback_path(candidate: Any, default: Path) -> Path:
    """The path to use when the caller named none."""
    if candidate is None:
        return default
    return Path(candidate)


def _extract_positions(raw: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Extract positions from raw user assets data.

    Returns a dict of {user: {bucket: [positions]}} extracted from the raw data.
    Handles the format where users are top-level keys with portfolio_name and positions.
    """
    positions: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for user, user_data in raw.items():
        if isinstance(user_data, dict) and "positions" in user_data:
            user_positions: dict[str, list[dict[str, Any]]] = {}
            for bucket, pos_list in user_data["positions"].items():
                # Normalize each position to have explicit columns
                normalized = []
                for pos in pos_list:
                    normalized_pos = {
                        "username": pos.get("username", user),
                        "name": pos.get("name", ""),
                        "short_name": pos.get("short_name"),
                        "isin": pos.get("isin"),
                        "value": pos.get("value"),
                        "shares": pos.get("shares"),
                        "broker": pos.get("broker"),
                        "dmem": pos.get("dmem"),
                        "dmem_other": pos.get("dmem_other"),
                        "usavn": pos.get("usavn"),
                    }
                    normalized.append(normalized_pos)
                user_positions[bucket] = normalized
            positions[user] = user_positions
    return positions


def _parse_user_assets(key: str, raw: dict[str, Any]) -> UserAssets:
    """Parse user assets, handling both old format (with positions) and new format (without)."""
    # Check if this is the old format with positions
    if "positions" in raw:
        # Create a copy without positions for the new format
        payload = {k: v for k, v in raw.items() if k != "positions"}
        return UserAssets.from_dict(key, payload)
    return UserAssets.from_dict(key, raw)


def migrate(db_file: Path, source_dir: Path, *, dry_run: bool = False) -> list[tuple[str, int]]:
    """Import every JSON store into the database; returns (table, rows)."""
    imported: list[tuple[str, int]] = []

    # 1. Create all tables first
    if not dry_run:
        tables_to_create = [
            ("user_portfolios", UserPortfolioRecord),
            ("positions", PositionRecord),
            ("cache", CacheEntry),
            ("isin_registry", IsinRecord),
            ("users", UserRecord),
            ("sessions", SessionRecord),
        ]
        for table, factory in tables_to_create:
            backend = SqliteStorage(db_file, table, factory)
            backend.open()
            backend.close()

    for filename, table, row_type in [
        ("assets.json", "user_assets", UserAssets),
        ("cache.json", "cache", CacheEntry),
        ("isin.json", "isin_registry", IsinRecord),
        ("users.json", "users", UserRecord),
        (".sessions.json", "sessions", SessionRecord),
    ]:
        raw = _read_json(source_dir / filename)
        if dry_run:
            imported.append((table, len(raw)))
            continue

        # For the old tables, we just import as-is for backwards compatibility
        # except user_assets which needs special handling for positions
        if table == "user_assets":
            # Extract positions first before stripping them
            positions_data = _extract_positions(raw)
            
            # Create UserAssets without positions
            payload = {k: v for k, v in raw.items() if k != "positions"}
            backend = SqliteStorage(db_file, table, row_type)
            backend.open()
            count = 0
            for key, raw_payload in raw.items():
                try:
                    row = _parse_user_assets(str(key), raw_payload)
                except (TypeError, ValueError, KeyError) as exc:
                    print(f"warning: skipping bad row {key!r} in {filename} ({exc})")
                    continue
                backend.put(row)
                count += 1
            backend.persist()
            backend.close()
            imported.append((table, count))
            
            # Save positions data for step 2 - attach to db_file for later access
            # We'll re-read from source_dir in step 2
            
        elif table in ("cache", "isin_registry", "users", "sessions"):
            backend = SqliteStorage(db_file, table, row_type)
            backend.open()
            count = 0
            for key, payload in raw.items():
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

    # 2. Normalize user_assets -> user_portfolios + positions
    # Positions are stored with explicit columns; countries/sectors are NOT stored
    # in position_countries/position_sectors (they're cached per ISIN in business logic)
    if not dry_run:
        # Re-read the assets JSON to get positions
        raw = _read_json(source_dir / "assets.json")
        positions_data = _extract_positions(raw)
        
        backend_assets = SqliteStorage(db_file, "user_assets", UserAssets)
        backend_assets.open()
        backend_portfolios = SqliteStorage(db_file, "user_portfolios", UserPortfolioRecord)
        backend_portfolios.open()
        backend_positions = SqliteStorage(db_file, "positions", PositionRecord)
        backend_positions.open()

        for user, payload in backend_assets.snapshot().items():
            # payload is already a dict (parsed by snapshot()) - without positions now
            portfolio_name = payload.get("portfolio_name", "Portfolio")
            backend_portfolios.put(UserPortfolioRecord(user, {"portfolio_name": portfolio_name}))

            # Migrate positions with explicit columns only
            # Get positions from the extracted data (from step 1)
            user_positions = positions_data.get(user, {})
            for bucket, pos_list in user_positions.items():
                for idx, pos in enumerate(pos_list):
                    _migrate_position_simple(
                        backend_positions,
                        username=user,
                        bucket=bucket,
                        index=idx,
                        pos=pos,
                    )

        backend_portfolios.persist()
        backend_positions.persist()
        backend_assets.close()
        backend_portfolios.close()
        backend_positions.close()

    return imported


def _migrate_position_simple(
    positions_backend: SqliteStorage,
    username: str,
    bucket: str,
    index: int,
    pos: dict[str, Any],
) -> None:
    """Migrate a single position with explicit columns only.

    Countries and sectors are NOT stored in the DB - they're cached per ISIN
    and calculated in the business logic update, not in the DB.
    """
    # Insert position with explicit columns only
    # NOTE: We only store the fields that exist in the new normalized schema.
    # Countries/sectors that were previously in JSON blobs are NOT migrated to
    # position_countries/position_sectors tables - they need to be handled
    # by the business logic update process.
    record = PositionRecord(
        key="0",
        username=pos.get("username", username),
        bucket=bucket,
        position_index=index,
        name=pos.get("name", ""),
        short_name=pos.get("short_name"),
        isin=pos.get("isin"),
        value=pos.get("value"),
        shares=pos.get("shares"),
        broker=pos.get("broker"),
        dmem=pos.get("dmem"),
        dmem_other=pos.get("dmem_other"),
        usavn=pos.get("usavn"),
    )
    positions_backend.put(record)
    positions_backend.persist()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize the SQLite database from blob storage to normalized schema."
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
