"""Unit tests for Scalable ``sc`` Popen login/command/logout sequencing."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from scalable import Scalable, fetch_scalable_etfs, _TAGESGELD_FETCH_KEY


class FakeProc:
    def __init__(self, text: str = "", rc: int = 0, err: str = "") -> None:
        self._text = text
        self.stdout = io.StringIO(text)
        self.returncode = rc
        self._err = err
        self.killed = False

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
        calls: list[list[str]] = []

        def fake_popen(cmd, **kwargs):
            calls.append(list(cmd))
            sub = cmd[1:]
            if sub[:1] == ["login"]:
                return FakeProc(
                    "https://secure.scalable.capital/activate?user_code=ABC\n"
                )
            if sub[:2] == ["broker", "holdings"]:
                return FakeProc(HOLDINGS_JSON)
            if sub[:1] == ["overnight"]:
                return FakeProc(OVERNIGHT)
            if sub[:1] == ["logout"]:
                return FakeProc("")
            raise AssertionError(cmd)

        with patch("scalable.subprocess.Popen", side_effect=fake_popen):
            rows = fetch_scalable_etfs(sc_bin="sc")

        self.assertEqual(calls[0], ["sc", "login", "--local-read-only"])
        self.assertEqual(calls[1], ["sc", "broker", "holdings", "--json"])
        self.assertEqual(calls[2], ["sc", "overnight"])
        self.assertEqual(calls[3], ["sc", "logout"])
        self.assertIn("IE0006WW1TQ4", rows)
        self.assertIn(_TAGESGELD_FETCH_KEY, rows)
        self.assertEqual(rows[_TAGESGELD_FETCH_KEY].value, 40.32)

    def test_logout_runs_when_holdings_fail(self) -> None:
        calls: list[list[str]] = []

        def fake_popen(cmd, **kwargs):
            calls.append(list(cmd))
            sub = cmd[1:]
            if sub[:1] == ["login"]:
                return FakeProc("ok\n")
            if sub[:2] == ["broker", "holdings"]:
                return FakeProc("boom", rc=1, err="fail")
            if sub[:1] == ["logout"]:
                return FakeProc("")
            raise AssertionError(cmd)

        with patch("scalable.subprocess.Popen", side_effect=fake_popen):
            with self.assertRaises(RuntimeError):
                fetch_scalable_etfs(sc_bin="sc")

        self.assertEqual(calls[-1], ["sc", "logout"])

    def test_commands_require_login(self) -> None:
        session = Scalable(sc_bin="sc")
        with self.assertRaises(RuntimeError):
            session.holdings_json()


if __name__ == "__main__":
    unittest.main()
