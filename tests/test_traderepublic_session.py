"""Unit tests for Trade Republic pytr login/portfolio/logout sequencing."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

from traderepublic import (
    TradeRepublic,
    _CASH_FETCH_KEY,
    cash_row,
    fetch_traderepublic_etfs,
    parse_positions,
)


class FakeTr:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class FakePytrPortfolio:
    def __init__(self, tr, include_watchlist: bool = False) -> None:
        self.tr = tr
        self.include_watchlist = include_watchlist
        self.positions = [
            {
                "instrumentId": "IE0006WW1TQ4",
                "name": "Xtrackers",
                "netSize": "4",
                "price": "40.315",
                "netValue": Decimal("140.00"),
            }
        ]
        self.cash = [{"amount": "40.32", "currencyId": "EUR"}]

    async def portfolio_loop(self) -> None:
        await self.tr.close()


class TestTradeRepublicParsing(unittest.TestCase):
    def test_parse_positions_and_cash(self) -> None:
        rows = parse_positions(
            [
                {
                    "instrumentId": "IE0006WW1TQ4",
                    "name": "Xtrackers",
                    "netSize": "4",
                    "price": "40.315",
                    "netValue": Decimal("140.00"),
                },
                {
                    "isin": "IE000BI8OT95",
                    "name": "Zero",
                    "netSize": "0",
                    "price": "10",
                    "netValue": "0",
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["isin"], "IE0006WW1TQ4")
        self.assertEqual(rows[0]["shares"], 4.0)
        self.assertEqual(rows[0]["value"], 140.0)
        self.assertEqual(rows[0]["price"], 40.315)
        cash = cash_row([{"amount": "40.32", "currencyId": "EUR"}])
        self.assertIsNotNone(cash)
        self.assertEqual(cash["value"], 40.32)
        self.assertTrue(cash["is_cash"])

    def test_cash_rejects_non_eur(self) -> None:
        with self.assertRaises(ValueError):
            cash_row([{"amount": "10", "currencyId": "USD"}])

    def test_parse_requires_isin(self) -> None:
        with self.assertRaises(ValueError):
            parse_positions([{"netSize": "1", "price": "1", "netValue": "1"}])


class TestTradeRepublicSession(unittest.TestCase):
    def test_login_then_portfolio_then_logout(self) -> None:
        fake_tr = FakeTr()
        logins: list[dict] = []

        def fake_login(**kwargs):
            logins.append(kwargs)
            return fake_tr

        with patch(
            "traderepublic._import_pytr",
            return_value=(fake_login, FakePytrPortfolio),
        ):
            rows = fetch_traderepublic_etfs()

        self.assertEqual(logins[0]["v2"], True)
        self.assertIn("IE0006WW1TQ4", rows)
        self.assertIn(_CASH_FETCH_KEY, rows)
        self.assertEqual(rows[_CASH_FETCH_KEY].value, 40.32)
        self.assertGreaterEqual(fake_tr.closed, 1)

    def test_logout_runs_when_portfolio_fails(self) -> None:
        fake_tr = FakeTr()

        def fake_login(**kwargs):
            return fake_tr

        class BoomPortfolio:
            def __init__(self, tr, include_watchlist: bool = False) -> None:
                self.tr = tr

            async def portfolio_loop(self) -> None:
                raise RuntimeError("boom")

        with patch(
            "traderepublic._import_pytr",
            return_value=(fake_login, BoomPortfolio),
        ):
            with self.assertRaises(RuntimeError):
                fetch_traderepublic_etfs()

        self.assertEqual(fake_tr.closed, 1)

    def test_commands_require_login(self) -> None:
        session = TradeRepublic()
        with self.assertRaises(RuntimeError):
            session.portfolio_and_cash()


if __name__ == "__main__":
    unittest.main()
