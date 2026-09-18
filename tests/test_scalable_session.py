"""Unit tests for Scalable ``sc`` Popen login/command/logout sequencing."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from scrape.scalable import Scalable, fetch_scalable_etfs, _TAGESGELD_FETCH_KEY


class FakeProc:
    def __init__(self, text: str = "", rc: int = 0, err: str = "") -> None:
        self._text = text
        self.stdout = io.StringIO(text)
        self.returncode = rc
        self._err = err
        self.killed = False
        self.poll_rc = None

    def poll(self):
        return self.poll_rc

    def wait(self, timeout=None) -> int:
        return self.returncode

    def communicate(self, timeout=None) -> tuple[str, str]:
        return self._text, self._err

    def kill(self) -> None:
        self.killed = True


HOLDINGS_JSON = """
{"result": {"items": [{
  "isin": "IE0006WW1TQ4",
  "name": "Xtrackers",
  "quantity": 4,
  "valuation": 140,
  "quote_mid_price": 40.315,
  "quote_currency": "EUR",
  "valuation_currency": "EUR"
}]}}
"""

OVERNIGHT = "account_name: Tagesgeld\nbalance: 40.32\n"


class TestScalableSession(unittest.TestCase):
    def test_login_streams_activation_url_then_commands_then_logout(self) -> None:
        import subprocess

        calls: list[list[str]] = []
        seen_kwargs: dict = {}

        def fake_popen(cmd, **kwargs):
            calls.append(list(cmd))
            sub = cmd[1:]
            if sub[:1] == ["login"]:
                seen_kwargs.update(kwargs)
                return FakeLoginProc(ACTIVATION_OUTPUT, rc=0, exit_after_polls=2)
            if sub[:2] == ["broker", "holdings"]:
                return FakeProc(HOLDINGS_JSON)
            if sub[:1] == ["overnight"]:
                return FakeProc(OVERNIGHT)
            if sub[:1] == ["logout"]:
                return FakeProc("")
            raise AssertionError(cmd)

        page = _fake_page(frames=[_fake_frame()])
        pw, browser = _fake_playwright(page)
        with (
            patch("scrape.scalable.subprocess.Popen", side_effect=fake_popen),
            patch("playwright.sync_api.sync_playwright", return_value=pw),
        ):
            rows = fetch_scalable_etfs(sc_bin="sc")

        self.assertEqual(calls[0], ["sc", "login", "--local-read-only"])
        self.assertEqual(calls[1], ["sc", "broker", "holdings", "--json"])
        self.assertEqual(calls[2], ["sc", "overnight"])
        self.assertEqual(calls[3], ["sc", "logout"])
        # The browser answers for sc: stdin is closed, not inherited.
        self.assertIs(seen_kwargs.get("stdin"), subprocess.DEVNULL)
        browser.close.assert_called_once_with()
        self.assertIn("IE0006WW1TQ4", rows)
        self.assertIn(_TAGESGELD_FETCH_KEY, rows)
        self.assertEqual(rows[_TAGESGELD_FETCH_KEY].value, 40.32)

    def test_logout_runs_when_holdings_fail(self) -> None:
        calls: list[list[str]] = []

        def fake_popen(cmd, **kwargs):
            calls.append(list(cmd))
            sub = cmd[1:]
            if sub[:1] == ["login"]:
                return FakeLoginProc(ACTIVATION_OUTPUT, rc=0, exit_after_polls=2)
            if sub[:2] == ["broker", "holdings"]:
                return FakeProc("boom", rc=1, err="fail")
            if sub[:1] == ["logout"]:
                return FakeProc("")
            raise AssertionError(cmd)

        page = _fake_page(frames=[_fake_frame()])
        pw, _browser = _fake_playwright(page)
        with (
            patch("scrape.scalable.subprocess.Popen", side_effect=fake_popen),
            patch("playwright.sync_api.sync_playwright", return_value=pw),
        ):
            with self.assertRaises(RuntimeError):
                fetch_scalable_etfs(sc_bin="sc")

        self.assertEqual(calls[-1], ["sc", "logout"])

    def test_commands_require_login(self) -> None:
        session = Scalable(sc_bin="sc")
        with self.assertRaises(RuntimeError):
            session.holdings_json()


ACTIVATION_OUTPUT = """Warning: session secrets are stored in local files.

