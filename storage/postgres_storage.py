# SPDX-License-Identifier: AGPL-3.0-or-later
"""Postgres backend stub for the :mod:`storage.storage` abstractions.

Not yet implemented: every I/O method raises :exc:`NotImplementedError`
with its intended SQL in the docstring. The driver (``psycopg``) is
imported lazily inside :meth:`connect`, so importing this module never
requires the driver to be installed.

Intended layout: one table per store, ``(key TEXT PRIMARY KEY,
payload JSONB)``; rows serialise via ``to_dict()`` / ``from_dict()``,
exactly like the JSON backend. Lifecycle here is transactional
(``connect`` / ``commit`` / ``rollback`` / context manager) — there is
deliberately no ``load`` / ``save`` / dirty flag; every point access
hits the database inside the caller's transaction.
"""

from __future__ import annotations

from typing import Any, TypeVar

from storage.storage import Storage, StorageObject

__all__ = ["PostgresStorage"]

T = TypeVar("T", bound=StorageObject)


class PostgresStorage(Storage[T]):
    """Transactional Postgres store (stub)."""

    def __init__(self, dsn: str, table: str, object_factory: type[T]) -> None:
        self._dsn = dsn
        self._table = table
        self._factory: type[T] = object_factory
        self._conn: Any = None

    @property
    def table(self) -> str:
        return self._table

    @property
    def connected(self) -> bool:
        return self._conn is not None

    # -- lifecycle (Postgres-only, not on the Storage ABC) --------------
    def connect(self) -> PostgresStorage[T]:
        """Open the connection (``psycopg.connect(dsn)``)."""
        raise NotImplementedError(
            "PostgresStorage.connect: psycopg.connect(self._dsn); "
            "CREATE TABLE IF NOT EXISTS <table> "
            "(key TEXT PRIMARY KEY, payload JSONB)"
        )

    def commit(self) -> None:
        """Commit the current transaction (``conn.commit()``)."""
        raise NotImplementedError("PostgresStorage.commit: self._conn.commit()")

    def rollback(self) -> None:
        """Roll back the current transaction (``conn.rollback()``)."""
        raise NotImplementedError("PostgresStorage.rollback: self._conn.rollback()")

    # -- backend-neutral lifecycle (Storage ABC) ------------------------
    def open(self) -> PostgresStorage[T]:
        """Connect + ensure table (``connect()`` + DDL)."""
        self.connect()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Full-table scan as ``{key: payload}`` (bulk-only, not hot-path)."""
        raise NotImplementedError(
            "PostgresStorage.snapshot: SELECT key, payload FROM <table>"
        )

    def persist(self) -> None:
        """Commit staged writes."""
        self.commit()

    def __enter__(self) -> PostgresStorage[T]:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    # -- point access (Storage ABC) -------------------------------------
    def _require_conn(self) -> Any:
        if self._conn is None:
            raise NotImplementedError(
                "PostgresStorage: call connect() (or use it as a context "
                "manager) before issuing queries"
            )
        return self._conn

    def get(self, key: str) -> T | None:
        """``SELECT payload FROM <table> WHERE key = %s`` + ``from_dict``."""
        raise NotImplementedError(
            "PostgresStorage.get: SELECT payload FROM <table> WHERE key = %s"
        )

    def get_or_create(self, key: str) -> T:
        """``SELECT`` then ``INSERT ... ON CONFLICT (key) DO NOTHING``."""
        raise NotImplementedError(
            "PostgresStorage.get_or_create: SELECT, else INSERT an empty "
            "payload with ON CONFLICT (key) DO NOTHING, then re-SELECT"
        )

    def put(self, obj: T) -> None:
        """``INSERT ... ON CONFLICT (key) DO UPDATE SET payload``."""
        raise NotImplementedError(
            "PostgresStorage.put: INSERT INTO <table> (key, payload) VALUES "
            "(%s, %s) ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload"
        )

    def add(self, obj: T) -> None:
        """Plain ``INSERT``; unique violation surfaces as ``KeyError``."""
        raise NotImplementedError(
            "PostgresStorage.add: INSERT INTO <table> (key, payload) VALUES "
            "(%s, %s); catch UniqueViolation and raise KeyError"
        )

    def upsert(self, key: str, partial: dict[str, Any]) -> T:
        """Single-statement read-modify-write (row lock or JSONB ``||``)."""
        raise NotImplementedError(
            "PostgresStorage.upsert: SELECT ... FOR UPDATE then UPDATE, or "
            "a single INSERT ... ON CONFLICT DO UPDATE with payload merging; "
            "validate the merged row via merge() before writing"
        )

    def remove(self, key: str) -> None:
        """``DELETE FROM <table> WHERE key = %s`` (no error when missing)."""
        raise NotImplementedError(
            "PostgresStorage.remove: DELETE FROM <table> WHERE key = %s"
        )

    def __contains__(self, key: object) -> bool:
        """``SELECT 1 FROM <table> WHERE key = %s``."""
        raise NotImplementedError(
            "PostgresStorage.__contains__: SELECT 1 FROM <table> WHERE key = %s"
        )

    def close(self) -> None:
        """Release the connection *without* committing."""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
