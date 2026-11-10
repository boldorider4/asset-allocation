# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard for the canonical asset-bucket vocabulary in cli.common."""

from __future__ import annotations

import unittest

from cli.common import ASSET_BUCKETS, ASSET_BUCKET_KEYS, AssetBucket


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
                "Crypto",
                "Fixed Maturity",
                "Cash",
                "Annuities",
                "Real Estate Equity",
                "Private Equity",
            ],
        )

    def test_literal_alias_covers_every_key(self) -> None:
        self.assertEqual(
            set(AssetBucket.__args__),  # type: ignore[attr-defined]
            set(ASSET_BUCKET_KEYS),
        )


if __name__ == "__main__":
    unittest.main()
