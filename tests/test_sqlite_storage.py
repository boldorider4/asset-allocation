# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SQLite backend: the storage contract, on a real database.

The backend is the file-backed twin of the Postgres stub: the same
class shape, the same methods, the same signatures. The parity test
below keeps the two from drifting apart.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from storage.postgres_storage import PostgresStorage
from storage.records import CacheEntry, UserAssets, UserRecord
from storage.sqlite_storage import SqliteStorage


def _store(path: Path, table: str = "rows", factory=UserRecord) -> SqliteStorage:
    return SqliteStorage(path, table, factory)  # type: ignore[arg-type]


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

    def test_the_database_file_is_json(self) -> None:
        # The payload is the row's JSON form: the file stays readable.
        store = _store(self.path, "cache", CacheEntry)
        store.open()
        store.put(CacheEntry("IE00A", {"price": 1.5}))
        store.persist()
        conn = sqlite3.connect(self.path)
        payload = conn.execute("SELECT payload FROM cache").fetchone()[0]
        self.assertEqual(json.loads(payload), {"price": 1.5})

    def test_an_asset_row_round_trips(self) -> None:
        store = _store(self.path, "user_assets", UserAssets)
        store.open()
        store.put(
            UserAssets(
                "alice",
                {"portfolio_name": "Fund", "positions": {"equity_portfolio": []}},
            )
        )
        store.persist()
        record = store.get("alice")
        assert record is not None
        self.assertEqual(record.portfolio_name, "Fund")
        self.assertEqual(record.positions(), {"equity_portfolio": []})


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


class TestParityWithThePostgresStub(unittest.TestCase):
    """The SQLite backend mirrors the stub: same methods, same parameters."""

    def _public_api(self, cls: type) -> dict[str, list[str]]:
        return {
            name: [
                param
                for param in inspect.signature(getattr(cls, name)).parameters
                if param not in ("self", "cls")
            ]
            for name in dir(cls)
            if not name.startswith("_")
            and callable(getattr(cls, name, None))
        }

    def test_the_same_public_methods(self) -> None:
        self.assertEqual(
            sorted(self._public_api(SqliteStorage)),
            sorted(self._public_api(PostgresStorage)),
        )

    def test_the_same_parameters(self) -> None:
        self.assertEqual(
            self._public_api(SqliteStorage), self._public_api(PostgresStorage)
        )

    def test_the_constructor_takes_the_same_shape(self) -> None:
        # (locator, table, row factory) — only the locator differs.
        for cls in (SqliteStorage, PostgresStorage):
            params = list(inspect.signature(cls.__init__).parameters)
            self.assertEqual(params[2:], ["table", "object_factory"])


if __name__ == "__main__":
    unittest.main()
