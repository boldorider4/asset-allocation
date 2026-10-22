# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage abstractions, row types, backends and repositories.

* :mod:`storage.storage` — backend-agnostic ``Storage`` / ``StorageObject``.
* :mod:`storage.records` — backend-agnostic row types (``DictRow``,
  ``CacheEntry``, ``AssetBucket``, ``UserAssets``, ``IsinRecord``).
* :mod:`storage.json_storage` / :mod:`storage.memory_storage` /
  :mod:`storage.sqlite_storage` — backends.
* :mod:`storage.wiring` — the composition point choosing the backend.
* :mod:`storage.repositories` — domain logic over any backend
  (``CacheRepository``, ``AssetRepository``, ``IsinRegistry``).
"""

from storage.json_storage import JsonStorage
from storage.memory_storage import MemoryStorage
from storage.records import (
    DEFAULT_PORTFOLIO_NAME,
    AssetBucket,
    CacheEntry,
    DictRow,
    IsinRecord,
    UserAssets,
    UserRecord,
)
from storage.repositories import (
    AssetRepository,
    CacheRepository,
    IsinRegistry,
    UnknownUser,
    UsersRepository,
)
from storage.sqlite_storage import SqliteStorage
from storage.storage import Storage, StorageObject
from storage.wiring import (
    BACKEND_JSON,
    BACKEND_SQLITE,
    assets_backend,
    normalize_backend,
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
    "JsonStorage",
    "MemoryStorage",
    "SqliteStorage",
    "CacheRepository",
    "AssetRepository",
    "IsinRegistry",
    "UsersRepository",
    "UnknownUser",
    "assets_backend",
    "users_backend",
    "normalize_backend",
    "BACKEND_JSON",
    "BACKEND_SQLITE",
]