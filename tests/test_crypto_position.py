# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crypto ETC positions: no geo/sector vendor data, hardcoded sector wedge.

Covers the CryptoPosition class behavior (split-fetch hooks, cached split
rows ignored, inherited JustETF quote machinery) and the factory routing
that shortcuts specialized issuers when the registry says the ISIN is a
crypto product.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.common import CRYPTO_PORTFOLIO
from cli.context import AppConfig, RuntimeContext, ServerConfig
from position.crypto_position import CRYPTO_SECTOR_LABEL, CryptoPosition
from position.factory import factory
from position.position import fold_unknown_sector_label
from position.justetf_position import JustETFPosition
from tests.store_helpers import read_isin, write_cache

_ISIN = "XX00CRYPTO12"


def _ctx(tmp: Path, cache: dict | None = None) -> RuntimeContext:
    server = ServerConfig(
        port=0,
        address="localhost",
        directory=tmp,
        db_file=tmp / "asalloc.db",
    )
    config = AppConfig(server=server)
    ctx = RuntimeContext(config=config)
    if cache:
        for isin, entry in cache.items():
            ctx.cache[isin] = entry
        write_cache(server.db_file, cache)
    ctx.cache_loaded = True
    return ctx


class TestCryptoPosition(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)

    def test_split_flags_on_never_scrape_and_hardcode_split(self) -> None:
        ctx = _ctx(self.tmp)
        ctx.config.fetch_geosplit = True
        ctx.config.fetch_sectorsplit = True
        with patch.object(
            JustETFPosition,
            "_fetch_countries_with_retries",
            side_effect=AssertionError("crypto must not scrape countries"),
        ), patch.object(JustETFPosition, "_fast_info_price", return_value=15.0):
            pos = CryptoPosition(_ISIN, name="Crypto ETC", ctx=ctx)
        self.assertEqual(pos.price, 15.0)
        self.assertEqual(pos.countries(), [])
        self.assertEqual(
            pos.sectors(),
            [{"name": CRYPTO_SECTOR_LABEL, "weight_pct": 100.0}],
        )

    def test_cached_split_rows_are_ignored_and_price_inherited(self) -> None:
        ctx = _ctx(
            self.tmp,
            cache={
                _ISIN: {
                    "price": 10.0,
                    "countries": {"Germany": 1.0},
                    "sectors": {"Technology": 1.0},
                }
            },
        )
        pos = CryptoPosition(
            _ISIN,
            name="Crypto ETC",
            price=10.0,
            cached_countries={"Germany": 1.0},
            cached_sectors={"Technology": 1.0},
            ctx=ctx,
        )
        self.assertEqual(pos.countries(), [])
        self.assertIsNone(pos.sectors())
        self.assertEqual(pos.price, 10.0)

    def test_sector_wedge_label_stays_out_of_other(self) -> None:
        self.assertEqual(fold_unknown_sector_label(CRYPTO_SECTOR_LABEL), CRYPTO_SECTOR_LABEL)


class TestFactoryCryptoRouting(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.ctx = _ctx(self.tmp)
        self.ctx.isin_registry.register_isin(_ISIN, bucket=CRYPTO_PORTFOLIO)
        self.ctx.flush_isin_registry()

    def test_crypto_bucket_shortcuts_specialized_issuers(self) -> None:
        self.ctx.config.fetch_prices = False
        with patch(
            "position.factory.dws_product_url_exists", return_value=True
        ) as dws, patch.object(
            JustETFPosition, "_fast_info_price", side_effect=RuntimeError("no quotes")
        ):
            pos = factory(
                _ISIN,
                name="Crypto ETC",
                shares=None,
                value=200.0,
                broker="other",
                ctx=self.ctx,
            )
        self.assertIsInstance(pos, CryptoPosition)
        dws.assert_not_called()
        self.assertEqual(pos.value, 200.0)
        rows = read_isin(self.tmp / "asalloc.db")
        self.assertEqual(rows[_ISIN]["bucket"], CRYPTO_PORTFOLIO)
        self.assertIsNone(rows[_ISIN].get("issuer"))

    def test_cache_writeback_stores_crypto_wedge_as_fraction(self) -> None:
        self.ctx.config.fetch_geosplit = True
        self.ctx.config.fetch_sectorsplit = True
        with patch.object(JustETFPosition, "_fast_info_price", return_value=15.0):
            pos = factory(
                _ISIN,
                name="Crypto ETC",
                shares=2,
                broker="other",
                ctx=self.ctx,
            )
        self.assertIsInstance(pos, CryptoPosition)
        cached = self.ctx.cache[_ISIN]
        self.assertEqual(cached["sectors"], {CRYPTO_SECTOR_LABEL: 1.0})


if __name__ == "__main__":
    unittest.main()
