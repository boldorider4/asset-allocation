# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard endpoint: a session gates the app routes; /data/* is per user.

Every protected route needs the session cookie the login endpoint hands
out (``asalloc_sid=...``): without it the server answers 302 (login
page) or 404 (chart data).
"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from assets_helpers import user_payload

from visual.web.backend.serve import DashboardHandler

#: The user the fixtures know about (the assets store is keyed by user).
_USER = "default"
#: A second known user: the store holds a portfolio for them, too.
_OTHER_USER = "alice"
#: Sentinel for "the session cookie of this test"; ``None`` sends none.
_SESSION = object()


def _login_cookie(port: int, user: str = _USER, incognito: bool = False) -> str:
    """Log in over HTTP; returns the session cookie (``asalloc_sid=...``).

    The cookie is the credential every protected route needs from now
    on: without it the server answers 302 (login page) or 404 (charts).
    """
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/login",
        data=json.dumps({"user": user, "incognito": incognito}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        resp.read()
        set_cookie = resp.headers.get("Set-Cookie") or ""
    cookie = set_cookie.split(";", 1)[0].strip()
    if not cookie.startswith("asalloc_sid="):
        raise AssertionError(f"login set no session cookie: {set_cookie!r}")
    return cookie


class _ServerCase(unittest.TestCase):
    """A dashboard server on a temp workspace, with session helpers."""

    data_dirname = "data"

    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        tmp = Path(self._holder.name)
        self.root = tmp / "visualizer"
        self.data_root = self.root / self.data_dirname
        (self.data_root / _USER).mkdir(parents=True)
        (self.data_root / _USER / "01-a.raw").write_text("PLAIN", encoding="utf-8")
        (self.data_root / _OTHER_USER).mkdir(parents=True)
        (self.data_root / _OTHER_USER / "01-a.raw").write_text("OTHER", encoding="utf-8")
        (self.root / "index.html").write_text("DASHBOARD", encoding="utf-8")
        (self.root / "login.html").write_text("LOGIN PAGE", encoding="utf-8")
        self.assets = tmp / "assets.json"
        self.assets.write_text(
            json.dumps(
                {
                    # Both known users hold a position, so their chart
                    # files are legitimate (a user without positions has
                    # no charts - the server clears them).
                    _USER: user_payload(
                        {
                            "equity_portfolio": [
                                {
                                    "name": "A",
                                    "ISIN": "IE00A",
                                    "shares": 1,
                                    "value": 10,
                                }
                            ]
                        }
                    ),
                    _OTHER_USER: user_payload(
                        {
                            "equity_portfolio": [
                                {
                                    "name": "B",
                                    "ISIN": "IE00B",
                                    "shares": 2,
                                    "value": 20,
                                }
                            ]
                        }
                    ),
                }
            ),
            encoding="utf-8",
        )
        # The accounts that may log in (the users store, not the assets).
        self.users = tmp / "users.json"
        self.users.write_text(
            json.dumps(
                {_USER: {"password": ""}, _OTHER_USER: {"password": "other-pw"}}
            ),
            encoding="utf-8",
        )
        # The shared price cache the constituents page reads.
        self.cache = tmp / "cache.json"
        self.cache.write_text("{}", encoding="utf-8")
        handler = functools.partial(
            DashboardHandler,
            directory=str(self.root),
            assets_file=str(self.assets),
            cache_file=str(self.cache),
            users_file=str(self.users),
            data_dirname=self.data_dirname,
        )
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._httpd.shutdown)
        # The session every protected request carries.
        self.cookie = self.login()

    # -- requests ------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _dashboard(self, query: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/dashboard{query}"

    def login(self, user: str = _USER, incognito: bool = False) -> str:
        """Log in over HTTP; returns the session cookie."""
        return _login_cookie(self.port, user, incognito)

    def _get(
        self,
        path: str,
        cookie: str | None | object = _SESSION,
        referer: str | None = None,
    ) -> tuple[int, str]:
        """GET (redirects followed); returns (status, body)."""
        req = urllib.request.Request(self._url(path))
        if cookie is _SESSION:
            cookie = self.cookie
        if cookie:
            req.add_header("Cookie", cookie)
        if referer is not None:
            req.add_header("Referer", referer)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def _raw(
        self,
        path: str,
        cookie: str | None | object = _SESSION,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Request without redirect following; returns (status, headers, body)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            sent = dict(headers or {})
            if cookie is _SESSION:
                cookie = self.cookie
            if cookie:
                sent["Cookie"] = cookie
            conn.request(method, path, body=data, headers=sent)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
        finally:
            conn.close()

    def _post_json(
        self,
        path: str,
        payload: dict,
        cookie: str | None | object = _SESSION,
    ) -> tuple[int, dict[str, str], str]:
        """POST a JSON body; returns (status, headers, body)."""
        return self._raw(
            path,
            cookie=cookie,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )


class TestDashboardEndpoint(_ServerCase):
    def test_dashboard_needs_a_session(self) -> None:
        # Without a session the app shell is not served at all.
        status, headers, _ = self._raw("/dashboard", cookie=None)
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/login")

    def test_dashboard_serves_index_in_both_modes(self) -> None:
        for query in ("", "?incognito=false", "?incognito=true"):
            status, body = self._get(f"/dashboard{query}")
            self.assertEqual(status, 200, query)
            self.assertEqual(body, "DASHBOARD", query)

    def test_responses_force_revalidation(self) -> None:
        # No blind caching anywhere: documents, scripts, and chart
        # payloads must revalidate, so deploys/updates take effect
        # without ever clearing browser data. no-cache (not no-store)
        # keeps back-forward cache eligibility.
        for path in ("/dashboard", "/index.html", "/data/01-a.raw"):
            req = urllib.request.Request(self._url(path))
            req.add_header("Cookie", self.cookie)
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200, path)
                self.assertEqual(
                    resp.headers.get("Cache-Control"), "no-cache", path
                )

    def test_root_redirects_to_the_landing_page(self) -> None:
        # No session: the login page. With a session: the dashboard.
        status, headers, _ = self._raw("/", cookie=None)
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/login")
        status, headers, _ = self._raw("/")
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/dashboard")
        # The query rides along.
        status, headers, _ = self._raw("/?incognito=true")
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/dashboard?incognito=true")
        # Redirect target serves the gallery.
        status, body = self._get("/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(body, "DASHBOARD")

    def test_login_page_is_served_when_logged_out(self) -> None:
        status, body = self._get("/login", cookie=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, "LOGIN PAGE")

    def test_login_redirects_to_dashboard_when_logged_in(self) -> None:
        status, headers, _ = self._raw("/login")
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/dashboard")

    def test_data_file_served_literally_regardless_of_referer(self) -> None:
        status, body = self._get("/data/01-a.raw")
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw", referer=self._dashboard()
        )
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw",
            referer=self._dashboard("?incognito=true"),
        )
        self.assertEqual((status, body), (200, "PLAIN"))

    def test_data_needs_a_session(self) -> None:
        status, body = self._get("/data/01-a.raw", cookie=None)
        self.assertEqual(status, 404)
        self.assertNotIn("PLAIN", body)

    def test_empty_user_has_no_charts(self) -> None:
        # A user without positions must not keep showing the last
        # computed charts: the server clears their chart dir.
        empty = "empty_user"
        (self.data_root / empty).mkdir(parents=True)
        stale = self.data_root / empty / "01-complete-portfolio.raw"
        stale.write_text("STALE", encoding="utf-8")
        assets = json.loads(self.assets.read_text(encoding="utf-8"))
        assets[empty] = user_payload({"equity_portfolio": []})
        self.assets.write_text(json.dumps(assets), encoding="utf-8")
        users = json.loads(self.users.read_text(encoding="utf-8"))
        users[empty] = {"password": ""}
        self.users.write_text(json.dumps(users), encoding="utf-8")

        cookie = self.login(empty)
        # The dashboard shell heals the charts on the way in...
        self._get("/dashboard", cookie=cookie)
        self.assertFalse(stale.exists())
        # ...and so does the constituents page.
        stale.write_text("STALE", encoding="utf-8")
        self._get("/constituents", cookie=cookie)
        self.assertFalse(stale.exists())

    def test_user_with_positions_keeps_their_charts(self) -> None:
        # The charts of a user who still holds positions stay untouched.
        chart = self.data_root / _USER / "01-a.raw"
        self.assertTrue(chart.exists())
        self._get("/dashboard", cookie=self.cookie)
        self._get("/constituents", cookie=self.cookie)
        self.assertTrue(chart.exists())

    def test_data_serves_the_session_users_directory(self) -> None:
        # Charts are per user: the session decides whose dir is served.
        status, body = self._get("/data/01-a.raw")
        self.assertEqual((status, body), (200, "PLAIN"))
        other = self.login(_OTHER_USER)
        status, body = self._get("/data/01-a.raw", cookie=other)
        self.assertEqual((status, body), (200, "OTHER"))

    def test_data_listing_shows_file(self) -> None:
        status, body = self._get("/data/")
        self.assertEqual(status, 200)
        self.assertIn("01-a.raw", body)

    def test_unknown_query_still_serves_same_file(self) -> None:
        status, body = self._get("/data/01-a.raw?incognito=yes")
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw",
            referer=self._dashboard("?incognito=yes"),
        )
        self.assertEqual((status, body), (200, "PLAIN"))

    def test_traversal_stays_jailed(self) -> None:
        status, body = self._get(
            "/data/../index.html",
            referer=self._dashboard("?incognito=true"),
        )
        self.assertEqual(status, 404)
        self.assertNotIn("DASHBOARD", body)


