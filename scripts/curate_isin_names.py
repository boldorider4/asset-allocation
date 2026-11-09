#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Curation script for ``isin_registry.name`` backfill from JustETF.

Usage::

    python scripts/curate_isin_names.py --db-file asalloc.db
    python scripts/curate_isin_names.py --db-file asalloc.db --users
    python scripts/curate_isin_names.py --db-file asalloc.db --dry-run
    python scripts/curate_isin_names.py --db-file asalloc.db --run state.json

The script scans every ``isin_registry`` row (optionally intersected with
``user_positions`` with ``--users``), and fills the ``name`` column from
``just_etf_name``.  Names already present are skipped; names fetched from
JustETF are persisted with ``IsinRegistry.register_isin``.

Progress is written to a temp JSON file next to the database, and a
``--run`` option re-reads that file to resume a partial run.  When the
whole registry has been traversed, the file is removed; if names remain
unresolved, it is kept so a later run can continue there.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from position.justetf_position import just_etf_name  # noqa: E402
from storage.repositories import IsinRegistry  # noqa: E402
from storage.wiring import isin_backend  # noqa: E402

FetchName = Callable[[str], str | None]
_RUN_VERSION = 1


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _require_registry_table(db_file: Path) -> None:
    """Fail fast when the name column has not been migrated yet."""
    if not db_file.is_file():
        raise FileNotFoundError(f"database not found: {db_file}")
    conn = sqlite3.connect(_read_only_uri(db_file), uri=True)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'isin_registry'"
        ).fetchone()
        if table is None:
            raise ValueError(
                f"table 'isin_registry' not found in {db_file}"
            )
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(isin_registry)").fetchall()
        }
    finally:
        conn.close()
    if "name" not in columns:
        raise ValueError(
            "table 'isin_registry' is missing the 'name' column; run "
            "scripts/v3.1.0_add_isin_registry_name.py first"
        )


