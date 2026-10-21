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

from visual.web.backend.session import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
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


if __name__ == "__main__":
    unittest.main()