class TestSessionApi(_ServerCase):
    """The session endpoints: log in, ask who is logged in, log out."""

    def test_login_rejects_an_unknown_user(self) -> None:
        status, _, body = self._post_json("/api/login", {"user": "nobody"})
        self.assertEqual(status, 404)
        self.assertIn("unknown user: nobody", body)

    def test_login_accepts_a_known_user(self) -> None:
        status, headers, body = self._post_json("/api/login", {"user": _USER})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user"], _USER)
        self.assertFalse(payload["incognito"])
        self.assertEqual(payload["redirect"], "/dashboard?incognito=false")
        self.assertIn("asalloc_sid=", headers["set-cookie"])

    def test_login_carries_the_incognito_flag(self) -> None:
        status, headers, body = self._post_json(
            "/api/login", {"user": _USER, "incognito": True}
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["incognito"])
        self.assertEqual(payload["redirect"], "/dashboard?incognito=true")
        cookie = headers["set-cookie"].split(";", 1)[0]
        status, _, body = self._raw("/api/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body), {"user": _USER, "incognito": True}
        )

    def test_login_needs_a_user_name(self) -> None:
        status, _, body = self._post_json("/api/login", {"user": "   "})
        self.assertEqual(status, 400)
        self.assertIn("user", body)


class TestUsersStoreLogin(_ServerCase):
    """The login asks the users store, not the assets store."""

    def test_user_in_assets_but_not_in_users_is_rejected(self) -> None:
        # The assets store knows them, the users store does not: unknown.
        self.assertIn(_USER, json.loads(self.assets.read_text(encoding="utf-8")))
        accounts = json.loads(self.users.read_text(encoding="utf-8"))
        accounts.pop(_USER, None)
        self.users.write_text(json.dumps(accounts), encoding="utf-8")
        status, _, body = self._post_json("/api/login", {"user": _USER})
        self.assertEqual(status, 404)
        self.assertIn("unknown user", body)

    def test_user_in_users_but_not_in_assets_gets_a_portfolio(self) -> None:
        # A known account with no portfolio yet: the login creates an
        # empty one, so the dashboard shows the empty state.
        fresh = "fresh_user"
        accounts = json.loads(self.users.read_text(encoding="utf-8"))
        accounts[fresh] = {"password": ""}
        self.users.write_text(json.dumps(accounts), encoding="utf-8")
        self.assertNotIn(
            fresh, json.loads(self.assets.read_text(encoding="utf-8"))
        )
        status, headers, body = self._post_json("/api/login", {"user": fresh})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        cookie = headers["set-cookie"].split(";", 1)[0]
        # The assets store gained the user with an empty portfolio.
        stored = json.loads(self.assets.read_text(encoding="utf-8"))
        self.assertIn(fresh, stored)
        self.assertEqual(stored[fresh], user_payload({}))
        # ...and the pages behave as they do for an empty portfolio.
        status, body = self._get("/constituents", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("Equity", body)

    def test_login_does_not_touch_an_existing_portfolio(self) -> None:
        before = json.loads(self.assets.read_text(encoding="utf-8"))
        status, _, _ = self._post_json("/api/login", {"user": _USER})
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(self.assets.read_text(encoding="utf-8")), before
        )

    def test_session_reports_the_logged_in_user(self) -> None:
        status, _, body = self._raw("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"user": _USER, "incognito": False})

    def test_session_without_a_login_reports_no_user(self) -> None:
        status, _, body = self._raw("/api/session", cookie=None)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"user": None, "incognito": False})

    def test_logout_clears_the_session(self) -> None:
        status, headers, body = self._post_json("/api/logout", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "redirect": "/login"})
        # The cookie is cleared...
        self.assertIn("asalloc_sid=", headers["set-cookie"])
        self.assertIn("Max-Age=0", headers["set-cookie"])
        # ...and the session is gone: the dashboard is out of reach again.
        status, headers, _ = self._raw("/dashboard", cookie=self.cookie)
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/login")
        status, _, body = self._raw("/api/session", cookie=self.cookie)
        self.assertEqual(json.loads(body), {"user": None, "incognito": False})

    def test_login_page_reaches_the_dashboard_after_a_login(self) -> None:
        # The round trip a browser makes: login, then land on the app.
        cookie = self.login(_OTHER_USER)
        status, body = self._get("/dashboard", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body, "DASHBOARD")
        status, _, body = self._raw("/api/session", cookie=cookie)
        self.assertEqual(json.loads(body)["user"], _OTHER_USER)


class TestCustomDataLayout(_ServerCase):
    """Renamed on-disk data trees still serve under the fixed /data prefix."""

    data_dirname = "customdir"

    def test_renamed_tree_serves_literally(self) -> None:
        status, body = self._get("/data/01-a.raw")
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw", referer=self._dashboard()
        )
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw",
            referer=self._dashboard("?incognito=true"),
        )
        self.assertEqual((status, body), (200, "PLAIN"))

    def test_renamed_tree_needs_a_session(self) -> None:
        status, body = self._get("/data/01-a.raw", cookie=None)
        self.assertEqual(status, 404)
        self.assertNotIn("PLAIN", body)


if __name__ == "__main__":
    unittest.main()
