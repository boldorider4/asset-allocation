# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite backend for the :mod:`storage.storage` abstractions.

One table per store, one row per key::

    CREATE TABLE <table> (key TEXT PRIMARY KEY, payload TEXT NOT NULL)

The payload is the row's JSON form (``to_dict()``), so a row written
by one backend reads back identically in the other.

SQLite runs the database *inside* the process: no server, no idle
load, nothing to supervise. WAL mode lets the web server read while
the update writes, and ``busy_timeout`` makes a writer-writer
collision wait a moment instead of failing.

One connection per database file, shared by every table of that
file, and **guarded by a lock**: the web server is multi-threaded
and Python's ``sqlite3`` module does *not* make a shared connection
thread-safe. Its prepared-statement cache lives on the connection,
and the throwaway cursor ``Connection.execute`` returns resets that
statement when it is garbage collected — so one thread could reset a
statement another thread was stepping ("bad parameter or other API
misuse", empty rows, a ``NULL`` payload where a row was expected).
The lock serializes the connection's use, which is what makes the
shared connection safe again.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, TypeVar

from storage.records import AssetBucket
from storage.storage import Storage, StorageObject

__all__ = ["SqliteStorage"]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=StorageObject)

#: How long a statement waits for a lock before giving up (seconds).
BUSY_TIMEOUT_SECONDS = 5.0

#: The open connections, one per database file, with the lock that
#: serializes their use.
#:
#: Every table of a database *shares* one connection: a second
#: connection would deadlock against the first one's write transaction
#: (SQLite's busy handler cannot break that deadlock, it only times
#: out). One connection per process, one transaction, no surprises.
#:
#: The lock is not optional: the web server is multi-threaded, and
#: Python's sqlite3 module does not serialize a *shared* connection
#: (see the module docstring). Every statement runs under it.
_CONNECTIONS: dict[str, tuple[sqlite3.Connection, int, threading.RLock]] = {}


