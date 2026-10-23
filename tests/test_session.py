# SPDX-License-Identifier: AGPL-3.0-or-later
"""The session store: who is logged in, and for how long.

A session lives 30 minutes. An expired one is not a session: the
record is dropped and the browser is sent back to the login page.
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from visual.web.backend.session import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    clear_sessions,
    login,
    logout,
    read_session,
)


def _cookie(session_id: str) -> str:
    return f"{COOKIE_NAME}={session_id}"


class TestSessionStore(unittest.TestCase):
    def test_a_fresh_session_reads_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            login(db, "sid", "alice", incognito=True)
            session = read_session(db, _cookie("sid"))
            assert session is not None
            self.assertEqual(session.user, "alice")
            self.assertTrue(session.incognito)

    def test_an_unknown_cookie_has_no_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            self.assertIsNone(read_session(db, _cookie("nope")))
            self.assertIsNone(read_session(db, None))

    def test_logout_drops_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            login(db, "sid", "alice")
            logout(db, "sid")
            self.assertIsNone(read_session(db, _cookie("sid")))

    def test_a_session_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            login(db, "sid", "alice")
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "UPDATE sessions SET expires_at = ? WHERE session_id = 'sid'",
                    (time.time() - 1,),
                )
                conn.commit()
            finally:
                conn.close()
            self.assertIsNone(read_session(db, _cookie("sid")))
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT session_id FROM sessions"
                ).fetchall()
            finally:
                conn.close()
            self.assertNotIn(("sid",), rows)

    def test_a_session_without_an_expiry_is_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            login(db, "sid", "alice")
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "UPDATE sessions SET expires_at = 0 WHERE session_id = 'sid'"
                )
                conn.commit()
            finally:
                conn.close()
            self.assertIsNone(read_session(db, _cookie("sid")))

    def test_the_ttl_is_thirty_minutes(self) -> None:
        self.assertEqual(SESSION_TTL_SECONDS, 30 * 60)

    def test_a_fresh_session_carries_its_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            session = login(db, "sid", "alice")
            self.assertGreater(session.expires_at, time.time())
            self.assertLessEqual(
                session.expires_at - time.time(), SESSION_TTL_SECONDS + 1
            )


class TestBrokenSessionRow(unittest.TestCase):
    """A damaged session row is no session, not a broken request."""

    def test_a_legacy_sessions_table_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            conn = sqlite3.connect(db)
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
                read_session(db, _cookie("sid"))

    def test_a_damaged_row_does_not_kill_the_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            login(db, "sid", "alice")
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "UPDATE sessions SET expires_at = 'not a number' "
                    "WHERE session_id = 'sid'"
                )
                conn.commit()
            finally:
                conn.close()
            self.assertIsNone(read_session(db, _cookie("sid")))


class TestClearSessions(unittest.TestCase):
    """``asalloc clear-sessions``: everybody logs in again."""

    def test_it_clears_the_sessions_of_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            login(db, "one", "alice")
            login(db, "two", "bob")
            cleared = clear_sessions(db)
            self.assertEqual(cleared, 2)
            self.assertIsNone(read_session(db, _cookie("one")))
            self.assertIsNone(read_session(db, _cookie("two")))

    def test_only_the_expired_ones_can_be_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            login(db, "live", "alice")
            login(db, "dead", "bob")
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "UPDATE sessions SET expires_at = ? WHERE session_id = 'dead'",
                    (time.time() - 1,),
                )
                conn.commit()
            finally:
                conn.close()
            cleared = clear_sessions(db, expired_only=True)
            self.assertEqual(cleared, 1)
            session = read_session(db, _cookie("live"))
            assert session is not None
            self.assertEqual(session.user, "alice")


if __name__ == "__main__":
    unittest.main()
