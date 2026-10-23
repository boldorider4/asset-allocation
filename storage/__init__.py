# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage abstractions, row types, backends and repositories.

* :mod:`storage.storage` — backend-agnostic ``Storage`` / ``StorageObject``.
* :mod:`storage.records` — backend-agnostic row types (``DictRow``,
  ``CacheEntry``, ``AssetBucket``, ``IsinRecord``, ``UserRecord``,
  ``SessionRecord``, ``PositionRecord``, ``UserPortfolioRecord``).
* :mod:`storage.memory_storage` / :mod:`storage.sqlite_storage` — backends.
  :mod:`storage.json_storage` remains available for direct import and
  one-time import tooling.
* :mod:`storage.wiring` — the composition point building the SQLite backend.
* :mod:`storage.repositories` — domain logic over any backend
  (``CacheRepository``, ``AssetRepository``, ``IsinRegistry``,
  ``PositionRepository``, ``UserPortfolioRepository``,
  ``UsersRepository``, ``SessionsRepository``).
"""

from storage.memory_storage import MemoryStorage
from storage.records import (
    DEFAULT_PORTFOLIO_NAME,
    AssetBucket,
    CacheEntry,
    DictRow,
    IsinRecord,
    PositionRecord,
    SessionRecord,
    UserPortfolioRecord,
    UserRecord,
)
from storage.repositories import (
    AssetRepository,
    CacheRepository,
    IsinRegistry,
    PositionRepository,
    SessionsRepository,
    UserPortfolioRepository,
    UsersRepository,
)
from storage.sqlite_storage import SqliteStorage
from storage.storage import Storage, StorageObject
from storage.wiring import (
    cache_backend,
    isin_backend,
    positions_backend,
    sessions_backend,
    user_portfolios_backend,
    users_backend,
)

__all__ = [
    "Storage",
    "StorageObject",
    "DEFAULT_PORTFOLIO_NAME",
    "DictRow",
    "CacheEntry",
    "AssetBucket",
    "IsinRecord",
    "UserRecord",
    "SessionRecord",
    "PositionRecord",
    "UserPortfolioRecord",
    "MemoryStorage",
    "SqliteStorage",
    "CacheRepository",
    "AssetRepository",
    "IsinRegistry",
    "PositionRepository",
    "UserPortfolioRepository",
    "UsersRepository",
    "SessionsRepository",
    "UnknownUser",
    "cache_backend",
    "isin_backend",
    "positions_backend",
    "sessions_backend",
    "user_portfolios_backend",
    "users_backend",
]
