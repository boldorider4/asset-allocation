#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backfill registry portfolio buckets from JustETF Investment focus.

Usage::

    python scripts/rescan_isin_asset_classes.py --db-file asalloc.db
    python scripts/rescan_isin_asset_classes.py --db-file asalloc.db --dry-run

Every ISIN in ``isin_registry`` is fetched from JustETF. The profile's
Investment focus is mapped to a portfolio bucket and written only when it
differs. Unknown focus, fetch failures, and profiles without the row leave
the existing bucket untouched. The issuer, product ref, and visits counter
are preserved.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from position.justetf_position import just_etf_asset_class  # noqa: E402
from storage.repositories import IsinRegistry  # noqa: E402
from storage.wiring import isin_backend  # noqa: E402

FetchAssetClass = Callable[[str], str | None]


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _require_registry_table(db_file: Path) -> None:
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
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(isin_registry)").fetchall()
        }
    finally:
        conn.close()
    if "visits" not in columns:
        raise ValueError(
            "table 'isin_registry' is missing the 'visits' column; run "
            "scripts/v3.0.0_add_isin_registry_visits.py first"
        )


def rescan_asset_classes(
    db_file: Path,
    *,
    fetch_asset_class: FetchAssetClass = just_etf_asset_class,
    dry_run: bool = False,
    emit: Callable[[str], None] = print,
) -> tuple[int, int, int, int]:
    """Refresh every registry bucket from JustETF.

    Returns ``(updated, unchanged, unresolved, errors)``. ``unresolved``
    covers profiles whose focus is missing or outside the supported
    mapping; those rows keep their current bucket.
    """
    _require_registry_table(db_file)
    registry = IsinRegistry(isin_backend(db_file))
    registry.open()
    updated = unchanged = unresolved = errors = 0
    try:
        for isin in sorted(registry.snapshot()):
            try:
                before = registry.get_bucket_for_isin(isin)
                after = fetch_asset_class(isin)
                if after is None:
                    emit(
                        f"{isin}: unresolved (no asset class); "
                        f"kept {before or 'missing'}"
                    )
                    unresolved += 1
                    continue
                if before == after:
                    emit(f"{isin}: unchanged ({after})")
                    unchanged += 1
                    continue
                if dry_run:
                    emit(
                        f"{isin}: would update {before or 'missing'} -> {after}"
                    )
                    updated += 1
                    continue
                registry.set_bucket_for_isin(isin, after)
                registry.persist()
                emit(f"{isin}: {before or 'missing'} -> {after}")
                updated += 1
            except Exception as exc:  # noqa: BLE001 - keep scanning
                emit(f"{isin}: error: {exc}")
                errors += 1
    finally:
        registry.close()
    return updated, unchanged, unresolved, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill portfolio buckets in isin_registry from JustETF "
            "Investment focus."
        )
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
        help="Report the changes without persisting them.",
    )
    args = parser.parse_args(argv)

    try:
        updated, unchanged, unresolved, errors = rescan_asset_classes(
            args.db_file, dry_run=args.dry_run
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"updated {updated}, unchanged {unchanged}, "
        f"unresolved {unresolved}, errors {errors}",
        file=sys.stderr,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
