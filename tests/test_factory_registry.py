# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-pass issuer dispatch in ``position.factory``.

Pass 1 consults the ISIN registry and constructs a DB hit with no URL
probes. Pass 2 (unknown issuer, or a build that yielded no usable data)
runs the vendor probes once via the helper and registers the outcome.
Anything unresolved falls back to the generic constructors.
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

from cli.context import AppConfig, RuntimeContext  # noqa: E402
from position.blackrock_position import BlackRockPosition  # noqa: E402
from position.factory import factory  # noqa: E402
from position.justetf_position import JustETFPosition  # noqa: E402
from position.xtrackers_position import XtrackersPosition  # noqa: E402
from position.yfinance_position import YFinancePosition  # noqa: E402


def _ctx() -> RuntimeContext:
    holder = tempfile.TemporaryDirectory()
    tmp = Path(holder.name)
    ctx = RuntimeContext(
        config=AppConfig(
            fetch_geosplit=True,
            fetch_prices=False,
            cache_file=tmp / "cache.json",
            assets_file=tmp / "assets.json",
            isin_file=tmp / "isin.json",
        )
    )
    ctx.cache = {}
    ctx.cache_loaded = True
    ctx._holder = holder  # type: ignore[attr-defined]
    return ctx


def _no_fetch(cls=JustETFPosition):
    return patch.object(
        cls, "_fetch_countries_with_retries", return_value=[]
    )


class TestRegistryDbHit(unittest.TestCase):
    def test_db_hit_constructs_without_probes(self) -> None:
        ctx = _ctx()
        ctx.isin_registry.register_isin(
            "IE00BTJRMP35", issuer="dws", bucket="equity_portfolio"
        )
        with (
            patch("position.factory.dws_product_url_exists") as dws,
            patch("position.factory.ishares_product_url_exists") as ishares,
            patch("position.factory.amundi_product_url_exists") as amundi,
            patch("position.factory.ssga_product_url_exists") as ssga,
            patch("position.factory.ubs_product_url_exists") as ubs,
            patch("position.factory.invesco_product_url_exists") as invesco,
            patch("position.factory.landg_product_url_exists") as landg,
            _no_fetch(),
        ):
            pos = factory(
                isin="IE00BTJRMP35",
                name="Xtrackers",
                shares=1,
                price=10.0,
                ctx=ctx,
            )
        self.assertIsInstance(pos, XtrackersPosition)
        for mock in (dws, ishares, amundi, ssga, ubs, invesco, landg):
            mock.assert_not_called()

    def test_falsy_isin_skips_registry(self) -> None:
        ctx = _ctx()
        before = ctx.isin_registry.snapshot()
        with _no_fetch():
            factory(isin=None, name="Tagesgeld", value=5.0, ctx=ctx)
        self.assertEqual(ctx.isin_registry.snapshot(), before)

    def test_justetf_fallback_records_justetf(self) -> None:
        ctx = _ctx()
        with patch("position.factory._probe_issuer_for_isin", return_value=None):
            with _no_fetch():
                pos = factory(isin="XX000NEW02", name="New", shares=1, price=10.0, ctx=ctx)
        self.assertIsInstance(pos, JustETFPosition)
        self.assertEqual(ctx.isin_registry.get_issuer_for_isin("XX000NEW02"), "justetf")
        row = ctx.isin_registry.get("XX000NEW02")
        assert row is not None
        self.assertEqual(row.bucket, "equity_portfolio")

    def test_yfinance_fallback_records_yfinance(self) -> None:
        ctx = _ctx()
        ctx.config.position_source = "yfinance"
        with patch("position.factory._probe_issuer_for_isin", return_value=None):
            with patch.object(YFinancePosition, "_fast_info_price", return_value=12.0):
                pos = factory(isin="XX000NEW03", name="New", shares=1, ctx=ctx)
        self.assertIsInstance(pos, YFinancePosition)
        self.assertEqual(ctx.isin_registry.get_issuer_for_isin("XX000NEW03"), "yfinance")