Open this URL:
https://secure.scalable.capital/activate?user_code=RNPH-QXWJ

Verify the code RNPH-QXWJ in your browser.

Waiting for browser confirmation...
"""


def _hidden_locator():
    from unittest.mock import MagicMock

    loc = MagicMock()
    loc.count.return_value = 0
    return loc


def _visible_locator(value=""):
    from unittest.mock import MagicMock

    loc = MagicMock()
    loc.count.return_value = 1
    loc.first = loc
    loc.is_visible.return_value = True
    loc.input_value.return_value = value
    return loc


def _fake_frame(*, code=None, email=None, password=None, submit=None):
    from unittest.mock import MagicMock

    frame = MagicMock()

    def locator(sel):
        if "code" in sel:
            return code if code is not None else _hidden_locator()
        if "password" in sel:
            return password if password is not None else _hidden_locator()
        return email if email is not None else _hidden_locator()

    frame.locator.side_effect = locator
    frame.get_by_role.return_value = (
        submit if submit is not None else _hidden_locator()
    )
    return frame


def _fake_page(*, frames, url="https://secure.scalable.capital/activate?user_code=X"):
    from unittest.mock import MagicMock

    page = MagicMock()
    page.frames = frames
    page.url = url
    return page


def _fake_playwright(page):
    from unittest.mock import MagicMock

    browser = MagicMock()
    context = MagicMock()
    context.new_page.return_value = page
    browser.new_context.return_value = context
    pw = MagicMock()
    pw.__enter__.return_value = pw
    pw.chromium.launch.return_value = browser
    return pw, browser


class FakeLoginProc(FakeProc):
    """Fake ``sc login`` process that 'confirms' after a few polls."""

    def __init__(self, text: str, rc: int = 0, exit_after_polls: int = 3) -> None:
        super().__init__(text, rc)
        self._polls = 0
        self._exit_after = exit_after_polls

    def poll(self):
        self._polls += 1
        if self._polls > self._exit_after:
            return self.returncode
        return None


class TestConfirmationControls(unittest.TestCase):
    """The real confirmation screen: prefilled bare code input + a
    Bestätigen button sitting next to an Abbrechen lookalike."""

    def _confirm_frame(self, *, confirm=None):
        from unittest.mock import MagicMock

        frame = MagicMock()

        def get_by_role(role, name=None):
            loc = MagicMock()
            if (
                confirm is not None
                and name is not None
                and hasattr(name, "search")
                and name.search("Bestätigen")
            ):
                loc.count.return_value = 1
                loc.first = confirm
                confirm.is_visible.return_value = True
            else:
                loc.count.return_value = 0
            return loc

        frame.locator.side_effect = lambda sel: _hidden_locator()
        frame.get_by_role.side_effect = get_by_role
        return frame

    def _page_with(self, frame):
        from unittest.mock import MagicMock

        page = MagicMock()
        page.frames = [frame]
        return page

    def test_confirm_button_prefers_bestatigen(self) -> None:
        from scrape.scalable import _find_confirm_button

        confirm = _visible_locator()
        page = self._page_with(self._confirm_frame(confirm=confirm))
        self.assertIs(_find_confirm_button(page), confirm)

    def test_confirm_button_never_bare_submit(self) -> None:
        from scrape.scalable import _find_confirm_button

        # A visible submit button with no confirm text must NOT match:
        # on the real page that slot holds Abbrechen.
        page = self._page_with(self._confirm_frame(confirm=None))
        self.assertIsNone(_find_confirm_button(page))

    def test_code_display_matches_bare_prefilled_input(self) -> None:
        from unittest.mock import MagicMock

        from scrape.scalable import _find_code_display

        display = _visible_locator(value="SZWB-BMKC")
        frame = MagicMock()

        def locator(sel):
            if sel == 'input[type="text"]':
                found = MagicMock()
                found.count.return_value = 1
                found.nth.return_value = display
                return found
            return _hidden_locator()

        frame.locator.side_effect = locator
        self.assertIs(_find_code_display(self._page_with(frame)), display)

    def test_code_display_ignores_non_code_values(self) -> None:
        from unittest.mock import MagicMock

        from scrape.scalable import _find_code_display

        for value in ("joe@example.com", "hi", "12 34"):
            email_like = _visible_locator(value=value)
            frame = MagicMock()

            def locator(sel, _loc=email_like):
                if sel == 'input[type="text"]':
                    found = MagicMock()
                    found.count.return_value = 1
                    found.nth.return_value = _loc
                    return found
                return _hidden_locator()

            frame.locator.side_effect = locator
            with self.subTest(value=value):
                self.assertIsNone(_find_code_display(self._page_with(frame)))

    def test_ensure_code_fills_when_empty(self) -> None:
        from unittest.mock import MagicMock

        from scrape.scalable import _ensure_code

        display = _visible_locator(value="")
        frame = MagicMock()

        def locator(sel):
            if sel == 'input[type="text"]':
                found = MagicMock()
                found.count.return_value = 1
                found.nth.return_value = display
                return found
            return _hidden_locator()

        frame.locator.side_effect = locator
        self.assertTrue(_ensure_code(self._page_with(frame), "RNPH-QXWJ"))
        display.fill.assert_called_with("RNPH-QXWJ", timeout=15_000)

    def test_ensure_code_raises_on_mismatch(self) -> None:
        from unittest.mock import MagicMock

        from scrape.scalable import _ensure_code

        display = _visible_locator(value="EVIL-CODE")
        frame = MagicMock()

        def locator(sel):
            if sel == 'input[type="text"]':
                found = MagicMock()
                found.count.return_value = 1
                found.nth.return_value = display
                return found
            return _hidden_locator()

        frame.locator.side_effect = locator
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            _ensure_code(self._page_with(frame), "RNPH-QXWJ")

    def test_find_visible_button_prefers_confirm_text(self) -> None:
        from unittest.mock import MagicMock

        from scrape.scalable import _find_visible_button

        confirm = _visible_locator()
        cancel = _visible_locator()
        scope = MagicMock()
        scope.locator.return_value = cancel
        cancel.count.return_value = 1
        cancel.first = cancel

        def get_by_role(role, name=None):
            loc = MagicMock()
            if name is not None and hasattr(name, "search") and name.search("Bestätigen"):
                loc.count.return_value = 1
                loc.first = confirm
            else:
                loc.count.return_value = 0
            return loc

        scope.get_by_role.side_effect = get_by_role
        self.assertIs(_find_visible_button(scope), confirm)


class TestParseActivationOutput(unittest.TestCase):
    def test_parses_real_output(self) -> None:
        from scrape.scalable import parse_activation_output

        self.assertEqual(
            parse_activation_output(ACTIVATION_OUTPUT),
            (
                "https://secure.scalable.capital/activate?user_code=RNPH-QXWJ",
                "RNPH-QXWJ",
            ),
        )

    def test_none_without_url(self) -> None:
        from scrape.scalable import parse_activation_output

        self.assertIsNone(parse_activation_output(""))
        self.assertIsNone(parse_activation_output("Waiting for browser confirmation..."))
        self.assertIsNone(parse_activation_output("Verify the code RNPH-QXWJ here."))

    def test_strips_trailing_punctuation(self) -> None:
        from scrape.scalable import parse_activation_output

        url, code = parse_activation_output(
            "open https://secure.scalable.capital/activate?user_code=AB12-CD34."
        )
        self.assertEqual(code, "AB12-CD34")
        self.assertTrue(url.endswith("AB12-CD34"))


class TestScalableBrowserLogin(unittest.TestCase):
    def test_code_stage_fills_and_confirms(self) -> None:
        from scrape.scalable import playwright_device_login

        code = _visible_locator(value="")
        submit = _visible_locator()
        page = _fake_page(frames=[_fake_frame(code=code, submit=submit)])
        pw, browser = _fake_playwright(page)
        calls = {"n": 0}

        def sc_done() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        with patch("playwright.sync_api.sync_playwright", return_value=pw):
            playwright_device_login(
                "https://secure.scalable.capital/activate?user_code=RNPH-QXWJ",
                "RNPH-QXWJ",
                sc_done=sc_done,
                timeout_s=30,
            )
        code.fill.assert_called_with("RNPH-QXWJ", timeout=15_000)
        submit.click.assert_called_once_with(timeout=15_000)
        browser.close.assert_called_once_with()

    def test_credential_stage_prompts_like_oskar(self) -> None:
        import sys

        from scrape.scalable import playwright_device_login

        email = _visible_locator(value="")
        password = _visible_locator(value="")
        submit = _visible_locator()
        page = _fake_page(frames=[_fake_frame(email=email, password=password, submit=submit)])
        pw, browser = _fake_playwright(page)
        calls = {"n": 0}

        def sc_done() -> bool:
            calls["n"] += 1
            return calls["n"] > 2

        with (
            patch("playwright.sync_api.sync_playwright", return_value=pw),
            patch("builtins.input", return_value="  joe@example.com  "),
            patch("getpass.getpass", return_value="s3cret"),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            playwright_device_login(
                "https://secure.scalable.capital/activate?user_code=RNPH-QXWJ",
                "RNPH-QXWJ",
                sc_done=sc_done,
                timeout_s=30,
            )
        email.fill.assert_called_with("joe@example.com", timeout=15_000)
        password.fill.assert_called_with("s3cret", timeout=15_000)
        browser.close.assert_called_once_with()

    def test_requires_interactive_terminal(self) -> None:
        from scrape.scalable import _require_interactive_terminal

        with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
            _require_interactive_terminal()

    def test_sc_early_exit_aborts(self) -> None:
        proc = FakeProc("boom\n", rc=1)
        proc.poll_rc = 1
        with patch(
            "scrape.scalable.subprocess.Popen", return_value=proc
        ):
            session = Scalable(sc_bin="sc")
            with self.assertRaisesRegex(RuntimeError, "boom"):
                session.login(timeout_s=10)

    def test_no_activation_url_times_out(self) -> None:
        proc = FakeProc("Waiting for browser confirmation...\n", rc=0)
        with patch(
            "scrape.scalable.subprocess.Popen", return_value=proc
        ):
            session = Scalable(sc_bin="sc")
            with self.assertRaisesRegex(RuntimeError, "no activation URL"):
                session.login(timeout_s=2)

    def test_full_browser_login_flow(self) -> None:
        import subprocess

        proc = FakeLoginProc(ACTIVATION_OUTPUT, rc=0, exit_after_polls=3)
        seen: dict = {}

        def fake_popen(cmd, **kwargs):
            seen.update(kwargs)
            return proc

        page = _fake_page(frames=[_fake_frame()])
        pw, browser = _fake_playwright(page)
        with (
            patch("scrape.scalable.subprocess.Popen", side_effect=fake_popen),
            patch("playwright.sync_api.sync_playwright", return_value=pw),
        ):
            session = Scalable(sc_bin="sc")
            session.login(timeout_s=30)
        # Browser answers for us: stdin is closed, not inherited.
        self.assertIs(seen.get("stdin"), subprocess.DEVNULL)
        browser.close.assert_called_once_with()
        self.assertTrue(session._logged_in)

    def test_browser_closed_on_failure(self) -> None:
        page = _fake_page(frames=[_fake_frame()])
        pw, browser = _fake_playwright(page)
        with patch("playwright.sync_api.sync_playwright", return_value=pw):
            from scrape.scalable import playwright_device_login

            with self.assertRaisesRegex(RuntimeError, "timed out"):
                playwright_device_login(
                    "https://secure.scalable.capital/activate?user_code=X",
                    "X",
                    sc_done=lambda: False,
                    timeout_s=1,
                )
        browser.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
