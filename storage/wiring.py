# SPDX-License-Identifier: AGPL-3.0-or-later
"""Composition point: which backend stores the data.

Everything above this line (repositories, the runtime context, the web
backend, the scrapers) talks to the *repository* and never to a concrete
backend. This module is the single place where the concrete backend is
built, so moving a store to SQLite is one line here.

Configuration lives with the application: the backend name, the SQLite
file and the Postgres DSN are read from ``config.ini`` by the app layer
(see ``cli.context``) and passed in. This module reads no files and
knows no settings.

The row type stays the one the store has always used (one row per user
for the assets, one per ISIN for the cache, ...), which maps onto the
SQLite table layout ``(key TEXT PRIMARY KEY, payload TEXT NOT NULL)``
with ``key`` = the row key and ``payload`` = the row's JSON form.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from storage.json_storage import JsonStorage
from storage.postgres_storage import PostgresStorage
from storage.records import (
    CacheEntry,
    IsinRecord,
    SessionRecord,
    UserAssets,
    UserRecord,
)
from storage.sqlite_storage import SqliteStorage

if TYPE_CHECKING:
    from storage.storage import Storage

__all__ = [
    "BACKEND_JSON",
    "BACKEND_POSTGRES",
    "BACKEND_SQLITE",
    "DEFAULT_POSTGRES_DSN",
    "POSTGRES_TABLE",
    "POSTGRES_USERS_TABLE",
    "SUPPORTED_BACKENDS",
    "assets_backend",
    "cache_backend",
    "isin_backend",
    "normalize_backend",
    "sessions_backend",
    "users_backend",
]

logger = logging.getLogger(__name__)

#: Backend names the application may configure.
BACKEND_JSON = "json"
BACKEND_POSTGRES = "postgres"
BACKEND_SQLITE = "sqlite"
SUPPORTED_BACKENDS: tuple[str, ...] = (
    BACKEND_JSON,
    BACKEND_SQLITE,
    BACKEND_POSTGRES,
)

#: Where the Postgres backend will keep its rows.
POSTGRES_TABLE = "user_assets"
#: Where the Postgres backend will keep the user accounts.
POSTGRES_USERS_TABLE = "users"
#: Connection string used when the configuration does not carry one.
DEFAULT_POSTGRES_DSN = "postgresql://localhost/asalloc"

#: Tables of the SQLite database (one per store).
SQLITE_TABLE_ASSETS = "user_assets"
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


def assets_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> "Storage[UserAssets]":
    """Backend for the assets store (one row per user).

    The JSON backend is strict: a malformed row raises instead of being
    skipped, so a broken assets file fails loudly rather than looking
    like an empty portfolio.
    """
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(
            db_file or path, SQLITE_TABLE_ASSETS, UserAssets
        )
    if name == BACKEND_POSTGRES:
        dsn = (postgres_dsn or "").strip() or DEFAULT_POSTGRES_DSN
        logger.error(
            "assets backend %r is selected but not implemented yet; the "
            "store will fail to open (dsn=%s, table=%s)",
            name,
            dsn,
            POSTGRES_TABLE,
        )
        return PostgresStorage(dsn, POSTGRES_TABLE, UserAssets)
    return JsonStorage(path, UserAssets, strict=True)


def users_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
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
    if name == BACKEND_POSTGRES:
        dsn = (postgres_dsn or "").strip() or DEFAULT_POSTGRES_DSN
        logger.error(
            "users backend %r is selected but not implemented yet; the "
            "store will fail to open (dsn=%s, table=%s)",
            name,
            dsn,
            POSTGRES_USERS_TABLE,
        )
        return PostgresStorage(dsn, POSTGRES_USERS_TABLE, UserRecord)
    return JsonStorage(path, UserRecord, strict=True)


def cache_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> "Storage[CacheEntry]":
    """Backend for the per-ISIN cache (price, countries, sectors)."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(db_file or path, SQLITE_TABLE_CACHE, CacheEntry)
    if name == BACKEND_POSTGRES:
        dsn = (postgres_dsn or "").strip() or DEFAULT_POSTGRES_DSN
        logger.error(
            "cache backend %r is selected but not implemented yet; the "
            "store will fail to open (dsn=%s, table=%s)",
            name,
            dsn,
            "cache",
        )
        return PostgresStorage(dsn, "cache", CacheEntry)
    return JsonStorage(path, CacheEntry)


def isin_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> "Storage[IsinRecord]":
    """Backend for the ISIN registry (issuer, bucket, vendor ref)."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(db_file or path, SQLITE_TABLE_ISIN, IsinRecord)
    if name == BACKEND_POSTGRES:
        dsn = (postgres_dsn or "").strip() or DEFAULT_POSTGRES_DSN
        logger.error(
            "isin backend %r is selected but not implemented yet; the "
            "store will fail to open (dsn=%s, table=%s)",
            name,
            dsn,
            "isin_registry",
        )
        return PostgresStorage(dsn, "isin_registry", IsinRecord)
    return JsonStorage(path, IsinRecord)


def sessions_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
    db_file: str | Path | None = None,
) -> "Storage[SessionRecord]":
    """Backend for the browser sessions (one row per session id)."""
    name = normalize_backend(backend)
    if name == BACKEND_SQLITE:
        return _sqlite_backend(
            db_file or path, SQLITE_TABLE_SESSIONS, SessionRecord
        )
    if name == BACKEND_POSTGRES:
        dsn = (postgres_dsn or "").strip() or DEFAULT_POSTGRES_DSN
        logger.error(
            "sessions backend %r is selected but not implemented yet; the "
            "store will fail to open (dsn=%s, table=%s)",
            name,
            dsn,
            "sessions",
        )
        return PostgresStorage(dsn, "sessions", SessionRecord)
    return JsonStorage(path, SessionRecord, strict=True)
