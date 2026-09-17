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

from visual.constituents import (
    load_constituents,
    render_constituents_page,
    store_constituent_value,
)
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

    def test_full_label_mapping_and_order(self) -> None:
        buckets = {
            "pension_portfolio": [],
            "commodity_portfolio": [],
            "cash_portfolio": [],
            "bond_portfolio": [],
            "equity_portfolio": [],
            "fixed_maturity_bond_portfolio": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.json"
            cache = Path(tmp) / "cache.json"
            assets.write_text(json.dumps(buckets), encoding="utf-8")
            cache.write_text("{}", encoding="utf-8")
            sections = load_constituents(assets, cache)
        self.assertEqual(
            [label for label, _ in sections],
            [
                "Equity",
                "Fixed Income",
                "Commodities / Inflation",
                "Fixed Maturity",
                "Cash",
                "Pensions",
            ],
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
        # Tagesgeld rows are cash-like: locked shares, regardless of broker.
        self.assertFalse(cash[0]["editable_shares"])
        self.assertTrue(cash[0]["no_quote"])

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

    def test_tables_scroll_horizontally_on_small_screens(self) -> None:
        page = self._page()
        self.assertEqual(page.count('class="table-scroll"'), 3)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("overflow-x: auto", css)
        self.assertIn(".table-scroll table", css)

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
            '<span class="cell-box locked" data-field="shares">80.00</span>'
        )
        self.assertIn(oskar_locked, page)

    def test_cashlike_rows_show_dashes(self) -> None:
        page = self._page()
        # Tagesgeld (scalable broker): locked "-" for shares and price...
        self.assertIn(
            '<span class="cell-box locked" data-field="shares">-</span>', page
        )
        self.assertIn(
            '<span class="cell-box locked" data-field="price">-</span>', page
        )
        # ...while its value cell is editable with the booked amount.
        self.assertIn(
            '<input class="cell-box editable" value="100.00" data-field="value"',
            page,
        )

    def test_pension_rows_show_dashes(self) -> None:
        assets = {
            "pension_portfolio": [
                {
                    "name": "bAV",
                    "shares": 10,
                    "value": 5000,
                    "broker": "scalable",
                    "ISIN": "IE00X",
                }
            ]
        }
        cache = {"IE00X": {"price": 50.0}}
        with tempfile.TemporaryDirectory() as tmp:
            assets_path = Path(tmp) / "assets.json"
            cache_path = Path(tmp) / "cache.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            sections = load_constituents(assets_path, cache_path)
        self.assertEqual([label for label, _ in sections], ["Pensions"])
        row = dict(sections)["Pensions"][0]
        self.assertFalse(row["editable_shares"])
        self.assertTrue(row["no_quote"])
        page = render_constituents_page(sections)
        self.assertIn(
            '<span class="cell-box locked" data-field="shares">-</span>', page
        )
        self.assertIn(
            '<span class="cell-box locked" data-field="price">-</span>', page
        )
        # Value stays editable with the booked amount.
        self.assertIn(
            '<input class="cell-box editable" value="5000.00" data-field="value"',
            page,
        )

    def test_check24_rows_show_dashes_in_any_bucket(self) -> None:
        assets = {
            "fixed_maturity_bond_portfolio": [
                {
                    "name": "Check Bond",
                    "shares": 7,
                    "value": 700,
                    "broker": "check24",
                    "ISIN": "IE00Y",
                }
            ],
            "equity_portfolio": [
                {
                    "name": "Check ETF",
                    "shares": 7,
                    "value": 700,
                    "broker": "check24",
                    "ISIN": "IE00Y",
                }
            ],
        }
        cache = {"IE00Y": {"price": 10.0}}
        with tempfile.TemporaryDirectory() as tmp:
            assets_path = Path(tmp) / "assets.json"
            cache_path = Path(tmp) / "cache.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            sections = load_constituents(assets_path, cache_path)
        by_label = dict(sections)
        self.assertFalse(by_label["Fixed Maturity"][0]["editable_shares"])
        self.assertFalse(by_label["Equity"][0]["editable_shares"])
        self.assertTrue(by_label["Fixed Maturity"][0]["no_quote"])
        self.assertTrue(by_label["Equity"][0]["no_quote"])
        page = render_constituents_page(sections)
        self.assertEqual(
            page.count('<span class="cell-box locked" data-field="shares">-</span>'),
            2,
        )
        self.assertEqual(
            page.count('<span class="cell-box locked" data-field="price">-</span>'),
            2,
        )
        # Value stays editable with the booked amount, like pensions.
        self.assertEqual(
            page.count(
                '<input class="cell-box editable" value="700.00" data-field="value"'
            ),
            2,
        )

    def test_broker_marks_and_escaping(self) -> None:
        page = self._page()
        self.assertIn('src="icons/scalable.png"', page)
        self.assertIn('src="icons/oskar.png"', page)
        self.assertIn('src="icons/traderepublic.png"', page)
        # Unknown brokers keep the SVG fallback mark.
        self.assertIn("<svg", page)
        self.assertIn("&lt;evil&gt; &amp; co", page)
        self.assertNotIn("<evil>", page)

    def test_overview_button_top_right(self) -> None:
        page = self._page()
        self.assertIn('<div class="nav-buttons">', page)
        self.assertIn(
            '<a id="overview-link" class="nav-button" href="/dashboard">Dashboard</a>',
            page,
        )
        self.assertIn('id="update-status"', page)

    def test_blocking_update_overlay(self) -> None:
        page = self._page()
        self.assertIn('id="update-overlay" class="overlay" hidden', page)
        self.assertIn("Updating charts…", page)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".overlay {", css)
        self.assertIn("position: fixed", css)
        self.assertIn(".overlay[hidden]", css)

    def test_dashboard_has_sync_prices_button(self) -> None:
        index = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="sync-link"', index)
        self.assertIn(">Sync Prices</a>", index)
        self.assertIn('<script src="dashboard.js"></script>', index)

    def test_dashboard_edit_link_cancels_then_navigates(self) -> None:
        index = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="edit-link"', index)
        dashboard_js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "dashboard.js"
        ).read_text(encoding="utf-8")
        self.assertIn("/api/cancel", dashboard_js)
        self.assertIn("/constituents", dashboard_js)
        dashboard_js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "dashboard.js"
        ).read_text(encoding="utf-8")
        self.assertIn("mode:", dashboard_js)
        self.assertIn('"fat"', dashboard_js)
        self.assertIn("/api/update", dashboard_js)

    def test_editable_inputs_carry_identity_and_baseline(self) -> None:
        page = self._page()
        self.assertIn('data-bucket="equity_portfolio"', page)
        self.assertIn('data-index="0"', page)
        self.assertIn('data-field="shares"', page)
        self.assertIn('data-original="220.00"', page)
        self.assertIn('<script src="constituents.js"></script>', page)

    def test_overview_only_updates_when_dirty(self) -> None:
        js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "constituents.js"
        ).read_text(encoding="utf-8")
        # A successful save stages an update...
        self.assertIn("let dirty = false", js)
        self.assertIn("dirty = true", js)
        # ...while a clean Overview navigates straight to the dashboard.
        self.assertIn("if (!dirty)", js)
        self.assertIn('window.location.href = "/dashboard"', js)

    def test_save_refreshes_pair_with_red_flare_fallback(self) -> None:
        js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "constituents.js"
        ).read_text(encoding="utf-8")
        # Both boxes refresh from the response; red flare only when stale.
        self.assertIn('closest("tr")', js)
        self.assertIn("stale-flash", js)
        # Accepted saves flash green on both boxes of the pair.
        self.assertIn("for (const box of updated)", js)
        self.assertIn('flash(box, "saved-flash")', js)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("input.cell-box.editable.stale-flash", css)


