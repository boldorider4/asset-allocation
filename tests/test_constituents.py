# SPDX-License-Identifier: AGPL-3.0-or-later
"""Constituents endpoint: sections, cells, icons, and the /constituents route."""

from __future__ import annotations

import functools
import http.server
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from visual.web.backend.constituents import (
    add_constituent,
    delete_constituent,
    known_brokers,
    load_constituents,
    render_constituents_page,
    reorder_constituents,
    store_constituent_value,
)
from visual.web.backend.serve import DashboardHandler

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


class _StubUpdateProcess:
    """Test double for the spawned update worker.

    Runs the target in a thread so ``cli.update.main`` patches apply.
    ``terminate``/``kill`` only mark the call; real stopping comes from
    the cancel event, mirroring a graceful child shutdown.
    """

    def __init__(self, target, args) -> None:
        self._target = target
        self._args = args
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self.exitcode: int | None = None
        self.terminate_called = False
        self.kill_called = False

    def start(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)
        if not self._thread.is_alive() and self.exitcode is None:
            self.exitcode = 0

    def terminate(self) -> None:
        self.terminate_called = True

    def kill(self) -> None:
        self.kill_called = True


_SPAWNED_STUBS: list[_StubUpdateProcess] = []


def _stub_spawn_update_process(target, args) -> _StubUpdateProcess:
    proc = _StubUpdateProcess(target, args)
    _SPAWNED_STUBS.append(proc)
    proc.start()
    return proc


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
        # Editability follows the ISIN, not the broker: the Oskar row has
        # one, so its shares are editable...
        self.assertTrue(cash[1]["editable_shares"])
        self.assertTrue(cash[1]["editable_value"])
        # ...while the ISIN-less Tagesgeld row locks shares regardless of broker.
        self.assertFalse(cash[0]["editable_shares"])
        self.assertTrue(cash[0]["editable_value"])
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
            / "frontend"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("overflow-x: auto", css)
        self.assertIn(".table-scroll table", css)

    def test_cells_and_placeholders(self) -> None:
        page = self._page()
        self.assertIn("Amundi Core", page)
        # Editable shares for rows carrying an ISIN.
        self.assertIn('class="cell-box editable"', page)
        # Locked value and price boxes.
        self.assertIn('class="cell-box locked"', page)
        # Null value renders as an em-dash, never "None".
        self.assertIn("—", page)
        self.assertNotIn(">None<", page)

    def test_label_and_isin_columns(self) -> None:
        assets = {
            "equity_portfolio": [
                {
                    "name": "Amundi Core",
                    "short_name": "Amundi",
                    "shares": 220,
                    "value": None,
                    "broker": "scalable",
                    "ISIN": "IE000BI8OT95",
                },
                {
                    "name": "<evil> & co",
                    "short_name": None,
                    "shares": 10,
                    "value": 500.5,
                    "broker": "traderepublic",
                    "ISIN": "IE00B4YBJ215",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            assets_path = Path(tmp) / "assets.json"
            cache_path = Path(tmp) / "cache.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            cache_path.write_text("{}", encoding="utf-8")
            sections = load_constituents(assets_path, cache_path)
        equity = dict(sections)["Equity"]
        self.assertEqual(equity[0]["short_name"], "Amundi")
        self.assertEqual(equity[0]["isin"], "IE000BI8OT95")
        page = render_constituents_page(sections)
        self.assertIn(
            '<th class="grip-head" aria-hidden="true"></th>'
            "<th>Name</th><th>Group</th><th>ISIN</th>"
            "<th>Value</th><th>Shares</th><th>Price</th><th>Broker</th>"
            '<th class="trash-head" aria-hidden="true"></th>',
            page,
        )
        # Label maps short_name into an always-editable box...
        self.assertIn(
            '<input class="cell-box editable text" value="Amundi" '
            'data-field="short_name"',
            page,
        )
        # ...missing short_name renders as an empty box, never "None".
        self.assertIn('value="" data-field="short_name"', page)
        # Boxes cap accepted characters (Name is plain text, not a box).
        short_name_tag = page.split('data-field="short_name"')[1].split("/>")[0]
        self.assertIn('maxlength="32"', short_name_tag)
        shares_tag = page.split('data-field="shares"')[1].split("/>")[0]
        self.assertIn('maxlength="16"', shares_tag)
        # ISIN renders as plain text like Name, never a box or an input.
        self.assertIn("<td>IE000BI8OT95</td>", page)
        self.assertNotIn('data-field="ISIN"', page)
        # Evil names stay escaped in both name and label cells.
        self.assertIn("&lt;evil&gt; &amp; co", page)
        self.assertNotIn("<evil>", page)

    def test_rows_carry_grip_and_position(self) -> None:
        page = self._page()
        # One grip cell per body row (5 rows across the fixture buckets).
        self.assertEqual(page.count('<td class="grip-cell">'), 5)
        self.assertIn(
            '<span class="grip" title="Drag to reorder" aria-hidden="true">≡</span>',
            page,
        )
        # Rows expose their address for the drag permutation.
        self.assertIn(
            '<tr data-bucket="equity_portfolio" data-index="0">', page
        )
        self.assertIn(
            '<tr data-bucket="cash_portfolio" data-index="1">', page
        )
        # One trash button per body row, addressed like the inputs.
        self.assertEqual(page.count('<td class="trash-cell">'), 5)
        self.assertIn(
            '<button type="button" class="trash" '
            'data-bucket="equity_portfolio" data-index="0"',
            page,
        )
        self.assertIn('aria-label="Delete Amundi Core"', page)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("th:nth-child(8),", css)
        self.assertIn(".grip {", css)
        self.assertIn("touch-action: none", css)

    def test_sections_offer_add_row_button(self) -> None:
        page = self._page()
        # One + button per section (3 fixture sections), addressed by bucket.
        self.assertEqual(page.count('class="add-row"'), 3)
        self.assertIn(
            '<button type="button" class="add" data-bucket="equity_portfolio"',
            page,
        )
        self.assertIn(
            '<button type="button" class="add" data-bucket="cash_portfolio"',
            page,
        )
        # No draft rows in server-rendered HTML; JS builds them on demand.
        self.assertNotIn("tr.draft", page)
        self.assertNotIn("broker-select", page)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".add-row", css)
        self.assertIn("tr.draft td", css)
        self.assertIn("select.broker-select", css)
        self.assertIn("input.cell-box.editable:disabled", css)
        self.assertIn("input.cell-box.draft-isin", css)

    def test_shares_locked_only_without_isin(self) -> None:
        page = self._page()
        self.assertNotIn('data-field="shares" readonly', page)
        # The Oskar row carries an ISIN, so the broker does not lock its
        # shares: they render as an editable input like any ISIN row.
        self.assertIn(
            '<input class="cell-box editable" value="80.00" '
            'data-field="shares"',
            page,
        )
        # Rows without an ISIN lock shares as "-" instead.
        self.assertIn(
            '<span class="cell-box locked" data-field="shares">-</span>', page
        )

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
        # Pensions carry no ISIN, so shares/price lock as "-"; the bucket
        # itself plays no role in the rule.
        assets = {
            "pension_portfolio": [
                {
                    "name": "bAV",
                    "shares": 10,
                    "value": 5000,
                    "broker": "scalable",
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

    def test_broker_does_not_affect_editability(self) -> None:
        # check24 rows carrying an ISIN behave like any other ISIN row, in
        # every bucket: editable shares and value, locked quoted price.
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
        self.assertTrue(by_label["Fixed Maturity"][0]["editable_shares"])
        self.assertTrue(by_label["Equity"][0]["editable_shares"])
        self.assertFalse(by_label["Fixed Maturity"][0]["no_quote"])
        self.assertFalse(by_label["Equity"][0]["no_quote"])
        page = render_constituents_page(sections)
        self.assertEqual(
            page.count(
                '<input class="cell-box editable" value="7.00" data-field="shares"'
            ),
            2,
        )
        self.assertEqual(
            page.count('<span class="cell-box locked" data-field="price">10.00</span>'),
            2,
        )
        # Value stays editable with the booked amount.
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

    def test_page_uses_dashboard_favicon(self) -> None:
        page = self._page()
        self.assertIn(
            '<link rel="icon" type="image/png" href="favicon.png" />', page
        )

    def test_overview_button_top_right(self) -> None:
        page = self._page()
        self.assertIn('<div class="nav-buttons">', page)
        self.assertIn(
            '<a id="overview-link" class="nav-button" href="/dashboard">Dashboard</a>',
            page,
        )
        self.assertIn('id="update-status"', page)

    def test_overview_button_holds_incognito_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets, cache = _write_files(Path(tmp))
            sections = load_constituents(assets, cache)
        self.assertIn(
            '<a id="overview-link" class="nav-button" href="/dashboard">Dashboard</a>',
            render_constituents_page(sections),
        )
        self.assertIn(
            '<a id="overview-link" class="nav-button" '
            'href="/dashboard?incognito=true">Dashboard</a>',
            render_constituents_page(sections, incognito=True),
        )

    def test_constituents_has_incognito_button_left_of_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets, cache = _write_files(Path(tmp))
            sections = load_constituents(assets, cache)
        plain = render_constituents_page(sections)
        veiled = render_constituents_page(sections, incognito=True)
        for page in (plain, veiled):
            incognito_pos = page.index('id="incognito-link"')
            overview_pos = page.index('id="overview-link"')
            self.assertLess(incognito_pos, overview_pos)
            self.assertIn('class="incognito-glyph"', page)
        self.assertIn('href="/constituents?incognito=true"', plain)
        self.assertIn('aria-pressed="true"', veiled)
        self.assertIn('href="/constituents"', veiled)

    def test_incognito_blur_is_frontend_only(self) -> None:
        root = Path(__file__).resolve().parent.parent / "visual" / "web" / "frontend"
        app_js = (root / "app.js").read_text(encoding="utf-8")
        # Euro figures are wrapped in blur-able spans; labels, pcts, and
        # card titles never get the class.
        self.assertIn('"euro"', app_js)
        self.assertIn("appendFigureSpans", app_js)
        dashboard_js = (root / "dashboard.js").read_text(encoding="utf-8")
        # Instant toggle: no reload, state via replaceState + body class.
        self.assertIn("replaceState", dashboard_js)
        self.assertIn('classList.toggle("incognito"', dashboard_js)
        self.assertIn("applyIncognitoState", dashboard_js)
        constituents_js = (root / "constituents.js").read_text(encoding="utf-8")
        self.assertIn("replaceState", constituents_js)
        self.assertIn('classList.toggle("incognito"', constituents_js)
        self.assertIn("wireIncognitoToggle", constituents_js)
        css = (root / "styles.css").read_text(encoding="utf-8")
        self.assertIn("body.incognito .euro", css)
        self.assertIn("blur(", css)
        self.assertIn('input[data-field="value"]', css)
        self.assertIn('input[data-field="shares"]', css)
        self.assertIn('span.cell-box.locked[data-field="value"]', css)
        self.assertIn('span.cell-box.locked[data-field="shares"]', css)
        # Price figures are never blurred.
        self.assertNotIn('data-field="price"', css)

    def test_view_switcher_swaps_without_reload(self) -> None:
        root = Path(__file__).resolve().parent.parent / "visual" / "web" / "frontend"
        self.assertTrue((root / "switcher.js").is_file())
        switcher = (root / "switcher.js").read_text(encoding="utf-8")
        # Single-document hash routing: view state in #constituents, no
        # document reload on the hot path, classic navigation fallback.
        self.assertIn("__switchView", switcher)
        self.assertIn("body.innerHTML", switcher)
        self.assertIn("hashchange", switcher)
        self.assertIn("#constituents", switcher)
        self.assertIn("window.location.href", switcher)
        # Fresh loads boot the dashboard and strip any hash; the
        # incognito query (backend-tracked) is never touched by this.
        self.assertIn("replaceState", switcher)
        self.assertIn("location.hash", switcher)
        # Freshness: dashboard verifies on show, constituents revalidates
        # in background without clobbering drafts or focused inputs.
        self.assertIn("__dashboardShow", switcher)
        self.assertIn("__constituentsRevalidate", switcher)
        self.assertIn("tr.draft", switcher)
        self.assertIn("activeElement", switcher)
        # Rapid clicks can't apply views out of order.
        self.assertIn("navSeq", switcher)
        # Stash is keyed explicitly (hash already names the new view when
        # hashchange fires), never by "current view".
        self.assertIn("stashLeaving", switcher)
        self.assertIn("otherView", switcher)
        # Toggles preserve the view hash when rewriting the query.
        dashboard_js = (root / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("window.location.hash", dashboard_js)
        constituents_js = (root / "constituents.js").read_text(encoding="utf-8")
        self.assertIn("window.location.hash", constituents_js)
        # Both pages load all scripts; each boots behind its own marker.
        index = (root / "index.html").read_text(encoding="utf-8")
        for tag in (
            '<script src="app.js"></script>',
            '<script src="dashboard.js"></script>',
            '<script src="constituents.js"></script>',
            '<script src="switcher.js"></script>',
        ):
            self.assertIn(tag, index)
        with tempfile.TemporaryDirectory() as tmp:
            assets, cache = _write_files(Path(tmp))
            sections = load_constituents(assets, cache)
        page = render_constituents_page(sections)
        for tag in (
            '<script src="app.js"></script>',
            '<script src="dashboard.js"></script>',
            '<script src="constituents.js"></script>',
            '<script src="switcher.js"></script>',
        ):
            self.assertIn(tag, page)
        app_js = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn("__dashboardShow", app_js)
        self.assertIn("galleryEl()", app_js)
        self.assertNotIn("const GALLERY =", app_js)
        # Footer stamp is idempotent: versionText strips a previous
        # "Last updated" suffix instead of stacking it every poll.
        self.assertIn("Last updated:", app_js)
        self.assertNotIn("dataset.wired", app_js)
        dashboard_js = (root / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("__dashboardInit", dashboard_js)
        self.assertIn("__switchView", dashboard_js)
        self.assertIn("sync-link", dashboard_js)
        constituents_js = (root / "constituents.js").read_text(encoding="utf-8")
        self.assertIn("__constituentsInit", constituents_js)
        self.assertIn("__switchView", constituents_js)
        self.assertIn("overview-link", constituents_js)
        # Bind guards are identity-based: dataset markers would serialize
        # into the cached view HTML and leave restored nodes unbound
        # (clicks falling through to full reloads after one round trip).
        for js in (dashboard_js, constituents_js):
            self.assertIn("WeakSet", js)
            self.assertNotIn("dataset.wired", js)

    def test_instant_switch_prefetch_and_snapshot(self) -> None:
        root = Path(__file__).resolve().parent.parent / "visual" / "web" / "frontend"
        app_js = (root / "app.js").read_text(encoding="utf-8")
        # Raw chart payloads load in parallel, not one await per file.
        self.assertIn("Promise.all", app_js)
        self.assertNotIn("for (const file of files)", app_js)
        # Gallery snapshot paints instantly, then verifies in background.
        self.assertIn("sessionStorage", app_js)
        self.assertIn("restoreSnapshot", app_js)
        self.assertIn("saveSnapshot", app_js)
        self.assertIn("lastSignature", app_js)
        # Idle prefetch of the constituents document (default cache mode).
        self.assertIn("prefetch-constituents", app_js)
        self.assertIn("requestIdleCallback", app_js)
        dashboard_js = (root / "dashboard.js").read_text(encoding="utf-8")
        # Edit leaves without awaiting the cancel; keepalive delivers it.
        self.assertIn("keepalive", dashboard_js)
        self.assertNotIn('await fetch("/api/cancel"', dashboard_js)
        # Toggle keeps the prefetched URL on the current state.
        self.assertIn("prefetch-constituents", dashboard_js)
        constituents_js = (root / "constituents.js").read_text(encoding="utf-8")
        self.assertIn("prefetch-dashboard", constituents_js)
        self.assertIn("requestIdleCallback", constituents_js)

    def test_blocking_update_overlay(self) -> None:
        page = self._page()
        self.assertIn('id="update-overlay" class="overlay" hidden', page)
        self.assertIn("Updating charts…", page)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
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
            / "frontend"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="sync-link"', index)
        self.assertIn('aria-label="Sync Prices"', index)
        self.assertIn('class="sync-glyph"', index)
        self.assertTrue(
            (
                Path(__file__).resolve().parent.parent
                / "visual"
                / "web"
                / "frontend"
                / "icons"
                / "sync.svg"
            ).is_file()
        )
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".nav-button.icon-button", css)
        # The glyph reuses the asset as a mask so it matches the Edit
        # button text color, tinting gold only on hover.
        self.assertIn('url("icons/sync.svg")', css)
        self.assertIn(".nav-button.icon-button:hover .sync-glyph", css)
        self.assertIn('<script src="dashboard.js"></script>', index)

    def test_dashboard_edit_link_cancels_then_navigates(self) -> None:
        index = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="edit-link"', index)
        dashboard_js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "dashboard.js"
        ).read_text(encoding="utf-8")
        self.assertIn("/api/cancel", dashboard_js)
        self.assertIn("/constituents", dashboard_js)
        # Edit navigates via the link href (which carries ?incognito=true
        # when active) instead of a hardcoded path.
        self.assertIn('getAttribute("href")', dashboard_js)
        dashboard_js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
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
            / "frontend"
            / "constituents.js"
        ).read_text(encoding="utf-8")
        # A successful save stages an update...
        self.assertIn("let dirty = false", js)
        self.assertIn("dirty = true", js)
        # ...while a clean Overview navigates straight to the dashboard.
        self.assertIn("if (!dirty)", js)
        self.assertIn("window.location.href = target", js)
        # Shares/value edits, short_name edits, deletes, and adds stage an update:
        # one dirty flag in the pair-refresh path, one in the short_name path,
        # one in the delete flow, one in the add flow. Reorders persist silently.
        self.assertEqual(js.count("dirty = true"), 4)
        # In the pair-refresh path (shares/value), dirty is set after refreshPair.
        pair_refresh = js.split("const updated = refreshPair(input, data);")[1].split(
            "if (data && data.recomputed === false)"
        )[0]
        self.assertIn("dirty = true", pair_refresh)
        delete_flow = js.split("async function deleteRow")[1].split(
            "document.addEventListener("
        )[0]
        self.assertIn("dirty = true", delete_flow)
        add_flow = js.split("async function confirmDraft")[1].split(
            "document.addEventListener("
        )[0]
        self.assertIn("dirty = true", add_flow)
        persist = js.split("async function persistOrder")[1].split(
            "document.addEventListener("
        )[0]
        self.assertNotIn("dirty", persist)
        label_flow = js.split('field === "short_name"')[1].split(
            "const updated = refreshPair"
        )[0]
        self.assertIn("dirty = true", label_flow)

    def test_rows_drag_to_reorder_and_persist(self) -> None:
        js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "constituents.js"
        ).read_text(encoding="utf-8")
        # Handle-initiated Pointer Events drag, same-tbody drops only...
        self.assertIn('closest(".grip")', js)
        self.assertIn("setPointerCapture", js)
        self.assertIn("pointercancel", js)
        self.assertIn("/api/constituents/order", js)
        # ...with index rewrite after persist and DOM revert on failure.
        self.assertIn('querySelectorAll("input[data-index]")', js)
        self.assertIn("Reorder failed: ", js)
        # Trash buttons delete via their own endpoint and stage an update.
        self.assertIn('closest("button.trash")', js)
        self.assertIn("async function deleteRow", js)
        self.assertIn("/api/constituents/delete", js)
        self.assertIn("Delete failed: ", js)
        # Draft rows: broker catalog, ISIN check unlocking shares, OK persist.
        self.assertIn("button.add", js)
        self.assertIn("button.ok", js)
        self.assertIn("select.broker-select", js)
        self.assertIn("tr.draft", js)
        self.assertIn("/api/constituents/brokers", js)
        self.assertIn("/api/constituents/check-isin", js)
        self.assertIn("/api/constituents/add", js)
        self.assertIn("Add failed: ", js)
        # Draft value/price cells show the Euro unit like finalized rows.
        self.assertIn("unitSpan", js)
        # Draft fields are editable from the start (shares still gated
        # on the ISIN check); OK enables only for persistable drafts.
        self.assertIn("refreshOkState", js)
        self.assertIn('setAttribute("disabled"', js)
        self.assertIn('removeAttribute("disabled")', js)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".ok:disabled", css)

    def test_save_refreshes_pair_with_red_flare_fallback(self) -> None:
        js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
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
            / "frontend"
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

    def test_short_name_edit_persists_without_recompute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            result = store_constituent_value(
                assets, "equity_portfolio", 0, "short_name", "Amundi"
            )
            self.assertEqual(result["short_name"], "Amundi")
            self.assertTrue(result["recomputed"])
            data = self._read(assets)
            self.assertEqual(data["equity_portfolio"][0]["short_name"], "Amundi")
            # Numbers are untouched by a label edit.
            self.assertEqual(data["equity_portfolio"][0]["shares"], 220)
            self.assertIsNone(data["equity_portfolio"][0]["value"])

    def test_short_name_editable_even_on_locked_rows(self) -> None:
        from visual.web.backend.constituents import updatable_fields

        # ISIN-less rows lock shares, but the label stays editable.
        self.assertIn(
            "short_name",
            updatable_fields(
                "cash_portfolio",
                {"name": "Tagesgeld", "broker": "scalable", "ISIN": None},
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            result = store_constituent_value(
                assets, "cash_portfolio", 1, "short_name", "Oskar ETF"
            )
            self.assertEqual(result["short_name"], "Oskar ETF")
            data = self._read(assets)
            self.assertEqual(data["cash_portfolio"][1]["short_name"], "Oskar ETF")
            self.assertEqual(data["cash_portfolio"][1]["shares"], 80)

    def test_rejects_bad_address_field_and_junk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            before = assets.read_text(encoding="utf-8")
            for bucket, index, field, value in [
                ("nope", 0, "shares", "1"),
                ("equity_portfolio", 99, "shares", "1"),
                ("equity_portfolio", 0, "price", "1"),
                ("equity_portfolio", 0, "ISIN", "IE00X"),
                ("equity_portfolio", 0, "shares", "abc"),
                ("equity_portfolio", 0, "shares", float("nan")),
                ("equity_portfolio", 0, "shares", True),
                ("equity_portfolio", 0, "short_name", True),
                ("equity_portfolio", 0, "short_name", 123),
                ("equity_portfolio", 0, "short_name", "x" * 33),
                ("equity_portfolio", 0, "shares", "1" * 17),
            ]:
                with self.subTest(bucket=bucket, index=index, field=field, value=value):
                    with self.assertRaises(ValueError):
                        store_constituent_value(assets, bucket, index, field, value)
            self.assertEqual(assets.read_text(encoding="utf-8"), before)

    def test_rejects_locked_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            # Shares without an ISIN are locked...
            with self.assertRaises(ValueError):
                store_constituent_value(assets, "cash_portfolio", 0, "shares", "5")
            # ...but value stays editable on the very same row...
            result = store_constituent_value(
                assets, "cash_portfolio", 0, "value", "150"
            )
            self.assertEqual(result["value"], 150.0)
            # ...and shares are editable wherever an ISIN exists,
            # regardless of broker.
            result = store_constituent_value(
                assets, "cash_portfolio", 1, "shares", "81", {}
            )
            self.assertEqual(result["shares"], 81.0)
            self.assertFalse(result["recomputed"])
            # Only the two accepted edits touched the file.
            data = self._read(assets)
            self.assertEqual(data["cash_portfolio"][0]["value"], 150.0)
            self.assertEqual(data["cash_portfolio"][1]["shares"], 81.0)
            self.assertEqual(data["cash_portfolio"][1]["value"], 10100)

    def test_editability_follows_isin_not_broker(self) -> None:
        from visual.web.backend.constituents import updatable_fields

        for broker in ("scalable", "oskar", "check24", "tf-bank"):
            with self.subTest(broker=broker):
                fields = updatable_fields(
                    "equity_portfolio",
                    {"name": "X", "broker": broker, "ISIN": "IE00X"},
                )
                self.assertEqual(fields, {"short_name", "value", "shares"})
                fields = updatable_fields(
                    "equity_portfolio",
                    {"name": "X", "broker": broker, "ISIN": None},
                )
                self.assertEqual(fields, {"short_name", "value"})
        # Blank strings count as missing.
        self.assertNotIn(
            "shares",
            updatable_fields(
                "equity_portfolio",
                {"name": "X", "broker": "scalable", "ISIN": "   "},
            ),
        )


class TestReorderConstituents(unittest.TestCase):
    def _assets(self, tmp: Path) -> Path:
        assets = tmp / "assets.json"
        assets.write_text(json.dumps(_ASSETS), encoding="utf-8")
        return assets

    def _names(self, assets: Path, bucket: str = "equity_portfolio") -> list:
        data = json.loads(assets.read_text(encoding="utf-8"))
        return [row["name"] for row in data[bucket]]

    def test_permute_persists_and_echoes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            result = reorder_constituents(assets, "equity_portfolio", [1, 0])
            self.assertEqual(result, {"order": [1, 0]})
            self.assertEqual(
                self._names(assets), ["Trade Republic ETF", "Amundi Core"]
            )
            # Other buckets are untouched.
            data = json.loads(assets.read_text(encoding="utf-8"))
            self.assertEqual(data["cash_portfolio"][0]["name"], "Tagesgeld")
            json.loads(assets.read_text(encoding="utf-8"))

    def test_identity_order_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            before = json.loads(assets.read_text(encoding="utf-8"))
            result = reorder_constituents(assets, "equity_portfolio", [0, 1])
            self.assertEqual(result, {"order": [0, 1]})
            self.assertEqual(
                json.loads(assets.read_text(encoding="utf-8")), before
            )

    def test_rejects_bad_orders_and_leaves_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            before = assets.read_text(encoding="utf-8")
            for bucket, order in [
                ("nope", [0, 1]),
                ("equity_portfolio", [0, 0]),
                ("equity_portfolio", [0]),
                ("equity_portfolio", [0, 1, 2]),
                ("equity_portfolio", [1]),
                ("equity_portfolio", []),
                ("equity_portfolio", "01"),
                ("equity_portfolio", [0, "1"]),
                ("equity_portfolio", [0, True]),
                ("equity_portfolio", None),
                ("equity_portfolio", [0, -1]),
            ]:
                with self.subTest(bucket=bucket, order=order):
                    with self.assertRaises(ValueError):
                        reorder_constituents(assets, bucket, order)
            self.assertEqual(assets.read_text(encoding="utf-8"), before)

    def test_rejects_non_list_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.json"
            assets.write_text(json.dumps({"equity_portfolio": {"a": 1}}))
            with self.assertRaises(ValueError):
                reorder_constituents(assets, "equity_portfolio", [0])


class TestKnownBrokers(unittest.TestCase):
    def test_five_icon_brokers_with_marks(self) -> None:
        brokers = known_brokers()
        self.assertEqual(
            [b["id"] for b in brokers],
            ["oskar", "scalable", "traderepublic", "check24", "alte-leipziger"],
        )
        for entry in brokers:
            self.assertIn(entry["id"], entry["mark"])
            self.assertTrue(
                "icons/" in entry["mark"] or "<svg" in entry["mark"]
            )


class TestAddConstituent(unittest.TestCase):
    def _assets(self, tmp: Path) -> Path:
        assets = tmp / "assets.json"
        assets.write_text(json.dumps(_ASSETS), encoding="utf-8")
        return assets

    def test_full_row_appends_and_reports_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            display = add_constituent(
                assets,
                "equity_portfolio",
                name="  New Fund  ",
                short_name="NF",
                isin="ie000bi8ot95",
                value="100.5",
                shares=7,
                broker="scalable",
                cache={"IE000BI8OT95": {"price": 81.25}},
            )
            self.assertEqual(display["index"], 2)
            self.assertEqual(display["name"], "New Fund")
            self.assertEqual(display["isin"], "IE000BI8OT95")
            self.assertEqual(display["price"], 81.25)
            self.assertTrue(display["editable_shares"])
            self.assertTrue(display["editable_value"])
            self.assertFalse(display["no_quote"])
            data = json.loads(assets.read_text(encoding="utf-8"))
            stored = data["equity_portfolio"][2]
            self.assertEqual(stored["name"], "New Fund")
            self.assertEqual(stored["ISIN"], "IE000BI8OT95")
            self.assertEqual(stored["shares"], 7.0)
            # Other buckets are untouched.
            self.assertEqual(len(data["cash_portfolio"]), 2)

    def test_minimal_row_defaults_to_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            display = add_constituent(
                assets, "cash_portfolio", name="Spare", broker="scalable",
            )
            self.assertEqual(display["index"], 2)
            self.assertIsNone(display["isin"])
            self.assertIsNone(display["value"])
            self.assertIsNone(display["shares"])
            self.assertTrue(display["no_quote"])
            self.assertFalse(display["editable_shares"])
            self.assertTrue(display["editable_value"])

    def test_rejects_junk_and_leaves_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            before = assets.read_text(encoding="utf-8")
            for kwargs in [
                {"name": "X", "broker": "nope"},
                {"name": "   ", "broker": "scalable"},
                {"name": "X", "broker": "scalable", "isin": "TOOSHORT"},
                {"name": "X", "broker": "scalable", "isin": "not an isin!!"},
                {"name": "X", "broker": "scalable", "value": "abc"},
                {"name": "X", "broker": "scalable", "shares": float("nan")},
                {"name": "X", "broker": "scalable", "short_name": True},
                {"broker": "scalable"},
            ]:
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        add_constituent(assets, "equity_portfolio", **kwargs)
            with self.assertRaises(ValueError):
                add_constituent(assets, "nope", name="X", broker="scalable")
            self.assertEqual(assets.read_text(encoding="utf-8"), before)


class TestDeleteConstituent(unittest.TestCase):
    def _assets(self, tmp: Path) -> Path:
        assets = tmp / "assets.json"
        assets.write_text(json.dumps(_ASSETS), encoding="utf-8")
        return assets

    def test_delete_removes_row_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            result = delete_constituent(assets, "equity_portfolio", 0)
            self.assertEqual(result, {"deleted": True, "rows": 1})
            data = json.loads(assets.read_text(encoding="utf-8"))
            self.assertEqual(
                [row["name"] for row in data["equity_portfolio"]],
                ["Trade Republic ETF"],
            )
            # Other buckets are untouched.
            self.assertEqual(len(data["cash_portfolio"]), 2)
            json.loads(assets.read_text(encoding="utf-8"))

    def test_delete_last_row_empties_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.json"
            assets.write_text(
                json.dumps({"equity_portfolio": [{"name": "Solo"}]})
            )
            result = delete_constituent(assets, "equity_portfolio", 0)
            self.assertEqual(result, {"deleted": True, "rows": 0})
            data = json.loads(assets.read_text(encoding="utf-8"))
            self.assertEqual(data["equity_portfolio"], [])

    def test_rejects_bad_address_and_leaves_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = self._assets(Path(tmp))
            before = assets.read_text(encoding="utf-8")
            for bucket, index in [
                ("nope", 0),
                ("equity_portfolio", 2),
                ("equity_portfolio", -1),
                ("equity_portfolio", "0"),
                ("equity_portfolio", True),
                ("equity_portfolio", None),
            ]:
                with self.subTest(bucket=bucket, index=index):
                    with self.assertRaises(ValueError):
                        delete_constituent(assets, bucket, index)
            self.assertEqual(assets.read_text(encoding="utf-8"), before)


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
            'data-original="500.50" aria-label="value" maxlength="16" /> '
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
            / "frontend"
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
            / "frontend"
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
            / "frontend"
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
            / "frontend"
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
        # Updates run in stubbed child processes (threads), so
        # ``cli.update.main`` patches apply and no real worker spawns.
        del _SPAWNED_STUBS[:]
        from unittest.mock import patch

        spawn_patch = patch(
            "visual.web.backend.serve._spawn_update_process",
            side_effect=_stub_spawn_update_process,
        )
        spawn_patch.start()
        self.addCleanup(spawn_patch.stop)
        # Isolate the stub worker from the real config file: the worker
        # reads ini defaults, which must not leak repo settings into tests.
        env_patch = patch.dict(os.environ, {"ASALLOC_CONFIG": str(tmp / "nope.ini")})
        env_patch.start()
        self.addCleanup(env_patch.stop)
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

    def test_constituents_holds_incognito_in_overview_link(self) -> None:
        status, body = self._get("/constituents?incognito=true")
        self.assertEqual(status, 200)
        self.assertIn(
            '<a id="overview-link" class="nav-button" '
            'href="/dashboard?incognito=true">Dashboard</a>',
            body,
        )
        status, body = self._get("/constituents")
        self.assertEqual(status, 200)
        self.assertIn(
            '<a id="overview-link" class="nav-button" href="/dashboard">Dashboard</a>',
            body,
        )

    def test_dashboard_has_incognito_button(self) -> None:
        index = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="incognito-link"', index)
        self.assertIn('href="/dashboard?incognito=true"', index)
        self.assertIn('aria-label="Incognito mode"', index)
        self.assertIn('class="incognito-glyph"', index)
        self.assertTrue(
            (
                Path(__file__).resolve().parent.parent
                / "visual"
                / "web"
                / "frontend"
                / "icons"
                / "incognito.svg"
            ).is_file()
        )
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".incognito-glyph", css)
        self.assertIn('url("icons/incognito.svg")', css)
        self.assertIn('[aria-pressed="true"]', css)
        dashboard_js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "dashboard.js"
        ).read_text(encoding="utf-8")
        self.assertIn("wireIncognitoToggle", dashboard_js)
        self.assertIn("URLSearchParams", dashboard_js)
        self.assertIn("aria-pressed", dashboard_js)
        self.assertIn("/constituents?incognito=true", dashboard_js)

    def test_overview_navigates_via_link_href(self) -> None:
        js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "constituents.js"
        ).read_text(encoding="utf-8")
        # The Dashboard href carries the gallery mode; the return trip
        # must use it instead of a hardcoded path.
        self.assertIn('link.getAttribute("href")', js)

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
            json.loads(body),
            {"value": 0.0, "shares": None, "short_name": None, "recomputed": True},
        )

    def test_post_short_name_persists_to_disk(self) -> None:
        status, body = self._post(
            {
                "bucket": "equity_portfolio",
                "index": 0,
                "field": "short_name",
                "value": "Amundi",
            }
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["short_name"], "Amundi")
        self.assertTrue(payload["recomputed"])
        data = json.loads(self.assets.read_text(encoding="utf-8"))
        self.assertEqual(data["equity_portfolio"][0]["short_name"], "Amundi")
        self.assertEqual(data["equity_portfolio"][0]["shares"], 220)

    def _post_order(self, payload: dict) -> tuple[int, str]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/constituents/order",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_post_order_persists_to_disk(self) -> None:
        status, body = self._post_order(
            {"bucket": "equity_portfolio", "order": [1, 0]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"order": [1, 0]})
        data = json.loads(self.assets.read_text(encoding="utf-8"))
        self.assertEqual(
            [row["name"] for row in data["equity_portfolio"]],
            ["Trade Republic ETF", "Amundi Core"],
        )

    def test_post_order_rejects_junk_and_leaves_file_untouched(self) -> None:
        before = self.assets.read_text(encoding="utf-8")
        for payload in [
            {"bucket": "nope", "order": [0, 1]},
            {"bucket": "equity_portfolio", "order": [0, 0]},
            {"bucket": "equity_portfolio", "order": [0]},
            {"bucket": "equity_portfolio", "order": "nope"},
            {"bucket": "equity_portfolio"},
        ]:
            with self.subTest(payload=payload):
                status, _ = self._post_order(payload)
                self.assertEqual(status, 400)
        self.assertEqual(self.assets.read_text(encoding="utf-8"), before)

    def _post_delete(self, payload: dict) -> tuple[int, str]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/constituents/delete",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_post_delete_persists_to_disk(self) -> None:
        status, body = self._post_delete(
            {"bucket": "equity_portfolio", "index": 0}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"deleted": True, "rows": 1})
        data = json.loads(self.assets.read_text(encoding="utf-8"))
        self.assertEqual(
            [row["name"] for row in data["equity_portfolio"]],
            ["Trade Republic ETF"],
        )

    def test_post_delete_rejects_junk_and_leaves_file_untouched(self) -> None:
        before = self.assets.read_text(encoding="utf-8")
        for payload in [
            {"bucket": "nope", "index": 0},
            {"bucket": "equity_portfolio", "index": 5},
            {"bucket": "equity_portfolio", "index": -1},
            {"bucket": "equity_portfolio", "index": "0"},
            {"bucket": "equity_portfolio"},
        ]:
            with self.subTest(payload=payload):
                status, _ = self._post_delete(payload)
                self.assertEqual(status, 400)
        self.assertEqual(self.assets.read_text(encoding="utf-8"), before)

    def _post_add(self, payload: dict) -> tuple[int, str]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/constituents/add",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_post_add_persists_and_returns_row(self) -> None:
        status, body = self._post_add(
            {
                "bucket": "equity_portfolio",
                "name": "New Fund",
                "short_name": "NF",
                "isin": "IE000BI8OT95",
                "value": 100.5,
                "shares": 7,
                "broker": "scalable",
            }
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["index"], 2)
        self.assertIn("<tr ", payload["row"])
        self.assertIn("New Fund", payload["row"])
        self.assertIn('data-index="2"', payload["row"])
        data = json.loads(self.assets.read_text(encoding="utf-8"))
        self.assertEqual(data["equity_portfolio"][2]["name"], "New Fund")
        self.assertEqual(data["equity_portfolio"][2]["ISIN"], "IE000BI8OT95")

    def test_post_add_rejects_junk_and_leaves_file_untouched(self) -> None:
        before = self.assets.read_text(encoding="utf-8")
        for payload in [
            {"bucket": "nope", "name": "X", "broker": "scalable"},
            {"bucket": "equity_portfolio", "name": "   ", "broker": "scalable"},
            {"bucket": "equity_portfolio", "name": "X", "broker": "nope"},
            {"bucket": "equity_portfolio", "name": "X", "broker": "scalable",
             "isin": "SHORT"},
            {"bucket": "equity_portfolio", "name": "X", "broker": "scalable",
             "value": "abc"},
            {"bucket": "equity_portfolio", "broker": "scalable"},
        ]:
            with self.subTest(payload=payload):
                status, _ = self._post_add(payload)
                self.assertEqual(status, 400)
        self.assertEqual(self.assets.read_text(encoding="utf-8"), before)

    def test_get_brokers_lists_marks(self) -> None:
        status, body = self._get("/api/constituents/brokers")
        self.assertEqual(status, 200)
        brokers = json.loads(body)["brokers"]
        self.assertEqual(len(brokers), 5)
        self.assertEqual(
            [b["id"] for b in brokers],
            ["oskar", "scalable", "traderepublic", "check24", "alte-leipziger"],
        )
        for entry in brokers:
            self.assertTrue("icons/" in entry["mark"] or "<svg" in entry["mark"])

    def _post_check_isin(self, payload: dict) -> tuple[int, str]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/constituents/check-isin",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def test_post_check_isin_reports_probe(self) -> None:
        from unittest.mock import patch

        with patch(
            "position.justetf_position.just_etf_product_url_exists",
            return_value=True,
        ):
            status, body = self._post_check_isin({"isin": "IE000BI8OT95"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"exists": True})
        with patch(
            "position.justetf_position.just_etf_product_url_exists",
            return_value=False,
        ):
            status, body = self._post_check_isin({"isin": "XX0000000000"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"exists": False})

    def test_post_check_isin_rejects_blank(self) -> None:
        for payload in [{"isin": "   "}, {"isin": 123}, {}]:
            with self.subTest(payload=payload):
                status, _ = self._post_check_isin(payload)
                self.assertEqual(status, 400)

    def test_post_rejects_junk_and_leaves_file_untouched(self) -> None:
        before = self.assets.read_text(encoding="utf-8")
        for payload in [
            {"bucket": "nope", "index": 0, "field": "shares", "value": "1"},
            {"bucket": "equity_portfolio", "index": 9, "field": "shares", "value": "1"},
            {"bucket": "equity_portfolio", "index": 0, "field": "price", "value": "1"},
            {"bucket": "equity_portfolio", "index": 0, "field": "shares", "value": "abc"},
            # Forged edit of a locked shares cell (row without ISIN).
            {"bucket": "cash_portfolio", "index": 0, "field": "shares", "value": "5"},
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
        with (
            patch("cli.update.main", side_effect=fake_main),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch("visual.web.backend.serve._restore_update_timer"),
        ):
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
        self.assertFalse(hasattr(config, "plot_clear"))
        self.assertFalse(hasattr(config, "plot_incognito"))
        self.assertEqual(seen["level"], logging.ERROR)
        self.assertEqual(logging.getLogger().level, before)
        self.assertEqual(str(config.assets_file), str(self.assets))
        self.assertEqual(str(config.cache_file), str(self.cache))

    def test_post_update_failure_is_500(self) -> None:
        from unittest.mock import patch

        with (
            patch("cli.update.main", side_effect=RuntimeError("boom")),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch("visual.web.backend.serve._restore_update_timer"),
        ):
            status, body = self._post_update()
        self.assertEqual(status, 500)
        self.assertIn("boom", body)
        self.assertNotIn("Traceback", body)

    def test_post_update_reads_ini_defaults(self) -> None:
        from unittest.mock import patch

        seen = {}
        ini = Path(self._holder.name) / "asalloc.ini"
        ini.write_text(
            "[server]\nport = 1\n"
            "[update]\nfetch_geosplit = True\n"
            "fetch_sectorsplit = True\n"
            "[plotter]\noutput_dir = plots\n",
            encoding="utf-8",
        )

        def fake_main(ctx) -> None:
            seen["config"] = ctx.config

        with (
            patch("cli.update.main", side_effect=fake_main),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch("visual.web.backend.serve._restore_update_timer"),
            patch.dict(os.environ, {"ASALLOC_CONFIG": str(ini)}),
        ):
            status, _ = self._post_update()
        self.assertEqual(status, 200)
        config = seen["config"]
        # Lite POST carries no flags: ini values apply.
        self.assertTrue(config.fetch_geosplit)
        self.assertTrue(config.fetch_sectorsplit)
        self.assertFalse(config.fetch_prices)
        self.assertFalse(hasattr(config, "plot_clear"))
        self.assertFalse(hasattr(config, "plot_incognito"))
        self.assertEqual(
            config.plotter_config.output_dir, ini.parent / "plots"
        )
        # Explicit handler arguments still win over ini.
        self.assertEqual(str(config.assets_file), str(self.assets))

    def test_post_update_fat_mode_sets_fetch(self) -> None:
        import logging
        from unittest.mock import patch

        seen = {}

        def fake_main(ctx) -> None:
            seen["config"] = ctx.config
            seen["level"] = logging.getLogger().level

        before = logging.getLogger().level
        with (
            patch("cli.update.main", side_effect=fake_main),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch("visual.web.backend.serve._restore_update_timer"),
        ):
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
        self.assertFalse(hasattr(config, "plot_clear"))
        self.assertFalse(hasattr(config, "plot_incognito"))
        self.assertEqual(seen["level"], logging.ERROR)
        self.assertEqual(logging.getLogger().level, before)

    def test_post_update_garbage_body_falls_back_to_lite(self) -> None:
        from unittest.mock import patch

        seen = {}

        def fake_main(ctx) -> None:
            seen["config"] = ctx.config

        with (
            patch("cli.update.main", side_effect=fake_main),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch("visual.web.backend.serve._restore_update_timer"),
        ):
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
        self.assertFalse(seen["config"].fetch_geosplit)
        self.assertFalse(seen["config"].fetch_sectorsplit)
        self.assertFalse(hasattr(seen["config"], "plot_clear"))
        self.assertFalse(hasattr(seen["config"], "plot_incognito"))

    def test_update_status_idle(self) -> None:
        status, body = self._get("/api/update")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["updating"])
        self.assertIn("last_ok", payload)
        self.assertIn("last_error", payload)

    def test_update_status_tracks_running_job(self) -> None:
        import threading as _threading
        from unittest.mock import patch

        entered = _threading.Event()
        release = _threading.Event()
        self.addCleanup(release.set)

        def fake_main(ctx) -> None:
            entered.set()
            release.wait(timeout=30)

        results: dict = {}

        def run() -> None:
            try:
                with (
                    patch("cli.update.main", side_effect=fake_main),
                    patch("visual.web.backend.serve._quiesce_update_units"),
                    patch("visual.web.backend.serve._restore_update_timer"),
                ):
                    results["update"] = self._post_update()
            except Exception as exc:  # never lose thread errors silently
                results["error"] = exc

        thread = _threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=30))
            status, body = self._get("/api/update")
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["updating"])
        finally:
            release.set()
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "update thread did not finish")
        self.assertNotIn("error", results)
        self.assertEqual(results["update"][0], 200)
        status, body = self._get("/api/update")
        payload = json.loads(body)
        self.assertFalse(payload["updating"])
        self.assertTrue(payload["last_ok"])

    def test_quiesce_failure_aborts_update(self) -> None:
        from unittest.mock import patch

        with (
            patch(
                "visual.web.backend.serve._quiesce_update_units",
                side_effect=RuntimeError("no systemd"),
            ),
            patch("cli.update.main") as no_run,
            patch(
                "visual.web.backend.serve._restore_update_timer"
            ) as no_restore,
        ):
            status, body = self._post_update()
        self.assertEqual(status, 500)
        self.assertIn("update not started", body)
        no_run.assert_not_called()
        no_restore.assert_not_called()
        # The job flag clears, so a later update is not stuck at 409.
        with (
            patch("cli.update.main"),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch("visual.web.backend.serve._restore_update_timer"),
        ):
            status, _ = self._post_update()
        self.assertEqual(status, 200)

    def test_timer_restored_after_successful_update(self) -> None:
        from unittest.mock import patch

        with (
            patch("cli.update.main"),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch(
                "visual.web.backend.serve._restore_update_timer"
            ) as restore,
        ):
            status, _ = self._post_update()
        self.assertEqual(status, 200)
        restore.assert_called_once_with()

    def test_timer_restored_after_failed_update(self) -> None:
        from unittest.mock import patch

        with (
            patch("cli.update.main", side_effect=RuntimeError("boom")),
            patch("visual.web.backend.serve._quiesce_update_units"),
            patch(
                "visual.web.backend.serve._restore_update_timer"
            ) as restore,
        ):
            status, _ = self._post_update()
        self.assertEqual(status, 500)
        restore.assert_called_once_with()

    def test_dashboard_sync_button_polls_status(self) -> None:
        dashboard_js = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "dashboard.js"
        ).read_text(encoding="utf-8")
        self.assertIn("pollUpdateStatus", dashboard_js)
        self.assertIn("/api/update", dashboard_js)
        self.assertIn("last_ok", dashboard_js)
        self.assertIn("aria-disabled", dashboard_js)
        css = (
            Path(__file__).resolve().parent.parent
            / "visual"
            / "web"
            / "frontend"
            / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("sync-spin", css)

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
        # Same stubbed spawning as TestConstituentsRoute (see above).
        del _SPAWNED_STUBS[:]
        from unittest.mock import patch

        spawn_patch = patch(
            "visual.web.backend.serve._spawn_update_process",
            side_effect=_stub_spawn_update_process,
        )
        spawn_patch.start()
        self.addCleanup(spawn_patch.stop)
        # Isolate the stub worker from the real config file (see above).
        env_patch = patch.dict(os.environ, {"ASALLOC_CONFIG": str(tmp / "nope.ini")})
        env_patch.start()
        self.addCleanup(env_patch.stop)
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
                with (
                    patch("cli.update.main", side_effect=fake_main),
                    patch("visual.web.backend.serve._quiesce_update_units"),
                    patch("visual.web.backend.serve._restore_update_timer"),
                ):
                    results["first"] = self._post_path("/api/update")
            except Exception as exc:  # never lose thread errors silently
                results["error"] = exc

        thread = _threading.Thread(target=first, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=30))
            with patch("cli.update.main") as no_run:
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

    def test_cancel_terminates_process(self) -> None:
        import threading as _threading
        from unittest.mock import patch

        entered = _threading.Event()
        release = _threading.Event()
        self.addCleanup(release.set)

        def fake_main(ctx) -> None:
            entered.set()
            release.wait(timeout=30)

        results: dict = {}

        def run_update() -> None:
            try:
                with (
                    patch("cli.update.main", side_effect=fake_main),
                    patch("visual.web.backend.serve._quiesce_update_units"),
                    patch("visual.web.backend.serve._restore_update_timer"),
                ):
                    results["update"] = self._post_path("/api/update")
            except Exception as exc:  # never lose thread errors silently
                results["error"] = exc

        thread = _threading.Thread(target=run_update, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=30))
            proc = _SPAWNED_STUBS[-1]
            status, body = self._post_path("/api/cancel")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"cancelled": True})
            self.assertTrue(proc.terminate_called)
        finally:
            release.set()
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "update thread did not finish")
        self.assertNotIn("error", results)
        status, body = results["update"]
        self.assertEqual(status, 409)
        self.assertIn("cancelled", body)

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
                with (
                    patch("cli.update.main", side_effect=fake_main),
                    patch("visual.web.backend.serve._quiesce_update_units"),
                    patch("visual.web.backend.serve._restore_update_timer"),
                ):
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