#: The locks, one per database file (see :func:`_lock_for`).
_LOCKS: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    """The lock that serializes the use of a database file.

    One lock per file, so two different databases never wait for
    each other. Re-entrant: a store method may call another one
    (an upsert gets, merges and puts) without deadlocking itself.
    """
    key = str(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = threading.RLock()
        _LOCKS[key] = lock
    return lock


def _shared_connection(path: Path) -> sqlite3.Connection:
    """The connection for a database file (one per process)."""
    key = str(path)
    entry = _CONNECTIONS.get(key)
    if entry is not None:
        conn, refs, _lock = entry
        _CONNECTIONS[key] = (conn, refs + 1, _lock)
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_SECONDS,
        # Autocommit: every statement is its own transaction. A
        # long-lived read transaction would hold a *snapshot* of the
        # database, and a write on top of a stale snapshot fails
        # outright with SQLITE_BUSY_SNAPSHOT ("database is locked") —
        # the busy handler cannot break that one. That is what made
        # the server's writes (edits, sessions) fail next to the
        # update process. The context manager still groups statements
        # in an explicit transaction (see __enter__).
        isolation_level=None,
        # The web server is multi-threaded and the connection is shared
        # per process: the lock around every statement (not sqlite3)
        # serializes the access, so this is safe.
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # Readers keep working while a writer is active; a writer-writer
    # collision waits for the lock instead of failing outright.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
    conn.execute("PRAGMA synchronous=NORMAL")
    _CONNECTIONS[key] = (conn, 1, _lock_for(path))
    return conn


def _release_connection(path: Path) -> None:
    """Give back one reference; the last one closes the connection."""
    key = str(path)
    entry = _CONNECTIONS.get(key)
    if entry is None:
        return
    conn, refs, _lock = entry
    if refs > 1:
        _CONNECTIONS[key] = (conn, refs - 1, _lock)
        return
    del _CONNECTIONS[key]
    conn.close()


class SqliteStorage(Storage[T]):
    """Transactional SQLite store."""

    def __init__(
        self, path: str | Path, table: str, object_factory: type[T]
    ) -> None:
        self._path = Path(path).resolve()
        self._table = table
        self._factory: type[T] = object_factory
        self._conn: sqlite3.Connection | None = None
        # The lock that serializes this store's use of the file's
        # connection (one per file, shared by all its tables).
        self._lock = _lock_for(self._path)

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
        with self._lock:
            if self._conn is not None:
                return self
            self._conn = _shared_connection(self._path)
            self._ensure_table()
            return self

    def commit(self) -> None:
        """Commit the open transaction (a no-op without one)."""
        with self._lock:
            if self._conn is not None and self._conn.in_transaction:
                self._conn.commit()

    def rollback(self) -> None:
        """Roll the open transaction back (a no-op without one)."""
        with self._lock:
            if self._conn is not None and self._conn.in_transaction:
                self._conn.rollback()

    # -- backend-neutral lifecycle (Storage ABC) ------------------------
    def open(self) -> SqliteStorage[T]:
        """Connect + ensure the table (idempotent)."""
        self.connect()
        return self

    def snapshot(self) -> dict[str, Any]:
        """Full-table scan as ``{key: payload}`` (bulk-only, not hot-path).

        A broken row (no payload, or a payload that is not JSON) is
        reported and left out: the rest of the store is still usable.
        """
        with self._lock:
            conn = self._require_conn()
            # The positions table uses 'id' as primary key instead of 'key'
            # and has explicit columns instead of a payload JSON blob
            if self._table == "positions":
                rows = conn.execute(
                    f"SELECT id as key, username, bucket, position_index, name, short_name, "
                    "isin, value, shares, broker, dmem, dmem_other, usavn FROM "
                    f"{self._quoted_table()}"
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT key, payload FROM {self._quoted_table()}"
                ).fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            if self._table == "positions":
                # For positions table, create a dict from the explicit columns
                # row is a sqlite3.Row with columns: key, username, bucket, etc.
                out[row["key"]] = {
                    "username": row["username"],
                    "bucket": row["bucket"],
                    "position_index": row["position_index"],
                    "name": row["name"],
                    "short_name": row["short_name"],
                    "isin": row["isin"],
                    "value": row["value"],
                    "shares": row["shares"],
                    "broker": row["broker"],
                    "dmem": row["dmem"],
                    "dmem_other": row["dmem_other"],
                    "usavn": row["usavn"],
                }
            else:
                # For other tables, row has key and payload columns
                if row["payload"] is None:
                    logger.warning(
                        "row %r of table %r has no payload; leaving it out",
                        row["key"],
                        self._table,
                    )
                    continue
                try:
                    out[row["key"]] = json.loads(row["payload"])
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "row %r of table %r has an unreadable payload (%s); "
                        "leaving it out",
                        row["key"],
                        self._table,
                        exc,
                    )
        return out

    def persist(self) -> None:
        """Make the staged writes durable.

        Autocommit already wrote them; this only closes an explicit
        transaction (the context manager's).
        """
        self.commit()

    def close(self) -> None:
        """Release the connection.

        Nothing is lost: the connection is in autocommit mode, so the
        writes are already durable. The connection is shared with the
        other stores of the same database file: the last store to close
        it closes it for good.
        """
        with self._lock:
            if self._conn is not None:
                self._conn = None
                _release_connection(self._path)

    def __enter__(self) -> SqliteStorage[T]:
        # The lock is held for the whole block: the explicit
        # transaction below belongs to this thread alone, so no other
        # thread's statement can slip into it (nor reset the
        # connection's statement cache under it).
        self._lock.acquire()
        try:
            self.connect()
            # The context manager is the one place that groups statements:
            # an explicit transaction (autocommit is on, so it is ours).
            self._require_conn().execute("BEGIN")
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            conn = self._conn
            if conn is not None and conn.in_transaction:
                conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
            self.close()
        finally:
            self._lock.release()

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
        with self._lock:
            conn = self._require_conn()
            table = self._table
            if table == "user_assets":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            elif table == "user_portfolios":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            elif table == "positions":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "username TEXT NOT NULL, "
                    "bucket TEXT NOT NULL, "
                    "position_index INTEGER NOT NULL, "
                    "name TEXT NOT NULL, "
                    "short_name TEXT, "
                    "isin TEXT, "
                    "value REAL, "
                    "shares REAL, "
                    "broker TEXT, "
                    "dmem REAL, "
                    "dmem_other REAL, "
                    "usavn REAL, "
                    "UNIQUE(username, bucket, position_index))"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_positions_user_bucket "
                    "ON positions(username, bucket)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_positions_isin "
                    "ON positions(isin)"
                )
            elif table == "position_countries":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE, "
                    "name TEXT NOT NULL, "
                    "weight_pct REAL NOT NULL, "
                    "PRIMARY KEY (position_id, name))"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_position_countries_position "
                    "ON position_countries(position_id)"
                )
            elif table == "position_sectors":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE, "
                    "name TEXT NOT NULL, "
                    "weight_pct REAL NOT NULL, "
                    "PRIMARY KEY (position_id, name))"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_position_sectors_position "
                    "ON position_sectors(position_id)"
                )
            elif table == "cache":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            elif table == "isin_registry":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(isin TEXT PRIMARY KEY, issuer TEXT, bucket TEXT, product_ref TEXT)"
                )
            elif table == "users":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            elif table == "sessions":
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            else:
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                    "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            conn.commit()

    def _decode(self, key: str, payload: Any) -> T | None:
        """The row for a raw payload; None when the payload is unusable.

        A row whose payload is NULL (or is not JSON) is broken: it is
        reported and treated as *no row*, so one damaged row can never
        take a request down with a decoding error.
        """
        if payload is None:
            logger.warning(
                "row %r of table %r has no payload; treating it as missing",
                key,
                self._table,
            )
            return None
        try:
            return self._factory.from_dict(str(key), json.loads(payload))
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning(
                "row %r of table %r has an unreadable payload (%s); "
                "treating it as missing",
                key,
                self._table,
                exc,
            )
            return None

    def get(self, key: str) -> T | None:
        """``SELECT payload FROM <table> WHERE key = ?``."""
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                f"SELECT payload FROM {self._quoted_table()} WHERE key = ?",
                (str(key),),
            ).fetchone()
            if row is None:
                return None
            return self._decode(str(key), row["payload"])

    def get_or_create(self, key: str) -> T:
        """``SELECT``, else ``INSERT`` an empty payload (ignoring a race)."""
        with self._lock:
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
        with self._lock:
            # Handle tables with auto-incrementing primary keys (e.g., positions table)
            if self._table == "positions" and obj.key == "0":
                # New record: insert without ID, let SQLite auto-assign
                self._require_conn().execute(
                    f"""INSERT INTO {self._quoted_table()}
                    (username, bucket, position_index, name, short_name, isin, value, shares, broker, dmem, dmem_other, usavn)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        obj.username, obj.bucket, obj.position_index, obj.name,
                        obj.short_name, obj.isin, obj.value, obj.shares,
                        obj.broker, obj.dmem, obj.dmem_other, obj.usavn
                    ),
                )
                # Update object's key with the auto-assigned ID
                obj._key = str(self._require_conn().execute("SELECT last_insert_rowid()").fetchone()[0])
            # Handle isin_registry table which uses 'isin' column instead of 'key'
            elif self._table == "isin_registry":
                d = obj.to_dict()
                self._require_conn().execute(
                    f"INSERT INTO {self._quoted_table()} (isin, issuer, bucket, product_ref) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(isin) DO UPDATE SET issuer = excluded.issuer, "
                    "bucket = excluded.bucket, product_ref = excluded.product_ref",
                    (obj.key, d.get("issuer", ""), d.get("bucket", ""), d.get("product_ref", "")),
                )
            else:
                self._require_conn().execute(
                    f"INSERT INTO {self._quoted_table()} (key, payload) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload",
                    (obj.key, json.dumps(obj.to_dict())),
                )

    def add(self, obj: T) -> None:
        """Plain ``INSERT``; a duplicate surfaces as ``KeyError``."""
        with self._lock:
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
        with self._lock:
            obj = self.get_or_create(key)
            obj.merge(partial)
            self.put(obj)
            return obj

    def remove(self, key: str) -> None:
        """``DELETE FROM <table> WHERE key = ?`` (no error when missing)."""
        with self._lock:
            self._require_conn().execute(
                f"DELETE FROM {self._quoted_table()} WHERE key = ?", (str(key),)
            )

    def __contains__(self, key: object) -> bool:
        """``SELECT 1 FROM <table> WHERE key = ?``."""
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                f"SELECT 1 FROM {self._quoted_table()} WHERE key = ?", (str(key),)
            ).fetchone()
            return row is not None