class TestStoreConstituentValue(unittest.TestCase):
    def _assets(self, tmp: Path) -> Path:
        assets = tmp / "assets.json"
        assets.write_text(json.dumps(_ASSETS), encoding="utf-8")
        return assets

    def _read(self, assets: Path):
        return json.loads(assets.read_text(encoding="utf-8"))

    def test_valid_shares_edit_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            result = store_constituent_value(
                assets, "equity_portfolio", 0, "shares", "230.5"
            )
            self.assertEqual(result["shares"], 230.5)
            data = self._read(assets)
            self.assertEqual(data["equity_portfolio"][0]["shares"], 230.5)
            # Untouched rows survive the round-trip.
            self.assertEqual(data["equity_portfolio"][1]["shares"], 10)
            json.loads(assets.read_text(encoding="utf-8"))

    def test_shares_edit_recomputes_value_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            cache = {"IE00B4YBJ215": {"price": 99.5}}
            result = store_constituent_value(
                assets, "equity_portfolio", 1, "shares", "12", cache
            )
            self.assertEqual(result["shares"], 12.0)
            self.assertAlmostEqual(result["value"], 12.0 * 99.5)
            self.assertTrue(result["recomputed"])
            data = self._read(assets)
            self.assertEqual(data["equity_portfolio"][1]["shares"], 12.0)
            self.assertAlmostEqual(data["equity_portfolio"][1]["value"], 12.0 * 99.5)

    def test_value_edit_recomputes_shares_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            cache = {"IE00B4YBJ215": {"price": 99.5}}
            result = store_constituent_value(
                assets, "equity_portfolio", 1, "value", "199", cache
            )
            self.assertEqual(result["value"], 199.0)
            self.assertAlmostEqual(result["shares"], 199.0 / 99.5)
            self.assertTrue(result["recomputed"])
            data = self._read(assets)
            self.assertAlmostEqual(data["equity_portfolio"][1]["shares"], 199.0 / 99.5)

    def test_recompute_needs_cached_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            # Shares edit without a cached price: value untouched, flagged.
            result = store_constituent_value(
                assets, "equity_portfolio", 1, "shares", "12", {}
            )
            self.assertEqual(result["shares"], 12.0)
            self.assertEqual(result["value"], 500.5)
            self.assertFalse(result["recomputed"])
            # Value edit with existing shares but no price: shares untouched.
            result = store_constituent_value(
                assets, "equity_portfolio", 1, "value", "199", {}
            )
            self.assertEqual(result["shares"], 12.0)
            self.assertFalse(result["recomputed"])
            # Zero price counts as missing (no division by zero).
            result = store_constituent_value(
                assets,
                "equity_portfolio",
                1,
                "shares",
                "12",
                {"IE00B4YBJ215": {"price": 0.0}},
            )
            self.assertFalse(result["recomputed"])

    def test_empty_value_means_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            result = store_constituent_value(
                assets, "cash_portfolio", 0, "value", "   "
            )
            self.assertEqual(result["value"], 0.0)
            self.assertTrue(result["recomputed"])
            self.assertEqual(self._read(assets)["cash_portfolio"][0]["value"], 0.0)

    def test_rejects_bad_address_field_and_junk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            before = assets.read_text(encoding="utf-8")
            for bucket, index, field, value in [
                ("nope", 0, "shares", "1"),
                ("equity_portfolio", 99, "shares", "1"),
                ("equity_portfolio", 0, "price", "1"),
                ("equity_portfolio", 0, "shares", "abc"),
                ("equity_portfolio", 0, "shares", float("nan")),
                ("equity_portfolio", 0, "shares", True),
            ]:
                with self.subTest(bucket=bucket, index=index, field=field, value=value):
                    with self.assertRaises(ValueError):
                        store_constituent_value(assets, bucket, index, field, value)
            self.assertEqual(assets.read_text(encoding="utf-8"), before)

    def test_rejects_locked_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            before = assets.read_text(encoding="utf-8")
            # Oskar shares are locked...
            with self.assertRaises(ValueError):
                store_constituent_value(assets, "cash_portfolio", 1, "shares", "5")
            # ...as is an Oskar value cell.
            with self.assertRaises(ValueError):
                store_constituent_value(
                    assets, "cash_portfolio", 1, "value", "5"
                )
            self.assertEqual(assets.read_text(encoding="utf-8"), before)

    def test_value_editable_wherever_shares_are(self) -> None:
        from visual.constituents import updatable_fields

        self.assertIn(
            "value",
            updatable_fields(
                "equity_portfolio",
                {"name": "X", "broker": "scalable", "ISIN": "IE00X"},
            ),
        )
        self.assertNotIn(
            "value",
            updatable_fields(
                "equity_portfolio",
                {"name": "X", "broker": "oskar", "ISIN": "IE00X"},
            ),
        )


