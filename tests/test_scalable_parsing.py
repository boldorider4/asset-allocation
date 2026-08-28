"""Unit tests for Scalable holdings and overnight parsing."""

from __future__ import annotations

import unittest

from scalable import (
    overnight_tagesgeld_row,
    parse_holdings_json,
    parse_overnight_text,
)

HOLDINGS_JSON = """
{
  "account_id": "oiusdofhshdkjfkjsdkjfs",
  "portfolio_id": "asdjhksjdhfknsdfsdsfsd",
  "resolution": {
    "account": "selected_context",
    "portfolio": "selected_context"
  },
  "result": {
    "account_id": "tWoapBJwgmpCdtrNVX8uJq",
    "count": 2,
    "items": [
      {
        "blocked_quantity": 0,
        "fifo_price": 135.8718,
        "isin": "DE000EWG2LD7",
        "name": "Boerse Stuttgart EUWAX Gold II",
        "pending_quantity": 0,
        "quantity": 1,
        "quote_currency": "EUR",
        "quote_is_outdated": false,
        "quote_mid_price": 129.6555,
        "quote_timestamp_utc": "2026-08-27T18:38:30.629Z",
        "security_type": "ETF",
        "valuation": 270,
        "valuation_currency": "EUR"
      },
      {
        "blocked_quantity": 0,
        "fifo_price": 35.608314,
        "isin": "IE0006WW1TQ4",
        "name": "Xtrackers MSCI World ex USA (Acc)",
        "pending_quantity": 0,
        "quantity": 4,
        "quote_currency": "EUR",
        "quote_is_outdated": false,
        "quote_mid_price": 40.315,
        "quote_timestamp_utc": "2026-08-27T18:38:32.354Z",
        "security_type": "ETF",
        "valuation": 140,
        "valuation_currency": "EUR"
      }
    ],
    "portfolio_id": "a6tnTMbGpnbbuXuAasbcBk"
  }
}
"""

OVERNIGHT_TEXT = """
savings_account_id: 4HFjHsmdskJdksjKJdsnnd
account_name: Tagesgeld
owner_kind: personal
interest_rate: 0.025
balance: 40.32
current_interest_bearing_amount: 40.32
current_accrued_amount: 25.55
estimated_next_payout_amount: 1.12
next_payout_date: 2026-09-01T00:00:00+00:00
deposit_accrued_lifetime_amount: 5.05
"""


class TestParseHoldingsJson(unittest.TestCase):
    def test_parses_quantity_valuation_and_mid_price(self) -> None:
        rows = parse_holdings_json(HOLDINGS_JSON)
        self.assertEqual(len(rows), 2)
        gold = rows[0]
        self.assertEqual(gold["isin"], "DE000EWG2LD7")
        self.assertEqual(gold["shares"], 1.0)
        self.assertEqual(gold["value"], 270.0)
        self.assertEqual(gold["price"], 129.6555)
        self.assertEqual(gold["name"], "Boerse Stuttgart EUWAX Gold II")
        world = rows[1]
        self.assertEqual(world["isin"], "IE0006WW1TQ4")
        self.assertEqual(world["shares"], 4.0)
        self.assertEqual(world["value"], 140.0)
        self.assertEqual(world["price"], 40.315)

    def test_rejects_non_eur_quote(self) -> None:
        raw = HOLDINGS_JSON.replace('"quote_currency": "EUR"', '"quote_currency": "USD"', 1)
        with self.assertRaises(ValueError):
            parse_holdings_json(raw)

    def test_rejects_invalid_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_holdings_json("not-json")


class TestParseOvernight(unittest.TestCase):
    def test_parses_key_value_lines(self) -> None:
        data = parse_overnight_text(OVERNIGHT_TEXT)
        self.assertEqual(data["account_name"], "Tagesgeld")
        self.assertEqual(data["balance"], "40.32")

    def test_tagesgeld_row_from_overnight(self) -> None:
        row = overnight_tagesgeld_row(OVERNIGHT_TEXT)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["name"], "Tagesgeld")
        self.assertEqual(row["value"], 40.32)
        self.assertIsNone(row["isin"])
        self.assertIsNone(row["shares"])
        self.assertIsNone(row["price"])
        self.assertTrue(row["is_tagesgeld"])

    def test_empty_overnight_is_absent(self) -> None:
        self.assertIsNone(overnight_tagesgeld_row(""))
        self.assertIsNone(overnight_tagesgeld_row("account_name: Tagesgeld\n"))


if __name__ == "__main__":
    unittest.main()
