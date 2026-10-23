# SPDX-License-Identifier: AGPL-3.0-or-later
"""Composition point: the SQLite stores.

Everything above this line (repositories, the runtime context, the web
backend, the scrapers) talks to the *repository* and never to a concrete
backend. This module is the single place where the concrete SQLite
backend is built.

The SQLite file is read from ``config.ini`` by the app layer (see
``cli.context``) and passed in. This module reads no files and knows no
settings.

Most stores map onto explicit-column SQLite tables: ``user_portfolios``
(one row per user), ``user_positions`` (one row per position with a
composite key), ``users`` (one account per row), ``cache`` (price and
split weights per ISIN) and ``sessions`` (one browser session per id).
The generic layout ``(key TEXT PRIMARY KEY, payload TEXT NOT NULL)`` is
kept for custom/test tables only.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from storage.records import (
    CacheEntry,
    IsinRecord,
    PositionRecord,
    SessionRecord,
    UserPortfolioRecord,
    UserRecord,
)
from storage.sqlite_storage import SqliteStorage

if TYPE_CHECKING:
    from storage.storage import Storage

__all__ = [
    "SQLITE_TABLE_USER_PORTFOLIOS",
    "SQLITE_TABLE_POSITIONS",
    "SQLITE_TABLE_CACHE",
    "SQLITE_TABLE_ISIN",
    "SQLITE_TABLE_USERS",
    "SQLITE_TABLE_SESSIONS",
    "cache_backend",
    "isin_backend",
    "positions_backend",
    "sessions_backend",
    "user_portfolios_backend",
    "users_backend",
]

#: Tables of the SQLite database (one per store).
SQLITE_TABLE_USER_PORTFOLIOS = "user_portfolios"
SQLITE_TABLE_POSITIONS = "user_positions"
SQLITE_TABLE_CACHE = "cache"
SQLITE_TABLE_ISIN = "isin_registry"
SQLITE_TABLE_USERS = "users"
SQLITE_TABLE_SESSIONS = "sessions"

#: One backend per process: a DB connection is expensive to open and
#: the state lives in the database, so the same (path, table) must
#: always land on the same connection.
_DB_BACKENDS: dict[tuple[str, str, str], "Storage"] = {}


def _sqlite_backend(
    db_file: str | Path, table: str, factory: type
) -> "Storage":
    """The SQLite backend for ``(db_file, table)``; one connection per process."""
    key = (str(db_file), table, getattr(factory, "__name__", str(factory)))
    backend = _DB_BACKENDS.get(key)
    if backend is None:
        backend = SqliteStorage(db_file, table, factory)  # type: ignore[arg-type]
        _DB_BACKENDS[key] = backend
    return backend


def user_portfolios_backend(
    db_file: str | Path,
) -> "Storage[UserPortfolioRecord]":
    """Backend for the user portfolio names (one row per user)."""
    return _sqlite_backend(
        db_file, SQLITE_TABLE_USER_PORTFOLIOS, UserPortfolioRecord
    )


def positions_backend(
    db_file: str | Path,
) -> "Storage[PositionRecord]":
    """Backend for the normalized positions table."""
    return _sqlite_backend(db_file, SQLITE_TABLE_POSITIONS, PositionRecord)


def cache_backend(
    db_file: str | Path,
) -> "Storage[CacheEntry]":
    """Backend for the per-ISIN cache (price, countries, sectors)."""
    return _sqlite_backend(db_file, SQLITE_TABLE_CACHE, CacheEntry)


def isin_backend(
    db_file: str | Path,
) -> "Storage[IsinRecord]":
    """Backend for the ISIN registry (issuer, bucket, vendor ref)."""
    return _sqlite_backend(db_file, SQLITE_TABLE_ISIN, IsinRecord)


def sessions_backend(
    db_file: str | Path,
) -> "Storage[SessionRecord]":
    """Backend for the browser sessions (one row per session id)."""
    return _sqlite_backend(db_file, SQLITE_TABLE_SESSIONS, SessionRecord)


def users_backend(
    db_file: str | Path,
) -> "Storage[UserRecord]":
    """Backend for the user accounts (one row per user name)."""
    return _sqlite_backend(db_file, SQLITE_TABLE_USERS, UserRecord)
