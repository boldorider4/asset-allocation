#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backfill registry portfolio buckets from JustETF Investment focus.

Usage::

    python scripts/rescan_isin_asset_classes.py --db-file asalloc.db
    python scripts/rescan_isin_asset_classes.py --db-file asalloc.db --dry-run
    python scripts/rescan_isin_asset_classes.py --db-file asalloc.db --users
    python scripts/rescan_isin_asset_classes.py --db-file asalloc.db --run \
state.json

Every ISIN in ``isin_registry`` is fetched from JustETF. The profile's
Investment focus is mapped to a portfolio bucket and written only when it
differs. Unknown focus, fetch failures, and profiles without the row leave
the existing bucket untouched. The issuer, product ref, and visits counter
are preserved.

With ``--users`` only ISINs that also appear in ``user_positions`` are
scanned, so maintenance work can be scoped to the rows portfolios use.

Progress is written to a temp JSON file next to the database, and a
``--run`` option re-reads that file to resume a partial run. When the
whole registry has been traversed, the file is removed; if any ISIN is
still unresolved, it is kept so a later run can continue there.
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
from scripts.curate_isin_names import (  # noqa: E402,F401
    _position_isins,
    _prepare_run_file,
    _run_payload,
    _write_run_file,
)
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
    users: bool = False,
    run_file: str | Path | None = None,
    emit: Callable[[str], None] = print,
) -> tuple[int, int, int, int]:
    """Refresh every registry bucket from JustETF.

    Returns ``(updated, unchanged, unresolved, errors)``. ``unresolved``
    covers profiles whose focus is missing or outside the supported
    mapping; those rows keep their current bucket. When one or more rows
    are unresolved, a run file stays behind so a later ``--run <file>``
    can continue where this run left off.
    """
    database = Path(db_file).expanduser().resolve()
    _require_registry_table(database)
    registry = IsinRegistry(isin_backend(database))
    registry.open()
    updated = unchanged = unresolved = errors = 0
    path: Path | None = None
    examined: set[str] = set()
    failed: set[str] = set()
    last_isin: str | None = None
    try:
        candidates = {
            str(isin).strip().upper()
            for isin in registry.snapshot()
            if str(isin).strip()
        }
        if users:
            candidates &= _position_isins(database)
        if not dry_run:
            path, examined, failed, last_isin = _prepare_run_file(
                database,
                candidates,
                users=users,
                run_file=run_file,
            )
        pending = (
            isin for isin in sorted(candidates) if isin not in examined
        )
        try:
            for isin in pending:
                try:
                    before = registry.get_bucket_for_isin(isin)
                    after = fetch_asset_class(isin)
                    if after is None:
                        emit(
                            f"{isin}: unresolved (no asset class); "
                            f"kept {before or 'missing'}"
                        )
                        unresolved += 1
                        failed.add(isin)
                        examined.discard(isin)
                        last_isin = isin
                        continue
                    if before == after:
                        emit(f"{isin}: unchanged ({after})")
                        unchanged += 1
                        examined.add(isin)
                        failed.discard(isin)
                        last_isin = isin
                        continue
                    if dry_run:
                        emit(
                            f"{isin}: would update "
                            f"{before or 'missing'} -> {after}"
                        )
                        updated += 1
                        continue
                    registry.set_bucket_for_isin(isin, after)
                    registry.persist()
                    emit(f"{isin}: {before or 'missing'} -> {after}")
                    updated += 1
                    examined.add(isin)
                    failed.discard(isin)
                    last_isin = isin
                except Exception as exc:  # noqa: BLE001 - keep scanning
                    emit(f"{isin}: error: {exc}")
                    errors += 1
                    failed.add(isin)
                    examined.discard(isin)
                    last_isin = isin
                finally:
                    if path is not None:
                        _write_run_file(
                            path,
                            _run_payload(
                                database,
                                users=users,
                                examined=examined,
                                failed=failed,
                                last_isin=last_isin,
                            ),
                        )
        finally:
            if path is not None and not failed:
                path.unlink(missing_ok=True)
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
    parser.add_argument(
        "--users",
        action="store_true",
        help="Only rescan ISINs that also appear in user_positions.",
    )
    parser.add_argument(
        "--run",
        type=Path,
        default=None,
        help="Resume a previous run from this state file.",
    )
    args = parser.parse_args(argv)

    try:
        updated, unchanged, unresolved, errors = rescan_asset_classes(
            args.db_file,
            dry_run=args.dry_run,
            users=args.users,
            run_file=args.run,
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