class TestProbePhase(unittest.TestCase):
    def test_probe_hit_registers_issuer_and_constructs(self) -> None:
        ctx = _ctx()
        with (
            patch("position.factory.dws_product_url_exists", return_value=False),
            patch("position.factory.amundi_product_url_exists", return_value=False),
            patch("position.factory.ubs_product_url_exists", return_value=False),
            patch("position.factory.invesco_product_url_exists", return_value=False),
            patch("position.factory.landg_product_url_exists", return_value=False),
            patch("position.factory.ishares_product_url_exists", return_value=True),
            _no_fetch(),
        ):
            pos = factory(isin="XX000PROBE1", name="iShares Whatever", shares=1, price=10.0, ctx=ctx)
        self.assertIsInstance(pos, BlackRockPosition)
        self.assertEqual(ctx.isin_registry.get_issuer_for_isin("XX000PROBE1"), "ishares")

    def test_empty_build_falls_back_without_db_downgrade(self) -> None:
        # A DB-hit build that yields no data (both flags on) falls back
        # to generic, but the registry row keeps its slug: transient
        # vendor failures must not downgrade decided rows.
        ctx = _ctx()
        ctx.config.fetch_sectorsplit = True
        ctx.isin_registry.register_isin(
            "IE00BKM4GZ66", issuer="ishares", bucket="equity_portfolio"
        )
        with (
            patch("position.factory._probe_issuer_for_isin", return_value=None),
            patch.object(JustETFPosition, "_fetch_countries_with_retries", return_value=[]),
            patch.object(JustETFPosition, "_fetch_sectors_with_retries", return_value=[]),
        ):
            pos = factory(isin="IE00BKM4GZ66", name="iShares", shares=1, price=10.0, ctx=ctx)
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, BlackRockPosition)
        self.assertEqual(ctx.isin_registry.get_issuer_for_isin("IE00BKM4GZ66"), "ishares")

    def test_exception_falls_back_without_db_downgrade(self) -> None:
        # A ctor hard failure (not swallowed by the accessors) falls back
        # to generic while the decided row keeps its slug.
        import position.factory as factory_mod

        class Boom(JustETFPosition):
            def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("boom")

        ctx = _ctx()
        ctx.isin_registry.register_isin(
            "IE00BKM4GZ66", issuer="ishares", bucket="equity_portfolio"
        )
        with (
            patch("position.factory._probe_issuer_for_isin", return_value=None),
            patch.dict(factory_mod._ISSUER_POSITION, {"ishares": Boom}),
            patch.object(JustETFPosition, "_fetch_countries_with_retries", return_value=[]),
        ):
            pos = factory(isin="IE00BKM4GZ66", name="iShares", shares=1, price=10.0, ctx=ctx)
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, Boom)
        self.assertEqual(ctx.isin_registry.get_issuer_for_isin("IE00BKM4GZ66"), "ishares")


class TestRegistryFlush(unittest.TestCase):
    def _probe_hit_ctx(self, isin: str) -> RuntimeContext:
        ctx = _ctx()
        with (
            patch("position.factory.dws_product_url_exists", return_value=False),
            patch("position.factory.amundi_product_url_exists", return_value=False),
            patch("position.factory.ubs_product_url_exists", return_value=False),
            patch("position.factory.invesco_product_url_exists", return_value=False),
            patch("position.factory.landg_product_url_exists", return_value=False),
            patch("position.factory.ishares_product_url_exists", return_value=True),
            _no_fetch(),
        ):
            factory(isin=isin, name="iShares Whatever", shares=1, price=10.0, ctx=ctx)
        return ctx

    def test_probe_hit_survives_flush_to_disk(self) -> None:
        ctx = self._probe_hit_ctx("XX000PROBE9")
        ctx.flush_isin_registry()
        on_disk = json.loads(Path(ctx.config.isin_file).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["XX000PROBE9"]["issuer"], "ishares")

    def test_reloaded_registry_takes_db_path_without_probes(self) -> None:
        ctx = self._probe_hit_ctx("XX000PROBE9")
        ctx.flush_isin_registry()
        reloaded = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=True,
                fetch_prices=False,
                cache_file=ctx.config.cache_file,
                assets_file=ctx.config.assets_file,
                isin_file=ctx.config.isin_file,
            )
        )
        reloaded.cache = {}
        reloaded.cache_loaded = True
        with (
            patch(
                "position.factory._probe_issuer_for_isin",
                side_effect=AssertionError("probe must not run on a DB hit"),
            ),
            _no_fetch(),
        ):
            pos = factory(
                isin="XX000PROBE9",
                name="iShares Whatever",
                shares=1,
                price=10.0,
                ctx=reloaded,
            )
        self.assertIsInstance(pos, BlackRockPosition)

    def test_flush_without_registry_touch_creates_no_file(self) -> None:
        ctx = _ctx()
        ctx.flush_isin_registry()
        self.assertFalse(Path(ctx.config.isin_file).exists())


if __name__ == "__main__":
    unittest.main()
