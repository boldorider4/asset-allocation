# SPDX-License-Identifier: AGPL-3.0-or-later
"""The session store: who is logged in, and for how long.

A session lives 30 minutes. An expired one is not a session: the
record is dropped and the browser is sent back to the login page.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from storage.wiring import BACKEND_JSON, BACKEND_SQLITE
from visual.web.backend.session import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    clear_sessions,
    login,
    logout,
    read_session,
    store_path,
)


def _cookie(session_id: str) -> str:
    return f"{COOKIE_NAME}={session_id}"


class TestSessionStore(unittest.TestCase):
    def test_a_fresh_session_reads_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            login(tmp, "sid", "alice", incognito=True)
            session = read_session(tmp, _cookie("sid"))
            assert session is not None
            self.assertEqual(session.user, "alice")
            self.assertTrue(session.incognito)

    def test_an_unknown_cookie_has_no_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_session(tmp, _cookie("nope")))
            self.assertIsNone(read_session(tmp, None))

    def test_logout_drops_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            login(tmp, "sid", "alice")
            logout(tmp, "sid")
            self.assertIsNone(read_session(tmp, _cookie("sid")))

    def test_a_session_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            login(tmp, "sid", "alice")
            path = store_path(tmp)
            store = json.loads(path.read_text(encoding="utf-8"))
            store["sid"]["expires_at"] = time.time() - 1
            path.write_text(json.dumps(store), encoding="utf-8")
            self.assertIsNone(read_session(tmp, _cookie("sid")))
            # The stale record is gone, not just ignored.
            store = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("sid", store)

    def test_a_session_without_an_expiry_is_expired(self) -> None:
        # Sessions written before the timeout existed must not linger.
        with tempfile.TemporaryDirectory() as tmp:
            path = store_path(tmp)
            path.write_text(
                json.dumps({"sid": {"user": "alice", "incognito": False}}),
                encoding="utf-8",
            )
            self.assertIsNone(read_session(tmp, _cookie("sid")))

    def test_the_ttl_is_thirty_minutes(self) -> None:
        self.assertEqual(SESSION_TTL_SECONDS, 30 * 60)

    def test_a_fresh_session_carries_its_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = login(tmp, "sid", "alice")
            self.assertGreater(session.expires_at, time.time())
            self.assertLessEqual(
                session.expires_at - time.time(), SESSION_TTL_SECONDS + 1
            )


class TestSessionStoreBackends(unittest.TestCase):
    """The session store works the same on every backend."""

    def _store_args(self, tmp: str, backend: str) -> tuple[str, Path | None]:
        """The store arguments for a backend (the db file for SQLite)."""
        if backend == BACKEND_SQLITE:
            return backend, Path(tmp) / "sessions.db"
        return backend, None

    def test_a_session_round_trips_on_every_backend(self) -> None:
        for backend in (BACKEND_JSON, BACKEND_SQLITE):
            with self.subTest(backend=backend):
                with tempfile.TemporaryDirectory() as tmp:
                    name, db_file = self._store_args(tmp, backend)
                    login(
                        tmp,
                        "sid",
                        "alice",
                        incognito=True,
                        backend=name,
                        db_file=db_file,
                    )
                    session = read_session(
                        tmp,
                        _cookie("sid"),
                        backend=name,
                        db_file=db_file,
                    )
                    assert session is not None
                    self.assertEqual(session.user, "alice")
                    self.assertTrue(session.incognito)

    def test_logout_ends_the_session_on_every_backend(self) -> None:
        for backend in (BACKEND_JSON, BACKEND_SQLITE):
            with self.subTest(backend=backend):
                with tempfile.TemporaryDirectory() as tmp:
                    name, db_file = self._store_args(tmp, backend)
                    login(tmp, "sid", "alice", backend=name, db_file=db_file)
                    logout(tmp, "sid", backend=name, db_file=db_file)
                    self.assertIsNone(
                        read_session(
                            tmp, _cookie("sid"), backend=name, db_file=db_file
                        )
                    )

    def test_an_expired_session_is_gone_on_every_backend(self) -> None:
        for backend in (BACKEND_JSON, BACKEND_SQLITE):
            with self.subTest(backend=backend):
                with tempfile.TemporaryDirectory() as tmp:
                    name, db_file = self._store_args(tmp, backend)
                    login(tmp, "sid", "alice", backend=name, db_file=db_file)
                    # The expiry is in the past: the session is over.
                    expired = time.time() - 1
                    if backend == BACKEND_JSON:
                        path = store_path(tmp)
                        store = json.loads(path.read_text(encoding="utf-8"))
                        store["sid"]["expires_at"] = expired
                        path.write_text(json.dumps(store), encoding="utf-8")
                    else:
                        import sqlite3

                        conn = sqlite3.connect(db_file)
                        conn.execute(
                            "UPDATE sessions SET expires_at = ? "
                            "WHERE session_id = ?",
                            (expired, "sid"),
                        )
                        conn.commit()
                        conn.close()
                    self.assertIsNone(
                        read_session(
                            tmp, _cookie("sid"), backend=name, db_file=db_file
                        )
                    )


class TestBrokenSessionRow(unittest.TestCase):
    """A damaged session row is no session, not a broken request."""

    def test_a_legacy_sessions_table_is_rejected(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    "CREATE TABLE sessions (key TEXT PRIMARY KEY, payload TEXT)"
                )
                conn.execute(
                    "INSERT INTO sessions (key, payload) VALUES ('sid', NULL)"
                )
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(ValueError, "legacy"):
                read_session(
                    tmp,
                    _cookie("sid"),
                    backend=BACKEND_SQLITE,
                    db_file=db_file,
                )

    def test_a_damaged_row_does_not_kill_the_request(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            login(tmp, "sid", "alice", backend=BACKEND_SQLITE, db_file=db_file)
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    "UPDATE sessions SET expires_at = 'not a number' "
                    "WHERE session_id = 'sid'"
                )
                conn.commit()
            finally:
                conn.close()
            self.assertIsNone(
                read_session(tmp, _cookie("sid"), backend=BACKEND_SQLITE, db_file=db_file)
            )


class TestClearSessions(unittest.TestCase):
    """``asalloc clear-sessions``: everybody logs in again."""

    def test_it_clears_the_sessions_of_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            login(tmp, "one", "alice", backend=BACKEND_SQLITE, db_file=db_file)
            login(tmp, "two", "bob", backend=BACKEND_SQLITE, db_file=db_file)
            cleared = clear_sessions(
                tmp, backend=BACKEND_SQLITE, db_file=db_file
            )
            self.assertEqual(cleared, 2)
            self.assertIsNone(
                read_session(tmp, _cookie("one"), backend=BACKEND_SQLITE, db_file=db_file)
            )
            self.assertIsNone(
                read_session(tmp, _cookie("two"), backend=BACKEND_SQLITE, db_file=db_file)
            )

    def test_it_clears_the_sessions_of_the_json_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            login(tmp, "one", "alice")
            login(tmp, "two", "bob")
            self.assertEqual(clear_sessions(tmp), 2)
            self.assertIsNone(read_session(tmp, _cookie("one")))

    def test_only_the_expired_ones_can_be_cleared(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "asalloc.db"
            login(tmp, "live", "alice", backend=BACKEND_SQLITE, db_file=db_file)
            login(tmp, "dead", "bob", backend=BACKEND_SQLITE, db_file=db_file)
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    "UPDATE sessions SET expires_at = ? WHERE session_id = 'dead'",
                    (time.time() - 1,),
                )
                conn.commit()
            finally:
                conn.close()
            cleared = clear_sessions(
                tmp, backend=BACKEND_SQLITE, db_file=db_file, expired_only=True
            )
            self.assertEqual(cleared, 1)
            # The live session is still there.
            session = read_session(
                tmp, _cookie("live"), backend=BACKEND_SQLITE, db_file=db_file
            )
            assert session is not None
            self.assertEqual(session.user, "alice")

    def test_clearing_nothing_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clear_sessions(tmp), 0)


class TestSessionSurvivesAnUpdate(unittest.TestCase):
    """The update writes to the same store; the session must survive it.

    The update runs in its own process (like the systemd timer does),
    so this is the interleaving that broke the session before: one
    writer and the server sharing the database.
    """

    def test_the_session_survives_a_concurrent_update(self) -> None:
        import os
        import sqlite3
        import subprocess
        import sys

        from cli.context import AppConfig, RuntimeContext, ServerConfig
        from cli.update import main as run_update
        from storage.repositories import UsersRepository
        from storage.wiring import users_backend

        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_file = root / "asalloc.db"
            assets = root / "assets.json"
            cache = root / "cache.json"
            isin = root / "isin.json"
            assets.write_text(
                json.dumps(
                    {
                        "alice": {
                            "portfolio_name": "Fund",
                            "positions": {
                                "equity_portfolio": [
                                    {
                                        "name": "A",
                                        "ISIN": "IE00A",
                                        "shares": 1,
                                        "value": 10,
                                    }
                                ]
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            cache.write_text(json.dumps({"IE00A": {"price": 10.0}}), encoding="utf-8")
            isin.write_text("{}", encoding="utf-8")

            # The users live in the database (the sqlite backend).
            users_repo = UsersRepository(
                users_backend(root / "users.json", "sqlite", db_file=db_file)
            )
            users_repo.open()
            users_repo.add_user("alice", "pw")
            users_repo.persist()

            ctx = RuntimeContext(
                config=AppConfig(
                    user="alice",
                    assets_file=assets,
                    cache_file=cache,
                    isin_file=isin,
                    plotter="web",
                    server=ServerConfig(
                        port=0,
                        address="localhost",
                        directory=root / "viz",
                        backend="sqlite",
                        db_file=db_file,
                    ),
                )
            )
            # A session, written by the server.
            from visual.web.backend.session import login, read_session

            login(
                root / "viz",
                "sid",
                "alice",
                backend="sqlite",
                db_file=db_file,
            )
            # The update, in its own process (like the systemd timer).
            worker = root / "worker.py"
            worker.write_text(
                "from pathlib import Path\n"
                "from cli.context import AppConfig, RuntimeContext, ServerConfig\n"
                "from cli.update import main\n"
                f"cfg = AppConfig(user='alice', assets_file={str(assets)!r},\n"
                f"    cache_file={str(cache)!r}, isin_file={str(isin)!r},\n"
                "    server=ServerConfig(port=0, address='localhost',\n"
                f"        directory=Path({str(root / 'viz')!r}), backend='sqlite',\n"
                f"        db_file={str(db_file)!r}))\n"
                "main(RuntimeContext(config=cfg))\n",
                encoding="utf-8",
            )
            env = dict(os.environ, ASALLOC_CONFIG=str(root / "none.ini"))
            result = subprocess.run(
                [sys.executable, str(worker)],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            # The session is still there.
            session = read_session(
                root / "viz", _cookie("sid"), backend="sqlite", db_file=db_file
            )
            assert session is not None
            self.assertEqual(session.user, "alice")
            # And the update really used the same database: it created
            # the normalized position tables there.
            conn = sqlite3.connect(db_file)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                conn.close()
            self.assertIn("user_positions", tables)
            self.assertIn("user_portfolios", tables)
            self.assertNotIn("user_assets", tables)


if __name__ == "__main__":
    unittest.main()
