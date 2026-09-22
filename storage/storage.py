# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backend-agnostic storage abstractions.

``StorageObject`` is a single row (a cache entry, an asset bucket, an
ISIN registry record, ...). ``Storage`` manages rows keyed by string.

The ABC is deliberately *point-access only*: ``get``/``put``/``add``/
``upsert``/``remove``/``get_or_create``/``__contains__``/``close``.
Lifecycle is backend-defined and lives on concrete backends, not here:

* file backends: lazy ``load()``, dirty tracking, atomic ``save()``;
* DB backends: ``connect()``, ``commit()``/``rollback()``, context manager.

Domain logic lives in repositories (see :mod:`storage.repositories`),
which program against this interface so backends swap without touching them.

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
from typing import Any, Generic, TypeVar

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
        """Serialised form of this row (must be backend-encodable)."""
        ...

    @classmethod
    @abstractmethod
    def from_dict(cls, key: str, raw: Any) -> StorageObject:
        """Build a row from its serialised form.

        Must coerce and validate; raise ``TypeError``/``ValueError``/
        ``KeyError`` on malformed input.
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
    """Point-access repository of :class:`StorageObject` rows keyed by string."""

    @abstractmethod
    def get(self, key: str) -> T | None:
        """Return the row for ``key`` or ``None``."""
        ...

    @abstractmethod
    def get_or_create(self, key: str) -> T:
        """Return the row for ``key``, creating an empty one if missing.

        Backends implement this atomically where possible. Rows whose
        empty form is invalid (e.g. registry records needing an issuer)
        raise on creation; create those via :meth:`upsert` with a
        complete partial instead.
        """
        ...

    @abstractmethod
    def put(self, obj: T) -> None:
        """Insert or replace ``obj`` wholesale."""
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

    @abstractmethod
    def __contains__(self, key: object) -> bool:
        ...

    @abstractmethod
    def close(self) -> None:
        """Backend-defined teardown.

        File backends flush buffered writes; DB backends release the
        connection *without* committing (use ``commit()`` or the context
        manager for that). Must be safe to call without prior use.
        """
        ...
