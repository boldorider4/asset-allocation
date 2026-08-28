"""Unit tests for OSKAR cockpit wait/click helpers (mocked Playwright page)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from oskar import _click_allocation_tab, _wait_for_cockpit_tabs


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
