# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage abstractions and JSON-file backend.

Backend-agnostic interfaces live in :mod:`storage.storage`; the
JSON-file derivations live in :mod:`storage.json_storage`.
"""

from storage.json_storage import (
    AssetBucket,
    AssetStore,
    CacheEntry,
    CacheStore,
    DEFAULT_ISIN_RECORDS,
    IsinRecord,
    IsinRegistryStore,
    JsonStorage,
    JsonStorageObject,
)
from storage.storage import Storage, StorageObject

__all__ = [
    "Storage",
    "StorageObject",
    "JsonStorage",
    "JsonStorageObject",
    "CacheEntry",
    "CacheStore",
    "AssetBucket",
    "AssetStore",
    "IsinRecord",
    "IsinRegistryStore",
    "DEFAULT_ISIN_RECORDS",
]
