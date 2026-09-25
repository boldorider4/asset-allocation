# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage abstractions, row types, backends and repositories.

* :mod:`storage.storage` — backend-agnostic ``Storage`` / ``StorageObject``.
* :mod:`storage.records` — backend-agnostic row types (``DictRow``,
  ``CacheEntry``, ``AssetBucket``, ``IsinRecord``).
* :mod:`storage.json_storage` / :mod:`storage.memory_storage` /
  :mod:`storage.postgres_storage` — backends.
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
)
from storage.repositories import AssetRepository, CacheRepository, IsinRegistry
from storage.storage import Storage, StorageObject

__all__ = [
    "Storage",
    "StorageObject",
    "DictRow",
    "CacheEntry",
    "AssetBucket",
    "IsinRecord",
    "JsonStorage",
    "MemoryStorage",
    "PostgresStorage",
    "CacheRepository",
    "AssetRepository",
    "IsinRegistry",
]
