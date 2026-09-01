"""Unit tests for OSKAR cockpit wait/click helpers (mocked Playwright page)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scrape.oskar import (
    _click_allocation_tab,
    _hide_headed_browser_window,
    _switch_to_headless_after_login,
    _wait_for_cockpit_tabs,
)


class TestHideHeadedBrowserWindow(unittest.TestCase):
    def test_minimizes_via_cdp(self) -> None:
        page = MagicMock()
        cdp = MagicMock()
        cdp.send.side_effect = [
            {"windowId": 7},
            None,
        ]
        page.context.new_cdp_session.return_value = cdp

        _hide_headed_browser_window(page)

        page.context.new_cdp_session.assert_called_once_with(page)
        self.assertEqual(
            cdp.send.call_args_list[0].args,
            ("Browser.getWindowForTarget",),
        )
        self.assertEqual(
            cdp.send.call_args_list[1].args,
            (
                "Browser.setWindowBounds",
                {"windowId": 7, "bounds": {"windowState": "minimized"}},
            ),
        )

    def test_swallows_cdp_errors(self) -> None:
        page = MagicMock()
        page.context.new_cdp_session.side_effect = RuntimeError("no cdp")
        _hide_headed_browser_window(page)


class TestSwitchToHeadlessAfterLogin(unittest.TestCase):
    @patch("scrape.oskar._wait_for_cockpit_tabs")
    @patch("scrape.oskar._page_needs_login", return_value=False)
    @patch("scrape.oskar._open_oskar_page")
    def test_reuses_storage_state_in_headless_browser(
        self, open_page: MagicMock, _needs_login: MagicMock, _tabs: MagicMock
    ) -> None:
        headed_browser = MagicMock()
        headed_context = MagicMock()
        headed_context.storage_state.return_value = {"cookies": ["auth0"]}
        headless_browser, headless_context, headless_page = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        open_page.return_value = (headless_browser, headless_context, headless_page)

        browser, context, page, headed_visible = _switch_to_headless_after_login(
            MagicMock(),
            headed_browser,
            headed_context,
            dashboard_url="https://mein.oskar.de/cockpit/dashboard",
            timeout_ms=30_000,
        )

        headed_browser.close.assert_called_once()
        self.assertTrue(open_page.call_args.kwargs["headless"])
        self.assertEqual(
            open_page.call_args.kwargs["storage_state"], {"cookies": ["auth0"]}
        )
        self.assertIs(browser, headless_browser)
        self.assertIs(context, headless_context)
        self.assertIs(page, headless_page)
        self.assertFalse(headed_visible)

    @patch("scrape.oskar._hide_headed_browser_window")
    @patch("scrape.oskar._wait_for_cockpit_tabs")
    @patch("scrape.oskar._page_needs_login", side_effect=[True, False])
    @patch("scrape.oskar._open_oskar_page")
    def test_falls_back_to_minimized_headed_browser(
        self,
        open_page: MagicMock,
        _needs_login: MagicMock,
        _tabs: MagicMock,
        hide: MagicMock,
    ) -> None:
        headless_browser = MagicMock()
        fallback_page = MagicMock()
        open_page.side_effect = [
            (headless_browser, MagicMock(), MagicMock()),
            (MagicMock(), MagicMock(), fallback_page),
        ]

        _browser, _context, page, headed_visible = _switch_to_headless_after_login(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            dashboard_url="https://mein.oskar.de/cockpit/dashboard",
            timeout_ms=30_000,
        )

        headless_browser.close.assert_called_once()
        self.assertFalse(open_page.call_args_list[1].kwargs["headless"])
        self.assertIs(page, fallback_page)
        self.assertTrue(headed_visible)
        hide.assert_called_once_with(fallback_page)


class TestWaitForCockpitTabs(unittest.TestCase):
    @patch("oskar._cockpit_ready", side_effect=[False, False, True])
    @patch("oskar._try_dismiss_sourcepoint_cookie_banner")
    def test_polls_until_cockpit_ready(
        self, _dismiss: MagicMock, _ready: MagicMock
    ) -> None:
        page = MagicMock()
        _wait_for_cockpit_tabs(page, timeout_ms=5_000)
        self.assertEqual(_ready.call_count, 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)


class TestClickAllocationTab(unittest.TestCase):
    @patch("oskar._try_dismiss_sourcepoint_cookie_banner")
    def test_retries_until_tab_visible(self, _dismiss: MagicMock) -> None:
        page = MagicMock()
        frame = MagicMock()
        frame.url = "https://mein.oskar.de/cockpit/dashboard"
        page.frames = [frame]

        empty = MagicMock()
        empty.count.return_value = 0
        ready = MagicMock()
        ready.count.return_value = 1
        first = MagicMock()
        ready.first = first

        tab_locator = MagicMock()
        tab_locator.count.side_effect = [0, 0, 1]
        tab_locator.first = first

        frame.get_by_role.return_value = tab_locator
        frame.get_by_text.return_value = empty

        _click_allocation_tab(page, timeout_ms=5_000)

        frame.get_by_role.assert_called()
        first.wait_for.assert_called_once()
        first.click.assert_called_once()

    @patch("oskar._try_dismiss_sourcepoint_cookie_banner")
    def test_raises_after_timeout(self, _dismiss: MagicMock) -> None:
        page = MagicMock()
        frame = MagicMock()
        frame.url = "https://mein.oskar.de/cockpit/dashboard"
        page.frames = [frame]

        locator = MagicMock()
        locator.count.return_value = 0
        frame.get_by_role.return_value = locator
        frame.get_by_text.return_value = locator

        with self.assertRaisesRegex(RuntimeError, "could not activate Gewichtung tab"):
            _click_allocation_tab(page, timeout_ms=400)
