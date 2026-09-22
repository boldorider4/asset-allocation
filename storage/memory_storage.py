# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-memory backend for the :mod:`storage.storage` abstractions.

Reference implementation of the point-access :class:`Storage` ABC:
no I/O, no dirty tracking, strict on bad rows (invalid ``initial``
entries raise instead of being skipped — unlike the lenient JSON
loader). Used as the default backend for the seeded in-memory ISIN
registry and as the swap-backend in composition tests.
"""

from __future__ import annotations

from typing import Any, TypeVar

from storage.records import AssetBucket
from storage.storage import Storage, StorageObject

__all__ = ["MemoryStorage"]

T = TypeVar("T", bound=StorageObject)


class MemoryStorage(Storage[T]):
    """Dict-backed :class:`Storage` with no persistence."""

    def __init__(
        self,
        object_factory: type[T],
        initial: dict[str, Any] | None = None,
    ) -> None:
        self._factory: type[T] = object_factory
        self._objects: dict[str, T] = {}
        for key, raw in (initial or {}).items():
            self._objects[str(key)] = object_factory.from_dict(str(key), raw)  # type: ignore[assignment]

    def _empty_raw(self) -> Any:
        return [] if self._factory is AssetBucket else {}

    def get(self, key: str) -> T | None:
        return self._objects.get(str(key))

    def get_or_create(self, key: str) -> T:
        key = str(key)
        obj = self._objects.get(key)
        if obj is None:
            obj = self._factory.from_dict(key, self._empty_raw())  # type: ignore[assignment]
            self._objects[key] = obj
        return obj

    def put(self, obj: T) -> None:
        self._objects[obj.key] = obj

    def add(self, obj: T) -> None:
        if obj.key in self._objects:
            raise KeyError(f"row {obj.key!r} already exists")
        self._objects[obj.key] = obj

    def upsert(self, key: str, partial: dict[str, Any]) -> T:
        obj = self.get_or_create(key)
        obj.merge(partial)
        return obj

    def remove(self, key: str) -> None:
        self._objects.pop(str(key), None)

    def __contains__(self, key: object) -> bool:
        return str(key) in self._objects

    def __len__(self) -> int:
        return len(self._objects)

    # -- backend-neutral lifecycle (Storage ABC) ------------------------
    def open(self) -> MemoryStorage[T]:
        # Nothing to prepare.
        return self

    def snapshot(self) -> dict[str, Any]:
        return {k: o.to_dict() for k, o in self._objects.items()}

    def persist(self) -> None:
        # Nothing to persist.
        pass

    def close(self) -> None:
        # Nothing to release; no buffered writes to flush.
        pass
