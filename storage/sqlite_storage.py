# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite backend for the :mod:`storage.storage` abstractions.

The application tables are normalized: ``user_positions`` has one row per
position, keyed by ``(username, bucket, position_idx)``, with typed columns
and an ISIN index; ``user_portfolios`` holds one portfolio name per user;
``users`` one password per account; ``cache`` one price/split row per ISIN;
``sessions`` one browser session per id. Only arbitrary/custom tables use
the generic layout::

    CREATE TABLE <table> (key TEXT PRIMARY KEY, payload TEXT NOT NULL)

The payload is the row's JSON form (``to_dict()``), so a row written by
one backend reads back identically in the other. A legacy ``(key, payload)``
application table is rejected unless the caller explicitly opts into the
one-time migration (see ``migrate_legacy``).

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

from storage.records import (
    DEFAULT_PORTFOLIO_NAME,
    AssetBucket,
    CacheEntry,
    PositionRecord,
    SessionRecord,
    UserPortfolioRecord,
    UserRecord,
)
from storage.storage import Storage, StorageObject

__all__ = ["SqliteStorage"]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=StorageObject)

#: Explicit-column tables with one primary key and a simple record mapping.
#:
#: ``user_positions`` is excluded because its key is composite and its rows
#: need a bespoke mapping; ``isin_registry`` keeps its existing path.
SINGLE_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "user_portfolios": ("username", "portfolio_name"),
    "users": ("username", "password"),
    "cache": ("isin", "price", "countries", "sectors"),
    "sessions": ("session_id", "username", "incognito", "expires_at"),
}

