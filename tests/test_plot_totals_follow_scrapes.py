# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plot totals must follow broker scrapes (regression test).

Reported bug: ``asalloc update --fetch-scalable`` updated the prices in
the JSON (and the constituents endpoint served them), but the total
printed with the plot stayed at the pre-scrape value. Root cause: the
update pipeline built all ``Portfolio`` objects before running the
scrapes, so plot totals came from pre-scrape ``Position`` snapshots
while the JSON was flushed from post-scrape dicts.

Covers both broker flags: the scalable path (shares + value from the
scrape) and the oskar path (value from the scrape, shares estimated as
value/price via a JustETF position and persisted to the assets file).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.context import AppConfig, RuntimeContext, ServerConfig  # noqa: E402
from scrape.oskar import OskarEtf, _OSKAR  # noqa: E402
from scrape.scalable import _SCALABLE, ScalableHolding  # noqa: E402

ISIN = "XX000TOTALS1"
OLD_VALUE = 20.0
NEW_VALUE = 40.0
CACHED_PRICE = 10.0

EMPTY_BUCKETS = {
    "fixed_maturity_bond_portfolio": [],
    "cash_portfolio": [],
    "bond_portfolio": [],
    "commodity_portfolio": [],
    "pension_portfolio": [],
}


def _seed_files(tmp: Path, *, broker: str, shares) -> tuple[Path, Path, Path]:
    assets = tmp / "assets.json"
    cache = tmp / "cache.json"
    viz = tmp / "viz"
    assets.write_text(
        json.dumps(
            {
                "equity_portfolio": [
                    {
                        "name": "Totals",
                        "ISIN": ISIN,
                        "shares": shares,
                        "value": OLD_VALUE,
                        "broker": broker,
                    }
                ],
                **EMPTY_BUCKETS,
            }
        ),
        encoding="utf-8",
    )
    cache.write_text(json.dumps({ISIN: {"price": CACHED_PRICE}}), encoding="utf-8")
    return assets, cache, viz


def _run_main(tmp: Path, *, broker: str, shares, fetch: dict) -> tuple[str, dict]:
    from cli.update import main as run_update

    assets, cache, viz = _seed_files(tmp, broker=broker, shares=shares)
    isin = tmp / "isin.json"
    flags = {
        "fetch_prices": False,
        "fetch_geosplit": False,
        "fetch_sectorsplit": False,
        **fetch,
    }
    config = AppConfig(
        assets_file=assets,
        cache_file=cache,
        isin_file=isin,
        server=ServerConfig(port=0, address="localhost", directory=viz),
        **flags,
    )
    ctx = RuntimeContext(config=config)
    run_update(ctx)
    raw_text = "".join(
        p.read_text(encoding="utf-8") for p in sorted((viz / "data").glob("*.raw"))
    )
    stored_assets = json.loads(assets.read_text(encoding="utf-8"))
    return raw_text, stored_assets


class PlotTotalsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        # cli.update.main points WebChart output at the run's dir via
        # class globals; restore them so other tests are unaffected.
        from visual.plot.web_chart import WebChart

        self._orig = (WebChart.data_dir, dict(WebChart._slug_counts), WebChart._plot_seq)
        self.addCleanup(self._restore_webchart)

    def _restore_webchart(self) -> None:
        from visual.plot.web_chart import WebChart

        WebChart.data_dir, counts, seq = self._orig
        WebChart._slug_counts = counts
        WebChart._plot_seq = seq


class TestScalablePlotTotals(PlotTotalsTestBase):
    def test_totals_follow_changed_holdings(self) -> None:
        holding = {
            ISIN: ScalableHolding(
                isin=ISIN, name="Totals", shares=4, value=NEW_VALUE, price=CACHED_PRICE
            )
        }
        with patch("scrape.scalable.fetch_scalable_etfs", return_value=holding):
            raw_text, stored = _run_main(
                Path(self._holder.name),
                broker=_SCALABLE,
                shares=2,
                fetch={"fetch_scalable": True},
            )
        # JSON is fresh ...
        self.assertEqual(stored["equity_portfolio"][0]["value"], NEW_VALUE)
        self.assertEqual(stored["equity_portfolio"][0]["shares"], 4)
        # ... and so are the plotted totals.
        self.assertIn(f"Total Value: {NEW_VALUE:.2f}", raw_text)
        self.assertIn(f"Net Worth: {NEW_VALUE:.2f}", raw_text)
        self.assertNotIn(f"Total Value: {OLD_VALUE:.2f}", raw_text)
        self.assertNotIn(f"Net Worth: {OLD_VALUE:.2f}", raw_text)


class TestOskarPlotTotals(PlotTotalsTestBase):
    def test_totals_follow_changed_holdings(self) -> None:
        etfs = {
            ISIN: OskarEtf(
                isin=ISIN,
                name="Totals",
                weight_pct=None,
                value_eur=NEW_VALUE,
                raw_text="x",
                category="",
            )
        }
        # Estimation needs both flags: fresh cockpit value + fresh quote.
        with (
            patch("scrape.oskar.fetch_oskar_etfs", return_value=etfs),
            patch(
                "position.justetf_position.JustETFPosition._fast_info_price",
                return_value=CACHED_PRICE,
            ),
        ):
            raw_text, stored = _run_main(
                Path(self._holder.name),
                broker=_OSKAR,
                shares=None,
                fetch={"fetch_oskar": True, "fetch_prices": True},
            )
        self.assertEqual(stored["equity_portfolio"][0]["value"], NEW_VALUE)
        # Shares estimated as value/price from a JustETF position quote.
        self.assertEqual(
            stored["equity_portfolio"][0]["shares"], NEW_VALUE / CACHED_PRICE
        )
        self.assertIn(f"Total Value: {NEW_VALUE:.2f}", raw_text)
        self.assertIn(f"Net Worth: {NEW_VALUE:.2f}", raw_text)
        self.assertNotIn(f"Total Value: {OLD_VALUE:.2f}", raw_text)
        self.assertNotIn(f"Net Worth: {OLD_VALUE:.2f}", raw_text)


if __name__ == "__main__":
    unittest.main()