class TestUpdateUnits(unittest.TestCase):
    def test_run_systemctl_success(self) -> None:
        from unittest.mock import Mock, patch

        with patch("subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            from visual.web.backend.serve import _run_systemctl

            _run_systemctl("stop", "asalloc-update.timer")
        run.assert_called_once_with(
            ["systemctl", "--user", "stop", "asalloc-update.timer"],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_run_systemctl_failure_reports_stderr(self) -> None:
        from unittest.mock import Mock, patch

        from visual.web.backend.serve import _run_systemctl

        with patch("subprocess.run") as run:
            run.return_value = Mock(
                returncode=1, stdout="", stderr="Unit not found."
            )
            with self.assertRaisesRegex(RuntimeError, "Unit not found"):
                _run_systemctl("stop", "asalloc-update.timer")

    def test_run_systemctl_missing_binary(self) -> None:
        from unittest.mock import patch

        from visual.web.backend.serve import _run_systemctl

        with patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "systemctl not found"):
                _run_systemctl("stop", "asalloc-update.timer")

    def test_run_systemctl_timeout(self) -> None:
        import subprocess
        from unittest.mock import patch

        from visual.web.backend.serve import _run_systemctl

        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("x", 60)
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                _run_systemctl("stop", "asalloc-update.timer")

    def test_quiesce_partial_failure_restores_service(self) -> None:
        from unittest.mock import patch

        from visual.web.backend.serve import _quiesce_update_units

        calls = []

        def fake_run(*args: str) -> None:
            calls.append(args)
            if args[:2] == ("stop", "asalloc-update.timer"):
                raise RuntimeError("timer stop failed")

        with patch(
            "visual.web.backend.serve._run_systemctl", side_effect=fake_run
        ):
            with self.assertRaisesRegex(RuntimeError, "timer stop failed"):
                _quiesce_update_units()
        self.assertEqual(
            calls,
            [
                ("stop", "asalloc-update.service"),
                ("stop", "asalloc-update.timer"),
                ("start", "asalloc-update.service"),
            ],
        )

    def test_status_payload_shape(self) -> None:
        from visual.web.backend.serve import _update_status_payload

        payload = _update_status_payload()
        self.assertFalse(payload["updating"])
        self.assertIn("last_ok", payload)
        self.assertIn("last_error", payload)

    def test_terminate_process_kills_real_process(self) -> None:
        import multiprocessing
        import time

        from visual.web.backend.serve import _terminate_process

        proc = multiprocessing.get_context("spawn").Process(
            target=time.sleep, args=(60,), daemon=True
        )
        proc.start()
        try:
            self.assertTrue(proc.is_alive())
            self.assertTrue(_terminate_process(proc))
        finally:
            if proc.is_alive():
                proc.kill()
            proc.join(timeout=10)
        self.assertFalse(proc.is_alive())

    def test_terminate_process_dead_is_noop(self) -> None:
        from unittest.mock import Mock

        from visual.web.backend.serve import _terminate_process

        proc = Mock()
        proc.is_alive.return_value = False
        self.assertTrue(_terminate_process(proc))
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_terminate_process_escalates_to_kill(self) -> None:
        from unittest.mock import Mock

        from visual.web.backend.serve import _terminate_process

        proc = Mock()
        proc.is_alive.side_effect = [True, True, True, False]
        self.assertTrue(_terminate_process(proc))
        proc.terminate.assert_called_once_with()
        proc.kill.assert_called_once_with()

    def test_terminate_process_gives_up_on_unkillable(self) -> None:
        from unittest.mock import Mock

        from visual.web.backend.serve import _terminate_process

        proc = Mock()
        proc.is_alive.return_value = True
        self.assertFalse(_terminate_process(proc))
        proc.terminate.assert_called_once_with()
        proc.kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