#: The primary-key column of every table in :data:`SINGLE_KEY_COLUMNS`.
PRIMARY_KEY_COLUMNS: dict[str, str] = {
    table: columns[0] for table, columns in SINGLE_KEY_COLUMNS.items()
}

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
        self,
        path: str | Path,
        table: str,
        object_factory: type[T],
        *,
        migrate_legacy: bool = False,
    ) -> None:
        self._path = Path(path).resolve()
        self._table = table
        self._factory: type[T] = object_factory
        self._migrate_legacy = migrate_legacy
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
            try:
                self._ensure_table()
            except BaseException:
                self._conn = None
                _release_connection(self._path)
                raise
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
            # user_positions has explicit columns and a composite primary
            # key instead of the generic (key, payload) layout.
            if self._table == "user_positions":
                rows = conn.execute(
                    f"SELECT {self._position_columns()} FROM {self._quoted_table()}"
                ).fetchall()
            elif self._table == "isin_registry":
                rows = conn.execute(
                    f"SELECT {self._isin_columns()} FROM {self._quoted_table()}"
                ).fetchall()
            elif self._table in SINGLE_KEY_COLUMNS:
                rows = conn.execute(
                    f"SELECT {self._normalized_columns()} "
                    f"FROM {self._quoted_table()}"
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT key, payload FROM {self._quoted_table()}"
                ).fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            if self._table == "user_positions":
                key = PositionRecord.encode_key(
                    row["username"], row["bucket"], row["position_idx"]
                )
                out[key] = self._position_row_to_dict(row)
            elif self._table == "isin_registry":
                out[str(row["isin"])] = self._isin_row_to_dict(row)
            elif self._table in SINGLE_KEY_COLUMNS:
                key = str(row[PRIMARY_KEY_COLUMNS[self._table]])
                try:
                    out[key] = self._normalized_row_to_dict(row)
                except (TypeError, ValueError, KeyError) as exc:
                    logger.warning(
                        "row %r of table %r is unreadable (%s); leaving it out",
                        key,
                        self._table,
                        exc,
                    )
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

        The connection opens on demand, so a caller that forgot
        :meth:`open` still works.
        """
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    @staticmethod
    def _isin_columns() -> str:
        return "isin, issuer, bucket, product_ref"

    @staticmethod
    def _isin_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "issuer": row["issuer"],
            "bucket": row["bucket"],
            "product_ref": row["product_ref"],
        }

    def _quoted_table(self) -> str:
        return f'"{self._table}"'

    @staticmethod
    def _position_columns() -> str:
        return (
            "username, bucket, position_idx, name, short_name, isin, "
            "value, shares, broker, dmem, usavn, dmem_other"
        )

    @staticmethod
    def _position_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "username": row["username"],
            "bucket": row["bucket"],
            "position_idx": row["position_idx"],
            "name": row["name"],
            "short_name": row["short_name"],
            "isin": row["isin"],
            "value": row["value"],
            "shares": row["shares"],
            "broker": row["broker"],
            "dmem": row["dmem"],
            "usavn": row["usavn"],
            "dmem_other": row["dmem_other"],
        }

    def _normalized_columns(self) -> str:
        return ", ".join(SINGLE_KEY_COLUMNS[self._table])

    def _normalized_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert one explicit-column row to the record's JSON payload."""
        table = self._table
        if table == "user_portfolios":
            name = row["portfolio_name"]
            return {
                UserPortfolioRecord.PORTFOLIO_NAME: (
                    DEFAULT_PORTFOLIO_NAME if name is None else str(name)
                )
            }
        if table == "users":
            password = row["password"]
            return {UserRecord.PASSWORD: "" if password is None else str(password)}
        if table == "cache":
            payload: dict[str, Any] = {}
            if row["price"] is not None:
                payload[CacheEntry.PRICE] = row["price"]
            for field in (CacheEntry.COUNTRIES, CacheEntry.SECTORS):
                raw = row[field]
                if raw is not None:
                    payload[field] = json.loads(raw)
            return payload
        # sessions
        return {
            SessionRecord.USER: str(row["username"] or ""),
            SessionRecord.INCOGNITO: bool(row["incognito"]),
            SessionRecord.EXPIRES_AT: float(row["expires_at"] or 0.0),
        }

    @staticmethod
    def _json_or_none(value: Any) -> str | None:
        return None if value is None else json.dumps(value)

    def _normalized_values(self, obj: T) -> tuple[Any, ...]:
        """The explicit-column values for one record, in schema order."""
        table = self._table
        data = obj.to_dict()
        if table == "user_portfolios":
            return (
                obj.key,
                data.get(
                    UserPortfolioRecord.PORTFOLIO_NAME, DEFAULT_PORTFOLIO_NAME
                ),
            )
        if table == "users":
            return (obj.key, data.get(UserRecord.PASSWORD, ""))
        if table == "cache":
            return (
                obj.key,
                data.get(CacheEntry.PRICE),
                self._json_or_none(data.get(CacheEntry.COUNTRIES)),
                self._json_or_none(data.get(CacheEntry.SECTORS)),
            )
        # sessions
        return (
            obj.key,
            data.get(SessionRecord.USER, ""),
            int(bool(data.get(SessionRecord.INCOGNITO))),
            float(data.get(SessionRecord.EXPIRES_AT) or 0.0),
        )

    def _normalized_insert_sql(self) -> str:
        columns = SINGLE_KEY_COLUMNS[self._table]
        placeholders = ", ".join("?" for _ in columns)
        return (
            f"INSERT INTO {self._quoted_table()} ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )

    def _normalized_upsert_sql(self) -> str:
        columns = SINGLE_KEY_COLUMNS[self._table]
        primary_key = PRIMARY_KEY_COLUMNS[self._table]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in columns
            if column != primary_key
        )
        return (
            f"INSERT INTO {self._quoted_table()} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT({primary_key}) DO UPDATE SET {updates}"
        )

    def _table_columns(self) -> set[str]:
        rows = self._require_conn().execute(
            f"PRAGMA table_info({self._quoted_table()})"
        ).fetchall()
        return {str(row[1]) for row in rows}

    def _upgrade_legacy_table(self) -> None:
        """Rebuild a legacy ``(key, payload)`` application table in place."""
        table = self._table
        if table not in SINGLE_KEY_COLUMNS or "payload" not in self._table_columns():
            return
        conn = self._require_conn()
        logger.info("upgrading legacy table %r to explicit columns", table)
        conn.execute("SAVEPOINT legacy_upgrade")
        try:
            rows = conn.execute(
                f"SELECT key, payload FROM {self._quoted_table()}"
            ).fetchall()
            converted: list[tuple[Any, ...]] = []
            for row in rows:
                key = str(row["key"])
                raw = row["payload"]
                if raw is None:
                    logger.warning(
                        "legacy row %r of table %r has no payload; dropping it",
                        key,
                        table,
                    )
                    continue
                try:
                    obj = self._factory.from_dict(key, json.loads(raw))
                except (TypeError, ValueError, KeyError) as exc:
                    logger.warning(
                        "legacy row %r of table %r is unreadable (%s); dropping it",
                        key,
                        table,
                        exc,
                    )
                    continue
                converted.append(self._normalized_values(obj))

            temporary = self._quoted_identifier(f"{table}__legacy")
            conn.execute(
                f"ALTER TABLE {self._quoted_table()} RENAME TO {temporary}"
            )
            self._create_table(conn)
            insert_sql = self._normalized_insert_sql()
            for values in converted:
                conn.execute(insert_sql, values)
            conn.execute(f"DROP TABLE {temporary}")
            conn.execute("RELEASE legacy_upgrade")
        except BaseException:
            conn.execute("ROLLBACK TO legacy_upgrade")
            conn.execute("RELEASE legacy_upgrade")
            raise

    @staticmethod
    def _quoted_identifier(name: str) -> str:
        return f'"{name}"'

    def _empty_raw(self) -> Any:
        # Bucket rows serialise as bare lists, everything else as objects.
        return [] if self._factory is AssetBucket else {}

    def _create_table(self, conn: sqlite3.Connection) -> None:
        table = self._table
        if table == "user_positions":
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                "(username TEXT NOT NULL, "
                "bucket TEXT NOT NULL, "
                "position_idx INTEGER NOT NULL, "
                "name TEXT NOT NULL, "
                "short_name TEXT, "
                "isin TEXT, "
                "value REAL, "
                "shares REAL, "
                "broker TEXT, "
                "dmem REAL, "
                "usavn REAL, "
                "dmem_other REAL, "
                "PRIMARY KEY (username, bucket, position_idx))"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_positions_isin "
                "ON user_positions(isin)"
            )
        elif table == "user_portfolios":
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                "(username TEXT PRIMARY KEY, portfolio_name TEXT NOT NULL)"
            )
        elif table == "cache":
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                "(isin TEXT PRIMARY KEY, price REAL, countries TEXT, sectors TEXT)"
            )
        elif table == "isin_registry":
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                "(isin TEXT PRIMARY KEY, issuer TEXT, bucket TEXT, product_ref TEXT)"
            )
        elif table == "users":
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                "(username TEXT PRIMARY KEY, password TEXT NOT NULL DEFAULT '')"
            )
        elif table == "sessions":
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                "(session_id TEXT PRIMARY KEY, "
                "username TEXT NOT NULL DEFAULT '', "
                "incognito INTEGER NOT NULL DEFAULT 0, "
                "expires_at REAL NOT NULL DEFAULT 0)"
            )
        else:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._quoted_table()} "
                "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _ensure_table(self) -> None:
        with self._lock:
            conn = self._require_conn()
            self._create_table(conn)
            self._ensure_supported_schema()
            conn.commit()

    def _ensure_supported_schema(self) -> None:
        """Reject legacy application tables unless migration is requested."""
        if self._table not in SINGLE_KEY_COLUMNS:
            return
        actual = self._table_columns()
        if "payload" in actual:
            if self._migrate_legacy:
                self._upgrade_legacy_table()
                return
            raise ValueError(
                f"table {self._table!r} has a legacy (key, payload) schema; "
                "run scripts/migrate_normalize_db.py before opening it"
            )
        missing = set(SINGLE_KEY_COLUMNS[self._table]) - actual
        if missing:
            raise ValueError(
                f"table {self._table!r} is missing columns: "
                f"{', '.join(sorted(missing))}"
            )

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
        """``SELECT`` one row by its (possibly composite) key."""
        with self._lock:
            conn = self._require_conn()
            if self._table == "user_positions":
                username, bucket, index = PositionRecord.decode_key(key)
                row = conn.execute(
                    f"SELECT {self._position_columns()} FROM {self._quoted_table()} "
                    "WHERE username = ? AND bucket = ? AND position_idx = ?",
                    (username, bucket, index),
                ).fetchone()
                if row is None:
                    return None
                return self._factory.from_dict(  # type: ignore[return-value]
                    str(key), self._position_row_to_dict(row)
                )
            if self._table == "isin_registry":
                row = conn.execute(
                    f"SELECT {self._isin_columns()} FROM {self._quoted_table()} "
                    "WHERE isin = ?",
                    (str(key),),
                ).fetchone()
                if row is None:
                    return None
                return self._factory.from_dict(  # type: ignore[return-value]
                    str(key), self._isin_row_to_dict(row)
                )
            if self._table in SINGLE_KEY_COLUMNS:
                primary_key = PRIMARY_KEY_COLUMNS[self._table]
                row = conn.execute(
                    f"SELECT {self._normalized_columns()} "
                    f"FROM {self._quoted_table()} WHERE {primary_key} = ?",
                    (str(key),),
                ).fetchone()
                if row is None:
                    return None
                try:
                    return self._factory.from_dict(  # type: ignore[return-value]
                        str(key), self._normalized_row_to_dict(row)
                    )
                except (TypeError, ValueError, KeyError) as exc:
                    logger.warning(
                        "row %r of table %r is unreadable (%s); treating it "
                        "as missing",
                        key,
                        self._table,
                        exc,
                    )
                    return None
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
            if self._table == "user_positions":
                # A position row is meaningless without its fields.
                raise KeyError(f"position row {key!r} does not exist")
            if self._table == "isin_registry":
                obj = self._factory.from_dict(key, {})  # type: ignore[assignment]
                self._require_conn().execute(
                    f"INSERT OR IGNORE INTO {self._quoted_table()} "
                    "(isin, issuer, bucket, product_ref) VALUES (?, NULL, NULL, NULL)",
                    (key,),
                )
                return self.get(key) or obj
            if self._table in SINGLE_KEY_COLUMNS:
                obj = self._factory.from_dict(  # type: ignore[assignment]
                    key, self._empty_raw()
                )
                columns = SINGLE_KEY_COLUMNS[self._table]
                placeholders = ", ".join("?" for _ in columns)
                self._require_conn().execute(
                    f"INSERT OR IGNORE INTO {self._quoted_table()} "
                    f"({', '.join(columns)}) VALUES ({placeholders})",
                    self._normalized_values(obj),
                )
                return self.get(key) or obj
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
        """Insert or replace one row (upsert on the table's primary key)."""
        with self._lock:
            if self._table == "user_positions":
                d = obj.to_dict()
                self._require_conn().execute(
                    f"INSERT INTO {self._quoted_table()} "
                    "(username, bucket, position_idx, name, short_name, isin, "
                    "value, shares, broker, dmem, usavn, dmem_other) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(username, bucket, position_idx) DO UPDATE SET "
                    "name = excluded.name, short_name = excluded.short_name, "
                    "isin = excluded.isin, value = excluded.value, "
                    "shares = excluded.shares, broker = excluded.broker, "
                    "dmem = excluded.dmem, usavn = excluded.usavn, "
                    "dmem_other = excluded.dmem_other",
                    (
                        d.get("username"),
                        d.get("bucket"),
                        d.get("position_idx"),
                        d.get("name", ""),
                        d.get("short_name"),
                        d.get("isin"),
                        d.get("value"),
                        d.get("shares"),
                        d.get("broker"),
                        d.get("dmem"),
                        d.get("usavn"),
                        d.get("dmem_other"),
                    ),
                )
            # Handle isin_registry table which uses 'isin' column instead of 'key'
            elif self._table == "isin_registry":
                d = obj.to_dict()
                self._require_conn().execute(
                    f"INSERT INTO {self._quoted_table()} (isin, issuer, bucket, product_ref) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(isin) DO UPDATE SET issuer = excluded.issuer, "
                    "bucket = excluded.bucket, product_ref = excluded.product_ref",
                    (obj.key, d.get("issuer"), d.get("bucket"), d.get("product_ref")),
                )
            elif self._table in SINGLE_KEY_COLUMNS:
                self._require_conn().execute(
                    self._normalized_upsert_sql(), self._normalized_values(obj)
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
            if self._table == "user_positions":
                if obj.key in self:
                    raise KeyError(f"row {obj.key!r} already exists")
                self.put(obj)
                return
            if self._table == "isin_registry":
                if obj.key in self:
                    raise KeyError(f"row {obj.key!r} already exists")
                self.put(obj)
                return
            if self._table in SINGLE_KEY_COLUMNS:
                if obj.key in self:
                    raise KeyError(f"row {obj.key!r} already exists")
                self.put(obj)
                return
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
        """Delete one row by key (no error when missing)."""
        with self._lock:
            conn = self._require_conn()
            if self._table == "user_positions":
                username, bucket, index = PositionRecord.decode_key(key)
                conn.execute(
                    f"DELETE FROM {self._quoted_table()} "
                    "WHERE username = ? AND bucket = ? AND position_idx = ?",
                    (username, bucket, index),
                )
                return
            if self._table == "isin_registry":
                conn.execute(
                    f"DELETE FROM {self._quoted_table()} WHERE isin = ?", (str(key),)
                )
                return
            if self._table in SINGLE_KEY_COLUMNS:
                primary_key = PRIMARY_KEY_COLUMNS[self._table]
                conn.execute(
                    f"DELETE FROM {self._quoted_table()} WHERE {primary_key} = ?",
                    (str(key),),
                )
                return
            conn.execute(
                f"DELETE FROM {self._quoted_table()} WHERE key = ?", (str(key),)
            )

    def __contains__(self, key: object) -> bool:
        """True when the row exists."""
        with self._lock:
            conn = self._require_conn()
            if self._table == "user_positions":
                username, bucket, index = PositionRecord.decode_key(str(key))
                row = conn.execute(
                    f"SELECT 1 FROM {self._quoted_table()} "
                    "WHERE username = ? AND bucket = ? AND position_idx = ?",
                    (username, bucket, index),
                ).fetchone()
                return row is not None
            if self._table == "isin_registry":
                row = conn.execute(
                    f"SELECT 1 FROM {self._quoted_table()} WHERE isin = ?",
                    (str(key),),
                ).fetchone()
                return row is not None
            if self._table in SINGLE_KEY_COLUMNS:
                primary_key = PRIMARY_KEY_COLUMNS[self._table]
                row = conn.execute(
                    f"SELECT 1 FROM {self._quoted_table()} "
                    f"WHERE {primary_key} = ?",
                    (str(key),),
                ).fetchone()
                return row is not None
            row = conn.execute(
                f"SELECT 1 FROM {self._quoted_table()} WHERE key = ?", (str(key),)
            ).fetchone()
            return row is not None
