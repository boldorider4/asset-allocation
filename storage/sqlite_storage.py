# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite backend for the :mod:`storage.storage` abstractions.

The file-backed sibling of :mod:`storage.postgres_storage`: the same
class shape, the same method names, the same signatures — but here the
SQL is real instead of a stub. One table per store, one row per key::

    CREATE TABLE <table> (key TEXT PRIMARY KEY, payload TEXT NOT NULL)

The payload is the row's JSON form (``to_dict()``), so a row written
by one backend reads back identically in the other.

SQLite runs the database *inside* the process: no server, no idle
load, nothing to supervise. WAL mode lets the web server read while
the update writes, and ``busy_timeout`` makes a writer-writer
collision wait a moment instead of failing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, TypeVar

from storage.records import AssetBucket
from storage.storage import Storage, StorageObject

__all__ = ["SqliteStorage"]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=StorageObject)

#: How long a statement waits for a lock before giving up (seconds).
BUSY_TIMEOUT_SECONDS = 5.0

#: The open connections, one per database file.
#:
#: Every table of a database *shares* one connection: a second
#: connection would deadlock against the first one's write transaction
#: (SQLite's busy handler cannot break that deadlock, it only times
#: out). One connection per process, one transaction, no surprises.
_CONNECTIONS: dict[str, tuple[sqlite3.Connection, int]] = {}


def _shared_connection(path: Path) -> sqlite3.Connection:
    """The connection for a database file (one per process)."""
    key = str(path)
    entry = _CONNECTIONS.get(key)
    if entry is not None:
        conn, refs = entry
        _CONNECTIONS[key] = (conn, refs + 1)
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_SECONDS,
        isolation_level="DEFERRED",
        # The web server is multi-threaded and the connection is shared
        # per process: sqlite3 serializes the statements on the
        # connection itself, so this is safe.
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # Readers keep working while a writer is active; a writer-writer
    # collision waits for the lock instead of failing outright.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
    conn.execute("PRAGMA synchronous=NORMAL")
    _CONNECTIONS[key] = (conn, 1)
    return conn


def _release_connection(path: Path) -> None:
    """Give back one reference; the last one closes the connection."""
    key = str(path)
    entry = _CONNECTIONS.get(key)
    if entry is None:
        return
    conn, refs = entry
    if refs > 1:
        _CONNECTIONS[key] = (conn, refs - 1)
        return
    del _CONNECTIONS[key]
    conn.close()


