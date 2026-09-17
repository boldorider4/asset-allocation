"""Constituents endpoint: sections, cells, icons, and the /constituents route."""

from __future__ import annotations

import functools
import http.server
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from visual.constituents import load_constituents, render_constituents_page
from visual.serve import DashboardHandler

_ASSETS = {
    "cash_portfolio": [
        {"name": "Tagesgeld", "value": 100, "broker": "scalable", "ISIN": None},
        {
            "name": "<evil> & co",
            "shares": 80,
            "value": 10100,
            "broker": "oskar",
            "ISIN": "LU0290358497",
        },
    ],
    "equity_portfolio": [
        {
            "name": "Amundi Core",
            "shares": 220,
            "value": None,
            "broker": "scalable",
            "ISIN": "IE000BI8OT95",
        },
        {
            "name": "Trade Republic ETF",
            "shares": 10,
            "value": 500.5,
            "broker": "traderepublic",
            "ISIN": "IE00B4YBJ215",
        },
    ],
    "weird_bucket": [
        {"name": "Mystery", "shares": None, "value": None, "broker": "tf-bank", "ISIN": None}
    ],
}

_CACHE = {
    "IE000BI8OT95": {"price": 81.25},
    "IE00B4YBJ215": {"price": 99.5},
}


def _write_files(tmp: Path) -> tuple[Path, Path]:
    assets = tmp / "assets.json"
    cache = tmp / "cache.json"
    assets.write_text(json.dumps(_ASSETS), encoding="utf-8")
    cache.write_text(json.dumps(_CACHE), encoding="utf-8")
    return assets, cache


class TestLoadConstituents(unittest.TestCase):
    def test_sections_in_canonical_order_with_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets, cache = _write_files(Path(tmp))
            sections = load_constituents(assets, cache)
        self.assertEqual(
            [label for label, _ in sections], ["Equity", "Cash", "Weird Bucket"]
        )

    def test_row_fields_and_cache_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets, cache = _write_files(Path(tmp))
            sections = load_constituents(assets, cache)
        equity = dict(sections)["Equity"]
        self.assertEqual(equity[0]["price"], 81.25)
        self.assertIsNone(equity[0]["value"])
        self.assertTrue(equity[0]["editable_shares"])
        self.assertEqual(equity[1]["price"], 99.5)
        cash = dict(sections)["Cash"]
        self.assertIsNone(cash[0]["price"])
        self.assertFalse(cash[1]["editable_shares"])

    def test_missing_cache_file_means_no_prices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.json"
            assets.write_text(json.dumps(_ASSETS), encoding="utf-8")
            sections = load_constituents(assets, Path(tmp) / "nope.json")
        self.assertIsNone(dict(sections)["Equity"][0]["price"])

    def test_non_object_assets_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.json"
            assets.write_text("[1, 2]", encoding="utf-8")
            cache = Path(tmp) / "cache.json"
            cache.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_constituents(assets, cache)


class TestRenderConstituentsPage(unittest.TestCase):
    def _page(self) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            assets, cache = _write_files(Path(tmp))
            sections = load_constituents(assets, cache)
        return render_constituents_page(sections)

    def test_thin_rules_divide_sections(self) -> None:
        page = self._page()
        self.assertEqual(page.count('class="section-rule"'), 2)
        self.assertIn("<h2>Equity</h2>", page)
        self.assertIn("<h2>Weird Bucket</h2>", page)

    def test_cells_and_placeholders(self) -> None:
        page = self._page()
        self.assertIn("Amundi Core", page)
        # Editable shares for non-Oskar rows.
        self.assertIn('class="cell-box editable"', page)
        # Locked value and price boxes.
        self.assertIn('class="cell-box locked"', page)
        # Null value renders as an em-dash, never "None".
        self.assertIn("—", page)
        self.assertNotIn(">None<", page)

    def test_oskar_shares_not_editable(self) -> None:
        page = self._page()
        self.assertNotIn('data-field="shares" readonly', page)
        # Oskar row shares render as a locked box instead of an input.
        oskar_locked = (
            '<span class="cell-box locked" data-field="shares">80</span>'
        )
        self.assertIn(oskar_locked, page)

    def test_broker_marks_and_escaping(self) -> None:
        page = self._page()
        self.assertIn("<svg", page)
        self.assertIn("&lt;evil&gt; &amp; co", page)
        self.assertNotIn("<evil>", page)

    def test_overview_button_top_right(self) -> None:
        page = self._page()
        self.assertIn('<a class="nav-button" href="/dashboard">Overview</a>', page)


class TestConstituentsRoute(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        tmp = Path(self._holder.name)
        self.root = tmp / "visualizer"
        self.root.mkdir()
        (self.root / "index.html").write_text("DASHBOARD", encoding="utf-8")
        self.assets, self.cache = _write_files(tmp)
        handler = functools.partial(
            DashboardHandler,
            directory=str(self.root),
            assets_file=str(self.assets),
            cache_file=str(self.cache),
        )
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._httpd.shutdown)

    def _get(self, path: str) -> tuple[int, str]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_constituents_renders_table(self) -> None:
        for path in ("/constituents", "/constituents/"):
            status, body = self._get(path)
            self.assertEqual(status, 200, path)
            self.assertIn("Amundi Core", body)
            self.assertIn("81.25", body)
            self.assertIn("<table>", body)

    def test_missing_files_yield_502_without_traceback(self) -> None:
        handler = functools.partial(
            DashboardHandler,
            directory=str(self.root),
            assets_file=str(self.root / "nope.json"),
            cache_file=str(self.cache),
        )
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/constituents")
        try:
            urllib.request.urlopen(req)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 502)
            body = exc.read().decode("utf-8", errors="replace")
            self.assertIn("constituents unavailable", body)
            self.assertNotIn("Traceback", body)


if __name__ == "__main__":
    unittest.main()
