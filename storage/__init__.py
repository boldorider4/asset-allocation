# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage abstractions, row types, backends and repositories.

* :mod:`storage.storage` — backend-agnostic ``Storage`` / ``StorageObject``.
* :mod:`storage.records` — backend-agnostic row types (``DictRow``,
  ``CacheEntry``, ``AssetBucket``, ``UserAssets``, ``IsinRecord``,
  ``UserRecord``, ``SessionRecord``, ``PositionRecord``,
  ``CountryRecord``, ``SectorRecord``, ``UserPortfolioRecord``).
* :mod:`storage.json_storage` / :mod:`storage.memory_storage` /
  :mod:`storage.sqlite_storage` — backends.
* :mod:`storage.wiring` — the composition point choosing the backend.
* :mod:`storage.repositories` — domain logic over any backend
  (``CacheRepository``, ``AssetRepository``, ``IsinRegistry``,
  ``PositionRepository``, ``CountryRepository``, ``SectorRepository``,
  ``UserPortfolioRepository``, ``UsersRepository``, ``SessionsRepository``).
"""

from storage.json_storage import JsonStorage
from storage.memory_storage import MemoryStorage
from storage.records import (
    DEFAULT_PORTFOLIO_NAME,
    AssetBucket,
    CacheEntry,
    CountryRecord,
    DictRow,
    IsinRecord,
    PositionRecord,
    SectorRecord,
    SessionRecord,
    UserAssets,
    UserPortfolioRecord,
    UserRecord,
)
from storage.repositories import (
    AssetRepository,
    CacheRepository,
    CountryRepository,
    IsinRegistry,
    PositionRepository,
    SectorRepository,
    SessionsRepository,
    UserPortfolioRepository,
    UsersRepository,
)
from storage.sqlite_storage import SqliteStorage
from storage.storage import Storage, StorageObject
from storage.wiring import (
    BACKEND_JSON,
    BACKEND_SQLITE,
    assets_backend,
    cache_backend,
    isin_backend,
    normalize_backend,
    positions_backend,
    position_countries_backend,
    position_sectors_backend,
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
    "UserAssets",
    "IsinRecord",
    "UserRecord",
    "SessionRecord",
    "PositionRecord",
    "CountryRecord",
    "SectorRecord",
    "UserPortfolioRecord",
    "JsonStorage",
    "MemoryStorage",
    "SqliteStorage",
    "CacheRepository",
    "AssetRepository",
    "IsinRegistry",
    "PositionRepository",
    "CountryRepository",
    "SectorRepository",
    "UserPortfolioRepository",
    "UsersRepository",
    "SessionsRepository",
    "UnknownUser",
    "assets_backend",
    "cache_backend",
    "isin_backend",
    "normalize_backend",
    "positions_backend",
    "position_countries_backend",
    "position_sectors_backend",
    "sessions_backend",
    "user_portfolios_backend",
    "users_backend",
    "BACKEND_JSON",
    "BACKEND_SQLITE",
]