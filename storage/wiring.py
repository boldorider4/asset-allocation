# SPDX-License-Identifier: AGPL-3.0-or-later
"""Composition point: which backend stores the data.

Everything above this line (repositories, the runtime context, the web
backend, the scrapers) talks to the *repository* and never to a concrete
backend. This module is the single place where the concrete backend is
built, so moving a store to SQLite is one line here.

Configuration lives with the application: the backend name and the SQLite
file are read from ``config.ini`` by the app layer (see ``cli.context``)
and passed in. This module reads no files and knows no settings.

Most stores keep their row type and map onto the SQLite layout
``(key TEXT PRIMARY KEY, payload TEXT NOT NULL)`` with ``key`` = the
row key and ``payload`` = the row's JSON form. Portfolios (one row per
user) and positions (normalized, one row per position with a composite
key) have dedicated tables.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from storage.json_storage import JsonStorage
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
    "BACKEND_JSON",
    "BACKEND_SQLITE",
    "SUPPORTED_BACKENDS",
    "SQLITE_TABLE_USER_PORTFOLIOS",
    "SQLITE_TABLE_POSITIONS",
    "SQLITE_TABLE_CACHE",
    "SQLITE_TABLE_ISIN",
    "SQLITE_TABLE_USERS",
    "SQLITE_TABLE_SESSIONS",
    "cache_backend",
    "isin_backend",
    "normalize_backend",
    "positions_backend",
    "sessions_backend",
    "user_portfolios_backend",
    "users_backend",
]

logger = logging.getLogger(__name__)

#: Backend names the application may configure.
BACKEND_JSON = "json"
BACKEND_SQLITE = "sqlite"
SUPPORTED_BACKENDS: tuple[str, ...] = (
    BACKEND_JSON,
    BACKEND_SQLITE,
)

#: Tables of the SQLite database (one per store).
SQLITE_TABLE_USER_PORTFOLIOS = "user_portfolios"
SQLITE_TABLE_POSITIONS = "user_positions"
SQLITE_TABLE_CACHE = "cache"
SQLITE_TABLE_ISIN = "isin_registry"
SQLITE_TABLE_USERS = "users"
SQLITE_TABLE_SESSIONS = "sessions"

#: One backend per process: a DB connection is expensive to open and
#: the state lives in the database, so the same (path, table) must
#: always land on the same connection. The JSON backend is deliberately
#: NOT cached: it reloads its file per use, and callers rely on that.
_DB_BACKENDS: dict[tuple[str, str, str], "Storage"] = {}


def normalize_backend(name: str | None) -> str:
    """Canonical backend name; unknown names fall back to ``json``.

    Pure: it reads no configuration, the caller passes the configured
    value in.
    """
    candidate = (name or "").strip().lower()
    if candidate in SUPPORTED_BACKENDS:
        return candidate
    logger.warning(
        "unknown assets backend %r (supported: %s); using %r",
        candidate,
        ", ".join(SUPPORTED_BACKENDS),
        BACKEND_JSON,
    )
    return BACKEND_JSON


def _sqlite_backend(
    path: str | Path, table: str, factory: type
) -> "Storage":
    """The SQLite backend for ``(path, table)``; one connection per process."""
    key = (str(path), table, getattr(factory, "__name__", str(factory)))
    backend = _DB_BACKENDS.get(key)
    if backend is None:
        backend = SqliteStorage(path, table, factory)  # type: ignore[arg-type]
        _DB_BACKENDS[key] = backend
    return backend


def user_portfolios_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    db_file: str | Path | None = None,
) -> "Storage[UserPortfolioRecord]":
    """Backend for the user portfolio names (one row per user)."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(
            db_file or path, SQLITE_TABLE_USER_PORTFOLIOS, UserPortfolioRecord
        )
    return JsonStorage(path, UserPortfolioRecord, strict=True)


def positions_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    db_file: str | Path | None = None,
) -> "Storage[PositionRecord]":
    """Backend for the normalized positions table."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(
            db_file or path, SQLITE_TABLE_POSITIONS, PositionRecord
        )
    return JsonStorage(path, PositionRecord)


def cache_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    db_file: str | Path | None = None,
) -> "Storage[CacheEntry]":
    """Backend for the per-ISIN cache (price, countries, sectors)."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(db_file or path, SQLITE_TABLE_CACHE, CacheEntry)
    return JsonStorage(path, CacheEntry)


def isin_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    db_file: str | Path | None = None,
) -> "Storage[IsinRecord]":
    """Backend for the ISIN registry (issuer, bucket, vendor ref)."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(db_file or path, SQLITE_TABLE_ISIN, IsinRecord)
    return JsonStorage(path, IsinRecord)


def sessions_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    db_file: str | Path | None = None,
) -> "Storage[SessionRecord]":
    """Backend for the browser sessions (one row per session id)."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(
            db_file or path, SQLITE_TABLE_SESSIONS, SessionRecord
        )
    return JsonStorage(path, SessionRecord, strict=True)


def users_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    db_file: str | Path | None = None,
) -> "Storage[UserRecord]":
    """Backend for the user accounts (one row per user name).

    Same backend selector as the assets store, so moving the accounts
    to SQLite is one config line. The row key is the user name (the
    primary key of the future table), the payload is the password.

    The JSON backend is strict: a malformed account raises instead of
    being skipped, so a broken users file fails loudly instead of
    quietly locking everybody out.
    """
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(db_file or path, SQLITE_TABLE_USERS, UserRecord)
    return JsonStorage(path, UserRecord, strict=True)