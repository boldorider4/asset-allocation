"""Unit tests for OSKAR cockpit wait/click helpers (mocked Playwright page)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scrape.oskar import (
    _click_allocation_tab,
    _click_sourcepoint_button,
    _ensure_sourcepoint_cookie_banner_dismissed,
    _hide_headed_browser_window,
    _on_cockpit_dashboard,
    _switch_to_headless_after_login,
    _wait_for_cockpit_dashboard,
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
    @patch("scrape.oskar._wait_for_cockpit_dashboard")
    @patch("scrape.oskar._open_oskar_page")
    def test_reuses_storage_state_in_headless_browser(
        self, open_page: MagicMock, wait_dashboard: MagicMock
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
        # The headless attempt has to reach the dashboard URL as well.
        wait_dashboard.assert_called_once()
        self.assertIs(wait_dashboard.call_args.args[0], headless_page)
        self.assertIs(browser, headless_browser)
        self.assertIs(context, headless_context)
        self.assertIs(page, headless_page)
        self.assertFalse(headed_visible)

    @patch("scrape.oskar._hide_headed_browser_window")
    @patch("scrape.oskar._on_cockpit_dashboard", return_value=True)
    @patch(
        "scrape.oskar._wait_for_cockpit_dashboard",
        side_effect=RuntimeError("no redirect in headless"),
    )
    @patch("scrape.oskar._open_oskar_page")
    def test_falls_back_to_minimized_headed_browser(
        self,
        open_page: MagicMock,
        _wait_dashboard: MagicMock,
        _on_dashboard: MagicMock,
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


class TestClickSourcepointButton(unittest.TestCase):
    def test_uses_normal_click_first(self) -> None:
        el = MagicMock()
        self.assertTrue(_click_sourcepoint_button(el, timeout_ms=1_000))
        el.click.assert_called_once_with(timeout=1_000)
        el.evaluate.assert_not_called()

    def test_falls_back_to_force_then_dom_click(self) -> None:
        el = MagicMock()
        el.click.side_effect = [RuntimeError("not stable"), RuntimeError("still not")]

        self.assertTrue(_click_sourcepoint_button(el, timeout_ms=1_000))

        self.assertEqual(el.click.call_count, 2)
        self.assertTrue(el.click.call_args_list[1].kwargs["force"])
        el.evaluate.assert_called_once_with("el => el.click()")

    def test_returns_false_when_every_strategy_fails(self) -> None:
        el = MagicMock()
        el.click.side_effect = RuntimeError("blocked")
        el.evaluate.side_effect = RuntimeError("blocked")
        self.assertFalse(_click_sourcepoint_button(el, timeout_ms=1_000))


class TestEnsureCookieBannerDismissed(unittest.TestCase):
    @patch("scrape.oskar._try_dismiss_sourcepoint_cookie_banner", return_value=True)
    @patch(
        "scrape.oskar._sourcepoint_cookie_banner_present",
        side_effect=[True, True, False],
    )
    def test_retries_until_banner_gone(
        self, _present: MagicMock, dismiss: MagicMock
    ) -> None:
        page = MagicMock()
        self.assertTrue(
            _ensure_sourcepoint_cookie_banner_dismissed(page, timeout_ms=5_000)
        )
        self.assertEqual(dismiss.call_count, 2)

    @patch("scrape.oskar._try_dismiss_sourcepoint_cookie_banner", return_value=False)
    @patch("scrape.oskar._sourcepoint_cookie_banner_present", return_value=True)
    def test_gives_up_after_attempts(
        self, _present: MagicMock, dismiss: MagicMock
    ) -> None:
        page = MagicMock()
        self.assertFalse(
            _ensure_sourcepoint_cookie_banner_dismissed(
                page, timeout_ms=5_000, attempts=3
            )
        )
        self.assertEqual(dismiss.call_count, 3)

    @patch("scrape.oskar._try_dismiss_sourcepoint_cookie_banner")
    @patch("scrape.oskar._sourcepoint_cookie_banner_present", return_value=False)
    def test_no_click_when_no_banner(
        self, _present: MagicMock, dismiss: MagicMock
    ) -> None:
        page = MagicMock()
        self.assertTrue(
            _ensure_sourcepoint_cookie_banner_dismissed(page, timeout_ms=5_000)
        )
        dismiss.assert_not_called()


class TestOnCockpitDashboard(unittest.TestCase):
    def test_matches_dashboard_urls(self) -> None:
        for url in (
            "https://mein.oskar.de/cockpit/dashboard",
            "https://mein.oskar.de/cockpit/dashboard/",
            "https://mein.oskar.de/cockpit/dashboard?foo=1#bar",
        ):
            page = MagicMock()
            page.url = url
            self.assertTrue(_on_cockpit_dashboard(page), url)

    def test_rejects_login_and_other_pages(self) -> None:
        for url in (
            "https://login.oskar.de/u/login/identifier",
            "https://mein.oskar.de/login",
            "https://mein.oskar.de/cockpit/settings",
            "",
        ):
            page = MagicMock()
            page.url = url
            self.assertFalse(_on_cockpit_dashboard(page), url)


class TestWaitForCockpitDashboard(unittest.TestCase):
    @patch("scrape.oskar._on_cockpit_dashboard", side_effect=[False, False, True])
    def test_polls_until_redirect_lands(self, on_dashboard: MagicMock) -> None:
        page = MagicMock()
        _wait_for_cockpit_dashboard(page, timeout_ms=5_000)
        self.assertEqual(on_dashboard.call_count, 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)

    @patch("scrape.oskar._on_cockpit_dashboard", return_value=False)
    def test_raises_when_redirect_never_lands(self, _on_dashboard: MagicMock) -> None:
        page = MagicMock()
        with self.assertRaisesRegex(RuntimeError, "timed out waiting for the redirect"):
            _wait_for_cockpit_dashboard(page, timeout_ms=400)


class TestClickAllocationTab(unittest.TestCase):
    @patch("scrape.oskar._try_dismiss_sourcepoint_cookie_banner")
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

    @patch("scrape.oskar._try_dismiss_sourcepoint_cookie_banner")
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
