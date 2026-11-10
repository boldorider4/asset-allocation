# SPDX-License-Identifier: AGPL-3.0-or-later
"""CryptoPortfolio: one value-weighted ``Cryptocurrencies`` sector wedge."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cli.common import CRYPTO_PORTFOLIO
from cli.context import AppConfig, RuntimeContext, ServerConfig
from portfolio.crypto_portfolio import CryptoPortfolio


def _ctx(tmp: Path) -> RuntimeContext:
    server = ServerConfig(
        port=0,
        address="localhost",
        directory=tmp,
        db_file=tmp / "asalloc.db",
    )
    config = AppConfig(server=server)
    return RuntimeContext(config=config)


class TestCryptoPortfolio(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.ctx = _ctx(self.tmp)

    def _crypto(self, rows: list[dict]) -> CryptoPortfolio:
        rows = [{**row, "broker": row.get("broker", "other")} for row in rows]
        for row in rows:
            if row.get("ISIN"):
                self.ctx.isin_registry.register_isin(
                    row["ISIN"], bucket=CRYPTO_PORTFOLIO
                )
        self.ctx.flush_isin_registry()
        return CryptoPortfolio(
            name="Crypto", positions=rows, ctx=self.ctx
        )

    def test_manual_rows_yield_one_crypto_wedge(self) -> None:
        # Manual entries (no ISIN, booked value only) still aggregate
        # into the single hardcoded wedge so aggregation never depends
        # on vendor data crypto products don't carry.
        p = self._crypto(
            [
                {"name": "BTC fund", "value": 300.0},
                {"name": "ETH fund", "value": 700.0},
            ]
        )
        self.assertEqual(p.value, 1000.0)
        self.assertEqual(p.sectors, {"Cryptocurrencies": 1.0})

    def test_value_weighted_wedge_from_isin_tracked_rows(self) -> None:
        # ISIN rows route into CryptoPosition through the factory; each
        # carries the same single wedge, so the value-weighted union is
        # flat 1.0 (crypto has no sub-sectors by definition).
        p = self._crypto(
            [
                {"name": "BTC ETC", "ISIN": "XX00CRYPTO12", "value": 250.0},
                {"name": "ETH ETC", "value": 250.0},
            ]
        )
        self.assertEqual(p.value, 500.0)
        self.assertEqual(p.sectors, {"Cryptocurrencies": 1.0})

    def test_empty_portfolio_has_no_wedges(self) -> None:
        p = self._crypto([])
        self.assertEqual(p.sectors, {})


if __name__ == "__main__":
    unittest.main()
