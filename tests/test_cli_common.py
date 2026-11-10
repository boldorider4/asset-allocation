# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard for the canonical asset-bucket and broker vocabularies."""

from __future__ import annotations

import unittest

from cli.common import ASSET_BUCKETS, ASSET_BUCKET_KEYS, BROKER_ICONS, BROKER_SECTIONS, CRYPTO_PORTFOLIO, REAL_ESTATE_PORTFOLIO


class TestAssetBuckets(unittest.TestCase):
    def test_nine_buckets_with_unique_labels(self) -> None:
        self.assertEqual(len(ASSET_BUCKETS), 9)
        self.assertEqual(len(ASSET_BUCKET_KEYS), len(ASSET_BUCKETS))
        keys = [key for key, _ in ASSET_BUCKETS]
        labels = [label for _, label in ASSET_BUCKETS]
        self.assertEqual(len(set(keys)), len(keys))
        self.assertEqual(len(set(labels)), len(labels))
        self.assertEqual(set(ASSET_BUCKET_KEYS), set(keys))

    def test_canonical_order(self) -> None:
        self.assertEqual(
            [key for key, _ in ASSET_BUCKETS],
            [
                "equity_portfolio",
                "bond_portfolio",
                "commodity_portfolio",
                "crypto_portfolio",
                "fixed_maturity_bond_portfolio",
                "cash_portfolio",
                "pension_portfolio",
                "real_estate_portfolio",
                "private_equity_portfolio",
            ],
        )
        self.assertEqual(
            [label for _, label in ASSET_BUCKETS],
            [
                "Equity",
                "Fixed Income",
                "Commodities / Inflation",
                "Cryptocurrencies",
                "Fixed Maturity",
                "Cash",
                "Annuities",
                "Real Estate Equity",
                "Private Equity",
            ],
        )

class TestBrokerLiterals(unittest.TestCase):
    def test_seven_brokers_with_unique_ids(self) -> None:
        self.assertEqual(
            list(BROKER_ICONS),
            [
                "oskar",
                "scalable",
                "traderepublic",
                "check24",
                "alte-leipziger",
                "cryptowallet",
                "realestate",
            ],
        )

    def test_restrictive_sections(self) -> None:
        self.assertEqual(
            BROKER_SECTIONS["cryptowallet"], frozenset({CRYPTO_PORTFOLIO})
        )
        self.assertEqual(
            BROKER_SECTIONS["realestate"], frozenset({REAL_ESTATE_PORTFOLIO})
        )


if __name__ == "__main__":
    unittest.main()