class TestNumberFormatting(unittest.TestCase):
    def _page(self) -> str:
        assets = {
            "equity_portfolio": [
                {
                    "name": "Decimals",
                    "shares": 220.0,
                    "value": 500.50,
                    "broker": "scalable",
                    "ISIN": "IE00X",
                }
            ]
        }
        cache = {"IE00X": {"price": 81.256}}
        with tempfile.TemporaryDirectory() as tmp:
            assets_path = Path(tmp) / "assets.json"
            cache_path = Path(tmp) / "cache.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            sections = load_constituents(assets_path, cache_path)
        return render_constituents_page(sections)

    def test_exactly_two_decimals(self) -> None:
        page = self._page()
        self.assertIn(">81.26<", page)
        self.assertIn('value="500.50"', page)
        self.assertIn('value="220.00"', page)
        self.assertNotIn("81.256", page)

    def test_euro_suffix_outside_value_and_price_boxes(self) -> None:
        page = self._page()
        self.assertEqual(page.count('<span class="unit">Euro</span>'), 2)
        self.assertIn(
            '<input class="cell-box editable" value="500.50" '
            'data-field="value" data-bucket="equity_portfolio" data-index="0" '
            'data-original="500.50" aria-label="value" /> '
            '<span class="unit">Euro</span>',
            page,
        )
        self.assertIn(
            '<span class="cell-box locked" data-field="price">81.26</span> '
            '<span class="unit">Euro</span>',
            page,
        )

    def test_headers_right_justified_with_fixed_columns(self) -> None:
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("th,\ntd {", css)
        self.assertIn("width: 6rem", css)
        self.assertIn("input.cell-box.editable", css)

    def test_alte_leipziger_broker_mark(self) -> None:
        assets = {
            "equity_portfolio": [
                {
                    "name": "AL Fund",
                    "shares": 5,
                    "value": 100,
                    "broker": "alte-leipziger",
                    "ISIN": None,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            assets_path = Path(tmp) / "assets.json"
            cache_path = Path(tmp) / "cache.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            sections = load_constituents(assets_path, cache_path)
        page = render_constituents_page(sections)
        self.assertIn('src="icons/alte-leipziger.png"', page)

    def test_check24_broker_mark(self) -> None:
        assets = {
            "equity_portfolio": [
                {
                    "name": "Check ETF",
                    "shares": 5,
                    "value": 100,
                    "broker": "check24",
                    "ISIN": None,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            assets_path = Path(tmp) / "assets.json"
            cache_path = Path(tmp) / "cache.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            sections = load_constituents(assets_path, cache_path)
        page = render_constituents_page(sections)
        self.assertIn('src="icons/check24.png"', page)

    def test_name_cell_right_aligned_with_fixed_columns(self) -> None:
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("table-layout: fixed", css)
        self.assertIn("td.name", css)
        self.assertIn("text-align: right", css)
        with tempfile.TemporaryDirectory() as tmp:
            assets, cache = _write_files(Path(tmp))
            sections = load_constituents(assets, cache)
        page = render_constituents_page(sections)
        self.assertIn('<td class="name"', page)

    def test_everything_right_justified(self) -> None:
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "styles.css"
        ).read_text(encoding="utf-8")
        # Headers, cells and box contents all align right.
        self.assertRegex(css, r"th,\s*\ntd \{\s*\n\s*text-align: right;")
        self.assertIn(".cell-box {", css)
        box_rule = css.split(".cell-box {", 1)[1].split("}", 1)[0]
        self.assertIn("text-align: right", box_rule)

    def test_masthead_keeps_title_and_buttons_in_reserved_columns(self) -> None:
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "styles.css"
        ).read_text(encoding="utf-8")
        # Title column shrinks, button column never overlaps and wraps.
        self.assertIn(".masthead h1 {", css)
        self.assertIn("justify-content: space-between", css)
        self.assertIn("flex-wrap: wrap", css)
        self.assertIn("white-space: nowrap", css)


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

    def _post(self, payload: dict) -> tuple[int, str]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/constituents",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_post_edit_persists_to_disk(self) -> None:
        status, body = self._post(
            {
                "bucket": "equity_portfolio",
                "index": 0,
                "field": "shares",
                "value": "230.5",
            }
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["shares"], 230.5)
        self.assertAlmostEqual(payload["value"], 230.5 * 81.25)
        self.assertTrue(payload["recomputed"])
        data = json.loads(self.assets.read_text(encoding="utf-8"))
        self.assertEqual(data["equity_portfolio"][0]["shares"], 230.5)
        self.assertAlmostEqual(data["equity_portfolio"][0]["value"], 230.5 * 81.25)

    def test_post_empty_means_zero(self) -> None:
        status, body = self._post(
            {"bucket": "cash_portfolio", "index": 0, "field": "value", "value": ""}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body), {"value": 0.0, "shares": None, "recomputed": True}
        )

    def test_post_rejects_junk_and_leaves_file_untouched(self) -> None:
        before = self.assets.read_text(encoding="utf-8")
        for payload in [
            {"bucket": "nope", "index": 0, "field": "shares", "value": "1"},
            {"bucket": "equity_portfolio", "index": 9, "field": "shares", "value": "1"},
            {"bucket": "equity_portfolio", "index": 0, "field": "price", "value": "1"},
            {"bucket": "equity_portfolio", "index": 0, "field": "shares", "value": "abc"},
            # Forged edit of a locked Oskar cell.
            {"bucket": "cash_portfolio", "index": 1, "field": "shares", "value": "5"},
        ]:
            with self.subTest(payload=payload):
                status, _ = self._post(payload)
                self.assertEqual(status, 400)
        self.assertEqual(self.assets.read_text(encoding="utf-8"), before)

    def test_post_unknown_endpoint_is_404(self) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/nope",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def _post_update(self, payload: dict | None = None) -> tuple[int, str]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/update",
            data=json.dumps(payload if payload is not None else {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_post_update_runs_lite_refresh_at_error_level(self) -> None:
        import logging
        from unittest.mock import patch

        seen = {}

        def fake_main(ctx) -> None:
            seen["config"] = ctx.config
            seen["level"] = logging.getLogger().level

        before = logging.getLogger().level
        with patch("allocation.main", side_effect=fake_main):
            status, body = self._post_update()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"updated": True})
        config = seen["config"]
        self.assertFalse(config.fetch_prices)
        self.assertFalse(config.fetch_geosplit)
        self.assertFalse(config.fetch_sectorsplit)
        self.assertFalse(config.fetch_oskar)
        self.assertFalse(config.fetch_scalable)
        self.assertFalse(config.fetch_traderepublic)
        self.assertTrue(config.plot_clear)
        self.assertFalse(config.plot_incognito)
        self.assertEqual(seen["level"], logging.ERROR)
        self.assertEqual(logging.getLogger().level, before)
        self.assertEqual(str(config.assets_file), str(self.assets))
        self.assertEqual(str(config.cache_file), str(self.cache))

    def test_post_update_failure_is_500(self) -> None:
        from unittest.mock import patch

        with patch("allocation.main", side_effect=RuntimeError("boom")):
            status, body = self._post_update()
        self.assertEqual(status, 500)
        self.assertIn("boom", body)
        self.assertNotIn("Traceback", body)

    def test_post_update_fat_mode_sets_fetch_and_both_plots(self) -> None:
        import logging
        from unittest.mock import patch

        seen = {}

        def fake_main(ctx) -> None:
            seen["config"] = ctx.config
            seen["level"] = logging.getLogger().level

        before = logging.getLogger().level
        with patch("allocation.main", side_effect=fake_main):
            status, body = self._post_update({"mode": "fat"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"updated": True})
        config = seen["config"]
        self.assertTrue(config.fetch_prices)
        self.assertTrue(config.fetch_geosplit)
        self.assertTrue(config.fetch_sectorsplit)
        self.assertFalse(config.fetch_oskar)
        self.assertFalse(config.fetch_scalable)
        self.assertFalse(config.fetch_traderepublic)
        self.assertTrue(config.plot_clear)
        self.assertTrue(config.plot_incognito)
        self.assertEqual(seen["level"], logging.ERROR)
        self.assertEqual(logging.getLogger().level, before)

    def test_post_update_garbage_body_falls_back_to_lite(self) -> None:
        from unittest.mock import patch

        seen = {}

        def fake_main(ctx) -> None:
            seen["config"] = ctx.config

        with patch("allocation.main", side_effect=fake_main):
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/update",
                data=b"not json{{{",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        self.assertEqual(status, 200)
        self.assertFalse(seen["config"].fetch_prices)
        self.assertTrue(seen["config"].plot_clear)
        self.assertFalse(seen["config"].plot_incognito)

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


class TestUpdateCancellation(unittest.TestCase):
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

    def _post_path(self, path: str, payload: dict | None = None) -> tuple[int, str]:
        data = (
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else b"{}"
        )
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_cancel_without_job(self) -> None:
        status, body = self._post_path("/api/cancel")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"cancelled": False})

    def test_overlapping_update_returns_409(self) -> None:
        import threading as _threading
        from unittest.mock import patch

        entered = _threading.Event()
        release = _threading.Event()
        self.addCleanup(release.set)

        def fake_main(ctx) -> None:
            entered.set()
            release.wait(timeout=30)

        results: dict = {}

        def first() -> None:
            try:
                with patch("allocation.main", side_effect=fake_main):
                    results["first"] = self._post_path("/api/update")
            except Exception as exc:  # never lose thread errors silently
                results["error"] = exc

        thread = _threading.Thread(target=first, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=30))
            with patch("allocation.main") as no_run:
                status, body = self._post_path("/api/update")
                no_run.assert_not_called()
            self.assertEqual(status, 409)
            self.assertIn("already in progress", body)
        finally:
            release.set()
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "first update thread did not finish")
        self.assertNotIn("error", results)
        self.assertEqual(results["first"][0], 200)

    def test_cancel_stops_running_update(self) -> None:
        import threading as _threading
        from unittest.mock import patch

        from position.factory import UpdateCancelled

        entered = _threading.Event()

        def fake_main(ctx) -> None:
            self.assertIsNotNone(ctx.cancel_event)
            entered.set()
            if not ctx.cancel_event.wait(timeout=30):
                raise RuntimeError("cancel never arrived")
            raise UpdateCancelled("cancelled by user")

        results: dict = {}

        def run_update() -> None:
            try:
                with patch("allocation.main", side_effect=fake_main):
                    results["update"] = self._post_path("/api/update")
            except Exception as exc:  # never lose thread errors silently
                results["error"] = exc

        thread = _threading.Thread(target=run_update, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=30))
            status, body = self._post_path("/api/cancel")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"cancelled": True})
            thread.join(timeout=30)
        finally:
            if thread.is_alive():
                # Never leave a wedged server thread behind.
                self.fail("update thread did not finish after cancel")
        self.assertNotIn("error", results)
        status, body = results["update"]
        self.assertEqual(status, 409)
        self.assertIn("cancelled", body)


if __name__ == "__main__":
    unittest.main()
