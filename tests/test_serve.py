# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard endpoint: /dashboard serves index.html; /data/* serves literally."""

from __future__ import annotations

import functools
import http.server
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from visual.web.backend.serve import DashboardHandler


class TestDashboardEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.root = Path(self._holder.name) / "visualizer"
        (self.root / "data").mkdir(parents=True)
        (self.root / "index.html").write_text("DASHBOARD", encoding="utf-8")
        (self.root / "data" / "01-a.raw").write_text("PLAIN", encoding="utf-8")
        handler = functools.partial(DashboardHandler, directory=str(self.root))
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._httpd.shutdown)

    def _get(self, path: str, referer: str | None = None) -> tuple[int, str]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if referer is not None:
            req.add_header("Referer", referer)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def _dashboard(self, query: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/dashboard{query}"

    def test_dashboard_serves_index_in_both_modes(self) -> None:
        for query in ("", "?incognito=false", "?incognito=true"):
            status, body = self._get(f"/dashboard{query}")
            self.assertEqual(status, 200, query)
            self.assertEqual(body, "DASHBOARD", query)

    def test_root_redirects_to_dashboard(self) -> None:
        import http.client

        for path, location in (
            ("/", "/dashboard"),
            ("/?incognito=true", "/dashboard?incognito=true"),
        ):
            conn = http.client.HTTPConnection("127.0.0.1", self.port)
            conn.request("GET", path)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 302, path)
            self.assertEqual(resp.getheader("Location"), location, path)
            conn.close()
        # Redirect target serves the gallery.
        status, body = self._get("/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(body, "DASHBOARD")

    def test_data_file_served_literally_regardless_of_referer(self) -> None:
        status, body = self._get("/data/01-a.raw")
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get("/data/01-a.raw", referer=self._dashboard())
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw", referer=self._dashboard("?incognito=true")
        )
        self.assertEqual((status, body), (200, "PLAIN"))

    def test_data_listing_shows_file(self) -> None:
        status, body = self._get("/data/")
        self.assertEqual(status, 200)
        self.assertIn("01-a.raw", body)

    def test_unknown_query_still_serves_same_file(self) -> None:
        status, body = self._get("/data/01-a.raw?incognito=yes")
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw", referer=self._dashboard("?incognito=yes")
        )
        self.assertEqual((status, body), (200, "PLAIN"))

    def test_traversal_stays_jailed(self) -> None:
        status, body = self._get(
            "/data/../index.html", referer=self._dashboard("?incognito=true")
        )
        self.assertEqual(status, 404)
        self.assertNotIn("DASHBOARD", body)


class TestCustomDataLayout(unittest.TestCase):
    """Renamed on-disk data trees still serve under the fixed /data prefix."""

    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.root = Path(self._holder.name) / "visualizer"
        (self.root / "customdir").mkdir(parents=True)
        (self.root / "index.html").write_text("DASHBOARD", encoding="utf-8")
        (self.root / "customdir" / "01-a.raw").write_text("PLAIN", encoding="utf-8")
        handler = functools.partial(
            DashboardHandler,
            directory=str(self.root),
            data_dirname="customdir",
        )
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._httpd.shutdown)

    def _get(self, path: str, referer: str | None = None) -> tuple[int, str]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if referer is not None:
            req.add_header("Referer", referer)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def _dashboard(self, query: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/dashboard{query}"

    def test_renamed_tree_serves_literally(self) -> None:
        status, body = self._get("/data/01-a.raw")
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get("/data/01-a.raw", referer=self._dashboard())
        self.assertEqual((status, body), (200, "PLAIN"))
        status, body = self._get(
            "/data/01-a.raw", referer=self._dashboard("?incognito=true")
        )
        self.assertEqual((status, body), (200, "PLAIN"))


if __name__ == "__main__":
    unittest.main()
