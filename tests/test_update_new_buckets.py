# SPDX-License-Identifier: AGPL-3.0-or-later
"""The update pipeline composes the three new buckets.

- ``crypto_portfolio`` feeds the growth-portfolio charts (sector wedge
  "Cryptocurrencies") and the complete portfolio.
- ``real_estate_portfolio``/``private_equity_portfolio`` are manual
  buckets that only appear in the complete portfolio.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cli.context import AppConfig, RuntimeContext, ServerConfig
from cli.update import main as run_update
from tests.store_helpers import write_cache, write_positions


def _run_update(tmp: Path, buckets: dict) -> str:
    db = tmp / "asalloc.db"
    viz = tmp / "viz"
    write_positions(db, buckets)
    write_cache(db, {})
    config = AppConfig(
        server=ServerConfig(
            port=0, address="localhost", directory=viz, db_file=db
        )
    )
    ctx = RuntimeContext(config=config)
    run_update(ctx)
    raw_text = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted((viz / "data" / "default").glob("*.raw"))
    )
    return raw_text


class TestUpdateNewBuckets(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        from visual.plot.web_chart import WebChart

        self._orig = (
            WebChart.data_dir,
            dict(WebChart._slug_counts),
            WebChart._plot_seq,
        )
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        from visual.plot.web_chart import WebChart

        WebChart.data_dir, WebChart._slug_counts, WebChart._plot_seq = self._orig

    def test_crypto_enters_growth_and_total_while_other_two_stay_out(self) -> None:
        raw = _run_update(
            self.tmp,
            {
                "crypto_portfolio": [
                    {"name": "BTC ETC", "value": 500.0, "broker": "other"}
                ],
                "real_estate_portfolio": [
                    {"name": "Rental", "value": 700.0, "broker": "other"}
                ],
                "private_equity_portfolio": [
                    {"name": "PE stake", "value": 800.0, "broker": "other"}
                ],
            },
        )
        # Growth portfolio: 500 (crypto only; RE/PE excluded).
        self.assertIn("Total Value: 500.00", raw)
        # Crypto contributes its hardcoded wedge to the growth sector chart.
        self.assertIn("Cryptocurrencies", raw)
        # Complete portfolio: 500 + 700 + 800.
        self.assertIn("Net Worth: 2000.00", raw)

    def test_manual_crypto_row_without_isin(self) -> None:
        raw = _run_update(
            self.tmp,
            {"crypto_portfolio": [{"name": "BTC wallet", "value": 42.0}]},
        )
        self.assertIn("Net Worth: 42.00", raw)
        self.assertIn("Cryptocurrencies", raw)


if __name__ == "__main__":
    unittest.main()