class SqliteStorage(Storage[T]):
    """Transactional SQLite store."""

    def __init__(
        self, path: str | Path, table: str, object_factory: type[T]
    ) -> None:
        self._path = Path(path)
        self._table = table
        self._factory: type[T] = object_factory
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        """The database file."""
        return self._path

    @property
    def table(self) -> str:
        """The table this store reads and writes."""
        return self._table

    @property
    def connected(self) -> bool:
        return self._conn is not None

    # -- lifecycle (SQLite-only, not on the Storage ABC) ----------------
    def connect(self) -> SqliteStorage[T]:
        """Open the connection and tune the file (WAL, busy timeout).

        The connection is shared with the other stores of the same
        database file (one connection per process). Idempotent: an open
        connection is kept as it is, so the per-process holder can call
        it on every use.
        """
        if self._conn is not None:
            return self
        self._conn = _shared_connection(self._path)
        self._ensure_table()
        return self

    def commit(self) -> None:
        """Commit the open transaction."""
        if self._conn is not None:
            self._conn.commit()

    def rollback(self) -> None:
        """Roll the open transaction back."""
        if self._conn is not None:
            self._conn.rollback()

    # -- backend-neutral lifecycle (Storage ABC) ------------------------
    def open(self) -> SqliteStorage[T]:
        """Connect + ensure the table (idempotent)."""
        self.connect()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Full-table scan as ``{key: payload}`` (bulk-only, not hot-path)."""
        conn = self._require_conn()
        rows = conn.execute(
            f"SELECT key, payload FROM {self._quoted_table()}"
        ).fetchall()
        return {row["key"]: json.loads(row["payload"]) for row in rows}

    def persist(self) -> None:
        """Commit staged writes."""
        self.commit()

    def close(self) -> None:
        """Release the connection *without* committing.

        The connection is shared with the other stores of the same
        database file: the last store to close it closes it for good.
        """
        if self._conn is not None:
            self._conn = None
            _release_connection(self._path)

    def __enter__(self) -> SqliteStorage[T]:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    # -- point access (Storage ABC) -------------------------------------
    def _require_conn(self) -> sqlite3.Connection:
        """The connection, opened on first use.

        The JSON backend loads its file on demand ✅ — the database
        backend opens its connection the same way, so a caller that
        forgot :meth:`open` still works.
        """
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    def _quoted_table(self) -> str:
        return f'"{self._table}"'

    def _empty_raw(self) -> Any:
        # Bucket rows serialise as bare lists, everything else as objects.
        return [] if self._factory is AssetBucket else {}

    def _ensure_table(self) -> None:
        conn = self._require_conn()
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
            "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        conn.commit()

    def get(self, key: str) -> T | None:
        """``SELECT payload FROM <table> WHERE key = ?``."""
        conn = self._require_conn()
        row = conn.execute(
            f"SELECT payload FROM {self._quoted_table()} WHERE key = ?",
            (str(key),),
        ).fetchone()
        if row is None:
            return None
        return self._factory.from_dict(str(key), json.loads(row["payload"]))

    def get_or_create(self, key: str) -> T:
        """``SELECT``, else ``INSERT`` an empty payload (ignoring a race)."""
        key = str(key)
        existing = self.get(key)
        if existing is not None:
            return existing
        obj = self._factory.from_dict(key, self._empty_raw())  # type: ignore[assignment]
        self._require_conn().execute(
            f"INSERT OR IGNORE INTO {self._quoted_table()} (key, payload) "
            "VALUES (?, ?)",
            (key, json.dumps(obj.to_dict())),
        )
        # The row may have been written by someone else meanwhile: the
        # stored one wins.
        return self.get(key) or obj

    def put(self, obj: T) -> None:
        """``INSERT INTO <table> (key, payload) VALUES (?, ?)``
        ``ON CONFLICT(key) DO UPDATE SET payload = excluded.payload``."""
        self._require_conn().execute(
            f"INSERT INTO {self._quoted_table()} (key, payload) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload",
            (obj.key, json.dumps(obj.to_dict())),
        )

    def add(self, obj: T) -> None:
        """Plain ``INSERT``; a duplicate surfaces as ``KeyError``."""
        try:
            self._require_conn().execute(
                f"INSERT INTO {self._quoted_table()} (key, payload) "
                "VALUES (?, ?)",
                (obj.key, json.dumps(obj.to_dict())),
            )
        except sqlite3.IntegrityError as exc:
            raise KeyError(f"row {obj.key!r} already exists") from exc

    def upsert(self, key: str, partial: dict[str, Any]) -> T:
        """Get-or-create ``key``, merge ``partial`` into it, return it."""
        obj = self.get_or_create(key)
        obj.merge(partial)
        self.put(obj)
        return obj

    def remove(self, key: str) -> None:
        """``DELETE FROM <table> WHERE key = ?`` (no error when missing)."""
        self._require_conn().execute(
            f"DELETE FROM {self._quoted_table()} WHERE key = ?", (str(key),)
        )

    def __contains__(self, key: object) -> bool:
        """``SELECT 1 FROM <table> WHERE key = ?``."""
        conn = self._require_conn()
        row = conn.execute(
            f"SELECT 1 FROM {self._quoted_table()} WHERE key = ?", (str(key),)
        ).fetchone()
        return row is not None
