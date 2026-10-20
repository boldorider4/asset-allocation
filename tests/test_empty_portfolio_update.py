# SPDX-License-Identifier: AGPL-3.0-or-later
"""The update pipeline tolerates a fresh (empty) user portfolio.

A user listed in the assets file without any positions yet must not
break the run: missing buckets are empty portfolios, and empty
portfolios are skipped instead of raising "data must contain at least
one entry".
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cli.context import AppConfig, RuntimeContext, ServerConfig
from cli.update import main as run_update


def _ctx(tmp: Path, payload: object, user: str = "alice") -> RuntimeContext:
    assets = tmp / "assets.json"
    cache = tmp / "cache.json"
    assets.write_text(json.dumps({user: payload}), encoding="utf-8")
    cache.write_text("{}", encoding="utf-8")
    return RuntimeContext(
        config=AppConfig(
            user=user,
            assets_file=assets,
            cache_file=cache,
            isin_file=tmp / "isin.json",
            plotter="web",
            server=ServerConfig(
                port=0, address="localhost", directory=tmp / "viz"
            ),
        )
    )


class TestEmptyPortfolioUpdate(unittest.TestCase):
    def test_empty_portfolio_update_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(Path(tmp), {})
            ctx.plotter_class().data_dir = Path(tmp) / "data"
            # Must not raise: empty portfolios are skipped with a warning.
            run_update(ctx)
            self.assertEqual(list((Path(tmp) / "data").glob("*.raw")), [])

    def test_null_portfolio_update_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(Path(tmp), None)
            ctx.plotter_class().data_dir = Path(tmp) / "data"
            run_update(ctx)

    def test_partial_portfolio_update_uses_missing_buckets_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(
                Path(tmp),
                {"equity_portfolio": []},
            )
            ctx.plotter_class().data_dir = Path(tmp) / "data"
            # Only the equity bucket exists; the other five are missing
            # and must be treated as empty, not as a KeyError.
            run_update(ctx)


if __name__ == "__main__":
    unittest.main()
