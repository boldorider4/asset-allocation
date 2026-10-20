# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage abstractions, row types, backends and repositories.

* :mod:`storage.storage` — backend-agnostic ``Storage`` / ``StorageObject``.
* :mod:`storage.records` — backend-agnostic row types (``DictRow``,
  ``CacheEntry``, ``AssetBucket``, ``UserAssets``, ``IsinRecord``).
* :mod:`storage.json_storage` / :mod:`storage.memory_storage` /
  :mod:`storage.postgres_storage` — backends.
* :mod:`storage.wiring` — the composition point choosing the backend.
* :mod:`storage.repositories` — domain logic over any backend
  (``CacheRepository``, ``AssetRepository``, ``IsinRegistry``).
"""

from storage.json_storage import JsonStorage
from storage.memory_storage import MemoryStorage
from storage.postgres_storage import PostgresStorage
from storage.records import (
    AssetBucket,
    CacheEntry,
    DictRow,
    IsinRecord,
    UserAssets,
)
from storage.repositories import (
    AssetRepository,
    CacheRepository,
    IsinRegistry,
    UnknownUser,
)
from storage.storage import Storage, StorageObject
from storage.wiring import (
    BACKEND_JSON,
    BACKEND_POSTGRES,
    assets_backend,
    normalize_backend,
)

__all__ = [
    "Storage",
    "StorageObject",
    "DictRow",
    "CacheEntry",
    "AssetBucket",
    "UserAssets",
    "IsinRecord",
    "JsonStorage",
    "MemoryStorage",
    "PostgresStorage",
    "CacheRepository",
    "AssetRepository",
    "IsinRegistry",
    "UnknownUser",
    "assets_backend",
    "normalize_backend",
    "BACKEND_JSON",
    "BACKEND_POSTGRES",
]
