# SPDX-License-Identifier: AGPL-3.0-or-later
"""Composition point: which backend stores the assets.

Everything above this line (repositories, the runtime context, the web
backend, the scrapers) talks to the *repository* and never to a concrete
backend. This module is the single place where the concrete backend is
built, so moving the assets store to Postgres is one line here.

Configuration lives with the application: the backend name and the
Postgres DSN are read from ``config.ini`` by the app layer (see
``cli.context``) and passed in. This module reads no files and knows no
settings.

The row type stays ``UserAssets`` (one row per user, payload = that
user's portfolio buckets), which maps onto the Postgres stub's intended
layout ``(key TEXT PRIMARY KEY, payload JSONB)`` with ``key`` = user.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from storage.json_storage import JsonStorage
from storage.postgres_storage import PostgresStorage
from storage.records import UserAssets

if TYPE_CHECKING:
    from storage.storage import Storage

__all__ = [
    "BACKEND_JSON",
    "BACKEND_POSTGRES",
    "DEFAULT_POSTGRES_DSN",
    "POSTGRES_TABLE",
    "SUPPORTED_BACKENDS",
    "assets_backend",
    "normalize_backend",
]

logger = logging.getLogger(__name__)

#: Backend names the application may configure.
BACKEND_JSON = "json"
BACKEND_POSTGRES = "postgres"
SUPPORTED_BACKENDS: tuple[str, ...] = (BACKEND_JSON, BACKEND_POSTGRES)

#: Where the Postgres backend will keep its rows.
POSTGRES_TABLE = "user_assets"
#: Connection string used when the configuration does not carry one.
DEFAULT_POSTGRES_DSN = "postgresql://localhost/asalloc"


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


def assets_backend(
    path: str | Path,
    backend: str = BACKEND_JSON,
    postgres_dsn: str = DEFAULT_POSTGRES_DSN,
) -> "Storage[UserAssets]":
    """Backend for the assets store (one row per user).

    The JSON backend is strict: a malformed row raises instead of being
    skipped, so a broken assets file fails loudly rather than looking
    like an empty portfolio.
    """
    name = normalize_backend(backend)
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
