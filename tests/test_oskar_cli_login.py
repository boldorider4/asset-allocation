"""Unit tests for the headless OSKAR CLI login (mocked Playwright page)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scrape.oskar import (
    _find_login_controls,
    _perform_cli_login,
)


def _locator(*, visible=True, value=""):
    loc = MagicMock()
    loc.count.return_value = 1 if visible else 0
    loc.first = loc
    loc.is_visible.return_value = visible
    loc.input_value.return_value = value
    return loc


def _frame(*, email=None, password=None, submit=None):
    frame = MagicMock()
    frame.url = "https://login.oskar.de/u/login/identifier"

    def locator(sel):
        if "password" in sel:
            return password if password is not None else _locator(visible=False)
        return email if email is not None else _locator(visible=False)

    frame.locator.side_effect = locator
    submit_loc = submit if submit is not None else _locator(visible=False)
    frame.get_by_role.return_value = submit_loc
    return frame


class TestFindLoginControls(unittest.TestCase):
    def test_finds_email_and_password(self) -> None:
        email, password = _locator(), _locator()
        page = MagicMock()
        page.frames = [_frame(email=email, password=password)]
        found_email, found_password, _ = _find_login_controls(page)
        self.assertIs(found_email, email)
        self.assertIs(found_password, password)

    def test_identifier_first_screen_returns_email_only(self) -> None:
        email = _locator()
        page = MagicMock()
        page.frames = [_frame(email=email)]
        found_email, found_password, _ = _find_login_controls(page)
        self.assertIs(found_email, email)
        self.assertIsNone(found_password)

    def test_no_form_returns_nones(self) -> None:
        page = MagicMock()
        page.frames = [_frame()]
        self.assertEqual(_find_login_controls(page), (None, None, None))

    def test_skips_frames_without_form(self) -> None:
        email = _locator()
        page = MagicMock()
        page.frames = [_frame(), _frame(email=email, password=_locator())]
        found_email, found_password, _ = _find_login_controls(page)
        self.assertIs(found_email, email)
        self.assertIsNotNone(found_password)


class TestPerformCliLogin(unittest.TestCase):
    def test_returns_immediately_when_already_logged_in(self) -> None:
        page = MagicMock()
        with (
            patch("scrape.oskar._on_cockpit_dashboard", return_value=True),
            patch("scrape.oskar._find_login_controls") as find,
            patch("builtins.input") as prompt,
        ):
            _perform_cli_login(page, timeout_ms=5_000)
        find.assert_not_called()
        prompt.assert_not_called()

    def test_fills_prompted_credentials_and_submits(self) -> None:
        email, password, submit = _locator(value=""), _locator(), _locator()
        page = MagicMock()
        page.url = "https://login.oskar.de/u/login/identifier"
        with (
            patch(
                "scrape.oskar._on_cockpit_dashboard",
                side_effect=[False, False, True],
            ),
            patch(
                "scrape.oskar._find_login_controls",
                return_value=(email, password, submit),
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="user@example.com") as prompt,
            patch("scrape.oskar.getpass.getpass", return_value="s3cret") as hidden,
        ):
            _perform_cli_login(page, timeout_ms=30_000)
        prompt.assert_called_once()
        hidden.assert_called_once()  # password never goes through visible input()
        email.fill.assert_called_once_with("user@example.com", timeout=15_000)
        password.fill.assert_called_once_with("s3cret", timeout=15_000)
        submit.click.assert_called()

    def test_prefilled_email_only_prompts_password(self) -> None:
        email = _locator(value="remembered@example.com")
        password, submit = _locator(), _locator()
        page = MagicMock()
        with (
            patch(
                "scrape.oskar._on_cockpit_dashboard",
                side_effect=[False, False, True],
            ),
            patch(
                "scrape.oskar._find_login_controls",
                return_value=(email, password, submit),
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input") as prompt,
            patch("scrape.oskar.getpass.getpass", return_value="s3cret"),
        ):
            _perform_cli_login(page, timeout_ms=30_000)
        prompt.assert_not_called()
        email.fill.assert_not_called()
        password.fill.assert_called_once_with("s3cret", timeout=15_000)

    def test_rejected_login_reprompts(self) -> None:
        email, password, submit = _locator(value=""), _locator(), _locator()
        page = MagicMock()
        # Dashboard never lands; the form keeps showing → each attempt re-prompts.
        with (
            patch("scrape.oskar._on_cockpit_dashboard", return_value=False),
            patch(
                "scrape.oskar._find_login_controls",
                return_value=(email, password, submit),
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="user@example.com") as prompt,
            patch("scrape.oskar.getpass.getpass", return_value="s3cret"),
            patch("scrape.oskar._CLI_LOGIN_ATTEMPT_WAIT_MS", 1),
        ):
            with self.assertRaisesRegex(RuntimeError, "headless login timed out"):
                _perform_cli_login(page, timeout_ms=400)
        self.assertGreaterEqual(prompt.call_count, 2)

    def test_non_tty_raises_without_prompting(self) -> None:
        email, password, submit = _locator(value=""), _locator(), _locator()
        page = MagicMock()
        with (
            patch("scrape.oskar._on_cockpit_dashboard", return_value=False),
            patch(
                "scrape.oskar._find_login_controls",
                return_value=(email, password, submit),
            ),
            patch("sys.stdin.isatty", return_value=False),
            patch("builtins.input") as prompt,
        ):
            with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                _perform_cli_login(page, timeout_ms=5_000)
        prompt.assert_not_called()

    def test_timeout_without_form_raises(self) -> None:
        page = MagicMock()
        page.url = "https://login.oskar.de/u/login/identifier"
        with (
            patch("scrape.oskar._on_cockpit_dashboard", return_value=False),
            patch("sys.stdin.isatty", return_value=True),
            patch(
                "scrape.oskar._find_login_controls",
                return_value=(None, None, None),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "headless login timed out"):
                _perform_cli_login(page, timeout_ms=400)


if __name__ == "__main__":
    unittest.main()
