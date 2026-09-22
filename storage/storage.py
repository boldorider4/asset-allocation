# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backend-agnostic storage abstractions.

``StorageObject`` is a single row (a cache entry, an asset bucket, an
ISIN registry record, ...). ``Storage`` manages rows keyed by string.

This module is deliberately backend-free: no ``json``/``pathlib``/
``os`` imports. Concrete backends (JSON file today, Postgres/MariaDB
tomorrow) only re-implement :class:`Storage`; callers program against
this interface so the backend can be swapped without touching them.

Persistence contract shared by all backends:

* :meth:`StorageObject.to_dict` / :meth:`StorageObject.from_dict`
  define the serialised form of one row.
* :meth:`StorageObject.merge` applies a *validated partial upsert*:
  only the supplied fields change, everything else is preserved.
* :meth:`Storage.put` replaces a whole row, :meth:`Storage.upsert`
  get-or-creates then merges, :meth:`Storage.add` inserts only and
  raises on duplicates (mirrors a DB uniqueness constraint).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Iterator, TypeVar

__all__ = ["StorageObject", "Storage"]

T = TypeVar("T", bound="StorageObject")


class StorageObject(ABC):
    """One row in a :class:`Storage`."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Unique string key of this row (ISIN, bucket name, ...)."""
        ...

    @abstractmethod
    def to_dict(self) -> Any:
        """Serialised form of this row (JSON-serialisable)."""
        ...

    @classmethod
    @abstractmethod
    def from_dict(cls, key: str, raw: Any) -> StorageObject:
        """Build a row from its serialised form.

        Must coerce and validate; raise ``TypeError``/``ValueError``/
        ``KeyError`` on malformed input. Backends treat such errors as
        "skip this row with a warning" when loading (lenient-on-load).
        """
        ...

    @abstractmethod
    def merge(self, partial: dict[str, Any]) -> None:
        """Merge a partial update into this row (validated).

        Unknown keys raise ``KeyError``; bad values raise
        ``TypeError``/``ValueError`` (strict-on-write).
        """
        ...

    @abstractmethod
    def validate(self) -> None:
        """Raise on invalid in-memory state (strict-on-write)."""
        ...


class Storage(ABC, Generic[T]):
    """Repository of :class:`StorageObject` rows keyed by string."""

    # -- point access -------------------------------------------------
    @abstractmethod
    def get(self, key: str) -> T | None:
        """Return the row for ``key`` or ``None``."""
        ...

    @abstractmethod
    def put(self, obj: T) -> None:
        """Insert or replace ``obj`` wholesale; marks the store dirty."""
        ...

    @abstractmethod
    def add(self, obj: T) -> None:
        """Insert ``obj``; raise ``KeyError`` if ``obj.key`` exists."""
        ...

    @abstractmethod
    def upsert(self, key: str, partial: dict[str, Any]) -> T:
        """Get-or-create ``key``, merge ``partial`` into it, return it."""
        ...

    @abstractmethod
    def remove(self, key: str) -> None:
        """Delete ``key`` if present (no error when missing)."""
        ...

    # -- mapping-style introspection ----------------------------------
    @abstractmethod
    def __contains__(self, key: object) -> bool:
        ...

    @abstractmethod
    def __len__(self) -> int:
        ...

    @abstractmethod
    def keys(self) -> Iterator[str]:
        ...

    @abstractmethod
    def items(self) -> Iterator[tuple[str, T]]:
        ...

    @abstractmethod
    def values(self) -> Iterator[T]:
        ...

    # -- persistence --------------------------------------------------
    @abstractmethod
    def load(self) -> Storage:
        """Load rows from the backing store (idempotent)."""
        ...

    @abstractmethod
    def save(self) -> None:
        """Persist dirty rows to the backing store (no-op when clean)."""
        ...

    @property
    @abstractmethod
    def is_dirty(self) -> bool:
        """True when in-memory state differs from the backing store."""
        ...

    @abstractmethod
    def mark_clean(self) -> None:
        """Clear the dirty flag without persisting (testing escape hatch)."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Flush if dirty and release backend resources (if any)."""
        ...
