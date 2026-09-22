# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON-file backend for the :mod:`storage.storage` abstractions.

Only transport lives here: :class:`JsonStorage` persists
``{key: row.to_dict()}`` objects with lazy load, dirty tracking and
atomic save (temp file + rename). Row types live in
:mod:`storage.records`; domain logic lives in
:mod:`storage.repositories`. Nothing here imports ``context``/``utils``/
``position`` so the dependency arrow stays one-way.

Load is lenient (missing/corrupt file or bad row ⇒ skip with a
warning); mutation is strict (bad keys/values raise, via the rows).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator, TypeVar

from storage.records import AssetBucket, DictRow
from storage.storage import Storage, StorageObject

__all__ = [
    "JsonStorage",
]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=StorageObject)


class JsonStorage(Storage[T]):
    """Generic JSON-file store: ``{key: row.to_dict()}`` object on disk.

    ``object_factory`` defaults to :class:`DictRow`, so ``JsonStorage(path)``
    works bare for scratch/generic use. Note the schema-full contract: a
    bare ``DictRow`` has an empty ``ALLOWED_KEYS`` and therefore only holds
    empty-dict rows — every application use must go through a typed row
    with its own ``ALLOWED_KEYS`` (e.g. ``CacheEntry``). If those key sets
    ever change, treat it as a schema migration (including future DB DDL).
    """

    def __init__(
        self, path: str | Path, object_factory: type[T] | None = None
    ) -> None:
        self._path = Path(path)
        self._factory: type[T] = (
            object_factory if object_factory is not None else DictRow  # type: ignore[assignment]
        )
        self._objects: dict[str, T] = {}
        self._loaded = False
        self._dirty = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def is_dirty(self) -> bool:
        """True when in-memory state differs from the file on disk."""
        return self._dirty

    # -- internals ------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _empty_raw(self) -> Any:
        # Bucket rows serialise as bare lists, everything else as objects.
        return [] if self._factory is AssetBucket else {}

    # -- point access (Storage ABC) -------------------------------------
    def get(self, key: str) -> T | None:
        self._ensure_loaded()
        return self._objects.get(str(key))

    def get_or_create(self, key: str) -> T:
        """Return the row for ``key``, creating an empty one if missing."""
        self._ensure_loaded()
        key = str(key)
        obj = self._objects.get(key)
        if obj is None:
            obj = self._factory.from_dict(key, self._empty_raw())  # type: ignore[assignment]
            self._objects[key] = obj
            self._dirty = True
        return obj

    def put(self, obj: T) -> None:
        self._ensure_loaded()
        self._objects[obj.key] = obj
        self._dirty = True

    def add(self, obj: T) -> None:
        self._ensure_loaded()
        if obj.key in self._objects:
            raise KeyError(f"row {obj.key!r} already exists")
        self._objects[obj.key] = obj
        self._dirty = True

    def upsert(self, key: str, partial: dict[str, Any]) -> T:
        obj = self.get_or_create(key)
        before = obj.to_dict()
        obj.merge(partial)
        if obj.to_dict() != before:
            self._dirty = True
        return obj

    def remove(self, key: str) -> None:
        self._ensure_loaded()
        if self._objects.pop(str(key), None) is not None:
            self._dirty = True

    def __contains__(self, key: object) -> bool:
        self._ensure_loaded()
        return str(key) in self._objects

    # -- backend-neutral lifecycle (Storage ABC) ------------------------
    def open(self) -> JsonStorage[T]:
        """Load the file (idempotent; lenient on bad input)."""
        return self.load()

    def snapshot(self) -> dict[str, Any]:
        """Whole store as plain ``{key: row.to_dict()}`` (wire format)."""
        return self.to_plain_dict()

    def persist(self) -> None:
        """Atomically save staged writes (no-op when clean)."""
        self.save()

    def close(self) -> None:
        # File-backend teardown: flush buffered writes.
        if self._dirty:
            self.save()

    # -- file lifecycle (JSON-only, not on the Storage ABC) -------------
    def load(self) -> JsonStorage[T]:
        """Load rows from the file (idempotent; lenient on bad input)."""
        if self._loaded:
            return self
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            logger.info("storage file %s not found; starting empty", self._path)
            raw = {}
        except json.JSONDecodeError:
            logger.warning("storage file %s is not valid JSON; starting empty", self._path)
            raw = {}
        except OSError as exc:
            logger.warning("storage file %s unreadable (%s); starting empty", self._path, exc)
            raw = {}
        if not isinstance(raw, dict):
            logger.warning("storage file %s root must be an object; starting empty", self._path)
            raw = {}
        objects: dict[str, T] = {}
        for key, value in raw.items():
            try:
                objects[str(key)] = self._factory.from_dict(str(key), value)  # type: ignore[assignment]
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning("skipping bad row %r in %s (%s)", key, self._path, exc)
        self._objects = objects
        self._loaded = True
        self._dirty = False
        return self

    def save(self) -> None:
        """Persist dirty rows atomically (no-op when clean)."""
        if self._loaded and not self._dirty:
            return
        self._ensure_loaded()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.to_plain_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, self._path)
        self._dirty = False
        logger.info("wrote storage to %s", self._path)

    def mark_clean(self) -> None:
        """Clear the dirty flag without persisting (testing escape hatch)."""
        self._dirty = False

    # -- bulk introspection (JSON-only conveniences, not for hot paths) --
    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._objects)

    def keys(self) -> Iterator[str]:
        self._ensure_loaded()
        return iter(list(self._objects.keys()))

    def items(self) -> Iterator[tuple[str, T]]:
        self._ensure_loaded()
        return iter(list(self._objects.items()))

    def values(self) -> Iterator[T]:
        self._ensure_loaded()
        return iter(list(self._objects.values()))

    def to_plain_dict(self) -> dict[str, Any]:
        """Whole store as plain ``{key: row.to_dict()}`` (wire format)."""
        self._ensure_loaded()
        return {k: o.to_dict() for k, o in self._objects.items()}