def _position_isins(db_file: Path) -> set[str]:
    """Distinct ISINs stored in ``user_positions`` (from any user)."""
    conn = sqlite3.connect(_read_only_uri(db_file), uri=True)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'user_positions'"
        ).fetchone()
        if table is None:
            return set()
        rows = conn.execute(
            "SELECT DISTINCT isin FROM user_positions "
            "WHERE isin IS NOT NULL AND TRIM(isin) <> ''"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(
            f"cannot read user_positions from {db_file}: {exc}"
        ) from exc
    finally:
        conn.close()
    return {str(isin).strip().upper() for isin, in rows if str(isin).strip()}


def _run_payload(
    db_file: Path,
    *,
    users: bool,
    examined: set[str],
    failed: set[str],
    last_isin: str | None,
) -> dict[str, Any]:
    return {
        "version": _RUN_VERSION,
        "db_file": str(db_file.resolve()),
        "users": bool(users),
        "examined": sorted(examined),
        "failed": sorted(failed),
        "last_isin": last_isin,
    }


def _write_run_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _new_run_file(db_file: Path, *, users: bool) -> Path:
    fd, name = tempfile.mkstemp(
        prefix=f"{db_file.name}.curate-names.",
        suffix=".json",
        dir=db_file.parent,
    )
    os.close(fd)
    path = Path(name)
    try:
        _write_run_file(
            path,
            _run_payload(
                db_file,
                users=users,
                examined=set(),
                failed=set(),
                last_isin=None,
            ),
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _load_run_file(
    path: Path,
    db_file: Path,
    *,
    users: bool,
) -> tuple[set[str], set[str], str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid run file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"invalid run file {path}: expected a JSON object")
    if raw.get("version") != _RUN_VERSION:
        raise ValueError(
            f"invalid run file {path}: unsupported version "
            f"{raw.get('version')!r}"
        )
    stored_db = raw.get("db_file")
    if not isinstance(stored_db, str) or not stored_db.strip():
        raise ValueError(f"invalid run file {path}: missing db_file")
    if Path(stored_db).expanduser().resolve() != db_file.resolve():
        raise ValueError(
            f"run file {path} belongs to {stored_db}, not {db_file}"
        )
    if raw.get("users") != bool(users):
        raise ValueError(
            f"run file {path} belongs to users={raw.get('users')!r}, "
            f"not users={bool(users)!r}"
        )
    examined_values = raw.get("examined", [])
    failed_values = raw.get("failed", [])
    if not isinstance(examined_values, list) or not isinstance(failed_values, list):
        raise ValueError(
            f"invalid run file {path}: examined and failed must both "
            "be lists of ISINs"
        )
    examined = {
        str(isin).strip().upper()
        for isin in examined_values
        if str(isin).strip()
    }
    failed = {
        str(isin).strip().upper()
        for isin in failed_values
        if str(isin).strip()
    }
    last_isin = raw.get("last_isin")
    if last_isin is not None and not isinstance(last_isin, str):
        raise ValueError(
            f"invalid run file {path}: last_isin must be a string or null"
        )
    return examined, failed, last_isin


def _prepare_run_file(
    db_file: Path,
    candidates: set[str],
    *,
    users: bool,
    run_file: str | Path | None,
) -> tuple[Path, set[str], set[str], str | None]:
    """Create or load a run file; returns ``(path, examined, failed)``."""
    if run_file is None:
        return (
            _new_run_file(db_file, users=users),
            set(),
            set(),
            None,
        )
    path = Path(run_file).expanduser()
    if not path.is_file():
        raise ValueError(f"run file not found: {path}")
    examined, failed, last_isin = _load_run_file(
        path,
        db_file,
        users=users,
    )
    candidates_set = set(candidates)
    examined &= candidates_set
    failed &= candidates_set
    if last_isin not in candidates_set:
        last_isin = None
    return path, examined, failed, last_isin


def curate_isin_names(
    db_file: str | Path,
    *,
    fetch_name: FetchName = just_etf_name,
    dry_run: bool = False,
    users: bool = False,
    run_file: str | Path | None = None,
    emit: Callable[[str], None] = print,
) -> tuple[int, int, int, int]:
    """Backfill names in ``isin_registry`` from ``fetch_name``.

    Returns ``(updated, unchanged, unresolved, errors)``.  When one or
    more ``isin_registry`` rows are unresolved, a run file stays behind
    so a later ``--run <file>`` can continue where this run left off.
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
            positions = _position_isins(database)
            candidates &= positions
        if not dry_run:
            path, examined, failed, last_isin = _prepare_run_file(
                database,
                candidates,
                users=users,
                run_file=run_file,
            )
        pending = (
            isin
            for isin in sorted(candidates)
            if isin not in examined
        )
        try:
            for isin in pending:
                try:
                    before = registry.get_name_for_isin(isin)
                    if before:
                        unchanged += 1
                        examined.add(isin)
                        failed.discard(isin)
                        last_isin = isin
                        emit(f"{isin}: unchanged ({before})")
                        continue
                    name = fetch_name(isin)
                    if not name or not str(name).strip():
                        unresolved += 1
                        failed.add(isin)
                        examined.discard(isin)
                        last_isin = isin
                        emit(f"{isin}: unresolved (no name)")
                        continue
                    name = str(name).strip()
                    if dry_run:
                        updated += 1
                        emit(f"{isin}: would update ({name})")
                        continue
                    registry.register_isin(isin, name=name)
                    registry.persist()
                    updated += 1
                    examined.add(isin)
                    failed.discard(isin)
                    last_isin = isin
                    emit(f"{isin}: -> {name}")
                except Exception as exc:  # noqa: BLE001 - keep scanning
                    errors += 1
                    failed.add(isin)
                    examined.discard(isin)
                    last_isin = isin
                    emit(f"{isin}: error: {exc}")
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
            "Backfill names in isin_registry from JustETF product titles."
        )
    )
    parser.add_argument(
        "--db-file",
        required=True,
        type=Path,
        help="SQLite database file.",
    )
    parser.add_argument(
        "--users",
        action="store_true",
        help=(
            "Only curate ISINs that also appear in user_positions."
        ),
    )
    parser.add_argument(
        "--run",
        type=Path,
        default=None,
        help=(
            "Resume a previous run from this state file."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report names without writing them to the registry.",
    )
    args = parser.parse_args(argv)

    try:
        updated, unchanged, unresolved, errors = curate_isin_names(
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
