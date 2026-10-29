# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refresh cached country and sector splits for ISINs in the SQLite registry."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from cli.common import DEFAULT_USER_NAME
from cli.context import AppConfig, RuntimeContext, ServerConfig
from position.factory import factory
from utils import save_position_in_cache

logger = logging.getLogger(__name__)

_RUN_VERSION = 1

__all__ = ["CurateError", "run_curate"]


class CurateError(RuntimeError):
    """A curation run cannot start or resume."""


def _normalize_user(user: str | None) -> str | None:
    if user is None:
        return None
    name = str(user).strip()
    if not name:
        raise CurateError("--user must not be empty")
    return name


def _build_context(db_file: Path, user: str | None) -> RuntimeContext:
    config = AppConfig(
        fetch_prices=False,
        fetch_geosplit=True,
        fetch_sectorsplit=True,
        fetch_oskar=False,
        fetch_scalable=False,
        fetch_traderepublic=False,
        position_source="justetf",
        user=user or DEFAULT_USER_NAME,
        server=ServerConfig(
            port=0,
            address="localhost",
            directory=db_file.parent,
            db_file=db_file,
        ),
    )
    return RuntimeContext(config=config)


def _selected_isins(ctx: RuntimeContext, user: str | None) -> list[str]:
    registry = ctx.isin_registry.snapshot()
    all_isins = sorted(
        {
            str(isin).strip()
            for isin in registry
            if str(isin).strip()
        }
    )
    if user is None:
        return all_isins
    if not ctx.asset_repo.exists():
        raise CurateError(
            f"unknown user {user!r}: no portfolio stored for it"
        )
    wanted: set[str] = set()
    for positions in ctx.asset_repo.snapshot().values():
        for position in positions:
            raw = position.get("ISIN") or position.get("isin")
            if raw is None:
                continue
            isin = str(raw).strip().upper()
            if isin:
                wanted.add(isin)
    return [isin for isin in all_isins if isin.upper() in wanted]


def _run_payload(
    db_file: Path,
    user: str | None,
    examined: set[str],
    failed: set[str],
    last_isin: str | None,
) -> dict[str, Any]:
    return {
        "version": _RUN_VERSION,
        "db_file": str(db_file.resolve()),
        "user": user,
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


def _new_run_file(
    db_file: Path,
    user: str | None,
) -> Path:
    fd, name = tempfile.mkstemp(
        prefix=f"{db_file.name}.curate.",
        suffix=".json",
        dir=db_file.parent,
    )
    os.close(fd)
    path = Path(name)
    try:
        _write_run_file(path, _run_payload(db_file, user, set(), set(), None))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _string_set(raw: Any, field: str, path: Path) -> set[str]:
    if not isinstance(raw, list) or any(
        not isinstance(value, str) for value in raw
    ):
        raise CurateError(
            f"invalid run file {path}: {field!r} must be a list of ISINs"
        )
    return set(raw)


def _load_run_file(
    path: Path,
    db_file: Path,
    user: str | None,
) -> tuple[set[str], set[str], str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CurateError(f"cannot read run file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CurateError(f"invalid run file {path}: expected a JSON object")
    if raw.get("version") != _RUN_VERSION:
        raise CurateError(
            f"invalid run file {path}: unsupported version {raw.get('version')!r}"
        )
    stored_db = raw.get("db_file")
    if not isinstance(stored_db, str) or not stored_db.strip():
        raise CurateError(f"invalid run file {path}: missing db_file")
    if Path(stored_db).expanduser().resolve() != db_file.resolve():
        raise CurateError(
            f"run file {path} belongs to {stored_db}, not {db_file}"
        )
    if raw.get("user") != user:
        raise CurateError(
            f"run file {path} belongs to user {raw.get('user')!r}, not {user!r}"
        )
    examined = _string_set(raw.get("examined", []), "examined", path)
    failed = _string_set(raw.get("failed", []), "failed", path)
    last_isin = raw.get("last_isin")
    if last_isin is not None and not isinstance(last_isin, str):
        raise CurateError(
            f"invalid run file {path}: 'last_isin' must be a string or null"
        )
    return examined, failed, last_isin


def _prepare_run_file(
    db_file: Path,
    selected: list[str],
    user: str | None,
    run_file: str | Path | None,
) -> tuple[Path, set[str], set[str], str | None]:
    if run_file is None:
        return _new_run_file(db_file, user), set(), set(), None
    path = Path(run_file).expanduser()
    if not path.is_file():
        raise CurateError(f"run file not found: {path}")
    examined, failed, last_isin = _load_run_file(path, db_file, user)
    selected_set = set(selected)
    examined &= selected_set
    failed &= selected_set
    if last_isin not in selected_set:
        last_isin = None
    return path, examined, failed, last_isin


def run_curate(
    db_file: str | Path,
    *,
    user: str | None = None,
    run_file: str | Path | None = None,
) -> int:
    """Curate registry ISINs.

    Return 0 on success, 1 on per-ISIN failures, 130 on interrupt.
    """
    database = Path(db_file).expanduser()
    if not database.is_file():
        raise CurateError(f"database not found: {database}")
    user = _normalize_user(user)
    ctx = _build_context(database, user)
    selected = _selected_isins(ctx, user)
    path, examined, failed, last_isin = _prepare_run_file(
        database, selected, user, run_file
    )
    pending = [isin for isin in selected if isin not in examined]
    try:
        for isin in pending:
            try:
                position = factory(isin, ctx=ctx, write_cache=False)
                countries = position.countries() or []
                sectors = position.sectors() or []
                if countries or sectors:
                    save_position_in_cache(
                        ctx,
                        isin,
                        countries=countries if countries else None,
                        sectors=sectors if sectors else None,
                        update_countries=bool(countries),
                        update_sectors=bool(sectors),
                    )
            except Exception as exc:
                logger.warning("curate: %s failed: %s", isin, exc)
                failed.add(isin)
                examined.discard(isin)
                last_isin = isin
                _write_run_file(
                    path,
                    _run_payload(
                        database, user, examined, failed, last_isin
                    ),
                )
                continue
            ctx.cache_repo.persist()
            ctx.flush_isin_registry()
            examined.add(isin)
            failed.discard(isin)
            last_isin = isin
            _write_run_file(
                path,
                _run_payload(database, user, examined, failed, last_isin),
            )
        if failed:
            _write_run_file(
                path,
                _run_payload(database, user, examined, failed, last_isin),
            )
            print(
                f"curation incomplete: {len(failed)} ISIN(s) failed; "
                f"resume with --run {path.resolve()}",
                file=sys.stderr,
            )
            return 1
        path.unlink(missing_ok=True)
        print(f"curated {len(selected)} ISIN(s) from {database}")
        return 0
    except KeyboardInterrupt:
        _write_run_file(
            path,
            _run_payload(database, user, examined, failed, last_isin),
        )
        print(
            f"curation interrupted; resume with --run {path.resolve()}",
            file=sys.stderr,
        )
        return 130
