# SPDX-License-Identifier: AGPL-3.0-or-later
"""Composition point: which backend stores the assets.

Everything above this line (repositories, the runtime context, the web
backend, the scrapers) talks to the *repository* and never to a concrete
backend. This module is the single place where the concrete backend is
chosen, so moving the assets store to Postgres is one line:

    def assets_backend(path): return PostgresStorage(dsn, "user_assets", UserAssets)

The row type stays ``UserAssets`` (one row per user, payload = that
user's portfolio buckets), which maps onto the Postgres stub's intended
layout ``(key TEXT PRIMARY KEY, payload JSONB)`` with ``key`` = user.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from storage.json_storage import JsonStorage
from storage.records import UserAssets

if TYPE_CHECKING:
    from storage.storage import Storage

__all__ = ["assets_backend"]


def assets_backend(path: str | Path) -> "Storage[UserAssets]":
    """Backend for the assets store (one row per user).

    Strict: a malformed row raises instead of being skipped, so a broken
    assets file fails loudly rather than looking like an empty portfolio.
    """
    return JsonStorage(path, UserAssets, strict=True)
