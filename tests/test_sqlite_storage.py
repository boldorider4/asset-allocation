# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SQLite backend: the storage contract, on a real database.

The backend is the file-backed twin of the JSON backend: the same
class shape, the same method names, the same signatures.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from storage.records import (
    CacheEntry,
    IsinRecord,
    PositionRecord,
    SessionRecord,
    UserPortfolioRecord,
    UserRecord,
)
from storage.sqlite_storage import SqliteStorage


def _store(path: Path, table: str = "rows", factory=UserRecord) -> SqliteStorage:
    return SqliteStorage(path, table, factory)  # type: ignore[arg-type]


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }


class TestIsinRegistryBackend(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.path = Path(self._holder.name) / "store.db"
        self.store = _store(self.path, "isin_registry", IsinRecord)
        self.store.open()

    def test_point_access_uses_the_isin_column(self) -> None:
        self.store.put(
            IsinRecord(
                "IE00TEST0001",
                issuer="justetf",
                bucket="equity_portfolio",
                product_ref="prod",
            )
        )
        record = self.store.get("IE00TEST0001")
        assert record is not None
        self.assertEqual(record.issuer, "justetf")
        self.assertEqual(record.bucket, "equity_portfolio")
        self.assertEqual(record.product_ref, "prod")
        self.assertIn("IE00TEST0001", self.store)

    def test_snapshot_returns_the_registry_payload(self) -> None:
        self.store.put(
            IsinRecord("IE00TEST0002", issuer="yfinance", bucket="bond_portfolio")
        )
        self.assertEqual(
            self.store.snapshot(),
            {
                "IE00TEST0002": {
                    "issuer": "yfinance",
                    "bucket": "bond_portfolio",
                    "product_ref": None,
                }
            },
        )

    def test_get_or_create_add_and_remove(self) -> None:
        created = self.store.get_or_create("IE00TEST0003")
        self.assertEqual(created.issuer, None)
        self.assertIn("IE00TEST0003", self.store)
        self.store.add(IsinRecord("IE00TEST0004", issuer="justetf"))
        with self.assertRaises(KeyError):
            self.store.add(IsinRecord("IE00TEST0004", issuer="justetf"))
        self.store.remove("IE00TEST0004")
        self.assertNotIn("IE00TEST0004", self.store)


class TestNormalizedApplicationTables(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.path = Path(self._holder.name) / "store.db"

    def test_user_portfolios_use_explicit_columns(self) -> None:
        store = _store(self.path, "user_portfolios", UserPortfolioRecord)
        store.open()
        created = store.get_or_create("alice")
        self.assertEqual(created.portfolio_name, "Portfolio")
        store.upsert("alice", {"portfolio_name": "Alice Fund"})
        self.assertEqual(
            store.snapshot(), {"alice": {"portfolio_name": "Alice Fund"}}
        )
        self.assertEqual(
            _columns(self.path, "user_portfolios"),
            {"username", "portfolio_name"},
        )
        store.remove("alice")
        self.assertNotIn("alice", store)

    def test_users_use_explicit_columns(self) -> None:
        store = _store(self.path, "users", UserRecord)
        store.open()
        created = store.get_or_create("alice")
        self.assertEqual(created.password, "")
        store.add(UserRecord("bob", {"password": "first"}))
        with self.assertRaises(KeyError):
            store.add(UserRecord("bob", {"password": "second"}))
        store.upsert("bob", {"password": "pw"})
        self.assertEqual(
            store.snapshot(),
            {"alice": {"password": ""}, "bob": {"password": "pw"}},
        )
        self.assertEqual(
            _columns(self.path, "users"), {"username", "password"}
        )

    def test_cache_uses_explicit_columns(self) -> None:
        store = _store(self.path, "cache", CacheEntry)
        store.open()
        store.upsert(
            "IE00TEST0001",
            {
                "price": 1.5,
                "countries": {"France": 0.5},
                "sectors": {"Technology": 1.0},
            },
        )
        entry = store.get("IE00TEST0001")
        assert entry is not None
        self.assertEqual(entry.parsed(), (1.5, {"France": 0.5}, {"Technology": 1.0}))
        self.assertEqual(
            store.snapshot(),
            {
                "IE00TEST0001": {
                    "price": 1.5,
                    "countries": {"France": 0.5},
                    "sectors": {"Technology": 1.0},
                }
            },
        )
        self.assertEqual(
            _columns(self.path, "cache"),
            {"isin", "price", "countries", "sectors"},
        )

    def test_sessions_use_explicit_columns(self) -> None:
        store = _store(self.path, "sessions", SessionRecord)
        store.open()
        created = store.get_or_create("sid")
        self.assertEqual(created.user, "")
        self.assertEqual(created.expires_at, 0.0)
        store.put(
            SessionRecord(
                "sid",
                {"user": "alice", "incognito": True, "expires_at": 123.0},
            )
        )
        self.assertEqual(
            store.snapshot(),
            {
                "sid": {
                    "user": "alice",
                    "incognito": True,
                    "expires_at": 123.0,
                }
            },
        )
        self.assertEqual(
            _columns(self.path, "sessions"),
            {"session_id", "username", "incognito", "expires_at"},
        )
        store.remove("sid")
        self.assertNotIn("sid", store)

    def test_legacy_payload_tables_are_upgraded(self) -> None:
        payloads = {
            "user_portfolios": {"portfolio_name": "Alice Fund"},
            "users": {"password": "pw"},
            "cache": {
                "price": 2.0,
                "countries": {"France": 0.5},
                "sectors": {"Technology": 1.0},
            },
            "sessions": {
                "user": "alice",
                "incognito": True,
                "expires_at": 123.0,
            },
        }
        with sqlite3.connect(self.path) as conn:
            for table, payload in payloads.items():
                conn.execute(
                    f"CREATE TABLE {table} (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                conn.execute(
                    f"INSERT INTO {table} VALUES (?, ?)",
                    (f"{table}-key", json.dumps(payload)),
                )

        expected = {
            "user_portfolios": ("user_portfolios-key", UserPortfolioRecord),
            "users": ("users-key", UserRecord),
            "cache": ("cache-key", CacheEntry),
            "sessions": ("sessions-key", SessionRecord),
        }
        for table, (key, factory) in expected.items():
            with self.subTest(table=table):
                store = _store(self.path, table, factory)
                store.open()
                self.assertIn(key, store)
                self.assertNotIn("payload", _columns(self.path, table))
                store.close()
        cache = _store(self.path, "cache", CacheEntry)
        cache.open()
        try:
            snapshot = cache.snapshot()
        finally:
            cache.close()
        self.assertEqual(
            snapshot,
            {
                "cache-key": {
                    "price": 2.0,
                    "countries": {"France": 0.5},
                    "sectors": {"Technology": 1.0},
                }
            },
        )


class TestPointAccess(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.path = Path(self._holder.name) / "store.db"
        self.store = _store(self.path)
        self.store.open()

    def test_get_returns_none_for_a_missing_key(self) -> None:
        self.assertIsNone(self.store.get("nope"))

    def test_put_then_get_round_trips(self) -> None:
        self.store.put(UserRecord("alice", {"password": "x"}))
        record = self.store.get("alice")
        assert record is not None
        self.assertEqual(record.password, "x")

    def test_put_replaces_the_row(self) -> None:
        self.store.put(UserRecord("alice", {"password": "x"}))
        self.store.put(UserRecord("alice", {"password": "y"}))
        record = self.store.get("alice")
        assert record is not None
        self.assertEqual(record.password, "y")

    def test_add_inserts_and_refuses_duplicates(self) -> None:
        self.store.add(UserRecord("alice", {"password": "x"}))
        with self.assertRaises(KeyError):
            self.store.add(UserRecord("alice", {"password": "y"}))

    def test_get_or_create_makes_an_empty_row(self) -> None:
        record = self.store.get_or_create("alice")
        self.assertEqual(record.key, "alice")
        self.assertEqual(record.password, "")
        # ...and it is really there.
        self.assertIn("alice", self.store)

    def test_upsert_merges_a_partial(self) -> None:
        self.store.upsert("alice", {"password": "x"})
        self.store.upsert("alice", {"password": "y"})
        record = self.store.get("alice")
        assert record is not None
        self.assertEqual(record.password, "y")

    def test_remove_deletes_the_row(self) -> None:
        self.store.put(UserRecord("alice", {"password": "x"}))
        self.store.remove("alice")
        self.assertIsNone(self.store.get("alice"))
        # Removing a missing row is fine.
        self.store.remove("alice")

    def test_contains(self) -> None:
        self.store.put(UserRecord("alice", {"password": "x"}))
        self.assertIn("alice", self.store)
        self.assertNotIn("bob", self.store)

    def test_snapshot_returns_the_whole_store(self) -> None:
        self.store.put(UserRecord("alice", {"password": "x"}))
        self.store.put(UserRecord("bob", {"password": ""}))
        self.assertEqual(
            self.store.snapshot(),
            {"alice": {"password": "x"}, "bob": {"password": ""}},
        )

    def test_a_malformed_row_raises(self) -> None:
        # The rows are strict: a bad row is an error, not a silent skip.
        with self.assertRaises(KeyError):
            self.store.put(UserRecord("alice", {"nope": 1}))


class TestLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.path = Path(self._holder.name) / "store.db"

    def test_open_creates_the_file_and_the_table(self) -> None:
        store = _store(self.path)
        store.open()
        self.assertTrue(self.path.is_file())
        self.assertEqual(store.table, "rows")
        conn = sqlite3.connect(self.path)
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        self.assertIn("rows", tables)

    def test_open_is_idempotent(self) -> None:
        store = _store(self.path)
        store.open()
        store.open()
        self.assertTrue(store.connected)

    def test_the_stores_of_one_database_share_a_connection(self) -> None:
        # Two connections to one file would deadlock against each
        # other's write transaction: the tables share one connection.
        first = _store(self.path, "a")
        second = _store(self.path, "b")
        first.open()
        second.open()
        first.put(UserRecord("alice", {"password": "x"}))
        second.put(UserRecord("bob", {"password": "y"}))
        first.persist()
        self.assertEqual(first.get("alice").password, "x")  # type: ignore[union-attr]
        self.assertEqual(second.get("bob").password, "y")  # type: ignore[union-attr]

    def test_persist_commits(self) -> None:
        store = _store(self.path)
        store.open()
        store.put(UserRecord("alice", {"password": "x"}))
        store.persist()
        # A fresh connection sees the committed row.
        other = _store(self.path)
        other.open()
        self.assertIsNotNone(other.get("alice"))

    def test_a_write_is_durable_without_an_explicit_commit(self) -> None:
        # Autocommit: the write is durable on its own (the Postgres
        # stub would keep it in its transaction until commit).
        store = _store(self.path)
        store.open()
        store.put(UserRecord("alice", {"password": "x"}))
        store.close()
        self.assertFalse(store.connected)
        other = _store(self.path)
        other.open()
        self.assertIsNotNone(other.get("alice"))

    def test_the_context_manager_commits(self) -> None:
        with _store(self.path) as store:
            store.put(UserRecord("alice", {"password": "x"}))
        other = _store(self.path)
        other.open()
        self.assertIsNotNone(other.get("alice"))

    def test_the_context_manager_rolls_back_on_error(self) -> None:
        with self.assertRaises(RuntimeError):
            with _store(self.path) as store:
                store.put(UserRecord("alice", {"password": "x"}))
                raise RuntimeError("boom")
        other = _store(self.path)
        other.open()
        self.assertIsNone(other.get("alice"))

    def test_the_cache_table_has_explicit_columns(self) -> None:
        store = _store(self.path, "cache", CacheEntry)
        store.open()
        store.put(
            CacheEntry(
                "IE00A",
                {"price": 1.5, "countries": {"France": 0.5}},
            )
        )
        store.persist()
        conn = sqlite3.connect(self.path)
        row = conn.execute(
            "SELECT isin, price, countries, sectors FROM cache"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "IE00A")
        self.assertEqual(row[1], 1.5)
        self.assertEqual(json.loads(row[2]), {"France": 0.5})
        self.assertIsNone(row[3])

    def test_a_position_row_round_trips_on_the_composite_key(self) -> None:
        store = _store(self.path, "user_positions", PositionRecord)
        store.open()
        row = PositionRecord(
            username="alice",
            bucket="equity_portfolio",
            position_idx=0,
            name="ETF",
            isin="IE00TEST0001",
            value=1.0,
        )
        store.put(row)
        store.persist()

        self.assertIn(row.key, store)
        record = store.get(row.key)
        assert record is not None
        self.assertEqual(record.username, "alice")
        self.assertEqual(record.bucket, "equity_portfolio")
        self.assertEqual(record.position_idx, 0)
        self.assertEqual(record.isin, "IE00TEST0001")

        # Upsert on the same composite key updates the row.
        store.put(
            PositionRecord(
                username="alice",
                bucket="equity_portfolio",
                position_idx=0,
                name="ETF renamed",
                isin="IE00TEST0001",
                value=2.0,
            )
        )
        record = store.get(row.key)
        assert record is not None
        self.assertEqual(record.name, "ETF renamed")
        self.assertEqual(record.value, 2.0)
        self.assertEqual(len(store.snapshot()), 1)

        store.remove(row.key)
        self.assertNotIn(row.key, store)
        self.assertIsNone(store.get(row.key))


class TestConcurrency(unittest.TestCase):
    """A write must not be poisoned by a stale read snapshot.

    A long-lived read transaction holds a *snapshot* of the database;
    a write on top of a snapshot that another connection has moved past
    fails outright with SQLITE_BUSY_SNAPSHOT ("database is locked") —
    the busy handler cannot break that one. That is what made the
    server's writes (edits, sessions) fail next to the update process.
    """

    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.path = Path(self._holder.name) / "store.db"

    def _other_connection(self) -> sqlite3.Connection:
        """A second connection to the same file (another process)."""
        conn = sqlite3.connect(self.path, timeout=5.0)
        self.addCleanup(conn.close)
        return conn

    def test_a_write_after_another_connection_committed(self) -> None:
        store = _store(self.path)
        store.open()
        store.get("nope")  # the read that would freeze a snapshot
        other = self._other_connection()
        other.execute(
            "CREATE TABLE IF NOT EXISTS rows "
            "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        other.execute("INSERT OR REPLACE INTO rows VALUES ('x', '{}')")
        other.commit()
        # The write on the stale snapshot must still work.
        store.put(UserRecord("alice", {"password": "x"}))
        self.assertIsNotNone(store.get("alice"))

    def test_a_write_while_another_process_writes(self) -> None:
        store = _store(self.path)
        store.open()
        store.get("nope")
        # Another process writes and commits (the update worker).
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sqlite3, sys;"
                "conn = sqlite3.connect(sys.argv[1], timeout=5);"
                "conn.execute('CREATE TABLE IF NOT EXISTS rows "
                "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)');"
                "conn.execute(\"INSERT OR REPLACE INTO rows VALUES ('y', '{}')\");"
                "conn.commit()",
                str(self.path),
            ],
            check=True,
        )
        store.put(UserRecord("alice", {"password": "x"}))
        self.assertIsNotNone(store.get("alice"))

    def test_a_write_is_visible_to_another_connection(self) -> None:
        store = _store(self.path)
        store.open()
        store.put(UserRecord("alice", {"password": "x"}))
        other = self._other_connection()
        row = other.execute(
            "SELECT payload FROM rows WHERE key = 'alice'"
        ).fetchone()
        self.assertIsNotNone(row)


class TestConcurrentUse(unittest.TestCase):
    """The shared connection must survive the server's request threads.

    The web server is multi-threaded and every table of a database
    shares one connection. Python's ``sqlite3`` does not make that safe
    on its own: its prepared-statement cache lives on the connection,
    and the throwaway cursor ``Connection.execute`` returns resets that
    statement when it is garbage collected - so one thread could reset
    a statement another thread was stepping ("bad parameter or other
    API misuse", empty rows, a ``NULL`` payload where a row was
    expected). The backend serializes the connection's use with a
    lock; this is the test that says it does.
    """

    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.path = Path(self._holder.name) / "store.db"
        store = _store(self.path)
        store.open()
        store.put(UserRecord("seed", {"password": "x"}))
        store.close()

    def test_readers_and_writers_do_not_corrupt_each_other(self) -> None:
        import re
        import threading

        errors: list[BaseException] = []
        stop = threading.Event()
        # The row the readers watch: the writers replace it over and
        # over, so a reader must always see one of those two shapes -
        # never a torn or empty one.
        valid = re.compile(r"^(x|p\d+)$")

        def reader() -> None:
            store = _store(self.path)
            store.open()
            try:
                while not stop.is_set():
                    try:
                        record = store.get("seed")
                        if record is None or not valid.match(record.password):
                            errors.append(
                                AssertionError(f"row read back wrong: {record!r}")
                            )
                    except BaseException as exc:  # noqa: BLE001 - reported below
                        errors.append(exc)
                        return
            finally:
                store.close()

        def writer() -> None:
            store = _store(self.path)
            store.open()
            try:
                count = 0
                while not stop.is_set():
                    count += 1
                    try:
                        store.put(UserRecord("seed", {"password": f"p{count}"}))
                    except BaseException as exc:  # noqa: BLE001 - reported below
                        errors.append(exc)
                        return
            finally:
                store.close()

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        stop.wait(3.0)
        stop.set()
        for thread in threads:
            thread.join(5.0)
        self.assertEqual(errors, [], f"{len(errors)} error(s) under concurrency")
        # The store is still usable and holds whole rows.
        store = _store(self.path)
        store.open()
        record = store.get("seed")
        assert record is not None
        self.assertTrue(valid.match(record.password))

    def test_a_row_without_a_payload_reads_as_missing(self) -> None:
        # A damaged row must not take a request down with a decoding
        # error: it reads as *no row* and is reported.
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("DROP TABLE rows")
            conn.execute("CREATE TABLE rows (key TEXT PRIMARY KEY, payload TEXT)")
            conn.execute("INSERT INTO rows (key, payload) VALUES ('broken', NULL)")
            conn.commit()
        finally:
            conn.close()
        store = _store(self.path)
        store.open()
        self.assertIsNone(store.get("broken"))
        # ...and the rest of the store still works.
        store.put(UserRecord("alice", {"password": "x"}))
        self.assertIsNotNone(store.get("alice"))


if __name__ == "__main__":
    unittest.main()
