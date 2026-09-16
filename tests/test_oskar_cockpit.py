"""Unit tests for OSKAR cockpit wait/click helpers (mocked Playwright page)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scrape.oskar import (
    _click_allocation_tab,
    _on_cockpit_dashboard,
)


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
