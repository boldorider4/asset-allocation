# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end cache survival across ``allocation.main`` with --fetch-scalable.

Regression test for the reported bug: ``asalloc update --fetch-scalable
--fetch-geosplit --fetch-sectorsplit`` deleted sectors/countries from the
cache for every matched ISIN (factory staged fresh splits, then the
update wiped them and the flush persisted the hole), while the same run
without ``--fetch-scalable`` rewrote them.
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

from context import AppConfig, RuntimeContext, ServerConfig  # noqa: E402
from scrape.scalable import ScalableHolding  # noqa: E402

ISIN = "XX000UNKNOWN1"
FRESH_COUNTRIES = [
    {"name": "United States", "weight_pct": 90.0},
    {"name": "Germany", "weight_pct": 10.0},
]
FRESH_SECTORS = [{"name": "Technology", "weight_pct": 100.0}]


def _seed_files(tmp: Path) -> tuple[Path, Path, Path]:
    assets = tmp / "assets.json"
    cache = tmp / "cache.json"
    viz = tmp / "viz"
    assets.write_text(
        json.dumps(
            {
                "equity_portfolio": [
                    {
                        "name": "Unknown",
                        "ISIN": ISIN,
                        "shares": 2,
                        "value": 20.0,
                        "broker": "scalable",
                    }
                ],
                "fixed_maturity_bond_portfolio": [],
                "cash_portfolio": [],
                "bond_portfolio": [],
                "commodity_portfolio": [],
                "pension_portfolio": [],
            }
        ),
        encoding="utf-8",
    )
    cache.write_text(
        json.dumps(
            {
                ISIN: {
                    "price": 10.0,
                    "countries": {"France": 0.5},
                    "sectors": {"Finance": 0.5},
                }
            }
        ),
        encoding="utf-8",
    )
    return assets, cache, viz


def _holding() -> dict:
    return {
        ISIN: ScalableHolding(
            isin=ISIN,
            name="Unknown",
            shares=2,
            value=20.0,
            price=10.0,
        ),
    }


def _run_main(tmp: Path, *, geosplit: bool, sectorsplit: bool):
    from unittest.mock import MagicMock

    from allocation import main as run_update

    assets, cache, viz = _seed_files(tmp)
    config = AppConfig(
        fetch_scalable=True,
        fetch_geosplit=geosplit,
        fetch_sectorsplit=sectorsplit,
        plot_clear=True,
        assets_file=assets,
        cache_file=cache,
        server=ServerConfig(port=0, address="localhost", directory=viz),
    )
    ctx = RuntimeContext(config=config)
    countries_mock = MagicMock(return_value=[dict(r) for r in FRESH_COUNTRIES])
    sectors_mock = MagicMock(return_value=[dict(r) for r in FRESH_SECTORS])
    with (
        patch(
            "scrape.scalable.fetch_scalable_etfs", return_value=_holding()
        ),
        patch(
            "position.justetf_position.JustETFPosition._fetch_countries_with_retries",
            countries_mock,
        ),
        patch(
            "position.justetf_position.JustETFPosition._fetch_sectors_with_retries",
            sectors_mock,
        ),
    ):
        run_update(ctx)
    stored = json.loads(cache.read_text(encoding="utf-8"))
    return stored, countries_mock, sectors_mock


class TestScalableUpdateKeepsFreshSplits(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        # allocation.main points WebChart output at the run's dir via
        # class globals; restore them so other tests are unaffected.
        from visual.plot.web_chart import WebChart

        self._orig = (WebChart.data_dir, dict(WebChart._slug_counts), WebChart._plot_seq)
        self.addCleanup(self._restore_webchart)

    def _restore_webchart(self) -> None:
        from visual.plot.web_chart import WebChart

        WebChart.data_dir, counts, seq = self._orig
        WebChart._slug_counts = counts
        WebChart._plot_seq = seq

    def test_flags_on_rewrites_splits(self) -> None:
        stored, countries_mock, sectors_mock = _run_main(
            Path(self._holder.name), geosplit=True, sectorsplit=True
        )
        row = stored[ISIN]
        self.assertEqual(
            row["countries"], {"United States": 0.9, "Germany": 0.1}
        )
        self.assertEqual(row["sectors"], {"Technology": 1.0})
        # Fresh factory scrape, no invalidation: exactly one fetch each.
        countries_mock.assert_called_once_with()
        sectors_mock.assert_called_once_with()

    def test_flags_off_keeps_legacy_clear(self) -> None:
        stored, countries_mock, sectors_mock = _run_main(
            Path(self._holder.name), geosplit=False, sectorsplit=False
        )
        row = stored[ISIN]
        self.assertNotIn("countries", row)
        self.assertNotIn("sectors", row)
        self.assertEqual(row["price"], 10.0)
        # No stealth countries scrape: cached countries are used as-is,
        # only sectors refetch after invalidation.
        countries_mock.assert_not_called()
        sectors_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
